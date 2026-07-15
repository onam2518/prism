"""토픽 타겟 페르소나(topic_personas): 엔티티×페르소나 친화도(usermeta)를 소비 측으로 개통.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
배경: 친화도 행렬은 '타겟팅 실계산 근거'로 이미 산출되지만 시각화에만 쓰였다(2026-07-15 리뷰 P3-2).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fake_um(team=None, **kw):
    return {"aggregate": {"entity_persona": {
        "personas": ["뉴스 헤비", "스포츠 팬"],
        "rows": [["손흥민", "스포츠 팬", [1.0, 5.0]],
                 ["삼성전자", "뉴스 헤비", [4.0, 1.0]]]}}}


class TestTopicPersonas(unittest.TestCase):
    def _patch(self):
        from prism import serve
        orig = serve.usermeta_data
        serve.usermeta_data = _fake_um
        self.addCleanup(lambda: setattr(serve, "usermeta_data", orig))
        return serve

    def test_ranked_share(self):
        serve = self._patch()
        out = serve.topic_personas(["손흥민"])
        self.assertEqual(out[0]["persona"], "스포츠 팬")
        self.assertAlmostEqual(out[0]["share"], round(5.0 / 6.0, 3))
        self.assertEqual(out[1]["persona"], "뉴스 헤비")

    def test_multi_entity_merge(self):
        serve = self._patch()
        out = serve.topic_personas(["손흥민", "삼성전자"])       # 합산 5:5 + 1:1 → 균형
        self.assertEqual(len(out), 2)
        self.assertAlmostEqual(out[0]["share"], 0.545, places=2)

    def test_unknown_or_empty_entities(self):
        serve = self._patch()
        self.assertEqual(serve.topic_personas(["없는엔티티"]), [])
        self.assertEqual(serve.topic_personas([]), [])

    def test_no_usermeta_graceful(self):
        from prism import serve
        orig = serve.usermeta_data
        serve.usermeta_data = lambda team=None, **kw: {}
        self.addCleanup(lambda: setattr(serve, "usermeta_data", orig))
        self.assertEqual(serve.topic_personas(["손흥민"]), [])


if __name__ == "__main__":
    unittest.main()
