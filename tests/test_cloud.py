import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from scripts.collector import normalize_url, has_secret_query
from scripts.cloud import describe_cloud_config, fresh_cloud_configs
from scripts.routes import publish_routes


class CloudTests(unittest.TestCase):
    def test_cloud_identity_is_read_without_auth_files_or_plugin_execution(self):
        config={'spider':'./spider.jar','sites':[
            {'key':'login','name':'配置中心','type':3,'api':'csp_Config'},
            {'key':'guard','name':'我的云盘┃配置','type':3,'api':'csp_MyDriveGuard'},
            {'key':'wogg','name':'玩偶','type':3,'api':'csp_Wogg','ext':'./private-token.json'},
            {'key':'search','name':'盘搜','type':3,'api':'csp_PanSou'},
            {'key':'ali','name':'阿里云盘','type':3,'api':'csp_PanAli'},
            {'key':'baidu','name':'百度网盘','type':3,'api':'csp_PanBaidu'},
            {'key':'quark','name':'夸克网盘','type':3,'api':'csp_PanQuark'},
            {'key':'uc','name':'UC网盘','type':3,'api':'csp_PanUC'},
            {'key':'ordinary','name':'普通电影','type':1,'api':'https://example.com/api'}]}
        info=describe_cloud_config(config)
        self.assertEqual(info['site_count'],6)
        self.assertEqual(info['providers'],['阿里云盘','百度网盘','夸克','UC'])
        self.assertEqual(info['login_names'],['配置中心','我的云盘┃配置'])
        self.assertNotIn('private-token',json.dumps(info))
        self.assertEqual(describe_cloud_config({'sites':[config['sites'][-1]]})['site_count'],0)

    def test_same_vod_import_keeps_original_cloud_configs_and_requires_fresh_multisite_sources(self):
        stamp=datetime.now(timezone.utc)
        def row(url,**overrides):
            value={'id':'a'*16,'name':'网盘多站','url':url,'role':'cloud','kind':'config',
                   'status':'ok','count':8,'checked_at':stamp.isoformat(),
                   'cloud':{'site_count':3,'providers':['夸克'],'login_names':['配置中心']}}
            value.update(overrides)
            return value
        url='https://example.com/original/config.json'
        records=[row(url),row(url),row('https://example.com/failed.json',status='error'),
                 row('https://example.com/single.json',cloud={'site_count':1}),
                 row('https://example.com/stale.json',checked_at=(stamp-timedelta(hours=37)).isoformat()),
                 row('https://example.com/unknown.json',role='ordinary'),
                 row('https://example.com/private.json?token=secret'),row('http://127.0.0.1/config.json')]
        configs=fresh_cloud_configs(records,normalize_url,has_secret_query)
        self.assertEqual(len(configs),1)
        self.assertEqual(configs[0]['url'],url)
        self.assertEqual(configs[0]['name'],'网盘 · 网盘多站（需自行登录）')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            vod={'id':'ordinary','name':'电影站','api':'https://cj.lziapi.com/api',
                 'status':'passed','passed':1,'checked_at':stamp.isoformat()}
            publish_routes(root,{'routes':[vod]},'https://user.github.io/project/',configs)
            exported=json.loads((root/'checked/routes.json').read_text())['urls']
            self.assertEqual(exported,[
                {'name':'普通点播','url':'https://user.github.io/project/checked/vod-all.json'},
                {'name':'网盘 · 网盘多站（需自行登录）','url':url}])
            self.assertEqual(len(json.loads((root/'checked/vod-all.json').read_text())['sites']),1)
            self.assertEqual(list((root/'checked/vod').glob('*.json')),[root/'checked/vod/ordinary.json'])
