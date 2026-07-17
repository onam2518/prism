/* Prism 앱 조각 00 · prismApp 프로퍼티 그룹(자동 분할 · 앱 분할 6차).
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({

      tabItems: [{ id: 'text', label: '텍스트' }, { id: 'excel', label: '엑셀' }],   // 이미지는 실험실 미디어 탭 전담(이관)
      activeTabId: 'text',
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
        board: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M21 11.5a7.5 7.5 0 0 1-7.5 7.5H5l1.6-2.6A7.5 7.5 0 1 1 21 11.5Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M9 10h7M9 13h4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
      },
      // 멤버 메뉴: 콘텐츠 검수(판정·교정 → 정답 축적) / 평가(일치율·모델 비교) / 게시판(제안·오류)
      mods: [
        { g: '검수 · 평가', items: [
          { id: 'create', label: '콘텐츠 검수', ic: 'eval' },
          { id: 'evaluate', label: '평가', ic: 'dash' },
          { id: 'board', label: '게시판', ic: 'board' } ] },
        // 콘텐츠 인입(수동·자동·실행 큐)은 '콘텐츠 관리' 단일 메뉴로 통합 · 전부 관리자 통제
        // 권한 3단계: 운영 관리자(전부) > 슈퍼관리자(생성자 부여 · 운영 작업 메뉴) > 팀 관리자('팀 관리'만)
        { g: '관리자', gcond: 'admin', items: [
          { id: 'content', label: '콘텐츠 관리', ic: 'intake', cond: 'opsadmin' },
          { id: 'testset', label: '정답셋 관리', ic: 'eval', cond: 'opsadmin' },
          { id: 'admin', label: '팀 관리', ic: 'admin', cond: 'admin' },
          // 사전 · 정책: 인텐트/카테고리/엔티티(개체 고유키·타입·속성)/정책 4탭
          { id: 'dict', label: '사전 · 정책', ic: 'dict', cond: 'opsadmin' },
          // 스튜디오 = 설계 도구 묶음: 프롬프트(계약·래퍼) + 토픽(클러스터링 설계 · 실험실에서 승격)
          { id: 'studio', label: '스튜디오', ic: 'prompt', cond: 'opsadmin' },
          // 실험실: 지금 테스트하지 않는 탐구 요소(법령·사용자·미디어) 보관
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
      canMenu(id, cond) {                    // 메뉴별 권한(생성자 설정 매트릭스) 기반 가시성 · 서버 강제와 동일 판정
        if (this.backend !== 'supabase') return true;            // 로컬 단독 = 전체
        const ad = this.adminData || {};
        if (ad.isCreator || ad.isSysAdmin) return true;          // 생성자·운영관리자 = 전체
        if (id === 'system') return this.navVisible('sysadmin'); // 시스템 설정 = 운영관리자 고정
        const row = (ad.menuPerms || {})[id];
        if (row) {                                               // 유효 매트릭스(기본+생성자 설정)
          const role = ad.isSuperAdmin ? 'super' : (ad.isAdmin ? 'admin' : null);
          return role ? !!row[role] : false;
        }
        return this.navVisible(cond);                            // 매트릭스 없으면 기존 tier 폴백
      },
      contentTab: 'run',                      // 콘텐츠 관리 STEP 1 카드: 수동(run)/자동(auto)
      addPurpose: 'review',                   // 추가 용도: review 검수용(기본) | eval 평가용(홀드아웃)
      createTab: 'raw',                       // 콘텐츠 검수: raw(검수 대상 콘텐츠·기본) | edit(결과 비교)
      testTab: 'status',                      // 정답셋 관리: status(현황·학습 반영) | golden(정답셋) | data(학습 데이터)
      labTab: 'legal',                        // 실험실(지금 미테스트 요소): legal(법령) | user(사용자) | media(미디어)
      studioTab: 'prompt',                    // 스튜디오: prompt(프롬프트 계약·래퍼) | topic(토픽 설계)
      dictTab: 'intent',                      // 사전·정책: intent(인텐트) | category(카테고리) | entity(엔티티 사전) | policy(품질·법령·처리)
      queueTrig: '',                          // 실행 큐 자동/수동 필터
      get filteredJobs() { return (this.runningJobs || []).filter((j) => !this.queueTrig || (this.queueTrig === 'auto' ? j.trigger === 'auto' : j.trigger !== 'auto')); },
      // 위젯 홈 인터랙션 상태
      chatOpen: false, addMenuOpen: false, editing: false, theme: 'light',
      chatMsgs: [{ from: 'bot', text: '무엇을 도와드릴까요? 작업을 말로 지시해 보세요' }],
      chatDraft: '',
      dashData: null, dictData: null, userData: null, modBusy: false, dictGroup: '',
      // topicData 는 서버 '데이터 없음' 응답(serve.topics_data)과 같은 빈 계약으로 초기화 —
      // null 이면 로드 전 첫 렌더에서 토픽 패널의 topicData.* 표현식들이 콘솔 TypeError 를 던진다(표시 영향은 없던 잔재)
      topicData: { n_contents: 0, single: [], composite: [], filter: [], custom: [], customDefs: [],
                   settings: {}, exclusions: {}, catalog: { intents: [], cats: [], keywords: [], eattrs: [] }, summary: {} },
      topicView: 'all',                     // 토픽 현황 필터: all | manual(수동 생성) | auto(자동 생성)
      topicGenTab: 'manual',                // 토픽 생성하기 탭: manual(4단계 정의) | auto(자동 묶기 기준)
      // 토픽 스튜디오: 자연어+차원으로 조건 기반 토픽을 정의·미리보기·저장 + 자동 클러스터링 튜닝
      studio: { name: '', prompt: '', cats: [], intents: [], keywords: [], eattrs: [], kwInput: '', eaKey: 'gender', eaVal: '', editId: null, auto: { cats: [], intents: [], keywords: [] }, req: { cats: [], intents: [], keywords: [] }, neg: { cats: [], intents: [], keywords: [] } },
      studioPreview: { bundles: [], n_total: 0, must_n: 0, opt_n: 0 },
      studioMsg: '', studioBusy: false, studioSaving: false, studioSuggesting: false, studioModel: '', _studioT: null,
      modelsBusy: false, modelsMsgStudio: '',   // 토픽 스튜디오: 모델 목록 새로고침(라우터 포함) 상태
      // 미디어 메타 파이프라인(T1 자막 파싱 실험기)
      mediaSub: { raw: '', fmt: '' }, mediaRes: null, mediaBusy: false, mediaMsg: '',
      mediaVid: { file: null, caption: '', subs: '' }, mediaVidRes: null, mediaVidBusy: false, mediaVidMsg: '',
      mediaImg: { files: [], caption: '' }, mediaImgRes: null, mediaImgBusy: false, mediaImgMsg: '',
      mediaS5: { text: '', models: [] }, mediaS5Res: null, mediaS5Busy: false, mediaS5Msg: '',
      settingsDraft: { co_min: 2, entity_min: 2 }, settingsMsg: '', settingsSaving: false,
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
      menuPermsEdit: {},      // 메뉴 권한 매트릭스 편집 상태(생성자) · adminData.menuPerms 로 초기화
      menuPermsMsg: '',
      bfBusy: false, bfMsg: '', bfMisses: [],   // 원문 링크 백필(시스템 설정)
      updateAvail: false,     // 새 버전 배포 감지(서버 부팅 ID 변화) · 새로고침 배너
      _adminBusy: false,      // ensureAdmin 동시 실행 가드(초기 이중 트리거 dedupe)
      // 골든셋 평가(정합성) 상태 + 테스트(로우 데이터) 상태
      goldenResult: null, goldenBusy: false, goldenMsg: '',
      rawData: null, rawSel: null,
      // 콘텐츠별 검수 담당 배정(관리자 전용): 편집 중 행·선택 담당자·최소 검수인원
      assignSel: null, assignPick: [], assignMin: 1, assignBusy: false,
      async loadRaw(limit) { try { const p = new URLSearchParams({ limit: String(limit || 200) }); if (this.reviewer) p.set('reviewer', this.reviewer); const r = await (await this._afetch('/raw?' + p.toString())).json(); if (r && r.ok) { this.rawData = r; this.rawSel = null; this.assignSel = null; this._absorbFreshFb(); } } catch (e) {} },
      // 배정 UI 게이트: 로컬(단독)은 항상, 운영(supabase)은 팀 관리자만 · team_members 원천 = adminData.members
      get assignAdmin() { return this.backend !== 'supabase' || !!(this.adminData && this.adminData.isAdmin); },
      get assignMembers() { return (this.adminData && this.adminData.members) || []; },
      assigneeNames(r) {
        const ids = (r && r.assignees) || []; const mem = this.assignMembers;
        return ids.map((id) => { const m = mem.find((x) => x.id === id); return (m && m.name) || id; });
      },
      openAssign(r) {
        if (this.assignSel && this.assignSel.hash === r.hash) { this.assignSel = null; return; }
        this.assignSel = r; this.assignPick = ((r.assignees || []).slice()); this.assignMin = Math.max(1, r.min_reviewers || 1);
      },
      toggleAssign(id) {
        const i = this.assignPick.indexOf(id);
        if (i >= 0) this.assignPick.splice(i, 1); else this.assignPick.push(id);
        if (this.assignMin > this.assignPick.length) this.assignMin = Math.max(1, this.assignPick.length);
      },
      async saveAssign() {
        if (!this.assignSel) return;
        const h = this.assignSel.hash; this.assignBusy = true;
        try {
          const body = JSON.stringify({ hash: h, reviewers: this.assignPick, min_reviewers: this.assignMin });
          const r = await (await this._afetch('/content-assign', { method: 'POST', headers: this._authHeaders(), body })).json();
          if (r && r.ok) {
            // 낙관적 반영: 편집 대상 행에 즉시 배정 결과 반영(다음 loadRaw 전에도 표기 정합)
            const row = ((this.rawData || {}).items || []).find((x) => x.hash === h);
            if (row) { row.assignees = r.assignees || []; row.min_reviewers = r.min_reviewers || 0; }
            this.assignSel = null;
          } else this._err((r && r.error) || '배정 실패');
        } catch (e) { this._err('배정 실패'); }
        this.assignBusy = false;
      },
      // ── 검수자 일괄 배정(슈퍼관리자 이상) · 리포트 옆 버튼 → ds-dialog 모달 ──
      bulkOpen: false, assignBulkBusy: false,   // 일괄 '배정' 전용 · 일괄 '실행'(bulkBusy)과 분리(플래그 공유 시 상호 오염)
      bulkQ: '', bulkSvc: '', bulkGrade: '', bulkRev: 'todo', bulkAsg: 'unassigned',
      bulkPick: [], bulkMin: 1, bulkRandN: 50, bulkChecked: {}, bulkMode: 'same', bulkRandMsg: '',
      bulkGrpN: 1, bulkPickG: [],   // 그룹 선택: 그룹 수 · 그룹별 담당자(bulkChecked 값 = 그룹 번호 1..G)
      // 노출 게이트: 로컬은 항상, 운영은 슈퍼관리자·운영관리자(opsadmin)만
      get opsAdmin() { return this.backend !== 'supabase' || !!(this.adminData && (this.adminData.isSysAdmin || this.adminData.isSuperAdmin)); },
      // 배정 모달 풀은 넉넉히(1000): 표시용 200 캡을 그대로 쓰면 그 너머 콘텐츠가 배정에서 조용히 빠진다
      openBulk() { this.bulkChecked = {}; this.bulkPick = []; this.bulkMin = 1; this.bulkQ = ''; this.bulkMode = 'same'; this.bulkGrpN = 1; this.bulkPickG = []; this.bulkRandMsg = ''; this.bulkOpen = true; this.loadRaw(1000); this.loadAssignLog(); },
      // 배정 감사 이력: 누가·언제·어떤 방식으로 몇 건을 배정/해제했는지(모달 하단 표시)
      assignLog: null,
      async loadAssignLog() { try { const r = await (await this._afetch('/assign-log', { headers: this._authHeaders() })).json(); if (r && r.ok) this.assignLog = r.items; } catch (e) {} },
      assignLogTxt(it) {
        const who = (it.reviewers || []).map((id) => ((this.assignMembers.find((m) => m.id === id) || {}).name || String(id).slice(0, 6))).join('·');
        return this.fmtTs(it.ts) + ' · ' + it.by + ' · ' + it.mode + ' ' + it.n + '건' + (who ? (' → ' + who) : '');
      },
      // 필터 결과(현재 표와 동일 규칙 + 배정 상태 필터)
      get bulkFiltered() {
        return (((this.rawData || {}).items) || []).filter((r) => {
          if (this.bulkQ && !((r.title || '') + (r.category || []).join(' ') + (r.reasons || []).join(' ')).toLowerCase().includes(this.bulkQ.toLowerCase())) return false;
          if (this.bulkGrade && (r.grade || '') !== this.bulkGrade) return false;
          if (this.bulkSvc && (r.service || '') !== this.bulkSvc) return false;
          if (this.bulkRev === 'todo' && this.myVerdict(r.fb)) return false;
          if (this.bulkRev === 'done' && !this.myVerdict(r.fb)) return false;
          if (this.bulkAsg === 'unassigned' && (r.assignees || []).length) return false;
          if (this.bulkAsg === 'assigned' && !(r.assignees || []).length) return false;
          return true;
        });
      },
      get bulkSelHashes() { return this.bulkFiltered.filter((r) => this.bulkChecked[r.hash]).map((r) => r.hash); },
      get bulkAllOn() { const f = this.bulkFiltered; return f.length > 0 && f.every((r) => this.bulkChecked[r.hash]); },
      // 선택 항목 중 이미 배정된 건수(덮어쓰기 경고용)
      get bulkOverwrite() { return this.bulkFiltered.filter((r) => this.bulkChecked[r.hash] && (r.assignees || []).length).length; },
      // 그룹 수(입력 방어: 빈칸·NaN → 1 · 상한 8) · 2 이상이면 그룹 모드
      get bulkGrps() { return Math.max(1, Math.min(8, parseInt(this.bulkGrpN, 10) || 1)); },
      // 그룹별 선택 건수 [그룹1건수, 그룹2건수, …]
      get bulkGrpCounts() {
        const c = Array(this.bulkGrps).fill(0);
        this.bulkFiltered.forEach((r) => { const g = parseInt(this.bulkChecked[r.hash], 10) || 0; if (g >= 1 && g <= c.length) c[g - 1]++; });
        return c;
      },
      // 그룹 모드 실행 가능: 모든 그룹에 콘텐츠 1건·담당자 1명 이상
      get bulkGrpReady() {
        if (this.bulkGrps <= 1) return true;
        return this.bulkGrpCounts.every((n) => n > 0)
          && Array.from({ length: this.bulkGrps }, (_, i) => this.bulkPickAt(i + 1)).every((p) => p.length > 0);
      },
      bulkGrpHashes(g) { return this.bulkFiltered.filter((r) => parseInt(this.bulkChecked[r.hash], 10) === g).map((r) => r.hash); },
      bulkPickAt(g) { return this.bulkPickG[g - 1] || []; },
      bulkPickGToggle(g, id) {
        const arr = this.bulkPickAt(g).slice();
        const i = arr.indexOf(id);
        if (i >= 0) arr.splice(i, 1); else arr.push(id);
        const next = this.bulkPickG.slice(); next[g - 1] = arr; this.bulkPickG = next;   // 재할당(반응성)
      },
      // 그룹 수 변경: 기존 선택의 그룹 번호가 무의미해지므로 선택·담당자 초기화(단일↔그룹 전환 포함)
      bulkGrpChanged() { this.bulkChecked = {}; this.bulkPickG = []; if (this.bulkGrps > 1) this.bulkMode = 'same'; },
      bulkNames(ids) { return (ids || []).map((id) => ((this.assignMembers.find((m) => m.id === id) || {}).name || id)).join(', '); },
      // 체크 맵은 새 객체로 재할당(Alpine 반응성: 신규 키 추가도 안전하게 감지)
      bulkToggle(h) {
        if (this.bulkGrps > 1) {   // 그룹 모드: 클릭마다 미선택 → 그룹1 → 그룹2 → … → 해제 순환
          const cur = parseInt(this.bulkChecked[h], 10) || 0;
          const next = cur >= this.bulkGrps ? 0 : cur + 1;
          this.bulkChecked = Object.assign({}, this.bulkChecked, { [h]: next || false });
          return;
        }
        this.bulkChecked = Object.assign({}, this.bulkChecked, { [h]: !this.bulkChecked[h] });
      },
      bulkToggleAll() {
        const on = !this.bulkAllOn; const m = Object.assign({}, this.bulkChecked);
        let g = 0;   // 그룹 모드 전체 선택: 겹치지 않게 그룹을 순환하며 배정
        this.bulkFiltered.forEach((r) => { m[r.hash] = on ? (this.bulkGrps > 1 ? ((g++ % this.bulkGrps) + 1) : true) : false; });
        this.bulkChecked = m;
      },
      bulkRandom() {
        const pool = this.bulkFiltered.slice();
        for (let i = pool.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); const t = pool[i]; pool[i] = pool[j]; pool[j] = t; }
        const want = Math.max(0, parseInt(this.bulkRandN, 10) || 0);
        const n = Math.min(pool.length, want);
        const m = {};                                   // 재추출: 기존 선택 초기화
        let picked = 0;
        if (this.bulkGrps > 1) {                        // 그룹 모드: 그룹마다 N건씩 서로 겹치지 않게 추출
          let k = 0;
          for (let g = 1; g <= this.bulkGrps; g++) for (let i = 0; i < n && k < pool.length; i++) { m[pool[k++].hash] = g; picked++; }
        } else {
          for (let i = 0; i < n; i++) { m[pool[i].hash] = true; picked++; }
        }
        this.bulkChecked = m;
        // 풀 부족으로 요청보다 적게 뽑히면 명시(조용한 축소가 '100건씩 했는데 96건' 혼란을 만든다)
        const need = want * this.bulkGrps;
        this.bulkRandMsg = picked < need
          ? '⚠ 요청 ' + need + '건(' + want + '건 × ' + this.bulkGrps + (this.bulkGrps > 1 ? '그룹' : '') + ') 중 ' + picked + '건만 추출됨 · 필터 결과가 ' + pool.length + '건뿐입니다'
          : '';
      },
      bulkPickToggle(id) {
        const i = this.bulkPick.indexOf(id);
        if (i >= 0) this.bulkPick.splice(i, 1); else this.bulkPick.push(id);
        if (this.bulkMin > this.bulkPick.length) this.bulkMin = Math.max(1, this.bulkPick.length);
      },
      async saveBulk() {
        if (this.bulkGrps > 1) return this.saveBulkGroups();
        const hashes = this.bulkSelHashes;
        if (!hashes.length || !this.bulkPick.length) return;
        this.assignBulkBusy = true;
        try {
          // JSON.stringify 는 undefined 키를 버린다 → 기존(same) 모드 요청 본문은 이전과 동일 유지
          const body = JSON.stringify({ hashes, reviewers: this.bulkPick, min_reviewers: this.bulkMin,
            mode: this.bulkMode === 'distribute' ? 'distribute' : undefined });
          const r = await (await this._afetch('/content-assign-bulk', { method: 'POST', headers: this._authHeaders(), body })).json();
          if (r && r.ok) {
            this.bulkOpen = false; this.loadRaw();
            if (r.mode === 'distribute') this.liveToast('나눠 배정 완료 · ' + this.bulkPerTxt(r.per_reviewer));
          }
          else this._err((r && r.error) || '일괄 배정 실패');
        } catch (e) { this._err('일괄 배정 실패'); }
        this.assignBulkBusy = false;
      },
      // 그룹 모드 실행: 그룹별 (콘텐츠, 담당자)를 기존 엔드포인트로 순차 호출(덮어쓰기라 재시도 안전).
      // 중간 실패 시 몇 그룹까지 반영됐는지 알리고 모달은 열어 둔다 → 그대로 재실행하면 이어서 복구.
      async saveBulkGroups() {
        if (this.assignBulkBusy || !this.bulkGrpReady) return;
        this.assignBulkBusy = true;
        const G = this.bulkGrps;
        const groups = Array.from({ length: G }, (_, i) => ({ g: i + 1, hashes: this.bulkGrpHashes(i + 1), reviewers: this.bulkPickAt(i + 1) }));
        const done = [];
        try {
          for (const grp of groups) {
            const body = JSON.stringify({ hashes: grp.hashes, reviewers: grp.reviewers, min_reviewers: this.bulkMin });
            const r = await (await this._afetch('/content-assign-bulk', { method: 'POST', headers: this._authHeaders(), body })).json();
            if (!(r && r.ok)) {
              this._err('그룹' + grp.g + ' 배정 실패' + (done.length ? ' · 그룹' + done.join('·') + '은 반영됨' : '') + ((r && r.error) ? ' · ' + r.error : ''));
              this.assignBulkBusy = false; return;
            }
            done.push(grp.g);
          }
          this.bulkOpen = false; this.loadRaw();
          this.liveToast('그룹별 배정 완료 · ' + groups.map((grp) => '그룹' + grp.g + ' ' + grp.hashes.length + '건 → ' + this.bulkNames(grp.reviewers)).join(' · '));
        } catch (e) { this._err('일괄 배정 실패' + (done.length ? ' · 그룹' + done.join('·') + '은 반영됨' : '')); }
        this.assignBulkBusy = false;
      },
      // 분배 결과 요약: {reviewer_id: n} → "이름 n건 · 이름 n건"
}));
