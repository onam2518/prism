"""인텐트 정의 단일 원천 회귀 테스트 (2026-08-12).

배경: 범용① 8종의 정의가 두 곳에 있었다 —
  · INTENT_UNIVERSAL_DEFS : 검수 화면(/dict intentDefs) 노출용
  · INTENT_VALUE_DEFS     : v17 부터 프롬프트 주입용
`{**UNIVERSAL, **VALUE}` 병합이라 v17 배포 순간부터 UI 사전이 100% 덮여 죽은 값이 됐고,
검수자가 보는 문구가 예고 없이 바뀌었다. 사전을 둘로 두면 언젠가 반드시 갈라지므로 하나로 합쳤다.

이 테스트가 지키는 것: **검수자가 읽는 정의와 모델이 받는 정의가 같은 문장이어야 한다.**
기준이 갈리면 사람이 옳게 고친 것을 모델이 계속 되돌리고, 그 불일치가 검수 지적으로 쌓인다.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import dictionaries as D
from prism import dictops as DO
from prism import meta_prompts as M


class TestSingleSource(unittest.TestCase):
    def test_dead_ui_dict_is_gone(self):
        """덮여서 안 쓰이는 사전을 남겨두면 다음 사람이 그걸 고치고 반영이 안 된다."""
        self.assertFalse(hasattr(D, "INTENT_UNIVERSAL_DEFS"),
                         "INTENT_UNIVERSAL_DEFS 는 INTENT_VALUE_DEFS 로 합쳤다")

    def test_review_screen_serves_the_same_dict(self):
        data = DO.dict_data() if hasattr(DO, "dict_data") else None
        if not isinstance(data, dict) or "intentDefs" not in data:
            self.skipTest("dict_data 가 스토어를 요구하는 구성")
        self.assertEqual(data["intentDefs"], dict(D.INTENT_VALUE_DEFS))

    def test_prompt_and_screen_use_identical_text(self):
        """서비스별 프롬프트에 실리는 문장이 검수 화면 정의와 글자 그대로 같아야 한다."""
        for svc in ("뉴스", "스포츠", "티스토리", "커뮤니티", "연예"):
            txt = M.intent_dictionary_text(svc)
            for v in D.INTENT_CATEGORIES_UNIVERSAL:
                self.assertIn(f"- {v}: {D.INTENT_VALUE_DEFS[v]}", txt,
                              f"{svc} · {v} 정의문이 화면과 다르다")


class TestRecoveredClause(unittest.TestCase):
    """합치는 과정에서 옛 UI 문구의 유용한 단서를 잃지 않았는지."""

    def test_practical_info_keeps_interest_consumption_exclusion(self):
        # 옛 UI 정의: "…(이적 소식·경기 일정 등 관심사 소비형 정보 제외)"
        d = D.INTENT_VALUE_DEFS["실용 정보"]
        self.assertIn("관심사 소비형", d)
        self.assertIn("이적 소식", d)

    def test_fandom_keeps_ugc_clause(self):
        self.assertIn("UGC", D.INTENT_VALUE_DEFS["팬덤·화제성"])

    def test_debate_keeps_stance_precedence(self):
        d = D.INTENT_VALUE_DEFS["의견·논쟁"]
        self.assertIn("옹호·지지", d)
        self.assertIn("반박·비판", d)

    def test_every_universal_value_still_defined(self):
        missing = [v for v in D.INTENT_CATEGORIES_UNIVERSAL if not D.INTENT_VALUE_DEFS.get(v)]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
