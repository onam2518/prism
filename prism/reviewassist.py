"""내부 검수 보조(트랙 A) 도구 · 검수자가 화면에서 판정을 빨리 내리도록 돕는다.

검수를 대신하는 기능이 아니다. 검수 결과는 **사람의 판단을 재는 값**이라, 보조가 답을
흘리는 순간 측정 대상이 사람이 아니게 된다. 그래서 아래 넷은 편의 요구가 아니라 계약이다.

1. **판정 전에는 근거·기준만.** `stage='before'` 응답에는 추천성 키가 아예 없다(빈 값이
   아니라 키 부재). 비워서 내려보내면 클라이언트가 언젠가 그 키를 읽고, 그때부터 조용히
   답이 샌다. 모르는 stage 값은 'before' 로 수렴한다(모르면 덜 주는 쪽).
2. **추천·수정 제안은 판정 뒤에만.** `stage='after'` 에서만 suggestions 가 붙는다.
3. **골드 문항은 어떤 경로로도 나가지 않는다.** 요청 해시가 골드면 거절하고, 선례·제안
   목록에서도 거른다. 골드는 검수자 신뢰도를 재는 장치라 보조가 개입하면 측정이 무너진다.
4. **근거를 지어내지 않는다.** 저장된 값을 조립할 뿐 모델을 새로 돌리지 않는다. 판정 근거를
   사후에 재생성하면 그럴듯한 창작이 된다(모델은 자기 추론 과정에 접근하지 못한다).
   `quality_meta.evidence` 가 비어 있으면 없다고 말한다 — 2026-08-12 신설 필드라 그 이전
   운영 데이터는 대부분 비어 있고, 그 빈칸을 메우려는 순간 이 도구는 거짓말을 시작한다.

공통 가드(팀 강제·상한·잘림 보고·예외 은닉)와 디스패치는 `prismtools` 를 그대로 쓴다.
도구 계층 규칙이 두 벌이 되면 반드시 어긋나고, 어긋난 쪽이 조용히 틀린 답을 낸다.

앞단은 `serve.py` 의 `/assist`(gate=team) 하나. 팀은 세션에서 해석한 값만 들어온다.
"""
from __future__ import annotations

from . import dictionaries as D
from . import prismtools as PT

_SV = None                                   # serve 주입(컴포지션 루트)

STAGES = ("before", "after")

# stage='before' 응답에서 존재 자체가 금지된 키. 코드가 실수로 담아도 마지막에 떨어낸다
# (테스트뿐 아니라 런타임에서도 막는 이중 방어 · 이 목록이 독립성의 경계선이다).
SUGGESTIVE_KEYS = ("suggestions", "recommendation", "recommended", "answer", "verdict_hint", "advice")

GOLD_MSG = "골드 문항에는 검수 보조를 제공하지 않습니다"
NOT_FOUND_MSG = "콘텐츠를 찾지 못했습니다"
# 근거가 없을 때 내려보내는 문장. '없다' 를 말하는 것이 이 도구의 정확성이다.
NO_EVIDENCE = "저장된 모델 판정 근거가 없습니다(근거 저장 이전 데이터) · 근거는 지어내지 않습니다"

TITLE_MAX, NOTE_MAX, EVIDENCE_SNIP = 60, 200, 200
CRITERIA_MAX = 12
PRECEDENT_DEFAULT, PRECEDENT_MAX = 5, 20
DISSENT_MAX = 20
PATCH_SCAN, SUGGEST_MAX = 400, 5

# 교정 로그(patch_log)가 쓰는 요소 키 → 이 콘텐츠의 현재 값을 읽어 올 자리.
# 여기 없는 키는 제안 대상이 아니다(모르는 필드로 추측하지 않는다).
PATCH_FIELDS = ("summary", "entities", "intent", "content_category", "topic",
                "finalGrade", "reasons")
# 교정 로그 요소 키 → _values 의 키(이름이 다른 것만).
_VAL_KEY = {"finalGrade": "grade"}


