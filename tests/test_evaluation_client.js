const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function make(){
  const window={},timers=[];
  const ctx={window,URLSearchParams,clearTimeout(){},setTimeout(fn){timers.push(fn);return timers.length;}};
  for(const name of ['app-04-_err.js','app-25-evaluation.js']) vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../prism/vendor',name),'utf8'),ctx);
  const app={};for(const part of window.PRISM_APP_PARTS)Object.defineProperties(app,Object.getOwnPropertyDescriptors(part()));
  app._authHeaders=()=>({});app.verTxt='v12';return app;
}
async function run(){
  const a=make(),calls=[];
  a._afetch=async(url,options)=>{calls.push({url,options});return{json:async()=>url==='/evaluation-start'?{ok:true,id:'r1',run:{id:'r1',status:'queued'}}:url.startsWith('/evaluation-run?')?{ok:true,run:{id:'r1',status:'running'}}:{ok:true,items:[]}};};
  a.esSlots=[{key:1,model:'solar|A'},{key:2,model:'timely|B'}];a.esReference='B';
  await a.esStart();await Promise.resolve();
  const request=JSON.parse(calls.find(c=>c.url==='/evaluation-start').options.body);
  assert.deepEqual(request.models,['A','B']);assert.equal(request.baseline_model,'B');assert.equal(request.limits.max_calls,100);assert.ok(request.request_id);
  await a.esStart();assert.equal(calls.filter(c=>c.url==='/evaluation-start').length,1,'duplicate start while active must be ignored');
  a.esBusy=false;a.esSlots[1].model='timely|A';await a.esStart();assert.match(a.esError,/중복/);
  a.esSlots=[{key:1,model:'solar|A'}];a.esMaxCalls=0;await a.esStart();assert.match(a.esError,/한도/);
  assert.match(a.esCost({cost:{status:'unknown',known_usd:0}}),/미확인/);assert.doesNotMatch(a.esCost({cost:{status:'unknown',known_usd:0}}),/\$0/);
  assert.match(a.esCost({cost:{status:'partial',known_usd:.02,unknown_calls:3}}),/일부.*0.020000.*3회/);
  assert.match(a.esCost({cost:{status:'known',known_usd:0,total_usd:0,calls:1}}),/\$0.000000/);
  assert.equal(a.esStatus({status:'cancel_requested'}),'중단 요청됨');assert.equal(a.esStatus({status:'cancelled'}),'중단 완료');
  assert.equal(a.esCanResume({status:'cancelled',can_resume:true}),true);assert.equal(a.esCanResume({status:'running',can_resume:true}),false);
  const normalized=a.esNormalize({id:'r',status:'completed',models:['A','B'],total:2,model_results:[{model:'A',total:2,metrics:{grade_accuracy:.5,grade_n:2,summary_sim:null}}],items:[{hash:'h',source:{title:'당시 제목'},expected:{summary:''},got:{A:{ok:true,summary:'결과'},B:null},states:{B:'pending'}}],snapshot:{baseline_model:'A',metric_version:'v1'}});
  assert.equal(normalized.models[0].grade_accuracy,.5);assert.equal(normalized.items[0].title,'당시 제목');assert.equal(normalized.items[0].got.B.unprocessed,true);assert.equal(normalized.items[0].expected.summary,'');
  a.evalDetail=normalized;assert.equal(a.evalCols[0].grade_n,2);assert.equal(a.evalCellStatus(a.evalItems[0].got.B),'미처리');
  assert.match(a.esVerdict(normalized),/판단 보류/);assert.match(a.esVerdict({...normalized,comparisons:[{comparable:false}]}),/비교 불가/);
  assert.equal(a.evalAssessmentValue(a.evalCols[0],'summary_semantic_cosine'),'미측정');
  // All detail/history/candidate viewing paths must stay read-only.
  const reads=[];a._afetch=async(url,options)=>{assert.equal(options.method,undefined);reads.push(url);return{json:async()=>url.startsWith('/evaluation-run?')?{ok:true,run:{...normalized,models:['A','B']}}:url.startsWith('/evaluation-candidate?')?{ok:true,candidate:{id:'c',status:'completed'}}:{ok:true,items:[url==='/evaluation-runs'?{kind:'evaluation',id:'new',models:['A'],created_at:200}:{kind:'eval',id:12,ts:100}]}};};
  await a.openEvalHistory({kind:'evaluation',id:'r'});await a.loadEvalRuns();a.esCandidate={id:'c'};await a.esPollCandidate('c');
  assert.equal(a.evalRuns.length,2);assert.equal(a.evalRuns[0].kind,'evaluation');assert.equal(a.evalRuns[1].kind,'eval');assert.ok(reads.some(url=>url.startsWith('/eval-grounding?kind=evaluation')));
  // A late unified detail response cannot replace another selected history record.
  const pending=[];a._afetch=(url,options)=>new Promise(resolve=>pending.push({url,reply:r=>resolve({json:async()=>r})}));
  const old=a.openEvalHistory({kind:'evaluation',id:'old'}),fresh=a.openEvalHistory({kind:'eval',id:44});pending[1].reply({ok:true,kind:'eval',id:44,models:[],items:[]});await fresh;pending[0].reply({ok:true,run:{id:'old',models:[]}});await old;assert.equal(a.evalDetail.id,44);
  // The candidate branch uses frozen source and only operative prompt paths.
  a.evalDetail={kind:'evaluation',snapshot:{baseline_model:'A',executions:{A:{four_calls:true,single_system:'unused',calls:{summary:{system:'active'},entities:{error:'unavailable'}}}}}};
  assert.equal(a.esCandidateChangeOptions.length,1);assert.equal(a.esCandidateChangeOptions[0].value,'calls.summary.system');
  a.esCandidate={allowed_changes:['calls.summary.system'],selected_round:1,final_comparison:{decision:'hold'}};
  const promptCandidate={round:1,execution:{calls:{summary:{system:'새 프롬프트 <script>literal</script>'}}}};
  const changes=a.esCandidatePromptChanges(promptCandidate);assert.equal(changes.length,1);assert.equal(changes[0].before,'active');assert.equal(changes[0].after,'새 프롬프트 <script>literal</script>');
  assert.match(a.esCandidateFinalLabel(promptCandidate),/판단 보류/);assert.match(a.esCandidateFinalLabel({...promptCandidate,round:2}),/미검증/);
  assert.equal(a.esCandidatePromptChanges({execution:{calls:{summary:{system:'active'}}}}).length,0);
  assert.equal(a.esStage({status:'cancelled',phase:'cancelled'}),'현재 단계 · 중단 완료');
  assert.doesNotMatch(JSON.stringify(a.esBasisRows({snapshot:{dataset_fingerprint:'SECRET_HASH',rows:[],executions:{}}})),/SECRET_HASH/);
  a.evalSelected={kind:'evaluation',id:'source'};a.esFindCandidates();a.esCandidateGoal='근거 없는 리드문 줄이기';a.esCandidate=null;
  a._afetch=async(url,options)=>{calls.push({url,options});return{json:async()=>({ok:true,candidate:{id:'c',status:'completed',candidates:[]}})};};
  await a.esStartCandidate();const candidateRequest=JSON.parse(calls.find(c=>c.url==='/evaluation-candidate-start').options.body);assert.equal(candidateRequest.source_run_id,'source');assert.deepEqual(candidateRequest.allowed_changes,['calls.summary.system']);assert.equal(candidateRequest.development_ids,undefined);
  const mutations=[];a._afetch=async(url,options)=>{mutations.push(url);return{json:async()=>({ok:true,status:'cancelled',run:{id:'r1',status:'cancelled',can_resume:true},items:[]})};};a.esRun={id:'r1',status:'running'};await a.esCancel('r1');await a.esResume('r1');assert.ok(mutations.includes('/evaluation-cancel'));assert.ok(mutations.includes('/evaluation-resume'));assert.equal(mutations.some(u=>u.includes('apply')||u.includes('autopilot')),false);
  const previews=[],p=make();p.evalScope='all';p._afetch=url=>new Promise(resolve=>previews.push({url,reply:body=>resolve({json:async()=>body})}));
  const firstPreview=p.esLoadPreview();p.evalScope='eval';const secondPreview=p.esLoadPreview();previews[1].reply({ok:true,total:4});await secondPreview;previews[0].reply({ok:true,total:99});await firstPreview;assert.equal(p.esPreview.total,4,'late preview must not replace the selected scope');
  a.evalSelected={kind:'evaluation',id:'opaque'};a.esMaxCalls=23;a.loadEvalGrounding=async()=>{};let diagnostic;
  a._afetch=async(url,options)=>{diagnostic={url,body:JSON.parse(options.body)};return{json:async()=>({ok:true})};};
  await a.runEvalGrounding();assert.equal(diagnostic.url,'/eval-grounding-start');assert.equal(diagnostic.body.kind,'evaluation');assert.equal(diagnostic.body.id,'opaque');assert.equal(diagnostic.body.max_calls,23);
  a.evalGrounding={status:'cancel_requested'};assert.match(a.evalGroundingStatus(),/중단 요청됨/);
  const env=make(),environmentCalls=[],saved={id:'env1',name:'엄격 검수',judge_model:'judge',limits:{max_calls:37}};
  env.esEnvironmentName='엄격 검수';env.esEnvironmentJudge='solar|judge';env.esMaxCalls=37;
  env._afetch=async(url,options)=>{environmentCalls.push({url,options});return{json:async()=>url==='/evaluation-environment-save'?{ok:true,environment:saved}:{ok:true,items:[saved]}};};
  await env.esSaveEnvironment();assert.equal(env.esEnvironmentId,'env1');assert.equal(env.evalJudgeModel,'judge');assert.equal(env.esMaxCalls,37);assert.deepEqual(JSON.parse(environmentCalls[0].options.body),{name:'엄격 검수',judge_model:'judge',max_calls:37});assert.equal(environmentCalls[1].options.method,undefined);
  env.esSlots=[{key:1,model:'A'}];let environmentStart;env.esPoll=async()=>{};env.loadEvalRuns=async()=>{};
  env._afetch=async(url,options)=>{environmentStart=JSON.parse(options.body);return{json:async()=>({ok:true,id:'env-run',run:{id:'env-run',status:'queued'}})};};await env.esStart();assert.equal(environmentStart.environment_id,'env1');
  assert.equal(a.esLegacyComparisonState({verdict:'improved',paired:{comparable:true,metrics:{grade_accuracy:{n:1,conclusion:'insufficient_sample'}}}}),'hold');
  assert.match(a.esLegacyComparisonLabel({verdict:'even',paired:{comparable:true,metrics:{grade_accuracy:{n:40,conclusion:'inconclusive'}}}}),/판단 보류/);
  a.evalCmp={ok:true,a:{summary_sim:.73,summary_n:9},b:{summary_sim:.81,summary_n:9}};
  const similarity=a.evalCmpRows.find(r=>r.k.includes('리드문'));assert.equal(similarity.a,'0.7300');assert.equal(similarity.b,'0.8100');assert.doesNotMatch(similarity.dTxt,/%/);
  assert.match(a.esContext({kind:'evaluation',total:16,completed:16,models:['A','B'],snapshot:{rows:Array(8).fill({})}}),/자료 8개 · 모델별 처리 16건/);
  assert.match(a.esCost({cost:{status:'known',total_usd:0,calls:4,sources:['synthetic']}}),/모의 실행 비용/);
  const rerun=make(),repeatCalls=[];
  rerun.esPoll=async()=>{};
  rerun._afetch=async(url,options)=>{repeatCalls.push({url,body:JSON.parse(options.body)});return{json:async()=>({ok:true,id:'new',run:{id:'new',status:'running'}})};};
  await rerun.esRerun('frozen-source');
  assert.equal(repeatCalls[0].url,'/evaluation-rerun');assert.equal(repeatCalls[0].body.id,'frozen-source');
  assert.ok(repeatCalls[0].body.request_id);assert.equal(repeatCalls[0].body.models,undefined,'repeat must use the server snapshot');
  assert.equal(rerun.esRun.id,'new');assert.equal(rerun.evalTab,'run');
  await rerun.esRerun('frozen-source');assert.equal(repeatCalls.length,1,'active repeat cannot be started twice');
  console.log('Common evaluation client: request contract, read-only views, legacy history, races, cost, states, candidates passed');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
