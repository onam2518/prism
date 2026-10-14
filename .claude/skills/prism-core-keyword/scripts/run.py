"""운영 컨테이너 안에서 실행 · 4종 메타(리드문·엔티티·인텐트·카테고리)만 추출 · DB 미저장 · 이어서 돌리기 지원.
사용(컨테이너): cd /app && PYTHONPATH=/app nohup python3 /data/kw/run.py <모델> <입력.json> <출력.jsonl> [동시수] > /data/kw/log 2>&1 &
출력에 이미 있는 id 는 건너뛴다(배포 재시작으로 끊기면 같은 명령을 다시 실행). 끝나면 <출력>.done 생성."""
import json, os, sys, threading
from concurrent.futures import ThreadPoolExecutor
from prism import serve as S, pipeline as PIPE
from prism.runops import input_auxiliary
S.load_dict_overrides(); S.sync_prompt()          # 운영 사전 덮어쓰기·학습 보정 · 빠지면 운영과 프롬프트가 달라진다
MODEL, SRC, DST = sys.argv[1], sys.argv[2], sys.argv[3]
llm, route = S.llm_for_model(MODEL, False)
assert llm is not None and not llm.mock and llm.model == MODEL, (route, getattr(llm, 'model', None))
items = json.load(open(SRC))
done = set()
if os.path.exists(DST):
    for line in open(DST):
        try: done.add(json.loads(line)['id'])
        except Exception: pass
lock = threading.Lock()
def one(it):
    if it['id'] in done:
        return
    f = {'displayServiceName': it['service'], 'title': it['title'], 'body': it['body'], 'item_unique_key': it['id']}
    content = {'displayServiceName': it['service'], 'title': it['title'], 'subtitle': '', 'body': it['body'],
               'source_url': '', 'image_urls': [], 'input_aux': input_auxiliary(f), 'item_unique_key': it['id']}
    try:
        out = PIPE.extract(content, llm, legal=False, quality=False)   # 품질·법령 제외 = 메타 4호출만
        im = out.get('item_meta') or {}
        rec = {'id': it['id'], 'summary': im.get('summary', ''), 'entities': im.get('entities') or [],
               'intent': im.get('intent') or [], 'content_category': im.get('content_category') or [],
               'meta_status': im.get('meta_status') or {}, 'cost': (out.get('trace') or {}).get('cost_usd', 0),
               'models': sorted({c.get('model', '') for c in ((im.get('run_manifest') or {}).get('calls') or {}).values()})}
    except Exception as e:
        rec = {'id': it['id'], 'error': repr(e)[:200]}
    with lock:
        with open(DST, 'a') as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
with ThreadPoolExecutor(int(sys.argv[4]) if len(sys.argv) > 4 else 6) as ex:
    list(ex.map(one, items))
open(DST + '.done', 'w').write('ok')
