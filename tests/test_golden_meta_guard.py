"""정답 유실 방지(2026-10-02): 업로드 기본 병합 · 전체 삭제 동작.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
메타 축 보존(빈 값 덮어쓰기 차단)은 DB 트리거 · 검증은 supabase/tests/golden_meta_guard.sql.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGoldenMetaGuard(unittest.TestCase):
    def _with_store(self):
        import tempfile
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve, st

    def _row(self, title, **exp):
        return {"content": {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "b " + title},
                "expected": exp}

    def test_upload_defaults_to_merge(self):
        serve, st = self._with_store()
        serve.register_golden("", None, [self._row("기존", finalGrade="G", content_category=["Sports"])], "")
        serve.register_golden("", None, [self._row("추가", finalGrade="R", content_category=["Sports"])], "")
        self.assertEqual(st.golden_count(None), 2)                        # 기본 호출이 기존 정답을 지우지 않는다
        serve.register_golden("", None, [self._row("교체", finalGrade="G")], "", merge=False)
        self.assertEqual(st.golden_count(None), 1)                        # 전체 교체는 명시할 때만

    def test_clear_golden_action_deletes(self):
        serve, st = self._with_store()
        from prism import adminops as AO
        serve.register_golden("", None, [self._row("a", finalGrade="G"), self._row("b", finalGrade="R")], "")
        self.assertEqual(st.golden_count(None), 2)
        AO.admin_action("", None, {"action": "clear_golden"})
        self.assertEqual(st.golden_count(None), 0)                        # 빈 목록 등록으로 아무것도 안 지우던 버그


if __name__ == "__main__":
    unittest.main()
