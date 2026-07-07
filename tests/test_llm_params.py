"""신형 gpt(max_tokens 미지원) 파라미터 적응 회귀.

배경(2026-07-07): Timely 라우터가 요청에 max_tokens 기본값을 주입하는데 gpt-5 계열이
이를 거부(HTTP400 · "Use 'max_completion_tokens' instead") → 전 콜 실패로 초안 메타가
전량 빈 값으로 저장됐다. 수정 = gpt-5* 는 max_completion_tokens 사전 명시, 그 외 모델은
400 안내문을 보고 전환 후 즉시 재시도.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import io
import json
import os
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Resp:
    """urlopen 컨텍스트 매니저 대역."""
    def __init__(self, obj):
        self._b = json.dumps(obj).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ok_payload():
    return {"choices": [{"message": {"content": "{\"ok\": true}"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def _http400_max_tokens():
    msg = (b'{"error":{"code":"unsupported_parameter","message":"Unsupported parameter: '
           b"'max_tokens' is not supported with this model. Use 'max_completion_tokens' "
           b'instead.","param":"max_tokens","type":"invalid_request_error"}}')
    return urllib.error.HTTPError("http://x", 400, "Bad Request", None, io.BytesIO(msg))


class TestMaxCompletionAdaptation(unittest.TestCase):
    def _client(self, model):
        from prism.config import Config
        from prism.llm import LLMClient
        cfg = Config()                                   # 로컬 설정 파일 오염 방지(Config.load 회피)
        cfg.chat_url = "http://unit.test/v1/chat/completions"
        return LLMClient(config=cfg, api_key="test-key", model=model)

    def _run(self, client, responses):
        """urlopen 을 대역으로 바꿔 complete_json 1회 실행. 보낸 바디 목록과 결과 반환."""
        sent = []
        orig = urllib.request.urlopen

        def fake(req, timeout=None):
            sent.append(json.loads(req.data.decode("utf-8")))
            r = responses[min(len(sent) - 1, len(responses) - 1)]
            if isinstance(r, Exception):
                raise r
            return _Resp(r)

        urllib.request.urlopen = fake
        try:
            obj, res = client.complete_json("시스템", "사용자", tag="t")
        finally:
            urllib.request.urlopen = orig
        return sent, obj, res

    def test_gpt5_sends_max_completion_tokens_upfront(self):
        c = self._client("gpt-5.4")
        sent, obj, res = self._run(c, [_ok_payload()])
        self.assertEqual(len(sent), 1)
        self.assertIn("max_completion_tokens", sent[0])
        self.assertNotIn("max_tokens", sent[0])
        self.assertEqual(obj, {"ok": True})

    def test_other_model_adapts_on_http400_guidance(self):
        c = self._client("claude-opus-4-8")
        self.assertFalse(c._max_completion)
        sent, obj, res = self._run(c, [_http400_max_tokens(), _ok_payload()])
        self.assertEqual(len(sent), 2)                       # 400 → 전환 → 즉시 재시도
        self.assertNotIn("max_completion_tokens", sent[0])   # 최초엔 미전송(기존 계약 유지)
        self.assertIn("max_completion_tokens", sent[1])
        self.assertEqual(obj, {"ok": True})
        self.assertEqual(res.retries, 1)
        self.assertTrue(c._max_completion)                   # 이후 콜부터는 사전 명시

    def test_unrelated_http400_still_fails_without_adaptation(self):
        c = self._client("solar-pro2")
        err = urllib.error.HTTPError("http://x", 400, "Bad Request", None,
                                     io.BytesIO(b'{"error":{"message":"content filter"}}'))
        sent, obj, res = self._run(c, [err])
        self.assertEqual(len(sent), 1)                       # 안내문 없으면 비재시도 유지
        self.assertIn("_fail", obj)
        self.assertFalse(c._max_completion)


if __name__ == "__main__":
    unittest.main()
