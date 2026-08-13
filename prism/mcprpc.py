"""MCP 전송 공통 모듈 · JSON-RPC 2.0 over HTTP(단일 엔드포인트 · 무세션).

프리즘의 MCP 표면은 `/mcp` 하나다. 종전에는 실험 프로덕트인 스펙트럼 관문이 함께 있었고(2026-08-13 제거),
파트너용 프리즘 서버(`/mcp`). **둘은 성격이 달라 통합하지 않는다** — 도구도 인증도
데이터도 각자 갖는다. 그러나 전송(요청을 읽고 메서드를 가르고 응답 모양을 맞추는 일)은
같아야 한다. 갈라지면 한쪽만 고쳐지고, 안 고쳐진 쪽이 조용히 규약을 어긴다.

## 이 모듈이 지키는 규칙

1. **무세션.** 요청마다 키로 완결한다. main 머지마다 자동배포라 세션을 들면 배포 때마다
   클라이언트 연결이 끊긴다. `Mcp-Session-Id` 를 발급하지 않는다.
2. **인증은 전송이 책임진다 · 기본은 닫힘.** 인증 함수가 없거나 예외를 던지면 열지 않고
   401 이다. 키 모듈이 아직 없는 상태로 배포돼도 표면이 열리지 않는다.
3. **인증 실패 응답은 사유를 가리지 않는다.** 키 없음·형식 오류·불일치·만료·폐기가 전부
   같은 401 + 같은 문구다. 갈리면 그 차이가 열거 수단이 된다(2026-08-11 감사 H4 —
   배포 키 경로가 401/404 로 갈려 슬러그를 훑을 수 있었다).
4. **인증 실패는 기록하지 않는다.** `on_call` 은 인증을 통과한 요청에만 불린다(감사 O2 —
   그 관문이 인증 실패마다 상태 파일을 재기록해, 틀린 키로 600번 두드리면 실사용
   기록 500건이 밀려났다).
5. **도구 실행 실패는 JSON-RPC 에러가 아니다.** MCP 규약대로 `{"content": [...],
   "isError": true}` 로 결과 안에 담아, 모델이 문구를 읽고 스스로 고치게 한다.
   JSON-RPC 에러 코드는 인증·프로토콜 단계에만 쓴다.
6. **예외 원문·스택을 응답에 싣지 않는다**(감사 H3).

## 붙이는 법

도구·인증·데이터는 얹는 쪽(`mcpserver.py` 등)이 콜백으로 준다.

    ep = mcprpc.Endpoint(
        name="prism", version="1", instructions="…",
        authenticate=fn(key) -> ctx | Rejection | None,   # None·거짓 = 401
        list_tools=fn(ctx) -> [ {name, description, inputSchema}, … ],
        call_tool=fn(ctx, name, args, meta) -> payload | (payload, is_error),
        on_call=fn(ctx, tool, ok, ms, resp_bytes) -> None)   # 인증 통과분만
    status, body = ep.handle(auth_header, raw_body)   # body 가 None 이면 빈 202

`handle_body(key, data)` 는 본문을 이미 파싱한 호출자용이다(MCP 와
단순 REST 를 한 경로에서 가르는 구조가 그대로 얹히도록 남겨 둔 진입점).
"""
from __future__ import annotations

import json
import time

PROTOCOL_VERSION = "2025-06-18"

# JSON-RPC 표준 코드 + MCP 관례 코드(-32000 대역은 구현 정의)
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
UNAUTHORIZED = -32001
RATE_LIMITED = -32002

# 인증 실패 문구는 **하나뿐이다**. 사유별로 갈리면 그 차이가 열거 수단이 된다(감사 H4).
UNAUTHORIZED_TEXT = ("접속 키가 없거나 쓸 수 없습니다. "
                     "Authorization: Bearer <키> 헤더를 확인하세요.")


class Rejection:
    """도구를 부르기 **전** 단계에서 요청을 되돌리는 사유(인증·레이트리밋).

    도구 실행 실패는 여기 오지 않는다 — 그건 결과 안에 `isError` 로 담긴다."""
    __slots__ = ("status", "code", "message")

    def __init__(self, status: int = 401, code: int = UNAUTHORIZED,
                 message: str = UNAUTHORIZED_TEXT):
        self.status, self.code, self.message = status, code, message


