import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts import build as site_builder
from scripts.cloud import prepare_cloud_snapshot, save_cloud_snapshot, fresh_cloud_merge, fresh_cloud_configs
from scripts.collector import normalize_url, has_secret_query


class CloudMergeTests(unittest.TestCase):
    def cloud_fixture(self):
        stamp=datetime.now(timezone.utc).isoformat()
        url='https://example.com/cloud/api.json'
        row={'id':'a'*16,'name':'网盘来源','url':url,'role':'cloud','kind':'config',
             'status':'ok','count':8,'checked_at':stamp,'cloud':{'site_count':2}}
        config={'spider':'./spider.jar;md5;'+'a'*32,'danmaku':'http://127.0.0.1:9978/proxy?do=danmaku',
                'parses':[{'name':'聚合','type':3,'url':'Web'}], 'lives':[{'url':'https://example.com/unverified.m3u'}],
                'sites':[
                    {'key':'login','name':'配置中心','type':3,'api':'csp_Config','searchable':0},
                    {'key':'wogg','name':'玩偶','type':3,'api':'csp_Wogg','searchable':1,
                     'quickSearch':1,'ext':{'site':['https://example.com/wogg']}},
                    {'key':'wood','name':'木偶','type':3,'api':'csp_PanWebShare','searchable':1,'quickSearch':1,
                     'ext':'./settings.json'},
                    {'key':'other','name':'普通插件','type':3,'api':'csp_Other'},
                    {'key':'adult','name':'成人网盘','type':3,'api':'csp_PanSou'},
                    {'key':'otherjar','name':'其他网盘','type':3,'api':'csp_PanSou','jar':'./other.jar'}]}
        return row,config

    def test_plugin_paths_and_client_context_survive_selective_merge(self):
        row,config=self.cloud_fixture()
        snapshot=prepare_cloud_snapshot(config,row['url'],row['name'],row['checked_at'],normalize_url,has_secret_query)
        output=snapshot['config']
        self.assertEqual(output['spider'],'https://example.com/cloud/spider.jar;md5;'+'a'*32)
        self.assertEqual([site['key'] for site in output['sites']],['login','wogg','wood'])
        self.assertEqual(output['sites'][-1]['ext'],'https://example.com/cloud/settings.json')
        self.assertEqual(output['sites'][-1]['api'],'csp_PanWebShare')
        self.assertEqual(output['sites'][-1]['quickSearch'],1)
        self.assertEqual(output['danmaku'],config['danmaku'])
        self.assertEqual(output['parses'][0]['url'],'Web')
        self.assertNotIn('lives',output)

    def test_credentials_in_site_extensions_are_never_copied(self):
        row,config=self.cloud_fixture()
        for ext in ({'token':'member-secret'}, {'headers':{'Cookie':'member-secret'}}, {'UcCookie':'member-secret'},
                    'https://example.com/auth.json?access_token=member-secret',
                    '{"token":"member-secret"}', 'https://fixture:fixture@example.com/config.json',
                    {'pwd':'member-secret'}):
            with self.subTest(ext=ext):
                config['sites'][1]['ext']=ext
                with self.assertRaises(ValueError):
                    prepare_cloud_snapshot(config,row['url'],row['name'],row['checked_at'],normalize_url,has_secret_query)

    def test_failed_stale_or_adult_source_cannot_reuse_old_cloud_sites(self):
        row,config=self.cloud_fixture()
        settings={'source_url':row['url']}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            documents={row['url']:(json.dumps(config),row['url'])}
            save_cloud_snapshot(root,[row],documents,json.loads,normalize_url,has_secret_query,settings)
            configs=fresh_cloud_configs([row],normalize_url,has_secret_query)
            self.assertEqual(fresh_cloud_merge(root,configs,normalize_url,has_secret_query,settings)[1]['site_count'],2)
            variants=[{**row,'status':'error'},{**row,'category':'adult'},
                      {**row,'checked_at':(datetime.now(timezone.utc)-timedelta(hours=37)).isoformat()},
                      {**row,'checked_at':datetime.now(timezone.utc).isoformat()}]
            for invalid in variants:
                with self.subTest(invalid=invalid):
                    configs=fresh_cloud_configs([invalid],normalize_url,has_secret_query)
                    self.assertEqual(fresh_cloud_merge(root,configs,normalize_url,has_secret_query,settings)[0],{})
            configs=fresh_cloud_configs([row,{**row,'category':'adult','status':'error'}],normalize_url,has_secret_query)
            self.assertEqual(fresh_cloud_merge(root,configs,normalize_url,has_secret_query,settings)[0],{})
            save_cloud_snapshot(root,[{**row,'status':'error'}],{},json.loads,normalize_url,has_secret_query,settings)
            self.assertEqual(json.loads((root/'data/cloud-sites.json').read_text())['config'],{})

    def test_cloud_and_standard_sites_search_from_the_same_published_config(self):
        stamp = datetime.now(timezone.utc).isoformat()
        source_url = 'https://example.com/cloud/api.json'
        entry = {'id':'a'*16, 'name':'网盘来源', 'url':source_url,
                 'role':'cloud', 'kind':'config', 'status':'ok', 'count':8,
                 'checked_at':stamp, 'cloud':{'site_count':2,'login_names':['配置中心']}}
        snapshot = {'schema_version':1, 'source_url':source_url, 'source_name':'网盘来源',
                    'checked_at':stamp, 'config':{
            'spider':'https://example.com/cloud/spider.jar;md5;'+'a'*32,
            'flags':['cloud'], 'sites':[
                {'key':'login','name':'配置中心','type':3,'api':'csp_Config','searchable':0},
                {'key':'wogg','name':'玩偶','type':3,'api':'csp_Wogg','searchable':1,
                 'quickSearch':1,'ext':{'site':['https://example.com/wogg']}},
                {'key':'wood','name':'木偶','type':3,'api':'csp_PanWebShare',
                 'searchable':1,'quickSearch':1,'ext':{'site':['https://example.com/wood']}}]}}
        route = {'id':'normal','name':'普通电影站','api':'https://cj.lziapi.com/api',
                 'status':'passed','passed':1,'checked_at':stamp,'searchable':1}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'data').mkdir()
            (root/'index.html').write_text('<script id="catalog-data" type="application/json">{}</script>')
            (root/'data/sources.json').write_text(json.dumps({'entries':[entry]}))
            (root/'data/routes.json').write_text(json.dumps({'routes':[route]}))
            (root/'data/cloud-sites.json').write_text(json.dumps(snapshot))
            (root/'sources.config.json').write_text(json.dumps({
                'routes':{'base_url':'https://example.com/pocket/'},
                'cloud_merge':{'source_url':source_url}}))
            for asset in ('apple-touch-icon.png','icon-192.png','icon-512.png','icon.svg','site.webmanifest'):
                (root/asset).write_text('fixture')
            with patch.object(site_builder, 'ROOT', root):
                site_builder.build(site=True)
            combined = json.loads((root/'checked/vod-all.json').read_text())
            self.assertEqual({site['name'] for site in combined['sites']},
                             {'普通电影站','配置中心','玩偶','木偶'})
            self.assertEqual(combined['spider'], snapshot['config']['spider'])
            self.assertEqual(combined['flags'], ['cloud'])
            self.assertEqual(len([site for site in combined['sites'] if site['searchable']]),3)
            groups = json.loads((root/'checked/routes.json').read_text())['urls']
            self.assertEqual([group['url'] for group in groups],
                             ['https://example.com/pocket/checked/vod-all.json'])
            self.assertEqual((root/'checked/vod-all.json').read_bytes(),
                             (root/'_site/checked/vod-all.json').read_bytes())
            data = json.loads((root/'data/sources.json').read_text())
            self.assertEqual(data['cloud_merge']['site_count'],2)
            self.assertEqual(data['cloud_merge']['searchable_count'],2)
            self.assertEqual(data['cloud_merge']['login_names'],['配置中心'])


if __name__ == '__main__':
    unittest.main()
