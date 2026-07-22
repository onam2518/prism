"""중간 심각도 보안 회귀 테스트(2026-07-15 감사).

- SSRF: 100.64.0.0/10(CGNAT) 차단 추가.
- 관리자 허용목록 fail-closed: supabase 모드 + 목록 미설정 = 운영 관리자 없음(파괴작업 차단).
- 인입 소스 auth 마스킹 + 재저장 시 실값 복원(마스크 라운드트립).
- SSE broadcast 팀 스코프: team 지정 시 같은 팀 구독자에게만.

실행: python3 -m pytest tests/test_security_medium.py -q
"""
import os
import queue as _q
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSsrfCgnat(unittest.TestCase):
    def test_cgnat_blocked_public_allowed(self):
        from prism import serve as SV
        self.assertIsNotNone(SV._validate_public_url("http://100.64.1.2/x"))     # CGNAT 차단
        self.assertIsNotNone(SV._validate_public_url("http://100.127.255.1/"))   # /10 경계 내
        self.assertIsNone(SV._validate_public_url("http://93.184.216.34/ok"))    # 공인 통과
        self.assertIsNone(SV._validate_public_url("http://100.128.0.1/ok"))      # /10 밖 = 공인


class TestSysAdminFailClosed(unittest.TestCase):
    def setUp(self):
        from prism import adminops as AO
        self.AO = AO
        self._orig = (AO._supa, AO.admin_emails, AO._team_admin)
        AO.admin_emails = lambda: set()                  # 허용목록 미설정
        AO._team_admin = lambda uid, team: True          # 위임 팀 관리자(폴백 대상)
        self.addCleanup(lambda: self._restore())

    def _restore(self):
        self.AO._supa, self.AO.admin_emails, self.AO._team_admin = self._orig

    def test_supabase_empty_allowlist_denies(self):
        self.AO._supa = lambda: ("http://x", "k")
        self.assertFalse(self.AO.is_sys_admin_user("u", "t", "a@b.c"))  # 운영 모드 폴백 금지

    def test_local_empty_allowlist_falls_back(self):
        self.AO._supa = lambda: None
        self.assertTrue(self.AO.is_sys_admin_user("u", "t", "a@b.c"))   # 로컬 단독은 팀 관리자 폴백

    def test_allowlist_email_always_admin(self):
        self.AO._supa = lambda: ("http://x", "k")
        self.AO.admin_emails = lambda: {"ops@corp.com"}
        self.assertTrue(self.AO.is_sys_admin_user("u", "t", "ops@corp.com"))
        self.assertFalse(self.AO.is_sys_admin_user("u", "t", "other@x.com"))


class TestIngestSecretMask(unittest.TestCase):
    def test_mask_and_roundtrip(self):
        from prism import serve as SV
        old = [{"id": "s1", "endpoint": "https://api/x", "auth": "Bearer SECRET"},
               {"id": "s2", "endpoint": "https://api/y", "auth": ""}]
        masked = SV._mask_ingest_sources(old)
        self.assertEqual(masked[0]["auth"], "***")       # 시크릿 마스킹
        self.assertEqual(masked[1]["auth"], "")          # 빈 값은 그대로
        # UI 가 마스킹된 응답을 그대로 재저장 → 실값 복원(덮어쓰기 방지)
        restored = SV._unmask_ingest_sources(masked, old)
        self.assertEqual(restored[0]["auth"], "Bearer SECRET")
        # 매칭 안 되는 센티널은 빈 값(리터럴 '***' 저장 방지)
        orphan = SV._unmask_ingest_sources([{"id": "sX", "auth": "***"}], old)
        self.assertEqual(orphan[0]["auth"], "")
        # 새 실값은 그대로 저장
        changed = SV._unmask_ingest_sources([{"id": "s1", "auth": "Bearer NEW"}], old)
        self.assertEqual(changed[0]["auth"], "Bearer NEW")


class TestSseTeamScope(unittest.TestCase):
    def setUp(self):
        from prism import serve as SV
        self.SV = SV
        with SV._sub_lock:
            self._saved = list(SV._subscribers)
            SV._subscribers.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        with self.SV._sub_lock:
            self.SV._subscribers[:] = self._saved

    def test_broadcast_scoped_to_team(self):
        SV = self.SV
        qa = SV._sse_subscribe("teamA")
        qb = SV._sse_subscribe("teamB")
        SV.broadcast({"type": "feedback", "x": 1}, team="teamA")
        self.assertEqual(qa.get_nowait()["x"], 1)        # 같은 팀만 수신
        with self.assertRaises(_q.Empty):
            qb.get_nowait()                              # 타 팀 미수신

    def test_broadcast_none_team_all(self):
        SV = self.SV
        qa = SV._sse_subscribe("teamA")
        qb = SV._sse_subscribe(None)
        SV.broadcast({"type": "reviewer"}, team=None)    # 팀 무관 = 전체
        self.assertTrue(qa.get_nowait())
        self.assertTrue(qb.get_nowait())

    def test_unsubscribe_removes(self):
        SV = self.SV
        q = SV._sse_subscribe("teamA")
        SV._sse_unsubscribe(q)
        SV.broadcast({"z": 1}, team="teamA")
        with self.assertRaises(_q.Empty):
            q.get_nowait()


class TestSseQueryTokenTeam(unittest.TestCase):
    """/events(EventSource)는 Authorization 헤더를 못 싣어 token 쿼리로 인증한다.
    구독 팀도 이 토큰으로 해석해야 한다(헤더 전용 _req_team 사용 시 운영에서 전원 team=None
    → 팀 스코프 전면 무력화 + 교차팀 유출 회귀). _serve_sse 는 블로킹 루프라 캡처용으로 대체."""

    def setUp(self):
        import prism.serve as SV
        self.SV = SV
        self._orig = (SV._supa, SV.validate_jwt, SV.team_of)
        SV._supa = lambda: ("http://x", "k")
        SV.validate_jwt = lambda tok, strict=False: {"tokA": "uidA"}.get(tok)
        SV.team_of = lambda uid: {"uidA": "teamA"}.get(uid)

    def tearDown(self):
        self.SV._supa, self.SV.validate_jwt, self.SV.team_of = self._orig

    def _capture(self):
        got = []
        return got, types.SimpleNamespace(_serve_sse=lambda team=None: got.append(team))

    def test_events_resolves_team_from_query_token(self):
        got, h = self._capture()
        self.SV._g_events(h, {"token": ["tokA"]})
        self.assertEqual(got, ["teamA"])                 # 쿼리 token → 구독 팀 해석

    def test_events_no_token_team_none(self):
        got, h = self._capture()
        self.SV._g_events(h, {})
        self.assertEqual(got, [None])                    # 토큰 없음 → None(전 팀 폴백 아님)

    def test_events_local_mode_team_none(self):
        self.SV._supa = lambda: None
        got, h = self._capture()
        self.SV._g_events(h, {"token": ["tokA"]})
        self.assertEqual(got, [None])                    # sqlite 단독은 팀 스코프 없음


if __name__ == "__main__":
    unittest.main()
