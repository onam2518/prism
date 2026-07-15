/* 모바일 검수 전용(/m) 앱 상태 · 데스크탑과 같은 API 계약(/auth·/reviewer·/raw·/feedback·/dict)만 사용.
   v1.1: 목록 화면(탭 진입) + 카드 검수 · 카테고리 한글 표시(catKo · /dict 재사용). */
window.mreview = () => ({
  view: 'boot', sheet: '', backend: '', guideUrl: '', theme: 'light',
  email: '', pw: '', nick: '', err: '', busy: false,
  authToken: '', rtoken: '', reviewer: '', name: '',
  items: [], idx: 0, done: 0, fixed: 0, earned: 0, points: null, toast: '', _toastT: null,
  fix: { elems: ['summary'], note: '' },
  defTitle: '', defBody: '', dict: null,
  updateAvail: false, _boot: '',                   // 새 버전 배포 감지(서버 부팅 ID 변화) · 새로고침 배너(PC 규약)
  // 요소·인텐트 정의 사전 = 서버 /dict 단일 원천(fixElements·intentDefs) · 데스크탑과 공용(이원화 부채 해소)
  get FIX_ELEMENTS() { return (this.dict && this.dict.fixElements) || []; },

  async init() {
    // 테마: head 선적용 스크립트가 저장값(prism_m_theme)>기기 설정으로 data-theme 를 먼저 세팅 · 여기선 상태만 동기화
    this.theme = document.documentElement.getAttribute('data-theme') || 'light';
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
      this._seenBoot(cfg.bootId);
    } catch (e) {}
    // 배포 감지: 모바일은 SSE 없이 탭 복귀 + 10분 주기로 bootId 재확인(홈화면 앱 복귀 케이스 커버)
    document.addEventListener('visibilitychange', () => { if (!document.hidden) this.checkBoot(); });
    setInterval(() => this.checkBoot(), 10 * 60 * 1000);
    if (this.backend === 'supabase' ? this.authToken : this.reviewer) await this.boot();
    else this.view = 'login';
  },
  _seenBoot(b) {                                   // 최초 값 기억 · 달라지면 새 버전 배너(분기 없음 · 새로고침 단일 유도)
    if (!b) return;
    if (this._boot && this._boot !== b) this.updateAvail = true;
    if (!this._boot) this._boot = b;
  },
  async checkBoot() {
    if (this.updateAvail) return;                  // 이미 감지됨 · 재확인 불필요
    try { this._seenBoot(((await (await fetch('/config')).json()) || {}).bootId); } catch (e) {}
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
  toggleTheme() {                                  // 라이트/다크 전환 · PC(app.js toggleTheme)와 동일 data-theme 규약
    this.theme = this.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', this.theme);
    try { localStorage.setItem('prism_m_theme', this.theme); } catch (e) {}
  },
  logout() {
    try { localStorage.removeItem('prism_token'); localStorage.removeItem('prism_rtoken'); localStorage.removeItem('prism_reviewer'); } catch (e) {}
    this.authToken = ''; this.rtoken = ''; this.reviewer = ''; this.name = '';
    this.sheet = ''; this.view = 'login';
  },

  async boot() {
    this.view = 'boot';
    await this.syncProfile();                      // 데스크탑에서 닉네임 변경 시 localStorage 옛 이름 교체
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
      // v2: 골드 문항(블라인드 검증 문항)도 포함 · 목록은 전체(검수 완료 포함) 노출, 미검수는 배지로 구분
      this.items = (r && r.items) || [];
      this.idx = 0; this.done = 0; this.fixed = 0; this.earned = 0;
    } catch (e) { this.err = '목록을 불러오지 못했습니다'; }
  },
  async loadDict() {
    try { const d = await (await this.afetch('/dict')).json(); if (d) this.dict = d; } catch (e) {}   // 운영 게이트: 인증 GET
  },
  async loadPoints() {
    try {
      const r = await (await this.afetch('/arena' + (this.reviewer ? ('?reviewer=' + encodeURIComponent(this.reviewer)) : ''))).json();
      // 내 행 매칭 = reviewer_id(서버 my_id) 우선 · 이름 매칭은 닉네임 변경 직후 어긋난다(데스크탑과 동일 규약)
      const rows = (r && r.leaderboard) || [];
      const me = (r && r.my_id && rows.find((x) => x.reviewer_id === r.my_id)) || rows.find((x) => x.reviewer === this.name);
      if (me) this.points = me.points;
    } catch (e) {}
  },
  async syncProfile() {                            // 프로필 = 서버 기준(기기 간 닉네임 동기화)
    if (this.backend !== 'supabase' || !this.authToken) return;
    try {
      const p = await (await this.afetch('/reviewer', { method: 'POST', headers: this._hdrs(), body: JSON.stringify({ mode: 'login' }) })).json();
      if (p && p.ok && p.name && p.name !== this.name) {
        this.name = p.name; this.reviewer = p.name;
        try { localStorage.setItem('prism_reviewer', p.name); } catch (e) {}
      }
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
    const fb = (it && it.fb) || {};                // 로컬도 loadItems 가 reviewer 를 보내 mine 이 채워진다
    return !!fb.mine;                              // 타 검수자 표만 있으면 미검수로 남는다(2026-07-10)
  },
  unreviewedCount() { return this.items.filter((it) => !this.reviewed(it)).length; },
  progPct() { const t = this.items.length; return t ? Math.round((t - this.unreviewedCount()) / t * 100) : 0; },
  gradeLabel(g) { return g === 'G' ? '유통 가능 · G' : (g === 'R' ? '차단 · R' : '판정 보류 · 재실행 필요'); },
  gradeClass(g) { return g === 'G' ? 'ds-badge--success' : (g === 'R' ? 'ds-badge--error' : 'ds-badge--reason'); },
  teamLine(fb) { return '팀 의견 · 정확 ' + (fb.good || 0) + '개 · 수정 필요 ' + (fb.bad || 0) + '개'; },
  intentDef(v) { this._def(v, ((this.dict && this.dict.intentDefs) || {})[v] || '관점·형식을 나타내는 인텐트 값입니다.'); },
  // 정의 시트 확장(v2): 카테고리·등급·품질 사유도 탭 정의 · 원천은 전부 /dict
  catDef(v) {
    const d = this.dict || {};
    const parts = String(v).split('/').map((p) => p.trim());
    const t2 = parts[1] || parts[0];
    const dd = (d.tier2Defs || {})[t2] || {};
    const body = (dd.def || (d.categoryCriteria || {})[parts[0]] || '콘텐츠 카테고리입니다.')
      + (dd.ex ? ' · 예: ' + dd.ex : '');
    this._def(this.catKo(v), body);
  },
  gradeDef(g) {
    const key = (g === 'G' || g === 'R') ? g : 'YELLOW';
    const row = ((this.dict && this.dict.gradeDefs) || []).find((x) => x.k === key);
    this._def(row ? row.t : this.gradeLabel(g), row ? row.d : '유통 가능 여부 판정 등급입니다.');
  },
  reasonKo(v) {                                    // 품질 사유 병기(한글/영문순 · 데스크탑 reasonBoth 와 동일 규칙)
    const nm = ((this.dict && this.dict.qualityNames) || {})[v];
    return nm ? (nm + ' (' + v + ')') : v;
  },
  reasonDef(v) { this._def(this.reasonKo(v), ((this.dict && this.dict.qualityMetas) || {})[v] || '유통 제외 판정의 품질 사유입니다.'); },
  _def(title, body) { this.defTitle = title; this.defBody = body; if (!this.dict) this.loadDict(); this.sheet = 'def'; },

  open(i) { this.idx = i; this.view = 'card'; },
  back() { this.view = 'list'; },

  // ── 카드 스와이프 판정: 오른쪽으로 밀기=정확 · 왼쪽으로 밀기=수정 시트 ──
  // 세로 스크롤과 구분(|dy| 우세면 무시) · 시트 열림 중에는 비활성 · 판정 자체는 good()/openFix() 재사용
  swipeDx: 0, _swX: 0, _swY: 0, _swOn: false,
  swStart(e) {
    const t = e.touches && e.touches[0];
    if (!t || this.sheet) return;
    this._swX = t.clientX; this._swY = t.clientY; this._swOn = true; this.swipeDx = 0;
  },
  swMove(e) {
    if (!this._swOn) return;
    const t = e.touches && e.touches[0]; if (!t) return;
    const dx = t.clientX - this._swX, dy = t.clientY - this._swY;
    if (Math.abs(dy) > Math.abs(dx) * 1.2) { this.swipeDx = 0; return; }   // 세로 스크롤 우선
    this.swipeDx = Math.max(-120, Math.min(120, dx));
  },
  swEnd() {
    if (!this._swOn) return;
    this._swOn = false;
    const dx = this.swipeDx; this.swipeDx = 0;
    if (dx >= 70) this.good();                       // → 정확(저장 실패·연타 처리는 good 이 담당)
    else if (dx <= -70) this.openFix();              // ← 수정 시트(메모 입력 후 저장)
  },
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

  // 골드 문항 응답 = 응답 후 정오답 공개(즉시 학습 피드백 · 데스크탑과 동일 규약) · 반영 시 true
  _goldReveal(r) {
    if (!(r && r.gold)) return false;
    if (r.gold.correct) this._celebrate(10, '골드 문항 정답');
    else { this.toast = '🏅 골드 문항 · 아쉽지만 오답이에요'; if (this._toastT) clearTimeout(this._toastT); this._toastT = setTimeout(() => { this.toast = ''; }, 2200); }
    return true;
  },
  _missions(r) { ((r && r.missions_completed) || []).forEach((m) => this._celebrate(m.bonus, '미션 달성 · ' + m.label)); },
  async good() {
    const r = await this._post('good');
    if (r === 'skip') return;                       // 연타 무시(진행도 안 넘김)
    if (r === null) { this._netFail(); return; }    // 서버 미저장(오프라인 등) → 진행·점수 올리지 않음(판정 유실 방지)
    this._markMine('good');
    if (!this._goldReveal(r)) this._celebrate(10, '검수 완료');
    this._missions(r);
    this._advance();
  },
  _netFail() {
    this.toast = '⚠️ 저장 실패 · 연결 확인 후 다시 시도해 주세요';
    if (this._toastT) clearTimeout(this._toastT); this._toastT = setTimeout(() => { this.toast = ''; }, 2600);
  },
  openFix() {
    const c = this.cur(); if (!c) return;
    const fb = (c.fb || {});
    // 직전 교정 이어쓰기: 내 메모·요소 프리필(선두 '[요소] ' 태그는 저장 시 재부착이라 벗긴다)
    this.fix.note = String(fb.note || '').replace(/^\[[^\]]*\]\s*/, '');
    this.fix.elems = (fb.elems && fb.elems.length) ? fb.elems.slice() : ['summary'];
    if (!this.dict) this.loadDict();                 // 부팅 시 로드 실패 대비 재시도(요소 칩 원천)
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
    if (r === null) { this._netFail(); return; }    // 시트 유지(메모 보존) · 재시도 유도
    this._markMine('bad', tagged, this.fix.elems);
    this.sheet = ''; this.fixed += 1;
    if (!this._goldReveal(r)) this._celebrate(hadNote ? 10 : 25, hadNote ? '검수 완료' : '교정 반영');
    this._missions(r);
    this._advance();
  },
  _celebrate(pt, label) {
    if (this.points !== null) this.points += pt;
    // 완료 화면 '이번에 획득 PT' = 실누적. 모든 지급(일반·골드 정답·미션 보너스)이 이 함수를
    // 지나므로 여기서만 더한다 — 추정식(판정×10+교정×15)은 골드 오답·이어쓰기·미션에서 어긋났다.
    this.earned += pt;
    this.toast = '✨ +' + pt + ' PT · ' + label;
    if (this._toastT) clearTimeout(this._toastT);
    this._toastT = setTimeout(() => { this.toast = ''; }, 1600);
  },
});
