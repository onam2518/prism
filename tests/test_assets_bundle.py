"""벤더 자산 번들 · 조각을 이어붙여 단일 URL 로 내는 규약(2026-08-03).

첫 로드에 vendor JS 15개 + CSS 6개가 각각 왕복하던 것을 번들 2개로 줄였다.
깨지면 화면이 통째로 안 뜨는 자리라 순서·누락·경계 개행을 여기서 잠근다.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import assets


class TestBundleComposition(unittest.TestCase):
    def test_js_loader_runs_last(self):
        """로더(app.js)는 조각들이 PRISM_APP_PARTS 등록을 마친 뒤 실행돼야 한다."""
        parts = assets.parts(assets.JS_BUNDLE)
        self.assertEqual(parts[-1], "app.js")
        self.assertTrue(all(p.startswith("app-") for p in parts[:-1]))

    def test_js_fragments_in_filename_order(self):
        """조각 병합은 파일명 순이 계약이다(vendor/app.js 주석과 같은 규약)."""
        parts = assets.parts(assets.JS_BUNDLE)[:-1]
        self.assertEqual(parts, sorted(parts))

    def test_js_bundle_includes_every_fragment(self):
        """조각을 새로 추가했는데 번들에서 빠지면 그 기능만 조용히 죽는다."""
        on_disk = {f for f in os.listdir(assets.VENDOR_DIR)
                   if f.startswith("app-") and f.endswith(".js") and not f.startswith("app-bundle")}
        self.assertEqual(set(assets.parts(assets.JS_BUNDLE)[:-1]), on_disk)

    def test_css_order_puts_tw_last(self):
        """tw.css 는 preflight(리셋)라 마지막이어야 앞 규칙을 덮지 않는다."""
        parts = assets.parts(assets.CSS_BUNDLE)
        self.assertEqual(parts[-1], "tw.css")
        self.assertIn("app.css", parts)
        self.assertLess(parts.index("app.css"), parts.index("tw.css"))

    def test_boundaries_get_a_newline(self):
        """조각 끝이 `// 주석`이면 개행 없이 붙을 때 다음 조각 첫 줄이 주석에 먹힌다."""
        for name in (assets.JS_BUNDLE, assets.CSS_BUNDLE):
            raw = assets.build(name).decode("utf-8")
            for part in assets.parts(name):
                with open(os.path.join(assets.VENDOR_DIR, part), encoding="utf-8") as fh:
                    body = fh.read()
                self.assertIn(body.rstrip("\n") + "\n", raw, "%s 경계 개행 누락" % part)

    def test_unknown_name_is_not_a_bundle(self):
        self.assertIsNone(assets.parts("app.css"))
        self.assertIsNone(assets.parts("../secret"))

    def test_mtime_tracks_newest_fragment(self):
        newest = max(os.path.getmtime(os.path.join(assets.VENDOR_DIR, p))
                     for p in assets.parts(assets.JS_BUNDLE))
        self.assertEqual(assets.mtime(assets.JS_BUNDLE), newest)


class TestPageUsesBundle(unittest.TestCase):
    def test_page_links_only_the_bundles(self):
        """조각을 개별 <script>/<link> 로 다시 늘어놓으면 왕복 수가 원상복구된다."""
        from prism import page
        links = sorted(set(re.findall(r"/vendor/[\w.\-]+\.(?:js|css)", page.PAGE)))
        self.assertEqual(links, ["/vendor/alpine.js", "/vendor/app-bundle.css", "/vendor/app-bundle.js"])

    def test_alpine_loads_after_the_bundle(self):
        """alpine:init 은 조각 등록·로더 실행이 끝난 뒤에 떠야 한다."""
        from prism import page
        self.assertLess(page.PAGE.index("/vendor/app-bundle.js"), page.PAGE.index("/vendor/alpine.js"))


if __name__ == "__main__":
    unittest.main()
