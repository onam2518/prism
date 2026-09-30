"""같은 합성 콘텐츠로 엔티티 확신도 산출을 측정한다(네트워크·LLM 호출 없음)."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--rows', type=int, default=200)
    parser.add_argument('--entities', type=int, default=20)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if not (1 <= args.rows <= 1000 and 1 <= args.entities <= 200 and 1 <= args.repeats <= 20):
        parser.error('rows=1~1000, entities=1~200, repeats=1~20 범위가 필요합니다')
    sys.path.insert(0, args.repo)
    from prism import entconf
    names = ['구단' + str(i) for i in range(args.entities)]
    rows = [({'title': names[i % len(names)] + ' 경기 소식',
              'body': (' · '.join(names) + '의 경기 소식. ') * 100},
             {'summary': ' · '.join(names[:3]),
              'entities': [{'name': n, 'type': 'OG'} for n in names]}) for i in range(args.rows)]
    elapsed = []
    for _ in range(args.repeats):
        started = time.perf_counter()
        out = [entconf.scored_entities(im, ref) for ref, im in rows]
        elapsed.append((time.perf_counter() - started) * 1000)
    digest = hashlib.sha256(json.dumps(out, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    print(json.dumps({'rows': args.rows, 'entities_per_row': args.entities, 'repeats': args.repeats,
                      'body_characters': len(rows[0][0]['body']),
                      'median_ms': round(statistics.median(elapsed), 2),
                      'result_sha256': digest}, ensure_ascii=False))


if __name__ == '__main__':
    main()
