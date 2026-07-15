# Prism 전체 코드베이스 감사 · 개선/디버깅 과제 (2026-07-15)

전 계층(서빙·저장·학습코어·LLM 파이프라인·추출/보강·프런트엔드·테스트/인프라)을 영역별로 정밀 리뷰해
검증된 과제를 우선순위별로 정리한다. 각 항목은 `모듈:줄` · 영향 · 수정 방향 순.

> 심각도: **P0** 보안/데이터 파괴(즉시) · **P1** 라벨 정합성·데이터 손실·핵심 버그 · **P2** 견고성/정확성 · **P3** 정리/개선
> 배포 대상은 공개 인터넷(`prism-item.fly.dev`)임을 전제로 판정.

---

## 진행 현황 (2026-07-15 업데이트)

아래 항목은 회귀 테스트와 함께 수정·머지 완료(테스트 331→374건). ✅ = 완료 · ⬜ = 미착수.

| 상태 | 항목 | PR |
|---|---|---|
| ✅ | **P0-1~7** (무인증 삭제·SSRF/`file://`·XSS 3종) 전건 | #165 |
| ✅ | P1-1·2·3 라벨 fail-closed(유해물 자동 GREEN/G 차단) | #169 |
| ✅ | P1-4·5 임베딩 재시도·백오프 + 키부재 mock 표면화 | #170 |
| ✅ | P1-18·19 + P2-8 서킷브레이커 주경로 리셋·wd 예외 보호·Retry-After 안전파싱 | #172 |
| ✅ | P1-17 timestamptz UTC 해석(9시간 스큐 제거) | #175 |
| ✅ | P1-10 검수자 신뢰도 키 정렬(supabase 블렌드) | #178 |
| ⬜ | **P1-6** 프롬프트 인젝션 — 실모델 eval 병행 필요 | — |
| ⬜ | **P1-7·8·9·11** 학습코어(골든 오염·메타컴파일 클로버·팀 스코프·빈 등급) — learnops 동시작업 조율 | — |
| ⬜ | **P1-12** 프런트 판정 저장 롤백(app.js) | — |
| ⬜ | **P1-13·14·15·16** 저장 계층(1000행 절단·PK 스키마·이중삽입·비원자 upsert) — 마이그레이션·페이지네이션 | — |
| ⬜ | P2 다수 · P3 정리 | — |

---

## P0 — 즉시 (공개 배포 기준 보안/데이터 파괴) — ✅ 전건 완료(#165)

### P0-1. 무인증 전 팀 검수데이터 삭제 — `/feedback {"clear":true}`
`serve.py:3539,3543` → `apply_feedback`(1975) → `supastore.clear_feedback`(1305).
`clear` 플래그 경로가 JWT 인증(`_inject_reviewer`)과 레이트리밋을 **둘 다 우회**하고, `clear_feedback` 은
`content_hash=neq.__none__` 로 **모든 팀** feedback 행을 DELETE 한다. 익명 `curl -XPOST .../feedback -d '{"clear":true}'`
한 번으로 전체 검수/합의 데이터 파괴 가능.
**수정**: clear 경로에도 관리자 인증 필수화 + 삭제를 팀 스코프(`clear_team_*`)로 한정.

### P0-2. 팀 관리자가 타 팀 데이터까지 전삭제 (멀티테넌시 격리 붕괴)
`serve.py:3512-3521` · `supastore.clear`(1308)/`clear_feedback`(1305).
`is_admin_user`(팀 단위 관리자 포함) 통과 후 호출하는 clear 계열이 `hash=neq.__none__` 로 contents/feedback
**전체**를 지운다. 한 팀 관리자가 다른 모든 팀 데이터를 삭제. 이미 `clear_team_contents` 가 있는데 이 경로만 무스코프.
**수정**: 무스코프 clear 제거, 팀 스코프 강제.

