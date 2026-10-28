"""Team-scoped frozen evaluation service, backed by atomic report CAS.

Spectrum's pure runtime primitives own call identity/admission/accounting;
Prism owns data, authorization, clients and the existing evaluation denominators.
A persisted result and its completion flag are one CAS write. GET is read-only.
"""
from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from pathlib import Path

from . import evaluation as EV, execution as EX, spectrum_adapter as SP
from .config import Config
from .store import golden_hash

_SV = None
_INDEX = 'evaluation_index_v1'
_PREFIX = 'evaluation_run_'
_ENVIRONMENTS = 'evaluation_environments_v1'
_ACTIVE = {}
_LOCK = threading.Lock()
LEASE_SECONDS = 120
MAX_MODELS = 8
MAX_ROWS = 1000
ACTIVE_STATUSES = ('queued', 'running', 'cancel_requested')


def _service_version():
    root = Path(__file__).parent
    return SP._hash({name: (root / name).read_text(encoding='utf-8')
                     for name in ('evalservice.py', 'evalops.py', 'spectrum_adapter.py')})


class ServiceError(ValueError):
    def __init__(self, message, code='invalid_request'):
        super().__init__(message)
        self.code = code


class _CallStopped(BaseException):
    """Cross the legacy client's broad retry catch, then become RuntimeStopped."""


def _error(exc):
    return {'ok': False, 'error': str(exc)[:300], 'code': getattr(exc, 'code', 'invalid_request')}


def _store(team):
    if _SV is None:
        raise ServiceError('평가 서비스가 연결되지 않았습니다', 'unavailable')
    if _SV._supa() and not team:
        raise ServiceError('팀 소속이 필요합니다', 'team_required')
    st = _SV.get_store()
    if not st or not hasattr(st, 'compare_report'):
        raise ServiceError('원자적 평가 저장소를 사용할 수 없습니다', 'unavailable')
    return st


def _load(st, run_id, team):
    if not isinstance(run_id, str) or not run_id or len(run_id) > 100:
        raise ServiceError('실행 ID가 올바르지 않습니다')
    try:
        run = st.get_report(_PREFIX + run_id, team=team)
    finally:
        if hasattr(st, 'close_thread_connection'):
            st.close_thread_connection()
    if not run or run.get('team') != (team or ''):
        raise ServiceError('평가 실행을 찾을 수 없습니다', 'not_found')
    return run


def _update(st, run_id, team, change):
    try:
        for _ in range(100):
            before = _load(st, run_id, team)
            after = copy.deepcopy(before)
            change(after)
            after['updated_at'] = time.time()
            if st.compare_report(_PREFIX + run_id, before, after, team=team):
                return after
    finally:
        if hasattr(st, 'close_thread_connection'):
            st.close_thread_connection()
    raise ServiceError('실행 상태 갱신이 경합했습니다. 다시 시도하세요', 'conflict')


def _resolve(model):
    if model:
        llm, route = _SV.llm_for_model(model, _SV.Handler.server_mock)
        if llm is None:
            raise ServiceError('모델 호출 불가: ' + str(route), 'model_unavailable')
        return llm
    return _SV.make_text_llm(Config.load(), _SV.Handler.server_mock)


