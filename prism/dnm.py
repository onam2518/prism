"""DNM 원천 식별·불변 작업·발행 상태. 일반 실험 콘텐츠와 별도의 명시적 실행 경로."""
import copy
import math
import time
import uuid
from datetime import datetime

from . import execution as EX, meta_contract as MC, dictionaries as D, prompts as P
from .schema import Content, source_key_status, source_prefix

CONTRACT = 'dnm-common-2026-09-29-r3'
INTENTS = 'intent-common-68-2026-09-29'
CATEGORIES = 'category-common-21-2026-09-29-r3'
CALLS = {'summary': 'summary', 'entities': 'entities', 'intent': 'intent', 'content_category': 'category'}
DONE = ('success', 'no_value')


def _timestamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            raise ValueError()
        return dt.timestamp()
    except (ValueError, TypeError):
        raise ValueError('적용 시각에는 시간대가 포함된 ISO 날짜가 필요합니다')


def validate_registry(registry):
    if not isinstance(registry, dict) or not isinstance(registry.get('entries'), list):
        raise ValueError('등록표에는 version과 entries가 필요합니다')
    if not isinstance(registry.get('version'), str) or not registry['version'].strip():
        raise ValueError('등록표 버전이 필요합니다')
    seen = set()
    for row in registry['entries']:
        if not isinstance(row, dict) or not isinstance(row.get('route_id'), str) or not row['route_id']:
            raise ValueError('실제 인입 경로 식별자가 필요합니다')
        if row['route_id'] in seen:
            raise ValueError('같은 경로를 중복 등록할 수 없습니다')
        seen.add(row['route_id'])
        if row.get('ingestion_class') not in ('partner_news', 'search_news', 'other'):
            raise ValueError('허용하지 않는 수집 분류입니다')
        if any(not isinstance(row.get(k), str) or not row[k].strip()
               for k in ('owner', 'evidence', 'approved_by', 'approved_at')):
            raise ValueError('경로 담당·표본 근거·승인자·확인일이 필요합니다')
        _timestamp(row['effective_from']); _timestamp(row['approved_at'])
        if not isinstance(row.get('matches'), dict) or not row['matches']:
            raise ValueError('원천 판별 필드와 제공 여부·값의 매핑이 필요합니다')
        for field, spec in row['matches'].items():
            if not isinstance(field, str) or not isinstance(spec, dict) or type(spec.get('provided')) is not bool:
                raise ValueError('원천 필드 매핑에는 provided가 필요합니다')
            if spec['provided'] and 'value' not in spec:
                raise ValueError('제공된 필드는 null을 포함한 원래 value를 명시해야 합니다')
    return copy.deepcopy(registry)


def classify(event, registry, now=None):
    fields = event.get('source_fields') or {}
    key = fields.get('item_unique_key')
    out = {'item_unique_key': key, 'source_prefix': source_prefix(key),
           'source_key_status': source_key_status(key), 'source_fields': copy.deepcopy(fields),
           'ingestion_class': 'unresolved', 'scope_status': 'unresolved',
           'source_registry_version': (registry or {}).get('version'), 'decision': 'pending_source'}
    row = next((r for r in (registry or {}).get('entries', []) if r['route_id'] == event.get('route_id')), None)
    if not row or _timestamp(row['effective_from']) > (time.time() if now is None else now):
        return out
    if any((k in fields) != v['provided'] or (v['provided'] and fields[k] != v['value'])
           for k, v in row['matches'].items()):
        return out
    out['ingestion_class'] = row['ingestion_class']
    out['scope_status'] = 'excluded' if row['ingestion_class'] == 'other' else 'eligible'
    if out['scope_status'] == 'excluded':
        out['decision'] = 'skipped_out_of_scope'
    elif out['source_key_status'] in ('missing', 'invalid'):
        out['decision'] = 'pending_source_key'
    elif out['source_key_status'] == 'unregistered' and row.get('unique_key_verified') is not True:
        out['decision'] = 'pending_source_key'
    else:
        out['decision'] = 'run'
    return out


