"""상시 검수: 기준별 판정·조치·작업시간·주간 계획. 기존 reports를 원장으로 사용."""
import copy
import hashlib
import json
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

FIELDS = ('summary', 'entities', 'intent', 'content_category')
LABELS = dict(zip(FIELDS, ('리드문', '엔티티', '인텐트', '카테고리')))
STATUSES = ('accurate', 'needs_fix', 'hold')
CASE_STATES = ('open', 'working', 'recheck', 'verify', 'closed', 'hold')
KST = timezone(timedelta(hours=9))
HASH = re.compile(r'^[0-9a-f]{16}$')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def read_row(st, ch, team=None):
    if not HASH.fullmatch(str(ch or '')):
        raise ValueError('실제 검수 콘텐츠를 선택하세요')
    if getattr(st, 'REMOTE', False):
        rows = st._get('contents', 'select=*&' + st._hash_q(ch, team))
        if not rows:
            raise ValueError('콘텐츠를 찾을 수 없습니다')
        row = rows[0]
        return {k: row.get(k) for k in ('service', 'title', 'subtitle', 'body', 'source_fields',
                'item_meta', 'quality_meta', 'model', 'version')}
    row = st._conn().execute('SELECT service,title,item_meta,payload FROM results WHERE content_hash=?', (ch,)).fetchone()
    if not row:
        raise ValueError('콘텐츠를 찾을 수 없습니다')
    payload = json.loads(row[3] or '{}'); ref = payload.get('content_ref') or {}
    return {'service': row[0], 'title': row[1], 'subtitle': ref.get('subtitle', ''),
            'body': ref.get('body', ''), 'source_fields': ref.get('source_fields') or {},
            'item_meta': json.loads(row[2] or '{}'), 'quality_meta': payload.get('quality_meta') or {},
            'model': (payload.get('trace') or {}).get('model'), 'version': (payload.get('trace') or {}).get('version')}


def basis(row):
    im, source = row.get('item_meta') or {}, row.get('source_fields') or {}
    out = {'item_unique_key': source.get('item_unique_key') or im.get('item_unique_key'),
           'input_revision': source.get('input_revision') or im.get('input_revision'),
           'policy_version': source.get('policy_version') or im.get('policy_version'),
           'publication_revision': source.get('publication_revision') or im.get('publication_revision'),
           'model': row.get('model'), 'prompt_version': row.get('version'),
           'title': row.get('title'), 'body': row.get('body'), 'snapshot': copy.deepcopy(row)}
    out['token'] = digest(row)
    return out


def reports(st, prefix, team=None):
    if getattr(st, 'REMOTE', False):
        rows, offset = [], 0
        while True:
            part = st._get('reports', 'select=kind,payload&team_key=eq.' + urllib.parse.quote(team or '')
                           + '&kind=like.' + urllib.parse.quote(prefix + '*')
                           + '&order=kind&limit=500&offset=' + str(offset))
            rows.extend(part)
            if len(part) < 500:
                return {r['kind']: r['payload'] for r in rows}
            offset += len(part)
    return {k: json.loads(v) for k, v in st._conn().execute(
        'SELECT kind,payload FROM reports WHERE team=? AND substr(kind,1,?)=?', (team or '', len(prefix), prefix))}


