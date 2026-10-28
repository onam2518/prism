import copy
import threading
import time
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from prism import evalcandidate as candidate, execution
from prism.store import golden_hash


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.reports = {}
        self.rows = [{'content': {'title': str(i)}, 'expected': {'summary': 'gold' + str(i)}} for i in range(5)]
        self.snapshot = {'rows': copy.deepcopy(self.rows), 'execution': {'single_system': 'original', 'calls': {}, 'quality': {}}}
        self.run = {'status': 'done', 'metrics': {'basis_fingerprint': execution.digest(self.snapshot)}}
        self.reports[('team', 'eval_snapshot_1')] = self.snapshot
        self.operating_prompt = {'prompt': 'original'}
        self.gold = copy.deepcopy(self.rows)

    def eval_run_get(self, run_id, team):
        return copy.deepcopy(self.run) if run_id == 1 and team == 'team' else None

    def get_report(self, kind, team=None):
        with self.lock:
            return copy.deepcopy(self.reports.get((team, kind)))

    def compare_report(self, kind, expected, payload, team=None, guard=None, guard_payload=None):
        with self.lock:
            key = (team, kind)
            if self.reports.get(key) != expected or (guard and self.reports.get((team, guard[0])) != guard[1]):
                return False
            self.reports[key] = copy.deepcopy(payload)
            if guard_payload is not None:
                self.reports[(team, guard[0])] = copy.deepcopy(guard_payload)
            return True


def request(**changes):
    return dict({'source_run_id': 1, 'goal': 'improve', 'allowed_changes': ['single_system'],
                 'max_calls': 20, 'max_rounds': 2}, **changes)


def wait(store, identifier):
    for _ in range(200):
        state = candidate.get(store, 'team', identifier)['candidate']
        if state['status'] not in ('running', 'cancel_requested'):
            return state
        time.sleep(.005)
    raise AssertionError('worker did not finish')


class CandidateTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()

    def assert_unchanged(self):
        self.assertEqual(self.store.operating_prompt, {'prompt': 'original'})
        self.assertEqual(self.store.gold, self.store.rows)

    def test_development_feedback_excludes_final_and_get_has_no_calls(self):
        observations, evaluations, calls = [], [], []
        def propose(goal, allowed, frozen, feedback, budget):
            observations.append(copy.deepcopy(feedback))
            budget.call(calls.append, 'proposal')
            frozen['single_system'] = 'candidate'
            return frozen
        def evaluate(spec, rows, budget):
            ids = [golden_hash(r) for r in rows]
            evaluations.append(ids)
            budget.call(calls.append, 'evaluation')
            return {'case_ids': ids}
        started = candidate.start(self.store, 'team', request(), propose=propose, evaluate=evaluate,
                                  compare=lambda *_: {'decision': 'improved'})
        state = wait(self.store, started['id'])
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(len(calls), 7)
        self.assertEqual(evaluations[0], evaluations[1])
        self.assertFalse(set(evaluations[0]) & set(evaluations[3]))
        self.assertEqual(observations[1][1]['development']['case_ids'], evaluations[0])
        self.assertEqual(state['final_result']['case_ids'], evaluations[4])
        self.assertEqual(state['development_ids'], evaluations[0])
        self.assertEqual(state['final_ids'], evaluations[3])
        self.assertFalse(candidate.get(self.store, 'other', started['id'])['ok'])
        candidate.get(self.store, 'team', started['id'])
        self.assertEqual(len(calls), 7)
        self.assert_unchanged()

    def test_cancel_during_call(self):
        entered, release = threading.Event(), threading.Event()
        def propose(goal, allowed, frozen, feedback, budget):
            budget.call(lambda: (entered.set(), release.wait(2)))
            frozen['single_system'] = 'candidate'
            return frozen
        started = candidate.start(self.store, 'team', request(), propose=propose, evaluate=lambda *args: {})
        self.assertTrue(entered.wait(1))
        self.assertFalse(candidate.start(self.store, 'team', request(), propose=propose, evaluate=lambda *args: {})['ok'])
        self.assertFalse(candidate.cancel(self.store, 'other', started['id'])['ok'])
        self.assertEqual(candidate.cancel(self.store, 'team', started['id'])['candidate']['status'], 'cancel_requested')
        release.set()
        self.assertEqual(wait(self.store, started['id'])['status'], 'cancelled')
        self.assert_unchanged()

    def test_saved_final_cases_never_enter_development_feedback(self):
        ids = sorted(golden_hash(row) for row in self.store.rows)
        final_id = ids[-1]
        items = [{'hash': key, 'got': {'m': {'summary': 'FINAL_SECRET' if key == final_id else 'development'}}}
                 for key in ids]
        feedback_seen = []
        def propose(goal, allowed, frozen, feedback, budget):
            feedback_seen.append(copy.deepcopy(feedback))
            frozen['single_system'] = 'candidate'
            return frozen
        result = candidate.start(self.store, 'team', request(max_rounds=2), propose=propose,
                                 evaluate=lambda *args: {'items': []},
                                 source_results={'model': 'm', 'items': items},
                                 compare=lambda *_: {'decision': 'improved'})
        state = wait(self.store, result['id'])
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(len(feedback_seen), 2)
        self.assertNotIn('FINAL_SECRET', str(feedback_seen))
        self.assertIn('FINAL_SECRET', str(state['final_baseline']))
        self.assert_unchanged()

    def test_cancel_races_completion_compare_and_swap(self):
        original = self.store.compare_report
        injected = []
        def concurrent_cancel(kind, expected, payload, **kwargs):
            if payload.get('status') == 'completed' and not injected:
                injected.append(True)
                cancelled = copy.deepcopy(expected)
                cancelled['status'] = 'cancel_requested'
                self.assertTrue(original(kind, expected, cancelled, **kwargs))
                return False
            return original(kind, expected, payload, **kwargs)
        def propose(goal, allowed, frozen, feedback, budget):
            frozen['single_system'] = 'candidate'
            return frozen
        with patch.object(self.store, 'compare_report', side_effect=concurrent_cancel):
            result = candidate.start(self.store, 'team', request(max_rounds=1), propose=propose,
                                     evaluate=lambda *args: {}, compare=lambda *_: {'decision': 'improved'})
            state = wait(self.store, result['id'])
        self.assertTrue(injected)
        self.assertEqual(state['status'], 'cancelled')
        self.assert_unchanged()

    def test_failure_limit_and_forbidden_change(self):
        def bad(goal, allowed, frozen, feedback, budget):
            frozen['calls'] = {'extra': {}}
            return frozen
        result = candidate.start(self.store, 'team', request(), propose=bad, evaluate=lambda *args: {})
        self.assertEqual(wait(self.store, result['id'])['status'], 'failed')
        def propose(goal, allowed, frozen, feedback, budget):
            budget.call(lambda: None)
            frozen['single_system'] = 'candidate'
            return frozen
        def evaluate(spec, rows, budget):
            return budget.call(lambda: {})
        result = candidate.start(self.store, 'team', request(max_calls=1), propose=propose, evaluate=evaluate)
        stopped = wait(self.store, result['id'])
        self.assertEqual((stopped['status'], stopped['calls']), ('stopped', 1))
        self.assert_unchanged()

    def test_refuses_unready_invalid_or_tampered_snapshot(self):
        self.assertFalse(candidate.start(self.store, 'team', request())['ok'])
        stub = dict(propose=lambda *a: {}, evaluate=lambda *a: {})
        self.assertFalse(candidate.start(self.store, 'team', request(max_calls=0), **stub)['ok'])
        self.store.run['metrics']['basis_fingerprint'] = 'tampered'
        self.assertFalse(candidate.start(self.store, 'team', request(), **stub)['ok'])
        self.assert_unchanged()

    def test_paired_comparison_distinguishes_improvement_regression_and_hold(self):
        def report(grade, count=2):
            return {'model': 'm', 'items': [{'hash': str(i), 'source': {'title': str(i), 'body': 'body'},
                    'expected': {'finalGrade': 'G'}, 'got': {'m': {'finalGrade': grade}}} for i in range(count)]}
        improved = candidate._compare(report('R'), report('G'))
        self.assertEqual(improved['decision'], 'improved')
        self.assertEqual([c['grade'] for c in improved['cases']], ['improved', 'improved'])
        self.assertEqual(candidate._compare(report('G'), report('R'))['decision'], 'regression')
        self.assertEqual(candidate._compare(report('R', 1), report('G', 1))['decision'], 'hold')
        mismatched = report('R'); mismatched['items'][0]['source']['body'] = 'different'
        self.assertEqual(candidate._compare(report('G'), mismatched)['decision'], 'hold')
        before, after = report('R'), report('G')
        for item in before['items'] + after['items']:
            item['expected']['summary'] = '도서관 독서 프로그램 안내'
        for item in before['items']:
            item['got']['m']['summary'] = item['expected']['summary']
        for item in after['items']:
            item['got']['m']['summary'] = '완전히 다른 내용'
        self.assertEqual(candidate._compare(before, after)['decision'], 'regression',
                         'A grade improvement must not hide a summary regression')
        for item in before['items']:
            item['got']['m']['finalGrade'] = 'G'
            item['got']['m']['summary'] = '완전히 다른 내용'
        for item in after['items']:
            item['got']['m']['summary'] = item['expected']['summary']
        self.assertEqual(candidate._compare(before, after)['decision'], 'improved',
                         'A candidate can improve a measured field without changing the grade')

    def test_http_adapter_runs_frozen_evaluations_with_shared_budget(self):
        snapshot = {'rows': copy.deepcopy(self.store.rows), 'models': ['model'], 'baseline_model': 'model',
                    'executions': {'model': self.store.snapshot['execution']}, 'limits': {},
                    'scope': 'all', 'metric_version': 'v1'}
        evaluations = []
        class Client:
            def _mock(self, *args, **kwargs):
                return {'changes': {'single_system': 'new candidate'}}, None
            def complete_json(self, system, user, tag):
                return self._mock(system, user, tag)
        def frozen(team, spec, *, created_by, background, call_guard):
            evaluations.append(copy.deepcopy(spec))
            call_guard(lambda: None)
            return {'ok': True, 'id': str(len(evaluations)), 'run': {'status': 'completed',
                    'model_results': [], 'items': [], 'cost': {'status': 'unknown'}}}
        service = SimpleNamespace(get_run=lambda identifier, team: {'ok': True, 'run': {'status': 'completed', 'snapshot': snapshot}},
                                  start_frozen=frozen)
        server = SimpleNamespace(get_store=lambda: self.store, Handler=SimpleNamespace(server_mock=True))
        with patch.object(candidate, '_SV', server), patch('prism.evalservice', service, create=True), \
                patch.object(execution, 'restore', return_value=Client()):
            result = candidate.start_request('team', request(source_run_id='source', max_rounds=1))
            state = wait(self.store, result['id'])
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(state['calls'], 3)
        self.assertEqual(len(evaluations), 2)
        self.assertEqual(evaluations[1]['executions']['model']['single_system'], 'new candidate')
        self.assertEqual(state['outcome'], 'hold')
        self.assert_unchanged()

    def test_sqlite_persists_candidate_and_source_index(self):
        from prism.store import Store as SqliteStore
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteStore(directory + '/candidate.db')
            source = self.store.snapshot
            def propose(goal, allowed, frozen, feedback, budget):
                frozen['single_system'] = 'candidate'
                return frozen
            result = candidate.start(store, 'team', request(source_run_id='source', max_rounds=1),
                                     source_loader=lambda *_: (source, source['execution']),
                                     propose=propose, evaluate=lambda *args: {'status': 'ok'})
            self.assertTrue(result['ok'])
            state = wait(store, result['id'])
            self.assertEqual(state['status'], 'completed')
            self.assertEqual(candidate.get(store, 'team', result['id'])['candidate']['outcome'], 'hold')

    def test_actual_frozen_service_and_mock_provider(self):
        from prism import config, evalservice, llm, serve
        from prism.store import Store as SqliteStore, content_hash
        original_cache = serve._LLM_CACHE
        original_entries = original_cache.copy()
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteStore(directory + '/candidate.db')
            for i in range(3):
                content = {'displayServiceName': '뉴스', 'title': '제목 ' + str(i), 'body': '원문 ' + str(i)}
                store.upsert_golden(content_hash(content), content, {'finalGrade': 'G', 'reasons': []})
            original_mock = llm.LLMClient._mock
            def provider(client, system, user, tag=''):
                if tag == 'evaluation_candidate':
                    return {'changes': {'calls.summary.system': '개선된 시스템 프롬프트'}}, llm.LLMResult('', 0, 0, 0, 0)
                return original_mock(client, system, user, tag)
            with patch.object(config, 'DEFAULT_CONFIG_PATH', directory + '/config.json'), \
                    patch.object(serve, '_STORE', store), patch.object(serve, '_supa', return_value=False), \
                    patch.object(serve, '_LLM_CACHE', {}), \
                    patch.object(serve.Handler, 'server_mock', True), patch.object(evalservice, '_SV', serve), \
                    patch.object(candidate, '_SV', serve), patch.object(llm.LLMClient, '_mock', provider):
                source = evalservice.start(None, models=['solar-pro2'], scope='all', background=False)
                self.assertTrue(source['ok'], source)
                self.assertEqual(source['run']['status'], 'completed', source)
                source_snapshot = copy.deepcopy(source['run']['snapshot'])
                before = copy.deepcopy(store.get_golden(None))
                result = candidate.start_request(None, request(source_run_id=source['id'],
                    allowed_changes=['calls.summary.system'], max_calls=100, max_rounds=1))
                self.assertTrue(result['ok'], result)
                for _ in range(1000):
                    state = candidate.get(store, None, result['id'])['candidate']
                    if state['status'] not in ('running', 'cancel_requested'):
                        break
                    time.sleep(.01)
                self.assertEqual(state['status'], 'completed', state)
                self.assertGreater(state['calls'], 1)
                self.assertEqual(store.get_golden(None), before)
                self.assertEqual(evalservice.get_run(source['id'], None)['run']['snapshot'], source_snapshot)
        self.assertIs(serve._LLM_CACHE, original_cache)
        self.assertEqual(serve._LLM_CACHE, original_entries)


if __name__ == '__main__':
    unittest.main()
