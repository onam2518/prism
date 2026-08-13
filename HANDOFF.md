# HANDOFF · Prism · 팀 검수·평가 플랫폼

다음 세션이 바로 이어갈 수 있도록 현재 상태를 정리한 문서. (갱신: 2026-07-30 · 세션 공통 규칙은 `CLAUDE.md`가 우선)

> **지금 상태**(2026-07-30): 배포 대기 없음 · **모델 실행 정상**(타임리 라우터를 운영
> 주소 `api.timelyrouter.ai` 로 이전 완료 · 실호출 200 확인). 다만 7/29 에 402 가
> 832건 났고 **잔액인지 프로젝트 지출 한도인지는 콘솔에서 확인해야 한다**(API 없음).
> 배치 1회가 약 $1.3 을 쓴다. 상세는 데일리로그 07-29·07-30.

## 한 줄 요약
Prism 은 콘텐츠 메타(리드문·엔티티·인텐트·카테고리) 추출을 넘어 **팀이 산출물을 검수·평가하고(HITL), 그 합의를 골든셋·프롬프트 개선과 특화 LLM 학습데이터로 되먹이는 평가 플랫폼**이다. supabase 운영 전용, 품질 가중 게이미피케이션(골드 문항·미션), 검수 → 골든셋 → 학습 일배치 폐루프 + 학습데이터 추출(SFT/DPO/rationale)까지 동작.

## 정본 위치
- 정본 = origin `github.com/onam2518/prism` main (사용자 소유 · private). 작업·배포 머신(2026-07-07 현재):
  `/Users/pete.axz-pc/orca/prism` (키 파일 4종·flyctl 보유 · 구 tony 머신 표기는 07-06 시점 기록).
- **main 직접 커밋·push 금지** · 기능 브랜치(`feat/…`) → PR → 머지 (2026-07-07 `CLAUDE.md` 규칙 ·
  멀티 세션 동시작업 안전. 커밋은 내 파일만 명시 스테이징 · 배포는 클린 워크트리 스냅샷에서만).
- 릴리즈: `gh release`, 최신 **v0.5.17**. **데스크탑(DMG) 배포는 2026-07-14 제거(웹 전용)** — 팀원은 브라우저 접속, QA는 `scripts/seed_qa.py` + `--mock` 서버(QA_CHECKLIST.md).

## 실행 방법
- **운영(팀원 포함)**: https://prism-item.fly.dev 브라우저 접속 (Fly 상시 서버 · supabase 모드).
- **모바일 검수(폰)**: https://prism-item.fly.dev/m — 검수만 덜어낸 카드 UI(2026-07-09 신설 ·
  같은 계정 · 목록 → 탭 → 판정/교정 · 관리자 기능 없음). 마크업 `prism/page_mobile.py` ·
  로직 `prism/vendor/mobile.js`(+mobile.css) · 서버 로직 공유(기존 API 계약만 사용).
- 로컬 개발: `PRISM_DB=$(mktemp -d)/t.db python3 -m prism.serve --mock --port <임시포트>` (운영 8765 회피 · DB 격리 · CLAUDE.md 규칙 6). 코드 바꾸면 **서버 재시작해야** 반영(페이지 메모리 로드).
- 배포: main 최신화 → `git worktree add --detach <경로> origin/main` → 그 안에서 `fly deploy` → `curl https://prism-item.fly.dev/config` 검증(backend supabase · configured true).

## 운영 모드 (중요 · supabase 전용화됨)
- **로컬(sqlite 단독) 모드는 UI 상 제거**. 첫 화면 = 로그인/가입.
- 모드 스위치: `PRISM_BACKEND=supabase` + `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` 셋 다 있으면 팀(supabase) 모드(`_supa()`), 아니면 sqlite.
- 로컬 supabase 검증 시 키파일 참조: `~/.prism_supabase_key`(service_role 키) · `~/.prism_supabase_url`(project URL) — env 로 주입해 기동.
- **보안 게이트**: supabase 모드에서 `/store` clear·`/config` POST 는 `is_admin_user(uid, team, email)` 관리자 한정(비관리자 403). 관리자 허용목록 `~/.prism_admin_emails`(예: `pete.ryu@axzcorp.com`) 또는 `PRISM_ADMIN_EMAILS`.
- **팀 생성 = 관리자 메뉴**. 일반 사용자는 팀 코드로 **참가만**, 또는 팀없음(solo).
- ⚠️ service_role 키는 과거 세션에서 유출된 적 있음 → **로테이션 권장**. 값 echo/print/표시 금지, 공개 레포에 내부 식별자 노출 금지.

## 저장소 (dual-mode store)
- `prism/store.py`(sqlite) / `prism/supastore.py`(Supabase PostgREST/urllib), `get_store()`가 모드로 스위치.
- ⚠️ **sqlite `recent()`는 `payload` 컬럼을 읽는다**(item_meta 컬럼 아님). 콘텐츠 메타를 고칠 땐 `update_item_meta`가 `item_meta`·`payload.item_meta` **둘 다** 갱신해야 함(2026-07-01 버그 수정). supabase 는 `item_meta` jsonb 단일 소스라 무관.
- `golden` 테이블(`content_hash PK, content, expected, ts`) + `upsert/register/get/count/clear_golden`.

## 검수 → 골든셋 → 학습 폐루프 (신규 핵심)
- 서버: `prism/serve.py`
  - `patch_content_meta(content_hash, patch, team)` · 검수자 구조화 교정(빈 카테고리 채우기 등). 라우트 `/patch-meta`.
  - `build_golden_from_reviews(team)` · 정확 다수결 + 카테고리 채워짐 → **골든 확정**, 카테고리 공백 → **need_category**.
  - `compare_models_on_golden(models, team)` · 모델별 grade_accuracy/reason_jaccard/cost, best.
  - `learning_batch(team, models)` · meta_compile + build_golden + eval + compare, `_LAST_LEARN_REPORT` 저장. 라우트 `/learn-batch`·`/learn-report`·`/compare-models`.
  - `start_learning_scheduler` · 검수 목표(퀘스트) 스레드(실시간 아님: 합의·진동 방지). 관리자가 '검수 목표/퀘스트 생성' 카드에서 지정한 일시(Config learn_next_at 'YYYY-MM-DDTHH:MM')에 학습 반영 1회 실행 후 목표 소진(빈 값으로 저장) · 다음 목표는 관리자가 재생성. 목표 미설정 시 자동 반영 없음(⚡ 즉시 반영만). 시한은 홈 히어로·사이드바 팀 퀘스트(D-day)·/arena·/learn-report 와 단일 원천.
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
- **콘텐츠 인입 = 관리자 전용**: 서버 /run·/run-batch·/ingest-run 을 supabase 모드에서 관리자 게이트(403).
  로컬(sqlite)은 관리자 취급.
