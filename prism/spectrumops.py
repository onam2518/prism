"""스펙트럼(Spectrum) · 사내 MCP 허브 프로토타입 (실험실 · 백엔드 코어).

기획 문서: pplan 13. Spectrum (Confluence 440336487) · MVP v1 범위의 실험실 프로토타입 ·
1호 연결은 B안(TIARA 선출발).
데이터 원천: prj. Beluga (Confluence 375620090) · 벨루가는 S3 + Iceberg 레이크하우스이고
브론즈(원본) → 실버(정리) → 골드(큐레이션) 3계층으로 쌓인다. 스펙트럼 커넥터는 그중
골드 레이어만 읽는 DB 커넥터다.

데이터가 흐르는 길: MCP 클라이언트 → 데이터 연결 서버(이 관문) → 벨루가 골드 조회 → 반환.

허브가 하는 일은 네 가지다.
1. 카탈로그: 어떤 데이터를 MCP 로 붙여 쓸 수 있는지 목록으로 보여준다(코드 내 상수).
2. 접속 키: 사람마다 키를 발급하고 기한이 지나면 막는다.
3. 접속 관문: 키 확인(authenticate) → 권한 확인(authorize) → 실행(dispatch) → 사용 기록 적재.
   화면 체험(POST /spectrum 의 gw_try)과 실제 관문(POST /spectrum-gw)이 이 코어(call) 하나를
   같이 쓴다. 체험에서 본 결과와 실제 호출 결과가 갈라지지 않게 하려는 것.
4. 사용 기록·지표: 누가 언제 무엇을 불렀는지 최근 500건을 보관하고 주간 지표를 낸다.

저장은 프로토타입이라 새 SQLite 테이블 대신 JSON 사이드카 파일 하나를 쓴다.
경로는 PRISM_SPECTRUM_PATH · 없으면 PRISM_DB 옆 · 그것도 없으면 설정 파일 옆 spectrum.json.
서버가 ThreadingHTTPServer 라 쓰기는 잠금(RLock)으로 묶고 임시 파일 교체로 저장한다.

관문이 돌려주는 데이터는 전부 모의(비식별 합성)다. 실서비스 전환 시 바뀌는 자리는
주석으로 표시해 두었다(권한은 LDP 연동 · 키는 해시 저장 · 데이터는 벨루가 골드 Iceberg 표 조회).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import random
import secrets
import threading
import time

from .config import DEFAULT_CONFIG_PATH

VERSION = "0.1.0"                       # 관문·카탈로그 버전(응답에 실어 보낸다)
PROTOCOL_VERSION = "2025-06-18"         # MCP 프로토콜 기본값(initialize 에서 클라이언트 값 우선)
KEY_PREFIX = "spk_"
DEFAULT_TTL_DAYS = 90
MAX_TTL_DAYS = 365
MAX_KEYS_PER_USER = 10                  # 사용자당 살아 있는 키 개수 상한
KEYS_CAP = 200                          # 파일 크기 상한(넘치면 폐기된 옛 키부터 정리)
USAGE_CAP = 500                         # 사용 기록 보관 건수(넘치면 오래된 것부터 버린다)
CONNECTOR_ID = "user-logs"
GATEWAY_PATH = "/spectrum-gw"
DEFAULT_BASE_URL = "https://prism-item.fly.dev"

_LOCK = threading.RLock()               # 저장 파일 보호(재진입 가능 · call 안에서 중첩 사용)


# ── 카탈로그 ────────────────────────────────────────────────────────────────
# 1호 커넥터의 도구 정의. inputSchema 는 MCP tools/list 가 그대로 쓰는 JSON Schema 이자
# 화면 카탈로그의 설명 원천이다(한 곳만 고치면 둘 다 바뀐다).
_TOOLS = [
    {
        "name": "list_tables",
        "title": "테이블 목록",
        "desc": "벨루가 골드 레이어에서 열려 있는 표와 각 칸의 뜻을 알려준다. "
                "표가 어떻게 나뉘어 저장되는지(파티션)와 지금 보고 있는 스냅샷 번호도 같이 준다.",
        "scope": "user_logs:read",
        "restricted": False,
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "query_logs",
        "title": "행동 로그 조회",
        "desc": "기간 · 행동 종류 · 서비스로 걸러서 행동 로그를 가져온다. 한 번에 최대 100줄. "
                "as_of 를 주면 그날까지 쌓였던 표를 그대로 다시 읽는다(과거 시점 조회).",
        "scope": "user_logs:read",
        "restricted": False,
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "시작 날짜(YYYY-MM-DD) · 비우면 최근 7일 전체"},
                "date_to": {"type": "string", "description": "끝 날짜(YYYY-MM-DD) · 비우면 오늘까지"},
                "action": {"type": "string", "description": "행동 종류 하나만 고르기(click · view · dwell 등)"},
                "svc": {"type": "string", "description": "서비스 코드(news · tv · shorts · cafe)"},
                "as_of": {"type": "string",
                          "description": "과거 시점 조회(YYYY-MM-DD) · 그날까지 쌓였던 표를 그대로 읽는다 · "
                                         "같은 날짜로 다시 물으면 늘 같은 답이 나온다"},
                "limit": {"type": "integer", "description": "가져올 줄 수(기본 20 · 최대 100)",
                          "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "agg_metrics",
        "title": "요약 지표",
        "desc": "날짜별 요약값을 준다. 쓸 수 있는 값은 dau(하루 이용자) · clicks(클릭 수) · dwell(평균 체류 초).",
        "scope": "user_logs:read",
        "restricted": False,
        "inputSchema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": ["dau", "clicks", "dwell"],
                           "description": "보고 싶은 값 하나"},
                "days": {"type": "integer", "description": "며칠치를 볼지(기본 7 · 최대 30)",
                         "minimum": 1, "maximum": 30},
            },
            "required": ["metric"],
            "additionalProperties": False,
        },
    },
    {
        "name": "query_search_terms",
        "title": "검색어 조회",
        "desc": "검색어 데이터는 저장 위치도 권한도 따로다. 스펙트럼 v1 에서는 막아 둔 도구.",
        "scope": "search_terms:read",
        "restricted": True,
        "inputSchema": {
            "type": "object",
            "properties": {"q": {"type": "string", "description": "찾을 검색어"}},
            "additionalProperties": False,
        },
    },
]

_TOOL_BY_NAME = {t["name"]: t for t in _TOOLS}

# 막아 둔 도구의 거절 사유(정책 시연 · 화면과 관문이 같은 문구를 쓴다)
FORBIDDEN_DETAIL = "검색어 데이터는 별도 저장 · 별도 권한 대상입니다. 스펙트럼 v1 범위 밖."

# TIARA 행동 로그 스킴을 바탕으로 한 비식별 필드(개인을 지목할 수 있는 값은 넣지 않는다).
_FIELDS_ACTION = [
    ("ts", "timestamp", "행동이 일어난 시각(초 단위)"),
    ("svc", "string", "서비스 코드 · news · tv · shorts · cafe"),
    ("page", "string", "어떤 화면에서 일어났는지 · 홈탭 · 마이 콘텐츠 탭 등"),
    ("section", "string", "그 화면 안 어느 묶음인지 · 추천 묶음 이름"),
    ("action", "string", "무엇을 했는지 · click · view · dwell · scroll · like · dislike · doublelike · pick"),
    ("item_id", "string", "본 콘텐츠 번호"),
    ("item_type", "string", "콘텐츠 형태 · article · video · short"),
    ("pseudo_id", "string", "사람을 대신하는 익명 키(원본 아이디를 해시로 바꾼 값)"),
    ("session_id", "string", "한 번 들어와서 나갈 때까지를 묶는 키"),
    ("dwell_ms", "integer", "그 콘텐츠에 머문 시간(밀리초)"),
    ("scroll_pct", "integer", "화면을 얼마나 내렸는지(%)"),
    ("device", "string", "기기 구분 · ios · aos · web"),
    ("app_ver", "string", "앱 버전"),
    ("rank", "integer", "목록에서 몇 번째로 보였는지"),
]

_FIELDS_SESSION = [
    ("date", "date", "날짜"),
    ("pseudo_id", "string", "사람을 대신하는 익명 키"),
    ("svc", "string", "서비스 코드"),
    ("sessions", "integer", "그날 들어온 횟수"),
    ("actions", "integer", "그날 한 행동 수"),
    ("dwell_ms_sum", "integer", "그날 머문 시간 합(밀리초)"),
    ("items_seen", "integer", "본 콘텐츠 수"),
    ("items_clicked", "integer", "눌러 본 콘텐츠 수"),
    ("device", "string", "가장 많이 쓴 기기"),
]

# 표 정의(고정분). 스냅샷 번호는 날짜에 따라 달라지므로 _tables() 가 붙인다.
_TABLE_DEFS = [
    {"name": "gold.user_action_log", "grain": "행동 1건",
     "desc": "행동 하나가 한 줄. 언제 · 어느 화면에서 · 무엇을 했는지가 들어 있다.",
     "partitionBy": ["date", "svc"],
     "partitionDesc": "날짜와 서비스로 나눠 저장한다. 기간을 좁혀 물을수록 빨리 답한다.",
     "fields": [{"name": n, "type": t, "desc": d} for n, t, d in _FIELDS_ACTION]},
    {"name": "gold.user_session_daily", "grain": "익명 사용자 1명 · 하루",
     "desc": "하루치를 사람 단위로 미리 합쳐 둔 표. 주간 리포트처럼 큰 그림을 볼 때 쓴다.",
     "partitionBy": ["date"],
     "partitionDesc": "날짜로 나눠 저장한다.",
     "fields": [{"name": n, "type": t, "desc": d} for n, t, d in _FIELDS_SESSION]},
]


def _snapshot_id(table: str, day: str) -> str:
    """모의 Iceberg 스냅샷 번호. 표와 날짜가 같으면 늘 같은 번호가 나온다.
    자릿수가 커서 화면에서 숫자로 다루면 값이 뭉개지므로 문자열로 준다."""
    return str(int(hashlib.sha1((table + "@" + day).encode()).hexdigest()[:15], 16))


def _tables(as_of: str = "") -> list:
    """열려 있는 표 목록 + 레이크하우스 메타(계층·형식·저장소·파티션·스냅샷)."""
    day = as_of or _dt.date.today().isoformat()
    out = []
    for t in _TABLE_DEFS:
        row = dict(t)
        row.update({
            "layer": "gold",                 # 스펙트럼은 골드만 연다(브론즈·실버는 대상 아님)
            "format": "iceberg",
            "storage": "s3",
            "catalog": "beluga",
            "timeTravel": True,
            "snapshotId": _snapshot_id(t["name"], day),
            "snapshotAt": day,
        })
        out.append(row)
    return out

_USE_CASES = [
    "홈탭 클릭 흐름 대시보드",
    "체류시간 주간 리포트",
    "숏폼 좋아요 · 싫어요 비율 추이",
    "화면별 이탈 구간 찾기",
    "개편 전후 행동 변화 비교",
]


def base_url() -> str:
    """관문 공개 주소(안내 문구·server.json 에 쓴다). 배포 환경마다 다르면 환경변수로 바꾼다."""
    return (os.environ.get("PRISM_PUBLIC_BASE") or DEFAULT_BASE_URL).rstrip("/")


def gateway_url() -> str:
    return base_url() + GATEWAY_PATH


def _server_json() -> dict:
    """공식 MCP Registry 의 server.json 과 같은 모양. 나중에 레지스트리에 그대로 올릴 수 있게."""
    return {
        "$schema": "https://static.modelcontextprotocol.io/schemas/2025-07-09/server.schema.json",
        "name": "net.daumkakao.spectrum/user-logs",
        "description": "벨루가 골드 레이어(S3+Iceberg)의 사용자 행동 로그(비식별)를 "
                       "MCP 로 물어볼 수 있게 열어 주는 DB 커넥터.",
        "version": VERSION,
        "remotes": [{
            "type": "streamable-http",
            "url": gateway_url(),
            "headers": [{
                "name": "Authorization",
                "description": "스펙트럼 접속 키 · Bearer spk_ 로 시작합니다",
                "isRequired": True,
                "isSecret": True,
            }],
        }],
    }


def catalog() -> list:
    """커넥터 목록. live 1개(1호) + preparing 2개(카탈로그 형태 검증용)."""
    return [
        {
            "id": CONNECTOR_ID,
            "status": "live",
            "name": "사용자 행동 로그",
            "owner": "플랫폼기획 · 데이터허브 크루",
            "dataBasis": "벨루가 골드 레이어(S3+Iceberg) · 로그 원천 TIARA(현행) · 12월 PAST 전환 예정",
            "kind": "db",                    # DB 커넥터: 벨루가 골드 표를 읽는다
            "warehouse": {"name": "beluga", "layer": "gold", "format": "iceberg",
                          "storage": "s3", "timeTravel": True,
                          "desc": "벨루가는 원본(브론즈) · 정리(실버) · 큐레이션(골드) 3단으로 쌓는다. "
                                  "스펙트럼은 정리가 끝난 골드만 읽는다."},
            "version": VERSION,
            "transport": "streamable-http",
            "endpoint": GATEWAY_PATH,
            "url": gateway_url(),
            "summary": "벨루가 골드 레이어의 Iceberg 표를 읽는 DB 커넥터. 홈탭·마이 콘텐츠 탭에서 "
                       "사람들이 무엇을 눌렀고 얼마나 머물렀는지를 비식별 상태로 물어볼 수 있고, "
                       "과거 시점의 표도 그대로 다시 읽을 수 있다.",
            "tools": [{"name": t["name"], "title": t["title"], "desc": t["desc"],
                       "restricted": t["restricted"],
                       "restrictedLabel": ("권한 제한" if t["restricted"] else ""),
                       "inputSchema": t["inputSchema"]} for t in _TOOLS],
            "useCases": list(_USE_CASES),
            "serverJson": _server_json(),
        },
        {
            "id": "item-meta",
            "status": "preparing",
            "name": "아이템 메타",
            "owner": "플랫폼기획 · DNM 크루",
            "dataBasis": "DNM 아이템 메타 과제 산출물",
            "kind": "db",
            "version": "0.0.0",
            "transport": "streamable-http",
            "endpoint": "",
            "url": "",
            "summary": "콘텐츠 한 건의 요약·카테고리·엔티티를 물어보는 커넥터. "
                       "DNM 아이템 메타 과제가 끝나야 붙일 수 있다.",
            "tools": [],
            "useCases": ["콘텐츠 묶음 자동 설명", "카테고리 오분류 점검"],
            "note": "전제: DNM 아이템 메타 과제 완료",
        },
        {
            "id": "beluga-gold",
            "status": "preparing",
            "name": "벨루가 골드 테이블",
            "owner": "데이터허브 크루 · 벨루가 TF",
            "dataBasis": "벨루가 골드 레이어(S3+Iceberg) · 행동 로그 밖의 공용 표",
            "kind": "db",
            "warehouse": {"name": "beluga", "layer": "gold", "format": "iceberg",
                          "storage": "s3", "timeTravel": True,
                          "desc": "브론즈(원본)와 실버(정리 중)는 열지 않는다. 손이 덜 간 표는 "
                                  "보는 사람마다 다르게 읽혀 숫자가 어긋나기 때문."},
            "version": "0.0.0",
            "transport": "streamable-http",
            "endpoint": "",
            "url": "",
            "summary": "행동 로그 말고도 벨루가 골드에 쌓인 공용 표를 그대로 물어보는 커넥터. "
                       "정리가 끝난 골드만 열어 누가 물어도 같은 숫자가 나오게 한다. "
                       "어디까지 열지는 벨루가 TF 와 협의가 끝나야 정해진다.",
            "tools": [],
            "useCases": ["지표 정의 통일", "리포트 재작성 없이 바로 조회", "과거 시점 숫자 재현"],
            "note": "전제: 벨루가 TF 협의 후 공개 범위 확정",
        },
    ]


# ── 저장(JSON 사이드카) ─────────────────────────────────────────────────────
def state_path() -> str:
    """저장 파일 경로. 호출할 때마다 환경변수를 다시 본다(테스트가 경로를 갈아끼운다)."""
    p = (os.environ.get("PRISM_SPECTRUM_PATH") or "").strip()
    if p:
        return p
    db = (os.environ.get("PRISM_DB") or "").strip()
    base = os.path.dirname(db) if db else os.path.dirname(DEFAULT_CONFIG_PATH)
    return os.path.join(base or ".", "spectrum.json")


def _empty_state() -> dict:
    return {"keys": [], "usage": []}


def _load() -> dict:
    path = state_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    data.setdefault("keys", [])
    data.setdefault("usage", [])
    if not isinstance(data["keys"], list):
        data["keys"] = []
    if not isinstance(data["usage"], list):
        data["usage"] = []
    return data


def _save(state: dict):
    """임시 파일에 쓰고 바꿔치기 · 쓰다 만 파일이 남지 않게. 호출 전에 _LOCK 을 잡는다."""
    path = state_path()
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, path)


# ── 접속 키 ─────────────────────────────────────────────────────────────────
def _mask(key: str) -> str:
    """목록에 보여 줄 형태: 앞 6자(spk_ 포함) + 뒤 4자. 전체 키는 발급 응답에서만 나간다."""
    key = key or ""
    return (key[:6] + "…" + key[-4:]) if len(key) > 12 else "spk_…"


def _key_public(rec: dict, now: float = None) -> dict:
    now = _now() if now is None else now
    exp = float(rec.get("expiresAt") or 0)
    return {
        "id": rec.get("id", ""),
        "label": rec.get("label", ""),
        "masked": _mask(rec.get("key", "")),
        "createdAt": rec.get("createdAt", 0),
        "expiresAt": exp,
        "expiresAtText": _day(exp),
        "lastUsedAt": rec.get("lastUsedAt") or 0,
        "revoked": bool(rec.get("revoked")),
        "expired": exp < now,
        "daysLeft": max(0, int((exp - now) // 86400)),
        "calls": int(rec.get("calls") or 0),
    }


def _now() -> float:
    return time.time()


def _day(epoch: float) -> str:
    try:
        return _dt.datetime.fromtimestamp(float(epoch)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def issue_key(user: str, label: str = "", ttl_days: int = DEFAULT_TTL_DAYS) -> dict:
    """접속 키 발급. 전체 키 문자열은 이 응답에서 딱 한 번만 나간다.
    프로토타입이라 원문을 파일에 그대로 둔다 · 운영 전환 시 해시 저장(deployops 방식)."""
    user = (user or "").strip()
    if not user:
        return {"ok": False, "error": "로그인 정보를 확인할 수 없습니다"}
    try:
        ttl = int(ttl_days or DEFAULT_TTL_DAYS)
    except (TypeError, ValueError):
        return {"ok": False, "error": "유효기간은 숫자로 넣어 주세요"}
    if not (1 <= ttl <= MAX_TTL_DAYS):
        return {"ok": False, "error": f"유효기간은 1일부터 {MAX_TTL_DAYS}일까지 정할 수 있습니다"}
    now = _now()
    with _LOCK:
        st = _load()
        mine = [k for k in st["keys"]
                if k.get("user") == user and not k.get("revoked")
                and float(k.get("expiresAt") or 0) >= now]
        if len(mine) >= MAX_KEYS_PER_USER:
            return {"ok": False,
                    "error": f"쓸 수 있는 키는 한 사람당 {MAX_KEYS_PER_USER}개까지입니다 · "
                             "안 쓰는 키를 먼저 폐기하세요"}
        token = KEY_PREFIX + secrets.token_hex(16)          # spk_ + 32자 hex
        rec = {"id": "k_" + secrets.token_hex(6), "user": user,
               "label": (label or "").strip()[:40] or "이름 없는 키",
               "key": token, "createdAt": now, "expiresAt": now + ttl * 86400,
               "lastUsedAt": 0, "revoked": False, "calls": 0}
        st["keys"].append(rec)
        if len(st["keys"]) > KEYS_CAP:                      # 파일이 계속 자라지 않게
            st["keys"].sort(key=lambda k: (not k.get("revoked"), float(k.get("createdAt") or 0)))
            st["keys"] = st["keys"][-KEYS_CAP:]
        _save(st)
    return {"ok": True, "id": rec["id"], "key": token, "masked": _mask(token),
            "expiresAt": rec["expiresAt"], "expiresAtText": _day(rec["expiresAt"]),
            "hint": "이 키는 다시 보여 주지 않습니다 · 지금 복사해 두세요"}


def revoke_key(user: str, key_id: str) -> dict:
    """키 폐기. 남의 키는 건드릴 수 없다."""
    user = (user or "").strip()
    key_id = (key_id or "").strip()
    with _LOCK:
        st = _load()
        for k in st["keys"]:
            if k.get("id") == key_id and k.get("user") == user:
                k["revoked"] = True
                _save(st)
                return {"ok": True, "id": key_id}
    return {"ok": False, "error": "키를 찾을 수 없습니다"}


def _find_key(keys: list, key: str = "", key_id: str = "", user: str = None):
    """키 문자열이 있으면 그것으로, 없으면 (키 id + 소유자)로 찾는다.
    키 문자열 비교는 compare_digest 로 · 앞자리만 맞춰 보는 시도를 막는다."""
    if key:
        for k in keys:
            stored = k.get("key") or ""
            if len(stored) == len(key) and secrets.compare_digest(stored, key):
                return k
        return None
    if not key_id:
        return None
    for k in keys:
        if k.get("id") == key_id and (not user or k.get("user") == user):
            return k
    return None


# 인증 실패 사유 → 사람이 읽는 문구
_ERR_TEXT = {
    "invalid_key": "접속 키가 없거나 잘못됐습니다",
    "expired_key": "접속 키 기한이 지났습니다 · 새로 발급받으세요",
    "revoked_key": "폐기한 접속 키입니다",
}


def authenticate(key: str = "", key_id: str = "", user: str = None, state: dict = None):
    """키 확인. (키 기록, 실패 사유) 를 돌려준다. 실패 사유가 빈 문자열이면 통과."""
    st = _load() if state is None else state
    rec = _find_key(st.get("keys") or [], key=key, key_id=key_id, user=user)
    if not rec:
        return None, "invalid_key"
    if rec.get("revoked"):
        return rec, "revoked_key"
    if float(rec.get("expiresAt") or 0) < _now():
        return rec, "expired_key"
    return rec, ""


def scopes_for(rec: dict) -> list:
    """이 키가 무엇을 볼 수 있는지. 프로토타입은 '키가 살아 있으면 행동 로그 읽기'로 본다.
    실서비스는 LDP 연동 · 사람마다 받은 권한을 그때그때 확인해야 한다."""
    return ["user_logs:read"]


def authorize(tool: str, rec: dict = None):
    """권한 확인. (통과 여부, 사유) · 도구가 요구하는 스코프를 키가 가졌는지 본다.
    프로토타입은 행동 로그 읽기만 주므로 검색어 도구가 막힌다(정책 시연).
    실서비스는 scopes_for 가 LDP 연동으로 바뀌고 이 대조는 그대로 쓴다."""
    spec = _TOOL_BY_NAME.get(tool)
    if not spec:
        return False, f"모르는 도구입니다: {tool}"
    need = spec.get("scope") or ""
    if need and need not in scopes_for(rec):
        return False, (FORBIDDEN_DETAIL if spec.get("restricted")
                       else f"이 키에는 {need} 권한이 없습니다")
    return True, ""


# ── 모의 데이터(비식별 합성 · 시드 고정) ────────────────────────────────────
_SVCS = ["news", "tv", "shorts", "cafe"]
_PAGES = ["홈탭", "마이 콘텐츠 탭", "발견 탭", "콘텐츠 상세", "숏폼 뷰어"]
_SECTIONS = ["오늘의 추천", "많이 본", "이어 보기", "구독 묶음", "실시간 인기"]
_ACTIONS = ["click", "view", "dwell", "scroll", "like", "dislike", "doublelike", "pick"]
_ITEM_TYPES = ["article", "video", "short"]
_DEVICES = ["ios", "aos", "web"]
_APP_VERS = ["11.2.0", "11.3.1", "11.4.0"]
_MOCK_SEED = 20260806                   # 시드 고정: 언제 불러도 같은 표가 나온다
_MOCK_N = 420
_ROWS_CACHE = {}                        # {오늘 날짜: 행 목록} · 날짜가 바뀌면 새로 만든다


def _build_rows(today: _dt.date) -> list:
    rnd = random.Random(_MOCK_SEED)
    rows = []
    for i in range(_MOCK_N):
        day = today - _dt.timedelta(days=rnd.randrange(7))
        stamp = _dt.datetime(day.year, day.month, day.day,
                             rnd.randrange(24), rnd.randrange(60), rnd.randrange(60))
        action = _ACTIONS[rnd.randrange(len(_ACTIONS))]
        dwell = rnd.randrange(800, 240000) if action in ("dwell", "view", "click") else rnd.randrange(200, 9000)
        rows.append({
            "ts": stamp.strftime("%Y-%m-%dT%H:%M:%S"),
            "svc": _SVCS[rnd.randrange(len(_SVCS))],
            "page": _PAGES[rnd.randrange(len(_PAGES))],
            "section": _SECTIONS[rnd.randrange(len(_SECTIONS))],
            "action": action,
            "item_id": "it_%08d" % rnd.randrange(10 ** 8),
            "item_type": _ITEM_TYPES[rnd.randrange(len(_ITEM_TYPES))],
            "pseudo_id": "ps_" + hashlib.sha1(("u%d" % rnd.randrange(120)).encode()).hexdigest()[:12],
            "session_id": "se_" + hashlib.sha1(("s%d" % rnd.randrange(240)).encode()).hexdigest()[:10],
            "dwell_ms": dwell,
            "scroll_pct": rnd.randrange(0, 101),
            "device": _DEVICES[rnd.randrange(len(_DEVICES))],
            "app_ver": _APP_VERS[rnd.randrange(len(_APP_VERS))],
            "rank": rnd.randrange(1, 31),
        })
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows


def _mock_rows() -> list:
    """오늘 기준 최근 7일치 모의 로그. 실서비스는 이 자리가 벨루가 골드 Iceberg 표 조회로 바뀐다."""
    key = _dt.date.today().isoformat()
    cached = _ROWS_CACHE.get(key)
    if cached is None:
        _ROWS_CACHE.clear()
        cached = _build_rows(_dt.date.today())
        _ROWS_CACHE[key] = cached
    return cached


def _metric_value(metric: str, day: str) -> float:
    """날짜마다 늘 같은 값이 나오게 해시로 만든다(무작위 흔들림 없음)."""
    h = int(hashlib.sha1((metric + ":" + day).encode()).hexdigest()[:8], 16)
    if metric == "dau":
        return 41000 + h % 7000
    if metric == "clicks":
        return 128000 + h % 26000
    return round(88 + (h % 260) / 10.0, 1)          # dwell: 평균 체류 초


_METRIC_LABEL = {"dau": ("하루 이용자 수", "명"), "clicks": ("클릭 수", "회"),
                 "dwell": ("평균 체류 시간", "초")}


def _tool_list_tables(params: dict) -> dict:
    return {"connector": CONNECTOR_ID, "catalog": "beluga", "layer": "gold",
            "format": "iceberg", "storage": "s3", "timeTravel": True,
            "tables": _tables(),
            "note": "골드 레이어(정리가 끝난 표)만 열려 있습니다 · 브론즈·실버는 스펙트럼 대상이 아닙니다. "
                    "모두 비식별 데이터라 사람을 지목할 수 있는 값은 들어 있지 않습니다."}


def _tool_query_logs(params: dict) -> dict:
    rows = _mock_rows()
    date_from = (params.get("date_from") or "").strip()[:10]
    date_to = (params.get("date_to") or "").strip()[:10]
    action = (params.get("action") or "").strip()
    svc = (params.get("svc") or "").strip()
    as_of = (params.get("as_of") or "").strip()[:10]
    try:
        limit = int(params.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(100, limit))
    out = []
    for r in rows:
        day = r["ts"][:10]
        if as_of and day > as_of:            # 타임 트래블: 그날 이후에 쌓인 줄은 그 스냅샷에 없다
            continue
        if date_from and day < date_from:
            continue
        if date_to and day > date_to:
            continue
        if action and r["action"] != action:
            continue
        if svc and r["svc"] != svc:
            continue
        out.append(r)
    table = "gold.user_action_log"
    snap_day = as_of or _dt.date.today().isoformat()
    return {"connector": CONNECTOR_ID, "table": table, "layer": "gold", "format": "iceberg",
            "rows": out[:limit], "total": len(out), "limit": limit,
            "filters": {"date_from": date_from, "date_to": date_to,
                        "action": action, "svc": svc, "as_of": as_of},
            "snapshot": {"id": _snapshot_id(table, snap_day), "asOf": snap_day,
                         "timeTravel": bool(as_of),
                         "desc": ("as_of 로 과거 시점의 표를 읽었습니다" if as_of
                                  else "지금 시점의 표를 읽었습니다")},
            "note": "모의 데이터입니다 · 실제 연결 시 벨루가 골드 레이어의 Iceberg 표를 조회합니다."}


def _tool_agg_metrics(params: dict) -> dict:
    metric = (params.get("metric") or "dau").strip()
    if metric not in _METRIC_LABEL:
        raise ValueError("metric 은 dau · clicks · dwell 중 하나여야 합니다")
    try:
        days = int(params.get("days") or 7)
    except (TypeError, ValueError):
        days = 7
    days = max(1, min(30, days))
    today = _dt.date.today()
    series = []
    for i in range(days - 1, -1, -1):
        day = (today - _dt.timedelta(days=i)).isoformat()
        series.append({"date": day, "value": _metric_value(metric, day)})
    label, unit = _METRIC_LABEL[metric]
    return {"connector": CONNECTOR_ID, "metric": metric, "label": label, "unit": unit,
            "days": days, "series": series,
            "table": "gold.user_session_daily", "layer": "gold", "format": "iceberg",
            "note": "모의 데이터입니다 · 실제 연결 시 벨루가 골드 레이어의 집계 표를 조회합니다."}


_DISPATCH = {
    "list_tables": _tool_list_tables,
    "query_logs": _tool_query_logs,
    "agg_metrics": _tool_agg_metrics,
}


def dispatch(tool: str, params: dict = None) -> dict:
    """도구 실행. 권한 확인은 authorize 가 이미 끝낸 뒤에 부른다."""
    fn = _DISPATCH.get(tool)
    if not fn:
        raise ValueError(f"실행할 수 없는 도구입니다: {tool}")
    return fn(params or {})


# ── 관문 코어 ───────────────────────────────────────────────────────────────
def _record(state: dict, user: str, tool: str, ok: bool, ms: int, note: str = ""):
    """사용 기록 적재(최근 USAGE_CAP 건만 남긴다). 호출 전에 _LOCK 을 잡는다."""
    state["usage"].append({"ts": _now(), "user": user or "unknown",
                           "connector": CONNECTOR_ID, "tool": tool,
                           "ok": bool(ok), "ms": int(ms), "note": (note or "")[:200]})
    if len(state["usage"]) > USAGE_CAP:
        state["usage"] = state["usage"][-USAGE_CAP:]


def call(tool: str, params: dict = None, key: str = "", key_id: str = "", user: str = None):
    """관문 하나뿐인 입구: 키 확인 → 권한 확인 → 실행 → 사용 기록.
    (HTTP 상태, 응답 본문) 을 돌려준다. 화면 체험과 실제 관문이 이 함수를 같이 쓴다."""
    t0 = time.time()
    tool = (tool or "").strip()
    with _LOCK:
        st = _load()
        rec, err = authenticate(key=key, key_id=key_id, user=user, state=st)
        who = (rec or {}).get("user") or (user or "unknown")
        if err:
            return _finish(st, 401, {"ok": False, "error": err, "detail": _ERR_TEXT[err]},
                           who, tool or "(없음)", t0, _ERR_TEXT[err])
        if tool not in _TOOL_BY_NAME:
            return _finish(st, 400,
                           {"ok": False, "error": "unknown_tool",
                            "detail": f"모르는 도구입니다: {tool or '(없음)'}",
                            "tools": [t["name"] for t in _TOOLS]},
                           who, tool or "(없음)", t0, "모르는 도구")
        allowed, detail = authorize(tool, rec)
        if not allowed:
            return _finish(st, 403, {"ok": False, "error": "forbidden", "detail": detail},
                           who, tool, t0, detail)
        try:
            result = dispatch(tool, params or {})
        except Exception as e:                       # 도구 안에서 난 문제는 400 으로 돌려준다
            return _finish(st, 400, {"ok": False, "error": "bad_params",
                                     "detail": str(e)[:200]}, who, tool, t0, str(e)[:200])
        rec["lastUsedAt"] = _now()
        rec["calls"] = int(rec.get("calls") or 0) + 1
        return _finish(st, 200, {"ok": True, "connector": CONNECTOR_ID, "tool": tool,
                                 "result": result}, who, tool, t0, "")


def _finish(state: dict, status: int, body: dict, user: str, tool: str, t0: float, note: str):
    ms = int((time.time() - t0) * 1000)
    body["ms"] = ms
    _record(state, user, tool, status == 200, ms, note)
    _save(state)
    return status, body


# ── 사용 기록·지표 ──────────────────────────────────────────────────────────
def metrics(state: dict = None) -> dict:
    """주간 지표: 호출 수 · 쓴 사람 수 · 살아 있는 키 · 평균 응답 · 실패 비율."""
    st = _load() if state is None else state
    now = _now()
    week = [u for u in st["usage"] if float(u.get("ts") or 0) >= now - 7 * 86400]
    ms = [int(u.get("ms") or 0) for u in week]
    fails = sum(1 for u in week if not u.get("ok"))
    active = sum(1 for k in st["keys"]
                 if not k.get("revoked") and float(k.get("expiresAt") or 0) >= now)
    return {
        "weekCalls": len(week),
        "weekUsers": len({u.get("user") for u in week if u.get("user")}),
        "activeKeys": active,
        "avgMs": int(sum(ms) / len(ms)) if ms else 0,
        "failRate": round(fails / len(week), 3) if week else 0.0,
        "totalCalls": len(st["usage"]),
    }


def usage_list(limit: int = 50, state: dict = None) -> list:
    st = _load() if state is None else state
    rows = sorted(st["usage"], key=lambda u: float(u.get("ts") or 0), reverse=True)
    return rows[:max(1, min(USAGE_CAP, int(limit or 50)))]


def reset_demo() -> dict:
    """시연 초기화: 사용 기록만 지운다(발급한 키는 그대로 둔다)."""
    with _LOCK:
        st = _load()
        n = len(st["usage"])
        st["usage"] = []
        _save(st)
    return {"ok": True, "cleared": n}


# ── 화면(실험실) 진입점 ─────────────────────────────────────────────────────
def spectrum_data(user: str) -> dict:
    """GET /spectrum: 카탈로그 · 내 키(마스킹) · 최근 사용 기록 · 지표."""
    user = (user or "").strip()
    with _LOCK:
        st = _load()
        now = _now()
        mine = [_key_public(k, now) for k in st["keys"] if k.get("user") == user]
        mine.sort(key=lambda k: k["createdAt"], reverse=True)
        return {
            "ok": True,
            "user": user,
            "version": VERSION,
            "catalog": catalog(),
            "keys": mine,
            "usage": usage_list(50, st),
            "metrics": metrics(st),
            "gateway": {
                "path": GATEWAY_PATH,
                "url": gateway_url(),
                "auth": "Bearer spk_*",
                "transport": "streamable-http",
                "protocolVersion": PROTOCOL_VERSION,
                "connectHint": 'claude mcp add --transport http spectrum '
                               + gateway_url() + ' --header "Authorization: Bearer spk_..."',
            },
        }


def spectrum_action(data: dict, user: str) -> dict:
    """POST /spectrum: 키 발급·폐기 · 관문 체험 · 시연 초기화."""
    data = data if isinstance(data, dict) else {}
    act = (data.get("action") or "").strip()
    if act == "issue_key":
        return issue_key(user, data.get("label") or "", data.get("ttlDays") or DEFAULT_TTL_DAYS)
    if act == "revoke_key":
        return revoke_key(user, data.get("id") or "")
    if act == "gw_try":
        status, body = call(data.get("tool") or "", data.get("params") or {},
                            key_id=(data.get("keyId") or ""), user=user)
        out = {"ok": status == 200, "status": status, "permitted": status not in (401, 403),
               "ms": body.get("ms", 0), "tool": (data.get("tool") or "")}
        if status == 200:
            out["result"] = body.get("result")
        else:
            out["error"] = body.get("error") or "error"
            out["detail"] = body.get("detail") or ""
        return out
    if act == "reset_demo":
        return reset_demo()
    return {"ok": False, "error": f"모르는 동작입니다: {act or '(없음)'}"}


# ── 공개 관문(POST /spectrum-gw · GET /spectrum-gw) ─────────────────────────
def gateway_info() -> dict:
    """GET /spectrum-gw: 무인증 안내. 어디로 어떻게 붙는지 한눈에."""
    return {
        "service": "spectrum-gateway",
        "version": VERSION,
        "auth": "Bearer spk_*",
        "connector": CONNECTOR_ID,
        "transport": "streamable-http",
        "protocolVersion": PROTOCOL_VERSION,
        "endpoint": gateway_url(),
        "tools": [t["name"] for t in _TOOLS],
        "hint": 'claude mcp add --transport http spectrum ' + gateway_url()
                + ' --header "Authorization: Bearer spk_..." (키는 프리즘 실험실 · 스펙트럼 탭에서 발급)',
    }


def _bearer(auth_header: str) -> str:
    h = (auth_header or "").strip()
    return h[7:].strip() if h[:7].lower() == "bearer " else ""


def _rpc_ok(rid, result) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _rpc_err(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _rpc_tools() -> list:
    return [{"name": t["name"],
             "description": (t["desc"] + (" (권한 제한: " + FORBIDDEN_DETAIL + ")")
                             if t["restricted"] else t["desc"]),
             "inputSchema": t["inputSchema"]} for t in _TOOLS]


def gateway_request(auth_header: str, body: bytes):
    """POST /spectrum-gw 진입점. (HTTP 상태, 응답 본문) · 본문이 None 이면 빈 응답.

    본문에 jsonrpc 키가 있으면 MCP(JSON-RPC 2.0 · 세션 없이 요청마다 완결) ·
    없으면 단순 REST({key?, tool, params?}). 두 갈래 모두 같은 관문 코어(call)를 쓴다."""
    try:
        data = json.loads(body or b"{}")
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "bad_json", "detail": "본문을 JSON 으로 읽을 수 없습니다"}
    if not isinstance(data, dict):
        return 400, {"ok": False, "error": "bad_json",
                     "detail": "본문은 JSON 오브젝트 하나여야 합니다(여러 건 묶음은 받지 않습니다)"}
    key = _bearer(auth_header) or (data.get("key") or "")
    if "jsonrpc" in data:
        return _mcp(key, data)
    return call((data.get("tool") or ""), data.get("params") or {}, key=key)


def _mcp(key: str, data: dict):
    """MCP 최소 구현. 세션 관리는 하지 않는다(요청마다 키로만 판단)."""
    method = (data.get("method") or "").strip()
    rid = data.get("id")
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    if method.startswith("notifications/"):
        return 202, None                               # 알림은 답이 없다(빈 응답)
    with _LOCK:
        st = _load()
        rec, err = authenticate(key=key, state=st)
        if err:
            _record(st, (rec or {}).get("user") or "unknown", method or "(없음)",
                    False, 0, _ERR_TEXT[err])
            _save(st)
            return 401, _rpc_err(rid, -32001, _ERR_TEXT[err])
        who = rec.get("user") or "unknown"
        if method == "initialize":
            _record(st, who, "initialize", True, 0, "")
            _save(st)
            return 200, _rpc_ok(rid, {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "spectrum-gateway", "version": VERSION},
                "instructions": "사내 행동 로그(비식별)를 물어볼 수 있는 커넥터입니다. "
                                "먼저 list_tables 로 어떤 표가 있는지 확인하세요.",
            })
        if method == "tools/list":
            _record(st, who, "tools/list", True, 0, "")
            _save(st)
            return 200, _rpc_ok(rid, {"tools": _rpc_tools()})
        if method != "tools/call":
            _record(st, who, method or "(없음)", False, 0, "모르는 메서드")
            _save(st)
            return 200, _rpc_err(rid, -32601, f"모르는 메서드입니다: {method or '(없음)'}")
    name = (params.get("name") or "").strip()
    args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
    status, out = call(name, args, key=key)            # 사용 기록은 call 안에서 적재
    if status == 200:
        return 200, _rpc_ok(rid, {"content": [{"type": "text",
                                               "text": json.dumps(out.get("result"), ensure_ascii=False)}]})
    # 도구 실행 단계의 실패는 MCP 규약대로 결과 안에 담아 보낸다(호출측이 문구를 읽게)
    return 200, _rpc_ok(rid, {"content": [{"type": "text",
                                           "text": out.get("detail") or out.get("error") or "요청을 처리하지 못했습니다"}],
                              "isError": True})
