"""실제 앱 조각: 예산 설정 오류를 성공으로 표시하지 않고 예산 중단 폴링을 종료한다."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'node 필요')
class TaskBudgetUI(unittest.TestCase):
    def test_save_and_budget_terminal_state(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const box = {window: {}, FormData: class {get() {return '0.05';}}, clearTimeout() {},
             setTimeout() {throw new Error('terminal budget state was polled again');}};
vm.createContext(box);
for (const name of ['app-08-copytext.js', 'app-04-_err.js'])
  vm.runInContext(fs.readFileSync(ROOT + '/prism/vendor/' + name, 'utf8'), box);
const parts = box.window.PRISM_APP_PARTS.map(f => f());
(async () => {
  for (const kind of ['ok', 'http', 'json', 'network']) {
    const before = {taskBudgetUsd: 0.1};
    const app = {cfg: before, _authHeaders() {return {};},
      async _afetch() {
        if (kind === 'network') throw new Error('offline');
        return {ok: kind !== 'http', status: 500,
                async json() {return kind === 'ok' ? {taskBudgetUsd: 0.05} :
                  kind === 'json' ? {error: 'disk full'} : {};}};
      }};
    await parts[0].saveTaskBudget.call(app, {});
    assert.equal(app.cfgBusy, false);
    if (kind === 'ok') {
      assert.equal(app.cfg.taskBudgetUsd, 0.05);
      assert.ok(app.taskBudgetMsg.startsWith('저장됨'));
    } else {
      assert.strictEqual(app.cfg, before);
      assert.ok(app.taskBudgetMsg.startsWith('저장 실패'));
    }
  }
  const result = {budget_stop: true, spent_usd: 0.08, budget_skipped: 6};
  const app = {cmpBusy: true, _authHeaders() {return {};}, liveToast() {}, _err(e) {throw Error(e);},
    async _afetch() {return {async json() {
      return {ok: true, job: {status: 'budget_stop', budget_stop: true, result}};
    }}}};
  parts[1].pollCompare.call(app, 1);
  await new Promise(setImmediate);
  assert.equal(app.cmpBusy, false);
  assert.strictEqual(app.cmpResult, result);
  assert.equal(parts[1].cmpWin.call({cmpResult: result}, 'cost_usd', 0, true), false);
  assert.equal(parts[1].evalRunStatusTxt({status: 'failed', budget_stop: true}), '예산 중단');
})().catch(e => {console.error(e); process.exitCode = 1;});
'''.replace('ROOT', json.dumps(str(root)))
        r = subprocess.run(['node', '-e', script], text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
