"""멀티테넌시 격리 회귀 테스트(2026-07-15 감사 · team=None 페일오픈).

- _gate_get / _team_ok: supabase 모드에서 팀 미소속 인증계정의 데이터 GET 을 fail-closed
  (운영 관리자·전역 참조 라우트만 예외). 팀=None 폴백으로 전 팀 데이터를 열람하던 경로 차단.
- _require_team: 데이터 POST(/usermeta·/learn-report)도 팀 소속 요구.
- build_results_csv / build_report_html: team 스코프를 저장소 조회에 전달(전 팀 CSV/리포트 유출 방지).

실행: python3 -m pytest tests/test_security_isolation.py -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fake_handler(SV, path="/raw", email="u@x.com"):
    h = SV.Handler.__new__(SV.Handler)          # 소켓 없이 인스턴스만(게이트 로직 단위 검증)
    h.path = path
    h.headers = {"Authorization": "Bearer tok"}
    h._sent = []
    h._send = lambda code, *a, **k: h._sent.append(code)
    return h


class TestTeamFailClosed(unittest.TestCase):
    def setUp(self):
        from prism import serve as SV
        self.SV = SV
        self._orig = (SV._supa, SV.validate_jwt, SV.is_sys_admin_user, SV.team_of, SV.jwt_email)
        SV._supa = lambda: ("http://fake", "key")            # supabase(운영) 모드 강제
        SV.validate_jwt = lambda t: "uid1" if t else ""
        SV.jwt_email = lambda t: "u@x.com"
        SV.is_sys_admin_user = lambda *a, **k: False
        SV.team_of = lambda uid: None                        # 기본: 팀 미소속
        self.addCleanup(self._restore)

    def _restore(self):
        (self.SV._supa, self.SV.validate_jwt, self.SV.is_sys_admin_user,
         self.SV.team_of, self.SV.jwt_email) = self._orig

    def test_teamless_denied_on_data_get(self):
        SV = self.SV
        for path in ("/raw", "/queue", "/dashboard", "/arena", "/model-stats",
                     "/golden-status", "/topics", "/drafts", "/reap", "/events"):
            h = _fake_handler(SV, path=path + "?token=tok")
            self.assertFalse(h._gate_get(), path)            # 팀 없음 → 차단

    def test_team_member_allowed(self):
        SV = self.SV
        SV.team_of = lambda uid: "teamA"
        for path in ("/raw", "/dashboard", "/queue"):
            h = _fake_handler(SV, path=path)
            self.assertTrue(h._gate_get(), path)

    def test_sysadmin_bypasses_team_requirement(self):
        SV = self.SV
        SV.is_sys_admin_user = lambda *a, **k: True           # 운영 관리자(허용목록)
        h = _fake_handler(SV, path="/dashboard")
        self.assertTrue(h._gate_get())

    def test_teamless_ok_routes_open(self):
        SV = self.SV
        for path in ("/admin", "/models", "/vocab", "/dict", "/ingest-status"):
            h = _fake_handler(SV, path=path)
            self.assertTrue(h._gate_get(), path)             # 전역 참조·관리자 판정은 팀 없이 허용

    def test_public_get_open(self):
        SV = self.SV
        for path in ("/", "/config", "/m", "/vendor/app.js", "/template.csv"):
            h = _fake_handler(SV, path=path)
            self.assertTrue(h._gate_get(), path)

    def test_unauthenticated_denied(self):
        SV = self.SV
        SV.validate_jwt = lambda t: ""                       # 토큰 무효
        h = _fake_handler(SV, path="/raw")
        self.assertFalse(h._gate_get())

    def test_require_team_post(self):
        SV = self.SV
        h = _fake_handler(SV, path="/usermeta")
        self.assertFalse(h._require_team())                  # 팀 없음 → False + 403
        self.assertIn(403, h._sent)
        SV.team_of = lambda uid: "teamA"
        h2 = _fake_handler(SV, path="/usermeta")
        self.assertTrue(h2._require_team())

    def test_local_mode_open(self):
        SV = self.SV
        SV._supa = lambda: None                              # sqlite(로컬 단독) → 게이트 없음
        h = _fake_handler(SV, path="/raw")
        self.assertTrue(h._gate_get())
        self.assertTrue(h._require_team())


class TestExportTeamScope(unittest.TestCase):
    """CSV·리포트 빌더가 저장소 조회에 team 을 전달(전 팀 유출 방지)."""

    def test_builders_pass_team(self):
        from prism import serve as SV
        seen = []
        orig = SV.results_rows
        SV.results_rows = lambda limit=5000, team=None: (seen.append(team) or [])
        self.addCleanup(lambda: setattr(SV, "results_rows", orig))
        SV.build_results_csv(team="teamA")
        SV.build_report_html(team="teamB")
        self.assertEqual(seen, ["teamA", "teamB"])           # team=None 전 팀 폴백 아님


if __name__ == "__main__":
    unittest.main()
