"""Focused markup contracts for the operations and lab design pass."""

import os
from html.parser import HTMLParser
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ui(name):
    with open(os.path.join(ROOT, "prism", "ui", name), encoding="utf-8") as source:
        return source.read()


class DesignOpsSurfaceTests(unittest.TestCase):
    def test_lab_tabs_expose_selected_panel_relationships(self):
        lab = _ui("06-quality-lab.html")
        for tab in ("user", "mcp", "auto"):
            self.assertIn('id="lab-tab-%s" role="tab"' % tab, lab)
            self.assertIn('aria-controls="lab-panel-%s"' % tab, lab)

    def test_destructive_controls_use_danger_outline(self):
        for name, action in (
            ("10-usermeta.html", "memDelete()"),
            ("12-board.html", "boardDelete(b)"),
            ("16-settings.html", "forgetMetabaseKey()"),
            ("19b-crew.html", "crewOwnerDel(i)"),
            ("19d-mcp-keys.html", "mkRevoke(k)"),
        ):
            line = next(line for line in _ui(name).splitlines() if action in line)
            self.assertIn("ds-btn--outline", line)
            self.assertIn("ds-btn--c-danger", line)

    def test_board_answer_template_keeps_label_and_editor_in_one_root(self):
        class Roots(HTMLParser):
            def __init__(self):
                super().__init__()
                self.depth = 0
                self.roots = []
                self.editor = False
                self.active = False

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "template" and attrs.get("x-if") == "baEdit":
                    self.active = True
                    return
                if self.active:
                    if self.depth == 0:
                        self.roots.append(tag)
                    self.editor |= tag == "textarea" and attrs.get("id") == "board-answer"
                    self.depth += 1

            def handle_endtag(self, tag):
                if self.active:
                    if tag == "template" and self.depth == 0:
                        self.active = False
                    else:
                        self.depth -= 1

        parsed = Roots()
        parsed.feed(_ui("12-board.html"))
        self.assertEqual(len(parsed.roots), 1, "Alpine x-if renders only its first root")
        self.assertTrue(parsed.editor)

    def test_assignment_mode_is_a_single_choice_group(self):
        crew = _ui("19b-crew.html")
        self.assertIn('role="group" aria-label="배정 방식"', crew)
        self.assertIn('x-bind:aria-pressed="bulkMode===\'same\'"', crew)
        self.assertIn('x-bind:aria-pressed="bulkMode===\'distribute\'"', crew)


if __name__ == "__main__":
    unittest.main()
