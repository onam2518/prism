/* Prism 앱 조각 10 · 하이브리드 선택형 입력(최종 검수 수정 · 시안 3).
   추천 칩 상시 + 검색 드롭다운 병행 · 정답 확정 팝업(05-review)과 엔티티 수정(20-ingest-policy) 공용.
   후보 원천 = /dict(dictData · 인텐트 범용①②+서비스 분기 · IAB Tier1/Tier2 · 한글 표시명)
   + /entdict(등재 개체 · 검색·추천용 캐시). 저장 계약 불변(카테고리=영문 경로 배열 · 인텐트=사전 표기 배열). */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
      // ── 픽커 인스턴스 상태(id → {q, open, free[]}) · free = "새 엔티티로 추가"로 넣은 사전 밖 값(신규 표시) ──
      hyb: {},
      hybSt(id) { if (!this.hyb[id]) this.hyb[id] = { q: '', open: false, free: [] }; return this.hyb[id]; },
      hybInit(ids) { (ids || []).forEach((id) => { this.hyb[id] = { q: '', open: false, free: [] }; }); },
      hybFree(id, v) { return this.hybSt(id).free.includes(v); },
      hybDisp(kind, v) { return kind === 'category' ? this.catKo(v) : v; },   // 칩·드롭다운 표시(카테고리만 한글) · 값은 영문 경로 유지
      hybAdd(id, sel, v, free) {
        v = String(v || '').trim();
        if (!v || !sel || sel.includes(v)) return;
        sel.push(v);
        const st = this.hybSt(id);
        if (free && !st.free.includes(v)) st.free.push(v);
        st.q = ''; st.open = false;
      },
      hybRemove(id, sel, v) {
        const i = sel.indexOf(v); if (i >= 0) sel.splice(i, 1);
        const st = this.hybSt(id); const j = st.free.indexOf(v); if (j >= 0) st.free.splice(j, 1);
      },
      hybInput(id, v) { const st = this.hybSt(id); st.q = v; st.open = !!String(v).trim(); },
      hybEnter(id, fld, ctx, sel) {                // Enter = 첫 후보 · 매칭 없고 자유 입력 허용(엔티티)이면 신규 추가
        const m = this._hybMatches(id, fld.kind, ctx, sel);
        if (m.length) this.hybAdd(id, sel, m[0].v);
        else if (fld.free && this.hybFreeAdd(id, sel)) this.hybAdd(id, sel, this.hybFreeAdd(id, sel), true);
      },
      hybEsc(id, ev) {                             // Esc = 드롭다운만 닫기(닫혀 있으면 팝업 Esc 로 전파)
        const st = this.hybSt(id);
        if (st.open) { st.open = false; ev.stopPropagation(); }
      },
      // ── 후보 그룹(사전 전량 · 드롭다운 검색 대상): kind → [{label, items:[{v, ko, def, tag}]}] ──
      hybGroups(kind, service) {
        const d = this.dictData;
        if (!d) { if (!this._dictReq) { this._dictReq = true; this.loadDict(); } return []; }
        if (kind === 'intent') {
          const key = (d.serviceKeyMap || {})[service] || service || '';
          const mk = (v) => ({ v: v, ko: '', def: (d.intentDefs || {})[v] || '', tag: '' });
          const out = [
            { label: '범용① 소비 방식', items: (d.intentUniversal || []).map(mk) },
            { label: '범용② 형식·전달', items: (d.intentForm || []).map(mk) },
          ];
          const own = (d.intentByService || {})[key] || [];
          if (own.length) out.push({ label: '서비스 분기 · ' + key, items: own.map(mk) });
          return out;
        }
        if (kind === 'category') {                 // 그룹 = Tier1(한글·영문 병기) · 항목 = Tier1 단독 + Tier1/Tier2 경로
          return (d.iabTier1 || []).map((t1) => ({
            label: ((d.tier1Ko || {})[t1] || t1) + ' · ' + t1,
            items: [{ v: t1, ko: this.catKo(t1), def: 'Tier1 단독 지정', tag: '' }]
              .concat(((d.tier2 || {})[t1] || []).map((t2) => ({ v: t1 + ' / ' + t2, ko: this.catKo(t1 + ' / ' + t2), def: '', tag: '' }))),
          }));
        }
        return [{ label: '엔티티 사전 등재분', items: this.hybEntList().map((e) => ({ v: e.name, ko: '', def: '', tag: e.type || '' })) }];
      },
      _hybMatches(id, kind, ctx, sel) {            // 검색 매칭(한글·영문·정의 동시) · 선택분 제외 · 상한 30
        const q = (this.hybSt(id).q || '').trim().toLowerCase();
        if (!q) return [];
        const out = [];
        this.hybGroups(kind, (ctx && ctx.service) || '').forEach((g) => g.items.forEach((it) => {
          if ((sel || []).includes(it.v)) return;
          if (it.v.toLowerCase().indexOf(q) >= 0 || (it.ko || '').toLowerCase().indexOf(q) >= 0
              || (it.def || '').toLowerCase().indexOf(q) >= 0) out.push(Object.assign({ group: g.label }, it));
        }));
        return out.slice(0, 30);
      },
      hybDrop(id, kind, ctx, sel) {                // 드롭다운 렌더용: 매칭을 그룹 헤더 유지한 채 재그룹
        const gs = [];
        this._hybMatches(id, kind, ctx, sel).forEach((it) => {
          let g = gs.find((x) => x.label === it.group);
          if (!g) { g = { label: it.group, items: [] }; gs.push(g); }
          g.items.push(it);
        });
        return gs;
      },
      hybFreeAdd(id, sel) {                        // "새 엔티티로 추가" 후보(엔티티 전용): 사전·선택에 정확 일치가 없을 때만
        const q = (this.hybSt(id).q || '').trim();
        if (!q || (sel || []).includes(q)) return '';
        return this.hybEntList().some((e) => e.name === q) ? '' : q;
      },
      // ── 추천(클라이언트 · 결정적 규칙 · 서버 호출 없음): 사유 라벨 동반 ──
      _hybTokens(v) { return String(v || '').split(/[^0-9A-Za-z가-힣]+/).filter((t) => t.length >= 2); },
      _hybHit(v, text) { return this._hybTokens(v).some((t) => text.indexOf(t.toLowerCase()) >= 0); },
      hybReco(kind, ctx, sel) {
        const d = this.dictData;
        if (!d || !ctx) return [];
        const lead = ((ctx.title || '') + ' ' + (ctx.summary || '')).toLowerCase();
        const out = [];
        const push = (v, why) => { if (!(sel || []).includes(v) && !out.some((r) => r.v === v)) out.push({ v: v, why: why }); };
        if (kind === 'intent') {                   // 서비스 분기 전량 + 리드문·제목 토큰 매칭 범용값 · 최대 8
          const key = (d.serviceKeyMap || {})[ctx.service] || ctx.service || '';
          const own = (d.intentByService || {})[key] || [];
          own.filter((v) => this._hybHit(v, lead)).forEach((v) => push(v, '리드문 매칭'));
          own.forEach((v) => push(v, '서비스 분기'));
          [].concat(d.intentUniversal || [], d.intentForm || [])
            .filter((v) => this._hybHit(v, lead)).forEach((v) => push(v, '리드문 매칭'));
          return out.slice(0, 8);
        }
        if (kind === 'category') {                 // 리드문 토큰 매칭(한글 표시명) + 기존 라벨의 Tier1 하위 경로 · 최대 8
          const t1s = [];
          [].concat(sel || [], ctx.category || []).forEach((c) => {
            const t1 = String(c).split('/')[0].trim();
            if ((d.iabTier1 || []).includes(t1) && !t1s.includes(t1)) t1s.push(t1);
          });
          const paths = (t1) => ((d.tier2 || {})[t1] || []).map((t2) => t1 + ' / ' + t2);
          (d.iabTier1 || []).forEach((t1) => paths(t1).forEach((p) => { if (this._hybHit(this.catKo(p), lead)) push(p, '리드문 매칭'); }));
          t1s.forEach((t1) => paths(t1).forEach((p) => push(p, '기존 라벨 하위')));
          return out.slice(0, 8);
        }
        // entity: 사전 등재분 중 리드문·본문에 등장하는 것 · 최대 6
        const body = (ctx.body || '').toLowerCase();
        this.hybEntList().forEach((e) => {
          const n = (e.name || '').toLowerCase();
          if (n.length < 2) return;
          if (lead.indexOf(n) >= 0) push(e.name, '리드문 매칭');
          else if (body.indexOf(n) >= 0) push(e.name, '본문 매칭');
        });
        return out.slice(0, 6);
      },
      // ── 엔티티 사전 캐시(검색·추천 공용): /entdict 등재분(최근 갱신순 상한 500) 1회 로드 ──
      hybEnt: null, _hybEntReq: false,
      hybEntList() { if (!this.hybEnt) { this.hybEntLoad(); return []; } return this.hybEnt; },
      async hybEntLoad() {
        if (this._hybEntReq) return;
        this._hybEntReq = true;
        try { const r = await (await this._afetch('/entdict?limit=500')).json(); if (r && r.items) this.hybEnt = r.items; } catch (e) {}
      },
      // 정답 확정 팝업 필드 사양(분류·의도·엔티티) · 05-review 템플릿 반복의 단일 원천
      get faFields() {
        return [
          { id: 'cat', key: 'cats', kind: 'category', label: '분류', pol: '사전 한정 · 표시는 한글, 저장은 영문 경로', free: false, ph: '카테고리 검색 · 한글·영문 모두 · 예) 야구, base' },
          { id: 'int', key: 'intent', kind: 'intent', label: '의도', pol: '사전 한정 · 범용①+②+서비스 분기', free: false, ph: '인텐트 검색 · 예) 경기, 리뷰' },
          { id: 'ent', key: 'entities', kind: 'entity', label: '엔티티', pol: '사전 추천 + 새 엔티티 자유 입력', free: true, ph: '엔티티 검색 · 사전에 없으면 Enter = 새 엔티티로 추가' },
        ];
      },
}));
