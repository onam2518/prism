"""저장된 평가의 사실성·정보 보존 진단. 명시적 실행만 유료 호출을 수행한다."""
import copy
import json
import threading
import time

from . import accuracy_eval as A, context_eval as C, evaluation as E
from .eval_contract import field_status

_SV = None
_LOCK = threading.Lock()
_ACTIVE = {}
_CANCEL = set()
VERSION = "grounded-review-2026-10-28-v1"


def selector(data):
    kind = data.get("kind")
    if kind not in ("eval", "compare", "pilot"):
        raise ValueError("평가 종류가 올바르지 않습니다")
    if kind == "compare":
        key = str(data.get("key") or "")
        if not key.startswith("model_compare_v") or not key[len("model_compare_v"):].isdigit():
            raise ValueError("저장된 모델 비교 키가 필요합니다")
        return {"kind": kind, "key": key}
    rid, rnd = int(data.get("id") or 0), int(data.get("round") or 0)
    if rid < 1 or kind == "pilot" and rnd < 1:
        raise ValueError("평가 번호가 올바르지 않습니다")
    return {"kind": kind, "id": rid, "round": rnd}


def _key(sel):
    return "grounded_eval_" + E.digest(sel)


def _history(sel, team):
    from . import evalops
    return evalops.eval_history_detail(sel["kind"], sel.get("id", 0), sel.get("round", 0), sel.get("key", ""), team)


def get(data, team=None):
    try:
        sel = selector(data)
        history = _history(sel, team)
        if not history.get("ok"):
            return history
        st = _SV.get_store()
        value = st.get_report(_key(sel), team=team)
        if not value:
            return {"ok": True, "status": "not_started", "available": any(i.get("source") for i in history.get("items", []))}
        value = copy.deepcopy(value)
        with _LOCK:
            active = _ACTIVE.get((str(team or ""), _key(sel)))
            value["stalled"] = value["status"] == "running" and not (active and active.is_alive())
        value.pop("items", None)  # 원문은 기존 평가 상세에 표시하며 진행 조회에 중복 전송하지 않는다.
        return {"ok": True, **value}
    except (ValueError, TypeError):
        return {"ok": False, "error": "평가 지정을 확인해 주세요"}


def start(data, team=None):
    from . import execution as EX
    from .config import Config
    try:
        sel = selector(data)
        history = _history(sel, team)
        if not history.get("ok"):
            return history
        if history.get("status") not in ("done", "applied", "reverted") or history.get("detail_mode") != "all":
            raise ValueError("완료한 평가와 전체 저장 결과가 필요합니다")
        items = [{"hash": i["hash"], "source": i.get("source"), "expected": i.get("expected") or {},
                  "got": i.get("got") or {}} for i in history.get("items", [])]
        items = copy.deepcopy(items)
        if not items or not any(i.get("source") for i in items):
            raise ValueError("평가 당시 원문이 저장되지 않았습니다. 새 평가를 실행해 주세요")
        model = str(data.get("model") or Config.load().model).strip()
        llm, _ = _SV.llm_for_model(model, _SV.Handler.server_mock)
        if llm is None or llm.mock:
            raise ValueError("실제로 연결할 수 있는 평가 모델을 선택해 주세요")
        llm = EX.clone_client(llm)
        llm.cfg.retry.max_retries = 0
        llm.cfg.retry.max_format_retries = 0
        llm.max_output_tokens = 4096
        settings = EX.client_settings(llm)
        basis = E.digest({"items": items, "judge": settings, "version": VERSION,
                          "fact_system": C.FACT_REVIEW_SYSTEM, "coverage_system": A.COVERAGE_SYSTEM})
        key, identity = _key(sel), (str(team or ""), _key(sel))
        st = _SV.get_store()
        with _LOCK:
            if identity in _ACTIVE and _ACTIVE[identity].is_alive():
                raise ValueError("이 평가는 실행 중입니다")
            old = st.get_report(key, team=team)
            if old and old["basis"] != basis:
                raise ValueError("평가 원문·모델·방식이 이전과 다릅니다. 같은 조건으로 재개하세요")
            if old and old["status"] == "done":
                return {"ok": True, "status": "done"}
            state = old or {"version": VERSION, "basis": basis, "selector": sel, "judge_model": model,
                           "judge_settings": settings, "max_output_tokens": 4096,
                           "fact_system": C.FACT_REVIEW_SYSTEM, "coverage_system": A.COVERAGE_SYSTEM,
                           "items": items, "records": {}, "attempts": [], "created_at": time.time(),
                           "cost_usd": 0.0, "cost_complete": True, "input_tokens": 0, "output_tokens": 0}
            state.update(status="running", error="", total=len(items), completed=0)
            st.save_report(key, state, team=team)
            _CANCEL.discard(identity)
            th = threading.Thread(target=_worker, args=(identity, key, team, state, llm), daemon=True)
            _ACTIVE[identity] = th
            th.start()
        return {"ok": True, "status": "running", "total": len(items), "judge_model": model}
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}


def cancel(data, team=None):
    try:
        sel = selector(data)
        history = _history(sel, team)
        if not history.get("ok"):
            return history
        identity = (str(team or ""), _key(sel))
        with _LOCK:
            if identity not in _ACTIVE:
                return {"ok": False, "error": "실행 중인 진단이 없습니다"}
            _CANCEL.add(identity)
        return {"ok": True}
    except (ValueError, TypeError):
        return {"ok": False, "error": "평가의 지정을 확인해주세요"}


