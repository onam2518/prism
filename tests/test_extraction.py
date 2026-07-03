"""아이템 메타 추출 계약: mock 하네스 · 계열 래퍼 · 분리형 4호출(순차·차단·검증·라우팅).

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
구조 근거는 TESTING.md 참조.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestHarnessMock(unittest.TestCase):
    def test_content_category_is_list(self):
        from prism import harness
        from prism.llm import LLMClient
        out = harness.run({"displayServiceName": "연예", "title": "정국 빌보드 1위",
                           "body": "방탄소년단 정국의 솔로 앨범이 빌보드 핫100 1위에 올랐다. 한국 솔로 최초의 기록이다."},
                          LLMClient(mock=True))
        cc = out["item_meta"]["content_category"]
        self.assertIsInstance(cc, list)


class TestMetaPromptBaseline(unittest.TestCase):
    """기준 문서(contextual-meta-extraction v2.1) 기본 적용: 계열 라우팅·코어 규칙·사전 주입."""
    def test_family_routing(self):
        from prism import meta_prompts as MP
        self.assertEqual(MP.family_of("openai/gpt-5.4-mini"), "gpt")
        self.assertEqual(MP.family_of("gemini-3.1-pro"), "gemini")
        self.assertEqual(MP.family_of("anthropic/claude-sonnet-4.6"), "claude")
        self.assertEqual(MP.family_of("solar-pro3-260323"), "solar")
        self.assertEqual(MP.family_of("deepseek/deepseek-v3.2"), "default")

    def test_item_system_family_framing_and_dicts(self):
        from prism import prompts as P
        from prism.schema import Content
        c = Content(displayServiceName="뉴스", title="제목", subtitle="", body="본문")
        solar = P.item_system(c, "solar-pro3-260323")
        self.assertIn("CRITICAL", solar)
        self.assertIn("자가 검증", solar)
        claude = P.item_system(c, "anthropic/claude-sonnet-4.6")
        self.assertIn("<background>", claude)
        self.assertNotIn("CRITICAL", claude)          # Claude 리터럴리즘: 과격 지시 금지(문서 §5)
        gpt = P.item_system(c, "gpt-5.4")
        self.assertIn("<output_contract>", gpt)
        for sys_p in (solar, claude, gpt):            # 공통: 코어 규칙 + 사전 + 골드 예시
            self.assertIn("인용 출처 vs 핵심 주체", sys_p)
            self.assertIn("노동·사회 이슈", sys_p)     # 확정 표기 + 예시 A
            self.assertIn("Business and Finance", sys_p)
            self.assertIn("인터뷰", sys_p)             # 범용② 주입

    def test_intent_dictionary_contract(self):
        from prism import dictionaries as D
        cats = D.intent_categories_for("뉴스")
        self.assertIn("노동·사회 이슈", cats)          # 확정 1: 구 표기 교체
        self.assertNotIn("노동 이슈 보도", cats)
        for v in D.INTENT_FORM_UNIVERSAL:             # 확정 4: 범용② 8종 주입·검증 포함
            self.assertIn(v, cats)
        self.assertEqual(len(D.INTENT_FORM_UNIVERSAL), 8)


class TestFourCallExtraction(unittest.TestCase):
    """분리형 4호출 계약: 순차 산출 · 단락 차단 · 사전 기계 검증 · 콜별 모델 라우팅."""
    def _llm(self, answers):
        class L:
            model = "solar-pro3-260323"
            mock = True
            def __init__(self):
                self.calls = []
            def complete_json(self, sys_p, user_p, tag=""):
                self.calls.append(tag)
                return dict(answers.get(tag, {})), {"tag": tag}
        return L()

    def _content(self, title="한국은행 기준금리 동결", body="본문", svc="뉴스"):
        from prism.schema import Content
        return Content(displayServiceName=svc, title=title, subtitle="", body=body)

    def test_sequential_calls_and_validation(self):
        from prism import agents as AG
        llm = self._llm({
            "item_summary": {"summary": "한국은행이 기준금리를 동결한 사실을 전한다."},
            "item_entities": {"entities": ["한국은행", "기준금리", "물가", "네번째버림"]},
            "item_intent": {"intent": ["속보·단신", "사전에없는값"]},
            "item_category": {"content_category": ["Business and Finance / Economy", "엉터리"]},
        })
        AG.META_CFG = {"four_calls": True, "call_models": {}}
        im, results = AG.run_item(llm, self._content())
        self.assertEqual(llm.calls, ["item_summary", "item_entities", "item_intent", "item_category"])
        self.assertEqual(len(im.entities), 3)                       # 1~3개 강제
        self.assertEqual(im.intent, ["속보·단신"])                    # 사전 불일치 드롭
        self.assertEqual(im.content_category, ["Business and Finance / Economy"])

    def test_parallel_calls_output_parity(self):
        """parallel_calls A/B 옵션: 산출(ItemMeta)이 순차와 동일하고 트레이스 순서도 ①→② 결정론."""
        from prism import agents as AG
        answers = {
            "item_summary": {"summary": "한국은행이 기준금리를 동결한 사실을 전한다."},
            "item_entities": {"entities": ["한국은행", "기준금리"]},
            "item_intent": {"intent": ["속보·단신"]},
            "item_category": {"content_category": ["Business and Finance / Economy"]},
        }
        AG.META_CFG = {"four_calls": True, "call_models": {}}
        seq_im, seq_res = AG.run_item(self._llm(answers), self._content())
        llm_p = self._llm(answers)
        par_im, par_res = AG.run_item(llm_p, self._content(), parallel=True)
        self.assertEqual((par_im.summary, par_im.entities, par_im.intent, par_im.content_category),
                         (seq_im.summary, seq_im.entities, seq_im.intent, seq_im.content_category))
        self.assertEqual(sorted(llm_p.calls[:2]), ["item_entities", "item_summary"])
        self.assertEqual(llm_p.calls[2:], ["item_intent", "item_category"])
        tags = [r.get("tag") for r in par_res if isinstance(r, dict) and r.get("tag")]
        self.assertEqual(tags, [r.get("tag") for r in seq_res if isinstance(r, dict) and r.get("tag")])

    def test_parallel_empty_summary_still_blocks(self):
        """parallel 모드 단락 차단 파리티: ① 빈값이면 ②는 실행됐어도 산출은 전부 빈 값."""
        from prism import agents as AG
        llm = self._llm({"item_summary": {"summary": ""}, "item_entities": {"entities": ["개체"]}})
        AG.META_CFG = {"four_calls": True, "call_models": {}}
        im, _ = AG.run_item(llm, self._content(title="", body=""), parallel=True)
        self.assertEqual((im.summary, im.entities, im.intent, im.content_category), ("", [], [], []))
        self.assertNotIn("item_intent", llm.calls)              # ③④는 여전히 생략

    def test_empty_summary_short_circuits(self):
        from prism import agents as AG
        llm = self._llm({"item_summary": {"summary": ""}})
        AG.META_CFG = {"four_calls": True, "call_models": {}}
        im, _ = AG.run_item(llm, self._content(title="", body=""))
        self.assertEqual(llm.calls, ["item_summary"])               # 후속 호출 생략
        self.assertEqual((im.summary, im.entities, im.intent), ("", [], []))

    def test_all_dropped_retries_once(self):
        from prism import agents as AG
        llm = self._llm({
            "item_summary": {"summary": "리드문"},
            "item_entities": {"entities": ["개체"]},
            "item_intent": {"intent": ["목록외값"]},
            "item_category": {"content_category": ["News and Politics / Society"]},
        })
        AG.META_CFG = {"four_calls": True, "call_models": {}}
        AG.run_item(llm, self._content())
        self.assertEqual(llm.calls.count("item_intent"), 2)          # 전량 드롭 → 1회 재요청

    def test_call_model_routing(self):
        from prism import agents as AG
        main = self._llm({"item_summary": {"summary": "리드문"}, "item_entities": {"entities": ["개체"]},
                          "item_intent": {"intent": ["속보·단신"]}})
        heavy = self._llm({"item_category": {"content_category": ["News and Politics / Society"]}})
        heavy.model = "gpt-5.4"
        AG.META_CFG = {"four_calls": True, "call_models": {"category": "gpt-5.4"}}
        AG.LLM_FOR_CALL = lambda mid: heavy if mid == "gpt-5.4" else None
        try:
            im, _ = AG.run_item(main, self._content())
        finally:
            AG.LLM_FOR_CALL = None
            AG.META_CFG = {"four_calls": True, "call_models": {}}
        self.assertEqual(heavy.calls, ["item_category"])             # ④만 상위 모델로
        self.assertEqual(im.content_category, ["News and Politics / Society"])


class TestFamilyWrappers(unittest.TestCase):
    def test_override_and_restore(self):
        from prism import meta_prompts as MP
        MP.WRAPPER_OVERRIDES = {"solar": "# 커스텀\n{ROLE}\n{RULES}"}
        try:
            sysp = MP.call_system("solar-pro2", "summary")
            self.assertTrue(sysp.startswith("# 커스텀"))
            self.assertIn("리드문 정의", sysp)                        # 계약 코어는 그대로 삽입
        finally:
            MP.WRAPPER_OVERRIDES = {}
        self.assertTrue(MP.call_system("solar-pro2", "summary").startswith("# 역할"))

    def test_call_dictionaries_are_separated(self):
        from prism import meta_prompts as MP
        s3 = MP.call_system("gpt-5.4", "intent", "스포츠")
        s4 = MP.call_system("gpt-5.4", "category")
        self.assertIn("경기 라인업·중계", s3)                          # ③ = 인텐트 사전(서비스 분기)
        self.assertNotIn("Tier 2 목록", s3.split("IAB")[0]) if "IAB" in s3 else None
        self.assertNotIn("구분 기준", s3)                              # ③에 IAB 사전 미적재
        self.assertIn("구분 기준", s4)                                 # ④ = IAB 사전+기준
        self.assertNotIn("경기 라인업·중계", s4)                        # ④에 인텐트 사전 미적재


if __name__ == "__main__":
    unittest.main()
