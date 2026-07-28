"""배정 가중: 과거 실측보다 '이번 주 신고'에 무게를 옮긴다(사용자 결정 2026-07-28).

운영 실측: 신고 시간이 전원 기본값이라 캐파 차이가 전적으로 과거 속도에서 나왔다
(4.1배 · 상위 3명 40%). 속도를 팀 중앙값 쪽으로 당겨(rate_shrink) 캐파가 '이번 주에
내겠다고 한 시간'에 더 비례하게 만들고, 이번 주 확인이 없는 사람은 보수적으로 잡는다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import crewops as CRW


class ShrinkTest(unittest.TestCase):
    def _cfg(self, shrink):
        c = dict(CRW.DEFAULT_SETTINGS)
        c["rate_shrink"] = shrink
        return c

    def test_shrink_1_keeps_measured_rate(self):
        r = CRW._effective_rate({"rate_per_hour": 40.0}, {}, self._cfg(1.0), 70.0)
        self.assertAlmostEqual(r, 40.0, places=3)

    def test_shrink_0_collapses_to_team_median(self):
        for measured in (20.0, 40.0, 89.0):
            r = CRW._effective_rate({"rate_per_hour": measured}, {}, self._cfg(0.0), 70.0)
            self.assertAlmostEqual(r, 70.0, places=3, msg=measured)

    def test_half_shrink_is_midway(self):
        r = CRW._effective_rate({"rate_per_hour": 30.0}, {}, self._cfg(0.5), 70.0)
        self.assertAlmostEqual(r, 50.0, places=3)          # 70 + (30-70)*0.5

    def test_shrink_narrows_the_spread(self):
        """쏠림 완화가 목적이므로 배율이 실제로 줄어야 한다."""
        fast, slow = 90.0, 22.0
        wide = (CRW._effective_rate({"rate_per_hour": fast}, {}, self._cfg(1.0), 70.0)
                / CRW._effective_rate({"rate_per_hour": slow}, {}, self._cfg(1.0), 70.0))
        tight = (CRW._effective_rate({"rate_per_hour": fast}, {}, self._cfg(0.5), 70.0)
                 / CRW._effective_rate({"rate_per_hour": slow}, {}, self._cfg(0.5), 70.0))
        self.assertGreater(wide, tight)
        self.assertLess(tight, 2.5)

    def test_speed_cap_still_applies_before_shrink(self):
        cap = float(CRW.DEFAULT_SETTINGS["rate_cap_per_hour"])
        r = CRW._effective_rate({"rate_per_hour": 500.0}, {}, self._cfg(1.0), 70.0)
        self.assertLessEqual(r, cap)

    def test_manual_override_is_not_shrunk(self):
        """사람이 명시한 값은 추정이 아니라 지시다 — 중앙값 쪽으로 당기지 않는다."""
        r = CRW._effective_rate({"rate_per_hour": 500.0}, {"rate_override": 40}, self._cfg(0.0), 70.0)
        self.assertAlmostEqual(r, 40.0, places=3)

    def test_bad_shrink_value_does_not_crash(self):
        c = dict(CRW.DEFAULT_SETTINGS); c["rate_shrink"] = "이상한값"
        self.assertGreater(CRW._effective_rate({"rate_per_hour": 40.0}, {}, c, 70.0), 0)


class SettingsTest(unittest.TestCase):
    def test_new_knobs_have_defaults(self):
        self.assertIn("rate_shrink", CRW.DEFAULT_SETTINGS)
        self.assertIn("unconfirmed_factor", CRW.DEFAULT_SETTINGS)
        self.assertTrue(0.0 <= CRW.DEFAULT_SETTINGS["rate_shrink"] <= 1.0)
        self.assertTrue(0.0 < CRW.DEFAULT_SETTINGS["unconfirmed_factor"] <= 1.0)


class MarkupTest(unittest.TestCase):
    def test_knobs_and_rationale_shown(self):
        from prism import page
        self.assertIn("과거 속도 반영도", page.PAGE)
        self.assertIn("미확인자 반영도", page.PAGE)
        self.assertIn("이번 주 미확인", page.PAGE)          # 카드 배지
        self.assertIn("배분 기준", page.PAGE)               # 자동 배정 패널 설명


if __name__ == "__main__":
    unittest.main()
