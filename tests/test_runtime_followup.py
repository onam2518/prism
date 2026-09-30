"""운영 전환 후 전체 경로 검사에서 발견한 집계·정답 결속·평가 회귀."""
import copy
import tempfile
import unittest
import unicodedata
from pathlib import Path
from unittest.mock import patch

from prism import serve as S, reviewops as R, learnops as L, crewops as C
from prism import dashboard, topic, entconf as EC, evalops as E, execution as EX, dnm
from prism.store import Store, content_hash, golden_hash


class RuntimeFollowup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = Store(str(Path(self.tmp.name) / 'db'))
        self.addCleanup(self.st._conn().close)
        p = patch.object(S, 'get_store', return_value=self.st)
        p.start(); self.addCleanup(p.stop)
        S._AGG_CACHE.clear()
        self.addCleanup(S._AGG_CACHE.clear)

    def dnm_rows(self):
        return [{'content': {'title': '같은 제목', 'body': '같은 본문',
                             'source_fields': {'item_unique_key': 'hamny-' + key}},
                 'expected': {'summary': key, 'contract_version': dnm.CONTRACT,
                              'input_revision': 'input', 'policy_version': 'policy'}} for key in ('a', 'b')]

    def test_object_and_legacy_categories_share_coverage(self):
        for i in range(8):
            cats = ['Sports'] if i < 4 else [{'tier1': 'Sports', 'tier2': None}]
            self.st.register_golden(None, [{'content': {'title': str(i), 'body': 'b'},
                                           'expected': {'content_category': cats}}], replace=False)
        self.assertNotIn('Sports', R._lack_classes())
        cov = {r['cls']: r for r in L.learn_data()['coverage']}
        self.assertEqual(cov['Sports']['have'], 8)
        self.assertEqual(cov['Sports']['lack'], 0)

    def test_object_categories_drive_crew_and_topic_domain(self):
        row = {'content_ref': {'title': 't', 'body': 'b'}, 'item_meta': {'content_category': [
            {'tier1': 'Sports', 'tier2': 'Soccer (International)'},
            {'tier1': 'Sports', 'tier2': None}, 'Books']}}
        with patch.object(S, 'results_rows', return_value=[row]):
            cats = C.content_categories()
        self.assertEqual(cats[content_hash(row['content_ref'])], ['Sports', 'Books'])
        self.assertEqual(topic._rep_category([row], [0]), 'Sports')
        self.assertEqual(dashboard.tier1_remap({'tier1': 'Sports', 'tier2': None}), 'Sports')

    def test_gold_origin_cache_binds_requested_hashes_and_store(self):
        class Remote:
            REMOTE = True
            def __init__(self): self.calls = []
            def origin_meta_for(self, hashes, team=None):
                self.calls.append((hashes, team))
                return dict.fromkeys(hashes, {'model': 'fixture'})
        st = Remote()
        self.assertEqual(set(R._gold_origin_meta(st, ['a'], 't')), {'a'})
        self.assertEqual(set(R._gold_origin_meta(st, ['a', 'b'], 't')), {'a', 'b'})
        R._gold_origin_meta(st, ['b', 'a', 'a'], 't')
        self.assertEqual(len(st.calls), 2)
        other = Remote()
        R._gold_origin_meta(other, ['a', 'b'], 't')
        self.assertEqual(len(other.calls), 1)
        S._agg_bump()
        R._gold_origin_meta(other, ['a', 'b'], 't')
        self.assertEqual(len(other.calls), 2)

    def test_confirmed_empty_gold_is_not_replaced_by_original_output(self):
        expected = {'summary': '', 'entities': [], 'intent': [], 'content_category': []}
        original = {'item_meta': {'summary': 'old', 'entities': ['old'], 'intent': ['old'],
                                 'content_category': ['Sports']}}
        self.assertEqual(R.gold_shown_meta(expected, original), expected)
        self.assertEqual(R.gold_shown_meta({}, original), original['item_meta'])

    def test_dnm_golden_scope_and_knowhow_do_not_join_same_body(self):
        rows = self.dnm_rows()
        legacy = {'content': {'title': '같은 제목', 'body': '같은 본문'}, 'expected': {'summary': 'legacy'}}
        self.st.register_golden(None, [legacy] + rows)
        self.st.save_feedback(content_hash(legacy['content']), '', '같은 제목', 'good', 'extract',
                              'legacy note', 1, reviewer='r')
        selected = L._scope_golden([legacy] + rows, 'all', self.st, hashes=[golden_hash(rows[1])])
        self.assertEqual(selected, rows[1:])
        exported = {r['hash']: r for r in L.knowhow_rows()}
        self.assertEqual(len(exported), 3)
        self.assertTrue(exported[golden_hash(legacy)]['opinions'])
        for row in rows:
            self.assertEqual(exported[golden_hash(row)]['opinions'], [])
            self.assertEqual(exported[golden_hash(row)]['revisions'], [])

    def rubric_run(self):
        rows = self.dnm_rows()
        rid = self.st.eval_run_create(None, 'fixture', 'all', len(rows))
        snapshot = {'rows': copy.deepcopy(rows)}
        self.st.save_report('eval_snapshot_' + str(rid), snapshot)
        self.st.eval_run_update(rid, status='done', metrics={'basis_fingerprint': EX.digest(snapshot)})
        results = [{'hash': golden_hash(r), 'expected': r['expected'], 'got': {'summary': 'out'}} for r in rows]
        self.st.eval_results_add(rid, results)
        return rid, results

    def test_rubric_uses_frozen_inputs_after_golden_removed(self):
        rid, results = self.rubric_run()
        seen = []
        def judge(llm, items):
            seen.extend(items)
            return {i['id']: dict.fromkeys(E.RUBRIC_AXES, 4) for i in items}
        with patch.object(E, '_judge_batch', side_effect=judge):
            E._rubric_loop(rid, results, None, None)
        self.assertEqual(len(seen), 2)
        self.assertEqual({i['input'] for i in seen}, {'같은 제목\n같은 본문'})
        self.assertEqual(len({i['id'] for i in seen}), 2)
        run = self.st.eval_run_get(rid)
        self.assertEqual(run['rubric_status'], 'done')
        self.assertEqual(run['rubric']['n'], 2)

    def test_rubric_rejects_changed_snapshot(self):
        rid, results = self.rubric_run()
        self.st.save_report('eval_snapshot_' + str(rid), {'rows': []})
        with patch.object(E, '_judge_batch') as judge:
            E._rubric_loop(rid, results, None, None)
        judge.assert_not_called()
        self.assertEqual(self.st.eval_run_get(rid)['rubric_status'], 'failed')

    def test_entity_scoring_preserves_duplicates_unicode_and_rank(self):
        ref = {'title': unicodedata.normalize('NFD', '삼성전자 소식'),
               'body': '삼성전자와 ACME 소식. ' * 100}
        im = {'summary': '삼성전자 ACME', 'entities': [
            {'name': '삼성전자', 'type': 'OG'}, '삼성 전자', 'ACME', 'acme', '미등장', ' ']}
        expected = [{'name': name, 'conf': EC.entity_confidence(name, ref, im)}
                    for name in ('삼성전자', '삼성 전자', 'ACME', 'acme', '미등장', ' ')]
        with patch.object(EC, '_norm', wraps=EC._norm) as normalize:
            actual = EC.scored_entities(im, ref)
        self.assertEqual(actual, expected)
        self.assertEqual(sum(c.args == (ref['body'],) for c in normalize.call_args_list), 1)
