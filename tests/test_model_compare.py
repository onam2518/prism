"""모델별 비교(learnops.compare_models_on_golden): 모델 간 병렬 · 건별 비교표 · 영속 · 조회 라우트.

실행: python3 -m unittest tests.test_model_compare  (stdlib unittest · 의존성 0)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_evalops import _mk_store, _seed_golden  # noqa: E402


class TestCompareModels(unittest.TestCase):
    def setUp(self):
        from prism import serve
        self.st = _mk_store()
        self._orig_store = serve.get_store
        self._orig_mock = serve.Handler.server_mock
        serve.get_store = lambda: self.st
        serve.Handler.server_mock = True                 # 외부 호출 0(결정론 mock)
        self.addCleanup(lambda: (setattr(serve, "get_store", self._orig_store),
                                 setattr(serve.Handler, "server_mock", self._orig_mock)))

    def test_multi_model_items_and_persist(self):
        from prism import learnops as LO
        _seed_golden(self.st, 4)
        r = LO.compare_models_on_golden(["solar-pro2", "gpt-5.4", "solar-pro2"])   # 중복은 1개로
        self.assertTrue(r["ok"], r)
        self.assertEqual(sorted(m["model"] for m in r["models"]), ["gpt-5.4", "solar-pro2"])
        self.assertEqual(r["golden_n"], 4)
        self.assertEqual(len(r["items"]), 4)
        it = r["items"][0]
        self.assertIn(it["expected"]["grade"], ("G", "R"))
        self.assertEqual(set(it["got"]), {"gpt-5.4", "solar-pro2"})
        for g in it["got"].values():
            self.assertIn("grade", g); self.assertIn("ok", g); self.assertIn("empty", g)
        self.assertIsInstance(it["split"], bool); self.assertIsInstance(it["all_ok"], bool)
        self.assertEqual(r["miss_n"], sum(1 for x in r["items"] if not x["all_ok"]))
        self.assertIn("ts", r)
        # 영속: 마지막 비교를 그대로 되돌려 준다
        last = LO.last_model_compare()
        self.assertTrue(last["ok"])
        self.assertEqual(last["best"], r["best"])
        self.assertEqual(len(last["items"]), 4)

    def test_no_saved_compare(self):
        from prism import learnops as LO
        self.assertFalse(LO.last_model_compare()["ok"])

    def test_too_many_models(self):
        from prism import learnops as LO
        _seed_golden(self.st, 2)
        r = LO.compare_models_on_golden([f"m{i}" for i in range(7)])
        self.assertFalse(r["ok"])
        self.assertIn("6개", r["error"])

    def test_route_registered(self):
        from prism import serve
        self.assertIn("/model-compare-last", serve._GET_ROUTES)
