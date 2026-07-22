---
name: prism-weekly
description: 프리즘 주간회의 자료 PPTX 생성. 사용자가 "주간회의 자료 만들어줘", "위클리 자료 준비", "주간 보고 만들어줘"처럼 요청할 때 사용. 에이전트 팀 병렬 수집(변경사항 · 검수 데이터 분석 · 소요 접수) → prism-slides 덱 작성 → Artifact 사전 검수 → 승인 후 슬라이드 이미지 방식 PPTX 변환 · 전달.
---

# prism-weekly · 주간회의 자료 생성

3가지 꼭지로 주간회의 PPTX 를 만든다.

1. 지난주까지 변경사항(기능 · 정책)
2. 지난주까지 검수 데이터 분석
3. 추가 논의 필요과제 · 개선 필요과제 소요 접수

산출 흐름: **에이전트 3팀 병렬 수집 → 종합 → prism-slides HTML 덱 → Artifact 사전 검수(사용자 승인) → 슬라이드 이미지 캡처 → PPTX → `~/Desktop/` 전달.**
사용자 승인 전에는 절대 PPTX 를 만들지 않는다.

## 사전 조건

- 키 파일: `~/.prism_supabase_url` · `~/.prism_supabase_key` (운영 Supabase 읽기 조회용)
- 도구: `gh`(머지 PR 수집) · Google Chrome + Node 22 이상(캡처) · prism-slides 스킬(`~/.claude/skills/prism-slides`)
- **운영 DB 는 읽기 전용**: PostgREST 에 GET 만 보낸다. POST · PATCH · DELETE 금지.
  service key 를 출력 · 로그 · 슬라이드 · 에이전트 반환값에 남기지 않는다.
- 마커 파일 `~/.prism_weekly_last`: `YYYY-MM-DD<TAB>끝PR번호` (지난 자료가 커버한 끝 시점).
  없으면 사용자에게 시작점을 묻는다.

## 1. 범위 결정

- 기본 범위: 마커 다음날 00:00(KST)부터 실행일 기준 직전 일요일 24:00(KST)까지.
- 사용자 인자(날짜 · PR 번호)가 있으면 그것을 우선한다.
- gh 의 mergedAt 과 DB 의 ts(epoch 초)는 UTC 다. KST(+9h) 변환 후 비교한다.
- 에이전트에게 넘길 때는 KST 표기와 epoch 값을 둘 다 계산해 전달한다.

## 2. 에이전트 팀 병렬 수집

Agent tool 3개를 **한 메시지에 동시에** 스폰한다. 각 에이전트에게 기간(KST 시작 · 끝 + epoch 값)과
아래 임무 · 반환 형식을 그대로 전달한다. 반환은 사람용 문장이 아니라 원시 데이터(JSON)로 받는다.

### A. 변경사항 수집

- `gh pr list --state merged --limit 200 --json number,title,mergedAt,body` 로 기간 내 머지 PR 수집.
- 과제 그룹핑(prism-changelog 규칙 준용): 정책 변화와 신규 기능이 각각 항목 ·
  리팩토링과 자잘한 버그픽스는 "기반 정비" 한 항목으로 묶음 · docs 단독 PR 은 관련 과제 비고로 흡수.
  근거가 부족하면 `git log` 커밋 본문과 `HANDOFF.md` 로 배경을 보강.
- 반환: `[{과제명, 구분(기능|정책|기반), 배경 한 줄, asis, tobe, pr번호들}]` · 8~12건 이내.

### B. 검수 데이터 분석

- 운영 Supabase PostgREST 를 GET 으로만 조회한다:

  ```bash
  U=$(cat ~/.prism_supabase_url)/rest/v1; K=$(cat ~/.prism_supabase_key)
  curl -s "$U/prism_feedback?select=verdict,reviewer,stage,ts&ts=gte.<시작epoch>&ts=lt.<끝epoch>" \
    -H "apikey: $K" -H "Authorization: Bearer $K"
  ```

  주요 테이블: `prism_feedback`(판정 원장: verdict · reviewer · stage · ts) · `prism_golden`(정답셋) ·
  `prism_gold_checks`(골드 문항 판정 correct) · `prism_assignments`(배정) · 팀 구분 컬럼은 `team_id`.
  PostgREST 는 기본 1000행 제한이므로 건수가 많으면 `limit`/`offset` 또는 `Range` 헤더로 페이지네이션.
