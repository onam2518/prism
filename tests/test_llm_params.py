"""라우터 경유 모델별 파라미터 협상 회귀.

배경(2026-07-07 실측 · Timely 라우터):
 · gpt-5.4: max_tokens 미지원(라우터가 기본값 주입) → "Use 'max_completion_tokens'" 400
 · gpt-5.4 + reasoning_effort: temperature=0 거부("Only the default (1) value is supported") 400
 · claude-*: response_format json_object 거부("Input should be 'json_schema'") 400
→ 전 콜 실패로 초안 메타가 전량 빈 값 저장. 수정 = 400 안내문에서 배우고 즉시 재시도,
  결과는 모델별 프로세스 캐시(_PARAM_ADAPT)로 공유해 배치에서 400 낭비 방지.

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


def _http400(msg: bytes):
    return urllib.error.HTTPError("http://x", 400, "Bad Request", None, io.BytesIO(msg))


_MAX_TOKENS_400 = (b'{"error":{"code":"unsupported_parameter","message":"Unsupported parameter: '
                   b"'max_tokens' is not supported with this model. Use 'max_completion_tokens' "
                   b'instead.","param":"max_tokens","type":"invalid_request_error"}}')
_TEMPERATURE_400 = (b'{"error":{"code":"unsupported_value","message":"Unsupported value: '
                    b"'temperature' does not support 0 with this model. Only the default (1) "
                    b'value is supported.","param":"temperature","type":"invalid_request_error"}}')
_RESPONSE_FORMAT_400 = (b'{"error":{"code":"invalid_request_error","message":"response_format.type: '
                        b'Input should be \'json_schema\'","param":null,"type":"invalid_request_error"}}')


class TestParamNegotiation(unittest.TestCase):
    def setUp(self):
        from prism.llm import LLMClient
        LLMClient._PARAM_ADAPT.clear()                 # 프로세스 캐시 격리(테스트 간 오염 방지)

    def _client(self, model):
        from prism.config import Config
        from prism.llm import LLMClient
        cfg = Config()                                 # 로컬 설정 파일 오염 방지(Config.load 회피)
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
        sent, obj, _ = self._run(self._client("gpt-5.4"), [_ok_payload()])
        self.assertEqual(len(sent), 1)
        self.assertIn("max_completion_tokens", sent[0])
        self.assertNotIn("max_tokens", sent[0])
        self.assertEqual(obj, {"ok": True})

    def test_adapts_max_tokens_guidance_on_400(self):
        c = self._client("claude-opus-4-8")
        sent, obj, res = self._run(c, [_http400(_MAX_TOKENS_400), _ok_payload()])
        self.assertEqual(len(sent), 2)                       # 400 → 전환 → 즉시 재시도
        self.assertNotIn("max_completion_tokens", sent[0])   # 최초엔 미전송(기존 계약 유지)
        self.assertIn("max_completion_tokens", sent[1])
        self.assertEqual(obj, {"ok": True})
        self.assertEqual(res.retries, 1)

    def test_adapts_temperature_rejection_on_400(self):
        c = self._client("gpt-5.4")
        sent, obj, _ = self._run(c, [_http400(_TEMPERATURE_400), _ok_payload()])
        self.assertEqual(len(sent), 2)
        self.assertIn("temperature", sent[0])
        self.assertNotIn("temperature", sent[1])             # 거부 학습 후 생략
        self.assertEqual(obj, {"ok": True})

    def test_adapts_temperature_deprecated_variant(self):
        # claude 계열의 다른 표현: "`temperature` is deprecated for this model." (2026-07-07 실측)
        c = self._client("claude-opus-4-8")
        err = _http400(b'{"error":{"code":"invalid_request_error","message":"`temperature` is '
                       b'deprecated for this model.","param":null,"type":"invalid_request_error"}}')
        sent, obj, _ = self._run(c, [err, _ok_payload()])
        self.assertEqual(len(sent), 2)
        self.assertNotIn("temperature", sent[1])
        self.assertEqual(obj, {"ok": True})

    def test_adapts_response_format_rejection_on_400(self):
        c = self._client("claude-opus-4-8")
        sent, obj, _ = self._run(c, [_http400(_RESPONSE_FORMAT_400), _ok_payload()])
        self.assertEqual(len(sent), 2)
        self.assertIn("response_format", sent[0])
        self.assertNotIn("response_format", sent[1])         # 생략 · JSON 은 프롬프트 계약+파서 복구
        self.assertEqual(obj, {"ok": True})

    def test_adaptation_shared_across_clients_of_same_model(self):
        c1 = self._client("claude-opus-4-8")
        self._run(c1, [_http400(_RESPONSE_FORMAT_400), _ok_payload()])
        c2 = self._client("claude-opus-4-8")                 # 새 인스턴스(배치의 다음 아이템)
        sent, obj, _ = self._run(c2, [_ok_payload()])
        self.assertEqual(len(sent), 1)                       # 재학습 없이 곧장 성공(400 낭비 없음)
        self.assertNotIn("response_format", sent[0])
        c3 = self._client("solar-pro2")                      # 다른 모델은 캐시 무영향
        sent3, _, _ = self._run(c3, [_ok_payload()])
        self.assertIn("response_format", sent3[0])

    def test_unrelated_http400_still_fails_without_adaptation(self):
        c = self._client("solar-pro2")
        err = _http400(b'{"error":{"message":"content filter"}}')
        sent, obj, _ = self._run(c, [err])
        self.assertEqual(len(sent), 1)                       # 안내문 없으면 비재시도 유지
        self.assertIn("_fail", obj)

    def test_gemini_omits_response_format_upfront(self):
        # gemini 계열: 라우터가 response_format json_object 를 400 아닌 '침묵 빈응답'으로 돌려줘
        # 자동학습(400 안내문)이 안 걸린다 → 계열 판정으로 선제 시드(첫 콜부터 response_format 생략).
        for model in ("gemini-2.5-pro", "gemini-1.5-pro", "google/gemini-2.5-flash"):
            self.setUp()                                     # 캐시 격리
            sent, obj, _ = self._run(self._client(model), [_ok_payload()])
            self.assertEqual(len(sent), 1, model)
            self.assertNotIn("response_format", sent[0], model)
            self.assertEqual(obj, {"ok": True})

    def test_gemini_seed_does_not_leak_to_other_families(self):
        # 회귀 가드: gemini 선제 시드가 gpt/claude 첫 콜엔 영향 없어야(response_format 정상 전송)
        for model in ("gpt-5.4", "claude-opus-4-8"):
            self.setUp()
            sent, _, _ = self._run(self._client(model), [_ok_payload()])
            self.assertIn("response_format", sent[0], model)


if __name__ == "__main__":
    unittest.main()
