"""운영 관리 메뉴가 한쪽 권한 때문에 통째로 숨던 회귀 차단.

배경(2026-07-29 운영 신고): 슈퍼관리자에게 '운영 관리' 메뉴가 안 보였다.
생성자가 예전에 저장한 매트릭스가 `admin: {super: false, admin: false}` 였는데,
그 메뉴 id 가 '팀 관리' 에서 '운영 관리(팀 관리 + 검수운영)' 으로 바뀌면서
검수운영까지 함께 잠긴 것. crew 는 {super: true} 로 열려 있었는데도 못 들어갔다.

한 메뉴가 두 기능을 담으면 상위는 '둘 중 하나라도 가능'으로 열고,
각 탭이 자기 권한으로 게이트해야 한다. 브라우저 없이 정적 검사한다.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UI = os.path.join(_ROOT, "prism", "ui")
_JS = os.path.join(_ROOT, "prism", "vendor")


def _read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


class TestOpsMenuGate(unittest.TestCase):
    def test_sidebar_uses_composite_visibility(self):
        """사이드바의 운영 관리 항목은 합성 판정(opsMenuVisible)을 써야 한다."""
        head = _read(_UI, "00-head.html")
        self.assertIn("opsMenuVisible", head,
                      "운영 관리 메뉴가 admin 권한 하나에만 묶여 있습니다 · "
                      "팀 관리를 끄면 검수운영까지 숨습니다")

    def test_getters_exist(self):
        js = _read(_JS, "app-00-tabitems.js")
        for g in ("canTeamTab", "canCrewTab", "opsMenuVisible"):
            self.assertIn(g, js, f"{g} 판정이 없습니다")
        # 합성 = 둘 중 하나라도
        self.assertRegex(js, r"opsMenuVisible\(\)\s*\{\s*return\s+this\.canTeamTab\s*\|\|\s*this\.canCrewTab")

    def _tab_button(self, markup: str, label: str) -> str:
        """탭 버튼 한 개의 마크업(줄바꿈 포함)을 돌려준다."""
        end = markup.index(">" + label + "</button>")
        start = markup.rindex("<button", 0, end)
        return markup[start:end]

    def test_each_tab_gated_by_own_permission(self):
        team = _read(_UI, "15-team.html")
        self.assertIn('x-show="canTeamTab"', self._tab_button(team, "팀 관리"),
                      "팀 관리 탭이 자기 권한으로 게이트되지 않습니다")
        self.assertIn('x-show="canCrewTab"', self._tab_button(team, "검수운영 관리"),
                      "검수운영 탭이 자기 권한으로 게이트되지 않습니다")
        # 본문도 함께 잠가야 탭만 숨고 내용이 남는 일이 없다
        self.assertIn("adminTab === 'team' && canTeamTab", team)

    def test_unreachable_tab_is_switched(self):
        """볼 수 없는 탭이 선택돼 있으면 빈 화면이 된다 · 접근 가능한 탭으로 옮겨야 한다."""
        js = _read(_JS, "app-02-_afterverdict.js")
        self.assertIn("!this.canTeamTab && this.canCrewTab", js)
        self.assertIn("!this.canCrewTab && this.canTeamTab", js)


class TestServerMatrixUnchanged(unittest.TestCase):
    """서버 기본 매트릭스는 그대로 · 이번 수정은 프론트 노출 판정만 바꾼다."""

    def test_defaults(self):
        from prism import adminops as AO
        self.assertEqual(AO.DEFAULT_MENU_PERMS["admin"], {"super": True, "admin": True})
        self.assertEqual(AO.DEFAULT_MENU_PERMS["crew"], {"super": True, "admin": False})

    def test_stored_off_admin_still_allows_crew_routes(self):
        """저장된 admin=off 가 crew 라우트 권한까지 막지는 않는다(서버는 id 별 판정)."""
        from prism import adminops as AO
        self.assertIn("crew", AO.CONFIGURABLE_MENUS)
        self.assertNotEqual(AO.DEFAULT_MENU_PERMS["crew"], AO.DEFAULT_MENU_PERMS["admin"])


if __name__ == "__main__":
    unittest.main()
