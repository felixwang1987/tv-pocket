"""Extract independent JSON VOD sites, sample actual video, publish one collection."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode

try:
    from .playback import probe_stream, timestamp, sample_items, FRESH_SECONDS
except ImportError:
    from playback import probe_stream, timestamp, sample_items, FRESH_SECONDS

ADULT = re.compile(r'成人|色情|伦理|福利|无码|有码|里番|萝莉|丝袜|性感|情色|淫|女优|av资源|18禁|18\+', re.I)
TRANSIENT = {'ac', 'pg', 'page', 'wd', 'ids', 'h', 't', 'limit'}
SECRET = {'token', 'access_token', 'password', 'passwd', 'cookie', 'auth', 'key', 'apikey', 'api_key'}


def api_request_url(api, **params):
    p = urlsplit(api)
    query = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True) if k not in params]
    query.extend((k,str(v)) for k,v in params.items())
    return urlunsplit((p.scheme,p.netloc,p.path,urlencode(query),''))


def canonical_api(value, base, normalize):
    if not isinstance(value,str) or not value.strip():
        return None
    clean = normalize(urljoin(base,value.strip()))
    if not clean:
        return None
    p = urlsplit(clean)
    query = parse_qsl(p.query,keep_blank_values=True)
    if any(k.lower() in SECRET for k,v in query):
        return None
    query = sorted((k,v) for k,v in query if k.lower() not in TRANSIENT)
    return urlunsplit((p.scheme,p.netloc,p.path.rstrip('/') or '/',urlencode(query),''))


def discover_routes(documents, net, parse, normalize, settings):
    queue = deque((url,text,base,0) for url,(text,base) in documents.items())
    seen = set(documents)
    routes, issues = {}, []
    checked_configs = unsupported = fetched = 0
    deadline = time.monotonic() + settings.get('discovery_seconds',90)
    offset = int(time.time() // (6*3600))
    # Cached documents cost no extra network calls. Additional nested documents
    # have their own depth, count and time bounds, including cyclic stores.
    while queue:
        url,text,base,depth = queue.popleft()
        if text is None:
            if fetched >= settings.get('max_documents',60) or time.monotonic() >= deadline:
                continue
            fetched += 1
            try:
                body,base = net.fetch_bytes(url,timeout=6)
                text = body.decode('utf-8-sig',errors='replace')
            except Exception:
                issues.append({'url':url,'reason':'下级配置读取失败'})
                continue
        try:
            obj = parse(text)
        except (ValueError,RecursionError):
            continue
        if not isinstance(obj,dict):
            continue
        sites = obj.get('sites',[])
        if isinstance(sites,list) and sites:
            checked_configs += 1
            for site in sites:
                if not isinstance(site,dict):
                    continue
                # Only self-contained standard JSON APIs can be faithfully
                # moved to a new config without their original plugin context.
                if site.get('type') != 1 or any(site.get(k) for k in ('playUrl','playurl','jar','ext','header','headers')):
                    unsupported += 1
                    continue
                name = str(site.get('name') or site.get('key') or '点播线路')[:100]
                if ADULT.search(name):
                    unsupported += 1
                    continue
                api = canonical_api(site.get('api'),base,normalize)
                if not api:
                    unsupported += 1
                    continue
                if api not in routes:
                    routes[api] = {'id':hashlib.sha256(api.encode()).hexdigest()[:16], 'name':name,
                                   'api':api, 'sources':[]}
                if url not in routes[api]['sources']:
                    routes[api]['sources'].append(url)
        if depth >= settings.get('max_depth',3):
            continue
        children=[]
        for field,key in (('storeHouse','sourceUrl'),('urls','url')):
            values=obj.get(field,[])
            if not isinstance(values,list):
                continue
            for child in values:
                if isinstance(child,dict) and isinstance(child.get(key),str):
                    target=normalize(urljoin(base,child[key]))
                    if target and target not in seen:
                        children.append(target)
        for child in sample_items(children,8,offset):
            if child not in seen:
                seen.add(child)
                queue.append((child,None,child,depth+1))
    return list(routes.values()), {'configs':checked_configs, 'nested_documents':fetched,
                                  'unsupported_sites':unsupported, 'issues':issues[:30]}


def program_rows(payload):
    if not isinstance(payload,dict) or not isinstance(payload.get('list'),list):
        return []
    return [r for r in payload['list'] if isinstance(r,dict)
            and isinstance(r.get('vod_id'),(str,int)) and str(r['vod_id'])
            and isinstance(r.get('vod_name'),str) and r['vod_name'].strip()
            and not ADULT.search(str(r.get('type_name',''))+' '+r['vod_name'])]


def direct_media_urls(program):
    value=program.get('vod_play_url','')
    if not isinstance(value,str):
        return []
    out=[]
    for group in value.split('$$$'):
        for episode in group.split('#'):
            if '$' not in episode:
                continue
            target=episode.split('$',1)[1].strip()
            p=urlsplit(target)
            if p.scheme in ('http','https') and p.hostname and not p.username and not p.password and re.search(r'\.(m3u8|mp4)(?:$)',p.path,re.I):
                if target not in out:
                    out.append(target)
    return out


def check_route(row, net, probe=probe_stream, deadline=None):
    output={**row,'checked_at':timestamp(),'status':'unverified','sampled':0,'passed':0,'samples':[], 'searchable':0}

    def read(**params):
        if deadline is not None and time.monotonic() >= deadline:
            raise ValueError('本轮点播抽检时限已到')
        body,_=net.fetch_bytes(api_request_url(row['api'],**params),timeout=6,limit=2_000_000)
        return json.loads(body.decode('utf-8-sig',errors='strict'))

    try:
        listing=program_rows(read(ac='videolist',pg=1))
        if not listing:
            raise ValueError('未返回可识别的普通节目列表')
        # Two different titles avoid treating one playable trailer as proof
        # for every title. The published label always says this is a sample.
        unique=list({str(p['vod_id']):p for p in listing}.values())
        for program in sample_items(unique,2,int(time.time()//(6*3600))):
            if deadline is not None and time.monotonic() >= deadline:
                break
            title=program['vod_name'][:100]
            result={'status':'unverified','reason':'节目详情未提供直接视频地址，可能需要解析插件'}
            try:
                details=program_rows(read(ac='videolist',ids=program['vod_id']))
                matching=[p for p in details if str(p['vod_id'])==str(program['vod_id'])]
                urls=direct_media_urls(matching[0]) if matching else []
                for url in urls[:2]:
                    if deadline is not None and time.monotonic() >= deadline:
                        break
                    result=probe(net,url)
                    if result['status']=='passed':
                        break
            except Exception as error:
                result={'status':'unverified' if isinstance(error,ValueError) else 'failed','reason':str(error)[:140]}
            output['samples'].append({'name':title,**result})
        output['sampled']=len(output['samples'])
        output['passed']=sum(s['status']=='passed' for s in output['samples'])
        failed=sum(s['status']=='failed' for s in output['samples'])
        output['status']=('passed' if output['sampled'] and output['passed']==output['sampled'] else
                          'partial' if output['passed'] else 'failed' if failed else 'unverified')
        output['reason']='抽检 '+str(output['sampled'])+' 部节目，'+str(output['passed'])+' 部出画面'
        if output['passed']:
            try:
                first=unique[0]
                search=program_rows(read(ac='videolist',wd=first['vod_name']))
                # A negative query catches endpoints that silently ignore wd.
                negative=program_rows(read(ac='videolist',wd='pocket_no_match_'+os.urandom(12).hex()))
                output['searchable']=int(not negative and any(str(r['vod_id'])==str(first['vod_id']) for r in search))
            except Exception:
                pass
    except Exception as error:
        output.update(status='unverified' if isinstance(error,ValueError) else 'failed',reason=str(error)[:140])
    output['checked_at']=timestamp()
    return output


def fresh_routes(document):
    result=[]
    current=datetime.now(timezone.utc)
    for row in document.get('routes',[]):
        try:
            age=(current-datetime.fromisoformat(row['checked_at'])).total_seconds()
            if row['status'] not in ('passed','partial') or row.get('passed',0)<1 or not 0<=age<=FRESH_SECONDS:
                continue
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',row['id']):
                continue
            p=urlsplit(row['api'])
            if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
                continue
            result.append(row)
        except (KeyError,TypeError,ValueError):
            continue
    return sorted(result,key=lambda r:(-r.get('passed',0),-r.get('searchable',0),r['name']))


def publish_routes(root, document, base_url):
    rows=fresh_routes(document)
    checked=Path(root)/'checked'
    folder=checked/'vod'
    folder.mkdir(parents=True,exist_ok=True)
    ids={r['id'] for r in rows}
    for old in folder.glob('*.json'):
        if old.stem not in ids:
            old.unlink()
    urls=[]
    sites=[]
    for row in rows:
        site={'key':'pocket_'+row['id'],'name':row['name'],'type':1,'api':row['api'],
              'searchable':row.get('searchable',0),'quickSearch':row.get('searchable',0),'filterable':0}
        sites.append(site)
        (folder/(row['id']+'.json')).write_text(json.dumps({'sites':[site]},ensure_ascii=False,indent=2)+'\n')
        urls.append({'name':row['name'],'url':urljoin(base_url,'checked/vod/'+row['id']+'.json')})
    (checked/'vod-all.json').write_text(json.dumps({'sites':sites},ensure_ascii=False,indent=2)+'\n')
    if rows:
        urls.insert(0,{'name':'影视口袋 · 全部实测线路','url':urljoin(base_url,'checked/vod-all.json')})
    (checked/'routes.json').write_text(json.dumps({'urls':urls},ensure_ascii=False,indent=2)+'\n')
    return len(rows)


def collect_routes(root, documents, net, parse, normalize, settings):
    started=time.monotonic()
    candidates,discovery=discover_routes(documents,net,parse,normalize,settings)
    selected=sample_items(candidates,settings.get('max_apis',48),int(time.time()//(6*3600)))
    deadline=started+settings.get('max_seconds',360)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda r:check_route(r,net,deadline=deadline),selected))
    document={'schema_version':1,'generated_at':timestamp(),
              'environment':'GitHub Actions 公网' if os.environ.get('GITHUB_ACTIONS') else '本机网络',
              'discovery':discovery,'routes':results,
              'summary':{'discovered_apis':len(candidates),'checked_apis':len(results),
                         'verified_routes':len(fresh_routes({'routes':results})),
                         'sampled_programs':sum(r['sampled'] for r in results),
                         'passed_programs':sum(r['passed'] for r in results),
                         'elapsed_seconds':round(time.monotonic()-started)}}
    target=Path(root)/'data/routes.json'
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(document,ensure_ascii=False,indent=2)+'\n')
    publish_routes(root,document,settings['base_url'])
    return document
