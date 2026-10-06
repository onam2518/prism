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
            "lat": [], "yellow": 0, "auto_n": 0, "auto_hit": 0, "per_reason": {}, "per_service": {},
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
    from .learnops import _eval_values
    m["n"] += 1
    exp = row.get("expected") or {}
    abtest.intent_tally(m, exp, out)             # 인텐트 계수는 abtest.score 와 단일 소스
    ME.meta_tally(m, exp, out)                   # 카테고리·엔티티·리드문(같은 카운터 dict · abtest.score 와 단일 소스)
    if exp.get("finalGrade") == "R":             # 유해 미탐률 분모(산출 실패 행도 포함)
        m["harm_n"] = int(m.get("harm_n") or 0) + 1   # 구 런 재개 시 키가 없다 → get 으로 시작
    has_grade = exp.get("finalGrade") in ("G", "R")
    m["grade_n"] = m.get("grade_n", m["n"] - 1) + int(has_grade)
    if out is None:
        m["empty"] += 1
        return {"expected": dict(exp),
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
    if not has_grade:
        return {"expected": dict(exp), "got": _eval_values(out),
                "passed": None, "error": ""}
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
    # 서비스별 카운터는 구 런 메트릭에 없다 → setdefault 로 시작(재개 시 KeyError 방지)
    sd = m.setdefault("per_service", {}).setdefault(abtest.service_key(row), {"n": 0, "grade_ok": 0})
    sd["n"] += 1
    sd["grade_ok"] += int(grade_ok)
    return {"expected": dict(exp), "got": _eval_values(out),
            "passed": bool(grade_ok), "error": ""}


def _percentile(vals: list, p: float):
    if not vals:
        return None
    s = sorted(vals)
    i = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
    return round(float(s[i]), 1)


def _prepare(team, model: str, scope: str, resolve_model=True):
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
    if not resolve_model:
        return rows[:MAX_ROWS], None, "", ""
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


def _eval_basis(rows, model, team):
    import hashlib
    from . import learnops as LO
    prompts = LO.compose_prompts(team, model)
    prompts.pop("ts", None)
    snapshot = {"rows": rows, "prompts": prompts, "config": Config.load().redacted()}
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), default=str).encode()).hexdigest()


def eval_run_start(team=None, model: str = "", scope: str = "all", created_by: str = "",
                   policy_version: str = "", protocol=None) -> dict:
    """평가 런 생성 + 백그라운드 실행 시작. 즉시 {id,total} 반환(진행은 폴링)."""
    st = _SV.get_store()
    rows, llm, used_model, err = _prepare(team, model, scope, resolve_model=not policy_version)
    if rows is None:
        return {"ok": False, "error": err}
    if not hasattr(st, "eval_run_create"):
        return {"ok": False, "error": "스토어가 평가 런을 지원하지 않습니다"}
    from . import execution as EX
    import copy
    rows = copy.deepcopy(rows)
    policy = None
    try:
        if policy_version:
            from .dnm import training_fields
            policy = (st.get_report("dnm_control", team=team) or {}).get("policy")
            if not policy or policy["policy_version"] != policy_version:
                raise ValueError("선택한 DNM 정책이 현재 설정과 다릅니다")
            checked = []
            for row in rows:
                expected = row.get("expected") or {}
                fields = training_fields(row.get("content") or {}, expected, policy_version)
                if fields:
                    from .meta_contract import FIELDS
                    row["expected"] = dict({k: v for k, v in expected.items() if k not in (*FIELDS, "finalGrade", "reasons")}, **fields)
                    checked.append(row)
            rows = checked
            if not rows:
                raise ValueError("현재 입력·정책에 확정된 DNM 정답이 없습니다")
            if protocol is not None:
                _validate_protocol(protocol, len(rows))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        if policy:
            llm = EX.restore(policy["execution"], lambda mid: _SV.llm_for_model(mid, _SV.Handler.server_mock)[0])
            llm.dnm_policy = policy
            execution = policy["execution"]
            used_model = llm.model
        else:
            llm, execution = EX.capture(llm, rows)
    except Exception as exc:
        return {"ok": False, "error": "실행 명세 고정 실패: " + str(exc)[:200]}
    run_id = st.eval_run_create(team, used_model, scope, len(rows), created_by=created_by or "")
    snapshot = {"rows": rows, "execution": execution, "model": used_model, "scope": scope}
    if policy:
        snapshot.update(policy_version=policy_version, dnm_policy=policy, protocol=protocol)
    metrics = _zero_metrics()
    metrics["basis_fingerprint"] = EX.digest(snapshot)
    metrics["summary_sim_method"] = execution["scoring"]["summary"]
    try:
        st.save_report("eval_snapshot_" + str(run_id), snapshot, team=team)
        st.eval_run_update(run_id, team=team, metrics=metrics)
        st.save_report("eval_prompts_" + str(run_id),
                       EX.prompt_record(execution, run_id, time.time()), team=team)
    except Exception as exc:
        st.eval_run_update(run_id, team=team, status="failed", error="실행 스냅샷 저장 실패")
        return {"ok": False, "error": "실행 스냅샷 저장 실패: " + str(exc)[:200]}
    _launch(run_id, rows, llm, team, metrics)
    return {"ok": True, "id": run_id, "total": len(rows)}


