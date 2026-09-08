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
import json
import threading
import time

from .config import Config
from . import metaeval as ME

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입

_ACTIVE: dict = {}              # run_id → Thread (이 프로세스가 실행 중인 런)
_CANCEL: set = set()            # 중단 요청된 run_id
_LOCK = threading.Lock()
CHUNK = 24                      # 청크당 건수(concurrency 8 의 3배 · 커서 갱신 주기)
MAX_ROWS = 1000                 # 런당 평가 상한(get_golden 상한과 동일)

_RUBRIC_ACTIVE: dict = {}       # run_id → Thread (루브릭 채점 스레드)
_RUBRIC_CANCEL: set = set()
RUBRIC_CHUNK = 10               # 저지 1회 호출당 배치 케이스 수(Atelier result-judge 패턴)
RUBRIC_AXES = ("accuracy", "format", "policy", "conciseness")


def _zero_metrics() -> dict:
    """abtest.score 와 같은 계수 규칙의 증분 카운터(런 행에 누적 저장)."""
    return {"n": 0, "grade_hit": 0, "reason_exact": 0, "jaccard_sum": 0.0,
            # harm_n = 기대 R 행 수(유해 미탐률의 분모). 구 런 메트릭에는 없어서
            # 리포트 쪽이 '키 부재 = 구 정의' 로 갈라 읽는다(하위호환).
            "harm_miss": 0, "harm_n": 0, "empty": 0, "cost_usd": 0.0, "tok_in": 0, "tok_out": 0,
            "lat": [], "yellow": 0, "auto_n": 0, "auto_hit": 0, "per_reason": {},
            # 인텐트 카운터(abtest.intent_tally 와 같은 키) · 구 런 메트릭에는 없으므로
            # 읽는 쪽은 항상 .get 기본값으로 다룬다(재개·구 런 리포트 하위호환).
            "intent_n": 0, "intent_exact": 0, "intent_jac_sum": 0.0,
            "intent_top1": 0, "intent_skipped": 0, "per_intent": {},
            # 카테고리·엔티티·리드문 카운터(ME.meta_tally 와 같은 키) · 구 런 메트릭에는
            # 없으므로(ME.meta_tally/meta_report 는 항상 .get 기본값으로 다뤄 하위호환).
            "cat_n": 0, "cat_f1_sum": 0.0, "cat_hf1_sum": 0.0, "cat_exact": 0, "per_cat": {},
            "ent_n": 0, "ent_f1_sum": 0.0, "ent_pf1_sum": 0.0,
            "sum_n": 0, "sum_sim_sum": 0.0, "sum_low": 0}


