# Prism × Supabase · 데이터·인증 이행 설계서

> 목적: Prism의 **평가 워킹셋**(검수·피드백·REAP·리더보드 + 검토 중 콘텐츠)을 로컬 SQLite에서
> **Supabase(Postgres + Auth)**로 이행. 이로써 (1) **ID/PW 인증**(Supabase Auth) (2) **상시 공유
> DB**(클라우드, 호스트 머신 불필요) (3) **사칭 불가한 검수자 식별**을 얻는다.
> 프로덕션 콘텐츠 파이어호스는 대상 아님 · 검토하는 부분집합만.

## 결정 (확정)

| 항목 | 결정 | 근거 |
|---|---|---|
| 프로젝트 | 기존 **PromptForge**(`yujinhcdbllcnnfvcmfp`), **`public.prism_*`** 테이블(접두사) | +$0. public 기본 노출 → **REST 노출 설정 불필요**. Auth 공유 수용(소수 팀) |
| 아키텍처 | **Frontend → Prism 서버 → Supabase** (서버가 허브) | 현 구조 유지, store만 교체 |
| store 접근 | **PostgREST REST + urllib**(stdlib 유지) | "의존성 0" 거의 보존. psycopg는 선택지(직접연결·의존성↑) |
| 모드 | **dual-mode**: `PRISM_BACKEND=sqlite`(기본·로컬) ↔ `supabase`(팀) | 로컬 오프라인 사용 보존 |
| 콘텐츠 | **검토 대상만**(YELLOW·샘플) `prism.contents` 적재 + retention | 파이어호스 제외 |
| 인증 | **Supabase Auth(이메일+비번)**, 검수자 = `auth.users` | 사칭 불가, RLS 본인 쓰기 강제 |

## 아키텍처

```
[브라우저]                         [Prism 서버(stdlib http.server)]        [Supabase]
  Supabase Auth(이메일+비번) ─로그인→ access_token(JWT)
  JWT 를 Authorization 헤더로 ───────▶ 요청마다 JWT 검증(/auth/v1/user 또는 JWT secret)
                                       → reviewer_id(uuid) 확정
  검수/피드백 ──────────────────────▶ SupabaseStore (service_role)
                                          └─ PostgREST REST(urllib) ──▶ prism.* 테이블
  아레나/리더보드 ◀── 집계(REST fetch + Python aggregate) ◀───────────┘
```

- **서버가 service_role 로 DB 접근**(RLS 우회) → 신원은 *서버가 검증한 JWT*로 강제. RLS 는
  직접 접근 대비 심층방어로 유지.
- 로컬 모드(`sqlite`)면 위 전부 우회하고 기존 SQLite 그대로(오프라인).

## 스키마 (적용됨 · `prism_move_to_public`)

**`public.prism_*`** 테이블(접두사로 구분, public 기본 노출 → 노출 설정 불필요):
```
public.prism_reviewers(id uuid PK→auth.users, name, avatar, created_at)
public.prism_contents(hash PK, service, title, body, source, final_grade,
               item_meta jsonb, quality_meta jsonb, review, created_at)
public.prism_feedback(content_hash, reviewer_id→prism_reviewers, service, title,
               verdict, stage, note, reap_remember/explain/ask/plan, ts,
               PK(content_hash, reviewer_id))
```
RLS: 인증 사용자 읽기(리더보드·합의), 쓰기는 본인 행만. 색인: feedback(reviewer_id, verdict), contents(review).
**REST 검증됨**: anon 키로 3 테이블 GET 200(노출 확인). 쓰기/인증 읽기는 service_role(서버).

