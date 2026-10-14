"""엑셀 → input.json · 사용: python3 prepare.py <입력.xlsx> <출력.json>
열 이름이 달라도 아래 후보 중 먼저 맞는 열을 쓴다. 키·제목·본문이 모두 빈 행(서식만 있는 행)은 건너뛴다."""
import json, sys, openpyxl
ID = ('콘텐츠키', 'item_unique_key', 'ID', 'id', '키')
SVC = ('서비스', 'displayServiceName', 'service')
TITLE = ('displayTitle', '제목', 'title', '타이틀', '콘텐츠 이름')
BODY = ('body', '본문', '내용')
wb = openpyxl.load_workbook(sys.argv[1], read_only=True)
ws = wb.worksheets[0]
rows = list(ws.iter_rows(values_only=True))
hdr = [str(h or '').strip() for h in rows[0]]
pick = lambda cands: next((hdr.index(c) for c in cands if c in hdr), None)
i_id, i_s, i_t, i_b = pick(ID), pick(SVC), pick(TITLE), pick(BODY)
assert i_t is not None or i_b is not None, f'제목·본문 열을 찾지 못함: {hdr}'
out = []
for n, r in enumerate(rows[1:], 2):
    g = lambda i: str(r[i]).strip() if i is not None and i < len(r) and r[i] is not None else ''
    t, b = g(i_t), g(i_b)
    if not (g(i_id) or t or b):
        continue
    out.append({'id': g(i_id) or f'row{n}', 'service': g(i_s), 'title': t, 'body': b})
json.dump(out, open(sys.argv[2], 'w'), ensure_ascii=False)
print('rows', len(out), 'unique ids', len({o['id'] for o in out}),
      'empty text', sum(1 for o in out if not (o['title'] or o['body'])))
