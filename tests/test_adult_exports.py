import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import build as site_builder


class AdultExportTests(unittest.TestCase):
    def test_normal_imports_exclude_adult_content_and_unmerged_cloud_configs(self):
        checked_at = datetime.now(timezone.utc).isoformat()
        def sample(name, url, category='ordinary'):
            return {'name':name, 'url':url, 'category':category,
                    'status':'passed', 'checked_at':checked_at}
        normal_url = 'https://example.com/news.m3u8'
        adult_url = 'https://example.com/adult.m3u8'
        duplicate_url = 'https://example.com/conflict.m3u8'
        entries = [
            {'kind':'live', 'status':'ok', 'category':'ordinary', 'playback':{'samples':[
                sample('#新闻,测试',normal_url), sample('重复',duplicate_url)]}},
            {'kind':'live', 'status':'ok', 'category':'adult', 'playback':{'samples':[
                sample('成人频道',adult_url), sample('成人重复',duplicate_url)]}},
            {'id':'a'*16,'kind':'config','status':'ok','role':'cloud','checked_at':checked_at,
             'name':'云盘配置','count':5,'url':'https://example.com/cloud/api.json',
             'cloud':{'site_count':2,'providers':['夸克'],'login_names':['配置中心']}}
        ]
        routes = [
            {'id':'1'*16,'name':'普通站','api':'https://360zyzz.com/api.php',
             'category':'ordinary','status':'passed','checked_at':checked_at,'passed':1,'sampled':1},
            {'id':'2'*16,'name':'成人站','api':'https://apiyutu.com/api.php',
             'category':'adult','status':'passed','checked_at':checked_at,'passed':1,'sampled':1}
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'data').mkdir()
            (root/'index.html').write_text('<script id="catalog-data" type="application/json">{}</script>')
            (root/'data/sources.json').write_text(json.dumps({'schema_version':1,'entries':entries}))
            (root/'data/routes.json').write_text(json.dumps({'routes':routes}))
            (root/'sources.config.json').write_text(json.dumps({
                'routes':{'base_url':'https://example.com/pocket/'},'adult_live_pin':'2468'}))
            for asset in ('apple-touch-icon.png','icon-192.png','icon-512.png','icon.svg','site.webmanifest'):
                (root/asset).write_text('fixture')
            with patch.object(site_builder, 'ROOT', root):
                site_builder.build(site=True)
            self.assertTrue((root/'checked/routes-ordinary.json').is_file(), 'ordinary VOD import missing')
            self.assertTrue((root/'checked/live-ordinary.m3u').is_file(), 'ordinary live import missing')
            ordinary = json.loads((root/'checked/routes-ordinary.json').read_text())['urls']
            self.assertEqual([r['url'] for r in ordinary], [
                'https://example.com/pocket/checked/vod-all.json'])
            full = json.loads((root/'checked/routes.json').read_text())['urls']
            self.assertEqual(len(full), 2)
            self.assertIn('https://example.com/pocket/checked/vod-adult.json', [r['url'] for r in full])
            normal_live = (root/'checked/live-ordinary.m3u').read_text()
            self.assertIn(normal_url, normal_live)
            self.assertNotIn(adult_url, normal_live)
            self.assertNotIn(duplicate_url, normal_live)
            self.assertNotIn('成人直播', normal_live)
            full_live = (root/'checked/live.m3u').read_text()
            self.assertIn(adult_url, full_live)
            self.assertIn(duplicate_url, full_live)
            native_live=(root/'checked/live.txt').read_text()
            self.assertIn('普通直播,#genre#',native_live)
            self.assertIn('成人直播_2468,#genre#',native_live)
            self.assertIn('＃新闻，测试,'+normal_url,native_live)
            self.assertEqual(native_live.count(normal_url),1)
            self.assertEqual(native_live.count(adult_url),1)
            self.assertEqual(native_live.count(duplicate_url),1)
            self.assertNotIn('2468',full_live)
            for relative in ['checked/routes-ordinary.json','checked/live-ordinary.m3u','checked/live.txt']:
                self.assertEqual((root/relative).read_bytes(), (root/'_site'/relative).read_bytes())


if __name__ == '__main__':
    unittest.main()
