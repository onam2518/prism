# Prism 아키텍처 맵 · 리팩토링 로드맵

새 세션(사람·Claude)이 "어디를 고치면 되는지"를 빨리 찾기 위한 지도.
기능 위치를 찾느라 5,000줄 파일을 처음부터 읽지 말고 이 문서에서 시작한다.
구조가 바뀌는 PR 은 이 문서도 함께 갱신한다.

## 전체 그림

```
브라우저 (page.py 의 PAGE 단일 HTML + vendor/app.js Alpine 앱, /m 은 mobile.js)
   │  fetch(JSON) / SSE(/events)
   ▼
serve.py  ─ HTTP 계층(라우트 테이블 GET/POST · 최장 접두 우선) + 컴포지션 루트
   │         (전역 상태 _STORE/_agg/SSE · 설정/LLM 라우팅 · 각 도메인에 _SV 주입) ≈3.4k줄
   ├─ *ops.py 도메인 모듈(serve 를 _SV 로 역참조 · 아래 표): learnops(학습) adminops(인증)
   │   reviewops(검수·배정·게임화) runops(실행 파이프라인) ingestops(인입·잡)
   │   topicops(토픽) dictops(사전) dashops(대시보드·롤업·리포트) mediaops umops boardops
   │   crewops(검수 인력 운영·캐파·스케줄) evalops(런 비교·평가) weekops(주간기록) deployops(배포 게이트)
   │   reviewassist(검수 보조 에이전트 · 트랙 A 도구)
   │   metaquery(콘텐츠 조회 · 메타베이스 경유 데브 발행분 조회→검수 지정 · 2026-09-02)
   ├─ pipeline.py + prompts.py/meta_prompts.py/agents.py   LLM 추출 파이프라인
   ├─ topic.py / entdict.py / dictionaries.py / usermeta.py / mediaext.py / imagext.py
   │  modelmeta.py(모델 표시 정보 · 이름/제공자/비용 등급 · 선택 드롭다운 원천)
   │  mcpkeys.py(MCP 파트너 키 · 트랙 B 외부 MCP · 발급/해석/레이트리밋/사용 기록 ·
   │             저장은 store/supastore 의 mcp_* 계약 · 전송 /mcp 는 resolve·rate_check·log_call 만 쓴다)
   │  promptdist.py(외부 파트너용 추출 프롬프트 배포 + 결과 규칙 검증 · 조립은 meta_prompts
   │             단일 원천 · 학습 보정 제외 + 버전·지문 동봉이 계약 · 등록은 prismtools.TOOLS)
   │              도메인 모듈(비교적 잘 분리된 편 · 새 기능은 이 패턴을 따를 것)
   └─ store.py(SQLite 로컬) / supastore.py(Supabase 팀 운영)   저장 계층(동일 계약)
```

- 백엔드 선택: `PRISM_BACKEND`(sqlite|supabase) → `serve.get_store()` 가 단일 진입점.
- 배포: main 머지 → GitHub Actions(test → fly deploy) → `prism-item.fly.dev`.

## serve.py 내부 지도 (도메인 클러스터)

serve.py 는 "모듈이 되다 만" 도메인들이 함수 접두어로 뭉쳐 있다. 수정 전 해당
클러스터만 읽으면 된다 (`grep -n "^def " prism/serve.py` 로 최신 위치 확인).

