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
