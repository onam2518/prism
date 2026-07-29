"""results_rows·골든셋 30s 캐시 회귀(P1-2/3).

TTL 내 재호출은 스토어를 다시 부르지 않고, 쓰기 경로의 _agg_bump 가 즉시 무효화하며,
반환 리스트는 복사본이라 호출측 정렬·절단이 캐시를 오염시키지 않는다.

실행: python3 -m pytest tests/test_agg_cache_rows.py -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import serve                    # noqa: E402
from prism import reviewops as RV          # noqa: E402


class _CountingStore:
    REMOTE = True                                      # 원격 스토어에서만 캐시가 켜진다

    def __init__(self):
        self.recent_calls = 0
        self.get_golden_calls = 0
        self.rows = [{"content_ref": {"title": "t1"}}, {"content_ref": {"title": "t2"}}]

    def recent(self, limit, team=None):
        self.recent_calls += 1
        return self.rows

    def get_golden(self, team=None):
        self.get_golden_calls += 1
        return [{"content": {"title": "골드", "displayServiceName": "뉴스", "body": "b"},
                 "expected": {"finalGrade": "G"}}]

    def gold_answered(self, reviewer, team=None):
        return set()


class TestRowsCache(unittest.TestCase):
    def setUp(self):
        self._orig = serve._STORE
        self.st = _CountingStore()
        serve._STORE = self.st
        serve._agg_bump()                              # 이전 테스트의 캐시 격리

    def tearDown(self):
        serve._STORE = self._orig
        serve._agg_bump()

    def test_ttl_reuses_store_result(self):
        serve.results_rows(team="t")
        serve.results_rows(team="t")
        serve.results_rows(team="t")
        self.assertEqual(self.st.recent_calls, 1)

    def test_bump_invalidates(self):
        serve.results_rows(team="t")
        serve._agg_bump()
        serve.results_rows(team="t")
        self.assertEqual(self.st.recent_calls, 2)

    def test_returns_copy_not_cache(self):
        a = serve.results_rows(team="t")
        a.reverse()                                    # 호출측 정렬이 캐시를 바꾸면 안 된다
        b = serve.results_rows(team="t")
        self.assertEqual(b[0]["content_ref"]["title"], "t1")

    def test_inject_gold_uses_cache(self):
        RV._inject_gold([{"hash": "x"} for _ in range(20)], "rv1", "t")
        RV._inject_gold([{"hash": "x"} for _ in range(20)], "rv2", "t")
        self.assertEqual(self.st.get_golden_calls, 1)  # 검수자가 달라도 골든 전량 조회는 1회


if __name__ == "__main__":
    unittest.main()