**학습 루프 확장(적용됨 · `prism_learning_loop_tables`, 2026-07-02)**:
```
public.prism_feedback + element text                      -- 교정 대상 요소 영속
public.prism_patch_log(id, team_id, content_hash, reviewer_id,
               element, before jsonb, after jsonb, created_at)   -- 교정 전/후 append(선호쌍 원천)
public.prism_gold_checks(id, team_id, content_hash, reviewer_id,
               expected, verdict, correct, created_at)     -- 골드 문항 응답(검수자 품질 측정)
public.prism_events(id, team_id, reviewer_id, kind, day, bonus, meta,
               created_at, UNIQUE(reviewer_id, kind, day)) -- 미션 보상 1회 기록(감사 추적)
```
신규 3 테이블은 RLS enable + 정책 없음 = service_role(서버) 전용.

**콘텐츠별 검수 담당 배정(`prism_assignments`, 2026-07-14 · 적용됨 · 운영 DB 존재 확인 2026-07-28)**:
```sql
create table if not exists public.prism_assignments (
  content_hash  text not null,
  reviewer_id   uuid not null references public.prism_reviewers(id) on delete cascade,
  team_id       uuid,
  min_reviewers integer not null default 1,       -- 통과 기준 N(콘텐츠당 동일, 비정규화)
  ts            timestamptz not null default now(),
  primary key (content_hash, reviewer_id, team_id)
);
create index if not exists ix_assign_team on public.prism_assignments(team_id, reviewer_id);
alter table public.prism_assignments enable row level security;   -- 정책 없음 = service_role(서버) 전용
```
배정되면 해당 콘텐츠는 담당자에게만 큐 노출(배타적). 미배정 콘텐츠는 기존 오픈 큐 유지.
진척: 개인 분모 = 내 담당 수, 팀 진척 = Σ 콘텐츠별 min(검수인원,N)/N ÷ 배정 콘텐츠 수.
> PK 에 team_id 포함 → team 단위 격리. team_id NULL(팀 미소속)은 실사용 없음(배정은 팀 관리자 기능).

**엔티티 사전(`prism_entities` 외 2, 2026-07-14 · 적용됨 · `prism_entity_dictionary` 마이그레이션)**:
```sql
-- 개체 사전: 고유키(entity_id)·타입(NER 6종: PS·OG·LC·AF·EV·TM)·타입별 속성(attrs jsonb)
create table if not exists public.prism_entities (
  entity_id    text primary key,                  -- 'e_'+sha1(정규화 이름)[:12] 대리키
  name         text not null,                     -- 정규 표기
  type         text not null default '',          -- ''=보류(타입만 미부여 · 노출 유지)
  status       text not null default 'pending',   -- active | pending | merged
  attrs        jsonb not null default '{}',       -- 성별·국적·직업(대분류)·소속 등 타입별 속성
  attr_meta    jsonb not null default '{}',       -- 필드별 {source: wikidata|manual, status: auto|confirmed}
  external_ids jsonb not null default '{}',       -- {wikidata: QID, object_id: …} 외부 공통키 매핑
  merged_into  text not null default '',
  created_at   double precision,
  updated_at   double precision
);
-- 별칭 조회(이형 표기 → 개체) · 위키데이터 정식 라벨도 보강 시 자동 등재
create table if not exists public.prism_entity_aliases (
  alias     text primary key,
  entity_id text not null
);
create index if not exists ix_ealias_ent on public.prism_entity_aliases(entity_id);
-- 콘텐츠 ↔ 개체 링크(팀 스코프) · item_meta.entities 추출 산출물은 불변, 링크만 추가
create table if not exists public.prism_content_entities (
  content_hash text not null,
  entity_id    text not null,
  surface      text not null default '',           -- 원문 표기(추출 문자열)
  team         text not null default '',
  ts           double precision,
  primary key (content_hash, entity_id, team)
);
create index if not exists ix_centities_ent on public.prism_content_entities(entity_id);
alter table public.prism_entities enable row level security;          -- 정책 없음 = service_role 전용
alter table public.prism_entity_aliases enable row level security;
alter table public.prism_content_entities enable row level security;
```
사전은 전역(개체는 팀 무관 사실), 콘텐츠 링크만 팀 스코프. 타입·속성 보강은 Wikidata(POC) ·
수동 확정(attr_meta.status=confirmed) 필드는 재보강이 덮어쓰지 않음. 설계 배경: DNM 위키 366018723.

