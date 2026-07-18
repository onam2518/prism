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
