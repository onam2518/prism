# 성능 개선 백로그 · 2026-07-28 스윕 산출

2026-07-28 전체 코드·문서 효율화 스윕(에이전트 팀: 탐색 11 · 검증 11 · 적용 12)에서
도출한 성능 개선 과제와 정리 보류 건. 정리 자체(죽은 코드·주석·문서 어긋남 99건)는
같은 날 PR 로 반영 완료. 이 문서는 **아직 하지 않은 일**의 목록이다.

## 이어받는 세션을 위한 안내

- 과제는 서로 독립적이다. 하나씩 브랜치(`feat/perf-…`) → PR → 머지로 진행한다.
- 착수 전 해당 코드 위치를 다시 확인할 것. 줄 번호는 2026-07-28 기준이라 밀릴 수 있다.
- 커밋 전 `python3 -m unittest discover tests` 전체 통과 필수. 캐시를 추가하는 과제는
  쓰기 경로의 무효화(`_agg_bump` 류)까지 테스트에 포함할 것.
- 완료한 과제는 이 문서의 체크박스를 갱신하는 커밋을 같은 PR 에 포함한다.

## P1 · 국소 수정(저위험 · 효과 즉시)

- [x] **P1-1 관리자 게이트 supabase 왕복 캐시** · `adminops.py:244`, `serve.py:2845` · 완료 2026-07-29
  - 증상: 위임 관리자의 메뉴 대상 POST 1건마다 menu_allowed(6왕복) + 라우트 gate(4왕복)로
    supabase 왕복 최대 10회. GET 쪽(/queue, /final-queue)도 로드마다 2~4회.
    team_info·is_team_admin·is_team_super·menu_perms 전부 무캐시.
  - 계획: `_TEAM_CACHE`/`_JWT_CACHE` 와 같은 패턴으로 (uid,team)→(is_admin,is_super),
    team→(team_info,menu_perms) 를 60s TTL 캐시. set_member_admin/super·set_menu_perms·
    delete_team·remove_member 쓰기 경로에서 즉시 무효화. menu_allowed 와
    _admin_gate/_super_gate 가 캐시 공유 시 요청당 10회 → 0~2회.
  - 유의: 권한 회수 반영이 최대 60s 지연됨(기존 team_of 캐시와 동일 수준).
    2026-07-22 스윕에서 보류됐던 과제. 캐시 TTL 정책은 적용 전 사용자에게 한 번 확인할 것.
- [ ] **P1-2 results_rows 캐시** · `serve.py:287`
  - 증상: st.recent(5000)(전 컬럼·최대 5왕복·수 MB)를 /raw·/model-stats·/final-queue·/drill
    요청마다 재조회. dashboard 만 30s 캐시가 있음.
  - 계획: results_rows 자체를 `_agg_cached(("rows", team, limit))` 로 감싼다. 쓰기 경로는
    이미 전부 _agg_bump 호출이라 스테일 없음. 한 함수 수정으로 끝.
- [ ] **P1-3 골든셋 전량 fetch 캐시** · `reviewops.py:1057~1065`
  - 증상: /queue·/raw 로드마다 get_golden(팀 골든 전량 · content/expected 원문 포함)을
    재조회. 검수자들이 수시로 여는 화면이라 절대 빈도 높음.
  - 계획: get_golden 결과를 `_agg_cached(("golden", team))` 캐시. 골든 쓰기 경로
    (upsert/register/remove_golden) 뒤 _agg_bump 확인·보강. 골드 문항 선택은
    (검수자,일자) 시드 결정적이라 캐시로 결과가 달라지지 않음.
- [x] **P1-4 final-queue 의 feedback 3중 fetch 제거** · `reviewops.py:114~168` · 완료 2026-07-29
  - 증상: /final-queue 1건이 feedback_map → reviewer_weights 내부 → _attach_fb 내부로
    같은 feedback 전량을 3회 왕복.
  - 계획: fmap 을 1회 조회해 `reviewer_weights(team, fmap=)`·`_attach_fb(..., fmap=)` 로
    전달(crewops.capacity(team, fmap=) 과 동일한 기존 관례). 기본값 유지라 하위 호환.
- [x] **P1-5 dashboard 의 feedback 2중 fetch 제거** · `dashops.py:272~275` · 완료 2026-07-29
  - 증상: 재계산 1회가 feedback_map 과 feedback_stats(_all_feedback)로 같은 테이블 전량 2회.
  - 계획: feedback_stats 를 fmap 원본 행에서 파생하는 순수 함수로 바꾸거나 rows 선택 인자
    추가. 통계 정의(good/bad/learned/split)는 동일 원본에서 재현 가능해 수치 불변.
- [x] **P1-6 crew 재계산의 테이블 중복 fetch 축소** · `crewops.py:359~422` · 완료 2026-07-29
  - 증상: _crew_compute 1회에 assignments 3회 · feedback 2회 · gold_checks 2회 ·
    reviewers 2회, 합계 약 15왕복.
  - 계획: reviewer_weights 에 fmap/gold 인자 전달, review_targets 스냅샷 재사용,
    supastore.assignees 가 assigned_at 도 반환하게 확장해 _assign_ts 왕복 통합. 15회 → 8회 이하.
