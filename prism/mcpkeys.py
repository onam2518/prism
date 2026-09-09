"""MCP 파트너 키(트랙 B · 외부 MCP) 도메인 · 발급·해석·레이트리밋·사용 기록.

외부 서비스와 다른 팀이 프리즘 MCP 표면(`/mcp`)을 부를 때 쓰는 키다. 배포 키(`pr_live_`)와
별개로 새로 만든다 — 배포 키는 '프롬프트 슬러그' 에 매달린 키라 사용자·팀 바인딩이 없다.

  접두어    pmk_ (Prism MCP Key)
  저장      sha256 해시만 · 평문은 발급 응답에서 1회만 나가고 어디에도 남지 않는다
  바인딩    (user_id, team) 을 발급 시점에 고정 · 운영은 현재 소속과 맞을 때만 해석한다
  만료      기본 90일 · 최대 365일        사용자당 상한  5개(유효 키 기준)
  폐기      즉시 무효 · **(key_id, team, user_id) 3중 필터**로만 조회·폐기
  소유      목록·폐기 모두 본인 것만. 같은 팀이어도, 관리자여도 남의 키는 못 보고 못 지운다

설계가 감사 결과를 그대로 반영한 곳(그냥 취향이 아니다):

· `team` 이 비면 발급을 **거부**한다. 저장 계층은 team falsy 를 '내 팀 없음'이 아니라
  '전 팀'으로 해석한다 — 감사 H1 에서 팀 미소속 계정이 그 폴백을 타고 타 팀 콘텐츠를
  덮어썼다. 팀 없는 키는 만들지 않는 것이 유일하게 안전한 처리다.
· `revoke` 는 `(key_id, team, user_id)` 3중 필터다. 배포 키가 팀 스코프 없이 만들어져 자기 팀
  관리자가 **타 팀** 키를 끊을 수 있었다(감사 O3 · deployops.deployment_key_revoke 주석).
  소유자 필터는 그 반대편이다 — 팀 경계는 봤는데 소유자를 안 보면 같은 팀 아무나 남의 키를
  끊는다. key_id 단독으로 지우는 경로를 이 모듈에 두지 않는다.
· `key_id` 는 순차 정수가 아니라 난수 문자열이다. 감사 O3 의 피해가 커진 이유가 순차 id 라
  남의 키를 찍어 맞힐 수 있었던 것이다.
· 인증 실패는 **기록하지 않는다**. 감사 O2 에서 옛 스펙트럼 관문이 인증 실패마다 상태 파일을
  전량 재기록해, 틀린 키로 600번 두드리면 실사용 감사 기록 500건이 밀려났다.
  `log_call` 은 `resolve` 를 통과한 호출만 받는다(레이트리밋에 걸린 호출도 대상이 아니다).

전송 담당(`/mcp`)과의 경계 계약 — 이 세 개만 쓴다:
    resolve(raw_key)  -> {"user_id","team","key_id","is_admin"} | None
    rate_check(key_id)-> (allowed: bool, retry_after_sec: int)
    log_call(key_id, tool, ok, ms, resp_bytes) -> None
관리용(전송은 안 쓴다): issue(user_id, team, days, label) · list_keys(user_id, team) ·
    revoke(key_id, team, user_id) · usage(key_id, team, user_id)
관리용 셋은 전부 **소유자 축을 필수로** 받는다. 화면이 '내 키만' 을 지키는 것이 아니라
백엔드가 지킨다 — 화면을 우회해 직접 POST 해도 남의 키는 손댈 수 없어야 한다.

컴포지션: learnops·deployops 와 동일 — serve 가 기동 시 `_SV`(자기 모듈 객체)를 주입한다.
저장은 store.py(SQLite) / supastore.py(Supabase) 가 같은 계약으로 구현한다.
"""
from __future__ import annotations
import hashlib
import secrets
import threading
import time

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입

KEY_PREFIX = "pmk_"
PREFIX_CHARS = 6                # 평문에서 화면에 남길 비밀부 글자 수(그 뒤는 영원히 못 본다)
LOCAL_TEAM = "local"            # sqlite 단독 모드의 단일 테넌트 이름(운영에는 존재하지 않는다)

