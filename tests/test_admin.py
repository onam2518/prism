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
        from prism import adminops as AO                      # 권한 로직 정의 위치(라우트 분리 2차)
        orig = AO.admin_emails
        orig_team = AO._team_admin
        AO.admin_emails = lambda: {"ops@corp.com"}
        self.addCleanup(lambda: setattr(AO, "admin_emails", orig))
        self.addCleanup(lambda: setattr(AO, "_team_admin", orig_team))
        AO._team_admin = lambda uid, team: uid == "team-boss"
        # 운영 관리자: 허용목록 이메일만 · 팀 소속 무관
        self.assertTrue(AO.is_sys_admin_user("u1", None, "ops@corp.com"))
        self.assertFalse(AO.is_sys_admin_user("team-boss", "t1", "member@corp.com"))
        # 관리자(팀 관리 접근) = 운영 OR 팀 관리자 · 허용목록이 팀 관리자를 죽이지 않는다
        self.assertTrue(AO.is_admin_user("team-boss", "t1", "member@corp.com"))
        self.assertTrue(AO.is_admin_user("u1", "t1", "ops@corp.com"))
        self.assertFalse(AO.is_admin_user("u2", "t1", "member@corp.com"))
        # 허용목록 미설정 → 팀 관리자 로직 폴백
        AO.admin_emails = lambda: set()
        self.assertTrue(AO.is_sys_admin_user("team-boss", "t1", "any@x.com"))

    def test_admin_data_without_team_keeps_sys_admin(self):
        from prism import adminops as AO
        import prism.serve                                     # _SV 주입(컴포지션) 보장
        self.assertIs(AO._SV, prism.serve)
        orig = AO.admin_emails
        AO.admin_emails = lambda: {"ops@corp.com"}
        self.addCleanup(lambda: setattr(AO, "admin_emails", orig))
        d = AO.admin_data("uid", None, "ops@corp.com")        # 팀 미소속 로그인 직후
        self.assertTrue(d["isSysAdmin"] and d["isAdmin"])      # 관리자 메뉴 게이팅 유지
        d2 = AO.admin_data("uid", None, "member@x.com")
        self.assertFalse(d2["isSysAdmin"])

    def test_apply_config_key_gate_supabase(self):
        """supabase 모드 API 키 등록: 운영 관리자(allow_key=True)만 반영 · 그 외 무시."""
        import prism.serve as SV
        orig_mode = SV.backend_mode
        orig_env = os.environ.get("UPSTAGE_API_KEY")
        SV.backend_mode = lambda: ("supabase", True)
        def _restore():
            SV.backend_mode = orig_mode
            if orig_env is None:
                os.environ.pop("UPSTAGE_API_KEY", None)
            else:
                os.environ["UPSTAGE_API_KEY"] = orig_env
        self.addCleanup(_restore)
        os.environ.pop("UPSTAGE_API_KEY", None)
        SV.apply_config({"api_key": "sk-test-gate"})           # 비관리자(기본): 무시
        self.assertNotEqual(os.environ.get("UPSTAGE_API_KEY"), "sk-test-gate")
        SV.apply_config({"api_key": "sk-test-gate"}, allow_key=True)   # 운영 관리자: 반영
        self.assertEqual(os.environ.get("UPSTAGE_API_KEY"), "sk-test-gate")

    def test_rerun_blocked_while_quest_active(self):
        """퀘스트 진행 중 전체 재실행 차단(검수 중 초안 교체 = 합의 오염 방지) · 미실행만은 허용."""
        import prism.serve as SV
        orig = SV.quest_active
        SV.quest_active = lambda: True
        self.addCleanup(lambda: setattr(SV, "quest_active", orig))
        r = SV.rerun_all("solar-pro2", scope="all")
        self.assertIn("퀘스트 진행 중", r.get("error", ""))
        r2 = SV.rerun_all("solar-pro2", scope="pending")      # 미실행 실행은 초안 교체가 아님
        self.assertTrue(r2.get("ok"))
        SV.quest_active = lambda: False                        # 퀘스트 없으면 차단 없음
        r3 = SV.rerun_all("solar-pro2", scope="all")
        self.assertNotIn("error", r3)

    def test_local_store_clear_helpers(self):
        import tempfile
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        st.save_feedback("h1", "뉴스", "t", "good", "review", "", 1.0, reviewer="복실")
        st.clear_team_feedback()
        self.assertEqual(st.feedback_map(), {})

    def test_validate_jwt_strict_distinguishes_transient(self):
        """인증 서버 일시 장애 vs 토큰 무효 구분: /admin 이 200+isAdmin:false 로 굳어
        관리자 메뉴가 사라지던 간헐 증상의 원천(2026-07-07)."""
        import io
        import urllib.error
        import urllib.request
        from prism import adminops as AO
        orig_supa, orig_open = AO._supa, urllib.request.urlopen
        AO._supa = lambda: ("http://auth.test", "k")
        self.addCleanup(lambda: (setattr(AO, "_supa", orig_supa),
                                 setattr(urllib.request, "urlopen", orig_open)))

        def transient(req, timeout=None):
            raise OSError("connection refused")
        urllib.request.urlopen = transient
        self.assertIsNone(AO.validate_jwt("tok-x"))              # 비 strict = 기존 계약(None)
        with self.assertRaises(AO.AuthBackendUnavailable):       # strict = 재시도 신호(503 응답용)
            AO.validate_jwt("tok-x", strict=True)

        def invalid(req, timeout=None):
            raise urllib.error.HTTPError("u", 401, "bad", None, io.BytesIO(b"{}"))
        urllib.request.urlopen = invalid
        self.assertIsNone(AO.validate_jwt("tok-y", strict=True))  # 토큰 무효 = 확정 None(재시도 없음)

    def test_auth_refresh_mode(self):
        """세션 갱신(grant_type=refresh_token): 이메일·비밀번호 없이 토큰 연장 ·
        access 1시간 만료로 관리자 메뉴가 조용히 강등되던 증상(2026-07-08)의 해결 경로."""
        from prism import adminops as AO
        orig_supa, orig_post = AO._supa, AO._auth_post
        AO._supa = lambda: ("http://auth.test", "k")
        self.addCleanup(lambda: (setattr(AO, "_supa", orig_supa),
                                 setattr(AO, "_auth_post", orig_post)))
        calls = []

        def fake_post(url, path, key, body):
            calls.append((path, body))
            return {"access_token": "new-at", "refresh_token": "new-rt", "user": {"id": "u1", "email": "a@b.c"}}
        AO._auth_post = fake_post
        r = AO.auth_action({"mode": "refresh", "refresh_token": "old-rt"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["access_token"], "new-at")
        self.assertEqual(r["refresh_token"], "new-rt")                # 회전된 토큰 전달
        self.assertIn("grant_type=refresh_token", calls[0][0])
        self.assertEqual(calls[0][1], {"refresh_token": "old-rt"})
        r2 = AO.auth_action({"mode": "refresh"})                      # 토큰 없음 = 재로그인 안내
        self.assertFalse(r2["ok"])
        # 로그인 응답도 refresh_token 을 포함(클라이언트 저장용)
        r3 = AO.auth_action({"mode": "login", "email": "a@b.c", "password": "pw"})
        self.assertTrue(r3["ok"] and r3["refresh_token"] == "new-rt")

    def test_super_admin_tier(self):
        """권한 3단계: 슈퍼관리자(생성자 부여)는 운영 작업 접근 O · 팀 관리자만인 멤버는 X."""
        from prism import adminops as AO
        orig = (AO.admin_emails, AO._team_admin, AO._team_super)
        self.addCleanup(lambda: (setattr(AO, "admin_emails", orig[0]),
                                 setattr(AO, "_team_admin", orig[1]),
                                 setattr(AO, "_team_super", orig[2])))
        AO.admin_emails = lambda: {"ops@corp.com"}
        AO._team_admin = lambda uid, team: uid in ("boss", "mgr")
        AO._team_super = lambda uid, team: uid in ("boss", "sup")
        self.assertTrue(AO.is_super_admin_user("sup", "t1", "sup@x.com"))    # 슈퍼관리자
        self.assertFalse(AO.is_super_admin_user("mgr", "t1", "mgr@x.com"))   # 팀 관리자는 운영 작업 불가
        self.assertTrue(AO.is_super_admin_user("u1", None, "ops@corp.com"))  # 운영 관리자 포함
        self.assertTrue(AO.is_admin_user("sup", "t1", "sup@x.com"))          # 팀 관리 접근도 포함

    def test_permission_grant_creator_only(self):
        """관리자·슈퍼관리자 지정/해제는 오직 팀 생성자만 · 부여받은 관리자(운영 관리자 포함) 불가."""
        from prism import adminops as AO

        class FakeStore:
            def __init__(self):
                self.calls = []
            def team_info(self, team):
                return {"id": team, "created_by": "boss", "invite_code": "X"}
            def set_member_admin(self, team, member, on):
                self.calls.append(("admin", member, bool(on)))
            def set_member_super(self, team, member, on):
                self.calls.append(("super", member, bool(on)))
            def is_team_admin(self, uid, team):
                return uid in ("boss", "mgr")
            def is_team_super(self, uid, team):
                return uid == "boss"

        st = FakeStore()
        orig_store, orig_supa, orig_emails = AO._SV.get_store, AO._supa, AO.admin_emails
        AO._SV.get_store = lambda: st
        AO._supa = lambda: ("http://x", "k")
        AO.admin_emails = lambda: {"ops@corp.com"}
        self.addCleanup(lambda: (setattr(AO._SV, "get_store", orig_store),
                                 setattr(AO, "_supa", orig_supa),
                                 setattr(AO, "admin_emails", orig_emails)))
        # 위임받은 관리자(mgr)와 운영 관리자(허용목록)도 지정 불가
        r = AO.admin_action("mgr", "t1", {"action": "set_super", "member": "u9"}, "mgr@x.com")
        self.assertIn("팀 생성자만", r.get("error", ""))
        r = AO.admin_action("u1", "t1", {"action": "set_admin", "member": "u9"}, "ops@corp.com")
        self.assertIn("팀 생성자만", r.get("error", ""))
        self.assertEqual(st.calls, [])
        # 생성자는 지정/해제 가능 · 생성자 자신은 대상 불가
        r = AO.admin_action("boss", "t1", {"action": "set_super", "member": "u9"}, "boss@x.com")
        self.assertTrue(r.get("ok"))
        r = AO.admin_action("boss", "t1", {"action": "unset_admin", "member": "u8"}, "boss@x.com")
        self.assertTrue(r.get("ok"))
        self.assertEqual(st.calls, [("super", "u9", True), ("admin", "u8", False)])
        r = AO.admin_action("boss", "t1", {"action": "set_super", "member": "boss"}, "boss@x.com")
        self.assertIn("생성자의 권한", r.get("error", ""))


if __name__ == "__main__":
    unittest.main()
