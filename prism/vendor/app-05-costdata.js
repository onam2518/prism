/* Prism 앱 조각 05 · prismApp 프로퍼티 그룹(자동 분할 · 앱 분할 6차).
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
      costData: null,
      async loadCost() {
        try { const r = await (await this._afetch('/cost-rollup?days=30', { headers: this._authHeaders() })).json(); if (r && r.ok) this.costData = r; } catch (e) {}
      },
      get costMax() { return Math.max(0.000001, ...(((this.costData || {}).by_day) || []).map((d) => d.cost)); },
      usdTxt(v) { return v == null ? '·' : ('$' + (Math.round(v * 10000) / 10000)); },
      // 실패 트리아지(종류×모델×서비스 · 관리자): 실행 시점 누적 원장(reports fail_rollup)
      failData: null,
      async loadFails() {
        try { const r = await (await this._afetch('/fail-rollup?days=30', { headers: this._authHeaders() })).json(); if (r && r.ok) this.failData = r; } catch (e) {}
      },
      failKindKr(k) { return ({ parse_empty: '빈 응답(파싱 실패)', api: 'API 오류', network: '연결 실패', auth: '인증 오류', content_filter: '콘텐츠 필터', rate: '요청 제한', unknown: '기타' })[k] || k; },
      // 학습 지시 원본 관리(개별 끄기 · 관리자): 끈 지시는 다음 학습 반영부터 제외
      routesOpen: false, routesRaw: null,
      async loadRoutesRaw() { try { const r = await (await this._afetch('/routes-raw', { headers: this._authHeaders() })).json(); if (r && r.ok) this.routesRaw = r; } catch (e) {} },
      async toggleRoute(r) {
        try {
          const res = await (await this._afetch('/route-disable', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ text: r.text, disabled: !r.disabled }) })).json();
          if (res && res.ok) { r.disabled = !r.disabled; this.liveToast(r.disabled ? '지시를 껐어요 · 다음 학습 반영부터 제외' : '지시를 다시 켰어요'); }
          else this._err((res && res.error) || '변경 실패');
        } catch (e) { this._err('변경 실패'); }
      },
      // 골든 생성 현황(팀원 공개)
      goldenStatus: null,
      async loadGoldenStatus() { try { const r = await (await this._afetch('/golden-status', { headers: this._authHeaders() })).json(); if (r && r.ok) { this.goldenStatus = r; this.loadVerHist(); } } catch (e) {} },
      // 관리자 골든 브라우저
      goldenList: null,
      async loadGoldenList() { try { const r = await (await this._afetch('/golden-list', { headers: this._authHeaders() })).json(); if (r && r.ok) this.goldenList = r; } catch (e) {} },
      async removeGolden(h) {
        if (!(await this.dsConfirm('이 골든 항목을 제거할까요? (평가 정답셋에서 빠집니다)', { ok: '제거', danger: true }))) return;
        try { await this._afetch('/golden-remove', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ hash: h }) }); } catch (e) {}
        this.loadGoldenList(); this.loadGoldenStatus(); this.loadLearnData();
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
      async loadVerHist() {                                      // 표 행 = v2..현재(반영 회차+1) · 각 버전 지표를 병렬 로드
        const seq = (this.goldenStatus && this.goldenStatus.batch_seq != null) ? this.goldenStatus.batch_seq : 0;
        const cur = seq + 1;
        const vers = []; for (let v = cur; v >= 2; v--) vers.push(v);
        if (!vers.length) { this.verRows = []; this.verSel = null; this.verSnap = null; return; }
        const rows = await Promise.all(vers.map(async v => {
          let rep = null;
          try { const r = await (await this._afetch('/learn-report?v=' + v, { headers: this._authHeaders() })).json(); rep = (r && r.ok) ? r.report : null; } catch (e) {}
          const g = rep && rep.golden;
          return { v: v, cur: v === cur, report: rep, ts: rep ? rep.ts : null,
                   acc: rep ? rep.grade_accuracy : null, delta: rep ? rep.improve_delta : null,
                   confirmed: g ? g.confirmed : null };
        }));
        this.verRows = rows;
        if (this.verSel == null || !rows.some(r => r.v === this.verSel)) this.selectVer(cur);
      },
      async selectVer(v) {                                       // 행 토글 + 그 버전 지시(스냅샷) 로드
        if (this.verSel === v) { this.verSel = null; return; }  // 다시 누르면 접기
        this.verSel = v; this.verBusy = true; this.verSnap = null;
        try { const r = await (await this._afetch('/prompt-snapshot?v=' + v, { headers: this._authHeaders() })).json(); this.verSnap = (r && r.ok) ? r.snapshot : null; }
        catch (e) { this.verSnap = null; }
        this.verBusy = false;
      },
      verRow(v) { return this.verRows.find(r => r.v === v) || null; },
      verDir(stage) { const s = this.verSnap; return (s && s.learned && s.learned[stage]) || ''; },
      verAmb(stage) { const r = this.verRow(this.verSel), res = (r && r.report && r.report.improve && r.report.improve.results) || {}; return (res[stage] && res[stage].ambiguities) || []; },
      verHasDir() { return ['extract', 'analyze', 'review', 'judge'].some(s => this.verDir(s)); },
      async openDirModal(v) {                                    // 그 버전 지시를 모델에 적용하는 팝업
        this.dmOpen = true; this.dmVer = v; this.dmModel = 'common'; this.dmMsg = ''; this.dmSnap = null; this.dmStages = {}; this.dmBusy = true;
        try { const r = await (await this._afetch('/prompt-snapshot?v=' + v, { headers: this._authHeaders() })).json(); this.dmSnap = (r && r.ok) ? r.snapshot : null; } catch (e) {}
        const l = (this.dmSnap && this.dmSnap.learned) || {};
        ['extract', 'analyze', 'review', 'judge'].forEach(s => { if ((l[s] || '').trim()) this.dmStages[s] = true; });
        this.dmBusy = false;
      },
      dmDir(stage) { const l = (this.dmSnap && this.dmSnap.learned) || {}; return (l[stage] || '').trim(); },
      dmStageList() { return ['extract', 'analyze', 'review', 'judge'].filter(s => this.dmDir(s)); },
      async applyDir() {                                         // 선택 모델(공통/특정)에 지시 적용
        const stages = Object.keys(this.dmStages).filter(s => this.dmStages[s]);
        if (!stages.length) { this.dmMsg = '적용할 단계를 선택하세요'; return; }
        this.dmBusy = true; this.dmMsg = '';
        try {
          const r = await (await this._afetch('/apply-directive', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ version: this.dmVer, model: this.dmModel, stages: stages }) })).json();
          if (r && r.ok) { this.dmMsg = '✓ v' + this.dmVer + ' 지시 ' + (r.applied || []).length + '개 · ' + (this.dmModel === 'common' ? '공통(전 모델)' : this.dmModel + ' 전용') + '에 적용됨'; setTimeout(() => { this.dmOpen = false; }, 1200); }
          else this.dmMsg = (r && r.error) || '적용 실패';
        } catch (e) { this.dmMsg = '적용 실패'; }
        this.dmBusy = false;
      },
      nextBatchAt: 0,
      // 학습 반영 주기(모델 버전 시한 · 관리자): N일마다 지정 시각에 반영 · 지금 실행 시 주기 재시작
      learnNextAt: '', learnSchedMsg: '', schedEditing: false, learnRepeat: 0,
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
          const r = await (await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ learn_next_at: this.learnNextAt || '', golden_min_good: parseInt(this.goldenMinGood, 10) || 1, learn_repeat_days: parseInt(this.learnRepeat, 10) || 0 }) })).json();
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
      async deleteQuest() {                              // 퀘스트 삭제 = 목표 해제 · 검수 의견·점수는 불변
        if (!(await this.dsConfirm('진행 중인 퀘스트를 삭제할까요? 반영 예약이 해제되고 팀 홈의 D-day 카드가 사라집니다 · 검수 의견은 그대로 남습니다', { ok: '삭제', danger: true }))) return;
        try {
          await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ learn_next_at: '' }) });
          this.learnNextAt = ''; this.nextBatchAt = 0; this.schedEditing = false; this.learnRepeat = 0;
          this.learnSchedMsg = '퀘스트를 삭제했습니다';
          this.loadLearnReport();
          this.loadArena();
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
      maybeQuestReminder() {                           // 마감 임박(D-1 이하) 1일 1회 리마인드 · 배정 있으면 내 몫 기준
        const a = this.arenaData;
        const left = this.questLeftMine();
        if (!(a && a.next_batch_at && left)) return;
        const dd = this.ddayTxt(a.next_batch_at);
        if (dd !== 'D-DAY' && dd !== 'D-1') return;
        const mark = new Date().toDateString() + ':' + a.next_batch_at;
        try { if (localStorage.getItem('prismQuestRemind') === mark) return; localStorage.setItem('prismQuestRemind', mark); } catch (e) {}
        this.liveToast('⏰ 팀 퀘스트 마감 임박 ' + dd + ' · 남은 ' + (this.questMyTotal() ? '내 배정 ' : '') + left + '건, 완주까지 화이팅!');
      },
      deltaTxt(d) { const v = (d || 0) * 100; return (v >= 0 ? '+' : '') + v.toFixed(1) + '%p'; },
      dirBullets(text) {                                 // 학습 보정 지시문 → 본문 불릿(줄 단위 우선, 없으면 문장 단위)
        if (!text) return [];
        const t = String(text).trim();
        const src = t.indexOf('\n') >= 0 ? t : t.replace(/([다라마요]\.)\s+/g, '$1\n');   // 문장 종결 뒤 개행
        return src.split('\n').map(s => s.replace(/^[\s\-–—·•*]+/, '').trim()).filter(Boolean);
      },
      questTotal() { return (this.arenaData && this.arenaData.total_targets) || 0; },
      // 진행 = 팀 평균 검수 건수(quest_avg_done) > 커버리지(quest_done) > 구 산식 순 폴백
      questDone() { const a = this.arenaData; if (a && a.quest_avg_done != null) return Math.min(a.quest_avg_done, this.questTotal()); if (a && a.quest_done != null) return Math.min(a.quest_done, this.questTotal()); const t = this.questTotal(); return Math.max(0, t - ((a && a.queue) || 0)); },
      questAvgLabel() { const a = this.arenaData; return a && a.quest_avg_done != null ? '팀 평균 ' : ''; },
      // 개인별 진척도(완료/배정)의 팀 평균(%) — 총 대상보다 배정이 적어도 왜곡 없음 · 구서버는 null
      questTeamPct() { const a = this.arenaData; return a && a.quest_team_progress != null ? Math.round(a.quest_team_progress * 100) : null; },
      // 내 배정 기준 개인화: 목표·남은 건수는 총 대상(예: 200)이 아니라 '내 몫'으로 표시
      questMyTotal() { return this.arenaAssignedTotal || 0; },
      questMyDone() { return Math.min(this.arenaAssignedDone || 0, this.questMyTotal()); },
      questMyLeft() { return Math.max(0, this.questMyTotal() - this.questMyDone()); },
      questLeftMine() { return this.questMyTotal() ? this.questMyLeft() : this.questLeft(); },
      // 완주(커버리지) 건수 — 남은 건수·목표 문구의 원천(게이지 %와 분리)
      questCover() { const a = this.arenaData; if (a && a.quest_done != null) return Math.min(a.quest_done, this.questTotal()); return this.questDone(); },
      questLeft() { return Math.max(0, this.questTotal() - this.questCover()); },
      questPct() { const p = this.questTeamPct(); if (p != null) return p; const t = this.questTotal(); return t ? Math.round(this.questDone() / t * 100) : 0; },
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
      // ingestOnceBusy: 일회성 크롤러 가져오기 전용 · 소스별 맵(ingestBusy: {})과 키 충돌 금지(중복 선언 시 버튼 영구 비활성)
      ingestEndpoint: '', ingestN: 20, ingestMsg: '', ingestOnceBusy: false,
      async ingestRun() {
        if (!(this.ingestEndpoint || '').trim()) { this.ingestMsg = '크롤러 엔드포인트를 입력하세요'; return; }
        this.ingestOnceBusy = true; this.ingestMsg = '인입·추출 중…';
        try { const r = await (await this._afetch('/admin', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ action: 'ingest', endpoint: this.ingestEndpoint, n: this.ingestN }) })).json();
          this.ingestMsg = r.ok ? ('✓ ' + r.fetched + '건 인입 → 검수 대기 ' + r.queued + '건 적재') : (r.error || '실패'); } catch (e) { this.ingestMsg = '오류'; }
        this.ingestOnceBusy = false;
      },
      // 결과 출처 필터(자동 인입/단건/배치)
      get srcOptions() { const s = new Set(((this.dashData && this.dashData.contents) || []).map((c) => c.source || '단건')); return [...s]; },
      get filteredContents() { const cs = (this.dashData && this.dashData.contents) || []; return this.srcFilter ? cs.filter((c) => (c.source || '단건') === this.srcFilter) : cs; },
      // 아레나 파생값(게이지·내 순위)
      get arenaPct() { const d = this.arenaData; return d ? Math.round((d.accuracy || 0) * 100) : 0; },
      get arenaTargetPct() { const d = this.arenaData; return d ? Math.round((d.target || 0.9) * 100) : 90; },
      // 검수 진척율: 개인(내가 검수한 대상 비율) · 팀(팀원 평균)
      get myProgressPct() { const m = this.arenaMe; return m ? Math.round((m.progress || 0) * 100) : 0; },
      get teamProgressPct() { const d = this.arenaData; return d ? Math.round((d.team_progress || 0) * 100) : 0; },
      get reviewTargets() { const d = this.arenaData; return d ? (d.total_targets || 0) : 0; },
      // 히어로 캡션: 진척율(%)과 같은 모집단으로 표기(누적 reviews 금지 — '대상 4건 중 45건' 모순 방지)
      get arenaAssignedTotal() { const m = this.arenaMe; return (m && m.assigned_total) || 0; },
      get arenaAssignedDone() { const m = this.arenaMe; return (m && m.assigned_done) || 0; },
      get arenaTargetReviews() { const m = this.arenaMe; if (!m) return 0; const v = m.target_reviews != null ? m.target_reviews : m.reviews; return Math.min(v || 0, this.reviewTargets); },
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
      // 내 행 매칭 = reviewer_id(서버 my_id) 우선: 이름 매칭은 닉네임 변경 직후 기기 간 캐시로 어긋난다
      get arenaMe() { const d = this.arenaData; if (!d || !this.reviewer) return null; const rows = d.leaderboard || []; return (d.my_id && rows.find((r) => r.reviewer_id === d.my_id)) || rows.find((r) => r.reviewer === this.reviewer) || null; },
      get arenaMyRank() { const d = this.arenaData; const m = this.arenaMe; if (!d || !m) return 0; const i = (d.leaderboard || []).indexOf(m); return i < 0 ? 0 : i + 1; },
      rankMedal(i) { return ['🥇', '🥈', '🥉'][i] || ('#' + (i + 1)); },
      // 주간 리그(D-9): 이번 주 점수 순위 + 승급/강등 존 + 지난주 대비 이동
}));
