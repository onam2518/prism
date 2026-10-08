/* 핵심 키워드 작업공간 · 기존 문장 실험은 app-23 그대로 유지 */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
  kwTab: 'run', kwTabs: [['run','실행·비교'], ['review','키워드 검수'], ['versions','프롬프트 버전'], ['loop','개선 루프'], ['sentence','문장 실험']],
  kwCatalog: null, kwBusy: false, kwError: '', kwMessage: '', kwRunData: null, kwRunId: '', _kwTimer: null,
  kwCount: 3, kwSlots: [{model:'',version_id:''},{model:'',version_id:''},{model:'',version_id:''}],
  kwSource: 'direct', kwSample: 3, kwHashes: '', kwReuse: '', kwTitle: '', kwLead: '', kwEntities: '', kwIntents: '', kwCategories: '', kwBlind: true,
  kwDraft: {model:'',title:'',rules:'',note:'',parent_id:'',proposal_id:''}, kwVersion: null,
  kwReview: null, kwCompiler: '', kwLoopVersion: '', kwTraining: [], kwProposal: null, kwAdoptVersion: '',
  async kwRequest(query='', data=null) {
    const r = await (await this._afetch('/lab-keywords' + query, data ? {
      method:'POST', headers:this._authHeaders(), body:JSON.stringify({...data,reviewer:this.reviewer || ''})
    } : {headers:this._authHeaders()})).json();
    if (!r || !r.ok) throw new Error((r && r.error) || '요청을 처리하지 못했습니다');
    return r;
  },
  async kwInit() {
    if (this.kwCatalog) return;
    await this.kwLoad();
  },
  async kwLoad() {
    try {
      this.kwCatalog = await this.kwRequest();
      if (!this.kwDraft.rules) this.kwDraft.rules = this.kwCatalog.default_rules;
    } catch(e) { this.kwError=e.message; }
  },
  kwChangeTab(id) {
    this.kwTab=id; this.kwError=''; this.kwMessage='';
    if(id==='sentence') this.erInit();
    else this.kwInit();
  },
  kwTabKey(event) {
    const tabs=Array.from(event.currentTarget.querySelectorAll('[role="tab"]'));
    const index=tabs.indexOf(event.target); let next=index;
    if(event.key==='ArrowRight') next=(index+1)%tabs.length;
    else if(event.key==='ArrowLeft') next=(index+tabs.length-1)%tabs.length;
    else if(event.key==='Home') next=0;
    else if(event.key==='End') next=tabs.length-1;
    else return;
    event.preventDefault(); tabs[next].click(); tabs[next].focus();
  },
  kwModel(slot, value) { slot.model=value; slot.version_id=''; },
  kwVersions(model) { return ((this.kwCatalog || {}).versions || []).filter(v=>v.model===model); },
  kwVersionLabel(v) { return 'v'+v.number+' · '+v.title; },
  kwDate(t) { return new Date(t*1000).toLocaleString(); },
  kwState(run) { return ({running:'실행 중',done:'완료',interrupted:'중단'})[run.status] || run.status; },
  kwReady() { return this.kwSlots.slice(0,this.kwCount).every(s=>s.model && s.version_id) && !this.kwBusy; },
  kwSplit(s) { return String(s || '').split(',').map(x=>x.trim()).filter(Boolean); },
  async kwStart() {
    if(!this.kwReady()) return;
    this.kwBusy=true; this.kwError=''; this.kwMessage='';
    try {
      const data={action:'start', slots:this.kwSlots.slice(0,this.kwCount).map(s=>({...s})),blind:this.kwBlind};
      if(this.kwSource==='reuse') data.source_run=this.kwReuse;
      else if(this.kwSource==='sample') data.sample=Number(this.kwSample);
      else if(this.kwSource==='hash') data.hashes=this.kwHashes.split(/[\s,]+/).filter(Boolean);
      else Object.assign(data,{title:this.kwTitle,meta:{summary:this.kwLead,entities:this.kwSplit(this.kwEntities),
        intent:this.kwSplit(this.kwIntents),content_category:this.kwSplit(this.kwCategories)}});
      const r=await this.kwRequest('',data); this.kwRunId=r.id; this.kwReview=null;
      await this.kwOpenRun(r.id); await this.kwLoad();
    } catch(e) { this.kwError=e.message; }
    finally {this.kwBusy=false;}
  },
  async kwOpenRun(id) {
    if(!id) return;
    clearTimeout(this._kwTimer); this.kwRunId=id;
    try {
      const r=await this.kwRequest('?run='+encodeURIComponent(id));
      if(this.kwRunId!==id) return;
      this.kwRunData=r.run;
      if(r.run.status==='running') this._kwTimer=setTimeout(()=>this.kwOpenRun(id),1800);
    } catch(e) {this.kwError=e.message;}
  },
  kwCell(item, slot) {return (this.kwRunData && this.kwRunData.cells[item.key+':'+slot.label]) || null;},
  kwChecks(cell) {return this.erKeywordChecks(cell);},
  kwRate(m, verdict) {return m.reviewed_keywords ? Math.round(100*m.judgments[verdict]/m.reviewed_keywords)+'%' : '미검수';},
  kwMoney(value) {return value==null ? '미집계' : '$'+Number(value).toFixed(6);},
  async kwReveal() {
    try {await this.kwRequest('',{action:'reveal',run_id:this.kwRunId});await this.kwOpenRun(this.kwRunId);}
    catch(e){this.kwError=e.message;}
  },
  async kwViewVersion(id) {
    if(!id) return;
    try {this.kwVersion=(await this.kwRequest('?version='+encodeURIComponent(id))).version;}
    catch(e){this.kwError=e.message;}
  },
  kwCloneVersion() {
    if(!this.kwVersion) return;
    const v=this.kwVersion;
    this.kwDraft={model:v.model,title:v.title+' 개선',rules:v.rules,note:'',parent_id:v.id,proposal_id:''};
    this.kwMessage='복제했습니다 · 변경 후 새 버전으로 저장하세요';
  },
  async kwSaveVersion() {
    if(this.kwBusy) return;
    this.kwBusy=true;this.kwError='';
    try {
      const r=await this.kwRequest('',{action:'version',...this.kwDraft});
      this.kwVersion=r.version; await this.kwLoad();this.kwMessage=this.kwVersionLabel(r.version)+' 저장 완료';
      this.kwDraft={model:r.version.model,title:'',rules:r.version.rules,note:'',parent_id:r.version.id,proposal_id:''};
    } catch(e){this.kwError=e.message;} finally{this.kwBusy=false;}
  },
  kwOpenReview(item, slot) {
    const cell=this.kwCell(item,slot); if(!cell || cell.status!=='done') return;
    const latest=(cell.reviews || []).slice(-1)[0];
    this.kwReview={item,slot:slot.label,cell_id:cell.id,expected_revision:cell.review_revision,
      judgments:((cell.refined || {}).keywords || []).map((k,i)=>({original:k.text,kind:k.kind,
        verdict:latest ? latest.judgments[i].verdict : '',reason:latest ? latest.judgments[i].reason : '',
        corrected:latest ? latest.judgments[i].corrected : k.text})),
      additions:latest ? JSON.parse(JSON.stringify(latest.additions)) : [],
      no_keywords:latest ? latest.no_keywords : false, note:latest ? latest.note : '',
      partition:(cell.final || {}).partition || 'development',final:cell.final || null};
    this.kwChangeTab('review');
  },
  async kwSaveReview(finalize=false) {
    if(this.kwBusy || !this.kwReview) return;
    this.kwBusy=true;this.kwError='';
    try {
      const q=this.kwReview;
      await this.kwRequest('',{action:'review',run_id:this.kwRunId,cell_id:q.cell_id,
        expected_revision:q.expected_revision,judgments:q.judgments,additions:q.additions,
        no_keywords:q.no_keywords,note:q.note,partition:q.partition,finalize});
      await this.kwOpenRun(this.kwRunId);
      const cell=this.kwRunData.cells[q.cell_id];q.expected_revision=cell.review_revision;q.final=cell.final;
      this.kwMessage=finalize ? '최종 확정했습니다 · 정답셋 반영 여부를 선택하세요' : '키워드별 판단을 저장했습니다';
    }catch(e){this.kwError=e.message;}finally{this.kwBusy=false;}
  },
  kwCurrentGold() {return ((this.kwCatalog || {}).gold || []).find(g=>this.kwReview && g.item_key===this.kwReview.item.key);},
  async kwSaveGold() {
    if(this.kwBusy || !this.kwReview) return;
    this.kwBusy=true;this.kwError='';
    try {
      await this.kwRequest('',{action:'gold',run_id:this.kwRunId,cell_id:this.kwReview.cell_id,
        expected_gold_id:(this.kwCurrentGold() || {}).id || ''});
      await this.kwLoad();this.kwMessage='키워드 정답셋에 반영했습니다';
    }catch(e){this.kwError=e.message;}finally{this.kwBusy=false;}
  },
  kwDevelopmentGold() {return ((this.kwCatalog || {}).gold || []).filter(g=>g.partition==='development');},
  async kwCompile() {
    if(this.kwBusy) return;
    this.kwBusy=true;this.kwError='';this.kwProposal=null;
    try {this.kwProposal=(await this.kwRequest('',{action:'compile',version_id:this.kwLoopVersion,
      compiler_model:this.kwCompiler,gold_keys:this.kwTraining})).proposal;}
    catch(e){this.kwError=e.message;}finally{this.kwBusy=false;}
  },
  kwUseProposal() {
    const p=this.kwProposal;if(!p)return;
    this.kwDraft={model:p.model,title:'검수 기반 개선',rules:p.rules,note:p.changes.map(c=>c.reason+': '+c.change).join('\n'),parent_id:p.parent_id,proposal_id:p.id};
    this.kwChangeTab('versions');this.kwMessage='개선안을 확인하고 새 버전으로 저장하세요';
  },
  async kwAdopt() {
    if(this.kwBusy)return;this.kwBusy=true;this.kwError='';
    try {await this.kwRequest('',{action:'adopt',run_id:this.kwRunId,version_id:this.kwAdoptVersion});
      await this.kwLoad();this.kwMessage='기본 사용 후보로 채택했습니다 · 실행 시 모델·버전 선택은 유지됩니다';}
    catch(e){this.kwError=e.message;}finally{this.kwBusy=false;}
  }
}));
