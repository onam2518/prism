"""보존 기한(retention) 회귀 테스트.

- 평가용(purpose=eval) 콘텐츠 보존 필터가 조회 쿼리에 포함되는지
- 파생 테이블(초안·피드백·평가판정·배정) 연쇄 삭제가 함께 실행되는지
- 잘못된 hash 는 삭제 대상에서 걸러지는지 (in.() 쿼리 오염 방지)
- 스케줄러가 PRISM_RETENTION_DAYS 미설정 시 가동하지 않는지 (opt-in 계약)
- 일배치가 상한(회당 5,000)에 막히지 않고 다 지울 때까지 반복하는지 (드레인 · 2026-09-02)

실행: python3 -m pytest tests/test_retention.py -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_store(rows):
    """네트워크 없이 쿼리 기록만 하는 SupaStore 대역."""
    from prism import supastore as SS

    class _Fake(SS.SupabaseStore):
        def __init__(self):                       # 네트워크·env 초기화 우회
            self.gets, self.deletes = [], []
            self._rows = rows

        def _get(self, table, query=""):
            self.gets.append((table, query))
            return self._rows

        def _req(self, method, table, query="", body=None, prefer=None):
            if method == "DELETE":
                self.deletes.append((table, query))
            return []

    return _Fake()


class TestRetention(unittest.TestCase):
    def test_eval_preserved_and_cascade(self):
        h1, h2 = "a" * 16, "b" * 16
        st = _make_store([{"hash": h1}, {"hash": h2}, {"hash": "잘못된값"}])
        n = st.retention(days=30)
        self.assertEqual(n, 2)                    # 형식 불량 hash 제외
        table, query = st.gets[0]
        self.assertEqual(table, "contents")
        self.assertIn("or=(purpose.is.null,purpose.neq.eval)", query)   # 평가용 보존
        self.assertIn("created_at=lt.", query)
        deleted_tables = [t for t, _ in st.deletes]
        for t in ("contents", "drafts", "feedback", "eval_checks", "assignments"):
            self.assertIn(t, deleted_tables)      # 파생 연쇄(고아 배정 방지)
        for _, q in st.deletes:
            self.assertNotIn("잘못된값", q)

    def test_empty_noop(self):
        st = _make_store([])
        self.assertEqual(st.retention(days=30), 0)
        self.assertEqual(st.deletes, [])          # 대상 없으면 DELETE 미호출

    def test_drain_repeats_until_below_cap(self):
        """일 35,000건 인입 물량 대응: 반환량이 상한이면 계속, 못 미치면 그날 소진으로 종료."""
        from prism import adminops as AO

        class _St:
            def __init__(self, batches):
                self.batches, self.calls = list(batches), []

            def retention(self, days, max_rows):
                self.calls.append((days, max_rows))
                return self.batches.pop(0) if self.batches else 0

        st = _St([5000, 5000, 5000, 5000, 5000, 5000, 5000, 1200])   # 3.6만 건 잔여 시나리오
        orig = AO.time.sleep
        AO.time.sleep = lambda s: None            # 회차 간 대기 생략
        try:
            total = AO._retention_drain(st, days=90)
        finally:
            AO.time.sleep = orig
        self.assertEqual(total, 36200)
        self.assertEqual(len(st.calls), 8)        # 1,200 < 5,000 에서 멈춤
        self.assertTrue(all(c == (90, 5000) for c in st.calls))

    def test_drain_capped_by_max_calls(self):
        """첫 가동 과거분 폭탄 안전판: 하루 상한(max_calls)에서 끊고 다음 회차로 넘긴다."""
        from prism import adminops as AO

        class _St:
            def __init__(self):
                self.n = 0

            def retention(self, days, max_rows):
                self.n += 1
                return max_rows                   # 무한히 남아 있는 상황
        st = _St()
        orig = AO.time.sleep
        AO.time.sleep = lambda s: None
        try:
            total = AO._retention_drain(st, days=90, per_call=100, max_calls=5)
        finally:
            AO.time.sleep = orig
        self.assertEqual((total, st.n), (500, 5))

    def test_scheduler_opt_in(self):
        from prism import adminops as AO
        old = os.environ.pop("PRISM_RETENTION_DAYS", None)
        try:
            self.assertFalse(AO.start_retention_scheduler())      # 미설정 → 미가동
            os.environ["PRISM_RETENTION_DAYS"] = "abc"
            self.assertFalse(AO.start_retention_scheduler())      # 비정수 → 미가동
            os.environ["PRISM_RETENTION_DAYS"] = "0"
            self.assertFalse(AO.start_retention_scheduler())      # 0 → 미가동
        finally:
            if old is None:
                os.environ.pop("PRISM_RETENTION_DAYS", None)
            else:
                os.environ["PRISM_RETENTION_DAYS"] = old


if __name__ == "__main__":
    unittest.main()
