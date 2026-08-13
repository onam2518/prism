/* Prism 앱 조각 17 · prismApp 프로퍼티 그룹. 검수 보조 대화창(트랙 A · 내부 검수 보조).
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유.
   마크업은 prism/ui/19e-review-assist.html(화면 우하단 플로팅 버튼 + 대화창 · body 로 텔레포트).

   2026-08-13: 검수 상세 안의 접이식 패널에서 **대화창**으로 바꿨다. 검수 상세는 이미 빽빽해서
   패널이 판정 UI 를 밀어냈고, 콘텐츠를 옮길 때마다 다시 펴야 했다. 대화창은 화면 우하단에
   떠 있어 상세 레이아웃을 건드리지 않고, 열어 두면 콘텐츠를 옮겨도 그대로 따라온다.

   ## 이 화면이 지키는 규칙

   보조는 검수를 대신하지 않는다. 품질 측정의 독립성이 깨지면 이 도구가 재는 대상이
   사람이 아니라 모델이 되어 버린다. 그래서 화면 쪽에서 다섯 가지를 강제한다.

   1. **판정 전에는 콘텐츠 자체에서 나오는 것만 부른다.** 3줄 요약·모델 근거·분류 기준·정책
      예시·개체 사전까지다. 선례와 다른 검수자 의견, 교정 사례는 **남이 내린 판정**이라
      판정을 낸 뒤에만 부른다. 취향 문제가 아니라 측정이 깨지는 문제다 — 프리즘은 검수자끼리의
      일치도로 신뢰도를 계산하는데, B 가 A 의 판정을 보고 정하면 그 일치는 더 이상 독립된
      근거가 아니다. 지표가 조용히 부풀고, 부풀었다는 걸 알 방법이 없다.
   2. **판정 전에 못 쓰는 칩은 숨기지 않고 잠근다.** 종전 패널은 아예 그리지 않았다(x-if).
      대화창에서는 칩이 곧 목록이라 없으면 "왜 없지" 가 되고, 잠겨 있으면 "판정을 먼저 내라"
      가 화면에 드러난다. **잠김은 판정 단계로만 정해진다** — 콘텐츠를 보지 않으므로 골드든
      아니든 칩 구성·문구가 한 글자도 다르지 않다. 호출은 여전히 막는다(잠긴 칩은 눌러도
      아무 일도 일어나지 않고, 서버도 두 도구를 stage 로 거절한다 · after_only).
   3. **stage 는 화면 상태에서 정한다.** 내 표(myVerdict)가 있으면 after · 없으면 before.
      팀 합의(fb.verdict)가 아니라 내 표를 본다 — 남이 먼저 판정했다고 내 화면이 '판정 후'가
      되면 그 순간 측정이 샌다. 서버가 되돌려 준 stage 가 보낸 것과 다르면 말풍선에 적는다.
   4. **칩은 도구를 그대로 부른다(모델을 안 거친다).** 즉시 뜨고, 지어낼 자리가 없다.
      모델이 등장하는 자리는 자유질문(/assist-ask) 하나뿐이고 그것도 판정 뒤에만 열린다.
      자유질문 답변은 **출처가 붙은 문장만 그린다** — 서버가 이미 걸러 보내지만 화면도
      한 번 더 거른다. 대조할 수 없는 문장은 보조가 아니라 또 하나의 추측이다.
   5. **골드 문항이 화면에서 티가 나면 안 된다.** 골드는 검수자 신뢰도를 재는 장치라
      보조가 붙으면 측정이 사라진다. 그런데 **막는 방식이 골드를 알려 주면 그것도 측정이
      사라진다** · 대화창이 골드에서만 사라지거나 칩이 하나 빠지면 검수자가 "이건 골드구나"를
      배운다. 그래서 이 조각에는 **골드 분기가 하나도 없다.** 골드를 안전하게 만드는 일은
      전부 서버가 한다(reviewassist.flip_blind_key · gold 는 밑 콘텐츠로 풀려 평소대로 답한다).

   ## 요약·기준을 서버에서 받지 않고 화면 값으로 만드는 이유(중요)

   골드 문항은 큐가 **카테고리 한 자리를 일부러 뒤집어** 보여준다(reviewops.GOLD_FLIP_ELEMENT ·
   해시 홀짝으로 대략 절반 · 2026-08-13 이전에는 등급이었다). 화면에는 뒤집힌 값이 그려지는데
   서버 도구는 같은 콘텐츠의 **저장된 참값**을 읽는다. 서버가 만든 요약을 그대로 그리면 화면과
   요약이 갈리고, 브리핑만 열면 뒤집힌 문항을 골라낼 수 있다.

   서버가 화면과 똑같이 뒤집어 답하게 하는 방법도 있지만, 그러면 뒤집기 규칙이 두 곳에
   생기고 나중에 한쪽만 바뀌면 그 어긋남이 다시 오라클이 된다. 그래서 서버는 뒤집지 않고
   **그 요소를 전 콘텐츠에서 말하지 않는** 쪽을 골랐다(reviewassist.flip_blind_key).

   그래서 이 대화창은 다음 불변식을 지킨다.

     **말풍선에 그리는 값은 (가) 이미 화면에 그려진 값 또는 (나) 콘텐츠와 무관한 공용 사전에서
     만들어진다. 콘텐츠 해시로 서버가 만든 텍스트는 그대로 옮겨 적을 뿐 화면이 다시 가공하지
     않는다.**

   - 3줄 요약 = detail(화면 값)로 조립 · 골드는 뒤집힌 값 그대로라 화면과 항상 일치한다
   - 분류 기준 = /dict 의 정의문(INTENT_VALUE_DEFS · QUALITY_METAS)에서 화면에 걸린 값만 추림.
     서버 도구 get_taxonomy 와 같은 원천이라 따로 부를 필요가 없다(해시도 보내지 않는다).
   - 정책 예시·개체 사전 = 공용 사전 도구(get_examples · lookup_entity). **해시를 보내지 않고**
     화면에 그려진 값으로 묻는다 — 골드에서 물어도 뒤집힌 값 기준이라 화면과 어긋나지 않는다.
   - 서버에서 받는 것은 evidence · 교정 사례 · 선례 · 다른 검수자 의견 · 자유질문 답변뿐이고,
     골드에서도 **평범한 콘텐츠와 완전히 같은 응답**이 온다(서버가 그렇게 만든다).

   ## 없는 건 없다고 말한다

   운영 데이터 상당수가 근거 필드 신설 이전이라 실제로 비어 있다. has_evidence:false 면
   "저장된 근거 없음"을 그대로 보여주고 빈 자리를 채우지 않는다. 목록이 잘렸으면
   truncated 를 화면에 띄운다 — 조용한 절단은 "다 봤다"로 읽혀 판단을 그르친다.
   교정 사례는 같은 교정이 2건 이상 쌓인 필드에만 붙어서 대체로 비어 있다. 비면 비었다고 둔다.

   ## 백엔드가 없어도 화면이 깨지지 않는다

   POST /assist 가 404 면 보조를 조용히 접는다(토스트도 띄우지 않는다 · 검수 흐름에
   끼어들지 않는다). 한 번 404 를 보면 다시 부르지 않는다. 자유질문(/assist-ask)만 없으면
   칩은 그대로 쓰고 입력칸만 접는다. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({

      asxOpen: false,          // 대화창 펼침 여부(기본 닫힘 · 열면 콘텐츠를 옮겨도 따라온다)
      asxOff: false,           // /assist 미제공(404) · 한 번 확인하면 다시 부르지 않는다
      asxAskOff: false,        // /assist-ask 미제공(404) · 칩은 그대로 두고 입력칸만 접는다
      asxBusy: false,          // 칩 답을 기다리는 중
      asxAsking: false,        // 자유질문 답을 기다리는 중
      asxErr: '',              // 사람이 읽을 한국어 한 줄(서버 error 그대로)
      asxMsgs: [],             // 대화 내용 · 콘텐츠가 바뀌면 비운다(열림 상태는 유지)
      asxQ: '',                // 자유질문 입력
      asxKey: '',              // 지금 대화가 붙어 있는 콘텐츠 해시
      asxBrief: null,          // content_brief 결과(근거·교정 사례가 같이 온다)
      asxBriefKey: '',         // 그 결과의 (hash|stage) · 단계가 바뀌면 다시 부른다
      asxSent: '',             // 마지막으로 보낸 stage · 서버 echo 와 대조한다
      asxPos: null, asxDrag: null,   // 드래그 이동 · 자리 기억(정책 팔레트와 같은 관례)
      _asxRestored: false,

      /* 칩 목록은 **고정이다.** 콘텐츠에 따라 늘거나 줄면 그 차이가 곧 콘텐츠 힌트다.
         after:true = 판정 후 전용. 판정 전에는 지우지 않고 **잠근 채로** 보여 준다(규칙 2).
         tip 은 칩마다 붙박이 문장이라 콘텐츠를 타지 않는다(잠김 문구도 단계만 본다). */
      asxChips: [
        { id: 'summary', label: '3줄로 정리해 줘', after: false,
          tip: '지금 화면에 있는 값을 세 줄로 묶어 드려요' },
        { id: 'evidence', label: '모델은 왜 이렇게 봤어', after: false,
          tip: '모델이 저장해 둔 판정 근거를 그대로 보여 드려요 · 없으면 없다고 합니다' },
        { id: 'criteria', label: '이 값들의 기준이 뭐야', after: false,
          tip: '화면에 걸린 인텐트·품질 사유의 기준 문장이에요' },
        { id: 'examples', label: '정책 예시 보여줘', after: false,
          tip: '화면에 걸린 분류값의 예시예요 · 예시는 아직 초안이에요' },
        { id: 'entity', label: '이 개체 사전에 있어', after: false,
          tip: '화면에 잡힌 개체가 개체 사전에 있는지 찾아 드려요' },
        { id: 'prec', label: '비슷한 선례', after: true,
          tip: '비슷한 과거 판정과 몇 사람이 그렇게 봤는지 보여 드려요' },
        { id: 'dissent', label: '다른 검수자 의견', after: true,
          tip: '내 판정은 빼고 다른 검수자 의견을 세 줄로 묶어 드려요 · 이름과 사유 원문은 안 보여 드려요' },
        { id: 'sugg', label: '비슷한 교정 사례', after: true,
          tip: '같은 값을 같게 고친 과거 교정이 몇 건인지 세어 드려요' },
      ],
      // 잠긴 칩에 붙는 문구. **단계만 본다** — 콘텐츠에 대해서는 아무것도 말하지 않는다.
      ASX_LOCK_TIP: '판정을 낸 뒤에 볼 수 있어요 · 남이 내린 판정을 먼저 보면 검수 결과를 그대로 재기 어려워요',

      // 이 조각에는 골드 분기가 **없다**. 분기가 하나라도 있으면 언젠가 그 분기가 화면 차이로
      // 새고, 검수자가 그것으로 골드를 배운다. 골드를 안전하게 만드는 일은 전부 서버가 한다.
      // 보조를 붙일 화면인가: 기초 검수 상세. 최종검수(편입·제외)는 다른 결정이라 제외.
      asxAvail() {
        const d = this.detail;
        return !!(d && d.hash) && !this.finalMode;
      },
      // 판정 전/후는 화면 상태에서 정한다 — 내 표(myVerdict)가 있으면 판정을 낸 것.
      asxStage() { return (this.detail && this.myVerdict(this.detail.fb)) ? 'after' : 'before'; },
      asxAfter() { return this.asxStage() === 'after'; },
      asxStageLabel(s) { return s === 'after' ? '판정 후' : '판정 전'; },
      // 잠김 판정. 칩의 after 플래그와 지금 단계만 본다(콘텐츠를 보지 않는다).
      asxLocked(c) { return !!(c && c.after) && !this.asxAfter(); },
      asxChipTip(c) { return this.asxLocked(c) ? this.ASX_LOCK_TIP : ((c && c.tip) || ''); },
      // 서버가 실제로 해석한 단계(없으면 화면 판단). 모르는 stage 는 서버가 before 로 수렴시킨다.
      asxEff() { return ((this.asxBrief || {}).stage) || this.asxStage(); },
      // 보낸 stage 와 서버가 처리한 stage 가 다르면 그 사실을 화면이 알아야 한다
      // (오타·구버전 클라이언트가 조용히 before 로 떨어지는 것을 잡는 장치).
      asxStageEcho() {
        const got = (this.asxBrief || {}).stage;
        return (got && this.asxSent && got !== this.asxSent) ? got : '';
      },
      asxEchoLine() {
        const got = this.asxStageEcho();
        return got ? ('요청한 단계 ' + this.asxStageLabel(this.asxSent) + ' · 서버가 처리한 단계 '
          + this.asxStageLabel(got) + ' · 아래 내용은 서버가 처리한 단계 기준입니다') : '';
      },
      // 교정 사례: 화면 판단과 서버 판단이 모두 '판정 후'일 때만 그린다
      asxSuggestOn() { return this.asxAfter() && this.asxEff() === 'after'; },

      // ── 열고 닫기 · 자리 기억(정책 팔레트 polfab/polpal 과 같은 관례) ──────────
      asxToggle() {
        if (!this._asxRestored) { this._asxRestored = true; this.asxRestore(); }
        this.asxOpen = !this.asxOpen;
        if (this.asxOpen) this.asxGreet();
        this.asxSave();
      },
      asxRestore() {
        try {
          const p = JSON.parse(localStorage.getItem('prismAsxChat') || 'null');
          if (p) this.asxPos = p.pos || null;
        } catch (e) {}
      },
      asxSave() {
        try { localStorage.setItem('prismAsxChat', JSON.stringify({ pos: this.asxPos })); } catch (e) {}
      },
      asxStyle() {
        return this.asxPos ? ('left:' + this.asxPos.x + 'px; top:' + this.asxPos.y + 'px; right:auto; bottom:auto;') : '';
      },
      asxDragStart(e) {
        if (e.target && e.target.closest && e.target.closest('button')) return;   // 닫기 버튼은 드래그 제외
        const box = this.$refs.asxchat;
        if (!box) return;
        const r = box.getBoundingClientRect();
        this.asxDrag = { dx: e.clientX - r.left, dy: e.clientY - r.top };
        const move = (ev) => {
          if (!this.asxDrag) return;
          const x = Math.min(Math.max(ev.clientX - this.asxDrag.dx, 8), window.innerWidth - 120);
          const y = Math.min(Math.max(ev.clientY - this.asxDrag.dy, 8), window.innerHeight - 48);
          this.asxPos = { x, y };
        };
        const up = () => { this.asxDrag = null; this.asxSave(); window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', up);
      },

      // x-effect 대상(마크업 루트). **열어 두면 콘텐츠가 바뀔 때 따라간다.** 사용자가 매번
      // 다시 여는 것을 복잡해했고 종전 패널도 그렇게 동작했다. 대화 내용은 다른 콘텐츠 이야기라
      // 비우고, 열림 상태는 유지한다. asxKey 를 읽고 쓰지만 값이 같아지면 재실행이 멈춘다.
      asxWatch() {
        if (!this.asxAvail()) return;
        const h = this.detail.hash;
        if (this.asxKey === h) return;
        this.asxKey = h;
        this.asxReset();
        if (this.asxOpen) this.asxGreet();
      },
      asxReset() {
        this.asxMsgs = []; this.asxBrief = null; this.asxBriefKey = '';
        this.asxErr = ''; this.asxQ = '';
      },
      // 첫 화면 = 3줄 요약 하나. 화면 값으로만 만들므로 서버를 부르지 않고, 콘텐츠를 옮기면
      // 누르지 않아도 새 콘텐츠 요약이 선다(종전 패널이 열려 있을 때 하던 일).
      asxGreet() {
        if (!this.asxAvail() || this.asxMsgs.length) return;
        this.asxPush(Object.assign({ role: 'bot' }, this.asxSummaryMsg()));
      },
      asxPush(m) {
        this.asxMsgs = this.asxMsgs.concat([m]);
        this.$nextTick(() => {
          const b = this.$refs.asxbody;
          if (b) b.scrollTop = b.scrollHeight;
        });
      },

      // ── 칩 = 도구 호출(모델을 안 거친다 · 즉시 뜬다) ──────────────────────────
      async asxChip(c) {
        if (this.asxOff || this.asxBusy || !this.asxAvail()) return;
        if (this.asxLocked(c)) return;                 // 잠긴 칩은 눌러도 아무 일도 일어나지 않는다
        const hash = this.detail.hash;
        this.asxPush({ role: 'me', text: c.label });
        this.asxErr = '';
        this.asxBusy = true;
        try {
          const m = await this.asxAnswer(c.id);
          // 답을 기다리는 사이 다른 콘텐츠로 넘어갔으면 버린다(잔상 방지)
          if (!this.detail || this.detail.hash !== hash) return;
          if (this.asxErr) { this.asxPush({ role: 'bot', kind: 'text', text: this.asxErr }); this.asxErr = ''; return; }
          if (m) this.asxPush(Object.assign({ role: 'bot' }, m));
        } finally { this.asxBusy = false; }
      },

      async asxAnswer(id) {
        if (id === 'summary') return this.asxSummaryMsg();
        if (id === 'criteria') {
          return { kind: 'kv', title: '이 콘텐츠에 걸린 분류 기준',
                   items: this.asxCriteria(), empty: '기준 없음' };
        }
        if (id === 'evidence') {
          const b = await this.asxLoadBrief();
          if (!b) return null;
          return { kind: 'lines', title: '모델 판정 근거',
                   lines: b.has_evidence ? [String(b.evidence || '')] : [],
                   empty: '저장된 근거 없음', echo: this.asxEchoLine() };
        }
        if (id === 'sugg') {
          const b = await this.asxLoadBrief();
          if (!b) return null;
          return { kind: 'sug', title: '비슷한 교정 사례', items: this.asxSuggest(),
                   empty: '비슷한 교정 사례 없음 · 같은 교정이 2건 이상 쌓인 필드만 올라옵니다',
                   echo: this.asxEchoLine() };
        }
        if (id === 'examples') return await this.asxExamples();
        if (id === 'entity') return await this.asxEntities();
        if (id === 'prec') {
          const r = await this.asxCall('verdict_precedents',
            { hash: this.detail.hash, stage: this.asxStage(), limit: 5 });
          if (!r) return null;
          return { kind: 'prec', title: '비슷한 선례', items: (r.items || []),
                   empty: '선례 없음', cut: this.asxCut(r) };
        }
        /* 다른 검수자 의견: 사람별 나열이 아니라 **말풍선 하나에 3줄**.
           사람 이름과 사유 원문을 사람 수만큼 그리면 읽는 데 시간이 걸리고, 특정 사람의
           문장에 판단이 끌려간다. 그래서 서버가 조립한 세 줄만 받아 **그대로** 쓴다 —
           자르거나 이어 붙이거나 순서를 바꾸면 그 순간 무엇이 왜 그렇게 보이는지가 두 곳으로
           갈린다. 이름·사유는 응답에서 빠지지만 남아 오더라도 그리지 않는다. 대신 몇 명 것을
           모았는지(n)는 남긴다 — 사람이 사라지면 무게를 가늠할 근거까지 사라진다. */
        if (id === 'dissent') {
          const r = await this.asxCall('reviewer_dissent',
            { hash: this.detail.hash, stage: this.asxStage() });
          if (!r) return null;
          return { kind: 'lines', title: '다른 검수자 의견', lines: (r.lines || []),
                   n: Number(r.n || 0) || 0, split: !!r.split, cut: this.asxDisCut(r),
                   empty: '다른 의견 없음' };
        }
        return null;
      },

      asxSummaryMsg() {
        return { kind: 'lines', title: '3줄 요약', lines: this.asxSummary(), empty: '요약 없음' };
      },

      // content_brief 는 근거와 교정 사례를 함께 준다. 같은 (해시|단계)면 다시 부르지 않는다.
      async asxLoadBrief() {
        const hash = this.detail.hash;
        const stage = this.asxStage();
        const key = hash + '|' + stage;
        if (this.asxBrief && this.asxBriefKey === key) return this.asxBrief;
        this.asxSent = stage;
        const r = await this.asxCall('content_brief', { hash: hash, stage: stage });
        if (r) { this.asxBrief = r; this.asxBriefKey = key; }
        return r;
      },

      // 정책 예시: **화면에 걸린 분류값**으로 묻는다(해시를 보내지 않는다). 골드는 화면이
      // 뒤집힌 카테고리를 그리고 있으므로 그 값으로 묻게 되어 화면과 어긋날 자리가 없다.
      async asxExamples() {
        const d = this.detail || {};
        const svc = d.service || '';
        const wants = [['intent', (d.intent || []).slice(0, 4)],
                       ['category', (d.category || []).slice(0, 4)]];
        const asked = wants.filter((w) => w[1].length);
        if (!asked.length) {
          return { kind: 'kv', title: '정책 예시', items: [], empty: '화면에 걸린 분류값이 없습니다' };
        }
        const rs = await Promise.all(asked.map((w) =>
          this.asxCall('get_examples', { kind: w[0], values: w[1], service: svc })));
        const items = [];
        let note = '';
        rs.forEach((r) => {
          if (!r) return;
          note = note || String(r.draft_note || '');   // 초안 표시는 서버 문장을 그대로 옮긴다
          (r.items || []).forEach((it) => {
            items.push({ key: it.label || it.key || '',
                         desc: it.has_example ? String(it.example || '') : String(it.note || '') });
          });
        });
        return { kind: 'kv', title: '정책 예시', items: items, note: note, empty: '등록된 예시가 없습니다' };
      },

      // 개체 사전: 화면에 잡힌 개체 이름으로만 묻는다(해시를 보내지 않는다).
      async asxEntities() {
        const names = ((this.detail || {}).entities || []).slice(0, 3).map((e) => String(e || '')).filter(Boolean);
        if (!names.length) {
          return { kind: 'kv', title: '개체 사전', items: [], empty: '화면에 잡힌 개체가 없습니다' };
        }
        const rs = await Promise.all(names.map((n) => this.asxCall('lookup_entity', { name: n, limit: 3 })));
        const items = names.map((n, i) => {
          const hit = (((rs[i] || {}).items) || [])[0];
          return { key: n, desc: hit ? this.asxEntLine(hit) : '사전에 없습니다' };
        });
        return { kind: 'kv', title: '개체 사전', items: items, empty: '' };
      },
      asxEntLine(it) {
        const bits = [String(it.name || ''), String(it.type || ''), String(it.status || '')].filter(Boolean);
        const al = (it.aliases || []).filter(Boolean);
        return '사전에 있습니다 · ' + bits.join(' · ') + (al.length ? ' · 다른 이름 ' + al.join(', ') : '');
      },

      // 도구 호출 공통. 경계 계약: {tool, args} → {ok:true, result} | {ok:false, error}
      async asxCall(tool, args) {
        if (this.asxOff) return null;
        let r = null;
        try {
          r = await this._afetch('/assist', {
            method: 'POST', headers: this._authHeaders(),
            // reviewer = 내 이름. '다른 검수자 의견' 에서 내 판정을 빼는 데만 쓴다.
            // 운영(supabase)에서는 서버가 로그인 uid 로 덮으므로 이 값은 무시된다(사칭 방지) ·
            // 로그인이 없는 로컬에서만 쓰인다(검수 저장 경로와 같은 규칙).
            body: JSON.stringify({ tool: tool, args: args, reviewer: this.reviewer || '' }),
          });
        } catch (e) { this.asxErr = '검수 보조를 부르지 못했습니다'; return null; }
        // 아직 라우트가 없는 단계(404) · 조용히 접기만 하고 토스트는 띄우지 않는다
        if (r.status === 404) { this.asxOff = true; this.asxOpen = false; this.asxReset(); return null; }
        let d = null;
        try { d = await r.json(); } catch (e) { d = null; }
        if (!d) { this.asxErr = '검수 보조 응답을 읽지 못했습니다'; return null; }
        if (d.ok !== true) { this.asxErr = d.error || '검수 보조를 부르지 못했습니다'; return null; }
        return d.result || null;
      },

      // ── 자유질문(모델이 등장하는 유일한 자리 · 판정 뒤에만) ────────────────────
      // 서버 계약: POST /assist-ask {hash, stage, question} → {ok, result{answer[], sources[], …}}
      async asxAsk() {
        const q = String(this.asxQ || '').trim();
        if (!q || this.asxAsking || this.asxAskOff || !this.asxAvail() || !this.asxAfter()) return;
        const hash = this.detail.hash;
        this.asxPush({ role: 'me', text: q });
        this.asxQ = '';
        this.asxAsking = true;
        try {
          let r = null;
          try {
            r = await this._afetch('/assist-ask', {
              method: 'POST', headers: this._authHeaders(),
              body: JSON.stringify({ hash: hash, stage: 'after', question: q,
                                     reviewer: this.reviewer || '' }),
            });
          } catch (e) { this.asxPush({ role: 'bot', kind: 'text', text: '검수 보조를 부르지 못했습니다' }); return; }
          // 라우트가 없으면 입력칸만 조용히 접는다(칩은 그대로 쓴다 · 토스트 없음)
          if (r.status === 404) { this.asxAskOff = true; return; }
          let d = null;
          try { d = await r.json(); } catch (e) { d = null; }
          if (!this.detail || this.detail.hash !== hash) return;   // 그 사이 콘텐츠가 바뀌면 버린다
          if (!d) { this.asxPush({ role: 'bot', kind: 'text', text: '검수 보조 응답을 읽지 못했습니다' }); return; }
          if (d.ok !== true) {
            this.asxPush({ role: 'bot', kind: 'text', text: d.error || '검수 보조를 부르지 못했습니다' });
            return;
          }
          const res = d.result || {};
          this.asxPush({ role: 'bot', kind: 'ans', lines: this.asxAnsLines(res),
                         srcs: (res.sources || []), note: String(res.note || ''),
                         model: String(res.model_label || res.model || ''),
                         truncated: !!res.truncated });
        } finally { this.asxAsking = false; }
      },
      /* **출처 없는 문장은 그리지 않는다.** 서버가 이미 걸러 보내지만(reviewassist._ask_clean)
         화면도 한 번 더 거른다 — 이 기능의 안전장치라 두 겹으로 둔다. 실재하지 않는 id 를
         단 문장, 출처를 안 단 문장은 통째로 사라진다. 검수자가 대조할 수 없는 문장은 보조가
         아니라 또 하나의 추측이고, 그런 문장이 판정 옆에 놓이면 그게 새 오라클이 된다. */
      asxAnsLines(res) {
        const ok = {};
        (((res || {}).sources) || []).forEach((s) => { if (s && s.id) ok[s.id] = s; });
        return ((((res || {}).answer) || [])).map((a) => ({
          text: String((a && a.text) || ''),
          sources: (((a && a.sources) || [])).filter((id) => !!ok[id]),
        })).filter((a) => a.text && a.sources.length);
      },
      asxSrc(m, id) { return ((m || {}).srcs || []).find((s) => s && s.id === id) || null; },
      asxSrcLabel(m, id) { const s = this.asxSrc(m, id); return (s && s.label) || String(id || ''); },
      asxSrcText(m, id) { const s = this.asxSrc(m, id); return (s && s.text) || '자료 본문이 비어 있습니다'; },
      // 어떤 모델이 답했는지 보인다. 설정이 바뀌면 답의 성격도 바뀌는데 화면에 안 적으면
      // 검수자는 그 변화를 알 길이 없다(서버도 같은 값을 롤링 로그에 남긴다).
      asxModelLine(m) { return (m && m.model) ? (m.model + ' 모델이 답했습니다') : ''; },
      asxAskHint() {
        return this.asxAfter() ? '' : '판정을 낸 뒤에 직접 물어볼 수 있어요 · 먼저 판정해 주세요';
      },

      // ── 표시 도우미 ────────────────────────────────────────────────────────
      // 3줄 요약: 화면에 그려진 값(detail)만으로 만든다. 서버가 만든 요약을 쓰지 않는 이유는
      // 파일 상단 주석 참고 · 골드는 큐가 카테고리를 뒤집어 보여주므로 저장값으로 만든 요약과
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
      /* 몇 사람 의견을 모았나. **내 판정은 빠진 수다**(서버가 묻는 사람 본인을 뺀다 ·
         reviewassist.reviewer_dissent). 그냥 'N명' 이라고 쓰면 나를 포함하는지 헷갈리고,
         헷갈린 채로 읽으면 무게를 잘못 단다. 칩 이름이 '다른 검수자 의견' 인 것과 같은 뜻이다. */
      asxDisNote(n) { return '내 판정은 빼고 ' + (Number(n) || 0) + '명 의견을 모았습니다'; },
      // 잘렸으면 잘렸다고 쓴다(조용한 절단 금지) · 선례처럼 목록을 그리는 영역용
      asxCut(res) {
        if (!res || !res.truncated) return '';
        return '전체 ' + (res.total || 0) + '건 중 ' + (((res.items) || []).length) + '건만 보여줍니다';
      },
      // 의견 요약의 truncated 는 선례의 것과 뜻이 다르다. 목록이 잘린 게 아니라
      // **둘째 줄의 지적 요소 나열**이 상한에서 잘린 것이다(의견 자체는 전부 셌다).
      // 그래서 '몇 건 중 몇 건' 이 아니라 요소가 더 있다고 쓴다 — 건수를 쓰면
      // 의견을 일부만 봤다는 뜻으로 읽힌다.
      // 상한 값(서버 DIGEST_ELEM_MAX)은 문구에 넣지 않는다. 두 곳에 두면 한쪽만 바뀐다.
      asxDisCut(res) {
        if (!res || !res.truncated) return '';
        return '지적한 요소가 더 있습니다 · 많이 나온 것부터 적었습니다';
      },
      // 선례에 참여한 검수 인원. 몇 사람이 그렇게 봤는지가 무게를 다는 근거라 함께 보여 준다.
      // (1인 판정은 선례로 내려오지 않는다 · 값이 없으면 지어내지 않고 비워 둔다)
      asxWho(n) { return n ? (n + '인 일치') : ''; },
      asxVerdictLabel(v) { return v === 'good' ? '정확' : v === 'bad' ? '수정 필요' : v === 'split' ? '의견 갈림' : (v || '·'); },
      asxVerdictClass(v) { return v === 'good' ? 'ds-badge--success' : v === 'bad' ? 'ds-badge--error' : 'ds-badge--reason'; },
}));
