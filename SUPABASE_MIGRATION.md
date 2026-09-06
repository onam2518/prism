# Prism × Supabase · 데이터·인증 이행 설계서

> 목적: Prism의 **평가 워킹셋**(검수·피드백·REAP·리더보드 + 검토 중 콘텐츠)을 로컬 SQLite에서
> **Supabase(Postgres + Auth)**로 이행. 이로써 (1) **ID/PW 인증**(Supabase Auth) (2) **상시 공유
> DB**(클라우드, 호스트 머신 불필요) (3) **사칭 불가한 검수자 식별**을 얻는다.
> 프로덕션 콘텐츠 파이어호스는 대상 아님 · 검토하는 부분집합만.

## 결정 (확정)

| 항목 | 결정 | 근거 |
|---|---|---|
| 프로젝트 | ~~기존 **PromptForge**(`yujinhcdbllcnnfvcmfp`) 공유~~ → **2026-08-12 전용 프로젝트 분리: `prism`(`uycdzslkhkruvmyjcbgj` · ap-northeast-1)** | 공유 nano 인스턴스 CPU 고갈로 전 쿼리 8s 타임아웃 장애(8/12 오후) → 전용 Micro 분리. 스키마·데이터·auth.users(38 · UUID/해시 보존) 전량 이관, 옛 프로젝트(`yujinhcdbllcnnfvcmfp`)는 **2026-08-13 소유자 지시로 즉시 삭제**(1주 보관 계획 앞당김) |
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

### 콘텐츠 저장 경계 (2026-09-06 · 운영 미적용)

- 설치 파일: `scripts/migrate_content_team_guard.sql`. 문서의 전역 `hash` PK와
  `team_id uuid`를 전제로 하며 운영 DDL은 확인하지 않았다. 기존 `subtitle`, `source_url`,
  `model`, `version` 및 `migrate_content_images.sql`의 `image_urls jsonb` 컬럼이 필요하다.
- `public.prism_sync_contents(p_rows jsonb)`는 콘텐츠 배열 한 번을 한 트랜잭션으로 저장한다.
  `ON CONFLICT (hash)`의 행 잠금 아래 NULL까지 포함한 팀 일치를 검사한다. 다른 팀 행이
  한 건이라도 있으면 SQLSTATE `23505`로 신규 행·같은 팀 갱신도 전부 롤백한다.
  여러 팀이 같은 콘텐츠를 독립 보유하는 `(team_id, hash)` 이관은 포함하지 않는다.
- 같은 팀의 모델 산출·모델·버전은 갱신하고, 최초의 비어 있지 않은 출처와 DB의 최신
  `quality_meta.ops_hold`·`source_status`는 보존한다. 운영 플래그 변경/제거는 기존 팀 조건의
  PATCH 경로를 사용한다. 재실행이 보낸 오래된 플래그가 이후 운영자 수정을 되돌리지 않는다.
- 입력이 배열이 아니면 `22023`, 필수값·타입·제약 오류도 트랜잭션 전체 실패다. 함수는
  `SECURITY INVOKER`이며 PUBLIC/anon/authenticated 실행 권한을 제거하고 service_role에만
  허용한다. 기존 테이블/RLS 권한과 서버의 팀 인증 검증은 유지한다.
- 앱의 기존 여부/보존값 조회 오류는 쓰기 전에 중단한다. RPC 미설치·스키마 캐시 지연은
  저장 오류로 반환하며 직접 REST upsert로 폴백하지 않는다. 충돌은 HTTP 409 기반 저장
  오류로 전달하고 성공 건수로 집계하지 않는다. 연결/응답 유실은 커밋 여부가 불명확할 수
  있으므로 결과를 조회해 확인한다. 기존 공통 HTTP 재시도 정책은 이 변경 범위 밖이다.

적용 순서(별도 운영 승인 후):

1. 콘텐츠 쓰기 작업을 멈추고 운영 담당자가 대상 스키마/권한을 확인한다. 기존 버전의
   직접 REST upsert에는 이 RPC 보호가 없으므로 구버전 작성자를 함께 중단한다.
