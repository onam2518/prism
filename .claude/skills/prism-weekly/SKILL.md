---
name: prism-weekly
description: 프리즘 주간회의 자료 PPTX 생성. 사용자가 "주간회의 자료 만들어줘", "위클리 자료 준비", "주간 보고 만들어줘"처럼 요청할 때 사용. 에이전트 팀 병렬 수집(변경사항 · 검수 데이터 분석 · 정책 안건) → 화면 캡처 포함 prism-slides 덱 작성 → Artifact 사전 검수 → 승인 후 슬라이드 이미지 방식 PPTX 변환 · 전달.
---

# prism-weekly · 주간회의 자료 생성

3가지 꼭지로 주간회의 PPTX 를 만든다.

1. 지난주까지 변경사항(기능 · 정책) · 주요 개편은 화면 캡처와 함께 상세 소개
2. 지난주까지 검수 데이터 분석
3. 정책 논의 안건(상위 의사결정 필요 건만 · 개발 과제 제외)

산출 흐름: **에이전트 병렬 수집 → 화면 캡처 → 종합 → prism-slides HTML 덱 → Artifact 사전 검수(사용자 승인) → 슬라이드 이미지 캡처 → PPTX → `~/Desktop/` 전달.**
사용자 승인 전에는 절대 PPTX 를 만들지 않는다.

## 사전 조건

- 키 파일: `~/.prism_supabase_url` · `~/.prism_supabase_key` (운영 Supabase 읽기 조회용)
- 도구: `gh` · Google Chrome + Node 22 이상 · prism-slides 스킬(`~/.claude/skills/prism-slides`)
- **운영 DB 는 읽기 전용**: PostgREST 에 GET 만 보낸다. POST · PATCH · DELETE 금지.
  service key 를 출력 · 로그 · 슬라이드 · 에이전트 반환값에 남기지 않는다.
- **운영 DB 시간 컬럼은 timestamptz 다(epoch 아님)**: 기간 필터는 UTC ISO 로.
  예: KST 7/14 00:00 = `ts=gte.2026-07-13T15:00:00Z`.
- 마커 파일 `~/.prism_weekly_last`: `YYYY-MM-DD<TAB>끝PR번호`. 없으면 사용자에게 시작점을 묻는다.
- 변경사항 상세의 정본: 컨플루언스 **"Prism 기능 및 변경 사항"** (DNM · pageId `419037211`).
  최신이면 과제 배경 · AS-IS/TO-BE 를 여기서 가져다 쓴다(Atlassian MCP `getConfluencePage`).

## 1. 범위 결정

- 기본 범위: 마커 다음날 00:00(KST)부터 실행일 기준 직전 일요일 24:00(KST)까지.
- 사용자 인자(날짜 · PR 번호)가 있으면 그것을 우선한다.
- 에이전트에게 넘길 때 KST 표기와 UTC ISO 경계를 둘 다 계산해 전달한다.
- PR 번호가 낮아도 기간 내 머지된 장기 브랜치가 흔하다. mergedAt 으로만 필터하고,
  의심스러우면 gh 로 직접 재검증한다.

## 2. 에이전트 팀 병렬 수집

1차 3팀을 **한 메시지에 동시에** 스폰한다. 반환은 원시 데이터(JSON)로 받는다.

### A. 변경사항 수집
- `gh pr list --state merged --limit 200 --json number,title,mergedAt,body` → 기간 필터.
- 과제 그룹핑(prism-changelog 규칙 준용): 정책 · 신규 기능 각각 항목, 리팩토링 · 버그픽스는 "기반 정비" 묶음, docs 는 비고 흡수. 8~12건.
- 반환: `[{과제명, 구분(기능|정책|기반), 배경, asis, tobe, prs}]`.

### B. 검수 데이터 분석
- 조회 예(GET 만 · timestamptz):
  ```bash
  U=$(cat ~/.prism_supabase_url)/rest/v1; K=$(cat ~/.prism_supabase_key)
  curl -s "$U/prism_feedback?select=verdict,reviewer,stage,ts&ts=gte.<시작ISO>&ts=lt.<끝ISO>" \
    -H "apikey: $K" -H "Authorization: Bearer $K"
  ```
  테이블: `prism_feedback` · `prism_golden` · `prism_gold_checks` · `prism_assignments` · 팀 컬럼 `team_id` ·
  검수자 이름은 `prism_reviewers` 매핑. 1000행 제한 시 limit/offset 페이지네이션.
- 지표(정의는 `prism/dashops.py` `_dashboard_compute` 와 일치): 기간 검수 건수 · verdict 분포 ·
  stage 분포 · 참여자와 1인당 처리량 · 골든 신규/누적 · 골드체크 정답률 · 전주 대비.
  기간 길이가 다르면 하루 평균으로 비교한다. 표본 적은 지표(골드체크 등)는 "참고치" 명시.