**평가 런(`prism_eval_runs` 외 1, 2026-07-18 · Atelier eval_runs 체계 이식)**:
```sql
-- 평가 런: 골든셋 평가 실행 단위(이력). 청크마다 cursor 갱신 → 진행률 폴링·중단 재개.
create table if not exists public.prism_eval_runs (
  id          bigint generated always as identity primary key,
  team_id     uuid,
  model       text not null default '',              -- ''=당시 기본 모델
  scope       text not null default 'all',           -- all=전체 정답셋 | eval=평가용 홀드아웃
  status      text not null default 'running',       -- running | done | failed | cancelled
  cursor      integer not null default 0,
  total       integer not null default 0,
  metrics     jsonb,                                 -- 증분 카운터(n·grade_hit·per_reason 등)
  error       text,
  created_by  text not null default '',              -- 시작한 관리자 uid
  created_at  timestamptz not null default now(),
  finished_at timestamptz
);
create index if not exists ix_eval_runs_team on public.prism_eval_runs(team_id, id desc);
-- 건별 결과: 기대 vs 실제 등급·사유 스냅샷(불일치 감사·재개 판별 원천). 재실행 upsert 안전.
create table if not exists public.prism_eval_results (
  run_id       bigint not null references public.prism_eval_runs(id) on delete cascade,
  content_hash text not null,
  title        text not null default '',
  expected     jsonb,
  got          jsonb,
  passed       boolean,
  error        text not null default '',
  created_at   timestamptz not null default now(),
  primary key (run_id, content_hash)
);
alter table public.prism_eval_runs enable row level security;     -- 정책 없음 = service_role 전용
alter table public.prism_eval_results enable row level security;
```
기존 즉시 평가(`/eval-golden`)와 채점 규칙 동일(abtest.score 단일 소스) · 런 영속화로
이력 비교·서버 재시작 후 재개를 더한다. 설계 원천: Atelier(구 PromptForge) eval_runs/eval_run_results.

**루브릭 진단(`prism_eval_rubric` 마이그레이션, 2026-07-18 · 적용됨 · Atelier rubric-judge 이식)**:
```sql
alter table public.prism_eval_runs
  add column if not exists rubric_status text not null default '',   -- ''|running|done|failed|cancelled
  add column if not exists rubric_cursor integer not null default 0,
  add column if not exists rubric jsonb;                             -- 축별 평균 + n
alter table public.prism_eval_results
  add column if not exists rubric jsonb;    -- {accuracy,format,policy,conciseness,note} 1~5
```
완주 런의 건별 산출을 LLM 심사관이 4축(정확성·형식·정책·간결성) 1~5점으로 배치 채점
(RUBRIC_CHUNK=10건/호출 · 미채점 건만 재실행). 낮은 축 = 프롬프트 개선 우선순위.

**오토파일럿(`prism_autopilot_runs`, 2026-07-18 · 적용됨 · Atelier autopilot 이식)**:
```sql
create table if not exists public.prism_autopilot_runs (
  id bigint generated always as identity primary key,
  team_id uuid, status text not null default 'running',   -- running|done|stopped|failed
  target numeric not null default 0.9, max_rounds integer not null default 5,
  round integer not null default 0,
  start_accuracy numeric, best_accuracy numeric, last_accuracy numeric,
  history jsonb,                       -- 라운드별 {round,accuracy,pre,delta,reverted,version}
  stop_reason text, error text, created_by text not null default '',
  created_at timestamptz not null default now(), heartbeat_at timestamptz, finished_at timestamptz
);
create index if not exists ix_autopilot_team on public.prism_autopilot_runs(team_id, id desc);
alter table public.prism_autopilot_runs enable row level security;
```
한 라운드 = learnops.learning_batch(피드백 보정→같은 정답셋 재평가 · 악화 자동 원복).
종료 = 목표 달성 · 개선 정체(2라운드 연속 무향상) · 최대 라운드(cap 10) · 수동 중지.

