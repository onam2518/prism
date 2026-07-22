"""이미지 시각 슬롯 선택(실험실 미디어) 회귀: per-call 오버라이드 · 순수 사진 라우터 폴백 ·
후보 목록 노출 · run_pipeline vision_used.

배경: 리포트(2026-07-22 · 샘플 20장)에서 기본 Upstage IE 는 순수 사진 장면 인식 0/20,
라우터 멀티모달(gemini-3.5-flash)은 20/20. 실험을 실험실 이미지 탭 기능으로 옮기며
시각 슬롯을 호출별로 고를 수 있게 하고, IE 가 순수 사진을 못 읽으면 라우터로 폴백한다.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0 · 네트워크 없음: 비전 호출 monkeypatch)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

IMG_BYTES = b"\xff\xd8\xff\xe0jpegish"
IMG = {"bytes": IMG_BYTES, "filename": "a.jpg", "mime": "image/jpeg"}


class _EnvPatch(unittest.TestCase):
    """env·imagext 함수 패치 후 원복하는 공용 베이스."""

    def _setenv(self, **kv):
        from prism import imagext
        for k, v in kv.items():
            old = os.environ.get(k)
            self.addCleanup(lambda k=k, old=old: (os.environ.__setitem__(k, old)
                                                   if old is not None else os.environ.pop(k, None)))
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return imagext

    def _patch(self, mod, name, fn):
        old = getattr(mod, name)
        setattr(mod, name, fn)
        self.addCleanup(lambda: setattr(mod, name, old))


class TestVisionOverride(_EnvPatch):
    def test_router_override_routes_to_router(self):
        """vision=(timely, model) 오버라이드 → vision_understand(IE) 아니라 vision_via_router 로."""
        imagext = self._setenv(PRISM_TIMELY_KEY="k", UPSTAGE_API_KEY=None, PRISM_API_KEY=None)
        seen = {}
        self._patch(imagext, "vision_via_router",
                    lambda b, mime, model, service, **kw: seen.update(model=model, service=service)
                    or {"description": "무대 공연", "visible_text": "", "entities": ["가수"], "scene": "공연장"})
        self._patch(imagext, "vision_understand",
                    lambda *a, **k: self.fail("IE 경로가 호출되면 안 된다(라우터 오버라이드)"))
        sig = imagext.extract_signals([IMG], mock=False, vision=("timely", "gemini-3.5-flash"))[0]
        self.assertEqual(seen, {"model": "gemini-3.5-flash", "service": "timely"})
        self.assertIn("무대 공연", sig["vision"])

    def test_no_override_uses_config_default(self):
        """오버라이드 없으면 전역 config(_vision_cfg) 기본값(upstage_ie) → IE 경로."""
        imagext = self._setenv(UPSTAGE_API_KEY="sk", PRISM_TIMELY_KEY=None)
        self._patch(imagext, "_vision_cfg", lambda: ("upstage_ie", ""))
        used = {}
        self._patch(imagext, "vision_understand",
                    lambda b, mime="image/png", **k: used.update(hit=True)
                    or {"description": "간판", "visible_text": "카페", "entities": [], "scene": ""})
        sig = imagext.extract_signals([IMG], mock=False, vision=None)[0]
        self.assertTrue(used.get("hit"))
        self.assertIn("간판", sig["vision"])


class TestPurePhotoFallback(_EnvPatch):
    def test_ie_no_text_falls_back_to_router(self):
        """IE 가 NoTextInImage(순수 사진) → 라우터 키 있으면 gemini 로 1회 폴백 · note 표기."""
        imagext = self._setenv(UPSTAGE_API_KEY="sk", PRISM_TIMELY_KEY="k")

        def _raise(*a, **k):
            raise imagext.NoTextInImage("No text elements")
        self._patch(imagext, "vision_understand", _raise)
        fb_seen = {}
        self._patch(imagext, "vision_via_router",
                    lambda b, mime, model, service, **kw: fb_seen.update(model=model, service=service)
                    or {"description": "접시에 담긴 김밥", "visible_text": "", "entities": [], "scene": "음식"})
        sig = imagext.extract_signals([IMG], mock=False, vision=("upstage_ie", ""))[0]
        self.assertEqual(fb_seen, {"model": "gemini-3.5-flash", "service": "timely"})
        self.assertIn("김밥", sig["vision"])
        self.assertIn("폴백", sig.get("note", ""))

    def test_ie_no_text_no_router_key_notes_only(self):
        """라우터 키 없으면 폴백 없이 안내 note 만(기존 동작 유지)."""
        imagext = self._setenv(UPSTAGE_API_KEY="sk", PRISM_TIMELY_KEY=None)

        def _raise(*a, **k):
            raise imagext.NoTextInImage("No text elements")
        self._patch(imagext, "vision_understand", _raise)
        self._patch(imagext, "ocr_image", lambda *a, **k: "")   # OCR 폴백도 빈값
        sig = imagext.extract_signals([IMG], mock=False, vision=("upstage_ie", ""))[0]
        self.assertEqual(sig["vision"], "")
        self.assertIn("라우터 멀티모달 권장", sig.get("note", ""))


class TestVisionCandidates(unittest.TestCase):
    def test_baseline_always_present(self):
        from prism import serve
        old = os.environ.pop("PRISM_TIMELY_KEY", None)
        try:
            cands = serve._vision_candidates(serve.Config.load())
        finally:
            if old is not None:
                os.environ["PRISM_TIMELY_KEY"] = old
        self.assertEqual(cands[0]["id"], "upstage_ie")

    def test_router_candidates_when_key(self):
        from prism import serve
        os.environ["PRISM_TIMELY_KEY"] = "k"
        try:
            ids = [c["id"] for c in serve._vision_candidates(serve.Config.load())]
        finally:
            os.environ.pop("PRISM_TIMELY_KEY", None)
        self.assertIn("timely:gemini-3.5-flash", ids)
        self.assertIn("timely:gpt-5.4-mini", ids)


class TestRunPipelineVisionUsed(unittest.TestCase):
    def test_vision_used_reported(self):
        """run_pipeline 이미지 분기가 요청 슬롯을 vision_used 로 돌려준다(UI 표기용)."""
        from prism import serve
        fields = {"displayServiceName": "포토",
                  "image0": {"bytes": IMG_BYTES, "filename": "a.jpg", "mime": "image/jpeg"}}
        res = serve.run_pipeline(fields, mock=True, persist=False,
                                 vision=("timely", "gemini-3.5-flash"))
        self.assertEqual(res["vision_used"], {"provider": "timely", "model": "gemini-3.5-flash"})
        self.assertEqual(res["source"], "image")


if __name__ == "__main__":
    unittest.main()