- [ ] **P1-7 벤더 정적 파일 인메모리 gzip 캐시** · `serve.py:2823~2824`
  - 증상: 요청마다 open+read+gzip level6 재압축. 배포 직후엔 전 접속자가 약 450KB 를
    새로 받아 동일 압축이 반복됨.
  - 계획: (경로, mtime) 키 인메모리 캐시로 원본·gzip 바이트를 1회 생성 후 재사용.
    파일 약 20개·수 MB 라 메모리 부담 없음.

## P2 · 구조 변경(효과 큼 · 신중히)

- [ ] **P2-1 /raw 목록 응답 슬림화** · `reviewops.py:822~887`
  - 증상: 목록 행마다 body 전문 + item_meta/quality_meta 원본 + 평탄화 중복 사본 +
    entities_scored(행당 재계산)를 실어 기본 2000건(딥링크 3000건) 전송. gzip 전 4~10MB.
    다인 검수 중 SSE feedback 이벤트마다 4초 스로틀로 전체 재조회(loadRawThrottled).
  - 계획: 표에 실제 쓰는 필드만 내려주는 슬림 프로젝션을 기본으로 하고, 상세는
    해시 단건 라우트로 분리. 목록 페이로드 약 10~20배 축소. 프런트(app-00·02·04 조각)
    동시 수정 필요라 범위가 큼.
- [ ] **P2-2 검수 표 x-for 표시 캡** · `prism/ui/14-golden.html:504`, `app-02-_afterverdict.js:36`
  - 증상: rawFiltered 2000~3000행을 캡 없이 전부 DOM 렌더(행마다 중첩 x-for 2개).
    보이는 건 스크롤 박스 10여 행뿐. getter 다중 참조로 필터·정렬도 반복 평가.
  - 계획: `rawFiltered.slice(0, rawShown)`(초기 200 · 더 보기 증분) + 계산 결과를 지역
    변수에 담아 getter 재평가 제거. 크루 탭 표시 캡 200 과 동일 규약.
- [ ] **P2-3 SPA HTML 사전압축 + ETag 304** · `serve.py:2624~2635, 2876`
  - 증상: 부팅당 정적인 617KB HTML 을 매 요청 gzip 재압축(실측 7ms) · no-store 라
    재방문에도 135KB 전량 재전송.
  - 계획: 부팅 시 1회 압축해 캐시, Cache-Control 을 no-cache 로 바꾸고
    ETag=_BOOT_ID + If-None-Match 304 추가. 항상 재검증하므로 옛 페이지 캐시 방지
    목적(WKWebView)은 유지됨. /m 동일 적용.
- [ ] **P2-4 head 앱 스크립트 defer** · `prism/ui/00-head.html:45~59`
  - 증상: app 조각 15개 약 450KB 가 head 동기 로드로 본문 파싱·첫 페인트를 차단.
    조각은 PRISM_APP_PARTS 팩토리 등록만 하고 소비는 alpine:init 시점이라 동기 불필요.
  - 계획: 조각·로더 script 태그에 defer(문서 순서 실행 보장으로 조각→로더→alpine 순서
    유지). 인라인 툴팁 스크립트는 그대로. 적용 후 정적 데모(make_demo) 경로도 확인할 것.

## P3 · 장기 구조 과제

- [ ] **P3-1 feedback·assignments 전량 스캔 상수화** · `supastore.py:756, 281, 295`
  - 증상: _all_feedback(limit 50000)·assignees·assignment_times 가 팀 필터 외 조건 없이
    전 행을 받아 파이썬 집계. 거의 모든 핫패스가 이 위에 있고 행 수 증가에 선형으로 느려짐.
  - 계획: 1단계 feedback_stats·gold_stats·assignment_load 를 PostgREST 집계/뷰/RPC 로
    이관(행 전송 제거). 2단계 feedback_map 호출부에 since_ts·hash 필터 인자.
  - 유의: supabase 스키마 변경(마이그레이션) 필요 · sqlite Store 와 계약 이원화 관리 필요.
    착수 전 사용자와 범위 협의할 것.

## 정리 보류 건(2026-07-28 스윕에서 의도적으로 남김)

제품 판단이 필요해 자동 적용에서 제외한 것들. 지우려면 사용자 결정이 먼저다.

- **/eval-rubric-cancel 라우트**(`serve.py`, `evalops.py:453`): 호출 UI 가 없지만 루브릭
  채점 중단 메커니즘(_RUBRIC_CANCEL·cancelled 분기)의 일부. 중단 버튼을 붙이든 메커니즘째
  걷어내든 한쪽으로 정리 필요.
- **mediaext T2·T3 트랙**(transcribe/visual): 운영 미사용이지만 설정으로 활성화 가능한
  기능 트랙. 폐기 여부는 미디어 로드맵 결정 사항.
- **홈 위젯 캔버스**(`ui/01-home.html` x-show="false") · **상단바 위젯 추가 드롭다운**
  (`ui/00-head.html` addmenu) · **#grid 위젯 편집 IIFE**(`ui/21-tail.html`): 위젯 대시보드
  기능 일괄 잔재. 복원 계획이 없으면 세 건을 한 PR 로 함께 제거하는 것이 맞다.
- **make_demo report_stub_coverage regex 무력화**(`scripts/make_demo.py:426`): 라우트 추출
  정규식이 0건 매칭이라 검사가 헛돎. 삭제가 아니라 수리(현행 라우트 테이블 형식 반영)가
  필요한 건이라 별도 과제로 남김.
