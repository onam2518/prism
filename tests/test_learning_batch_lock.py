"""공유 프롬프트를 보호하는 모든 학습 배치 호출자의 중복 거절 · 재시도 계약 (실 LLM 없음)."""
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from prism import evalops, learnops, prompts, qa_seed, serve
from prism.store import Store


class TestLearningBatchLock(unittest.TestCase):
    @contextmanager
    def running_batch(self, team):
        entered, release = threading.Event(), threading.Event()

        def run(current_team, models, model):
            if current_team == team:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test did not release batch")
            return {"ok": True}

        with patch.object(learnops, "_learning_batch", side_effect=run) as work:
            with ThreadPoolExecutor(max_workers=1) as pool:
                handler = SimpleNamespace(_req_team=lambda: team)
                first = pool.submit(serve._p_learn_batch, handler, b"{}")
                try:
                    self.assertTrue(entered.wait(2))
                    yield work
                finally:
                    release.set()
                    self.assertEqual(first.result(timeout=2), {"ok": True})

    def test_two_manual_requests_run_one_batch_and_retry_after_success(self):
        handler = SimpleNamespace(_req_team=lambda: "team-A")
        with self.running_batch("team-A") as work:
            result = serve._p_learn_batch(handler, b'{"models": ["m1", "m2"]}')
            self.assertFalse(result["ok"])
            self.assertTrue(result["busy"])
            self.assertIn("이미 실행 중", result["error"])
            self.assertEqual(work.call_count, 1)
        with patch.object(learnops, "_learning_batch", return_value={"ok": True}) as work:
            self.assertTrue(serve._p_learn_batch(handler, b'{"models": ["m1", "m2"]}')["ok"])
            work.assert_called_once_with("team-A", ["m1", "m2"], "")

    def test_empty_and_none_team_share_lock(self):
        with self.running_batch(None) as work:
            self.assertTrue(learnops.learning_batch("")["busy"])
            self.assertEqual(work.call_count, 1)

    def test_other_team_is_busy_then_can_retry_with_model(self):
        with self.running_batch("team-A") as work:
            self.assertTrue(learnops.learning_batch("team-B", ["m"], "chosen")["busy"])
            self.assertEqual(work.call_count, 1)
        with patch.object(learnops, "_learning_batch", return_value={"ok": True}) as work:
            self.assertTrue(learnops.learning_batch("team-B", ["m"], "chosen")["ok"])
            work.assert_called_once_with("team-B", ["m"], "chosen")

    def test_exception_releases_lock(self):
        for error in (RuntimeError("batch failed"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                with patch.object(learnops, "_learning_batch", side_effect=[error, {"ok": True}]) as work:
                    with self.assertRaises(type(error)):
                        learnops.learning_batch("team-exception")
                    self.assertTrue(learnops.learning_batch("team-exception")["ok"])
                    self.assertEqual(work.call_count, 2)

    def test_schedule_busy_retains_oneoff_and_recurring_dates_then_retries(self):
        for repeat in (0, 7):
            with self.subTest(repeat=repeat):
                cfg = SimpleNamespace(learn_next_at="2020-01-01T04:30", learn_team="scheduled",
                                      learn_repeat_days=repeat, save_template=Mock())
                with patch.object(learnops.Config, "load", return_value=cfg):
                    with self.running_batch("scheduled") as work:
                        self.assertFalse(learnops._run_due_batch(cfg))
                        self.assertEqual(cfg.learn_next_at, "2020-01-01T04:30")
                        self.assertEqual(cfg.learn_team, "scheduled")
                        cfg.save_template.assert_not_called()
                        self.assertEqual(work.call_count, 1)
                    with patch.object(learnops, "_learning_batch", return_value={"ok": True}):
                        with patch.object(serve, "_report_save"):
                            self.assertTrue(learnops._run_due_batch(cfg))
                    cfg.save_template.assert_called_once_with()
                    if repeat:
                        self.assertGreater(learnops.next_batch_time(cfg.learn_next_at),
                                           learnops.time.time())
                    else:
                        self.assertEqual(cfg.learn_next_at, "")

    def test_budget_stop_releases_lock_without_automatic_schedule_retry(self):
        cfg = SimpleNamespace(learn_next_at="2020-01-01T04:30", learn_team="failed",
                              save_template=Mock())
        with patch.object(learnops.Config, "load", return_value=cfg):
            with patch.object(learnops, "_learning_batch", side_effect=[
                    {"ok": False, "budget_stop": True}, {"ok": True}]) as work:
                self.assertTrue(learnops._run_due_batch(cfg))
                self.assertEqual(cfg.learn_next_at, "")
                cfg.save_template.assert_called_once_with()
                self.assertTrue(learnops.learning_batch("failed")["ok"])
                self.assertEqual(work.call_count, 2)

    def test_autopilot_busy_is_failed_without_completed_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = Store(tmp + "/t.db")
            with patch.object(evalops, "_SV", SimpleNamespace(get_store=lambda: st)):
                rid = st.autopilot_create("pilot", 0.9, 3)
                with self.running_batch("pilot") as work:
                    evalops._pilot_loop(rid, "pilot", 0.9, 3)
                    self.assertEqual(work.call_count, 1)
                run = st.autopilot_latest("pilot")
                self.assertEqual(run["status"], "failed")
                self.assertIn("이미 실행 중", run["error"])
                self.assertEqual(run["round"], 0)
                self.assertEqual(run["history"], [])
                self.assertIsNone(run["best_accuracy"])
                self.assertNotIn(rid, evalops._PILOT_ACTIVE)

    def test_qa_seed_propagates_batch_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = Store(tmp + "/t.db")
            with patch.object(serve, "_STORE", st), patch.object(serve.Handler, "server_mock", True):
                with patch.object(serve, "sync_prompt"):
                    with self.running_batch(None) as work:
                        result = qa_seed.seed(verbose=False)
                        self.assertFalse(result["ok"])
                        self.assertTrue(result["busy"])
                        self.assertEqual(work.call_count, 1)

    def test_autopilot_rejection_preserves_completed_round_and_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = Store(tmp + "/t.db")
            with patch.object(evalops, "_SV", SimpleNamespace(get_store=lambda: st)):
                rid = st.autopilot_create("pilot-later", 0.9, 3)
                with patch.object(learnops, "_learning_batch", side_effect=[
                        {"ok": True, "grade_accuracy": 0.7}, {"ok": False, "busy": True}]) as work:
                    evalops._pilot_loop(rid, "pilot-later", 0.9, 3)
                    self.assertEqual(work.call_count, 2)
                run = st.autopilot_latest("pilot-later")
                self.assertEqual(run["status"], "failed")
                self.assertEqual(run["error"], "학습 배치 실행 실패")
                self.assertEqual(run["round"], 1)
                self.assertEqual(len(run["history"]), 1)
                self.assertEqual(run["best_accuracy"], 0.7)

    def test_cross_team_cannot_overlap_shared_prompt_rollback(self):
        evaluating, release = threading.Event(), threading.Event()
        eval_calls, compiled = {}, []
        original = {"analyze": "original"}
        original_models = {"model": {"analyze": "original-model"}}

        def compile_batch(team):
            compiled.append(team)
            prompts.LEARNED = {"analyze": team}
            prompts.LEARNED_BY_MODEL = {"model": {"analyze": team}}
            return {"ok": True}

        def evaluate(team, model=""):
            eval_calls[team] = eval_calls.get(team, 0) + 1
            if team == "team-A" and eval_calls[team] == 2:
                evaluating.set()
                if not release.wait(5):
                    raise TimeoutError("test did not release evaluation")
                accuracy = 0.5  # A must roll both global prompt maps back.
            else:
                accuracy = 0.9 if eval_calls[team] == 1 else 1.0
            return {"ok": True, "grade_accuracy": accuracy, "evaluated": 10}

        sv = SimpleNamespace(award_quest_bonus=Mock(), get_store=lambda: None,
                             _report_save=Mock(), _agg_bump=Mock(), broadcast=Mock())
        with ExitStack() as patches:
            for obj, name, value in (
                    (prompts, "LEARNED", original),
                    (prompts, "LEARNED_BY_MODEL", original_models),
                    (learnops, "_SV", sv),
                    (learnops, "_LAST_LEARN_REPORT", {}),
                    (learnops, "build_golden_from_reviews", Mock(return_value={"confirmed": 0})),
                    (learnops, "snapshot_prompts", Mock(return_value={})),
                    (learnops, "meta_compile_run", compile_batch),
                    (learnops, "eval_golden", evaluate)):
                patches.enter_context(patch.object(obj, name, value))
            with ThreadPoolExecutor(max_workers=1) as pool:
                first = pool.submit(learnops.learning_batch, "team-A")
                try:
                    self.assertTrue(evaluating.wait(2))
                    self.assertTrue(learnops.learning_batch("team-B")["busy"])
                    self.assertEqual(compiled, ["team-A"])
                    self.assertNotIn("team-B", eval_calls)
                    self.assertEqual(prompts.LEARNED, {"analyze": "team-A"})
                    self.assertEqual(prompts.LEARNED_BY_MODEL, {"model": {"analyze": "team-A"}})
                finally:
                    release.set()
                    report = first.result(timeout=2)
            self.assertTrue(report["improve"]["reverted"])
            self.assertEqual(prompts.LEARNED, original)
            self.assertEqual(prompts.LEARNED_BY_MODEL, original_models)
            self.assertTrue(learnops.learning_batch("team-B")["ok"])
            self.assertEqual(compiled, ["team-A", "team-B"])
            self.assertEqual(prompts.LEARNED, {"analyze": "team-B"})
            self.assertEqual(prompts.LEARNED_BY_MODEL, {"model": {"analyze": "team-B"}})
