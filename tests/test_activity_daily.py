"""검수 활동 일별 집계(activity_daily): 처리량·교정·골드 정답 추이의 원천.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
계약: 최근 N일 연속(빈 날 0 채움) · 검수=good/bad 판정 수 · 교정=bad 수 · 취소 빈 표 제외.
"""
import datetime as dt
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestActivityDaily(unittest.TestCase):
    def _store(self):
        from prism.store import Store
        return Store(os.path.join(tempfile.mkdtemp(), "t.db"))

    def test_buckets_and_continuity(self):
        st = self._store()
        now = time.time()
        DAY = 86400.0
        st.save_feedback("h1", "s", "T", "good", "review", "", now, reviewer="A")
        st.save_feedback("h2", "s", "T", "bad", "review", "", now, reviewer="A")
        st.save_feedback("h3", "s", "T", "good", "review", "", now - DAY, reviewer="B")
        st.save_feedback("h4", "s", "T", "good", "review", "", now - 40 * DAY, reviewer="B")  # 창 밖
        c = st._conn()
        c.execute("INSERT INTO gold_checks(content_hash,reviewer,expected,verdict,correct,ts) "
                  "VALUES(?,?,?,?,?,?)", ("g1", "A", "ok", "ok", 1, now))
        c.commit()
        rows = st.activity_daily(days=30)
        self.assertEqual(len(rows), 30)                            # 빈 날 포함 연속
        today = dt.date.today().isoformat()
        yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        by = {r["day"]: r for r in rows}
        self.assertEqual(rows[-1]["day"], today)                   # 마지막 = 오늘
        self.assertEqual((by[today]["reviews"], by[today]["corrections"]), (2, 1))
        self.assertEqual((by[today]["gold_n"], by[today]["gold_correct"]), (1, 1))
        self.assertEqual(by[yesterday]["reviews"], 1)
        self.assertEqual(sum(r["reviews"] for r in rows), 3)       # 40일 전 건은 제외

    def test_cancelled_empty_verdict_excluded(self):
        st = self._store()
        st.save_feedback("h1", "s", "T", "", "review", "", time.time(), reviewer="A")   # 취소 빈 표
        rows = st.activity_daily(days=7)
        self.assertEqual(len(rows), 7)
        self.assertEqual(sum(r["reviews"] for r in rows), 0)

    def test_days_clamped(self):
        st = self._store()
        self.assertEqual(len(st.activity_daily(days=0)), 30)       # 0(미지정) → 기본 30
        self.assertEqual(len(st.activity_daily(days=999)), 90)     # 상한 90


if __name__ == "__main__":
    unittest.main()
