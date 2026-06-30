# Prism × Supabase — 데이터·인증 이행 설계서

> 목적: Prism의 **평가 워킹셋**(검수·피드백·REAP·리더보드 + 검토 중 콘텐츠)을 로컬 SQLite에서
> **Supabase(Postgres + Auth)**로 이행. 이로써 (1) **ID/PW 인증**(Supabase Auth) (2) **상시 공유
> DB**(클라우드, 호스트 머신 불필요) (3) **사칭 불가한 검수자 식별**을 얻는다.
> 프로덕션 콘텐츠 파이어호스는 대상 아님 — 검토하는 부분집합만.

## 결정 (확정)

| 항목 | 결정 | 근거 |
|---|---|---|
| 프로젝트 | 기존 **PromptForge**(`yujinhcdbllcnnfvcmfp`)의 격리 **`prism` 스키마** | +$0, 기존 public 무영향. Auth 공유 수용(소수 팀) |
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

## 스키마 (이미 적용됨 — `prism_schema_init`)

```
prism.reviewers(id uuid PK→auth.users, name, avatar, created_at)
prism.contents(hash PK, service, title, body, source, final_grade,
               item_meta jsonb, quality_meta jsonb, review, created_at)
prism.feedback(content_hash, reviewer_id→reviewers, service, title,
               verdict, stage, note, reap_remember/explain/ask/plan, ts,
               PK(content_hash, reviewer_id))
```
RLS: 인증 사용자 읽기(리더보드·합의), 쓰기는 본인 행만. 색인: feedback(reviewer_id, verdict), contents(review).

## 상세 설계 (확정)

### (a) 정체성 통일 — dual-mode 의 핵심
SQLite 는 검수자를 **이름 문자열**로, Supabase 는 **auth uuid**로 식별한다. serve 가 요청마다
`current_reviewer = {key, name, avatar}` 를 만든다:
- **sqlite**: `key = name`(사용자 입력) — 기존 동작 그대로.
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

## Phase 2 — store 계층 (dual-mode)

**목표**: `store.py` 의 메서드 계약을 그대로 둔 채 백엔드를 갈아끼운다.

- `store.py`: 기존 `Store`(SQLite) 유지. 신규 `supastore.py`에 **`SupabaseStore`** — 동일 메서드
  (`save_feedback`·`feedback_map`·`review_queue`·`arena_stats`·`set_reviewer`·`reviewers_map`·
  `save_reap`·`get_reap`·`save_many`/`save_dedup`(검토 콘텐츠만)) 를 **PostgREST REST**로 구현.
- `get_store()`(serve.py): `PRISM_BACKEND` 로 SQLite/Supabase 선택. 실패 시 SQLite 폴백.
- **REST 매핑 예**:
  - upsert: `POST /rest/v1/prism.feedback` + `Prefer: resolution=merge-duplicates`
  - 조회: `GET /rest/v1/prism.feedback?select=*` → Python 집계(arena_stats/feedback_map).
  - 소규모라 집계는 fetch 후 Python(현 로직 재사용). 추후 Postgres view/RPC 로 최적화 가능.
- **헤더**: `apikey: <service_role>`, `Authorization: Bearer <service_role>`,
  `Accept-Profile/Content-Profile: prism`(스키마 지정).
- **전제 설정**:
  1. Supabase 대시보드 **Settings→API→Exposed schemas 에 `prism` 추가**(기본 public만 노출).
  2. 서버 env: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`(비밀 — 커밋·로그 금지), `PRISM_BACKEND=supabase`.

## Phase 3 — 인증 (Supabase Auth)

- **프론트(온보딩 모달 교체)**: 이름-피커 → **로그인/가입**(이메일+비번). Supabase Auth REST
  (`/auth/v1/signup`·`/auth/v1/token?grant_type=password`)를 fetch 로 직접 호출(라이브러리 없이),
  `access_token` 보관. 첫 로그인 시 캐릭터 선택 → `prism.reviewers` upsert(name·avatar).
- **서버**: 요청의 `Authorization: Bearer <user JWT>` 검증 → `reviewer_id`. 모든 피드백·검수가
  *검증된 reviewer_id*에 귀속(클라이언트가 보낸 이름 신뢰 안 함 → **사칭 불가**).
- 리더보드·아레나의 reviewer 는 `prism.reviewers.name`(인증 사용자).

## Phase 4 — 콘텐츠 동기화 + retention

- 추출 결과 중 **검토 대상만**(YELLOW 또는 명시 샘플) `prism.contents` upsert. 전량 아님.
- **retention**: `created_at < now() - INTERVAL 'N days'` 주기 삭제(서버 기동 시 또는 크론).
  기본 30일(35k/일·본문 포함도 8GB 내). 설정값 `PRISM_RETENTION_DAYS`.

## Phase 5 — 검증·컷오버

- `PRISM_BACKEND=sqlite`(기본) 회귀: 기존 동작 무변경 확인.
- `PRISM_BACKEND=supabase`: 가입→로그인→검수→리더보드/아레나/REAP E2E.
- RLS 검증: 타인 행 쓰기 차단. 사칭 시도 차단(서버 JWT 검증).
- 컷오버: 팀은 supabase 모드, 개인 오프라인은 sqlite 모드. 데이터 이관 필요 시 일회성 스크립트.

## 보안 원칙

- **service_role 키는 절대 커밋·로그·채팅 금지** — 서버 env 로만. `.gitignore` 에 `.env` 포함.
- publishable(anon) 키는 프론트용(공개 안전).
- RLS 는 심층방어로 유지(서버 검증 + RLS 이중).
- 비밀번호는 Supabase Auth 가 해시·관리(Prism 은 평문 비번 취급 안 함).

## 작업 순서

1. (이 문서) 설계 확정
2. Phase 2: `supastore.py` + `get_store` 분기 + 노출 스키마 설정 안내
3. Phase 3: 프론트 로그인/가입 + 서버 JWT 검증
4. Phase 4: 콘텐츠 동기화 + retention
5. Phase 5: 양모드 E2E
