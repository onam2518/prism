"""메타 채점(metaeval): 카테고리 계층 F1 · 엔티티 정규화 F1 · 리드문 2-gram · 종합·게이트 · 진단·쿡북 · 반영 라우트."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import metaeval as ME  # noqa: E402

EXP = {"content_category": ["News and Politics / Politics"], "entities": ["삼성전자(주)", "이재명"],
       "summary": "삼성전자가 새 반도체 공장을 짓는다.", "intent": ["정보"]}
OUT = {"item_meta": {"content_category": ["News and Politics / Society"], "entities": ["삼성전자", "김철수"],
                     "summary": "삼성전자가 반도체 공장을 새로 짓는다", "intent": ["정보"]}}


class TestMetaScore(unittest.TestCase):
    def test_tally_report(self):
        acc = {}
        ME.meta_tally(acc, EXP, OUT)
        r = ME.meta_report(acc)
        self.assertEqual(r["cat_f1"], 0.0)                 # 정확 경로 불일치
        self.assertEqual(r["cat_hf1"], 0.5)                # 부모(News and Politics)만 일치 → 반점
        self.assertEqual(r["ent_f1"], 0.5)                 # ㈜ 제거 후 삼성전자 일치 · 이재명 누락 · 김철수 과다
        self.assertGreater(r["summary_sim"], 0.6)
        self.assertEqual(r["cat_confusion"][0]["n"], 1)
        self.assertEqual(r["ent_missed"][0]["name"], "이재명")

    def test_survives_json_roundtrip(self):
        """evalops 는 acc 를 청크마다 JSON 으로 저장·재개 시 다시 읽는다(Counter·튜플 키는
        왕복 후 plain dict 로 바뀐다) · 재개 후 meta_tally/meta_report 가 그대로 동작해야 한다."""
        import json
        acc = {}
        ME.meta_tally(acc, EXP, OUT)
        acc = json.loads(json.dumps(acc, ensure_ascii=False))    # 저장→재개 흉내
        ME.meta_tally(acc, EXP, OUT)                              # 재개 후 이어서 누적
        r = ME.meta_report(acc)
        self.assertEqual(r["cat_n"], 2)
        self.assertEqual(r["cat_confusion"][0]["n"], 2)
        self.assertEqual(r["ent_missed"][0]["name"], "이재명")

    def test_empty_expected_skipped_and_none_out(self):
        acc = {}
        ME.meta_tally(acc, {"content_category": [], "entities": [], "summary": ""}, OUT)
        self.assertEqual(ME.meta_report(acc)["cat_n"], 0)
        ME.meta_tally(acc, EXP, None)                     # 실패 산출 = 빈 산출로 채점
        self.assertEqual(ME.meta_report(acc)["ent_f1"], 0.0)

    def test_score_merges_meta_and_intent_f1(self):
        from prism import abtest
        rows = [{"content": {}, "expected": EXP}]
        m = abtest.score(rows, [{"quality_meta": {"finalGrade": "G", "reasons": []}, "trace": {}, **OUT}])
        self.assertIn("cat_hf1", m); self.assertEqual(m["intent_f1"], 1.0)

    def test_overall_and_gate(self):
        o = ME.overall({"grade_accuracy": 0.9, "intent_f1": 0.5, "intent_n": 4, "cat_n": 0, "ent_n": 0, "summary_n": 0}, 0.85, 0.6)
        self.assertEqual(o["gate_fails"], ["intent_f1"])
        self.assertAlmostEqual(o["overall"], (0.4 * 0.9 + 0.15 * 0.5) / 0.55, 3)

    def test_diagnose_recipes(self):
        acc = {}
        for _ in range(3):
            ME.meta_tally(acc, EXP, OUT)
        m = {**ME.meta_report(acc), "by_intent_value": {"구매": {"n": 4, "tp": 1, "fp": 0, "fn": 3, "f1": 0.4}}}
        d = ME.diagnose(m)
        fields = [x["field"] for x in d]
        self.assertIn("intent", fields); self.assertIn("category", fields); self.assertIn("entities", fields)
        self.assertEqual(sum(1 for x in d if x["field"] == "category"), 1)   # 혼동으로 잡힌 카테고리는 누락으로 중복 안 함
        for x in d:
            self.assertIn(x["stage"], ("extract", "analyze", "review")); self.assertTrue(x["directive"] and x["id"])


class TestCookbookRoute(unittest.TestCase):
    def test_append_stage_directive(self):
        from prism import serve
        from prism.config import Config
        cfg = Config.load()
        cfg.stage_prompts = {}
        self.assertTrue(serve._append_stage_directive(cfg, "common", "analyze", "지시 A"))
        self.assertFalse(serve._append_stage_directive(cfg, "common", "analyze", "지시 A"))   # 중복은 건너뜀
        self.assertTrue(serve._append_stage_directive(cfg, "solar-pro2", "extract", "지시 B"))
        self.assertEqual(cfg.stage_prompts["analyze"], "지시 A")
        self.assertEqual(cfg.model_prompts["solar-pro2"]["extract"], "지시 B")
        self.assertIn("/cookbook-apply", serve._POST_ROUTES)


if __name__ == "__main__":
    unittest.main()