def _worker(identity, key, team, state, llm):
    st = _SV.get_store()
    def save():
        st.save_report(key, state, team=team)

    def ask(slot, system, user, validate, model=None):
        previous = state["records"].get(slot)
        if previous and previous.get("ok"):
            return previous
        if identity in _CANCEL:
            raise InterruptedError()
        record = {"slot": slot, "model": model, "document_id": slot.split(":")[0],
                  "stage": slot.split(":")[-1], "ok": False, "request_hash": E.digest([system, user])}
        try:
            obj, raw = llm.complete_json(system, json.dumps(user, ensure_ascii=False), tag="evaluation_grounding")
            record.update(input_tokens=raw.in_tok, output_tokens=raw.out_tok, cost_usd=raw.cost_usd,
                          text=raw.text, retries=raw.retries)
            state["input_tokens"] += raw.in_tok
            state["output_tokens"] += raw.out_tok
            if raw.cost_usd is None:
                state["cost_complete"] = False
            else:
                state["cost_usd"] += raw.cost_usd
            response = raw.raw or {}
            record["response_model"] = response.get("model")
            choice = (response.get("choices") or [{}])[0]
            if raw.fail_kind or choice.get("finish_reason") not in (None, "stop"):
                raise ValueError("incomplete judge output")
            record.update(review=validate(obj), ok=True)
        except Exception as exc:
            record["error"] = type(exc).__name__
        state["attempts"].append(record)
        state["records"][slot] = record
        save()
        return record

    try:
        if identity in _CANCEL:
            raise InterruptedError()
        models = sorted({m for i in state["items"] for m in i["got"]})
        state.setdefault("semantic_reports", {})
        for model in models:
            cached = state["semantic_reports"].get(model)
            if not cached or "summary_semantic_cosine" in E.readiness(cached):
                if identity in _CANCEL:
                    raise InterruptedError()
                state["semantic_reports"][model] = E.report(state["items"], model, embed=E.semantic_embed)
                save()
        state["reports"] = copy.deepcopy(state["semantic_reports"])
        state["source_missing_n"] = sum(not item.get("source") for item in state["items"])
        for index, item in enumerate(state["items"]):
            if identity in _CANCEL:
                raise InterruptedError()
            if not item.get("source"):
                state["completed"] = index + 1
                continue
            src = item["source"].get("title", "") + "\n" + item["source"].get("body", "")
            expected = item["expected"]
            reference = None
            if field_status(expected, "summary") == "scored" and expected.get("summary"):
                ref_text = expected["summary"]
                reference = ask(item["hash"] + ":reference", C.FACT_REVIEW_SYSTEM,
                    {"source_units": C.source_units(src), "candidate": ref_text},
                    lambda o: C.validate_fact_review(src, ref_text, o))
            for model in (models if index % 2 == 0 else list(reversed(models))):
                got = item["got"].get(model)
                candidate = (got or {}).get("summary") or ""
                values = state["reports"][model]["documents"][index]["scores"]
                values.update(judge_support_rate=None, reference_claim_coverage=None)
                if not candidate.strip():
                    if reference and reference.get("ok") and A.reference_claims(reference["review"]):
                        values["reference_claim_coverage"] = 0.0
                    continue
                slot = item["hash"] + ":" + E.digest(model)
                factual = ask(slot + ":fact", C.FACT_REVIEW_SYSTEM,
                    {"source_units": C.source_units(src), "candidate": candidate},
                    lambda o: C.validate_fact_review(src, candidate, o), model)
                if factual["ok"]:
                    for metric in ("support_rate", "contradiction_rate", "unverifiable_rate"):
                        values["judge_" + metric] = factual["review"][metric]
                if reference and reference.get("ok"):
                    claims = A.reference_claims(reference["review"])
                    values["reference_excluded_claims"] = len(reference["review"]["claims"]) - len(claims)
                    if claims:
                        coverage = ask(slot + ":coverage", A.COVERAGE_SYSTEM,
                            {"reference_claims": claims, "candidate_units": C.source_units(candidate)},
                            lambda o: A.validate_coverage(claims, candidate, o), model)
                        if coverage["ok"]:
                            values["reference_claim_coverage"] = coverage["review"]["coverage"]
            state["completed"] = index + 1
            for report in state["reports"].values():
                report["metrics"] = E.aggregate(report["documents"])
            save()
        state["paired_comparisons"] = [{"a": a, "b": b, **E.paired(state["reports"][a], state["reports"][b])}
                                        for i, a in enumerate(models) for b in models[i + 1:]]
        state["status"] = "partial" if (any(not r["ok"] for r in state["records"].values()) or
            any("summary_semantic_cosine" in E.readiness(r) for r in state["reports"].values()) or
            state["source_missing_n"]) else "done"
    except InterruptedError:
        state["status"] = "cancelled"
    except Exception as exc:
        state.update(status="failed", error=type(exc).__name__)
    finally:
        state["finished_at"] = time.time()
        try:
            save()
        finally:
            with _LOCK:
                _ACTIVE.pop(identity, None)
                _CANCEL.discard(identity)
