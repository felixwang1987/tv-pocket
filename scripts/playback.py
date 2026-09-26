"""Bounded public-stream sampling. FFmpeg receives local bytes, never URLs."""
import concurrent.futures
from datetime import datetime, timezone
import os
import re
import shutil
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit

FRESH_SECONDS = 36 * 3600


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def parse_channels(text, base):
    channels, seen = [], set()
    m3u = text.lstrip('\ufeff\r\n ').startswith('#EXTM3U')
    name, unsupported = '', ''
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith('#EXTINF:'):
            name = line.split(',', 1)[-1].strip()
            unsupported = ''
        elif line.startswith(('#EXTVLCOPT:', '#KODIPROP:', '#EXTHTTP:')):
            unsupported = '需要播放器专用请求头或选项，未自动验证'
        elif not line.startswith('#'):
            if m3u:
                value = line
            elif ',' in line:
                name, value = line.split(',', 1)
                unsupported = ''
            else:
                continue
            if value == '#genre#':
                continue
            if '|' in value or '#' in value or '$' in value:
                unsupported = '地址含播放器专用参数，未自动验证'
            url = urljoin(base, value.strip())
            if url not in seen:
                seen.add(url)
                channels.append({'name':name[:120] or '未命名频道', 'url':url, 'unsupported':unsupported})
            name, unsupported = '', ''
    return channels


def sample_items(items, limit, offset=0):
    if len(items) <= limit:
        return items
    # Spread the sample across the list and rotate it on later scheduled runs.
    return [items[(int(i * len(items) / limit) + offset) % len(items)] for i in range(limit)]


def decode_video(body):
    executable = os.environ.get('FFMPEG') or shutil.which('ffmpeg')
    if not executable:
        raise ValueError('检测机器未安装视频解码器')
    # Explicit demuxers and pipe-only protocols prevent an untrusted payload
    # from causing FFmpeg to read arbitrary files or make network requests.
    if any(body[i:i+1] == b'G' and body[i+188:i+189] == b'G' for i in range(min(188, max(0, len(body)-188)))):
        fmt = 'mpegts'
    elif body[4:8] in (b'ftyp', b'styp', b'moov'):
        fmt = 'mov'
    elif body.startswith(b'FLV'):
        fmt = 'flv'
    else:
        return False
    try:
        result = subprocess.run([
            executable, '-nostdin', '-v', 'error', '-max_alloc', '67108864',
            '-protocol_whitelist', 'pipe', '-format_whitelist', fmt, '-f', fmt,
            '-probesize', '4000000', '-analyzeduration', '3000000', '-threads', '1',
            '-i', 'pipe:0', '-map', '0:v:0', '-frames:v', '1', '-threads', '1',
            '-f', 'framemd5', 'pipe:1'], input=body, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=10, check=False)
        return result.returncode == 0 and bool(re.search(rb'^0,\s*\d.*[0-9a-f]{32}\s*$', result.stdout, re.M))
    except subprocess.TimeoutExpired:
        return False


def probe_stream(net, url, decoder=decode_video):
    visited = set()

    def fetch(target, depth=0):
        if depth > 4 or target in visited:
            raise ValueError('播放列表循环或层级过深，需客户端验证')
        visited.add(target)
        body, final_url = net.fetch_bytes(target, limit=4_000_000, partial=True, timeout=6)
        if body.lstrip(b'\xef\xbb\xbf\r\n ').startswith(b'#EXTM3U'):
            text = body.decode('utf-8-sig', errors='replace')
            if re.search(r'#EXT-X-KEY:(?!METHOD=NONE(?:,|\s|$))', text):
                raise ValueError('加密直播，需支持它的客户端验证')
            if '#EXT-X-BYTERANGE:' in text or re.search(r'#EXT-X-MAP:.*BYTERANGE=', text):
                raise ValueError('分段字节范围直播，需客户端验证')
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            variants = []
            for i, line in enumerate(lines[:-1]):
                if line.startswith('#EXT-X-STREAM-INF:'):
                    bandwidth = re.search(r'(?:[:,])BANDWIDTH=(\d+)', line)
                    codecs = re.search(r'CODECS="([^"]+)"', line)
                    if codecs and all(re.match(r'^(mp4a|ac-3|ec-3|opus|flac)(\.|$)', c.strip(), re.I)
                                      for c in codecs[1].split(',')) and 'RESOLUTION=' not in line:
                        continue
                    if not lines[i+1].startswith('#'):
                        variants.append((int(bandwidth[1]) if bandwidth else 0, urljoin(final_url, lines[i+1])))
            if variants:
                return fetch(min(variants)[1], depth + 1)
            if '#EXT-X-STREAM-INF:' in text:
                raise ValueError('主播放列表未找到视频变体，可能只有音频')
            segments, current_map = [], None
            for line in lines:
                if line.startswith('#EXT-X-MAP:'):
                    match = re.search(r'URI="([^"]+)"', line)
                    current_map = urljoin(final_url, match[1]) if match else None
                elif not line.startswith('#'):
                    segments.append((urljoin(final_url, line), current_map))
            if not segments or '#EXTINF:' not in text:
                raise ValueError('未找到可抽检的视频分段')
            segment_url, map_url = segments[-2] if len(segments) > 1 else segments[0]
            segment = fetch(segment_url, depth + 1)
            if map_url:
                init, _ = net.fetch_bytes(map_url, limit=1_000_000, timeout=6)
                segment = init + segment
            return segment
        if body.lstrip().lower().startswith((b'<!doctype', b'<html', b'{', b'[')):
            raise RuntimeError('返回网页或错误信息，不是视频数据')
        return body

    try:
        body = fetch(url)
        if not decoder(body):
            return {'status':'failed', 'reason':'未能从抽取数据解码视频帧（格式、数据或解码限制）'}
        return {'status':'passed', 'reason':'已取到视频数据并解码出 1 帧画面'}
    except HTTPError as error:
        return {'status':'failed', 'reason':'HTTP ' + str(error.code) + ('，服务器限制访问' if error.code in (401, 403) else '')}
    except (TimeoutError, URLError, OSError):
        return {'status':'failed', 'reason':'检测网络连接失败或超时'}
    except ValueError as error:
        return {'status':'unverified', 'reason':str(error)[:120]}
    except Exception as error:
        return {'status':'failed', 'reason':str(error)[:120] or '无法读取视频'}


