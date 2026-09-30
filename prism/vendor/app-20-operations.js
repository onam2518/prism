/* 상시 검수 원장 · 실제 메뉴 안에서 판정/조치/시간/주차를 연결한다. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
  opsDetail: null, opsAxes: {}, opsBusy: false, opsList: null, opsState: '', opsOwner: '',
  opsHistoryBasis:'', opsTimeType: 'first', opsCase: null, opsDecision: '', opsEvidence: '',
  opsFields: [{key:'summary',label:'리드문'},{key:'entities',label:'엔티티'},{key:'intent',label:'인텐트'},{key:'content_category',label:'카테고리'}],
  opsStateLabel(s) { return ({open:'미조치',working:'조치 중',recheck:'재검수 대기',verify:'반영 확인',closed:'종료',hold:'판단 보류'})[s] || s; },
  opsTypeLabel(s) { return ({first:'1차 검수',fix:'수정',recheck:'재검수',final:'최종 조정',operation:'운영'})[s] || s; },
  async opsRequest(path, data) {
    const options = data ? {method:'POST',headers:this._authHeaders(),body:JSON.stringify(Object.assign({reviewer:this.reviewer||''},data))} : {headers:this._authHeaders()};
    const r = await (await this._afetch(path, options)).json();
    if (!r || !r.ok) throw new Error((r && r.error) || '저장 결과를 확인할 수 없습니다');
    return r;
  },
  opsUrl(hash) { return '/ops-review?hash='+encodeURIComponent(hash)+'&reviewer='+encodeURIComponent(this.reviewer||''); },
  async opsOpen(c) {
    this.opsDetail=null; this.opsCase=null; this.opsAxes={};this.opsHistoryBasis='';
    if (!c || !/^[0-9a-f]{16}$/.test(c.hash||'')) return;
    try {
      const r=await this.opsRequest(this.opsUrl(c.hash));
      if (!this.detail || this.detail.hash!==c.hash) return;
      this.opsDetail=r;
      if(!this.detail.body && r.basis.body)this.detail.body=r.basis.body;
      this.opsFields.forEach(f=>{this.opsAxes[f.key]=Object.assign({status:'',reason:'',proposal:''},((r.review||{}).axes||{})[f.key]||{});});
      const pending=r.cases.filter(x=>x.state!=='closed');
      this.opsCase=pending.length ? JSON.parse(JSON.stringify(pending[pending.length-1])) : null;
      if (this.opsCase) { this.opsCase.service_required=true; this.opsCase.receipt_evidence=''; }
    } catch(e) { this._err(e.message); }
  },
  async opsSave(data) {
    const current=this.opsDetail;
    if (!current || this.opsBusy) return null;
    this.opsBusy=true;
    try {
      const r=await this.opsRequest('/ops-review',Object.assign({hash:current.hash,revision:current.revision,basis_token:current.basis.token},data));
      if (this.detail && this.detail.hash===r.hash) {
        this.opsDetail=r;
        if(r.basis.token!==current.basis.token)this.opsFields.forEach(f=>{this.opsAxes[f.key]={status:'',reason:'',proposal:''};});
      }
      this.liveToast('저장 완료 · 판정과 후속 조치는 별도 확인');
      await this.opsLoad();
      return r;
    } catch(e) { this._err(e.message); return null; }
    finally { this.opsBusy=false; }
  },
  opsAllAccurate() { this.opsFields.forEach(f=>{this.opsAxes[f.key].status='accurate';}); },
  async opsSaveReview() {
    const r=await this.opsSave({action:'review',axes:this.opsAxes});
    if (!r) return;
    await this.loadRaw();
    if (this.detail && r.hash===this.detail.hash) {
      const item=((this.rawData||{}).items||[]).find(x=>x.hash===r.hash);
      if (item) this.detail.fb=item.fb;
      const pending=r.cases.filter(x=>x.state!=='closed');
      this.opsCase=pending.length ? Object.assign({},pending[pending.length-1],{service_required:true,receipt_evidence:''}) : null;
    }
  },
  async opsSaveCase() {
    if (!this.opsCase) return;
    const data=Object.assign({},this.opsCase,{action:'case',case_id:this.opsCase.id});
    if (this.opsCase.receipt_evidence) data.receipt={evidence:this.opsCase.receipt_evidence,publication_revision:this.opsDetail.basis.publication_revision};
    const r=await this.opsSave(data);
    if (r) this.opsCase=Object.assign({},r.cases.find(x=>x.id===data.case_id),{service_required:data.service_required,receipt_evidence:''});
  },
  async opsLoad() {
    try { this.opsList=await this.opsRequest('/ops-review?reviewer='+encodeURIComponent(this.reviewer||'')); }
    catch(e) { this._err(e.message); }
  },
  get opsFilteredCases() { return ((this.opsList||{}).items||[]).filter(c=>(!this.opsState||c.state===this.opsState)&&(!this.opsOwner||c.owner===this.opsOwner)); },
  async opsOpenCase(c) {
    try {
      const r=await this.opsRequest(this.opsUrl(c.hash)); const b=r.basis.snapshot, im=b.item_meta||{}, qm=b.quality_meta||{};
      if(c.legacy){await this.opsRequest('/ops-review',{action:'import_legacy',hash:c.hash,revision:r.revision,basis_token:r.basis.token});}
      this.openDetail({hash:c.hash,title:b.title,body:b.body,service:b.service,model:b.model,summary:im.summary||'',
        entities:(im.entities||[]).map(e=>typeof e==='string'?e:e.name),intent:im.intent||[],category:this._categoryPaths(im.content_category),grade:qm.finalGrade||'',reasons:qm.reasons||[]});
    } catch(e) { this._err(e.message); }
  },
  get opsMySession() {
    return ((this.opsDetail||{}).sessions||[]).filter(s=>!s.finished && s.by===((this.arenaData||{}).my_id||this.reviewer)).slice(-1)[0] || null;
  },
  async opsTime(event) { await this.opsSave({action:'time',event:event,type:this.opsTimeType}); },
  async opsCalibrate() { await this.opsSave({action:'calibration',decision:this.opsDecision,evidence:this.opsEvidence}); },
  async opsCaptureFinal(row, form) {
    try {
      const r=await this.opsRequest(this.opsUrl(row.hash)); const v=r.values;
      const names=a=>(a||[]).map(x=>typeof x==='string'?x:(x.name||x.path)).join('|');
      if ((row.summary||'')!==(v.summary||'') || names(row.entities)!==names(v.entities) || names(row.intent)!==names(v.intent) || names(row.category)!==names(v.content_category)) throw new Error('목록의 값이 변경되었습니다. 새로고침 후 다시 열어 주세요');
      form.opsSnapshot=r; return r;
    } catch(e) { form.opsError=e.message; this._err(e.message); return null; }
  },
  async opsCorrectFinal(form) {
    const snapshot=await form.opsCapture;
    if (!snapshot) return false;
    try {
      const patch={}; const values={summary:form.summary,entities:form.entities,intent:form.intent,content_category:form.cats};
      for(const key of Object.keys(values))if(JSON.stringify(values[key])!==JSON.stringify(snapshot.values[key]))patch[key]=values[key];
      if(form.grade!==((snapshot.basis.snapshot.quality_meta||{}).finalGrade||''))patch.finalGrade=form.grade;
      const r=await this.opsRequest('/ops-review',{action:'correct_final',hash:form.hash,revision:snapshot.revision,basis_token:snapshot.basis.token,patch:patch});
      if (this.detail && this.detail.hash===form.hash) {this.opsDetail=r;Object.assign(this.detail,{summary:form.summary,entities:form.entities,intent:form.intent,category:form.cats,grade:form.grade});}
      this.liveToast('수정값·편입 결정·작업 이력 저장 완료 · 정답셋 반영은 별도 확인');
      await this.loadFinalQueue(); if(this.histOpen)this.loadHistory();return true;
    } catch(e) {this._err(e.message);return false;}
  },
  opsPlanRows: [], opsPlanHash: '', opsPlanPick:[], opsPlanBusy:false, opsPlanDefaultOwner:'', opsPlanDefaultTopic:'', opsWeek: '', opsPlanTarget: 100, opsPlanReason: '',
  opsPlanInit() {
    if(this.opsWeek)return;
    const d=new Date();const n=(d.getDay()+6)%7;d.setDate(d.getDate()-n);this.opsWeek=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
    this.opsLoad();this.loadRaw();
  },
  async opsPlanAdd() {
    if(this.opsPlanBusy)return;
    const hashes=[...new Set([this.opsPlanHash,...this.opsPlanPick].filter(Boolean))].filter(h=>!this.opsPlanRows.some(e=>e.hash===h));
    if(!hashes.length)return;
    this.opsPlanBusy=true;
    try {
      const selected=[];
      for(let i=0;i<hashes.length;i+=4){
        const rows=await Promise.all(hashes.slice(i,i+4).map(h=>this.opsRequest(this.opsUrl(h))));
        rows.forEach(r=>selected.push({hash:r.hash,title:r.basis.title,basis_token:r.basis.token,owner:this.opsPlanDefaultOwner,topic:this.opsPlanDefaultTopic,sample:'general',carry_from:'',work_type:'first'}));
      }
      this.opsPlanRows.push(...selected);this.opsPlanHash='';this.opsPlanPick=[];
    } catch(e){this._err(e.message);}finally{this.opsPlanBusy=false;}
  },
  async opsPlanSave() {
    const plan=((this.opsList||{}).plans||{})['ops_week_'+this.opsWeek];
    try {
      await this.opsRequest('/ops-review',{action:'plan',week:this.opsWeek,revision:plan?plan.revision:0,entries:this.opsPlanRows,target:Number(this.opsPlanTarget),adjustment_reason:this.opsPlanReason,
        selection:{keyword:this.rawQ||'',service:this.rawSvc||'',category:this.rawCat||'',timezone:'Asia/Seoul'}});
      this.liveToast('주간 계획과 담당 배정 저장 완료'); await this.opsLoad();await this.loadRaw();this.loadCrew();
    }catch(e){this._err(e.message);}
  },
  opsPlanUse(week) { const p=((this.opsList||{}).plans||{})['ops_week_'+week];if(!p)return;this.opsWeek=week;const v=p.versions[p.versions.length-1];this.opsPlanRows=JSON.parse(JSON.stringify(v.entries));this.opsPlanTarget=v.target;this.opsPlanReason=v.adjustment_reason; },
  finalOffset:0, finalReason:'', finalOrder:'oldest',
  async loadFinalQueue() {
    this.finalBusy=true;
    try {this.finalQueue=await this.opsRequest('/final-queue?offset='+this.finalOffset+'&limit=50&reason='+encodeURIComponent(this.finalReason)+'&order='+this.finalOrder+'&reviewer='+encodeURIComponent(this.reviewer||''));}
    catch(e){this._err(e.message);}finally{this.finalBusy=false;}
  },
  opsDnm:null, opsDnmMsg:'',
  async opsDnmLoad() {try{this.opsDnm=await this.opsRequest('/dnm');}catch(e){this.opsDnmMsg=e.message;}},
  async opsDnmFile(event, action) {
    const file=event.target.files[0];if(!file)return;
    try {
      const value=JSON.parse(await file.text());const data={action:action,expected_revision:((this.opsDnm||{}).control||{}).revision||0};
      if(action==='configure')data.registry=value;
      if(action==='approve')data.evidence=value;
      if(action==='event')data.event=value;
      const r=await this.opsRequest('/dnm',data);this.opsDnmMsg=r.publication ? ('원천 처리 결과 · 발행 순번 '+r.publication.publication_revision+' · '+(r.publication.publishable?'발행 가능':'전환 증빙 확인 필요')) : '기준 파일 저장 완료';await this.opsDnmLoad();
    }catch(e){this.opsDnmMsg=e.message;}finally{event.target.value='';}
  },
  async opsDnmPrepare() {
    try {const r=await this.opsRequest('/dnm',{action:'prepare_policy'});await this.opsRequest('/dnm',{action:'configure',policy:r.policy,expected_revision:((this.opsDnm||{}).control||{}).revision||0});await this.opsDnmLoad();this.opsDnmMsg='현재 실행 기준의 정책 저장 완료 · 전환 승인 증빙은 별도 확인';}
    catch(e){this.opsDnmMsg=e.message;}
  }
}));
