"""서브 화면 헬퍼: 드릴다운·상세 표준화 · 배지 영속 · 집계 캐시.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
구조 근거는 TESTING.md 참조.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDrillAndDetail(unittest.TestCase):
    def _rows(self):
        return [
            {"content_ref": {"title": "삼성 노조", "body": "본문1", "displayServiceName": "뉴스",
                             "source_url": "https://x/1", "body_hash": "h1"},
             "item_meta": {"summary": "리드문1", "entities": ["삼성전자"],
                           "intent": ["사건 경과 보도"], "content_category": ["News and Politics / Society"]},
             "quality_meta": {"finalGrade": "G", "reasons": []}},
            {"content_ref": {"title": "낚시", "body": "본문2", "displayServiceName": "커뮤니티"},
             "item_meta": {"summary": "리드문2", "entities": [], "intent": ["흥미·화제"],
                           "content_category": []},
             "quality_meta": {"finalGrade": "R", "reasons": ["clickbait"]}},
        ]

    def test_detail_row(self):
        from prism.serve import _detail_row
        r = _detail_row(self._rows()[0])
        self.assertEqual(r["title"], "삼성 노조")
        self.assertEqual(r["body"], "본문1")
        self.assertEqual(r["url"], "https://x/1")
        self.assertEqual(r["grade"], "G")
        self.assertIn("삼성전자", r["entities"])

    def test_drill_filter(self):
        import prism.serve as S
        S._LAST_RESULTS = self._rows()                                 # store 없을 때 메모리 사용
        orig = S.get_store
        S.get_store = lambda: None
        try:
            by_intent = S.drill_contents("intent", "사건 경과 보도")
            self.assertEqual(by_intent["n"], 1)
            by_reason = S.drill_contents("reason", "clickbait")
            self.assertEqual(by_reason["n"], 1)
            by_cat = S.drill_contents("category", "News and Politics")
            self.assertEqual(by_cat["n"], 1)
        finally:
            S.get_store = orig


class TestBadges(unittest.TestCase):
    def test_save_badges_echo_without_store(self):
        import prism.serve as S
        orig = S.get_store
        S.get_store = lambda: None
        try:
            out = S.save_badges("u1", ["첫 검수", "연속 3일"])
            self.assertTrue(out["ok"])
            self.assertEqual(out["badges"], ["첫 검수", "연속 3일"])
            self.assertFalse(out.get("persisted", True))
        finally:
            S.get_store = orig

    def test_save_badges_monotonic_union(self):
        import prism.serve as S

        class FakeStore:                                   # save_badges = 기존 ∪ 신규
            def __init__(self):
                self.saved = {"u1": ["첫 검수"]}

            def save_badges(self, uid, earned):
                cur = self.saved.get(uid, [])
                merged = cur + [b for b in earned if b not in cur]
                self.saved[uid] = merged
                return merged

        fake = FakeStore()
        orig = S.get_store
        S.get_store = lambda: fake
        try:
            out = S.save_badges("u1", ["첫 검수", "Lv.5"])
            self.assertEqual(out["badges"], ["첫 검수", "Lv.5"])   # 중복 없이 합집합
        finally:
            S.get_store = orig


class TestAggCache(unittest.TestCase):
    def test_memo_and_invalidation(self):
        import prism.serve as S
        S._AGG_CACHE.clear()
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            return calls["n"]

        a = S._agg_cached(("t", None), fn)
        b = S._agg_cached(("t", None), fn)          # 캐시 히트 → 재계산 없음
        self.assertEqual(a, b)
        self.assertEqual(calls["n"], 1)
        S._agg_bump()                                # 무효화 → 재계산
        c = S._agg_cached(("t", None), fn)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(c, 2)


if __name__ == "__main__":
    unittest.main()