def commit(st, ch, team, expected_row, old, new, patch=None, feedback=None, extra=None):
    """판정·교정·필수 이력과 기준 확인을 한 트랜잭션으로 저장."""
    kind = 'ops_review_' + ch
    if getattr(st, 'REMOTE', False):
        return st._req('POST', 'rpc/prism_ops_commit', body={
            'p_hash': ch, 'p_team': team, 'p_row': expected_row, 'p_kind': kind,
            'p_expected': old, 'p_payload': new, 'p_patch': patch,
            'p_feedback': feedback, 'p_extra': extra}) is True
    c = st._conn()
    try:
        c.execute('BEGIN IMMEDIATE')
        if read_row(st, ch, team) != expected_row or st.get_report(kind, team) != old:
            c.rollback(); return False
        if extra and st.get_report(extra['kind'], team) != extra['expected']:
            c.rollback(); return False
        if patch:
            data = json.loads(c.execute('SELECT payload FROM results WHERE content_hash=?', (ch,)).fetchone()[0] or '{}')
            im = dict(expected_row.get('item_meta') or {}); qm = dict(expected_row.get('quality_meta') or {})
            im.update(patch.get('item_meta') or {}); qm.update(patch.get('quality_meta') or {})
            data.update(item_meta=im, quality_meta=qm)
            c.execute('UPDATE results SET item_meta=?,payload=?,final_grade=?,reasons=? WHERE content_hash=?',
                      (json.dumps(im), json.dumps(data), qm.get('finalGrade', ''), json.dumps(qm.get('reasons', [])), ch))
            c.execute('INSERT INTO patch_log(content_hash,reviewer,element,before,after,ts) VALUES(?,?,?,?,?,?)',
                      (ch, new['history'][-1]['by'], ','.join((patch.get('item_meta') or {}).keys()) or 'grade',
                       json.dumps(expected_row), json.dumps(patch), time.time()))
        if feedback:
            if feedback['verdict'] in ('good', 'bad'):
                c.execute('INSERT INTO feedback(content_hash,reviewer,service,title,verdict,stage,note,ts,element) '
                          'VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(content_hash,reviewer) DO UPDATE SET '
                          'verdict=excluded.verdict,stage=excluded.stage,note=excluded.note,ts=excluded.ts,element=excluded.element',
                          (ch, feedback['reviewer'], expected_row['service'], expected_row['title'], feedback['verdict'],
                           'analyze', feedback.get('note', ''), time.time(), feedback.get('element', '')))
            else:
                c.execute('DELETE FROM feedback WHERE content_hash=? AND reviewer=?', (ch, feedback['reviewer']))
        for key, value in [(kind, new)] + ([(extra['kind'], extra['payload'])] if extra else []):
            c.execute('INSERT INTO reports(kind,team,payload,ts) VALUES(?,?,?,?) ON CONFLICT(kind,team) '
                      'DO UPDATE SET payload=excluded.payload,ts=excluded.ts', (key, team or '', json.dumps(value), time.time()))
        c.commit(); return True
    except Exception:
        c.rollback(); raise


def detail(st, ch, team=None, who='', privileged=False):
    row = read_row(st, ch, team); b = basis(row)
    record = st.get_report('ops_review_' + ch, team) or {'revision': 0, 'reviews': [], 'cases': [], 'sessions': [], 'history': []}
    latest = next((r for r in reversed(record['reviews']) if r['by'] == who and r['basis']['token'] == b['token']), None)
    result = {'ok': True, 'team': team, 'hash': ch, 'basis': b, 'revision': record['revision'], 'review': latest,
              'cases': record['cases'], 'sessions': [s for s in record['sessions'] if privileged or s['by'] == who],
              'history': record['history'] if privileged else [x for x in record['history'] if x['by'] == who],
              'values': {k: (row.get('item_meta') or {}).get(k, '' if k == 'summary' else []) for k in FIELDS}}
    result['stages'] = record.get('stages') or {}
    result['bases'] = record.get('bases') or {}
    result['golden'] = ch in st.golden_hashes(team)
    return result


def _text(value, limit=3000):
    return str(value or '').strip()[:limit]


def _date(value):
    value = _text(value, 40)
    if value:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value


def _stamp(value):
    if not value:
        return 0
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return (d if d.tzinfo else d.replace(tzinfo=KST)).timestamp()


