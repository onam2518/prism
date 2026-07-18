/* Prism 앱 조각 09 · prismApp 프로퍼티 그룹 — 스튜디오 빌더 + 정책 메타 프롬프트 탭.
   로더(app.js)가 파일명 순으로 디스크립터 병합(게터 보존) · 조각 간 this 공유. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
      // ── 스튜디오 · 선언형 프롬프트 빌더(Atelier step1 이식) ──
      bSpec: { task: '', role: '', background: '', constraints: '', format: '', examples: '', families: ['solar'] },
      bFamilies: [{ id: 'claude', label: 'Claude' }, { id: 'gpt', label: 'GPT' }, { id: 'gemini', label: 'Gemini' }, { id: 'solar', label: 'Solar' }, { id: 'deepseek', label: 'DeepSeek' }],
      bBusy: false, bMsg: '', bResult: null, bApplyStage: '', bApplyBusy: false,
      bFamilyLabel(f) { const x = this.bFamilies.find((v) => v.id === f); return x ? x.label : f; },
      toggleBFamily(id) {
        const a = this.bSpec.families; const i = a.indexOf(id);
        if (i >= 0) a.splice(i, 1); else a.push(id);
      },
      async runBuilder() {
        this.bBusy = true; this.bMsg = ''; this.bResult = null;
        try {
          const r = await (await this._afetch('/builder-compile', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ spec: this.bSpec }) })).json();
          if (r && r.ok) this.bResult = r;
          else this.bMsg = (r && r.error) || '컴파일 실패';
        } catch (e) { this.bMsg = '컴파일 실패'; }
        this.bBusy = false;
      },
      async applyBuilder(fam) {
        const p = this.bResult && this.bResult.ok && this.bResult.prompts[fam];
        if (!p || !this.bApplyStage) return;
        this.bApplyBusy = true;
        try {
          // stage_prompts 는 4단계 전체 덮어쓰기 계약 → 기존 오버라이드 보존해 대상 단계만 교체
          const sp = Object.assign({ extract: '', analyze: '', review: '', judge: '' }, (this.cfg && this.cfg.stagePrompts) || {});
          sp[this.bApplyStage] = p;
          const r = await (await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ stage_prompts: sp, stage: this.bApplyStage }) })).json();
          if (r && !r.error) { this.liveToast(this.bFamilyLabel(fam) + ' 프롬프트를 ' + this.bApplyStage + ' 단계에 적용'); await this.refreshConfig(); }
          else this.bMsg = (r && r.error) || '적용 실패';
        } catch (e) { this.bMsg = '적용 실패'; }
        this.bApplyBusy = false;
      },
      // ── 정책 · 메타 프롬프트 탭: 단계 원천 지시 편집(빈 값 저장 = 기본 복원) ──
      stageDefs: [{ id: 'extract', label: '추출 · 리드문/엔티티' }, { id: 'analyze', label: '분석 · 인텐트/카테고리' },
                  { id: 'review', label: '검수 · 품질' }, { id: 'judge', label: '판정 · 법령' }],
      stageDrafts: { extract: '', analyze: '', review: '', judge: '' },
      stageDefaults: {}, stageLearned: {}, stageMsg: {}, stageBusy: false,
      async loadStageDrafts() {
        try {
          await this.refreshConfig();
          const d = await (await this._afetch('/prompt-defaults', { headers: this._authHeaders() })).json();
          this.stageDefaults = d.defaults || {};
          this.stageLearned = d.learned || {};
          const ov = (this.cfg && this.cfg.stagePrompts) || {};
          for (const s of this.stageDefs) this.stageDrafts[s.id] = ov[s.id] || this.stageDefaults[s.id] || '';
        } catch (e) {}
      },
      async _saveStagePrompts(id, value) {
        const sp = Object.assign({ extract: '', analyze: '', review: '', judge: '' }, (this.cfg && this.cfg.stagePrompts) || {});
        sp[id] = value;
        const r = await (await this._afetch('/config', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ stage_prompts: sp, stage: id }) })).json();
        if (r && r.error) throw new Error(r.error);
        await this.loadStageDrafts();
      },
      async saveStage(id) {
        this.stageBusy = true; this.stageMsg = { ...this.stageMsg, [id]: '' };
        try {
          const draft = (this.stageDrafts[id] || '').trim();
          const isDefault = draft === (this.stageDefaults[id] || '').trim();
          await this._saveStagePrompts(id, isDefault ? '' : draft);   // 기본값 그대로면 오버라이드 제거
          this.stageMsg = { ...this.stageMsg, [id]: isDefault ? '기본값으로 저장됨' : '저장됨' };
        } catch (e) { this.stageMsg = { ...this.stageMsg, [id]: '저장 실패: ' + (e.message || '') }; }
        this.stageBusy = false;
      },
      async resetStage(id) {
        this.stageBusy = true;
        try {
          await this._saveStagePrompts(id, '');
          this.stageMsg = { ...this.stageMsg, [id]: '기본값 복원됨' };
        } catch (e) { this.stageMsg = { ...this.stageMsg, [id]: '복원 실패: ' + (e.message || '') }; }
        this.stageBusy = false;
      },
}));
