# HANDOFF — Prism · 팀 검수·평가 플랫폼

다음 세션이 바로 이어갈 수 있도록 현재 상태를 정리한 문서. (갱신: 2026-07-02, v0.4.4+)

## 한 줄 요약
Prism 은 콘텐츠 메타(리드문·엔티티·인텐트·카테고리) 추출을 넘어 **팀이 산출물을 검수·평가하고(HITL), 그 합의를 골든셋·프롬프트 개선과 특화 LLM 학습데이터로 되먹이는 평가 플랫폼**이다. supabase 운영 전용, 품질 가중 게이미피케이션(골드 문항·미션), 검수 → 골든셋 → 학습 일배치 폐루프 + 학습데이터 추출(SFT/DPO/rationale)까지 동작.

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

## 품질 가중 게임화 + 학습데이터 추출 (2026-07-02 신규 · 논문 근거는 LEARNING_DESIGN.md)
- **골드 문항(G-1)**: 골든셋에서 (검수자,일자) 시드로 결정적 블라인드 출제(`_inject_gold`).
  hash 홀수 = 등급 뒤집기 변형(정답 bad). 응답은 `gold_checks` 테이블로 분리(feedback 무오염).
  hash 형식 `gold:<ok|bad>:<content_hash>`, `/feedback` 이 `apply_gold_answer` 로 분기.
- **품질 가중 점수(G-2)**: `(검수10+교정25+구조화교정5+합의일치5+골드10) × (0.5+0.5×골드정확도, 응답5건↑) + 미션보너스`.
  sqlite·supastore `arena_stats` 동일 산식. 리더보드에 gold_acc·quality_mult·consensus_matches·split_reviews·agree_rate.
- **신뢰도 가중 골든 합의(G-4)**: `reviewer_weights()`(골드 정확도 기반) → `build_golden_from_reviews` 가중 다수결.
  골드 데이터 없으면 전원 1.0 = 기존 다수결과 동일(하위호환).
- **판정형 미션(G-5)**: MISSIONS 3종(오늘 검수5·골드 정답1·불일치 재검토1). `/arena?reviewer=` 로 진행도,
  달성 시 `events` 테이블에 (reviewer,kind,day) 1회 기록 → 중복 보상 방지. 검수 큐 = split 재검토 우선 → 저확신 순.
- **레이트리밋**: `/feedback` 간격 0.8s·분당 40(초과 429).
- **학습데이터 추출(관리자)**: `/learn-data`(클래스 커버리지·Krippendorff α·검수자 신뢰도(합의 일치율+골드+Dawid-Skene EM)·
  라벨 오류 후보(_LAST_EVAL_DETAIL)·소요 대비표) + `/learn-export?kind=sft|dpo|rationale`(JSONL).
  `prism/quality.py` = alpha·DS EM·이항 95% CI. UI = 학습 일배치 아래 '학습 데이터 현황' 패널.
- **신규 테이블**: `patch_log`(교정 전/후 append=DPO 원천) · `gold_checks` · `events`. feedback 에 `element` 컬럼.
  supabase DDL 적용됨(`prism_learning_loop_tables`), 신규 3테이블은 service_role 전용(RLS 정책 없음).
- ⚠️ patch 는 이제 `update_item_meta` 전에 `get_item_meta` 로 before 를 떠서 `log_patch` 에 남긴다(선호쌍 rejected).

## 골든셋 생성 체계 (2026-07-02 후반 개편)
- **누적(upsert) 방식**: `build_golden_from_reviews` 가 전체 교체 대신 (team, content_hash) upsert.
  관리자 등록분(source=manual)·과거 확정분 보존, 합의가 뒤집힌 검수 유래(review) 골든만 강등(제거).
  supabase `prism_golden` 에 content_hash·source 컬럼 추가(`prism_golden_provenance`). ⚠️ 구 register_golden(replace)로 돌아가면 안 됨.
- **확정 요건**: 정확 >= `Config.golden_min_good`(기본 1) + 골드 정확도 가중 다수. 신규 확정 기여 검수자에게
  events `golden:<hash>` 1회 +10(리더보드 golden_contribs · 배지 '골든 기여 10').
- **관리자**: 팀 관리 골든 패널 = 브라우저(목록·출처·오류 의심 플래그·개별 제거) + .jsonl 업로드(병합 기본,
  등급 G|R·카테고리 사전 스냅 검증, skipped 집계). 라우트 /golden(merge)·/golden-list·/golden-remove.
- **사용자**: '검수 및 평가 → 테스트' 탭 = **골든셋 생성**(기본 탭, 구 실시간 콘텐츠 평가: 생성 현황 타일 +
  분류 필요 목록 + 일배치) | **골든셋 평가**(현행 버전 정합성 %·95% CI·버킷 + 모델별 비교 실행 UI /compare-models).
  분류 채우기 미션(fill1) 추가, /golden-status(팀원 공개) 라우트.
- **콘텐츠 인입 = 관리자 전용**: 수동 추출(run)·실행 큐·자동 인입·인입 정책 메뉴를 관리자 그룹으로 이동,
  서버도 /run·/run-batch·/ingest-run 을 supabase 모드에서 관리자 게이트(403). 로컬(sqlite)은 관리자 취급.

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
1. **실 팀 운영 개시**: supabase 는 아직 팀 데이터 0건(2026-07-02 확인). 실사용에서 골드 문항 노출 비율(현재
   큐의 ~10%·최소 1)·미션 난이도·품질 배율 체감을 모니터링해 튜닝.
2. **DPO 선호쌍 축적**: 상세 화면 교정이 patch_log 로 쌓인다. 등급/리드문 등 카테고리 외 요소의
   구조화 교정 UI 확대(현재 구조화 교정은 카테고리 채우기 중심).
3. Dawid-Skene EM 을 합의 가중치에 직접 반영할지 검토(현재 통계 표시용, 가중치는 골드 정확도 근사).
4. `/learn-report` 가 과거 POST 전용이던 문제는 GET 라우트 추가로 해소됨. 데스크탑 재빌드 시 버전 범프.
5. service_role 키 로테이션(미처리, 사용자 지시로 보류).

## 메모리
`prism-goal-evaluation-platform`·`prism-harness-architecture`·`prism-team-hitl`·`prism-supabase`·`prism-gamification`·`prism-anchor-design`·`prism-deploy-release-workflow` 참조.
