"""평가 채점의 지연 축: score() 가 건별 trace.latency_ms 를 p50/p95 로 집계한다.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
근거: by_call·total 지연은 이미 수집되는데 비교 지표에서만 빠져 있던 갭(2026-07-15 리뷰 P1-6).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _out(grade, ms=None):
    tr = {"cost_usd": 0.0, "tokens": {"in": 0, "out": 0}}
    if ms is not None:
        tr["latency_ms"] = {"total": ms}
    return {"quality_meta": {"finalGrade": grade, "reasons": [], "review": ""}, "trace": tr}


class TestScoreLatency(unittest.TestCase):
    def test_percentiles_from_traces(self):
        from prism.abtest import score
        rows = [{"expected": {"finalGrade": "G"}}] * 5
        outs = [_out("G", ms) for ms in (100, 200, 300, 400, 500)]
        m = score(rows, outs)
        self.assertEqual(m["latency_p50_ms"], 300.0)
        self.assertEqual(m["latency_p95_ms"], 500.0)

    def test_missing_latency_yields_none(self):
        from prism.abtest import score
        rows = [{"expected": {"finalGrade": "G"}}] * 2
        m = score(rows, [_out("G"), _out("G")])
        self.assertIsNone(m["latency_p50_ms"])
        self.assertIsNone(m["latency_p95_ms"])

    def test_ab_keys_include_latency_as_better_lower(self):
        from prism.abtest import _AB_KEYS, _BETTER_LOWER
        self.assertIn("latency_p50_ms", _AB_KEYS)
        self.assertIn("latency_p95_ms", _AB_KEYS)
        self.assertIn("latency_p50_ms", _BETTER_LOWER)
        self.assertIn("latency_p95_ms", _BETTER_LOWER)


if __name__ == "__main__":
    unittest.main()