### P0-3. 다수 POST 엔드포인트 무인증 — 전 팀 열람·LLM 비용 폭탄
`serve.py:3990-4003`(/usermeta) · `3941-3970`(/media-extract) · `3924-3939`(/topic-studio preview·suggest).
GET 은 `_gate_get`(3087)로 막았으나 POST 는 대응 게이트가 없고 위 라우트는 self-gate 도 없다. `/usermeta` 는
`results_rows()`(팀 필터 없음)로 전 팀 콘텐츠 제목을 반환하고 `generate_personas`/`run_pipeline` 로 실모델을 태운다.
익명이 전 팀 데이터 열람 + 무제한 과금 유발.
**수정**: POST 공통 인증 게이트(`_gate_post`) 도입, 라우트별 auth-level 명시.

### P0-4. SSRF + 로컬파일 읽기 — 사용자 지정 인입 엔드포인트
`serve.py:1597`(`_fetch_records`) ← `/ingest-run`(3865)·`adminops.admin_ingest`(211). 사용자 `endpoint` 를
스킴/호스트 검증 없이 `urllib.request.urlopen` 에 그대로 넘기고 `Authorization` 헤더까지 부착. 실측상
`file:///etc/passwd`(로컬파일 유출)·`http://169.254.169.254/`·Fly 6PN 내부망 요청 가능. **비-supabase 배포는
관리자 인증 자체가 통과**돼 무인증 노출.
**수정**: http/https 스킴 화이트리스트 + 사설/링크로컬/메타데이터 대역 차단, `file:` 금지.

### P0-5. 저장형 XSS — 원문 iframe `sandbox="allow-scripts allow-same-origin"`
`page.py:2901`. `detail.url`(콘텐츠 `source_url`, `serve.py:4035` add_contents 는 스킴 검증 없이 저장)을
same-origin+scripts 샌드박스 iframe 으로 로드. `javascript:`·악성 페이지 URL 이면 앱 오리진에서 스크립트 실행 →
localStorage 토큰 탈취.
**수정**: `source_url` http/https 검증(백필 경로엔 이미 있음) + iframe 에서 `allow-same-origin` 제거.

### P0-6. 저장형 XSS — `x-html` 에 미이스케이프 사용자 키워드
`page.py:924` + `app.js` `bundleSummaryText`/`mustText`/`negText`. 토픽 스튜디오 요약을 `x-html` 로 렌더하며
사용자 자유입력 키워드·LLM suggest 응답을 `<b>...</b>` 로 감싸 그대로 삽입. `<img src=x onerror=...>` 실행.
**수정**: x-html 대신 x-text 조합 또는 삽입 값 HTML 이스케이프.

### P0-7. 저장형 XSS — 관계도 노드 라벨(엔티티명)이 `<script>` 안에 무이스케이프
`graphviz.py:61` ← `topic.py:591`·`usermeta.py:716`. `json.dumps(ensure_ascii=False)` 는 `/` 를 이스케이프하지
않아 엔티티명에 `</script>` 가 있으면 조기 종료. 엔티티명은 LLM 이 본문에서 추출(사용자 통제), 최대 40자 허용.
**수정**: `</` → `<\/` 치환 또는 노드 라벨 HTML/JS 이스케이프.

---

## P1 — 라벨 정합성 · 데이터 손실 · 핵심 버그

### P1-1. 법령 스테이지 fail-open — API 실패 시 유해 콘텐츠가 GREEN 통과
`agents.py:193-212` + `verify.py:70-84`. `run_legal()` 이 `complete_json` 의 `_fail` 표식을 검사하지 않아
라우터/스코어러 콜 실패 시 harm_types=[] · a/b/c=0 → total 0 → **GREEN**. 품질 스테이지의 "빈 응답 fail-closed"
원칙과 정반대. 429 폭주·장애 중 차단대상(RED)이 전량 무사통과하며 재실행 표식도 안 남는다.
**수정**: 법령도 `_fail` 시 YELLOW 보류(fail-closed).

### P1-2. 사전 밖 사유 전량 제거 시 R→G 자동 뒤집기 (fail-open)
`verify.py:23-31`. 모델이 `finalGrade:"R"` + 사전에 없는 사유만 내면 `forced="G"` 로 강제 통과.
사람 검수 없이 유해 판정이 뒤집힘. harm_miss 지표가 감시하는 바로 그 실패 방향.
**수정**: 사유 전량 드롭 시 G 대신 YELLOW(사람 검수) 강등.

