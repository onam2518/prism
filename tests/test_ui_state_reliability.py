"""검수·설정 화면의 실패 상태가 성공·빈 목록으로 바뀌지 않는지 Node VM으로 확인한다."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestUiStateReliability(unittest.TestCase):
    def test_frontend_save_and_load_failures_keep_last_good_state(self):
        if not shutil.which("node"):
            self.skipTest("node 미설치")
        script = r"""
const fs = require('fs'), vm = require('vm');
const ctx = { window: {}, console, setTimeout, clearTimeout };
ctx.globalThis = ctx;
vm.createContext(ctx);
for (const path of process.argv.slice(2)) vm.runInContext(fs.readFileSync(path, 'utf8'), ctx, { filename: path });
const app = {};
for (const make of ctx.window.PRISM_APP_PARTS) {
  Object.defineProperties(app, Object.getOwnPropertyDescriptors(make()));
}
app._authHeaders = () => ({}); app._err = (m) => { app.lastErr = m; };
app.liveToast = () => {}; app.celebratePoints = () => {}; app.hybInit = () => {}; app.hybEntLoad = () => {};
app.isRouter = () => false; app.cfgModel = 'model-a'; app.reviewer = 'reviewer';
const reply = (ok, status, body) => ({ ok, status, json: async () => body });
(async () => {
  const stableCfg = { model: 'stable' };
  app.cfg = stableCfg; app._afetch = async () => reply(false, 500, { error: '서버 오류' });
  await app.saveTextSlot();
  const textFail = { cfgKept: app.cfg === stableCfg, msg: app.slotMsg };
  app._afetch = async () => reply(true, 200, { model: 'saved' }); await app.saveTextSlot();
  const textOk = { cfg: app.cfg.model, msg: app.slotMsg };
  app.cfg = stableCfg; app.reasoning = 'default'; app._afetch = async () => reply(false, 400, { error: '거절' });
  await app.setReasoning('high');
  const reasoningFail = { cfgKept: app.cfg === stableCfg, reasoning: app.reasoning, msg: app.reasoningMsg };
  app._afetch = async () => reply(true, 200, { reasoning: 'high' }); await app.setReasoning('high');
  const reasoningOk = { cfg: app.cfg.reasoning, reasoning: app.reasoning, msg: app.reasoningMsg };
  app.finalQueue = { items: [{ hash: 'last' }] }; app._afetch = async () => { throw new Error('offline'); };
  await app.loadFinalQueue();
  const finalFail = { kept: app.finalQueue.items[0].hash, msg: app.finalErr, busy: app.finalBusy };
  app.evalRuns = [{ id: 7 }]; await app.loadEvalRuns();
  const evalListFail = { kept: app.evalRuns[0].id, msg: app.evalRunsErr, busy: app.evalRunsBusy };
  app.evalRunId = 7; app.goldenResult = { status: 'running', cursor: 2 }; app.pollEvalRun(7);
  await new Promise((resolve) => setTimeout(resolve, 0));
  const pollFail = { kept: app.goldenResult.cursor, msg: app.goldenMsg, busy: app.goldenBusy };
  const row = { hash: 'item', title: '제목', grade: '', category: [], intent: [], entities: [], fb: {} };
  app.openFinalAnswer(row); let patchCalls = 0; app._afetch = async () => { patchCalls++; return reply(true, 200, { ok: true }); };
  await app.saveFinalAnswer();
  const gradeBlank = { grade: app.fa.grade, patchCalls, msg: app.faErr };
  app.fa.grade = 'G'; app.faOpen = true; app._afetch = async () => { patchCalls++; return reply(true, 200, { ok: true }); };
  await app.saveFinalAnswer();
  const gradeSelected = { patchCalls, open: app.faOpen };
  let confirmCalls = 0; let release; const pending = new Promise((resolve) => { release = resolve; });
  const item = { hash: 'ar', service: 's', title: 't', ai: { verdict: 'good', reason: '' } };
  app._postFb = async () => { confirmCalls++; return pending; };
  const one = app.arConfirm(item, 'good'), two = app.arConfirm(item, 'bad');
  const during = { calls: confirmCalls, saving: item._saving };
  release({ ok: false }); await one; await two;
  const failed = { done: !!item._done, saving: item._saving, msg: app.arMsg };
  app._postFb = async () => ({ ok: true }); await app.arConfirm(item, 'good');
  const retried = { done: item._done, saving: item._saving };
  console.log(JSON.stringify({ textFail, textOk, reasoningFail, reasoningOk, finalFail, evalListFail, pollFail, gradeBlank, gradeSelected, during, failed, retried }));
})().catch((e) => { console.error(e.stack); process.exit(1); });
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
            path = f.name
        try:
            result = subprocess.run(
                ["node", path,
                 os.path.join(ROOT, "prism/vendor/app-01-bulkpertxt.js"),
                 os.path.join(ROOT, "prism/vendor/app-04-_err.js"),
                 os.path.join(ROOT, "prism/vendor/app-08-copytext.js"),
                 os.path.join(ROOT, "prism/vendor/app-18-autoreview.js")],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr[:600])
            out = json.loads(result.stdout)
        finally:
            os.unlink(path)
        self.assertTrue(out["textFail"]["cfgKept"])
        self.assertIn("서버 오류", out["textFail"]["msg"])
        self.assertEqual(out["textOk"], {"cfg": "saved", "msg": "✓ 적용됨"})
        self.assertEqual(out["reasoningFail"]["reasoning"], "default")
        self.assertTrue(out["reasoningFail"]["cfgKept"])
        self.assertEqual(out["reasoningOk"], {"cfg": "high", "reasoning": "high", "msg": "✓ 저장됨"})
        self.assertEqual(out["finalFail"]["kept"], "last")
        self.assertFalse(out["finalFail"]["busy"])
        self.assertEqual(out["evalListFail"]["kept"], 7)
        self.assertFalse(out["evalListFail"]["busy"])
        self.assertEqual(out["pollFail"]["kept"], 2)
        self.assertIn("마지막 상태", out["pollFail"]["msg"])
        self.assertEqual(out["gradeBlank"]["grade"], "")
        self.assertEqual(out["gradeBlank"]["patchCalls"], 0)
        self.assertIn("등급", out["gradeBlank"]["msg"])
        self.assertEqual(out["gradeSelected"], {"patchCalls": 2, "open": False})
        self.assertEqual(out["during"], {"calls": 1, "saving": True})
        self.assertFalse(out["failed"]["done"])
        self.assertFalse(out["failed"]["saving"])
        self.assertEqual(out["retried"], {"done": "good", "saving": False})

    def test_failure_and_filtered_empty_states_are_rendered(self):
        for rel, names in {
            "prism/ui/05-review.html": ("finalErr", "다시 시도"),
            "prism/ui/13-eval.html": ("evalRunsErr", "goldenMsg", "다시 시도"),
            "prism/ui/18-queue.html": ("!filteredJobs.length", "전체 보기"),
            "prism/ui/19f-autoreview.html": ("it._saving", "저장 중…"),
        }.items():
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                src = f.read()
            for name in names:
                self.assertIn(name, src, "%s: %s" % (rel, name))


if __name__ == "__main__":
    unittest.main()
