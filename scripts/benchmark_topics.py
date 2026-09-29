"""합성 콘텐츠로 토픽 계산 측정. --from-file은 서버의 이전 JSONL 왕복 경로를 재현한다."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--rows', type=int, default=1500)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--from-file', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.rows <= 10000 or not 1 <= args.repeats <= 50:
        parser.error('rows는 1~10000, repeats는 1~50 범위여야 합니다')
    sys.path.insert(0, args.repo)
    from prism import topic
    rows = [{'content_ref': {'title': f'기사 {i}', 'body': '본문 내용 ' * 600, 'displayServiceName': '뉴스'},
             'item_meta': {'entities': [{'name': f'선수{i//20}', 'type': 'PS'}, {'name': f'구단{i//100}', 'type': 'OG'}],
                           'intent': ['경기 결과·리뷰'],
                           'content_category': [{'tier1': 'Sports', 'tier2': 'Soccer (International)'}]},
             'quality_meta': {'finalGrade': 'G'}} for i in range(args.rows)]
    elapsed = []
    for _ in range(args.repeats):
        started = time.perf_counter()
        if args.from_file:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'rows.jsonl'
                path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
                out = topic.build_topics(str(path))
        else:
            out = topic.build_topics_rows(rows)
        elapsed.append((time.perf_counter() - started) * 1000)
    digest = hashlib.sha256(json.dumps(out, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    print(json.dumps({'rows': args.rows, 'repeats': args.repeats, 'from_file': args.from_file,
                      'median_ms': round(statistics.median(elapsed), 2), 'result_sha256': digest,
                      'summary': out['summary']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
