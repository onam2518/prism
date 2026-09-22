/* Prism 앱 조각 07 · prismApp 프로퍼티 그룹(자동 분할 · 앱 분할 6차).
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
      eventHint() {
        const v = this.settingsDraft.co_min;
        if (v <= 1) return '조금만 겹쳐도 한 사건으로 묶습니다 · 사건형 토픽이 많아집니다';
        if (v >= 3) return '확실히 많이 겹칠 때만 묶습니다 · 사건형 토픽이 적어집니다';
        return '적당히 겹칠 때 묶습니다 (기본)';
      },
      entityHint() {
        const v = this.settingsDraft.entity_min;
        if (v <= 1) return '기사가 1건이라도 그 인물·브랜드를 토픽으로 만듭니다 · 아주 촘촘';
        if (v >= 3) return '기사가 3건 이상 쌓였을 때만 토픽으로 만듭니다 · 엄선';
        return '기사가 2건 이상일 때 토픽으로 만듭니다 (기본)';
      },
      async studioSave() {
        if (!this.studio.name.trim()) { this.studioMsg = '토픽 이름을 입력하세요'; return; }
        this.studioSaving = true; this.studioMsg = '저장 중…';
        try {
          const r = await this._studioPost({ action: 'save', def: this.studioDef() });
          if (r && r.error) { this.studioMsg = '오류: ' + r.error; }
          else {
            this.topicData = r; this._syncTopicSettings(); this.studioMsg = '저장했습니다'; this.studioReset();
            const dup = (r.similar || [])[0];        // 중복 의심: 저장은 되고 경고만(합칠지 판단은 사람이)
            if (dup) this.liveToast('⚠ 비슷한 토픽이 이미 있어요 · 「' + dup.name + '」 (' + Math.round(dup.score * 100) + '% 유사) · 겹치면 하나로 합쳐 주세요');
          }
        } catch (e) { this.studioMsg = '저장 실패'; } this.studioSaving = false;
      },
      // ── 말로 만들기(스펙 132112 화면 1): 문장 → suggest → preview → 칩 · 건수 · 표본 · 문장을 보태 다듬기 ──
      _applySuggest(s) {
        const st = this.studio; let negN = 0;
        ['cats', 'intents', 'keywords', 'srcs'].forEach(dim => {
          st[dim] = st[dim] || []; st.req[dim] = st.req[dim] || []; st.neg[dim] = st.neg[dim] || [];
          (s[dim] || []).forEach(v => { if (!st[dim].includes(v)) st[dim].push(v); });
          // 필수(req): 자동생성이 필수로 지정한 값을 필수 상태로(선택은 그대로 선택)
          (((s.req) || {})[dim] || []).forEach(v => { if (st[dim].includes(v) && !st.req[dim].includes(v)) st.req[dim].push(v); });
          // 제외(neg): 배제 표현의 대상 → 제외 상태로(선택 · 필수와 상충 시 제외 우선)
          (((s.neg) || {})[dim] || []).forEach(v => {
            const si = st[dim].indexOf(v); if (si >= 0) st[dim].splice(si, 1);
            const ri = st.req[dim].indexOf(v); if (ri >= 0) st.req[dim].splice(ri, 1);
            if (!st.neg[dim].includes(v)) { st.neg[dim].push(v); negN++; }
          });
        });
        (s.eattrs || []).forEach(v => { if (!st.eattrs.includes(v)) st.eattrs.push(v); });
        // 원천 조건(feed): 목록은 합치고 값은 새 해석이 덮는다(빈 값은 기존 유지)
        const fd = Object.assign({}, st.feed || {}); let feedN = 0;
        Object.entries(s.feed || {}).forEach(([k, v]) => {
          if (Array.isArray(v)) { if (v.length) { fd[k] = Array.from(new Set((fd[k] || []).concat(v))); feedN += v.length; } }
          else if (k === 'base_excl') { if (v === false) { fd.base_excl = false; feedN++; } }
          else if (k === 'basis') { if (s.feed.days) fd.basis = v; }
          else if (v) { fd[k] = v; feedN++; }
        });
        st.feed = fd;
        st.auto = { cats: (s.cats || []).slice(), intents: (s.intents || []).slice(), keywords: (s.keywords || []).slice() };
        const n = ['cats', 'intents', 'keywords', 'srcs', 'eattrs'].reduce((a, d) => a + ((s[d] || []).length), 0) + negN + feedN;
        return { n, negN };
      },
      _talkChips() {
        const KO = { cats: '분야', intents: '의도', keywords: '엔티티', srcs: '출처' }; const st = this.studio; const out = [];
        const lab = (dim, v) => dim === 'cats' ? this.catBoth(v) : v;
        ['cats', 'intents', 'keywords', 'srcs'].forEach(dim => (st[dim] || []).forEach(v => out.push({ key: dim + ':' + v, dim, k: KO[dim], v: lab(dim, v), req: (st.req[dim] || []).includes(v) })));
        (st.eattrs || []).forEach(v => out.push({ key: 'eattrs:' + v, dim: 'eattrs', k: '속성', v: this.eattrLabel(v), req: true }));
        ['cats', 'intents', 'keywords', 'srcs'].forEach(dim => ((st.neg || {})[dim] || []).forEach(v => out.push({ key: 'neg:' + dim + ':' + v, dim, k: KO[dim] + ' 제외', v: lab(dim, v), neg: true })));
        ((this.studioPreview && this.studioPreview.feed_chips) || []).forEach(c => out.push({ key: 'feed:' + c.f + ':' + c.x, feed: true, f: c.f, x: c.x, k: c.k, v: c.v, neg: !!c.neg }));
        return out;
      },
      _chipRemove(c) {
        const st = this.studio;
        if (c.feed) { const fd = Object.assign({}, st.feed || {}); const cur = fd[c.f]; if (Array.isArray(cur)) fd[c.f] = cur.filter(x => x !== c.x); else if (c.f === 'base_excl') fd.base_excl = true; else fd[c.f] = (typeof cur === 'number') ? 0 : ''; st.feed = fd; }
        else if (c.dim === 'eattrs') st.eattrs = st.eattrs.filter(x => x !== c.v);
        else if (c.neg) st.neg[c.dim] = (st.neg[c.dim] || []).filter(x => x !== c.rawV);
        else { st[c.dim] = (st[c.dim] || []).filter(x => x !== c.rawV); st.req[c.dim] = (st.req[c.dim] || []).filter(x => x !== c.rawV); }
      },
      _talkPrune() { this.talk.dropped.forEach(c => this._chipRemove(c)); },
      talkN() { const t = this.talk.turns[this.talk.turns.length - 1]; return t ? (t.n || 0) : 0; },
      async _talkRefresh(before) {
        const p = await this._studioPost({ action: 'preview', def: this.studioDef() });
        const pv = (p && p.preview) || { bundles: [], n_total: 0, feed_chips: [] };
        this.studioPreview = pv;
        const t = this.talk.turns[this.talk.turns.length - 1]; if (!t) return;
        const chips = this._talkChips().map(c => Object.assign(c, { rawV: c.rawV || this._rawV(c) }));
        if (before) chips.forEach(c => { c.new = !before.has(c.key); });
        const core = (pv.bundles || []).find(b => b.kind === 'core') || { count: 0, samples: [] };
        t.chips = chips; t.n = core.count || 0; t.samples = (core.samples || []).slice(0, 5);
        t.neg_n = pv.neg_blocked || 0; t.feed_n = pv.feed_blocked || 0;
        const miss = pv.feed_miss || {}; const mk = Object.keys(miss);
        t.miss = mk.length ? ('데이터에 없는 필드라 빠짐 · ' + this.feedMissText(miss) + ' · 적재 때 원천 필드(src)가 채워지면 다시 계산돼요') : '';
      },
      _rawV(c) { if (c.feed || c.dim === 'eattrs') return c.v; const src = c.neg ? (this.studio.neg[c.dim] || []) : (this.studio[c.dim] || []); return src.find(v => (c.dim === 'cats' ? this.catBoth(v) : v) === c.v) || c.v; },
      async talkSend() {
        const s = (this.talk.input || '').trim(); if (!s || this.talk.busy) return;
        this.talk.busy = true; this.studioMsg = '';
        const sentences = this.talk.turns.map(t => t.text).concat([s]);
        const mid = String(this.studioModel || '').split('|').pop();
        try {
          // 누적 해석: 문장 전체를 다시 풀되, 사용자가 뺀 칩은 되살리지 않는다
          const r = await this._studioPost({ action: 'suggest', text: sentences.join(' '), model: mid });
          const before = new Set(this._talkChips().map(c => c.key));
          this._applySuggest((r && r.suggest) || {}); this._talkPrune();
          if (!this.studio.name.trim()) this.studio.name = s.replace(/[.。!?]+$/, '').slice(0, 40);
          this.studio.prompt = sentences.join(' / ');
          const prev = this.talk.turns.length ? this.talk.turns[this.talk.turns.length - 1].n : null;
          this.talk.turns.push({ text: s, chips: [], n: 0, prev, samples: [], via: (r && r.via) || '', model: mid, miss: '', neg_n: 0, feed_n: 0 });
          await this._talkRefresh(before);
          this.talk.input = '';
        } catch (e) { this.studioMsg = '해석 실패 · 다시 시도하세요'; }
        this.talk.busy = false;
      },
      async talkDrop(c) { this._chipRemove(c); this.talk.dropped.push(c); await this._talkRefresh(); },
      talkReset() { this.talk = { turns: [], input: '', busy: false, dropped: [], help: false }; this.studioReset(); },
      talkEdit(g) {
        this.studioEdit(g); this.studioManual = false;
        const def = (this.topicData.customDefs || []).find(d => d.id === g.id) || {};
        this.talk = { turns: [{ text: def.prompt || g.prompt || g.name || '', chips: [], n: g.core_count || 0, prev: null, samples: [], via: '', model: '', miss: '', neg_n: 0, feed_n: 0 }], input: '', busy: false, dropped: [], help: false };
        this._talkRefresh();
      },
      async talkSave(status) {
        if (!this.talk.turns.length || this.studioSaving) return;
        if (!this.studio.name.trim()) { this.studioMsg = '토픽 이름을 적어 주세요'; return; }
        this.studioSaving = true; this.studioMsg = '저장 중…';
        const def = this.studioDef(); def.status = status; def.talk_model = String(this.studioModel || '').split('|').pop();
        try {
          const r = await this._studioPost({ action: 'save', def, talk: true, reviewer: this.reviewer || '' });
          if (r && r.error) { this.studioMsg = '오류: ' + r.error; }
          else {
            this.topicData = r; this._syncTopicSettings();
            const sv = r.saved || {};
            const msg = sv.locked ? '지금 데이터에 0건이라 초안으로 저장했어요' : (sv.status === 'active' ? '활성화했어요 · 현황 표에서 스위치로 켜고 끕니다' : '초안으로 저장했어요');
            const dup = (r.similar || [])[0];
            if (dup) this.liveToast('⚠ 비슷한 토픽이 이미 있어요 · 「' + dup.name + '」 (' + Math.round(dup.score * 100) + '% 유사) · 겹치면 하나로 합쳐 주세요');
            this.talkReset(); this.studioMsg = msg;
          }
        } catch (e) { this.studioMsg = '저장 실패 · 다시 시도하세요'; }
        this.studioSaving = false;
      },
      // ── 원천 조건(직접 손보기) ──
      feedState(list, negList, v) { const fd = this.studio.feed || {}; if (negList && (fd[negList] || []).includes(v)) return 'neg'; return (fd[list] || []).includes(v) ? 'sel' : 'off'; },
      feedCls(list, negList, v) { const s = this.feedState(list, negList, v); return s === 'off' ? 'ds-badge--neutral' : s === 'neg' ? 'ds-badge--error is-neg' : 'ds-badge--entity'; },
      feedCycle(list, negList, v) {
        const fd = Object.assign({}, this.studio.feed || {}); const s = this.feedState(list, negList, v);
        fd[list] = (fd[list] || []).filter(x => x !== v); if (negList) fd[negList] = (fd[negList] || []).filter(x => x !== v);
        if (s === 'off') fd[list].push(v); else if (s === 'sel' && negList) fd[negList].push(v);
        this.studio.feed = fd; this.schedulePreview();
      },
      feedList(k, text) { const fd = Object.assign({}, this.studio.feed || {}); fd[k] = String(text || '').split(',').map(x => x.trim()).filter(Boolean); this.studio.feed = fd; this.schedulePreview(); },
      feedMissText(miss) { const KO = { days: '기간', types: '형식', svc_cats: '서비스 분류', creators: '작성자', image: '사진', video: '영상', len: '길이', rules: '룰', tags: '태그', dri: '열독률', cp_grades: '매체 등급', isExclusive: '단독', mainNews: '주요 뉴스', planning: '기획', isPhotoNews: '포토뉴스', subsequent: '후속', duplicate: '중복', copyNews: '복제', ads: '광고', adultImage: '성인 이미지', gutter: '선정', includePaidAd: '유료광고' }; return Object.keys(miss || {}).map(k => (KO[k] || k) + ' ' + miss[k] + '건').join(' · '); },
      // ── 토픽 현황(스펙 132112 화면 2): 상태 스위치 · 필터 · 기록 ──
      statusKo(st) { return { active: '활성', paused: '일시정지', draft: '초안', archived: '보관' }[st] || st || ''; },
      // 현황 행 필터: 상태(활성·정지·초안 | 정체·급감 | 보관) × 만든 방식(말로·직접·자동) × 검색(이름·문장·엔티티·묶음 ID)
      topicRowOk(r, kind) {
        const st = r.status || 'active'; const f = this.topicStatus;
        if (f === 'archived' ? st !== 'archived' : st === 'archived') return false;
        if (f === 'warn' && !r.signal) return false;
        const v = this.topicView;
        if (v === 'auto' && kind !== 'auto') return false;
        if ((v === 'talk' || v === 'manual') && (kind !== 'custom' || (r.via || 'manual') !== v)) return false;
        const q = (this.topicQuery || '').trim().toLowerCase();
        if (q) {
          const hay = [r.name, r.prompt, r.cluster_id].concat(r.entities || r.rep_entities || [], (r.must || []).map(m => m.v), (r.neg || []).map(m => m.v)).filter(Boolean).join(' ').toLowerCase();
          if (!hay.includes(q)) return false;
        }
        return true;
      },
      talkExample(text) { this.talk.input = text; return this.talkSend(); },
      topicSwTip(r) { const st = r.status || 'active'; return st === 'active' ? '켜짐 · 누르면 일시정지(건수는 계속 세고 유통만 멈춤)' : st === 'paused' ? '일시정지 · 누르면 켬' : st === 'draft' ? '초안 · 누르면 활성(0건이면 잠김)' : '보관 · 복구 버튼으로'; },
      logLine(log) { const e = (log || [])[log.length - 1]; if (!e) return ''; return this.fmtTs(e.ts) + (e.who ? ' ' + e.who : '') + ' · ' + e.what; },
      topicToggle(r) { const st = r.status || 'active'; return this.topicStatusSet(r, st === 'active' ? 'paused' : 'active'); },
      async topicStatusSet(r, st) {
        const id = r.id || r.cluster_id; if (!id) return;
        this.topicBusy = id; this.topicMsg = '';
        try {
          const res = await this._studioPost({ action: 'status', id, status: st, reviewer: this.reviewer || '' });
          if (res && !res.error) {
            this.topicData = res; this._syncTopicSettings();
            const sv = res.saved || {};
            this.topicMsg = sv.locked ? '지금 데이터에 0건이라 켤 수 없어요 · 초안 그대로' : ({ paused: '일시정지했어요 · 건수는 계속 세고 유통만 멈춰요', active: '켰어요', archived: '보관했어요 · 보관 필터에서 복구', draft: '초안으로 돌렸어요' }[sv.status] || '');
          } else this.topicMsg = (res && res.error) || '상태 변경 실패';
        } catch (e) { this.topicMsg = '상태 변경 실패 · 다시 시도하세요'; }
        this.topicBusy = '';
      },
      studioEdit(g) {
        this.topicGenTab = 'manual';         // 수정은 수동 정의 위저드에서
        const def = (this.topicData.customDefs || []).find(d => d.id === g.id) || {};
        const rq = def.req || { cats: [], intents: [], keywords: [] };
        const ng = def.neg || { cats: [], intents: [], keywords: [] };
        this.studio = { name: def.name || g.name || '', prompt: def.prompt || g.prompt || '', cats: [...(def.cats || [])], intents: [...(def.intents || [])], keywords: [...(def.keywords || [])], srcs: [...(def.srcs || [])], feed: Object.assign({}, def.feed || {}), eattrs: [...(def.eattrs || [])], kwInput: '', eaKey: 'gender', eaVal: '', editId: g.id, auto: { cats: [], intents: [], keywords: [] }, req: { cats: [...(rq.cats || [])], intents: [...(rq.intents || [])], keywords: [...(rq.keywords || [])], srcs: [...(rq.srcs || [])] }, neg: { cats: [...(ng.cats || [])], intents: [...(ng.intents || [])], keywords: [...(ng.keywords || [])], srcs: [...(ng.srcs || [])] } };
        this.studioMsg = ''; this.studioPreviewNow();
        try { window.scrollTo({ top: 0, behavior: 'smooth' }); } catch (e) {}
      },
      studioReset() { this.studio = { name: '', prompt: '', cats: [], intents: [], keywords: [], srcs: [], eattrs: [], feed: {}, kwInput: '', eaKey: 'gender', eaVal: '', editId: null, auto: { cats: [], intents: [], keywords: [] }, req: { cats: [], intents: [], keywords: [], srcs: [] }, neg: { cats: [], intents: [], keywords: [], srcs: [] } }; this.studioPreview = { bundles: [], n_total: (this.topicData && this.topicData.n_contents) || 0, must_n: 0, opt_n: 0 }; },
      async studioDelete(g) {
        if (!(await this.dsConfirm('토픽 “' + (g.name || g.id) + '” 을 삭제할까요?', { ok: '삭제', danger: true }))) return;
        try { const r = await this._studioPost({ action: 'delete', id: g.id }); if (r && !r.error) { this.topicData = r; this._syncTopicSettings(); if (this.studio.editId === g.id) this.studioReset(); } } catch (e) { this._err('삭제 실패'); }
      },
      async saveTopicSettings() {
        this.settingsSaving = true; this.settingsMsg = '적용 중…';
        try {
          const r = await this._studioPost({ action: 'settings', settings: { co_min: this.settingsDraft.co_min, entity_min: this.settingsDraft.entity_min } });
          if (r && r.error) { this.settingsMsg = '오류: ' + r.error; }
          else { this.topicData = r; this._syncTopicSettings(); this.settingsMsg = '적용했습니다 · 자동 토픽을 재생성했습니다'; }
        } catch (e) { this.settingsMsg = '적용 실패'; } this.settingsSaving = false;
      },
      // ── 토픽 큐레이션: 개별 콘텐츠 제외·복구(자동·사용자 토픽 공통 · 변경은 관리자) ──
      get topicAdmin() { return this.backend !== 'supabase' || !!(this.adminData && this.adminData.isAdmin); },
      async topicExclude(c) {
        if (!this.drillData || !this.drillData.topic_id || !c.hash) return;
        if (!(await this.dsConfirm('“' + (c.title || '').slice(0, 40) + '” 을(를) 이 토픽에서 제외할까요?\n매칭 조건은 그대로 두고 이 콘텐츠만 뺍니다 · 아래 ‘토픽에서 제외한 콘텐츠’에서 복구할 수 있어요', { ok: '제외', danger: true }))) return;
        try {
          const r = await this._studioPost({ action: 'exclude', id: this.drillData.topic_id, hash: c.hash, title: c.title || '', topic: this.drillData.value || '' });
          if (r && r.error) { this._err(r.error); return; }
          this.topicData = r; this._syncTopicSettings();
          this.drillData.items = this.drillData.items.filter(x => x.hash !== c.hash);
          this.drillData.n = this.drillData.items.length;
        } catch (e) { this._err('제외 실패'); }
      },
      async topicRestore(tid, h) {
        try {
          const r = await this._studioPost({ action: 'restore', id: tid, hash: h });
          if (r && r.error) { this._err(r.error); return; }
          this.topicData = r; this._syncTopicSettings();
        } catch (e) { this._err('복구 실패'); }
      },
      exclusionRows() {                                  // 관리 패널: {토픽id: [항목]} → 최신순 평탄화
        const ex = (this.topicData && this.topicData.exclusions) || {}; const out = [];
        for (const tid of Object.keys(ex)) for (const e of (ex[tid] || [])) {
          const h = typeof e === 'string' ? e : ((e && e.h) || '');
          if (h) out.push({ tid, h, title: (e && e.title) || '', topic: (e && e.topic) || tid, ts: (e && e.ts) || 0 });
        }
        out.sort((a, b) => b.ts - a.ts); return out;
      },
      qexN() { const d = this.topicData; return (d && d.n_eligible != null) ? Math.max(0, (d.n_contents || 0) - d.n_eligible) : 0; },
      async loadDict() { this.modBusy = true; try { const r = await this._afetch('/dict'); const d = await r.json(); if (r.ok && d && !d.error && d.serviceGroups) { this.dictData = d; if (!this.dictGroup) this.dictGroup = (d.serviceGroups || [])[0] || ''; } } catch (e) { this._err('사전 불러오기 실패 · 네트워크 확인 후 새로고침 해주세요'); } this.modBusy = false; },
      // 성공 응답(사전 본문)일 때만 dictData 반영 · 401 등 오류 본문을 넣으면 dictData.iabMap 등이
      // undefined 라 Object.keys() 마운트 크래시로 본문 전체가 안 뜬다(초기 토큰 준비 전 /dict 401 레이스·토큰 만료).
      // ── 엔티티 사전(별도 메뉴): 목록·필터·수동 편집(사람 확정)·위키데이터/나무위키 보강 ──
      entData: null, entQ: '', entType: '', entStatus: '', entMsg: '', entAddName: '',
      entEdit: null, entEditAliases: [], entEditContents: [], entEditMsg: '', entAliasInput: '',
      _entPollT: null,
      _entProgress(s) { return (s.done || 0) + '/' + (s.total || 0) + ' · 매칭 ' + (s.hit || 0) + ' · 미등재 ' + (s.miss || 0) + ((s.fail || 0) ? ' · 실패 ' + s.fail : ''); },
      async loadEntdict(silent) {
        if (!silent) this.modBusy = true;
        try {
          const p = new URLSearchParams();
          if (this.entQ.trim()) p.set('q', this.entQ.trim());
          if (this.entType) p.set('type', this.entType);
          if (this.entStatus) p.set('status', this.entStatus);
          this.entData = await (await this._afetch('/entdict?' + p.toString())).json();
        } catch (e) {
          // 폴링(silent)은 조용히 넘기고 다음 회차로 자력 복구 · 사용자가 부른 조회만 알린다
          if (!silent) this._err('엔티티 사전 불러오기 실패 · 네트워크 확인 후 새로고침 해주세요');
        }
        if (!silent) this.modBusy = false;
        // 일괄 보강 진척: 실행 중이면 1.5초 폴링으로 목록·카운트 실시간 갱신(수동 새로고침 불필요)
        const s = (this.entData && this.entData.enrich) || {};
        clearTimeout(this._entPollT);
        if (s.running) {
          this.entMsg = '일괄 보강 진행 중 · ' + this._entProgress(s);
          this._entPollT = setTimeout(() => this.loadEntdict(true), 1500);
        } else if (this._entPollT !== null && s.total) {
          this._entPollT = null;
          this.entMsg = '일괄 보강 완료 · ' + this._entProgress(s);
        }
      },
      async entAction(body) {
        // 호출부가 먼저 busy 메시지를 세팅하므로 여기서 예외가 새면 '저장 중…'이 영구 고정된다
        try {
          const r = await this._afetch('/entdict', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(body) });
          return await r.json();
        } catch (e) { return { ok: false, error: '네트워크 오류' }; }
      },
      entTypeLabel(t) { return t ? (((this.entData && this.entData.meta.types[t]) || t) + ' · ' + t) : '보류'; },
      entSrc(e) {   // 마지막 보강 소스(출처 컬럼): namuwiki | wikidata | ''(미조회·미스·동음이의)
        const s = e && e.attr_meta && e.attr_meta._enrich;
        return (s && s.result === 'hit') ? (s.source || '') : '';
      },
      entAttrSummary(e) {
        const a = e.attrs || {}; const out = [];
        ['gender', 'occupation', 'nationality', 'affiliation', 'org_kind', 'country', 'loc_kind', 'af_kind', 'ev_kind', 'domain'].forEach(k => { if (a[k]) out.push(a[k]); });
        return out.join(' · ');
      },
      entAttrFields() {
        const t = this.entEdit && this.entEdit.type;
        return (t && this.entData && this.entData.meta.attrFields[t]) || [];
      },
      async openEntEdit(e) {
        this.entEditMsg = ''; this.entAliasInput = '';
        const d = await this.entAction({ action: 'detail', id: e.entity_id });
        if (!d.ok) { this.entMsg = '오류: ' + (d.error || '조회 실패'); return; }
        this.entEdit = JSON.parse(JSON.stringify(d.entity));
        this.entEdit.attrs = this.entEdit.attrs || {};
        this.entEditAliases = d.aliases || []; this.entEditContents = d.contents || [];
      },
      async saveEntEdit() {
        if (!this.entEdit) return;
        this.entEditMsg = '저장 중…';
        const d = await this.entAction({ action: 'update', id: this.entEdit.entity_id, type: this.entEdit.type || '', attrs: this.entEdit.attrs || {}, alias: this.entAliasInput.trim() });
        if (!d.ok) { this.entEditMsg = '오류: ' + (d.error || ''); return; }
        this.entEdit = null; this.entMsg = '저장됨 · 수동 확정 필드는 재보강이 덮어쓰지 않습니다'; this.loadEntdict(true);
        if (this.detailOpen) this.loadEntLookup();       // 검수 상세에서 열었을 때 뱃지 정보 갱신
      },
      async entEnrich(e) {
        this.entMsg = '보강 중… (' + e.name + ')';
        const d = await this.entAction({ action: 'enrich', id: e.entity_id });
        this.entMsg = !d.ok ? ('오류: ' + (d.error || ''))
          : (d.matched ? ('매칭됨 · ' + (d.qid || (d.source === 'namuwiki' ? '나무위키' : '')) + (d.type ? (' · 타입 ' + d.type) : ''))
          : (d.ambiguous ? '동음이의 문서 · 자동 결정 없이 보류(수동 편집으로 확정)' : '위키데이터·나무위키 미등재 · 보류 유지(수동 편집으로 확정 가능)'));
        this.loadEntdict();
      },
      async entEnrichAll(scope) {
        if (scope === 'all' && !(await this.dsConfirm('전체 개체를 재보강할까요? 나무위키 우선으로 다시 조회하며, 수동 확정 필드는 보존됩니다.', { ok: '전체 재보강' }))) return;
        const d = await this.entAction({ action: 'enrich_pending', scope: scope || '' });
        if (!d.ok) { this.entMsg = '오류: ' + (d.error || ''); return; }
        if (d.mock) { this.entMsg = 'mock 모드 · 네트워크 보강은 생략됩니다'; return; }
        if (d.already_running) { this.loadEntdict(true); return; }        // 진행 중이면 진척 표시에 합류
        if (!d.queued) { this.entMsg = scope === 'all' ? '재보강할 개체가 없습니다' : '보강할 미조회 개체가 없습니다'; return; }
        this.entMsg = '일괄 보강 시작 · 0/' + d.queued;
        this.loadEntdict(true);                                           // 폴링 시작(진척 자동 갱신)
      },
      async entBackfill() {
        if (!(await this.dsConfirm('적재된 콘텐츠의 엔티티를 사전에 색인할까요? 신규 개체는 위키데이터(미스 시 나무위키)로 백그라운드 보강됩니다.', { ok: '색인' }))) return;
        this.entMsg = '색인 중…';
        const d = await this.entAction({ action: 'backfill' });
        this.entMsg = d.ok ? ('색인 완료 · 콘텐츠 ' + d.scanned + '건 스캔 · 신규 개체 ' + d.created + ' · 링크 ' + d.linked) : ('오류: ' + (d.error || ''));
        this.loadEntdict(true);                                           // 보강이 시작됐으면 폴링이 이어받음
      },
      async entDelete(e) {
        if (!(await this.dsConfirm('“' + e.name + '” 개체를 사전에서 삭제할까요? 콘텐츠 링크도 함께 삭제됩니다.', { ok: '삭제', danger: true }))) return;
        await this.entAction({ action: 'delete', id: e.entity_id });
        this.loadEntdict();
      },
      async entPurgeUnlisted() {
        const n = (this.entData && this.entData.stats && this.entData.stats.unlisted) || 0;
        if (!(await this.dsConfirm('미등재 개체 ' + n + '건을 일괄 삭제할까요? 콘텐츠 링크·별칭도 함께 삭제됩니다. 다빈도 개체(진짜 개체 후보)는 먼저 수동 확정을 권장합니다.', { ok: '정리', danger: true }))) return;
        const d = await this.entAction({ action: 'purge_unlisted' });
        this.entMsg = d.ok ? ('미등재 ' + (d.purged || 0) + '건 정리됨') : ('오류: ' + (d.error || ''));
        this.loadEntdict();
      },
      async entAdd() {
        const name = (this.entAddName || '').trim();
        if (!name) return;
        const d = await this.entAction({ action: 'add', name });
        if (!d.ok) { this.entMsg = '오류: ' + (d.error || ''); return; }
        this.entAddName = ''; this.entMsg = '등재됨(보류) · 행의 보강 버튼으로 위키데이터 조회'; this.loadEntdict();
      },
      // 사전·정책 편집(사용자 직접 수정)
      editT: null, editKey: null, editKind: 'list', editVal: '', editTitle: '', editMsg: '', editExtra: '', editFilter: '',
      startEdit(target, key, value, kind, title) {
        this.editT = target; this.editKey = key; this.editKind = kind || 'list'; this.editTitle = title || target; this.editMsg = ''; this.editExtra = ''; this.editFilter = '';
        this.editVal = (kind === 'text') ? (value || '') : (Array.isArray(value) ? value.join('\n') : '');
      },
      startEditLegal(code, v) { this.startEdit('legal_types', code, (v && v.label) || '', 'text', '법령 · ' + code); this.editExtra = (v && v.article) || ''; },
      startEditIntake(type, row) { this.startEdit('intake_policy', type, (row && row.method) || '', 'intake', '인입 정책 · ' + type); this.editFilter = (row && row.filter) || ''; this.editExtra = (row && row.status) || ''; },
      cancelEdit() { this.editT = null; this.editMsg = ''; },
      async saveEdit() {
        let value = this.editKind === 'text' || this.editKind === 'intake' ? this.editVal : this.editVal.split('\n').map(s => s.trim()).filter(Boolean);
        if (this.editT === 'legal_types') value = { label: this.editVal, article: this.editExtra };
        if (this.editT === 'intake_policy') value = { filter: this.editFilter, method: this.editVal, status: this.editExtra };
        const body = { target: this.editT, value }; if (this.editKey != null) body.key = this.editKey;
        this.editMsg = '저장 중…';
        try {
          const r = await this._afetch('/dict', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(body) });
          const d = await r.json();
          if (d.error) { this.editMsg = '오류: ' + d.error; return; }
          this.dictData = d; this.editT = null;
        } catch (e) { this.editMsg = '오류: ' + e; }
      },
      async resetDict() {
        if (!(await this.dsConfirm('사전 편집을 모두 초기화할까요? (베이스 사전은 재시작 시 완전 복원)', { ok: '초기화', danger: true }))) return;
        try { const r = await this._afetch('/dict', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ reset: true }) }); const d = await r.json(); if (r.ok && d && !d.error && d.serviceGroups) this.dictData = d; } catch (e) {}
      },
      get tr() { return (this.result && this.result.output && this.result.output.trace) || {}; },
      isRouter(p) { return p === 'bizrouter' || p === 'timely'; },
      // ── 모델 선택(Atelier 방식): 소스별 그룹 + 연결된 제공자만 활성 ──
      get textGroups() {
        return [
          { label: '직접 · Solar', on: !!this.cfg.hasKey, items: this.modelOptions.map((m) => ({ provider: 'solar', model: m })) },
          { label: '통합 · Timely', on: !!this.cfg.hasTimelyKey, items: this.modelCatalog.timely.text.map((m) => ({ provider: 'timely', model: m })) },
          { label: '통합 · BizRouter', on: !!this.cfg.hasBizKey, items: this.modelCatalog.bizrouter.text.map((m) => ({ provider: 'bizrouter', model: m })) },
        ];
      },
      get textValue() { return this.textProvider === 'solar' ? ('solar|' + (this.cfgModel || '')) : (this.textProvider + '|' + (this.textModel || '')); },
      onTextPick(v) {
        const i = v.indexOf('|'); const p = v.slice(0, i), m = v.slice(i + 1);
        this.textProvider = p; if (p === 'solar') this.cfgModel = m; else this.textModel = m;
        this.saveTextSlot();
      },
      get excelLabel() { return this.excelFile ? this.excelFile.name : '선택된 파일 없음'; },
      onExcel(e) { this.excelFile = e.target.files[0] || null; e.target.value = ''; this.status = ''; },
      onDropExcel(e) { this.xlsDrag = false; const f = e.dataTransfer.files[0]; if (f) this.excelFile = f; },

      // ── 결과 복사 / 내보내기 ──
}));
