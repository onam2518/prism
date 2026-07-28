"""배정 하한·상한 (사용자 결정 2026-07-28).

· 하한: 아무도 캐파의 절반을 채우기 전에는 다음 사람으로 넘어가지 않는다.
  소수에게 몰아주고 나머지를 0건으로 두는 것도 쏠림이다.
· 상한: 잔여가 이미 캐파를 넘긴 사람은 새 배정을 뒤로 미룬다. 성과 판단이 아니라
  용량 판단이다(더 줘도 그 주에 못 하고 그 콘텐츠까지 정체된다). 다만 다른 후보가
  없으면 받는다 — 배정 자체가 멈추면 안 된다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import crewops as CRW


class SettingsTest(unittest.TestCase):
    def test_defaults_present(self):
        self.assertEqual(CRW.DEFAULT_SETTINGS["min_fill_ratio"], 0.5)   # 절반
        self.assertEqual(CRW.DEFAULT_SETTINGS["cap_limit"], 1.0)


class ScoreBandTest(unittest.TestCase):
    """구간 순서: 하한 미달 < 정상 < 캐파 초과. 구간이 뒤집히면 안 된다."""

    def _src(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "prism", "crewops.py"), encoding="utf-8") as f:
            return f.read()

    def test_three_bands_exist(self):
        s = self._src()
        i = s.index("def _score(rid, cs):")
        body = s[i:i + 1600]
        self.assertIn("return -2.0 + adj / fl", body)          # 보장 구간
        self.assertIn("return adj / cap[rid]", body)           # 정상 구간
        self.assertIn("return 100.0 + adj / cap[rid]", body)   # 초과 구간

    def test_floor_uses_actual_load_not_match_bonus(self):
        """구간 판정은 실제 배정량(u)으로 · 분야 강점 보정(adj)은 구간 안 정렬에만."""
        s = self._src()
        i = s.index("def _score(rid, cs):")
        body = s[i:i + 1600]
        self.assertIn("if fl > 0 and u < fl:", body)
        self.assertIn("if u < cap[rid] * cap_limit:", body)

    def test_held_is_reported(self):
        s = self._src()
        self.assertIn('"held": held', s)
        self.assertIn("used[m[\"id\"]] >= cap[m[\"id\"]] * cap_limit", s)

    def test_plan_exposes_floor(self):
        s = self._src()
        self.assertIn('"floor": floors.get(rid, 0)', s)


class MarkupTest(unittest.TestCase):
    def test_knobs_and_held_shown(self):
        from prism import page
        self.assertIn("보장 하한(캐파 대비)", page.PAGE)
        self.assertIn("새 배정 상한(캐파 배수)", page.PAGE)
        self.assertIn("이번엔 새 배정 없음", page.PAGE)
        self.assertIn("p.floor", page.PAGE)                     # 계획 표의 하한 열


if __name__ == "__main__":
    unittest.main()
