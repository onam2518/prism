"""평가 런(evalops · Atelier eval_runs 이식): 스토어 계약 · 백그라운드 실행 · 재개.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mk_store():
    import tempfile
    from prism.store import Store
    return Store(os.path.join(tempfile.mkdtemp(), "t.db"))


def _seed_golden(st, n=5):
    """골든 n건 시드 · content_hash 목록 반환."""
    from prism.store import content_hash
    hashes = []
    for i in range(n):
        content = {"displayServiceName": "뉴스", "title": f"평가 콘텐츠 {i}",
                   "subtitle": "", "body": f"본문 {i} · 평가 런 테스트용 텍스트입니다."}
        expected = {"finalGrade": "G" if i % 2 == 0 else "R",
                    "reasons": [] if i % 2 == 0 else ["adult"]}
        st.upsert_golden(content_hash(content), content, expected)
        hashes.append(content_hash(content))
    return hashes


class TestEvalRunStore(unittest.TestCase):
    """SQLite 스토어 계약: 런 생성·갱신·조회 · 건별 결과 upsert·재개 판별."""

    def test_run_roundtrip(self):
        st = _mk_store()
        rid = st.eval_run_create("", "solar-pro", "all", 7, created_by="uid-1")
        self.assertGreater(rid, 0)
        run = st.eval_run_get(rid)
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["total"], 7)
        self.assertEqual(run["model"], "solar-pro")
        st.eval_run_update(rid, cursor=3, metrics={"n": 3, "grade_hit": 2})
        st.eval_run_update(rid, status="done", finished=time.time())
        run = st.eval_run_get(rid)
        self.assertEqual(run["cursor"], 3)
        self.assertEqual(run["status"], "done")
        self.assertEqual(run["metrics"]["grade_hit"], 2)
        self.assertTrue(run["finished"])
        items = st.eval_runs_list()
        self.assertEqual(items[0]["id"], rid)

    def test_results_upsert_and_hashes(self):
        st = _mk_store()
        rid = st.eval_run_create("", "", "all", 2)
        rows = [{"hash": "h1", "title": "t1", "expected": {"finalGrade": "G", "reasons": []},
                 "got": {"finalGrade": "R", "reasons": ["adult"]}, "passed": False, "error": ""},
                {"hash": "h2", "title": "t2", "expected": {"finalGrade": "R", "reasons": ["adult"]},
                 "got": {"finalGrade": "R", "reasons": ["adult"]}, "passed": True, "error": ""}]
        st.eval_results_add(rid, rows)
        st.eval_results_add(rid, rows)                   # 재실행 upsert 안전
        self.assertEqual(st.eval_result_hashes(rid), {"h1", "h2"})
        fails = st.eval_results_list(rid, only_fail=True)
        self.assertEqual(len(fails), 1)
        self.assertEqual(fails[0]["hash"], "h1")
        self.assertEqual(fails[0]["got"]["finalGrade"], "R")


class TestEvalRunFlow(unittest.TestCase):
    """serve 컴포지션 + mock LLM 으로 런 전체 흐름(시작→완주→리포트→재개)."""

    def _with_serve(self):
        from prism import serve
        from prism import evalops
        st = _mk_store()
        serve._STORE = st
        self._orig_mock = serve.Handler.server_mock
        serve.Handler.server_mock = True                 # 외부 호출 0(결정론 mock)
        self.addCleanup(lambda: (setattr(serve, "_STORE", None),
                                 setattr(serve.Handler, "server_mock", self._orig_mock)))
        return serve, evalops, st

    def _wait_done(self, st, rid, timeout=60):
        t0 = time.time()
        while time.time() - t0 < timeout:
            run = st.eval_run_get(rid)
            if run and run["status"] != "running":
                return run
            time.sleep(0.1)
        self.fail("평가 런이 제한 시간 안에 끝나지 않았습니다")

    def test_start_to_done_and_report(self):
        serve, evalops, st = self._with_serve()
        _seed_golden(st, 5)
        r = serve.eval_run_start(None, model="", scope="all")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["total"], 5)
        run = self._wait_done(st, r["id"])
        self.assertEqual(run["status"], "done")
        self.assertEqual(run["cursor"], 5)
        self.assertEqual(len(st.eval_results_list(r["id"])), 5)
        rep = serve.eval_run_report(r["id"])
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["evaluated"], 5)
        for k in ("grade_accuracy", "reason_jaccard", "harm_miss_rate",
                  "by_reason_bucket", "grade_ci", "basis", "detail", "status"):
            self.assertIn(k, rep)
        self.assertEqual(rep["basis"]["scope"], "all")
        lst = serve.eval_runs_list(None)
        self.assertTrue(lst["ok"])
        self.assertEqual(lst["items"][0]["id"], r["id"])
        self.assertFalse(lst["items"][0]["stalled"])

    def test_start_without_golden(self):
        serve, evalops, st = self._with_serve()
        r = serve.eval_run_start(None)
        self.assertFalse(r.get("ok"))
        self.assertIn("골든셋", r.get("error", ""))

    def test_stalled_resume_completes_remaining(self):
        serve, evalops, st = self._with_serve()
        hashes = _seed_golden(st, 4)
        # 서버 재시작으로 유실된 런 흉내: running 행 + 앞 2건만 결과 존재 · 스레드 없음
        rid = st.eval_run_create("", "", "all", 4)
        st.eval_results_add(rid, [{"hash": h, "title": "", "expected": {"finalGrade": "G", "reasons": []},
                                   "got": {"finalGrade": "G", "reasons": []}, "passed": True, "error": ""}
                                  for h in hashes[:2]])
        st.eval_run_update(rid, cursor=2, metrics={"n": 2, "grade_hit": 2, "reason_exact": 2,
                                                   "jaccard_sum": 2.0, "harm_miss": 0, "empty": 0,
                                                   "cost_usd": 0.0, "tok_in": 0, "tok_out": 0,
                                                   "lat": [], "yellow": 0, "auto_n": 2, "auto_hit": 2,
                                                   "per_reason": {}})
        lst = serve.eval_runs_list(None)
        self.assertTrue(lst["items"][0]["stalled"])       # 프로세스에 스레드 없음 → 재개 대상 표시
        rr = serve.eval_run_resume(rid, None)
        self.assertTrue(rr.get("ok"), rr)
        self.assertEqual(rr["remain"], 2)                 # 저장된 2건 제외 · 남은 건만
        run = self._wait_done(st, rid)
        self.assertEqual(run["status"], "done")
        self.assertEqual(len(st.eval_result_hashes(rid)), 4)
        self.assertEqual(run["metrics"]["n"], 4)          # 카운터가 기존 2건 위에 누적

    def test_cancel_without_thread_marks_cancelled(self):
        serve, evalops, st = self._with_serve()
        rid = st.eval_run_create("", "", "all", 3)
        r = serve.eval_run_cancel(rid, None)
        self.assertTrue(r.get("ok"))
        self.assertEqual(st.eval_run_get(rid)["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
