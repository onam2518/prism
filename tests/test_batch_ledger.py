"""엑셀 일괄 추출·자동 인입 경로도 비용·실패 원장에 기록된다.

배경(감사 2026-08-04): _log_cost_rollup/_log_fail_rollup 은 run_pipeline 에서만 호출됐는데,
run_batch(엑셀 일괄 추출)와 ingest_run_source(5분 간격 자동 폴링)는 PIPE.extract 를 직접
불러 실키 LLM 지출이 일별 비용 롤업·AL.on_cost 임계 알림에서 전부 빠졌고, 이 경로의 콜
실패(402 크레딧 소진 포함)는 실패 원장·'최근 실패 콘텐츠' 목록에 한 건도 안 남았다 —
크레딧이 마른 상태로 자동 인입이 매 폴링 실패를 반복해도 트리아지 신호가 0 이었다.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import json as _j
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeLLM:
    def __init__(self, model, mock=False):
        self.model = model
        self.mock = mock


def _fail_trace(model="real-m"):
    return {"model": model, "cost_usd": 0.01, "tokens": {"in": 100, "out": 20},
            "by_call": {"summary": {"n": 1, "cost": 0.01, "in": 100, "out": 20}},
            "fails": [{"tag": "summary", "kind": "billing",
                       "detail": "HTTP402: insufficient_balance"}]}


class LedgerBase(unittest.TestCase):
    def _serve(self):
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._INGEST_STATE.clear()
        orig_mock = serve.Handler.server_mock
        serve.Handler.server_mock = True                  # 외부 부수효과(사전 보강 등) 차단
        self.addCleanup(serve._INGEST_STATE.clear)
        self.addCleanup(lambda: setattr(serve.Handler, "server_mock", orig_mock))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)
        return serve

    def _isolate_cfg(self, payload):
        from prism import config as C
        p = os.path.join(tempfile.mkdtemp(), "config.json")
        open(p, "w", encoding="utf-8").write(_j.dumps(payload))
        orig = C.DEFAULT_CONFIG_PATH
        C.DEFAULT_CONFIG_PATH = p
        self.addCleanup(lambda: setattr(C, "DEFAULT_CONFIG_PATH", orig))

    def _stub_llm(self, serve, mock=False):
        """실키 흉내(mock=False): 원장은 실호출만 기록한다."""
        orig = serve.make_text_llm
        serve.make_text_llm = lambda cfg, m: _FakeLLM("real-m", mock=mock)
        self.addCleanup(lambda: setattr(serve, "make_text_llm", orig))

    def _stub_extract(self, serve, trace):
        calls = []

        def fake(content, llm, legal=False):
            calls.append(content.get("title"))
            return {"item_meta": {"summary": "s"}, "quality_meta": {"finalGrade": "G"},
                    "trace": dict(trace)}
        orig = serve.PIPE.extract
        serve.PIPE.extract = fake
        self.addCleanup(lambda: setattr(serve.PIPE, "extract", orig))
        return calls


class TestExcelBatchLedger(LedgerBase):
    CSV = ("콘텐츠 그룹,제목,본문\n"
           "뉴스,기사 하나,본문1\n"
           "뉴스,기사 둘,본문2\n").encode("utf-8")

    def test_real_key_batch_records_cost_and_fails(self):
        serve = self._serve()
        self._isolate_cfg({})
        self._stub_llm(serve, mock=False)
        self._stub_extract(serve, _fail_trace())
        r = serve.run_batch(self.CSV, "u.csv")
        self.assertEqual(r["count"], 2)
        cost = serve.cost_rollup_data(None, days=7)
        self.assertEqual(cost["total"]["n"], 2)             # 건별 비용 누적
        self.assertAlmostEqual(cost["total"]["cost"], 0.02)
        fail = serve.fail_rollup_data(None, days=7)
        self.assertEqual(fail["total"], 2)                  # 402 실패도 원장 등재
        kinds = {x["k"]: x["n"] for x in fail["by_kind"]}
        self.assertEqual(kinds["billing"], 2)
        self.assertEqual(len(fail["recent"]), 2)            # 최근 실패 콘텐츠(개별 재실행 대상)
        self.assertEqual({e["title"] for e in fail["recent"]}, {"기사 하나", "기사 둘"})

    def test_mock_batch_records_nothing(self):
        """mock 실행은 종전대로 원장 무기록(테스트·데모 지출 왜곡 방지)."""
        serve = self._serve()
        self._isolate_cfg({})
        self._stub_llm(serve, mock=True)
        self._stub_extract(serve, _fail_trace())
        serve.run_batch(self.CSV, "u.csv")
        self.assertEqual(serve.cost_rollup_data(None, days=7)["total"]["n"], 0)
        self.assertEqual(serve.fail_rollup_data(None, days=7)["total"], 0)


class TestAutoIngestLedger(LedgerBase):
    def _stub_fetch(self, rows):
        import prism.ingestops as IG
        orig = IG._fetch_records
        IG._fetch_records = lambda endpoint, limit, method, auth: (rows, None)
        self.addCleanup(lambda: setattr(IG, "_fetch_records", orig))

    def test_polling_ingest_records_cost_and_fails(self):
        serve = self._serve()
        self._isolate_cfg({})
        self._stub_llm(serve, mock=False)
        self._stub_extract(serve, _fail_trace())
        self._stub_fetch([{"제목": "폴링 기사", "본문": "본문", "콘텐츠 그룹": "뉴스"}])
        r = serve.ingest_run_source({"id": "s1", "name": "소스", "endpoint": "https://api.example.com/x"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(serve.cost_rollup_data(None, days=7)["total"]["n"], 1)
        fail = serve.fail_rollup_data(None, days=7)
        self.assertEqual(fail["total"], 1)
        self.assertEqual([e["title"] for e in fail["recent"]], ["폴링 기사"])

    def test_mock_polling_records_nothing(self):
        serve = self._serve()
        self._isolate_cfg({})
        self._stub_llm(serve, mock=True)
        self._stub_extract(serve, _fail_trace())
        self._stub_fetch([{"제목": "폴링 기사", "본문": "본문"}])
        self.assertTrue(serve.ingest_run_source({"id": "s2", "endpoint": "https://api.example.com/x"})["ok"])
        self.assertEqual(serve.cost_rollup_data(None, days=7)["total"]["n"], 0)
        self.assertEqual(serve.fail_rollup_data(None, days=7)["total"], 0)


if __name__ == "__main__":
    unittest.main()