def _validate_snapshot(snapshot):
    snapshot = copy.deepcopy(snapshot)
    if not isinstance(snapshot, dict):
        raise ServiceError('실행 스냅샷이 필요합니다')
    models, rows = snapshot.get('models'), snapshot.get('rows')
    if (not isinstance(models, list) or not 1 <= len(models) <= MAX_MODELS or
            any(not isinstance(m, str) or not m or len(m) > 200 for m in models) or len(set(models)) != len(models)):
        raise ServiceError('평가 대상 모델은 중복 없는 1~8개여야 합니다')
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        raise ServiceError('평가 자료는 1~1000건이어야 합니다')
    if any(not isinstance(r, dict) or not isinstance(r.get('content'), dict) or
           not isinstance(r.get('expected'), dict) for r in rows):
        raise ServiceError('원문·정답 스냅샷이 올바르지 않습니다')
    hashes = [golden_hash(row) for row in rows]
    if len(hashes) != len(set(hashes)):
        raise ServiceError('중복 평가 자료가 있습니다')
    if snapshot.get('scope') not in ('all', 'eval') or snapshot.get('baseline_model') not in models:
        raise ServiceError('자료 범위 또는 비교 기준이 올바르지 않습니다')
    if snapshot.get('metric_version') != EV.VERSION:
        raise ServiceError('평가 기준 버전이 바뀌었습니다. 새 평가를 시작하세요', 'version_changed')
    if snapshot.get('service_version') != _service_version():
        raise ServiceError('평가 실행 코드가 바뀌었습니다. 새 평가를 시작하세요', 'version_changed')
    snapshot['limits'] = SP.validate_limits(snapshot.get('limits'))
    if set(snapshot.get('executions') or {}) != set(models):
        raise ServiceError('모델별 실행 명세가 필요합니다')
    for model in models:
        # The UI target label can be an alias; the frozen main client records
        # the actual provider ID (e.g. gpt-5.4 -> openai/gpt-5.4).
        EX.restore(snapshot['executions'][model], _resolve)
    snapshot['dataset_fingerprint'] = SP._hash(rows)
    snapshot['prompt_version'] = SP._hash({model: {
        'calls': {call: entry.get('system') for call, entry in plan['calls'].items()},
        'single_system': plan.get('single_system'), 'quality': plan.get('quality')}
        for model, plan in snapshot['executions'].items()})[:12]
    SP._clean(snapshot)
    SP._json(snapshot)
    return snapshot


def start(team=None, models=None, scope='all', baseline_model='', limits=None,
          request_id='', created_by='', environment_id='', *, background=True):
    try:
        st = _store(team)
        if scope not in ('all', 'eval'):
            raise ServiceError('자료 범위는 all 또는 eval이어야 합니다')
        if models is None:
            models = [Config.load().model]
        if (not isinstance(models, list) or not 1 <= len(models) <= MAX_MODELS or
                any(not isinstance(m, str) or not m.strip() for m in models) or len(set(models)) != len(models)):
            raise ServiceError('평가 대상 모델은 중복 없는 1~8개여야 합니다')
        environment = None
        if environment_id:
            environment = next((e for e in (st.get_report(_ENVIRONMENTS, team=team) or {}).get('items', [])
                                if e['id'] == environment_id), None)
            if environment is None:
                raise ServiceError('저장 검수 환경을 찾을 수 없습니다', 'not_found')
            if environment['metric_version'] != EV.VERSION:
                raise ServiceError('저장 환경의 평가 버전이 바뀌었습니다', 'version_changed')
        limits = SP.validate_limits(limits if limits is not None else (environment or {}).get('limits'))
        if baseline_model and baseline_model not in models:
            raise ServiceError('비교 기준 모델이 평가 대상에 없습니다')
        from .learnops import _scope_golden
        rows = copy.deepcopy(_scope_golden(st.get_golden(team), scope, st, team))[:MAX_ROWS]
        if not rows:
            raise ServiceError('범위에 해당하는 정답셋이 없습니다')
        plans = {model: EX.capture(_resolve(model), rows)[1] for model in models}
        snapshot = {'rows': rows, 'models': models, 'executions': plans, 'scope': scope,
                    'baseline_model': baseline_model or models[0], 'metric_version': EV.VERSION,
                    'limits': SP.validate_limits(limits), 'method': 'prism-default-v1',
                    'service_version': _service_version(),
                    'engine': {'name': 'spectrum-runtime-adapter', 'source_revision': SP.SOURCE_REVISION,
                               'source_sha256': SP.SOURCE_SHA256}}
        if environment:
            snapshot['environment'] = copy.deepcopy(environment)
        return start_frozen(team, snapshot, created_by=created_by, request_id=request_id, background=background)
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _error(exc)


