"""운영 검수: 조치 누락·동시 저장·부분 저장·보류·이월·시간 분리의 회귀 검증."""
import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from prism import opsreview as O
from prism.store import Store, content_hash


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st=Store(os.path.join(self.tmp.name,'test.db'))
        content={'displayServiceName':'뉴스','title':'운영 검수','subtitle':'','body':'본문'}
        self.ch=content_hash(content)
        im={'summary':'리드문','entities':[],'intent':[],'content_category':['Sports']}
        payload={'content_ref':content,'item_meta':im,'quality_meta':{'finalGrade':'G'},'trace':{'model':'test','version':1}}
        c=self.st._conn()
        c.execute('INSERT INTO results(content_hash,service,title,item_meta,payload,final_grade,created_at) VALUES(?,?,?,?,?,?,?)',
                  (self.ch,'뉴스',content['title'],json.dumps(im),json.dumps(payload),'G',1));c.commit()
    def request(self,action,**kw):
        detail=O.detail(self.st,self.ch,who='me')
        return O.action(self.st,dict(action=action,hash=self.ch,revision=detail['revision'],basis_token=detail['basis']['token'],**kw),who='me',privileged=True,final=True)
    def axes(self,status='accurate'):
        return {k:{'status':status,'reason':'보류 근거' if status=='hold' else '', 'proposal':'수정 제안' if status=='needs_fix' else ''} for k in O.FIELDS}
    def test_status_specific_fields_are_validated_and_persisted(self):
        axes=self.axes()
        axes['summary']={'status':'needs_fix','reason':'숨겨진 보류 사유','proposal':'리드문 수치 수정'}
        axes['entities']={'status':'accurate','reason':'예전 사유','proposal':'예전 제안'}
        result=self.request('review',axes=axes)
        self.assertTrue(result['ok'],result)
        stored=self.st.get_report('ops_review_'+self.ch)['reviews'][-1]['axes']
        self.assertEqual(stored['summary']['reason'],'')
        self.assertEqual(stored['summary']['proposal'],'리드문 수치 수정')
        self.assertEqual(stored['entities']['reason'],'')
        self.assertEqual(stored['entities']['proposal'],'')
        self.assertIn('리드문 수치 수정',result['cases'][0]['reason'])
        self.assertNotIn('숨겨진',result['cases'][0]['reason'])
        note=self.st._conn().execute('SELECT note FROM feedback WHERE content_hash=?',(self.ch,)).fetchone()[0]
        self.assertIn('리드문 수치 수정',note)
        axes['summary']={'status':'hold','reason':'원문 수치 확인 필요','proposal':'숨겨진 수정 제안'}
        self.assertTrue(self.request('review',axes=axes)['ok'])
        stored=self.st.get_report('ops_review_'+self.ch)['reviews'][-1]['axes']['summary']
        self.assertEqual(stored['reason'],'원문 수치 확인 필요')
        self.assertEqual(stored['proposal'],'')

    def test_inactive_text_does_not_satisfy_required_field(self):
        for status,reason,proposal in [('needs_fix','남은 보류 사유',''),('hold','','남은 수정 제안')]:
            axes=self.axes();axes['summary']={'status':status,'reason':reason,'proposal':proposal}
            self.assertFalse(self.request('review',axes=axes)['ok'])
            self.assertIsNone(self.st.get_report('ops_review_'+self.ch))

    def test_review_lifecycle_persists_history_feedback_and_case_until_explicit_close(self):
        axes=self.axes();axes['summary']={'status':'needs_fix','proposal':'날짜 수정','reason':'숨긴 보류'}
        bad=self.request('review',axes=axes);self.assertTrue(bad['ok'],bad)
        case_id=bad['cases'][0]['id']
        stored=self.st.get_report('ops_review_'+self.ch)
        self.assertEqual(stored['history'][-1]['data']['axes']['summary']['reason'],'')
        self.assertEqual(self.st.feedback_map()[self.ch]['verdict'],'bad')
        self.assertIn('날짜 수정',' '.join(self.st.learned_by_stage().values()))
        stale=copy.deepcopy(bad)
        axes['summary']={'status':'hold','reason':'원문 날짜 재확인','proposal':'숨긴 수정'}
        hold=self.request('review',axes=axes);self.assertTrue(hold['ok'],hold)
        self.assertEqual(len(hold['cases']),1)
        self.assertEqual(hold['cases'][0]['state'],'hold')
        self.assertNotIn(self.ch,self.st.feedback_map())
        self.assertNotIn('날짜 수정',' '.join(self.st.learned_by_stage().values()))
        accurate=self.request('review',axes=self.axes());self.assertTrue(accurate['ok'],accurate)
        self.assertEqual(self.st.feedback_map()[self.ch]['verdict'],'good')
        self.assertNotEqual(accurate['cases'][0]['state'],'closed')
        rejected=O.action(self.st,dict(action='review',hash=self.ch,revision=stale['revision'],basis_token=stale['basis']['token'],axes=axes),who='me')
        self.assertFalse(rejected['ok'])
        before=self.st.get_report('ops_review_'+self.ch)
        self.assertFalse(self.request('case',case_id=case_id,state='closed')['ok'])
        self.assertEqual(self.st.get_report('ops_review_'+self.ch),before)
        closed=self.request('case',case_id=case_id,state='closed',recheck_evidence='날짜 대조 완료',service_required=False,service_waiver_reason='서비스 미반영 검수 사례')
        self.assertTrue(closed['ok'],closed)
        self.assertEqual(closed['cases'][0]['state'],'closed')
        self.assertTrue(self.request('undo')['ok'])
        self.assertNotIn(self.ch,self.st.feedback_map())
        self.assertEqual(O.detail(self.st,self.ch,who='me')['cases'][0]['state'],'closed')

    def test_report_failure_rolls_back_feedback_and_review(self):
        self.st._conn().execute("CREATE TRIGGER review_fail BEFORE INSERT ON reports WHEN NEW.kind LIKE 'ops_review_%' BEGIN SELECT RAISE(ABORT,'test report failure'); END")
        with self.assertRaises(Exception):self.request('review',axes=self.axes('needs_fix'))
        self.assertIsNone(self.st.get_report('ops_review_'+self.ch))
        self.assertEqual(self.st.feedback_map(),{})

    def test_bad_and_hold_survive_outside_todo(self):
        axes=self.axes();axes['entities']['status']='hold';axes['entities']['reason']='원문 확인 필요'
        result=self.request('review',axes=axes);self.assertTrue(result['ok'],result)
        self.assertEqual(result['cases'][0]['state'],'hold')
        self.assertFalse(self.st.feedback_map())
        self.assertEqual(O.overview(self.st,who='me')['stats']['hold'],1)
    def test_old_view_cannot_overwrite_review_or_content(self):
        old=O.detail(self.st,self.ch,who='me')
        self.assertTrue(self.request('review',axes=self.axes())['ok'])
        stale=O.action(self.st,dict(action='review',hash=self.ch,revision=old['revision'],basis_token=old['basis']['token'],axes=self.axes('needs_fix')),who='me')
        self.assertFalse(stale['ok'])
        self.assertFalse(O.action(self.st,dict(action='patch',hash=self.ch,revision=old['revision'],basis_token=old['basis']['token'],patch={'summary':'오래된 수정'}),who='me')['ok'])
        self.assertEqual(O.read_row(self.st,self.ch)['item_meta']['summary'],'리드문')
    def test_atomic_patch_and_final_and_audit(self):
        result=self.request('correct_final',patch={'summary':'수정된 리드문'})
        self.assertTrue(result['ok'],result)
        self.assertEqual(O.read_row(self.st,self.ch)['item_meta']['summary'],'수정된 리드문')
        self.assertEqual(self.st.get_report('final_verdicts')['items'][self.ch]['verdict'],'good')
        self.assertEqual(self.st._conn().execute('SELECT COUNT(*) FROM patch_log').fetchone()[0],1)
    def test_audit_failure_rolls_back_meta_and_final(self):
        self.st._conn().execute("CREATE TRIGGER audit_fail BEFORE INSERT ON patch_log BEGIN SELECT RAISE(ABORT,'audit unavailable'); END")
        with self.assertRaises(Exception):self.request('correct_final',patch={'summary':'수정'})
        self.assertEqual(O.read_row(self.st,self.ch)['item_meta']['summary'],'리드문')
        self.assertIsNone(self.st.get_report('final_verdicts'))
        self.assertIsNone(self.st.get_report('ops_review_'+self.ch))
    def test_unmodified_human_confirmed_axes_only(self):
        result=self.request('review',axes=self.axes());self.assertTrue(result['ok'],result)
        self.assertEqual(O.human_expected(self.st,self.ch)['summary'],'리드문')
        self.request('undo')
        self.assertEqual(O.human_expected(self.st,self.ch),{})
    def test_close_requires_recheck_and_service_evidence(self):
        result=self.request('review',axes=self.axes('needs_fix'));case=result['cases'][0]
        self.assertFalse(self.request('case',case_id=case['id'],owner='me',due='2026-10-01',state='closed')['ok'])
        self.request('review',axes=self.axes())
        self.assertFalse(self.request('case',case_id=case['id'],owner='me',due='2026-10-01',state='closed',recheck_evidence='재검수 근거')['ok'])
        result=self.request('case',case_id=case['id'],owner='me',due='2026-10-01',state='closed',recheck_evidence='재검수 근거',service_required=False,service_waiver_reason='검수 기준 사례로만 사용')
        self.assertTrue(result['ok'],result)
    def test_pause_resume_counts_only_active_time(self):
        with patch.object(O.time,'time',return_value=100):self.request('time',event='start',type='first')
        with patch.object(O.time,'time',return_value=120):self.request('time',event='pause')
        with patch.object(O.time,'time',return_value=200):self.request('time',event='resume')
        with patch.object(O.time,'time',return_value=230):result=self.request('time',event='finish')
        self.assertEqual(result['sessions'][0]['seconds'],50)
        self.assertEqual(O.overview(self.st,who='me')['seconds_by_type']['me|first'],50)
    def test_weekly_versions_carryover_and_no_duplicate(self):
        b=O.detail(self.st,self.ch)['basis']['token']
        entry={'hash':self.ch,'owner':'me','topic':'스포츠','basis_token':b,'sample':'general'}
        first=O.action(self.st,{'action':'plan','week':'2026-09-28','entries':[entry],'revision':0},who='me',privileged=True)
        self.assertTrue(first['ok'],first)
        duplicate=O.action(self.st,{'action':'plan','week':'2026-09-28','entries':[entry,entry],'revision':1},who='me',privileged=True)
        self.assertFalse(duplicate['ok'])
        carry=copy.deepcopy(entry);carry['carry_from']='2026-09-28'
        next_week=O.action(self.st,{'action':'plan','week':'2026-10-05','entries':[carry]},who='me',privileged=True)
        self.assertTrue(next_week['ok'],next_week);self.assertEqual(next_week['new_counts'],{})
        self.assertEqual(len(self.st.get_report('ops_week_2026-09-28')['versions']),1)



    def test_handoff_requires_reason_and_waiver_requires_operator(self):
        case=self.request('review',axes=self.axes('needs_fix'))['cases'][0]
        self.assertFalse(self.request('case',case_id=case['id'],owner='other',due='2026-10-01',state='working')['ok'])
        self.assertTrue(self.request('case',case_id=case['id'],owner='other',due='2026-10-01',state='working',handoff_reason='휴가 대체')['ok'])

    def test_weekly_assignment_rolls_back_if_plan_log_fails(self):
        self.st._conn().execute("CREATE TRIGGER plan_fail BEFORE INSERT ON reports WHEN NEW.kind LIKE 'ops_week_%' BEGIN SELECT RAISE(ABORT,'plan unavailable'); END")
        b=O.detail(self.st,self.ch)['basis']['token']
        with self.assertRaises(Exception):
            O.action(self.st,{'action':'plan','week':'2026-09-28','entries':[{'hash':self.ch,'owner':'me','topic':'스포츠','basis_token':b}]},who='me',privileged=True)
        self.assertEqual(self.st.assignees(),{})

    def test_legacy_bad_case_remains_after_time_record(self):
        self.st.save_feedback(self.ch,'뉴스','운영 검수','bad','review','수정 근거',1,reviewer='me')
        self.request('time',event='start',type='recheck')
        result=O.overview(self.st,who='me',legacy=self.st.feedback_map())
        self.assertEqual(len(result['items']),1)
        self.assertTrue(result['items'][0]['legacy'])

    def test_legacy_lists_only_unsettled_bad_consensus(self):
        self.st.save_feedback(self.ch,'뉴스','운영 검수','bad','review','수정 근거',1,reviewer='me')
        self.st.save_feedback(self.ch,'뉴스','운영 검수','good','review','',2,reviewer='other')
        fmap=self.st.feedback_map()
        self.assertEqual(O.overview(self.st,who='me',privileged=True,legacy=fmap)['items'],[])     # 의견 갈림 = 최종 검수 몫
        self.st.save_feedback(self.ch,'뉴스','운영 검수','bad','review','수정 근거',3,reviewer='third')
        fmap=self.st.feedback_map()
        self.assertEqual(len(O.overview(self.st,who='me',privileged=True,legacy=fmap)['items']),1)  # 수정 필요 합의
        self.assertEqual(O.overview(self.st,who='me',privileged=True,legacy=fmap,settled={self.ch})['items'],[])  # 정답 편입·최종 판정

    def test_missing_due_is_not_unassigned(self):
        self.st.save_feedback(self.ch,'뉴스','운영 검수','bad','review','수정 근거',1,reviewer='me')
        stats=O.overview(self.st,who='me',privileged=True,legacy=self.st.feedback_map())['stats']
        self.assertEqual(stats['unassigned'],0)                                                 # 담당 있음 · 기한 없음

    def test_case_state_change_without_due(self):
        self.st.save_feedback(self.ch,'뉴스','운영 검수','bad','review','수정 근거',1,reviewer='me')
        self.request('import_legacy')
        case=O.detail(self.st,self.ch,who='me')['cases'][0]
        self.assertTrue(self.request('case',case_id=case['id'],state='recheck')['ok'])          # 기한 없이 재검수 대기
        self.assertEqual(O.detail(self.st,self.ch,who='me')['cases'][0]['state'],'recheck')

    def test_golden_does_not_reuse_vote_after_metadata_changes(self):
        from prism import serve
        old=serve._STORE;serve._STORE=self.st
        self.addCleanup(lambda:setattr(serve,'_STORE',old))
        self.addCleanup(serve._agg_bump)
        self.request('review',axes=self.axes())
        self.request('patch',patch={'summary':'다른 리드문'})
        serve._agg_bump()
        self.assertEqual(serve.promotion_pending()['promote'],0)
        serve.build_golden_from_reviews()
        self.assertNotIn(self.ch,self.st.golden_hashes())
        self.request('review',axes=self.axes())
        serve._agg_bump()
        self.assertEqual(serve.promotion_pending()['promote'],1)
        serve.build_golden_from_reviews()
        self.assertIn(self.ch,self.st.golden_hashes())
        expected=self.st.get_golden()[0]['expected']
        self.assertEqual(expected['summary'],'다른 리드문')
        self.assertEqual(expected['content_category'],['Sports'])
        self.assertEqual(serve.build_golden_from_reviews()['new'],0)
        self.assertEqual(self.st.golden_count(),1)
