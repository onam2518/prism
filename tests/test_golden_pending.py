"""반영 대기 집계(promotion_pending)와 /golden-status 노출.

배경(운영 2026-08-06): 검수 콘텐츠 488건인데 정답셋 122건. 학습 반영이 7/22 이후
멈췄는데 그 사실이 어디에도 안 보여 '검수한 수량이 반영이 안 된다'로 읽혔다.
정답셋 현황에 승격 대기 수·반영 정체 일수·의견 갈림(최종검수 몫)을 상시 노출한다.

정합 고정: promotion_pending 의 분류는 build_golden_from_reviews(승격)와 같은 규칙이어야
한다 — 갈라지면 '골든도 큐도 아닌' 영구 미확정이 생긴다. 승격 대기 수 == 실제 배치의
confirmed 를 시드 데이터로 검증한다.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import json as _j
import os
import sys
import tempfile
import time as _t
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class PendingBase(unittest.TestCase):
    def _with_store(self):
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        serve._agg_bump()
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)
        return serve, st

    def _put_reviewed(self, st, title, verdicts, cats=("Sports",), grade="G"):
        from prism.store import content_hash
        content = {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "본문 " + title}
        ch = content_hash(content)
        im = {"summary": title, "entities": [], "intent": [], "content_category": list(cats)}
        payload = {"quality_meta": {"review": "yellow", "finalGrade": grade, "reasons": []},
                   "item_meta": im, "content_ref": dict(content)}
        c = st._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,reasons,item_meta,payload,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (ch, "뉴스", title, grade, "[]", _j.dumps(im), _j.dumps(payload), _t.time()))
        c.commit()
        for rv, v in verdicts:
            st.save_feedback(ch, "뉴스", title, v, "review", "", _t.time(), reviewer=rv)
        return ch

    def _seed_mixed(self, st):
        """승격 1 · 등급 없음 1 · 분류 없음 1 · 갈림 1 · 수정필요 일방 1."""
        self._put_reviewed(st, "승격감", [("A", "good"), ("B", "good")])
        self._put_reviewed(st, "등급 없음", [("A", "good")], grade="")
        self._put_reviewed(st, "분류 없음", [("A", "good")], cats=())
        self._put_reviewed(st, "갈림", [("A", "good"), ("B", "bad"), ("C", "bad")])
        self._put_reviewed(st, "수정필요 합의", [("A", "bad"), ("B", "bad")])


class TestPromotionPending(PendingBase):
    def test_buckets(self):
        serve, st = self._with_store()
        self._seed_mixed(st)
        p = serve.promotion_pending(None)
        self.assertEqual(p, {"promote": 1, "split": 1, "no_grade": 1, "no_cat": 1, "base_fix": 1})

    def test_matches_actual_batch(self):
        """승격 대기 수 == 실제 배치 confirmed(같은 게이트) · 배치 후 대기 0."""
        serve, st = self._with_store()
        self._seed_mixed(st)
        p0 = serve.promotion_pending(None)
        g = serve.build_golden_from_reviews(None)
        self.assertEqual(g["confirmed"], p0["promote"])
        serve._agg_bump()                                  # 골든 반영분 캐시 무효화
        p1 = serve.promotion_pending(None)
        self.assertEqual(p1["promote"], 0)                 # 승격분은 대기에서 빠진다
        self.assertEqual(p1["split"], p0["split"])         # 갈림은 배치가 못 푼다(최종검수 몫)

    def test_final_verdict_overrides(self):
        """리드 최종판정: good=승격 대기 편입 · bad=대기 목록에서 제외(결정 완료)."""
        serve, st = self._with_store()
        ch = self._put_reviewed(st, "갈림", [("A", "good"), ("B", "bad"), ("C", "bad")])
        self.assertEqual(serve.promotion_pending(None)["split"], 1)
        serve.set_final_verdict(ch, "good", by="리드", team=None)
        serve._agg_bump()
        p = serve.promotion_pending(None)
        self.assertEqual((p["promote"], p["split"]), (1, 0))
        serve.set_final_verdict(ch, "bad", by="리드", team=None)
        serve._agg_bump()
        p = serve.promotion_pending(None)
        self.assertEqual((p["promote"], p["split"]), (0, 0))


class TestGoldenStatusRoute(PendingBase):
    def test_status_exposes_pending_and_stale(self):
        serve, st = self._with_store()
        self._seed_mixed(st)

        class FakeH:
            def _req_team(self):
                return None
        r = serve._g_golden_status(FakeH(), {})
        self.assertTrue(r["ok"])
        self.assertEqual(r["pending"]["promote"], 1)
        self.assertIn("stale_days", r)                     # 반영 이력 없으면 None
        self.assertIsNone(r["stale_days"])

    def test_ui_wires_pending_strip(self):
        from prism import page
        self.assertIn("goldenStatus.pending && goldenStatus.pending.promote", page.PAGE)
        self.assertIn("goldenStatus.stale_days", page.PAGE)
        self.assertIn("goldenStatus.pending.split", page.PAGE)


if __name__ == "__main__":
    unittest.main()
