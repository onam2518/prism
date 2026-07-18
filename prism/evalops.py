"""평가 런 도메인 (Atelier eval_runs 이식 · 학습(learnops)과 분리).

골든셋 평가를 '런' 단위로 영속화한다: 백그라운드 청크 실행(커서 진행) →
건별 결과 저장(불일치 감사·실패 군집 원천) → 요약 지표 집계.
기존 즉시 평가(learnops.eval_golden)와 채점 의미를 동일하게 유지하되
(abtest.score 와 같은 계수 규칙), 이력·진행률·중단 복구를 더한다.

Atelier(구 PromptForge)의 eval_runs/eval_run_results 체계에서 가져온 설계:
· 런 행에 cursor/total 을 두고 청크마다 갱신 → 진행률 표시·재개 지점.
· 건별 결과를 (run_id, content_hash) 로 저장 → 서버 재시작 후 남은 건만 재실행.
· 지표는 증분 카운터(metrics)로 런 행에 누적 → 완주 전에도 부분 리포트 가능.

컴포지션: learnops 와 동일 — serve 가 기동 시 `_SV`(자기 모듈 객체)를 주입한다.
HTTP 디스패치는 serve 가 유지.
"""
from __future__ import annotations
import threading
import time

from .config import Config

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입

_ACTIVE: dict = {}              # run_id → Thread (이 프로세스가 실행 중인 런)
_CANCEL: set = set()            # 중단 요청된 run_id
_LOCK = threading.Lock()
CHUNK = 24                      # 청크당 건수(concurrency 8 의 3배 · 커서 갱신 주기)
MAX_ROWS = 1000                 # 런당 평가 상한(get_golden 상한과 동일)


def _zero_metrics() -> dict:
    """abtest.score 와 같은 계수 규칙의 증분 카운터(런 행에 누적 저장)."""
    return {"n": 0, "grade_hit": 0, "reason_exact": 0, "jaccard_sum": 0.0,
            "harm_miss": 0, "empty": 0, "cost_usd": 0.0, "tok_in": 0, "tok_out": 0,
            "lat": [], "yellow": 0, "auto_n": 0, "auto_hit": 0, "per_reason": {}}


def _tally(m: dict, row: dict, out) -> dict:
    """건 1개를 카운터에 반영하고 저장용 건별 결과 행을 반환.
    계수 규칙은 abtest.score 와 동일(실패 산출도 등급 채점에 포함 · empty 비배타)."""
    m["n"] += 1
    exp = row.get("expected") or {}
    if out is None:
        m["empty"] += 1
        return {"expected": {"finalGrade": exp.get("finalGrade", ""),
                             "reasons": exp.get("reasons", []) or []},
                "got": None, "passed": False, "error": "empty"}
    qm = out.get("quality_meta") or {}
    tr = out.get("trace") or {}
    m["cost_usd"] = round(m["cost_usd"] + (tr.get("cost_usd") or 0.0), 6)
    m["tok_in"] += (tr.get("tokens") or {}).get("in", 0)
    m["tok_out"] += (tr.get("tokens") or {}).get("out", 0)
    lt = tr.get("latency_ms")
    t_ms = lt.get("total") if isinstance(lt, dict) else lt
    if t_ms:
        m["lat"].append(float(t_ms))
    if any("fail" in str(f) or "unparse" in str(f) for f in tr.get("fallbacks", [])):
        m["empty"] += 1
    grade_ok = qm.get("finalGrade") == exp.get("finalGrade")
    m["grade_hit"] += int(grade_ok)
    if qm.get("review") == "yellow":
        m["yellow"] += 1
    else:
        m["auto_n"] += 1
        m["auto_hit"] += int(grade_ok)
    got, want = set(qm.get("reasons") or []), set(exp.get("reasons") or [])
    m["reason_exact"] += int(got == want)
    u = got | want
    m["jaccard_sum"] += (len(got & want) / len(u)) if u else 1.0
    if exp.get("finalGrade") == "R" and qm.get("finalGrade") == "G":
        m["harm_miss"] += 1
    bucket = (exp.get("reasons") or ["normal"])[0]
    d = m["per_reason"].setdefault(bucket, {"n": 0, "grade_ok": 0})
    d["n"] += 1
    d["grade_ok"] += int(grade_ok)
    return {"expected": {"finalGrade": exp.get("finalGrade", ""),
                         "reasons": sorted(want)},
            "got": {"finalGrade": qm.get("finalGrade", ""), "reasons": sorted(got)},
            "passed": bool(grade_ok), "error": ""}


