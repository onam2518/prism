import json
import os
import random
import shutil
import subprocess
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

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
                            {"text": "기준금리 인하", "kind": "combo"},                                       # 메타에 근거가 있는 주제어
                            {"text": "한국은행 기준금리 인하했다", "kind": "combo"},                          # '인하했다' 어절이 메타에 없음 → 버림
                            {"text": "한국은행", "kind": "single"}]}
        v = ER.validate(obj, names, canon, meta)
        self.assertEqual([e["name"] for e in v["entities"]], ["한국은행", "이창용", "기자 홍길동", "삼성"])
        self.assertEqual(v["entities"][0]["canonical"], "한국은행")
        self.assertEqual(v["entities"][0]["relevance"], 100)
        self.assertEqual((v["entities"][2]["type"], v["entities"][2]["relevance"]), ("", 0))
        self.assertEqual(v["keywords"], [{"text": "이창용", "kind": "single"},
                                         {"text": "한국은행 기준금리 인하", "kind": "combo"},
                                         {"text": "기준금리 인하", "kind": "single"}])
        self.assertEqual(v["dropped"], ["기자 홍길동"])
        self.assertEqual(v["renamed"], [["삼성", "삼성그룹"]])
        self.assertEqual(ER.validate({"keywords": ["본문에만 있는 말"]}, names, canon, meta)["keywords"], [])
        with self.assertRaises(ValueError):
            ER.validate([], names, canon)

    def test_rejected_entities_cannot_reenter_as_topic_or_combo(self):
        obj = {"entities": [{"name": "삼성", "keep": False}, {"name": "이창용", "keep": "false"},
                            {"name": "한국은행", "keep": True}],
               "keywords": [{"text": t, "kind": k} for t, k in [
                   ("삼성", "single"), ("삼성 금리", "combo"), ("이창용", "single"),
                   ("한국은행 금리", "combo")]]}
        out = ER.validate(obj, ["삼성", "이창용", "한국은행"], {}, "삼성 이창용 한국은행 금리")
        self.assertEqual(out["keywords"], [{"text": "한국은행 금리", "kind": "combo"}])
        self.assertEqual(len(out["rejected"]), 3)

    def test_token_boundaries_normalization_and_malformed_responses(self):
        import unicodedata
        for text in ("삼성전자", "predict", "서울대학교"):
            term = {"삼성전자": "삼성", "predict": "dict", "서울대학교": "서울"}[text]
            out = ER.validate({"keywords": [{"text": term, "kind": "single"}]}, [], {}, text)
            self.assertEqual(out["keywords"], [])
        phrase = "기준금리 인하"
        out = ER.validate({"keywords": [{"text": unicodedata.normalize("NFD", phrase), "kind": "single"},
                                         {"text": phrase, "kind": "single"}]}, [], {}, "기준금리를 인하했다")
        self.assertEqual(out["keywords"], [{"text": phrase, "kind": "single"}])
        for obj in ({}, {"keywords": {}}, {"entities": "bad", "keywords": []}):
            with self.assertRaises(ValueError):
                ER.validate(obj, [], {})
        out = ER.validate({"keywords": [None, {"text": None, "kind": "single"}]}, [], {})
        self.assertEqual(out["keywords"], [])

    def test_semantic_review_rejects_relationships_duplicates_and_false_quotes(self):
        candidates = [{"text": t, "kind": "combo"} for t in ("가온은행 금리 인하", "가온은행 금리 하락", "누리은행 금리 인하")]
        rows = [{"index": 0, "supported": True, "distinct": True, "evidence": ["가온은행은 금리를 인하했다"], "reason": "중심 사실"},
                {"index": 1, "supported": True, "distinct": False, "evidence": ["가온은행은 금리를 인하했다"], "reason": "같은 범위의 중복"},
                {"index": 2, "supported": True, "distinct": True, "evidence": ["누리은행은 금리를 인하했다"], "reason": "사실"}]
        refined = {"keywords": candidates, "rejected": []}
        with patch.object(ER, "_call", return_value=({"decisions": rows}, {})):
            ER.review_keywords(refined, {"summary": "가온은행은 금리를 인하했다. 누리은행은 동결했다"}, {}, None)
        self.assertEqual(refined["keywords"], candidates[:1])
        self.assertIn("인용", refined["rejected"][1]["reason"])

    def test_incomplete_review_fails_closed_and_sentence_fallback_unchanged(self):
        class Stub:
            def complete_json(self, system, user, tag):
                if tag == "core_keyword":
                    return {"entities": [{"name": "한국은행", "keep": True}],
                            "keywords": [{"text": "한국은행", "kind": "single"}]}, None
                if tag == "core_keyword_review":
                    return {"decisions": []}, None
                self.sentence_input = json.loads(user)
                return {"sentence": "한국은행이 기준금리를 발표했다."}, None
        llm = Stub()
        eng = {c: (llm, False, "rules", "model") for c in ER.CALLS}
        with patch.object(ER, "_canon", return_value={}):
            out = ER.process({}, {"entities": ["한국은행"], "summary": "한국은행이 기준금리를 발표했다."}, eng)
        self.assertEqual(out["refined"]["keywords"], [])
        self.assertEqual(out["refined"]["verification"], "failed")
        self.assertIn("의미 검사", out["error"])
        self.assertEqual(llm.sentence_input["핵심키워드"], [])
        self.assertTrue(out["sentence"]["text"])

    def test_keyword_history_is_durable_scoped_and_votes_are_idempotent(self):
        from prism.store import Store
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "t.db")
            st = Store(path)
            ER._SV = types.SimpleNamespace(get_store=lambda: st)
            self.addCleanup(lambda: setattr(ER, "_SV", None))
            eng = {"keyword": (None, True, "original rules", "model-v1")}
            out = {"hash": "h", "title": "title", "base": [{"name": "기존"}],
                   "refined": {"keywords": [{"text": "새 후보", "kind": "single"}], "verification": "checked"},
                   "sentence": {"text": "보관하면 안 되는 문장"}}
            ER._record_keywords(out, {"summary": "원래 메타"}, eng, "team-a")
            rid = out["result_id"]
            ER._SV = types.SimpleNamespace(get_store=lambda: Store(path))
            stored = ER.keyword_history("team-a")["items"][0]
            self.assertEqual(stored["input"]["리드문"], "원래 메타")
            self.assertEqual(stored["model"], "model-v1")
            self.assertNotIn("sentence", stored)
            self.assertEqual(ER.keyword_history("team-b")["items"], [])
            self.assertFalse(ER.vote({"result_id": rid, "pick": "refined"}, "qa", "team-b")["ok"])
            for _ in range(2):
                self.assertTrue(ER.vote({"result_id": rid, "pick": "refined", "base": ["위조"]}, "qa", "team-a")["ok"])
            self.assertEqual(ER.votes("team-a")["n"], 1)
            self.assertTrue(ER.vote({"result_id": rid, "pick": "base"}, "qa", "team-a")["ok"])
            self.assertEqual(ER.votes("team-a")["tally"], {"refined": 0, "base": 1, "both": 0, "neither": 0})
            self.assertEqual(ER.keyword_history("team-a")["items"][0]["base"], [{"name": "기존"}])
            self.assertEqual(st.get_report(ER.KEYWORD_HISTORY_KIND, "team-a")["items"][0]["prompt"], "original rules")

    def test_metas_only_input(self):
        im = {"summary": "리드문", "entities": ["가"], "intent": ["속보·단신"], "content_category": [{"tier1": "Sports", "tier2": "Golf"}]}
        payload = json.loads(ER._payload(im, {}))
        self.assertEqual(set(payload), {"리드문", "엔티티", "인텐트", "카테고리"})                   # 제목·본문 없음
        self.assertNotIn("본문", ER._meta_text(im))

    def test_topic_phrase_and_selection_order(self):
        meta = "한국은행은 기준금리 인하를 결정했다. 이창용 총재가 발표했다."
        obj = {"entities": [{"name": "한국은행", "keep": True}], "keywords": [
            {"text": "기준금리 인하", "kind": "single"},
            {"text": "기준금리 인하 이유", "kind": "single"},
            {"text": "한국은행", "kind": "single"},
            {"text": "한국은행", "kind": "single"},
            {"text": "한국은행 기준금리 인하", "kind": "combo"},
            {"text": "이창용", "kind": "single"}]}
        out = ER.validate(obj, ["한국은행"], {}, meta)
        self.assertEqual(out["keywords"], [obj["keywords"][i] for i in (0, 2, 4)])
        self.assertEqual(ER.validate({"keywords": [{"text": "금리 인하 수혜주", "kind": "single"}]},
                                     [], {}, meta)["keywords"], [])
        topic = {"text": "기준금리 인하", "kind": "single"}
        self.assertEqual(ER.validate({"keywords": [topic]}, [], {},
                                     "한국은행이 기준금리를 0.25%포인트 인하했다.")["keywords"], [topic])
        self.assertEqual(ER.validate({"keywords": [{"text": "한국은행 기준금리 인하 결정", "kind": "single"}]},
                                     [], {}, meta)["keywords"], [])

    def test_canonical_name_in_combo(self):
        obj = {"entities": [{"name": "한은", "canonical": "한국은행", "keep": True}],
               "keywords": [{"text": "한국은행 기준금리 인하", "kind": "combo"}]}
        self.assertEqual(ER.validate(obj, ["한은"], {"한은": "한국은행"}, "한은 기준금리 인하")["keywords"],
                         obj["keywords"])
        self.assertEqual(ER.validate(obj, ["한은"], {}, "한은 기준금리 인하")["keywords"], [])

    def test_review_passes_only_validated_keywords_and_meta_to_sentence(self):
        calls = []
        class Capture:
            def complete_json(self, system, user, tag):
                calls.append((tag, system, json.loads(user)))
                if tag == "core_keyword":
                    return {"entities": [{"name": "한국은행", "keep": True}], "keywords": [
                        {"text": "기준금리 인하", "kind": "single"},
                        {"text": "수혜주 추천", "kind": "single"},
                        {"text": "한국은행", "kind": "single"}]}, None
                if tag == "core_keyword_review":
                    return {"decisions": [{"index": i, "supported": True, "distinct": True,
                            "evidence": ["한국은행이 기준금리 인하를 결정했다."], "reason": "중심 사실"}
                            for i in range(2)]}, None
                return {"sentence": "한국은행이 기준금리 인하를 결정했다."}, None
        llm = Capture()
        eng = {c: (llm, False, ER._system(c, {}), "test") for c in ER.CALLS}
        im = {"summary": "한국은행이 기준금리 인하를 결정했다.", "entities": ["한국은행"],
              "intent": ["속보·단신"], "content_category": []}
        with patch.object(ER, "_canon", return_value={}):
            out = ER.process({"title": "원문 전용 제목", "body": "원문 전용 본문"}, im, eng)
        self.assertEqual(out["content"]["body"], "원문 전용 본문")
        self.assertEqual(out["content"]["summary"], im["summary"])
        self.assertEqual([c[0] for c in calls], ["core_keyword", "core_keyword_review", "core_sentence"])
        self.assertEqual(calls[2][2]["핵심키워드"], [
            {"키워드": "기준금리 인하", "유형": "단일형"}, {"키워드": "한국은행", "유형": "단일형"}])
        for _, _, payload in calls:
            self.assertNotIn("원문 전용", json.dumps(payload, ensure_ascii=False))
            self.assertEqual(payload.get("meta", payload)["리드문"], im["summary"])
        self.assertTrue(out["sentence"]["from_keywords"])

    def test_empty_keywords_still_use_only_two_calls(self):
        calls = []
        class Capture:
            def complete_json(self, system, user, tag):
                calls.append((tag, json.loads(user)))
                return ({"entities": [], "keywords": []} if tag == "core_keyword" else
                        {"sentence": "한국은행이 기준금리를 발표했다."}), None
        eng = {c: (Capture(), False, ER._system(c, {}), "test") for c in ER.CALLS}
        with patch.object(ER, "_canon", return_value={}):
            out = ER.process({}, {"entities": ["한국은행"], "summary": "한국은행이 기준금리를 발표했다."}, eng)
        self.assertEqual([c[0] for c in calls], ["core_keyword", "core_sentence"])
        self.assertEqual(calls[1][1]["핵심키워드"], [])
        self.assertFalse(out["sentence"]["from_keywords"])

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
        eng = {"keyword": (None, True, "rules", "mock")}
        ER._record_keywords(out, {"entities": ["가", "나", "다"]}, eng)
        self.assertTrue(ER.vote({"result_id": out["result_id"], "pick": "refined"}, "qa")["ok"])
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

    def test_model_rules_migration_isolation_and_atomic_validation(self):
        store = {ER.CONFIG_KIND: {"keyword": {"model": "solar-pro3", "rules": "기존 규칙"}}}
        ER._SV = types.SimpleNamespace(_report_get=lambda k, t=None, d=None: store.get(k, d),
                                       _report_save=lambda k, v, t=None: store.__setitem__(k, v))
        self.addCleanup(lambda: setattr(ER, "_SV", None))
        with patch.object(ER.Config, "load", return_value=types.SimpleNamespace(model="solar-pro4")):
            self.assertEqual(ER.get_config()["keyword"]["rules"], "기존 규칙")
            r = ER.save_config({"keyword": {"model": "solar-pro4", "rules": "새 모델 규칙"}})
            self.assertEqual(r["keyword"]["rules_by_model"], {"solar-pro3": "기존 규칙", "solar-pro4": "새 모델 규칙"})
            self.assertIn(ER.SOLAR_EXAMPLES["keyword"], r["keyword"]["system"])
            r = ER.save_config({"keyword": {"model": "", "rules": ER.KW_RULES}})
            self.assertFalse(r["keyword"]["custom"])
            self.assertEqual(r["keyword"]["rules_by_model"]["solar-pro3"], "기존 규칙")
            self.assertEqual(r["keyword"]["effective_model"], "solar-pro4")
            before = json.dumps(store, sort_keys=True)
            self.assertFalse(ER.save_config({"keyword": {"model": "solar-pro3", "rules": "수정"},
                                            "sentence": {"rules": "x" * 8001}})["ok"])
            self.assertEqual(json.dumps(store, sort_keys=True), before)
            self.assertFalse(ER.save_config({"keyword": {"rules_by_model": ["bad"]}})["ok"])
            self.assertEqual(json.dumps(store, sort_keys=True), before)
        # 기본 모델이 바뀌어도 이전 모델의 규칙이 따라가지 않는다.
        with patch.object(ER.Config, "load", return_value=types.SimpleNamespace(model="solar-pro2")):
            self.assertFalse(ER.get_config()["keyword"]["custom"])
            self.assertEqual(ER.get_config()["keyword"]["effective_model"], "solar-pro2")
        self.assertNotIn(ER.SOLAR_EXAMPLES["keyword"], ER._system("keyword", {}, "gpt-test"))

    def test_sampling_balances_categories_and_varies_items(self):
        rows = [(f"{category}-{i}", {}, {"content_category": [category + " / Topic"], "intent": []})
                for category, size in (("Finance", 50), ("Sports", 10), ("Arts", 1)) for i in range(size)]
        one = ER._sample_rows(rows, 6, random.Random(7))
        two = ER._sample_rows(rows, 6, random.Random(19))
        self.assertEqual(len({r[0] for r in one}), 6)
        self.assertEqual({r[0].split("-")[0] for r in one}, {"Finance", "Sports", "Arts"})
        self.assertNotEqual([r[0] for r in one], [r[0] for r in two])
        self.assertEqual(len(ER._sample_rows(rows, 300, random.Random(7))), len(rows))
        self.assertEqual(len(rows), 61)
        fallback = [(str(i), {}, {"intent": [intent]}) for i, intent in enumerate(["news"] * 10 + ["review"])]
        self.assertEqual({r[2]["intent"][0] for r in ER._sample_rows(fallback, 2, random.Random(7))}, {"news", "review"})

    def test_status_team_scope_and_frozen_elapsed(self):
        run = {"running": False, "team": "a", "started": 1, "elapsed_s": 2.5,
               "items": [], "active": {}, "done": 0, "total": 0}
        with patch.dict(ER._RUNS, {999: run}, clear=True):
            self.assertFalse(ER.status(999, "b")["ok"])
            self.assertFalse(ER.status(999)["ok"])
            with patch.object(ER.time, "time", return_value=100):
                self.assertEqual(ER.status(999, "a")["elapsed_s"], 2.5)
            self.assertNotIn("team", ER.status(999, "a"))

    @unittest.skipUnless(shutil.which("node"), "Node.js 없음")
    def test_client_model_drafts_pagination_and_duplicate_start(self):
        subprocess.run(["node", os.path.join(os.path.dirname(__file__), "test_entrefine_client.js")],
                       check=True, capture_output=True, text=True, timeout=10)

    def test_process_keeps_other_call_on_failure(self):
        class Boom:
            def complete_json(self, *a, **k):
                raise RuntimeError("down")
        ER._SV = types.SimpleNamespace(get_store=lambda: None)
        self.addCleanup(lambda: setattr(ER, "_SV", None))
        eng = {"keyword": (Boom(), False, "s", "m"), "sentence": (None, True, "s", "m")}
        stages = []
        out = ER.process({"title": "가나다라마바사 제목", "body": "가 나 다"}, {"entities": ["가", "나", "다"], "summary": "요약"}, eng, stages.append)
        self.assertEqual(stages, ["keyword", "sentence"])
        self.assertIn("down", out["error"]); self.assertEqual(len(out["base"]), 3)  # 키워드 실패 · 지금 방식은 남김
        self.assertTrue(out["sentence"]["text"]); self.assertEqual(out["sentence"]["base"], "요약")
        self.assertFalse(out["sentence"]["from_keywords"])                            # 키워드 실패 → 메타만으로


    def test_invalid_sentence_keeps_draft_without_counting_as_success(self):
        draft = "검토 중인 계획이며 아직 확정되지 않았다. " * 8
        class Long:
            def complete_json(self, *args, **kwargs):
                return {"sentence": draft}, None
        result = ER.sentence({"summary": "기존 리드문"}, [], Long())
        self.assertEqual(result["draft"], draft.strip())
        self.assertIn("길이", result["error"])
        self.assertNotIn("text", result)
        self.assertEqual(result["base"], "기존 리드문")
        stats = ER.summary([{"error": "키워드 실패", "sentence": result}])
        self.assertEqual((stats["sent_n"], stats["sent_fails"]), (0, 1))
        self.assertIsNone(stats["sent_len_avg"])

    def test_picker_value_to_model_id(self):
        """픽커 값 'timely|claude-opus-5' 를 그대로 넘기면 라우터가 bad_request(2026-10-07 운영) → 모델 id 만."""
        seen = []
        ER._SV = types.SimpleNamespace(Handler=types.SimpleNamespace(server_mock=False),
                                       llm_for_model=lambda m, mock: (seen.append(m) or object(), "timely"))
        self.addCleanup(lambda: setattr(ER, "_SV", None))
        ER._llm("timely|claude-opus-5"); ER._llm("solar-pro3"); ER._llm("")
        self.assertEqual(seen, ["claude-opus-5", "solar-pro3", ""])
        self.assertEqual(ER._model_id("bizrouter|openai/gpt-5.4"), "openai/gpt-5.4")

    def test_batch_reports_stage_and_finishes_failed_item(self):
        row = {"content": {"title": "시험", "body": "내용"},
               "expected": {"entities": ["가", "나", "다"], "summary": "요약"}}
        ER._SV = types.SimpleNamespace(get_store=lambda: types.SimpleNamespace(get_golden=lambda team: [row]))
        self.addCleanup(lambda: setattr(ER, "_SV", None))
        entered, release = threading.Event(), threading.Event()

        def broken_process(content, item_meta, eng, progress):
            progress("keyword")
            entered.set()
            release.wait(2)
            raise RuntimeError("호출 실패")

        try:
            with patch.object(ER, "_engines", return_value=({"keyword": (None, True, "", "mock"),
                                                              "sentence": (None, True, "", "mock")}, "")), \
                 patch.object(ER, "process", side_effect=broken_process):
                started = ER.start(n=1)
                self.assertTrue(started["ok"])
                self.assertTrue(entered.wait(2))
                during = ER.status(started["id"])
                self.assertEqual(during["done"], 0)
                self.assertEqual(list(during["active"].values()), ["keyword"])
                self.assertGreaterEqual(during["elapsed_s"], 0)
                release.set()
                for _ in range(100):
                    result = ER.status(started["id"])
                    if not result["running"]:
                        break
                    time.sleep(0.01)
                self.assertFalse(result["running"])
                self.assertEqual((result["done"], result["total"]), (1, 1))
                self.assertEqual(result["summary"]["fails"], 1)
                self.assertIn("호출 실패", result["items"][0]["error"])
        finally:
            release.set()

    def test_batch_reprocesses_item_in_mock_mode(self):
        row = {"content": {"title": "가 나", "body": "가 나 다"},
               "expected": {"entities": ["가", "나", "다"], "summary": "가 나 다 소식"}}
        ER._SV = types.SimpleNamespace(get_store=lambda: types.SimpleNamespace(get_golden=lambda team: [row]))
        self.addCleanup(lambda: setattr(ER, "_SV", None))
        engines = {c: (None, True, "", "mock") for c in ER.CALLS}
        with patch.object(ER, "_engines", return_value=(engines, "")):
            started = ER.start(n=1)
            self.assertTrue(started["ok"])
            for _ in range(100):
                result = ER.status(started["id"])
                if not result["running"]:
                    break
                time.sleep(0.01)
        self.assertFalse(result["running"])
        self.assertEqual((result["done"], result["total"]), (1, 1))
        self.assertEqual((result["summary"]["n"], result["summary"]["sent_n"]), (1, 1))
        self.assertTrue(result["items"][0]["refined"]["keywords"])


    def test_fast_client_for_solar_only(self):
        """Solar 는 이 탭 전용 사본(추론 minimal · 제한 120초) · 공유 클라이언트는 그대로 · 다른 제공자는 손대지 않음."""
        from prism.config import Config
        ER._FAST.clear()
        shared = types.SimpleNamespace(model="solar-pro4-260806", cfg=Config())
        other = types.SimpleNamespace(model="claude-opus-5", cfg=Config())
        a = ER._fast(shared)
        self.assertIsNot(a, shared)
        self.assertEqual(a.reasoning_effort, "minimal")
        self.assertGreaterEqual(a.cfg.timeout, 120)
        self.assertIs(ER._fast(shared), a)                           # 모델당 한 번만 만든다(호출 제한 창 공유)
        self.assertNotEqual(getattr(shared.cfg, "timeout", None), a.cfg.timeout) if Config().timeout < 120 else None
        self.assertIs(ER._fast(other), other)
        replacement = types.SimpleNamespace(model=shared.model, cfg=Config(), api_key="fixture-only")
        self.assertIsNot(ER._fast(replacement), a)
        self.assertEqual(ER._fast(replacement).api_key, "fixture-only")


if __name__ == "__main__":
    unittest.main()
