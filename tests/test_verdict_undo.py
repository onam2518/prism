"""판정 실행취소 회귀: 취소 = 표 행 삭제(빈 표 미집계 · 팀 표 수 정합) + 작업 이력 '판정 취소'.

배경: 기존에는 취소가 verdict='' 로 upsert 돼 빈 행이 팀 표 수(n)에 계속 집계됐다
(정확 0 · 수정 필요 1 인데 표 3개 표기 · 2026-07-08 사용자 재현).

실행: python3 -m pytest tests/ -q
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestVerdictUndo(unittest.TestCase):
    def test_delete_and_recount(self):
        from prism.store import Store
        with tempfile.TemporaryDirectory() as d:
            st = Store(os.path.join(d, "t.db"))
            st.save_feedback("h1", "", "", "good", "analyze", "", 1.0, reviewer="a")
            st.save_feedback("h1", "", "", "good", "analyze", "", 2.0, reviewer="b")
            st.save_feedback("h1", "", "", "bad", "analyze", "메모", 3.0, reviewer="c")
            fb = st.feedback_map()["h1"]
            self.assertEqual((fb["n"], fb["good"], fb["bad"]), (3, 2, 1))

            self.assertEqual(st.delete_feedback("h1", "a"), "good")   # 이전 판정 반환
            fb = st.feedback_map()["h1"]
            self.assertEqual((fb["n"], fb["good"], fb["bad"]), (2, 1, 1))
            self.assertEqual(fb["consensus"], "split")                # 동수 = 의견 갈림
            self.assertEqual(st.delete_feedback("h1", "a"), "")       # 이미 없는 표

            # 과거 취소가 남긴 빈 표(레거시 데이터)는 집계에서 제외 → 표 수 부풀림 없음
            st.save_feedback("h1", "", "", "", "analyze", "", 4.0, reviewer="z")
            fb = st.feedback_map()["h1"]
            self.assertEqual((fb["n"], fb["good"], fb["bad"]), (2, 1, 1))

    def test_apply_feedback_undo_flow(self):
        import prism.serve as SV
        from prism.store import Store
        with tempfile.TemporaryDirectory() as d:
            st = Store(os.path.join(d, "t.db"))
            orig = SV.get_store
            SV.get_store = lambda: st
            self.addCleanup(lambda: setattr(SV, "get_store", orig))

            SV.apply_feedback({"hash": "h9", "verdict": "good", "reviewer": "복실"})
            self.assertIn("h9", st.feedback_map())
            r = SV.apply_feedback({"hash": "h9", "verdict": "", "reviewer": "복실"})
            self.assertTrue(r["ok"])
            self.assertNotIn("h9", st.feedback_map())                 # 빈 행이 남지 않는다
            hist = SV.content_history("h9")
            labels = [(i["kind"], i["label"]) for i in hist["items"]]
            self.assertIn(("undo", "판정 취소"), labels)              # 취소 사실은 이력에 보존
            # 표가 없던 콘텐츠의 취소는 이력을 만들지 않는다(무기록)
            SV.apply_feedback({"hash": "h0", "verdict": "", "reviewer": "복실"})
            self.assertEqual(SV.content_history("h0")["items"], [])


if __name__ == "__main__":
    unittest.main()
