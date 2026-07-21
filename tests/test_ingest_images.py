"""수집 이미지 URL(게시판 #9) 회귀: 별칭 매핑 · 정규화 · content_ref 왕복 · 검수 상세 노출.

카페 등 회원 전용 원문은 비회원 열람이 막혀 사진 확인이 안 된다.
수집 피드가 전달한 이미지 URL 을 참조 필드로 저장·표시한다(추출 입력 아님 · 해시 불변).

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestImageAliasMapping(unittest.TestCase):
    def test_alias_variants(self):
        from prism import ingest as ING
        for header in ("image", "image_url", "images", "image_urls", "thumbnail",
                       "thumbnail_url", "thumb", "이미지", "썸네일", "사진"):
            m = ING.infer_mapping(["제목", "본문", header])
            self.assertEqual(m.get("image_urls"), header, header)

    def test_image_url_not_stolen_by_source_url(self):
        # 'url' 포함 매칭이 image_url 을 원문 링크로 삼키지 않아야 한다(배정 순서 계약)
        from prism import ingest as ING
        m = ING.infer_mapping(["제목", "본문", "image_url"])
        self.assertEqual(m.get("image_urls"), "image_url")
        self.assertNotIn("source_url", m)
        # 원문 링크 컬럼이 따로 있으면 둘 다 제 필드로
        m2 = ING.infer_mapping(["제목", "본문", "url", "썸네일"])
        self.assertEqual(m2.get("source_url"), "url")
        self.assertEqual(m2.get("image_urls"), "썸네일")


class TestImageNormalization(unittest.TestCase):
    def test_string_list_and_limits(self):
        from prism import ingest as ING
        rows = [
            {"제목": "t1", "본문": "b1", "이미지": "https://a.kakaocdn.net/1.jpg"},
            {"제목": "t2", "본문": "b2",
             "이미지": "https://x.com/1.jpg, http://x.com/2.jpg ,javascript:alert(1)"},
            {"제목": "t3", "본문": "b3",
             "이미지": ["https://x.com/ok.png", "ftp://x.com/no.png", "그냥 텍스트", "",
                        "https://x.com/ok.png"]},                    # 중복·비 http(s) 제외
            {"제목": "t4", "본문": "b4",
             "이미지": [f"https://x.com/{i}.jpg" for i in range(12)]},   # 상한 8개
            {"제목": "t5", "본문": "b5"},                                # 미제공 → 빈 목록
        ]
        out, m = ING.to_contents_rows(rows)
        self.assertEqual(m.get("image_urls"), "이미지")
        self.assertEqual(out[0]["image_urls"], ["https://a.kakaocdn.net/1.jpg"])
        self.assertEqual(out[1]["image_urls"], ["https://x.com/1.jpg", "http://x.com/2.jpg"])
        self.assertEqual(out[2]["image_urls"], ["https://x.com/ok.png"])
        self.assertEqual(len(out[3]["image_urls"]), 8)
        self.assertEqual(out[4]["image_urls"], [])
        # 문자열 필드 계약은 그대로(이미지만 목록)
        self.assertEqual(out[0]["title"], "t1")
        self.assertIsInstance(out[0]["source_url"], str)


class TestContentRefRoundtrip(unittest.TestCase):
    CONTENT = {"displayServiceName": "카페", "title": "제목", "subtitle": "부제",
               "body": "본문", "source_url": "https://cafe.daum.net/x/1",
               "image_urls": ["https://x.com/1.jpg", "https://x.com/2.jpg"]}

    def test_ref_carries_images_and_hash_unchanged(self):
        from prism.schema import Content
        from prism.store import content_hash
        c = Content.from_dict(self.CONTENT)
        self.assertEqual(c.ref()["image_urls"], self.CONTENT["image_urls"])
        # 정체성(4필드 해시)은 이미지 유무와 무관
        no_img = dict(self.CONTENT, image_urls=[])
        self.assertEqual(content_hash(self.CONTENT), content_hash(no_img))
        self.assertEqual(c.body_hash(), Content.from_dict(no_img).body_hash())

    def test_sqlite_payload_roundtrip(self):
        from prism.schema import Content
        from prism.store import Store
        with tempfile.TemporaryDirectory() as td:
            st = Store(os.path.join(td, "t.db"))
            out = {"content_ref": Content.from_dict(self.CONTENT).ref(),
                   "quality_meta": {"finalGrade": "G", "reasons": []},
                   "item_meta": {"summary": "s"}, "trace": {"model": "m"}}
            st.save_dedup([(self.CONTENT, out)], "run-1")
            rows = st.recent(10)
            self.assertEqual(rows[0]["content_ref"]["image_urls"], self.CONTENT["image_urls"])


class TestReviewDetailImages(unittest.TestCase):
    def test_raw_rows_and_detail_row_expose_images(self):
        import prism.serve as SV
        row = {"content_ref": {"displayServiceName": "카페", "title": "t", "subtitle": "", "body": "b",
                               "source_url": "https://cafe.daum.net/x/1",
                               "image_urls": ["https://x.com/1.jpg"]},
               "item_meta": {"summary": "s"}, "quality_meta": {"finalGrade": "G"},
               "trace": {"model": "m", "version": 1}}

        class FakeStore:
            def feedback_map(self, team=None):
                return {}
            def purpose_map(self, team=None):
                return {}

        orig = (SV.results_rows, SV.get_store)
        SV.results_rows = lambda limit=5000, team=None: [row]
        SV.get_store = lambda: FakeStore()
        self.addCleanup(lambda: (setattr(SV, "results_rows", orig[0]),
                                 setattr(SV, "get_store", orig[1])))
        item = SV.raw_rows()["items"][0]
        self.assertEqual(item["images"], ["https://x.com/1.jpg"])
        # 상세/목록 공용 행(_detail_row · final_review_queue 등)도 동일 노출
        self.assertEqual(SV._detail_row(row)["images"], ["https://x.com/1.jpg"])
        # 이미지 없는 과거 payload 는 빈 목록(하위호환)
        old = dict(row, content_ref={"displayServiceName": "n", "title": "t2", "subtitle": "", "body": "b2"})
        self.assertEqual(SV._detail_row(old)["images"], [])


class TestSupastoreSync(unittest.TestCase):
    def test_sync_contents_row_includes_image_urls(self):
        from prism.supastore import SupabaseStore
        captured = {}
        st = SupabaseStore.__new__(SupabaseStore)         # 네트워크 없이 행 구성만 검증
        st._upsert = lambda table, rows: captured.update(table=table, rows=rows)
        content = {"displayServiceName": "카페", "title": "t", "subtitle": "", "body": "b",
                   "image_urls": ["https://x.com/1.jpg"]}
        out = {"quality_meta": {"finalGrade": "G", "review": "yellow"}, "item_meta": {}, "trace": {}}
        n = st.sync_contents([(content, out)])
        self.assertEqual(n, 1)
        self.assertEqual(captured["rows"][0]["image_urls"], ["https://x.com/1.jpg"])


if __name__ == "__main__":
    unittest.main()
