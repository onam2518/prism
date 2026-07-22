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


class TestCacheEviction(unittest.TestCase):
    """장기 가동 시 무한 성장 방지: 상한 초과 시 만료 항목 일괄 정리(동작 불변)."""

    def test_rl_hits_evicts_expired(self):
        import time
        from prism import serve as SV
        with SV._RL_LOCK:
            SV._RL_HITS.clear()
            old = time.time() - 120                       # 60s 초과 = 판정 무영향
            for i in range(600):
                SV._RL_HITS[f"k{i}"] = [old]
        self.addCleanup(lambda: SV._RL_HITS.clear())
        SV.rate_limited("fresh")                          # len>512 → 만료 키 일괄 정리 트리거
        self.assertNotIn("k0", SV._RL_HITS)               # 만료 키 제거
        self.assertIn("fresh", SV._RL_HITS)
        self.assertLess(len(SV._RL_HITS), 600)

    def test_team_cache_evicts_expired(self):
        import time
        from prism import serve as SV
        SV._TEAM_CACHE.clear()
        past = time.time() - 10                           # 이미 만료
        for i in range(600):
            SV._TEAM_CACHE[f"u{i}"] = ("t", past)
        orig = SV.get_store
        SV.get_store = lambda: None                       # reviewer_team 없음 → team=None
        self.addCleanup(lambda: setattr(SV, "get_store", orig))
        self.addCleanup(lambda: SV._TEAM_CACHE.clear())
        SV.team_of("newuid")                              # len>512 → 만료분 정리 + 신규 삽입
        self.assertNotIn("u0", SV._TEAM_CACHE)
        self.assertIn("newuid", SV._TEAM_CACHE)


if __name__ == "__main__":
    unittest.main()
