"""모델별 비교(learnops.compare_models_on_golden): 모델 간 병렬 · 건별 비교표 · 영속 · 조회 라우트.

실행: python3 -m unittest tests.test_model_compare  (stdlib unittest · 의존성 0)
"""
import os
import sys
import time
import unittest
import threading
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_evalops import _mk_store, _seed_golden  # noqa: E402


class TestCompareModels(unittest.TestCase):
    def setUp(self):
        from prism import serve
        self.st = _mk_store()
        self._orig_store = serve.get_store
        self._orig_mock = serve.Handler.server_mock
        serve.get_store = lambda: self.st
        serve.Handler.server_mock = True                 # 외부 호출 0(결정론 mock)
        self.addCleanup(lambda: (setattr(serve, "get_store", self._orig_store),
                                 setattr(serve.Handler, "server_mock", self._orig_mock)))

    def test_multi_model_items_and_persist(self):
        from prism import learnops as LO
        _seed_golden(self.st, 4)
        r = LO.compare_models_on_golden(["solar-pro2", "gpt-5.4", "solar-pro2"])   # 중복은 1개로
        self.assertTrue(r["ok"], r)
        self.assertEqual(sorted(m["model"] for m in r["models"]), ["gpt-5.4", "solar-pro2"])
        self.assertEqual(r["golden_n"], 4)
        self.assertEqual(len(r["items"]), 4)
        it = r["items"][0]
        self.assertIn(it["expected"]["grade"], ("G", "R"))
        self.assertEqual(set(it["got"]), {"gpt-5.4", "solar-pro2"})
        for g in it["got"].values():
            self.assertIn("grade", g); self.assertIn("ok", g); self.assertIn("empty", g)
        self.assertIsInstance(it["split"], bool); self.assertIsInstance(it["all_ok"], bool)
        self.assertEqual(r["miss_n"], sum(1 for x in r["items"] if not x["all_ok"]))
        self.assertIn("ts", r)
        # 영속: 마지막 비교를 그대로 되돌려 준다
        last = LO.last_model_compare()
        self.assertTrue(last["ok"])
        self.assertEqual(last["best"], r["best"])
        self.assertEqual(len(last["items"]), 4)

    def test_no_saved_compare(self):
        from prism import learnops as LO
        self.assertFalse(LO.last_model_compare()["ok"])

    def test_too_many_models(self):
        from prism import learnops as LO
        _seed_golden(self.st, 2)
        r = LO.compare_models_on_golden([f"m{i}" for i in range(7)])
        self.assertFalse(r["ok"])
        self.assertIn("6개", r["error"])

    def test_route_registered(self):
        from prism import serve
        self.assertIn("/model-compare-last", serve._GET_ROUTES)


