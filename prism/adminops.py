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
    """로그인/가입 프록시(서버만 키 보유). mode=login|signup. 키는 프론트에 노출 안 함."""
    s = _supa()
    if not s:
        return {"ok": False, "error": "supabase 모드가 아닙니다"}
    url, key = s
    email = (data.get("email") or "").strip()
    pw = data.get("password") or ""
    if not email or not pw:
        return {"ok": False, "error": "이메일·비밀번호를 입력하세요"}
    try:
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
        return {"ok": True, "access_token": at, "uid": (tok.get("user") or {}).get("id"), "email": email}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP{e.code}: {e.read().decode('utf-8', 'replace')[:160]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}

def validate_jwt(token: str):
    """user JWT → uid(검증). 60s 캐시. 실패 시 None."""
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
    except Exception:
        uid = None
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

def is_sys_admin_user(uid, team, email="") -> bool:
    """운영(시스템) 관리자 = 허용목록(~/.prism_admin_emails) 이메일. 팀 소속과 무관.
    허용목록 미설정 시 팀 관리자 로직으로 폴백(단독 운영 호환)."""
    allow = admin_emails()
    if allow:
        return bool(email and email.strip().lower() in allow)
    return _team_admin(uid, team)

def is_admin_user(uid, team, email="") -> bool:
    """관리자(팀 관리 접근) = 운영 관리자 OR 팀 관리자(생성자·위임).
    권한 2단계: 팀 관리자는 '팀 관리'만 추가, 나머지 관리자 메뉴는 운영 관리자 전용."""
    return is_sys_admin_user(uid, team, email) or _team_admin(uid, team)

def admin_data(uid, team, email="") -> dict:
    """팀 관리: 팀 정보·멤버·관리자 여부. supabase 전용.
    팀 미소속이어도 운영 관리자(허용목록)는 isSysAdmin/isAdmin 을 내려 관리자 메뉴가 열리게 한다."""
    st = _SV.get_store()
    if not (st and team and hasattr(st, "team_members")):
        sysadm = is_sys_admin_user(uid, team, email)
        return {"ok": False, "isAdmin": sysadm, "isSysAdmin": sysadm, "team": None, "members": []}
    gc = st.golden_count(team) if hasattr(st, "golden_count") else 0
    return {"ok": True, "isAdmin": is_admin_user(uid, team, email),
            "isSysAdmin": is_sys_admin_user(uid, team, email),
            "team": st.team_info(team), "members": st.team_members(team), "goldenCount": gc}

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

def admin_action(uid, team, data, email="") -> dict:
    """관리자 액션(데이터 삭제·멤버 제거). 팀 생성자만."""
    st = _SV.get_store()
    if _supa() and not is_admin_user(uid, team, email):    # 로컬 단독 실행 = 관리자 취급(타 라우트와 동일)
        return {"ok": False, "error": "관리자 전용입니다"}
    act = data.get("action")
    data_ops = ("clear_feedback", "clear_contents", "clear_golden", "delete_team")
    if act in data_ops and _supa() and not is_sys_admin_user(uid, team, email):
        return {"ok": False, "error": "데이터 관리는 운영 관리자 전용입니다"}
    if act == "clear_feedback":
        st.clear_team_feedback(team)
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
    elif act in ("set_admin", "unset_admin") and data.get("member"):
        t = st.team_info(team) if hasattr(st, "team_info") else None
        if t and data["member"] == t.get("created_by"):
            return {"ok": False, "error": "생성자의 관리자 권한은 변경할 수 없습니다"}
        if not hasattr(st, "set_member_admin"):
            return {"ok": False, "error": "이 백엔드는 위임을 지원하지 않습니다"}
        st.set_member_admin(team, data["member"], act == "set_admin")
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