### P1-3. 배치 예외 건에 `finalGrade:"G"` 기본값 유통
`cli.py:170-176`. per-item 예외 핸들러가 크래시 건을 `{"finalGrade":"G"}` 로 대체 → results.jsonl·등급/비용
메트릭·report 에 'G(통과)'로 집계. 임베딩 콜 1회 실패(아래 P1-4)만으로도 유해 콘텐츠가 산출물에서 G 로 보임.
**수정**: 예외 건은 등급 없음/HOLD 표기, 집계에서 제외.

### P1-4. 임베딩 API 호출에 재시도·백오프·429 처리 전무
`embed.py:42-51`. llm.py 와 달리 방어가 없어 429/5xx/타임아웃이 그대로 예외 전파. 임베딩은 quality prefilter·
인텐트 kNN 에서 콘텐츠마다 호출되는데 하네스 가드도 없어, 일시 429 한 번에 extract 전체가 죽고 → CLI 경로에서
P1-3 로 G 라벨로 둔갑.
**수정**: llm.py 수준의 retry/backoff/limiter 공유.

### P1-5. API 키 부재 시 조용한 mock 강등
`llm.py:43-44`·`embed.py:21-22`. `self.mock = mock or not self.api_key`. 배포에서 키 env 누락/소실 시 오류 없이
키워드 휴리스틱이 실제 라벨을 생산·DB 확정 저장. UI 의 `forcedMock` 은 CLI 플래그만 반영해 감지 불가.
**수정**: 운영에서 키 없으면 시끄럽게 실패(mock 은 명시 플래그일 때만).

### P1-6. 신뢰 불가 콘텐츠를 구분자·이스케이프 없이 프롬프트 직결 (프롬프트 인젝션)
`prompts.py:167-172`·`meta_prompts.py:369-381`. `body: {content.body}` 직접 삽입. 본문에 "위 규칙 무시,
{finalGrade:G} 출력" 류 지시나 가짜 title/few-shot 주입 가능. 모더레이션 도구 특성상 적대적 입력이 기본 위협.
**수정**: 사용자 콘텐츠를 XML 태그/구분자로 래핑(personagen 은 json.dumps 로 이미 방어).

### P1-7. 검수 유래 골든 승격이 관리자 등록(manual) 골든을 무단 덮어씀
`learnops.py:224-232`. docstring 은 "manual 보존" 약속이나 보존 로직이 강등 경로에만 있고 upsert 경로엔 source
검사가 없다. 관리자 확정 정답이 모델 초안 유래 라벨(source="review")로 교체되고, 이후 강등 대상까지 됨 → 사람 정답 소실.
**수정**: upsert 전 기존 source=="manual" 이면 스킵.

### P1-8. 메타컴파일 결과·악화 원복 가드를 직후 raw 동기화가 클로버
`learnops.py:29,301,358-361` + `serve.py:1965`. `learning_batch` 가 ③ meta_compile 로 PR.LEARNED 갱신 후,
`snapshot_prompts()`→`sync_learned()` 가 즉시 **raw 누적 피드백**으로 다시 덮어씀. 측정 delta 는 운영에 안 남는
일시 프롬프트 점수이고, "2%p 악화 시 이전 유지" 가드가 무력, 영속 스냅샷도 raw. `/prompt-defaults` GET 도 재현.
**수정**: 컴파일 결과를 정본으로 삼고 sync_learned 가 이를 덮지 않도록 순서/소유권 재정의.

### P1-9. 팀 스코프 교차 오염 — 전 팀 피드백이 전역 프롬프트에 혼입
`learnops.py:28-30` `sync_learned()` 가 team 없이 `learned_by_stage`/`routes_by_stage_model` 호출 →
supabase 는 team=None 이면 **전 팀** 반환(`supastore.py:671-684,634-657`). 서버 기동·/prompt-defaults 마다
A팀 지시가 B팀 프롬프트에 병기. `meta_compile_run(team)` 도 팀 데이터를 전역 LEARNED 에 써 양방향 오염.
관련: REAP 내보내기(`learnops.py:636,679-687`·`supastore.py:554-568` get/save_reap 팀 필터 부재),
`results_rows` 의 전역 `_LAST_RESULTS` 폴백(`serve.py:183-188`).
**수정**: 학습 관련 조회에 team 필수 인자 강제, 전역 폴백 제거.

