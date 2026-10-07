/* Prism 앱 조각 23 · 실험실 › 엔티티 재처리(prism/entrefine.py · /lab-entrefine-*).
   한 건 시험 + 정답셋 일괄(백그라운드 잡 · 1.5초 폴링) · 지금 방식과 재처리 키워드 비교 투표(events 영속). */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({

      erModel: '', erHash: '', erTitle: '', erBody: '', erEnts: '', erTrying: false, erOne: null, erOneErr: '',
      erN: 30, erRunning: false, erDone: 0, erTotal: 0, erItems: [], erSum: null, erMsg: '', erErr: false, _erT: null, erVotes: null,
      erPicks: [['refined', '재처리'], ['base', '지금 방식'], ['both', '둘 다 좋음'], ['neither', '둘 다 별로']],

      erPickKo(p) { return (this.erPicks.find((x) => x[0] === p) || [p, p])[1]; },
      erVotesTxt() {
        const v = this.erVotes; if (!v || !v.n) return '아직 투표가 없습니다 · 결과 옆 버튼으로 어느 쪽이 나은지 골라 주세요';
        const t = v.tally; return '투표 ' + v.n + '건 · 재처리 ' + t.refined + ' · 지금 방식 ' + t.base + ' · 둘 다 좋음 ' + t.both + ' · 둘 다 별로 ' + t.neither;
      },
      async erInit() {
        try { const r = await (await this._afetch('/lab-entrefine-votes', { headers: this._authHeaders() })).json(); if (r && r.ok) this.erVotes = r; } catch (e) {}
      },
      async erTry() {
        this.erTrying = true; this.erOne = null; this.erOneErr = '';
        try {
          const r = await (await this._afetch('/lab-entrefine-try', { method: 'POST', headers: this._authHeaders(),
            body: JSON.stringify({ hash: this.erHash, title: this.erTitle, body: this.erBody, entities: this.erEnts, model: this.erModel }) })).json();
          if (r && r.ok) this.erOne = r; else this.erOneErr = (r && r.error) || '재처리 실패';
        } catch (e) { this.erOneErr = '재처리 실패'; }
        this.erTrying = false;
      },
      async erRun() {
        clearTimeout(this._erT); this.erItems = []; this.erSum = null; this.erMsg = ''; this.erErr = false; this.erDone = 0;
        try {
          const r = await (await this._afetch('/lab-entrefine-run', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ n: this.erN, model: this.erModel }) })).json();
          if (!r || !r.ok) { this.erErr = true; this.erMsg = (r && r.error) || '실행 실패'; return; }
          this.erRunning = true; this.erTotal = r.total; this._erPoll(r.id);
        } catch (e) { this.erErr = true; this.erMsg = '실행 실패'; }
      },
      _erPoll(id) {
        const tick = async () => {
          let r = null;
          try { r = await (await this._afetch('/lab-entrefine-status?id=' + id, { headers: this._authHeaders() })).json(); } catch (e) {}
          if (!r || !r.ok) { this.erRunning = false; this.erErr = true; this.erMsg = (r && r.error) || '진척 조회 실패'; return; }
          this.erDone = r.done; this.erTotal = r.total;
          const voted = Object.fromEntries(this.erItems.filter((x) => x._voted).map((x) => [x.hash, x._voted]));
          this.erItems = r.items.map((x) => Object.assign(x, voted[x.hash] ? { _voted: voted[x.hash] } : {}));
          this.erSum = r.summary;
          if (r.running) { this._erT = setTimeout(tick, 1500); return; }
          this.erRunning = false; this.erErr = !!(r.summary && r.summary.fails);
          this.erMsg = r.total + '건 완료 · ' + r.elapsed_s + '초 · 모델 ' + r.model + (r.summary && r.summary.fails ? (' · 실패 ' + r.summary.fails + '건') : '');
        };
        tick();
      },
      async erVote(it, pick) {
        try {
          const r = await (await this._afetch('/lab-entrefine-vote', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({
            reviewer: this.reviewer || '', hash: it.hash, pick: pick, model: this.erModel,
            base: (it.base || []).map((b) => b.name), refined: ((it.refined || {}).keywords || []).map((k) => k.text) }) })).json();
          if (r && r.ok) { it._voted = pick; this.erInit(); } else this._err((r && r.error) || '기록 실패');
        } catch (e) { this._err('기록 실패'); }
      },
}));
