"""DNM ↔ 컨플루언스 주기 연동.

방향별 원본: 위키 → 프리즘은 등록표 표·사전 버전 라벨(라벨은 코드와 대조만), 프리즘 → 위키는
'프리즘 자동 기록' 제목 바로 아래 표 하나만 갱신. 승인(approve)은 자동으로 하지 않는다.
"""
import base64
import copy
import json
import os
import re
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from . import dnm, execution as EX

CONFIG = 'dnm_wiki'                    # 팀 없는 보고서: {enabled, team, policy_pages, registry_page, record_page, interval_min}
STATUS = 'dnm_wiki_status'
DEFAULT = {'enabled': False, 'team': None, 'interval_min': 30, 'registry_page': '', 'record_page': '',
           'policy_pages': ['365789408', '364314733', '278856094']}  # 131123 카테고리 · 13112 아이템 메타 · 인텐트 정의
ANCHOR = '프리즘 자동 기록'
TAG = '[prism-sync]'
LABELS = {'policy_version': (r'dnm-common-[0-9A-Za-z.-]*[0-9A-Za-z]', dnm.CONTRACT),
          'intent_dictionary_version': (r'intent-common-[0-9A-Za-z.-]*[0-9A-Za-z]', dnm.INTENTS),
          'category_dictionary_version': (r'category-common-[0-9A-Za-z.-]*[0-9A-Za-z]', dnm.CATEGORIES)}
COLUMNS = {'경로 ID': 'route_id', '수집 분류': 'ingestion_class', '판별 필드': 'field', '제공 여부': 'provided',
           '값': 'value', '적용 시작': 'effective_from', '담당': 'owner', '표본 근거': 'evidence',
           '승인자': 'approved_by', '승인일': 'approved_at', '키 검증': 'unique_key_verified'}
YES, NO = {'y', 'yes', '예', 'o', 'true', '제공'}, {'n', 'no', '아니오', 'x', 'false', '미제공'}
KST = timezone(timedelta(hours=9))
_LOCK = threading.Lock()


# ── ADF ──────────────────────────────────────────────────────────────
def text(node):
    if isinstance(node, list):
        return ''.join(text(n) for n in node)
    if not isinstance(node, dict):
        return ''
    if node.get('type') == 'text':
        return node.get('text', '')
    if node.get('type') == 'hardBreak':
        return '\n'
    return text(node.get('content') or [])


def _find_anchor(content):
    """'프리즘 자동 기록' 제목이 든 content 목록과 위치. 레이아웃·펼치기 안도 찾는다."""
    for i, node in enumerate(content or []):
        if not isinstance(node, dict):
            continue
        if node.get('type') == 'heading' and ANCHOR in text(node):
            return content, i
        found = _find_anchor(node.get('content'))
        if found:
            return found
    return None


def strip_record(adf):
    """기록 영역(제목 뒤 표 하나)을 뺀 사본. 라벨 추출과 '나머지 본문 불변' 검증에 쓴다."""
    adf = copy.deepcopy(adf)
    found = _find_anchor(adf.get('content'))
    if found:
        content, i = found
        if i + 1 < len(content) and content[i + 1].get('type') == 'table':
            del content[i + 1]
    return adf


def _tables(node):
    if isinstance(node, list):
        for n in node:
            yield from _tables(n)
    elif isinstance(node, dict):
        if node.get('type') == 'table':
            yield node
        else:
            yield from _tables(node.get('content') or [])


def table_rows(table):
    rows = [[text(c).strip() for c in r.get('content') or []] for r in table.get('content') or []]
    return rows[0] if rows else [], rows[1:]


def _cell(value, header=False):
    return {'type': 'tableHeader' if header else 'tableCell',
            'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': value}] if value else []}]}


def make_table(pairs):
    rows = [['항목', '값']] + [list(p) for p in pairs]
    return {'type': 'table', 'attrs': {'isNumberColumnEnabled': False, 'layout': 'default'},
            'content': [{'type': 'tableRow', 'content': [_cell(v, i == 0) for v in r]} for i, r in enumerate(rows)]}


def labels(adf):
    """현행 본문(Appendix 앞·기록 영역 제외)에서 라벨별 첫 값."""
    body = text(strip_record(adf).get('content') or []).split('Appendix')[0]
    out = {}
    for key, (pattern, _) in LABELS.items():
        m = re.search(pattern, body)
        if m:
            out[key] = m.group(0)
    return out