class TestCompareBackground(unittest.TestCase):
    def setUp(self):
        from prism import serve
        self.st = _mk_store()
        self._orig_store = serve.get_store
        self._orig_mock = serve.Handler.server_mock
        serve.get_store = lambda: self.st
        serve.Handler.server_mock = True
        self.addCleanup(lambda: (setattr(serve, "get_store", self._orig_store),
                                 setattr(serve.Handler, "server_mock", self._orig_mock)))

    def test_start_status_jobs(self):
        import time
        from prism import learnops as LO, serve
        _seed_golden(self.st, 5)
        r = LO.compare_start(["solar-pro2", "gpt-5.4"])
        self.assertTrue(r["ok"], r); jid = r["id"]
        for _ in range(100):                             # mock 이라 금방 끝난다
            s = LO.compare_status(jid)
            if s["job"]["status"] == "done":
                break
            time.sleep(0.05)
        self.assertEqual(s["job"]["status"], "done")
        pm = s["job"]["models"]["solar-pro2"]
        self.assertEqual((pm["done"], pm["total"], pm["status"]), (5, 5, "done"))
        self.assertEqual(len(s["job"]["result"]["items"]), 5)
        self.assertIn("overall", s["job"]["result"]["models"][0])
        jobs = LO.compare_jobs()["jobs"]
        self.assertEqual(jobs[0]["id"], jid); self.assertNotIn("result", jobs[0])
        with LO._COMPARE_LOCK:
            LO._COMPARE_STORE[0] = None          # 완료 결과도 다음 부팅 뒤 reports에서 다시 읽는다
        self.assertEqual(len(LO.compare_status(jid)["job"]["result"]["items"]), 5)
        self.assertFalse(LO.compare_status(jid, team="other")["ok"])         # 팀 스코프
        self.assertTrue(LO.last_model_compare()["ok"])                       # 완료분 영속
        for p in ("/compare-status", "/compare-jobs"):
            self.assertIn(p, serve._GET_ROUTES)
        for p in ("/compare-start", "/compare-cancel", "/compare-restart"):
            self.assertIn(p, serve._POST_ROUTES)
        self.assertTrue(serve.is_public_get("/vendor/compare-window.html"))

    def test_boot_marks_unfinished_job_interrupted_and_scopes_team(self):
        """부팅 뒤 reports를 읽되, 잃어버린 스레드를 자동 재개하지 않는다."""
        from prism import learnops as LO
        saved = {"id": 7, "ts": time.time() - 10, "team": "team-a", "scope": "all",
                 "status": "running", "golden_n": 16, "skipped": [], "error": "", "finished": None,
                 "end_reason": "", "restart_of": None, "result": None,
                 "models": {"solar-pro2": {"done": 8, "total": 16, "status": "running", "route": "mock"}}}
        self.st.save_report(LO._COMPARE_REPORT, {"seq": 7, "jobs": [saved]}, "team-a")
        with LO._COMPARE_LOCK:
            LO._COMPARE_STORE[0] = None          # 프로세스 재기동(메모리 캐시 유실) 모사
        jobs = LO.compare_jobs("team-a")["jobs"]
        self.assertEqual((jobs[0]["status"], jobs[0]["end_reason"]), ("interrupted", "서버 재시작으로 중단됨"))
        self.assertEqual(jobs[0]["models"]["solar-pro2"]["status"], "interrupted")
        self.assertFalse(LO.compare_status(7, "team-b")["ok"])
        self.assertFalse(LO.compare_cancel(7, "team-b")["ok"])
        persisted = self.st.get_report(LO._COMPARE_REPORT, "team-a")["jobs"][0]
        self.assertEqual(persisted["status"], "interrupted")

    def test_cancel_stops_only_after_one_eight_item_chunk(self):
        """취소는 진행 중인 LLM 묶음을 끊지 않고 다음 8건 경계에서 멈춘다."""
        from types import SimpleNamespace
        from prism import abtest
        from prism import learnops as LO
        calls = []
        old_run, old_score = abtest.run_methodology, abtest.score
        abtest.run_methodology = lambda rows, *args, **kw: calls.append(len(rows)) or [None] * len(rows)
        abtest.score = lambda *args, **kw: {}
        self.addCleanup(lambda: (setattr(abtest, "run_methodology", old_run),
                                 setattr(abtest, "score", old_score)))
        rows = [{"content": {"title": str(i)}, "expected": {"finalGrade": "G"}} for i in range(16)]
        cfg = SimpleNamespace(thresholds=SimpleNamespace(eval_gate=.85, meta_gate=.6))
        llm = SimpleNamespace(mock=True)
        with self.assertRaises(LO._CompareInterrupted):
            LO._compare_run_model(("mock", llm, "mock"), rows, cfg, cancelled=lambda: bool(calls))
        self.assertEqual(calls, [8])

    def test_cancel_is_team_scoped_and_persisted(self):
        from prism import learnops as LO
        LO._compare_restore("team-a")
        job = {"id": 11, "ts": time.time(), "team": "team-a", "scope": "all", "status": "running",
               "golden_n": 1, "skipped": [], "error": "", "finished": None, "end_reason": "", "result": None,
               "models": {"solar-pro2": {"done": 0, "total": 1, "status": "running", "route": "mock"}}}
        with LO._COMPARE_LOCK:
            LO._COMPARE_JOBS[LO._compare_key("team-a", 11)] = job
            LO._COMPARE_SEQ[0] = max(LO._COMPARE_SEQ[0], 11)
        self.assertFalse(LO.compare_cancel(11, "team-b")["ok"])
        self.assertEqual(LO.compare_cancel(11, "team-a")["status"], "cancel_requested")
        saved = self.st.get_report(LO._COMPARE_REPORT, "team-a")["jobs"][0]
        self.assertEqual((saved["status"], saved["end_reason"]), ("cancel_requested", "사용자 취소 요청"))