DEFAULT_DAYS = 90
MAX_DAYS = 365
MAX_KEYS_PER_USER = 5
MAX_LABEL = 60

# ── 호출 상한(감사 H4) · **아래 두 값은 근거 없는 초안이다** ───────────────
# 실사용 데이터가 없는 상태에서 정한 자리 지킴이 숫자다. 분당만 두면 하루 86,400 호출이
# 통과하므로 일일 상한을 같이 둔다는 '구조'만 확정이고, 숫자 자체는 미확정이다.
#
# 무엇을 보고 조정하나 — 이미 다 기록하고 있으므로 새로 계측할 것은 없다:
#   · 키·도구별 호출 분포   mcp_calls(key_id, tool, ts)          → 정상 사용의 분당 최대치
#   · 성공/실패 비율        mcp_calls.ok                          → 실패 폭주(재시도 루프) 탐지
#   · 응답 크기·소요        mcp_calls(resp_bytes, ms)             → 비용 기준 상한이 필요한지
# 조정 기준: 정상 사용 상위 백분위(p99)가 상한에 닿기 시작하면 올린다. 반대로 상한에 닿는
# 키가 계속 특정 하나뿐이면 그건 상한이 낮은 게 아니라 그 키가 잘못 쓰이는 것이다.
# 스펙 §7 이 '도구별 호출 분포를 2단계 도구 선정 근거로 쓴다'고 한 것과 같은 원천을 본다.
PER_MIN = 60                    # 키당 분당 호출 상한 · 초안
PER_DAY = 5000                  # 키당 일일 호출 상한 · 초안
DAY_SEC = 86400.0

# 발급 응답 안내 문구(화면·API 공통 · 평문을 다시 볼 수 없다는 사실을 여기 한 곳에서 정한다)
ONCE_HINT = "이 키는 지금 이 화면에서만 보입니다 · 서버는 해시만 저장하므로 다시 조회할 수 없습니다"


def _hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _store():
    st = _SV.get_store() if _SV else None
    return st if (st and hasattr(st, "mcp_key_add")) else None


def _bare(raw: str) -> str:
    """`Authorization: Bearer pmk_…` 와 생 키를 같이 받는다."""
    t = (raw or "").strip()
    return t[7:].strip() if t[:7].lower() == "bearer " else t


def _team(team) -> str:
    return (team or "").strip() if isinstance(team, str) else ""


def scope_team(team):
    """요청 팀 → 키 스코프. 운영(supabase)은 요청 팀 그대로(없으면 그대로 없음 = 발급 거부),
    sqlite 단독은 팀 개념이 없으므로 단일 테넌트 이름으로 정규화한다.
    **falsy 를 그대로 스토어에 흘리지 않기 위한 곳이 여기 하나뿐**이다 — issue 는 여전히
    빈 팀을 거부하므로, 로컬 편의가 운영의 fail-closed 를 무르게 만들지 않는다."""
    t = _team(team)
    if t:
        return t
    try:
        return None if _SV._supa() else LOCAL_TEAM
    except Exception:
        return None


def _is_admin(user_id, team) -> bool:
    """키 권한 = 발급자의 **지금** 권한(발급 시점 스냅샷이 아니다 → 강등이 즉시 반영된다).
    운영 관리자 허용목록은 이메일 기반인데 키 호출에는 이메일이 없다 → 판정하지 않는다.
    그래서 키 권한은 발급자 권한보다 같거나 낮다(넘지 않는다 = 요구사항).
    sqlite 단독은 게이트 자체가 열려 있는 로컬 모드라 그대로 관리자."""
    try:
        if not _SV._supa():
            return True
        return bool(_SV.is_admin_user(user_id, team, ""))
    except Exception:
        return False                                  # 판정 불가 = 비관리자(fail-closed)


def _is_current_member(st, user_id, team) -> bool:
    """운영 키의 발급자가 지금도 같은 팀 소속인지 확인한다.

    로컬 SQLite 단일 팀 계약은 그대로 유지한다. 운영에서 조회 기능이 없거나
    조회가 실패하면 현재 소속을 증명할 수 없으므로 인증을 거절한다.
    """
    try:
        if not _SV._supa():
            return True
        lookup = getattr(st, "reviewer_team", None)
        return callable(lookup) and _team(lookup(user_id)) == team
    except Exception:
        return False