2. `psql -X -v ON_ERROR_STOP=1 "$PRISM_MIGRATION_DSN" -f scripts/migrate_content_team_guard.sql`
   또는 SQL Editor에서 파일 전체를 실행한다. BEGIN/COMMIT과 권한 설정을 분리하지 않는다.
   실패하면 전체 롤백 후 원인을 수정한다. 함수 재설치는 기존 콘텐츠를 수정하지 않는다.
3. PostgREST schema reload 후 새 코드를 배포한다. 테스트 팀의 정상 저장·타 팀 충돌·
   기존 운영 플래그 보존을 확인한 뒤 쓰기를 재개한다. 설치 없는 앱 선배포는 저장을 중단한다.
4. 앱 롤백 시 RPC는 남겨도 되지만 구버전 쓰기는 재개하지 않는다. 함수 삭제는 작성자 중단
   상태에서만 `DROP FUNCTION public.prism_sync_contents(jsonb)`로 수행한다.

로컬 검증: `python3 -m unittest discover tests -p 'test_content_preservation.py'`.
설치된 `initdb`/`pg_ctl`/`psql`로 일회용 Unix socket 전용 클러스터를 만들고 종료·삭제한다.
실행 파일이 없으면 SQL 테스트는 명시적으로 skip한다. 모의 HTTP는 항상 검증하며,
로컬 SQL 통과가 운영 DDL/실 PostgREST 설치 완료를 뜻하지 않는다.

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

**MCP 파트너 키(`prism_mcp_keys` 외 1, 2026-08-13 · 적용됨 · 트랙 B 외부 MCP · `prism/mcpkeys.py`)**:
```sql
-- 키는 sha256 해시만 저장(평문 미보관 · 발급 시 1회 표시).
-- key_id 는 난수 문자열이다 — 순차 정수면 남의 키 id 를 찍어 맞힐 수 있다(감사 O3).
-- team_id NOT NULL 이 핵심: 팀 없는 키는 DB 가 거절한다. 저장 계층은 team falsy 를
-- '내 팀 없음' 이 아니라 '전 팀' 으로 읽으므로(감사 H1) 그런 키가 있으면 안 된다.
create table if not exists public.prism_mcp_keys (
  key_id       text primary key,
  team_id      uuid not null,
  user_id      uuid not null references auth.users(id) on delete cascade,
  key_hash     text not null,
  key_prefix   text not null default '',        -- 화면에 남는 접두 6자
  label        text not null default '',
  revoked      boolean not null default false,
  created_at   timestamptz not null default now(),
  expires_at   timestamptz not null,            -- 기본 90일 · 최대 365일
  last_used_at timestamptz
);
create unique index if not exists ux_mcpkeys_hash on public.prism_mcp_keys(key_hash);
create index if not exists ix_mcpkeys_owner on public.prism_mcp_keys(team_id, user_id);
alter table public.prism_mcp_keys enable row level security;   -- 정책 없음 = service_role(서버) 전용

-- 사용 기록: **인증을 통과한 호출만** 쌓인다. 인증 실패를 적재하면 틀린 키를 난타하는 것만으로
-- 실사용 감사 기록이 밀려난다(감사 O2 · 스펙트럼 관문 실측).
create table if not exists public.prism_mcp_calls (
  id          bigint generated always as identity primary key,
  key_id      text not null references public.prism_mcp_keys(key_id) on delete cascade,
  team_id     uuid, user_id uuid,
  key_prefix  text not null default '', tool text not null default '',
  ok          boolean not null default false,
  ms          integer not null default 0, resp_bytes integer not null default 0,
  created_at  timestamptz not null default now()
);
create index if not exists ix_mcpcalls_key on public.prism_mcp_calls(key_id, created_at desc);
alter table public.prism_mcp_calls enable row level security;
```
조회는 `(user_id, team_id)`, 폐기는 `(key_id, team_id, user_id)` 복합 필터다(`supastore.mcp_key_revoke`).
팀 필터는 팀 스코프 없이 만든 배포 키에서 자기 팀 관리자가 타 팀 키를 끊을 수 있었던 감사 O3 의
회귀 방지이고, 소유자 필터는 그 반대편(팀은 맞는데 소유자를 안 봐 같은 팀 아무나 동료 키를 끊는 것)이다.
키는 개인 자격증명이라 관리자도 남의 키를 보거나 지우지 못한다.

