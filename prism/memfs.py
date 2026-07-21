"""파일 기반 메모리(실험실 · 사용자 메타): 마크다운 파일 트리를 팀 스코프로 저장.

설계 실험을 비개발자가 화면에서 직접 구동하는 백엔드.
- 시연 과정(STEP 1~4): 실서비스형 피드 소비 → 실시간 측정 → 결론(페르소나 판정) → 활용
- 생성 과정: 소비가 그 턴에 /topics/<주제>.md 로 자동 기록 + 수동 조작(전체 쓰기·추가·삭제) + 주입
저장(report KV · 팀 스코프 · 실제 파일시스템 미사용):
- usermeta_memory = {"files": {경로: {content, ver, updated}}}
- usermeta_demo   = {"events": [{idx, event, dwell, scroll, t, title, cat, intent, path, emo?, text?}],
                     "last_logic": str}  · 결론·측정은 저장하지 않고 매 요청 실계산(실시간)
파일당 크기·파일 수 제한은 서버가 강제한다.

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
        out.append("- /" + p + (" · " + d if d else ""))
    return "\n".join(out)


def _catalog(team=None, limit: int = 30) -> list:
    """소비 시연용 카탈로그: 추출 결과 중 유통 가능(G) 콘텐츠 · idx 는 추출 순서.
    사용자 메타 파이프라인 입력(viewed)과 같은 필드를 유지해 측정 로직을 재사용한다."""
    from .usermeta import _t1
    out = []
    for i, r in enumerate(_SV.results_rows(team=team)):
        if (r.get("quality_meta") or {}).get("finalGrade", "G") == "R":
            continue
        im = r.get("item_meta") or {}
        cats = im.get("content_category") or []
        out.append({"idx": i,
                    "title": ((r.get("content_ref") or {}).get("title") or "")[:60],
                    "service": (r.get("content_ref") or {}).get("displayServiceName", ""),
                    "summary": (im.get("summary") or "").strip()[:160],
                    "intent_categories": im.get("intent") or [],
                    "entity_categories": [_t1(c) for c in cats],
                    "entities": im.get("entities") or [],
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


def _observe(files, title, cat, intent, label, dwell=None, tag="observed"):
    """관찰([observed]) 또는 발화([stated]) 1건 → /topics/<주제>.md 에 한 줄 기록(공용)."""
    path = "topics/" + _slug(cat) + ".md"
    if path not in files and len(files) >= MAX_FILES:
        return None, "파일 수 제한(" + str(MAX_FILES) + "개)에 도달했습니다"
    prev = (files.get(path) or {}).get("content") or _topic_header(path, cat, 0)
    n = prev.count("- [observed]") + (1 if tag == "observed" else 0)
    line = ('- [' + tag + '] ' + _now() + ' · "' + title + '" ' + label +
            (" · 체류 " + str(dwell) + "초" if dwell is not None else "") + " (" + intent + ")")
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
    """조작 5종 중 쓰기 계열(write·append·delete) · 읽기는 GET · 소비 기록은 시연(demo_ops)이 담당.
    성공 시 최신 memory_data + wrote(방금 기록분) 반환 · 실패는 {"error": ...}."""
    body = body or {}
    op = body.get("op") or ""
    files = _files(team)
    wrote = None

    if op == "write":                                # 전체 쓰기 · 신규 생성 겸용
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


# ═══ 소비 시연 세션(STEP 1 소비·수집 → 2 측정·로직 → 3 결론 → 4 활용) ═══
# 시안 B(단계 진행) 뼈대 + STEP 1 우측 A(실시간 3단) + STEP 3 D(인과 카드) 절충 · 채택안.
# 좌측 피드에서 발생한 실제 행동(노출·클릭·체류)을 이벤트로 쌓고,
# 측정·판정은 usermeta 의 실로직(_profile_from_logs · _nearest_persona)을 그대로 재사용한다.

DEMO_KIND = "usermeta_demo"
DEMO_EVENTS_MAX = 400
EV_LABEL = {"impression": "노출", "click": "클릭", "skim": "훑고 나감",
            "read": "끝까지 읽음", "react": "반응", "comment": "댓글", "search": "검색"}
EMOTIONS = ("추천해요", "좋아요", "감동이에요", "화나요", "슬퍼요")   # 기사 하단 감정 반응 5종
EMO_POS = ("추천해요", "좋아요", "감동이에요")   # 긍정 · 나머지(화나요·슬퍼요)는 부정 분포로 집계
REACT_W = 1.0                    # 호응 가중: 반응 1건당 선호 합산치
COMMENT_W = 1.5                  # 호응 가중: 댓글 1건당(더 강한 참여 신호)
# 사용자 친화 표기: 추천 칩·검색 매칭 전용 · 내부 값(IAB 카테고리·인텐트)은 화면에 노출하지 않는다
CAT_KO = {"News and Politics": "뉴스·시사", "Sports": "스포츠", "Entertainment": "연예",
          "Business and Finance": "경제·재테크", "Technology and Computing": "테크·IT",
          "Science": "과학", "Food & Drink": "푸드", "Travel": "여행",
          "Style & Fashion": "패션·뷰티", "Healthy Living": "건강", "Video Gaming": "게임",
          "Music and Audio": "음악", "Movies": "영화", "Television": "TV·방송",
          "Personal Finance": "재테크", "Automotive": "자동차"}
INT_KO = {"기획·심층": "깊이 있는 분석만", "분석·해설": "차분한 해설 기사만",
          "흥미·화제": "가볍게 볼 화제만", "속보": "지금 뜨는 속보만",
          "속보·사건 추적": "지금 뜨는 속보만", "사건 경과 보도": "사건 흐름 따라잡기",
          "인물 동정": "인물 소식만", "유머": "웃음 충전 콘텐츠",
          "라이프스타일": "일상 꿀팁 모음", "의견·논평": "여러 시각의 논평"}
# TIARA(전사 통합 행동로그) 체계 매핑 · pplan/286294107 스펙 시트 기준.
# 노출=ViewableImpression(실제 보인 콘텐츠만) · 클릭=Event(ClickContent · 읽기 화면은 Pageview
# ViewContent 병행) · 읽기 종료=Usage(UsagePage · 체류·스크롤) · 저장=Event(표준 Kind 없음 →
# 액션명 구분 권고). Usage 체류 최대 600초(10분) 초과분은 스펙대로 최대값으로 잘라 저장.
TIARA_TAG = {"impression": "ViewImp", "click": "Event", "read": "Usage",
             "skim": "Usage", "react": "Event", "comment": "Event", "search": "Event"}
USAGE_MAX_SEC = 600


def _now_t() -> str:
    return _dt.datetime.now().strftime("%H:%M:%S")


def _demo_session(team) -> dict:
    sess = dict(_SV._report_get(DEMO_KIND, team, {}) or {})
    sess.pop("finished", None)                       # 구 버전(단계형 결론) 잔재 키 정리
    sess.pop("conclusion", None)
    return sess


def _demo_save(team, sess):
    st = _SV.get_store()
    if st and hasattr(st, "save_report"):
        st.save_report(DEMO_KIND, sess, team=team)


def _viewed_logs(events, catalog):
    """이벤트 → 파이프라인 입력 접기: 콘텐츠당 최종 상태 1행(클릭 여부 · 최대 체류·스크롤)."""
    byidx = {c["idx"]: c for c in catalog}
    acc = {}
    for e in events:
        if e.get("event") == "impression" or e.get("idx") not in byidx:
            continue
        a = acc.setdefault(e["idx"], {"clicked": False, "dwell": 0, "scroll": 0, "consumed": False})
        if e["event"] == "click":
            a["clicked"] = True
        if e["event"] in ("read", "skim"):
            a["consumed"] = True
        a["dwell"] = max(a["dwell"], int(e.get("dwell") or 0))
        a["scroll"] = max(a["scroll"], int(e.get("scroll") or 0))
    acc = {i: a for i, a in acc.items() if a["consumed"]}
    viewed = [byidx[i] for i in acc]
    logs = [{"content_idx": i, "event": "click" if a["clicked"] else "impression",
             "dwell_sec": a["dwell"], "scroll_pct": a["scroll"]} for i, a in acc.items()]
    return viewed, logs


def _live_measures(events, catalog) -> dict:
    """실시간 측정: usermeta 실로직(체류·클릭) + 호응(반응·댓글) 가중을 합산해 즉시 재계산.
    호응은 콘텐츠·주제에 대한 참여 신호라 선호·강도에 반영되고, 강도는 판정에도 들어간다."""
    from collections import Counter
    from . import usermeta as UM
    viewed, logs = _viewed_logs(events, catalog)
    w_ent, w_int = Counter(), Counter()
    form, ents, eng = {}, [], {"views": 0, "clicks": 0, "click_rate": 0, "avg_dwell_sec": 0}
    breadth = 0
    if viewed:
        form, _intensity, prof = UM._profile_from_logs(viewed, logs)
        form["시간대"] = "시연 세션"
        w_ent.update(dict(prof["ent"]))
        w_int.update(dict(prof["int"]))
        ents, eng = prof["ents"], prof["eng"]
        breadth = round(UM._breadth(viewed), 2)
    resp = {"reacts": 0, "comments": 0, "pos": 0, "neg": 0, "emos": {}, "boost": 0.0}
    for e in events:
        ev = e.get("event")
        if ev not in ("react", "comment"):
            continue
        w = COMMENT_W if ev == "comment" else REACT_W
        if e.get("cat"):
            w_ent[e["cat"]] += w
        if e.get("intent"):
            w_int[e["intent"]] += w
        resp["boost"] = round(resp["boost"] + w, 1)
        if ev == "react":
            resp["reacts"] += 1
            emo = e.get("emo") or ""
            resp["emos"][emo] = resp["emos"].get(emo, 0) + 1
            resp["pos" if emo in EMO_POS else "neg"] += 1
        else:
            resp["comments"] += 1
    top_ent = sorted(w_ent.items(), key=lambda x: -x[1])[:5]
    top_int = sorted(w_int.items(), key=lambda x: -x[1])[:6]
    mx = max([v for _, v in top_int], default=1)
    intensity = {k: ("고" if v >= mx * .66 else "중" if v >= mx * .33 else "저") for k, v in top_int}
    total = sum(w for _, w in top_ent) or 1.0
    cats = [{"name": k, "w": round(w, 1), "pct": round(w / total * 100)} for k, w in top_ent]
    return {"form": form, "intensity": intensity, "cats": cats,
            "ints": top_int, "ents": ents, "eng": eng, "breadth": breadth, "resp": resp}


def _ev_line(e) -> str:
    """콘솔형 로그 한 줄 · TIARA ActionType/ActionKind 를 코드처럼 그대로 노출."""
    t = e.get("t", "")
    ev = e.get("event")
    idx = e.get("idx")
    title = (e.get("title") or "")[:22]
    if ev == "impression":
        return t + ' [ViewImp] content_id=' + str(idx) + ' "' + title + '"'
    if ev == "click":
        return t + ' [Event] ClickContent content_id=' + str(idx) + ' "' + title + '"'
    if ev in ("read", "skim"):
        return (t + " [Usage] UsagePage content_id=" + str(idx) + " dwell=" + str(e.get("dwell") or 0)
                + "s scroll=" + str(e.get("scroll") or 0) + '% "' + title + '"')
    if ev == "react":
        return t + " [Event] Like emotion='" + (e.get("emo") or "") + "' content_id=" + str(idx)
    if ev == "comment":
        return t + ' [Event] WriteComment content_id=' + str(idx) + ' text="' + (e.get("text") or "") + '"'
    if ev == "search":
        return t + ' [Event] Search query="' + title + '"'
    return t + " [" + TIARA_TAG.get(ev, "-") + "] " + str(ev)


def _suggests(catalog) -> list:
    """프롬프트 폼의 추천 유도 문구 · 실제 피드의 주제·맥락에서 사용자 언어로만 생성.
    매핑에 없는 내부 값은 노출하지 않는다(사용자 친화 표기 원칙)."""
    out, seen = [], set()
    for c in catalog:
        ko = CAT_KO.get(c["cat"])
        if ko and ko not in seen:
            seen.add(ko)
            out.append(ko + " 몰아보기")
        if len(out) >= 2:
            break
    for c in catalog:
        ko = INT_KO.get(c.get("intent") or "")
        if ko and ko not in out:
            out.append(ko)
            break
    return out[:3] or ["오늘의 인기 콘텐츠 보기"]


def demo_data(team=None) -> dict:
    catalog = _catalog(team)
    sess = _demo_session(team)
    events = sess.get("events") or []
    imp = len({e.get("idx") for e in events if e.get("event") == "impression"})
    consumed = len({e.get("idx") for e in events if e.get("event") in ("read", "skim")})
    out = {"contents": [dict({k: c[k] for k in ("idx", "title", "summary", "service", "cat", "intent")},
                             cat_ko=CAT_KO.get(c["cat"], ""), intent_ko=INT_KO.get(c["intent"], ""))
                        for c in catalog],
           "session": {"events_n": len(events), "impressions": imp, "consumed": consumed},
           "stream": [_ev_line(e) for e in reversed(events[-30:])],
           "live": _live_measures(events, catalog),
           "logic": sess.get("last_logic") or "",
           "personas": _personas_brief(),
           "suggests": _suggests(catalog),
           "formula": "가중치 = 체류초 ÷ 30 × 클릭가중(클릭 2.0 · 비클릭 1.0) + 호응(반응 1.0 · 댓글 1.5) → 카테고리·맥락별 합산 → 상대 등급(저/중/고)"}
    if consumed:                                     # 결론도 실시간 · 소비가 쌓일 때마다 자동 재판정
        out["conclusion"] = _conclusion(events, catalog, team)
    return out


def _personas_brief() -> list:
    """판정 기준(기본 페르소나 8종) 요약 · 구 '페르소나 정의' 표를 STEP 3 정책 참고로 흡수."""
    from . import usermeta as UM
    return [{"name": p["name"], "full": p["full"], "desc": p["desc"]} for p in UM.PERSONAS]


def _conclusion(events, catalog, team=None) -> dict:
    """사용 종료 → 결론: 실측 요약 + 페르소나 판정(실로직) + 인과 카드 + 메모리 반영."""
    from . import usermeta as UM
    viewed, _logs = _viewed_logs(events, catalog)
    live = _live_measures(events, catalog)
    if not viewed:
        return {"empty": True, "note": "소비된 콘텐츠가 없습니다 · STEP 1 에서 콘텐츠를 읽어 주세요"}
    hit = UM._nearest_persona(live["form"], live["intensity"], viewed,
                              tf={}, ents_top=live.get("ents"), profile=None)
    pdef = next((p for p in UM.PERSONAS if p["name"] == hit["name"]), {})
    chain = []
    for e in events:
        if e.get("event") in ("read", "skim"):
            chain.append({"t": e.get("t", ""), "act": EV_LABEL[e["event"]] + ' · "' + (e.get("title") or "") + '"',
                          "measure": (e.get("cat") or "") + " 가중 +" + str(round((e.get("dwell") or 0) / 30.0, 1))
                                     + " · 체류 " + str(e.get("dwell") or 0) + "초 (" + (e.get("intent") or "") + ")",
                          "file": ("/" + e["path"]) if e.get("path") else "측정만"})
        elif e.get("event") == "click":
            chain.append({"t": e.get("t", ""), "act": '클릭 · "' + (e.get("title") or "") + '"',
                          "measure": "이 콘텐츠 이후 가중 ×2.0", "file": "측정만"})
        elif e.get("event") == "react":
            chain.append({"t": e.get("t", ""), "act": "반응 '" + (e.get("emo") or "") + "' · \"" + (e.get("title") or "") + '"',
                          "measure": "호응 가중 +" + str(REACT_W) + " · 감정은 Custom Properties",
                          "file": ("/" + e["path"]) if e.get("path") else "측정만"})
        elif e.get("event") == "search":
            chain.append({"t": e.get("t", ""), "act": "검색 · '" + (e.get("title") or "") + "'",
                          "measure": "원하는 콘텐츠 직접 선언 · 검색어 부가 정보",
                          "file": ("/" + e["path"]) if e.get("path") else "측정만"})
        elif e.get("event") == "comment":
            chain.append({"t": e.get("t", ""), "act": '댓글 · "' + (e.get("text") or "") + '"',
                          "measure": "호응 가중 +" + str(COMMENT_W) + " · 직접 발화 → [stated] 기록",
                          "file": ("/" + e["path"]) if e.get("path") else "측정만"})
    top_cat = live["cats"][0]["name"] if live["cats"] else "기타"
    top_int = live["ints"][0][0] if live.get("ints") else "기타"
    depth = live["form"].get("깊이", "·")
    scenarios = [
        {"title": "소비 형태 기반 홈 재배치",
         "desc": depth + " 소비형: " + ("심층·이어보기 슬롯을 위로 올립니다" if depth == "몰입"
                                        else "숏폼·이슈 카드를 위로 올립니다")},
        {"title": "능동형 컴포넌트",
         "desc": top_cat + " × " + top_int + " 조건의 큐레이션 슬롯을 능동 삽입합니다"},
        {"title": "광고 타겟팅",
         "desc": top_cat + " 관심 × " + top_int + " 선호 교차로 정밀 매칭합니다"}]
    resp = live.get("resp") or {}
    resp_line = ("반응 " + str(resp.get("reacts", 0)) + "건(긍정 " + str(resp.get("pos", 0)) + " · 부정 "
                 + str(resp.get("neg", 0)) + ") · 댓글 " + str(resp.get("comments", 0)) + "건 · 선호 가중 +"
                 + str(resp.get("boost", 0)))
    return {"persona": {"name": hit["name"], "full": pdef.get("full", hit["name"]),
                        "desc": pdef.get("desc", ""), "conf": hit["conf"], "rule": hit["rule"],
                        "second": hit.get("second"), "provisional": bool(hit.get("provisional"))},
            "basis": [["소비 콘텐츠", str(len(viewed)) + "건 · 카테고리 다양성 " + str(live["breadth"])],
                      ["호응", resp_line],
                      ["소비 형태", " · ".join(k + " " + v for k, v in live["form"].items())],
                      ["소비 강도", " · ".join(k + " " + v for k, v in list(live["intensity"].items())[:4]) or "·"],
                      ["판정", hit["name"] + " · " + hit["rule"] + " · 신뢰도 " + hit["conf"]
                       + ((" · 2순위 " + hit["second"]) if hit.get("second") else "")]],
            "chain": chain,
            "memory": {"files": sorted({"/" + e["path"] for e in events if e.get("path")}),
                       "injection": injection_text(_files(team))},
            "scenarios": scenarios,
            "note": ("소비 5건 미만이라 잠정 판정입니다 · STEP 1 에서 더 소비하면 판별이 정교해집니다"
                     if len(viewed) < 5 else "")}


def demo_ops(body: dict, team=None) -> dict:
    """소비 시연 조작: event(노출·검색·클릭·정독·훑기·반응·댓글) · reset(처음부터) · 결론은 실시간."""
    body = body or {}
    op = body.get("op") or ""
    sess = _demo_session(team)
    events = list(sess.get("events") or [])
    catalog = _catalog(team)
    byidx = {c["idx"]: c for c in catalog}
    wrote = None

    if op == "event":
        ev = body.get("event") or ""
        if ev == "impression":                       # 피드 진입 → 보인 콘텐츠 일괄 노출(중복 무시)
            idxs = body.get("idxs") or ([body.get("idx")] if body.get("idx") is not None else [])
            seen = {e.get("idx") for e in events if e.get("event") == "impression"}
            for i in idxs:
                try:
                    i = int(i)
                except (TypeError, ValueError):
                    continue
                if i in byidx and i not in seen:
                    seen.add(i)
                    events.append({"idx": i, "event": "impression", "dwell": 0, "scroll": 0,
                                   "t": _now_t(), "title": byidx[i]["title"][:40]})
            sess["last_logic"] = "노출 " + str(len(seen)) + "건 등록 · 노출은 클릭률·소비율의 분모로만 쓰입니다"
        elif ev == "search":
            q = (body.get("query") or "").strip()[:60]
            if not q:
                return {"error": "찾고 싶은 콘텐츠를 입력하세요"}
            toks = [t for t in re.split(r"\s+", q) if len(t) >= 2]
            hit = next((c for c in catalog for t in toks
                        if t in c["title"] or t in c["cat"] or t in c["intent"]
                        or t in CAT_KO.get(c["cat"], "") or t in INT_KO.get(c["intent"], "")), None)
            files = _files(team)
            wrote, err = _observe(files, q, hit["cat"] if hit else "기타",
                                  hit["intent"] if hit else "검색", "검색 · 원하는 콘텐츠 선언", tag="stated")
            if err:
                return {"error": err}
            _persist(team, files)
            events.append({"idx": -1, "event": "search", "dwell": 0, "scroll": 0,
                           "t": _now_t(), "title": q, "path": wrote["path"]})
            sess["last_logic"] = ("검색 '" + q + "' · Event(Search) + 결과 화면 Pageview(ViewSearchResults) · "
                                  "검색어는 부가 정보로 남고, 직접 선언이라 /" + wrote["path"] + " 에 [stated] 기록")
        elif ev in ("click", "read", "skim", "react", "comment"):
            try:
                idx = int(body.get("idx"))
            except (TypeError, ValueError):
                return {"error": "콘텐츠 번호가 올바르지 않습니다"}
            c = byidx.get(idx)
            if not c:
                return {"error": "콘텐츠를 찾을 수 없습니다 · 새로고침 후 다시 시도하세요"}
            dwell = max(0, min(USAGE_MAX_SEC, int(body.get("dwell_sec") or 0)))   # TIARA Usage 최대 10분
            scroll = max(0, min(100, int(body.get("scroll_pct") or 0)))
            rec = {"idx": idx, "event": ev, "dwell": dwell, "scroll": scroll, "t": _now_t(),
                   "title": c["title"][:40], "cat": c["cat"], "intent": c["intent"], "path": ""}
            if ev == "click":
                sess["last_logic"] = ('클릭 "' + c["title"][:24] + '" → Event(ClickContent) + 읽기 화면 '
                                      "Pageview(ViewContent) · 이 콘텐츠의 소비 가중 ×2.0")
            elif ev == "react":                      # 감정 반응 → Event(Like) · 감정 상세는 Custom Properties
                emo = body.get("emotion") or ""
                if emo not in EMOTIONS:
                    return {"error": "지원하지 않는 반응입니다"}
                # 반응은 콘텐츠당 최신 1건만 유지 · 감정을 바꿔 누르면 교체(호응 중복 가중 방지)
                events = [e for e in events
                          if not (e.get("event") == "react" and e.get("idx") == idx)]
                files = _files(team)
                wrote, err = _observe(files, c["title"] or "(제목 없음)", c["cat"], c["intent"],
                                      "반응 '" + emo + "'")
                if err:
                    return {"error": err}
                _persist(team, files)
                rec["emo"] = emo
                rec["path"] = wrote["path"]
                sess["last_logic"] = ("반응 '" + emo + "' → Event(Like) · 호응 가중 +" + str(REACT_W)
                                      + " 을 " + c["cat"] + " 선호에 합산 · 감정은 Custom Properties 기록 · /"
                                      + wrote["path"] + " 에 [observed]")
            elif ev == "comment":                    # 댓글 = 직접 발화 → Event(WriteComment) + [stated] 기록
                text = (body.get("text") or "").strip()[:200]
                if not text:
                    return {"error": "댓글 내용을 입력하세요"}
                files = _files(team)
                wrote, err = _observe(files, text, c["cat"], c["intent"],
                                      '· "' + c["title"][:24] + '" 기사에 남긴 의견', tag="stated")
                if err:
                    return {"error": err}
                _persist(team, files)
                rec["text"] = text[:40]
                rec["path"] = wrote["path"]
                sess["last_logic"] = ("댓글 → Event(WriteComment) · 호응 가중 +" + str(COMMENT_W)
                                      + " 을 " + c["cat"] + " 선호에 합산 · 직접 말한 의견이라 /"
                                      + wrote["path"] + " 에 [stated] 로 기록(관찰과 구분)")
            else:
                files = _files(team)
                wrote, err = _observe(files, c["title"] or "(제목 없음)", c["cat"], c["intent"],
                                      EV_LABEL[ev], dwell)
                if err:
                    return {"error": err}
                _persist(team, files)
                rec["path"] = wrote["path"]
                clicked = any(e.get("idx") == idx and e.get("event") == "click" for e in events)
                wt = round((dwell / 30.0) * (2.0 if clicked else 1.0), 1)
                sess["last_logic"] = ("체류 " + str(dwell) + "초 ÷ 30 × 클릭가중 "
                                      + ("2.0" if clicked else "1.0") + " → " + c["cat"] + " +" + str(wt)
                                      + " · /" + wrote["path"] + " 에 [observed] 기록")
            events.append(rec)
        else:
            return {"error": "지원하지 않는 이벤트입니다: " + str(ev)[:20]}
        sess["events"] = events[-DEMO_EVENTS_MAX:]
        _demo_save(team, sess)

    elif op == "reset":                              # 세션만 초기화 · 메모리 파일은 유지
        sess = {}
        _demo_save(team, sess)

    else:
        return {"error": "지원하지 않는 조작입니다: " + str(op)[:20]}

    out = demo_data(team)
    if wrote:
        out["wrote"] = wrote
    return out
