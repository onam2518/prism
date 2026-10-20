/* 실험실 이미지 검증: 입력·결과는 현재 화면에서만 보관. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
  icDefaults: null, icLoading: false, icModels: [], icModel: '', icPrompt: '',
  icImage: '', icMime: '', icName: '', icReading: false, icReadSeq: 0,
  icBusy: false, icError: '', icResult: null,
  async icInit() {
    if (this.icDefaults || this.icLoading) return;
    this.icLoading = true; this.icError = '';
    try {
      const r = await this._afetch('/lab-imagecheck-try', {method: 'POST', headers: this._authHeaders(), body: JSON.stringify({action: 'defaults'})});
      const d = await r.json();
      if (!r.ok || !d.ok || !Array.isArray(d.models) || typeof d.prompt !== 'string') throw new Error(d.error || '기본 설정을 불러오지 못했습니다');
      this.icDefaults = d; this.icModels = d.models; this.icPrompt = d.prompt;
      const first = d.models[0]?.items[0]; this.icModel = first ? first.provider + '|' + first.model : '';
    } catch (e) { this.icError = e.message || '연결을 확인하세요'; }
    finally { this.icLoading = false; }
  },
  icResetPrompt() { if (this.icDefaults) { this.icPrompt = this.icDefaults.prompt; this.icResult = null; } },
  icClear() { this.icReadSeq++; this.icImage = ''; this.icMime = ''; this.icName = ''; this.icResult = null; this.icError = ''; this.icReading = false; },
  async icFile(event) {
    if (this.icBusy) return;
    const file = event.target.files?.[0]; event.target.value = '';
    this.icClear(); if (!file) return;
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size === 0 || file.size > 10 * 1024 * 1024) {
      this.icError = 'PNG·JPEG·WebP 이미지 한 장을 10 MiB 이하로 선택하세요'; return;
    }
    const seq = this.icReadSeq; this.icReading = true;
    try {
      const url = await new Promise((resolve, reject) => {
        const reader = new FileReader(); reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error('이미지 파일을 읽지 못했습니다')); reader.readAsDataURL(file);
      });
      if (seq !== this.icReadSeq) return;
      this.icImage = url; this.icMime = file.type; this.icName = file.name;
    } catch (e) { if (seq === this.icReadSeq) this.icError = e.message; }
    finally { if (seq === this.icReadSeq) this.icReading = false; }
  },
  icLabel(value) { return ({present: '관찰됨', absent: '관찰되지 않음', unknown: '판단 불가'})[value] || value; },
  async icTry() {
    if (this.icBusy || this.icReading || !this.icImage || !this.icModel || !this.icPrompt.trim()) return;
    this.icBusy = true; this.icError = ''; this.icResult = null;
    try {
      const r = await this._afetch('/lab-imagecheck-try', {method: 'POST', headers: this._authHeaders(), body: JSON.stringify({
        image: this.icImage.split(',')[1], mime: this.icMime, model: this.icModel, prompt: this.icPrompt
      })});
      const d = await r.json();
      if (!r.ok || !d.ok) throw new Error(d.error || '요청을 처리하지 못했습니다');
      this.icResult = d;
    } catch (e) { this.icError = e.message || '연결을 확인하세요'; }
    finally { this.icBusy = false; }
  },
  icDownload() {
    if (!this.icResult) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(this.icResult, null, 2)], {type: 'application/json'}));
    const a = document.createElement('a'); a.href = url; a.download = 'image-validation-' + Date.now() + '.json';
    a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}));
