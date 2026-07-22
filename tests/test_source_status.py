"""원문 소실 신고 플래그 + 온디맨드 원문 상태 확인 회귀 테스트 (게시판 #10).

content_ref.source_status 에 저장 · 품질 라벨(finalGrade·reasons)과 분리 → 학습 루프 미포함.
raw_rows(검수 목록)·_detail_row(상세)로 노출되어 배지·토글에 흐른다.
check_source_url 은 판정(gone·temp·unknown·ok)만 반환 · 자동 플래그 확정 없음.
네트워크는 전부 스텁(실 외부 요청 금지).

실행: python3 -m pytest tests/test_source_status.py -q
"""
import io
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSourceStatusStore(unittest.TestCase):
    def _store(self):
        from prism.store import Store
        return Store(os.path.join(tempfile.mkdtemp(), "t.db"))

    def _save(self, st, title="콘텐츠"):
        from prism.store import content_hash
        content = {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "본문 " + title}
        out = {"content_ref": dict(content, source_url="https://blog.example.com/1"),
               "quality_meta": {"finalGrade": "G", "reasons": []},
               "item_meta": {"summary": "s", "content_category": ["Sports"], "intent": [], "entities": []},
               "trace": {"model": "m"}}
        st.save_result(content, out, "run1")
        return content_hash(content)

    def _row(self, st):
        return st.recent()[0] if st.recent() else None

    def test_set_toggle_and_isolation(self):
        st = self._store()
        ch = self._save(st)
        self.assertTrue(st.set_source_status(ch, "gone", "복실"))
        row = self._row(st)
        ss = (row["content_ref"] or {}).get("source_status") or {}
        self.assertEqual(ss.get("state"), "gone")                 # 저장됨
        self.assertEqual(ss.get("by"), "복실")                    # 신고자 기록
        self.assertGreater(float(ss.get("ts") or 0), 0)           # 시각 기록
        self.assertEqual(row["quality_meta"]["finalGrade"], "G")  # 등급 불변(라벨 분리)
        self.assertEqual(row["quality_meta"]["reasons"], [])      # 사유 불변
        # 해제: state="" 로 토글(기록은 남김)
        self.assertTrue(st.set_source_status(ch, "", "딱지"))
        ss = (self._row(st)["content_ref"] or {}).get("source_status") or {}
        self.assertEqual(ss.get("state"), "")
        self.assertEqual(ss.get("by"), "딱지")

    def test_missing_hash_and_invalid_state(self):
        st = self._store()
        self.assertFalse(st.set_source_status("nope", "gone", "복실"))
        ch = self._save(st)
        self.assertFalse(st.set_source_status(ch, "weird", "복실"))   # 허용 상태는 gone·"" 뿐

    def test_exposed_in_raw_rows_and_detail(self):
        from prism import serve
        st = self._store()
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        ch = self._save(st, "소실 콘텐츠")
        st.set_source_status(ch, "gone", "복실")
        # 검수 목록(raw_rows): 배지 원천
        items = serve.raw_rows(limit=10)["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0].get("source_status") or {}).get("state"), "gone")
        # 상세/목록 공용 행(_detail_row): 상세 토글 원천
        d = serve._detail_row(self._row(st))
        self.assertEqual((d.get("source_status") or {}).get("state"), "gone")


class _FakeResp(io.BytesIO):
    """urllib 응답 스텁: read/status/컨텍스트 매니저만 흉내."""
    def __init__(self, body=b"", status=200):
        super().__init__(body)
        self.status = status

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestCheckSourceUrl(unittest.TestCase):
    """분류 단위 테스트 · 네트워크는 build_opener 스텁(실 외부 요청·DNS 조회 없음)."""

    URL = "https://blog.example.com/post/1"

    def setUp(self):
        from prism import ingestops
        self._ing = ingestops
        # URL 공인대역 검증은 DNS 조회를 수반 → 스텁(SSRF 검증 자체는 별도 함수 몫)
        self._orig_validate = ingestops._validate_public_url
        ingestops._validate_public_url = lambda url: None
        self._orig_opener = urllib.request.build_opener
        self.addCleanup(lambda: setattr(ingestops, "_validate_public_url", self._orig_validate))
        self.addCleanup(lambda: setattr(urllib.request, "build_opener", self._orig_opener))

    def _stub(self, open_fn):
        class _Opener:
            def open(self, req, timeout=0):
                return open_fn(req)
        urllib.request.build_opener = lambda *handlers: _Opener()

    def _http_error(self, code):
        return urllib.error.HTTPError(self.URL, code, "err", hdrs=None, fp=io.BytesIO(b""))

    def test_gone_by_404_410(self):
        for code in (404, 410):
            self._stub(lambda req, c=code: (_ for _ in ()).throw(self._http_error(c)))
            r = self._ing.check_source_url(self.URL)
            self.assertEqual((r["state"], r["code"]), ("gone", code))

    def test_gone_by_soft404_signature(self):
        body = "<html><body>권한이 없거나 존재하지 않는 글입니다.</body></html>".encode("utf-8")
        self._stub(lambda req: _FakeResp(body, 200))
        r = self._ing.check_source_url(self.URL)
        self.assertEqual(r["state"], "gone")
        self.assertIn("권한이 없거나", r["sign"])                  # 어떤 시그니처가 걸렸는지 반환

    def test_temp_by_5xx_and_network_failure(self):
        self._stub(lambda req: (_ for _ in ()).throw(self._http_error(503)))
        self.assertEqual(self._ing.check_source_url(self.URL)["state"], "temp")
        self._stub(lambda req: (_ for _ in ()).throw(urllib.error.URLError("timed out")))
        self.assertEqual(self._ing.check_source_url(self.URL)["state"], "temp")

    def test_unknown_by_403(self):
        self._stub(lambda req: (_ for _ in ()).throw(self._http_error(403)))
        r = self._ing.check_source_url(self.URL)
        self.assertEqual((r["state"], r["code"]), ("unknown", 403))   # 자동 확정 금지 · 사람 판단

    def test_ok_on_clean_200(self):
        self._stub(lambda req: _FakeResp("<html><body>정상 글 본문</body></html>".encode("utf-8"), 200))
        r = self._ing.check_source_url(self.URL)
        self.assertEqual(r["state"], "ok")
        self.assertEqual(r["sign"], "")

    def test_bad_url_rejected_without_fetch(self):
        # 검증 실패는 fetch 전에 반환(스텁 없이도 외부 요청 없음)
        self._ing._validate_public_url = self._orig_validate
        r = self._ing.check_source_url("ftp://example.com/a")
        self.assertFalse(r["ok"])
        self.assertIn("http", r["error"])


if __name__ == "__main__":
    unittest.main()
