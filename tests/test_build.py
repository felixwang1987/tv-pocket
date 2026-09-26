import unittest, json, re, os, shutil, subprocess
from pathlib import Path
from scripts.build import embed


class BuildTests(unittest.TestCase):
    def test_source_form_prefills_github_issue_without_sending_credentials(self):
        html = Path('index.html').read_text()
        match = re.search(r'<script id="source-form-logic">(.*?)</script>', html, re.S)
        self.assertIsNotNone(match)
        node = os.environ.get('NODE') or shutil.which('node')
        if not node:
            self.skipTest('Node.js is not available for browser-script test')
        harness = r'''
const vm=require('node:vm');
const source=JSON.parse(process.argv[1]);
const fields={source_name:{value:'测试台'},source_url:{value:'https://example.com/feeds.m3u'},
source_type:{value:'direct'},source_category:{value:'adult'}};
const els={'source-form':{elements:{namedItem:key=>fields[key]}},
'source-error':{textContent:''},'source-dialog':{showModal(){this.opened=true}},
'add-source':{}};
let navigated='';
const context={document:{getElementById:id=>els[id]},URL,
location:{assign:value=>{navigated=value}}};
vm.runInNewContext(source,context);
els['add-source'].onclick();
els['source-form'].onsubmit({preventDefault(){}});
const u=new URL(navigated);
const result={opened:els['source-dialog'].opened,origin:u.origin,path:u.pathname,
params:Object.fromEntries(u.searchParams),error:els['source-error'].textContent};
fields.source_url.value='https://example.com/a.json?ToKeN=member-secret';
navigated='';els['source-form'].onsubmit({preventDefault(){}});
result.rejectedSecret=!navigated&&!!els['source-error'].textContent;
result.rejectedAliases=['apiKey','accessToken','authToken','sessionid'].every(key=>{
 fields.source_url.value='https://example.com/a.json?'+key+'=secret';
 navigated='';els['source-form'].onsubmit({preventDefault(){}});
 return !navigated&&!!els['source-error'].textContent;
});
process.stdout.write(JSON.stringify(result));
'''
        run = subprocess.run([node, '-e', harness, json.dumps(match.group(1))],
                             text=True, capture_output=True, check=True)
        result = json.loads(run.stdout)
        self.assertEqual(result['origin'] + result['path'],
                         'https://github.com/felixwang1987/tv-pocket/issues/new')
        self.assertEqual(result['params'], {
            'template':'source.yml','source_name':'测试台',
            'source_url':'https://example.com/feeds.m3u',
            'source_type':'直链','source_category':'成人'})
        self.assertTrue(result['opened'])
        self.assertEqual(result['error'], '')
        self.assertTrue(result['rejectedSecret'])
        self.assertTrue(result['rejectedAliases'])

    def test_embedded_data_cannot_close_script_or_insert_markup(self):
        template = '<script id="catalog-data" type="application/json">{}</script><p>keep</p>'
        data = {'entries':[{'name':'</script><img src=x onerror=alert(1)>'}]}
        html = embed(template, data)
        self.assertEqual(html.count('</script>'), 1)
        payload = re.search(r'application/json">(.*?)</script>', html, re.S).group(1)
        self.assertEqual(json.loads(payload), data)
        self.assertTrue(html.endswith('<p>keep</p>'))

    def test_missing_marker_is_error_not_silent_stale_output(self):
        with self.assertRaises(ValueError): embed('<p>hello</p>', {'entries':[]})