**프롬프트 배포(`prism_deployments` 외 1, 2026-07-18 · 적용됨 · Atelier deployments 이식)**:
```sql
create table if not exists public.prism_deployments (
  id bigint generated always as identity primary key,
  team_id uuid, slug text not null unique, name text not null default '',
  version integer not null default 0,          -- prompt_snapshot_v{N} pin · 0=항상 최신
  active boolean not null default true, created_by text not null default '',
  created_at timestamptz not null default now(), updated_at timestamptz
);
create table if not exists public.prism_deployment_keys (
  id bigint generated always as identity primary key,
  deployment_id bigint not null references public.prism_deployments(id) on delete cascade,
  key_hash text not null,                      -- sha256 · 평문 미보관(발급 시 1회 표시)
  key_prefix text not null default '', revoked boolean not null default false,
  created_at timestamptz not null default now(), last_used_at timestamptz
);
create index if not exists ix_depkeys_dep on public.prism_deployment_keys(deployment_id);
alter table public.prism_deployments enable row level security;
alter table public.prism_deployment_keys enable row level security;
```
공개 서빙 `GET /api/v1/prompt?slug=` + `Authorization: Bearer pr_live_…`(deployops 자체 검증).
pin 교체 = 호출측 무변경 즉시 프롬프트 교체.

**프롬프트 라이브러리(`prism_prompt_library`, 2026-07-18 · 적용됨 · Atelier prompt_library 이식)**:
```sql
create table if not exists public.prism_prompt_library (
  id bigint generated always as identity primary key,
  team_id uuid, name text not null default '', domain text not null default '',
  prompt text not null default '', note text not null default '',
  source text not null default 'manual',       -- manual | builder 등 출처
  pinned boolean not null default false, created_by text not null default '',
  created_at timestamptz not null default now()
);
create index if not exists ix_prompt_library_team on public.prism_prompt_library(team_id, pinned, id desc);
alter table public.prism_prompt_library enable row level security;
```
스튜디오 라이브러리 탭: 패턴 저장·핀 우선 정렬·복사·단계 원천 지시 적용(빌더 결과 저장 연동).

## 테이블 네임스페이스 정리 방침 (2026-07-18)

같은 Supabase 프로젝트(구 PromptForge)에 두 제품의 테이블이 공존해 왔다. Atelier 를
Prism 으로 이식(기능 흡수)하면서 **`prism_` 접두사를 유일한 정식 네임스페이스**로 통일한다.

- **정식**: `prism_*` — 신규 테이블은 반드시 이 접두사(supastore 가 접두사를 하드코딩).
- **legacy(Atelier · 구 PromptForge)**: 무접두사 22개(`sessions`·`versions`·`model_results`·
  `test_cases`·`test_results`·`usage_logs`·`profiles`·`teams`·`datasets`·`eval_runs` 등).
  Atelier 앱 은퇴 후 보존 가치 확인 → 아카이브(export) → 삭제. **삭제는 소유자 확인 후에만.**
- **백업**: `prism_*_bak_20260707` 7개 — 복구 시효 지나면 삭제 후보.

