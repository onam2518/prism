"""폴백 체인 + 일괄 실행 예산 상한: 빈 산출 구제와 비용 통제 가드레일.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
계약: 실호출 산출이 전량 빈값이면 Config.fallback_models 순서로 1회씩 재시도(성공 채택 ·
trace.fallback_from 표기) · rerun_all 은 batch_budget_usd(0=무제한) 도달 시 남은 대상 중단.
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


class TestFallbackChain(unittest.TestCase):
    def _isolate_cfg(self, payload):
        from prism import config as C
        p = os.path.join(tempfile.mkdtemp(), "config.json")
        open(p, "w", encoding="utf-8").write(_j.dumps(payload))
        orig = C.DEFAULT_CONFIG_PATH
        C.DEFAULT_CONFIG_PATH = p
        self.addCleanup(lambda: setattr(C, "DEFAULT_CONFIG_PATH", orig))

    def test_pipeline_empty_detector(self):
        from prism import serve
        self.assertTrue(serve._pipeline_empty({"item_meta": {}, "quality_meta": {}}))
        self.assertFalse(serve._pipeline_empty({"item_meta": {"summary": "s"}, "quality_meta": {}}))
        self.assertFalse(serve._pipeline_empty({"item_meta": {}, "quality_meta": {"finalGrade": "G"}}))

    def test_empty_output_retries_with_fallback(self):
        from prism import serve
        self._isolate_cfg({"fallback_models": ["backup-m"]})
        calls = []

        def fake_extract(content, llm, legal=False):
            calls.append(getattr(llm, "model", ""))
            if len(calls) == 1:                       # 1차(주 모델): 전량 빈값
                return {"item_meta": {}, "quality_meta": {}, "trace": {"model": llm.model}}
            return {"item_meta": {"summary": "복구됨"}, "quality_meta": {"finalGrade": "G"},
                    "trace": {"model": llm.model}}

        o_ext, o_make, o_for = serve.PIPE.extract, serve.make_text_llm, serve.llm_for_model
        serve.PIPE.extract = fake_extract
        serve.make_text_llm = lambda cfg, mock: _FakeLLM("primary-m")
        serve.llm_for_model = lambda m, mock: (_FakeLLM(m), "fake")
        self.addCleanup(lambda: (setattr(serve.PIPE, "extract", o_ext),
                                 setattr(serve, "make_text_llm", o_make),
                                 setattr(serve, "llm_for_model", o_for)))
        res = serve.run_pipeline({"displayServiceName": "뉴스", "title": "t", "subtitle": "", "body": "b"},
                                 mock=False, persist=False)
        out = res.get("output") or {}
        self.assertEqual(calls, ["primary-m", "backup-m"])   # 주 모델 → 폴백 1회
        self.assertEqual((out.get("item_meta") or {}).get("summary"), "복구됨")
        self.assertEqual((out.get("trace") or {}).get("fallback_from"), "primary-m")

    def test_nonempty_output_skips_fallback(self):
        from prism import serve
        self._isolate_cfg({"fallback_models": ["backup-m"]})
        calls = []

        def fake_extract(content, llm, legal=False):
            calls.append(getattr(llm, "model", ""))
            return {"item_meta": {"summary": "정상"}, "quality_meta": {"finalGrade": "G"}, "trace": {}}

        o_ext, o_make = serve.PIPE.extract, serve.make_text_llm
        serve.PIPE.extract = fake_extract
        serve.make_text_llm = lambda cfg, mock: _FakeLLM("primary-m")
        self.addCleanup(lambda: (setattr(serve.PIPE, "extract", o_ext),
                                 setattr(serve, "make_text_llm", o_make)))
        serve.run_pipeline({"displayServiceName": "뉴스", "title": "t", "subtitle": "", "body": "b"},
                           mock=False, persist=False)
        self.assertEqual(calls, ["primary-m"])               # 폴백 미발동


class TestBatchBudget(unittest.TestCase):
    def _serve_with_rows(self, n):
        from prism import serve
        from prism.store import Store, content_hash
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
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

    def test_budget_stops_batch(self):
        serve = self._serve_with_rows(5)
        self._isolate_cfg({"batch_budget_usd": 0.12})
        orig = serve.rerun_content
        serve.rerun_content = lambda ch, model, team=None, row=None, force_quest=False: {
            "output": {"trace": {"cost_usd": 0.05}}}
        self.addCleanup(lambda: setattr(serve, "rerun_content", orig))
        r = serve.rerun_all("m2", scope="all")
        self.assertTrue(r["budget_stop"])
        self.assertEqual(r["done"], 3)                       # 0.15 >= 0.12 → 3건 후 중단
        self.assertEqual(r["skipped"], 2)
        self.assertAlmostEqual(r["spent_usd"], 0.15)

    def test_no_budget_runs_all(self):
        serve = self._serve_with_rows(4)
        self._isolate_cfg({})                                # 0 = 무제한
        orig = serve.rerun_content
        serve.rerun_content = lambda ch, model, team=None, row=None, force_quest=False: {
            "output": {"trace": {"cost_usd": 0.05}}}
        self.addCleanup(lambda: setattr(serve, "rerun_content", orig))
        r = serve.rerun_all("m2", scope="all")
        self.assertFalse(r["budget_stop"])
        self.assertEqual(r["done"], 4)


if __name__ == "__main__":
    unittest.main()