def input_snapshot(content):
    c = Content.from_dict(content)
    return {'calls': {call: P.call_user(call, c) for call in CALLS.values()},
            'input_aux': c.input_aux}


def validate_value(field, value):
    if field == 'summary':
        return isinstance(value, str)
    if not isinstance(value, list):
        return False
    if field == 'intent':
        return all(isinstance(v, str) and v in D.intent_categories() for v in value) and len(set(value)) == len(value)
    if field == 'entities':
        return all(isinstance(v, dict) for v in value) and MC.clean_entities(value) == value
    if field == 'content_category':
        return all(isinstance(v, dict) for v in value) and MC.clean_categories(value) == value
    return False


def prepare_policy(llm):
    _, snapshot = EX.capture(llm, [])
    if not snapshot['four_calls'] or any('error' in c for c in snapshot['calls'].values()):
        raise ValueError('독립 4종의 모델·프롬프트 설정을 모두 확인해야 합니다')
    snapshot.pop('quality', None); snapshot.pop('quality_version', None); snapshot.pop('single_system', None)
    # DNM has no hidden model fallback or HTTP retry: the durable job owns all three attempts.
    for settings in [snapshot['main']] + [v['client'] for v in snapshot['calls'].values()]:
        settings['config'].setdefault('retry', {}).update(max_retries=0, max_format_retries=0)
    manifest = {'contract_version': CONTRACT, 'intent_dictionary_version': INTENTS,
                'category_dictionary_version': CATEGORIES, 'entity_format_version': 'v1',
                'response_schema_version': 'common-meta-v2', 'preprocessing_version': snapshot['engine'],
                'execution': snapshot}
    manifest['policy_version'] = CONTRACT + '+' + EX.digest(manifest)[:20]
    return manifest