# ── 소소한 도구 ──────────────────────────────────────────────────────────────
def _clip(s, n: int) -> str:
    s = str(s or "").strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"


def _names(vs) -> list:
    """엔티티 등 문자열/객체 혼재 목록을 표시 문자열 목록으로."""
    out = []
    for v in vs or []:
        n = (v.get("name") or v.get("text") or "") if isinstance(v, dict) else str(v or "")
        n = str(n).strip()
        if n:
            out.append(n)
    return out


def _epoch(ts) -> float:
    """정렬용 epoch. sqlite=float · supabase=ISO 문자열 혼재를 흡수(reviewops 이력과 같은 해석)."""
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _norm(v) -> str:
    """값 비교용 정규화. 목록은 순서를 지켜 잇는다(순서도 교정 대상이라 정렬하지 않는다)."""
    if isinstance(v, (list, tuple)):
        return " · ".join(str(x).strip() for x in v if str(x).strip())
    return str(v if v is not None else "").strip()


def _reason_label(rid: str) -> str:
    return (getattr(D, "QUALITY_META_NAMES", {}) or {}).get(rid, rid)


def _index(team) -> dict:
    """팀 스코프 콘텐츠 해시 → 저장 행. 팀 필터는 저장 계층이 건다(호출자 팀만 넘긴다)."""
    if not _SV:
        return {}
    try:
        rows = _SV.results_rows(team=team) or []
    except Exception:
        return {}
    out = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        try:
            k = _SV._row_key(r.get("content_ref") or {})
        except Exception:
            continue
        if k:
            out[k] = r
    return out


def _feedback(team) -> dict:
    st = _SV.get_store() if _SV else None
    if not (st and hasattr(st, "feedback_map")):
        return {}
    try:
        return st.feedback_map(team=team) or {}
    except Exception:
        return {}


def _values(row: dict) -> dict:
    """부여된 값 묶음. 저장된 것만 담는다(빈 값은 빈 값으로 남긴다)."""
    ref = row.get("content_ref") or {}
    qm = row.get("quality_meta") or {}
    im = row.get("item_meta") or {}
    reasons = [str(r) for r in (qm.get("reasons") or []) if r]
    return {
        "service": str(ref.get("displayServiceName", "") or ""),
        "title": str(ref.get("title", "") or ""),
        "grade": str(qm.get("finalGrade", "") or ""),
        "reasons": reasons,
        "reason_labels": [_reason_label(r) for r in reasons],
        "review": str(qm.get("review", "") or ""),
        "review_reason": str(qm.get("review_reason", "") or ""),
        "confidence": qm.get("confidence"),
        "intent": _names(im.get("intent")),
        "content_category": _names(im.get("content_category")),
        "entities": _names(im.get("entities")),
        "summary": str(im.get("summary", "") or ""),
        "topic": str(im.get("topic", "") or ""),
    }


# ── content_brief ────────────────────────────────────────────────────────────
def _summary3(vals: dict, evidence: str) -> list:
    """3줄 요약. 저장된 값을 잇는 것뿐이라 문장이 늘거나 줄지 않는다(항상 3줄)."""
    cat = " · ".join(vals["content_category"]) or "카테고리 미부여"
    l1 = f"{vals['service'] or '서비스 미상'} · {_clip(vals['title'], TITLE_MAX)} · {cat}"
    rs = ", ".join(vals["reason_labels"]) or "지적된 품질 사유 없음"
    l2 = f"모델 초안: 등급 {vals['grade'] or '미상'} · {rs}"
    if vals["review"] == "yellow":
        l2 += " · 사람 검수 필요" + (f"({vals['review_reason']})" if vals["review_reason"] else "")
    l3 = ("모델이 남긴 판정 근거: " + _clip(evidence, EVIDENCE_SNIP)) if evidence else NO_EVIDENCE
    return [l1, l2, l3]


