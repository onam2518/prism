"""키워드 실험실 · 구글 시트 연동 1단계(시트 행 읽기 → 대상 선별 → 결과 기재).

시트 쪽은 Apps Script 웹앱(docs/SHEET_SYNC.md) · GET 으로 행을 읽고 POST 로 결과 칸을 쓴다.
팀별 설정은 report `keyword_lab_sheet` = {url, key} · 키는 응답·로그·오류 문구에 내보내지 않는다.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import keywordlab as KL
from .store import content_hash

CONFIG = 'keyword_lab_sheet'
URL_RE = re.compile(r'https://script\.google\.com/(?:a/macros/[A-Za-z0-9.-]+/|macros/)s/[A-Za-z0-9_-]+/exec')
TEMPLATE_URL = 'https://docs.google.com/spreadsheets/d/10v-uIRGSiqpLXv3EbQIEvyMWufVh67b6wiiC9Oiifk0/copy'
MAX_ROWS = 5000
WRITE_CHUNK = 50
TIMEOUT = 30
LIMITS = {'제목': 300, '본문': 20000}           # 그 외 입력 칸은 2000
INPUT_COLUMNS = ('콘텐츠키', '서비스', '제목', '본문', '원문 링크')
RESULT_COLUMNS = ('리드문', '엔티티', '인텐트', '카테고리', '핵심키워드', '핵심문장', '모델', '처리 시각', '상태', '프리즘 해시', '실험 ID', '오류')
OVER = f'{KL.MAX_ITEMS}건 초과 · 다음 실행에서 처리'


def _config(team):
    return KL._get(CONFIG, team) or {}


def public_config(team):
    c = _config(team)
    return {'url': c.get('url', ''), 'has_key': bool(c.get('key')), 'template_url': TEMPLATE_URL}


def save_config(body, team):
    if body.get('code') is not None:                # 연결 코드 '<웹앱 url>#<key>' · 키 안에 '#' 이 있을 수 있어 첫 '#' 기준
        url, _, key = str(body['code']).strip().partition('#')
        url, key = url.strip(), key.strip()
    else:
        url, key = str(body.get('url') or '').strip(), str(body.get('key') or '').strip()
    if not URL_RE.fullmatch(url):
        raise ValueError('웹앱 주소는 https://script.google.com/macros/s/…/exec 형태여야 합니다')
    if key and not 8 <= len(key) <= 200:
        raise ValueError('비밀 키는 8~200자로 입력하세요')
    if not key and not _config(team).get('key'):
        raise ValueError('비밀 키를 입력하세요')
    KL._update(CONFIG, team, lambda c: c.update(url=url, **({'key': key} if key else {})), {})
    return {'ok': True, 'sheet': public_config(team)}


def _call(req, key):
    """웹앱 호출 · 오류 문구에 URL·키를 싣지 않는다."""
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as exc:
        raise ValueError(f'시트 웹앱 호출 실패 (HTTP {exc.code})')
    except (urllib.error.URLError, OSError):
        raise ValueError('시트 웹앱에 연결하지 못했습니다 · 주소와 배포 상태를 확인하세요')
    except ValueError:
        raise ValueError('시트 웹앱 응답을 읽지 못했습니다 · 배포 상태를 확인하세요')
    if not isinstance(data, dict) or data.get('ok') is not True:
        err = data.get('error') if isinstance(data, dict) else ''
        err = str(err or '시트 웹앱이 오류를 돌려줬습니다').replace(key, '***')[:200]
        raise ValueError('시트 비밀 키가 맞지 않습니다' if err == 'unauthorized' else err)
    return data


def _ready(team):
    c = _config(team)
    if not c.get('url') or not c.get('key'):
        raise ValueError('구글 시트 연동 설정(웹앱 주소·비밀 키)을 먼저 저장하세요')
    return c


def fetch_rows(team):
    """시트 행 → [{row, values{열 이름: 문자열}}] · 형식이 어긋나면 전체를 거절한다(신뢰 경계)."""
    c = _ready(team)
    data = _call(urllib.request.Request(c['url'] + '?' + urllib.parse.urlencode({'key': c['key']})), c['key'])
    rows = data.get('rows')
    if not isinstance(rows, list):
        raise ValueError('시트 웹앱 응답 형식이 다릅니다')
    if len(rows) > MAX_ROWS:
        raise ValueError(f'시트 행이 {MAX_ROWS}건을 넘습니다 · 처리한 행을 다른 시트로 옮기세요')
    out = []
    for r in rows:
        if not isinstance(r, dict) or type(r.get('row')) is not int or r['row'] < 2 or not isinstance(r.get('values'), dict):
            raise ValueError('시트 웹앱 응답 형식이 다릅니다')
        out.append({'row': r['row'], 'values': {str(k).strip(): '' if v is None else str(v).strip() for k, v in r['values'].items()}})
    cols = {k for r in out for k in r['values']}
    if out and (not cols & {'제목', '본문'} or '상태' not in cols):
        raise ValueError('시트 양식이 다릅니다 · Apps Script 의 setup 을 실행하세요')
    return out


def select(rows, include_pending=False):
    """처리 대상과 건너뜀(사유) · 시트 행 순서 유지 · 순수 함수."""
    targets, skipped, seen = [], [], set()
    for r in rows:
        v = r['values']
        title, status = v.get('제목', ''), v.get('상태', '')
        content = {'displayServiceName': v.get('서비스', ''), 'title': title, 'body': v.get('본문', '')}
        h = content_hash(content)
        if status not in ('', '실패', '미검수'):
            reason = status                           # 검수 완료 · 반영됨(2단계) 등 · 항상 제외
        elif status == '미검수' and not include_pending:
            reason = '미검수(다시 생성 꺼짐)'
        elif not title and not content['body']:
            reason = '제목·본문 없음'
        elif any(len(v.get(k, '')) > LIMITS.get(k, 2000) for k in INPUT_COLUMNS):
            reason = '필드 길이 초과'
        elif h in seen:
            reason = '같은 콘텐츠 중복'
        elif len(targets) >= KL.MAX_ITEMS:
            reason = OVER
        else:
            seen.add(h)
            if v.get('원문 링크'):
                content['source_url'] = v['원문 링크']
            if v.get('콘텐츠키'):
                content['item_unique_key'] = v['콘텐츠키']
            targets.append({'row': r['row'], 'title': title, 'hash': h, 'content': content})
            continue
        skipped.append({'row': r['row'], 'title': title, 'reason': reason})
    return targets, skipped


def preview(body, team):
    rows = fetch_rows(team)
    targets, skipped = select(rows, body.get('include_pending') is True)
    return {'ok': True, 'read': len(rows), 'rows': [{'row': t['row'], 'title': t['title']} for t in targets], 'skipped': skipped}


def write(team, updates):
    """[{row, values}] 를 50행씩 기재 · 반환: 기재 행 수."""
    c = _ready(team)
    written = 0
    for i in range(0, len(updates), WRITE_CHUNK):
        req = urllib.request.Request(c['url'], method='POST', headers={'Content-Type': 'application/json'},
                                     data=json.dumps({'key': c['key'], 'updates': updates[i:i + WRITE_CHUNK]}, ensure_ascii=False).encode())
        written += int(_call(req, c['key']).get('written') or 0)   # 302 → googleusercontent GET 은 urllib 가 따라간다
    return written


def _kst(ts):
    return time.strftime('%Y-%m-%d %H:%M', time.gmtime(ts + 9 * 3600))


def updates_of(run):
    """실험 기록 → 시트 기재 행 · 완료(미검수)·실패 칸만 · 아직 결과 없는 행은 뺀다."""
    from .meta_contract import category_paths, entity_names
    label, meta_model, out = run['slots'][0]['label'], run['sheet'].get('meta_model', ''), []
    for it in run['items']:
        cell = run['cells'].get(it['key'] + ':' + label)
        if 'sheet_row' not in it or not cell or cell['status'] not in ('done', 'failed'):
            continue
        m, ok = it['meta'], cell['status'] == 'done'
        out.append({'row': it['sheet_row'], 'values': {
            '리드문': m.get('summary') or '', '엔티티': ', '.join(entity_names(m.get('entities'))),
            '인텐트': ', '.join(x for x in m.get('intent') or [] if isinstance(x, str)),
            '카테고리': ', '.join(category_paths(m.get('content_category'))),
            '핵심키워드': ', '.join(k['text'] for k in (cell.get('refined') or {}).get('keywords', [])) if ok else '',
            '핵심문장': '',                               # ponytail: 키워드 실험은 문장을 안 만든다 · 문장 실험 연동 때 채움
            '모델': f"메타 {meta_model or '-'} · 키워드 {cell.get('actual_model') or run['slots'][0]['version']['model']}",
            '처리 시각': _kst(run['updated_at']), '상태': '미검수' if ok else '실패',
            '프리즘 해시': it['hash'], '실험 ID': run['id'], '오류': '' if ok else str(cell.get('error') or '')[:500]}})
    return out
