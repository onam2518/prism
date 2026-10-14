"""키워드 실험실 · 구글 시트 연동(시트 → 프리즘 호출 · A안).

사내 계정은 Apps Script 웹앱을 외부 공개로 배포할 수 없어, 시트(Code.gs)가 UrlFetchApp 으로 POST /sheet-sync 를 부른다.
연결 코드 = '<팀>.<비밀>' · 프리즘엔 sha256 만 저장(평문은 발급 응답에 1회) · 인증만 한다(모델·버전은 시트 설정 탭).
op: options(설정 탭 목록) · run(행 → 실행 · mode meta|keyword|both) · result(완료된 행의 기재 값) · written(기재 결과 보고).
mode: meta=원천 메타만(작은 실행 기록) · keyword=시트에 적힌 메타로 핵심키워드 · both=메타 생성 후 키워드. docs/SHEET_SYNC.md
"""
import hashlib
import hmac
import json
import re
import secrets
import time

from . import keywordlab as KL
from .modelmeta import KNOWN_ROUTER_MODELS
from .store import content_hash

CONFIG = 'keyword_lab_sheet'
TEMPLATE_URL = 'https://docs.google.com/spreadsheets/d/10v-uIRGSiqpLXv3EbQIEvyMWufVh67b6wiiC9Oiifk0/copy'
MAX_ROWS = 300                                   # Code.gs 가 상태로 거른 뒤 보내는 상한 · 본문 상한(32MB) 안
LIMITS = {'제목': 300, '본문': 20000}           # 그 외 입력 칸은 2000
INPUT_COLUMNS = ('콘텐츠키', '서비스', '제목', '본문', '원문 링크')
RESULT_COLUMNS = ('리드문', '엔티티', '인텐트', '카테고리', '핵심키워드', '핵심문장', '모델', '처리 시각', '상태', '프리즘 해시', '실험 ID', '오류')
OVER = f'{KL.MAX_ITEMS}건 초과 · 다음 실행에서 처리'
DENIED = '연결 코드가 맞지 않습니다 · 프리즘에서 새로 발급해 시트 메뉴 프리즘 › 연결 설정에 넣으세요'
CODE_RE = re.compile(r'([\w-]{1,64})\.([\w-]{40,64})')
MODES = ('meta', 'keyword', 'both')
DONE_COLUMN = {'meta': '리드문', 'keyword': '핵심키워드', 'both': '핵심키워드'}   # 이 칸이 찬 미검수 행 = 이미 만든 행
META_DEFAULT = '운영 기본'
STALE = '{}을(를) 설정 탭에서 다시 고르세요 · 목록이 바뀌었으면 시트 메뉴 프리즘 › 설정 목록 새로고침'


def _config(team):
    return KL._get(CONFIG, team) or {}


def _hash(code):
    return hashlib.sha256(code.encode()).hexdigest()


def public_config(team):
    c = _config(team)
    return {'connected': bool(c.get('key_hash')), 'issued_at': c.get('issued_at'), 'template_url': TEMPLATE_URL}


def issue(team, actor):
    """관리자: 연결 코드 발급(이전 코드 무효) · 인증만 · 모델·프롬프트는 시트 설정 탭에서 고른다."""
    code = f"{team or '-'}.{secrets.token_urlsafe(32)}"
    KL._update(CONFIG, team, lambda c: (c.clear(), c.update(key_hash=_hash(code), by=actor, issued_at=time.time())), {})
    return {'ok': True, 'code': code, 'sheet': public_config(team)}


def _versions(team):
    """설정 탭 드롭다운 표시값 → 핵심키워드 프롬프트 버전 · 서버가 같은 표시값을 다시 만들어 검증한다(최신순)."""
    return {f"v{v['number']} · {v['model']} · {v['title']}": v for v in reversed(KL._catalog(team)['versions'])
            if KL._target(v) == 'keyword' and not v.get('draft') and v['validator_version'] == KL.ER.KEYWORD_VALIDATION_VERSION}


def options(team):
    return {'meta_models': [META_DEFAULT] + KNOWN_ROUTER_MODELS, 'keyword_versions': list(_versions(team))}


def _meta_model(name):
    if name in (None, '', META_DEFAULT):
        return ''                                    # run_pipeline 운영 기본 텍스트 모델
    if name not in KNOWN_ROUTER_MODELS:
        raise ValueError(STALE.format('원천 메타 모델'))
    return name


def _team(code):
    """연결 코드 → 팀 · 형식·팀·해시 어느 쪽이 틀려도 같은 거절(팀 열거 방지)."""
    m = CODE_RE.fullmatch(code) if isinstance(code, str) else None
    team = None if not m or m.group(1) == '-' else m.group(1)
    saved = _config(team).get('key_hash', '') if m else ''
    if not saved or not hmac.compare_digest(saved, _hash(code)):
        raise PermissionError(DENIED)
    return team


