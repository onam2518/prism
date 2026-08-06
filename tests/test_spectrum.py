"""스펙트럼(사내 MCP 허브 프로토타입) 계약 테스트.

키 수명주기(발급 · 마스킹 · 폐기 · 만료) · 관문 코어(인증 → 권한 → 실행 → 사용 기록) ·
MCP 최소 구현(JSON-RPC) · 지표 · 라우트 게이트를 검증한다.

실행: python3 -m pytest tests/test_spectrum.py -q
"""
import datetime
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import spectrumops as spo


class SpectrumBase(unittest.TestCase):
    """테스트마다 저장 파일을 임시 경로로 격리한다(spectrumops 는 호출 시마다 env 를 본다)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._old = os.environ.get("PRISM_SPECTRUM_PATH")
        os.environ["PRISM_SPECTRUM_PATH"] = os.path.join(self._tmp, "spectrum.json")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("PRISM_SPECTRUM_PATH", None)
        else:
            os.environ["PRISM_SPECTRUM_PATH"] = self._old

    # 자주 쓰는 준비물: 유효 키 하나
    def _key(self, user="tester@t", label="테스트"):
        out = spo.issue_key(user, label)
        self.assertTrue(out["ok"], out)
        return out


class TestKeys(SpectrumBase):
    def test_issue_key_format_and_once(self):
        out = self._key()
        self.assertTrue(out["key"].startswith("spk_"))
        self.assertEqual(len(out["key"]), 4 + 32)                  # spk_ + 32자 hex
        int(out["key"][4:], 16)                                    # hex 가 아니면 여기서 터진다
        self.assertIn("…", out["masked"])
        self.assertTrue(out["expiresAtText"])

    def test_issue_requires_user_and_ttl_bounds(self):
        self.assertFalse(spo.issue_key("")["ok"])
        out = spo.issue_key("u@t", ttl_days=0)                     # 0(비움)은 기본 90일로
        self.assertTrue(out["ok"])
        self.assertFalse(spo.issue_key("u@t", ttl_days=-5)["ok"])
        self.assertFalse(spo.issue_key("u@t", ttl_days=spo.MAX_TTL_DAYS + 1)["ok"])
        self.assertFalse(spo.issue_key("u@t", ttl_days="abc")["ok"])

    def test_key_cap_per_user(self):
        for _ in range(spo.MAX_KEYS_PER_USER):
            self.assertTrue(spo.issue_key("cap@t")["ok"])
        out = spo.issue_key("cap@t")
        self.assertFalse(out["ok"])
        self.assertIn("폐기", out["error"])
        # 다른 사용자는 상한과 무관
        self.assertTrue(spo.issue_key("other@t")["ok"])

    def test_full_key_never_in_screen_data(self):
        out = self._key()
        blob = json.dumps(spo.spectrum_data("tester@t"), ensure_ascii=False)
        self.assertNotIn(out["key"], blob)                         # 전체 키는 발급 응답 1회뿐
        self.assertIn(out["masked"], blob)

    def test_screen_data_lists_only_my_keys(self):
        self._key(user="a@t")
        self._key(user="b@t")
        keys = spo.spectrum_data("a@t")["keys"]
        self.assertEqual(len(keys), 1)

    def test_revoke_only_own_key(self):
        out = self._key()
        self.assertFalse(spo.revoke_key("someone@else", out["id"])["ok"])
        self.assertTrue(spo.revoke_key("tester@t", out["id"])["ok"])
        status, body = spo.call("list_tables", key=out["key"])
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "revoked_key")

    def test_expired_key_401(self):
        out = self._key()
        path = spo.state_path()                                    # 저장 파일을 직접 과거로 조작
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        st["keys"][0]["expiresAt"] = 1.0
        with open(path, "w", encoding="utf-8") as f:
            json.dump(st, f)
        status, body = spo.call("list_tables", key=out["key"])
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "expired_key")

    def test_invalid_key_401(self):
        status, body = spo.call("list_tables", key="spk_" + "0" * 32)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "invalid_key")

    def test_concurrent_issue_no_loss(self):
        errs = []

        def worker(i):
            try:
                assert spo.issue_key("con@t", "k%d" % i)["ok"]
            except Exception as e:                                 # noqa: BLE001 · 실패 수집용
                errs.append(e)

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertFalse(errs)
        self.assertEqual(len(spo.spectrum_data("con@t")["keys"]), 8)


class TestGatewayRest(SpectrumBase):
    def test_list_tables(self):
        k = self._key()["key"]
        status, body = spo.call("list_tables", key=k)
        self.assertEqual(status, 200)
        names = [t["name"] for t in body["result"]["tables"]]
        self.assertIn("gold.user_action_log", names)
        self.assertTrue(all(t["fields"] for t in body["result"]["tables"]))

    def test_query_logs_filter_limit_deterministic(self):
        k = self._key()["key"]
        p = {"action": "click", "limit": 5}
        s1, b1 = spo.call("query_logs", p, key=k)
        s2, b2 = spo.call("query_logs", p, key=k)
        self.assertEqual(s1, 200)
        r1 = b1["result"]
        self.assertLessEqual(len(r1["rows"]), 5)
        self.assertTrue(all(r["action"] == "click" for r in r1["rows"]))
        self.assertEqual(r1["rows"], b2["result"]["rows"])         # 같은 요청 = 같은 결과
        self.assertGreaterEqual(r1["total"], len(r1["rows"]))

    def test_list_tables_lakehouse_meta(self):
        """DB 커넥터 계약: 벨루가 골드 레이어 메타(계층·형식·저장소·파티션·스냅샷)를 함께 준다."""
        k = self._key()["key"]
        status, body = spo.call("list_tables", key=k)
        r = body["result"]
        self.assertEqual((r["catalog"], r["layer"], r["format"], r["storage"]),
                         ("beluga", "gold", "iceberg", "s3"))
        self.assertIn("골드", r["note"])                            # 골드만 연다는 안내
        for t in r["tables"]:
            self.assertEqual((t["layer"], t["format"]), ("gold", "iceberg"))
            self.assertTrue(t["partitionBy"])
            self.assertTrue(t["timeTravel"] and t["snapshotId"] and t["snapshotAt"])
        live = [c for c in spo.catalog() if c["status"] == "live"][0]
        self.assertEqual(live["warehouse"]["name"], "beluga")
        self.assertEqual(live["warehouse"]["layer"], "gold")

    def test_query_logs_time_travel_as_of(self):
        """Iceberg 타임 트래블: as_of 이후에 쌓인 줄은 안 나오고 스냅샷 번호는 날짜마다 고정."""
        k = self._key()["key"]
        as_of = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
        s1, b1 = spo.call("query_logs", {"as_of": as_of, "limit": 100}, key=k)
        s2, b2 = spo.call("query_logs", {"limit": 100}, key=k)
        past, now = b1["result"], b2["result"]
        self.assertTrue(all(row["ts"][:10] <= as_of for row in past["rows"]))
        self.assertLess(past["total"], now["total"])                # 과거 스냅샷이 더 적다
        self.assertTrue(past["snapshot"]["timeTravel"])
        self.assertFalse(now["snapshot"]["timeTravel"])
        self.assertEqual(past["snapshot"]["asOf"], as_of)
        s3, b3 = spo.call("query_logs", {"as_of": as_of}, key=k)
        self.assertEqual(b3["result"]["snapshot"]["id"], past["snapshot"]["id"])   # 같은 시점 = 같은 번호
        self.assertNotEqual(past["snapshot"]["id"], now["snapshot"]["id"])

    def test_agg_metrics_series(self):
        k = self._key()["key"]
        status, body = spo.call("agg_metrics", {"metric": "dau", "days": 7}, key=k)
        self.assertEqual(status, 200)
        self.assertEqual(len(body["result"]["series"]), 7)
        # 잘못된 metric 은 도구 안 오류 → 400 bad_params
        status, body = spo.call("agg_metrics", {"metric": "pageviews"}, key=k)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "bad_params")

    def test_search_terms_forbidden_403(self):
        k = self._key()["key"]
        status, body = spo.call("query_search_terms", {"q": "날씨"}, key=k)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "forbidden")
        self.assertIn("별도 권한", body["detail"])

    def test_unknown_tool_400_with_tool_list(self):
        k = self._key()["key"]
        status, body = spo.call("drop_tables", key=k)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "unknown_tool")
        self.assertIn("query_logs", body["tools"])

    def test_gateway_request_bearer_and_body_key(self):
        k = self._key()["key"]
        # Authorization 헤더 우선
        status, out = spo.gateway_request("Bearer " + k, json.dumps({"tool": "list_tables"}).encode())
        self.assertEqual(status, 200)
        # 헤더가 없으면 body.key 폴백
        status, out = spo.gateway_request("", json.dumps({"tool": "list_tables", "key": k}).encode())
        self.assertEqual(status, 200)
        self.assertTrue(out["ok"])

    def test_gateway_request_bad_json(self):
        status, out = spo.gateway_request("", b"{broken")
        self.assertEqual(status, 400)
        self.assertEqual(out["error"], "bad_json")
        status, out = spo.gateway_request("", b"[1,2]")
        self.assertEqual(status, 400)


class TestGatewayMcp(SpectrumBase):
    def _rpc(self, key, method, params=None, rid=1):
        body = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            body["params"] = params
        return spo.gateway_request("Bearer " + key, json.dumps(body).encode())

    def test_initialize_echoes_protocol(self):
        k = self._key()["key"]
        status, out = self._rpc(k, "initialize", {"protocolVersion": "2025-03-26"})
        self.assertEqual(status, 200)
        self.assertEqual(out["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(out["result"]["serverInfo"]["name"], "spectrum-gateway")
        # 클라이언트가 버전을 안 보내면 서버 기본값
        status, out = self._rpc(k, "initialize")
        self.assertEqual(out["result"]["protocolVersion"], spo.PROTOCOL_VERSION)

    def test_notification_202_empty(self):
        k = self._key()["key"]
        status, out = self._rpc(k, "notifications/initialized")
        self.assertEqual(status, 202)
        self.assertIsNone(out)

    def test_tools_list(self):
        k = self._key()["key"]
        status, out = self._rpc(k, "tools/list")
        self.assertEqual(status, 200)
        tools = out["result"]["tools"]
        self.assertEqual(len(tools), 4)
        self.assertTrue(all("inputSchema" in t for t in tools))

    def test_tools_call_ok_and_error(self):
        k = self._key()["key"]
        status, out = self._rpc(k, "tools/call", {"name": "query_logs", "arguments": {"limit": 3}})
        self.assertEqual(status, 200)
        parsed = json.loads(out["result"]["content"][0]["text"])   # text = JSON 문자열
        self.assertLessEqual(len(parsed["rows"]), 3)
        # 막힌 도구는 isError 로 결과 안에 담는다(MCP 규약)
        status, out = self._rpc(k, "tools/call", {"name": "query_search_terms", "arguments": {}})
        self.assertEqual(status, 200)
        self.assertTrue(out["result"]["isError"])

    def test_unknown_method_and_invalid_key(self):
        k = self._key()["key"]
        status, out = self._rpc(k, "resources/list")
        self.assertEqual(status, 200)
        self.assertEqual(out["error"]["code"], -32601)
        status, out = self._rpc("spk_" + "f" * 32, "tools/list")
        self.assertEqual(status, 401)
        self.assertEqual(out["error"]["code"], -32001)


class TestUsageMetrics(SpectrumBase):
    def test_usage_recorded_and_metrics(self):
        k = self._key()
        spo.call("query_logs", {"limit": 1}, key=k["key"])
        spo.call("query_search_terms", key=k["key"])
        data = spo.spectrum_data("tester@t")
        usage = data["usage"]
        self.assertEqual(usage[0]["tool"], "query_search_terms")   # 최신순
        self.assertFalse(usage[0]["ok"])
        self.assertIn("별도 권한", usage[0]["note"])               # 실패 사유가 note 에 남는다
        m = data["metrics"]
        self.assertEqual(m["weekCalls"], 2)
        self.assertEqual(m["weekUsers"], 1)
        self.assertEqual(m["activeKeys"], 1)
        self.assertAlmostEqual(m["failRate"], 0.5)
        # 키 쪽에도 사용 흔적
        self.assertEqual(data["keys"][0]["calls"], 1)              # 403 은 calls 미증가
        self.assertGreater(data["keys"][0]["lastUsedAt"], 0)

    def test_reset_demo_keeps_keys(self):
        k = self._key()
        spo.call("list_tables", key=k["key"])
        out = spo.reset_demo()
        self.assertTrue(out["ok"])
        data = spo.spectrum_data("tester@t")
        self.assertEqual(data["usage"], [])
        self.assertEqual(len(data["keys"]), 1)

    def test_gw_try_screen_action(self):
        kid = self._key()["id"]
        ok = spo.spectrum_action({"action": "gw_try", "keyId": kid,
                                  "tool": "list_tables", "params": {}}, "tester@t")
        self.assertTrue(ok["ok"])
        self.assertTrue(ok["permitted"])
        self.assertIn("tables", ok["result"])
        no = spo.spectrum_action({"action": "gw_try", "keyId": kid,
                                  "tool": "query_search_terms", "params": {}}, "tester@t")
        self.assertFalse(no["ok"])
        self.assertFalse(no["permitted"])
        self.assertEqual(no["status"], 403)
        self.assertIn("별도 권한", no["detail"])
        # 남의 키 id 로는 체험 불가(소유자 대조)
        other = spo.spectrum_action({"action": "gw_try", "keyId": kid,
                                     "tool": "list_tables", "params": {}}, "else@t")
        self.assertEqual(other["status"], 401)

    def test_unknown_action(self):
        self.assertFalse(spo.spectrum_action({"action": "explode"}, "t@t")["ok"])


class TestServeGates(SpectrumBase):
    def test_menu_gate_and_public(self):
        from prism import serve
        self.assertEqual(serve._menu_for_path("/spectrum"), "lab")
        self.assertIsNone(serve._menu_for_path("/spectrum-gw"))    # 관문은 메뉴 권한 예외
        self.assertIn("/spectrum-gw", serve._PUBLIC_GET)           # 안내 GET 은 공개

    def test_catalog_shape(self):
        cat = spo.catalog()
        live = [c for c in cat if c["status"] == "live"]
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]["id"], "user-logs")
        self.assertEqual(len(live[0]["tools"]), 4)
        sj = live[0]["serverJson"]
        self.assertTrue(sj["name"].startswith("net.daumkakao.spectrum/"))
        self.assertEqual(sj["remotes"][0]["type"], "streamable-http")
        # 준비중 커넥터도 카탈로그 형태(요약 · 담당)를 갖춘다
        for c in cat:
            self.assertTrue(c["summary"] and c["owner"])


if __name__ == "__main__":
    unittest.main()
