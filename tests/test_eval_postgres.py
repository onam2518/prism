"""실제 PostgreSQL의 평가 시작 RPC 동시성·권한·반복 적용 계약(원격 DB 없음)."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest


_PG_BIN = "/opt/homebrew/bin"


def _pg(name):
    return os.path.join(_PG_BIN, name) if os.path.exists(os.path.join(_PG_BIN, name)) else shutil.which(name)


@unittest.skipUnless(all(_pg(name) for name in ("initdb", "pg_ctl", "psql")),
                     "로컬 PostgreSQL 바이너리 미설치")
class TestEvalPostgres(unittest.TestCase):
    TEAM = "11111111-1111-1111-1111-111111111111"
    OTHER = "22222222-2222-2222-2222-222222222222"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="prism-eval-pg-", dir="/tmp")
        cls.addClassCleanup(cls.tmp.cleanup)
        base = Path(cls.tmp.name)
        cls.db = base / "db"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        subprocess.run([_pg("initdb"), "-D", str(cls.db), "-A", "trust", "-U", "postgres",
                        "--no-locale", "--encoding=UTF8"], check=True, capture_output=True)
        subprocess.run([_pg("pg_ctl"), "-D", str(cls.db), "-l", str(base / "server.log"),
                        "-o", "-F -c listen_addresses='' -k %s -p %s" % (base, cls.port),
                        "-w", "start"], check=True, capture_output=True)
        cls.addClassCleanup(lambda: subprocess.run(
            [_pg("pg_ctl"), "-D", str(cls.db), "-m", "immediate", "-w", "stop"],
            capture_output=True, check=True))
        cls.psql = [_pg("psql"), "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-h", str(base),
                    "-p", str(cls.port), "-U", "postgres", "-d", "postgres"]
        cls.sql("""
            CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role;
            ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO anon, authenticated;
            CREATE TABLE prism_eval_runs (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, team_id uuid,
                model text NOT NULL DEFAULT '', scope text NOT NULL DEFAULT 'all',
                status text NOT NULL, cursor integer NOT NULL DEFAULT 0, total integer NOT NULL,
                created_by text NOT NULL DEFAULT ''
            );
            GRANT USAGE ON SCHEMA public TO service_role;
            GRANT ALL ON prism_eval_runs TO service_role;
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;
        """)
        cls.migration = (Path(__file__).parents[1] / "migrations" /
                         "20260906_eval_run_basis_fingerprint.sql").read_text()
        cls.sql(cls.migration)

    @classmethod
    def sql(cls, text, check=True, env=None):
        result = subprocess.run(cls.psql, input=text, text=True, capture_output=True, timeout=10, env=env)
        if check and result.returncode:
            raise AssertionError(result.stderr[:1200])
        return result.stdout.strip() if check else result

    @classmethod
    def call(cls, team, *, fresh=False):
        return "SELECT public.prism_eval_run_start_or_reuse('%s', 'model', 'all', 3, 'tester', 'sha256:test', %s);" % (
            team, "true" if fresh else "false")

    def setUp(self):
        self.sql("TRUNCATE prism_eval_runs RESTART IDENTITY;")

    def test_concurrent_start_reuses_one_run_and_explicit_experiment_creates_new(self):
        env = dict(os.environ, PGAPPNAME="prism-eval-first")
        first = subprocess.Popen(self.psql, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, env=env)
        try:
            first.stdin.write("BEGIN; " + self.call(self.TEAM) + " SELECT pg_sleep(1); COMMIT;")
            first.stdin.close()
            first.stdin = None
            deadline = time.monotonic() + 5
            while self.sql("SELECT coalesce(wait_event, '') FROM pg_stat_activity "
                           "WHERE application_name='prism-eval-first'") != "PgSleep":
                if first.poll() is not None or time.monotonic() > deadline:
                    self.fail("첫 시작의 미커밋 상태를 확인하지 못했습니다")
                time.sleep(0.01)
            second = json.loads(self.sql(self.call(self.TEAM)))
            out, error = first.communicate(timeout=5)
            self.assertEqual(first.returncode, 0, error)
            first_result = json.loads(out.splitlines()[0])
        finally:
            if first.poll() is None:
                first.kill()
            first.communicate(timeout=5)
        self.assertEqual({first_result["id"], second["id"]}, {first_result["id"]})
        self.assertEqual(sorted([first_result["reused"], second["reused"]]), [False, True])
        self.assertEqual(self.sql("SELECT count(*) FROM prism_eval_runs"), "1")
        fresh = json.loads(self.sql(self.call(self.TEAM, fresh=True)))
        self.assertFalse(fresh["reused"])
        self.assertNotEqual(fresh["id"], first_result["id"])

    def test_teams_are_isolated(self):
        one = json.loads(self.sql(self.call(self.TEAM)))
        other = json.loads(self.sql(self.call(self.OTHER)))
        again = json.loads(self.sql(self.call(self.TEAM)))
        self.assertNotEqual(one["id"], other["id"])
        self.assertEqual(again, {"id": one["id"], "reused": True})
        self.assertEqual(self.sql("SELECT count(*) FROM prism_eval_runs"), "2")

    def test_only_service_role_can_execute_and_migration_is_repeat_safe(self):
        for role in ("anon", "authenticated"):
            denied = self.sql("SET ROLE %s; " % role + self.call(self.TEAM), check=False)
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("permission denied for function prism_eval_run_start_or_reuse", denied.stderr)
        self.assertIn('"reused": false', self.sql("SET ROLE service_role; " + self.call(self.TEAM)))
        self.sql(self.migration)
        self.assertEqual(self.sql("SELECT count(*) FROM prism_eval_runs"), "1")


if __name__ == "__main__":
    unittest.main()
