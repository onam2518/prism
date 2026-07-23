/* Prism 앱 조각 11 · prismApp 프로퍼티 그룹 — 실험실 · 콘텐츠 에이전트(사용자향 시연 프로토타입).
   동작하는 시연용: 자연어 → 조건 변환 → 미리보기 → 위젯 생성 → 홈 배치·크기·삭제 → 피드백.
   저장은 localStorage(prism_ca) — 서버 의존 없음(실험실 탐구 요소). 설계: docs/CONTENT_DASHBOARD_HANDOFF.md */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({

      // ── 콘텐츠 원천: 실제 등록된 콘텐츠(/raw) · 추출 메타(카테고리·인텐트·엔티티) 그대로 사용 ──
      caPool: [],                          // [{id,t,src,ago,ents,topics,kinds,tone,field}] · caLoad 가 /raw 에서 채움
      caLoading: false, caLoadMsg: '',
      caDict: { ents: [], topics: [], kinds: [], fields: [] },   // 실제 콘텐츠에서 수집한 사전(자연어 매칭용)
      caTone: [
        { k: '깊게', words: ['깊이', '깊게', '심층', '분석', '자세', '기획'] },
        { k: '빠르게', words: ['빠르게', '속보', '짧게', '간단'] },
        { k: '가볍게', words: ['가볍게', '가벼운', '재미', '화제', '흥미'] },
      ],
      // ── 표시 라벨: 화면에는 한글만 · 내부 값(영문 카테고리 등)은 그대로 유지 ──
      caIntentKo: {                        // 인텐트(내부 라벨) → 사용자 말
        '속보·사건 추적': '새로 나온 소식', '심층 분석': '깊이 있는 분석', '의견·논평': '의견·칼럼',
        '흥미·화제': '가벼운 화제', '인물 동정': '인물 이야기', '기획·심층': '기획 기사',
        '분석·해설': '해설', '사건 경과 보도': '사건 진행', '라이프스타일': '생활',
      },
      caLabel(kind, v) {                   // kind: topic|field|kind(내용)|ent
        if (!v) return '';
        if (kind === 'topic' || kind === 'field') {
          const ko = (this.catKo ? this.catKo(v) : v) || v;      // 앱 공통 카테고리 한글 변환 재사용
          return String(ko).split('/')[0].trim();                // 사용자에겐 대분류 한 단계만
        }
        if (kind === 'kind') return this.caIntentKo[v] || v;
        return v;
      },
      // Anchor 카테고리 컬러(도메인 식별색) · semantic Background/Text.Category.*
      caCatKey(v) {
        const s = String(v || '');
        if (/Sport|스포츠/i.test(s)) return 'sports';
        if (/Entertain|연예|Music|Movie/i.test(s)) return 'ent';
        if (/Shopping|쇼핑|Business|Finance|경제/i.test(s)) return 'shop';
        if (/Cafe|카페|Community|커뮤니티/i.test(s)) return 'cafe';
        if (/Hobby|Interest|관심|Travel|Food|Style/i.test(s)) return 'interest';
        return 'news';
      },
      caWidgetCat(w) {
        const c = (w && w.cond) || {};
        return this.caCatKey((c.topics || [])[0] || (c.fields || [])[0] || '');
      },
      caLabels(kind, arr) { return (arr || []).map((v) => this.caLabel(kind, v)); },
      // 인텐트(추출 메타) → 읽는 방식(사용자 말) 대응
      _caGoodEnt(e) {                      // 추출 토막(조사 결합·한 글자)은 위젯 후보에서 제외
        const s = String(e || '').trim();
        if (s.length < 2 || s.length > 20) return false;
        if (/(이|가|은|는|을|를|의|에|에서|으로|와|과|도|만)$/.test(s) && s.length <= 4) return false;
        return true;
      },
      caToneOf(intents) {
        const s = (intents || []).join(' ');
        if (/심층|기획|분석|해설/.test(s)) return '깊게';
        if (/속보|사건|경과/.test(s)) return '빠르게';
        if (/흥미|화제|유머|인물/.test(s)) return '가볍게';
        return '';
      },
      async caLoad(force) {
        if (this.caLoading || (this.caPool.length && !force)) return;
        this.caLoading = true; this.caLoadMsg = '';
        try {
          const r = await this._afetch('/raw?limit=200');
          const j = await r.json();
          const items = (j && j.items) || [];
          const pool = [], eSet = {}, tSet = {}, kSet = {}, fSet = {};
          items.forEach((it, idx) => {
            if (it.grade && it.grade !== 'G') return;             // 사용자에게는 품질 통과분만
            const cats = (it.category || []).map((c) => String(c).split('/')[0].trim()).filter(Boolean);
            const ents = (it.entities || []).map((e) => (typeof e === 'string' ? e : (e && (e.name || e.value)) || '')).filter(Boolean);
            const intents = (it.intent || []).map((x) => String(x)).filter(Boolean);
            const field = cats[0] || '';
            pool.push({ id: it.hash || ('c' + idx), t: it.title || '(제목 없음)',
                        src: it.service || '', ago: '', summary: it.summary || '',
                        url: it.url || '', ents: ents.slice(0, 6), topics: cats,
                        kinds: intents, tone: this.caToneOf(intents), field: field });
            ents.slice(0, 6).forEach((e) => { if (this._caGoodEnt(e)) eSet[e] = 1; });
            cats.forEach((c) => { tSet[c] = 1; fSet[c] = 1; });
            intents.forEach((k) => { kSet[k] = 1; });
          });
          this.caPool = pool;
          this.caDict = { ents: Object.keys(eSet), topics: Object.keys(tSet),
                          kinds: Object.keys(kSet), fields: Object.keys(fSet) };
          this.caLoadMsg = pool.length ? ('등록된 콘텐츠 ' + pool.length + '건을 불러왔어요')
                                       : '등록된 콘텐츠가 없어요 · 콘텐츠 관리에서 추출을 먼저 실행하세요';
        } catch (e) {
          this.caLoadMsg = '콘텐츠를 불러오지 못했어요';
        }
        this.caLoading = false;
      },

      // ── 상태 ──
      caWidgets: [],                       // [{id,name,size,cond:{ents,topics,kinds,excl,tone},pins:[],hidden:[]}]
      caText: '',                          // 사용자가 직접 입력 · 아래 예시 버튼은 등록 콘텐츠에서 자동 생성
      caDraft: null,                       // 이해한 조건(초안) · {name,size,cond,why:[]}
      caEditing: false,                    // 홈 편집 모드
      caMsg: '',
      caNew: { ent: '', kind: '', excl: '' },   // 칩 직접 추가 입력
      caThinking: false, caVia: '',        // LLM 호출 상태·경로 표시
      caArrange: false,                    // 홈 배치 모드(핸드폰 홈처럼 흔들림·드래그)
      caDragId: '', caOverId: '',          // 드래그 중인 위젯 · 올라간 위젯
      caEditId: '',                        // 수정 중인 기존 위젯 id('' = 새로 만들기)
      caOnboard: false, caObStep: 1,       // 온보딩(첫 진입 · 관심 고르기 → 보는 방식 → 완료)
      caObPick: [], caObTone: '',          // 온보딩 선택값(분야 · 읽는 방식)
      caFresh: 40,                         // 새로운 소식 얼마나(0 익숙하게 ↔ 100 새롭게)
      caSeen: {},                          // 소비 기록(열어본 소식) · '나를 위한 추천' 근거
      caGallery: false,                    // 위젯 갤러리 열림
      caChat: [],                          // 대화로 다듬기 기록 [{me,bot}]
      caChatText: '',
      caDevice: 'mobile',                  // 시연 화면: mobile | pc
      caEntered: false,                    // 홈 화면에서 플로팅 버튼으로 진입했는지

      caBoot() {                           // 최초 진입: 실제 콘텐츠 로드 + 저장분 복원(없으면 기본 위젯)
        this.caLoad();
        if (!this.dictData && this.loadDict) { try { this.loadDict(); } catch (e) {} }   // 카테고리 한글 원천
        if (this.caWidgets.length) return;
        try {
          const raw = localStorage.getItem('prism_ca');
          if (raw) {
            const s = JSON.parse(raw);
            if (s) {
              if (typeof s.fresh === 'number') this.caFresh = s.fresh;
              if (s.seen) this.caSeen = s.seen;
              if (s.widgets && s.widgets.length) { this.caWidgets = s.widgets; return; }
            }
          }
        } catch (e) {}
        this.caOnboard = true;             // 저장분이 없으면 온보딩부터(빈 화면을 주지 않는다)
        this.caWidgets = [];               // 온보딩이 첫 위젯을 만든다
      },
      caSave() {
        try {
          localStorage.setItem('prism_ca', JSON.stringify({
            widgets: this.caWidgets, fresh: this.caFresh, seen: this.caSeen, onboarded: !this.caOnboard,
          }));
        } catch (e) {}
      },
      // ── 온보딩: 관심 분야 → 보는 방식 → 첫 위젯 자동 생성(빈 화면 없이 시작) ──
      caObFields() {                       // 등록 콘텐츠에 실제로 있는 분야만 제시
        return (this.caDict.topics || []).slice(0, 8);
      },
      caObToggle(v) {
        const i = this.caObPick.indexOf(v);
        if (i >= 0) this.caObPick.splice(i, 1); else this.caObPick.push(v);
      },
      caObNext() {
        if (this.caObStep === 1) {
          if (!this.caObPick.length) { this.caToast('관심 있는 분야를 하나 이상 골라주세요'); return; }
          this.caObStep = 2; return;
        }
        this.caObDone();
      },
      caObDone() {                         // 고른 관심 → 위젯으로
        const mk = (name, cond, size) => ({ id: 'w' + (this._caSeq = (this._caSeq || 0) + 1) + '-' + this.caObStep,
                                            name: name, size: size || 'md', src: 'cond',
                                            cond: Object.assign({ ents: [], topics: [], fields: [], kinds: [], excl: [], tone: '' }, cond),
                                            pins: [], hidden: [] });
        const out = [mk('나를 위한 추천', {}, 'lg')];
        out[0].src = 'reco';
        this.caObPick.forEach((f) => out.push(mk(this.caLabel('topic', f) + ' 소식', { topics: [f] })));
        if (this.caObTone) {
          const t = this.caObTone;
          out.push(mk(t === '깊게' ? '깊이 읽기' : (t === '가볍게' ? '가볍게 보기' : '빠른 소식'), { tone: t }, 'sm'));
        }
        this.caWidgets = out;
        this.caOnboard = false; this.caObStep = 1;
        this.caSave();
        this.caToast('첫 화면을 준비했어요 · 언제든 바꿀 수 있어요');
      },
      caObSkip() {                         // 건너뛰어도 빈 화면을 주지 않는다
        this.caObPick = (this.caDict.topics || []).slice(0, 1);
        this.caObDone();
      },
      caReset() {
        this.caWidgets = []; this.caOnboard = true; this.caObStep = 1; this.caObPick = []; this.caObTone = '';
        this.caEntered = false; this.caArrange = false; this.caSeen = {}; this.caFresh = 40;
        try { localStorage.removeItem('prism_ca'); } catch (e) {}
        this.caBoot(); this.caToast('처음 상태로 되돌렸습니다');
      },
      _caJosa(w, withF, noF) {             // 받침 있으면 withF, 없으면 noF (예: 이라/라 · 은/는)
        const c = String(w || '').trim().slice(-1);
        const code = c.charCodeAt(0);
        if (!c || code < 0xAC00 || code > 0xD7A3) return noF;
        return ((code - 0xAC00) % 28) ? withF : noF;
      },
      caToast(m) { this.caMsg = m; clearTimeout(this._caT); this._caT = setTimeout(() => { this.caMsg = ''; }, 2200); },

      // ── 자연어 → 조건 변환(시연용 규칙 기반) ──
      // 1) 문장을 제외절("~빼고/말고/제외")과 나머지로 나눈다 2) 사전값은 토큰 부분일치로 찾는다
      //    (사전값이 "속보·사건 추적" 이어도 사용자는 "속보" 라고만 말한다)
      _caTokens(entry) {
        return String(entry).split(/[·/,\s]+/).map((t) => t.trim()).filter((t) => t.length >= 2);
      },
      _caHas(entry, text, kind) {
        if (!text) return false;
        if (text.indexOf(entry) >= 0) return true;
        if (this._caTokens(entry).some((t) => text.indexOf(t) >= 0)) return true;
        const ko = kind ? this.caLabel(kind, entry) : '';        // 한글 라벨로도 매칭(사용자는 한글로 말한다)
        if (ko && ko !== entry) {
          if (text.indexOf(ko) >= 0) return true;
          if (this._caTokens(ko).some((t) => text.indexOf(t) >= 0)) return true;
        }
        return false;
      },
      _caSplit(s) {                        // 제외절 / 긍정절 분리
        const parts = s.split(/[,\n]|그리고|또는/).map((x) => x.trim()).filter(Boolean);
        const neg = [], pos = [];
        parts.forEach((p) => (/(빼|제외|말고|없이|안 ?나오게)/.test(p) ? neg : pos).push(p));
        return { pos: pos.join(' '), neg: neg.join(' ') };
      },
      caUnderstandRule() {                 // 규칙 폴백(LLM 미구성·실패 시)
        const raw = (this.caText || '').trim();
        if (!raw) { this.caToast('무엇을 모을지 한 문장으로 적어주세요'); return; }
        if (!this.caPool.length) { this.caToast('등록된 콘텐츠를 불러오는 중이에요 · 잠시 후 다시'); this.caLoad(); return; }
        const seg = this._caSplit(raw);
        const pick = (arr, text, kind) => (arr || []).filter((w) => this._caHas(w, text, kind));
        const excl = [].concat(pick(this.caDict.kinds, seg.neg, 'kind'), pick(this.caDict.topics, seg.neg, 'topic'));
        const ents = pick(this.caDict.ents, seg.pos, 'ent');
        const topics = pick(this.caDict.topics, seg.pos, 'topic').filter((w) => excl.indexOf(w) < 0);
        const kinds = pick(this.caDict.kinds, seg.pos, 'kind').filter((w) => excl.indexOf(w) < 0);
        const fields = pick(this.caDict.fields, seg.pos, 'field').filter((w) => excl.indexOf(w) < 0);
        let tone = '';                     // 읽는 방식은 긍정절에서만(제외절의 "속보 빼고" 가 톤이 되면 안 된다)
        this.caTone.forEach((t) => { if (t.words.some((w) => seg.pos.indexOf(w) >= 0)) tone = t.k; });
        const why = [];
        if (ents.length) why.push({ said: ents.join('·'), to: '모을 것(인물·팀)' });
        if (topics.length) why.push({ said: this.caLabels('topic', topics).join('·'), to: '모을 것(주제·분야)' });
        if (kinds.length) why.push({ said: this.caLabels('kind', kinds).join('·'), to: '꼭 이런 내용' });
        if (excl.length) why.push({ said: this.caLabels('kind', excl).join('·') + ' 빼고', to: '이런 건 빼고' });
        if (tone) why.push({ said: tone === '깊게' ? '깊이 있는' : tone, to: '읽는 방식' });
        // 주제와 분야가 같은 값이면 중복 노출하지 않는다(카테고리 하나가 두 축에 잡히는 경우)
        const fieldsOnly = fields.filter((f) => topics.indexOf(f) < 0);
        const cond = { ents: ents, topics: topics, fields: fieldsOnly, kinds: kinds, excl: excl, tone: tone };
        if (!ents.length && !topics.length && !fieldsOnly.length && !kinds.length && !tone) {
          this.caDraft = null;
          const ex = (this.caDict.topics[0] || this.caDict.ents[0] || '');
          this.caToast('이해하지 못했어요' + (ex ? (' · 예) ' + ex + ' 소식 깊이 있게') : '')); return;
        }
        this.caDraft = { name: this.caSuggestName(cond), size: 'md', cond: cond, why: why };
      },
      // 자연어 이해: 실제 LLM(/ca-understand) 우선 · 실패하면 규칙 폴백
      async caUnderstand() {
        const raw = (this.caText || '').trim();
        if (!raw) { this.caToast('무엇을 모을지 한 문장으로 적어주세요'); return; }
        if (!this.caPool.length) { this.caToast('등록된 콘텐츠를 불러오는 중이에요 · 잠시 후 다시'); this.caLoad(); return; }
        this.caThinking = true; this.caVia = '';
        try {
          const r = await this._afetch('/ca-understand', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: raw, dict: {
              ents: (this.caDict.ents || []).slice(0, 60),
              topics: (this.caDict.topics || []).slice(0, 60),
              kinds: (this.caDict.kinds || []).slice(0, 40) } }),
          });
          const j = await r.json();
          if (j && j.ok && j.cond) {
            const c = j.cond;
            this.caDraft = { name: c.name || this.caSuggestName(c), size: 'md',
                             cond: { ents: c.ents || [], topics: c.topics || [], fields: c.fields || [],
                                     kinds: c.kinds || [], excl: c.excl || [], tone: c.tone || '' },
                             why: c.why || [] };
            this.caVia = 'AI 가 이해했어요';
            this.caThinking = false; return;
          }
          this.caVia = '기본 규칙으로 이해했어요';        // LLM 미구성(키 없음)·실패
        } catch (e) { this.caVia = '기본 규칙으로 이해했어요'; }
        this.caThinking = false;
        this.caUnderstandRule();
      },
      caExamples() {                       // 등록 콘텐츠에서 만든 예시 문장(한글 라벨 · 하드코딩 없음)
        const d = this.caDict || {}, out = [];
        if ((d.topics || []).length) out.push(this.caLabel('topic', d.topics[0]) + ' 소식 깊이 있게');
        if ((d.ents || []).length) out.push(d.ents[0] + ' 소식만');
        if ((d.kinds || []).length) out.push(this.caLabel('kind', d.kinds[0]) + '만 모아서');
        return out.slice(0, 3);
      },
      // ── 대화로 다듬기: 지금 조건에 자연어 요청을 얹는다 ──
      async caRefine() {
        const t = (this.caChatText || '').trim();
        if (!t || !this.caDraft) return;
        this.caChatText = '';
        const before = JSON.parse(JSON.stringify(this.caDraft.cond));
        const seg = this._caSplit(t);
        const pick = (arr, text, kind) => (arr || []).filter((w) => this._caHas(w, text, kind));
        const c = this.caDraft.cond;
        const add = (key, vals) => { vals.forEach((v) => { if ((c[key] || []).indexOf(v) < 0) (c[key] = c[key] || []).push(v); }); };
        const del = (key, vals) => { vals.forEach((v) => { const i = (c[key] || []).indexOf(v); if (i >= 0) c[key].splice(i, 1); }); };
        const negK = pick(this.caDict.kinds, seg.neg, 'kind'), negT = pick(this.caDict.topics, seg.neg, 'topic');
        add('excl', negK.concat(negT)); del('kinds', negK); del('topics', negT);
        add('ents', pick(this.caDict.ents, seg.pos, 'ent'));
        add('topics', pick(this.caDict.topics, seg.pos, 'topic').filter((v) => (c.excl || []).indexOf(v) < 0));
        add('kinds', pick(this.caDict.kinds, seg.pos, 'kind').filter((v) => (c.excl || []).indexOf(v) < 0));
        this.caTone.forEach((x) => { if (x.words.some((wd) => seg.pos.indexOf(wd) >= 0)) c.tone = x.k; });
        if (/이름/.test(t)) {                                   // "이름은 ~로"
          const m = t.match(/이름[은는]?\s*['\"]?([^'\"]+?)['\"]?\s*(으?로|로)/);
          if (m && m[1]) this.caDraft.name = m[1].trim().slice(0, 30);
        }
        const changed = JSON.stringify(before) !== JSON.stringify(c);
        this.caChat.push({ me: t, bot: changed ? '반영했어요 · 미리보기를 확인해 보세요' : '무슨 뜻인지 몰라 그대로 뒀어요 · 다르게 말해 주세요' });
        if (this.caChat.length > 6) this.caChat.shift();
      },
      caSuggestName(c) {
        const base = c.ents[0] ? this.caLabel('ent', c.ents[0])
                   : (c.topics[0] ? this.caLabel('topic', c.topics[0])
                   : (c.fields[0] ? this.caLabel('field', c.fields[0])
                   : (c.kinds[0] ? this.caLabel('kind', c.kinds[0]) : '')));
        if (!base) return c.tone === '깊게' ? '깊이 읽기' : (c.tone === '가볍게' ? '가볍게 보기' : '빠른 소식');
        if (c.tone === '깊게') return base + ' 깊이 읽기';
        return base + ' 소식';
      },
      // 조건 칩 조작(초안)
      caChipDel(group, v) {
        if (!this.caDraft) return;
        const a = this.caDraft.cond[group] || [];
        const i = a.indexOf(v); if (i >= 0) a.splice(i, 1);
        this.caDraft.name = this.caSuggestName(this.caDraft.cond);
      },
      caChipAdd(group, v) {
        v = (v || '').trim(); if (!v || !this.caDraft) return;
        const a = this.caDraft.cond[group] || (this.caDraft.cond[group] = []);
        if (a.indexOf(v) < 0) a.push(v);
        this.caNew = { ent: '', kind: '', excl: '' };
        this.caDraft.name = this.caSuggestName(this.caDraft.cond);
      },
      caSetTone(t) { if (this.caDraft) { this.caDraft.cond.tone = this.caDraft.cond.tone === t ? '' : t; } },

      // ── 조건 → 기사 매칭(미리보기·위젯 공용) ──
      caMatch(cond, withReason) {
        const c = cond || {};
        const rows = [];
        (this.caPool || []).forEach((a) => {
          const hasEnt = (c.ents || []).length ? (c.ents || []).some((e) => a.ents.indexOf(e) >= 0) : null;
          const hasTop = (c.topics || []).length ? (c.topics || []).some((t) => a.topics.indexOf(t) >= 0) : null;
          const hasFld = (c.fields || []).length ? (c.fields || []).indexOf(a.field) >= 0 : null;
          const anyTarget = [hasEnt, hasTop, hasFld].filter((v) => v !== null);
          const targetOk = anyTarget.length ? anyTarget.some(Boolean) : true;
          const kindOk = (c.kinds || []).length ? (c.kinds || []).some((k) => a.kinds.indexOf(k) >= 0) : true;
          const excluded = (c.excl || []).some((x) => a.kinds.indexOf(x) >= 0 || a.topics.indexOf(x) >= 0);
          const toneOk = c.tone ? a.tone === c.tone : true;
          const ok = targetOk && kindOk && toneOk && !excluded;
          if (ok) {
            const why = [];
            if (hasEnt) why.push((c.ents || []).filter((e) => a.ents.indexOf(e) >= 0)[0]);
            if (hasTop) why.push(this.caLabel('topic', (c.topics || []).filter((t) => a.topics.indexOf(t) >= 0)[0]));
            if (hasFld) why.push(this.caLabel('field', a.field));
            if ((c.kinds || []).length) why.push(this.caLabel('kind', a.kinds[0]));
            rows.push({ a: a, ok: true, why: why.filter(Boolean).join(' · ') });
          } else if (withReason) {
            let r = '조건과 달라 빠짐';
            if (excluded) {
              const x = this.caLabel('kind', (c.excl || []).filter((v) => a.kinds.indexOf(v) >= 0 || a.topics.indexOf(v) >= 0)[0]);
              r = x + this._caJosa(x, '이라', '라') + ' 빠짐';
            }
            else if (!toneOk) r = '읽는 방식이 달라 빠짐';
            else if (!kindOk) r = '내용이 달라 빠짐';
            // 대상(인물·주제)에 걸린 것만 '빠진 이유'로 보여준다(무관한 기사 나열 방지)
            if (targetOk) rows.push({ a: a, ok: false, why: r });
          }
        });
        return rows;
      },
      caPreview() { return this.caMatch(this.caDraft ? this.caDraft.cond : null, true).slice(0, 6); },
      caPreviewOkN() { return this.caMatch(this.caDraft ? this.caDraft.cond : null, false).length; },

      // ── 위젯 생성·편집 ──
      caCreate() {
        if (!this.caDraft) return;
        const d = this.caDraft;
        if (this.caEditId) {                                  // 기존 위젯 수정 저장
          const w = this.caWidgets.filter((x) => x.id === this.caEditId)[0];
          if (w) {
            w.name = (d.name || w.name).trim();
            w.size = d.size || w.size;
            w.cond = JSON.parse(JSON.stringify(d.cond));
            w.hidden = [];                                    // 조건이 바뀌었으니 숨김은 초기화
            this.caSave();
            this.caToast('「' + w.name + '」 위젯을 수정했어요');
          }
          this.caEditId = ''; this.caDraft = null; this.caText = '';
          this.caTab = 'home';
          return;
        }
        const w = { id: 'w' + Date.now(), name: (d.name || '내 소식').trim(), size: d.size || 'md',
                    cond: JSON.parse(JSON.stringify(d.cond)), pins: [], hidden: [] };
        this.caWidgets.push(w); this.caSave();
        this.caDraft = null; this.caText = '';
        this.caTab = 'home';
        this.caToast('「' + w.name + '」 위젯을 홈에 추가했어요');
      },
      // ── 위젯 갤러리: 실제 콘텐츠 메타로 만드는 위젯 종류 ──
      caGalleryItems() {
        const d = this.caDict || {};
        const out = [
          { key: 'reco', name: '나를 위한 추천', desc: '내가 자주 본 것 기반', mk: () => ({ src: 'reco', cond: {} }) },
          { key: 'hot', name: '지금 많이 보는', desc: '전체에서 많이 보는 소식', mk: () => ({ src: 'hot', cond: {} }) },
        ];
        (d.topics || []).slice(0, 4).forEach((t) => out.push({
          key: 'topic:' + t, name: this.caLabel('topic', t), desc: '주제·분야',
          mk: () => ({ src: 'cond', cond: { topics: [t] } }) }));
        (d.ents || []).slice(0, 2).forEach((e) => out.push({
          key: 'ent:' + e, name: e, desc: '인물·팀 팔로우',
          mk: () => ({ src: 'cond', cond: { ents: [e] } }) }));
        out.push({ key: 'tone', name: '깊이 읽기', desc: '분석·해설 위주',
                   mk: () => ({ src: 'cond', cond: { tone: '깊게' } }) });
        return out;
      },
      caAddFromGallery(g) {
        const base = g.mk();
        const w = { id: 'w' + Date.now(), name: g.name, size: g.key === 'reco' ? 'lg' : 'md', src: base.src,
                    cond: Object.assign({ ents: [], topics: [], fields: [], kinds: [], excl: [], tone: '' }, base.cond),
                    pins: [], hidden: [] };
        this.caWidgets.push(w); this.caSave(); this.caGallery = false; this.caEntered = false;
        this.caToast('「' + w.name + '」 위젯을 홈에 추가했어요');
      },
      // 소비 기록(카드 열람) · '나를 위한 추천'과 다양성의 근거
      caOpen(a) {
        this.caSeen[a.id] = (this.caSeen[a.id] || 0) + 1;
        (a.topics || []).forEach((t) => { this.caSeen['t:' + t] = (this.caSeen['t:' + t] || 0) + 1; });
        this.caSave(); this.caToast('읽은 소식으로 기억할게요 · 추천에 반영됩니다');
      },
      caSeenScore(a) {                     // 이 소식 자체를 본 횟수(가중) + 같은 주제를 본 횟수
        let n = (this.caSeen[a.id] || 0) * 3;
        (a.topics || []).forEach((t) => { n += (this.caSeen['t:' + t] || 0); });
        return n;
      },
      caWidgetRows(w) {
        const cap = w.size === 'lg' ? 5 : (w.size === 'md' ? 3 : 2);
        const hidden = (w.hidden || []);
        const pin = (w.pins || []);
        let rows;
        if (w.src === 'reco') {            // 나를 위한 추천: 내가 자주 본 주제 우선
          rows = (this.caPool || []).filter((a) => hidden.indexOf(a.id) < 0)
            .map((a) => ({ a: a, ok: true, why: this.caSeenScore(a) ? '자주 보는 주제예요' : '새로 살펴볼 만해요' }))
            .sort((x, y) => this.caSeenScore(y.a) - this.caSeenScore(x.a));
        } else if (w.src === 'hot') {      // 지금 많이 보는: 전체 인기(시연은 최신순 = 지금 뜨는)
          rows = (this.caPool || []).filter((a) => hidden.indexOf(a.id) < 0)
            .map((a, i) => ({ a: a, ok: true, why: '지금 많이 보는 소식 ' + (i + 1) + '위' }));
        } else {
          rows = this.caMatch(w.cond, false).filter((r) => hidden.indexOf(r.a.id) < 0);
        }
        rows.sort((x, y) => (pin.indexOf(y.a.id) - pin.indexOf(x.a.id)));   // 고정 먼저
        if (w._pg && rows.length > cap) {                                    // 새로고침 필: 다음 묶음
          const off = (w._pg * cap) % rows.length;
          rows = rows.slice(off).concat(rows.slice(0, off));
        }
        const keep = rows.slice(0, cap);
        // 새로운 소식 얼마나: 값이 클수록 조건 밖 소식을 한 칸 섞는다(익숙함 ↔ 새로움)
        if (this.caFresh >= 60 && keep.length === cap && w.src !== 'hot') {
          const ids = keep.map((r) => r.a.id);
          const rest = (this.caPool || []).filter((a) => ids.indexOf(a.id) < 0 && hidden.indexOf(a.id) < 0);
          // 조건 밖(=아직 이 위젯에 안 뜬) 소식 중 내가 덜 본 것부터
          const fresh = rest.slice().sort((x, y) => this.caSeenScore(x) - this.caSeenScore(y))[0];
          if (fresh) keep[cap - 1] = { a: fresh, ok: true, why: '평소 안 보던 소식이라 골라봤어요' };
        }
        return keep;
      },
      // 카드마다 "왜 이 소식?"(사용자 말)
      caRowWhy(w, r) {
        if (r && r.why) return r.why;
        return this.caWidgetWhy(w);
      },
      caIsPinned(w, id) { return (w.pins || []).indexOf(id) >= 0; },
      caTogglePin(w, id) {
        const p = w.pins || (w.pins = []);
        const i = p.indexOf(id);
        if (i >= 0) { p.splice(i, 1); this.caToast('고정을 풀었어요'); }
        else { p.push(id); this.caToast('이 소식을 고정했어요 · 항상 이 자리에 남습니다'); }
        this.caSave();
      },
      caDislike(w, id) {                   // 관심없음 = 이 위젯에서 숨기고 다음 소식으로 교체
        (w.hidden || (w.hidden = [])).push(id);
        this.caSave(); this.caToast('덜 보여드릴게요 · 다음 소식으로 바꿨어요');
      },
      caSetSize(w, s) { w.size = s; this.caSave(); },
      caRemove(w) {
        const i = this.caWidgets.indexOf(w);
        if (i >= 0) { const n = w.name; this.caWidgets.splice(i, 1); this.caSave(); this.caToast('「' + n + '」 위젯을 지웠어요'); }
      },
      caMove(w, dir) {
        const i = this.caWidgets.indexOf(w), j = i + dir;
        if (i < 0 || j < 0 || j >= this.caWidgets.length) return;
        this.caWidgets.splice(j, 0, this.caWidgets.splice(i, 1)[0]);
        this.caSave();
      },
      // ── 홈 배치(드래그) · 핸드폰 홈 위젯 옮기듯 ──
      caDragStart(w, ev) {
        if (!this.caArrange) return;
        this.caDragId = w.id;
        try { ev.dataTransfer.effectAllowed = 'move'; ev.dataTransfer.setData('text/plain', w.id); } catch (e) {}
      },
      caDragOver(w, ev) {
        if (!this.caArrange || !this.caDragId || this.caDragId === w.id) return;
        ev.preventDefault();                                   // drop 허용
        this.caOverId = w.id;
        const from = this.caWidgets.findIndex((x) => x.id === this.caDragId);
        const to = this.caWidgets.findIndex((x) => x.id === w.id);
        if (from < 0 || to < 0 || from === to) return;
        this.caWidgets.splice(to, 0, this.caWidgets.splice(from, 1)[0]);   // 실시간 재배치(홈에서 바로 보임)
      },
      caDragEnd() {
        if (this.caDragId) { this.caSave(); this.caToast('위치를 옮겼어요'); }
        this.caDragId = ''; this.caOverId = '';
      },
      caToggleArrange() {
        this.caArrange = !this.caArrange;
        if (!this.caArrange) { this.caSave(); this.caToast('배치를 저장했어요'); }
        else this.caToast('위젯을 끌어서 옮기고, 크기를 바꾸고, 지울 수 있어요');
      },
      // ── 기존 위젯 수정(이름·조건을 매니저에서 다시 편집) ──
      caEditWidget(w) {
        this.caEditId = w.id;
        this.caDraft = { name: w.name, size: w.size,
                         cond: JSON.parse(JSON.stringify(w.cond)),
                         why: [{ said: w.name, to: '지금 이 위젯의 조건' }] };
        this.caText = '';
        this.caVia = '';
        this.caEntered = true; this.caTab = 'make';
      },
      caCancelEdit() { this.caEditId = ''; this.caDraft = null; },
      caShuffle(w) {                       // '새로운 ○○' · 다음 묶음으로 넘긴다(다음 앱 새로고침 필)
        w._pg = ((w._pg || 0) + 1);
        this.caToast('새로운 소식으로 바꿨어요');
      },
      caPage(w) {
        const cap = w.size === 'lg' ? 5 : (w.size === 'md' ? 3 : 2);
        const total = Math.max(1, Math.ceil((this.caPool || []).length / cap));
        return (((w._pg || 0) % total) + 1) + ' / ' + total;
      },
      caSizeLabel(s) { return s === 'lg' ? '크게' : (s === 'md' ? '보통' : '작게'); },
      // 위젯이 왜 이 소식을 보여주는지 한 줄(사용자 말)
      caWidgetWhy(w) {
        const c = w.cond || {};
        const t = [].concat(c.ents || [], this.caLabels('topic', c.topics), this.caLabels('field', c.fields));
        let s = t.length ? t.join('·') + ' 관심으로 모았어요' : '요즘 많이 보는 소식이에요';
        if ((c.excl || []).length) {
          const x = this.caLabels('kind', c.excl).join('·');
          s += ' · ' + x + this._caJosa(x, '은', '는') + ' 빼고';
        }
        if (c.tone) s += ' · ' + (c.tone === '깊게' ? '깊이 있는 글' : c.tone) + ' 위주';
        return s;
      },
}));
