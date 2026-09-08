"""평가 리포트 쪼개 보기: 서비스별 등급 일치율(by_service) · 최근 회차 추이 라우트.

실행: python3 -m unittest tests.test_eval_breakdown  (stdlib unittest · 의존성 0)
배경: 유형(by_reason_bucket)별로만 쪼개져 있어 '어느 서비스가 약한지'와 '회차별 추이'를
화면에서 볼 수 없던 갭. 채점은 abtest.score(일괄)·evalops._tally(증분) 단일 소스라 두 경로가
같은 키(displayServiceName)로 세는지까지 함께 잠근다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_evalops import _mk_store  # noqa: E402


def _row(service, grade):
    return {"content": {"displayServiceName": service, "title": "t", "body": "b"},
            "expected": {"finalGrade": grade, "reasons": []}}


def _out(grade):
    return {"quality_meta": {"finalGrade": grade, "reasons": [], "review": ""},
            "trace": {"cost_usd": 0.0, "tokens": {"in": 0, "out": 0}}}


class TestByService(unittest.TestCase):
    """서비스 2개가 섞인 골든셋에서 서비스별 표본 수·일치율·신뢰구간이 따로 잡히는지."""

    def test_two_services_split(self):
        from prism.abtest import score
        rows = [_row("뉴스", "G"), _row("뉴스", "G"), _row("뉴스", "R"),   # 뉴스 3건 중 2건 적중
                _row("포토", "G"), _row("포토", "G")]                      # 포토 2건 전건 적중
        outs = [_out("G"), _out("G"), _out("G"), _out("G"), _out("G")]
        bs = score(rows, outs)["by_service"]
        self.assertEqual(sorted(bs), ["뉴스", "포토"])
        self.assertEqual(bs["뉴스"]["n"], 3)
        self.assertEqual(bs["뉴스"]["grade_acc"], round(2 / 3, 3))
        self.assertEqual(bs["포토"], {"n": 2, "grade_acc": 1.0, "ci_lo": 1.0, "ci_hi": 1.0})
        self.assertLessEqual(bs["뉴스"]["ci_lo"], bs["뉴스"]["grade_acc"])
        self.assertGreaterEqual(bs["뉴스"]["ci_hi"], bs["뉴스"]["grade_acc"])

    def test_missing_service_falls_into_one_bucket(self):
        from prism.abtest import score
        rows = [{"expected": {"finalGrade": "G", "reasons": []}},
                {"content": {"displayServiceName": "  "}, "expected": {"finalGrade": "G", "reasons": []}}]
        bs = score(rows, [_out("G"), _out("G")])["by_service"]
        self.assertEqual(bs, {"(미지정)": {"n": 2, "grade_acc": 1.0, "ci_lo": 1.0, "ci_hi": 1.0}})

    def test_incremental_tally_matches_batch_score(self):
        """증분 경로(evalops._tally)도 같은 키로 센다 — 두 경로가 어긋나면 화면 값이 갈린다."""
        from prism import abtest, evalops
        rows = [_row("뉴스", "G"), _row("포토", "R")]
        outs = [_out("G"), _out("G")]
        m = evalops._zero_metrics()
        for row, out in zip(rows, outs):
            evalops._tally(m, row, out)
        self.assertEqual(abtest.service_report(m["per_service"]),
                         abtest.score(rows, outs)["by_service"])


class _FakeHandler:
    def _req_team(self):
        return None


class TestLearnReportTrend(unittest.TestCase):
    """최근 회차 라우트: 버전 내림차순 · 상한 · 빈 버전 건너뛰기."""

    def setUp(self):
        from prism import serve
        self.st = _mk_store()
        orig = serve.get_store
        serve.get_store = lambda: self.st
        self.addCleanup(lambda: setattr(serve, "get_store", orig))

    def _seed(self, versions, batches):
        for d in range(batches):                 # batch_seq = learn_batch 이벤트 수 · 버전 = +1
            self.st.log_event_once(None, "learn_batch", d + 1, 0)
        for v in versions:
            self.st.save_report(f"learn_report_v{v}",
                                {"ok": True, "ts": 1700000000 + v, "grade_accuracy": 0.5 + v / 100,
                                 "eval": {"ok": True, "n": 30, "grade_accuracy": 0.5 + v / 100}})

    def test_route_returns_last_reports_newest_first(self):
        from prism import serve
        self._seed(range(1, 8), batches=7)       # 버전 1~8 중 1~7 영속(top = 7+1 = 8 은 비어 있음)
        fn, admin = serve._GET_ROUTES["/learn-reports"]
        self.assertFalse(admin)
        items = fn(_FakeHandler(), {})["items"]
        self.assertEqual([it["version"] for it in items], [7, 6, 5, 4, 3])
        self.assertEqual(items[0]["n"], 30)
        self.assertAlmostEqual(items[0]["grade_accuracy"], 0.57)
        self.assertIsNotNone(items[0]["overall"])

    def test_limit_and_empty(self):
        from prism import serve
        fn, _ = serve._GET_ROUTES["/learn-reports"]
        self.assertEqual(fn(_FakeHandler(), {})["items"], [])   # 회차 없음 = 빈 목록(오류 아님)
        self._seed([1, 2, 3], batches=3)
        self.assertEqual([it["version"] for it in fn(_FakeHandler(), {"limit": ["2"]})["items"]], [3, 2])


if __name__ == "__main__":
    unittest.main()