def _flag(value, column):
    v = value.strip().lower()
    if v in YES:
        return True
    if v in NO:
        return False
    raise ValueError(f'{column} 값은 Y 또는 N 이어야 합니다: {value!r}')


def registry(adf, page_id):
    """'경로 ID' 열이 있는 첫 표 → 등록표. 표가 없거나 행이 비면 None(파일 업로드 유지)."""
    for table in _tables(adf.get('content') or []):
        head, rows = table_rows(table)
        if '경로 ID' not in head:
            continue
        missing = [c for c in COLUMNS if c not in head]
        if missing:
            raise ValueError('등록표 열이 없습니다: ' + ', '.join(missing))
        routes = {}
        for raw in rows:
            r = {COLUMNS[h]: v for h, v in zip(head, raw) if h in COLUMNS}
            if not r.get('route_id'):
                continue
            try:
                value = json.loads(r['value'])
            except ValueError:
                value = r['value']
            entry = {k: r[k] for k in ('route_id', 'ingestion_class', 'effective_from', 'owner',
                                       'evidence', 'approved_by', 'approved_at')}
            entry['unique_key_verified'] = r['unique_key_verified'].strip().lower() in YES
            row = routes.setdefault(r['route_id'], dict(entry, matches={}))
            if {k: row[k] for k in entry} != entry:
                raise ValueError(f"같은 경로 {r['route_id']} 의 행끼리 값이 다릅니다")
            provided = _flag(r['provided'], '제공 여부')
            row['matches'][r['field']] = {'provided': provided, 'value': value} if provided else {'provided': False}
        if not routes:
            return None
        entries = list(routes.values())
        return dnm.validate_registry({'version': f'wiki-{page_id}-' + EX.digest(entries)[:12], 'entries': entries})
    return None


# ── Confluence REST ──────────────────────────────────────────────────
def _api(method, path, body=None):
    email, token = os.environ.get('PRISM_CONFLUENCE_EMAIL'), os.environ.get('PRISM_CONFLUENCE_TOKEN')
    if not email or not token:
        raise ValueError('PRISM_CONFLUENCE_EMAIL·PRISM_CONFLUENCE_TOKEN 이 설정되지 않았습니다')
    base = os.environ.get('PRISM_CONFLUENCE_BASE', 'https://daumcorp.atlassian.net')
    req = urllib.request.Request(base + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': 'Basic ' + base64.b64encode(f'{email}:{token}'.encode()).decode(),
                                          'Accept': 'application/json', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b'{}')


def read_page(page_id):
    page = _api('GET', f'/wiki/api/v2/pages/{page_id}?body-format=atlas_doc_format')
    return page, json.loads(page['body']['atlas_doc_format']['value'])


def write_record(page_id, pairs, api=None):
    """기록 영역만 바꿔 저장. 표 내용(기록 시각 제외)이 같으면 쓰지 않는다. 반환: 썼는지."""
    page, adf = (api or read_page)(page_id)
    found = _find_anchor(adf.get('content'))
    if not found:
        raise ValueError(f"기록 문서 {page_id} 에 '{ANCHOR}' 제목이 없습니다")
    content, i = found
    old = content[i + 1] if i + 1 < len(content) and content[i + 1].get('type') == 'table' else None
    stable = [list(p) for p in pairs if p[0] != '기록 시각']
    if old is not None and [r for r in table_rows(old)[1] if r[:1] != ['기록 시각']] == stable:
        return False
    new = copy.deepcopy(adf)
    content, i = _find_anchor(new['content'])
    content[i + 1:i + 2 if old is not None else i + 1] = [make_table(pairs)]
    if strip_record(new) != strip_record(adf):          # 사람이 쓴 본문은 한 글자도 바뀌면 안 된다
        raise ValueError('기록 영역 밖 본문이 바뀌어 저장을 중단했습니다')
    _api('PUT', f'/wiki/api/v2/pages/{page_id}', {
        'id': str(page_id), 'status': 'current', 'title': page['title'],
        'body': {'representation': 'atlas_doc_format', 'value': json.dumps(new, ensure_ascii=False)},
        # 버전 번호가 그 사이 바뀌었으면 컨플루언스가 409 로 거절한다(낙관적 잠금)
        'version': {'number': page['version']['number'] + 1, 'message': TAG + ' DNM 정책·등록표 기록'}})
    return True


# ── 동기화 ───────────────────────────────────────────────────────────
def config(store):
    return dict(DEFAULT, **(store.get_report(CONFIG) or {}))


