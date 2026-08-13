"""프리즘 MCP 서버(트랙 B · 외부 클라이언트) — 전송 위에 프리즘 도구를 얹는 층.

전송은 `mcprpc.Endpoint` 가 한다. 이 파일이 하는 일은 셋뿐이다.

  1. **인증**: 파트너 키 모듈(`mcpkeys`)로 키를 풀어 `(user_id, team, key_id, is_admin)`
     을 얻는다. 모듈이 없거나 계약을 못 채우면 표면을 열지 않는다(전부 401).
  2. **노출**: `prismtools.tools_for("external")` 를 `tools/list` 로 편다.
     **도구를 여기서 정의하지 않는다** — 정의가 갈리면 내부 검수 보조(트랙 A)와 외부가
     서로 다른 규칙으로 같은 이름을 쓰게 된다.
  3. **실행**: `prismtools.call` 로 넘긴다. team 은 **키에서 해석한 값만** 넘긴다.

## team 을 인자로 받지 않는 이유

저장 계층은 team 이 falsy 면 필터를 생략해 '전 팀' 으로 읽는다(2026-08-11 감사 H1).
도구 사용자가 team 을 채울 수 있으면 그 자체가 교차 팀 통로다. `prismtools.call` 이
이미 args 의 team 을 버리지만, 이 층에서도 애초에 넘기지 않는다. 두 겹으로 막는다.

## 클라이언트 모델(`client_model`) — 왜 도구 인자인가

외부 클라이언트가 자기 LLM 을 알려주면 프리즘이 응답 분량을 그 모델에 맞춘다.
모델명을 어디서 읽을지 두 후보(initialize 의 clientInfo · 도구 인자)를 **실측**했다.
claude-code 2.1.228 이 실제로 보낸 것:

    initialize.params.clientInfo = {"name": "claude-code", "title": "Claude Code",
                                    "version": "2.1.228", "description": …, "websiteUrl": …}
    tools/call.params._meta      = {"claudecode/toolUseId": …, "progressToken": 2}

clientInfo 에는 **모델명이 없다**(제품 이름이지 LLM 이 아니다 · `--model sonnet` 으로
띄워도 payload 어디에도 안 나온다). 게다가 전송이 무세션이라, clientInfo 를 읽어도
tools/call 시점까지 들고 있으려면 키별 상태를 둬야 해서 무세션 설계가 깨진다.
그래서 **도구 인자**로 받는다. 스키마에 선택 인자로 얹고 실행 직전에 떼어내므로
`prismtools` 도구 정의는 그대로다. 안 채우면 조용히 기본값으로 수렴하고 실패하지 않는다.
metadata 로 넣고 싶은 클라이언트를 위해 `params._meta["prism/clientModel"]` 도 읽는다.

예외 하나: 도구가 `client_model` 을 **스스로 선언한 경우**에는 떼어낸 값을 그 도구에 그대로
넘긴다(`get_extraction_prompt` 는 이 값으로 모델 계열 래퍼를 고른다 = 응답 분량이 아니라
응답 내용이 달라진다). 안 그러면 그 도구는 스키마에 인자를 걸어 두고도 영원히 빈 값을 받는다.
"""
from __future__ import annotations

from . import mcprpc
from . import prismtools as PT

SERVER_NAME = "prism"
SERVER_VERSION = "1"

INSTRUCTIONS = (
    "프리즘(콘텐츠 검수·메타데이터 운영)의 읽기 도구입니다. "
    "분류 체계의 허용값과 개체 사전을 물어볼 수 있습니다. "
    "값을 지어내지 말고 get_taxonomy 로 쓸 수 있는 값을 먼저 확인하세요. "
    "메타를 직접 만들 때는 get_extraction_prompt 로 프리즘의 현행 프롬프트를 받아 그 프롬프트로 "
    "돌리세요. 그래야 기준이 프리즘 것이 되고, 응답의 version·fingerprint 로 나중에 되짚을 수 "
    "있습니다(학습 보정이 빠져 있어 프리즘 실제 실행과는 다를 수 있습니다). "
    "만든 결과는 validate_result 로 규칙 위반만 확인할 수 있습니다 · 품질 판정이 아닙니다. "
    "조회 범위(팀)는 접속 키에서 정해집니다 — 인자로 팀을 넣지 않습니다. "
    "응답에 truncated 가 true 면 더 있다는 뜻이니 조건을 좁혀 다시 부르세요."
)

