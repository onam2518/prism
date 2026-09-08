/* Prism 앱 조각 04 · prismApp 프로퍼티 그룹(자동 분할 · 앱 분할 6차).
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
      _err(m) { this.errMsg = m; if (this._errT) clearTimeout(this._errT); this._errT = setTimeout(() => { this.errMsg = ''; }, 4800); },
      logout() {
        try { localStorage.removeItem('prism_reviewer'); localStorage.removeItem('prism_reviewer_char'); localStorage.removeItem('prism_token'); localStorage.removeItem('prism_rtoken'); } catch (e) {}
        this._loadCred();                              // 저장 선택 시 재로그인 편의(프리필 유지)
        this.reviewer = ''; this.authToken = ''; this.rtoken = ''; this.adminData = null; this.arenaData = null;
        this.dashData = null; this.rawData = null;     // 팀 데이터 잔존 제거(다음 로그인 시 재로드)
        if (this._es) { try { this._es.close(); } catch (e) {} this._es = null; }   // 구 토큰 SSE 종료
        this.mod = 'home'; this.reviewerEditing = true;
      },
      goldenMinGood: 1,
      // 검수 보조 에이전트 모델(시스템 설정) · 값·선택지 모두 서버 /config 가 원천이다.
      // 목록을 화면에서 따로 만들면 서버가 인정하지 않는 모델을 고를 수 있게 된다.
      assistModel: '', assistModels: [], assistMsg: '',
      _loadCred() {
        try {
          localStorage.removeItem('prism_cred');       // 레거시: 평문 비밀번호 저장분 제거(마이그레이션)
          const email = localStorage.getItem('prism_email');
          if (email) { this.authEmail = email; this.saveCred = true; }   // 아이디만 프리필(비밀번호는 재입력)
        } catch (e) {}
      },
      _storeCred(email, pw) {
        try {
          localStorage.removeItem('prism_cred');       // 비밀번호는 저장하지 않는다(XSS/공용PC 탈취 방지)
          if (this.saveCred) localStorage.setItem('prism_email', email);
          else localStorage.removeItem('prism_email');
        } catch (e) {}
      },
      async saveReviewer() {
        // 프로필 편집(이미 로그인): 재인증 없이 닉네임·캐릭터만 갱신(팀 재참가 안 함)
        if (this.backend === 'supabase' && this.authToken) {
          const nv = (this.reviewer || '').trim();
          if (!nv) { this.authMsg = '닉네임을 입력하세요'; return; }
          try { await this._afetch('/reviewer', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ reviewer: nv, name: nv, char: this.reviewerChar }) }); } catch (e) {}
          try { localStorage.setItem('prism_reviewer', nv); localStorage.setItem('prism_reviewer_char', this.reviewerChar); } catch (e) {}
          this.reviewerEditing = false; this.authMsg = '';
          return;
        }
        if (this.backend === 'supabase') {                          // 로그인/가입 먼저
          const email = (this.authEmail || '').trim(), pw = this.authPw || '';
          if (!email || !pw) { this.authMsg = '이메일·비밀번호를 입력하세요'; return; }
          if (this.authMode === 'signup') {
            if (pw.length < 6) { this.authMsg = '비밀번호는 6자 이상이어야 합니다'; return; }
            if (pw !== this.authPw2) { this.authMsg = '비밀번호가 일치하지 않습니다'; return; }
          }
          this.authMsg = ''; this.authBusy = true;                 // 진행 애니메이션(정보 변경 화면 노출 방지)
          let r;
          try { r = await (await fetch('/auth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: this.authMode, email: email, password: pw }) })).json(); }
          catch (e) { this.authBusy = false; this.authMsg = '네트워크 오류'; return; }
          if (!r.ok) { this.authBusy = false; this.authMsg = this._authErr(r.error, r.raw); return; }
          this._storeCred(email, pw);                               // 아이디·비밀번호 저장(선택 시 · 이 기기)
          this.authToken = r.access_token; this.rtoken = r.refresh_token || ''; this.authMsg = '';
          try { localStorage.setItem('prism_token', this.authToken); if (this.rtoken) localStorage.setItem('prism_rtoken', this.rtoken); } catch (e) {}
          if (this.authMode === 'login') {                          // 로그인: 기존 프로필 로드(재입력 없음)
            let rr; try { rr = await (await fetch('/reviewer', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ mode: 'login' }) })).json(); } catch (e) { this.authBusy = false; this.authMsg = '네트워크 오류'; return; }
            if (rr && rr.needSignup) { this.authBusy = false; this.authMode = 'signup'; this.authMsg = '가입 정보가 없습니다 · 닉네임·캐릭터·팀을 설정해 가입하세요'; return; }
            if (!rr || !rr.ok) { this.authBusy = false; this.authMsg = (rr && rr.error) || '프로필 로드 실패'; return; }
            this.reviewer = rr.name; this.reviewerChar = rr.char || this.reviewerChar;
            this._badgeSeen = Array.isArray(rr.badges) ? rr.badges : null;   // 서버 배지 기준선(기기 간)
            if (rr.team && rr.team.invite_code) { this.myInvite = rr.team.invite_code; }
            try { localStorage.setItem('prism_reviewer', this.reviewer); localStorage.setItem('prism_reviewer_char', this.reviewerChar); } catch (e) {}
            if (!this.saveCred) { this.authEmail = ''; this.authPw = ''; }   // 상태 정리(저장 시 프리필 유지)
            this.reviewerEditing = false; this.authBusy = false; this.authMsg = ''; this.refreshConfig();
            this.loadAdmin();                                        // 관리자 메뉴 게이팅 즉시 갱신
            this.loadDash(); this.loadVocab(); this.loadDict(); this.startLive();   // 로그인 전 401 이던 데이터·SSE 재개(서버 게이트)
            if (this.mod === 'arena' || this.mod === 'home') this.loadArena();
            return;
          }
        }
        // 가입(supabase) 또는 로컬: 닉네임 필수
        const v = (this.reviewer || '').trim();
        if (!v) { this.authMsg = '닉네임을 입력하세요'; return; }
        this.reviewer = v;
        try { localStorage.setItem('prism_reviewer', v); localStorage.setItem('prism_reviewer_char', this.reviewerChar); } catch (e) {}
        // 검수자 등록(이름·캐릭터 + 팀). supabase 면 서버가 Bearer 의 uid 로 귀속(사칭 불가).
        const body = { reviewer: v, name: v, char: this.reviewerChar };
        // 팀: 없음(none) 또는 코드 참가(join). 팀 생성은 관리자 메뉴 전용.
        if (this.backend === 'supabase') { body.team_mode = this.noTeam ? 'none' : 'join'; body.invite_code = this.inviteCode; }
        try {
          const rr = await (await this._afetch('/reviewer', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(body) })).json();
          if (rr && !rr.ok) { this.authBusy = false; this.authMsg = rr.error || '등록 실패'; return; }
          if (rr && rr.team && rr.team.invite_code) { this.myInvite = rr.team.invite_code; }   // 초대코드 표시
        } catch (e) {}
        if (!this.saveCred) { this.authEmail = ''; this.authPw = ''; }
        this.authPw2 = '';
        this.reviewerEditing = false; this.authBusy = false; this.refreshConfig();
        this.loadDash(); this.loadVocab(); this.loadDict(); this.startLive();       // 가입 완료 = 로그인 상태 · 데이터·SSE 재개
        if (this.mod === 'arena' || this.mod === 'home') this.loadArena();
      },
      _authErr(err, raw) {
        const s = ((err || '') + ' ' + (raw || '')).toLowerCase();
        if (s.includes('invalid') && s.includes('credential')) return '이메일 또는 비밀번호가 올바르지 않습니다';
        if (s.includes('already') || s.includes('registered') || s.includes('exists')) return '이미 가입된 이메일입니다 · 로그인해 주세요';
        if (s.includes('password')) return '비밀번호를 확인하세요(6자 이상)';
        if (s.includes('email')) return '이메일 형식을 확인하세요';
        return err || '로그인 실패';
      },
      charImg(id) { return (this.charOptions.find((c) => c.id === id) || this.charOptions[0]).img; },
      _seenBoot(b) {                                 // 최초 값 기억 · 달라지면 새 버전 배너(분기 없음 · 새로고침 단일 유도)
        if (!b) return;
        if (this._boot && this._boot !== b) this.updateAvail = true;
        if (!this._boot) this._boot = b;
      },
      startLive() {
        try {
          if (this._es) { try { this._es.close(); } catch (e) {} this._es = null; }
          // 운영(supabase)은 /events 도 로그인 필수 · EventSource 는 헤더를 못 실어 token 쿼리로 검증.
          // 로그인 전엔 구독 보류(401 로 닫히면 브라우저가 재시도하지 않음) → 로그인·가입 시 재호출.
          if (this.backend === 'supabase' && !this.authToken) return;
          const es = new EventSource('/events' + (this.authToken ? ('?token=' + encodeURIComponent(this.authToken)) : '')); this._es = es;
          es.onmessage = (e) => { let d; try { d = JSON.parse(e.data); } catch (_) { return; } this.onLive(d); };
          es.onerror = () => {};                       // 자동 재연결(브라우저 기본)
        } catch (e) {}
      },
      onLive(d) {
        if (!d || !d.type) return;
        if (d.type === 'hello') { this._seenBoot(d.boot); return; }   // 배포 감지: SSE 재연결 시 부팅 ID 비교
        if (d.type === 'feedback') {
          if (d.reviewer && d.reviewer !== this.reviewer) {
            const v = d.verdict === 'good' ? '정확' : d.verdict === 'bad' ? '문제' : '취소';
            this.liveToast(d.reviewer + '님 · 「' + (d.title || '콘텐츠') + '」 ' + v);
          }
          if (this.mod === 'create') this.loadRawThrottled();                            // 다인 동시 검수 시 판정 1건마다 전체 재조회 방지
          if (this.mod === 'arena' || this.mod === 'home') this.loadArenaThrottled();    // 정확도 게이지 실시간 상승(스로틀)
          if (this.mod === 'evaluate' || this.mod === 'home') this.loadDashThrottled();  // dash·eval 은 selectMod 별칭이라 mod 로 영영 안 옴 → evaluate 로 교정
        } else if (d.type === 'reap') {
          if (d.plan) this.liveToast('개선안 반영 · ' + (d.plan.length > 42 ? d.plan.slice(0, 42) + '…' : d.plan));
          if (this.mod === 'arena' || this.mod === 'home') this.loadArena();
          if (this.mod === 'studio') this.loadPromptDefaults();   // 단계 프롬프트(LEARNED) 갱신
        } else if (d.type === 'learn_batch') {           // 반영 완료 모먼트(팀 전체)
          this.liveToast('🎉 v' + (d.version || '') + ' 반영 완료' + (d.grade_accuracy != null ? ' · 정답 일치율 ' + this.pctTxt(d.grade_accuracy) : '') + (d.reverted ? ' · 보정 미반영(악화 방지)' : (d.improve_delta != null ? ' · 보정 효과 ' + this.deltaTxt(d.improve_delta) : '')) + ' · 새 퀘스트를 기다립니다');
          this.loadArena(); this.loadLearnReport();
          if (this.mod === 'testset') { this.loadGoldenStatus(); this.loadGoldenList(); }
        } else if (d.type === 'reviewer') {
          if (this.mod === 'arena' || this.mod === 'home') this.loadArena();             // 다른 사람 캐릭터 변경 반영
        }
      },
      liveToast(msg) { this.liveMsg = msg; clearTimeout(this._lt); this._lt = setTimeout(() => { this.liveMsg = ''; }, 4200); },
      async loadArena() { try { const p = this.reviewer ? ('?reviewer=' + encodeURIComponent(this.reviewer)) : ''; const r = await this._afetch('/arena' + p); const d = await r.json(); if (r.ok && d) { this.arenaData = d; this.maybeQuestReminder(); } } catch (e) { this._err('아레나 불러오기 실패 · 네트워크 확인 후 새로고침 해주세요'); } this.checkBadges(); },
      async loadAdmin() { try { this.adminData = await (await this._afetch('/admin', { headers: this._authHeaders() })).json(); this._initMenuPerms(); } catch (e) { this._err('팀 관리 불러오기 실패'); } },
      _initMenuPerms() {                     // 유효 매트릭스 → 편집 상태(각 메뉴 {super,admin} 보장)
        const src = (this.adminData && this.adminData.menuPerms) || {};
        const order = (this.adminData && this.adminData.menuOrder) || [];
        const out = {};
        order.forEach((mid) => { const r = src[mid] || {}; out[mid] = { super: !!r.super, admin: !!r.admin }; });
        this.menuPermsEdit = out;
      },
      async saveMenuPerms() {
        this.menuPermsMsg = '저장 중…';
        try {
          const r = await (await this._afetch('/admin', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ action: 'set_menu_perms', perms: this.menuPermsEdit }) })).json();
          if (r && r.ok) { if (this.adminData) this.adminData.menuPerms = r.menuPerms || this.menuPermsEdit; this.menuPermsMsg = '저장됐습니다'; }
          else { this.menuPermsMsg = (r && r.error) || '저장 실패'; }
        } catch (e) { this.menuPermsMsg = '저장 실패'; }
      },
      // 관리자 판정 보장 로드: 일시 실패(배포 재시작·네트워크 순단)면 백오프 재시도.
      // 단발 loadAdmin 만으로는 실패 시 adminData 가 null 로 굳어 관리자에게 사용자 메뉴만 노출됐다(간헐 · 2026-07-07).
      // 세션 자동 갱신: supabase access token 은 1시간 만료 · refresh_token 으로 무중단 연장.
      // (만료 후 관리자 메뉴가 조용히 사용자 메뉴로 강등되던 원인 · 2026-07-08)
      rtoken: '',
      async authRefresh() {
        if (!this.rtoken) return false;
        if (this._refreshBusy) return this._refreshBusy;            // 동시 호출 dedupe
        this._refreshBusy = (async () => {
          try {
            const r = await (await fetch('/auth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: 'refresh', refresh_token: this.rtoken }) })).json();
            if (!(r && r.ok && r.access_token)) return false;
            this.authToken = r.access_token;
            if (r.refresh_token) this.rtoken = r.refresh_token;     // supabase 는 갱신 시 회전
            try { localStorage.setItem('prism_token', this.authToken); localStorage.setItem('prism_rtoken', this.rtoken); } catch (e) {}
            if (!this._es || this._es.readyState === 2) this.startLive();   // 만료로 닫힌 SSE 를 새 토큰으로 재구독
            return true;
          } catch (e) { return false; }
          finally { this._refreshBusy = null; }
        })();
        return this._refreshBusy;
      },
      async ensureAdmin(tries) {
        if (this._adminBusy) return;
        this._adminBusy = true;
        try {
          let refreshed = false;
          for (let i = 0; i < (tries || 6); i++) {
            try {
              const r = await this._afetch('/admin', { headers: this._authHeaders() });
              if (r.status === 401) {                               // 토큰 만료: 1회 자동 갱신 후 즉시 재시도
                if (!refreshed && await this.authRefresh()) { refreshed = true; continue; }
                this.adminData = await r.json();
                this._err('로그인이 만료됐습니다 · 다시 로그인해주세요');
                this.logout();
                return;
              }
              if (r.ok || r.status === 403) {                       // 503 등은 재시도
                this.adminData = await r.json();
                return;
              }
            } catch (e) {}
            await new Promise((res) => setTimeout(res, 700 * Math.pow(2, i)));   // 0.7→…→22.4s(누적 ~44s · 배포 재시작 흡수)
          }
          this._err('팀 관리 불러오기 실패 · 네트워크 확인 후 새로고침 해주세요');
        } finally { this._adminBusy = false; }
      },
      get filteredGolden() {                   // 정답셋 목록 · 모델별 분리 없음(정답은 모델 무관 사람 확정값)
        return (this.goldenList && this.goldenList.items) || [];
      },
      goldenShown: 200,                        // 정답셋 표시 캡(더 보기 증분) · 검수 표 rawShown 200 과 동일 규약
      get goldenShownList() { return this.filteredGolden.slice(0, this.goldenShown); },
      // 프롬프트 스튜디오 · 기준 계약/계열 래퍼/미리보기
      contractCall: 'summary', wrapFam: 'solar', wrapDraft: '', wrapMsg: '',
      pvModel: '', pvCall: 'summary', pvService: '뉴스', pvData: null,
      callLabel(cl) { return ({ summary: '① 리드문', entities: '② 엔티티', intent: '③ 인텐트', category: '④ 카테고리' })[cl] || cl; },
      get contractText() {
        const mc = this.cfg.metaContract || {};
        if (this.contractCall === 'examples') return mc.examples || '';
        return (mc.rules || {})[this.contractCall] || '';
      },
      syncWrapDraft() {
        const ov = (this.cfg.familyWrappers || {})[this.wrapFam];
        this.wrapDraft = ov || (this.cfg.familyWrapperDefaults || {})[this.wrapFam] || '';
      },
      async saveWrapper() {
        const body = { family_wrappers: {} }; body.family_wrappers[this.wrapFam] = this.wrapDraft || '';
        try { await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(body) }); await this.refreshConfig(); this.syncWrapDraft(); this.wrapMsg = '✓ 저장됨'; this.loadPreview(); } catch (e) { this.wrapMsg = '실패'; }
        setTimeout(() => { this.wrapMsg = ''; }, 2500);
      },
      async restoreWrapper() {
        const body = { family_wrappers: {} }; body.family_wrappers[this.wrapFam] = '';
        try { await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(body) }); await this.refreshConfig(); this.syncWrapDraft(); this.wrapMsg = '✓ 기본값 복원'; this.loadPreview(); } catch (e) { this.wrapMsg = '실패'; }
        setTimeout(() => { this.wrapMsg = ''; }, 2500);
      },
      async loadPreview() {
        if (!this.pvModel) this.pvModel = this.availableModels[0] || '';
        if (!this.pvModel) return;
        try { this.pvData = await (await fetch('/prompt-preview?model=' + encodeURIComponent(this.pvModel) + '&call=' + encodeURIComponent(this.pvCall) + '&service=' + encodeURIComponent(this.pvService), { headers: this._authHeaders() })).json(); } catch (e) { this.pvData = null; }
      },
      async togglePurpose(c) {               // 관리자: 용도 전환(검수용 ↔ 평가용 홀드아웃)
        const next = c.purpose === 'eval' ? 'review' : 'eval';
        try {
          const r = await (await this._afetch('/purpose', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ hashes: [c.hash], purpose: next }) })).json();
          if (r && r.ok) { c.purpose = next; this.liveToast((c.title || '콘텐츠') + ' · ' + (next === 'eval' ? '평가용' : '검수용') + ' 전환'); }
          else this._err((r && r.error) || '용도 전환 실패');
        } catch (e) { this._err('용도 전환 실패'); }
      },
      evalModel: '', evalScope: 'all',        // 평가 기준: 기준 모델 · 대상 콘텐츠 풀(all=전체 정답셋 | eval=평가용 홀드아웃)
      // 평가 런(이력 영속 · Atelier 이식): 시작 → 백그라운드 실행 → 폴링으로 진행률·리포트
      evalRuns: [], evalRunId: null, _evalPollT: null,
      async runGolden() {
        this.goldenBusy = true; this.goldenResult = null;
        try {
          const r = await (await this._afetch('/eval-run-start', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ model: String(this.evalModel || '').split('|').pop(), scope: this.evalScope }) })).json();   // 픽커 값은 provider|model → 서버(llm_for_model)엔 model id 만
          if (!r || !r.ok) { this.goldenResult = r; this.goldenBusy = false; return; }
          this.evalRunId = r.id; this.loadEvalRuns(); this.pollEvalRun(r.id);
        } catch (e) { this.goldenBusy = false; }
      },
      pollEvalRun(id) {                        // 런 리포트 폴링: 실행 중엔 부분 리포트로 진행률 표시
        clearTimeout(this._evalPollT);
        const tick = async () => {
          let r = null;
          try { r = await (await this._afetch('/eval-run?id=' + id, { headers: this._authHeaders() })).json(); } catch (e) {}
          if (!r || this.evalRunId !== id) { this.goldenBusy = false; return; }
          this.goldenResult = r;
          this.goldenBusy = (r.status === 'running');
          if (r.status === 'running' || r.rubric_status === 'running') { this._evalPollT = setTimeout(tick, 2500); }
          else { this.loadEvalRuns(); }
        };
        tick();
      },
      async loadEvalRuns() {
        try { const r = await (await this._afetch('/eval-runs', { headers: this._authHeaders() })).json(); if (r && r.ok) this.evalRuns = r.items || []; } catch (e) {}
      },
      openEvalRun(id) { this.evalRunId = id; this.goldenResult = null; this.pollEvalRun(id); },
      async cancelEvalRun(id) {
        try { await (await this._afetch('/eval-run-cancel', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ id }) })).json(); } catch (e) {}
        this.loadEvalRuns(); if (this.evalRunId === id) this.pollEvalRun(id);
      },
      async resumeEvalRun(id) {
        try {
          const r = await (await this._afetch('/eval-run-resume', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ id }) })).json();
          if (r && r.ok) { this.evalRunId = id; this.pollEvalRun(id); }
        } catch (e) {}
        this.loadEvalRuns();
      },
      evalRunStatusTxt(r) {
        if (r.status === 'running') return r.stalled ? '중단됨(재개 가능)' : ('실행 중 ' + (r.cursor || 0) + '/' + (r.total || 0));
        return { done: '완료', failed: '실패', cancelled: '중단' }[r.status] || r.status;
      },
      // 런 비교(기준 A vs 대상 B) · 회귀 가드: 학습배치와 같은 기준(conservative acceptance)
      cmpRunA: null, evalCmp: null, evalCmpBusy: false,
      setCmpBase(id) { this.cmpRunA = (this.cmpRunA === id ? null : id); this.evalCmp = null; },
      async compareRuns(bId) {
        if (!this.cmpRunA || this.cmpRunA === bId) return;
        this.evalCmpBusy = true; this.evalCmp = null;
        try { this.evalCmp = await (await this._afetch('/eval-run-compare?a=' + this.cmpRunA + '&b=' + bId, { headers: this._authHeaders() })).json(); } catch (e) {}
        this.evalCmpBusy = false;
      },
      get evalCmpRows() {                      // 비교 표 행: a·b 표시값 + 델타(inv=낮을수록 좋음)
        const c = this.evalCmp; if (!c || !c.ok) return [];
        const pct = (v) => v == null ? '·' : Math.round(v * 100) + '%';
        const mk = (k, f, inv, fmt, nf) => {                    // nf: 표본수 필드 · n=0 이면 그 쪽은 '·'
          const av = (nf && !c.a[nf]) ? null : c.a[f], bv = (nf && !c.b[nf]) ? null : c.b[f];
          const d = (av == null || bv == null) ? null : (bv - av);
          const good = d == null || Math.abs(d) < 1e-9 ? '' : ((d > 0) !== !!inv ? 'up' : 'down');
          const dTxt = d == null ? '·' : (Math.abs(d) < 1e-9 ? '=' : ((d > 0 ? '+' : '') + (fmt === 'raw' ? (Math.round(d * 100) / 100) : Math.round(d * 100) + '%p')));
          return { k, a: fmt === 'raw' ? (av == null ? '·' : av) : pct(av), b: fmt === 'raw' ? (bv == null ? '·' : bv) : pct(bv), dTxt, cls: good };
        };
        const rows = [mk('등급 일치율', 'grade_accuracy'), mk('사유 일치', 'reason_jaccard'),
                      mk('유해 놓침', 'harm_miss_rate', true), mk('빈 결과', 'empty_rate', true),
                      mk('비용($)', 'cost_usd', true, 'raw'),
                      // 아이템 메타 4축(ME.FIELD_KO 와 같은 라벨) · 표본(n) 없는 축은 '·'
                      mk('인텐트 F1', 'intent_f1', false, '', 'intent_n'),
                      mk('카테고리 F1(계층)', 'cat_hf1', false, '', 'cat_n'),
                      mk('엔티티 F1', 'ent_f1', false, '', 'ent_n'),
                      mk('리드문 유사도', 'summary_sim', false, '', 'summary_n')];
        if (c.a.rubric && c.a.rubric.n && c.b.rubric && c.b.rubric.n) {
          const ax = [['정확성', 'accuracy'], ['형식', 'format'], ['정책', 'policy'], ['간결성', 'conciseness']];
          for (const [k, f] of ax) {
            const av = c.a.rubric[f], bv = c.b.rubric[f];
            const d = (av == null || bv == null) ? null : Math.round((bv - av) * 100) / 100;
            rows.push({ k: '루브릭 · ' + k, a: av + '/5', b: bv + '/5', dTxt: d == null ? '·' : (d === 0 ? '=' : (d > 0 ? '+' : '') + d), cls: d == null || d === 0 ? '' : (d > 0 ? 'up' : 'down') });
          }
        }
        return rows;
      },
      // 오토파일럿(자동 개선 루프 · Atelier 이식): 시작/중지 + 상태 폴링(라운드가 길어 5s)
      pilot: null, pilotTarget: '0.9', pilotMeta: '', pilotRounds: '5', pilotModel: '',
      pilotM(hh) { return (hh && hh.metrics) || { grade_accuracy: hh && hh.accuracy }; },   // 구 라운드(metrics 없음)는 등급만
      pilotWin(f, hh) {                        // 라운드 중 유일하게 가장 좋은 값(비용·지연은 낮을수록)
        const hs = (this.pilot && this.pilot.history) || []; if (hs.length < 2) return false;
        const lower = (f === 'cost_usd' || f === 'latency_p50_ms'); const v = this.pilotM(hh)[f]; if (v == null) return false;
        return hs.every((o) => o === hh || this.pilotM(o)[f] == null || (lower ? v < this.pilotM(o)[f] : v > this.pilotM(o)[f]));
      }, pilotBusy: false, pilotMsg: '', _pilotPollT: null,
      async loadPilot() {
        try {
          const r = await (await this._afetch('/autopilot-status', { headers: this._authHeaders() })).json();
          if (r && r.ok) {
            this.pilot = r.run;
            clearTimeout(this._pilotPollT);
            if (r.run && r.run.status === 'running' && !r.run.stalled) this._pilotPollT = setTimeout(() => this.loadPilot(), 5000);
          }
        } catch (e) {
          // 순단으로 상태 조회가 실패해도 폴링을 이어간다 — 재예약이 catch 밖에만 있으면
          // 한 번의 실패로 진행률 갱신이 조용히 멎는다(실행은 서버에서 계속 중).
          if (this.pilot && this.pilot.status === 'running' && !this.pilot.stalled) {
            clearTimeout(this._pilotPollT);
            this._pilotPollT = setTimeout(() => this.loadPilot(), 5000);
          }
        }
      },
      async startPilot() {
        this.pilotBusy = true; this.pilotMsg = '';
        try {
          const r = await (await this._afetch('/autopilot-start', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ target: parseFloat(this.pilotTarget), meta_target: this.pilotMeta ? parseFloat(this.pilotMeta) : null, max_rounds: parseInt(this.pilotRounds, 10), model: String(this.pilotModel || '').split('|').pop() }) })).json();   // 픽커 값은 provider|model → 서버(llm_for_model)엔 model id 만
          if (!r || !r.ok) this.pilotMsg = (r && r.error) || '시작 실패';
          this.loadPilot();
        } catch (e) { this.pilotMsg = '시작 실패'; }
        this.pilotBusy = false;
      },
      async stopPilot() {
        try { await (await this._afetch('/autopilot-stop', { method: 'POST', headers: this._authHeaders(), body: '{}' })).json(); } catch (e) {}
        this.loadPilot();
      },
      async startRubric() {                    // 루브릭 진단(4축 · Atelier 이식): 완주 런 대상 배치 채점
        if (!this.evalRunId) return;
        try {
          const r = await (await this._afetch('/eval-rubric-start', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ id: this.evalRunId }) })).json();
          if (r && r.ok) this.pollEvalRun(this.evalRunId);
          else this._err((r && r.error) || '루브릭 채점 시작 실패');
        } catch (e) { this._err('루브릭 채점 시작 실패'); }
      },
      async cancelRubric() {                   // 채점 중단(이미 채점된 건은 유지 · 백엔드 배치 사이 확인)
        if (!this.evalRunId) return;
        try {
          const r = await (await this._afetch('/eval-rubric-cancel', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ id: this.evalRunId }) })).json();
          if (!(r && r.ok)) this._err((r && r.error) || '중단 요청 실패');
        } catch (e) { this._err('중단 요청 실패'); }
      },
      // 모델별 정합성 비교(골든셋 평가 탭) · 슬롯 N개(2~6) 동시 실호출 · 이항 95% CI 표기 · 건별 비교표
      cmpModels: ['', ''], cmpBusy: false, cmpResult: null, cmpItemFilter: 'miss', cmpLoopModel: '', cookBusy: false,
      _CMP_FIELD_KO: { grade_accuracy: '등급 일치율', intent_f1: '인텐트 F1', cat_hf1: '카테고리 F1', ent_f1: '엔티티 F1', summary_sim: '리드문 유사도', grade: '등급', intent: '인텐트', category: '카테고리', entities: '엔티티', summary: '리드문' },
      cmpFieldKo(k) { return this._CMP_FIELD_KO[k] || k; },
      get cmpIssues() {                        // 개선 루프 대상 모델(기본 best)의 진단 목록
        const c = this.cmpCols; if (!c.length) return [];
        const m = c.find((x) => x.model === this.cmpLoopModel) || c.find((x) => x.model === (this.cmpResult && this.cmpResult.best)) || c[0];
        return m.issues || [];
      },
      async applyRecipe(it) {                  // 쿡북 지시 → 공통 스테이지 프롬프트 끝에 얹기(/cookbook-apply · 관리자)
        if (!it || this.cookBusy) return;
        this.cookBusy = true;
        try {
          const r = await (await this._afetch('/cookbook-apply', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ stage: it.stage, directive: it.directive }) })).json();
          if (r && r.ok) { it.applied = true; this.liveToast((r.added ? '프롬프트에 반영 · ' : '이미 반영돼 있음 · ') + it.stage + ' 단계 · 다시 비교 실행으로 확인하세요'); }
          else this._err((r && r.error) || '반영 실패');
        } catch (e) { this._err('반영 실패'); }
        this.cookBusy = false;
      },
      _CMP_COLORS: ['var(--ds-primary)', 'var(--ds-warning)', 'var(--ds-success)', 'var(--ds-error)', '#7c3aed', '#0891b2'],
      cmpTag(i) { return 'ABCDEF'[i] || String(i + 1); },
      cmpColor(i) { return this._CMP_COLORS[i % this._CMP_COLORS.length]; },
      cmpRemoveSlot(i) { if (this.cmpModels.length > 2) this.cmpModels.splice(i, 1); },
      get cmpPicked() { return Array.from(new Set(this.cmpModels.map((v) => String(v || '').split('|').pop()).filter(Boolean))); },   // 픽커 값은 provider|model → 서버엔 model id 만 · 중복 제거
      get cmpCols() {
        const ms = (this.cmpResult && this.cmpResult.models) || [];
        const picked = this.cmpPicked;
        const cols = picked.map((id) => ms.find((m) => m.model === id)).filter(Boolean);
        return cols.length ? cols : ms;          // 저장된 비교를 불러온 직후엔 슬롯과 무관하게 결과 순서대로
      },
      cmpWin(f, mi, lower) {                     // 그 줄에서 유일하게 가장 좋은 값(동률이면 표시 안 함)
        const c = this.cmpCols; if (c.length < 2) return false;
        const v = c[mi][f]; if (v == null) return false;
        return c.every((o, oi) => oi === mi || o[f] == null || (lower ? v < o[f] : v > o[f]));
      },
      cmpCell(it, model) { return (it.got && it.got[model]) || { grade: '', reasons: [], ok: false, empty: true }; },
      get cmpItems() {
        const all = (this.cmpResult && this.cmpResult.items) || [];
        const f = this.cmpItemFilter;
        const sel = f === 'all' ? all : (f === 'split' ? all.filter((it) => it.split) : all.filter((it) => !it.all_ok));
        return sel.slice(0, 100);
      },
      get cmpItemsMore() {
        const all = (this.cmpResult && this.cmpResult.items) || [];
        const f = this.cmpItemFilter;
        const n = f === 'all' ? all.length : (f === 'split' ? all.filter((it) => it.split).length : all.filter((it) => !it.all_ok).length);
        return n > 100;
      },
      cmpJob: null, _cmpPollT: null,           // 백그라운드 비교 잡(진척은 별도 창 · 메인은 완료만 받아 표를 채움)
      get cmpProgressTxt() {
        const j = this.cmpJob; if (!j) return '';
        const ms = Object.values(j.models || {}); const done = ms.reduce((n, m) => n + (m.done || 0), 0); const tot = ms.reduce((n, m) => n + (m.total || 0), 0);
        return tot ? (done + '/' + tot + ' · 모델 ' + ms.filter((m) => m.status === 'done').length + '/' + ms.length) : '';
      },
      openCompareWindow(id) {                  // 앱 안 팝업이 아니라 브라우저 새 창(진척도 큐)
        try { const w = window.open('/vendor/compare-window.html' + (id ? '?id=' + id : ''), 'prism-compare-queue', 'width=860,height=720,noopener=false'); if (w) w.focus(); return w; } catch (e) { return null; }
      },
      async runCompare() {
        if (this.cmpPicked.length < 2 || this.cmpBusy) return;
        const win = this.openCompareWindow();   // 클릭 동기 구간에서 먼저 열어야 팝업 차단을 피한다
        this.cmpBusy = true; this.cmpResult = null; this.cmpJob = null;
        try {
          const r = await (await this._afetch('/compare-start', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ models: this.cmpPicked, scope: this.evalScope }) })).json();
          if (!(r && r.ok)) { this.cmpResult = r; this.cmpBusy = false; if (win) win.close(); return; }
          if (win) { try { win.location.replace('/vendor/compare-window.html?id=' + r.id); } catch (e) {} }
          this.pollCompare(r.id);
        } catch (e) { this._err('모델 비교 시작 실패'); this.cmpBusy = false; }
      },
      pollCompare(id) {
        clearTimeout(this._cmpPollT);
        const step = async () => {
          try {
            const r = await (await this._afetch('/compare-status?id=' + id, { headers: this._authHeaders() })).json();
            if (r && r.ok) {
              this.cmpJob = r.job;
              if (r.job.status === 'done') { this.cmpResult = r.job.result; this.cmpBusy = false; this.liveToast('모델 비교 완료 · #' + id); return; }
              if (r.job.status === 'failed') { this._err('모델 비교 실패: ' + (r.job.error || '')); this.cmpBusy = false; return; }
            } else if (r && r.error) { this._err(r.error); this.cmpBusy = false; return; }
          } catch (e) {}
          this._cmpPollT = setTimeout(step, 3000);
        };
        step();
      },
      _cmpMsgBound: false,
      _bindCompareMessage() {                  // 진척도 창의 '메인 화면에서 자세히' → 결과 불러와 평가 탭으로
        if (this._cmpMsgBound) return; this._cmpMsgBound = true;
        window.addEventListener('message', async (ev) => {
          if (ev.origin !== location.origin || !ev.data || ev.data.type !== 'prism-compare-done') return;
          try { const r = await (await this._afetch('/compare-status?id=' + ev.data.id, { headers: this._authHeaders() })).json(); if (r && r.ok && r.job.result) { this.cmpResult = r.job.result; this.cmpBusy = false; this.cmpJob = r.job; } } catch (e) {}
          if (this.mod !== 'evaluate') this.selectMod('evaluate');
        });
      },
      async loadCompareLast() {                  // 탭 재진입 시 마지막 비교(영속분) 복원 · 슬롯이 비어 있으면 비교했던 모델로 채움
        this._bindCompareMessage();
        if (this.cmpResult || this.cmpBusy) return;
        try {
          const r = await (await this._afetch('/model-compare-last', { headers: this._authHeaders() })).json();
          if (!(r && r.ok)) return;
          this.cmpResult = r;
          if (!this.cmpPicked.length) this.cmpModels = (r.models || []).map((m) => m.model).concat(['', '']).slice(0, Math.max(2, (r.models || []).length));
        } catch (e) {}
      },
      // 비교 결과의 추천 모델을 기본 모델(cfg.model)로 승격 · /config POST(관리자 게이트는 서버가 판정)
      applyBusy: false,
      async applyModel(m) {
        if (!m || this.applyBusy) return;
        this.applyBusy = true;
        try {
          const r = await (await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ model: m }) })).json();
          if (r && !r.error) { this.cfgModel = m; this.liveToast('기본 모델 적용 · ' + m + ' · 다음 실행부터 이 모델로 초안을 만듭니다'); }
          else this._err((r && r.error) || '모델 적용 실패');
        } catch (e) { this._err('모델 적용 실패'); }
        this.applyBusy = false;
      },
      myEvalVote(d) { const r = (d.judge && d.judge.reviewers) || {}; const me = (this.arenaData && this.arenaData.my_id) || this.reviewer || ''; return r[me] || r[this.reviewer] || ''; },
      evalConsensus(d) {
        const j = d.judge || {}; const mg = (this.goldenResult && this.goldenResult.min_good) || 1;
        if ((j.adopt || 0) >= mg && (j.adopt || 0) > (j.reject || 0)) return 'adopt';
        if ((j.reject || 0) >= mg && (j.reject || 0) > (j.adopt || 0)) return 'reject';
        return '';
      },
      async evalJudge(d, verdict) {                 // 평가 판정: 검수와 같은 집단 지성(1인 1표 · 재판정 허용)
        if (!this.ensureReviewer()) return;
        try {
          const r = await (await this._afetch('/eval-judge', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ hash: d.hash, verdict: verdict, reviewer: this.reviewer, expected: d.expected, got: d.got }) })).json();
          if (r && r.ok) { d.judge = r.judge; this.liveToast('평가 판정 저장 · ' + (verdict === 'adopt' ? '채택' : '탈락')); }
          else this._err((r && r.error) || '판정 저장 실패');
        } catch (e) { this._err('판정 저장 실패'); }
      },
      ciOf(p, n) { if (p == null || !n) return '·'; const s = Math.sqrt(Math.max(p * (1 - p), 0) / n); return this.pctTxt(Math.max(0, p - 1.96 * s)) + '~' + this.pctTxt(Math.min(1, p + 1.96 * s)); },
      latTxt(ms) { return ms == null ? '·' : ((Math.round(ms / 100) / 10) + '초'); },   // 지연 표기: ms → 0.1초 단위
      // 검수 활동 추이(일별 30일): 막대=검수량 · 툴팁에 교정·골드 정답률
      activityData: null,
      async loadActivity() {
        try { const r = await (await this._afetch('/activity-daily?days=30', { headers: this._authHeaders() })).json(); if (r && r.ok) this.activityData = r.days; } catch (e) {}
      },
      get actMax() { return Math.max(1, ...((this.activityData || []).map((d) => d.reviews))); },
      actSum(k, n) { return (this.activityData || []).slice(-n).reduce((s, d) => s + (d[k] || 0), 0); },
      actTip(d) {
        const g = d.gold_n ? (' · 골드 정답률 ' + Math.round((d.gold_correct / d.gold_n) * 100) + '% (' + d.gold_n + '문항)') : '';
        return d.day.slice(5).replace('-', '/') + ' · 검수 ' + d.reviews + '건 · 교정 ' + d.corrections + '건' + g;
      },
      // 비용 롤업(일별×모델×콜 · 관리자): 실행 시점 누적 원장(reports cost_rollup)
}));
