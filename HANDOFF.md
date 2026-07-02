# HANDOFF — Prism · 팀 검수·평가 플랫폼

다음 세션이 바로 이어갈 수 있도록 현재 상태를 정리한 문서. (갱신: 2026-07-01, v0.4.4)

## 한 줄 요약
Prism 은 콘텐츠 메타(리드문·엔티티·인텐트·카테고리) 추출을 넘어 **팀이 산출물을 검수·평가하고(HITL), 그 합의를 골든셋·프롬프트 개선으로 되먹이는 평가 플랫폼**이다. supabase 운영 전용, 게이미피케이션 검수 아레나, 검수 → 골든셋 → 학습 일배치 폐루프까지 동작.

## 정본 위치
- 레포: `/Users/pete.axz-pc/Desktop/project/prism` (origin `github.com/onam2518/prism`, 사용자 소유)
- **작업은 `main` 브랜치에 직접**(과거 `feat/policy-edit` 경유 PR 방식 → 현재는 main 직커밋). `feat/image-meta-poc`는 과거 브랜치.
- 릴리즈: `gh release`, 최신 **v0.4.4**. DMG 자산 첨부.

## 실행 방법
- `cd ~/Desktop/project/prism && python3 -m prism.serve` → http://127.0.0.1:8765
- 데스크탑 앱: `desktop/app.py`(pywebview). 코드 바꾸면 **서버 재시작해야** 반영(페이지 메모리 로드).
- `--mock` = 키 있어도 강제 mock. 8765 점유 시 `lsof -ti tcp:8765 | xargs kill`.

## 운영 모드 (중요 — supabase 전용화됨)
- **로컬(sqlite 단독) 모드는 UI 상 제거**. 첫 화면 = 로그인/가입.
- 모드 스위치: `PRISM_BACKEND=supabase` + `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` 셋 다 있으면 팀(supabase) 모드(`_supa()`), 아니면 sqlite.
- **GUI 앱은 셸 env 미상속** → `desktop/app.py:_enable_supabase()`가 키파일에서 직접 로드:
  - `~/.prism_supabase_key`(service_role 키) · `~/.prism_supabase_url`(project URL). 있으면 env 세팅 후 supabase 모드로 기동.
- **보안 게이트**: supabase 모드에서 `/store` clear·`/config` POST 는 `is_admin_user(uid, team, email)` 관리자 한정(비관리자 403). 관리자 허용목록 `~/.prism_admin_emails`(예: `pete.ryu@axzcorp.com`) 또는 `PRISM_ADMIN_EMAILS`.
- **팀 생성 = 관리자 메뉴**. 일반 사용자는 팀 코드로 **참가만**, 또는 팀없음(solo).
- ⚠️ service_role 키는 과거 세션에서 유출된 적 있음 → **로테이션 권장**. 값 echo/print/표시 금지, 공개 레포에 내부 식별자 노출 금지.

## 저장소 (dual-mode store)
- `prism/store.py`(sqlite) / `prism/supastore.py`(Supabase PostgREST/urllib), `get_store()`가 모드로 스위치.
- ⚠️ **sqlite `recent()`는 `payload` 컬럼을 읽는다**(item_meta 컬럼 아님). 콘텐츠 메타를 고칠 땐 `update_item_meta`가 `item_meta`·`payload.item_meta` **둘 다** 갱신해야 함(2026-07-01 버그 수정). supabase 는 `item_meta` jsonb 단일 소스라 무관.
- `golden` 테이블(`content_hash PK, content, expected, ts`) + `upsert/register/get/count/clear_golden`.

## 검수 → 골든셋 → 학습 폐루프 (신규 핵심)
- 서버: `prism/serve.py`
  - `patch_content_meta(content_hash, patch, team)` — 검수자 구조화 교정(빈 카테고리 채우기 등). 라우트 `/patch-meta`.
  - `build_golden_from_reviews(team)` — 정확 다수결 + 카테고리 채워짐 → **골든 확정**, 카테고리 공백 → **need_category**.
  - `compare_models_on_golden(models, team)` — 모델별 grade_accuracy/reason_jaccard/cost, best.
  - `learning_batch(team, models)` — meta_compile + build_golden + eval + compare, `_LAST_LEARN_REPORT` 저장. 라우트 `/learn-batch`·`/learn-report`·`/compare-models`.
  - `start_learning_scheduler(hour=4)` — 매일 04:00 스레드(실시간 아님: 합의·진동 방지).
