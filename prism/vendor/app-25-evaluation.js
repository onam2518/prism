/* Common evaluation UI. Reads never start model calls; only explicit form actions POST. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
  esSlots: [{key:1,model:''}], esSlotKey:1, esReference:'', esMethod:'basic', esMaxCalls:100,
  esEnvironments:[], esEnvironmentId:'', esEnvironmentName:'', esEnvironmentJudge:'solar-pro4-260806', esEnvironmentBusy:false, esEnvironmentError:'',
  esPreview:null, esPreviewError:'', esPreviewBusy:false, _esPreviewSeq:0,
  esRun:null, esBusy:false, esActionBusy:false, esError:'', esRequestId:'', _esPollT:null,
  esCandidateForm:false, esCandidateGoal:'', esCandidateChanges:'single_system', esCandidateMaxCalls:100,
  esCandidateMaxRounds:3, esCandidate:null, esCandidateBusy:false, esCandidateError:'', _esCandidateT:null,
  get esModels() { return [...new Set(this.esSlots.map(s=>String(s.model||'').split('|').pop()).filter(Boolean))]; },
  esAddSlot() { if(this.esSlots.length<6) this.esSlots.push({key:++this.esSlotKey,model:''}); },
  esRemoveSlot(key) { if(this.esSlots.length>1) this.esSlots=this.esSlots.filter(s=>s.key!==key); this.esSyncReference(); },
  esSyncReference() { if(!this.esModels.includes(this.esReference)) this.esReference=this.esModels[0]||''; },
  get esSetupSummary() { return (this.evalScope==='eval'?'평가용 자료':'전체 정답셋')+' · '+(this.esPreview && this.esPreview.total!=null?this.esPreview.total+'건':'건수 미확인')+' · '+(this.esModels.join(', ')||'평가 대상 미선택')+' · 현재 프롬프트 '+(this.verTxt||'버전 확인 필요')+' · '+((this.esEnvironments.find(e=>e.id===this.esEnvironmentId)||{}).name||'기본 평가')+' · 최대 '+this.esMaxCalls+'회 호출'; },
  async esInit() {
    this.esLoadPreview();this.esLoadEnvironments();await this.loadEvalRuns();
    const active=this.evalRuns.find(r=>r.kind==='evaluation'&&this.esIsActive(r));
    if(active&&!this.esRun){this.esRun=active;this.esPoll(active.id);}
  },
  async esLoadEnvironments() {
    try{const r=await this.esGet('/evaluation-environments');this.esEnvironments=r.items||[];this.esEnvironmentError='';}
    catch(e){this.esEnvironmentError='검수 환경 조회 실패: '+e.message;}
  },
  esSelectEnvironment() {
    const env=this.esEnvironments.find(e=>e.id===this.esEnvironmentId);
    if(env){this.esEnvironmentJudge=env.judge_model;this.evalJudgeModel=env.judge_model;if(env.limits?.max_calls)this.esMaxCalls=env.limits.max_calls;}
  },
  async esSaveEnvironment() {
    if(this.esEnvironmentBusy)return;this.esEnvironmentError='';
    const name=this.esEnvironmentName.trim(),judge=String(this.esEnvironmentJudge||'').split('|').pop();
    if(!name||!judge||!Number.isInteger(Number(this.esMaxCalls))||this.esMaxCalls<1||this.esMaxCalls>100000){this.esEnvironmentError='환경 이름, 판정 모델과 올바른 호출 한도를 입력하세요.';return;}
    this.esEnvironmentBusy=true;
    try{const r=await this.esPost('/evaluation-environment-save',{name,judge_model:judge,max_calls:Number(this.esMaxCalls)});await this.esLoadEnvironments();this.esEnvironmentId=r.environment.id;this.esSelectEnvironment();}
    catch(e){this.esEnvironmentError=e.message;}finally{this.esEnvironmentBusy=false;}
  },
  async esLoadPreview() {
    const seq=++this._esPreviewSeq;this.esPreviewBusy=true;this.esPreview=null;this.esPreviewError='';
    try {const r=await this.esGet('/evaluation-preview?scope='+encodeURIComponent(this.evalScope));if(seq===this._esPreviewSeq)this.esPreview=r;}
    catch(e){if(seq===this._esPreviewSeq)this.esPreviewError='자료 건수 조회 실패: '+e.message;}
    finally{if(seq===this._esPreviewSeq)this.esPreviewBusy=false;}
  },
  esIsActive(run) { return !!run && !run.stalled && ['queued','running','cancel_requested'].includes(run.status); },
  esCanResume(run) { return !!run && !!run.can_resume && !this.esIsActive(run); },
  esStatus(run) {
    if(!run) return '미실행';
    if(run.stalled) return '실행 연결 끊김 · 재개 조건 확인';
    return ({queued:'실행 대기',running:'실행 중',cancel_requested:'중단 요청됨',cancelled:'중단 완료',completed:'실행 완료',done:'실행 완료',failed:'실행 실패',limit_reached:'호출 한도 도달',stopped:'한도 도달 · 탐색 중단',insufficient_data:'표본 부족 · 판단 보류',inconclusive:'판단 보류',found:'후보 탐색 완료'})[run.status]||run.status||'상태 기록 없음';
  },
  esStage(run) { return run ? '현재 단계 · '+(({running:'결과 생성·기본 평가',proposal:'후보 생성',development:'개발 자료 비교',final:'최종 자료 평가',queued:'대기',generation:'결과 생성',evaluation:'평가',completed:'완료',candidate_generation:'후보 생성',candidate_evaluation:'후보 평가'})[run.phase||run.stage]||run.phase||run.stage||this.esStatus(run)) : ''; },
  esCost(run) {
    const cost=run && run.cost;
    const coverage=cost?.coverage?.embedding_calls==='not_requested'?' · 임베딩 미실행':'';
    if(!cost || cost.status==='unknown' || (cost.total_usd==null && cost.known_usd==null)) return '비용 미확인 · 미집계 호출이 있을 수 있습니다';
    if(cost.status==='known' && cost.total_usd!=null) return ((cost.sources||[]).length && cost.sources.every(s=>s==='synthetic')?'모의 실행 비용 $':'확인된 비용 $')+Number(cost.total_usd).toFixed(6)+' · '+(cost.calls==null?'호출 수 미확인':cost.calls+'회 호출')+coverage;
    return '일부 비용 미집계 · 확인된 금액 '+(cost.known_usd==null?'미확인':'$'+Number(cost.known_usd).toFixed(6))+' · 미집계 '+(cost.unknown_calls==null?'호출 수 미확인':cost.unknown_calls+'회 호출')+coverage;
  },
  esProgressRows(run) { return ((run&&run.model_results)||[]).map(m=>({model:m.model,success:m.completed??'미확인',failed:m.failed??'미확인',pending:m.pending??'미확인'})); },
  async esGet(url, options={}) {
    const result=await (await this._afetch(url,{headers:this._authHeaders(),...options})).json();
    if(!result || !result.ok) throw new Error(result&&result.error||'요청을 처리하지 못했습니다');
    return result;
  },
  esPost(url, body) { return this.esGet(url,{method:'POST',body:JSON.stringify(body)}); },
  async esStart() {
    if(this.esBusy || this.esActionBusy) return;
    this.esError=''; this.esSyncReference();
    if(this.esModels.length!==this.esSlots.length) { this.esError='대상 모델을 모두 선택하고 중복을 제거하세요.'; return; }
    if(!Number.isInteger(Number(this.esMaxCalls)) || this.esMaxCalls<1 || this.esMaxCalls>100000) { this.esError='호출 한도는 1~100000 사이의 정수로 입력하세요.'; return; }
    this.esBusy=true;
    const body={models:this.esModels,scope:this.evalScope,baseline_model:this.esReference,limits:{max_calls:Number(this.esMaxCalls)},...(this.esEnvironmentId?{environment_id:this.esEnvironmentId}:{})};
    // Retain a key for a transport retry of the exact same conditions.
    const signature=JSON.stringify(body);
    if(this._esRequestSignature!==signature || !this.esRequestId) { this.esRequestId='ui-'+Date.now()+'-'+Math.random().toString(36).slice(2); this._esRequestSignature=signature; }
    try {
      const r=await this.esPost('/evaluation-start',{...body,request_id:this.esRequestId});
      this.esRun=r.run||{id:r.id,status:'queued'}; await this.esPoll(r.id||this.esRun.id); this.loadEvalRuns();
    } catch(e) { this.esError=e.message; this.esBusy=false; }
  },
  async esPoll(id) {
    clearTimeout(this._esPollT);
    try {
      const r=await this.esGet('/evaluation-run?id='+encodeURIComponent(id));
      if(this.esRun && this.esRun.id!==id) return;
      this.esRun=r.run; this.esBusy=this.esIsActive(r.run); this.esError='';
      if(this.esBusy) this._esPollT=setTimeout(()=>this.esPoll(id),2500);
      else { this.esRequestId=''; this.loadEvalRuns(); }
    } catch(e) { this.esError='진행 조회 실패: '+e.message+' · 이력에서 다시 확인하세요.'; this.esBusy=false; }
  },
  async esCancel(id) {
    if(this.esActionBusy) return; this.esActionBusy=true; this.esError='';
    try { const r=await this.esPost('/evaluation-cancel',{id});
      if(!this.esRun||this.esRun.id===id){this.esRun={...(this.esRun||{}),id,status:r.status||'cancel_requested'};await this.esPoll(id);}
      else await this.loadEvalRuns();
      if(this.evalSelected?.kind==='evaluation'&&this.evalSelected.id===id) await this.openEvalHistory(this.evalSelected,true);
    }
    catch(e) { this.esError=e.message; } finally { this.esActionBusy=false; }
  },
  async esResume(id) {
    if(this.esActionBusy) return; this.esActionBusy=true; this.esError='';
    try { const r=await this.esPost('/evaluation-resume',{id}); this.esRun=r.run||{id,status:'queued'}; this.evalTab='run'; await this.esPoll(id); }
    catch(e) { this.esError=e.message; } finally { this.esActionBusy=false; }
  },
  async esRerun(id) {
    if(this.esBusy || this.esActionBusy) return;
    this.esActionBusy=true; this.esError=''; this.esCandidateError='';
    if(this._esRerunSource!==id || !this._esRerunKey) {
      this._esRerunSource=id; this._esRerunKey='rerun-'+Date.now()+'-'+Math.random().toString(36).slice(2);
    }
    try {
      const r=await this.esPost('/evaluation-rerun',{id,request_id:this._esRerunKey});
      this._esRerunKey=''; this.esRun=r.run; this.esBusy=this.esIsActive(r.run);
      this.evalTab='run'; await this.esPoll(r.id);
    } catch(e) { this.esCandidateError=e.message; }
    finally { this.esActionBusy=false; }
  },
  esOpenRun(id) { return this.openEvalHistory({kind:'evaluation',id}); },
  esNormalize(run) {
    const snapshot=run.snapshot||{};
    const models=(run.model_results||[]).map(m=>({...m,...(m.metrics||{}),model:m.model,n:(m.metrics||{}).n??m.total}));
    const documents=Object.fromEntries(((run.model_results||[])[0]?.evaluation?.documents||[]).map(d=>[d.id,d]));
    const items=(run.items||[]).map(it=>{
      const expected={...(it.expected||{}),grade:(it.expected||{}).grade||(it.expected||{}).finalGrade};
      const got=Object.fromEntries((run.models||[]).map(model=>{
        const data=(it.got||{})[model], state=(it.states||{})[model], error=(it.errors||{})[model];
        const grade=data&&(data.grade||data.finalGrade), expectedGrade=expected.grade;
        return [model,{...(data||{}),grade,ok:state==='completed'&&grade&&expectedGrade?grade===expectedGrade:null,
          empty:state==='failed',error:error||'',unprocessed:!state||['pending','queued','running'].includes(state)}];
      }));
      return {...it,expected,expected_status:it.expected_status||(documents[it.hash]||{}).expected_status||{},
        title:it.title||(it.source||{}).title||it.hash,got,split:new Set(Object.values(got).map(c=>c.grade).filter(Boolean)).size>1};
    });
    return {...run,kind:'evaluation',ts:run.created_at,model_names:run.models,models,items,detail_mode:'all',version:snapshot.prompt_version||snapshot.version||null,
      metric_version:snapshot.metric_version,paired_comparisons:(run.comparisons||[]).map(c=>({...c,a:c.baseline_model,b:c.model}))};
  },

  async openUnifiedEvalHistory(row,refresh=false) {
    clearTimeout(this._evalDetailT); clearTimeout(this._evalGroundingT);
    const seq=++this._evalDetailSeq;
    if(!refresh) { this.evalTab='results'; this.evalSelected={...row}; this.evalDetail=null; this.evalGrounding=null; this.evalGroundingError=''; this.evalItemFilter='all'; this.evalItemSearch=''; this.evalItemLimit=50; this.esCandidateForm=false; this.esCandidate=null; clearTimeout(this._esCandidateT); }
    this.evalDetailBusy=true; this.evalDetailError='';
    try {
      const r=await this.esGet('/evaluation-run?id='+encodeURIComponent(row.id));
      if(seq!==this._evalDetailSeq) return;
      this.evalDetail=this.esNormalize(r.run);
      const environment=(r.run.snapshot||{}).environment;
      if(environment?.judge_model)this.evalJudgeModel=environment.judge_model;
      if(!refresh){const limit=(r.run.snapshot||{}).limits?.max_calls||environment?.limits?.max_calls;if(limit)this.esMaxCalls=limit;}
      if(!refresh) this.esRestoreCandidate(row.id);
      if((r.run.items||[]).some(i=>i.source)) this.loadEvalGrounding(row,seq);
      if(!refresh&&this.$nextTick) this.$nextTick(()=>{ if(seq===this._evalDetailSeq&&this.$refs.evalDetailTitle) this.$refs.evalDetailTitle.focus(); });
      if(this.esIsActive(r.run)) this._evalDetailT=setTimeout(()=>{if(this.evalTab==='results') this.openUnifiedEvalHistory(row,true);},3000);
    } catch(e) { if(seq===this._evalDetailSeq) this.evalDetailError=e.message; }
    finally { if(seq===this._evalDetailSeq) this.evalDetailBusy=false; }
  },
  async loadEvalRuns() {
    const results=await Promise.allSettled([this.esGet('/evaluation-runs'),this.esGet('/eval-runs')]);
    const rows=[],errors=[];
    results.forEach((r,index)=>{if(r.status==='fulfilled') rows.push(...(r.value.items||[]).map(row=>index===0?{...row,kind:'evaluation',model_names:row.models,ts:row.created_at}:row));else errors.push((index===0?'통합':'과거')+' 이력: '+r.reason.message);});
    this.evalRuns=rows.sort((a,b)=>(Number(b.ts)||Date.parse(b.ts)||0)-(Number(a.ts)||Date.parse(a.ts)||0)); this.evalHistoryError=errors.join(' · ');
  },
  esLegacyComparisonState(comparison) {
    if(comparison.verdict==='incomparable'||comparison.paired?.comparable===false)return 'incomparable';
    const grade=comparison.paired?.metrics?.grade_accuracy;
    if(!grade||grade.n<2||['unmeasured','insufficient_sample','inconclusive'].includes(grade.conclusion))return 'hold';
    return comparison.verdict;
  },
  esLegacyComparisonLabel(comparison) {
    return ({incomparable:'비교 불가 · 조건 확인',hold:'판단 보류 · 표본과 신뢰구간 확인',regressed:'회귀 발견 · 채택 보류',improved:'개선 후보 · 검토 필요',even:'판단 보류 · 동등성 미확인'})[this.esLegacyComparisonState(comparison)]||'판단 보류';
  },
  esVerdict(run) {
    if(!run) return '';
    if(this.esIsActive(run)) return this.esStatus(run)+' · 완료 전 결론 보류';
    const comparisons=run.comparisons||[];
    const incomparable=comparisons.filter(c=>c.comparable===false||c.verdict==='incomparable');
    if(incomparable.length) return '비교 불가 · '+incomparable.map(c=>c.reason||'평가 기준 또는 자료 조건 확인 필요').join(' · ');
    if(run.kind==='evaluation') return (run.model_names||run.models||[]).length<2?'품질 현황 · 원문 근거와 검토 필요 사례를 확인하세요':'판단 보류 · 동일 사례의 차이와 유효 표본을 검토하세요. 새 기준의 우열 판정은 아직 제공하지 않습니다';
    return '과거 저장 결과 · 표본 부족·기준 불일치 시 결론을 보류하세요. 점수 순위만으로 우세를 확정하지 않습니다';
  },
  esPromptVersions(run) {
    return Object.entries((run.snapshot||{}).executions||{}).map(([model,plan])=>model+' · 메타 '+(plan.item_version||'기록 없음')+' · 품질 '+(plan.quality_version||'기록 없음')+' · 실행 코드 '+(plan.engine||'기록 없음'));
  },
  esContext(run) {
    const s=run.snapshot||{};
    if(run.kind!=='evaluation') return '과거 저장 지표와 분모를 보존합니다. 원문·정답·판정 버전 중 기록이 없는 조건은 확인할 수 없습니다.';
    const n=Array.isArray(s.rows)?s.rows.length:run.total==null?'미확인':run.total/Math.max((run.model_names||run.models||[]).length,1);
    return '자료 '+n+'개 · 모델별 처리 '+(run.total??'미확인')+'건 · 성공 '+(run.completed??'미확인')+'건 / 실패 '+(run.failed??'미확인')+'건 / 미처리 '+(run.pending??'미확인')+'건'+' · 기준 대상 '+(s.baseline_model||'기록 없음')+' · 지표 버전 '+(s.metric_version||'기록 없음')+' · 자료·정답 식별자 '+(s.dataset_fingerprint||run.basis_fingerprint||'기록 없음')+' · 프롬프트·실행 설정은 당시 스냅샷에 고정 · 검수 환경 '+(s.environment?.name||'기본 평가')+(s.environment?.version?' · 환경 버전 '+s.environment.version:'')+' · 추가진단 기본 판정 모델 '+(s.environment?.judge_model||'미지정');
  },
  get esCandidateChangeOptions() {
    const s=(this.evalDetail||{}).snapshot||{}, plan=(s.executions||{})[s.baseline_model]||{};
    const options=plan.four_calls===false?(plan.single_system?[{value:'single_system',label:'단일 호출 시스템 프롬프트'}]:[]):Object.entries(plan.calls||{}).filter(([,v])=>v.system).map(([key])=>({value:'calls.'+key+'.system',label:({summary:'리드문',entities:'엔티티',intent:'인텐트',category:'카테고리'})[key]||key}));
    return options.concat(Object.entries(plan.quality||{}).filter(([,v])=>typeof v==='string').map(([key],i)=>({value:'quality.'+key,label:'품질 판정 프롬프트 '+(i+1)})));
  },
  esFindCandidates(issue) {
    if(!this.evalDetail||this.evalDetail.kind!=='evaluation') { this.esCandidateError='통합 평가 결과를 먼저 선택하세요.'; this.evalTab='results'; return; }
    this.esCandidateForm=true; this.esCandidateError=''; this.esCandidateChanges=(this.esCandidateChangeOptions[0]||{}).value||''; if(issue) this.esCandidateGoal=issue.directive||issue.why||'';
    if(this.$nextTick) this.$nextTick(()=>{if(this.$refs.esCandidateGoal) this.$refs.esCandidateGoal.focus();});
  },
  get esCandidateSummary() { const c=this.esCandidate; return c?this.esStatus(c)+' · '+(c.phase||'')+' · 호출 '+(c.calls??'미확인')+'/'+(c.max_calls??'미확인')+' · 반복 '+(c.round??'미확인')+'/'+(c.max_rounds??'미확인')+' · '+this.esCandidateDecision(c.final_comparison||{decision:c.outcome})+' · '+this.esCost(c):''; },
  esCandidateDecision(comparison) {
    if(!comparison) return '아직 비교하지 않음';
    return ({improved:'개선 후보 · 근거 검토 필요',regression:'악화 발견 · 채택 보류',equal:'동등 · 차이 확인 필요',hold:'판단 보류',incomparable:'비교 불가'})[comparison.decision]||'판단 보류';
  },
  esCandidateCaseSummary(comparison) {
    if(!comparison) return '사례 비교 기록 없음';
    const cases=comparison.cases||[],counts={};for(const c of cases)counts[c.grade]=(counts[c.grade]||0)+1;
    return '등급 비교 · 개선 '+(counts.improved||0)+' / 악화 '+(counts.regressed||0)+' / 동등 '+(counts.equal||0)+' / 불명확 '+(counts.unknown||0)+'건 · '+((comparison.paired||{}).reason||'항목별 근거와 표본을 확인하세요');
  },
  esRestoreCandidate(sourceId) {
    try {const id=window.localStorage?.getItem('prism-evaluation-candidate:'+sourceId);if(id){this.esCandidate={id};this.esPollCandidate(id);}}catch(e){}
  },
  async esStartCandidate() {
    if(this.esCandidateBusy||this.esIsActive(this.esCandidate)) return;
    this.esCandidateError='';
    if(!this.evalSelected||this.evalSelected.kind!=='evaluation'||!this.esCandidateGoal.trim()||!this.esCandidateChangeOptions.some(o=>o.value===this.esCandidateChanges)) {this.esCandidateError='통합 평가 결과, 개선 목표와 실행 가능한 변경 범위가 필요합니다.';return;}
    if(!Number.isInteger(Number(this.esCandidateMaxCalls))||this.esCandidateMaxCalls<1||this.esCandidateMaxCalls>100000||!Number.isInteger(Number(this.esCandidateMaxRounds))||this.esCandidateMaxRounds<1||this.esCandidateMaxRounds>20) {this.esCandidateError='호출·반복 한도를 정수로 입력하세요.';return;}
    this.esCandidateBusy=true; const sourceId=this.evalSelected.id;
    try {const r=await this.esPost('/evaluation-candidate-start',{source_run_id:sourceId,goal:this.esCandidateGoal.trim(),allowed_changes:[this.esCandidateChanges],max_calls:Number(this.esCandidateMaxCalls),max_rounds:Number(this.esCandidateMaxRounds)});try{window.localStorage?.setItem('prism-evaluation-candidate:'+sourceId,r.candidate.id);}catch(e){} if(this.evalSelected?.id===sourceId){this.esCandidate=r.candidate;await this.esPollCandidate(r.candidate.id);}}
    catch(e){this.esCandidateError=e.message;}finally{this.esCandidateBusy=false;}
  },
  async esPollCandidate(id) {
    clearTimeout(this._esCandidateT);
    try {const r=await this.esGet('/evaluation-candidate?id='+encodeURIComponent(id));if(!this.esCandidate||this.esCandidate.id!==id)return;this.esCandidate=r.candidate;if(this.esIsActive(r.candidate))this._esCandidateT=setTimeout(()=>this.esPollCandidate(id),3000);}
    catch(e){this.esCandidateError='후보 조회 실패: '+e.message;}
  },
  async esCancelCandidate() {
    if(!this.esCandidate||this.esCandidateBusy)return;this.esCandidateBusy=true;
    try {const r=await this.esPost('/evaluation-candidate-cancel',{id:this.esCandidate.id});this.esCandidate=r.candidate||{...this.esCandidate,status:'cancel_requested'};await this.esPollCandidate(this.esCandidate.id);}
    catch(e){this.esCandidateError=e.message;}finally{this.esCandidateBusy=false;}
  }
}));