class Runtime:
    def __init__(self, store, team=None):
        self.store, self.team = store, team

    def _get(self, kind):
        return self.store.get_report(kind, team=self.team)

    def _change(self, kind, change, guard=None):
        for _ in range(8):
            before = self._get(kind)
            after, result = change(copy.deepcopy(before))
            if after == before:
                return result
            if self.store.compare_report(kind, before, after, team=self.team, guard=guard):
                return result
            if guard is not None and self._get(guard[0]) != guard[1]:
                raise ValueError("실행 중 등록표·정책이 바뀌었습니다. 현재 상태로 다시 처리하세요")
        raise ValueError('동시 변경이 많습니다. 현재 상태를 확인한 뒤 다시 시도하세요')

    def register(self, kind, version, value):
        key = 'dnm_' + kind + '_' + EX.digest(version)
        def write(old):
            if old is not None and old != value:
                raise ValueError('발급한 버전의 내용을 바꿀 수 없습니다')
            return copy.deepcopy(value), value
        return self._change(key, write)

    def configure(self, registry=None, policy=None, expected_revision=None):
        if registry is not None:
            registry = validate_registry(registry)
            self.register('registry', registry['version'], registry)
        if policy is not None:
            base = {k: v for k, v in policy.items() if k != 'policy_version'}
            if policy.get('contract_version') != CONTRACT or policy.get('policy_version') != CONTRACT + '+' + EX.digest(base)[:20]:
                raise ValueError('정책 실행 명세 지문이 일치하지 않습니다')
            if policy['execution']['engine'] != EX.engine_version():
                raise ValueError('현재 코드로 생성한 실행 명세가 필요합니다')
            self.register('policy', policy['policy_version'], policy)
        def change(old):
            old = old or {'revision': 0}
            if expected_revision != old['revision']:
                raise ValueError('전환 설정이 바뀌었습니다. 다시 확인하세요')
            new = dict(old, revision=old['revision'] + 1, approval=None)
            if registry is not None:
                new['registry'] = registry
            if policy is not None:
                new['policy'] = policy
            return new, new
        return self._change('dnm_control', change)

    def approve(self, evidence, expected_revision):
        """검증 결과를 만들어내지 않는다. 담당자가 제공한 근거와 적용 버전을 결속한다."""
        if not isinstance(evidence, dict):
            raise ValueError('운영 전환 검증 기록이 필요합니다')
        for role in ('platform_planning', 'datahub'):
            record = evidence.get(role) or {}
            if any(not record.get(k) for k in ('owner', 'result_link', 'confirmed_at')):
                raise ValueError('플랫폼기획·데이터허브의 검증 기록이 모두 필요합니다')
            _timestamp(record['confirmed_at'])
        if any(not evidence.get(k) for k in ('evaluation_run', 'sample_size', 'targets', 'tolerances',
                                             'applies_at', 'rollback_policy_version', 'rollback_result')):
            raise ValueError('평가 표본·목표·허용 오차·적용 시각·되돌리기 기록이 필요합니다')
        _timestamp(evidence['applies_at'])
        def change(old):
            if not old or old['revision'] != expected_revision or not old.get('registry') or not old.get('policy'):
                raise ValueError('등록표·정책 설정을 먼저 확인하세요')
            version = old['policy']['policy_version']
            if evidence.get('policy_version') != version or evidence.get('source_registry_version') != old['registry']['version']:
                raise ValueError('승인 기록의 정책·등록표 버전이 다릅니다')
            execution = old['policy']['execution']
            if any(c['mock'] for c in [execution['main']] + [v['client'] for v in execution['calls'].values()]):
                raise ValueError('모의 모델은 운영 전환할 수 없습니다')
            rollback = self._get('dnm_policy_' + EX.digest(evidence['rollback_policy_version']))
            if not rollback or rollback['policy_version'] == version:
                raise ValueError('등록된 이전 정책과 되돌리기 검증 기록이 필요합니다')
            run = self.store.eval_run_get(evidence['evaluation_run'], self.team)
            if not run or run.get('status') != 'done':
                raise ValueError('완료된 고정 평가 결과가 필요합니다')
            snapshot = self._get('eval_snapshot_' + str(evidence['evaluation_run']))
            if not snapshot or not snapshot.get('rows') or len(snapshot['rows']) != evidence['sample_size']:
                raise ValueError('평가 표본 수가 고정 스냅샷과 다릅니다')
            if snapshot.get('policy_version') != version or snapshot.get('dnm_policy') != old['policy']:
                raise ValueError('승인 정책과 같은 실행 명세로 평가해야 합니다')
            if (run.get('metrics') or {}).get('basis_fingerprint') != EX.digest(snapshot):
                raise ValueError('평가 스냅샷의 지문이 일치하지 않습니다')
            protocol = snapshot.get('protocol') or {}
            if not protocol or any(protocol.get(k) != evidence.get(k) for k in ('sample_size', 'targets', 'tolerances')):
                raise ValueError('평가 전에 고정한 표본·목표·허용 오차와 승인 기록이 다릅니다')
            from .evalops import eval_run_report, _validate_protocol
            _validate_protocol(protocol, len(snapshot['rows']))
            report = eval_run_report(evidence['evaluation_run'], self.team)
            if report.get('evaluated') != len(snapshot['rows']) or any(not report.get(k) for k in ('intent_n', 'cat_n', 'ent_n', 'summary_n')):
                raise ValueError('모든 표본과 네 메타 항목의 평가 결과가 필요합니다')
            for metric, target in protocol['targets'].items():
                actual = report.get(metric)
                tolerance = protocol['tolerances'][metric]
                if (type(actual) not in (int, float) or not math.isfinite(actual)
                        or ('min' in target and actual < target['min'] - tolerance)
                        or ('max' in target and actual > target['max'] + tolerance)):
                    raise ValueError('평가 목표 미충족: ' + metric)
            if any(not training_fields(r.get('content') or {}, r.get('expected') or {}, version) for r in snapshot['rows']):
                raise ValueError('현재 정책에 확정된 평가 정답이 필요합니다')
            new = dict(old, revision=old['revision'] + 1, approval=copy.deepcopy(evidence))
            return new, new
        return self._change('dnm_control', change)

    @staticmethod
    def _key(item_key):
        return 'dnm_item_' + EX.digest(item_key)

    def begin(self, event):
        if (not isinstance(event, dict) or not isinstance(event.get('content'), dict)
                or not isinstance(event.get('source_fields'), dict)
                or not isinstance(event.get('event_id'), str) or not event['event_id']
                or type(event.get('source_revision')) is not int or event['source_revision'] < 0):
            raise ValueError('원천 event_id·source_revision·content·source_fields가 필요합니다')
        control_raw = self._get('dnm_control')
        control = control_raw or {}
        policy = control.get('policy') or {}
        source = classify(event, control.get('registry'))
        item_key = source['item_unique_key']
        if source['source_key_status'] in ('missing', 'invalid'):
            self.register('pending_event', event['event_id'], {'event': event, 'source': source})
            return dict(source, publication_revision=0, publishable=False)
        policy_version = policy.get('policy_version')
        def change(old):
            old = old or {'publication_revision': 0, 'bundles': {}}
            prior_event = old.get('event') or {}
            selected_event = prior_event if event['source_revision'] < prior_event.get('source_revision', -1) else event
            if event['source_revision'] == prior_event.get('source_revision') and event != prior_event:
                raise ValueError('같은 원천 revision에 다른 이벤트를 사용할 수 없습니다')
            snapshot = input_snapshot(selected_event['content'])
            revision = EX.digest(snapshot)
            bundle_id = EX.digest([revision, policy_version])
            selected_source = classify(selected_event, control.get('registry'))
            before = copy.deepcopy(old)
            override = old.get('rollback') or {}
            selected_id = bundle_id
            if override.get('control_revision') == control.get('revision') and override.get('input_revision') == revision:
                selected_id = override['bundle_id']
            previous = old['bundles'].get(old.get('current'), {})
            if bundle_id not in old['bundles']:
                jobs = {}
                for field in MC.FIELDS:
                    manual = (previous.get('jobs', {}).get(field) or {}).get('origin') == 'manual'
                    status = ('configuration_error' if not policy else
                              'insufficient_input' if not (Content.from_dict(selected_event['content']).title or Content.from_dict(selected_event['content']).body) else 'pending')
                    jobs[field] = {'value': '' if field == 'summary' else [], 'status': 'pending' if manual else status,
                                   'origin': 'manual' if manual else 'auto', 'attempt': 0,
                                   'reason': 'manual_review_required' if manual else '', 'attempts': []}
                old['bundles'][bundle_id] = {'input_revision': revision, 'input_snapshot': snapshot,
                                             'policy_version': policy_version, 'jobs': jobs}
            old.update(event=copy.deepcopy(selected_event), source=selected_source, current=selected_id,
                       control_revision=control.get('revision', 0))
            if before != old:
                old['publication_revision'] += 1
            return old, self._publication(old, control)
        return self._change(self._key(item_key), change, guard=("dnm_control", control_raw))

    def current(self, item_key):
        old = self._get(self._key(item_key))
        if not old:
            return None
        # Reading a publication also reconciles registry withdrawal/return and policy changes.
        return self.begin(old['event'])

    @staticmethod
    def _publication(state, control):
        bundle = state['bundles'][state['current']]
        source = copy.deepcopy(state['source'])
        allowed = source['decision'] == 'run'
        approval = control.get('approval') or {}
        approved_versions = (approval.get('policy_version'), approval.get('rollback_policy_version'))
        publishable = bool(allowed and approval and bundle['policy_version'] in approved_versions
                           and _timestamp(approval['applies_at']) <= time.time())
        jobs = bundle['jobs']
        return dict(source, input_revision=bundle['input_revision'], policy_version=bundle['policy_version'],
                    contract_version=CONTRACT, intent_dictionary_version=INTENTS,
                    category_dictionary_version=CATEGORIES, entity_format_version='v1',
                    response_schema_version='common-meta-v2', publication_revision=state['publication_revision'],
                    publishable=publishable, execution_allowed=allowed,
                    meta_status={f: j['status'] for f, j in jobs.items()},
                    origin={f: j['origin'] for f, j in jobs.items()},
                    manual_fields={f: {k: j.get(k) for k in ('confirmed_by', 'confirmed_at')}
                                   for f, j in jobs.items() if j['origin'] == 'manual'},
                    attempts={f: j['attempt'] for f, j in jobs.items()},
                    manual_review_required=[f for f, j in jobs.items() if j.get('reason') == 'manual_review_required'],
                    **{f: j['value'] if allowed and j['status'] in DONE else ('' if f == 'summary' else [])
                       for f, j in jobs.items()})

    def claim(self, item_key, bundle_id, field, timeout=120):
        now, token = time.time(), uuid.uuid4().hex
        control = self._get("dnm_control")
        def change(old):
            if (not old or old.get('current') != bundle_id or old['source']['decision'] != 'run'
                    or old.get('control_revision') != (control or {}).get('revision', 0)):
                return old, None
            job = old['bundles'][bundle_id]['jobs'][field]
            if (job['status'] in DONE or job.get('reason') or job['status'] in ('configuration_error', 'insufficient_input')
                    or job['attempt'] >= 3 or job.get('lease_until', 0) > now):
                return old, None
            job.update(status='pending', attempt=job['attempt'] + 1, token=token,
                       lease_until=now + max(30, timeout + 30))
            old['publication_revision'] += 1
            return old, token
        return self._change(self._key(item_key), change, guard=("dnm_control", control))

    def complete(self, item_key, bundle_id, field, token, status, value, detail='', measurement=None):
        if status not in ('success', 'no_value', 'invalid_output', 'call_failed', 'configuration_error'):
            raise ValueError('지원하지 않는 작업 결과 상태입니다')
        if status in DONE and (not validate_value(field, value) or bool(value) != (status == 'success')):
            raise ValueError('상태와 메타 값이 일치하지 않습니다')
        def change(old):
            job = old['bundles'][bundle_id]['jobs'][field]
            record = {'token': token, 'status': status, 'value': value, 'detail': detail, 'measurement': measurement or {}, 'finished': time.time()}
            if any(r['token'] == token for r in job['attempts']):
                return old, None
            job['attempts'].append(record)
            if token == job.get('token') and job['origin'] == 'auto':
                job.update(status=status, value=value if status in DONE else ('' if field == 'summary' else []),
                           lease_until=0, token=None)
                if job['attempt'] >= 3 and status not in DONE:
                    job['reason'] = 'attempts_exhausted'
                if old['current'] == bundle_id and old['source']['decision'] == 'run':
                    old['publication_revision'] += 1
            return old, None
        self._change(self._key(item_key), change)

    def manual(self, item_key, field, value, input_revision, policy_version, who, automatic=False):
        self.current(item_key)
        control = self._get("dnm_control")
        if field not in MC.FIELDS or not who or (not automatic and not validate_value(field, value)):
            raise ValueError('확정자와 유효한 메타 값이 필요합니다')
        def change(old):
            if not old:
                raise ValueError('콘텐츠를 찾을 수 없습니다')
            b = old['bundles'][old['current']]
            if (b['input_revision'], b['policy_version']) != (input_revision, policy_version):
                raise ValueError('입력·정책 버전이 바뀌었습니다. 현재 값을 다시 확인하세요')
            j = b['jobs'][field]
            if automatic and j['attempt'] >= 3:
                raise ValueError('같은 작업의 시도 횟수를 초기화할 수 없습니다')
            previous = {k: copy.deepcopy(v) for k, v in j.items() if k != 'manual_history'}
            j.setdefault('manual_history', []).append(previous)
            j.update(value=('' if field == 'summary' else []) if automatic else copy.deepcopy(value),
                     status='pending' if automatic else ('success' if value else 'no_value'),
                     origin='auto' if automatic else 'manual', reason='', token=None, lease_until=0,
                     confirmed_by=who, confirmed_at=time.time())
            old['publication_revision'] += 1
            return old, None
        self._change(self._key(item_key), change, guard=("dnm_control", control))
        return self.current(item_key)

    def execute(self, event, resolve):
        current = self.begin(event)
        if not current.get('execution_allowed'):
            return current
        control = self._get('dnm_control') or {}
        policy = control.get('policy') or {}
        if not policy:
            return current
        key = current['item_unique_key']
        state = self._get(self._key(key))
        bundle_id = state['current']
        try:
            client = EX.restore(policy['execution'], resolve)
        except ValueError:
            def configuration_error(old):
                if old['current'] == bundle_id:
                    for job in old['bundles'][bundle_id]['jobs'].values():
                        if job['status'] not in DONE and job['origin'] != 'manual':
                            job.update(status='configuration_error', reason='configuration_error')
                    old['publication_revision'] += 1
                return old, None
            self._change(self._key(key), configuration_error)
            return self.current(key)
        from concurrent.futures import ThreadPoolExecutor
        def extract(field):
            call = CALLS[field]
            llm = client.execution['routes'][call]
            user = state['bundles'][bundle_id]['input_snapshot']['calls'][call]
            while True:
                # Registry/policy changes can withdraw this item while another field is running.
                latest = self.current(key)
                if latest['policy_version'] != policy['policy_version'] or not latest['execution_allowed']:
                    return
                token = self.claim(key, bundle_id, field, timeout=getattr(llm, 'timeout', 120))
                if token is None:
                    return
                result = attempt(llm, policy['execution']['calls'][call]['system'], user, field)
                value, status, detail = result['value'], result['status'], result['detail']
                self.complete(key, bundle_id, field, token, status, value, detail, measurement=result["measurement"])
                if status in DONE or detail in ('auth', 'billing', 'credit_exhausted'):
                    return
        def work(field):
            try:
                extract(field)
            finally:
                close = getattr(self.store, 'close_thread_connection', None)
                if close:
                    close()
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(work, MC.FIELDS))
        return self.current(key)


    def rollback(self, item_key, policy_version, expected_revision):
        self.current(item_key)
        control = self._get('dnm_control') or {}
        approval = control.get('approval') or {}
        if policy_version != approval.get('rollback_policy_version'):
            raise ValueError('승인 기록의 되돌릴 정책만 선택할 수 있습니다')
        def change(old):
            if not old or old['publication_revision'] != expected_revision:
                raise ValueError('발행 버전이 바뀌었습니다. 다시 확인하세요')
            current = old['bundles'][old['current']]
            target = next(((key, b) for key, b in old['bundles'].items()
                           if b['input_revision'] == current['input_revision'] and b['policy_version'] == policy_version), None)
            if not target:
                raise ValueError('현재 입력에 해당하는 과거 정책 묶음이 없습니다')
            old['current'] = target[0]
            old['rollback'] = {'bundle_id': target[0], 'input_revision': current['input_revision'],
                               'control_revision': control['revision']}
            old['publication_revision'] += 1
            return old, None
        self._change(self._key(item_key), change, guard=("dnm_control", control))
        return self.current(item_key)


    def golden(self, item_key):
        current = self.current(item_key)
        if not current or current['scope_status'] != 'eligible':
            raise ValueError('대상 콘텐츠의 현재 메타만 정답으로 등록할 수 있습니다')
        state = self._get(self._key(item_key))
        jobs = state['bundles'][state['current']]['jobs']
        confirmed = {f: j for f, j in jobs.items() if j['origin'] == 'manual' and j['status'] in DONE}
        if not confirmed:
            raise ValueError('현재 입력·정책에 수동 확정된 항목이 없습니다')
        expected = {f: j['value'] for f, j in confirmed.items()}
        expected.update(contract_version=CONTRACT, policy_version=current['policy_version'],
                        input_revision=current['input_revision'], intent_dictionary_version=INTENTS,
                        category_dictionary_version=CATEGORIES,
                        meta_status={f: j['status'] for f, j in confirmed.items()},
                        confirmed_fields={f: {'by': j['confirmed_by'], 'at': j['confirmed_at'],
                                              'input_revision': current['input_revision'],
                                              'policy_version': current['policy_version']}
                                          for f, j in confirmed.items()})
        content = dict(state['event']['content'], source_fields=state['event']['source_fields'])
        content.update({k: v for k, v in state['event']['source_fields'].items()
                        if k in ('item_unique_key', 'service_code', 'cp_type')})
        return {'content': content, 'expected': expected}


