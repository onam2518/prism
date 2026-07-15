"""QA 목업 시드 CLI: 로컬 sqlite 스토어에 콘텐츠 10건 + 검수·정답·판정 예시 적재.

사용: PRISM_BACKEND=sqlite PRISM_DB=./qa.db python3 scripts/seed_qa.py
(스토어가 비어 있을 때만 시드 · 멱등)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PRISM_BACKEND", "sqlite")
# 시드는 오프라인 결정성: 콘텐츠 적재가 위키데이터 보강 데몬 스레드를 띄우면
# 스크립트 종료 시 sqlite 사용 중 스레드가 강제 종료되며 segfault(exit 139)가 난다.
os.environ.setdefault("PRISM_ENTDICT_ENRICH", "0")

from prism import qa_seed  # noqa: E402

if __name__ == "__main__":
    out = qa_seed.seed()
    print(out)