def eval_run_resume(run_id: int, team=None) -> dict:
    """중단(서버 재시작·실패)된 런 재개: 저장된 건별 결과를 빼고 남은 건만 실행.
    시작 시 저장한 정답·프롬프트·모델·설정으로 재개하며 현재 정답셋을 다시 읽지 않는다."""
    st = _SV.get_store()
    run = st.eval_run_get(run_id, team) if hasattr(st, "eval_run_get") else None
    if not run:
        return {"ok": False, "error": "런을 찾을 수 없습니다"}
    with _LOCK:
        if run_id in _ACTIVE and _ACTIVE[run_id].is_alive():
            return {"ok": False, "error": "이미 실행 중입니다"}
    if run.get("status") not in ("running", "failed"):
        return {"ok": False, "error": "재개할 수 없는 상태입니다: " + str(run.get("status"))}
    from . import execution as EX
    snapshot = st.get_report("eval_snapshot_" + str(run_id), team=team)
    basis = (run.get("metrics") or {}).get("basis_fingerprint")
    if not isinstance(snapshot, dict) or not basis or basis != EX.digest(snapshot):
        return {"ok": False, "error": "고정 실행 스냅샷이 없거나 손상됐습니다. 새 평가를 시작하세요"}
    try:
        llm = EX.restore(snapshot["execution"],
                         lambda mid: _SV.llm_for_model(mid, _SV.Handler.server_mock)[0]
                         if mid else _SV.make_text_llm(Config.load(), _SV.Handler.server_mock))
        rows = snapshot["rows"]
        if snapshot.get("dnm_policy"):
            llm.dnm_policy = snapshot["dnm_policy"]
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:250]}
    from .store import golden_hash
    done = st.eval_result_hashes(run_id, team)
    remain = [r for r in rows if golden_hash(r) not in done]
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
    from .store import golden_hash
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
                r.update({"hash": golden_hash(row), "title": (c.get("title") or "")[:60]})
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


class PilotStop(Exception):
    """수동 중지 요청 · 학습 배치의 진척 콜백(청크 경계)에서 던져 라운드를 안에서 끊는다."""
# ponytail: 라운드 안 진척은 프로세스 메모리 · 재시작하면 런도 같이 죽으므로 영속 불필요
_PILOT_PROG: dict = {}          # run_id → {round, phase, done, total, seen, ts}
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
                              golden_hashes=frozen, model=model)
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
    # 요청 사실을 즉시 남긴다 · 라운드(수십 분) 안에서는 진척 콜백이 다음 청크 경계에서 끊고,
    # 그 전까지 화면은 이 문구로 '요청됨' 을 보인다(종전엔 라운드가 끝날 때까지 아무 표시가 없었다)
    st.autopilot_update(rid, team=team, stop_reason="중지 요청됨 · 진행 중인 단계를 다음 청크 경계에서 멈춥니다")
    return {"ok": True, "id": rid, "requested": True}


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
        if run["stalled"]:                        # 스레드가 없는 running = 서버 재시작(배포)으로 끊긴 런 → 끊긴 라운드부터 재개
            try:
                res = _pilot_resume(dict(run, team=team))
                run["status"], run["stop_reason"] = res["status"], res["stop_reason"]
                run["stalled"] = res["status"] != "running"
            except Exception:
                pass
        if run.get("status") == "running":
            run["progress"] = _PILOT_PROG.get(run["id"])
        run["golden_n"] = len(run.pop("golden_hashes", None) or [])   # 화면엔 건수만(해시 목록은 응답에서 뺀다)
    return {"ok": True, "run": run}


