"""실행 시작 시 실제 프롬프트·모델·추론 설정을 고정한다. 자격증명은 저장하지 않는다."""
import copy
import hashlib
import json
from pathlib import Path

from . import agents as A, prompts as P, routing as R
from .schema import Content
from .meta_prompts import CALLS

ENGINE_FILES = ('agents.py', 'schema.py', 'dictionaries.py', 'meta_contract.py',
                'harness.py', 'execution.py', 'dnm.py', 'meta_prompts.py', 'prompts.py', 'routing.py', 'llm.py', 'metaeval.py', 'abtest.py', 'verify.py')
CONFIG_KEYS = ('chat_url', 'model', 'reasoning_effort', 'timeout', 'retry', 'rate',
               'prices', 'prompt_cache', 'max_tokens', 'temperature')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def engine_version():
    root = Path(__file__).parent
    return digest({n: hashlib.sha256((root / n).read_bytes()).hexdigest()
                   for n in ENGINE_FILES if (root / n).is_file()})


def quality_key(routing):
    return digest([routing.service_group, routing.active_quality_metas])


def client_settings(llm):
    cfg = getattr(llm, 'cfg', None)
    values = cfg.redacted() if cfg else {}
    return {'model': getattr(llm, 'model', '') or '',
            'config': {k: values[k] for k in CONFIG_KEYS if k in values},
            'reasoning_effort': getattr(llm, 'reasoning_effort', None),
            'timeout': getattr(llm, 'timeout', None),
            'adapt': sorted(getattr(llm, '_adapt', set())), 'mock': bool(llm.mock)}


def clone_client(llm, settings=None):
    out = copy.copy(llm)
    if hasattr(llm, 'cfg'):
        out.cfg = copy.deepcopy(llm.cfg)
    if hasattr(llm, '_adapt'):
        out._adapt = set(llm._adapt)
    if settings:
        from .config import _merge
        if hasattr(out, 'cfg'):
            _merge(out.cfg, settings['config'])
        out.model = settings['model']
        for k in ('reasoning_effort', 'timeout'):
            if settings.get(k) is not None:
                setattr(out, k, settings[k])
        out._adapt = set(settings.get('adapt') or [])
        if bool(out.mock) != settings['mock']:
            raise ValueError('실행 당시 모델 연결을 복원할 수 없습니다')
    if out.mock and getattr(out, "_mock_fn", None) is None:
        from .harness import _mock_generator
        out._mock_fn = _mock_generator
    out.frozen_parameters = True
    return out


def capture(llm, rows):
    """설정 읽기와 라우트 해석은 이 시점 한 번뿐이다. 각 행은 같은 사본을 쓴다."""
    main = clone_client(llm)
    dummy = Content.from_dict({})
    plan = {'schema': 1, 'engine': engine_version(), 'main': client_settings(main),
            'four_calls': bool(A.META_CFG.get('four_calls', True)),
            'calls': {}, 'quality': {}, 'quality_version': P.quality_version(),
            'item_version': P.IMETA_VERSION, 'scoring': {'summary': 'bigram_f1'}}
    routes = {}
    for call in CALLS:
        try:
            route = clone_client(A._call_llm(main, call))
            routes[call] = route
            plan['calls'][call] = {'client': client_settings(route),
                                   'system': P.call_system(dummy, call, route.model)}
        except Exception:
            plan['calls'][call] = {'error': 'configuration_error'}
    for row in rows:
        content = Content.from_dict(row.get('content') or {})
        routing = R.dispatch(content)
        plan['quality'][quality_key(routing)] = P.quality_system(
            routing.active_quality_metas, routing.service_group)
    plan['single_system'] = P.item_system(dummy, main.model)
    plan = json.loads(json.dumps(plan))  # JSON 저장 전후 동일한 불변 명세(튜플→목록)
    main.execution = {'snapshot': copy.deepcopy(plan), 'routes': routes}
    return main, plan


def restore(snapshot, resolve):
    if snapshot.get('schema') != 1 or snapshot.get('engine') != engine_version():
        raise ValueError('추출·검증 코드 버전이 바뀌었습니다. 새 평가를 시작하세요')

    def client(settings):
        obj = resolve(settings['model'])
        if obj is None:
            raise ValueError('고정 모델을 호출할 수 없습니다: ' + settings['model'])
        # Never send the current credential to an endpoint changed since the snapshot.
        current_url = getattr(getattr(obj, 'cfg', None), 'chat_url', '')
        if current_url != settings['config'].get('chat_url', current_url):
            raise ValueError('모델 연결 주소가 바뀌었습니다. 새 평가를 시작하세요')
        return clone_client(obj, settings)

    main = client(snapshot['main'])
    routes = {c: client(v['client']) for c, v in snapshot['calls'].items() if 'client' in v}
    main.execution = {'snapshot': copy.deepcopy(snapshot), 'routes': routes}
    return main


def prompt_record(snapshot, run_id, created_at):
    """기존 조회·내보내기 형식에도 실제 고정 실행 명세만 투영한다."""
    return {'run_id': run_id, 'ts': created_at, 'version': snapshot['engine'][:12],
            'model': snapshot['main']['model'], 'quality_version': snapshot.get('quality_version', ''),
            'calls': {call: {'model': (entry.get('client') or {}).get('model', ''),
                             'system': entry.get('system', ''), 'error': entry.get('error', '')}
                      for call, entry in snapshot['calls'].items()},
            'item': snapshot.get('single_system', ''),
            'quality': ({'model': snapshot['main']['model'], 'by_service': snapshot['quality']}
                        if snapshot.get('quality') else None),
            'execution': copy.deepcopy(snapshot)}
