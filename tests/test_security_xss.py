"""저장형 XSS 회귀 테스트(2026-07-15 감사 · /report DATA 임베드 브레이크아웃).

- dashboard.render / usermeta.render_html: 콘텐츠 제목·본문·검수노트에 '</script>' 가 있어도
  <script> DATA 블록을 조기 종료하지 못한다(graphviz 와 동일한 '</'→'<\\/' 규약).
- 리포트 패널 iframe 은 sandbox(allow-scripts · same-origin 없음)로 격리.
- _send 는 CSP·nosniff·frame-options 등 보안 헤더를 붙인다.

실행: python3 -m pytest tests/test_security_xss.py -q
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_PAYLOAD = '</script><img src=x onerror=alert(document.domain)>'


class TestReportScriptBreakout(unittest.TestCase):
    def _results(self, tmp):
        p = os.path.join(tmp, "results.jsonl")
        row = {"content_ref": {"title": _PAYLOAD, "body": "본문", "displayServiceName": "뉴스"},
               "item_meta": {"summary": "리드", "entities": [_PAYLOAD], "intent": [], "content_category": []},
               "quality_meta": {"finalGrade": "G", "reasons": []}}
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return p

    def test_dashboard_render_neutralizes_breakout(self):
        from prism import dashboard as D
        with tempfile.TemporaryDirectory() as tmp:
            html, _ = D.render(self._results(tmp), title="t")
        self.assertNotIn("</script><img", html)      # 원본 브레이크아웃 시퀀스 소멸
        self.assertIn("<\\/script>", html)            # 이스케이프된 형태로 중화

    def test_usermeta_render_neutralizes_breakout(self):
        from prism import usermeta as U
        data = {"users": [], "personas_def": [], "poison": _PAYLOAD}
        html = U.render_html("", data=data)
        self.assertNotIn("</script><img", html)
        self.assertIn("<\\/script>", html)

    def test_report_iframes_sandboxed(self):
        from prism import dashboard as D
        self.assertIn('sandbox="allow-scripts"', D._INTEGRATED)   # same-origin 미허용 = 토큰 접근 불가


def _headers(port, path):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    with urllib.request.urlopen(req, timeout=20) as r:
        return {k.lower(): v for k, v in r.headers.items()}


class TestSecurityHeaders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["PRISM_BACKEND"] = "sqlite"
        os.environ["PRISM_DB"] = os.path.join(tempfile.mkdtemp(), "hdr.db")
        from http.server import ThreadingHTTPServer
        from prism import config as _cfg
        from prism import serve
        cls._orig_cfg = _cfg.DEFAULT_CONFIG_PATH
        _cfg.DEFAULT_CONFIG_PATH = os.path.join(tempfile.mkdtemp(), "config.json")
        cls._cfg = _cfg
        serve._STORE = None
        serve.Handler.server_mock = True
        cls.serve = serve
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.serve._STORE = None
        cls._cfg.DEFAULT_CONFIG_PATH = cls._orig_cfg

    def test_headers_present_on_page_and_json(self):
        for path in ("/", "/config"):
            h = _headers(self.port, path)
            self.assertIn("content-security-policy", h, path)
            self.assertIn("frame-ancestors 'none'", h["content-security-policy"])
            self.assertIn("connect-src 'self'", h["content-security-policy"])
            self.assertEqual(h.get("x-content-type-options"), "nosniff", path)
            self.assertEqual(h.get("x-frame-options"), "DENY", path)
            self.assertIn("referrer-policy", h)


if __name__ == "__main__":
    unittest.main()
