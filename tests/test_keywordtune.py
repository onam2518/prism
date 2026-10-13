import copy
import unittest
from unittest.mock import patch

from prism import keywordlab as K, keywordtune as T, entrefine as E
from prism.supastore import SupabaseStore
from tests import test_keywordlab as base


class KeywordCompletionTest(unittest.TestCase):
    setUp = base.KeywordLabTest.setUp
    version = base.KeywordLabTest.version
    run_case = base.KeywordLabTest.run_case

    def completion(self, partition='development'):
        run = self.run_case(1)
        cell = next(iter(run['cells'].values()))
        return run, {'action': 'complete_review', 'run_id': run['id'], 'cell_id': cell['id'],
                     'expected_revision': 0, 'request_id': K._id(), 'expected_gold_id': '',
                     'judgments': [{'verdict': 'accept'} for _ in K._candidates(cell)],
                     'additions': [], 'note': '검수 메모', 'no_keywords': not K._candidates(cell), 'partition': partition}

    def test_complete_retry_and_unchanged_draft_preserve_final_and_gold(self):
        run, body = self.completion('evaluation')
        first = K.complete_review(body, 'a', 'reviewer', True)
        self.assertEqual(first['gold']['partition'], 'evaluation')
        self.assertEqual(first['gold']['review_id'], first['cell']['final']['id'])
        self.assertEqual(first, K.complete_review(body, 'a', 'reviewer', True))
        ack = K.review(dict(body, expected_revision=1), 'a', 'reviewer', True)
        self.assertEqual(ack['cell']['final']['id'], first['cell']['final']['id'])
        self.assertEqual(ack['cell']['review_revision'], 1)
        self.assertEqual(len(K.catalog('a')['gold']), 1)
        self.assertFalse(K.action(body, 'b', 'reviewer', can_final=True)['ok'])
        self.assertFalse(K.action(body, 'a', 'reviewer', can_final=False)['ok'])
        self.assertFalse(K.action(dict(body, note='다른 입력'), 'a', 'reviewer', can_final=True)['ok'])

    def test_failed_or_stale_gold_never_leaves_partial_finalization(self):
        run, body = self.completion()
        key = K._key('run', run['id'])
        before = K._get(key, 'a')
        with patch.object(self.store, 'compare_report', side_effect=RuntimeError('database unavailable')):
            self.assertFalse(K.action(body, 'a', 'x', can_final=True)['ok'])
        self.assertEqual(K._get(key, 'a'), before)
        self.assertEqual(K.catalog('a')['gold'], [])
        with self.assertRaisesRegex(ValueError, '기존 정답'):
            K.complete_review(dict(body, expected_gold_id='stale'), 'a', 'x', True)
        self.assertEqual(K._get(key, 'a'), before)
        first = K.complete_review(body, 'a', 'x', True)
        self.assertEqual(first['cell']['review_revision'], 1)

    def test_exposed_content_cannot_become_evaluation_and_cancel_invalidates(self):
        run, body = self.completion()
        first = K.complete_review(body, 'a', 'x', True)
        with patch.object(K, '_engine', return_value=(None, True, '')):
            K.compile_proposal({'version_id': run['slots'][0]['version']['id'], 'compiler_model': 'solar-pro3',
                                'gold_keys': [first['gold']['item_key']]}, 'a', 'x')
        changed = dict(body, partition='evaluation', expected_revision=1, request_id=K._id(), expected_gold_id=first['gold']['id'])
        with self.assertRaisesRegex(ValueError, '개선에 사용'):
            K.complete_review(changed, 'a', 'x', True)
        self.assertEqual(K._get(K._key('run', run['id']), 'a')['cells'][body['cell_id']]['final']['id'], first['cell']['final']['id'])
        K.cancel_final(dict(body, expected_revision=1, final_id=first['cell']['final']['id']), 'a', 'x', True)
        self.assertEqual(K.catalog('a')['gold'], [])
        self.assertFalse(K.action(body, 'a', 'x', can_final=True)['ok'])
        saved = K.complete_review(dict(body, expected_revision=2, request_id=K._id()), 'a', 'x', True)
        self.assertNotEqual(saved['gold']['id'], first['gold']['id'])
        self.assertEqual(saved['cell']['final']['note'], '검수 메모')

    def test_paired_store_rollback_conflict_and_supabase_rpc_contract(self):
        self.store.save_report('first', {'v': 0}, 'a')
        self.store.save_report('second', {'v': 0}, 'a')
        self.assertFalse(self.store.compare_report('first', {'v': 0}, {'v': 1}, team='a', guard=('second', {'v': 9}), guard_payload={'v': 1}))
        with self.assertRaises(TypeError):
            self.store.compare_report('first', {'v': 0}, {'v': 1}, team='a', guard=('second', {'v': 0}), guard_payload={'bad': object()})
        self.assertEqual(self.store.get_report('first', 'a'), {'v': 0})
        self.assertEqual(self.store.get_report('second', 'a'), {'v': 0})
        self.assertTrue(self.store.compare_report('first', {'v': 0}, {'v': 1}, team='a', guard=('second', {'v': 0}), guard_payload={'v': 1}))
        store = object.__new__(SupabaseStore)
        with patch.object(store, '_req', return_value=True) as request:
            self.assertTrue(store.compare_report('first', {}, {'v': 1}, team='team-a', guard=('second', {}), guard_payload={'v': 2}))
            self.assertEqual(request.call_args[0], ('POST', 'rpc/prism_compare_report_pair'))
            self.assertEqual(request.call_args[1]['body']['p_team_key'], 'team-a')
            self.assertEqual(request.call_args[1]['body']['p_guard_payload'], {'v': 2})


