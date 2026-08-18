"""AI 초안 판정 (실험실 · 검수 보조와 별개).

검수가 귀찮은 운영자를 위해, **별도 심판 모델**이 대기 콘텐츠의 메타데이터가 맞는지
정확/수정 초안 + 근거 + 확신도를 미리 채운다. 사람은 그걸 보며 확정/뒤집기만 한다.
판정을 자동 커밋하지 않는다 — 확정은 평소 검수와 같은 /feedback 경로(사람 행위)로만.

대상: **나에게 배정된 대기(YELLOW) 콘텐츠 전부**(아직 내가 판정 안 한 것). 배정이 없으면
미배정 포함 대기 큐로 폴백한다. 골드 문항은 대상이 아니다(results_rows 실 콘텐츠만 · 골드는
_inject_gold 로만 큐에 섞이므로 애초에 안 들어옴).

실행은 오래 걸릴 수 있어 **백그라운드 잡 + 진척도/예상 시간**으로 돈다(평가 런과 같은 패턴):
start() 가 잡을 띄우고 id·total 을 주면, status(id) 를 폴링해 done/total·ETA·부분 결과를 본다.

측정 정직성: 심판 모델 ≠ 콘텐츠 생성 모델 권장(assist_model 과 같은 이유) · 같으면 sameModel 표기.

컴포지션: 스토어·LLM 라우팅·결과 뷰는 serve 가 _SV 로 주입(learnops 관례).
"""
from __future__ import annotations

import json
import threading
import time

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
_CAP = 200                      # 한 번에 채점할 최대 콘텐츠(비용·시간 상한 · 넘치면 truncated)

_RUNS: dict = {}                # {id: {running,total,done,started,items,model,scope,error,reviewer}}
_LOCK = threading.Lock()
_SEQ = 0


def _clip(s, n: int) -> str:
    s = str(s or "").strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"


def _fmt_dur(sec: float) -> str:
    sec = int(max(0, sec))
    if sec < 60:
        return f"{sec}초"
    if sec < 3600:
        return f"{sec // 60}분 {sec % 60}초"
    return f"{sec // 3600}시간 {(sec % 3600) // 60}분"


def _target_rows(st, team, reviewer: str):
    """(rows, scope). 배정이 있으면 **나에게 배정된 미검수 콘텐츠 전부**(진척율 '내 담당 N중 M검수'
    와 같은 정의 = 배정분 중 내가 아직 판정 안 한 것 · YELLOW 여부 무관 — 배정 대부분이 자동 확정
    G/R 이라 YELLOW 만 보면 몇 건만 잡혔다 · 2026-08-18). 실제 검수가 필요한 YELLOW 를 앞에 둔다.
    배정이 없으면 오픈 검수 큐(YELLOW)로 폴백. 채점엔 본문·메타가 필요하니 results_rows 원본을 쓴다.
    골드는 실 콘텐츠가 아니라 results_rows 에 없다(자연 제외)."""
    me = (reviewer or "").strip()
    try:
        fmap = _SV.feedback_map_cached(team)
    except Exception:
        fmap = {}

    def _judged(ch):
        fb = fmap.get(ch) or {}
        return bool(me) and any((v.get("reviewer") == me or v.get("reviewer_id") == me)
                                for v in (fb.get("verdicts") or []))

    try:
        assigned = st.assignees(team=team) or {}
    except Exception:
        assigned = {}
    mine = {ch for ch, a in assigned.items() if me and me in (a.get("reviewers") or [])}
    scope = "assigned" if mine else "all"
    rows = _SV.results_rows(team=team)
    out = []
    if scope == "assigned":
        rows_by = {_SV._row_key(r.get("content_ref") or {}): r for r in rows}
        for ch in mine:
            r = rows_by.get(ch)
            if r is None or _judged(ch):                               # 원본 없음(옛 콘텐츠)·이미 판정 = 제외
                continue
            out.append((ch, r))
        out.sort(key=lambda cr: 0 if (cr[1].get("quality_meta") or {}).get("review") == "yellow" else 1)
    else:                                                              # 배정 없음: 오픈 검수 큐(YELLOW)
        for r in rows:
            if ((r.get("quality_meta") or {}).get("review") or "") != "yellow":
                continue
            ch = _SV._row_key(r.get("content_ref") or {})
            if _judged(ch):
                continue
            out.append((ch, r))
    return out, scope


