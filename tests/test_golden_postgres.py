"""실제 PostgreSQL의 골든 교체·롤백·권한·동시성 계약(설치된 바이너리만 사용)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


@unittest.skipUnless(all(shutil.which(x) for x in ("initdb", "pg_ctl", "psql")),
                     "로컬 PostgreSQL 바이너리 미설치")
class TestGoldenPostgres(unittest.TestCase):
    TEAM = "11111111-1111-1111-1111-111111111111"
    OTHER = "22222222-2222-2222-2222-222222222222"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="prism-golden-pg-", dir="/tmp")
        cls.addClassCleanup(cls.tmp.cleanup)
        base = Path(cls.tmp.name)
        cls.db = base / "db"
        subprocess.run(["initdb", "-D", str(cls.db), "-A", "trust", "-U", "postgres",
                        "--no-locale", "--encoding=UTF8"], check=True, capture_output=True)
        subprocess.run(["pg_ctl", "-D", str(cls.db), "-l", str(base / "server.log"),
                        "-o", "-F -c listen_addresses='' -k %s -p 65432" % base,
                        "-w", "start"], check=True, capture_output=True)
        cls.addClassCleanup(lambda: subprocess.run(
            ["pg_ctl", "-D", str(cls.db), "-m", "immediate", "-w", "stop"],
            capture_output=True, check=True))
        cls.psql = ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-h", str(base),
                    "-p", "65432", "-U", "postgres", "-d", "postgres"]
        cls.sql("""
            CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role;
            ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO anon, authenticated;
            CREATE TABLE prism_golden (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                team_id uuid, content_hash text, content jsonb, expected jsonb,
                source text, created_at timestamptz DEFAULT now(),
                CHECK (expected->>'finalGrade' IN ('G', 'R'))
            );
            GRANT USAGE ON SCHEMA public TO service_role;
            GRANT ALL ON prism_golden TO service_role;
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;
        """)
        migration = Path(__file__).parents[1] / "scripts" / "migrate_golden_atomic.sql"
        cls.sql(migration.read_text())

    @classmethod
    def sql(cls, text, check=True):
        p = subprocess.run(cls.psql, input=text, text=True, capture_output=True, timeout=10)
        if check and p.returncode:
            raise AssertionError(p.stderr[:1200])
        return p.stdout.strip() if check else p

    @staticmethod
    def row(index, grade="G"):
        return {"content_hash": "%016x" % index, "content": {"title": "row-%d" % index},
                "expected": {"finalGrade": grade}}

    @classmethod
    def write(cls, rows, team=None, replace=True):
        payload = json.dumps(rows).replace("'", "''")
        return "SELECT public.prism_write_golden('%s', '%s'::jsonb, %s, 'test');" % (
            team or cls.TEAM, payload, "true" if replace else "false")

    def setUp(self):
        self.sql("TRUNCATE prism_golden;")

    def test_failed_insert_rolls_back_delete_and_keeps_other_team(self):
        self.sql(self.write([self.row(1)]))
        self.sql(self.write([self.row(2)], self.OTHER))
        for bad_at in (0, 500):
            rows = [self.row(i + 10, "X" if i == bad_at else "G") for i in range(501)]
            failed = self.sql(self.write(rows), check=False)
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(self.sql("SELECT content_hash FROM prism_golden ORDER BY content_hash"),
                             "0000000000000001\n0000000000000002")

    def test_only_service_role_can_call_rpc(self):
        for role in ("anon", "authenticated"):
            denied = self.sql("SET ROLE %s; " % role + self.write([self.row(1)]), check=False)
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("permission denied for function prism_write_golden", denied.stderr)
        self.assertEqual(self.sql("SET ROLE service_role; " + self.write([self.row(1)])), "1")

    def test_concurrent_replacements_end_with_one_complete_set(self):
        env = dict(os.environ, PGAPPNAME="prism-golden-first")
        first = subprocess.Popen(self.psql, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, env=env)
        try:
            first.stdin.write("BEGIN; " + self.write([self.row(1)]) + " SELECT pg_sleep(2); COMMIT;")
            first.stdin.close()
            first.stdin = None
            deadline = time.monotonic() + 5
            while self.sql("SELECT coalesce(wait_event, '') FROM pg_stat_activity "
                          "WHERE application_name='prism-golden-first'") != "PgSleep":
                if first.poll() is not None or time.monotonic() > deadline:
                    self.fail("첫 교체의 미커밋 상태를 확인하지 못했습니다")
                time.sleep(0.01)
            self.sql(self.write([self.row(2)]))
            _, error = first.communicate(timeout=5)
            self.assertEqual(first.returncode, 0, error)
            self.assertEqual(self.sql("SELECT content_hash FROM prism_golden"), "0000000000000002")
        finally:
            if first.poll() is None:
                first.kill()
            first.communicate(timeout=5)