def action(st, data, team=None, who='', privileged=False, final=False):
    try:
        if not who:
            raise ValueError('검수자 정보가 필요합니다')
        cmd = data.get('action'); ch = data.get('hash')
        if cmd == 'plan':
            if not privileged: raise ValueError('주간 계획은 운영 담당자가 저장할 수 있습니다')
            return save_plan(st, data, team, who)
        row = read_row(st, ch, team); b = basis(row)
        old = st.get_report('ops_review_' + ch, team)
        new = copy.deepcopy(old or {'revision': 0, 'reviews': [], 'cases': [], 'sessions': [], 'history': []})
        if data.get('basis_token') != b['token'] or data.get('revision') != new['revision']:
            raise ValueError('원문·메타·정책 또는 다른 담당자의 기록이 바뀌었습니다. 다시 열어 확인하세요')
        patch = feedback = extra = None
        now = time.time()
        new.setdefault('bases', {})[b['token']] = b
        basis_ref = {k: v for k, v in b.items() if k not in ('snapshot', 'body')}
        if cmd in ('review', 'undo'):
            asg = (st.assignees(team=team, hashes=[ch]) or {}).get(ch) or {}
            if asg.get('reviewers') and who not in asg['reviewers']:
                raise ValueError('다른 검수자에게 배정된 콘텐츠입니다')
            axes = data.get('axes') or {}
            if cmd == 'review':
                if set(axes) != set(FIELDS): raise ValueError('메타 4종의 판정을 모두 선택하세요')
                for key, axis in axes.items():
                    if not isinstance(axis, dict) or axis.get('status') not in STATUSES:
                        raise ValueError('항목별 판정을 확인하세요')
                    if axis['status'] == 'hold' and not _text(axis.get('reason')):
                        raise ValueError(LABELS[key] + '의 보류 사유를 입력하세요')
                    if axis['status'] == 'needs_fix' and not _text(axis.get('proposal')):
                        raise ValueError(LABELS[key] + '의 삭제·추가·수정 제안을 입력하세요')
                axes = {k: {'status': a['status'], 'reason': _text(a.get('reason')) if a['status'] == 'hold' else '',
                            'proposal': _text(a.get('proposal')) if a['status'] == 'needs_fix' else '', 'value': copy.deepcopy((row.get('item_meta') or {}).get(k))}
                        for k, a in axes.items()}
                states = [a['status'] for a in axes.values()]
                verdict = 'hold' if 'hold' in states else ('bad' if 'needs_fix' in states else 'good')
                new['reviews'].append({'by': who, 'ts': now, 'basis': basis_ref, 'axes': axes})
                if verdict != 'good':
                    case = next((c for c in new['cases'] if c['basis_token'] == b['token'] and c['state'] != 'closed'), None)
                    if case is None:
                        case = {'id': digest([ch, b['token'], now])[:16], 'basis_token': b['token'], 'opened_at': now,
                                'owner': who, 'priority': 'normal', 'due': '', 'state': 'open', 'receipts': []}
                        new['cases'].append(case)
                    case['state'] = 'hold' if verdict == 'hold' else 'open'
                    case['reason'] = '\n'.join(LABELS[k]+': '+(a['reason'] or a['proposal']) for k,a in axes.items() if a['status']!='accurate')
                feedback = {'reviewer': who, 'verdict': verdict,
                            'note': '\n'.join(LABELS[k]+': '+(a['reason'] or a['proposal']) for k,a in axes.items() if a['reason'] or a['proposal']),
                            'element': ','.join('category' if k=='content_category' else k for k,a in axes.items() if a['status']=='needs_fix')}
            else:
                new['reviews'].append({'by': who, 'ts': now, 'basis': basis_ref, 'axes': {}, 'withdrawn': True})
                feedback = {'reviewer': who, 'verdict': ''}
        elif cmd == 'import_legacy':
            feedback_rows=(st.feedback_map(team=team) or {}).get(ch) or {}
            bad=[v for v in feedback_rows.get('verdicts',[]) if v.get('verdict')=='bad' and (privileged or (v.get('reviewer_id') or v.get('reviewer'))==who)]
            if not bad: raise ValueError('기존 수정 필요 판정을 찾을 수 없습니다')
            if new['cases']: raise ValueError('기존 조치 건을 확인하세요')
            new['cases'].append({'id':digest([ch,b['token'],now])[:16],'basis_token':b['token'],'opened_at':now,
                                 'owner':who,'priority':'normal','due':'','state':'open','receipts':[],
                                 'reason':'이전 수정 필요 판정의 조치 확인 · '+_text(bad[-1].get('note'))})
        elif cmd == 'case':
            case = next((c for c in new['cases'] if c['id'] == data.get('case_id')), None)
            if not case: raise ValueError('조치 건을 찾을 수 없습니다')
            if not privileged and case.get('owner') != who: raise ValueError('조치 담당자만 변경할 수 있습니다')
            if case['state'] == 'closed': raise ValueError('종료한 조치의 이력은 변경할 수 없습니다')
            state = data.get('state', case['state'])
            if state not in CASE_STATES: raise ValueError('처리 상태를 확인하세요')
            previous_owner = case.get('owner')
            for key in ('owner', 'backup', 'priority', 'due', 'next_check', 'task_url', 'reason', 'recheck_evidence', 'handoff_reason', 'service_waiver_reason'):
                if key in data: case[key] = _date(data[key]) if key in ('due','next_check') else _text(data[key])
            if previous_owner != case.get('owner') and not case.get('handoff_reason'):
                raise ValueError('담당 변경 사유를 입력하세요')
            if not case.get('owner'): raise ValueError('담당자를 입력하세요')   # 조치 기한은 선택 항목
            if state == 'hold' and (not case.get('reason') or not case.get('next_check')):
                raise ValueError('보류 사유와 다음 확인일을 입력하세요')
            if data.get('receipt'):
                if not privileged: raise ValueError('서비스 반영 근거는 운영 담당자가 확인할 수 있습니다')
                receipt = data['receipt']
                if not _text(receipt.get('evidence')) or not receipt.get('publication_revision'):
                    raise ValueError('발행 순번과 실제 서비스 수신 근거를 입력하세요')
                if receipt.get('publication_revision') != b['publication_revision'] or not b['item_unique_key']:
                    raise ValueError('현재 원천 아이템·발행 순번과 일치하는 수신 근거가 필요합니다')
                case['receipts'].append(dict(receipt, by=who, ts=now, item_unique_key=b['item_unique_key']))
            if state == 'closed':
                reviews = [r for r in new['reviews'] if r['basis']['token']==b['token']]
                last_by = {r['by']:r for r in reviews}
                if not case.get('recheck_evidence') or not any(all(a['status']=='accurate' for a in r['axes'].values()) and len(r['axes'])==4 for r in last_by.values()):
                    raise ValueError('현재 기준 메타 4종의 재검수와 근거가 필요합니다')
                service_required = data.get('service_required', True)
                if service_required is False and (not privileged or not case.get('service_waiver_reason')):
                    raise ValueError('서비스 반영 확인을 제외하려면 운영 담당자의 사유가 필요합니다')
                valid_receipts = [r for r in case['receipts'] if r.get('item_unique_key') == b['item_unique_key']
                                  and r.get('publication_revision') == b['publication_revision']]
                if service_required and not valid_receipts:
                    raise ValueError('실제 서비스 반영 근거를 확인하세요')
                case['closed_at'] = now; case['closed_by'] = who
                case['service_required'] = bool(data.get('service_required', True))
            case['state'] = state; case['updated_at'] = now
        elif cmd == 'time':
            event = data.get('event'); current = next((s for s in reversed(new['sessions']) if s['by']==who and not s.get('finished')), None)
            if event in ('start', 'resume') and any(s.get('active_since') and not s.get('finished') and s['by'] == who
                    for key,record in reports(st,'ops_review_',team).items() if key != 'ops_review_' + ch
                    for s in record.get('sessions',[])):
                raise ValueError('다른 콘텐츠의 작업을 중단하거나 종료하세요')
            if event == 'start':
                if current: raise ValueError('진행 중인 작업을 먼저 종료하세요')
                typ = data.get('type')
                if typ not in ('first','fix','recheck','final','operation'): raise ValueError('작업 유형을 선택하세요')
                new['sessions'].append({'by':who,'type':typ,'basis_token':b['token'],'started':now,'active_since':now,'seconds':0,'events':[{'event':'start','ts':now}]})
            elif current and event in ('pause','resume','finish'):
                if event=='resume' and current.get('active_since'): raise ValueError('이미 진행 중인 작업입니다')
                if event=='pause' and not current.get('active_since'): raise ValueError('이미 중단된 작업입니다')
                if current.get('active_since'): current['seconds'] += max(0, now-current['active_since'])
                current['active_since'] = now if event=='resume' else None
                current['events'].append({'event':event,'ts':now})
                if event=='finish': current['finished']=now
            else: raise ValueError('진행 중인 작업이 없습니다')
        elif cmd in ('patch','correct_final'):
            if cmd=='correct_final' and not (privileged or final): raise ValueError('최종검수자만 결정할 수 있습니다')
            raw = data.get('patch') or {}; im = {}; qm = {}
            if not isinstance(raw, dict) or any(k not in (*FIELDS,'finalGrade') for k in raw): raise ValueError('수정 항목을 확인하세요')
            from .dnm import validate_value
            for key,value in raw.items():
                if key=='finalGrade':
                    if value not in ('G','R'): raise ValueError('품질 등급을 별도로 확인하세요')
                    qm[key]=value
                elif not validate_value(key,value): raise ValueError(LABELS[key]+'의 유효한 수정값을 입력하세요')
                else: im[key]=value
            if im:
                manual = dict((row.get('item_meta') or {}).get('manual_fields') or {})
                for key in im: manual[key]={'input_revision':b['input_revision'],'policy_version':b['policy_version'],'reviewer':who,'confirmed_at':now}
                im['manual_fields']=manual
            patch={'item_meta':im,'quality_meta':qm} if raw else None
            new.setdefault('stages',{})['correction']={'status':'saved','by':who,'ts':now,'patch':raw}
            if cmd=='correct_final':
                grade=qm.get('finalGrade') or (row.get('quality_meta') or {}).get('finalGrade')
                if grade not in ('G','R'): raise ValueError('메타 정확성과 별도로 품질 등급을 확인하세요')
                finals=st.get_report('final_verdicts',team)
                updated=copy.deepcopy(finals or {'items':{}})
                corrected_row = copy.deepcopy(row)
                corrected_row['item_meta'] = dict(row.get('item_meta') or {}, **im)
                corrected_row['quality_meta'] = dict(row.get('quality_meta') or {}, **qm)
                updated.setdefault('items',{})[ch]={'verdict':'good','by':who,'ts':now,'basis_token':basis(corrected_row)['token']}
                extra={'kind':'final_verdicts','expected':finals,'payload':updated}
                new['stages']['final']={'status':'saved','by':who,'ts':now}
        elif cmd == 'calibration':
            if not privileged: raise ValueError('기준 합의는 운영 담당자가 확정할 수 있습니다')
            if not b['policy_version']: raise ValueError('실제 적용 정책 버전의 증빙을 먼저 연결하세요')
            if not _text(data.get('evidence')) or not _text(data.get('decision')): raise ValueError('원문 근거와 합의 내용을 입력하세요')
            new.setdefault('calibrations',[]).append({'by':who,'ts':now,'basis':basis_ref,'decision':_text(data['decision']),'evidence':_text(data['evidence'])})
        else:
            raise ValueError('지원하지 않는 검수 작업입니다')
        new['revision'] += 1
        history_data = dict(data, axes=axes) if cmd == 'review' else data
        new['history'].append({'action':cmd,'by':who,'ts':now,'basis':basis_ref,'data':copy.deepcopy(history_data)})
        if not commit(st,ch,team,row,old,new,patch,feedback,extra):
            raise ValueError('저장 중 원문·정책·다른 담당자의 기록이 바뀌었습니다. 다시 확인하세요')
        return detail(st,ch,team,who,privileged)
    except (ValueError,TypeError,KeyError) as exc:
        return {'ok':False,'error':str(exc)}


