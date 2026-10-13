const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
async function main(){
 const window={};vm.runInNewContext(fs.readFileSync('prism/vendor/app-24-keywordlab.js','utf8'),{window,clearTimeout,setTimeout,crypto:require('node:crypto')});
 const a=window.PRISM_APP_PARTS[0]();a._authHeaders=()=>({});a.reviewer='검수자';
 assert.equal(a.kwReady(),false);a.kwChooseMode('single');a.kwSlots[0]={model:'solar',version_id:'v1'};
 assert.equal(a.kwReady(),false);a.kwLead='한국은행이 금리를 인하했다';a.kwEntities='한국은행';assert.equal(a.kwReady(),true);
 a.kwModel(a.kwSlots[0],'gpt');assert.equal(a.kwSlots[0].version_id,'');
 let sent;a._afetch=async(url,opt)=>{sent=JSON.parse(opt.body);return{json:async()=>({ok:true})}};
 await a.kwRequest('',{action:'review'});assert.equal(sent.reviewer,'검수자');
 a.kwRunData={cells:{'key:A':{id:'key:A',status:'done',review_revision:2,refined:{keywords:[{text:'한국은행',kind:'single'}]},reviews:[],final:null}}};
 a.kwCatalog={versions:[],gold:[]};a.kwOpenReview({key:'key'},{label:'A'});assert.equal(a.kwReview.expected_revision,2);assert.equal(a.kwReview.judgments[0].verdict,'');assert.equal(a.kwTab,'run');
 a.kwRunId='run';a.kwReview.judgments[0].verdict='accept';a.kwOpenRun=async()=>true;
 a._afetch=async(url,opt)=>{sent=JSON.parse(opt.body);return{json:async()=>({ok:true,revision:1,cell:{id:'key:A',review_revision:3,reviews:[{judgments:sent.judgments}],final:null}})}};
 await a.kwSaveReview();assert.equal(sent.expected_revision,2);assert.equal(a.kwReview.expected_revision,3);
 a._afetch=async()=>({json:async()=>({ok:false,error:'저장 충돌'})});await a.kwSaveReview();assert.match(a.kwError,/저장 충돌/);assert.equal(a.kwMessage,'');assert.equal(a.kwBusy,false);

 const b=window.PRISM_APP_PARTS[0]();
 assert.deepEqual(Array.from(b.kwTabs,t=>t[0]),['run','prompts','improve']);
 b.kwCatalog={can_manage:true,runs:[{id:'one',n:1},{id:'many',n:3}],versions:[],default_rules:'기본 규칙'};
 b.kwChooseMode('compare');assert.equal(b.kwCount,2);
 b.kwCount=3;b.kwSlots=[{model:'m1',version_id:'v1'},{model:'m2',version_id:'v2'},{model:'m3',version_id:'v3'}];
 b.kwSource='reuse';b.kwReuse='many';assert.equal(b.kwReady(),true);
 b.kwSlots[2].version_id='v2';assert.equal(b.kwReady(),false);b.kwSlots[2].version_id='v3';
 b.kwChooseMode('single');assert.equal(b.kwCount,1);assert.equal(b.kwReuse,'many');assert.equal(b.kwReady(),true);
 b.kwSlots[0].version_id='v1';b.kwSource='hash';b.kwHashes='a,a';assert.equal(b.kwReady(),false);b.kwHashes='a,b';assert.equal(b.kwReady(),true);
 b.kwSource='sample';b.kwSample=20;b.kwRequest=async(q,data)=>{sent=data;return{ok:true,id:'new'}};b.kwOpenRun=async()=>{};b.kwLoad=async()=>{};
 await b.kwStart();assert.equal(sent.slots.length,1);assert.equal(sent.sample,20);assert.equal(sent.blind,false);
 b.kwChooseMode('compare');b.kwCount=3;b.kwSample=2;await b.kwStart();assert.equal(sent.slots.length,3);assert.equal(sent.sample,2);assert.equal(sent.blind,true);
 b.kwCreateVersion(1);assert.equal(b.kwTab,'prompts');assert.equal(b.kwDraft.model,'m2');
 b.kwRequest=async()=>({ok:true,version:{id:'new-v',model:'m2',number:1,rules:'new rules',title:'new'}});
 await b.kwSaveVersion();assert.equal(b.kwTab,'run');assert.equal(b.kwSlots[1].version_id,'new-v');assert.equal(b.kwDraftSlot,null);

 // 모델을 고르기 전에도 작성 화면에 진입하고 저장한 모델·버전을 원래 조합에 연결한다.
 const draft=window.PRISM_APP_PARTS[0]();draft.kwCatalog={can_manage:true,default_rules:'기본 규칙'};
 draft.kwChooseMode('single');draft.kwVersion={id:'old'};draft.kwCreateVersion(0);
 assert.equal(draft.kwTab,'prompts');assert.equal(draft.kwDraft.model,'');assert.equal(draft.kwVersion,null);
 draft.kwDraft.model='solar';draft.kwDraft.title='첫 버전';draft.kwLoad=async()=>{};
 draft.kwRequest=async(q,data)=>({ok:true,version:{...data,id:'created',number:1}});
 await draft.kwSaveVersion();assert.equal(draft.kwTab,'run');assert.equal(draft.kwSlots[0].model,'solar');assert.equal(draft.kwSlots[0].version_id,'created');
 draft.kwCreateVersion(0);draft.kwDraft.model='other';await draft.kwSaveVersion();assert.equal(draft.kwSlots[0].model,'other');
 draft.kwCreateVersion(0);draft.kwRequest=async()=>{throw new Error('저장 실패')};await draft.kwSaveVersion();
 assert.equal(draft.kwTab,'prompts');assert.equal(draft.kwDraftSlot,0);assert.equal(draft.kwBusy,false);
 draft.kwTab='run';draft.kwBusy=true;draft.kwCreateVersion(1);assert.equal(draft.kwTab,'run');
 draft.kwBusy=false;draft.kwCatalog.can_manage=false;draft.kwCreateVersion(1);assert.equal(draft.kwTab,'run');
 const markup=fs.readFileSync('prism/ui/19j-keywordlab.html','utf8');
 assert.match(markup,/x-bind:disabled="kwBusy \|\| !kwCatalog\?\.can_manage">새 버전 만들기/);
 const c=window.PRISM_APP_PARTS[0]();c.kwRunData={id:'old'};c.kwReview={};c._kwSavedReview=c.kwReviewSnapshot();await c.kwOpenRun('');assert.equal(c.kwRunData,null);assert.equal(c.kwReview,null);
 b.kwCatalog.rules_by_target={keyword:'keyword rules',sentence:'preserved sentence rules'};
 b.kwTargetChange('sentence');assert.equal(b.kwDraft.rules,'preserved sentence rules');assert.equal(b.kwSlots[0].version_id,'');assert.equal(b.kwRunData,null);
 b.kwChooseMode('single');b.kwSlots[0]={model:'m1',version_id:'sv1'};b.kwLead='리드문';b.kwEntities='';b.kwSource='direct';
 assert.equal(b.kwReady(),false);b.kwSentenceKeywords='한국은행';assert.equal(b.kwReady(),true);
 b.kwRequest=async(q,data)=>{sent=data;return{ok:true,id:'sentence-run'}};
 await b.kwStart();assert.equal(sent.target,'sentence');assert.equal(sent.keyword_source,'manual');assert.equal(sent.keywords[0],'한국은행');
 b.kwRunData={target:'sentence',cells:{'s:A':{id:'s:A',status:'failed',review_revision:0,sentence:{draft:'긴 문장 초안'},reviews:[]}}};
 b.kwOpenReview({key:'s'},{label:'A'});assert.equal(b.kwReview.target,'sentence');assert.equal(b.kwReview.judgments[0].original,'긴 문장 초안');assert.equal(b.kwTab,'run');
 // 상세 검수: 다음 콘텐츠는 같은 조합 우선 · 실패 셀 건너뜀 · 저장 후 다음 미검수 · 삭제 시 선택값 정리
 const d=window.PRISM_APP_PARTS[0]();d.kwCatalog={versions:[],gold:[]};
 const cell=(id,status,reviews)=>({id,status,review_revision:0,refined:{keywords:[{text:'한국은행 금리',kind:'combo'}]},reviews,final:null});
 const it=[{key:'i1'},{key:'i2'},{key:'i3'}],sl=[{label:'A'},{label:'B'}];
 d.kwRunData={items:it,slots:sl,cells:{'i1:A':cell('i1:A','done',[]),'i1:B':cell('i1:B','done',[]),'i2:A':cell('i2:A','failed',[]),'i2:B':cell('i2:B','done',[{judgments:[{verdict:'accept',reason:'',corrected:'한국은행 금리'}],additions:[],no_keywords:false,note:''}]),'i3:B':cell('i3:B','done',[])}};
 d.kwOpenReview(it[0],sl[1]);assert.equal(d.kwReviewGo(1),true);assert.equal(d.kwReview.cell_id,'i2:B');
 d.kwOpenReview(it[0],sl[0]);d.kwReviewGo(1);assert.equal(d.kwReview.cell_id,'i2:B');assert.equal(d.kwReviewGo(-1),true);assert.equal(d.kwReview.cell_id,'i1:B');
 assert.equal(d.kwNextPending(),true);assert.equal(d.kwReview.cell_id,'i3:B');d.kwNextPending();assert.equal(d.kwReview.cell_id,'i1:A');
 assert.equal(d.kwSlotState(d.kwRunData.cells['i2:A']),'실패');assert.equal(d.kwInKeywords('한국은행'),true);assert.equal(d.kwInKeywords('물가'),false);
 d.kwVersion={id:'v9',model:'solar',number:2,title:'t'};d.kwSlots[0].version_id='v9';d.kwLoopVersion='v9';d.kwCatalog.active={};
 d.kwRequest=async(q,data)=>{sent=data;return{ok:true}};d.kwLoad=async()=>{};const confirm0=window.confirm;window.confirm=()=>true;
 await d.kwDeleteVersion();window.confirm=confirm0;assert.equal(sent.action,'delete');assert.equal(d.kwSlots[0].version_id,'');assert.equal(d.kwLoopVersion,'');assert.equal(d.kwVersion,null);
 d.kwCatalog.active={m:{version_id:'v1'}};assert.equal(d.kwAdopted({id:'v1'}),true);
 // 결과 목록: 콘텐츠 상태 · 행 클릭은 판정 안 한 조합부터
 assert.deepEqual(d.kwItemState(it[0])[0],'미검수');assert.equal(d.kwItemState(it[1])[0],'판정됨');
 d.kwRunData.cells['i3:A']=cell('i3:A','failed',[]);assert.equal(d.kwItemState(it[2])[0],'미검수');
 d.kwRunData.cells['i1:A'].reviews=[{judgments:[{verdict:'accept',reason:'',corrected:'x'}],additions:[],no_keywords:false,note:''}];
 assert.equal(d.kwItemState(it[0])[0],'검수 중');d.kwOpenItem(it[0]);assert.equal(d.kwReview.cell_id,'i1:B');
 d.kwRunData.cells['i2:B'].final={};assert.equal(d.kwItemState(it[1])[0],'확정');
 delete d.kwRunData.cells['i3:B'];assert.equal(d.kwItemState(it[2])[0],'대기');
 // 실험 기록 삭제: 확인 후 선택·상세·결과를 비운다
 const e=window.PRISM_APP_PARTS[0]();e.kwCatalog={runs:[{id:'r1',n:2,created_at:1}]};e.kwRunId='r1';e.kwRunData={};e.kwReview={};
 e.kwRequest=async(q,data)=>{sent=data;return{ok:true}};e.kwLoad=async()=>{};const c0=window.confirm;window.confirm=()=>true;
 await e.kwDeleteRun();window.confirm=c0;assert.equal(sent.action,'delete_run');assert.equal(sent.run_id,'r1');assert.equal(e.kwRunId,'');assert.equal(e.kwRunData,null);assert.equal(e.kwReview,null);
 // 저장 실패와 재조회 실패를 구분하고, 서버가 확인한 판단을 이전 조회로 덮지 않는다.
 const f=window.PRISM_APP_PARTS[0]();f.kwCatalog={};f.kwRunId='r';
 f.kwRunData={id:'r',revision:0,status:'done',items:it,slots:[sl[0]],cells:Object.fromEntries(it.map(i=>[i.key+':A',cell(i.key+':A','done',[])]))};
 f.kwOpenReview(it[0],sl[0]);let requests=0;
 f.kwRequest=async()=>{requests++;throw Error('통신 실패');};
 await f.kwSaveReview();assert.match(f.kwError,/1번째 키워드/);assert.equal(requests,0);
 f.kwReview.judgments[0].verdict='edit';f.kwReview.note='검수 메모';
 await f.kwSaveReview();assert.match(f.kwError,/사유/);assert.equal(requests,0);
 f.kwReview.judgments[0].reason='표현 문제';
 const dirty=f.kwReviewSnapshot();
 assert.equal(f.kwReviewGo(1),false);f.kwCloseReview();f.kwChangeTab('prompts');f.kwTargetChange('sentence');
 await f.kwOpenRun('other');assert.equal(f.kwRunId,'r');assert.equal(f.kwTab,'run');assert.equal(f.kwTarget,'keyword');assert.equal(f.kwReviewSnapshot(),dirty);
 let prevented=false;f.kwBeforeUnload({preventDefault(){prevented=true}});assert.equal(prevented,true);
 await f.kwSaveReview();assert.match(f.kwError,/통신 실패/);assert.equal(f.kwReviewSnapshot(),dirty);assert.equal(f.kwReviewDirty(),true);
 let finishPost;f.kwRequest=async(query,data)=>{
   if(!data) throw Error('재조회 실패');
   await new Promise(resolve=>finishPost=resolve);
   return {ok:true,revision:1,cell:{id:data.cell_id,review_revision:1,reviews:[{judgments:data.judgments,additions:[],no_keywords:false,note:data.note}],final:null}};
 };
 const saving=f.kwSaveReview();assert.equal(f.kwBusy,true);assert.equal(f.kwReviewGo(1),false);f.kwCloseReview();assert.equal(f.kwReview.cell_id,'i1:A');
 finishPost();await saving;assert.equal(f.kwReview.cell_id,'i1:A');assert.equal(f.kwReviewDirty(),false);assert.match(f.kwError,/판단은 저장했지만/);assert.equal(f.kwMessage,'');
 const stale=JSON.parse(JSON.stringify(f.kwRunData));stale.revision=0;stale.cells['i1:A'].reviews=[];stale.cells['i1:A'].review_revision=0;
 f.kwRequest=async()=>({ok:true,run:stale});await f.kwOpenRun('r');
 assert.equal(f.kwRunData.cells['i1:A'].review_revision,1);assert.equal(f.kwRunData.cells['i1:A'].reviews[0].note,'검수 메모');
 assert.equal(f.kwReviewGo(1),true);assert.equal(f.kwReviewGo(-1),true);assert.equal(f.kwReview.note,'검수 메모');assert.equal(f.kwReview.judgments[0].verdict,'edit');
 f.kwReview.note='아직 저장하지 않은 메모';const savedReviews=JSON.stringify(f.kwRunData.cells['i1:A'].reviews);
 window.confirm=()=>false;await f.kwDiscardReview();assert.equal(f.kwReview.note,'아직 저장하지 않은 메모');
 window.confirm=()=>true;await f.kwDiscardReview();window.confirm=confirm0;
 assert.equal(f.kwReview,null);assert.equal(JSON.stringify(f.kwRunData.cells['i1:A'].reviews),savedReviews);
 assert.match(markup,/x-on:mousedown.self="kwCloseReview\(\)"/);assert.match(markup,/kwBeforeUnload\(\$event\)/);
 const cancel=window.PRISM_APP_PARTS[0]();cancel.kwRunId='r';cancel.kwRunData=JSON.parse(JSON.stringify(f.kwRunData));
 cancel.kwRunData.cells['i1:A'].final={id:'final-review',partition:'development'};
 cancel.kwCatalog={can_final:true,gold:[{item_key:'i1',run_id:'r',cell_id:'i1:A',review_id:'final-review',partition:'development'},
   {item_key:'i2',run_id:'other',cell_id:'i2:A',review_id:'other-review',partition:'development'}]};
 cancel.kwTraining=['i1','i2'];cancel.kwOpenReview(it[0],sl[0]);const oldNote=cancel.kwReview.note;let canceledRequests=0;
 cancel.kwRequest=async(query,data)=>{
   if(!data) return {ok:true,run:JSON.parse(JSON.stringify(cancel.kwRunData))};
   canceledRequests++;sent=data;return {ok:true,revision:2,cell:{...cancel.kwRunData.cells[data.cell_id],review_revision:2,final:null}};
 };
 window.confirm=()=>false;await cancel.kwCancelFinal();assert.equal(canceledRequests,0);
 window.confirm=()=>true;cancel.kwCatalog.can_final=false;await cancel.kwCancelFinal();assert.equal(canceledRequests,0);
 cancel.kwCatalog.can_final=true;cancel.kwReview.note='미저장';await cancel.kwCancelFinal();assert.equal(canceledRequests,0);cancel.kwReview.note=oldNote;
 await cancel.kwCancelFinal();window.confirm=confirm0;
 assert.equal(sent.action,'cancel_final');assert.equal(sent.final_id,'final-review');assert.equal(sent.expected_revision,1);
 assert.equal(cancel.kwReview.final,null);assert.equal(cancel.kwReview.note,oldNote);assert.equal(cancel.kwReview.expected_revision,2);
 assert.equal(cancel.kwReview.cell_id,'i1:A');assert.equal(cancel.kwCatalog.gold.length,1);assert.deepEqual(Array.from(cancel.kwTraining),['i2']);
 assert.equal(cancel.kwSlotState(cancel.kwRunData.cells['i1:A']),'판정됨');assert.equal(cancel.kwReviewDirty(),false);
 cancel.kwReview.final={id:'final-again'};cancel.kwRequest=async()=>{throw Error('저장 충돌')};window.confirm=()=>true;
 await cancel.kwCancelFinal();window.confirm=confirm0;assert.equal(cancel.kwReview.final.id,'final-again');assert.match(cancel.kwError,/저장 충돌/);assert.equal(cancel.kwMessage,'');
 cancel.kwRequest=async()=>({ok:true,cell:{id:'i1:A',reviews:[{note:oldNote}],review_revision:3,final:null},revision:3});
 cancel.kwOpenRun=async()=>false;window.confirm=()=>true;await cancel.kwCancelFinal();window.confirm=confirm0;
 assert.equal(cancel.kwReview.final,null);assert.equal(cancel.kwReview.expected_revision,3);assert.equal(cancel.kwReview.note,oldNote);
 assert.match(cancel.kwError,/최종 확정은 취소했지만/);assert.equal(cancel.kwMessage,'');assert.equal(cancel.kwReviewDirty(),false);
 const done=window.PRISM_APP_PARTS[0]();done.kwRunId='r';done.kwCatalog={can_final:true,gold:[]};
 done.kwRunData={id:'r',revision:0,items:[it[0]],slots:[sl[0]],cells:{'i1:A':cell('i1:A','done',[])}};
 done.kwOpenReview(it[0],sl[0]);done.kwReview.judgments[0].verdict='accept';let completeCalls=0,requestId;
 done.kwRequest=async(query,data)=>{
   completeCalls++;assert.equal(data.action,'complete_review');
   if(completeCalls===1){requestId=data.request_id;throw Error('응답 유실');}
   assert.equal(data.request_id,requestId);
   const final={id:'final-done',partition:data.partition};
   return {ok:true,revision:1,cell:{id:data.cell_id,review_revision:1,final,reviews:[{...data,id:final.id}]},
     gold:{id:'gold-done',item_key:'i1',run_id:'r',cell_id:'i1:A',review_id:final.id,partition:data.partition,keywords:[{text:'한국은행 금리'}]}};
 };
 await done.kwCompleteReview();assert.equal(completeCalls,0);assert.match(done.kwError,/용도/);
 done.kwReview.partition='evaluation';const pending=done.kwReviewSnapshot();
 await done.kwCompleteReview();assert.equal(completeCalls,1);assert.equal(done.kwReviewSnapshot(),pending);assert.equal(done.kwReviewDirty(),true);
 await done.kwCompleteReview();assert.equal(completeCalls,2);assert.equal(done.kwCompletionDone(),true);assert.equal(done.kwReviewDirty(),false);
 assert.equal(done.kwReview.cell_id,'i1:A');assert.match(done.kwMessage,/평가용/);assert.equal(done.kwCatalog.gold[0].id,'gold-done');
 await done.kwCompleteReview();assert.equal(completeCalls,2);assert.equal(done.kwReview.expected_revision,1);
 done.kwReview.note='수정';assert.equal(done.kwCompletionDone(),false);
 assert.equal(markup.includes('kwSaveGold()'),false);assert.equal(markup.includes('kwSaveReview(true)'),false);
 assert.match(markup,/검수 완료·정답셋 저장/);assert.match(markup,/name="kw-gold-partition" value="evaluation"/);
 assert.match(markup,/<fieldset class="detailview__eval" x-bind:disabled="kwBusy"/);
 console.log('Keyword workspace client: persistence, completion retries, validation, navigation and final cancellation passed');
 // 개선 실행 조건과 요청 중 상태, 실패 후 재시도, 결과 가져오기를 검증한다.
 const improve=window.PRISM_APP_PARTS[0]();improve.kwTab='improve';
 improve.kwCatalog={can_manage:true,versions:[{id:'base',target:'keyword'}],gold:Array.from({length:21},(_,i)=>({item_key:'dev'+i,partition:'development'}))};
 improve.kwLoopVersion='base';improve.kwCompiler='timely|claude-fable-5';
 assert.match(improve.kwImproveIssue(),/1~20/);
 improve.kwTraining=['dev0'];assert.equal(improve.kwImproveIssue(),'');assert.match(improve.kwImproveIssue(true),/평가용/);
 improve.kwTraining=improve.kwCatalog.gold.map(g=>g.item_key);assert.match(improve.kwImproveIssue(),/1~20/);
 improve.kwTraining=['stale'];assert.match(improve.kwImproveIssue(),/변경/);improve.kwTraining=['dev0'];
 improve.kwCatalog.gold.push({item_key:'eval',partition:'evaluation'});improve.kwEvaluation=['eval'];
 improve.kwTuneRounds=0;assert.match(improve.kwImproveIssue(true),/1~10/);improve.kwTuneRounds=3;
 assert.equal(improve.kwImproveIssue(true),'');improve.kwCatalog.tuning={status:'running'};assert.match(improve.kwImproveIssue(),/실행 중/);improve.kwCatalog.tuning=null;
 const proposal={id:'p',target:'keyword',parent_id:'base',model:'solar-pro4',rules:'완성 규칙',changes:[{reason:'누락',change:'주제 포함'}]};
 let finish,focus;improve.kwFocus=id=>{focus=id};improve.kwRequest=async(q,data)=>{sent=data;return new Promise(resolve=>{finish=resolve})};
 const compilePending=improve.kwCompile();assert.equal(improve.kwBusy,true);assert.equal(improve.kwImproving,'manual');assert.equal(sent.compiler_model,'timely|claude-fable-5');
 await improve.kwCompile();finish({ok:true,proposal});await compilePending;
 assert.equal(improve.kwBusy,false);assert.equal(improve.kwImproving,'');assert.equal(improve.kwProposal,proposal);assert.equal(focus,'kw-proposal-result');
 improve.kwRequest=async()=>{throw new Error('모델 호출 실패 · timeout')};await improve.kwCompile();
 assert.match(improve.kwError,/timeout/);assert.equal(improve.kwProposal,proposal);assert.equal(improve.kwTraining[0],'dev0');assert.equal(focus,'kw-improve-status');assert.equal(improve.kwBusy,false);
 improve.kwUseProposal();assert.equal(improve.kwTab,'prompts');assert.equal(focus,'kw-version-draft');assert.equal(improve.kwDraft.rules,proposal.rules);assert.equal(improve.kwDraft.proposal_id,'p');
 improve.kwTab='improve';improve.kwLoad=async()=>{};
 improve.kwRequest=async()=>({ok:true,tuning:{status:'running'}});await improve.kwStartTuning();assert.equal(improve.kwCatalog.tuning.status,'running');assert.equal(improve.kwImproving,'');assert.match(improve.kwMessage,/시작했습니다/);

}
main().catch(e=>{console.error(e);process.exitCode=1});