- **콘텐츠 관리 단일 메뉴**: 수동 추출·자동 인입·실행 큐·인입 정책을 mod `content` 하나로 통합(탭 `contentTab`,
  홈 탭=실행 큐, 자동/수동 필터 칩). 구 mod id(run/queue/auto/intake)는 selectMod 에서 매핑(하위호환).
  팀 관리의 '콘텐츠 인입(검토용)' 패널은 자동 인입 탭 '일회성 인입'으로 이동.
- **피드백 오케스트레이터**: 수정 필요 시 요소 **다중 선택**(fixelem 칩, `elements` 배열 → feedback.element
  콤마 저장). 비동기 후처리(`_reap_async`)가 `FL.route_feedback`(LLM)으로 교정 원문을 요소·단계별 개선
  지시로 재분류 → `feedback_routes` 테이블(append, supabase `prism_feedback_routes`) → `learned_by_stage` 가
  라우팅 지시 우선으로 병합(일배치 meta_compile 로 유입). mock/실패 시 선택 요소 폴백(무손실).
- **IA 최종(2026-07-02 심야 · 멤버 메뉴 2개)**: `테스트셋 생성`(mod create) | `평가`(mod evaluate).
  - 테스트셋 생성 탭 4: 현황(golden: 정답 누적·분류필요·학습 반영·학습 데이터[관리자]) | 검수 대기(queue) |
    콘텐츠 검수(review: **접근 방식 토글** reviewView = 결과 보며 수정(edit, 집계+분포드릴+fbrow) /
    원본 목록(raw, `/raw` JSON 뷰어)) | 분석(insight: 품질·법령/토픽/사용자).
  - 평가 = 단일 페이지: 정답 일치율 평가(%·신뢰구간·유형별) + 모델별 비교. 구 mod id(dash/eval/review/
    quality/user)는 selectMod 매핑으로 하위호환. 현황 대시보드 메뉴는 제거(분석 탭으로 흡수).
  - 중복 제거: 학습 반영(구 일배치) 리포트는 요약 한 줄(타일·비교표 삭제, 현황·평가 탭이 소유),
    일배치는 모델 비교 미실행(평가 탭 온디맨드).
  - **용어 원칙**: 멤버 화면은 쉬운 말(정답셋·일치율·신뢰구간·유해 놓침·원본 목록·학습 반영),
    기술 용어(정합성·CI·Dawid-Skene 등)는 관리자 패널·백로그 문서에만. 태그 배지(등급·카테고리·사유·
    인텐트·YELLOW)는 전 페이지 `termDef()` 호버 정의. 스탯 클러스터는 숫자 line-height 1 + 라벨
    11px/margin 6px 통일(goldgrid align-items:flex-end)로 기준선 정렬.
  - 데모(make_demo)는 정답 현황·학습 리포트·모델 비교·학습 데이터·검수 대기(골드 문항)·실행 큐·원본
    목록 예시(EX 스텁)까지 표시.
  - **정답셋 관리(관리자 메뉴, mod testset · 탭 3)**: 현황·학습 반영(테스트셋 현황 타일 + 분류 필요 +
    학습 반영 '지금 실행') | 정답셋 목록(업로드·병합·제거) | 학습 데이터. 멤버 화면에서 현황·실행 제거,
    안내 문구만 잔류.
  - **콘텐츠 검수(멤버, mod create · 탭 3)**: 검수 대기 | 결과 목록(구 '결과 보며 수정') | 원본 목록.
    처리 이력은 콘텐츠 관리 · 수동 추출 하단으로 이동.
  - **실험실(관리자 메뉴, mod lab · 탭 3)**: 사용자 | 스펙트럼 | MCP 키 = 지금 테스트하지 않는 탐구 요소 보관.
    (2026-08-13 정리: 법령 탭 제거 · 토글은 시스템 설정으로 · 미디어는 콘텐츠 추가 탭으로 승격 · 콘텐츠 에이전트 폐기)
    품질 판정은 검수·평가 핵심 흐름이 담당(실험실 아님).
  - **모델 출처(provenance)**: Trace.model 에 초안 생성 모델 기록 → contents.model(supabase DDL
    `prism_model_provenance`)·payload.trace 로 영속. 결과 목록·검수 대기·원본 목록·상세에 모델 배지,
    피드백 payload 로 서버 전달 → `feedback_routes.model` 귀속(라우팅 프롬프트에 [초안 생성 모델] 주입).
    모델별 프롬프트(model_prompts·stage_models)는 기존 존재 → meta_compile 의 모델별 그룹 컴파일은 후속.
  - 평가 결과는 카드화(tiles + tile--hero, 유형별 막대도 tile 박스 안) · 산개 방지.
  - **도구 취지(정본)**: 관리자 주입 → 사용자 검수 → 골든셋 → **파인튜닝 스펙·소요서 근거 산출**.
    소요서 = `/learn-spec`(.md 자동 생성, 학습 데이터 탭 버튼): 실데이터 수치 + 기준치 + 권장 스펙 + 논문 출처.
  - **모델 재실행**: 콘텐츠 관리 · 수동 추출의 '다른 모델로 재실행' 패널(/rerun, 관리자). 같은 콘텐츠를
    지정 모델로 초안 재생성 · 이전 초안 patch_log(rerun:구→신) 보존 · save_many 무조건 upsert.
    run_pipeline 에 model 파라미터(llm_for_model 라우팅). (구 ⚠️ 비-YELLOW 미갱신 엣지는 2026-07-03
    구조 감사에서 해소: sync_contents(include_all=True) upsert · 계약 테스트 test_save_many_include_all_nonyellow.)
  - **검수 대상 콘텐츠(구 원본 목록 · 콘텐츠 검수 첫 탭)**: 상단 **모델 칩 → 버전 선택 → 목록**
    구조(모델별 정답셋 전제 명기 배너). 검수 대기(YELLOW)·불일치 배지로 흡수(큐 탭 삭제, 골드 문항은
    /raw 에서 삽입). 검수열 = '검수하기'(상세 열기) / 완료 시 ✓일시. hash 키는 `_row_key` 정합.
  - **초안 버전**: 학습 반영 실행마다 events kind='learn_batch' 기록 → `batch_seq()+1` 을
    trace.version 으로 스탬프(run_pipeline·재실행). supabase contents.version 컬럼(`prism_draft_version`).
  - **결과 비교(콘텐츠 검수 둘째 탭)**: 집계·분포 시각화 + `/drafts`(현재 초안 + 재실행 이전 초안)
    양분할 비교 · 다른 필드 하이라이트. 검수·교정 fbrow 목록은 제거(표+상세가 담당).
  - **선택 컨트롤 디자인 정책(2026-07-02 최종)**: 칩(`.srcfilter__chip`, 30px) = 다중 토글 필터 ·
    `.selctl`(태그+셀렉트 박스) = 단일 지정 선택 · `.selctl--a/--b`(A 파랑/B 주황) = A/B 비교 슬롯.
    적용처: 모델·버전별 결과 현황(A/B 슬롯, 모델→버전 종속 선택), 검수 대상 콘텐츠(모델 selctl),
    비교 팝업(상단 A/B **상속** · 자체 선택 없음 · 해당 초안 없으면 '초안 없음' 배지 + 최근접 폴백).
  - **콘텐츠 관리 = 원페이지 STEP 구성(2026-07-02)**: 상위 탭 제거. STEP 1 콘텐츠 추가(수동/자동
    카드 칩 전환 + **추가 용도** selctl: 검수용/평가용, 추가된 콘텐츠 목록에서 건별 용도 전환 /purpose)
    → STEP 2 모델 실행(단건·일괄 /rerun-all · v=batch_seq+1 · auto_rerun_after_batch 기본 꺼짐)
    → STEP 3 실행 큐. `.stepline` 컴포넌트(STEP 배지 GmarketSans). 수집(인입) 정책은 사전·정책 통합.
  - **콘텐츠 용도(purpose)**: review(검수용, 기본)=검수·골든 축적 / eval(평가용)=검수 목록(/raw)에서
    제외되는 평가 전용 홀드아웃(학습 오염 방지). sqlite `content_purpose` 테이블(결과 upsert 와 분리해
    재실행에도 보존) · supabase `prism_contents.purpose`(DDL prism_content_purpose, 기본 'review').
    수동 추가·엑셀 배치 모두 purpose 필드 전달(run_pipeline/run_batch).
  - **평가 기준(evaluate 상단 패널)**: 기준 모델(selctl, 비우면 현재 설정 모델 · llm_for_model 라우팅)
    + 프롬프트 버전 표기(v=batch_seq+1, 평가는 항상 현재 버전) + 대상 콘텐츠 칩(전체 정답셋/평가용만
    scope=all|eval, _scope_golden 필터). /eval-golden·/compare-models 에 model·scope 전달, 결과에
    basis{model,version,scope} 명시.
  - **평가 페이지(2026-07-02)**: 평가 기준(기준 모델·버전·대상 콘텐츠) → 평가 실행(요약 타일 + 유형별
    태그 막대) → **평가 상세 · 건별 판정**(불일치 목록, 채택=모델 결과 채택(정답 교정 후보)/탈락=모델
    오답 확정, 1인 1표 upsert /eval-judge, 최초 판정 +5pt, 합의(golden_min_good) 시 정답셋 목록
    '교정 필요' 배지 · 골든 자동 교체는 안 함) → 모델별 비교(A/B 슬롯 · abbar · ▲ · ★ best).
    저장: sqlite eval_checks · supabase prism_eval_checks.
  - **권한 2단계(2026-07-02)**: 운영 관리자(허용목록 ~/.prism_admin_emails · is_sys_admin_user) =
    전체 관리자 메뉴. 팀 관리자(생성자·위임) = '팀 관리'만 추가. 허용목록 미설정 시 기존 로직 폴백.
    메뉴 게이팅 navVisible(cond: admin|sysadmin) · adminData.isSysAdmin.
  - **시스템 설정 메뉴(mod system · 운영 관리자)**: 데이터 관리(상단: 피드백/콘텐츠/정답셋 삭제 +
    로컬 적재 초기화 + **팀 삭제**(2중 확인, supabase delete_team)) + 하단 API 키·모델 카드 인라인
    (설정 모달 폐지 · settingsOpen 제거, 딥링크 ?settings·topbar 기어 → selectMod('system')).
    팀 관리 메뉴는 멤버·초대코드만 유지.
  - **게임 정책(v0.5.0)**: 레벨 커브 = 구간 요구 pt 100+80×(레벨-1) 등차 증가, Lv.50 만렙 98,980pt
    ≈ 검수 9,898건(개인 1만 건 완주 설계, store.level_of 단일 원천 · 클라 lvlFloor/lvlNeed 동기화).
    배지 22종 래더(볼륨 1~10k 완주 · 스트릭 3/7/30/100일 · 기여 1/50/500 · 품질 5종 · 지위 Lv.10/25/50).
  - **검증 체계(v0.5.0)**: tests 46종 = 유닛 40 + HTTP 스모크 6(tests/test_http_smoke.py: mock 서버
    실부팅 후 화면 전 버튼 엔드포인트 실호출 · DEFAULT_CONFIG_PATH 격리로 로컬 config 오염 방지).
    termDef 는 '값 (건수)' 접미 정규화 후 사전 매칭(집계 칩 호버 정의).
  - **기준 프롬프트(2026-07-02 · imeta@v9)**: 원천 = `~/Desktop/contextual-meta-extraction.md` v2.1
    (모델별 메타 추출 쿡북, DNM 계약). `prism/meta_prompts.py` 가 코드 원천: 코어 규칙 C1~C4 원문 +
    골드 예시 + 모델 계열 래퍼(gpt=<output_contract>/gemini=스키마 재명시/claude=<background> 담백/
    solar=CRITICAL+자가 검증). STAGE_DIRECTIVE_DEFAULT extract·analyze 기본값 = C1+C2 / C3+C4
    (스튜디오 '기본값 복원' = 문서 기준). item_system(content, model) 이 실행 모델 계열로 래핑,
    사전 주입 = 인텐트(범용①·②+서비스 분기) + IAB Tier1/Tier2 전체. 확정 반영: '노동·사회 이슈'
    표기 교체, 범용② 형식·전달 8종 사전 추가(주입·검증 공용). 미적용 갭: 분리형 4호출(현 1콜 통합),
    호출별 모델 티어, Structured Outputs/responseSchema/cache_control API 강제, IAB '구분 기준'
    텍스트(사전에 경로만 존재), 12 서비스 분기 세분화(공개 레포 내부명 노출 금지로 일반명 유지).
  - **분리형 4호출 계약 적용(2026-07-02 · 기준 원문 1312/인텐트·카테고리 사전 동기)**: agents._run_item_calls
    = ① 리드문 → ② 엔티티(1~3 강제) → ③ 인텐트(사전: 범용①·②+서비스 분기 · 표기 정확 일치만) →
    ④ 카테고리(사전: Tier1 설명+Tier2 전체+**구분 기준** 주입 · normalize 스냅). 단락 차단(① 빈 문자열 →
    후속 생략), 전량 드롭 시 1회 재요청. Config.meta_four_calls(기본 True · 해제=통합 1콜 폴백),
    meta_call_models(호출별 모델 티어 · llm_for_model 라우팅, AG.LLM_FOR_CALL 주입). mock 태그
    item_summary/entities/intent/category. response_format json_object 는 llm._call 에 기존 적용.
  - **사전 개편(원문 동기)**: displayServiceName 정의값 10개+구명칭(카카오TV·카카오비디오→TV) 분기
    → 서비스 카테고리 8종(뉴스/연예/스포츠/콘텐츠뷰/음악/커뮤니티/티스토리/TV) · 분류값 전면 교체
    (뉴스 7종 통합 등) + INTENT_VALUE_DEFS(설명) · IAB Tier2 신규 Military★/Lifestyle★ ·
    CATEGORY_CRITERIA(구분 기준 15종) · 골드 예시 5종(1312 예시 1~4 + 경계 보강, 신 분류값 표기).
  - **프롬프트 수정 원칙(스튜디오 개편)**: 코어 규칙·골드 예시 = 계약(읽기 전용 카드) · 수정은
    **모델 계열 쿡북 래퍼**({ROLE}{SCHEMA}{RULES}{EXAMPLES}{SELF_CHECK}{LEARNED} 템플릿,
    config.family_wrappers · 빈 저장=기본 복원) 단위로만. 스튜디오 = 계약 뷰 + 래퍼 편집 +
    호출별 모델 티어 + **최종 프롬프트 미리보기**(/prompt-preview?model&call&service · 콜×모델×서비스
    합성 결과). 구 추출·분석 카드 제거(검수·판정 카드는 유지).
  - **토큰 정책(2026-07-03 · Astryx 참조 감사)**: 간격은 4px 그리드 스케일 `--ds-space-1..10` +
    시맨틱 `--ds-pad-panel-x`(패널 좌우 20px)·`--ds-pad-inset`(패널 내 표·필터 인셋 16px, 정렬 정책의
    단일 원천). radius 는 용도 별칭으로 선택: `--ds-radius-card`(패널·카드)/`--ds-radius-control`
    (입력·버튼)/`--ds-radius-chip`(칩·배지). 포커스는 전 컴포넌트 `--ds-focus-ring` box-shadow 로
    통일(outline 하드코딩 금지 · 전역 :where 폴백만 예외). 신규 스타일은 하드코딩 px 대신 토큰 사용.
    비교 원천: Meta Astryx theme-neutral(spacing scale·radius 시맨틱 명명 참조 · React 컴포넌트
    계층은 미도입, 토큰 관례만 차용). 컴포넌트별 상태·사용 규칙은 **DESIGN_COMPONENTS.md**(계약)가
    단일 원천 · 스케일 일치 간격 px 는 토큰으로 전면 이관(serve 82곳 + ds-components 51곳, 값 보존).
  - 후속 소요: meta_compile 의 모델별 그룹 컴파일(feedback_routes.model 활용 · 모델별 프롬프트 개선 반영).

