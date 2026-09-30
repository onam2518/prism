# DNM 전환 구현 검증 — 2026-09-30

## 범위

원천 경로 등록, 불변 실행 명세, 항목별 영속 시도 예약, 현재 발행 상태, 수동 확정/정답/SFT 결속, 고정 평가, 운영 승인/롤백, 토픽 조건식과 미리보기 승인/CAS/undo를 추가했다. 관리자 API와 계약은 [DNM_TRANSITION.md](DNM_TRANSITION.md)에 있다.

실제 원천 등록표·정책 승인·운영 품질 목표를 만들어 넣지 않았다. 실제 인입과 외부 소비처 연결, 합의된 표본의 품질·비용·지연 검증이 끝나기 전에는 전체 DNM 전환 완료가 아니다.

## 코드 검증

- Python 3.14와 Python 3.8 각각 전체 unittest **2,280개 통과**, 기존 조건부 skip 20개. HTTP 서버 테스트도 실행했다.
- pyflakes, 앱 JavaScript 구문 검사, 변경 공백 검사 통과.
- `node tests/test_topic_client.js`: 실제 클라이언트 코드의 미리보기→승인 토큰 전송, 취소 시 쓰기 없음, 충돌 시 쓰기 없음, 중첩 조건과 외부 참조 표시 검증.
- 고정 평가의 프롬프트 기록을 기존 조회/ZIP API에 연결하는 과정에서 발견한 호환성 오류를 수정했다. ZIP에는 실제 실행 명세 `execution.json`을 포함하며, DNM 기록에 현재 품질 프롬프트를 사후 추가하지 않는다.
- 고정 평가도 운영처럼 네 항목을 병렬 호출한다. 지연시간은 개별 호출 시간의 합이 아닌 실제 경과 시간이며, 비용과 토큰은 모든 시도의 합이다. 네 호출의 동시 진입과 시간·비용 집계를 회귀 테스트로 검증했다.
- 이 실행 환경에서는 내장 브라우저 backend와 Chrome 자동화 연결을 사용할 수 없어 시각적인 브라우저 검증은 수행하지 못했다. HTTP·클라이언트 로직 테스트를 화면 검증으로 표현하지 않는다.

## 운영 DB 검증

적용 migration:

1. `20260930004533_versioned_policy_state`: reports CAS RPC, 제어 설정과 상태의 동시 비교, 서비스 역할 전용 실행 권한.
2. `20260930005147_restrict_direct_table_access`: `public.prism_*` 표/시퀀스의 공개·로그인 역할 직접 접근 제거, RLS 유지, 앱 서버의 서비스 역할 접근 유지.

기존 DB에는 팀 한정 정책과 `authenticated USING (true)` 정책이 함께 있었다. 허용 정책이 OR로 결합되어 팀 제한을 우회하고, 사용자 자신의 프로필에서 관리자 여부/팀을 직접 변경할 수 있었다. 실제 앱은 브라우저가 Python API를 호출하고 서버의 `SupabaseStore`가 서비스 역할로 DB에 접근하므로, 사용하지 않는 직접 접근 정책과 권한을 제거했다. 사용자 데이터 행은 수정하지 않았다.

- 서비스 역할로 CAS 최초 저장·구버전 거절·제어 설정 검사·팀 키 분리 검증 통과. 검증 SQL의 fixture는 트랜잭션 롤백.
- 별도 동시 요청 2개로 동일 revision을 갱신: 정확히 1개 성공, 1개 충돌. 해당 합성 행은 검증 후 제거했고 잔여 fixture 0건.
- 일반 로그인 역할의 콘텐츠 직접 조회와 프로필 관리자 권한 직접 변경이 `insufficient_privilege`로 거절됨을 확인.
- 콘텐츠 1,451건·정답 901건 유지.
- SQL 재검증: `supabase/tests/versioned_policy_state.sql`, `supabase/tests/direct_table_access.sql`.

## DB 진단의 잔여 항목

이번 수정으로 공개 역할의 SECURITY DEFINER 실행 경고와 중복 허용 정책·행별 인증 함수 재계산 경고가 사라졌다.

- 유출 비밀번호 차단 기능은 아직 비활성 상태라는 Auth 경고가 남는다. 계정/요금제 설정을 확인할 운영 항목이다. [공식 설정 안내](https://supabase.com/docs/guides/auth/password-security#password-strength-and-leaked-password-protection)
- 정책 없는 RLS 표 28개는 서버 전용 구조에 해당한다. 공개·로그인 역할의 표 접근 권한을 제거한 상태로 유지한다. [진단 설명](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy)
- 미사용 인덱스 6개는 관측 기간과 실제 트래픽을 검토하기 전까지 유지한다. [진단 설명](https://supabase.com/docs/guides/database/database-linter?lint=0005_unused_index)

## 배포와 남은 운영 작업

머지 전 Python 3.8/3.12 CI를 통과해야 한다. main 머지 뒤 기존 Fly Deploy workflow의 테스트·배포·`/config` 사후 검증을 확인한다. 새 CAS RPC가 DB에 먼저 설치되어 있어야 한다.

실제 등록표, 평가 전 합의한 표본·목표·허용 오차, 담당자 확인, 이전 정책과 롤백 증빙, 외부 발행 연결은 별도 확인 대상이다. reports 이력 JSON의 실제 크기·경합률과 보관 기간도 실제 인입 규모에서 측정해야 한다. 합성 회귀 테스트 결과를 실제 모델 품질이나 운영 성능으로 간주하지 않는다.
