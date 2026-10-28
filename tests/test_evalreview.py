"""유료 진단의 명시적 실행·재개·원문 고정·팀 접근 검증."""
import copy
import json
import tempfile
import threading
import time
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
        # Bind the composition root before patching, so cleanup restores it
        # even when another test first imports serve while this patch is active.
        from prism import serve  # noqa: F401
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
        self.state['status'] = 'running'
        self.st.save_report(self.key, self.state, team='team-a')
        self.work(judge)
        self.assertEqual(self.state["status"], "done")

    def test_cancel_races_final_save(self):
        original = self.st.compare_report
        injected = []
        def concurrent_cancel(kind, expected, payload, **kwargs):
            if payload.get('status') == 'done' and not injected:
                injected.append(True)
                current = copy.deepcopy(expected)
                current['status'] = 'cancel_requested'
                self.assertTrue(original(kind, expected, current, **kwargs))
                return False
            return original(kind, expected, payload, **kwargs)
        with patch.object(self.st, 'compare_report', side_effect=concurrent_cancel):
            self.work(FakeJudge())
        self.assertEqual(self.st.get_report(self.key, team='team-a')['status'], 'cancelled')
        self.assertTrue(injected)

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

    def test_common_run_opaque_selector_keeps_frozen_source_and_owner(self):
        from prism import evalservice
        selector = R.selector({'kind': 'evaluation', 'id': 'opaque-run-id'})
        run = {'status': 'completed', 'items': copy.deepcopy(self.state['items'])}
        with patch.object(evalservice, 'get_run', return_value={'ok': True, 'run': run}) as read:
            history = R._history(selector, 'team-a')
        read.assert_called_once_with('opaque-run-id', 'team-a')
        self.assertEqual(history['status'], 'done')
        self.assertEqual(history['items'][0]['source']['body'], '동결 원문')
        with patch.object(evalservice, 'get_run', return_value={'ok': False, 'error': 'not found'}):
            self.assertFalse(R.get(selector, 'team-b')['ok'])

    def test_embedding_attempt_counts_toward_limit_without_zero_cost(self):
        self.state['max_calls'] = 1
        judge = FakeJudge()
        self.work(judge)
        self.assertEqual(self.state['status'], 'limit_reached')
        self.assertEqual(len(self.state['attempts']), 1)
        self.assertEqual(self.state['attempts'][0]['stage'], 'embedding')
        self.assertIsNone(self.state['attempts'][0]['cost_usd'])
        self.assertFalse(self.state['cost_complete'])
        self.assertEqual(judge.calls, [])

    def test_cancel_request_persists_until_worker_stops(self):
        import time
        state = {**self.state, 'owner': 'worker', 'lease': time.time()+120}
        self.st.save_report(self.key, state, team='team-a')
        with patch.object(R, '_history', return_value={'ok': True}):
            requested = R.cancel(self.sel, 'team-a')
        self.assertEqual(requested['status'], 'cancel_requested')
        self.state = state
        judge = FakeJudge()
        self.work(judge)
        self.assertEqual(self.st.get_report(self.key, team='team-a')['status'], 'cancelled')
        self.assertEqual(judge.calls, [])

    def test_unknown_failed_judge_cost_is_not_treated_as_free(self):
        judge = FakeJudge()
        with patch.object(judge, 'complete_json', side_effect=TimeoutError('provider disconnected')):
            self.work(judge)
        self.assertFalse(self.state['cost_complete'])
        self.assertEqual(self.state['status'], 'partial')
        self.assertTrue(any(a.get('status') == 'failed' for a in self.state['attempts']))

    def test_long_call_renews_lease_and_honors_cancel(self):
        entered, release = threading.Event(), threading.Event()
        judge = FakeJudge()
        original = judge.complete_json
        def blocked(*args, **kwargs):
            entered.set()
            release.wait(2)
            return original(*args, **kwargs)
        judge.complete_json = blocked
        state = {**self.state, 'owner': 'worker', 'lease': time.time() + .15}
        self.st.save_report(self.key, state, team='team-a')
        with patch.object(R, 'LEASE_SECONDS', .15), patch.object(E, 'semantic_embed', side_effect=lambda texts: [[1., 0.] for _ in texts]):
            thread = threading.Thread(target=R._worker, args=(self.identity, self.key, 'team-a', state, judge))
            thread.start()
            try:
                self.assertTrue(entered.wait(1))
                time.sleep(.3)
                self.assertGreater(self.st.get_report(self.key, team='team-a')['lease'], time.time())
                with patch.object(R, '_history', return_value={'ok': True}):
                    self.assertEqual(R.cancel(self.sel, 'team-a')['status'], 'cancel_requested')
            finally:
                release.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.st.get_report(self.key, team='team-a')['status'], 'cancelled')


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

    def test_environment_freezes_model_limits_settings_and_resume(self):
        from prism import execution as EX
        from prism.config import Config
        judge = FakeJudge(); judge.mock = False; judge.model = 'judge'; judge.cfg = Config()
        judge.cfg.retry.max_retries = 0; judge.cfg.retry.max_format_retries = 0
        settings = json.loads(json.dumps(EX.client_settings(judge)))
        env = {'diagnostic_version': R.VERSION, 'judge_model': 'judge', 'judge_settings': settings,
               'fact_system': 'saved fact', 'coverage_system': 'saved coverage', 'max_output_tokens': 4096,
               'limits': {'max_calls': 7}}
        history = {'ok': True, 'status': 'done', 'detail_mode': 'all', 'items': self.state['items'],
                   'snapshot': {'environment': env, 'limits': {'max_calls': 6}}}
        server = NS(get_store=lambda: self.st, Handler=NS(server_mock=False),
                    llm_for_model=lambda model, mock: (judge, 'test'))
        class Deferred:
            def __init__(self, target, args, daemon): self.args = args
            def start(self): pass
            def is_alive(self): return True
        with patch.object(R, '_SV', server), patch.object(R, '_history', return_value=history), patch.object(R.threading, 'Thread', Deferred):
            self.assertFalse(R.start({**self.sel, 'model': 'other'}, 'team-a')['ok'])
            result = R.start(self.sel, 'team-a')
            self.assertTrue(result['ok'], result)
            state = R._ACTIVE.pop(self.identity).args[3]
            self.assertEqual(json.loads(json.dumps(state['judge_settings'])), settings)
            self.assertEqual((state['fact_system'], state['coverage_system'], state['max_calls']), ('saved fact', 'saved coverage', 6))
            state.update(status='partial', lease=0)
            self.st.save_report(self.key, state, team='team-a')
            self.assertFalse(R.start({**self.sel, 'max_calls': 8}, 'team-a')['ok'])
            judge.cfg.temperature = .9
            resumed = R.start(self.sel, 'team-a')
            self.assertTrue(resumed['ok'], resumed)
            resumed_worker = R._ACTIVE.pop(self.identity)
            self.assertEqual(json.loads(json.dumps(EX.client_settings(resumed_worker.args[4]))), settings)
            state = resumed_worker.args[3]; state.update(status='partial', lease=0)
            self.st.save_report(self.key, state, team='team-a')
            judge.cfg.chat_url = 'https://changed.invalid'
            self.assertIn('주소', R.start(self.sel, 'team-a')['error'])

    def test_expired_cancel_is_settled_before_explicit_resume(self):
        state = {**self.state, 'status': 'cancel_requested', 'lease': time.time() - 1,
                 'owner': 'lost', 'attempts': [{'stage': 'fact', 'status': 'running'}]}
        self.st.save_report(self.key, state, team='team-a')
        history = {'ok': True, 'status': 'done', 'detail_mode': 'all', 'items': self.state['items']}
        with patch.object(R, '_history', return_value=history):
            result = R.start(self.sel, 'team-a')
        self.assertFalse(result['ok'])
        saved = self.st.get_report(self.key, team='team-a')
        self.assertEqual(saved['status'], 'cancelled')
        self.assertEqual(saved['attempts'][0]['status'], 'uncertain')
        self.assertFalse(saved['cost_complete'])
        self.assertFalse(saved['owner'])

    def test_environment_saved_by_evaluation_service_is_accepted(self):
        from prism import evalservice
        from prism.config import Config
        judge = FakeJudge(); judge.mock = False; judge.model = 'judge'; judge.cfg = Config()
        server = NS(get_store=lambda: self.st, _supa=lambda: False, Handler=NS(server_mock=False),
                    llm_for_model=lambda model, mock: (judge, 'test'))
        class Deferred:
            def __init__(self, target, args, daemon): self.args = args
            def start(self): pass
            def is_alive(self): return True
        with patch.object(evalservice, '_SV', server), patch.object(R, '_SV', server), \
                patch.object(R.threading, 'Thread', Deferred):
            result = evalservice.save_environment('team-a', '고정 환경', 'judge', max_calls=9)
            self.assertTrue(result['ok'], result)
            history = {'ok': True, 'status': 'done', 'detail_mode': 'all', 'items': self.state['items'],
                       'snapshot': {'environment': result['environment']}}
            with patch.object(R, '_history', return_value=history):
                started = R.start(self.sel, 'team-a')
            self.assertTrue(started['ok'], started)
            frozen = R._ACTIVE.pop(self.identity).args[3]
            self.assertEqual(frozen['max_calls'], 9)
            self.assertEqual(frozen['judge_model'], 'judge')
            self.assertEqual(judge.calls, [])

    def test_legacy_partial_resume_restores_saved_settings_and_systems(self):
        from prism import execution as EX
        from prism.config import Config
        judge = FakeJudge(); judge.mock = False; judge.model = 'judge'; judge.cfg = Config()
        history = {'ok': True, 'status': 'done', 'detail_mode': 'all', 'items': self.state['items']}
        server = NS(get_store=lambda: self.st, Handler=NS(server_mock=False),
                    llm_for_model=lambda model, mock: (judge, 'test'))
        class Deferred:
            def __init__(self, target, args, daemon): self.args = args
            def start(self): pass
            def is_alive(self): return True
        with patch.object(R, '_SV', server), patch.object(R, '_history', return_value=history), patch.object(R.threading, 'Thread', Deferred):
            self.assertTrue(R.start({**self.sel, 'model': 'judge', 'max_calls': 8}, 'team-a')['ok'])
            old = R._ACTIVE.pop(self.identity).args[3]
            old.update(status='partial', lease=0, fact_system='old fact', coverage_system='old coverage')
            old['basis'] = E.digest({'items': old['items'], 'judge': old['judge_settings'], 'version': old['version'],
                                     'fact_system': old['fact_system'], 'coverage_system': old['coverage_system']})
            self.st.save_report(self.key, old, team='team-a')
            judge.cfg.temperature = .9
            resumed = R.start(self.sel, 'team-a')
            self.assertTrue(resumed['ok'], resumed)
            worker = R._ACTIVE.pop(self.identity)
            self.assertEqual(E.digest(EX.client_settings(worker.args[4])), E.digest(old['judge_settings']))
            self.assertEqual(worker.args[3]['fact_system'], 'old fact')
            self.assertEqual(worker.args[3]['coverage_system'], 'old coverage')
            self.assertEqual(worker.args[3]['max_calls'], 8)

    def test_mutation_endpoints_require_admin(self):
        from prism import serve
        for route in ('/eval-grounding-start', '/eval-grounding-cancel'):
            self.assertEqual(serve._POST_ROUTES[route][1], 'admin')
