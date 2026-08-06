/* Prism 앱 조각 15 · prismApp 프로퍼티 그룹. 로그뷰어(실험실 · 사용자 서브탭):
   PAST 로그 검증 도구 시연. 기획 "13. 로그 검증 도구" 기반 · 백엔드 GET/POST /usermeta-logviewer.

   뷰어가 아니라 검증 도구: 시연 세션(= 검증 세션 · uuid 분리)의 발생 로그를 봉투로 합성해
   ① 로그 단위 형식 판정 ② 중복 검사 ③ 기대 로그 체크리스트 대조를 서버가 계산해 내려주고,
   화면은 판정 결과(합격·불합격·경고·정보)와 근거만 보여준다. 판정 로직은 전부 서버(pastcheck.py).
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({

      lv: null,                     // GET /usermeta-logviewer 응답 {session, logs, behavior, checklist, bad_on}
      lvSel: -1,                    // 상세 열람 중인 로그 인덱스(-1 = 닫힘)
      lvBusy: false, lvMsg: '',

      async loadLogViewer() {
        try {
          const d = await (await this._afetch('/usermeta-logviewer', { headers: this._authHeaders() })).json();
          if (d && !d.error) { this.lv = d; if (this.lvSel >= (d.logs || []).length) this.lvSel = -1; }
        } catch (e) {}
      },
      async lvOps(op) {             // inject(위반 예시 주입) · clear(주입 제거)
        this.lvBusy = true; this.lvMsg = '';
        try {
          const d = await (await this._afetch('/usermeta-logviewer', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ op }) })).json();
          if (!d || d.error) { this.lvMsg = '오류: ' + (d ? d.error : '응답 없음'); return; }
          this.lv = d; this.lvSel = -1;
        } catch (e) { this.lvMsg = '오류: ' + e; } finally { this.lvBusy = false; }
      },
      lvBadge(v) {                  // 판정 → 배지 클래스(합격·경고·불합격·정보)
        return v === 'fail' ? 'ds-badge--error' : v === 'warn' ? 'ds-badge--warning'
             : v === 'pass' ? 'ds-badge--success' : 'ds-badge--neutral';
      },
      lvLabel(v) { return v === 'fail' ? '불합격' : v === 'warn' ? '경고' : v === 'pass' ? '합격' : '정보'; },
      lvLog(i) { return (this.lv && this.lv.logs && this.lv.logs[i]) || null; },
}));
