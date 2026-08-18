"""AI 초안 판정 (실험실 · 검수 보조와 별개).

검수가 귀찮은 운영자를 위해, **별도 심판 모델**이 대기 콘텐츠의 메타데이터가 맞는지
정확/수정 초안 + 근거 + 확신도를 미리 채운다. 사람은 그걸 보며 확정/뒤집기만 한다.
판정을 자동 커밋하지 않는다 — 확정은 평소 검수와 같은 /feedback 경로(사람 행위)로만.

측정 독립성:
- **골드 문항은 대상이 아니다.** results_rows(실 콘텐츠)만 보고, 골드는 _inject_gold 로만
  큐에 섞이므로 애초에 여기 들어오지 않는다(별도 제외 로직 불필요).
- **심판 모델 ≠ 콘텐츠 생성 모델** 이 원칙(assist_model 과 같은 이유 · 자기 숙제 자기 채점 방지).
  모델은 config.draft_judge_model() 로 고른다(기본 = 목록 최고 지능). 콘텐츠 모델을 출력에
  실어 같은 모델이면 화면에서 눈에 띄게 한다.

컴포지션: 스토어·LLM 라우팅·결과 뷰는 serve 가 _SV 로 주입(learnops 관례).
"""
from __future__ import annotations

import json

from . import config as C

_SV = None                      # serve 모듈 객체 · serve import 시 주입

_JUDGE_SYSTEM = (
    "너는 콘텐츠 메타데이터 품질 검수자다. 주어진 콘텐츠(제목·본문)와 AI가 추출한 메타데이터"
    "(리드문·엔티티·인텐트·카테고리·등급·사유)를 보고 메타데이터가 정확한지 판정한다.\n"
    "- 정확하면 verdict=\"good\", 고칠 게 있으면 verdict=\"bad\".\n"
    "- confidence 는 0~1 사이 숫자(자신 없으면 낮게 · 애매하면 0.5 이하).\n"
    "- reason 은 한국어 한 문장(왜 그렇게 봤는지 · 근거에 있는 사실만).\n"
    "- elements 는 틀린 요소 이름 목록(summary·entities·intent·category·grade 중 · verdict=bad 일 때만).\n"
    "근거에 없는 것을 지어내지 않는다. 판단이 어려우면 confidence 를 낮춘다. "
    "JSON 객체 하나로만 답한다. 키: "
    '{"verdict":"good|bad","confidence":0.0,"reason":"...","elements":["..."]}'
)

_ELEM_OK = {"summary", "entities", "intent", "category", "grade"}


def _clip(s, n: int) -> str:
    s = str(s or "").strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"


def _pending_rows(st, team, reviewer, limit: int) -> list:
    """검수 대기(YELLOW)이면서 이 사람이 아직 판정하지 않은 실 콘텐츠 행. 골드는 실 콘텐츠가
    아니라 여기 없다. reviewer 는 본인 판정 제외 판별용(이름 또는 uid)."""
    rows = _SV.results_rows(team=team)
    try:
        fmap = _SV.feedback_map_cached(team)
    except Exception:
        fmap = {}
    me = (reviewer or "").strip()
    out = []
    for r in rows:
        qm = r.get("quality_meta") or {}
        if (qm.get("review") or "") != "yellow":       # 검수 대상(YELLOW)만
            continue
        ref = r.get("content_ref") or {}
        ch = _SV._row_key(ref)
        fb = fmap.get(ch) or {}
        judged = any((v.get("reviewer") == me or v.get("reviewer_id") == me)
                     for v in (fb.get("verdicts") or [])) if me else False
        if judged:                                      # 이미 내가 판정한 건 다시 제안하지 않는다
            continue
        out.append((ch, r))
        if len(out) >= limit:
            break
    return out


def _judge_one(llm, r: dict) -> dict:
    """콘텐츠 1건을 심판 모델에 넘겨 정확/수정 초안을 받는다. 실패·형식오류는 낮은 확신도로 표기."""
    ref = r.get("content_ref") or {}
    im = r.get("item_meta") or {}
    qm = r.get("quality_meta") or {}
    payload = {
        "제목": ref.get("title", ""), "서비스": ref.get("displayServiceName", ""),
        "본문": _clip(ref.get("body", ""), 2000),
        "리드문": im.get("summary", ""), "엔티티": im.get("entities", []) or [],
        "인텐트": im.get("intent", []) or [], "카테고리": im.get("content_category", []) or [],
        "등급": qm.get("finalGrade", ""), "사유": qm.get("reasons", []) or [],
    }
    obj, _res = llm.complete_json(_JUDGE_SYSTEM, json.dumps(payload, ensure_ascii=False), tag="autoreview")
    if not isinstance(obj, dict) or obj.get("_fail"):
        return {"verdict": "", "confidence": 0.0,
                "reason": "심판 모델 호출 실패 · 직접 검수하세요", "elements": []}
    verdict = "bad" if str(obj.get("verdict") or "").lower() == "bad" else \
        ("good" if str(obj.get("verdict") or "").lower() == "good" else "")
    try:
        conf = max(0.0, min(1.0, float(obj.get("confidence"))))
    except (TypeError, ValueError):
        conf = 0.0
    elems = [e for e in (obj.get("elements") or []) if isinstance(e, str) and e.strip() in _ELEM_OK]
    return {"verdict": verdict, "confidence": round(conf, 2),
            "reason": _clip(obj.get("reason", ""), 200), "elements": elems if verdict == "bad" else []}


def suggest(team=None, reviewer: str = "", limit: int = 20) -> dict:
    """대기 콘텐츠에 AI 초안 판정을 채워 돌려준다(커밋하지 않음). 확정은 화면에서 사람이 /feedback 으로.
    반환 {ok, model, items:[{hash,title,service,contentModel,sameModel, ai:{verdict,confidence,reason,elements}}], n}."""
    st = _SV.get_store()
    if not st:
        return {"ok": False, "error": "store unavailable", "items": [], "n": 0}
    cfg = C.Config.load()
    judge = C.draft_judge_model(cfg)
    mock = bool(getattr(_SV.Handler, "server_mock", False))
    llm, route = _SV.llm_for_model(judge, mock)
    if llm is None:
        return {"ok": False, "error": "심판 모델을 부를 수 없습니다(%s) · 실험실에서 다른 모델을 고르거나 "
                                      "라우터 키를 등록하세요" % (route or judge), "model": judge, "items": [], "n": 0}
    limit = max(1, min(100, int(limit or 20)))
    items = []
    for ch, r in _pending_rows(st, team, reviewer, limit):
        ai = _judge_one(llm, r)
        cmodel = (r.get("trace") or {}).get("model", "") or ""
        ref = r.get("content_ref") or {}
        items.append({"hash": ch, "title": ref.get("title", "") or "(제목 없음)",
                      "service": ref.get("displayServiceName", ""),
                      "contentModel": cmodel, "sameModel": bool(cmodel and cmodel == judge),
                      "ai": ai})
    return {"ok": True, "model": judge, "items": items, "n": len(items)}