def _tally(m: dict, row: dict, out) -> dict:
    """건 1개를 카운터에 반영하고 저장용 건별 결과 행을 반환.
    계수 규칙은 abtest.score 와 동일(실패 산출도 등급 채점에 포함 · empty 비배타)."""
    from . import abtest
    m["n"] += 1
    exp = row.get("expected") or {}
    abtest.intent_tally(m, exp, out)             # 인텐트 계수는 abtest.score 와 단일 소스
    ME.meta_tally(m, exp, out)                   # 카테고리·엔티티·리드문(같은 카운터 dict · abtest.score 와 단일 소스)
    if exp.get("finalGrade") == "R":             # 유해 미탐률 분모(산출 실패 행도 포함)
        m["harm_n"] = int(m.get("harm_n") or 0) + 1   # 구 런 재개 시 키가 없다 → get 으로 시작
    want_intent = abtest.intent_expected(exp)
    if out is None:
        m["empty"] += 1
        return {"expected": {"finalGrade": exp.get("finalGrade", ""),
                             "reasons": exp.get("reasons", []) or [],
                             "intent": want_intent},
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
    im = out.get("item_meta")
    summary = (im.get("summary") if isinstance(im, dict)
               else getattr(im, "summary", "")) or ""
    # 인텐트는 기대·산출 양쪽에 대칭으로 싣는다(루브릭 저지 accuracy 축의 판단 근거 ·
    # 정렬하지 않음 = 대표값 첫 번째 순서를 보존).
    return {"expected": {"finalGrade": exp.get("finalGrade", ""),
                         "reasons": sorted(want), "intent": want_intent},
            "got": {"finalGrade": qm.get("finalGrade", ""), "reasons": sorted(got),
                    "intent": abtest.intent_got(out),
                    "summary": str(summary)[:200]},   # 루브릭 저지의 '실제 응답' 원천
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


def eval_run_compare(a_id: int, b_id: int, team=None) -> dict:
    """두 런 비교(a=기준 · b=대상 · 보통 b 가 나중 버전). 회귀 판정은 학습 일배치의
    가드(learnops._batch_regressions)와 단일 소스 — 수동 프롬프트 변경에도 같은
    conservative acceptance(점수 낮아지면 채택 안 함 · Atelier 원칙) 기준을 적용한다."""
    ra = eval_run_report(a_id, team)
    rb = eval_run_report(b_id, team)
    if not ra.get("ok"):
        return {"ok": False, "error": f"기준 런(#{a_id})을 찾을 수 없습니다"}
    if not rb.get("ok"):
        return {"ok": False, "error": f"대상 런(#{b_id})을 찾을 수 없습니다"}
    if ra.get("status") != "done" or rb.get("status") != "done":
        return {"ok": False, "error": "완주한 런끼리만 비교할 수 있습니다"}
    from . import learnops as LO
    regressions = LO._batch_regressions(ra, rb, grade_drop=LO._regress_grade_drop())
    if regressions:                              # 부분 개선이라도 회귀 지점이 있으면 보류(보수 채택)
        verdict = "regressed"
    elif (rb.get("grade_accuracy") or 0) > (ra.get("grade_accuracy") or 0) + 1e-9:
        verdict = "improved"
    else:
        verdict = "even"
    return {"ok": True, "a": ra, "b": rb, "regressions": regressions, "verdict": verdict}


# ── 오토파일럿(자동 개선 루프 · Atelier autopilot 이식) ─────────────────────
# 한 라운드 = learnops.learning_batch(피드백→프롬프트 보정→같은 정답셋 재평가 ·
# 악화 시 자동 원복). 오토파일럿은 그 라운드를 목표 달성까지 반복하는 상태머신:
# 종료 = 목표 달성 · 개선 정체(2라운드 연속 향상 없음) · 최대 라운드 · 수동 중지.
_PILOT_ACTIVE: dict = {}        # run_id → Thread
_PILOT_STOP: set = set()
PILOT_ROUNDS_CAP = 10           # 폭주 방지 상한(Atelier max_versions cap 상응)
PILOT_STALL_ROUNDS = 2          # 연속 무향상 허용 라운드(초과 시 정체 종료)


def autopilot_start(team=None, target=0.9, max_rounds=5, created_by="", model: str = "",
                    meta_target=None) -> dict:
    """meta_target: 아이템 메타(인텐트·카테고리·엔티티·리드문) 일치율 목표 · 비면 설정 meta_gate."""
    st = _SV.get_store()
    if not (st and hasattr(st, "autopilot_create")):
        return {"ok": False, "error": "스토어가 오토파일럿을 지원하지 않습니다"}
    try:
        target = float(target)
        max_rounds = int(max_rounds)
        meta_target = float(meta_target) if meta_target not in (None, "") else _meta_gate()
    except (TypeError, ValueError):
        return {"ok": False, "error": "목표·라운드 값이 올바르지 않습니다"}
    if not (0.5 <= meta_target <= 1.0):
        return {"ok": False, "error": "메타 일치율 목표는 50~100% 사이여야 합니다"}
    if not (0.5 <= target <= 1.0):
        return {"ok": False, "error": "목표 일치율은 50~100% 사이여야 합니다"}
    max_rounds = max(1, min(PILOT_ROUNDS_CAP, max_rounds))
    if not (hasattr(st, "golden_count") and st.golden_count(team)):
        return {"ok": False, "error": "정답셋이 없습니다 · 검수 합의 또는 수동 등록으로 먼저 쌓으세요"}
    latest = st.autopilot_latest(team)
    if latest and latest.get("status") == "running":
        with _LOCK:
            alive = latest["id"] in _PILOT_ACTIVE and _PILOT_ACTIVE[latest["id"]].is_alive()
        if alive:
            return {"ok": False, "error": "이미 오토파일럿이 실행 중입니다"}
        st.autopilot_update(latest["id"], team=team, status="stopped",
                            stop_reason="서버 재시작으로 중단", finished=time.time())
    model = (model or "").strip()
    if model:                                        # 원하는 모델로 라운드 평가 · 호출 불가면 시작 전에 막는다
        llm, route = _SV.llm_for_model(model, _SV.Handler.server_mock)
        if llm is None:
            return {"ok": False, "error": f"모델 호출 불가({route}): {model}"}
    frozen = sorted(st.golden_hashes(team))       # 라운드마다 정답셋이 늘면 최고/정체 비교가 다른 셋끼리가 된다 → 시작 셋으로 고정
    rid = st.autopilot_create(team, target, max_rounds, created_by=created_by or "", meta_target=meta_target,
                              golden_hashes=frozen)
    th = threading.Thread(target=_pilot_loop, args=(rid, team, target, max_rounds, model, meta_target, frozen),
                          name=f"prism-autopilot-{rid}", daemon=True)
    with _LOCK:
        _PILOT_ACTIVE[rid] = th
    th.start()
    return {"ok": True, "id": rid, "target": target, "max_rounds": max_rounds, "model": model,
            "golden_n": len(frozen)}


def autopilot_stop(team=None) -> dict:
    """실행 중 오토파일럿 중지 요청(라운드 경계에서 멈춤 · 반영된 라운드는 유지)."""
    st = _SV.get_store()
    latest = st.autopilot_latest(team) if (st and hasattr(st, "autopilot_latest")) else None
    if not latest or latest.get("status") != "running":
        return {"ok": False, "error": "실행 중인 오토파일럿이 없습니다"}
    rid = latest["id"]
    with _LOCK:
        alive = rid in _PILOT_ACTIVE and _PILOT_ACTIVE[rid].is_alive()
    if not alive:                                # 재시작 유실 → 상태만 정리
        st.autopilot_update(rid, team=team, status="stopped",
                            stop_reason="서버 재시작으로 중단", finished=time.time())
        return {"ok": True, "id": rid}
    _PILOT_STOP.add(rid)
    return {"ok": True, "id": rid}


def autopilot_status(team=None) -> dict:
    st = _SV.get_store()
    if not (st and hasattr(st, "autopilot_latest")):
        return {"ok": True, "run": None}
    run = st.autopilot_latest(team)
    if run:
        with _LOCK:
            run["stalled"] = bool(run.get("status") == "running"
                                  and not (run["id"] in _PILOT_ACTIVE
                                           and _PILOT_ACTIVE[run["id"]].is_alive()))
        if run["stalled"]:                        # 스레드가 없는 running = 서버 재시작(배포)으로 죽은 런 → 기록으로 정리해 화면을 풀어 준다
            try:
                st.autopilot_update(run["id"], team=team, status="stopped",
                                    stop_reason="서버 재시작으로 중단 · 다시 시작하세요", finished=time.time())
                run["status"], run["stop_reason"] = "stopped", "서버 재시작으로 중단 · 다시 시작하세요"
            except Exception:
                pass
        run["golden_n"] = len(run.pop("golden_hashes", None) or [])   # 화면엔 건수만(해시 목록은 응답에서 뺀다)
    return {"ok": True, "run": run}


_ROUND_KEYS = ("grade_accuracy", "reason_jaccard", "harm_miss_rate", "empty_rate", "meta_hold_rate",
               "intent_n", "intent_f1", "cat_n", "cat_hf1", "ent_n", "ent_f1", "summary_n", "summary_sim",
               "cost_usd", "latency_p50_ms", "latency_p95_ms")


def _meta_gate() -> float:
    return float(getattr(Config.load().thresholds, "meta_gate", 0.6) or 0.6)


def _pilot_loop(rid: int, team, target: float, max_rounds: int, model: str = "", meta_target: float = 0.6,
                golden_hashes=None):
    """라운드 반복: learning_batch → 정확도 추적 → 종료 조건 판정. 이력은 라운드마다 영속.
    향상 판정 = 종합 점수 상승 AND 등급 신뢰구간이 최고 라운드와 안 겹침(ci_overlap) ·
    두 구간이 겹치면 점 추정치가 올라도 '동등'으로 보고 향상 없음으로 집계한다(표본 노이즈 방지).
    n(evaluated) 을 모르는 라운드는 CI 판단이 불가하므로 종전처럼 종합 점수만으로 판정한다."""
    from . import learnops as LO
    st = _SV.get_store()
    history = []
    best = None          # 최고 등급 일치율(화면 표시용)
    best_score = None    # 최고 종합 점수(정체 판정용 · 목표 판정과 같은 5축)
    best_acc, best_acc_n = None, None   # 최고 라운드의 등급 일치율·표본 n(CI 동등 판정용)
    no_improve = 0
    stall_rounds = int(getattr(Config.load().thresholds, "pilot_stall_rounds", PILOT_STALL_ROUNDS)
                       or PILOT_STALL_ROUNDS)
    try:
        for rnd in range(1, max_rounds + 1):
            if rid in _PILOT_STOP:
                st.autopilot_update(rid, team=team, status="stopped",
                                    stop_reason=f"수동 중지(라운드 {rnd - 1} 완료)",
                                    finished=time.time())
                return
            st.autopilot_update(rid, team=team, round=rnd, heartbeat=time.time())
            rep = LO.learning_batch(team, model=model, golden_hashes=golden_hashes)
            if rep.get("skipped"):          # 다른 호출자와 배치 겹침 · 잠깐 대기 후 한 번만 재시도
                time.sleep(2)
                rep = LO.learning_batch(team, model=model, golden_hashes=golden_hashes)
            acc = rep.get("grade_accuracy")
            if acc is None:
                st.autopilot_update(rid, team=team, status="failed",
                                    error="라운드 평가 불가 · 정답셋·모델 설정을 확인하세요",
                                    history=history, finished=time.time())
                return
            empty = float(((rep.get("eval") or {}).get("empty_rate")) or 0)
            if empty >= 0.9:                        # 호출이 거의 다 실패한 라운드는 '정체'가 아니라 장애(한도·키·모델명)
                st.autopilot_update(rid, team=team, status="failed",
                                    error=f"모델 호출 실패 · 빈 결과 {empty:.0%} · "
                                          f"{model or '기본 텍스트 슬롯'} 의 API 키·지출 한도·모델명을 확인하세요",
                                    history=history, finished=time.time())
                return
            pre = (rep.get("eval_pre") or {}).get("grade_accuracy")
            reverted = bool((rep.get("improve") or {}).get("reverted"))
            # 모델별 비교와 같은 평가 항목(종합·게이트·메타 F1·비용·속도)을 라운드마다 남긴다
            ev = rep.get("eval") or {"grade_accuracy": acc}
            ov = ME.overall(ev, target, meta_target)
            metrics = {k: ev.get(k) for k in _ROUND_KEYS}
            metrics.update(ov)
            history.append({"round": rnd, "accuracy": acc, "pre": pre, "model": model, "metrics": metrics,
                            "delta": rep.get("improve_delta"), "reverted": reverted,
                            "version": int((rep.get("prompt_snapshot") or {}).get("version") or 0)})
            best = acc if best is None else max(best, acc)
            n_cur = int(ev.get("evaluated") or 0)
            overall_up = best_score is None or ov["overall"] > best_score + 1e-9   # 정체는 종합 점수로(등급 한 축 아님)
            # 종합이 올라도 등급 CI 가 최고 라운드와 겹치면(표본 노이즈로 동등) 향상으로 안 친다.
            # n(evaluated) 을 모르는(구 리포트·페이크) 라운드는 CI 판단 불가 → 종전처럼 종합 점수만으로 판정.
            if overall_up and best_score is not None and n_cur and best_acc_n \
                    and LO.ci_overlap(acc, n_cur, best_acc, best_acc_n):
                overall_up = False
            improved = overall_up
            if improved:
                best_score, best_acc, best_acc_n = ov["overall"], acc, n_cur
            fields = {"last_accuracy": acc, "best_accuracy": best,
                      "history": history, "heartbeat": time.time()}
            if rnd == 1:
                fields["start_accuracy"] = pre if pre is not None else acc
            st.autopilot_update(rid, team=team, **fields)
            if acc >= target - 1e-9 and ov["passed"]:   # 목표 = 등급 일치율 + 메타 목표 전부 통과(비교표와 같은 두 손잡이)
                st.autopilot_update(rid, team=team, status="done",
                                    stop_reason=f"목표 달성 · 일치율 {acc:.0%} ≥ 목표 {target:.0%} · 메타 {meta_target:.0%} 전부 통과 · 종합 {ov['overall']:.0%}",
                                    finished=time.time())
                return
            no_improve = 0 if improved else no_improve + 1
            if no_improve >= stall_rounds:
                st.autopilot_update(rid, team=team, status="done",
                                    stop_reason=f"개선 정체 · {stall_rounds}라운드 연속 종합 향상 없음"
                                                 f"(등급 신뢰구간이 최고 라운드와 겹치는 동등 라운드는 향상 없음으로 집계 · "
                                                 f"최고 종합 {best_score:.0%} · 일치율 {best:.0%})",
                                    finished=time.time())
                return
        st.autopilot_update(rid, team=team, status="done",
                            stop_reason=f"최대 라운드({max_rounds}) 도달 · 최고 {best:.0%}",
                            finished=time.time())
    except Exception as e:
        try:
            st.autopilot_update(rid, team=team, status="failed", error=str(e)[:300],
                                history=history, finished=time.time())
        except Exception:
            pass
        print(f"  [autopilot] #{rid} 실패: {e}")
    finally:
        with _LOCK:
            _PILOT_ACTIVE.pop(rid, None)
        _PILOT_STOP.discard(rid)


# ── 루브릭 저지(4축 · Atelier rubric-judge 이식) ────────────────────────────
def _clamp15(v) -> int:
    """1~5 정수로 클램프(비수치는 중립 3). Atelier clamp 와 동일 규칙."""
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return 3
    return max(1, min(5, n))


def _judge_batch(llm, items: list) -> dict:
    """케이스 배치를 저지 1회 호출로 채점 → {id: {축…, note}}. 파싱 실패 시 빈 dict(재시도 대상).
    프롬프트는 Atelier rubric-judge 이식 · '실제'는 프리즘 추출 산출(등급·사유·요약)."""
    cases = "\n\n".join(
        f"[{i + 1}] id={it['id']}\n입력: {it['input'][:500]}\n기대: {it['expected'][:400]}\n실제: {it['actual'][:500]}"
        for i, it in enumerate(items))
    system = ("당신은 LLM 출력 채점 심사관입니다. 각 케이스의 실제 응답(콘텐츠 메타 추출 산출: "
              "등급·사유·인텐트·요약)을 네 가지 축으로 1~5점으로 매깁니다. 반드시 JSON 만 출력하세요.")
    user = f"""## 채점 축 (각 1~5, 높을수록 좋음)

- accuracy(정확성): 기대 정답(등급·사유·인텐트)과 의미가 일치하는가. 정답이면 5, 완전히 틀리면 1.
- format(형식): 요구된 산출 구조(등급 라벨·사유 코드·요약)를 지켰는가. 빈 값·형식 붕괴는 감점.
- policy(정책): 유해(R) 콘텐츠를 놓치지 않았는가. R 정답을 G 로 판정했으면 크게 감점.
- conciseness(간결성): 요약이 불필요한 췌언 없이 필요한 만큼만 담겼는가.

## 채점 대상

{cases}

---

## 출력 형식 (반드시 이 JSON 만)

{{"scores": [{{"id": "<id 그대로>", "accuracy": 5, "format": 4, "policy": 5, "conciseness": 4, "note": "한 줄 사유"}}]}}

모든 케이스를 채점하세요. JSON 만 출력하고 다른 설명은 하지 마세요."""
    data, _res = llm.complete_json(system, user, tag="rubric")
    out = {}
    for r in (data.get("scores") or []):
        rid = str(r.get("id") or "")
        if not rid:
            continue
        sc = {a: _clamp15(r.get(a)) for a in RUBRIC_AXES}
        sc["note"] = str(r.get("note") or "")[:200]
        out[rid] = sc
    return out


def rubric_start(run_id: int, team=None) -> dict:
    """완주(done)한 런의 건별 결과를 4축 루브릭으로 채점(백그라운드 배치).
    이미 채점된 건은 건너뛴다(재실행 = 남은 건만 · 실패 후 재개와 동일 경로)."""
    st = _SV.get_store()
    run = st.eval_run_get(run_id, team) if (st and hasattr(st, "eval_run_get")) else None
    if not run:
        return {"ok": False, "error": "런을 찾을 수 없습니다"}
    if run.get("status") != "done":
        return {"ok": False, "error": "완주한 런만 루브릭 채점이 가능합니다"}
    with _LOCK:
        if run_id in _RUBRIC_ACTIVE and _RUBRIC_ACTIVE[run_id].is_alive():
            return {"ok": False, "error": "이미 채점 중입니다"}
    cfg = Config.load()
    llm = _SV.make_text_llm(cfg, _SV.Handler.server_mock)
    pending = st.eval_results_missing_rubric(run_id, team)
    if not pending:
        return {"ok": False, "error": "채점할 건이 없습니다(전건 채점 완료)"}
    st.eval_run_update(run_id, team=team, rubric_status="running", error="")
    th = threading.Thread(target=_rubric_loop, args=(run_id, pending, llm, team),
                          name=f"prism-eval-rubric-{run_id}", daemon=True)
    with _LOCK:
        _RUBRIC_ACTIVE[run_id] = th
    th.start()
    return {"ok": True, "id": run_id, "pending": len(pending)}


def rubric_cancel(run_id: int, team=None) -> dict:
    with _LOCK:
        alive = run_id in _RUBRIC_ACTIVE and _RUBRIC_ACTIVE[run_id].is_alive()
    if not alive:
        return {"ok": False, "error": "채점 중이 아닙니다"}
    _RUBRIC_CANCEL.add(run_id)
    return {"ok": True, "id": run_id}


def _rubric_loop(run_id: int, pending: list, llm, team):
    """RUBRIC_CHUNK 배치로 저지 호출 → 건별 rubric 저장 → 커서 갱신 → 완료 시 축별 평균 집계.
    저지 호출·파싱 실패는 배치 단위로 건너뛰고 연속 3회면 failed(남은 건은 재실행으로)."""
    st = _SV.get_store()
    from .store import content_hash
    try:
        gmap = {}
        for g in (st.get_golden(team) or []):
            gmap[content_hash(g.get("content") or {})] = g.get("content") or {}
        scored = fails = 0
        for i in range(0, len(pending), RUBRIC_CHUNK):
            if run_id in _RUBRIC_CANCEL:
                st.eval_run_update(run_id, team=team, rubric_status="cancelled")
                return
            batch = pending[i:i + RUBRIC_CHUNK]
            items = []
            for r in batch:
                c = gmap.get(r.get("hash"))
                if c is None:                    # 골든에서 빠진 건(그사이 삭제) → 채점 불가 표기
                    st.eval_result_rubric_set(run_id, r.get("hash"), {"skipped": True}, team)
                    continue
                items.append({"id": r.get("hash"),
                              "input": ((c.get("title") or "") + "\n" + (c.get("body") or "")).strip(),
                              "expected": json.dumps(r.get("expected") or {}, ensure_ascii=False),
                              "actual": json.dumps(r.get("got") or {}, ensure_ascii=False)})
            if items:
                try:
                    scores = _judge_batch(llm, items)
                except Exception as e:
                    scores = {}
                    print(f"  [eval-rubric] #{run_id} 배치 실패(건너뜀): {e}")
                if not scores:
                    fails += 1
                    if fails >= 3:               # 연속 실패 = 모델·키 문제 개연 → 명시 종료
                        st.eval_run_update(run_id, team=team, rubric_status="failed",
                                           error="루브릭 저지 연속 실패 · 모델 설정 확인 후 재실행")
                        return
                else:
                    fails = 0
                for it in items:
                    sc = scores.get(it["id"])
                    if sc:
                        st.eval_result_rubric_set(run_id, it["id"], sc, team)
                        scored += 1
            st.eval_run_update(run_id, team=team, rubric_cursor=scored)
        agg = {a: 0.0 for a in RUBRIC_AXES}
        n = 0
        for r in st.eval_results_list(run_id, team, limit=2000):
            rb = r.get("rubric") or {}
            if not rb or rb.get("skipped"):
                continue
            n += 1
            for a in RUBRIC_AXES:
                agg[a] += rb.get(a) or 0
        if n == 0 and pending:                    # 전 배치 실패 = 채점 0건 → done 으로 위장하지 않는다
            st.eval_run_update(run_id, team=team, rubric_status="failed",
                               error="루브릭 저지 응답 파싱 실패 · 모델 설정 확인 후 재실행")
            return
        rubric = {a: round(agg[a] / n, 2) for a in RUBRIC_AXES} if n else {}
        rubric["n"] = n
        st.eval_run_update(run_id, team=team, rubric_status="done", rubric=rubric)
    except Exception as e:
        try:
            st.eval_run_update(run_id, team=team, rubric_status="failed", error=str(e)[:300])
        except Exception:
            pass
        print(f"  [eval-rubric] #{run_id} 실패: {e}")
    finally:
        with _LOCK:
            _RUBRIC_ACTIVE.pop(run_id, None)
        _RUBRIC_CANCEL.discard(run_id)


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
    from . import abtest
    from . import quality as Q
    # 유해 미탐률 분모 전환(2026-08-11): 신규 런은 '기대 R 행 수'(=1-recall(R)),
    # harm_n 키가 없는 **구 런은 계산 불가**라 종전 정의(전체 행 대비)를 그대로 보여 준다.
    # 어느 정의로 계산된 값인지는 harm_miss_basis 로 함께 내려보낸다(과거 값과 섞이므로 필수).
    harm_miss = m.get("harm_miss", 0)
    harm_n = m.get("harm_n")
    if harm_n is None:
        harm_rate, harm_basis = (round(harm_miss / n, 4) if n else 0), "all_rows"
    else:
        harm_rate, harm_basis = (round(harm_miss / harm_n, 4) if harm_n else None), "expected_r"
    out = {**abtest.intent_report(m),             # abtest.score 와 같은 인텐트 키(순수 추가)
           **ME.meta_report(m),                   # abtest.score 와 같은 카테고리·엔티티·리드문 키(순수 추가)
           "ok": True, "id": run_id, "status": run.get("status"),
           "cursor": run.get("cursor") or 0, "total": run.get("total") or 0,
           "ts": run.get("ts"), "finished": run.get("finished"),
           "run_error": run.get("error") or "",
           "rubric_status": run.get("rubric_status") or "",
           "rubric_cursor": run.get("rubric_cursor") or 0,
           "rubric": run.get("rubric"),
           "evaluated": n,
           "grade_accuracy": round(m.get("grade_hit", 0) / n, 4) if n else 0,
           "reason_exact_match": round(m.get("reason_exact", 0) / n, 4) if n else 0,
           "reason_jaccard": round(m.get("jaccard_sum", 0.0) / n, 4) if n else 0,
           "harm_miss_rate": harm_rate,
           "harm_miss_share": round(harm_miss / n, 4) if n else 0,   # 종전 정의 병기
           "harm_expected_n": int(harm_n or 0),
           "harm_miss_basis": harm_basis,
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
        rb = r.get("rubric") or {}
        detail.append({"hash": r.get("hash"), "title": r.get("title") or "",
                       "expected": exp, "got": got,
                       "rubric_note": (rb.get("note") or "") if not rb.get("skipped") else "",
                       "judge": jm.get(r.get("hash")) or {"adopt": 0, "reject": 0, "reviewers": {}}})
    out["detail"] = detail
    return out
