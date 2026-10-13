"""키워드 정답 재현을 위한 자동 튜닝. 후보 버전만 만들며 채택은 검수자에게 맡긴다."""
import copy
import threading
import time

from . import keywordlab as K

STATE = 'keyword_lab_tuning_v1'
ACTIVE = set()
LOCK = threading.Lock()


class TuningStopped(Exception):
    pass


def status(team):
    run = K._get(STATE, team)
    if run and run['status'] == 'running' and (team, run['id']) not in ACTIVE:
        # ponytail: 실행기는 프로세스 1개 · 다중 워커 도입 시 임대와 heartbeat로 교체
        def interrupted(current):
            if current['id'] == run['id'] and current['status'] == 'running':
                current.update(status='stopped', reason='서버 재시작으로 중단 · 저장된 후보를 확인하고 다시 시작하세요')
        run = K._update(STATE, team, interrupted)
    return {k: v for k, v in run.items() if k != 'gold'} if run else None


def _save(team, ident, **fields):
    def change(run):
        if not run or run['id'] != ident:
            raise TuningStopped('다른 튜닝 실행으로 변경되었습니다')
        run.update(fields, updated_at=time.time())
    return K._update(STATE, team, change)


def start(body, team, actor):
    version = K._version(body.get('version_id'), team)
    if K._target(version) != 'keyword':
        raise ValueError('키워드 프롬프트를 선택하세요')
    compiler = body.get('compiler_model')
    if not isinstance(compiler, str) or not compiler or len(compiler) > 120:
        raise ValueError('메타컴파일러 모델을 선택하세요')
    rounds = body.get('max_rounds', 3)
    if type(rounds) is not int or not 1 <= rounds <= 10:
        raise ValueError('최대 반복 횟수는 1~10회입니다')
    gold = K._current_gold(team)
    selected = {}
    for field, partition, cap in (('gold_keys', 'development', 20), ('evaluation_keys', 'evaluation', 30)):
        keys = body.get(field)
        if not isinstance(keys, list) or not 1 <= len(keys) <= cap or not all(isinstance(k, str) for k in keys) or len(set(keys)) != len(keys):
            raise ValueError(('개선용' if partition == 'development' else '평가용') + ' 정답을 1~' + str(cap) + '개 선택하세요')
        for key in keys:
            g = gold.get(key)
            if not g or K._target(g) != 'keyword' or g['partition'] != partition:
                raise ValueError('정답의 용도와 최종 확정 상태를 다시 확인하세요')
            selected[key] = copy.deepcopy(g)
    train_hashes = {gold[k]['content_hash'] for k in body['gold_keys']} | set(version.get('training_hashes', []))
    if any(gold[k]['content_hash'] in train_hashes or k in version.get('training_keys', []) for k in body['evaluation_keys']):
        raise ValueError('개선에 사용한 콘텐츠는 평가용으로 사용할 수 없습니다')
    for model in (version['model'], compiler):
        _, _, error = K._engine(model)
        if error:
            raise ValueError(error)
    with LOCK:
        status(team)
        before = K._get(STATE, team)
        if before and before['status'] == 'running':
            raise ValueError('이미 키워드 오토파일럿이 실행 중입니다')
        ident = K._id()
        run = {'id': ident, 'status': 'running', 'by': actor, 'created_at': time.time(),
               'version_id': version['id'], 'compiler_model': compiler, 'max_rounds': rounds,
               'gold_keys': body['gold_keys'], 'evaluation_keys': body['evaluation_keys'], 'gold': selected,
               'round': 0, 'phase': '기준 평가', 'history': [], 'best': None, 'stop_requested': False}
        ACTIVE.add((team, ident))
        try:
            if not K._store().compare_report(STATE, before, run, team=team):
                raise ValueError('다른 튜닝이 시작되었습니다 · 다시 불러오세요')
            threading.Thread(target=_loop, args=(run, team), daemon=True).start()
        except Exception:
            ACTIVE.discard((team, ident))
            raise
    return {'ok': True, 'tuning': {k: v for k, v in run.items() if k != 'gold'}}


def stop(team):
    run = status(team)
    if not run or run['status'] != 'running':
        raise ValueError('실행 중인 키워드 오토파일럿이 없습니다')
    _save(team, run['id'], stop_requested=True)
    return {'ok': True}