def preview(team=None, scope='all'):
    """Read-only current eligibility; start captures its own immutable version."""
    try:
        st = _store(team)
        if scope not in ('all', 'eval'):
            raise ServiceError('자료 범위는 all 또는 eval이어야 합니다')
        from .learnops import _scope_golden
        from . import prompts as P
        rows = _scope_golden(st.get_golden(team), scope, st, team)
        available = len(rows)
        rows = rows[:MAX_ROWS]
        eligibility = {field: {} for field in EV.FIELDS}
        for row in rows:
            for field in EV.FIELDS:
                state = EV.field_status(row.get('expected') or {}, field)
                counts = eligibility[field]
                counts[state] = counts.get(state, 0) + 1
        return {'ok': True, 'scope': scope, 'total': len(rows), 'available_total': available,
                'truncated': available > len(rows), 'metric_version': EV.VERSION, 'eligibility': eligibility,
                'prompt': {'quality_version': P.quality_version(), 'item_version': P.IMETA_VERSION},
                'cost': {'status': 'unknown', 'total_usd': None},
                'note': '실행 시점에 원문·정답·프롬프트를 고정합니다'}
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _error(exc)
    finally:
        if 'st' in locals() and hasattr(st, 'close_thread_connection'):
            st.close_thread_connection()


def list_environments(team=None):
    try:
        st = _store(team)
        return {'ok': True, 'items': (st.get_report(_ENVIRONMENTS, team=team) or {}).get('items', [])}
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _error(exc)
    finally:
        if 'st' in locals() and hasattr(st, 'close_thread_connection'):
            st.close_thread_connection()


def save_environment(team, name, judge_model, max_calls=1000, created_by=''):
    """Save immutable diagnostic defaults without executing the judge."""
    try:
        st = _store(team)
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ServiceError('검수 환경 이름은 1~100자여야 합니다')
        if not isinstance(judge_model, str) or not judge_model.strip() or len(judge_model) > 200:
            raise ServiceError('판정 모델이 필요합니다')
        limits = SP.validate_limits({'max_calls': max_calls})
        from . import evalreview, context_eval, accuracy_eval
        client = EX.clone_client(_resolve(judge_model))
        client.cfg.retry.max_retries = 0
        client.cfg.retry.max_format_retries = 0
        spec = {'name': name.strip(), 'judge_model': client.model, 'judge_settings': EX.client_settings(client),
                'metric_version': EV.VERSION, 'diagnostic_version': evalreview.VERSION,
                'fact_system': context_eval.FACT_REVIEW_SYSTEM, 'coverage_system': accuracy_eval.COVERAGE_SYSTEM,
                'max_output_tokens': 4096, 'limits': limits}
        SP._clean(spec)
        spec = json.loads(SP._json(spec))
        version = SP._hash(spec)
        for _ in range(100):
            old = st.get_report(_ENVIRONMENTS, team=team)
            state = copy.deepcopy(old) if old else {'items': []}
            existing = next((e for e in state['items'] if e['version'] == version), None)
            if existing:
                return {'ok': True, 'environment': existing}
            environment = dict(spec, id=uuid.uuid4().hex, version=version,
                               created_at=time.time(), created_by=created_by)
            state['items'].insert(0, environment)
            if st.compare_report(_ENVIRONMENTS, old, state, team=team):
                return {'ok': True, 'environment': environment}
        raise ServiceError('검수 환경 저장이 경합했습니다', 'conflict')
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _error(exc)
    finally:
        if 'st' in locals() and hasattr(st, 'close_thread_connection'):
            st.close_thread_connection()


