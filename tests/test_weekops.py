"""주간 운영 기록(prism/weekops.py).

설계 근거: 지난 주를 지금 계산하면 값이 흔들린다(feedback upsert 로 과거 실적이 줄고,
활동 원장은 2026-07-22 부터만 있다). 그래서 마감된 주는 한 번 계산해 고정하고,
스냅샷이 없는 주만 역산 + estimated 로 표시한다.

주차 정의: 월~일 · 1주차 = 2026-07-20 ~ 2026-07-26.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import weekops as W


class _FakeSV:
    """serve 주입 자리를 대신하는 최소 더블(리포트 저장·조회 + crew_data)."""

    def __init__(self, crew=None, reports=None):
        self.reports = dict(reports or {})
        self.crew = crew or {}
        self.saved = 0

    def _report_get(self, kind, team=None, default=None):
        return self.reports.get(kind, default if default is not None else {})

    def _report_save(self, kind, payload, team=None):
        self.reports[kind] = payload
        self.saved += 1

    def crew_data(self, team=None, scope_uid=""):
        return self.crew


class WeekMathTest(unittest.TestCase):
    def test_week1_is_the_anchor_week(self):
        self.assertEqual(W.week_of("2026-07-20"), 1)      # 월
        self.assertEqual(W.week_of("2026-07-26"), 1)      # 일
        self.assertEqual(W.week_range(1), ("2026-07-20", "2026-07-26"))

    def test_next_week_rolls_over_on_monday(self):
        self.assertEqual(W.week_of("2026-07-27"), 2)
        self.assertEqual(W.week_range(2), ("2026-07-27", "2026-08-02"))

    def test_before_anchor_is_zero_or_less(self):
        self.assertEqual(W.week_of("2026-07-19"), 0)      # 앵커 직전 일요일
        self.assertLess(W.week_of("2026-07-01"), 1)

    def test_range_roundtrip_across_month_and_year(self):
        for n in (1, 3, 10, 30, 60):
            start, end = W.week_range(n)
            self.assertEqual(W.week_of(start), n, start)
            self.assertEqual(W.week_of(end), n, end)


class _Base(unittest.TestCase):
    def setUp(self):
        self.crew = {
            "summary": {"pending": 100, "stale_total": 7, "final_pending": 3,
                        "golden_n": 40, "weekly_capacity": 500, "targets_n": 200,
                        "active_members": 4},
            "members": [{"id": "u1", "name": "A", "load": {"done": 12, "pending": 5}}],
            "burndown": [{"day": "2026-07-20", "done": 10, "activity": 12, "left": 60},
                         {"day": "2026-07-26", "done": 4, "activity": 4, "left": 40},
                         {"day": "2026-07-27", "done": 2, "activity": 2, "left": 38}],
        }
        self.act = {"days": {"2026-07-22": {"reviews": 100, "corrections": 40},
                             "2026-07-27": {"reviews": 9, "corrections": 3}}}
        self.sv = _FakeSV(crew=self.crew, reports={"activity_rollup": self.act})
        W._SV = self.sv
        self.addCleanup(lambda: setattr(W, "_SV", None))
        # 2026-07-28(화) = 2주차 · 1주차는 마감된 상태
        self.now = 1785200000.0
        self.assertEqual(W.current_week(self.now), 2)


class CaptureTest(_Base):
    def test_closed_week_is_snapshotted_once(self):
        self.assertEqual(W.capture(None, crew=self.crew, now=self.now), 1)
        saved = self.sv.reports[W.WEEKLY_KIND]["weeks"]
        self.assertIn("1", saved)
        self.assertFalse(saved["1"]["estimated"])
        self.assertEqual(saved["1"]["reviews"], 100)          # 원장 07-22 만 1주차에 포함
        self.assertEqual(saved["1"]["done_assigned"], 14)     # 번다운 07-20·26
        self.assertEqual(saved["1"]["stale_total"], 7)        # 적립 시점 상태값
        self.assertTrue(saved["1"]["captured_at"])

    def test_second_capture_does_not_overwrite(self):
        W.capture(None, crew=self.crew, now=self.now)
        before = dict(self.sv.reports[W.WEEKLY_KIND]["weeks"]["1"])
        self.crew["summary"]["stale_total"] = 999               # 이후 상태가 바뀌어도
        self.assertEqual(W.capture(None, crew=self.crew, now=self.now), 0)
        self.assertEqual(self.sv.reports[W.WEEKLY_KIND]["weeks"]["1"], before)

    def test_current_week_is_never_snapshotted(self):
        W.capture(None, crew=self.crew, now=self.now)
        self.assertNotIn("2", self.sv.reports[W.WEEKLY_KIND]["weeks"])

    def test_no_closed_week_yet_is_noop(self):
        early = 1784600000.0                                    # 1주차 진행 중
        self.assertEqual(W.current_week(early), 1)
        self.assertEqual(W.capture(None, crew=self.crew, now=early), 0)
        self.assertNotIn(W.WEEKLY_KIND, self.sv.reports)

    def test_older_weeks_freeze_as_estimates_not_confirmed(self):
        """1주 이상 화면을 안 연 팀: 확정(_snap)은 직전 마감 주(cur-1)만 —
        '오늘의 상태 지표'가 오래된 과거 주 전부에 estimated=False 확정값으로
        영구 고정되는 오염 방지(감사 2026-08-04). 오래된 주는 역산값으로만 고정한다."""
        later = self.now + 14 * 86400                           # 2026-08-11(화) = 4주차
        self.assertEqual(W.current_week(later), 4)
        self.assertEqual(W.capture(None, crew=self.crew, now=later), 3)
        saved = self.sv.reports[W.WEEKLY_KIND]["weeks"]
        self.assertFalse(saved["3"]["estimated"])               # 직전 마감 주만 확정
        self.assertEqual(saved["3"]["stale_total"], 7)          # 적립 시점 상태값
        for n in ("1", "2"):
            self.assertTrue(saved[n]["estimated"])              # 오래된 주 = 역산 고정
            self.assertIsNone(saved[n]["stale_total"])          # 미래 시점 상태값을 붙이지 않는다
            self.assertTrue(saved[n]["captured_at"])            # 적립 시점은 남긴다(감사 추적)
        # 조회도 추정 표기를 유지한다(estimated 배지 원천)
        r = W.weekly_records(None, weeks=8, crew=self.crew, now=later)
        w1 = next(w for w in r["weeks"] if w["week"] == 1)
        self.assertTrue(w1["estimated"])


class RecordsTest(_Base):
    def test_unsnapshotted_past_week_is_marked_estimated(self):
        r = W.weekly_records(None, weeks=4, crew=self.crew, now=self.now)
        w1 = next(w for w in r["weeks"] if w["week"] == 1)
        self.assertTrue(w1["estimated"])                        # 스냅샷 전 = 역산
        self.assertIsNone(w1["stale_total"])                    # 과거 상태값은 만들지 않는다

    def test_snapshot_wins_over_recomputation(self):
        W.capture(None, crew=self.crew, now=self.now)
        self.crew["burndown"][0]["done"] = 0                    # 재검수로 과거가 줄어든 상황
        r = W.weekly_records(None, weeks=4, crew=self.crew, now=self.now)
        w1 = next(w for w in r["weeks"] if w["week"] == 1)
        self.assertFalse(w1["estimated"])
        self.assertEqual(w1["done_assigned"], 14)               # 고정값 유지

    def test_current_week_is_running_and_uses_live_summary(self):
        r = W.weekly_records(None, weeks=4, crew=self.crew, now=self.now)
        w2 = next(w for w in r["weeks"] if w["week"] == 2)
        self.assertTrue(w2["running"])
        self.assertEqual(w2["pending_end"], 100)                # 진행 중 = 현재 잔여
        self.assertEqual(w2["reviews"], 9)

    def test_ledger_days_exposes_partial_coverage(self):
        """원장이 주의 일부만 덮으면 화면이 '몇 일분'인지 밝힐 수 있어야 한다."""
        r = W.weekly_records(None, weeks=4, crew=self.crew, now=self.now)
        w1 = next(w for w in r["weeks"] if w["week"] == 1)
        self.assertEqual(w1["ledger_days"], 1)                  # 07-22 하루뿐

    def test_newest_week_first(self):
        r = W.weekly_records(None, weeks=4, crew=self.crew, now=self.now)
        self.assertEqual([w["week"] for w in r["weeks"]], [2, 1])
        self.assertEqual(r["current"], 2)
        self.assertEqual(r["week1"], W.WEEK1_MONDAY)


class MarkupTest(unittest.TestCase):
    def test_weekly_panel_rendered(self):
        from prism import page
        self.assertIn("주간 기록", page.PAGE)
        self.assertIn("crewWeeks", page.PAGE)
        self.assertIn("추정", page.PAGE)                        # 역산 주 표시
        self.assertIn("crewIsWeekStart", page.PAGE)             # 번다운 주 구분선


if __name__ == "__main__":
    unittest.main()
