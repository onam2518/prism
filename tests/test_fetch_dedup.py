"""핫패스 중복 fetch 제거 회귀(P1-4/5/6).

같은 요청 안에서 feedback·assignments 전량을 여러 번 내려받던 것을
조회 1회 + 결과 공유(fmap=·rows=·assignments_snapshot)로 줄인 계약을 고정한다.

실행: python3 -m pytest tests/test_fetch_dedup.py -q
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import reviewops as RV          # noqa: E402
from prism import serve                    # noqa: E402
from prism.store import Store              # noqa: E402


class _CountingStore:
    """feedback_map 호출 수만 세는 최소 스토어(가중치·부착 경로 검증용)."""
    def __init__(self):
        self.feedback_map_calls = 0

    def feedback_map(self, team=None, rows=None):
        self.feedback_map_calls += 1
        return {}

    def gold_stats(self, team=None):
        return {}


class TestSharedFmap(unittest.TestCase):
    def setUp(self):
        self._orig = serve._STORE
        self.st = _CountingStore()
        serve._STORE = self.st

    def tearDown(self):
        serve._STORE = self._orig

    def test_reviewer_weights_reuses_fmap(self):
        RV.reviewer_weights("t", fmap={})
        self.assertEqual(self.st.feedback_map_calls, 0)   # fmap 전달 시 재조회 없음

    def test_reviewer_weights_fetches_without_fmap(self):
        RV.reviewer_weights("t")
        self.assertEqual(self.st.feedback_map_calls, 1)   # 기본 동작(하위 호환)

    def test_attach_fb_reuses_fmap(self):
        items = [{"hash": "h1"}]
        out = serve._attach_fb(items, team="t", fmap={"h1": {"good": 1, "bad": 0, "verdicts": []}})
        self.assertEqual(self.st.feedback_map_calls, 0)
        self.assertIn("fb", out[0])


class TestStoreSnapshot(unittest.TestCase):
    def test_sqlite_snapshot_matches_pair(self):
        with tempfile.TemporaryDirectory() as d:
            st = Store(os.path.join(d, "t.db"))
            st.set_assignees("a" * 16, ["r1", "r2"], min_reviewers=2)
            asg, times = st.assignments_snapshot(None)
            self.assertEqual(asg, st.assignees(None))
            self.assertEqual(times, st.assignment_times(None))
            self.assertEqual(sorted(asg["a" * 16]["reviewers"]), ["r1", "r2"])

    def test_sqlite_review_targets_assigned_param(self):
        with tempfile.TemporaryDirectory() as d:
            st = Store(os.path.join(d, "t.db"))
            # 저장된 콘텐츠 없음 → live 교집합이 비므로 assigned 를 줘도 결과 동일(계약 확인)
            self.assertEqual(st.review_targets(None, assigned=set()), st.review_targets(None))


if __name__ == "__main__":
    unittest.main()
