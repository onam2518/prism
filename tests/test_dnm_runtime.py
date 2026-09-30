import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from prism import dnm, agents as A, execution as EX, topic_conditions as TC
from prism.config import Config
from prism.llm import LLMClient
from prism.store import Store


class DNMRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(str(Path(self.tmp.name) / 'db'))
        self.addCleanup(self.store._conn().close)
        self.r = dnm.Runtime(self.store, 't')
        self.calls = []
        self.bad = False
        self.llm = LLMClient(model='test', mock=True, config=Config())
        def response(system, user, tag):
            self.calls.append(tag)
            if tag == 'item_summary': return {'summary': '내용 요약'}
            if tag == 'item_entities': return {'entities': ['invalid'] if self.bad else [{'name': 'A', 'type': 'OG'}]}
            if tag == 'item_intent': return {'intent': []}
            return {'content_category': []}
        self.llm._mock_fn = response
        self.p = patch.object(A, 'META_CFG', {'four_calls': True, 'call_models': {}})
        self.p.start(); self.addCleanup(self.p.stop)
        self.policy = dnm.prepare_policy(self.llm)
        self.registry = {'version': 'r1', 'entries': [{
            'route_id': 'fixture-news', 'ingestion_class': 'partner_news',
            'matches': {'service_code': {'provided': True, 'value': 'test'}},
            'effective_from': '2020-01-01T00:00:00+09:00', 'owner': 'fixture',
            'evidence': 'fixture only', 'approved_by': 'fixture',
            'approved_at': '2020-01-01T00:00:00+09:00', 'unique_key_verified': True}]}
        self.r.configure(self.registry, self.policy, 0)
        self.event = {'event_id': 'e1', 'source_revision': 1, 'route_id': 'fixture-news',
                      'source_fields': {'item_unique_key': 'hamny-test-1', 'service_code': 'test', 'cp_type': None},
                      'content': {'title': '제목', 'body': '내용'}}

    def run_event(self, event=None):
        return self.r.execute(event or self.event, lambda _: self.llm)

    def test_independent_results_and_idempotent_replay(self):
        first = self.run_event()
        self.assertEqual(set(first['meta_status'].values()), {'success', 'no_value'})
        self.assertEqual(first['attempts'], dict.fromkeys(dnm.MC.FIELDS, 1))
        self.assertFalse(first['publishable'])
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.run_event(), first)
        self.assertEqual(len(self.calls), 4)

    def test_bad_axis_has_only_three_durable_attempts(self):
        self.bad = True
        result = self.run_event()
        self.assertEqual(result['attempts']['entities'], 3)
        self.assertEqual(result['meta_status']['entities'], 'invalid_output')
        self.assertEqual(result['meta_status']['summary'], 'success')
        self.run_event(); self.assertEqual(len(self.calls), 6)

    def test_withdraw_and_return_reuses_results_and_attempts(self):
        result = self.run_event()
        reg = copy.deepcopy(self.registry); reg['version'] = 'r2'; reg['entries'][0]['ingestion_class'] = 'other'
        self.r.configure(registry=reg, expected_revision=1)
        withdrawn = self.r.current('hamny-test-1')
        self.assertEqual(withdrawn['summary'], '')
        self.assertEqual(withdrawn['decision'], 'skipped_out_of_scope')
        self.assertGreater(withdrawn['publication_revision'], result['publication_revision'])
        reg['version'] = 'r3'; reg['entries'][0]['ingestion_class'] = 'search_news'
        self.r.configure(registry=reg, expected_revision=2)
        returned = self.run_event()
        self.assertEqual(returned['summary'], result['summary'])
        self.assertEqual(returned['attempts'], result['attempts'])
        self.assertEqual(len(self.calls), 4)
        self.assertGreater(returned['publication_revision'], withdrawn['publication_revision'])

    def test_late_result_does_not_replace_new_input(self):
        self.r.begin(self.event)
        state = self.r._get(self.r._key('hamny-test-1')); old = state['current']
        token = self.r.claim('hamny-test-1', old, 'summary')
        changed = copy.deepcopy(self.event); changed.update(event_id='e2', source_revision=2)
        changed['content']['image_count'] = 0
        newer = self.r.begin(changed)
        self.r.complete('hamny-test-1', old, 'summary', token, 'success', 'old')
        current = self.r.current('hamny-test-1')
        self.assertEqual(current['summary'], '')
        self.assertEqual(current['publication_revision'], newer['publication_revision'])
        self.assertEqual(self.r._get(self.r._key('hamny-test-1'))['bundles'][old]['jobs']['summary']['value'], 'old')
        self.assertEqual(self.r.begin(self.event)['input_revision'], current['input_revision'])

    def test_old_event_reconciles_current_registry_without_restoring_old_input(self):
        newer = copy.deepcopy(self.event); newer.update(event_id='e2', source_revision=2)
        newer['content']['body'] = 'latest'
        current = self.run_event(newer)
        reg = copy.deepcopy(self.registry); reg['version'] = 'r2'; reg['entries'][0]['ingestion_class'] = 'other'
        self.r.configure(registry=reg, expected_revision=1)
        result = self.r.begin(self.event)
        self.assertEqual(result['decision'], 'skipped_out_of_scope')
        self.assertEqual(result['input_revision'], current['input_revision'])
        self.assertEqual(result['summary'], '')

    def test_same_revision_conflict_and_source_identity(self):
        first = self.run_event()
        other = copy.deepcopy(self.event); other['content']['body'] = 'different'
        with self.assertRaises(ValueError): self.r.begin(other)
        other = copy.deepcopy(self.event); other['source_fields']['item_unique_key'] = 'hamny-test-2'
        self.run_event(other)
        self.assertEqual(self.r.current('hamny-test-1'), first)
        self.assertEqual(len(self.calls), 8)

    def test_manual_new_version_requires_confirmation_and_binds_golden(self):
        current = self.run_event()
        confirmed = self.r.manual('hamny-test-1', 'summary', '수동', current['input_revision'], current['policy_version'], 'reviewer')
        self.assertEqual(self.run_event()['summary'], '수동')
        golden = self.r.golden('hamny-test-1')
        self.assertEqual(dnm.training_fields(golden['content'], golden['expected'], current['policy_version']), {'summary': '수동'})
        golden['content']['body'] = '다른 입력'
        self.assertEqual(dnm.training_fields(golden['content'], golden['expected'], current['policy_version']), {})
        changed = copy.deepcopy(self.event); changed.update(event_id='e2', source_revision=2)
        changed['content']['body'] = 'new'
        newer = self.run_event(changed)
        self.assertIn('summary', newer['manual_review_required'])
        self.assertEqual(newer['summary'], '')
        self.assertEqual(newer['attempts']['summary'], 0)
        with self.assertRaises(ValueError):
            self.r.manual('hamny-test-1', 'summary', 'stale', confirmed['input_revision'], confirmed['policy_version'], 'reviewer')

    def test_concurrent_claim_is_single_and_budget_survives_expired_lease(self):
        self.r.begin(self.event)
        bundle = self.r._get(self.r._key('hamny-test-1'))['current']
        def claim(_):
            try: return self.r.claim('hamny-test-1', bundle, 'summary')
            finally: self.store._conn().close()
        with ThreadPoolExecutor(max_workers=4) as pool:
            tokens = list(pool.map(claim, range(4)))
        self.assertEqual(sum(t is not None for t in tokens), 1)
        oldtoken = next(t for t in tokens if t)
        with patch('prism.dnm.time.time', return_value=10 ** 11):
            newtoken = self.r.claim('hamny-test-1', bundle, 'summary')
        self.r.complete('hamny-test-1', bundle, 'summary', oldtoken, 'success', 'late attempt')
        self.assertEqual(self.r.current('hamny-test-1')['summary'], '')
        self.r.complete('hamny-test-1', bundle, 'summary', newtoken, 'success', 'latest')
        self.assertEqual(self.r.current('hamny-test-1')['summary'], 'latest')

    def test_unknown_route_and_missing_key_do_not_call(self):
        event = copy.deepcopy(self.event); event['route_id'] = 'unregistered'
        self.assertEqual(self.run_event(event)['decision'], 'pending_source')
        event = copy.deepcopy(self.event); event['source_fields']['item_unique_key'] = None
        self.assertEqual(self.run_event(event)['decision'], 'pending_source_key')
        self.assertEqual(self.calls, [])

    def test_registry_matches_presence_null_and_unknown_without_prefix_inference(self):
        reg = copy.deepcopy(self.registry)
        reg['entries'][0]['matches'] = {'cp_type': {'provided': True, 'value': None}}
        self.assertEqual(dnm.classify(self.event, reg)['scope_status'], 'eligible')
        for change in ('missing', '', 'unknown'):
            event = copy.deepcopy(self.event)
            if change == 'missing': event['source_fields'].pop('cp_type')
            else: event['source_fields']['cp_type'] = change
            self.assertEqual(dnm.classify(event, reg)['scope_status'], 'unresolved')

    def test_invalid_registry_and_mutating_issued_policy_are_rejected(self):
        bad = copy.deepcopy(self.registry); bad['entries'][0]['evidence'] = ''
        with self.assertRaises(ValueError): dnm.validate_registry(bad)
        bad = copy.deepcopy(self.policy); bad['execution']['calls']['summary']['system'] += 'changed'
        with self.assertRaises(ValueError): self.r.configure(policy=bad, expected_revision=1)
        with self.assertRaises(ValueError): self.r.approve({}, 1)

    def test_dnm_consumer_rejects_unapproved_and_wrong_binding(self):
        p = self.run_event()
        expr = {'field': 'entities', 'values': ['A']}
        row = dnm.consumer_row(dict(self.event['content'], source_fields=self.event['source_fields']), p, p['policy_version'], p['input_revision'])
        self.assertIsNone(TC.evaluate(expr, row))
        row['item_meta']['publishable'] = True  # fixture for an already-approved publication
        self.assertTrue(TC.evaluate(expr, row))
        row['_dnm_input_revision'] = 'changed'
        self.assertIsNone(TC.evaluate({'not': expr}, row))

    def test_fixed_evaluation_uses_the_same_validation_and_budget(self):
        self.bad = True
        llm = EX.restore(self.policy['execution'], lambda _: self.llm)
        llm.dnm_policy = self.policy
        out = dnm.evaluate_content(self.event['content'], llm)
        self.assertEqual(out['item_meta']['meta_status']['entities'], 'invalid_output')
        self.assertEqual(out['item_meta']['run_manifest']['attempts']['entities'], 3)
        self.assertEqual(out['item_meta']['policy_version'], self.policy['policy_version'])
        self.assertEqual(len(self.calls), 6)

    def test_no_input_and_unregistered_prefix_contract(self):
        event = copy.deepcopy(self.event); event['content'] = {'title': ' ', 'body': None}
        result = self.run_event(event)
        self.assertEqual(set(result['meta_status'].values()), {'insufficient_input'})
        self.assertEqual(self.calls, [])
        event = copy.deepcopy(self.event); event['source_fields']['item_unique_key'] = 'new-prefix-id'
        self.assertEqual(self.run_event(event)['source_key_status'], 'unregistered')

    def test_guarded_write_rejects_changed_control(self):
        expected = self.r._get('dnm_control')
        self.assertFalse(self.store.compare_report('dnm_test', None, {}, team='t', guard=('dnm_control', {})))
        self.assertTrue(self.store.compare_report('dnm_test', None, {}, team='t', guard=('dnm_control', expected)))

    def test_identical_text_from_different_sources_has_distinct_golden_identity(self):
        from prism.store import golden_hash
        rows = []
        for key in ('hamny-test-1', 'hamny-test-2'):
            event = copy.deepcopy(self.event); event['source_fields']['item_unique_key'] = key
            current = self.run_event(event)
            self.r.manual(key, 'summary', '확정', current['input_revision'], current['policy_version'], 'fixture')
            rows.append(self.r.golden(key))
        self.store.register_golden('t', rows, replace=False)
        self.assertEqual(len(self.store.get_golden('t')), 2)
        self.assertNotEqual(golden_hash(rows[0]), golden_hash(rows[1]))

    def test_dnm_evaluation_uses_selected_policy_without_default_model(self):
        from prism import serve, evalops
        current = self.run_event()
        for field, value in [('summary', '확정'), ('intent', []), ('entities', []), ('content_category', [])]:
            self.r.manual('hamny-test-1', field, value, current['input_revision'], current['policy_version'], 'fixture')
        self.store.register_golden('t', [self.r.golden('hamny-test-1')], replace=False)
        with patch.object(serve, 'get_store', return_value=self.store), \
             patch.object(serve, 'make_text_llm', side_effect=AssertionError('default model used')), \
             patch.object(serve, 'llm_for_model', return_value=(self.llm, 'fixture')), \
             patch.object(evalops, '_launch') as launch:
            result = evalops.eval_run_start(team='t', policy_version=self.policy['policy_version'])
        self.assertTrue(result.get('ok'), result)
        frozen = self.store.get_report('eval_snapshot_' + str(result['id']), team='t')
        self.assertEqual(frozen['model'], 'test')
        self.assertEqual(launch.call_args.args[2].dnm_policy, self.policy)

    def approved_fixture(self):
        """Synthetic evidence for the gate only; this is not a model quality evaluation."""
        from prism import evalops
        previous = copy.deepcopy(self.policy)
        for c in [previous['execution']['main']] + [v['client'] for v in previous['execution']['calls'].values()]:
            c['mock'] = False
        previous.pop('policy_version')
        previous['policy_version'] = dnm.CONTRACT + '+' + EX.digest(previous)[:20]
        self.r.configure(policy=previous, expected_revision=1)
        self.r.begin(self.event)
        policy = copy.deepcopy(previous)
        policy['execution']['calls']['summary']['system'] += '\nfixture revision'
        policy.pop('policy_version')
        policy['policy_version'] = dnm.CONTRACT + '+' + EX.digest(policy)[:20]
        self.r.configure(policy=policy, expected_revision=2)
        current = self.r.begin(self.event)
        self.r.manual('hamny-test-1', 'summary', '확정', current['input_revision'], current['policy_version'], 'fixture')
        row = self.r.golden('hamny-test-1')
        targets = {k: {'min': .5} for k in ('intent_f1', 'cat_hf1', 'ent_f1', 'summary_sim')}
        targets.update({k: {'max': 100} for k in ('cost_usd', 'latency_p95_ms', 'empty_rate')})
        protocol = {'sample_size': 1, 'targets': targets, 'tolerances': dict.fromkeys(targets, 0)}
        rid = self.store.eval_run_create('t', 'test', 'all', 1)
        snapshot = {'rows': [row], 'policy_version': policy['policy_version'], 'dnm_policy': policy, 'protocol': protocol}
        self.store.save_report('eval_snapshot_' + str(rid), snapshot, team='t')
        self.store.eval_run_update(rid, team='t', status='done', metrics={'basis_fingerprint': EX.digest(snapshot)})
        evidence = dict(protocol, evaluation_run=rid, policy_version=policy['policy_version'], source_registry_version='r1',
                        applies_at='2020-01-01T00:00:00+09:00', rollback_policy_version=previous['policy_version'], rollback_result='fixture')
        for role in ('platform_planning', 'datahub'):
            evidence[role] = {'owner': 'fixture', 'result_link': 'fixture', 'confirmed_at': '2020-01-01T00:00:00+09:00'}
        report = dict.fromkeys(targets, 1)
        report.update(evaluated=1, intent_n=1, cat_n=1, ent_n=1, summary_n=1)
        p = patch.object(evalops, 'eval_run_report', return_value=report)
        p.start(); self.addCleanup(p.stop)
        return evidence, report

    def test_approval_requires_measured_targets_and_rollback_is_current_input_only(self):
        evidence, report = self.approved_fixture()
        report['summary_sim'] = float('nan')
        with self.assertRaises(ValueError): self.r.approve(evidence, 3)
        report['summary_sim'] = .1
        with self.assertRaises(ValueError): self.r.approve(evidence, 3)
        report['summary_sim'] = 1
        wrong = dict(evidence, sample_size=2)
        with self.assertRaises(ValueError): self.r.approve(wrong, 3)
        self.r.approve(evidence, 3)
        current = self.r.current('hamny-test-1')
        self.assertTrue(current['publishable'])
        rolled = self.r.rollback('hamny-test-1', evidence['rollback_policy_version'], current['publication_revision'])
        self.assertGreater(rolled['publication_revision'], current['publication_revision'])
        self.assertEqual(rolled['policy_version'], evidence['rollback_policy_version'])
        self.assertEqual(self.r.current('hamny-test-1'), rolled)
        with self.assertRaises(ValueError): self.r.rollback('hamny-test-1', evidence['rollback_policy_version'], current['publication_revision'])
        event = copy.deepcopy(self.event); event.update(source_revision=2, event_id='e2'); event['content']['body'] = 'new input'
        changed = self.r.begin(event)
        with self.assertRaises(ValueError): self.r.rollback('hamny-test-1', evidence['rollback_policy_version'], changed['publication_revision'])
