import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts import build as site_builder
from scripts.cloud import prepare_cloud_snapshot, save_cloud_snapshot, fresh_cloud_merge, fresh_cloud_configs
from scripts.collector import normalize_url, has_secret_query
from scripts.routes import publish_routes


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

    def family_fixture(self):
        stamp=datetime.now(timezone.utc).isoformat()
        url='https://example.com/tv/x.json'
        config={
            'spider':'./x.jpg;md5;'+'a'*32,
            'ijk':[{'group':'original','options':{'codec':'hardware'}}],
            'proxy':[{'host':'video.example.com','url':'https://example.com/proxy'}],
            'danmaku':'http://127.0.0.1:9978/proxy?do=danmaku',
            'rules':[{'name':'literal','regex':['./keep-as-regex']}],
            'custom_context':{'asset':'../client/settings.json'},
            'lives':[{'url':'https://example.com/unverified.m3u'}],
            'sites':[
                {'key':'duplicate','name':'旧同名站','type':1,'api':'https://example.com/old','searchable':1},
                {'key':'xml','name':'原XML','type':0,'api':'./feed.xml','searchable':1},
                {'key':'plain','name':'原JSON','type':1,'api':'https://cj.lziapi.com/api','searchable':1,'quickSearch':0},
                {'key':'huban','name':'弹幕|小窗','type':3,'api':'csp_Huban',
                 'jar':'./HubanTC.jar;md5;'+'b'*32,'searchable':1,'quickSearch':1},
                {'key':'js','name':'原JS','type':3,'api':'./api.js','searchable':1,
                 'ext':'{"config":"../client/ext.json"}'},
                {'key':'wogg','name':'玩偶','type':3,'api':'csp_Wogg','searchable':1,
                 'ext':'https://tvfan.eu.org/Cloud-drive.txt'},
                {'key':'duplicate','name':'末同名站','type':1,'api':'https://example.com/new','searchable':0},
                {'key':'secret','name':'带凭据站','type':3,'api':'csp_Other',
                 'ext':{'token':'family-member-secret'}},
                {'key':'local','name':'本地配置','type':3,'api':'csp_Other','ext':{'json':'file:///client/a.json'}},
                {'key':'adult','name':'成人资源','type':3,'api':'csp_Other'},
                {'key':'invalid','name':'未知类型','type':2,'api':'https://example.com/api'}]}
        row={'id':'a'*16,'name':'用户验证配置','url':url,'role':'cloud','kind':'config',
             'status':'ok','count':len(config['sites']),'checked_at':stamp,'cloud':{'site_count':1}}
        return row,config

    def test_family_keeps_original_plugins_and_context_and_skips_only_invalid_sites(self):
        row,config=self.family_fixture()
        snapshot=prepare_cloud_snapshot(config,row['url'],row['name'],row['checked_at'],
                                        normalize_url,has_secret_query,mode='family')
        output=snapshot['config']
        self.assertEqual(snapshot['mode'],'family')
        self.assertEqual([site['key'] for site in output['sites']],['xml','plain','huban','js','wogg','duplicate'])
        self.assertEqual(output['sites'][2]['jar'],'https://example.com/tv/HubanTC.jar;md5;'+'b'*32)
        self.assertEqual(output['sites'][2]['name'],'弹幕|小窗')
        self.assertEqual(output['sites'][2]['api'],'csp_Huban')
        self.assertEqual(output['sites'][1]['quickSearch'],0)
        self.assertEqual(output['sites'][3]['api'],'https://example.com/tv/api.js')
        self.assertEqual(json.loads(output['sites'][3]['ext'])['config'],'https://example.com/client/ext.json')
        self.assertEqual(output['sites'][4]['ext'],'https://tvfan.eu.org/Cloud-drive.txt')
        self.assertEqual(output['sites'][5]['api'],'https://example.com/new')
        self.assertEqual(output['ijk'],config['ijk'])
        self.assertEqual(output['proxy'],config['proxy'])
        self.assertEqual(output['rules'],config['rules'])
        self.assertEqual(output['danmaku'],config['danmaku'])
        self.assertEqual(output['custom_context']['asset'],'https://example.com/client/settings.json')
        self.assertNotIn('lives',output)
        self.assertEqual(len(snapshot['skipped_sites']),5)
        self.assertNotIn('family-member-secret',json.dumps(snapshot))
        # A rejected last duplicate must not revive an earlier valid occurrence.
        config['sites'].append({'key':'huban','name':'末项凭据','type':3,'api':'csp_Huban',
                                'ext':{'token':'last-member-secret'}})
        latest=prepare_cloud_snapshot(config,row['url'],row['name'],row['checked_at'],
                                      normalize_url,has_secret_query,mode='family')
        self.assertNotIn('huban',[site['key'] for site in latest['config']['sites']])
        self.assertNotIn('last-member-secret',json.dumps(latest))

    def test_family_snapshot_revalidation_keeps_reports_and_allows_one_cloud_site(self):
        row,config=self.family_fixture()
        settings={'source_url':row['url'],'mode':'family'}
        self.assertEqual(fresh_cloud_configs([row],normalize_url,has_secret_query),[])
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            documents={row['url']:(json.dumps(config),row['url'])}
            snapshot=save_cloud_snapshot(root,[row],documents,json.loads,normalize_url,has_secret_query,settings)
            configs=fresh_cloud_configs([row],normalize_url,has_secret_query,settings)
            self.assertEqual(len(configs),1)
            original,meta=fresh_cloud_merge(root,configs,normalize_url,has_secret_query,settings)
            self.assertEqual(original,snapshot['config'])
            self.assertEqual(meta['mode'],'family')
            self.assertEqual(meta['total_sites'],6)
            self.assertEqual(meta['searchable_count'],5)
            self.assertEqual(meta['cloud_site_count'],1)
            self.assertEqual(meta['site_count'],1)
            self.assertEqual(meta['skipped_sites'],snapshot['skipped_sites'])
            self.assertEqual(meta['skipped_site_count'],5)
            self.assertNotIn('family-member-secret',(root/'data/cloud-sites.json').read_text())
            self.assertEqual(fresh_cloud_merge(root,configs,normalize_url,has_secret_query,
                                              {'source_url':row['url']})[0],{})
            variants=[{**row,'status':'error'},{**row,'category':'adult'},
                      {**row,'checked_at':(datetime.now(timezone.utc)-timedelta(hours=37)).isoformat()},
                      {**row,'checked_at':datetime.now(timezone.utc).isoformat()}]
            for invalid in variants:
                with self.subTest(invalid=invalid):
                    changed=fresh_cloud_configs([invalid],normalize_url,has_secret_query,settings)
                    self.assertEqual(fresh_cloud_merge(root,changed,normalize_url,has_secret_query,settings)[0],{})
            changed=fresh_cloud_configs([row,{**row,'category':'adult','status':'error'}],
                                         normalize_url,has_secret_query,settings)
            self.assertEqual(fresh_cloud_merge(root,changed,normalize_url,has_secret_query,settings)[0],{})

    def test_family_requires_two_safe_sites_and_a_searchable_site_without_global_credentials(self):
        row,config=self.family_fixture()
        for sites in ([config['sites'][2]],
                      [{**config['sites'][1],'searchable':0},{**config['sites'][2],'searchable':0}]):
            with self.subTest(sites=sites):
                with self.assertRaises(ValueError):
                    prepare_cloud_snapshot({**config,'sites':sites},row['url'],row['name'],row['checked_at'],
                                           normalize_url,has_secret_query,mode='family')
        with self.assertRaises(ValueError):
            prepare_cloud_snapshot({**config,'token':'global-member-secret'},row['url'],row['name'],row['checked_at'],
                                   normalize_url,has_secret_query,mode='family')
        with self.assertRaises(ValueError):
            prepare_cloud_snapshot({**config,'wallpaper':'HTTPS://fixture:member-secret@example.com/image.png'},
                                   row['url'],row['name'],row['checked_at'],normalize_url,has_secret_query,mode='family')

    def test_family_filters_explicit_adult_labels_without_collapsing_shared_plugin_classes(self):
        row,config=self.family_fixture()
        config['sites']=[
            {'key':'wood','name':'木偶4k网盘','type':3,'api':'csp_MoggCAT','ext':'https://example.com/wood'},
            {'key':'tiger','name':'虎斑4k网盘','type':3,'api':'csp_MoggCAT','ext':'https://example.com/tiger'},
            {'key':'adult','name':'4Kav','type':3,'api':'csp_Other'},
            {'key':'type4','name':'盘搜|集合','type':4,'api':'https://example.com/search-api'},
            {'key':'adult2','name':'倫理资源','type':3,'api':'csp_Other'},
            {'key':'risk','name':'未滿十八','type':3,'api':'csp_Other'}]
        output=prepare_cloud_snapshot(config,row['url'],row['name'],row['checked_at'],
                                      normalize_url,has_secret_query,mode='family')
        self.assertEqual([site['key'] for site in output['config']['sites']],['wood','tiger','adult','type4'])
        self.assertEqual(len(output['skipped_sites']),2)

    def test_family_accepts_only_exact_client_credential_file_references(self):
        row,config=self.family_fixture()
        pointer='http://127.0.0.1:9978/file/cloud/uc_cookie.txt'
        allowed=[{'key':'wood','name':'木偶网盘','type':3,'api':'csp_MoggCAT','ext':{'cookie':pointer}},
                 {'key':'tiger','name':'虎斑网盘','type':3,'api':'csp_MoggCAT',
                  'ext':json.dumps({'token':pointer})}]
        output=prepare_cloud_snapshot({**config,'sites':allowed},row['url'],row['name'],row['checked_at'],
                                      normalize_url,has_secret_query,mode='family')
        self.assertEqual(output['config']['sites'],allowed)
        for rejected in ('actual-member-secret',
                         'http://127.0.0.1:9979/file/cloud/uc_cookie.txt',
                         'http://localhost:9978/file/cloud/uc_cookie.txt',
                         'http://127.0.0.1:9978/file/../uc_cookie.txt',
                         'http://127.0.0.1:9978/file/cloud/uc_cookie.json',
                         pointer+'?token=actual-member-secret',pointer+'#fragment',
                         'http://member:secret@127.0.0.1:9978/file/cloud/uc_cookie.txt'):
            with self.subTest(rejected=rejected):
                invalid={'key':'invalid','name':'凭据站','type':3,'api':'csp_Other','ext':{'token':rejected}}
                snapshot=prepare_cloud_snapshot({**config,'sites':allowed+[invalid]},row['url'],row['name'],row['checked_at'],
                                                normalize_url,has_secret_query,mode='family')
                self.assertEqual(snapshot['config']['sites'],allowed)
                self.assertEqual(snapshot['skipped_sites'],[{'index':2,'reason':'含登录凭据'}])
                self.assertNotIn('actual-member-secret',json.dumps(snapshot))
        for credential_url in ('HTTPS://example.com/profile.json?accessToken=actual-member-secret',
                               '../auth.json?access_token=fixture-secret'):
            with self.subTest(credential_url=credential_url):
                invalid={'key':'url-secret','name':'网址参数站','type':3,'api':'csp_Other',
                         'ext':{'config':credential_url}}
                snapshot=prepare_cloud_snapshot({**config,'sites':allowed+[invalid]},row['url'],row['name'],row['checked_at'],
                                                normalize_url,has_secret_query,mode='family')
                self.assertEqual(snapshot['config']['sites'],allowed)
                self.assertEqual(snapshot['skipped_sites'],[{'index':2,'reason':'含登录凭据'}])
                self.assertNotIn('actual-member-secret',json.dumps(snapshot))
                self.assertNotIn('fixture-secret',json.dumps(snapshot))

    def test_family_publishes_original_sites_first_and_adds_only_new_verified_api_and_keys(self):
        stamp=datetime.now(timezone.utc).isoformat()
        def verified(id,api,category='ordinary'):
            return {'id':id,'name':id,'api':api,'category':category,'status':'passed',
                    'passed':1,'checked_at':stamp,'searchable':1}
        original={'spider':'https://example.com/x.jpg','ijk':['original'],
                  'sites':[{'key':'huban','name':'弹幕|小窗','type':3,'api':'csp_Huban',
                            'jar':'https://example.com/HubanTC.jar'},
                           {'key':'json','name':'原普通接口','type':1,
                            'api':'HTTPS://CJ.LZIAPI.COM:443/api/?wd=old&pg=1&b=2&a=1'},
                           {'key':'pocket_collision','name':'原插件键','type':3,'api':'csp_Original'}]}
        document={'routes':[verified('duplicate','https://cj.lziapi.com/api?a=1&b=2'),
                            verified('collision','https://360zyzz.com/api'),
                            verified('added','https://jszyapi.com/api'),
                            verified('adult','https://api.yirenziyuan.com/api','adult')]}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            publish_routes(root,document,'https://example.com/pocket/',original,mode='family')
            combined=json.loads((root/'checked/vod-all.json').read_text())
            self.assertEqual(combined['sites'][:3],original['sites'])
            self.assertEqual([site['key'] for site in combined['sites']],['huban','json','pocket_collision','pocket_added'])
            self.assertEqual(combined['spider'],original['spider'])
            self.assertEqual(combined['ijk'],original['ijk'])
            groups=json.loads((root/'checked/routes.json').read_text())['urls']
            self.assertEqual(groups,[{'name':'普通点播','url':'https://example.com/pocket/checked/vod-all.json?profile=family'},
                                     {'name':'成人点播','url':'https://example.com/pocket/checked/vod-adult.json'}])
            self.assertEqual(json.loads((root/'checked/routes-ordinary.json').read_text())['urls'],groups[:1])

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
                    {'pwd':'member-secret'}, 'HTTPS://fixture:member-secret@example.com/config.json',
                    ' HTTPS://example.com/profile.json?accessToken=member-secret ',
                    '{"config":"HTTPS://example.com/profile.json?accessToken=member-secret"}',
                    '../auth.json?access_token=fixture-secret',
                    ' FILE:///client/profile.txt'):
            with self.subTest(ext=ext):
                config['sites'][1]['ext']=ext
                with self.assertRaises(ValueError):
                    prepare_cloud_snapshot(config,row['url'],row['name'],row['checked_at'],normalize_url,has_secret_query)

    def test_global_credential_urls_are_case_insensitive_in_both_modes(self):
        row,config=self.cloud_fixture()
        for mode in ('selective','family'):
            for wallpaper in ('HTTPS://fixture:member-secret@example.com/image.png',
                              ' HTTPS://example.com/image.png?accessToken=member-secret ',
                              '../auth.json?access_token=fixture-secret',
                              ' FILE:///client/profile.txt', 'CONTENT://client/profile.txt',
                              'CLAN://client/profile.txt'):
                with self.subTest(mode=mode,wallpaper=wallpaper):
                    with self.assertRaises(ValueError):
                        prepare_cloud_snapshot({**config,'wallpaper':wallpaper},row['url'],row['name'],row['checked_at'],
                                               normalize_url,has_secret_query,mode=mode)

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
