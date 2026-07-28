/* Prism 앱 조각 12 · prismApp 프로퍼티 그룹. 검수운영: 팀원의 여력 · 일정 · 일 나누기.
   화면 prism/ui/19b-crew.html · 백엔드 prism/crewops.py(GET /crew · POST /crew-*).
   표시 원칙: 지표는 '약속 대비 어디쯤인가'로 보이고, 이상 신호는 경고가 아니라
   다시 나누기·도움 주기 버튼으로 잇는다. 문구는 기계적인 말 대신 일상어로 쓴다. */
window.PRISM_APP_PARTS = window.PRISM_APP_PARTS || [];
window.PRISM_APP_PARTS.push(() => ({

      crewTab: 'dash',                    // dash(한눈에 보기) | people(팀원과 일정) | assign(일 나눠주기)
      crewData: null, crewBusy: false, crewMsg: '',
      crewPlanRes: null, crewMoveRes: null, crewEscRes: null, crewAutoRes: null,
      crewAsgScope: 'unassigned', crewAsgLimit: 200, crewAsgMin: 2, crewDue: '',

      get crewMembers() { return (this.crewData && this.crewData.members) || []; },
      get crewSum() { return (this.crewData && this.crewData.summary) || {}; },
      get crewBurn() { return (this.crewData && this.crewData.burndown) || []; },
      // 그래프 세로 기준 = 남은 양과 끝낸 양 중 큰 값(둘이 같은 축을 쓰도록) · 0 나눗셈 방지로 하한 1
      get crewBurnMax() { return Math.max(1, ...this.crewBurn.map((d) => Math.max(d.left || 0, d.done || 0))); },

      // 나눠줄 후보: 검수 대상 목록(loadRaw 가 채운 rawData.items)에서 고른다 · 정답 문항(가상 행)은 제외
      get crewCfg() { return (this.crewData && this.crewData.settings) || {}; },
      get crewAutoTxt() {
        const r = this.crewAutoRes;
        if (!r) return '';
        const bits = [];
        if (r.wave) bits.push(r.wave.n ? ('자동 배정 ' + r.wave.n + '건') : ('배정 대상 없음' + (r.wave.error ? (' · ' + r.wave.error) : '')));
        if (r.rebalance) bits.push('재배정 ' + r.rebalance.n + '건');
        if (r.escalate) bits.push('추가 배정 ' + r.escalate.n + '건');
        return bits.length ? ('이번 주기(' + r.cycle + ') · ' + bits.join(' · ')) : ('이번 주기(' + r.cycle + ') 실행 대상 없음');
      },

      get crewAsgPool() {
        const rows = ((this.rawData || {}).items || []).filter((r) => !this.isGoldRow(r));
        const pool = this.crewAsgScope === 'unassigned'
          ? rows.filter((r) => !((r.assignees || []).length)) : rows;
        return pool.slice(0, Math.max(1, this.crewAsgLimit || 200));
      },

      async _crewPost(path, body) {
        return await (await this._afetch(path, { method: 'POST', headers: this._authHeaders(),
                                                 body: JSON.stringify(body) })).json();
      },

      async loadCrew() {
        this.crewBusy = true;
        try {
          const r = await (await this._afetch('/crew', { headers: this._authHeaders() })).json();
          if (r && r.ok) this.crewData = r;
          else this._err((r && r.error) || '검수운영 정보를 불러오지 못했습니다');
        } catch (e) { this._err('검수운영 정보를 불러오지 못했습니다'); }
        finally { this.crewBusy = false; }
        this.loadAssignLog();
      },

      async crewSave(m, patch) {
        // 낙관적 반영 없이 서버 응답으로만 갱신한다. 검증(상태·날짜 형식)이 서버에 있어
        // 미리 그려두면 거절된 값이 화면에 남는다.
        this.crewBusy = true;
        try {
          const r = await this._crewPost('/crew-profile', { uid: m.id, patch });
          if (r && r.ok) { m.profile = r.profile; this.liveToast(m.name + ' 일정 저장 완료'); await this.loadCrew(); }
          else this._err((r && r.error) || '저장하지 못했습니다');
        } catch (e) { this._err('저장하지 못했습니다'); }
        finally { this.crewBusy = false; }
      },

      crewToggleDay(m, i) {
        const cur = (m.profile.workdays || []).slice();
        const at = cur.indexOf(i);
        if (at >= 0) cur.splice(at, 1); else cur.push(i);
        return cur.sort();
      },

      async crewPlan(apply) {
        const hashes = this.crewAsgPool.map((r) => r.hash);
        if (!hashes.length) { this.crewMsg = '배정할 콘텐츠가 없습니다'; return; }
        this.crewBusy = true; this.crewMsg = '';
        try {
          const body = { hashes, min_reviewers: this.crewAsgMin, apply: !!apply };
          if (apply && this.crewDue) body.due_at = Date.parse(this.crewDue) / 1000;
          const r = await this._crewPost('/crew-assign', body);
          if (r && r.ok) {
            this.crewPlanRes = r;
            if (apply) {
              this.crewMsg = r.n + '건 배정 완료';
              this.liveToast('여력 비례 배정 완료 · ' + r.n + '건');
              this.crewPlanRes = null;
              await this.loadCrew(); await this.loadRaw();
            }
          } else { this.crewPlanRes = null; this.crewMsg = (r && r.error) || '배정 계획을 계산하지 못했습니다'; }
        } catch (e) { this.crewMsg = '배정 계획을 계산하지 못했습니다'; }
        finally { this.crewBusy = false; }
      },

      async crewRebalance(apply) {
        this.crewBusy = true;
        try {
          const r = await this._crewPost('/crew-rebalance', { apply: !!apply });
          this.crewMoveRes = r || null;
          if (apply && r && r.ok) {
            this.liveToast('재배정 완료 · ' + r.n + '건');
            await this.loadCrew(); await this.loadRaw();
            this.crewMoveRes = Object.assign({}, r, { applied: true });
          }
          if (this.crewTab !== 'assign') this.crewTab = 'assign';   // 한눈에 보기에서 눌러도 계획이 보이는 곳으로
        } catch (e) { this._err('재배정 계획을 세우지 못했습니다'); }
        finally { this.crewBusy = false; }
      },

      async crewEscalate(apply) {
        this.crewBusy = true;
        try {
          const r = await this._crewPost('/crew-escalate', { apply: !!apply });
          this.crewEscRes = r || null;
          if (apply && r && r.ok) {
            this.liveToast('추가 배정 완료 · ' + r.n + '건');
            await this.loadCrew(); await this.loadRaw(1000);
            this.crewEscRes = Object.assign({}, r, { applied: true });
          }
        } catch (e) { this._err('불일치 건을 찾지 못했습니다'); }
        finally { this.crewBusy = false; }
      },

      async crewAuto() {
        this.crewBusy = true;
        try {
          const r = await this._crewPost('/crew-auto', { apply: true });
          this.crewAutoRes = r || null;
          await this.loadCrew();
        } catch (e) { this._err('자동 운영 실행에 실패했습니다'); }
        finally { this.crewBusy = false; }
      },

      async crewSetting(key, val) {
        this.crewBusy = true;
        try {
          const patch = {}; patch[key] = val;
          const r = await this._crewPost('/crew-profile', { settings: patch });
          if (r && r.ok && this.crewData) this.crewData.settings = r.settings;
          else if (!(r && r.ok)) this._err((r && r.error) || '설정을 저장하지 못했습니다');
        } catch (e) { this._err('설정을 저장하지 못했습니다'); }
        finally { this.crewBusy = false; }
      },

      // ── 표시 헬퍼 ──────────────────────────────────────────────────────
      crewAvatar(a) {
        const M = { boksil: 'boksil-catcher.svg', daesik: 'daesik-batter.svg',
                    yonghee: 'yonghee-pitcher.svg', ddakji: 'ddakji-manager.svg' };
        return M[a] || M.boksil;
      },
      crewSigTxt(s) {
        return ({ green: '정상', yellow: '지연 우려', red: '정체', idle: '여력 있음',
                  done: '완료', leave: '부재', off: '비활성' })[s] || s;
      },
      crewStatusTxt(m) {
        if (m.on_leave) return '부재';
        return ({ active: '활동 중', onboarding: '교육 중', leave: '휴가', inactive: '비활성' })[m.profile.status] || '참여 중';
      },
      crewStatusCls(m) {
        if (m.on_leave || m.profile.status === 'leave') return 'ds-badge--warning';
        if (m.profile.status === 'onboarding') return 'ds-badge--intent';
        if (m.profile.status === 'inactive') return 'ds-badge--neutral';
        return 'ds-badge--success';
      },
      crewHours(h) {
        const v = Number(h || 0);
        if (!v) return '잔여 없음';
        return v < 1 ? ('예상 ' + Math.max(1, Math.round(v * 60)) + '분') : ('예상 ' + v.toFixed(1) + '시간');
      },
      crewDate(ts) {
        if (!ts) return '';
        const d = new Date(Number(ts) * 1000);
        const p = (n) => String(n).padStart(2, '0');
        return (d.getMonth() + 1) + '/' + d.getDate() + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
      },
}));
