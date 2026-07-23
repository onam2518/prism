"""최종판정(2층 최종검수) 점수 산입 회귀(2026-07-23 요청):

최종판정은 final_verdicts 원장에만 기록되고 검수자 의견 행(feedback)을 만들지 않아
점수 기여가 0이었다 → 최종검수자만 랭킹이 오르지 않았다. 산식에 최종판정 40점을 넣는다.

① 최종판정 1건 = 40점 · 품질 배율 적용
② 기초 검수 0건이어도 리더보드에 노출(최종판정만 하는 검수자)
③ 원장 기반이라 과거 판정도 소급 반영 · 철회(원장에서 제거)분은 자동 제외
④ 판정자 미상(by 공란)은 개인 점수에 귀속하지 않음

실행: python3 -m pytest tests/ -q
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism.store import FINAL_VERDICT_POINTS


def _row(st, rid, team=None):
    for r in st.arena_stats(team=team)["leaderboard"]:
        if r["reviewer_id"] == rid:
            return r
    return None


class TestFinalVerdictPoints(unittest.TestCase):
    def _store(self, d):
        import prism.serve as SV
        from prism.store import Store
        st = Store(os.path.join(d, "t.db"))
        orig = SV.get_store
        SV.get_store = lambda: st
        self.addCleanup(lambda: setattr(SV, "get_store", orig))
        return st

    def _finals(self, st, items, team=None):
        st.save_report("final_verdicts", {"items": items}, team=team)

    def test_final_verdict_awards_points(self):
        with tempfile.TemporaryDirectory() as d:
            st = self._store(d)
            st.save_feedback("h1", "", "", "good", "analyze", "", 1.0, reviewer="a")
            base = _row(st, "a")["points"]                   # 검수 10
            self.assertEqual(base, 10)
            now = time.time()
            self._finals(st, {"x1": {"verdict": "good", "by": "a", "ts": now},
                              "x2": {"verdict": "bad", "by": "a", "ts": now}})
            row = _row(st, "a")
            self.assertEqual(row["final_verdicts"], 2)
            self.assertEqual(row["points"], base + 2 * FINAL_VERDICT_POINTS)

    def test_final_only_reviewer_on_leaderboard(self):
        """기초 검수 이력이 전혀 없는 최종검수자도 리더보드에 나타난다(종전엔 누락)."""
        with tempfile.TemporaryDirectory() as d:
            st = self._store(d)
            self._finals(st, {"x1": {"verdict": "good", "by": "solo", "ts": time.time()}})
            row = _row(st, "solo")
            self.assertIsNotNone(row, "최종판정만 한 검수자가 리더보드에서 빠졌다")
            self.assertEqual(row["reviews"], 0)
            self.assertEqual(row["points"], FINAL_VERDICT_POINTS)

    def test_retroactive_old_verdicts_count(self):
        """소급 보정: 주(week) 창을 벗어난 과거 판정도 누적 점수엔 그대로 산입."""
        with tempfile.TemporaryDirectory() as d:
            st = self._store(d)
            old = time.time() - 60 * 86400.0                 # 60일 전
            self._finals(st, {"x1": {"verdict": "good", "by": "a", "ts": old}})
            row = _row(st, "a")
            self.assertEqual(row["points"], FINAL_VERDICT_POINTS)
            self.assertEqual(row["week_points"], 0)          # 이번주 창엔 미포함

    def test_withdrawn_verdict_not_counted(self):
        with tempfile.TemporaryDirectory() as d:
            st = self._store(d)
            self._finals(st, {"x1": {"verdict": "good", "by": "a", "ts": time.time()}})
            self.assertEqual(_row(st, "a")["points"], FINAL_VERDICT_POINTS)
            self._finals(st, {})                             # 철회 = 원장에서 제거
            self.assertIsNone(_row(st, "a"))

    def test_unknown_judge_not_attributed(self):
        with tempfile.TemporaryDirectory() as d:
            st = self._store(d)
            st.save_feedback("h1", "", "", "good", "analyze", "", 1.0, reviewer="a")
            self._finals(st, {"x1": {"verdict": "good", "by": "", "ts": time.time()}})
            self.assertEqual(_row(st, "a")["points"], 10)    # 판정자 미상 → 아무에게도 안 붙음

    def test_quality_multiplier_applies(self):
        """골드 응답 5건 이상이면 품질 배율이 최종판정 점수에도 걸린다."""
        with tempfile.TemporaryDirectory() as d:
            st = self._store(d)
            for i in range(5):                               # 골드 4정답/1오답 → acc 0.8 → 배율 0.9
                st.save_gold_check("g%d" % i, "a", "good", "good" if i < 4 else "bad")
            self._finals(st, {"x1": {"verdict": "good", "by": "a", "ts": time.time()}})
            row = _row(st, "a")
            self.assertEqual(row["quality_mult"], 0.9)
            # base = 골드응답 5×10 + 최종판정 40 = 90 · ×0.9 = 81
            self.assertEqual(row["points"], round((5 * 10 + FINAL_VERDICT_POINTS) * 0.9))


if __name__ == "__main__":
    unittest.main()