# 클라이언트 LLM 을 받는 자리(위 독스트링 참고). 도구 정의가 아니라 전송 계층의 선택 인자다.
CLIENT_MODEL_ARG = "client_model"
CLIENT_MODEL_META = "prism/clientModel"
CLIENT_MODEL_DESC = ("부르는 쪽 LLM 이름(선택 · 예: claude-haiku-4-5 · gpt-5-mini). "
                     "넣으면 응답 분량을 그 모델에 맞춘다. 모르면 비워 둔다.")

# 맥락이 좁거나 값싼 모델에는 한 번에 덜 싣는다. 응답 크기가 곧 호출자의 비용이라,
# 50건을 밀어넣고 모델이 못 읽는 것보다 10건을 주고 truncated 로 알리는 편이 낫다.
SMALL_MODEL_HINTS = ("haiku", "mini", "nano", "lite", "flash", "small", "-8b", "-7b", "-4b")
SMALL_MODEL_ITEM_CAP = 10

_KEYS = None                                 # 키 모듈 캐시 겸 테스트 주입점(가짜를 꽂는다)


# ── 파트너 키 모듈 ───────────────────────────────────────────────────────────
def _import_keys():
    """실제 import 를 함수로 감싼 이유: 모듈이 없는 상황(fail-closed)을 테스트가 재현한다."""
    from . import mcpkeys as M
    return M


def keys():
    """파트너 키 모듈. 없거나 계약을 못 채우면 None → `/mcp` 는 전부 401.

    이 모듈은 다른 세션이 만든다. 아직 없는 채로 배포돼도 표면이 열려서는 안 되므로
    '모르면 닫는다' 로 수렴시킨다. 계약:
        resolve(raw_key)  -> {"user_id", "team", "key_id", "is_admin"} | None
        rate_check(key_id)-> (allowed, retry_after_sec)
        log_call(key_id, tool, ok, ms, resp_bytes) -> None
    """
    if _KEYS is not None:
        return _KEYS
    try:
        mod = _import_keys()
    except Exception:
        return None
    for fn in ("resolve", "rate_check", "log_call"):
        if not callable(getattr(mod, fn, None)):
            return None                      # 계약을 못 채운 모듈은 없는 것으로 본다
    return mod


def authenticate(raw_key: str):
    """키 → 호출 맥락. 실패는 **사유를 가리지 않고** 같은 401 이다(감사 H4)."""
    mod = keys()
    if mod is None:
        return mcprpc.Rejection()
    try:
        who = mod.resolve(raw_key)
    except Exception:
        who = None                           # 키 모듈이 터져도 열지 않는다
    if not isinstance(who, dict):
        return mcprpc.Rejection()
    team = str(who.get("team") or "").strip()
    key_id = str(who.get("key_id") or "").strip()
    if not team or not key_id:
        # team 없는 키는 발급 자체를 막지만, 뚫려도 여기서 막힌다(falsy team = 전 팀 · H1)
        return mcprpc.Rejection()
    try:
        allowed, retry = mod.rate_check(key_id)
    except Exception:
        allowed, retry = False, 60           # 상한을 못 재면 통과시키지 않는다
    if not allowed:
        return mcprpc.Rejection(status=429, code=mcprpc.RATE_LIMITED,
                                message="요청이 너무 잦습니다. %d초 뒤에 다시 부르세요."
                                        % PT.qint(retry, 60, 1, 3600))
    return {"user_id": str(who.get("user_id") or ""), "team": team,
            "key_id": key_id, "is_admin": bool(who.get("is_admin"))}


def log_call(ctx, tool: str, ok: bool, ms: int, resp_bytes: int):
    """사용 기록. 인증을 통과한 호출만 온다(전송이 보장 · 감사 O2)."""
    mod = keys()
    if mod is None:
        return
    try:
        mod.log_call(ctx.get("key_id", ""), tool, bool(ok), int(ms), int(resp_bytes))
    except Exception:
        pass                                 # 기록 실패가 응답을 막지 않는다


# ── 도구 노출 ────────────────────────────────────────────────────────────────
def visible_tools(ctx) -> dict:
    """이 키에 열린 도구. external 스코프 + 관리자 전용 도구는 관리자 키에만."""
    return {n: s for n, s in PT.tools_for("external").items()
            if not (s.get("admin") and not ctx.get("is_admin"))}