def _criteria(vals: dict, team) -> list:
    """이 콘텐츠에 걸린 분류 기준 발췌. 정의문 원천은 INTENT_VALUE_DEFS·QUALITY_METAS 하나뿐이라
    prismtools.get_taxonomy 를 그대로 재사용한다(검수 화면·추출 프롬프트와 같은 문장)."""
    tx = PT.get_taxonomy("intent", service=vals["service"], team=team) or {}
    defs = {v.get("key"): v.get("desc", "") for v in (tx.get("values") or [])}
    picked = [k for k in vals["intent"] if k in defs]
    items = [{"key": k, "desc": defs.get(k, "")} for k in picked]

    rtx = PT.get_taxonomy("reason", team=team) or {}
    rdefs = {v.get("key"): (v.get("label") or v.get("key"), v.get("desc", ""))
             for v in (rtx.get("values") or [])}
    for r in vals["reasons"]:
        lbl, desc = rdefs.get(r, (_reason_label(r), ""))
        items.append({"key": lbl, "desc": desc})

    if not picked:              # 부여된 인텐트가 없으면 그 서비스의 후보 기준을 보여 준다(추천 아님)
        room = max(0, CRITERIA_MAX - len(items))
        items += [{"key": k, "desc": defs.get(k, "")} for k in list(defs)[:room]]

    out, seen = [], set()
    for it in items:
        if it["key"] and it["key"] not in seen:
            seen.add(it["key"])
            out.append(it)
    return out[:CRITERIA_MAX]


def _suggestions(ch: str, vals: dict, idx: dict, team) -> list:
    """수정 제안 = 같은 서비스에서 **같은 값을 같게 고친 선례**의 집계. 새로 만들어 내는 값이 없다.

    판정 뒤에만 부른다. before 에서 이 함수가 불릴 일이 없어야 한다(content_brief 가 분기)."""
    st = _SV.get_store() if _SV else None
    if not (st and hasattr(st, "patch_rows")):
        return []
    try:
        rows = st.patch_rows(limit=PATCH_SCAN, team=team) or []
    except Exception:
        return []
    svc_of = {h: str((r.get("content_ref") or {}).get("displayServiceName", "") or "")
              for h, r in idx.items()}
    cur = {k: _norm(vals[_VAL_KEY.get(k, k)]) for k in PATCH_FIELDS}
    tally = {}
    for pr in PT.strip_gold(rows):
        h = str(pr.get("hash") or "")
        if not h or h == ch or svc_of.get(h) != vals["service"]:
            continue                              # 다른 서비스·자기 자신의 교정은 선례가 아니다
        before, after = pr.get("before"), pr.get("after")
        if not (isinstance(before, dict) and isinstance(after, dict)):
            continue
        for k, av in after.items():
            if k not in cur:
                continue
            b, a = _norm(before.get(k)), _norm(av)
            if not a or a == b or b != cur[k]:    # 같은 값에서 출발해 실제로 바뀐 교정만
                continue
            key = (k, b, a)
            tally[key] = tally.get(key, 0) + 1
    ranked = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"field": k, "from": b, "to": a,
             "basis": f"같은 서비스({vals['service']})에서 같은 값을 이렇게 고친 선례 {n}건"}
            for (k, b, a), n in ranked[:SUGGEST_MAX]]


def content_brief(hash: str = "", stage: str = "before", team=None) -> dict:
    """이 콘텐츠가 왜 이렇게 판정됐나 · 3줄 요약 + 저장된 근거 + 부여된 값 + 분류 기준."""
    blocked = PT.need_team(team)
    if blocked:
        return blocked
    ch = str(hash or "").strip()
    if not ch:
        return {"error": "콘텐츠를 지정해 주세요"}
    if PT.is_gold(ch):
        return {"error": GOLD_MSG}
    stage = str(stage or "").strip().lower()
    if stage not in STAGES:
        stage = "before"                          # 모르는 값 = 덜 주는 쪽으로 수렴
    idx = _index(team)
    row = idx.get(ch)
    if row is None:
        return {"error": NOT_FOUND_MSG}
    vals = _values(row)
    ev = str((row.get("quality_meta") or {}).get("evidence") or "").strip()
    out = {
        "stage": stage,
        "summary3": _summary3(vals, ev),
        "evidence": ev or None,                   # 없으면 없다고 말한다(빈 문자열로 얼버무리지 않는다)
        "has_evidence": bool(ev),
        "values": vals,
        "criteria": _criteria(vals, team),
    }
    if stage == "after":
        out["suggestions"] = _suggestions(ch, vals, idx, team)
    else:
        for k in SUGGESTIVE_KEYS:                 # 이중 방어: 판정 전에는 키 자체가 없어야 한다
            out.pop(k, None)
    return out


