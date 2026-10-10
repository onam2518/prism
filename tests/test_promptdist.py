"""콜별 user 메시지 자리표(promptdist.user_template) 회귀 테스트.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import meta_prompts as MP
from prism import promptdist as PD


class TestUserTemplate(unittest.TestCase):
    """입력 계약(user 메시지)은 손으로 적지 않고 실제 조립 함수에서 뽑는다.
    `test_the_derived_length_is_a_placeholder_not_a_constant` 가 없으면 핸드오프 번들에
    '본문 글자수: 6' 이라는 **엉뚱한 상수**가 계약처럼 나간다 · 이 단언이 유일한 눈이다."""

    def test_the_derived_length_is_a_placeholder_not_a_constant(self):
        t = PD.user_template("intent")
        self.assertIn("{본문 글자수}", t)
        self.assertNotIn("본문 글자수: %d" % len(PD._PH_BODY), t)

    def test_the_image_count_line_keeps_the_information_none_distinction(self):
        t = PD.user_template("intent")
        self.assertIn("정보 없음", t)
        self.assertIn("0장", t)                     # '정보 없음 != 0장' 이 계약에 남아 있어야 한다
        self.assertNotIn(MP._IMG_UNKNOWN, t)        # 자리표로 바뀌었다

    def test_every_call_takes_title_and_body_without_the_service_name(self):
        """2026-09-22 정책(511247058 '호출별 입력 개정'): 네 콜 모두 title·body 만 받는다.
        서비스명이 다시 투영되면 번들의 입력 계약과 실제 입력이 함께 어긋난다."""
        for call in PD.CALLS:
            t = PD.user_template(call)
            self.assertIn("title:", t, call)
            self.assertIn("body:", t, call)
            self.assertNotIn("displayServiceName", t, call)


if __name__ == "__main__":
    unittest.main()
