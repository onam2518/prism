"""골든 업로드의 데이터 보존 계약: 빈 입력과 Supabase 원자적 쓰기.

실행: python3 -m unittest tests.test_golden_atomic
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _row(title, grade="G"):
    return {"content": {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "본문"},
            "expected": {"finalGrade": grade, "content_category": ["Sports"]}}


class TestGoldenUploadRejectsEmpty(unittest.TestCase):
    def setUp(self):
        from prism import serve
        from prism.store import Store
        self.serve = serve
        self.store = Store(os.path.join(tempfile.mkdtemp(), "golden.db"))
        self.previous = serve._STORE
        serve._STORE = self.store

    def tearDown(self):
        self.serve._STORE = self.previous

    def test_empty_and_all_invalid_upload_keep_existing_answers(self):
        self.store.register_golden(None, [_row("기존 정답")], replace=True)
        for rows in ([], [_row("부적합", "X")]):
            with self.subTest(rows=rows):
                result = self.serve.register_golden("admin", None, rows, merge=False)
                self.assertFalse(result["ok"])
                self.assertEqual(result["count"], 0)
                self.assertEqual([r["content"]["title"] for r in self.store.get_golden(None)], ["기존 정답"])

    def test_explicit_store_replace_empty_remains_the_delete_action(self):
        self.store.register_golden(None, [_row("삭제 전")], replace=True)
        self.store.register_golden(None, [], replace=True)  # admin_action 의 명시적 전체 삭제 경로
        self.assertEqual(self.store.golden_count(None), 0)


class TestSupabaseGoldenAtomicWrite(unittest.TestCase):
    TEAM = "11111111-1111-1111-1111-111111111111"

    def _stub(self, rpc):
        from prism.supastore import SupabaseStore
        store = SupabaseStore.__new__(SupabaseStore)
        def request(method, table, *, body=None):
            self.assertEqual((method, table), ("POST", "rpc/prism_write_golden"))
            return call("prism_write_golden", body)
        call = rpc
        store._req = request
        return store

    def test_replace_uses_one_team_bound_rpc_for_more_than_two_legacy_chunks(self):
        calls = []
        store = self._stub(lambda fn, args: (calls.append((fn, args)) or len(args["p_rows"])))
        rows = [_row("대량-%03d" % i) for i in range(1001)]
        self.assertEqual(store.register_golden(self.TEAM, rows, replace=True), len(rows))
        self.assertEqual(len(calls), 1)
        fn, args = calls[0]
        self.assertEqual(fn, "prism_write_golden")
        self.assertEqual(args["p_team_id"], self.TEAM)
        self.assertTrue(args["p_replace"])
        self.assertEqual(len(args["p_rows"]), len(rows))
        self.assertTrue(all("team_id" not in row for row in args["p_rows"]))

    def test_rpc_failure_is_propagated_without_rest_fallback(self):
        def fail(fn, args):
            raise RuntimeError("RPC unavailable")
        with self.assertRaisesRegex(RuntimeError, "RPC unavailable"):
            self._stub(fail).register_golden(self.TEAM, [_row("실패")], replace=True)

    def test_merge_and_replace_match_sqlite_row_contract(self):
        from prism.store import Store
        mirror = Store(os.path.join(tempfile.mkdtemp(), "mirror.db"))

        def rpc(fn, args):
            self.assertEqual(fn, "prism_write_golden")
            rows = [{"content": row["content"], "expected": row["expected"]}
                    for row in args["p_rows"]]
            return mirror.register_golden(None, rows, replace=args["p_replace"], source=args["p_source"])

        remote = self._stub(rpc)
        sqlite = Store(os.path.join(tempfile.mkdtemp(), "sqlite.db"))
        initial, merged = [_row("기존")], [_row("기존", "R"), _row("추가")]
        self.assertEqual(remote.register_golden(self.TEAM, initial, replace=True),
                         sqlite.register_golden(None, initial, replace=True))
        self.assertEqual(remote.register_golden(self.TEAM, merged, replace=False),
                         sqlite.register_golden(None, merged, replace=False))
        self.assertEqual(mirror.get_golden(None), sqlite.get_golden(None))

    def test_migration_requires_team_and_composite_upsert(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "scripts", "migrate_golden_atomic.sql")
        with open(path, encoding="utf-8") as f:
            sql = f.read().lower()
        self.assertIn("unique (team_id, content_hash)", sql)
        self.assertIn("if p_team_id is null", sql)
        self.assertIn("on conflict (team_id, content_hash) do update", sql)
        self.assertIn("delete from public.prism_golden where team_id = p_team_id", sql)


if __name__ == "__main__":
    unittest.main()
