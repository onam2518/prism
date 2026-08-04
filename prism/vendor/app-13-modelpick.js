/* Prism 앱 조각 13 · 모델 선택 드롭다운(mpick)의 데이터 원천.

   화면 10곳(사용 모델·정답셋·평가·사전·프롬프트 스튜디오·토픽 스튜디오)이 같은 드롭다운을 쓴다.
   마크업은 조각 파일에 `<x-modelpick …>` 한 줄로 쓰고 page.py 가 펼친다 — 여기서는
   그 마크업이 도는 데 필요한 행 목록(mpRows)과 표시 헬퍼만 제공한다.

   이름·제공자·비용 등급은 서버(/config 의 modelMeta · prism/modelmeta.py)가 원천이다.
   서버에 없는 모델(설정에 갓 적어 넣은 id 등)도 화면에서 그대로 고를 수 있어야 하므로
   메타가 없으면 원본 id 를 이름으로 쓰고 등급은 비운다. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({
      // 서버가 준 모델 표시 정보 {id: {label, family, familyLabel, tier, tierLabel, avgUsd, runs}}
      get modelMeta() { return (this.cfg && this.cfg.modelMeta) || {}; },

      mpMeta(id) { return this.modelMeta[String(id || '')] || null; },
      mpLabel(id) {
        const m = this.mpMeta(id);
        return (m && m.label) || String(id || '');
      },
      // 제공자 아이콘: 외부 이미지 없이(자체완결) 계열 머리글자 + 계열색 배지
      mpIcon(fam) {
        return ({ anthropic: 'A', openai: 'O', google: 'G', deepseek: 'D', upstage: 'U',
                  mistral: 'M', xai: 'X', meta: 'M', alibaba: 'Q' })[fam] || '·';
      },
      // 건당 평균 비용 표기 · 등급을 못 붙인 모델도 실제 숫자는 보여 준다(판단 근거)
      mpAvgText(m) {
        if (!m || !(m.avgUsd > 0)) return '';
        return '건당 $' + (m.avgUsd < 0.001 ? m.avgUsd.toFixed(5) : m.avgUsd.toFixed(4));
      },

      /* 드롭다운 행 목록. src 는 두 가지 중 하나:
         · 문자열 배열       → 그룹 없는 단순 목록
         · {label,on,items:[{provider,model}]} 배열 → 제공자 그룹 목록(사용 모델)
         opts.first = {label, value} 면 맨 위에 고정 항목(전체·현재 설정 모델 등)을 둔다.
         opts.suffix 는 이름 뒤에 붙일 꼬리(예: ' 전용'). */
      mpRows(src, opts) {
        const o = opts || {};
        const rows = [];
        if (o.first) rows.push(this._mpOpt(o.first.value == null ? '' : o.first.value, '', true, o.first.label));
        const list = Array.isArray(src) ? src : [];
        const grouped = list.length && list[0] && typeof list[0] === 'object' && Array.isArray(list[0].items);
        if (grouped) {
          for (const g of list) {
            const items = (g && g.items) || [];
            if (!items.length) continue;
            rows.push({ head: true, label: g.label || '', on: !!g.on });
            for (const it of items) {
              const val = (it.provider || '') + '|' + (it.model || '');
              rows.push(this._mpOpt(val, it.model || '', !!g.on, null, o.suffix));
            }
          }
        } else {
          for (const m of list) {
            if (!m) continue;
            rows.push(this._mpOpt(String(m), String(m), true, null, o.suffix));
          }
        }
        return rows;
      },
      _mpOpt(value, model, on, forceLabel, suffix) {
        const meta = model ? this.mpMeta(model) : null;
        const name = forceLabel != null ? forceLabel : (this.mpLabel(model) + (suffix || ''));
        return { head: false, value: value, model: model, on: on !== false, label: name,
                 family: (meta && meta.family) || '', icon: this.mpIcon((meta && meta.family) || ''),
                 tier: (meta && meta.tier) || '', tierLabel: (meta && meta.tierLabel) || '',
                 avgText: this.mpAvgText(meta),
                 tip: this._mpTip(model, meta) };
      },
      _mpTip(model, meta) {
        if (!model) return '';
        const bits = [model];                                // 원본 id 는 툴팁에 남긴다(디버깅·설정 대조)
        if (meta && meta.runs > 0) bits.push('실행 ' + meta.runs + '건 · ' + this.mpAvgText(meta));
        if (meta && meta.tierLabel) bits.push(meta.tierLabel);
        return bits.join(' · ');
      },
      // 버튼에 보일 현재 선택 이름. 목록에서 찾고, 없으면 원본 id(설정에만 있는 모델)
      mpCurLabel(src, val, opts) {
        const hit = this.mpRows(src, opts).find((r) => !r.head && r.value === val);
        if (hit) return hit.label;
        const bare = String(val || '').indexOf('|') >= 0 ? String(val).split('|').slice(1).join('|') : String(val || '');
        return bare ? this.mpLabel(bare) : ((opts && opts.first && opts.first.label) || '선택…');
      },
      mpCurFamily(src, val, opts) {
        const hit = this.mpRows(src, opts).find((r) => !r.head && r.value === val);
        return hit ? hit.family : '';
      },

      /* 메뉴 위치: 패널이 overflow:hidden 이라 absolute 로 띄우면 잘린다(사용 모델 카드).
         버튼의 화면 좌표를 재서 position:fixed 로 붙인다 — 어떤 조상이 잘라도 안 잘린다.
         아래 공간이 좁으면 위로 펼치고, 오른쪽으로 넘치면 화면 안으로 당긴다. */
      mpMenuBox(btn) {
        const r = btn.getBoundingClientRect();
        const gap = 6, edge = 10;
        const vh = window.innerHeight, vw = window.innerWidth;
        const below = vh - r.bottom - gap - edge, above = r.top - gap - edge;
        const up = below < 220 && above > below;
        const cap = Math.min(420, Math.round(vh * 0.62));
        const box = { position: 'fixed', minWidth: Math.round(r.width) + 'px',
                      maxWidth: Math.max(220, Math.min(560, vw - 2 * edge)) + 'px' };
        // 오른쪽 넘침 방지: 버튼 왼쪽 정렬을 기본으로 하되 화면 밖으로 나가면 당긴다
        box.left = Math.round(Math.max(edge, Math.min(r.left, vw - r.width - edge))) + 'px';
        if (up) {
          box.top = 'auto';
          box.bottom = Math.round(vh - r.top + gap) + 'px';
          box.maxHeight = Math.max(160, Math.min(cap, above)) + 'px';
        } else {
          box.bottom = 'auto';
          box.top = Math.round(r.bottom + gap) + 'px';
          box.maxHeight = Math.max(160, Math.min(cap, below)) + 'px';
        }
        return box;
      },
}));
