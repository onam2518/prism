<div align="center">

# Prism

**콘텐츠 검수·평가 플랫폼: 관리자가 넣은 콘텐츠를 팀이 검수하면, 그 합의가 정답셋(골든)과 파인튜닝 스펙·소요서의 근거가 됩니다.**

![license](https://img.shields.io/badge/license-MIT-black?style=flat-square)
![python](https://img.shields.io/badge/python-3.8%2B-black?style=flat-square)

**온라인 데모:** [통합 데모](https://onam2518.github.io/prism/demo.html)

[처음 사용자 가이드](GUIDE.md) · [QA 체크리스트](QA_CHECKLIST.md) · [개발 인수인계](HANDOFF.md) · [학습 설계](LEARNING_DESIGN.md)

</div>

```
콘텐츠 추가(수동·자동) → 모델 실행(초안 vN) → 팀 검수(판정·교정·게임화)
      → 정답셋(골든) 누적 → 평가(일치율·건별 판정·모델 A/B) → 학습데이터·소요서(.md)
```

> 데모(`docs/demo.html`)와 `examples/`의 콘텐츠는 전부 합성 예시입니다.

## 무엇을 하는 도구인가

LLM으로 콘텐츠 메타(리드문·엔티티·인텐트·카테고리·품질)를 뽑을 때 문제는 "모델이 뽑은 결과를 누가, 어떻게 믿을 것인가"입니다. Prism은 그 답을 팀의 검수 합의에서 찾습니다.

- **검수가 곧 데이터**: 팀원이 초안을 정확/수정으로 판정하고 교정하면, 합의된 결과가 정답셋(골든)으로 쌓입니다.
- **정답셋이 곧 기준**: 쌓인 정답셋으로 모델·프롬프트 버전을 평가하고(일치율·신뢰구간), 모델끼리 A/B 비교합니다.
- **결과물이 곧 소요서**: 축적 현황과 논문 기준치를 대비해 SFT/DPO/판단근거 데이터셋과 파인튜닝 소요서(.md)를 뽑아냅니다.
- **계속하게 만드는 장치**: 레벨·배지·미션·리그 등 게임화가 붙어 있습니다. 개인이 1만 건을 검수하면 만렙과 전 배지가 완성되는 설계입니다.

## 빠른 시작

### 웹 (로컬 서버)

```bash
git clone https://github.com/onam2518/prism && cd prism
python3 -m prism.serve --mock          # http://localhost:8765 · 키 없이 모의 추출로 전 기능 체험
```

- 파이썬 표준 라이브러리만 사용합니다. `pip install` 없이 3.8+ 어디서나 돌아갑니다.
- 실제 모델 호출은 화면의 시스템 설정에서 API 키를 넣으면 됩니다(Upstage Solar 직접 또는 통합 라우터).

### 데스크탑 (macOS)

[Releases](https://github.com/onam2518/prism/releases)에서 최신 DMG를 받아 설치합니다.

- `Prism-x.y.z.dmg`: 운영 빌드. 팀 로그인(supabase 설정 시)과 로컬 단독 모드를 지원합니다.
- `Prism-QA-x.y.z.dmg`: QA 빌드. 키·로그인 없이 목업 콘텐츠 10건이 자동으로 채워진 상태로 시작합니다. 점검 항목은 [QA_CHECKLIST.md](QA_CHECKLIST.md)를 보세요.

### CLI (배치 추출)

화면 없이 파일 배치만 돌릴 때 사용합니다. 상세 옵션은 `python3 -m prism.cli` 도움말 참조.

```bash
python3 -m prism.cli extract --batch examples/contents.sample.jsonl --mock --out results.jsonl
python3 -m prism.cli report  --batch examples/contents.sample.jsonl --mock --out report.html
```

## 화면 구성

| 메뉴 | 역할 |
|---|---|
| 홈 | 검수 진척율 · 내 캐릭터(레벨·배지·미션) · 주간 리그 |
| 콘텐츠 검수 | 검수 대상 목록(판정·교정) · 결과 비교(모델×버전 A/B) |
| 평가 | 평가 기준 설정 · 정답셋 일치율 · 불일치 건별 판정(채택/탈락) · 모델 A/B 비교 |
| 콘텐츠 관리* | STEP 1 콘텐츠 추가(수동·자동, 용도 지정) → STEP 2 모델 실행 → STEP 3 실행 큐 |
| 테스트셋 관리* | 정답셋 현황·학습 반영 · 정답셋 목록 · 학습 데이터(SFT/DPO/소요서) |
| 프롬프트 스튜디오* | 기준 계약(4호출 규칙, 읽기 전용) · 모델별 쿡북 래퍼 편집 · 최종 프롬프트 미리보기 |
| 사전·정책* / 실험실* / 팀 관리 / 시스템 설정* | 분류 사전 · 탐구 요소 · 멤버/초대코드 · 데이터/API 키/데스크탑 옵션 |

\* 관리자 메뉴. 운영 관리자(허용목록)는 전체, 팀 관리자(생성자·위임)는 팀 관리만 봅니다.

## 핵심 개념

- **분리형 4호출**: 아이템 메타는 리드문 → 엔티티 → 인텐트 → 카테고리 순서의 개별 호출로 뽑습니다. 호출 사이에 사전 검증이 들어가고, 호출별로 다른 모델을 지정할 수 있습니다.
- **용도(검수용/평가용)**: 콘텐츠 추가 시 용도를 정합니다. 평가용은 검수 목록에서 제외되어 오염 없는 평가 전용 홀드아웃으로 보존됩니다.
- **버전(v)**: 초안 버전은 학습 반영 회차 + 1 입니다. 검수 피드백이 매일 한 번 프롬프트에 반영되고, 새 버전으로 재실행해 개선을 비교합니다.
- **정답셋(골든)**: 검수 '정확' 합의가 학습 반영 때 누적 승격됩니다. 평가에서 불일치가 '채택' 합의되면 교정 대상으로 표시됩니다.
- **품질 등급**: G 유통 가능 · R 유통 제외 · YELLOW 판정 애매(사람 검수 대상).

## 데이터 입력

콘텐츠는 4필드가 기본입니다. 제목·본문 두 개가 핵심입니다.

```json
{"displayServiceName":"뉴스","title":"제목","subtitle":"","body":"본문 텍스트"}
```

- 화면의 콘텐츠 관리에서 텍스트·이미지·엑셀(.xlsx/CSV)로 추가합니다. 엑셀 템플릿은 화면에서 내려받을 수 있습니다.
- 컬럼명이 달라도 자동 추론합니다(제목/본문/서비스명). CLI는 `check <file>` 로 동작 가능 여부를 먼저 판정합니다.

## 저장소 문서

| 문서 | 대상 |
|---|---|
| [GUIDE.md](GUIDE.md) | 처음 사용하는 팀원 · 관리자 |
| [QA_CHECKLIST.md](QA_CHECKLIST.md) | QA 담당자 (QA 빌드 기준 기대값 포함) |
| [HANDOFF.md](HANDOFF.md) | 개발 인수인계 (구조·정책·이력) |
| [LEARNING_DESIGN.md](LEARNING_DESIGN.md) | 학습데이터·게임화 설계 근거 (논문 인용) |
| [SUPABASE_MIGRATION.md](SUPABASE_MIGRATION.md) | 팀 모드(Supabase) 테이블 명세 |

## 기술 요약

- 서버: 파이썬 표준 라이브러리 http.server, 단일 파일 UI(Alpine.js). 외부 패키지 의존 없음.
- 저장소: 로컬 SQLite 기본, 팀 모드는 Supabase(PostgREST) 이중 지원.
- 모의 모드(`--mock`): 키 없이 결정론적 모의 추출로 전 기능이 동작합니다.
- 모델 연결: OpenAI 호환 `/v1/chat/completions` 이면 어디든(Upstage 직접, 통합 라우터, 로컬 vLLM/Ollama).
- 데스크탑: pywebview 네이티브 창. 다운로드·저장 데이터 옵션은 시스템 설정에서 조정합니다.

## 한계

- 판정 정확도는 연결한 모델에 달려 있습니다. 경계 사례는 YELLOW로 회수해 사람 검수로 확정하는 설계입니다.
- 사용자 메타(실험실)는 행동 로그를 연결해야 실데이터로 동작합니다.
- 동봉된 예시·데모 데이터는 전부 합성입니다.

## 라이선스

MIT. 포함된 third-party 구성요소는 NOTICE 참조.
