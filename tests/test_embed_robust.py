"""임베딩 클라이언트 견고성 테스트(2026-07-15 감사 P1-4·5).

- _api_embed: 429/5xx/타임아웃에 지수백오프 재시도(기존엔 방어 전무 → 일시 장애로 extract 전체 실패)
- implicit_mock: 키 부재로 인한 암묵 mock 을 명시 mock 과 구분(조용한 휴리스틱 라벨 감지)

실행: python3 -m pytest tests/test_embed_robust.py -q
"""
import json
import os
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeResp:
    def __init__(self, data):
        self._d = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self._d).encode()


class TestEmbedRetry(unittest.TestCase):
    def setUp(self):
        import prism.embed as E
        self.E = E
        self._orig_open = E.urllib.request.urlopen
        self._orig_sleep = E.time.sleep
        E.time.sleep = lambda *_a, **_k: None      # 실지연 제거

    def tearDown(self):
        self.E.urllib.request.urlopen = self._orig_open
        self.E.time.sleep = self._orig_sleep

    def test_retries_on_429_then_succeeds(self):
        E = self.E
        calls = {"n": 0}

        def fake_open(req, timeout=30):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.HTTPError(E.EMB_URL, 429, "rate", {}, None)
            return _FakeResp({"data": [{"embedding": [0.1, 0.2, 0.3]}],
                              "usage": {"total_tokens": 7}})

        E.urllib.request.urlopen = fake_open
        c = E.EmbeddingClient(api_key="k")          # 실키(비mock) 경로
        vec = c._api_embed("hello", True)
        self.assertEqual(vec, [0.1, 0.2, 0.3])
        self.assertEqual(calls["n"], 3)             # 두 번 재시도 후 성공
        self.assertEqual(c.tokens, 7)

    def test_raises_after_exhausting_retries(self):
        E = self.E

        def always_500(req, timeout=30):
            raise urllib.error.HTTPError(E.EMB_URL, 503, "down", {}, None)

        E.urllib.request.urlopen = always_500
        c = E.EmbeddingClient(api_key="k")
        with self.assertRaises(urllib.error.HTTPError):
            c._api_embed("x", False, max_retries=2)

    def test_non_retryable_4xx_raises_immediately(self):
        E = self.E
        calls = {"n": 0}

        def unauthorized(req, timeout=30):
            calls["n"] += 1
            raise urllib.error.HTTPError(E.EMB_URL, 401, "bad key", {}, None)

        E.urllib.request.urlopen = unauthorized
        c = E.EmbeddingClient(api_key="k")
        with self.assertRaises(urllib.error.HTTPError):
            c._api_embed("x", True)
        self.assertEqual(calls["n"], 1)             # 401 은 재시도 없이 즉시 전파


class TestImplicitMock(unittest.TestCase):
    def test_flags(self):
        from prism.embed import EmbeddingClient
        self.assertTrue(EmbeddingClient(mock=False, api_key="").implicit_mock)
        self.assertFalse(EmbeddingClient(mock=True).implicit_mock)
        self.assertFalse(EmbeddingClient(api_key="k").implicit_mock)


if __name__ == "__main__":
    unittest.main()