# ── verdict_precedents ───────────────────────────────────────────────────────
def verdict_precedents(hash: str = "", limit=None, team=None) -> dict:
    """비슷한 과거 판정(선례). 무엇이 '비슷함'인지(why_similar)를 함께 실어 검수자가 스스로 본다.

    확정된 것만 센다 = 표가 있고 갈리지 않은 건(agree). 갈린 건은 reviewer_dissent 쪽이다."""
    blocked = PT.need_team(team)
    if blocked:
        return blocked
    ch = str(hash or "").strip()
    empty = {"items": [], "total": 0, "truncated": False}
    if not ch:
        return dict(empty, error="콘텐츠를 지정해 주세요")
    if PT.is_gold(ch):
        return dict(empty, error=GOLD_MSG)
    lim = PT.qint(limit, PRECEDENT_DEFAULT, 1, PRECEDENT_MAX)
    idx = _index(team)
    row = idx.get(ch)
    if row is None:
        return dict(empty, error=NOT_FOUND_MSG)
    me = _values(row)
    mine = (set(me["reasons"]), set(me["intent"]), set(me["content_category"]))
    fbm = _feedback(team)

    ranked = []
    for h, r in idx.items():
        if h == ch or PT.is_gold(h):              # 골드는 선례로도 나가지 않는다
            continue
        v = _values(r)
        if v["service"] != me["service"]:
            continue
        fb = fbm.get(h) or {}
        if not (fb.get("n") and fb.get("agree")):  # 확정(만장일치)된 판정만 선례로 쓴다
            continue
        verdict = str(fb.get("consensus") or fb.get("verdict") or "")
        if verdict not in ("good", "bad"):
            continue
        why, score = [], 0
        shared = mine[0] & set(v["reasons"])
        if shared:
            why.append("같은 품질 사유: " + ", ".join(_reason_label(x) for x in sorted(shared)))
            score += 3
        shared = mine[1] & set(v["intent"])
        if shared:
            why.append("같은 인텐트: " + ", ".join(sorted(shared)))
            score += 2
        shared = mine[2] & set(v["content_category"])
        if shared:
            why.append("같은 카테고리: " + ", ".join(sorted(shared)))
            score += 2
        if not score:                             # 서비스만 같은 건 '비슷함' 이 아니다
            continue
        if v["grade"] and v["grade"] == me["grade"]:
            why.append(f"같은 등급: {v['grade']}")
            score += 1
        last = (fb.get("verdicts") or [{}])[-1]
        ts = _epoch(last.get("ts"))
        ranked.append((score, ts, {
            "hash": h,
            "title": _clip(v["title"], TITLE_MAX),
            "verdict": verdict,
            "reason": _clip(fb.get("note") or last.get("note") or "", NOTE_MAX),
            "ts": ts,
            "why_similar": " · ".join([f"같은 서비스: {v['service']}"] + why),
        }))
    ranked.sort(key=lambda x: (-x[0], -x[1]))
    return PT.envelope([it for _, _, it in ranked], lim)