def _percentile(vals: list, p: float):
    if not vals:
        return None
    s = sorted(vals)
    i = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
    return round(float(s[i]), 1)


def _prepare(team, model: str, scope: str):
    """골든셋 로드(scope 필터) + LLM 해석. learnops.eval_golden 과 동일 규칙.
    반환 (rows, llm, used_model, error) · 실패 시 rows=None."""
    from . import learnops as LO
    st = _SV.get_store()
    if not (st and hasattr(st, "get_golden")):
        return None, None, "", "골든셋 평가는 스토어 가용 시에만 가능합니다"
    rows = st.get_golden(team)
    if not rows:
        return None, None, "", "등록된 골든셋이 없습니다 · 팀 관리에서 등록하세요"
    rows = LO._scope_golden(rows, scope, st, team)
    if not rows:
        return None, None, "", "평가용으로 지정된 콘텐츠의 정답이 없습니다 · 콘텐츠 관리 STEP 1에서 용도를 지정하세요"
    cfg = Config.load()
    used_model = (model or "").strip()
    if used_model:
        llm, route = _SV.llm_for_model(used_model, _SV.Handler.server_mock)
        if llm is None:
            return None, None, "", f"모델 호출 불가({route}): {used_model}"
    else:
        llm = _SV.make_text_llm(cfg, _SV.Handler.server_mock)
        used_model = cfg.model or getattr(llm, "model", "") or ""
    return rows[:MAX_ROWS], llm, used_model, ""


def eval_run_start(team=None, model: str = "", scope: str = "all", created_by: str = "") -> dict:
    """평가 런 생성 + 백그라운드 실행 시작. 즉시 {id,total} 반환(진행은 폴링)."""
    st = _SV.get_store()
    rows, llm, used_model, err = _prepare(team, model, scope)
    if rows is None:
        return {"ok": False, "error": err}
    if not hasattr(st, "eval_run_create"):
        return {"ok": False, "error": "스토어가 평가 런을 지원하지 않습니다"}
    run_id = st.eval_run_create(team, used_model, scope, len(rows), created_by=created_by or "")
    _launch(run_id, rows, llm, team, _zero_metrics())
    return {"ok": True, "id": run_id, "total": len(rows)}


def eval_run_resume(run_id: int, team=None) -> dict:
    """중단(서버 재시작·실패)된 런 재개: 저장된 건별 결과를 빼고 남은 건만 실행.
    같은 모델·범위로 골든셋을 다시 읽으므로, 그사이 골든이 바뀌면 남은 건 기준도 그에 따른다."""
    st = _SV.get_store()
    run = st.eval_run_get(run_id, team) if hasattr(st, "eval_run_get") else None
    if not run:
        return {"ok": False, "error": "런을 찾을 수 없습니다"}
    with _LOCK:
        if run_id in _ACTIVE and _ACTIVE[run_id].is_alive():
            return {"ok": False, "error": "이미 실행 중입니다"}
    if run.get("status") not in ("running", "failed"):
        return {"ok": False, "error": "재개할 수 없는 상태입니다: " + str(run.get("status"))}
    rows, llm, _used, err = _prepare(team, run.get("model") or "", run.get("scope") or "all")
    if rows is None:
        return {"ok": False, "error": err}
    from .store import content_hash
    done = st.eval_result_hashes(run_id, team)
    remain = [r for r in rows if content_hash(r.get("content") or {}) not in done]
    base = run.get("metrics") or _zero_metrics()
    st.eval_run_update(run_id, team=team, status="running", error="",
                       total=len(done) + len(remain))
    _launch(run_id, remain, llm, team, base)
    return {"ok": True, "id": run_id, "remain": len(remain)}