## 메타 체계 (코드가 이 기준으로 정렬)
`ItemMeta` 키: `summary`(리드문) · `entities` · `intent`(속성 분류) · `content_category` · `topic`/`topic_categories`(3차, 기본 빈값). 메타풀→토픽 전환(`metapool.py→topic.py`, `build_topics`).

## 코드 구조 (핵심 파일)
- `prism/serve.py`(~2,900줄) · HTTP 디스패치 + 콘텐츠 실행·검수 라우트(stdlib http.server). 컴포지션 루트(learnops/adminops 에 `_SV` 주입 + 하위호환 별칭).
- UI 는 2026-07-17 분할: 마크업 `prism/ui/NN-*.html` 24조각(`prism/page.py` 가 합성) · 앱 Alpine JS `prism/vendor/app-NN-*.js` 14조각(로더 `app.js`) + `app.css`. 도메인 지도는 `ARCHITECTURE.md` · 데모는 make_demo 가 재인라인.
- `prism/learnops.py` · 학습·골든·평가 도메인(learning_batch·eval_golden·소요서·버전 스냅샷). `prism/adminops.py` · 인증 프록시·JWT 캐시·권한 2단계·팀 액션.
- `prism/store.py`·`supastore.py` · dual-mode 저장소 + golden.
- `prism/pipeline.py·agents.py·prompts.py·verify.py·schema.py` · 추출 파이프라인. `abtest.py` · 평가 지표(grade_accuracy·reason_jaccard·empty_rate·cost).
- `prism/imagext.py` · 이미지 인제스트(방식 A, 코어 무수정).
- `scripts/make_demo.py` · GitHub Pages 데모(`docs/demo.html`) 재생성(fetch 스텁·CDN 폰트·vendor 복사).
- `design-system/` · Anchor 디자인 시스템(`--ds-*` 토큰, GmarketSans/Pretendard 이중폰트).

