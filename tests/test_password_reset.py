"""비밀번호 재설정(생성자 전용) 게이트·동작 회귀 테스트.

생성자만 팀원 임시 비밀번호를 발급 · 생성자 본인·비팀원·비생성자는 거부 ·
Supabase auth admin(PUT /auth/v1/admin/users/{uid}) 호출 파라미터 검증.

실행: python3 -m pytest tests/test_password_reset.py -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import adminops as AO           # noqa: E402


class _FakeStore:
    def __init__(self, created_by, members):
        self._cb = created_by
        self._members = members

    def team_info(self, team):
        return {"created_by": self._cb}

    def team_members(self, team):
        return list(self._members)

    # 게이트가 is_admin_user 를 지나므로 최소 구현 제공.
    # M 은 위임된 팀 관리자(외부 게이트 통과) 지만 생성자는 아님 → 재설정은 거부돼야 한다.
    def is_team_super(self, uid, team):
        return uid == self._cb

    def is_team_admin(self, uid, team):
        return uid in (self._cb, "M")


class _FakeSV:
    def __init__(self, store):
        self._s = store

    def get_store(self):
        return self._s


class PwResetBase(unittest.TestCase):
    def setUp(self):
        self._orig = (AO._supa, AO._SV, AO.admin_emails, AO._auth_req)
        AO._supa = lambda: ("https://x.supabase.co", "svc-key")
        AO.admin_emails = lambda: set()
        self.calls = []
        AO._auth_req = lambda url, path, key, body, method="POST": self.calls.append(
            {"url": url, "path": path, "key": key, "body": body, "method": method}) or {}
        store = _FakeStore(created_by="C", members=[
            {"id": "C", "name": "생성자"}, {"id": "M", "name": "복실"}])
        AO._SV = _FakeSV(store)

    def tearDown(self):
        AO._supa, AO._SV, AO.admin_emails, AO._auth_req = self._orig

    def _act(self, uid, member):
        return AO.admin_action(uid, "team1", {"action": "reset_password", "member": member})


class TestPasswordReset(PwResetBase):
    def test_creator_resets_member(self):
        r = self._act("C", "M")
        self.assertTrue(r.get("ok"))
        self.assertTrue(r.get("tempPassword"))
        self.assertGreaterEqual(len(r["tempPassword"]), 8)
        self.assertEqual(r.get("member"), "복실")
        # Supabase admin PUT 로 대상 uid 비밀번호 설정
        self.assertEqual(len(self.calls), 1)
        c = self.calls[0]
        self.assertEqual(c["method"], "PUT")
        self.assertEqual(c["path"], "/auth/v1/admin/users/M")
        self.assertEqual(c["body"], {"password": r["tempPassword"]})

    def test_non_creator_delegated_admin_denied(self):
        # 위임 관리자여도 재설정은 생성자만 (M 은 생성자 아님)
        r = self._act("M", "C")
        self.assertFalse(r.get("ok"))
        self.assertIn("생성자", r.get("error", ""))
        self.assertEqual(self.calls, [])

    def test_creator_cannot_reset_self(self):
        r = self._act("C", "C")
        self.assertFalse(r.get("ok"))
        self.assertEqual(self.calls, [])

    def test_non_member_rejected(self):
        r = self._act("C", "GHOST")
        self.assertFalse(r.get("ok"))
        self.assertIn("멤버", r.get("error", ""))
        self.assertEqual(self.calls, [])

    def test_temp_password_excludes_ambiguous_chars(self):
        r = self._act("C", "M")
        for ch in "0O1lI":
            self.assertNotIn(ch, r["tempPassword"])


if __name__ == "__main__":
    unittest.main()