- 집계 지표(정의는 `prism/dashops.py` 의 `_dashboard_compute` 와 일치시킨다):
  기간 내 검수 건수 · verdict 분포 · 참여 검수자 수와 1인당 처리량 · 골든셋 신규 적립과 누적 ·
  골드체크 정답률 · 전주 대비 증감.
- 반환: `{지표별 수치, 전주 대비, 눈에 띄는 점 1~3개(실데이터 근거)}`.

### C. 소요 접수

- 게시판: `prism_board?status=in.(open,doing)&select=id,kind,title,body,reviewer,status,ts` 로
  미처리 기능개선 제안(kind=feature)과 오류 제보(kind=bug)를 수집.
- 보강: `HANDOFF.md` 잔여 과제 · `docs/CODE_AUDIT_2026-07-15.md` 백로그 ·
  사용자가 대화에서 언급한 이월 안건.
- 반환: `[{제목, 출처(게시판|백로그|감사), 상태, 접수일, 논의포인트 한 줄}]`.

## 3. 덱 작성 (prism-slides)

prism-slides 스킬 규칙을 그대로 따른다(예제 CSS 복사 · 빌드 · 스크린샷 검증). 표준 구성 10~16장:

| 순서 | 내용 | 캐릭터 |
| --- | --- | --- |
| 표지 | "프리즘 주간회의 · MM.DD" + 팀 그리드 | 4종 |
| 목차 | 3꼭지 agenda | 없음 |
| PART 1 | 변경사항: 과제 카드(기능/정책 배지) · AS-IS→TO-BE | 딱지(감독) |
| PART 2 | 검수 데이터: 숫자 타일 · verdict 밴드 · 추이 | 대식(타자) · 품질 지표는 복실(포수) |
| PART 3 | 소요 접수: 논의 안건 체크리스트 · 출처 배지 | 용희(투수) |
| 마무리 | 오늘 결정할 것 요약 | 딱지 |

글쓰기: em dash 금지 · 쉬운 일상어 · 수치는 실데이터 인용(`.tag.ex` 실례 배지) · 결론 먼저.

## 4. Artifact 사전 검수

빌드된 HTML 을 Artifact 로 배포하고 사용자 확인을 받는다. 피드백이 오면
소스 수정 → 재빌드 → **같은 파일 경로로 재배포**를 반복한다.
**사용자가 승인하기 전에는 5단계로 넘어가지 않는다.**

## 5. PPTX 변환 (슬라이드 이미지 방식)

```bash
SK=.claude/skills/prism-weekly            # 저장소 루트 기준
node $SK/capture_deck.mjs <덱.html> <scratchpad>/slides        # 장당 3840×2160 PNG
python3 $SK/build_pptx.py <scratchpad>/프리즘_주간회의_YYYYMMDD.pptx <scratchpad>/slides
cp <scratchpad>/프리즘_주간회의_YYYYMMDD.pptx ~/Desktop/
```

- `capture_deck.mjs`: Chrome headless CDP 로 `.slide` 를 순서대로 활성화해 캡처.
  HUD · 진행바 · 애니메이션은 자동 제거. 종료 시 Chrome 프로세스가 남지 않았는지 확인.
- `build_pptx.py`: PNG 를 16:9 전면 이미지로 삽입. 최초 실행 시 스킬 폴더에
  `.venv`(python-pptx) 를 자동 생성한다.
- 이미지 방식이라 PPT 안에서 텍스트 수정은 불가. 수정 요청이 오면 소스 HTML 을 고치고
  4~5단계를 다시 돈다.

## 6. 마무리

- `~/.prism_weekly_last` 를 `커버끝날짜<TAB>끝PR번호` 로 갱신.
- 띄운 프로세스(http.server · headless Chrome) 정리.
- 보고: PPTX 경로(`~/Desktop/…`) · Artifact 링크 · 꼭지별 한 줄 요약.

## 주의사항

- 운영 DB 쓰기 요청 금지 · service key 노출 금지.
- 데이터가 빈 꼭지(예: 신규 소요 0건)도 슬라이드는 만들되 "이번 주 없음"으로 명시한다.
- 팀이 여럿 조회되면(team_id 여러 값) 합산할지 팀별로 나눌지 사용자에게 확인한다.
- 슬라이드 내용이 1080px 높이를 넘치면 캡처가 잘린다. Artifact 검수 단계에서 넘침을 확인해 둔다.