def _rows(rows):
    """시트가 보낸 행 → [{row, values{열 이름: 문자열}}] · 형식이 어긋나면 전체를 거절한다(신뢰 경계)."""
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError(f'행 형식이 다르거나 {MAX_ROWS}행을 넘습니다')
    out = []
    for r in rows:
        if not isinstance(r, dict) or type(r.get('row')) is not int or r['row'] < 2 or not isinstance(r.get('values'), dict):
            raise ValueError('행 형식이 다릅니다 · Code.gs 를 최신으로 바꾸세요')
        out.append({'row': r['row'], 'values': {str(k).strip(): '' if v is None else str(v).strip() for k, v in r['values'].items()}})
    cols = {k for r in out for k in r['values']}
    if out and (not cols & {'제목', '본문', '리드문'} or '상태' not in cols):
        raise ValueError('시트 양식이 다릅니다 · 시트 메뉴 프리즘 › 양식 만들기를 실행하세요')
    return out


def _meta_error(meta):
    try:
        KL._meta_item(None, meta)
        return ''
    except ValueError as exc:
        return str(exc)


def select(rows, include_pending=False, mode='both'):
    """처리 대상과 건너뜀(사유) · 시트 행 순서 유지 · 순수 함수 · keyword 는 시트의 메타 칸을 입력으로 읽는다."""
    targets, skipped, seen = [], [], set()
    for r in rows:
        v = r['values']
        title, status = v.get('제목', ''), v.get('상태', '')
        content = {'displayServiceName': v.get('서비스', ''), 'title': title, 'body': v.get('본문', '')}
        meta = {'summary': v.get('리드문', ''), 'entities': KL._split(v.get('엔티티')), 'intent': KL._split(v.get('인텐트')),
                'content_category': KL._split(v.get('카테고리'))} if mode == 'keyword' else None
        h = KL._digest(meta) if meta and not (title or content['body']) else content_hash(content)
        if status not in ('', '실패', '미검수'):
            reason = status                           # 검수 완료 · 반영됨(2단계) 등 · 항상 제외
        elif status == '미검수' and v.get(DONE_COLUMN[mode]) and not include_pending:
            reason = '미검수(다시 생성 꺼짐)'           # 메타만 만든 행은 키워드 칸이 비어 키워드 실행 대상
        elif meta is None and not title and not content['body']:
            reason = '제목·본문 없음'
        elif meta is not None and not meta['summary']:
            reason = '리드문 없음'
        elif meta is not None and not meta['entities']:
            reason = '엔티티 없음'
        elif any(len(v.get(k, '')) > LIMITS.get(k, 2000) for k in INPUT_COLUMNS):
            reason = '필드 길이 초과'
        elif meta is not None and _meta_error(meta):
            reason = _meta_error(meta)
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
            targets.append({'row': r['row'], 'title': title, 'hash': h, 'content': content, **({'meta': meta} if meta else {})})
            continue
        skipped.append({'row': r['row'], 'title': title, 'reason': reason})
    return targets, skipped


def _kst(ts):
    return time.strftime('%Y-%m-%d %H:%M', time.gmtime(ts + 9 * 3600))


def _meta_values(m):
    from .meta_contract import category_paths, entity_names
    return {'리드문': m.get('summary') or '', '엔티티': ', '.join(entity_names(m.get('entities'))),
            '인텐트': ', '.join(x for x in m.get('intent') or [] if isinstance(x, str)),
            '카테고리': ', '.join(category_paths(m.get('content_category')))}


def updates_of(run):
    """실행 기록 → 시트 기재 행 · 끝난(미검수·실패) 행만 · 실행 종류가 만든 칸만 적는다(키워드만 실행은 메타 칸을 안 건드림)."""
    s, out = run['sheet'], []
    mode, label = s.get('mode', 'both'), run['slots'][0]['label'] if run.get('slots') else ''
    for it in run['items']:
        if mode == 'meta':
            if it.get('status') not in ('done', 'failed'):
                continue
            ok, error = it['status'] == 'done', it.get('error')
            values = dict(_meta_values(it['meta']) if it.get('meta') else {}, 모델=f"메타 {it.get('model') or '-'}")
        else:
            cell = run['cells'].get(it['key'] + ':' + label)
            if 'sheet_row' not in it or not cell or cell['status'] not in ('done', 'failed'):
                continue
            ok, error = cell['status'] == 'done', cell.get('error')
            kw = f"키워드 {cell['actual_model']}" if cell.get('actual_model') else ''   # 키워드 미실행 칸은 메타만
            values = {'핵심키워드': ', '.join(k['text'] for k in (cell.get('refined') or {}).get('keywords', [])) if ok else '',
                      '핵심문장': ''}                     # ponytail: 키워드 실험은 문장을 안 만든다 · 문장 실험 연동 때 채움
            if mode == 'both':
                values.update(_meta_values(it['meta']), 모델=' · '.join(filter(None, [f"메타 {s.get('meta_model') or '-'}", kw])))
            else:
                values['모델'] = kw or '-'
        values.update({'처리 시각': _kst(run['updated_at']), '상태': '미검수' if ok else '실패', '프리즘 해시': it['hash'],
                       '실험 ID': run['id'], '오류': '' if ok else str(error or '')[:500]})
        # 정렬·행 삽입으로 위치가 바뀐 행엔 쓰지 않도록 원문과 대조(Code.gs)
        out.append({'row': it['sheet_row'], 'values': values,
                    'expect': {'제목': it['content'].get('title', ''), '콘텐츠키': it['content'].get('item_unique_key', '')}})
    return out


