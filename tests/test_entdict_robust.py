"""엔티티 보강 견고성 테스트(2026-07-15 감사 P1-19·P2-8).

- enrich_entity: wd_entity/wd_labels 실패도 예외 보호 안에서 처리되고 브레이커가 집계한다
  (과거 이 둘은 try 밖 → 단건 500·브레이커 눈멀음).
- _retry_after_sec: Retry-After HTTP-date·음수·비수치를 안전 파싱(크래시 방지).

실행: python3 -m pytest tests/test_entdict_robust.py -q
"""
import os
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import entdict as ED           # noqa: E402
from prism.store import Store             # noqa: E402


class TestRetryAfterParse(unittest.TestCase):
    def _err(self, val):
        return urllib.error.HTTPError("u", 429, "rate", {"Retry-After": val} if val is not None else {}, None)

    def test_numeric(self):
        self.assertEqual(ED._retry_after_sec(self._err("5"), default=20, cap=60), 5)

    def test_over_cap(self):
        self.assertEqual(ED._retry_after_sec(self._err("999"), default=20, cap=60), 60)

    def test_negative_clamped_to_zero(self):
        self.assertEqual(ED._retry_after_sec(self._err("-3"), default=20, cap=60), 0)

    def test_http_date_falls_back_to_default(self):
        self.assertEqual(ED._retry_after_sec(self._err("Wed, 21 Oct 2026 07:28:00 GMT"),
                                             default=20, cap=60), 20)

    def test_missing_header_default(self):
        self.assertEqual(ED._retry_after_sec(self._err(None), default=20, cap=60), 20)


class TestEnrichWdFailureContained(unittest.TestCase):
    def setUp(self):
        self.store = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self._orig = {n: getattr(ED, n) for n in ("_namu_try", "wd_search", "wd_entity")}
        ED._wd_breaker_reset()
        ED._namu_try = lambda e: None                    # 나무위키 스킵 → 위키데이터 경로로 직행

    def tearDown(self):
        for n, f in self._orig.items():
            setattr(ED, n, f)
        ED._wd_breaker_reset()

    def _register(self, name="안세영"):
        ED.ingest_meta(self.store, [("ch1", [name])])
        return self.store.ent_id_by_alias(name)

    def test_wd_entity_failure_caught_and_counted(self):
        eid = self._register()
        ED.wd_search = lambda name: {"id": "Q1", "label": "안세영"}

        def boom(qid):
            raise urllib.error.URLError("timeout")
        ED.wd_entity = boom

        r = ED.enrich_entity(self.store, eid)
        self.assertFalse(r["ok"])                         # 예외가 밖으로 전파되지 않고 실패로 처리
        self.assertEqual(ED._WD_BREAKER["consec_fail"], 1)  # 브레이커가 이 실패를 집계(과거엔 0)

    def test_wd_entity_repeated_failures_trip_breaker(self):
        ED.wd_search = lambda name: {"id": "Q1", "label": "x"}

        def boom(qid):
            raise urllib.error.URLError("timeout")
        ED.wd_entity = boom
        for i in range(ED._WD_TRIP_FAIL):
            eid = self._register(f"안세영{i}")
            ED.enrich_entity(self.store, eid)
        self.assertTrue(ED._WD_BREAKER["tripped"])       # 연속 실패 임계 → 브레이커 tripped


if __name__ == "__main__":
    unittest.main()
