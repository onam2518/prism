"""검수 판정 표시 회귀: 팀 합의(split 포함)와 '내 판정(mine)' 분리.

배경(2026-07-07): 한 검수자가 판정을 정확으로 바꿔도 다른 검수자의 수정필요 표가 남아
합의가 동점(split)이 되는데, 상세 배지가 이진(good 아니면 '수정 필요')이라
"정확으로 했는데 수정필요로 조회" 혼란 발생. /raw 가 fb.mine(요청자 본인 표)을 내려주고
UI 는 3상태(정확/수정/의견 갈림)+내 판정 병기로 수정.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRawFeedbackMine(unittest.TestCase):
    def test_split_consensus_with_mine(self):
        import prism.serve as SV
        row = {"content_ref": {"displayServiceName": "뉴스", "title": "t", "subtitle": "", "body": "b"},
               "item_meta": {"summary": "s"}, "quality_meta": {"finalGrade": "G", "review": "yellow"},
               "trace": {"model": "m", "version": 1}}
        ch = SV._row_key(row["content_ref"])

        class FakeStore:
            def feedback_map(self, team=None):
                return {ch: {"verdicts": [
                    {"reviewer": "복실", "reviewer_id": "uid-a", "verdict": "bad", "ts": 1},
                    {"reviewer": "댕댕", "reviewer_id": "uid-b", "verdict": "good", "ts": 2}],
                    "good": 1, "bad": 1, "n": 2, "consensus": "split", "verdict": "split",
                    "stage": "analyze", "note": ""}}
            def purpose_map(self, team=None):
                return {}

        orig = (SV.results_rows, SV.get_store, SV._inject_gold)
        SV.results_rows = lambda limit=5000, team=None: [row]
        SV.get_store = lambda: FakeStore()
        SV._inject_gold = lambda items, reviewer, team=None: []
        self.addCleanup(lambda: (setattr(SV, "results_rows", orig[0]),
                                 setattr(SV, "get_store", orig[1]),
                                 setattr(SV, "_inject_gold", orig[2])))

        # uid 로 요청(운영 supabase: bearer uid) → 내 표(good) 식별 · 합의는 split 유지
        r = SV.raw_rows(reviewer="uid-b")
        item = next(i for i in r["items"] if i["hash"] == ch)
        self.assertEqual(item["fb"]["verdict"], "split")     # 팀 합의 = 동점
        self.assertEqual(item["fb"]["mine"], "good")         # 내 판정은 정확
        self.assertTrue(item["split"])
        # 표시명으로 요청(sqlite 폴백)도 동일하게 식별
        r2 = SV.raw_rows(reviewer="복실")
        item2 = next(i for i in r2["items"] if i["hash"] == ch)
        self.assertEqual(item2["fb"]["mine"], "bad")
        # 표가 없는 요청자는 mine 빈 값(기존 계약 유지)
        r3 = SV.raw_rows(reviewer="uid-z")
        item3 = next(i for i in r3["items"] if i["hash"] == ch)
        self.assertEqual(item3["fb"]["mine"], "")


if __name__ == "__main__":
    unittest.main()