class TestComparePersistence(unittest.TestCase):
    """이벤트로 복원 경합을 고정하고 백그라운드 진입점을 직접 실행한다."""

    setUp = TestCompareBackground.setUp

    def saved(self, jid=7, status="done"):
        return {"id": jid, "ts": jid, "status": status, "models": {}, "scope": "all"}

    def start_deferred(self, models=None):
        from prism import learnops as LO
        with patch.object(LO.threading, "Thread") as thread:
            result = LO.compare_start(models or ["solar-pro2"])
        self.assertTrue(result["ok"], result)
        return result["id"], thread.call_args.kwargs["target"]

    def test_failed_restore_never_writes_and_retries(self):
        from prism import learnops as LO
        _seed_golden(self.st, 2)
        self.st.save_report(LO._COMPARE_REPORT, {"seq": 30, "jobs": [self.saved()]})
        with patch.object(self.st, "get_report", side_effect=OSError("offline")), \
                patch.object(self.st, "save_report") as save:
            for _ in range(2):
                result = LO.compare_start(["solar-pro2"])
                self.assertFalse(result["ok"])
                self.assertIn("복원 실패", result["error"])
                self.assertNotIn("", LO._COMPARE_LOADED)
                self.assertFalse(LO._compare_persist(None, self.st))
            save.assert_not_called()
        jid, _ = self.start_deferred()
        self.assertEqual(jid, 31)
        self.assertEqual({j["id"] for j in self.st.get_report(LO._COMPARE_REPORT)["jobs"]}, {7, 31})
        self.assertIs(LO._COMPARE_STORE[0], self.st)

    def test_first_restore_serializes_overlapping_start(self):
        from prism import learnops as LO
        _seed_golden(self.st, 2)
        self.st.save_report(LO._COMPARE_REPORT, {"seq": 7, "jobs": [self.saved(status="running")]})
        entered, release, blocked = threading.Event(), threading.Event(), threading.Event()
        lock = threading.RLock()
        class ObservedLock:
            def __enter__(self):
                if not lock.acquire(blocking=False):
                    if threading.current_thread().name == "overlapping-start":
                        blocked.set()
                    lock.acquire()
                return self
            def __exit__(self, *args):
                lock.release()
        original_get = self.st.get_report
        results = {}
        def read(*a, **kw):
            entered.set()
            self.assertTrue(release.wait(3))
            return original_get(*a, **kw)
        def start():
            results["start"] = LO.compare_start(["solar-pro2"])
        loader = threading.Thread(target=lambda: results.update(restored=LO.compare_jobs()))
        starter = threading.Thread(target=start, name="overlapping-start")
        with patch.object(self.st, "get_report", side_effect=read) as get, \
                patch.object(LO, "_COMPARE_LOCK", ObservedLock()), \
                patch.object(LO.threading, "Thread"):
            loader.start()
            try:
                self.assertTrue(entered.wait(3))
                starter.start()
                self.assertTrue(blocked.wait(3))
            finally:
                release.set()
                loader.join(3)
                if starter.ident is not None:
                    starter.join(3)
        self.assertFalse(loader.is_alive())
        self.assertFalse(starter.is_alive())
        self.assertTrue(results["start"]["ok"], results)
        jid = results["start"]["id"]
        self.assertEqual(jid, 8)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(LO.compare_status(7)["job"]["status"], "interrupted")
        self.assertEqual(LO.compare_status(jid)["job"]["status"], "running")

    def test_active_jobs_survive_terminal_history_limit_and_remain_cancellable(self):
        from prism import learnops as LO
        _seed_golden(self.st, 1)
        ids = [self.start_deferred()[0] for _ in range(LO._COMPARE_KEEP + 2)]
        with LO._COMPARE_LOCK:
            for jid in range(100, 100 + LO._COMPARE_KEEP + 3):
                LO._COMPARE_JOBS[("", jid)] = self.saved(jid)
            self.assertTrue(LO._compare_persist(None, self.st))
        saved = self.st.get_report(LO._COMPARE_REPORT)["jobs"]
        self.assertEqual(len(saved), len(ids) + LO._COMPARE_KEEP)
        self.assertEqual({j["id"] for j in saved if j["status"] == "running"}, set(ids))
        self.assertTrue(LO.compare_cancel(ids[0])["ok"])
        self.assertEqual(LO.compare_status(ids[0])["job"]["status"], "cancel_requested")
        self.assertEqual(len(LO.compare_jobs()["jobs"]), len(saved))

    def test_progress_save_failure_stops_paid_chunks_and_queued_models(self):
        from prism import learnops as LO, abtest
        _seed_golden(self.st, 16)
        original_save = self.st.save_report
        charged = []
        def save(kind, report, *a, **kw):
            if kind == LO._COMPARE_REPORT and any(
                    m.get("done", 0) for j in report["jobs"] for m in j["models"].values()):
                raise OSError("disk full")
            return original_save(kind, report, *a, **kw)
        # 한 worker로 대기 모델을 확정: 첫 진척 저장 실패 뒤 나머지 모델의 호출도 없어야 한다.
        from concurrent.futures import ThreadPoolExecutor
        with patch.object(self.st, "save_report", side_effect=save), \
                patch.object(abtest, "run_methodology", side_effect=lambda rows, *a, **kw:
                             charged.append(len(rows)) or [None] * len(rows)), \
                patch("concurrent.futures.ThreadPoolExecutor", side_effect=lambda **kw: ThreadPoolExecutor(max_workers=1)):
            jid, run = self.start_deferred(["a", "b", "c", "d", "e", "f"])
            run()
        job = LO.compare_status(jid)["job"]
        self.assertEqual(charged, [8])
        self.assertEqual(job["status"], "failed")
        self.assertIn("저장 실패", job["error"])
        self.assertIsNone(job.get("result"))

    def test_final_history_save_failure_is_not_done(self):
        from prism import learnops as LO
        _seed_golden(self.st, 1)
        jid, run = self.start_deferred()
        original_save = self.st.save_report
        def save(kind, report, *a, **kw):
            if kind == LO._COMPARE_REPORT and any(j["status"] == "done" for j in report["jobs"]):
                raise OSError("disk full")
            return original_save(kind, report, *a, **kw)
        with patch.object(self.st, "save_report", side_effect=save):
            run()
        job = LO.compare_status(jid)["job"]
        self.assertEqual(job["status"], "failed")
        self.assertFalse(job["result"]["ok"])
        self.assertIn("저장 실패", job["error"])

    def test_final_result_save_failure_surfaces_in_sync_and_background(self):
        from prism import learnops as LO
        _seed_golden(self.st, 1)
        jid, run = self.start_deferred()
        original_save = self.st.save_report
        def save(kind, report, *a, **kw):
            if kind == "model_compare":
                raise OSError("disk full")
            return original_save(kind, report, *a, **kw)
        with patch.object(self.st, "save_report", side_effect=save):
            result = LO.compare_models_on_golden(["solar-pro2"])
            self.assertFalse(result["ok"])
            run()
        job = LO.compare_status(jid)["job"]
        self.assertEqual(job["status"], "failed")
        self.assertIn("결과 저장 실패", job["error"])
