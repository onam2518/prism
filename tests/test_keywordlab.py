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

    def test_delete_version_keeps_runs_blocks_adopted_and_never_reuses_number(self):
        run = self.run_case(count=2)
        for cell in run['cells'].values(): self.final(run, cell)
        K.reveal({'run_id': run['id']}, 'a')
        adopted, other = (s['version']['id'] for s in run['slots'])
        K.adopt({'run_id': run['id'], 'version_id': adopted}, 'a')
        self.assertFalse(K.action({'action': 'delete', 'version_id': adopted}, 'a', 'tester', True)['ok'])
        self.assertTrue(K.action({'action': 'delete', 'version_id': other}, 'a', 'tester', True)['ok'])
        self.assertNotIn(other, [v['id'] for v in K.catalog('a')['versions']])
        self.assertIn(other, [s['version']['id'] for s in K._get(K._key('run', run['id']), 'a')['slots']])
        with self.assertRaises(ValueError): K._version(other, 'a')
        self.assertFalse(K.action({'action': 'delete', 'version_id': other}, 'a', 'tester', True)['ok'])
        self.assertEqual(self.version('새 버전')['number'], 3)

    def test_export_csv_rows_body_blind_and_formula_guard(self):
        import csv, io
        self.store.upsert_golden('g1', {'title': '=HYPERLINK("x")', 'body': '본문 원문\n둘째 줄', 'source_url': 'https://example.com/a'},
                                 {'summary': '한국은행이 기준금리를 인하했다', 'entities': ['한국은행', '이창용'], 'intent': ['속보'], 'content_category': ['경제']})
        versions = [self.version(str(i)) for i in range(2)]
        with patch.object(K.threading, 'Thread'):
            rid = K.start({'slots': [{'model': v['model'], 'version_id': v['id']} for v in versions], 'sample': 1}, 'a', 'tester')['id']
        with patch.object(E, '_llm', return_value=(None, True, '')): K._run(rid, 'a')
        run = K._get(K._key('run', rid), 'a')
        cell = sorted(run['cells'].values(), key=lambda c: c['slot'])[0]
        kws = cell['refined']['keywords']
        K.review({'run_id': rid, 'cell_id': cell['id'], 'expected_revision': 0, 'no_keywords': len(kws) < 2,
                  'judgments': [{'verdict': 'exclude', 'reason': '중복'}] + [{'verdict': 'accept'} for _ in kws[1:]]}, 'a', 'tester', True)
        raw = K.export_csv(rid, 'a').decode('utf-8')
        self.assertTrue(raw.startswith('\ufeff'))
        rows = list(csv.DictReader(io.StringIO(raw[1:])))
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first['제목'], "'=HYPERLINK(\"x\")")          # 수식 인젝션 중화
        self.assertEqual(first['본문'], '본문 원문\n둘째 줄')
        self.assertEqual(first['원문 URL'], 'https://example.com/a')               # 콘텐츠 행이 없으면 정답셋 원문의 링크
        self.assertEqual(K.run_detail(rid, 'a')['run']['sources'][run['items'][0]['hash']], 'https://example.com/a')
        self.assertEqual(first['엔티티'], '한국은행 | 이창용')
        self.assertEqual(first['모델·버전'], '비공개')
        self.assertEqual(first['핵심 키워드'], ' | '.join(k['text'] for k in kws))
        self.assertEqual(first['검수 상태'], '판정됨')
        self.assertIn(kws[0]['text'] + ' → 제외 (중복)', first['판정 사유'])
        self.assertEqual(rows[1]['검수 상태'], '미검수')
        with self.assertRaises(ValueError): K.export_csv(rid, 'b')

    def test_restart_orphaned_running_run_is_interrupted_and_still_usable(self):
        run = self.run_case(count=2)
        key = K._key('run', run['id'])
        for cell in run['cells'].values(): self.final(run, cell)
        def orphan(r):                                   # 재시작: running 기록만 남고 스레드 없음 · B 칸은 생성 전 중단
            r['status'] = 'running'; r['cells'].pop(next(c for c in r['cells'] if c.endswith(':B')))
        K._update(key, 'a', orphan)
        self.assertNotIn(('a', run['id']), K.ACTIVE)
        K.reveal({'run_id': run['id']}, 'a')
        saved = K._get(key, 'a')
        self.assertEqual((saved['status'], saved['revealed']), ('interrupted', True))
        a, b = ({s['label']: s['version']['id'] for s in saved['slots']}[x] for x in 'AB')
        with self.assertRaisesRegex(ValueError, '최종 확정'): K.adopt({'run_id': run['id'], 'version_id': b}, 'a')
        self.assertTrue(K.adopt({'run_id': run['id'], 'version_id': a}, 'a')['ok'])

    def test_delete_run_blocks_gold_adopted_running_and_clears(self):
        run = self.run_case(count=1)
        cell = next(iter(run['cells'].values()))
        self.final(run, cell)
        gold = K.confirm_gold({'run_id': run['id'], 'cell_id': cell['id']}, 'a', 'tester', True)['gold']
        self.assertFalse(K.action({'action': 'delete_run', 'run_id': run['id']}, 'a', 'tester', True)['ok'])
        other = self.run_case(count=1)
        self.assertFalse(K.action({'action': 'delete_run', 'run_id': other['id']}, 'a', 'tester')['ok'])   # 관리 권한
        with K.LOCK: K.ACTIVE.add(('a', other['id']))
        self.assertFalse(K.action({'action': 'delete_run', 'run_id': other['id']}, 'a', 'tester', True)['ok'])
        with K.LOCK: K.ACTIVE.discard(('a', other['id']))
        self.assertTrue(K.action({'action': 'delete_run', 'run_id': other['id']}, 'a', 'tester', True)['ok'])
        self.assertNotIn(other['id'], [r['id'] for r in K.catalog('a')['runs']])
        with self.assertRaises(ValueError): K.run_detail(other['id'], 'a')
        self.assertIsNone(self.store.get_report('keyword_lab_run_' + other['id'], 'a'))   # 빈 행을 남기지 않는다
        self.assertFalse(K.action({'action': 'delete_run', 'run_id': other['id']}, 'b', 'tester', True)['ok'])
        self.assertEqual(K.catalog('a')['gold'][0]['id'], gold['id'])

    def test_explicit_model_version_and_management_permissions(self):
        v = self.version()
        for slots in ([],[{'model':'solar-pro3'}],[{'model':'wrong','version_id':v['id']}], [{'model':v['model'],'version_id':v['id']}]*2):
            self.assertFalse(K.action({'action':'start','slots':slots,'meta':self.meta},'a','tester',True)['ok'])
        for action in ('version','delete','start','compile','adopt'):
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


