"""자동 운영 lease: 독립 스토어/겹친 실행/만료/팀 격리 및 PostgREST 요청 계약."""
import tempfile
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from urllib.parse import parse_qs

from prism.store import Store
from prism.supastore import SupabaseStore
import test_audit_ops as audit


class LeaseContract:
    def test_overlap_expiry_and_stale_release(self):
        ready = threading.Event()
        release = threading.Event()

        def first():
            self.assertTrue(self.st.claim_crew_auto('old', 100, 10, 'a'))
            ready.set()
            self.assertTrue(release.wait(5))

        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(first)
            try:
                self.assertTrue(ready.wait(5))
                self.assertFalse(self.other.claim_crew_auto('new', 109, 10, 'a'))
                self.assertTrue(self.other.claim_crew_auto('other-team', 109, 10, 'b'))
                self.assertTrue(self.other.claim_crew_auto('new', 110, 10, 'a'))
                self.st.release_crew_auto('old', 'a')
                self.assertFalse(self.st.claim_crew_auto('third', 111, 10, 'a'))
                self.other.release_crew_auto('new', 'a')
                self.assertTrue(self.st.claim_crew_auto('third', 111, 10, 'a'))
            finally:
                release.set()
            pending.result()


class SQLiteLease(LeaseContract, unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.st = Store(tmp.name + '/lease.db')
        self.other = Store(self.st.path)

    def test_another_process_cannot_claim_active_team(self):
        code = ("from prism.store import Store; import sys; "
                "s=Store(sys.argv[1]); print(s.claim_crew_auto('child',100,10,'a'),flush=True); "
                "sys.stdin.readline(); s.release_crew_auto('child','a')")
        child = subprocess.Popen([sys.executable, '-c', code, self.st.path],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'True')
            self.assertFalse(self.st.claim_crew_auto('parent', 101, 10, 'a'))
            child.communicate('done\n', timeout=5)
            self.assertEqual(child.returncode, 0)
            self.assertTrue(self.st.claim_crew_auto('parent', 101, 10, 'a'))
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()


class SupabaseLease(LeaseContract, unittest.TestCase):
    def setUp(self):
        # Model the server's atomic statements; assert exact REST predicates and preferences.
        rows, lock = {}, threading.Lock()

        def req(method, table, *, body=None, query='', prefer=''):
            self.assertEqual(table, 'reports')
            with lock:
                if method == 'POST':
                    self.assertEqual(prefer, 'resolution=ignore-duplicates,return=representation')
                    row = body[0]
                    key = (row['kind'], row['team_key'])
                    if key in rows:
                        return []
                    rows[key] = row['payload']
                    return [row]
                q = parse_qs(query, keep_blank_values=True)
                key = ('crew_auto_lease', q['team_key'][0][3:])
                self.assertEqual(q['kind'], ['eq.crew_auto_lease'])
                row = rows.get(key)
                if method == 'PATCH':
                    self.assertEqual(prefer, 'return=representation')
                    cutoff = float(q['payload->expires'][0][4:])
                    self.assertTrue(q['payload->expires'][0].startswith('lte.'))
                    if row and row['expires'] <= cutoff:
                        rows[key] = body['payload']
                        return [body]
                    return []
                self.assertEqual(method, 'DELETE')
                if row and row['owner'] == q['payload->>owner'][0][3:]:
                    del rows[key]
                return []

        self.st, self.other = object.__new__(SupabaseStore), object.__new__(SupabaseStore)
        self.st._req = self.other._req = req


class TickOverlap(audit.TestAutoTickClaimsCycle):
    def test_second_tick_starts_while_first_is_distributing(self):
        serve = self._serve()
        st = self._fixture(serve)
        entered, finish = threading.Event(), threading.Event()
        original = serve.CRW.plan_distribute

        def paused(*args, **kwargs):
            entered.set()
            self.assertTrue(finish.wait(5))
            return original(*args, **kwargs)

        with mock.patch.object(serve.CRW, 'plan_distribute', side_effect=paused) as work:
            with ThreadPoolExecutor(1) as pool:
                first = pool.submit(serve.CRW.auto_tick)
                try:
                    self.assertTrue(entered.wait(5))
                    second = serve.CRW.auto_tick()
                    self.assertIsNone(second['wave'])
                    self.assertEqual(work.call_count, 1)
                finally:
                    finish.set()
                self.assertTrue(first.result()['wave']['ok'])
        slots = sum(len(a['reviewers']) for a in st.assignees(None).values())
        self.assertEqual(sum(serve.CRW.wave().get('plan', {}).values()), slots)

    def test_claim_and_state_errors_fail_closed(self):
        serve = self._serve()
        st = self._fixture(serve)
        for method in ('claim_crew_auto', 'get_report'):
            with self.subTest(method=method), mock.patch.object(st, method, side_effect=RuntimeError('offline')):
                with mock.patch.object(serve.CRW, 'plan_distribute') as work:
                    with self.assertRaises(RuntimeError):
                        serve.CRW.auto_tick()
                    work.assert_not_called()

    def test_dry_run_writes_nothing(self):
        serve = self._serve()
        st = self._fixture(serve)
        before = st._conn().total_changes
        with mock.patch.object(st, 'claim_crew_auto', side_effect=AssertionError('dry-run claim')):
            serve.CRW.auto_tick(apply=False)
        self.assertEqual(st._conn().total_changes, before)

    def test_successful_wave_survives_later_stage_exception(self):
        serve = self._serve()
        self._fixture(serve)
        serve.CRW.set_settings({"auto_escalate": 1})
        with mock.patch.object(serve.CRW, 'escalate_split', side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError):
                serve.CRW.auto_tick()
        with mock.patch.object(serve.CRW, 'plan_distribute') as work:
            serve.CRW.auto_tick()
            work.assert_not_called()
