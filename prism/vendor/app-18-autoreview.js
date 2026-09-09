/* Prism 앱 조각 18 · AI 초안 판정(실험실 · 운영자 전용). 별도 심판 모델이 나에게 배정된
   대기 콘텐츠를 미리 채점(정확/수정 + 근거 + 확신도) → 사람은 확정/뒤집기만 한다.
   오래 걸릴 수 있어 백그라운드 잡 + 진척도/예상 시간(평가 런과 같은 폴링 패턴).
   자동 커밋 안 함 · 확정은 평소 검수와 같은 /feedback(사람 행위). 골드는 대상 아님.
   초안은 서버에 상시 적재된다: 서브탭 진입 시 /autoreview-drafts 로 저장된 초안을 표에 채우고
   (나갔다 와도·배포돼도 그대로), 실행은 새 초안만 더한다(_arMerge 로 hash upsert). run id 는
   localStorage 에 두어 진행 중 실행의 진척도만 이어본다. 확정 여부는 서버 judged(=feedback)로 복원.
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({

      arModel: '', arModels: [],            // 심판 모델(설정) · 후보
      arRunId: null, arRunning: false, arDone: 0, arTotal: 0, arPct: 0, arEta: '', arScope: '',
      arTruncated: 0, arItems: [], arMsg: '', _arPollT: null,
      arAssigned: 0, arFilter: 'all',       // 내 배정 총건(진척 스트립) · 인박스 세그먼트 필터
      arPage: 1, arPageSize: 50,            // 목록 페이지네이션(현재 페이지만 렌더 · DOM 절약)

      _arSaveRun(id) { try { if (id) localStorage.setItem('prism_ar_run', String(id)); else localStorage.removeItem('prism_ar_run'); } catch (e) {} },
      _arLoadRun() { try { const v = parseInt(localStorage.getItem('prism_ar_run') || '', 10); return Number.isFinite(v) ? v : null; } catch (e) { return null; } },

      async arInit() {                      // 서브탭 진입: 심판 모델·후보 + 저장된 초안(상시 목록) + 진행 중 실행 이어받기
        const c = this.cfg || {};
        if (typeof c.draftJudgeModel === 'string' && c.draftJudgeModel) this.arModel = c.draftJudgeModel;
        if (Array.isArray(c.draftJudgeModels)) this.arModels = c.draftJudgeModels;
        await this._arLoadDrafts();         // 저장된 초안을 표에 채운다 — 나갔다 와도·배포돼도 그대로
        const prev = this._arLoadRun();
        if (prev && this.arRunId !== prev) { this.arRunId = prev; this._arPoll(prev); }  // 진행 중이면 진척도 이어보기
      },
      async _arLoadDrafts() {               // 상시 적재된 초안(실행과 무관)을 서버에서 로드
        try {
          const r = await (await this._afetch('/autoreview-drafts', { headers: this._authHeaders() })).json();
          if (r && r.ok !== false && Array.isArray(r.items)) { this._arMerge(r.items); this.arAssigned = r.assigned || 0; }
        } catch (e) {}
      },
      _arMerge(items) {                     // hash 로 upsert(덮어쓰기 아님) · 내가 방금 누른 로컬 _done 보존
        const by = {}; (this.arItems || []).forEach((y) => { by[y.hash] = y; });
        (items || []).forEach((x) => { const p = by[x.hash];
          by[x.hash] = Object.assign({}, p || {}, x, { _done: (p && p._done) || x.judged || '' }); });
        this.arItems = Object.values(by);
      },
      async saveDraftJudgeModel() {         // 실험실 모델 설정 저장(/config · assist_model 과 같은 규약)
        this.arMsg = '';
        try {
          const r = await (await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ draft_judge_model: this.arModel || '' }) })).json();
          if (r && r.error) { this.arMsg = r.error; return; }
          if (r && typeof r.draftJudgeModel === 'string' && r.draftJudgeModel) this.arModel = r.draftJudgeModel;
          if (this.cfg) this.cfg.draftJudgeModel = this.arModel;
        } catch (e) { this.arMsg = '모델 저장 실패'; }
      },

      arConf(ai) { return ai && ai.confidence != null ? Math.round(ai.confidence * 100) : 0; },
      arLow(it) { return it.ai && it.ai.verdict && (it.ai.confidence || 0) < 0.6; },  // 저확신 = 꼭 직접 보게 강조
      arDoneN() { return (this.arItems || []).filter((x) => x._done).length; },
      arPendingN() { return (this.arItems || []).filter((x) => !x._done).length; },   // 미확정(초안만 있고 아직 확정 안 함)
      arScopeTxt() { return (this.arScope === 'assigned' ? '내 배정' : '대기') + ' 콘텐츠'; },
      arGradeCls(g) { return g === 'G' ? 'argdot--g' : g === 'R' ? 'argdot--r' : 'argdot--n'; },  // 등급 dot 색
      arFailed(x) { return !x._done && (!x.ai || !x.ai.verdict); },   // 판정 실패(라우터 실패로 verdict 없음) · 재판정 대상
      arFailedN() { return (this.arItems || []).filter((x) => this.arFailed(x)).length; },
      arCounts() {                          // 세그먼트 필터 배지 수
        const its = this.arItems || [];
        return { all: its.length, pending: its.filter((x) => !x._done).length,
                 low: its.filter((x) => !x._done && this.arLow(x)).length,
                 bad: its.filter((x) => x.ai && x.ai.verdict === 'bad').length,
                 failed: its.filter((x) => this.arFailed(x)).length,
                 done: its.filter((x) => x._done).length };
      },
      arView() {                            // 현재 필터로 좁힌 인박스(미확정 먼저 · 서버 정렬 유지)
        const f = this.arFilter, its = this.arItems || [];
        if (f === 'pending') return its.filter((x) => !x._done);
        if (f === 'low') return its.filter((x) => !x._done && this.arLow(x));
        if (f === 'bad') return its.filter((x) => x.ai && x.ai.verdict === 'bad');
        if (f === 'failed') return its.filter((x) => this.arFailed(x));
        if (f === 'done') return its.filter((x) => x._done);
        return its;
      },
      arPages() { return Math.max(1, Math.ceil(this.arView().length / this.arPageSize)); },   // 총 페이지 수
      arPageNow() { return Math.min(Math.max(1, this.arPage), this.arPages()); },              // 범위 클램프한 현재 페이지
      arPaged() {                           // 현재 페이지 조각만 렌더 → 목록이 길어도 DOM 은 한 페이지
        const p = this.arPageNow(), sz = this.arPageSize;
        return this.arView().slice((p - 1) * sz, p * sz);
      },
      arGoto(p) { this.arPage = Math.min(Math.max(1, p), this.arPages()); },                   // 이전/다음
      arSetFilter(f) { this.arFilter = f; this.arPage = 1; },                                  // 필터 바꾸면 1페이지로

      async arRun() {                       // 백그라운드 잡 시작 → 폴링으로 진척도·부분 결과
        clearTimeout(this._arPollT);
        this.arMsg = ''; this.arDone = 0; this.arTotal = 0; this.arPct = 0; this.arEta = ''; this.arRunId = null; this.arTruncated = 0;
        this.arRunning = true;              // arItems 는 비우지 않는다 — 상시 목록에 새 초안이 더해진다
        try {
          const r = await (await this._afetch('/autoreview-run', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({}) })).json();
          if (!r || r.error) { this.arRunning = false; this.arMsg = (r && r.error) || '실행 실패'; return; }
          this.arScope = r.scope || '';
          if (!r.total) { this.arRunning = false; this._arSaveRun(null); this.arMsg = r.empty || '새로 판정할 대상이 없습니다'; await this._arLoadDrafts(); return; }
          this.arRunId = r.id; this.arTotal = r.total; this.arTruncated = r.truncated || 0; this._arSaveRun(r.id);
          this.arMsg = '심판 ' + (r.model || '') + ' · ' + this.arScopeTxt() + ' ' + r.total + '건 채점 중…' + (r.truncated ? (' (상한 초과 ' + r.truncated + '건 제외)') : '');
          this._arPoll(r.id);
        } catch (e) { this.arRunning = false; this.arMsg = '실행 실패'; }
      },
      _arPoll(id) {
        clearTimeout(this._arPollT);
        const tick = async () => {
          let r = null;
          try { r = await (await this._afetch('/autoreview-status?id=' + id, { headers: this._authHeaders() })).json(); } catch (e) {}
          if (!r || this.arRunId !== id) return;
          if (r.error) { this.arRunning = false; this.arRunId = null; this._arSaveRun(null); this.arMsg = r.error; return; }
          this.arDone = r.done || 0; this.arTotal = r.total || 0; this.arPct = r.pct || 0; this.arScope = r.scope || this.arScope; this.arTruncated = r.truncated || 0;
          this.arEta = r.eta || '';
          // 새 초안을 상시 목록에 병합(로컬 _done 보존 · 서버 judged=feedback 반영)
          this._arMerge(r.items);
          this.arRunning = !!r.running;
          if (r.running) { this._arPollT = setTimeout(tick, 1500); }
          else { this.arMsg = this.arScopeTxt() + ' ' + this.arTotal + '건 완료' + (r.elapsed ? (' · 소요 ' + r.elapsed) : '') + ' · 확정/뒤집기는 직접 눌러야 반영됩니다'; }
        };
        tick();
      },
      async arConfirm(it, verdict) {        // 확정 = 평소 검수와 같은 /feedback(사람 행위) · 초안임을 note 에 표기
        if (it._done || it._saving || !verdict) return;
        it._saving = true;
        const els = verdict === 'bad' ? ((it.ai && it.ai.elements) || []) : [];
        const note = 'AI 초안 확인' + (it.ai && it.ai.reason ? (' · ' + it.ai.reason) : '');
        try {
          const r = await this._postFb({ hash: it.hash, service: it.service, title: it.title, verdict: verdict, elements: els, note: note });
          if (r && r.ok !== false) it._done = verdict;
          else this.arMsg = '확정 저장 실패 · 다시 시도하세요';
        } catch (e) { this.arMsg = '확정 저장 실패 · 다시 시도하세요'; }
        finally { it._saving = false; }
      },
}));
