/* 키워드·문장 공통 실험 · 결과 검수 · 프롬프트 관리 */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
  kwTab: 'run', kwTabs: [['run','실행·비교'], ['improve','프롬프트 개선'], ['prompts','프롬프트 관리']], kwImproving: '',
  kwTarget:'keyword', kwKeywordSource:'manual', kwSentenceKeywords:'', kwKeywordSlot:'', kwInputRun:null, kwInputLoading:false,
  kwCatalog: null, kwBusy: false, kwError: '', kwMessage: '', kwRunData: null, kwRunId: '', _kwTimer: null,
  kwMode: '', kwCount: 1, kwDraftSlot: null, kwSlots: [{model:'',version_id:''},{model:'',version_id:''},{model:'',version_id:''}],
  kwSource: 'direct', kwExcel: null, kwXlsDrag: false, kwSample: 3, kwHashes: '', kwReuse: '', kwTitle: '', kwLead: '', kwEntities: '', kwIntents: '', kwCategories: '', kwBlind: true,
  kwDraft: {model:'',title:'',rules:'',note:'',parent_id:'',proposal_id:''}, kwVersion: null,
  kwReview: null, _kwSavedReview: '', kwReviewView: 'meta', kwSideOpen: {ents:false,rej:false}, kwAutoNext: false, kwCompiler: '', kwLoopVersion: '', kwTraining: [], kwEvaluation: [], kwTuneRounds: 3, _kwTuneTimer: null, kwProposal: null, kwImproveMode: 'once', kwPickVersion: '',
  async kwRequest(query='', data=null) {
    const r = await (await this._afetch('/lab-keywords' + query, data ? {
      method:'POST', headers:this._authHeaders(), body:JSON.stringify({...data,reviewer:this.reviewer || ''})
    } : {headers:this._authHeaders()})).json();
    if (!r || !r.ok) {const error=new Error((r && r.error) || '요청을 처리하지 못했습니다');error.rejected=true;throw error;}
    return r;
  },
  async kwInit() {
    if (this.kwCatalog && this.kwCatalog.tuning?.status!=='running') return;
    await this.kwLoad();
  },
  async kwLoad() {
    try {
      this.kwCatalog = await this.kwRequest();
      if (!this.kwDraft.rules) this.kwDraft.rules = this.kwDefaultRules();
      clearTimeout(this._kwTuneTimer);
      if(this.kwCatalog.tuning?.status==='running') this._kwTuneTimer=setTimeout(()=>{if(this.mod==='lab') this.kwLoad();},2000);
    } catch(e) { this.kwError=e.message; }
  },
  kwChangeTab(id) {
    if(id!==this.kwTab && !this.kwCanLeaveReview()) return;
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
    if(!this.kwCanLeaveReview()) return;
    this.kwTarget=target; clearTimeout(this._kwTimer); this.kwRunId='';this.kwRunData=null;this.kwReview=null;
    this.kwSlots.forEach(s=>s.version_id='');this.kwVersion=null;this.kwDraftSlot=null;
    this.kwDraft={target,model:'',title:'',rules:this.kwDefaultRules(),note:'',parent_id:'',proposal_id:''};
    this.kwLoopVersion='';this.kwTraining=[];this.kwEvaluation=[];this.kwProposal=null;this.kwPickVersion='';
    this.kwSource='direct';this.kwExcel=null;this.kwReuse='';this.kwKeywordSource='manual';this.kwKeywordSlot='';this.kwInputRun=null;
    this.kwError='';this.kwMessage='';
  },
  kwAllVersions() {return (this.kwCatalog?.versions || []).filter(v=>(v.target || 'keyword')===this.kwTarget && !v.draft);},   // 초안(저장 전 후보)은 숨김
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
    if(this.kwSource==='excel' && !this.kwExcel?.rows?.length) return '엑셀 파일을 올려 반영할 콘텐츠를 확인하세요';
    if(this.kwSource==='sample' && (!Number.isInteger(this.kwSample) || this.kwSample<1 || this.kwSample>100)) return '샘플 수는 1~100건으로 입력하세요';
    if(this.kwSource==='hash') {
      const hashes=this.kwHashes.split(/[\s,]+/).filter(Boolean);
      if(!hashes.length || hashes.length>100 || new Set(hashes).size!==hashes.length) return '중복 없이 콘텐츠 해시를 1~100개 입력하세요';
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
    if(this.kwBusy || !this.kwCatalog?.can_manage) return;
    const slot=this.kwSlots[index]; this.kwDraftSlot=index;
    this.kwVersion=null;
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
      else if(this.kwSource==='excel') data.rows=this.kwExcel.rows.map(r=>({title:r.title || '엑셀 '+r.row+'행',meta:r.meta}));
      else Object.assign(data,{title:this.kwTitle,meta:{summary:this.kwLead,entities:this.kwSplit(this.kwEntities),
        intent:this.kwSplit(this.kwIntents),content_category:this.kwSplit(this.kwCategories)}});
      const r=await this.kwRequest('',data); this.kwRunId=r.id; this.kwReview=null;
      await this.kwOpenRun(r.id); await this.kwLoad();
    } catch(e) { this.kwError=e.message; }
    finally {this.kwBusy=false;}
  },
  async kwOpenRun(id) {
    if(id!==this.kwRunId) {
      if(!this.kwCanLeaveReview()) return false;
      this.kwReview=null;
    }
    clearTimeout(this._kwTimer); this.kwRunId=id;
    if(!id) {this.kwRunData=null;this.kwReview=null;return;}
    try {
      const r=await this.kwRequest('?run='+encodeURIComponent(id));
      if(this.kwRunId!==id) return false;
      if(this.kwRunData?.id===id && r.run.revision<this.kwRunData.revision) return true;
      this.kwRunData=r.run;
      if(r.run.status==='running') this._kwTimer=setTimeout(()=>this.kwOpenRun(id),r.run.items.length>30 ? 5000 : 1800);   // 큰 실험은 기록이 커서 덜 자주
      return true;
    } catch(e) {this.kwError=e.message;return false;}
  },
  kwCell(item, slot) {return (this.kwRunData && this.kwRunData.cells[item.key+':'+slot.label]) || null;},
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
      if(this.kwDraftSlot!=null) {
        this.kwSlots[this.kwDraftSlot].model=r.version.model;
        this.kwSlots[this.kwDraftSlot].version_id=r.version.id; this.kwChangeTab('run');
        this.kwMessage=this.kwVersionLabel(r.version)+' 저장 완료 · 해당 조합에 선택했습니다';
      }
      this.kwDraftSlot=null;
    } catch(e){this.kwError=e.message;} finally{this.kwBusy=false;}
  },
  kwPriorReviews(item) {return this.kwRunData?.prior_reviews?.[item.key] || [];},
  async kwEnterReview(item, slot) {
    if(this.kwBusy || !this.kwCanLeaveReview()) return false;
    const rid=this.kwRunId, entry=(this._kwEntry || 0)+1;this._kwEntry=entry;
    if(!await this.kwOpenRun(rid) || this.kwRunId!==rid || this._kwEntry!==entry) return false;
    const current=this.kwRunData.items.find(i=>i.key===item.key);
    if(!current || !this.kwOpenReview(current,slot)) return false;
    this.kwReview.readOnly=Boolean(this.kwPriorReviews(current).length || this.kwReviewCell()?.reviews?.length);
    return true;
  },
  async kwEnableReview() {
    const q=this.kwReview;
    if(!q || this.kwBusy) return;
    const before=JSON.stringify(this.kwPriorReviews(q.item));
    if(!await this.kwEnterReview(q.item,{label:q.slot})) return;
    if(before!==JSON.stringify(this.kwPriorReviews(q.item))) {
      this.kwReviewError('새로 저장된 검수가 있습니다. 기존 결과를 확인한 뒤 검수를 시작하세요.');
      return;
    }
    this.kwReview.readOnly=false;
    this.kwError='';this.kwMessage='기존 검수를 확인했습니다 · 다른 검수자와 중복 작업하지 않도록 담당자를 확인하세요';
  },
  async kwViewPriorReview(prior) {
    if(this.kwBusy || !this.kwCanLeaveReview()) return;
    if(!await this.kwOpenRun(prior.run_id)) return;
    const cell=this.kwRunData.cells[prior.cell_id];
    const item=this.kwRunData.items.find(i=>i.key===cell?.item_key);
    if(item && this.kwOpenReview(item,{label:prior.slot})) this.kwReview.readOnly=true;
  },
  kwOpenReview(item, slot) {
    if(!this.kwCanLeaveReview()) return false;
    const cell=this.kwCell(item,slot); if(!this.kwReviewable(cell)) return;
    const latest=(cell.reviews || []).slice(-1)[0];
    this.kwReview={target:this.kwRunData.target || 'keyword',item,slot:slot.label,cell_id:cell.id,expected_revision:cell.review_revision,
      judgments:this.kwCandidates(cell).map((k,i)=>({original:k.text,kind:k.kind,
        verdict:latest ? latest.judgments[i].verdict : '',reason:latest ? latest.judgments[i].reason : '',
        corrected:latest ? latest.judgments[i].corrected : k.text})),
      additions:latest ? JSON.parse(JSON.stringify(latest.additions)) : [],
      no_keywords:latest ? latest.no_keywords : false, note:latest ? latest.note : '',
      partition:(cell.final || latest || {}).partition || '',final:cell.final || null,completionRequest:null};
    this._kwSavedReview=this.kwReviewSnapshot();
    this.kwChangeTab('run'); this.kwSideOpen={ents:false,rej:false};
    return true;
  },
  kwReviewSnapshot() {
    if(!this.kwReview) return '';
    const {judgments,additions,no_keywords,note,partition}=this.kwReview;
    return JSON.stringify({judgments,additions,no_keywords,note,partition});
  },
  kwReviewDirty() {return Boolean(this.kwReview && this.kwReviewSnapshot()!==this._kwSavedReview);},
  kwReviewError(message) {
    this.kwError=message;this.kwMessage='';
    this.$nextTick?.(()=>document.getElementById('kw-review-status')?.focus());
  },
  kwCanLeaveReview() {
    if(!this.kwReview) return true;
    if(this.kwBusy) return false;
    if(this.kwReviewDirty()) {
      this.kwReviewError('저장하지 않은 검수가 있습니다. 검수 완료·정답셋 저장 또는 임시 저장을 눌러 주세요.');
      return false;
    }
    return true;
  },
  kwCloseReview() {if(this.kwCanLeaveReview()) this.kwReview=null;},
  async kwDiscardReview() {
    if(this.kwBusy || !this.kwReviewDirty() || !window.confirm('저장하지 않은 입력을 취소할까요? 마지막으로 저장된 판단은 유지됩니다.')) return;
    this.kwReview=null;this.kwError='';this.kwMessage='';
    await this.kwOpenRun(this.kwRunId);
  },
  kwBeforeUnload(event) {
    if(this.kwReviewDirty() || (this.kwBusy && this.kwReview)) {event.preventDefault();event.returnValue='';}
  },
  kwReviewMissing() {
    const q=this.kwReview, label=this.kwTargetLabel(q.target);
    for(const [i,j] of q.judgments.entries()) {
      if(!j.verdict) return (i+1)+'번째 '+label+'의 판단을 선택하세요.';
      if(j.verdict!=='accept' && !j.reason.trim()) return (i+1)+'번째 '+label+'의 사유를 선택하세요.';
      if(j.verdict==='edit' && !j.corrected.trim()) return (i+1)+'번째 '+label+'의 수정 내용을 입력하세요.';
    }
    for(const [i,a] of q.additions.entries()) {
      if(!a.text.trim() || !a.reason.trim()) return (i+1)+'번째 추가 키워드와 사유를 입력하세요.';
    }
    if(!q.no_keywords && !q.additions.length && !q.judgments.some(j=>['accept','edit','hold'].includes(j.verdict)))
      return '적합한 '+label+'이 없는지 확인하고 해당 항목을 선택하세요.';
    return '';
  },
  // 상세 검수(detailview) 보조: 현재 셀 · 콘텐츠 이동 · 조합 상태 · 단축키
  kwReviewCell() {return this.kwReview && this.kwRunData ? this.kwRunData.cells[this.kwReview.cell_id] || null : null;},
  kwReviewIndex() {return this.kwReview ? (this.kwRunData?.items || []).findIndex(it=>it.key===this.kwReview.item.key) : -1;},
  kwReviewGo(step) {
    if(!this.kwCanLeaveReview()) return false;
    const items=this.kwRunData?.items || [], slots=this.kwRunData?.slots || [];
    for(let i=this.kwReviewIndex()+step;i>=0 && i<items.length;i+=step) {
      const same=slots.find(s=>s.label===this.kwReview.slot);
      const slot=[same,...slots].find(s=>s && this.kwReviewable(this.kwCell(items[i],s)));
      if(slot) return this.kwEnterReview(items[i],slot);
    }
    return false;
  },
  kwNextPending(finalOnly=false) {
    const items=this.kwRunData?.items || [], slots=this.kwRunData?.slots || [];
    const pairs=items.flatMap(it=>slots.map(s=>[it,s]));
    const at=pairs.findIndex(([it,s])=>it.key===this.kwReview.item.key && s.label===this.kwReview.slot);
    const next=[...pairs.slice(at+1),...pairs.slice(0,at)].find(([it,s])=>{const c=this.kwCell(it,s);return this.kwReviewable(c) && (finalOnly ? !c.final : !(c.reviews || []).length);});
    return next ? this.kwEnterReview(next[0],next[1]) : false;
  },
  kwReviewKey(e) {
    if(!this.kwReview || this.kwTab!=='run' || ['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)) return;
    if(e.key==='Escape') this.kwCloseReview();
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
  async kwExcelTemplate() {
    try {
      const r=await this._afetch('/lab-keywords?template=1',{headers:this._authHeaders()});
      if(!(r.headers.get('content-type') || '').includes('spreadsheet')) throw new Error((await r.json()).error || '양식을 받지 못했습니다');
      const u=URL.createObjectURL(await r.blob()), a=document.createElement('a');
      a.href=u; a.download='keyword_lab_template.xlsx'; a.click(); setTimeout(()=>URL.revokeObjectURL(u),60000);
    } catch(e) {this.kwError=e.message;}
  },
  // 엑셀·CSV 업로드 → 서버가 행별 반영·제외를 돌려준다(실행은 STEP 4에서)
  async kwExcelPick(file) {
    if(!file || this.kwBusy) return;
    if(file.size>2*1024*1024) {this.kwError='파일은 2MB 이하여야 합니다';return;}
    this.kwBusy=true;this.kwError='';this.kwExcel=null;
    try {
      const data=await new Promise((ok,no)=>{const fr=new FileReader();fr.onload=()=>ok(String(fr.result).split(',')[1] || '');fr.onerror=()=>no(new Error('파일을 읽지 못했습니다'));fr.readAsDataURL(file);});
      const r=await this.kwRequest('',{action:'parse_excel',name:file.name,data});
      this.kwExcel={name:file.name,rows:r.rows,rejected:r.rejected};
    } catch(e) {this.kwError=e.message;} finally {this.kwBusy=false;}
  },
  kwExcelRows() {return [...(this.kwExcel?.rows || []),...(this.kwExcel?.rejected || [])].sort((a,b)=>a.row-b.row);},
  // 실행 결과 엑셀 · 현재 값만(최종 확정이면 확정값, 아니면 모델 결과) · 파일명은 서버 규칙
  async kwDownloadXlsx() {
    try {
      const r=await this._afetch('/lab-keywords?xlsx=1&run='+encodeURIComponent(this.kwRunId),{headers:this._authHeaders()});
      if(!(r.headers.get('content-type') || '').includes('spreadsheet')) throw new Error((await r.json()).error || '엑셀을 만들지 못했습니다');
      const name=((r.headers.get('content-disposition') || '').match(/filename="([^"]+)"/) || [])[1] || 'keyword_result.xlsx';
      const u=URL.createObjectURL(await r.blob()), a=document.createElement('a');
      a.href=u; a.download=name; a.click(); setTimeout(()=>URL.revokeObjectURL(u),60000);
    } catch(e) {this.kwError=e.message;}
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
    if(slot) return this.kwEnterReview(item,slot);
  },
  kwMetaList(v) {return Array.isArray(v) ? v.map(x=>typeof x==='string' ? x : (x && x.name) || '').filter(Boolean) : [];},
  kwInKeywords(name) {return this.kwCandidates(this.kwReviewCell()).some(k=>k.text.includes(name));},
  kwApplySavedReview(saved, q, rid) {
    Object.assign(this.kwRunData.cells[q.cell_id],saved.cell);this.kwRunData.revision=saved.revision;
    q.expected_revision=saved.cell.review_revision;q.final=saved.cell.final;
    this._kwSavedReview=this.kwReviewSnapshot();
    if(this.kwCatalog?.gold) {
      this.kwCatalog.gold=this.kwCatalog.gold.filter(g=>g.run_id!==rid || g.cell_id!==q.cell_id || g.review_id===q.final?.id);
      this.kwTraining=this.kwTraining.filter(key=>this.kwDevelopmentGold().some(g=>g.item_key===key));
      this.kwEvaluation=this.kwEvaluation.filter(key=>this.kwEvaluationGold().some(g=>g.item_key===key));
    }
  },
  async kwConfirmOverwrite(q, rid, complete=false) {
    // 응답 유실 후 같은 완료 요청은 서버의 멱등 응답으로 확인한다.
    if(complete && q.completionRequest?.submitted) return true;
    const {run}=await this.kwRequest('?run='+encodeURIComponent(rid));
    const cell=run.cells[q.cell_id];
    if(!cell) throw new Error('검수 결과를 다시 열어 주세요.');
    const latest=(cell.reviews || []).slice(-1)[0], gold=run.current_gold?.[q.item.key];
    const previous=latest || (run.prior_reviews?.[q.item.key] || []).slice(-1)[0];
    if(previous || (complete && gold)) {
      const owner=(complete && gold) || previous;
      const before=(complete && gold) ? this.kwAnswers(gold) : (latest ? this.kwAnswers(latest) : previous.answers || []);
      const warning=(cell.review_revision!==q.expected_revision ? '작업 중 다른 검수가 저장되었습니다.\n' : '기존 검수 결과가 있습니다.\n')+
        (owner.by || '검수자')+' · '+this.kwDate(owner.at)+'\n기존 결과: '+(before.map(k=>k.text).join(' · ') || '적합한 결과 없음')+
        '\n변경 결과: '+(this.kwGoldPreview().join(' · ') || '적합한 결과 없음')+
        '\n\n현재 입력으로 저장할까요? 이전 결과와 검수자·시각은 이력에 남습니다.';
      if(!window.confirm(warning)) return false;
    }
    q.expected_revision=cell.review_revision;
    if(complete) q.completionRequest.expected_gold_id=gold?.id || '';
    this.kwRunData.prior_reviews=run.prior_reviews || {};
    return true;
  },
  async kwSaveReview() {
    if(this.kwBusy || !this.kwReview) return;
    this.kwError='';this.kwMessage='';
    const missing=this.kwReviewMissing();
    if(missing) {this.kwReviewError(missing);return;}
    this.kwBusy=true;
    try {
      const q=this.kwReview, rid=this.kwRunId;
      if(!await this.kwConfirmOverwrite(q,rid)) return;
      const saved=await this.kwRequest('',{action:'review',run_id:rid,cell_id:q.cell_id,
        expected_revision:q.expected_revision,judgments:q.judgments,additions:q.additions,
        no_keywords:q.no_keywords,note:q.note,partition:q.partition,finalize:false});
      this.kwApplySavedReview(saved,q,rid);
      if(!await this.kwOpenRun(rid)) {
        this.kwReviewError('판단은 저장했지만 결과를 다시 불러오지 못했습니다. 잠시 후 실험 기록을 다시 열어 주세요.');
        return;
      }
      this.kwBusy=false;
      this.kwMessage='판단을 임시 저장했습니다 · 검수 완료·정답셋 저장까지 진행하세요';
    }catch(e){this.kwReviewError('저장 완료를 확인하지 못했습니다. '+e.message);}finally{this.kwBusy=false;}
  },
  kwCurrentGold() {return ((this.kwCatalog || {}).gold || []).find(g=>this.kwReview && g.item_key===this.kwReview.item.key);},
  async kwCancelFinal() {
    if(!this.kwReview?.final || !this.kwCatalog?.can_final || !this.kwCanLeaveReview()) return;
    if(!window.confirm('이 결과의 최종 확정을 취소할까요? 판단과 메모는 보존합니다. 이 확정을 근거로 반영한 정답은 개선·새 실험에서 제외되며, 기존 프롬프트 버전과 실험 결과는 유지됩니다.')) return;
    this.kwBusy=true;this.kwError='';this.kwMessage='';
    try {
      const q=this.kwReview, rid=this.kwRunId;
      const saved=await this.kwRequest('',{action:'cancel_final',run_id:rid,cell_id:q.cell_id,
        expected_revision:q.expected_revision,final_id:q.final.id});
      this.kwApplySavedReview(saved,q,rid);
      if(!await this.kwOpenRun(rid)) {
        this.kwReviewError('최종 확정은 취소했지만 결과를 다시 불러오지 못했습니다. 잠시 후 실험 기록을 다시 열어 주세요.');
        return;
      }
      this.kwMessage='최종 확정을 취소했습니다. 판단과 메모는 보존되며, 다시 사용하려면 검수 완료·정답셋 저장을 눌러 주세요.';
    } catch(e) {this.kwReviewError('확정 취소 완료를 확인하지 못했습니다. '+e.message);}
    finally {this.kwBusy=false;}
  },
  kwGoldPreview() {
    const q=this.kwReview;if(!q)return [];
    return [...q.judgments.filter(j=>['accept','edit'].includes(j.verdict)).map(j=>j.verdict==='edit' ? j.corrected.trim() : j.original),...q.additions.map(a=>a.text.trim())].filter(Boolean);
  },
  kwCompletionDone() {
    const q=this.kwReview,g=this.kwCurrentGold();
    return Boolean(q?.final && g?.review_id===q.final.id && !this.kwReviewDirty());
  },
  async kwCompleteReview() {
    if(this.kwBusy || !this.kwReview || this.kwCompletionDone() || !this.kwCatalog?.can_final) return;
    this.kwError='';this.kwMessage='';
    const missing=this.kwReviewMissing();
    if(missing || !['development','evaluation'].includes(this.kwReview.partition)) {
      this.kwReviewError(missing || '정답셋 용도를 선택하세요.');return;
    }
    const q=this.kwReview,rid=this.kwRunId,snapshot=this.kwReviewSnapshot();
    if(!q.completionRequest || q.completionRequest.snapshot!==snapshot) q.completionRequest={
      id:crypto.randomUUID().replace(/-/g,''),snapshot,expected_gold_id:this.kwCurrentGold()?.id || ''};
    this.kwBusy=true;
    try {
      if(!await this.kwConfirmOverwrite(q,rid,true)) return;
      q.completionRequest.submitted=true;
      const saved=await this.kwRequest('',{action:'complete_review',run_id:rid,cell_id:q.cell_id,
        expected_revision:q.expected_revision,request_id:q.completionRequest.id,expected_gold_id:q.completionRequest.expected_gold_id,
        judgments:q.judgments,additions:q.additions,no_keywords:q.no_keywords,note:q.note,partition:q.partition});
      this.kwApplySavedReview(saved,q,rid);
      this.kwCatalog.gold=[...(this.kwCatalog.gold || []).filter(g=>g.item_key!==saved.gold.item_key),saved.gold];
      q.completionRequest=null;this.kwBusy=false;
      this.kwMessage='검수 완료 · '+(saved.gold.partition==='evaluation' ? '평가용' : '개선용')+' '+this.kwTargetLabel(q.target)+' 정답셋에 저장했습니다';
      if(!await this.kwOpenRun(rid)) this.kwMessage+=' · 이력은 결과를 다시 열어 확인하세요';
      if(this.kwAutoNext && await this.kwNextPending(true)) this.kwMessage+=' · 다음 검수 결과입니다';
    }catch(e){if(e.rejected) q.completionRequest=null;this.kwReviewError('완료를 확인하지 못했습니다. 입력은 유지됩니다. '+e.message);}finally{this.kwBusy=false;}
  },
  kwDevelopmentGold() {return ((this.kwCatalog || {}).gold || []).filter(g=>g.partition==='development' && (g.target || 'keyword')===this.kwTarget);},
  kwEvaluationGold() {return ((this.kwCatalog || {}).gold || []).filter(g=>g.partition==='evaluation' && (g.target || 'keyword')===this.kwTarget);},
  kwContentCategories(items) {
    return [...new Set(items.flatMap(item=>this.kwMetaList(item.meta?.content_category)))].sort((a,b)=>a.localeCompare(b,'ko'));
  },
  kwContentRows(items, query='', category='') {
    const words=query.trim().normalize('NFC').toLocaleLowerCase().split(/\s+/).filter(Boolean);
    return items.filter(item=>{
      const categories=this.kwMetaList(item.meta?.content_category);
      if(category && !categories.includes(category)) return false;
      const text=[item.title || '',item.meta?.summary || '',...categories,...categories.map(c=>this.catKo?.(c) || c),
        ...this.kwMetaList(item.meta?.entities),...this.kwAnswers(item).map(k=>k.text || k),item.by || ''].join(' ').normalize('NFC').toLocaleLowerCase();
      return words.every(word=>text.includes(word));
    });
  },
  kwGoldRows(partition, query='', category='', selectedOnly=false) {
    const gold=partition==='evaluation' ? this.kwEvaluationGold() : this.kwDevelopmentGold();
    const selected=partition==='evaluation' ? this.kwEvaluation : this.kwTraining;
    return this.kwContentRows(gold,query,category).filter(g=>!selectedOnly || selected.includes(g.item_key)).sort((a,b)=>(b.at || 0)-(a.at || 0));
  },
  kwResultRows(query='', category='', state='') {
    return this.kwContentRows(this.kwRunData?.items || [],query,category).filter(item=>!state || this.kwItemState(item)[0]===state);
  },
  kwImpMode() {const t=this.kwCatalog?.tuning;return t?.status==='running' ? (t.max_rounds===1 ? 'once' : 'auto') : this.kwImproveMode;},
  kwAuto() {return this.kwTarget==='keyword' && this.kwImpMode()==='auto';},
  kwTuning() {const t=this.kwCatalog?.tuning;return t && this.kwTarget==='keyword' ? t : null;},   // 실행 유형과 관계없이 마지막 실행
  kwTuneCandidates() {
    const vs=this.kwCatalog?.versions || [];
    return (this.kwTuning()?.history || []).filter(h=>h.round>0).map(h=>({...h,version:vs.find(v=>v.id===h.version_id)})).filter(h=>h.version);
  },
  kwSaveTarget() {
    const c=this.kwTuneCandidates(), t=this.kwTuning();if(!c.length)return null;
    return c.find(h=>h.version_id===this.kwPickVersion) || c.find(h=>h.version_id===t.best?.version_id) || c[c.length-1];
  },
  async kwSaveCandidate() {
    const h=this.kwSaveTarget();if(this.kwBusy || !h || !h.version.draft)return;
    this.kwBusy=true;this.kwError='';
    try {await this.kwRequest('',{action:'save_version',version_id:h.version_id});await this.kwLoad();
      this.kwPickVersion=h.version_id;this.kwMessage='';}
    catch(e){this.kwError=e.message;}finally{this.kwBusy=false;}
  },
  async kwSaveProposal() {
    const p=this.kwProposal;if(this.kwBusy || !p || p.saved)return;
    this.kwBusy=true;this.kwError='';
    try {const r=await this.kwRequest('',{action:'version',target:p.target || 'keyword',model:p.model,rules:p.rules,title:'단건 개선',
        note:p.changes.map(c=>c.reason+': '+c.change).join('\n'),parent_id:p.parent_id,proposal_id:p.id});
      this.kwProposal={...p,saved:r.version};await this.kwLoad();}
    catch(e){this.kwError=e.message;}finally{this.kwBusy=false;}
  },
  kwImproveIssue() {
    if(!this.kwCatalog?.can_manage) return '프롬프트 관리 권한이 필요합니다.';
    if(this.kwCatalog?.tuning?.status==='running') return '오토파일럿 실행 중입니다. 아래에서 진행 상황을 확인하세요.';
    if(!this.kwAllVersions().some(v=>v.id===this.kwLoopVersion)) return '개선할 프롬프트 버전을 선택하세요.';
    if(!this.kwCompiler) return '개선안을 작성할 메타컴파일러 모델을 선택하세요.';
    if(!this.kwTraining.length || this.kwTraining.length>20) return '개선용 정답을 1~20개 선택하세요.';
    if(this.kwTraining.some(key=>!this.kwDevelopmentGold().some(g=>g.item_key===key))) return '개선용 정답이 변경되었습니다. 다시 선택하세요.';
    if(this.kwTarget==='keyword') {
      if(!this.kwEvaluation.length || this.kwEvaluation.length>30) return '평가용 정답 1~30개가 필요합니다. 검수 완료 시 평가용으로 저장한 뒤 선택하세요.';
      if(this.kwEvaluation.some(key=>!this.kwEvaluationGold().some(g=>g.item_key===key))) return '평가용 정답이 변경되었습니다. 다시 선택하세요.';
    }
    if(this.kwAuto() && (!Number.isInteger(this.kwTuneRounds) || this.kwTuneRounds<1 || this.kwTuneRounds>10)) return '최대 반복 횟수는 1~10회로 입력하세요.';
    return '';
  },
  kwFocus(id) {
    this.$nextTick?.(()=>{const el=document.getElementById(id);el?.scrollIntoView({block:'center',behavior:'instant'});el?.focus({preventScroll:true});});
  },
  async kwStartTuning() {
    if(this.kwBusy || !this.kwCanLeaveReview())return;
    this.kwError=this.kwImproveIssue();if(this.kwError)return;
    const auto=this.kwAuto();this.kwBusy=true;this.kwImproving=auto ? 'auto' : 'manual';this.kwMessage='';this.kwPickVersion='';
    try {const r=await this.kwRequest('',{action:'tune_start',version_id:this.kwLoopVersion,compiler_model:this.kwCompiler,
      gold_keys:this.kwTraining,evaluation_keys:this.kwEvaluation,max_rounds:auto ? Number(this.kwTuneRounds) : 1});
      this.kwCatalog.tuning=r.tuning;await this.kwLoad();
      this.kwMessage=(auto ? '반복 개선' : '단건 개선')+'을 시작했습니다. 실행 결과 확인에서 진행 상황과 평가 점수를 확인하세요.';
    }catch(e){this.kwError=e.message;}finally{this.kwBusy=false;this.kwImproving='';}
    if(this.kwTab==='improve')this.kwFocus('kw-improve-status');
  },
  async kwStopTuning() {
    if(this.kwBusy)return;this.kwBusy=true;
    try {await this.kwRequest('',{action:'tune_stop'});await this.kwLoad();}
    catch(e){this.kwError=e.message;}finally{this.kwBusy=false;}
  },
  async kwOpenTuningCandidate(candidate) {
    if(!candidate || !this.kwCanLeaveReview())return;
    if(await this.kwOpenRun(candidate.run_id)) {
      this.kwChangeTab('run');
      this.kwMessage='튜닝 후보의 결과를 검수하세요.';
    }
  },
  async kwCompile() {
    if(this.kwBusy) return;
    this.kwError=this.kwImproveIssue();if(this.kwError)return;
    this.kwBusy=true;this.kwImproving='manual';this.kwMessage='';
    try {this.kwProposal=(await this.kwRequest('',{action:'compile',version_id:this.kwLoopVersion,
      compiler_model:this.kwCompiler,gold_keys:this.kwTraining})).proposal;
      this.kwMessage='개선안 작성 완료 · 결과를 확인하고 프롬프트를 저장하세요.';
    }catch(e){this.kwError='개선안을 작성하지 못했습니다. '+e.message;}
    finally{this.kwBusy=false;this.kwImproving='';}
    if(this.kwTab==='improve')this.kwFocus(this.kwError ? 'kw-improve-status' : 'kw-proposal-result');
  },
  // 정답 목록 선택 삭제 · 선택은 개선·평가 선택과 같은 체크박스를 쓴다
  async kwDeleteGold(partition) {
    const field=partition==='development' ? 'kwTraining' : 'kwEvaluation', keys=[...this[field]];
    if(this.kwBusy || !keys.length || !window.confirm('선택한 정답 '+keys.length+'개를 삭제합니다.\n실험의 검수 기록은 남으며, 같은 결과를 다시 확정하면 정답으로 돌아옵니다.')) return;
    this.kwBusy=true;this.kwError='';
    try {await this.kwRequest('',{action:'delete_gold',item_keys:keys});this[field]=[];await this.kwLoad();this.kwMessage='정답 '+keys.length+'개 삭제 완료';}
    catch(e){this.kwError=e.message;}finally{this.kwBusy=false;}
  },
  // 정답 목록 CSV · 선택한 정답이 있으면 그것만, 없으면 해당 용도 전체
  kwDownloadGold(partition) {
    const all=partition==='development' ? this.kwDevelopmentGold() : this.kwEvaluationGold();
    const picked=partition==='development' ? this.kwTraining : this.kwEvaluation;
    const rows=picked.length ? all.filter(g=>picked.includes(g.item_key)) : all;
    const esc=v=>{let t=String(v==null ? '' : v);if(/^[=+\-@\t\r]/.test(t))t="'"+t;return '"'+t.replace(/"/g,'""')+'"';};   // 수식 주입 차단
    const out=[['콘텐츠','카테고리','확정 '+this.kwTargetLabel(),'용도','확정자','확정 일시','콘텐츠 해시']];
    for(const g of rows) out.push([g.title,this.kwMetaList(g.meta?.content_category).map(c=>this.catKo(c)).join(' · '),this.kwAnswers(g).map(k=>k.text).join(' · '),
      partition==='development' ? '개선용' : '평가용',g.by,g.at ? this.kwDate(g.at) : '',g.content_hash]);
    const day=new Date(Date.now()+9*3600e3).toISOString().slice(0,10).replace(/-/g,'');   // KST
    const u=URL.createObjectURL(new Blob(['\ufeff'+out.map(r=>r.map(esc).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'})), a=document.createElement('a');
    a.href=u;a.download=this.kwTarget+'_gold_'+day+'_'+(partition==='development' ? '개선용' : '평가용')+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(u),60000);
  },
  kwShowVersion(v) {
    this.kwVersion=(this.kwCatalog?.versions || []).find(x=>x.id===v?.id) || v || null;
    this.kwChangeTab('prompts');
  }
}));
