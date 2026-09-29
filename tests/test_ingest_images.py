"""수집 이미지 URL(게시판 #9) 회귀: 별칭 매핑 · 정규화 · content_ref 왕복 · 검수 상세 노출.

카페 등 회원 전용 원문은 비회원 열람이 막혀 사진 확인이 안 된다.
수집 피드가 전달한 이미지 URL 을 참조 필드로 저장·표시한다(추출 입력 아님 · 해시 불변).

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import json
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
    def _fake_store(self, captured):
        from prism.supastore import SupabaseStore
        st = SupabaseStore.__new__(SupabaseStore)         # 네트워크 없이 행 구성만 검증
        st._get = lambda table, query="": []            # 신규 행의 보존 조회 성공
        st._upsert = lambda table, rows: captured.update(table=table, rows=rows)
        return st

    def test_sync_contents_row_includes_image_urls(self):
        captured = {}
        st = self._fake_store(captured)
        content = {"displayServiceName": "카페", "title": "t", "subtitle": "", "body": "b",
                   "image_urls": ["https://x.com/1.jpg"]}
        out = {"quality_meta": {"finalGrade": "G", "review": "yellow"}, "item_meta": {}, "trace": {}}
        n = st.sync_contents([(content, out)])
        self.assertEqual(n, 1)
        self.assertEqual(captured["rows"][0]["image_urls"], ["https://x.com/1.jpg"])

    def test_upsert_row_always_writes_image_urls_column(self):
        """계약 고정: 행은 out.content_ref 가 아니라 content 를 읽고, image_urls 를 항상 싣는다.
        = 이미지 없는 content 로 upsert 하면 기존 값이 [] 로 덮인다. 그래서 재실행처럼
        content 를 재구성하는 호출자는 반드시 image_urls 를 되실어야 한다(2026-08-03 유실 원인)."""
        captured = {}
        st = self._fake_store(captured)
        content = {"displayServiceName": "카페", "title": "t", "subtitle": "", "body": "b"}
        out = {"content_ref": {"image_urls": ["https://x.com/keep.jpg"]},   # ref 는 읽지 않는다
               "quality_meta": {"finalGrade": "G"}, "item_meta": {}, "trace": {}}
        st.sync_contents([(content, out)], include_all=True)
        row = captured["rows"][0]
        self.assertIn("image_urls", row)                  # 컬럼이 payload 에 늘 있으니 덮어쓰기다
        self.assertEqual(row["image_urls"], [])


class TestRunPipelineCarriesImages(unittest.TestCase):
    """단건 실행(/run)이 참조 이미지 URL 을 content 에 싣는다.
    종전에는 텍스트 분기가 4필드+source_url 만 재구성해 이미지가 저장 계층에 닿지 못했다."""

    def test_text_branch_keeps_and_sanitizes(self):
        from prism import serve
        res = serve.run_pipeline({"displayServiceName": "카페", "title": "제목", "body": "본문",
                                  "image_urls": "https://x.com/1.jpg, javascript:alert(1)"},
                                 mock=True, persist=False)
        self.assertEqual(res["content"]["image_urls"], ["https://x.com/1.jpg"])

    def test_text_branch_defaults_to_empty_list(self):
        from prism import serve
        res = serve.run_pipeline({"displayServiceName": "카페", "title": "제목", "body": "본문"},
                                 mock=True, persist=False)
        self.assertEqual(res["content"]["image_urls"], [])

    def test_images_alias_accepted(self):
        from prism import serve
        res = serve.run_pipeline({"displayServiceName": "카페", "title": "제목", "body": "본문",
                                  "images": ["https://x.com/a.jpg"]}, mock=True, persist=False)
        self.assertEqual(res["content"]["image_urls"], ["https://x.com/a.jpg"])


class ImagePathBase(unittest.TestCase):
    IMGS = ["https://x.com/1.jpg", "https://x.com/2.jpg"]
    CONTENT = {"displayServiceName": "카페", "title": "사진 있는 글", "subtitle": "",
               "body": "본문", "source_url": "https://cafe.daum.net/x/1", "image_urls": IMGS}

    def _serve(self):
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._INGEST_STATE.clear()
        orig_mock = serve.Handler.server_mock
        serve.Handler.server_mock = True                  # 외부 호출 0(결정론 mock)
        self.addCleanup(serve._INGEST_STATE.clear)
        self.addCleanup(lambda: setattr(serve.Handler, "server_mock", orig_mock))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)
        return serve

    def _images_of(self, serve, title):
        for r in serve.results_rows():
            if (r.get("content_ref") or {}).get("title") == title:
                return (r["content_ref"] or {}).get("image_urls")
        return None


class TestStepOneAddKeepsImages(ImagePathBase):
    def test_add_contents_content_ref_carries_images(self):
        serve = self._serve()
        r = serve.add_contents([dict(self.CONTENT)], source="배치")
        self.assertTrue(r.get("ok"))
        self.assertEqual(self._images_of(serve, self.CONTENT["title"]), self.IMGS)

    def test_add_contents_normalizes_and_counts(self):
        serve = self._serve()
        r = serve.add_contents([dict(self.CONTENT, image_urls="https://x.com/1.jpg,ftp://x/2.jpg"),
                                dict(self.CONTENT, title="사진 없는 글", image_urls=[])],
                               source="배치")
        self.assertEqual(self._images_of(serve, self.CONTENT["title"]), ["https://x.com/1.jpg"])
        self.assertEqual(self._images_of(serve, "사진 없는 글"), [])
        self.assertEqual(r["with_images"], 1)             # 적재율 관측(조용한 0건 재발 방지)

    def test_add_does_not_mutate_caller_dict(self):
        serve = self._serve()
        src = dict(self.CONTENT, image_urls="https://x.com/1.jpg")
        serve.add_contents([src], source="배치")
        self.assertEqual(src["image_urls"], "https://x.com/1.jpg")

    def test_job_message_reports_image_coverage(self):
        serve = self._serve()
        serve.add_contents([dict(self.CONTENT)], source="배치")
        msgs = [j["last_msg"] for j in serve.ingest_status()["jobs"] if j["kind"] == "콘텐츠 추가"]
        self.assertTrue(any("이미지 1/1건" in m for m in msgs), msgs)


class TestRerunKeepsImages(ImagePathBase):
    """운영 유실의 재현·회귀: STEP 1 추가(이미지 있음) → STEP 2 모델 실행(=일괄 재실행).
    재실행이 ref 의 image_urls 를 되싣지 않아 저장 계층이 매번 [] 로 덮어썼고,
    운영 400건이 전부 source='재실행' 이라 한 건도 이미지가 남지 않았다(2026-08-03)."""

    def test_batch_run_after_add_keeps_images(self):
        serve = self._serve()
        serve.add_contents([dict(self.CONTENT)], source="배치")
        self.assertEqual(self._images_of(serve, self.CONTENT["title"]), self.IMGS)   # STEP 1
        r = serve.rerun_all("", None, scope="pending")                               # STEP 2
        self.assertEqual(r.get("done"), 1, r)
        self.assertEqual(self._images_of(serve, self.CONTENT["title"]), self.IMGS)   # 실행 후에도

    def test_rerun_hands_images_to_pipeline(self):
        """재실행이 파이프라인에 넘기는 입력에 이미지가 실린다 — supabase 는 이 content 를
        그대로 행으로 쓰므로(위 계약 테스트) 여기서 빠지면 곧바로 덮어쓰기가 된다."""
        import prism.runops as RN
        serve = self._serve()
        serve.add_contents([dict(self.CONTENT)], source="배치")
        ch = None
        for r in serve.results_rows():
            if (r.get("content_ref") or {}).get("title") == self.CONTENT["title"]:
                ch = serve._row_key(r["content_ref"])
        captured = {}
        orig = RN.run_pipeline
        RN.run_pipeline = lambda fields, **k: (captured.update(fields=fields), {
            "output": {"content_ref": {}, "quality_meta": {"finalGrade": "G"},
                       "item_meta": {"summary": "s"}, "trace": {"model": "m1"}}})[1]
        self.addCleanup(lambda: setattr(RN, "run_pipeline", orig))
        serve.rerun_content(ch, "m1")
        self.assertEqual(captured["fields"]["image_urls"], self.IMGS)


class TestBatchUploadKeepsImages(ImagePathBase):
    def _csv(self):
        return ("콘텐츠 그룹,제목,본문,이미지 URL\n"
                "카페,사진 있는 글,본문,\"https://x.com/1.jpg,https://x.com/2.jpg\"\n"
                "카페,사진 없는 글,본문2,\n").encode("utf-8")

    def test_add_only_upload_keeps_images(self):
        serve = self._serve()
        r = serve.run_batch(self._csv(), "u.csv", add_only=True)
        self.assertEqual(r.get("mapping", {}).get("image_urls"), "이미지 URL")
        self.assertEqual(self._images_of(serve, "사진 있는 글"), self.IMGS)
        self.assertEqual(self._images_of(serve, "사진 없는 글"), [])
        self.assertEqual(r["with_images"], 1)

    def test_extract_upload_keeps_images(self):
        serve = self._serve()
        r = serve.run_batch(self._csv(), "u.csv")
        self.assertEqual(r["with_images"], 1)
        self.assertEqual(self._images_of(serve, "사진 있는 글"), self.IMGS)


class TestSingleAddRouteKeepsImages(unittest.TestCase):
    """/run?add_only=1 의 필드 화이트리스트에 image_urls 가 없어 단건 추가는 항상 유실됐다."""

    def test_route_passes_image_urls_through(self):
        import prism.serve as SV
        captured = {}
        orig = SV.add_contents
        SV.add_contents = lambda contents, **k: (captured.update(contents=contents), {"ok": True})[1]
        self.addCleanup(lambda: setattr(SV, "add_contents", orig))

        class FakeH:
            path = "/run"
            headers = {"Content-Type": "application/json"}
            server_mock = True

            def _req_team(self):
                return None

            def _bearer_uid(self):
                return ""

            def _bearer_email(self):
                return ""

        body = json.dumps({"add_only": "1", "displayServiceName": "카페", "title": "t",
                           "body": "b", "image_urls": ["https://x.com/1.jpg"]}).encode("utf-8")
        SV._POST_ROUTES["/run"][0](FakeH(), body)
        self.assertEqual(captured["contents"][0]["image_urls"], ["https://x.com/1.jpg"])


class TestImageCoverageStat(unittest.TestCase):
    def test_counts_rows_with_images(self):
        from prism.runops import _img_note, img_coverage
        rows = [{"image_urls": ["https://x/1.jpg"]}, {"image_urls": []}, {}]
        self.assertEqual(img_coverage(rows), {"n": 3, "with_images": 1})
        self.assertEqual(_img_note(rows), " · 이미지 1/3건")
        self.assertEqual(_img_note([]), "")                # 빈 인입에는 꼬리표 없음


if __name__ == "__main__":
    unittest.main()
