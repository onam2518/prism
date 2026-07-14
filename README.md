<div align="center">

# Prism

**콘텐츠 검수·평가 플랫폼 · 팀의 검수 합의가 정답셋(골든)이 되고, 정답셋이 모델 평가와 파인튜닝 소요서의 근거가 되는 구조**

![license](https://img.shields.io/badge/license-MIT-black?style=flat-square)
![python](https://img.shields.io/badge/python-3.8%2B-black?style=flat-square)
![deps](https://img.shields.io/badge/dependencies-0-black?style=flat-square)

**온라인 데모:** [통합 데모](https://onam2518.github.io/prism/demo.html)

[처음 사용자 가이드](GUIDE.md) · [QA 체크리스트](QA_CHECKLIST.md) · [개발 인수인계](HANDOFF.md) · [학습 설계](LEARNING_DESIGN.md)

</div>

```
콘텐츠 추가(수동·자동) → 모델 실행(초안 vN) → 팀 검수(판정·교정·게임화)
      → 정답셋(골든) 누적 → 평가(일치율·건별 판정·모델 A/B) → 학습데이터·소요서 → 핸드오프 번들(.zip)
```

> [!NOTE]
> 데모(`docs/demo.html`)와 `examples/`의 콘텐츠는 전부 합성 예시 · 실제 서비스 데이터 아님


## 🔍 무엇을 하는 도구인가

- LLM으로 콘텐츠 메타(리드문·엔티티·인텐트·카테고리·품질)를 뽑을 때의 진짜 문제는 "모델 결과를 누가, 어떻게 믿을 것인가"
- Prism의 답은 팀의 검수 합의

| 원리 | 내용 |
|---|---|
| 검수가 곧 데이터 | <ul><li>팀원이 초안을 정확/수정으로 판정하고 교정</li><li>합의된 결과가 정답셋(골든)으로 축적</li></ul> |
| 정답셋이 곧 기준 | <ul><li>쌓인 정답셋으로 모델·프롬프트 버전 평가 (일치율·신뢰구간)</li><li>모델 간 A/B 비교</li></ul> |
| 결과물이 곧 소요서 | 축적 현황과 논문 기준치를 대비해 SFT/DPO/판단근거 데이터셋과 파인튜닝 소요서(.md) 산출 |
| 지속 장치 | <ul><li>레벨·배지·미션·리그 등 게임화 탑재</li><li>개인 1만 건 검수 시 만렙(Lv.50)과 전 배지 완성 설계</li></ul> |


## ⚡ 빠른 시작

### 웹 (로컬 서버)

```bash
git clone https://github.com/onam2518/prism && cd prism
python3 -m prism.serve --mock          # http://localhost:8765 · 키 없이 모의 추출로 전 기능 체험
```

- 파이썬 표준 라이브러리만 사용 · `pip install` 없이 3.8+ 어디서나 동작
- 실제 모델 호출은 화면의 시스템 설정에서 API 키 등록 (Upstage Solar 직접 또는 통합 라우터)
- 옵션: `--port`(기본 8765), `--host`, `--mock`(키가 있어도 강제 모의)

### Docker (팀 공유 서버)

```bash
docker build -t prism .
docker run -p 8765:8765 -v prism-data:/data prism                      # 키 없으면 자동 mock
docker run -p 8765:8765 -e UPSTAGE_API_KEY=up_xxx -v prism-data:/data prism   # 실모델
docker compose up -d                                                   # 팀 상시 가동 (권장)
```

- LAN 팀원은 `http://<호스트IP>:8765` 접속으로 같은 검수 큐·피드백 공유
- 벤더 에셋(Tailwind/Alpine/Pretendard)은 `prism/vendor/`에 포함 · 컨테이너 오프라인 동작 가능
- 상세: [DOCKER.md](DOCKER.md)

### CLI (배치 추출)

```bash
python3 -m prism.cli extract --batch examples/contents.sample.jsonl --mock --out results.jsonl
python3 -m prism.cli report  --batch examples/contents.sample.jsonl --mock --out report.html
python3 -m prism.cli check   contents.xlsx          # 엑셀/CSV 입력의 동작 가능 여부 사전 판정
python3 -m prism.cli doctor                          # API·모델·임베딩·DB 연결 점검
```

- 화면 없이 파일 배치만 돌릴 때 사용
- 전체 명령(`extract`/`eval`/`ab`/`dashboard`/`topic`/`report`/`usage`/`tune` 등)은 `python3 -m prism.cli --help` 참조


## 🗂️ 화면 구성

| 메뉴 | 역할 |
|---|---|
| 홈 | 검수 진척율 · 내 캐릭터(레벨·배지·미션) · 주간 리그 · 팀 퀘스트 D-day |
| 콘텐츠 검수 | 검수 대상 목록(판정·교정) · 결과 비교(모델×버전 A/B) |
| 평가 | 평가 기준 설정 · 정답셋 일치율 · 불일치 건별 판정(채택/탈락) · 모델 A/B 비교 |
| 콘텐츠 관리* | STEP 1 콘텐츠 추가(수동·자동, 용도 지정) → STEP 2 모델 실행 → STEP 3 실행 큐 |
| 정답셋 관리* | 검수 목표(퀘스트) 생성·학습 반영 · 정답셋 목록 · 학습 데이터(SFT/DPO/노하우/소요서·핸드오프 번들) |
| 프롬프트 스튜디오* | 기준 계약(4호출 규칙, 읽기 전용) · 모델별 쿡북 래퍼 편집 · 최종 프롬프트 미리보기 |
| 사전·정책* / 실험실* / 팀 관리 / 시스템 설정* | 분류 사전 · 탐구 요소 · 멤버/초대코드 · 데이터/API 키 |

\* 관리자 메뉴 · 운영 관리자(허용목록)는 전체, 팀 관리자(생성자·위임)는 팀 관리만 표시


## 📖 핵심 개념

- **분리형 4호출**: 리드문 → 엔티티 → 인텐트 → 카테고리 순서의 개별 호출로 추출 · 호출 사이 사전 검증 수행, 호출별 상이 모델 지정 가능
- **용도(검수용/평가용)**: 콘텐츠 추가 시 지정 · 평가용은 검수 목록에서 제외되어 오염 없는 평가 전용 홀드아웃으로 보존
- **버전(v)**: 초안 버전 = 학습 반영 회차 + 1 · 검수 피드백이 반영 일시마다 프롬프트에 반영되고, 새 버전 재실행으로 개선 비교
- **검수 목표(팀 퀘스트)**: 관리자가 지정한 반영 일시가 팀 퀘스트 마감(D-day) · 지정 일시에 학습 반영 1회 실행, 목표 미생성 시 자동 반영 없음
- **정답셋(골든)**: 검수 '정확' 합의가 학습 반영 때 누적 승격 · 평가에서 '채택' 합의 건은 교정 대상으로 표시
- **학습 안전장치**: 반영 전후 일치율 자동 비교 · 2%p 초과 악화 시 해당 보정 자동 원복
- **품질 등급**: G 유통 가능 · R 유통 제외 · YELLOW 판정 애매(사람 검수 대상)


## 📥 데이터 입력

```json
{"displayServiceName":"뉴스","title":"제목","subtitle":"","body":"본문 텍스트"}
```

- 콘텐츠는 4필드 기본 · 핵심은 제목·본문 2개
- 화면의 콘텐츠 관리에서 텍스트·엑셀(.xlsx/CSV)로 추가 · 엑셀 템플릿은 화면에서 다운로드, 1회 최대 200행 (이미지 입력은 실험실 · 미디어 탭)
- 컬럼명이 달라도 제목/본문/서비스명 자동 추론 · CLI `check <file>` 로 동작 가능 여부 사전 판정


## 🏗️ 아키텍처

```
브라우저 (Alpine.js 단일 페이지)
   │  HTTP (JSON)
prism/serve.py (stdlib http.server)
   ├─ page.py            UI 마크업 · vendor/app.js·app.css 스크립트/스타일
   ├─ pipeline.py agents.py   분리형 4호출 추출 · 사전 검증 · 라우팅(REAP)
   ├─ quality.py         품질 판정 (G/R/YELLOW)
   ├─ learnops.py        학습 반영 배치 (골든 승격 · 프롬프트 보정 · 전후 델타 검증)
   ├─ adminops.py        관리자 도메인 (권한 2단계 · 팀 · 데이터 관리)
   ├─ prompts.py promptstore.py meta_prompts.py   계약 · 쿡북 래퍼 · 버전 관리
   └─ store.py / supastore.py   저장 계층 이중 구현
         ├─ SQLite (로컬 단독, 기본)
         └─ Supabase PostgREST (팀 모드)
```

- **의존성 0 원칙**: 서버·테스트 전부 파이썬 표준 라이브러리 · 벤더 JS/CSS/폰트는 저장소에 동봉(오프라인 동작)
- **모델 연결**: OpenAI 호환 `/v1/chat/completions` 이면 어디든 연결 가능 (Upstage 직접, 통합 라우터, 로컬 vLLM/Ollama)
- **모의 모드(`--mock`)**: 키 없이 결정론적 모의 추출로 전 기능 동작 · 데모·테스트·QA의 기반 (목업 시드는 `scripts/seed_qa.py` · 점검 항목은 [QA_CHECKLIST.md](QA_CHECKLIST.md))
- **저장 이중화**: `Store`(SQLite)와 `SupabaseStore`(PostgREST)가 동일 계약 · 계약 테스트로 표류 방지


## ⚙️ 환경 변수

| 변수 | 용도 |
|---|---|
| `PRISM_BACKEND` | `supabase` 지정 시 팀 모드 전환 (기본 로컬 SQLite) |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` | 팀 모드 연결 정보 (로컬 검증 시 `~/.prism_supabase_url`·`~/.prism_supabase_key` 키 파일 참조) |
| `PRISM_ADMIN_EMAILS` | 운영 관리자 허용목록 (쉼표 구분 · `~/.prism_admin_emails` 파일도 가능) |
| `PRISM_DB` | SQLite 경로 (기본 `prism.db` · Docker는 `/data/prism.db`) |
| `UPSTAGE_API_KEY` | Upstage Solar 직접 연결 키 |
| `PRISM_BASE_URL` / `PRISM_API_KEY` | 임의 OpenAI 호환 엔드포인트·키 |
| `PRISM_MODEL` | 기본 실행 모델 |
| `PRISM_CONCURRENCY` / `PRISM_RPM` / `PRISM_TPM` | 동시성·분당 요청·분당 토큰 제한 |

> [!WARNING]
> 키는 환경 변수 또는 권한 600 키 파일로만 주입 · 저장소·이미지에 포함 금지


## 🧪 테스트

```bash
python3 -m pytest tests/ -q        # stdlib unittest · 외부 의존성 0
```

| 층 | 파일 | 검증 대상 |
|---|---|---|
| 1. 단위·계약 | `test_dictionaries` `test_store` `test_gamification` `test_extraction` 등 | 순수 함수·스토어·계약 로직 (사전 정규화, 통계, 레벨 커브, 4호출 검증, 권한) |
| 2. 메타모픽·속성 | `test_metamorphic` | 오라클 없는 변환 계층 · 멱등성, 동치, 단조성, INV/DIR (고정 시드 난수) |
| 3. 통합·e2e | `test_http_smoke` | 실제 HTTP 서버 부팅 · 화면 전 버튼의 엔드포인트 계약 + 검수→정답셋 승격 e2e |
| 4. 이중 구현 계약 | `test_store_contract` | SQLite/Supabase 동일 시나리오 실행으로 이중 구현 표류 차단 · 라이브는 `PRISM_TEST_SUPABASE=1` |

- 방법론 근거(논문 인용)와 작성 규칙: [TESTING.md](TESTING.md)
- 수동 확인 항목: [QA_CHECKLIST.md](QA_CHECKLIST.md)


## 📚 저장소 문서

| 문서 | 대상 |
|---|---|
| [GUIDE.md](GUIDE.md) | 처음 사용하는 팀원 · 관리자 |
| [QA_CHECKLIST.md](QA_CHECKLIST.md) | QA 담당자 (QA 빌드 기준 기대값 포함) |
| [HANDOFF.md](HANDOFF.md) | 개발 인수인계 (구조·정책·이력) |
| [LEARNING_DESIGN.md](LEARNING_DESIGN.md) | 학습데이터·게임화 설계 근거 (논문 인용) |
| [TESTING.md](TESTING.md) | 테스트 구조와 방법론 근거 (3층 + 메타모픽) |
| [DOCKER.md](DOCKER.md) | 팀 공유 서버 컨테이너 구성 |
| [DESIGN_COMPONENTS.md](DESIGN_COMPONENTS.md) | 컴포넌트 계약 (상태 매트릭스 · 토큰 정책) |
| [SUPABASE_MIGRATION.md](SUPABASE_MIGRATION.md) | 팀 모드(Supabase) 테이블 명세 |


## 🛑 한계

- 판정 정확도는 연결한 모델에 의존 · 경계 사례는 YELLOW로 회수해 사람 검수로 확정하는 설계
- 사용자 메타(실험실)는 행동 로그 연결 시에만 실데이터 동작
- 동봉된 예시·데모 데이터는 전부 합성


## 라이선스

- MIT · 포함된 third-party 구성요소는 [NOTICE](NOTICE) 참조
