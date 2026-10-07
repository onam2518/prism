/* Prism 앱 조각 23 · 실험실 › 핵심 키워드 / 문장(prism/entrefine.py · /lab-entrefine-* · /lab-core-config).
   STEP 1 모델·프롬프트(호출별 모델·규칙 저장 · 출력 형식 고정) → STEP 2 실행(단건 | 다건 · 키워드 비교 투표). */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({

      erMode: 'one', erCfg: null, erRules: { keyword: '', sentence: '' }, erDrafts: { keyword: {}, sentence: {} }, erKwModel: '', erStModel: '', erSaving: false, erCfgMsg: '', erCfgErr: false,
      erHash: '', erTitle: '', erBody: '', erEnts: '', erSum1: '', erInt: '', erCat: '', erTrying: false, erOne: null, erOneErr: '',
      erN: 30, erRunning: false, erDone: 0, erTotal: 0, erPhase: '', erItems: [], erSum: null, erMsg: '', erErr: false, _erT: null, erVotes: null,
      erPage: 1, erPageSize: 10,
      erPicks: [['refined', '재가공'], ['base', '지금 방식'], ['both', '둘 다 좋음'], ['neither', '둘 다 별로']],

      erPickKo(p) { return (this.erPicks.find((x) => x[0] === p) || [p, p])[1]; },
      erVotesTxt() {
        const v = this.erVotes; if (!v || !v.n) return '아직 투표가 없습니다 · 결과 옆 버튼으로 어느 쪽이 나은지 골라 주세요';
        const t = v.tally; return '투표 ' + v.n + '건 · 재처리 ' + t.refined + ' · 지금 방식 ' + t.base + ' · 둘 다 좋음 ' + t.both + ' · 둘 다 별로 ' + t.neither;
      },
      async erInit() {
        try { const r = await (await this._afetch('/lab-entrefine-votes', { headers: this._authHeaders() })).json(); if (r && r.ok) this.erVotes = r; } catch (e) {}
        if (!this.erCfg) this.erLoadCfg();
      },
      _erApplyCfg(r) {
        this.erCfg = r; this.erRules = { keyword: r.keyword.rules, sentence: r.sentence.rules };
        this.erKwModel = r.keyword.model || ''; this.erStModel = r.sentence.model || '';
        this.erDrafts = { keyword: { ...r.keyword.rules_by_model }, sentence: { ...r.sentence.rules_by_model } };
      },
      _erModelKey(model) { return (model || '').split('|').pop().trim() || (this.erCfg && this.erCfg.default_model) || ''; },
      _erRememberRules(call) {
        const model = call === 'keyword' ? this.erKwModel : this.erStModel;
        this.erDrafts[call][this._erModelKey(model)] = this.erRules[call];
      },
      erSelectModel(call, model) {
        if (!this.erCfg || this.erSaving) return;
        this._erRememberRules(call);
        if (call === 'keyword') this.erKwModel = model; else this.erStModel = model;
        this.erRules[call] = this.erDrafts[call][this._erModelKey(model)] || this.erCfg.defaults[call];
      },
      erPageCount() { return Math.max(1, Math.ceil(this.erItems.length / this.erPageSize)); },
      erPageItems() { return this.erItems.slice((this.erPage - 1) * this.erPageSize, this.erPage * this.erPageSize); },
      erPageLine() {
        const start = (this.erPage - 1) * this.erPageSize;
        return this.erItems.length + '건 중 ' + (start + 1) + '~' + Math.min(start + this.erPageSize, this.erItems.length) + '건';
      },
      async erLoadCfg() {
        this.erCfgMsg = ''; this.erCfgErr = false;
        try {
          const r = await (await this._afetch('/lab-core-config', { headers: this._authHeaders() })).json();
          if (!r || !r.ok) throw new Error((r && r.error) || '설정을 불러오지 못했습니다');
          this._erApplyCfg(r);
        } catch (e) { this.erCfgErr = true; this.erCfgMsg = e.message || '설정을 불러오지 못했습니다'; }
      },
      async erSaveCfg() {
        if (!this.erCfg || this.erSaving) return;
        this._erRememberRules('keyword'); this._erRememberRules('sentence');
        this.erSaving = true; this.erCfgMsg = ''; this.erCfgErr = false;
        try {
          const r = await (await this._afetch('/lab-core-config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({
            keyword: { model: this.erKwModel, rules: this.erRules.keyword, rules_by_model: this.erDrafts.keyword },
            sentence: { model: this.erStModel, rules: this.erRules.sentence, rules_by_model: this.erDrafts.sentence } }) })).json();
          if (r && r.ok) { this._erApplyCfg(r); this.erCfgMsg = '저장했습니다 · 다음 실행부터 적용됩니다'; } else { this.erCfgErr = true; this.erCfgMsg = (r && r.error) || '저장 실패'; }
        } catch (e) { this.erCfgErr = true; this.erCfgMsg = '저장 실패'; }
        this.erSaving = false;
      },
      erCfgLine() {
        const c = this.erCfg; if (!c) return '모델·프롬프트 설정을 불러오는 중입니다';
        const one = (x, n) => n + ' · ' + (x.model || '기본 모델') + (x.custom ? ' · 수정한 프롬프트' : ' · 기본 프롬프트');
        return one(c.keyword, '핵심 키워드') + '  /  ' + one(c.sentence, '핵심 문장');
      },
      erOneMeta() {
        const o = this.erOne || {}, m = o.models || {}, s = o.sentence || {};
        return '키워드 ' + (m.keyword || '') + ' ' + (o.latency_ms != null ? o.latency_ms + 'ms' : '') + ' · 문장 ' + (m.sentence || '') + ' ' + (s.latency_ms != null ? s.latency_ms + 'ms' : '');
      },
      async erTry() {
        this.erTrying = true; this.erOne = null; this.erOneErr = '';
        try {
          const r = await (await this._afetch('/lab-entrefine-try', { method: 'POST', headers: this._authHeaders(),
            body: JSON.stringify({ hash: this.erHash, title: this.erTitle, body: this.erBody, entities: this.erEnts, summary: this.erSum1, intent: this.erInt, category: this.erCat }) })).json();
          if (r && r.ok) this.erOne = r; else this.erOneErr = (r && r.error) || '재가공 실패';
        } catch (e) { this.erOneErr = '재가공 실패'; }
        this.erTrying = false;
      },
      async erRun() {
        if (this.erRunning) return;
        clearTimeout(this._erT); this.erItems = []; this.erPage = 1; this.erSum = null; this.erMsg = ''; this.erErr = false; this.erDone = 0; this.erPhase = '';
        this.erRunning = true;
        try {
          const r = await (await this._afetch('/lab-entrefine-run', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ n: this.erN }) })).json();
          if (!r || !r.ok) { this.erRunning = false; this.erErr = true; this.erMsg = (r && r.error) || '실행 실패'; return; }
          this.erRunning = true; this.erTotal = r.total; this._erPoll(r.id);
        } catch (e) { this.erRunning = false; this.erErr = true; this.erMsg = '실행 실패'; }
      },
      _erPoll(id) {
        const tick = async () => {
          let r = null;
          try { r = await (await this._afetch('/lab-entrefine-status?id=' + id, { headers: this._authHeaders() })).json(); } catch (e) {}
          if (!r || !r.ok) { this.erRunning = false; this.erErr = true; this.erMsg = (r && r.error) || '진척 조회 실패'; return; }
          this.erDone = r.done; this.erTotal = r.total;
          const stages = Object.values(r.active || {});
          this.erPhase = stages.length ? (stages.includes('keyword') ? '핵심 키워드' : '핵심 문장') + ' 호출 중 · ' + (r.elapsed_s || 0) + '초 경과' : '다음 콘텐츠 준비 중 · ' + (r.elapsed_s || 0) + '초 경과';
          const voted = Object.fromEntries(this.erItems.filter((x) => x._voted).map((x) => [x.hash, x._voted]));
          this.erItems = r.items.map((x) => Object.assign(x, voted[x.hash] ? { _voted: voted[x.hash] } : {}));
          this.erSum = r.summary;
          if (r.running) { this._erT = setTimeout(tick, 1500); return; }
          this.erRunning = false; this.erPhase = '';
          this.erErr = !!(r.error || (r.summary && (r.summary.fails || r.summary.sent_fails)));
          this.erMsg = (r.error ? '일괄 처리 중단 · ' + r.error : r.total + '건 완료') + ' · ' + r.elapsed_s + '초 · 키워드 ' + ((r.models || {}).keyword || '') + ' · 문장 ' + ((r.models || {}).sentence || '');
        };
        tick();
      },
      async erVote(it, pick) {
        try {
          const r = await (await this._afetch('/lab-entrefine-vote', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({
            reviewer: this.reviewer || '', hash: it.hash, pick: pick, model: ((it.models || {}).keyword) || this.erKwModel,
            base: (it.base || []).map((b) => b.name), refined: ((it.refined || {}).keywords || []).map((k) => k.text) }) })).json();
          if (r && r.ok) { it._voted = pick; this.erInit(); } else this._err((r && r.error) || '기록 실패');
        } catch (e) { this._err('기록 실패'); }
      },
}));
