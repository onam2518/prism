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
      async loadDict() { this.modBusy = true; try { const r = await this._afetch('/dict'); const d = await r.json(); if (r.ok && d && !d.error && d.serviceGroups) { this.dictData = d; if (!this.dictGroup) this.dictGroup = (d.serviceGroups || [])[0] || ''; } } catch (e) { this._err('사전 불러오기 실패 · 네트워크 확인 후 새로고침 해주세요'); } this.modBusy = false; },
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
        } catch (e) {
          // 폴링(silent)은 조용히 넘기고 다음 회차로 자력 복구 · 사용자가 부른 조회만 알린다
          if (!silent) this._err('엔티티 사전 불러오기 실패 · 네트워크 확인 후 새로고침 해주세요');
        }
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
      loadUser() { this.loadMem(); this.loadDemoLab(); },   // 실험실 · 사용자 탭 진입점(시연 + 생성 과정만 로드)
      // ── 실험실 · 사용자: 생성 과정(파일 기반 메모리) ──
      // 자동 기록은 시연(demoClose→event)이 담당 · 여기는 파일 확인·수동 조작(전체 쓰기
      // 버전 토큰 · 끝에 추가 · 삭제)과 주입 미리보기. 응답의 wrote 로 방금 기록분 하이라이트.
      memData: null, memSel: '', memDraft: '', memVer: 0, memLine: '', memMsg: '',
      memNew: { kind: 'topics', name: '' }, memInject: false, memFlash: '', memLog: [], memBusy: false,
      async loadMem() { try { const d = await (await this._afetch('/usermeta-memory', { headers: this.authToken ? { 'Authorization': 'Bearer ' + this.authToken } : {} })).json(); if (d && !d.error) this.memData = d; } catch (e) { this._err('메모리 파일 불러오기 실패'); } },
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
      // ── 실험실 · 사용자: 소비 시연(STEP 1 소비·수집 → 2 측정·로직 → 3 결론 → 4 활용) ──
      // 좌측 피드(Anchor DS)에서의 실제 행동을 이벤트로 서버에 보내고, 우측 3단(실시간·누적·로직)을
      // 응답으로 갱신. 체류는 실측 초 × 10 배속(가상 체류)으로 보내 실로직 임계값(30·45초)을 체감시킨다.
      labUserView: 'run',                           // 사용자 탭 서브뷰: run(시연) | gen(생성 과정) | policy(정책) | viewer(로그뷰어)
      demoData: null, demoReading: null, demoTick: 0, _demoTimer: null,
      demoQ: '', demoQFilter: '',
      demoVariant: 'b', demoBoardSel: '', demoCardSel: 'main',          // 시안 선택(a 피드형·b 블록형·c 보드형·d 대화형) · 이벤트 계약은 동일
      demoTheme: 'dark',                            // 시연 폰 테마(다크/라이트) · 화면 전환일 뿐 수집·측정과 무관해 리셋 없음
      demoSlug(s) { return (s || '').trim().replace(/[^0-9A-Za-z가-힣]+/g, '-').replace(/^-+|-+$/g, '').toLowerCase(); },
      demoKo(c) { return (c && (c.cat_ko || c.cat)) || ''; },
      demoCats() {                                  // 시안 A 관심 칩: 피드 주제의 사용자 표기(최대 5)
        const seen = new Set(), out = [];
        for (const c of ((this.demoData && this.demoData.contents) || [])) {
          const k = this.demoKo(c);
          if (k && !seen.has(k)) { seen.add(k); out.push(k); }
          if (out.length >= 5) break;
        }
        return out;
      },
      demoCatTap(k) { this.demoQFilter = this.demoQFilter === k ? '' : k; },   // 칩 = 조용한 필터(검색과 달리 기록 없음)
      demoLastQ() { const r = (this.demoData && this.demoData.prompts && this.demoData.prompts.recent) || []; return r.length ? r[0].q : ''; },
      demoHist() { return ((this.demoData && this.demoData.prompts && this.demoData.prompts.recent) || []).slice(0, 3); },
      demoRerun(q) { this.demoQ = q; this.demoSearch(); },
      demoWhy(c) {                                  // 시안 D 카드 설명 한 줄: 근거 하나만 · 사용자 언어
        if (this.demoQFilter) return "물어보신 '" + this.demoQFilter + "'";
        const k = this.demoKo(c);
        return k ? '자주 보는 ' + k : '새로 들어온 콘텐츠';
      },
      demoBlocks() {                                // 시안 B: 이유가 다른 블록 · 재료 없으면 블록 자체가 빠짐
        const base = this.demoFeed(), used = new Set(), out = [];
        const take = (arr, n) => { const r = []; for (const c of arr) { if (!used.has(c.idx)) { used.add(c.idx); r.push(c); if (r.length >= n) break; } } return r; };
        const lastQ = this.demoLastQ();
        if (lastQ) {
          const ls = this.demoData && this.demoData.search;
          let pool;
          if (ls && ls.q === lastQ) { const set = new Set(ls.idxs || []); pool = base.filter(c => set.has(c.idx)); }
          else { const toks = lastQ.split(/\s+/).filter(t => t.length >= 2); pool = base.filter(c => toks.some(t => (c.title || '').includes(t) || (c.cat || '').includes(t) || (c.cat_ko || '').includes(t) || (c.intent || '').includes(t) || (c.intent_ko || '').includes(t))); }
          const m = take(pool, 2);
          if (m.length) out.push({ t: "물어보신 '" + lastQ + "' 소식이에요", s: '', items: m, num: false });
        }
        const cats = (this.demoData && this.demoData.live && this.demoData.live.cats) || [];
        const inCat = (c, name) => c.cat === name || ((c.entity_categories || []).indexOf(name) >= 0);   // 가중 집계는 전체 카테고리 기준
        if (cats[0]) {
          const m = take(base.filter(c => inCat(c, cats[0].name)), 2);
          if (m.length) out.push({ t: '요즘 자주 보시는 ' + (this.demoKo(m[0]) || '주제'), s: '읽으신 기록으로 골랐어요', items: m, num: false });
        }
        if (cats[1]) {
          const m = take(base.filter(c => inCat(c, cats[1].name)), 2);
          if (m.length) out.push({ t: '관심 두시는 ' + (this.demoKo(m[0]) || '주제') + ' 소식', s: '', items: m, num: false });
        }
        const rest = take(base, 4);
        if (rest.length) out.push({ t: '지금 많이 읽히는 콘텐츠', s: '취향과 상관없이 모두에게 같아요', items: rest, num: true });
        return out;
      },
      demoBoards() {                                // 시안 C: 메모리 주제 파일 = 보드
        const files = ((this.memData && this.memData.files) || []).filter(f => f.path.indexOf('topics/') === 0);
        const cs = (this.demoData && this.demoData.contents) || [];
        return files.map(f => {
          const slug = f.path.slice(7).replace(/\.md$/, '');
          const items = cs.filter(c => this.demoSlug(c.cat) === slug);
          const label = items.length ? (this.demoKo(items[0]) || slug) : slug.replace(/-/g, ' ');   // 무결과 보드는 검색어 원형에 가깝게
          return { path: f.path, slug, label, desc: f.desc, items };
        });
      },
      // 가로 스크롤 인터랙션: 데스크톱 마우스용 드래그 스와이프 + 세로 휠 → 가로 이동
      demoDrag: null, demoDragMoved: false,
      demoDragStart(e) {
        const el = e.currentTarget;
        this.demoDrag = { el, x: e.clientX, left: el.scrollLeft, moved: false, pid: e.pointerId };
      },
      demoDragMove(e) {
        const d = this.demoDrag; if (!d) return;
        const dx = e.clientX - d.x;
        // 캡처는 드래그 판정 후에만: pointerdown 시점에 걸면 자식 버튼의 click 이 컨테이너로 재타게팅되어 칩 탭이 죽는다(Chrome)
        if (!d.moved && Math.abs(dx) > 4) {
          d.moved = true;
          if (d.el.setPointerCapture) { try { d.el.setPointerCapture(d.pid); } catch (err) {} }
        }
        if (d.moved) d.el.scrollLeft = d.left - dx;
      },
      demoDragEnd() {
        if (!this.demoDrag) return;                 // pointerup 뒤 이어지는 pointerleave 중복 호출이 억제 플래그를 지우지 않게
        this.demoDragMoved = !!this.demoDrag.moved;
        this.demoDrag = null;
        if (this.demoDragMoved) setTimeout(() => { this.demoDragMoved = false; }, 0);
      },
      demoPersonaArt(name) { return ({ '정독러': '📚', '스낵러': '🍿', '조사자': '🔍', '이중모드': '🌗', '편식러': '🎯', '팬덤': '⭐', '전환기': '🧭', '라이트': '🍃' })[name] || '👤'; },
      demoDef(name) { return ((this.demoData && this.demoData.personas) || []).find(p => p.name === name) || null; },
      demoTierCls() {                               // 신뢰도 = 카드 등급(고 골드 · 중 실버 · 저/잠정 다크)
        const p = this.demoData && this.demoData.conclusion && this.demoData.conclusion.persona;
        if (!p || p.provisional || p.conf === '저') return 'pcard--dim';
        return p.conf === '고' ? 'pcard--gold' : 'pcard--silver';
      },
      demoTierLabel() {
        const p = this.demoData && this.demoData.conclusion && this.demoData.conclusion.persona;
        if (!p) return '';
        return p.provisional ? '잠정 판정' : ('신뢰도 ' + p.conf + ' · 1순위');
      },
      demoBasis() {                                 // 판정 근거: 선택한 카드 기준으로 판정 행만 교체
        const c = this.demoData && this.demoData.conclusion;
        if (!c) return [];
        const rows = (c.basis || []).map(r => r.slice());
        if (this.demoCardSel === 'second' && c.persona && c.persona.second) {
          const d = this.demoDef(c.persona.second) || {};
          for (let i = 0; i < rows.length; i++) {
            if (rows[i][0] === '판정') rows[i] = ['판정', '참고 원형 2순위: ' + (d.full || c.persona.second) + (d.desc ? ' · ' + d.desc : '') + ' · 생성 페르소나가 이 원형과의 경계에 있습니다'];
          }
        }
        return rows;
      },
      demoStats() {                                 // 카드 스탯 = 판정 근거(깊이 · 체류 · 폭)
        const l = (this.demoData && this.demoData.live) || {};
        const depth = ({ '몰입': 90, '혼합': 55, '훑기': 25 })[(l.form || {})['깊이']] || 20;
        const dwell = Math.min(100, Math.round(((l.eng || {}).avg_dwell_sec || 0) / 60 * 100));
        const breadth = Math.round((l.breadth || 0) * 100);
        return [['깊이', depth], ['체류', dwell], ['폭', breadth]];
      },
      demoBoard() {
        const bs = this.demoBoards();
        if (!bs.length) return null;
        return bs.find(b => b.path === this.demoBoardSel) || bs[0];
      },
      demoBusy: false, demoMsg: '', demoImpressed: false, demoChainOpen: false, demoDefsOpen: false,
      _demoQueue: null, _demoInflight: 0,
      demoX: 10,                                    // 가상 체류 배속(화면에 명시)
      async loadDemoLab() { try { const d = await (await this._afetch('/usermeta-demo', { headers: this.authToken ? { 'Authorization': 'Bearer ' + this.authToken } : {} })).json(); if (d && !d.error) { this.demoData = d; this.demoImpress(); } } catch (e) { this._err('데모 실험실 불러오기 실패'); } },
      demoPost(body) {                              // 순차 큐: 서버가 세션을 통째로 읽고-쓰므로 동시 요청은 앞선 이벤트를 덮어쓴다
        this._demoInflight++; this.demoBusy = true;
        const p = (this._demoQueue || Promise.resolve()).then(() => this._demoPostNow(body))
          .finally(() => { if (--this._demoInflight <= 0) { this._demoInflight = 0; this.demoBusy = false; } });
        this._demoQueue = p.catch(() => {});   // 거부가 큐를 영구 정지시키지 않게(현재는 _demoPostNow 가 항상 fulfill)
        return p;
      },
      async _demoPostNow(body) {
        this.demoMsg = '';
        try {
          const d = await (await this._afetch('/usermeta-demo', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(body) })).json();
          if (!d || d.error) { this.demoMsg = '오류: ' + (d ? d.error : '응답 없음'); return null; }
          this.demoData = d;
          if (d.wrote) this.loadMem();               // 시연이 파일을 썼으면 결론의 파일 열람용으로 동기화
          this.$nextTick(() => { const el = this.$refs.demolog; if (el) el.scrollTop = el.scrollHeight; });
          return d;
        } catch (e) { this.demoMsg = '오류: ' + e; return null; }
      },
      demoImpress() {                               // 피드 진입 1회 → 보이는 콘텐츠 일괄 노출 이벤트
        if (this.demoImpressed || !this.demoData || !this.demoData.contents.length) return;
        this.demoImpressed = true;
        this.demoPost({ op: 'event', event: 'impression', idxs: this.demoData.contents.map(c => c.idx) });
      },
      demoVirtualDwell() { return this.demoReading ? Math.max(1, Math.round((Date.now() - this.demoReading.t0) / 1000 * this.demoX)) : 0; },
      demoOpen(c) {                                 // 카드 탭 = 클릭 이벤트 + 읽기 화면 · 체류 타이머 시작
        if (this.demoReading) return;
        this.demoReading = { idx: c.idx, t0: Date.now(), c, reacted: '', comments: [] };
        this.demoTick = 0;
        this._demoTimer = setInterval(() => { this.demoTick = this.demoVirtualDwell(); }, 300);
        this.demoPost({ op: 'event', event: 'click', idx: c.idx, from_search: !!this.demoQFilter });
      },
      async demoClose() {                           // 나가기: 체류로 정독(30초 이상)/훑기 자동 판정 → Usage 기록
        if (!this.demoReading) return;
        const idx = this.demoReading.idx, dwell = this.demoVirtualDwell();
        clearInterval(this._demoTimer); this._demoTimer = null; this.demoReading = null; this.demoCmt = '';
        const act = dwell >= 30 ? 'read' : 'skim';
        await this.demoPost({ op: 'event', event: act, idx, dwell_sec: dwell, scroll_pct: act === 'read' ? 95 : 20 });
      },
      async demoReact(emo) {                        // 감정 반응 → 피드백 Event(긍정 Like · 부정 Dislike) · 다시 누르면 감정 변경(서버가 최신 1건만 집계)
        if (!this.demoReading || this.demoReading.reacted === emo) return;
        const d = await this.demoPost({ op: 'event', event: 'react', idx: this.demoReading.idx, emotion: emo });
        if (d && this.demoReading) this.demoReading.reacted = emo;
      },
      demoCmt: '', demoFileSel: '',
      demoShowFile(f) { this.demoFileSel = this.demoFileSel === f ? '' : f; if (this.demoFileSel && !this.memFile(f.replace(/^\//, ''))) this.loadMem(); },
      demoFileBody() { const f = this.memFile((this.demoFileSel || '').replace(/^\//, '')); return f ? f.content : '불러오는 중 · 잠시 후 다시 눌러주세요'; },
      async demoComment() {                         // 댓글 → Event(표준 분류 없음 · 행동 이름 구분) · 본문은 로그 미수집 → 메모리 [stated]
        const t = (this.demoCmt || '').trim();
        if (!t || !this.demoReading) return;
        const d = await this.demoPost({ op: 'event', event: 'comment', idx: this.demoReading.idx, text: t });
        if (d && this.demoReading) { (this.demoReading.comments = this.demoReading.comments || []).push(t); this.demoCmt = ''; }
      },
      demoWhen(i) { return ['방금', '10분 전', '1시간 전', '2시간 전', '어제'][Math.min(i | 0, 4)]; },   // 예시용 시간 메타(위치 기반)
      demoFmtT(s) { const v = Math.max(0, s | 0); return Math.floor(v / 60) + ':' + ('0' + (v % 60)).slice(-2); },
      demoFeed() {                                  // 검색어 필터: 서버 매칭 결과가 정본 · 칩 필터만 로컬 판단
        const cs = (this.demoData && this.demoData.contents) || [];
        const q = (this.demoQFilter || '').trim();
        if (!q) return cs;
        const ls = this.demoData && this.demoData.search;
        if (ls && ls.q === q) { const set = new Set(ls.idxs || []); return cs.filter(c => set.has(c.idx)); }
        const toks = q.split(/\s+/).filter(t => t.length >= 2);
        if (!toks.length) return cs;
        return cs.filter(c => toks.some(t => (c.title || '').includes(t) || (c.summary || '').includes(t) || (c.cat || '').includes(t) || (c.intent || '').includes(t) || (c.cat_ko || '').includes(t) || (c.intent_ko || '').includes(t)));
      },
      async demoSearch() {                          // 콘텐츠 찾기 · Event(Search)+ViewSearchResults · 검색어는 [stated]
        const q = (this.demoQ || '').trim();
        if (!q) return;
        const d = await this.demoPost({ op: 'event', event: 'search', query: q });
        if (d) {
          this.demoQFilter = q;
          if (d.wrote && d.wrote.path) this.demoBoardSel = d.wrote.path;   // 시안 C: 방금 물어본 주제의 보드로 이동
        }
      },
      async _demoResetCore() {                      // 세션 리셋 공통부(이벤트·프롬프트 초기화 · 메모리 파일 유지)
        if (this.demoReading) { clearInterval(this._demoTimer); this._demoTimer = null; this.demoReading = null; }
        const d = await this.demoPost({ op: 'reset' });
        if (d) { this.demoImpressed = false; this.demoChainOpen = false; this.demoDefsOpen = false; this.demoQ = ''; this.demoQFilter = ''; this.demoFileSel = ''; this.demoBoardSel = ''; this.demoCardSel = 'main'; this.demoImpress(); this.loadMem(); }
        return !!d;
      },
      async demoReset() {
        if (!(await this.dsConfirm('시연 세션을 처음부터 다시 시작할까요? 수집한 이벤트와 결론이 지워집니다(메모리 파일은 유지).', { ok: '처음부터', danger: true }))) return;
        await this._demoResetCore();
      },
      async demoSwitch(v) {                         // 시안 전환 = 독립 실험: 로그·측정·결론·프롬프트 초기화
        if (v === this.demoVariant) return;
        const hasLog = !!(this.demoData && this.demoData.session && this.demoData.session.events_n);
        if (hasLog && !(await this.dsConfirm('시안을 바꾸면 지금까지의 로그 · 측정 · 결론이 초기화됩니다(시안별 독립 실험). 계속할까요?', { ok: '바꾸고 초기화', danger: true }))) return;
        this.demoVariant = v;
        if (hasLog) await this._demoResetCore();
        else { this.demoQ = ''; this.demoQFilter = ''; this.demoImpress(); }   // 리셋 없는 경로도 칩 필터 잔존 방지
      },
      get tr() { return (this.result && this.result.output && this.result.output.trace) || {}; },
      isRouter(p) { return p === 'bizrouter' || p === 'timely'; },
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
      get excelLabel() { return this.excelFile ? this.excelFile.name : '선택된 파일 없음'; },
      onExcel(e) { this.excelFile = e.target.files[0] || null; e.target.value = ''; this.status = ''; },
      onDropExcel(e) { this.xlsDrag = false; const f = e.dataTransfer.files[0]; if (f) this.excelFile = f; },

      // ── 결과 복사 / 내보내기 ──
}));
