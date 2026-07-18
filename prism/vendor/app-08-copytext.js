/* Prism 앱 조각 08 · prismApp 프로퍼티 그룹(자동 분할 · 앱 분할 6차).
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
      async copyText(t, label) {
        try { await navigator.clipboard.writeText(t || ''); this.flashCopy((label || '복사') + ' 됨'); }
        catch (e) { this.flashCopy('복사 실패'); }
      },
      flashCopy(m) { this.copyMsg = m; clearTimeout(this._cpT); this._cpT = setTimeout(() => { this.copyMsg = ''; }, 1600); },
      exportBatchCsv() {
        const its = (this.batchResult && this.batchResult.items) || [];
        const esc = (v) => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
        const rows = [['제목', '리드문', '엔티티', '인텐트', '등급']];
        for (const it of its) rows.push([it.title, it.summary, (it.entities || []).join(' · '), (it.intent || []).join(' · '), it.grade]);
        const csv = '\ufeff' + rows.map((r) => r.map(esc).join(',')).join('\r\n');
        const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
        const a = document.createElement('a'); a.href = url; a.download = 'prism_results.csv';
        document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
      },
      get modelOptions() {
        const a = this.models.slice();
        if (this.cfgModel && !a.includes(this.cfgModel)) a.unshift(this.cfgModel);
        return a;
      },
      async loadModels() {                     // 모델 새로고침: 전 제공자(직접 Solar 실조회 + 라우터 카탈로그)
        this.modelsMsg = '불러오는 중…'; this.cfgBusy = true;
        try {
          if (this.keyInputs.solar) {              // 입력한 키를 먼저 적용(세션)
            await this._afetch('/config', { method: 'POST', headers: this._authHeaders(),
              body: JSON.stringify({ api_key: this.keyInputs.solar }) });
          }
          try {                                    // Solar 는 실조회(키 있을 때) · 실패해도 전체는 계속
            const j = await (await fetch('/models', { headers: this._authHeaders() })).json();
            if (j.ok) {
              this.models = j.models;
              if (!this.cfgModel || !this.models.includes(this.cfgModel))
                this.cfgModel = this.cfg.model || this.models[0] || '';
            }
          } catch (e) {}
          await this.refreshConfig();
          const total = this.textGroups.reduce((n, g) => n + g.items.length, 0);
          const on = this.textGroups.filter((g) => g.on).length;
          this.modelsMsg = '전 제공자 ' + total + '개 모델 · 연결 ' + on + '/' + this.textGroups.length;
        } catch (e) { this.modelsMsg = '오류: ' + e; }
        this.cfgBusy = false;
      },
      async refreshConfig() {
        try {
          const r = await this._afetch('/config'); this.cfg = await r.json();
          if (this.cfg.backend) this.backend = this.cfg.backend;     // sqlite | supabase
          this._seenBoot(this.cfg.bootId);                            // 배포 감지 폴백(SSE 차단 환경)
          if (this.backend === 'supabase' && this.authToken) this.ensureAdmin();  // 관리자 여부 → nav 게이팅(재시도 포함)
          if (!this.cfgModel) this.cfgModel = this.cfg.model;
          if (this.cfg.reasoning) this.reasoning = this.cfg.reasoning;
          if (typeof this.cfg.systemPrompt === 'string') this.systemPrompt = this.cfg.systemPrompt;
          if (Array.isArray(this.cfg.availableModels)) this.availableModels = this.cfg.availableModels;
          if (this.cfg.goldenMinGood) this.goldenMinGood = this.cfg.goldenMinGood;
          if (typeof this.cfg.learnNextAt === 'string') this.learnNextAt = this.cfg.learnNextAt;
          if (this.cfg.learnRepeatDays != null) this.learnRepeat = this.cfg.learnRepeatDays;
          if (!this.wrapDraft) this.syncWrapDraft();
          if (!this.cmpA && this.availableModels.length) { this.cmpA = this.availableModels[0]; this.cmpB = this.availableModels[1] || ''; }   // A/B 기본 슬롯
          if (Array.isArray(this.cfg.ingestSources)) this.ingestSources = this.cfg.ingestSources.slice();
          if (this.cfg.guideUrls) this.teamLinks = Object.assign({ guide: '', guide_user: '', guide_admin: '' }, this.cfg.guideUrls);
          if (!this._keyTargetInit) { this._keyTargetInit = true; this.keyTarget = ['bizrouter', 'timely', 'solar'].find((s) => this.keyState(s)) || 'bizrouter'; }
          if (this.cfg.textProvider) this.textProvider = this.cfg.textProvider;
          if (typeof this.cfg.textModel === 'string' && this.cfg.textModel) this.textModel = this.cfg.textModel;
          if (this.cfg.visionProvider) this.visionProvider = this.cfg.visionProvider;
          if (typeof this.cfg.visionModel === 'string' && this.cfg.visionModel) this.visionModel = this.cfg.visionModel;
          if (typeof this.cfg.legalEnabled === 'boolean') this.legalEnabled = this.cfg.legalEnabled;
        } catch (e) { /* noop */ }
      },
      async toggleLegal() {
        try { await this._afetch('/config', { method: 'POST', headers: this._authHeaders(),
          body: JSON.stringify({ legal_enabled: this.legalEnabled }) }); } catch (e) {}
      },
      // ── 키(서비스별) ──
      keyState(service) { return !!this.cfg[this.keyDefs[service].has]; },
      keyPersisted(service) { return !!this.cfg[this.keyDefs[service].persisted]; },
      async saveKey(service) {
        this.keyMsgs[service] = '저장 중…'; this.cfgBusy = true;
        try {
          const body = { persist: this.cfgPersist };
          if (service === 'solar') { body.api_key = this.keyInputs.solar; if (this.cfgModel) body.model = this.cfgModel; }
          else body[service + '_api_key'] = this.keyInputs[service];
          const r = await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(body) });
          const j = await r.json();
          if (!r.ok || (j && j.error)) { this.keyMsgs[service] = '오류: ' + ((j && j.error) || r.status); this.cfgBusy = false; return; }
          this.cfg = j; this.keyInputs[service] = '';
        } catch (e) { this.keyMsgs[service] = '오류: ' + e; this.cfgBusy = false; return; }
        if (this.keyState(service)) {
          if (service === 'solar' && !this.models.length) this.loadModels();
          await this.testConn(service);                           // 저장 즉시 연결 검증(전 서비스)
        } else { this.keyMsgs[service] = '저장 실패'; this.cfgBusy = false; }
      },
      async forgetKey(service) {
        try {
          const body = {}; if (service === 'solar') body.forget = true; else body['forget_' + service] = true;
          const r = await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(body) });
          const j = await r.json();
          if (!r.ok || (j && j.error)) { this.keyMsgs[service] = '오류: ' + ((j && j.error) || r.status); return; }
          this.cfg = j; this.keyMsgs[service] = '키 삭제됨';
        } catch (e) { this.keyMsgs[service] = '오류: ' + e; }
      },
      async saveTeamLinks() {
        this.tlMsg = '저장 중…';
        try {
          const r = await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ team_links: this.teamLinks }) });
          const j = await r.json();
          if (!r.ok || (j && j.error)) { this.tlMsg = '오류: ' + ((j && j.error) || r.status); return; }
          this.cfg = j; this.tlMsg = '✓ 저장됨';
        } catch (e) { this.tlMsg = '오류: ' + e; }
      },
      async testConn(service = 'solar') {
        this.cfgBusy = true; this.keyMsgs[service] = '연결 테스트 중…';
        try { const j = await (await this._afetch('/ping', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(service === 'solar' ? {} : { service }) })).json();
          this.keyMsgs[service] = (j.ok ? '✓ 성공 · ' : '✗ 실패 · ') + j.detail; }
        catch (e) { this.keyMsgs[service] = '오류: ' + e; }
        await this.refreshConfig(); this.cfgBusy = false;
      },
      // 텍스트 슬롯(메타 생성)
      async saveTextSlot() {
        this.slotMsg = '저장 중…';
        const payload = { text_provider: this.textProvider };
        if (this.isRouter(this.textProvider)) payload.text_model = this.textModel;
        else if (this.cfgModel) payload.model = this.cfgModel;
        try { const r = await this._afetch('/config', { method: 'POST', headers: this._authHeaders(),
            body: JSON.stringify(payload) }); this.cfg = await r.json(); this.slotMsg = '✓ 적용됨'; }
        catch (e) { this.slotMsg = '오류: ' + e; }
      },
      setReasoning(id) { this.reasoning = id; fetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ reasoning: id }) }).catch(() => {}); },
      async clearStore() {
        if (!(await this.dsConfirm('적재된 추출 결과를 모두 삭제할까요? (되돌릴 수 없음)', { ok: '삭제', danger: true }))) return;
        try { const r = await this._afetch('/store', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ clear: true }) });
          const d = await r.json(); this.cfg.storedCount = d.count || 0; this.loadDash(); } catch (e) {}
      },

      // ── 자동 인입 파이프라인 소스(API/Kafka) ──
      ingestSources: [], ingestMsg: '',
      newSrc: { type: 'api', name: '', endpoint: '', method: 'GET', auth: '', brokers: '', topic: '', group: '', interval: '60' },
      resetNewSrc() { this.newSrc = { type: 'api', name: '', endpoint: '', method: 'GET', auth: '', brokers: '', topic: '', group: '', interval: '60' }; },
      addSource() {
        if (!this.newSrc.name.trim()) { this.ingestMsg = '이름을 입력하세요'; return; }
        this.ingestSources.push(Object.assign({ id: 'src-' + Date.now(), enabled: true }, this.newSrc));
        this.resetNewSrc(); this.saveIngest();
      },
      removeSource(id) { this.ingestSources = this.ingestSources.filter((s) => s.id !== id); this.saveIngest(); },
      toggleSource(s) { s.enabled = !s.enabled; this.saveIngest(); },
      ingestBusy: {}, ingestRunMsg: {}, ingestJobs: [], _ingestPoll: null,
      delArm: '', _delArmT: null,                  // 콘텐츠 개별 삭제 2단계 확인
      jobFilter: null,                              // 실행 큐 작업 클릭 -> 해당 콘텐츠만 보기 {name, hashes}
      jobContents(j) {
        if (!(j.hashes || []).length) return;
        this.jobFilter = { name: j.name, kind: j.kind || '', hashes: j.hashes };
        this.$nextTick(() => { const el = document.getElementById('added-contents'); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); });
      },
      get contentRows() {
        const all = (this.dashData && this.dashData.contents) || [];
        if (!this.jobFilter) return all;
        const set = new Set(this.jobFilter.hashes);
        return all.filter((c) => set.has(c.hash));
      },
      fmtEta(s) { s = Math.max(0, Math.round(s || 0)); return s >= 60 ? (Math.floor(s / 60) + '분 ' + (s % 60) + '초') : (s + '초'); },
      // 개별 콘텐츠 재실행: STEP 2 사용 모델(bulkModel)로 이 건만 초안 재생성(/rerun · 이력 보존)
      // 퀘스트 진행 중 서버 차단에 걸리면 확인 모달을 거쳐 이 한 건만 강행(force) — 기본 보호는 유지
      rerunBusy: {},
      async rerunOne(c, force) {
        if (this.rerunBusy[c.hash]) return;
        this.rerunBusy = { ...this.rerunBusy, [c.hash]: true };
        let retryConfirm = false;
        try {
          const r = await (await this._afetch('/rerun', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ hash: c.hash, model: this.bulkModel || '', force: !!force }) })).json();
          if (r && !r.error) { this.liveToast((c.title || '콘텐츠') + ' · 재실행 완료'); this.loadDash(); this.loadRaw && this.loadRaw(); }
          else if (r && !force && /퀘스트/.test(r.error || '')) retryConfirm = true;
          else this._err((r && r.error) || '재실행 실패');
        } catch (e) { this._err('재실행 실패'); }
        this.rerunBusy = { ...this.rerunBusy, [c.hash]: false };
        if (retryConfirm) {
          const ok = await this.dsConfirm('퀘스트(검수 목표) 진행 중입니다. 초안을 새로 만들면 이 콘텐츠의 기존 검수 의견과 어긋날 수 있습니다. 이 건만 재실행할까요?', { title: '퀘스트 중 재실행', ok: '재실행', danger: true });
          if (ok) await this.rerunOne(c, true);
        }
      },
      async removeContent(c) {
        if (this.delArm !== c.hash) {              // 1차 클릭 = 확인 대기(3초)
          this.delArm = c.hash;
          clearTimeout(this._delArmT); this._delArmT = setTimeout(() => { this.delArm = ''; }, 3000);
          return;
        }
        this.delArm = ''; clearTimeout(this._delArmT);
        try {
          const r = await (await this._afetch('/content-remove', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ hash: c.hash }) })).json();
          if (r && r.ok) { this.loadDash(); this.loadRaw && this.loadRaw(); }
          else this._err((r && r.error) || '삭제 실패');
        } catch (e) { this._err('삭제 실패'); }
      },
      async ingestNow(s) {
        this.ingestBusy[s.id] = true; this.ingestRunMsg[s.id] = '';
        this.pollIngestStatus();                         // 진행률 폴링 시작
        try {
          const r = await (await fetch('/ingest-run', { method: 'POST', headers: this._authHeaders(),
            body: JSON.stringify({ id: s.id, name: s.name, endpoint: s.endpoint, method: s.method || 'GET', auth: s.auth || '', limit: 100 }) })).json();
          if (!r.ok) { this.ingestRunMsg[s.id] = '오류: ' + (r.error || '실패') + (r.headers ? (' / 헤더: ' + r.headers.join(', ')) : ''); }
          else { this.ingestRunMsg[s.id] = `✓ ${r.fetched}건 수신 → 신규 ${r.inserted} · 갱신 ${r.updated} · 제외 ${r.skipped}` + (r.mock ? ' (mock)' : ''); this.loadDash(); }
        } catch (e) { this.ingestRunMsg[s.id] = '오류: ' + e; }
        this.ingestBusy[s.id] = false;
      },
      // 자동 인입 상태(진행률) 폴링 · 실행 큐/자동 인입 뷰에서 사용
      async fetchIngestStatus() { try { const d = await (await fetch('/ingest-status', { headers: this._authHeaders() })).json(); this.ingestJobs = d.jobs || []; if (d.running) this.loadDashThrottled(); return d; } catch (e) { return { jobs: [], running: false }; } },
      pollIngestStatus() {
        if (this._ingestPoll) return;
        let seenRun = false, empties = 0;              // 작업 등록 전 첫 조회에 폴링이 꺼지던 결함 방지
        const tick = async () => {
          const d = await this.fetchIngestStatus();
          if (d.running) { seenRun = true; empties = 0; return; }
          empties++;
          if ((seenRun || empties >= 4) && !this.loading && !this.bulkBusy) {
            clearInterval(this._ingestPoll); this._ingestPoll = null; this.loadDash();
          }
        };
        this._ingestPoll = setInterval(tick, 1500); tick();
      },
      loadDashThrottled() { const now = Date.now(); if (now - (this._lastDash || 0) > 4000) { this._lastDash = now; this.loadDash(); } },
      loadRawThrottled() { const now = Date.now(); if (now - (this._lastRaw || 0) > 4000) { this._lastRaw = now; this.loadRaw(); } },
      loadArenaThrottled() { const now = Date.now(); if (now - (this._lastArena || 0) > 4000) { this._lastArena = now; this.loadArena(); } },
      get runningJobs() { return (this.ingestJobs || []).filter((j) => j.running); },
      get runningCount() { return (this.loading ? 1 : 0) + this.runningJobs.length; },
      srcJob(s) { return (this.ingestJobs || []).find((j) => j.id === s.id); },
      srcRunning(s) { const j = this.srcJob(s); return !!(this.ingestBusy[s.id] || (j && j.running)); },
      async saveIngest() {
        this.ingestMsg = '저장 중…';
        try { await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ ingest_sources: this.ingestSources }) }); this.ingestMsg = '✓ 저장됨'; clearTimeout(this._inT); this._inT = setTimeout(() => { this.ingestMsg = ''; }, 1600); } catch (e) { this.ingestMsg = '오류: ' + e; }
      },
      // ── 현황 결과 엑셀(CSV) 다운로드 ──
      _dl(name, rows) {
        const esc = (v) => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
        const csv = '\ufeff' + rows.map((r) => r.map(esc).join(',')).join('\r\n');
        const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
        a.download = name; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
      },
      // CSV·리포트는 인증 GET 이라 링크(<a href>) 대신 fetch+Blob 으로 받는다(서버 게이트).
      async exportDash() {
        try {
          const r = await this._afetch('/export.csv', { headers: this._authHeaders() });
          const u = URL.createObjectURL(new Blob([await r.blob()], { type: 'text/csv' }));
          const a = document.createElement('a'); a.href = u; a.download = 'prism_results.csv'; a.click();
          setTimeout(() => URL.revokeObjectURL(u), 60000);
        } catch (e) { this._err('CSV 다운로드 실패'); }
      },
      async openReport() {
        try {
          const r = await this._afetch('/report', { headers: this._authHeaders() });
          const u = URL.createObjectURL(new Blob([await r.text()], { type: 'text/html' }));
          window.open(u, '_blank');
          setTimeout(() => URL.revokeObjectURL(u), 60000);
        } catch (e) { this._err('리포트 열기 실패'); }
      },
      exportTopics() {
        const d = this.topicData || {}; const rows = [['구분', '토픽', '구성', '콘텐츠']];
        (d.custom || []).forEach((g) => rows.push(['수동', g.name, (g.prompt || ''), g.core_count || '']));
        (d.single || []).forEach((t) => rows.push(['자동 · 엔티티형', t.cluster_id, (t.entities || t.rep_entities || []).join(' · '), t.n_contents || '']));
        (d.composite || []).forEach((t) => rows.push(['자동 · 사건형', t.cluster_id, (t.rep_entities || t.entities || []).join(' · '), t.n_contents || '']));
        this._dl('prism_topics.csv', rows);
      },
      exportGolden() {                       // 정답셋 엑셀(CSV) 다운로드
        const its = (this.goldenList && this.goldenList.items) || [];
        const rows = [['제목', '등급', '카테고리', '버전', '출처', '교정 필요']];
        its.forEach((g) => rows.push([g.title || '', g.grade || '', (g.category || []).join(' · '), g.version ? ('v' + g.version) : '', g.source === 'manual' ? '직접' : '검수', g.fix_needed ? 'Y' : '']));
        this._dl('prism_golden.csv', rows);
      },
      exportUsers() {
        const us = (this.userData && this.userData.users) || []; const rows = [['user_id', '페르소나', '조회', '클릭률', '평균체류', '소비형태', '선호엔티티']];
        us.forEach((u) => rows.push([u.user_id, u.persona, u.engagement.views, u.engagement.click_rate, u.engagement.avg_dwell_sec, Object.entries(u.form).map((e) => e[0] + ':' + e[1]).join(' · '), (u.affinity_entities || []).map((e) => e[0]).join(' · ')]));
        this._dl('prism_users.csv', rows);
      },
      exportEval() {
        const t = this.tr || {}; const rows = [['항목', '값'], ['prompt_version', t.prompt_version || ''], ['fallbacks', (t.fallbacks || []).join(' · ')], ['cost_usd', t.cost_usd || 0], ['tokens', JSON.stringify(t.tokens || {})], ['latency_ms', JSON.stringify(t.latency_ms || {})]];
        this._dl('prism_eval.csv', rows);
      },
      selectTab(id) { this.activeTabId = id; this.status = ''; },

      // DNM 메타 체계(13. 프로젝트 기획 / 1312. 아이템 메타) 기준 item_meta 필드:
      //   summary(리드문) · entities(엔티티) · intent(인텐트) · content_category(콘텐츠 카테고리)
      get im() { return (this.result && this.result.output.item_meta) || {}; },
      get q() { return (this.result && this.result.output.quality_meta) || {}; },
      get contentCats() {
        // 1312: 콘텐츠 단위 카테고리 N개(복수 매핑) → 리스트 그대로
        return this.im.content_category || [];
      },

      // 엑셀 배치 인포그래픽: 총건·등급분포·인텐트 상위·평균 리드문 길이
      get batchStats() {
        const its = (this.batchResult && this.batchResult.items) || [];
        const n = its.length;
        const g = its.filter((x) => x.grade === 'G').length;
        const counts = {};
        let lenSum = 0, lenN = 0;
        for (const x of its) {
          for (const t of (x.intent || [])) counts[t] = (counts[t] || 0) + 1;
          if (x.summary) { lenSum += x.summary.length; lenN += 1; }
        }
        const top = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 5)
          .map(([k, v]) => ({ k, v, pct: n ? Math.round((v / n) * 100) : 0 }));
        return {
          n, g, r: n - g,
          gPct: n ? Math.round((g / n) * 100) : 0,
          ents: its.reduce((s, x) => s + ((x.entities || []).length), 0),
          avgLen: lenN ? Math.round(lenSum / lenN) : 0,
          intents: top,
        };
      },

      async run() {
        this.loading = true; this.status = ''; this.result = null; this.batchResult = null;
        const fd = new FormData();
        fd.append('purpose', this.addPurpose || 'review');   // 추가 용도(STEP 1 선택)
        if (this.activeTabId !== 'image') fd.append('add_only', '1');   // 추가=저장만 · 실행은 STEP 2(이미지는 즉시)
        let endpoint = '/run';
        if (this.activeTabId === 'image') {
          if (!this.imgFiles.length) { this.status = '이미지를 선택하세요'; this.loading = false; return; }
          this.imgFiles.forEach((f, i) => fd.append('image' + i, f));
          fd.append('displayServiceName', this.group);
          fd.append('title', this.imgTitle);
          fd.append('caption', this.imgCaption);
        } else if (this.activeTabId === 'excel') {
          if (!this.excelFile) { this.status = '엑셀/CSV 파일을 선택하세요'; this.loading = false; return; }
          fd.append('file', this.excelFile); endpoint = '/run-batch';
        } else {
          fd.append('displayServiceName', this.group);
          fd.append('title', this.txtTitle);
          fd.append('body', this.txtBody);
          fd.append('source_url', this.txtUrl);
        }
        try {
          if (endpoint === '/run-batch') this.pollIngestStatus();   // 실행 큐 진척도 실시간
          const j = await (await this._afetch(endpoint, { method: 'POST', headers: this.authToken ? { 'Authorization': 'Bearer ' + this.authToken } : {}, body: fd })).json();
          if (j.error) { this.status = '오류: ' + j.error; }
          else if (j.pending) { this.status = '✓ ' + (j.added || 0) + '건 추가됨 · STEP 2 모델 실행에서 초안을 생성하세요'; this.loadDash(); }
          else if (j.source === 'excel') { this.batchResult = j; }
          else { this.result = j; }
        } catch (e) { this.status = '오류: ' + e; }
        finally { this.loading = false; }
      },
}));
