"""모델 새로고침(/models) · 라우터(Timely) 실목록 합류.

화면 카탈로그(app-02 modelCatalog)는 스냅샷이라 그 뒤 나온 모델을 못 골랐다(평가 기준 등).
/models 가 라우터 실목록을 함께 실어 보내고, 조회 실패는 새로고침 전체를 막지 않는다."""
import io
import json
import os
import unittest
import urllib.error
import urllib.request
from unittest import mock

from prism import serve as SV


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")
        self.status = 200

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _KeyEnv(unittest.TestCase):
    KEYS = ("UPSTAGE_API_KEY", "PRISM_API_KEY", "PRISM_BIZROUTER_KEY", "PRISM_ROUTER_KEY", "PRISM_TIMELY_KEY")

    def _keys(self, **on):
        for k in self.KEYS:
            orig = os.environ.get(k)
            self.addCleanup(lambda k=k, v=orig: (os.environ.pop(k, None) if v is None
                                                 else os.environ.__setitem__(k, v)))
            os.environ.pop(k, None)
        for k, v in on.items():
            os.environ[k] = v


class TestRouterModels(_KeyEnv):
    def test_no_key_is_empty(self):
        self._keys()
        self.assertEqual(SV._router_models(), {})

    def test_live_ids_sorted_and_non_text_filtered(self):
        self._keys(PRISM_TIMELY_KEY="t-key")
        seen = {}

        def fake(req, timeout=0):
            seen["url"] = req.full_url
            seen["auth"] = req.get_header("Authorization")
            return _Resp({"data": [{"id": "solar-pro4", "name": "Upstage: Solar Pro 4"},
                                   {"id": "claude-opus-5"}, {"id": "text-embedding-3-large"},
                                   {"id": "gpt-image-1"}, {"id": "whisper-1"}, {"id": ""}]})
        with mock.patch.object(urllib.request, "urlopen", fake):
            out = SV._router_models()
        self.assertEqual(out, {"timely": ["claude-opus-5", "solar-pro4"]})
        self.assertTrue(seen["url"].endswith("/v1/models"))
        self.assertEqual(seen["auth"], "Bearer t-key")

    def test_http_error_keeps_refresh_alive(self):
        """키 만료(401) 같은 실패는 빈 목록 + 사유 · 예외를 화면까지 올리지 않는다."""
        self._keys(PRISM_TIMELY_KEY="t-key")

        def boom(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, io.BytesIO(b""))
        with mock.patch.object(urllib.request, "urlopen", boom):
            out = SV._router_models()
        self.assertEqual(out["timely"], [])
        self.assertIn("401", out["detail"])

    def test_list_models_carries_router_even_without_solar_key(self):
        """Solar 키가 없어도(ok=false) 라우터 실목록은 실린다 — 라우터만 쓰는 팀도 새로고침이 된다."""
        self._keys(PRISM_TIMELY_KEY="t-key")
        with mock.patch.object(urllib.request, "urlopen", lambda req, timeout=0: _Resp({"data": [{"id": "glm-5.3"}]})):
            out = SV.list_models()
        self.assertFalse(out["ok"])
        self.assertEqual(out["models"], [])
        self.assertEqual(out["router"], {"timely": ["glm-5.3"]})


if __name__ == "__main__":
    unittest.main()
