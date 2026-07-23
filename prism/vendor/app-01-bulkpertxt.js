/* Prism 앱 조각 01 · prismApp 프로퍼티 그룹(자동 분할 · 앱 분할 6차).
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
      bulkPerTxt(per) {
        return Object.entries(per || {}).map(([id, n]) => ((this.assignMembers.find((m) => m.id === id) || {}).name || id) + ' ' + n + '건').join(' · ');
      },
      // 검수 '완료' 판정은 팀 합의(fb.verdict)가 아니라 '내 표(fb.mine)' 기준이어야 한다.
      // (그러지 않으면 타 검수자가 검수한 콘텐츠도 내 목록에서 완료로 보인다 · 2026-07-10)
      // mine 은 서버가 검수자 식별 시에만 채운다 → 미제공(undefined)일 때만 합의로 폴백(골드 문항 등).
      myVerdict(fb) { return (fb && (fb.mine !== undefined ? (fb.mine || '') : (fb.verdict || ''))) || ''; },
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
        const upd = (r) => { if (r && r.hash === hash) { r.fb = Object.assign({}, r.fb, fb); r._doneLocal = !!this.myVerdict(fb); if (fb && fb.good !== undefined) r.split = !!(fb.good && fb.bad); } };
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
      _rawToDetail(r) { return { hash: r.hash, title: r.title, service: r.service, body: r.body || '', url: r.url || '', images: r.images || [], summary: r.summary || '', entities: r.entities || [], entities_scored: r.entities_scored || [], intent: r.intent || [], category: r.category || [], grade: r.grade || '', reasons: r.reasons || [], model: r.model || '', final: r.final || '', assignees: r.assignees || [], source_status: r.source_status || {}, fb: Object.assign({}, r.fb) }; },
      // 배정 배타 검수(UI): 지정 검수자가 있는데 내가 아니면 판정 버튼 비활성(서버 게이트는 백스톱).
      detailAssignBlocked() {
        if (this.opsAdmin) return false;                 // 생성자·슈퍼관리자: 배정 무관 검수 가능
        const d = this.detail;
        if (!d || !d.assignees || !d.assignees.length) return false;
        const me = (this.arenaData && this.arenaData.my_id) || this.reviewer || '';
        return d.assignees.indexOf(me) < 0;
      },
      openRawDetail(r) {                                 // 목록 컨텍스트 보존 -> 상세에서 이전/다음·자동 이동
        const list = this.rawFiltered.slice();
        this.openDetail(this._rawToDetail(r));
        this.detailNav = { list: list, idx: Math.max(0, list.findIndex((x) => x.hash === r.hash)) };
      },
      detailNav: null,
      // ── 2층 검수: 최종검수자 역할 + 최종검수 큐(미확정분 편입/제외 결정) ──
      finalQueue: null, finalBusy: false,
      get finalReviewers() { return (this.arenaData && this.arenaData.final_reviewers) || []; },
      get isFinalReviewer() { return !!(this.arenaData && this.arenaData.my_id && this.finalReviewers.includes(this.arenaData.my_id)); },
      async loadFinalQueue() {
        this.finalBusy = true;
        try { const r = await (await this._afetch('/final-queue', { headers: this._authHeaders() })).json(); if (r && r.ok) this.finalQueue = r; } catch (e) {}
        this.finalBusy = false;
      },
      async finalDecide(r, v) {                    // 편입(good)/제외(bad)/철회('') · 기존 /final-verdict 재사용
        try {
          const res = await (await this._afetch('/final-verdict', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ hash: r.hash, verdict: v, reviewer: this.reviewer }) })).json();
          if (res && res.ok && res.gold) {         // 골드 캘리브레이션 문항: 정오 알림 후 목록에서 제거(원장 무오염)
            this.liveToast(res.gold.correct ? '골드 문항 정답 · 판정 정확도에 반영됐어요' : ('골드 문항 오답 · 정답은 ' + (res.gold.expected === 'good' ? '편입' : '제외') + '이었어요'));
            this.finalQueue.items = ((this.finalQueue || {}).items || []).filter((x) => x.hash !== r.hash);
            (res.missions_completed || []).forEach((m) => this.celebratePoints(m.bonus, '미션 달성 · ' + m.label));
            if (this.finalCtx && this.finalCtx.hash === r.hash) { this.detailOpen = false; this.finalCtx = null; }
            return;
          }
          if (res && res.ok) {
            r.final = v || '';
            r.final_by = v ? (this.reviewer || '') : '';
            r.final_ts = v ? (Date.now() / 1000) : 0;
            this.liveToast(v === 'good' ? '편입 확정 · 다음 학습 반영 때 정답셋으로 승격됩니다' : (v === 'bad' ? '제외 확정 · 정답셋으로 승격되지 않습니다' : '최종판정을 철회했어요'));
            (res.missions_completed || []).forEach((m) => this.celebratePoints(m.bonus, '미션 달성 · ' + m.label));
          } else this._err((res && res.error) || '저장 실패');
        } catch (e) { this._err('저장 실패'); }
      },
      // 최종검수 컨텍스트: 최종 검수 탭에서 상세로 들어오면 '검수 판정' 자리가 '최종검수 결정'으로 바뀐다
      finalCtx: null,
      get finalMode() { return !!(this.finalCtx && this.detail && this.finalCtx.hash === this.detail.hash); },
      openFinalDetail(r) {                          // 목록 = 결정 현황 · 결정은 상세 안에서
        const list = ((this.finalQueue || {}).items || []).slice();
        this.openDetail(this._rawToDetail(r));
        this.finalCtx = r;
        this.detailNav = { list: list, idx: Math.max(0, list.findIndex((x) => x.hash === r.hash)) };
      },
      // 정답 확정(고쳐서 편입): 요약·분류·의도·등급을 직접 고친 뒤 그 상태로 편입 · 수정본이 곧 골든 정답
      faOpen: false, faBusy: false, fa: null,
      openFinalAnswer(r) {
        this.fa = { hash: r.hash, title: r.title || '(제목 없음)', row: r,
          summary: r.summary || '', cats: (r.category || []).join(', '),
          intent: (r.intent || []).join(', '), grade: r.grade === 'R' ? 'R' : 'G',
          note: (r.fb && r.fb.note) || '', elems: (r.fb && r.fb.elems) || [] };
        this.faOpen = true;
      },
      async toggleOpsHold(r) {                  // 운영자 수동 노출제한 토글(라벨·학습과 분리)
        if (!r || !r.hash) return;
        const on = !r.ops_hold;
        try {
          const res = await (await this._afetch('/ops-hold', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ hash: r.hash, on: on }) })).json();
          if (res && res.ok) { r.ops_hold = on; if (this.detail && this.detail.hash === r.hash) this.detail.ops_hold = on; }
          else this._err((res && res.error) || '노출제한 저장 실패');
        } catch (e) { this._err('노출제한 저장 실패'); }
      },
      srcGone(r) { return !!(r && r.source_status && r.source_status.state === 'gone'); },
      async toggleSourceGone(r) {               // 원문 소실 신고 토글(게시판 #10 · 라벨·학습과 분리)
        if (!r || !r.hash) return;
        const on = !this.srcGone(r);
        try {
          const res = await (await this._afetch('/source-status', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ hash: r.hash, on: on, reviewer: this.reviewer || '' }) })).json();
          if (res && res.ok) {
            const ss = { state: on ? 'gone' : '', by: this.reviewer || '', ts: Date.now() / 1000 };
            r.source_status = ss;
            if (this.detail && this.detail.hash === r.hash) this.detail.source_status = ss;
            [(this.rawData || {}).items, (this.finalQueue || {}).items, (this.dashData || {}).contents].forEach((list) => {
              const t = (list || []).find((x) => x.hash === r.hash); if (t) t.source_status = ss;   // 목록 배지 즉시 반영
            });
            this.liveToast(on ? '원문 소실로 표시했어요 · 목록에 배지가 붙습니다' : '원문 소실 표시를 해제했어요');
          } else this._err((res && res.error) || '원문 소실 표시 저장 실패');
        } catch (e) { this._err('원문 소실 표시 저장 실패'); }
      },
      // 온디맨드 원문 상태 확인(게시판 #10): 서버가 판정(gone·temp·unknown·ok)만 반환 · 확정은 위 토글 버튼
      srcCheckBusy: false, srcCheckMsg: '',
      async checkSource(r) {
        if (!(r && r.url) || this.srcCheckBusy) return;
        this.srcCheckBusy = true; this.srcCheckMsg = '원문 상태 확인 중…';
        try {
          const res = await (await this._afetch('/check-source', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ url: r.url }) })).json();
          this.srcCheckMsg = (res && res.ok) ? this._srcStateMsg(res) : ((res && res.error) || '확인 실패 · 잠시 후 다시 시도하세요');
        } catch (e) { this.srcCheckMsg = '확인 실패 · 잠시 후 다시 시도하세요'; }
        this.srcCheckBusy = false;
      },
      _srcStateMsg(res) {
        const c = res.code ? (' · HTTP ' + res.code) : '';
        if (res.state === 'gone') return '원문 소실로 보입니다' + c + (res.sign ? (' · 안내문 "' + res.sign + '" 감지') : '') + ' · 맞으면 아래 버튼으로 표시하세요';
        if (res.state === 'temp') return '일시 오류로 보입니다' + c + ' · 잠시 후 다시 확인하세요';
        if (res.state === 'ok') return '원문 페이지가 응답합니다' + c + ' · 내용 일치는 직접 대조하세요';
        return '자동 판정이 어렵습니다' + c + ' · 원문 바로가기로 직접 확인하세요';
      },
      async saveFinalAnswer() {
        const f = this.fa; if (!f || this.faBusy) return;
        this.faBusy = true;
        try {
          if (!(f.hash || '').startsWith('goldf:')) {   // 골드 문항은 편집 대상이 아니라 판정만 기록됨
            const r0 = f.row; const patch = {};
            const cats = f.cats.split(',').map((s) => s.trim()).filter(Boolean);
            const intent = f.intent.split(',').map((s) => s.trim()).filter(Boolean);
            if (f.summary !== (r0.summary || '')) patch.summary = f.summary;
            if (cats.join('|') !== (r0.category || []).join('|')) patch.content_category = cats;
            if (intent.join('|') !== (r0.intent || []).join('|')) patch.intent = intent;
            if (f.grade !== (r0.grade || '')) patch.finalGrade = f.grade;
            if (Object.keys(patch).length) {
              let pr = null;
              try { pr = await (await this._afetch('/patch-meta', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ hash: f.hash, patch: patch, reviewer: this.reviewer || '' }) })).json(); } catch (e) {}
              if (!(pr && pr.ok)) { this._err((pr && pr.error) || '교정 저장 실패 · 편입은 진행되지 않았어요'); return; }
              r0.summary = f.summary; r0.category = cats; r0.intent = intent; r0.grade = f.grade;   // 목록 즉시 반영
              if (this.detail && this.detail.hash === f.hash) {                                     // 열려 있는 상세도 동기화
                this.detail.summary = f.summary; this.detail.category = cats; this.detail.intent = intent; this.detail.grade = f.grade;
              }
              (pr.missions_completed || []).forEach((m) => this.celebratePoints(m.bonus, '미션 달성 · ' + m.label));
            }
          }
          await this.finalDecide(f.row, 'good');
          this.faOpen = false;
        } finally { this.faBusy = false; }
      },
      async toggleFinalRole(m) {                    // 팀 관리: 최종검수자 지정/해제(슈퍼관리자 이상)
        const nv = this.finalReviewers.includes(m.id) ? '' : 'final';
        try {
          const r = await (await this._afetch('/reviewer-role', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ id: m.id, role: nv }) })).json();
          if (r && r.ok) { if (this.arenaData) this.arenaData.final_reviewers = r.final_reviewers; this.liveToast(nv ? (m.name + ' · 최종검수자로 지정') : (m.name + ' · 최종검수자 해제')); }
          else this._err((r && r.error) || '변경 실패');
        } catch (e) { this._err('변경 실패'); }
      },
      // 목록 키보드 포커스(J/K 이동 대상 행) · 필터가 바뀌면 handler 쪽 클램프로 정합 유지
      rawFocusIdx: -1,
      _rawFocusScroll() {
        try { const el = document.querySelector('[data-rawrow="' + this.rawFocusIdx + '"]'); if (el) el.scrollIntoView({ block: 'nearest' }); } catch (e) {}
      },
      // 리드 최종판정(타이브레이크 · 슈퍼관리자 이상): 의견 갈림을 확정하고 골든 승격에 우선 반영
      async setFinal(c, v) {
        try {
          const r = await (await this._afetch('/final-verdict', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ hash: c.hash, verdict: v, reviewer: this.reviewer }) })).json();
          if (r && r.ok) {
            c.final = v || '';
            this.loadRaw();   // 리드 최종판정 후 전체 재조회(final 은 fb 밖 필드라 _syncFbByHash 로는 동기화 불가)
            this.liveToast(v ? ('리드 최종판정 · ' + (v === 'good' ? '정확' : '수정 필요') + ' 확정') : '최종판정을 철회했어요');
            (r.missions_completed || []).forEach((m) => this.celebratePoints(m.bonus, '미션 달성 · ' + m.label));
          } else this._err((r && r.error) || '저장 실패');
        } catch (e) { this._err('저장 실패'); }
      },
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
          const d = await (await this._afetch('/reviewer', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ mode: 'rename', reviewer: this.reviewer, name: nv, char: this.reviewerChar }) })).json();
          if (!d.ok) { this.nickMsg = '오류: ' + (d.error || '변경 실패'); }
          else {
            this.reviewer = nv; try { localStorage.setItem('prism_reviewer', nv); } catch (e) {}
            this.nickEdit = false; this.nickMsg = '';
            this.loadArena();                        // 리더보드·캐릭터 카드에 새 이름 즉시 반영
          }
        } catch (e) { this.nickMsg = '오류: ' + e; }
        this.nickBusy = false;
      },
      // 프로필 동기화(부팅): 다른 기기·탭에서 닉네임을 바꾼 경우 localStorage 의 옛 이름을 서버 기준으로 교체
      async syncProfile() {
        if (!this.authToken) return;                   // 토큰 없음(로컬 sqlite) = 이름이 곧 키라 동기화 불필요
        try {
          const p = await (await fetch('/reviewer', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ mode: 'login' }) })).json();
          if (p && p.ok && p.name && (p.name !== this.reviewer || (p.char || '') !== this.reviewerChar)) {
            this.reviewer = p.name; this.reviewerChar = p.char || this.reviewerChar;
            try { localStorage.setItem('prism_reviewer', this.reviewer); localStorage.setItem('prism_reviewer_char', this.reviewerChar); } catch (e) {}
          }
        } catch (e) {}
      },
      // 게시판(기능개선·오류 제보 · 팀 스코프)
      boardData: null, boardForm: { kind: 'bug', title: '', body: '' }, boardBusy: false, boardMsg: '',
      get boardAdmin() { return this.backend !== 'supabase' || !!(this.adminData && (this.adminData.isAdmin || this.adminData.isSuperAdmin || this.adminData.isSysAdmin)); },
      async loadBoard() {
        this.modBusy = true;
        try { this.boardData = await (await fetch('/board', { headers: this.authToken ? { 'Authorization': 'Bearer ' + this.authToken } : {} })).json(); } catch (e) {}
        this.modBusy = false;
        if (this.backend === 'supabase' && !this.adminData) this.loadAdmin();   // 상태 변경 권한 판정용
      },
      async boardSubmit() {
        if (!(this.boardForm.title || '').trim()) { this.boardMsg = '제목을 입력하세요'; return; }
        this.boardBusy = true; this.boardMsg = '';
        try {
          const d = await (await fetch('/board', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ action: 'create', reviewer: this.reviewer, kind: this.boardForm.kind, title: this.boardForm.title, body: this.boardForm.body }) })).json();
          if (d.error) { this.boardMsg = '오류: ' + d.error; }
          else { this.boardData = d; this.boardForm = { kind: this.boardForm.kind, title: '', body: '' }; this.boardMsg = '등록됨 · 팀에 공유되었습니다'; }
        } catch (e) { this.boardMsg = '오류: ' + e; }
        this.boardBusy = false;
      },
      async boardStatus(b, status) {
        try {
          const d = await (await fetch('/board', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ action: 'status', id: b.id, status, reviewer: this.reviewer }) })).json();
          if (d.error) { this.boardMsg = '오류: ' + d.error; } else this.boardData = d;
        } catch (e) {}
      },
      baOpen: false, baItem: null, baText: '', baEdit: false, baBusy: false, baMsg: '',
      openBoardAns(b, edit) {                   // 문의 답변 팝업 열기(보기/등록/수정)
        this.baItem = b; this.baText = b.answer || ''; this.baEdit = !!edit; this.baMsg = ''; this.baOpen = true;
      },
      async saveBoardAns() {                    // 팝업에서 답변 저장(관리자)
        if (!this.baItem || this.baBusy) return;
        this.baBusy = true; this.baMsg = '';
        try {
          const d = await (await fetch('/board', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ action: 'answer', id: this.baItem.id, answer: this.baText || '', reviewer: this.reviewer }) })).json();
          if (d.error) { this.baMsg = '오류: ' + d.error; }
          else {
            this.boardData = d;
            const it = (d.items || []).find((x) => x.id === this.baItem.id);
            if (it) this.baItem = it;
            this.baEdit = false; this.baMsg = '답변이 저장·공유되었습니다';
          }
        } catch (e) { this.baMsg = '답변 저장 실패'; }
        this.baBusy = false;
      },
      async boardDelete(b) {
        if (!(await this.dsConfirm('이 글을 삭제할까요?', { ok: '삭제', danger: true }))) return;
        try {
          const d = await (await fetch('/board', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ action: 'delete', id: b.id, reviewer: this.reviewer }) })).json();
          if (d.error) { this.boardMsg = '오류: ' + d.error; } else this.boardData = d;
        } catch (e) {}
      },
      detailGo(step) {                                   // 상세에서 목록 순서로 이전/다음 이동
        if (!this.detailNav) return;
        const i = this.detailNav.idx + step;
        if (i < 0 || i >= this.detailNav.list.length) return;
        const nav = this.detailNav;
        const wasFinal = !!this.finalCtx;                // 최종검수 흐름이면 다음 항목도 결정 바 유지
        this.openDetail(this._rawToDetail(nav.list[i]));
        if (wasFinal) this.finalCtx = nav.list[i];
        nav.idx = i;
        this.detailNav = nav;
      },
      detailNextTodo() {                                 // 다음 미검수 항목으로 · 없으면 완료 안내 후 닫기
        if (!this.detailNav) return;
        const nav = this.detailNav;
        for (let i = nav.idx + 1; i < nav.list.length; i++) {
          const r = nav.list[i];
          if (!this.myVerdict(r.fb) && !r._doneLocal) {
            this.openDetail(this._rawToDetail(r));
            nav.idx = i;
            this.detailNav = nav;
            return;
          }
        }
        this.liveToast('🎉 목록의 검수를 모두 마쳤어요!');
        this.detailOpen = false;
      },
}));
