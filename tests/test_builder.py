"""선언형 프롬프트 빌더(builder_compile · Atelier step1 이식): 게이트·컴파일·빈 결과."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _StubResult:
    cost_usd = 0.00123


class TestBuilderCompile(unittest.TestCase):
    def _with_serve(self, payload=None):
        import tempfile
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self._orig_mock = serve.Handler.server_mock
        serve.Handler.server_mock = True
        self.addCleanup(lambda: (setattr(serve, "_STORE", None),
                                 setattr(serve.Handler, "server_mock", self._orig_mock)))
        if payload is not None:
            orig = serve.make_text_llm

            class _Stub:
                def complete_json(self, system, user, tag=""):
                    return payload, _StubResult()
            serve.make_text_llm = lambda cfg, mock: _Stub()
            self.addCleanup(lambda: setattr(serve, "make_text_llm", orig))
        return serve

    def test_requires_task_and_families(self):
        serve = self._with_serve()
        r = serve.builder_compile({"task": "", "families": ["solar"]})
        self.assertFalse(r.get("ok"))
        self.assertIn("과업", r.get("error", ""))
        r = serve.builder_compile({"task": "분류기", "families": ["없는계열"]})
        self.assertFalse(r.get("ok"))
        self.assertIn("계열", r.get("error", ""))

    def test_compile_per_family(self):
        serve = self._with_serve(payload={
            "prompts": {"solar": "솔라용 시스템", "gpt": "GPT용 시스템"},
            "notes": "솔라는 한국어 우선"})
        r = serve.builder_compile({"task": "커머스 등급 분류", "role": "전문가",
                                   "families": ["solar", "gpt"]})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["prompts"]["solar"], "솔라용 시스템")
        self.assertEqual(r["prompts"]["gpt"], "GPT용 시스템")
        self.assertEqual(r["families"], ["solar", "gpt"])
        self.assertIn("한국어", r["notes"])
        self.assertEqual(r["cost_usd"], 0.00123)

    def test_empty_result_is_error(self):
        serve = self._with_serve(payload={"finalGrade": "G"})   # 저지 형식 아님(mock 등)
        r = serve.builder_compile({"task": "분류기", "families": ["solar"]})
        self.assertFalse(r.get("ok"))
        self.assertIn("비었습니다", r.get("error", ""))


if __name__ == "__main__":
    unittest.main()


class TestBuilderTest(unittest.TestCase):
    """빌더 ④ 테스트 실행: 프롬프트·입력 게이트 · 텍스트 완성 스텁 · mock 경로."""

    def _with_serve(self, text=None):
        import tempfile
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self._orig_mock = serve.Handler.server_mock
        serve.Handler.server_mock = True
        self.addCleanup(lambda: (setattr(serve, "_STORE", None),
                                 setattr(serve.Handler, "server_mock", self._orig_mock)))
        if text is not None:
            orig = serve.make_text_llm

            class _Res:
                latency_ms = 42
                cost_usd = 0.001
                in_tok = 10
                out_tok = 20

                def __init__(self, t):
                    self.text = t

            class _Stub:
                model = "stub-model"

                def complete_text(self, system, user, tag=""):
                    return _Res(text)
            serve.make_text_llm = lambda cfg, mock: _Stub()
            self.addCleanup(lambda: setattr(serve, "make_text_llm", orig))
        return serve

    def test_gates(self):
        serve = self._with_serve()
        self.assertIn("컴파일", serve.builder_test("", "입력").get("error", ""))
        self.assertIn("샘플", serve.builder_test("시스템", "").get("error", ""))

    def test_runs_and_reports(self):
        serve = self._with_serve(text='{"finalGrade": "G"}')
        r = serve.builder_test("당신은 분류기다", "본문 샘플")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["output"], '{"finalGrade": "G"}')
        self.assertEqual(r["latency_ms"], 42)
        self.assertEqual(r["tokens"], {"in": 10, "out": 20})

    def test_mock_llm_returns_text(self):
        serve = self._with_serve()                       # 스텁 없이 실제 mock LLM 경로
        r = serve.builder_test("당신은 분류기다", "본문 샘플")
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(r["output"])                     # mock 도 텍스트 산출
