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

from . import alerts as AL
from .store import day_key

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입


_COST_LOCK = threading.Lock()


# 롤업 신규 키(2026-08-09 · 프롬프트 캐시 관측). 기존 키(cost·n·in·out)는 그대로 두고 추가만 한다.
# 구 리포트에는 이 키들이 아예 없으므로 읽을 때도 쓸 때도 `get(...) or 0` 로 시작해야 한다
# (기존 원장을 마이그레이션하지 않고 그대로 이어 쓴다 = 그날부터 쌓이고 과거는 0).
_CALL_EXTRA = ("ms", "cache_read", "cache_write", "retries")


def _log_cost_rollup(trace: dict, team=None):
    """실행 1건의 비용·토큰·지연·캐시를 일별 롤업 리포트에 누적. 실패는 실행을 막지 않는다."""
    try:
        trace = trace or {}
        cost = float(trace.get("cost_usd") or 0.0)
        by_call = trace.get("by_call") or {}
        if not (cost or by_call):
            return
        day = day_key()
        tokens = trace.get("tokens") or {}
        lat = trace.get("latency_ms") or {}
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
            # 캐시 토큰·실소요(wall) — 종전에는 트레이스에만 있다 실행이 끝나면 사라졌다.
            d["cache_read"] = int(d.get("cache_read") or 0) + int(tokens.get("cache_read") or 0)
            d["cache_write"] = int(d.get("cache_write") or 0) + int(tokens.get("cache_write") or 0)
            d["wall_ms"] = int(d.get("wall_ms") or 0) + int(lat.get("wall") or 0)
            m = d["models"].setdefault(model, {"cost": 0.0, "n": 0})
            m["cost"] = round(m["cost"] + cost, 6)
            m["n"] += 1
            for tag, b in by_call.items():
                b = b or {}
                cle = d["calls"].setdefault(str(tag), {"cost": 0.0, "n": 0, "in": 0, "out": 0})
                cle["cost"] = round(cle["cost"] + float(b.get("cost") or 0.0), 6)
                cle["n"] += int(b.get("n") or 0)
                cle["in"] += int(b.get("in") or 0)
                cle["out"] += int(b.get("out") or 0)
                for k in _CALL_EXTRA:          # 신규 키: 구 항목엔 없으니 get 으로 시작
                    cle[k] = int(cle.get(k) or 0) + int(b.get(k) or 0)
            if len(days) > 90:                       # 90일 초과분 정리(리포트 무한 성장 방지)
                for k in sorted(days)[:-90]:
                    days.pop(k, None)
            _SV._report_save("cost_rollup", rep, team)
            day_total = d["cost"]
        AL.on_cost(day, day_total)                   # 당일 임계 초과 통지(웹훅 미설정 시 무동작)
    except Exception:
        pass


# ── 실패 트리아지 원장(종류×모델×서비스) ─────────────────────────────────────
# supabase 는 콘텐츠에 트레이스(fails)를 저장하지 않아 실행 시점 누적이 유일한 영속 원천.
# reports kind='fail_rollup'(팀 스코프) · 단일 서버 프로세스 전제 프로세스 락 직렬화.
_FAIL_LOCK = threading.Lock()


_RECENT_FAIL_CAP = 100          # 최근 실패 콘텐츠 목록 상한(원장 무한 성장 방지)


def _log_fail_rollup(trace: dict, service: str = "", team=None,
                     content_hash: str = "", title: str = ""):
    """실행 1건의 콜 실패(trace.fails)를 일별 원장에 누적. 실패 없으면 카운터 무기록.
    content_hash 가 있으면 '최근 실패 콘텐츠' 목록(recent)도 관리: 실패 시 등재(중복은
    최신으로 교체) · 무실패 성공 실행 시 제거 — 재실행으로 해소된 건이 목록에 남지 않는다."""
    try:
        trace = trace or {}
        fails = trace.get("fails") or []
        ch = (content_hash or "").strip()
        if not fails:
            if ch:                                   # 성공 실행 → 해소된 콘텐츠는 목록에서 제거
                with _FAIL_LOCK:
                    rep = _SV._report_get("fail_rollup", team, {}) or {}
                    rec = rep.get("recent") or []
                    kept = [e for e in rec if (e or {}).get("hash") != ch]
                    if len(kept) != len(rec):
                        rep["recent"] = kept
                        _SV._report_save("fail_rollup", rep, team)
            return
        day = day_key()
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
            if ch:                                   # 개별 재실행 대상 식별용(콘텐츠 단위)
                kinds = sorted({str((f or {}).get("kind") or "unknown") for f in fails})
                calls = sorted({str((f or {}).get("tag") or "") for f in fails} - {""})
                # 예외 원문: 종류 배지만으로는 원인을 못 좁힌다(타임아웃 vs 응답 형식 vs 연결).
                # 콜당 1줄 · 총 3줄로 제한해 원장이 비대해지지 않게 한다.
                details = [f"{(f or {}).get('tag') or '?'}: {str((f or {}).get('detail') or '')[:160]}"
                           for f in fails if (f or {}).get("detail")][:3]
                rec = [e for e in (rep.get("recent") or []) if (e or {}).get("hash") != ch]
                rec.insert(0, {"hash": ch, "title": (title or "").strip(),
                               "service": svc, "model": model, "kinds": kinds,
                               "calls": calls, "details": details, "day": day})
                rep["recent"] = rec[:_RECENT_FAIL_CAP]
            _SV._report_save("fail_rollup", rep, team)
        first = (fails[0] or {}) if fails else {}
        AL.on_fail(len(fails), kind=str(first.get("kind") or ""), model=model)   # 급증 통지
    except Exception:
        pass


