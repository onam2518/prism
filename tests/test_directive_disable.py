"""학습 지시 개별 무효화: 잘못 들어간 보정 하나만 끈다(원본 보존 · 다음 컴파일부터 제외).

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
설계: DDL 없이 reports kind='disabled_directives'(지시 원문 정확 일치 키)로 양 백엔드 공통.
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDirectiveDisable(unittest.TestCase):
    def _serve(self):
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve, st

    def _route(self, st, stage, directive, model=""):
        c = st._conn()
        c.execute("INSERT INTO feedback_routes(content_hash,reviewer,element,stage,directive,model,ts) "
                  "VALUES('h','A','category',?,?,?,?)", (stage, directive, model, time.time()))
        c.commit()

    def test_store_exclude_filters_routes_and_notes(self):
        serve, st = self._serve()
        self._route(st, "analyze", "카테고리는 사전 표기 그대로")
        self._route(st, "analyze", "잘못 들어간 지시")
        self._route(st, "judge", "잘못 들어간 지시", model="gpt-x")
        st.save_feedback("h2", "s", "T", "bad", "review", "잘못 들어간 지시", time.time(), reviewer="A")
        ex = {"잘못 들어간 지시"}
        self.assertEqual(st.routes_by_stage(exclude=ex)["analyze"], ["카테고리는 사전 표기 그대로"])
        self.assertEqual(st.routes_by_stage_model(exclude=ex), {})          # 모델 귀속도 제외
        learned = st.learned_by_stage(exclude=ex)
        self.assertNotIn("잘못 들어간", learned.get("analyze", "") + learned.get("review", ""))
        # exclude 없이면 그대로 보인다(원본 보존)
        self.assertIn("잘못 들어간 지시", st.routes_by_stage()["analyze"])

    def test_disable_roundtrip_and_overview(self):
        serve, st = self._serve()
        self._route(st, "analyze", "좋은 지시")
        self._route(st, "analyze", "나쁜 지시")
        r = serve.set_directive_disabled("나쁜 지시", True)
        self.assertTrue(r["ok"])
        self.assertEqual(serve.disabled_directives(), {"나쁜 지시"})
        ov = {i["text"]: i for i in serve.routes_overview(None)["items"]}
        self.assertTrue(ov["나쁜 지시"]["disabled"])
        self.assertFalse(ov["좋은 지시"]["disabled"])
        serve.set_directive_disabled("나쁜 지시", False)                    # 다시 켜기
        self.assertEqual(serve.disabled_directives(), set())

    def test_empty_text_rejected(self):
        serve, _ = self._serve()
        self.assertFalse(serve.set_directive_disabled("  ", True)["ok"])


if __name__ == "__main__":
    unittest.main()
