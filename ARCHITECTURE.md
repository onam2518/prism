# Prism 아키텍처 맵 · 리팩토링 로드맵

새 세션(사람·Claude)이 "어디를 고치면 되는지"를 빨리 찾기 위한 지도.
기능 위치를 찾느라 5,000줄 파일을 처음부터 읽지 말고 이 문서에서 시작한다.
구조가 바뀌는 PR 은 이 문서도 함께 갱신한다.

## 전체 그림

```
브라우저 (page.py 의 PAGE 단일 HTML + vendor/app.js Alpine 앱, /m 은 mobile.js)
   │  fetch(JSON) / SSE(/events)
   ▼
serve.py  ─ HTTP 서버(stdlib http.server) + 대부분의 비즈니스 로직  ← 최대 허브(≈5.4k줄)
   │         GET: 라우트 테이블(_GET_ROUTES · 최장 접두 우선)   POST: do_POST if/elif
   ├─ learnops.py   학습 일배치·골든·소요서·핸드오프 (serve 를 LO._SV 로 역참조)
   ├─ adminops.py   인증(JWT)·관리자 판정 (serve 를 AO._SV 로 역참조)
   ├─ pipeline.py + prompts.py/meta_prompts.py/agents.py   LLM 추출 파이프라인
   ├─ topic.py / entdict.py / dictionaries.py / usermeta.py / mediaext.py / imagext.py
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
| 실행 파이프라인 | `run_pipeline` `run_batch` `rerun_*` `add_contents` `store_save` | /run /run-batch /rerun* |
| 검수(1층) | `apply_feedback` `review_queue` `raw_rows` `patch_content_meta` `content_history` `drafts_for` | /feedback /queue /raw /history /drafts |
| 검수(2층·최종) | `final_review_queue` `set_final_verdict` `reviewer_roles` `_inject_gold_final` | /final-queue /final-verdict /reviewer-role |
| 배정 | `distribute_assignments` `assign_log_data` | /content-assign* /assign-log |
| 게임화 | `arena_data` `mission_progress` `save_badges` `reviewer_weights` | /arena /badges |
| 학습 연동 | `learn-*` 핸들러(실체는 learnops) `apply_gold_answer` `disabled_directives` | /learn-* /golden* /apply-directive |
| 토픽 → **topicops.py** | `topics_data` `topic_studio_action` `similar_topics` `topic_drill` `topic_snapshot` | /topics /topic-studio /topic-drill |
| 사전 → **dictops.py** | `entdict_data` `entdict_action` `_enrich_*` / 구사전 `dict_data` `edit_dict` | /entdict* /dict |
| 사용자 메타 | `usermeta_*` `build_template_xlsx` | /usermeta* |
| 미디어 → **mediaops.py** | `media_action` `media_s5ab` `media_native` | /media-extract |
| 인입·잡 | `ingest_run_source` `_job_*` `_ingest_scheduler` `backfill_urls` | /ingest-* /backfill-urls |
| 대시보드·롤업 | `dashboard_data` `drill_contents` `cost_rollup_data` `fail_rollup_data` | /dashboard /drill /cost-rollup /fail-rollup |
| 게시판 | `board_data` `board_action` | /board |
| HTTP 계층 | `Handler`(게이트 `_gate_get` `_admin_gate` `_require_*` · 응답 `_send` `_send_file`) | 전 라우트 |

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

- `serve._STORE` 전역 + `get_store()` 지연 초기화. **테스트 15개가 `serve._STORE = None`
  으로 리셋한다** — 상태를 다른 모듈로 옮기면 이 계약이 조용히 깨진다.
- `LO._SV = serve` / `AO._SV = serve` 역주입: learnops·adminops 가 serve 의 함수를
  런타임에 참조한다. 순환 import 를 피한 구조이므로 유지.
- 집계 캐시 `_agg_cached`(+`_agg_bump`), 인입 잡 `_INGEST_STATE`, SSE 구독자 목록도
  serve 전역 — 도메인 추출 시 이 상태들은 serve 에 남기고 함수만 옮긴다.

## UI 구조

- `page.py` = `PAGE` 단일 HTML 문자열(마크업·Alpine 템플릿 전부). `vendor/app.js` =
  Alpine 데이터·메서드(데스크톱), `vendor/mobile.js` = /m 전용.
- 캐시버스터: 부팅 ID(`_BOOT_ID`)를 `?v=` 로 주입(serve 하단 `_PAGE_V` 재작성).
- 화면 수정 시: 마크업은 page.py, 동작·상태는 app.js — 둘 다 허브 파일이라
  다른 세션 미커밋 변경 확인 후 작업(CLAUDE.md 규칙 7).

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
- [ ] **2단계(2차) — 도메인 추출: 대시보드·롤업·검수**: `dashboard_data`/`drill_contents`,
  `cost/fail_rollup`, 검수(1층·2층) 클러스터. 검수는 상태 의존이 가장 커서 마지막.
- [ ] **3단계 — page.py 분할**: `PAGE` 를 화면 섹션별 파일(`prism/ui/*.html`)로 쪼개
  import 시 이어붙이기. 분할 직후 커밋에서 기존 PAGE 와 바이트 동일함을 테스트로
  증명 → 이후 섹션 파일이 세션 간 충돌 단위가 된다.
- [ ] **4단계 — app.js 분할**: 화면(탭)별 파일로 나누고 `Object.assign` 믹스인으로
  Alpine 데이터 합성. `<script>` 로드 순서는 PAGE 에서 고정.
- [ ] **지속 — 새 도메인은 새 모듈**: usermeta.py·entdict.py 처럼 시작부터 별도
  파일 + serve 는 라우트 등록만. serve.py 가 다시 자라는 것을 막는 유일한 방법.
