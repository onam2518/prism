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

      /* ── 필터·검색(기획 131 R8) ────────────────────────────────────────
         현행 필터가 행동 유형·서비스 2축뿐이라는 개선 근거에 대응. 판정 결과 기준
         필터로 불합격만 즉시 보는 것이 R8 수용 기준이다. 필드값 검색은 상세를 열지
         않고도 값으로 로그를 찾기 위한 것(인터뷰 개선 희망 항목). */
      lvFilter: '',                 // '' | fail | warn | info | pass
      lvType: '',                   // 행동 유형(Pageview·Event·ViewImp·Usage)
      lvQ: '',                      // 필드값·이름·사유 검색
      lvOnlyMiss: false,            // 체크리스트: 미달(누락·중복)만 보기
      lvTypes() {
        return [...new Set(((this.lv && this.lv.logs) || []).map((l) => (l.title.match(/^\[([^\]]+)\]/) || [])[1]).filter(Boolean))];
      },
      // 원본 인덱스(_i)를 실어 보낸다 — 상세 열람(lvSel)은 필터와 무관하게 원본 기준이어야 한다
      lvShown() {
        const q = (this.lvQ || '').trim().toLowerCase();
        return ((this.lv && this.lv.logs) || []).map((l, i) => ({ l, i })).filter(({ l }) => {
          if (this.lvFilter && l.verdict !== this.lvFilter) return false;
          if (this.lvType && l.title.indexOf('[' + this.lvType + ']') !== 0) return false;
          if (!q) return true;
          if (l.title.toLowerCase().includes(q)) return true;
          if ((l.violations || []).some((v) => String(v[1]).toLowerCase().includes(q)
                                            || String(v[2] || '').toLowerCase().includes(q))) return true;
          return (l.fields || []).some((f) => String(f[0]).toLowerCase().includes(q)
                                           || String(f[1]).toLowerCase().includes(q));
        });
      },
      lvTally(v) { return ((this.lv && this.lv.logs) || []).filter((l) => l.verdict === v).length; },
      // 판정 근거 규칙 항목(R2 수용 기준) · 같은 규칙이 여러 번 걸리면 한 번만 보여 준다
      lvRules(l) { return [...new Set(((l && l.violations) || []).map((v) => v[3]).filter(Boolean))]; },
      lvChecklist() {
        const items = (this.lv && this.lv.checklist && this.lv.checklist.items) || [];
        return this.lvOnlyMiss ? items.filter((it) => it.state !== 'pass') : items;
      },
      lvStateBadge(s) { return s === 'pass' ? 'ds-badge--success' : s === 'dup' ? 'ds-badge--warning' : 'ds-badge--error'; },
      lvStateLabel(s) { return s === 'pass' ? '발생' : s === 'dup' ? '중복' : '누락'; },

      /* ── 판정 규칙 시뮬레이터(기획 131 · Prism 조건부 활용안) ──────────────
         131 배치 검토 결론은 3안 독립 신설이고 Prism 배치는 부적합(실 로그를 외부
         인프라로 반출 불가)이다. 그 문서가 Prism 에 남긴 조기 시범 범위가
         "실 로그 없이 가능한 판정 규칙 시뮬레이터(샘플 로그 붙여넣기 검사)"라 그것만 둔다.
         붙여넣은 표본은 판정만 하고 저장하지 않는다(POST /usermeta-logcheck). */
      lvSimText: '', lvSimBusy: false, lvSimMsg: '', lvSim: null, lvSimSel: -1,
      async lvSimRun() {
        this.lvSimBusy = true; this.lvSimMsg = ''; this.lvSimSel = -1;
        try {
          const d = await (await this._afetch('/usermeta-logcheck', {
            method: 'POST', headers: this._authHeaders(),
            body: JSON.stringify({ text: this.lvSimText }),
          })).json();
          if (!d || d.error) { this.lvSim = null; this.lvSimMsg = d ? d.error : '응답 없음'; return; }
          this.lvSim = d;
          this.lvSimMsg = d.errors && d.errors.length ? ('파싱 건너뜀 ' + d.errors.length + '건') : '';
        } catch (e) { this.lvSim = null; this.lvSimMsg = '오류: ' + e; } finally { this.lvSimBusy = false; }
      },
      lvSimClear() { this.lvSimText = ''; this.lvSim = null; this.lvSimMsg = ''; this.lvSimSel = -1; },
      // 예시 채우기: 정상 1건 + 위반 2건(정의 외 값·필수 누락·중복·JSON 형식) · 그룹 표기로 둔다
      lvSimSample() {
        this.lvSimText = JSON.stringify([
          { log_unique_id: 'a1', access_timestamp: 1785900000000,
            common: { service_id: 'daum_news', deployment: 'real', sdk_type: 'WEB', uuid: 'u-1', suid: 's-1', islogin: false, page: 'home_tab' },
            action: { type: 'Event', name: '홈탭_기사_클릭', kind: 'ClickContent' },
            'content.id': 7, 'click.layer1': 'main_feed', custom_props: { tesla_slot: 'A1' } },
          { log_unique_id: 'a2', access_timestamp: 1785900001000,
            common: { service_id: 'daum_news', deployment: 'real', sdk_type: 'WEB', uuid: 'u-1', suid: 's-1', islogin: false },
            action: { type: 'Nope', name: 'item', kind: 'Bogus' } },
          { log_unique_id: 'a1', access_timestamp: 1785900002000,
            common: { service_id: '', deployment: 'real', sdk_type: 'WEB', uuid: 'u-1', suid: 's-1', islogin: false, page: 'home_tab' },
            action: { type: 'ViewImp', name: '홈탭_노출' }, viewimp_extra: '{oops' },
        ], null, 2);
        this.lvSim = null; this.lvSimMsg = '';
      },
      lvSimLog(i) { return (this.lvSim && this.lvSim.logs && this.lvSim.logs[i]) || null; },
}));