**은퇴 실행(2026-07-18 · 적용됨 · `atelier_retire_step1_decouple`/`step2_legacy_schema`)**:
최근 7일 호출 0 · 마지막 실사용 2026-06-17 확인 후 되돌림 가능한 범위로 실행.
```
① cron 중지: promptforge_autopilot_tick(매분!) · promptforge_daily_cleanup
② 가입 트리거 분리: on_auth_user_created(auth.users→profiles) drop
   — auth 는 Prism 과 공유라 profiles 의존을 먼저 끊어야 가입이 안 깨진다
③ 무접두사 22개 → `legacy` 스키마 이동(41MB 데이터 보존 · PostgREST 비노출)
   복원: alter table legacy.<t> set schema public;
④ Atelier 전용 함수 drop(claim_next_forge_version·cleanup_promptforge_data·
   trigger_eval_tick·invoke_autopilot_tick·inc_*_fork) · set_updated_at 은 유지(트리거 참조)
```
→ public 스키마 = `prism_*` 26개(운영 19 + `_bak_20260707` 7)로 통일.
~~남은 일(소유자 확인 후): `drop schema legacy cascade` · bak 7개 drop~~
**은퇴 완결(2026-07-18 · 소유자 승인 · `atelier_retire_step3_purge`)**: legacy 스키마(22개
테이블 · 41MB)·`prism_*_bak_20260707` 7개 영구 삭제 → public = `prism_*` 운영 19개만 잔존.
남은 일: Atelier 깃 레포 아카이브(GitHub 설정 · 코드 밖 작업).

## 상세 설계 (확정)

### (a) 정체성 통일 · dual-mode 의 핵심
SQLite 는 검수자를 **이름 문자열**로, Supabase 는 **auth uuid**로 식별한다. serve 가 요청마다
`current_reviewer = {key, name, avatar}` 를 만든다:
- **sqlite**: `key = name`(사용자 입력) · 기존 동작 그대로.
- **supabase**: `key = auth uuid`, `name = 표시명`.

store 메서드는 **`reviewer_key`(귀속 키) + `reviewer_name`(표시)** 를 받는다.
- sqlite: `feedback.reviewer = key(=name)`.
- supabase: `feedback.reviewer_id = key(uuid)`, `reviewers.name = name`.
리더보드·합의 표시는 항상 `name`. → serve 위쪽 코드는 백엔드 무관하게 동일.

### (b) JWT 검증 (supabase 모드)
서버가 요청의 `Authorization: Bearer <user JWT>` 로 **`GET {url}/auth/v1/user`**(apikey=anon)
호출 → 200 이면 `user.id`(uuid)·`email` 확보 = `current_reviewer.key`. 토큰별 **짧은 캐시(60s)**.
실패 → 401. (로컬 서명검증보다 단순·정확; JWT secret 불필요.) sqlite 모드는 인증 없음.

### (c) Store 인터페이스 (양 백엔드 동일 시그니처)
`save_feedback · feedback_map · feedback_stats · review_queue · arena_stats · set_reviewer ·
reviewers_map · save_reap · get_reap · learned_by_stage · save_many/save_dedup(검토 콘텐츠만)`.
집계(arena_stats·feedback_map)는 supabase 에서 **REST fetch → 기존 Python 집계 재사용**(소규모).

### (d) 콘텐츠 동기화 트리거
supabase 모드: `store_save` 에서 **`review=='yellow'` 또는 명시 sample 인 콘텐츠만**
`prism.contents` upsert(파이어호스 제외). retention: 서버 기동 시 + 주기적으로
`delete where created_at < now() - INTERVAL 'N days'`(기본 30).

## Phase 2 · store 계층 (dual-mode)

**목표**: `store.py` 의 메서드 계약을 그대로 둔 채 백엔드를 갈아끼운다.

- `store.py`: 기존 `Store`(SQLite) 유지. 신규 `supastore.py`에 **`SupabaseStore`** · 동일 메서드
  (`save_feedback`·`feedback_map`·`review_queue`·`arena_stats`·`set_reviewer`·`reviewers_map`·
  `save_reap`·`get_reap`·`save_many`/`save_dedup`(검토 콘텐츠만)) 를 **PostgREST REST**로 구현.
