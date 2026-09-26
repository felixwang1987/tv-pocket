import unittest
from pathlib import Path
import json
import tempfile
from unittest.mock import patch, MagicMock
import scripts.collector as collector
from urllib.request import Request
from urllib.parse import parse_qs, urlsplit
from scripts.collector import (classify, extract_links, normalize_url, merge_records, check_public_url,
                               extract_source_page_links, extract_generic_page_links, discover_source_pages)


class ParsingTests(unittest.TestCase):
    def test_generic_page_extracts_public_static_sources_only(self):
        html = '''<nav><a href="/menu.json">导航</a></nav>
        <main><a href="/tv/pack.json">多仓配置</a>
        <p>直播 https://cdn.example/live.m3u</p>
        <a href="https://cdn.example/poster.png">封面</a>
        <a href="https://cdn.example/config.png">配置图片</a>
        <a href="/other-page.html">配置讨论</a>
        <a href="https://cdn.example/movie.mp4">直播视频</a>
        <main hidden><a href="/hidden.json">隐藏配置</a></main>
        <div role="navigation"><a href="/side.json">侧栏配置</a></div>
        <div aria-hidden="true"><a href="/aria.json">隐藏配置</a></div>
        <div style="display:none"><a href="/style-hidden.json">隐藏配置</a></div>
        <a href="https://cdn.example/private.json?api_%6bey=secret">私密</a>
        <a href="https://cdn.example/private2.json?accessToken=secret">私密</a>
        <a href="https://cdn.example/source.json?version=2">普通配置</a></main>
        <script>var x='https://cdn.example/script.json'</script>
        <style>.a{background:url(https://cdn.example/style.json)}</style>'''
        rows = extract_generic_page_links(html, 'https://www.example/page.html')
        self.assertEqual({r['url'] for r in rows}, {
            'https://www.example/tv/pack.json', 'https://cdn.example/live.m3u',
            'https://cdn.example/source.json?version=2'})
        self.assertTrue(all(r['sources'] == ['https://www.example/page.html'] for r in rows))

    def test_generic_page_keeps_adult_category_and_enforces_30_links(self):
        url = 'https://www.example/links.html'
        html = '<main>' + ''.join(f'<a href="/{i}.json">配置{i}</a>' for i in range(35)) + '</main>'
        class Network:
            def fetch_bytes(self, target, **kwargs):
                self.kwargs = kwargs
                return html.encode(), target
        net = Network()
        rows, issues = discover_source_pages(net, {'source_pages': [
            {'url': url, 'parser': 'links', 'category': 'adult', 'max_links': 80}]})
        self.assertEqual(issues, [])
        self.assertEqual(len(rows), 30)
        self.assertTrue(all(r['category'] == 'adult' for r in rows))
        self.assertEqual(net.kwargs['limit'], 500_000)
        self.assertEqual(net.kwargs['timeout'], 10)

    def test_candidate_selection_preserves_adult_page_classification(self):
        found = [{'name':'成人直播','url':'https://example.com/adult.m3u',
                  'sources':['https://example.com/links.html'],'category':'adult'}]
        selected = collector.choose_candidates([], [], found, 1)
        self.assertEqual(selected[0]['category'], 'adult')

    def test_generic_page_scan_is_capped_at_30_pages(self):
        class Network:
            def __init__(self): self.calls = 0
            def fetch_bytes(self, target, **kwargs):
                self.calls += 1
                return b'<main><a href="/source.json">\xe9\x85\x8d\xe7\xbd\xae</a></main>', target
        net = Network()
        pages = [{'url':f'https://example.com/{i}.html','parser':'links'} for i in range(35)]
        discover_source_pages(net, {'source_pages':pages})
        self.assertEqual(net.calls, 30)

    def test_redirected_page_resolves_relative_links_at_final_url_and_keeps_original_source(self):
        class Network:
            def fetch_bytes(self, target, **kwargs):
                return b'<main><a href="live.m3u">Live</a></main>', 'https://example.com/list/'
        rows, issues = discover_source_pages(Network(), {'source_pages': [
            {'url':'https://example.com/list','parser':'links'}]})
        self.assertEqual(issues, [])
        self.assertEqual(rows[0]['url'], 'https://example.com/list/live.m3u')
        self.assertEqual(rows[0]['sources'], ['https://example.com/list'])

    def test_first_check_failure_of_pinned_source_remains_visible(self):
        class Network:
            def __init__(self, *args, **kwargs): pass
            def fetch_bytes(self, url, **kwargs): raise OSError('暂时无法访问')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'sources.config.json').write_text(json.dumps({
                'seeds':[{'name':'我的直播','url':'https://example.com/live.m3u',
                          'category':'adult','sources':[]}],
                'max_candidates':1,'request_budget':1}))
            with patch.object(collector, 'Network', Network), patch('builtins.print'):
                result = collector.collect(root, discover=False, github_only=True)
            self.assertEqual(len(result['entries']), 1)
            self.assertEqual(result['entries'][0]['status'], 'error')
            self.assertEqual(result['entries'][0]['category'], 'adult')
            self.assertEqual(result['entries'][0]['kind'], 'unknown')

    def test_collection_children_receive_reserved_checks(self):
        urls={
            'https://example.com/parent.json':{'urls':[{'name':'下级单仓','url':'child.json'}]},
            'https://example.com/other.json':{'sites':[{'name':'普通','api':'https://api.example/vod'}]},
            'https://example.com/third.json':{'sites':[{'name':'其他','api':'https://api.example/other'}]},
            'https://example.com/child.json':{'sites':[{'name':'子配置','api':'https://api.example/child'}]},
        }
        class Network:
            def __init__(self,*args,**kwargs):pass
            def fetch_bytes(self,url,**kwargs):return json.dumps(urls[url]).encode(),url
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'sources.config.json').write_text(json.dumps({
                'seeds':[{'name':name,'url':url,'sources':[]} for name,url in
                         [('合集','https://example.com/parent.json'),('其他','https://example.com/other.json'),
                          ('第三','https://example.com/third.json')]],
                'max_candidates':3,'child_slots':1,'request_budget':10}))
            with patch.object(collector,'Network',Network), patch('builtins.print'):
                result=collector.collect(root,discover=False,github_only=True)
            self.assertIn('https://example.com/child.json',
                          {r['url'] for r in result['entries'] if r['status']=='ok'})

    def test_unused_child_slots_return_to_primary_candidates(self):
        class Network:
            def __init__(self,*args,**kwargs):pass
            def fetch_bytes(self,url,**kwargs):
                return json.dumps({'sites':[{'name':'普通','api':'https://api.example/vod'}]}).encode(),url
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            seeds=[{'name':str(i),'url':f'https://example.com/{i}.json','sources':[]} for i in range(3)]
            (root/'sources.config.json').write_text(json.dumps({'seeds':seeds,'max_candidates':3,
                                                                 'child_slots':1,'request_budget':10}))
            with patch.object(collector,'Network',Network), patch('builtins.print'):
                result=collector.collect(root,discover=False,github_only=True)
            self.assertEqual(len([r for r in result['entries'] if r['status']=='ok']),3)

    def test_discuz_post_extracts_non_github_sources_without_page_chrome(self):
        html='''<a href="https://outside.example/nav.json">导航</a>
        <td class="t_f" id="postmessage_1">★饭太硬<br>http://www.饭太硬.net/tv<br>
        ★外部单仓<br>https://store.example/one.json<br>
        ★无效<br>http://127.0.0.1/private<br>javascript:alert(1)</td>'''
        rows=extract_source_page_links(html,'https://bbs.example/thread.html')
        self.assertEqual([r['name'] for r in rows],['饭太硬','外部单仓'])
        self.assertEqual(rows[0]['url'],'http://www.xn--sss604efuw.net/tv')
        self.assertEqual(rows[1]['url'],'https://store.example/one.json')
        self.assertEqual(rows[1]['sources'],['https://bbs.example/thread.html'])

    def test_source_page_uses_declared_gbk_and_keeps_other_pages_after_failure(self):
        url='https://bbs.example/thread.html'
        html='<meta charset="gbk"><td id="postmessage_1">★外部配置<br>https://store.example/a.json</td>'
        class Network:
            def fetch_bytes(self,target,**kwargs):
                if target==url:return html.encode('gbk'),target
                raise OSError('页面暂时不可达')
        found,issues=discover_source_pages(Network(),{'source_pages':[
            {'url':'https://bad.example/forum'},{'url':url}]})
        self.assertEqual([r['url'] for r in found],['https://store.example/a.json'])
        self.assertEqual(len(issues),1)
        self.assertEqual(found[0]['name'],'外部配置')

    def test_search_can_fill_a_larger_repository_budget(self):
        class API:
            def api(self, path):
                if path.startswith('/search/repositories?'):
                    query = parse_qs(urlsplit(path).query)
                    kind = query['q'][0]
                    size = min(int(query['per_page'][0]), 8)
                    return {'items':[{'full_name':f'{kind}/repo{i}', 'private':False,
                                      'archived':False, 'default_branch':'main'} for i in range(size)]}
                if '/git/trees/' in path:
                    return {'tree':[{'path':'config.json', 'type':'blob', 'size':100}]}
                raise AssertionError(path)
        rows, errors, count = collector.discover_candidates(API(), {
            'repositories':[], 'queries':['vod','live'], 'max_repositories':12,
            'files_per_repository':1})
        self.assertEqual((count, len(rows), errors), (12, 12, []))

    def test_search_keeps_both_vod_and_live_repositories_when_slots_are_scarce(self):
        class API:
            def api(self, path):
                if path.startswith('/search/repositories?'):
                    query = parse_qs(urlsplit(path).query)
                    kind = query['q'][0]
                    return {'items':[{'full_name':f'{kind}/repo{i}', 'private':False,
                                      'archived':False, 'default_branch':'main'} for i in range(8)]}
                if '/git/trees/' in path:
                    return {'tree':[{'path':'config.json', 'type':'blob', 'size':100}]}
                raise AssertionError(path)
        rows, errors, count = collector.discover_candidates(API(), {
            'repositories':[], 'queries':['vod','live'], 'max_repositories':4,
            'files_per_repository':1})
        self.assertEqual((count, errors), (4, []))
        self.assertEqual({row['name'].split(' · ')[0] for row in rows}, {'vod','live'})

    def test_redirects_consume_the_same_request_budget(self):
        net = collector.Network(budget=1)
        handler = collector.SafeRedirect(lambda u:u, on_redirect=net.take_request)
        req = Request('https://example.com/a')
        handler.redirect_request(req, None, 302, '', {}, 'https://example.com/b')
        with self.assertRaises(ValueError):
            handler.redirect_request(req, None, 302, '', {}, 'https://example.com/c')

    def test_header_comments_are_valid_jsonc(self):
        for prefix in ['// heading\n', '/* heading */\n']:
            result = classify(prefix + '{"urls":[{"url":"https://a.example/a"}]}', 'https://a.example/a')
            self.assertIsNotNone(result)
            self.assertEqual(result['kind'], 'collection')

    def test_m3u8_files_are_discovered_without_readme_links(self):
        class API:
            def api(self, path):
                if '/git/trees/' in path:
                    return {'tree':[{'path':'live.m3u8','type':'blob','size':60}]}
                return {'default_branch':'main'}
        rows, errors, count = collector.discover_candidates(API(), {'repositories':['test/repo'], 'queries':[], 'files_per_repository':4})
        self.assertEqual([r['url'] for r in rows], ['https://raw.githubusercontent.com/test/repo/main/live.m3u8'])

    def test_oldest_records_get_slots_when_new_discovery_is_full(self):
        self.assertTrue(hasattr(collector,'choose_candidates'))
        old = [{'name':'old'+str(i),'url':'https://a.example/'+str(i),'checked_at':'2026-09-0'+str(i+1)} for i in range(4)]
        fresh = [{'name':'new'+str(i),'url':'https://a.example/new'+str(i)} for i in range(10)]
        selected = collector.choose_candidates([], old, fresh, 3)
        self.assertEqual([r['name'] for r in selected], ['old0','old1','new0'])

    def test_candidate_selection_keeps_all_discovery_sources(self):
        old = [{'name':'one', 'url':'https://a.example/x', 'sources':['https://github.com/a/b']}]
        found = [{'name':'one', 'url':'https://a.example/x', 'sources':['https://github.com/c/d']}]
        for previous, fresh in [(old,found),([],old+found)]:
            selected = collector.choose_candidates([],previous,fresh,3)
            self.assertEqual(selected[0]['sources'], ['https://github.com/a/b','https://github.com/c/d'])

    def test_public_connection_pins_the_checked_address(self):
        self.assertTrue(hasattr(collector,'open_public_socket'))
        public = [(2,1,6,'',('93.184.216.34',80))]
        private = [(2,1,6,'',('127.0.0.1',80))]
        sock = MagicMock()
        with patch.object(collector.socket,'getaddrinfo',side_effect=[public,private]) as dns, patch.object(collector.socket,'socket',return_value=sock):
            self.assertIs(collector.open_public_socket('example.com',80,5),sock)
            self.assertEqual(dns.call_count,1)
            sock.connect.assert_called_once_with(('93.184.216.34',80))

    def test_json_comments_do_not_break_urls_or_string_punctuation(self):
        text = '{// comment\n"storeHouse":[{"sourceName":"a,}","sourceUrl":"https://a.example/x//y"},],}'
        self.assertEqual(classify(text, 'https://a.example/a')['kind'], 'multi')
        self.assertEqual(extract_links(text, 'https://a.example/a')[0]['url'], 'https://a.example/x//y')

    def test_host_div_footer_after_json_does_not_hide_collection(self):
        text = ('{"urls":[{"name":"甲","url":"https://a.example/a.json"}]}'
                '<div style="text-align:center"><div style="position:relative">\n'
                '</div></div>')
        self.assertEqual(classify(text, 'https://a.example/dc.txt')['kind'], 'collection')
        self.assertEqual(extract_links(text, 'https://a.example/dc.txt'),
                         [{'name':'甲','url':'https://a.example/a.json'}])
        with self.assertRaises(ValueError):
            collector.parse_jsonc('{"urls":[]} {"sites":[]}')

    def test_types_are_not_guessed_from_extension(self):
        cases = [('oops', None), ('<html>error</html>', None), ('{"sites":[]}', None),
                 ('{"urls":[{"name":"one","url":"https://a.example/a"}]}', 'collection'),
                 ('{"sites":[{"key":"a","api":"csp_Test"}]}', 'config'),
                 ('#EXTM3U\n#EXTINF:-1,频道\nhttps://a.example/stream', 'live'),
                 ('#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\nseg.ts', 'stream'),
                 ('央视,#genre#\nCCTV-1,https://a.example/live', 'live')]
        for text, kind in cases:
            with self.subTest(text=text):
                out = classify(text, 'https://a.example/file.json')
                self.assertEqual(out['kind'] if out else None, kind)

    def test_child_configs_resolve_relative_urls_not_plugins(self):
        text = '{"urls":[{"name":"甲","url":"./a.json"}],"spider":"https://a.example/evil.jar"}'
        self.assertEqual(extract_links(text, 'https://a.example/folder/b.json'),
                         [{'name':'甲','url':'https://a.example/folder/a.json'}])

    def test_readme_extracts_links_and_ignores_images(self):
        out = extract_links('[仓](https://github.com/o/r/blob/main/a.json)\nhttps://a.example/live.m3u\nhttps://a.example/p.png', 'https://github.com/o/r')
        self.assertEqual({x['url'] for x in out}, {'https://raw.githubusercontent.com/o/r/main/a.json', 'https://a.example/live.m3u'})

    def test_url_normalization_and_bad_schemes(self):
        self.assertEqual(normalize_url('https://github.com/o/r/blob/main/a.json#x'), 'https://raw.githubusercontent.com/o/r/main/a.json')
        for url in ['javascript:alert(1)', 'file:///tmp/x', 'https://user:pass@a.example/x', 'http://127.0.0.1/a', 'http://[::1]/a', 'https://localhost/x']:
            with self.subTest(url=url): self.assertIsNone(normalize_url(url))

    def test_dns_private_addresses_are_rejected(self):
        def resolver(*args, **kwargs): return [(2, 1, 6, '', ('10.0.0.1', 443))]
        with self.assertRaises(ValueError): check_public_url('https://a.example/x', resolver=resolver)

    def test_only_reviewed_vod_media_host_can_use_its_extra_port(self):
        public = [(2, 1, 6, '', ('93.184.216.34', 65))]
        private = [(2, 1, 6, '', ('10.0.0.1', 65))]
        url = 'https://p.hhwenjian.com:65/video.ts'
        with patch.object(collector.socket, 'getaddrinfo', return_value=public):
            with self.assertRaises(ValueError): collector.Network().validate(url)
            try:
                route_net = collector.Network(extra_ports={'p.hhwenjian.com': {65}})
            except TypeError:
                self.fail('点播检查器尚不能限定主机开放媒体端口')
            self.assertEqual(route_net.validate(url), url)
            with self.assertRaises(ValueError): route_net.validate('https://other.example:65/video.ts')
        with patch.object(collector.socket, 'getaddrinfo', return_value=private):
            with self.assertRaises(ValueError): route_net.validate(url)

    def test_failure_keeps_previous_kind_and_last_success(self):
        old = [{'id':'a', 'url':'https://a.example/x', 'kind':'multi', 'count':3, 'last_ok':'yesterday', 'sources':['https://github.com/a/b'], 'status':'ok'}]
        new = [{'id':'a', 'url':'https://a.example/x', 'status':'error', 'checked_at':'today', 'sources':['https://github.com/c/d']}]
        row = merge_records(old, new)[0]
        self.assertEqual((row['kind'],row['last_ok'],row['status']), ('multi','yesterday','error'))
        self.assertEqual(len(row['sources']), 2)

    def test_duplicates_merge_sources(self):
        rows = [{'url':'https://a.example/x','status':'ok','sources':[s]} for s in ['https://github.com/a/b','https://github.com/c/d']]
        self.assertEqual(len(merge_records([], rows)), 1)
        self.assertEqual(len(merge_records([], rows)[0]['sources']), 2)


if __name__ == '__main__': unittest.main()
