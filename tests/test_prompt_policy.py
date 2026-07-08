"""프롬프트·사전 정책 회귀 (2026-07-08 회의 소요 반영).

- 엔티티 수량: 1~3 상한 → 핵심만(건당 가변). 프롬프트 3곳(규칙·스키마·자가검증)과
  agents 절단([:3]) 동기 제거 · 잔존 상한 표기가 다시 들어오면 실패.
- 복합 명사: 분해 금지 지침이 엔티티 정의에 존재.
- 인텐트 관점 축: 옹호·지지 / 반박·비판 신설(범용① · 전 서비스 주입) + 정의 +
  의견·논쟁과의 경계 지침이 사전 주입 텍스트에 포함.

실행: python3 -m pytest tests/ -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestPromptPolicy(unittest.TestCase):
    def test_entity_count_cap_removed(self):
        from prism import meta_prompts as MP
        # 엔티티 콜 관련 텍스트만 검사(인텐트의 '총 1~3개 권장'은 별개 정책 · 유지)
        blob = MP.CALL_RULES["entities"] + MP.CALL_SCHEMAS["entities"] + MP.CALL_SELF_CHECK["entities"]
        self.assertNotIn("1~3", blob)                       # 상한 표기 재유입 방지
        self.assertIn("복합 명사", blob)                     # 분해 금지 지침 존재
        self.assertIn("상한은 없다", blob)

    def test_agents_no_entity_truncation(self):
        import inspect
        from prism import agents as AG
        src = inspect.getsource(AG)
        self.assertNotIn("entities\")) if str(x).strip()][:3]", src)   # [:3] 절단 제거

    def test_perspective_intents_registered(self):
        from prism import dictionaries as D
        for svc in ("뉴스", "티스토리", "미정의서비스"):
            vals = D.intent_categories_for(svc)
            self.assertIn("옹호·지지", vals)                 # 범용① = 전 서비스 주입·검증 통과
            self.assertIn("반박·비판", vals)
        self.assertIn("옹호·지지", D.INTENT_VALUE_DEFS)
        self.assertIn("반박·비판", D.INTENT_VALUE_DEFS)
        self.assertIn("의견·논쟁", D.INTENT_VALUE_DEFS.get("옹호·지지", ""))   # 경계 명시

    def test_intent_dictionary_text_has_boundary_rule(self):
        from prism import meta_prompts as MP
        txt = MP.intent_dictionary_text("뉴스")
        self.assertIn("옹호·지지", txt)
        self.assertIn("반박·비판", txt)
        self.assertIn("관점 축 구분", txt)                   # 경계 지침 주입


if __name__ == "__main__":
    unittest.main()