def check_live(net, text, url, kind='live', limit=3, offset=0, deadline=None, decoder=decode_video):
    channels = [{'name':'直播流', 'url':url}] if kind == 'stream' else parse_channels(text, url)
    samples = []
    for channel in sample_items(channels, limit, offset):
        if deadline is not None and time.monotonic() >= deadline:
            result = {'status':'unverified', 'reason':'本轮抽检时限已到，等待下次轮检'}
        elif channel.get('unsupported'):
            result = {'status':'unverified', 'reason':channel['unsupported']}
        elif urlsplit(channel['url']).scheme not in ('http', 'https'):
            result = {'status':'unverified', 'reason':'非 HTTP(S) 直播，需客户端或运营商网络验证'}
        else:
            result = probe_stream(net, channel['url'], decoder)
        samples.append({**channel, **result, 'checked_at':timestamp()})
    passed = sum(s['status'] == 'passed' for s in samples)
    failed = sum(s['status'] == 'failed' for s in samples)
    status = 'passed' if samples and passed == len(samples) else 'partial' if passed else 'failed' if failed else 'unverified'
    return {'status':status, 'checked_at':timestamp(), 'passed':passed, 'sampled':len(samples),
            'total':len(channels), 'samples':samples}


def verify_catalog(records, documents, net, classify, extract_links, settings):
    started = time.monotonic()
    deadline = started + settings.get('max_seconds', 360)
    environment = 'GitHub Actions 公网' if os.environ.get('GITHUB_ACTIONS') else '本机网络'
    offset = int(time.time() // (6 * 3600))
    eligible = [r for r in records if r.get('status') == 'ok' and r['url'] in documents]
    live = sorted((r for r in eligible if r.get('kind') in ('live', 'stream')),
                  key=lambda r:r.get('playback', {}).get('checked_at', ''))
    chosen = live[:settings.get('max_lists', 48)]
    configs = [r for r in eligible if r.get('kind') not in ('live', 'stream')]
    # Config checks cannot emulate a TVBox plugin or a signed-in client.
    for row in configs:
        row['playback'] = {'status':'config_only', 'checked_at':timestamp(), 'environment':environment,
                           'reason':'配置可读；点播解析、网盘登录和会员权限需在影视仓实播验证', 'samples':[]}

    def check_config(row):
        text, base = documents[row['url']]
        samples = []
        for child in sample_items(extract_links(text, base), 3, offset):
            if time.monotonic() >= deadline:
                break
            try:
                body, final_url = net.fetch_bytes(child['url'], timeout=6)
                good = bool(classify(body.decode('utf-8-sig', errors='replace'), final_url))
                result = {'status':'readable' if good else 'failed', 'reason':'下级文件可识别，尚未实播' if good else '下级内容不是可识别配置'}
            except Exception as error:
                result = {'status':'failed', 'reason':str(error)[:100]}
            samples.append({**child, **result})
        row['playback'].update(samples=samples, sampled=len(samples), readable=sum(s['status'] == 'readable' for s in samples))

    def check(row):
        text, base = documents[row['url']]
        row['playback'] = {**check_live(net, text, base, row['kind'], settings.get('samples_per_list', 3), offset, deadline),
                           'environment':environment}

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(check, chosen))
        list(pool.map(check_config, [r for r in configs if r['kind'] in ('multi', 'collection')][:24]))
    return {'checked_at':timestamp(), 'environment':environment, 'checked_lists':len(chosen),
            'sampled_channels':sum(r['playback']['sampled'] for r in chosen),
            'passed_channels':sum(r['playback']['passed'] for r in chosen),
            'elapsed_seconds':round(time.monotonic()-started)}


def verified_playlist(records):
    current = datetime.now(timezone.utc)
    output, seen = ['#EXTM3U', f'# 仅含最近 {FRESH_SECONDS // 3600} 小时解码出画面的抽检频道；检测网络不同，播放仍可能受限。'], set()
    for row in records:
        if row.get('status') != 'ok' or row.get('kind') not in ('live', 'stream'):
            continue
        for sample in row.get('playback', {}).get('samples', []):
            try:
                age = (current - datetime.fromisoformat(sample['checked_at'])).total_seconds()
                p = urlsplit(sample['url'])
                if not 0 <= age <= FRESH_SECONDS or sample['status'] != 'passed' or p.scheme not in ('http', 'https') or p.username or p.password:
                    continue
            except (KeyError, ValueError, TypeError):
                continue
            if sample['url'] in seen or any(c in sample['url'] for c in '\r\n'):
                continue
            seen.add(sample['url'])
            name = re.sub(r'[\r\n\x00-\x1f]+', ' ', sample.get('name', '直播')).strip()
            output.extend(['#EXTINF:-1,' + name, sample['url']])
    return '\n'.join(output) + '\n', len(seen)