# ── 발급·목록·폐기(관리 화면) ────────────────────────────────────────────────
def issue(user_id, team, days: int = DEFAULT_DAYS, label: str = "") -> dict:
    """파트너 키 발급. 평문(`key`)은 **이 응답에서만** 나간다(저장은 sha256).

    반환: {"ok": True, "key", "key_id", "prefix", "expires_at", "hint"} · 실패 시 {"ok": False, "error"}
    """
    st = _store()
    if not st:
        return {"ok": False, "error": "스토어가 MCP 키를 지원하지 않습니다"}
    uid = (user_id or "").strip()
    tm = _team(team)
    if not tm:
        # 팀 없는 키를 만들면 그 키의 모든 스토어 호출이 team=None 으로 나가고, 저장 계층은
        # 그걸 '전 팀'으로 읽는다(감사 H1). 발급 자체를 막는 것이 유일한 안전 처리다.
        return {"ok": False, "error": "팀 소속이 필요합니다 · 팀에 속하지 않은 계정은 키를 발급할 수 없습니다"}
    if not uid:
        return {"ok": False, "error": "발급자 식별이 필요합니다"}
    try:
        days = int(days or DEFAULT_DAYS)
    except (TypeError, ValueError):
        days = DEFAULT_DAYS
    days = max(1, min(MAX_DAYS, days))                # 클램프 · 예외 원문을 응답에 싣지 않는다(감사 H3)
    live = [k for k in list_keys(uid, tm) if not k["revoked"] and not k["expired"]]
    if len(live) >= MAX_KEYS_PER_USER:
        return {"ok": False,
                "error": f"키는 사용자당 {MAX_KEYS_PER_USER}개까지입니다 · 쓰지 않는 키를 폐기하고 다시 발급하세요"}
    token = KEY_PREFIX + secrets.token_urlsafe(32)
    prefix = token[:len(KEY_PREFIX) + PREFIX_CHARS] + "…"
    key_id = secrets.token_hex(12)                    # 난수 id · 순차 정수면 남의 키를 찍어 맞힐 수 있다(감사 O3)
    now = time.time()
    expires_at = now + days * DAY_SEC
    st.mcp_key_add(uid, tm, key_id, _hash(token), prefix, (label or "").strip()[:MAX_LABEL], expires_at)
    return {"ok": True, "key": token, "key_id": key_id, "prefix": prefix,
            "expires_at": expires_at, "hint": ONCE_HINT}


def list_keys(user_id, team) -> list:
    """내 키 목록(비밀 없음 · 접두 6자만). (user_id, team) 복합 필터로 조회한다."""
    st = _store()
    tm = _team(team)
    uid = (user_id or "").strip()
    if not (st and tm and uid):
        return []                                     # 팀·사용자 미상 = 빈 목록(전 팀 폴백 금지)
    now = time.time()
    out = []
    for r in st.mcp_keys_for(uid, tm):
        exp = float(r.get("expires_at") or 0)
        out.append({"key_id": str(r.get("key_id") or ""), "prefix": r.get("prefix") or "",
                    "label": r.get("label") or "", "created_at": float(r.get("created_at") or 0),
                    "expires_at": exp, "last_used_at": float(r.get("last_used_at") or 0),
                    "revoked": bool(r.get("revoked")),
                    "expired": bool(exp and exp <= now)})
    return out


def revoke(key_id, team, user_id) -> bool:
    """키 폐기(즉시 무효). **(key_id, team, user_id) 3중 필터** — 하나라도 안 맞으면 False.

    팀 필터는 감사 O3(자기 팀 관리자가 타 팀 키를 폐기) 회귀 지점이고, 소유자 필터는 그
    반대편이다: 팀 경계는 봤는데 소유자를 안 보면 같은 팀 아무나 남의 키를 끊을 수 있다.
    키는 개인 자격증명이므로 **관리자도 남의 키를 지우지 못한다**(권한이 아니라 소유의 문제).
    셋 중 하나라도 비면 거부한다 — falsy 를 '전체'로 읽는 자리를 만들지 않는다.
    """
    st = _store()
    tm = _team(team)
    kid = str(key_id or "").strip()
    uid = (user_id or "").strip()
    if not (st and tm and kid and uid):
        return False                                  # 팀·소유자 미상 = 거부(fail-closed)
    ok = bool(st.mcp_key_revoke(kid, tm, uid))
    if ok:
        with _RL_LOCK:                                # 폐기 즉시 캐시에서도 지운다
            _META.pop(kid, None)
    return ok


