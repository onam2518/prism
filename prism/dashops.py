"""대시보드·롤업 도메인 (serve 에서 분리 · 라우트 분리 4차 · 로드맵 2단계 2차).

대시보드 집계(dashboard_data)·드릴다운(drill_contents)과 비용/실패 롤업
(cost/fail_rollup: 기록 훅 + 조회)을 담당. HTTP 디스패치는 serve 가 유지.

컴포지션: 스토어·결과 뷰·집계 캐시는 serve 가 `_SV` 로 주입(learnops 관례).
"""
from __future__ import annotations

import json
import os
import tempfile
import threading

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입


_COST_LOCK = threading.Lock()


def _log_cost_rollup(trace: dict, team=None):
    """실행 1건의 비용·토큰을 일별 롤업 리포트에 누적. 실패는 실행을 막지 않는다."""
    try:
        trace = trace or {}
        cost = float(trace.get("cost_usd") or 0.0)
        by_call = trace.get("by_call") or {}
        if not (cost or by_call):
            return
        import datetime as _dt
        day = _dt.date.today().isoformat()
        tokens = trace.get("tokens") or {}
        model = (trace.get("model") or "").strip() or "(미기록)"
        with _COST_LOCK:
            rep = _SV._report_get("cost_rollup", team, {}) or {}
            days = rep.setdefault("days", {})
            d = days.setdefault(day, {"cost": 0.0, "n": 0, "in": 0, "out": 0,
                                      "models": {}, "calls": {}})
            d["cost"] = round(d["cost"] + cost, 6)
            d["n"] += 1
            d["in"] += int(tokens.get("in") or 0)
            d["out"] += int(tokens.get("out") or 0)
            m = d["models"].setdefault(model, {"cost": 0.0, "n": 0})
            m["cost"] = round(m["cost"] + cost, 6)
            m["n"] += 1
            for tag, b in by_call.items():
                cle = d["calls"].setdefault(str(tag), {"cost": 0.0, "n": 0, "in": 0, "out": 0})
                cle["cost"] = round(cle["cost"] + float((b or {}).get("cost") or 0.0), 6)
                cle["n"] += int((b or {}).get("n") or 0)
                cle["in"] += int((b or {}).get("in") or 0)
                cle["out"] += int((b or {}).get("out") or 0)
            if len(days) > 90:                       # 90일 초과분 정리(리포트 무한 성장 방지)
                for k in sorted(days)[:-90]:
                    days.pop(k, None)
            _SV._report_save("cost_rollup", rep, team)
    except Exception:
        pass


# ── 실패 트리아지 원장(종류×모델×서비스) ─────────────────────────────────────
# supabase 는 콘텐츠에 트레이스(fails)를 저장하지 않아 실행 시점 누적이 유일한 영속 원천.
# reports kind='fail_rollup'(팀 스코프) · 단일 서버 프로세스 전제 프로세스 락 직렬화.
_FAIL_LOCK = threading.Lock()


def _log_fail_rollup(trace: dict, service: str = "", team=None):
    """실행 1건의 콜 실패(trace.fails)를 일별 원장에 누적. 실패 없으면 무기록."""
    try:
        trace = trace or {}
        fails = trace.get("fails") or []
        if not fails:
            return
        import datetime as _dt
        day = _dt.date.today().isoformat()
        model = (trace.get("model") or "").strip() or "(미기록)"
        svc = (service or "").strip() or "(미기록)"
        with _FAIL_LOCK:
            rep = _SV._report_get("fail_rollup", team, {}) or {}
            days = rep.setdefault("days", {})
            d = days.setdefault(day, {})
            for f in fails:
                kind = str((f or {}).get("kind") or "unknown")
                tag = str((f or {}).get("tag") or "")
                key = "|".join((kind, model, svc, tag))
                d[key] = int(d.get(key) or 0) + 1
            if len(days) > 90:                       # 90일 초과분 정리
                for k in sorted(days)[:-90]:
                    days.pop(k, None)
            _SV._report_save("fail_rollup", rep, team)
    except Exception:
        pass


