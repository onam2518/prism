"""파일 기반 메모리(실험실 · 사용자 메타): 마크다운 파일 트리를 팀 스코프로 저장.

설계 실험을 비개발자가 화면에서 직접 구동하는 백엔드 —
- 소비 시연: 추출 콘텐츠를 훑기·정독·저장으로 소비하면 그 턴에 /topics/<주제>.md 로 자동 기록
- 수동 조작: 읽기 · 전체 쓰기(버전 토큰 검사) · 끝에 추가 · 삭제(명시 요청이 있을 때만)
- 새 대화 주입: 파일 목록 → 주입 블록 미리보기 생성
저장은 store 의 report KV(usermeta_memory) 를 재사용한다 · 실제 파일시스템을 쓰지 않아
배포·테스트 격리가 안전하고, 파일당 크기·파일 수 제한을 서버가 강제한다.

컴포지션: 스토어·결과 행은 serve 가 `_SV` 로 주입(umops 관례).
"""
from __future__ import annotations

import datetime as _dt
import re

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입

KIND = "usermeta_memory"
MAX_FILES = 64                  # 계정당 파일 수 제한
MAX_BYTES = 4000                # 파일당 크기 제한(설계 명세의 '파일당 크기 제한 존재')
ROOT_FILES = ("profile.md", "preferences.md")
DIRS = ("topics", "areas", "people")
_NAME = re.compile(r"^[0-9A-Za-z가-힣][0-9A-Za-z가-힣_-]{0,38}\.md$")
_ORDER = {"profile.md": 0, "preferences.md": 1, "topics": 2, "areas": 3, "people": 4}

# 소비 액션 → (기록 문구, 체류초, 스크롤%) · 시연이므로 결정적 값
ACTIONS = {"skim": ("훑고 지나감", 3, 20), "read": ("끝까지 읽음", 45, 95),
           "save": ("저장함", 60, 100)}


def _now() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _files(team) -> dict:
    return dict((_SV._report_get(KIND, team, {}) or {}).get("files") or {})


def _persist(team, files):
    st = _SV.get_store()
    if st and hasattr(st, "save_report"):
        st.save_report(KIND, {"files": files}, team=team)


def valid_path(path):
    """허용 경로만 통과: profile.md · preferences.md · topics|areas|people/<이름>.md"""
    p = (path or "").strip().lstrip("/")
    if p in ROOT_FILES:
        return p
    parts = p.split("/")
    if len(parts) == 2 and parts[0] in DIRS and _NAME.match(parts[1]):
        return p
    return None


def _desc(content: str) -> str:
    """상단 메타데이터 블록의 '설명:' 줄 → 목록·주입 미리보기에 사용."""
    for ln in (content or "").splitlines()[:8]:
        if ln.startswith("설명:"):
            return ln[3:].strip()
    return ""


def _order_key(p: str):
    return (_ORDER.get(p.split("/")[0], 9), p)


def _slug(cat: str) -> str:
    s = re.sub(r"[^0-9A-Za-z가-힣]+", "-", (cat or "").strip()).strip("-").lower()
    return s or "misc"


def injection_text(files: dict) -> str:
    """새 대화 시작 시 자동 주입되는 블록(목록만 · 본문은 필요한 파일만 읽음)."""
    if not files:
        return ("[새 대화 시작 · 메모리 주입]\n현재 이 계정의 메모리는 비어 있습니다. "
                "대화·소비에서 지속 가치가 있는 정보가 나오면 파일이 생깁니다.")
    out = ["[새 대화 시작 · 메모리 주입]",
           "아래는 이 계정의 메모리 파일 목록입니다. 필요한 파일만 읽어 활용합니다.", ""]
    for p in sorted(files, key=_order_key):
        d = _desc(files[p].get("content", ""))
        out.append("- /" + p + (" — " + d if d else ""))
    return "\n".join(out)


def demo_contents(team=None, limit: int = 30) -> list:
    """소비 시연용 카탈로그: 추출 결과 중 유통 가능(G) 콘텐츠 · idx 는 추출 순서."""
    from .usermeta import _t1
    out = []
    for i, r in enumerate(_SV.results_rows(team=team)):
        if (r.get("quality_meta") or {}).get("finalGrade", "G") == "R":
            continue
        im = r.get("item_meta") or {}
        cats = im.get("content_category") or []
        out.append({"idx": i,
                    "title": ((r.get("content_ref") or {}).get("title") or "")[:60],
                    "summary": (im.get("summary") or "").strip()[:90],
                    "cat": _t1(cats[0]) if cats else "기타",
                    "intent": (im.get("intent") or ["기타"])[0]})
        if len(out) >= limit:
            break
    return out


def memory_data(team=None) -> dict:
    files = _files(team)
    items = []
    for p in sorted(files, key=_order_key):
        f = files[p]
        body = f.get("content", "")
        items.append({"path": p, "desc": _desc(body), "ver": f.get("ver", 1),
                      "bytes": len(body.encode("utf-8")), "content": body,
                      "updated": f.get("updated", "")})
    return {"files": items, "injection": injection_text(files),
            "contents": demo_contents(team),
            "limits": {"max_files": MAX_FILES, "max_bytes": MAX_BYTES}}


def _size_ok(content: str):
    return len((content or "").encode("utf-8")) <= MAX_BYTES


def _put(files, path, content, updated=None):
    prev = files.get(path) or {}
    files[path] = {"content": content, "ver": int(prev.get("ver", 0)) + 1,
                   "updated": updated or _now()}