_ROUND_KEYS = ("n", "grade_accuracy", "reason_jaccard", "harm_miss_rate", "empty_rate", "meta_hold_rate",
               "intent_n", "intent_hit", "intent_f1", "cat_n", "cat_hf1", "ent_n", "ent_f1", "summary_n", "summary_sim",
               "cost_usd", "latency_p50_ms", "latency_p95_ms")


def _meta_gate() -> float:
    return float(getattr(Config.load().thresholds, "meta_gate", 0.6) or 0.6)


PILOT_MAX_RESUMES = 3                             # 재시작마다 자동 재개 · 같은 런이 계속 죽으면(장애 반복) 이 횟수 뒤 중단


def _pilot_resume(run: dict) -> dict:
    """서버 재시작(배포)으로 스레드를 잃은 running 런을 끊긴 라운드부터 이어서 실행한다.
    완료된 라운드는 history 에 영속돼 있어 최고·정체 판정을 그대로 복원한다. 끊긴 라운드의 미검증 보정은
    프로세스와 함께 사라졌으므로 그 라운드를 처음부터 다시 돈다."""
    st = _SV.get_store()
    rid, team = run["id"], run.get("team")
    with _LOCK:
        if rid in _PILOT_ACTIVE and _PILOT_ACTIVE[rid].is_alive():
            return {"status": "running", "stop_reason": run.get("stop_reason") or ""}
        resumes = int(run.get("resumes") or 0)
        history = list(run.get("history") or [])
        if resumes >= PILOT_MAX_RESUMES:
            reason = f"서버 재시작이 {resumes}회 반복돼 중단 · 장애를 확인한 뒤 다시 시작하세요"
            st.autopilot_update(rid, team=team, status="stopped", stop_reason=reason, finished=time.time())
            return {"status": "stopped", "stop_reason": reason}
        reason = f"서버 재시작 후 라운드 {len(history) + 1}부터 이어서 실행({resumes + 1}회째 재개)"
        st.autopilot_update(rid, team=team, resumes=resumes + 1, stop_reason=reason, heartbeat=time.time())
        th = threading.Thread(target=_pilot_loop,
                              args=(rid, team, float(run.get("target") or 0.9), int(run.get("max_rounds") or 1),
                                    run.get("model") or "", float(run.get("meta_target") or _meta_gate()),
                                    run.get("golden_hashes") or None),
                              kwargs={"history": history}, name=f"prism-autopilot-{rid}", daemon=True)
        _PILOT_ACTIVE[rid] = th
    th.start()
    print(f"  [autopilot] #{rid} {reason}")
    return {"status": "running", "stop_reason": reason}


def autopilot_resume_all() -> list:
    """서버 시작 시: 직전 프로세스에서 돌던 오토파일럿을 모두 재개(팀 무관)."""
    st = _SV.get_store()
    if not (st and hasattr(st, "autopilot_running")):
        return []
    out = []
    for run in st.autopilot_running():
        try:
            out.append(dict(_pilot_resume(run), id=run["id"]))
        except Exception as e:
            print(f"  [autopilot] #{run.get('id')} 재개 실패: {e}")
    return out