def _schema(spec) -> dict:
    """도구 스키마 + 선택 인자 client_model. 원본(prismtools)은 건드리지 않는다.

    도구가 client_model 을 **스스로 선언한 경우**(그 값이 응답 내용을 바꾸는 도구 ·
    get_extraction_prompt 는 모델 계열로 래퍼를 고른다)에는 그 설명을 덮지 않는다.
    전송 계층의 설명("응답 분량을 맞춘다")은 그 도구에서 사실이 아니다."""
    src = spec["inputSchema"]
    props = dict(src.get("properties") or {})
    props.setdefault(CLIENT_MODEL_ARG, {"type": "string", "description": CLIENT_MODEL_DESC})
    out = dict(src)
    out["properties"] = props
    return out


def list_tools(ctx) -> list:
    return [{"name": name, "title": spec["title"], "description": spec["desc"],
             "inputSchema": _schema(spec)}
            for name, spec in sorted(visible_tools(ctx).items())]


def _item_cap(client_model: str) -> int:
    """클라이언트 모델 → 한 응답 항목 상한(0 = 따로 조이지 않음)."""
    m = (client_model or "").strip().lower()
    return SMALL_MODEL_ITEM_CAP if any(h in m for h in SMALL_MODEL_HINTS) else 0


def _fit(args: dict, spec: dict, client_model: str) -> dict:
    """모델 분량에 맞춰 정수 인자를 **조이기만** 한다(늘리지 않는다).

    잘린 사실은 prismtools 의 envelope 가 truncated·total 로 알린다(감사 P3)."""
    cap = _item_cap(client_model)
    if not cap:
        return args
    for k, p in (spec["inputSchema"].get("properties") or {}).items():
        if p.get("type") != "integer" or k not in args:
            continue
        n = PT.qint(args[k], 0, 0, 10 ** 9)
        if n > cap:
            args[k] = cap
    return args


def call_tool(ctx, name: str, args: dict, meta=None):
    """(응답, isError). 실패도 200 으로 나가고 모델이 문구를 읽는다."""
    args = dict(args or {})
    model = str(args.pop(CLIENT_MODEL_ARG, "") or (meta or {}).get(CLIENT_MODEL_META) or "").strip()
    tools = visible_tools(ctx)
    spec = tools.get((name or "").strip())
    if not spec:
        # 다음 행동을 정할 수 있는 문구로. 이 키에 안 열린 도구도 여기서 걸린다.
        return ({"error": "'%s' 는 지금 부를 수 없는 도구입니다. 쓸 수 있는 것: %s"
                          % (name or "(없음)", " · ".join(sorted(tools)) or "없음")}, True)
    # 도구가 선언한 인자만 넘긴다. team 은 여기서 떨어진다 — prismtools 도 args 의 team 을
    # 버리지만, 교차 팀 통로는 두 겹으로 막는다(감사 H1).
    props = spec["inputSchema"].get("properties") or {}
    args = {k: v for k, v in args.items() if k in props}
    if CLIENT_MODEL_ARG in props:
        # 위에서 pop 한 값을 **도구가 스스로 선언했을 때만** 되돌려 준다. 이 층은 client_model 을
        # 응답 분량 조정용으로 떼어 가는데, 그 값이 응답 **내용**을 정하는 도구도 있다
        # (get_extraction_prompt = 모델 계열 래퍼 선택). 떼어 가기만 하면 그 도구는 인자를
        # 스키마에 걸어 두고도 영원히 빈 값을 받는다. _meta 로 온 값도 같은 자리로 들어간다.
        args[CLIENT_MODEL_ARG] = model
    out = PT.call(name, _fit(args, spec, model), team=ctx["team"])
    if not isinstance(out, dict):
        return ({"error": "도구가 예상 밖 형식을 돌려줬습니다"}, True)
    return out, bool(out.get("error"))


ENDPOINT = mcprpc.Endpoint(name=SERVER_NAME, version=SERVER_VERSION, instructions=INSTRUCTIONS,
                           authenticate=authenticate, list_tools=list_tools,
                           call_tool=call_tool, on_call=log_call)


def handle(auth_header: str, raw_body):
    """POST /mcp 진입점. (HTTP 상태, 응답 본문) · 본문이 None 이면 빈 응답(알림)."""
    return ENDPOINT.handle(auth_header, raw_body)