def start_frozen(team, snapshot, *, created_by='', request_id='', background=True, call_guard=None, rerun_of=''):
    """Trusted candidate entrypoint; HTTP never accepts an arbitrary execution plan."""
    try:
        st = _store(team)
        snapshot = _validate_snapshot(snapshot)
        if not isinstance(request_id, str) or len(request_id) > 200:
            raise ServiceError('request_id는 200자 이내 문자열이어야 합니다')
        basis = SP.job_identity('prism.evaluate.v1', snapshot, {'adapter': 1})
        active_key = basis if not rerun_of else SP._hash([basis, rerun_of])
        for _ in range(100):
            idx = st.get_report(_INDEX, team=team)
            index = copy.deepcopy(idx) if idx else {'runs': [], 'requests': {}, 'active': {}}
            prior = index['requests'].get(request_id) if request_id else None
            if prior and (prior['basis'] != basis or prior.get('rerun_of', '') != rerun_of):
                raise ServiceError('동일 요청 ID의 실행 조건이 다릅니다', 'idempotency_conflict')
            existing = prior['id'] if prior else index['active'].get(active_key)
            if existing:
                run = _load(st, existing, team)
                if prior or run['status'] in ACTIVE_STATUSES:
                    return {'ok': True, 'id': existing, 'run': _public(run), 'deduplicated': True}
            run_id = uuid.uuid4().hex
            now = time.time()
            run = {'id': run_id, 'kind': 'evaluation', 'team': team or '', 'created_by': created_by,
                   'created_at': now, 'updated_at': now, 'status': 'queued', 'phase': 'queued',
                   'snapshot': snapshot, 'basis_fingerprint': basis, 'results': {}, 'attempts': {},
                   'owner': '', 'lease': 0, 'error': '', 'external_guard': call_guard is not None, 'rerun_of': rerun_of}
            index['runs'].insert(0, run_id)
            index['active'][active_key] = run_id
            if request_id:
                index['requests'][request_id] = {'id': run_id, 'basis': basis, 'rerun_of': rerun_of}
            if st.compare_report(_PREFIX + run_id, None, run, team=team,
                                 guard=(_INDEX, idx), guard_payload=index):
                _launch(st, run_id, team, background=background, call_guard=call_guard)
                return {'ok': True, 'id': run_id, 'run': _public(_load(st, run_id, team)), 'deduplicated': False}
        raise ServiceError('중복 실행 확인이 경합했습니다', 'conflict')
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _error(exc)


def _key(model, row):
    return SP._hash([model, golden_hash(row)])


def _owned(run, owner):
    if run.get('owner') != owner or run.get('lease', 0) <= time.time():
        raise ServiceError('실행 소유권이 만료되었습니다', 'lease_lost')


def _launch(st, run_id, team, *, background=True, call_guard=None):
    owner = uuid.uuid4().hex
    def claim(run):
        if run['status'] == 'completed':
            raise ServiceError('이미 완료된 실행입니다', 'completed')
        if run.get('lease', 0) > time.time():
            raise ServiceError('이미 실행 중입니다', 'already_running')
        run.update(owner=owner, lease=time.time() + LEASE_SECONDS, status='running', phase='generating', error='')
        run.pop('report', None)
        for attempt in run['attempts'].values():
            if attempt['status'] == 'running':
                attempt.update(status='uncertain', usage=SP._usage({}, 0, 'unknown'))
        for result in run['results'].values():
            if result['status'] == 'running':
                result.update(status='failed', error='이전 호출의 완료 여부 미확인')
    _update(st, run_id, team, claim)
    if not background:
        _run(st, run_id, team, owner, call_guard)
        return
    th = threading.Thread(target=_run, args=(st, run_id, team, owner, call_guard), daemon=True,
                          name='prism-evaluation-' + run_id[:12])
    with _LOCK:
        _ACTIVE[run_id] = th
    th.start()