def cost_rollup_data(team=None, days: int = 30) -> dict:
    """비용 롤업 조회: 최근 days 일 연속 by_day + 창 내 모델별·콜별 합산."""
    import datetime as _dt
    days = max(1, min(90, int(days or 30)))
    rep = _SV._report_get("cost_rollup", team, {}) or {}
    stored = rep.get("days") or {}
    today = _dt.date.today()
    by_day, by_model, by_call = [], {}, {}
    total = {"cost": 0.0, "n": 0, "in": 0, "out": 0}
    for i in range(days - 1, -1, -1):
        k = (today - _dt.timedelta(days=i)).isoformat()
        d = stored.get(k) or {}
        by_day.append({"day": k, "cost": round(float(d.get("cost") or 0.0), 6),
                       "n": int(d.get("n") or 0)})
        total["cost"] = round(total["cost"] + float(d.get("cost") or 0.0), 6)
        total["n"] += int(d.get("n") or 0)
        total["in"] += int(d.get("in") or 0)
        total["out"] += int(d.get("out") or 0)
        for mk, mv in (d.get("models") or {}).items():
            e = by_model.setdefault(mk, {"model": mk, "cost": 0.0, "n": 0})
            e["cost"] = round(e["cost"] + float(mv.get("cost") or 0.0), 6)
            e["n"] += int(mv.get("n") or 0)
        for ck, cv in (d.get("calls") or {}).items():
            e = by_call.setdefault(ck, {"call": ck, "cost": 0.0, "n": 0, "in": 0, "out": 0})
            e["cost"] = round(e["cost"] + float(cv.get("cost") or 0.0), 6)
            e["n"] += int(cv.get("n") or 0)
            e["in"] += int(cv.get("in") or 0)
            e["out"] += int(cv.get("out") or 0)
    return {"ok": True, "window_days": days, "total": total, "by_day": by_day,
            "by_model": sorted(by_model.values(), key=lambda x: -x["cost"]),
            "by_call": sorted(by_call.values(), key=lambda x: -x["cost"])}
def fail_rollup_data(team=None, days: int = 30) -> dict:
    """실패 트리아지 조회: 창 내 종류별·모델별·서비스별·콜별 합산 + 상세 조합 상위."""
    import datetime as _dt
    days = max(1, min(90, int(days or 30)))
    rep = _SV._report_get("fail_rollup", team, {}) or {}
    stored = rep.get("days") or {}
    today = _dt.date.today()
    keys = {(today - _dt.timedelta(days=i)).isoformat() for i in range(days)}
    by_kind, by_model, by_service, by_call, combos = {}, {}, {}, {}, {}
    total = 0
    for day, counters in stored.items():
        if day not in keys:
            continue
        for key, n in (counters or {}).items():
            parts = (key.split("|") + ["", "", "", ""])[:4]
            kind, model, svc, tag = parts
            n = int(n or 0)
            total += n
            by_kind[kind] = by_kind.get(kind, 0) + n
            by_model[model] = by_model.get(model, 0) + n
            by_service[svc] = by_service.get(svc, 0) + n
            if tag:
                by_call[tag] = by_call.get(tag, 0) + n
            ck = (kind, model, svc)
            combos[ck] = combos.get(ck, 0) + n
    def _sorted(d):
        return [{"k": k, "n": n} for k, n in sorted(d.items(), key=lambda x: -x[1])]
    top = [{"kind": k[0], "model": k[1], "service": k[2], "n": n}
           for k, n in sorted(combos.items(), key=lambda x: -x[1])[:20]]
    return {"ok": True, "window_days": days, "total": total,
            "by_kind": _sorted(by_kind), "by_model": _sorted(by_model),
            "by_service": _sorted(by_service), "by_call": _sorted(by_call), "top": top}
# ── 학습 지시 무효화(개별 끄기) ──────────────────────────────────────────────
def dashboard_data(team=None) -> dict:
    """\ub300\uc2dc\ubcf4\ub4dc \ubaa8\ub4c8 \uc9d1\uacc4. team \ubcc4 \uc2a4\ucf54\ud551 \u00b7 \uc9e7\uc740 TTL \uce90\uc2dc(\ubc18\ubcf5 \ub85c\ub4dc \uc2dc 5000\ud589 \uc7ac\uc2a4\uce94 \ubc29\uc9c0)."""
    return _SV._agg_cached(("dash", team), lambda: _dashboard_compute(team))


