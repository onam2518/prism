"""저장된 평가의 사실성·정보 보존 진단. 명시적 실행만 유료 호출을 수행한다."""
import copy
import json
import threading
import time
import uuid

from . import accuracy_eval as A, context_eval as C, evaluation as E
from .eval_contract import field_status

_SV = None
_LOCK = threading.Lock()
_ACTIVE = {}
_CANCEL = set()
VERSION = "grounded-review-2026-10-28-v1"
LEASE_SECONDS = 180


class _BudgetStop(BaseException):
    """Do not let semantic_pairs turn a requested stop into a missing metric."""


def selector(data):
    if not isinstance(data, dict):
        raise ValueError("평가 지정을 확인해 주세요")
    kind = data.get("kind")
    if kind == "evaluation":
        rid = data.get("id")
        if not isinstance(rid, str) or not rid or len(rid) > 100:
            raise ValueError("평가 실행 ID가 필요합니다")
        return {"kind": kind, "id": rid}
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


def _settle_stale_cancel(state):
    state = copy.deepcopy(state)
    for attempt in state.get('attempts', []):
        if attempt.get('status') == 'running':
            attempt.update(status='uncertain', error='이전 호출 완료 여부 미확인')
            state['cost_complete'] = False
    state.update(status='cancelled', owner='', lease=0, finished_at=time.time())
    return state


