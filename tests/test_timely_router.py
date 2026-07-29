"""Timely 라우터 이전(2026-07-29): 스테이징 → 운영(api.timelyrouter.ai).

모델 public id 형식(bare)·요청 규격(max_tokens·response_format)은 그대로라 주소와 키만
바뀐다. 실호출로 확인함: claude-opus-4-8 · solar-open2 모두 200, response_format 동작.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import imagext as IMG
from prism import modelmeta as MM


class BaseUrlTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("PRISM_TIMELY_BASE", None)

    def test_default_is_production_router(self):
        self.assertEqual(IMG.router_chat_url("timely"),
                         "https://api.timelyrouter.ai/v1/chat/completions")

    def test_models_url_derived_from_same_base(self):
        self.assertEqual(IMG.router_models_url("timely"),
                         "https://api.timelyrouter.ai/v1/models")

    def test_env_override_wins(self):
        """주소가 또 바뀌거나 되돌려야 할 때 배포 없이 시크릿만으로 전환한다."""
        os.environ["PRISM_TIMELY_BASE"] = "https://router.stg.timelyai.io/v1"
        self.assertEqual(IMG.router_chat_url("timely"),
                         "https://router.stg.timelyai.io/v1/chat/completions")

    def test_blank_override_falls_back_to_default(self):
        os.environ["PRISM_TIMELY_BASE"] = "   "
        self.assertIn("api.timelyrouter.ai", IMG.router_chat_url("timely"))

    def test_bizrouter_untouched(self):
        self.assertIn("bizrouter.ai", IMG.router_chat_url("bizrouter"))


class CatalogTest(unittest.TestCase):
    """라우터 GET /v1/models 실목록(26종)과 어긋나면 처음 고르는 모델이 원본 id 로 보인다."""

    def test_current_production_model_still_listed(self):
        self.assertIn("claude-opus-4-8", MM.KNOWN_ROUTER_MODELS)

    def test_solar_models_available_through_router(self):
        for m in ("solar-pro3", "solar-open2", "solar-pro2"):
            self.assertIn(m, MM.KNOWN_ROUTER_MODELS, m)

    def test_removed_model_is_gone(self):
        """deepseek-chat 은 새 라우터 목록에 없다 — 고를 수 있으면 404 로 실패한다."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "prism", "vendor", "app-02-_afterverdict.js"),
                  encoding="utf-8") as f:
            src = f.read()
        i = src.index("timely: {")
        self.assertNotIn("'deepseek-chat'", src[i:i + 900])

    def test_new_models_get_readable_names(self):
        for mid, want in (("solar-open2", "Solar Open 2"), ("claude-opus-5", "Claude Opus 5"),
                          ("gpt-5.6-sol", "GPT-5.6 Sol"), ("glm-5.2", "GLM 5.2"),
                          ("kimi-k3", "Kimi K3")):
            self.assertEqual(MM.label(mid), want, mid)

    def test_new_families_resolve(self):
        self.assertEqual(MM.family("kimi-k3"), "moonshot")
        self.assertEqual(MM.family("glm-5.2"), "zhipu")
        self.assertEqual(MM.family("solar-open2"), "upstage")


if __name__ == "__main__":
    unittest.main()
