<div align="center">

# Prism

**콘텐츠를 넣으면 메타를 추출·그룹핑해 단일 HTML 리포트로 만들어 주는 터미널 에이전트**

모델·데이터·디자인은 갈아끼우는 부품, 코어는 "어떤 회사 콘텐츠에도 붙는" 파이프라인입니다.

![license](https://img.shields.io/badge/license-MIT-black?style=flat-square)
![python](https://img.shields.io/badge/python-3.8%2B-black?style=flat-square)
![deps](https://img.shields.io/badge/dependencies-0-10b981?style=flat-square)
![output](https://img.shields.io/badge/output-self--contained%20HTML-5e6ad2?style=flat-square)
![model](https://img.shields.io/badge/model-swappable-5e6ad2?style=flat-square)

[데모 보기](https://htmlpreview.github.io/?https://github.com/onam2518/prism/blob/main/docs/demo.html) ·
[데모 내려받기](docs/demo.html) · [5분 사용법](#5분-사용법) · [구조](#구조) · [활용](#무엇에-쓰나-활용)

</div>

```
content  ─▶  콘텐츠 메타  ─▶  메타풀  ─▶  사용자 메타  ─▶  report.html
            (품질·법령·아이템)   (단독·복합·필터)   (소비형태·강도)      (단일 파일)
```

> 데모(`docs/demo.html`)와 `examples/`는 전부 **합성 예시**입니다.

---

## 왜 만들었나

콘텐츠 메타 · 어떤 글인지, 붙일 수 있는지, 무엇에 관한 건지 · 는 보통 **흩어진 스크립트와 사람 손**으로 만들어집니다. 만드는 방식도, 결과를 보는 방식도 제각각이죠.

Prism은 그 과정을 **하나의 파이프라인 · 한 번의 명령 · 단일 HTML 리포트**로 묶습니다. 서버도 빌드도 필요 없습니다. 결과물을 받는 사람은 파일을 **더블클릭**만 하면 됩니다 · 오프라인이든, 이메일 첨부든, USB든 그대로 열립니다.

## 4대 원칙

1. **산출물은 언제나 self-contained HTML 1파일.** 의존성 0(파이썬 표준 라이브러리), 그래프 라이브러리까지 인라인 벤더링. 서버리스.
2. **터미널이 곧 설정.** 추출·그룹핑·리포트·스케줄을 전부 CLI로. `report` 한 줄을 크론에 걸면 매일 자동 생성.
3. **모델은 사용자가 선택.** `--model`로 OpenAI 호환 어떤 모델이든 연결. 키가 없으면 `--mock`으로 전 기능을 체험.
4. **토큰을 아낀다.** LLM 호출 최소화가 기본 · 규칙 레이어는 $0, 카테고리는 임베딩 우선, 유통 불가 콘텐츠는 추출을 생략.

## 설치

```bash
git clone https://github.com/onam2518/prism && cd prism
python3 -m prism.cli            # 배너 + 도움말 (의존성 없음, 바로 실행)
```

## 5분 사용법

```bash
# 0) 키 없이 체험 · mock 모드
python3 -m prism.cli extract --input examples/content.json --mock

# 1) 콘텐츠 묶음 → 메타 추출
python3 -m prism.cli extract --batch examples/contents.sample.jsonl --mock --out results.jsonl

# 2) 한 번에 리포트 · 추출 → 메타풀 → 대시보드 → 단일 HTML
python3 -m prism.cli report --batch examples/contents.sample.jsonl --mock --out report.html

# 3) 실제 모델로 · OpenAI 호환 엔드포인트
export PRISM_API_KEY=...        # 또는 config.json
python3 -m prism.cli report --batch contents.jsonl --model <your-model> --out report.html
```

리포트 HTML은 **아이템 메타 · 메타풀 · 사용자 메타** 세 개 탭으로 열립니다.

## 구조

```
content (4필드: 서비스명·제목·부제·본문)
  └ Dispatcher (규칙)        서비스 그룹 분기 · 활성 메타 결정          [$0]
     ├ 품질 메타             유통 가능(G)/불가(R), 저신뢰는 YELLOW(사람 검수)
     │    └ R → 이후 단계 생략 (토큰 절약)
     ├ 아이템 메타           인텐트 · 엔티티 · 인텐트/엔티티 카테고리
     ├ [옵션] 법령 메타       위반 유형 스코어링 → 차단
     └ Verifier · Aggregator (규칙)  →  통합 JSON + trace            [$0]
        └ 메타풀: 단독형(엔티티) · 복합형(사건) · 필터형(조건)
        └ 사용자 메타: 행동 로그 → 소비 형태·강도 → 페르소나
```

- **에이전트 팀 + 하네스** · 품질·법령·아이템·카테고리 추출기는 각각 독립 에이전트이고, `pipeline`이 게이팅으로 조율합니다. 새 메타가 필요하면 에이전트를 추가하는 식으로 확장합니다.
- **LLM 비의존 레이어** · Dispatcher·Verifier·Aggregator는 순수 규칙. 카테고리만 "임베딩 후보 + LLM 판단"의 2-pass 구조입니다.
- **YELLOW(사람 검수)** · 임베딩 2차 의견과 LLM 판정이 엇갈리면 자동으로 사람 검수 대상으로 분리합니다.

## 무엇에 쓰나 (활용)

- **콘텐츠 운영 대시보드** · 어떤 메타가 어떻게 붙었는지, 무엇이 유통 가능한지 한눈에.
- **메타풀 큐레이션** · 엔티티(인물·기업) 단위, 사건 단위, 관심사 조건 단위로 콘텐츠를 자동 그룹핑해 추천·큐레이션 슬롯으로.
- **사용자 프로파일링** · 행동 로그를 소비 "형태/강도"로 환산 → 페르소나 → 홈 재배치·능동 추천·광고 타겟팅.
- **매일 자동 리포트** · 크론으로 어제 콘텐츠를 매일 아침 HTML로 받기.

```bash
# 크론: 매일 08:00, 어제 콘텐츠 → 리포트
0 8 * * * cd /path/to/prism && python3 -m prism.cli report \
  --batch /data/yesterday.jsonl --profile profiles/your-company.json \
  --out ~/reports/$(date +\%Y\%m\%d).html
```

## 회사마다 다르게 (프로파일)

서비스 종류·분류 체계·품질 기준은 회사마다 다릅니다. 코어는 그대로 두고 **사전만 교체**하면 됩니다.

```bash
python3 -m prism.cli report --batch contents.jsonl --profile profiles/example-acme.json --out report.html
```

`profiles/example-acme.json`을 참고하세요. 교체 가능한 키는 다음과 같습니다.

`service_group` · `intent_universal` · `intent_by_service` · `iab_tier1` · `quality_metas` · `title`

## 데이터 명세 (실제 동작 조건)

실제로 돌리려면 두 종류의 입력만 맞추면 됩니다.

**① 콘텐츠 (추출 입력)** · jsonl, 한 줄에 한 건:

```json
{"displayServiceName":"뉴스","title":"제목","subtitle":"","body":"본문 텍스트"}
```

4필드 고정. `displayServiceName`은 프로파일의 `service_group` 키와 매칭됩니다(없으면 `media` 기본값).

**② 행동 로그 (사용자 메타용, 선택)** · jsonl, `--logs`로 연결:

```json
{"user_id":"u_001","content_id":"<results의 id 또는 제목>","event":"click|impression","dwell_sec":42,"scroll_pct":80,"ts":"2026-06-08T08:01:00"}
```

`examples/behavior_logs.sample.jsonl`을 참고하세요. 이 로그가 있으면 **실데이터로** 소비 형태·강도·페르소나를 산출합니다.

> **사용자 메타 기본 동작** · 행동 로그가 연결되지 않으면 가짜 데이터를 만들지 않고 **빈 상태**(개념·명세만)로 표시합니다.
> `--logs`로 실데이터를 연결하거나 `--demo`로 합성 목업을 채워 미리 볼 수 있습니다. 동작 예시는 [docs/demo.html](docs/demo.html)에서 확인하세요.

## 명령

| 명령 | 설명 |
| --- | --- |
| `extract --input/--batch` | 단건/배치 메타 추출 (`--resume`, `--legal`, `--yellow`) |
| `report --batch/--results` | 추출 → 메타풀 → 대시보드까지 한 번에 (`--profile`, `--logs`, `--demo`) |
| `metapool --results` | 메타풀 단독·복합·필터형 생성 |
| `usermeta --results` | 사용자 메타 · `--logs`(실데이터) / `--demo`(목업) / 기본(빈 상태) |
| `dashboard --results [--integrated]` | 메타 현황 + 관계도 + 추출 로직 상세 HTML |
| `eval --goldenset` | 라벨셋 일치율·게이트 |
| `doctor` / `usage` / `init-config` | 연결 점검 / 비용 로그 / 설정 템플릿 |

공통 옵션: `--model` · `--mock` · `--embed on|off` · `--config` · `--no-db`

## 디자인

리포트 스킨은 [awesome-design-md](https://github.com/VoltAgent/awesome-design-md) 토큰(색·타이포·라운드)을 입혀 교체합니다.
현재 적용: **Linear**(`DESIGN-linear.md`) · 근검정 캔버스 + 단일 라벤더 액센트 + 플랫·헤어라인.
원칙: 그라데이션·스포트라이트·이모지·무지개 구조색 금지(데이터 시각화 색만 예외).

## 한계

- 판정 정확도는 사용하는 모델에 달려 있습니다. 경계 사례는 YELLOW(사람 검수)로 회수하는 설계입니다.
- 사용자 메타는 행동 로그 파이프라인이 있어야 실데이터로 동작합니다. 없으면 빈 상태 또는 `--demo`.
- 동봉된 `examples/`와 `docs/demo.html`은 전부 합성 예시입니다.

## 라이선스

MIT · 그래프 시각화는 [force-graph](https://github.com/vasturiano/force-graph)(MIT)를 인라인 벤더링했습니다(NOTICE 참조).