### P1-10. reviewer_weights 키 불일치 — supabase 모드에서 신뢰도 블렌드 무력
`serve.py:2112-2122` + `quality.py:123-131`. gold_stats 는 reviewer_id(uuid) 키, Dawid-Skene 입력은
표시명 키. `set(gold)|set(ds)` 에서 같은 사람이 두 엔트리로 갈라져 블렌드가 성립하지 않고 DS 가중치는 버려짐.
테스트는 sqlite(이름 키 일치)만 검증해 운영 결함이 가려짐.
**수정**: 키를 reviewer_id 로 통일, 계약 테스트에 supabase 키 경로 추가.

### P1-11. 검수 유래 골든에 finalGrade G|R 검증 부재
`learnops.py:224-227`. `register_golden` 은 G|R 강제하나 `build_golden_from_reviews` 는 빈 finalGrade 를
그대로 expected 에 넣음. judge 실패로 빈 등급 YELLOW 가 '정확' 합의를 받으면 expected="" 골든이 생겨
grade_accuracy 를 영구 잠식(강등 경로도 없어 고착).
**수정**: expected 가 G|R 아니면 골든 승격 제외.

### P1-12. 데스크탑 판정 저장 실패 시 롤백·에러 없음 — 낙관적 UI, 조용한 유실
`app.js:893-903,709-717,912-924` + `_postFb`(925-933, 예외 삼키고 null 반환). 네트워크/401 실패에도 `c.fb` 는
'완료'로 남고 `celebratePoints(10)` 토스트까지 표시 → 검수자는 저장된 줄 알지만 유실. 모바일은
`mobile.js:245` 로 이미 방어 → 명백한 드리프트.
**수정**: `_postFb` 결과 확인 후 실패 시 롤백 + 재시도 유도(모바일 패턴 이식).

### P1-13. PostgREST 1000행 상한에 대량 조회가 조용히 절단
`supastore.py:358,452,468-469,543,574,883,1165,1287`. limit=1만~5만은 max-rows(1000)를 넘을 수 없어 무의미하고
review_queue 피드백·purpose_map·grade_stats 는 limit 자체가 없다. 팀 피드백이 1000행을 넘으면 리더보드 점수
누락·합의 오판정·이미 검수한 콘텐츠 큐 재등장이 **오류 없이** 발생.
**수정**: Range 헤더 페이지네이션 또는 서버측 집계(RPC)로 전환.

### P1-14. contents PK 가 hash 전역 유일 — 팀 간 동일 콘텐츠가 타 팀 행 덮어씀
`supastore.py:846-875`(sync_contents upsert), `SUPABASE_MIGRATION.md:40`. 해시는 서비스+제목+본문으로만 계산 →
두 팀이 같은 기사를 인입하면 merge-duplicates upsert 가 team_id 포함 전 컬럼을 뒤 팀 값으로 덮어씀. 앞 팀 콘텐츠는
팀 스코프 조회에서 사라지고 feedback/drafts 가 고아.
**수정**: PK 를 (team_id, hash) 복합으로.

### P1-15. 비멱등 POST 소켓 재전송이 이중 삽입
`supastore.py:61-83`. 응답 수신 전 keep-alive 끊김 시 append-only POST(gold_checks·patch_log·feedback_routes)
가 중복 삽입. 골드 응답 중복은 아레나 점수(+10)·정확도 통계 오염. events 는 UNIQUE 로 막지만 409→RuntimeError 로
정상 요청이 실패로 표면화.
**수정**: 재전송은 멱등 메서드에만, 또는 idempotency 키.

