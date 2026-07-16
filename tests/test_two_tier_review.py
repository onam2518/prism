"""2층 검수 1단계: 최종검수자 역할(사람 단위) + 최종검수 큐(미확정분 편입/제외).

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
계약: 큐 대상 = ① 의견 갈림(split) ② 정확 합의인데 분류 공백. 정상 확정 경로·수정 일방
합의·기초 표 부족·기확정 골든은 제외. 판정 저장은 final_verdicts(#173) 재사용.
"""
import json as _j
import os
import sys
import tempfile
import time as _t
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TwoTierBase(unittest.TestCase):
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


class TestReviewerRoles(TwoTierBase):
    def test_role_roundtrip(self):
        serve, _st = self._with_store()
        r = serve.set_reviewer_role("uid-1", "final", None)
        self.assertTrue(r["ok"])
        self.assertEqual(r["final_reviewers"], ["uid-1"])
        self.assertTrue(serve.is_final_reviewer("uid-1", None))
        self.assertFalse(serve.is_final_reviewer("uid-2", None))
        serve.set_reviewer_role("uid-1", "", None)               # 해제 = 기초 복귀
        self.assertEqual(serve.reviewer_roles(None), {})
        self.assertFalse(serve.set_reviewer_role("", "final")["ok"])


class TestFinalQueue(TwoTierBase):
    def test_queue_selection_rules(self):
        serve, st = self._with_store()
        ch_split = self._put_reviewed(st, "의견 갈림 건", [("A", "good"), ("B", "bad")])
        ch_nocat = self._put_reviewed(st, "분류 없는 합의", [("A", "good"), ("B", "good")], cats=())
        self._put_reviewed(st, "정상 확정 경로", [("A", "good"), ("B", "good")])          # 제외
        self._put_reviewed(st, "수정 일방 합의", [("A", "bad"), ("B", "bad")])            # 제외
        self._put_reviewed(st, "검수 없음", [])                                          # 제외
        q = serve.final_review_queue(None)
        got = {i["title"]: i["final_reason"] for i in q["items"]}
        self.assertEqual(got, {"의견 갈림 건": "의견 갈림", "분류 없는 합의": "분류 없음"})
        by = {i["title"]: i for i in q["items"]}
        self.assertEqual(by["의견 갈림 건"]["fb"]["good"], 1)     # 기초 의견 요약 동반
        self.assertEqual(by["의견 갈림 건"]["final"], "")
        self.assertIn("hash", by["의견 갈림 건"])
        # 최종판정 저장 → 큐에 상태 반영 · 리드 'good' 이면 골든 승격 후 큐에서 제거
        serve.set_final_verdict(ch_split, "good", by="리드", team=None)
        q2 = serve.final_review_queue(None)
        self.assertEqual({i["title"]: i["final"] for i in q2["items"]}.get("의견 갈림 건"), "good")
        serve.build_golden_from_reviews(None)                    # 학습 반영 시 승격
        self.assertIn(ch_split, st.golden_hashes())
        q3 = serve.final_review_queue(None)
        self.assertNotIn("의견 갈림 건", [i["title"] for i in q3["items"]])   # 확정분은 큐에서 사라짐
        self.assertIn(ch_nocat, [i["hash"] for i in q3["items"]])           # 분류 공백은 잔류


if __name__ == "__main__":
    unittest.main()
