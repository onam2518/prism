/* Prism 앱 조각 18 · AI 초안 판정(실험실 · 운영자 전용). 별도 심판 모델이 나에게 배정된
   대기 콘텐츠를 미리 채점(정확/수정 + 근거 + 확신도) → 사람은 확정/뒤집기만 한다.
   오래 걸릴 수 있어 백그라운드 잡 + 진척도/예상 시간(평가 런과 같은 폴링 패턴).
   자동 커밋 안 함 · 확정은 평소 검수와 같은 /feedback(사람 행위). 골드는 대상 아님.
   run id 를 localStorage 에 두어, 잠시 페이지를 나갔다 와도 같은 실행에 다시 붙는다
   (재실행으로 이미 채점한 걸 다시 돌리지 않게). 확정 여부는 서버 status 의 judged(=feedback)로 복원.
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({

      arModel: '', arModels: [],            // 심판 모델(설정) · 후보
      arRunId: null, arRunning: false, arDone: 0, arTotal: 0, arPct: 0, arEta: '', arScope: '',
      arTruncated: 0, arItems: [], arMsg: '', _arPollT: null,

      _arSaveRun(id) { try { if (id) localStorage.setItem('prism_ar_run', String(id)); else localStorage.removeItem('prism_ar_run'); } catch (e) {} },
      _arLoadRun() { try { const v = parseInt(localStorage.getItem('prism_ar_run') || '', 10); return Number.isFinite(v) ? v : null; } catch (e) { return null; } },

      arInit() {                            // 서브탭 진입: cfg 에서 심판 모델·후보 받기 + 진행/완료된 실행 이어받기
        const c = this.cfg || {};
        if (typeof c.draftJudgeModel === 'string' && c.draftJudgeModel) this.arModel = c.draftJudgeModel;
        if (Array.isArray(c.draftJudgeModels)) this.arModels = c.draftJudgeModels;
        const prev = this._arLoadRun();
        if (prev && this.arRunId !== prev) { this.arRunId = prev; this.arMsg = '이전 실행을 이어봅니다…'; this._arPoll(prev); }
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
      arScopeTxt() { return (this.arScope === 'assigned' ? '내 배정' : '대기') + ' 콘텐츠'; },

      async arRun() {                       // 백그라운드 잡 시작 → 폴링으로 진척도·부분 결과
        clearTimeout(this._arPollT);
        this.arMsg = ''; this.arItems = []; this.arDone = 0; this.arTotal = 0; this.arPct = 0; this.arEta = ''; this.arRunId = null; this.arTruncated = 0;
        this.arRunning = true;
        try {
          const r = await (await this._afetch('/autoreview-run', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({}) })).json();
          if (!r || r.error) { this.arRunning = false; this.arMsg = (r && r.error) || '실행 실패'; return; }
          this.arScope = r.scope || '';
          if (!r.total) { this.arRunning = false; this._arSaveRun(null); this.arMsg = r.empty || '검수 대상이 없습니다'; return; }
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
          // 확정 상태 복원: 내가 방금 누른 로컬 _done 우선, 없으면 서버 judged(=feedback 반영)
          this.arItems = (r.items || []).map((x) => { const p = this.arItems.find((y) => y.hash === x.hash); return Object.assign({ _done: (p && p._done) || x.judged || '' }, x); });
          this.arRunning = !!r.running;
          if (r.running) { this._arPollT = setTimeout(tick, 1500); }
          else { this.arMsg = this.arScopeTxt() + ' ' + this.arTotal + '건 완료' + (r.elapsed ? (' · 소요 ' + r.elapsed) : '') + ' · 확정/뒤집기는 직접 눌러야 반영됩니다'; }
        };
        tick();
      },
      async arConfirm(it, verdict) {        // 확정 = 평소 검수와 같은 /feedback(사람 행위) · 초안임을 note 에 표기
        if (it._done || !verdict) return;
        const els = verdict === 'bad' ? ((it.ai && it.ai.elements) || []) : [];
        const note = 'AI 초안 확인' + (it.ai && it.ai.reason ? (' · ' + it.ai.reason) : '');
        const r = await this._postFb({ hash: it.hash, service: it.service, title: it.title, verdict: verdict, elements: els, note: note });
        if (r && r.ok !== false) it._done = verdict;
        else this.arMsg = '확정 저장 실패 · 다시 시도하세요';
      },
}));
