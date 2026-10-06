/* Prism 앱 조각 21 · 실험실 › 솔라 디사이드 판정 시험(prism/decide.py · /lab-decide-*).
   한 건 직접 입력 판정 + 골든셋 일괄 채점(백그라운드 잡 · 1.5초 폴링) · 결과는 서버 메모리에만. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({

      dcTitle: '', dcBody: '', dcGate: 0.5, dcTrying: false, dcOne: null, dcOneErr: '',
      dcN: 30, dcEvalOnly: false, dcRunning: false, dcDone: 0, dcTotal: 0, dcRep: null, dcMsg: '', dcErr: false, _dcT: null,

      dcMetaKo(k) { return ((this.dcOne && this.dcOne.meta_ko) || {})[k] || k; },   // 이름 원천 = dictionaries.QUALITY_META_NAMES
      dcPct(v) { return Math.round((v || 0) * 100) + '%'; },
      dcMetaProbs() {                       // 5% 이상만 · 높은 순
        const m = Object.entries((this.dcOne && this.dcOne.probs.meta) || {}).filter(kv => kv[1] >= 0.05).sort((a, b) => b[1] - a[1]);
        return m.length ? m.map(kv => this.dcMetaKo(kv[0]) + ' ' + this.dcPct(kv[1])).join(' · ') : '모두 5% 미만';
      },
      async dcTry() {
        this.dcTrying = true; this.dcOne = null; this.dcOneErr = '';
        try {
          const r = await (await this._afetch('/lab-decide-try', { method: 'POST', headers: this._authHeaders(),
            body: JSON.stringify({ title: this.dcTitle, body: this.dcBody, gate: this.dcGate }) })).json();
          if (r && r.ok) this.dcOne = r; else this.dcOneErr = (r && r.error) || '판정 실패';
        } catch (e) { this.dcOneErr = '판정 실패'; }
        this.dcTrying = false;
      },
      async dcRun() {
        clearTimeout(this._dcT); this.dcRep = null; this.dcMsg = ''; this.dcErr = false; this.dcDone = 0;
        try {
          const r = await (await this._afetch('/lab-decide-run', { method: 'POST', headers: this._authHeaders(),
            body: JSON.stringify({ n: this.dcN, scope: this.dcEvalOnly ? 'eval' : 'all', gate: this.dcGate }) })).json();
          if (!r || !r.ok) { this.dcErr = true; this.dcMsg = (r && r.error) || '실행 실패'; return; }
          this.dcRunning = true; this.dcTotal = r.total; this._dcPoll(r.id);
        } catch (e) { this.dcErr = true; this.dcMsg = '실행 실패'; }
      },
      _dcPoll(id) {
        const tick = async () => {
          let r = null;
          try { r = await (await this._afetch('/lab-decide-status?id=' + id, { headers: this._authHeaders() })).json(); } catch (e) {}
          if (!r || !r.ok) { this.dcRunning = false; this.dcErr = true; this.dcMsg = (r && r.error) || '진척 조회 실패'; return; }
          this.dcDone = r.done; this.dcTotal = r.total;
          if (r.running) { this._dcT = setTimeout(tick, 1500); return; }
          this.dcRunning = false; this.dcRep = r.report; this.dcErr = !!r.fails;
          this.dcMsg = r.total + '건 완료 · ' + r.elapsed_s + '초 · 기준 확률 ' + r.gate + (r.fails ? (' · 호출 실패 ' + r.fails + '건(빈 산출로 채점) · ' + r.error) : '');
        };
        tick();
      },
      dcRows() {                            // [라벨, 키, 형식, 표본 키, 요약 타일] · 평가 상세(app-04 evalMetricRows)와 같은 라벨
        return [['평가 건수', 'n', 'raw'], ['등급 일치율', 'grade_accuracy', '', 'grade_n', 1], ['유해 미탐률', 'harm_miss_rate', '', '', 1],
                ['사유 일치', 'reason_jaccard'], ['인텐트 적중률', 'intent_hit', '', 'intent_n', 1], ['인텐트 F1(참고)', 'intent_f1', '', 'intent_n'],
                ['인텐트 첫 값 일치', 'intent_top1', '', 'intent_n'], ['카테고리 F1(계층)', 'cat_hf1', '', 'cat_n', 1], ['카테고리 F1', 'cat_f1', '', 'cat_n'],
                ['빈 결과', 'empty_rate'], ['비용($)', 'cost_usd', 'cost', '', 1], ['응답 속도 p50', 'latency_p50_ms', 'lat', '', 1], ['응답 속도 p95', 'latency_p95_ms', 'lat']];
      },
      dcFmt(row) {
        const v = this.dcRep && this.dcRep[row[1]];
        if (v == null || (row[3] && !this.dcRep[row[3]])) return '·';   // 표본 0 이면 0% 가 아니라 '없음'
        if (row[2] === 'raw') return v;
        if (row[2] === 'cost') return '$' + Number(v).toFixed(4);
        if (row[2] === 'lat') return Math.round(v) + 'ms';
        return Math.round(v * 1000) / 10 + '%';
      },
}));