| 도메인 | 대표 함수 | 관련 라우트 |
|---|---|---|
| 런타임 상태 | `get_store` `backend_mode` `_agg_cached` `broadcast` `_sse_*` `rate_limited` | /events |
| 설정·모델 | `config_status` `apply_config` `list_models` `llm_for_model` `ping_*` `sync_prompt` | /config /models /ping |
| 실행 파이프라인 → **runops.py** | `run_pipeline` `run_batch` `rerun_*` `add_contents` `store_save` | /run /run-batch /rerun* |
| 검수(1층) → **reviewops.py** | `apply_feedback` `review_queue` `raw_rows` `patch_content_meta` `content_history` `drafts_for` | /feedback /queue /raw /history /drafts /source-status |
| 검수(2층·최종) → **reviewops.py** | `final_review_queue` `set_final_verdict` `reviewer_roles` `_inject_gold_final` | /final-queue /final-verdict /reviewer-role |
| 배정 → **reviewops.py** | `distribute_assignments` `assign_log_data` | /content-assign* /assign-log |
| 검수 인력 운영(HR) → **crewops.py** | `capacity` `profiles`/`set_profile` `crew_data` `plan_distribute` `rebalance` `escalate_split` `category_reliability` `auto_tick` | /crew /crew-profile /crew-assign /crew-rebalance /crew-wave /crew-auto |
| 게임화 → **reviewops.py** | `arena_data` `mission_progress` `save_badges` `reviewer_weights` | /arena /badges |
| 학습 연동 | `learn-*` 핸들러(실체는 learnops) `apply_gold_answer` `disabled_directives` | /learn-* /golden* /apply-directive |
| 토픽 → **topicops.py** | `topics_data` `topic_studio_action` `similar_topics` `topic_drill` `topic_snapshot` | /topics /topic-studio /topic-drill |
| 사전 → **dictops.py** | `entdict_data` `entdict_action` `_enrich_*` / 구사전 `dict_data` `edit_dict` | /entdict* /dict |
| 사용자 메타 → **umops.py** | `usermeta_*` (입력 서식 `build_template_xlsx` 는 runops) | /usermeta* |
| 미디어(콘텐츠 추가 탭) → **mediaops.py** | `media_action` `media_s5ab` `media_native` `media_register` | /media-extract /media-register |
| 인입·잡 → **ingestops.py** | `ingest_run_source` `_job_*` `_ingest_scheduler` `backfill_urls` `check_source_url` · 스케줄러는 기본 비활성(PRISM_INGEST_AUTO=1 로 opt-in · 2026-09-02) | /ingest-* /backfill-urls /check-source |
| 콘텐츠 조회 → **metaquery.py** | `mq_status` `mq_search` `mq_register`(발행 메타를 초안으로 복사 인입 · 재추출 없음 · media_register 와 같은 계약) · 설정은 config.metabase_* + env PRISM_METABASE_KEY | /metaquery /metaquery-search /metaquery-register |
| 대시보드·롤업 → **dashops.py** | `dashboard_data` `drill_contents` `cost_rollup_data` `fail_rollup_data` `activity_daily_data` | /dashboard /drill /cost-rollup /fail-rollup /activity-daily |
| 게시판 → **boardops.py** | `board_data` `board_action` | /board |
| HTTP 계층 | `Handler`(게이트 `_gate_get` `_admin_gate` `_require_*` · 응답 `_send` `_send_file`) | 전 라우트 |

콘텐츠 조회의 수집 환경(bi-portal 은 사내망 전용 · 사내망 엔진이 register 를 호출) 설계는 `docs/METACOLLECT_DESIGN.md`.

### 라우트 추가 방법

- **GET**: `serve.py` 의 GET 라우트 테이블 섹션에 핸들러 1개 등록. 끝.
  ```python
  @_get_route("/my-data", admin=True)      # admin=True → 공통 403 게이트
  def _g_my_data(h, q):                    # h=Handler, q=parse_qs dict
      return {"ok": True}                  # dict = 200 JSON · None = 직접 응답한 것
  ```
  디스패치는 **최장 접두 우선**이라 등록 순서·가로채기 걱정이 없다.
  무인증 공개가 필요하면 `_PUBLIC_GET`, 팀 없이 허용이면 `_TEAMLESS_OK_GET` 에 추가.
- **POST**: 같은 패턴의 `@_post_route("/경로", gate=...)`. 핸들러는 `fn(h, body)` —
  본문 파싱(JSON/multipart)은 핸들러 몫, 예외는 디스패처가 일괄 500 처리.
  `gate`: `"admin"`(403) · `"super"`(403) · `"login"`(401) · `"team"` · `""`(내부 판단).

## 상태·컴포지션 주의점

- `serve._STORE` 전역 + `get_store()` 지연 초기화. **테스트 30여 개가 `serve._STORE = None`
  으로 리셋한다** — 상태를 다른 모듈로 옮기면 이 계약이 조용히 깨진다.
