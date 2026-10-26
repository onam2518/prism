"""최종검수 목록: 원격 왕복 수와 페이지 밖 상세 가공을 제한한다."""
import unittest
from unittest.mock import patch
from prism import opsreview as O, learnops as LO
from tests.test_two_tier_review import TwoTierBase


class QueuePerformanceTests(TwoTierBase):
    def test_only_visible_page_is_formatted(self):
        serve, st = self._with_store()
        for i in range(75):
            self._put_reviewed(st, '목록 '+str(i), [('A','good'),('B','bad')])
        with patch.object(serve, '_detail_row', wraps=serve._detail_row) as render:
            result = serve.final_review_queue(limit=20, offset=20)
        self.assertEqual(result['n'], 75)
        self.assertEqual(len(result['items']), 20)
        self.assertEqual(render.call_count, 20)
        self.assertTrue(all(r['body'] and r['fb']['good']==1 for r in result['items']))
        self.assertTrue(all('_source' not in r for r in result['items']))

    def test_remote_snapshots_batch_and_preserve_basis(self):
        hashes = [format(i, '016x') for i in range(250)]
        row = {'service':'뉴스','title':'원문','subtitle':None,'body':'본문', 'source_fields':None,
               'item_meta':{},'quality_meta':{},'model':None,'version':None}
        axes = {key:{'status':'accurate'} for key in O.FIELDS}
        record = {'reviews':[{'by':'A','basis':{'token':O.basis(row)['token']},'axes':axes}], 'history':[]}
        class Remote:
            REMOTE=True
            def __init__(self): self.calls=[]
            def _get(self, table, query):
                self.calls.append((table,query))
                self_test.assertIn('team_key=eq.team-a' if table=='reports' else 'team_id=eq.team-a',query)
                keys=query.split('in.(',1)[1].split(')',1)[0].split(',')
                if table=='reports': return [{'kind':key,'payload':record} for key in keys]
                return [dict(row,hash=key) for key in keys]
            def get_report(self,*args): raise AssertionError('건별 기록 조회 금지')
        self_test=self
        remote=Remote()
        snapshots=O.review_snapshots(remote,hashes,'team-a')
        self.assertEqual(len(remote.calls),6)
        self.assertEqual(len(snapshots),250)
        for ch in hashes:
            fb,final=LO._current_review_votes(remote,ch,'team-a',{}, {'verdict':'good','basis_token':O.basis(row)['token']},snapshot=snapshots[ch])
            self.assertEqual(fb['good'],1)
            self.assertEqual(final,'good')
        self.assertEqual(len(remote.calls),6)

    def test_missing_managed_content_is_not_treated_as_current(self):
        class Remote:
            REMOTE=True
            def _get(self,table,query):
                return [{'kind':'ops_review_'+'a'*16,'payload':{'reviews':[]}}] if table=='reports' else []
        with self.assertRaises(ValueError): O.review_snapshots(Remote(),['a'*16],'team-a')
