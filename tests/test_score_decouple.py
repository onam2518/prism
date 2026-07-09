"""게임 점수-검수 데이터 분리 회귀(2026-07-09 요청):

점수·레벨은 검수 데이터에서 파생 계산이라 피드백을 지우면 함께 사라졌다.
① 피드백 전체 삭제 시 사라질 몫을 이벤트 적립으로 자동 보존(총점 불변)
② '게임 점수 초기화' 별도 액션 = 음수 오프셋(검수 데이터·배지·정답셋 불변)
③ 보너스만 남은 검수자도 리더보드 유지 · 점수 0 하한(음수 방지)

실행: python3 -m pytest tests/ -q
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _points(st, rid):
    for row in st.arena_stats()["leaderboard"]:
        if row["reviewer_id"] == rid:
            return row["points"]
    return None


class TestScoreDecouple(unittest.TestCase):
    def _store(self, d):
        import prism.serve as SV
        from prism.store import Store
        st = Store(os.path.join(d, "t.db"))
        orig = SV.get_store
        SV.get_store = lambda: st
        self.addCleanup(lambda: setattr(SV, "get_store", orig))
        return st

    def test_clear_feedback_preserves_points(self):
        import prism.serve as SV
        with tempfile.TemporaryDirectory() as d:
            st = self._store(d)
            st.save_feedback("h1", "", "", "good", "analyze", "", 1.0, reviewer="a")
            st.save_feedback("h2", "", "", "bad", "analyze", "", 2.0, reviewer="a")
            st.save_feedback("h1", "", "", "good", "analyze", "", 3.0, reviewer="b")
            st.log_patch("h1", "a", "summary", {}, {})            # 구조화 교정 5점(삭제 후에도 원천 잔존)
            before_a, before_b = _points(st, "a"), _points(st, "b")
            self.assertEqual((before_a, before_b), (30, 15))      # a=검수20+합의5+교정5 · b=검수10+합의5

            r = SV.admin_action("", None, {"action": "clear_feedback"})
            self.assertTrue(r["ok"])
            self.assertEqual(st.feedback_map(), {})               # 검수 데이터는 삭제됨
            self.assertEqual(_points(st, "a"), before_a)          # 총점 불변(적립 + 잔존 원천 재계산)
            self.assertEqual(_points(st, "b"), before_b)          # 보너스만 남아도 리더보드 유지

    def test_reset_scores_zeroes_without_touching_data(self):
        import prism.serve as SV
        with tempfile.TemporaryDirectory() as d:
            st = self._store(d)
            st.save_feedback("h1", "", "", "good", "analyze", "", 1.0, reviewer="a")
            st.log_event_once("a", "daily5", 1, 20)               # 미션 보너스 포함 총점
            self.assertEqual(_points(st, "a"), 30)

            r = SV.admin_action("", None, {"action": "reset_scores"})
            self.assertEqual((r["ok"], r["reset"]), (True, 1))
            self.assertEqual(_points(st, "a"), 0)                 # 0부터 재시작(레벨도 함께)
            self.assertIn("h1", st.feedback_map())                # 검수 데이터 불변
            r2 = SV.admin_action("", None, {"action": "reset_scores"})
            self.assertEqual(r2["reset"], 0)                      # 재실행 무해(0점은 건너뜀)

            st.save_feedback("h9", "", "", "good", "analyze", "", 9.0, reviewer="a")
            self.assertEqual(_points(st, "a"), 10)                # 이후 활동은 새로 쌓인다

    def test_points_never_negative(self):
        with tempfile.TemporaryDirectory() as d:
            st = self._store(d)
            st.save_feedback("h1", "", "", "good", "analyze", "", 1.0, reviewer="a")
            st.log_event_once("a", "score_reset", 1, -999)        # 과잉 오프셋(콘텐츠 후속 삭제 등)
            row = [r for r in st.arena_stats()["leaderboard"] if r["reviewer_id"] == "a"][0]
            self.assertEqual((row["points"], row["week_points"]), (0, 0))   # 하한 0 · 음수 레벨 없음


if __name__ == "__main__":
    unittest.main()
