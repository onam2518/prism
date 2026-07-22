/* Prism 앱 조각 06 · prismApp 프로퍼티 그룹(자동 분할 · 앱 분할 6차).
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
      weeklyLeague() {
        const d = this.arenaData; if (!d || !d.leaderboard) return [];
        const board = d.leaderboard.map((r) => ({ ...r, wp: r.week_points || 0, lwp: r.last_week_points || 0 }))
          .sort((a, b) => b.wp - a.wp);
        const n = board.length;
        const upN = Math.max(1, Math.ceil(n * 0.3));
        const downN = n >= 5 ? Math.max(1, Math.floor(n * 0.2)) : 0;
        return board.map((r, i) => ({ ...r, rank: i + 1, delta: r.wp - r.lwp,
          zone: r.wp <= 0 ? 'idle' : (i < upN ? 'up' : (downN && i >= n - downN ? 'down' : 'keep')) }));
      },
      leagueActive() { return this.weeklyLeague().filter((r) => r.wp > 0).length; },
      leagueZoneKr(z) { return { up: '승급권', down: '강등권', keep: '유지권', idle: '대기' }[z] || ''; },
      leagueZoneLabel(z) { return { up: '▲ 승급', down: '▼ 강등', keep: '유지', idle: '대기' }[z] || ''; },
      leagueZoneClass(z) { return { up: 'ds-badge--success', down: 'ds-badge--category', keep: 'ds-badge--neutral', idle: 'ds-badge--neutral' }[z] || 'ds-badge--neutral'; },
      // 캐릭터 육성: 레벨 → 성장 티어·타이틀·XP
      levelTier(L) { return L >= 10 ? 4 : L >= 7 ? 3 : L >= 4 ? 2 : L >= 2 ? 1 : 0; },
      levelTitle(L) { return ['새내기 검수자', '숙련 검수자', '베테랑 검수자', '검수 마스터', '전설의 검수자'][this.levelTier(L)]; },
      levelEmoji(L) { return ['🌱', '🔰', '⭐', '🏆', '👑'][this.levelTier(L)]; },
      // 게이미피케이션(KB 프레임워크 적용): Flow 단계 + 배지 컬렉션(성취) + 오늘의 미션(도전)
      flowStage(L) { return L >= 10 ? 'Master' : L >= 4 ? 'Regular' : 'Rookie'; },
      flowStageKr(L) { return L >= 10 ? '마스터' : L >= 4 ? '정착' : '입문'; },
      badges() {
        const m = this.arenaMe; const r = (m&&m.reviews)||0, c = (m&&m.corrections)||0, s = (m&&m.streak)||0, L = (m&&m.level)||0;
        // 배지 세트(22) · 5분류: 볼륨·스트릭·기여·품질·지위. 개인 1만 건 검수 완주 시 전 배지 달성 규모.
        // 품질 배지는 개인 실측(골드 정확도·합의·불일치 해소)
        // 기반 = 유능감 정보 제공(Sailer 2017 · Ryan & Deci 2000). cur/target/unit = 진행도(모달 표시용).
        const gn = (m&&m.gold_n)||0, ga = (m&&m.gold_acc)||0, cm = (m&&m.consensus_matches)||0, sr = (m&&m.split_reviews)||0;
        const gap = Math.round(ga * 100);
        return [
          { icon: '🌱', label: '첫 검수', desc: '첫 검수를 완료했어요', exp: 10, color: '#18ba45', cat: '볼륨', cur: r, target: 1, unit: '검수', got: r >= 1 },
          { icon: '📖', label: '검수 50', desc: '누적 50건 검수', exp: 50, color: '#1e84ff', cat: '볼륨', cur: r, target: 50, unit: '검수', got: r >= 50 },
          { icon: '📚', label: '검수 250', desc: '누적 250건 검수', exp: 120, color: '#5c77ff', cat: '볼륨', cur: r, target: 250, unit: '검수', got: r >= 250 },
          { icon: '🏆', label: '검수 1,000', desc: '누적 1,000건 검수', exp: 300, color: '#f5a623', cat: '볼륨', cur: r, target: 1000, unit: '검수', got: r >= 1000 },
          { icon: '🚀', label: '검수 2,500', desc: '누적 2,500건 검수', exp: 500, color: '#ff9429', cat: '볼륨', cur: r, target: 2500, unit: '검수', got: r >= 2500 },
          { icon: '🌌', label: '검수 5,000', desc: '누적 5,000건 검수', exp: 800, color: '#a05cff', cat: '볼륨', cur: r, target: 5000, unit: '검수', got: r >= 5000 },
          { icon: '🏔️', label: '완주 10,000', desc: '누적 1만 건 검수 · 여정 완주', exp: 2000, color: '#f5a623', cat: '볼륨', cur: r, target: 10000, unit: '검수', got: r >= 10000 },
          { icon: '🔥', label: '연속 3일', desc: '3일 연속 검수', exp: 15, color: '#ff9429', cat: '스트릭', cur: s, target: 3, unit: '일', got: s >= 3 },
          { icon: '⚡', label: '연속 7일', desc: '7일 연속 검수', exp: 30, color: '#ff6a3d', cat: '스트릭', cur: s, target: 7, unit: '일', got: s >= 7 },
          { icon: '☄️', label: '연속 30일', desc: '30일 연속 검수', exp: 120, color: '#ff4e33', cat: '스트릭', cur: s, target: 30, unit: '일', got: s >= 30 },
          { icon: '🌋', label: '연속 100일', desc: '100일 연속 검수', exp: 500, color: '#d83a2c', cat: '스트릭', cur: s, target: 100, unit: '일', got: s >= 100 },
          { icon: '🏅', label: '개선 채택', desc: '개선안이 채택됐어요', exp: 25, color: '#a05cff', cat: '기여', cur: c, target: 1, unit: '개선', got: c >= 1 },
          { icon: '🛠️', label: '개선 50', desc: '개선안 50건 채택', exp: 150, color: '#7c5cff', cat: '기여', cur: c, target: 50, unit: '개선', got: c >= 50 },
          { icon: '⚒️', label: '개선 500', desc: '개선안 500건 채택', exp: 600, color: '#6a3dff', cat: '기여', cur: c, target: 500, unit: '개선', got: c >= 500 },
          { icon: '🎯', label: '골드 정확도 90%', desc: '골드 문항 10건 이상 · 정확도 90%', exp: 60, color: '#18ba45', cat: '품질', cur: gap, target: 90, unit: '%', got: gn >= 10 && ga >= 0.9 },
          { icon: '🎯', label: '골드 정확도 95%', desc: '골드 문항 50건 이상 · 정확도 95%', exp: 250, color: '#0f9c38', cat: '품질', cur: gap, target: 95, unit: '%', got: gn >= 50 && ga >= 0.95 },
          { icon: '🤝', label: '합의 메이커', desc: '팀 합의와 일치한 판정 500건', exp: 200, color: '#1e84ff', cat: '품질', cur: cm, target: 500, unit: '건', got: cm >= 500 },
          { icon: '⚖️', label: '불일치 해결사', desc: '의견 갈린 콘텐츠 재검토 100건', exp: 200, color: '#a05cff', cat: '품질', cur: sr, target: 100, unit: '건', got: sr >= 100 },
          { icon: '💎', label: '골든 기여 100', desc: '내 검수가 골든(정답) 확정 100건에 기여', exp: 300, color: '#f5a623', cat: '품질', cur: (m&&m.golden_contribs)||0, target: 100, unit: '건', got: ((m&&m.golden_contribs)||0) >= 100 },
          { icon: '⭐', label: 'Lv.10 마스터', desc: '레벨 10 도달(약 380건 검수)', exp: 100, color: '#ffb020', cat: '지위', cur: L, target: 10, unit: 'Lv', got: L >= 10 },
          { icon: '🌟', label: 'Lv.25', desc: '레벨 25 도달(여정의 절반 고지)', exp: 400, color: '#f5a623', cat: '지위', cur: L, target: 25, unit: 'Lv', got: L >= 25 },
          { icon: '👑', label: 'Lv.50 만렙', desc: '레벨 50 · 약 1만 건 검수 완주', exp: 2000, color: '#f5a623', cat: '지위', cur: L, target: 50, unit: 'Lv', got: L >= 50 },
                ];
      },
      badgeModalOpen: false,
      get badgeGot() { return this.badges().filter((x) => x.got).length; },
      async checkBadges() {
        if (!this.arenaMe) return;
        const key = 'prism_badges_' + (this.reviewer || '');
        // 기준선(이미 축하함): 서버 우선(_badgeSeen, 기기 간) → 없으면 로컬 폴백
        let localSeen = null;
        try { const raw = localStorage.getItem(key); if (raw) localSeen = JSON.parse(raw); } catch (e) {}
        const server = Array.isArray(this._badgeSeen) ? this._badgeSeen : null;
        const firstLoad = (server === null && localSeen === null);   // 최초 진입 = 기준선만
        const base = [].concat(server || [], localSeen || []);
        const got = this.badges().filter((x) => x.got).map((x) => x.label);
        const fresh = got.filter((l) => !base.includes(l));
        try { localStorage.setItem(key, JSON.stringify(got)); } catch (e) {}
        // 서버 영속(단조 증가) · 신규가 있거나 서버 기준선이 아직 없을 때
        if (this.reviewer && (fresh.length || server === null)) {
          try {
            const r = await (await this._afetch('/badges', { method: 'POST', headers: this._authHeaders(),
              body: JSON.stringify({ reviewer: this.reviewer, earned: got }) })).json();
            if (r && Array.isArray(r.badges)) this._badgeSeen = r.badges;
          } catch (e) {}
        }
        if (firstLoad) return;                            // 최초 기준선은 축하 생략(스팸 방지)
        if (fresh.length) { const bd = this.badges().find((x) => x.label === fresh[0]); if (bd) this.celebrateBadge(bd); }
      },
      celebrateBadge(bd) { this.badgeToast = bd; if (this._btT) clearTimeout(this._btT); this._btT = setTimeout(() => { this.badgeToast = null; }, 4500); },
      // 검수 완료 → 점수 상승 리워드(성취감). 연속 검수 시 key 로 애니메이션 재시작.
      celebratePoints(pts, label) {
        this._ptId = (this._ptId || 0) + 1;
        this.ptToast = { id: this._ptId, pts: pts, label: label || '' };
        const id = this._ptId;
        if (this._ptT) clearTimeout(this._ptT);
        this._ptT = setTimeout(() => { if (this.ptToast && this.ptToast.id === id) this.ptToast = null; }, 1700);
      },
      // 오늘의 미션: 서버 판정·보상(arenaData.missions). 아래 getter 는 미션 데이터 없을 때 폴백 안내.
      get missionList() { return (this.arenaData && this.arenaData.missions) || []; },
      get todayMission() {
        const q = (this.arenaData && this.arenaData.queue) || 0;
        if (q > 0) return { txt: '대기 ' + q + '건 비우기 🔥', to: 'review', cta: '검수하기' };
        return { txt: '일치율 점검', to: 'evaluate', cta: '평가' };
      },
      // 레벨 커브(서버 store.level_of 와 동일): 구간 요구 pt = 100 + 80×(레벨-1) · Lv.50 만렙 = 98,980pt ≈ 검수 1만 건
      lvlFloor(l) { return (l - 1) * 100 + 40 * (l - 1) * (l - 2); },
      lvlNeed(l) { return 100 + 80 * (l - 1); },
      xpPct(r) {
        if (!r) return 0; const L = r.level || 1;
        if (L >= 50) return 100;
        return Math.max(0, Math.min(100, Math.round(((r.points || 0) - this.lvlFloor(L)) / this.lvlNeed(L) * 100)));
      },
      xpToNext(r) { if (!r) return 0; const L = r.level || 1; return L >= 50 ? 0 : Math.max(0, this.lvlFloor(L + 1) - (r.points || 0)); },
      async queueFeedback(it, verdict) {
        if (!this.ensureReviewer()) return;
        it.note = it.note || '';
        const wasReviewed = !!it.myVerdict;
        const r = await this._postFb({ hash: it.hash, service: it.service, title: it.title, model: it.model || '', verdict: verdict, stage: 'review', note: it.note });
        if (r && r.gold) {                              // 골드 문항: 응답 후 정오답 공개(즉시 학습 피드백)
          it.reviewed = true; it.myVerdict = verdict; it.goldRevealed = true; it.goldCorrect = !!r.gold.correct;
          if (r.gold.correct) this.celebratePoints(10, '골드 문항 정답');
          else this.liveToast('골드 문항 · 정답과 달랐어요(품질 점수에 반영)');
          return;
        }
        if (r && r.error) { this._err(r.error); return; }
        if (!wasReviewed) this.celebratePoints((verdict === 'bad' && (it.note || '').trim()) ? 25 : 10, '검수 완료');
        it.reviewed = true; it.myVerdict = verdict;
        if (this.queueOnlyUnreviewed && !it.split) this.queueData.items = (this.queueData.items || []).filter((x) => x.hash !== it.hash);
      },
      ensureReviewer() { if (!(this.reviewer || '').trim()) { this.reviewerEditing = true; return false; } return true; },
      notifyViewing(it) { try { fetch('/presence', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reviewer: this.reviewer, hash: it.hash, action: 'viewing' }) }); } catch (e) {} },
      async clearFeedback() {
        if (!(await this.dsConfirm('누적된 평가 피드백과 학습 보정을 모두 초기화할까요?', { ok: '초기화', danger: true }))) return;
        try { await fetch('/feedback', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ clear: true }) }); } catch (e) {}
        this.loadDash();
      },
      learnedStages: { extract: false, analyze: false, review: false, judge: false },
      async loadPromptDefaults() { try { await this.refreshConfig(); const d = await (await fetch('/prompt-defaults', { headers: this._authHeaders() })).json(); this.learnedStages = d.learned || this.learnedStages; } catch (e) {} },
      async loadTopics() { this.modBusy = true; try { this.topicData = await (await fetch('/topics', { headers: this._authHeaders() })).json(); this._syncTopicSettings(); this._ensureStudioModels(); } catch (e) {} this.modBusy = false; },
      // 자동 리프레시 배지 툴팁: 지난 스냅샷 대비 변화 요약(상위 5개)
      topicSnapTip() {
        const s = (this.topicData || {}).snapshot || {}; const d = s.delta || {};
        if (!d.changed_n && !d.gone_n) return '1시간마다 자동으로 다시 매칭하고 변화를 기록합니다 · 지난 확인에서 변화 없음';
        const top = (d.changed || []).slice(0, 5).map((c) => c.label + ' ' + c.from + '→' + c.to + '건').join(' · ');
        return '지난 스냅샷 대비 변화 ' + (d.changed_n || 0) + '건' + (d.new_n ? (' (신규 토픽 ' + d.new_n + ')') : '') + (d.gone_n ? (' · 사라짐 ' + d.gone_n + '개') : '') + (top ? (' — ' + top) : '');
      },
      async _ensureStudioModels() {                 // 자동 채우기 모델 선택지: 없으면 1회 조회(가벼움 · 실패 무해)
        if ((this.models || []).length) return;
        try { const j = await (await fetch('/models', { headers: this._authHeaders() })).json(); if (j && j.ok && Array.isArray(j.models)) { this.models = j.models; if (!this.cfgModel) this.cfgModel = j.current || ''; } } catch (e) {}
      },
      _syncTopicSettings() { const s = (this.topicData && this.topicData.settings) || {}; this.settingsDraft = { co_min: s.co_min || 2, entity_min: s.entity_min || 2 }; },
      // ── 미디어: 영상(미저장) ──
      mediaVidPick(e) { this.mediaVid.file = (e.target.files && e.target.files[0]) || null; this.mediaVidRes = null; this.mediaVidMsg = ''; },
      async mediaNative() {
        if (!this.mediaVid.file) return;
        this.mediaVidBusy = true; this.mediaVidMsg = '영상 처리 중… (네이티브 트랙 → 병합 → 추출)'; this.mediaVidRes = null;
        try {
          const fd = new FormData();
          fd.append('file', this.mediaVid.file);
          if (this.mediaVid.caption) fd.append('caption', this.mediaVid.caption);
          if ((this.mediaVid.subs || '').trim()) fd.append('subtitles', this.mediaVid.subs);   // 자막 우선: 있으면 영상 모델 호출 생략
          const r = await (await this._afetch('/media-extract', { method: 'POST', headers: this.authToken ? { 'Authorization': 'Bearer ' + this.authToken } : {}, body: fd })).json();
          if (r && r.ok) { this.mediaVidRes = r; this.mediaVidMsg = (r.native && r.native.skipped) ? '완료 · 자막 우선(영상 모델 호출 없음 · 비용 0)' : (r.mock ? '완료 · mock(라우터 미연결)' : '완료'); }
          else { this.mediaVidMsg = (r && r.error) || '처리 실패'; }
        } catch (e) { this.mediaVidMsg = '처리 실패'; }
        this.mediaVidBusy = false;
      },
      // ── 미디어: 이미지(미저장) ──
      mediaImgPick(e) { this.mediaImg.files = Array.from(e.target.files || []); this.mediaImgRes = null; this.mediaImgMsg = ''; },
      async mediaImgRun() {
        if (!this.mediaImg.files.length) return;
        this.mediaImgBusy = true; this.mediaImgMsg = '이미지 처리 중… (시각 이해 → 추출)'; this.mediaImgRes = null;
        try {
          const fd = new FormData();
          this.mediaImg.files.forEach((f, i) => fd.append('image' + i, f));
          if (this.mediaImg.caption) fd.append('caption', this.mediaImg.caption);
          const [vp, vm] = String(this.mediaImg.vision || 'upstage_ie').split(':');   // 선택 시각 슬롯 → provider/model
          if (vp) fd.append('vision_provider', vp);
          if (vm) fd.append('vision_model', vm);
          const r = await (await this._afetch('/media-extract', { method: 'POST', headers: this.authToken ? { 'Authorization': 'Bearer ' + this.authToken } : {}, body: fd })).json();
          if (r && r.ok) { this.mediaImgRes = r; this.mediaImgMsg = r.mock ? '완료 · mock(비전 미연결)' : '완료'; }
          else { this.mediaImgMsg = (r && r.error) || '처리 실패'; }
        } catch (e) { this.mediaImgMsg = '처리 실패'; }
        this.mediaImgBusy = false;
      },
      // ── 토픽 스튜디오 ──
      async _studioPost(payload) {
        const r = await (await this._afetch('/topic-studio', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(payload) })).json();
        return r;
      },
      studioDef() { return { id: this.studio.editId, name: this.studio.name, prompt: this.studio.prompt, cats: this.studio.cats, intents: this.studio.intents, keywords: this.studio.keywords, eattrs: this.studio.eattrs, req: this.studio.req, neg: this.studio.neg }; },
      // 엔티티 속성 조건(개체 사전 축): 'key:value' · 항상 필수(같은 개체 AND · 예: 여성 스포츠인)
      eattrLabel(s) { const i = s.indexOf(':'); const ko = { type: '타입', gender: '성별', occupation: '직업', nationality: '국적', affiliation: '소속', org_kind: '조직', country: '국가', loc_kind: '장소', af_kind: '종류', ev_kind: '종류', domain: '도메인' }; return i < 0 ? s : (ko[s.slice(0, i)] || s.slice(0, i)) + '=' + s.slice(i + 1); },
      studioAddEattr(v) {
        const s = v || (this.studio.eaKey + ':' + (this.studio.eaVal || '').trim());
        if (!s || s.endsWith(':') || this.studio.eattrs.includes(s)) return;
        this.studio.eattrs.push(s); this.studio.eaVal = ''; this.schedulePreview();
      },
      studioDelEattr(s) { const i = this.studio.eattrs.indexOf(s); if (i >= 0) this.studio.eattrs.splice(i, 1); this.schedulePreview(); },
      eattrCandidates() {
        const all = (this.topicData && this.topicData.catalog && this.topicData.catalog.eattrs) || [];
        return all.filter(c => !this.studio.eattrs.includes(c.k)).slice(0, 14);
      },
      // 조건 칩 4상태: off(후보) → sel(선택·관련 묶음) → req(필수·모든 묶음 공통) → neg(제외·걸리면 탈락) → off
      studioState(dim, val) { if ((this.studio.neg[dim] || []).includes(val)) return 'neg'; if (!this.studio[dim].includes(val)) return 'off'; return this.studio.req[dim].includes(val) ? 'req' : 'sel'; },
      studioCycle(dim, val) {
        const sel = this.studio[dim], req = this.studio.req[dim], neg = this.studio.neg[dim];
        const st = this.studioState(dim, val);
        if (st === 'off') sel.push(val);                                              // off → 선택
        else if (st === 'sel') req.push(val);                                         // 선택 → 필수
        else if (st === 'req') { sel.splice(sel.indexOf(val), 1); req.splice(req.indexOf(val), 1); neg.push(val); }  // 필수 → 제외
        else neg.splice(neg.indexOf(val), 1);                                         // 제외 → off(키워드는 제거)
        this.schedulePreview();
      },
      negText() { const m = []; const c = this.studio.neg; ['cats', 'intents', 'keywords'].forEach(d => (c[d] || []).forEach(v => m.push(d === 'cats' ? this.catBoth(v) : v))); return m.join(' · '); },
      studioKwChips() { return this.studio.keywords.concat((this.studio.neg.keywords || []).filter(k => !this.studio.keywords.includes(k))); },
      bundleLabel(b) { return (b.valueset || []).map(v => v.dim === 'cats' ? this.catBoth(v.v) : v.v).join(' · ') || '전체(조건 없음)'; },
      mustText() { const m = []; const c = this.studio.req; ['cats', 'intents', 'keywords'].forEach(d => (c[d] || []).forEach(v => m.push(d === 'cats' ? this.catBoth(v) : v))); return m.join(' · '); },
      chipCls(dim, val, base) { const s = this.studioState(dim, val); if (s === 'off') return 'ds-badge--neutral'; if (s === 'neg') return 'ds-badge--error is-neg'; return base + (s === 'req' ? ' is-req' : ''); },
      coreSamples() { const c = (this.studioPreview.bundles || []).find(b => b.kind === 'core'); return (c && c.samples) || []; },
      _escHtml(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); },
      bundleSummaryText() {
        const bs = (this.studioPreview.bundles) || [];
        const n = (this.topicData && this.topicData.n_contents) || 0;
        if (!bs.length) return '아직 조건이 없어요 · <b>전체 ' + n + '건</b>이 한 묶음입니다';
        const core = bs.find(b => b.kind === 'core'); const rel = bs.filter(b => b.kind === 'related').length;
        const mt = this._escHtml(this.mustText());   // 사용자 자유입력 키워드 · x-html 삽입 전 이스케이프
        const base = mt ? ('<b>' + mt + '</b> 을(를) 필수 뼈대로, ') : '';
        const ngt = this._escHtml(this.negText());
        return base + '핵심 <b>' + ((core && core.count) || 0) + '건</b>' + (rel ? (' + 관련 묶음 <b>' + rel + '개</b>로 펼쳐집니다') : ' (선택 조건을 더하면 관련 묶음이 생겨요)') + (ngt ? (' · <b>✖ ' + ngt + '</b> 제외') : '');
      },
      studioAddKw() { const k = (this.studio.kwInput || '').trim(); if (k) { const ni = this.studio.neg.keywords.indexOf(k); if (ni >= 0) this.studio.neg.keywords.splice(ni, 1); if (!this.studio.keywords.includes(k)) this.studio.keywords.push(k); } this.studio.kwInput = ''; this.schedulePreview(); },
      // 추천 카드: 떠오르는 엔티티 → 키워드 추가 · 자동 사건 묶음 → 수동 토픽 폼 프리필
      suggestKeyword(name) {
        this.studio.kwInput = name; this.studioAddKw();
        if (!this.studio.name.trim()) this.studio.name = name + ' 모아보기';
        this.liveToast('키워드 추가 · ' + name);
      },
      promoteCluster(p) {
        const ents = (p.representative_entities || []).slice(0, 3);
        this.studio.name = (p.name || ents.join(' · ')) + ' 큐레이션';
        ents.forEach((e) => { if (!this.studio.keywords.includes(e)) this.studio.keywords.push(e); });
        if (!this.studio.prompt.trim()) this.studio.prompt = ents.join(', ') + ' 관련 콘텐츠를 모아줘';
        this.schedulePreview();
        this.liveToast('사건 묶음을 폼에 채웠어요 · 조건을 다듬고 저장하세요');
      },
      topKw() { const sel = this.studio.keywords, ng = this.studio.neg.keywords || []; const all = (this.topicData && this.topicData.catalog && this.topicData.catalog.keywords) || []; return all.filter(k => !sel.includes(k.k) && !ng.includes(k.k)).slice(0, 12); },
      // 스텝 진행 상태 · 필터 요약 · 자동선택 표시 · 모델 목록(라우터 포함)
      tStepDone() { const s = this.studio; return (s.name.trim() ? 1 : 0) + (s.prompt.trim() ? 1 : 0) + ((s.cats.length || s.intents.length || s.keywords.length) ? 1 : 0); },
      tActive() { const s = this.studio; if (!s.name.trim()) return 1; if (!s.prompt.trim()) return 2; if (!(s.cats.length || s.intents.length || s.keywords.length)) return 3; return 4; },
      isAuto(field, val) { return (this.studio.auto[field] || []).includes(val) && this.studio[field].includes(val); },
      // 조건 칩: 데이터 present + 선택됐지만 데이터엔 아직 없는 값(자동생성이 고른 전체 분류)까지 표시
      studioCatChips() { const a = (this.topicData && this.topicData.catalog && this.topicData.catalog.cats) || []; const have = new Set(a.map(c => c.k)); return a.concat(this.studio.cats.filter(c => !have.has(c)).map(c => ({ k: c, v: 0 }))); },
      studioIntentChips() { const a = (this.topicData && this.topicData.catalog && this.topicData.catalog.intents) || []; const have = new Set(a.map(c => c.k)); return a.concat(this.studio.intents.filter(c => !have.has(c)).map(c => ({ k: c, v: 0 }))); },
      get studioModelList() {          // 직접(Solar) + 라우터(Timely·BizRouter) optgroup 헤더 + 모델
        let groups = [];
        try { groups = this.textGroups || []; } catch (e) { groups = []; }
        const out = [];
        groups.forEach(g => {
          const items = (g.items || []).map(it => it.model).filter(Boolean);
          if (!items.length) return;
          out.push({ header: true, value: '', text: '── ' + g.label + (g.on ? '' : ' (키 없음)') + ' ──', key: 'h' + g.label });
          items.forEach(m => out.push({ header: false, value: m, text: m, key: g.label + '|' + m }));
        });
        return out;
      },
      async refreshStudioModels() {
        this.modelsBusy = true; this.modelsMsgStudio = '모델 목록 새로고침 중…';
        try { await this.loadModels(); this.modelsMsgStudio = '모델 목록을 새로고침했습니다 (직접 · 라우터)'; }
        catch (e) { this.modelsMsgStudio = '새로고침 실패'; }
        this.modelsBusy = false;
      },
      schedulePreview() { if (this._studioT) clearTimeout(this._studioT); this._studioT = setTimeout(() => this.studioPreviewNow(), 260); },
      async studioPreviewNow() {
        if (!this.topicData || !this.topicData.n_contents) return;
        this.studioBusy = true;
        try { const r = await this._studioPost({ action: 'preview', def: this.studioDef() }); if (r && r.preview) this.studioPreview = r.preview; } catch (e) {} this.studioBusy = false;
      },
      async studioSuggest() {
        const text = (this.studio.prompt || this.studio.name || '').trim();
        if (!text) { this.studioMsg = '먼저 자연어로 설명을 적어 주세요'; return; }
        this.studioSuggesting = true; this.studioMsg = '조건값을 채우는 중…';
        try {
          const r = await this._studioPost({ action: 'suggest', text, model: this.studioModel });
          const s = (r && r.suggest) || {};
          (s.cats || []).forEach(c => { if (!this.studio.cats.includes(c)) this.studio.cats.push(c); });
          (s.intents || []).forEach(c => { if (!this.studio.intents.includes(c)) this.studio.intents.push(c); });
          (s.keywords || []).forEach(c => { if (!this.studio.keywords.includes(c)) this.studio.keywords.push(c); });
          // 필수(req) 반영: 자동생성이 필수로 지정한 값을 필수 상태로(선택은 그대로 선택)
          const rq = (s.req) || { cats: [], intents: [], keywords: [] };
          ['cats', 'intents', 'keywords'].forEach(dim => { (rq[dim] || []).forEach(v => { if (this.studio[dim].includes(v) && !this.studio.req[dim].includes(v)) this.studio.req[dim].push(v); }); });
          // 제외(neg) 반영: 배제 표현('속보는 빼줘')의 대상 → 제외 상태로(선택·필수와 상충 시 제외 우선)
          const ng = (s.neg) || { cats: [], intents: [], keywords: [] };
          let negN = 0;
          ['cats', 'intents', 'keywords'].forEach(dim => { (ng[dim] || []).forEach(v => {
            const si = this.studio[dim].indexOf(v); if (si >= 0) this.studio[dim].splice(si, 1);
            const ri = this.studio.req[dim].indexOf(v); if (ri >= 0) this.studio.req[dim].splice(ri, 1);
            if (!this.studio.neg[dim].includes(v)) { this.studio.neg[dim].push(v); negN++; }
          }); });
          // 개체 속성(eattrs) 반영: 항상 필수 취급 · 사전 실재값만 서버가 검증해 내려줌
          (s.eattrs || []).forEach(v => { if (!this.studio.eattrs.includes(v)) this.studio.eattrs.push(v); });
          this.studio.auto = { cats: (s.cats || []).slice(), intents: (s.intents || []).slice(), keywords: (s.keywords || []).slice() };
          const n = (s.cats || []).length + (s.intents || []).length + (s.keywords || []).length + (s.eattrs || []).length + negN;
          const viaLlm = r && r.via === 'llm';
          const src = viaLlm ? ('모델(' + (this.studioModel || '기본') + ')') : '규칙';
          // 모델을 골랐는데 규칙으로 떨어졌으면 이유를 밝힌다(모델이 빈 응답·키 없음 등 · 조용한 폴백 방지)
          let why = '';
          if (!viaLlm && this.studioModel) {
            const rt = (r && r.route) || '';
            why = rt === 'mock' ? ' · 모의 모드라 실제 모델 대신 규칙' : /키|key/i.test(rt) ? ' · 모델 키가 없어 규칙' : (' · ' + (this.studioModel) + ' 응답이 비어 규칙으로 대체');
          }
          this.studioMsg = n ? (src + '이 조건값 ' + n + '개를 채웠습니다' + why + ' · 켜고 끄며 조정하세요')
            : (src + '이 일치하는 조건값을 찾지 못했습니다' + why + ' · 직접 선택하세요');
          this.schedulePreview();
        } catch (e) { this.studioMsg = '채우기 실패 · 다시 시도하세요'; } this.studioSuggesting = false;
      },
}));
