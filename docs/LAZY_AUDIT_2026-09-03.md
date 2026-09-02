# 과잉 설계 감사 2026-09-03 (prism-lazy · ponytail 사다리 기준)

ponytail 감사 스킬을 저장소 전체에 적용한 결과. 정확성·보안·성능은 범위 밖이고,
"지울 수 있는 것·표준 라이브러리로 바꿀 것·브라우저 기본으로 바꿀 것" 만 본다.
전부 실제 라인을 확인한 건이며, 고치지는 않았다. 총 추정 절감 약 1,600줄(의존성 변동 0).

태그: `delete` 죽은 코드 · `stdlib` 표준 라이브러리가 해 줌 · `native` 브라우저·SQLite 가 해 줌 ·
`yagni` 구현 하나뿐인 추상화 · `shrink` 같은 논리를 더 짧게.

## 1. 서버·도메인 모듈(serve.py · *ops.py) · 약 -290줄

| 태그 | 자를 것 | 대신할 것 | 위치 |
|---|---|---|---|
| yagni | `hasattr(st, "x")` 방어 177건 중 153건은 store·supastore 양쪽에 다 있는 메서드 | `st.x(...)` 직접 호출. supabase 전용 13개만 남김 | serve.py:2019 · reviewops 43건 · learnops 33건 · adminops 18건 · evalops 13건 |
| shrink | `apply_config` 232줄: has_* 플래그 14개 게이트 · 키 저장 3벌 · 스탬프 블록 2회 | 무조건 `Config.load()` · (data키, env, 경로) 표 루프 · `str(x or "").strip()` | serve.py:1163-1310 |
| shrink | `self._send(4xx, json.dumps({"error":…}))` 2줄 48회 | `h._err(code, msg)` | serve.py:3346-3392 외 |
| delete | 참조 0 재수출 별칭 10줄 | 없음 | serve.py:100-211 |
| yagni | ops → serve → 다른 ops 이중 홉 `_SV.<별칭>` 39건 | 소유 모듈 직접 import(순환 없음 확인) | crewops.py:354,1388 · learnops · boardops · mcpkeys |
| stdlib | 그레고리력 손계산 `_days_from_civil`/`_civil_from_days` | `date.fromisoformat().toordinal()` / `date.fromordinal().isoformat()` | weekops.py:30-55 |
| stdlib | `_parse_multipart` + `_kv` 손파서 | `email.parser.BytesParser` + `get_param("name", header="content-disposition")` | serve.py:407-441 |
| shrink | `/models` GET 파싱 3벌(`_router_models`·`_solar_models`·`ping_router`) | `_get_models(url, key, timeout)` 하나 | serve.py:1411,1431,1474 |
| shrink | 손수 만든 TTL 캐시 4벌(같은 만료·정리 패턴) | `_ttl_get`/`_ttl_put` 두 함수 | serve.py:342,744,883,1068 |
| shrink | `_agg_cached` 와 `_agg_cached_store` 는 weakref 한 줄 차이 | `st=None` 인자로 하나 | serve.py:354,366 |
| shrink | `_page_versioned`·`_mpage_versioned` 이중 캐시 | `_page_payload(mobile)` 안으로 | serve.py:3525-3562 |
| shrink | `_assist_candidates`·`_draft_judge_candidates` 복사본 | `_reachable_candidates(opts, cur)` | serve.py:987,1005 |
| delete | `_MODEL_FAMILY_PREFIX` 는 modelmeta 것의 부분집합 | `MM.family(pid)` | serve.py:737,798 |
| shrink | Bearer 토큰 파싱 3곳 | `_bearer_token()` | serve.py:1701,3317,3322 |
| delete | crewops `_epoch`·`_row_key` 되돌림 래퍼 · `_legacy_key_path` 홈 폴백(볼륨 이관 확인 후) | 없음 | crewops.py:354,1388 · serve.py:667-694 |

유지: evalops 루프 3개(종료 조건 다름) · dashops 롤업 2개(원장 구조 다름) · 1~3줄 라우트 핸들러(테이블 계약) · page.py.

## 2. 저장·LLM 계층(store · supastore · llm · harness 등) · 약 -520줄(운영 미도달 경로 보류 시 -135)

