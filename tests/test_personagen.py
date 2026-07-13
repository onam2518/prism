"""페르소나 능동 생성: 아이템 근거(대표 소비 콘텐츠) 경유 + basis 충실성 게이트.

생성 프롬프트에 실제 소비 콘텐츠(제목·리드문)가 들어가는지, LLM 이 입력에 없는
수치·속성을 지어내면 결정론 폴백으로 강등되는지(fail-open 금지) 검증.
실행: python3 -m pytest tests/ -q (stdlib unittest · 의존성 0)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import personagen as PG


class FakeLLM:
    def __init__(self, obj):
        self.obj = obj
        self.seen_user = None

    def complete_json(self, system, user, tag=""):
        self.seen_user = user
        return self.obj, None


def _user(rep=None):
    return {"user_id": "u1",
            "form": {"세션 길이": "장", "체류·완주": "고", "전환·이동": "느림",
                     "깊이": "몰입", "시간대": "평일 야간"},
            "intensity": {"기획·심층": "고"},
            "interest_entity_categories": [["Finance", 12.5]],
            "interest_intent_categories": [["기획·심층", 9.0]],
            "affinity_entities": [["연준", 5.0]],
            "engagement": {"views": 24, "clicks": 18, "click_rate": 0.75, "avg_dwell_sec": 62.3},
            "rep_contents": rep if rep is not None else [
                {"title": "연준 금리 인하 시점 분석", "summary": "연준이 9월 인하를 시사했다",
                 "cat": "Finance", "intent": "기획·심층", "dwell_sec": 120, "event": "click"}]}


PROFILE = {"user_id": "u1", "age_band": "30대", "interests": ["재테크"], "day_part": "야간"}


class TestItemGrounding(unittest.TestCase):
    """U2: 대표 소비 콘텐츠(제목·리드문)가 생성 근거로 프롬프트에 주입된다."""

    def test_rep_contents_in_prompt(self):
        llm = FakeLLM({"name": "심층러", "full": "야간 심층러", "desc": "금융 심층 몰입 소비",
                       "basis": ["평균 체류 62.3초", "클릭률 0.75", "재테크 관심 선언"]})
        card = PG._gen_one(llm, PROFILE, _user())
        self.assertIn("대표 소비 콘텐츠", llm.seen_user)
        self.assertIn("연준 금리 인하 시점 분석", llm.seen_user)
        self.assertIn("연준이 9월 인하를 시사했다", llm.seen_user)
        self.assertEqual(card["name"], "심층러")
        self.assertNotIn("downgraded", card)


class TestFaithfulnessGate(unittest.TestCase):
    """U3: 입력에 없는 수치·속성을 담은 카드는 폴백으로 강등(fail-open 금지)."""

    def test_fabricated_number_downgrades(self):
        llm = FakeLLM({"name": "심층러", "full": "야간 심층러", "desc": "금융 몰입",
                       "basis": ["평균 체류 999초로 최상위"]})
        card = PG._gen_one(llm, PROFILE, _user())
        self.assertIn("downgraded", card)
        self.assertNotEqual(card["name"], "심층러")

    def test_fabricated_age_band_downgrades(self):
        llm = FakeLLM({"name": "심층러", "full": "야간 심층러",
                       "desc": "20대 금융 몰입 소비", "basis": ["클릭률 0.75"]})
        card = PG._gen_one(llm, PROFILE, _user())
        self.assertIn("downgraded", card)

    def test_grounded_numbers_pass(self):
        llm = FakeLLM({"name": "심층러", "full": "야간 심층러", "desc": "30대 금융 몰입",
                       "basis": ["체류 62.3초 · 클릭 18건", "대표 콘텐츠 체류 120초"]})
        card = PG._gen_one(llm, PROFILE, _user())
        self.assertNotIn("downgraded", card)

    def test_generate_personas_carries_flag(self):
        llm = FakeLLM({"name": "심층러", "full": "야간 심층러", "desc": "금융 몰입",
                       "basis": ["평균 체류 999초"]})
        out = PG.generate_personas(llm, {"u1": PROFILE}, [_user()])
        self.assertIn("downgraded", out["u1"])
        self.assertTrue(out["u1"]["generated"])


class TestFaithfulUnit(unittest.TestCase):
    def test_faithful_checks(self):
        grounds = '{"지표": {"click_rate": 0.75, "views": 24}}'
        ok = {"desc": "클릭률 0.75", "basis": ["소비 24건"]}
        bad = {"desc": "클릭률 82%", "basis": []}
        self.assertTrue(PG._faithful(ok, grounds, {"age_band": ""}))
        self.assertFalse(PG._faithful(bad, grounds, {"age_band": ""}))


if __name__ == "__main__":
    unittest.main()
