"""핫패스 성능 회귀(코드 스윕 2026-07-22).

- SupabaseStore.split_reviewed_today: 미션 확인마다 feedback 전량(_all_feedback)을 스캔하던 것을
  오늘 내가 판정한 해시 집합에 한정한 in.() 조회로 대체. 카운트 동일 · 전량 스캔 호출 금지 ·
  팀 스코프 필터를 in.() 쿼리에도 유지.

supabase 경로는 sqlite 기반 기본 스위트가 커버하지 않으므로 _get 스텁으로 계약을 검증한다.
실행: python3 -m unittest tests.test_perf_hotpath
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSplitReviewedTodayScoped(unittest.TestCase):
    def _store(self, mine, all_rows):
        from prism.supastore import SupabaseStore
        st = SupabaseStore.__new__(SupabaseStore)              # 소켓/설정 없이 메서드만
        st._today_iso = lambda: "2026-07-22T00:00:00+00:00"
        st._all_feedback = lambda team=None: (_ for _ in ()).throw(
            AssertionError("split_reviewed_today 가 여전히 전량 스캔(_all_feedback)을 호출함"))
        calls = []

        def fake_get(table, query=""):
            calls.append((table, query))
            if "reviewer_id=eq." in query:                    # 오늘 내 판정 해시 목록
                return [{"content_hash": h} for h in mine]
            if "content_hash=in.(" in query:                  # 해시 집합의 전 검수자 판정
                want = set(re.search(r"content_hash=in\.\(([^)]*)\)", query).group(1).split(","))
                return [r for r in all_rows if r["content_hash"] in want]
            return []

        st._get = fake_get
        st._calls = calls
        return st

    def test_counts_split_only(self):
        h1, h2, h3 = "a" * 16, "b" * 16, "c" * 16
        all_rows = [
            {"content_hash": h1, "verdict": "good"},
            {"content_hash": h1, "verdict": "bad"},            # h1 = split(good+bad)
            {"content_hash": h2, "verdict": "good"},           # h2 = good 만
            {"content_hash": h3, "verdict": "bad"},
            {"content_hash": h3, "verdict": "bad"},            # h3 = bad 만
        ]
        st = self._store(mine=[h1, h2, h3], all_rows=all_rows)
        self.assertEqual(st.split_reviewed_today("A", "teamX"), 1)   # h1 만 split
        inq = [q for (t, q) in st._calls if "content_hash=in.(" in q]
        self.assertTrue(inq, "in.() 필터 조회를 사용해야 한다")
        self.assertIn("team_id=eq.teamX", inq[0])              # 팀 스코프 유지
        self.assertIn("verdict=in.(good,bad)", inq[0])

    def test_empty_mine_returns_zero_without_scan(self):
        st = self._store(mine=[], all_rows=[])
        self.assertEqual(st.split_reviewed_today("A", "teamX"), 0)
        self.assertFalse([q for (t, q) in st._calls if "content_hash=in.(" in q])  # 조기 반환


if __name__ == "__main__":
    unittest.main()
