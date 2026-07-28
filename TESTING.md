# Prism 테스트 구조

실행: `python3 -m pytest tests/ -q` (stdlib unittest, 외부 의존성 0)

## 구조 판단과 근거

Prism의 검증 대상은 성격이 다른 세 층이므로, 테스트도 세 층으로 나눈다.

| 층 | 파일 | 검증 대상 | 왜 이 방식인가 |
|---|---|---|---|
| 1. 단위·계약 | `test_dictionaries` `test_quality_stats` `test_store` `test_gamification` `test_extraction` `test_learning_loop` `test_admin` `test_serve_views` | 순수 함수·스토어·계약 로직 (사전 정규화, 통계, 레벨 커브, 4호출 검증, 권한) | 결정론 입출력이라 고전적 example 기반 단위 테스트가 가장 저렴하고 정확 |
| 2. 메타모픽·속성 | `test_metamorphic` | 오라클을 만들기 어려운 변환 계층 (정규화 멱등성, 서비스명 동치, 레벨 단조성, mock 파이프라인 INV/DIR, 프롬프트 합성 순수성) | 정답쌍 없이 "변환 전후 관계"로 결함을 잡는다. 케이스당 수백 조합을 고정 시드 난수로 탐색 |
| 3. 통합·e2e | `test_http_smoke` | 실제 HTTP 서버를 스레드로 부팅해 화면 전 버튼의 엔드포인트 계약 + 검수→정답셋 승격 e2e | 프론트가 호출하는 실제 경로·파라미터·응답 형태를 검증. UI 회귀의 대부분이 이 층에서 잡힘 |
| 4. 이중 구현 계약 | `test_store_contract` | sqlite(Store)와 supabase(SupabaseStore)가 같은 의미로 동작해야 하는 메서드(용도·평가 판정·리포트·골든·초안 이력·라우트 계층) | 동일 시나리오 Mixin 을 두 구현에 실행해 이중 구현 표류를 잡는다. 라이브는 `PRISM_TEST_SUPABASE=1`(일회용 계정·팀 생성 후 전부 정리), CI 기본은 skip |

세 층 밖의 수동 확인은 `QA_CHECKLIST.md`(QA 빌드 기준 기대값)가 담당한다.

## 방법론 근거 (논문)

- **메타모픽 테스트(MR)**: 오라클 문제를 변환 관계로 우회하는 표준 기법.
  Segura et al., "A Survey on Metamorphic Testing", IEEE TSE 2016.
  LLM/NLP 적용은 "Metamorphic Testing of Large Language Models for NLP" (ICSME 2025,
  arXiv:2511.02108)가 NLP 과제 MR 191종 카탈로그로 정리. 본 저장소는 그중 결정론 계층에
  적용 가능한 유형(동치 입력, 멱등성, 치역 제약, 격리)을 채택했다.
- **행동 테스트 유형(INV/DIR)**: Ribeiro et al., "Beyond Accuracy: Behavioral Testing of
  NLP Models with CheckList", ACL 2020. 불변 변환(공백 추가)과 방향성 변환(위험 신호 추가)을
  mock 파이프라인의 등급 계약에 적용했다.
- **속성 기반 테스트(PBT)**: MacIver et al., "Hypothesis: A new approach to property-based
  testing", JOSS 2019. 저장소의 의존성 0 원칙에 따라 라이브러리 없이 고정 시드 난수
  (SEED 상수)로 축소 적용해 재현성을 보장한다. 산업 적용 실태는 "Property-Based Testing
  in Practice" (ICSE 2024) 참조.
- **LLM 실호출 평가**: 비결정 LLM 출력 자체의 품질 평가는 테스트가 아니라 제품 기능
  (평가 메뉴: 정답셋 일치율·신뢰구간·건별 판정)으로 다룬다. 테스트는 결정론 계층
  (mock·계약·검증 로직)까지만 책임진다.

## 파일 맵

아래는 층별 핵심 파일 발췌다(전체 아님). 실제 테스트 파일은 97개(2026-07-28 기준)이므로
위치를 찾을 때는 `ls tests/` 로 확인한다.

```
tests/
  test_dictionaries.py    사전·스키마 계약 (정규화 · 인입 매핑 · 검증 화이트리스트)
  test_quality_stats.py   Krippendorff alpha · Dawid-Skene EM · 이항 CI
  test_store.py           스토어 계약 (학습 루프 테이블 · 용도 · 평가 판정)
  test_gamification.py    골드 문항 · 미션 · 품질 가중 점수 · 레벨 커브
  test_extraction.py      mock 하네스 · 계열 래퍼 · 분리형 4호출 (순차·차단·검증·라우팅)
  test_learning_loop.py   골든 누적 · 라우팅 · 학습 데이터 · QA 시드
  test_admin.py           권한 2단계 (운영/팀 관리자)
  test_serve_views.py     드릴다운 · 배지 영속 · 집계 캐시
  test_metamorphic.py     MR·속성 (멱등성 · 동치 · 단조성 · INV/DIR · 합성 순수성)
  test_http_smoke.py      HTTP 실부팅 스모크 + e2e (검수 → 정답셋 → 평가)
  test_store_contract.py  sqlite/supabase 동일 시나리오 계약 (라이브는 PRISM_TEST_SUPABASE=1)
  test_demo_js.py         정적 데모 스크립트 파스(node --check) + 필수 스텁 커버리지
```

## 작성 규칙

- 새 기능은 1층(계약 단위) 테스트를 기본으로 추가하고, 프론트가 호출하는 라우트가 생기면
  3층(`test_http_smoke`)에 버튼 계약을 추가한다.
- 변환·정규화 로직을 추가하면 2층에 멱등성/치역 MR을 함께 추가한다.
- 무작위를 쓰는 테스트는 반드시 고정 시드(SEED)로 재현 가능해야 한다.
- 테스트는 실제 `config.json`을 오염시키지 않는다 (`DEFAULT_CONFIG_PATH` 격리 패턴 참조:
  `test_http_smoke.setUpClass`).
