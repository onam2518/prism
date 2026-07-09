/* Prism 앱 스크립트(Alpine) · serve.PAGE 에서 분리된 단일 원천. */
  document.addEventListener('alpine:init', () => {
    Alpine.data('prismApp', () => ({
      tabItems: [{ id: 'image', label: '이미지' }, { id: 'text', label: '텍스트' }, { id: 'excel', label: '엑셀' }],
      activeTabId: 'image',
      // 위젯 홈 셸 · 홈(캔버스) + 카테고리 내비
      mod: 'home',
      dashTop: 'content',   // (구 현황 대시보드 잔여 상태 · 위젯 홈 호환용)
      drillOpen: false, drillData: null, drillBusy: false,  // 대시보드 드릴다운
      detailOpen: false, detail: null,   // 콘텐츠 상세(공통 컴포넌트): 좌 원문 렌더 · 우 평가
      badgeToast: null,   // 배지 달성 축하 오버레이
      ptToast: null,      // 검수 완료 시 점수 상승(+PT) 리워드 토스트
      errMsg: '',   // 전역 에러 토스트(조용한 실패 노출)
      dashSub: 'batch',     // 콘텐츠 서브탭: batch(배치결과) | quality(품질·법령) | topic(토픽)
      // 메뉴별 의미에 맞는 아이콘(공유 grid/square 폐기) kind 칩은 미사용
      navIcons: {
        home: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M4 11 12 4l8 7M6 10v9h12v-9" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
        auto: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M21 12a9 9 0 1 1-2.64-6.36" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><path d="M21 4v4h-4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        run: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
        queue: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M4 6h16M4 12h16M4 18h10" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><circle cx="19" cy="18" r="2" stroke="currentColor" stroke-width="1.6"/></svg>',
        dash: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M3 3v18h18M8 15v3m4-9v9m4-5v5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
        quality: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M9 12l2 2 4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        eval: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M9 11l3 3 8-8M21 12a9 9 0 1 1-6.2-8.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        user: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="8" r="3.4" stroke="currentColor" stroke-width="1.6"/><path d="M5 20a7 7 0 0 1 14 0" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
        dict: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M5 4h12a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M5 18a2 2 0 0 1 2-2h12" stroke="currentColor" stroke-width="1.5"/></svg>',
        prompt: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><rect x="3" y="4" width="18" height="16" rx="2" stroke="currentColor" stroke-width="1.5"/><path d="M7 9l3 3-3 3M13 15h4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        intake: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M3 5h18l-7 8v5l-4 2v-7z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
        review: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M9 11l2 2 4-4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12" cy="12" r="8.5" stroke="currentColor" stroke-width="1.5"/></svg>',
        admin: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><circle cx="9" cy="8" r="3" stroke="currentColor" stroke-width="1.6"/><path d="M3 20a6 6 0 0 1 12 0M16 7l2 2 4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        arena: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M8 21h8M12 17v4M6 4h12v4a6 6 0 0 1-12 0V4Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M18 5h2.5a2 2 0 0 1 0 4H18M6 5H3.5a2 2 0 0 0 0 4H6" stroke="currentColor" stroke-width="1.6"/></svg>',
        system: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" stroke="currentColor" stroke-width="1.5"/><path d="M19.4 13a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V13Z" stroke="currentColor" stroke-width="1.3"/></svg>',
      },
      // 멤버 메뉴는 둘: 콘텐츠 검수(판정·교정 → 정답 축적) / 평가(일치율·모델 비교)
      mods: [
        { g: '검수 · 평가', items: [
          { id: 'create', label: '콘텐츠 검수', ic: 'eval' },
          { id: 'evaluate', label: '평가', ic: 'dash' } ] },
        // 콘텐츠 인입(수동·자동·실행 큐)은 '콘텐츠 관리' 단일 메뉴로 통합 · 전부 관리자 통제
        // 권한 3단계: 운영 관리자(전부) > 슈퍼관리자(생성자 부여 · 운영 작업 메뉴) > 팀 관리자('팀 관리'만)
        { g: '관리자', gcond: 'admin', items: [
          { id: 'content', label: '콘텐츠 관리', ic: 'intake', cond: 'opsadmin' },
          { id: 'testset', label: '정답셋 관리', ic: 'eval', cond: 'opsadmin' },
          { id: 'admin', label: '팀 관리', ic: 'admin', cond: 'admin' },
          { id: 'dict', label: '사전 · 정책', ic: 'dict', cond: 'opsadmin' },
          { id: 'prompt', label: '프롬프트 스튜디오', ic: 'prompt', cond: 'opsadmin' },
          // 실험실: 지금 테스트하지 않는 탐구 요소(법령·토픽·사용자) 보관
          { id: 'lab', label: '실험실', ic: 'auto', cond: 'opsadmin' },
          // 시스템 설정: 데이터 관리(상단) + API 키·모델(하단) 통합 · 운영 관리자 전용(위험 작업)
          { id: 'system', label: '시스템 설정', ic: 'system', cond: 'sysadmin' } ] },
      ],
      get verTxt() {                         // 현재 프롬프트 버전(v=학습 반영 회차+1) · 미확정은 '-'
        return (this.goldenStatus && this.goldenStatus.batch_seq != null) ? ('v' + (this.goldenStatus.batch_seq + 1)) : '-';
      },
      navVisible(c) {                        // 메뉴 노출 판정: admin=팀 관리자 이상 · opsadmin=슈퍼관리자 이상 · sysadmin=운영 관리자
        if (!c) return true;
        if (c === 'admin') return this.backend !== 'supabase' || (this.adminData && this.adminData.isAdmin);
        if (c === 'opsadmin') return this.backend !== 'supabase' || (this.adminData && (this.adminData.isSysAdmin || this.adminData.isSuperAdmin));
        if (c === 'sysadmin') return this.backend !== 'supabase' || (this.adminData && this.adminData.isSysAdmin);
        return this.backend === c;
      },
      contentTab: 'run',                      // 콘텐츠 관리 STEP 1 카드: 수동(run)/자동(auto)
      addPurpose: 'review',                   // 추가 용도: review 검수용(기본) | eval 평가용(홀드아웃)
      createTab: 'raw',                       // 콘텐츠 검수: raw(검수 대상 콘텐츠·기본) | edit(결과 비교)
      testTab: 'status',                      // 정답셋 관리: status(현황·학습 반영) | golden(정답셋) | data(학습 데이터)
      labTab: 'legal',                        // 실험실(지금 미테스트 요소): legal(법령) | topic(토픽) | user(사용자)
      queueTrig: '',                          // 실행 큐 자동/수동 필터
      get filteredJobs() { return (this.runningJobs || []).filter((j) => !this.queueTrig || (this.queueTrig === 'auto' ? j.trigger === 'auto' : j.trigger !== 'auto')); },
      // 위젯 홈 인터랙션 상태
      chatOpen: false, addMenuOpen: false, editing: false, theme: 'light',
      chatMsgs: [{ from: 'bot', text: '무엇을 도와드릴까요? 작업을 말로 지시해 보세요' }],
      chatDraft: '',
      dashData: null, topicData: null, dictData: null, userData: null, modBusy: false, dictGroup: '',
      // 팀 실시간 협업: 검수자 식별(이름+캐릭터) · 검수 대기 · 라이브 이벤트
      reviewer: '', reviewerEditing: false, reviewerChar: 'boksil',
      saveCred: false, authBusy: false,        // 로그인: 아이디·비밀번호 저장(이 기기) · 진행 애니메이션
      // Supabase 인증(ID/PW) · backend==='supabase' 일 때
      backend: 'supabase', authToken: '', authEmail: '', authPw: '', authPw2: '', authMode: 'login', authMsg: '', noTeam: false,
      // 팀(멀티테넌시): 생성/가입 + 내 초대코드
      teamMode: 'join', teamName: '', inviteCode: '', myInvite: '', newTeamName: '', teamMsg: '',
      charOptions: [
        { id: 'boksil', label: '복실', role: '검수', img: '/vendor/boksil-catcher.svg' },
        { id: 'daesik', label: '대식', role: '추출', img: '/vendor/daesik-batter.svg' },
        { id: 'yonghee', label: '용희', role: '분석', img: '/vendor/yonghee-pitcher.svg' },
        { id: 'ddakji', label: '딱지', role: '판정', img: '/vendor/ddakji-manager.svg' },
      ],
      queueData: { items: [], n: 0 }, queueOnlyUnreviewed: true,
      arenaData: null,
      adminData: null,        // 팀 관리(supabase)
      bfBusy: false, bfMsg: '', bfMisses: [],   // 원문 링크 백필(시스템 설정)
      updateAvail: false,     // 새 버전 배포 감지(서버 부팅 ID 변화) · 새로고침 배너
      _adminBusy: false,      // ensureAdmin 동시 실행 가드(초기 이중 트리거 dedupe)
      // 골든셋 평가(정합성) 상태 + 테스트(로우 데이터) 상태
      goldenResult: null, goldenBusy: false, goldenMsg: '',
      rawData: null, rawSel: null,
      async loadRaw() { try { const r = await (await fetch('/raw?limit=200', { headers: this._authHeaders() })).json(); if (r && r.ok) { this.rawData = r; this.rawSel = null; this._absorbFreshFb(); } } catch (e) {} },
      // 목록 재조회(SSE 포함) 후: 열린 상세와 ←→ 탐색 목록은 '사본'이라 그대로 두면 예전 판정이
      // 계속 보인다(타 검수자·다른 기기 판정 미반영 · 2026-07-08 재현). 최신 fb 를 사본에 주입한다.
      _absorbFreshFb() {
        const map = {};
        (((this.rawData || {}).items) || []).forEach((r) => { if (r.hash) map[r.hash] = r.fb; });
        if (this.detailNav) (this.detailNav.list || []).forEach((r) => { if (map[r.hash]) r.fb = Object.assign({}, map[r.hash]); });
        if (this.detailOpen && this.detail && map[this.detail.hash] && !this.editVerdict && !this.pendingBad) {
          this.detail.fb = Object.assign({}, this.detail.fb, map[this.detail.hash]);   // 편집 중에는 방해 금지
        }
      },
      // 내 판정 직후: 모든 목록 사본(검수 목록·드릴 목록·탐색 목록)에 hash 기준 반영.
      // 기존에는 detailNav 가 있는 경로만 동기화돼 드릴 목록 재진입 시 예전 판정이 보였다.
      _syncFbByHash(hash, fb) {
        const upd = (r) => { if (r && r.hash === hash) { r.fb = Object.assign({}, r.fb, fb); r._doneLocal = !!(fb && fb.verdict); if (fb && fb.good !== undefined) r.split = !!(fb.good && fb.bad); } };
        (((this.rawData || {}).items) || []).forEach(upd);
        (((this.drillData || {}).items) || []).forEach(upd);
        if (this.detailNav) (this.detailNav.list || []).forEach(upd);
      },
      // 검수 대상 콘텐츠: 상단 모델→버전 구분 + 필터
      rawQ: '', rawGrade: '', rawModel: '', rawSvc: '', rawRev: '',
      // 결과 비교: 요소 단위 모델별 현황 + 콘텐츠별 초안 diff(팝업)
      cmpDraftHash: '', draftsData: null, cmpL: 0, cmpR: 1,
      cmpModalOpen: false, cmpTitle: '', cmpAmiss: false, cmpBmiss: false,
      msData: null, abAm: '', abAv: '', abBm: '', abBv: '',
      async loadModelStats() {
        try {
          const r = await (await fetch('/model-stats', { headers: this._authHeaders() })).json();
          if (r && r.ok) {
            this.msData = r;
            const ms = r.models;
            if (ms.length && !this.abAm) { this.abAm = ms[0].model; this.abAv = String(ms[0].version); }
            if (ms.length && !this.abBm) { const b = ms.find((m) => m.key !== (this.abAm + ' · v' + this.abAv)) || ms[0]; this.abBm = b.model; this.abBv = String(b.version); }
          }
        } catch (e) {}
      },
      get msModels() { return [...new Set((((this.msData||{}).models)||[]).map((m) => m.model))]; },
      msVers(model) { return (((this.msData||{}).models)||[]).filter((m) => m.model === model).map((m) => m.version).sort((a,b)=>b-a); },
      msCol(model, ver) { return (((this.msData||{}).models)||[]).find((m) => m.model === model && String(m.version) === String(ver)) || null; },
      get abCols() { return [this.msCol(this.abAm, this.abAv), this.msCol(this.abBm, this.abBv)].filter(Boolean); },
      abWin(f, mi) { const c = this.abCols; return c.length === 2 && (c[mi][f] || 0) > (c[1 - mi][f] || 0); },
      openCmpModal(r) { this.cmpTitle = r.title || '(제목 없음)'; this.cmpDraftHash = r.hash; this.cmpModalOpen = true; this.loadDrafts(); },
      async loadDrafts() {
        this.draftsData = null; this.cmpL = 0; this.cmpR = 1; this.cmpAmiss = false; this.cmpBmiss = false;
        if (!this.cmpDraftHash) return;
        try {
          const r = await (await fetch('/drafts?hash=' + encodeURIComponent(this.cmpDraftHash), { headers: this._authHeaders() })).json();
          if (!(r && r.ok)) return;
          this.draftsData = r;
          const items = r.items || [];
          const find = (m, v) => {
            let i = items.findIndex((d) => d.model === m && String(d.version) === String(v));
            if (i < 0) i = items.findIndex((d) => d.model === m);
            return i;
          };
          const ia = find(this.abAm, this.abAv), ib = find(this.abBm, this.abBv);
          this.cmpAmiss = ia < 0; this.cmpBmiss = ib < 0;
          this.cmpL = ia >= 0 ? ia : 0;
          this.cmpR = ib >= 0 ? ib : (items.length > 1 ? 1 : 0);
        } catch (e) {}
      },
      get draftPair() {
        const it = (this.draftsData||{}).items || [];
        if (!it.length) return null;
        return { l: it[Math.min(this.cmpL, it.length-1)], r: it[Math.min(this.cmpR, it.length-1)] };
      },
      get draftDiff() {
        const p = this.draftPair; if (!p) return [];
        const pick = (d) => ({
          '등급': (d.quality_meta||{}).finalGrade || '·',
          '품질 사유': ((d.quality_meta||{}).reasons||[]).join(' · ') || '·',
          '카테고리': ((d.item_meta||{}).content_category||[]).join(' · ') || '·',
          '인텐트': ((d.item_meta||{}).intent||[]).join(' · ') || '·',
          '엔티티': ((d.item_meta||{}).entities||[]).join(' · ') || '·',
          '리드문': (d.item_meta||{}).summary || '·',
        });
        const L = pick(p.l), R = pick(p.r);
        return Object.keys(L).map((k) => ({ k: k, l: L[k], r: R[k], diff: L[k] !== R[k] }));
      },
      _rawToDetail(r) { return { hash: r.hash, title: r.title, service: r.service, body: r.body || '', url: r.url || '', summary: r.summary || '', entities: r.entities || [], intent: r.intent || [], category: r.category || [], grade: r.grade || '', reasons: r.reasons || [], model: r.model || '', fb: Object.assign({}, r.fb) }; },
      openRawDetail(r) {                                 // 목록 컨텍스트 보존 -> 상세에서 이전/다음·자동 이동
        const list = this.rawFiltered.slice();
        this.openDetail(this._rawToDetail(r));
        this.detailNav = { list: list, idx: Math.max(0, list.findIndex((x) => x.hash === r.hash)) };
      },
      detailNav: null,
      autoNext: (function () { try { return localStorage.getItem('prismAutoNext') !== '0'; } catch (e) { return true; } })(),
      saveAutoNext() { try { localStorage.setItem('prismAutoNext', this.autoNext ? '1' : '0'); } catch (e) {} },
      // 닉네임 변경(홈 · 내 검수 캐릭터): 이름만 교체 · 팀·캐릭터·검수 이력 유지
      nickEdit: false, nickNew: '', nickMsg: '', nickBusy: false,
      async saveNick() {
        const nv = (this.nickNew || '').trim();
        if (!nv) { this.nickMsg = '닉네임을 입력하세요'; return; }
        if (nv === this.reviewer) { this.nickEdit = false; this.nickMsg = ''; return; }
        this.nickBusy = true; this.nickMsg = '';
        try {
          const d = await (await fetch('/reviewer', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ mode: 'rename', reviewer: this.reviewer, name: nv, char: this.reviewerChar }) })).json();
          if (!d.ok) { this.nickMsg = '오류: ' + (d.error || '변경 실패'); }
          else {
            this.reviewer = nv; try { localStorage.setItem('prism_reviewer', nv); } catch (e) {}
            this.nickEdit = false; this.nickMsg = '';
            this.loadArena();                        // 리더보드·캐릭터 카드에 새 이름 즉시 반영
          }
        } catch (e) { this.nickMsg = '오류: ' + e; }
        this.nickBusy = false;
      },
      detailGo(step) {                                   // 상세에서 목록 순서로 이전/다음 이동
        if (!this.detailNav) return;
        const i = this.detailNav.idx + step;
        if (i < 0 || i >= this.detailNav.list.length) return;
        const nav = this.detailNav;
        this.openDetail(this._rawToDetail(nav.list[i]));
        nav.idx = i;
        this.detailNav = nav;
      },
      detailNextTodo() {                                 // 다음 미검수 항목으로 · 없으면 완료 안내 후 닫기
        if (!this.detailNav) return;
        const nav = this.detailNav;
        for (let i = nav.idx + 1; i < nav.list.length; i++) {
          const r = nav.list[i];
          if (!(r.fb && r.fb.verdict) && !r._doneLocal) {
            this.openDetail(this._rawToDetail(r));
            nav.idx = i;
            this.detailNav = nav;
            return;
          }
        }
        this.liveToast('🎉 목록의 검수를 모두 마쳤어요!');
        this.detailOpen = false;
      },
      _afterVerdict() {                                  // 판정 직후: 자동 다음(토글) · 행 반영은 _syncFbByHash 가 담당
        if (this.histOpen) this.loadHistory();             // 이력이 열려 있으면 즉시 갱신
        if (!this.detailNav) return;
        if (this.autoNext) this.detailNextTodo();
      },
      get rawModels() { return [...new Set(((this.rawData||{}).items||[]).map((r) => r.model).filter(Boolean))]; },
      get rawSvcs() { return [...new Set(((this.rawData||{}).items||[]).map((r) => r.service).filter(Boolean))]; },
      get rawFiltered() {
        return (((this.rawData||{}).items)||[]).filter((r) => {
          if (this.rawQ && !((r.title||'') + (r.category||[]).join(' ') + (r.reasons||[]).join(' ')).toLowerCase().includes(this.rawQ.toLowerCase())) return false;
          if (this.rawGrade && (r.grade||'') !== this.rawGrade) return false;
          if (this.rawModel && (r.model||'') !== this.rawModel) return false;
          if (this.rawSvc && (r.service||'') !== this.rawSvc) return false;
          if (this.rawRev === 'todo' && (r.fb && r.fb.verdict)) return false;
          if (this.rawRev === 'done' && !(r.fb && r.fb.verdict)) return false;
          return true;
        });
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
      srcFilter: '',          // 결과 출처 필터(자동 인입/단건/배치)
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
          const kick = () => this.ensureAdmin();
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
        try { const q = new URLSearchParams(location.search); const m = q.get('m'); if (m) this.selectMod(m); if (q.has('settings')) this.selectMod('system'); } catch (e) {}
        fetch('/vocab').then(r => r.json()).then(j => { if (j.groups && j.groups.length) this.groups = j.groups; }).catch(() => {});
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
          if (e.code === 'KeyA') { e.preventDefault(); this.reviewGood(); }
          else if (e.code === 'KeyS') { e.preventDefault(); this.openEditVerdict(); this.pendingBad = true; }
          else if (e.code === 'ArrowRight') { e.preventDefault(); this.detailGo(1); }
          else if (e.code === 'ArrowLeft') { e.preventDefault(); this.detailGo(-1); }
          else if (e.code === 'Escape') { this.detailOpen = false; }
        });
        // 브라우저 뒤로/앞으로 = 메뉴 이동(URL ?m= 동기화)
        window.addEventListener('popstate', (e) => {
          const m = (e.state && e.state.m) || new URLSearchParams(location.search).get('m') || 'home';
          this._noPush = true; this.selectMod(m); this._noPush = false;
        });
      },
      get tabLabel() { return (this.tabItems.find(t => t.id === this.activeTabId) || {}).label || ''; },
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
        const m = { home: '검수 진척율을 함께 끝까지 · 검수할수록 진척·점수·배지로 성장합니다', auto: '콘텐츠 자동 인입 파이프라인 설정 (REST API · Kafka 등)', run: '수동으로 이미지·텍스트·엑셀 추출 (기본 운영은 자동 인입)', queue: '진행 중·대기 중인 추출 작업', dash: '추출 결과 집계 · 유통 G/R · 분포', review: 'YELLOW 사람검수 대기열 · 팀 다중 의견 + 실시간 협업', arena: '검수 진척율(개인·팀 평균) · 검수할수록 게이지가 차오르고 기여가 점수로', admin: '팀 멤버 · 초대 코드(팀 관리자)', system: '데이터 관리 + API 키·모델 설정(운영 관리자)', quality: '품질·법령 판정 + 엔티티·사건·조건 토픽', user: '행동 로그 → 소비 형태·강도·선호', eval: '콘텐츠별 평가 피드백(학습 루프) · 처리 이력·보정·비용', dict: '사전·카테고리·품질·법령 정책을 직접 수정', prompt: '추출 방향을 조향하는 시스템 프롬프트·추론 강도', intake: 'ITEM TYPE별 필터·처리 정책 + 콘텐츠 출처 분류', create: '검수 대상 콘텐츠를 판정·교정하고 결과를 모델·버전으로 비교합니다', evaluate: '정답셋 기준 평가 실행 · 불일치 건별 판정 · 모델별 A/B 비교', content: '콘텐츠 추가(수동·자동) → 모델 실행 → 실행 큐 · 용도(검수/평가) 지정', testset: '정답셋 현황·학습 반영 · 정답셋 목록 · 학습 데이터 추출', lab: '지금 테스트하지 않는 탐구 요소(법령·토픽·사용자) 보관' };
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
        if (id === 'eval') id = 'evaluate';
        if (id === 'golden') id = 'testset';
        this.mod = id;
        if (!this._noPush) {                          // URL 동기화(뒤로가기·새로고침 시 현재 화면 유지)
          try { const u = new URL(location.href); u.searchParams.set('m', id); history.pushState({ m: id }, '', u); } catch (e) {}
        }
        if (id === 'content' || id === 'evaluate') { this.loadDash(); this.loadGoldenStatus(); }
        if (id === 'prompt' || id === 'testset') this.loadGoldenStatus();
        if (id === 'prompt') { this.syncWrapDraft(); this.loadPreview(); }
        if (id === 'home') { this.loadArena(); this.loadDash(); }
        else if (id === 'create') { this.loadDash(); this.loadRaw(); }
        else if (id === 'evaluate') this.loadGoldenStatus();
        else if (id === 'arena') this.loadArena();
        else if (id === 'admin' || id === 'system') this.loadAdmin();
        else if (id === 'testset') { this.loadGoldenStatus(); this.loadLearnReport(); this.loadGoldenList(); this.loadLearnData(); this.loadAdmin(); }
        else if (id === 'lab') { this.loadDash(); this.loadTopics(); this.loadUser(); }
        else if (id === 'dict') this.loadDict();
        else if (id === 'prompt') this.loadPromptDefaults();
        if (id === 'content') { this.loadDash(); this.loadDict(); this.fetchIngestStatus(); this.pollIngestStatus(); }
      },
      toggleTheme() {
        this.theme = this.theme === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', this.theme);
      },
      // ── 플로팅 도우미(채팅 허브) ──
      chatSend(text) {
        const v = (text != null ? text : this.chatDraft).trim(); if (!v) return;
        this.chatMsgs.push({ from: 'me', text: v }); this.chatDraft = '';
        setTimeout(() => { this.chatMsgs.push({ from: 'bot', text: '알겠어요 "' + v + '" 작업을 큐에 넣었어요' }); }, 380);
      },
      chatAct(act) {
        this.chatOpen = false;
        if (act === 'extract') this.selectMod('run');
        else if (act === 'settings') this.selectMod('system');
        else if (act === 'dict') this.selectMod('dict');
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
      async loadDash() { this.modBusy = true; try { this.dashData = await (await fetch('/dashboard')).json(); } catch (e) { this._err('대시보드 불러오기 실패 · 다시 시도하세요'); } this.modBusy = false; },
      async drill(kind, value) {
        this.drillOpen = true; this.drillBusy = true; this.drillData = { kind: kind, value: value, items: [] };
        try { this.drillData = await (await fetch('/drill?kind=' + kind + '&value=' + encodeURIComponent(value))).json(); } catch (e) { this._err('콘텐츠 목록 불러오기 실패'); }
        this.drillBusy = false;
      },
      async topicDrill(t) {                              // 토픽 → 묶인 콘텐츠(배치 결과 드릴다운과 동일 모달)
        if (!t || !t.cluster_id) return;
        this.drillOpen = true; this.drillBusy = true;
        this.drillData = { kind: 'topic', value: (t.name || t.label || t.cluster_id), items: [] };
        try { this.drillData = await (await fetch('/topic-drill?cluster=' + encodeURIComponent(t.cluster_id))).json(); } catch (e) { this._err('토픽 콘텐츠 불러오기 실패'); }
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
      openDetail(c) { this.detailNav = null; this.detail = Object.assign({ entities: [], intent: [], category: [], reasons: [], fb: {} }, c); if (!this.detail.fb) this.detail.fb = {}; this.editVerdict = false; this.pendingBad = false; this.detailBack = this.drillOpen; this.detailOpen = true; this.drillOpen = false; this.histItems = []; if (this.histOpen) this.loadHistory(); },
      // 작업 이력(판정·교정·재실행 타임라인): 접이식 · 열려 있으면 항목 이동·판정 후 자동 갱신
      histOpen: false, histBusy: false, histItems: [],
      toggleHistory() { this.histOpen = !this.histOpen; if (this.histOpen) this.loadHistory(); },
      async loadHistory() {
        if (!(this.detail && this.detail.hash)) return;
        const h = this.detail.hash;
        this.histBusy = true;
        try {
          const r = await (await fetch('/history?hash=' + encodeURIComponent(h), { headers: this._authHeaders() })).json();
          if (r && r.ok && this.detail && this.detail.hash === h) this.histItems = r.items || [];
        } catch (e) {}
        this.histBusy = false;
      },
      histWhen(ts) { return ts ? new Date(ts * 1000).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''; },
      editVerdict: false, pendingBad: false, detailBack: false,
      // 정확 = 즉시 완료 · 수정 필요 = 요소·사유 입력 후 '완료 처리' 로만 확정(누른다고 바로 저장 안 함)
      reviewGood() { this.pendingBad = false; this.setFeedback(this.detail, 'good'); this.editVerdict = false; this._afterVerdict(); },
      reviewBadComplete() {
        if (!(this.detail.fb.note || '').trim()) { this._err('무엇을 왜 고쳐야 하는지 입력하세요'); return; }
        const cur = (this.detail.fb.mine !== undefined ? (this.detail.fb.mine || '') : (this.detail.fb.verdict || '')) || '';
        this.detail.fb = this._fbRecount(Object.assign({}, this.detail.fb, { mine: 'bad', ts: Date.now() / 1000 }), cur, 'bad');
        this._syncFbByHash(this.detail.hash, this.detail.fb);
        this.saveFbNote(this.detail);
        this.pendingBad = false; this.editVerdict = false;
        this._afterVerdict();
      },
      // 태그 용어 정의(호버 툴팁): 인텐트=고정 설명 · 품질 사유=사전(dictData) · 카테고리=경로
      INTENT_DEF: {
        '속보·사건 추적': '새로 발생한 사건·이슈를 빠르게 전하고 후속 경과를 추적', '심층 분석': '배경·맥락·데이터로 사안을 깊이 해설',
        '팬덤·화제성': '인물·작품에 대한 팬 반응·화제 중심', '실용 정보': '방법·팁·가이드 등 바로 쓰는 정보',
        '감성·공감': '감정·경험을 나누며 공감을 유도', '오락·유머': '재미·유머 중심의 가벼운 콘텐츠',
        '의견·논쟁': '찬반이 병렬로 오가는 주장·토론 콘텐츠(한쪽 논조가 뚜렷하면 옹호·지지/반박·비판)', '학술·전문': '전문 지식·연구·기술을 다룸',
        '옹호·지지': '특정 사안·인물·정책을 지지하는 한쪽 논조의 콘텐츠', '반박·비판': '특정 사안·인물·정책·주장에 반대·비판하는 한쪽 논조의 콘텐츠',
        '인터뷰': '인물 문답 중심의 전달 형식', '현장취재·르포': '현장에서 직접 취재한 심층 전달',
        '그래픽·인포그래픽': '도표·시각 자료 중심의 전달', '포토·영상 중심': '사진·영상이 본문의 중심',
        '보도자료·공식발표': '기관·기업의 공식 발표 기반', '후기·리뷰·비평': '사용·관람 경험의 평가·비평',
        '해설·팩트체크': '사안의 사실 검증·해설', '정형정보': '시세·일정·순위 등 정형 데이터 전달',
      },
      // 카테고리 한글 표시(UI 전용): 값·저장·전달은 영문(공식 표기) 유지, 렌더링만 변환.
      // 사전(dictData) 미로드 시 lazy 로드 후 원문 폴백 · 미등록 값도 원문 유지.
      catKo(v) {
        if (!v) return v;
        const d = this.dictData;
        if (!d) { if (!this._dictReq) { this._dictReq = true; this.loadDict(); } return v; }
        const t1k = d.tier1Ko || {}, t2k = d.tier2Ko || {};
        const parts = String(v).split('/').map((p) => p.trim());
        if (parts.length === 1) return t1k[parts[0]] || t2k[parts[0]] || parts[0];
        return (t1k[parts[0]] || parts[0]) + ' / ' + (t2k[parts[1]] || parts[1]);
      },
      catBoth(v) { const k = this.catKo(v); return (k && k !== v) ? (k + ' (' + v + ')') : v; },   // 병기(한글/영문순)
      // 품질 사유 병기(한글/영문순 · UI·도움말 공용): 값·저장은 영문 키 유지, 렌더만 변환
      reasonBoth(v) {
        if (!v) return v;
        if (v === 'normal') return '일반 (normal)';
        const d = this.dictData;
        if (!d) { if (!this._dictReq) { this._dictReq = true; this.loadDict(); } return v; }
        const nm = (d.qualityNames || {})[v];
        return nm ? (nm + ' (' + v + ')') : v;
      },
      termDef(kind, val) {
        val = String(val == null ? '' : val).replace(/\s*\(\d+\)\s*$/, '');   // '값 (건수)' 형태 정규화
        if (kind === 'intent') return this.INTENT_DEF[val] || ('인텐트 · ' + val);
        if (kind === 'reason') {
          const d = this.dictData;
          if (d && d.qualityMetas) { const nm = (d.qualityNames && d.qualityNames[val]) || ''; return (nm ? nm + ' · ' : '') + (d.qualityMetas[val] || val); }
          if (!this._dictReq) { this._dictReq = true; this.loadDict(); }
          return '품질 사유 · ' + val;
        }
        if (kind === 'category') return '콘텐츠 카테고리 · ' + val;
        if (kind === 'grade') return val === 'G' ? '등급 G · 유통 가능' : val === 'R' ? '등급 R · 유통 제외(문제 사유 있음)' : '등급 미판정';
        if (kind === 'entity') return '핵심 개체(인물·기관·작품 등) · ' + val;
        return val;
      },
      // ── 정책 팔레트(플로팅 도움말): 검수 중 사전·정책 기준 참조 · 드래그 이동 ──
      polOpen: false, polTab: 'intent', polQ: '', polHl: '', polPos: null, polDrag: null,
      polRestore() {
        try { const p = JSON.parse(localStorage.getItem('prismPolPal') || 'null'); if (p) { this.polPos = p.pos || null; this.polTab = p.tab || 'intent'; } } catch (e) {}
      },
      polSave() { try { localStorage.setItem('prismPolPal', JSON.stringify({ pos: this.polPos, tab: this.polTab })); } catch (e) {} },
      polToggle() { this.polOpen = !this.polOpen; if (this.polOpen && !this.dictData) this.loadDict(); this.polSave(); },
      polShow(kind, val) {                              // 값 태그 딥링크: 해당 정책 항목으로 점프·강조
        let v = String(val == null ? '' : val).replace(/\s*\(\d+\)\s*$/, '');
        if (kind === 'category') v = v.split('/')[0].trim();    // 기준은 Tier 1 단위
        this.polTab = kind === 'reason' ? 'quality' : (kind === 'grade' ? 'grade' : (kind === 'category' ? 'category' : 'intent'));
        this.polQ = ''; this.polHl = v; this.polOpen = true;
        if (!this.dictData) this.loadDict();
        this.polSave();
        this.$nextTick(() => { try { const el = document.querySelector('.polpal [data-pol="' + (window.CSS && CSS.escape ? CSS.escape(v) : v) + '"]'); if (el) el.scrollIntoView({ block: 'center' }); } catch (e) {} });
      },
      polEntries() {                                    // 탭 + 검색 → [{k(키), t(제목), d(기준)}]
        const d = this.dictData || {};
        let out = [];
        if (this.polTab === 'intent') {
          const defs = (d.intentDefs && Object.keys(d.intentDefs).length) ? d.intentDefs : this.INTENT_DEF;
          out = Object.keys(defs).map((k) => ({ k, t: k, d: defs[k] }));
        } else if (this.polTab === 'category') {
          const cr = d.categoryCriteria || {};
          const t1 = d.iabTier1 || [];
          out = t1.map((k) => ({ k, t: k, d: cr[k] || ((d.tier2 && d.tier2[k] && d.tier2[k].length) ? ('Tier 2: ' + d.tier2[k].join(' · ')) : '') }));
          Object.keys(cr).forEach((k) => { if (t1.indexOf(k) < 0) out.push({ k, t: k, d: cr[k] }); });
        } else if (this.polTab === 'quality') {
          const qm = d.qualityMetas || {};
          out = Object.keys(qm).map((k) => ({ k, t: ((d.qualityNames || {})[k] ? (d.qualityNames[k] + ' · ' + k) : k), d: qm[k] }));
        } else {                                        // grade: 판정 계약(코드 원천 고정)
          out = [
            { k: 'G', t: 'G · 유통 가능', d: '품질 사유가 임계 미만. 서비스 노출 가능 판정.' },
            { k: 'R', t: 'R · 유통 제외', d: '임계를 넘는 품질 사유가 1개 이상 확정. 사유 태그가 함께 표시됩니다.' },
            { k: 'YELLOW', t: 'YELLOW · 판정 애매', d: '모델 확신이 낮거나 경계 사례. 사람 검수 대상으로 회수되어 검수 큐에 들어옵니다.' },
          ];
        }
        const q = (this.polQ || '').trim().toLowerCase();
        if (q) out = out.filter((e) => (e.t + ' ' + (e.d || '')).toLowerCase().indexOf(q) >= 0);
        return out;
      },
      polCtx() {                                        // 열려 있는 검수 상세의 값 → 관련 정책 바로가기
        const c = this.detailOpen ? this.detail : null;
        if (!c) return [];
        const out = [];
        (c.intent || []).forEach((v) => out.push({ kind: 'intent', v: String(v) }));
        (c.category || []).forEach((v) => out.push({ kind: 'category', v: String(v) }));
        (c.reasons || []).forEach((v) => out.push({ kind: 'reason', v: String(v) }));
        if (c.grade) out.push({ kind: 'grade', v: String(c.grade) });
        return out.slice(0, 12);
      },
      polCtxLabel(x) {
        if (x.kind === 'category') return this.catKo(String(x.v).split('/')[0].trim());
        if (x.kind === 'reason') return this.reasonBoth(x.v);
        return x.v;
      },
      polStyle() { return this.polPos ? ('left:' + this.polPos.x + 'px; top:' + this.polPos.y + 'px; right:auto; bottom:auto;') : ''; },
      polDragStart(e) {
        if (e.target && e.target.closest && e.target.closest('button')) return;   // 닫기 버튼은 드래그 제외
        const box = this.$refs.polpal;
        if (!box) return;
        const r = box.getBoundingClientRect();
        this.polDrag = { dx: e.clientX - r.left, dy: e.clientY - r.top };
        const move = (ev) => {
          if (!this.polDrag) return;
          const x = Math.min(Math.max(ev.clientX - this.polDrag.dx, 8), window.innerWidth - 120);
          const y = Math.min(Math.max(ev.clientY - this.polDrag.dy, 8), window.innerHeight - 48);
          this.polPos = { x, y };
        };
        const up = () => { this.polDrag = null; this.polSave(); window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', up);
      },
      // 수정 대상 요소(파이프라인 단계 대신 '무엇을 고칠지'로 직관화). 각 요소 → 학습 단계 매핑.
      FIX_ELEMENTS: [
        { id: 'summary', label: '리드문', stage: 'analyze' },
        { id: 'entities', label: '엔티티', stage: 'analyze' },
        { id: 'intent', label: '인텐트', stage: 'analyze' },
        { id: 'category', label: '카테고리', stage: 'analyze' },
        { id: 'grade', label: '등급·유통', stage: 'judge' },
        { id: 'quality', label: '품질 사유', stage: 'review' },
      ],
      elemStage(id) { const e = this.FIX_ELEMENTS.find((x) => x.id === id); return e ? e.stage : 'analyze'; },
      elemLabel(id) { const e = this.FIX_ELEMENTS.find((x) => x.id === id); return e ? e.label : ''; },
      fmtTs(ts) {                                        // epoch(초) → 'M.D HH:mm'
        if (!ts) return '';
        const d = new Date((ts > 1e12 ? ts : ts * 1000));
        const p = (n) => (n < 10 ? '0' + n : '' + n);
        return (d.getMonth() + 1) + '.' + d.getDate() + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
      },
      // ── 배치 결과: 콘텐츠별 평가 피드백 → 학습 루프 ──
      fbNoteOpen: {},
      // 내 표 변경(cur→v)을 팀 카운트에 즉시 반영: 다음 /raw 응답 전에도 '팀 의견 · 정확 x개' 표기 정합.
      // verdict 는 서버 합의 규칙과 동일하게 재계산(다수결 · 동수 = 의견 갈림).
      _fbRecount(fb, cur, v) {
        if (cur === v) return fb;
        fb.good = Math.max(0, (fb.good || 0) + (v === 'good' ? 1 : 0) - (cur === 'good' ? 1 : 0));
        fb.bad = Math.max(0, (fb.bad || 0) + (v === 'bad' ? 1 : 0) - (cur === 'bad' ? 1 : 0));
        fb.n = fb.good + fb.bad;
        fb.verdict = fb.good > fb.bad ? 'good' : (fb.bad > fb.good ? 'bad' : (fb.n ? 'split' : ''));
        return fb;
      },
      // 추가 수정: 직전 교정(메모·선택 요소)을 남긴 채 편집을 연다(이어쓰기 · 회의 소요).
      // 저장 시 '[요소] ' 태그가 다시 붙으므로 선두 태그는 벗겨 중복을 막는다.
      openEditVerdict() {
        const fb = this.detail && this.detail.fb; if (!fb) return;
        if (fb.note) fb.note = String(fb.note).replace(/^\[[^\]]*\]\s*/, '');
        this.editVerdict = true;
        this.pendingBad = ((fb.mine || fb.verdict) === 'bad');
      },
      // 내 판정 취소: 내 표 행만 서버에서 삭제 · 남은 표 없으면 미검수로 복귀
      async undoVerdict() {
        const c = this.detail; if (!c || !c.fb) return;
        let cur = (c.fb.mine !== undefined ? (c.fb.mine || '') : (c.fb.verdict || '')) || '';
        // 로컬(sqlite) 모드는 /raw 가 내 표를 식별하지 않는다(mine 항상 빈 값) → 단독 표는 내 표로 간주
        if (!cur && this.backend !== 'supabase' && c.fb.n === 1) cur = c.fb.verdict || '';
        if (!cur) return;
        const nf = this._fbRecount(Object.assign({}, c.fb, { mine: '', ts: Date.now() / 1000 }), cur, '');
        c.fb = nf;
        this._syncFbByHash(c.hash, nf);
        this.editVerdict = false; this.pendingBad = false;
        await this._postFb({ hash: c.hash, service: c.service, title: c.title, model: c.model || '', verdict: '', stage: 'analyze', note: '' });
        if (this.histOpen) this.loadHistory();
      },
      async setFeedback(c, verdict) {
        // 재클릭 취소 비교는 '내 표(mine)' 기준. fb.verdict 는 팀 합의라 남의 표와 비교하면
        // 이미 합의가 같은 값일 때 내 첫 클릭이 취소('')로 전송되는 오동작이 난다.
        const cur = (c.fb && (c.fb.mine !== undefined ? (c.fb.mine || '') : (c.fb.verdict || ''))) || '';
        const v = (cur === verdict) ? '' : verdict;        // 같은 버튼 재클릭 = 취소
        c.fb = this._fbRecount(Object.assign({}, c.fb, { mine: v, ts: (v ? Date.now() / 1000 : 0) }), cur, v);   // 수정 일시 기록
        this._syncFbByHash(c.hash, c.fb);
        if (v === 'bad') this.fbNoteOpen[c.hash] = true;
        await this._postFb({ hash: c.hash, service: c.service, title: c.title, model: c.model || '', verdict: v, stage: (c.fb.stage || 'analyze'), note: (c.fb.note || '') });
        if (cur === '' && v !== '') this.celebratePoints(10, '검수 완료');   // 새 검수 = +10 PT
      },
      // 수정 대상 요소: 다중 선택 · 서버 오케스트레이터가 원문을 재분류해 단계별로 분기
      fbElems(fb) { if (!Array.isArray(fb.elems) || !fb.elems.length) fb.elems = [fb.element || 'summary']; return fb.elems; },
      toggleFixElem(fb, id) {
        const a = this.fbElems(fb);
        const i = a.indexOf(id);
        if (i >= 0) { if (a.length > 1) a.splice(i, 1); } else a.push(id);
        fb.element = a[0];
      },
      async saveFbNote(c) {
        const hadNote = !!(c.fb && c.fb._noteRewarded);
        const els = this.fbElems(c.fb).slice();                       // 수정 대상 요소(복수 가능)
        const stage = this.elemStage(els[0]);
        const raw = (c.fb.note || '').trim();
        const tagged = raw ? ('[' + els.map((e) => this.elemLabel(e)).join('·') + '] ' + raw) : raw;
        c.fb = Object.assign({}, c.fb, { verdict: c.fb.verdict || 'bad', stage: stage, ts: Date.now() / 1000 });
        // 서버에는 '내 표'를 보낸다 · fb.verdict 는 팀 합의라 'split' 등 표가 아닌 값이 저장될 수 있다
        const myV = (c.fb.mine !== undefined ? (c.fb.mine || '') : '') || 'bad';
        await this._postFb({ hash: c.hash, service: c.service, title: c.title, model: c.model || '', verdict: myV, stage: stage, elements: els, note: tagged });
        this.fbNoteOpen[c.hash] = false;
        if (!hadNote && (c.fb.note || '').trim()) { c.fb._noteRewarded = true; this.celebratePoints(25, '교정 반영'); }  // 교정 = +25 PT(서버 산정과 일치)
      },
      async _postFb(payload) {
        payload = Object.assign({ reviewer: this.reviewer || '', name: this.reviewer || '' }, payload);  // 키+표시명(supabase 면 서버가 uid 로 덮어씀)
        try {
          const r = await (await this._afetch('/feedback', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(payload) })).json();
          if (r && r.feedback && this.dashData) this.dashData.feedback = r.feedback;
          (r && r.missions_completed || []).forEach((m) => this.celebratePoints(m.bonus, '미션 달성 · ' + m.label));
          return r;
        } catch (e) { return null; }
      },
      // ── 팀 실시간 협업: 검수자 식별 · 검수 대기 · 라이브 ──
      loadReviewer() {
        try { this.reviewer = localStorage.getItem('prism_reviewer') || ''; this.reviewerChar = localStorage.getItem('prism_reviewer_char') || 'boksil'; this.authToken = localStorage.getItem('prism_token') || ''; this.rtoken = localStorage.getItem('prism_rtoken') || ''; } catch (e) {}
        this._loadCred();                              // 저장된 아이디·비밀번호 프리필(이 기기)
        if (!this.reviewer) this.reviewerEditing = true;            // 첫 방문 → 등록/로그인 모달
      },
      _authHeaders() { const h = { 'Content-Type': 'application/json' }; if (this.authToken) h['Authorization'] = 'Bearer ' + this.authToken; return h; },
      // 인증 공통 fetch(쓰기 라우트 통일 · 회의 소요 F): 401(토큰 만료)이면 1회 자동 갱신 후
      // 재시도하고, 그래도 만료면 안내 + 로그인 모달. 기존에는 경로마다 제각각이라
      // 만료 시 판정·교정이 조용히 유실됐다. Authorization 은 호출 시점의 최신 토큰으로 덮는다.
      async _afetch(url, opts) {
        opts = opts || {};
        const call = () => {
          const h = Object.assign({}, opts.headers || {});
          if (this.authToken) h['Authorization'] = 'Bearer ' + this.authToken;
          return fetch(url, Object.assign({}, opts, { headers: h }));
        };
        let r = await call();
        if (r.status === 401 && this.rtoken && await this.authRefresh()) r = await call();
        if (r.status === 401 && this.backend === 'supabase') {
          this._err('로그인이 만료됐습니다 · 다시 로그인해 주세요');
          this.reviewerEditing = true;
        }
        return r;
      },
      _err(m) { this.errMsg = m; if (this._errT) clearTimeout(this._errT); this._errT = setTimeout(() => { this.errMsg = ''; }, 4800); },
      get showProfileFields() { return this.backend !== 'supabase' || this.authMode === 'signup' || !!this.reviewer; },
      logout() {
        try { localStorage.removeItem('prism_reviewer'); localStorage.removeItem('prism_reviewer_char'); localStorage.removeItem('prism_token'); localStorage.removeItem('prism_rtoken'); } catch (e) {}
        this._loadCred();                              // 저장 선택 시 재로그인 편의(프리필 유지)
        this.reviewer = ''; this.authToken = ''; this.rtoken = ''; this.adminData = null; this.arenaData = null;
        this.mod = 'home'; this.reviewerEditing = true;
      },
      goldenMinGood: 1,
      get isDesktop() { return typeof window.pywebview !== 'undefined'; },
      dtAllowDl: true, dtPersist: true, dtMsg: '',
      async saveDesktopOpts() {
        try {
          await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ desktop_allow_downloads: !!this.dtAllowDl, desktop_persist_storage: !!this.dtPersist }) });
          this.dtMsg = '✓ 저장됨 · 앱 재시작 후 적용';
        } catch (e) { this.dtMsg = '저장 실패'; }
        setTimeout(() => { this.dtMsg = ''; }, 4000);
      },
      _loadCred() {
        try {
          const raw = localStorage.getItem('prism_cred');
          if (!raw) return;
          const d = JSON.parse(decodeURIComponent(escape(atob(raw))));
          this.authEmail = d.e || ''; this.authPw = d.p || ''; this.saveCred = true;
        } catch (e) {}
      },
      _storeCred(email, pw) {
        try {
          if (this.saveCred) localStorage.setItem('prism_cred', btoa(unescape(encodeURIComponent(JSON.stringify({ e: email, p: pw })))));
          else localStorage.removeItem('prism_cred');
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
          const es = new EventSource('/events'); this._es = es;
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
          if (this.mod === 'create') this.loadRaw();
          if (this.mod === 'arena' || this.mod === 'home') this.loadArena();             // 정확도 게이지 실시간 상승
          if (this.mod === 'dash' || this.mod === 'eval' || this.mod === 'home') this.loadDashThrottled();
        } else if (d.type === 'reap') {
          if (d.plan) this.liveToast('개선안 반영 · ' + (d.plan.length > 42 ? d.plan.slice(0, 42) + '…' : d.plan));
          if (this.mod === 'arena' || this.mod === 'home') this.loadArena();
          if (this.mod === 'prompt') this.loadPromptDefaults();   // 단계 프롬프트(LEARNED) 갱신
        } else if (d.type === 'learn_batch') {           // 반영 완료 모먼트(팀 전체)
          this.liveToast('🎉 v' + (d.version || '') + ' 반영 완료' + (d.grade_accuracy != null ? ' · 정답 일치율 ' + this.pctTxt(d.grade_accuracy) : '') + (d.reverted ? ' · 보정 미반영(악화 방지)' : (d.improve_delta != null ? ' · 보정 효과 ' + this.deltaTxt(d.improve_delta) : '')) + ' · 새 퀘스트를 기다립니다');
          this.loadArena(); this.loadLearnReport();
          if (this.mod === 'testset') { this.loadGoldenStatus(); this.loadGoldenList(); }
        } else if (d.type === 'reviewer') {
          if (this.mod === 'arena' || this.mod === 'home') this.loadArena();             // 다른 사람 캐릭터 변경 반영
        } else if (d.type === 'presence' && d.reviewer && d.reviewer !== this.reviewer) {
          if (d.action === 'viewing') this.liveSeen[d.hash] = d.reviewer; else delete this.liveSeen[d.hash];
        }
      },
      liveToast(msg) { this.liveMsg = msg; clearTimeout(this._lt); this._lt = setTimeout(() => { this.liveMsg = ''; }, 4200); },
      async loadQueue() { this.modBusy = true; try { const p = new URLSearchParams(); if (!this.queueOnlyUnreviewed) p.set('all', '1'); if (this.reviewer) p.set('reviewer', this.reviewer); this.queueData = await (await fetch('/queue?' + p.toString(), { headers: this._authHeaders() })).json(); } catch (e) {} this.modBusy = false; },
      async loadArena() { try { const p = this.reviewer ? ('?reviewer=' + encodeURIComponent(this.reviewer)) : ''; this.arenaData = await (await fetch('/arena' + p, { headers: this._authHeaders() })).json(); this.maybeQuestReminder(); } catch (e) { this._err('아레나 불러오기 실패'); } this.checkBadges(); },
      async loadAdmin() { try { this.adminData = await (await this._afetch('/admin', { headers: this._authHeaders() })).json(); } catch (e) { this._err('팀 관리 불러오기 실패'); } },
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
      goldenModel: '',                        // 정답셋 목록 · 유래 모델 필터
      get goldenModelList() {
        const its = (this.goldenList && this.goldenList.items) || [];
        return [...new Set(its.map(g => g.model || ''))].sort();
      },
      get filteredGolden() {
        const its = (this.goldenList && this.goldenList.items) || [];
        return this.goldenModel === '' ? its : its.filter(g => (g.model || '') === this.goldenModel);
      },
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
      async runGolden() {
        this.goldenBusy = true; this.goldenResult = null;
        try { this.goldenResult = await (await this._afetch('/eval-golden', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ model: this.evalModel, scope: this.evalScope }) })).json(); } catch (e) {}
        this.goldenBusy = false;
      },
      // 모델별 정합성 비교(골든셋 평가 탭) · 이항 95% CI 표기
      cmpA: '', cmpB: '', cmpBusy: false, cmpResult: null,
      get cmpCols() {
        const ms = (this.cmpResult && this.cmpResult.models) || [];
        return [this.cmpA, this.cmpB].map((id) => ms.find((m) => m.model === id)).filter(Boolean);
      },
      cmpWin(f, mi) { const c = this.cmpCols; return c.length === 2 && (c[mi][f] || 0) > (c[1 - mi][f] || 0); },
      async runCompare() {
        this.cmpBusy = true; this.cmpResult = null;
        try { this.cmpResult = await (await this._afetch('/compare-models', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ models: [this.cmpA, this.cmpB], scope: this.evalScope }) })).json(); } catch (e) { this._err('모델 비교 실패'); }
        this.cmpBusy = false;
      },
      myEvalVote(d) { const r = (d.judge && d.judge.reviewers) || {}; return r[this.reviewer] || ''; },
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
      // 골든 생성 현황(팀원 공개)
      goldenStatus: null,
      async loadGoldenStatus() { try { const r = await (await this._afetch('/golden-status', { headers: this._authHeaders() })).json(); if (r && r.ok) this.goldenStatus = r; } catch (e) {} },
      // 관리자 골든 브라우저
      goldenList: null,
      async loadGoldenList() { try { const r = await (await this._afetch('/golden-list', { headers: this._authHeaders() })).json(); if (r && r.ok) this.goldenList = r; } catch (e) {} },
      async removeGolden(h) {
        if (!(await this.dsConfirm('이 골든 항목을 제거할까요? (평가 정답셋에서 빠집니다)', { ok: '제거', danger: true }))) return;
        try { await this._afetch('/golden-remove', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ hash: h }) }); } catch (e) {}
        this.loadGoldenList(); this.loadGoldenStatus(); this.loadLearnData();
      },
      async runMetaCompile() {
        this.metaBusy = true;
        try { const r = await (await this._afetch('/meta-compile', { method: 'POST', headers: this._authHeaders() })).json(); this.metaResults = r.results || null; } catch (e) {}
        this.metaBusy = false; this.loadPromptDefaults();
      },
      // 일배치 학습(수동 실행): 개선 + 골든 축적 + 골든 회귀 평가 + 다중 모델 비교
      learnReport: null, learnBusy: false,
      async runLearnBatch() {
        this.learnBusy = true;
        try {                                        // 모델 비교는 '골든셋 평가' 탭에서 온디맨드(중복 실행 방지)
          const r = await (await this._afetch('/learn-batch', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({}) })).json();
          if (r && r.ok) { this.learnReport = r; this.metaResults = (r.improve && r.improve.results) || this.metaResults; }
          else this._err((r && r.error) || '일배치 실행 실패');
        } catch (e) { this._err('일배치 실행 실패'); }
        this.learnBusy = false; this.loadPromptDefaults(); this.loadGoldenStatus();
      },
      async loadLearnReport() { try { const r = await (await this._afetch('/learn-report')).json(); if (r && r.report && r.report.ts) this.learnReport = r.report; if (r && r.next_batch_at) this.nextBatchAt = r.next_batch_at; } catch (e) {} },
      nextBatchAt: 0,
      // 학습 반영 주기(모델 버전 시한 · 관리자): N일마다 지정 시각에 반영 · 지금 실행 시 주기 재시작
      learnNextAt: '', learnSchedMsg: '', schedEditing: false,
      schedEdit() {                                      // 퀘스트 생성/수정: 미지정이면 내일 04:00 프리필
        if (!this.learnNextAt) {
          const d = new Date(Date.now() + 86400000);
          const p = (n) => (n < 10 ? '0' + n : '' + n);
          this.learnNextAt = d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T04:00';
        }
        this.schedEditing = true;
      },
      async saveLearnSched() {                           // 목표 일시 + 확정 최소 인원 통합 저장
        try {
          const r = await (await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ learn_next_at: this.learnNextAt || '', golden_min_good: parseInt(this.goldenMinGood, 10) || 1 }) })).json();
          if ((this.learnNextAt || '') !== (r.learnNextAt || '')) {   // 서버 거부(과거 일시 등)
            this.learnNextAt = r.learnNextAt || '';
            this.learnSchedMsg = '지난 일시는 지정할 수 없어요';
          } else {
            this.learnSchedMsg = '✓ 저장됨';
            this.schedEditing = false;
          }
          this.loadLearnReport();                        // 다음 반영 예정 갱신
          this.loadArena();                              // 홈·사이드바 퀘스트 D-day 갱신
        } catch (e) { this.learnSchedMsg = '실패'; }
        setTimeout(() => { this.learnSchedMsg = ''; }, 2500);
      },
      ddayTxt(ts) {                                  // 버전 시한까지 D-n (당일 = D-DAY)
        if (!ts) return '';
        const now = new Date(); const due = new Date(ts * 1000);
        const d0 = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const d1 = new Date(due.getFullYear(), due.getMonth(), due.getDate());
        const n = Math.round((d1 - d0) / 86400000);
        return n <= 0 ? 'D-DAY' : 'D-' + n;
      },
      questDoneRecent() {                              // 목표 소진 후 72시간 동안 완료 잔상 표시
        const a = this.arenaData;
        return !!(a && !a.next_batch_at && a.last_batch_at && (Date.now() / 1000 - a.last_batch_at) < 72 * 3600);
      },
      maybeQuestReminder() {                           // 마감 임박(D-1 이하) 1일 1회 리마인드
        const a = this.arenaData;
        if (!(a && a.next_batch_at && a.queue)) return;
        const dd = this.ddayTxt(a.next_batch_at);
        if (dd !== 'D-DAY' && dd !== 'D-1') return;
        const mark = new Date().toDateString() + ':' + a.next_batch_at;
        try { if (localStorage.getItem('prismQuestRemind') === mark) return; localStorage.setItem('prismQuestRemind', mark); } catch (e) {}
        this.liveToast('⏰ 팀 퀘스트 마감 임박 ' + dd + ' · 남은 ' + a.queue + '건, 완주까지 화이팅!');
      },
      deltaTxt(d) { const v = (d || 0) * 100; return (v >= 0 ? '+' : '') + v.toFixed(1) + '%p'; },
      questTotal() { return (this.arenaData && this.arenaData.total_targets) || 0; },
      questDone() { const t = this.questTotal(); return Math.max(0, t - ((this.arenaData && this.arenaData.queue) || 0)); },
      questPct() { const t = this.questTotal(); return t ? Math.round(this.questDone() / t * 100) : 0; },
      // 학습 데이터 현황(관리자): 커버리지·일치도·신뢰도·오류 후보·추출(전 기준치 논문 근거)
      learnData: null, learnDataBusy: false,
      async loadLearnData() {
        this.learnDataBusy = true;
        try { const r = await (await fetch('/learn-data', { headers: this._authHeaders() })).json(); if (r && r.ok) this.learnData = r; } catch (e) {}
        this.learnDataBusy = false;
      },
      async exportLearn(kind) {
        try {
          const res = await fetch('/learn-export?kind=' + kind, { headers: this._authHeaders() });
          if (!res.ok) { this._err('내보내기 실패'); return; }
          const blob = await res.blob(); const a = document.createElement('a');
          a.href = URL.createObjectURL(blob); a.download = 'prism_' + kind + '.jsonl';
          a.click(); URL.revokeObjectURL(a.href);
        } catch (e) { this._err('내보내기 실패'); }
      },
      ciTxt(ci) { return ci ? (this.pctTxt(ci.acc) + ' · 95% CI ' + this.pctTxt(ci.lo) + '~' + this.pctTxt(ci.hi) + ' (n=' + ci.n + ')') : '·'; },
      async exportSpec() {
        try {
          const res = await fetch('/learn-spec', { headers: this._authHeaders() });
          if (!res.ok) { this._err('소요서 생성 실패'); return; }
          const blob = await res.blob(); const a = document.createElement('a');
          a.href = URL.createObjectURL(blob); a.download = 'prism_finetune_spec.md'; a.click(); URL.revokeObjectURL(a.href);
        } catch (e) { this._err('소요서 생성 실패'); }
      },
      async exportHandoff() {
        try {
          const res = await fetch('/handoff-export', { headers: this._authHeaders() });
          if (!res.ok) { this._err('핸드오프 번들 생성 실패'); return; }
          const m = (res.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/);
          const blob = await res.blob(); const a = document.createElement('a');
          a.href = URL.createObjectURL(blob); a.download = (m && m[1]) || 'prism_handoff.zip';
          a.click(); URL.revokeObjectURL(a.href);
        } catch (e) { this._err('핸드오프 번들 생성 실패'); }
      },
      pctTxt(v) { return (v == null) ? '·' : (Math.round(v * 1000) / 10) + '%'; },
      // 카테고리 옵션(IAB Tier1 / Tier2) · 빈칸 채우기 피커용
      get categoryOptions() {
        const d = this.dictData; if (!d) { if (!this._dictReq) { this._dictReq = true; this.loadDict(); } return []; }
        const out = [];
        (d.iabTier1 || []).forEach((t1) => { out.push(t1); ((d.tier2 && d.tier2[t1]) || []).forEach((t2) => out.push(t1 + ' / ' + t2)); });
        return out;
      },
      async fillCategory(c, val) {
        if (!val || !c) return;
        const cats = [val];
        let r = null;
        try { r = await (await this._afetch('/patch-meta', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ hash: c.hash, patch: { content_category: cats }, reviewer: this.reviewer || '' }) })).json(); } catch (e) { this._err('분류 저장 실패'); return; }
        c.category = cats;                                         // 로컬 즉시 반영
        this.celebratePoints(5, '분류 채움');
        (r && r.missions_completed || []).forEach((m) => this.celebratePoints(m.bonus, '미션 달성 · ' + m.label));
        this.loadGoldenStatus();
      },
      async adminAct(action, member) {
        if (action === 'clear_feedback' && !(await this.dsConfirm('우리 팀의 평가 피드백을 모두 삭제할까요? 게임 점수·레벨은 보존됩니다.', { ok: '삭제', danger: true }))) return;
        if (action === 'clear_contents' && !(await this.dsConfirm('우리 팀의 검토 콘텐츠를 모두 삭제할까요?', { ok: '삭제', danger: true }))) return;
        if (action === 'clear_golden' && !(await this.dsConfirm('정답셋(골든)을 모두 삭제할까요? 되돌릴 수 없습니다.', { ok: '삭제', danger: true }))) return;
        if (action === 'reset_scores' && !(await this.dsConfirm('팀 전원의 게임 점수·레벨을 0부터 다시 시작할까요? 검수 데이터·배지·정답셋은 그대로 둡니다.', { ok: '초기화', danger: true }))) return;
        if (action === 'delete_team') {
          if (!(await this.dsConfirm('팀을 삭제할까요? 멤버 소속이 모두 해제됩니다.', { ok: '팀 삭제', danger: true }))) return;
          if (!(await this.dsConfirm('정말 삭제합니다. 되돌릴 수 없습니다.', { ok: '최종 삭제', danger: true }))) return;
        }
        try { await this._afetch('/admin', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ action: action, member: member }) }); } catch (e) {}
        this.loadAdmin();
      },
      // 원문 링크 백필: 매핑 파일 업로드 → source_url 만 갱신(초안·판정 불변) · 결과 요약 표시
      async backfillUrls() {
        const f = this.$refs.bfFile && this.$refs.bfFile.files[0];
        if (!f) { this.bfMsg = '매핑 파일을 먼저 선택하세요'; return; }
        this.bfBusy = true; this.bfMsg = ''; this.bfMisses = [];
        try {
          const fd = new FormData(); fd.append('file', f, f.name);
          const j = await (await this._afetch('/backfill-urls', { method: 'POST', headers: this.authToken ? { 'Authorization': 'Bearer ' + this.authToken } : {}, body: fd })).json();
          if (j.error) { this.bfMsg = j.error; }
          else {
            const skip = (j.noMatch || 0) + (j.ambiguous || 0);
            this.bfMsg = '링크 갱신 ' + j.updated + '건 · 이미 같음 ' + j.unchanged + '건'
              + (skip ? ' · 미매칭 ' + skip + '건' : '') + (j.badUrl ? ' · URL 형식 오류 ' + j.badUrl + '건' : '');
            this.bfMisses = j.misses || [];
            if (j.updated) this.loadRaw();
          }
        } catch (e) { this.bfMsg = '실패 · 네트워크 상태를 확인하세요'; }
        this.bfBusy = false;
      },
      async createTeam() {
        const nm = (this.newTeamName || '').trim();
        if (!nm) return;
        try {
          const r = await (await this._afetch('/admin', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ action: 'create_team', name: nm }) })).json();
          if (!r || !r.ok) { this._err((r && r.error) || '팀 생성 실패'); return; }
          this.teamMsg = '팀 생성됨 · 초대코드 ' + (r.invite || ''); this.newTeamName = '';
          if (r.team && r.team.invite_code) this.myInvite = r.team.invite_code;
          this.loadAdmin();
        } catch (e) { this._err('팀 생성 실패'); }
      },
      copyInvite() { try { navigator.clipboard.writeText((this.adminData && this.adminData.team && this.adminData.team.invite_code) || ''); this.inviteCopied = true; setTimeout(() => { this.inviteCopied = false; }, 1500); } catch (e) {} },
      inviteCopied: false,
      ingestEndpoint: '', ingestN: 20, ingestMsg: '', ingestBusy: false,
      async ingestRun() {
        if (!(this.ingestEndpoint || '').trim()) { this.ingestMsg = '크롤러 엔드포인트를 입력하세요'; return; }
        this.ingestBusy = true; this.ingestMsg = '인입·추출 중…';
        try { const r = await (await this._afetch('/admin', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ action: 'ingest', endpoint: this.ingestEndpoint, n: this.ingestN }) })).json();
          this.ingestMsg = r.ok ? ('✓ ' + r.fetched + '건 인입 → 검수 대기 ' + r.queued + '건 적재') : (r.error || '실패'); } catch (e) { this.ingestMsg = '오류'; }
        this.ingestBusy = false;
      },
      // 결과 출처 필터(자동 인입/단건/배치)
      get srcOptions() { const s = new Set(((this.dashData && this.dashData.contents) || []).map((c) => c.source || '단건')); return [...s]; },
      get filteredContents() { const cs = (this.dashData && this.dashData.contents) || []; return this.srcFilter ? cs.filter((c) => (c.source || '단건') === this.srcFilter) : cs; },
      srcBadgeClass(s) { return s === '자동 인입' ? 'ds-badge--intent' : s === '배치' ? 'ds-badge--category' : 'ds-badge--neutral'; },
      // 아레나 파생값(게이지·내 순위)
      get arenaPct() { const d = this.arenaData; return d ? Math.round((d.accuracy || 0) * 100) : 0; },
      get arenaTargetPct() { const d = this.arenaData; return d ? Math.round((d.target || 0.9) * 100) : 90; },
      // 검수 진척율: 개인(내가 검수한 대상 비율) · 팀(팀원 평균)
      get myProgressPct() { const m = this.arenaMe; return m ? Math.round((m.progress || 0) * 100) : 0; },
      get teamProgressPct() { const d = this.arenaData; return d ? Math.round((d.team_progress || 0) * 100) : 0; },
      get reviewTargets() { const d = this.arenaData; return d ? (d.total_targets || 0) : 0; },
      // 시작하기(온보딩) 카드: 권한별 노출 · '다음부터 표시 안 함' localStorage 영속
      starterHide: (() => { try { return localStorage.getItem('prism_starter_hide') === '1'; } catch (e) { return false; } })(),
      hideStarter() { this.starterHide = true; try { localStorage.setItem('prism_starter_hide', '1'); } catch (e) {} },
      get starterGuide() {
        const g = (this.cfg && this.cfg.guideUrls) || {};
        return ((this.adminData && this.adminData.isAdmin) ? g.guide_admin : g.guide_user) || g.guide || '';
      },
      get starterVisible() {
        if (this.starterHide || !this.arenaData) return false;
        if (this.adminData && this.adminData.isAdmin) return !this.arenaData.total_targets && !this.arenaData.queue;
        const m = this.arenaMe;                        // 멤버: 첫 판정 전까지 안내
        return !((m && m.reviews) || 0);
      },
      get arenaMe() { const d = this.arenaData; if (!d || !this.reviewer) return null; return (d.leaderboard || []).find((r) => r.reviewer === this.reviewer) || null; },
      get arenaMyRank() { const d = this.arenaData; if (!d || !this.reviewer) return 0; const i = (d.leaderboard || []).findIndex((r) => r.reviewer === this.reviewer); return i < 0 ? 0 : i + 1; },
      rankMedal(i) { return ['🥇', '🥈', '🥉'][i] || ('#' + (i + 1)); },
      // 주간 리그(D-9): 이번 주 점수 순위 + 승급/강등 존 + 지난주 대비 이동
      weeklyLeague() {
        const d = this.arenaData; if (!d || !d.leaderboard) return [];
        const board = d.leaderboard.map((r) => ({ ...r, wp: r.week_points || 0, lwp: r.last_week_points || 0 }))
          .sort((a, b) => b.wp - a.wp);
        const n = board.length;
        const upN = Math.max(1, Math.ceil(n * 0.3));
        const downN = n >= 5 ? Math.max(1, Math.floor(n * 0.2)) : 0;
        return board.map((r, i) => ({ ...r, rank: i + 1, delta: r.wp - r.lwp,
          zone: r.wp <= 0 ? 'idle' : (i < upN ? 'up' : (downN && i >= n - downN ? 'down' : 'keep')) }));
      },
      leagueActive() { return this.weeklyLeague().filter((r) => r.wp > 0).length; },
      leagueZoneKr(z) { return { up: '승급권', down: '강등권', keep: '유지권', idle: '대기' }[z] || ''; },
      leagueZoneLabel(z) { return { up: '▲ 승급', down: '▼ 강등', keep: '유지', idle: '대기' }[z] || ''; },
      leagueZoneClass(z) { return { up: 'ds-badge--success', down: 'ds-badge--category', keep: 'ds-badge--neutral', idle: 'ds-badge--neutral' }[z] || 'ds-badge--neutral'; },
      // 캐릭터 육성: 레벨 → 성장 티어·타이틀·XP
      levelTier(L) { return L >= 10 ? 4 : L >= 7 ? 3 : L >= 4 ? 2 : L >= 2 ? 1 : 0; },
      levelTitle(L) { return ['새내기 검수자', '숙련 검수자', '베테랑 검수자', '검수 마스터', '전설의 검수자'][this.levelTier(L)]; },
      levelEmoji(L) { return ['🌱', '🔰', '⭐', '🏆', '👑'][this.levelTier(L)]; },
      // 게이미피케이션(KB 프레임워크 적용): Flow 단계 + 배지 컬렉션(성취) + 오늘의 미션(도전)
      flowStage(L) { return L >= 10 ? 'Master' : L >= 4 ? 'Regular' : 'Rookie'; },
      flowStageKr(L) { return L >= 10 ? '마스터' : L >= 4 ? '정착' : '입문'; },
      badges() {
        const m = this.arenaMe; const r = (m&&m.reviews)||0, c = (m&&m.corrections)||0, s = (m&&m.streak)||0, L = (m&&m.level)||0;
        // 배지 세트(22) · 5분류: 볼륨·스트릭·기여·품질·지위. 개인 1만 건 검수 완주 시 전 배지 달성 규모.
        // 품질 배지는 개인 실측(골드 정확도·합의·불일치 해소)
        // 기반 = 유능감 정보 제공(Sailer 2017 · Ryan & Deci 2000). cur/target/unit = 진행도(모달 표시용).
        const gn = (m&&m.gold_n)||0, ga = (m&&m.gold_acc)||0, cm = (m&&m.consensus_matches)||0, sr = (m&&m.split_reviews)||0;
        const gap = Math.round(ga * 100);
        return [
          { icon: '🌱', label: '첫 검수', desc: '첫 검수를 완료했어요', exp: 10, color: '#18ba45', cat: '볼륨', cur: r, target: 1, unit: '검수', got: r >= 1 },
          { icon: '📖', label: '검수 50', desc: '누적 50건 검수', exp: 50, color: '#1e84ff', cat: '볼륨', cur: r, target: 50, unit: '검수', got: r >= 50 },
          { icon: '📚', label: '검수 250', desc: '누적 250건 검수', exp: 120, color: '#5c77ff', cat: '볼륨', cur: r, target: 250, unit: '검수', got: r >= 250 },
          { icon: '🏆', label: '검수 1,000', desc: '누적 1,000건 검수', exp: 300, color: '#f5a623', cat: '볼륨', cur: r, target: 1000, unit: '검수', got: r >= 1000 },
          { icon: '🚀', label: '검수 2,500', desc: '누적 2,500건 검수', exp: 500, color: '#ff9429', cat: '볼륨', cur: r, target: 2500, unit: '검수', got: r >= 2500 },
          { icon: '🌌', label: '검수 5,000', desc: '누적 5,000건 검수', exp: 800, color: '#a05cff', cat: '볼륨', cur: r, target: 5000, unit: '검수', got: r >= 5000 },
          { icon: '🏔️', label: '완주 10,000', desc: '누적 1만 건 검수 · 여정 완주', exp: 2000, color: '#f5a623', cat: '볼륨', cur: r, target: 10000, unit: '검수', got: r >= 10000 },
          { icon: '🔥', label: '연속 3일', desc: '3일 연속 검수', exp: 15, color: '#ff9429', cat: '스트릭', cur: s, target: 3, unit: '일', got: s >= 3 },
          { icon: '⚡', label: '연속 7일', desc: '7일 연속 검수', exp: 30, color: '#ff6a3d', cat: '스트릭', cur: s, target: 7, unit: '일', got: s >= 7 },
          { icon: '☄️', label: '연속 30일', desc: '30일 연속 검수', exp: 120, color: '#ff4e33', cat: '스트릭', cur: s, target: 30, unit: '일', got: s >= 30 },
          { icon: '🌋', label: '연속 100일', desc: '100일 연속 검수', exp: 500, color: '#d83a2c', cat: '스트릭', cur: s, target: 100, unit: '일', got: s >= 100 },
          { icon: '🏅', label: '개선 채택', desc: '개선안이 채택됐어요', exp: 25, color: '#a05cff', cat: '기여', cur: c, target: 1, unit: '개선', got: c >= 1 },
          { icon: '🛠️', label: '개선 50', desc: '개선안 50건 채택', exp: 150, color: '#7c5cff', cat: '기여', cur: c, target: 50, unit: '개선', got: c >= 50 },
          { icon: '⚒️', label: '개선 500', desc: '개선안 500건 채택', exp: 600, color: '#6a3dff', cat: '기여', cur: c, target: 500, unit: '개선', got: c >= 500 },
          { icon: '🎯', label: '골드 정확도 90%', desc: '골드 문항 10건 이상 · 정확도 90%', exp: 60, color: '#18ba45', cat: '품질', cur: gap, target: 90, unit: '%', got: gn >= 10 && ga >= 0.9 },
          { icon: '🎯', label: '골드 정확도 95%', desc: '골드 문항 50건 이상 · 정확도 95%', exp: 250, color: '#0f9c38', cat: '품질', cur: gap, target: 95, unit: '%', got: gn >= 50 && ga >= 0.95 },
          { icon: '🤝', label: '합의 메이커', desc: '팀 합의와 일치한 판정 500건', exp: 200, color: '#1e84ff', cat: '품질', cur: cm, target: 500, unit: '건', got: cm >= 500 },
          { icon: '⚖️', label: '불일치 해결사', desc: '의견 갈린 콘텐츠 재검토 100건', exp: 200, color: '#a05cff', cat: '품질', cur: sr, target: 100, unit: '건', got: sr >= 100 },
          { icon: '💎', label: '골든 기여 100', desc: '내 검수가 골든(정답) 확정 100건에 기여', exp: 300, color: '#f5a623', cat: '품질', cur: (m&&m.golden_contribs)||0, target: 100, unit: '건', got: ((m&&m.golden_contribs)||0) >= 100 },
          { icon: '⭐', label: 'Lv.10 마스터', desc: '레벨 10 도달(약 380건 검수)', exp: 100, color: '#ffb020', cat: '지위', cur: L, target: 10, unit: 'Lv', got: L >= 10 },
          { icon: '🌟', label: 'Lv.25', desc: '레벨 25 도달(여정의 절반 고지)', exp: 400, color: '#f5a623', cat: '지위', cur: L, target: 25, unit: 'Lv', got: L >= 25 },
          { icon: '👑', label: 'Lv.50 만렙', desc: '레벨 50 · 약 1만 건 검수 완주', exp: 2000, color: '#f5a623', cat: '지위', cur: L, target: 50, unit: 'Lv', got: L >= 50 },
                ];
      },
      badgeModalOpen: false,
      get badgeGot() { return this.badges().filter((x) => x.got).length; },
      async checkBadges() {
        if (!this.arenaMe) return;
        const key = 'prism_badges_' + (this.reviewer || '');
        // 기준선(이미 축하함): 서버 우선(_badgeSeen, 기기 간) → 없으면 로컬 폴백
        let localSeen = null;
        try { const raw = localStorage.getItem(key); if (raw) localSeen = JSON.parse(raw); } catch (e) {}
        const server = Array.isArray(this._badgeSeen) ? this._badgeSeen : null;
        const firstLoad = (server === null && localSeen === null);   // 최초 진입 = 기준선만
        const base = [].concat(server || [], localSeen || []);
        const got = this.badges().filter((x) => x.got).map((x) => x.label);
        const fresh = got.filter((l) => !base.includes(l));
        try { localStorage.setItem(key, JSON.stringify(got)); } catch (e) {}
        // 서버 영속(단조 증가) · 신규가 있거나 서버 기준선이 아직 없을 때
        if (this.reviewer && (fresh.length || server === null)) {
          try {
            const r = await (await this._afetch('/badges', { method: 'POST', headers: this._authHeaders(),
              body: JSON.stringify({ reviewer: this.reviewer, earned: got }) })).json();
            if (r && Array.isArray(r.badges)) this._badgeSeen = r.badges;
          } catch (e) {}
        }
        if (firstLoad) return;                            // 최초 기준선은 축하 생략(스팸 방지)
        if (fresh.length) { const bd = this.badges().find((x) => x.label === fresh[0]); if (bd) this.celebrateBadge(bd); }
      },
      celebrateBadge(bd) { this.badgeToast = bd; if (this._btT) clearTimeout(this._btT); this._btT = setTimeout(() => { this.badgeToast = null; }, 4500); },
      // 검수 완료 → 점수 상승 리워드(성취감). 연속 검수 시 key 로 애니메이션 재시작.
      celebratePoints(pts, label) {
        this._ptId = (this._ptId || 0) + 1;
        this.ptToast = { id: this._ptId, pts: pts, label: label || '' };
        const id = this._ptId;
        if (this._ptT) clearTimeout(this._ptT);
        this._ptT = setTimeout(() => { if (this.ptToast && this.ptToast.id === id) this.ptToast = null; }, 1700);
      },
      // 오늘의 미션: 서버 판정·보상(arenaData.missions). 아래 getter 는 미션 데이터 없을 때 폴백 안내.
      get missionList() { return (this.arenaData && this.arenaData.missions) || []; },
      get todayMission() {
        const q = (this.arenaData && this.arenaData.queue) || 0;
        if (q > 0) return { txt: '대기 ' + q + '건 비우기 🔥', to: 'review', cta: '검수하기' };
        return { txt: '일치율 점검', to: 'evaluate', cta: '평가' };
      },
      // 레벨 커브(서버 store.level_of 와 동일): 구간 요구 pt = 100 + 80×(레벨-1) · Lv.50 만렙 = 98,980pt ≈ 검수 1만 건
      lvlFloor(l) { return (l - 1) * 100 + 40 * (l - 1) * (l - 2); },
      lvlNeed(l) { return 100 + 80 * (l - 1); },
      xpPct(r) {
        if (!r) return 0; const L = r.level || 1;
        if (L >= 50) return 100;
        return Math.max(0, Math.min(100, Math.round(((r.points || 0) - this.lvlFloor(L)) / this.lvlNeed(L) * 100)));
      },
      xpToNext(r) { if (!r) return 0; const L = r.level || 1; return L >= 50 ? 0 : Math.max(0, this.lvlFloor(L + 1) - (r.points || 0)); },
      async queueFeedback(it, verdict) {
        if (!this.ensureReviewer()) return;
        it.note = it.note || '';
        const wasReviewed = !!it.myVerdict;
        const r = await this._postFb({ hash: it.hash, service: it.service, title: it.title, model: it.model || '', verdict: verdict, stage: 'review', note: it.note });
        if (r && r.gold) {                              // 골드 문항: 응답 후 정오답 공개(즉시 학습 피드백)
          it.reviewed = true; it.myVerdict = verdict; it.goldRevealed = true; it.goldCorrect = !!r.gold.correct;
          if (r.gold.correct) this.celebratePoints(10, '골드 문항 정답');
          else this.liveToast('골드 문항 · 정답과 달랐어요(품질 점수에 반영)');
          return;
        }
        if (r && r.error) { this._err(r.error); return; }
        if (!wasReviewed) this.celebratePoints((verdict === 'bad' && (it.note || '').trim()) ? 25 : 10, '검수 완료');
        it.reviewed = true; it.myVerdict = verdict;
        if (this.queueOnlyUnreviewed && !it.split) this.queueData.items = (this.queueData.items || []).filter((x) => x.hash !== it.hash);
      },
      ensureReviewer() { if (!(this.reviewer || '').trim()) { this.reviewerEditing = true; return false; } return true; },
      notifyViewing(it) { try { fetch('/presence', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reviewer: this.reviewer, hash: it.hash, action: 'viewing' }) }); } catch (e) {} },
      async clearFeedback() {
        if (!(await this.dsConfirm('누적된 평가 피드백과 학습 보정을 모두 초기화할까요?', { ok: '초기화', danger: true }))) return;
        try { await fetch('/feedback', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ clear: true }) }); } catch (e) {}
        this.loadDash();
      },
      learnedStages: { extract: false, analyze: false, review: false, judge: false },
      async loadPromptDefaults() { try { await this.refreshConfig(); const d = await (await fetch('/prompt-defaults')).json(); this.learnedStages = d.learned || this.learnedStages; } catch (e) {} },
      async loadTopics() { this.modBusy = true; try { this.topicData = await (await fetch('/topics')).json(); } catch (e) {} this.modBusy = false; },
      async loadDict() { this.modBusy = true; try { this.dictData = await (await this._afetch('/dict')).json(); if (!this.dictGroup) this.dictGroup = (this.dictData.serviceGroups || [])[0] || ''; } catch (e) {} this.modBusy = false; },
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
        try { const r = await this._afetch('/dict', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ reset: true }) }); this.dictData = await r.json(); } catch (e) {}
      },
      async loadUser() { this.modBusy = true; try { this.userData = await (await this._afetch('/usermeta', { headers: this.authToken ? { 'Authorization': 'Bearer ' + this.authToken } : {} })).json(); } catch (e) {} this.modBusy = false; },
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
      get qm() { return (this.result && this.result.output && this.result.output.quality_meta) || {}; },
      get lm() { return (this.result && this.result.output && this.result.output.legal_meta) || {}; },
      get tr() { return (this.result && this.result.output && this.result.output.trace) || {}; },
      routerKeyPresent(p) { return p === 'bizrouter' ? !!this.cfg.hasBizKey : p === 'timely' ? !!this.cfg.hasTimelyKey : false; },
      isRouter(p) { return p === 'bizrouter' || p === 'timely'; },
      providerHasKey(p) { return p === 'solar' || p === 'upstage_ie' ? !!this.cfg.hasKey : this.routerKeyPresent(p); },
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
      sizeLabel(b) { if (!b) return ''; if (b < 1024) return b + 'B'; if (b < 1048576) return (b / 1024).toFixed(0) + 'KB'; return (b / 1048576).toFixed(1) + 'MB'; },
      onExcel(e) { this.excelFile = e.target.files[0] || null; e.target.value = ''; this.status = ''; },
      onDropExcel(e) { this.xlsDrag = false; const f = e.dataTransfer.files[0]; if (f) this.excelFile = f; },
      clearExcel() { this.excelFile = null; },

      // ── 결과 복사 / 내보내기 ──
      async copyText(t, label) {
        try { await navigator.clipboard.writeText(t || ''); this.flashCopy((label || '복사') + ' 됨'); }
        catch (e) { this.flashCopy('복사 실패'); }
      },
      copyJSON() { this.copyText(JSON.stringify(this.result ? this.result.output : {}, null, 2), 'JSON'); },
      flashCopy(m) { this.copyMsg = m; clearTimeout(this._cpT); this._cpT = setTimeout(() => { this.copyMsg = ''; }, 1600); },
      exportBatchCsv() {
        const its = (this.batchResult && this.batchResult.items) || [];
        const esc = (v) => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
        const rows = [['제목', '리드문', '엔티티', '인텐트', '등급']];
        for (const it of its) rows.push([it.title, it.summary, (it.entities || []).join(' · '), (it.intent || []).join(' · '), it.grade]);
        const csv = '\ufeff' + rows.map((r) => r.map(esc).join(',')).join('\r\n');
        const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
        const a = document.createElement('a'); a.href = url; a.download = 'prism_results.csv';
        document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
      },
      get modelOptions() {
        const a = this.models.slice();
        if (this.cfgModel && !a.includes(this.cfgModel)) a.unshift(this.cfgModel);
        return a;
      },
      async loadModels() {                     // 모델 새로고침: 전 제공자(직접 Solar 실조회 + 라우터 카탈로그)
        this.modelsMsg = '불러오는 중…'; this.cfgBusy = true;
        try {
          if (this.keyInputs.solar) {              // 입력한 키를 먼저 적용(세션)
            await this._afetch('/config', { method: 'POST', headers: this._authHeaders(),
              body: JSON.stringify({ api_key: this.keyInputs.solar }) });
          }
          try {                                    // Solar 는 실조회(키 있을 때) · 실패해도 전체는 계속
            const j = await (await fetch('/models')).json();
            if (j.ok) {
              this.models = j.models;
              if (!this.cfgModel || !this.models.includes(this.cfgModel))
                this.cfgModel = this.cfg.model || this.models[0] || '';
            }
          } catch (e) {}
          await this.refreshConfig();
          const total = this.textGroups.reduce((n, g) => n + g.items.length, 0);
          const on = this.textGroups.filter((g) => g.on).length;
          this.modelsMsg = '전 제공자 ' + total + '개 모델 · 연결 ' + on + '/' + this.textGroups.length;
        } catch (e) { this.modelsMsg = '오류: ' + e; }
        this.cfgBusy = false;
      },
      async refreshConfig() {
        try {
          const r = await this._afetch('/config'); this.cfg = await r.json();
          if (this.cfg.backend) this.backend = this.cfg.backend;     // sqlite | supabase
          this._seenBoot(this.cfg.bootId);                            // 배포 감지 폴백(SSE 차단 환경)
          if (this.backend === 'supabase' && this.authToken) this.ensureAdmin();  // 관리자 여부 → nav 게이팅(재시도 포함)
          if (!this.cfgModel) this.cfgModel = this.cfg.model;
          if (this.cfg.reasoning) this.reasoning = this.cfg.reasoning;
          if (typeof this.cfg.systemPrompt === 'string') this.systemPrompt = this.cfg.systemPrompt;
          if (Array.isArray(this.cfg.availableModels)) this.availableModels = this.cfg.availableModels;
          if (this.cfg.goldenMinGood) this.goldenMinGood = this.cfg.goldenMinGood;
          if (typeof this.cfg.learnNextAt === 'string') this.learnNextAt = this.cfg.learnNextAt;
          if (typeof this.cfg.desktopAllowDownloads === 'boolean') this.dtAllowDl = this.cfg.desktopAllowDownloads;
          if (typeof this.cfg.desktopPersistStorage === 'boolean') this.dtPersist = this.cfg.desktopPersistStorage;
          if (!this.wrapDraft) this.syncWrapDraft();
          if (!this.cmpA && this.availableModels.length) { this.cmpA = this.availableModels[0]; this.cmpB = this.availableModels[1] || ''; }   // A/B 기본 슬롯
          if (Array.isArray(this.cfg.ingestSources)) this.ingestSources = this.cfg.ingestSources.slice();
          if (this.cfg.guideUrls) this.teamLinks = Object.assign({ guide: '', guide_user: '', guide_admin: '' }, this.cfg.guideUrls);
          if (!this._keyTargetInit) { this._keyTargetInit = true; this.keyTarget = ['bizrouter', 'timely', 'solar'].find((s) => this.keyState(s)) || 'bizrouter'; }
          if (this.cfg.textProvider) this.textProvider = this.cfg.textProvider;
          if (typeof this.cfg.textModel === 'string' && this.cfg.textModel) this.textModel = this.cfg.textModel;
          if (this.cfg.visionProvider) this.visionProvider = this.cfg.visionProvider;
          if (typeof this.cfg.visionModel === 'string' && this.cfg.visionModel) this.visionModel = this.cfg.visionModel;
          if (typeof this.cfg.legalEnabled === 'boolean') this.legalEnabled = this.cfg.legalEnabled;
        } catch (e) { /* noop */ }
      },
      async toggleLegal() {
        try { await this._afetch('/config', { method: 'POST', headers: this._authHeaders(),
          body: JSON.stringify({ legal_enabled: this.legalEnabled }) }); } catch (e) {}
      },
      // ── 키(서비스별) ──
      keyState(service) { return !!this.cfg[this.keyDefs[service].has]; },
      keyPersisted(service) { return !!this.cfg[this.keyDefs[service].persisted]; },
      async saveKey(service) {
        this.keyMsgs[service] = '저장 중…'; this.cfgBusy = true;
        try {
          const body = { persist: this.cfgPersist };
          if (service === 'solar') { body.api_key = this.keyInputs.solar; if (this.cfgModel) body.model = this.cfgModel; }
          else body[service + '_api_key'] = this.keyInputs[service];
          const r = await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(body) });
          const j = await r.json();
          if (!r.ok || (j && j.error)) { this.keyMsgs[service] = '오류: ' + ((j && j.error) || r.status); this.cfgBusy = false; return; }
          this.cfg = j; this.keyInputs[service] = '';
        } catch (e) { this.keyMsgs[service] = '오류: ' + e; this.cfgBusy = false; return; }
        if (this.keyState(service)) {
          if (service === 'solar' && !this.models.length) this.loadModels();
          await this.testConn(service);                           // 저장 즉시 연결 검증(전 서비스)
        } else { this.keyMsgs[service] = '저장 실패'; this.cfgBusy = false; }
      },
      async forgetKey(service) {
        try {
          const body = {}; if (service === 'solar') body.forget = true; else body['forget_' + service] = true;
          const r = await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(body) });
          const j = await r.json();
          if (!r.ok || (j && j.error)) { this.keyMsgs[service] = '오류: ' + ((j && j.error) || r.status); return; }
          this.cfg = j; this.keyMsgs[service] = '키 삭제됨';
        } catch (e) { this.keyMsgs[service] = '오류: ' + e; }
      },
      async saveTeamLinks() {
        this.tlMsg = '저장 중…';
        try {
          const r = await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ team_links: this.teamLinks }) });
          const j = await r.json();
          if (!r.ok || (j && j.error)) { this.tlMsg = '오류: ' + ((j && j.error) || r.status); return; }
          this.cfg = j; this.tlMsg = '✓ 저장됨';
        } catch (e) { this.tlMsg = '오류: ' + e; }
      },
      async testConn(service = 'solar') {
        this.cfgBusy = true; this.keyMsgs[service] = '연결 테스트 중…';
        try { const j = await (await this._afetch('/ping', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(service === 'solar' ? {} : { service }) })).json();
          this.keyMsgs[service] = (j.ok ? '✓ 성공 · ' : '✗ 실패 · ') + j.detail; }
        catch (e) { this.keyMsgs[service] = '오류: ' + e; }
        await this.refreshConfig(); this.cfgBusy = false;
      },
      // 텍스트 슬롯(메타 생성)
      async saveTextSlot() {
        this.slotMsg = '저장 중…';
        const payload = { text_provider: this.textProvider };
        if (this.isRouter(this.textProvider)) payload.text_model = this.textModel;
        else if (this.cfgModel) payload.model = this.cfgModel;
        try { const r = await this._afetch('/config', { method: 'POST', headers: this._authHeaders(),
            body: JSON.stringify(payload) }); this.cfg = await r.json(); this.slotMsg = '✓ 적용됨'; }
        catch (e) { this.slotMsg = '오류: ' + e; }
      },
      setReasoning(id) { this.reasoning = id; fetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ reasoning: id }) }).catch(() => {}); },
      async clearStore() {
        if (!(await this.dsConfirm('적재된 추출 결과를 모두 삭제할까요? (되돌릴 수 없음)', { ok: '삭제', danger: true }))) return;
        try { const r = await this._afetch('/store', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ clear: true }) });
          const d = await r.json(); this.cfg.storedCount = d.count || 0; this.loadDash(); } catch (e) {}
      },

      // ── 자동 인입 파이프라인 소스(API/Kafka) ──
      ingestSources: [], ingestMsg: '',
      newSrc: { type: 'api', name: '', endpoint: '', method: 'GET', auth: '', brokers: '', topic: '', group: '', interval: '60' },
      resetNewSrc() { this.newSrc = { type: 'api', name: '', endpoint: '', method: 'GET', auth: '', brokers: '', topic: '', group: '', interval: '60' }; },
      addSource() {
        if (!this.newSrc.name.trim()) { this.ingestMsg = '이름을 입력하세요'; return; }
        this.ingestSources.push(Object.assign({ id: 'src-' + Date.now(), enabled: true }, this.newSrc));
        this.resetNewSrc(); this.saveIngest();
      },
      removeSource(id) { this.ingestSources = this.ingestSources.filter((s) => s.id !== id); this.saveIngest(); },
      toggleSource(s) { s.enabled = !s.enabled; this.saveIngest(); },
      ingestBusy: {}, ingestRunMsg: {}, ingestJobs: [], _ingestPoll: null,
      delArm: '', _delArmT: null,                  // 콘텐츠 개별 삭제 2단계 확인
      jobFilter: null,                              // 실행 큐 작업 클릭 -> 해당 콘텐츠만 보기 {name, hashes}
      jobContents(j) {
        if (!(j.hashes || []).length) return;
        this.jobFilter = { name: j.name, kind: j.kind || '', hashes: j.hashes };
        this.$nextTick(() => { const el = document.getElementById('added-contents'); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); });
      },
      get contentRows() {
        const all = (this.dashData && this.dashData.contents) || [];
        if (!this.jobFilter) return all;
        const set = new Set(this.jobFilter.hashes);
        return all.filter((c) => set.has(c.hash));
      },
      fmtEta(s) { s = Math.max(0, Math.round(s || 0)); return s >= 60 ? (Math.floor(s / 60) + '분 ' + (s % 60) + '초') : (s + '초'); },
      async removeContent(c) {
        if (this.delArm !== c.hash) {              // 1차 클릭 = 확인 대기(3초)
          this.delArm = c.hash;
          clearTimeout(this._delArmT); this._delArmT = setTimeout(() => { this.delArm = ''; }, 3000);
          return;
        }
        this.delArm = ''; clearTimeout(this._delArmT);
        try {
          const r = await (await this._afetch('/content-remove', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ hash: c.hash }) })).json();
          if (r && r.ok) { this.loadDash(); this.loadRaw && this.loadRaw(); }
          else this._err((r && r.error) || '삭제 실패');
        } catch (e) { this._err('삭제 실패'); }
      },
      async ingestNow(s) {
        this.ingestBusy[s.id] = true; this.ingestRunMsg[s.id] = '';
        this.pollIngestStatus();                         // 진행률 폴링 시작
        try {
          const r = await (await fetch('/ingest-run', { method: 'POST', headers: this._authHeaders(),
            body: JSON.stringify({ id: s.id, name: s.name, endpoint: s.endpoint, method: s.method || 'GET', auth: s.auth || '', limit: 100 }) })).json();
          if (!r.ok) { this.ingestRunMsg[s.id] = '오류: ' + (r.error || '실패') + (r.headers ? (' / 헤더: ' + r.headers.join(', ')) : ''); }
          else { this.ingestRunMsg[s.id] = `✓ ${r.fetched}건 수신 → 신규 ${r.inserted} · 갱신 ${r.updated} · 제외 ${r.skipped}` + (r.mock ? ' (mock)' : ''); this.loadDash(); }
        } catch (e) { this.ingestRunMsg[s.id] = '오류: ' + e; }
        this.ingestBusy[s.id] = false;
      },
      // 자동 인입 상태(진행률) 폴링 · 실행 큐/자동 인입 뷰에서 사용
      async fetchIngestStatus() { try { const d = await (await fetch('/ingest-status')).json(); this.ingestJobs = d.jobs || []; if (d.running) this.loadDashThrottled(); return d; } catch (e) { return { jobs: [], running: false }; } },
      pollIngestStatus() {
        if (this._ingestPoll) return;
        let seenRun = false, empties = 0;              // 작업 등록 전 첫 조회에 폴링이 꺼지던 결함 방지
        const tick = async () => {
          const d = await this.fetchIngestStatus();
          if (d.running) { seenRun = true; empties = 0; return; }
          empties++;
          if ((seenRun || empties >= 4) && !this.loading && !this.bulkBusy) {
            clearInterval(this._ingestPoll); this._ingestPoll = null; this.loadDash();
          }
        };
        this._ingestPoll = setInterval(tick, 1500); tick();
      },
      loadDashThrottled() { const now = Date.now(); if (now - (this._lastDash || 0) > 4000) { this._lastDash = now; this.loadDash(); } },
      get runningJobs() { return (this.ingestJobs || []).filter((j) => j.running); },
      get runningCount() { return (this.loading ? 1 : 0) + this.runningJobs.length; },
      srcJob(s) { return (this.ingestJobs || []).find((j) => j.id === s.id); },
      srcRunning(s) { const j = this.srcJob(s); return !!(this.ingestBusy[s.id] || (j && j.running)); },
      async saveIngest() {
        this.ingestMsg = '저장 중…';
        try { await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ ingest_sources: this.ingestSources }) }); this.ingestMsg = '✓ 저장됨'; clearTimeout(this._inT); this._inT = setTimeout(() => { this.ingestMsg = ''; }, 1600); } catch (e) { this.ingestMsg = '오류: ' + e; }
      },
      // ── 현황 결과 엑셀(CSV) 다운로드 ──
      _dl(name, rows) {
        const esc = (v) => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
        const csv = '\ufeff' + rows.map((r) => r.map(esc).join(',')).join('\r\n');
        const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
        a.download = name; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
      },
      exportDash() { window.open('/export.csv', '_blank'); },
      exportTopics() {
        const d = this.topicData || {}; const rows = [['유형', '클러스터', '대표 엔티티', '멤버']];
        (d.single || []).forEach((t) => rows.push(['엔티티형', t.cluster_id, (t.entities || t.rep_entities || []).join(' · '), t.n_contents || '']));
        (d.composite || []).forEach((t) => rows.push(['사건형', t.cluster_id, (t.rep_entities || t.entities || []).join(' · '), t.n_contents || '']));
        (d.filter || []).forEach((t) => rows.push(['조건형', t.cluster_id, (t.name || t.label || ''), t.n_contents || '']));
        this._dl('prism_topics.csv', rows);
      },
      exportGolden() {                       // 정답셋 엑셀(CSV) 다운로드
        const its = (this.goldenList && this.goldenList.items) || [];
        const rows = [['제목', '등급', '카테고리', '유래 모델', '버전', '출처', '교정 필요']];
        its.forEach((g) => rows.push([g.title || '', g.grade || '', (g.category || []).join(' · '), g.model || '', g.version ? ('v' + g.version) : '', g.source === 'manual' ? '직접' : '검수', g.fix_needed ? 'Y' : '']));
        this._dl('prism_golden.csv', rows);
      },
      exportUsers() {
        const us = (this.userData && this.userData.users) || []; const rows = [['user_id', '페르소나', '조회', '클릭률', '평균체류', '소비형태', '선호엔티티']];
        us.forEach((u) => rows.push([u.user_id, u.persona, u.engagement.views, u.engagement.click_rate, u.engagement.avg_dwell_sec, Object.entries(u.form).map((e) => e[0] + ':' + e[1]).join(' · '), (u.affinity_entities || []).map((e) => e[0]).join(' · ')]));
        this._dl('prism_users.csv', rows);
      },
      exportEval() {
        const t = this.tr || {}; const rows = [['항목', '값'], ['prompt_version', t.prompt_version || ''], ['fallbacks', (t.fallbacks || []).join(' · ')], ['cost_usd', t.cost_usd || 0], ['tokens', JSON.stringify(t.tokens || {})], ['latency_ms', JSON.stringify(t.latency_ms || {})]];
        this._dl('prism_eval.csv', rows);
      },
      selectTab(id) { this.activeTabId = id; this.status = ''; },

      // DNM 메타 체계(13. 프로젝트 기획 / 1312. 아이템 메타) 기준 item_meta 필드:
      //   summary(리드문) · entities(엔티티) · intent(인텐트) · content_category(콘텐츠 카테고리)
      get im() { return (this.result && this.result.output.item_meta) || {}; },
      get q() { return (this.result && this.result.output.quality_meta) || {}; },
      get contentCats() {
        // 1312: 콘텐츠 단위 카테고리 N개(복수 매핑) → 리스트 그대로
        return this.im.content_category || [];
      },

      // 엑셀 배치 인포그래픽: 총건·등급분포·인텐트 상위·평균 리드문 길이
      get batchStats() {
        const its = (this.batchResult && this.batchResult.items) || [];
        const n = its.length;
        const g = its.filter((x) => x.grade === 'G').length;
        const counts = {};
        let lenSum = 0, lenN = 0;
        for (const x of its) {
          for (const t of (x.intent || [])) counts[t] = (counts[t] || 0) + 1;
          if (x.summary) { lenSum += x.summary.length; lenN += 1; }
        }
        const top = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 5)
          .map(([k, v]) => ({ k, v, pct: n ? Math.round((v / n) * 100) : 0 }));
        return {
          n, g, r: n - g,
          gPct: n ? Math.round((g / n) * 100) : 0,
          ents: its.reduce((s, x) => s + ((x.entities || []).length), 0),
          avgLen: lenN ? Math.round(lenSum / lenN) : 0,
          intents: top,
        };
      },

      async run() {
        this.loading = true; this.status = ''; this.result = null; this.batchResult = null;
        const fd = new FormData();
        fd.append('purpose', this.addPurpose || 'review');   // 추가 용도(STEP 1 선택)
        if (this.activeTabId !== 'image') fd.append('add_only', '1');   // 추가=저장만 · 실행은 STEP 2(이미지는 즉시)
        let endpoint = '/run';
        if (this.activeTabId === 'image') {
          if (!this.imgFiles.length) { this.status = '이미지를 선택하세요'; this.loading = false; return; }
          this.imgFiles.forEach((f, i) => fd.append('image' + i, f));
          fd.append('displayServiceName', this.group);
          fd.append('title', this.imgTitle);
          fd.append('caption', this.imgCaption);
        } else if (this.activeTabId === 'excel') {
          if (!this.excelFile) { this.status = '엑셀/CSV 파일을 선택하세요'; this.loading = false; return; }
          fd.append('file', this.excelFile); endpoint = '/run-batch';
        } else {
          fd.append('displayServiceName', this.group);
          fd.append('title', this.txtTitle);
          fd.append('body', this.txtBody);
          fd.append('source_url', this.txtUrl);
        }
        try {
          if (endpoint === '/run-batch') this.pollIngestStatus();   // 실행 큐 진척도 실시간
          const j = await (await this._afetch(endpoint, { method: 'POST', headers: this.authToken ? { 'Authorization': 'Bearer ' + this.authToken } : {}, body: fd })).json();
          if (j.error) { this.status = '오류: ' + j.error; }
          else if (j.pending) { this.status = '✓ ' + (j.added || 0) + '건 추가됨 · STEP 2 모델 실행에서 초안을 생성하세요'; this.loadDash(); }
          else if (j.source === 'excel') { this.batchResult = j; }
          else { this.result = j; }
        } catch (e) { this.status = '오류: ' + e; }
        finally { this.loading = false; }
      },
    }));
  });
