"""MCP 전송 공통 모듈(mcprpc) 회귀 테스트 (2026-08-12).

전송은 프리즘 `/mcp` 와 스펙트럼 관문이 함께 쓸 자리다. 여기서 규약이 흔들리면 두 표면이
같이 흔들리므로, 도구가 아니라 **규칙 자체**를 단언한다.

지키는 것(전부 2026-08-11 감사에서 실제로 뚫렸던 것들):
  · 인증 함수가 없거나 터지면 열지 않는다 — 키 모듈이 늦게 와도 표면이 열리면 안 된다
  · 인증 실패는 사유별로 갈리지 않는다 — 갈리면 그 차이가 열거 수단이다(H4)
  · 인증 실패는 기록되지 않는다 — 실패 연사로 실사용 감사 기록이 밀려났다(O2)
  · 도구 실행 실패는 JSON-RPC 에러가 아니라 isError 로 온다 — 모델이 읽고 스스로 고친다
  · 예외 원문·스택이 응답에 실리지 않는다(H3)

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import mcprpc


def req(method, params=None, rid=1):
    body = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    return json.dumps(body).encode()


def endpoint(**kw):
    """기본은 '항상 통과' 인증 + 도구 하나. 각 테스트가 필요한 부분만 갈아 끼운다."""
    base = {
        "name": "t", "version": "9",
        "authenticate": lambda key: {"team": "team-1", "key_id": "k1"},
        "list_tools": lambda ctx: [{"name": "echo", "description": "그대로", "inputSchema":
                                    {"type": "object", "properties": {}}}],
        "call_tool": lambda ctx, name, args, meta: {"ok": True, "name": name, "args": args},
    }
    base.update(kw)
    return mcprpc.Endpoint(**base)


class TestFailClosed(unittest.TestCase):
    def test_no_auth_function_means_closed(self):
        """인증 함수를 안 준 엔드포인트는 열린 엔드포인트가 아니다."""
        ep = mcprpc.Endpoint(name="t", authenticate=None,
                             list_tools=lambda ctx: [{"name": "x"}])
        for m in ("initialize", "tools/list", "tools/call"):
            status, body = ep.handle("Bearer whatever", req(m))
            self.assertEqual(status, 401, m)
            self.assertIn("error", body, m)

    def test_auth_function_blowing_up_does_not_open_the_door(self):
        def boom(key):
            raise RuntimeError("키 저장소 /var/secret 접속 실패")
        status, body = endpoint(authenticate=boom).handle("Bearer k", req("tools/list"))
        self.assertEqual(status, 401)
        self.assertNotIn("secret", json.dumps(body, ensure_ascii=False))

    def test_falsy_context_is_a_rejection(self):
        for bad in (None, {}, "", 0):
            status, _ = endpoint(authenticate=lambda k: bad).handle("Bearer k", req("tools/list"))
            self.assertEqual(status, 401, repr(bad))

    def test_missing_header_is_rejected(self):
        """헤더가 없으면 키가 빈 문자열로 내려가고, 인증이 그걸 거절해야 한다."""
        ep = endpoint(authenticate=lambda k: {"team": "t", "key_id": "k"} if k else None)
        self.assertEqual(ep.handle("", req("tools/list"))[0], 401)
        self.assertEqual(ep.handle("Token abc", req("tools/list"))[0], 401)   # Bearer 아님
        self.assertEqual(ep.handle("Bearer abc", req("tools/list"))[0], 200)


class TestUniformAuthFailure(unittest.TestCase):
    def test_every_reason_produces_the_same_answer(self):
        """키 없음·형식 오류·불일치·만료·폐기 — 인증 층이 어떤 모양으로 거절하든 밖은 같아야 한다."""
        def raises(_k):
            raise ValueError("키 파싱 실패")
        rejections = (lambda k: None,                     # 불일치·만료·폐기(계약상 전부 None)
                      lambda k: False,                    # 거짓 맥락
                      lambda k: {},                       # 빈 맥락
                      raises,                             # 키 모듈 고장
                      lambda k: mcprpc.Rejection())       # 명시적 거절
        seen = set()
        for auth in rejections:
            status, body = endpoint(authenticate=auth).handle("Bearer k", req("tools/list"))
            seen.add((status, json.dumps(body["error"], ensure_ascii=False)))
        self.assertEqual(len(seen), 1, "사유별로 응답이 갈리면 열거가 된다: %r" % (seen,))

    def test_method_does_not_change_the_failure_shape(self):
        ep = endpoint(authenticate=lambda k: None)
        shapes = {json.dumps(ep.handle("Bearer k", req(m))[1]["error"], ensure_ascii=False)
                  for m in ("initialize", "tools/list", "tools/call", "없는메서드")}
        self.assertEqual(len(shapes), 1)

    def test_rejection_can_still_carry_a_different_status(self):
        """레이트리밋은 인증 실패와 다른 상태를 쓸 수 있어야 한다(429)."""
        rj = mcprpc.Rejection(status=429, code=mcprpc.RATE_LIMITED, message="잠시 후")
        status, body = endpoint(authenticate=lambda k: rj).handle("Bearer k", req("tools/list"))
        self.assertEqual(status, 429)
        self.assertEqual(body["error"]["code"], mcprpc.RATE_LIMITED)

    def test_request_id_is_echoed_so_clients_can_match(self):
        status, body = endpoint(authenticate=lambda k: None).handle(
            "Bearer k", req("tools/list", rid=77))
        self.assertEqual(status, 401)
        self.assertEqual(body["id"], 77)

    def test_broken_body_from_an_unauthenticated_caller_still_looks_the_same(self):
        """미인증 호출은 본문이 무엇이든 같은 401 — 본문 파싱 결과가 새지 않는다."""
        ep = endpoint(authenticate=lambda k: None)
        a = ep.handle("Bearer k", b"{broken")
        b = ep.handle("Bearer k", req("tools/list"))
        self.assertEqual(a[0], b[0])
        self.assertEqual(a[1]["error"], b[1]["error"])


class TestNoLoggingOnAuthFailure(unittest.TestCase):
    def test_failed_auth_is_never_recorded(self):
        log = []
        ep = endpoint(authenticate=lambda k: None,
                      on_call=lambda *a: log.append(a))
        for m in ("initialize", "tools/list", "tools/call", "notifications/initialized"):
            ep.handle("Bearer bad", req(m))
        self.assertEqual(log, [], "인증 실패가 기록되면 실사용 기록이 밀려난다(감사 O2)")

    def test_successful_calls_are_recorded_with_size_and_time(self):
        log = []
        ep = endpoint(on_call=lambda ctx, tool, ok, ms, size: log.append((tool, ok, ms, size)))
        ep.handle("Bearer k", req("tools/call", {"name": "echo", "arguments": {}}))
        self.assertEqual(len(log), 1)
        tool, ok, ms, size = log[0]
        self.assertEqual(tool, "echo")
        self.assertTrue(ok)
        self.assertGreaterEqual(ms, 0)
        self.assertGreater(size, 0)

    def test_notifications_are_not_recorded(self):
        log = []
        ep = endpoint(on_call=lambda *a: log.append(a))
        status, body = ep.handle("Bearer k", req("notifications/initialized"))
        self.assertEqual((status, body), (202, None))
        self.assertEqual(log, [])

    def test_logging_failure_does_not_break_the_response(self):
        def boom(*a):
            raise RuntimeError("기록 실패")
        status, body = endpoint(on_call=boom).handle("Bearer k", req("tools/list"))
        self.assertEqual(status, 200)
        self.assertIn("result", body)


class TestProtocol(unittest.TestCase):
    def test_initialize_answers_the_client_version_and_tools_capability(self):
        status, body = endpoint().handle("Bearer k", req(
            "initialize", {"protocolVersion": "2025-11-25"}))
        self.assertEqual(status, 200)
        r = body["result"]
        self.assertEqual(r["protocolVersion"], "2025-11-25")   # 클라이언트 값 우선
        self.assertEqual(r["capabilities"], {"tools": {}})
        self.assertEqual(r["serverInfo"], {"name": "t", "version": "9"})

    def test_initialize_without_a_version_falls_back_to_ours(self):
        body = endpoint().handle("Bearer k", req("initialize", {}))[1]
        self.assertEqual(body["result"]["protocolVersion"], mcprpc.PROTOCOL_VERSION)
        self.assertEqual(mcprpc.PROTOCOL_VERSION, "2025-06-18")

    def test_instructions_are_optional(self):
        self.assertNotIn("instructions", endpoint().handle("Bearer k", req("initialize"))[1]["result"])
        r = endpoint(instructions="먼저 tools/list").handle("Bearer k", req("initialize"))[1]["result"]
        self.assertEqual(r["instructions"], "먼저 tools/list")

    def test_ping_is_answered(self):
        status, body = endpoint().handle("Bearer k", req("ping"))
        self.assertEqual((status, body["result"]), (200, {}))

    def test_tools_list_passes_the_layer_above_through(self):
        body = endpoint().handle("Bearer k", req("tools/list"))[1]
        self.assertEqual([t["name"] for t in body["result"]["tools"]], ["echo"])

    def test_unknown_method_is_a_jsonrpc_error_with_guidance(self):
        status, body = endpoint().handle("Bearer k", req("resources/list"))
        self.assertEqual(status, 200)
        self.assertEqual(body["error"]["code"], mcprpc.METHOD_NOT_FOUND)
        self.assertIn("tools/list", body["error"]["message"])       # 다음 행동을 알려준다

    def test_bad_json_from_an_authenticated_caller_is_a_parse_error(self):
        status, body = endpoint().handle("Bearer k", b"{broken")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], mcprpc.PARSE_ERROR)

    def test_batch_requests_are_refused(self):
        status, body = endpoint().handle("Bearer k", b"[{}, {}]")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], mcprpc.INVALID_REQUEST)

    def test_no_session_id_is_ever_issued(self):
        """무세션이 설계 전제다 — 세션을 들면 배포마다 클라이언트가 끊긴다."""
        body = endpoint().handle("Bearer k", req("initialize"))[1]
        self.assertNotIn("sessionId", json.dumps(body))
        self.assertNotIn("Mcp-Session-Id", json.dumps(body))


class TestToolFailuresStayInsideTheResult(unittest.TestCase):
    def test_tool_error_is_not_a_jsonrpc_error(self):
        ep = endpoint(call_tool=lambda ctx, n, a, m: {"error": "그런 해시가 없습니다"})
        status, body = ep.handle("Bearer k", req("tools/call", {"name": "x"}))
        self.assertEqual(status, 200)
        self.assertNotIn("error", body)                 # JSON-RPC 에러가 아니다
        self.assertTrue(body["result"]["isError"])
        self.assertIn("그런 해시가 없습니다", body["result"]["content"][0]["text"])

    def test_success_carries_no_is_error_flag(self):
        body = endpoint().handle("Bearer k", req("tools/call", {"name": "echo"}))[1]
        self.assertNotIn("isError", body["result"])
        self.assertEqual(json.loads(body["result"]["content"][0]["text"])["ok"], True)

    def test_explicit_tuple_result_wins_over_the_error_key_guess(self):
        ep = endpoint(call_tool=lambda ctx, n, a, m: ({"note": "비었지만 정상"}, True))
        self.assertTrue(ep.handle("Bearer k", req("tools/call", {"name": "x"}))[1]["result"]["isError"])

    def test_tool_exception_becomes_is_error_without_leaking_internals(self):
        def boom(ctx, n, a, m):
            raise RuntimeError("내부 경로 /secret/path 노출")
        status, body = endpoint(call_tool=boom).handle("Bearer k", req("tools/call", {"name": "x"}))
        self.assertEqual(status, 200)
        self.assertTrue(body["result"]["isError"])
        self.assertNotIn("secret", json.dumps(body, ensure_ascii=False))

    def test_arguments_and_meta_reach_the_tool_layer(self):
        seen = {}

        def spy(ctx, name, args, meta):
            seen.update({"name": name, "args": args, "meta": meta, "team": ctx["team"]})
            return {"ok": True}
        endpoint(call_tool=spy).handle("Bearer k", req("tools/call", {
            "name": "echo", "arguments": {"a": 1}, "_meta": {"progressToken": 2}}))
        self.assertEqual(seen["args"], {"a": 1})
        self.assertEqual(seen["meta"], {"progressToken": 2})
        self.assertEqual(seen["team"], "team-1")        # 팀은 인증 맥락에서만 온다

    def test_malformed_arguments_are_normalised_not_crashed(self):
        seen = {}
        endpoint(call_tool=lambda c, n, a, m: seen.update(args=a) or {"ok": 1}).handle(
            "Bearer k", req("tools/call", {"name": "echo", "arguments": "문자열"}))
        self.assertEqual(seen["args"], {})


class TestHandleBodyEntry(unittest.TestCase):
    """스펙트럼 관문처럼 MCP·REST 를 한 경로에서 가르는 호출자를 위한 진입점."""

    def test_parsed_body_entry_shares_the_same_rules(self):
        ep = endpoint()
        status, body = ep.handle_body("k", {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        self.assertEqual((status, body["id"]), (200, 3))
        status, body = endpoint(authenticate=lambda k: None).handle_body(
            "k", {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        self.assertEqual(status, 401)
        self.assertEqual(body["id"], 3)


if __name__ == "__main__":
    unittest.main()