def _judge_one(llm, r: dict) -> dict:
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


def _run_loop(run_id, targets, llm, judge):
    for ch, r in targets:
        with _LOCK:
            run = _RUNS.get(run_id)
            if not run or not run["running"]:                          # 취소·정리됨
                return
        ai = _judge_one(llm, r)                                        # LLM 호출은 락 밖(느림)
        cmodel = (r.get("trace") or {}).get("model", "") or ""
        ref = r.get("content_ref") or {}
        item = {"hash": ch, "title": ref.get("title", "") or "(제목 없음)",
                "service": ref.get("displayServiceName", ""),
                "contentModel": cmodel, "sameModel": bool(cmodel and cmodel == judge), "ai": ai}
        with _LOCK:
            run = _RUNS.get(run_id)
            if not run:
                return
            run["items"].append(item)
            run["done"] += 1
    with _LOCK:
        run = _RUNS.get(run_id)
        if run:
            run["running"] = False


def start(team=None, reviewer: str = "") -> dict:
    """AI 초안 판정 백그라운드 잡 시작. 반환 {ok, id, total, model, scope}(또는 error)."""
    global _SEQ
    st = _SV.get_store()
    if not st:
        return {"ok": False, "error": "store unavailable"}
    cfg = C.Config.load()
    judge = C.draft_judge_model(cfg)
    mock = bool(getattr(_SV.Handler, "server_mock", False))
    llm, route = _SV.llm_for_model(judge, mock)
    if llm is None:
        return {"ok": False, "error": "심판 모델을 부를 수 없습니다(%s) · 실험실에서 다른 모델을 고르거나 "
                                      "라우터 키를 등록하세요" % (route or judge), "model": judge}
    targets, scope = _target_rows(st, team, reviewer)
    truncated = max(0, len(targets) - _CAP)
    targets = targets[:_CAP]
    with _LOCK:
        _SEQ += 1
        run_id = _SEQ
        _RUNS[run_id] = {"running": True, "total": len(targets), "done": 0, "started": time.time(),
                         "items": [], "model": judge, "scope": scope, "error": "",
                         "reviewer": reviewer, "truncated": truncated}
        for k in [k for k, v in _RUNS.items() if not v["running"] and k < run_id - 20]:   # 오래된 잡 정리
            _RUNS.pop(k, None)
    if not targets:
        with _LOCK:
            _RUNS[run_id]["running"] = False
        return {"ok": True, "id": run_id, "total": 0, "model": judge, "scope": scope,
                "empty": ("배정된" if scope == "assigned" else "대기") + " 검수 대상이 없습니다"}
    threading.Thread(target=_run_loop, args=(run_id, targets, llm, judge), daemon=True).start()
    return {"ok": True, "id": run_id, "total": len(targets), "model": judge, "scope": scope,
            **({"truncated": truncated} if truncated else {})}


def status(run_id) -> dict:
    """폴링: 진척도(done/total·pct·ETA) + 지금까지의 초안(items). 완료면 running=False."""
    try:
        rid = int(run_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "잘못된 id"}
    with _LOCK:
        run = _RUNS.get(rid)
        if not run:
            return {"ok": False, "error": "만료된 실행입니다 · 다시 실행하세요"}
        done, total, started = run["done"], run["total"], run["started"]
        snap = list(run["items"])
        out = {"ok": True, "id": rid, "running": run["running"], "done": done, "total": total,
               "model": run["model"], "scope": run["scope"], "items": snap,
               "truncated": run.get("truncated", 0)}
    out["pct"] = round(done / total, 3) if total else 1.0
    if run["running"] and done and total:                              # 남은 예상 시간(평균 속도 × 남은 건)
        per = (time.time() - started) / done
        out["eta"] = _fmt_dur(per * (total - done))
    elif not run["running"]:
        out["elapsed"] = _fmt_dur(time.time() - started)
    return out
