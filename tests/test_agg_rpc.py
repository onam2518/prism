"""집계 RPC 폴백 회귀(P3-1 1단계).

RPC 가용 시 통계는 서버측 집계 결과를 그대로 쓰고(행 전송 0), 미가용(마이그레이션 전)이면
기존 행 다운로드 계산으로 폴백하며, 실패한 함수는 프로세스당 1회만 시도한다.
RPC와 파이썬 계산의 수치 동일성은 운영 DB 전 팀 파리티 검증(2026-07-29)으로 확인됨.

실행: python3 -m pytest tests/test_agg_rpc.py -q
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism.supastore import SupabaseStore   # noqa: E402

ROWS = [
    {"content_hash": "h1", "reviewer_id": "r1", "verdict": "good", "note": "", "reap_plan": ""},
    {"content_hash": "h1", "reviewer_id": "r2", "verdict": "bad", "note": "이유", "reap_plan": ""},
    {"content_hash": "h2", "reviewer_id": "r1", "verdict": "bad", "note": "", "reap_plan": ""},
]


def _mk(rpc_status, rpc_body):
    """소켓 없이 메서드만: _http 를 가짜로 갈아끼운 SupabaseStore."""
    st = SupabaseStore.__new__(SupabaseStore)
    st.url = "https://x.supabase.co"
    st.base = st.url + "/rest/v1"
    st.key = "k"
    st.calls = {"rpc": 0, "get": 0}

    def _http(method, path, data, headers):
        if "/rpc/" in path:
            st.calls["rpc"] += 1
            return rpc_status, rpc_body, {}
        st.calls["get"] += 1
        return 200, json.dumps(ROWS), {}

    st._http = _http
    return st


class TestAggRpc(unittest.TestCase):
    def setUp(self):
        SupabaseStore._RPC_MISSING = set()          # 클래스 공유 상태 격리

    tearDown = setUp

    def test_rpc_hit_skips_row_download(self):
        agg = {"total": 3, "good": 1, "bad": 2, "learned": 1,
               "contents": 2, "reviewers": 2, "split": 1}
        st = _mk(200, json.dumps(agg))
        self.assertEqual(st.feedback_stats("t"), agg)
        self.assertEqual(st.calls, {"rpc": 1, "get": 0})   # 행 전송 없음

    def test_rpc_missing_falls_back_once(self):
        st = _mk(404, "")
        out = st.feedback_stats("t")
        self.assertEqual(out["total"], 3)                  # 폴백 = 기존 행 계산
        self.assertEqual(out["good"], 1)
        self.assertEqual(out["bad"], 2)
        self.assertEqual(out["learned"], 1)                # bad + note 있는 행
        self.assertEqual(out["split"], 1)                  # h1 = good+bad
        self.assertEqual(st.calls["rpc"], 1)
        st.feedback_stats("t")
        self.assertEqual(st.calls["rpc"], 1)               # 미존재 기억 → 재시도 없음

    def test_rows_param_bypasses_rpc(self):
        st = _mk(200, json.dumps({"total": 999}))
        out = st.feedback_stats("t", rows=ROWS)            # 행이 이미 손에 있으면 RPC 불필요
        self.assertEqual(out["total"], 3)
        self.assertEqual(st.calls["rpc"], 0)

    def test_gold_stats_rpc_hit(self):
        agg = {"r1": {"n": 6, "correct": 5, "acc": 0.8333}}
        st = _mk(200, json.dumps(agg))
        self.assertEqual(st.gold_stats("t"), agg)
        self.assertEqual(st.calls["get"], 0)


if __name__ == "__main__":
    unittest.main()
