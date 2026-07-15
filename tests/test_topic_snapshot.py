"""토픽 자동 리프레시 스냅샷: 주기 재계산 결과를 적재하고 직전 대비 변화를 계산한다.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
배경: 토픽은 조건 재평가 방식이라 열어둔 화면이 낡고, 성과 추이를 볼 원천이 없었다
(2026-07-15 리뷰 P2-5 · reports kind='topic_snapshots' · 토픽은 무팀 전역 뷰).
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestTopicSnapshot(unittest.TestCase):
    def _serve(self):
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve

    def test_delta_new_changed_gone(self):
        serve = self._serve()
        fixtures = [
            {"single": [{"cluster_id": "S-a", "name": "손흥민", "type": "single", "count": 3}],
             "composite": [],
             "custom": [{"id": "t1", "name": "경제 큐레이션", "core_count": 5}]},
            {"single": [{"cluster_id": "S-a", "name": "손흥민", "type": "single", "count": 5}],
             "composite": [], "custom": []},
        ]
        orig = serve.topics_data
        serve.topics_data = lambda: (fixtures.pop(0) if fixtures
                                     else {"single": [], "composite": [], "custom": []})
        self.addCleanup(lambda: setattr(serve, "topics_data", orig))
        d1 = serve.topic_snapshot()
        self.assertEqual(d1["changed_n"], 2)             # 첫 스냅샷: 둘 다 0→n (신규)
        self.assertEqual(d1["new_n"], 2)
        self.assertEqual(d1["gone_n"], 0)
        d2 = serve.topic_snapshot()
        self.assertEqual(d2["changed_n"], 1)             # S-a 3→5
        self.assertEqual((d2["changed"][0]["from"], d2["changed"][0]["to"]), (3, 5))
        self.assertEqual(d2["gone_n"], 1)                # t1 소멸
        self.assertEqual(d2["gone"][0]["label"], "경제 큐레이션")
        rep = serve._report_get("topic_snapshots", None, {})
        self.assertEqual(len(rep["entries"]), 2)         # 스냅샷 누적
        self.assertEqual(rep["last_delta"]["changed_n"], 1)

    def test_entries_capped(self):
        serve = self._serve()
        orig = serve.topics_data
        serve.topics_data = lambda: {"single": [], "composite": [], "custom": []}
        self.addCleanup(lambda: setattr(serve, "topics_data", orig))
        for _ in range(serve._TOPIC_SNAP_CAP + 5):
            serve.topic_snapshot()
        rep = serve._report_get("topic_snapshots", None, {})
        self.assertEqual(len(rep["entries"]), serve._TOPIC_SNAP_CAP)   # 무한 성장 방지


if __name__ == "__main__":
    unittest.main()
