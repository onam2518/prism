# 전체 코드 검사와 런타임 보완 · 2026-09-29

## 범위와 검증

Python 모듈 68개에 문법·정적 검사를 적용하고, 자체 JavaScript 19개에 구문 검사를 적용했다. 전체 unittest로 HTTP·인증·저장·추출·검수·학습·평가·토픽 경로를 확인했다. 저장 오류와 팀 경계, 평가 경계값, 토픽 계산을 추가로 추적하고 회귀 테스트를 보강했다. 모든 분기의 무결함을 보증하는 검사는 아니다.

전체 테스트 2,248건 통과. 20건은 라이브 연동 13건과 로컬 PostgreSQL 실행 파일이 필요한 7건이다. 운영 PostgreSQL에서는 service_role로 롤백되는 시험 트랜잭션을 실행해 아래 저장 계약을 확인했다. 기존 운영 데이터는 시험 전후 유지되고 시험용 팀은 남지 않았다.

## 수정한 오류

- 서로 다른 한 글자 요약 또는 한 글자 정답과 빈 산출을 100% 일치로 채점하던 오류를 수정했다. 엔티티 부분 일치가 정확 일치를 먼저 소비하던 비결정적 채점도 수정했다. 잘못된 엔티티 타입은 평가 전체를 중단시키지 않는다.
- 운영 콘텐츠 조회 실패 시 전역 메모리 결과를 다른 팀 응답으로 내보내지 않고 조회 오류를 반환한다. 콘텐츠 등록 전 기존 여부·보존값 조회 실패도 쓰기를 중단한다. 수동 수정은 기존 메타를 읽지 못하면 저장하지 않는다.
- MCP 키는 발급자의 현재 팀이 키에 고정된 팀과 일치할 때만 인증한다. 소속 조회 실패·탈퇴·이동은 인증을 거절한다.
- Supabase 쓰기는 응답 유실 때 자동 재전송하지 않는다. 새 ID를 생성하는 POST와 RPC는 서버에서 이미 커밋됐는지 알 수 없기 때문이다. 읽기 GET/HEAD의 유휴 연결 재시도는 유지한다.
- 집계 RPC의 일시 장애가 재시작 전까지 전체 행 다운로드를 강제하던 문제를 수정했다. 미존재(404)만 스토어별로 5분 기억하며 이후 다시 확인한다.
- 콘텐츠 저장 RPC가 행 잠금 아래에서 팀을 확인한다. 다른 팀의 동일 hash가 있으면 배치 전체를 거절한다. 같은 팀의 최신 운영 플래그·최초 출처·수동 확정 메타·원천값을 보존한다.
- 정답 교체·병합은 PostgreSQL에서 팀별 잠금과 한 트랜잭션으로 처리한다. 실패/빈 입력은 기존 정답을 지우지 않고 변경 전 정답은 이력에 남는다. SQLite도 교체 도중 실패하면 삭제와 삽입을 함께 롤백한다.
- 직접 콘텐츠 등록에서도 이미지 정보 미제공/null/0과 원천키 제공 여부를 보존한다.

콘텐츠 저장·팀 인증은 기존 초안 PR #526·#539의 구현과 테스트를 검토해 현재 메타 계약에 맞게 통합했다. #529의 재전송 문제도 검토했으며, 이 변경에서는 upsert를 포함한 모든 쓰기의 자동 재전송을 차단한다. 초안 PR 자체는 머지하거나 닫지 않았다.

## 성능 측정

같은 로컬 환경에서 합성 콘텐츠 1,500건(각 본문 600회 반복, 엔티티 2개)으로 5회 측정한 중앙값이다. 서버의 임시 JSONL 파일 저장·재읽기를 제거하고 반복되는 일반 엔티티 판정을 4,096항목 상한 캐시로 재사용했다. 서비스명 판정은 요청별로 유지한다.

| 경로 | 변경 전 | 변경 후 |
|---|---:|---:|
| 토픽 계산 중앙값 | 85.81ms | 31.86ms |
| 전체 결과 SHA-256 | 동일 | 동일 |

약 62.9% 감소했다. 운영 네트워크 지연이나 LLM 호출 시간의 개선율을 뜻하지 않는다. 재현: `python3 scripts/benchmark_topics.py`; 이전 체크아웃과 비교: `python3 scripts/benchmark_topics.py --repo <이전 체크아웃> --from-file`.

정답 병합도 행마다 DELETE/POST를 수행하던 경로를 전체 배치 RPC 1회로 바꿨다. API 왕복이 행 수에 비례해 늘지 않는다.

## 운영 반영과 남은 범위

앱 배포 전에 `supabase/migrations/20260929101722_atomic_content_and_golden_writes.sql`을 적용한다. migration 자체는 기존 콘텐츠·정답을 재작성하지 않는다. 새 함수는 SECURITY INVOKER, 고정 search_path와 service_role 실행 권한을 사용한다. PUBLIC/anon/authenticated는 실행할 수 없다. 함수 미설치·실패 시 직접 REST 쓰기로 우회하지 않는다.

롤백 시험에서 팀 간 충돌의 배치 전체 롤백, 최신 운영 플래그·수동값·원천값 보존, 잘못된/빈 정답 교체의 기존값 보존, 정상 교체·중복 제거·이력 보존을 검증했다. 다중 프로세스 동시 실행 부하 시험은 수행하지 않았다.

DNM 발행 전체 전환의 남은 조건은 [공통 메타 전환 문서](POLICY_CONTRACT_ROLLOUT.md)에 별도로 유지한다. 원천 레지스트리 없이 대상 여부를 추정하거나 과거 정답을 일괄 재확정하지 않는다. 원천 식별자를 기준으로 한 발행 revision·지연 응답 선택, 실행 전체 설정 고정, 토픽 미리보기/되돌리기 버전 검사는 이번 런타임 보완의 완료 항목에 포함하지 않는다.


## DB 후속 검사

`20260929102621_harden_aggregate_paths_and_foreign_keys.sql`로 정답 이력·배정·MCP 키·팀의 외래키 인덱스 4개를 추가하고 기존 집계 함수 3개의 검색 경로를 고정했다. 변경 전후 집계 결과 지문이 일치했다. Supabase의 외래키 인덱스 누락 4건과 변경 가능한 검색 경로 3건 권고는 해소됐다. 신규 인덱스의 미사용 INFO는 사용량이 쌓이기 전의 관찰값이므로 삭제 근거로 삼지 않았다.

기존 권고는 남아 있다: [RLS의 행별 인증 함수 평가](https://supabase.com/docs/guides/database/database-linter?lint=0003_auth_rls_initplan) 2건, [중복 허용 정책](https://supabase.com/docs/guides/database/database-linter?lint=0006_multiple_permissive_policies) 10건, prism_my_team의 [익명 실행](https://supabase.com/docs/guides/database/database-linter?lint=0028_anon_security_definer_function_executable)·[인증 사용자 실행](https://supabase.com/docs/guides/database/database-linter?lint=0029_authenticated_security_definer_function_executable) 권한, [유출 비밀번호 차단 비활성](https://supabase.com/docs/guides/auth/password-security#password-strength-and-leaked-password-protection). 기존 사용자 접근 정책과 인증 설정은 이번 변경에서 재정의하지 않았다.
