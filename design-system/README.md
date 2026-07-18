# Prism Design System

Prism UI의 디자인 토큰과 React 컴포넌트. **Source of truth = Anchor Design System(axz)** · 무채색 캔버스 + **Blue(Primary 액션)·Red(Accent 강조)** + 도메인 카테고리색 · **Pretendard** 단일 패밀리 · **Light/Dark** 자동 swap을 코드로 고정한 시스템.

원본 스펙: [`anchor/DESIGN.md`](./anchor/DESIGN.md) · 대표 컴포넌트 [`anchor/Button.md`](./anchor/Button.md) · 토큰 스냅샷 [`anchor/tokens.json`](./anchor/tokens.json). 본 패키지의 `tokens/tokens.json`·`src/theme.css`는 Anchor semantic 토큰을 `--ds-*` 변수로 박제한 것입니다(원칙: Semantic 토큰만 사용 · Primitive 직접 참조 금지 · Light/Dark 분기 금지).

**문서**
- **위젯형 SaaS 표준(제품 비종속)** → [`WIDGET_SAAS_STANDARD.md`](./WIDGET_SAAS_STANDARD.md) · 위젯형 SaaS 표준 디자인 정책
- **서비스 디자인 가이드(최상위)** → [`SERVICE_DESIGN.md`](./SERVICE_DESIGN.md) · 위젯 홈 중심 종합 가이드(Prism)
- **비주얼 원칙** → [`anchor/DESIGN.md`](./anchor/DESIGN.md) · **UX·플로우·에셋 원칙** → [`PRINCIPLES.md`](./PRINCIPLES.md) · **컴포넌트 구조** → [`COMPONENTS.md`](./COMPONENTS.md) · **산정/로드맵** → [`COMPONENT_ROADMAP.md`](./COMPONENT_ROADMAP.md) · **로고** → [`LOGO.md`](./LOGO.md) · **게이미피케이션** → [`GAMIFICATION.md`](./GAMIFICATION.md)

**화면**
- **위젯 홈(중심)** → `preview/widget-home.html` · 위젯 추가·삭제·재배치·리사이즈 · 기능/정보 구분 · 온보딩 · 설정 팝업
- **위젯 카탈로그** → `preview/widgets.html` · 코드베이스 기반 풀기능 위젯 + 사이드바(조합/단일 바로가기)
- **컴포넌트 갤러리** → `preview/index.html` · 41개 컴포넌트
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
| `Button` | `variant`(Solid·Outline) × `color`(Primary·Secondary·Subtlest·Neutral·Inverse·Ghost·Danger) × `size`(Sm~3Xl) × `shape` · 구 API(primary/secondary/ghost/pill) 호환 | Anchor Button.md 계약 |
| `Input` | `field` · `composer`, `invalid`, `hint`, `label` | composer = 시그니처 Ask 웰(radius 16) |
| `Select` | `options`, `invalid`, `label` | 정해진 값 입력 |
| `Card` | `answer`(기본) · `source` · `feed` | source 는 hover 시 Primary lift |
| `Badge` | `neutral` · `pro` · `status` · `citation` · `success` · `error` · `warning` | Blue·카테고리색은 절제 |
| `Tabs` | `underline`(상단 내비) · `sidebar`(세로) | 활성: Primary 밑줄 / tint 하이라이트 |
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
| Primary | `Blue #1e84ff` (hover `#0066db` · deep `#004fad` · tint `rgba(30,132,255,.16)`) · 사용자 액션 · Accent = `Red #ff4e33`(정보 강조) |
| Surface | base(page) `#f4f5f7` · surface(card) `#ffffff` · 무채색 캔버스 |
| Ink | text.primary `#000000` · body `rgba(0,0,0,.88)` · muted `rgba(0,0,0,.48)` · disabled `rgba(0,0,0,.32)` (알파 기반) |
| Border | hairline `rgba(0,0,0,.08)` · soft `rgba(0,0,0,.04)` · 저대비 무채색 |
| Dark | base `#161718` · surface `#202122` · Blue `#66a8ff` |
| Font | Pretendard(UI·본문 단일 패밀리) · Berkeley Mono(코드) |
| Radius | 4 · 8(버튼 Square/칩) · 12(카드) · 16 · 24(시트) · 100(pill) |
| Shadow | low·medium·high 3단(순수 흑 알파). 깊이는 surface 대비가 1차, shadow는 보조 |

## 라이트 / 다크

라이트가 기본. 다크는 셋 중 하나로 켭니다:
- 루트에 `class="ds-dark"` 또는 `data-theme="dark"`
- 아무 것도 안 하면 `prefers-color-scheme: dark` 자동 따라감 (`ds-light`/`data-theme=light` 로 고정 가능)

## 핵심 원칙 (anchor/DESIGN.md 발췌)

1. 답변/콘텐츠가 주인공 · 크롬은 muted 회색으로 물러남
2. Blue `#1e84ff`(Primary)·Red `#ff4e33`(Accent) 는 **액션·강조 신호만**. 장식 금지
3. 무채색 캔버스 · 깊이는 surface 대비가 1차, shadow는 보조
4. 본문은 매거진처럼 · `ds-answer`(17px / 1.52 / 68ch)
5. 디자인 변경은 `tokens.json` + `theme.css` + `components.css` 만 고치면 전체 반영

## Prism UI(serve.py)와의 관계

현재 Prism UI는 `serve.py` 안의 인라인 Tailwind입니다. `/design-sync` 후 이 토큰/컴포넌트 기준으로 `serve.py` 마크업을 맞춰갈 수 있습니다. `tailwind.preset.cjs` 를 preset 으로 물리면 같은 토큰을 클래스로 바로 사용 가능합니다.
