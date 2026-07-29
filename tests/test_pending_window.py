"""미실행 콘텐츠가 '표시용 창' 밖에 있으면 세지도, 실행되지도 않던 회귀 차단.

배경(2026-07-28 운영): 콘텐츠 600건 중 미실행 200건이 있는데 화면은 '미실행만 (0건)'.
① 건수를 recent_meta(최신 200건) 창에서 셌고 ② rerun_all 이 rows[-limit:] 로 먼저 잘라
그 안에서만 대상을 찾았다. 결과 뷰가 최신순이라 잘린 창은 '가장 오래된 200건'이었고,
미실행분(201~400위)은 양쪽 모두에서 빠져 실행할 방법이 없었다.

실행: python3 -m pytest tests/ -q
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class PendingWindowBase(unittest.TestCase):
    def _serve(self):
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)
        return serve

    def _put(self, st, i, ran, ts):
        """ran=True 면 실행된 행(모델·판정 있음) · False 면 추가만 된 미실행 행."""
        from prism.store import content_hash
        ref = {"displayServiceName": "s", "title": "c%04d" % i, "subtitle": "", "body": "b%04d" % i}
        h = content_hash(ref)
        payload = {"content_ref": ref}
        if ran:
            payload["quality_meta"] = {"review": "auto", "finalGrade": "G"}
            payload["trace"] = {"model": "m1"}
            payload["item_meta"] = {"content_category": ["News"]}
        else:
            payload["quality_meta"] = {}
        c = st._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,payload,created_at) "
                  "VALUES(?,?,?,?,?,?)", (h, "s", ref["title"], "G" if ran else "", json.dumps(payload), ts))
        c.commit()
        return h

    def _fixture(self, serve, n_new=250, n_pending=50, n_old=250):
        """운영과 같은 배치: 최신 실행분 → 미실행분 → 오래된 실행분.
        미실행이 최신순 상위 200 창에도, 하위 200 창에도 안 걸리도록 배치한다."""
        st = serve._STORE
        now = time.time()
        for i in range(n_old):                       # 가장 오래됨
            self._put(st, i, True, now - 3000 + i)
        pend = [self._put(st, 1000 + i, False, now - 2000 + i) for i in range(n_pending)]
        for i in range(n_new):                       # 가장 최신
            self._put(st, 2000 + i, True, now - 1000 + i)
        return pend


class TestPendingCount(PendingWindowBase):
    def test_counts_are_team_wide_not_window(self):
        """'미실행만 N건'은 표시용 200건 창이 아니라 팀 전체 기준이어야 한다."""
        serve = self._serve()
        pend = self._fixture(serve)
        d = serve.dashboard_data(None)
        self.assertEqual(d["pending_n"], len(pend))          # 창 기준이면 0 이 나온다
        self.assertEqual(d["contents_n"], 550)
        self.assertLessEqual(len(d["contents"]), 200)        # 표시 목록은 상한 유지(성능)

    def test_display_window_would_hide_them(self):
        """가드가 헛돌지 않는지: 표시용 창만 보면 실제로 0건으로 보인다."""
        serve = self._serve()
        self._fixture(serve)
        window = serve.dashboard_data(None)["contents"]
        self.assertEqual(sum(1 for c in window if not c.get("model")), 0)


class TestRerunAllTargets(PendingWindowBase):
    def test_pending_scope_reaches_items_outside_window(self):
        """미실행분이 창 밖에 있어도 실행 대상으로 잡혀야 한다."""
        serve = self._serve()
        pend = set(self._fixture(serve))
        called = []
        orig = serve.RN._SV.rerun_content

        def fake(ch, model, team=None, row=None, **kw):
            called.append(ch)
            return {"output": {"trace": {"cost_usd": 0.0}}}
        serve.rerun_content = fake
        self.addCleanup(lambda: setattr(serve, "rerun_content", orig))
        r = serve.rerun_all("m1", None, scope="pending")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(set(called), pend)                  # 미실행 전건이 대상
        self.assertEqual(r["done"], len(pend))

    def test_limit_is_a_batch_cap_not_a_window(self):
        """상한은 '한 번에 실행할 최대 건수' · 대상을 고른 뒤에 적용된다."""
        serve = self._serve()
        pend = set(self._fixture(serve))
        called = []
        orig = serve.rerun_content
        serve.rerun_content = lambda ch, model, team=None, row=None, **kw: (
            called.append(ch) or {"output": {"trace": {"cost_usd": 0.0}}})
        self.addCleanup(lambda: setattr(serve, "rerun_content", orig))
        r = serve.rerun_all("m1", None, limit=20, scope="pending")
        self.assertEqual(len(called), 20)
        self.assertTrue(set(called) <= pend)                 # 상한을 걸어도 대상은 미실행분에서만
        self.assertEqual(r["done"], 20)


if __name__ == "__main__":
    unittest.main()