def save_config(store, data, team):
    cfg = config(store)
    for k in ('registry_page', 'record_page'):
        if k in data:
            cfg[k] = re.sub(r'\D', '', str(data[k] or ''))
    if 'policy_pages' in data:
        cfg['policy_pages'] = [p for p in re.findall(r'\d+', str(data['policy_pages']))]
    if 'interval_min' in data:
        cfg['interval_min'] = max(5, int(data['interval_min'] or 30))
    if 'enabled' in data:
        cfg['enabled'] = data['enabled'] is True
    cfg['team'] = team
    store.save_report(CONFIG, cfg)
    return cfg


def _mock(policy):
    ex = (policy or {}).get('execution') or {}
    return any(c.get('mock') for c in [ex.get('main') or {}] + [v.get('client') or {} for v in (ex.get('calls') or {}).values()])


def sync(store, llm, read=None, write=None):
    """한 번 확인. 등록표·정책이 바뀌면 새 버전으로 configure(승인은 초기화·재승인 필요)."""
    read, write = read or read_page, write or write_record
    with _LOCK:
        cfg = config(store)
        runtime = dnm.Runtime(store, cfg['team'])
        status = {'checked_at': time.time(), 'pages': [], 'mismatch': [], 'actions': [], 'errors': []}
        for pid in cfg['policy_pages']:
            try:
                page, adf = read(pid)
                found = labels(adf)
                status['pages'].append({'id': pid, 'title': page.get('title'), 'labels': found})
                status['mismatch'] += [f"{page.get('title') or pid} · {k}={v} (코드 {LABELS[k][1]})"
                                       for k, v in found.items() if v != LABELS[k][1]]
            except Exception as e:
                status['errors'].append(f'정책 문서 {pid} 읽기 실패: {str(e)[:160]}')
        control = runtime._get('dnm_control') or {'revision': 0}
        new_registry = new_policy = None
        if cfg['registry_page']:
            try:
                reg = registry(read(cfg['registry_page'])[1], cfg['registry_page'])
                if reg and reg['version'] != (control.get('registry') or {}).get('version'):
                    new_registry = reg
            except Exception as e:
                status['errors'].append(f'등록표 반영 실패: {str(e)[:200]}')
        try:
            policy = dnm.prepare_policy(llm)
            current = control.get('policy') or {}
            if _mock(policy) and current and not _mock(current):
                status['errors'].append('모의 모델 정책으로 실제 정책을 덮지 않았습니다 · 모델 키 확인')
            elif policy['policy_version'] != current.get('policy_version'):
                new_policy = policy
        except Exception as e:
            status['errors'].append(f'정책 준비 실패: {str(e)[:200]}')
        if new_registry or new_policy:
            try:
                control = runtime.configure(new_registry, new_policy, control['revision'])
                status['actions'] += (['등록표 ' + new_registry['version']] if new_registry else []) + \
                                     (['정책 ' + new_policy['policy_version']] if new_policy else [])
            except ValueError as e:
                status['errors'].append(f'설정 저장 실패: {str(e)[:200]}')
        if cfg['record_page'] and control.get('policy'):
            pairs = [('policy_version', control['policy']['policy_version']),
                     ('contract_version', dnm.CONTRACT), ('intent_dictionary_version', dnm.INTENTS),
                     ('category_dictionary_version', dnm.CATEGORIES),
                     ('등록표 버전', (control.get('registry') or {}).get('version') or '미연결'),
                     ('전환 승인', '승인됨' if control.get('approval') else '재승인 필요'),
                     ('기록 시각', datetime.now(KST).strftime('%Y-%m-%d %H:%M KST'))]
            try:
                if write(cfg['record_page'], pairs):
                    status['actions'].append('위키 기록 갱신')
            except Exception as e:
                status['errors'].append(f'위키 기록 실패: {str(e)[:200]}')
        store.save_report(STATUS, status)
        return status


def start_scheduler(server):
    """설정에서 켠 경우에만 interval_min 간격으로 sync. 서버 시작 시 1회 호출."""
    def loop():
        while True:
            time.sleep(60)
            try:
                st = server.get_store()
                cfg = config(st)
                last = (st.get_report(STATUS) or {}).get('checked_at', 0)
                if cfg['enabled'] and time.time() - last >= cfg['interval_min'] * 60:
                    sync(st, server.llm_for_model('', server.Handler.server_mock)[0])
            except Exception as e:
                print(f'  [dnm-wiki] 확인 실패: {e}')
    threading.Thread(target=loop, name='prism-dnm-wiki', daemon=True).start()
