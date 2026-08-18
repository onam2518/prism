/* Prism 앱 조각 18 · AI 초안 판정(실험실 · 운영자 전용). 별도 심판 모델이 대기 콘텐츠의
   메타데이터를 정확/수정 초안 + 근거 + 확신도로 채우면, 사람은 확정/뒤집기만 한다.
   판정을 자동 커밋하지 않는다 — 확정은 평소 검수와 같은 /feedback(사람 행위)으로만.
   골드 문항은 대상이 아니다(서버 autoreview.suggest 가 실 콘텐츠만 본다).
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({

      arModel: '', arModels: [],            // 심판 모델(설정) · 후보(config.draftJudgeModel*)
      arItems: [], arBusy: false, arMsg: '', arLimit: 20,

      arInit() {                            // 서브탭 진입: cfg 에서 심판 모델·후보 받기
        const c = this.cfg || {};
        if (typeof c.draftJudgeModel === 'string' && c.draftJudgeModel) this.arModel = c.draftJudgeModel;
        if (Array.isArray(c.draftJudgeModels)) this.arModels = c.draftJudgeModels;
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

      async arRun() {                       // 대기 콘텐츠에 AI 초안 판정 채우기(커밋 안 함)
        this.arBusy = true; this.arMsg = ''; this.arItems = [];
        try {
          const r = await (await this._afetch('/autoreview-run', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ limit: this.arLimit }) })).json();
          if (!r || r.error) { this.arMsg = (r && r.error) || '실행 실패'; return; }
          this.arItems = (r.items || []).map((x) => Object.assign({ _done: '' }, x));
          this.arMsg = this.arItems.length ? ('심판 ' + (r.model || '') + ' · ' + this.arItems.length + '건 · 확정/뒤집기는 직접 눌러야 반영됩니다')
                                           : '검수 대기 콘텐츠가 없습니다';
        } catch (e) { this.arMsg = '실행 실패'; } finally { this.arBusy = false; }
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
