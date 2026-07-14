"""S5 메타추출 모델 A/B 하네스(media_s5ab) 계약 테스트.

같은 통합 원고를 여러 후보 모델에 태워 ItemMeta 를 나란히 반환하는지 검증.
mock 모드라 모델별 route=mock(동일 산출)이지만, 모델 축·item_meta 구조·상한·미저장을 확인."""
import os
import tempfile
import unittest

os.environ.setdefault("PRISM_DB", os.path.join(tempfile.mkdtemp(), "t.db"))

import prism.serve as S

BODY = "오늘 서울 도심에서 대규모 행사가 열렸다. 시민들이 모여 현장을 지켜봤다."


class TestMediaS5AB(unittest.TestCase):
    def setUp(self):
        S.Handler.server_mock = True

    def test_per_model_item_meta(self):
        r = S.media_s5ab(BODY, ["solar-pro2", "gpt-5.4", "gemini-2.5-pro"])
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["results"]), 3)
        models = [x["model"] for x in r["results"]]
        self.assertEqual(models, ["solar-pro2", "gpt-5.4", "gemini-2.5-pro"])
        for x in r["results"]:
            self.assertTrue(x.get("mock"))
            self.assertIn("item_meta", x)
            self.assertTrue(x["item_meta"].get("summary"))          # mock 채워짐
            self.assertIn("content_category", x["item_meta"])

    def test_dedup_and_cap(self):
        # 중복 제거 + 상한 6
        r = S.media_s5ab(BODY, ["m1", "m1", "m2", "m3", "m4", "m5", "m6", "m7", "m8"])
        names = [x["model"] for x in r["results"]]
        self.assertEqual(len(names), 6)
        self.assertEqual(names[0], "m1")
        self.assertEqual(len(set(names)), 6)                        # 중복 없음

    def test_action_validation(self):
        self.assertFalse(S.media_action({"action": "s5ab", "text": "", "models": ["x"]})["ok"])
        self.assertFalse(S.media_action({"action": "s5ab", "text": BODY, "models": []})["ok"])
        ok = S.media_action({"action": "s5ab", "text": BODY, "models": ["solar-pro2"]})
        self.assertTrue(ok["ok"])
        self.assertEqual(len(ok["results"]), 1)

    def test_not_persisted(self):
        st = S.get_store()
        before = len(S.results_rows()) if st else 0
        S.media_s5ab(BODY, ["solar-pro2", "gpt-5.4"])
        after = len(S.results_rows()) if st else 0
        self.assertEqual(before, after)                             # 실험 · 미저장


if __name__ == "__main__":
    unittest.main()
