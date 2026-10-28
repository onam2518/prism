"""Common evaluation UI contract and markup; no provider calls or extra dependencies."""
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class EvaluationFrontendTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'node required')
    def test_common_client_contract_and_state_regressions(self):
        result = subprocess.run(['node', str(ROOT / 'tests/test_evaluation_client.js')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_one_common_start_form_and_no_operational_apply_actions(self):
        class Parser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.forms, self.actions, self.tabs = [], [], []
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'form':
                    self.forms.append(attrs.get('x-on:submit.prevent'))
                if tag == 'button':
                    self.actions.append(attrs.get('x-on:click', ''))
                if attrs.get('role') == 'tab':
                    self.tabs.append(attrs['aria-controls'])
        parser = Parser()
        parser.feed((ROOT / 'prism/ui/13-eval.html').read_text())
        self.assertEqual(parser.forms, ['esStart()', 'esStartCandidate()'])
        self.assertEqual(parser.tabs, ['eval-panel-run', 'eval-panel-results'])
        self.assertFalse(any(action.startswith(('applyRecipe(', 'applyModel(', 'startPilot('))
                             for action in parser.actions))

    def test_new_fragment_is_in_the_shared_loader(self):
        from prism import assets
        parts = assets.parts(assets.JS_BUNDLE)
        self.assertIn('app-25-evaluation.js', parts)
        self.assertLess(parts.index('app-25-evaluation.js'), parts.index('app.js'))

    def test_results_keep_examples_before_explicit_diagnostics_and_candidates(self):
        markup = (ROOT / 'prism/ui/13-eval.html').read_text()
        self.assertLess(markup.index('esVerdict(evalDetail)'), markup.index('esContext(evalDetail)'))
        self.assertLess(markup.index('esContext(evalDetail)'), markup.index('id="eval-items-title"'))
        self.assertLess(markup.index('id="eval-items-title"'), markup.index('id="eval-assessment-title"'))
        self.assertLess(markup.index('id="eval-assessment-title"'), markup.index('id="evaluation-next-title"'))


if __name__ == '__main__':
    unittest.main()
