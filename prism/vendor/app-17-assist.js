/* Prism 앱 조각 17 · prismApp 프로퍼티 그룹. 검수 보조 패널(트랙 A · 내부 검수 보조).
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유.
   마크업은 prism/ui/19e-review-assist.html(검수 상세 우측 · 판정 바로 위로 텔레포트).

   ## 이 화면이 지키는 규칙

   보조는 검수를 대신하지 않는다. 품질 측정의 독립성이 깨지면 이 도구가 재는 대상이
   사람이 아니라 모델이 되어 버린다. 그래서 화면 쪽에서 네 가지를 강제한다.

   1. **판정 전에는 콘텐츠 자체에서 나오는 것만 부른다.** 3줄 요약·모델 근거·분류 기준까지다.
      선례와 다른 검수자 의견은 **남이 내린 판정**이라 판정을 낸 뒤에만 부르고, 그 전에는
      화면에 아예 그리지 않는다(숨기는 게 아니라 그리지 않는다 · 호출도 하지 않는다).
      취향 문제가 아니라 측정이 깨지는 문제다 — 프리즘은 검수자끼리의 일치도로 신뢰도를
      계산하는데, B 가 A 의 판정을 보고 정하면 그 일치는 더 이상 독립된 근거가 아니다.
      지표가 조용히 부풀고, 부풀었다는 걸 알 방법이 없다.
   2. **stage 는 화면 상태에서 정한다.** 내 표(myVerdict)가 있으면 after · 없으면 before.
      팀 합의(fb.verdict)가 아니라 내 표를 본다 — 남이 먼저 판정했다고 내 화면이 '판정 후'가
      되면 그 순간 측정이 샌다. 서버가 되돌려 준 stage 가 보낸 것과 다르면 화면에 알린다.
   3. **기본은 접힘이고, 펼 때만 부른다.** 호출 비용도 있지만 더 큰 이유는 주의 분산이다.
      보조를 켜지 않아도 검수는 그대로 된다(패널이 통째로 없어도 판정 UI 는 무관).
   4. **골드 문항이 화면에서 티가 나면 안 된다.** 골드는 검수자 신뢰도를 재는 장치라
      보조가 붙으면 측정이 사라진다. 그런데 **막는 방식이 골드를 알려 주면 그것도 측정이
      사라진다** — 패널이 골드에서만 사라지면 검수자가 "이건 골드구나"를 배운다.
      그래서 패널은 모든 콘텐츠에 똑같이 붙이고, 골드는 서버를 부르지 않는다.

   ## 요약·기준을 서버에서 받지 않고 화면 값으로 만드는 이유(중요)

   골드 문항은 큐가 **등급을 일부러 뒤집어** 보여준다(reviewops._inject_gold · 해시 홀짝으로
   대략 절반). 화면에는 뒤집힌 값이 그려지는데 서버 도구는 같은 해시의 **저장된 참값**을 읽는다.
   그래서 서버가 만든 요약을 그대로 그리면 "화면은 G인데 요약은 R" 이 되어, 브리핑만 열면
   뒤집힌 문항을 100% 골라낼 수 있다. 정답이 그대로 새는 것이다.

   서버가 화면과 똑같이 뒤집어 답하게 하는 방법도 있지만, 그러면 뒤집기 규칙이 두 곳에
   생기고 나중에 한쪽만 바뀌면 그 어긋남이 다시 오라클이 된다.

   그래서 이 패널은 다음 불변식을 지킨다.

     **패널에 그리는 값은 (가) 이미 화면에 그려진 값 또는 (나) 콘텐츠와 무관한 공용 사전에서
     만들어진다. 콘텐츠 해시로 서버가 만든 텍스트는 그리지 않는다.**

   - 3줄 요약 = detail(화면 값)로 조립 · 골드는 뒤집힌 값 그대로라 화면과 항상 일치한다
   - 분류 기준 = /dict 의 정의문(INTENT_VALUE_DEFS · QUALITY_METAS)에서 화면에 걸린 값만 추림.
     서버 도구 get_taxonomy 와 같은 원천이라 따로 부를 필요가 없다(해시도 보내지 않는다).
   - 서버에서 받는 것은 evidence(있으면) · stage 확인 · 교정 사례 · 선례 · 다른 검수자 의견뿐이고,
     이것들은 골드에서 비어도 평범한 콘텐츠에서 흔히 비는 값이라 신호가 되지 않는다.
     (evidence 는 2026-08-12 신설 필드라 운영 데이터 대부분이 비어 있다)

   ## 없는 건 없다고 말한다

   운영 데이터 상당수가 근거 필드 신설 이전이라 실제로 비어 있다. has_evidence:false 면
   "저장된 근거 없음"을 그대로 보여주고 빈 자리를 채우지 않는다. 목록이 잘렸으면
   truncated 를 화면에 띄운다 — 조용한 절단은 "다 봤다"로 읽혀 판단을 그르친다.
   수정 제안은 같은 교정이 2건 이상 쌓인 필드에만 붙어서 대체로 비어 있다. 비면 비었다고 둔다.

   ## 백엔드가 없어도 화면이 깨지지 않는다

   POST /assist 가 404 면 보조 영역만 조용히 비활성하고 토스트도 띄우지 않는다(검수 흐름에
   끼어들지 않는다). 한 번 404 를 보면 다시 부르지 않는다. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({

      asxOpen: false,          // 펼침 여부(기본 접힘 · 검수자가 열 때만 호출)
      asxBusy: false,
      asxOff: false,           // /assist 미제공(404) · 한 번 확인하면 다시 부르지 않는다
      asxErr: '',              // 사람이 읽을 한국어 한 줄(서버 error 그대로)
      asxBrief: null,          // content_brief 결과
      asxPrec: null,           // verdict_precedents 결과(판정 후에만)
      asxDis: null,            // reviewer_dissent 결과(판정 후에만)
      asxSent: '',             // 마지막으로 보낸 stage · 서버 echo 와 대조한다
      asxKey: '',              // 마지막으로 불러온 (hash|stage) · 재호출 판단 기준

      // 골드 문항(가상 검증 행 · gold: · goldf:) 판별. 패널을 없애는 데 쓰지 않는다 —
      // 없애면 그 자체가 골드 신호다. 서버를 부르지 않고 빈 상태로 그리는 데만 쓴다.
      asxGold(c) { return /^gold/.test(String((c && c.hash) || '')); },
      // 보조를 붙일 화면인가: 기초 검수 상세. 최종검수(편입·제외)는 다른 결정이라 제외.
      // 골드는 여기서 거르지 않는다(패널 유무가 골드를 알려주면 안 된다).
      asxAvail() {
        const d = this.detail;
        return !!(d && d.hash) && !this.finalMode;
      },
      // 판정 전/후는 화면 상태에서 정한다 — 내 표(myVerdict)가 있으면 판정을 낸 것.
      asxStage() { return (this.detail && this.myVerdict(this.detail.fb)) ? 'after' : 'before'; },
      asxAfter() { return this.asxStage() === 'after'; },
      // 서버가 실제로 해석한 단계(없으면 화면 판단). 모르는 stage 는 서버가 before 로 수렴시킨다.
      asxEff() { return ((this.asxBrief || {}).stage) || this.asxStage(); },
      // 보낸 stage 와 서버가 처리한 stage 가 다르면 그 사실을 화면이 알아야 한다
      // (오타·구버전 클라이언트가 조용히 before 로 떨어지는 것을 잡는 장치).
      asxStageEcho() {
        const got = (this.asxBrief || {}).stage;
        return (got && this.asxSent && got !== this.asxSent) ? got : '';
      },
      asxStageLabel(s) { return s === 'after' ? '판정 후' : '판정 전'; },
      // 수정 제안 영역: 화면 판단과 서버 판단이 모두 '판정 후'일 때만 그린다
      asxSuggestOn() { return this.asxAfter() && this.asxEff() === 'after'; },

      asxToggle() {
        this.asxOpen = !this.asxOpen;
        if (this.asxOpen) this.asxLoad();
      },
      // x-effect 대상(마크업 루트). 열려 있는 동안 콘텐츠가 바뀌거나 판정이 나면
      // 그 단계로 다시 부른다. 접혀 있으면 아무것도 하지 않는다(규칙 3).
      // asxKey 를 읽고 쓰지만 값이 같아지면 Alpine 이 더 이상 재실행하지 않아 멈춘다.
      asxWatch() {
        if (!this.asxOpen || this.asxOff) return;
        const d = this.detail;
        if (!this.asxAvail()) return;
        if (this.asxKey === d.hash + '|' + this.asxStage()) return;
        this.asxLoad();
      },
      _asxEnv() { return { items: [], total: 0, truncated: false }; },
      asxReset() { this.asxBrief = null; this.asxPrec = null; this.asxDis = null; this.asxErr = ''; },

      async asxLoad() {
        if (this.asxOff || !this.asxAvail()) return;
        const hash = this.detail.hash;
        const stage = this.asxStage();
        this.asxKey = hash + '|' + stage;      // 중복 호출 차단은 먼저(응답 대기 중 재진입 방지)
        this.asxSent = stage;
        this.asxErr = '';
        // 골드: 서버를 부르지 않는다. 부르면 (가) 도구가 골드라고 거절해 그 문구가 뜨거나
        // (나) 거절하지 않으면 뒤집기 전 참값이 내려와 정답이 샌다. 둘 다 골드를 알려 준다.
        // 요약·기준은 어차피 화면 값으로 만드니, 여기서 비는 건 근거·교정 사례·선례뿐이고
        // 그건 평범한 콘텐츠에서도 흔히 비는 값이다.
        if (this.asxGold(this.detail)) {
          this.asxBrief = { stage: stage, evidence: null, has_evidence: false, suggestions: [] };
          this.asxPrec = this._asxEnv(); this.asxDis = this._asxEnv();
          return;
        }
        this.asxBusy = true;
        try {
          // 판정 전에는 선례·다른 검수자 의견을 부르지 않는다(그리지도 않는다 · 규칙 1)
          const calls = [this.asxCall('content_brief', { hash: hash, stage: stage })];
          if (stage === 'after') {
            calls.push(this.asxCall('verdict_precedents', { hash: hash, limit: 5 }));
            calls.push(this.asxCall('reviewer_dissent', { hash: hash }));
          }
          const [brief, prec, dis] = await Promise.all(calls);
          // 응답을 기다리는 사이 다른 콘텐츠로 넘어갔으면 버린다(잔상 방지)
          if (!this.detail || this.detail.hash !== hash) return;
          this.asxBrief = brief;
          this.asxPrec = prec || this._asxEnv();
          this.asxDis = dis || this._asxEnv();
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
      // 3줄 요약: 화면에 그려진 값(detail)만으로 만든다. 서버가 해시로 만든 요약을 쓰지
      // 않는 이유는 파일 상단 주석 참고 — 골드는 큐가 등급을 뒤집어 보여주므로 서버 요약과
      // 화면이 어긋나고, 그 어긋남이 곧 정답 유출이다. 모든 콘텐츠가 이 한 경로를 쓴다.
      asxSummary() {
        const d = this.detail;
        if (!d) return [];
        const cat = (d.category || []).map((c) => this.catKo(c)).join(' · ') || '카테고리 미부여';
        const rs = (d.reasons || []).map((r) => this.reasonBoth(r)).join(', ') || '지적된 품질 사유 없음';
        const it = (d.intent || []).join(' · ') || '없음';
        const en = (d.entities || []).slice(0, 5).join(' · ') || '없음';
        return [
          (d.service || '서비스 미상') + ' · ' + (d.title || '(제목 없음)') + ' · ' + cat,
          '모델 초안: 등급 ' + (d.grade || '미상') + ' · ' + rs,
          '인텐트 ' + it + ' · 개체 ' + en,
        ];
      },
      // 이 콘텐츠에 걸린 분류 기준 발췌. 정의문 원천은 /dict 의 INTENT_VALUE_DEFS ·
      // QUALITY_METAS 로 서버 도구(get_taxonomy)와 같은 하나뿐이라 따로 부르지 않는다.
      // 고르는 기준도 화면에 그려진 인텐트·품질 사유라 콘텐츠 해시가 서버로 가지 않는다.
      asxCriteria() {
        const d = this.detail;
        if (!d) return [];
        const idefs = this.INTENT_DEF || {};                 // 게터가 /dict 지연 로드까지 처리
        const dd = this.dictData || {};
        const qm = dd.qualityMetas || {}, qn = dd.qualityNames || {};
        const out = [];
        (d.intent || []).forEach((k) => { if (idefs[k]) out.push({ key: k, desc: idefs[k] }); });
        (d.reasons || []).forEach((k) => { if (qm[k]) out.push({ key: qn[k] || k, desc: qm[k] }); });
        return out;
      },
      // 판정 후에만 서버가 싣는다 · 화면에서도 stage 를 한 번 더 확인해 잘못 온 값을 그리지 않는다
      asxSuggest() {
        if (!this.asxSuggestOn()) return [];
        return ((this.asxBrief || {}).suggestions) || [];
      },
      // content_brief.values(초안 필드값)는 그리지 않는다. 검수 상세가 바로 옆에 이미 같은 값을
      // 배지로 그리고 있어 중복이고, 골드에서는 저장된 참값이라 뒤집힌 화면과 어긋난다.
      asxPrecItems() { return ((this.asxPrec || {}).items) || []; },
      asxDisItems() { return ((this.asxDis || {}).items) || []; },
      // 잘렸으면 잘렸다고 쓴다(조용한 절단 금지) · 선례·다른 검수자 의견 공통 봉투
      asxCut(res) {
        if (!res || !res.truncated) return '';
        return '전체 ' + (res.total || 0) + '건 중 ' + (((res.items) || []).length) + '건만 보여줍니다';
      },
      // 선례에 참여한 검수 인원. 몇 사람이 그렇게 봤는지가 무게를 다는 근거라 함께 보여 준다.
      // (1인 판정은 선례로 내려오지 않는다 · 값이 없으면 지어내지 않고 비워 둔다)
      asxWho(n) { return n ? (n + '인 일치') : ''; },
      asxVerdictLabel(v) { return v === 'good' ? '정확' : v === 'bad' ? '수정 필요' : v === 'split' ? '의견 갈림' : (v || '·'); },
      asxVerdictClass(v) { return v === 'good' ? 'ds-badge--success' : v === 'bad' ? 'ds-badge--error' : 'ds-badge--reason'; },
}));
