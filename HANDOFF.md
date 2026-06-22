# HANDOFF — Prism · DNM 이미지→메타 작업

다음 세션이 바로 이어갈 수 있도록 현재 상태를 정리한 문서. (작성: 2026-06-22)

## 한 줄 요약
DNM 맥락형 콘텐츠 메타(이미지/텍스트/엑셀 → 리드문·엔티티·인텐트·콘텐츠 카테고리 추출)를
**Prism 레포에서 단일 관리**한다. 로컬 웹 UI + 실모델(Upstage Solar) 연결까지 동작.

## 정본 위치
- 레포: `/Users/pete.axz-pc/Desktop/project/prism` (origin `github.com/onam2518/prism`, 사용자 소유)
- 작업 브랜치: **`feat/image-meta-poc`** (push 됨, PR 미생성)
- 폐기/참고용(정본 아님): `~/prism`(작업용 클론), `~/dnm-leadsentence-poc`(초기 standalone PoC, Prism 통합으로 대체)

## 실행 방법
- 바탕화면 **`Prism 실행.command`** 더블클릭 → 서버 기동 + 브라우저 자동 오픈
- 또는: `cd ~/Desktop/project/prism && python3 -m prism.serve` → http://127.0.0.1:8765
- **키 설정은 UI 우상단 '설정' 버튼**(터미널 환경변수 불필요). `~/.prism_key`에 저장되어 재시작 시 자동 로드.
- 코드 바꾸면 **서버 재시작해야** 새 화면 반영(페이지가 메모리에 로드됨).
- `--mock` 플래그 = 키 있어도 강제 mock.

## 현재 상태 (업데이트)
- 키: `~/.prism_key`에 저장됨(실키, hasKey True)
- 생성 모델: **`solar-pro3-260323`** (작동 확인). ⚠️ **`solar-pro4-preview-260528`은 /models 에 뜨지만 chat 호출 시 HTTP400(invalid model)** → 메타 빈 값. 모델은 반드시 chat 가능한 것(pro3/pro2/mini)으로. 설정 저장 시 자동 연결 테스트가 무효 모델을 잡아줌.
- 엔드포인트: `https://api.upstage.ai/v1/solar/chat/completions`
- **실모델 end-to-end 검증 완료**: 텍스트/앱 경유 추출에서 리드문·엔티티 정상 생성.
- 데스크탑 앱: **`~/Desktop/Prism-0.2.0.dmg`** (pywebview .app, 미서명/ad-hoc). 디자인 시안 C + 고급 폴리시 반영.
- 디자인: 시안 C(구조·패널형) 전체 적용 + redesign 스킬 폴리시(빈 상태·스켈레톤·노이즈·focus 링).
- 8765 서버는 백그라운드로 떠 있을 수 있음 → 재시작 전 `lsof -ti tcp:8765 | xargs kill` 권장.
- 앱 config 는 `~/Library/Application Support/Prism/config.json`(frozen). 기존 파일 있으면 새 번들이 덮어쓰지 않으니 모델 바꾸려면 그 파일 수정 또는 앱 설정 UI 사용.

## DNM 메타 체계 (가장 중요 — 코드가 이 기준으로 정렬됨)
페이지 13 + 하위(131·1312·132) 기준. `ItemMeta` 직렬화 키:
| 신규 키 | 의미 | 구 Prism 키(폐기) |
|---|---|---|
| `summary` | 리드문(생성 문장) | `intent` |
| `entities` | 엔티티 | (동일) |
| `intent` | 인텐트(속성 분류값) | `intent_categories` |
| `content_category` | 콘텐츠 카테고리 | `entity_categories` |
| `topic` / `topic_categories` | 토픽(3차, 기본 빈값) | (신규 추가) |

**메타풀 → 토픽 전환**: 단독형→엔티티형 · 복합형→사건형 · 필터형→조건형.
모듈 `metapool.py → topic.py`(함수 `build_topics`, 구 `build_metapools` 별칭 유지).
내부 dict 키 `single/composite/filter`는 레거시로 유지(외부 소비처 없음, docstring에 매핑 명시).

