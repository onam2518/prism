"""품질 통계: Krippendorff alpha · Dawid-Skene EM · 이항 신뢰구간.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
구조 근거는 TESTING.md 참조.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestQuality(unittest.TestCase):
    def test_alpha_perfect_and_chance(self):
        from prism import quality as Q
        self.assertEqual(Q.krippendorff_alpha_binary([[1, 1], [0, 0]]), 1.0)   # 완전 일치
        self.assertEqual(Q.krippendorff_alpha_binary([[1, 0]]), 0.0)           # 우연 수준
        self.assertIsNone(Q.krippendorff_alpha_binary([[1]]))                  # 평가 불가

    def test_percent_agreement(self):
        from prism import quality as Q
        self.assertEqual(Q.percent_agreement([[1, 1], [1, 0]]), 0.5)

    def test_binomial_ci(self):
        from prism import quality as Q
        lo, hi = Q.binomial_ci(0.5, 100)
        self.assertAlmostEqual(lo, 0.402, places=3)
        self.assertAlmostEqual(hi, 0.598, places=3)
        self.assertEqual(Q.binomial_ci(1.0, 0), (0.0, 1.0))                    # n=0 방어

    def test_dawid_skene_flags_bad_annotator(self):
        from prism import quality as Q
        # r1·r2 는 항상 합의, r3 는 항상 반대 → r3 오류율이 가장 높아야 함
        labels = {f"u{i}": {"r1": i % 2, "r2": i % 2, "r3": 1 - (i % 2)} for i in range(10)}
        out = Q.dawid_skene_binary(labels)
        r = out["reviewers"]
        self.assertGreater(r["r3"]["error_rate"], r["r1"]["error_rate"])
        self.assertEqual(r["r1"]["n"], 10)


if __name__ == "__main__":
    unittest.main()
