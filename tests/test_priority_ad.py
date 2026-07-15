"""메타 우선순위(D3 · 260715 회의) 회귀 테스트.

우선순위: 인텐트 부여여부 > 품질 G/R > 광고성 사유. 광고성 계열(ad·spam) 단독 R 인데
정당한 편집 인텐트(보도자료·공식발표)가 부여되면 Red 로 승격하지 않고 사람 검수(yellow)로 보류한다.
유해·법령 사유가 하나라도 있으면 적용하지 않는다(모더레이션 유지).

실행: python3 -m pytest tests/test_priority_ad.py -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeLLM:
    mock = False
    model = "fake"

    def __init__(self, quality, item):
        self._q = quality
        self._i = item

    def complete_json(self, sys_p, user_p, tag=""):
        if tag == "quality":
            return dict(self._q), None
        # 아이템 4분할 콜(summary/entities/intent/category)·통합콜 모두: 전 필드 반환 → 각 콜이 자기 필드 추출
        return dict(self._i), None


def _run(quality, item):
    from prism import harness as H
    return H.run({"displayServiceName": "뉴스", "title": "t", "body": "b"},
                 _FakeLLM(quality, item), H.Methodology())


_AD_R = {"finalGrade": "R", "reasons": ["ad"], "evidence": "광고성"}
_PRESS = {"summary": "s", "entities": [], "intent": ["보도자료·공식발표"], "content_category": []}


class TestAdIntentPriority(unittest.TestCase):
    def test_ad_only_r_with_press_intent_downgrades_to_yellow(self):
        out = _run(_AD_R, _PRESS)
        qm = out["quality_meta"]
        self.assertEqual(qm["review"], "yellow")          # Red 미승격 · 사람 검수
        self.assertEqual(qm["finalGrade"], "")            # 판정 보류(자동 G 도 아님)

    def test_ad_only_r_without_press_intent_stays_r(self):
        out = _run(_AD_R, {**_PRESS, "intent": ["속보·단신"]})
        qm = out["quality_meta"]
        self.assertEqual(qm["finalGrade"], "R")           # 편집 인텐트 없음 → 광고성 R 유지

    def test_harm_r_with_press_intent_stays_r(self):
        # 유해(graphic) 사유가 있으면 편집 인텐트가 있어도 다운그레이드 안 함(모더레이션 유지)
        out = _run({"finalGrade": "R", "reasons": ["graphic"], "evidence": "과잉묘사"}, _PRESS)
        qm = out["quality_meta"]
        self.assertEqual(qm["finalGrade"], "R")

    def test_mixed_ad_and_harm_stays_r(self):
        out = _run({"finalGrade": "R", "reasons": ["ad", "graphic"], "evidence": "x"}, _PRESS)
        self.assertEqual(out["quality_meta"]["finalGrade"], "R")   # commerce 단독 아님 → 유지

    def test_green_unaffected(self):
        out = _run({"finalGrade": "G", "reasons": []}, _PRESS)
        self.assertEqual(out["quality_meta"]["finalGrade"], "G")   # G 는 그대로


if __name__ == "__main__":
    unittest.main()
