import unittest
from prism import accuracy_eval as A, solar_accuracy as S, meta_prompts as M
from scripts.evaluate_solar_context import prompt_sets


class AccuracyEvaluationTests(unittest.TestCase):
    def test_unsupported_reference_is_not_a_required_fact(self):
        ref = {"claims": [{"claim": "지원 시작", "verdict": "supported"},
                          {"claim": "모두 무료", "verdict": "unverifiable"}]}
        self.assertEqual(A.reference_claims(ref), {"1": "지원 시작"})

    def test_missing_information_is_not_full_accuracy(self):
        result = {"items": [{"id": "1", "verdict": "covered", "evidence_ids": ["1"]},
                            {"id": "2", "verdict": "missing", "evidence_ids": []}]}
        review = A.validate_coverage({"1": "신청 시작", "2": "월요일 마감"}, "신청을 받는다.", result)
        self.assertEqual(review["coverage"], .5)
        self.assertEqual(A.diagnostics([True]*4, "신청을 받는다.", {"support_rate": 1}, review)["review_all_clear"], 0)

    def test_omitted_duplicate_or_fabricated_claims_are_rejected(self):
        item = {"id": "1", "verdict": "covered", "evidence_ids": ["1"]}
        for items in ([], [item, item], [{**item, "id": "999"}], [{**item, "evidence_ids": ["999"]}],
                      [{**item, "evidence_ids": []}], [{**item, "verdict": "missing"}]):
            with self.subTest(items=items), self.assertRaises(ValueError):
                A.validate_coverage({"1": "신청 시작"}, "신청 시작.", {"items": items})

    def test_failures_and_missing_reviews_cannot_pass(self):
        self.assertIsNone(A.diagnostics([True]*4, "문장", None, None)["review_all_clear"])
        self.assertEqual(A.diagnostics([True, False], "문장", None, None)["review_all_clear"], 0)
        self.assertEqual(A.diagnostics([True]*4, "", None, None)["review_all_clear"], 0)
        self.assertEqual(A.diagnostics([True]*4, "문장", {"support_rate": 1}, {"coverage": 1})["review_all_clear"], 1)

    def test_prompt_retains_contract_and_examples_without_changing_production(self):
        prompts = prompt_sets(S)
        for call in M.CALLS:
            self.assertEqual(prompts["candidate"][call], S.call_system(call))
            self.assertIn(M.gold_examples(call), S.call_system(call))
            self.assertIn(M.CALL_RULES[call], S.call_system(call))
            self.assertNotIn(S.GUARD, M.call_system(S.MODEL, call))
        self.assertIn("tier1, tier2", S.call_system("category"))


if __name__ == "__main__":
    unittest.main()