def _meter_client(llm, invoke):
    """Wrap a private client copy; record each HTTP retry, not each document.

    The subclass survives agents.py's copy.copy without patching any module or
    global network function. Both real provider and mock paths are accounted.
    """
    base = type(llm)
    class MeteredClient(base):
        def complete_json(self, *args, **kwargs):
            try:
                return super().complete_json(*args, **kwargs)
            except _CallStopped as exc:
                raise SP.RuntimeStopped(str(exc)) from None

        def complete_text(self, *args, **kwargs):
            try:
                return super().complete_text(*args, **kwargs)
            except _CallStopped as exc:
                raise SP.RuntimeStopped(str(exc)) from None

        def _call(self, *args, **kwargs):
            return invoke(super()._call, args, kwargs, self, False)

        def _mock(self, *args, **kwargs):
            return invoke(super()._mock, args, kwargs, self, True)
    out = MeteredClient.__new__(MeteredClient)
    out.__dict__.update(llm.__dict__)
    return out


def _result_usage(result, seconds, mock):
    if mock:
        return SP._usage({'tokens': 0, 'cost_usd': 0}, seconds, 'synthetic')
    if result is None:
        return SP._usage({}, seconds, 'unknown')
    try:
        cost = result.cost_usd
        tokens = int(result.in_tok) + int(result.out_tok)
        # Provider tokens may be approximated and prices are configured estimates.
        return SP._usage({'tokens': tokens, 'cost_usd': cost}, seconds, 'estimated')
    except (AttributeError, TypeError, ValueError):
        return SP._usage({}, seconds, 'unknown')


