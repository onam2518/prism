"""자동 인입·엑셀 일괄 실행의 재추출 재과금 방지 + 비재시도 조기 중단(감사 P1).

- 자동 인입(ingest_run_source)이 매 폴링마다 '최신 N건'을 통째로 다시 추출하며 LLM 을
  재호출하던 것 → 이미 실행 완료된 해시는 건너뛴다(run_batch 와 동일).
- 크레딧 소진·인증 실패(비재시도) 감지 시 자동 인입·엑셀 일괄 실행 루프도 rerun_all 처럼
  즉시 중단하고 실패로 남긴다(초록불로 속이지 않음).

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeLLM:
    def __init__(self, model="m", mock=False):
        self.model = model
        self.mock = mock


def _ok_yellow(content, llm, legal=False):
    """저장되는(검토 대상 yellow) 산출."""
    return {"item_meta": {"summary": "s"},
            "quality_meta": {"finalGrade": "R", "review": "yellow"},
            "trace": {"model": "m", "version": 1}}


def _billing(content, llm, legal=False):
    """콜 실패(크레딧)만 있는 산출 — 에러 키 없음(실제 402 배치 모양)."""
    return {"item_meta": {}, "quality_meta": {},
            "trace": {"model": "m", "fails": [{"tag": "quality", "kind": "billing"}]}}


class Base(unittest.TestCase):
    def _serve(self, mock=True):
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._INGEST_STATE.clear()
        orig_mock = serve.Handler.server_mock
        serve.Handler.server_mock = mock
        self.addCleanup(serve._INGEST_STATE.clear)
        self.addCleanup(lambda: setattr(serve.Handler, "server_mock", orig_mock))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)
        self._isolate_cfg()
        return serve

    def _isolate_cfg(self):
        from prism import config as C
        p = os.path.join(tempfile.mkdtemp(), "config.json")
        open(p, "w", encoding="utf-8").write("{}")
        orig = C.DEFAULT_CONFIG_PATH
        C.DEFAULT_CONFIG_PATH = p
        self.addCleanup(lambda: setattr(C, "DEFAULT_CONFIG_PATH", orig))

    def _stub_extract(self, serve, fn):
        calls = []

        def wrapped(content, llm, legal=False):
            calls.append(content.get("title"))
            return fn(content, llm, legal)
        o = serve.PIPE.extract
        serve.PIPE.extract = wrapped
        self.addCleanup(lambda: setattr(serve.PIPE, "extract", o))
        return calls

    def _real_llm(self, serve):
        """make_text_llm 을 비 mock 으로: llm.mock=False 라야 비재시도 감지가 산다."""
        o = serve.make_text_llm
        serve.make_text_llm = lambda cfg, mock: _FakeLLM("m", mock=False)
        self.addCleanup(lambda: setattr(serve, "make_text_llm", o))

    def _stub_fetch(self, rows):
        import prism.ingestops as IO
        o = IO._fetch_records
        IO._fetch_records = lambda endpoint, limit, method, auth: (list(rows), None)
        self.addCleanup(lambda: setattr(IO, "_fetch_records", o))

    def _job(self, serve, sid):
        for j in serve.ingest_status()["jobs"]:
            if j.get("id") == sid or j.get("endpoint") == sid:
                return j
        return serve.ingest_status()["jobs"][-1]


class TestIngestReextractSkip(Base):
    def test_second_poll_skips_already_extracted(self):
        serve = self._serve(mock=True)
        rows = [{"제목": f"글{i}", "본문": f"본문{i}"} for i in range(3)]
        self._stub_fetch(rows)
        calls = self._stub_extract(serve, _ok_yellow)
        src = {"id": "s1", "endpoint": "http://x", "name": "테스트", "limit": 100}
        serve.ingest_run_source(src)
        self.assertEqual(len(calls), 3)                     # 첫 폴링: 전건 추출
        calls.clear()
        serve.ingest_run_source(src)
        self.assertEqual(len(calls), 0)                     # 둘째 폴링: 재추출 0(재과금 방지)
        self.assertIn("건너뜀", self._job(serve, "s1")["last_msg"])

    def test_new_rows_still_extracted_on_next_poll(self):
        serve = self._serve(mock=True)
        rows = [{"제목": "글0", "본문": "본문0"}]
        holder = {"rows": rows}
        import prism.ingestops as IO
        o = IO._fetch_records
        IO._fetch_records = lambda endpoint, limit, method, auth: (list(holder["rows"]), None)
        self.addCleanup(lambda: setattr(IO, "_fetch_records", o))
        calls = self._stub_extract(serve, _ok_yellow)
        src = {"id": "s2", "endpoint": "http://y", "name": "t", "limit": 100}
        serve.ingest_run_source(src)
        self.assertEqual(len(calls), 1)
        calls.clear()
        holder["rows"] = [{"제목": "글0", "본문": "본문0"}, {"제목": "글1", "본문": "본문1"}]  # 신규 1건 추가
        serve.ingest_run_source(src)
        self.assertEqual(len(calls), 1)                     # 신규만 추출(기존 1건 건너뜀)


class TestIngestHaltsOnNonRetryable(Base):
    def test_ingest_stops_on_billing(self):
        serve = self._serve(mock=False)
        self._real_llm(serve)
        self._stub_fetch([{"제목": f"글{i}", "본문": f"b{i}"} for i in range(4)])
        calls = self._stub_extract(serve, _billing)
        r = serve.ingest_run_source({"id": "s3", "endpoint": "http://z", "name": "t", "limit": 100})
        self.assertEqual(len(calls), 1)                     # 첫 건 감지 즉시 중단
        self.assertFalse(r.get("ok") and self._job(serve, "s3")["last_ok"])
        self.assertFalse(self._job(serve, "s3")["last_ok"])


if __name__ == "__main__":
    unittest.main()
