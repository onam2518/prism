import copy
import tempfile
import types
import unittest
from unittest.mock import patch
from prism import keywordlab as K, entrefine as E
from prism.store import Store


class KeywordLabTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name + '/t.db')
        self.sv = types.SimpleNamespace(get_store=lambda: self.store, _report_get=lambda k,t=None,d=None:self.store.get_report(k,t) or d)
        for module in (K, E):
            p = patch.object(module, '_SV', self.sv); p.start(); self.addCleanup(p.stop)
        self.meta = {'summary': '한국은행이 기준금리를 인하했다', 'entities': ['이창용', '한국은행'], 'intent': [], 'content_category': []}

    def version(self, title='기준'):
        return K.create_version({'model': 'solar-pro3', 'rules': '메타에 근거한 키워드 최대 3개', 'title': title}, 'a', 'tester')['version']

    def run_case(self, count=3, fail=False):
        versions = [self.version(str(i)) for i in range(count)]
        with patch.object(K.threading, 'Thread'):
            rid = K.start({'slots': [{'model': v['model'], 'version_id': v['id']} for v in versions], 'meta': self.meta}, 'a', 'tester')['id']
        with patch.object(E, '_llm', return_value=(None, True, '')):
            if fail:
                original = E.refine
                calls = []
                def broken(*args):
                    calls.append(1)
                    if len(calls) == 1: raise ValueError('one slot failed')
                    return original(*args)
                with patch.object(E, 'refine', side_effect=broken): K._run(rid, 'a')
            else: K._run(rid, 'a')
        return K._get(K._key('run', rid), 'a')

    def final(self, run, cell, partition='development'):
        b = {'run_id': run['id'], 'cell_id': cell['id'], 'expected_revision': cell['review_revision'],
             'judgments': [{'verdict': 'accept'} for _ in cell['refined']['keywords']],
             'no_keywords': not cell['refined']['keywords'], 'finalize': True, 'partition': partition}
        K.review(b, 'a', 'tester', True)
        return b

    def test_three_combinations_frozen_input_blind_and_independent_failure(self):
        run = self.run_case(fail=True)
        self.assertEqual(run['status'], 'done')
        self.assertEqual(len(run['cells']), 3)
        self.assertEqual(sum(c['status'] == 'failed' for c in run['cells'].values()), 1)
        hidden = K.run_detail(run['id'], 'a')['run']
        self.assertTrue(all('model' not in s['version'] for s in hidden['slots']))
        with self.assertRaises(ValueError): K.run_detail(run['id'], 'b')
        with self.assertRaises(ValueError): K.reveal({'run_id':run['id']}, 'a')
        for cell in run['cells'].values():
            if cell['status'] == 'done': self.final(run, cell)
        K.reveal({'run_id':run['id']}, 'a')
        self.assertTrue(K.run_detail(run['id'], 'a')['run']['revealed'])
        self.assertEqual(K._items({'source_run':run['id']}, 'a'), run['items'])

    def test_review_reason_revision_permissions_and_separate_gold(self):
        run = self.run_case(1); cell = next(iter(run['cells'].values()))
        bad = {'run_id':run['id'], 'cell_id':cell['id'], 'expected_revision':0,
               'judgments':[{'verdict':'edit','corrected':'한국은행'} for _ in cell['refined']['keywords']]}
        with self.assertRaisesRegex(ValueError, '사유'): K.review(bad, 'a', 'x', True)
        b = self.final(run, cell)
        with self.assertRaisesRegex(ValueError, '다른 검수'): K.review(b, 'a', 'x', True)
        with self.assertRaisesRegex(ValueError, '권한'): K.confirm_gold(b, 'a', 'x', False)
        g = K.confirm_gold(b, 'a', 'x', True)['gold']
        self.assertEqual(self.store.get_golden('a'), [])
        self.assertEqual(K.catalog('b')['gold'], [])
        self.assertEqual(g['keywords'][0]['text'], '한국은행')
        with self.assertRaisesRegex(ValueError, '기존 정답'): K.confirm_gold(b, 'a', 'x', True)

    def test_compile_loop_lineage_and_evaluation_isolation(self):
        run = self.run_case(1); cell = next(iter(run['cells'].values())); b = self.final(run, cell)
        gold = K.confirm_gold(b, 'a', 'tester', True)['gold']
        vid = run['slots'][0]['version']['id']
        with patch.object(E, '_llm', return_value=(None, True, '')):
            p = K.compile_proposal({'version_id':vid,'compiler_model':'solar-pro3','gold_keys':[gold['item_key']]},'a','tester')['proposal']
        v = K.create_version({'model':'solar-pro3','rules':p['rules'],'title':'개선','parent_id':vid,'proposal_id':p['id']},'a','tester')['version']
        self.assertEqual(v['training_keys'], [gold['item_key']])
        b.update(expected_revision=1, partition='evaluation')
        K.review(b, 'a', 'tester', True)
        b['expected_gold_id'] = gold['id']
        with self.assertRaisesRegex(ValueError, '개선에 사용'): K.confirm_gold(b,'a','tester',True)
        self.assertEqual(K._version(vid,'a')['rules'], '메타에 근거한 키워드 최대 3개')

    def test_evaluation_gold_never_compiled(self):
        run = self.run_case(1); cell = next(iter(run['cells'].values()))
        b = self.final(run,cell,'evaluation'); gold = K.confirm_gold(b,'a','tester',True)['gold']
        with self.assertRaisesRegex(ValueError,'개발용'):
            K.compile_proposal({'version_id':run['slots'][0]['version']['id'],'compiler_model':'solar-pro3','gold_keys':[gold['item_key']]},'a','tester')

    def test_explicit_model_version_and_management_permissions(self):
        v = self.version()
        for slots in ([],[{'model':'solar-pro3'}],[{'model':'wrong','version_id':v['id']}], [{'model':v['model'],'version_id':v['id']}]*2):
            self.assertFalse(K.action({'action':'start','slots':slots,'meta':self.meta},'a','tester',True)['ok'])
        for action in ('version','start','compile','adopt'):
            self.assertFalse(K.action({'action':action},'a','tester')['ok'])

    def test_provider_selection_is_preserved(self):
        cfg = types.SimpleNamespace(prices=types.SimpleNamespace())
        self.sv.IMG = types.SimpleNamespace(is_router=lambda p:p=='timely', router_key=lambda p:'test-key')
        self.sv.Config = types.SimpleNamespace(load=lambda:cfg)
        self.sv.MM = types.SimpleNamespace(prices=lambda m:(1,2,3))
        self.sv.make_text_llm = lambda c,mock: types.SimpleNamespace(model='gpt-test', provider=c.text_provider)
        llm, mock, error = K._engine('timely|gpt-test')
        self.assertEqual((llm.provider,cfg.text_model,mock,error),('timely','gpt-test',False,''))
        self.assertIsNone(K._engine('unknown|gpt-test')[0])
        with patch.object(E, '_llm', return_value=(None, False, '')) as direct:
            K._engine('solar|solar-pro4-260806')
            direct.assert_called_once_with('solar-pro4-260806')

    def test_gold_write_guard_prevents_stale_finalization(self):
        run = self.run_case(1); cell = next(iter(run['cells'].values())); b = self.final(run,cell)
        original = self.store.compare_report
        def conflict(kind, expected, payload, team=None, guard=None):
            if kind == K.CATALOG and guard:
                changed = copy.deepcopy(guard[1]); changed['revision'] += 1
                original(guard[0],guard[1],changed,team=team)
            return original(kind,expected,payload,team=team,guard=guard)
        with patch.object(self.store,'compare_report',side_effect=conflict):
            with self.assertRaisesRegex(ValueError,'동시에'): K.confirm_gold(b,'a','tester',True)
        self.assertEqual(K.catalog('a')['gold'],[])

    def test_all_excluded_requires_explicit_no_keywords(self):
        cell = {'refined':{'keywords':[{'text':'한국은행','kind':'single'}]}}
        b = {'judgments':[{'verdict':'exclude','reason':'범위 부적합'}]}
        with self.assertRaisesRegex(ValueError,'명시적으로'): K._review_payload(b,cell,{})
        b['no_keywords'] = True
        self.assertEqual(K._review_payload(b,cell,{})['keywords'],[])


class KeywordClientTest(unittest.TestCase):
    def test_client(self):
        import subprocess, shutil
        if not shutil.which('node'): self.skipTest('node unavailable')
        subprocess.run(['node', 'tests/test_keywordlab_client.js'], check=True, capture_output=True)
