"""참조 이미지 백필 회귀 테스트 (2026-08-12).

배경(운영 실측): 콘텐츠 1,451건이 image_urls 전부 빈 목록인데 source_url 은 100% 채워져 있었다
(업로드 시 '이미지 URL' 칸만 비운 것). 프롬프트는 v16 부터 이미지 수를 신호로 받게 돼 있어
입력만 채우면 살아나는데, 채울 길이 없었다 — 링크는 backfill_urls 로 구제 가능했지만
이미지는 그 경로가 없었다. 그 결과 '포토·영상 중심' 이 검수 지적 단일 최다(39건)가 됐다.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import ingestops as IO
from prism.store import Store, content_hash


def _csv(text: str) -> bytes:
    return text.encode("utf-8")


class _SV:
    """ingestops 가 기대하는 serve 역참조 최소 대역."""
    def __init__(self, store):
        self._store = store
        self.bumped = 0

    def get_store(self):
        return self._store

    def results_rows(self, team=None):
        return self._store.recent(500)

    def _row_key(self, ref):
        return content_hash(ref)

    def _agg_bump(self):
        self.bumped += 1


class TestImageBackfill(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.dir, "t.db"))
        self.content = {"displayServiceName": "뉴스", "title": "제목입니다",
                        "subtitle": "", "body": "본문입니다"}
        self.hash = content_hash(self.content)
        self.store.save_many([(self.content, {
            "content_ref": dict(self.content, source_url="", image_urls=[]),
            "item_meta": {"summary": "요약", "entities": [], "intent": [], "content_category": []},
            "quality_meta": {"finalGrade": "G", "reasons": []},
            "trace": {"model": "m", "version": 1},
        })], "test-run")
        self._orig = IO._SV
        IO._SV = _SV(self.store)

    def tearDown(self):
        IO._SV = self._orig

    def _ref(self):
        rows = self.store.recent(10)
        return (rows[0].get("content_ref") or {}) if rows else {}

    # ── 핵심: 이미지만 있는 표로도 백필된다 ──
    def test_image_only_sheet_backfills(self):
        res = IO.backfill_urls(_csv(
            "제목,이미지 URL\n제목입니다,https://img.example/1.jpg\n"), "a.csv")
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res["imgUpdated"], 1)
        self.assertEqual(self._ref().get("image_urls"), ["https://img.example/1.jpg"])

    def test_multiple_images_split_by_comma(self):
        IO.backfill_urls(_csv(
            '제목,이미지 URL\n제목입니다,"https://img.example/1.jpg, https://img.example/2.jpg"\n'), "a.csv")
        self.assertEqual(self._ref().get("image_urls"),
                         ["https://img.example/1.jpg", "https://img.example/2.jpg"])

    def test_link_and_image_together(self):
        res = IO.backfill_urls(_csv(
            "제목,원문 링크,이미지 URL\n제목입니다,https://news.example/1,https://img.example/1.jpg\n"), "a.csv")
        self.assertEqual(res["updated"], 1)        # 링크
        self.assertEqual(res["imgUpdated"], 1)     # 이미지
        ref = self._ref()
        self.assertEqual(ref.get("source_url"), "https://news.example/1")
        self.assertEqual(ref.get("image_urls"), ["https://img.example/1.jpg"])

    # ── 정체성 불변: 백필해도 해시가 그대로여야 재실행·골든 매칭이 같은 행을 가리킨다 ──
    def test_hash_unchanged_after_backfill(self):
        IO.backfill_urls(_csv(
            "제목,이미지 URL\n제목입니다,https://img.example/1.jpg\n"), "a.csv")
        self.assertEqual(content_hash(self._ref()), self.hash)

    # ── 기존 계약 보존 ──
    def test_link_only_sheet_still_works(self):
        res = IO.backfill_urls(_csv(
            "제목,원문 링크\n제목입니다,https://news.example/1\n"), "a.csv")
        self.assertEqual(res["updated"], 1)
        self.assertEqual(res.get("imgUpdated", 0), 0)

    def test_same_value_counts_as_unchanged(self):
        IO.backfill_urls(_csv("제목,이미지 URL\n제목입니다,https://img.example/1.jpg\n"), "a.csv")
        res = IO.backfill_urls(_csv("제목,이미지 URL\n제목입니다,https://img.example/1.jpg\n"), "a.csv")
        self.assertEqual(res["imgUpdated"], 0)
        self.assertEqual(res["imgUnchanged"], 1)

    def test_non_http_image_is_dropped_not_written(self):
        res = IO.backfill_urls(_csv("제목,이미지 URL\n제목입니다,그림1.jpg\n"), "a.csv")
        self.assertEqual(res["imgUpdated"], 0)
        self.assertEqual(self._ref().get("image_urls"), [])   # 쓰레기 값이 실리지 않는다

    def test_missing_both_columns_is_an_error(self):
        res = IO.backfill_urls(_csv("제목,본문\n제목입니다,본문\n"), "a.csv")
        self.assertIn("error", res)

    def test_unmatched_title_does_not_write(self):
        res = IO.backfill_urls(_csv("제목,이미지 URL\n없는 제목,https://img.example/1.jpg\n"), "a.csv")
        self.assertEqual(res["imgUpdated"], 0)
        self.assertEqual(res["noMatch"], 1)
        self.assertEqual(self._ref().get("image_urls"), [])

    def test_korean_alias_header(self):
        res = IO.backfill_urls(_csv("제목,사진\n제목입니다,https://img.example/9.jpg\n"), "a.csv")
        self.assertEqual(res["imgUpdated"], 1)


class TestStoreSetter(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.dir, "t.db"))
        c = {"displayServiceName": "뉴스", "title": "t", "subtitle": "", "body": "b"}
        self.h = content_hash(c)
        self.store.save_many([(c, {"content_ref": dict(c, image_urls=[]),
                                   "item_meta": {}, "quality_meta": {},
                                   "trace": {"model": "m", "version": 1}})], "test-run")

    def test_set_image_urls_returns_false_for_unknown_hash(self):
        self.assertFalse(self.store.set_image_urls("없는해시", ["https://a/1.jpg"]))

    def test_set_image_urls_replaces_list(self):
        self.assertTrue(self.store.set_image_urls(self.h, ["https://a/1.jpg"]))
        ref = self.store.recent(10)[0]["content_ref"]
        self.assertEqual(ref["image_urls"], ["https://a/1.jpg"])
        self.assertTrue(self.store.set_image_urls(self.h, []))     # 비우기도 가능
        self.assertEqual(self.store.recent(10)[0]["content_ref"]["image_urls"], [])


if __name__ == "__main__":
    unittest.main()
