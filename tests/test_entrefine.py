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
        meta = "한국은행이 기준금리 인하를 결정했다 이창용 한국은행 이창용 기자 홍길동 삼성 속보·단신 Business and Finance / Economy"
        obj = {"entities": [
                   {"name": "한국은행", "canonical": "한은", "type": "OG", "relevance": 150, "keep": True},   # 근거 없는 개명 → 원래 이름
                   {"name": "이창용", "canonical": "이창용", "type": "PS", "relevance": 80, "keep": True},
                   {"name": "기자 홍길동", "type": "XX", "relevance": "x", "keep": False},
                   {"name": "삼성", "canonical": "삼성그룹", "type": "OG", "relevance": 10, "keep": True},   # 사전 정식명은 허용
                   {"name": "없는이름", "relevance": 99, "keep": True}],                                     # 1차 목록 밖 → 버림
               "keywords": [{"text": "이창용", "kind": "single"},
                            {"text": "한국은행 기준금리 인하", "kind": "combo"},                              # 엔티티 포함 · 어절 모두 메타에 있음
                            {"text": "한국은행 금리 동결", "kind": "combo"},                                  # '동결'이 메타에 없음 → 버림
                            {"text": "기준금리 인하", "kind": "combo"},                                       # 엔티티 없음 → 버림
                            {"text": "한국은행 기준금리 인하했다", "kind": "combo"},                          # '인하했다' 어절이 메타에 없음 → 버림
                            {"text": "한국은행", "kind": "single"}]}
        v = ER.validate(obj, names, canon, meta)
        self.assertEqual([e["name"] for e in v["entities"]], ["한국은행", "이창용", "기자 홍길동", "삼성"])
        self.assertEqual(v["entities"][0]["canonical"], "한국은행")
        self.assertEqual(v["entities"][0]["relevance"], 100)
        self.assertEqual((v["entities"][2]["type"], v["entities"][2]["relevance"]), ("", 0))
        self.assertEqual(v["keywords"], [{"text": "한국은행 기준금리 인하", "kind": "combo"},            # 조합형 우선
                                         {"text": "이창용", "kind": "single"}, {"text": "한국은행", "kind": "single"}])
        self.assertEqual(v["dropped"], ["기자 홍길동"])
        self.assertEqual(v["renamed"], [["삼성", "삼성그룹"]])
        self.assertEqual(ER.validate({"keywords": ["본문에만 있는 말"]}, names, canon, meta)["keywords"], [])
        with self.assertRaises(ValueError):
            ER.validate([], names, canon)

    def test_metas_only_input(self):
        im = {"summary": "리드문", "entities": ["가"], "intent": ["속보·단신"], "content_category": [{"tier1": "Sports", "tier2": "Golf"}]}
        payload = json.loads(ER._payload(im, {}))
        self.assertEqual(set(payload), {"리드문", "엔티티", "인텐트", "카테고리"})                   # 제목·본문 없음
        self.assertNotIn("본문", ER._meta_text(im))

    def test_baseline_and_summary(self):
        im = {"entities": ["가", "나", "다", "라"], "summary": ""}
        b = ER.baseline(im, {"title": "라 소식", "body": "가 나 다 라 라"})
        self.assertEqual(b[0]["name"], "라")                                      # 제목에 있는 엔티티가 확신도 1위
        items = [{"base": [{"name": "가"}, {"name": "나"}, {"name": "다"}],
                  "refined": {"keywords": [{"text": "가", "kind": "single"}, {"text": "가 나 사건", "kind": "combo"}],
                              "entities": [{"name": "가"}, {"name": "나"}], "dropped": ["나"], "renamed": []}},
                 {"error": "x"}]
        s = ER.summary(items)
        self.assertEqual((s["n"], s["fails"], s["overlap_avg"], s["dropped_share"], s["short"], s["combo_share"]),
                         (1, 1, 1.0, 0.5, 1, 0.5))
        self.assertEqual(s["combo_share"], 0.5)

    def test_mock_refine_and_vote(self):
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        ER._SV = types.SimpleNamespace(get_store=lambda: st)
        self.addCleanup(lambda: setattr(ER, "_SV", None))
        out = ER.refine({"title": "가 나", "body": "가 나 다"}, {"entities": ["가", "나", "다"]}, None, mock=True)
        self.assertEqual(out["refined"]["keywords"][0]["kind"], "combo")              # mock 도 조합형 우선
        self.assertEqual(len(out["base"]), 3)
        self.assertFalse(ER.vote({"hash": "h", "pick": "bogus"}, "qa")["ok"])
        self.assertTrue(ER.vote({"hash": "h", "pick": "refined", "base": ["가"], "refined": ["다"]}, "qa")["ok"])
        self.assertEqual(ER.votes()["tally"]["refined"], 1)


    def test_config_and_sentence(self):
        store = {}
        ER._SV = types.SimpleNamespace(_report_get=lambda k, t=None, d=None: store.get(k, d),
                                       _report_save=lambda k, v, t=None: store.__setitem__(k, v))
        self.addCleanup(lambda: setattr(ER, "_SV", None))
        c = ER.get_config()
        self.assertFalse(c["keyword"]["custom"]); self.assertEqual(c["sentence"]["rules"], ER.SENT_RULES)
        r = ER.save_config({"keyword": {"model": "m1", "rules": "새 규칙"}, "sentence": {"model": "", "rules": ER.SENT_RULES}})
        self.assertTrue(r["keyword"]["custom"]); self.assertEqual(r["keyword"]["model"], "m1")
        self.assertFalse(r["sentence"]["custom"])                                  # 기본과 같으면 기본으로
        self.assertTrue(ER._system("keyword", r).endswith(ER.KW_SCHEMA))            # 출력 형식은 항상 뒤에 고정
        self.assertTrue(ER._system("keyword", r).startswith("새 규칙"))
        self.assertFalse(ER.save_config({"keyword": {"rules": "x" * 9000}})["ok"])
        self.assertEqual(ER.validate_sentence({"sentence": '  "한국은행이 기준금리를 0.25%p 내렸다"  '}), "한국은행이 기준금리를 0.25%p 내렸다")
        for bad in ({"sentence": "짧음"}, {"sentence": "가" * 200}, []):
            with self.assertRaises(ValueError):
                ER.validate_sentence(bad)

    def test_process_keeps_other_call_on_failure(self):
        class Boom:
            def complete_json(self, *a, **k):
                raise RuntimeError("down")
        ER._SV = types.SimpleNamespace(get_store=lambda: None)
        self.addCleanup(lambda: setattr(ER, "_SV", None))
        eng = {"keyword": (Boom(), False, "s", "m"), "sentence": (None, True, "s", "m")}
        out = ER.process({"title": "가나다라마바사 제목", "body": "가 나 다"}, {"entities": ["가", "나", "다"], "summary": "요약"}, eng)
        self.assertIn("down", out["error"]); self.assertEqual(len(out["base"]), 3)  # 키워드 실패 · 지금 방식은 남김
        self.assertTrue(out["sentence"]["text"]); self.assertEqual(out["sentence"]["base"], "요약")
        self.assertFalse(out["sentence"]["from_keywords"])                            # 키워드 실패 → 메타만으로


    def test_picker_value_to_model_id(self):
        """픽커 값 'timely|claude-opus-5' 를 그대로 넘기면 라우터가 bad_request(2026-10-07 운영) → 모델 id 만."""
        seen = []
        ER._SV = types.SimpleNamespace(Handler=types.SimpleNamespace(server_mock=False),
                                       llm_for_model=lambda m, mock: (seen.append(m) or object(), "timely"))
        self.addCleanup(lambda: setattr(ER, "_SV", None))
        ER._llm("timely|claude-opus-5"); ER._llm("solar-pro3"); ER._llm("")
        self.assertEqual(seen, ["claude-opus-5", "solar-pro3", ""])
        self.assertEqual(ER._model_id("bizrouter|openai/gpt-5.4"), "openai/gpt-5.4")


if __name__ == "__main__":
    unittest.main()
