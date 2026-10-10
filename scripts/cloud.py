"""Describe public cloud configs without loading their plugins or credentials."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit

try:
    from .playback import FRESH_SECONDS
except ImportError:
    from playback import FRESH_SECONDS

CLOUD_SITE = re.compile(r'网盘|云盘|盘搜|盘搜索|玩偶|panwebshare|pans?(?:search|sou|so|ali|baidu|quark|uc)|yunpan|wogg|wo4k|alist',re.I)
LOGIN_SITE = re.compile(r'配置|登录|授权|token|csp_config|mydriveguard',re.I)
PROVIDERS = [
    ('阿里云盘',re.compile(r'阿里|PanAli|Ali(?:Yun|Drive|Pan)',re.I)),
    ('百度网盘',re.compile(r'百度|PanBaidu|BaiduPan',re.I)),
    ('夸克',re.compile(r'夸克|quark',re.I)),
    ('UC',re.compile(r'UC.{0,4}网盘|PanUC|UCDrive|(?:^|[_ -])UC(?:$|[_ -])',re.I)),
]


def describe_cloud_config(config):
    related, providers, login_names = set(), set(), []
    sites=config.get('sites',[]) if isinstance(config,dict) else []
    for site in sites if isinstance(sites,list) else []:
        if not isinstance(site,dict) or site.get('type')!=3:
            continue
        identity=' '.join(site.get(k,'') for k in ('key','name','api') if isinstance(site.get(k),str))
        if LOGIN_SITE.search(identity):
            name=str(site.get('name') or site.get('key') or '')[:100]
            if name and name not in login_names:
                login_names.append(name)
            continue
        if CLOUD_SITE.search(identity):
            related.add(str(site.get('key') or site.get('api') or identity))
            providers.update(name for name,pattern in PROVIDERS if pattern.search(identity))
    return {'site_count':len(related),'providers':[name for name,_ in PROVIDERS if name in providers],
            'login_names':login_names[:5]}


def fresh_cloud_configs(entries, normalize, secret_check):
    current=datetime.now(timezone.utc)
    result, seen, adult_urls = [], set(), set()
    for row in entries:
        try:
            url=normalize(row['url'])
            if not url or secret_check(url):
                continue
            # A failed or stale duplicate must not erase a known adult category.
            if row.get('role')=='cloud' and row.get('category')=='adult':
                adult_urls.add(url)
            age=(current-datetime.fromisoformat(row['checked_at'])).total_seconds()
            info=row.get('cloud') or {}
            if (row.get('role')!='cloud' or row.get('status')!='ok' or row.get('kind')!='config'
                    or row.get('count',0)<2 or info.get('site_count',0)<2 or not 0<=age<=FRESH_SECONDS):
                continue
            if url in seen or not re.fullmatch(r'[a-f0-9]{16}',row['id']):
                continue
            seen.add(url)
            result.append({'id':row['id'],'name':'网盘 · '+str(row['name'])[:100]+'（需自行登录）',
                           'url':url,'category':'ordinary','checked_at':row['checked_at'],'total_sites':row['count'],
                           'site_count':info['site_count'],'providers':info.get('providers',[]),
                           'login_names':info.get('login_names',[])})
        except (KeyError,TypeError,ValueError):
            continue
    for config in result:
        if config['url'] in adult_urls:
            config['category']='adult'
    return result


def prepare_cloud_snapshot(config, source_url, source_name, checked_at, normalize, secret_check):
    """Keep one plugin context and its cloud sites; never load referenced files."""
    if not isinstance(config,dict):
        raise ValueError('网盘配置不是对象')
    blocked_labels=re.compile(r'成人|情色|色情|18\+|18禁|🔞|未成年|幼女|萝莉|小学生|初中生|高中生',re.I)
    secret_keys={'token','accesstoken','refreshtoken','cookie','authorization','password',
                 'passwd','pass','pwd','auth','apikey','secret','session','sessionid','sessionkey'}

    def portable(value):
        if isinstance(value,dict):
            out={}
            for key,item in value.items():
                plain=re.sub(r'[^a-z0-9]','',key.lower())
                if item and (plain in secret_keys or plain.endswith(('token','cookie','secret','password','sessionid','sessionkey'))):
                    raise ValueError('网盘配置包含登录凭据，未复制')
                out[key]=portable(item)
            return out
        if isinstance(value,list):
            return [portable(item) for item in value]
        if isinstance(value,str):
            if value.lstrip().startswith(('{','[')):
                try:
                    decoded=json.loads(value)
                except ValueError:
                    pass
                else:
                    portable(decoded)
            if value.startswith(('file:','content:','clan:','~/')):
                raise ValueError('网盘配置依赖本地文件，未合并')
            if value.startswith(('./','../')):
                value=urljoin(source_url,value)
            if value.startswith(('http://','https://')):
                parsed=urlsplit(value)
                if parsed.username or parsed.password or secret_check(value):
                    raise ValueError('网盘配置链接包含登录凭据，未复制')
        return value

    raw_spider=config.get('spider')
    if not isinstance(raw_spider,str):
        raise ValueError('网盘配置未提供单一插件')
    bits=raw_spider.split(';')
    spider=normalize(urljoin(source_url,bits[0]))
    if (not spider or secret_check(spider) or
            len(bits) not in (1,3) or len(bits)==3 and
            (bits[1]!='md5' or not re.fullmatch(r'[a-fA-F0-9]{32}',bits[2]))):
        raise ValueError('网盘插件地址不支持合并')
    selected, seen=[],set()
    for site in config.get('sites',[]) if isinstance(config.get('sites'),list) else []:
        if not isinstance(site,dict) or site.get('type')!=3:
            continue
        identity=' '.join(site.get(key,'') for key in ('key','name','api') if isinstance(site.get(key),str))
        if blocked_labels.search(identity) or not (CLOUD_SITE.search(identity) or LOGIN_SITE.search(identity)):
            continue
        if (not isinstance(site.get('key'),str) or not site['key'] or site['key'] in seen
                or not isinstance(site.get('name'),str) or not site['name']
                or not isinstance(site.get('api'),str) or not site['api'].startswith('csp_')
                or site.get('jar')):
            continue
        seen.add(site['key'])
        selected.append(portable(site))
    info=describe_cloud_config({'sites':selected})
    if info['site_count']<2 or not info['login_names']:
        raise ValueError('网盘站点或客户端登录入口不完整')
    output={key:portable(config[key]) for key in
            ('wallpaper','logo','danmaku','parses','rules','doh','flags','ads','hosts') if key in config}
    output.update(spider=';'.join([spider,*bits[1:]]),sites=selected)
    return {'schema_version':1,'source_url':source_url,'source_name':source_name,
            'checked_at':checked_at,'config':output}


def save_cloud_snapshot(root, records, documents, parse, normalize, secret_check, settings):
    source_url=normalize(settings.get('source_url'))
    snapshot={'schema_version':1,'source_url':source_url,'config':{}}
    for row in fresh_cloud_configs(records,normalize,secret_check):
        if row['url']!=source_url or row['category']=='adult' or source_url not in documents:
            continue
        text, final_url=documents[source_url]
        try:
            snapshot=prepare_cloud_snapshot(parse(text),final_url,row['name'],row['checked_at'],normalize,secret_check)
            snapshot['source_url']=source_url
            snapshot['document_url']=final_url
        except (ValueError,TypeError,RecursionError):
            snapshot={'schema_version':1,'source_url':source_url,'config':{}}
        break
    target=Path(root)/'data/cloud-sites.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(snapshot,ensure_ascii=False,indent=2)+'\n')
    return snapshot


def fresh_cloud_merge(root, cloud_configs, normalize, secret_check, settings):
    empty=({}, {'site_count':0,'searchable_count':0,'login_names':[]})
    target=Path(root)/'data/cloud-sites.json'
    if not target.exists():
        return empty
    try:
        snapshot=json.loads(target.read_text())
        selected=normalize(settings.get('source_url'))
        row=next(row for row in cloud_configs if row['url']==selected and row['category']=='ordinary')
        if snapshot.get('source_url')!=selected or snapshot.get('checked_at')!=row['checked_at']:
            return empty
        base=snapshot.get('document_url') or selected
        clean=prepare_cloud_snapshot(snapshot['config'],base,row['name'],row['checked_at'],normalize,secret_check)
        config=clean['config']
        info=describe_cloud_config(config)
        searchable=sum(site.get('searchable',1)!=0 and not LOGIN_SITE.search(
            ' '.join(str(site.get(key,'')) for key in ('key','name','api'))) for site in config['sites'])
        return config, {**info,'searchable_count':searchable,'source_name':row['name'],
                        'source_url':selected,'checked_at':row['checked_at']}
    except (OSError,ValueError,TypeError,KeyError,StopIteration,RecursionError):
        return empty
