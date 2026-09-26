import unittest
from unittest.mock import patch, MagicMock
import scripts.collector as collector
from urllib.request import Request
from urllib.parse import parse_qs, urlsplit
from scripts.collector import classify, extract_links, normalize_url, merge_records, check_public_url


class ParsingTests(unittest.TestCase):
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