## 코드 구조 (핵심 파일)
- `prism/imagext.py` — 이미지 인제스트 어댑터(방식 A): 이미지 → OCR + DocVision → 4필드 Content 합성. **Prism 코어 무수정**. DocVision 엔드포인트는 config.chat_url 사용.
- `prism/serve.py` — 로컬 웹 UI(stdlib http.server). 탭: **이미지 / 텍스트 / 엑셀**. 설정 패널(키·모델 드롭다운). 엔드포인트: `/run` `/run-batch` `/config` `/ping` `/models` `/vocab` `/report`.
- `prism/pipeline.py·agents.py·prompts.py·verify.py·schema.py` — 추출 파이프라인(신규 스키마 반영, 프롬프트 `imeta@v8`).
- `prism/topic.py` — 토픽(엔티티형·사건형·조건형) 빌더.
- `prism/dashboard.py·usermeta.py` — 통합 리포트/사용자 메타.
- `docs/prism_architecture.drawio/.png`, `docs/poc_architecture.*` — 구조도.
- `design-system/` — `/design-sync`용 스타터(토큰 + React 컴포넌트). **진행 중**.

## UI 동작 메모
- 메인 큰 제목/설명 제거됨 → 사이드바 상단에 아이콘+`리드문 · 메타 추출` 타이틀.
- 콘텐츠 그룹 = 드롭다운(서비스 그룹 8종, `/vocab`). 엑셀 탭 = xlsx/csv 업로드 → ingest 자동매핑 → 행별 일괄 추출(최대 200) → 결과 테이블 + 전체 리포트.
- 헤더 상태 배지: `Solar 연결됨`(키 있음) / `MOCK · 키 미설정`.

## 열린 작업 / 다음 단계
1. **실제 이미지로 end-to-end 검증** — `samples/`에 이미지 넣고 UI에서 추출 실행 → 리드문 품질·환각 점검.
2. **디자인시스템** — 사용자가 `design-system/`에서 `/design-sync` 실행 예정. 완료 후 토큰/컴포넌트를 `serve.py`에 반영. (사용자: "컴포넌트 단조로움, 디자인시스템 만들어 반영" → **컴포넌트 스타일 임의 변경 금지, 대기**.)
3. 엑셀 컬럼 자동매핑이 실제 파일에서 잘 되는지 확인, 안 되면 매핑 지정 UI 추가.
4. 콘텐츠 카테고리 사전화(자유생성 → 자사 사전), 검증 지표(ROUGE·Groundedness) — 1312/0021 후속.
5. 방식 B(네이티브 image_only 트랙)는 중기 과제.

## Confluence (DNM space)
- `0021. POC`(353501523) — 이 작업의 기획+구조 문서(구조도 사용자 삽입). 최신 v14.
- `134. 이미지형 콘텐츠 리드문·메타`(375653664) — 이미지형 PoC 스펙.
- `1312. 아이템 메타`(364314733) · `131`(274040089) · `132 토픽`(279904498) — 체계 정본.
- 첨부 업로드 MCP 없음 → 구조도 교체는 draw.io 매크로에 `docs/*.drawio` 붙여넣기(수동).

## 규칙 / 주의
- **문서 작성 시 하이픈 `-` 금지**(사용자 강한 선호) → 중점 `·`/괄호/문장으로. 코드 식별자는 예외.
- 사용자 WIP 보존: `d9633cd`는 사용자의 dashboard/metapool/usermeta/graphviz WIP 격리 스냅샷(squash/reword 가능).
- `docs/demo-*.html`는 생성 산출물 — 미커밋 변경 있어도 무시/재생성 가능.
- config.json의 `db_path`/`emb_cache_path`는 `~/Desktop/metacli/`(존재함). 다른 머신/클론에선 `--no-db --embed off`로 우회.
- 커밋 시 trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## 메모리
`prism-canonical-home` 메모리에 정본/통합 방침 기록됨.
