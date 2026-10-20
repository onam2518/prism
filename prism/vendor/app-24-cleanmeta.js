/* 실험실 클린 메타: 원문과 결과는 현재 화면에서만 보관. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
  cmTitle: '', cmBody: '', cmContext: '', cmModel: 'solar-decide', cmMode: 'cascade', cmGate: 0.9, cmPrice: '',
  cmPolicySet: 'common_v1', cmDefaults: null, cmScreenPrompt: '', cmDetailPrompt: '',
  cmCaseRef: '', cmFacts: [], cmLoadingDefaults: false,
  cmBusy: false, cmError: '', cmResult: null,
  async cmInit() {
    if (this.cmDefaults || this.cmLoadingDefaults) return;
    this.cmLoadingDefaults = true;
    try {
      const response = await this._afetch('/lab-cleanmeta-try', {
        method: 'POST', headers: this._authHeaders(), body: JSON.stringify({action: 'defaults'})
      });
      const result = await response.json();
      if (!response.ok || !result.ok || typeof result.prompts?.screen !== 'string' || typeof result.prompts?.detail !== 'string' || !Array.isArray(result.external_facts)) throw new Error(result.error || '기본 프롬프트를 불러오지 못했습니다');
      this.cmDefaults = result;
      this.cmScreenPrompt = result.prompts.screen; this.cmDetailPrompt = result.prompts.detail;
    } catch (error) { this.cmError = error.message || '기본값 연결을 확인하세요'; }
    finally { this.cmLoadingDefaults = false; }
  },
  cmResetPrompt(stage) {
    if (!this.cmDefaults) return;
    if (stage === 'screen') this.cmScreenPrompt = this.cmDefaults.prompts.screen;
    else this.cmDetailPrompt = this.cmDefaults.prompts.detail;
  },
  cmAddFact() {
    const option = (this.cmDefaults?.external_facts || []).find(f => !this.cmFacts.some(x => x.atom_id === f.id));
    if (option) this.cmFacts.push({atom_id: option.id, value: 'unknown', source: ''});
  },
  cmPolicies(run, layer) { return (run.policies || []).filter(p => p.layer === layer); },
  cmPartLabel(part) { return ({title: '제목', body: '본문'})[part] || part; },
  get cmModels() {
    const groups = (this.textGroups || []).filter(g => g.on || (this.cfg && this.cfg.forcedMock));
    return [...new Set(['solar-decide', ...groups.flatMap(g => g.items.map(item => item.model)), this.cmModel])].filter(Boolean);
  },
  cmScoreLabel(run) { return run.score_source === 'llm_self_report' ? '판단 점수 (모델 자기보고)' : '선택 확률'; },
  cmLabel(value) {
    return ({pass_candidate: '정상 후보', review: '검수 필요', hold: '보류', complete: '검사 완료',
      partial: '일부 미검사·실패', failed: '검사 실패', requested: '상세 검사', not_required: '선별 종료',
      unavailable: '선별 실패', present: '존재', absent: '부재', unknown: '판단 불가', matched: '조건 충족',
      not_matched: '조건 비해당', undetermined: '판단 불가', no_common_issue_found: '검사 범위 내 공통 사유 미발견',
      review_required: '공통 정책 검수 필요', insufficient_evidence: '공통 판단 자료 부족', common: '회사 공통 안전',
      legal: '법령·권리 검토', age: '연령·노출 조건', service: '서비스·품질·광고', urgent_review: '긴급 검수 후보',
      restrict_review: '제한 검수 후보', age_gate_review: '연령 검토 후보', legal_review: '법령 검토 후보',
      service_review: '별도 서비스 검토', none: '없음'})[value] || value;
  },
  cmProbability(value) { return value == null ? '미제공' : (value * 100).toFixed(1) + '%'; },
  async cmTry() {
    if (this.cmBusy) return;
    this.cmBusy = true; this.cmError = ''; this.cmResult = null;
    try {
      if (this.cmPolicySet === 'common_v1' && !this.cmDefaults) await this.cmInit();
      if (this.cmPolicySet === 'common_v1' && !this.cmDefaults) throw new Error(this.cmError || '기본값을 불러온 뒤 실행하세요');
      const common = this.cmPolicySet === 'common_v1' ? {
        prompts: {screen: this.cmScreenPrompt, detail: this.cmDetailPrompt},
        case_ref: this.cmCaseRef, reviewed_facts: this.cmFacts.map(f => ({...f, subject_ref: this.cmCaseRef}))
      } : {};
      const response = await this._afetch('/lab-cleanmeta-try', {
        method: 'POST', headers: this._authHeaders(), body: JSON.stringify({
          title: this.cmTitle, body: this.cmBody, context: this.cmContext, model: this.cmModel, mode: this.cmMode,
          gate: Number(this.cmGate), input_price: this.cmPrice === '' ? null : Number(this.cmPrice), policy_set: this.cmPolicySet, ...common
        })
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || '요청을 처리하지 못했습니다');
      this.cmResult = result;
    } catch (error) { this.cmError = error.message || '연결을 확인하세요'; }
    finally { this.cmBusy = false; }
  },
  cmDownload() {
    if (!this.cmResult) return;
    const blob = new Blob([JSON.stringify(this.cmResult, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url;
    link.download = 'clean-meta-' + this.cmResult.runs[0].input_hash.slice(0, 12) + '.json';
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}));
