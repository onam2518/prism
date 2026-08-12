/* Prism 앱 조각 16 · prismApp 프로퍼티 그룹 — MCP 파트너 키(트랙 B · 외부 MCP).
   백엔드는 GET /mcp-keys · POST /mcp-key-new · POST /mcp-key-revoke (로직은 prism/mcpkeys.py).
   화면 규칙 하나만 지키면 된다: **평문 키는 발급 직후 한 번만** 보여주고, 목록에는 접두 6자만.
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
      // ── 실험실 · MCP 키 탭 ──
      labMcpView: 'keys',                           // MCP 탭 서브뷰: keys(내 키) | connect(연결 방법)
      mkKeys: [],                                   // 목록(비밀 없음 · 접두 6자 + 사용량)
      mkMeta: { max: 5, default_days: 90, max_days: 365, per_min: 60, per_day: 5000, hint: '' },
      mkNew: { days: 90, label: '' },
      mkBusy: false, mkMsg: '',
      mkIssued: null,                               // 방금 발급한 평문 키(이 화면을 벗어나면 영영 못 본다)
      mkCopied: false,
      async mkLoad() {
        try {
          const r = await (await this._afetch('/mcp-keys', { headers: this._authHeaders() })).json();
          if (r && r.ok) {
            this.mkKeys = r.items || [];
            this.mkMeta = { max: r.max, default_days: r.default_days, max_days: r.max_days, per_min: r.per_min, per_day: r.per_day, hint: r.hint || '' };
            if (!this.mkNew.days) this.mkNew.days = r.default_days;
          }
        } catch (e) { this.mkMsg = '목록을 불러오지 못했습니다'; }
      },
      get mkLive() { return (this.mkKeys || []).filter((k) => !k.revoked && !k.expired); },
      mkFull() { return this.mkLive.length >= (this.mkMeta.max || 5); },
      async mkIssue() {
        if (this.mkBusy) return;
        this.mkBusy = true; this.mkMsg = ''; this.mkIssued = null; this.mkCopied = false;
        try {
          const r = await (await this._afetch('/mcp-key-new', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(this.mkNew) })).json();
          if (r && r.ok) { this.mkIssued = r; this.mkNew.label = ''; await this.mkLoad(); }
          else this.mkMsg = (r && r.error) || '발급하지 못했습니다';
        } catch (e) { this.mkMsg = '발급하지 못했습니다'; }
        this.mkBusy = false;
      },
      async mkRevoke(k) {
        // 폐기는 되돌릴 수 없고 호출측이 즉시 끊긴다 → 한 번 되묻는다(배포 키 삭제와 같은 관례).
        if (!window.confirm('키 ' + (k.prefix || '') + ' 를 폐기합니다.\n이 키를 쓰는 호출은 즉시 끊기고 되돌릴 수 없습니다.')) return;
        try {
          const r = await (await this._afetch('/mcp-key-revoke', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ key_id: k.key_id }) })).json();
          if (!(r && r.ok)) this.mkMsg = (r && r.error) || '폐기하지 못했습니다';
        } catch (e) { this.mkMsg = '폐기하지 못했습니다'; }
        await this.mkLoad();
      },
      mkCopy() {
        if (!(this.mkIssued && this.mkIssued.key)) return;
        this.copyText(this.mkIssued.key, 'MCP 키');
        this.mkCopied = true;
      },
      mkDismiss() {
        // 닫으면 평문은 메모리에서도 사라진다(다시 조회할 방법이 없다는 것을 동작으로도 지킨다).
        if (!this.mkCopied && !window.confirm('아직 복사하지 않았습니다. 닫으면 이 키를 다시 볼 수 없습니다. 닫을까요?')) return;
        this.mkIssued = null; this.mkCopied = false;
      },
      mkWhen(ts) {
        if (!ts) return '기록 없음';
        const d = new Date(ts * 1000);
        return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
      },
      mkLeft(k) {
        if (k.revoked) return '폐기됨';
        if (k.expired) return '만료됨';
        const d = Math.ceil((k.expires_at * 1000 - Date.now()) / 86400000);
        return d <= 0 ? '오늘 만료' : (d + '일 남음');
      },
      mkState(k) { return k.revoked ? 'ds-badge--neutral' : (k.expired ? 'ds-badge--warning' : 'ds-badge--success'); },
}));
