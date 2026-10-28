"""공통 채점·표본·미측정·대응 비교의 통합 계약."""
import copy
import unittest
from unittest.mock import patch

from prism import abtest, evaluation as E, metaeval as M, learnops as L


def item(expected=None, got=None):
    return {"hash": "doc", "source": {"title": "제목", "body": "원문"},
            "expected": expected or {"summary": "정답"}, "got": {"result": got or {"summary": "산출"}}}


class EvaluationContractTests(unittest.TestCase):
    def test_pending_intent_excluded_in_old_and_new_paths(self):
        exp = {"intent": ["리뷰"], "intent_review": "needed"}
        acc = {}
        abtest.intent_tally(acc, exp, {"item_meta": {"intent": ["리뷰"]}})
        self.assertEqual(abtest.intent_report(acc)["intent_n"], 0)
        report = E.report([item(exp, {"intent": ["리뷰"]})], "result")
        self.assertNotIn("intent_f1", report["metrics"])
        self.assertEqual(report["eligibility"]["intent"], {"pending": 1})
        self.assertIn("intent", E.readiness(report))

    def test_no_value_is_scored_but_generation_failure_is_not_success(self):
        exp = {"meta_status": {f: "no_value" for f in E.FIELDS}}
        r = item(exp); r["got"]["result"] = None
        report = E.report([r], "result")
        for metric in ("entity_f1", "intent_f1", "category_hier_f1", "summary_no_value_exact"):
            self.assertEqual(report["metrics"][metric]["value"], 0)
        self.assertFalse(M.overall({"evaluation": report}, .8, .8)["passed"])

    def test_semantic_failure_stays_missing_and_blocks_gate(self):
        r = E.report([item()], "result", embed=lambda _: (_ for _ in ()).throw(RuntimeError()))
        self.assertEqual(r["semantic_status"], "RuntimeError")
        self.assertIsNone(r["metrics"]["summary_semantic_cosine"]["value"])
        self.assertIn("summary_semantic_cosine", M.overall({"evaluation": r}, .8, .8)["gate_missing"])
        self.assertIsNotNone(r["metrics"]["summary_lexical_f1"]["value"])

    def test_paired_requires_identical_frozen_basis_and_embedding(self):
        a = E.report([item()], "result")
        for change in ("source", "expected", "hash"):
            altered = item()
            altered[change] = "other" if change == "hash" else {"summary": "다른 값"}
            b = E.report([altered], "result")
            self.assertFalse(E.paired(a, b)["comparable"])
        self.assertTrue(E.paired(a, a)["comparable"])
        b = copy.deepcopy(a); b["embedding_model"] = "different"
        self.assertFalse(E.paired(a, b)["comparable"])
        self.assertFalse(E.paired(None, a)["comparable"])
        self.assertTrue(L._batch_regressions({"evaluation": a}, {"evaluation": b}))

    def test_invalid_gold_not_scored_as_empty_perfect_match(self):
        r = E.report([item({"content_category": ["not-a-category"]}, {"content_category": []})], "result")
        self.assertNotIn("category_f1", r["metrics"])
        self.assertEqual(r["eligibility"]["content_category"], {"excluded": 1})

    def test_duplicate_sample_rejected(self):
        with self.assertRaises(ValueError):
            E.report([item(), item()], "result")

    def test_readonly_legacy_metrics_never_embed(self):
        with patch.object(M, "summary_sims", side_effect=AssertionError("paid call")):
            r = M.meta_report({"sum_pairs": [("가나다", "가나다")]}, read_only=True)
        self.assertEqual(r["summary_sim_method"], "bigram_f1")

    def test_missing_semantic_is_not_replaced_by_no_value_subset(self):
        a = item(); b = item({"meta_status": {"summary": "no_value"}}, {"summary": ""}); b["hash"] = "second"
        r = E.report([a, b], "result")
        self.assertIn("summary_semantic_cosine", E.readiness(r))
        self.assertFalse(M.overall({"evaluation": r}, .8, .8)["passed"])

    def test_negative_cosine_preserved(self):
        r = E.report([item()], "result", embed=lambda _: [[1, 0], [-1, 0]])
        self.assertEqual(r["metrics"]["summary_semantic_cosine"]["value"], -1)


if __name__ == "__main__":
    unittest.main()
