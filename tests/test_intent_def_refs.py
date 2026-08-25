"""인텐트 정의가 가리키는 이웃 값의 도달 가능성 회귀 테스트 (2026-08-12).

배경: 2026-08-12 갱신에서 '정책·행정'(뉴스 전용) 정의가 "정부기관이 연 행사·전시 소식은
'뉴스·소식'" 이라고 적었는데, **'뉴스·소식' 은 콘텐츠뷰·커뮤니티 후보이고 뉴스에는 없다.**
모델은 뉴스 기사를 보며 그 지시를 받지만 그 값을 고를 수 없다 — 결국 원래 값을 그대로 붙인다.
지시가 무력화되는 조용한 실패라 눈에 띄지 않는다.

먼저 넣었던 테스트는 "사전 전체에 그 값이 있는가"만 봐서 이 결함을 통과시켰다.
값은 존재했다. 다만 **그 서비스에서** 고를 수 없었을 뿐이다.

규칙: 정의가 작은따옴표로 다른 분류값을 가리킬 때, 그 값은
  (a) 같은 서비스 후보에 있거나,
  (b) 없다면 어느 서비스 값인지 문장에 밝혀야 한다(예: 티스토리 '리뷰·분석').
(b)는 기존 관례다 — 밝히면 읽는 쪽이 "여기서 고를 값이 아니다"를 안다.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import dictionaries as D


def _all_values_and_owners():
    allv = set(D.INTENT_CATEGORIES_UNIVERSAL) | set(D.INTENT_FORM_UNIVERSAL)
    owner = {}
    for svc, vals in D.INTENT_CATEGORIES_BY_SERVICE.items():
        allv |= set(vals)
        for v in vals:
            owner.setdefault(v, []).append(svc)
    return allv, owner


class TestDefinitionReferencesAreReachable(unittest.TestCase):
    def test_no_unqualified_reference_outside_the_service(self):
        allv, owner = _all_values_and_owners()
        bad = []
        for svc in D.INTENT_CATEGORIES_BY_SERVICE:
            cand = set(D.intent_categories_for(svc))
            for v in cand:
                d = D.INTENT_VALUE_DEFS.get(v, "")
                for q in re.findall(r"'([^']+)'", d):
                    if q in allv and q not in cand:
                        if not any(o in d for o in owner.get(q, [])):
                            bad.append(f"{svc} · {v} → '{q}'(이 서비스에 없고 소속도 안 밝힘)")
        self.assertEqual(bad, [], "고를 수 없는 값을 가리키는 정의:\n  " + "\n  ".join(bad))

    def test_policy_no_longer_points_at_unavailable_news_value(self):
        """이 결함의 원본 사례를 못 박아 둔다."""
        d = D.INTENT_VALUE_DEFS["정책·행정"]
        self.assertNotIn("'뉴스·소식'", d)
        self.assertIn("서비스값을 비우고", d)      # 갈 곳이 없을 때의 지시가 있어야 한다

    def test_every_quoted_reference_is_a_real_value(self):
        """오타·폐기값을 가리키지 않는지(전역 존재 검사 · 종전 테스트의 역할)."""
        allv, _ = _all_values_and_owners()
        # 분류값이 아닌 인용(설명용 표현)은 제외
        ignore = {"0장", "정보 없음"}
        unknown = []
        for v, d in D.INTENT_VALUE_DEFS.items():
            for q in re.findall(r"'([^']+)'", d):
                if q in ignore or q in allv:
                    continue
                unknown.append(f"{v} → '{q}'")
        self.assertEqual(unknown, [], "사전에 없는 값을 가리킨다:\n  " + "\n  ".join(unknown))


if __name__ == "__main__":
    unittest.main()


class TestParentheticalReferences(unittest.TestCase):
    """괄호 안에 따옴표 없이 적힌 값도 같은 규칙(게시판 #17 · 2026-08-25).

    '실용 정보' 정의의 "(정보 공유)" 가 커뮤니티 전용 값임을 밝히지 않아 검수자가 "뉴스 기사에도
    정보 공유를 붙여야 하나" 를 물었다. 위 테스트는 작은따옴표만 봐서 이런 곳 8군데를 통과시켰다."""

    IGNORE_PHRASES = ("사설·칼럼",)          # 분류값이 아닌 관용 표현(안에 '칼럼' 이 들어 있다)

    def test_no_unqualified_parenthetical_reference_outside_the_service(self):
        allv, owner = _all_values_and_owners()
        by_len = sorted(allv, key=len, reverse=True)      # 긴 이름 먼저 지워 '트렌드·시장 분석' 안의 '트렌드' 오탐 방지
        bad = []
        for svc in D.INTENT_CATEGORIES_BY_SERVICE:
            cand = set(D.intent_categories_for(svc))
            for v in cand:
                d = D.INTENT_VALUE_DEFS.get(v, "")
                for par in re.findall(r"\(([^()]*)\)", d):
                    rest = par
                    for ph in self.IGNORE_PHRASES:
                        rest = rest.replace(ph, " ")
                    for q in by_len:
                        if q == v or q not in rest:
                            continue
                        rest = rest.replace(q, " ")
                        if q not in cand and not any(o in par for o in owner.get(q, [])):
                            bad.append(f"{svc} · {v} → ({q}) 이 서비스에 없고 소속도 안 밝힘")
        self.assertEqual(bad, [], "고를 수 없는 값을 괄호로 가리키는 정의:\n  " + "\n  ".join(bad))

    def test_the_reported_cases_name_their_service(self):
        self.assertIn("커뮤니티 '정보 공유'", D.INTENT_VALUE_DEFS["실용 정보"])
        self.assertIn("커뮤니티 '정보 공유'", D.INTENT_VALUE_DEFS["오락·유머"])
        self.assertIn("티스토리 '리뷰·분석'", D.INTENT_VALUE_DEFS["후기·리뷰·비평"])


class TestReadingRuleReachesModelAndReviewer(unittest.TestCase):
    """괄호 값을 어떻게 읽는지는 정의 밖의 규칙이라, 정의문만 고쳐서는 전달되지 않는다."""

    def test_rule_is_in_every_service_prompt(self):
        from prism import meta_prompts as M
        for svc in ("뉴스", "스포츠", "티스토리", "커뮤니티", "연예", ""):
            self.assertIn(D.INTENT_REF_NOTE, M.intent_dictionary_text(svc), svc or "미정의")

    def test_dead_cross_service_rule_is_gone_from_call_rules(self):
        """'경계 콘텐츠는 양쪽 서비스값 동시 부여 가능' 은 후보에 한 서비스만 실리므로 실행 불가능했다."""
        from prism import meta_prompts as M
        self.assertNotIn("양쪽 분류값 동시 부여", M.CALL_RULES["intent"])
        self.assertIn("커뮤니티 '정보 공유'", M.CALL_RULES["intent"])

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
