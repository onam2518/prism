/* Prism 앱 조각 03 · prismApp 프로퍼티 그룹(자동 분할 · 앱 분할 6차).
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
      openDetail(c) {
        this.finalCtx = null;                    // 최종검수 결정 바는 최종 검수 탭 진입(openFinalDetail)에서만
        this.detailNav = null; this.detail = Object.assign({ entities: [], intent: [], category: [], reasons: [], fb: {} }, c); if (!this.detail.fb) this.detail.fb = {};
        // 결과 목록 등 집계 경로의 fb 는 팀 집계뿐(mine 없음) → /raw 사본에 같은 콘텐츠가 있으면
        // 내 표가 담긴 fb 로 교체(상세의 '완료' 게이팅·프리필이 내 표 기준으로 일관 · 2026-07-10)
        if (this.detail.fb.mine === undefined) {
          const fresh = (((this.rawData || {}).items) || []).find((r) => r.hash === this.detail.hash);
          if (fresh && fresh.fb) this.detail.fb = Object.assign({}, fresh.fb);
        }
        this.editVerdict = false; this.pendingBad = false; this.detailBack = this.drillOpen; this.detailOpen = true; this.drillOpen = false; this.histItems = []; if (this.histOpen) this.loadHistory();
        this.loadEntLookup();                    // 엔티티 → 개체 사전 정보(타입·속성) 표시
      },
      // ── 검수 상세 × 엔티티 사전: 뱃지에 타입·속성 표시 · 클릭 = 상세 팝업(수정·보강 가능) ──
      entLookup: {},
      async loadEntLookup() {
        this.entLookup = {};
        const names = (this.detail && this.detail.entities) || [];
        if (!names.length) return;
        try {
          const r = await (await this._afetch('/entdict-lookup?names=' + encodeURIComponent(names.join('|')))).json();
          if (r && r.ok) this.entLookup = r.entities || {};
        } catch (e) {}
      },
      entLkTip(name) {
        const e = this.entLookup[name];
        if (!e) return '개체 사전 미등재 · 클릭해 등재·확정할 수 있습니다(관리자)';
        const t = e.type ? ((this.entData && this.entData.meta ? this.entData.meta.types[e.type] : e.type) || e.type) : '타입 보류';
        const a = this.entAttrSummary(e);
        return '개체 사전 · ' + t + (a ? (' · ' + a) : '') + ' · 클릭해 상세·수정';
      },
      async openEntByName(name) {
        const e = this.entLookup[name];
        if (!e) { this._err('개체 사전에 등재되지 않은 엔티티입니다 · 사전·정책 > 엔티티에서 등재하세요'); return; }
        if (!this.entData) { try { this.entData = await (await this._afetch('/entdict?limit=1')).json(); } catch (er) {} }
        this.openEntEdit(e);
      },
      async entEnrichInPopup() {
        if (!this.entEdit) return;
        this.entEditMsg = '보강 중…';
        const d = await this.entAction({ action: 'enrich', id: this.entEdit.entity_id });
        this.entEditMsg = !d.ok ? ('오류: ' + (d.error || ''))
          : (d.matched ? ('보강됨 · ' + (d.source === 'namuwiki' ? '나무위키' : (d.qid || '위키데이터'))) : (d.ambiguous ? '동음이의 · 보류' : '미등재 · 보류'));
        const det = await this.entAction({ action: 'detail', id: this.entEdit.entity_id });
        if (det.ok) { this.entEdit = JSON.parse(JSON.stringify(det.entity)); this.entEdit.attrs = this.entEdit.attrs || {}; this.entEditAliases = det.aliases || []; this.entEditContents = det.contents || []; }
        this.loadEntLookup();
      },
      // 작업 이력(판정·교정·재실행 타임라인): 접이식 · 열려 있으면 항목 이동·판정 후 자동 갱신
      histOpen: false, histBusy: false, histItems: [],
      toggleHistory() { this.histOpen = !this.histOpen; if (this.histOpen) this.loadHistory(); },
      async loadHistory() {
        if (!(this.detail && this.detail.hash)) return;
        const h = this.detail.hash;
        this.histBusy = true;
        try {
          const r = await (await fetch('/history?hash=' + encodeURIComponent(h), { headers: this._authHeaders() })).json();
          if (r && r.ok && this.detail && this.detail.hash === h) this.histItems = r.items || [];
        } catch (e) {}
        this.histBusy = false;
      },
      histWhen(ts) { return ts ? new Date(ts * 1000).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''; },
      editVerdict: false, pendingBad: false, detailBack: false,
      // 정확 = 즉시 완료 · 수정 필요 = 요소·사유 입력 후 '완료 처리' 로만 확정(누른다고 바로 저장 안 함)
      reviewGood() { this.pendingBad = false; this.setFeedback(this.detail, 'good'); this.editVerdict = false; this._afterVerdict(); },
      reviewBadComplete() {
        if (!(this.detail.fb.note || '').trim()) { this._err('무엇을 왜 고쳐야 하는지 입력하세요'); return; }
        const cur = this.myVerdict(this.detail.fb);
        this.detail.fb = this._fbRecount(Object.assign({}, this.detail.fb, { mine: 'bad', ts: Date.now() / 1000 }), cur, 'bad');
        this._syncFbByHash(this.detail.hash, this.detail.fb);
        this.saveFbNote(this.detail);
        this.pendingBad = false; this.editVerdict = false;
        this._afterVerdict();
      },
      // 태그 용어 정의(호버 툴팁): 인텐트=사전(/dict intentDefs · 단일 원천) · 품질 사유=사전(dictData) · 카테고리=경로
      get INTENT_DEF() {
        const d = this.dictData;
        if (!d) { if (!this._dictReq) { this._dictReq = true; this.loadDict(); } return {}; }
        return d.intentDefs || {};
      },
      // 카테고리 한글 표시(UI 전용): 값·저장·전달은 영문(공식 표기) 유지, 렌더링만 변환.
      // 사전(dictData) 미로드 시 lazy 로드 후 원문 폴백 · 미등록 값도 원문 유지.
      catKo(v) {
        if (!v) return v;
        const d = this.dictData;
        if (!d) { if (!this._dictReq) { this._dictReq = true; this.loadDict(); } return v; }
        const t1k = d.tier1Ko || {}, t2k = d.tier2Ko || {};
        const parts = String(v).split('/').map((p) => p.trim());
        if (parts.length === 1) return t1k[parts[0]] || t2k[parts[0]] || parts[0];
        return (t1k[parts[0]] || parts[0]) + ' / ' + (t2k[parts[1]] || parts[1]);
      },
      catBoth(v) { const k = this.catKo(v); return (k && k !== v) ? (k + ' (' + v + ')') : v; },   // 병기(한글/영문순)
      // 품질 사유 병기(한글/영문순 · UI·도움말 공용): 값·저장은 영문 키 유지, 렌더만 변환
      reasonBoth(v) {
        if (!v) return v;
        if (v === 'normal') return '일반 (normal)';
        const d = this.dictData;
        if (!d) { if (!this._dictReq) { this._dictReq = true; this.loadDict(); } return v; }
        const nm = (d.qualityNames || {})[v];
        return nm ? (nm + ' (' + v + ')') : v;
      },
      termDef(kind, val) {
        val = String(val == null ? '' : val).replace(/\s*\(\d+\)\s*$/, '');   // '값 (건수)' 형태 정규화
        if (kind === 'intent') return this.INTENT_DEF[val] || ('인텐트 · ' + val);
        if (kind === 'reason') {
          const d = this.dictData;
          if (d && d.qualityMetas) { const nm = (d.qualityNames && d.qualityNames[val]) || ''; return (nm ? nm + ' · ' : '') + (d.qualityMetas[val] || val); }
          if (!this._dictReq) { this._dictReq = true; this.loadDict(); }
          return '품질 사유 · ' + val;
        }
        if (kind === 'category') return '콘텐츠 카테고리 · ' + val;
        if (kind === 'grade') return val === 'G' ? '등급 G · 유통 가능' : val === 'R' ? '등급 R · 유통 제외(문제 사유 있음)' : '등급 미판정';
        if (kind === 'entity') return '핵심 개체(인물·기관·작품 등) · ' + val;
        return val;
      },
      // ── 정책 팔레트(플로팅 도움말): 검수 중 사전·정책 기준 참조 · 드래그 이동 ──
      polOpen: false, polTab: 'intent', polQ: '', polHl: '', polPos: null, polDrag: null,
      polMatch: null,                                  // 값 클릭 → 매칭 콘텐츠 목록 오버레이 {kind,value,label,items}
      polRestore() {
        try { const p = JSON.parse(localStorage.getItem('prismPolPal') || 'null'); if (p) { this.polPos = p.pos || null; this.polTab = p.tab || 'intent'; } } catch (e) {}
      },
      polSave() { try { localStorage.setItem('prismPolPal', JSON.stringify({ pos: this.polPos, tab: this.polTab })); } catch (e) {} },
      polToggle() { this.polOpen = !this.polOpen; if (this.polOpen && !this.dictData) this.loadDict(); this.polSave(); },
      polShow(kind, val) {                              // 값 태그 딥링크: 해당 정책 항목으로 점프·강조
        let v = String(val == null ? '' : val).replace(/\s*\(\d+\)\s*$/, '');
        if (kind === 'category') { const ps = v.split('/').map((s) => s.trim()); v = ps[1] || ps[0]; }   // Tier2 행 우선, 없으면 Tier1 그룹
        this.polTab = kind === 'reason' ? 'quality' : (kind === 'grade' ? 'grade' : (kind === 'category' ? 'category' : 'intent'));
        this.polQ = ''; this.polHl = v; this.polOpen = true;
        if (!this.dictData) this.loadDict();
        this.polSave();
        this.$nextTick(() => { try { const el = document.querySelector('.polpal [data-pol="' + (window.CSS && CSS.escape ? CSS.escape(v) : v) + '"]'); if (el) el.scrollIntoView({ block: 'center' }); } catch (e) {} });
      },
      // 값 클릭 → 이 메타로 분류된 콘텐츠 목록(도움말로 연결되는 모든 메타: 인텐트·카테고리·품질 사유·등급)
      async polShowMatch(kind, key, label) {
        this.polMatch = { kind, value: key, label: label || key, items: [], loading: true };
        if (!(this.rawData && this.rawData.items && this.rawData.items.length)) { try { await this.loadRaw(2000); } catch (e) {} }
        const items = ((this.rawData || {}).items) || [];
        const catParts = (v) => String(v).split('/').map((s) => s.trim());
        const hit = (r) => {
          if (kind === 'intent') return (r.intent || []).includes(key);
          if (kind === 'reason') return (r.reasons || []).includes(key);
          if (kind === 'category') return (r.category || []).some((c) => c === key || catParts(c).includes(key));
          if (kind === 'grade') return key === 'YELLOW' ? (r.review === 'yellow') : ((r.grade || '') === key);
          return false;
        };
        const list = items.filter((r) => !this.isGoldRow(r) && hit(r));
        // 현재 열린 매칭 대상이 그대로면 갱신(비동기 로드 중 다른 값 클릭 시 최신 것만 반영)
        if (this.polMatch && this.polMatch.kind === kind && this.polMatch.value === key) {
          this.polMatch.items = list; this.polMatch.loading = false;
        }
      },
      // 매칭 콘텐츠 → 상세를 새 탭으로(딥링크 ?m=create&detail=<hash>)
      openContentNewTab(hash) {
        try { const u = new URL(location.href); u.searchParams.set('m', 'create'); u.searchParams.set('detail', hash); window.open(u.toString(), '_blank'); }
        catch (e) { window.open('?m=create&detail=' + encodeURIComponent(hash), '_blank'); }
      },
      polCatGroups() {                                  // 카테고리 탭: Tier1 그룹 → Tier2 표 행(정의·예시)
        const d = this.dictData || {};
        const q = (this.polQ || '').trim().toLowerCase();
        const defs = d.tier2Defs || {};
        return (d.iabTier1 || []).map((t1) => {
          const rows = ((d.tier2 || {})[t1] || []).map((t2) => {
            const dd = defs[t2] || {};
            return { k: t2, def: dd.def || '', ex: dd.ex || '' };
          }).filter((r) => !q || (t1 + ' ' + this.catKo(t1) + ' ' + r.k + ' ' + this.catKo(r.k) + ' ' + r.def + ' ' + r.ex).toLowerCase().indexOf(q) >= 0);
          return { t1, rule: (d.categoryCriteria || {})[t1] || '', rows };
        }).filter((g) => g.rows.length);
      },
      polRows() {                                       // 인텐트·품질·등급 탭 → 표 행 [{k,t,d,ex}]
        const d = this.dictData || {};
        let out = [];
        if (this.polTab === 'intent') {
          const defs = d.intentDefs || {};
          const ex = d.intentExamples || {};
          out = Object.keys(defs).map((k) => ({ k, t: k, d: defs[k], ex: ex[k] || '' }));
        } else if (this.polTab === 'quality') {
          const qm = d.qualityMetas || {};
          const nm = d.qualityNames || {};
          out = Object.keys(qm).map((k) => ({ k, t: nm[k] ? (nm[k] + ' (' + k + ')') : k, d: qm[k],
            ex: ((d.qualityApplies || {})[k] === 'ugc') ? 'UGC' : '전체' }));
        } else {                                        // grade: 판정 계약(사전 원천 · 미제공 시 내장 폴백)
          out = (d.gradeDefs && d.gradeDefs.length)
            ? d.gradeDefs.map((g) => ({ k: g.k, t: g.t, d: g.d, ex: '' }))
            : [
              { k: 'G', t: 'G · 유통 가능', d: '품질 사유가 임계 미만. 서비스 노출 가능 판정.', ex: '' },
              { k: 'R', t: 'R · 유통 제외', d: '임계를 넘는 품질 사유가 1개 이상 확정. 사유 태그가 함께 표시됩니다.', ex: '' },
              { k: 'YELLOW', t: 'YELLOW · 판정 애매', d: '모델 확신이 낮거나 경계 사례. 사람 검수 대상으로 회수되어 검수 큐에 들어옵니다.', ex: '' },
            ];
        }
        const q = (this.polQ || '').trim().toLowerCase();
        if (q) out = out.filter((e) => (e.t + ' ' + (e.d || '') + ' ' + (e.ex || '')).toLowerCase().indexOf(q) >= 0);
        return out;
      },
      polCtx() {                                        // 열려 있는 검수 상세의 값 → 관련 정책 바로가기
        const c = this.detailOpen ? this.detail : null;
        if (!c) return [];
        const out = [];
        (c.intent || []).forEach((v) => out.push({ kind: 'intent', v: String(v) }));
        (c.category || []).forEach((v) => out.push({ kind: 'category', v: String(v) }));
        (c.reasons || []).forEach((v) => out.push({ kind: 'reason', v: String(v) }));
        if (c.grade) out.push({ kind: 'grade', v: String(c.grade) });
        return out.slice(0, 12);
      },
      polCtxLabel(x) {
        if (x.kind === 'category') return this.catKo(String(x.v).split('/')[0].trim());
        if (x.kind === 'reason') return this.reasonBoth(x.v);
        return x.v;
      },
      polStyle() { return this.polPos ? ('left:' + this.polPos.x + 'px; top:' + this.polPos.y + 'px; right:auto; bottom:auto;') : ''; },
      polDragStart(e) {
        if (e.target && e.target.closest && e.target.closest('button')) return;   // 닫기 버튼은 드래그 제외
        const box = this.$refs.polpal;
        if (!box) return;
        const r = box.getBoundingClientRect();
        this.polDrag = { dx: e.clientX - r.left, dy: e.clientY - r.top };
        const move = (ev) => {
          if (!this.polDrag) return;
          const x = Math.min(Math.max(ev.clientX - this.polDrag.dx, 8), window.innerWidth - 120);
          const y = Math.min(Math.max(ev.clientY - this.polDrag.dy, 8), window.innerHeight - 48);
          this.polPos = { x, y };
        };
        const up = () => { this.polDrag = null; this.polSave(); window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', up);
      },
      // 수정 대상 요소(id·라벨·학습 단계) · 원천 = 서버 /dict fixElements(feedback_loop 단일 원천).
      // 사전 미로드 시 lazy 로드 후 빈 목록(dictData 도착 시 Alpine 반응형으로 재렌더).
      get FIX_ELEMENTS() {
        const d = this.dictData;
        if (!d) { if (!this._dictReq) { this._dictReq = true; this.loadDict(); } return []; }
        return d.fixElements || [];
      },
      elemStage(id) { const e = this.FIX_ELEMENTS.find((x) => x.id === id); return e ? e.stage : 'analyze'; },
      elemLabel(id) { const e = this.FIX_ELEMENTS.find((x) => x.id === id); return e ? e.label : ''; },
      fmtTs(ts) {                                        // epoch(초) → 'M.D HH:mm'
        if (!ts) return '';
        const d = new Date((ts > 1e12 ? ts : ts * 1000));
        const p = (n) => (n < 10 ? '0' + n : '' + n);
        return (d.getMonth() + 1) + '.' + d.getDate() + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
      },
      // ── 배치 결과: 콘텐츠별 평가 피드백 → 학습 루프 ──
      fbNoteOpen: {},
      // 내 표 변경(cur→v)을 팀 카운트에 즉시 반영: 다음 /raw 응답 전에도 '팀 의견 · 정확 x개' 표기 정합.
      // verdict 는 서버 합의 규칙과 동일하게 재계산(다수결 · 동수 = 의견 갈림).
      _fbRecount(fb, cur, v) {
        if (cur === v) return fb;
        fb.good = Math.max(0, (fb.good || 0) + (v === 'good' ? 1 : 0) - (cur === 'good' ? 1 : 0));
        fb.bad = Math.max(0, (fb.bad || 0) + (v === 'bad' ? 1 : 0) - (cur === 'bad' ? 1 : 0));
        fb.n = fb.good + fb.bad;
        fb.verdict = fb.good > fb.bad ? 'good' : (fb.bad > fb.good ? 'bad' : (fb.n ? 'split' : ''));
        return fb;
      },
      // 추가 수정: 직전 교정(메모·선택 요소)을 남긴 채 편집을 연다(이어쓰기 · 회의 소요).
      // 저장 시 '[요소] ' 태그가 다시 붙으므로 선두 태그는 벗겨 중복을 막는다.
      openEditVerdict() {
        const fb = this.detail && this.detail.fb; if (!fb) return;
        if (fb.note) fb.note = String(fb.note).replace(/^\[[^\]]*\]\s*/, '');
        this.editVerdict = true;
        this.pendingBad = (this.myVerdict(fb) === 'bad');   // 내 표 기준(팀 합의로 새면 남의 교정 모드가 열림)
      },
      // 내 판정 취소: 내 표 행만 서버에서 삭제 · 남은 표 없으면 미검수로 복귀
      async undoVerdict() {
        const c = this.detail; if (!c || !c.fb) return;
        let cur = this.myVerdict(c.fb);
        // 로컬(sqlite) 모드는 /raw 가 내 표를 식별하지 않는다(mine 항상 빈 값) → 단독 표는 내 표로 간주
        if (!cur && this.backend !== 'supabase' && c.fb.n === 1) cur = c.fb.verdict || '';
        if (!cur) return;
        const nf = this._fbRecount(Object.assign({}, c.fb, { mine: '', ts: Date.now() / 1000 }), cur, '');
        c.fb = nf;
        this._syncFbByHash(c.hash, nf);
        this.editVerdict = false; this.pendingBad = false;
        await this._postFb({ hash: c.hash, service: c.service, title: c.title, model: c.model || '', verdict: '', stage: 'analyze', note: '' });
        if (this.histOpen) this.loadHistory();
      },
      async setFeedback(c, verdict) {
        // 재클릭 취소 비교는 '내 표(mine)' 기준. fb.verdict 는 팀 합의라 남의 표와 비교하면
        // 이미 합의가 같은 값일 때 내 첫 클릭이 취소('')로 전송되는 오동작이 난다.
        const cur = this.myVerdict(c.fb);
        const v = (cur === verdict) ? '' : verdict;        // 같은 버튼 재클릭 = 취소
        c.fb = this._fbRecount(Object.assign({}, c.fb, { mine: v, ts: (v ? Date.now() / 1000 : 0) }), cur, v);   // 수정 일시 기록
        this._syncFbByHash(c.hash, c.fb);
        if (v === 'bad') this.fbNoteOpen[c.hash] = true;
        await this._postFb({ hash: c.hash, service: c.service, title: c.title, model: c.model || '', verdict: v, stage: (c.fb.stage || 'analyze'), note: (c.fb.note || '') });
        if (cur === '' && v !== '') this.celebratePoints(10, '검수 완료');   // 새 검수 = +10 PT
      },
      // 수정 대상 요소: 다중 선택 · 서버 오케스트레이터가 원문을 재분류해 단계별로 분기
      fbElems(fb) { if (!Array.isArray(fb.elems) || !fb.elems.length) fb.elems = [fb.element || 'summary']; return fb.elems; },
      toggleFixElem(fb, id) {
        const a = this.fbElems(fb);
        const i = a.indexOf(id);
        if (i >= 0) { if (a.length > 1) a.splice(i, 1); } else a.push(id);
        fb.element = a[0];
      },
      async saveFbNote(c) {
        const hadNote = !!(c.fb && c.fb._noteRewarded);
        const els = this.fbElems(c.fb).slice();                       // 수정 대상 요소(복수 가능)
        const stage = this.elemStage(els[0]);
        const raw = (c.fb.note || '').trim();
        const tagged = raw ? ('[' + els.map((e) => this.elemLabel(e)).join('·') + '] ' + raw) : raw;
        c.fb = Object.assign({}, c.fb, { verdict: c.fb.verdict || 'bad', stage: stage, ts: Date.now() / 1000 });
        // 서버에는 '내 표'를 보낸다 · fb.verdict 는 팀 합의라 'split' 등 표가 아닌 값이 저장될 수 있다
        const myV = (c.fb.mine !== undefined ? (c.fb.mine || '') : '') || 'bad';
        await this._postFb({ hash: c.hash, service: c.service, title: c.title, model: c.model || '', verdict: myV, stage: stage, elements: els, note: tagged });
        this.fbNoteOpen[c.hash] = false;
        if (!hadNote && (c.fb.note || '').trim()) { c.fb._noteRewarded = true; this.celebratePoints(25, '교정 반영'); }  // 교정 = +25 PT(서버 산정과 일치)
      },
      async _postFb(payload) {
        payload = Object.assign({ reviewer: this.reviewer || '', name: this.reviewer || '' }, payload);  // 키+표시명(supabase 면 서버가 uid 로 덮어씀)
        try {
          const r = await (await this._afetch('/feedback', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(payload) })).json();
          if (r && r.feedback && this.dashData) this.dashData.feedback = r.feedback;
          (r && r.missions_completed || []).forEach((m) => this.celebratePoints(m.bonus, '미션 달성 · ' + m.label));
          return r;
        } catch (e) { return null; }
      },
      // ── 팀 실시간 협업: 검수자 식별 · 검수 대기 · 라이브 ──
      loadReviewer() {
        try { this.reviewer = localStorage.getItem('prism_reviewer') || ''; this.reviewerChar = localStorage.getItem('prism_reviewer_char') || 'boksil'; this.authToken = localStorage.getItem('prism_token') || ''; this.rtoken = localStorage.getItem('prism_rtoken') || ''; } catch (e) {}
        this._loadCred();                              // 저장된 아이디·비밀번호 프리필(이 기기)
        if (!this.reviewer) this.reviewerEditing = true;            // 첫 방문 → 등록/로그인 모달
      },
      _authHeaders() { const h = { 'Content-Type': 'application/json' }; if (this.authToken) h['Authorization'] = 'Bearer ' + this.authToken; return h; },
      // 인증 공통 fetch(쓰기 라우트 통일 · 회의 소요 F): 401(토큰 만료)이면 1회 자동 갱신 후
      // 재시도하고, 그래도 만료면 안내 + 로그인 모달. 기존에는 경로마다 제각각이라
      // 만료 시 판정·교정이 조용히 유실됐다. Authorization 은 호출 시점의 최신 토큰으로 덮는다.
      async _afetch(url, opts) {
        opts = opts || {};
        const call = () => {
          const h = Object.assign({}, opts.headers || {});
          if (this.authToken) h['Authorization'] = 'Bearer ' + this.authToken;
          return fetch(url, Object.assign({}, opts, { headers: h }));
        };
        let r = await call();
        if (r.status === 401 && this.rtoken && await this.authRefresh()) r = await call();
        if (r.status === 401 && this.backend === 'supabase') {
          this._err('로그인이 만료됐습니다 · 다시 로그인해 주세요');
          this.reviewerEditing = true;
        }
        return r;
      },
}));