def eval_run_cancel(run_id: int, team=None) -> dict:
    """실행 중 런 중단 요청(청크 경계에서 멈춤 · 저장된 결과는 유지)."""
    with _LOCK:
        alive = run_id in _ACTIVE and _ACTIVE[run_id].is_alive()
    if not alive:                                # 이 프로세스에 스레드 없음 = 재시작 유실 → 상태만 정리
        st = _SV.get_store()
        run = st.eval_run_get(run_id, team) if hasattr(st, "eval_run_get") else None
        if run and run.get("status") == "running":
            st.eval_run_update(run_id, team=team, status="cancelled", finished=time.time())
            return {"ok": True, "id": run_id}
        return {"ok": False, "error": "실행 중이 아닙니다"}
    _CANCEL.add(run_id)
    return {"ok": True, "id": run_id}


def _launch(run_id: int, rows: list, llm, team, base_metrics: dict):
    th = threading.Thread(target=_run_loop, args=(run_id, rows, llm, team, base_metrics),
                          name=f"prism-eval-run-{run_id}", daemon=True)
    with _LOCK:
        _ACTIVE[run_id] = th
    th.start()


def _run_loop(run_id: int, rows: list, llm, team, m: dict):
    """청크 단위 실행: 실행(concurrency 8) → 건별 저장 → 커서·카운터 갱신."""
    st = _SV.get_store()
    from . import abtest
    from . import harness as H
    from .store import content_hash
    meth = H.Methodology(name="골든셋")
    try:
        for i in range(0, len(rows), CHUNK):
            if run_id in _CANCEL:
                st.eval_run_update(run_id, team=team, status="cancelled",
                                   metrics=m, finished=time.time())
                return
            chunk = rows[i:i + CHUNK]
            outs = abtest.run_methodology(chunk, meth, llm, concurrency=8)
            results = []
            for row, out in zip(chunk, outs):
                r = _tally(m, row, out)
                c = row.get("content") or {}
                r.update({"hash": content_hash(c), "title": (c.get("title") or "")[:60]})
                results.append(r)
            st.eval_results_add(run_id, results, team)
            st.eval_run_update(run_id, team=team, cursor=m["n"], metrics=m)
        st.eval_run_update(run_id, team=team, status="done", cursor=m["n"],
                           metrics=m, finished=time.time())
    except Exception as e:                        # 무음 실패 방지: 상태·사유를 런 행에 남긴다
        try:
            st.eval_run_update(run_id, team=team, status="failed", metrics=m,
                               error=str(e)[:300], finished=time.time())
        except Exception:
            pass
        print(f"  [eval-run] #{run_id} 실패: {e}")
    finally:
        with _LOCK:
            _ACTIVE.pop(run_id, None)
        _CANCEL.discard(run_id)


def eval_runs_list(team=None, limit: int = 20) -> dict:
    """평가 런 이력(최신순). running 인데 이 프로세스에 스레드가 없으면 stalled 표시(재개 대상)."""
    st = _SV.get_store()
    if not (st and hasattr(st, "eval_runs_list")):
        return {"ok": False, "error": "스토어가 평가 런을 지원하지 않습니다", "items": []}
    items = st.eval_runs_list(team, limit=limit)
    with _LOCK:
        for it in items:
            it["stalled"] = bool(it.get("status") == "running"
                                 and not (it.get("id") in _ACTIVE and _ACTIVE[it["id"]].is_alive()))
            m = it.pop("metrics", None) or {}
            n = m.get("n") or 0
            it["grade_accuracy"] = round(m.get("grade_hit", 0) / n, 4) if n else None
    return {"ok": True, "items": items}


