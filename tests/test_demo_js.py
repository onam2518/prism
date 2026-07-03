"""데모 산출물 회귀: docs/demo.html 인라인 스크립트가 전부 유효한 JS 인지(node --check).

배경: 스텁 스크립트에 SyntaxError 가 생기면 블록 전체가 죽어 데모의 fetch 스텁·로그인
시드가 통째로 무력화되는데, 문자열 grep 검증으로는 잡히지 않는다(실제 사고 사례).
node 가 없으면 skip.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDemoInlineScripts(unittest.TestCase):
    def test_all_inline_scripts_parse(self):
        if not shutil.which("node"):
            self.skipTest("node 미설치")
        demo = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "demo.html")
        if not os.path.exists(demo):
            self.skipTest("docs/demo.html 없음")
        s = open(demo, encoding="utf-8").read()
        blocks = re.findall(r"<script>(.*?)</script>", s, re.S)
        self.assertGreaterEqual(len(blocks), 3)
        for i, b in enumerate(blocks):
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
                f.write(b)
                path = f.name
            try:
                r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"block {i}: {r.stderr[:300]}")
            finally:
                os.unlink(path)


class TestDemoStubCoverage(unittest.TestCase):
    """데모에 노출되는 버튼이 쓰는 라우트는 반드시 스텁이 있어야 한다(무동작 회귀 방지)."""
    REQUIRED = ["/config", "/dashboard", "/arena", "/raw", "/drafts", "/model-stats",
                "/golden-status", "/golden-list", "/learn-report", "/learn-batch", "/learn-data",
                "/learn-export", "/learn-spec", "/eval-golden", "/eval-judge", "/compare-models",
                "/purpose", "/prompt-preview", "/feedback", "/admin", "/rerun"]

    def test_required_routes_stubbed(self):
        demo = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "demo.html")
        if not os.path.exists(demo):
            self.skipTest("docs/demo.html 없음")
        html = open(demo, encoding="utf-8").read()
        stubs = set(re.findall(r"u\.indexOf\('(/[a-zA-Z0-9\-_]+)'\)", html))
        missing = [r for r in self.REQUIRED
                   if not any(r.startswith(st) or st.startswith(r) for st in stubs)]
        self.assertEqual(missing, [], f"데모 스텁 누락: {missing}")


if __name__ == "__main__":
    unittest.main()