def cost_rollup_data(team=None, days: int = 30) -> dict:
    """비용 롤업 조회: 최근 days 일 연속 by_day + 창 내 모델별·콜별 합산."""
    import time as _t
    days = max(1, min(90, int(days or 30)))
    rep = _SV._report_get("cost_rollup", team, {}) or {}
    stored = rep.get("days") or {}
    now = _t.time()
    by_day, by_model, by_call = [], {}, {}
    # cache_read/cache_write/wall_ms 는 2026-08-09 이후 적재분에만 있다(그 전 날짜는 0).
    total = {"cost": 0.0, "n": 0, "in": 0, "out": 0,
             "cache_read": 0, "cache_write": 0, "wall_ms": 0}
    for i in range(days - 1, -1, -1):
        k = day_key(now - i * 86400)
        d = stored.get(k) or {}
        by_day.append({"day": k, "cost": round(float(d.get("cost") or 0.0), 6),
                       "n": int(d.get("n") or 0)})
        total["cost"] = round(total["cost"] + float(d.get("cost") or 0.0), 6)
        total["n"] += int(d.get("n") or 0)
        total["in"] += int(d.get("in") or 0)
        total["out"] += int(d.get("out") or 0)
        for kk in ("cache_read", "cache_write", "wall_ms"):
            total[kk] += int(d.get(kk) or 0)
        for mk, mv in (d.get("models") or {}).items():
            e = by_model.setdefault(mk, {"model": mk, "cost": 0.0, "n": 0})
            e["cost"] = round(e["cost"] + float(mv.get("cost") or 0.0), 6)
            e["n"] += int(mv.get("n") or 0)
        for ck, cv in (d.get("calls") or {}).items():
            e = by_call.setdefault(ck, {"call": ck, "cost": 0.0, "n": 0, "in": 0, "out": 0,
                                        "ms": 0, "cache_read": 0, "cache_write": 0, "retries": 0})
            e["cost"] = round(e["cost"] + float(cv.get("cost") or 0.0), 6)
            e["n"] += int(cv.get("n") or 0)
            e["in"] += int(cv.get("in") or 0)
            e["out"] += int(cv.get("out") or 0)
            for kk in _CALL_EXTRA:             # 구 리포트엔 없는 키 → 0 으로 채워 응답 모양을 고정
                e[kk] += int(cv.get(kk) or 0)
    return {"ok": True, "window_days": days, "total": total, "by_day": by_day,
            "by_model": sorted(by_model.values(), key=lambda x: -x["cost"]),
            "by_call": sorted(by_call.values(), key=lambda x: -x["cost"])}
def fail_rollup_data(team=None, days: int = 30) -> dict:
    """실패 트리아지 조회: 창 내 종류별·모델별·서비스별·콜별 합산 + 상세 조합 상위
    + 최근 실패 콘텐츠(recent · 개별 재실행 대상)."""
    import time as _t
    days = max(1, min(90, int(days or 30)))
    rep = _SV._report_get("fail_rollup", team, {}) or {}
    stored = rep.get("days") or {}
    now = _t.time()
    keys = {day_key(now - i * 86400) for i in range(days)}
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
    recent = [e for e in (rep.get("recent") or []) if (e or {}).get("day") in keys]
    return {"ok": True, "window_days": days, "total": total,
            "by_kind": _sorted(by_kind), "by_model": _sorted(by_model),
            "by_service": _sorted(by_service), "by_call": _sorted(by_call), "top": top,
            "recent": recent}


