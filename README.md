<div align="center">

# Prism

**콘텐츠를 넣으면 메타를 추출·그룹핑해서 단일 HTML 리포트로 만들어 주는 터미널 에이전트**

![license](https://img.shields.io/badge/license-MIT-black?style=flat-square)
![python](https://img.shields.io/badge/python-3.8%2B-black?style=flat-square)

**DEMO (온라인):** [통합 데모](https://onam2518.github.io/prism/demo.html) ·
[아이템 메타](https://onam2518.github.io/prism/demo-items.html) ·
[토픽](https://onam2518.github.io/prism/demo-metapool.html) ·
[사용자 메타](https://onam2518.github.io/prism/demo-users.html)

[5분 사용법](#5분-사용법) · [구조](#구조) · [활용](#이걸로-뭘-하나) · [용어 사전](#용어-사전)

</div>

```
content  ─▶  콘텐츠 메타  ─▶  토픽  ─▶  사용자 메타  ─▶  report.html
            (품질·법령·아이템)    (단독·복합·필터)   (소비형태·강도)      (단일 파일)
```

> DEMO(`docs/demo.html`)와 `examples/`는 전부 **합성 예시**입니다.

---

## 왜 만들었나

콘텐츠를 다루는 일은 결국 같은 질문의 반복입니다. **이거 내보내도 되나(품질), 무슨 내용이지(의미), 무엇과 묶이나(관계), 누가 어떻게 볼까(사용자).** 보통은 스크립트 몇 개에 사람 손을 더해 그때그때 답합니다.

Prism은 이 네 가지 '이해'를 **에이전트 팀**(각 메타를 맡은 추출기 + 게이팅 하네스)으로 자동화해서, **명령 한 줄 → 단일 HTML 한 장**으로 내놓습니다. 서버도 빌드도 없고, 받는 사람은 더블클릭만 하면 됩니다. (오프라인·이메일·USB 다 돼요.)

## 설치

```bash
git clone https://github.com/onam2518/prism && cd prism
python3 -m prism.cli            # 배너 + 도움말 (바로 실행)
```

**`pip install` 없습니다.** 받아서 바로 실행하면 됩니다.

- 파이썬 표준 라이브러리만 사용 → `python3` 3.8+ 면 **Windows · macOS · Linux** 어디서나.
- 클론 직후 `--mock`으로 전 기능을 키 없이 돌려볼 수 있습니다(실제 모델은 `PRISM_API_KEY` + `--model`).
- 생성된 리포트 HTML은 그래프 라이브러리까지 파일에 포함돼 **인터넷 없이도** 열립니다.
- Claude Code 같은 에이전트도 동일: 클론 후 명령만 실행하면 그대로 동작합니다.

## 5분 사용법

```bash
# 0) 키 없이 체험 (mock)
python3 -m prism.cli extract --input examples/content.json --mock

# 1) 콘텐츠 묶음 → 메타 추출
python3 -m prism.cli extract --batch examples/contents.sample.jsonl --mock --out results.jsonl

# 2) 한 번에 리포트 (추출 → 토픽 → 대시보드 → 단일 HTML)
python3 -m prism.cli report --batch examples/contents.sample.jsonl --mock --out report.html

# 3) 실제 모델로: 처음 한 번 설정
python3 -m prism.cli init --base-url https://api.openai.com/v1 --model gpt-4o-mini
export PRISM_API_KEY=...
python3 -m prism.cli report --batch contents.jsonl --out report.html
```

> 설정 전에는 실제 모델 호출이 막혀 있고, 안내 메시지가 뜹니다. 둘러보기는 `--mock`으로 키·설정 없이 가능합니다.

리포트 HTML은 3개 탭으로 열립니다: **아이템 메타 · 토픽 · 사용자 메타**.

### 모델은 무엇을 붙일 수 있나

`/v1/chat/completions` 형식(OpenAI 호환)을 따르는 곳이면 **무엇이든** 됩니다. `init` 또는 `--base-url`(`PRISM_BASE_URL`)로 엔드포인트만 지정하면 됩니다. 기본값은 없습니다.

| 제공자 | base URL 예시 |
| --- | --- |
| OpenAI | `https://api.openai.com/v1` |
| Together · Groq · Mistral 등 | 각 제공자의 `/v1` |
| 로컬 (Ollama) | `http://localhost:11434/v1` |
| 로컬 (vLLM · LM Studio) | `http://localhost:8000/v1` |

- Claude·Gemini처럼 자체 API 형식을 쓰는 모델은 OpenAI 호환 게이트웨이를 앞에 두면 됩니다.
- 임베딩(카테고리 분류용)은 제공자에 맞는 임베딩 모델이 없으면 `--embed off`로 끄거나 `PRISM_EMBED_MODEL`로 지정하세요.

## 구조

```
content (4필드: 서비스명·제목·부제·본문)
  └ Dispatcher (규칙)        서비스 그룹 분기 · 활성 메타 결정          [$0]
     ├ 품질 메타             유통 가능(G)/불가(R), 저신뢰는 YELLOW(사람 검수)
     │    └ R → 이후 생략 (토큰 절약)
     ├ 아이템 메타           인텐트 · 엔티티 · 인텐트/엔티티 카테고리
     ├ [옵션] 법령 메타       위반유형 스코어링 → 차단
     └ Verifier · Aggregator (규칙)  →  통합 JSON + trace            [$0]
        └ 토픽: 엔티티형(엔티티)·사건형(사건)·조건형(조건)
        └ 사용자 메타: 행동 로그 → 소비 형태·강도 → 페르소나
```

- **에이전트 팀 + 하네스**: 품질·법령·아이템·카테고리 추출기는 각각 독립 에이전트, `pipeline`이 게이팅으로 조율. 새 메타는 에이전트 추가로 확장.
- **LLM 비의존 레이어**: Dispatcher·Verifier·Aggregator는 순수 규칙. 카테고리는 임베딩 후보 + LLM 판단(2-pass).
- **YELLOW(사람 검수)**: 임베딩 2차의견과 LLM 판정이 엇갈리면 자동으로 사람 검수 대상으로 분리.

## 이걸로 뭘 하나

프리즘이 하는 건 하나입니다. **콘텐츠를 이해하는 것.** 그 이해(품질·의미·관계·사용자)를 어디에 꽂느냐가 응용이고, 아래는 일부일 뿐입니다.

**의미·품질 파악**: 들어온 글이 내보낼 수 있는지(G/YELLOW/R), 왜 막혔는지(광고·선정·낚시 등 사유별), 무엇에 관한지(인텐트·엔티티 카테고리)를 한눈에.

**관계로 묶기 (토픽)**: 흩어진 콘텐츠를 세 축으로 그룹핑.
- 엔티티형: 인물·기업 단위 ("손흥민 관련 전부")
- 사건형: 같은 사건을 자동으로 묶어 **대표 1건 + 관련 N건** (중복 제거·관점 분산)
- 조건형: 자연어 조건만 적으면 부합 콘텐츠가 자동 편입 ("경제 × 심층 분석"). **이 조건의 주체가 운영자면 큐레이션, 개인이면 개인 피드가 됩니다.**

**사용자 이해**: '무엇'이 아니라 '어떻게 소비하나'까지. 정독러에겐 심층, 스낵러에겐 요약. 행동→형태→강도→페르소나로 역추적하고, 선호 카테고리 교차로 타겟팅 정밀도를 높입니다.

이 이해는 큐레이션·추천·개인화·모더레이션 어디에든 꽂힙니다. 그리고 무엇을 만들든 `report` 한 줄을 크론에 걸면 매일 자동으로 단일 HTML이 도착합니다.

```bash
# 어떤 응용이든 매일 자동화: 어제 콘텐츠 → 단일 HTML
0 8 * * * cd /path/to/prism && python3 -m prism.cli report \
  --batch /data/yesterday.jsonl --profile profiles/your-company.json \
  --out ~/reports/$(date +\%Y\%m\%d).html
```

## 회사마다 다르게 (프로파일)

서비스 종류·분류 체계·품질 기준은 회사마다 다릅니다. 코어는 그대로 두고 **사전만 교체**합니다.

```bash
python3 -m prism.cli report --batch contents.jsonl --profile profiles/example-acme.json --out report.html
```

`profiles/example-acme.json` 참고. 교체 가능한 키: `service_group`, `intent_universal`, `intent_by_service`, `iab_tier1`, `quality_metas`, `title`.

## 데이터 명세 (실제로 돌리려면)

입력은 두 가지면 충분합니다.

**① 콘텐츠 (추출 대상)**: jsonl·엑셀·CSV:
```json
{"displayServiceName":"뉴스","title":"제목","subtitle":"","body":"본문 텍스트"}
```
4필드 권장. 핵심은 **제목·본문** 두 개. `displayServiceName`은 프로파일 `service_group` 키와 매칭(없으면 media 기본).

**엑셀/CSV도 됩니다.** 컬럼명이 달라도 자동 추론하고, 동작 가능 여부를 먼저 판정합니다:
```bash
python3 -m prism.cli check data.xlsx        # [가능]/[불가능] + 추론 매핑 출력
python3 -m prism.cli report --batch data.xlsx --out report.html   # 가능하면 바로 실행
python3 -m prism.cli report --batch data.xlsx --map "title=헤드라인,body=기사내용"  # 강제 지정
```
`헤드라인/기사내용/섹션` 같은 컬럼도 제목·본문·서비스로 자동 매핑. 제목·본문을 못 찾으면 **불가능**으로 판정하고 `--map` 지정을 안내합니다.

**② 행동 로그 (사용자 메타용, 선택)**: jsonl, `--logs`로 연결:
```json
{"user_id":"u_001","content_id":"<results의 id 또는 제목>","event":"click|impression","dwell_sec":42,"scroll_pct":80,"ts":"2026-06-08T08:01:00"}
```
`examples/behavior_logs.sample.jsonl` 참고. 이 로그가 있으면 **실데이터로** 소비 형태·강도·페르소나를 산출합니다.

> **사용자 메타 기본 동작**: 행동 로그가 연결되지 않으면 **가짜 데이터를 만들지 않고 빈 상태**(개념·명세만)로 표시합니다.
> `--logs`로 실데이터를 연결하거나, `--demo`로 합성 목업을 채워 미리 볼 수 있습니다. 동작 예시는 [온라인 데모](https://onam2518.github.io/prism/demo.html).

## 명령

| 명령 | 설명 |
| --- | --- |
| `extract --input/--batch` | 단건/배치 메타 추출 (`--resume`, `--legal`, `--yellow`) |
| `report --batch/--results` | 추출→토픽→대시보드 한 번에. `--profile`, `--logs`, `--demo` |
| `topic --results` | 토픽 엔티티형·사건형·조건형 생성 (구 `metapool` 별칭 유지) |
| `usermeta --results` | 사용자 메타: `--logs`(실데이터)/`--demo`(목업)/기본(빈 상태) |
| `dashboard --results [--integrated]` | 메타 현황 + 관계도 + 추출로직 상세 HTML |
| `check <file.xlsx/csv>` | 엑셀/CSV가 동작 가능한지 판정(가능/불가능 + 추론 매핑) |
| `eval --goldenset` | 라벨셋 일치율·게이트 |
| `init` | 설정(엔드포인트·모델) 구성 → config.json |
| `doctor` / `usage` | 연결 점검 / 비용 로그 |

공통: `--model` `--mock` `--embed on|off` `--config` `--no-db`

## 용어 사전

리포트 우상단 **`? 용어·구조`** 버튼에서도 볼 수 있습니다.

| 용어 | 뜻 |
| --- | --- |
| 품질 메타 | 유통 가능 여부. **G** 가능 · **R** 불가 · **YELLOW** 자동 판정 애매 → 사람 검수 |
| 아이템 메타 | 콘텐츠가 "무엇인지": 인텐트·엔티티·각각의 카테고리 |
| 인텐트 (카테고리) | 콘텐츠를 '왜·어떻게' 소비하는지(서술) → 분류값(속보·심층 분석·팩트체크 등) |
| 엔티티 (카테고리) | 콘텐츠 속 인물·기업·작품 등 고유 대상 → IAB 기반 분류 |
| 토픽 · 엔티티형 | 단일 엔티티 단위 그룹("이 인물·기업 관련 콘텐츠"). 영속 |
| 토픽 · 사건형 | 사건 단위 그룹(엔티티 공출현으로 자동 발견). 단기 |
| 토픽 · 조건형 | 조건 단위 그룹(인텐트 카테고리 × 엔티티 카테고리). 중장기 |
| 소비 형태(FORM) | '무엇'이 아니라 '어떻게' 소비하는가: 세션·체류/완주·전환·깊이·시간대 |
| 소비 강도 | 형태에서 산출되는 평가값. 소비 맥락(인텐트 카테고리)별 저·중·고 |
| 페르소나 | 형태·강도를 결합한 사용자 유형(정독러·스낵러·팬덤 등) |

## 한계

- 판정 정확도는 사용하는 모델에 달려 있습니다. 경계 사례는 YELLOW(사람 검수)로 회수하는 설계입니다.
- 사용자 메타는 행동 로그 파이프라인이 있어야 실데이터로 동작합니다. 없으면 빈 상태 또는 `--demo`.
- 동봉된 `examples/`·`docs/demo.html`은 전부 합성 예시입니다.

## 라이선스

MIT. 포함된 third-party 구성요소는 NOTICE 참조.
