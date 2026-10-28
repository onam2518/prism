"""Frozen common service: temporary DB and mocks only; no paid calls."""
import contextlib
import copy
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from prism import evaluation as EV, evalservice as E, spectrum_adapter as SP
from prism.store import Store, content_hash


def output(grade='G', **meta):
    return {'quality_meta': {'finalGrade': grade, 'reasons': [], 'review': 'auto'},
            'item_meta': dict(meta), 'trace': {'cost_usd': None, 'tokens': {'in': 2, 'out': 1}}}


class EvaluationServiceTests(unittest.TestCase):
    def setUp(self):
        from prism import serve, config
        self.sv = serve
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {'PRISM_BACKEND': 'sqlite'}))
        self.stack.enter_context(patch.object(config, 'DEFAULT_CONFIG_PATH', os.path.join(self.temp.name, 'config.json')))
        self.st = Store(os.path.join(self.temp.name, 'test.db'))
        self.addCleanup(self.st.close_thread_connection)
        self.stack.enter_context(patch.object(serve, '_STORE', self.st))
        self.stack.enter_context(patch.object(serve, '_LLM_CACHE', {}))
        self.stack.enter_context(patch.object(serve, '_supa', return_value=False))
        self.stack.enter_context(patch.object(serve.Handler, 'server_mock', True))
        self.stack.enter_context(patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')))
        for i in range(3):
            c = {'title': '자료 ' + str(i), 'body': '고정 평가 원문 ' + str(i), 'displayServiceName': '뉴스'}
            exp = {'finalGrade': 'R' if i == 1 else 'G', 'reasons': [], 'summary': '기준 문장',
                   'entities': [], 'intent_review': 'needed', 'intent': ['과거 인텐트'],
                   'meta_status': {'entities': 'no_value'}}
            self.st.upsert_golden(content_hash(c), c, exp)

    def fake_start(self, **kwargs):
        with patch('prism.harness.run', return_value=output(summary='기준 문장', entities=[])):
            result = E.start(models=['solar-pro2'], background=False, **kwargs)
        self.assertTrue(result['ok'], result)
        return result

    def raw(self, rid, team=None):
        return self.st.get_report(E._PREFIX + rid, team=team)

    def save(self, run, team=None):
        self.st.save_report(E._PREFIX + run['id'], run, team=team)

    def test_same_metrics_denominators_and_states(self):
        r = self.fake_start()['run']
        self.assertEqual(r['status'], 'completed')
        self.assertEqual(r['completed'], 3)
        mr = r['model_results'][0]
        self.assertEqual(mr['metrics']['grade_n'], 3)
        self.assertEqual(mr['metrics']['harm_expected_n'], 1)
        self.assertEqual(mr['metrics']['harm_miss_rate'], 1)
        self.assertEqual(mr['metrics']['grade_accuracy'], round(2 / 3, 4))
        self.assertEqual(mr['evaluation']['eligibility']['intent']['pending'], 3)
        self.assertEqual(mr['evaluation']['eligibility']['entities']['scored'], 3)
        self.assertEqual(mr['evaluation']['eligibility']['content_category']['missing'], 3)
        self.assertEqual(mr['evaluation']['metrics']['entity_f1']['n'], 3)
        self.assertEqual(mr['evaluation']['semantic_status'], 'not_measured')
        self.assertEqual(r['cost']['status'], 'unknown')
        self.assertIsNone(r['cost']['total_usd'])

    def test_frozen_data_and_prompt_survive_cancel_resume(self):
        called, rid, statuses = [], [], []
        original_rows = copy.deepcopy(self.st.get_golden(None))
        def fake_run(content, llm, methodology):
            called.append(content['title'])
            if len(called) == 1:
                run_id = E.list_runs()['items'][0]['id']
                rid.append(run_id)
                statuses.append(E.cancel(run_id)['status'])
                self.st.register_golden(None, [{'content': {'title': 'changed'}, 'expected': {'finalGrade': 'R'}}])
            return output()
        with patch('prism.harness.run', side_effect=fake_run):
            first = E.start(models=['solar-pro2'], background=False)
        self.assertEqual(statuses, ['cancel_requested'])
        self.assertEqual(first['run']['status'], 'cancelled')
        self.assertEqual(first['run']['completed'], 1)
        self.assertEqual(first['run']['snapshot']['rows'], original_rows)
        frozen = copy.deepcopy(first['run']['snapshot'])
        with patch('prism.harness.run', side_effect=lambda c, llm, m: (called.append(c['title']) or output())), \
                patch('prism.execution.capture', side_effect=AssertionError('recapture forbidden')):
            resumed = E.resume(rid[0], background=False)
        self.assertTrue(resumed['ok'], resumed)
        self.assertEqual(resumed['run']['status'], 'completed')
        self.assertEqual(resumed['run']['snapshot'], frozen)
        self.assertEqual(len(called), 3)
        self.assertNotIn('changed', called)

    def test_success_saved_before_summary_crash_is_reused(self):
        with patch('prism.harness.run', return_value=output()) as execute, \
                patch.object(E, '_finish', side_effect=RuntimeError('crash after saved results')):
            first = E.start(models=['solar-pro2'], background=False)
        self.assertEqual(execute.call_count, 3)
        run = self.raw(first['id'])
        self.assertEqual(len(run['results']), 3)
        run['lease'] = 0
        self.save(run)
        with patch('prism.harness.run', side_effect=AssertionError('completed row called again')):
            resumed = E.resume(first['id'], background=False)
        self.assertEqual(resumed['run']['status'], 'completed')
        self.assertEqual(resumed['run']['completed'], 3)

    def test_failed_item_retries_and_success_is_reused(self):
        calls = []
        def fail_one(content, llm, method):
            calls.append(content['title'])
            if len(calls) == 2:
                raise RuntimeError('provider unavailable')
            return output()
        with patch('prism.harness.run', side_effect=fail_one):
            first = E.start(models=['solar-pro2'], background=False)
        self.assertEqual(first['run']['status'], 'failed')
        self.assertEqual(first['run']['failed'], 1)
        self.assertEqual(first['run']['model_results'][0]['metrics']['grade_n'], 3)
        with patch('prism.harness.run', return_value=output()) as execute:
            resumed = E.resume(first['id'], background=False)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(resumed['run']['completed'], 3)

    def test_team_guards_on_all_operations(self):
        first = self.fake_start(team='team-a')
        rid = first['id']
        for op in (E.get_run, E.resume, E.cancel):
            self.assertEqual(op(rid, 'team-b')['code'], 'not_found')
            self.assertEqual(op(rid, None)['code'], 'not_found')
        self.assertEqual(E.list_runs('team-b')['items'], [])
        with patch.object(self.sv, '_supa', return_value=True), \
                patch.object(E, '_resolve', side_effect=AssertionError('unauthorized resolve')):
            self.assertEqual(E.start(models=['solar-pro2'])['code'], 'team_required')
            self.assertEqual(E.get_run(rid)['code'], 'team_required')
            self.assertEqual(E.resume(rid)['code'], 'team_required')
            self.assertEqual(E.cancel(rid)['code'], 'team_required')

    def test_read_only_get_never_recovers_or_calls(self):
        first = self.fake_start()
        run = self.raw(first['id'])
        run.update(status='running', lease=0)
        self.save(run)
        before = copy.deepcopy(self.raw(first['id']))
        with patch.object(self.st, 'compare_report', side_effect=AssertionError('GET write')), \
                patch.object(E, '_resolve', side_effect=AssertionError('GET model')), \
                patch.object(EV, 'semantic_embed', side_effect=AssertionError('GET embedding')):
            fetched = E.get_run(first['id'])
            history = E.list_runs()
        self.assertTrue(fetched['run']['stalled'])
        self.assertTrue(history['items'][0]['can_resume'])
        self.assertEqual(self.raw(first['id']), before)

    def test_completed_metrics_are_saved_not_rescored_on_get(self):
        first = self.fake_start()
        with patch.object(EV, 'report', side_effect=AssertionError('historical rescore')), \
                patch.object(E, '_metrics', side_effect=AssertionError('legacy rescore')):
            fetched = E.get_run(first['id'])
        self.assertEqual(fetched['run']['model_results'], first['run']['model_results'])

    def test_preview_has_current_count_without_calls_or_writes(self):
        with patch.object(E, '_resolve', side_effect=AssertionError('preview model')), \
                patch.object(self.st, 'compare_report', side_effect=AssertionError('preview write')):
            result = E.preview()
        self.assertEqual(result['total'], 3)
        self.assertEqual(result['eligibility']['intent']['pending'], 3)
        self.assertEqual(result['cost']['status'], 'unknown')
        self.assertFalse(E.preview(scope='invalid')['ok'])

    def test_idempotency_conflict_and_completed_reuse(self):
        first = self.fake_start(request_id='request-1')
        with patch('prism.harness.run', side_effect=AssertionError('duplicate model call')):
            again = E.start(models=['solar-pro2'], request_id='request-1', background=False)
            resumed = E.resume(first['id'], background=False)
        self.assertEqual(first['id'], again['id'])
        self.assertTrue(again['deduplicated'])
        self.assertTrue(resumed['deduplicated'])
        different = E.start(models=['solar-pro2'], request_id='request-1', limits={'max_calls': 5}, background=False)
        self.assertEqual(different['code'], 'idempotency_conflict')

    def test_rerun_creates_fresh_history_from_exact_snapshot(self):
        first = self.fake_start()
        self.st.register_golden(None, [{'content': {'title': 'changed'}, 'expected': {'finalGrade': 'R'}}])
        with patch('prism.execution.capture', side_effect=AssertionError('rerun recapture')), \
                patch('prism.harness.run', return_value=output()) as execute:
            result = E.rerun(first['id'], request_id='repeat', background=False)
            duplicate = E.rerun(first['id'], request_id='repeat', background=False)
        self.assertTrue(result['ok'], result)
        self.assertNotEqual(first['id'], result['id'])
        self.assertEqual(execute.call_count, 3)
        self.assertEqual(result['run']['snapshot'], first['run']['snapshot'])
        self.assertEqual(result['run']['rerun_of'], first['id'])
        self.assertEqual(result['id'], duplicate['id'])
        self.assertEqual(E.rerun(first['id'], 'another-team')['code'], 'not_found')
        run = self.raw(first['id'])
        run['external_guard'] = True
        self.save(run)
        self.assertEqual(E.rerun(first['id'])['code'], 'external_guard_required')

    def test_concurrent_starts_share_one_run(self):
        barrier, gate = threading.Barrier(4), threading.Event()
        returned = []
        def start():
            barrier.wait()
            returned.append(E.start(models=['solar-pro2']))
        def slow(*args):
            gate.wait(10)
            return output()
        with patch('prism.harness.run', side_effect=slow):
            threads = [threading.Thread(target=start) for _ in range(4)]
            for th in threads:
                th.start()
            for th in threads:
                th.join(10)
            self.assertEqual(len(returned), 4)
            self.assertTrue(all(r['ok'] for r in returned), returned)
            self.assertEqual(len({r['id'] for r in returned}), 1)
            gate.set()
            worker = E._ACTIVE.get(returned[0]['id'])
            if worker:
                worker.join(10)
        self.assertEqual(len(E.list_runs()['items']), 1)

    def test_actual_calls_obey_budget_including_parallel_calls(self):
        result = E.start(models=['solar-pro2'], limits={'max_calls': 2}, background=False)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['run']['status'], 'limit_reached')
        self.assertEqual(result['run']['cost']['calls'], 2)
        self.assertFalse(result['run']['can_resume'])
        self.assertEqual(E.resume(result['id'])['code'], 'limit_reached')

    def test_candidate_guard_blocks_without_recording_a_provider_call(self):
        first = self.fake_start()
        snapshot = first['run']['snapshot']
        def guard(fn, *a, **kw):
            raise RuntimeError('candidate call budget exhausted')
        result = E.start_frozen(None, snapshot, background=False, call_guard=guard)
        self.assertEqual(result['run']['status'], 'limit_reached')
        self.assertEqual(result['run']['cost']['calls'], 0)
        self.assertEqual(E.resume(result['id'], background=False)['code'], 'external_guard_required')

    def test_multi_model_run_preserves_same_basis(self):
        with patch('prism.harness.run', return_value=output()):
            result = E.start(models=['solar-pro2', 'gpt-5.4'], background=False)
        run = result['run']
        self.assertEqual(run['total'], 6)
        self.assertEqual(run['completed'], 6)
        self.assertEqual(len(run['items']), 3)
        self.assertEqual(set(run['items'][0]['got']), {'solar-pro2', 'gpt-5.4'})
        self.assertTrue(run['comparisons'][0]['comparable'])
        self.assertEqual(run['comparisons'][0]['verdict'], 'insufficient_evidence')

    def test_target_alias_preserves_actual_provider_model(self):
        from prism.config import Config
        from prism.llm import LLMClient
        client = LLMClient(mock=True, config=Config.load(), model='openai/gpt-5.4')
        with patch.object(E, '_resolve', return_value=client), patch('prism.harness.run', return_value=output()):
            result = E.start(models=['gpt-5.4'], background=False)
            saved = E.save_environment(None, '라우터 판정', 'gpt-5.4', 50)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['run']['status'], 'completed')
        self.assertEqual(result['run']['models'], ['gpt-5.4'])
        self.assertEqual(result['run']['snapshot']['executions']['gpt-5.4']['main']['model'], 'openai/gpt-5.4')
        self.assertEqual(saved['environment']['judge_model'], 'openai/gpt-5.4')
        self.assertEqual(saved['environment']['judge_settings']['model'], 'openai/gpt-5.4')

    def test_snapshot_corruption_rejected_before_resume(self):
        first = self.fake_start()
        run = self.raw(first['id'])
        run['snapshot']['rows'][0]['content']['body'] = 'tampered'
        run['status'] = 'cancelled'
        self.save(run)
        with patch('prism.harness.run', side_effect=AssertionError('corrupted execution')):
            self.assertEqual(E.resume(first['id'])['code'], 'snapshot_corrupt')

    def test_expired_inflight_attempt_retains_unknown_cost_on_resume(self):
        first = self.fake_start()
        run = self.raw(first['id'])
        run.update(status='running', lease=0)
        last = list(run['results'])[-1]
        run['results'][last].update(status='running', output=None)
        run['attempts']['lost'] = {'model': 'solar-pro2', 'status': 'running', 'usage': None}
        run['attempts']['known'] = {'model': 'solar-pro2', 'status': 'completed',
                                   'usage': SP._usage({'cost_usd': 0.1}, 0, 'estimated')}
        self.save(run)
        with patch('prism.harness.run', return_value=output()) as execute:
            resumed = E.resume(first['id'], background=False)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(resumed['run']['cost']['status'], 'partial')
        self.assertEqual(resumed['run']['cost']['known_usd'], 0.1)
        self.assertIsNone(resumed['run']['cost']['total_usd'])
        self.assertEqual(self.raw(first['id'])['attempts']['lost']['status'], 'uncertain')

    def test_live_persisted_lease_prevents_resume_without_local_thread(self):
        first = self.fake_start()
        run = self.raw(first['id'])
        run.update(status='running', lease=time.time() + 120, owner='another-server')
        self.save(run)
        with patch('prism.harness.run', side_effect=AssertionError('second worker')):
            result = E.resume(first['id'], background=False)
        self.assertEqual(result['code'], 'already_running')

    def test_saved_environment_is_scoped_and_frozen_in_execution(self):
        with patch('prism.llm.LLMClient._call', side_effect=AssertionError('environment model call')):
            saved = E.save_environment('team-a', '검수 환경', 'solar-pro2', 50)
        self.assertTrue(saved['ok'], saved)
        env = saved['environment']
        self.assertIn('judge_settings', env)
        self.assertEqual(env['judge_settings']['config']['retry']['max_retries'], 0)
        self.assertEqual(E.list_environments('team-b')['items'], [])
        self.assertEqual(E.start('team-b', models=['solar-pro2'], environment_id=env['id'])['code'], 'not_found')
        run = self.fake_start(team='team-a', environment_id=env['id'])['run']
        self.assertEqual(run['snapshot']['limits'], {'max_calls': 50})
        self.assertEqual(run['snapshot']['environment'], env)
        E.save_environment('team-a', '검수 환경', 'solar-pro2', 75)
        self.assertEqual(E.get_run(run['id'], 'team-a')['run']['snapshot']['environment'], env)

    def test_invalid_request_rejected_before_resolve(self):
        with patch.object(E, '_resolve', side_effect=AssertionError('invalid resolve')):
            for args in ({'models': []}, {'models': 'solar-pro2'}, {'models': ['a', 'a']},
                         {'models': ['a'], 'limits': {'max_calls': -1}},
                         {'models': ['a'], 'limits': {'max_cost_usd': 1}},
                         {'models': ['a'], 'baseline_model': 'b'}, {'models': ['a'], 'scope': 'bad'}):
                self.assertFalse(E.start(**args)['ok'], args)


class SpectrumAccountingTests(unittest.TestCase):
    def test_unknown_partial_known_are_distinct(self):
        unknown = {'usage': SP._usage({}, 0, 'unknown')}
        known = {'usage': SP._usage({'tokens': 5, 'cost_usd': 0.1}, 0, 'estimated')}
        for attempts, status, cost in (([unknown], 'unknown', None),
                                       ([unknown, known], 'partial', None), ([known], 'known', 0.1)):
            actual = SP.usage_summary(attempts)
            self.assertEqual(actual['status'], status)
            self.assertEqual(actual['total_usd'], cost)
        self.assertEqual(SP.usage_summary([unknown, known])['known_usd'], 0.1)

    def test_unknown_prices_and_invalid_usage(self):
        from prism.llm import LLMResult
        result = LLMResult('ok', 3, 2, 0, 0, price_in=None, price_out=None)
        self.assertIsNone(E._result_usage(result, 0.01, False)['cost_usd'])
        for value in (float('nan'), -1, True):
            with self.assertRaises(ValueError):
                SP._usage({'cost_usd': value}, 0, 'reported')
        with self.assertRaises(ValueError):
            SP._clean({'config': {'api_key': 'no'}})


if __name__ == '__main__':
    unittest.main()
