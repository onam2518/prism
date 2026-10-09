"""오류 주입과 실제 SQLite로 저장 경계·채점·계산 결과 보존을 확인한다."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from prism import dashboard, metaeval, serve, topic
from prism.store import Store
from prism.supastore import SupabaseStore


class TestRuntimeBoundaries(unittest.TestCase):
    def test_short_summary_false_positive(self):
        self.assertEqual(metaeval.summary_sim('가', '나'), 0)
        self.assertEqual(metaeval.summary_sim('A', ''), 0)
        self.assertEqual(metaeval.summary_sim(' A ', 'A'), 1)

    def test_exact_entity_match_cannot_be_stolen_by_partial(self):
        self.assertEqual(metaeval._ent_partial_f1({'ab', 'abc'}, {'abc', 'abx'}), .75)

    def test_malformed_entity_types_do_not_crash_report(self):
        acc = {}
        metaeval.meta_tally(acc, {'entities': [{'name': '사람', 'type': []}]}, {'item_meta': {'entities': [{'name': None}]}})
        self.assertEqual(acc['ent_type_n'], 0)

    def test_remote_read_failure_cannot_leak_memory_rows(self):
        st = SimpleNamespace(REMOTE=True, recent=Mock(side_effect=OSError('offline')))
        with patch.object(serve, 'get_store', return_value=st), patch.object(serve, '_LAST_RESULTS', [{'team': 'other'}]):
            with self.assertRaisesRegex(RuntimeError, '조회 실패'):
                serve.results_rows(team='current')

    def test_junk_cache_keeps_service_names_request_scoped(self):
        self.assertFalse(dashboard._is_junk_entity('산책동호회', set()))
        self.assertTrue(dashboard._is_junk_entity('산책동호회', {'산책동호회'}))
        self.assertFalse(dashboard._is_junk_entity('산책동호회', set()))

    def test_topic_memory_and_file_results_match_without_mutation(self):
        rows = [{'content_ref': {'title': str(i), 'body': '본문', 'displayServiceName': '뉴스'},
                 'item_meta': {'entities': [{'name': '선수', 'type': 'PS'}, {'name': '구단', 'type': 'OG'}],
                               'intent': ['경기 결과·리뷰'], 'content_category': [{'tier1': 'Sports', 'tier2': None}]},
                 'quality_meta': {'finalGrade': 'G'}} for i in range(4)]
        original = copy.deepcopy(rows)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'rows.jsonl'
            p.write_text(''.join(json.dumps(r) + '\n' for r in rows))
            self.assertEqual(topic.build_topics(str(p)), topic.build_topics_rows(rows))
        self.assertEqual(rows, original)

    def test_sqlite_golden_replace_rolls_back_late_insert_failure(self):
        with tempfile.TemporaryDirectory() as td:
            st = Store(str(Path(td) / 'db'))
            self.addCleanup(st._conn().close)
            old = [{'content': {'title': 'old'}, 'expected': {'summary': 'keep'}}]
            st.register_golden(None, old)
            st._conn().execute("CREATE TRIGGER fail_golden BEFORE INSERT ON golden WHEN NEW.content LIKE '%bad%' BEGIN SELECT RAISE(ABORT, 'bad row'); END")
            st._conn().commit()
            rows = [{'content': {'title': title}, 'expected': {'summary': 'new'}} for title in ('good', 'bad')]
            with self.assertRaisesRegex(Exception, 'bad row'):
                st.register_golden(None, rows)
            self.assertEqual(st.get_golden(), old)
            self.assertEqual(st.register_golden(None, []), 0)
            self.assertEqual(st.get_golden(), old)
            self.assertEqual(st._conn().execute('SELECT count(*) FROM golden_history').fetchone()[0], 0)

    def test_direct_add_preserves_source_and_image_presence(self):
        with tempfile.TemporaryDirectory() as td:
            st = Store(str(Path(td) / 'db'))
            self.addCleanup(st._conn().close)
            with patch.object(serve, 'get_store', return_value=st):
                result = serve.add_contents([{'title': 'raw input', 'cp_type': None}])
            self.assertNotIn('error', result)
            ref = st.recent()[0]['content_ref']
            self.assertEqual(ref['input_aux'], {'image_count': {'provided': False}})
            self.assertEqual(ref['source_fields'], {'cp_type': None})

    def test_golden_remote_replace_uses_one_atomic_request(self):
        st = object.__new__(SupabaseStore)
        st._req = Mock(return_value=2)
        rows = [{'content': {'title': str(i)}, 'expected': {'summary': 'x'}} for i in range(2)]
        self.assertEqual(st.register_golden('team', rows), 2)
        self.assertEqual(st._req.call_count, 1)
        args, kw = st._req.call_args
        self.assertEqual(args, ('POST', 'rpc/prism_write_golden'))
        self.assertTrue(kw['body']['p_replace'])
        st._req.reset_mock()
        self.assertEqual(st.register_golden('team', []), 0)
        st._req.assert_not_called()

    def test_rpc_recovers_after_transient_failure(self):
        st = object.__new__(SupabaseStore); st.key = 'test'
        st._http = Mock(side_effect=[(503, '', {}), (200, '{"total":2}', {})])
        self.assertIsNone(st._rpc_or_none('aggregate', {}))
        self.assertEqual(st._rpc_or_none('aggregate', {}), {'total': 2})

    def test_rpc_missing_expires_and_is_not_shared_between_stores(self):
        st = object.__new__(SupabaseStore); st.key = 'test'
        st._http = Mock(side_effect=[(404, '', {}), (200, '1', {})])
        with patch('prism.supastore.time.monotonic', return_value=10):
            self.assertIsNone(st._rpc_or_none('aggregate', {}))
            self.assertIsNone(st._rpc_or_none('aggregate', {}))
        self.assertEqual(st._http.call_count, 1)
        other = object.__new__(SupabaseStore); other.key = 'test'; other._http = Mock(return_value=(200, '2', {}))
        self.assertEqual(other._rpc_or_none('aggregate', {}), 2)
        with patch('prism.supastore.time.monotonic', return_value=311):
            self.assertEqual(st._rpc_or_none('aggregate', {}), 1)

    def test_writes_are_not_replayed_after_lost_response(self):
        for method in ('POST', 'PATCH', 'DELETE', 'GET'):
            with self.subTest(method=method), patch.object(SupabaseStore, '_POOL', {}):
                conn = Mock(); conn.getresponse.side_effect = ConnectionResetError('response lost after commit')
                st = object.__new__(SupabaseStore); st.url = 'https://unused.invalid'
                with patch('prism.supastore.http.client.HTTPSConnection', return_value=conn):
                    with self.assertRaises(ConnectionResetError):
                        st._http(method, '/rest/v1/table', b'{}', {'Prefer': 'resolution=merge-duplicates,return=minimal'})
                self.assertEqual(conn.request.call_count, 2 if method == 'GET' else 1)

    def test_bad_summary_and_failed_manual_lookup_do_not_write(self):
        st = Mock(); st.get_item_meta.side_effect = OSError('offline')
        with patch.object(serve, 'get_store', return_value=st):
            self.assertFalse(serve.patch_content_meta('abc', {'summary': 123})['ok'])
            self.assertFalse(serve.patch_content_meta('abc', {'summary': 'valid'})['ok'])
        st.update_item_meta.assert_not_called()
