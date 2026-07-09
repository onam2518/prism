"""관리자·팀·인증 도메인 (serve 에서 분리 · 라우트 분리 2차).

로그인/가입 프록시(서버만 키 보유), JWT 검증 캐시, 권한 2단계(운영 관리자=허용목록 ·
팀 관리자=생성자/위임), 팀 관리 액션(데이터 삭제·멤버·팀 생성)을 담당한다.

컴포지션: 서버 환경(스토어·인입 fetch·LLM·mock 플래그)은 serve 가 기동 시
`_SV`(자기 모듈 객체)로 주입한다(learnops 와 동일 구조 · 순환 import 없음).
"""
from __future__ import annotations
import json
import os
import threading
import time
import urllib.error
import urllib.request

from . import pipeline as PIPE
from .config import Config

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입


_JWT_CACHE = {}

_JWT_LOCK = threading.Lock()

def _supa():
    """(url, service_key) 또는 None(=sqlite 모드). 판정은 스토어 선택(serve.backend_mode)과 동일:
    명시 supabase 또는 '미설정 + 키 존재'(운영 자동)면 supabase · sqlite 명시만 로컬 강제.
    과거엔 명시 supabase 만 인정해, 키 자동 감지로 뜬 운영 서버에서 인증·관리자 게이트가
    전부 비활성화되는 불일치가 있었다."""
    from . import supastore
    b = (os.environ.get("PRISM_BACKEND") or "").strip().lower()
    if b != "sqlite" and supastore.configured():
        return os.environ["SUPABASE_URL"].rstrip("/"), os.environ["SUPABASE_SERVICE_KEY"]
    return None