# ── reviewer_dissent ─────────────────────────────────────────────────────────
def reviewer_dissent(hash: str = "", team=None) -> dict:
    """다른 검수자 의견·불일치. 양쪽을 시간순으로 나란히 준다.

    다수결·합의 값을 담지 않는다 — 담는 순간 그것이 정답으로 읽히고, 검수자가 자기 판단 대신
    다수를 따라간다(측정하려던 값이 사라진다)."""
    blocked = PT.need_team(team)
    if blocked:
        return blocked
    ch = str(hash or "").strip()
    empty = {"items": [], "total": 0, "truncated": False, "split": False}
    if not ch:
        return dict(empty, error="콘텐츠를 지정해 주세요")
    if PT.is_gold(ch):
        return dict(empty, error=GOLD_MSG)
    fb = _feedback(team).get(ch) or {}
    rows = sorted((fb.get("verdicts") or []), key=lambda v: _epoch(v.get("ts")))
    items = [{"reviewer": str(v.get("reviewer") or ""),
              "verdict": str(v.get("verdict") or ""),
              "reason": _clip(v.get("note") or "", NOTE_MAX),
              "ts": _epoch(v.get("ts"))} for v in rows]
    split = bool(fb.get("good")) and bool(fb.get("bad"))
    return PT.envelope(items, DISSENT_MAX, split=split)


# ── 도구 등록부(트랙 A 전용) ─────────────────────────────────────────────────
# scope 는 전부 internal — 이 도구들은 팀 콘텐츠와 검수자 판정을 읽는다. 외부 MCP(트랙 B)에
# 열리면 파트너 키로 팀 검수 이력이 통째로 나간다.
TOOLS = {
    "content_brief": {
        "scope": "internal",
        "title": "판정 근거 브리핑",
        "desc": "이 콘텐츠가 왜 그렇게 판정됐는지 3줄로 준다. 저장된 모델 근거·부여된 값·해당 분류 기준. "
                "판정 전(before)에는 근거와 기준만, 판정 뒤(after)에만 수정 제안이 붙는다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hash": {"type": "string", "description": "콘텐츠 해시"},
                "stage": {"type": "string", "enum": list(STAGES),
                          "description": "before=판정 전(근거·기준만) · after=판정 뒤(수정 제안 포함)"},
            },
            "required": ["hash"],
            "additionalProperties": False,
        },
        "fn": content_brief,
    },
    "verdict_precedents": {
        "scope": "internal",
        "title": "비슷한 과거 판정",
        "desc": "같은 서비스에서 같은 사유·같은 값으로 확정된 과거 판정을 준다. "
                "무엇이 비슷한지(why_similar)를 함께 주므로 판단은 검수자가 한다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hash": {"type": "string", "description": "콘텐츠 해시"},
                "limit": {"type": "integer", "minimum": 1, "maximum": PRECEDENT_MAX,
                          "description": f"가져올 수(기본 {PRECEDENT_DEFAULT} · 최대 {PRECEDENT_MAX})"},
            },
            "required": ["hash"],
            "additionalProperties": False,
        },
        "fn": verdict_precedents,
    },
    "reviewer_dissent": {
        "scope": "internal",
        "title": "검수자 의견·불일치",
        "desc": "이 콘텐츠에 판정이 갈린 이력이 있으면 양쪽을 나란히 준다(다수결을 정답으로 주지 않는다).",
        "inputSchema": {
            "type": "object",
            "properties": {"hash": {"type": "string", "description": "콘텐츠 해시"}},
            "required": ["hash"],
            "additionalProperties": False,
        },
        "fn": reviewer_dissent,
    },
}


def call(name, args, team=None) -> dict:
    """도구 실행 진입점. 가드·디스패치는 prismtools.call 을 그대로 쓴다(규칙 단일 원천).

    team 은 **호출자가 세션에서 해석한 값**이다. args 의 team 은 무시된다(스키마에 없다).
    이름·인자의 타입은 여기서 강제한다 — 앞단이 HTTP 본문이라 문자열도 dict 도 아닌 값이
    그대로 들어올 수 있고, 그러면 도구 오류가 아니라 500 이 난다(내부 노출)."""
    return PT.call(str(name or ""), args if isinstance(args, dict) else {},
                   team=team, registry=TOOLS)
