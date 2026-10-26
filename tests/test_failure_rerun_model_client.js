const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
function app() {
  const window={};
  for(const name of ['app-05-costdata.js','app-08-copytext.js'])
    vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../prism/vendor',name),'utf8'),{window});
  const a={};for(const part of window.PRISM_APP_PARTS)Object.defineProperties(a,Object.getOwnPropertyDescriptors(part()));
  a._authHeaders=()=>({});a._err=e=>{throw Error(e);};
  a.liveToast=a.loadDash=a.fetchIngestStatus=()=>{};a.loadFails=async()=>{};
  a.mpCurLabel=(_,m)=>m||'기본 설정 모델';a.availableModels=[];
  a.dsConfirm=async()=>true;
  a.requests=[];
  a._afetch=async(url,options)=>{a.requests.push({url,...JSON.parse(options.body)});return {json:async()=>({done:1})};};
  return a;
}
(async()=>{
  let a=app();a.failRunModel='router/chosen';
  await a.rerunFail({hash:'one',title:'단건'});
  assert.equal(a.requests[0].model,'router/chosen');
  assert.equal(a.requests[0].url,'/rerun');
  a.requests=[];await a.rerunOne({hash:'other'});
  assert.equal(a.requests[0].model,'','일반 목록의 기존 기본 모델 유지');
  a=app();a.failRunModel='router/batch';a.failSel={one:true,two:true};
  a.dsConfirm=async()=>{a.failRunModel='changed';a.failSel={other:true};return true;};
  await a.rerunFailPicked();
  assert.equal(a.requests[0].model,'router/batch');
  assert.deepEqual(a.requests[0].hashes,['one','two']);
  a=app();a.failRunModel='router/retry';a.failSel={one:true};
  a._afetch=async(url,options)=>{a.requests.push(JSON.parse(options.body));return {json:async()=>a.requests.length===1?{error:'퀘스트 진행 중'}:{done:1}};};
  a.dsConfirm=async()=>{a.failRunModel='other';a.failSel={other:true};return true;};
  await a.rerunFailPicked();
  assert.equal(a.requests.length,2);
  assert.equal(a.requests[1].force,true);
  assert.equal(a.requests[1].model,'router/retry');
  assert.deepEqual(a.requests[1].hashes,['one']);
  a=app();a.failRunModel='router/single';
  a._afetch=async(url,options)=>{a.requests.push(JSON.parse(options.body));return {json:async()=>a.requests.length===1?{error:'퀘스트 진행 중'}:{done:1}};};
  a.dsConfirm=async()=>{a.failRunModel='other';return true;};
  await a.rerunFail({hash:'one'});
  assert.equal(a.requests[1].model,'router/single');
  assert.equal(a.requests[1].force,true);
  console.log('failure rerun model client: OK');
})().catch(e=>{console.error(e);process.exitCode=1;});
