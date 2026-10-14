import io
import json
import tempfile
import types
import unittest
from unittest.mock import patch

from prism import keywordlab as K, entrefine as E, runops, sheetsync as S
from prism.store import Store

URL = 'https://script.google.com/macros/s/AKfy_abc-1/exec'
KEY = 'secret-key-123'


def row(n, title, status='', body='본문', **extra):
    return {'row': n, 'values': {'제목': title, '본문': body, '서비스': '뉴스', '상태': status, **extra}}


class SheetSyncTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(tmp.name + '/t.db')
        sv = types.SimpleNamespace(get_store=lambda: self.store, _report_get=lambda k, t=None, d=None: self.store.get_report(k, t) or d,
                                   Handler=types.SimpleNamespace(server_mock=True))
        for module in (K, E):
            p = patch.object(module, '_SV', sv); p.start(); self.addCleanup(p.stop)
        self.sent, self.rows, self.mismatched = [], [], []
        p = patch.object(S._OPENER, 'open', self.fake); p.start(); self.addCleanup(p.stop)

    def fake(self, req, timeout=None):
        self.sent.append(req)
        if req.get_method() == 'POST':
            body = json.loads(req.data)
            self.assertTrue(all('expect' in u for u in body['updates']))
            runs = K._catalog('a')['runs']
            if runs:
                self.post_status = K._get(K._key('run', runs[-1]['id']), 'a')['status']
            out = {'ok': True, 'written': len(body['updates']), 'mismatched': self.mismatched} if body['key'] == KEY else {'ok': False, 'error': 'unauthorized'}
        else:
            out = {'ok': True, 'rows': self.rows} if ('key=' + KEY) in req.full_url else {'ok': False, 'error': 'unauthorized'}
        return io.BytesIO(json.dumps(out).encode())

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

    def test_config_validation_and_masking(self):
        for bad in ('http://script.google.com/macros/s/x/exec', 'https://evil.com/macros/s/x/exec', URL + '?x=1'):
            with self.assertRaises(ValueError):
                S.save_config({'url': bad, 'key': KEY}, 'a')
        with self.assertRaises(ValueError):
            S.save_config({'url': URL, 'key': 'short'}, 'a')
        with self.assertRaises(ValueError):
            S.save_config({'url': URL}, 'a')                      # 첫 저장엔 키 필수
        out = K.action({'action': 'sheet_config', 'url': URL, 'key': KEY}, 'a', can_manage=True)
        self.assertEqual(out['sheet'], {'url': URL, 'has_key': True, 'template_url': S.TEMPLATE_URL})
        self.assertNotIn(KEY, json.dumps(K.catalog('a'), ensure_ascii=False))
        S.save_config({'url': URL}, 'a')                           # 키 비우면 기존 키 유지
        self.assertEqual(S._config('a')['key'], KEY)
        self.assertFalse(K.action({'action': 'sheet_preview'}, 'a')['ok'])   # 관리 권한 필요
        self.assertEqual(S.public_config('b'), {'url': '', 'has_key': False, 'template_url': S.TEMPLATE_URL})

    def test_connect_code(self):
        out = K.action({'action': 'sheet_config', 'code': f' {URL}#ab#cd-1234 '}, 'a', can_manage=True)
        self.assertEqual((out['sheet']['url'], S._config('a')['key']), (URL, 'ab#cd-1234'))   # 키 안의 '#' 보존
        self.assertFalse(K.action({'action': 'sheet_config', 'code': URL + '#short'}, 'a', can_manage=True)['ok'])
        self.assertFalse(K.action({'action': 'sheet_config', 'code': 'https://evil.com/x#' + KEY}, 'a', can_manage=True)['ok'])
        K.action({'action': 'sheet_config', 'code': URL}, 'a', can_manage=True)            # 키 없는 코드 = 기존 키 유지
        self.assertEqual(S._config('a')['key'], 'ab#cd-1234')

    def test_preview_unauthorized_hides_key(self):
        S.save_config({'url': URL, 'key': KEY}, 'a')
        ok = K.action({'action': 'sheet_preview'}, 'a', can_manage=True)
        self.assertEqual((ok['read'], ok['rows']), (0, []))
        self.rows = [{'row': 2, 'values': {'제목': '글', '메모': ''}}]          # 상태 열 없음
        self.assertEqual(K.action({'action': 'sheet_preview'}, 'a', can_manage=True)['error'], '시트 양식이 다릅니다 · Apps Script 의 setup 을 실행하세요')
        K._update(S.CONFIG, 'a', lambda c: c.update(key='wrong-key-999'))
        out = K.action({'action': 'sheet_preview'}, 'a', can_manage=True)
        self.assertEqual(out, {'ok': False, 'error': '시트 비밀 키가 맞지 않습니다'})
        with patch.object(S._OPENER, 'open', lambda req, timeout=None: io.BytesIO(b'{"ok":false,"error":"bad wrong-key-999"}')):
            self.assertEqual(K.action({'action': 'sheet_preview'}, 'a', can_manage=True)['error'], 'bad ***')

    def test_start_runs_meta_keywords_and_writes_sheet(self):
        S.save_config({'url': URL, 'key': KEY}, 'a')
        self.rows = [row(2, '기준금리 인하'), row(3, '이미 검수', '검수 완료'), row(4, '엔티티 없음'),
                     row(5, '다시', '실패', 콘텐츠키='k5')]
        self.rows[0]['values']['원문 링크'] = 'https://news.example/a'
        self.rows.append(row(6, '', body='제목 없는 본문'))

        def pipeline(fields, mock, team, persist):
            self.assertTrue(mock and persist)
            ents = [] if fields['title'] == '엔티티 없음' else ['이창용', '한국은행']
            return {'mock': True, 'output': {'item_meta': {'summary': '한국은행이 ' + fields['title'], 'entities': ents,
                    'intent': ['속보', 'ETC_OUT'], 'content_category': [{'tier1': 'ETC_OUT', 'tier2': None}]}}}
        v = K.create_version({'model': 'solar-pro3', 'rules': '키워드 최대 3개', 'title': '기준'}, 'a', 't')['version']
        body = {'slots': [{'model': v['model'], 'version_id': v['id']}], 'sheet': {'include_pending': False}}
        v2 = K.create_version({'model': 'solar-pro3', 'rules': '키워드 2개', 'title': '둘째'}, 'a', 't')['version']
        with self.assertRaisesRegex(ValueError, '단일 모델'):
            K.start(dict(body, slots=body['slots'] + [{'model': v2['model'], 'version_id': v2['id']}]), 'a', 't', background=False)
        with patch.object(runops, 'add_contents', return_value={'ok': True}) as add, \
             patch.object(runops, 'run_pipeline', side_effect=pipeline), patch.object(E, '_llm', return_value=(None, True, '')):
            rid = K.start(body, 'a', 't', background=False)['id']
        self.assertEqual([c['item_unique_key'] for c in add.call_args[0][0] if 'item_unique_key' in c], ['k5'])
        run = K._get(K._key('run', rid), 'a')
        self.assertEqual({k: run['sheet'][k] for k in ('read', 'targets', 'skipped', 'meta_failed', 'written', 'write_error')},
                         {'read': 5, 'targets': 4, 'skipped': 1, 'meta_failed': 1, 'written': 4, 'write_error': ''})
        self.assertEqual(run['items'][0]['meta']['intent'], ['속보'])          # ETC_OUT 제외
        self.assertEqual(next(x for x in K.catalog('a')['runs'] if x['id'] == rid)['item_keys'], [it['key'] for it in run['items']])
        post = json.loads(self.sent[-1].data)
        self.assertEqual([u['row'] for u in post['updates']], [2, 4, 5, 6])
        self.assertEqual(run['items'][-1]['title'], '시트 6행')
        first, failed = post['updates'][0]['values'], post['updates'][1]['values']
        self.assertEqual((first['상태'], first['리드문'], first['인텐트'], first['카테고리'], first['실험 ID']),
                         ('미검수', '한국은행이 기준금리 인하', '속보', '', rid))
        self.assertTrue(first['핵심키워드'] and first['모델'].startswith('메타 mock · 키워드 '))
        self.assertEqual((failed['상태'], failed['핵심키워드']), ('실패', ''))
        self.assertIn('엔티티', failed['오류'])
        self.assertEqual(failed['모델'], '메타 mock')                           # 키워드 미실행 칸은 메타 모델만
        self.assertEqual((post['updates'][2]['expect'], post['updates'][3]['expect']),
                         ({'제목': '다시', '콘텐츠키': 'k5'}, {'제목': '', '콘텐츠키': ''}))
        self.assertEqual(self.post_status, 'running')                           # 기재가 끝난 뒤에 완료 저장
        self.mismatched = [2]
        self.assertEqual(K.action({'action': 'sheet_write', 'id': rid}, 'a', can_manage=True)['sheet']['write_error'],
                         '1개 행 위치가 바뀌어 기재하지 않았습니다 · 정렬·삽입 없이 시트 다시 기재')
        self.assertEqual(K._get(K._key('run', rid), 'a')['sheet']['mismatched'], 1)
        self.mismatched = []
        # 기재 실패는 실험을 실패로 만들지 않고 다시 기재할 수 있다
        K._update(S.CONFIG, 'a', lambda c: c.update(key='wrong-key-999'))
        out = K.action({'action': 'sheet_write', 'id': rid}, 'a', can_manage=True)
        self.assertEqual((out['ok'], out['sheet']['write_error']), (False, '시트 비밀 키가 맞지 않습니다'))
        self.assertEqual(K._get(K._key('run', rid), 'a')['status'], 'done')
        K._update(S.CONFIG, 'a', lambda c: c.update(key=KEY))
        self.assertEqual(K.action({'action': 'sheet_write', 'id': rid}, 'a', can_manage=True)['sheet']['write_error'], '')

    def start_sheet(self, **patches):
        S.save_config({'url': URL, 'key': KEY}, 'a')
        self.rows = [row(2, '기준금리 인하')]
        v = K.create_version({'model': 'solar-pro3', 'rules': '키워드 최대 3개', 'title': '기준'}, 'a', 't')['version']
        with patch.object(runops, 'add_contents', return_value=patches.get('added', {'ok': True})), \
             patch.object(runops, 'run_pipeline', side_effect=AssertionError('다시 실행하면 안 됨')), \
             patch.object(self.store, 'get_item_meta', return_value=patches.get('stored')), patch.object(E, '_llm', return_value=(None, True, '')):
            rid = K.start({'slots': [{'model': v['model'], 'version_id': v['id']}], 'sheet': {}}, 'a', 't', background=False)['id']
        return K._get(K._key('run', rid), 'a'), json.loads(self.sent[-1].data)['updates'][0]['values']

    def test_reuses_stored_meta(self):
        stored = {'summary': '한국은행이 금리를 내렸다', 'entities': ['이창용', '한국은행'], 'intent': [], 'content_category': []}
        run, values = self.start_sheet(stored=stored)
        self.assertEqual((run['items'][0]['meta']['summary'], run['sheet']['meta_model'], values['상태']), (stored['summary'], '저장 결과', '미검수'))

    def test_add_contents_error_marks_rows(self):
        run, values = self.start_sheet(added={'error': '저장 실패 · 다시 시도하세요'})
        self.assertEqual((values['상태'], values['오류'], run['error']), ('실패', '콘텐츠 등록 실패: 저장 실패 · 다시 시도하세요', values['오류']))

    def test_one_sheet_run_per_team(self):
        K.SHEET_TEAMS.add('a')
        self.addCleanup(K.SHEET_TEAMS.discard, 'a')
        with self.assertRaisesRegex(ValueError, '이미 시트 실험이 실행 중입니다'):
            self.start_sheet()

    def test_redirect_hosts_and_size(self):
        req = S.urllib.request.Request(URL, method='POST', data=b'{}')
        follow = lambda url: S._Redirect().redirect_request(req, None, 302, 'Found', {}, url)
        self.assertIsNotNone(follow('https://script.googleusercontent.com/macros/echo?x=1'))
        self.assertIsNone(follow('https://evil.example/steal'))
        self.assertIsNone(follow('http://script.googleusercontent.com/x'))
        S.save_config({'url': URL, 'key': KEY}, 'a')
        with patch.object(S, 'MAX_BYTES', 10), self.assertRaisesRegex(ValueError, '너무 큽니다'):
            S.fetch_rows('a')

    def test_write_chunks(self):
        S.save_config({'url': URL, 'key': KEY}, 'a')
        self.assertEqual(S.write('a', [{'row': n, 'values': {'상태': '미검수'}, 'expect': {}} for n in range(2, 122)]), (120, []))
        self.assertEqual([len(json.loads(r.data)['updates']) for r in self.sent], [50, 50, 20])


if __name__ == '__main__':
    unittest.main()
