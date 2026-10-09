"""백엔드 성능 P1-A 회귀 테스트.

  1. supabase keep-alive 연결 풀: 스레드가 바뀌어도 연결 재사용 · 오래 쉰 연결 폐기 · 오류 연결 미반환
  2. 집계 캐시 single-flight: 같은 키 동시 미스는 한 번만 계산 · 예외는 캐시하지 않음
  3. 만료 캐시 항목은 쓰기마다 회수 · CSV 내보내기는 캐시 우회
  4. 롤업 원장: 연속 갱신은 원장 GET 1회 · 저장 실패해도 증분 유지
  5. 정답셋 1,000건 상한 해제(supabase 페이징 · sqlite 동일 계약)

실행: python3 -m pytest tests/test_perf_p1_backend.py -q
"""
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import serve as SV            # noqa: E402
from prism import dashops as DO          # noqa: E402
from prism.supastore import SupabaseStore  # noqa: E402


class _Resp:
    status = 200
    will_close = False

    def read(self):
        return b"[]"

    def getheaders(self):
        return []


class _FakeConn:
    made = []

    def __init__(self, host, timeout=None):
        self.host, self.fail, self.closed, self.n = host, False, False, 0
        _FakeConn.made.append(self)

    def request(self, method, path, body=None, headers=None):
        self.n += 1
        if self.fail:
            raise ConnectionError("reset")

    def getresponse(self):
        return _Resp()

    def close(self):
        self.closed = True


def _supa():
    st = SupabaseStore.__new__(SupabaseStore)
    st.url = "https://x.supabase.co"
    st.base = st.url + "/rest/v1"
    st.key = "k"
    return st


class TestConnPool(unittest.TestCase):
    def setUp(self):
        _FakeConn.made = []
        for p in (mock.patch.object(SupabaseStore, "_POOL", {}),
                  mock.patch("prism.supastore.http.client.HTTPSConnection", _FakeConn)):
            p.start()
            self.addCleanup(p.stop)

    def _call_in_thread(self, st, method="GET"):
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("r", st._http(method, "/rest/v1/x", None, {})))
        t.start()
        t.join()
        return out.get("r")

    def test_reused_across_threads(self):
        st = _supa()
        for _ in range(3):                       # 요청마다 새 스레드(ThreadingHTTPServer 흉내)
            self.assertEqual(self._call_in_thread(st)[0], 200)
        self.assertEqual(len(_FakeConn.made), 1)
        self.assertEqual(_FakeConn.made[0].n, 3)

    def test_idle_expired_connection_discarded(self):
        st = _supa()
        st._http("GET", "/a", None, {})
        old = _FakeConn.made[0]
        host = "x.supabase.co"
        SupabaseStore._POOL[host] = [(old, time.monotonic() - SupabaseStore._POOL_IDLE - 1)]
        st._http("POST", "/a", b"{}", {})
        self.assertTrue(old.closed)
        self.assertEqual(old.n, 1)               # 오래 쉰 소켓에 쓰기를 보내지 않는다
        self.assertEqual(len(_FakeConn.made), 2)

    def test_failed_connection_not_returned(self):
        st = _supa()
        st._http("GET", "/a", None, {})
        bad = _FakeConn.made[0]
        bad.fail = True
        self.assertEqual(st._http("GET", "/a", None, {})[0], 200)   # GET 은 새 연결로 1회 재시도
        self.assertTrue(bad.closed)
        pooled = [c for c, _ in SupabaseStore._POOL["x.supabase.co"]]
        self.assertNotIn(bad, pooled)
        pooled[0].fail = True
        with self.assertRaises(ConnectionError):  # 쓰기는 자동 재전송 금지
            st._http("POST", "/a", b"{}", {})
        self.assertEqual(SupabaseStore._POOL["x.supabase.co"], [])

    def test_pool_bounded(self):
        for _ in range(SupabaseStore._POOL_MAX + 3):
            SupabaseStore._give_conn("h", _FakeConn("h"))
        self.assertEqual(len(SupabaseStore._POOL["h"]), SupabaseStore._POOL_MAX)
        self.assertTrue(_FakeConn.made[-1].closed)