- 반환: `{지표, 전주 대비, 눈에 띄는 점 1~3개}`.

### C. 정책 안건 수집 (PART 3 용)
- **정책 · 운영 기준 · 거버넌스만. 개발 과제(성능 · 리팩토링 · 버그 · 마이그레이션)는 제외한다.**
- 수집원: 코드의 결정 대기 기본값(golden_min_good, 보존 기한, 권한 매트릭스 등) ·
  기간 PR 본문의 "추후 결정 · 협의 필요" · HANDOFF.md · LEARNING_DESIGN.md · 회의에서 미룬 기준.
- 반환: `[{안건, 상황(현재 기본값 · 수치 인용), 결정할 것, 근거(파일:라인 또는 PR)}]` · 5~8건.

### 2차 수집 (1차 결과 확인 후 필요 시)
- **상세(detail)**: 주요 신규 기능 · 대규모 개편 3~5건에 대해 what/why/할 수 있는 것/흐름/기본값/PR 수집.
- **게시판 반영(boarddone)**: `prism_board?select=*` 로 처리 완료 건과 답변을 모으고 반영 PR 매칭.
  미처리 건이 있으면 논의 안건 후보로도 넘긴다.

## 3. 화면 캡처 (주요 기능 상세용)

운영 데이터를 쓰지 않는다. 격리 목 서버 + 시드로 찍는다.

```bash
D=<scratchpad>/qa && mkdir -p $D && cd <저장소>
export PRISM_BACKEND=sqlite PRISM_DB=$D/qa.db PRISM_CONFIG=$D/qa_config.json PRISM_ENTDICT_ENRICH=0
python3 scripts/seed_qa.py
PRISM_BACKEND=sqlite PRISM_DB=$D/qa.db PRISM_CONFIG=$D/qa_config.json PRISM_ENTDICT_ENRICH=0 \
  python3 -m prism.serve --mock --port 8978 &      # 8765 회피
node .claude/skills/prism-weekly/capture_screens.mjs <shots.json> <scratchpad>/shots
cd <scratchpad>/shots && for f in *.png; do sips -s format jpeg -s formatOptions 90 "$f" --out "${f%.png}.jpg"; done
# 해상도 유지(리사이즈 금지 · 캡처 원본 4320×2700). 슬라이드를 8K 로 재캡처하므로
# 여기서 줄이면 PPT 에서 뭉개진다. HTML 이 너무 커져 Artifact 배포가 실패할 때만 -Z 2880 로 낮춘다.
```

- `shots.json` 예: `{"base":"http://127.0.0.1:8978","shots":[{"name":"labrun","url":"/?m=lab","clicks":["사용자"]}]}`
  모듈 은 URL `?m=` (lab 실험실 · studio 스튜디오 · create 콘텐츠 검수 · evaluate 평가 · dict 사전정책 · board 게시판).
- **함정: 탭 클릭은 버튼 텍스트 정확 일치(===)로 찾는다.** 부분 일치는 네비 메뉴("사전 · 정책")를 잘못 누른다.
  스크립트 기본이 정확 일치이므로 clicks 에 버튼 라벨을 그대로 쓴다(예: 실험실 서브탭 `["사용자","정책"]`).
- 검수자 등록 모달은 스크립트가 localStorage 프리셋으로 우회한다.
- 슬라이드 소스에는 `__SHOT_<이름대문자>__` 플레이스홀더(img src)로 넣고, prism-slides 빌드 후
  `python3 .claude/skills/prism-weekly/embed_shots.py <빌드.html> <최종.html> <shots 디렉토리>` 로 치환한다
  (토큰 `__SHOT_LABRUN__` ↔ 파일 `labrun.jpg` 소문자 일치 규약).
- 종료 시 목 서버 · headless Chrome 프로세스를 반드시 정리한다.

## 4. 덱 작성 (prism-slides)

prism-slides 스킬 규칙을 따르되, 아래 구성 · 레이아웃 규칙을 지킨다(사용자 확정 사항).

### 표준 구성 (18~22장)

| 순서 | 내용 | 캐릭터 |
| --- | --- | --- |
| 표지 · 목차 | "프리즘 주간회의 · MM.DD" + 3꼭지 agenda | 팀 그리드 |
| PART 1 divider | 변경사항 | 딱지(감독) |
| 전체 지도 | 과제 12건 표(과제 · 구분 칩 · 한 줄) | 없음 |
| **주요 기능 상세 4~6장** | 대규모 개편 · 신규 기능당 1~2장, **화면 캡처 필수** | 없음 |
| 그 밖의 기능 | 나머지 기능 카드 | 없음 |
| 게시판 반영 | 접수 의견 → 반영 표(제안자 표기 · 미처리 건수 명시) | 없음 |
| 정책 요약 | 정책 카드 + 화면 + 기반 정비 한 줄 | 딱지 말풍선 |
| **정책 상세** | **AS-IS → TO-BE 표**(무엇이 · 전에는 · 이제는) | 없음 |
| PART 2 divider + 3장 | 숫자 타일 · verdict 밴드 · 골든셋/검수자 | 대식 · 복실 |
| PART 3 divider + 2장 | 정책 안건 체크리스트(그룹 2개로 분할) | 용희 말풍선 |
| 마무리 | 오늘 정할 것 3건 **가로형 행**(안건 · 지금 상황 · 오늘 정할 것) + 팀 스트립 | 4종 |

