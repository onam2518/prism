"""프리즘 MCP 서버(mcpserver · 트랙 B) 회귀 테스트 (2026-08-12).

전송 규약은 test_mcprpc 가 본다. 여기서는 **프리즘 도구를 얹은 층**이 지켜야 할 것을 본다.

  · 파트너 키 모듈이 없으면 표면이 통째로 닫힌다(다른 세션이 만드는 중 · 늦게 와도 안 열림)
  · team 은 키에서만 온다 — 인자로 못 받고, 어떤 도구 스키마에도 없다(감사 H1)
  · external 로 안 연 도구는 목록에도 없고 불러도 안 돌아간다
  · 인증 실패·레이트리밋은 사용 기록에 적재되지 않는다(감사 O2)
  · 도구 실행 실패는 JSON-RPC 에러가 아니라 isError 로 온다

키 모듈은 아직 없다. 계약 모양의 가짜로 세운다:
    resolve(raw_key)   -> {"user_id","team","key_id","is_admin"} | None
    rate_check(key_id) -> (allowed, retry_after_sec)
    log_call(key_id, tool, ok, ms, resp_bytes) -> None

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("PRISM_DB", os.path.join(tempfile.mkdtemp(), "t.db"))
os.environ.setdefault("PRISM_BACKEND", "sqlite")

from prism import mcprpc
from prism import mcpserver as MS
from prism import prismtools as PT

GOOD_KEY = "pmk_good"
TEAM = "team-1"


class FakeKeys:
    """계약 모양의 가짜 키 모듈. 인증 실패 사유는 밖에서 구분할 수 없어야 한다."""

    def __init__(self, allowed=True, retry=0, is_admin=False, team=TEAM):
        self.calls, self.allowed, self.retry = [], allowed, retry
        self.is_admin, self.team = is_admin, team

    def resolve(self, raw_key):
        if raw_key != GOOD_KEY:                    # 없음·형식오류·불일치·만료·폐기 = 전부 None
            return None
        return {"user_id": "u1", "team": self.team, "key_id": "k1", "is_admin": self.is_admin}

    def rate_check(self, key_id):
        return self.allowed, self.retry

    def log_call(self, key_id, tool, ok, ms, resp_bytes):
        self.calls.append((key_id, tool, ok, ms, resp_bytes))


def req(method, params=None, rid=1):
    body = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    return json.dumps(body).encode()


class Base(unittest.TestCase):
    def use(self, keys):
        MS._KEYS = keys
        self.addCleanup(lambda: setattr(MS, "_KEYS", None))
        return keys

    def call(self, method, params=None, key=GOOD_KEY):
        return MS.handle("Bearer " + key if key else "", req(method, params))

    def tool(self, name, args=None, key=GOOD_KEY):
        return self.call("tools/call", {"name": name, "arguments": args or {}}, key=key)


class TestFailClosedWithoutKeyModule(Base):
    def test_everything_is_401_when_the_key_module_is_missing(self):
        """키 모듈은 다른 세션이 만든다. 없는 채로 배포돼도 열리면 안 된다."""
        MS._KEYS = None
        self.addCleanup(lambda: setattr(MS, "_KEYS", None))
        orig = MS._import_keys
        MS._import_keys = lambda: (_ for _ in ()).throw(ImportError("mcpkeys 없음"))
        self.addCleanup(lambda: setattr(MS, "_import_keys", orig))
        self.assertIsNone(MS.keys())
        for m in ("initialize", "tools/list", "tools/call", "ping"):
            status, body = self.call(m)
            self.assertEqual(status, 401, m)
            self.assertEqual(body["error"]["message"], mcprpc.UNAUTHORIZED_TEXT, m)

    def test_a_module_that_misses_the_contract_counts_as_missing(self):
        class Half:                                 # log_call 이 없다 = 기록 없이 열리는 표면
            def resolve(self, k):
                return {"user_id": "u", "team": TEAM, "key_id": "k", "is_admin": True}

            def rate_check(self, k):
                return True, 0
        MS._KEYS = None
        self.addCleanup(lambda: setattr(MS, "_KEYS", None))
        orig, MS._import_keys = MS._import_keys, lambda: Half()
        self.addCleanup(lambda: setattr(MS, "_import_keys", orig))
        self.assertIsNone(MS.keys())
        self.assertEqual(self.call("tools/list")[0], 401)

    def test_a_key_module_that_blows_up_does_not_open_the_door(self):
        class Broken(FakeKeys):
            def resolve(self, raw_key):
                raise RuntimeError("supabase 접속 실패 · /secret/dsn")
        self.use(Broken())
        status, body = self.call("tools/list")
        self.assertEqual(status, 401)
        self.assertNotIn("secret", json.dumps(body, ensure_ascii=False))

    def test_a_key_without_a_team_is_refused(self):
        """team 이 falsy 면 저장 계층이 '전 팀' 으로 읽는다(감사 H1) — 여기서 끊는다."""
        for team in ("", "   ", None):
            self.use(FakeKeys(team=team))
            self.assertEqual(self.call("tools/list")[0], 401, repr(team))
            MS._KEYS = None


class TestAuthFailureIsUniformAndUnlogged(Base):
    def test_wrong_keys_all_look_the_same(self):
        self.use(FakeKeys())
        shapes = set()
        for key in ("", "pmk_wrong", "가짜", "pmk_", "Bearer"):
            status, body = self.call("tools/list", key=key)
            shapes.add((status, json.dumps(body["error"], ensure_ascii=False)))
        self.assertEqual(len(shapes), 1, "사유별로 갈리면 키 열거가 된다: %r" % (shapes,))

    def test_failed_auth_is_not_written_to_the_usage_log(self):
        fake = self.use(FakeKeys())
        for _ in range(20):
            self.call("tools/list", key="pmk_wrong")
        self.assertEqual(fake.calls, [], "틀린 키 연사가 실사용 기록을 밀어낸다(감사 O2)")

    def test_rate_limited_calls_are_refused_and_not_logged(self):
        fake = self.use(FakeKeys(allowed=False, retry=42))
        status, body = self.call("tools/list")
        self.assertEqual(status, 429)
        self.assertEqual(body["error"]["code"], mcprpc.RATE_LIMITED)
        self.assertIn("42", body["error"]["message"])     # 언제 다시 부를지 알려준다
        self.assertEqual(fake.calls, [])

    def test_successful_calls_are_logged_with_the_tool_name(self):
        fake = self.use(FakeKeys())
        self.tool("get_taxonomy", {"kind": "intent"})
        self.assertEqual(len(fake.calls), 1)
        key_id, tool, ok, ms, size = fake.calls[0]
        self.assertEqual((key_id, tool, ok), ("k1", "get_taxonomy", True))
        self.assertGreater(size, 0)


class TestToolSurface(Base):
    def setUp(self):
        self.fake = self.use(FakeKeys())

    def test_tools_list_matches_the_external_scope_exactly(self):
        names = [t["name"] for t in self.call("tools/list")[1]["result"]["tools"]]
        self.assertEqual(sorted(names), sorted(PT.tools_for("external")))
        self.assertTrue(names)

    def test_internal_only_tools_are_neither_listed_nor_callable(self):
        """트랙 A 전용 도구가 외부로 새면 안 된다."""
        PT.TOOLS["_internal_probe"] = {
            "scope": "internal", "title": "내부 전용", "desc": "밖으로 나가면 안 된다",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "fn": lambda **kw: {"ok": True}}
        self.addCleanup(lambda: PT.TOOLS.pop("_internal_probe", None))
        names = [t["name"] for t in self.call("tools/list")[1]["result"]["tools"]]
        self.assertNotIn("_internal_probe", names)
        status, body = self.tool("_internal_probe")
        self.assertEqual(status, 200)
        self.assertTrue(body["result"]["isError"])
        self.assertIn("부를 수 없는 도구", body["result"]["content"][0]["text"])

    def test_admin_only_tools_stay_hidden_from_non_admin_keys(self):
        PT.TOOLS["_admin_probe"] = {
            "scope": "external", "admin": True, "title": "관리자 전용", "desc": "발급자 권한 안에서만",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "fn": lambda **kw: {"ok": True}}
        self.addCleanup(lambda: PT.TOOLS.pop("_admin_probe", None))
        names = [t["name"] for t in self.call("tools/list")[1]["result"]["tools"]]
        self.assertNotIn("_admin_probe", names)
        self.assertTrue(self.tool("_admin_probe")[1]["result"]["isError"])
        MS._KEYS = None
        self.use(FakeKeys(is_admin=True))
        names = [t["name"] for t in self.call("tools/list")[1]["result"]["tools"]]
        self.assertIn("_admin_probe", names)

    def test_no_tool_schema_exposes_team(self):
        """스키마에 team 이 있으면 모델이 채울 수 있다 = 교차 팀 통로(감사 H1)."""
        for t in self.call("tools/list")[1]["result"]["tools"]:
            self.assertNotIn("team", t["inputSchema"].get("properties", {}), t["name"])
        self.assertNotIn('"team"', json.dumps(self.call("tools/list")[1], ensure_ascii=False))

    def test_every_listed_tool_carries_a_usable_schema(self):
        for t in self.call("tools/list")[1]["result"]["tools"]:
            self.assertTrue(t["description"], t["name"])
            self.assertEqual(t["inputSchema"]["type"], "object", t["name"])
            self.assertFalse(t["inputSchema"].get("additionalProperties", True), t["name"])

    def test_unknown_tool_names_come_back_as_is_error_with_a_next_step(self):
        status, body = self.tool("없는도구")
        self.assertEqual(status, 200)
        self.assertNotIn("error", body)                  # JSON-RPC 에러가 아니다
        self.assertTrue(body["result"]["isError"])
        self.assertIn("get_taxonomy", body["result"]["content"][0]["text"])   # 고를 것을 알려준다

    def test_tool_execution_failure_is_is_error_not_a_jsonrpc_error(self):
        status, body = self.tool("get_taxonomy", {"kind": "없는종류"})
        self.assertEqual(status, 200)
        self.assertNotIn("error", body)
        self.assertTrue(body["result"]["isError"])
        self.assertIn("intent", body["result"]["content"][0]["text"])         # 고를 값을 알려준다

    def test_a_working_tool_returns_its_payload_as_text_json(self):
        body = self.tool("get_taxonomy", {"kind": "category"})[1]
        self.assertNotIn("isError", body["result"])
        payload = json.loads(body["result"]["content"][0]["text"])
        self.assertGreater(payload["total"], 0)


class TestTeamComesFromTheKeyOnly(Base):
    def setUp(self):
        self.fake = self.use(FakeKeys())

    def test_team_in_arguments_is_ignored(self):
        seen = {}
        orig = PT.call
        PT.call = lambda name, args, team=None: seen.update(args=args, team=team) or {"ok": True}
        self.addCleanup(lambda: setattr(PT, "call", orig))
        self.tool("get_taxonomy", {"kind": "intent", "team": "남의팀"})
        self.assertEqual(seen["team"], TEAM)             # 키에서 해석한 값
        self.assertNotIn("team", seen["args"], "이 층에서도 team 을 넘기지 않는다")

    def test_the_key_team_reaches_the_tool_layer(self):
        MS._KEYS = None
        self.use(FakeKeys(team="team-9"))
        seen = {}
        orig = PT.call
        PT.call = lambda name, args, team=None: seen.update(team=team) or {"ok": True}
        self.addCleanup(lambda: setattr(PT, "call", orig))
        self.tool("get_taxonomy", {"kind": "intent"})
        self.assertEqual(seen["team"], "team-9")


class TestClientModel(Base):
    """외부 클라이언트가 자기 LLM 을 알려주는 자리. 실측 근거는 mcpserver 독스트링 참고."""

    def setUp(self):
        self.fake = self.use(FakeKeys())
        self.seen = {}
        orig = PT.call
        PT.call = lambda name, args, team=None: self.seen.update(args=args) or {"ok": True}
        self.addCleanup(lambda: setattr(PT, "call", orig))

    def test_it_is_offered_on_every_tool_but_never_required(self):
        for t in self.call("tools/list")[1]["result"]["tools"]:
            self.assertIn(MS.CLIENT_MODEL_ARG, t["inputSchema"]["properties"], t["name"])
            self.assertNotIn(MS.CLIENT_MODEL_ARG, t["inputSchema"].get("required", []), t["name"])

    def test_it_never_reaches_the_tool_itself(self):
        self.tool("lookup_entity", {"name": "가", "client_model": "claude-opus-5"})
        self.assertNotIn("client_model", self.seen["args"])

    def test_a_small_model_gets_a_tighter_response(self):
        self.tool("lookup_entity", {"name": "가", "limit": 50, "client_model": "claude-haiku-4-5"})
        self.assertEqual(self.seen["args"]["limit"], MS.SMALL_MODEL_ITEM_CAP)

    def test_a_big_model_keeps_what_it_asked_for(self):
        self.tool("lookup_entity", {"name": "가", "limit": 50, "client_model": "claude-opus-5"})
        self.assertEqual(self.seen["args"]["limit"], 50)

    def test_it_only_tightens_never_widens(self):
        self.tool("lookup_entity", {"name": "가", "limit": 3, "client_model": "gpt-5-mini"})
        self.assertEqual(self.seen["args"]["limit"], 3)

    def test_an_unknown_or_missing_model_changes_nothing(self):
        for model in ("", "듣도보도못한모델", None):
            args = {"name": "가", "limit": 50}
            if model is not None:
                args["client_model"] = model
            self.tool("lookup_entity", args)
            self.assertEqual(self.seen["args"]["limit"], 50, repr(model))

    def test_it_can_also_arrive_as_request_metadata(self):
        MS.handle("Bearer " + GOOD_KEY, json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "lookup_entity", "arguments": {"name": "가", "limit": 50},
                       "_meta": {MS.CLIENT_MODEL_META: "gemini-3-flash"}}}).encode())
        self.assertEqual(self.seen["args"]["limit"], MS.SMALL_MODEL_ITEM_CAP)

    def test_a_garbage_value_does_not_fail_the_call(self):
        status, body = self.tool("lookup_entity", {"name": "가", "client_model": {"nope": 1}})
        self.assertEqual(status, 200)
        self.assertNotIn("isError", body["result"])


class TestRealRoundTrip(Base):
    """도구 실행 경로 전체를 한 번은 **진짜로** 지난다.

    `/mcp` → mcprpc → mcpserver → prismtools → 저장 계층. 나머지 도구 테스트는
    `get_taxonomy`(사전만 읽음) 아니면 `PT.call` 을 가짜로 바꿔 쓰기 때문에, 저장 계층
    배선이 끊기거나 도구 응답 모양이 바뀌어도 아무도 모른다. 실제로 트랙 A 가
    `lookup_entity` 의 출력 필드를 하나 뺐는데 이 파일의 어떤 테스트도 반응하지 않았다.
    외부에 나가는 계약이므로 한 경로는 끝까지 실물로 확인한다.

    무력화 실험으로 실제 잡는 것을 확인했다(주장만 적지 않는다):
      · `PTL._SV` 유실       → 깨짐
      · 응답에서 `name` 유실  → 깨짐
      · 조회가 조용히 0건     → 깨짐
    아래 `test_serve_injects_the_tool_layer_reference` 는 셋 중 첫째만 잡는다 —
    좁은 대신 실패 메시지가 원인을 바로 가리키므로 둘 다 둔다."""

    ENT = "MCP왕복시험개체"

    def test_lookup_entity_round_trips_through_the_store(self):
        self.use(FakeKeys())
        from prism import serve
        st = serve.get_store()
        c = st._conn()
        c.execute("INSERT OR REPLACE INTO entities"
                  "(entity_id,name,type,status,created_at,updated_at)"
                  " VALUES('mcp-e2e-1',?,'org','confirmed',1,1)", (self.ENT,))
        c.commit()
        self.addCleanup(c.commit)
        self.addCleanup(c.execute, "DELETE FROM entities WHERE entity_id='mcp-e2e-1'")

        status, body = self.tool("lookup_entity", {"name": self.ENT})
        self.assertEqual(status, 200)
        self.assertNotIn("isError", body["result"], "실 저장 계층까지 못 닿았다")
        payload = json.loads(body["result"]["content"][0]["text"])
        self.assertEqual(payload["total"], 1)
        self.assertFalse(payload["truncated"])            # 잘림 고지 계약(감사 P3)
        item = payload["items"][0]
        self.assertEqual(item["name"], self.ENT)
        self.assertNotIn("team", item)                    # 팀 식별자가 밖으로 나가지 않는다


class TestServerIdentity(Base):
    def test_initialize_tells_the_client_how_to_use_prism(self):
        self.use(FakeKeys())
        r = self.call("initialize", {"protocolVersion": "2025-11-25"})[1]["result"]
        self.assertEqual(r["capabilities"], {"tools": {}})
        self.assertEqual(r["serverInfo"]["name"], "prism")
        self.assertIn("get_taxonomy", r["instructions"])
        self.assertIn("truncated", r["instructions"])

    def test_route_is_registered_as_an_open_post_path(self):
        """게이트 없이 등록돼야 한다 — 인증은 로그인이 아니라 파트너 키가 한다."""
        from prism import serve
        self.assertIn("/mcp", serve._POST_ROUTES)
        self.assertEqual(serve._POST_ROUTES["/mcp"][1], "")
        self.assertIsNone(serve._menu_for_path("/mcp"))   # 실험실 메뉴 권한에 걸리지 않는다

    def test_serve_injects_the_tool_layer_reference(self):
        """`PTL._SV` 주입이 빠지면 도구가 **터지지 않고 조용히 열화한다.**

        실측: 주입이 없으면 lookup_entity 가 500 도 스택도 없이
        `{"error": "사전이 준비되지 않았습니다"}` 를 200 + isError 로 돌려준다.
        파트너 눈에는 "사전이 아직 준비 안 됐나 보다" 로 보여 장애를 알아채기 어렵다.

        이 한 줄을 지키는 이유는 위치 때문이다. serve.py 에서 `PTL._SV = …` 는
        `from . import mcpserver` **바로 위 줄**이라, 같은 자리에 import 를 넣은 다른
        브랜치와 충돌을 풀 때 함께 날아가기 쉽다(트랙 A `feat/assist-tools` 와 실제로
        그 자리에서 충돌한다). 깨지면 실패 메시지가 원인을 바로 가리키게 둔다."""
        from prism import serve
        self.assertIs(PT._SV, serve,
                      "serve 가 prismtools 에 _SV 를 주입하지 않았다 · "
                      "serve.py 의 `PTL._SV = sys.modules[__name__]` 확인")


if __name__ == "__main__":
    unittest.main()
