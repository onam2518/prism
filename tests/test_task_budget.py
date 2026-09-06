"""작업 비용: 제출 경계, 실패 trace, 무제한, 비교 병렬 초과와 부분 결과 회귀."""
import json
import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from prism import config as C, serve, runops as RO, ingestops as IO, evalops as EO, learnops as LO
from prism import abtest
from prism.store import Store
from tests.test_evalops import _seed_golden


class TaskBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg_path = os.path.join(self.tmp.name, 'config.json')
        self.patch(C, 'DEFAULT_CONFIG_PATH', self.cfg_path)
        self.set_budget(0)
        self.st = Store(os.path.join(self.tmp.name, 't.db'))
        self.patch(serve, 'get_store', lambda: self.st)
        self.patch(serve.Handler, 'server_mock', True)
        self.llm = SimpleNamespace(mock=False, model='budget-test')
        self.patch(serve, 'make_text_llm', lambda *a: self.llm)
        self.patch(serve, 'llm_for_model', lambda *a: (self.llm, 'fake'))
        self.costs = self.patch(serve, '_log_cost_rollup')
        self.patch(serve, '_log_fail_rollup')
        self.patch(serve, '_entdict_after_save')
        self.patch(serve, '_save_drafts')
        self.patch(RO, 'store_save')
        self.patch(IO, '_INGEST_STATE', {})
        self.patch(serve, '_INGEST_STATE', IO._INGEST_STATE)
        self.patch(EO, 'CHUNK', 2)
        self.patch(LO, '_COMPARE_CHUNK', 2)
        self.patch(LO, '_COMPARE_JOBS', {})
        self.patch(LO, '_LAST_EVAL_DETAIL', [])
        _seed_golden(self.st, 5)
        self.rows = self.st.get_golden()

    def patch(self, obj, name, *args, **kwargs):
        p = patch.object(obj, name, *args, **kwargs)
        value = p.start()
        self.addCleanup(p.stop)
        return value

    def set_budget(self, value):
        with open(self.cfg_path, 'w') as f:
            json.dump({'task_budget_usd': value}, f)

    def out(self, failed=False):
        return {'item_meta': {}, 'quality_meta': {'finalGrade': '' if failed else 'G', 'reasons': []},
                'trace': {'model': 'budget-test', 'cost_usd': 0.02,
                          'fallbacks': ['call_fail'] if failed else [],
                          'fails': [{'kind': 'timeout'}] if failed else []}}

    def method(self, failed=False):
        return self.patch(abtest, 'run_methodology',
                          side_effect=lambda rows, *a, **kw: [self.out(failed) for _ in rows])

    def test_upload_budget_unlimited_and_failure_cost(self):
        raw = ('콘텐츠 그룹,제목,본문\n' + ''.join('뉴스,title%d,body%d\n' % (i, i) for i in range(5))).encode()
        for limit, failed, expected in [(0.03, False, 2), (0, False, 5), (0.03, True, 2)]:
            with self.subTest(limit=limit, failed=failed):
                self.set_budget(limit)
                with patch.object(RO.PIPE, 'extract', side_effect=lambda *a, **kw: self.out(failed)) as extract:
                    r = RO.run_batch(raw, 'u.csv')
                self.assertEqual(extract.call_count, expected)
                self.assertEqual(r['count'], expected)
                self.assertEqual(r['skipped'], 5 - expected)
                self.assertAlmostEqual(r['spent_usd'], expected * 0.02)
                self.assertEqual(r['budget_stop'], bool(limit))
                job = next(v for k, v in IO._INGEST_STATE.items() if k.startswith('batch:'))
                self.assertEqual(job['budget_stop'], bool(limit))
                self.assertIn('비용', job['last_msg'])
        self.assertEqual(self.costs.call_count, 9)

    def test_ingest_budget_unlimited_and_failure_cost(self):
        self.patch(IO, '_fetch_records', return_value=([{}] * 5, None))
        from prism import ingest
        self.patch(ingest, 'to_contents_rows', return_value=([r['content'] for r in self.rows], {}))
        save = self.patch(self.st, 'save_dedup', return_value={'inserted': 0, 'updated': 0, 'skipped': 0})
        for limit, failed, expected in [(0.03, False, 2), (0, False, 5), (0.03, True, 2)]:
            with self.subTest(limit=limit, failed=failed):
                self.set_budget(limit)
                with patch.object(IO.PIPE, 'extract', side_effect=lambda *a, **kw: self.out(failed)) as extract:
                    r = IO.ingest_run_source({'id': 'test', 'endpoint': 'https://example.invalid'})
                self.assertTrue(r['ok'], r)
                self.assertEqual(extract.call_count, expected)
                self.assertEqual(len(save.call_args[0][0]), expected)
                self.assertEqual(r['budget_skipped'], 5 - expected)
                self.assertAlmostEqual(r['spent_usd'], expected * 0.02)
                self.assertEqual(r['budget_stop'], bool(limit))
                self.assertEqual(IO._INGEST_STATE['test']['last_ok'], not bool(limit))
        self.assertEqual(self.costs.call_count, 9)

    def test_eval_run_budget_unlimited_failure_and_persistence(self):
        for limit, failed, expected in [(0.03, False, 2), (0, False, 5), (0.03, True, 2)]:
            with self.subTest(limit=limit, failed=failed):
                self.set_budget(limit)
                rid = self.st.eval_run_create('', 'budget-test', 'all', 5)
                with patch.object(abtest, 'run_methodology', side_effect=lambda rows, *a, **kw: [self.out(failed) for _ in rows]) as run:
                    EO._run_loop(rid, self.rows, self.llm, None, EO._zero_metrics())
                r = EO.eval_run_report(rid)
                self.assertEqual(sum(len(c[0][0]) for c in run.call_args_list), expected)
                self.assertEqual(r['evaluated'], expected)
                self.assertEqual(r['skipped'], 5 - expected)
                self.assertAlmostEqual(r['spent_usd'], expected * 0.02)
                self.assertEqual(r['budget_stop'], bool(limit))
                self.assertEqual(r['status'], 'failed' if limit else 'done')
                listed = next(x for x in EO.eval_runs_list()['items'] if x['id'] == rid)
                self.assertEqual(listed['budget_stop'], bool(limit))
                self.assertEqual(listed['skipped'], 5 - expected)
                self.assertEqual(len(self.st.eval_result_hashes(rid)), expected)
                if limit:
                    self.assertIn('예산 중단', self.st.eval_run_get(rid)['error'])
        self.assertEqual(self.costs.call_count, 9)

    def test_eval_resume_does_not_reset_spend(self):
        self.set_budget(0.03)
        rid = self.st.eval_run_create('', 'budget-test', 'all', 5)
        run = self.method(failed=True)
        EO._run_loop(rid, self.rows, self.llm, None, EO._zero_metrics())
        base = self.st.eval_run_get(rid)['metrics']
        run.reset_mock()
        EO._run_loop(rid, self.rows[2:], self.llm, None, base)
        run.assert_not_called()
        self.assertEqual(EO.eval_run_report(rid)['spent_usd'], 0.04)
        self.set_budget(0)
        EO._run_loop(rid, self.rows[2:], self.llm, None, base)
        self.assertEqual(EO.eval_run_report(rid)['spent_usd'], 0.10)
        self.assertEqual(EO.eval_run_report(rid)['status'], 'done')

    def test_exact_final_chunk_is_complete(self):
        self.set_budget(0.04)
        self.method()
        rid = self.st.eval_run_create('', 'budget-test', 'all', 2)
        EO._run_loop(rid, self.rows[:2], self.llm, None, EO._zero_metrics())
        self.assertFalse(EO.eval_run_report(rid)['budget_stop'])

    def test_immediate_eval_budget_unlimited_and_failure_cost(self):
        for limit, failed, expected in [(0.03, False, 2), (0, False, 5), (0.03, True, 2)]:
            with self.subTest(limit=limit, failed=failed):
                self.set_budget(limit)
                with patch.object(abtest, 'run_methodology', side_effect=lambda rows, *a, **kw: [self.out(failed) for _ in rows]) as run:
                    r = LO.eval_golden()
                self.assertEqual(sum(len(c[0][0]) for c in run.call_args_list), expected)
                self.assertEqual(r['evaluated'], expected)
                self.assertEqual(r['skipped'], 5 - expected)
                self.assertEqual(r['ok'], not bool(limit))
                self.assertAlmostEqual(r['spent_usd'], expected * 0.02)
        self.assertEqual(self.costs.call_count, 9)

    def test_compare_budget_unlimited_and_failure_cost(self):
        for limit, failed, expected in [(0.03, False, 2), (0, False, 5), (0.03, True, 2)]:
            with self.subTest(limit=limit, failed=failed):
                self.set_budget(limit)
                with patch.object(abtest, 'run_methodology', side_effect=lambda rows, *a, **kw: [self.out(failed) for _ in rows]) as run:
                    r = LO.compare_models_on_golden(['a'])
                self.assertEqual(sum(len(c[0][0]) for c in run.call_args_list), expected)
                self.assertEqual(r['models'][0]['n'], expected)
                self.assertEqual(r['budget_skipped'], 5 - expected)
                self.assertAlmostEqual(r['spent_usd'], expected * 0.02)
                self.assertEqual(r['budget_stop'], bool(limit))
                if limit:
                    self.assertFalse(r['best'])
                    self.assertIsNone(r['cheapest_passing'])
                    self.assertFalse(r['models'][0]['passed'])
                self.assertEqual(LO.last_model_compare()['budget_stop'], bool(limit))
        self.assertEqual(self.costs.call_count, 9)

    def test_compare_parallel_inflight_overshoot_is_accounted(self):
        self.set_budget(0.01)
        barrier = threading.Barrier(2)
        def chunk(rows, *a, **kw):
            barrier.wait(timeout=3)
            return [self.out(True) for _ in rows]
        with patch.object(abtest, 'run_methodology', side_effect=chunk) as run:
            r = LO.compare_models_on_golden(['a', 'b'])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(r['spent_usd'], 0.08)
        self.assertGreater(r['spent_usd'], r['budget_usd'])
        self.assertEqual(r['budget_skipped'], 6)
        self.assertTrue(r['budget_stop'])
        self.assertFalse(r['best'])
        self.assertIsNone(r['cheapest_passing'])

    def test_compare_unstarted_model_is_not_scored_as_failed_rows(self):
        self.set_budget(0.03)
        run = self.method()
        prep, err = LO._compare_prepare(['a', 'b'], None, 'all')
        self.assertIsNone(err)
        first = LO._compare_run_model(prep['ready'][0], prep['rows'], prep['cfg'], budget=prep['budget'])
        second = LO._compare_run_model(prep['ready'][1], prep['rows'], prep['cfg'], budget=prep['budget'])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(second[0]['n'], 0)
        self.assertEqual(second[0]['budget_skipped'], 5)
        r = LO._compare_finish(prep, [first, second], None)
        self.assertEqual(r['items'], [])
        self.assertEqual(r['budget_skipped'], 8)
        self.assertFalse(r['best'])

    def test_compare_background_exposes_budget_terminal_state(self):
        self.set_budget(0.03)
        self.method(True)
        jid = LO.compare_start(['a'])['id']
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = LO.compare_status(jid)['job']
            if job['status'] != 'running':
                break
            time.sleep(0.01)
        self.assertEqual(job['status'], 'budget_stop')
        self.assertEqual(job['spent_usd'], 0.04)
        self.assertEqual(job['budget_skipped'], 3)
        self.assertEqual(job['models']['a']['status'], 'budget_stop')
        self.assertFalse(job['result']['best'])

    def test_learning_does_not_compile_after_partial_evaluation(self):
        self.set_budget(0.03)
        self.method()
        self.patch(LO, 'build_golden_from_reviews', return_value={})
        self.patch(serve, 'award_quest_bonus')
        compile_run = self.patch(LO, 'meta_compile_run')
        r = LO.learning_batch()
        self.assertTrue(r['budget_stop'])
        compile_run.assert_not_called()

    def test_invalid_saved_budget_never_starts_model_requests(self):
        self.set_budget('nan')
        run = self.method()
        rid = self.st.eval_run_create('', 'budget-test', 'all', 5)
        EO._run_loop(rid, self.rows, self.llm, None, EO._zero_metrics())
        self.assertEqual(EO.eval_run_report(rid)['status'], 'failed')
        self.assertFalse(LO.compare_models_on_golden(['a'])['ok'])
        with self.assertRaises(ValueError):
            LO.eval_golden()
        with self.assertRaises(ValueError):
            RO.run_batch(b'', 'x.csv')
        self.patch(IO, '_fetch_records', return_value=([{}], None))
        from prism import ingest
        self.patch(ingest, 'to_contents_rows', return_value=([self.rows[0]['content']], {}))
        extract = self.patch(IO.PIPE, 'extract')
        self.assertFalse(IO.ingest_run_source({'id': 'invalid'})['ok'])
        run.assert_not_called()
        extract.assert_not_called()

    def test_learning_reverts_partial_post_evaluation(self):
        from prism import prompts as PR
        self.patch(PR, 'LEARNED', {'x': 'before'})
        self.patch(PR, 'LEARNED_BY_MODEL', {})
        self.patch(LO, 'build_golden_from_reviews', return_value={})
        self.patch(serve, 'award_quest_bonus')
        self.patch(serve, 'rerun_unconfirmed', return_value={})
        self.patch(LO, 'snapshot_prompts', return_value={})
        def compile_run(*a):
            PR.LEARNED = {'x': 'after'}
            return {}
        self.patch(LO, 'meta_compile_run', side_effect=compile_run)
        self.patch(LO, 'eval_golden', side_effect=[{'ok': True, 'grade_accuracy': 1},
                   {'ok': False, 'budget_stop': True, 'error': '예산 중단'}])
        r = LO.learning_batch()
        self.assertEqual(PR.LEARNED, {'x': 'before'})
        self.assertTrue(r['improve']['reverted'])
        self.assertIn('예산 중단', r['improve']['revert_reason'])

    def test_config_validation_and_unauthorized_update(self):
        for bad in [-1, 1001, float('nan'), float('inf'), 'oops', '', {}, True]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                C.task_budget(bad)
        self.assertEqual(C.task_budget('0'), 0)
        self.assertEqual(C.task_budget('0.05'), 0.05)
        self.patch(serve, 'config_status', return_value={})
        self.patch(serve, 'sync_prompt')
        with patch.object(serve, 'backend_mode', return_value=('sqlite', '')):
            for bad in [None, 'nan', -1, '', True]:
                self.assertIn('error', serve.apply_config({'task_budget_usd': bad}))
            self.assertNotIn('error', serve.apply_config({'task_budget_usd': 0.05}))
            self.assertEqual(C.Config.load().task_budget_usd, 0.05)
            with patch.object(C.Config, 'save_template', side_effect=OSError('disk full')):
                self.assertIn('저장 실패', serve.apply_config({'task_budget_usd': 0.01})['error'])
        with patch.object(serve, 'backend_mode', return_value=('supabase', '')):
            serve.apply_config({'task_budget_usd': 0}, allow_key=False)
        self.assertEqual(C.Config.load().task_budget_usd, 0.05)


if __name__ == '__main__':
    unittest.main()