### 레이아웃 규칙 (필수)

- **개수별 배치**: 짝수 4건 = 2×2 로 페이지를 채움(카드 · 글자 확대) · 홀수 5건 = 윗줄 3 + 아랫줄 2 가운데 정렬 ·
  3건 이하 = 한 줄. (CSS: `.n4 { grid-template-columns:1fr 1fr }` · `.n5 { repeat(6,1fr); >* {span 2}; :nth-child(4){2/span 2} }`)
- **기능 상세 슬라이드(featrow)**: **화면 캡처 왼쪽 · 텍스트 오른쪽**. 텍스트는 테두리 박스(.txtbox) 안에 · 글자 1.05em ·
  **두 박스 높이 동일**(텍스트가 길면 이미지가 object-fit: cover 로 늘어나 하단 크롭) · **캡션은 그리드 밖 이미지 아래 좌측정렬**.
- **마무리**: 안건별 가로형 행 = [번호+안건명(노란 배경) | 지금 상황 | 오늘 정할 것] 3열 그리드.
- **용어**: "2층"처럼 구조 이름으로 사람 행위를 표현하지 않는다("최종 검수자가 확정"). 쉬운 일상어 · em dash 금지 ·
  수치는 실데이터 인용 · 표본 적으면 참고치 명시.
- **문장·표기의 정본은 `../weekly-study/references/writing-rules.md`.** 특히 로마자·숫자 뒤 조사 붙이기(G입니다 ·
  PART 3에서) · '-별' 붙임(Tier 1별) · 인용 부호는 둥근 홑따옴표 통일 · 내부 스펙 명칭 순화(예: 토픽 유형은
  "엔티티 · 사건 · 관심사 묶음"으로 쓰고 정식 명칭은 범례 한 줄) · 도해 SVG 라벨·aria-label 까지 동일 적용.

## 5. Artifact 사전 검수

빌드 + 캡처 임베드된 HTML 을 Artifact 로 배포하고 사용자 확인을 받는다. 피드백 반영 시
같은 파일 경로로 재배포(같은 URL 유지). **승인 전에는 6단계로 넘어가지 않는다.**

- 배포 전에 slop-check 스킬로 전 슬라이드를 검수한다(도해 라벨 · 표 안 텍스트 포함).
- 파일을 수정한 뒤에는 반드시 그 수정본으로 배포 파일을 다시 만든다. 원본에 저장하지 않고
  배포본만 갱신하면 다음 재배포 때 수정이 사라진다(DNM 덱 부록 유실 사고의 원인).

## 6. PPTX 변환 (슬라이드 이미지 방식)

```bash
SK=.claude/skills/prism-weekly
node $SK/capture_deck.mjs <최종덱.html> <scratchpad>/slides       # 장당 7680×4320 PNG(기본 4배)
python3 $SK/build_pptx.py <scratchpad>/프리즘_주간회의_YYYYMMDD.pptx <scratchpad>/slides
cp <scratchpad>/프리즘_주간회의_YYYYMMDD.pptx ~/Desktop/
```

- 이미지 방식이라 PPT 안에서 텍스트 수정 불가. 수정 요청 시 소스 수정 → 5~6단계 반복.
- 기본 4배(8K)가 화질 상한: 5배 이상은 캡처 지연·용량만 늘고 육안 차이가 없다.
  PPTX 가 수십 MB 로 커질 수 있다 · 공유처 용량 제한에 걸리면 scale 인자 3으로 재캡처.

## 7. 마무리

- `~/.prism_weekly_last` 를 `커버끝날짜<TAB>덱에 반영된 끝 PR 번호` 로 갱신.
- 띄운 프로세스(목 서버 · headless Chrome · http.server) 정리.
- 보고: PPTX 경로 · Artifact 링크 · 꼭지별 한 줄 요약.

## 주의사항

- 운영 DB 쓰기 금지 · service key 노출 금지.
- 데이터가 빈 꼭지도 슬라이드는 만들되 "이번 주 없음"으로 명시한다.
- 팀이 여럿 조회되면(team_id 여러 값) 합산할지 팀별로 나눌지 사용자에게 확인한다.
- 슬라이드 내용이 1080px 높이를 넘치면 캡처가 잘린다. Artifact 검수 단계에서 넘침을 확인한다.