## UI·디자인 메모
- 홈 = **검수 아레나**(Flow·오늘의 미션·배지 22종·주간 리그·선수카드). 히어로 지표 = **검수 진척율**(개인·팀 평균).
- 폰트: display=**GmarketSans**(게임형) / body=**Pretendard**. 다크모드는 Tailwind 색을 `var(--ds-*)`로 토큰화.
- 버튼: 맨 텍스트 금지(박스/아이콘). `.ds-btn--primary/--secondary/--ghost`는 앱단 정의(재벤더링 DS 는 `--solid.--c-*`만).
- 검수 완료 표기 전역(목록·상세), 추가 수정 버튼·수정 일시 로그, 판정 색상(정확=초록/수정필요=빨강).
- **정책 팔레트(2026-07-03)**: 우하단 `?` 런처 → 드래그 가능한 플로팅 도움말(인텐트/카테고리/품질 사유/등급 기준 + 검색). 검수 상세가 열려 있으면 그 항목의 값이 바로가기 칩으로 뜨고, 상세의 값 태그 클릭 = 해당 기준 딥링크. 원천은 /dict(intentDefs·categoryCriteria 신규 노출) 단일 · 위치/탭 localStorage.

## 규칙 / 주의
- **제품 카피에 em-dash `—` 금지**(·/괄호/문장). 코드 식별자·커밋 메시지·주석·독스트링은 예외.
- `x-show`(display:none)는 `.space-y-* > :not([hidden]) ~` 마진에 잡혀 팬텀 마진 유발 → 조건부 첫 자식은 `x-if`.
- 커밋 trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- 릴리즈 노트·공개 레포에 내부(DNM/Confluence) 식별자·정책 노출 금지.

