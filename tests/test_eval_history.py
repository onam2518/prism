"""평가 종류 간 상세 계약·과거 기록·팀 격리 회귀 검증."""
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from prism import evalops as E, learnops as L, serve
from prism.store import Store


class TestEvalHistory(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.st = Store(str(Path(tmp.name) / 't.db'))
        p = patch.object(serve, 'get_store', return_value=self.st)
        p.start()
        self.addCleanup(p.stop)

    def test_eval_includes_pass_fail_empty_and_ungraded_without_new_calls(self):
        rid = self.st.eval_run_create('', 'historical-model', 'eval', 4)
        rows = [
            {'hash': 'pass', 'expected': {'finalGrade': 'G'}, 'got': {'finalGrade': 'G'}, 'passed': True},
            {'hash': 'fail', 'expected': {'finalGrade': 'G'}, 'got': {'finalGrade': 'R'}, 'passed': False},
            {'hash': 'empty', 'expected': {'finalGrade': 'G', 'summary': '기대'}, 'got': None, 'error': 'empty'},
            {'hash': 'dnm', 'expected': {'intent': ['리뷰']}, 'got': {'intent': ['리뷰']}, 'passed': None},
        ]
        self.st.eval_results_add(rid, rows)
        self.st.eval_run_update(rid, metrics={'n': 4, 'grade_n': 3, 'grade_hit': 1})
        self.st.save_report('eval_prompts_' + str(rid), {'version': 'frozen-engine'})
        with patch.object(serve, 'llm_for_model', side_effect=AssertionError('read must not call models')):
            detail = E.eval_history_detail('eval', rid)
        self.assertEqual(detail['version'], 'frozen-engine')
        self.assertEqual(detail['scope'], 'eval')
        self.assertEqual(detail['models'][0]['model'], 'historical-model')
        self.assertEqual(detail['grade_accuracy'], 0.3333)
        self.assertEqual(E.eval_runs_list()['items'][0]['grade_accuracy'], 0.3333)
        items = {i['hash']: i for i in detail['items']}
        self.assertEqual(len(items), 4)
        self.assertIsNone(items['dnm']['got']['historical-model']['ok'])
        self.assertTrue(items['empty']['got']['historical-model']['empty'])
        self.assertIsNotNone(items['fail']['judgment'])
        self.assertIsNone(items['pass']['judgment'])

    def test_unknown_version_is_not_current_version(self):
        rid = self.st.eval_run_create('', 'old', 'all', 0)
        self.assertIsNone(E.eval_history_detail('eval', rid)['version'])

    def test_missing_expected_is_not_filled_from_model_or_current_golden(self):
        rid = self.st.eval_run_create('', 'model', 'all', 1)
        original = {'finalGrade': 'G', 'reasons': []}
        self.st.eval_results_add(rid, [{'hash': 'missing', 'expected': original,
                                      'got': {'finalGrade': 'G', 'summary': '모델 산출'}}])
        self.st.upsert_golden('missing', {'title': '현재 정답'},
                              {'finalGrade': 'G', 'summary': '나중에 확정한 정답'})
        result = E.eval_history_detail('eval', rid)
        item = result['items'][0]
        self.assertEqual(set(item['expected_status'].values()), {'missing'})
        self.assertNotIn('summary', item['expected'])
        self.assertEqual(item['got']['model']['summary'], '모델 산출')
        self.assertEqual(result['expected_coverage']['fields']['summary'], 0)
        self.assertEqual(self.st.eval_results_list(rid)[0]['expected'], original)

    def test_expected_coverage_uses_scoring_rules_and_keeps_values(self):
        items = [
            {'expected': {'finalGrade': 'G', 'intent': ['리뷰'], 'intent_review': 'needed',
                          'entities': [], 'content_category': [], 'summary': ''}},
            {'expected': {'entities': [], 'meta_status': {'entities': 'no_value'}}},
            {'expected': {'intent': ['리뷰'], 'summary': '사람이 확정한 리드문',
                          'content_category': ['News'], 'meta_status': {'content_category': 'input_hold'}}},
        ]
        result = E._history_expected(items)
        self.assertEqual(result['items'][0]['expected_status'],
                         {'intent': 'pending', 'content_category': 'empty', 'entities': 'empty', 'summary': 'empty'})
        self.assertEqual(result['items'][1]['expected_status']['entities'], 'scored')
        self.assertEqual(result['items'][2]['expected_status']['content_category'], 'excluded')
        self.assertEqual(result['expected_coverage']['fields'],
                         {'intent': 1, 'content_category': 0, 'entities': 1, 'summary': 1})
        self.assertEqual(result['expected_coverage']['total'], 3)
        self.assertEqual(result['items'][0]['expected'], items[0]['expected'])
        self.assertNotIn('expected_status', items[0])

    def test_legacy_compare_does_not_claim_gold_was_missing(self):
        key = 'model_compare_v1234567890'
        self.st.save_report(key, {'ok': True, 'models': [{'model': 'old'}], 'items': [
            {'hash': 'old', 'expected': {'grade': 'G', 'reasons': []}, 'got': {'old': {'grade': 'G'}}}]})
        result = E.eval_history_detail('compare', key=key)
        self.assertEqual(set(result['items'][0]['expected_status'].values()), {'unrecorded'})
        self.assertTrue(result['expected_coverage']['legacy'])

    def test_pilot_round_reads_its_own_report(self):
        rid = self.st.autopilot_create('', 0.9, 3)
        self.st.autopilot_update(rid, history=[{'round': 1, 'version': 4, 'accuracy': 0.8, 'model': 'old', 'reverted': True}])
        item = L._compare_item({'content': {'title': 'original'}, 'expected': {'summary': 'before'}},
                               {'original-model': {'item_meta': {'summary': 'after'}}})
        self.st.save_report(f'pilot_eval_{rid}_1', {'items': [item], 'grade_accuracy': None, 'n': 1})
        self.st.save_report('learn_report', {'eval': {'grade_accuracy': 0.1}})
        detail = E.eval_history_detail('pilot', rid, 1)
        self.assertEqual(detail['status'], 'reverted')
        self.assertEqual(detail['models'][0]['model'], 'original-model')
        self.assertEqual(detail['items'][0]['got']['original-model']['summary'], 'after')
        self.assertEqual(detail['detail_mode'], 'all')
        self.assertFalse(E.eval_history_detail('pilot', rid, 99)['ok'])

    def test_pilot_legacy_metrics_and_partial_details(self):
        rid = self.st.autopilot_create('', 0.9, 3)
        self.st.autopilot_update(rid, history=[{'round': 1, 'accuracy': 0.7, 'version': 3}, {'round': 2, 'accuracy': 0.8}])
        self.st.save_report('learn_report_v3', {'prompt_snapshot': {'version': 3}, 'eval': {
            'basis': {'model': 'old-model'}, 'detail': [{'hash': 'h', 'expected': 'G', 'got': 'R'}]}})
        d = E.eval_history_detail('pilot', rid, 1)
        self.assertEqual(d['detail_mode'], 'mismatches')
        self.assertEqual(d['items'][0]['expected'], {'grade': 'G'})
        d = E.eval_history_detail('pilot', rid, 2)
        self.assertEqual(d['models'][0]['grade_accuracy'], 0.8)
        self.assertEqual(d['detail_mode'], 'none')
        self.assertEqual(d['items'], [])

    def test_compare_requires_exact_key_and_preserves_all_models(self):
        key = 'model_compare_v1234567890'
        models = [{'model': 'A'}, {'model': 'B'}]
        self.st.save_report(key, {'ok': True, 'models': models, 'items': []})
        self.st.save_report('model_compare', {'ok': True, 'models': [{'model': 'latest'}]})
        self.assertEqual(E.eval_history_detail('compare', key=key)['models'], models)
        for bad in ('', 'model_compare', key + 'x', 'model_compare_v9999999999'):
            self.assertFalse(E.eval_history_detail('compare', key=bad)['ok'])
        self.assertFalse(E.eval_history_detail('unknown')['ok'])

    def test_new_snapshots_keep_meta_on_success_and_failure(self):
        expected = {'finalGrade': 'G', 'intent': ['리뷰'], 'entities': [{'name': '프리즘', 'type': 'OG'}],
                    'content_category': [{'tier1': 'News', 'tier2': None}], 'summary': '기대'}
        out = {'item_meta': {k: v for k, v in expected.items() if k != 'finalGrade'}, 'quality_meta': {'finalGrade': 'G'}}
        row = {'content': {'title': 'snapshot'}, 'expected': expected}
        for actual in (out, None):
            stored = E._tally(E._zero_metrics(), row, actual)
            self.assertEqual(stored['expected'], expected)
            if actual:
                self.assertEqual(stored['got']['entities'], expected['entities'])
                self.assertEqual(stored['got']['summary'], '기대')
        item = L._compare_item(row, {'M': out})
        self.assertEqual(item['expected']['content_category'], expected['content_category'])
        self.assertEqual(item['got']['M']['entities'], expected['entities'])

    def test_supabase_pilot_and_eval_detail_are_team_scoped(self):
        from prism.supastore import SupabaseStore
        st = object.__new__(SupabaseStore)
        calls = []
        def get(table, query):
            calls.append((table, query))
            return []
        st._get = get
        with patch.object(serve, 'get_store', return_value=st):
            self.assertFalse(E.eval_history_detail('pilot', 1, 1, team='other')['ok'])
            self.assertFalse(E.eval_history_detail('eval', 1, team='other')['ok'])
        self.assertEqual([t for t, q in calls], ['autopilot_runs', 'eval_runs'])
        self.assertTrue(all('team_id=eq.other' in q and 'id=eq.1' in q for t, q in calls))


class TestEvalFrontend(unittest.TestCase):
    def test_template_nesting_and_tab_panels(self):
        class Parser(HTMLParser):
            def __init__(self):
                super().__init__(); self.stack = []; self.tabs = []; self.panels = []
            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if a.get('role') == 'tab': self.tabs.append(a)
                if a.get('role') == 'tabpanel': self.panels.append(a)
                if tag not in ('input', 'br', 'hr', 'img', 'col', 'wbr'): self.stack.append(tag)
            def handle_endtag(self, tag):
                if not self.stack or self.stack.pop() != tag: raise AssertionError('unbalanced ' + tag)
        p = Parser()
        p.feed((Path(__file__).resolve().parents[1] / 'prism/ui/13-eval.html').read_text())
        self.assertEqual(p.stack, [])
        self.assertEqual(len(p.tabs), 2)
        self.assertEqual({t['aria-controls'] for t in p.tabs}, {p['id'] for p in p.panels})

    @unittest.skipUnless(shutil.which('node'), 'node required')
    def test_actual_client_selection_races_filters_and_live_state(self):
        r = subprocess.run(['node', str(Path(__file__).with_name('test_eval_client.js'))], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
