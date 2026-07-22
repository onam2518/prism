"""검수 활동 일별 집계(activity_daily): 처리량·교정·골드 정답 추이의 원천.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
계약: 최근 N일 연속(빈 날 0 채움) · 검수=good/bad 판정 수 · 교정=bad 수 · 취소 빈 표 제외 ·
일 경계 = 팀 타임존(day_key · PRISM_TZ_MIN 기본 KST) · 화면 조회는 append-only 롤업과 병합.
"""
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
        from prism.store import day_key
        today = day_key(now)                                       # 팀 타임존(기본 KST) 버킷
        yesterday = day_key(now - DAY)
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


class TestActivityRollupMerge(unittest.TestCase):
    """append-only 활동 롤업(판정 행위 시점 누적)과 스토어 재구성의 일별 max 병합.
    배경: feedback 은 (콘텐츠,검수자)당 1행 upsert 라 재검수하면 과거 날짜의 활동이
    최신 날짜로 이동(드레인) — 롤업이 있어야 추이가 사실을 유지한다."""

    def _serve(self):
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve

    def test_rollup_wins_over_drained_history(self):
        serve = self._serve()
        from prism.store import day_key
        st = serve.get_store()
        now = time.time()
        st.save_feedback("h1", "s", "T", "good", "review", "", now, reviewer="A")
        serve._log_activity_rollup(team=None, reviews=1)                    # 최초 판정
        serve._log_activity_rollup(team=None, reviews=1, corrections=1)     # 재검수(행위마다 누적)
        d = serve.activity_daily_data(None, days=7)
        by = {r["day"]: r for r in d["days"]}
        today = by[day_key(now)]
        self.assertEqual(today["reviews"], 2)          # max(재구성 1, 롤업 2)
        self.assertEqual(today["corrections"], 1)

    def test_apply_feedback_logs_activity(self):
        serve = self._serve()
        from prism.store import day_key
        serve.apply_feedback({"hash": "h9", "verdict": "bad", "reviewer": "A"})
        d = serve.activity_daily_data(None, days=7)
        e = {r["day"]: r for r in d["days"]}[day_key()]
        self.assertGreaterEqual(e["reviews"], 1)
        self.assertGreaterEqual(e["corrections"], 1)

    def test_gold_answer_logs_activity(self):
        serve = self._serve()
        from prism.store import day_key
        serve.apply_feedback({"hash": "gold:ok:abcd", "verdict": "good", "reviewer": "A"})
        d = serve.activity_daily_data(None, days=7)
        e = {r["day"]: r for r in d["days"]}[day_key()]
        self.assertEqual((e["gold_n"], e["gold_correct"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
