"""검수자 신뢰도 키 정렬 회귀 테스트(2026-07-15 감사 P1-10).

supabase 모드에서 gold_stats 는 reviewer_id(uuid) 키, Dawid-Skene 입력은 표시명 키라
같은 사람이 두 엔트리로 갈라져 신뢰도 블렌드가 성립하지 않았다. feedback_labels 가
reviewer_id(uuid) 우선 키를 쓰도록 정렬한다.

실행: python3 -m pytest tests/test_reviewer_weights.py -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _sb_fmap(n=6):
    """supabase 스타일 feedback_map: verdict 에 reviewer(표시명)+reviewer_id(uuid) 병기."""
    fm = {}
    for i in range(n):
        fm[f"h{i}"] = {"verdicts": [
            {"reviewer": "Alice", "reviewer_id": "uuid-a", "verdict": "good"},
            {"reviewer": "Bob", "reviewer_id": "uuid-b", "verdict": "good" if i % 2 else "bad"},
        ]}
    return fm


class TestFeedbackLabelsKey(unittest.TestCase):
    def test_supabase_keys_by_uuid(self):
        from prism import quality as Q
        labels = Q.feedback_labels(_sb_fmap())
        self.assertEqual(labels["h0"], {"uuid-a": 1, "uuid-b": 0})   # 표시명 아닌 uuid 키
        ds = Q.dawid_skene_binary(labels)["reviewers"]
        self.assertEqual(set(ds.keys()), {"uuid-a", "uuid-b"})       # DS 도 uuid 키

    def test_sqlite_falls_back_to_name(self):
        from prism import quality as Q
        fm = {"h1": {"verdicts": [{"reviewer": "Alice", "verdict": "good"},
                                  {"reviewer": "Bob", "verdict": "bad"}]}}
        labels = Q.feedback_labels(fm)                               # reviewer_id 없음 → 표시명 폴백
        self.assertEqual(labels["h1"], {"Alice": 1, "Bob": 0})


class TestReviewerWeightsBlend(unittest.TestCase):
    """gold(uuid) + DS(uuid) 가 같은 키로 합쳐져 한 사람당 한 엔트리로 블렌드된다."""

    class _FakeStore:
        def gold_stats(self, team=None):
            return {"uuid-a": {"n": 10, "acc": 0.8}}                 # 골드 정확도(uuid 키)

        def feedback_map(self, team=None):
            return _sb_fmap(8)                                       # uuid-a 8유닛 라벨 → DS n>=5

    def setUp(self):
        from prism import serve as SV
        self.SV = SV
        self._orig = SV.get_store
        SV.get_store = lambda: TestReviewerWeightsBlend._FakeStore()

    def tearDown(self):
        self.SV.get_store = self._orig

    def test_single_entry_per_person(self):
        out = self.SV.reviewer_weights(team="t")
        self.assertIn("uuid-a", out)                                # uuid 키로 존재
        self.assertNotIn("Alice", out)                              # 표시명 중복 엔트리 없음(버그였다면 존재)
        self.assertTrue(0.5 <= out["uuid-a"] <= 1.0)                # 블렌드 가중치 범위


if __name__ == "__main__":
    unittest.main()
