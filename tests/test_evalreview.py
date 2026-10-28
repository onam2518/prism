"""유료 진단의 명시적 실행·재개·원문 고정·팀 접근 검증."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from prism import evalreview as R, evaluation as E
from prism.store import Store


class FakeJudge:
    def __init__(self, fail_coverage=False):
        self.calls = []
        self.fail_coverage = fail_coverage

    def complete_json(self, system, user, tag):
        data = json.loads(user)
        self.calls.append(data)
        if "reference_claims" in data:
            obj = {"items": [{"id": key, "verdict": "covered", "evidence_ids": ["1"]}
                             for key in data["reference_claims"]]}
            if self.fail_coverage:
                obj["items"][0]["evidence_ids"] = ["made-up"]
        else:
            obj = {"claims": [{"claim": data["candidate"], "verdict": "supported", "evidence_ids": ["1"]}]}
        return obj, NS(text=json.dumps(obj), in_tok=10, out_tok=5, cost_usd=.01, retries=0,
                       fail_kind=None, raw={"model": "judge-snapshot", "choices": [{"finish_reason": "stop"}]})


class GroundedReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.st = Store(str(Path(tmp.name) / "t.db"))
        p = patch.object(R, "_SV", NS(get_store=lambda: self.st)); p.start(); self.addCleanup(p.stop)
        self.sel = {"kind": "eval", "id": 1, "round": 0}
        self.key = R._key(self.sel); self.identity = ("team-a", self.key)
        self.state = {"status": "running", "items": [{"hash": "doc", "source": {"body": "동결 원문"},
                      "expected": {"summary": "정답"}, "got": {"A": {"summary": "산출"}}}],
                      "records": {}, "attempts": [], "input_tokens": 0, "output_tokens": 0,
                      "cost_usd": 0., "cost_complete": True}
        R._CANCEL.discard(self.identity)
        self.addCleanup(R._CANCEL.discard, self.identity)

    def work(self, llm):
        with patch.object(E, "semantic_embed", side_effect=lambda texts: [[1., 0.] for _ in texts]):
            R._worker(self.identity, self.key, "team-a", self.state, llm)

    def test_failed_evidence_not_counted_and_resume_reuses_success(self):
        judge = FakeJudge(fail_coverage=True); self.work(judge)
        self.assertEqual(self.state["status"], "partial")
        self.assertIsNone(self.state["reports"]["A"]["metrics"]["reference_claim_coverage"]["value"])
        self.assertEqual(len(judge.calls), 3)
        self.assertEqual(judge.calls[0]["source_units"]["1"], "동결 원문")
        judge.fail_coverage = False; self.work(judge)
        self.assertEqual(self.state["status"], "done")
        self.assertEqual(len(judge.calls), 4, "only failed coverage is retried")
        self.assertAlmostEqual(self.state["cost_usd"], .04)
        report = self.state["reports"]["A"]
        self.assertEqual(report["metrics"]["reference_claim_coverage"]["value"], 1)
        coverage = next(r for r in self.state["records"].values() if r["stage"] == "coverage")
        self.assertEqual(coverage["review"]["items"][0]["claim"], "정답")
        self.work(judge)
        self.assertEqual(len(judge.calls), 4)

    def test_cancel_before_calls_preserves_resume(self):
        R._CANCEL.add(self.identity); judge = FakeJudge(); self.work(judge)
        self.assertEqual(self.state["status"], "cancelled")
        self.assertEqual(judge.calls, [])
        self.work(judge)
        self.assertEqual(self.state["status"], "done")

    def test_lookup_checks_team_authorization_before_storage(self):
        self.st.save_report(self.key, copy.deepcopy(self.state), team="team-a")
        with patch.object(R, "_history", return_value={"ok": False, "error": "not found"}) as history:
            self.assertFalse(R.get(self.sel, "team-b")["ok"])
            self.assertFalse(R.cancel(self.sel, "team-b")["ok"])
            self.assertFalse(R.start(self.sel, "team-b")["ok"])
        self.assertEqual(history.call_args.args[1], "team-b")

    def test_get_is_readonly_and_detects_stale_worker(self):
        self.st.save_report(self.key, self.state, team="team-a")
        with patch.object(R, "_history", return_value={"ok": True}), patch.object(E, "semantic_embed", side_effect=AssertionError("paid")):
            result = R.get(self.sel, "team-a")
        self.assertTrue(result["stalled"])
        self.assertNotIn("items", result)

    def test_original_required_no_current_content_fallback(self):
        with patch.object(R, "_history", return_value={"ok": True, "status": "done", "detail_mode": "all",
                "items": [{"hash": "doc", "expected": {"summary": "정답"}, "got": {}}]}):
            result = R.start(self.sel)
        self.assertFalse(result["ok"])
        self.assertIn("원문", result["error"])

    def test_invalid_selector_rejected(self):
        for data in ({"kind": "bad"}, {"kind": "eval", "id": 0}, {"kind": "compare", "key": "other"}):
            self.assertFalse(R.get(data)["ok"])


if __name__ == "__main__":
    unittest.main()

class StartReviewTests(unittest.TestCase):
    setUp = GroundedReviewTests.setUp
    work = GroundedReviewTests.work

    def test_start_freezes_settings_and_done_is_idempotent(self):
        from prism.config import Config
        judge = FakeJudge()
        judge.mock = False; judge.model = 'judge'; judge.cfg = Config()
        server = NS(get_store=lambda: self.st, Handler=NS(server_mock=False),
                    llm_for_model=lambda model, mock: (judge, 'test'))
        history = {"ok": True, "status": "done", "detail_mode": "all", "items": self.state["items"]}
        class Deferred:
            def __init__(self, target, args, daemon): self.args = args
            def start(self): pass
            def is_alive(self): return True
        with patch.object(R, '_SV', server), patch.object(R, '_history', return_value=history), patch.object(R.threading, 'Thread', Deferred):
            result = R.start({**self.sel, 'model':'judge'}, 'team-a')
            self.assertTrue(result['ok'], result)
            worker = R._ACTIVE[self.identity]
            frozen = worker.args[3]
            self.assertEqual(worker.args[4].max_output_tokens, 4096)
            self.assertEqual(worker.args[4].cfg.retry.max_retries, 0)
            self.assertFalse(R.start({**self.sel, 'model':'judge'}, 'team-a')['ok'])
            history['items'][0]['source']['body'] = '나중에 수정한 원문'
            self.assertEqual(frozen['items'][0]['source']['body'], '동결 원문')
            R._ACTIVE.pop(self.identity)
            history['items'][0]['source']['body'] = '동결 원문'
            frozen['status'] = 'done'
            self.st.save_report(self.key, frozen, team='team-a')
            self.assertEqual(R.start({**self.sel, 'model':'judge'}, 'team-a')['status'], 'done')
        self.addCleanup(R._ACTIVE.pop, self.identity, None)

    def test_missing_semantic_only_is_retried(self):
        judge = FakeJudge()
        with patch.object(E, 'semantic_embed', side_effect=RuntimeError('unavailable')):
            R._worker(self.identity, self.key, 'team-a', self.state, judge)
        self.assertEqual(self.state['status'], 'partial')
        self.assertEqual(len(judge.calls), 3)
        self.work(judge)
        self.assertEqual(self.state['status'], 'done')
        self.assertEqual(len(judge.calls), 3)

    def test_mutation_endpoints_require_admin(self):
        from prism import serve
        for route in ('/eval-grounding-start', '/eval-grounding-cancel'):
            self.assertEqual(serve._POST_ROUTES[route][1], 'admin')
