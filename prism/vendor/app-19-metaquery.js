// 콘텐츠 조회(메타베이스 경유) · 검수 지정 — mod === 'metaq'
// 서버(/metaquery*)가 메타베이스를 대신 호출한다(키 보호 · 브라우저 직접 호출 없음).
// 발행 메타는 재추출 없이 모델 초안으로 인입되어 기존 검수 흐름을 그대로 탄다.
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
  mqStatus: null,            // GET /metaquery 응답(설정·연결 상태) · null = 미로드
  mqRows: [],                // 조회 결과 행
  mqSel: {},                 // hash → true 선택 상태
  mqBusy: false,
  mqMsg: '',
  mqPurpose: 'review',       // 지정 용도: review 검수용(기본) | eval 평가용(홀드아웃)
  mqService: '', mqGrade: '', mqKeyword: '', mqFrom: '', mqTo: '',
  mqOffset: 0, mqLimit: 50,
  mqSearched: false,         // 첫 조회 전에는 빈 표 대신 안내를 보여 준다

  async mqLoad() {           // 메뉴 진입: 상태 1회 로드(조회는 버튼으로)
    if (this.mqStatus) return;
    try { const r = await this._afetch('/metaquery'); this.mqStatus = await r.json(); }
    catch (e) { this.mqStatus = { ok: false, configured: false }; }
  },
  mqFilters() {
    return { service: this.mqService, grade: this.mqGrade, keyword: this.mqKeyword,
             date_from: this.mqFrom, date_to: this.mqTo, limit: this.mqLimit, offset: this.mqOffset };
  },
  async mqSearch(reset = true) {
    if (reset) this.mqOffset = 0;
    this.mqBusy = true; this.mqMsg = '';
    try {
      const r = await this._afetch('/metaquery-search', { method: 'POST', headers: this._authHeaders(),
        body: JSON.stringify(this.mqFilters()) });
      const j = await r.json();
      if (!r.ok || j.error) { this.mqMsg = '오류: ' + (j.error || r.status); return; }
      this.mqRows = reset ? (j.rows || []) : this.mqRows.concat(j.rows || []);
      if (reset) this.mqSel = {};
      this.mqSearched = true;
      this.mqMsg = j.rows && j.rows.length ? '' : (reset ? '조건에 맞는 콘텐츠가 없습니다' : '더 가져올 콘텐츠가 없습니다');
    } catch (e) { this.mqMsg = '오류: ' + e; }
    finally { this.mqBusy = false; }
  },
  async mqMore() { this.mqOffset += this.mqLimit; await this.mqSearch(false); },
  mqToggle(row) {            // 이미 인입된 행은 선택 불가(중복 방지 안내)
    if (row.registered) return;
    if (this.mqSel[row.hash]) delete this.mqSel[row.hash];
    else this.mqSel[row.hash] = true;
  },
  mqToggleAll() {
    const open = this.mqRows.filter((r) => !r.registered);
    const all = open.length && open.every((r) => this.mqSel[r.hash]);
    this.mqSel = {};
    if (!all) open.forEach((r) => { this.mqSel[r.hash] = true; });
  },
  get mqSelCount() { return Object.keys(this.mqSel).length; },
  async mqRegister() {       // 선택 행을 검수 대상으로 지정(복사 인입 · 재추출 없음)
    const rows = this.mqRows.filter((r) => this.mqSel[r.hash]);
    if (!rows.length) { this.mqMsg = '먼저 지정할 콘텐츠를 선택하세요'; return; }
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
