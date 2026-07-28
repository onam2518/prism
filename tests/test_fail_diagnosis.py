"""실패 원인 진단: 예외 종류 세분화 + 원문(detail) 보존.

배경(2026-07-28): 운영에서 item_entities 콜이 '연결 실패'로 떴는데, 같은 콘텐츠·같은
모델을 다시 불러 보면 1.5초에 성공했다. 원인을 좁히려 해도 남은 정보가 kind='network'
하나뿐이라 타임아웃인지 연결 끊김인지 응답 형식 문제인지 가릴 수 없었다.
→ 비-HTTP 예외를 timeout·network·bad_response·unknown 으로 나누고, 예외 원문을
LLMResult.fail_detail → trace.fails[].detail → 실패 원장 recent[].details 로 흘린다.
"""
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestClassifyExc(unittest.TestCase):
    def test_timeout_direct_and_wrapped(self):
        from prism.llm import classify_exc
        self.assertEqual(classify_exc(socket.timeout("read timed out")), "timeout")
        self.assertEqual(classify_exc(TimeoutError("timed out")), "timeout")
        # 연결 단계 타임아웃은 URLError 안에 담겨 온다
        self.assertEqual(classify_exc(urllib.error.URLError(socket.timeout("t"))), "timeout")

    def test_connection_problems_are_network(self):
        from prism.llm import classify_exc
        self.assertEqual(classify_exc(urllib.error.URLError("dns fail")), "network")
        self.assertEqual(classify_exc(ConnectionResetError("reset by peer")), "network")

    def test_bad_envelope_is_not_network(self):
        from prism.llm import ResponseError, classify_exc
        self.assertEqual(classify_exc(ResponseError("비 JSON 응답")), "bad_response")

    def test_unknown_fallback(self):
        from prism.llm import classify_exc
        self.assertEqual(classify_exc(ValueError("뭔가 다른 것")), "unknown")


class TestFailDetailFlows(unittest.TestCase):
    """예외 원문이 결과 객체 → 트레이스 → 원장까지 끊기지 않고 흐르는지."""

    def test_llm_result_carries_detail(self):
        from prism.llm import LLMClient
        c = LLMClient(model="m", api_key="k", mock=False)
        obj, res = c._fail("timeout", "TimeoutError: read timed out", 4, tag="item_entities")
        self.assertEqual(res.fail_kind, "timeout")
        self.assertIn("read timed out", res.fail_detail)
        self.assertEqual(obj["_fail_kind"], "timeout")

    def test_detail_truncated(self):
        from prism.llm import LLMClient
        c = LLMClient(model="m", api_key="k", mock=False)
        _, res = c._fail("network", "x" * 900, 0)
        self.assertLessEqual(len(res.fail_detail), 300)

    def test_rollup_keeps_details(self):
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        tr = {"model": "solar-pro3-260323",
              "fails": [{"tag": "item_entities", "kind": "timeout",
                         "detail": "TimeoutError: The read operation timed out"}]}
        serve._log_fail_rollup(tr, service="티스토리", team=None,
                               content_hash="h1", title="페루 - 푸노")
        rec = serve.fail_rollup_data(None, days=7)["recent"]
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]["kinds"], ["timeout"])
        self.assertEqual(len(rec[0]["details"]), 1)
        self.assertIn("item_entities:", rec[0]["details"][0])
        self.assertIn("read operation timed out", rec[0]["details"][0])

    def test_rollup_without_detail_stays_valid(self):
        """detail 이 없는 과거 기록(하위 호환)도 등재는 그대로."""
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        serve._log_fail_rollup({"model": "m", "fails": [{"tag": "item_summary", "kind": "network"}]},
                               service="s", team=None, content_hash="h2", title="t")
        rec = serve.fail_rollup_data(None, days=7)["recent"]
        self.assertEqual(rec[0]["details"], [])


class TestBadEnvelopeRaisesResponseError(unittest.TestCase):
    """HTTP 200 인데 봉투가 깨진 응답은 network 이 아니라 bad_response 로 잡힌다."""

    class _Resp:
        def __init__(self, raw):
            self._raw = raw.encode("utf-8")

        def read(self):
            return self._raw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _call_with(self, raw):
        import urllib.request
        from prism.llm import LLMClient
        c = LLMClient(model="m", api_key="k", mock=False)
        orig = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: self._Resp(raw)
        try:
            return c._call("sys", "user")
        finally:
            urllib.request.urlopen = orig

    def test_non_json_body(self):
        from prism.llm import ResponseError, classify_exc
        with self.assertRaises(ResponseError) as cm:
            self._call_with("<html>502 Bad Gateway</html>")
        self.assertEqual(classify_exc(cm.exception), "bad_response")

    def test_json_but_not_object(self):
        from prism.llm import ResponseError
        with self.assertRaises(ResponseError):
            self._call_with(json.dumps(["nope"]))

    def test_choices_shape_mismatch(self):
        from prism.llm import ResponseError
        with self.assertRaises(ResponseError):
            self._call_with(json.dumps({"choices": [{"text": "구형 봉투"}]}))

    def test_valid_envelope_still_works(self):
        raw = json.dumps({"choices": [{"message": {"content": '{"entities": ["푸노"]}'}}],
                          "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        res = self._call_with(raw)
        self.assertIn("푸노", res.text)
        self.assertEqual(res.in_tok, 10)


class TestUiSurfacesDetail(unittest.TestCase):
    def _src(self, rel):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            return f.read()

    def test_new_kinds_have_korean_labels(self):
        src = self._src("prism/vendor/app-05-costdata.js")
        for k in ("timeout:", "bad_response:"):
            self.assertIn(k, src)
        self.assertIn("failTip(f, k)", src)

    def test_table_tooltip_uses_detail(self):
        from prism import page
        self.assertIn('x-bind:data-tip="failTip(f, k)"', page.PAGE)


if __name__ == "__main__":
    unittest.main()
