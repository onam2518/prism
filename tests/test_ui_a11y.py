"""Shared keyboard behavior without network, browser packages or production state."""
import http.server
import os
import pathlib
import shutil
import socketserver
import subprocess
import tempfile
import threading
import unittest


class KeyboardContract(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is required for browser logic checks')
    def test_modal_lifecycle_and_tabs(self):
        source = pathlib.Path(__file__).resolve().parents[1] / 'prism/vendor/app-19-ui-a11y.js'
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
let observers = [], keydown, dialogs = [], tablists = [];
const document = global.document = {body:{}, activeElement:null,
  querySelectorAll: s => s.startsWith('[aria-modal') ? dialogs : s === '[role="tablist"]' ? tablists : [],
  addEventListener: (name, fn) => {if(name==='keydown') keydown=fn;}};
global.getComputedStyle = el => ({visibility:'visible',zIndex:el.z || '0'});
global.requestAnimationFrame = fn => fn();
global.MutationObserver = class {constructor(fn){observers.push(fn)} observe(){}};
const mutations = () => observers.forEach(fn => fn());
function el(props={}) {return Object.assign({isConnected:true,hidden:false,disabled:false,tabIndex:0,children:[],
  getClientRects(){return this.hidden?[]:[1]},
  closest(s){if(s==='[role="tab"]')return this.tab?this:null;if(s==='[role="tablist"]')return this.list||null;return null},
  getAttribute(s){return this[s] ?? null},
  querySelectorAll(s){return this.children},
  querySelector(s){return s==='[data-dialog-close]' ? this.close : s==='[data-dialog-initial-focus]' ? this.initial : null},
  contains(e){return e===this || this.children.includes(e)},
  focus(){document.activeElement=this},click(){this.clicked=(this.clicked||0)+1;if(this.action)this.action()}
},props)}
const launcher=el(), first=el(), last=el();launcher.focus();
const dialog=el({children:[first,last],close:last});last.action=()=>{dialog.hidden=true};
require(process.argv[1]);
dialogs=[dialog];mutations();assert.equal(document.activeElement,first,'initial focus');
function key(k,shift=false){const e={key:k,shiftKey:shift,target:document.activeElement,preventDefault(){this.prevented=true},stopImmediatePropagation(){}};keydown(e);return e}
last.focus();assert.ok(key('Tab').prevented);assert.equal(document.activeElement,first,'forward wrap');
key('Tab',true);assert.equal(document.activeElement,last,'backward wrap');
const cancel=el(),confirm=el({z:80,children:[cancel],initial:cancel,close:cancel});cancel.action=()=>{confirm.hidden=true};
first.focus();dialogs.push(confirm);mutations();assert.equal(document.activeElement,cancel);
key('Escape');mutations();assert.equal(document.activeElement,first,'nested restore');
key('Escape');mutations();assert.equal(document.activeElement,launcher,'launcher restore');
// Alpine hide transitions retain layout temporarily but must release focus immediately.
dialog.hidden=false;dialog._x_isShown=false;mutations();assert.equal(document.activeElement,launcher);
dialog._x_isShown=true;mutations();assert.equal(document.activeElement,first);
dialog._x_isShown=false;mutations();assert.equal(document.activeElement,launcher);
const a=el({tab:true,'aria-selected':'true'}), disabled=el({tab:true,disabled:true,'aria-selected':'false'}), b=el({tab:true,'aria-selected':'false'});
const list=el({children:[a,disabled,b]});for(const t of list.children)t.list=list;
tablists=[list]; mutations();
assert.deepEqual([a.tabIndex,disabled.tabIndex,b.tabIndex],[ 0, 0, -1 ],'only the selected enabled tab is a tab stop');
a['aria-selected']='false'; b['aria-selected']='true'; mutations();
assert.deepEqual([a.tabIndex,disabled.tabIndex,b.tabIndex],[-1,0,0],'selected mutation is normalized without overwriting disabled tab');
const hidden=el({tab:true,hidden:true,tabIndex:7}); hidden.list=list; list.children.push(hidden); mutations();
assert.equal(hidden.tabIndex,7,'invisible tab keeps its component-owned tabindex');
const lateA=el({tab:true,'aria-selected':'false'}), lateB=el({tab:true,'aria-selected':'true'});
const lateList=el({children:[lateA,lateB]}); for(const t of lateList.children)t.list=lateList;
tablists.push(lateList); mutations();
assert.deepEqual([lateA.tabIndex,lateB.tabIndex],[-1,0],'new Alpine-inserted tablist is normalized');
a.focus();key('ArrowRight');assert.equal(document.activeElement,b);assert.equal(b.clicked,1);
key('Home');assert.equal(document.activeElement,a);key('End');assert.equal(document.activeElement,b);
key('ArrowRight');assert.equal(document.activeElement,a,'tab wrap skips disabled');
list['aria-orientation']='vertical';key('ArrowDown');assert.equal(document.activeElement,b);
// A nonmodal dialog never enters the modal query; no focus interception.
dialogs=[];launcher.focus();mutations();assert.equal(key('Tab').prevented,undefined);
console.log('modal open/trap/nested Escape/restore; roving selected tab stops including dynamic tablist; horizontal/vertical tabs; nonmodal passed');
'''
        result = subprocess.run(['node', '-e', script, str(source)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is required for browser logic checks')
    def test_tabs_render_and_handle_arrow_keys_in_chromium(self):
        """A real rendered fixture catches DOM/event behavior that a Node-only harness cannot."""
        chrome = next((path for path in (
            shutil.which('google-chrome'), shutil.which('chromium'),
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        ) if path and os.path.exists(path)), None)
        if not chrome:
            self.skipTest('Chromium is required for rendered keyboard validation')
        source = pathlib.Path(__file__).resolve().parents[1] / 'prism/vendor/app-19-ui-a11y.js'
        fixture = '''<!doctype html><body>
<div role="tablist" id="tabs"><button role="tab" aria-selected="true">첫 탭</button><button role="tab" aria-selected="false">둘째 탭</button></div>
<pre id="result"></pre><script>window.requestAnimationFrame = callback => { callback(); return 0; };</script><script src="/app-19-ui-a11y.js"></script><script>
addEventListener('load', () => requestAnimationFrame(() => {
  const tabs = [...document.querySelectorAll('#tabs [role=tab]')];
  const result = document.querySelector('#result');
  const initial = tabs.map(t => t.tabIndex);
  const mark = () => result.textContent = JSON.stringify({ initial, focusedSecond: document.activeElement === tabs[1], dynamic: [...document.querySelectorAll('#late [role=tab]')].map(t => t.tabIndex) });
  tabs[1].addEventListener('click', () => { tabs[0].setAttribute('aria-selected', 'false'); tabs[1].setAttribute('aria-selected', 'true'); });
  tabs[0].focus(); tabs[0].dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowRight', bubbles:true}));
  new MutationObserver(mark).observe(document.body, {childList:true});
  const late = document.createElement('div'); late.id = 'late'; late.setAttribute('role', 'tablist'); late.innerHTML = '<button role="tab" aria-selected="false">늦은 첫 탭</button><button role="tab" aria-selected="true">늦은 둘째 탭</button>'; document.body.append(late);
}));
</script></body>'''
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / 'index.html').write_text(fixture, encoding='utf-8')
            (root / 'app-19-ui-a11y.js').write_text(source.read_text(encoding='utf-8'), encoding='utf-8')
            class FixtureHandler(http.server.SimpleHTTPRequestHandler):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, directory=directory, **kwargs)

                def log_message(self, *_args):
                    pass

            with socketserver.TCPServer(('127.0.0.1', 0), FixtureHandler) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    page = f'http://127.0.0.1:{server.server_address[1]}/index.html'
                    result = subprocess.run([chrome, '--headless=new', '--disable-gpu', '--no-first-run', '--virtual-time-budget=1000', '--dump-dom', page], capture_output=True, text=True, timeout=30)
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr[-1000:])
        rendered = result.stdout.split('<pre id="result">', 1)[1].split('</pre>', 1)[0]
        self.assertIn('"initial":[0,-1]', rendered)
        self.assertIn('"focusedSecond":true', rendered)
        self.assertIn('"dynamic":[-1,0]', rendered)

    @unittest.skipUnless(shutil.which('node'), 'Node is required for browser logic checks')
    def test_compare_polling_keeps_focus_and_rows_on_failure(self):
        source = pathlib.Path(__file__).resolve().parents[1] / 'prism/vendor/compare-window.html'
        script = r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
let poll, offline=false;
const nodes={hint:{},error:{hidden:true},retry:{hidden:true}};
const doc=global.document={activeElement:null,getElementById:id=>nodes[id]};
function button(id,key){return {dataset:{[key]:id},hasAttribute:k=>k==='data-'+key,getAttribute:k=>k==='data-'+key?id:null,focus(){doc.activeElement=this}}}
nodes.list={html:'',buttons:[],set innerHTML(h){this.html=h;this.buttons=[...h.matchAll(/data-open="(\d+)"/g)].map(m=>button(m[1],'open'))},get innerHTML(){return this.html},
querySelectorAll:s=>s==='[data-open]'?nodes.list.buttons:[],querySelector:s=>nodes.list.buttons.find(b=>s.includes('"'+b.dataset.open+'"'))};
global.window={};global.location={search:'',origin:'http://fixture'};global.localStorage={getItem:()=>''};
global.setInterval=fn=>{poll=fn};
global.fetch=async()=>{if(offline)throw Error('offline');return {json:async()=>({ok:true,jobs:[{id:17,status:'done',ts:1,finished:2,golden_n:3,models:{solar:{status:'done',done:3,total:3}}}]})}};
const html=fs.readFileSync(process.argv[1],'utf8');const scripts=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
vm.runInThisContext(scripts.at(-1)[1]);
(async()=>{await new Promise(r=>setImmediate(r));const original=nodes.list.buttons[0];original.focus();await poll();assert.notEqual(doc.activeElement,original);assert.equal(doc.activeElement.dataset.open,'17');
const rows=nodes.list.innerHTML;offline=true;await poll();assert.equal(nodes.list.innerHTML,rows);assert.equal(nodes.error.hidden,false);assert.equal(nodes.retry.hidden,false);offline=false;await nodes.retry.onclick();assert.equal(nodes.error.hidden,true);console.log('poll focus and offline/retry retention passed')})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', script, str(source)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("'data-cancel'", source.read_text(encoding='utf-8'))
        self.assertIn("'data-restart'", source.read_text(encoding='utf-8'))
