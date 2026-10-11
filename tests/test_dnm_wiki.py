import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prism import dnm, dnmwiki as W, agents as A
from prism.config import Config
from prism.llm import LLMClient
from prism.store import Store


def t(s):
    return {'type': 'text', 'text': s}


def para(s):
    return {'type': 'paragraph', 'content': [t(s)]}


def table(rows):
    return {'type': 'table', 'content': [{'type': 'tableRow', 'content': [
        {'type': 'tableHeader' if i == 0 else 'tableCell', 'content': [para(c)]} for c in r]}
        for i, r in enumerate(rows)]}


HEAD = list(W.COLUMNS)
ROW = ['wire-a', 'partner_news', 'service_code', 'Y', '"news"', '2026-10-01T00:00:00+09:00',
       '담당자', '표본 30건', '승인자', '2026-10-02T00:00:00+09:00', 'N']


def policy_page(version=dnm.CATEGORIES, record=None):
    content = [para('현행 category_dictionary_version=' + version), {'type': 'heading', 'content': [t(W.ANCHOR)]}]
    content += [record] if record else []
    content += [{'type': 'heading', 'content': [t('Appendix')]}, para('이전 category-common-old-r1')]
    return {'type': 'doc', 'version': 1, 'content': content}


class DnmWiki(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = Store(str(Path(tmp.name) / 'db')); self.addCleanup(self.store._conn().close)
        self.llm = LLMClient(model='test', mock=True, config=Config())
        p = patch.object(A, 'META_CFG', {'four_calls': True, 'call_models': {}}); p.start(); self.addCleanup(p.stop)
        self.pages = {'1': policy_page(), '2': {'type': 'doc', 'content': [table([HEAD, ROW, ROW[:2] + ['flag', 'N', '', *ROW[5:]]])]}}
        self.writes = []
        W.save_config(self.store, {'policy_pages': '1', 'registry_page': '2', 'record_page': '1'}, 't')

    def read(self, pid):
        return {'title': 'p' + pid, 'version': {'number': 3}}, self.pages[pid]

    def test_labels_skip_appendix_and_record(self):
        adf = policy_page(record=table([['항목', '값'], ['category', 'category-common-written-by-prism']]))
        self.assertEqual(W.labels(adf), {'category_dictionary_version': dnm.CATEGORIES})

    def test_registry_table_merges_route_rows(self):
        reg = W.registry(self.pages['2'], '2')
        self.assertEqual(reg['entries'][0]['matches'], {'service_code': {'provided': True, 'value': 'news'},
                                                        'flag': {'provided': False}})
        self.assertFalse(reg['entries'][0]['unique_key_verified'])
        self.assertEqual(reg['version'], W.registry(self.pages['2'], '2')['version'])
        with self.assertRaises(ValueError):
            W.registry({'content': [table([HEAD[:3], ROW[:3]])]}, '2')

    def test_sync_configures_once_and_never_approves(self):
        s = W.sync(self.store, self.llm, read=self.read, write=lambda pid, pairs: self.writes.append(pairs) or True)
        self.assertEqual(len(s['actions']), 3, s)
        control = dnm.Runtime(self.store, 't')._get('dnm_control')
        self.assertIsNone(control['approval'])
        self.assertEqual(control['registry']['version'], W.registry(self.pages['2'], '2')['version'])
        again = W.sync(self.store, self.llm, read=self.read, write=lambda pid, pairs: False)
        self.assertEqual(again['actions'], [])
        self.assertEqual(dnm.Runtime(self.store, 't')._get('dnm_control')['revision'], control['revision'])

    def test_wiki_label_change_is_reported_not_applied(self):
        self.pages['1'] = policy_page('category-common-99')
        s = W.sync(self.store, self.llm, read=self.read, write=lambda *a: False)
        self.assertTrue(s['mismatch'])
        self.assertEqual(dnm.CATEGORIES, W.LABELS['category_dictionary_version'][1])

    def test_write_record_only_touches_region(self):
        puts = []
        with patch.object(W, '_api', lambda m, path, body=None: puts.append(body)):
            pairs = [('policy_version', 'x'), ('기록 시각', 'a')]
            self.assertTrue(W.write_record('1', pairs, api=self.read))
            new = json.loads(puts[0]['body']['value'])
            self.assertEqual(puts[0]['version']['number'], 4)
            self.assertIn(W.TAG, puts[0]['version']['message'])
            self.assertEqual(W.strip_record(new), W.strip_record(self.pages['1']))
            self.pages['1'] = new                       # 같은 내용·시각만 다르면 다시 쓰지 않는다(루프 방지)
            self.assertFalse(W.write_record('1', [('policy_version', 'x'), ('기록 시각', 'b')], api=self.read))
        self.pages['1'] = {'type': 'doc', 'content': [para('기록 제목 없음')]}
        with self.assertRaises(ValueError):
            W.write_record('1', pairs, api=self.read)



class NextWaitTest(unittest.TestCase):
    def test_sleeps_until_due_and_long_when_disabled(self):
        from prism import dnmwiki as W
        on = dict(W.DEFAULT, enabled=True, interval_min=30)
        self.assertEqual(W._next_wait(on, 1000, 1000 + 60), 29 * 60)
        self.assertEqual(W._next_wait(on, 1000, 1000 + 31 * 60), 0)
        self.assertEqual(W._next_wait(on, 0, 10 ** 9), 0)           # 한 번도 안 돌았으면 바로
        self.assertEqual(W._next_wait(dict(W.DEFAULT, enabled=False), 0, 10 ** 9), 600)

if __name__ == '__main__':
    unittest.main()
