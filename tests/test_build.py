import unittest, json, re, os, shutil, subprocess
from pathlib import Path
from scripts.build import embed


class BuildTests(unittest.TestCase):
    def test_vod_summary_uses_merged_cloud_sites_and_backup_configs_do_not_enable_import(self):
        html = Path('index.html').read_text()
        source = re.search(r'<script>\s*(.*?)</script>', html, re.S).group(1)
        node = os.environ.get('NODE') or shutil.which('node')
        if not node:
            self.skipTest('Node.js is not available for browser-script test')
        harness = r'''
const vm=require('node:vm');
const source=JSON.parse(process.argv[1]),options=JSON.parse(process.argv[2]),count=options.count,now=new Date().toISOString();
const mergedAt=options.stale?new Date(Date.now()-37*3600000).toISOString():now;
class Element {
 constructor(){this.children=[];this._text='';this.style={};this.dataset={};this.value='';this.namespaceURI='svg';this.classList={remove(){}};}
 set textContent(value){this._text=String(value);this.children=[];}
 get textContent(){return this._text+this.children.map(x=>x.textContent||'').join('');}
 append(...nodes){this.children.push(...nodes);} prepend(...nodes){this.children.unshift(...nodes);}
 replaceChildren(...nodes){this.children=nodes;this._text='';} setAttribute(){} focus(){} select(){}
}
const els={},el=id=>els[id]||(els[id]=new Element());el('status').value='all';
el('catalog-data').textContent=JSON.stringify({schema_version:1,entries:[],verified_routes:options.ordinary?[{id:'b'.repeat(16),name:'普通接口',category:'ordinary',checked_at:now,status:'passed',passed:1,sampled:1,searchable:true}]:[],generated_at:now,last_success_at:now,
cloud_routes:[{id:'a'.repeat(16),name:'备用网盘原配置',url:'https://example.com/cloud.json',checked_at:now,total_sites:107,site_count:11,providers:['夸克'],login_names:['配置中心']}],
cloud_merge:{site_count:count,searchable_count:count?11:0,source_name:'潇洒多站配置',source_url:'https://example.com/cloud.json',login_names:['配置中心'],checked_at:mergedAt}});
vm.runInNewContext(source,{document:{getElementById:el,createElement:()=>new Element(),createElementNS:()=>new Element(),querySelectorAll:()=>[]},
sessionStorage:{getItem:()=>null},localStorage:{getItem:()=>null},URL,Date,Intl,AbortController,
location:{href:'https://example.com/tv-pocket/',protocol:'https:'},setTimeout:()=>1,clearTimeout(){},fetch:async()=>{throw Error('fixture offline');}});
process.stdout.write(JSON.stringify({summary:el('routes-summary').textContent,details:el('route-list').textContent,disabled:el('copy-routes').disabled}));
'''
        def inspect(count, **options):
            run=subprocess.run([node,'-e',harness,json.dumps(source),json.dumps({'count':count,**options})],
                               text=True,capture_output=True,check=True)
            return json.loads(run.stdout)
        merged=inspect(19)
        self.assertFalse(merged['disabled'])
        self.assertIn('19',merged['summary'])
        self.assertIn('11',merged['summary'])
        self.assertIn('配置中心',merged['details'])
        backup_only=inspect(0)
        self.assertTrue(backup_only['disabled'])
        self.assertIn('备用原配置',backup_only['details'])
        self.assertIn('不代表',backup_only['details'])
        stale=inspect(19,stale=True)
        self.assertTrue(stale['disabled'])
        ordinary_only=inspect(0,ordinary=True)
        self.assertFalse(ordinary_only['disabled'])
        self.assertIn('普通 1 个站点',ordinary_only['summary'])
        self.assertIn('合入 0 个网盘站点',ordinary_only['summary'])

    def test_live_copy_uses_native_password_txt_format_and_vod_copy_stays_the_same(self):
        source=re.search(r'<script>\s*(.*?)</script>',Path('index.html').read_text(),re.S).group(1)
        node=os.environ.get('NODE') or shutil.which('node')
        if not node:
            self.skipTest('Node.js is not available for browser-script test')
        harness=r"""
const vm=require('node:vm'),source=JSON.parse(process.argv[1]),now=new Date().toISOString();
class Element {
 constructor(){this.children=[];this.style={};this.dataset={};this.value='';this.namespaceURI='svg';this.classList={remove(){}};}
 append(...nodes){this.children.push(...nodes);} prepend(...nodes){this.children.unshift(...nodes);}
 replaceChildren(...nodes){this.children=nodes;} setAttribute(){} focus(){} select(){}
}
const els={},el=id=>els[id]||(els[id]=new Element());el('status').value='all';
el('catalog-data').textContent=JSON.stringify({schema_version:1,generated_at:now,last_success_at:now,
entries:[{id:'c'.repeat(16),name:'测试频道',kind:'stream',url:'https://example.com/channel.m3u8',status:'ok',checked_at:now,
playback:{status:'passed',checked_at:now,sampled:1,passed:1,total:1,samples:[{status:'passed',checked_at:now,url:'https://example.com/channel.m3u8'}]}}],
verified_routes:[],cloud_merge:{site_count:2,searchable_count:1,checked_at:now}});
const copied=[];
vm.runInNewContext(source,{document:{getElementById:el,createElement:()=>new Element(),createElementNS:()=>new Element(),querySelectorAll:()=>[]},
sessionStorage:{getItem:()=>null},localStorage:{getItem:()=>null},URL,Date,Intl,AbortController,
window:{isSecureContext:true},navigator:{clipboard:{writeText:async value=>{copied.push(value);}}},
location:{href:'https://example.com/tv-pocket/',protocol:'https:'},setTimeout:()=>1,clearTimeout(){},fetch:async()=>{throw Error('fixture offline');}});
(async()=>{
 await el('copy-verified').onclick();await el('copy-routes').onclick();
 process.stdout.write(JSON.stringify({copied,liveDisabled:el('copy-verified').disabled,vodDisabled:el('copy-routes').disabled}));
})().catch(error=>{process.stderr.write(String(error));process.exitCode=1;});
"""
        run=subprocess.run([node,'-e',harness,json.dumps(source)],text=True,capture_output=True,check=True)
        result=json.loads(run.stdout)
        self.assertFalse(result['liveDisabled'])
        self.assertFalse(result['vodDisabled'])
        self.assertEqual(result['copied'],[
            'https://example.com/tv-pocket/checked/live.txt',
            'https://example.com/tv-pocket/checked/routes.json'])

    def test_issue_template_fields_can_all_be_prefilled_from_page(self):
        template = Path('.github/ISSUE_TEMPLATE/source.yml').read_text()
        fields = re.findall(r'  - type: input\n    id: ([^\n]+)', template)
        self.assertEqual(fields, [
            'source_name', 'source_url', 'source_type', 'source_category'])

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