def _history(sel, team):
    if sel['kind'] == 'evaluation':
        from . import evalservice
        result = evalservice.get_run(sel['id'], team)
        if not result.get('ok'):
            return result
        run = result['run']
        return {**run, 'ok': True, 'status': 'done' if run['status'] == 'completed' else run['status'],
                'detail_mode': 'all'}
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
            value["stalled"] = value["status"] in ("running", "cancel_requested") and (
                value.get('lease', 0) <= time.time() if value.get('owner') else not (active and active.is_alive()))
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
        environment = (history.get('snapshot') or {}).get('environment')
        if environment is not None:
            if not isinstance(environment, dict) or environment.get('diagnostic_version') != VERSION:
                raise ValueError('저장된 진단 환경 버전을 사용할 수 없습니다')
            if (not isinstance(environment.get('judge_settings'), dict) or
                    not isinstance(environment.get('judge_model'), str) or not environment['judge_model'] or
                    environment['judge_settings'].get('model') != environment['judge_model'] or
                    not all(isinstance(environment.get(k), str) and environment[k] for k in ('fact_system', 'coverage_system')) or
                    type(environment.get('max_output_tokens')) is not int or environment['max_output_tokens'] <= 0 or
                    not isinstance(environment.get('limits'), dict)):
                raise ValueError('저장된 진단 환경이 올바르지 않습니다')
        key, identity = _key(sel), (str(team or ""), _key(sel))
        st = _SV.get_store()
        with _LOCK:
            if identity in _ACTIVE and _ACTIVE[identity].is_alive():
                raise ValueError("이 평가는 실행 중입니다")
            old = st.get_report(key, team=team)
            environment_limit = (environment or {}).get('limits', {}).get('max_calls')
            run_limit = ((history.get('snapshot') or {}).get('limits') or {}).get('max_calls')
            default_limit = run_limit if run_limit is not None else environment_limit if environment_limit is not None else 1000
            max_calls = data.get('max_calls', (old or {}).get('max_calls', default_limit))
            if type(max_calls) is not int or not 1 <= max_calls <= 100000:
                raise ValueError('호출 한도는 1~100000 사이 정수여야 합니다')
            if environment is not None and (type(environment_limit) is not int or not 1 <= environment_limit <= 100000):
                raise ValueError('저장된 진단 환경의 호출 한도가 올바르지 않습니다')
            if old and old.get('lease', 0) > time.time() and old.get('status') in ('running', 'cancel_requested'):
                raise ValueError('이 진단은 실행 중입니다')
            if old and old.get('status') == 'cancel_requested':
                cancelled = _settle_stale_cancel(old)
                if not st.compare_report(key, old, cancelled, team=team):
                    raise ValueError('진단 상태가 바뀌었습니다. 다시 확인해 주세요')
                return {'ok': False, 'status': 'cancelled', 'error': '중단 요청이 완료되었습니다. 재개하려면 다시 시작하세요'}
            saved_settings = (old or {}).get('judge_settings') or (environment or {}).get('judge_settings')
            saved_model = (old or {}).get('judge_model') or (environment or {}).get('judge_model')
            if old and environment is not None and (
                    old.get('judge_settings') != environment['judge_settings'] or
                    old.get('judge_model') != environment['judge_model'] or
                    old.get('fact_system') != environment['fact_system'] or
                    old.get('coverage_system') != environment['coverage_system'] or
                    old.get('max_output_tokens') != environment['max_output_tokens']):
                raise ValueError('저장된 진단과 평가 환경이 다릅니다')
            requested_model = str(data.get('model') or '').strip()
            if saved_model and requested_model and requested_model != saved_model:
                raise ValueError('재개 시 저장된 판정 모델을 변경할 수 없습니다')
            model = saved_model or requested_model or Config.load().model
            llm, _ = _SV.llm_for_model(model, _SV.Handler.server_mock)
            if llm is None or llm.mock:
                raise ValueError('실제로 연결할 수 있는 평가 모델을 선택해 주세요')
            if saved_settings:
                old_url = (saved_settings.get('config') or {}).get('chat_url')
                current_url = getattr(llm.cfg, 'chat_url', '')
                if old_url is not None and current_url != old_url:
                    raise ValueError('저장된 판정 모델 연결 주소가 바뀌었습니다')
            llm = EX.clone_client(llm, saved_settings)
            llm.cfg.retry.max_retries = 0
            llm.cfg.retry.max_format_retries = 0
            llm.max_output_tokens = (old or {}).get('max_output_tokens', (environment or {}).get('max_output_tokens', 4096))
            settings = EX.client_settings(llm)
            fact_system = (old or {}).get('fact_system', (environment or {}).get('fact_system', C.FACT_REVIEW_SYSTEM))
            coverage_system = (old or {}).get('coverage_system', (environment or {}).get('coverage_system', A.COVERAGE_SYSTEM))
            basis = E.digest({'items': items, 'judge': settings, 'version': (old or {}).get('version', VERSION),
                              'fact_system': fact_system, 'coverage_system': coverage_system})
            if old and old["basis"] != basis:
                raise ValueError("평가 원문·모델·방식이 이전과 다릅니다. 같은 조건으로 재개하세요")
            if old and old.get('max_calls', max_calls) != max_calls:
                raise ValueError('재개 시 저장된 호출 한도를 변경할 수 없습니다')
            if old and old["status"] == "done":
                return {"ok": True, "status": "done"}
            state = copy.deepcopy(old) if old else {"version": VERSION, "basis": basis, "selector": sel, "judge_model": model,
                           "judge_settings": settings, "max_output_tokens": llm.max_output_tokens,
                           "fact_system": fact_system, "coverage_system": coverage_system,
                           "items": items, "records": {}, "attempts": [], "created_at": time.time(),
                           "cost_usd": 0.0, "cost_complete": True, "input_tokens": 0, "output_tokens": 0}
            if len(state['attempts']) >= max_calls:
                raise ValueError('저장된 진단의 호출 한도를 모두 사용했습니다')
            for attempt in state['attempts']:
                if attempt.get('status') == 'running':
                    attempt.update(status='uncertain', error='이전 호출 완료 여부 미확인')
                    state['cost_complete'] = False
            state.update(status="running", error="", total=len(items), completed=0,
                         max_calls=max_calls, owner=uuid.uuid4().hex, lease=time.time()+LEASE_SECONDS)
            if not st.compare_report(key, old, state, team=team):
                raise ValueError('동시에 진단이 시작되었습니다. 다시 확인해 주세요')
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
        st = _SV.get_store()
        with _LOCK:
            old = st.get_report(_key(sel), team=team)
            if not old or old.get('status') not in ('running', 'cancel_requested'):
                return {"ok": False, "error": "실행 중인 진단이 없습니다"}
            live = old.get('lease', 0) > time.time() or identity in _ACTIVE
            state = copy.deepcopy(old) if live else _settle_stale_cancel(old)
            if live:
                state['status'] = 'cancel_requested'
            if not st.compare_report(_key(sel), old, state, team=team):
                return {'ok': False, 'error': '상태가 바뀌었습니다. 다시 중단해 주세요'}
            _CANCEL.add(identity)
        return {"ok": True, 'status': state['status']}
    except (ValueError, TypeError):
        return {"ok": False, "error": "평가의 지정을 확인해주세요"}