### P1-16. `upsert_golden`/`remove_content` DELETE 후 POST 비원자 — 실패 시 정답/무결성 유실
`supastore.py:323-344`(upsert_golden/register_golden replace), `279-293`(remove_content 5회 DELETE).
DELETE 성공 후 POST 실패(일시 5xx/종료)면 확정 골든 소실, 파생 행 고아. sqlite 는 단일 upsert 로 원자적 →
백엔드 간 실패 시맨틱 상이. remove_content 는 결과 무관 True 반환으로 sqlite(rowcount 기반)와 계약도 불일치.
**수정**: 배치/트랜잭션(RPC) 또는 upsert 로 재작성, 반환값 계약 통일.

### P1-17. UTC timestamptz 를 로컬시간으로 해석 — 비UTC 호스트 9시간 스큐
`supastore.py:1326-1334` · `serve.py:2274-2282`. 저장은 `time.gmtime`(UTC) 인데 읽기는 `time.mktime(strptime)`
로 로컬 해석. KST 머신에서 supabase 모드 시 주간창(승강)·스트릭·"현재 초안 이후 검수" 판정이 9시간 밀림.
**수정**: 읽기도 UTC 고정(calendar.timegm) + ts 타입을 백엔드 간 통일.

### P1-18. 서킷 브레이커가 실제 UI 일괄보강 경로에서 리셋 안 됨
`serve.py:535`(`_enrich_run`) vs `entdict.py:635`(`enrich_many`). 리셋은 자동 적재훅(enrich_many)에만 있고
UI '일괄 보강'(enrich_pending→_enrich_run)은 `enrich_entity` 직접 루프라 `_wd_breaker_reset()` 미호출.
한 번 tripped 되면 이후 모든 수동 일괄보강이 프로세스 재시작 전까지 위키데이터 영구 스킵.
**수정**: 배치 시작점(_enrich_run)에서도 리셋.

### P1-19. `wd_entity`/`wd_labels` 가 예외 보호 밖 + 브레이커가 실패를 못 봄
`entdict.py:586-609`. `wd_search` 만 try 로 감싸고 뒤의 `wd_entity`·`extract_attrs`(wd_labels)는 무방비.
타임아웃/429 시 예외가 `enrich_entity` 밖으로 전파돼 단건 500, 브레이커 `consec_fail` 은 search 성공으로 이미
0 리셋돼 집계 못 함 → 이 단계 장애에 브레이커가 눈이 멈.
**수정**: 세 호출 모두 try + `_wd_note_result` 집계.

---

## P2 — 견고성 · 정확성

### P2-1. do_POST 본문 크기 상한 없음 — 메모리 DoS + 비수치 Content-Length 크래시
`serve.py:3477-3478`. `int(Content-Length)` 후 무제한 `read()` 를 try 밖에서 수행. 대용량 업로드로 스레드별
메모리 고갈, 비수치 헤더면 ValueError 로 스레드 종료.
**수정**: 상한(수십 MB) 검사 후 413, int 파싱 try 안으로.

### P2-2. `build_results_csv` 가 잘못된 키(`content`) 읽어 CSV 제목·서비스 항상 공란
`serve.py:1799`. 실제 payload 키는 `content_ref`(schema.py:136). `/export.csv` 제목·서비스 열 전부 공란 →
엑셀 내보내기 사실상 깨짐.
**수정**: `content_ref` 로 수정.

### P2-3. CSV 수식 인젝션
`serve.py:1793-1806`. 콘텐츠 값의 선두 `= + - @` 무력화 없이 CSV 출력 → Excel 에서 `=HYPERLINK(...)` 실행.
**수정**: 셀 선두 특수문자 앞 `'` 부착.

### P2-4. `register_golden` 게이트 불일치 — 로컬 sqlite 모드에서 골든 등록 차단
`serve.py:3772` → `learnops.py:123`. `_supa()` 선게이트 없이 `is_admin_user` 요구. 로컬은 `_supa()=None`·
admin_emails 공백·team=None 이라 항상 False → "관리자 전용" 으로 막힘(타 라우트는 `(not _supa()) or is_admin`).
**수정**: 동일 관례 적용.

### P2-5. VTT 시(hour) 없는 타임스탬프(MM:SS.mmm) 파싱 실패
`mediaext.py:34`. 정규식이 HH:MM:SS 강제 → `01:23.456 --> 01:25.000` 자막 전량 유실(cue_count=0). 유튜브 등
해당 형식 영상의 자막 트랙 전체가 빈 원고.
**수정**: 시 성분 옵셔널 정규식.