- `get_store()`(serve.py): `PRISM_BACKEND` 로 SQLite/Supabase 선택. 실패 시 SQLite 폴백.
- **REST 매핑 예**:
  - upsert: `POST /rest/v1/prism.feedback` + `Prefer: resolution=merge-duplicates`
  - 조회: `GET /rest/v1/prism.feedback?select=*` → Python 집계(arena_stats/feedback_map).
  - 소규모라 집계는 fetch 후 Python(현 로직 재사용). 추후 Postgres view/RPC 로 최적화 가능.
- **헤더**: `apikey: <service_role>`, `Authorization: Bearer <service_role>`. (public 스키마라
  Profile 헤더 불필요.)
- **전제 설정**: 서버 env `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`(비밀 · 커밋·로그 금지),
  `PRISM_BACKEND=supabase`. **대시보드 노출 설정 불필요**(public 기본 노출).

## Phase 3 · 인증 (Supabase Auth)

- **프론트(온보딩 모달 교체)**: 이름-피커 → **로그인/가입**(이메일+비번). Supabase Auth REST
  (`/auth/v1/signup`·`/auth/v1/token?grant_type=password`)를 fetch 로 직접 호출(라이브러리 없이),
  `access_token` 보관. 첫 로그인 시 캐릭터 선택 → `prism.reviewers` upsert(name·avatar).
- **서버**: 요청의 `Authorization: Bearer <user JWT>` 검증 → `reviewer_id`. 모든 피드백·검수가
  *검증된 reviewer_id*에 귀속(클라이언트가 보낸 이름 신뢰 안 함 → **사칭 불가**).
- 리더보드·아레나의 reviewer 는 `prism.reviewers.name`(인증 사용자).

## Phase 4 · 콘텐츠 동기화 + retention

- 추출 결과 중 **검토 대상만**(YELLOW 또는 명시 샘플) `prism.contents` upsert. 전량 아님.
- **retention**: `created_at < now() - INTERVAL 'N days'` 주기 삭제(서버 기동 시 또는 크론).
  기본 30일(35k/일·본문 포함도 8GB 내). 설정값 `PRISM_RETENTION_DAYS`.

## Phase 5 · 검증·컷오버

- `PRISM_BACKEND=sqlite`(기본) 회귀: 기존 동작 무변경 확인.
- `PRISM_BACKEND=supabase`: 가입→로그인→검수→리더보드/아레나/REAP E2E.
- RLS 검증: 타인 행 쓰기 차단. 사칭 시도 차단(서버 JWT 검증).
- 컷오버: 팀은 supabase 모드, 개인 오프라인은 sqlite 모드. 데이터 이관 필요 시 일회성 스크립트.

## 보안 원칙

- **service_role 키는 절대 커밋·로그·채팅 금지** · 서버 env 로만. `.gitignore` 에 `.env` 포함.
- publishable(anon) 키는 프론트용(공개 안전).
- RLS 는 심층방어로 유지(서버 검증 + RLS 이중).
- 비밀번호는 Supabase Auth 가 해시·관리(Prism 은 평문 비번 취급 안 함).

## 작업 순서 (완료 상태)

1. ✅ 설계 확정 (이 문서)
2. ✅ **Phase 2** `supastore.py`(PostgREST·stdlib) + `get_store` dual-mode 분기 · 라이브 E2E 검증
3. ✅ **Phase 3** Supabase Auth(ID/PW) 로그인/가입 프록시 + 서버 JWT 검증 + 사칭 불가 · 라이브 검증
4. ✅ **Phase 4** 콘텐츠 동기화(`sync_contents`, 검토 대상만) + `retention` · supastore 구현
5. ✅ **Phase 5** 양모드 E2E · supabase(인증·검수·아레나) + sqlite(회귀) 검증

**운영 전환**: 서버 env `SUPABASE_URL`·`SUPABASE_SERVICE_KEY`·`PRISM_BACKEND=supabase` 설정 시
Supabase 모드(상시 클라우드·ID/PW·팀 공유), 미설정 시 로컬 SQLite(오프라인). 코드 변경 없이 전환.
