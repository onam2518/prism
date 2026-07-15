"""중복 토픽 감지(similar_topics): 저장 시 비슷한 기존 정의를 경고(저장은 막지 않음).

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
경로: 임베딩 키 있으면 코사인(0.86↑) · 무키면 토큰 자카드(0.5↑) 폴백 · mock 임베딩은 판단 금지.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSimilarTopics(unittest.TestCase):
    def setUp(self):
        # 임베딩 실호출 차단: 키를 걷어 토큰 폴백 경로를 결정적으로 태운다
        self._saved = {k: os.environ.pop(k, None) for k in ("UPSTAGE_API_KEY", "PRISM_API_KEY")}
        self.addCleanup(lambda: [os.environ.__setitem__(k, v)
                                 for k, v in self._saved.items() if v is not None])

    def test_token_fallback_detects_near_duplicate(self):
        from prism.serve import similar_topics
        base = {"id": "t1", "name": "경제 심층분석", "prompt": "경제 산업 심층분석만 모아줘",
                "keywords": ["삼성전자", "반도체"], "cats": ["Business and Finance"]}
        far = {"id": "t3", "name": "스포츠 하이라이트", "prompt": "축구 야구 경기 하이라이트",
               "keywords": ["손흥민"], "cats": ["Sports"]}
        new = {"id": "t2", "name": "경제 심층분석 모음", "prompt": "경제 산업 심층분석 모아줘",
               "keywords": ["삼성전자", "반도체"], "cats": ["Business and Finance"]}
        out = similar_topics(new, [base, far])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], "t1")
        self.assertEqual(out[0]["via"], "token")
        self.assertGreaterEqual(out[0]["score"], 0.5)

    def test_self_and_empty_excluded(self):
        from prism.serve import similar_topics
        d = {"id": "t1", "name": "경제", "prompt": "", "keywords": [], "cats": []}
        self.assertEqual(similar_topics(d, [d]), [])           # 자기 자신 제외
        self.assertEqual(similar_topics(d, []), [])            # 비교 대상 없음
        self.assertEqual(similar_topics({"id": "x"}, [d]), []) # 서명 없음

    def test_distinct_topics_not_flagged(self):
        from prism.serve import similar_topics
        a = {"id": "a", "name": "연예 뉴스", "prompt": "연예인 소식", "keywords": ["아이유"], "cats": []}
        b = {"id": "b", "name": "부동산 정책", "prompt": "부동산 규제 금리", "keywords": ["국토부"], "cats": []}
        self.assertEqual(similar_topics(b, [a]), [])


if __name__ == "__main__":
    unittest.main()
