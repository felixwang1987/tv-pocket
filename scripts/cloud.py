"""Describe public cloud configs without loading their plugins or credentials."""
from datetime import datetime, timezone
import re

try:
    from .playback import FRESH_SECONDS
except ImportError:
    from playback import FRESH_SECONDS

CLOUD_SITE = re.compile(r'网盘|云盘|盘搜|盘搜索|玩偶|pans?(?:search|sou|so|ali|baidu|quark|uc)|yunpan|wogg|wo4k|alist',re.I)
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
    result, seen = [], set()
    for row in entries:
        try:
            age=(current-datetime.fromisoformat(row['checked_at'])).total_seconds()
            info=row.get('cloud') or {}
            if (row.get('role')!='cloud' or row.get('status')!='ok' or row.get('kind')!='config'
                    or row.get('count',0)<2 or info.get('site_count',0)<2 or not 0<=age<=FRESH_SECONDS):
                continue
            url=normalize(row['url'])
            if not url or secret_check(url) or url in seen or not re.fullmatch(r'[a-f0-9]{16}',row['id']):
                continue
            seen.add(url)
            result.append({'id':row['id'],'name':'网盘 · '+str(row['name'])[:100]+'（需自行登录）',
                           'url':url,'checked_at':row['checked_at'],'total_sites':row['count'],
                           'site_count':info['site_count'],'providers':info.get('providers',[]),
                           'login_names':info.get('login_names',[])})
        except (KeyError,TypeError,ValueError):
            continue
    return result