# ── 응답 조립 ────────────────────────────────────────────────────────────────
def rpc_ok(rid, result) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def rpc_err(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def bearer(auth_header: str) -> str:
    h = (auth_header or "").strip()
    return h[7:].strip() if h[:7].lower() == "bearer " else ""


def tool_result(payload, is_error: bool = False) -> dict:
    """도구 결과 → MCP content 블록. 실패도 200 + 결과 안의 `isError` 로 나간다."""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    out = {"content": [{"type": "text", "text": text}]}
    if is_error:
        out["isError"] = True
    return out


def _peek_id(raw) -> object:
    """인증 실패 응답에도 요청 id 를 실어 주기 위한 최소 파싱.

    id 는 클라이언트가 방금 보낸 자기 값이라 되돌려줘도 아무것도 새지 않는다.
    (본문 파싱보다 인증을 먼저 하는 이유는 §3 — 미인증 호출은 본문이 무엇이든 같은 401 이다.)"""
    try:
        data = json.loads(raw or b"{}")
        return data.get("id") if isinstance(data, dict) else None
    except (ValueError, TypeError, AttributeError):
        return None


def _parse(raw):
    """(데이터, 오류응답). 오류응답이 있으면 그대로 돌려준다."""
    try:
        data = json.loads(raw or b"{}")
    except (ValueError, TypeError):
        return None, (400, rpc_err(None, PARSE_ERROR, "본문을 JSON 으로 읽을 수 없습니다"))
    if not isinstance(data, dict):
        return None, (400, rpc_err(None, INVALID_REQUEST,
                                   "본문은 JSON-RPC 오브젝트 하나여야 합니다(묶음 요청은 받지 않습니다)"))
    return data, None


class Endpoint:
    """JSON-RPC 2.0 단일 엔드포인트. 상태를 갖지 않는다(콜백만 들고 있다)."""

    def __init__(self, name: str, version: str = "1", instructions: str = "",
                 authenticate=None, list_tools=None, call_tool=None, on_call=None,
                 protocol_version: str = PROTOCOL_VERSION):
        self.name, self.version, self.instructions = name, version, instructions
        self.authenticate, self.list_tools = authenticate, list_tools
        self.call_tool, self.on_call = call_tool, on_call
        self.protocol_version = protocol_version

    # ── 진입점 ───────────────────────────────────────────────────────────────
    def handle(self, auth_header: str, raw_body):
        """(HTTP 상태, 응답 본문). 본문이 None 이면 빈 응답(알림)."""
        ctx = self._auth(bearer(auth_header))
        if isinstance(ctx, Rejection):                 # 미인증은 본문을 보기 전에 돌려보낸다
            return ctx.status, rpc_err(_peek_id(raw_body), ctx.code, ctx.message)
        data, bad = _parse(raw_body)
        if bad:
            return bad
        return self._dispatch(ctx, data)

    def handle_body(self, key: str, data: dict):
        """본문을 이미 파싱한 호출자용(키도 호출자가 뽑아 준다)."""
        ctx = self._auth(key)
        if isinstance(ctx, Rejection):
            rid = data.get("id") if isinstance(data, dict) else None
            return ctx.status, rpc_err(rid, ctx.code, ctx.message)
        if not isinstance(data, dict):
            return 400, rpc_err(None, INVALID_REQUEST, "본문은 JSON-RPC 오브젝트 하나여야 합니다")
        return self._dispatch(ctx, data)

    # ── 내부 ─────────────────────────────────────────────────────────────────
    def _auth(self, key: str):
        """실패는 전부 같은 Rejection. 인증 함수가 없거나 터져도 **열지 않는다**."""
        if not callable(self.authenticate):
            return Rejection()
        try:
            ctx = self.authenticate(key)
        except Exception:
            return Rejection()                         # 키 모듈 고장 = 열림이 아니라 닫힘
        if isinstance(ctx, Rejection):
            return ctx
        return ctx if ctx else Rejection()

    def _dispatch(self, ctx, data: dict):
        method = str(data.get("method") or "").strip()
        rid = data.get("id")
        params = data.get("params") if isinstance(data.get("params"), dict) else {}

        if method.startswith("notifications/"):
            return 202, None                           # 알림은 답이 없다(빈 응답 · 기록도 없다)

        t0 = time.monotonic()
        if method == "initialize":
            # 클라이언트가 제안한 버전을 그대로 받는다. 우리 표면은 버전 간 차이에 걸리는
            # 기능(resources·sampling·elicitation)을 쓰지 않아, 낮춰 부르면 클라이언트만
            # 헛되이 재협상한다. 실측: claude-code 2.1.x 는 2025-11-25 를 제안하고
            # 서버가 답한 값을 이후 mcp-protocol-version 헤더에 그대로 쓴다.
            result = {"protocolVersion": params.get("protocolVersion") or self.protocol_version,
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": self.name, "version": self.version}}
            if self.instructions:
                result["instructions"] = self.instructions
            return self._finish(ctx, "initialize", True, t0, rpc_ok(rid, result))

        if method == "ping":                           # 연결 확인(빈 결과)
            return self._finish(ctx, "ping", True, t0, rpc_ok(rid, {}))

        if method == "tools/list":
            try:
                tools = list(self.list_tools(ctx) or []) if callable(self.list_tools) else []
            except Exception:
                tools = []
            return self._finish(ctx, "tools/list", True, t0, rpc_ok(rid, {"tools": tools}))

        if method == "tools/call":
            return self._tools_call(ctx, rid, params, t0)

        return self._finish(ctx, method or "(없음)", False, t0,
                            rpc_err(rid, METHOD_NOT_FOUND,
                                    "모르는 메서드입니다: %s (쓸 수 있는 것: initialize · "
                                    "tools/list · tools/call)" % (method or "(없음)")))

    def _tools_call(self, ctx, rid, params: dict, t0):
        name = str(params.get("name") or "").strip()
        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        if not callable(self.call_tool):
            payload, is_error = {"error": "이 서버에는 부를 수 있는 도구가 없습니다"}, True
        else:
            try:
                out = self.call_tool(ctx, name, args, meta)
            except Exception:
                # 예외 원문·스택은 서버 로그 몫이다. 응답에는 다음 행동만 남긴다(감사 H3).
                out = ({"error": "도구를 실행하지 못했습니다. 인자를 확인하고 다시 부르세요."}, True)
            if isinstance(out, tuple):
                payload, is_error = out[0], bool(out[1])
            else:
                payload = out
                is_error = bool(isinstance(out, dict) and out.get("error"))
        # 도구 실행 실패는 JSON-RPC 에러가 아니라 결과 안에 담는다(MCP 규약 · 모델이 읽고 고친다)
        return self._finish(ctx, name or "tools/call", not is_error, t0,
                            rpc_ok(rid, tool_result(payload, is_error)))

    def _finish(self, ctx, tool: str, ok: bool, t0, body: dict):
        blob = json.dumps(body, ensure_ascii=False)
        if callable(self.on_call):
            try:
                self.on_call(ctx, tool, ok, int((time.monotonic() - t0) * 1000),
                             len(blob.encode("utf-8")))
            except Exception:
                pass                                   # 기록 실패가 응답을 막지 않는다
        return 200, body
