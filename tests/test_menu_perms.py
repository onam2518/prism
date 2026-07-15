"""메뉴별 권한(생성자 설정) 회귀 테스트.

생성자·운영관리자 = 항상 전체 · 시스템 설정 = 운영관리자 전용(고정) ·
슈퍼/관리자 = 생성자 설정 매트릭스(미설정 시 기본 = 현재 동작). 백엔드 강제의 단일 판정원.

실행: python3 -m pytest tests/test_menu_perms.py -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import adminops as AO           # noqa: E402


class _FakeStore:
    def __init__(self, created_by, perms=None, supers=(), admins=()):
        self._cb = created_by
        self._perms = perms or {}
        self._supers = set(supers)
        self._admins = set(admins)

    def team_info(self, team):
        return {"created_by": self._cb}

    def menu_perms(self, team):
        return self._perms

    def set_menu_perms(self, team, perms):
        self._perms = perms
        return True

    def is_team_super(self, uid, team):
        return uid in self._supers

    def is_team_admin(self, uid, team):
        return uid in self._supers or uid in self._admins


class _FakeSV:
    def __init__(self, store):
        self._s = store

    def get_store(self):
        return self._s


class MenuPermBase(unittest.TestCase):
    def _install(self, store):
        self._orig = (AO._supa, AO._SV, AO.admin_emails)
        AO._supa = lambda: True                      # supabase(운영) 모드 강제
        AO._SV = _FakeSV(store)
        AO.admin_emails = lambda: set()              # 운영관리자 허용목록 없음(순수 팀 역할만)

    def tearDown(self):
        AO._supa, AO._SV, AO.admin_emails = self._orig


class TestMenuAllowed(MenuPermBase):
    def test_creator_all_menus(self):
        self._install(_FakeStore(created_by="C"))
        for mid in list(AO.CONFIGURABLE_MENUS) + ["system"]:
            self.assertTrue(AO.menu_allowed("C", "t", "", mid), mid)

    def test_super_defaults(self):
        self._install(_FakeStore(created_by="C", supers=["S"]))
        self.assertTrue(AO.menu_allowed("S", "t", "", "content"))    # 기본 super=True
        self.assertTrue(AO.menu_allowed("S", "t", "", "admin"))
        self.assertFalse(AO.menu_allowed("S", "t", "", "system"))    # 시스템 = 운영관리자 전용

    def test_admin_defaults(self):
        self._install(_FakeStore(created_by="C", admins=["A"]))
        self.assertFalse(AO.menu_allowed("A", "t", "", "content"))   # 기본 admin=False(opsadmin 메뉴)
        self.assertTrue(AO.menu_allowed("A", "t", "", "admin"))      # 팀 관리 admin=True
        self.assertFalse(AO.menu_allowed("A", "t", "", "system"))

    def test_creator_setting_overrides(self):
        # 생성자가 content 를 슈퍼관리자에게 숨김 · 관리자에게 studio 허용
        perms = {"content": {"super": False, "admin": False}, "studio": {"super": True, "admin": True}}
        self._install(_FakeStore(created_by="C", supers=["S"], admins=["A"], perms=perms))
        self.assertFalse(AO.menu_allowed("S", "t", "", "content"))   # 생성자가 숨김
        self.assertTrue(AO.menu_allowed("A", "t", "", "studio"))     # 생성자가 관리자에 허용
        self.assertTrue(AO.menu_allowed("S", "t", "", "studio"))

    def test_non_admin_denied(self):
        self._install(_FakeStore(created_by="C"))
        self.assertFalse(AO.menu_allowed("member", "t", "", "content"))

    def test_local_mode_all_allowed(self):
        self._orig = (AO._supa, AO._SV, AO.admin_emails)
        AO._supa = lambda: False                     # 로컬(sqlite) = 전체
        AO._SV = _FakeSV(_FakeStore(created_by="C"))
        AO.admin_emails = lambda: set()
        self.assertTrue(AO.menu_allowed("anyone", None, "", "system"))


class TestEffectivePerms(MenuPermBase):
    def test_merge_default_and_stored(self):
        self._install(_FakeStore(created_by="C", perms={"content": {"super": False}}))
        eff = AO.effective_menu_perms("t")
        self.assertEqual(eff["content"], {"super": False, "admin": False})  # stored super=False, admin=기본
        self.assertEqual(eff["admin"], {"super": True, "admin": True})      # 미설정 → 기본


if __name__ == "__main__":
    unittest.main()