- `LO._SV = serve` / `AO._SV = serve` 역주입: learnops·adminops 가 serve 의 함수를
  런타임에 참조한다. 순환 import 를 피한 구조이므로 유지.
- 집계 캐시 `_agg_cached`(+`_agg_bump`), 인입 잡 `_INGEST_STATE`, SSE 구독자 목록도
  serve 전역 — 도메인 추출 시 이 상태들은 serve 에 남기고 함수만 옮긴다.

## 검수 보조 에이전트 모델 (설정 계약 · 두 모듈이 의존)

검수 보조가 답할 때 쓰는 모델은 **품질 판정 모델과 따로** 고른다. 판정한 모델이 그 판정을
설명까지 하면 틀린 판정도 말이 되게 꾸며 내기 때문이다.

| 무엇 | 이름 |
|---|---|
| 설정 필드 | `Config.assist_model`(config.json · `POST /config` 본문 키 `assist_model`) |
| 해석 함수 | `config.assist_model(cfg=None) -> str` (serve 재수출 · **유일한 해석기**) |
| 유효값 집합 | `config.assist_model_options()`(원천 `modelmeta.KNOWN_ROUTER_MODELS`) |
| 기본값 | `config.MODEL_DEFAULT`(= `serve._SOLAR_MODEL_DEFAULT` 와 같은 값) |
| /config 응답 | `assistModel`(해석된 값) · `assistModels`(키로 부를 수 있는 후보 · `serve._assist_candidates`) |

- `assist_model()` 은 **미설정·잘못된 값·정상값 모두**에서 곧바로 쓸 수 있는 이름을 준다.
  부르는 쪽은 분기하지 않는다: `llm_for_model(assist_model(), mock)`.
- **미설정일 때 실행 모델(`cfg.model`)을 따라가지 않는다.** 따라가면 이 설정이 막으려던
  상황(판정한 모델이 자기 판정을 설명하는 것)이 기본 동작이 된다.
- 저장 시점에도 같은 목록으로 거른다(`apply_config` 가 `error` 한 줄로 거절 · 부분 반영 없음).
  읽는 쪽 해석은 손으로 고친 파일·예전에 저장된 값을 위한 안전망으로 남는다.
- 키가 빠지면 저장된 모델이 런타임에 안 불릴 수 있다. 그 실패는 **설정 화면을 가리키는
  문구**로 알린다(예: "설정된 모델을 부를 수 없습니다 · 시스템 설정에서 다시 골라 주세요").
- 화면: 시스템 설정 `ui/16-settings.html` · 동작 `vendor/app-08-copytext.js`
  (`saveAssistModel` · `judgeModel`/`assistSameAsJudge` = 판정 모델과 같아지면 알림 · 막지 않음).

## UI 구조

- 마크업: `prism/ui/NN-*.html` 화면 섹션 조각 24개를 `page.py` 가 파일명 순으로
  이어붙여 `PAGE` 합성. **화면 수정 = 해당 조각 파일만 편집** · 새 화면 모듈은 새 조각.
  **주의 ①**: `20-ingest-policy.html` 끝이 Alpine `x-data` 루트를 닫는다 — 새 화면 조각은
  파일명이 그보다 앞서야 한다(예: `19b-`). 뒤에 두면 스코프 밖이라 `x-show` 가 평가되지
  않아 마크업은 있는데 화면이 빈 채로 보인다.
  **주의 ②**: 관리자 메뉴 `admin`(운영 관리)은 `adminTab` 으로 팀 관리/검수운영 2탭을 담는다 —
  검수운영 마크업은 `19b-crew.html`, 탭 게이트는 권한 id `crew`(앞뒤 동일).
  **주의 ③**: `x-show` 와 같은 요소에 인라인 `display:flex` 를 주지 않는다. Alpine 이 보일 때
  display 속성을 지워 flex 가 날아간다(자식이 세로로 쌓여 그래프가 뭉갬) — `.flexrow` 클래스 사용.
  **주의 ④**: 조각은 최상위로만 이어붙는다 — 기존 화면 **안쪽**에 끼워야 하면 그 자리에
  자리표 한 줄(`<div id="…" style="display:contents">`)만 두고 본문은 새 조각에서
  `<template x-teleport="#자리표">` 로 꽂는다. 충돌 잦은 조각에 큰 마크업을 밀어 넣지 않기
  위한 관례다. 화면 위에 **떠 있는** 창(플로팅 버튼·대화창·드롭다운)은 자리표 대신
  `x-teleport="body"` 를 쓴다 — 탭 컨테이너 안에 있으면 조상 스타일에 눌리고 탭을 옮길 때
  같이 숨는다(예: 검수 보조 `19e-review-assist.html` · 모델 선택 메뉴 `page.py`).
