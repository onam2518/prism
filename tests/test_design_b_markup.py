"""Design-policy regression checks for the studio, dictionary, and learning surfaces."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def markup(name):
    return (ROOT / "prism" / "ui" / name).read_text(encoding="utf-8")


class DesignBMarkupTest(unittest.TestCase):
    def test_tab_panels_have_stable_accessibility_links(self):
        cases = {
            "07-studio.html": ("studio-topic-tab", "studio-topic-panel", "topic-manual-panel"),
            "08-dict.html": ("dict-intent-tab", "dict-engine-panel", 'role="tablist"'),
            "09-entdict.html": ("dict-entity-panel", "dict-entity-tab", 'role="tabpanel"'),
            "14-golden.html": ("test-status-tab", "test-data-panel", "review-raw-panel"),
            "19-prompt-studio.html": ("prompt-builder-tab", "prompt-library-panel", "studio-prompt-panel"),
        }
        for filename, markers in cases.items():
            source = markup(filename)
            for marker in markers:
                self.assertIn(marker, source, f"{filename} is missing {marker}")
            if filename != "09-entdict.html":
                self.assertIn('role="tab"', source)
                self.assertIn('x-bind:aria-selected=', source)

    def test_owned_destructive_actions_use_the_danger_outline(self):
        cases = {
            "07-studio.html": "studioDelete(g)",
            "09-entdict.html": "entDelete(e)",
            "14-golden.html": "removeGolden(g.hash)",
            "19-prompt-studio.html": "removeDeploy(d)",
        }
        for filename, action in cases.items():
            source = markup(filename)
            action_start = source.index(action)
            button_start = source.rfind("<button", 0, action_start)
            self.assertIn("ds-btn--outline ds-btn--c-danger", source[button_start:action_start])

    def test_golden_dialog_and_primary_progression_contract(self):
        source = markup("14-golden.html")
        self.assertIn('role="dialog" aria-modal="true" aria-labelledby="directive-modal-title"', source)
        self.assertGreaterEqual(source.count("data-dialog-close"), 2)
        self.assertIn("schedEditing ? 'ds-btn--secondary ds-btn--s-sm' : 'ds-btn--primary'", source)
        self.assertIn("x-show=\"schedEditing\" x-on:click=\"saveLearnSched()\"", source)
        self.assertIn('id="review-raw-panel" role="tabpanel" aria-labelledby="review-raw-tab"', source)

    def test_legacy_dictionary_engine_remains_reachable(self):
        source = markup("08-dict.html")
        self.assertIn('id="dict-engine-tab" role="tab" aria-controls="dict-engine-panel"', source)
        self.assertIn('id="dict-engine-panel" role="tabpanel" aria-labelledby="dict-engine-tab"', source)


if __name__ == "__main__":
    unittest.main()
