"""Focused source contracts for the content and evaluation design surfaces."""

from __future__ import annotations

import os
import unittest


ROOT = os.path.dirname(os.path.dirname(__file__))


def read(relative: str) -> str:
    with open(os.path.join(ROOT, relative), encoding="utf-8") as f:
        return f.read()


class TestDesignAUiContract(unittest.TestCase):
    def test_content_mode_tabs_have_linked_panels(self):
        manage = read("prism/ui/02-content-manage.html")
        run = read("prism/ui/03-run.html")
        self.assertIn('role="tablist" aria-label="콘텐츠 추가 방식"', manage)
        self.assertIn('aria-controls="content-auto-panel"', manage)
        self.assertIn('id="content-auto-panel" role="tabpanel"', manage)
        self.assertIn('id="content-run-panel" role="tabpanel"', run)

    def test_media_result_promotes_registration_and_uses_value_contract(self):
        src = read("prism/ui/03-run.html")
        self.assertEqual(src.count('class="panel" style="margin:0"'), 0)
        self.assertIn("mediaImgRes ? '다시 추출' : '추출 실행'", src)
        self.assertIn("mediaVidRes ? '다시 추출' : '추출 실행'", src)
        self.assertIn("termDef('intent', it)", src)
        self.assertIn("termDef('entity', e)", src)
        self.assertIn("termDef('category', c)", src)

    def test_evaluation_actions_are_exclusive_peer_modes(self):
        src = read("prism/ui/13-eval.html")
        self.assertNotIn("x-data=\"{ evalMode:", src)
        for name in ("run", "pilot", "compare"):
            self.assertIn('id="eval-%s-panel" role="tabpanel"' % name, src)
            self.assertIn("evalMode==='%s'" % name, src)
        self.assertIn('for="eval-scope"', src)
        self.assertIn('for="compare-item-filter"', src)

    def test_owned_destructive_and_single_choice_controls_use_ds_variants(self):
        manage = read("prism/ui/02-content-manage.html")
        dash = read("prism/ui/04-dashboard.html")
        queue = read("prism/ui/18-queue.html")
        self.assertIn("ds-btn--outline ds-btn--c-danger", manage)
        self.assertIn('x-model="bulkScope"', dash)
        self.assertNotIn("srcfilter__chip", dash)
        self.assertIn('for="queue-trigger"', queue)
        self.assertNotIn("queueTrig==='' ? 'sel'", queue)


if __name__ == "__main__":
    unittest.main()