def _run(st, run_id, team, owner, call_guard=None):
    stop_heartbeat = threading.Event()
    def heartbeat():
        while not stop_heartbeat.wait(20):
            try:
                def renew(run):
                    _owned(run, owner)
                    run['lease'] = time.time() + LEASE_SECONDS
                _update(st, run_id, team, renew)
            except Exception:
                return
    timer = threading.Thread(target=heartbeat, daemon=True)
    timer.start()
    try:
        run = _load(st, run_id, team)
        snapshot = _validate_snapshot(run['snapshot'])
        if SP.job_identity('prism.evaluate.v1', snapshot, {'adapter': 1}) != run['basis_fingerprint']:
            raise ServiceError('실행 스냅샷이 손상됐습니다', 'snapshot_corrupt')
        from . import harness as H
        for model in snapshot['models']:
            for row in snapshot['rows']:
                run = _load(st, run_id, team)
                _owned(run, owner)
                if run['status'] == 'cancel_requested':
                    _finish(st, run_id, team, owner, 'cancelled')
                    return
                key = _key(model, row)
                if run['results'].get(key, {}).get('status') == 'completed':
                    continue
                SP.admit(list(run['attempts'].values()), snapshot['limits'])
                def begin(r):
                    _owned(r, owner)
                    r['results'][key] = {'model': model, 'hash': golden_hash(row), 'status': 'running',
                                         'output': None, 'error': ''}
                _update(st, run_id, team, begin)
                blocked = []
                def invoke(fn, args, kwargs, client, mock):
                    attempt_id = uuid.uuid4().hex
                    def reserve(r):
                        _owned(r, owner)
                        if r['status'] == 'cancel_requested':
                            raise SP.RuntimeStopped('cancel requested')
                        SP.admit(list(r['attempts'].values()), snapshot['limits'])
                        r['attempts'][attempt_id] = {'model': model, 'hash': golden_hash(row),
                            'provider_model': client.model, 'tag': kwargs.get('tag') or (args[2] if len(args) > 2 else ''),
                            'status': 'running', 'started_at': time.time(), 'usage': None}
                    try:
                        _update(st, run_id, team, reserve)
                    except (SP.RuntimeStopped, ServiceError) as exc:
                        blocked.append(str(exc))
                        raise _CallStopped(str(exc)) from None
                    started = time.monotonic()
                    result = None
                    called = False
                    def perform(*a, **kw):
                        nonlocal called
                        called = True
                        return fn(*a, **kw)
                    try:
                        value = call_guard(perform, *args, **kwargs) if call_guard else perform(*args, **kwargs)
                        result = value[1] if mock else value
                    except BaseException as exc:
                        result = getattr(exc, 'result', None)
                        usage = _result_usage(result, time.monotonic() - started, mock=False)
                        def failed(r):
                            _owned(r, owner)
                            if called:
                                r['attempts'][attempt_id].update(status='failed', usage=usage)
                            else:
                                del r['attempts'][attempt_id]
                        _update(st, run_id, team, failed)
                        if not called:
                            blocked.append(str(exc))
                            raise _CallStopped(str(exc)) from None
                        raise
                    usage = _result_usage(result, time.monotonic() - started, mock)
                    def complete(r):
                        _owned(r, owner)
                        r['attempts'][attempt_id].update(status='completed', usage=usage)
                    _update(st, run_id, team, complete)
                    return value
                llm = EX.restore(snapshot['executions'][model], _resolve)
                llm = _meter_client(llm, invoke)
                llm.execution = dict(llm.execution, routes={c: _meter_client(v, invoke)
                                     for c, v in llm.execution['routes'].items()})
                output, error = None, ''
                try:
                    output = H.run(row['content'], llm, H.Methodology(name='공통 평가'))
                    error = _output_error(output)
                except Exception as exc:
                    error = str(exc)[:300]
                if blocked:
                    error = blocked[0]
                def save(r):
                    _owned(r, owner)
                    r['results'][key].update(status='failed' if error else 'completed', output=output, error=error)
                _update(st, run_id, team, save)
                if blocked:
                    current = _load(st, run_id, team)
                    _finish(st, run_id, team, owner,
                            'cancelled' if current['status'] == 'cancel_requested' else 'limit_reached', error)
                    return
        run = _load(st, run_id, team)
        status = ('cancelled' if run['status'] == 'cancel_requested' else
                  'failed' if any(r['status'] == 'failed' for r in run['results'].values()) else 'completed')
        _finish(st, run_id, team, owner, status)
    except SP.RuntimeStopped as exc:
        _finish(st, run_id, team, owner, 'limit_reached', str(exc))
    except Exception as exc:
        try:
            _finish(st, run_id, team, owner, 'failed', str(exc)[:300])
        except Exception:
            pass  # Durable lease expires; only explicit resume can recover it.
    finally:
        stop_heartbeat.set()
        timer.join(timeout=1)
        with _LOCK:
            _ACTIVE.pop(run_id, None)
        if hasattr(st, 'close_thread_connection'):
            st.close_thread_connection()


def _output_error(output):
    if not isinstance(output, dict):
        return '생성 결과 없음'
    states = (output.get('item_meta') or {}).get('meta_status') or {}
    bad = [f for f, state in states.items() if state in ('call_failed', 'invalid_output', 'configuration_error')]
    if bad or any('fail' in str(f) or 'unparse' in str(f) for f in (output.get('trace') or {}).get('fallbacks', [])):
        return '생성 실패: ' + ', '.join(bad)
    return ''


def _finish(st, run_id, team, owner, status, error=''):
    # Persist the scoring result, not only raw outputs: historical GET must not
    # silently rescore with a later deployment's metric implementation.
    current = _load(st, run_id, team)
    _owned(current, owner)
    current['status'] = 'cancelled' if current['status'] == 'cancel_requested' else status
    view = _public(current)
    report = {k: view[k] for k in ('items', 'model_results', 'comparisons', 'limitations')}
    def finish(run):
        _owned(run, owner)
        if run['status'] == 'cancel_requested':
            status_final = 'cancelled'
        else:
            status_final = status
        run.update(status=status_final, phase=status_final, owner='', lease=0, error=error, finished_at=time.time())
        run['report'] = copy.deepcopy(report)
        if status_final != 'completed':
            for comparison in run['report']['comparisons']:
                comparison.update(comparable=False, reason='미완료 실행: 전체 표본의 판단 보류', verdict='incomparable')
    return _update(st, run_id, team, finish)


