"""전송 성능 회귀: gzip 협상 · 벤더 캐시 헤더 · 팀 조회 캐시.

배경(2026-07-08 실측): 첫 로드 4.7MB(압축·캐시 0) · API 400~860ms(요청마다 새 TLS +
인증 요청마다 팀 조회 1콜). 수정 = _send gzip 협상 · 벤더 ?v= 불변 캐시/폰트 30일 ·
supastore keep-alive · team_of 60s 캐시.

실행: python3 -m pytest tests/test_perf_transport.py -q
"""
import gzip
import os
import sys
import tempfile
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["PRISM_BACKEND"] = "sqlite"
        os.environ["PRISM_DB"] = os.path.join(tempfile.mkdtemp(), "perf.db")
        from http.server import ThreadingHTTPServer
        from prism import config as _cfg
        from prism import serve
        cls._orig_cfg_path = _cfg.DEFAULT_CONFIG_PATH
        _cfg.DEFAULT_CONFIG_PATH = os.path.join(tempfile.mkdtemp(), "config.json")
        cls._cfg_mod = _cfg
        serve._STORE = None
        serve.Handler.server_mock = True
        cls.serve = serve
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.serve._STORE = None
        cls._cfg_mod.DEFAULT_CONFIG_PATH = cls._orig_cfg_path

    def _get(self, path, accept_gzip=True):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            headers={"Accept-Encoding": "gzip"} if accept_gzip else {})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, dict(r.headers), r.read()

    def test_gzip_negotiation_on_page(self):
        st, h, body = self._get("/")
        self.assertEqual(h.get("Content-Encoding"), "gzip")
        html = gzip.decompress(body).decode("utf-8")
        self.assertIn("<title>Prism</title>", html)                        # 압축 왕복 무손실
        st2, h2, body2 = self._get("/", accept_gzip=False)  # 미수용 클라이언트는 원문
        self.assertIsNone(h2.get("Content-Encoding"))
        self.assertIn("<title>Prism</title>", body2.decode("utf-8"))
        self.assertLess(len(body), len(body2) // 2)         # 절반 이하로 줄어야 의미

    def test_vendor_cache_headers(self):
        _, h, _ = self._get("/vendor/app.js?v=test123")
        self.assertIn("immutable", h.get("Cache-Control", ""))     # 버스터 = 불변 1년
        _, h2, _ = self._get("/vendor/app.js")
        self.assertIn("max-age=3600", h2.get("Cache-Control", ""))  # 버스터 없음 = 1시간
        _, h3, _ = self._get("/vendor/PretendardVariable.woff2")
        self.assertIn("max-age=2592000", h3.get("Cache-Control", ""))  # 폰트 30일
        self.assertIsNone(h3.get("Content-Encoding"))               # 폰트는 재압축 안 함

    def test_html_stays_no_store(self):
        _, h, _ = self._get("/")
        self.assertIn("no-store", h.get("Cache-Control", ""))       # 페이지 자체는 항상 재요청(버전 감지)

    def test_team_of_cache(self):
        SV = self.serve
        calls = []

        class FakeStore:
            def reviewer_team(self, uid):
                calls.append(uid)
                return "team-1"

        orig = SV.get_store
        SV.get_store = lambda: FakeStore()
        self.addCleanup(lambda: setattr(SV, "get_store", orig))
        SV._TEAM_CACHE.clear()
        self.assertEqual(SV.team_of("u1"), "team-1")
        self.assertEqual(SV.team_of("u1"), "team-1")
        self.assertEqual(calls, ["u1"])                     # 60s 내 재조회 없음
        SV._TEAM_CACHE.pop("u1", None)                      # 무효화(가입·팀 변경 경로) 후 재조회
        self.assertEqual(SV.team_of("u1"), "team-1")
        self.assertEqual(calls, ["u1", "u1"])
        self.assertIsNone(SV.team_of(""))                   # uid 없음 = None(스토어 미호출)


if __name__ == "__main__":
    unittest.main()
