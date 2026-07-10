/* 모바일 검수 전용(/m) 앱 상태 · 데스크탑과 같은 API 계약(/auth·/reviewer·/raw·/feedback·/dict)만 사용.
   v1.1: 목록 화면(탭 진입) + 카드 검수 · 카테고리 한글 표시(catKo · /dict 재사용).
   v1.2: 골드 문항 카드(판정 즉시 정오답 공개) · 정의 시트 확장(카테고리·품질 사유·등급·엔티티 ·
   원천 /dict) · 완료 화면 획득 PT 실누적. */
window.mreview = () => ({
  view: 'boot', sheet: '', backend: '', guideUrl: '',
  email: '', pw: '', nick: '', err: '', busy: false,
  authToken: '', rtoken: '', reviewer: '', name: '',
  items: [], idx: 0, done: 0, fixed: 0, earned: 0, points: null,
  toast: '', toastKind: '', _toastT: null,
  fix: { elems: ['summary'], note: '' },
  defTitle: '', defBody: '', dict: null,
  srcOpen: false, srcUrl: '', srcService: '',   // 원문 전체화면(iframe) · 열 때 현재 카드 url 스냅샷
  // 데스크탑 app.js 와 동일 요소 사전 · 검수 화면 이원화의 유일한 중복(정의 데이터는 /dict 원천)
  FIX_ELEMENTS: [
    { id: 'summary', label: '리드문', stage: 'analyze' },
    { id: 'entities', label: '엔티티', stage: 'analyze' },
    { id: 'intent', label: '인텐트', stage: 'analyze' },
    { id: 'category', label: '카테고리', stage: 'analyze' },
    { id: 'grade', label: '등급·유통', stage: 'judge' },
    { id: 'quality', label: '품질 사유', stage: 'review' },
  ],
  // 인텐트 정의 폴백: 원천은 /dict(intentDefs · 서비스 분기 값 포함) → 사전 미로드 시에만 사용
  INTENT_DEF: {
    '속보·사건 추적': '새로 발생한 사건·이슈를 빠르게 전하고 후속 경과를 추적', '심층 분석': '배경·맥락·데이터로 사안을 깊이 해설',
    '팬덤·화제성': '인물·작품에 대한 팬 반응·화제 중심', '실용 정보': '방법·팁·가이드 등 바로 쓰는 정보',
    '감성·공감': '감정·경험을 나누며 공감을 유도', '오락·유머': '재미·유머 중심의 가벼운 콘텐츠',
    '의견·논쟁': '찬반이 병렬로 오가는 주장·토론 콘텐츠(한쪽 논조가 뚜렷하면 옹호·지지/반박·비판)', '학술·전문': '전문 지식·연구·기술을 다룸',
    '옹호·지지': '특정 사안·인물·정책을 지지하는 한쪽 논조의 콘텐츠', '반박·비판': '특정 사안·인물·정책·주장에 반대·비판하는 한쪽 논조의 콘텐츠',
    '인터뷰': '인물 문답 중심의 전달 형식', '현장취재·르포': '현장에서 직접 취재한 심층 전달',
    '그래픽·인포그래픽': '도표·시각 자료 중심의 전달', '포토·영상 중심': '사진·영상이 본문의 중심',
    '보도자료·공식발표': '기관·기업의 공식 발표 기반', '후기·리뷰·비평': '사용·관람 경험의 평가·비평',
  },

  async init() {
    try {
      this.authToken = localStorage.getItem('prism_token') || '';
      this.rtoken = localStorage.getItem('prism_rtoken') || '';
      this.reviewer = localStorage.getItem('prism_reviewer') || '';
      this.name = localStorage.getItem('prism_reviewer') || '';
    } catch (e) {}
    try {
      const cfg = await (await fetch('/config')).json();
      this.backend = cfg.backend || '';
      const g = cfg.guideUrls || {};
      this.guideUrl = g.guide_user || g.guide || '';
    } catch (e) {}
    if (this.backend === 'supabase' ? this.authToken : this.reviewer) await this.boot();
    else this.view = 'login';
  },

  _hdrs() { const h = { 'Content-Type': 'application/json' }; if (this.authToken) h['Authorization'] = 'Bearer ' + this.authToken; return h; },
  // 인증 공통 fetch: 401 → 갱신 1회 → 재시도 → 그래도 만료면 로그인 화면(데스크탑 _afetch 와 동일 규약)
  async afetch(url, opts) {
    opts = opts || {};
    const call = () => {
      const h = Object.assign({}, opts.headers || {});
      if (this.authToken) h['Authorization'] = 'Bearer ' + this.authToken;
      return fetch(url, Object.assign({}, opts, { headers: h }));
    };
    let r = await call();
    if (r.status === 401 && this.rtoken && await this.refresh()) r = await call();
    if (r.status === 401 && this.backend === 'supabase') { this.err = '로그인이 만료됐습니다 · 다시 로그인해 주세요'; this.view = 'login'; }
    return r;
  },
  async refresh() {
    try {
      const r = await (await fetch('/auth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: 'refresh', refresh_token: this.rtoken }) })).json();
      if (!(r && r.ok && r.access_token)) return false;
      this.authToken = r.access_token;
      if (r.refresh_token) this.rtoken = r.refresh_token;
      try { localStorage.setItem('prism_token', this.authToken); localStorage.setItem('prism_rtoken', this.rtoken); } catch (e) {}
      return true;
    } catch (e) { return false; }
  },

  async login() {
    const email = (this.email || '').trim(), pw = this.pw || '';
    if (!email || !pw) { this.err = '이메일과 비밀번호를 입력하세요'; return; }
    this.busy = true; this.err = '';
    try {
      const r = await (await fetch('/auth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: 'login', email: email, password: pw }) })).json();
      if (!(r && r.ok && r.access_token)) { this.err = (r && r.error) || '로그인 실패'; this.busy = false; return; }
      this.authToken = r.access_token; this.rtoken = r.refresh_token || '';
      const p = await (await this.afetch('/reviewer', { method: 'POST', headers: this._hdrs(), body: JSON.stringify({ mode: 'login' }) })).json();
      if (!(p && p.ok)) { this.err = (p && p.needSignup) ? '가입이 필요합니다 · 데스크탑에서 가입 후 이용하세요' : ((p && p.error) || '프로필 조회 실패'); this.busy = false; return; }
      this.name = p.name || ''; this.reviewer = this.name;
      try { localStorage.setItem('prism_token', this.authToken); localStorage.setItem('prism_rtoken', this.rtoken); localStorage.setItem('prism_reviewer', this.name); } catch (e) {}
      await this.boot();
    } catch (e) { this.err = '네트워크 오류 · 잠시 후 다시 시도하세요'; }
    this.busy = false;
  },
  nickStart() {                                   // 로컬(sqlite) 모드: 닉네임만으로 시작(데스크탑과 동일 취급)
    const n = (this.nick || '').trim();
    if (!n) { this.err = '닉네임을 입력하세요'; return; }
    this.reviewer = n; this.name = n;
    try { localStorage.setItem('prism_reviewer', n); } catch (e) {}
    this.boot();
  },
  logout() {
    try { localStorage.removeItem('prism_token'); localStorage.removeItem('prism_rtoken'); localStorage.removeItem('prism_reviewer'); } catch (e) {}
    this.authToken = ''; this.rtoken = ''; this.reviewer = ''; this.name = '';
    this.sheet = ''; this.srcOpen = false; this.view = 'login';
  },

  async boot() {
    this.view = 'boot';
    await this.loadItems();
    this.loadPoints();
    this.loadDict();                               // 카테고리 한글 표시(비차단)
    this.view = 'list';
    if (!this.helpSeen()) { this.sheet = 'help'; try { localStorage.setItem('prism_m_help', '1'); } catch (e) {} }
  },
  helpSeen() { try { return !!localStorage.getItem('prism_m_help'); } catch (e) { return true; } },
  async loadItems() {
    try {
      const q = this.backend === 'supabase' ? '' : ('&reviewer=' + encodeURIComponent(this.reviewer));
      const r = await (await this.afetch('/raw?limit=200' + q)).json();
      // 골드 문항(hash gold:*)도 노출: 서버가 블라인드로 섞어 주고, 응답한 문항은 재출제 안 됨.
      // 목록은 전체(검수 완료 포함) 노출, 미검수는 배지로 구분
      this.items = (r && r.items) || [];
      this.idx = 0; this.done = 0; this.fixed = 0; this.earned = 0;
    } catch (e) { this.err = '목록을 불러오지 못했습니다'; }
  },
  async loadDict() {
    try { const d = await (await fetch('/dict')).json(); if (d) this.dict = d; } catch (e) {}
  },
  async loadPoints() {
    try {
      const r = await (await this.afetch('/arena' + (this.reviewer ? ('?reviewer=' + encodeURIComponent(this.reviewer)) : ''))).json();
      const me = ((r && r.leaderboard) || []).find((x) => x.reviewer === this.name);
      if (me) this.points = me.points;
    } catch (e) {}
  },

  // 카테고리 한글 표시(UI 전용 · 데스크탑 catKo 와 동일 규칙): 값·저장·전달은 영문 유지
  catKo(v) {
    if (!v || !this.dict) return v;
    const t1k = this.dict.tier1Ko || {}, t2k = this.dict.tier2Ko || {};
    const parts = String(v).split('/').map((p) => p.trim());
    if (parts.length === 1) return t1k[parts[0]] || t2k[parts[0]] || parts[0];
    return (t1k[parts[0]] || parts[0]) + ' / ' + (t2k[parts[1]] || parts[1]);
  },

  cur() { return this.items[this.idx] || null; },
  reviewed(it) {                                   // '완료' = 내 표(fb.mine) 기준 · 팀 합의(fb.n)가 아님
    if (it && it.goldDone) return true;            // 골드 문항: 응답 즉시 완료(fb 는 안 건드림 · 재출제 안 됨)
    const fb = (it && it.fb) || {};                // 로컬도 loadItems 가 reviewer 를 보내 mine 이 채워진다
    return !!fb.mine;                              // 타 검수자 표만 있으면 미검수로 남는다(2026-07-10)
  },
  unreviewedCount() { return this.items.filter((it) => !this.reviewed(it)).length; },
  progPct() { const t = this.items.length; return t ? Math.round((t - this.unreviewedCount()) / t * 100) : 0; },
  gradeLabel(g) { return g === 'G' ? '유통 가능 · G' : (g === 'R' ? '차단 · R' : '판정 보류 · 재실행 필요'); },
  gradeClass(g) { return g === 'G' ? 'ds-badge--success' : (g === 'R' ? 'ds-badge--error' : 'ds-badge--reason'); },
  teamLine(fb) { return '팀 의견 · 정확 ' + (fb.good || 0) + '개 · 수정 필요 ' + (fb.bad || 0) + '개'; },
  // 품질 사유 한글 라벨(UI 전용 · 값·저장은 영문 키 유지)
  reasonKo(v) { return ((this.dict || {}).qualityNames || {})[v] || v; },
  // 값 배지 탭 → 정의 시트: 데스크탑 termDef 와 같은 축(인텐트·카테고리·품질 사유·등급·엔티티).
  // 정의 원천은 /dict(intentDefs·tier2Defs·qualityMetas·gradeDefs) · 미로드 시 일반 안내로 폴백.
  showDef(kind, v) {
    const d = this.dict || {};
    if (kind === 'intent') {
      this.defTitle = '인텐트 · ' + v;
      this.defBody = (d.intentDefs || {})[v] || this.INTENT_DEF[v] || '관점·형식을 나타내는 인텐트 값입니다.';
    } else if (kind === 'category') {
      const parts = String(v).split('/').map((p) => p.trim());
      const dd = (d.tier2Defs || {})[parts[1] || parts[0]] || {};
      this.defTitle = '카테고리 · ' + this.catKo(v);
      this.defBody = (dd.def || '콘텐츠 카테고리(IAB 기반) 값입니다.') + (dd.ex ? ('\n예시 · ' + dd.ex) : '');
    } else if (kind === 'reason') {
      this.defTitle = '품질 사유 · ' + this.reasonKo(v);
      this.defBody = (d.qualityMetas || {})[v] || '유통 제외(R) 판단에 쓰인 품질 사유입니다.';
    } else if (kind === 'grade') {
      const g = (d.gradeDefs || []).find((x) => x.k === (v || 'YELLOW')) || null;
      this.defTitle = g ? g.t : ('등급 · ' + this.gradeLabel(v));
      this.defBody = g ? g.d : '판정 계약 정의를 불러오지 못했습니다 · 도움말의 가이드를 참고하세요.';
    } else {                                       // entity: 값별 사전 없음 · 요소 자체를 설명
      this.defTitle = v;
      this.defBody = '핵심 개체(인물·기관·작품 등) · 본문에서 추출된 주요 엔티티입니다. 본문에 없거나 핵심이 아니면 교정 대상이에요.';
    }
    this.sheet = 'def';
  },

  open(i) { this.idx = i; this.view = 'card'; },
  back() { this.srcOpen = false; this.view = 'list'; },
  // 원문 전체화면: url 스냅샷 후 열기(다음 카드로 넘어가도 표시 정합) · iframe 은 x-if 로 열 때만 로드
  openSrc() { const c = this.cur(); if (!c || !c.url) return; this.srcUrl = c.url; this.srcService = c.service || ''; this.srcOpen = true; },
  closeSrc() { this.srcOpen = false; },
  startReview() {
    const i = this.items.findIndex((it) => !this.reviewed(it));
    if (i >= 0) this.open(i);
  },
  _advance() {                                     // 다음 미검수(현재 뒤 → 앞 순환) · 없으면 완료 화면
    this.done += 1;
    const n = this.items.length;
    for (let s = 1; s <= n; s++) {
      const j = (this.idx + s) % n;
      if (!this.reviewed(this.items[j])) { this.idx = j; return; }
    }
    this.view = 'done';
  },
  _markMine(verdict, note, elems) {                // 목록 배지·진행률 즉시 반영(다음 /raw 전 표시 정합)
    const c = this.cur(); if (!c) return;
    const fb = Object.assign({}, c.fb || {});
    if (!fb.mine) {
      fb.n = (fb.n || 0) + 1;
      const k = verdict === 'good' ? 'good' : 'bad';
      fb[k] = (fb[k] || 0) + 1;
    }
    fb.mine = verdict;
    if (note !== undefined) fb.note = note;
    if (elems) fb.elems = elems.slice();
    c.fb = fb;
  },

  _lastPost: 0,
  async _post(verdict, elements, note) {          // 검수 속도 제한(0.8초) 존중 · 연타 방지
    const now = Date.now();
    if (now - this._lastPost < 900) return 'skip';
    this._lastPost = now;
    const c = this.cur(); if (!c) return null;
    const body = { hash: c.hash, service: c.service || '', title: c.title || '', model: c.model || '',
                   verdict: verdict, stage: elements && elements.length ? this.elemStage(elements[0]) : 'analyze',
                   note: note || '', reviewer: this.reviewer || '', name: this.name || '' };
    if (elements && elements.length) body.elements = elements;
    try { return await (await this.afetch('/feedback', { method: 'POST', headers: this._hdrs(), body: JSON.stringify(body) })).json(); }
    catch (e) { return null; }
  },
  elemStage(id) { const e = this.FIX_ELEMENTS.find((x) => x.id === id); return e ? e.stage : 'analyze'; },
  elemLabel(id) { const e = this.FIX_ELEMENTS.find((x) => x.id === id); return e ? e.label : ''; },

  async good() {
    const r = await this._post('good');
    if (r === 'skip') return;                                     // 연타 무시(진행도 안 넘김)
    if (r && r.gold) { this._goldReveal(r.gold); return; }
    this._markMine('good');
    this._celebrate(10, '검수 완료');
    this._advance();
  },
  // 골드 문항(정답 알려진 검증 문항): 서버가 feedback 대신 gold_checks 에 기록하고 정오답을
  // 즉시 알려준다(데스크탑과 동일 계약). 팀 피드백(fb)은 건드리지 않고 완료 처리만.
  _goldReveal(gold) {
    const c = this.cur();
    if (c) { c.goldDone = true; c.goldCorrect = !!gold.correct; }
    if (gold.correct) this._celebrate(10, '골드 문항 정답');
    else this._show('골드 문항 · 정답과 달랐어요 (품질 점수에 반영)', 'info', 2600);
    this._advance();
  },
  openFix() {
    const c = this.cur(); if (!c) return;
    const fb = (c.fb || {});
    // 직전 교정 이어쓰기: 내 메모·요소 프리필(선두 '[요소] ' 태그는 저장 시 재부착이라 벗긴다)
    this.fix.note = String(fb.note || '').replace(/^\[[^\]]*\]\s*/, '');
    this.fix.elems = (fb.elems && fb.elems.length) ? fb.elems.slice() : ['summary'];
    this.sheet = 'fix';
  },
  toggleElem(id) {
    const i = this.fix.elems.indexOf(id);
    if (i >= 0) { if (this.fix.elems.length > 1) this.fix.elems.splice(i, 1); } else this.fix.elems.push(id);
  },
  async saveFix() {
    const raw = (this.fix.note || '').trim(); if (!raw) return;
    const hadNote = !!(((this.cur() || {}).fb || {}).note);
    const tagged = '[' + this.fix.elems.map((e) => this.elemLabel(e)).join('·') + '] ' + raw;
    const r = await this._post('bad', this.fix.elems.slice(), tagged);
    if (r === 'skip') return;
    this.sheet = '';
    if (r && r.gold) { this._goldReveal(r.gold); return; }        // 골드는 교정 아님(메모는 기록 안 됨)
    this._markMine('bad', tagged, this.fix.elems);
    this.fixed += 1;
    this._celebrate(hadNote ? 10 : 25, hadNote ? '검수 완료' : '교정 반영');
    this._advance();
  },
  _celebrate(pt, label) {
    if (this.points !== null) this.points += pt;
    this.earned += pt;                             // 완료 화면 '이번에 획득 PT' = 실누적(추정식 아님)
    this._show('✨ +' + pt + ' PT · ' + label, '', 1600);
  },
  _show(msg, kind, ms) {
    this.toast = msg; this.toastKind = kind || '';
    if (this._toastT) clearTimeout(this._toastT);
    this._toastT = setTimeout(() => { this.toast = ''; }, ms || 1600);
  },
});
