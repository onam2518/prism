import unittest

from prism import abtest, decide as DC, dictionaries as D


class DecideTest(unittest.TestCase):
    def test_questions_contract(self):
        q = DC.questions()
        self.assertEqual(len(q), len(D.QUALITY_METAS) + len(D.INTENT_VALUE_DEFS) + 1)
        self.assertLessEqual(len(q), DC.MAX_QUESTIONS)   # 한 요청 질문 상한(운영 422 재발 방지)
        q.update(DC.cat2_questions(range(len(D.CONTENT_CATEGORY_TIER2))))
        for k, v in q.items():
            if v["type"] == "noul":
                self.assertNotIn("criteria", v)          # noul 은 criteria 키 자체를 빼야 한다(null 도 거부)
            else:                                        # choice 라벨 = 한 글자 · 문항당 26개 상한(운영 422 재발 방지)
                self.assertLessEqual(len(v["criteria"]), 26)
                self.assertTrue(all(len(k2) == 1 and k2 in DC._LABELS for k2 in v["criteria"]))
        self.assertIn(DC._NONE, q["cat1"]["criteria"])

    def test_to_output_and_score(self):
        names = list(D.INTENT_VALUE_DEFS)
        t1s = list(D.CONTENT_CATEGORY_TIER2)
        path = lambda i, j: f"{t1s[i]} / {D.CONTENT_CATEGORY_TIER2[t1s[i]][j]}"
        ans = {k: {"type": "noul", "noul": 0.0} for k in DC.questions() if not k.startswith("cat")}
        ans["q_ad"]["noul"] = 0.8
        ans["i3"]["noul"] = 0.9; ans["i5"]["noul"] = 0.6; ans["i7"]["noul"] = 0.55; ans["i9"]["noul"] = 0.51
        ans["cat1"] = {"type": "choice", "choice": "A", "probabilities": {"A": 0.6, "C": 0.35, "Z": 0.05}}
        ans["cat2_0"] = {"type": "choice", "choice": "B", "probabilities": {"B": 0.7, "A": 0.3}}
        ans["cat2_2"] = {"type": "choice", "choice": "A", "probabilities": {"A": 0.9}}
        calls = []
        def fake(body, key):
            calls.append(sorted(body["questions"]))
            return {"answers": {k: v for k, v in ans.items() if k in body["questions"]},
                    "usage": {"input_tokens": 2500, "output_tokens": 0}}
        out = DC.judge({"title": "t", "body": "b"}, 0.5, fake, "k")
        self.assertEqual(out["quality_meta"], {"finalGrade": "R", "reasons": ["ad"], "review": ""})
        self.assertEqual(out["item_meta"]["intent"], [names[3], names[5], names[7]])   # 임계 이상 상위 INTENT_MAX 개
        self.assertEqual(out["item_meta"]["content_category"], [path(0, 1), path(2, 0)])   # 1차 2순위 ≥ CAT_SECOND 면 그 경로도
        self.assertAlmostEqual(out["trace"]["cost_usd"], 5000 * DC.PRICE_IN, 6)   # 두 호출 토큰 합
        self.assertEqual(calls[1], ["cat2_0", "cat2_2"])                           # 2차는 고른 대분류만
        none = DC.to_output({"answers": {"cat1": {"choice": "Z", "probabilities": {"Z": 0.9}}}})
        self.assertEqual(none["item_meta"]["content_category"], [])
        self.assertEqual(none["quality_meta"]["finalGrade"], "G")
        rows = [{"content": {}, "expected": {"finalGrade": "R", "reasons": ["ad"], "intent": [names[3], "x"],
                                             "content_category": [path(0, 1)]}}]
        m = abtest.score(rows, [out])
        self.assertEqual(m["grade_accuracy"], 1.0); self.assertEqual(m["intent_hit"], 1.0)
        for k in DC.REPORT_KEYS:
            self.assertIn(k, m)                          # 리포트 키가 score 출력에 다 있다

    def test_golden_job_mock(self):
        """골든셋 일괄 잡: mock 서버 → 시작·폴링·리포트(엔티티·리드문 키는 리포트에서 뺀다)."""
        import time
        from prism import serve
        from tests.test_evalops import _mk_store, _seed_golden
        st = _mk_store(); serve._STORE = st
        orig = serve.Handler.server_mock; serve.Handler.server_mock = True
        self.addCleanup(lambda: (setattr(serve, "_STORE", None), setattr(serve.Handler, "server_mock", orig)))
        _seed_golden(st, 4)
        r = DC.start(n=3)
        self.assertTrue(r["ok"], r); self.assertEqual(r["total"], 3)
        t0 = time.time()
        while DC.status(r["id"]).get("running") and time.time() - t0 < 10:
            time.sleep(0.05)
        s = DC.status(r["id"])
        self.assertEqual(s["done"], 3); self.assertIsNotNone(s["report"]); self.assertEqual(s["report"]["n"], 3)
        self.assertNotIn("ent_f1", s["report"])


if __name__ == "__main__":
    unittest.main()