def _topic_header(path: str, cat: str, n: int) -> str:
    line = "설명: " + cat + " 소비 기록 · " + str(n) + "회"
    if n >= 3:
        line += " · 반복 소비 주제"
    return ("---\n파일: /" + path + "\n" + line +
            "\n출처: 소비 시연(실험실)\n별칭: " + cat + "\n---\n")


def _bump_desc(content: str, cat: str, n: int) -> str:
    """토픽 파일 상단 '설명:' 줄을 누적 횟수로 재생성(관찰이 쌓이면 설명이 자란다)."""
    lines = (content or "").splitlines()
    line = "설명: " + cat + " 소비 기록 · " + str(n) + "회"
    if n >= 3:
        line += " · 반복 소비 주제"
    for i, ln in enumerate(lines[:8]):
        if ln.startswith("설명:"):
            lines[i] = line
            break
    return "\n".join(lines) + ("\n" if content.endswith("\n") else "")


def _consume(files, body, team):
    """소비 이벤트 → 해당 주제 파일에 [observed] 한 줄 자동 기록. (wrote, err) 반환."""
    try:
        idx = int(body.get("idx"))
    except (TypeError, ValueError):
        return None, "콘텐츠 번호가 올바르지 않습니다"
    rows = _SV.results_rows(team=team)
    if idx < 0 or idx >= len(rows):
        return None, "콘텐츠를 찾을 수 없습니다 · 새로고침 후 다시 시도하세요"
    from .usermeta import _t1
    r = rows[idx]
    im = r.get("item_meta") or {}
    title = ((r.get("content_ref") or {}).get("title") or "")[:60] or "(제목 없음)"
    cats = im.get("content_category") or []
    cat = _t1(cats[0]) if cats else "기타"
    intent = (im.get("intent") or ["기타"])[0]
    label, dwell, _scroll = ACTIONS.get(body.get("action") or "read", ACTIONS["read"])
    path = "topics/" + _slug(cat) + ".md"
    if path not in files and len(files) >= MAX_FILES:
        return None, "파일 수 제한(" + str(MAX_FILES) + "개)에 도달했습니다"
    prev = (files.get(path) or {}).get("content") or _topic_header(path, cat, 0)
    n = prev.count("- [observed]") + 1
    line = ('- [observed] ' + _now() + ' · "' + title + '" ' + label +
            " · 체류 " + str(dwell) + "초 (" + intent + ")")
    content = _bump_desc(prev, cat, n)
    if not content.endswith("\n"):
        content += "\n"
    content += line + "\n"
    if not _size_ok(content):
        return None, ("파일당 크기 제한(" + str(MAX_BYTES) + "바이트)에 도달했습니다 · "
                      "설계 명세의 한계 그대로입니다. 파일을 열어 오래된 줄을 지워 주세요")
    _put(files, path, content)
    return {"path": path, "line": line}, None


def memory_ops(body: dict, team=None) -> dict:
    """조작 5종 중 쓰기 계열(consume·write·append·delete) · 읽기는 GET.
    성공 시 최신 memory_data + wrote(방금 기록분) 반환 · 실패는 {"error": ...}."""
    body = body or {}
    op = body.get("op") or ""
    files = _files(team)
    wrote = None

    if op == "consume":
        wrote, err = _consume(files, body, team)
        if err:
            return {"error": err}

    elif op == "write":                              # 전체 쓰기 · 신규 생성 겸용
        path = valid_path(body.get("path"))
        if not path:
            return {"error": "경로가 규칙에 맞지 않습니다 · profile.md · preferences.md · "
                             "topics|areas|people/이름.md 만 가능합니다(한글·영문·숫자·-_)"}
        content = body.get("content") or ""
        if not _size_ok(content):
            return {"error": "파일당 크기 제한(" + str(MAX_BYTES) + "바이트)을 넘었습니다"}
        if path in files:                            # 수정: 버전 토큰이 맞아야 덮어쓴다
            if int(body.get("ver") or 0) != int(files[path].get("ver", 0)):
                return {"error": "버전 충돌 · 그 사이 파일이 바뀌었습니다. 파일을 다시 열어 확인 후 저장하세요"}
        elif len(files) >= MAX_FILES:
            return {"error": "파일 수 제한(" + str(MAX_FILES) + "개)에 도달했습니다"}
        _put(files, path, content)
        wrote = {"path": path, "line": "(전체 쓰기)"}

    elif op == "append":                             # 끝에 추가 · 한 줄
        path = valid_path(body.get("path"))
        if not path or path not in files:
            return {"error": "추가할 파일이 없습니다 · 먼저 파일을 만들어 주세요"}
        line = (body.get("line") or "").strip()
        if not line:
            return {"error": "추가할 내용을 입력하세요"}
        content = files[path]["content"]
        if content and not content.endswith("\n"):
            content += "\n"
        content += line + "\n"
        if not _size_ok(content):
            return {"error": "파일당 크기 제한(" + str(MAX_BYTES) + "바이트)에 도달했습니다"}
        _put(files, path, content)
        wrote = {"path": path, "line": line}

    elif op == "delete":                             # 삭제는 명시적 요청이 있을 때만(UI 확인 후)
        path = valid_path(body.get("path"))
        if not path or path not in files:
            return {"error": "삭제할 파일이 없습니다"}
        del files[path]
        wrote = {"path": path, "line": "(삭제)"}

    else:
        return {"error": "지원하지 않는 조작입니다: " + str(op)[:20]}

    _persist(team, files)
    out = memory_data(team)
    out["wrote"] = wrote
    return out
