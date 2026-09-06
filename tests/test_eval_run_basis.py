"""평가 런 기준 고정·동일 시작 재사용 계약(SQLite/Supabase, 외부 호출 없음)."""
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _store():
    from prism.store import Store
    return Store(os.path.join(tempfile.mkdtemp(), "eval-basis.db"))


def _seed(st, n=3):
    from prism.store import content_hash
    for i in range(n):
        content = {"displayServiceName": "뉴스", "title": "기준 %s" % i,
                   "subtitle": "", "body": "본문 %s" % i}
        st.upsert_golden(content_hash(content), content,
                         {"finalGrade": "G", "reasons": []})


class TestStoreEvalRunBasis(unittest.TestCase):
    def test_fingerprint_roundtrip_and_parallel_start_reuse(self):
        st = _store()
        fingerprint = "sha256:" + "a" * 64
        gate = threading.Barrier(4)
        started, errors = [], []

        def start():
            try:
                gate.wait()
                started.append(st.eval_run_start_or_reuse("", "m", "all", 3, fingerprint))
            except Exception as e:
                errors.append(e)

        workers = [threading.Thread(target=start) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(errors, [])
        self.assertEqual({item["id"] for item in started}, {started[0]["id"]})
        self.assertEqual(sum(not item["reused"] for item in started), 1)
        run = st.eval_run_get(started[0]["id"])
        self.assertEqual(run["basis_fingerprint"], fingerprint)
        separate = st.eval_run_start_or_reuse("", "m", "all", 3, fingerprint,
                                               new_experiment=True)
        self.assertFalse(separate["reused"])
        self.assertNotEqual(separate["id"], run["id"])


class TestEvalRunBasisFlow(unittest.TestCase):
    def setUp(self):
        from prism import evalops, serve
        self.evalops, self.serve, self.st = evalops, serve, _store()
        self.previous_store = serve._STORE
        self.previous_mock = serve.Handler.server_mock
        self.previous_launch = evalops._launch
        serve._STORE = self.st
        serve.Handler.server_mock = True
        self.launches = []
        evalops._launch = lambda *args: self.launches.append(args)  # 영속 상태만 검증: LLM 호출 없음

    def tearDown(self):
        self.serve._STORE = self.previous_store
        self.serve.Handler.server_mock = self.previous_mock
        self.evalops._launch = self.previous_launch

    def test_changed_golden_refuses_resume_but_unchanged_resumes(self):
        _seed(self.st)
        started = self.serve.eval_run_start(None)
        self.assertTrue(started["ok"])
        same = self.serve.eval_run_resume(started["id"], None)
        self.assertTrue(same["ok"], same)
        self.assertEqual(same["remain"], 3)

        row = self.st.get_golden(None)[0]
        from prism.store import content_hash
        self.st.upsert_golden(content_hash(row["content"]), row["content"],
                              {"finalGrade": "R", "reasons": ["adult"]})
        changed = self.serve.eval_run_resume(started["id"], None)
        self.assertFalse(changed["ok"])
        self.assertIn("새 평가", changed["error"])

    def test_fingerprint_is_order_independent_and_tracks_expected(self):
        _seed(self.st, 2)
        rows = self.st.get_golden(None)
        before = self.evalops._golden_fingerprint(rows)
        self.assertEqual(before, self.evalops._golden_fingerprint(list(reversed(rows))))
        changed = [dict(row, expected=dict(row["expected"], finalGrade="R")) for row in rows]
        self.assertNotEqual(before, self.evalops._golden_fingerprint(changed))

    def test_legacy_run_and_max_rows_change_require_safe_new_start(self):
        _seed(self.st, 3)
        legacy = self.st.eval_run_create("", "", "all", 3)
        old = self.serve.eval_run_resume(legacy, None)
        self.assertFalse(old["ok"])
        self.assertIn("기준 지문", old["error"])

        prior = self.evalops.MAX_ROWS
        self.evalops.MAX_ROWS = 2
        try:
            started = self.serve.eval_run_start(None)
            self.assertTrue(started["ok"])
            self.evalops.MAX_ROWS = 3
            changed = self.serve.eval_run_resume(started["id"], None)
        finally:
            self.evalops.MAX_ROWS = prior
        self.assertFalse(changed["ok"])
        self.assertIn("골든 기준", changed["error"])

    def test_parallel_start_reuses_and_explicit_experiment_does_not(self):
        _seed(self.st)
        gate = threading.Barrier(2)
        got, errors = [], []

        def start():
            try:
                gate.wait()
                got.append(self.serve.eval_run_start(None, model="", scope="all"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=start) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual({item["id"] for item in got}, {got[0]["id"]})
        self.assertEqual(sum(not item["reused"] for item in got), 1)
        self.assertEqual(len(self.launches), 1)
        fresh = self.serve.eval_run_start(None, new_experiment=True)
        self.assertTrue(fresh["ok"])
        self.assertFalse(fresh["reused"])
        self.assertNotEqual(fresh["id"], got[0]["id"])


class TestSupabaseEvalRunBasisContract(unittest.TestCase):
    def test_atomic_rpc_contract_and_no_fallback(self):
        from prism.supastore import SupabaseStore
        st = SupabaseStore.__new__(SupabaseStore)
        captured = {}
        st._rpc_required = lambda fn, args: (captured.update(fn=fn, args=args) or
                                             {"id": 12, "reused": True})
        st._req = lambda *args, **kwargs: self.fail("RPC 실패 시 REST create 폴백은 금지")
        got = st.eval_run_start_or_reuse("team-A", "m", "eval", 5, "sha256:x",
                                         created_by="uid", new_experiment=True)
        self.assertEqual(got, {"id": 12, "reused": True})
        self.assertEqual(captured["fn"], "prism_eval_run_start_or_reuse")
        self.assertEqual(captured["args"], {"p_team": "team-A", "p_model": "m",
                                             "p_scope": "eval", "p_total": 5,
                                             "p_created_by": "uid", "p_basis_fingerprint": "sha256:x",
                                             "p_new_experiment": True})

        st._rpc_required = lambda *args: None
        with self.assertRaises(RuntimeError):
            st.eval_run_start_or_reuse("team-A", "m", "all", 1, "sha256:x")


if __name__ == "__main__":
    unittest.main()
