---
name: prism-core-keyword
description: 핵심키워드 실험용 메타 추출. 콘텐츠 엑셀(키·서비스·제목·본문)을 프리즘 운영 파이프라인에 태워 리드문·엔티티·인텐트·카테고리 4종만 추출하고, 키워드 실험실 업로드 양식(100건씩)과 전체 정리본 xlsx로 전달한다. 사용자가 "핵심키워드", "이 엑셀 메타 추출해줘", "키워드 실험실 양식으로", "리드문 엔티티 카테고리 인텐트 뽑아줘"라고 할 때 사용.
---

# prism-core-keyword · 핵심키워드용 메타 추출

콘텐츠 엑셀을 받아 **운영과 같은 코드·프롬프트·학습 보정**으로 메타 4종을 뽑고, 키워드 실험실에 바로 올릴 수 있는 양식으로 전달한다. 운영 DB에는 저장하지 않는다.

## 준비물

- 입력 엑셀: 첫 시트 · 열 후보 `콘텐츠키`·`서비스`·`displayTitle`(제목)·`body`(본문). 서식만 있는 빈 행이 많을 수 있다(1,000행처럼 보여도 실제 38건인 경우가 있었다).
- 전달 양식: 이 스킬 폴더의 `keyword_lab_template.xlsx` (원본 `~/Downloads/keyword_lab_template.xlsx` · 열: 콘텐츠 이름 / 리드문 / 엔티티 / 인텐트 / 카테고리 / 원문 URL(선택 · 넣으면 상세 검수에 원문 페이지 탭))
- 스크립트: `scripts/prepare.py` · `scripts/run.py` · `scripts/deliver.py`
- 작업 폴더: 세션 scratchpad 아래 `kw/` (로컬) · 운영 볼륨 `/data/kw` (컨테이너)

## 절차

1. **모델 확인**
   - 사용자가 말한 모델명이 부정확하면(예: "PRO 4-2600800") 운영 기본 실행 모델로 맞춘다.
   - 운영 기본 모델 확인: `fly ssh console -a prism-item -C "python3 -c \"from prism.config import Config; from prism import serve as S; print(S.make_text_llm(Config.load(), False).model)\""`
   - 라우터 쪽 정확한 id 는 Timely 모델 목록으로 확인(로컬 키는 만료일 수 있음 · 컨테이너의 PRISM_TIMELY_KEY 로 조회).
   - 답장에 "어떤 모델로 해석했는지" 한 줄 남긴다.

2. **입력 정리** (로컬)
   ```
   python3 scripts/prepare.py "<입력.xlsx>" <scratch>/kw/input.json
   ```
   - 출력의 rows 수를 사용자 기대와 대조. 빈 텍스트 행이 있으면 알린다(호출 없이 insufficient_input).
   - 비용 감: 건당 약 $0.007(solar-pro4 · 4호출) · 소요 약 6건/분(동시 6~8).

3. **운영 컨테이너에서 추출**
   - 다른 세션의 main 배포로 컨테이너가 수 분마다 재시작될 수 있다 → `/tmp` 금지, **`/data/kw` 볼륨** 사용 · 끊기면 같은 명령 재실행(완료 id 건너뜀).
   - 시작 전 `gh run list -R onam2518/prism --branch main --limit 3` 으로 진행 중 배포가 있으면 끝난 뒤 시작.
   ```
   fly ssh console -a prism-item -C "sh -c 'rm -rf /data/kw; mkdir -p /data/kw'"
   fly ssh sftp put <scratch>/kw/input.json /data/kw/input.json -a prism-item
   fly ssh sftp put scripts/run.py /data/kw/run.py -a prism-item
   fly ssh console -a prism-item -C "sh -c 'cd /app && PYTHONPATH=/app nohup python3 /data/kw/run.py <모델> /data/kw/input.json /data/kw/out.jsonl 8 > /data/kw/log 2>&1 & echo started'"
   ```
   - 진행 확인: `wc -l < /data/kw/out.jsonl` · 완료 표식 `/data/kw/out.jsonl.done` · 재시작 여부 `cut -d" " -f1 /proc/uptime`
   - `fly ssh sftp put` 은 덮어쓰지 않는다 → 같은 경로는 먼저 `rm`.
   - 컨테이너에 `ps`·`pkill` 없음. `PYTHONPATH=/app` 빠지면 `No module named prism`.

4. **실패 항목 재시도**
   - 결과를 받아(`fly ssh sftp get /data/kw/out.jsonl ...`) meta_status 가 call_failed·invalid_output 인 id 만 골라 retry.json 으로 만들고 `run.py ... /data/kw/retry.json /data/kw/retry.jsonl` 로 한 번 더.
   - 재시도도 실패하면 더 돌리지 말고 전달 때 표시한다.
   - 실행 모델 검증: 결과의 `models` 가 전부 지정 모델인지 확인(run_manifest 기준 · 표기 라벨이 아님).

5. **전달 파일 만들기** (로컬)
   ```
   python3 scripts/deliver.py <scratch>/kw/input.json <scratch>/kw/out.jsonl,<scratch>/kw/retry.jsonl keyword_lab_template.xlsx ~/Desktop/<모델>_v<버전>_<YYYYMMDD>
   ```
   - 산출: `…_keywordlab_1.xlsx`, `_2.xlsx` …(100건씩 · 키워드 실험실 MAX_ITEMS) + `…_meta.xlsx`(ID·타이틀·본문·리드문·인텐트·엔티티(유형)·카테고리 전체본)
   - 파일명 규칙: 모델_v버전_요청일자(KST) · 버전은 운영 학습 버전(batch_seq+1)
   - 양식 규칙(keywordlab.parse_excel): 콘텐츠 이름=제목(300자 이내) · 값 여러 개는 `, ` · 엔티티는 이름만 · 카테고리 `Tier1 / Tier2` · 리드문 또는 엔티티가 비면 업로드에서 제외된다.

6. **정리·보고**
   - `fly ssh console -a prism-item -C "rm -rf /data/kw"` 로 컨테이너 임시 파일 삭제.
   - 보고: 건수 · 실제 실행 모델 · 비용 · 실패/재시도 결과 · 업로드에서 빠질 행(엔티티 없음 등) · 파일 경로.

## 하지 않는 것

- `run_pipeline(persist=False)` 사용 금지: 품질·법령 판정까지 돌아 비용이 늘고 등급 R 이면 메타가 버려질 수 있다. `PIPE.extract(quality=False, legal=False)` 가 메타 4호출과 같은 경로다.
- 운영 DB 저장(store_save·add_contents) 금지 · 실험 결과는 파일로만 전달.
- 로컬에서 추출 금지: 로컬 키·사전 덮어쓰기·학습 보정이 운영과 달라 결과가 운영과 어긋난다.
- 호출 실패 분류 필드에는 파이프라인이 `ETC_OUT` 을 넣는다(2026-10-14 정책) · 그대로 전달하고 보고에 건수를 적는다.