class TestAggCache(unittest.TestCase):
    def setUp(self):
        SV._AGG_CACHE.clear()
        self.addCleanup(SV._AGG_CACHE.clear)

    def test_single_flight(self):
        calls = []

        def compute():
            calls.append(1)
            time.sleep(0.1)
            return {"v": 1}

        res = []
        ts = [threading.Thread(target=lambda: res.append(SV._agg_cached(("sf", "t"), compute)))
              for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(res), 8)
        self.assertTrue(all(r is res[0] for r in res))

    def test_exception_not_cached_and_lock_released(self):
        def boom():
            raise ValueError("x")
        with self.assertRaises(ValueError):
            SV._agg_cached(("ex",), boom)
        self.assertNotIn(("ex",), SV._AGG_CACHE)
        self.assertEqual(SV._agg_cached(("ex",), lambda: 7), 7)

    def test_expired_swept_on_every_write(self):
        past = time.time() - 1
        for i in range(3):                       # 256 미만이어도 만료분은 회수
            SV._AGG_CACHE[("rows", i)] = (past, SV._AGG_VERSION, [i])
        SV._agg_cached(("new",), lambda: 1)
        self.assertEqual(list(SV._AGG_CACHE), [("new",)])

    def test_csv_export_bypasses_cache(self):
        class St:                                # 약참조 가능한 원격 스토어 흉내
            REMOTE = True

            def recent(self, limit, team=None):
                return []
        st = St()
        with mock.patch.object(SV, "get_store", lambda: st):
            DO.build_results_csv(team="t")
            SV.results_rows(team="t")
        self.assertEqual([k[0] for k in SV._AGG_CACHE], ["rows"])
        self.assertNotIn(("rows", "t", DO.CSV_MAX_ROWS + 1), SV._AGG_CACHE)


class TestLedger(unittest.TestCase):
    def test_one_get_and_failed_save_keeps_increment(self):
        gets, saved = [], []

        class St:
            def get_report(self, kind, team=None):
                gets.append(kind)
                return {"days": {}}

        st = St()
        fail = {"on": True}

        def save(kind, payload, team=None):
            if fail["on"]:
                return                           # serve._report_save 처럼 실패를 삼킨다
            saved.append(payload)

        fake = types.SimpleNamespace(get_store=lambda: st, _report_save=save)
        with mock.patch.object(DO, "_SV", fake), mock.patch.object(DO, "_LEDGER", {}):
            DO._log_activity_rollup(team="t", reviews=1)
            fail["on"] = False
            DO._log_activity_rollup(team="t", reviews=1)
            DO._log_activity_rollup(team="t", reviews=1)
        self.assertEqual(gets, ["activity_rollup"])
        self.assertEqual(sum(d["reviews"] for d in saved[-1]["days"].values()), 3)


class TestGoldenUncapped(unittest.TestCase):
    def test_supabase_get_golden_pages_past_1000(self):
        st = _supa()
        queries = []

        def fake_req(method, table, query="", body=None, prefer=""):
            queries.append(query)
            off = int(query.split("offset=")[1])
            lim = int(query.split("limit=")[1].split("&")[0])
            n = max(0, min(lim, 2500 - off))
            return [{"content": {"i": off + i}, "expected": {}} for i in range(n)]

        st._req = fake_req
        self.assertEqual(len(st.get_golden("t")), 2500)
        self.assertEqual(len(st.golden_entries("t")), 2500)
        self.assertEqual(len(st.get_golden("t", limit=10)), 10)

    def test_sqlite_get_golden_uncapped(self):
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        c = st._conn()
        c.executemany("INSERT INTO golden(content_hash,content,expected) VALUES(?,?,?)",
                      [(f"h{i}", json.dumps({"i": i}), "{}") for i in range(1005)])
        c.commit()
        self.assertEqual(len(st.get_golden()), 1005)
        self.assertEqual(len(st.golden_entries()), 1005)
        self.assertEqual(len(st.get_golden(limit=5)), 5)


if __name__ == "__main__":
    unittest.main()