## 구조 감사 개선 (2026-07-03 · P1~P3 일괄)
- **리포트 영속화**: `_LAST_*` 전역 → `store.save_report/get_report`(kind×팀 upsert). 재시작 소실·팀 오염 해소. `/eval-judge` 레이트리밋 추가.
- **스토어 계약 테스트**: `tests/test_store_contract.py` 동일 시나리오 Mixin 을 sqlite/supabase 양쪽에 실행. 라이브는 `PRISM_TEST_SUPABASE=1`(일회용 auth 계정+팀 생성 후 정리 · contents.team_id 가 prism_teams FK 라 실팀 필수).
- **재실행 정합**: `sync_contents(include_all=True)` 로 비-YELLOW 재실행 결과도 upsert(모델·버전·review 갱신 누락 해소).
- **초안 전체 이력**: `drafts`(sqlite)·`prism_drafts`(supabase) 테이블에 (hash,모델,버전) 스냅샷 적재. 결과 비교 팝업의 정확 매칭 원천, patch_log 는 과거 데이터 폴백.
- **모델별 learned 계층**: feedback_routes.model 그룹 → `PR.LEARNED_BY_MODEL` → 그 모델의 item/call 프롬프트에만 병기(공통=모델 미기록 라우트). 메타컴파일도 모델별 그룹 컴파일(`model_results`).
- **골든 확정 인원 UI**: 학습 반영 카드에서 `golden_min_good`(1~5) 조정 → /config.
- **프롬프트 버전 스냅샷**: 학습 반영마다 다음 버전(v=회차+1)이 쓸 콜별 최종 시스템 프롬프트를 `prompt_snapshot_v{N}` 리포트로 영속. 조회 `GET /prompt-snapshot?v=N`(미지정=최신) · 버전 재현 근거.
- **자산 분리 1단계**: 인라인 앱 JS/CSS → `prism/vendor/app.js`·`app.css`. PyInstaller 스펙은 vendor 디렉토리 통째 포함이라 무변경.
- **라우트 분리(점진 3차 · 2026-07-03 후반)**: 학습·골든·평가 → `learnops.py`, 관리자·팀·인증 → `adminops.py`, HTML 마크업 → `page.py`(serve.py 7,667→2,507줄). serve 가 자기 모듈 객체를 `_SV` 로 주입(-m 실행 __main__ 이중 인스턴스 회피)하고 하위호환 별칭 유지 · HTTP 계약 무변경(스모크 게이트).
- **4호출 ①② 병렬 실행(기본 on · 2026-07-03 실측 승격)**: `Methodology.parallel_calls=True` 기본. 실키 A/B(solar-pro3, 16쌍 교차 2라운드): 평균 -15.5% · 중앙값 -18.0% 지연, API 실패 0, 산출 일치 16/16. 산출·차단 계약 파리티 유지(① 빈값 → ② 폐기·③④ 생략, ③④는 prior 의존이라 순차 유지). 순차 회귀 비교는 abtest 프리셋 `sequential`.

