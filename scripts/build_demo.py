#!/usr/bin/env python3
"""docs/ 데모 HTML 4종을 examples/demo-results.jsonl(합성 40건)로 최신 코드 기준 재생성.

- demo.html          : 통합(아이템 메타 + 메타풀 + 사용자 메타) 탭 단일 HTML
- demo-items.html    : 아이템 메타 대시보드(단독)
- demo-metapool.html : 메타풀 생성 체계(단독)
- demo-users.html    : 사용자 메타(목업, n_users=200)

사용:  python3 scripts/build_demo.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prism import dashboard as DASH
from prism import metapool as MP
from prism import usermeta as UM

RESULTS = os.path.join(ROOT, "examples", "demo-results.jsonl")
DOCS = os.path.join(ROOT, "docs")
N_USERS = 200

# DEMO 배너(합성 데이터 고지). 통합/단독 모두 동일 적용.
NOTICE = ('<div style="background:rgba(226,163,60,.1);border-bottom:1px solid '
          'rgba(226,163,60,.35);color:#e2a33c;padding:9px 24px;font-size:12.5px">'
          '<b>DEMO</b> (합성 예시 데이터 · 실제 추출 결과 아님)</div>')


def main():
    os.makedirs(DOCS, exist_ok=True)

    # 통합(탭 단일 HTML): 각 패널 iframe(srcdoc) 격리
    info = DASH.build_integrated(RESULTS, os.path.join(DOCS, "demo.html"),
                                 title="Prism", n_users=N_USERS,
                                 demo=True, notice=NOTICE)

    # 단독 페이지들
    DASH.build(RESULTS, os.path.join(DOCS, "demo-items.html"),
               title="아이템 메타 (DEMO)", notice=NOTICE)
    MP.build_html(RESULTS, os.path.join(DOCS, "demo-metapool.html"), notice=NOTICE)
    UM.build_html(RESULTS, os.path.join(DOCS, "demo-users.html"),
                  n_users=N_USERS, demo=True, notice=NOTICE)

    mp = MP.build_metapools(RESULTS)["summary"]
    print(f"✓ docs/demo*.html 재생성 (콘텐츠 {info['contents']})")
    print(f"  메타풀: 단독 {mp['single']} · 복합 {mp['composite']} · "
          f"필터 {mp['filter']}(활성 {mp['filter_active']})")


if __name__ == "__main__":
    main()
