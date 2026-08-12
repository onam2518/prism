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
          if (Array.isArray(this.cfg.availableModels)) this.availableModels = this.cfg.availableModels;
          if (Array.isArray(this.cfg.visionCandidates)) this.visionCandidates = this.cfg.visionCandidates;
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
          if (typeof this.cfg.legalEnabled === 'boolean') this.legalEnabled = this.cfg.legalEnabled;
          if (typeof this.cfg.assistModel === 'string' && this.cfg.assistModel) this.assistModel = this.cfg.assistModel;
          if (Array.isArray(this.cfg.assistModels)) this.assistModels = this.cfg.assistModels;
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
      // 검수 보조 에이전트 모델(시스템 설정 · 관리자). 고른 즉시 저장하고 서버가 해석한 값을
      // 되받아 화면에 반영한다. 화면이 실제로 쓰이는 모델과 다른 이름을 들고 있으면 안 된다
      // (해석은 서버 config.assist_model 한 곳 · 화면은 판정하지 않는다).
      async saveAssistModel() {
        this.assistMsg = '저장 중…';
        try {
          const r = await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ assist_model: this.assistModel || '' }) });
          const j = await r.json();
          if (!r.ok || (j && j.error)) { this.assistMsg = '오류: ' + ((j && j.error) || r.status); return; }
          this.cfg = j;
          if (typeof j.assistModel === 'string' && j.assistModel) this.assistModel = j.assistModel;
          this.assistMsg = '✓ 저장됨';
        } catch (e) { this.assistMsg = '오류: ' + e; }
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
      // 모델 필터: 빈 값=전체 · PENDING 상수=아직 초안이 없는(미실행) 건만
      contentModel: '',
      PENDING_MODEL: '(미실행)',
      get contentModels() {
        const ms = new Set();
        ((this.dashData && this.dashData.contents) || []).forEach((c) => { if (c.model) ms.add(c.model); });
        return [...ms].sort();
      },
      get contentRows() {
        let all = (this.dashData && this.dashData.contents) || [];
        if (this.jobFilter) {
          const set = new Set(this.jobFilter.hashes);
          all = all.filter((c) => set.has(c.hash));
        }
        if (this.contentModel === this.PENDING_MODEL) return all.filter((c) => !c.model);
        if (this.contentModel) return all.filter((c) => (c.model || '') === this.contentModel);
        return all;
      },
      // 다중 선택 재실행: 체크한 건만 /rerun-all 에 hashes 로 넘긴다(서버가 scope=selected 로 승격).
      // 선택은 '지금 보이는 행'만 대상 — 필터를 바꾸면 비운다(안 보이는 건이 딸려 실행되는 사고 방지).
      pickSel: {}, pickBusy: false,
      get pickedHashes() { return Object.keys(this.pickSel).filter((h) => this.pickSel[h]); },
      get pickAllOn() {
        const rs = this.contentRows;
        return rs.length > 0 && rs.every((c) => this.pickSel[c.hash]);
      },
      togglePick(h) { this.pickSel = Object.assign({}, this.pickSel, { [h]: !this.pickSel[h] }); },
      togglePickAll() {
        const on = !this.pickAllOn, next = Object.assign({}, this.pickSel);
        this.contentRows.forEach((c) => { next[c.hash] = on; });
        this.pickSel = next;
      },
      clearPick() { this.pickSel = {}; },
      pickModel(v) { this.contentModel = v; this.clearPick(); },
      async rerunPicked(force) {
        const hs = this.pickedHashes;
        if (!hs.length || this.pickBusy) return;
        const mname = '기본 실행 모델';                 // 모델 비움('') = 서버 기본 실행 모델
        if (!force && !(await this.dsConfirm(hs.length + '건을 ' + mname + ' 로 재실행합니다(건당 비용 발생 · 기존 초안은 이력 보존) · 진행할까요?', { ok: '재실행' }))) return;
        this.pickBusy = true;
        let retryConfirm = false;
        try {
          const r = await (await this._afetch('/rerun-all', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ model: '', hashes: hs, force: !!force }) })).json();
          if (r && !r.error) {
            this.liveToast('선택 ' + (r.done || 0) + '건 재실행 완료'
              + (r.failed ? (' · 실패 ' + r.failed) : '')
              + (r.over_cap ? (' · 상한 초과 ' + r.over_cap + '건 제외') : ''));
            this.clearPick(); this.loadDash(); this.loadRaw && this.loadRaw(); this.fetchIngestStatus();
          } else if (r && !force && /퀘스트/.test(r.error || '')) retryConfirm = true;
          else this._err((r && r.error) || '재실행 실패');
        } catch (e) { this._err('재실행 실패'); }
        this.pickBusy = false;
        if (retryConfirm) {
          const ok = await this.dsConfirm('퀘스트(검수 목표) 진행 중입니다. 초안을 새로 만들면 기존 검수 의견과 어긋날 수 있습니다. 선택한 ' + hs.length + '건을 재실행할까요?', { title: '퀘스트 중 재실행', ok: '재실행', danger: true });
          if (ok) await this.rerunPicked(true);
        }
      },
      fmtEta(s) { s = Math.max(0, Math.round(s || 0)); return s >= 60 ? (Math.floor(s / 60) + '분 ' + (s % 60) + '초') : (s + '초'); },
      // 개별 콘텐츠 재실행: 서버 기본 실행 모델로 이 건만 초안 재생성(/rerun · 이력 보존)
      // 퀘스트 진행 중 서버 차단에 걸리면 확인 모달을 거쳐 이 한 건만 강행(force) — 기본 보호는 유지
      rerunBusy: {},
      async rerunOne(c, force) {
        if (this.rerunBusy[c.hash]) return;
        this.rerunBusy = { ...this.rerunBusy, [c.hash]: true };
        let retryConfirm = false;
        try {
          const r = await (await this._afetch('/rerun', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ hash: c.hash, model: '', force: !!force }) })).json();   // 모델 비움 = 서버 기본 실행 모델
          if (r && !r.error) { this.liveToast((c.title || '콘텐츠') + ' · 재실행 완료'); this.loadDash(); this.loadRaw && this.loadRaw(); this.fetchIngestStatus(); }
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
      // 정답셋 현황은 loadVerHist() 로 버전별 지표를 병렬 팬아웃한다 · 메뉴 왕복 시 재조회 억제.
      // 직접 호출(새로고침 버튼·학습 반영 직후·SSE)은 스로틀을 거치지 않아 최신화가 보장된다.
      loadGoldenStatusThrottled() { const now = Date.now(); if (now - (this._lastGoldenStatus || 0) > 4000) { this._lastGoldenStatus = now; this.loadGoldenStatus(); } },
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
      exportEval() {
        const t = this.tr || {}; const rows = [['항목', '값'], ['prompt_version', t.prompt_version || ''], ['fallbacks', (t.fallbacks || []).join(' · ')], ['cost_usd', t.cost_usd || 0], ['tokens', JSON.stringify(t.tokens || {})], ['latency_ms', JSON.stringify(t.latency_ms || {})]];
        this._dl('prism_eval.csv', rows);
      },
      selectTab(id) { this.activeTabId = id; this.status = ''; },

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
        fd.append('add_only', '1');   // 추가=저장만 · 실행은 STEP 2
        let endpoint = '/run';
        if (this.activeTabId === 'excel') {
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
          else if (j.pending) {
            // 신규/기존 구분 표기: 누적 파일 재업로드 시 '전부 추가된 것처럼' 보이던 혼선 방지(2026-08-05)
            const ex = j.existing ? ' · 기존 ' + j.existing + '건 유지(재실행 안 함)' : '';
            this.status = j.added ? ('✓ 신규 ' + j.added + '건 추가' + ex + ' · STEP 2 모델 실행에서 초안을 생성하세요')
                                  : ('✓ 신규 없음' + ex + ' · 모두 이미 등록된 콘텐츠입니다');
            this.loadDash();
          }
          else if (j.source === 'excel') { this.batchResult = j; }
          else { this.result = j; }
        } catch (e) { this.status = '오류: ' + e; }
        finally { this.loading = false; }
      },
}));