def _auth_post(url, path, key, body):
    req = urllib.request.Request(url + path, method="POST",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"apikey": key, "Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}

def auth_action(data: dict) -> dict:
    """로그인/가입/세션 갱신 프록시(서버만 키 보유). mode=login|signup|refresh. 키는 프론트에 노출 안 함.
    refresh: access token 1시간 만료 후에도 refresh_token 으로 무중단 연장(관리자 메뉴가
    조용히 사용자 메뉴로 강등되던 원인 = 만료 토큰의 401 · 2026-07-08)."""
    s = _supa()
    if not s:
        return {"ok": False, "error": "supabase 모드가 아닙니다"}
    url, key = s
    try:
        if data.get("mode") == "refresh":            # 세션 갱신: 이메일·비밀번호 불필요
            rt = (data.get("refresh_token") or "").strip()
            if not rt:
                return {"ok": False, "error": "갱신 토큰 없음 · 다시 로그인하세요"}
            tok = _auth_post(url, "/auth/v1/token?grant_type=refresh_token", key, {"refresh_token": rt})
            at = tok.get("access_token")
            if not at:
                return {"ok": False, "error": "세션 갱신 실패 · 다시 로그인하세요"}
            return {"ok": True, "access_token": at, "refresh_token": tok.get("refresh_token") or rt,
                    "uid": (tok.get("user") or {}).get("id"),
                    "email": ((tok.get("user") or {}).get("email") or "")}
        email = (data.get("email") or "").strip()
        pw = data.get("password") or ""
        if not email or not pw:
            return {"ok": False, "error": "이메일·비밀번호를 입력하세요"}
        if data.get("mode") == "signup":            # 내부 도구: 가입 즉시 확인(admin)
            try:
                _auth_post(url, "/auth/v1/admin/users", key,
                           {"email": email, "password": pw, "email_confirm": True})
            except urllib.error.HTTPError as e:
                if e.code not in (422, 409):        # 이미 존재 → 로그인으로 진행
                    raise
        tok = _auth_post(url, "/auth/v1/token?grant_type=password", key, {"email": email, "password": pw})
        at = tok.get("access_token")
        if not at:
            return {"ok": False, "error": "로그인 실패"}
        return {"ok": True, "access_token": at, "refresh_token": tok.get("refresh_token") or "",
                "uid": (tok.get("user") or {}).get("id"), "email": email}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP{e.code}: {e.read().decode('utf-8', 'replace')[:160]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}

class AuthBackendUnavailable(Exception):
    """인증 서버(supabase auth) 일시 장애: 토큰 무효와 구분해 재시도 가능(503)으로 응답하기 위한 신호."""


def validate_jwt(token: str, strict: bool = False):
    """user JWT → uid(검증). 60s 캐시. 실패 시 None.
    strict=True: 인증 서버 연결 실패(일시 장애)를 토큰 무효와 구분해 AuthBackendUnavailable 로 던진다.
    (구분 없이 None 을 주면 /admin 이 200+isAdmin:false 로 굳어 관리자 메뉴가 사라지는 간헐 증상)"""
    s = _supa()
    if not s or not token:
        return None
    now = time.time()
    with _JWT_LOCK:
        hit = _JWT_CACHE.get(token)
        if hit and hit[1] > now:
            return hit[0]
    url, key = s
    uid = None
    email = ""
    try:
        req = urllib.request.Request(url + "/auth/v1/user", method="GET",
                                     headers={"apikey": key, "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            u = json.loads(r.read().decode("utf-8"))
            uid = u.get("id")
            email = (u.get("email") or "").strip().lower()
    except urllib.error.HTTPError:
        uid = None                                   # 4xx = 토큰 무효(확정 · 재시도 무의미)
    except Exception:
        uid = None                                   # 네트워크/타임아웃 = 일시 장애
        if strict:
            raise AuthBackendUnavailable()
    if uid:
        with _JWT_LOCK:
            _JWT_CACHE[token] = (uid, now + 60)
            _JWT_EMAIL[token] = (email, now + 60)
    return uid

_JWT_EMAIL = {}                                  # token -> (email, expiry)

def jwt_email(token: str) -> str:
    """user JWT → email(소문자). validate_jwt 캐시 재사용. 관리자 허용목록 판정용."""
    if not token:
        return ""
    with _JWT_LOCK:
        hit = _JWT_EMAIL.get(token)
    if hit and hit[1] > time.time():
        return hit[0]
    validate_jwt(token)                          # 캐시 채우기(email 동반)
    with _JWT_LOCK:
        hit = _JWT_EMAIL.get(token)
    return hit[0] if hit else ""

def admin_emails() -> set:
    """관리자 허용목록(엄격 모드). env PRISM_ADMIN_EMAILS 또는 ~/.prism_admin_emails(콤마/개행 구분)."""
    raw = os.environ.get("PRISM_ADMIN_EMAILS", "")
    if not raw:
        try:
            p = os.path.expanduser("~/.prism_admin_emails")
            if os.path.exists(p):
                raw = open(p, encoding="utf-8").read()
        except Exception:
            raw = ""
    return {e.strip().lower() for e in raw.replace("\n", ",").split(",") if e.strip()}

def _team_admin(uid, team) -> bool:
    """팀 관리자(생성자 OR is_admin 위임) 판정."""
    st = _SV.get_store()
    return bool(st and team and hasattr(st, "is_team_admin") and st.is_team_admin(uid, team))

def _team_super(uid, team) -> bool:
    """슈퍼관리자(생성자 OR super_admin 위임) 판정."""
    st = _SV.get_store()
    return bool(st and team and hasattr(st, "is_team_super") and st.is_team_super(uid, team))

def is_sys_admin_user(uid, team, email="") -> bool:
    """운영(시스템) 관리자 = 허용목록(~/.prism_admin_emails) 이메일. 팀 소속과 무관.
    허용목록 미설정 시 팀 관리자 로직으로 폴백(단독 운영 호환)."""
    allow = admin_emails()
    if allow:
        return bool(email and email.strip().lower() in allow)
    return _team_admin(uid, team)

def is_super_admin_user(uid, team, email="") -> bool:
    """운영 작업 관리자 = 운영 관리자 OR 슈퍼관리자(생성자 부여).
    관리자 메뉴 전체를 열되 위험 작업(시스템 설정·데이터/팀 삭제·API 키)은 운영 관리자 전용 유지."""
    return is_sys_admin_user(uid, team, email) or _team_super(uid, team)

def is_admin_user(uid, team, email="") -> bool:
    """관리자(팀 관리 접근) = 운영 관리자 OR 슈퍼관리자 OR 팀 관리자(생성자·위임).
    권한 3단계: 운영 관리자(전부) > 슈퍼관리자(운영 작업) > 팀 관리자('팀 관리'만)."""
    return is_sys_admin_user(uid, team, email) or _team_super(uid, team) or _team_admin(uid, team)

def admin_data(uid, team, email="") -> dict:
    """팀 관리: 팀 정보·멤버·관리자 여부. supabase 전용.
    팀 미소속이어도 운영 관리자(허용목록)는 isSysAdmin/isAdmin 을 내려 관리자 메뉴가 열리게 한다."""
    st = _SV.get_store()
    if not (st and team and hasattr(st, "team_members")):
        sysadm = is_sys_admin_user(uid, team, email)
        return {"ok": False, "isAdmin": sysadm, "isSysAdmin": sysadm, "isSuperAdmin": sysadm,
                "isCreator": False, "team": None, "members": []}
    gc = st.golden_count(team) if hasattr(st, "golden_count") else 0
    t = st.team_info(team)
    return {"ok": True, "isAdmin": is_admin_user(uid, team, email),
            "isSysAdmin": is_sys_admin_user(uid, team, email),
            "isSuperAdmin": is_super_admin_user(uid, team, email),
            "isCreator": bool(t and uid and t.get("created_by") == uid),
            "team": t, "members": st.team_members(team), "goldenCount": gc}

def admin_ingest(uid, team, endpoint, n, email="") -> dict:
    """관리자: 크롤러 엔드포인트에서 N건 당겨와 추출 → 전건 검토 대상으로 팀 큐 적재(배치).
    실시간 스트리밍 부담 없이 관리자가 수량 목표로 트리거."""
    st = _SV.get_store()
    if not is_admin_user(uid, team, email):
        return {"ok": False, "error": "관리자 전용입니다"}
    n = max(1, min(int(n or 20), 200))             # 수량 상한(응답성)
    from . import ingest as ING
    rows, err = _SV._fetch_records((endpoint or "").strip(), n, "GET", "")
    if err:
        return {"ok": False, "error": err}
    try:
        contents, _m = ING.to_contents_rows(rows[:n])
    except Exception as e:
        return {"ok": False, "error": f"형식 매핑 실패: {str(e)[:140]}"}
    cfg = Config.load()
    llm = _SV.make_text_llm(cfg, _SV.Handler.server_mock)
    pairs = []
    for c in contents:
        out = PIPE.extract(c, llm, legal=cfg.legal_enabled)
        out.setdefault("quality_meta", {})["review"] = "yellow"   # 인입 배치는 전건 검토 대상
        pairs.append((c, out))
    st.sync_contents(pairs, source="자동 인입", team=team)
    return {"ok": True, "fetched": len(contents), "queued": len(pairs)}

def _score_rows(st, team) -> list:
    """현재 리더보드(점수는 원천 데이터에서 파생 계산). 실패는 빈 목록(게임 계층은 베스트 에포트)."""
    try:
        return (st.arena_stats(team=team) or {}).get("leaderboard") or []
    except Exception:
        return []


def carry_scores(st, team):
    """피드백 전체 삭제 직전: 삭제로 사라질 점수 몫(검수·교정·합의)을 이벤트 적립으로 보존.
    남는 원천(patch_log 5점 · gold_checks 10점 · 기존 보너스)에서 다시 계산될 몫은 빼서
    이중 적립을 막는다 — 삭제 후 총점이 삭제 전과 같게(게임 진척도와 데이터 관리 분리)."""
    bonuses = st.event_bonus(team) if hasattr(st, "event_bonus") else {}
    day = int(time.time())                         # 초 단위 = 같은 초 재실행만 dedupe(멱등 안전망)
    for row in _score_rows(st, team):
        rid = row.get("reviewer_id") or ""
        mult = row.get("quality_mult") or 1
        surviving = round((int(row.get("patches") or 0) * 5 + int(row.get("gold_n") or 0) * 10) * mult)
        carry = (int(row.get("points") or 0)
                 - int((bonuses.get(rid) or {}).get("total") or 0) - surviving)
        if rid and carry > 0:
            st.log_event_once(rid, "score_carry", day, carry, meta="피드백 삭제 · 점수 보존", team=team)


def reset_scores(st, team) -> int:
    """게임 점수 초기화: 검수 데이터는 건드리지 않고 현재 총점만큼 음수 오프셋 이벤트를 적립.
    파생 계산이라 원천 삭제 없이도 0부터 다시 시작한다(배지·검수 이력·골든 불변)."""
    day = int(time.time())
    n = 0
    for row in _score_rows(st, team):
        rid = row.get("reviewer_id") or ""
        pts = int(row.get("points") or 0)
        if rid and pts > 0 and st.log_event_once(rid, "score_reset", day, -pts,
                                                 meta="관리자 점수 초기화", team=team):
            n += 1
    return n


def admin_action(uid, team, data, email="") -> dict:
    """관리자 액션(데이터 삭제·멤버 제거). 팀 생성자만."""
    st = _SV.get_store()
    if _supa() and not is_admin_user(uid, team, email):    # 로컬 단독 실행 = 관리자 취급(타 라우트와 동일)
        return {"ok": False, "error": "관리자 전용입니다"}
    act = data.get("action")
    data_ops = ("clear_feedback", "clear_contents", "clear_golden", "delete_team", "reset_scores")
    if act in data_ops and _supa() and not is_sys_admin_user(uid, team, email):
        return {"ok": False, "error": "데이터 관리는 운영 관리자 전용입니다"}
    if act == "clear_feedback":
        carry_scores(st, team)                     # 게임 점수·레벨은 보존(분리) · 초기화는 별도 버튼
        st.clear_team_feedback(team)
        _SV._agg_bump()                            # 아레나·대시보드 집계 캐시 즉시 무효화
    elif act == "reset_scores":                    # 게임 점수 초기화(검수 데이터·배지 불변)
        n = reset_scores(st, team)
        _SV._agg_bump()
        return {"ok": True, "reset": n}
    elif act == "clear_contents":
        st.clear_team_contents(team)
    elif act == "clear_golden":                    # 정답셋 전체 삭제(되돌릴 수 없음)
        st.register_golden(team, [], replace=True, source="manual")
    elif act == "delete_team":                     # 팀 삭제: 멤버 소속 해제 + 팀 행 삭제
        if not hasattr(st, "delete_team"):
            return {"ok": False, "error": "이 백엔드는 팀 삭제를 지원하지 않습니다"}
        st.delete_team(team)
    elif act == "remove_member" and data.get("member"):
        st.remove_member(team, data["member"])
    elif act in ("set_admin", "unset_admin", "set_super", "unset_super") and data.get("member"):
        t = st.team_info(team) if hasattr(st, "team_info") else None
        # 권한 지정은 오직 팀 생성자만 · 부여받은 관리자(슈퍼관리자 포함)와 운영 관리자도 불가
        if _supa() and not (t and uid and uid == t.get("created_by")):
            return {"ok": False, "error": "권한 지정은 팀 생성자만 할 수 있습니다"}
        if t and data["member"] == t.get("created_by"):
            return {"ok": False, "error": "생성자의 권한은 변경할 수 없습니다"}
        fn = "set_member_super" if act in ("set_super", "unset_super") else "set_member_admin"
        if not hasattr(st, fn):
            return {"ok": False, "error": "이 백엔드는 위임을 지원하지 않습니다"}
        getattr(st, fn)(team, data["member"], act in ("set_admin", "set_super"))
    elif act == "ingest":                          # 크롤러 수량 인입 → 검토 큐
        return admin_ingest(uid, team, data.get("endpoint"), data.get("n"), email)
    elif act == "create_team" and not hasattr(st, "ensure_team"):
        return {"ok": False, "error": "팀 기능은 팀 모드(공유 서버)에서 동작합니다 · 로컬 단독 실행은 팀 없이 개인 검수로 진행됩니다"}
    elif act == "create_team":                    # 관리자: 새 팀 생성(초대코드 발급)
        nm = (data.get("name") or "").strip()
        if not nm:
            return {"ok": False, "error": "팀 이름을 입력하세요"}
        tid = st.ensure_team(uid, "create", nm)
        if not tid:
            return {"ok": False, "error": "팀 생성 실패"}
        prof = st.get_reviewer(uid) if hasattr(st, "get_reviewer") else None
        st.set_reviewer(uid, (prof or {}).get("name") or uid, (prof or {}).get("char", "boksil"), tid)
        info = st.team_info(tid) if hasattr(st, "team_info") else None
        return {"ok": True, "team": info, "invite": (info or {}).get("invite_code")}
    else:
        return {"ok": False, "error": "알 수 없는 액션"}
    return {"ok": True}
