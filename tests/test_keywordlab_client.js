const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
async function main(){
 const window={};vm.runInNewContext(fs.readFileSync('prism/vendor/app-24-keywordlab.js','utf8'),{window,clearTimeout,setTimeout});
 const a=window.PRISM_APP_PARTS[0]();a._authHeaders=()=>({});a.reviewer='검수자';
 assert.equal(a.kwReady(),false);a.kwCount=1;a.kwSlots[0]={model:'solar',version_id:'v1'};assert.equal(a.kwReady(),true);
 a.kwModel(a.kwSlots[0],'gpt');assert.equal(a.kwSlots[0].version_id,'');
 let sent;a._afetch=async(url,opt)=>{sent=JSON.parse(opt.body);return{json:async()=>({ok:true})}};
 await a.kwRequest('',{action:'review'});assert.equal(sent.reviewer,'검수자');
 a.kwRunData={cells:{'key:A':{id:'key:A',status:'done',review_revision:2,refined:{keywords:[{text:'한국은행',kind:'single'}]},reviews:[],final:null}}};
 a.kwCatalog={versions:[],gold:[]};a.kwOpenReview({key:'key'},{label:'A'});assert.equal(a.kwReview.expected_revision,2);assert.equal(a.kwReview.judgments[0].verdict,'');
 a.kwRunId='run';a.kwOpenRun=async()=>{a.kwRunData.cells['key:A'].review_revision=3};
 await a.kwSaveReview();assert.equal(sent.expected_revision,2);assert.equal(a.kwReview.expected_revision,3);
 a._afetch=async()=>({json:async()=>({ok:false,error:'저장 충돌'})});await a.kwSaveReview();assert.equal(a.kwError,'저장 충돌');assert.equal(a.kwBusy,false);
 console.log('Keyword workspace client: required selection, review identity, revision and failures passed');
}
main().catch(e=>{console.error(e);process.exitCode=1});
