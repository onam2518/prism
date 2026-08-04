"""콘텐츠 작업 이력(/history) 회귀: 판정+교정+재실행 병합 · 최신순 · ts 형식 혼재 흡수.

실행: python3 -m pytest tests/ -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestContentHistory(unittest.TestCase):
    def test_merged_timeline(self):
        import prism.serve as SV

        class FakeStore:
            def feedback_map(self, team=None):
                return {"h1": {"verdicts": [
                    {"reviewer": "복실", "verdict": "bad", "note": "리드문 어색", "ts": 100.0},
                    # supabase 형 ISO 문자열 ts 도 흡수돼야 한다
                    {"reviewer": "댕댕", "verdict": "good", "note": "", "ts": "2026-07-08T00:00:00+00:00"},
                ]}}
            def patch_rows(self, limit=5000, team=None, content_hash=None):
                # 필터를 무시하고 전량을 돌려줘 클라이언트측 hash 재확인(방어선)도 함께 검증한다
                return [
                    {"hash": "h1", "reviewer": "복실", "element": "summary", "ts": 150.0},
                    {"hash": "h1", "reviewer": "", "element": "rerun:solar->gpt-5.4", "ts": 200.0},
                    {"hash": "h2", "reviewer": "남", "element": "intent", "ts": 999.0},   # 다른 콘텐츠 제외
                ]

        orig = SV.get_store
        SV.get_store = lambda: FakeStore()
        self.addCleanup(lambda: setattr(SV, "get_store", orig))
        r = SV.content_history("h1")
        self.assertTrue(r["ok"])
        kinds = [(i["kind"], i["label"]) for i in r["items"]]
        self.assertEqual(len(r["items"]), 4)                          # h2 제외
        self.assertEqual(r["items"][0]["kind"], "verdict")            # ISO ts(2026년) 가 최신
        self.assertIn("정확", r["items"][0]["label"])
        self.assertIn(("rerun", "초안 재실행 · solar → gpt-5.4"), kinds)
        self.assertIn(("patch", "교정 · summary"), kinds)
        self.assertEqual(r["items"][-1]["note"], "리드문 어색")        # 가장 오래된 판정 + 메모 보존
        self.assertEqual(SV.content_history("")["ok"], False)

    def test_content_group_alias(self):
        from prism import ingest as ING
        m = ING.infer_mapping(["콘텐츠 그룹", "제목", "본문"])
        self.assertEqual(m.get("displayServiceName"), "콘텐츠 그룹")   # 공식 템플릿 헤더 정합


if __name__ == "__main__":
    unittest.main()
