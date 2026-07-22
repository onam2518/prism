/* Prism 앱 조각 02 · prismApp 프로퍼티 그룹(자동 분할 · 앱 분할 6차).
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
      _afterVerdict() {                                  // 판정 직후: 자동 다음(토글) · 행 반영은 _syncFbByHash 가 담당
        if (this.histOpen) this.loadHistory();             // 이력이 열려 있으면 즉시 갱신
        if (!this.detailNav) return;
        if (this.autoNext) this.detailNextTodo();
      },
      get rawModels() { return [...new Set(((this.rawData||{}).items||[]).map((r) => r.model).filter(Boolean))]; },
      get rawSvcs() { return [...new Set(((this.rawData||{}).items||[]).map((r) => r.service).filter(Boolean))]; },
      rawGapFirst: false,                        // 부족 분류 우선 보기(능동학습: 라벨 예산을 부족 클래스로)
      // 배정 배타 노출: 지정 검수자가 있는데 내가 아니면 목록에서 숨김(미배정은 전원 노출).
      // my_id 우선(닉네임 변경에 안전) · 로컬(sqlite)은 이름 폴백 · detailAssignBlocked 과 동일 규약.
      // 생성자·슈퍼관리자(opsAdmin)는 배정 무관 전체 열람 → 배타 숨김 예외.
      assignedToOther(r) {
        if (this.opsAdmin) return false;
        const a = (r && r.assignees) || [];
        if (!a.length) return false;
        const me = (this.arenaData && this.arenaData.my_id) || this.reviewer || '';
        return a.indexOf(me) < 0;
      },
      get rawFiltered() {
        const out = (((this.rawData||{}).items)||[]).filter((r) => {
          if (this.assignedToOther(r)) return false;   // 내 배정분 + 미배정분만(타인 배정분 숨김)
          if (this.rawQ && !((r.title||'') + (r.category||[]).join(' ') + (r.reasons||[]).join(' ')).toLowerCase().includes(this.rawQ.toLowerCase())) return false;
          if (this.rawGrade && (r.grade||'') !== this.rawGrade) return false;
          if (this.rawModel && (r.model||'') !== this.rawModel) return false;
          if (this.rawSvc && (r.service||'') !== this.rawSvc) return false;
          if (this.rawRev === 'todo' && this.myVerdict(r.fb)) return false;
          if (this.rawRev === 'done' && !this.myVerdict(r.fb)) return false;
          return true;
        });
        // 안정 정렬: 부족 분류를 앞으로 올리되 그룹 안에서는 기존(최근순) 유지
        return this.rawGapFirst ? out.slice().sort((a, b) => (b.class_gap ? 1 : 0) - (a.class_gap ? 1 : 0)) : out;
      },

      // 관리자: 같은 콘텐츠를 다른 모델로 재실행(초안 재생성)
      bulkModel: '', bulkBusy: false, bulkMsg: '', bulkScope: 'pending',
      get pendingCount() { return (((this.dashData && this.dashData.contents) || []).filter((c) => !c.model)).length; },
      get questActive() { const d = this.arenaData; return !!(d && d.next_batch_at && d.next_batch_at * 1000 > Date.now()); },
      async runBulk() {
        if (this.bulkScope === 'all' && this.questActive) { this.bulkMsg = '퀘스트 진행 중 · 전체 재실행은 반영 후 가능합니다'; return; }
        const mname = this.textModel || this.cfg.model || '기본 모델';
        if (!(await this.dsConfirm((this.bulkScope === 'pending' ? ('미실행 콘텐츠 ' + this.pendingCount + '건을 ') : '모든 콘텐츠를 ') + mname + ' 로 실행합니다(건당 비용 발생 · 기존 초안은 이력 보존) · 진행할까요?', { ok: '실행' }))) return;
        this.bulkBusy = true; this.bulkMsg = '';
        try {
          this.pollIngestStatus();                   // 실행 큐 진척도 실시간
          const r = await (await this._afetch('/rerun-all', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ model: '', scope: this.bulkScope }) })).json();   // 모델 비움 = 사용 모델(기본 실행 모델)
          this.bulkMsg = r && r.ok ? (r.msg || ('✓ 완료 ' + r.done + '건' + (r.failed ? (' · 실패 ' + r.failed) : ''))) : ((r && r.error) || '실패');
          this.loadDash(); this.loadRaw();
        } catch (e) { this.bulkMsg = '일괄 실행 실패'; }
        this.bulkBusy = false;
      },
      metaResults: null, metaBusy: false,
      verRows: [], verSel: null, verSnap: null, verBusy: false,   // 버전별 지시 히스토리(표)
      dmOpen: false, dmVer: null, dmModel: 'common', dmSnap: null, dmStages: {}, dmBusy: false, dmMsg: '',   // 모델 적용 팝업
      liveMsg: '', liveSeen: {}, _es: null,
      loading: false,
      status: '',
      result: null,
      batchResult: null,
      groups: ['뉴스', '연예', '스포츠', '콘텐츠', '커뮤니티', '블로그', '음악', '동영상'],
      group: '뉴스',
      imgTitle: '', imgCaption: '',
      txtTitle: '', txtBody: '', txtUrl: '',
      imgFiles: [], imgThumbs: [], imgDrag: false,
      excelFile: null, xlsDrag: false,
      copyMsg: '',

      // 설정(키 / 모델 슬롯 / 추론강도 / 추가 지시) · 우측 설정 패널
      cfg: { hasKey: false, model: '', persisted: false, forcedMock: false, hasBizKey: false, hasTimelyKey: false, guideUrls: {} },
      cfgModel: '', cfgPersist: true, cfgBusy: false,
      keyTarget: 'bizrouter',                        // API 키 대상 선택(계층형 단일 입력)
      get keySummary() {
        const set = ['bizrouter', 'timely', 'solar'].filter((s) => this.keyState(s)).map((s) => this.keyDefs[s].label.replace(' 키', ''));
        return set.length ? '등록됨: ' + set.join(' · ') : '등록된 키가 없습니다 · 모의(mock) 모드로 동작합니다';
      },
      teamLinks: { guide: '', guide_user: '', guide_admin: '' }, tlMsg: '', tlTarget: 'guide',   // 팀 가이드 링크(시작하기 카드 바로가기)
      get tlStatus() {
        const nm = { guide: '개요', guide_user: '사용자', guide_admin: '관리자' };
        const set = Object.keys(nm).filter((k) => (this.teamLinks[k] || '').trim());
        return set.length ? '등록됨: ' + set.map((k) => nm[k]).join(' · ') : '';
      },
      models: [], modelsMsg: '',
      reasoning: 'default', systemPrompt: '', legalEnabled: false,
      availableModels: [],
      visionCandidates: [],                   // 이미지 탭 시각 슬롯 선택지(/config visionCandidates)
      reasoningOpts: [{ id: 'low', label: 'Low' }, { id: 'default', label: 'Medium' }, { id: 'high', label: 'High' }],

      // 모델 슬롯 + 스텝식 설정
      textProvider: 'solar', textModel: '',
      visionProvider: 'upstage_ie', visionModel: '',
      slotMsg: '',
      keyServices: ['bizrouter', 'timely', 'solar'],  // 라우터 카드 먼저, 직접(Solar) 뒤
      keyShow: { solar: false, bizrouter: false, timely: false },
      keyInputs: { solar: '', bizrouter: '', timely: '' },
      keyMsgs: { solar: '', bizrouter: '', timely: '' },
      keyDefs: {
        solar:     { label: 'Upstage Solar 키', ph: 'up_xxxxxxxx', has: 'hasKey', persisted: 'persisted' },
        bizrouter: { label: 'BizRouter 키', ph: 'sk-br-v1-…', has: 'hasBizKey', persisted: 'bizPersisted' },
        timely:    { label: 'Timely 키', ph: 'timely API key', has: 'hasTimelyKey', persisted: 'timelyPersisted' },
      },
      providerLabels: { solar: 'Solar', upstage_ie: 'Upstage', bizrouter: 'BizRouter', timely: 'Timely' },
      modelCatalog: {
        bizrouter: {
          text: ['openai/gpt-5.4', 'openai/gpt-5.4-mini', 'anthropic/claude-sonnet-4.6',
            'anthropic/claude-opus-4.6', 'google/gemini-2.5-pro', 'google/gemini-2.5-flash', 'deepseek/deepseek-v3.2'],
          vision: ['google/gemini-2.5-flash', 'google/gemini-2.5-pro', 'openai/gpt-5.4',
            'openai/gpt-5-mini', 'anthropic/claude-sonnet-4.6', 'anthropic/claude-opus-4.6'],
        },
        timely: {
          text: ['gpt-5.4', 'gpt-5.4-mini', 'claude-opus-4-8', 'claude-sonnet-4-6', 'claude-haiku-4-5',
            'gemini-3.5-flash', 'gemini-3.1-pro-preview', 'deepseek-v4-pro', 'deepseek-chat'],
          vision: ['gpt-5.4', 'gpt-5.4-mini', 'claude-opus-4-8', 'claude-sonnet-4-6',
            'gemini-3.5-flash', 'gemini-3.1-pro-preview'],
        },
      },

      init() {
        this.loadReviewer();                           // 검수자·토큰(localStorage) · refreshConfig 의 관리자 로드보다 먼저
        if (this.authToken) {                          // 관리자 판정을 /config 성공에 묶지 않는다(새로고침 경합 방지)
          const kick = () => { this.ensureAdmin(); this.syncProfile(); };   // 프로필 = 서버 기준(닉네임 변경 기기 간 반영)
          this.rtoken ? this.authRefresh().then(kick, kick) : kick();   // 부팅 선갱신: 만료 토큰 새로고침 케이스
        }
        setInterval(() => { if (this.rtoken && this.authToken) this.authRefresh(); }, 45 * 60 * 1000);   // 1h 만료 전 주기 연장
        this.refreshConfig();
        this.startLive();                              // 실시간 SSE 구독
        // 장기 폴백: 탭 복귀 시에도 판정이 비어 있으면 재시도(일시 실패로 사용자 메뉴만 굳는 것 방지)
        window.addEventListener('focus', () => {
          if (this.backend === 'supabase' && this.authToken && !this.adminData) this.ensureAdmin();
        });
        this.loadHome();                               // 배치된 홈 위젯(localStorage)
        this.loadDash();                               // 홈 위젯 데이터(/dashboard)
        this.loadArena();                              // 홈 = 아레나
        this.polRestore();                             // 정책 팔레트 위치·탭 복원
        // 딥링크: ?m=run|dash|quality|... 로 특정 뷰 진입(설정은 ?settings)
        try {
          const q = new URLSearchParams(location.search); const m = q.get('m');
          const dh = q.get('detail');                  // 콘텐츠 상세 딥링크(정책 팔레트 매칭 목록 → 새 탭)
          if (dh) { this._pendingDetail = dh; this._pendingRetry = false; this.selectMod('create'); this.loadRaw(2000); }
          else if (m) this.selectMod(m);
          if (q.has('settings')) this.selectMod('system');
        } catch (e) {}
        this.loadVocab();
        this.loadDict();                               // 검수 요소·인텐트 정의 등 UI 사전 선로드(/dict 단일 원천)
        // Cmd/Ctrl + Enter 로 추출 실행
        window.addEventListener('keydown', (e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !this.loading) { e.preventDefault(); this.run(); }
        });
        // 검수 상세 단축키: A=정확 · S=수정 · ←→=이전/다음 · Esc=닫기 (입력 중에는 무시)
        window.addEventListener('keydown', (e) => {
          if (!this.detailOpen) return;
          const t = e.target;
          if (t && /INPUT|TEXTAREA|SELECT/.test(t.tagName)) return;
          if (e.metaKey || e.ctrlKey || e.altKey) return;
          if (e.code === 'KeyA') { e.preventDefault(); if (this.finalMode) this.finalDecide(this.finalCtx, 'good'); else this.reviewGood(); }
          else if (e.code === 'KeyS') { e.preventDefault(); if (this.finalMode) this.finalDecide(this.finalCtx, 'bad'); else { this.openEditVerdict(); this.pendingBad = true; } }
          else if (e.code === 'ArrowRight') { e.preventDefault(); this.detailGo(1); }
          else if (e.code === 'ArrowLeft') { e.preventDefault(); this.detailGo(-1); }
          else if (e.code === 'Escape') { this.detailOpen = false; }
        });
        // 검수 목록 단축키: J/K=행 이동 · Enter=상세 열기 (콘텐츠 검수 목록에서 · 상세 닫힘 · 입력 중 무시)
        window.addEventListener('keydown', (e) => {
          if (this.detailOpen || this.mod !== 'create' || this.createTab !== 'raw') return;
          const t = e.target;
          if (t && /INPUT|TEXTAREA|SELECT/.test(t.tagName)) return;
          if (e.metaKey || e.ctrlKey || e.altKey) return;
          const list = this.rawFiltered;
          if (!list.length) return;
          if (e.code === 'KeyJ') { e.preventDefault(); this.rawFocusIdx = Math.min(list.length - 1, this.rawFocusIdx + 1); this._rawFocusScroll(); }
          else if (e.code === 'KeyK') { e.preventDefault(); this.rawFocusIdx = Math.max(0, (this.rawFocusIdx < 0 ? 1 : this.rawFocusIdx) - 1); this._rawFocusScroll(); }
          else if (e.code === 'Enter' && this.rawFocusIdx >= 0 && list[this.rawFocusIdx]) { e.preventDefault(); this.openRawDetail(list[this.rawFocusIdx]); }
        });
        // 브라우저 뒤로/앞으로 = 메뉴 이동(URL ?m= 동기화)
        window.addEventListener('popstate', (e) => {
          const m = (e.state && e.state.m) || new URLSearchParams(location.search).get('m') || 'home';
          this._noPush = true; this.selectMod(m); this._noPush = false;
        });
      },
      get modLabel() {
        if (this.mod === 'home') return '홈';
        for (const g of this.mods) for (const it of g.items) if (it.id === this.mod) return it.label;
        return '';
      },
      // 연결 현황(다중) · 여러 제공자를 동시에 넣어도 각각의 연결 상태를 표시
      get connList() {
        return [
          { id: 'solar', label: 'Solar', on: !!this.cfg.hasKey },
          { id: 'bizrouter', label: 'BizRouter', on: !!this.cfg.hasBizKey },
          { id: 'timely', label: 'Timely', on: !!this.cfg.hasTimelyKey },
        ];
      },
      get connCount() { return this.connList.filter((c) => c.on).length; },
      get modSub() {
        // 한 줄 소개(표준 · 시안 A): "이 화면에서 무엇을 합니다" 한 문장 · 쉬운 일상어 · 비유 금지
        const m = { home: '내 검수 진척과 팀 현황을 한눈에 봅니다', auto: '콘텐츠를 자동으로 받아오는 수집 소스를 설정합니다', run: '이미지·텍스트·엑셀을 수동으로 추출합니다', queue: '진행 중인 작업의 진척과 완료 이력을 봅니다', dash: '추출 결과를 집계해 봅니다', review: '검수 대기 콘텐츠를 함께 판정합니다', arena: '내 검수 진척과 팀 현황을 한눈에 봅니다', admin: '팀 멤버와 초대 코드를 관리합니다', system: '데이터 관리와 API 키·모델을 설정합니다(운영 관리자)', quality: '품질·법령 판정 결과를 봅니다', user: '행동 로그로 소비 형태·강도·선호를 봅니다', eval: '콘텐츠별 평가 피드백과 처리 이력을 봅니다', dict: '추출이 참조하는 사전·정책과 프롬프트 엔진을 관리합니다', studio: '프롬프트와 토픽(묶음 기준)을 설계합니다', prompt: '프롬프트를 선언으로 만들고 테스트해 배포합니다', intake: '콘텐츠 필터·처리 정책과 출처 분류를 설정합니다', create: '모델 초안을 판정·교정하고 모델·버전으로 비교합니다', evaluate: '정답셋 기준으로 모델을 평가하고 불일치를 판정합니다', content: '콘텐츠를 모으고 모델을 실행해 초안을 만듭니다', testset: '정답셋을 관리하고 학습 반영을 실행합니다', lab: '아직 테스트하지 않는 탐구 요소를 보관합니다', board: '기능개선 제안과 오류 제보를 남깁니다(우리 팀에만 공개)' };
        return m[this.mod] || '';
      },
      selectMod(id) {
        this.status = ''; this.addMenuOpen = false;
        // 구 메뉴 id 호환 매핑(위젯·URL): 인입류 → 콘텐츠 관리 · 검수류 → 콘텐츠 검수 · 분석/현황 → 정답셋 관리
        // 주의: 별칭 치환을 끝낸 뒤 mod 를 확정한다(치환 전 대입 시 매칭 섹션이 없어 빈 화면).
        if (id === 'intake') id = 'dict';
        if (id === 'run' || id === 'auto') { this.contentTab = id; id = 'content'; }
        if (id === 'queue') id = 'content';
        if (id === 'dash') { id = 'create'; this.createTab = 'raw'; }
        if (id === 'review') { id = 'create'; this.createTab = 'raw'; }
        if (id === 'quality') { id = 'lab'; this.labTab = 'legal'; }
        if (id === 'user') { id = 'lab'; this.labTab = 'user'; }
        if (id === 'prompt') { id = 'studio'; this.studioTab = 'prompt'; }   // 구 메뉴 · 위젯·URL 호환
        if (id === 'topic') { id = 'studio'; this.studioTab = 'topic'; }     // 실험실 시절 딥링크 호환
        if (id === 'entdict') { id = 'dict'; this.dictTab = 'entity'; }      // 별도 메뉴 시절 딥링크 호환
        if (id === 'eval') id = 'evaluate';
        if (id === 'golden') id = 'testset';
        this.mod = id;
        if (!this._noPush) {                          // URL 동기화(뒤로가기·새로고침 시 현재 화면 유지)
          try { const u = new URL(location.href); u.searchParams.set('m', id); history.pushState({ m: id }, '', u); } catch (e) {}
        }
        // 메뉴별 데이터 로드: 메뉴당 1회씩만(중복 fetch 제거) · 탭 데이터는 현재 탭 것만(나머지는 탭 클릭 시 lazy)
        if (id === 'home') { this.loadArena(); this.loadDash(); }
        else if (id === 'create') { this.loadDash(); this.loadRaw(); }
        else if (id === 'evaluate') { this.loadDash(); this.loadGoldenStatus(); this.loadEvalRuns(); this.loadPilot(); }
        else if (id === 'arena') this.loadArena();
        else if (id === 'board') this.loadBoard();
        else if (id === 'admin' || id === 'system') this.loadAdmin();
        else if (id === 'testset') { this.loadGoldenStatus(); this.loadLearnReport(); this.loadGoldenList(); this.loadLearnData(); this.loadAdmin(); this.loadActivity(); this.loadCost(); }
        else if (id === 'lab') { this.loadDash(); this.loadUser(); }
        else if (id === 'dict') {
          this.loadDict();
          if (this.dictTab === 'entity') this.loadEntdict();
          else if (this.dictTab === 'prompt') this.loadStageDrafts();
          else if (this.dictTab === 'engine') { this.syncWrapDraft(); this.loadPreview(); this.loadPromptDefaults(); }
        }
        else if (id === 'studio') {
          this.loadGoldenStatus();
          if (this.studioTab === 'topic') this.loadTopics();
          else if (this.promptSub === 'deploy') this.loadDeploys();      // 프롬프트 탭 = 빌더+하위(배포·라이브러리)
          else if (this.promptSub === 'library') this.loadLibrary();
        }
        else if (id === 'content') { this.loadDash(); this.loadGoldenStatus(); this.loadDict(); this.fetchIngestStatus(); this.pollIngestStatus(); this.loadFails(); }
      },
      toggleTheme() {
        this.theme = this.theme === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', this.theme);
      },
      // ── 홈 위젯 구성(실동작 위젯만) + 직접 배치 + localStorage 영속 ──
      placed: null,                              // 배치된 위젯 id 목록(첫 방문 = 빈 배열)
      homeCatalog: [
        { id: 'launch-run', label: '새 추출 런처' },
        { id: 'launch-batch', label: '배치 결과 런처' },
        { id: 'launch-dict', label: '사전·정책 런처' },
        { id: 'metrics', label: '핵심 지표' },
        { id: 'quality', label: '품질 점수' },
        { id: 'intents', label: '인텐트 분포' },
        { id: 'categories', label: '카테고리 분포' },
        { id: 'process', label: '처리 프로세스' },
      ],
      loadHome() { try { const s = localStorage.getItem('prism_home'); this.placed = s ? JSON.parse(s) : []; } catch (e) { this.placed = []; } },
      saveHome() { try { localStorage.setItem('prism_home', JSON.stringify(this.placed || [])); } catch (e) {} },
      hasWidget(id) { return !!(this.placed && this.placed.includes(id)); },
      addWidget(id) { this.addMenuOpen = false; if (!this.placed) this.placed = []; if (!this.placed.includes(id)) { this.placed.push(id); this.saveHome(); } },
      removeWidget(id) { this.placed = (this.placed || []).filter((x) => x !== id); this.saveHome(); },
      useRecommended() { this.placed = ['launch-run', 'launch-dict', 'metrics', 'quality', 'intents']; this.saveHome(); },
      // 데이터 GET 은 운영(supabase)에서 로그인 필수(서버 게이트 · 2026-07-10) → 인증 헤더 동봉.
      // 로그인 전 401 은 JSON 으로 조용히 떨어지고, 로그인·가입 완료 시 재로드한다.
      loadVocab() { fetch('/vocab', { headers: this._authHeaders() }).then(r => r.json()).then(j => { if (j.groups && j.groups.length) this.groups = j.groups; }).catch(() => {}); },
      intentMismatch(text, service) {           // 검수자가 콘텐츠 서비스와 다른 서비스-전용 인텐트를 넣었는지(범용①·②는 제외)
        const d = this.dictData || {}; const bySvc = d.intentByService || {};
        if (!Object.keys(bySvc).length) return [];   // 사전 미로드 시 경고 안 함
        const key = (d.serviceKeyMap || {})[service] || service;
        const uni = new Set([].concat(d.intentUniversal || [], d.intentForm || []));
        const own = new Set(bySvc[key] || []);
        const owner = {};                        // 서비스-전용 값 → 소속 서비스들
        Object.keys(bySvc).forEach(s => (bySvc[s] || []).forEach(val => { (owner[val] = owner[val] || []).push(s); }));
        return (text || '').split(',').map(s => s.trim()).filter(Boolean)
          .filter(t => !uni.has(t) && !own.has(t) && owner[t] && owner[t].indexOf(key) < 0);
      },
      async loadDash() { this.modBusy = true; try { const r = await this._afetch('/dashboard'); const d = await r.json(); if (r.ok && d && !d.error) this.dashData = d; } catch (e) {} this.modBusy = false; },
      // _afetch 사용: 토큰 만료 시 자동 갱신·재로그인 안내(만료를 '불러오기 실패'로 오인하던 문제) · 성공 응답만 반영
      async drill(kind, value) {
        this.drillOpen = true; this.drillBusy = true; this.drillData = { kind: kind, value: value, items: [] };
        try { this.drillData = await (await fetch('/drill?kind=' + kind + '&value=' + encodeURIComponent(value) + (this.reviewer ? '&reviewer=' + encodeURIComponent(this.reviewer) : ''), { headers: this._authHeaders() })).json(); } catch (e) { this._err('콘텐츠 목록 불러오기 실패'); }
        this.drillBusy = false;
      },
      async topicDrill(t) {                              // 토픽 → 묶인 콘텐츠(배치 결과 드릴다운과 동일 모달)
        if (!t || !t.cluster_id) return;
        this.drillOpen = true; this.drillBusy = true;
        this.drillData = { kind: 'topic', value: (t.name || t.label || t.cluster_id), items: [] };
        try { this.drillData = await (await fetch('/topic-drill?cluster=' + encodeURIComponent(t.cluster_id) + (this.reviewer ? '&reviewer=' + encodeURIComponent(this.reviewer) : ''), { headers: this._authHeaders() })).json(); } catch (e) { this._err('토픽 콘텐츠 불러오기 실패'); }
        this.drillBusy = false;
      },
      drillKindKr(k) { return k === 'intent' ? '인텐트' : k === 'category' ? '카테고리' : k === 'topic' ? '토픽' : '품질 사유'; },
      // 확인 모달(공통) · 네이티브 confirm 대체: 자동화(CDP)에서 렌더러를 블로킹하지 않고 DS 일관 유지
      confirmOpen: false, confirmTitle: '확인', confirmMsg: '', confirmOk: '진행', confirmDanger: false, _confirmResolve: null,
      dsConfirm(msg, opts) {
        opts = opts || {};
        this.confirmTitle = opts.title || '확인'; this.confirmMsg = msg;
        this.confirmOk = opts.ok || '진행'; this.confirmDanger = !!opts.danger;
        this.confirmOpen = true;
        return new Promise((resolve) => { this._confirmResolve = resolve; });
      },
      confirmAnswer(v) {
        this.confirmOpen = false;
        const r = this._confirmResolve; this._confirmResolve = null;
        if (r) r(!!v);
      },
      // 좌측 패널 보기: 추출 텍스트 ↔ 원문 페이지(iframe) · 선택은 기억, 링크 없는 항목(골드 문항 등)은 텍스트 고정
      dvcSrc: (function () { try { return localStorage.getItem('prismDetailSrc') || 'text'; } catch (e) { return 'text'; } })(),
      dvcView() { return (this.detail && this.detail.url) ? this.dvcSrc : 'text'; },
      setDvcView(v) { this.dvcSrc = v; try { localStorage.setItem('prismDetailSrc', v); } catch (e) {} },
      // 원문 배율 3단계 · 작게 50 / 보통 75 / 크게 100 · 축소하면 한 화면에 더 담긴다 · 선택은 기억
      dvcZoom: (function () { try { const z = parseInt(localStorage.getItem('prismDetailZoom') || '100', 10); return [50, 75, 100].indexOf(z) >= 0 ? z : 100; } catch (e) { return 100; } })(),
      setDvcZoom(z) { this.dvcZoom = z; try { localStorage.setItem('prismDetailZoom', String(z)); } catch (e) {} },
      // 콘텐츠 상세 스플릿뷰(공통): 어떤 목록에서든 openDetail(content) 로 진입
}));