def training_fields(content, expected, policy_version):
    if expected.get('contract_version') != CONTRACT:
        return None
    if (not policy_version or expected.get('policy_version') != policy_version
            or expected.get('input_revision') != EX.digest(input_snapshot(content))
            or expected.get('intent_dictionary_version') != INTENTS
            or expected.get('category_dictionary_version') != CATEGORIES):
        return {}
    result = {}
    for field, evidence in (expected.get('confirmed_fields') or {}).items():
        if field == 'intent' and expected.get('intent_review') == 'needed':
            continue
        if (field in MC.FIELDS and evidence.get('by') and evidence.get('at')
                and evidence.get('input_revision') == expected['input_revision']
                and evidence.get('policy_version') == policy_version
                and expected.get('meta_status', {}).get(field) in DONE
                and validate_value(field, expected.get(field))):
            result[field] = expected[field]
    return result


def consumer_row(content, publication, current_policy, current_input):
    """소비처는 전달한 현재 키·입력·정책과 publication을 결속해 사용한다."""
    return {'content_ref': content, 'item_meta': publication,
            '_dnm_policy_version': current_policy, '_dnm_input_revision': current_input,
            '_dnm_item_unique_key': (content.get('source_fields') or content).get('item_unique_key')}


def attempt(llm, system, user, field):
    value, status, detail = ('' if field == 'summary' else []), 'call_failed', ''
    started = time.monotonic()
    measurement = {'cost_usd': 0, 'tokens_in': 0, 'tokens_out': 0}
    try:
        obj, res = llm.complete_json(system, user, tag='item_' + CALLS[field])
        measurement.update(cost_usd=getattr(res, 'cost_usd', 0), tokens_in=getattr(res, 'in_tok', 0),
                           tokens_out=getattr(res, 'out_tok', 0))
        if obj.get('_fail'):
            detail = str(obj.get('_fail_kind') or getattr(res, 'fail_kind', '') or obj['_fail'])[:150]
            status = 'invalid_output' if detail in ('parse_empty', 'contract_miss') else 'call_failed'
        elif field not in obj or not validate_value(field, obj[field]):
            status = 'invalid_output'
        else:
            value = obj[field]; status = 'success' if value else 'no_value'
    except Exception as exc:
        detail = type(exc).__name__
    measurement['latency_ms'] = (time.monotonic() - started) * 1000
    return {'status': status, 'value': value, 'detail': detail, 'measurement': measurement}


