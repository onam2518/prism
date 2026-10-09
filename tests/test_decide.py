import unittest

from prism import abtest, decide as DC, dictionaries as D


def _ch(probs):
    return {"type": "choice", "choice": max(probs, key=probs.get), "probabilities": probs}


class DecideTest(unittest.TestCase):
    def test_questions_contract(self):
        q = DC.questions()
        self.assertEqual(len(q), 6)                          # 등급 1 · 인텐트 4 · 대분류 1
        q.update(DC.cat2_questions(range(len(D.CONTENT_CATEGORY_TIER2))))
        for k, v in q.items():                               # 운영 422 재발 방지: 선택지 26개 이하 · 한 글자 라벨 · 해당 없음
            self.assertEqual(v["type"], "choice")
            self.assertLessEqual(len(v["criteria"]), 26)
            self.assertTrue(all(len(x) == 1 and x in DC._LABELS for x in v["criteria"]))
            self.assertIn(DC._NONE, v["criteria"])
        names = [n for _, _, ns in DC._intent_groups() for n in ns]
        self.assertEqual(sorted(names), sorted(D.INTENT_VALUE_DEFS))   # 인텐트 68 이 빠짐·중복 없이 4문항에
        bare = DC.questions(defs=False)["int_u"]["criteria"]["A"]
        self.assertNotIn(":", bare)                          # 이름만 모드

    def test_judge_maps_choices(self):
        metas, t1s = list(D.QUALITY_METAS), list(D.CONTENT_CATEGORY_TIER2)
        ans = {"grade": _ch({"A": 0.55, "E": 0.35, "Z": 0.10}),
               "int_u": _ch({"B": 0.6, "Z": 0.4}), "int_f": _ch({"Z": 0.9, "A": 0.1}),
               "int_c1": _ch({"C": 0.45, "D": 0.35, "Z": 0.2}), "int_c2": _ch({"Z": 0.8, "A": 0.2}),
               "cat1": _ch({"A": 0.6, "C": 0.35, "Z": 0.05}),
               "cat2_0": _ch({"B": 0.7, "A": 0.3}), "cat2_2": _ch({"A": 0.9, "Z": 0.1})}
        calls = []

        def fake(body, key):
            calls.append((sorted(body["questions"]), len(body["state"]["body"])))
            return {"answers": {k: ans[k] for k in body["questions"]}, "usage": {"input_tokens": 1000, "output_tokens": 0}}

        out = DC.judge({"title": "t", "body": "x" * 5000}, 0.5, fake, "k")
        self.assertEqual(out["quality_meta"], {"finalGrade": "R", "reasons": [metas[0], metas[4]], "review": ""})
        u, c1 = DC._intent_groups()[0][2], DC._intent_groups()[2][2]
        self.assertEqual(out["item_meta"]["intent"], [u[1], c1[2], c1[3]])      # 해당 없음 제외 · PICK_MIN 이상 · 확률 순
        path = lambda i, j: f"{t1s[i]} / {D.CONTENT_CATEGORY_TIER2[t1s[i]][j]}"
        self.assertEqual(out["item_meta"]["content_category"], [path(0, 1), path(2, 0)])
        self.assertEqual(out["conf"], {"grade": 0.9, "intent": 0.6, "cat": 0.6})
        self.assertEqual(out["trace"]["requests"], 3)
        self.assertAlmostEqual(out["trace"]["cost_usd"], 3000 * DC.PRICE_IN, 6)
        bodies = {tuple(q): n for q, n in calls}
        self.assertEqual(bodies[("grade",)], 5000)                              # 유해 판정만 본문 전체
        self.assertTrue(all(n == DC.LEAD_MAX for q, n in calls if q != ["grade"]))   # 나머지는 앞부분만
        g = DC.to_output({"answers": {"grade": _ch({"Z": 0.8, "A": 0.2})}})
        self.assertEqual(g["quality_meta"]["finalGrade"], "G"); self.assertEqual(g["quality_meta"]["reasons"], [])
        rows = [{"content": {}, "expected": {"finalGrade": "R", "reasons": [metas[0]], "intent": [u[1]],
                                             "content_category": [path(0, 1)]}}]
        m = abtest.score(rows, [out])
        self.assertEqual(m["grade_accuracy"], 1.0); self.assertEqual(m["intent_hit"], 1.0)
        for k in DC.REPORT_KEYS:
            self.assertIn(k, m)
        sr = DC.selective_report(rows, [out])
        self.assertEqual(sr["grade"]["n"], 1); self.assertEqual(sr["intent"]["acc"], 1.0); self.assertEqual(sr["cat"]["acc"], 1.0)

    def test_selective(self):
        pairs = [(0.95, True), (0.92, True), (0.85, False), (0.65, True), (0.55, False)]
        s = DC.selective(pairs)
        r9 = next(r for r in s["rows"] if r["t"] == 0.9)
        self.assertEqual((r9["n"], r9["coverage"], r9["acc"]), (2, 0.4, 1.0))   # 기준 0.9 → 40% 처리 · 정확도 100%
        self.assertEqual(s["rows"][0]["coverage"], 1.0); self.assertEqual(s["acc"], 0.6)
        b9 = next(b for b in s["bins"] if b["lo"] == 0.9)
        self.assertEqual((b9["n"], b9["acc"]), (2, 1.0))
        self.assertAlmostEqual(s["ece"], (2 * abs(1 - 0.935) + abs(0 - 0.85) + abs(1 - 0.65) + abs(0 - 0.55)) / 5, 3)
        self.assertIsNone(DC.selective([])["acc"])

    def test_golden_job_mock(self):
        """골든셋 일괄 잡: mock 서버 → 시작·폴링·리포트(엔티티·리드문 키는 리포트에서 뺀다 · selective 포함)."""
        import time
        from prism import serve
        from tests.test_evalops import _mk_store, _seed_golden
        st = _mk_store(); serve._STORE = st
        orig = serve.Handler.server_mock; serve.Handler.server_mock = True
        self.addCleanup(lambda: (setattr(serve, "_STORE", None), setattr(serve.Handler, "server_mock", orig)))
        _seed_golden(st, 4)
        r = DC.start(n=3, defs=False)
        self.assertTrue(r["ok"], r); self.assertEqual(r["total"], 3)
        t0 = time.time()
        while DC.status(r["id"]).get("running") and time.time() - t0 < 10:
            time.sleep(0.05)
        s = DC.status(r["id"])
        self.assertEqual(s["done"], 3); self.assertEqual(s["report"]["n"], 3); self.assertFalse(s["defs"])
        self.assertNotIn("ent_f1", s["report"]); self.assertEqual(s["report"]["selective"]["grade"]["n"], 3)
        self.assertFalse(DC.status(r["id"], "other-team")["ok"])           # 다른 팀은 진척·결과를 볼 수 없다
        self.assertNotIn("team", s)


if __name__ == "__main__":
    unittest.main()
