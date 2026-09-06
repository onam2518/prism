"""모델별 비교(learnops.compare_models_on_golden): 모델 간 병렬 · 건별 비교표 · 영속 · 조회 라우트.

실행: python3 -m unittest tests.test_model_compare  (stdlib unittest · 의존성 0)
"""
import os
import sys
import time
import unittest

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