def eval_run_report(run_id: int, team=None) -> dict:
    """런 리포트: 요약 지표(즉시 평가와 동일 필드) + 불일치 상세(건별 판정 병합).
    완주 전(running)에도 지금까지 카운터로 부분 리포트를 낸다."""
    st = _SV.get_store()
    run = st.eval_run_get(run_id, team) if (st and hasattr(st, "eval_run_get")) else None
    if not run:
        return {"ok": False, "error": "런을 찾을 수 없습니다"}
    m = run.get("metrics") or {}
    n = m.get("n") or 0
    cfg = Config.load()
    from . import quality as Q
    out = {"ok": True, "id": run_id, "status": run.get("status"),
           "cursor": run.get("cursor") or 0, "total": run.get("total") or 0,
           "ts": run.get("ts"), "finished": run.get("finished"),
           "run_error": run.get("error") or "",
           "evaluated": n,
           "grade_accuracy": round(m.get("grade_hit", 0) / n, 4) if n else 0,
           "reason_exact_match": round(m.get("reason_exact", 0) / n, 4) if n else 0,
           "reason_jaccard": round(m.get("jaccard_sum", 0.0) / n, 4) if n else 0,
           "harm_miss_rate": round(m.get("harm_miss", 0) / n, 4) if n else 0,
           "empty_rate": round(m.get("empty", 0) / n, 4) if n else 0,
           "cost_usd": round(m.get("cost_usd", 0.0), 6),
           "tokens": {"in": m.get("tok_in", 0), "out": m.get("tok_out", 0)},
           "latency_p50_ms": _percentile(m.get("lat") or [], 0.5),
           "latency_p95_ms": _percentile(m.get("lat") or [], 0.95),
           "yellow_rate": round(m.get("yellow", 0) / n, 4) if n else 0,
           "auto_coverage": round(m.get("auto_n", 0) / n, 4) if n else 0,
           "auto_grade_accuracy": (round(m.get("auto_hit", 0) / m.get("auto_n", 1), 4)
                                   if m.get("auto_n") else 0),
           "by_reason_bucket": {k: {"n": v["n"], "grade_acc": round(v["grade_ok"] / v["n"], 3)}
                                for k, v in sorted((m.get("per_reason") or {}).items()) if v.get("n")},
           "min_good": int(getattr(cfg, "golden_min_good", 1) or 1)}
    lo, hi = Q.binomial_ci(out["grade_accuracy"] or 0.0, n)
    out["grade_ci"] = {"lo": lo, "hi": hi, "n": n}
    try:
        seq = st.batch_seq(team) if hasattr(st, "batch_seq") else 0
    except Exception:
        seq = 0
    out["basis"] = {"model": run.get("model") or "", "version": seq + 1,
                    "scope": run.get("scope") or "all"}
    detail = []                                  # 불일치(등급) 건만 · 즉시 평가 detail 과 동일 형태
    try:
        fails = st.eval_results_list(run_id, team, only_fail=True, limit=500)
    except Exception:
        fails = []
    try:
        jm = st.eval_check_counts(team) if hasattr(st, "eval_check_counts") else {}
    except Exception:
        jm = {}
    for r in fails:
        exp = (r.get("expected") or {}).get("finalGrade", "")
        got = (r.get("got") or {}).get("finalGrade", "") if r.get("got") else ""
        if not exp or not got or exp == got:     # empty 산출 등 등급 비교 불가 건은 상세에서 제외
            continue
        detail.append({"hash": r.get("hash"), "title": r.get("title") or "",
                       "expected": exp, "got": got,
                       "judge": jm.get(r.get("hash")) or {"adopt": 0, "reject": 0, "reviewers": {}}})
    out["detail"] = detail
    return out