def cancel(run_id, team=None):
    try:
        st = _store(team)
        def request(run):
            if run['status'] not in ACTIVE_STATUSES:
                return
            live = run.get('lease', 0) > time.time()
            run.update(status='cancel_requested' if live else 'cancelled',
                       phase='cancel_requested' if live else 'cancelled')
        run = _update(st, run_id, team, request)
        return {'ok': True, 'id': run_id, 'status': run['status']}
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _error(exc)


def resume(run_id, team=None, *, background=True, call_guard=None):
    try:
        st = _store(team)
        run = _load(st, run_id, team)
        if run['status'] == 'completed':
            return {'ok': True, 'id': run_id, 'run': _public(run), 'deduplicated': True}
        if run.get('external_guard') and call_guard is None:
            raise ServiceError('상위 후보 탐색의 호출 한도와 중단 조건이 필요한 실행입니다', 'external_guard_required')
        snapshot = _validate_snapshot(run['snapshot'])
        if SP.job_identity('prism.evaluate.v1', snapshot, {'adapter': 1}) != run['basis_fingerprint']:
            raise ServiceError('실행 스냅샷이 손상됐습니다', 'snapshot_corrupt')
        if 'max_calls' in snapshot['limits'] and len(run['attempts']) >= snapshot['limits']['max_calls']:
            raise ServiceError('호출 한도를 모두 사용했습니다. 새 조건으로 실행하세요', 'limit_reached')
        _launch(st, run_id, team, background=background, call_guard=call_guard)
        return {'ok': True, 'id': run_id, 'run': _public(_load(st, run_id, team))}
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _error(exc)


def get_run(run_id, team=None):
    try:
        return {'ok': True, 'run': _public(_load(_store(team), run_id, team))}
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _error(exc)


def rerun(run_id, team=None, *, request_id='', created_by='', background=True):
    """Create a fresh result history under the exact original frozen conditions."""
    try:
        run = _load(_store(team), run_id, team)
        if run.get('external_guard'):
            raise ServiceError('후보 탐색 하위 실행은 독립 재평가할 수 없습니다', 'external_guard_required')
        if run['status'] in ACTIVE_STATUSES:
            raise ServiceError('진행 중인 실행은 완료 또는 중단 후 다시 평가하세요', 'already_running')
        snapshot = _validate_snapshot(run['snapshot'])
        if SP.job_identity('prism.evaluate.v1', snapshot, {'adapter': 1}) != run['basis_fingerprint']:
            raise ServiceError('실행 스냅샷이 손상됐습니다', 'snapshot_corrupt')
        return start_frozen(team, snapshot, request_id=request_id, created_by=created_by,
                            background=background, rerun_of=run_id)
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _error(exc)


def list_runs(team=None):
    try:
        st = _store(team)
        index = st.get_report(_INDEX, team=team) or {}
        return {'ok': True, 'items': [_public(_load(st, rid, team), detail=False)
                                     for rid in index.get('runs', [])[:100]]}
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _error(exc)


def _metrics(rows, outputs):
    from . import evalops as EO, abtest, metaeval as ME
    m = EO._zero_metrics()
    for row, out in zip(rows, outputs):
        EO._tally(m, row, out)
    n, gn, hn = m['n'], m.get('grade_n', 0), m['harm_n']
    return {**abtest.intent_report(m), **ME.meta_report(m, read_only=True),
            'n': n, 'grade_n': gn,
            'grade_accuracy': round(m['grade_hit'] / gn, 4) if gn else None,
            'reason_exact_match': round(m['reason_exact'] / gn, 4) if gn else None,
            'reason_jaccard': round(m['jaccard_sum'] / gn, 4) if gn else None,
            'harm_miss_rate': round(m['harm_miss'] / hn, 4) if hn else None,
            'harm_expected_n': hn, 'harm_miss_basis': 'expected_r',
            'empty_rate': round(m['empty'] / n, 4) if n else 0,
            'tokens': {'in': m['tok_in'], 'out': m['tok_out']}}