## 에이전트 구조 개선 (2026-07-03 후반)
- **개선 방향 검증(delta)**: learning_batch 가 골든 고정 → 개선 전 평가 → 메타컴파일 → 개선 후 평가 순으로 돌며 `improve_delta` 를 기록. **2%p 초과 악화면 LEARNED 원복**(보정 미반영 배지 · 완료 토스트에 효과 표기).
- **사전 갭 관측**: ③④ 드롭 원값·재요청 여부를 트레이스 verdict(`drop`)로 보존, `/learn-data` 의 `dict_gap`(상위 10 + 재요청 수)으로 집계 · 학습 데이터 탭 노출.
- **신뢰도 블렌드**: reviewer_weights = 골드 정확도 + Dawid-Skene EM 추정 정확도 평균(각 표본 5+).
- **콜별 텔레메트리**: LLMResult.tag → trace.by_call({n·cost·in·out·ms}) · 처리 이력에 '콜별 비용' 행.
- **quality ∥ item 병렬 옵션**: `Methodology.parallel_quality_item`(기본 off · 프리셋 `stage-parallel`) · 사후 게이트로 산출 파리티(R 이면 아이템 폐기 = 비용 트레이드오프) · 승격은 실측 A/B 후.
- **REAP 라우팅 확신 가드**: 재분류 confidence < 0.5 면 검수자 선택 요소 폴백(스테이지 오염 방지).
- **규칙 보정**: 문자열 단일값 코어션 + 인텐트 공백 변형(`속보 · 단신`) 구제로 재요청·드롭 절감.
- **mock 스키마 계약 테스트**: 태그별 mock 응답 키 ⊇ 실스키마 필수 키(드리프트 감시).
- 보류: legal 스코어러 병렬화(legal 기본 off · 필요 시 동일 패턴).

## 2026-07-06 세션 요약 (운영 개시 준비 · v0.5.6~v0.5.17)

상세는 `prism/daily-log/2026-07-06.md`. 핵심만:

- **운영 차단급 결함 수정**: ① 앱 관리자 요청 전반이 인증 헤더 없이 나가 supabase 모드에서 403(v0.5.7)
  ② supabase 가 yellow 만 저장해 인입 콘텐츠 전량 유실(v0.5.11) ③ 배치 내 중복 행이 upsert 전체를
  죽이는 21000(v0.5.15) ④ 큐 폴링 조기 종료로 진행률 미표시(v0.5.16) ⑤ 저장 실패가 성공처럼 보임(v0.5.14).
- **흐름 개편**: STEP 1 추가=저장만(미실행) → STEP 2 사용 모델 카드에서 대상(미실행만/전체)+실행 →
  STEP 3 실행 큐·이력(진척도·ETA·작업 클릭=콘텐츠 필터). 퀘스트 진행 중 재실행 물리 차단(v0.5.17).
- **UI**: 시작하기 3분할 카드(권한별·가이드 링크·숨김), API 키/가이드 링크 계층형 입력, 개별 삭제(파생 연쇄).
- **팀 가이드 링크**: reports kind='team_links'(supabase) → /config guideUrls. 내부 위키 URL 은 코드 금지
  (GitHub Pages 데모 공개). 시스템 설정에서 수정.
- **정리**: supabase 콘텐츠·초안 전량 삭제(팀 DNM·검수자·가이드 링크 보존) · 테스트용 임시 계정/팀 정리 완료.
- 키 상태: Upstage(`~/.prism_key`)·Timely(`~/.prism_timely_key`) 등록·연결 검증 완료. service_role 키는
  2026-04-27 발급 원본(로테이션 미처리 · 보류 중).

## 2026-07-07 세션 요약 (운영 전환 완료)

상세는 `prism/daily-log/2026-07-07.md`. 핵심만:

- **Fly 상시 서버 운영 개시**: prism-item.fly.dev 검증·재배포. 배포 절차 = main 최신화 → 클린 워크트리
  스냅샷에서 `fly deploy` → /config 검증. 07-06 "다음 단계" 1·2·5번(배포·온보딩·키 로테이션) 완료.
- **원문 페이지 패널**: 검수 상세 좌측 '추출 텍스트 ↔ 원문 페이지(iframe)' 탭 + 배율 3단계(작게50/보통75/크게100)
  + 다이얼로그 확장. source_url 이 실행(rerun)·run_pipeline·단건 추가에서 유실되던 3곳 수정(실행 upsert 가
  supabase 컬럼을 "" 로 덮어쓰던 결함). 템플릿·수동 폼에 원문 링크 입력 추가. ⚠️ 기존 건 URL 백필은
  재업로드 불가(save_dedup skip + 실행건 리셋 부작용) · 별도 기능 필요.
- **슈퍼관리자(권한 3단계)**: 운영 관리자(허용목록·전부) > 슈퍼관리자(생성자 부여 · 운영 작업 메뉴 전체,
  시스템 설정·데이터/팀 삭제·API 키 제외 · cond `opsadmin`) > 팀 관리자(위임 · 팀 관리만).
  지정/해제는 **오직 팀 생성자만**(서버+UI 이중 게이트 · 운영 관리자도 불가). DDL `prism_reviewers.super_admin`.
- **네이티브 confirm 10곳 → DS 확인 모달**(dsConfirm · Promise): CDP 자동화 렌더러 블로킹 해소.
- **병행 세션**: 모델러 핸드오프 번들(.zip) · 페르소나 능동 생성(usermeta) · `CLAUDE.md` 동시작업 규칙 신설
  (main 직커밋 금지 → 브랜치·PR · 파일 명시 스테이징 · 클린 스냅샷 배포).
- Confluence 사용자 가이드 STEP 0 = 브라우저 접속(prism-item.fly.dev) 안내로 교체(v7 · 맥 앱 안내 제거).

## 2026-07-08~09 세션 요약 (안정화 · 개선 배치 A~G · 모바일 검수)

