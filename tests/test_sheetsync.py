import json
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from prism import keywordlab as K, entrefine as E, runops, sheetsync as S
from prism.store import Store


def row(n, title, status='', body='본문', **extra):
    return {'row': n, 'values': {'제목': title, '본문': body, '서비스': '뉴스', '상태': status, **extra}}


class _NowThread:                                    # 백그라운드 실행을 호출 안에서 끝낸다
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def pipeline(fields, mock, team, persist):
    ents = [] if fields['title'] == '엔티티 없음' else ['이창용', '한국은행']
    return {'mock': True, 'output': {'item_meta': {'summary': '한국은행이 ' + fields['title'], 'entities': ents,
            'intent': ['속보', 'ETC_OUT'], 'content_category': [{'tier1': 'ETC_OUT', 'tier2': None}]}}}


class SheetSyncTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(tmp.name + '/t.db')
        sv = types.SimpleNamespace(get_store=lambda: self.store, _report_get=lambda k, t=None, d=None: self.store.get_report(k, t) or d,
                                   Handler=types.SimpleNamespace(server_mock=True))
        for module in (K, E):
            p = patch.object(module, '_SV', sv); p.start(); self.addCleanup(p.stop)
        for p in (patch.object(K, 'threading', types.SimpleNamespace(Thread=_NowThread, Lock=threading.Lock)), patch.object(E, '_llm', return_value=(None, True, ''))):
            p.start(); self.addCleanup(p.stop)
        self.v = K.create_version({'model': 'solar-pro3', 'rules': '키워드 최대 3개', 'title': '기준'}, 'a', 't')['version']
        self.code = self.issue()

    def issue(self, team='a'):
        out = K.action({'action': 'sheet_key', 'slot': {'model': self.v['model'], 'version_id': self.v['id']}}, team, 'pete', can_manage=True)
        return out.get('code')

    def call(self, **body):
        return S.handle(json.dumps(dict({'key': self.code}, **body), ensure_ascii=False).encode())

    def run_rows(self, rows, **patches):
        with patch.object(runops, 'add_contents', return_value=patches.get('added', {'ok': True})) as add, \
             patch.object(runops, 'run_pipeline', side_effect=patches.get('pipeline', pipeline)), \
             patch.object(self.store, 'get_item_meta', return_value=patches.get('stored')):
            out = self.call(op='run', rows=rows, include_pending=patches.get('pending', False))
        self.add = add
        return out

    def test_select(self):
        rows = [row(2, '새 글'), row(3, '완료', '검수 완료'), row(4, '반영', '반영됨'), row(5, '대기', '미검수'),
                row(6, '실패', '실패'), row(7, '', body=''), row(8, '긴 제목' * 100), row(9, '새 글')]
        targets, skipped = S.select(rows)
        self.assertEqual([t['row'] for t in targets], [2, 6])
        self.assertEqual({s['row']: s['reason'] for s in skipped},
                         {3: '검수 완료', 4: '반영됨', 5: '미검수(다시 생성 꺼짐)', 7: '제목·본문 없음', 8: '필드 길이 초과', 9: '같은 콘텐츠 중복'})
        self.assertEqual([t['row'] for t in S.select(rows, True)[0]], [2, 5, 6])
        many, over = S.select([row(n, f'글 {n}') for n in range(2, 2 + K.MAX_ITEMS + 3)])
        self.assertEqual((len(many), [s['reason'] for s in over]), (K.MAX_ITEMS, [S.OVER] * 3))
        self.assertEqual(many[0]['content'], {'displayServiceName': '뉴스', 'title': '글 2', 'body': '본문'})

    def test_issue_stores_hash_only_and_rotates(self):
        self.assertRegex(self.code, r'^a\.[\w-]{43}$')
        cfg = S._config('a')
        self.assertEqual((cfg['version_id'], cfg['model'], cfg['by']), (self.v['id'], self.v['model'], 'pete'))
        self.assertNotIn(self.code.split('.')[1], json.dumps([cfg, K.catalog('a')], ensure_ascii=False))
        self.assertEqual(K.catalog('a')['sheet']['connected'], True)
        self.assertFalse(K.action({'action': 'sheet_key', 'slot': {'model': 'x', 'version_id': self.v['id']}}, 'a', can_manage=True)['ok'])
        self.assertFalse(K.action({'action': 'sheet_key', 'slot': {}}, 'a')['ok'])          # 관리 권한 필요
        old, self.code = self.code, self.issue()
        self.assertEqual(self.call(op='result', id='0' * 32)['error'], '시트에서 시작한 실험이 아닙니다')
        self.code = old                                                                      # 다시 만들면 이전 코드는 막힘
        self.assertEqual(self.call(op='result', id='0' * 32)['error'], S.DENIED)

    def test_denied_looks_the_same(self):
        for key in (None, '', 'a', 'a.' + 'x' * 43, 'b.' + self.code.split('.')[1], '-.' + self.code.split('.')[1], self.code + 'x'):
            self.assertEqual(S.handle(json.dumps({'key': key, 'op': 'run'}).encode()), {'ok': False, 'error': S.DENIED})
        self.assertEqual(S.handle(b'[]')['ok'], False)
        self.assertEqual(S.handle(b'{bad')['ok'], False)

    def test_none_team_code(self):                    # sqlite 단일 팀 모드(team=None) = '-.' 접두
        self.v = K.create_version({'model': 'solar-pro3', 'rules': '키워드 최대 3개', 'title': '기준'}, None, 't')['version']
        self.code = self.issue(None)
        self.assertTrue(self.code.startswith('-.'))
        self.assertEqual(self.call(op='nope')['error'], '지원하지 않는 작업입니다')

    def test_run_result_written(self):
        rows = [row(2, '기준금리 인하', **{'원문 링크': 'https://news.example/a'}), row(3, '이미 검수', '검수 완료'),
                row(4, '엔티티 없음'), row(5, '다시', '실패', 콘텐츠키='k5'), row(6, '', body='제목 없는 본문')]
        out = self.run_rows(rows)
        self.assertEqual((out['ok'], out['targets'], out['skipped']), (True, 4, 1))
        rid = out['id']
        self.assertEqual([c['item_unique_key'] for c in self.add.call_args[0][0] if 'item_unique_key' in c], ['k5'])
        run = K._get(K._key('run', rid), 'a')
        self.assertEqual((run['by'], run['status'], run['slots'][0]['version']['id']), ('구글 시트', 'done', self.v['id']))
        self.assertNotIn('rows', run['sheet'])
        self.assertEqual(run['items'][0]['meta']['intent'], ['속보'])          # ETC_OUT 제외
        res = self.call(op='result', id=rid)
        self.assertEqual((res['status'], res['total']), ('done', 4))
        ups = res['updates']
        self.assertEqual([u['row'] for u in ups], [2, 4, 5, 6])
        first, failed = ups[0]['values'], ups[1]['values']
        self.assertEqual((first['상태'], first['리드문'], first['인텐트'], first['카테고리'], first['실험 ID']),
                         ('미검수', '한국은행이 기준금리 인하', '속보', '', rid))
        self.assertTrue(first['핵심키워드'] and first['모델'].startswith('메타 mock · 키워드 '))
        self.assertEqual((failed['상태'], failed['핵심키워드'], failed['모델']), ('실패', '', '메타 mock'))
        self.assertIn('엔티티', failed['오류'])
        self.assertEqual((ups[2]['expect'], ups[3]['expect']), ({'제목': '다시', '콘텐츠키': 'k5'}, {'제목': '', '콘텐츠키': ''}))
        self.assertEqual(self.call(op='written', id=rid, written=3, mismatched=1), {'ok': True})
        s = K._get(K._key('run', rid), 'a')['sheet']
        self.assertEqual((s['written'], s['mismatched']), (3, 1))
        self.assertIn('1개 행 위치가 바뀌어', s['write_error'])
        self.assertFalse(self.call(op='written', id=rid, written='3', mismatched=0)['ok'])

    def test_bad_rows_rejected_whole(self):
        for rows in ('x', [{'row': 1, 'values': {}}], [{'row': '2', 'values': {}}], [row(n, 'a') for n in range(2, 3 + S.MAX_ROWS)]):
            self.assertFalse(self.run_rows(rows)['ok'])
        self.assertEqual(self.run_rows([{'row': 2, 'values': {'제목': '글'}}])['error'], '시트 양식이 다릅니다 · 시트 메뉴 프리즘 › 양식 만들기를 실행하세요')
        self.assertEqual(self.run_rows([row(2, '완료', '검수 완료')])['error'], '처리할 행이 없습니다')
        self.assertEqual(K._catalog('a')['runs'], [])

    def test_reuses_stored_meta(self):
        stored = {'summary': '한국은행이 금리를 내렸다', 'entities': ['이창용', '한국은행'], 'intent': [], 'content_category': []}
        rid = self.run_rows([row(2, '기준금리 인하')], stored=stored, pipeline=AssertionError('다시 실행하면 안 됨'))['id']
        run = K._get(K._key('run', rid), 'a')
        self.assertEqual((run['items'][0]['meta']['summary'], run['sheet']['meta_model']), (stored['summary'], '저장 결과'))

    def test_add_contents_error_marks_rows(self):
        rid = self.run_rows([row(2, '기준금리 인하')], added={'error': '저장 실패 · 다시 시도하세요'})['id']
        values = self.call(op='result', id=rid)['updates'][0]['values']
        self.assertEqual((values['상태'], values['오류']), ('실패', '콘텐츠 등록 실패: 저장 실패 · 다시 시도하세요'))

    def test_one_sheet_run_per_team(self):
        K.SHEET_TEAMS.add('a')
        self.addCleanup(K.SHEET_TEAMS.discard, 'a')
        self.assertEqual(self.run_rows([row(2, '기준금리 인하')])['error'], '이미 시트 실험이 실행 중입니다')


if __name__ == '__main__':
    unittest.main()