> 적용: 2026-08-13 · 운영 프로젝트 `uycdzslkhkruvmyjcbgj` · 두 테이블 RLS enable + 정책 0(서버 전용) ·
> 인덱스·외래키 확인 완료. 기존 테이블 무변경.

**AI 초안 판정 상시 적재(`prism_autoreview`, 2026-08-19 · 적용됨 · 실험실 초안 판정 인박스 · `prism/autoreview.py`)**:
```sql
create table if not exists public.prism_autoreview (
  content_hash  text not null,
  reviewer_id   uuid not null references public.prism_reviewers(id) on delete cascade,
  team_id       uuid,
  verdict       text not null default '', confidence real not null default 0,
  reason        text not null default '', elements jsonb not null default '[]'::jsonb,
  model         text not null default '', content_model text not null default '',
  same_model    boolean not null default false,
  service       text not null default '', title text not null default '',
  grade         text not null default '',                -- 등급 dot(콘텐츠 관리 차용) 원천 · G/R
  created_at    timestamptz not null default now(),
  primary key (content_hash, reviewer_id)
);
create index if not exists ix_autoreview_reviewer on public.prism_autoreview(reviewer_id, created_at desc);
alter table public.prism_autoreview enable row level security;   -- 정책 없음 = service_role(서버) 전용
```
심판 모델이 검수자별로 채운 정확/수정 초안을 저장 → 페이지 이탈·재배포에도 유지 · 재실행 시 이미 초안
있는 건 스킵. 확정은 별개(`prism_feedback`) · 초안은 남겨 audit/학습 신호(초안 vs 사람 최종)로 쓴다.
검수자당 콘텐츠 1건(PK content_hash+reviewer_id · team_id 는 PK 밖 · feedback 관례와 동일).
> 적용: 2026-08-19 · 운영 프로젝트 `uycdzslkhkruvmyjcbgj` · `create_prism_autoreview` + `add_grade_to_prism_autoreview` ·
> RLS enable + 정책 0(서버 전용) · 스키마 리로드 완료. 기존 테이블 무변경.

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

## 집계 RPC (2026-07-29 · 성능 백로그 P3-1 1단계 · 적용됨)

핫패스 통계 3종을 서버측 집계 함수로 이관해 행 전송을 없앴다(파이썬 집계와 수치 동일 ·
운영 전 팀 파리티 검증 완료). 함수 미존재 환경(새 인스턴스·복제 DB)에서는 supastore 가
자동으로 행 다운로드 방식으로 폴백하므로 적용 순서와 무관하게 안전하다.

- `prism_agg_feedback_stats(p_team)` · `prism_agg_gold_stats(p_team)` ·
  `prism_agg_assignment_load(p_team)` — jsonb 반환 · `service_role` 전용(EXECUTE 회수:
  public/anon/authenticated).
- 원본 SQL 은 Supabase 마이그레이션 `prism_agg_rpc_hotpath_stats` 로 기록돼 있다
  (대시보드 → Database → Migrations). 수정 시 supastore 폴백 계산과 반드시 함께 바꿀 것.

## `prism_contents.source` = 최초 인입 경로 (2026-08-03 · DDL 불필요)

`source` 는 **콘텐츠가 처음 들어온 경로**(`단건`·`엑셀`·`배치`·`자동 인입`·`qa`)를 뜻한다.
종전 upsert 는 매 실행마다 `source` 를 덮어써서, 재실행이 한 번이라도 지나간 행은 최초 출처가
사라졌다(**운영 실측 2026-08-03: `prism_contents` 400건 전부 `재실행`**). 인입 채널별 품질·비용
분석과 "이미지 업로드 경로로 들어온 건이 있었는지"를 확인할 방법이 없어진다.

