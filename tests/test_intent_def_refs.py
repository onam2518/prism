"""인텐트 정의가 가리키는 이웃 값의 도달 가능성 회귀 테스트.

2026-08-12 원본 배경: '정책·행정'(뉴스 전용) 정의가 "정부기관이 연 행사·전시 소식은
'뉴스·소식'" 이라고 적었는데 그 값은 콘텐츠뷰·커뮤니티 후보라 뉴스에서는 고를 수 없었다.
모델은 지시를 받고도 따를 수 없어 원래 값을 그대로 붙였다.

2026-09-22 공통 사전 전환(511017603): 68개 후보가 출처와 무관하게 전부 선택 가능해져
'그 서비스에서 고를 수 없는 이웃 값' 이라는 사각지대 자체가 사라졌다. 그래서 서비스별
도달 가능성 검사는 걷어내고, 남은 계약만 지킨다.
· 정의가 가리키는 값은 사전에 실재해야 한다(오타·폐기값 참조 금지).
· 읽는 법(INTENT_REF_NOTE)이 프롬프트와 검수 화면에 같은 문장으로 도달해야 한다.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import dictionaries as D


class TestDefinitionReferencesAreReachable(unittest.TestCase):
    def test_every_quoted_reference_is_a_real_value(self):
        """오타·폐기값을 가리키지 않는지(전역 존재 검사)."""
        allv = set(D.intent_categories())
        ignore = {"0장", "정보 없음"}          # 분류값이 아닌 인용(설명용 표현)
        unknown = []
        for v, d in D.INTENT_VALUE_DEFS.items():
            for q in re.findall(r"'([^']+)'", d):
                if q in ignore or q in allv:
                    continue
                unknown.append(f"{v} → '{q}'")
        self.assertEqual(unknown, [], "사전에 없는 값을 가리킨다:\n  " + "\n  ".join(unknown))

    def test_no_definition_points_at_a_retired_value(self):
        """통합으로 뺀 7개는 정의문 어디에도 남으면 안 된다(3-1)."""
        bad = [f"{v} → {gone}" for v, d in D.INTENT_VALUE_DEFS.items()
               for gone in D.INTENT_RETIRED if gone in d]
        self.assertEqual(bad, [], "폐기값을 경계로 가리키는 정의:\n  " + "\n  ".join(bad))

    def test_policy_points_at_the_now_reachable_news_value(self):
        """공통 사전에서는 '뉴스·소식' 을 실제로 고를 수 있으므로 다시 그쪽을 가리킨다.

        종전에는 고를 수 없어 "서비스값을 비우고 범용값만" 이라고 우회했는데,
        그 지시는 서비스 분기가 사라진 지금 실행할 대상이 없다."""
        d = D.INTENT_VALUE_DEFS["정책·행정"]
        self.assertIn("주체", d)
        self.assertIn("주제가 행정 자체", d)
        self.assertIn("'뉴스·소식'", d)
        self.assertNotIn("서비스값을 비우고", d)


class TestReadingRuleReachesModelAndReviewer(unittest.TestCase):
    """괄호 값을 어떻게 읽는지는 정의 밖의 규칙이라, 정의문만 고쳐서는 전달되지 않는다."""

    def test_rule_is_in_the_prompt(self):
        from prism import meta_prompts as M
        self.assertIn(D.INTENT_REF_NOTE, M.intent_dictionary_text())

    def test_reading_rule_no_longer_restricts_by_source(self):
        """'다른 서비스 값은 고를 수 없다' 는 읽는 법은 폐지됐다(2-10)."""
        self.assertNotIn("서비스 후보", D.INTENT_REF_NOTE)
        self.assertIn("출처를 근거로 후보를 제한하지 않는다", D.INTENT_REF_NOTE)

    def test_service_branch_is_gone_from_call_rules(self):
        from prism import meta_prompts as M
        rules = M.CALL_RULES["intent"]
        self.assertNotIn("displayServiceName", rules)
        self.assertNotIn("서비스 분류값", rules)
        self.assertNotIn("콘텐츠뷰면", rules)

    def test_rule_is_served_to_the_review_screen(self):
        from prism import dictops as DO
        data = DO.dict_data() if hasattr(DO, "dict_data") else None
        if not isinstance(data, dict) or "intentDefs" not in data:
            self.skipTest("dict_data 가 스토어를 요구하는 구성")
        self.assertEqual(data.get("intentRefNote"), D.INTENT_REF_NOTE)

    def test_prompt_version_bumped(self):
        from prism import prompts as P
        m = re.match(r"imeta@v(\d+)", P.IMETA_VERSION)
        self.assertIsNotNone(m, P.IMETA_VERSION)
        self.assertGreaterEqual(int(m.group(1)), 19, P.IMETA_VERSION)


class TestPersonStoryBoundary(unittest.TestCase):
    """'인물·사연' 은 한 줄 정의뿐이라 "일반인이 누구냐" 를 물었다(게시판 #17).
    가르는 기준은 신분이 아니라 사람 자체가 주제인가다(정책·행정의 '주체가 아니라 주제' 와 같은 계열)."""

    def test_judged_by_subject_not_by_fame(self):
        d = D.INTENT_VALUE_DEFS["인물·사연"]
        self.assertIn("유명", d)
        self.assertIn("주제", d)
        self.assertIn("미부여", d)
        self.assertNotIn("일반인·시민의 삶·사연, 인물 조명", d)   # 옛 한 줄 정의로 되돌아가지 않았는지

    def test_watching_counts_as_experience_for_reviews(self):
        self.assertIn("관람", D.INTENT_VALUE_DEFS["후기·리뷰·비평"])
        self.assertIn("관람·시청도 체험", D.INTENT_VALUE_DEFS["리뷰·분석"])


if __name__ == "__main__":
    unittest.main()
