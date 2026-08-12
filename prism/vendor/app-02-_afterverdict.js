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
      // 배정 배타 판정: 지정 검수자가 있는데 내가 아니면 판정 불가 — 생성자·관리자도 예외 없다
      // (서버 reviewops 게이트와 같은 규약 · 직접 검수하려면 배정을 수정). 미배정은 전원 가능.
      // my_id 우선(닉네임 변경에 안전) · 로컬(sqlite)은 이름 폴백.
      assignBlocked(r) {
        const a = (r && r.assignees) || [];
        if (!a.length) return false;
        const me = (this.arenaData && this.arenaData.my_id) || this.reviewer || '';
        return a.indexOf(me) < 0;
      },
      // 배정 배타 노출: 타인 배정분은 목록에서 숨김(미배정은 전원 노출).
      // 생성자·슈퍼관리자(opsAdmin)는 배정 무관 전체 '열람' → 숨김만 예외(판정은 위 규칙 그대로).
      assignedToOther(r) { return this.opsAdmin ? false : this.assignBlocked(r); },
      // 나에게 지정된 건: 배정자 목록에 내가 있다 — assignBlocked 와 같은 기준(my_id 우선 · 이름 폴백).
      // 미배정분은 '전원 가능'이지 '내 것'이 아니므로 제외한다.
      assignedToMe(r) {
        const a = (r && r.assignees) || [];
        if (!a.length) return false;
        const me = (this.arenaData && this.arenaData.my_id) || this.reviewer || '';
        return !!me && a.indexOf(me) >= 0;
      },
      // 검수 목록 필터 체인 결과(2026-08-04 감사): getter 로 두면 바인딩(독립 반응 효과)마다
      // 전량 재계산돼 넓은 창(2000행)에서 키입력 1회당 ~8회 풀 스캔이 났다.
      // 루트 x-effect(_rawRecalc · 00-head.html)가 입력(rawData·검색·필터·판정) 변화 때
      // 한 번만 계산해 아래 일반 상태에 채우고, 바인딩들은 결과 배열만 구독한다.
      rawScoped: [],        // 검수 상태를 뺀 나머지 필터(검색·등급·모델·서비스·배정) 통과분 — 목록과 '숨김 N건' 힌트의 공통 모집단
      rawFiltered: [],      // rawScoped 에 검수 상태(rawRev)·부족 분류 우선(rawGapFirst)까지 적용한 최종 목록
      rawDoneHidden: 0,     // 기본값(미검수)이 감춘 '내가 판정 완료한' 건수 · 0 이면 힌트를 띄우지 않는다
      _rawRecalc() {
        const q = (this.rawQ || '').toLowerCase();
        const scoped = (((this.rawData||{}).items)||[]).filter((r) => {
          if (this.assignedToOther(r)) return false;   // 내 배정분 + 미배정분만(타인 배정분 숨김)
          if (this.rawMineOnly && !this.assignedToMe(r)) return false;   // 내 배정분만(미배정분 제외)
          // 미배정만(배정 전 물량 확인) · 골드 문항(가상 행)은 배정 개념이 없어 함께 제외
          if (this.rawUnassignedOnly && (this.isGoldRow(r) || ((r.assignees || []).length))) return false;
          if (q && !((r.title||'') + (r.category||[]).join(' ') + (r.reasons||[]).join(' ')).toLowerCase().includes(q)) return false;
          if (this.rawGrade && (r.grade||'') !== this.rawGrade) return false;
          if (this.rawModel && (r.model||'') !== this.rawModel) return false;
          if (this.rawSvc && (r.service||'') !== this.rawSvc) return false;
          return true;
        });
        let done = 0;                                  // 내 판정 완료분(rawRev 와 무관하게 세고 표시만 조건부)
        const out = scoped.filter((r) => {
          const v = this.myVerdict(r.fb);
          if (v) done += 1;
          if (this.rawRev === 'todo' && v) return false;
          if (this.rawRev === 'done' && !v) return false;
          return true;
        });
        this.rawScoped = scoped;
        // 안정 정렬: 부족 분류를 앞으로 올리되 그룹 안에서는 기존(최근순) 유지
        this.rawFiltered = this.rawGapFirst ? out.slice().sort((a, b) => (b.class_gap ? 1 : 0) - (a.class_gap ? 1 : 0)) : out;
        this.rawDoneHidden = this.rawRev === 'todo' ? done : 0;
      },
      // 상단 '필터' 버튼 배지: 기본값에서 벗어난 조건 개수(검색어는 밖에 남아 있으니 세지 않는다).
      // 0 이면 배지를 숨겨 '필터 없음'을 조용히 알린다.
      get rawFilterN() {
        return (this.rawGrade ? 1 : 0) + (this.rawSvc ? 1 : 0) + (this.rawRev !== 'todo' ? 1 : 0)
             + (this.rawMineOnly ? 1 : 0) + (this.rawUnassignedOnly ? 1 : 0) + (this.rawGapFirst ? 1 : 0);
      },
      // 초기화: 검색어(밖)는 그대로 두고 팝오버 안 조건만 기본값으로. 검수 상태는 rawRevPick 경유
      // (창 넓힘 상태를 건드리지 않고 'todo' 로 돌린다).
      rawFilterReset() {
        this.rawGrade = ''; this.rawSvc = ''; this.rawMineOnly = false; this.rawUnassignedOnly = false;
        this.rawGapFirst = false;
        if (this.rawRev !== 'todo') this.rawRevPick('todo');
      },
      rawShown: 200,                             // 검수 표 표시 캡(더 보기 증분) · 크루 탭 캡 200 과 동일 규약
      // 렌더 전용 절단본: 2000행 전부를 DOM 에 그리지 않는다(보이는 건 스크롤 박스 10여 행).
      // 건수 표시·일괄 작업·상세 이전/다음은 rawFiltered(전체)를 그대로 쓴다.
      get rawShownList() { return this.rawFiltered.slice(0, this.rawShown); },
      async rawSelToggle(r) {                      // JSON 원문 보기: 슬림 목록엔 메타 원본이 없어 단건 조회로 채운다
        if (this.rawSel && this.rawSel.hash === r.hash) { this.rawSel = null; return; }
        let d = r;
        if (!r.item_meta) {
          try {
            const j = await (await this._afetch('/raw-detail?hash=' + encodeURIComponent(r.hash))).json();
            if (j && j.ok && j.item) d = Object.assign({}, r, j.item);
          } catch (e) {}
        }
        this.rawSel = d;
      },
      // 관리자: 같은 콘텐츠를 다른 모델로 재실행(초안 재생성) · 모델은 항상 서버 기본 실행 모델
      // (모델 피커 UI 는 2026-07-06 커밋 22ed957 로 제거 — 구 bulkModel 상태도 함께 정리)
      bulkBusy: false, bulkMsg: '', bulkScope: 'pending',
      // 전체 기준 건수는 서버가 준다(dashData.contents 는 표시용 최신 200건이라 세면 안 된다 ·
      // 창 기준으로 세던 때 미실행 200건이 창 밖이라 '0건'으로 보이고 실행이 막혔다)
      get pendingCount() { return (this.dashData && this.dashData.pending_n) || 0; },
      get contentsCount() { return (this.dashData && this.dashData.contents_n) || 0; },
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
      liveMsg: '', _es: null,
      loading: false,
      status: '',
      result: null,
      batchResult: null,
      groups: ['뉴스', '연예', '스포츠', '콘텐츠', '커뮤니티', '블로그', '음악', '동영상'],
      group: '뉴스',
      txtTitle: '', txtBody: '', txtUrl: '',
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
      reasoning: 'default', legalEnabled: false,
      availableModels: [],
      visionCandidates: [],                   // 이미지 탭 시각 슬롯 선택지(/config visionCandidates)
      reasoningOpts: [{ id: 'low', label: 'Low' }, { id: 'default', label: 'Medium' }, { id: 'high', label: 'High' }],

      // 모델 슬롯 + 스텝식 설정
      textProvider: 'solar', textModel: '',
      slotMsg: '',
      keyShow: { solar: false, bizrouter: false, timely: false },
      keyInputs: { solar: '', bizrouter: '', timely: '' },
      keyMsgs: { solar: '', bizrouter: '', timely: '' },
      keyDefs: {
        solar:     { label: 'Upstage Solar 키', ph: 'up_xxxxxxxx', has: 'hasKey', persisted: 'persisted' },
        bizrouter: { label: 'BizRouter 키', ph: 'sk-br-v1-…', has: 'hasBizKey', persisted: 'bizPersisted' },
        timely:    { label: 'Timely 키', ph: 'timely API key', has: 'hasTimelyKey', persisted: 'timelyPersisted' },
      },
      modelCatalog: {
        bizrouter: {
          text: ['openai/gpt-5.4', 'openai/gpt-5.4-mini', 'anthropic/claude-sonnet-4.6',
            'anthropic/claude-opus-4.6', 'google/gemini-2.5-pro', 'google/gemini-2.5-flash', 'deepseek/deepseek-v3.2'],
          vision: ['google/gemini-2.5-flash', 'google/gemini-2.5-pro', 'openai/gpt-5.4',
            'openai/gpt-5-mini', 'anthropic/claude-sonnet-4.6', 'anthropic/claude-opus-4.6'],
        },
        // 2026-07-29 라우터 이전(api.timelyrouter.ai) 시 GET /v1/models 실목록으로 맞춤.
        // deepseek-chat 은 목록에서 빠져 제거 · Solar 는 라우터 경유로도 쓸 수 있어 합류.
        timely: {
          text: ['claude-opus-5', 'claude-opus-4-8', 'claude-opus-4-7', 'claude-sonnet-5',
            'claude-sonnet-4-6', 'claude-haiku-4-5', 'claude-fable-5',
            'gpt-5.6-sol', 'gpt-5.4', 'gpt-5.4-mini', 'gpt-5.4-nano',
            'gemini-3.5-flash', 'gemini-3.1-pro-preview', 'gemini-3.1-flash-lite',
            'solar-pro3', 'solar-open2', 'solar-pro2',
            'deepseek-v4-pro', 'kimi-k3', 'glm-5.2', 'qwen3-235b-a22b'],
          vision: ['claude-opus-5', 'claude-opus-4-8', 'claude-sonnet-5', 'claude-sonnet-4-6',
            'gpt-5.4', 'gpt-5.4-mini', 'gemini-3.5-flash', 'gemini-3.1-pro-preview'],
        },
      },

      init() {
        this.loadReviewer();                           // 검수자·토큰(localStorage) · refreshConfig 의 관리자 로드보다 먼저
        if (this.authToken) {                          // 관리자 판정을 /config 성공에 묶지 않는다(새로고침 경합 방지)
          // 주간 본인 확인: 주차가 넘어간 뒤 첫 로그인에서 한 번 받는다(확인 전 진행 차단)
          const kick = () => { this.ensureAdmin(); this.syncProfile(); this.checkWeekConfirm(); };   // 프로필 = 서버 기준(닉네임 변경 기기 간 반영)
          this.rtoken ? this.authRefresh().then(kick, kick) : kick();   // 부팅 선갱신: 만료 토큰 새로고침 케이스
        }
        setInterval(() => { if (this.rtoken && this.authToken) this.authRefresh(); }, 45 * 60 * 1000);   // 1h 만료 전 주기 연장
        this.refreshConfig();
        this.startLive();                              // 실시간 SSE 구독
        // 장기 폴백: 탭 복귀 시에도 판정이 비어 있으면 재시도(일시 실패로 사용자 메뉴만 굳는 것 방지)
        window.addEventListener('focus', () => {
          if (this.backend === 'supabase' && this.authToken && !this.adminData) this.ensureAdmin();
        });
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
          // 서브뷰 딥링크(?view=): 메뉴 단위까지만 있어 위키·문서에서 특정 화면을 걸 수 없었다.
          // 사용자 탭 서브뷰(시연·생성 과정·정책·로그뷰어)를 주소로 지정한다.
          const vw = q.get('view');
          if (vw && this.mod === 'lab' && ['run', 'gen', 'policy', 'viewer'].indexOf(vw) >= 0) {
            this.labTab = 'user';                      // 이 서브뷰들은 사용자 탭에만 있다
            this.labUserView = vw;
            if (vw === 'viewer') this.loadLogViewer();
          }
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
          const list = this.rawShownList;        // 키보드 이동은 실제 렌더된 행(표시 캡) 안에서만
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
        const m = { home: '내 검수 진척과 팀 현황을 한눈에 봅니다', auto: '콘텐츠를 자동으로 받아오는 수집 소스를 설정합니다', run: '이미지·텍스트·엑셀을 수동으로 추출합니다', queue: '진행 중인 작업의 진척과 완료 이력을 봅니다', dash: '추출 결과를 집계해 봅니다', review: '검수 대기 콘텐츠를 함께 판정합니다', arena: '내 검수 진척과 팀 현황을 한눈에 봅니다', admin: '팀 멤버와 검수 인력의 일정을 관리합니다', system: '데이터 관리와 API 키·모델을 설정합니다(운영 관리자)', quality: '품질·법령 판정 결과를 봅니다', user: '행동 로그로 소비 형태·강도·선호를 봅니다', eval: '콘텐츠별 평가 피드백과 처리 이력을 봅니다', dict: '추출이 참조하는 사전·정책과 프롬프트 엔진을 관리합니다', studio: '프롬프트와 토픽(묶음 기준)을 설계합니다', prompt: '프롬프트를 선언으로 만들고 테스트해 배포합니다', intake: '콘텐츠 필터·처리 정책과 출처 분류를 설정합니다', create: '모델 초안을 판정·교정하고 모델·버전으로 비교합니다', evaluate: '정답셋 기준으로 모델을 평가하고 불일치를 판정합니다', content: '콘텐츠를 모으고 모델을 실행해 초안을 만듭니다', testset: '정답셋을 관리하고 학습 반영을 실행합니다', lab: '아직 테스트하지 않는 탐구 요소를 보관합니다', board: '기능개선 제안과 오류 제보를 남깁니다(우리 팀에만 공개)' };
        return m[this.mod] || '';
      },
      selectMod(id) {
        this.status = '';
        // 구 메뉴 id 호환 매핑(위젯·URL): 인입류 → 콘텐츠 관리 · 검수류 → 콘텐츠 검수 · 분석/현황 → 정답셋 관리
        // 주의: 별칭 치환을 끝낸 뒤 mod 를 확정한다(치환 전 대입 시 매칭 섹션이 없어 빈 화면).
        if (id === 'intake') id = 'dict';
        if (id === 'run' || id === 'auto') { this.contentTab = id; id = 'content'; }
        if (id === 'queue') id = 'content';
        if (id === 'dash') { id = 'create'; this.createTab = 'raw'; }
        if (id === 'review') { id = 'create'; this.createTab = 'raw'; }
        if (id === 'quality') { id = 'lab'; this.labTab = 'legal'; }
        if (id === 'user') { id = 'lab'; this.labTab = 'user'; }
        if (id === 'spectrum') { id = 'lab'; this.labTab = 'spectrum'; this.spLoad(); }   // 사내 MCP 허브(실험실 탭) 딥링크
        if (id === 'prompt') { id = 'studio'; this.studioTab = 'prompt'; }   // 구 메뉴 · 위젯·URL 호환
        if (id === 'topic') { id = 'studio'; this.studioTab = 'topic'; }     // 실험실 시절 딥링크 호환
        if (id === 'entdict') { id = 'dict'; this.dictTab = 'entity'; }      // 별도 메뉴 시절 딥링크 호환
        if (id === 'crew') { id = 'admin'; this.adminTab = 'crew'; }   // 검수운영은 운영 관리의 탭으로 통합(딥링크 호환)
        if (id === 'eval') id = 'evaluate';
        if (id === 'golden') id = 'testset';
        this.mod = id;
        if (!this._noPush) {                          // URL 동기화(뒤로가기·새로고침 시 현재 화면 유지)
          try { const u = new URL(location.href); u.searchParams.set('m', id); history.pushState({ m: id }, '', u); } catch (e) {}
        }
        // 메뉴별 데이터 로드: 메뉴당 1회씩만(중복 fetch 제거) · 탭 데이터는 현재 탭 것만(나머지는 탭 클릭 시 lazy)
        // 메뉴 왕복 재조회 억제(2026-08-11): 여러 메뉴가 공유하는 로더는 4초 스로틀 변형을 쓴다.
        // SSE(app-04 startLive)가 판정·변경 시 같은 스로틀 로더를 이미 부르므로 신선도는 그쪽이 지킨다.
        // 예외 — crew 의 loadRaw(:아래)는 배정 직후 최신 후보 풀이 목적이라 스로틀 없이 그대로 둔다.
        if (id === 'home') { this.loadArenaThrottled(); this.loadDashThrottled(); }
        else if (id === 'create') { this.loadDashThrottled(); this.loadRawThrottled(); }
        else if (id === 'evaluate') { this.loadDashThrottled(); this.loadGoldenStatusThrottled(); this.loadEvalRuns(); this.loadPilot(); }
        else if (id === 'arena') this.loadArenaThrottled();
        else if (id === 'board') this.loadBoard();
        else if (id === 'admin' || id === 'system') {
          this.loadAdmin();
          if (id === 'system') this.mkLoad();          // 시스템 설정 = 자격증명 자리(API 키 · MCP 파트너 키)
          // 볼 수 없는 탭이 선택돼 있으면 빈 화면이 된다 · 접근 가능한 탭으로 옮긴다
          if (id === 'admin') {
            if (this.adminTab === 'team' && !this.canTeamTab && this.canCrewTab) this.adminTab = 'crew';
            else if (this.adminTab === 'crew' && !this.canCrewTab && this.canTeamTab) this.adminTab = 'team';
            if (this.adminTab === 'crew') { this.loadCrew(); this.loadRaw(); }   // 후보 풀 = 기본 창(2000) 전체 · 배정 직후 최신화가 목적이라 스로틀 제외
          }
        }
        else if (id === 'testset') { this.loadGoldenStatusThrottled(); this.loadLearnReport(); this.loadGoldenList(); this.loadLearnData(); this.loadAdmin(); this.loadActivity(); this.loadCost(); }
        else if (id === 'lab') { this.loadDashThrottled(); this.loadUser(); }
        else if (id === 'dict') {
          if (!this.dictData) this.loadDict();          // /dict 는 세션 중 사실상 불변(36KB) · 편집·초기화는 응답으로 dictData 를 직접 갱신한다
          if (this.dictTab === 'entity') this.loadEntdict();
          else if (this.dictTab === 'prompt') this.loadStageDrafts();
          else if (this.dictTab === 'engine') { this.syncWrapDraft(); this.loadPreview(); this.loadPromptDefaults(); }
        }
        else if (id === 'studio') {
          this.loadGoldenStatusThrottled();
          if (this.studioTab === 'topic') this.loadTopics();
          else if (this.promptSub === 'deploy') this.loadDeploys();      // 프롬프트 탭 = 빌더+하위(배포·라이브러리)
          else if (this.promptSub === 'library') this.loadLibrary();
        }
        else if (id === 'content') { this.loadDashThrottled(); this.loadGoldenStatusThrottled(); if (!this.dictData) this.loadDict(); this.fetchIngestStatus(); this.pollIngestStatus(); this.loadFails(); }
      },
      toggleTheme() {
        this.theme = this.theme === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', this.theme);
      },
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
      async loadDash() { this.modBusy = true; try { const r = await this._afetch('/dashboard'); const d = await r.json(); if (r.ok && d && !d.error) this.dashData = d; } catch (e) { this._err('대시보드 불러오기 실패 · 네트워크 확인 후 새로고침 해주세요'); } this.modBusy = false; },
      // _afetch 사용: 토큰 만료 시 자동 갱신·재로그인 안내(만료를 '불러오기 실패'로 오인하던 문제) · 성공 응답만 반영
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
