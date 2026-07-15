"""부족 분류 우선(능동학습): 정답셋이 부족한 Tier1 콘텐츠에 배지를 달아 라벨 예산을 유도.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
계약: _lack_classes = 골든 보유 8건 미만 Tier1 집합 · raw_rows 행에 class_gap 플래그.
"""
import json as _j
import os
import sys
import tempfile
import time as _t
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestClassGapQueue(unittest.TestCase):
    def _serve(self):
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        serve._agg_bump()
        return serve, st

    def _put(self, st, title, cats):
        from prism.store import content_hash
        content = {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "본문 " + title}
        ch = content_hash(content)
        im = {"summary": title, "entities": [], "intent": [], "content_category": list(cats)}
        payload = {"quality_meta": {"review": "yellow", "finalGrade": "G", "reasons": []},
                   "item_meta": im, "trace": {"model": "m", "version": 1},
                   "content_ref": dict(content)}
        c = st._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,reasons,item_meta,payload,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (ch, "뉴스", title, "G", "[]", _j.dumps(im), _j.dumps(payload), _t.time()))
        c.commit()
        return ch

    def test_lack_classes_threshold(self):
        serve, st = self._serve()
        # Sports 골든 8건(충분) · Business 는 1건(부족)
        for i in range(8):
            st.register_golden(None, [{"content": {"displayServiceName": "뉴스", "title": "s%d" % i,
                                                   "subtitle": "", "body": "b"},
                                       "expected": {"finalGrade": "G", "content_category": ["Sports"]}}],
                               replace=False, source="manual")
        st.register_golden(None, [{"content": {"displayServiceName": "뉴스", "title": "biz",
                                               "subtitle": "", "body": "b"},
                                   "expected": {"finalGrade": "G",
                                                "content_category": ["Business and Finance"]}}],
                           replace=False, source="manual")
        serve._agg_bump()
        lack = serve._lack_classes(None)
        self.assertNotIn("Sports", lack)                       # 8건 이상 → 충분
        self.assertIn("Business and Finance", lack)            # 1건 → 부족

    def test_raw_rows_flag(self):
        serve, st = self._serve()
        for i in range(8):
            st.register_golden(None, [{"content": {"displayServiceName": "뉴스", "title": "s%d" % i,
                                                   "subtitle": "", "body": "b"},
                                       "expected": {"finalGrade": "G", "content_category": ["Sports"]}}],
                               replace=False, source="manual")
        serve._agg_bump()
        ch_gap = self._put(st, "경제 기사", ["Business and Finance / Economy"])   # Tier1 부족
        ch_ok = self._put(st, "스포츠 기사", ["Sports"])                          # 충분
        rows = {r["hash"]: r for r in serve.raw_rows()["items"]}
        self.assertTrue(rows[ch_gap]["class_gap"])
        self.assertFalse(rows[ch_ok]["class_gap"])


if __name__ == "__main__":
    unittest.main()
