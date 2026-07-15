"""트렌딩 엔티티(ent_trending): 최근 창 언급 급증 후보 · 추천 토픽 카드의 원천.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
계약: 최근 h시간 vs 그 전 같은 창 비교 · 최소 2건 + 증가분만 · 증가폭 내림차순 · 이름은 사전 우선.
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestEntTrending(unittest.TestCase):
    def _store(self):
        from prism.store import Store
        return Store(os.path.join(tempfile.mkdtemp(), "t.db"))

    def _link(self, st, eid, ch, ts, surface=""):
        c = st._conn()
        c.execute("INSERT OR IGNORE INTO content_entities(content_hash,entity_id,surface,team,ts) "
                  "VALUES(?,?,?,?,?)", (ch, eid, surface or eid, "", ts))
        c.commit()

    def test_rising_entities_ranked(self):
        st = self._store()
        now = time.time()
        H = 3600.0
        c = st._conn()
        c.execute("INSERT INTO entities(entity_id,name,type,status,created_at,updated_at) "
                  "VALUES('e1','손흥민','PS','confirmed',?,?)", (now, now))
        c.commit()
        for i in range(4):                                  # e1: 최근 4건 · 그 전 1건 → +3
            self._link(st, "e1", "c%d" % i, now - i * H)
        self._link(st, "e1", "cp", now - 60 * H)
        for i in range(2):                                  # e2: 최근 2건 · 그 전 0건 → +2 (이름=surface)
            self._link(st, "e2", "d%d" % i, now - i * H, surface="새 브랜드")
        self._link(st, "e3", "x1", now - H)                 # e3: 1건뿐 → 최소 2건 미달
        self._link(st, "e4", "y1", now - H)                 # e4: 최근 2건 = 그 전 2건 → 증가 없음
        self._link(st, "e4", "y2", now - 2 * H)
        self._link(st, "e4", "y3", now - 60 * H)
        self._link(st, "e4", "y4", now - 61 * H)
        out = st.ent_trending(hours=48, limit=8)
        names = [(t["name"], t["recent"], t["prev"]) for t in out]
        self.assertEqual(names[0], ("손흥민", 4, 1))         # 사전 이름 우선 · 증가폭 1위
        self.assertEqual(names[1], ("새 브랜드", 2, 0))      # surface 폴백
        self.assertEqual(len(out), 2)                        # e3(소량)·e4(무증가) 제외

    def test_limit(self):
        st = self._store()
        now = time.time()
        for k in range(5):
            for i in range(2):
                self._link(st, "e%d" % k, "c%d-%d" % (k, i), now - i * 60)
        self.assertEqual(len(st.ent_trending(hours=48, limit=3)), 3)


if __name__ == "__main__":
    unittest.main()
