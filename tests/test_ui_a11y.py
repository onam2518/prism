"""Shared keyboard behavior without network, browser packages or production state."""
import pathlib
import shutil
import subprocess
import unittest


class KeyboardContract(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is required for browser logic checks')
    def test_modal_lifecycle_and_tabs(self):
        source = pathlib.Path(__file__).resolve().parents[1] / 'prism/vendor/app-19-ui-a11y.js'
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
let observer, keydown, dialogs = [];
const document = global.document = {body:{}, activeElement:null,
  querySelectorAll: s => s.startsWith('[aria-modal') ? dialogs : [],
  addEventListener: (name, fn) => {if(name==='keydown') keydown=fn;}};
global.getComputedStyle = el => ({visibility:'visible',zIndex:el.z || '0'});
global.requestAnimationFrame = fn => fn();
global.MutationObserver = class {constructor(fn){observer=fn} observe(){}};
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
dialogs=[dialog];observer();assert.equal(document.activeElement,first,'initial focus');
function key(k,shift=false){const e={key:k,shiftKey:shift,target:document.activeElement,preventDefault(){this.prevented=true},stopImmediatePropagation(){}};keydown(e);return e}
last.focus();assert.ok(key('Tab').prevented);assert.equal(document.activeElement,first,'forward wrap');
key('Tab',true);assert.equal(document.activeElement,last,'backward wrap');
const cancel=el(),confirm=el({z:80,children:[cancel],initial:cancel,close:cancel});cancel.action=()=>{confirm.hidden=true};
first.focus();dialogs.push(confirm);observer();assert.equal(document.activeElement,cancel);
key('Escape');observer();assert.equal(document.activeElement,first,'nested restore');
key('Escape');observer();assert.equal(document.activeElement,launcher,'launcher restore');
// Alpine hide transitions retain layout temporarily but must release focus immediately.
dialog.hidden=false;dialog._x_isShown=false;observer();assert.equal(document.activeElement,launcher);
dialog._x_isShown=true;observer();assert.equal(document.activeElement,first);
dialog._x_isShown=false;observer();assert.equal(document.activeElement,launcher);
const a=el({tab:true}), disabled=el({tab:true,disabled:true}), b=el({tab:true});
const list=el({children:[a,disabled,b]});for(const t of list.children)t.list=list;
a.focus();key('ArrowRight');assert.equal(document.activeElement,b);assert.equal(b.clicked,1);
key('Home');assert.equal(document.activeElement,a);key('End');assert.equal(document.activeElement,b);
key('ArrowRight');assert.equal(document.activeElement,a,'tab wrap skips disabled');
list['aria-orientation']='vertical';key('ArrowDown');assert.equal(document.activeElement,b);
// A nonmodal dialog never enters the modal query; no focus interception.
dialogs=[];launcher.focus();observer();assert.equal(key('Tab').prevented,undefined);
console.log('modal open/trap/nested Escape/restore; horizontal/vertical tabs; nonmodal passed');
'''
        result = subprocess.run(['node', '-e', script, str(source)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
