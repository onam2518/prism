"""권한 2단계: 운영 관리자(허용목록) · 팀 관리자 · 팀 미소속 게이팅.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
구조 근거는 TESTING.md 참조.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAdminTiers(unittest.TestCase):
    """권한 2단계: 운영 관리자(허용목록) vs 팀 관리자(생성자·위임 · 팀 관리만)."""
    def test_sys_admin_allowlist_and_team_admin_tiers(self):
        from prism import serve
        orig = serve.admin_emails
        orig_team = serve._team_admin
        serve.admin_emails = lambda: {"ops@corp.com"}
        self.addCleanup(lambda: setattr(serve, "admin_emails", orig))
        self.addCleanup(lambda: setattr(serve, "_team_admin", orig_team))
        serve._team_admin = lambda uid, team: uid == "team-boss"
        # 운영 관리자: 허용목록 이메일만 · 팀 소속 무관
        self.assertTrue(serve.is_sys_admin_user("u1", None, "ops@corp.com"))
        self.assertFalse(serve.is_sys_admin_user("team-boss", "t1", "member@corp.com"))
        # 관리자(팀 관리 접근) = 운영 OR 팀 관리자 · 허용목록이 팀 관리자를 죽이지 않는다
        self.assertTrue(serve.is_admin_user("team-boss", "t1", "member@corp.com"))
        self.assertTrue(serve.is_admin_user("u1", "t1", "ops@corp.com"))
        self.assertFalse(serve.is_admin_user("u2", "t1", "member@corp.com"))
        # 허용목록 미설정 → 팀 관리자 로직 폴백
        serve.admin_emails = lambda: set()
        self.assertTrue(serve.is_sys_admin_user("team-boss", "t1", "any@x.com"))

    def test_admin_data_without_team_keeps_sys_admin(self):
        from prism import serve
        orig = serve.admin_emails
        serve.admin_emails = lambda: {"ops@corp.com"}
        self.addCleanup(lambda: setattr(serve, "admin_emails", orig))
        d = serve.admin_data("uid", None, "ops@corp.com")     # 팀 미소속 로그인 직후
        self.assertTrue(d["isSysAdmin"] and d["isAdmin"])      # 관리자 메뉴 게이팅 유지
        d2 = serve.admin_data("uid", None, "member@x.com")
        self.assertFalse(d2["isSysAdmin"])

    def test_local_store_clear_helpers(self):
        import tempfile
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        st.save_feedback("h1", "뉴스", "t", "good", "review", "", 1.0, reviewer="복실")
        st.clear_team_feedback()
        self.assertEqual(st.feedback_map(), {})


if __name__ == "__main__":
    unittest.main()