def usage(key_id, team, user_id) -> dict:
    """키 1개의 오늘 사용량(성공/실패 버킷 분리). 화면 표시용 · 판정에는 쓰지 않는다.

    호출처(GET /mcp-keys)가 이미 소유자 목록만 넘기지만 여기서도 팀·소유자를 다시 본다 —
    단독으로 불러도 남의 키 사용량이 새지 않아야 다음 호출자가 실수할 여지가 없다.
    """
    st = _store()
    kid = str(key_id or "").strip()
    tm = _team(team)
    uid = (user_id or "").strip()
    if not (st and kid and tm and uid and hasattr(st, "mcp_call_count")):
        return {"ok": 0, "fail": 0}
    row = st.mcp_key_find(key_id=kid)
    if not row or _team(row.get("team")) != tm or (row.get("user_id") or "") != uid:
        return {"ok": 0, "fail": 0}
    since = _day_start(time.time())
    return {"ok": int(st.mcp_call_count(kid, since, ok=True)),
            "fail": int(st.mcp_call_count(kid, since, ok=False))}


# ── 전송(/mcp)이 쓰는 세 함수 ────────────────────────────────────────────────
def resolve(raw_key):
    """생 키 → {"user_id","team","key_id","is_admin"} · 실패는 **전부 None**.

    없음·형식오류·불일치·만료·폐기를 구분해 주지 않는다(호출자가 키 상태를 열거하지 못하게).
    실패 경로에서는 아무것도 저장하지 않는다 — 감사 O2 의 '인증 실패가 감사 기록을 밀어냄'을
    구조적으로 불가능하게 만드는 것이 이 함수의 책임이다.
    """
    st = _store()
    if not st:
        return None
    token = _bare(raw_key)
    if not token.startswith(KEY_PREFIX) or len(token) <= len(KEY_PREFIX) + PREFIX_CHARS:
        return None
    try:
        row = st.mcp_key_find(key_hash=_hash(token))
    except Exception:
        return None                                   # 스토어 일시 오류도 인증 실패와 같게(fail-closed)
    if not row or row.get("revoked"):
        return None
    team = _team(row.get("team"))
    uid = (row.get("user_id") or "").strip()
    if not (team and uid):
        return None                                   # 팀 없는 키는 발급되지 않지만, 있어도 안 받는다
    exp = float(row.get("expires_at") or 0)
    if exp and exp <= time.time():
        return None
    if not _is_current_member(st, uid, team):
        return None                                   # 소속 해제·이동·조회 실패 = 인증 거절
    kid = str(row.get("key_id") or "")
    prefix = row.get("prefix") or ""
    with _RL_LOCK:                                    # log_call 이 쓸 비밀 없는 메타(사용자·팀·접두)
        if len(_META) > 4096:
            _META.clear()
        _META[kid] = (uid, team, prefix)
    try:
        st.mcp_key_touch(kid)                         # 마지막 사용 시각(감사) · 실패해도 인증에는 무해
    except Exception:
        pass
    return {"user_id": uid, "team": team, "key_id": kid, "is_admin": _is_admin(uid, team)}


def rate_check(key_id):
    """(허용 여부, 재시도까지 남은 초). 키당 **분당 상한 + 일일 상한**(감사 H4).

    분당은 프로세스 안 슬라이딩 윈도. 일일은 프로세스 메모리에 세되, 그날 첫 호출에서만
    사용 기록으로 기준선을 채운다 — main 머지마다 재배포되는 환경에서 재시작이 일일 상한을
    통째로 리셋해 버리는 구멍을 막는다(왕복은 키·날짜당 1회).
    막힌 호출은 창에 넣지 않는다(막혀도 계속 두드리면 영영 못 들어오는 일이 없게).
    """
    kid = str(key_id or "").strip()
    if not kid:
        return False, 60
    now = time.time()
    day = _day_start(now)
    base = _day_base(kid, day)                        # 스토어 왕복은 락 밖에서(전 키 직렬화 방지)
    with _RL_LOCK:
        _sweep(now)
        q = _MIN_HITS.setdefault(kid, [])
        while q and now - q[0] > 60:
            q.pop(0)
        if len(q) >= PER_MIN:
            return False, max(1, int(60 - (now - q[0])) + 1)
        d, used = _DAY_HITS.get(kid) or (day, 0)
        if d != day:
            used = 0
        total = base + used
        if total >= PER_DAY:
            return False, max(1, int(day + DAY_SEC - now))
        q.append(now)
        _DAY_HITS[kid] = (day, used + 1)
        return True, 0


