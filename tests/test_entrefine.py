import json
import os
import tempfile
import types
import unittest

from prism import entrefine as ER


class EntRefineTest(unittest.TestCase):
    def test_validate_guards(self):
        names = ["한국은행", "이창용", "기자 홍길동", "삼성"]
        canon = {"삼성": "삼성그룹"}
        text = "한국은행 기준금리 인하 결정 이창용 총재"
        obj = {"entities": [
                   {"name": "한국은행", "canonical": "한은", "type": "OG", "relevance": 150, "keep": True},   # 근거 없는 개명 → 원래 이름
                   {"name": "이창용", "canonical": "이창용", "type": "PS", "relevance": 80, "keep": True},
                   {"name": "기자 홍길동", "type": "XX", "relevance": "x", "keep": False},
                   {"name": "삼성", "canonical": "삼성그룹", "type": "OG", "relevance": 10, "keep": True},   # 사전 정식명은 허용
                   {"name": "없는이름", "relevance": 99, "keep": True}],                                     # 1차 목록 밖 → 버림
               "keywords": [{"text": "한국은행", "kind": "entity"}, {"text": "기준금리 인하", "kind": "concept"},
                            {"text": "금리 동결", "kind": "concept"},                                          # 원문에 없음 → 버림
                            "한국은행", "이창용", "삼성그룹"]}
        v = ER.validate(obj, names, canon, text)
        self.assertEqual([e["name"] for e in v["entities"]], ["한국은행", "이창용", "기자 홍길동", "삼성"])
        self.assertEqual(v["entities"][0]["canonical"], "한국은행")
        self.assertEqual(v["entities"][0]["relevance"], 100)
        self.assertEqual((v["entities"][2]["type"], v["entities"][2]["relevance"]), ("", 0))
        self.assertEqual(v["keywords"], [{"text": "한국은행", "kind": "entity"}, {"text": "기준금리 인하", "kind": "concept"},
                                         {"text": "이창용", "kind": "entity"}])                               # 중복 제거 · 3개 상한
        self.assertEqual(v["dropped"], ["기자 홍길동"])
        self.assertEqual(v["renamed"], [["삼성", "삼성그룹"]])
        with self.assertRaises(ValueError):
            ER.validate([], names, canon)

    def test_baseline_and_summary(self):
        im = {"entities": ["가", "나", "다", "라"], "summary": ""}
        b = ER.baseline(im, {"title": "라 소식", "body": "가 나 다 라 라"})
        self.assertEqual(b[0]["name"], "라")                                      # 제목에 있는 엔티티가 확신도 1위
        items = [{"base": [{"name": "가"}, {"name": "나"}, {"name": "다"}],
                  "refined": {"keywords": [{"text": "가", "kind": "entity"}, {"text": "새 주제", "kind": "concept"}],
                              "entities": [{"name": "가"}, {"name": "나"}], "dropped": ["나"], "renamed": []}},
                 {"error": "x"}]
        s = ER.summary(items)
        self.assertEqual((s["n"], s["fails"], s["overlap_avg"], s["dropped_share"], s["short"], s["concept_share"]),
                         (1, 1, 1.0, 0.5, 1, 0.5))

    def test_mock_refine_and_vote(self):
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        ER._SV = types.SimpleNamespace(get_store=lambda: st)
        self.addCleanup(lambda: setattr(ER, "_SV", None))
        out = ER.refine({"title": "가 나", "body": "가 나 다"}, {"entities": ["가", "나", "다"]}, None, mock=True)
        self.assertEqual(len(out["refined"]["keywords"]), 2)
        self.assertEqual(len(out["base"]), 3)
        self.assertFalse(ER.vote({"hash": "h", "pick": "bogus"}, "qa")["ok"])
        self.assertTrue(ER.vote({"hash": "h", "pick": "refined", "base": ["가"], "refined": ["다"]}, "qa")["ok"])
        self.assertEqual(ER.votes()["tally"]["refined"], 1)


if __name__ == "__main__":
    unittest.main()
