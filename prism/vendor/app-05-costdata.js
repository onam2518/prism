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
      // 402 는 두 가지이고 대응이 다르다: billing=크레딧 충전 · quota=프로젝트 한도 상향.
      // http_402 는 2026-07-29 이전 적재분의 옛 키(구분 없이 뭉쳐 있어 '잔액/한도'로 표기).
      failKindKr(k) { return ({ parse_empty: '빈 응답(파싱 실패)', api: 'API 오류', network: '연결 끊김', timeout: '응답 시간 초과', bad_response: '응답 형식 오류', auth: '인증 오류', billing: '크레딧 부족', quota: '프로젝트 지출 한도', http_402: '잔액/한도(구분 전)', forbidden: '권한 거부', not_found: '경로 없음', too_long: '입력 초과', bad_request: '잘못된 요청', content_filter: '콘텐츠 필터', rate: '요청 제한', unknown: '기타' })[k] || k; },
      // 402 계열은 조치(충전·한도 상향) 전에는 재실행해도 같은 오류가 난다 — 그 사실을 알린다
      _failHas(f, keys) { return ((f || {}).kinds || []).some((k) => keys.includes(k)); },
      get failBillingN() { return this.failAll.filter((f) => this._failHas(f, ['billing', 'http_402'])).length; },
      get failQuotaN() { return this.failAll.filter((f) => this._failHas(f, ['quota'])).length; },
      // 실패 배지 툴팁: 예외 원문(details · 콜별 1줄)을 그대로 보여 준다. 원문이 없는
      // 과거 기록은 원인 키만 뜬다(2026-07-28 이전 적재분에는 details 가 없다).
      failTip(f, k) {
        const d = ((f || {}).details || []).join('\n');
        return d ? (k + '\n' + d) : k;
      },
      // 실패 콘텐츠 개별 재실행: STEP 2 사용 모델로 이 건만 재생성 · 무실패 성공이면 서버가 recent 에서 제거
      async rerunFail(f) {
        await this.rerunOne({ hash: f.hash, title: f.title });
        this.loadFails();
      },
      // ── 실패 목록 필터 + 다중 선택 재실행 ('추가된 콘텐츠' 표와 같은 규약) ──
      // 실패가 한 종류로 몰릴 때(예: 잔액 부족 402 전건) 원인별로 갈라 보고 한 번에 다시 돌린다.
      failKind: '', failModel: '',
      get failAll() { return ((this.failData || {}).recent) || []; },
      get failKindOpts() {
        const s = new Set();
        this.failAll.forEach((f) => (f.kinds || []).forEach((k) => s.add(k)));
        return [...s].sort();
      },
      get failModelOpts() {
        const s = new Set();
        this.failAll.forEach((f) => { if (f.model) s.add(f.model); });
        return [...s].sort();
      },
      get failRows() {
        return this.failAll.filter((f) => {
          if (this.failKind && !(f.kinds || []).includes(this.failKind)) return false;
          if (this.failModel && (f.model || '') !== this.failModel) return false;
          return true;
        });
      },
      failSel: {}, failBusy: false,
      get failPicked() { return Object.keys(this.failSel).filter((h) => this.failSel[h]); },
      get failAllOn() {
        const rs = this.failRows;
        return rs.length > 0 && rs.every((f) => this.failSel[f.hash]);
      },
      toggleFailPick(h) { this.failSel = Object.assign({}, this.failSel, { [h]: !this.failSel[h] }); },
      toggleFailPickAll() {
        const on = !this.failAllOn, next = Object.assign({}, this.failSel);
        this.failRows.forEach((f) => { next[f.hash] = on; });
        this.failSel = next;
      },
      clearFailPick() { this.failSel = {}; },
      // 필터를 바꾸면 선택을 비운다 — 안 보이는 건이 딸려 실행되는 사고 방지(콘텐츠 표와 동일)
      pickFailKind(v) { this.failKind = v; this.clearFailPick(); },
      pickFailModel(v) { this.failModel = v; this.clearFailPick(); },
      async rerunFailPicked(force) {
        const hs = this.failPicked;
        if (!hs.length || this.failBusy) return;
        const mname = this.bulkModel || '기본 실행 모델';
        if (!force && !(await this.dsConfirm('실패한 ' + hs.length + '건을 ' + mname + ' 로 다시 실행합니다(건당 비용 발생) · 진행할까요?', { ok: '재실행' }))) return;
        this.failBusy = true;
        let retryConfirm = false;
        try {
          const before = this.failAll.length;
          const r = await (await this._afetch('/rerun-all', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ model: this.bulkModel || '', hashes: hs, force: !!force }) })).json();
          if (r && !r.error) {
            this.clearFailPick(); this.loadDash(); this.fetchIngestStatus();
            await this.loadFails();
            // '재실행 완료' 만 띄우면 목록이 그대로일 때 왜 안 줄었는지 알 수 없다.
            // 실제로 몇 건이 목록에서 빠졌는지(=해소) 세어 알린다.
            const cleared = Math.max(0, before - this.failAll.length);
            this.liveToast('재실행 ' + (r.done || 0) + '건 · 해소 ' + cleared + '건'
              + (this.failAll.length ? (' · 남음 ' + this.failAll.length + '건') : '')
              + (cleared === 0 && this.failBillingN ? ' (잔액 부족 · 충전 전에는 해소되지 않습니다)' : '')
              + (r.over_cap ? (' · 상한 초과 ' + r.over_cap + '건 제외') : ''));
          } else if (r && !force && /퀘스트/.test(r.error || '')) retryConfirm = true;
          else this._err((r && r.error) || '재실행 실패');
        } catch (e) { this._err('재실행 실패'); }
        this.failBusy = false;
        if (retryConfirm) {
          const ok = await this.dsConfirm('퀘스트(검수 목표) 진행 중입니다. 초안을 새로 만들면 기존 검수 의견과 어긋날 수 있습니다. 선택한 ' + hs.length + '건을 재실행할까요?', { title: '퀘스트 중 재실행', ok: '재실행', danger: true });
          if (ok) await this.rerunFailPicked(true);
        }
      },
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
        if (!(await this.dsConfirm('이 정답 항목을 제거할까요? (정답셋에서 빠집니다)', { ok: '제거', danger: true }))) return;
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
      // 개인별 진척도(완료/배정)의 팀 평균(%) — 총 대상보다 배정이 적어도 왜곡 없음 · 구서버는 null
      questTeamPct() { const a = this.arenaData; return a && a.quest_team_progress != null ? Math.round(a.quest_team_progress * 100) : null; },
      // 내 배정 기준 개인화: 목표·남은 건수는 총 대상(예: 200)이 아니라 '내 몫'으로 표시
      questMyTotal() { return this.arenaAssignedTotal || 0; },
      questMyDone() { return Math.min(this.arenaAssignedDone || 0, this.questMyTotal()); },
      questMyLeft() { return Math.max(0, this.questMyTotal() - this.questMyDone()); },
      questLeftMine() { return this.questMyTotal() ? this.questMyLeft() : this.questLeft(); },
      // 오늘의 응원(확정 시안): 리본 = 영어 핵심 단어 · 타이틀 = 문구 · 날짜 로테이션(팀 전원 동일)
      QUEST_CHEERS: [
        { kw: 'STEADY!', msg: '오늘도 한 건씩, 꾸준함이 이겨요' },
        { kw: "LET'S GO!", msg: '몸 풀렸으면 바로 한 건 가보자고' },
        { kw: 'HOMERUN!', msg: '이 페이스면 오늘 홈런각이에요' },
        { kw: 'FOCUS!', msg: '지금 10분이면 3건은 거뜬해요' },
        { kw: 'ON BASE!', msg: '짧게 쳐도 좋아요, 일단 출루' },
        { kw: 'PACE UP!', msg: '어제의 나보다 한 건만 더' },
        { kw: 'TEAMWORK!', msg: '내 한 건이 팀의 진척이 돼요' },
        { kw: 'SLIDE!', msg: '마감 전 세이프, 지금이 타이밍' },
        { kw: 'STREAK!', msg: '어제도 오늘도, 연승 이어가요' },
        { kw: 'NICE CATCH!', msg: '어려운 건 잡아내는 게 실력' },
        { kw: 'RHYTHM!', msg: '한 건 두 건, 리듬 타면 금방이에요' },
        { kw: 'SHARP!', msg: '빠르게보다 정확하게, 그게 프로' },
        { kw: 'LEVEL UP!', msg: '오늘 검수한 만큼 모델이 똑똑해져요' },
        { kw: 'CLUTCH!', msg: '지금이 승부처, 한 건만 더' },
        { kw: 'WARM-UP!', msg: '가볍게 한 건으로 시동 걸어요' },
        { kw: 'COMEBACK!', msg: '지금 시작해도 충분히 역전이에요' },
        { kw: 'CLOSER!', msg: '9회말 마무리, 깔끔하게 가요' },
        { kw: 'STARTER!', msg: '오늘의 선발은 바로 당신' },
        { kw: 'EASY!', msg: '천천히 봐도 좋아요, 정확하면 충분' },
        { kw: 'FULL SWING!', msg: '망설이지 말고 풀스윙으로 가요' },
      ],
      questCheer() {                                     // 내 몫 완료 시엔 풀과 무관하게 DONE! 상태 고정
        if (this.questMyTotal() && !this.questMyLeft()) return { kw: 'DONE!', msg: '내 몫 끝! 이제 팀을 도와줘요', done: true };
        const d = new Date();
        const idx = (d.getFullYear() * 10000 + (d.getMonth() + 1) * 100 + d.getDate()) % this.QUEST_CHEERS.length;
        return this.QUEST_CHEERS[idx];
      },
      questMates() {                                     // 파티 = 팀원 캐릭터(리더보드 순 · 나 제외 · 3명)
        // 캐릭터 종류 기준 중복 제거: 팀원들이 같은 캐릭터를 써도 씬에는 서로 다른 친구들만
        // (리드=내 캐릭터 포함 4종이라 항상 상이한 3명 구성 가능 · 부족분은 미사용 캐릭터로 채움)
        const d = this.arenaData; const me = (d && d.my_id) || this.reviewer || '';
        const seen = new Set([this.reviewerChar]);
        const chars = [];
        (((d && d.leaderboard) || []).filter((r) => (r.reviewer_id || r.reviewer) !== me)).forEach((r) => {
          if (r.char && !seen.has(r.char)) { seen.add(r.char); chars.push(r.char); }
        });
        const fill = ['boksil', 'daesik', 'yonghee', 'ddakji'].filter((c) => !seen.has(c));
        while (chars.length < 3 && fill.length) chars.push(fill.shift());
        return chars.slice(0, 3);
      },
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
        if (action === 'clear_golden' && !(await this.dsConfirm('정답셋을 모두 삭제할까요? 되돌릴 수 없습니다.', { ok: '삭제', danger: true }))) return;
        if (action === 'reset_scores' && !(await this.dsConfirm('팀 전원의 게임 점수·레벨을 0부터 다시 시작할까요? 검수 데이터·배지·정답셋은 그대로 둡니다.', { ok: '초기화', danger: true }))) return;
        if (action === 'delete_team') {
          if (!(await this.dsConfirm('팀을 삭제할까요? 멤버 소속이 모두 해제됩니다.', { ok: '팀 삭제', danger: true }))) return;
          if (!(await this.dsConfirm('정말 삭제합니다. 되돌릴 수 없습니다.', { ok: '최종 삭제', danger: true }))) return;
        }
        try { await this._afetch('/admin', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ action: action, member: member }) }); } catch (e) {}
        this.loadAdmin();
      },
      // 비밀번호 재설정(생성자 전용): 임시 비번을 발급받아 1회 표시 · 생성자가 직접 전달
      pwReset: null,
      async resetMemberPassword(m) {
        if (!(await this.dsConfirm((m.name || '이 멤버') + ' 님의 비밀번호를 재설정할까요? 임시 비밀번호가 발급되며, 기존 비밀번호로는 더 이상 로그인할 수 없습니다.', { ok: '재설정' }))) return;
        try {
          const r = await (await this._afetch('/admin', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ action: 'reset_password', member: m.id }) })).json();
          if (r && r.ok && r.tempPassword) this.pwReset = { member: r.member || m.name, pw: r.tempPassword };
          else this._err((r && r.error) || '재설정에 실패했습니다');
        } catch (e) { this._err('재설정 요청이 실패했습니다 · 네트워크 상태를 확인하세요'); }
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
      // 아레나 파생값(게이지·내 순위)
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
