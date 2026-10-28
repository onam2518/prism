"""Isolated, bounded candidate search against a frozen evaluation snapshot.

The caller supplies adapters; every paid call must go through Budget.call.  No
adapter is installed by default, so exposing an unfinished integration is unsafe.

The development/holdout separation follows the design of Spectrum PR #1's
recursive.py (head 5c847b39608142a0a7c4160ac6698a0a18c5d94a); this is a
Prism-specific implementation, not a vendored recursive controller.
"""
import copy
import json
import threading
import time
import uuid

from . import execution
from . import evaluation
from .store import golden_hash

_LOCK = threading.RLock()
_ACTIVE = set()
_SV = None


def _changes(before, after, path=()):
    if isinstance(before, dict) and isinstance(after, dict):
        if set(before) != set(after):
            raise ValueError('실행 명세의 구조는 변경할 수 없습니다')
        return [p for key in before for p in _changes(before[key], after[key], path + (key,))]
    return [] if before == after else [path]


def _check_candidate(before, after, allowed):
    for path in _changes(before, after):
        name = '.'.join(path)
        prompt = (path == ('single_system',) or
                  len(path) == 3 and path[0] == 'calls' and path[2] == 'system' or
                  len(path) == 2 and path[0] == 'quality')
        if not prompt or name not in allowed:
            raise ValueError('허용되지 않은 실행 명세 변경: ' + name)
        value = after
        for key in path:
            value = value[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError('프롬프트는 비울 수 없습니다')


def _check_allowed(plan, allowed):
    for name in allowed:
        path = tuple(name.split('.'))
        prompt = (path == ('single_system',) or
                  len(path) == 3 and path[0] == 'calls' and path[2] == 'system' or
                  len(path) == 2 and path[0] == 'quality')
        value = plan
        try:
            for part in path:
                value = value[part]
        except (KeyError, TypeError):
            raise ValueError('허용 변경 경로를 찾을 수 없습니다: ' + name)
        if not prompt or not isinstance(value, str):
            raise ValueError('변경할 수 없는 경로입니다: ' + name)
        if path == ('single_system',) and plan.get('four_calls'):
            raise ValueError('이 실행은 다중 호출 방식이므로 개별 호출 프롬프트를 선택하세요')


class Stopped(Exception):
    pass


def _key(identifier):
    return "evaluation_candidate_" + str(identifier)


def _read(store, team, identifier):
    return store.get_report(_key(identifier), team=team)


def _compare(baseline, candidate):
    """Paired, descriptive evidence; small samples and regressions stay on hold."""
    try:
        before = evaluation.report(baseline['items'], baseline['model'])
        after = evaluation.report(candidate['items'], candidate['model'])
        paired = evaluation.paired(before, after)
        metrics = paired.get('metrics') or {}
        lower_is_better = {'harm_miss', 'generation_failure', 'judge_contradiction_rate',
                           'judge_unverifiable_rate', 'reference_excluded_claims'}
        measured = {key: value for key, value in metrics.items() if value.get('delta') is not None}
        # A grade gain must not hide a regression in metadata, preserved information
        # or source support. Unmeasured context is never evidence of improvement.
        worse = any(value['delta'] > 0 if key in lower_is_better else value['delta'] < 0
                    for key, value in measured.items())
        gains = [key for key, value in measured.items() if value.get('conclusion') ==
                 ('decrease' if key in lower_is_better else 'increase')]
        decision = ('hold' if not paired['comparable'] else 'regression' if worse else 'improved' if
                    gains else 'hold')
        left = {d['id']: d for d in before['documents']}
        cases = []
        for item in after['documents']:
            previous = left.get(item['id'])
            if not previous:
                continue
            a, b = previous['scores'].get('grade_accuracy'), item['scores'].get('grade_accuracy')
            status = 'unknown' if a is None or b is None else 'improved' if b > a else 'regressed' if b < a else 'equal'
            cases.append({'id': item['id'], 'grade': status})
        return {'decision': decision, 'paired': paired, 'cases': cases, 'improved_metrics': gains,
                'limitations': ['measured_metrics_only', 'source_support_requires_separate_diagnostic']}
    except (KeyError, TypeError, ValueError):
        return {'decision': 'hold', 'reason': '동일 사례 비교 자료가 부족합니다'}


def _update(store, team, identifier, change):
    # CAS prevents a cancel request from being overwritten by progress updates.
    while True:
        old = _read(store, team, identifier)
        if old is None:
            raise ValueError("후보 탐색을 찾을 수 없습니다")
        new = copy.deepcopy(old)
        change(new)
        new['updated_at'] = time.time()
        if store.compare_report(_key(identifier), old, new, team=team):
            return new


class Budget:
    def __init__(self, store, team, identifier):
        self.store, self.team, self.identifier = store, team, identifier

    def check(self):
        state = _read(self.store, self.team, self.identifier)
        if not state or state['status'] == 'cancel_requested':
            raise Stopped('중단 요청됨')

    def max_remaining(self):
        self.check()
        state = _read(self.store, self.team, self.identifier)
        remaining = state['max_calls'] - state['calls']
        if remaining <= 0:
            raise Stopped('호출 한도 도달')
        return remaining

    def call(self, fn, *args, **kwargs):
        def reserve(state):
            if state['status'] == 'cancel_requested':
                raise Stopped('중단 요청됨')
            if state['calls'] >= state['max_calls']:
                raise Stopped('호출 한도 도달')
            state['calls'] += 1
        try:
            _update(self.store, self.team, self.identifier, reserve)
            return fn(*args, **kwargs)
        finally:
            if hasattr(self.store, 'close_thread_connection'):
                self.store.close_thread_connection()


def start(store, team, request, *, propose=None, evaluate=None, source_loader=None, source_results=None, compare=_compare):
    """Start asynchronously. Adapters receive only development data until final.

    propose(goal, allowed_changes, execution, feedback, budget) returns a frozen
    candidate execution; evaluate(execution, rows, budget) returns JSON results.
    Both adapters must route each actual provider attempt through budget.call.
    """
    if propose is None or evaluate is None:
        return {'ok': False, 'error': '후보 탐색 실행기가 준비되지 않았습니다'}
    try:
        source_id = request['source_run_id']
        if source_loader:
            snapshot, frozen = source_loader(source_id, team)
        else:
            if type(source_id) is not int or source_id <= 0:
                raise ValueError('올바른 원본 실행이 필요합니다')
            run = store.eval_run_get(source_id, team)
            snapshot = store.get_report('eval_snapshot_' + str(source_id), team=team)
            if not run or run.get('status') != 'done' or not isinstance(snapshot, dict):
                raise ValueError('완료된 고정 평가 실행이 필요합니다')
            if (run.get('metrics') or {}).get('basis_fingerprint') != execution.digest(snapshot):
                raise ValueError('고정 평가 스냅샷이 없거나 손상됐습니다')
            frozen = snapshot['execution']
        goal, allowed = request['goal'], request['allowed_changes']
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 8000:
            raise ValueError('개선 목표가 필요합니다')
        if not isinstance(allowed, list) or not allowed or any(not isinstance(x, str) or not x.strip() for x in allowed):
            raise ValueError('허용 변경 범위가 필요합니다')
        _check_allowed(frozen, allowed)
        calls, rounds = request['max_calls'], request['max_rounds']
        if type(calls) is not int or not 1 <= calls <= 1000 or type(rounds) is not int or not 1 <= rounds <= 100:
            raise ValueError('호출 및 반복 한도를 확인하세요')
        rows = snapshot['rows']
        indexed = {golden_hash(row): row for row in rows}
        if len(indexed) != len(rows) or len(indexed) < 2:
            raise ValueError('중복되지 않은 고정 자료가 두 건 이상 필요합니다')
        # The split is fixed by the source snapshot, never controlled by the caller.
        ids = sorted(indexed)
        final_count = max(1, len(ids) // 5)
        dev, final = ids[:-final_count], ids[-final_count:]
        frozen = copy.deepcopy(frozen)
        dev_rows = copy.deepcopy([indexed[x] for x in dev])
        final_rows = copy.deepcopy([indexed[x] for x in final])
    except (KeyError, TypeError, ValueError) as exc:
        return {'ok': False, 'error': str(exc)}
    identifier = uuid.uuid4().hex
    state = {'id': identifier, 'source_run_id': source_id, 'status': 'running',
             'phase': 'queued', 'goal': goal, 'allowed_changes': copy.deepcopy(allowed),
             'max_calls': calls, 'calls': 0, 'max_rounds': rounds, 'round': 0,
             'development_ids': list(dev), 'final_ids': list(final), 'candidates': [],
             'created_at': time.time(), 'updated_at': time.time(), 'error': '', 'cost': {'status': 'unknown', 'total_usd': None}}
    index_key = 'evaluation_candidate_source_' + execution.digest(source_id)
    previous = store.get_report(index_key, team=team)
    if previous:
        existing = _read(store, team, previous.get('id'))
        if existing and existing['status'] in ('running', 'cancel_requested'):
            return {'ok': False, 'error': '이 결과의 후보 탐색이 이미 실행 중입니다', 'id': existing['id']}
    if not store.compare_report(_key(identifier), None, state, team=team,
                                guard=(index_key, previous), guard_payload={'id': identifier}):
        return {'ok': False, 'error': '동시에 후보 탐색이 시작되었습니다. 다시 확인하세요'}
    with _LOCK:
        _ACTIVE.add((team, identifier))
        threading.Thread(target=_run, args=(store, team, identifier, frozen, dev_rows,
                                           final_rows, propose, evaluate, source_results, compare), daemon=True).start()
    return {'ok': True, 'id': identifier, 'candidate': _read(store, team, identifier)}


def _run(store, team, identifier, frozen, dev_rows, final_rows, propose, evaluate, source_results, compare):
    budget = Budget(store, team, identifier)
    try:
        state = _read(store, team, identifier)
        def baseline(rows):
            if source_results:
                ids = {golden_hash(row) for row in rows}
                items = [item for item in source_results['items'] if item['hash'] in ids]
                if len(items) == len(ids) and {item['hash'] for item in items} == ids:
                    return {'model': source_results['model'], 'items': items, 'run_id': state['source_run_id']}
            return evaluate(copy.deepcopy(frozen), copy.deepcopy(rows), budget)
        dev_baseline = baseline(dev_rows)
        budget.check()
        feedback = [{'baseline': copy.deepcopy(dev_baseline)}]
        selected = None
        for number in range(1, state['max_rounds'] + 1):
            budget.check()
            _update(store, team, identifier, lambda s: s.update(phase='proposal', round=number))
            candidate = propose(state['goal'], copy.deepcopy(state['allowed_changes']),
                                copy.deepcopy(frozen), copy.deepcopy(feedback), budget)
            if not isinstance(candidate, dict):
                raise ValueError('후보 실행 명세가 올바르지 않습니다')
            _check_candidate(frozen, candidate, state['allowed_changes'])
            budget.check()
            _update(store, team, identifier, lambda s: s.update(phase='development'))
            result = evaluate(copy.deepcopy(candidate), copy.deepcopy(dev_rows), budget)
            budget.check()
            comparison = compare(dev_baseline, result)
            entry = {'round': number, 'execution': copy.deepcopy(candidate), 'development': copy.deepcopy(result),
                     'development_comparison': comparison}
            feedback.append(entry)
            _update(store, team, identifier, lambda s: s['candidates'].append(copy.deepcopy(entry)))
            if selected is None and comparison.get('decision') == 'improved':
                selected = entry
        budget.check()
        if selected is None:
            def hold(s):
                if s['status'] == 'cancel_requested':
                    raise Stopped('중단 요청됨')
                s.update(status='completed', phase='completed', outcome='hold', finished_at=time.time())
            _update(store, team, identifier, hold)
            return
        _update(store, team, identifier, lambda s: s.update(phase='final'))
        final_baseline = baseline(final_rows)
        result = evaluate(copy.deepcopy(selected['execution']), copy.deepcopy(final_rows), budget)
        final_comparison = compare(final_baseline, result)
        budget.check()
        def finish(s):
            if s['status'] == 'cancel_requested':
                raise Stopped('중단 요청됨')
            s.update(status='completed', phase='completed', outcome=final_comparison.get('decision', 'hold'),
                     selected_round=selected['round'], final_baseline=final_baseline, final_result=result,
                     final_comparison=final_comparison, finished_at=time.time())
        _update(store, team, identifier, finish)
    except Stopped as exc:
        _update(store, team, identifier, lambda s: s.update(status='cancelled' if s['status'] == 'cancel_requested' else 'stopped',
                                                            phase='stopped', error=str(exc), finished_at=time.time()))
    except Exception as exc:
        _update(store, team, identifier, lambda s: s.update(status='cancelled' if s['status'] == 'cancel_requested' else 'failed', phase='failed', error=str(exc)[:300], finished_at=time.time()))
    finally:
        with _LOCK:
            _ACTIVE.discard((team, identifier))
        if hasattr(store, 'close_thread_connection'):
            store.close_thread_connection()


def get(store, team, identifier):
    state = _read(store, team, identifier)
    if not state:
        return {'ok': False, 'error': '후보 탐색을 찾을 수 없습니다'}
    state['stalled'] = (state['status'] in ('running', 'cancel_requested') and
                        time.time() - state.get('updated_at', state['created_at']) > 1800)
    return {'ok': True, 'candidate': state}


def cancel(store, team, identifier):
    state = _read(store, team, identifier)
    if not state:
        return {'ok': False, 'error': '후보 탐색을 찾을 수 없습니다'}
    def request(s):
        if s['status'] not in ('running', 'cancel_requested'):
            raise ValueError('실행 중이 아닙니다')
        s['status'] = 'cancel_requested'
    try:
        state = _update(store, team, identifier, request)
        return {'ok': True, 'candidate': state}
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}


def start_request(team, request, created_by=''):
    """HTTP adapter: run the proposal and frozen evaluations only after start."""
    from . import evalservice
    from .config import Config
    if _SV is None:
        return {'ok': False, 'error': '후보 탐색 실행기가 준비되지 않았습니다'}
    source_id = request.get('source_run_id')
    source = evalservice.get_run(source_id, team)
    if not source.get('ok'):
        return {'ok': False, 'error': '원본 평가를 찾을 수 없습니다'}
    run = source['run']
    if run.get('status') != 'completed':
        return {'ok': False, 'error': '완료된 고정 평가 실행이 필요합니다'}
    snapshot = copy.deepcopy(run.get('snapshot') or {})
    model = snapshot.get('baseline_model') or (snapshot.get('models') or [None])[0]
    frozen = (snapshot.get('executions') or {}).get(model)
    if not isinstance(frozen, dict):
        return {'ok': False, 'error': '고정 실행 명세가 없습니다'}

    def load_source(_identifier, _team):
        return snapshot, frozen

    def propose(goal, allowed, plan, feedback, budget):
        # Resolve credentials only for the explicitly started operation. Wrapping
        # the low-level provider methods accounts for retries as separate calls.
        client = execution.restore(plan, lambda mid: _SV.llm_for_model(mid, _SV.Handler.server_mock)[0]
                                   if mid else _SV.make_text_llm(Config.load(), _SV.Handler.server_mock))
        for method in ('_call', '_mock'):
            original = getattr(client, method, None)
            if original is not None:
                setattr(client, method, lambda *args, _fn=original, **kw: budget.call(_fn, *args, **kw))
        current = {}
        for name in allowed:
            value = plan
            try:
                for part in name.split('.'):
                    value = value[part]
            except (KeyError, TypeError):
                raise ValueError('허용 변경 경로를 찾을 수 없습니다: ' + name)
            current[name] = value
        payload = {'goal': goal, 'allowed_prompts': current, 'development_feedback': feedback}
        result, _ = client.complete_json(
            '개발 평가 피드백을 바탕으로 허용된 프롬프트만 개선하세요. '
            'JSON 객체 {"changes": {"경로": "전체 프롬프트"}}만 반환하세요.',
            json.dumps(payload, ensure_ascii=False), tag='evaluation_candidate')
        budget.check()
        changes = result.get('changes') if isinstance(result, dict) else None
        if not isinstance(changes, dict) or not changes or not set(changes) <= set(allowed):
            raise ValueError('후보 응답의 변경 범위가 올바르지 않습니다')
        for name, value in changes.items():
            parts = name.split('.')
            target = plan
            for part in parts[:-1]:
                target = target[part]
            target[parts[-1]] = value
        return plan

    def evaluate(plan, rows, budget):
        spec = copy.deepcopy(snapshot)
        spec.update(rows=rows, models=[model], baseline_model=model, executions={model: plan})
        remaining = budget.max_remaining()
        spec['limits'] = dict(spec.get('limits') or {}, max_calls=remaining)
        response = evalservice.start_frozen(team, spec, created_by=created_by,
                                            background=False, call_guard=budget.call)
        budget.check()
        if not response.get('ok'):
            raise ValueError(response.get('error') or '후보 평가 시작 실패')
        detail = response.get('run') or {}
        if detail.get('status') != 'completed':
            budget.max_remaining()
            raise ValueError('후보 평가 미완료: ' + str(detail.get('status') or 'unknown'))
        return {'run_id': response.get('id'), 'model': model, 'model_results': detail.get('model_results'),
                'items': detail.get('items'), 'cost': detail.get('cost')}

    source_results = {'model': model, 'items': copy.deepcopy(run.get('items') or [])}
    return start(_SV.get_store(), team, request, propose=propose, evaluate=evaluate,
                 source_loader=load_source, source_results=source_results)


def get_request(team, identifier):
    return get(_SV.get_store(), team, identifier)


def cancel_request(team, identifier):
    return cancel(_SV.get_store(), team, identifier)