- 요소 기반 교정: `FIX_ELEMENTS`·`elemStage`(요소→analyze/judge/review). 검수자가 어느 요소가 틀렸는지 고르면 해당 단계 프롬프트 보정(`PR.LEARNED[stage]` → `prompts._learned(stage)`)에 반영.
- **프롬프트 반영은 일배치가 소유**. `apply_feedback`·`_reap_async`에서 live `sync_learned()` 제거(캡처만).
- 프론트: `runLearnBatch`·`loadLearnReport`·`categoryOptions`·`fillCategory`(POST /patch-meta). '학습 일배치' 리포트 패널(타일 + 모델 비교표), 상세 내 빈 카테고리 gap-fill picker.
- 합의 기준: 정확≥1 & 정확≥수정필요. 필수 채움 = 카테고리만. `content_hash` = sha1(displayServiceName+title+subtitle+body)[:16].

## 메타 체계 (코드가 이 기준으로 정렬)
`ItemMeta` 키: `summary`(리드문) · `entities` · `intent`(속성 분류) · `content_category` · `topic`/`topic_categories`(3차, 기본 빈값). 메타풀→토픽 전환(`metapool.py→topic.py`, `build_topics`).

## 코드 구조 (핵심 파일)
- `prism/serve.py`(~5500줄) — 앱 전체(stdlib http.server). 모든 UI 인라인(Alpine.js + Tailwind CDN). 온보딩·아레나·대시보드·검수·프롬프트 스튜디오·학습 배치.
- `prism/store.py`·`supastore.py` — dual-mode 저장소 + golden.
- `prism/pipeline.py·agents.py·prompts.py·verify.py·schema.py` — 추출 파이프라인. `abtest.py` — 평가 지표(grade_accuracy·reason_jaccard·empty_rate·cost).
- `prism/imagext.py` — 이미지 인제스트(방식 A, 코어 무수정).
- `desktop/app.py`·`Prism.spec`(v0.4.4)·`make_dmg.sh` — 패키징. 빌드 venv `/tmp/prism-pkg/bin/python`.
- `scripts/make_demo.py` — GitHub Pages 데모(`docs/demo.html`) 재생성(fetch 스텁·CDN 폰트·vendor 복사).
- `design-system/` — Anchor 디자인 시스템(`--ds-*` 토큰, GmarketSans/Pretendard 이중폰트).

## UI·디자인 메모
- 홈 = **검수 아레나**(Flow·오늘의 미션·배지 12종·주간 리그·선수카드). 히어로 지표 = **검수 진척율**(개인·팀 평균).
- 폰트: display=**GmarketSans**(게임형) / body=**Pretendard**. 다크모드는 Tailwind 색을 `var(--ds-*)`로 토큰화.
- 버튼: 맨 텍스트 금지(박스/아이콘). `.ds-btn--primary/--secondary/--ghost`는 앱단 정의(재벤더링 DS 는 `--solid.--c-*`만).
- 검수 완료 표기 전역(목록·상세), 추가 수정 버튼·수정 일시 로그, 판정 색상(정확=초록/수정필요=빨강).

## 규칙 / 주의
- **제품 카피에 em-dash `—` 금지**(·/괄호/문장). 확인: `grep -c "—" prism/serve.py` == 0. 코드 식별자·커밋 메시지는 예외.
- `x-show`(display:none)는 `.space-y-* > :not([hidden]) ~` 마진에 잡혀 팬텀 마진 유발 → 조건부 첫 자식은 `x-if`.
- 커밋 trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- 릴리즈 노트·공개 레포에 내부(DNM/Confluence) 식별자·정책 노출 금지.

## 다음 단계
1. **실 supabase 팀 데이터**로 골든셋·일배치 라이브 검증(현재 sqlite E2E 만).
2. **다중 모델 실호출** 연결(`compare_models_on_golden` 지금은 로직·지표 골격), 평가 결과 수신 UI 정합.
3. 빈 카테고리 gap-fill 이 실 사전(iabTier1/2) 커버리지에서 충분한지 점검, 부족 시 사전 확장.
4. service_role 키 로테이션.

## 메모리
`prism-goal-evaluation-platform`·`prism-harness-architecture`·`prism-team-hitl`·`prism-supabase`·`prism-gamification`·`prism-anchor-design`·`prism-deploy-release-workflow` 참조.