def _dashboard_compute(team=None) -> dict:
    """\uc801\uc7ac \uacb0\uacfc \uc9d1\uacc4(\uc720\ud1b5 G/R \u00b7 \uc778\ud150\ud2b8 \u00b7 \uce74\ud14c\uace0\ub9ac \u00b7 \ud488\uc9c8 \uc0ac\uc720) + \ucf58\ud150\uce20\ubcc4 \ud53c\ub4dc\ubc31."""
    rows = _SV.results_rows(team=team)
    n = len(rows)
    g = sum(1 for r in rows if (r.get("quality_meta") or {}).get("finalGrade") == "G")
    intent_c, cat_c, reason_c = {}, {}, {}
    lead_sum = lead_n = ent_total = 0
    for r in rows:
        im = r.get("item_meta") or {}
        for t in (im.get("intent") or []):
            intent_c[t] = intent_c.get(t, 0) + 1
        for v in (im.get("content_category") or []):
            top = (v or "").split("/")[0].strip()
            if top:
                cat_c[top] = cat_c.get(top, 0) + 1
        ent_total += len(im.get("entities") or [])
        s = im.get("summary") or ""
        if s:
            lead_sum += len(s); lead_n += 1
        for rs in ((r.get("quality_meta") or {}).get("reasons") or []):
            reason_c[rs] = reason_c.get(rs, 0) + 1

    def topk(dd, k=8):
        items = sorted(dd.items(), key=lambda x: -x[1])[:k]
        return [{"k": a, "v": b, "pct": round(b / n * 100) if n else 0} for a, b in items]

    # 콘텐츠별 행 + 평가 피드백(학습 루프) 부착
    contents, fb_stats = [], {"total": 0, "good": 0, "bad": 0, "learned": 0}
    try:
        st = _SV.get_store()
        if st:
            fmap = st.feedback_map(team=team)
            for row in st.recent_meta(team=team):
                row["fb"] = _SV._fb_public(fmap.get(row["hash"], {}))
                contents.append(row)
            fb_stats = st.feedback_stats(team=team)
    except Exception:
        pass

    return {
        "n": n, "g": g, "r": n - g, "gPct": round(g / n * 100) if n else 0,
        "entities": ent_total, "avgLead": round(lead_sum / lead_n) if lead_n else 0,
        "intents": topk(intent_c), "categories": topk(cat_c), "qualityReasons": topk(reason_c),
        "contents": contents, "feedback": fb_stats,
    }


def drill_contents(kind: str, value: str, team=None, reviewer: str = "") -> dict:
    """대시보드 드릴다운: intent|category|reason = value 로 판정된 콘텐츠 목록."""
    rows = _SV.results_rows(team=team)
    out = []
    for r in rows:
        im = r.get("item_meta") or {}
        qm = r.get("quality_meta") or {}
        if kind == "intent":
            hit = value in (im.get("intent") or [])
        elif kind == "category":
            hit = any((v or "").split("/")[0].strip() == value or (v or "").strip() == value
                      for v in (im.get("content_category") or []))
        elif kind == "reason":
            hit = value in (qm.get("reasons") or [])
        else:
            hit = False
        if hit:
            out.append(_SV._detail_row(r))
    return {"ok": True, "kind": kind, "value": value, "items": _SV._attach_fb(out, team, reviewer), "n": len(out)}


def build_results_csv(team=None) -> bytes:
    """적재된 추출 결과(콘텐츠 현황)를 CSV(엑셀)로 내보냄. team 스코프 강제(전 팀 유출 방지)."""
    rows = _SV.results_rows(team=team)
    out = ["제목,서비스,리드문,엔티티,인텐트,콘텐츠 카테고리,등급,품질 사유,노출제한"]
    def esc(v):
        s = str(v if v is not None else "")
        # CSV 수식 인젝션 중화: 셀 선두 = + - @ 및 탭/CR 은 스프레드시트가 수식/DDE 로 실행 →
        # 선행 작은따옴표로 무력화(RFC4180 따옴표 이스케이프는 유지).
        if s[:1] in ("=", "+", "-", "@", "\t", "\r"):
            s = "'" + s
        return '"' + s.replace('"', '""') + '"'
    for r in rows:
        im = r.get("item_meta") or {}
        qm = r.get("quality_meta") or {}
        c = r.get("content") or {}
        cat = " · ".join(im.get("content_category") or [])
        out.append(",".join(esc(x) for x in [
            c.get("title", ""), c.get("displayServiceName", ""), im.get("summary", ""),
            " · ".join(im.get("entities") or []), " · ".join(im.get("intent") or []),
            cat, qm.get("finalGrade", ""), " · ".join(qm.get("reasons") or []),
            "제한" if qm.get("ops_hold") else "",
        ]))
    return ("﻿" + "\r\n".join(out)).encode("utf-8")


def build_report_html(team=None) -> str:
    rows = _SV.results_rows(team=team)
    if not rows:
        return "<p>아직 실행 결과가 없습니다. 먼저 추출을 실행하세요.</p>"
    from . import dashboard as DASH
    with tempfile.TemporaryDirectory() as d:
        rpath = os.path.join(d, "results.jsonl")
        with open(rpath, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        out = os.path.join(d, "report.html")
        try:
            DASH.build_integrated(rpath, out, title="Prism 리포트")
            return open(out, encoding="utf-8").read()
        except Exception as e:
            return f"<p>리포트 생성 실패: {e}</p>"


# ── 설정(API 키 / 모델 / 엔드포인트) ─────────────────────────────────────────