def log_call(key_id, tool, ok, ms, resp_bytes) -> None:
    """호출 1건 기록: 키 접두 · 사용자 · 팀 · 도구명 · 성공여부 · 소요 ms · 응답 바이트.

    **인증을 통과한 호출만 넣는다.** 인증 실패를 여기로 흘리면 틀린 키를 난타하는 것만으로
    실사용 기록이 밀려난다(감사 O2). 기록 실패는 삼킨다 — 감사 적재가 도구 응답을 막지 않는다.
    """
    st = _store()
    kid = str(key_id or "").strip()
    if not (st and kid and hasattr(st, "mcp_call_add")):
        return
    meta = _META.get(kid)
    if not meta:                                      # resolve 뒤에 오는 것이 계약이라 사실상 안 오는 경로
        row = None
        try:
            row = st.mcp_key_find(key_id=kid)
        except Exception:
            row = None
        if not row:
            return                                    # 정체를 모르는 호출은 기록하지 않는다(추측 금지)
        meta = ((row.get("user_id") or ""), _team(row.get("team")), (row.get("prefix") or ""))
    uid, team, prefix = meta
    try:
        st.mcp_call_add(kid, uid, team, prefix, str(tool or "")[:64], bool(ok),
                        _int(ms), _int(resp_bytes))
    except Exception:
        pass


# ── 레이트리밋 내부 상태(프로세스 메모리) ────────────────────────────────────
_RL_LOCK = threading.Lock()
_MIN_HITS: dict = {}            # key_id -> [호출 시각…] (최근 60초)
_DAY_HITS: dict = {}            # key_id -> (일 시작 epoch, 이 프로세스가 센 수)
_DAY_BASE: dict = {}            # key_id -> (일 시작 epoch, 재시작 전 기준선)
_META: dict = {}                # key_id -> (user_id, team, prefix) · resolve 가 채운다


def _int(v) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0                                      # 잘못된 인자는 0 으로 클램프(예외 원문 응답 금지)


def _day_start(now: float) -> float:
    """팀 타임존(KST 기본) 기준 그날 0시의 epoch. 일별 롤업과 같은 버킷을 쓴다."""
    try:
        from .store import _tz_sec
        tz = _tz_sec()
    except Exception:
        tz = 0
    return ((now + tz) // DAY_SEC) * DAY_SEC - tz


def _day_base(kid: str, day: float) -> int:
    """그날의 기준선(이 프로세스가 세기 전에 이미 쌓인 호출 수). 키·날짜당 1회만 조회."""
    with _RL_LOCK:
        cur = _DAY_BASE.get(kid)
        if cur and cur[0] == day:
            return cur[1]
    n = 0
    st = _store()
    if st and hasattr(st, "mcp_call_count"):
        try:
            n = int(st.mcp_call_count(kid, day))
        except Exception:
            n = 0
    with _RL_LOCK:
        _DAY_BASE[kid] = (day, n)
    return n


def _sweep(now: float):
    """장기 가동 중 무한 성장 방지 · 호출자가 _RL_LOCK 을 잡고 있어야 한다."""
    if len(_MIN_HITS) <= 512:
        return
    for k in [k for k, v in _MIN_HITS.items() if not v or now - v[-1] > 60]:
        _MIN_HITS.pop(k, None)
        _DAY_HITS.pop(k, None)
        _DAY_BASE.pop(k, None)


def _reset_state():
    """테스트 전용: 프로세스 메모리 상태 초기화(레이트리밋 창·기준선·메타)."""
    with _RL_LOCK:
        _MIN_HITS.clear()
        _DAY_HITS.clear()
        _DAY_BASE.clear()
        _META.clear()
