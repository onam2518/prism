# Prism 코드 감사 · 2026-08-13 (검증 완료 2026-08-18)

- **기준 커밋**: `0d85cbc` (감사 실행 시점 main) · 라인 번호는 이 스냅샷 기준
- **현재 HEAD**: `600d0f7` · 기준 이후 2커밋(PR #452 · FAB/푸터 UI) 머지됨 · UI 조각 관련 라인은 소폭 이동 가능
- **범위**: 전체 코드(백엔드 파이썬 · vendor JS · ui 조각 · 문서) 버그·성능·정리 3축
- **방법**: Find 15방향 병렬 탐색 → 근사 중복 제거 → 발견 건별 반박 검증(safe/careful 등급) → 과제 그룹핑. 1차 실행이 사용량 한도에 걸려 저널 복구 후 126건 전량 재검증으로 완료.
- **제외(재보고 안 함)**: CODE_AUDIT_2026-08-04 careful 백로그 9건 · PERF_BACKLOG_2026-07-28 후속 2건 · 의도된 계약(raw 슬림·ETag·30s 캐시·집계 RPC·골드 블라인딩 설계·의존성 0)

**검증 결과**: 검증 126 → 확정 117 · 불확실 4 · 반박 5. 확정을 **50개 과제**로 묶음.

| 우선순위 | 과제 수 | 뜻 |
|---|---|---|
| P0 | 2 | 운영 데이터 훼손·보안 · 즉시 |
| P1 | 8 | 사용자 체감 버그·핫패스 성능 |
| P2 | 28 | 저빈도 버그·중간 성능 |
| P3 | 12 | 정리(죽은 코드·주석·문서) |

> grade: **safe**=국소 수정 · **careful**=계약·동시성·정책에 닿아 신중. effort: S/M/L.

## 착수 순서·의존 (종합 노트)

착수 순서. P0 두 건(과제 1 가입 제한·과제 2 교차 팀 PK)을 먼저. 과제 2 는 effort L(복합키 마이그레이션)이라 가장 먼저 손대고, add_contents 의 existing_hashes 팀 필터 의미를 바꾸므로 과제 11(빈결과 가드)·27(자동 인입 스킵)·39(계약 테스트)가 여기에 의존한다. 계약 테스트(과제 39)를 과제 2·11·27 의 회귀 가드로 앞이나 함께 머지하는 것을 권한다.

허브 파일 충돌. serve.py 를 건드리는 과제가 다수(과제 6·7·8·26·29·36·41). 과제 26(_agg_bump 도메인 분리)을 먼저 머지해야 캐시 재사용 과제 29·30·33 이 도메인 키에 등록할 수 있으니 26 을 선행으로 둔다. 같은 화면 조각·모듈 동시 편집은 CLAUDE.md 규칙대로 스태거링.

검수자 식별. 과제 3(_is_me/uid 규약 확립)을 먼저, 그 뒤 과제 23(스토어 이관·questbot 정규화)이 같은 규약을 따른다. 골드 위장 과제 4 는 검수 보조 독립성·측정 설계에 민감하니 정책 확인 후 반영.

학습 모듈. learnops.py 를 여러 과제(9·32·33·46 주석)가 건드린다. 과제 9(팀 스코프·영속 정합)를 먼저, perf 과제 32·33 은 그 뒤. 코드 주석 과제 46 의 learnops 문구도 9 뒤에.

스펙트럼. 과제 45(mcprpc 이관)가 과제 24 의 ping·형오류·사유열거를 근본 해소한다. 용량이 되면 45 를 먼저 해 24 범위를 축소, 아니면 24(즉시 견고화)를 먼저 하고 45 를 후속으로. 사이드카 키 소실·키 상한 삭제는 어느 경로든 과제 24 에 남는다.

같은 파일 순서 조정. 20-ingest-policy.html: 과제 25(기능 버그) 먼저, 과제 44(인라인 스타일·주석 정리) 나중. ARCHITECTURE.md: 과제 47(문서 갱신)과 과제 41(죽은 라우트 삭제 시 ARCH 행 제거)이 같은 파일이라 41 의 ARCH 편집을 47 로 합치거나 순서를 맞춘다. runops.py: 과제 40(죽은 코드)은 과제 12(원장) 뒤에.

정리 과제(40~50)는 대체로 마지막 배치. 문서 3묶음(47·48·49)과 자산 삭제(50)는 서로 다른 파일이라 병렬 안전. 과제 41 의 /crew-escalate·과제 40 의 crewops 조건은 운영 수동 호출 여부 확인 후 삭제(careful).

## P0 과제

### [P0] 공개 셀프 가입 관리자 권한 상승 차단

- 성격 `bug` · 등급 **careful** · 규모 M · 브랜치 `fix/signup-admin-guard`
- 공개 셀프 가입 + 이메일 자동확인으로 미등록 관리자 이메일을 선점해 운영관리자 권한을 얻는 경로를 막는다. 화이트리스트·초대 기반 가입 또는 관리자 계정 사전 프로비저닝(uid 바인딩)로 전환하고 email_confirm 자동확정을 실제 메일 검증으로 바꾼다.
- 근거 발견 1건:
  - **공개 셀프 가입 + 이메일 자동확인 → 미등록 관리자 이메일 선점으로 운영관리자 권한 상승** · `prism/adminops.py:82` · bug/high

### [P0] 교차 팀 contents upsert 데이터 탈취 차단

- 성격 `bug` · 등급 **careful** · 규모 L · 브랜치 `fix/contents-team-pk`
- contents 해시 전역 PK 충돌 시 타 팀 행을 통째로 탈취·초기화하는 upsert 를 막는다. 근본은 (hash, team_id) 복합키 마이그레이션, 단기는 sync_contents upsert 의 team_id 승계 + add_contents existing_hashes 전역화. effort L 이라 가장 먼저 착수.
- 근거 발견 1건:
  - **contents 해시 전역 PK 충돌 시 타 팀 행을 통째로 탈취·초기화하는 교차 팀 upsert** · `prism/supastore.py:1256` · bug/medium

## P1 과제

### [P1] 검수 보조 판정 축 uid 정규화

- 성격 `bug` · 등급 **careful** · 규모 M · 브랜치 `fix/reviewer-dissent-uid`
- supabase 에서 본인 판정 제외가 이름 vs uid 불일치로 동작하지 않아 골드 빈결과가 골드 표시로 새는 것, _elem_tally 가 판정·교정 축을 섞어 과대 집계하는 것, 동수 경계값 문구 오류를 uid(reviewer_id 우선) 규약으로 함께 정규화한다.
- 근거 발견 3건:
  - **reviewer_dissent 본인 판정 제외가 supabase 모드에서 동작 안 함(이름 vs uid 불일치) · 골드 빈 결과가 곧 골드 표시가 됨** · `prism/reviewassist.py:689` · bug/high
  - **_elem_tally 가 판정 축(표시명)과 교정 축(uid)을 한 집합에 섞어 supabase 에서 '지적한 요소 N명' 과대 집계** · `prism/reviewassist.py:612` · bug/medium
  - **reviewer_dissent 셋째 줄이 동수(good==bad)에서 '소수 의견 N명' 으로 표기 · 경계값에서 문구가 거짓** · `prism/reviewassist.py:703` · bug/low

### [P1] 골드 문항 위장·채점 정합

- 성격 `bug` · 등급 **careful** · 규모 L · 브랜치 `fix/gold-camouflage-scoring`
- 골드 문항이 목록 맨 위 고정·응답 모양 차이·빈 비교 버튼으로 판정 전에 식별되는 문제와 뒤집기 골드의 '고쳐서 편입' 오채점을 함께 고친다. 검수 보조 독립성·측정 설계에 민감하므로 정책 확인 후 반영.
- 근거 발견 4건:
  - **검수 표(/raw)의 골드 문항이 항상 목록 맨 위(index 0)에 고정 삽입 · 위치 자체가 골드 표시** · `prism/reviewops.py:969` · bug/medium
  - **최종검수 큐의 골드 문항(goldf:)에서 '비교' 버튼이 빈 결과 · 판정 전에 골드를 식별할 수 있음** · `prism/reviewops.py:1107` · bug/low
  - **_inject_gold_final 골드 행이 실제 행과 응답 모양이 다름(entities_scored·images·final_by/final_ts·fb 하위키 누락) · 상세 화면에서 확신도 배지가 골드에서만 사라짐** · `prism/reviewops.py:258` · bug/medium
  - **최종검수 골드(뒤집기 문항)에서 '고쳐서 편입'(결함을 정확히 잡은 정상 행동)이 오답 처리됨** · `prism/reviewops.py:238` · bug/medium

### [P1] 행동 로그 숫자 파싱 예외로 usermeta 고착

- 성격 `bug` · 등급 **safe** · 규모 S · 브랜치 `fix/usermeta-numeric-parse`
- 빈 셀·소수점 dwell_sec 하나로 int() 예외가 나고 저장이 선행되어 사용자 메타 모듈이 계속 오류 상태로 고착된다. 관대한 숫자 변환 헬퍼(int(float(x or 0)))를 적용하거나 저장 전 숫자 컬럼을 정규화하고 불량 행 수를 보고한다.
- 근거 발견 1건:
  - **행동 로그의 빈 셀·소수점 dwell_sec 하나로 int() 예외 · 저장이 선행되어 사용자 메타 모듈이 계속 오류 상태로 고착** · `prism/usermeta.py:326` · bug/medium

### [P1] 디스패처 게이트 예외로 무응답 종료

- 성격 `bug` · 등급 **careful** · 규모 S · 브랜치 `fix/dispatch-gate-guard`
- 인증·메뉴 게이트가 do_GET/do_POST 의 try 밖에서 실행되어 스토어 일시 오류 시 무응답으로 연결이 끊긴다. 게이트 블록을 try 로 감싸 예외 시 500/503 으로 응답한다. 게이트 판정 자체는 불변이라 몽키패치 계약에 영향 없음.
- 근거 발견 1건:
  - **인증·메뉴 게이트가 디스패처 try 밖에서 실행되어 스토어 일시 오류 시 무응답 연결 종료** · `prism/serve.py:3138` · bug/medium

### [P1] /config 권한 스코프 강화·왕복 절감

- 성격 `mixed` · 등급 **careful** · 규모 M · 브랜치 `fix/config-authz-scope`
- 팀 미소속 인증 계정에 /config 전체(프롬프트·인입 소스)가 노출되고 비운영 팀 관리자가 전역 config 를 수정하는 문제를 역할별 응답 분할·쓰기 게이트 승격으로 막고, 인증 /config 의 원격 3왕복·미사용 storedCount 를 캐시·생략으로 줄인다. 과제 1(가입 제한)과 병행.
- 근거 발견 4건:
  - **팀 미소속 인증 계정에 /config 전체 응답 노출(무인증만 슬림 처리)** · `prism/serve.py:1487` · bug/medium
  - **인증 /config 가 매 요청 원격 3왕복(count·team_links·target_models) · storedCount 는 supabase UI 미사용** · `prism/serve.py:1108` · perf/low
  - **/config 가 시스템/단계/모델 프롬프트·인입 소스 엔드포인트를 모든 로그인 계정(무팀·셀프가입 포함)에 공개** · `prism/serve.py:1481` · bug/low
  - **비운영(팀) 관리자가 전역 config.json(모델·프롬프트·인입 소스)을 수정해 전 팀에 영향** · `prism/serve.py:2001` · bug/low

### [P1] _agg_bump 도메인 분리로 캐시 무력화 해소

- 성격 `perf` · 등급 **careful** · 규모 M · 브랜치 `perf/agg-bump-domain`
- 전역 단일 버전 _agg_bump 로 판정 1건마다 콘텐츠 전량 캐시까지 무효화되어 활발한 검수 중 캐시가 무력화된다. 버전 카운터를 도메인별(contents/feedback/golden/config)로 분리하고 캐시 키에 도메인을 등록한다. serve._agg_bump 몽키패치 계약 확인 필요. 캐시 재사용 과제(29·30·33)의 선행.
- 근거 발견 1건:
  - **전역 단일 버전 _agg_bump: 판정 1건마다 콘텐츠 전량 캐시까지 전부 무효화되어 활발한 검수 중 캐시가 무력화** · `prism/serve.py:352` · perf/medium

### [P1] 자동 인입 재추출 스킵·조기 중단

- 성격 `perf` · 등급 **careful** · 규모 M · 브랜치 `perf/auto-ingest-skip-break`
- 자동 인입이 매 폴링마다 이미 적재된 콘텐츠를 전량 재추출(LLM 재과금)하고, 엑셀·자동 인입 루프에 비재시도성(402·한도·인증) 조기 중단과 실패 계수가 없다. existing_hashes 로 실행 완료 건을 걸러 신규+미실행만 추출하고, _nonretry_kinds 감지 시 break + 실패 계수를 적용한다. 과제 2 의 existing_hashes 변경에 의존.
- 근거 발견 2건:
  - **자동 인입이 매 폴링마다 이미 적재된 콘텐츠를 전량 재추출(LLM 재과금 · existing_hashes 스킵 부재)** · `prism/ingestops.py:297` · perf/medium
  - **엑셀 일괄 추출·자동 인입 루프에 비재시도성(402 크레딧·한도·인증) 조기 중단이 없고 run_batch 는 실패 계수도 없음** · `prism/runops.py:611` · perf/medium

### [P1] 목록 응답 슬림화(/drill·/final-queue)

- 성격 `perf` · 등급 **careful** · 규모 M · 브랜치 `perf/list-slim`
- /drill 이 매칭 전 행에 body 전문 + entities_scored 를 실어 수십 MB JSON 을 내리고, /final-queue 도 200행에 같은 비용을 지불한다. /raw 슬림 관례를 적용해 표시 필드·상한만 내리고 본문은 /raw-detail 재사용, rerun_unconfirmed 용 (hash,사유) 헬퍼를 분리한다. 드릴·큐 UI(app 조각) 동반 수정.
- 근거 발견 2건:
  - **/drill 응답 무상한: 매칭 전 행에 body 전문 + entities_scored 를 실어 수십 MB JSON·행당 CPU 재계산** · `prism/dashops.py:346` · perf/medium
  - **/final-queue 가 200행에 body 전문 + entities_scored 를 싣고, rerun_unconfirmed 는 해시만 필요한데 같은 비용을 지불** · `prism/reviewops.py:197` · perf/low

## P2 과제

### [P2] 서버 팀 필터 누락 교차 노출

- 성격 `bug` · 등급 **careful** · 규모 M · 브랜치 `fix/team-filter-leak`
- results_rows 의 _LAST_RESULTS 폴백이 스토어 오류 시 팀 필터 없이 타 팀 결과를 반환하고, /ingest-status 가 팀 무필터 전 잡을 미소속 계정에도 노출한다. 원격 스토어면 폴백을 끄고, 잡을 team 태깅해 요청 팀 것만 반환한다.
- 근거 발견 2건:
  - **results_rows 의 _LAST_RESULTS 폴백이 supabase 오류 시 팀 필터 없이 타 팀 결과를 반환** · `prism/serve.py:321` · bug/low
  - **/ingest-status 가 팀 무필터 전 잡 노출 + _TEAMLESS_OK_GET 포함으로 미소속 계정에도 공개** · `prism/serve.py:1431` · bug/low

### [P2] 학습 팀 스코프·컴파일 영속 정합

- 성격 `bug` · 등급 **careful** · 규모 M · 브랜치 `fix/learn-team-scope`
- 일배치 컴파일·원복 프롬프트가 snapshot 경유 sync_learned 로 raw 라인에 덮이고, sync_learned 는 팀 무구분으로 LEARNED 를 만들며, 전역 폴백이 타 팀 평가 상세를 노출한다. 컴파일 결과 영속 우선 로드 + LEARNED 팀 키 계층화 + 전역 폴백 팀 분리로 함께 정리한다.
- 근거 발견 3건:
  - **학습 일배치의 컴파일·원복 프롬프트가 snapshot 경유 sync_learned 로 즉시 raw 라인으로 덮임** · `prism/learnops.py:449` · bug/medium
  - **팀 무구분 전역 폴백(_LAST_EVAL_DETAIL·_LAST_LEARN_REPORT)이 다른 팀 평가 상세를 노출** · `prism/learnops.py:202` · bug/low
  - **sync_learned 는 팀 무구분(전 팀 혼합)으로 LEARNED 를 만들고 meta_compile 은 한 팀 결과를 전역 적용** · `prism/learnops.py:32` · bug/low

### [P2] 토픽 스튜디오 설정 팀 스코프화

- 성격 `bug` · 등급 **careful** · 규모 M · 브랜치 `fix/topic-studio-team-scope`
- 토픽 스튜디오 설정이 전역 버킷(team_key="")이라 supabase 다중팀에서 팀 간 유출·간섭이 난다. _studio_config/_save_studio_config 에 team 인자를 넣어 get_report/save_report 를 팀 스코프화하고 기존 전역 데이터는 1회 이관한다.
- 근거 발견 1건:
  - **토픽 스튜디오 설정이 전역 버킷(team_key="")이라 supabase 다중팀에서 팀 간 유출·간섭** · `prism/topicops.py:29` · bug/medium

### [P2] 저장 쓰기 경로 상태 승계·정합

- 성격 `bug` · 등급 **careful** · 규모 M · 브랜치 `fix/store-write-integrity`
- supabase save_dedup 의 빈결과 가드 부재로 재추가가 실행 결과를 미실행으로 되돌리고, set_purpose 가 청크 없이 단일 URL 로 조용히 실패하며, sqlite save_result 가 ops_hold/source_status 플래그 승계 없이 payload 를 통째 교체한다. 세 쓰기 경로의 상태 승계·청크·빈결과 가드를 맞춘다. 과제 2 의 existing_hashes 변경에 의존.
- 근거 발견 3건:
  - **save_dedup 빈 결과 가드가 supabase 쪽에 없음 · 재추가가 실행 결과를 미실행으로 되돌림** · `prism/supastore.py:2260` · bug/medium
  - **set_purpose 가 in.() 청크 없이 단일 URL 로 전송 · 대량 평가용 지정이 조용히 실패** · `prism/supastore.py:2219` · bug/medium
  - **save_result(CLI 단건)가 운영자 플래그 승계 없이 payload 통째 교체 · ops_hold/source_status 유실** · `prism/store.py:457` · bug/low

### [P2] 실행 원장 비용·실패 누락 보정

- 성격 `bug` · 등급 **careful** · 규모 M · 브랜치 `fix/run-ledger-completeness`
- 폴백 체인이 버린 실패 시도의 비용·실패가 원장과 예산 상한에서 누락되고, 비전 호출 지출이 0 으로 잡히며 비전 실패가 실패 원장에 안 남는다. 버린 시도 trace 누적 + imagext usage 반환·병합으로 원장을 완결한다.
- 근거 발견 2건:
  - **폴백 체인이 버린 실패 시도의 비용·실패가 원장(비용·실패 롤업)과 예산 상한에서 통째로 누락** · `prism/runops.py:285` · bug/medium
  - **비전 호출(imagext) 지출이 비용 원장에 0 으로 잡히고 비전 실패는 실패 원장에 전혀 안 남음** · `prism/runops.py:229` · bug/low

### [P2] 런 비교 유해 미탐 기준 혼용

- 성격 `bug` · 등급 **careful** · 규모 S · 브랜치 `fix/eval-harm-basis`
- eval_run_compare 가 구·신 유해 미탐률 정의(all_rows vs expected_r)를 섞어 비교해 regressed 를 오판한다. 두 리포트의 harm_miss_basis 가 다르면 유해 축 비교를 건너뛰고 사유를 표기하거나 공통 정의로 정규화한 뒤 비교한다.
- 근거 발견 1건:
  - **런 비교가 구·신 유해 미탐률 정의(all_rows vs expected_r)를 섞어 비교해 regressed 오판** · `prism/evalops.py:252` · bug/low

### [P2] 판정 시뮬레이터 노출-클릭 오탐

- 성격 `bug` · 등급 **safe** · 규모 S · 브랜치 `fix/pastcheck-viewimp`
- 판정 시뮬레이터의 노출-클릭 연결 검사가 실 로그의 중첩 viewimp 배열을 못 읽어 전 클릭을 link_miss 로 오탐한다. _flatten 이 dict 리스트를 펼치거나 리스트형 viewimp_contents 에서 id 를 추출하는 분기를 추가하고 회귀 테스트를 넣는다.
- 근거 발견 1건:
  - **판정 시뮬레이터의 노출-클릭 연결 검사가 실 로그의 중첩 viewimp 배열을 못 읽어 전 클릭을 link_miss 오탐** · `prism/pastcheck.py:307` · bug/low

### [P2] 잡 ID 초 단위 충돌

- 성격 `bug` · 등급 **safe** · 규모 S · 브랜치 `fix/job-id-collision`
- 잡 ID 가 초 단위 시각(HHMMSS)이라 동시 실행 시 충돌해 실행 큐 진행률·이력이 섞인다. jid 에 단조 카운터나 uuid4 hex 앞 6자를 덧붙이고 기존 접두 규약(add:/rerun:/batch:)은 유지한다.
- 근거 발견 1건:
  - **잡 ID 가 초 단위 시각(HHMMSS)이라 동시 실행 시 충돌 · 실행 큐 진행률·이력이 서로 섞임** · `prism/runops.py:376` · bug/low

### [P2] 통합 1콜 폴백 인텐트 정의문 누락

- 성격 `bug` · 등급 **careful** · 규모 S · 브랜치 `fix/prompt-intent-fallback`
- 통합 1콜 폴백(item_system)에 인텐트 정의문이 미주입되어 v17/v18 개선이 분리형 4호출에만 반영된다. item_system 의 intents 를 MP.intent_dictionary_text(displayServiceName)로 교체해 분리형과 같은 정의문 병기 사전을 쓰게 한다.
- 근거 발견 1건:
  - **통합 1콜 폴백(item_system)에 인텐트 정의문 미주입 — v17/v18 개선이 분리형 4호출에만 반영됨** · `prism/prompts.py:135` · bug/low

### [P2] 위키데이터 서킷 브레이커 단일 실행 가드

- 성격 `bug` · 등급 **careful** · 규모 M · 브랜치 `fix/wikidata-breaker-guard`
- 위키데이터 서킷 브레이커가 동시 배치에 의해 중간 리셋되고 보강 러너 2벌 사이 단일 실행 가드가 없다. 보강을 dictops._enrich_start 경유로 일원화하거나 _wd_breaker_reset 을 활성 배치 없을 때만 수행하도록 소유권을 만든다.
- 근거 발견 1건:
  - **위키데이터 서킷 브레이커가 동시 배치에 의해 중간 리셋 · 보강 러너 2벌 사이 단일 실행 가드 부재** · `prism/entdict.py:738` · bug/low

### [P2] plan_distribute 최종검수자 무통보 제외

- 성격 `bug` · 등급 **safe** · 규모 S · 브랜치 `fix/plan-distribute-final`
- plan_distribute 에서 지정 검수자 중 최종검수자가 말없이 제외되고 blocked 에도 안 잡힌다. is_final 로 걸러진 인원을 blocked 또는 excluded_final 필드로 응답에 명시한다.
- 근거 발견 1건:
  - **plan_distribute: 지정 검수자 중 최종검수자는 말없이 제외되고 blocked 에도 안 잡힌다** · `prism/crewops.py:787` · bug/low

### [P2] lookup_entity 조용한 절단

- 성격 `bug` · 등급 **careful** · 규모 S · 브랜치 `fix/lookup-entity-truncated`
- lookup_entity 가 limit 요청에서 DB 에 더 있어도 truncated=False 를 돌려준다. ent_list 를 LIMIT+1 건으로 조회해 초과 여부로 truncated 를 세우고 total 을 '이상' 의미로 응답한다. 한 줄 수정 + 회귀 테스트.
- 근거 발견 1건:
  - **lookup_entity 가 limit=50 요청에서 DB 에 더 있어도 truncated=False 를 돌려준다(조용한 절단)** · `prism/prismtools.py:155` · bug/low

### [P2] 스토어 락 없는 갱신 원자성 보강

- 성격 `bug` · 등급 **careful** · 규모 M · 브랜치 `fix/store-atomicity`
- quality_meta/item_meta 계열이 락 없는 read-modify-write 로 동시 갱신을 유실하고, mcpkeys issue 의 사용자당 5개 상한 검사가 원자적이지 않아 동시 발급으로 넘길 수 있다. 단일 프로세스 전제의 모듈 락으로 검사~쓰기 구간을 직렬화한다.
- 근거 발견 2건:
  - **issue 의 사용자당 5개 상한 검사가 원자적이지 않아 동시 발급으로 상한을 넘길 수 있다** · `prism/mcpkeys.py:146` · bug/low
  - **quality_meta·item_meta 계열이 락 없는 read-modify-write · 동시 요청 간 갱신 유실** · `prism/supastore.py:1380` · bug/low

### [P2] 삭제 경로 반환값·연쇄 정리 정합

- 성격 `bug` · 등급 **careful** · 규모 M · 브랜치 `fix/delete-path-integrity`
- remove_content 등 삭제·갱신 계열이 매칭 여부와 무관하게 항상 True 를 반환(rowcount 계약 위반)하고, 콘텐츠 삭제·retention 이 content_entities 링크를 정리하지 않아 고아 링크가 누적된다. return=representation 으로 bool 을 결정하고 연쇄 삭제에 content_entities 를 추가한다.
- 근거 발견 2건:
  - **remove_content 등 삭제·갱신 계열이 매칭 여부와 무관하게 항상 True 반환(sqlite rowcount 계약 위반)** · `prism/supastore.py:516` · bug/low
  - **콘텐츠 삭제·retention 이 content_entities 링크를 정리하지 않아 고아 링크 무한 누적** · `prism/supastore.py:1749` · bug/low

### [P2] 조회 창 절단으로 대기 건 증발

- 성격 `bug` · 등급 **careful** · 규모 M · 브랜치 `fix/queue-window-truncation`
- supabase routes_by_stage 가 최근 80행 창에서만 단계 지시를 찾아 sqlite 와 학습 보정이 갈리고, sqlite review_queue 가 최신 limit*6 행 창에서만 YELLOW 를 찾아 대기 건이 큐에서 증발한다. 단계별 분할 조회·목표 수를 채우는 커서 루프로 교체한다.
- 근거 발견 2건:
  - **routes_by_stage 최근 80행 창 · 특정 단계 지시가 창 밖으로 밀려 sqlite 와 학습 보정이 갈림** · `prism/supastore.py:979` · bug/low
  - **sqlite review_queue 가 최신 limit*6 행 창에서만 YELLOW 를 찾아 대기 건이 큐에서 증발** · `prism/store.py:2001` · bug/low

### [P2] 검수자 식별 스토어 계약 정합

- 성격 `bug` · 등급 **careful** · 규모 M · 브랜치 `fix/reviewer-identity-sync`
- questbot 이 reviewers_map 백엔드 계약 갈림을 정규화 없이 써 sqlite 에서 크래시하고, rename_reviewer 가 final_verdicts 원장의 by=이름 을 이관하지 않아 최종판정 점수가 옛 이름에 잔류한다. reviewers_map 정규화 적용 + 이름 이관을 함께 처리한다. 과제 3 의 uid 규약 뒤에 착수.
- 근거 발견 2건:
  - **questbot 이 reviewers_map 백엔드 계약 갈림을 정규화 없이 사용 · sqlite 에서 즉시 크래시** · `prism/questbot.py:160` · bug/low
  - **rename_reviewer 가 final_verdicts 원장(reports)의 by=이름 을 이관하지 않아 최종판정 점수가 옛 이름에 잔류** · `prism/store.py:1331` · bug/low

### [P2] 스펙트럼 공개 관문 견고화

- 성격 `bug` · 등급 **careful** · 규모 M · 브랜치 `fix/spectrum-gw-harden`
- /spectrum-gw 공개 관문의 ping -32601 오류, 형 오류 본문의 500(대신 401/400), 인증 실패 사유 3종 열거, KEYS_CAP 초과 시 살아있는 키 삭제, 사이드카 _load 가 I/O 오류를 빈 상태로 삼켜 키 소실되는 문제를 함께 잡는다. C20·C64 의 데이터 소실은 과제 45(mcprpc 이관)와 무관하게 별도로 남는다.
- 근거 발견 5건:
  - **/spectrum-gw MCP 갈래가 ping 메서드를 -32601 오류로 돌려주고 실패 기록까지 남긴다** · `prism/spectrumops.py:883` · bug/low
  - **사이드카 _load 가 읽기 실패를 빈 상태로 삼켜, 일시 I/O 오류 + 쓰기 1회면 발급 키·사용 기록 전량이 영구 소실된다** · `prism/spectrumops.py:325` · bug/low
  - **공개 관문이 형 오류 본문(key·tool·method 가 문자열이 아님)에 401/400 대신 500 을 낸다** · `prism/spectrumops.py:849` · bug/low
  - **공개 관문의 인증 실패 사유 3종(invalid/expired/revoked)이 구분 응답되어 키 상태 열거가 가능하다** · `prism/spectrumops.py:455` · bug/low
  - **KEYS_CAP(200) 초과 시 살아 있는 키도 가장 오래된 것부터 조용히 삭제된다** · `prism/spectrumops.py:415` · bug/low

### [P2] 프론트엔드 UI 규약 위반 수정

- 성격 `bug` · 등급 **safe** · 규모 S · 브랜치 `fix/ui-convention`
- 정책 팔레트(polpal)의 x-show + 문자열 x-bind:style 조합(19e에서 결함으로 문서화된 패턴)을 template x-if 로 옮기고, mkRevoke/mkDismiss 의 네이티브 window.confirm 을 dsConfirm 공통 규약으로 교체한다. C29 는 20-ingest-policy.html 을 건드려 과제 44 와 순서 조정 필요.
- 근거 발견 2건:
  - **정책 팔레트(polpal)에 x-show + 문자열 x-bind:style 조합 잔존 · 19e에서 결함으로 문서화되어 수정한 패턴** · `prism/ui/20-ingest-policy.html:173` · bug/low
  - **mkRevoke·mkDismiss가 네이티브 window.confirm 사용 · dsConfirm 공통 규약 위반** · `prism/vendor/app-16-mcpkeys.js:39` · bug/low

### [P2] 스토어 계약·핸들러 테스트 공백 보강

- 성격 `bug` · 등급 **safe** · 규모 M · 브랜치 `test/store-contract-gaps`
- SupabaseStore.origin_meta_for·existing_hashes·set_image_urls(운영 골드 출제·일괄 추출 스킵·이미지 백필 경로)와 POST /media-register 게이트 분기·비용 원장 n_billed 쓰기측이 무테스트다. _FakeRest·FakeH 계약 테스트로 sqlite/supabase 파리티·팀 필터·청크 경계를 단언한다. 과제 2·11·27 의 회귀 가드라 그 앞이나 함께 머지.
- 근거 발견 5건:
  - **[test-gap] POST /media-register HTTP 핸들러(관리자 게이트 분기)와 media 라우트의 content 메뉴 승격이 어떤 테스트에도 닿지 않는다** · `prism/serve.py:2780` · bug/low
  - **[test-gap] SupabaseStore.origin_meta_for 무테스트 · 여기가 깨지면 운영에서 골드 출제가 조용히 전면 중단된다** · `prism/supastore.py:237` · bug/low
  - **[test-gap] SupabaseStore.existing_hashes 무테스트 · 신규/기존 구분과 일괄 추출 스킵의 운영 경로** · `prism/supastore.py:222` · bug/low
  - **[test-gap] SupabaseStore.set_image_urls 무테스트 · 이미지 백필의 운영 쓰기 경로** · `prism/supastore.py:529` · bug/low
  - **[test-gap] 비용 원장 n_billed 기록(쓰기측)이 무테스트 · 읽기측 폴백에 가려 회귀가 침묵한다** · `prism/dashops.py:62` · bug/low

### [P2] 무캐시 feedback·purpose·assignees 조회 캐시 재사용

- 성격 `perf` · 등급 **careful** · 규모 M · 브랜치 `perf/feedback-cache-reuse`
- raw_rows·content_history·검수 보조 패널·/golden-status 가 30s 캐시를 우회해 요청마다 feedback 전량과 purpose_map·assignees·patch_log 400행을 재조회한다. feedback_map_cached·_agg_cached_store 로 30s 메모로 접는다. 과제 26(도메인 bump) 이후 착수해 도메인 키에 등록.
- 근거 발견 4건:
  - **raw_rows 가 요청마다 purpose_map(contents 전 행)·assignees(assignments 전 행)를 무캐시 전량 조회** · `prism/reviewops.py:904` · perf/low
  - **검수 보조가 serve 30s 캐시를 우회 · 패널 호출마다 feedback 전량 + patch_log 400행 재조회** · `prism/reviewassist.py:277` · perf/medium
  - **content_history 가 해시 1건 이력에 feedback 전량을 무캐시로 내려받음** · `prism/reviewops.py:1073` · perf/low
  - **/golden-status 가 feedback_map 캐시를 우회해 요청마다 feedback 전량을 재조회** · `prism/serve.py:1882` · perf/low

### [P2] 활동 집계 30일 창 쿼리·캐시

- 성격 `perf` · 등급 **careful** · 규모 M · 브랜치 `perf/activity-daily-window`
- activity_daily 가 feedback 전량 + gold_checks 2만행을 받아 파이썬에서 30일 필터한다. 쿼리에 ts=gte·created_at=gte 창 필터를 추가(start_ts 는 이미 함수 안)하고 dashops.activity_daily_data 를 _agg_cached 로 감싼다. 스토어 쿼리와 대시옵스 캐시 두 층을 함께.
- 근거 발견 2건:
  - **activity_daily 가 feedback·gold 전량을 받아 파이썬에서 30일 필터** · `prism/supastore.py:770` · perf/medium
  - **activity_daily_data 가 요청마다 feedback 전량+gold_checks 2만행을 받아 파이썬에서 30일 필터** · `prism/dashops.py:251` · perf/low

### [P2] 인입 배치 조회·스케줄러 효율화

- 성격 `perf` · 등급 **careful** · 규모 M · 브랜치 `perf/ingest-batch-schedule`
- backfill_urls 가 행당 PATCH 순차 호출(1,451건 기준 최대 2,900왕복)이고, 인입 스케줄러가 단일 스레드 직렬이라 느린 소스 하나가 다른 소스 폴링을 무기한 지연시킨다. 500행 청크 부분 컬럼 upsert(3~6왕복) + due 소스 daemon 스레드 분리로 해소한다.
- 근거 발견 2건:
  - **backfill_urls 가 행당 PATCH 순차 호출 · 실측 시나리오(1,451건 백필) 기준 최대 2,900왕복** · `prism/ingestops.py:108` · perf/low
  - **인입 스케줄러가 단일 스레드 직렬 실행이라 느린 소스 하나가 다른 소스 폴링을 무기한 지연** · `prism/ingestops.py:444` · perf/low

### [P2] 핸드오프 번들 조회 접기·REAP N+1 제거

- 성격 `perf` · 등급 **careful** · 규모 M · 브랜치 `perf/handoff-bundle-fold`
- handoff_bundle 이 한 요청에서 feedback 4회·골든 3회·patch_log 3회·contents 2회 중복 조회하고, REAP 결속이 해시당 get_reap 순차 호출로 번들 발행 시 최대 1,200왕복 N+1 이 난다. fmap·golden·patches·cmap 1회 조회 후 인자 전달 + get_reap_many 청크 조회로 접는다.
- 근거 발견 2건:
  - **handoff_bundle 이 한 요청 안에서 feedback 전량 4회 · 골든 3회 · patch_log 3회 · contents_by_hash 2회 중복 조회** · `prism/learnops.py:1038` · perf/low
  - **REAP 결속이 해시당 get_reap 순차 호출(건당 2왕복) · 번들 발행 시 최대 1,200왕복 N+1** · `prism/learnops.py:903` · perf/medium

### [P2] 골든 목록·승격 보상 조회 최적화

- 성격 `perf` · 등급 **careful** · 규모 M · 브랜치 `perf/golden-list-reward`
- golden_list 가 모델·버전 부착용으로 recent_meta 1,000행(메타 블롭 포함)을 통째로 조회하고, 골든 승격 보상 log_event_once 가 (신규 골든×기여자)당 GET+POST 2왕복 순차 실행한다. origin_meta_for 슬림 조회로 교체 + events kind=in.() 일괄 확인·미기록분 일괄 POST 로 접는다.
- 근거 발견 2건:
  - **golden_list 가 모델·버전 부착용으로 recent_meta 1,000행(메타 블롭 포함)을 통째로 조회** · `prism/learnops.py:206` · perf/low
  - **골든 승격 보상 log_event_once 가 (신규 골든×기여자)당 GET+POST 2왕복 순차 실행** · `prism/learnops.py:309` · perf/low

### [P2] rebalance·escalate 일괄 배정

- 성격 `perf` · 등급 **careful** · 규모 M · 브랜치 `perf/crew-assign-bulk`
- rebalance·escalate_split 적용이 콘텐츠당 set_assignees(DELETE+POST) 순차 호출이라 200건이면 400왕복이 난다. changed 를 담당조합·min 기준으로 그룹핑해 set_assignees_bulk 재사용, 근본은 hash별 상이 목록을 받는 bulk 메서드(2왕복).
- 근거 발견 1건:
  - **rebalance·escalate_split 적용이 콘텐츠당 set_assignees(DELETE+POST) 순차 호출 · 200건이면 400왕복** · `prism/crewops.py:963` · perf/medium

### [P2] 토픽 드릴 페르소나 경량 조회

- 성격 `perf` · 등급 **careful** · 규모 M · 브랜치 `perf/topic-personas-readonly`
- topic_drill → topic_personas 가 usermeta 전체 빌드(O(n²) 유사도 + 미생성 페르소나 LLM 생성)를 동기 유발한다. 저장된 리포트만 읽는 경량 경로로 분리하거나 usermeta_data 에 read_only 플래그를 추가해 드릴다운에서 LLM 생성을 건너뛴다.
- 근거 발견 1건:
  - **topic_drill → topic_personas 가 usermeta 전체 빌드(O(n²) 유사도 + 미생성 페르소나 LLM 생성)를 동기 유발** · `prism/topicops.py:459` · perf/low

### [P2] MCP 키 인증·조회 왕복 절감

- 성격 `perf` · 등급 **careful** · 규모 M · 브랜치 `perf/mcp-keys-roundtrips`
- resolve 가 인증마다 mcp_key_touch PATCH 를 보내 /mcp 요청당 고정 3왕복이 붙고, GET /mcp-keys 가 키마다 usage() 3왕복을 반복해 최대 16회 N+1 이 난다. touch 시각을 캐시해 60초 1회 PATCH + 키별 성공/실패 집계 1회 조회로 왕복 16→2~3.
- 근거 발견 2건:
  - **resolve 가 인증마다 mcp_key_touch PATCH 를 보내 /mcp 요청당 고정 원격 3왕복이 붙는다(supabase)** · `prism/mcpkeys.py:254` · perf/low
  - **GET /mcp-keys 가 키마다 usage() 3왕복을 반복해 최대 16회 원격 호출 N+1(supabase)** · `prism/serve.py:1835` · perf/low

### [P2] entity_confidence 본문 정규화 1회화

- 성격 `perf` · 등급 **safe** · 규모 S · 브랜치 `perf/entity-confidence-normalize`
- entity_confidence 가 엔티티마다 본문 전체를 재정규화(행당 k회 NFC+정규식+casefold)한다. scored_entities 에서 title/summary/body 정규화와 names 목록을 1회 계산해 넘긴다. 순수 함수 계약·결과 불변, 단건 시그니처는 위임으로 보존.
- 근거 발견 1건:
  - **entity_confidence 가 엔티티마다 본문 전체를 재정규화(행당 k회 NFC+정규식+casefold)** · `prism/entconf.py:85` · perf/low

## P3 과제

### [P3] 백엔드 죽은 코드·상수 정리

- 성격 `cleanup` · 등급 **careful** · 규모 M · 브랜치 `chore/backend-dead-code`
- runops 함수 안 중복 import, crewops rebalance 죽은 조건·도크스트링 불일치, mcpkeys AUTH_FAIL·personagen PROFILE_FIELDS 미사용 상수, embed.embed_many 무호출 메서드를 삭제·교정한다. runops.py 를 건드려 과제 12 뒤에 착수.
- 근거 발견 5건:
  - **함수 안 중복 import: run_pipeline·run_batch 가 content_hash 를 같은 스코프에서 두 번 import** · `prism/runops.py:293` · cleanup/low
  - **rebalance 정체 판정의 죽은 조건: (progress==0 and not available) 이 not available 에 흡수 · docstring 의 폴백은 미구현** · `prism/crewops.py:896` · cleanup/low
  - **AUTH_FAIL 상수 미사용 · '인증 실패 단일 문구' 주석이 사실과 다름** · `prism/mcpkeys.py:75` · cleanup/low
  - **PROFILE_FIELDS 상수 미사용 · 파서는 필드명을 하드코딩** · `prism/personagen.py:12` · cleanup/low
  - **EmbeddingClient.embed_many 메서드 무호출** · `prism/embed.py:58` · cleanup/low

### [P3] serve.py 죽은 재수출·라우트 정리

- 성격 `cleanup` · 등급 **careful** · 규모 M · 브랜치 `chore/serve-dead-exports`
- serve.py 174~184 의 검수운영(HR)·주간기록 재수출 별칭 11개(crew_data 제외)와 무참조 POST /crew-escalate 라우트를 삭제하고 관련 주석·ARCHITECTURE.md 표를 교정한다. /crew-escalate 는 운영 수동 호출 여부 확인 후 삭제. ARCHITECTURE.md 행 제거는 과제 47 과 순서 조정.
- 근거 발견 3건:
  - **검수운영(HR)·주간기록 재수출 별칭 11개가 전혀 참조되지 않는 죽은 코드** · `prism/serve.py:174` · cleanup/low
  - **검수운영(HR) 재수출 10개가 어디서도 참조되지 않는 죽은 별칭** · `prism/serve.py:175` · cleanup/low
  - **POST /crew-escalate 라우트 무참조(UI·테스트·문서 호출자 0)** · `prism/serve.py:2458` · cleanup/low

### [P3] 저장 계층 죽은 코드 정리

- 성격 `cleanup` · 등급 **careful** · 규모 M · 브랜치 `chore/store-dead-code`
- UI 미사용 accuracy_delta 필드(supabase 0.0 스텁·sqlite 누적)와 죽은 Store.clear/SupabaseStore.clear(후자는 전 팀 무필터 삭제라 위험), cases 테이블 DDL·낡은 docstring 을 제거한다. 응답 shape 회귀 테스트로 확인.
- 근거 발견 3건:
  - **accuracy_delta 필드: UI 미사용 + supabase 는 0.0 고정 + sqlite 계산도 주석과 어긋남** · `prism/store.py:1970` · cleanup/low
  - **Store.clear()/SupabaseStore.clear() 죽은 코드 · 후자는 전 팀 무필터 삭제라 위험한 잔존물** · `prism/supastore.py:2250` · cleanup/low
  - **cases 테이블 DDL 죽은 스키마 · 모듈 docstring 의 '판정사례' 서술도 낡음** · `prism/store.py:164` · cleanup/low

### [P3] reviewassist 낡은 골드 주석·미사용 상수

- 성격 `cleanup` · 등급 **careful** · 규모 S · 브랜치 `chore/reviewassist-comments`
- content_brief 가 골드를 '아직 거절한다'는 낡은 주석 3곳과 미사용 GOLD_MSG·ASK_NO_MODEL 상수가 현재 동작과 어긋난다. 주석을 현재 동작(골드도 _resolve 로 평소대로 답함)에 맞게 갱신하고 미사용 상수를 삭제, 테스트는 리터럴 문자열로 대체한다.
- 근거 발견 2건:
  - **content_brief 가 골드를 '아직 거절한다'고 말하는 낡은 주석 3곳(+미사용 GOLD_MSG) · 현재 동작과 정반대** · `prism/reviewassist.py:984` · cleanup/low
  - **ASK_NO_MODEL 상수 미사용 · _ask_no_model("") 과 중복** · `prism/reviewassist.py:870` · cleanup/low

### [P3] 프론트엔드 죽은 코드·CSS·인라인 스타일 정리

- 성격 `cleanup` · 등급 **safe** · 규모 M · 브랜치 `chore/frontend-dead-code`
- logviewer lvFieldBad 죽은 함수, app.css 죽은 셀렉터 다수, navIcons 6종·modSub 도달 불가 10종, 20-ingest-policy 의 x-show 인라인 display 선언·제거된 IIFE 잔재 주석을 삭제한다. grep 재확인 후 제거. 20-ingest-policy.html 은 과제 25 뒤에 착수.
- 근거 발견 6건:
  - **lvFieldBad() 죽은 코드 · 정의만 있고 어디서도 호출되지 않음** · `prism/vendor/app-15-logviewer.js:65` · cleanup/low
  - **죽은 CSS 잔존: .ds-guide 블록 · .tmodel__sel/.tmodel__rf · .w-stat-l·.ds-eyebrow·.ds-hint·.secdesc·.rc-d·.ds-empty__title** · `prism/vendor/app.css:67` · cleanup/low
  - **navIcons 죽은 항목 6종(run·queue·quality·user·review·arena) · 어떤 메뉴도 참조하지 않는 SVG 문자열** · `prism/vendor/app-00-tabitems.js:19` · cleanup/low
  - **modSub 소개문 맵의 도달 불가 항목 10종 · selectMod 별칭 치환으로 mod가 그 값이 될 수 없음** · `prism/vendor/app-02-_afterverdict.js:266` · cleanup/low
  - **주의 ③ 위반: x-show 요소에 인라인 display:flex/inline-flex (185 · 336) · 현재는 클래스 폴백이 가려줌** · `prism/ui/20-ingest-policy.html:185` · cleanup/low
  - **파일 말미의 '위젯 홈 인터랙션 · SERVICE_DESIGN §4.3' 주석이 제거된 IIFE를 가리키는 잔재** · `prism/ui/20-ingest-policy.html:592` · cleanup/low

### [P3] 스펙트럼 관문 mcprpc 전송 이관

- 성격 `cleanup` · 등급 **careful** · 규모 M · 브랜치 `chore/spectrum-mcprpc-transport`
- 스펙트럼 관문이 mcprpc 공용 전송을 쓰지 않고 JSON-RPC 전송을 중복 구현한다(이미 3곳 분기). mcprpc.Endpoint 어댑터로 _mcp 를 위임 교체하고 _rpc_ok/_rpc_err/_bearer 중복을 삭제한다. 이 이관이 과제 24 의 ping·형오류·사유열거를 근본 해소하므로, 용량이 되면 이 과제를 먼저 해 24 를 축소한다.
- 근거 발견 1건:
  - **스펙트럼 관문이 mcprpc 공용 전송을 쓰지 않고 JSON-RPC 전송을 중복 구현(이미 3곳에서 갈라짐)** · `prism/spectrumops.py:855` · cleanup/low

### [P3] 코드 주석·도크스트링 최신화

- 성격 `cleanup` · 등급 **safe** · 규모 M · 브랜치 `chore/code-comments-sync`
- 번들 도입 전 서술이 남은 app.js 로더·page.py 도크스트링·00-head.html 로드순서 주석, mcpserver _KEYS·mobile.js 헤더 오설명, learnops eval_golden 의 'supabase 전용' 오류 문구를 실동작에 맞게 교정한다. learnops.py 는 과제 9 와 파일 충돌 주의.
- 근거 발견 6건:
  - **_KEYS 주석 '키 모듈 캐시 겸 테스트 주입점' 이 사실과 다르다(캐시로 동작하지 않음)** · `prism/mcpserver.py:70` · cleanup/low
  - **로더 머리 주석이 낡음 · '00-head.html 의 script 순서가 원천'은 번들 도입 전 이야기** · `prism/vendor/app.js:3` · cleanup/low
  - **eval_golden 의 'Supabase 모드 전용' 오류 문구가 사실과 다름(sqlite 도 골든 평가 지원)** · `prism/learnops.py:67` · cleanup/low
  - **mobile.js 헤더의 API 엔드포인트 목록('…만 사용')이 실사용과 어긋남** · `prism/vendor/mobile.js:1` · cleanup/low
  - **page.py 도크스트링이 번들 이전의 /vendor/app.js·app.css 직접 참조를 말함** · `prism/page.py:6` · cleanup/low
  - **번들 이전 시절의 로드 순서 주석이 바로 아래 현행 주석과 중복 잔존** · `prism/ui/00-head.html:43` · cleanup/low

### [P3] ARCHITECTURE.md 최신화

- 성격 `cleanup` · 등급 **safe** · 규모 S · 브랜치 `chore/architecture-md-sync`
- umops 행의 build_template_xlsx 오귀속, UI 구조 절의 앱 JS 조각 수·로드 순서 원천, serve.py 크기 표기(약 2.9k→3.5k줄), 도메인 지도의 evalops·deployops·weekops·reviewassist 부재를 함께 교정한다. 같은 파일이라 한 PR 로 처리. 과제 41(ARCH 행 제거)과 순서 조정.
- 근거 발견 4건:
  - **serve 도메인 표의 umops 행이 build_template_xlsx 를 잘못 귀속(실체는 runops · umops 는 build_usermeta_template_csv)** · `ARCHITECTURE.md:52` · cleanup/low
  - **UI 구조 절이 낡음: 앱 JS 조각 '14개'(실제 17개) · 로드 순서 원천 '00-head.html script 태그'(실제 assets.py 번들)** · `ARCHITECTURE.md:124` · cleanup/low
  - **serve.py 크기 표기 ≈2.9k줄이 실제 3,454줄과 어긋남** · `ARCHITECTURE.md:14` · cleanup/low
  - **[doc-fix] 도메인 모듈 지도에 evalops·deployops·weekops·reviewassist 부재** · `ARCHITECTURE.md:15` · cleanup/low

### [P3] HANDOFF.md 최신화

- 성격 `cleanup` · 등급 **safe** · 규모 M · 브랜치 `chore/handoff-md-sync`
- '로컬 sqlite UI 제거' 오서술, '갱신 2026-07-30'·지금 상태·다음 단계가 8월 대형 변경(DB 분리·실험실 정리·MCP 키) 미반영, serve.py·앱 JS 조각 수치 낡음을 8/13 기준으로 재작성한다. 같은 파일이라 한 PR.
- 근거 발견 3건:
  - **[doc-fix] '로컬(sqlite) 모드는 UI 상 제거' 서술 · 현행 코드에는 로컬 검수자 등록 UI 존재** · `HANDOFF.md:29` · cleanup/low
  - **[doc-fix] '갱신: 2026-07-30' 표기·'지금 상태'·'다음 단계'가 8월 대형 변경을 반영하지 못한 채 잔존** · `HANDOFF.md:3` · cleanup/low
  - **[doc-fix] 코드 구조 수치 낡음: serve.py '~2,900줄'(실제 3,454) · 앱 JS '14조각'(실제 17)** · `HANDOFF.md:203` · cleanup/low

### [P3] 개발 문서 최신화

- 성격 `cleanup` · 등급 **safe** · 규모 M · 브랜치 `chore/dev-docs-sync`
- GUIDE 학습 반영 '매일 새벽 자동' 오안내, DOCKER compose 필수 env 누락, BOARD 구 프로젝트 '백업' 오서술(8/13 삭제), IA_ROADMAP 사용자 메타 계획 마커, TESTING 테스트 파일 수·QA_CHECKLIST 버전 앵커를 현행으로 교정한다. 각기 다른 파일이라 저충돌.
- 근거 발견 6건:
  - **[doc-fix] 학습 반영을 '매일 새벽 자동'으로 안내 · 실제는 퀘스트 지정 일시 1회 실행** · `GUIDE.md:101` · cleanup/medium
  - **[doc-fix] compose 안내가 현행 compose.yml(Supabase 필수)과 어긋나 그대로 따르면 기동 실패** · `DOCKER.md:22` · cleanup/low
  - **[doc-fix] '구 yujinhcdbllcnnfvcmfp 는 백업' 서술 · 구 프로젝트는 2026-08-13 완전 삭제됨** · `docs/BOARD_IMPROVEMENT_AGENT.md:12` · cleanup/low
  - **[doc-fix] 사용자 메타 ⬜(계획) 표기 · 실험실 PoC(usermeta·umops) 구현 존재 · §1.4 '현재 구현' 라우트도 초기 9개에 정지** · `docs/IA_ROADMAP.md:22` · cleanup/low
  - **[doc-fix] 테스트 파일 수 '97개(2026-07-28 기준)' · 현재 155개** · `TESTING.md:38` · cleanup/low
  - **[doc-fix] 제목의 '(v0.5.2 기준)' 버전 앵커 낡음 · 본문은 8/13 까지 갱신됨** · `QA_CHECKLIST.md:1` · cleanup/low

### [P3] 폐기 문서 자산 삭제

- 성격 `cleanup` · 등급 **safe** · 규모 S · 브랜치 `chore/remove-stale-assets`
- 폐기된 콘텐츠 에이전트 와이어프레임 3종과 GUIDE 미삽입·무참조 guide-assets PNG 7개(약 3.4MB)를 삭제한다. 이력은 git·ARTIFACTS.md 색인으로 충분. 필요 시 현행 화면으로 재캡처.
- 근거 발견 2건:
  - **[doc-del] 폐기된 콘텐츠 에이전트의 와이어프레임 3종 · 모문서 삭제 후 잔존 · 무참조** · `docs/content-agent-portal-wireframe.html:1` · cleanup/low
  - **[doc-del] guide-assets PNG 7개(~3.4MB) · GUIDE.md 미삽입 · 무참조 · 7/15 감사 플래그 후 방치** · `docs/guide-assets/guide-home.png:1` · cleanup/low

### [P3] 스펙트럼 사이드카 쓰기 배치

- 성격 `perf` · 등급 **careful** · 규모 M · 브랜치 `perf/spectrum-sidecar-batch`
- 관문 호출 1건마다 사이드카 전체(약 150KB·최대 1,700행)를 재직렬화·재기록한다. 프로토타입 범위에서 usage 기록을 N건·T초 배치로 모으거나 keys 와 usage 파일을 분리해 호출 경로는 usage 파일만 쓴다. 운영 전환 시 저장 계층 이관으로 자연 해소.
- 근거 발견 1건:
  - **관문 호출 1건마다 사이드카 전체(≈150KB · 최대 1,700행)를 재직렬화·재기록한다** · `prism/spectrumops.py:706` · perf/low

## 부록 · 반박·불확실 (과제에서 제외)

**반박(REFUTED · 실제 문제 아님):**
- `prism/learnops.py:258` 골든 승격·대기 집계가 스토어 키 대신 content_ref 재계산 해시를 사용해 sqlite 에서 승격 누락 — The finding's premise is false. It assumes the stored content_ref carries a normalized body so content_hash(content_ref) diverges from the store key. But _paylo
- `prism/ingestops.py:320` 자동 인입도 전량 추출 후 일괄 저장 · 중단 시 지출한 추출 결과 유실(백로그 [30]과 동일 패턴의 다른 지점) — Line/evidence anchoring is accurate: ingest_run_source (297–316) extracts up to limit records then calls save_dedup once at 320, and the outer except at 338 cov
- `prism/mediaops.py:29` 미디어 승격(2026-08-13) 후 실험실 잔재: media_action 의 s5ab(A/B)·subtitles JSON 분기는 UI 무참조 — 발견의 국소 사실은 모두 맞다: mediaops.py:29 = `if action == "s5ab":`, UI(prism/ui/03-run.html)는 mediaImgRun()/mediaNative() 두 경로뿐이고 둘 다 prism/vendor/app-06-weeklyleague.js
- `prism/mcpserver.py:177` 소형 모델 응답 상한(_fit)이 인자를 안 보낸 호출(기본값 경로)에는 적용되지 않는다 — Code mechanics are accurate (mcpserver.py:181 clamps only present integer args; get_examples default=20 at prismtools.py:166; get_taxonomy has no limit), but th
- `prism/ui/03-run.html:97` 화면 노출 em dash 잔존 10곳 · em dash 금지 규칙(PR#448 스윕)에서 누락 — 발견의 사실 관찰(라인·evidence)은 맞다. '—'(U+2014)가 03-run.html 97·98·99·100·152·153·154·155, 09-entdict.html:56, 20-ingest-policy.html:44 에 실재하고 x-text 로 화면에 그려진다. 그러나 발견

**불확실(UNCERTAIN · 코드로 확정 못 함 · 판단 보류):**
- `prism/reviewops.py:61` 골드 출제 방식 표기(GOLD_SCHEME)를 붙이지만 읽는 쪽(gold_stats 등)이 회차를 분리하지 않아 구·신 회차 신뢰도가 여전히 한 분모에 섞임 — 코드 사실 자체는 맞다: 읽기 경로(store.py gold_stats 748-757 · gold_stats_since 759-769 · gold_today 847-853, supastore.py gold_stats 729-740 및 RPC prism_agg_gold_stats(p_te
- `prism/llm.py:390` usage 가 null(또는 필드값 null)인 200 응답에서 정상 완성 텍스트를 버리고 최대 4회 재과금 재시도 — 코드 메커니즘은 실재: llm.py:390 `payload.get("usage", {})` 는 봉투에 `"usage": null` 이 오면 None 을 돌려주고, 391행 `None.get(...)` 에서 AttributeError → complete_json 의 일반 except(30
- `prism/mediaops.py:90` media_native: 비전 슬롯이 라우터가 아니면 mock 폴백한다는 계약과 달리 bizrouter+S5 텍스트 모델로 실호출 — Code facts all check out: mediaops.py:90-92 matches; default vision_provider="upstage_ie"/vision_model="" (config.py:97-98) is not a router (ROUTERS={bizrouter,
- `prism/usermeta.py:297` build_from_logs 의 등급 필터 주석이 사실과 다름: 'G만·build_mock 과 동일 기준'이라지만 코드는 R 만 제외 — 코드 팩트는 정확: build_from_logs(:299) `(qm.get("finalGrade") or "G")=="R"` 는 R만 스킵하고 등급 미기록 행은 "G" 기본값으로 포함, build_mock(:480) `!= "G"` 와 _empty_user_meta(:90) `== "G
