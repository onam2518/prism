"""Prism 핵심 로직 유닛 테스트 (stdlib unittest · 의존성 0).

실행: python3 -m unittest discover -s tests   (또는 python3 tests/test_core.py)
커버: content_category(list) · 사전화 · 인입 매핑(source_url) · 드릴다운 ·
      상세 표준화 · verify Tier1 화이트리스트 · 스키마 참조 필드.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDictionaries(unittest.TestCase):
    def test_normalize_content_category_snap(self):
        from prism import dictionaries as D
        self.assertEqual(D.normalize_content_category("Sports / Soccer (Domestic)"),
                         "Sports / Soccer (Domestic)")
        self.assertEqual(D.normalize_content_category("엉터리"), "Unclassified")

    def test_normalize_category_list(self):
        from prism import dictionaries as D
        out = D.normalize_category_list(["Sports", "Sports", "엉터리", "News and Politics"])
        self.assertEqual(out, ["Sports", "News and Politics"])          # 중복·미분류 제거
        self.assertEqual(D.normalize_category_list("Sports"), ["Sports"])  # 문자열 허용
        # 구 dict 형식 호환(값만 추림)
        self.assertEqual(D.normalize_category_list({"e": "Sports"}), ["Sports"])


class TestIngest(unittest.TestCase):
    def test_source_url_mapping(self):
        from prism.ingest import to_contents_rows
        rows = [{"제목": "T", "내용": "본문", "서비스명": "뉴스", "원문링크": "https://x/1"}]
        items, m = to_contents_rows(rows)
        self.assertEqual(m.get("source_url"), "원문링크")
        self.assertEqual(items[0]["source_url"], "https://x/1")

    def test_required_missing_raises(self):
        from prism.ingest import to_contents_rows
        with self.assertRaises(ValueError):
            to_contents_rows([{"제목": "T"}])                          # body 없음


class TestSchema(unittest.TestCase):
    def test_content_source_url_and_ref(self):
        from prism.schema import Content
        c = Content.from_dict({"displayServiceName": "뉴스", "title": "T",
                               "body": "B", "url": "https://x/2"})
        self.assertEqual(c.source_url, "https://x/2")                  # url→source_url
        ref = c.ref()
        self.assertEqual(ref["body"], "B")
        self.assertEqual(ref["source_url"], "https://x/2")


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


class TestVerify(unittest.TestCase):
    def test_content_category_tier1_whitelist_list(self):
        from prism.verify import verify_item
        from prism.schema import ItemMeta, Content
        im = ItemMeta(content_category=["News and Politics / Society", "엉터리Tier1 / X",
                                        "News and Politics / Society"])  # 중복+사전외
        verify_item(im, Content(displayServiceName="뉴스", title="t"))
        self.assertEqual(im.content_category, ["News and Politics / Society"])  # 화이트리스트+중복제거


class TestHarnessMock(unittest.TestCase):
    def test_content_category_is_list(self):
        from prism import harness
        from prism.llm import LLMClient
        out = harness.run({"displayServiceName": "연예", "title": "정국 빌보드 1위",
                           "body": "방탄소년단 정국의 솔로 앨범이 빌보드 핫100 1위에 올랐다. 한국 솔로 최초의 기록이다."},
                          LLMClient(mock=True))
        cc = out["item_meta"]["content_category"]
        self.assertIsInstance(cc, list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