| 태그 | 자를 것 | 대신할 것 | 위치 |
|---|---|---|---|
| yagni | 임베딩 사전필터·YELLOW 게이트·퓨샷 경로 전체. 운영 진입점은 emb·prefilter·fewshot 을 안 넘기고 CLI 플래그로만 도달(README 미문서) | 없음(테스트 8곳 동반 수정) | classify.py:61 · fewshot.py · model_guides.py:5-47 · harness.py:74-149 · cli.py · config.py:64-65 · 약 -220 |
| yagni | mediaext 오디오·비주얼 개별 트랙(운영 호출 0 · 테스트만) | `cap_frames` 만 유지. 리드 판단 필요 | mediaext.py:94-162,170-281 · 약 -165 |
| yagni | config 모델 옵션·선택 함수 4개가 본문 동일 | `_model_options(default)` · `_pick_model(cfg, field, default)` | config.py:211-262 · -30 |
| stdlib | `llm._top_level_objects` 중괄호 스캐너 26줄 | `json.JSONDecoder().raw_decode(s, i)` 반복 8줄 | llm.py:465-490 |
| delete | Config 죽은 필드(embed_url 등 3 · PRISM_EMBED_MODEL · legal_confidence/legal_red · Prices.embedding) | 없음 | config.py:59,66-67,75-79,170-171,189 |
| delete | 호출자 0 메서드: set_member_admin/super · stage_services 양쪽 · clear_assignees(테스트만) · `_blocked_quality` | 없음 | supastore.py:472,489,1944,316 · store.py:1319,1049 · harness.py:348 |
| shrink | 중복 헬퍼 4쌍(`_hash` · `_retry_after` · `_slug` · `_norm`) | 한 곳에 두고 import | embed.py:18 · entdict.py:303 · deployops.py:22 · mcpkeys.py:76 |
| stdlib | `d[k] = d.get(k, 0) + 1` 손 카운터 약 35곳 | `collections.Counter` | store.py:126 · supastore.py:1132 외 |
| stdlib | 시각 기반 의사난수 지터 | `random.uniform(-1, 1)` | ratelimit.py:45-52 |
| delete | 테스트만 쓰는 공개 함수(export_rows · router_models_url · yellow_count) | 없음(테스트 수정 동반) | entlabel.py:120 · imagext.py:117 · store.py:1931 |
| shrink | `_read_config_file` 8개 상한 LRU 정리 | dict 그대로(경로 1~2개) | config.py:281-284 |

메모: Store/SupabaseStore 메서드 200여 개는 거의 전부 호출자가 있어 죽은 코드가 아님. crewbot.py 는 cron 진입점이라 import 0 이어도 살아 있음.

## 3. 프론트(ui · vendor) · 테스트 구조 · 약 -790줄(테스트 -720 · JS -60 · CSS -10)

| 태그 | 자를 것 | 대신할 것 | 위치 |
|---|---|---|---|
| shrink | 테스트의 `orig = X.a / X.a = fake / addCleanup(setattr)` 3줄 145곳 | `mock.patch.object` 감싼 `_patch(tc, obj, name, val)` 한 줄 | tests/test_add_accumulate.py:72 외 |
| yagni | `_serve()`·`_with_store()`·`_store()` 를 40개 파일이 각자 정의(약 240줄) | `tests/_h.py` 한 곳 | tests/test_assign_audit.py:16 외 |
| delete | 146개 파일의 같은 `sys.path.insert(...)` | `pytest.ini` 의 `pythonpath = .` 두 줄 · unittest discover 는 이미 됨 | tests/* |
| yagni | `_isolate_cfg` 10개 파일 복제 · `_stub_extract` 5개 복제 | 헬퍼 모듈 한 곳 | tests/test_add_accumulate.py:58,66 외 |
| stdlib | urlopen 대역 `_FakeResp` 클래스 5개 복제 | `io.BytesIO(json.dumps(obj).encode())` | tests/test_audit_llm.py:28 외 |
| shrink | `_afetch(url, { headers: this._authHeaders() })` 116건(이미 `_afetchOnce` 가 붙임) | body 있을 때 Content-Type 기본값을 `_afetchOnce` 한 줄에 · 호출부 headers 제거 | vendor/app-03-opendetail.js:456-460 |
| delete | `exportBatchCsv` 가 기존 `_dl` 5줄을 다시 짬 | `this._dl('prism_results.csv', rows)` | vendor/app-08-copytext.js:327-332 |
| shrink | Blob 다운로드 4줄 4곳 | `_dlBlob(blob, name)` | vendor/app-05-costdata.js:316-338 · app-08:334 |
| shrink | `_afetch` 대신 raw `fetch` 23건(401 갱신 못 탐) | 전부 `_afetch` | app-01 · 02 · 04 · 05 · 06 · 08 · 03 |
| native | 손으로 짠 날짜 포맷 4개 | `toLocaleDateString('ko-KR', …)` · `toISOString().slice(0,16)` | app-16-mcpkeys.js:56 · app-12-crew.js:69,316 |
| native | `JSON.parse(JSON.stringify(x))` | `structuredClone(x)` | app-03:139 · app-07:138 |
| native | 인라인 `style="cursor:help"` 108개 | `[data-tip]:not(button):not(a){cursor:help}` 한 규칙 | app.css:170 · ui/*.html |
| shrink | 같은 인라인 flex 문자열 반복 · hintbox 패딩 5회 | `.hrow` 유틸 · 클래스 | app.css:357 · ui/19b · 20 |
| delete | 안 쓰는 CSS 클래스 5개 | 없음 | app.css:223,922,934 |

유지: `[data-tip]` 전역 툴팁 위임 · app.js 로더 · `-webkit-` 19건 · `[data-tip]::after` 숨김.

## 처리 순서 제안

1. 위험 0 · 절감 큰 것부터: 테스트 헬퍼 통합(-720) → hasattr 방어 제거(-40) → `_err` 헬퍼 → 재수출 별칭 삭제.
2. 표준 라이브러리 치환(weekops 달력 · multipart · JSON 복구 · Counter): 한 건씩 테스트 붙여서.
3. 판단 필요: 사전필터·퓨샷 경로(-220)와 mediaext 트랙(-165)은 되살릴 계획이 있으면 보류.
