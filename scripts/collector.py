"""Discover and check public playlist/configuration documents. Never execute them."""
import argparse
import concurrent.futures
import hashlib
from html.parser import HTMLParser
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, urljoin, quote, urlencode, parse_qsl, unquote
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPHandler, HTTPSHandler, ProxyHandler

try:
    from .playback import verify_catalog, verified_playlist
    from .routes import collect_routes, fresh_routes
except ImportError:
    from playback import verify_catalog, verified_playlist
    from routes import collect_routes, fresh_routes

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 3_000_000
GITHUB_HOSTS = {'api.github.com', 'raw.githubusercontent.com', 'iptv-org.github.io', 'mcp2016.github.io'}
VOD_MEDIA_PORTS = {
    'p.hhwenjian.com': {65},
    'hnts.ymuuy.com': {65},
    'gs.gszyi.com': {999},
    'c.baisiweiting.com': {18443},
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def normalize_url(value, extra_ports=None):
    if not isinstance(value, str):
        return None
    try:
        p = urlsplit(value.strip())
        host = (p.hostname or '').lower()
        if p.scheme not in ('http', 'https') or not host or p.username or p.password:
            return None
        if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
            return None
        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_global or ip.is_multicast:
                return None
        except ValueError:
            pass
        if p.port not in (None, 80, 443) and p.port not in (extra_ports or {}).get(host, ()):
            return None
        path = p.path or '/'
        if host == 'github.com':
            bits = path.split('/')
            if len(bits) >= 6 and bits[3] in ('blob', 'raw'):
                host = 'raw.githubusercontent.com'
                path = '/' + '/'.join(bits[1:3] + bits[4:])
        netloc = '[' + host + ']' if ':' in host else host.encode('idna').decode()
        if p.port:
            netloc += ':' + str(p.port)
        return urlunsplit((p.scheme, netloc, quote(path, safe='/%:@!$&\'()*+,;=-._~'),
                           quote(p.query, safe='=&%+/:@,;?'), ''))
    except (ValueError, UnicodeError):
        return None


def has_secret_query(url):
    """Reject source URLs with credential-like query field names."""
    exact = {'key', 'pass', 'pwd', 'auth', 'authorization', 'sign', 'session', 'cookie'}
    endings = ('token', 'secret', 'password', 'sessionid', 'sessionkey',
               'apikey', 'authkey', 'signature')
    for key, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        plain = re.sub(r'[^a-z0-9]', '', unquote(key).lower())
        if plain in exact or plain.endswith(endings):
            return True
    return False


def check_public_url(url, resolver=None, extra_ports=None):
    clean = normalize_url(url, extra_ports)
    if not clean:
        raise ValueError('只允许公共 HTTP(S) 地址')
    p = urlsplit(clean)
    resolver = resolver or socket.getaddrinfo
    addresses = resolver(p.hostname, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError('地址无法解析')
    for result in addresses:
        ip = ipaddress.ip_address(result[4][0])
        if not ip.is_global or ip.is_multicast:
            raise ValueError('拒绝内网或特殊网络地址')
    return clean


def open_public_socket(host, port, timeout):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError('地址无法解析')
    for family, kind, protocol, _, address in addresses:
        ip = ipaddress.ip_address(address[0])
        if not ip.is_global or ip.is_multicast:
            raise ValueError('拒绝内网或特殊网络地址')
    error = None
    for family, kind, protocol, _, address in addresses:
        sock = socket.socket(family, kind, protocol)
        try:
            sock.settimeout(timeout)
            sock.connect(address)  # Numeric, already checked; never resolve twice.
            return sock
        except OSError as caught:
            error = caught
            sock.close()
    raise error or OSError('无法连接')


class PublicHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = open_public_socket(self.host, self.port, self.timeout)


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        sock = open_public_socket(self.host, self.port, self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


class PublicHTTPHandler(HTTPHandler):
    def http_open(self, req):
        return self.do_open(PublicHTTPConnection, req)


class PublicHTTPSHandler(HTTPSHandler):
    def https_open(self, req):
        return self.do_open(PublicHTTPSConnection, req, context=self._context)


class SafeRedirect(HTTPRedirectHandler):
    def __init__(self, validator=check_public_url, on_redirect=None):
        super().__init__()
        self.validator = validator
        self.on_redirect = on_redirect

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = self.validator(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, target)
        if redirected:
            redirected.remove_header('Authorization')
            if self.on_redirect:
                self.on_redirect()
        return redirected


class Network:
    def __init__(self, budget=260, github_only=False, extra_ports=None):
        self.remaining = budget
        self.lock = threading.Lock()
        self.github_only = github_only
        self.extra_ports = extra_ports or {}

    def validate(self, url):
        clean = normalize_url(url, self.extra_ports)
        if self.github_only:
            # Local proxy DNS may return RFC 2544 benchmark addresses. In this
            # explicit mode, requests are restricted to four fixed public hosts.
            if not clean or urlsplit(clean).hostname not in GITHUB_HOSTS or not clean.startswith('https://'):
                raise ValueError('本地检查仅限 GitHub 官方文件域名')
            return clean
        return check_public_url(url, extra_ports=self.extra_ports)

    def fetch(self, url, api=False):
        body, _ = self.fetch_bytes(url, api=api)
        return body.decode('utf-8-sig', errors='replace')

    def fetch_bytes(self, url, api=False, limit=MAX_BYTES, partial=False, timeout=12):
        self.take_request()
        return self._fetch_bytes(url, api, limit, partial, timeout)

    def take_request(self):
        with self.lock:
            if self.remaining <= 0:
                raise ValueError('本轮请求数量已达上限')
            self.remaining -= 1

    def _fetch_bytes(self, url, api, limit, partial, timeout):
        clean = self.validate(url)
        headers = {'User-Agent': 'TV-Pocket/1.0', 'Accept': 'application/json,text/plain,*/*'}
        if api and urlsplit(clean).netloc == 'api.github.com' and clean.startswith('https://'):
            token = os.environ.get('GITHUB_TOKEN')
            if token:
                headers['Authorization'] = 'Bearer ' + token
        handlers = [SafeRedirect(self.validate, on_redirect=self.take_request), ProxyHandler({})]
        if not self.github_only:
            handlers += [PublicHTTPHandler(), PublicHTTPSHandler()]
        end = time.monotonic() + timeout
        with build_opener(*handlers).open(Request(clean, headers=headers), timeout=timeout) as response:
            body = bytearray()
            while len(body) < limit + (not partial):
                if time.monotonic() >= end:
                    if partial and body:
                        break
                    raise TimeoutError('读取超过本次检查时限')
                try:
                    chunk = response.read1(min(65536, limit + (not partial) - len(body)))
                except TimeoutError:
                    if partial and body:
                        break
                    raise
                if not chunk:
                    break
                body.extend(chunk)
            if not partial and len(body) > limit:
                raise ValueError('文件超过检查大小上限')
            return bytes(body), response.geturl()

    def api(self, path):
        return json.loads(self.fetch('https://api.github.com' + path, api=True))


def parse_jsonc(text):
    # Lex the comments/trailing commas, preserving every character inside strings.
    out, i, quoted = [], 0, False
    while i < len(text):
        c = text[i]
        if quoted:
            out.append(c)
            if c == '\\' and i + 1 < len(text):
                i += 1
                out.append(text[i])
            elif c == '"':
                quoted = False
        elif c == '"':
            quoted = True
            out.append(c)
        elif text[i:i+2] == '//':
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
            out.append('\n')
            continue
        elif text[i:i+2] == '/*':
            end = text.find('*/', i + 2)
            if end < 0:
                raise ValueError('未闭合注释')
            i = end + 2
            out.append(' ')
            continue
        else:
            out.append(c)
        i += 1
    text = ''.join(out)
    out, quoted, i = [], False, 0
    while i < len(text):
        c = text[i]
        if quoted:
            out.append(c)
            if c == '\\' and i + 1 < len(text):
                i += 1
                out.append(text[i])
            elif c == '"':
                quoted = False
        elif c == '"':
            quoted = True
            out.append(c)
        elif c == ',' and text[i+1:].lstrip().startswith(('}', ']')):
            pass
        else:
            out.append(c)
        i += 1
    cleaned = ''.join(out).lstrip()
    obj, end = json.JSONDecoder().raw_decode(cleaned)
    footer = cleaned[end:]
    # Some public hosts append an empty div badge after an otherwise valid
    # config. Accept only that markup, never a second document or page text.
    if footer.strip() and (len(footer) > 4096 or not re.fullmatch(r'(?:\s|</?div\b[^<>]*>)*', footer, re.I)):
        raise ValueError('配置 JSON 后包含额外内容')
    return obj


def classify(text, url):
    text = text.lstrip('\ufeff \r\n\t')
    if text.startswith('#EXTM3U'):
        if '#EXT-X-TARGETDURATION:' in text or '#EXT-X-STREAM-INF:' in text:
            return {'kind': 'stream', 'format': 'HLS', 'count': 1}
        count = text.count('#EXTINF:')
        if count and re.search(r'^https?://', text, re.M):
            return {'kind': 'live', 'format': 'M3U', 'count': count}
    if text.startswith(('{', '//', '/*')):
        try:
            obj = parse_jsonc(text)
            for field, kind in [('storeHouse', 'multi'), ('urls', 'collection'), ('sites', 'config')]:
                rows = obj.get(field)
                required = {'storeHouse':'sourceUrl', 'urls':'url', 'sites':'api'}[field]
                if isinstance(rows, list):
                    count = sum(isinstance(x, dict) and bool(x.get(required)) for x in rows)
                    if count:
                        return {'kind': kind, 'format': 'JSON', 'count': count}
        except (ValueError, AttributeError, RecursionError):
            pass
    count = len(re.findall(r'^[^\n,<>{}]+,https?://\S+', text, re.M))
    if count:
        return {'kind': 'live', 'format': 'TXT', 'count': count}
    return None


def extract_links(text, base):
    found = []
    def add(name, value):
        if not isinstance(value, str):
            return
        url = normalize_url(urljoin(base, value))
        if url and not has_secret_query(url):
            found.append({'name': str(name or urlsplit(url).path.rsplit('/',1)[-1])[:160], 'url': url})
    try:
        obj = parse_jsonc(text)
    except (ValueError, RecursionError):
        obj = None
    if isinstance(obj, dict):
        for key in ('storeHouse', 'urls', 'lives'):
            rows = obj.get(key, [])
            if not isinstance(rows, list):
                continue
            for item in rows:
                if isinstance(item, dict):
                    add(item.get('sourceName') or item.get('name'), item.get('sourceUrl') or item.get('url'))
        return found
    # Only README-style candidate URLs, never video segments or plug-in binaries.
    if text.lstrip().startswith(('#EXTM3U', '{')) or isinstance(obj, list):
        return []
    for line in text.splitlines():
        for match in re.finditer(r'https?://[^\s<>"`\]\)]+', line):
            value = match.group().rstrip('。,;|')
            p = urlsplit(value)
            if re.search(r'\.(?:png|jpg|svg|gif|jar|js|apk|zip|exe)(?:$|\?)', value, re.I):
                continue
            relevant = re.search(r'\.(?:json|m3u8?|txt)(?:$|\?)', value, re.I)
            contextual = re.search(r'多仓|单仓|接口|直播|配置', line)
            if relevant or (contextual and p.hostname not in ('github.com','www.github.com')):
                label = re.sub(r'https?://\S+', '', line).strip(' >*|-[]():：')[:60]
                add(label or p.path.rsplit('/',1)[-1], value)
    return found


class ForumPosts(HTMLParser):
    """Read visible Discuz post text, excluding navigation and page scripts."""
    def __init__(self, post_id_prefix):
        super().__init__(convert_charrefs=True)
        self.post_id_prefix = post_id_prefix
        self.depth = 0
        self.ignored = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'td' and str(attributes.get('id') or '').startswith(self.post_id_prefix):
            self.depth = 1
        elif self.depth and tag == 'td':
            self.depth += 1
        elif self.depth and tag in ('script', 'style'):
            self.ignored += 1
        elif self.depth and tag in ('br', 'p', 'div', 'li'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.depth and tag == 'td':
            self.depth -= 1
            if not self.depth:
                self.parts.append('\n')
        elif self.ignored and tag in ('script', 'style'):
            self.ignored -= 1
        elif self.depth and tag in ('p', 'div', 'li'):
            self.parts.append('\n')

    def handle_data(self, data):
        if self.depth and not self.ignored:
            self.parts.append(data)


def decode_source_page(body):
    header = body[:4096].decode('ascii', errors='ignore')
    match = re.search(r'charset\s*=\s*["\']?([a-zA-Z0-9_-]+)', header, re.I)
    declared = match[1].lower() if match else ''
    encoding = 'gb18030' if declared in ('gbk', 'gb2312', 'gb18030') else 'utf-8-sig'
    return body.decode(encoding, errors='replace')


def extract_source_page_links(html, base, post_id_prefix='postmessage_'):
    parser = ForumPosts(post_id_prefix)
    parser.feed(html)
    rows, seen, label = [], set(), ''
    for line in ''.join(parser.parts).splitlines():
        line = line.strip()
        if line.startswith('★'):
            label = line.lstrip('★').strip(' ：:')[:100]
        if not label:
            continue
        for match in re.finditer(r'https?://[^\s<>"`]+', line, re.I):
            url = normalize_url(match.group().rstrip('。,;:，)]}'))
            if url and url not in seen and not has_secret_query(url):
                seen.add(url)
                rows.append({'name':label, 'url':url, 'sources':[base]})
    return rows


SOURCE_FILE = re.compile(r'\.(?:json|m3u8?|txt)$', re.I)
NON_SOURCE_FILE = re.compile(r'\.(?:html?|php|png|jpe?g|gif|svg|webp|css|js|jar|apk|zip|exe|pdf|mp4|mkv|mov|avi|ts|flv|webm|mp3|aac|wav)$', re.I)
SOURCE_CONTEXT = re.compile(r'多仓|单仓|接口|直播|线路|配置|影视仓|TVBox', re.I)


def extract_generic_page_links(html: str, base: str) -> list:
    """Extract likely public source links from static visible page content."""
    class VisibleLinks(HTMLParser):
        SKIP = {'script', 'style', 'nav', 'header', 'footer', 'aside', 'template'}
        BLOCK = {'p', 'div', 'li', 'br', 'article', 'section', 'main', 'h1', 'h2', 'h3'}
        VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.ignored = []
            self.anchor = None
            self.parts = []
            self.links = []

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if self.ignored:
                if tag not in self.VOID:
                    self.ignored.append(tag)
                return
            style = attributes.get('style') or ''
            hidden = (tag in self.SKIP or 'hidden' in attributes or 'inert' in attributes or
                      (attributes.get('aria-hidden') or '').lower() == 'true' or
                      (attributes.get('role') or '').lower() == 'navigation' or
                      bool(re.search(r'(?:display\s*:\s*none|visibility\s*:\s*hidden)', style, re.I)))
            if hidden:
                if tag not in self.VOID:
                    self.ignored.append(tag)
                return
            if tag in self.BLOCK:
                self.parts.append('\n')
            if tag == 'a':
                self.anchor = {'url': dict(attrs).get('href'), 'name': ''}

        def handle_endtag(self, tag):
            if self.ignored:
                if tag in self.ignored:
                    while self.ignored and self.ignored.pop() != tag:
                        pass
                return
            if tag == 'a' and self.anchor:
                self.links.append(self.anchor)
                self.anchor = None
            if tag in self.BLOCK:
                self.parts.append('\n')

        def handle_data(self, data):
            if self.ignored:
                return
            self.parts.append(data)
            if self.anchor:
                self.anchor['name'] += data

    parser = VisibleLinks()
    parser.feed(html)
    rows, seen = [], set()

    def add(value, label):
        if not isinstance(value, str):
            return
        url = normalize_url(urljoin(base, value.strip()))
        if not url or url in seen:
            return
        parts = urlsplit(url)
        if has_secret_query(url):
            return
        if NON_SOURCE_FILE.search(parts.path):
            return
        if not SOURCE_FILE.search(parts.path) and not SOURCE_CONTEXT.search(label or ''):
            return
        seen.add(url)
        name = re.sub(r'\s+', ' ', label or '').strip()[:100]
        rows.append({'name': name or unquote(parts.path.rsplit('/', 1)[-1]) or parts.hostname,
                     'url': url, 'sources': [base]})

    for anchor in parser.links:
        add(anchor['url'], anchor['name'])
    for line in ''.join(parser.parts).splitlines():
        for match in re.finditer(r'https?://[^\s<>"`]+', line, re.I):
            value = match.group().rstrip('。,;:，)]}')
            add(value, line.replace(match.group(), ''))
    return rows


def discover_source_pages(net, config):
    found, issues = [], []
    for page in config.get('source_pages', [])[:30]:
        if not isinstance(page, dict):
            continue
        url = normalize_url(page.get('url'))
        if not url:
            continue
        try:
            body, final_url = net.fetch_bytes(url, limit=500_000, timeout=10)
            html = decode_source_page(body)
            if page.get('parser') == 'links':
                rows = extract_generic_page_links(html, final_url)
                rows = [{**row, 'sources':[url]} for row in rows]
                limit = min(30, max(0, int(page.get('max_links', 30))))
            else:
                rows = extract_source_page_links(html, url, page.get('post_id_prefix', 'postmessage_'))
                limit = min(50, max(0, int(page.get('max_links', 50))))
            category = page.get('category')
            found.extend([{**row, **({'category': category} if category in ('ordinary', 'adult') else {})}
                          for row in rows[:limit]])
        except Exception as error:
            issues.append(url + '：' + str(error)[:120])
    return found, issues


def merge_records(old, new):
    result = {}
    for item in old + new:
        url = normalize_url(item.get('url'))
        if not url:
            continue
        previous = result.get(url, {})
        merged = {**previous, **item, 'url': url}
        merged['sources'] = sorted(set(previous.get('sources', []) + item.get('sources', [])))
        merged['id'] = hashlib.sha256(url.encode()).hexdigest()[:16]
        result[url] = merged
    return list(result.values())


def choose_candidates(seeds, old, found, limit):
    metadata = {row['url']: row for row in merge_records(old, found + seeds)}
    selected = merge_records([], seeds)[:limit]
    seen = {r['url'] for r in selected}
    remaining = max(0, limit - len(selected))
    older = [r for r in sorted(old, key=lambda r:r.get('checked_at','')) if r['url'] not in seen]
    old_quota = min(len(older), max(1, int(remaining * .7))) if remaining else 0
    known = {r['url'] for r in old}
    fresh = [r for r in found if r['url'] not in known and r['url'] not in seen]
    for row in older[:old_quota] + fresh + older[old_quota:]:
        url = normalize_url(row.get('url'))
        if url and url not in seen and len(selected) < limit:
            # Candidate metadata must not carry yesterday's status into today.
            selected.append({'name':row['name'], 'url':url, 'sources':row.get('sources',[])})
            seen.add(url)
    return [{**row, 'sources':metadata[row['url']].get('sources', []),
             **({'category':metadata[row['url']]['category']}
                if metadata[row['url']].get('category') in ('ordinary', 'adult') else {})}
            for row in selected]


def discover_candidates(net, config):
    repos = {r: {'full_name':r} for r in config['repositories']}
    errors, candidates = [], []
    search_groups = []
    for query in config['queries']:
        try:
            payload = net.api('/search/repositories?' + urlencode({
                'q':query, 'sort':'updated', 'per_page':config.get('search_results_per_query',12)}))
            search_groups.append([repo for repo in payload.get('items', [])
                                  if not repo.get('private') and not repo.get('archived')])
            if payload.get('incomplete_results'):
                errors.append('GitHub 搜索只返回部分结果')
        except Exception as error:
            errors.append('GitHub 搜索失败：' + str(error)[:120])
    # Rotate through queries so one large result set cannot use every slot.
    for index in range(max((len(group) for group in search_groups), default=0)):
        for group in search_groups:
            if len(repos) >= config['max_repositories']:
                break
            if index < len(group):
                repo = group[index]
                repos.setdefault(repo['full_name'], repo)
    for name, repo in repos.items():
        try:
            if 'default_branch' not in repo:
                repo = net.api('/repos/' + name)
            branch = quote(repo['default_branch'], safe='')
            base = 'https://raw.githubusercontent.com/' + name + '/' + branch + '/'
            provenance = 'https://github.com/' + name
            tree = net.api('/repos/' + name + '/git/trees/' + branch + '?recursive=1')
            paths = [row['path'] for row in tree.get('tree', [])
                     if row.get('type') == 'blob' and row.get('size', 0) <= MAX_BYTES]
            readmes = sorted(p for p in paths if p.lower() == 'readme.md')
            for path in readmes:
                text = net.fetch(base + quote(path, safe='/'))
                for candidate in extract_links(text, base + path)[:30]:
                    candidates.append({**candidate, 'sources':[provenance]})
            paths = [p for p in paths if re.search(r'\.(json|m3u8?|txt)$', p, re.I)
                     and not re.search(r'(^|/)(node_modules|\.git|package|tsconfig|test|vendor|api|epg)', p, re.I)]
            paths.sort(key=lambda p: (not bool(re.search(r'duocang|多仓|urls|store|^tv|index|live|直播|config', p, re.I)), p.count('/'), p))
            for path in paths[:config['files_per_repository']]:
                candidates.append({'name':name.split('/')[0] + ' · ' + path,
                                   'url':base + quote(path, safe='/'), 'sources':[provenance]})
        except Exception as error:
            errors.append(name + '：' + str(error)[:120])
    return candidates, errors, len(repos)


def collect(root=ROOT, discover=True, github_only=False):
    config = json.loads((root / 'sources.config.json').read_text())
    target = root / 'data/sources.json'
    old = json.loads(target.read_text()) if target.exists() else {'entries': []}
    net = Network(config.get('request_budget', 260), github_only=github_only)
    checked_at = now()
    seeds = config['seeds']
    found = []
    issues, repos_count = [], 0
    page_found = []
    if discover:
        page_found, page_issues = discover_source_pages(net, config) if not github_only else ([], [])
        github_found, github_issues, repos_count = discover_candidates(net, config)
        found = page_found + github_found
        issues = page_issues + github_issues
    max_candidates=config['max_candidates']
    child_slots=min(max(0,int(config.get('child_slots',0))),max(0,max_candidates-1))
    candidates = choose_candidates(seeds, old['entries'], found, max_candidates-child_slots)
    previous = {r['url']:r for r in old['entries']}
    results, children, documents = [], [], {}
    def check(item):
        row = {**item, 'checked_at':now()}
        try:
            body, final_url = net.fetch_bytes(item['url'])
            text = body.decode('utf-8-sig', errors='replace')
            info = classify(text, final_url)
            if not info:
                raise ValueError('内容不是可识别的配置或播放列表')
            row.update(info, status='ok', last_ok=row['checked_at'], error='')
            documents[item['url']] = (text, final_url)
            child = []
            if info['kind'] in ('multi', 'collection', 'config'):
                child = [{**x, 'sources':item['sources'],
                          **({'category':'adult'} if item.get('category') == 'adult' else {})}
                         for x in extract_links(text, final_url)[:40]]
            return row, child
        except Exception as error:
            row.setdefault('kind', 'unknown')
            row.update(status='error', error=str(error)[:180])
            return row, []
    def check_batch(batch):
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            for row, child in pool.map(check, batch):
                results.append(row)
                children.extend(child)
    check_batch(candidates)
    checked_urls = {r['url'] for r in results}
    extra = [c for c in merge_records([], children) if c['url'] not in checked_urls]
    extra = extra[:max(0, max_candidates - len(candidates))]
    check_batch(extra)
    # Unused child capacity goes back to ordinary discovery candidates.
    if len(results) < max_candidates:
        checked_urls = {r['url'] for r in results}
        fallback = [r for r in choose_candidates(seeds, old['entries'], found, max_candidates)
                    if r['url'] not in checked_urls]
        check_batch(fallback[:max_candidates-len(results)])
    pinned = {normalize_url(row.get('url')) for row in seeds}
    accepted = [r for r in results if r['status'] == 'ok' or r['url'] in previous or r['url'] in pinned]
    records = merge_records(old['entries'], accepted)
    records.sort(key=lambda r: (r.get('status') != 'ok', r.get('kind',''), r.get('name','')))
    records = records[:500]
    playback_summary = {}
    routes_document = {'routes':[], 'summary':{}}
    if not github_only:
        settings = config.get('playback', {})
        health_net = Network(settings.get('request_budget', 500))
        playback_summary = verify_catalog(records, documents, health_net, classify, extract_links, settings)
        route_settings = config.get('routes', {})
        if route_settings.get('base_url'):
            config_urls = {r['url'] for r in records if r.get('status') == 'ok' and r.get('kind') in ('multi','collection','config')}
            route_documents = {u:d for u,d in documents.items() if u in config_urls}
            routes_document = collect_routes(root, route_documents,
                                             Network(route_settings.get('request_budget',800), extra_ports=VOD_MEDIA_PORTS),
                                             parse_jsonc, normalize_url, route_settings)
    playlist, verified_count = verified_playlist(records)
    playback_summary['verified_streams'] = verified_count
    (root / 'checked').mkdir(exist_ok=True)
    (root / 'checked/live.m3u').write_text(playlist)
    success = sum(r['status'] == 'ok' for r in results)
    document = {
        'schema_version':1, 'generated_at':now(), 'checked_at':checked_at,
        'last_success_at': now() if success else old.get('last_success_at'),
        'discovery': {'enabled':discover, 'repositories':repos_count,
                      'source_pages_checked':min(30,len(config.get('source_pages',[]))) if discover and not github_only else 0,
                      'source_page_candidates':len(page_found),
                      'issues':issues, 'github_only':github_only},
        'summary': {'checked':len(results), 'recognized':success, 'rejected_or_failed':len(results)-success},
        'playback_summary':playback_summary,
        'routes_summary':{**routes_document['summary'], 'checked_at':routes_document.get('generated_at'),
                          'environment':routes_document.get('environment')},
        'verified_routes':[{k:r.get(k) for k in ('id','name','category','checked_at','status','sampled','passed','searchable','sources')}
                           for r in fresh_routes(routes_document)],
        'entries':records,
    }
    target.parent.mkdir(exist_ok=True)
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n')
    temp.replace(target)
    print(json.dumps({'entries':len(records), **document['summary'], 'playback':playback_summary,
                      'routes':document['routes_summary'], 'discovery_issues':len(issues)}, ensure_ascii=False))
    return document


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-discover', action='store_true', help='Only check seeds and previous records')
    parser.add_argument('--github-only', action='store_true', help='For local proxy DNS: check four fixed GitHub hosts only')
    args = parser.parse_args()
    collect(discover=not args.no_discover, github_only=args.github_only)