def save_plan(st, data, team, who):
    week = _text(data.get('week'),10); d=datetime.strptime(week,'%Y-%m-%d')
    if d.weekday()!=0: raise ValueError('주차의 월요일을 선택하세요')
    entries=data.get('entries') or []
    if not isinstance(entries,list) or not entries or len(entries)>5000: raise ValueError('주간 검수 대상을 선택하세요')
    target=int(data.get('target',100))
    if target<1 or target>1000 or target!=100 and not _text(data.get('adjustment_reason')): raise ValueError('조정 수량과 사유를 확인하세요')
    normalized=[]; seen=set(); counts={}; guards={}
    for entry in entries:
        ch=entry.get('hash'); owner=_text(entry.get('owner'),100); topic=_text(entry.get('topic'),100)
        if ch in seen or not owner or not topic: raise ValueError('고유 콘텐츠·담당자·대표 주제를 확인하세요')
        seen.add(ch); current_row=read_row(st,ch,team); b=basis(current_row); guards[ch]=current_row
        if (st.get_report('reviewer_roles',team) or {}).get('items',{}).get(owner)=='final':
            raise ValueError('최종검수자는 기초 배정 대상에서 제외합니다')
        if entry.get('basis_token')!=b['token']: raise ValueError('대상 선정 이후 메타·원문이 바뀌었습니다')
        sample=entry.get('sample','general')
        if sample not in ('general','focused'): raise ValueError('일반/집중 표본을 구분하세요')
        work_type=entry.get('work_type','first')
        if work_type not in ('first','recheck'): raise ValueError('1차 검수와 재검수를 구분하세요')
        carry=_text(entry.get('carry_from'),10)
        if carry:
            carry_date=datetime.strptime(carry,'%Y-%m-%d')
            if carry_date.weekday()!=0 or carry_date>=d: raise ValueError('이월 원래 주차는 이전 주차의 월요일이어야 합니다')
            previous=st.get_report('ops_week_'+carry,team) or {}
            if not any(e['hash']==ch and e['owner']==owner for v in previous.get('versions',[]) for e in v['entries']): raise ValueError('원래 배정 주차의 대상·담당자를 확인하세요')
        elif work_type=='first': counts[owner]=counts.get(owner,0)+1
        normalized.append({'hash':ch,'owner':owner,'topic':topic,'sample':sample,'basis_token':b['token'],'carry_from':carry,'work_type':work_type,'selected_at':time.time()})
    if any(n>target for n in counts.values()): raise ValueError('담당자별 여러 주제의 신규 배정 합계가 계획 수량을 초과합니다')
    kind='ops_week_'+week; old=st.get_report(kind,team)
    new=copy.deepcopy(old or {'revision':0,'versions':[]})
    if data.get('revision',0)!=new['revision']: raise ValueError('주간 계획이 바뀌었습니다. 다시 확인하세요')
    new['versions'].append({'entries':normalized,'by':who,'ts':time.time(),'target':target,'adjustment_reason':_text(data.get('adjustment_reason')),
                            'selection':copy.deepcopy(data.get('selection') or {}),'timezone':'Asia/Seoul'})
    new['revision']+=1
    if getattr(st,'REMOTE',False):
        saved=st._req('POST','rpc/prism_ops_plan',body={'p_team':team,'p_kind':kind,'p_expected':old,'p_payload':new,'p_guards':guards}) is True
    else:
        c=st._conn()
        try:
            c.execute('BEGIN IMMEDIATE')
            saved=st.get_report(kind,team)==old and all(read_row(st,ch,team)==guard for ch,guard in guards.items())
            if saved:
                for entry in normalized:
                    c.execute('DELETE FROM assignments WHERE content_hash=? AND team=?',(entry['hash'],team or ''))
                    c.execute('INSERT INTO assignments(content_hash,reviewer,team,ts) VALUES(?,?,?,?)',(entry['hash'],entry['owner'],team or '',time.time()))
                    c.execute('INSERT INTO assignment_cfg(content_hash,team,min_reviewers) VALUES(?,?,1) ON CONFLICT(content_hash,team) DO UPDATE SET min_reviewers=1',(entry['hash'],team or ''))
                c.execute('INSERT INTO reports(kind,team,payload,ts) VALUES(?,?,?,?) ON CONFLICT(kind,team) DO UPDATE SET payload=excluded.payload,ts=excluded.ts',(kind,team or '',json.dumps(new),time.time()))
                c.commit()
            else:c.rollback()
        except Exception:
            c.rollback();raise
    if not saved: raise ValueError('다른 담당자가 계획·원문·메타를 변경했습니다. 다시 확인하세요')
    return {'ok':True,'plan':new,'week':week,'new_counts':counts}


