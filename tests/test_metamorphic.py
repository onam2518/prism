"""메타모픽·속성 기반 테스트: 입출력 쌍 대신 '변환 전후 관계(MR)'와 '불변 속성'을 검증한다.

왜 이 층이 필요한가: LLM 파이프라인은 정답 오라클을 매 케이스 만들기 어렵다. 메타모픽 테스트는
오라클 없이 관계(동치 입력 → 동치 출력, 단조성, 멱등성)로 결함을 잡는 기법으로, LLM/NLP 시스템
검증에서 표준적 접근으로 정리되어 있다.
근거: Segura et al., "A Survey on Metamorphic Testing" (IEEE TSE 2016) ·
Ribeiro et al., "Beyond Accuracy: Behavioral Testing of NLP Models with CheckList" (ACL 2020,
INV/DIR 테스트 유형) · "Metamorphic Testing of Large Language Models for NLP" (ICSME 2025,
arXiv:2511.02108, NLP 과제 MR 카탈로그 191종) · 속성 기반 무작위 탐색은 MacIver et al.,
"Hypothesis: A new approach to property-based testing" (JOSS 2019)의 접근을 의존성 없이
고정 시드 난수로 축소 적용(재현 가능).

실행: python3 -m pytest tests/test_metamorphic.py -q
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SEED = 20260703          # 고정 시드: 속성 탐색의 재현성 보장(비결정 실패 방지)
N_CASES = 200


class TestDictionaryRelations(unittest.TestCase):
    """사전 정규화 계층의 MR: 멱등성 · 부분집합 · 서비스 동치."""

    def test_mr_normalize_idempotent(self):
        """MR(멱등): f(f(x)) == f(x). 정규화를 두 번 걸어도 결과가 변하면 스냅 로직 결함."""
        from prism import dictionaries as D
        rng = random.Random(SEED)
        pool = (D.IAB_TIER1
                + [f"{t1} / {t2}" for t1, t2s in D.CONTENT_CATEGORY_TIER2.items() for t2 in t2s]
                + ["엉터리", "", "sports", "News and Politics/Society", "Sports / soccer (domestic)"])
        for _ in range(N_CASES):
            xs = rng.sample(pool, k=rng.randint(0, 6))
            once = D.normalize_category_list(xs)
            self.assertEqual(D.normalize_category_list(once), once, xs)

    def test_mr_normalize_output_in_dictionary(self):
        """속성(치역): 정규화 출력은 항상 사전 경로 집합의 부분집합."""
        from prism import dictionaries as D
        valid = set(D.IAB_TIER1) | {f"{t1} / {t2}" for t1, t2s in D.CONTENT_CATEGORY_TIER2.items() for t2 in t2s}
        rng = random.Random(SEED + 1)
        alphabet = "abc가나다 /()"
        for _ in range(N_CASES):
            xs = ["".join(rng.choice(alphabet) for _ in range(rng.randint(1, 24)))
                  for _ in range(rng.randint(0, 4))]
            for out in D.normalize_category_list(xs):
                self.assertIn(out, valid, xs)

    def test_mr_service_name_equivalence(self):
        """MR(동치 입력): 구·신 서비스명과 레거시 UI 명은 같은 인텐트 후보를 만든다(마이그레이션 공존 계약)."""
        from prism import dictionaries as D
        for group in (("VOD", "루프", "카카오TV", "카카오비디오", "동영상"),
                      ("다음카페", "콘텐츠뷰 (커뮤니티)", "커뮤니티"),
                      ("멜론", "음악"), ("티스토리", "블로그")):
            expect = D.intent_categories_for(group[0])
            for alias in group[1:]:
                self.assertEqual(D.intent_categories_for(alias), expect, alias)

    def test_mr_unknown_service_gets_universal_only(self):
        """MR(폴백): 미정의 서비스명은 항상 범용①+②만(서비스 분류값 미부여) · PGC 폴백 계약."""
        from prism import dictionaries as D
        universal = D.INTENT_CATEGORIES_UNIVERSAL + D.INTENT_FORM_UNIVERSAL
        rng = random.Random(SEED + 2)
        for _ in range(50):
            name = "".join(rng.choice("XYZQW낯선서비스") for _ in range(rng.randint(3, 10)))
            if name in getattr(D, "_SERVICE_NAME_MAP", {}):
                continue
            self.assertEqual(D.intent_categories_for(name), universal, name)


class TestLevelCurveProperties(unittest.TestCase):
    """레벨 커브의 수학 속성: 단조성 · 경계 왕복 · 상한."""

    def test_property_monotonic_and_bounded(self):
        from prism.store import level_of, LEVEL_MAX
        rng = random.Random(SEED + 3)
        pts = sorted(rng.randint(0, 200_000) for _ in range(N_CASES))
        levels = [level_of(p) for p in pts]
        self.assertEqual(levels, sorted(levels))            # 포인트 증가 → 레벨 비감소
        self.assertTrue(all(1 <= lv <= LEVEL_MAX for lv in levels))

    def test_mr_floor_roundtrip(self):
        """MR(경계 왕복): 모든 레벨 l 에 대해 level_of(floor(l)) == l, level_of(floor(l)-1) == l-1."""
        from prism.store import level_of, level_floor, LEVEL_MAX
        for lv in range(2, LEVEL_MAX + 1):
            self.assertEqual(level_of(level_floor(lv)), lv)
            self.assertEqual(level_of(level_floor(lv) - 1), lv - 1)


class TestPipelineBehavior(unittest.TestCase):
    """CheckList(ACL 2020) 유형을 mock 파이프라인 계약에 적용: INV(불변) · DIR(방향)."""

    def _extract(self, title, body):
        from prism import pipeline as PIPE, serve
        llm = serve.make_text_llm(serve.Config.load(), True)
        return PIPE.extract({"displayServiceName": "뉴스", "title": title, "body": body}, llm)

    def test_inv_whitespace_padding_keeps_grade(self):
        """INV: 본문 끝 공백 추가는 등급을 바꾸지 않는다."""
        body = "한국은행 금융통화위원회가 기준금리를 현 수준에서 동결하기로 결정했다. 물가 추이를 지켜본다."
        g1 = (self._extract("금리 동결", body).get("quality_meta") or {}).get("finalGrade")
        g2 = (self._extract("금리 동결", body + "   ").get("quality_meta") or {}).get("finalGrade")
        self.assertEqual(g1, g2)

    def test_dir_risk_keyword_never_improves_grade(self):
        """DIR: 위험 신호(광고 키워드) 추가가 등급을 좋게 만들면 안 된다(G→R 은 허용, R→G 금지)."""
        base = "한국은행 금융통화위원회가 기준금리를 현 수준에서 동결하기로 결정했다. 물가 추이를 지켜본다."
        order = {"G": 0, "R": 1}
        g1 = (self._extract("금리 동결", base).get("quality_meta") or {}).get("finalGrade")
        g2 = (self._extract("금리 동결", base + " 지금 구매하면 최저가 쿠폰 할인.").get("quality_meta") or {}).get("finalGrade")
        self.assertGreaterEqual(order.get(g2, 0), order.get(g1, 0))

    def test_property_intent_output_subset_of_dictionary(self):
        """속성(치역): 4호출 결과의 intent 는 어떤 서비스든 항상 해당 사전의 부분집합(기계 검증 계약)."""
        from prism import agents as AG, serve
        from prism import dictionaries as D
        from prism.schema import Content
        llm = serve.make_text_llm(serve.Config.load(), True)
        AG.META_CFG = {"four_calls": True, "call_models": {}}
        for svc in list(D.INTENT_CATEGORIES_BY_SERVICE) + ["낯선서비스"]:
            c = Content(displayServiceName=svc, title="속성 검증 제목",
                        subtitle="", body="속성 검증을 위한 충분히 긴 본문입니다. 결정론 모의 추출로 확인합니다.")
            im, _ = AG.run_item(llm, c)
            valid = set(D.intent_categories_for(svc))
            self.assertTrue(set(im.intent).issubset(valid), (svc, im.intent))


class TestPromptComposition(unittest.TestCase):
    """프롬프트 합성의 MR: 순수성(동일 입력 → 동일 출력) · 오버라이드 복원 · 사전 분리."""

    def test_mr_purity_and_override_roundtrip(self):
        from prism import meta_prompts as MP
        a = MP.call_system("gpt-5.4", "intent", "뉴스")
        b = MP.call_system("gpt-5.4", "intent", "뉴스")
        self.assertEqual(a, b)                              # 순수 함수(캐싱 전제)
        MP.WRAPPER_OVERRIDES = {"gpt": "# T\n{ROLE}\n{RULES}"}
        try:
            self.assertNotEqual(MP.call_system("gpt-5.4", "intent", "뉴스"), a)
        finally:
            MP.WRAPPER_OVERRIDES = {}
        self.assertEqual(MP.call_system("gpt-5.4", "intent", "뉴스"), a)   # 복원 = 원상태

    def test_mr_dictionary_isolation_across_all_families(self):
        """MR(사전 분리): 어느 계열이든 ③에는 IAB 구분 기준이, ④에는 인텐트 분류값이 실리지 않는다."""
        from prism import meta_prompts as MP
        for model in ("gpt-5.4", "gemini-2.5-pro", "claude-sonnet-4.6", "solar-pro3", "unknown-model"):
            s3 = MP.call_system(model, "intent", "스포츠")
            s4 = MP.call_system(model, "category", "스포츠")
            self.assertIn("경기 프리뷰", s3, model)
            self.assertNotIn("구분 기준", s3, model)
            self.assertIn("구분 기준", s4, model)
            self.assertNotIn("경기 프리뷰", s4, model)


if __name__ == "__main__":
    unittest.main()