def _start_meta(sheet, team):
    """원천 메타만: 실험실 실험 없이 작은 실행 기록(실험 기록 키 공간 · 카탈로그 미등록)만 남긴다."""
    rows = _rows(sheet['rows'])
    targets, skipped = select(rows, sheet['include_pending'], 'meta')
    if not targets:
        raise ValueError('처리할 행이 없습니다')
    rid, now = KL._id(), time.time()
    rec = {'id': rid, 'target': 'meta', 'by': '구글 시트', 'status': 'running', 'created_at': now, 'updated_at': now, 'revision': 0,
           'items': [{'sheet_row': t['row'], 'title': t['title'], 'hash': t['hash'], 'content': t['content']} for t in targets],
           'sheet': {'read': len(rows), 'targets': len(targets), 'skipped': len(skipped), 'mode': 'meta', 'meta_pick': sheet['meta_pick'],
                     'include_pending': sheet['include_pending'], 'written': 0, 'mismatched': 0, 'write_error': '', 'written_at': None}}
    with KL.LOCK:
        if team in KL.SHEET_TEAMS:
            raise ValueError('이미 시트 실험이 실행 중입니다')
        KL.SHEET_TEAMS.add(team)
        KL.ACTIVE.add((team, rid))
    KL._update(KL._key('run', rid), team, lambda r: r.update(rec), {})
    KL.threading.Thread(target=_meta_run, args=(rid, team, rec), daemon=True).start()
    return {'id': rid}


def _meta_run(rid, team, rec):
    try:
        done, _ = KL.sheet_metas(rec['items'], team, rec['sheet']['meta_pick'])
        for it, m, used, error in done:
            it.update(status='failed' if error else 'done', meta=m or {}, model=used, error=error[:500])
        end = {'items': rec['items'], 'status': 'done'}
    except Exception:
        end = {'status': 'interrupted', 'error': '메타 생성이 중단되었습니다 · 다시 실행하세요'}
    try:
        KL._update(KL._key('run', rid), team, lambda r: r.update(end, updated_at=time.time()))
    finally:
        with KL.LOCK:
            KL.ACTIVE.discard((team, rid))
            KL.SHEET_TEAMS.discard(team)


def _run(body, team):
    run = KL._get(KL._key('run', body.get('id')), team)
    if not run or 'sheet' not in run:
        raise ValueError('시트에서 시작한 실험이 아닙니다')
    return run


def handle(raw):
    """POST /sheet-sync 본문 → 응답 dict · 로그인 대신 연결 코드로 팀을 정한다."""
    try:
        body = json.loads(raw or b'{}')
        if not isinstance(body, dict):
            raise ValueError('요청 형식을 확인하세요')
        team, op = _team(body.get('key')), body.get('op')
        if op == 'options':
            return dict(options(team), ok=True)
        if op == 'run':
            mode = body.get('mode', 'both')
            if mode not in MODES:
                raise ValueError('실행 종류를 확인하세요 · Code.gs 를 최신으로 바꾸세요')
            sheet = {'rows': body.get('rows'), 'include_pending': body.get('include_pending') is True, 'mode': mode,
                     'meta_pick': _meta_model(body.get('meta_model')) if mode != 'keyword' else ''}
            if mode == 'meta':
                out = _start_meta(sheet, team)
            else:
                v = _versions(team).get(body.get('keyword_version'))
                if not v:
                    raise ValueError(STALE.format('핵심키워드 프롬프트'))
                out = KL.start({'target': 'keyword', 'slots': [{'model': v['model'], 'version_id': v['id']}], 'sheet': sheet},
                               team, '구글 시트')
            s = _run(out, team)['sheet']
            return {'ok': True, 'id': out['id'], 'targets': s['targets'], 'skipped': s['skipped']}
        if op == 'result':                       # 실행 중이어도 끝난 행은 먼저 받아 적을 수 있다
            KL._settle(body.get('id'), team)
            run = _run(body, team)
            return {'ok': True, 'status': run['status'], 'total': len(run['items']), 'updates': updates_of(run)}
        if op == 'written':
            written, mismatched = body.get('written'), body.get('mismatched')
            if type(written) is not int or type(mismatched) is not int:
                raise ValueError('기재 결과 형식을 확인하세요')
            _run(body, team)
            KL._update(KL._key('run', body['id']), team, lambda r: r['sheet'].update(
                written=written, mismatched=mismatched, written_at=time.time(),
                write_error=f'{mismatched}개 행 위치가 바뀌어 기재하지 않았습니다 · 정렬·삽입 없이 결과 받기를 다시 누르세요' if mismatched else ''))
            return {'ok': True}
        raise ValueError('지원하지 않는 작업입니다')
    except (ValueError, TypeError, KeyError, PermissionError) as exc:
        return {'ok': False, 'error': str(exc)[:300]}
