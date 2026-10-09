/* 키워드·문장 공통 실험 · 결과 검수 · 프롬프트 관리 */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
  kwTab: 'run', kwTabs: [['run','실행·비교'], ['prompts','프롬프트 관리']],
  kwTarget:'keyword', kwKeywordSource:'manual', kwSentenceKeywords:'', kwKeywordSlot:'', kwInputRun:null, kwInputLoading:false,
  kwCatalog: null, kwBusy: false, kwError: '', kwMessage: '', kwRunData: null, kwRunId: '', _kwTimer: null,
  kwMode: '', kwCount: 1, kwDraftSlot: null, kwSlots: [{model:'',version_id:''},{model:'',version_id:''},{model:'',version_id:''}],
  kwSource: 'direct', kwSample: 3, kwHashes: '', kwReuse: '', kwTitle: '', kwLead: '', kwEntities: '', kwIntents: '', kwCategories: '', kwBlind: true,
  kwDraft: {model:'',title:'',rules:'',note:'',parent_id:'',proposal_id:''}, kwVersion: null,
  kwReview: null, kwReviewView: 'meta', kwSideOpen: {ents:false,rej:false}, kwAutoNext: true, kwCompiler: '', kwLoopVersion: '', kwTraining: [], kwProposal: null, kwAdoptVersion: '',
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
      if (!this.kwDraft.rules) this.kwDraft.rules = this.kwDefaultRules();
    } catch(e) { this.kwError=e.message; }
  },
  kwChangeTab(id) {
    this.kwTab=id; this.kwError=''; this.kwMessage='';
    this.kwInit();
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
  kwTargetLabel(target=this.kwTarget) {return target==='sentence' ? '문장' : '키워드';},
  kwDefaultRules() {return this.kwCatalog?.rules_by_target?.[this.kwTarget] || (this.kwTarget==='keyword' ? this.kwCatalog?.default_rules : '') || '';},
  kwTargetChange(target) {
    this.kwTarget=target; clearTimeout(this._kwTimer); this.kwRunId='';this.kwRunData=null;this.kwReview=null;
    this.kwSlots.forEach(s=>s.version_id='');this.kwVersion=null;this.kwDraftSlot=null;
    this.kwDraft={target,model:'',title:'',rules:this.kwDefaultRules(),note:'',parent_id:'',proposal_id:''};
    this.kwLoopVersion='';this.kwTraining=[];this.kwProposal=null;this.kwAdoptVersion='';
    this.kwSource='direct';this.kwReuse='';this.kwKeywordSource='manual';this.kwKeywordSlot='';this.kwInputRun=null;
    this.kwError='';this.kwMessage='';
  },
  kwAllVersions() {return (this.kwCatalog?.versions || []).filter(v=>(v.target || 'keyword')===this.kwTarget);},
  kwRuns() {return (this.kwCatalog?.runs || []).filter(r=>(r.target || 'keyword')===this.kwTarget);},
  kwAnswers(g) {return g?.target==='sentence' ? (g.sentences || []) : (g?.keywords || []);},
  kwCandidates(cell) {
    if(cell?.sentence) {const text=cell.sentence.text || cell.sentence.draft;return text ? [{text,kind:'sentence'}] : [];}
    return cell?.refined?.keywords || [];
  },
  kwReviewable(cell) {return cell && (cell.status==='done' || Boolean(cell.sentence?.draft));},
  kwSourceChanged() {
    this.kwReuse='';this.kwInputRun=null;this.kwKeywordSlot='';
    this.kwKeywordSource=this.kwSource==='direct' ? 'manual' : this.kwSource==='reuse' ? 'run' : 'gold';
  },
  async kwLoadInputRun(id) {
    this.kwInputRun=null;this.kwKeywordSlot='';if(!id)return;
    this.kwInputLoading=true;
    try {const r=await this.kwRequest('?run='+encodeURIComponent(id));if(this.kwReuse===id)this.kwInputRun=r.run;}
    catch(e){this.kwError=e.message;}finally{this.kwInputLoading=false;}
  },
  kwModel(slot, value) { slot.model=value; slot.version_id=''; },
  kwVersions(model) { return this.kwAllVersions().filter(v=>v.model===model); },
  kwVersionLabel(v) { return 'v'+v.number+' · '+v.title; },
  kwDate(t) { return new Date(t*1000).toLocaleString(); },
  kwState(run) { return ({running:'실행 중',done:'완료',interrupted:'중단'})[run.status] || run.status; },
  kwChooseMode(mode) {
    this.kwMode=mode; this.kwCount=mode==='single' ? 1 : 2;
    this.kwError='';
  },
  kwReusableRuns() {return ((this.kwCatalog || {}).runs || []).filter(r=>(this.kwTarget==='sentence' || (r.target || 'keyword')==='keyword'));},
  kwInputIssue() {
    if(!['single','compare'].includes(this.kwMode)) return '테스트 방식을 먼저 선택하세요';
    if((this.kwMode==='single' && this.kwCount!==1) || (this.kwMode==='compare' && ![2,3].includes(this.kwCount))) return '비교 조합 수를 확인하세요';
    const slots=this.kwSlots.slice(0,this.kwCount);
    if(slots.some(s=>!s.model || !s.version_id)) return this.kwMode==='single' ? '모델과 프롬프트 버전을 선택하세요' : '각 조합의 모델과 프롬프트 버전을 선택하세요';
    if(new Set(slots.map(s=>s.version_id)).size!==slots.length) return '서로 다른 모델·프롬프트 조합을 선택하세요';
    if(this.kwSource==='direct' && (!this.kwLead.trim() || (this.kwTarget==='keyword' && !this.kwSplit(this.kwEntities).length))) return this.kwTarget==='sentence' ? '리드문을 입력하세요' : '리드문과 엔티티를 입력하세요';
    if(this.kwSource==='sample' && (!Number.isInteger(this.kwSample) || this.kwSample<1 || this.kwSample>30)) return '샘플 수는 1~30건으로 입력하세요';
    if(this.kwSource==='hash') {
      const hashes=this.kwHashes.split(/[\s,]+/).filter(Boolean);
      if(!hashes.length || hashes.length>30 || new Set(hashes).size!==hashes.length) return '중복 없이 콘텐츠 해시를 1~30개 입력하세요';
    }
    if(this.kwSource==='reuse' && !this.kwReusableRuns().some(r=>r.id===this.kwReuse)) return '입력을 재사용할 실험을 선택하세요';
    if(this.kwTarget==='sentence') {
      if(this.kwInputLoading)return '입력 실험을 불러오는 중입니다';
      if(this.kwSource==='reuse' && this.kwInputRun?.target==='sentence') return '';
      if(this.kwKeywordSource==='manual' && (this.kwSource!=='direct' || !this.kwSplit(this.kwSentenceKeywords).length || this.kwSplit(this.kwSentenceKeywords).length>3)) return '입력 키워드를 1~3개 지정하세요';
      if(this.kwKeywordSource==='run' && (!this.kwInputRun?.revealed || !this.kwKeywordSlot)) return '공개된 키워드 실험과 사용할 결과 조합을 선택하세요';
    }
    return '';
  },
  kwReady() { return !this.kwInputIssue() && !this.kwBusy; },
  kwCreateVersion(index) {
    const slot=this.kwSlots[index]; this.kwDraftSlot=index;
    this.kwDraft={target:this.kwTarget,model:slot.model,title:'',rules:this.kwDefaultRules(),note:'',parent_id:'',proposal_id:''};
    this.kwChangeTab('prompts');
  },
  kwSplit(s) { return String(s || '').split(',').map(x=>x.trim()).filter(Boolean); },
  async kwStart() {
    if(!this.kwReady()) return;
    this.kwBusy=true; this.kwError=''; this.kwMessage='';
    try {
      const data={action:'start',target:this.kwTarget, slots:this.kwSlots.slice(0,this.kwCount).map(s=>({...s})),blind:this.kwMode==='compare' && this.kwBlind};
      if(this.kwTarget==='sentence') Object.assign(data,{keyword_source:this.kwKeywordSource,keyword_slot:this.kwKeywordSlot,keywords:this.kwSplit(this.kwSentenceKeywords)});
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
    clearTimeout(this._kwTimer); this.kwRunId=id;
    if(!id) {this.kwRunData=null;this.kwReview=null;return;}
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
    const v=this.kwVersion; this.kwDraftSlot=null;
    this.kwDraft={target:v.target || 'keyword',model:v.model,title:v.title+' 개선',rules:v.rules,note:'',parent_id:v.id,proposal_id:''};
    this.kwMessage='복제했습니다 · 변경 후 새 버전으로 저장하세요';
  },
  kwAdopted(v) {return Object.values(this.kwCatalog?.active || {}).some(a=>a.version_id===v?.id);},
  async kwDeleteVersion() {
    const v=this.kwVersion;
    if(!v || this.kwBusy || !window.confirm(v.model+' · '+this.kwVersionLabel(v)+' 버전을 삭제합니다.\n실행한 실험 기록·정답셋은 유지되며 되돌릴 수 없습니다.')) return;
    this.kwBusy=true;this.kwError='';
    try {
      await this.kwRequest('',{action:'delete',version_id:v.id});
      // 삭제한 버전을 가리키던 선택값을 비운다(다음 요청이 '버전 없음'으로 실패하지 않게).
      this.kwSlots.forEach(s=>{if(s.version_id===v.id) s.version_id='';});
      if(this.kwDraft.parent_id===v.id) this.kwDraft.parent_id='';
      if(this.kwLoopVersion===v.id) {this.kwLoopVersion='';this.kwProposal=null;}
      if(this.kwAdoptVersion===v.id) this.kwAdoptVersion='';
      this.kwVersion=null; await this.kwLoad(); this.kwMessage=this.kwVersionLabel(v)+' 삭제 완료';
    } catch(e){this.kwError=e.message;} finally{this.kwBusy=false;}
  },
  async kwSaveVersion() {
    if(this.kwBusy) return;
    this.kwBusy=true;this.kwError='';
    try {
      const r=await this.kwRequest('',{action:'version',target:this.kwTarget,...this.kwDraft});
      this.kwVersion=r.version; await this.kwLoad();this.kwMessage=this.kwVersionLabel(r.version)+' 저장 완료';
      this.kwDraft={target:r.version.target || 'keyword',model:r.version.model,title:'',rules:r.version.rules,note:'',parent_id:r.version.id,proposal_id:''};
      if(this.kwDraftSlot!=null && this.kwSlots[this.kwDraftSlot].model===r.version.model) {
        this.kwSlots[this.kwDraftSlot].version_id=r.version.id; this.kwChangeTab('run');
        this.kwMessage=this.kwVersionLabel(r.version)+' 저장 완료 · 해당 조합에 선택했습니다';
      }
      this.kwDraftSlot=null;
    } catch(e){this.kwError=e.message;} finally{this.kwBusy=false;}
  },
  kwOpenReview(item, slot) {
    const cell=this.kwCell(item,slot); if(!this.kwReviewable(cell)) return;
    const latest=(cell.reviews || []).slice(-1)[0];
    this.kwReview={target:this.kwRunData.target || 'keyword',item,slot:slot.label,cell_id:cell.id,expected_revision:cell.review_revision,
      judgments:this.kwCandidates(cell).map((k,i)=>({original:k.text,kind:k.kind,
        verdict:latest ? latest.judgments[i].verdict : '',reason:latest ? latest.judgments[i].reason : '',
        corrected:latest ? latest.judgments[i].corrected : k.text})),
      additions:latest ? JSON.parse(JSON.stringify(latest.additions)) : [],
      no_keywords:latest ? latest.no_keywords : false, note:latest ? latest.note : '',
      partition:(cell.final || {}).partition || 'development',final:cell.final || null};
    this.kwChangeTab('run'); this.kwSideOpen={ents:false,rej:false};
  },
  // 상세 검수(detailview) 보조: 현재 셀 · 콘텐츠 이동 · 조합 상태 · 단축키
  kwReviewCell() {return this.kwReview && this.kwRunData ? this.kwRunData.cells[this.kwReview.cell_id] || null : null;},
  kwReviewIndex() {return this.kwReview ? (this.kwRunData?.items || []).findIndex(it=>it.key===this.kwReview.item.key) : -1;},
  kwReviewGo(step) {
    const items=this.kwRunData?.items || [], slots=this.kwRunData?.slots || [];
    for(let i=this.kwReviewIndex()+step;i>=0 && i<items.length;i+=step) {
      const same=slots.find(s=>s.label===this.kwReview.slot);
      const slot=[same,...slots].find(s=>s && this.kwReviewable(this.kwCell(items[i],s)));
      if(slot) {this.kwOpenReview(items[i],slot);return true;}
    }
    return false;
  },
  kwNextPending() {
    const items=this.kwRunData?.items || [], slots=this.kwRunData?.slots || [];
    const pairs=items.flatMap(it=>slots.map(s=>[it,s]));
    const at=pairs.findIndex(([it,s])=>it.key===this.kwReview.item.key && s.label===this.kwReview.slot);
    const next=[...pairs.slice(at+1),...pairs.slice(0,at)].find(([it,s])=>{const c=this.kwCell(it,s);return this.kwReviewable(c) && !(c.reviews || []).length;});
    if(next) this.kwOpenReview(next[0],next[1]);
    return Boolean(next);
  },
  kwReviewKey(e) {
    if(!this.kwReview || this.kwTab!=='run' || ['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)) return;
    if(e.key==='Escape') this.kwReview=null;
    else if(e.key==='ArrowRight') this.kwReviewGo(1);
    else if(e.key==='ArrowLeft') this.kwReviewGo(-1);
  },
  kwSlotState(cell) {
    if(!cell) return '대기';
    if(!this.kwReviewable(cell)) return '실패';
    return cell.final ? '확정' : (cell.reviews || []).length ? '판정됨' : '미검수';
  },
  // 실행 결과 CSV: 인증 GET 이라 fetch+Blob(exportDash 와 같은 이유) · 오류면 JSON 이 오므로 형식으로 구분
  async kwDeleteRun() {
    const r=this.kwRuns().find(x=>x.id===this.kwRunId);
    if(!r || this.kwBusy || !window.confirm(this.kwDate(r.created_at)+' 실험 기록(콘텐츠 '+r.n+'건)을 삭제합니다.\n검수 판단도 함께 사라지며 되돌릴 수 없습니다.')) return;
    this.kwBusy=true;this.kwError='';
    try {
      await this.kwRequest('',{action:'delete_run',run_id:r.id});
      clearTimeout(this._kwTimer);this.kwRunId='';this.kwRunData=null;this.kwReview=null;
      await this.kwLoad();this.kwMessage='실험 기록을 삭제했습니다';
    } catch(e){this.kwError=e.message;} finally{this.kwBusy=false;}
  },
  async kwDownloadCsv() {
    try {
      const r=await this._afetch('/lab-keywords?csv=1&run='+encodeURIComponent(this.kwRunId),{headers:this._authHeaders()});
      if(!(r.headers.get('content-type') || '').includes('text/csv')) throw new Error((await r.json()).error || 'CSV를 만들지 못했습니다');
      const u=URL.createObjectURL(await r.blob()), a=document.createElement('a');
      a.href=u; a.download='prism_keyword_'+this.kwRunId.slice(0,8)+'.csv'; a.click(); setTimeout(()=>URL.revokeObjectURL(u),60000);
    } catch(e) {this.kwError=e.message;}
  },
  kwSourceUrl() {return (this.kwReview && this.kwRunData?.sources?.[this.kwReview.item.hash]) || '';},
  kwSlotBadge(cell) {return ({'확정':'ds-badge--success','판정됨':'ds-badge--intent','실패':'ds-badge--error'})[this.kwSlotState(cell)] || 'ds-badge--neutral';},
  // 콘텐츠 한 건의 목록 상태: 판정할 조합이 남았으면 미검수/검수 중 · 모두 확정이면 확정
  kwItemState(item) {
    const cells=(this.kwRunData?.slots || []).map(s=>this.kwCell(item,s)), ok=cells.filter(c=>this.kwReviewable(c));
    if(cells.some(c=>!c)) return ['대기','ds-badge--neutral'];
    if(!ok.length) return ['실패','ds-badge--error'];
    if(ok.every(c=>c.final)) return ['확정','ds-badge--success'];
    if(ok.every(c=>(c.reviews || []).length)) return ['판정됨','ds-badge--intent'];
    return ok.some(c=>(c.reviews || []).length) ? ['검수 중','ds-badge--warning'] : ['미검수','ds-badge--neutral'];
  },
  kwOpenItem(item) {
    const slots=this.kwRunData?.slots || [], ok=slots.filter(s=>this.kwReviewable(this.kwCell(item,s)));
    const slot=ok.find(s=>!(this.kwCell(item,s).reviews || []).length) || ok[0];
    if(slot) this.kwOpenReview(item,slot);
  },
  kwMetaList(v) {return Array.isArray(v) ? v.map(x=>typeof x==='string' ? x : (x && x.name) || '').filter(Boolean) : [];},
  kwInKeywords(name) {return this.kwCandidates(this.kwReviewCell()).some(k=>k.text.includes(name));},
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
      this.kwMessage=finalize ? '최종 확정했습니다 · 정답셋 반영 여부를 선택하세요' : '판단을 저장했습니다';
      if(!finalize && this.kwAutoNext && this.kwNextPending()) this.kwMessage='판단을 저장했습니다 · 다음 미검수 결과입니다';
    }catch(e){this.kwError=e.message;}finally{this.kwBusy=false;}
  },
  kwCurrentGold() {return ((this.kwCatalog || {}).gold || []).find(g=>this.kwReview && g.item_key===this.kwReview.item.key);},
  async kwSaveGold() {
    if(this.kwBusy || !this.kwReview) return;
    this.kwBusy=true;this.kwError='';
    try {
      await this.kwRequest('',{action:'gold',run_id:this.kwRunId,cell_id:this.kwReview.cell_id,
        expected_gold_id:(this.kwCurrentGold() || {}).id || ''});
      await this.kwLoad();this.kwMessage=this.kwTargetLabel()+' 정답셋에 반영했습니다';
    }catch(e){this.kwError=e.message;}finally{this.kwBusy=false;}
  },
  kwDevelopmentGold() {return ((this.kwCatalog || {}).gold || []).filter(g=>g.partition==='development' && (g.target || 'keyword')===this.kwTarget);},
  async kwCompile() {
    if(this.kwBusy) return;
    this.kwBusy=true;this.kwError='';this.kwProposal=null;
    try {this.kwProposal=(await this.kwRequest('',{action:'compile',version_id:this.kwLoopVersion,
      compiler_model:this.kwCompiler,gold_keys:this.kwTraining})).proposal;}
    catch(e){this.kwError=e.message;}finally{this.kwBusy=false;}
  },
  kwUseProposal() {
    const p=this.kwProposal;if(!p)return;this.kwDraftSlot=null;
    this.kwDraft={target:p.target || 'keyword',model:p.model,title:'검수 기반 개선',rules:p.rules,note:p.changes.map(c=>c.reason+': '+c.change).join('\n'),parent_id:p.parent_id,proposal_id:p.id};
    this.kwChangeTab('prompts');this.kwMessage='개선안을 확인하고 새 버전으로 저장하세요';
  },
  async kwAdopt() {
    if(this.kwBusy)return;this.kwBusy=true;this.kwError='';
    try {await this.kwRequest('',{action:'adopt',run_id:this.kwRunId,version_id:this.kwAdoptVersion});
      await this.kwLoad();this.kwMessage='기본 사용 후보로 채택했습니다 · 실행 시 모델·버전 선택은 유지됩니다';}
    catch(e){this.kwError=e.message;}finally{this.kwBusy=false;}
  }
}));
