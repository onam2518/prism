"""오탐 상위 인텐트의 '미부여' 경계 회귀 테스트 (2026-08-12).

배경(운영 실측): 인텐트 지적 441건 중 258건(58.5%)이 **이미 정의가 있는 값**에서 났다.
정의가 없어서가 아니라 '언제 붙이지 말아야 하는지'가 없어서다 — 그 값들은 전부
한 줄짜리 정의였다("정책·행정: 정책·사업 소개 + 법안 통과 + 행정 발표 통합").

검수자 메모에서 뽑은 대표 규칙:
- 정책·행정: "주최보다는 기사의 주제, 즉 행사 개최라는 점에 초점을 맞춰야 함"
  → 주체가 정부라는 이유로 부여하지 않는다(팬덤·화제성의 'UGC 라는 이유로' 와 같은 계열)
- 정형정보: "주기적으로 같은 형식으로 발행되는 기사는 아님" → 보도자료를 정형정보로 보지 않는다
- 정보 공유: "단계별 안내가 있다기보단 팁을 모아둔 것" → 가이드·튜토리얼과 갈린다

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import dictionaries as D
from prism import meta_prompts as M
from prism import prompts as P

# 검수 지적 상위(오탐) · 정의에 미부여 경계가 반드시 있어야 하는 값
OVERASSIGNED = ["심층 분석", "실용 정보", "학술·전문", "속보·사건 추적",
                "정책·행정", "정형정보", "뉴스·소식", "정보 공유"]


class TestOverassignedValuesHaveBoundaries(unittest.TestCase):
    def test_all_have_definitions(self):
        missing = [v for v in OVERASSIGNED if not D.INTENT_VALUE_DEFS.get(v)]
        self.assertEqual(missing, [], f"정의 누락: {missing}")

    def test_all_state_when_not_to_assign(self):
        """붙일 때만 적으면 모델은 계속 붙인다. 안 붙일 때를 적어야 한다."""
        weak = [v for v in OVERASSIGNED
                if not any(k in D.INTENT_VALUE_DEFS[v] for k in ("미부여", "부여하지 않는다", "아님"))]
        self.assertEqual(weak, [], f"미부여 경계 없음: {weak}")

    def test_definitions_reach_every_service_prompt(self):
        for svc in ("뉴스", "스포츠", "티스토리", "커뮤니티", "연예"):
            txt = M.intent_dictionary_text(svc)
            for v in OVERASSIGNED:
                if v in D.intent_categories_for(svc):
                    self.assertIn(D.INTENT_VALUE_DEFS[v][:16], txt,
                                  f"{svc} 프롬프트에 {v} 정의문이 없다")


class TestSpecificBoundaryRules(unittest.TestCase):
    """검수자가 실제로 지적한 규칙이 정의에 들어갔는지."""

    def test_policy_is_judged_by_topic_not_by_actor(self):
        """핵심 규칙은 '주체가 아니라 주제'다.

        종전에는 갈 곳으로 '뉴스·소식' 을 단언했는데, 그 값은 콘텐츠뷰·커뮤니티 후보이고
        정책·행정이 사는 뉴스에는 없다 — 고를 수 없는 값을 가리키던 결함을 이 테스트가
        오히려 못 박고 있었다(2026-08-12 수정). 지금은 규칙 자체와, 갈 곳이 없을 때의
        지시(서비스값을 비운다)를 단언한다."""
        d = D.INTENT_VALUE_DEFS["정책·행정"]
        self.assertIn("주체", d)
        self.assertIn("주제가 행정 자체", d)
        self.assertIn("서비스값을 비우고", d)
        self.assertNotIn("'뉴스·소식'", d)

    def test_news_absorbs_government_hosted_events(self):
        """정부 주최 행사 소식을 받는 쪽 규칙은 '뉴스·소식' 정의에 남아 있어야 한다."""
        self.assertIn("정부·지자체가 주최한 행사", D.INTENT_VALUE_DEFS["뉴스·소식"])

    def test_formal_info_requires_actual_periodicity(self):
        d = D.INTENT_VALUE_DEFS["정형정보"]
        self.assertIn("되풀이", d)
        self.assertIn("보도자료·공식발표", d)

    def test_info_share_is_separated_from_guide_and_practical(self):
        d = D.INTENT_VALUE_DEFS["정보 공유"]
        self.assertIn("가이드·튜토리얼", d)
        self.assertIn("실용 정보", d)

    def test_no_boundary_points_at_a_nonexistent_value(self):
        """정의가 가리키는 이웃 값이 실제로 사전에 있어야 한다(오타·폐기값 참조 방지)."""
        allv = set(D.INTENT_CATEGORIES_UNIVERSAL) | set(D.INTENT_FORM_UNIVERSAL)
        for vals in D.INTENT_CATEGORIES_BY_SERVICE.values():
            allv |= set(vals)
        for name in ("클립·하이라이트", "가이드·튜토리얼", "실용 정보",
                     "보도자료·공식발표", "오락·유머", "뉴스·소식", "정책·행정"):
            self.assertIn(name, allv, f"정의가 없는 값을 가리킨다: {name}")


class TestPromptVersion(unittest.TestCase):
    def test_version_bumped_past_v17(self):
        import re
        m = re.match(r"imeta@v(\d+)", P.IMETA_VERSION)
        self.assertIsNotNone(m, P.IMETA_VERSION)
        self.assertGreaterEqual(int(m.group(1)), 18, P.IMETA_VERSION)


if __name__ == "__main__":
    unittest.main()
