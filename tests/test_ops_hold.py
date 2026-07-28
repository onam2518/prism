"""운영자 수동 노출제한(ops_hold) 회귀 테스트 (게시판 #2).

quality_meta.ops_hold 에 저장 · 품질 라벨(finalGrade·reasons)과 분리 → 학습 루프 미포함.
recent() 로 노출되어 검수 UI·CSV 내보내기에 흐른다.

실행: python3 -m pytest tests/test_ops_hold.py -q
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestOpsHold(unittest.TestCase):
    def _store(self):
        from prism.store import Store
        return Store(os.path.join(tempfile.mkdtemp(), "t.db"))

    def _save(self, st, title="콘텐츠"):
        from prism.store import content_hash
        content = {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "본문 " + title}
        out = {"content_ref": dict(content),
               "quality_meta": {"finalGrade": "G", "reasons": []},
               "item_meta": {"summary": "s", "content_category": ["Sports"], "intent": [], "entities": []},
               "trace": {"model": "m"}}
        st.save_result(content, out, "run1")
        return content_hash(content)

    def _row(self, st, ch):
        for r in st.recent():
            cr = r.get("content_ref") or {}
            if cr.get("body_hash") == ch or (r.get("content_ref") or {}).get("title"):
                return r
        return st.recent()[0] if st.recent() else None

    def test_set_and_isolation(self):
        st = self._store()
        ch = self._save(st)
        self.assertTrue(st.set_ops_hold(ch, True))
        row = self._row(st, ch)
        self.assertTrue(row["quality_meta"]["ops_hold"])          # 저장됨
        self.assertEqual(row["quality_meta"]["finalGrade"], "G")  # 등급 불변(라벨 분리)
        self.assertEqual(row["quality_meta"]["reasons"], [])      # 사유 불변
        # 해제
        self.assertTrue(st.set_ops_hold(ch, False))
        self.assertFalse((self._row(st, ch)["quality_meta"]).get("ops_hold"))

    def test_missing_hash(self):
        st = self._store()
        self.assertFalse(st.set_ops_hold("nope", True))

    def test_excluded_from_golden_learning(self):
        # ops_hold 는 build_golden_from_reviews 가 읽는 필드(finalGrade·content_category)와 무관 →
        # 골든 승격/학습에 영향 없음을 간접 확인(등급·카테고리 그대로 승격 대상).
        import time as _t
        from prism import serve
        st = self._store()
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        ch = self._save(st, "합의물")
        st.set_ops_hold(ch, True)
        st.save_feedback(ch, "뉴스", "합의물", "good", "review", "", _t.time(), reviewer="A")
        st.save_feedback(ch, "뉴스", "합의물", "good", "review", "", _t.time(), reviewer="B")
        g = serve.build_golden_from_reviews(None)
        self.assertEqual(g["confirmed"], 1)                       # ops_hold 여부와 무관하게 정상 승격


if __name__ == "__main__":
    unittest.main()
