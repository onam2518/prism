"""보강 배치 진행 플래그의 고아 방지(2026-08-09).

사고: 배포가 "10분을 기다려도 배치가 끝나지 않아" 중단됐고, 그 뒤로도 공개 /config 의
ingesting 이 1시간 45분 넘게 True 로 남았다. 이 플래그는 _ENRICH_STATE(프로세스 메모리)를
읽는데, 지우는 유일한 방법이 프로세스 재시작이고 그 재시작이 곧 배포다. 즉 고아 플래그
하나로 배포가 영구히 막힌다.

구조적 원인 2가지를 여기서 잠근다.
  ① _enrich_run 에 try/finally 가 없어 루프 밖 예외 시 running=True 가 영구 잔존
  ② 진행 중 표시에 상한이 없어 고아 플래그를 판별할 방법이 없음
"""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("PRISM_DB", os.path.join(tempfile.mkdtemp(), "t.db"))
os.environ.setdefault("PRISM_BACKEND", "sqlite")

from prism import dictops as DO


class _Base(unittest.TestCase):
    def setUp(self):
        self._saved = dict(DO._ENRICH_STATE)

    def tearDown(self):
        DO._ENRICH_STATE.clear()
        DO._ENRICH_STATE.update(self._saved)


class TestOrphanFlagCannotBlockForever(_Base):
    def test_idle_is_not_running(self):
        DO._ENRICH_STATE.update(running=False, started=0, total=0)
        self.assertFalse(DO.enrich_running())
        self.assertFalse(DO.enrich_stale())

    def test_fresh_batch_is_running(self):
        DO._ENRICH_STATE.update(running=True, started=time.time(), total=10)
        self.assertTrue(DO.enrich_running())
        self.assertFalse(DO.enrich_stale())

    def test_flag_past_budget_is_stale_not_running(self):
        """상한을 넘기면 진행 중으로 보지 않는다 — 배포 가드가 풀려야 한다."""
        DO._ENRICH_STATE.update(running=True, total=10,
                                started=time.time() - (DO._ENRICH_MIN_BUDGET_S + 60))
        self.assertTrue(DO.enrich_stale())
        self.assertFalse(DO.enrich_running())

    def test_budget_scales_with_batch_size(self):
        """개체가 많으면 오래 걸리는 것이 정상 — 크기에 비례해 상한을 늘린다."""
        big = 5000
        DO._ENRICH_STATE.update(running=True, total=big,
                                started=time.time() - (DO._ENRICH_MIN_BUDGET_S + 60))
        self.assertFalse(DO.enrich_stale(), "큰 배치를 고아로 오판하면 정상 배치를 끊는다")
        DO._ENRICH_STATE["started"] = time.time() - (big * DO._ENRICH_ITEM_BUDGET_S + 60)
        self.assertTrue(DO.enrich_stale())

    def test_missing_started_defers_judgment(self):
        """시작 시각이 없는 구버전 상태는 고아로 단정하지 않는다."""
        DO._ENRICH_STATE.update(running=True, started=0, total=10)
        self.assertFalse(DO.enrich_stale())
        self.assertTrue(DO.enrich_running())

    def test_view_exposes_stale(self):
        DO._ENRICH_STATE.update(running=True, total=1,
                                started=time.time() - (DO._ENRICH_MIN_BUDGET_S + 60))
        v = DO.enrich_view()
        self.assertTrue(v["stale"])
        self.assertFalse(v["running"], "화면도 고아 상태를 진행 중으로 표시하면 안 된다")


class TestRunAlwaysClearsFlag(_Base):
    def test_finally_clears_on_exception_outside_loop(self):
        """루프 밖(브레이커 리셋)에서 터져도 running 은 내려가야 한다."""
        import prism.entdict as ED
        saved = ED._wd_breaker_reset
        ED._wd_breaker_reset = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            DO._ENRICH_STATE.update(running=True, started=time.time(), total=1)
            with self.assertRaises(RuntimeError):
                DO._enrich_run(None, ["e1"])
            self.assertFalse(DO._ENRICH_STATE["running"])
            self.assertTrue(DO._ENRICH_STATE["finished_at"])
        finally:
            ED._wd_breaker_reset = saved

    def test_source_has_try_finally(self):
        """리팩토링으로 finally 가 사라지면 같은 사고가 재발한다."""
        with open(os.path.join(ROOT, "prism/dictops.py"), encoding="utf-8") as fh:
            src = fh.read()
        body = src[src.index("def _enrich_run("):src.index("def _enrich_start(")]
        self.assertIn("finally:", body)
        self.assertIn('S["running"] = False', body.split("finally:")[1])


class TestDeployGuardReadsGuardedHelper(_Base):
    def test_config_ingesting_uses_enrich_running(self):
        """공개 /config 의 ingesting 이 원시 플래그를 읽으면 상한이 무의미해진다."""
        with open(os.path.join(ROOT, "prism/serve.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertEqual(src.count("enrich_running()"), 2)
        self.assertNotIn('_ENRICH_STATE.get("running")', src)

    def test_start_guard_uses_helper(self):
        """단일 실행 가드도 상한을 봐야 고아 플래그가 새 배치를 영구 차단하지 않는다."""
        with open(os.path.join(ROOT, "prism/dictops.py"), encoding="utf-8") as fh:
            src = fh.read()
        body = src[src.index("def _enrich_start("):src.index("def enrich_stale(")]
        self.assertIn("enrich_running()", body)


if __name__ == "__main__":
    unittest.main()
