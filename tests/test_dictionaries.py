"""사전·스키마 계약: 분류 정규화 · 인입 매핑 · 검증 화이트리스트.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
구조 근거는 TESTING.md 참조.
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


class TestVerify(unittest.TestCase):
    def test_content_category_tier1_whitelist_list(self):
        from prism.verify import verify_item
        from prism.schema import ItemMeta, Content
        im = ItemMeta(content_category=["News and Politics / Society", "엉터리Tier1 / X",
                                        "News and Politics / Society"])  # 중복+사전외
        verify_item(im, Content(displayServiceName="뉴스", title="t"))
        self.assertEqual(im.content_category, ["News and Politics / Society"])  # 화이트리스트+중복제거


if __name__ == "__main__":
    unittest.main()


class TestCategoryKoDisplay(unittest.TestCase):
    """한글 표시명(UI 전용) 계약: 전 분류 커버 · 병기 형식 · 데이터 계층 불변."""

    def test_ko_labels_cover_all_tiers(self):
        from prism import dictionaries as D
        for t1 in D.IAB_TIER1:
            self.assertIn(t1, D.IAB_TIER1_KO, t1)
        all_t2 = [t for v in D.CONTENT_CATEGORY_TIER2.values() for t in v]
        self.assertEqual(len(all_t2), len(set(all_t2)))          # 평면 KO 맵 전제: Tier2 전역 유일
        for t2 in all_t2:
            self.assertIn(t2, D.TIER2_KO, t2)

    def test_category_ko_and_bilingual(self):
        from prism import dictionaries as D
        self.assertEqual(D.category_ko("News and Politics / Politics"), "뉴스·정치 / 정치")
        self.assertEqual(D.category_ko("Sports"), "스포츠")
        self.assertEqual(D.category_ko("Politics"), "정치")       # Tier2 단독도 변환
        self.assertEqual(D.category_ko("엉터리 / Unknown"), "엉터리 / Unknown")   # 미등록은 원문
        self.assertEqual(D.category_bilingual("Sports"), "스포츠 (Sports)")
        self.assertEqual(D.category_bilingual(""), "")

    def test_canonical_values_stay_english(self):
        from prism import dictionaries as D
        # 정규화(저장 계층)는 한글 입력을 스냅하지 않는 한 영문 원문 유지 · KO 맵과 무관
        self.assertEqual(D.normalize_content_category("Sports / Soccer (Domestic)"),
                         "Sports / Soccer (Domestic)")


class TestQualityKoNames(unittest.TestCase):
    def test_quality_names_cover_all_metas(self):
        """품질 병기(한글/영문) 전제: 모든 품질 메타 키에 한글 메타명 존재."""
        from prism import dictionaries as D
        for k in D.QUALITY_METAS:
            self.assertIn(k, D.QUALITY_META_NAMES, k)
            self.assertTrue(D.QUALITY_META_NAMES[k].strip(), k)