class SentenceLabTest(unittest.TestCase):
    setUp = KeywordLabTest.setUp
    version = KeywordLabTest.version
    run_case = KeywordLabTest.run_case
    final = KeywordLabTest.final
    def sentence_run(self, body=None, versions=1):
        vs=[K.create_version({'target':'sentence','model':'solar-pro3','rules':E.SENT_RULES,'title':'문장 '+str(i)},'a','tester')['version'] for i in range(versions)]
        request={'target':'sentence','slots':[{'model':v['model'],'version_id':v['id']} for v in vs],
                 'meta':self.meta,'keyword_source':'manual','keywords':['한국은행 기준금리 인하']}
        request.update(body or {})
        with patch.object(K.threading,'Thread'):
            rid=K.start(request,'a','tester')['id']
        with patch.object(E,'_llm',return_value=(None,True,'')), patch.object(E,'refine',side_effect=AssertionError('sentence must not regenerate keywords')):
            K._run(rid,'a')
        return K._get(K._key('run',rid),'a')

    def sentence_final(self, run, partition='development'):
        cell=next(iter(run['cells'].values()))
        b={'run_id':run['id'],'cell_id':cell['id'],'expected_revision':cell['review_revision'],
           'judgments':[{'verdict':'edit','reason':'핵심 사건이 드러나도록 수정','corrected':'한국은행이 기준금리를 인하했다.'}],
           'finalize':True,'partition':partition}
        K.review(b,'a','tester',True)
        return b

    def test_sentence_three_versions_freeze_keywords_and_preserve_system(self):
        run=self.sentence_run(versions=3)
        self.assertEqual(len(run['cells']),3)
        self.assertTrue(all(c['status']=='done' and c['sentence']['from_keywords'] for c in run['cells'].values()))
        self.assertEqual(run['items'][0]['keywords'],[{'text':'한국은행 기준금리 인하','kind':'single'}])
        self.assertEqual(run['slots'][0]['version']['system'],E._system('sentence',{'sentence':{'rules':E.SENT_RULES}},'solar-pro3'))
        self.assertEqual([v['version']['number'] for v in sorted(run['slots'],key=lambda s:s['version']['number'])],[1,2,3])

    def test_sentence_gold_and_compiler_are_separate_from_keywords(self):
        keyword_run=self.run_case(1); cell=next(iter(keyword_run['cells'].values()))
        keyword_gold=K.confirm_gold(self.final(keyword_run,cell),'a','tester',True)['gold']
        run=self.sentence_run();gold=K.confirm_gold(self.sentence_final(run),'a','tester',True)['gold']
        self.assertNotEqual(gold['item_key'],keyword_gold['item_key'])
        self.assertIn('sentences',gold);self.assertNotIn('keywords',gold)
        self.assertEqual(len(K.catalog('a')['gold']),2)
        vid=run['slots'][0]['version']['id']
        with self.assertRaisesRegex(ValueError,'개발용'):
            K.compile_proposal({'version_id':vid,'compiler_model':'solar-pro3','gold_keys':[keyword_gold['item_key']]},'a','tester')
        with patch.object(E,'_llm',return_value=(None,True,'')):
            p=K.compile_proposal({'version_id':vid,'compiler_model':'solar-pro3','gold_keys':[gold['item_key']]},'a','tester')['proposal']
        stored=K._get(K._key('proposal',p['id']),'a')
        self.assertEqual(stored['input']['schema'],E.SENT_SCHEMA)
        self.assertEqual(stored['input']['reviews'][0]['input_keywords'],run['items'][0]['keywords'])
        self.assertIn('핵심 문장',stored['system'])
        self.assertEqual(p['target'],'sentence')
        v=K.create_version({'target':'sentence','model':p['model'],'title':'문장 개선','rules':p['rules'],'parent_id':vid,'proposal_id':p['id']},'a','tester')['version']
        self.assertEqual(v['training_keys'],[gold['item_key']])

    def test_sentence_failed_draft_can_be_corrected_but_not_accepted(self):
        run=self.sentence_run();cid=next(iter(run['cells']))
        def corrupt(r):
            r['cells'][cid]['status']='failed'
            r['cells'][cid]['sentence']={'draft':'가'*150,'error':'too long'}
        K._update(K._key('run',run['id']),'a',corrupt)
        b={'run_id':run['id'],'cell_id':cid,'expected_revision':0,'judgments':[{'verdict':'accept'}],'finalize':True}
        with self.assertRaisesRegex(ValueError,'길이'):K.review(b,'a','tester',True)
        b['judgments']=[{'verdict':'edit','reason':'중복 정보 축약','corrected':'한국은행이 기준금리를 인하했다.'}]
        K.review(b,'a','tester',True)
        self.assertEqual(K.confirm_gold(b,'a','tester',True)['gold']['sentences'][0]['text'],'한국은행이 기준금리를 인하했다.')

    def test_sentence_keyword_sources_and_target_mismatch(self):
        kwrun=self.run_case(1)
        with self.assertRaisesRegex(ValueError,'공개된'):
            K._prepare_items({'source_run':kwrun['id'],'keyword_source':'run','keyword_slot':'A'},'a','sentence')
        for c in kwrun['cells'].values():self.final(kwrun,c)
        K.reveal({'run_id':kwrun['id']},'a')
        items=K._prepare_items({'source_run':kwrun['id'],'keyword_source':'run','keyword_slot':'A'},'a','sentence')
        self.assertEqual(items[0]['keywords'],next(iter(kwrun['cells'].values()))['refined']['keywords'])
        with self.assertRaisesRegex(ValueError,'실험 대상'):
            K.start({'target':'sentence','slots':[{'model':kwrun['slots'][0]['version']['model'],'version_id':kwrun['slots'][0]['version']['id']}],'meta':self.meta},'a','tester')
        run=self.sentence_run({'keyword_source':'none'})
        self.assertFalse(next(iter(run['cells'].values()))['sentence']['from_keywords'])
        with self.assertRaisesRegex(ValueError,'입력 방식'):
            K._prepare_items({'meta':self.meta},'a','sentence')

    def test_sentence_evaluation_cannot_enter_improvement(self):
        run=self.sentence_run();gold=K.confirm_gold(self.sentence_final(run,'evaluation'),'a','tester',True)['gold']
        with self.assertRaisesRegex(ValueError,'개발용'):
            K.compile_proposal({'version_id':run['slots'][0]['version']['id'],'compiler_model':'solar-pro3','gold_keys':[gold['item_key']]},'a','tester')

    def test_sentence_training_and_adoption_do_not_overwrite_keyword_state(self):
        kwrun=self.run_case(1)
        for c in kwrun['cells'].values():self.final(kwrun,c)
        K.reveal({'run_id':kwrun['id']},'a')
        kwvid=kwrun['slots'][0]['version']['id']
        K.adopt({'run_id':kwrun['id'],'version_id':kwvid},'a')
        run=self.sentence_run();b=self.sentence_final(run)
        g=K.confirm_gold(b,'a','tester',True)['gold']
        vid=run['slots'][0]['version']['id']
        with patch.object(E,'_llm',return_value=(None,True,'')):
            K.compile_proposal({'version_id':vid,'compiler_model':'solar-pro3','gold_keys':[g['item_key']]},'a','tester')
        K.reveal({'run_id':run['id']},'a')
        K.adopt({'run_id':run['id'],'version_id':vid},'a')
        self.assertEqual(K.catalog('a')['active']['solar-pro3']['version_id'],kwvid)
        self.assertEqual(K.catalog('a')['active']['sentence|solar-pro3']['version_id'],vid)
        b.update(expected_revision=1,partition='evaluation',expected_gold_id=g['id'])
        K.review(b,'a','tester',True)
        with self.assertRaisesRegex(ValueError,'개선에 사용'):K.confirm_gold(b,'a','tester',True)

    def test_blind_reveal_needs_manage_and_hides_cost_signals(self):
        run = self.run_case()
        for cell in run['cells'].values():
            self.final(run, cell)
        hidden = K.run_detail(run['id'], 'a')['run']
        for m in hidden['metrics']:                        # 비용·지연·토큰은 모델 추정 단서 → 공개 전엔 가림
            self.assertEqual((m['avg_ms'], m['tokens'], m['cost_usd']), (None, None, None))
            self.assertEqual(m['completed'], 1)            # 건수·판정은 유지
        for cell in hidden['cells'].values():
            self.assertFalse({'tokens', 'elapsed_ms', 'latency_ms', 'actual_model'} & set(cell))
        denied = K.action({'action': 'reveal', 'run_id': run['id']}, 'a', 'tester', can_manage=False)
        self.assertFalse(denied['ok'])
        self.assertFalse(K.run_detail(run['id'], 'a')['run']['revealed'])
        self.assertTrue(K.action({'action': 'reveal', 'run_id': run['id']}, 'a', 'tester', can_manage=True)['ok'])
        shown = K.run_detail(run['id'], 'a')['run']
        self.assertIsNotNone(shown['metrics'][0]['avg_ms'])
        self.assertIn('elapsed_ms', next(iter(shown['cells'].values())))

    def test_engine_built_once_per_model_per_run(self):
        with patch.object(K, '_engine', wraps=K._engine) as eng:
            run = self.run_case(count=3)                   # 3조합 · 같은 모델
        self.assertEqual(len(run['cells']), 3)
        self.assertEqual(eng.call_count, 1)

    def test_concurrent_workers_save_every_cell(self):
        """작업자 여럿이 같은 실험 문서를 저장해도 프로세스 안에선 줄 서서 CAS 충돌·유실이 없다."""
        import threading, time as _t
        key = K._key('run', K._id())
        K._update(key, 'a', lambda r: r.update(cells={}), {})
        real_get, real_cas, misses = self.store.get_report, self.store.compare_report, []
        def slow_get(*a, **kw):
            v = real_get(*a, **kw)
            _t.sleep(0.002)                                # 읽기~CAS 사이 틈을 넓혀 경쟁을 드러낸다
            return v
        def cas(*a, **kw):
            ok = real_cas(*a, **kw)
            if not ok: misses.append(1)
            return ok
        def worker(w):
            for i in range(8):
                K._update(key, 'a', lambda r, c='%d-%d' % (w, i): r['cells'].update({c: 1}))
        with patch.object(self.store, 'get_report', slow_get), patch.object(self.store, 'compare_report', cas):
            ths = [threading.Thread(target=worker, args=(w,)) for w in range(4)]
            [t.start() for t in ths]
            [t.join(30) for t in ths]
        self.assertEqual(len(K._get(key, 'a')['cells']), 32)
        self.assertEqual(misses, [])
