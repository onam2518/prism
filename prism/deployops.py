"""프롬프트 배포 도메인 (Atelier deployments 이식 · 스튜디오에서 관리).

프롬프트 스냅샷 버전을 slug 에 pin 해 외부 서비스가 Bearer 키로 당겨 쓰는
공개 API 를 제공한다: GET /api/v1/prompt?slug=… + Authorization: Bearer pr_live_….
· pin 교체 = 즉시 서빙 프롬프트 교체(호출측 코드 무변경 · Atelier 원칙)
· version 0 = 항상 최신 스냅샷(prompt_snapshot_latest)
· 키는 sha256 해시만 저장(평문 미보관 · 발급 시 1회 표시)

컴포지션: learnops 와 동일 — serve 가 기동 시 `_SV`(자기 모듈 객체)를 주입한다.
"""
from __future__ import annotations
import hashlib
import re
import secrets

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
KEY_PREFIX = "pr_live_"


def _hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def deployment_save(team=None, dep_id=None, slug="", name="", version=0, active=True,
                    created_by="") -> dict:
    """배포 생성/수정. slug 는 소문자·숫자·하이픈(전역 유일) · version 0=최신 스냅샷."""
    st = _SV.get_store()
    if not (st and hasattr(st, "deploy_save")):
        return {"ok": False, "error": "스토어가 배포를 지원하지 않습니다"}
    slug = (slug or "").strip().lower()
    if not _SLUG_RE.match(slug):
        return {"ok": False, "error": "slug 는 소문자·숫자·하이픈 2~63자여야 합니다"}
    try:
        version = max(0, int(version or 0))
    except (TypeError, ValueError):
        return {"ok": False, "error": "버전은 숫자여야 합니다(0=항상 최신)"}
    if version and not _snapshot(team, version):
        return {"ok": False, "error": f"프롬프트 스냅샷 v{version} 이 없습니다 · 학습 반영 이력을 확인하세요"}
    other = st.deploy_by_slug(slug)
    if other and (dep_id is None or int(other["id"]) != int(dep_id)):
        return {"ok": False, "error": f"이미 사용 중인 slug 입니다: {slug}"}
    rid = st.deploy_save(team, dep_id=dep_id, slug=slug, name=(name or "").strip()[:80],
                         version=version, active=bool(active), created_by=created_by or "")
    return {"ok": True, "id": rid}


def deployment_remove(dep_id, team=None) -> dict:
    st = _SV.get_store()
    ok = bool(st and hasattr(st, "deploy_remove") and st.deploy_remove(int(dep_id), team))
    return {"ok": ok} if ok else {"ok": False, "error": "배포를 찾을 수 없습니다"}


def deployments_list(team=None) -> dict:
    st = _SV.get_store()
    if not (st and hasattr(st, "deploys_list")):
        return {"ok": False, "error": "스토어가 배포를 지원하지 않습니다", "items": []}
    items = st.deploys_list(team)
    for d in items:
        d["keys"] = st.deploy_keys_for(d["id"], meta_only=True)
    return {"ok": True, "items": items}


def deployment_key_new(dep_id, team=None) -> dict:
    """API 키 발급 · 평문은 이 응답에서 1회만 노출(저장은 sha256)."""
    st = _SV.get_store()
    dep = st.deploy_get(int(dep_id), team) if (st and hasattr(st, "deploy_get")) else None
    if not dep:
        return {"ok": False, "error": "배포를 찾을 수 없습니다"}
    token = KEY_PREFIX + secrets.token_urlsafe(24)
    prefix = token[:len(KEY_PREFIX) + 6] + "…"
    kid = st.deploy_key_add(int(dep_id), _hash(token), prefix)
    return {"ok": True, "id": kid, "key": token, "prefix": prefix,
            "hint": "이 키는 다시 표시되지 않습니다 · 지금 복사해 보관하세요"}


def deployment_key_revoke(dep_id, key_id, team=None) -> dict:
    """키 폐기. 형제 동작(key_new·remove·save·list)과 같은 (id, team) 소유 확인을 선행한다 —
    이 경로만 team 을 받아 놓고 안 써서, 팀 관리자가 남의 팀 dep_id/key_id(순차 정수)로
    타 팀 배포 키를 끊을 수 있었다(deployment_keys 에는 team 컬럼이 없어 스토어 필터가 유일한 방벽)."""
    st = _SV.get_store()
    dep = st.deploy_get(int(dep_id), team) if (st and hasattr(st, "deploy_get")) else None
    if not dep:
        return {"ok": False, "error": "배포를 찾을 수 없습니다"}
    ok = bool(hasattr(st, "deploy_key_revoke") and st.deploy_key_revoke(int(key_id), int(dep_id)))
    return {"ok": ok} if ok else {"ok": False, "error": "키를 찾을 수 없습니다"}


def _snapshot(team, version: int):
    kind = f"prompt_snapshot_v{int(version)}" if version else "prompt_snapshot_latest"
    try:
        return _SV._report_get(kind, team, None)
    except Exception:
        return None


def _not_found():                # 슬러그·키 오류 공통 응답(호출마다 새 dict)
    return 404, {"error": "unknown deployment or invalid api key"}


def serve_prompt(slug: str, bearer: str, call: str = "") -> tuple[int, dict]:
    """공개 서빙: (HTTP 상태, 본문). 키는 배포별 sha256 대조 · revoked 제외.
    반환 본문은 pin 된 스냅샷의 콜별 시스템 프롬프트(외부 호출측 계약).
    슬러그 오류와 키 오류를 같은 404 로 답한다 — 예전처럼 '없는 슬러그 404 / 슬러그는 맞고
    키만 틀리면 401' 로 갈리면 무인증 호출자가 응답 코드만으로 슬러그를 열거할 수 있다."""
    st = _SV.get_store()
    if not (st and hasattr(st, "deploy_by_slug")):
        return 503, {"error": "deployment store unavailable"}
    dep = st.deploy_by_slug((slug or "").strip().lower())
    if not dep or not dep.get("active"):
        return _not_found()
    token = (bearer or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token.startswith(KEY_PREFIX):
        return _not_found()
    h = _hash(token)
    match = None
    for k in st.deploy_keys_for(dep["id"]):
        if not k.get("revoked") and k.get("hash") == h:
            match = k
            break
    if not match:
        return _not_found()
    snap = _snapshot(dep.get("team"), int(dep.get("version") or 0))
    if not snap:
        return 404, {"error": "no prompt snapshot pinned"}
    try:
        st.deploy_key_touch(match["id"])         # 마지막 사용 시각(감사) · 실패 무해
    except Exception:
        pass
    calls = snap.get("calls") or {}
    if call:
        c = calls.get(call)
        if not c:
            return 404, {"error": f"unknown call: {call}", "calls": sorted(calls.keys())}
        return 200, {"slug": dep["slug"], "version": snap.get("version"),
                     "call": call, "model": c.get("model"), "system": c.get("system")}
    return 200, {"slug": dep["slug"], "version": snap.get("version"),
                 "quality_version": snap.get("quality_version"),
                 "calls": calls}
