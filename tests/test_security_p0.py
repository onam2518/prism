"""P0 보안 회귀 테스트(2026-07-15 감사 대응).

- _validate_public_url: 인입 SSRF 방어(스킴 화이트리스트 + 사설/내부 대역 차단)
- _safe_url: 저장 원문 링크 정제(javascript:·data: 차단 → 원문 iframe XSS 방어)
- graphviz.section: 노드 라벨(LLM 추출 개체명)의 </script> 탈출 방어

실행: python3 -m pytest tests/test_security_p0.py -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestFetchUrlSsrf(unittest.TestCase):
    """_validate_public_url: file://·내부망·메타데이터 주소 거부, 공인 주소 허용."""

    def test_rejects_non_http_schemes(self):
        from prism import serve as SV
        for u in ("file:///etc/passwd", "ftp://host/x", "gopher://h/", "javascript:1", ""):
            self.assertIsNotNone(SV._validate_public_url(u), u)

    def test_rejects_internal_addresses(self):
        from prism import serve as SV
        # 리터럴 IP 라 getaddrinfo 가 네트워크 없이 그대로 반환 → 결정론적
        for u in ("http://127.0.0.1/x", "http://169.254.169.254/latest/meta-data/",
                  "http://10.1.2.3/", "http://192.168.0.1/", "http://172.16.5.9/",
                  "http://[::1]/", "http://0.0.0.0/"):
            self.assertIsNotNone(SV._validate_public_url(u), u)

    def test_allows_public_literal_ip(self):
        from prism import serve as SV
        # 공인 리터럴 IP(93.184.216.34 = 과거 example.com) → 통과(None)
        self.assertIsNone(SV._validate_public_url("http://93.184.216.34/records"))
        self.assertIsNone(SV._validate_public_url("https://93.184.216.34:8443/records?limit=10"))


class TestSafeUrl(unittest.TestCase):
    """_safe_url: 저장 링크는 http/https 만 통과, 스크립트 스킴은 빈 문자열."""

    def test_blanks_dangerous_schemes(self):
        from prism import serve as SV
        for u in ("javascript:alert(1)", "data:text/html,<script>1</script>",
                  "vbscript:msgbox", "  javascript:alert(1)  ", "//evil.example/x", "ftp://h/x", ""):
            self.assertEqual(SV._safe_url(u), "", u)

    def test_keeps_http_https(self):
        from prism import serve as SV
        self.assertEqual(SV._safe_url("https://news.example/a"), "https://news.example/a")
        self.assertEqual(SV._safe_url("  http://x.example/y  "), "http://x.example/y")


class TestGraphvizLabelEscape(unittest.TestCase):
    """section(): 악성 노드 라벨이 <script> 블록을 조기 종료하지 못한다."""

    def test_label_cannot_break_out_of_script(self):
        from prism import graphviz as GV
        payload = '</script><img src=x onerror=alert(document.cookie)>'
        html = GV.section("g1", [{"id": "n", "label": payload, "kind": "a", "val": 5}],
                          [], {"a": "#000"}, "제목", hint="힌트")
        # 원본 브레이크아웃 시퀀스가 그대로 남아선 안 됨
        self.assertNotIn("</script><img", html)
        # 이스케이프된 형태(<\/)로 중화되어야 함
        self.assertIn("<\\/script>", html)


if __name__ == "__main__":
    unittest.main()
