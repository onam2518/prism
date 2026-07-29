"""권한 판정 캐시 회귀: TTL 내 스토어 왕복 1회 · 쓰기 경로 즉시 무효화 · 스토어별 격리.

menu_allowed·관리자 게이트가 요청마다 supabase 를 최대 10회 왕복하던 것을
adminops 의 60s TTL 캐시(_role_flags·_team_meta)로 줄인 변경의 계약을 고정한다.

실행: python3 -m pytest tests/test_authz_cache.py -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import adminops as AO           # noqa: E402


class _CountingStore:
    def __init__(self, created_by="C", supers=(), admins=(), perms=None):
        self._cb = created_by
        self._supers = set(supers)
        self._admins = set(admins)
        self._perms = perms or {}
        self.calls = {"team_info": 0, "menu_perms": 0, "is_team_admin": 0, "is_team_super": 0}

    def team_info(self, team):
        self.calls["team_info"] += 1
        return {"created_by": self._cb}

    def menu_perms(self, team):
        self.calls["menu_perms"] += 1
        return self._perms

    def set_menu_perms(self, team, perms):
        self._perms = perms
        return True

    def is_team_admin(self, uid, team):
        self.calls["is_team_admin"] += 1
        return uid in self._supers or uid in self._admins

    def is_team_super(self, uid, team):
        self.calls["is_team_super"] += 1
        return uid in self._supers


class _FakeSV:
    def __init__(self, store):
        self._s = store

    def get_store(self):
        return self._s


class AuthzCacheBase(unittest.TestCase):
    def setUp(self):
        self._orig = (AO._supa, AO._SV, AO.admin_emails)
        self.st = _CountingStore(supers=["S"], admins=["A"])
        AO._supa = lambda: True                      # supabase(운영) 모드 강제
        AO._SV = _FakeSV(self.st)
        AO.admin_emails = lambda: set()

    def tearDown(self):
        AO._supa, AO._SV, AO.admin_emails = self._orig


class TestAuthzCache(AuthzCacheBase):
    def test_repeat_calls_hit_store_once(self):
        for _ in range(5):
            self.assertTrue(AO.menu_allowed("S", "t", "", "content"))
        for name in ("team_info", "menu_perms", "is_team_admin", "is_team_super"):
            self.assertEqual(self.st.calls[name], 1, name)

    def test_clear_reflects_role_change(self):
        self.assertFalse(AO.menu_allowed("B", "t", "", "admin"))
        self.st._admins.add("B")
        self.assertFalse(AO.menu_allowed("B", "t", "", "admin"))   # TTL 내에는 캐시 유지
        AO.authz_cache_clear("t")
        self.assertTrue(AO.menu_allowed("B", "t", "", "admin"))    # 무효화 즉시 반영

    def test_clear_reflects_menu_perms_change(self):
        self.assertTrue(AO.menu_allowed("S", "t", "", "content"))
        self.st._perms = {"content": {"super": False, "admin": False}}
        AO.authz_cache_clear("t")
        self.assertFalse(AO.menu_allowed("S", "t", "", "content"))

    def test_store_isolation(self):
        # 같은 팀 키라도 스토어 객체가 다르면 캐시를 공유하지 않는다(테스트·_STORE 리셋 격리 계약)
        self.assertTrue(AO.menu_allowed("S", "t", "", "content"))
        AO._SV = _FakeSV(_CountingStore(supers=()))
        self.assertFalse(AO.menu_allowed("S", "t", "", "content"))


if __name__ == "__main__":
    unittest.main()
