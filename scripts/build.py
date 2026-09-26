"""Embed the catalog in the portable HTML and optionally prepare a Pages artifact."""
import argparse
import json
from pathlib import Path
import re
import shutil

try:
    from .playback import verified_playlist
except ImportError:
    from playback import verified_playlist

ROOT = Path(__file__).resolve().parents[1]


def embed(html, data):
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    payload = payload.replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    pattern = r'(<script id="catalog-data" type="application/json">).*?(</script>)'
    result, count = re.subn(pattern, lambda m:m[1] + payload + m[2], html, flags=re.S)
    if count != 1:
        raise ValueError('HTML 必须包含唯一 catalog-data 标记')
    return result


def build(site=False):
    html_path = ROOT / 'index.html'
    data = json.loads((ROOT / 'data/sources.json').read_text())
    playlist, _ = verified_playlist(data['entries'])
    (ROOT / 'checked').mkdir(exist_ok=True)
    (ROOT / 'checked/live.m3u').write_text(playlist)
    html_path.write_text(embed(html_path.read_text(), data))
    if site:
        out = ROOT / '_site'
        (out / 'data').mkdir(parents=True, exist_ok=True)
        shutil.copy2(html_path, out / 'index.html')
        shutil.copy2(ROOT / 'data/sources.json', out / 'data/sources.json')
        (out / 'checked').mkdir(exist_ok=True)
        shutil.copy2(ROOT / 'checked/live.m3u', out / 'checked/live.m3u')
        for asset in ('apple-touch-icon.png', 'icon-192.png', 'icon-512.png', 'icon.svg', 'site.webmanifest'):
            shutil.copy2(ROOT / asset, out / asset)
        (out / '.nojekyll').touch()
    print('已内嵌 %s 条来源%s' % (len(data['entries']), '，发布目录 _site/' if site else ''))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--site', action='store_true')
    build(parser.parse_args().site)
