/* Prism 앱 조각 17 · prismApp 프로퍼티 그룹. 검수 보조 패널(트랙 A · 내부 검수 보조).
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유.
   마크업은 prism/ui/19e-review-assist.html(검수 상세 우측 · 판정 바로 위로 텔레포트).

   ## 이 화면이 지키는 규칙

   보조는 검수를 대신하지 않는다. 품질 측정의 독립성이 깨지면 이 도구가 재는 대상이
   사람이 아니라 모델이 되어 버린다. 그래서 화면 쪽에서 세 가지를 강제한다.

   1. **판정 전에는 근거와 기준만 부른다.** 서버로 보내는 stage 는 화면 상태에서
      정한다(내 표가 있으면 after · 없으면 before). 추천·수정 제안은 서버가 after
      에만 싣는다 — 화면이 stage 를 틀리게 실어 보내면 그 계약이 무너지므로
      stage 산출은 myVerdict 한 곳만 본다.
   2. **기본은 접힘이고, 펼 때만 부른다.** 호출 비용도 있지만 더 큰 이유는 주의 분산이다.
      보조를 켜지 않아도 검수는 그대로 된다(패널이 통째로 없어도 판정 UI 는 무관).
   3. **골드 문항에는 보조가 붙지 않는다.** 골드는 검수자 신뢰도를 재는 장치라
      보조가 붙으면 측정이 사라진다. 큐에서 빼는 게 아니라 보조만 막는다.

   ## 없는 건 없다고 말한다

   운영 데이터 상당수가 근거 필드 신설 이전이라 실제로 비어 있다. has_evidence:false 면
   "저장된 근거 없음"을 그대로 보여주고 빈 자리를 채우지 않는다. 목록이 잘렸으면
   truncated 를 화면에 띄운다 — 조용한 절단은 "다 봤다"로 읽혀 판단을 그르친다.

   ## 백엔드가 없어도 화면이 깨지지 않는다

   POST /assist 는 다른 트랙에서 붙인다. 404 면 보조 영역만 조용히 비활성하고
   토스트도 띄우지 않는다(검수 흐름에 끼어들지 않는다). 한 번 404 를 보면 다시 부르지 않는다. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({

      asxOpen: false,          // 펼침 여부(기본 접힘 · 검수자가 열 때만 호출)
      asxBusy: false,
      asxOff: false,           // /assist 미제공(404) · 한 번 확인하면 다시 부르지 않는다
      asxErr: '',              // 사람이 읽을 한국어 한 줄(서버 error 그대로)
      asxBrief: null,          // content_brief 결과
      asxPrec: null,           // verdict_precedents 결과
      asxDis: null,            // reviewer_dissent 결과
      asxKey: '',              // 마지막으로 불러온 (hash|stage) · 재호출 판단 기준

      // 골드 문항(가상 검증 행 · gold: · goldf:)에는 보조를 붙이지 않는다.
      asxGold(c) { return /^gold/.test(String((c && c.hash) || '')); },
      // 보조를 붙일 화면인가: 기초 검수 상세 · 골드 아님. 최종검수(편입·제외)는 다른 결정이라 제외.
      asxAvail() {
        const d = this.detail;
        return !!(d && d.hash) && !this.finalMode && !this.asxGold(d);
      },
      // 판정 전/후는 화면 상태에서 정한다 — 내 표(myVerdict)가 있으면 판정을 낸 것.
      // 팀 합의(fb.verdict)가 아니라 내 표를 본다(남의 판정으로 내 화면이 after 가 되면 안 된다).
      asxStage() { return (this.detail && this.myVerdict(this.detail.fb)) ? 'after' : 'before'; },

      asxToggle() {
        this.asxOpen = !this.asxOpen;
        if (this.asxOpen) this.asxLoad();
      },
      // x-effect 대상(마크업 루트). 열려 있는 동안 콘텐츠가 바뀌거나 판정이 나면
      // 그 단계로 다시 부른다. 접혀 있으면 아무것도 하지 않는다(원칙 2).
      // asxKey 를 읽고 쓰지만 값이 같아지면 Alpine 이 더 이상 재실행하지 않아 멈춘다.
      asxWatch() {
        if (!this.asxOpen || this.asxOff) return;
        const d = this.detail;
        if (!this.asxAvail()) return;
        if (this.asxKey === d.hash + '|' + this.asxStage()) return;
        this.asxLoad();
      },
      asxReset() { this.asxBrief = null; this.asxPrec = null; this.asxDis = null; this.asxErr = ''; },

      async asxLoad() {
        if (this.asxOff || !this.asxAvail()) return;
        const hash = this.detail.hash;
        const stage = this.asxStage();
        this.asxKey = hash + '|' + stage;      // 중복 호출 차단은 먼저(응답 대기 중 재진입 방지)
        this.asxBusy = true; this.asxErr = '';
        try {
          const [brief, prec, dis] = await Promise.all([
            this.asxCall('content_brief', { hash: hash, stage: stage }),
            this.asxCall('verdict_precedents', { hash: hash, limit: 5 }),
            this.asxCall('reviewer_dissent', { hash: hash }),
          ]);
          // 응답을 기다리는 사이 다른 콘텐츠로 넘어갔으면 버린다(잔상 방지)
          if (!this.detail || this.detail.hash !== hash) return;
          this.asxBrief = brief; this.asxPrec = prec; this.asxDis = dis;
        } finally { this.asxBusy = false; }
      },

      // 도구 호출 공통. 경계 계약: {tool, args} → {ok:true, result} | {ok:false, error}
      async asxCall(tool, args) {
        if (this.asxOff) return null;
        let r = null;
        try {
          r = await this._afetch('/assist', {
            method: 'POST', headers: this._authHeaders(),
            body: JSON.stringify({ tool: tool, args: args }),
          });
        } catch (e) { this.asxErr = '검수 보조를 부르지 못했습니다'; return null; }
        // 아직 라우트가 없는 단계(404) · 조용히 비활성만 하고 토스트는 띄우지 않는다
        if (r.status === 404) { this.asxOff = true; this.asxReset(); return null; }
        let d = null;
        try { d = await r.json(); } catch (e) { d = null; }
        if (!d) { this.asxErr = '검수 보조 응답을 읽지 못했습니다'; return null; }
        if (d.ok !== true) { this.asxErr = d.error || '검수 보조를 부르지 못했습니다'; return null; }
        return d.result || null;
      },

      // ── 표시 도우미 ────────────────────────────────────────────────────────
      asxSummary() { return ((this.asxBrief || {}).summary3) || []; },
      asxCriteria() { return ((this.asxBrief || {}).criteria) || []; },
      // 판정 후에만 서버가 싣는다 · 화면에서도 stage 를 한 번 더 확인해 잘못 온 값을 그리지 않는다
      asxSuggest() {
        if (this.asxStage() !== 'after') return [];
        return ((this.asxBrief || {}).suggestions) || [];
      },
      // values 는 초안의 필드값 묶음(형태가 도구 쪽 계약이라 키를 고정하지 않는다).
      // 배열은 가운뎃점으로 펴고 객체는 JSON 그대로 — 화면이 값을 해석해 꾸미지 않는다.
      asxValues() {
        const v = (this.asxBrief || {}).values;
        if (!v || typeof v !== 'object') return [];
        return Object.keys(v).map((k) => {
          const x = v[k];
          const s = Array.isArray(x) ? x.join(' · ')
                  : (x && typeof x === 'object') ? JSON.stringify(x)
                  : (x === null || x === undefined || x === '') ? '' : String(x);
          return { k: k, v: s };
        });
      },
      asxPrecItems() { return ((this.asxPrec || {}).items) || []; },
      asxDisItems() { return ((this.asxDis || {}).items) || []; },
      // 잘렸으면 잘렸다고 쓴다(조용한 절단 금지)
      asxCut(res) {
        if (!res || !res.truncated) return '';
        return '전체 ' + (res.total || 0) + '건 중 ' + (((res.items) || []).length) + '건만 보여줍니다';
      },
      asxVerdictLabel(v) { return v === 'good' ? '정확' : v === 'bad' ? '수정 필요' : v === 'split' ? '의견 갈림' : (v || '·'); },
      asxVerdictClass(v) { return v === 'good' ? 'ds-badge--success' : v === 'bad' ? 'ds-badge--error' : 'ds-badge--reason'; },
}));
