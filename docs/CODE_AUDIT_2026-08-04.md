# 코드 감사 2026-08-04 · 성능·버그 전수 점검

멀티 에이전트 감사(탐색 7 도메인 병렬 + 적대 검증): 발견 42 → 중복 제거 38 → 검증 확정 38(기각 0).
이전 스윕(7/22 코드 스윕 · 7/28~29 성능 백로그)과 의도된 계약(raw 슬림 · ETag · 30s 캐시 · 집계 RPC 등)은 제외한 결과다.
**high 전건 + safe 전건 29건은 즉시 수정**(PR #388~#393 · 회귀 테스트 82건 추가 · 1068 → 1150 passed), careful 등급 9건은 아래 백로그로 남긴다.

## 수정 완료 29건 (2026-08-04 머지)

| PR | 파일:라인 | 심각도 | 내용 |
|---|---|---|---|
| #388 | prism/dictionaries.py:758 | bug/high | 사전 편집 저장이 안 건드린 형제 키의 복원값까지 툼스톤으로 기록 · 코드 신규 사전 값 영구 삭제가 편집 경로로 재발 |
| #389 | prism/serve.py:1855 | bug/high | 레이트리밋 키가 Fly 프록시 IP · /auth 가 전역 공유 버킷이 되어 오차단·로그인 잠금 가능 |
| #389 | prism/serve.py:1327 | perf/medium | 무인증 /config(15초 헬스체크 포함)가 전체 config_status 를 계산 후 버림 · 요청당 supabase 왕복 약 3회 낭비 |
| #389 | prism/serve.py:2764 | bug/medium | do_GET 디스패치에 예외 처리가 없어 핸들러 예외 시 응답 없이 연결 종료 · limit 류 쿼리로 즉시 재현 |
| #389 | prism/serve.py:2429 | bug/low | /presence · 팀 미소속 인증 계정이 team=None 브로드캐스트로 전 팀 SSE 스트림에 임의 이벤트 주입 |
| #389 | prism/serve.py:1931 | bug/low | /reviewer 가 팀 캐시를 저장 전에 pop · 병렬 요청이 옛 팀을 재캐시하면 가입·팀 변경 반영이 최대 60초 지연 |
| #390 | prism/supastore.py:1832 | bug/high | id 키 자원(배포·키·라이브러리·평가런·오토파일럿)이 team 미필터 · 타 팀 자원 열람·조작 가능 |
| #390 | prism/supastore.py:1144 | bug/high | 재실행 upsert 가 quality_meta 를 통째로 덮어 ops_hold·source_status 플래그 유실 |
| #390 | prism/supastore.py:1898 | bug/medium | 비유일 정렬키 + offset 페이징 · 1000행 초과 시 recent/assignees 행 중복·누락 |
| #390 | prism/supastore.py:1176 | perf/medium | review_queue 가 매 호출 feedback 테이블 전량 다운로드(상한·필터 없음) |
| #390 | prism/store.py:513 | perf/low | results(created_at) 인덱스 부재 · recent/review_queue/recent_meta 가 매 호출 전량 정렬 |
| #391 | prism/ui/07-studio.html:77 | bug/low | 토픽 스튜디오 모델 선택이 네이티브 select 잔존 · x-modelpick 규칙 위반 + 미연결 라우터 모델도 선택 가능 |
| #391 | prism/ui/00-head.html:90 | bug/low | +PT 토스트의 x-bind:key 가 x-for 밖이라 무동작 · 연속 검수 시 애니메이션 재시작 안 되고 후속 토스트가 안 보일 수 있음 |
| #391 | prism/vendor/app-02-_afterverdict.js:34 | perf/low | 검수 원본 목록 필터 getter 체인이 바인딩마다 전량 재계산 · 넓은 창(2000행)에서 키입력당 ~8회 풀 스캔 |
| #391 | prism/vendor/app-02-_afterverdict.js:324 | perf/low | 번들에 실려 가는 죽은 코드 묶음: drill()·crewEscalate()·im/q·showProfileFields·teamMode·caEditing·bulkModel |
| #391 | prism/ui/21-tail.html:33 | perf/low | 카드 헤더 장식 MutationObserver 가 변이 배치마다 문서 전체를 재스캔 |
| #392 | prism/reviewops.py:822 | perf/high | feedback 전량 원격 재조회가 요청마다 무캐시로 반복(rows 캐시와 비대칭) |
| #392 | prism/reviewops.py:992 | perf/medium | /history 단건 조회가 patch_log 전량(JSON 블롭 포함 5000행)을 매번 다운로드 |
| #392 | prism/reviewops.py:150 | bug/medium | 최종검수 큐가 '합의+분류 있음+등급 공백' 건을 승격 예정으로 오인해 건너뛰어 영구 미확정으로 남는다 |
| #392 | prism/weekops.py:144 | bug/medium | 주간 스냅샷 capture 가 미적립 과거 주 전부에 '오늘의 상태 지표'를 estimated=False 확정값으로 고정한다 |
| #392 | prism/crewops.py:760 | perf/medium | rebalance·escalate_split 가 _crew_compute 직후 같은 테이블(assignees·feedback_map·review_targets 등)을 다시 전량 조회한다 |
| #392 | prism/crewops.py:1028 | perf/low | plan_distribute 경로의 category_reliability 가 무캐시로 feedback_map 을 또 전량 조회한다(기본 켜짐) |
| #392 | prism/reviewops.py:139 | perf/low | final_review_queue 가 _row_key 지름길 대신 매 호출 전 행의 content_hash(본문 SHA)를 재계산한다 |
| #393 | prism/runops.py:321 | bug/high | 402(크레딧·한도) 등 비재시도성 실패에도 일괄 실행이 끝까지 돌고, 전건 실패를 '완료'로 집계 |
| #393 | prism/runops.py:517 | bug/medium | 엑셀 일괄 추출·자동 인입 경로가 비용·실패 원장을 통째로 우회(402 폭주도 원장 무기록) |
| #393 | prism/runops.py:227 | perf/medium | 배치 루프에서 _batch_seq_cached 캐시가 매건 자기무효화되어 건마다 events 재조회(supabase 는 건당 GET 1만행) |
| #393 | prism/schema.py:115 | bug/low | 잘린 꼬리 태그만 있는 본문은 strip_html 이 정제하지 않음 · _DANGLING 이 도달 불가 코드가 됨 |
| #393 | prism/ingestops.py:105 | perf/low | _INGEST_STATE 잡 레지스트리가 메모리에서 무한 성장하고 상태 폴링 응답이 부팅 이후 전체 잡을 반환 |

운영 조치 노트(#388): 이미 오염된 운영 오버라이드 파일의 툼스톤(코드 신규 값이 삭제로 오기록된 항목)은 자동 복구하지 않는다. 증상(코드에 추가한 사전 값이 계속 사라짐)이 보이면 해당 팀 오버라이드의 `_removed` 항목을 점검해 걷어낼 것.

## 백로그 9건 · careful 등급(계약·동시성·정책에 닿아 별도 검토 후 착수)

### [10] prism/supastore.py:100 · bug/medium

**_http 가 비멱등 POST 도 1회 재시도 · 유휴 소켓 끊김 시 append-only 행 이중 기록**

- 증상: supastore.py:92~110 for attempt in (0,1) 루프가 메서드 구분 없이 HTTPException/ConnectionError 시 소켓 재수립 후 동일 요청 재전송. keep-alive 유휴 종료 경합에서 서버가 요청을 처리한 뒤 응답 전 연결이 끊기면(getresponse 예외) 같은 POST 가 두 번 실행된다. 영향 테이블: patch_log(605)·gold_checks(634)·events(768, log_event_once 의 check-then-insert 락 밖 재전송이라 중복 방지 무력)·board(1576)·feedback_routes(872) — 골드 응답 이중 카운트로 정확도·점수 왜곡, 게시글 이중 등록.
- 수정안: 재시도를 멱등 호출(GET·PATCH·DELETE·merge-duplicates upsert)로 한정하거나, c.request() 전송 성공 후 응답 단계 예외에서는 POST 재시도를 생략. 또는 append 계열 POST 에 클라이언트 생성 idempotency 키 컬럼 도입.

### [14] prism/reviewops.py:389 · perf/medium

**판정 저장 핫패스가 매 건마다 assignees 전량 조회 + 미션 판정용 스토어 왕복 4~6회를 수행한다**

- 증상: reviewops.py:388-391 apply_feedback 이 해시 1건의 배타 배정 확인을 위해 `st.assignees(team=...)` 전량(supastore.py:304 · assignments 전 행 GET)을 내려받고, 424 `_check_missions` → mission_progress(474-477)가 feedback_today·gold_today·split_reviewed_today·patches_today 4회 + reviewer_roles 리포트 조회를 더한다. 시나리오: supabase 팀에서 검수자 1명이 시간당 90건 판정하면 판정 POST 마다 전량 배정 테이블(수천 행) 다운로드 포함 6회 이상 원격 왕복이 반복되어 저장 지연·이그레스가 판정 수에 비례해 커진다. 스윕(2643d5a)은 feedback 전량 로드만 제거했고 이 경로는 남았다.
- 수정안: 배정 확인은 해시 단건 조회 메서드(content_hash=eq. 필터)를 스토어에 추가하거나 assignees 를 _agg_cached 로 짧게 캐시. mission_progress 의 오늘 지표 4종은 단일 조회로 묶거나 판정 응답 경로에서 비동기화.

### [17] prism/runops.py:386 · perf/medium

**rerun_content 이중 영속: 건당 저장·초안·엔티티 사전 적재가 2번씩(supabase 는 원격 호출 2배)**

- 증상: runops.py:386 이 run_pipeline 을 persist=True(기본)로 불러 내부 store_save(runops.py:99-104: save_dedup + _save_drafts + _entdict_after_save)가 실행되고, 직후 runops.py:392-397 이 같은 pair 로 st.save_many + _save_drafts + _entdict_after_save 를 반복한다. supabase 는 save_dedup 도 sync_contents(include_all=True)(supastore.py:1927-1933)라 save_many(supastore.py:1924-1925)와 완전 동일 · 건당 upsert POST + _kept_sources GET(supastore.py:1157-1165)이 2세트, save_draft 업서트와 entdict ingest 도 2회씩이다. rerun_all 200건 배치면 순수 중복 원격 호출이 수백 건 추가된다(핫패스 N×2 원격 쓰기).
- 수정안: rerun_content 에서 run_pipeline(persist=False)로 호출하고 저장은 save_many 1회 + drafts/entdict 1회로 일원화(미러 _LAST_RESULTS·_agg_bump·실패 원장 content_hash 전달을 명시적으로 보완). 저장 계약(sqlite 는 save_many 로 모델·버전 강제 갱신)이 걸려 있어 테스트로 dedup/upsert 동작 확인 필요.

### [20] prism/reviewops.py:509 · perf/medium

**reviewer_weights 의 Dawid-Skene EM 이 요청마다 재실행되고 learn_data 는 같은 요청에서 2회 계산**

- 증상: reviewops.py:521-525 가 호출마다 dawid_skene_binary 를 실행한다. quality.py:75-91 M-step 은 30회 반복 × 검수자수 × 전 유닛 순회(희소 라벨인데도 'r not in rv' 스킵으로 전 유닛을 돈다) · E-step 포함 유닛 2천·검수자 10명이면 반복당 수십만 dict 연산 × 30 으로 요청당 수백 ms 순수 CPU. final_review_queue 가 요청마다 호출(reviewops.py:127)하고, learn_data 는 learnops.py:587 에서 EM 을 1회 돌린 뒤 590 에서 reviewer_weights(team) 를 fmap 미전달로 또 호출해 feedback 원격 전량 재조회 + EM 재실행까지 겹친다(learnops.py:240 build_golden 도 동일 패턴)
- 수정안: reviewer_weights 결과를 _agg_cached(("rvw", team)) 로 메모(피드백·골드 쓰기 경로가 _agg_bump 호출이라 정합 유지). learnops.py:590·240 은 이미 손에 있는 fmap 을 인자로 전달

### [26] prism/supastore.py:511 · perf/low

**register_golden 병합 경로가 건당 DELETE+POST 2왕복 · N건 등록에 2N 원격 호출**

- 증상: supastore.py:510~516 replace=False 분기가 행마다 upsert_golden 호출 → upsert_golden(488~497)은 DELETE 1회+POST 1회. 골든 500건 병합 업로드 시 1000회 순차 원격 호출(왕복 80~400ms 실측 기준 1.5~6분). 바로 위 replace=True 분기(507~508)는 이미 500건 청크 일괄 POST 로 처리하고 있어 대비가 명확. learnops.py:233~247 의 검수 확정 골든 편입도 같은 upsert_golden 을 반복 사용.
- 수정안: 병합 경로도 (team_id, content_hash) 충돌 대상 merge-duplicates 일괄 upsert(청크 500)로 전환. PostgREST upsert 가 PK 외 유니크 제약을 요구하므로 golden(team_id, content_hash) 유니크 인덱스 존재 확인 후 적용(미존재 시 마이그레이션 병행).

### [28] prism/crewops.py:657 · bug/low

**캐파 0 경계: 이번 주 0시간 신고자도 cap 이 1 로 강제되어 배정 후보가 되고 정상 구간 최우선으로 1건을 받는다**

- 증상: crewops.py:657 `cap = {m["id"]: max(1, m["weekly_capacity"] or 1) ...}` 는 hours_per_week=0 신고(weekly_capacity 0)를 cap=1 로 바꾼다. 677 floors 는 round(1×0.5)=0 이라 보장 구간을 건너뛰고, _score(699) 정상 구간 점수 0/1=0 은 하한을 갓 채운 다른 인원(≈0.5)보다 작아 그 사람이 먼저 뽑힌다. 시나리오: '이번 주 못 한다'고 0시간을 신고한 active 인원이 웨이브마다 1건씩 받아 그 콘텐츠가 stale_days(3일) 동안 정체 후에야 rebalance 로 회수된다. _available(333-336)은 status·부재만 보고 신고 시간 0 을 거르지 않는다.
- 수정안: weekly_capacity(또는 hours_per_week)가 0 인 인원을 pool 에서 제외하거나 cap 강제 하한을 없애 초과 구간에서 시작하게 한다. '일이 아예 안 가면 쏠림' 원칙과의 조율이 필요해 정책 확인 후 반영.

### [30] prism/runops.py:530 · bug/low

**엑셀 일괄 추출이 전량 추출 후 일괄 저장이라 중간 재시작·예외 시 지출한 추출 결과 전량 유실**

- 증상: runops.py:516-531 · run_batch 는 최대 200건을 순차 추출한 뒤에야 store_save(runops.py:530)를 1회 호출한다. 건당 수 초 × 200건 = 수 분~십수 분 창에서 배포 재시작(이 저장소는 main 머지 시 CI 자동배포)이나 루프 예외(runops.py:527-529 는 잡만 실패 표기 후 raise)가 나면 이미 LLM 비용을 지불한 results/pairs 가 전부 버려지고, _jobs_restore(ingestops.py:337-356)는 '다시 실행하세요' 로 안내해 전액 재지출로 이어진다.
- 수정안: 루프 안에서 일정 청크(예: 20건)마다 store_save 로 부분 적재하고, 예외 시에도 지금까지의 pairs 를 저장 후 재던지기(저장은 hash 기준 멱등이라 재실행 시 dedup 으로 안전).

### [36] prism/serve.py:2806 · perf/low

**_inject_reviewer 가 team_of 60s 캐시를 우회해 인증 POST 마다 원격 팀 조회 1콜 추가**

- 증상: serve.py:2806 이 st.reviewer_team(uid) 를 직접 호출한다. 같은 목적의 60s 캐시 team_of(serve.py:646-665)가 이미 있고 GET 게이트(_req_team · serve.py:2794)는 그것을 쓰며, 팀 변경 시 무효화도 구현돼 있다(serve.py:1931 /reviewer POST 에서 _TEAM_CACHE.pop). 결과적으로 판정·교정 등 인증 필요한 모든 POST 가 요청마다 supabase 1왕복(도쿄→서울 수십~수백 ms)을 불필요하게 추가한다
- 수정안: data["_team"] = team_of(uid) 로 교체(1줄). 스테일 특성은 GET 경로와 동일 계약(60s TTL + /reviewer 무효화)

### [37] prism/runops.py:71 · perf/low

**일괄 실행 시 초안 스냅샷을 건별 원격 upsert(N+1 · 배치 200건이면 200왕복)**

- 증상: runops.py:68-74 _save_drafts 가 pairs 를 돌며 st.save_draft 를 건별 호출하고, supastore.save_draft(supastore.py:1614-1618)는 매번 1행짜리 POST 를 보낸다. 콘텐츠 본 적재는 sync_contents 가 한 번에 배치 upsert(supastore.py:1165)하는데 초안만 건별 왕복이라, run_batch·rerun_all 200건이면 drafts 로만 200왕복(왕복 100ms 기준 약 20초)이 배치 완료를 지연시킨다
- 수정안: supastore 에 save_drafts_bulk(rows) 를 추가해 _upsert("drafts", rows) 1~2왕복으로 배치 전송하고 _save_drafts 가 rows 를 모아 호출(sqlite 는 executemany 또는 기존 건별 위임)