def overview(st, team=None, who='', privileged=False, legacy=None, live_hashes=None, settled=None):
    records=reports(st,'ops_review_',team); items=[]; sessions=[]; calibrations=[]; now=time.time()
    for key,record in records.items():
        ch=key[len('ops_review_'):]
        for case in record.get('cases',[]):
            if privileged or case.get('owner')==who or case.get('backup')==who:
                item=dict(case,hash=ch,revision=record['revision'],title=(record.get('history') or [{}])[-1].get('basis',{}).get('title',ch))
                item['overdue']=case['state']!='closed' and bool(case.get('due')) and _stamp(case['due'])+(86400 if len(case['due'])==10 else 0)<now
                item['check_overdue']=case['state']=='hold' and bool(case.get('next_check')) and _stamp(case['next_check'])+(86400 if len(case['next_check'])==10 else 0)<now
                items.append(item)
        sessions.extend(dict(s,hash=ch) for s in record.get('sessions',[]) if privileged or s['by']==who)
        if privileged: calibrations.extend(dict(c,hash=ch) for c in record.get('calibrations',[]))
    if legacy is not None:
        known={c['hash'] for c in items}
        for ch,fb in legacy.items():
            if ch in known or records.get('ops_review_'+ch, {}).get('cases') or live_hashes is not None and ch not in live_hashes: continue
            # 이전 판정 미조치는 '수정 필요' 합의가 남은 건만: 정답 편입·최종 판정(settled)은 끝난 건이고
            # 의견 갈림은 최종 검수 큐 몫이다(2026-10-04 · 한 표만 수정 필요여도 올라와 814건으로 부풀던 문제)
            if ch in (settled or ()) or fb.get('consensus')!='bad': continue
            bad=[v for v in fb.get('verdicts',[]) if v.get('verdict')=='bad' and (privileged or (v.get('reviewer_id') or v.get('reviewer'))==who)]
            if bad:
                v=bad[-1];items.append({'id':'legacy:'+ch,'hash':ch,'title':v.get('title') or ch,'state':'open','owner':v.get('reviewer_id') or v.get('reviewer'),'due':'','reason':'기존 수정 필요 · 조치 결과 확인 필요','opened_at':0,'overdue':False,'check_overdue':False,'legacy':True})
    items.sort(key=lambda x:(x['state']=='closed',not x['overdue'],x['opened_at']))
    seconds={}
    for session in sessions:
        if session.get('finished'):
            key=session['by']+'|'+session['type']; seconds[key]=seconds.get(key,0)+session['seconds']
    plans = reports(st,'ops_week_',team) if privileged else {}
    progress = []
    for kind,plan in plans.items():
        version = plan['versions'][-1]
        monday = _stamp(kind[len('ops_week_'):])
        groups = {}
        for e in version['entries']:
            key = (e['owner'],e['topic'])
            group = groups.setdefault(key, {'week':kind[len('ops_week_'):], 'owner':e['owner'], 'topic':e['topic'],
                                           'new':0,'carry':0,'recheck':0,'done':0,'general':0,'focused':0})
            group['carry' if e.get('carry_from') else 'recheck' if e.get('work_type')=='recheck' else 'new'] += 1
            group[e['sample']] += 1
            reviews = records.get('ops_review_'+e['hash'], {}).get('reviews', [])
            latest = next((r for r in reversed(reviews) if r['by']==e['owner'] and r['basis']['token']==e['basis_token']), None)
            if latest and not latest.get('withdrawn') and monday <= latest['ts'] < monday+7*86400:
                group['done'] += 1
        progress.extend(groups.values())
    return {'ok':True,'items':items,'sessions':sessions,'seconds_by_type':seconds,'calibrations':calibrations,
            'plans':plans,'plan_progress':progress,
            'stats':{'open':sum(c['state']!='closed' for c in items),'overdue':sum(c['overdue'] for c in items),
                     'hold':sum(c['state']=='hold' for c in items),'unassigned':sum(not c.get('owner') for c in items)}}   # 기한은 선택 항목 · 담당자 없음만 미배정


def human_expected(st, ch, team=None):
    row=read_row(st,ch,team); token=basis(row)['token']
    final=((st.get_report('final_verdicts',team) or {}).get('items') or {}).get(ch) or {}
    if final.get('verdict')=='good' and final.get('basis_token')==token:
        return {k:copy.deepcopy((row.get('item_meta') or {}).get(k)) for k in FIELDS}
    record=st.get_report('ops_review_'+ch,team)
    if not record: return {}
    latest={r['by']:r for r in record['reviews'] if r['basis']['token']==token}
    result={}
    for field in FIELDS:
        votes=[r['axes'][field] for r in latest.values() if field in r['axes']]
        if votes and all(v['status']=='accurate' for v in votes) and all(v['value']==votes[0]['value'] for v in votes):
            result[field]=votes[0]['value']
    return result
