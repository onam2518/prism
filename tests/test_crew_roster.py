"""검수운영 명단의 기준은 '현재 팀원'이다(2026-07-28 신고).

팀에서 제거한 사용자가 운영 관리에 계속 떴다. 명단을 팀원이 아니라 이력 합집합
(판정·프로필·배정)으로 만들고 있어서, 프로필만 남아도 살아남았다. 남으면 배정 후보와
팀 캐파·평균에까지 섞인다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class RosterTest(unittest.TestCase):
    def _src(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "prism", "crewops.py"), encoding="utf-8") as f:
            return f.read()

    def test_ids_are_intersected_with_current_roster(self):
        s = self._src()
        self.assertIn("(seen & set(rvs)) if rvs else seen", s)

    def test_empty_roster_falls_back_to_history(self):
        """reviewers_map 조회가 실패해 비면 화면이 통째로 비면 안 된다."""
        s = self._src()
        i = s.index("seen = set(rvs)")
        self.assertIn("if rvs else seen", s[i:i + 260])

    def test_orphan_assignment_is_surfaced(self):
        """명단에서 빠지면서 그 사람의 미완료 배정이 조용히 사라지면, 그 콘텐츠는
        아무도 보지 않는 채 남는다 — 요약에 남겨 재배정으로 회수하게 한다."""
        s = self._src()
        self.assertIn("orphan = sorted(", s)
        self.assertIn('"orphan": orphan_info', s)

    def test_panel_rendered(self):
        from prism import page
        self.assertIn("팀에서 빠진 검수자의 배정", page.PAGE)
        self.assertIn("crewSum.orphan", page.PAGE)


if __name__ == "__main__":
    unittest.main()