def evaluate_content(content, llm):
    """고정 평가도 운영과 같은 단일 시도·검증 함수와 축별 3회 상한을 쓴다."""
    policy = llm.dnm_policy
    snapshot = input_snapshot(content)
    c = Content.from_dict(content)
    results, values, statuses, counts = [], {}, {}, {}
    for field, call in CALLS.items():
        values[field] = '' if field == 'summary' else []
        statuses[field], counts[field] = 'insufficient_input', 0
        if not (c.title or c.body):
            continue
        route = llm.execution['routes'][call]
        for _ in range(3):
            result = attempt(route, policy['execution']['calls'][call]['system'], snapshot['calls'][call], field)
            counts[field] += 1
            values[field], statuses[field] = result['value'], result['status']
            results.append(result)
            if result['status'] in DONE or result['detail'] in ('auth', 'billing', 'credit_exhausted'):
                break
    im = dict(values, meta_status=statuses, input_revision=EX.digest(snapshot),
              policy_version=policy['policy_version'], contract_version=CONTRACT,
              run_manifest={'attempts': counts, 'execution_version': EX.digest(policy['execution'])})
    measures = [r['measurement'] for r in results]
    return {'content_ref': c.ref(), 'quality_meta': {}, 'item_meta': im,
            'trace': {'cost_usd': sum(m['cost_usd'] for m in measures),
                      'tokens': {'in': sum(m['tokens_in'] for m in measures), 'out': sum(m['tokens_out'] for m in measures)},
                      'latency_ms': {'total': sum(m['latency_ms'] for m in measures)},
                      'fallbacks': [f + '_fail' for f in MC.FIELDS if statuses[f] not in DONE]}}
