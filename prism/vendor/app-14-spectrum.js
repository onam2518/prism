/* Prism 앱 조각 14 · prismApp 프로퍼티 그룹. 스펙트럼(실험실): 사내 MCP 허브 프로토타입.
   기획 문서 "13. Spectrum" 기반 · 화면 prism/ui/19c-spectrum.html · 백엔드 GET/POST /spectrum.

   화면 구성(시안 B · 카탈로그 허브형): 첫 화면은 커넥터 카드 목록이고, 카드를 누르면 상세로
   들어가 거기서 바로 접속 키를 받고 쓰는 도구에 붙이는 데까지 이어진다. 찾기 → 보기 → 연결이
   한 줄기다. 관문 체험 · 사용 기록 · 접속 키 관리는 위쪽 작은 버튼으로 옮겨 둔 보조 화면이다.

   프로토타입이라 오가는 데이터는 모의값이지만, 관문 자체는 실제로 붙는 MCP 최소 구현이다.
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({

      sp: null,                     // GET /spectrum 응답 {catalog, keys, usage, metrics}
      spTab: 'catalog',             // catalog(카탈로그·기본) | try(관문 체험) | usage(사용 기록) | keys(접속 키 관리)
      spSel: null,                  // 상세로 들어간 커넥터(있으면 목록 대신 상세를 보여준다)
      spNewKey: null,               // 방금 발급된 키(전체 값은 이 응답에만 있다 · 다시 못 본다)
      spBusy: false, spMsg: '',
      spQ: '', spFilter: 'all',     // 카탈로그 찾기: 검색어 · 상태 좁히기(all | live | preparing)
      spKeyForm: { label: '', ttlDays: '90' },
      // params 는 기능별 입력칸을 한 곳에 모아 둔다(키가 겹치지 않는다) · 기능을 바꿔도 입력값이 날아가지 않고,
      // 보낼 때 spParamsOut() 이 지금 고른 기능의 칸만 골라 담는다.
      spTry: { keyId: '', tool: 'query_logs', out: null, busy: false,
               params: { action: 'click', limit: '20', as_of: '', metric: 'dau', days: '7' } },

      // 관문에 붙은 기능(tool) 목록 · 파라미터는 기본값을 채워 두고 바로 실행해 볼 수 있게 한다.
      // query_search_terms 는 일부러 막아 둔 기능이라 거절(403)이 정상 결과다(권한 정책 시연).
      spToolDefs: [
        { id: 'query_logs', label: 'query_logs · 행동 로그 조회', restricted: false,
          fields: [{ k: 'action', label: '행동', def: 'click', ph: '예) click' },
                   { k: 'limit', label: '가져올 개수', def: '20', num: true },
                   // 벨루가 골드는 Iceberg 라 과거 시점 그대로 다시 읽을 수 있다(타임 트래블)
                   { k: 'as_of', label: '기준 날짜(비우면 지금)', def: '', ph: '예) 2026-08-03' }] },
        { id: 'list_tables', label: 'list_tables · 볼 수 있는 표 목록', restricted: false, fields: [] },
        { id: 'agg_metrics', label: 'agg_metrics · 지표 집계', restricted: false,
          fields: [{ k: 'metric', label: '지표', def: 'dau', ph: '예) dau' },
                   { k: 'days', label: '기간(일)', def: '7', num: true }] },
        { id: 'query_search_terms', label: 'query_search_terms · 검색어 조회(권한 제한)', restricted: true, fields: [] },
      ],

      // ── 방어적 접근자: 서버 응답이 비어도 화면이 깨지지 않게 ──────────────
      get spCatalog() { return (this.sp && this.sp.catalog) || []; },
      get spKeys() { return (this.sp && this.sp.keys) || []; },
      get spUsage() { return (this.sp && this.sp.usage) || []; },
      get spMetrics() { return (this.sp && this.sp.metrics) || {}; },
      // 관문 체험에서 고를 수 있는 키 = 폐기하지 않았고 아직 기한이 남은 것
      get spLiveKeys() { return this.spKeys.filter((k) => !k.revoked && (this.spDday(k.expiresAt) === null || this.spDday(k.expiresAt) >= 0)); },
      get spOrigin() { try { return location.origin; } catch (e) { return ''; } },
      get spGwUrl() { return this.spOrigin + '/spectrum-gw'; },

      // ── 카탈로그 찾기 ──────────────────────────────────────────────────
      get spCounts() {
        const cs = this.spCatalog;
        return { all: cs.length,
                 live: cs.filter((c) => c.status === 'live').length,
                 preparing: cs.filter((c) => c.status !== 'live').length };
      },
      get spCatalogShown() {
        const q = (this.spQ || '').trim().toLowerCase();
        return this.spCatalog.filter((c) => {
          if (this.spFilter === 'live' && c.status !== 'live') return false;
          if (this.spFilter === 'preparing' && c.status === 'live') return false;
          if (!q) return true;
          const hay = [c.name, this.spDesc(c), this.spTeam(c), c.dataBasis, this.spWhTag(c),
                       (c.useCases || []).join(' '),
                       (c.tools || []).map((t) => (t.name || '') + ' ' + this.spDesc(t)).join(' ')].join(' ').toLowerCase();
          return hay.indexOf(q) >= 0;
        });
      },

      // ── 조회 · 변경 ────────────────────────────────────────────────────
      async spLoad() {
        this.spBusy = true;
        try {
          const r = await this._afetch('/spectrum', { headers: this._authHeaders() });
          const d = await r.json();
          if (r.ok && d && !d.error) {
            this.sp = d;
            // 열어 둔 상세는 갱신 뒤에도 같은 커넥터로 다시 물린다(사라지면 화면이 목록으로 튄다)
            if (this.spSel) this.spSel = this.spCatalog.find((c) => c.id === this.spSel.id) || null;
            if (!this.spTry.keyId) { const k = this.spLiveKeys[0]; if (k) this.spTry.keyId = k.id; }
          } else this._err((d && d.error) || '스펙트럼 정보를 불러오지 못했습니다');
        } catch (e) { this._err('스펙트럼 정보를 불러오지 못했습니다'); }
        finally { this.spBusy = false; }
      },

      async _spPost(body) {
        return await (await this._afetch('/spectrum', { method: 'POST', headers: this._authHeaders(),
                                                        body: JSON.stringify(body) })).json();
      },

      async spIssueKey() {
        if (this.spBusy) return;
        this.spBusy = true; this.spMsg = '';
        const ttl = parseInt(this.spKeyForm.ttlDays, 10);
        try {
          const r = await this._spPost({ action: 'issue_key', label: (this.spKeyForm.label || '').trim(),
                                         ttlDays: (isFinite(ttl) && ttl > 0) ? ttl : 90 });
          if (r && r.ok) {
            this.spNewKey = r;                        // 전체 키는 이 응답에만 있다
            this.spKeyForm.label = '';
            if (r.id) this.spTry.keyId = r.id;        // 방금 만든 키로 관문 체험 바로 가능
            await this.spLoad();
          } else this.spMsg = (r && r.error) || '접속 키를 발급하지 못했습니다';
        } catch (e) { this.spMsg = '접속 키를 발급하지 못했습니다'; }
        finally { this.spBusy = false; }
      },

      async spRevokeKey(id) {
        if (!id || this.spBusy) return;
        if (!(await this.dsConfirm('이 접속 키를 폐기할까요? 이 키로 붙여 둔 AI 도구는 바로 연결이 끊깁니다.',
                                   { ok: '폐기', danger: true }))) return;
        this.spBusy = true;
        try {
          const r = await this._spPost({ action: 'revoke_key', id: id });
          if (r && r.ok) { if (this.spNewKey && this.spNewKey.id === id) this.spNewKey = null; }
          else this._err((r && r.error) || '접속 키를 폐기하지 못했습니다');
        } catch (e) { this._err('접속 키를 폐기하지 못했습니다'); }
        finally { this.spBusy = false; }
        await this.spLoad();
      },

      async spGwTry() {
        if (this.spTry.busy) return;
        this.spTry.busy = true; this.spTry.out = null;
        try {
          const r = await this._spPost({ action: 'gw_try', keyId: this.spTry.keyId,
                                         tool: this.spTry.tool, params: this.spParamsOut() });
          // 거절 · 인증 실패도 '결과'다(status 로 온다) · status 가 없는 응답만 오류로 알린다
          if (r && r.status == null && r.error) this._err(r.error);
          else this.spTry.out = r || null;
        } catch (e) { this._err('관문을 호출하지 못했습니다'); }
        finally { this.spTry.busy = false; }
        await this.spLoad();                          // 호출 기록 · 지표 즉시 반영
      },

      async spReset() {
        if (this.spBusy) return;
        this.spBusy = true;
        try {
          const r = await this._spPost({ action: 'reset_demo' });
          if (r && r.ok) { this.spTry.out = null; }
          else this._err((r && r.error) || '기록을 초기화하지 못했습니다');
        } catch (e) { this._err('기록을 초기화하지 못했습니다'); }
        finally { this.spBusy = false; }
        await this.spLoad();
      },

      // ── 이동 · 폼 보조 ─────────────────────────────────────────────────
      spOpen(c) { this.spSel = c; this.spTab = 'catalog'; this.spScrollTop(); },
      spBackToList() { this.spSel = null; this.spScrollTop(); },
      spGo(tab) { this.spTab = tab; if (tab !== 'catalog') this.spSel = null; this.spScrollTop(); },
      spScrollTop() { try { window.scrollTo({ top: 0, behavior: 'smooth' }); } catch (e) {} },
      // 상세에서 관문 체험으로 갈 때, 그 커넥터의 첫 기능을 미리 골라 둔다
      spTryTool(name) {
        if (name && this.spToolDefs.some((t) => t.id === name)) this.spTry.tool = name;
        this.spTry.out = null; this.spGo('try');
      },
      spToolDef(id) { return this.spToolDefs.find((t) => t.id === id) || { id: id, fields: [] }; },
      spParamsOut() {
        const out = {};
        (this.spToolDef(this.spTry.tool).fields || []).forEach((f) => {
          const v = this.spTry.params[f.k];
          if (v === '' || v == null) return;
          out[f.k] = f.num ? Number(v) : v;
        });
        return out;
      },

      // ── 표시 헬퍼 ──────────────────────────────────────────────────────
      // 시각 값은 epoch 초로 오지만, 문자열로 오는 경우도 그대로 보여준다(숫자만 변환).
      _spNum(v) { const s = String(v == null ? '' : v).trim(); return /^\d+(\.\d+)?$/.test(s) ? Number(s) : null; },
      spWhen(v) {                                     // 날짜 + 시각 'M.D HH:mm'(fmtTs 재사용)
        const s = String(v == null ? '' : v).trim();
        if (!s) return '·';
        const n = this._spNum(s);
        return n === null ? s : this.fmtTs(n);
      },
      spDate(v) {                                     // 날짜만 'YYYY-MM-DD'
        const s = String(v == null ? '' : v).trim();
        if (!s) return '·';
        const n = this._spNum(s);
        if (n === null) return s;
        if (!n) return '·';                           // 0 = 값 없음(1970년으로 보이는 것 방지)
        const d = new Date(n > 1e12 ? n : n * 1000);
        const p = (x) => String(x).padStart(2, '0');
        return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
      },
      spDday(exp) {                                   // 남은 날 수(달력 날짜 기준) · 만료 정보가 없으면 null
        const n = this._spNum(exp);
        if (n === null || !n) return null;            // 0 · 빈 값 = 기한 없음(만료로 오판하지 않는다)
        const mid = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
        return Math.round((mid(new Date(n > 1e12 ? n : n * 1000)) - mid(new Date())) / 86400000);
      },
      spExpTxt(k) {
        const d = this.spDday(k && k.expiresAt);
        if (d === null) return '기한 없음';
        return d < 0 ? '만료됨' : (d === 0 ? '오늘 만료' : 'D-' + d);
      },
      spExpCls(k) {
        const d = this.spDday(k && k.expiresAt);
        if (d === null) return 'ds-badge--neutral';
        if (d < 0) return 'ds-badge--error';
        return d <= 7 ? 'ds-badge--warning' : 'ds-badge--neutral';
      },
      spStatusCls(s) {
        const n = Number(s || 0);
        return n === 200 ? 'ds-badge--success' : (n === 403 ? 'ds-badge--warning' : (n === 401 ? 'ds-badge--error' : 'ds-badge--neutral'));
      },
      spStatusTxt(s) {
        const n = Number(s || 0);
        return ({ 200: '성공 · 200', 401: '인증 실패 · 401', 403: '거절 · 403' })[n] || ('응답 ' + (s == null ? '?' : s));
      },
      spJson(v) { try { const s = JSON.stringify(v, null, 2); return s === undefined ? '' : s; } catch (e) { return String(v); } },
      // 비율은 0~1(소수)로도, 0~100(퍼센트)으로도 올 수 있어 둘 다 받는다(1 이하면 소수로 본다).
      spPct(v) {
        const n = Number(v);
        if (!isFinite(n)) return '·';
        return (n <= 1 ? Math.round(n * 1000) / 10 : Math.round(n * 10) / 10) + '%';
      },
      spNum(v) { const n = Number(v); return isFinite(n) ? n : '·'; },
      spParamTxt(p) {
        if (!p) return '';
        return (p.name || '') + (p.type ? (' · ' + p.type) : '') + (p.required ? ' · 필수' : '');
      },
      // 커넥터·기능의 이름표는 서버가 주는 대로 받는다(desc/summary · team/owner 둘 다 허용).
      spDesc(c) { return (c && (c.desc || c.summary)) || ''; },
      spTeam(c) { return (c && (c.team || c.owner)) || '미정'; },
      // 파라미터 표기: params 배열이 오면 그대로, MCP 표준 inputSchema 가 오면 같은 모양으로 펴서 쓴다.
      spToolParams(t) {
        if (!t) return [];
        if (Array.isArray(t.params)) return t.params;
        const sc = t.inputSchema || {};
        const props = sc.properties || {};
        const req = sc.required || [];
        return Object.keys(props).map((k) => {
          const p = props[k] || {};
          const enums = Array.isArray(p.enum) && p.enum.length ? (' · 고를 수 있는 값: ' + p.enum.join(' · ')) : '';
          return { name: k, type: p.type || '', desc: (p.description || p.desc || '') + enums,
                   required: req.indexOf(k) >= 0 };
        });
      },
      spHasJson(v) { return !!v && typeof v === 'object' && Object.keys(v).length > 0; },
      // ── 데이터 창고(벨루가 골드 레이어) 표기 ────────────────────────────
      spWh(c) { const w = c && c.warehouse; return this.spHasJson(w) ? w : null; },
      spWhTag(c) {                                    // 카드에 붙는 짧은 표기 · 예) '벨루가 골드 · Iceberg'
        const w = this.spWh(c);
        if (!w) return '';
        const nm = ({ beluga: '벨루가' })[w.name] || w.name || '';
        const ly = ({ gold: '골드', silver: '실버', bronze: '브론즈' })[w.layer] || w.layer || '';
        const fmt = w.format ? (String(w.format).charAt(0).toUpperCase() + String(w.format).slice(1)) : '';
        return [[nm, ly].filter(Boolean).join(' '), fmt].filter(Boolean).join(' · ');
      },
      // 결과 안에 표 모양(객체가 줄줄이 들어간 배열)이 있으면 JSON 옆에 간단 표로도 보여준다.
      // 기능마다 담는 이름이 달라(rows · series · tables …) 이름을 정해 두지 않고 먼저 나오는 것을 쓴다.
      spResultRows() {
        const r = this.spTry.out && this.spTry.out.result;
        if (!r || typeof r !== 'object') return [];
        const pick = (v) => (Array.isArray(v) && v.some((x) => x && typeof x === 'object' && !Array.isArray(x))) ? v : null;
        let rows = pick(r) || pick(r.rows);
        if (!rows) { for (const k of Object.keys(r)) { const v = pick(r[k]); if (v) { rows = v; break; } } }
        return (rows || []).filter((x) => x && typeof x === 'object' && !Array.isArray(x)).slice(0, 50);
      },
      spResultCols() {
        const cols = [];
        this.spResultRows().forEach((r) => Object.keys(r).forEach((k) => { if (cols.indexOf(k) < 0) cols.push(k); }));
        return cols.slice(0, 6);
      },
      spCell(row, k) {
        const v = row ? row[k] : null;
        if (v == null) return '·';
        return (typeof v === 'object') ? this.spJson(v) : String(v);
      },
      // 응답에 스냅샷 정보가 실려 오면(Iceberg 표) 어느 시점의 표를 읽었는지 한 줄로 알린다
      spSnap() {
        const r = this.spTry.out && this.spTry.out.result;
        return (r && typeof r === 'object' && this.spHasJson(r.snapshot)) ? r.snapshot : null;
      },
      // 연결 스니펫에 넣을 키: 방금 발급한 키가 있으면 실제 값, 없으면 자리 표시
      get spSnipKey() { return (this.spNewKey && this.spNewKey.key) || '<접속 키>'; },
      // 도구별 연결 방법 · 발급 직후에는 진짜 키가 들어간 채로 복사된다
      get spGuides() {
        const u = this.spGwUrl, key = this.spSnipKey;
        const hdr = { Authorization: 'Bearer ' + key };
        return [
          { id: 'cc', name: 'Claude Code', desc: '터미널에서 아래 한 줄이면 붙습니다',
            code: 'claude mcp add --transport http spectrum ' + u + ' --header "Authorization: Bearer ' + key + '"' },
          { id: 'cd', name: 'Claude Desktop', desc: '설정 파일(claude_desktop_config.json)에 아래를 넣습니다',
            code: this.spJson({ mcpServers: { spectrum: { type: 'http', url: u, headers: hdr } } }) },
          { id: 'cursor', name: 'Cursor', desc: '작업 폴더의 .cursor/mcp.json 에 아래를 넣습니다',
            code: this.spJson({ mcpServers: { spectrum: { url: u, headers: hdr } } }) },
          { id: 'gpt', name: 'ChatGPT', desc: '설정 · 커넥터에서 원격 MCP 서버를 새로 추가하고 아래 주소와 키를 넣습니다',
            code: '주소(URL): ' + u + '\n인증 헤더: Authorization: Bearer ' + key },
        ];
      },
}));