def _checkpoint(run, team):
    state = K._get(STATE, team)
    if not state or state['id'] != run['id'] or state.get('stop_requested'):
        raise TuningStopped('사용자 중지 · 저장된 후보 버전은 보존')
    current = K._current_gold(team)
    if any(current.get(k) != g for k, g in run['gold'].items()):
        raise TuningStopped('정답의 수정·확정 취소 감지 · 정답셋을 다시 확인하세요')


def _experiment(run, team, version_id, keys):
    _checkpoint(run, team)
    version = K._version(version_id, team)
    def stopped():
        state = K._get(STATE, team)
        return not state or state['id'] != run['id'] or state.get('stop_requested')
    result = K.start({'slots': [{'model': version['model'], 'version_id': version_id}],
                      'gold_keys': keys, 'blind': False}, team, run['by'], background=False,
                     gold_snapshot=run['gold'], should_stop=stopped)
    _checkpoint(run, team)
    experiment = K._get(K._key('run', result['id']), team)
    if experiment['status'] != 'done':
        raise ValueError('튜닝 실험이 중단되었습니다 · 실험 기록을 확인하세요')
    metrics = K._metrics(experiment)[0]
    if metrics['failed'] == len(keys):
        raise ValueError('모델 호출이 모두 실패했습니다 · 연결 설정과 실험 기록을 확인하세요')
    return result['id'], metrics


def _loop(run, team):
    ident = run['id']
    history, best, stalls = [], None, 0
    try:
        baseline_id, metrics = _experiment(run, team, run['version_id'], run['evaluation_keys'])
        best = {'version_id': run['version_id'], 'run_id': baseline_id,
                'matched': metrics['exact_matches'], 'total': metrics['exact_total']}
        if best['total'] != len(run['evaluation_keys']):
            raise ValueError('평가용 정답이 모두 평가되지 않았습니다')
        history.append(dict(best, round=0, improved=False, cost_usd=metrics['cost_usd']))
        _save(team, ident, best=best, history=history)
        reason = '평가용 정답의 키워드 표기·구성 100% 일치'
        for number in range(1, run['max_rounds'] + 1):
            if best['matched'] == best['total']:
                break
            _checkpoint(run, team)
            _save(team, ident, round=number, phase='개선용 재실행')
            development_id, _ = _experiment(run, team, best['version_id'], run['gold_keys'])
            _save(team, ident, phase='개선안 생성')
            proposal = K.compile_proposal({'version_id': best['version_id'], 'compiler_model': run['compiler_model'],
                                           'gold_keys': run['gold_keys'], 'comparison_run': development_id}, team, run['by'])['proposal']
            _checkpoint(run, team)
            version = K.create_version({'model': proposal['model'], 'rules': proposal['rules'],
                                         'title': '자동 튜닝 ' + str(number), 'parent_id': best['version_id'],
                                         'proposal_id': proposal['id'], 'note': '오토파일럿 후보 · 적용·채택 전 검수 필요'}, team, run['by'])['version']
            _save(team, ident, phase='후보 평가')
            result_id, metrics = _experiment(run, team, version['id'], run['evaluation_keys'])
            if metrics['exact_total'] != best['total']:
                raise ValueError('평가 대상이 달라졌습니다 · 튜닝 중단')
            improved = metrics['failed'] == 0 and metrics['exact_matches'] > best['matched']
            candidate = {'version_id': version['id'], 'run_id': result_id,
                         'matched': metrics['exact_matches'], 'total': metrics['exact_total']}
            history.append(dict(candidate, round=number, improved=improved, cost_usd=metrics['cost_usd']))
            if improved:
                best = candidate
            stalls = 0 if improved else stalls + 1
            _save(team, ident, best=best, history=history)
            if best['matched'] == best['total']:
                reason = '평가용 정답의 키워드 표기·구성 100% 일치'
                break
            if stalls >= 2:
                reason = '2회 연속 향상 없음 · 최고 후보 유지'
                break
            reason = '최대 반복 횟수 도달 · 최고 후보 확인 필요'
        _checkpoint(run, team)
        _save(team, ident, status='done', phase='튜닝 완료', reason=reason, finished_at=time.time())
    except TuningStopped as exc:
        _save(team, ident, status='stopped', reason=str(exc), finished_at=time.time())
    except Exception as exc:
        _save(team, ident, status='failed', reason=str(exc)[:400], finished_at=time.time())
    finally:
        with LOCK:
            ACTIVE.discard((team, ident))