def _worker(identity, key, team, state, llm):
    st = _SV.get_store()
    heartbeat_stop = threading.Event()

    def heartbeat():
        try:
            while not heartbeat_stop.wait(max(.01, LEASE_SECONDS / 3)):
                for _ in range(100):
                    current = st.get_report(key, team=team)
                    if not current or current.get('owner') != state.get('owner') or current.get('status') not in ('running', 'cancel_requested'):
                        return
                    renewed = copy.deepcopy(current)
                    renewed['lease'] = time.time() + LEASE_SECONDS
                    if st.compare_report(key, current, renewed, team=team):
                        break
        finally:
            if hasattr(st, 'close_thread_connection'):
                st.close_thread_connection()

    timer = None
    if state.get('owner'):
        timer = threading.Thread(target=heartbeat, daemon=True)
        timer.start()

    def save():
        for _ in range(100):
            previous = st.get_report(key, team=team)
            if previous and previous.get('owner') != state.get('owner'):
                raise RuntimeError('진단 실행 소유권이 변경됐습니다')
            if previous and previous.get('status') in ('cancel_requested', 'cancelled'):
                # A request may arrive between the final read and this CAS.
                state['status'] = ('cancel_requested' if previous['status'] == 'cancel_requested' and state['status'] in ('running', 'cancel_requested')
                                   else 'cancelled')
            if state.get('owner'):
                state['lease'] = time.time()+LEASE_SECONDS if state['status'] in ('running', 'cancel_requested') else 0
            if st.compare_report(key, previous, state, team=team):
                return
        raise RuntimeError('진단 저장 경합')

    def check():
        current = st.get_report(key, team=team)
        if identity in _CANCEL or current and current.get('status') in ('cancel_requested', 'cancelled'):
            raise _BudgetStop('cancelled')
        if len(state['attempts']) >= state.get('max_calls', 100000):
            raise _BudgetStop('limit_reached')

    def embed(texts):
        vectors = []
        for offset in range(0, len(texts), 64):
            check()
            record = {'stage': 'embedding', 'ok': False, 'status': 'running', 'cost_usd': None,
                      'request_hash': E.digest(texts[offset:offset+64])}
            state['attempts'].append(record)
            state['cost_complete'] = False  # The embedding helper does not return provider usage.
            save()
            try:
                vectors.extend(E.semantic_embed(texts[offset:offset+64]))
                record.update(ok=True, status='done')
            except Exception as exc:
                record.update(status='failed', error=type(exc).__name__)
                raise
            finally:
                save()
        return vectors

    def ask(slot, system, user, validate, model=None):
        previous = state["records"].get(slot)
        if previous and previous.get("ok"):
            return previous
        check()
        record = {"slot": slot, "model": model, "document_id": slot.split(":")[0],
                  "stage": slot.split(":")[-1], "ok": False, "status": "running",
                  "request_hash": E.digest([system, user])}
        state['attempts'].append(record)
        save()
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
            if 'cost_usd' not in record:
                state['cost_complete'] = False
        record['status'] = 'done' if record['ok'] else 'failed'
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
                state["semantic_reports"][model] = E.report(state["items"], model, embed=embed)
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
                reference = ask(item["hash"] + ":reference", state.get('fact_system', C.FACT_REVIEW_SYSTEM),
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
                factual = ask(slot + ":fact", state.get('fact_system', C.FACT_REVIEW_SYSTEM),
                    {"source_units": C.source_units(src), "candidate": candidate},
                    lambda o: C.validate_fact_review(src, candidate, o), model)
                if factual["ok"]:
                    for metric in ("support_rate", "contradiction_rate", "unverifiable_rate"):
                        values["judge_" + metric] = factual["review"][metric]
                if reference and reference.get("ok"):
                    claims = A.reference_claims(reference["review"])
                    values["reference_excluded_claims"] = len(reference["review"]["claims"]) - len(claims)
                    if claims:
                        coverage = ask(slot + ":coverage", state.get('coverage_system', A.COVERAGE_SYSTEM),
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
        current = st.get_report(key, team=team)
        if identity in _CANCEL or current and current.get('status') == 'cancel_requested':
            state['status'] = 'cancelled'
    except _BudgetStop as exc:
        state['status'] = str(exc)
    except InterruptedError:
        state["status"] = "cancelled"
    except Exception as exc:
        state.update(status="failed", error=type(exc).__name__)
    finally:
        heartbeat_stop.set()
        if timer:
            timer.join()
        state["finished_at"] = time.time()
        try:
            save()
        finally:
            with _LOCK:
                _ACTIVE.pop(identity, None)
                _CANCEL.discard(identity)
            if hasattr(st, 'close_thread_connection'):
                st.close_thread_connection()