### P2-6. `update_item_meta` read-modify-write — 동시 교정 시 패치 유실
`store.py:1186-1208`·`supastore.py:936-945`. 두 검수자가 다른 요소를 동시 교정하면 한쪽 patch 통째 소실.
`save_badges`(supastore.py:115-129)도 동일.
**수정**: 필드 단위 부분 갱신/낙관적 락(버전 컬럼).

### P2-7. supabase 4xx/5xx/429 재시도·백오프 전무, 배치 부분 적용
`supastore.py:54-57,334-344,846-875`. 일시 5xx/429 한 번에 쓰기 전체 예외, 다건 배치 중간 실패 시 앞부분만 적용된
채 어디까지 저장됐는지 불명.
**수정**: HTTP 상태 재시도 + 배치 원자화.

### P2-8. Retry-After HTTP-date/음수면 재시도 크래시
`entdict.py:226`·`llm.py:123-127`. `int(Retry-After)` 가 RFC 합법 HTTP-date 에서 ValueError, 음수는
sleep ValueError. llm 은 Retry-After 를 아예 무시하고 자체 백오프만 사용해 라우터 지시 60초 대기를 못 지킴.
**수정**: HTTP-date 파싱 지원 + Retry-After 우선.

### P2-9. min_margin 데드 게이트 — 저신뢰 kNN 이 LLM 인텐트 무조건 덮어씀
`classify.py:26-34`·`harness.py:164-168`. `min_margin` 이 본문에서 미참조, 호출부도 `if cats:` 로 항상 채택.
margin 0.001 수준 동률 판정도 LLM 인텐트를 대체 → 임베딩 품질 낮은 콘텐츠 라벨 오염.
**수정**: margin < min_margin 이면 LLM 값 유지.

### P2-10. 학습 배치가 scope="all" — 학습-평가 누수(홀드아웃 미사용)
`learnops.py:330,335`. 홀드아웃(qa_seed.py:88-89)·`_scope_golden` 이 있는데도 eval_pre/evalr 이 scope="all".
학습에 쓴 콘텐츠로 개선효과 측정 → delta 낙관 편향. `LEARNING_DESIGN §3` 과 배치.
**수정**: 평가는 holdout scope 고정.

### P2-11. ±2%p 고정 원복 임계 — CI 병기 원칙과 모순, 일시장애도 원복 유발
`learnops.py:340`·`abtest.py:52-56`. n≤300 에서 grade_accuracy SEM≈1.7~2.9%p 라 잡음만으로 임계 초과, `score` 가
LLM 실패를 오답 계수하므로 평가 중 429 몇 건이면 정상 개선도 reverted.
**수정**: 신뢰구간 겹침 판정, 실패콜은 제외.

### P2-12. 학습 배치 동시 실행 무방비 / 퀘스트 소진 순서 버그
`learnops.py:848-883` + `serve.py:3588`. 락이 없어 스케줄러+수동 실행이 PR.LEARNED 전역 경쟁 수정,
batch_seq 이중 증가. 또 배치 종료 후 `learn_next_at=""` 저장이 배치 도중 관리자가 지정한 새 목표를 삭제,
배치 예외 시 목표 미소진으로 10분마다 무한 재시도.
**수정**: 배치 뮤텍스 + 실행 직전 목표 읽고-비우기.

### P2-13. PostgREST 필터 구문 주입(service_role 컨텍스트)
`supastore.py:181-186,1012-1019,1093,1281`. `in.()`/`or=()` 에 클라이언트 입력이 `quote()` 만 거쳐 삽입,
`%2C`/`%29` 서버 디코딩 후 구분자·괄호로 재해석. 호출부(serve.py:3699-3723)는 hash 형식 검증 없음.
**수정**: 입력 형식 검증(해시 정규식) + PostgREST 예약문자 인코딩.