# ── 검수 활동 원장(append-only) ──────────────────────────────────────────────
# feedback 은 (콘텐츠,검수자)당 1행 upsert 라 재검수하면 과거 활동의 ts 가 최신으로
# 이동한다(추이 드레인). 판정 행위 시점에 일별 카운터로 증분 기록해 활동 추이를 보존.
_ACT_LOCK = threading.Lock()


def _log_activity_rollup(team=None, reviews=0, corrections=0, gold_n=0, gold_correct=0):
    """검수 활동 1건(판정·골드 응답)을 append-only 일별 롤업에 누적. 실패해도 검수는 계속."""
    try:
        with _ACT_LOCK:
            rep = _SV._report_get("activity_rollup", team, {}) or {}
            days = rep.setdefault("days", {})
            d = days.setdefault(day_key(), {"reviews": 0, "corrections": 0,
                                            "gold_n": 0, "gold_correct": 0})
            d["reviews"] = int(d.get("reviews") or 0) + int(reviews)
            d["corrections"] = int(d.get("corrections") or 0) + int(corrections)
            d["gold_n"] = int(d.get("gold_n") or 0) + int(gold_n)
            d["gold_correct"] = int(d.get("gold_correct") or 0) + int(gold_correct)
            if len(days) > 90:                       # 90일 초과분 정리
                for k in sorted(days)[:-90]:
                    days.pop(k, None)
            _SV._report_save("activity_rollup", rep, team)
    except Exception:
        pass


def activity_daily_data(team=None, days: int = 30) -> dict:
    """검수 활동 추이 조회: 스토어 재구성(feedback·gold 스캔) + append-only 롤업을
    일별 max 로 병합. 롤업 도입 전 과거 날짜는 재구성 값, 이후는 롤업이 우세하다."""
    st = _SV.get_store()
    rows = (st.activity_daily(days=days, team=team)
            if (st and hasattr(st, "activity_daily")) else [])
    stored = (_SV._report_get("activity_rollup", team, {}) or {}).get("days") or {}
    for r in rows:
        d = stored.get((r or {}).get("day"))
        if d:
            for k in ("reviews", "corrections", "gold_n", "gold_correct"):
                r[k] = max(int(r.get(k) or 0), int(d.get(k) or 0))
    return {"ok": True, "days": rows}


# ── 대시보드 집계·리포트 ──────────────────────────────────────────────
def dashboard_data(team=None) -> dict:
    """대시보드 모듈 집계. team 별 스코핑 · 짧은 TTL 캐시(반복 로드 시 5000행 재스캔 방지)."""
    return _SV._agg_cached(("dash", team), lambda: _dashboard_compute(team))


def _dashboard_compute(team=None) -> dict:
    """적재 결과 집계(유통 G/R · 인텐트 · 카테고리 · 품질 사유) + 콘텐츠별 피드백."""
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
            # supabase: 원본 행 1회 조회를 map·stats 가 공유(전량 fetch 2회 → 1회 · 수치 정의 불변)
            fb_rows = st.feedback_rows(team=team) if hasattr(st, "feedback_rows") else None
            if fb_rows is not None:
                fmap = st.feedback_map(team=team, rows=fb_rows)
                fb_stats = st.feedback_stats(team=team, rows=fb_rows)
            else:
                fmap = st.feedback_map(team=team)
                fb_stats = st.feedback_stats(team=team)
            for row in st.recent_meta(team=team):
                row["fb"] = _SV._fb_public(fmap.get(row["hash"], {}))
                contents.append(row)
    except Exception:
        pass

    # contents 는 표시용 최신 200건(recent_meta 상한)이라 건수를 세면 안 된다.
    # 실행 버튼의 '미실행만 N건'·'전체 재실행 N건'은 팀 전체 기준이어야 한다 —
    # 창 기준으로 세던 때는 미실행 200건이 창 밖이라 '0건'으로 보였다(2026-07-28).
    return {
        "n": n, "g": g, "r": n - g, "gPct": round(g / n * 100) if n else 0,
        "entities": ent_total, "avgLead": round(lead_sum / lead_n) if lead_n else 0,
        "intents": topk(intent_c), "categories": topk(cat_c), "qualityReasons": topk(reason_c),
        "contents": contents, "feedback": fb_stats,
        "contents_n": len(rows), "pending_n": sum(1 for r in rows if _SV._is_pending_row(r)),
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
        c = r.get("content_ref") or {}
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
