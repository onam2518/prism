# Prism Design System

Prism UI의 디자인 토큰과 React 컴포넌트. **Source of truth = Anchor Design System(axz)** — 무채색 캔버스 + **Blue(Primary 액션)·Red(Accent 강조)** + 도메인 카테고리색 · **Pretendard** 단일 패밀리 · **Light/Dark** 자동 swap을 코드로 고정한 시스템.

원본 스펙: [`anchor/DESIGN.md`](./anchor/DESIGN.md) · 대표 컴포넌트 [`anchor/Button.md`](./anchor/Button.md) · 토큰 스냅샷 [`anchor/tokens.json`](./anchor/tokens.json). 본 패키지의 `tokens/tokens.json`·`src/theme.css`는 Anchor semantic 토큰을 `--ds-*` 변수로 박제한 것입니다(원칙: Semantic 토큰만 사용 · Primitive 직접 참조 금지 · Light/Dark 분기 금지).

**문서**
- **위젯형 SaaS 표준(제품 비종속)** → [`WIDGET_SAAS_STANDARD.md`](./WIDGET_SAAS_STANDARD.md) — 위젯형 SaaS 표준 디자인 정책
- **서비스 디자인 가이드(최상위)** → [`SERVICE_DESIGN.md`](./SERVICE_DESIGN.md) — 위젯 홈 중심 종합 가이드(Prism)
- **비주얼 원칙** → `DESIGN.md` · **UX·플로우·에셋 원칙** → [`PRINCIPLES.md`](./PRINCIPLES.md) · **컴포넌트 구조** → [`COMPONENTS.md`](./COMPONENTS.md) · **산정/로드맵** → [`COMPONENT_ROADMAP.md`](./COMPONENT_ROADMAP.md) · **로고** → [`LOGO.md`](./LOGO.md)

**화면**
- **위젯 홈(중심)** → `preview/widget-home.html` — 위젯 추가·삭제·재배치·리사이즈 · 기능/정보 구분 · 온보딩 · 설정 팝업
- **위젯 카탈로그** → `preview/widgets.html` — 코드베이스 기반 풀기능 위젯 + 사이드바(조합/단일 바로가기)
- **컴포넌트 갤러리** → `preview/index.html` — 41개 컴포넌트
- *초기 탐색(참고, 위젯 홈에 흡수됨)* → `preview/dashboard.html` · `preview/agent-dashboard.html`

## 구성

```
design-system/
  package.json            # designSystem.{tokens,theme,components} 명시
  tokens/tokens.json      # 디자인 토큰 (W3C/DTCG 포맷)
  src/
    theme.css             # 토큰 → CSS 변수 (라이트 기본 + .ds-dark/[data-theme=dark] 다크)
    components.css        # 컴포넌트 스타일 (토큰만 참조)
    tokens.ts             # 타입드 토큰
    Button Input Select Card Badge Tabs Table Dialog (.tsx)
    index.ts
  tailwind.preset.cjs     # Tailwind 프로젝트용 프리셋
```

## 컴포넌트

| 컴포넌트 | variant / props | 비고 |
|---|---|---|
| `Button` | `primary` · `secondary` · `ghost` · `pill`(+`active`) | teal CTA / 보더 / 조용한 툴바 / 포커스모드 칩 |
| `Input` | `field` · `composer`, `invalid`, `hint`, `label` | composer = 시그니처 Ask 웰(radius 16) |
| `Select` | `options`, `invalid`, `label` | 정해진 값 입력 |
| `Card` | `answer`(기본) · `source` · `feed` | source 는 hover 시 teal lift |
| `Badge` | `neutral` · `pro` · `status` · `citation` · `success` · `error` · `warning` | teal 계열은 절제 |
| `Tabs` | `underline`(상단 내비) · `sidebar`(세로) | 활성: teal 밑줄 / tint 하이라이트 |
| `Table` | `columns`, `data`, `rowKey` | 에디토리얼, hover 시 tint 행 |
| `Dialog` | `open`, `onClose`, `title`, `footer` | 중앙 모달, Esc/백드롭 닫힘 |

## /design-sync 실행

```bash
cd ~/Desktop/project/prism/design-system
claude
› /design-sync
```

완료되면 조직의 **Design systems**에 등록됩니다. 이후 토큰/컴포넌트를 수정하고 다시 `/design-sync` 하면 갱신됩니다.

## 토큰 요약

| 그룹 | 값 |
|---|---|
| Primary | `teal #20808d` (hover `#1a6873` · pressed/deep `#13343b` · tint `#e5f2f2`) — 유일한 인터랙션/인용 색 |
| Surface | page `#fbfaf4` · card `#fcfcf9` · input/overlay `#ffffff` (순백 페이지 금지) |
| Ink | ink `#091717`(=`#000` 금지) · body `#2e3a3a` · muted `#5c6a6a` · placeholder `#8a9494` |
| Border | hairline `#e4e4dc` · soft `#efefe9` — 따뜻하고 저대비 |
| Dark | canvas `#0d1117` · surface `#161b22` · teal `#34b4c4` |
| Font | FK Grotesk(UI) · FK Grotesk Neue(본문) · FK Display(히어로) · Berkeley Mono — Inter 폴백 |
| Radius | 6(칩) · 10(버튼/입력) · 12(카드) · 16(컴포저/모달) · full |
| Shadow | 페이퍼-플랫 기본. 떠있는 레이어에만 잉크 틴트 그림자(`rgba(9,23,23,…)`) |

## 라이트 / 다크

라이트가 기본. 다크는 셋 중 하나로 켭니다:
- 루트에 `class="ds-dark"` 또는 `data-theme="dark"`
- 아무 것도 안 하면 `prefers-color-scheme: dark` 자동 따라감 (`ds-light`/`data-theme=light` 로 고정 가능)

## 핵심 원칙 (DESIGN.md 발췌)

1. 답변/콘텐츠가 주인공 — 크롬은 `#5c6a6a` 회색으로 물러남
2. teal `#20808d` 은 **액션·인용 신호만**. 장식 금지
3. 페이퍼-플랫 — 깊이는 따뜻한 보더 + surface 온도차로, 그림자 아님
4. 본문은 매거진처럼 — `ds-answer`(16px / 1.63 / 68ch)
5. 디자인 변경은 `tokens.json` + `theme.css` + `components.css` 만 고치면 전체 반영

## Prism UI(serve.py)와의 관계

현재 Prism UI는 `serve.py` 안의 인라인 Tailwind입니다. `/design-sync` 후 이 토큰/컴포넌트 기준으로 `serve.py` 마크업을 맞춰갈 수 있습니다. `tailwind.preset.cjs` 를 preset 으로 물리면 같은 토큰을 클래스로 바로 사용 가능합니다.
