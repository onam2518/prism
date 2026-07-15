"""배정 감사 추적(assign_log): 누가·언제·어떤 방식으로 몇 건을 배정/해제했는지 기록.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
배경(2026-07-15): 배정에 실행자 기록이 없어 '별도 지정 안 했는데 배정돼 있음 · 누가?'에
답할 수 없었다. reports kind='assign_log'(팀 스코프 · 상한 100 · DDL 불필요)로 원장화.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAssignAudit(unittest.TestCase):
    def _serve(self):
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve

    def test_log_and_read_newest_first(self):
        serve = self._serve()
        serve._log_assign("pete@axz.com", "균등 분배", 20, ["에디", "해씨"], 1, None)
        serve._log_assign("pete@axz.com", "일괄 해제", 37, [], 0, None)
        items = serve.assign_log_data(None)["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["mode"], "일괄 해제")          # 최신순
        self.assertEqual(items[0]["n"], 37)
        self.assertEqual(items[1]["by"], "pete@axz.com")
        self.assertEqual(items[1]["reviewers"], ["에디", "해씨"])
        self.assertTrue(items[0]["ts"] > 0)

    def test_capped_at_100(self):
        serve = self._serve()
        for i in range(105):
            serve._log_assign("a@b.c", "개별 배정", 1, ["r%d" % i], 1, None)
        items = serve.assign_log_data(None)["items"]
        self.assertEqual(len(items), 100)
        self.assertEqual(items[0]["reviewers"], ["r104"])        # 최신 유지 · 오래된 것 정리

    def test_team_scoped(self):
        serve = self._serve()
        serve._log_assign("a@b.c", "일괄 배정", 3, ["에디"], 1, "team-A")
        self.assertEqual(len(serve.assign_log_data("team-A")["items"]), 1)
        self.assertEqual(serve.assign_log_data("team-B")["items"], [])


if __name__ == "__main__":
    unittest.main()
