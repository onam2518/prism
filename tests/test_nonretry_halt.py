"""비재시도성 실패(402 크레딧·한도, 401 인증) 시 일괄 실행 조기 중단 + 실패 집계.

배경(감사 2026-08-04 · 실측 2026-07-29 402 832건): 크레딧이 마른 상태에서 일괄 실행이
끝까지 돌며 전건 실패를 '완료' 성공 잡으로 집계했다 — LLM 콜 실패는 trace.fails 로만
전달되고 결과에 error 키가 없어(res.get("error") 만 실패로 셈) done 으로 잡혔고,
비용도 0 이라 예산 상한도 안 걸렸다. ratelimit.classify_http_error 가 402 를
billing(크레딧)·quota(지출 한도)로 분류하며 '재실행해도 계속 실패'라고 못박았으므로,
배치 루프가 이 분류를 소비해 즉시 중단하고 폴백 체인도 예비 모델 시도를 생략한다.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import json as _j
import os
import sys
import tempfile
import time as _t
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeLLM:
    def __init__(self, model, mock=False):
        self.model = model
        self.mock = mock


def _fail_out(kind, cost=0.0):
    """콜 실패만 있는 산출(에러 키 없음 · run_pipeline 의 실제 402 배치 모양)."""
    return {"output": {"item_meta": {}, "quality_meta": {},
                       "trace": {"cost_usd": cost,
                                 "fails": [{"tag": "quality", "kind": kind,
                                            "detail": "HTTP402: insufficient_balance"}]}}}


class HaltBase(unittest.TestCase):
    def _serve_with_rows(self, n):
        from prism import serve
        from prism.store import Store, content_hash
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        serve._INGEST_STATE.clear()
        self.addCleanup(serve._INGEST_STATE.clear)
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)
        c = st._conn()
        for i in range(n):
            content = {"displayServiceName": "뉴스", "title": "t%d" % i, "subtitle": "", "body": "b%d" % i}
            ch = content_hash(content)
            payload = {"quality_meta": {"finalGrade": "G"}, "item_meta": {"summary": "s"},
                       "trace": {"model": "m"}, "content_ref": dict(content)}
            c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,payload,created_at) "
                      "VALUES(?,?,?,?,?,?)", (ch, "뉴스", "t%d" % i, "G", _j.dumps(payload), _t.time() + i))
        c.commit()
        return serve

    def _isolate_cfg(self, payload):
        from prism import config as C
        p = os.path.join(tempfile.mkdtemp(), "config.json")
        open(p, "w", encoding="utf-8").write(_j.dumps(payload))
        orig = C.DEFAULT_CONFIG_PATH
        C.DEFAULT_CONFIG_PATH = p
        self.addCleanup(lambda: setattr(C, "DEFAULT_CONFIG_PATH", orig))

    def _stub_rerun(self, serve, fn):
        orig = serve.rerun_content
        serve.rerun_content = fn
        self.addCleanup(lambda: setattr(serve, "rerun_content", orig))

    def _batch_job(self, serve):
        return [j for j in serve.ingest_status()["jobs"] if j["kind"] == "일괄 실행"][0]


class TestBatchHaltsOnNonRetryable(HaltBase):
    def test_billing_failure_stops_batch_and_counts_failed(self):
        serve = self._serve_with_rows(5)
        self._isolate_cfg({})
        calls = []
        self._stub_rerun(serve, lambda ch, model, team=None, row=None, **kw:
                         (calls.append(ch) or _fail_out("billing")))
        r = serve.rerun_all("m2", scope="all")
        self.assertEqual(len(calls), 1)                     # 첫 건 감지 즉시 중단(호출 증폭 방지)
        self.assertEqual((r["done"], r["failed"]), (0, 1))  # '완료'로 속이지 않는다
        self.assertEqual(r["halt_kinds"], ["billing"])
        self.assertEqual(r["skipped"], 4)
        self.assertIn("크레딧 부족", r.get("msg", ""))
        j = self._batch_job(serve)
        self.assertFalse(j["last_ok"])                      # 잡 결과 = 실패
        self.assertEqual(j["failed"], 1)
        self.assertIn("크레딧 부족", j["last_msg"])
        self.assertIn("중단", j["last_msg"])

    def test_quota_and_auth_also_halt(self):
        for kind, label in (("quota", "프로젝트 지출 한도"), ("auth", "인증 오류")):
            serve = self._serve_with_rows(3)
            self._isolate_cfg({})
            self._stub_rerun(serve, lambda ch, model, team=None, row=None, **kw: _fail_out(kind))
            r = serve.rerun_all("m2", scope="all")
            self.assertEqual(r["halt_kinds"], [kind])
            self.assertIn(label, self._batch_job(serve)["last_msg"])

    def test_retryable_failures_do_not_halt(self):
        """일시 오류(parse_empty·api 등)는 종전대로 배치를 계속 돈다."""
        serve = self._serve_with_rows(4)
        self._isolate_cfg({})
        calls = []
        self._stub_rerun(serve, lambda ch, model, team=None, row=None, **kw:
                         (calls.append(ch) or {"output": {"trace": {
                             "cost_usd": 0.01,
                             "fails": [{"tag": "entities", "kind": "parse_empty"}]}}}))
        r = serve.rerun_all("m2", scope="all")
        self.assertEqual(len(calls), 4)
        self.assertEqual(r["halt_kinds"], [])
        self.assertEqual(r["done"], 4)
        self.assertTrue(self._batch_job(serve)["last_ok"])

    def test_plain_error_rows_still_continue(self):
        """error 키 실패(모델 호출 불가 등)는 종전 동작 유지 — 실패 집계 후 계속."""
        serve = self._serve_with_rows(3)
        self._isolate_cfg({})
        calls = []
        self._stub_rerun(serve, lambda ch, model, team=None, row=None, **kw:
                         (calls.append(ch) or {"error": "모델 호출 불가"}))
        r = serve.rerun_all("m2", scope="all")
        self.assertEqual(len(calls), 3)
        self.assertEqual((r["done"], r["failed"]), (0, 3))
        self.assertEqual(r["halt_kinds"], [])


class TestFallbackSkipsNonRetryable(unittest.TestCase):
    """폴백 체인: 주 모델이 크레딧·한도·인증으로 죽었으면 예비 모델도 같은 402 로 죽는다
    (같은 라우터 키) — 무의미한 호출 증폭(건당 최대 3배)을 막기 위해 폴백을 생략한다."""

    def _isolate_cfg(self, payload):
        from prism import config as C
        p = os.path.join(tempfile.mkdtemp(), "config.json")
        open(p, "w", encoding="utf-8").write(_j.dumps(payload))
        orig = C.DEFAULT_CONFIG_PATH
        C.DEFAULT_CONFIG_PATH = p
        self.addCleanup(lambda: setattr(C, "DEFAULT_CONFIG_PATH", orig))

    def _run(self, serve, fail_kind):
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))   # 원장 기록 격리
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)
        calls = []

        def fake_extract(content, llm, legal=False):
            calls.append(getattr(llm, "model", ""))
            return {"item_meta": {}, "quality_meta": {},
                    "trace": {"model": llm.model,
                              "fails": [{"tag": "quality", "kind": fail_kind}]}}

        o_ext, o_make, o_for = serve.PIPE.extract, serve.make_text_llm, serve.llm_for_model
        serve.PIPE.extract = fake_extract
        serve.make_text_llm = lambda cfg, mock: _FakeLLM("primary-m")
        serve.llm_for_model = lambda m, mock: (_FakeLLM(m), "fake")
        self.addCleanup(lambda: (setattr(serve.PIPE, "extract", o_ext),
                                 setattr(serve, "make_text_llm", o_make),
                                 setattr(serve, "llm_for_model", o_for)))
        serve.run_pipeline({"displayServiceName": "뉴스", "title": "t", "subtitle": "", "body": "b"},
                           mock=False, persist=False)
        return calls

    def test_billing_empty_output_skips_fallback(self):
        from prism import serve
        self._isolate_cfg({"fallback_models": ["backup-m"]})
        self.assertEqual(self._run(serve, "billing"), ["primary-m"])   # 예비 모델 미시도

    def test_transient_empty_output_still_falls_back(self):
        from prism import serve
        self._isolate_cfg({"fallback_models": ["backup-m"]})
        self.assertEqual(self._run(serve, "parse_empty"), ["primary-m", "backup-m"])


if __name__ == "__main__":
    unittest.main()
