"""공통 인텐트 68개 전환 회귀 테스트 (2026-09-22 · 위키 511017603 · 511247058).

정책: 출처(displayServiceName)로 후보를 가르지 않고 모든 콘텐츠에 같은 68개를 준다.
· 고유값 75개 − 통합 7개 = 68개 · 신규 명칭 0개
· 개정 정의 10개 · 현행 정의 계승 58개
· 폐기 7개는 새 출력 금지 · 과거 결과 열람용 대응표(INTENT_RETIRED)만 남기고 자동 치환은 금지

이 파일이 깨지면 사전이 정책에서 벗어난 것이다. 문구는 정책 페이지 표에서 그대로 옮겼다.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import dictionaries as D
from prism import meta_prompts as M

# 정책 페이지 '유지 명칭 중 개정 정의 10개' 표: 값 → (TO-BE 부여 기준, 미부여·중복 방지)
REVISED_DEFS = {
    "실용 정보": ("독자가 바로 적용할 방법·조건·생활 팁이 콘텐츠의 중심인 경우",
                "단계별 수행 안내는 가이드·튜토리얼 우선 · 단순 소식·체험 평가 제외"),
    "큐레이션·모음": ("선별 기준에 따라 여러 정보·작품·곡을 묶어 소개하는 경우",
                   "음악도 동일 기준 · 작품 하나의 발매 소식 제외"),
    "컴백·신작 발매": ("앨범·곡·영화·드라마·예능 등 새 작품의 공개·출시 사실이 중심인 경우",
                   "미공개 작품의 예정 소식은 예고 · 인물 근황만 있으면 제외"),
    "랭킹·리스트": ("순위·차트·순위 변동 또는 항목 나열 자체가 핵심인 경우",
                 "단일 곡의 순위 진입·변동 포함 · 번호만 매긴 안내 절차 제외"),
    "의견·논쟁": ("쟁점에 관한 찬반·복수 관점의 비교나 논의가 본문의 중심인 경우",
                "한쪽 논조는 옹호·지지 또는 반박·비판 우선 · 가벼운 감상 제외"),
    "후기·리뷰·비평": ("직접 사용·관람·청취·체험에 근거한 평가가 중심인 경우",
                   "서비스·대상에 무관하게 적용 · 체험 근거 없는 정보 분석 제외"),
    "리뷰·분석": ("직접 체험 없이 제품·서비스·작품의 정보·스펙·시장 자료를 분석하는 경우",
                "체험에 근거한 평가와 동일 근거로 동시 부여 금지"),
    "창작·작품 공유": ("창작한 글·그림·사진·팬픽·번역 등 작품을 공개하는 경우",
                   "타인의 작품 출시를 알리는 소식 제외"),
    "연재": ("연속된 회차·기획 시리즈의 일부라는 근거가 확인되는 경우",
            "창작 여부와 독립 판정 · 단독 글·작품에 자동 부여 금지"),
    "뉴스·소식": ("새로운 사실·동향·발표를 전하거나 재공유하는 경우",
                "출처에 따른 정의 분리 폐지 · 발매·이적·경기 결과 등 구체적인 소식값 우선"),
}

# 정책 페이지 '변경 사항 · 통합 대상 7개'
RETIRED = {
    "생활·실용정보": ["실용 정보"],
    "음악 큐레이션": ["큐레이션·모음"],
    "신곡·앨범 발매": ["컴백·신작 발매", "예고"],
    "차트·랭킹": ["랭킹·리스트"],
    "의견·토론": ["의견·논쟁", "옹호·지지", "반박·비판"],
    "음원 리뷰·분석": ["후기·리뷰·비평", "리뷰·분석"],
    "창작·연재": ["창작·작품 공유", "연재"],
}


class TestCommonCandidateSet(unittest.TestCase):
    def test_exactly_68_unique_candidates(self):
        cats = D.intent_categories()
        self.assertEqual(len(cats), 68, "후보 수가 68이 아니다")
        self.assertEqual(len(set(cats)), 68, "중복 등재가 있다")

    def test_groups_add_up_to_the_policy_counts(self):
        self.assertEqual(len(D.INTENT_CATEGORIES_UNIVERSAL), 10)   # 범용① 1~10
        self.assertEqual(len(D.INTENT_FORM_UNIVERSAL), 8)          # 범용② 11~18
        self.assertEqual(len(D.INTENT_CATEGORIES_COMMON), 50)      # 세부 종류·속성 19~68

    def test_every_candidate_has_a_definition(self):
        missing = [v for v in D.intent_categories() if not D.INTENT_VALUE_DEFS.get(v)]
        self.assertEqual(missing, [], f"정의 누락: {missing}")

    def test_no_extra_definitions_beyond_the_candidates(self):
        """정의문만 남은 유령 값이 없어야 한다(후보와 정의의 집합이 같다)."""
        self.assertEqual(set(D.INTENT_VALUE_DEFS), set(D.intent_categories()))


class TestRetiredValues(unittest.TestCase):
    def test_retired_seven_are_gone_from_candidates_and_definitions(self):
        for v in RETIRED:
            self.assertNotIn(v, D.intent_categories(), f"{v} 가 후보에 남아 있다")
            self.assertNotIn(v, D.INTENT_VALUE_DEFS, f"{v} 정의문이 남아 있다")

    def test_retired_seven_are_gone_from_the_prompt(self):
        txt = M.call_dictionary("intent")
        for v in RETIRED:
            self.assertNotIn(v, txt, f"{v} 가 프롬프트에 주입된다")

    def test_the_mapping_table_matches_the_policy(self):
        self.assertEqual({k: list(v) for k, v in D.INTENT_RETIRED.items()}, RETIRED)

    def test_the_mapping_targets_are_all_live_candidates(self):
        cats = set(D.intent_categories())
        for old, news in D.INTENT_RETIRED.items():
            for v in news:
                self.assertIn(v, cats, f"{old} → {v} 가 후보에 없다")

    def test_the_table_is_a_display_aid_not_a_substitution(self):
        """정책: 조건부 전환값은 원문 재판정 · 단일 별칭 치환 금지.
        열람용 안내 문구만 만들고, 값을 바꿔치는 경로는 두지 않는다."""
        note = D.retired_note("의견·토론")
        self.assertIn("재판정", note)
        self.assertIn("옹호·지지", note)
        self.assertEqual(D.retired_note("의견·논쟁"), "")     # 살아 있는 값엔 안내가 붙지 않는다


class TestRevisedDefinitions(unittest.TestCase):
    def test_ten_revised_definitions_match_the_policy_wording(self):
        for v, (basis, boundary) in REVISED_DEFS.items():
            d = D.INTENT_VALUE_DEFS[v]
            self.assertTrue(d.startswith(basis), f"{v} 부여 기준이 페이지 문구와 다르다: {d}")
            self.assertIn(boundary, d, f"{v} 미부여·중복 방지 문구가 페이지와 다르다: {d}")

    def test_revised_definitions_reach_the_prompt_verbatim(self):
        txt = M.intent_dictionary_text()
        for v in REVISED_DEFS:
            self.assertIn(f"- {v}: {D.INTENT_VALUE_DEFS[v]}", txt, f"{v} 정의문이 프롬프트와 다르다")

    def test_inherited_definitions_are_58(self):
        """68 = 개정 10 + 계승 58(집계 검증표)."""
        self.assertEqual(len(D.intent_categories()) - len(REVISED_DEFS), 58)


class TestPromptVersionMarksTheTransition(unittest.TestCase):
    """사전이 통째로 바뀌면 버전 태그도 올라가야 한다(정책: 배포 사전·프롬프트·평가 정답의 버전 일치).

    이 태그는 외부 배포 응답(promptdist)과 런 기록(trace.prompt_version)에 그대로 실린다.
    올리지 않으면 파트너와 평가 런이 24~28개 후보 시절 프롬프트와 68개 프롬프트를 같은
    버전으로 읽는다."""

    def test_version_is_at_least_v20(self):
        import re
        from prism import prompts as P
        m = re.match(r"imeta@v(\d+)", P.IMETA_VERSION)
        self.assertIsNotNone(m, P.IMETA_VERSION)
        self.assertGreaterEqual(int(m.group(1)), 20, P.IMETA_VERSION)


class TestEditOverrideStillWorks(unittest.TestCase):
    """사전 편집 오버라이드가 새 키(intent_common)에서도 병합·툼스톤 규약을 지키는지."""

    def setUp(self):
        self._base = list(D.INTENT_CATEGORIES_COMMON)
        self._snap = D._BASE_SNAPSHOT

        def restore():
            D.INTENT_CATEGORIES_COMMON = list(self._base)
            D._BASE_SNAPSHOT = self._snap
        self.addCleanup(restore)

    def test_merge_restores_code_values_and_honors_removals(self):
        D._BASE_SNAPSHOT = None
        kept = [v for v in self._base if v != "트렌드"]
        ov = D.stamp_removals({"intent_common": kept}, "intent_common")
        self.assertEqual(ov[D.REMOVED_KEY]["intent_common"], ["트렌드"])
        D.apply_profile(ov)
        self.assertEqual(D.INTENT_CATEGORIES_COMMON, kept)       # 삭제 유지
        self.assertNotIn("트렌드", D.intent_categories())


if __name__ == "__main__":
    unittest.main()