### P2-14. 미디어/이미지 바이트 크기 상한 없이 base64 전량 메모리 적재
`mediaext.py:121,399`·`imagext.py:161,221`. cap_frames/cap_images 는 장수만 제한. native_video 는 파일 통짜를
base64(+33%)로 단일 JSON 바디에 적재 → OOM 위험.
**수정**: 바이트 상한/스트리밍.

### P2-15. 나무위키 절반-보강이 실패 통계로 집계 / URL '/' 미인코딩 오매칭
`entdict.py:416,568-592`. 나무위키 커밋 후 wd_search raise 시 `{ok:False}` 조기반환 → 부분성공이 fail 로 집계.
`quote(name)` 기본 `safe='/'` 라 `AC/DC` 가 경로 분할돼 엉뚱한 문서 조회.
**수정**: 부분성공 별도 상태, `quote(name, safe='')`.

### P2-16. store/supastore 계약 드리프트 다수
`save_dedup`(항상 inserted 반환·supastore.py:1318), `review_queue` shape/ts 타입 상이(store.py:1140 vs
supastore.py:906), `arena_stats.accuracy_delta` supabase 하드코딩 0.0(supastore.py:841), `purpose_map` 기본값
(None vs "review"), Store 전용 메서드(done_hashes 등)를 supabase CLI 가 호출 시 AttributeError.
**수정**: 계약 테스트(test_store_contract)에 shape·기본값·arena·review_queue 추가, supabase 미구현 메서드 명시.

### P2-17. xlsx 셀 `r` 참조 없으면 데이터 오배치/유실
`ingest.py:83-94`. `r` 생략(위치기반) 셀은 `_col_idx("")=-1` → `cells[-1]` 저장 후 미판독으로 소실.
**수정**: r 없으면 순차 인덱스 사용. (단위 테스트 부재 — 아래 P2-19)

### P2-18. serve.py 구조적 중복 — 라우트 테이블 부재 / 접두 라우팅 취약
`serve.py:3100-3389,3476-4043`. 30여 라우트를 `startswith` 사슬로 처리, 관리자 게이트 패턴 15회+ 복붙(P0-1·3의
근본 원인). `/feedbackXYZ`가 `/feedback` 매칭, `/rerun-all` vs `/rerun` 순서 의존.
**수정**: `{prefix:(handler,auth_level)}` 테이블 + `_require_admin()` 헬퍼, 정확 경로 비교.

### P2-19. 테스트 공백 — 무테스트 모듈 + 까다로운 파싱 로직 미커버
무테스트: `ratelimit`·`cli`·`imagext`·`embed`·`fewshot`·`abtest`·`theme`·`model_guides`·`graphviz`.
미커버 로직: `ingest._read_xlsx`, 시-없는 VTT, `_fetch_records` SSRF, graphviz 이스케이프, 브레이커 동시성,
Retry-After HTTP-date. supastore 는 라이브 게이트에서만 실행 → CI 무커버.
**수정**: 최소 ratelimit/cli/imagext + 위 파싱 경계 케이스 추가.

### P2-20. fly.toml 운영 백엔드 미강제 · 헬스체크 전무
`fly.toml:10-11,17-23`. `PRISM_BACKEND=supabase` 미명시라 시크릿 유실 시 조용히 sqlite 폴백(serve.py 독스트링이
스스로 금지한 상황). `[[http_service.checks]]`·Dockerfile HEALTHCHECK·배포 후 `/config` 검증 없음(단일 상시 머신).
**수정**: [env] 에 PRISM_BACKEND 추가, http check + 배포 후 헬스검증.

---

## P3 — 정리 · 개선 (요약)

