"""무인증 GET 게이트: supabase(운영) 모드에서 데이터 GET 은 로그인 필수(401),
페이지 셸·정적 자산·/config(최소 필드)는 공개 유지. 목록 응답의 검수 집계(_fb_public)는
검수자 식별자(verdicts 원본)를 싣지 않는다. (2026-07-10 무인증 전 팀 데이터 노출 차단)

실행: python3 -m pytest tests/test_get_gate.py -q
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestPublicGetPolicy(unittest.TestCase):
    """공개/비공개 GET 경로 분류(순수 함수)."""

    def test_public_paths(self):
        from prism import serve as SV
        for p in ("/", "/m", "/m/", "/vendor/app.js", "/config", "/config?x=1",
                  "/template.csv", "/template.xlsx", "/usermeta-template.csv"):
            self.assertTrue(SV.is_public_get(p), p)

    def test_data_paths_not_public(self):
        from prism import serve as SV
        for p in ("/dashboard", "/raw?limit=2", "/drill?kind=intent&value=x",
                  "/topic-drill?cluster=1", "/export.csv", "/report", "/events",
                  "/arena", "/topics", "/dict", "/vocab", "/board", "/queue",
                  "/models", "/ingest-status", "/prompt-defaults", "/golden-status"):
            self.assertFalse(SV.is_public_get(p), p)


class TestFbPublic(unittest.TestCase):
    """목록 응답용 검수 집계 축약: 개별 표(verdicts)·검수자 식별자 제외, 집계만 유지."""

    def test_strips_reviewer_identities(self):
        from prism import serve as SV
        fb = {"verdicts": [{"reviewer": "이름", "reviewer_id": "uid-1", "verdict": "good",
                            "stage": "analyze", "note": "메모", "ts": 100.0, "element": ""}],
              "good": 1, "bad": 0, "n": 1, "consensus": "good", "agree": True,
              "verdict": "good", "stage": "analyze", "note": "메모"}
        out = SV._fb_public(fb)
        self.assertNotIn("verdicts", out)
        self.assertNotIn("uid-1", json.dumps(out, ensure_ascii=False))
        self.assertEqual(out["verdict"], "good")
        self.assertEqual(out["n"], 1)
        self.assertEqual(out["good"], 1)
        self.assertEqual(out["ts"], 100.0)
        self.assertEqual(out["note"], "메모")

    def test_empty(self):
        """빈 피드백 = 0 값 딕셔너리(/raw 의 fb.n==0 계약 유지)."""
        from prism import serve as SV
        out = SV._fb_public({})
        self.assertEqual(out["n"], 0)
        self.assertEqual(out["verdict"], "")
        self.assertNotIn("mine", out)
        self.assertEqual(SV._fb_public({}, reviewer="uid-me")["mine"], "")

    def test_mine_by_reviewer(self):
        """reviewer 식별 시 '내 표(mine)'·내 교정(note·elems)만 실린다(내 표 기준 '완료' 원천)."""
        from prism import serve as SV
        fb = {"verdicts": [
                  {"reviewer": "다른이", "reviewer_id": "uid-other", "verdict": "good",
                   "stage": "analyze", "note": "남의 메모", "ts": 50.0, "element": ""},
                  {"reviewer": "나", "reviewer_id": "uid-me", "verdict": "bad",
                   "stage": "review", "note": "내 메모", "ts": 100.0, "element": "summary,intent"}],
              "good": 1, "bad": 1, "n": 2, "consensus": "split", "agree": False,
              "verdict": "split", "stage": "review", "note": "내 메모"}
        out = SV._fb_public(fb, reviewer="uid-me")
        self.assertEqual(out["mine"], "bad")
        self.assertEqual(out["note"], "내 메모")
        self.assertEqual(out["elems"], ["summary", "intent"])
        self.assertEqual(out["verdict"], "split")     # 팀 합의는 별도 유지
        no_match = SV._fb_public(fb, reviewer="uid-없음")
        self.assertEqual(no_match["mine"], "")        # 식별됐지만 내 표 없음 = 미검수
        self.assertEqual(no_match["note"], "")        # 남의 교정 프리필 방지
        anon = SV._fb_public(fb)
        self.assertNotIn("mine", anon)                # 미식별 = mine 없음(클라 myVerdict 합의 폴백)


class TestGateSupabaseMode(unittest.TestCase):
    """서버를 스레드로 띄우고 serve._supa 를 패치해 운영 모드 게이트를 검증.
    실 supabase 불필요: 무토큰 요청은 validate_jwt(빈 토큰 = None) 만 지나 401."""

    @classmethod
    def setUpClass(cls):
        os.environ["PRISM_BACKEND"] = "sqlite"
        os.environ["PRISM_DB"] = os.path.join(tempfile.mkdtemp(), "gate.db")
        from http.server import ThreadingHTTPServer
        from prism import serve as SV
        cls.SV = SV
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), SV.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls._orig_supa = SV._supa
        SV._supa = lambda: ("http://supabase.local", "test-key")   # 운영 모드 흉내(게이트 판단 전용)

    @classmethod
    def tearDownClass(cls):
        cls.SV._supa = cls._orig_supa
        cls.httpd.shutdown()

    def _get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_data_get_requires_login(self):
        for p in ("/dashboard", "/raw?limit=1", "/drill?kind=intent&value=x",
                  "/topic-drill?cluster=1", "/export.csv", "/report", "/events",
                  "/dict", "/vocab", "/topics", "/arena", "/models"):
            code, _ = self._get(p)
            self.assertEqual(code, 401, p)

    def test_public_still_open(self):
        for p in ("/", "/m", "/vendor/app.js", "/template.csv"):
            code, _ = self._get(p)
            self.assertEqual(code, 200, p)

    def test_config_minimal_without_login(self):
        code, body = self._get("/config")
        self.assertEqual(code, 200)
        cfg = json.loads(body)
        self.assertIn("backend", cfg)                 # 배포 검증(curl /config) 계약 유지
        self.assertIn("configured", cfg)
        for k in ("systemPrompt", "stagePrompts", "modelPrompts", "familyWrappers",
                  "metaContract", "guideUrls", "availableModels"):
            self.assertNotIn(k, cfg, k)               # 프롬프트 계약·내부 URL 은 로그인 후에만


if __name__ == "__main__":
    unittest.main()