def _public(run, detail=True):
    snapshot = run['snapshot']
    results = list(run['results'].values())
    completed = sum(r['status'] == 'completed' for r in results)
    failed = sum(r['status'] == 'failed' for r in results)
    total = len(snapshot['rows']) * len(snapshot['models'])
    stalled = run['status'] in ACTIVE_STATUSES and run.get('lease', 0) <= time.time()
    budget_left = ('max_calls' not in snapshot['limits'] or
                   len(run['attempts']) < snapshot['limits']['max_calls'])
    can_resume = bool((stalled or run['status'] in ('failed', 'cancelled')) and budget_left and
                      not run.get('external_guard'))
    out = {k: run[k] for k in ('id', 'kind', 'status', 'phase', 'created_at', 'updated_at', 'basis_fingerprint', 'error')}
    out.update(models=list(snapshot['models']), scope=snapshot['scope'], total=total,
               completed=completed, failed=failed, pending=total-completed-failed,
               cost=SP.usage_summary(list(run['attempts'].values())), can_resume=can_resume,
               stalled=stalled, baseline_model=snapshot['baseline_model'], rerun_of=run.get('rerun_of', ''),
               can_rerun=run['status'] not in ACTIVE_STATUSES and not run.get('external_guard'))
    if not detail:
        return out
    if run.get('report') is not None:
        return dict(out, snapshot=copy.deepcopy(snapshot), **copy.deepcopy(run['report']))
    items, model_results = [], []
    for row in snapshot['rows']:
        by = {model: run['results'].get(_key(model, row), {}) for model in snapshot['models']}
        items.append({'hash': golden_hash(row), 'source': EV.source(row), 'expected': row['expected'],
                      'got': {model: EV.prediction(r.get('output')) for model, r in by.items()},
                      'states': {model: r.get('status', 'pending') for model, r in by.items()},
                      'errors': {model: r.get('error', '') for model, r in by.items()}})
    for model in snapshot['models']:
        done = [r for r in results if r['model'] == model and r['status'] in ('completed', 'failed')]
        hashes = {r['hash']: r for r in done}
        rows = [row for row in snapshot['rows'] if golden_hash(row) in hashes]
        outputs = [hashes[golden_hash(row)].get('output') for row in rows]
        report = EV.report(EV.items(rows, outputs), 'result', embed=None)
        cost = SP.usage_summary([a for a in run['attempts'].values() if a['model'] == model])
        model_results.append({'model': model, 'total': len(snapshot['rows']),
            'completed': sum(r['status'] == 'completed' for r in done),
            'failed': sum(r['status'] == 'failed' for r in done), 'pending': len(snapshot['rows'])-len(done),
            'metrics': dict(_metrics(rows, outputs), cost_usd=cost['total_usd']), 'evaluation': report, 'cost': cost})
    baseline = next(r for r in model_results if r['model'] == snapshot['baseline_model'])
    comparisons = []
    for r in model_results:
        if r is baseline:
            continue
        paired = (EV.paired(baseline['evaluation'], r['evaluation']) if run['status'] == 'completed' else
                  {'comparable': False, 'reason': '미완료 실행: 전체 표본의 판단 보류', 'metrics': {}})
        comparisons.append({'baseline_model': baseline['model'], 'model': r['model'], **paired,
                            'verdict': 'insufficient_evidence' if paired['comparable'] else 'incomparable'})
    out.update(snapshot=copy.deepcopy(snapshot), items=items, model_results=model_results, comparisons=comparisons,
               limitations=['semantic_similarity_not_measured', 'contextual_winner_not_diagnosed',
                            'in_flight_call_outcome_may_be_unknown_after_restart'])
    return out