- 동작·상태: `vendor/app-NN-*.js` 프로퍼티 그룹 조각 16개 + 로더 `vendor/app.js` 가
  디스크립터 병합(게터 보존 · 조각 간 `this` 공유). 조각 → 로더 로드 순서는
  `ui/00-head.html` 의 script 태그가 원천. `vendor/mobile.js` = /m 전용(단일 파일).
- 캐시버스터: 부팅 ID(`_BOOT_ID`)를 `?v=` 로 주입(serve 하단 `_PAGE_V` 재작성).
- 정적 데모(scripts/make_demo.py)는 app 조각을 자동 글롭 인라인 — 조각 추가 시 무수정.

## 리팩토링 로드맵 (작업 효율 개선 · 단계별 독립 PR)

원칙: 한 PR 에 한 단계 · 동작 불변(기존 테스트 그대로 통과) · 파일 이동보다
"재수출 유지 + 점진 이관". 완료 시 이 문서의 체크박스를 갱신한다.

- [x] **0단계 — GET 라우트 테이블** (이 PR): if/elif 44분기 → 선언 테이블 + 최장
  접두 매칭 · 관리자 게이트/다운로드 응답 공통화.
- [x] **1단계 — POST 라우트 테이블**: do_POST 39분기를 같은 패턴으로. admin/super/
  login/team 게이트를 등록 옵션으로, try/except→500 복붙을 디스패처로 흡수.
- [x] **2단계(1차) — 도메인 추출: 토픽·사전·미디어**: `topicops.py`(477줄) ·
  `dictops.py`(302줄) · `mediaops.py`(100줄) 분리, `_SV` 주입(learnops 관례) +
  serve 재수출로 테스트·핸들러 호환. 몽키패치 계약: 테스트가 serve.topics_data ·
  serve._DICT_OVERRIDES_PATH 를 패치하므로 모듈 내부 상호 호출·상태 접근은 `_SV.` 경유.
- [x] **2단계(2차) — 도메인 추출: 대시보드·롤업·검수**: `reviewops.py`(검수 1층·2층·
  배정·게임화) · `dashops.py`(대시보드·드릴·비용/실패 롤업) 분리. 몽키패치 계약 추가:
  serve._supa·serve._inject_gold·serve.rerun_unconfirmed 도 `_SV.` 경유.
- [x] **2단계(3차) — 도메인 추출: 실행·인입·유저메타·게시판·리포트**: `runops.py` ·
  `ingestops.py`(_INGEST_STATE 는 serve 재수출과 같은 객체 공유 — 재바인딩 금지) ·
  `umops.py` · `boardops.py` · 리포트 빌더는 dashops 로. serve 잔류 = HTTP 계층 +
  컴포지션 루트(전역 상태·설정/LLM — 의도된 책임)이며 2,435줄.
- [x] **3단계 — page.py 분할** (PR #220): `PAGE` → `prism/ui/NN-*.html` 22조각,
  파일명 순 합성. 분할 전후 sha256 동일 검증 — 렌더 불변.
- [x] **4단계 — app.js 분할** (PR #221): `app-NN-*.js` 9조각 + 병합 로더
  (`getOwnPropertyDescriptors` — 게터 보존). node 동등성 검증(프로퍼티 754개 동일).
- [ ] **지속 — 새 도메인은 새 모듈**: usermeta.py·entdict.py 처럼 시작부터 별도
  파일 + serve 는 라우트 등록만. serve.py 가 다시 자라는 것을 막는 유일한 방법.
