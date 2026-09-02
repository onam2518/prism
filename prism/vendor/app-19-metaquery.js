// 콘텐츠 조회 · 검수 지정 · mod === 'metaq'
// 기본 소스는 스테이징(사내망 수집기가 /metaquery-stage 로 올린 발행분). 메타베이스 직접 조회는
// 서버가 대신 호출(사내망에서 띄운 프리즘에서만 유효). 발행 메타는 재추출 없이 모델 초안으로 인입.
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
  mqStatus: null,            // GET /metaquery 응답(설정·연결 상태) · null = 미로드
  mqRows: [],                // 조회 결과 행
  mqSel: {},                 // hash → true 선택 상태
  mqBusy: false,
  mqMsg: '',
  mqPurpose: 'review',       // 지정 용도: review 검수용(기본) | eval 평가용(홀드아웃)
  mqService: '', mqGrade: '', mqKeyword: '',
  mqFrom: '', mqTo: '',      // 발행 기간(종료일 포함) · 기본 오늘 하루 · 필터에서 넓힐 수 있다
  mqOffset: 0, mqLimit: 50, mqTotal: -1,
  mqSearched: false,         // 첫 조회 전에는 빈 표 대신 안내를 보여 준다
  mqSource: 'stage',         // stage 수집분(기본) | metabase 서버 직접 호출

  mqState: '',               // 인입 상태(화면 필터): '' 전체 | open 미인입 | done 인입됨
  mqIntent: '', mqCategory: '', mqEntity: '', mqModel: '', mqMeta: '',   // 메타별 필터(서버가 행 JSON 에서 거른다)
  async mqLoad() {           // 메뉴 진입: 상태만 로드 · 조회는 필터를 고른 뒤 사용자가 누른다(범위를 좁혀 부담을 줄인다)
    if (!this.mqFrom) { this.mqFrom = this.mqToday(); this.mqTo = this.mqFrom; }
    if (this.mqStatus) return;
    await this.mqStatusLoad();
  },
  mqToday() {                // KST 기준 오늘(YYYY-MM-DD)
    const d = new Date(Date.now() + 9 * 3600 * 1000);
    return d.toISOString().slice(0, 10);
  },
  async mqStatusLoad() {
    try { const r = await this._afetch('/metaquery'); this.mqStatus = await r.json(); }
    catch (e) { this.mqStatus = { ok: false, configured: false, staged: -1, services: [] }; }
  },
  async mqRefresh() { await this.mqStatusLoad(); if (this.mqSearched) await this.mqSearch(false); },
  mqSvcs() {                 // 서비스 선택지: 서버가 준 스테이징 목록 + 현재 표에 보이는 값
    const set = new Set(((this.mqStatus || {}).services) || []);
    this.mqRows.forEach((r) => { if (r.service) set.add(r.service); });
    if (this.mqService) set.add(this.mqService);
    return Array.from(set).sort();
  },
  get mqFilterN() {
    return [this.mqService, this.mqGrade, this.mqState, this.mqIntent, this.mqCategory, this.mqEntity, this.mqModel, this.mqMeta]
      .filter(Boolean).length + (this.mqSource !== 'stage' ? 1 : 0) + ((this.mqFrom !== this.mqToday() || this.mqTo !== this.mqToday()) ? 1 : 0);
  },
  mqFilterReset() {          // 조건 초기화(발행일은 오늘로) · 조회는 다시 누른다
    this.mqService = ''; this.mqGrade = ''; this.mqFrom = this.mqToday(); this.mqTo = this.mqFrom; this.mqState = ''; this.mqSource = 'stage';
    this.mqIntent = ''; this.mqCategory = ''; this.mqEntity = ''; this.mqModel = ''; this.mqMeta = '';
  },
  mqFacet(k) {               // 선택지: 서버 패싯(스테이징 실제 값) + 현재 선택값(비어 있어도 표시 유지)
    const list = (((this.mqStatus || {}).facets) || {})[k] || [];
    const cur = { intents: this.mqIntent, categories: this.mqCategory, models: this.mqModel }[k];
    return cur && list.indexOf(cur) < 0 ? list.concat([cur]) : list;
  },
  get mqShown() {            // 인입 상태만 화면에서 거른다(나머지 조건은 서버 조회)
    if (!this.mqState) return this.mqRows;
    return this.mqRows.filter((r) => this.mqState === 'done' ? r.registered : !r.registered);
  },
  mqKeys(e) {                // 단축키(검수 대상 목록과 같은 결) · 입력 중이거나 팝업이 열려 있으면 무시
    if (this.mod !== 'metaq' || this.detailOpen || this.cmpOpen) return;
    const t = e.target || {};
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName || '')) return;
    if (e.key === 'r' || e.key === 'R') { e.preventDefault(); this.mqRefresh(); }
  },
  mqFilters() {
    return { source: this.mqSource, service: this.mqService, grade: this.mqGrade, keyword: this.mqKeyword,
             date_from: this.mqFrom || this.mqToday(), date_to: this.mqTo || '', limit: this.mqLimit, offset: this.mqOffset,
             intent: this.mqIntent, category: this.mqCategory, entity: this.mqEntity, model: this.mqModel, meta: this.mqMeta };
  },
  async mqSearch(reset = true) {
    if (reset) this.mqOffset = 0;
    this.mqBusy = true; this.mqMsg = '';
    try {
      const r = await this._afetch('/metaquery-search', { method: 'POST', headers: this._authHeaders(),
        body: JSON.stringify(this.mqFilters()) });
      const j = await r.json();
      if (!r.ok || j.error) { this.mqMsg = '오류: ' + (j.error || r.status); return; }
      this.mqRows = j.rows || [];                        // 페이지 단위 교체(누적 아님)
      this.mqTotal = (typeof j.total === 'number') ? j.total : -1;
      this.mqSel = {};
      this.mqSearched = true;
      this.mqMsg = (j.rows && j.rows.length) || reset ? '' : '더 가져올 콘텐츠가 없습니다';
    } catch (e) { this.mqMsg = '오류: ' + e; }
    finally { this.mqBusy = false; }
  },
  async mqPage(dir) { this.mqOffset = Math.max(0, this.mqOffset + dir * this.mqLimit); await this.mqSearch(false); },
  mqHasNext() { return this.mqTotal >= 0 ? (this.mqOffset + this.mqRows.length) < this.mqTotal : this.mqRows.length >= this.mqLimit; },
  mqPageLabel() {
    const cur = Math.floor(this.mqOffset / this.mqLimit) + 1;
    if (this.mqTotal < 0) return cur + ' 페이지';
    return cur + ' / ' + Math.max(1, Math.ceil(this.mqTotal / this.mqLimit)) + ' 페이지';
  },
  mqRangeLabel() {
    if (!this.mqFrom) return '';
    if (!this.mqTo) return this.mqFrom + ' 이후';
    return this.mqFrom === this.mqTo ? ('발행일 ' + this.mqFrom) : (this.mqFrom + ' ~ ' + this.mqTo);
  },
  mqTotalLabel() {           // 조건에 맞는 총건수(서버) · 인입 상태 필터는 화면에서 걸러 표시 건수만 줄어든다
    if (!this.mqSearched) return (this.mqStatus && this.mqStatus.staged >= 0) ? ('수집분 ' + this.mqStatus.staged + '건') : '';
    const total = this.mqTotal >= 0 ? this.mqTotal : this.mqRows.length;
    return this.mqShown.length + ' / ' + total + '건';
  },
  mqSelectable(row) {        // 수집분에서는 인입된 행도 '목록에서 삭제' 용으로 고를 수 있다 · 직접 조회에서는 미인입만
    return this.mqSource === 'stage' || !row.registered;
  },
  mqToggle(row) {
    if (!this.mqSelectable(row)) return;
    if (this.mqSel[row.hash]) delete this.mqSel[row.hash];
    else this.mqSel[row.hash] = true;
  },
  mqToggleAll() {
    const open = this.mqRows.filter((r) => this.mqSelectable(r));
    const all = open.length && open.every((r) => this.mqSel[r.hash]);
    this.mqSel = {};
    if (!all) open.forEach((r) => { this.mqSel[r.hash] = true; });
  },
  get mqSelCount() { return Object.keys(this.mqSel).length; },
  _mqList(v) {               // 서버 _as_list 와 같은 해석: 배열 그대로 · 문자열은 쉼표·가운뎃점 분리
    if (Array.isArray(v)) return v.map(String).filter((x) => x.trim());
    const s = (v == null ? '' : String(v)).trim();
    if (!s) return [];
    if (s[0] === '[') { try { return JSON.parse(s).map(String).filter((x) => x.trim()); } catch (e) {} }
    return s.split(/\||,| · /).map((x) => x.trim()).filter(Boolean);   // 붙여 쓴 가운뎃점은 값의 일부(인텐트 명칭)
  },
  mqOpen(r) {                // 제목 클릭: 검수 상세의 콘텐츠 영역과 같은 읽기 전용 보기(판정·이력 없음 · 지정 전이라 검수 대상 아님)
    this.openContentView({ hash: r.hash, title: r.title || '', service: r.service || '', body: r.body || '', url: r.url || '',
      summary: r.summary || '', entities: this._mqList(r.entities), intent: this._mqList(r.intent), category: this._mqList(r.category),
      grade: r.grade || '', model: r.model ? ('dev:' + r.model) : '' });
    this.cmp.subtitle = r.subtitle || '';
  },
  async mqDelete() {         // 선택 행을 수집분 목록에서 삭제(검수 콘텐츠는 영향 없음)
    const hashes = this.mqRows.filter((r) => this.mqSel[r.hash]).map((r) => r.hash);
    if (!hashes.length) { this.mqMsg = '먼저 삭제할 행을 선택하세요'; return; }
    this.mqBusy = true; this.mqMsg = '삭제 중…';
    try {
      const r = await this._afetch('/metaquery-stage-delete', { method: 'POST', headers: this._authHeaders(),
        body: JSON.stringify({ hashes: hashes }) });
      const j = await r.json();
      if (!r.ok || j.error) { this.mqMsg = '오류: ' + (j.error || r.status); return; }
      this.mqRows = this.mqRows.filter((row) => !this.mqSel[row.hash]);
      this.mqSel = {};
      if (this.mqStatus) { this.mqStatus.staged = j.staged; this.mqStatus.services = this.mqSvcs().filter((sv) => this.mqRows.some((r) => r.service === sv)); }
      this.mqMsg = '✓ 목록에서 ' + j.deleted + '건 삭제';
    } catch (e) { this.mqMsg = '오류: ' + e; }
    finally { this.mqBusy = false; }
  },
  async mqRegister() {       // 선택 행을 검수 대상으로 지정(복사 인입 · 재추출 없음)
    const rows = this.mqRows.filter((r) => this.mqSel[r.hash] && !r.registered);
    if (!rows.length) { this.mqMsg = '먼저 지정할 콘텐츠를 선택하세요 (이미 인입된 행은 제외)'; return; }
    this.mqBusy = true; this.mqMsg = '지정 중…';
    try {
      const r = await this._afetch('/metaquery-register', { method: 'POST', headers: this._authHeaders(),
        body: JSON.stringify({ rows: rows, purpose: this.mqPurpose }) });
      const j = await r.json();
      if (!r.ok || j.error) { this.mqMsg = '오류: ' + (j.error || r.status); return; }
      this.mqMsg = '✓ 지정 ' + j.added + '건' + (j.existing ? ' · 이미 인입 ' + j.existing + '건' : '')
        + (j.skipped_empty ? ' · 제외 ' + j.skipped_empty + '건' : '');
      this.mqRows.forEach((row) => { if (this.mqSel[row.hash]) row.registered = true; });
      this.mqSel = {};
      this.loadRawThrottled && this.loadRawThrottled();   // 검수 표 신선도(콘텐츠 검수 화면 대비)
    } catch (e) { this.mqMsg = '오류: ' + e; }
    finally { this.mqBusy = false; }
  },
  // ── 시스템 설정: 메타베이스 연결 저장(운영 관리자 · /config 경로) ──
  mbUrl: '', mbDbId: '', mbQuery: '', mbKey: '', mbMsg: '',
  mqSyncCfg() {              // 설정 화면 진입 시 cfg → 입력값 동기화(빈 입력만 채움)
    const c = this.cfg || {};
    if (!this.mbUrl && c.metabaseUrl) this.mbUrl = c.metabaseUrl;
    if (!this.mbDbId && c.metabaseDbId) this.mbDbId = String(c.metabaseDbId);
    if (!this.mbQuery && c.metabaseQuery) this.mbQuery = c.metabaseQuery;
  },
  async saveMetabase() {
    this.mbMsg = '저장 중…';
    const body = { metabase_url: this.mbUrl || '', metabase_db_id: parseInt(this.mbDbId || '0', 10) || 0,
                   metabase_query: this.mbQuery || '' };
    if ((this.mbKey || '').trim()) { body.metabase_api_key = this.mbKey.trim(); body.persist = true; }
    try {
      const r = await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(body) });
      const j = await r.json();
      if (!r.ok || j.error) { this.mbMsg = '오류: ' + (j.error || r.status); return; }
      this.cfg = j; this.mbKey = ''; this.mqStatus = null;   // 상태 재로드 유도
      this.mbMsg = '✓ 저장됨';
    } catch (e) { this.mbMsg = '오류: ' + e; }
  },
  async forgetMetabaseKey() {
    try {
      const r = await this._afetch('/config', { method: 'POST', headers: this._authHeaders(),
        body: JSON.stringify({ forget_metabase: true }) });
      this.cfg = await r.json(); this.mqStatus = null; this.mbMsg = '키 삭제됨';
    } catch (e) { this.mbMsg = '오류: ' + e; }
  },
}));