- **적용 방식**: 앱 계층에서 보존한다. `supastore.sync_contents` 가 쓰기 직전에
  `select=hash,source&hash=in.(…)` 로 기존 라벨을 읽어, 값이 있으면 그 값을 그대로 되돌려 보낸다
  (PostgREST 의 `resolution=merge-duplicates` 는 보낸 컬럼을 무조건 덮어써서
  `ON CONFLICT … COALESCE` 같은 조건부 갱신을 표현할 수 없다). sqlite 는 같은 의미를
  `ON CONFLICT … source=CASE WHEN COALESCE(results.source,'')='' THEN excluded.source ELSE results.source END` 로 처리한다.
- **DDL 변경 없음** → 마이그레이션 적용 순서와 무관하게 안전하다(코드만 배포하면 된다).
- **기존 데이터**: 이미 `재실행` 으로 덮인 행은 **복구 불가**(원본 라벨이 어디에도 남아 있지 않다).
  빈 값(`''`·NULL)인 행만 다음 저장에서 자연 백필된다.
- **최종 실행 경로**는 `model`·`version` 컬럼과 `prism_patch_log`(element `rerun:구모델->신모델`)로
  계속 추적된다 — `source` 는 이 용도로 쓰지 않는다.

**선택(미적용) · DB 측 방어선**: 서버 밖 경로(수동 SQL·다른 클라이언트)까지 막고 싶으면
아래 트리거를 넣을 수 있다. 앱 보존과 의미가 같고, 읽고-쓰는 사이의 경합도 함께 없앤다.
현재는 쓰기 주체가 Prism 서버 하나뿐이라 적용하지 않았다.
```sql
create or replace function public.prism_keep_first_source() returns trigger as $$
begin
  if coalesce(old.source, '') <> '' then
    new.source := old.source;      -- 최초 인입 경로 고정(재실행 upsert 가 덮지 못함)
  end if;
  return new;
end $$ language plpgsql;

create trigger prism_contents_keep_first_source
  before update on public.prism_contents
  for each row execute function public.prism_keep_first_source();
```

## 조회 스테이징 `prism_mq_stage` (2026-09-02 · 콘텐츠 조회 → 검수 지정)

bi-portal 메타베이스가 사내망 전용이라 운영 서버가 직접 조회하지 못한다. 사내망 수집기(status-agent
`prism_push.py`)가 발행분 행을 `/metaquery-stage` 로 올리고, 운영자가 조회 화면에서 보고 고른 것만
검수로 지정한다(`/metaquery-register`). 스테이징은 검수 콘텐츠가 아니다: `prism_contents` 와 분리 ·
선택 삭제(`/metaquery-stage-delete`) · 올린 뒤 7일이 지나면 서버가 올릴 때마다 정리한다.
설계: `docs/METACOLLECT_DESIGN.md`.

```sql
create table if not exists public.prism_mq_stage (
  hash         text not null,                     -- 콘텐츠 해시(store.content_hash · contents 와 같은 계약)
  team_key     text not null default '',
  row          jsonb not null,                    -- metaquery.COLUMNS 별칭 행(제목·본문·발행 메타)
  service      text not null default '',
  grade        text not null default '',
  title        text not null default '',
  published_at text not null default '',
  staged_at    timestamptz not null default now(),
  primary key (hash, team_key)
);
create index if not exists ix_mqstage_team_staged on public.prism_mq_stage(team_key, staged_at desc);
alter table public.prism_mq_stage enable row level security;   -- 정책 없음 = service_role(서버) 전용
```

> 적용: 미적용(2026-09-02 기준). 표가 없으면 조회 화면이 "스테이징 조회 실패 · 표 생성 여부 확인" 안내를 띄우고
> 다른 기능은 영향 없다.

