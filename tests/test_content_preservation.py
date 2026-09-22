"""콘텐츠 조회 실패·원자적 팀 충돌 계약. 실 API 없이 mock과 일회용 로컬 PostgreSQL만 사용."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

from prism import runops
from prism.supastore import SupabaseStore


class TestContentWriteFailures(unittest.TestCase):
    CONTENT = {"displayServiceName": "뉴스", "title": "보존", "body": "본문"}
    OUT = {"quality_meta": {"review": "yellow"}, "item_meta": {}, "trace": {}}

    def test_existing_lookup_failure_never_saves_or_reports_success(self):
        st = Mock()
        st.existing_hashes.side_effect = RuntimeError("lookup unavailable")
        with patch.object(runops, "_SV", SimpleNamespace(get_store=lambda: st)), \
                patch.object(runops, "store_save") as save:
            result = runops.add_contents([self.CONTENT], team="A", purpose="eval")
        self.assertIn("다시 시도", result["error"])
        self.assertNotIn("ok", result)
        save.assert_not_called()
        st.set_purpose.assert_not_called()
        self.assertEqual(st.existing_hashes.call_args.kwargs["team"], "A")

    def store(self, status=200):
        st = SupabaseStore.__new__(SupabaseStore)
        st.url, st.base, st.key = "http://unused.invalid", "http://unused.invalid/rest/v1", "test"
        st._get = Mock(return_value=[])
        st._http = Mock(return_value=(status, "null" if status == 200 else '{"code":"failure"}', {}))
        return st

    def test_preservation_lookup_failure_stops_every_save_entry(self):
        for entry in ("sync_contents", "save_many", "save_dedup"):
            with self.subTest(entry=entry):
                st = self.store()
                st._get.side_effect = RuntimeError("preservation lookup failed")
                with self.assertRaisesRegex(RuntimeError, "lookup failed"):
                    getattr(st, entry)([(self.CONTENT, self.OUT)], team="A")
                st._http.assert_not_called()

    def test_rpc_missing_conflict_and_server_failure_have_no_rest_fallback(self):
        for status in (404, 409, 500):
            with self.subTest(status=status):
                st = self.store(status)
                with self.assertRaisesRegex(RuntimeError, "HTTP{}".format(status)):
                    st.save_dedup([(self.CONTENT, self.OUT)], team="A")
                self.assertEqual(st._http.call_count, 1)
                self.assertEqual(st._http.call_args.args[:2], ("POST", "/rest/v1/rpc/prism_sync_contents"))

    def test_rpc_receives_deduplicated_rows_with_explicit_team(self):
        st = self.store()
        self.assertEqual(st.sync_contents([(self.CONTENT, self.OUT)] * 2, team="A"), 1)
        call = st._http.call_args
        self.assertEqual(call.args[:2], ("POST", "/rest/v1/rpc/prism_sync_contents"))
        self.assertEqual(json.loads(call.args[2])["p_rows"][0]["team_id"], "A")
        self.assertEqual(len(json.loads(call.args[2])["p_rows"]), 1)

    def test_empty_or_filtered_batch_never_writes(self):
        st = self.store()
        self.assertEqual(st.sync_contents([]), 0)
        self.assertEqual(st.sync_contents([(self.CONTENT, {})]), 0)
        st._http.assert_not_called()

    def test_content_read_update_delete_keep_team_scope(self):
        st = self.store()
        saved = {"hash": "a" * 16, "team_id": "A", "item_meta": {"summary": "original"}}

        def get(table, query=""):
            return [saved] if parse_qs(query).get("team_id") == ["eq.A"] else []

        st._get = Mock(side_effect=get)
        st._req = Mock(return_value=[])
        self.assertIsNone(st.get_item_meta(saved["hash"], team="B"))
        self.assertFalse(st.update_item_meta(saved["hash"], {"summary": "bad"}, team="B"))
        st._req.assert_not_called()
        self.assertEqual(st.get_item_meta(saved["hash"], team="A"), {"summary": "original"})
        self.assertTrue(st.update_item_meta(saved["hash"], {"summary": "new"}, team="A"))
        self.assertEqual(parse_qs(st._req.call_args.kwargs["query"])["team_id"], ["eq.A"])
        st._req.reset_mock()
        st.remove_content(saved["hash"], team="B")
        for call in st._req.call_args_list:
            team_key = "team_key" if call.args[1] == "drafts" else "team_id"
            self.assertEqual(parse_qs(call.kwargs["query"])[team_key], ["eq.B"])


@unittest.skipUnless(all(shutil.which(tool) for tool in ("initdb", "pg_ctl", "psql")),
                     "로컬 PostgreSQL 실행 파일 미설치 (SQL 검증 미완료)")
class TestContentPostgres(unittest.TestCase):
    """전역 hash PK 문서 계약을 재현. 운영 DDL/접속 설정은 읽지 않는다."""
    A = "11111111-1111-1111-1111-111111111111"
    B = "22222222-2222-2222-2222-222222222222"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="prism-pg-", dir="/tmp")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
        cls.data = cls.tmp.name + "/data"
        subprocess.run(["initdb", "-D", cls.data, "-U", "prism_test", "-A", "trust", "--no-locale", "-E", "UTF8"],
                       env=cls.env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["pg_ctl", "-D", cls.data, "-l", cls.tmp.name + "/server.log", "-o",
                        "-F -k {} -c listen_addresses=''".format(cls.tmp.name), "-w", "start"],
                       env=cls.env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        cls.addClassCleanup(subprocess.run, ["pg_ctl", "-D", cls.data, "-m", "immediate", "-w", "stop"],
                            env=cls.env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        cls.psql = ["psql", "-X", "-qAt", "-h", cls.tmp.name, "-p", "5432", "-U", "prism_test",
                    "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=verbose"]
        cls.sql("""
            create role anon; create role authenticated; create role service_role;
            create table public.prism_contents (
                hash text primary key, team_id uuid, service text, title text, subtitle text,
                body text, source_url text, image_urls jsonb not null default '[]', source text,
                final_grade text, item_meta jsonb, quality_meta jsonb, model text, version integer,
                review text, created_at timestamptz not null default now()
            );
            grant select, insert, update on public.prism_contents to service_role;
        """)
        cls.migration = (Path(__file__).resolve().parents[1] / "scripts/migrate_content_team_guard.sql").read_text()
        cls.sql(cls.migration)

    @classmethod
    def sql(cls, query, check=True):
        return subprocess.run(cls.psql, input=query, text=True, capture_output=True,
                              env=cls.env, check=check, timeout=15)

    @classmethod
    def row(cls, team=None, **kw):
        return dict({"hash": "a" * 16, "team_id": team, "title": "original", "source": "엑셀",
                     "model": "m1", "version": 1, "item_meta": {"summary": "old"},
                     "quality_meta": {"finalGrade": "R", "ops_hold": True,
                                      "source_status": {"state": "gone"}}}, **kw)

    @staticmethod
    def rpc(rows):
        return "select public.prism_sync_contents('{}'::jsonb);".format(
            json.dumps(rows, ensure_ascii=False).replace("'", "''"))

    def snapshot(self):
        return self.sql("select coalesce(jsonb_agg(c order by hash), '[]'::jsonb) from prism_contents c;").stdout.strip()

    def setUp(self):
        self.sql("truncate prism_contents;")

    def test_new_and_same_team_rerun_keep_operator_values(self):
        self.sql(self.rpc([self.row(self.A)]))
        before = json.loads(self.snapshot())[0]
        incoming = self.row(self.A, source="재실행", model="m2", version=2,
                            item_meta={"summary": "new"}, quality_meta={"finalGrade": "G", "ops_hold": False})
        self.sql(self.rpc([incoming]))
        after = json.loads(self.snapshot())[0]
        self.assertEqual((after["model"], after["version"], after["item_meta"]), ("m2", 2, {"summary": "new"}))
        self.assertEqual(after["quality_meta"], dict(before["quality_meta"], finalGrade="G"))
        self.assertEqual((after["source"], after["team_id"], after["created_at"]),
                         (before["source"], self.A, before["created_at"]))

    def test_operator_change_after_python_lookup_wins_over_stale_payload(self):
        stale = self.row(self.A)
        self.sql(self.rpc([stale]))
        self.sql("update prism_contents set quality_meta = '{\"ops_hold\":false,\"source_status\":null}';")
        self.sql(self.rpc([stale]))
        self.assertEqual(json.loads(self.snapshot())[0]["quality_meta"],
                         {"finalGrade": "R", "ops_hold": False, "source_status": None})
        self.sql("update prism_contents set quality_meta = '{}';")
        self.sql(self.rpc([stale]))
        self.assertEqual(json.loads(self.snapshot())[0]["quality_meta"], {"finalGrade": "R"})

    def test_cross_team_and_null_boundaries_roll_back_entire_batch(self):
        for owner, writer in ((self.A, self.B), (self.A, None), (None, self.B)):
            with self.subTest(owner=owner, writer=writer):
                self.sql("truncate prism_contents;")
                self.sql(self.rpc([self.row(owner), self.row(writer, hash="0" * 16)]))
                before = self.snapshot()
                result = self.sql(self.rpc([self.row(writer, hash="0" * 16, title="changed"),
                                           self.row(writer, hash="1" * 16), self.row(writer)]), check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("23505", result.stderr)
                self.assertEqual(self.snapshot(), before)

    def test_null_team_same_owner_and_blank_source(self):
        self.sql(self.rpc([self.row(source="  ")]))
        self.sql(self.rpc([self.row(source="첫 인입", model="m2")]))
        saved = json.loads(self.snapshot())[0]
        self.assertEqual((saved["team_id"], saved["source"], saved["model"]), (None, "첫 인입", "m2"))

    def test_bad_payload_and_late_invalid_row_do_not_write(self):
        self.sql(self.rpc([self.row(self.A)]))
        before = self.snapshot()
        for payload in (None, {}, [self.row(self.A, model="m2"), self.row(self.A, hash="z" * 16, version="bad")]):
            with self.subTest(payload=payload):
                self.assertNotEqual(self.sql(self.rpc(payload), check=False).returncode, 0)
                self.assertEqual(self.snapshot(), before)

    def test_migration_repeat_and_permissions(self):
        self.sql(self.rpc([self.row(self.A)]))
        before = self.snapshot()
        self.sql(self.migration)
        self.assertEqual(self.snapshot(), before)
        for role in ("anon", "authenticated"):
            result = self.sql("set role {}; {}".format(role, self.rpc([self.row(self.A)])), check=False)
            self.assertIn("42501", result.stderr)
        self.sql("set role service_role; " + self.rpc([self.row(self.A, model="m2")]))
        self.assertEqual(json.loads(self.snapshot())[0]["model"], "m2")

    def test_concurrent_first_insert_commits_or_rolls_back_before_other_team(self):
        for finish in ("commit", "rollback"):
            with self.subTest(finish=finish):
                self.sql("truncate prism_contents;")
                with subprocess.Popen(self.psql, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True, env=self.env) as owner:
                    owner.stdin.write("begin; " + self.rpc([self.row(self.A)]) + "select 'ready';\n")
                    owner.stdin.flush()
                    while owner.stdout.readline().strip() != "ready":
                        if owner.poll() is not None:
                            self.fail("owner transaction failed")
                    with subprocess.Popen(self.psql, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True, env=self.env) as contender:
                        contender.stdin.write("set application_name = 'prism_content_contender'; "
                                              + self.rpc([self.row(self.B)]) + "\n")
                        contender.stdin.flush()
                        try:
                            deadline = time.monotonic() + 5
                            while time.monotonic() < deadline:
                                waiting = self.sql("select count(*) from pg_stat_activity where "
                                                   "application_name='prism_content_contender' and wait_event_type='Lock';")
                                if waiting.stdout.strip() == "1":
                                    break
                                time.sleep(.01)
                            else:
                                self.fail("contender did not wait for the conflicting row lock")
                        finally:
                            owner.communicate(finish + ";\n", timeout=10)
                            _, error = contender.communicate(timeout=10)
                    self.assertEqual(owner.returncode, 0)
                    self.assertEqual(contender.returncode == 0, finish == "rollback", error)
                    saved = json.loads(self.snapshot())
                    self.assertEqual(len(saved), 1)
                    self.assertEqual(saved[0]["team_id"], self.A if finish == "commit" else self.B)
                    self.assertEqual(saved[0]["quality_meta"], self.row()["quality_meta"])


if __name__ == "__main__":
    unittest.main()
