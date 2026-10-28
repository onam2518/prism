import json
import math
import unittest

from prism import context_eval as C, meta_prompts as M, solar_context as S
from scripts.evaluate_solar_context import prompt_sets, validate_output


class SemanticEvaluationTests(unittest.TestCase):
    def test_semantic_failure_does_not_become_lexical_score(self):
        def fail(texts):
            raise ConnectionError("offline")
        scores, status = C.semantic_pairs([("정부가 제출했다", "정부가 제출했다"), ("원문", "")], fail)
        self.assertEqual(scores, [None, 0])
        self.assertEqual(status, "ConnectionError")

    def test_empty_output_cannot_receive_high_embedding_score(self):
        seen = []
        def embed(texts):
            seen.extend(texts)
            return [[1, 0] for _ in texts]
        scores, status = C.semantic_pairs([("정답", ""), ("정답", "바꾼 표현"), ("", "오답")], embed)
        self.assertEqual(scores, [0, 1, None])
        self.assertEqual(status, "ok")
        self.assertEqual(seen, ["정답", "바꾼 표현"])

    def test_invalid_vectors_are_missing_not_similarity(self):
        for vector in ([0, 0], [math.nan, 1], [math.inf, 1], [True, 1], []):
            with self.subTest(vector=vector):
                scores, status = C.semantic_pairs([("a", "b")], lambda _: [vector, [1, 0]])
                self.assertEqual(scores, [None]); self.assertEqual(status, "ValueError")
        with self.assertRaises(ValueError):
            C.cosine([1, 2], [1])

    def test_cosine_retains_negative_range(self):
        self.assertEqual(C.cosine([1, 0], [-1, 0]), -1)

    def test_many_extra_labels_do_not_inflate_f1(self):
        scores = C.score_fields({"intent": ["a"]}, {"intent": ["a", "b", "c", "d"]})
        self.assertEqual(scores["intent_hit"], 1)
        self.assertEqual(scores["intent_precision"], .25)
        self.assertEqual(scores["intent_f1"], .4)

    def test_missing_labels_and_confirmed_empty_are_distinct(self):
        self.assertNotIn("entity_f1", C.score_fields({"entities": []}, {"entities": []}))
        expected = {"entities": [], "meta_status": {"entities": "no_value"}}
        self.assertEqual(C.score_fields(expected, {"entities": []})["entity_f1"], 1)
        self.assertEqual(C.score_fields(expected, {"entities": []}, failed_fields=["entities"])["entity_f1"], 0)
        self.assertEqual(C.score_fields(expected, {"entities": ["잘못 추가"]})["entity_f1"], 0)
        self.assertEqual(C.score_fields({"entities": ["이전값"], "meta_status": {"entities": "pending"}}, {}), {})

    def test_bootstrap_keeps_pairing_and_missing_counts(self):
        result = C.paired_bootstrap([.1, .9, None], [.2, 1, .8])
        self.assertEqual(result["n"], 2); self.assertEqual(result["missing_pairs"], 1)
        self.assertAlmostEqual(result["delta"], .1)
        for bound in result["ci95"]:
            self.assertAlmostEqual(bound, .1)
        self.assertEqual(result, C.paired_bootstrap([.1, .9, None], [.2, 1, .8]))
        self.assertEqual(C.paired_bootstrap([None], [1])["conclusion"], "unmeasured")
        self.assertIsNone(C.paired_bootstrap([0], [1])["ci95"])
        with self.assertRaises(ValueError):
            C.paired_bootstrap([1], [])

    def test_fact_review_requires_real_evidence(self):
        source = "도서관은 월요일에 휴관한다."
        claims = {"claims": [{"claim": "월요일 휴관", "verdict": "supported", "evidence": source}]}
        self.assertEqual(C.validate_fact_review(source, "월요일 휴관이다.", claims)["support_rate"], 1)
        claims["claims"][0]["evidence"] = "일요일에 휴관한다."
        with self.assertRaises(ValueError):
            C.validate_fact_review(source, "월요일 휴관이다.", claims)
        with self.assertRaises(ValueError):
            C.validate_fact_review(source, "월요일 휴관이다.", {"claims": []})
        self.assertIsNone(C.validate_fact_review(source, "", {"claims": []})["support_rate"])

    def test_fact_evidence_is_resolved_from_original_units(self):
        source = "도서관은 월요일에 휴관한다.\n화요일에는 문을 연다."
        result = {"claims": [{"claim": "월요일 휴관", "verdict": "supported", "evidence_ids": ["1"]}]}
        checked = C.validate_fact_review(source, "월요일 휴관한다.", result)
        self.assertEqual(checked["claims"][0]["evidence"], "도서관은 월요일에 휴관한다.")
        self.assertNotIn("evidence", result["claims"][0])
        result["claims"][0]["evidence_ids"] = ["999"]
        with self.assertRaises(ValueError):
            C.validate_fact_review(source, "월요일 휴관한다.", result)


class SolarPromptTests(unittest.TestCase):
    def test_target_snapshot_only_and_complete_json_examples(self):
        for call in M.CALLS:
            new = S.call_system(call)
            self.assertIn(S.GUARD, new)
            self.assertNotIn(S.GUARD, M.call_system(S.MODEL, call))
            self.assertNotIn(S.GUARD, M.call_system("solar-pro3-260323", call))
            self.assertNotIn(S.GUARD, M.call_system("solar-pro4-unknown", call))
            with self.assertRaises(ValueError):
                S.call_system(call, "solar-pro3-260323")
            for line in S.examples(call).splitlines():
                if line.startswith("출력: "):
                    validate_output(call, json.loads(line[len("출력: "):]))

    def test_all_example_facts_have_explicit_inputs(self):
        self.assertEqual(len(S.GROUNDED_INPUTS), len(M.GOLD))
        self.assertIn("759", S.GROUNDED_INPUTS[2]["body"])
        self.assertIn("프라이부르크", S.GROUNDED_INPUTS[0]["body"])
        for source, expected in zip(S.GROUNDED_INPUTS, M.GOLD):
            for entity in expected["entities"]:
                self.assertIn(entity["name"], source["title"] + source["body"])

    def test_export_matches_runtime_builder_and_retains_dictionary(self):
        exported = prompt_sets()
        for call in M.CALLS:
            self.assertEqual(exported["candidate"][call], S.call_system(call))
            self.assertEqual(exported["baseline"][call], M.call_system(S.MODEL, call))
            self.assertNotEqual(exported["baseline"][call], exported["candidate"][call])
            if M.call_dictionary(call):
                self.assertIn(M.call_dictionary(call), exported["candidate"][call])


if __name__ == "__main__":
    unittest.main()