class KeywordTuningTest(unittest.TestCase):
    version = KeywordCompletionTest.version
    run_case = KeywordCompletionTest.run_case
    completion = KeywordCompletionTest.completion

    def setUp(self):
        base.KeywordLabTest.setUp(self)
        self.addCleanup(T.ACTIVE.clear)

    def dataset(self):
        rows = []
        for name, partition in [('개선 전용', 'development'), ('평가 전용', 'evaluation')]:
            self.meta = dict(self.meta, summary=name)
            run, body = self.completion(partition)
            body['judgments'] = [{'verdict': 'edit', 'corrected': '정답', 'reason': '표현 문제'}]
            rows.append(K.complete_review(body, 'a', 'x', True)['gold'])
        return run['slots'][0]['version']['id'], rows

    def start_tuning(self, version, rows, rounds=3):
        body = {'action': 'tune_start', 'version_id': version, 'compiler_model': 'solar-pro3',
                'gold_keys': [rows[0]['item_key']], 'evaluation_keys': [rows[1]['item_key']], 'max_rounds': rounds}
        with patch.object(K, '_engine', return_value=(None, True, '')), patch.object(T.threading, 'Thread'):
            result = K.action(body, 'a', 'x', can_manage=True)
        self.assertTrue(result['ok'], result)
        return K._get(T.STATE, 'a')

    @staticmethod
    def generated(*args):
        text = '정답' if '# 검수 보완' in args[4] else '오답'
        return {'refined': {'keywords': [{'text': text, 'kind': 'single'}]}, 'tokens': {'in': 1, 'out': 1, 'cost_usd': 0}}

    def test_tunes_to_exact_match_without_adoption_or_evaluation_leak(self):
        version, rows = self.dataset()
        run = self.start_tuning(version, rows)
        gold_before = copy.deepcopy(K._catalog('a')['gold'])
        with patch.object(K, '_engine', return_value=(None, True, '')), patch.object(E, 'refine', side_effect=self.generated):
            T._loop(run, 'a')
        state = T.status('a')
        self.assertEqual(state['status'], 'done', state)
        self.assertEqual([h['matched'] for h in state['history']], [0, 1])
        self.assertEqual(state['best']['total'], 1)
        self.assertEqual(K._catalog('a')['active'], {})
        self.assertEqual(K._catalog('a')['gold'], gold_before)
        candidate = K._version(state['best']['version_id'], 'a')
        proposal = K._get(K._key('proposal', candidate['proposal_id']), 'a')
        self.assertIn('키워드 순서는 평가하지 않는다', proposal['system'])
        self.assertIn('쿡북 설명을 rules에 옮기지 않는다', proposal['system'])
        self.assertEqual(proposal['input']['reviews'][0]['current_output'][0]['text'], '오답')
        self.assertNotIn('평가 전용', str(proposal['input']))
        self.assertIsNone(T.status('b'))
        self.assertFalse(K.action({'action': 'adopt', 'run_id': state['best']['run_id'], 'version_id': candidate['id']}, 'a', 'x', can_manage=True)['ok'])

    def test_exact_scoring_counts_extra_missing_case_empty_and_failed_results(self):
        version, rows = self.dataset()
        gold = rows[1]
        source = K._get(K._key('run', gold['run_id']), 'a')
        item = source['items'][0]
        source['gold_snapshot'] = {item['key']: dict(gold, keywords=[{'text': '서울'}, {'text': '부산'}])}
        cell = next(iter(source['cells'].values()))
        for actual, status, expected in [(['부산', '서울'], 'done', 1), (['서울'], 'done', 0), (['서울', '부산', '대구'], 'done', 0), ([], 'failed', 0), (['서울', '서울'], 'done', 0)]:
            cell['refined']['keywords'] = [{'text': text} for text in actual]
            cell['status'] = status
            metrics = K._metrics(source)[0]
            self.assertEqual((metrics['exact_matches'], metrics['exact_total']), (expected, 1))
        self.assertFalse(K._exact_keywords([{'text': 'ai'}], [{'text': 'AI'}]))
        source['gold_snapshot'][item['key']]['keywords'] = []
        cell['status'] = 'done'; cell['refined']['keywords'] = []
        self.assertEqual(K._metrics(source)[0]['exact_matches'], 1)

    def test_stall_stop_and_canceled_gold_preserve_candidate_history(self):
        version, rows = self.dataset()
        run = self.start_tuning(version, rows, 5)
        with patch.object(K, '_engine', return_value=(None, True, '')):
            T._loop(run, 'a')
        state = T.status('a')
        self.assertEqual(state['status'], 'done', state)
        self.assertEqual(state['round'], 2)
        self.assertEqual(state['best']['version_id'], version)
        run = self.start_tuning(version, rows)
        T.stop('a')
        T._loop(run, 'a')
        self.assertEqual(T.status('a')['status'], 'stopped')
        run = self.start_tuning(version, rows)
        gold = rows[1]
        K.cancel_final({'run_id': gold['run_id'], 'cell_id': gold['cell_id'], 'expected_revision': 1, 'final_id': gold['review_id']}, 'a', 'x', True)
        T._loop(run, 'a')
        self.assertEqual(T.status('a')['status'], 'stopped')
        self.assertIn('확정 취소', T.status('a')['reason'])

    def test_permissions_partition_validation_restart_and_missing_comparison(self):
        version, rows = self.dataset()
        body = {'action': 'tune_start', 'version_id': version, 'compiler_model': 'solar-pro3',
                'gold_keys': [rows[0]['item_key']], 'evaluation_keys': [rows[0]['item_key']], 'max_rounds': 1}
        self.assertFalse(K.action(body, 'a', 'x', can_manage=False)['ok'])
        self.assertFalse(K.action(body, 'a', 'x', can_manage=True)['ok'])
        run = self.start_tuning(version, rows)
        with self.assertRaisesRegex(ValueError, '이미'):
            with patch.object(K, '_engine', return_value=(None, True, '')):
                T.start(dict(body, evaluation_keys=[rows[1]['item_key']]), 'a', 'x')
        T.ACTIVE.discard(('a', run['id']))
        self.assertEqual(T.status('a')['status'], 'stopped')
        self.assertIn('서버 재시작', T.status('a')['reason'])
        with self.assertRaisesRegex(ValueError, '재실행 결과가 없습니다'):
            K.compile_proposal(dict(body, comparison_run=K._id()), 'a', 'x')
