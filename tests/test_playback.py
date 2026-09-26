import os
import shutil
import subprocess
import unittest
from datetime import datetime, timezone, timedelta

from scripts.playback import parse_channels, probe_stream, check_live, verified_playlist, decode_video


class FakeNetwork:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def fetch_bytes(self, url, **kwargs):
        self.calls.append(url)
        value = self.responses[url]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, str):
            value = value.encode()
        return value, url


class PlaybackTests(unittest.TestCase):
    def test_playlist_parsing_keeps_header_requirements_and_deduplicates(self):
        text = '#EXTM3U\n#EXTINF:-1,一台\na.m3u8\n#EXTINF:-1,重复\na.m3u8\n#EXTINF:-1,二台\n#EXTVLCOPT:http-referrer=https://example.com\nb.m3u8'
        channels = parse_channels(text, 'https://example.com/list.m3u')
        self.assertEqual(len(channels), 2)
        self.assertEqual(channels[0]['url'], 'https://example.com/a.m3u8')
        self.assertTrue(channels[1]['unsupported'])

    def test_explicit_adult_group_survives_verified_export(self):
        text='#EXTM3U\n#EXTINF:-1 group-title="XXX",成人频道\nhttps://example.com/a.m3u8\n#EXTINF:-1 group-title="新闻",普通频道\nhttps://example.com/b.m3u8'
        channels=parse_channels(text,'https://example.com/list.m3u')
        self.assertEqual([r['category'] for r in channels],['adult','ordinary'])
        now=datetime.now(timezone.utc).isoformat()
        records=[{'kind':'live','status':'ok','playback':{'samples':[
            {**channel,'status':'passed','checked_at':now} for channel in channels]}}]
        playlist,count=verified_playlist(records)
        self.assertEqual(count,2)
        self.assertIn('group-title="成人直播",成人频道',playlist)
        self.assertIn('group-title="普通直播",普通频道',playlist)

    def test_http_200_html_is_not_a_playable_stream(self):
        net = FakeNetwork({'https://example.com/live': '<html>login</html>'})
        result = probe_stream(net, 'https://example.com/live', decoder=lambda b: self.fail('HTML reached decoder'))
        self.assertEqual(result['status'], 'failed')
        self.assertIn('视频', result['reason'])

    def test_master_and_relative_segment_are_decoded(self):
        net = FakeNetwork({'https://example.com/live': '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nv/index.m3u8',
                           'https://example.com/v/index.m3u8': '#EXTM3U\n#EXT-X-TARGETDURATION:5\n#EXTINF:5,\n../video.ts',
                           'https://example.com/video.ts': b'video'})
        seen = []
        result = probe_stream(net, 'https://example.com/live', decoder=lambda b: seen.append(b) or True)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(seen, [b'video'])

    def test_fmp4_initialization_is_prepended(self):
        net = FakeNetwork({'https://example.com/live': '#EXTM3U\n#EXT-X-TARGETDURATION:5\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:5,\nchunk.m4s',
                           'https://example.com/init.mp4': b'init', 'https://example.com/chunk.m4s': b'chunk'})
        result = probe_stream(net, 'https://example.com/live', decoder=lambda b: b == b'initchunk')
        self.assertEqual(result['status'], 'passed')

    def test_master_skips_declared_audio_only_variant(self):
        net = FakeNetwork({'https://example.com/live': '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100,CODECS="mp4a.40.2"\naudio.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=1000,CODECS="avc1.42001e,mp4a.40.2"\nvideo.ts',
                           'https://example.com/audio.m3u8': b'audio', 'https://example.com/video.ts': b'video'})
        result = probe_stream(net, 'https://example.com/live', decoder=lambda b: b == b'video')
        self.assertEqual(result['status'], 'passed')
        self.assertNotIn('https://example.com/audio.m3u8', net.calls)

    def test_selected_segment_uses_its_own_initialization(self):
        net = FakeNetwork({'https://example.com/live': '#EXTM3U\n#EXT-X-TARGETDURATION:5\n#EXT-X-MAP:URI="a.mp4"\n#EXTINF:5,\na.m4s\n#EXT-X-MAP:URI="b.mp4"\n#EXTINF:5,\nb.m4s',
                           'https://example.com/a.mp4': b'initA', 'https://example.com/b.mp4': b'initB',
                           'https://example.com/a.m4s': b'segmentA', 'https://example.com/b.m4s': b'segmentB'})
        self.assertEqual(probe_stream(net, 'https://example.com/live', decoder=lambda b: b == b'initAsegmentA')['status'], 'passed')

    def test_public_aes128_hls_segment_is_decrypted_before_video_check(self):
        key = bytes.fromhex('00112233445566778899aabbccddeeff')
        iv = bytes.fromhex('00000000000000000000000000000007')
        clear = b'video frame sample'
        encrypted = subprocess.run(['openssl', 'enc', '-aes-128-cbc', '-K', key.hex(), '-iv', iv.hex()],
                                   input=clear, capture_output=True, check=True).stdout
        net = FakeNetwork({'https://example.com/live': '#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:7\n'
                           '#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXTINF:5,\npart.ts',
                           'https://example.com/key.bin': key,
                           'https://example.com/part.ts': encrypted})
        result = probe_stream(net, 'https://example.com/live', decoder=lambda b: b == clear)
        self.assertEqual(result['status'], 'passed')

    def test_aes128_explicit_iv_overrides_media_sequence(self):
        key = bytes.fromhex('00112233445566778899aabbccddeeff')
        iv = bytes.fromhex('00000000000000000000000000000007')
        clear = b'another video frame'
        encrypted = subprocess.run(['openssl', 'enc', '-aes-128-cbc', '-K', key.hex(), '-iv', iv.hex()],
                                   input=clear, capture_output=True, check=True).stdout
        net = FakeNetwork({'https://example.com/live': '#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:99\n'
                           '#EXT-X-KEY:METHOD=AES-128,URI="key.bin",IV=0x00000000000000000000000000000007,KEYFORMAT="identity"\n'
                           '#EXTINF:5,\npart.ts',
                           'https://example.com/key.bin': key,
                           'https://example.com/part.ts': encrypted})
        self.assertEqual(probe_stream(net, 'https://example.com/live', decoder=lambda b: b == clear)['status'], 'passed')

    def test_unsupported_encryption_or_cyclic_hls_is_never_marked_passed(self):
        encrypted = '#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="secret.key"\n#EXTINF:5,\nv.ts'
        cycle = '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\nlive'
        for text in (encrypted, cycle):
            net = FakeNetwork({'https://example.com/live': text})
            result = probe_stream(net, 'https://example.com/live', decoder=lambda b: True)
            self.assertEqual(result['status'], 'unverified')
            self.assertNotIn('https://example.com/secret.key', net.calls)

    def test_live_sampling_does_not_spend_budget_on_encrypted_segments(self):
        net = FakeNetwork({'https://example.com/live': '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n'
                           '#EXTINF:5,\npart.ts', 'https://example.com/key.bin': b'0' * 16,
                           'https://example.com/part.ts': b'not a video'})
        result = check_live(net, '', 'https://example.com/live', kind='stream', decoder=lambda b: True)
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(net.calls, ['https://example.com/live'])

    def test_one_good_channel_does_not_mark_all_samples_passed(self):
        text = '#EXTM3U\n#EXTINF:-1,好\nhttps://example.com/good\n#EXTINF:-1,坏\nhttps://example.com/bad'
        net = FakeNetwork({'https://example.com/good': b'video', 'https://example.com/bad': TimeoutError()})
        result = check_live(net, text, 'https://example.com/list', decoder=lambda b: True)
        self.assertEqual((result['status'], result['passed'], result['sampled']), ('partial', 1, 2))

    def test_private_segment_is_rejected_by_transport(self):
        net = FakeNetwork({'https://example.com/live': '#EXTM3U\n#EXT-X-TARGETDURATION:5\n#EXTINF:5,\nhttp://127.0.0.1/secret',
                           'http://127.0.0.1/secret': ValueError('拒绝内网或特殊网络地址')})
        self.assertEqual(probe_stream(net, 'https://example.com/live', decoder=lambda b: True)['status'], 'unverified')

    def test_export_excludes_stale_failed_and_unchecked_streams(self):
        fresh = datetime.now(timezone.utc).isoformat()
        yesterday = (datetime.now(timezone.utc) - timedelta(hours=26)).isoformat()
        stale = (datetime.now(timezone.utc) - timedelta(hours=37)).isoformat()
        def row(url, stamp=fresh, source='ok', status='passed'):
            return {'kind':'live', 'status':source, 'playback':{'checked_at':stamp, 'samples':[
                {'name':'频道\n#INJECT', 'url':url, 'status':status, 'checked_at':stamp}]}}
        rows = [row('https://example.com/ok'), row('https://example.com/ok'),
                row('https://example.com/yesterday', yesterday), row('https://example.com/old', stale),
                row('https://example.com/bad', source='error'), row('https://example.com/unknown', status='unverified')]
        content, count = verified_playlist(rows)
        self.assertEqual(count, 2)
        self.assertEqual(content.count('https://example.com/ok'), 1)
        self.assertIn('https://example.com/yesterday', content)
        self.assertNotIn('\n#INJECT', content)
        self.assertNotIn('/old', content)

    def test_real_decoder_rejects_garbage_and_decodes_generated_video(self):
        executable = os.environ.get('FFMPEG') or shutil.which('ffmpeg')
        if not executable:
            if os.environ.get('CI'):
                self.fail('CI must install ffmpeg')
            self.skipTest('ffmpeg not installed')
        fixture = subprocess.run([executable, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=10',
                                  '-t', '1', '-c:v', 'mpeg2video', '-f', 'mpegts', 'pipe:1'],
                                 capture_output=True, check=True, timeout=15).stdout
        self.assertTrue(decode_video(fixture))
        self.assertFalse(decode_video(b'not video'))


if __name__ == '__main__':
    unittest.main()
