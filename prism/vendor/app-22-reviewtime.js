/* Prism 앱 조각 22 · 검수 소요 시간 측정(prism/reviewtime.py · POST /review-time).
   콘텐츠 상세 진입(openDetail → _rtStart)부터 상세를 떠날 때(_rtFlush)까지 한 번의 진입을 잰다.
   · wall = 화면에 머문 시간 · active = 탭이 보이고 마지막 입력 뒤 IDLE 이내였던 1초 단위 누적
   · 판정 시각: 정확 = 저장 순간(setFeedback) · 수정 = '수정 필요'를 누른 순간(pendingBad 감지)
   · note 시각: 교정 메모 저장(saveFbNote) · 판정 없이 닫으면 abandoned · 이미 판정한 콘텐츠면 revisit
   보내기는 상세를 닫거나 다른 콘텐츠로 넘어가거나 페이지를 떠날 때 한 번(keepalive). */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({

      _rt: null, _rtTimer: null, _rtLastInput: 0, _rtBound: false,
      _RT_IDLE_MS: 120000,                // reviewtime.IDLE_MS 와 같은 값

      rtsDays: 14, rtsData: null, rtsMsg: '',      // 관리자 요약(검수운영 › 현황 · GET /review-time-stats)
      async rtsLoad() {
        this.rtsMsg = '';
        try {
          const r = await (await this._afetch('/review-time-stats?days=' + this.rtsDays, { headers: this._authHeaders() })).json();
          if (r && r.ok) this.rtsData = r; else this.rtsMsg = (r && r.error) || '불러오지 못했습니다';
        } catch (e) { this.rtsMsg = '불러오지 못했습니다'; }
      },
      rtsSec(s) {
        if (s == null) return '·';
        return s >= 60 ? (Math.floor(s / 60) + '분 ' + Math.round(s % 60) + '초') : (Math.round(s) + '초');
      },

      _rtStart(c) {
        this._rtFlush();                  // 앞 진입 마감(다른 콘텐츠로 바로 넘어온 경우)
        if (!c || !c.hash) return;
        const now = Date.now();
        this._rt = { hash: c.hash, open: now, active: 0, verdictWall: null, verdictActive: null, badWall: null, badActive: null,
                     noteWall: null, noteActive: null, verdict: '', bodyLen: String(c.body || '').length,
                     gold: String(c.hash).startsWith('gold:'), src: this.mod || '', revisit: !!this.myVerdict(c.fb || {}) };
        this._rtLastInput = now;
        if (!this._rtBound) {
          const touch = () => { this._rtLastInput = Date.now(); };
          ['keydown', 'mousedown', 'mousemove', 'wheel', 'touchstart', 'scroll'].forEach((t) => window.addEventListener(t, touch, { passive: true, capture: true }));
          window.addEventListener('pagehide', () => this._rtFlush());
          this._rtBound = true;
        }
        clearInterval(this._rtTimer);
        this._rtTimer = setInterval(() => this._rtTick(), 1000);
      },
      _rtTick() {
        const r = this._rt;
        if (!r) { clearInterval(this._rtTimer); return; }
        if (!this.detailOpen || !this.detail || this.detail.hash !== r.hash) { this._rtFlush(); return; }
        if (!document.hidden && Date.now() - this._rtLastInput < this._RT_IDLE_MS) r.active += 1000;
        if (this.pendingBad && r.badWall == null) { r.badWall = Date.now() - r.open; r.badActive = r.active; }   // '수정 필요'를 누른 순간
      },
      _rtMark(c, kind, verdict) {         // kind: verdict(정확 저장) | note(교정 메모 저장 · 수정 완료)
        const r = this._rt;
        if (!r || !c || c.hash !== r.hash) return;
        const wall = Date.now() - r.open;
        if (kind === 'note') {
          r.noteWall = wall; r.noteActive = r.active;
          if (r.verdictWall == null && !r.revisit) {
            r.verdictWall = r.badWall != null ? r.badWall : wall; r.verdictActive = r.badActive != null ? r.badActive : r.active; r.verdict = 'bad';
          }
        } else if (r.verdictWall == null && !r.revisit) {
          r.verdictWall = wall; r.verdictActive = r.active; r.verdict = verdict || '';
        }
      },
      _rtFlush() {
        const r = this._rt;
        this._rt = null; clearInterval(this._rtTimer);
        if (!r) return;
        const wall = Date.now() - r.open;
        if (wall < 1000) return;          // 스쳐 지나간 진입은 버린다
        const body = { reviewer: this.reviewer || '', hash: r.hash, outcome: r.verdictWall != null ? 'verdict' : (r.revisit ? 'revisit' : 'abandoned'), verdict: r.verdict,
                       wall_ms: wall, active_ms: r.active, verdict_wall_ms: r.verdictWall, verdict_active_ms: r.verdictActive,
                       note_wall_ms: r.noteWall, note_active_ms: r.noteActive, body_len: r.bodyLen, gold: r.gold, src: r.src };
        try { fetch('/review-time', { method: 'POST', keepalive: true, headers: this._authHeaders(), body: JSON.stringify(body) }).catch(() => {}); } catch (e) {}
      },
}));
