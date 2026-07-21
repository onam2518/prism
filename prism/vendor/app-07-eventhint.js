/* Prism 앱 조각 07 · prismApp 프로퍼티 그룹(자동 분할 · 앱 분할 6차).
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
      eventHint() {
        const v = this.settingsDraft.co_min;
        if (v <= 1) return '조금만 겹쳐도 한 사건으로 묶습니다 · 사건형 토픽이 많아집니다';
        if (v >= 3) return '확실히 많이 겹칠 때만 묶습니다 · 사건형 토픽이 적어집니다';
        return '적당히 겹칠 때 묶습니다 (기본)';
      },
      entityHint() {
        const v = this.settingsDraft.entity_min;
        if (v <= 1) return '기사가 1건이라도 그 인물·브랜드를 토픽으로 만듭니다 · 아주 촘촘';
        if (v >= 3) return '기사가 3건 이상 쌓였을 때만 토픽으로 만듭니다 · 엄선';
        return '기사가 2건 이상일 때 토픽으로 만듭니다 (기본)';
      },
      async studioSave() {
        if (!this.studio.name.trim()) { this.studioMsg = '토픽 이름을 입력하세요'; return; }
        this.studioSaving = true; this.studioMsg = '저장 중…';
        try {
          const r = await this._studioPost({ action: 'save', def: this.studioDef() });
          if (r && r.error) { this.studioMsg = '오류: ' + r.error; }
          else {
            this.topicData = r; this._syncTopicSettings(); this.studioMsg = '저장했습니다'; this.studioReset();
            const dup = (r.similar || [])[0];        // 중복 의심: 저장은 되고 경고만(합칠지 판단은 사람이)
            if (dup) this.liveToast('⚠ 비슷한 토픽이 이미 있어요 · 「' + dup.name + '」 (' + Math.round(dup.score * 100) + '% 유사) · 겹치면 하나로 합쳐 주세요');
          }
        } catch (e) { this.studioMsg = '저장 실패'; } this.studioSaving = false;
      },
      studioEdit(g) {
        this.topicGenTab = 'manual';         // 수정은 수동 정의 위저드에서
        const def = (this.topicData.customDefs || []).find(d => d.id === g.id) || {};
        const rq = def.req || { cats: [], intents: [], keywords: [] };
        const ng = def.neg || { cats: [], intents: [], keywords: [] };
        this.studio = { name: def.name || g.name || '', prompt: def.prompt || g.prompt || '', cats: [...(def.cats || [])], intents: [...(def.intents || [])], keywords: [...(def.keywords || [])], eattrs: [...(def.eattrs || [])], kwInput: '', eaKey: 'gender', eaVal: '', editId: g.id, auto: { cats: [], intents: [], keywords: [] }, req: { cats: [...(rq.cats || [])], intents: [...(rq.intents || [])], keywords: [...(rq.keywords || [])] }, neg: { cats: [...(ng.cats || [])], intents: [...(ng.intents || [])], keywords: [...(ng.keywords || [])] } };
        this.studioMsg = ''; this.studioPreviewNow();
        try { window.scrollTo({ top: 0, behavior: 'smooth' }); } catch (e) {}
      },
      studioReset() { this.studio = { name: '', prompt: '', cats: [], intents: [], keywords: [], eattrs: [], kwInput: '', eaKey: 'gender', eaVal: '', editId: null, auto: { cats: [], intents: [], keywords: [] }, req: { cats: [], intents: [], keywords: [] }, neg: { cats: [], intents: [], keywords: [] } }; this.studioPreview = { bundles: [], n_total: (this.topicData && this.topicData.n_contents) || 0, must_n: 0, opt_n: 0 }; },
      async studioDelete(g) {
        if (!(await this.dsConfirm('토픽 “' + (g.name || g.id) + '” 을 삭제할까요?', { ok: '삭제', danger: true }))) return;
        try { const r = await this._studioPost({ action: 'delete', id: g.id }); if (r && !r.error) { this.topicData = r; this._syncTopicSettings(); if (this.studio.editId === g.id) this.studioReset(); } } catch (e) { this._err('삭제 실패'); }
      },
      async saveTopicSettings() {
        this.settingsSaving = true; this.settingsMsg = '적용 중…';
        try {
          const r = await this._studioPost({ action: 'settings', settings: { co_min: this.settingsDraft.co_min, entity_min: this.settingsDraft.entity_min } });
          if (r && r.error) { this.settingsMsg = '오류: ' + r.error; }
          else { this.topicData = r; this._syncTopicSettings(); this.settingsMsg = '적용했습니다 · 자동 토픽을 재생성했습니다'; }
        } catch (e) { this.settingsMsg = '적용 실패'; } this.settingsSaving = false;
      },
      // ── 토픽 큐레이션: 개별 콘텐츠 제외·복구(자동·사용자 토픽 공통 · 변경은 관리자) ──
      get topicAdmin() { return this.backend !== 'supabase' || !!(this.adminData && this.adminData.isAdmin); },
      async topicExclude(c) {
        if (!this.drillData || !this.drillData.topic_id || !c.hash) return;
        if (!(await this.dsConfirm('“' + (c.title || '').slice(0, 40) + '” 을(를) 이 토픽에서 제외할까요?\n매칭 조건은 그대로 두고 이 콘텐츠만 뺍니다 · 아래 ‘토픽에서 제외한 콘텐츠’에서 복구할 수 있어요', { ok: '제외', danger: true }))) return;
        try {
          const r = await this._studioPost({ action: 'exclude', id: this.drillData.topic_id, hash: c.hash, title: c.title || '', topic: this.drillData.value || '' });
          if (r && r.error) { this._err(r.error); return; }
          this.topicData = r; this._syncTopicSettings();
          this.drillData.items = this.drillData.items.filter(x => x.hash !== c.hash);
          this.drillData.n = this.drillData.items.length;
        } catch (e) { this._err('제외 실패'); }
      },
      async topicRestore(tid, h) {
        try {
          const r = await this._studioPost({ action: 'restore', id: tid, hash: h });
          if (r && r.error) { this._err(r.error); return; }
          this.topicData = r; this._syncTopicSettings();
        } catch (e) { this._err('복구 실패'); }
      },
      exclusionRows() {                                  // 관리 패널: {토픽id: [항목]} → 최신순 평탄화
        const ex = (this.topicData && this.topicData.exclusions) || {}; const out = [];
        for (const tid of Object.keys(ex)) for (const e of (ex[tid] || [])) {
          const h = typeof e === 'string' ? e : ((e && e.h) || '');
          if (h) out.push({ tid, h, title: (e && e.title) || '', topic: (e && e.topic) || tid, ts: (e && e.ts) || 0 });
        }
        out.sort((a, b) => b.ts - a.ts); return out;
      },
      qexN() { const d = this.topicData; return (d && d.n_eligible != null) ? Math.max(0, (d.n_contents || 0) - d.n_eligible) : 0; },
      async loadDict() { this.modBusy = true; try { const r = await this._afetch('/dict'); const d = await r.json(); if (r.ok && d && !d.error && d.serviceGroups) { this.dictData = d; if (!this.dictGroup) this.dictGroup = (d.serviceGroups || [])[0] || ''; } } catch (e) {} this.modBusy = false; },
      // 성공 응답(사전 본문)일 때만 dictData 반영 · 401 등 오류 본문을 넣으면 dictData.iabMap 등이
      // undefined 라 Object.keys() 마운트 크래시로 본문 전체가 안 뜬다(초기 토큰 준비 전 /dict 401 레이스·토큰 만료).
      // ── 엔티티 사전(별도 메뉴): 목록·필터·수동 편집(사람 확정)·위키데이터/나무위키 보강 ──
      entData: null, entQ: '', entType: '', entStatus: '', entMsg: '', entAddName: '',
      entEdit: null, entEditAliases: [], entEditContents: [], entEditMsg: '', entAliasInput: '',
      _entPollT: null,
      _entProgress(s) { return (s.done || 0) + '/' + (s.total || 0) + ' · 매칭 ' + (s.hit || 0) + ' · 미등재 ' + (s.miss || 0) + ((s.fail || 0) ? ' · 실패 ' + s.fail : ''); },
      async loadEntdict(silent) {
        if (!silent) this.modBusy = true;
        try {
          const p = new URLSearchParams();
          if (this.entQ.trim()) p.set('q', this.entQ.trim());
          if (this.entType) p.set('type', this.entType);
          if (this.entStatus) p.set('status', this.entStatus);
          this.entData = await (await this._afetch('/entdict?' + p.toString())).json();
        } catch (e) {}
        if (!silent) this.modBusy = false;
        // 일괄 보강 진척: 실행 중이면 1.5초 폴링으로 목록·카운트 실시간 갱신(수동 새로고침 불필요)
        const s = (this.entData && this.entData.enrich) || {};
        clearTimeout(this._entPollT);
        if (s.running) {
          this.entMsg = '일괄 보강 진행 중 · ' + this._entProgress(s);
          this._entPollT = setTimeout(() => this.loadEntdict(true), 1500);
        } else if (this._entPollT !== null && s.total) {
          this._entPollT = null;
          this.entMsg = '일괄 보강 완료 · ' + this._entProgress(s);
        }
      },
      async entAction(body) {
        // 호출부가 먼저 busy 메시지를 세팅하므로 여기서 예외가 새면 '저장 중…'이 영구 고정된다
        try {
          const r = await this._afetch('/entdict', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(body) });
          return await r.json();
        } catch (e) { return { ok: false, error: '네트워크 오류' }; }
      },
      entTypeLabel(t) { return t ? (((this.entData && this.entData.meta.types[t]) || t) + ' · ' + t) : '보류'; },
      entSrc(e) {   // 마지막 보강 소스(출처 컬럼): namuwiki | wikidata | ''(미조회·미스·동음이의)
        const s = e && e.attr_meta && e.attr_meta._enrich;
        return (s && s.result === 'hit') ? (s.source || '') : '';
      },
      entAttrSummary(e) {
        const a = e.attrs || {}; const out = [];
        ['gender', 'occupation', 'nationality', 'affiliation', 'org_kind', 'country', 'loc_kind', 'af_kind', 'ev_kind', 'domain'].forEach(k => { if (a[k]) out.push(a[k]); });
        return out.join(' · ');
      },
      entAttrFields() {
        const t = this.entEdit && this.entEdit.type;
        return (t && this.entData && this.entData.meta.attrFields[t]) || [];
      },
      async openEntEdit(e) {
        this.entEditMsg = ''; this.entAliasInput = '';
        const d = await this.entAction({ action: 'detail', id: e.entity_id });
        if (!d.ok) { this.entMsg = '오류: ' + (d.error || '조회 실패'); return; }
        this.entEdit = JSON.parse(JSON.stringify(d.entity));
        this.entEdit.attrs = this.entEdit.attrs || {};
        this.entEditAliases = d.aliases || []; this.entEditContents = d.contents || [];
      },
      async saveEntEdit() {
        if (!this.entEdit) return;
        this.entEditMsg = '저장 중…';
        const d = await this.entAction({ action: 'update', id: this.entEdit.entity_id, type: this.entEdit.type || '', attrs: this.entEdit.attrs || {}, alias: this.entAliasInput.trim() });
        if (!d.ok) { this.entEditMsg = '오류: ' + (d.error || ''); return; }
        this.entEdit = null; this.entMsg = '저장됨 · 수동 확정 필드는 재보강이 덮어쓰지 않습니다'; this.loadEntdict(true);
        if (this.detailOpen) this.loadEntLookup();       // 검수 상세에서 열었을 때 뱃지 정보 갱신
      },
      async entEnrich(e) {
        this.entMsg = '보강 중… (' + e.name + ')';
        const d = await this.entAction({ action: 'enrich', id: e.entity_id });
        this.entMsg = !d.ok ? ('오류: ' + (d.error || ''))
          : (d.matched ? ('매칭됨 · ' + (d.qid || (d.source === 'namuwiki' ? '나무위키' : '')) + (d.type ? (' · 타입 ' + d.type) : ''))
          : (d.ambiguous ? '동음이의 문서 · 자동 결정 없이 보류(수동 편집으로 확정)' : '위키데이터·나무위키 미등재 · 보류 유지(수동 편집으로 확정 가능)'));
        this.loadEntdict();
      },
      async entEnrichAll(scope) {
        if (scope === 'all' && !(await this.dsConfirm('전체 개체를 재보강할까요? 나무위키 우선으로 다시 조회하며, 수동 확정 필드는 보존됩니다.', { ok: '전체 재보강' }))) return;
        const d = await this.entAction({ action: 'enrich_pending', scope: scope || '' });
        if (!d.ok) { this.entMsg = '오류: ' + (d.error || ''); return; }
        if (d.mock) { this.entMsg = 'mock 모드 · 네트워크 보강은 생략됩니다'; return; }
        if (d.already_running) { this.loadEntdict(true); return; }        // 진행 중이면 진척 표시에 합류
        if (!d.queued) { this.entMsg = scope === 'all' ? '재보강할 개체가 없습니다' : '보강할 미조회 개체가 없습니다'; return; }
        this.entMsg = '일괄 보강 시작 · 0/' + d.queued;
        this.loadEntdict(true);                                           // 폴링 시작(진척 자동 갱신)
      },
      async entBackfill() {
        if (!(await this.dsConfirm('적재된 콘텐츠의 엔티티를 사전에 색인할까요? 신규 개체는 위키데이터(미스 시 나무위키)로 백그라운드 보강됩니다.', { ok: '색인' }))) return;
        this.entMsg = '색인 중…';
        const d = await this.entAction({ action: 'backfill' });
        this.entMsg = d.ok ? ('색인 완료 · 콘텐츠 ' + d.scanned + '건 스캔 · 신규 개체 ' + d.created + ' · 링크 ' + d.linked) : ('오류: ' + (d.error || ''));
        this.loadEntdict(true);                                           // 보강이 시작됐으면 폴링이 이어받음
      },
      async entDelete(e) {
        if (!(await this.dsConfirm('“' + e.name + '” 개체를 사전에서 삭제할까요? 콘텐츠 링크도 함께 삭제됩니다.', { ok: '삭제', danger: true }))) return;
        await this.entAction({ action: 'delete', id: e.entity_id });
        this.loadEntdict();
      },
      async entPurgeUnlisted() {
        const n = (this.entData && this.entData.stats && this.entData.stats.unlisted) || 0;
        if (!(await this.dsConfirm('미등재 개체 ' + n + '건을 일괄 삭제할까요? 콘텐츠 링크·별칭도 함께 삭제됩니다. 다빈도 개체(진짜 개체 후보)는 먼저 수동 확정을 권장합니다.', { ok: '정리', danger: true }))) return;
        const d = await this.entAction({ action: 'purge_unlisted' });
        this.entMsg = d.ok ? ('미등재 ' + (d.purged || 0) + '건 정리됨') : ('오류: ' + (d.error || ''));
        this.loadEntdict();
      },
      async entAdd() {
        const name = (this.entAddName || '').trim();
        if (!name) return;
        const d = await this.entAction({ action: 'add', name });
        if (!d.ok) { this.entMsg = '오류: ' + (d.error || ''); return; }
        this.entAddName = ''; this.entMsg = '등재됨(보류) · 행의 보강 버튼으로 위키데이터 조회'; this.loadEntdict();
      },
      // 사전·정책 편집(사용자 직접 수정)
      editT: null, editKey: null, editKind: 'list', editVal: '', editTitle: '', editMsg: '', editExtra: '', editFilter: '',
      startEdit(target, key, value, kind, title) {
        this.editT = target; this.editKey = key; this.editKind = kind || 'list'; this.editTitle = title || target; this.editMsg = ''; this.editExtra = ''; this.editFilter = '';
        this.editVal = (kind === 'text') ? (value || '') : (Array.isArray(value) ? value.join('\n') : '');
      },
      startEditLegal(code, v) { this.startEdit('legal_types', code, (v && v.label) || '', 'text', '법령 · ' + code); this.editExtra = (v && v.article) || ''; },
      startEditIntake(type, row) { this.startEdit('intake_policy', type, (row && row.method) || '', 'intake', '인입 정책 · ' + type); this.editFilter = (row && row.filter) || ''; this.editExtra = (row && row.status) || ''; },
      cancelEdit() { this.editT = null; this.editMsg = ''; },
      async saveEdit() {
        let value = this.editKind === 'text' || this.editKind === 'intake' ? this.editVal : this.editVal.split('\n').map(s => s.trim()).filter(Boolean);
        if (this.editT === 'legal_types') value = { label: this.editVal, article: this.editExtra };
        if (this.editT === 'intake_policy') value = { filter: this.editFilter, method: this.editVal, status: this.editExtra };
        const body = { target: this.editT, value }; if (this.editKey != null) body.key = this.editKey;
        this.editMsg = '저장 중…';
        try {
          const r = await this._afetch('/dict', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(body) });
          const d = await r.json();
          if (d.error) { this.editMsg = '오류: ' + d.error; return; }
          this.dictData = d; this.editT = null;
        } catch (e) { this.editMsg = '오류: ' + e; }
      },
      async resetDict() {
        if (!(await this.dsConfirm('사전 편집을 모두 초기화할까요? (베이스 사전은 재시작 시 완전 복원)', { ok: '초기화', danger: true }))) return;
        try { const r = await this._afetch('/dict', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ reset: true }) }); const d = await r.json(); if (r.ok && d && !d.error && d.serviceGroups) this.dictData = d; } catch (e) {}
      },
      async loadUser() { this.loadMem(); this.modBusy = true; try { this.userData = await (await this._afetch('/usermeta', { headers: this.authToken ? { 'Authorization': 'Bearer ' + this.authToken } : {} })).json(); } catch (e) {} this.modBusy = false; },
      async uploadUserLog(e) {
        const f = e.target.files[0]; e.target.value = ''; if (!f) return;
        this.modBusy = true; this.pfMsg = '';
        try { const fd = new FormData(); fd.append('file', f);
          const d = await (await this._afetch('/usermeta', { method: 'POST', headers: this.authToken ? { 'Authorization': 'Bearer ' + this.authToken } : {}, body: fd })).json();
          if (d.error) { this.pfMsg = '오류: ' + d.error; } else { this.userData = d; this.pfMsg = this._genMsg(d); } }
        catch (err) {} this.modBusy = false;
      },
      // 실험실 · 사용자: 프로필(사용자 메타) 입력 → 행동 로그와 모이면 서버가 페르소나 능동 생성
      pf: { user_id: '', age_band: '', interests: '', day_part: '' }, pfMsg: '',
      _genMsg(d) {
        if (d.generated_n) return '생성 페르소나 ' + d.generated_n + '개 · 재료가 모인 사용자는 자동 생성됩니다';
        if ((d.profiles_n || 0) && !(d.users || []).length) return '행동 로그가 연결되면 자동 생성됩니다';
        if (!(d.profiles_n || 0) && (d.users || []).length) return '프로필이 저장되면 자동 생성됩니다';
        return '';
      },
      async saveProfile() {
        if (!this.pf.user_id.trim()) { this.pfMsg = 'user_id 를 입력하세요'; return; }
        this.modBusy = true; this.pfMsg = '';
        try {
          const d = await (await this._afetch('/usermeta-profiles', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ profile: this.pf }) })).json();
          if (d.error) { this.pfMsg = '오류: ' + d.error; }
          else { this.userData = d; this.pf = { user_id: '', age_band: '', interests: '', day_part: '' }; this.pfMsg = '프로필 저장됨' + (this._genMsg(d) ? ' · ' + this._genMsg(d) : ''); }
        } catch (e) { this.pfMsg = '오류: ' + e; }
        this.modBusy = false;
      },
      async uploadProfiles(e) {
        const f = e.target.files[0]; e.target.value = ''; if (!f) return;
        this.modBusy = true; this.pfMsg = '';
        try { const fd = new FormData(); fd.append('file', f);
          const d = await (await this._afetch('/usermeta-profiles', { method: 'POST', headers: this.authToken ? { 'Authorization': 'Bearer ' + this.authToken } : {}, body: fd })).json();
          if (d.error) { this.pfMsg = '오류: ' + d.error; }
          else { this.userData = d; this.pfMsg = (d.saved || 0) + '명 저장됨' + (this._genMsg(d) ? ' · ' + this._genMsg(d) : ''); }
        } catch (err) { this.pfMsg = '오류: ' + err; }
        this.modBusy = false;
      },
      // ── 실험실 · 사용자: 파일 기반 메모리(설계 실험 구동) ──
      // 소비 시연(memConsume)이 그 턴에 서버 자동 기록 → 응답의 wrote 로 방금 기록분을
      // 하이라이트·로그에 실시간 반영. 수동 조작은 전체 쓰기(버전 토큰)·끝에 추가·삭제.
      memData: null, memSel: '', memDraft: '', memVer: 0, memLine: '', memMsg: '',
      memNew: { kind: 'topics', name: '' }, memInject: false, memFlash: '', memLog: [], memBusy: false,
      async loadMem() { try { const d = await (await this._afetch('/usermeta-memory', { headers: this.authToken ? { 'Authorization': 'Bearer ' + this.authToken } : {} })).json(); if (d && !d.error) this.memData = d; } catch (e) {} },
      memFile(p) { return ((this.memData && this.memData.files) || []).find(f => f.path === p) || null; },
      memOpen(p) { const f = this.memFile(p); if (!f) return; this.memSel = p; this.memDraft = f.content; this.memVer = f.ver; this.memMsg = ''; },
      async memPost(body, okMsg) {
        this.memBusy = true; this.memMsg = '';
        try {
          const d = await (await this._afetch('/usermeta-memory', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(body) })).json();
          if (!d || d.error) { this.memMsg = '오류: ' + (d ? d.error : '응답 없음'); this.memBusy = false; return null; }
          this.memData = d;
          if (d.wrote) {
            this.memLog.unshift(d.wrote); this.memLog = this.memLog.slice(0, 4);
            this.memFlash = d.wrote.path; setTimeout(() => { this.memFlash = ''; }, 1600);
          }
          if (this.memSel) { const f = this.memFile(this.memSel); if (f) { this.memDraft = f.content; this.memVer = f.ver; } else { this.memSel = ''; this.memDraft = ''; } }
          if (okMsg) this.memMsg = okMsg;
          this.memBusy = false; return d;
        } catch (e) { this.memMsg = '오류: ' + e; this.memBusy = false; return null; }
      },
      async memConsume(c, action) { const d = await this.memPost({ op: 'consume', idx: c.idx, action }); if (d && d.wrote) this.memOpen(d.wrote.path); },
      memTemplate(p) {
        return '---\n파일: /' + p + '\n설명: 한 줄 설명을 적어 주세요\n출처: 직접 입력\n별칭: \n---\n- [stated] 사용자가 직접 말한 사실만 한 줄씩 적습니다\n';
      },
      async memCreate() {
        const k = this.memNew.kind; let p = k;
        if (k !== 'profile.md' && k !== 'preferences.md') {
          const n = (this.memNew.name || '').trim();
          if (!n) { this.memMsg = '파일 이름을 입력하세요 (한글·영문·숫자·-_)'; return; }
          p = k + '/' + n + (n.endsWith('.md') ? '' : '.md');
        }
        if (this.memFile(p)) { this.memOpen(p); return; }
        const d = await this.memPost({ op: 'write', path: p, content: this.memTemplate(p) }, '파일 생성됨 · /' + p);
        if (d) { this.memNew.name = ''; this.memOpen(p); this.memMsg = '파일 생성됨 · /' + p; }
      },
      async memSave() { if (this.memSel) await this.memPost({ op: 'write', path: this.memSel, content: this.memDraft, ver: this.memVer }, '저장됨 (전체 쓰기)'); },
      async memAppendLine() {
        const t = (this.memLine || '').trim(); if (!t || !this.memSel) return;
        const line = t.indexOf('- [') === 0 ? t : '- [stated] ' + t;
        const d = await this.memPost({ op: 'append', path: this.memSel, line }, '끝에 추가됨');
        if (d) this.memLine = '';
      },
      async memDelete() {
        if (!this.memSel) return;
        if (!(await this.dsConfirm('/' + this.memSel + ' 파일을 삭제할까요? 삭제는 명시적 요청이 있을 때만 실행됩니다.', { ok: '삭제', danger: true }))) return;
        await this.memPost({ op: 'delete', path: this.memSel }, '삭제됨');
      },
      get qm() { return (this.result && this.result.output && this.result.output.quality_meta) || {}; },
      get lm() { return (this.result && this.result.output && this.result.output.legal_meta) || {}; },
      get tr() { return (this.result && this.result.output && this.result.output.trace) || {}; },
      routerKeyPresent(p) { return p === 'bizrouter' ? !!this.cfg.hasBizKey : p === 'timely' ? !!this.cfg.hasTimelyKey : false; },
      isRouter(p) { return p === 'bizrouter' || p === 'timely'; },
      get textReady() { return this.isRouter(this.textProvider) ? this.routerKeyPresent(this.textProvider) : !!this.cfg.hasKey; },
      // ── 모델 선택(Atelier 방식): 소스별 그룹 + 연결된 제공자만 활성 ──
      get textGroups() {
        return [
          { label: '직접 · Solar', on: !!this.cfg.hasKey, items: this.modelOptions.map((m) => ({ provider: 'solar', model: m })) },
          { label: '통합 · Timely', on: !!this.cfg.hasTimelyKey, items: this.modelCatalog.timely.text.map((m) => ({ provider: 'timely', model: m })) },
          { label: '통합 · BizRouter', on: !!this.cfg.hasBizKey, items: this.modelCatalog.bizrouter.text.map((m) => ({ provider: 'bizrouter', model: m })) },
        ];
      },
      get textValue() { return this.textProvider === 'solar' ? ('solar|' + (this.cfgModel || '')) : (this.textProvider + '|' + (this.textModel || '')); },
      onTextPick(v) {
        const i = v.indexOf('|'); const p = v.slice(0, i), m = v.slice(i + 1);
        this.textProvider = p; if (p === 'solar') this.cfgModel = m; else this.textModel = m;
        this.saveTextSlot();
      },
      optVal(provider, model) { return provider + '|' + model; },
      // ── 이미지 입력: 선택·드롭·붙여넣기·썸네일 ──
      get fileLabel() { return this.imgFiles.length ? (this.imgFiles.length + '개 선택됨') : '선택된 파일 없음'; },
      get excelLabel() { return this.excelFile ? this.excelFile.name : '선택된 파일 없음'; },
      addImages(list) {
        const imgs = Array.from(list || []).filter((f) => f.type.startsWith('image/'));
        if (!imgs.length) return;
        this.imgFiles = this.imgFiles.concat(imgs);
        this._rebuildThumbs();
        this.status = '';
      },
      _rebuildThumbs() {
        this.imgThumbs.forEach((u) => URL.revokeObjectURL(u));
        this.imgThumbs = this.imgFiles.map((f) => URL.createObjectURL(f));
      },
      onFiles(e) { this.addImages(e.target.files); e.target.value = ''; },
      onDropImages(e) { this.imgDrag = false; this.addImages(e.dataTransfer.files); },
      onPasteImages(e) {
        const items = (e.clipboardData && e.clipboardData.items) || [];
        const fs = [];
        for (const it of items) { if (it.kind === 'file') { const f = it.getAsFile(); if (f) fs.push(f); } }
        if (fs.length) { e.preventDefault(); this.addImages(fs); }
      },
      removeImage(i) {
        URL.revokeObjectURL(this.imgThumbs[i]);
        this.imgFiles.splice(i, 1); this.imgThumbs.splice(i, 1);
      },
      clearImages() { this.imgThumbs.forEach((u) => URL.revokeObjectURL(u)); this.imgFiles = []; this.imgThumbs = []; },
      onExcel(e) { this.excelFile = e.target.files[0] || null; e.target.value = ''; this.status = ''; },
      onDropExcel(e) { this.xlsDrag = false; const f = e.dataTransfer.files[0]; if (f) this.excelFile = f; },

      // ── 결과 복사 / 내보내기 ──
}));
