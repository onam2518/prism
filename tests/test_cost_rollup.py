"""비용 롤업 원장: 실행 시점 누적(_log_cost_rollup) → 일별×모델×콜 조회(cost_rollup_data).

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
배경: supabase 는 콘텐츠에 트레이스(by_call·cost)를 저장하지 않아 실행 시점 누적이
유일한 영속 원천(reports kind='cost_rollup' · 2026-07-15 리뷰 P2-2).
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _trace(cost=0.01, model="gpt-x"):
    return {"cost_usd": cost, "model": model, "tokens": {"in": 100, "out": 50},
            "by_call": {"summary": {"n": 1, "cost": 0.004, "in": 60, "out": 30},
                        "category": {"n": 1, "cost": 0.006, "in": 40, "out": 20}}}


class TestCostRollup(unittest.TestCase):
    def _serve(self):
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve

    def test_accumulate_same_day_and_window(self):
        serve = self._serve()
        serve._log_cost_rollup(_trace(), team=None)
        serve._log_cost_rollup(_trace(), team=None)          # 같은 날 2건 병합
        d = serve.cost_rollup_data(None, days=7)
        self.assertTrue(d["ok"])
        self.assertEqual(len(d["by_day"]), 7)                # 빈 날 포함 연속
        self.assertEqual(d["total"]["n"], 2)
        self.assertAlmostEqual(d["total"]["cost"], 0.02)
        self.assertAlmostEqual(d["by_day"][-1]["cost"], 0.02)   # 마지막 = 오늘
        m = {x["model"]: x for x in d["by_model"]}
        self.assertEqual(m["gpt-x"]["n"], 2)
        c = {x["call"]: x for x in d["by_call"]}
        self.assertAlmostEqual(c["summary"]["cost"], 0.008)
        self.assertEqual((c["summary"]["in"], c["summary"]["out"]), (120, 60))
        self.assertAlmostEqual(c["category"]["cost"], 0.012)

    def test_zero_trace_is_noop(self):
        serve = self._serve()
        serve._log_cost_rollup({}, team=None)                # 비용·콜 없음 → 기록 안 함
        serve._log_cost_rollup({"cost_usd": 0, "by_call": {}}, team=None)
        d = serve.cost_rollup_data(None, days=7)
        self.assertEqual(d["total"]["n"], 0)
        self.assertEqual(d["by_model"], [])

    def test_team_scoped(self):
        serve = self._serve()
        serve._log_cost_rollup(_trace(), team="team-A")
        self.assertEqual(serve.cost_rollup_data("team-A", days=7)["total"]["n"], 1)
        self.assertEqual(serve.cost_rollup_data("team-B", days=7)["total"]["n"], 0)


if __name__ == "__main__":
    unittest.main()
