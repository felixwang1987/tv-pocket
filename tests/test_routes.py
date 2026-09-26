import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlsplit, parse_qs

from scripts.collector import normalize_url, parse_jsonc
from scripts.routes import discover_routes, check_route, publish_routes, api_request_url, program_rows


class Network:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def fetch_bytes(self, url, **kwargs):
        self.calls.append(url)
        value = self.responses(url) if callable(self.responses) else self.responses[url]
        if isinstance(value, Exception):
            raise value
        if not isinstance(value, (str, bytes)):
            value = json.dumps(value)
        return value.encode() if isinstance(value, str) else value, url


class RouteTests(unittest.TestCase):
    def test_nested_stores_flatten_and_deduplicate_apis_without_executing_plugins(self):
        root='https://example.com/store/root.json'
        documents={root:(json.dumps({'storeHouse':[{'sourceUrl':'child.json'}]}), root)}
        net=Network({'https://example.com/store/child.json':{'urls':[{'url':'../config.json'},{'url':'root.json'}]},
                     'https://example.com/config.json':{'spider':'https://example.com/code.jar','sites':[
                         {'key':'a','name':'甲','type':1,'api':'./api?ac=list'},
                         {'key':'b','name':'重复','type':1,'api':'./api?ac=videolist'},
                         {'key':'c','name':'插件','type':3,'api':'csp_Test'}]}})
        routes, summary=discover_routes(documents, net, parse_jsonc, normalize_url, {'max_documents':10,'approved_api_hosts':['example.com']})
        self.assertEqual(len(routes),1)
        self.assertEqual(routes[0]['api'],'https://example.com/api')
        self.assertNotIn('https://example.com/code.jar',net.calls)
        self.assertEqual(summary['unsupported_sites'],1)

    def test_query_parameters_are_replaced_without_losing_feed_identity(self):
        url=api_request_url('https://example.com/api?ac=list&pg=9&from=direct',ac='videolist',ids='a b')
        self.assertEqual(parse_qs(urlsplit(url).query),{'ac':['videolist'],'pg':['9'],'from':['direct'],'ids':['a b']})

    def test_a_playable_sample_is_required_not_just_a_readable_catalog(self):
        def respond(url):
            q=parse_qs(urlsplit(url).query)
            if 'ids' in q:
                return {'list':[{'vod_id':'1','type_name':'国产剧','vod_name':'示例影片','vod_play_url':'正片$https://example.com/video.m3u8'}]}
            return {'list':[{'vod_id':'1','type_name':'国产剧','vod_name':'示例影片'}]}
        row={'id':'abc','name':'示例','api':'https://example.com/api','sources':[]}
        failed=check_route(row,Network(respond),probe=lambda n,u:{'status':'failed','reason':'无法解码'})
        passed=check_route(row,Network(respond),probe=lambda n,u:{'status':'passed','reason':'出画面'})
        self.assertEqual(failed['status'],'failed')
        self.assertEqual(passed['status'],'passed')
        self.assertEqual(passed['passed'],1)

    def test_parse_pages_and_paid_or_script_sites_never_become_verified_routes(self):
        root='https://example.com/config'
        sites=[{'key':'jar','name':'需要插件','type':3,'api':'csp_Test'},
               {'key':'parse','name':'需解析','type':1,'api':'https://example.com/api','playUrl':'https://example.com/parse?url='},
               {'key':'header','name':'需请求头','type':1,'api':'https://example.com/headers','header':{'Cookie':'secret'}},
               {'key':'adult','name':'成人专用','type':1,'api':'https://example.com/adult'}]
        rows,_=discover_routes({root:(json.dumps({'sites':sites}),root)},Network({}),parse_jsonc,normalize_url,{})
        self.assertEqual(rows,[])

    def test_video_page_does_not_reach_decoder_as_a_stream(self):
        net=Network(lambda url:{'list':[{'vod_id':'1','type_name':'国产剧','vod_name':'示例影片','vod_play_url':'正片$https://example.com/watch/1.html'}]})
        result=check_route({'id':'a','name':'a','api':'https://example.com/api'},net,
                           probe=lambda n,u:self.fail('HTML page submitted to playback'))
        self.assertEqual(result['status'],'unverified')

    def test_detail_failure_preserves_another_playable_program(self):
        def respond(url):
            q=parse_qs(urlsplit(url).query)
            if q.get('ids')==['2']:
                raise OSError('详情暂时不可达')
            if 'ids' in q:
                return {'list':[{'vod_id':'1','type_name':'国产剧','vod_name':'甲','vod_play_url':'正片$https://example.com/video.m3u8'}]}
            return {'list':[{'vod_id':'1','type_name':'国产剧','vod_name':'甲'},{'vod_id':'2','type_name':'国产剧','vod_name':'乙'}]}
        result=check_route({'id':'a','name':'a','api':'https://example.com/api'},Network(respond),
                           probe=lambda n,u:{'status':'passed','reason':'出画面'})
        self.assertEqual((result['status'],result['sampled'],result['passed']),('partial',2,1))

    def test_ignored_search_query_is_not_marked_searchable(self):
        net=Network(lambda url:{'list':[{'vod_id':'1','type_name':'国产剧','vod_name':'甲','vod_play_url':'正片$https://example.com/video.m3u8'}]})
        result=check_route({'id':'a','name':'a','api':'https://example.com/api'},net,
                           probe=lambda n,u:{'status':'passed','reason':'出画面'})
        self.assertEqual(result['searchable'],0)

    def test_unknown_api_and_unclassified_programs_are_excluded(self):
        root='https://example.com/config'
        sites=[{'name':'普通名称','type':1,'api':'https://unreviewed.example/api'}]
        rows,_=discover_routes({root:(json.dumps({'sites':sites}),root)},Network({}),parse_jsonc,normalize_url,{})
        self.assertEqual(rows,[])
        programs=[{'vod_id':'1','type_name':'国产剧','vod_name':'节目','type_name':'国产剧'},
                  {'vod_id':'2','vod_name':'节目'},
                  {'vod_id':'3','type_name':'国产剧','vod_name':'节目','type_name':'伦理剧'}]
        self.assertEqual([r['vod_id'] for r in program_rows({'list':programs})],['1'])

    def test_collection_contains_only_fresh_successful_site_configs(self):
        stamp=datetime.now(timezone.utc)
        def row(key,status,age=0):
            return {'id':key,'name':key,'api':'https://cj.lziapi.com/'+key,'status':status,'passed':1,
                    'checked_at':(stamp-timedelta(hours=age)).isoformat(),'samples':[]}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            document={'generated_at':stamp.isoformat(),'routes':[row('good','passed'),row('bad','failed'),row('old','passed',19)]}
            unknown=row('unknown','passed')
            unknown['api']='https://unknown.example/api'
            document['routes'].append(unknown)
            count=publish_routes(root,document,'https://user.github.io/project/')
            collection=json.loads((root/'checked/routes.json').read_text())
            merged=json.loads((root/'checked/vod-all.json').read_text())
            self.assertEqual(count,1)
            self.assertEqual(len(merged['sites']),1)
            self.assertEqual(merged['sites'][0]['api'],'https://cj.lziapi.com/good')
            self.assertEqual(collection['urls'][1]['url'],'https://user.github.io/project/checked/vod/good.json')
            self.assertNotIn('spider',merged)
            document['routes'][0]['status']='failed'
            publish_routes(root,document,'https://user.github.io/project/')
            self.assertFalse((root/'checked/vod/good.json').exists())
            self.assertEqual(json.loads((root/'checked/routes.json').read_text())['urls'],[])


if __name__=='__main__':
    unittest.main()
