"""게시판 도메인 (serve 에서 분리 · 라우트 분리 4차 · 로드맵 2단계 3차).

기능개선 제안·오류 제보(팀 스코프) 목록/등록/상태 변경/답변. HTTP 디스패치는 serve 유지.
컴포지션: 스토어·SSE 방송은 serve 가 `_SV` 로 주입(learnops 관례).
"""
from __future__ import annotations

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입


def board_data(team=None, uid: str = "") -> dict:
    """게시판(기능개선·오류 제보) 목록 · 팀 스코프.
    작성자는 uid 로 저장하고 표시명은 조회 시점에 해석 → 닉네임 변경이 자동 반영된다."""
    st = _SV.get_store()
    if not (st and hasattr(st, "board_list")):
        return {"items": [], "n": 0}
    items = st.board_list(team=team)
    if _SV._supa():
        names = st.reviewers_map(None) if hasattr(st, "reviewers_map") else {}
        for it in items:
            meta = names.get(it["author_id"]) or {}
            it["author"] = meta.get("name") or (it["author_id"][:8] or "(탈퇴)")
            it["mine"] = bool(uid and it["author_id"] == uid)
    else:                                            # sqlite: 키=이름 · 단일 사용자 = 전부 내 글
        for it in items:
            it["author"] = it["author_id"]
            it["mine"] = True
    return {"items": items, "n": len(items)}


def board_action(data: dict, team=None, uid: str = "", email: str = "") -> dict:
    """게시판 동작: 등록=팀원 · 상태 변경=관리자 · 삭제=작성자 또는 관리자."""
    st = _SV.get_store()
    if not (st and hasattr(st, "board_add")):
        return {"ok": False, "error": "게시판을 지원하지 않는 저장소입니다"}
    act = (data.get("action") or "create").strip()
    rv = (data.get("reviewer") or "").strip()        # sqlite=이름 · supabase=_inject_reviewer 가 uid 주입
    if act == "create":
        title = (data.get("title") or "").strip()[:80]
        if not title:
            return {"ok": False, "error": "제목을 입력하세요"}
        st.board_add("feature" if data.get("kind") == "feature" else "bug",
                     title, (data.get("body") or "").strip()[:2000], rv, team=team)
    else:
        it = st.board_get(int(data.get("id") or 0), team=team)
        if not it:
            return {"ok": False, "error": "항목을 찾을 수 없습니다"}
        admin = (not _SV._supa()) or _SV.is_admin_user(uid, team, email)
        if act == "status":
            if not admin:
                return {"ok": False, "error": "상태 변경은 관리자 전용입니다"}
            if data.get("status") not in ("open", "doing", "done"):
                return {"ok": False, "error": "상태 값이 올바르지 않습니다"}
            st.board_set_status(it["id"], data["status"], team=team)
        elif act == "answer":                          # 문의 답변(관리자 전용)
            if not admin:
                return {"ok": False, "error": "답변은 관리자 전용입니다"}
            if not hasattr(st, "board_answer"):
                return {"ok": False, "error": "이 백엔드는 답변을 지원하지 않습니다"}
            st.board_answer(it["id"], (data.get("answer") or "").strip()[:2000], team=team)
        elif act == "delete":
            if not (admin or (it["author_id"] and it["author_id"] == (uid or rv))):
                return {"ok": False, "error": "작성자 또는 관리자만 삭제할 수 있습니다"}
            st.board_delete(it["id"], team=team)
        else:
            return {"ok": False, "error": "알 수 없는 동작입니다"}
    out = board_data(team, uid or rv)
    out["ok"] = True
    return out