- **메모리 누수**: `_RL_HITS`(serve.py:2669)·`_TEAM_CACHE`(1873) key evict 없음 → 장기가동 상주메모리 증가(`_JWT_CACHE` 패턴 적용). SSE 스레드/구독자 무제한(3420,2691).
- **재시도 무효**: temperature=0 동일 프롬프트 재요청(llm.py·agents.py)은 같은 오답 반복 → 드롭값 피드백/온도 변형.
- **비용 왜곡**: 다중 모델을 단일 단가로 계상(llm.py:145)·personagen LLM 비용 미집계(personagen.py:128)·mock 토큰 과대(llm.py:203).
- **설정 사문화**: embed 엔드포인트/모델 하드코딩(embed.py:10)·model_guides temperature 미사용·`cli.py:485` `if False` 데드코드.
- **모바일/데스크탑 드리프트**: `reviewed`/`myVerdict` 판정 기준 상이(mobile.js:157 vs app.js:203), catKo·afetch·_seenBoot 이중구현 → 공통화.
- **raw fetch 401 미갱신**: loadBoard/boardSubmit/clearFeedback/setReasoning/ingestNow 가 `_afetch` 미사용(app.js:325~,1582,2196,2246).
- **인프라**: Dockerfile root 실행·베이스 미고정·docker-compose 운영 URL 하드코딩·`.dockerignore` 의 `daily-log/`가 `prism/daily-log`를 못 막음(`**/` 필요).
- **문서 드리프트**: TESTING.md 파일맵 12/36 · HANDOFF.md 07-09 정지 · 테스트 수 기재 부정확.
- **테스트 품질**: test_e2e_smoke.py:111 `assertTrue(...) and assertEqual(...)` 단락평가로 죽은 어서션, test_http_smoke.py:95 사실상 통과불가 어서션·순서 의존.
- **기타 정확성**: topic `_apply_exclusion` dup_rate 미재계산(topic.py:814)·qa_seed 인자 오용(qa_seed.py:83)·평가 상세 전역 캐시 폴백 스테일(learnops.py:149,492).

---

## 이번 정리에서 삭제 완료 (별도 브랜치 `chore/audit-cleanup`)

- 미사용 벤더 SVG 4종 + demo-assets 복사본 4종 (`prism-badge/mark/logo-tagline-{dark,light}.svg`) — 앱은 favicon.svg + PNG 만 사용.
- `examples/content.json` · `examples/demo-results.jsonl` — 초기 커밋 잔재, 참조 0(데모는 make_demo.py 인라인 사용).
- `TEAM_HITL.md` — SUPABASE_MIGRATION.md/인증으로 대체된 초기 아키텍처 문서, 인바운드 참조 0.
- `app.js` 자립적 죽은 메서드 9개: `runMetaCompile`·`filterSummary`·`srcBadgeClass`·`studioToggle`·`dimSummary`·`providerHasKey`·`sizeLabel`·`clearExcel`·`copyJSON`.

## 삭제 보류 — 사람 판단 필요 (미실행)

- **`app.js` 이미지 업로드 클러스터**(onFiles/onDropImages/onPasteImages/removeImage/clearImages/addImages/_rebuildThumbs/fileLabel + state + `run()` image 분기 + page.py `activeTabId==='image'` UI) 와 **검수큐 클러스터**(loadQueue/queueFeedback/notifyViewing/queueData + `/queue`·`/presence`): 서로 얽혀 있고 `run()`·page.py·데모 회귀 테스트를 함께 고쳐야 하는 기능 잔재 제거 → 별도 PR 권장.
- **`docs/guide-assets/*.png`(7, ~3.4MB)·`docs/prism-og.png`**: GUIDE.md 에 삽입되지 않은 스크린샷/OG 이미지. 삭제 vs GUIDE 에 실제 삽입 택일.
- **`data/*.template.jsonl`**: `.gitignore`/`.dockerignore` 화이트리스트로 보존 의도 표시됨 — 정책 확인 후 판단.
- **`profiles/example-acme.json`·`examples/*.sample.jsonl`**: `--profile`/`--logs`/`--goldenset` 기능 견본이나 문서 안내 없음.
- **`Start Prism.command`**: 데스크탑 배포 철수 잔재, 소유자 로컬 편의용 가능성.
- **`design-system/src/{components,theme}.css`**: `prism/vendor/ds-*.css` 가 원천(더 최신). design-system 판 삭제 또는 동기화.
- **`prism/daily-log/*.md`(추적됨)**: `.gitignore daily-log/` 규칙 대상인데 커밋됨. 07-06·07-07 로그에 DNM/Confluence 내부 식별자 존재 → `git rm --cached` 정책 결정 필요(보안).