상세는 `prism/daily-log/2026-07-08.md`·`2026-07-09.md`. 핵심만(PR #39~#75 머지·배포):

- **첫 실배치 사고 3중 복구 + 성능 3차**(7/8 · #39~#50): 파라미터 협상(_PARAM_ADAPT) ·
  세션 자동 갱신(refresh token) · 배포 감지 배너 · 첫 로드 4.7MB → 약 1.6MB · 재방문 54KB.
- **회의 백로그 12건 → 개선 배치 A~G 전량 반영**(7/8 오후 · #52~#62):
  A 작업 이력 타임라인(/history · 열람은 팀 생성자·슈퍼관리자 전용 #58) + 내/팀 판정 표기
  재설계("팀 판정 · X" + "팀 의견 · 정확 x개") · B 원문 링크 백필(/backfill-urls · 07-07
  미결 3번 해소) · C 판정 실행취소(취소 = 표 행 삭제 + 이력 보존 · 빈 표가 n 부풀리던
  카운트 버그 근치) + 추가 수정 이어쓰기(메모·요소 프리필) · D 품질 콜 실패 fail-open 제거
  (판정 보류 + fail_kind=api 재실행 · verify_item 엔티티 절단 잔재 제거) · E 배포-배치 충돌
  가드(CI 최대 10분 대기) + 실행 큐 영속화(reports kind=jobs · 재시작 시 중단 표시) ·
  F 인증 만료 통일(_afetch · 쓰기 45곳 · 401 → 갱신 → 재시도) · G E2E 스모크(실서버 부팅
  전 구간 · CI 게이트 합류).
- **프롬프트·사전 정책 전환**(#54 + 병행 #64): 엔티티 상한 없음·핵심만·복합 명사 원형 ·
  관점 인텐트 '옹호·지지/반박·비판' 신설(+판정 교정) · 카테고리/품질 사유 한글 표시(#65·#67).
- **게임 점수-검수 데이터 분리**(7/9 · #68): 점수는 파생 계산이라 피드백 삭제 = 진척도
  소멸이던 결합 해제 — 삭제 직전 score_carry 적립(총점 불변) + '게임 점수 초기화' 버튼
  (score_reset 음수 오프셋 · 검수 데이터·배지 불변).
- **모바일 검수 /m**(7/9 · #74·#75): 위 '실행 방법' 참조. 시안(HTML 6프레임) → v1(카드
  스택) → 운영 피드백 → v1.1(목록 기본 화면 · catKo 한글 · 사파리 woff2 폴백 · iOS<15.4
  dvh 폴백). (구 ⚠️ 이원화 부채는 2026-07-13 #86 해소: FIX_ELEMENTS·INTENT_DEF 를 /dict
  단일 원천(fixElements·intentDefs 병합)으로 통합 · 스모크 드리프트 가드 추가.)
- 테스트 120 → **159** · 병행 세션: 게시판(#71) · 패치노트 슬랙 스킬(#69·#73 · "#n부터
  패치노트" 요청 시 사용) · 정책 도움말 표(#70) · 팀 UI(#66) · 위키 정책 정합(#72).

## 2026-07-10~15 세션 요약 (보안 게이트 · 내 표 기준 · 엔티티 사전 · 토픽 스튜디오)

07-10~14 데일리로그는 공백(멀티 세션 상시 가동 · 상세는 PR #78~#147 이력). 핵심만:

- **보안: 데이터 GET 무인증 노출 차단**(07-10 · #81 · 배포됨): supabase 모드에서 페이지
  셸·/vendor·/config·서식 외 GET 은 로그인 필수(401). 무인증이면 team=None 으로 팀 필터가
  생략돼 전 팀 데이터가 내려가던 결함. /config 무인증 응답 최소화(프롬프트 계약·가이드 URL
  은 로그인 후) · 목록 fb 는 `_fb_public` 축약(verdicts 원본 = 검수자 식별자 미노출) ·
  SSE(/events)는 token 쿼리 검증. 잔여: topics_data 팀 파라미터화 · /presence POST 무인증.
- **검수 '완료' = 내 표(mine) 기준 완결**(07-10 · #79·#83): 목록·상세·다음 미검수 이동·드릴
  경로 전부 myVerdict(mine 우선 · 미제공 시만 합의 폴백) 단일 원천. 남이 검수한 콘텐츠도
  내가 안 했으면 판정 버튼 노출 + '팀 의견 · 정확 x개' 병기.
- **엔티티 사전(entdict.py · 07-11~14 신설, #129~#136)**: 적재 시 개체 등록(별칭·게이트) →
  나무위키 1순위·위키데이터 폴백 보강(타입 NER 6종 + 속성) → 토픽 조건(eattrs) 연동.
  미등재(unlisted) 상태 분리 · 위키데이터 429 백오프.
- **엔티티 사전 품질 교정**(07-15 · #144·#145 · 데일리로그 2026-07-15 상세): 리디아 고
  EV 오분류 신고 → 분류당 1표(PS 최우선) 판정 + 인포박스 실마크업(`<strong><span>`) 파싱
  수정 + 재판정 흐름 결함 2건 → 운영 재보강 645건(타입 84건 교정 · PS 211건 attrs 채움).
  **위키데이터 서킷 브레이커**: 배치 중 429 누적 8회·연속 실패 3회면 남은 배치는 위키데이터
  건너뜀(`wd_tripped` · 배치마다 리셋 · 수동 게이트 `PRISM_ENTDICT_WD=0`) · 건너뛴 배치는
  unlisted 강등 금지. ⚠️ 보류(pending) 296건은 다음 일괄 보강에서 위키데이터 재시도 대상.
- 그 외 병행(발췌): 첫 로드 성능·전체 감사 3부작(#137~#139) · 토픽 스튜디오 개편(수동/자동
  생성 탭 · 현황 통합 · eattrs 자동생성 #133~#142·#146·#147) · 모바일 v1.2(골드 문항·PWA ·
  #82 계열→#141) · 학습 배치 팀 스코프(#143).
- **배포 상태**: build 07-15 01:52(#145까지). #146·#147 은 다음 배포 대상.

## 2026-07-28 세션 요약 (배정 규칙 정합 · 라우터 402 · 모델 선택 UI · 검수운영)

07-16~27 데일리로그는 공백(멀티 세션 상시 가동 · 상세는 PR #150~#330 이력).
07-17 로그는 `origin/docs/log-0717` 에 미머지로 남아 있다. 07-28 상세는
데일리로그 `prism/daily-log/2026-07-28.md`.

- **배정 배타 규칙 · 화면이 서버와 어긋나 있었다**(#331): 서버(`reviewops.apply_feedback` ·
  07-17)는 "지정 검수자가 있으면 지정된 사람만 판정 · 생성자·관리자도 예외 없음"인데,
  07-22 커밋이 열람 예외를 풀면서 **판정 잠금까지 같이 풀어** 눌러도 서버가 거절하는
  막다른 길이 됐다. 열람(`assignedToOther` · 관리자 예외 유지)과 판정(`assignBlocked` ·
  예외 없음)을 분리하고 상세 잠금은 공용 규칙에 위임. 목록에는 `🔒 담당자 검수` 표시.
- **검수 대상 목록 기본 '미검수'**(#332): 완료분은 필터를 걸어야 보인다. 사라진 게
  아님을 알리는 `검수 완료 N건 숨김 · 보기` 힌트 동반.
- **목록 창/상한 순서 결함**(#337 · 병행): `raw_rows` 가 최신 200건을 자른 **뒤에** 미실행·
  홀드아웃을 걸러내 검수 대상 아닌 행이 창을 먹었다(미실행 200건 초과 시 표가 통째로
  빔). 제외 규칙을 먼저 적용하도록 뒤집고 완료 필터는 창 2000건으로 확대.
- **모델 선택 드롭다운 9곳 통일**(#334): 원본 id 대신 읽는 이름 + 제공자 배지 + 비용 등급.
  `prism/modelmeta.py` 신규(id → 이름·계열·등급) · 등급은 **실제 누적 비용 원장에서 계산**
  (과금 0 인 묶음 제외 · 표본 20건 미만 무표기). 마크업은 조각의 `<x-modelpick …>` 한 줄을
  `page.py` 합성 중에 펼친다(9벌 복사 방지). 메뉴는 `x-teleport` 로 body 에 빼낸다 —
  패널이 `overflow:hidden` + `:hover` transform 이라 fixed 로도 갇힌다.
- **검수운영 메뉴 신설**(#333 · 병행): 여력·일정 관리와 여력 비례 배정. 운영 실측
  (900슬롯 일괄 배정 후 11일간 조정 없음 · 처리 속도 23배 차이 · 미완료 293건이 일부
  인원에 묶임 · 2명 유휴)이 근거.
- ⚠️ **라우터 잔액 소진(HTTP 402)**: 07-28 텍스트 모델 호출 800건이 전건
  `insufficient_balance` 로 실패. 운영 텍스트는 **Timely 라우터**(`PRISM_TIMELY_KEY`)를 탄다.
  과금은 $0 이지만 **산출이 비어도 행은 저장돼**(runops → store_save) 200건이 YELLOW 로
  검수 목록에 올라왔다. 폴백 체인은 같은 키라 함께 402.
- ⚠️ **배포 대기 3건**(#333·#334·#337): Fly Deploy 워크플로에 "실행 중 배치를 끊지 않는다"
  가드가 있어 재실행 배치(`ingesting: true`) 중에는 10분 대기 후 중단된다. 운영은
  **v1.271(#332까지)**. 배치 종료 후 Actions 에서 재실행.
- **테스트 674 → 715**(모델 표시 18건 · 배정/목록 8건 · 검수운영 등 병행분 포함).

## 다음 단계 (2026-07-30 시점)

1. **타임리 지출 한도 확인** ← 콘솔에서. 7/29 에 Opus 348건($1.1251) 성공 뒤 832건이
   402 로 떨어졌다. `insufficient_balance`(충전)인지 `project_limit_exceeded`(한도 상향)
   인지에 따라 조치가 다르고, **API 로는 볼 수 없다**(12개 경로 404 · 문서에도 없음).
   앞으로 발생하는 402 는 #371 이후 실행 큐 화면에서 두 종류로 구분돼 보인다.
2. **배치 예산 상한 설정**(`batch_budget_usd`): 값을 넣으면 한도에 부딪히기 전에 우리가
   먼저 멈춰 빈 값 저장을 막는다. 현재 미설정 · 배치 1회 약 $1.3 이 기준선.
3. **슬랙 공지 발송 대기**: 검수자용 초안 작성·검증 완료(엔티티 라벨 + 노출 기준 완화 +
   참고사항). 발송 승인만 남았다. 발송기 `.claude/skills/prism-patchnote/send_slack.py`.
4. **엔티티 라벨 수집 → 확신도 재최적화**: #367 배포됨. `entlabel.export_rows` 로 합의
   표본이 100건쯤 모이면 가중치를 다시 맞춘다. **지금 가중치는 판별력을 올린 게 아니라
   구조적 상한만 푼 것**이다(어떤 조합도 AUC 0.61 로 평평했다 · 데일리로그 07-29).
4. **실패해도 저장되는 흐름 재검토**: 전 콜 실패(빈 산출)면 검수 대상으로 올리지 말지,
   올리되 별도 상태로 구분할지. 지금은 정상 YELLOW 와 구분이 안 된다.
5. **운영 사이클**(유지): 실배치 → 실행 → 퀘스트 → 검수(모바일 /m 병행) → 학습 반영.
   골드 문항 비율·미션 난이도·품질 배율·모델별 learned 실데이터 모니터링.
6. **모바일 v2 운영 확인**(2026-07-13 구현): 아이폰 실기기에서 홈화면 설치·정의 시트·
   폰트·본문 재확인(본문이 비면 "본문이 저장되지 않은 콘텐츠" 안내로 데이터/렌더 구분).
7. **모델별 검수 판정(hash×model) 스키마 확장**: 설계안 별도 제출 예정(대공사 · 배치 제외).
8. 데이터 대기(사용자 입력): 관점 인텐트 채택률(C1) · 샘플링 균형 표본 · 인텐트 매핑 오류 사례.
9. **큐(실행 작업) 단위 퀘스트**(설계안 보류 유지) · DPO 축적·Dawid-Skene·라우트 2단계.
10. 규모 조건부 성능: /raw 페이지네이션 · feedback_map 증분화 · Pretendard 서브셋 분할 잔여.
11. **엔티티 사전 후속**: 보류 296건 위키데이터 재시도(다음 일괄 보강) · topics_data 팀
    파라미터화 · /presence POST 무인증 정리 · 나무위키 fetch 재시도(일시 실패 → 미등재 오탐 방지).

## 메모리
작업·배포 머신(pete) 메모리: `prism-ops-environment`(배포 머신·prism-item·"배포해줘" 절차·키 파일 위치) ·
`reference-confluence-prism-pages`(DNM 페이지 ID). 세션 공통 규칙은 repo `CLAUDE.md` 가 정본.