def _pilot_loop(rid: int, team, target: float, max_rounds: int, model: str = "", meta_target: float = 0.6,
                golden_hashes=None, history=None):
    """라운드 반복: learning_batch → 정확도 추적 → 종료 조건 판정. 이력은 라운드마다 영속.
    향상 판정 = 종합 점수 상승 AND 등급 신뢰구간이 최고 라운드와 안 겹침(ci_overlap) ·
    두 구간이 겹치면 점 추정치가 올라도 '동등'으로 보고 향상 없음으로 집계한다(표본 노이즈 방지).
    n(evaluated) 을 모르는 라운드는 CI 판단이 불가하므로 종전처럼 종합 점수만으로 판정한다."""
    from . import learnops as LO
    st = _SV.get_store()
    history = list(history or [])                    # 재개: 완료 라운드 이력 · 새 런: 빈 목록
    best = None          # 최고 등급 일치율(화면 표시용)
    best_score = None    # 최고 종합 점수(정체 판정용 · 목표 판정과 같은 5축)
    best_acc, best_acc_n = None, None   # 최고 라운드의 등급 일치율·표본 n(CI 동등 판정용)
    no_improve = 0
    stall_rounds = int(getattr(Config.load().thresholds, "pilot_stall_rounds", PILOT_STALL_ROUNDS)
                       or PILOT_STALL_ROUNDS)

    def _judge(acc, overall, n_cur):
        """라운드 결과를 최고 기록과 비교해 향상 여부 판정 · 상태 갱신(새 라운드·재개 복원 공용)."""
        nonlocal best, best_score, best_acc, best_acc_n, no_improve
        best = acc if best is None else max(best, acc)
        up = best_score is None or overall > best_score + 1e-9   # 정체는 종합 점수로(등급 한 축 아님)
        # 종합이 올라도 등급 CI 가 최고 라운드와 겹치면(표본 노이즈로 동등) 향상으로 안 친다.
        # n(evaluated) 을 모르는(구 리포트·페이크) 라운드는 CI 판단 불가 → 종전처럼 종합 점수만으로 판정.
        if up and best_score is not None and n_cur and best_acc_n \
                and LO.ci_overlap(acc, n_cur, best_acc, best_acc_n):
            up = False
        if up:
            best_score, best_acc, best_acc_n = overall, acc, n_cur
        no_improve = 0 if up else no_improve + 1
        return up

    for h in history:                                 # 재개: 완료 라운드로 최고·정체 상태 복원
        hm = h.get("metrics") or {}
        if h.get("accuracy") is not None and hm.get("overall") is not None:
            _judge(float(h["accuracy"]), float(hm["overall"]), int(hm.get("n") or 0))
    try:
        if history and no_improve >= stall_rounds:    # 끊기기 직전 라운드에서 이미 정체 조건 충족
            st.autopilot_update(rid, team=team, status="done",
                                stop_reason=f"개선 정체 · {stall_rounds}라운드 연속 종합 향상 없음(재개 시 확인)",
                                finished=time.time())
            return
        for rnd in range(len(history) + 1, max_rounds + 1):
            if rid in _PILOT_STOP:
                st.autopilot_update(rid, team=team, status="stopped",
                                    stop_reason=f"수동 중지(라운드 {rnd - 1} 완료)",
                                    finished=time.time())
                return
            st.autopilot_update(rid, team=team, round=rnd, heartbeat=time.time())

            def _prog(phase, done=0, total=0, _r=rnd):   # 화면 진척(단계·건수) · seen = 이 라운드에 실제로 돈 단계
                if rid in _PILOT_STOP and phase != "wrap":   # wrap(스냅샷·회차 기록)은 끊지 않는다 · 반영된 보정에 버전이 붙어야 한다
                    raise PilotStop()
                p = _PILOT_PROG.get(rid) or {}
                seen = list(p.get("seen") or []) if p.get("round") == _r else []
                if phase not in seen:
                    seen.append(phase)
                _PILOT_PROG[rid] = {"round": _r, "phase": phase, "done": done, "total": total,
                                    "seen": seen, "ts": time.time()}
            try:
                rep = LO.learning_batch(team, model=model, golden_hashes=golden_hashes, progress=_prog)
                if rep.get("skipped"):          # 다른 호출자와 배치 겹침 · 잠깐 대기 후 한 번만 재시도
                    time.sleep(2)
                    rep = LO.learning_batch(team, model=model, golden_hashes=golden_hashes, progress=_prog)
            except PilotStop:                   # 라운드 안 중단 · learning_batch 가 미검증 보정을 되돌린 뒤 올라온다
                st.autopilot_update(rid, team=team, status="stopped",
                                    stop_reason=f"수동 중지 · 라운드 {rnd} 진행 중 중단(이 라운드 보정은 반영하지 않음)",
                                    history=history, finished=time.time())
                return
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
            _SV._report_save(f"pilot_eval_{rid}_{rnd}", ev, team)
            history.append({"round": rnd, "ts": time.time(), "accuracy": acc, "pre": pre, "model": model, "metrics": metrics,
                            "delta": rep.get("improve_delta"), "reverted": reverted,
                            "version": int((rep.get("prompt_snapshot") or {}).get("version") or 0)})
            n_cur = int(ev.get("evaluated") or 0)
            metrics["n"] = n_cur or metrics.get("n")   # 재개 시 CI 판정 복원용 표본 n
            _judge(acc, ov["overall"], n_cur)
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
        _PILOT_PROG.pop(rid, None)


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
    from .store import golden_hash
    try:
        run = st.eval_run_get(run_id, team) or {}
        basis = (run.get("metrics") or {}).get("basis_fingerprint")
        if basis:
            from . import execution as EX
            snapshot = st.get_report("eval_snapshot_" + str(run_id), team=team)
            if not isinstance(snapshot, dict) or EX.digest(snapshot) != basis:
                raise ValueError("고정 실행 스냅샷이 없거나 손상됐습니다")
            rows = snapshot["rows"]
        else:
            rows = st.get_golden(team) or []
        gmap = {}
        for g in rows:
            gmap[golden_hash(g)] = g.get("content") or {}
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
            n = m.get("grade_n", m.get("n")) or 0
            it["grade_accuracy"] = round(m.get("grade_hit", 0) / n, 4) if n else None
            it["kind"] = "eval"
    # 오토파일럿 라운드 · 모델별 비교도 '평가' 라 같은 이력에 합류(읽기 시 파생 · 저장 구조 무변경) · kind 로 구분
    # id 는 화면 키라 종류 간 안 겹치게 접두어 · 라운드 시각은 구 라운드(ts 없음)면 런 시각으로 대신
    for run in (st.autopilot_list(team, limit=10) if hasattr(st, "autopilot_list") else []):
        n_gold = len(run.get("golden_hashes") or [])
        for hh in run.get("history") or []:
            items.append({"kind": "pilot", "id": f"p{run['id']}-{hh.get('round')}", "pilot_id": run["id"],
                          "round": hh.get("round"), "ts": hh.get("ts") or run.get("ts"), "model": hh.get("model") or "",
                          "golden_n": n_gold, "status": "reverted" if hh.get("reverted") else "applied",
                          "grade_accuracy": hh.get("accuracy"), "version": hh.get("version") or 0})
    from . import learnops as LO
    for it in (LO.compare_history(team).get("items") or []):
        items.append({"kind": "compare", "id": "c" + str(it.get("key")), "key": it.get("key"), "ts": it.get("ts"),
                      "model": ", ".join(it.get("models") or []), "best": it.get("best") or "",
                      "scope": it.get("scope") or "all", "golden_n": it.get("golden_n"), "status": "done",
                      "grade_accuracy": it.get("grade_accuracy"), "version": it.get("prompt_snapshot_version") or 0})
    items.sort(key=lambda x: -(x.get("ts") or 0))
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
           "grade_n": m.get("grade_n", n),
           "grade_accuracy": round(m.get("grade_hit", 0) / m.get("grade_n", n), 4) if m.get("grade_n", n) else None,
           "reason_exact_match": round(m.get("reason_exact", 0) / m.get("grade_n", n), 4) if m.get("grade_n", n) else None,
           "reason_jaccard": round(m.get("jaccard_sum", 0.0) / m.get("grade_n", n), 4) if m.get("grade_n", n) else None,
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
           "by_service": abtest.service_report(m.get("per_service") or {}),
           "min_good": int(getattr(cfg, "golden_min_good", 1) or 1)}
    lo, hi = Q.binomial_ci(out["grade_accuracy"] or 0.0, m.get("grade_n", n))
    out["grade_ci"] = {"lo": lo, "hi": hi, "n": m.get("grade_n", n)}
    prompt = _SV._report_get("eval_prompts_" + str(run_id), team) or {}
    out["basis"] = {"model": run.get("model") or "", "version": prompt.get("version"),
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


def _history_expected(items, legacy=False, recovery=None):
    """저장된 기대값만으로 채점 가능 여부를 설명한다. 현재 정답·모델 산출은 섞지 않는다."""
    from . import abtest
    fields = (("intent", "intent_n"), ("content_category", "cat_n"),
              ("entities", "ent_n"), ("summary", "sum_pairs"))
    coverage = {key: 0 for key, _ in fields}
    annotated = []
    for item in items:
        exp = item.get("expected") or {}
        unrecorded = legacy or set(exp).issubset({"grade", "reasons"})
        counts, statuses = {}, {}
        if not unrecorded:
            # 실제 채점과 같은 정규화·재확정·no_value 규칙을 사용한다. 모델 호출 없음.
            abtest.intent_tally(counts, exp, None)
            ME.meta_tally(counts, exp, None)
        for key, counter in fields:
            if unrecorded:
                status = "unrecorded"
            elif key == "intent" and exp.get("intent_review") == "needed":
                status = "pending"
            elif counts.get(counter):
                status = "scored"
                coverage[key] += 1
            elif (exp.get("meta_status") or {}).get(key) not in (None, "success", "no_value"):
                status = "excluded"
            elif exp.get(key) is None:
                status = "missing"
            elif not exp.get(key):
                status = "empty"
            else:
                status = "excluded"
            statuses[key] = status
        recovered = ((recovery or {}).get("items") or {}).get(item.get("hash")) or {}
        values = {k: v for k, v in (recovered.get("values") or {}).items()
                  if k in dict(fields) and k not in exp}
        annotated.append({**item, "expected_status": statuses,
                          "expected_recovery": {"values": values, "id": (recovery or {}).get("id"),
                                                "at": (recovery or {}).get("at")} if values else None})
    return {"items": annotated, "expected_coverage": {"total": len(items), "fields": coverage,
            "legacy": legacy or bool(items) and all(i["expected_status"]["intent"] == "unrecorded" for i in annotated)}}


def eval_history_detail(kind, run_id=0, round_no=0, key="", team=None):
    """세 종류의 저장된 평가를 같은 모델 열·건별 행 계약으로 읽는다. 모델 호출 없음."""
    from . import learnops as LO
    st = _SV.get_store()
    def with_recovery(items, legacy=False):
        recovery = _SV._agg_cached(("golden_meta_recovery", team),
                                  lambda: st.get_report("golden_meta_recovery", team) or {})
        return _history_expected(items, legacy=legacy, recovery=recovery)
    missing = {"ok": False, "error": "평가 기록을 찾을 수 없습니다"}
    if kind == "compare":
        if not key:
            return missing
        rep = LO.last_model_compare(team, key)
        if not rep.get("ok"):
            return missing
        return {**rep, "kind": kind, "id": "c" + key, "status": "done",
                **with_recovery(rep.get("items") or []),
                "version": rep.get("prompt_snapshot_version"), "detail_mode": "all"}
    if kind == "eval":
        rep = eval_run_report(run_id, team)
        if not rep.get("ok"):
            return missing
        model = rep["basis"]["model"] or "기록된 모델"
        judges = {d["hash"]: d for d in rep.get("detail") or []}
        items = []
        for row in st.eval_results_list(run_id, team, limit=MAX_ROWS):
            exp, got = row.get("expected") or {}, row.get("got")
            grade = (got or {}).get("finalGrade", "")
            passed = (grade == exp["finalGrade"]) if exp.get("finalGrade") else None
            items.append({"hash": row["hash"], "title": row.get("title") or "",
                          "expected": {**exp, "grade": exp.get("finalGrade", "")},
                          "got": {model: {**(got or {}), "grade": grade, "ok": passed,
                                          "empty": got is None, "error": row.get("error") or "",
                                          "rubric": row.get("rubric")}},
                          "all_ok": passed, "split": False, "judgment": judges.get(row["hash"])})
        metrics = {k: v for k, v in rep.items() if k != "detail"}
        return {**rep, "kind": kind, "models": [{**metrics, "model": model, "n": rep["evaluated"]}],
                **with_recovery(items), "detail_mode": "all", "scope": rep["basis"]["scope"],
                "version": rep["basis"]["version"]}
    if kind != "pilot" or not st or not hasattr(st, "autopilot_get"):
        return missing
    run = st.autopilot_get(run_id, team)
    hh = next((h for h in (run or {}).get("history") or [] if h.get("round") == round_no), None)
    if not hh:
        return missing
    ev = _SV._report_get(f"pilot_eval_{run_id}_{round_no}", team)
    if not ev and hh.get("version"):
        old = _SV._report_get(f"learn_report_v{hh['version']}", team) or {}
        if (old.get("prompt_snapshot") or {}).get("version") == hh["version"]:
            ev = old.get("eval")
    ev = ev or {}
    model = (ev.get("basis") or {}).get("model") or hh.get("model") or "기록된 모델"
    metrics = {"grade_accuracy": hh.get("accuracy"), **(hh.get("metrics") or {}), **ev, "model": model}
    metrics.pop("items", None)
    metrics.pop("detail", None)
    items = ev.get("items") or []
    mode = "all" if "items" in ev else "mismatches" if ev.get("detail") else "none"
    if mode == "mismatches":
        items = [{"hash": d["hash"], "title": d.get("title") or "",
                  "expected": {"grade": d.get("expected")},
                  "got": {model: {"grade": d.get("got"), "ok": False, "empty": False}},
                  "all_ok": False, "split": False} for d in ev["detail"]]
    # 단일 모델 결과는 저장 당시 모델명을 사용한다(현재 기본 모델과 무관).
    if items and len(items[0].get("got") or {}) == 1:
        model = next(iter(items[0]["got"]))
        metrics["model"] = model
    return {"ok": True, "kind": kind, "id": f"p{run_id}-{round_no}", "pilot_id": run_id,
            "round": round_no, "ts": hh.get("ts") or run.get("ts"), "version": hh.get("version"),
            "status": "reverted" if hh.get("reverted") else "applied", "scope": "fixed",
            "total": len(run.get("golden_hashes") or []), "models": [metrics],
            **with_recovery(items, legacy=mode != "all"),
            "detail_mode": mode, "pre": hh.get("pre"), "delta": hh.get("delta")}


def _validate_protocol(protocol, size):
    import math
    required = {"intent_f1", "cat_hf1", "ent_f1", "summary_sim", "cost_usd", "latency_p95_ms", "empty_rate"}
    if not isinstance(protocol, dict) or protocol.get("sample_size") != size:
        raise ValueError("평가 전에 확정한 표본 수와 실제 정답 수가 다릅니다")
    targets, tolerances = protocol.get("targets") or {}, protocol.get("tolerances") or {}
    if (not isinstance(targets, dict) or not isinstance(tolerances, dict)
            or not required.issubset(targets) or set(targets) != set(tolerances)):
        raise ValueError("4종 품질·실패율·비용·지연의 목표와 허용 오차를 모두 지정하세요")
    for key, target in targets.items():
        tolerance = tolerances[key]
        if (not isinstance(target, dict) or not target or set(target) - {"min", "max"}
                or type(tolerance) not in (int, float) or not math.isfinite(tolerance) or tolerance < 0
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in target.values())):
            raise ValueError("평가 목표와 허용 오차가 유효한 수치여야 합니다")
        if "min" in target and "max" in target and target["min"] > target["max"]:
            raise ValueError("평가 목표 최솟값은 최댓값보다 클 수 없습니다")
