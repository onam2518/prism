import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prism import serve as S, topicops as T, execution as EX, prompts as P, harness as H
from prism.store import Store
from prism.llm import LLMClient
from prism.config import Config


class TopicApproval(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = Store(str(Path(self.tmp.name) / 'db'))
        self.addCleanup(self.st._conn().close)
        self.p = patch.object(S, 'get_store', return_value=self.st)
        self.p.start(); self.addCleanup(self.p.stop)
        S._agg_bump()
        self.rows = [{'content_ref': {'title': 'A', 'body': 'B'},
                      'quality_meta': {'finalGrade': 'G'},
                      'item_meta': {'entities': ['A']}, '_hash': 'a', '_ts': 1}]
        p = patch.object(S, 'results_rows', side_effect=lambda **kw: copy.deepcopy(self.rows))
        p.start(); self.addCleanup(p.stop)

    def request(self, **kw):
        return dict({'action': 'save', 'expected_revision': (self.st.get_report('topic_studio') or {}).get('revision', 0),
                     'def': {'name': 'A', 'keywords': ['A']}}, **kw)

    def preview(self, request):
        result = T.topic_studio_action({'action': 'preview_action', 'request': request}, mock=True)
        self.assertTrue(result.get('ok'), result)
        return dict(request, preview_token=result['preview_token'])

    def test_approval_binds_candidates_and_condition(self):
        q = self.preview(self.request())
        self.assertIsNone(self.st.get_report('topic_studio'))
        self.rows[0]['item_meta']['entities'] = ['B']
        self.assertTrue(T.topic_studio_action(q, mock=True)['conflict'])
        q = self.preview(self.request())
        q['def']['keywords'] = ['B']
        self.assertTrue(T.topic_studio_action(q, mock=True)['conflict'])

    def test_duplicate_and_stale_undo_are_rejected(self):
        q = self.preview(self.request())
        first = T.topic_studio_action(q, mock=True)
        self.assertTrue(first.get('ok'), first)
        self.assertTrue(T.topic_studio_action(q, mock=True)['conflict'])
        old_undo = {'action': 'undo', 'expected_revision': 1, 'undo_revision': 1}
        undo = self.preview(old_undo)
        second = self.preview(self.request(action='settings', settings={'co_min': 3}))
        self.assertTrue(T.topic_studio_action(second, mock=True)['ok'])
        self.assertTrue(T.topic_studio_action(undo, mock=True)['conflict'])

    def test_atomic_conflict_leaves_state_unchanged(self):
        q = self.preview(self.request())
        with patch.object(self.st, 'compare_report', return_value=False):
            self.assertTrue(T.topic_studio_action(q, mock=True)['conflict'])
        self.assertIsNone(self.st.get_report('topic_studio'))

    def test_reversible_change_has_a_new_revision(self):
        self.assertTrue(T.topic_studio_action(self.preview(self.request()), mock=True)['ok'])
        q = self.preview({'action': 'undo', 'expected_revision': 1, 'undo_revision': 1})
        result = T.topic_studio_action(q, mock=True)
        self.assertEqual(result['revision'], 2)
        self.assertEqual(result['customDefs'], [])

    def test_compare_report_missing_is_not_empty_and_team_isolated(self):
        self.assertFalse(self.st.compare_report('x', {}, {'v': 1}))
        self.assertTrue(self.st.compare_report('x', None, {'v': 1}, team='a'))
        self.assertTrue(self.st.compare_report('x', None, {'v': 2}, team='b'))
        self.assertFalse(self.st.compare_report('x', None, {'v': 3}, team='a'))
        self.assertEqual(self.st.get_report('x', 'a'), {'v': 1})


class FrozenExecution(unittest.TestCase):
    def test_prompt_export_uses_frozen_record_without_adding_quality_to_dnm(self):
        import json
        from prism import learnops
        obj = LLMClient(model='mock', mock=True, config=Config())
        _, plan = EX.capture(obj, [])
        record = EX.prompt_record(plan, 7, 123)
        with patch.object(learnops, 'quality_prompts', side_effect=AssertionError('live quality read')):
            self.assertIs(S._with_quality(record), record)
        files = learnops.prompt_files(record, 'test')
        self.assertEqual(json.loads(files['execution.json']), plan)
        self.assertIn(plan['calls']['intent']['system'], files['04-intent.txt'])

    def test_settings_and_prompts_do_not_follow_live_changes(self):
        rows = [{'content': {'title': '카카오 소식', 'body': '카카오 소식'}}]
        original = LLMClient(model='mock', mock=True, config=Config())
        client, snapshot = EX.capture(original, rows)
        captured = snapshot['calls']['summary']['system']
        original.cfg.retry.max_retries = 19
        original.model = 'other'
        with patch.object(P, 'call_system', side_effect=AssertionError('live prompt read')), \
             patch.object(P, 'quality_system', side_effect=AssertionError('live quality read')), \
             patch.object(P, 'quality_version', side_effect=AssertionError('live version read')):
            out = H.run(rows[0]['content'], client)
        self.assertEqual(client.model, 'mock')
        self.assertEqual(client.execution['routes']['summary'].cfg.retry.max_retries, 2)
        self.assertEqual(snapshot['calls']['summary']['system'], captured)
        self.assertIn('execution_version', out['item_meta']['run_manifest'])
        self.assertNotIn('api_key', str(snapshot))
        self.assertIn(snapshot['quality_version'], out['trace']['prompt_version'])

    def test_fixed_report_does_not_call_live_embedding(self):
        from prism import metaeval
        with patch.object(metaeval, 'summary_sims', side_effect=AssertionError('live scorer read')):
            report = metaeval.meta_report({'sum_pairs': [('동일 문장', '동일 문장')], 'summary_sim_method': 'bigram_f1'})
        self.assertEqual(report['summary_sim'], 1)
        self.assertEqual(report['summary_sim_method'], 'bigram_f1')

    def test_restore_rejects_endpoint_and_engine_change(self):
        obj = LLMClient(model='mock', mock=True, config=Config())
        _, plan = EX.capture(obj, [])
        plan['engine'] = 'old'
        with self.assertRaises(ValueError): EX.restore(plan, lambda _: obj)
        _, plan = EX.capture(obj, [])
        obj.cfg.chat_url = 'https://changed.invalid'
        with self.assertRaises(ValueError): EX.restore(plan, lambda _: obj)

class TopicLanguage(unittest.TestCase):
    def test_merge_preserves_legacy_keyword_and_alias_matching(self):
        from prism.topic_conditions import evaluate, definition_expr
        expr = definition_expr({'keywords': ['삼성', 'A']})
        self.assertTrue(evaluate(expr, {'item_meta': {'entities': ['삼성전자', '에이']}}, {'A': 'key-a', '에이': 'key-a'}))
        self.assertFalse(evaluate(expr, {'item_meta': {'entities': ['삼성전자']}}))

    def test_pairings_remain_grouped(self):
        from prism.topic_conditions import evaluate, definition_expr
        a = {'keywords': ['A'], 'intents': ['경기 결과·리뷰']}
        b = {'keywords': ['B'], 'intents': ['경기 프리뷰']}
        expr = {'any': [definition_expr(a), definition_expr(b)]}
        row = lambda name, intent: {'item_meta': {'entities': [name], 'intent': [intent]}}
        self.assertTrue(evaluate(expr, row('A', '경기 결과·리뷰')))
        self.assertTrue(evaluate(expr, row('B', '경기 프리뷰')))
        self.assertFalse(evaluate(expr, row('A', '경기 프리뷰')))

    def test_unknown_required_source_is_not_dropped(self):
        with patch.object(S, 'results_rows', return_value=[]):
            r = T.topic_studio_action({'action': 'suggest', 'text': '미등록CP의 삼성 기사만'}, mock=True)
        self.assertFalse(r['ok'])
        self.assertTrue(r['needs_confirmation'])

    def test_inactive_window_requires_an_operator_setting(self):
        rows = [{'_ts': 100}]
        self.assertFalse(T._row_stats([0], rows, now=10**8)['inactive'])
        self.assertTrue(T._row_stats([0], rows, now=10**8, settings={'inactive_days': 14})['inactive'])
