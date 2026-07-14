"""S5 메타추출 모델 A/B 하네스(media_s5ab) 계약 테스트.

같은 통합 원고를 여러 후보 모델에 태워 ItemMeta 를 나란히 반환하는지 검증.
mock 모드라 모델별 route=mock(동일 산출)이지만, 모델 축·item_meta 구조·상한·미저장을 확인."""
import os
import tempfile
import unittest

os.environ.setdefault("PRISM_DB", os.path.join(tempfile.mkdtemp(), "t.db"))

import prism.serve as S

BODY = "오늘 서울 도심에서 대규모 행사가 열렸다. 시민들이 모여 현장을 지켜봤다."


class TestMediaS5AB(unittest.TestCase):
    def setUp(self):
        S.Handler.server_mock = True

    def test_per_model_item_meta(self):
        r = S.media_s5ab(BODY, ["solar-pro2", "gpt-5.4", "gemini-2.5-pro"])
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["results"]), 3)
        models = [x["model"] for x in r["results"]]
        self.assertEqual(models, ["solar-pro2", "gpt-5.4", "gemini-2.5-pro"])
        for x in r["results"]:
            self.assertTrue(x.get("mock"))
            self.assertIn("item_meta", x)
            self.assertTrue(x["item_meta"].get("summary"))          # mock 채워짐
            self.assertIn("content_category", x["item_meta"])
            # 계측 필드: 빈 산출 진단(mock 은 채워지므로 empty=False·fails 비어야)
            self.assertIn("empty", x)
            self.assertIn("fails", x)
            self.assertFalse(x["empty"])
            self.assertEqual(x["fails"], [])

    def test_dedup_and_cap(self):
        # 중복 제거 + 상한 6
        r = S.media_s5ab(BODY, ["m1", "m1", "m2", "m3", "m4", "m5", "m6", "m7", "m8"])
        names = [x["model"] for x in r["results"]]
        self.assertEqual(len(names), 6)
        self.assertEqual(names[0], "m1")
        self.assertEqual(len(set(names)), 6)                        # 중복 없음

    def test_action_validation(self):
        self.assertFalse(S.media_action({"action": "s5ab", "text": "", "models": ["x"]})["ok"])
        self.assertFalse(S.media_action({"action": "s5ab", "text": BODY, "models": []})["ok"])
        ok = S.media_action({"action": "s5ab", "text": BODY, "models": ["solar-pro2"]})
        self.assertTrue(ok["ok"])
        self.assertEqual(len(ok["results"]), 1)

    def test_not_persisted(self):
        st = S.get_store()
        before = len(S.results_rows()) if st else 0
        S.media_s5ab(BODY, ["solar-pro2", "gpt-5.4"])
        after = len(S.results_rows()) if st else 0
        self.assertEqual(before, after)                             # 실험 · 미저장

    def test_trace_surfaces_fail_kind(self):
        # 콜 실패 시 trace.fails 에 fail_kind 가 표면화되는지(하네스 계측). 도달 불가 URL → network.
        from prism import pipeline as PIPE
        from prism.llm import LLMClient
        from prism.config import Config, RetryPolicy
        cfg = Config()
        cfg.chat_url = "http://127.0.0.1:1/v1/chat/completions"      # 연결 거부(즉시 실패)
        cfg.retry = RetryPolicy(max_retries=0)                       # 백오프 없이 1회
        llm = LLMClient(config=cfg, api_key="k", model="gpt-5.4")   # 비-mock
        out = PIPE.extract({"displayServiceName": "영상", "title": "", "subtitle": "",
                            "body": "서울 도심 행사"}, llm, legal=False)
        fails = (out.get("trace") or {}).get("fails")
        self.assertTrue(fails)                                       # 최소 1건 표면화
        self.assertTrue(any(f.get("kind") for f in fails))          # fail_kind 존재


if __name__ == "__main__":
    unittest.main()
