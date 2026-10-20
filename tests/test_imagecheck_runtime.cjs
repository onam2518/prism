const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const window = {PRISM_APP_PARTS: []};
const readers = [];
class FileReader { readAsDataURL(file) { readers.push({reader:this, file}); } }
vm.runInNewContext(fs.readFileSync('prism/vendor/app-24-imagecheck.js', 'utf8'), {window, Blob, URL, setTimeout, FileReader});
(async () => {
  const app = window.PRISM_APP_PARTS[0]();
  const requests = [];
  const defaults = {ok:true, prompt:'기본 지침', models:[{label:'Timely', on:true, items:[{provider:'timely',model:'vision-test'}]}]};
  app._authHeaders = () => ({Authorization:'Bearer test'});
  app._afetch = async (url, init) => {
    requests.push({url, init});
    return {ok:true,json:async () => JSON.parse(init.body).action === 'defaults' ? defaults : {ok:true,status:'complete',observations:[],image:{sha256:'hash'}}};
  };
  await app.icInit(); assert.equal(app.icModel, 'timely|vision-test');
  assert.equal(app.icPrompt, '기본 지침');
  await app.icTry(); assert.equal(requests.length, 1);
  const file = name => ({target:{files:[{name,type:'image/png',size:123}],value:'x'}});
  const first = app.icFile(file('first.png')); const second = app.icFile(file('second.png'));
  readers[1].reader.result = 'data:image/png;base64,SECOND'; readers[1].reader.onload(); await second;
  readers[0].reader.result = 'data:image/png;base64,FIRST'; readers[0].reader.onload(); await first;
  assert.equal(app.icName,'second.png'); assert.equal(app.icImage,'data:image/png;base64,SECOND'); assert.equal(app.icReading,false);
  app.icPrompt = '사용자 지침'; await app.icTry();
  assert.equal(requests.length,2); assert.equal(requests[1].url,'/lab-imagecheck-try');
  assert.equal(requests[1].init.headers.Authorization,'Bearer test');
  assert.deepEqual(JSON.parse(requests[1].init.body),{image:'SECOND',mime:'image/png',model:'timely|vision-test',prompt:'사용자 지침'});
  app.icBusy = true; await app.icTry(); assert.equal(requests.length,2); app.icBusy = false;
  app.icResetPrompt(); assert.equal(app.icPrompt,'기본 지침'); assert.equal(app.icResult,null);
  app.icResult = {ok:true}; app._afetch = async () => ({ok:false,json:async () => ({error:'권한 없음'})});
  await app.icTry(); assert.equal(app.icResult,null); assert.equal(app.icError,'권한 없음'); assert.equal(app.icBusy,false);
  app.icClear(); assert.equal(app.icImage,''); assert.equal(app.icResult,null);
  await app.icFile({target:{files:[{type:'image/svg+xml',size:10}],value:'x'}}); assert.match(app.icError,/PNG/);
  app.icDefaults = null; app._afetch = async () => {throw new Error('연결 실패');};
  await app.icInit(); assert.equal(app.icError,'연결 실패'); assert.equal(app.icLoading,false);
  console.log('image validation frontend runtime: passed');
})().catch(e => {console.error(e);process.exitCode=1;});
