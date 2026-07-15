"""리드 최종판정(타이브레이크): 의견 갈림을 슈퍼관리자 이상이 확정 · 골든 승격에서 다수결보다 우선.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
설계: reports kind='final_verdicts'(팀 스코프 · DDL 불필요) · 검수자 개별 의견 행은 불변(보존).
"""
import json as _j
import os
import sys
import tempfile
import time as _t
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FinalVerdictBase(unittest.TestCase):
    def _with_store(self):
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve, st

    def _put_reviewed(self, st, title, verdicts, cats=("Sports",)):
        from prism.store import content_hash
        content = {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "본문 " + title}
        ch = content_hash(content)
        im = {"summary": title, "entities": [], "intent": [], "content_category": list(cats)}
        payload = {"quality_meta": {"review": "yellow", "finalGrade": "G", "reasons": []},
                   "item_meta": im, "content_ref": dict(content)}
        c = st._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,reasons,item_meta,payload,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (ch, "뉴스", title, "G", "[]", _j.dumps(im), _j.dumps(payload), _t.time()))
        c.commit()
        for rv, v in verdicts:
            st.save_feedback(ch, "뉴스", title, v, "review", "", _t.time(), reviewer=rv)
        return ch


class TestFinalVerdict(FinalVerdictBase):
    def test_roundtrip_and_withdraw(self):
        serve, _st = self._with_store()
        r = serve.set_final_verdict("h1", "good", by="리드", team=None)
        self.assertTrue(r["ok"])
        self.assertEqual(serve.final_verdicts(None)["h1"]["verdict"], "good")
        serve.set_final_verdict("h1", "", team=None)                 # 철회
        self.assertEqual(serve.final_verdicts(None), {})
        self.assertFalse(serve.set_final_verdict("", "good")["ok"])  # hash 필수

    def test_final_good_promotes_split_content(self):
        serve, st = self._with_store()
        ch = self._put_reviewed(st, "의견 갈림", [("A", "good"), ("B", "bad")])   # split · 다수결 미충족
        g0 = serve.build_golden_from_reviews(None)
        self.assertEqual(g0["confirmed"], 0)
        self.assertEqual(g0["disagree"], 1)
        serve.set_final_verdict(ch, "good", by="리드", team=None)     # 리드 확정 → 승격
        g1 = serve.build_golden_from_reviews(None)
        self.assertEqual(g1["confirmed"], 1)
        self.assertIn(ch, st.golden_hashes())

    def test_final_bad_blocks_and_demotes(self):
        serve, st = self._with_store()
        ch = self._put_reviewed(st, "합의 정확", [("A", "good"), ("B", "good")])
        serve.build_golden_from_reviews(None)                        # 먼저 정상 승격
        self.assertIn(ch, st.golden_hashes())
        serve.set_final_verdict(ch, "bad", by="리드", team=None)      # 리드가 '수정 필요' 확정
        g = serve.build_golden_from_reviews(None)
        self.assertEqual(g["demoted"], 1)                            # 검수 유래 골든 강등
        self.assertNotIn(ch, st.golden_hashes())

    def test_raw_rows_carry_final_badge(self):
        serve, st = self._with_store()
        ch = self._put_reviewed(st, "배지 확인", [("A", "good"), ("B", "bad")])
        serve.set_final_verdict(ch, "good", team=None)
        row = next(r for r in serve.raw_rows()["items"] if r["hash"] == ch)
        self.assertEqual(row["final"], "good")


if __name__ == "__main__":
    unittest.main()
