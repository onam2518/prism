# Anchor Design System

> **Stitch DESIGN.md format** — AI 코딩 도구(Claude Code · Cursor · v0 등)가 Anchor 디자인 시스템을 일관되게 사용하도록 압축 정리한 단일 참조 문서.
> 버전 **v0.1** · ads-workspace 이관 2026-06-12

Anchor 디자인 시스템의 핵심 규칙을 빠르게 이해하기 위한 문서입니다.

**원본·빌드 구조**: 디자인 원본은 **Figma**(디자이너가 편집)이고, `figma-publish` 파이프라인이 이를 읽어 빌드용 토큰 스냅샷 [`snapshots/tokens/tokens.json`](../snapshots/tokens/tokens.json)으로 박제합니다. AI 도구는 이 문서를 먼저 참고한 뒤, 필요한 경우 `tokens.json`(정확한 값)과 프리뷰 사이트(시각 확인)를 추가로 참조합니다.

---

## About this document

- 이 문서는 **Anchor Design System의 Foundation 사용 가이드**입니다 — 디자인 시스템 전체를 설명하는 스펙 문서가 아닙니다.
- **단독 스펙이 아닙니다.** 반드시 `tokens.json`과 함께 사용해야 합니다.
- **Figma가 Source of Truth**이며, `tokens.json`은 Figma에서 빌드되고, 본 문서는 그 **사용 규칙**을 설명합니다.
- 본 문서 버전(**v0.1**)은 **토큰 스냅샷 버전과 별개**입니다 (토큰은 `figma-publish`로 독립적으로 버전업).

### Current Scope (v0.1)

| | 범위 |
|---|---|
| **포함** | Foundation · Color · Typography · Radius · Shadow · Token Rules · AI Usage Guide · **Button (대표 컴포넌트 → [`Button.md`](Button.md))** |
| **미포함** | Component Library (Button 외) · Pattern Library · Layout · Responsive · Motion · Accessibility |

> 미포함 영역 중 "언젠가 정의할 것"은 §10 Known Gaps·아래 Roadmap, "이 문서가 다루지 않는 것"은 Out of Scope 참고.

### Document Principles

- **Figma가 Source of Truth.**
- `tokens.json`은 Figma에서 생성된다 — 손으로 직접 고치지 않는다.
- DESIGN.md는 토큰 **사용 규칙**을 설명한다 (값 자체는 `tokens.json`).
- Primitive보다 **Semantic**을 사용한다.
- Component·Pattern은 **이후 버전**에서 확장한다 (→ Roadmap).

---

## 1. Visual Theme & Atmosphere

Anchor는 한국어 콘텐츠·검색·뉴스·커뮤니티 플랫폼(axz)의 디자인 시스템입니다.

**시스템 보이스 — 무채색 캔버스 + 도메인별 액센트.** 단일 브랜드 컬러 중심의 시스템과 달리, Anchor는 무채색 기반 표면 위에 **도메인별 식별색**을 적용하는 구조를 채택합니다. 뉴스·쇼핑·스포츠·연예·카페스토리·커뮤니티 등 성격이 다른 도메인이 한 플랫폼에 공존해야 하므로, 단일 브랜드색이 모든 도메인을 덮을 수 없다는 제약에서 출발했습니다. `Primary`(Blue)는 **사용자 액션**(버튼·링크), `Accent`(Red)는 **정보 강조**(속보·라이브·실시간)에 한정해 사용하며, 도메인 식별은 `*.category.*` 토큰이 담당합니다.

- **톤**: 명료한 정보 전달 · 절제된 뉴트럴 베이스 + 의미 있는 곳에만 액센트
- **베이스**: 무채색 스케일(black/white/gray) 기반의 차분한 표면
- **액센트**: Blue(Primary 액션) · Red(Accent · 속보·라이브 등 강조)
- **카테고리 컬러**: 도메인별 고유색 — 스포츠(Indigo) · 연예(Violet) · 카페스토리(Coral) · 관심사(Orange) · 커뮤니티(Lavender)
- **모드**: Light / Dark 듀얼 모드 필수. 같은 Semantic 토큰 이름이 모드에 따라 자동 swap
- **타입페이스**: Pretendard 단일 패밀리 (한국어·라틴 모두 최적화)
- **카테고리 다양성**: 단일 플랫폼이지만 도메인(뉴스·쇼핑·스포츠·연예·커뮤니티 등)별 식별성을 유지

---

## 2. Color Palette & Roles

- Primitive(atomic): 실제 색상값
- Semantic: 역할 기반 토큰

실제 화면 구현에서는 Semantic 토큰만 사용합니다.

### 표기 규칙 — Figma ↔ ads tokens.json ↔ 본 문서

같은 토큰이 Figma · 빌드 스냅샷(`tokens.json`) · 본 문서에서 **표기 방식만 다를 뿐 동일한 토큰**을 가리킵니다.

- **Figma 변수명**: 가독성을 위해 **Pascal Case 경로** — 예: `Gray/100` · `Background/Surface/Base`
- **ads tokens.json(빌드 저장 경로)**: Primitive는 `atomic.*`, Semantic은 `semantic.light.*` / `semantic.dark.*` (PascalCase, 모드가 경로에 포함)
- **본 문서**: Primitive·Radius는 ads 저장 경로(`atomic.*`)를 그대로, **Semantic·Shadow·Typography는 가독성을 위해 모드 무관 "역할/합성 경로"** 로 표기 (아래 매핑으로 ads 경로에 대응)

| 종류 | Figma | ads tokens.json 저장 경로 | 본 문서 표기 |
|---|---|---|---|
| Primitive | `Gray/100` | `atomic.Gray.100` | `atomic.Gray.100` |
| Semantic | `Background/Surface/Base` | `semantic.light.Background.Surface.Base` · `semantic.dark.…` | `background.surface.base` |
| Typography | `Body/Lg/Normal` | `atomic.Body.Lg-Normal-size` + `-weight` + `atomic.lineHeight.Body.Lg.Normal` | `body.large.normal` |
| Radius | `Radius/8` | `atomic.Radius.8` | `atomic.Radius.8` |
| Shadow | `Shadow/High` | `semantic.light.Shadow.High` · `semantic.dark.…` | `shadow.high` |

> **모드 자동 swap**: Semantic 토큰은 ads에서 `semantic.light.*`·`semantic.dark.*`로 따로 저장되지만, 소비처(dds-workspace)가 모드별 CSS로 빌드하므로 **사용 코드에선 모드 무관 역할 이름 하나**로 참조합니다. 그래서 본 문서는 `background.surface.base`처럼 모드 없는 경로로 적습니다.

### Primitive 그룹 (14개)

| 그룹 | 단계 | 비고 |
|---|---|---|
| `atomic.Black` | 20·50·100·200·300·400·500·600·700·800·900·1000 | 알파 채널 포함 |
| `atomic.White` | 20·50·100·200·300·400·500·600·700·800·900·1000 | 알파 채널 포함 |
| `atomic.Gray` | 50·100·200·300·400·500·600·700·800·900·1000 | 솔리드 |
| `atomic.Blue` | 50·100·200·300·400·500·600·700 + `Link.300`·`Link.700` | Primary 계열 |
| `atomic.Indigo` | 50·100·200·300·400·500·600·700 | 스포츠 |
| `atomic.Red` | 50·100·200·300·400·500·600·700 | Accent |
| `atomic.Coral` | 50·100·200·300·400·500·600·700 | 카페스토리 |
| `atomic.Orange` | 50·100·200·300·400·500·600·700 | 관심사 |
| `atomic.Yellow` | 50·100·200·300·400·500·600·700 | Pay 배지·커뮤니티 피드 |
| `atomic.Violet` | 50·100·200·300·400·500·600·700 | 연예 |
| `atomic.Lavender` | 50·100·200·300·400·500·600·700 | 커뮤니티 |
| `atomic.Purple` | 300·700 만 | 단계 보강 deferred |
| `atomic.Green` | 단일값 | 단계 보강 deferred |
| `atomic.Olive` | 단일값 | Dark 커뮤니티 피드 |

### Semantic 카테고리 (4종 · 모드당 96개 × Light/Dark 2모드 = 총 192개)

| 카테고리 | 역할 |
|---|---|
| `background` | 표면, 인터랙션 면, 카테고리 박스, 상태(hover/info/accent), 레이어(popup/snackbar/sheet/overlay) |
| `text` | 본문 계층(primary·secondary·tertiary·quaternary·subtle·disabled·inverse·link), static(고정색), state, category |
| `border` | 인풋, 섬네일, 인디케이터, 카테고리, 버튼 아웃라인, 디바이더 |
| `icon` | 본문 계층, static, category, inverse, link, state, navigation |

### 값이 없는 그룹 토큰 ⚠️

다음 3개 토큰은 **값이 없는 그룹 토큰**입니다 (자체 값 없음). 자식 키까지 명시해야 합니다:

| 부모 (사용 금지) | 자식 키 (사용) |
|---|---|
| `background.surface` | `.base` · `.on` · `.onlayer` · `.table` · `.thumbnail` · `.placeholder` · `.highlight` · `.widget` |
| `border.thumbnail` | `.default` · `.inverse` |
| `border.static.white` | `.default` · `.divider` |

예시: ❌ `background.surface` (직접 사용 금지 · 값 없음) → ✅ `background.surface.base`

### Static 변종

이름에 `static`이 들어간 토큰(`text.static.*`, `icon.static.*`, `border.static.*`)은 **모드와 무관하게 고정**됩니다. 딤 영역 위 흰 배지 텍스트처럼 모드에 영향받지 않아야 할 때만 사용합니다.

**자세히는**: [`snapshots/tokens/tokens.json`](../snapshots/tokens/tokens.json) (`atomic.*` + `semantic.light/dark.*`) · 프리뷰 사이트(`site-dist/index.html`)에서 색상 스와치·semantic 매핑 시각 확인

---

## 3. Typography Rules

### 패밀리

```
fontFamily = "Pretendard"
```

### 타이포그래피 토큰 (21개)

> 본 문서는 합성 표기(`display.large` = size/weight/lineHeight 묶음)로 적습니다. ads `tokens.json`에선 `atomic.<Cat>.<Size>-size` / `-weight` + `atomic.lineHeight.<Cat>.<…>`로 분해 저장됩니다 (예: `body.large.normal` → `atomic.Body.Lg-Normal-size` + `atomic.Body.Lg-Normal-weight` + `atomic.lineHeight.Body.Lg.Normal`). `header`는 ads에서 `atomic.Title.Xl-*`로 저장됩니다.

| 토큰 | px / weight / lineHeight | 용도 |
|---|---|---|
| `display.large` | 40 / 700 / 1.2 | PC 웹 대 타이틀 |
| `display.medium` | 26 / 700 / 1.2 | PC 웹 소 타이틀 |
| `header` | 24 / 700 / 1.2 | 다음 메인 헤더 |
| `title.large` | 22 / 700 / 1.2 | 통검 정답형 / 홈탭 구독 언론사 / 에러화면 |
| `title.medium` | 20 / 700 / 1.2 | 콘텐츠 메뉴 타이틀 (백버튼 상단) |
| `title.small` | 18 / 700 / 1.2 | 기본형 / 단수형 컬렉션 타이틀 |
| `body.large.normal` | 17 / 400 / 1.4 | 본문 기본 텍스트 |
| `body.large.emphasis` | 17 / 600 / 1.4 | 본문 기본 (앱 전용) |
| `body.large.strong` | 17 / 700 / 1.4 | 본문 부분 강조 |
| `body.large.readingNormal` | 17 / 400 / 1.52 | 본문 글줄이 긴 경우 (기사·문서형) |
| `body.large.readingStrong` | 17 / 700 / 1.52 | 본문 글줄이 긴 경우 (부분 강조) |
| `body.medium.normal/strong` | 16 / 400 or 700 / 1.32 | 컬럼형 부가정보 |
| `body.small.normal/emphasis/strong` | 15 / 400·600·700 / 1.32 | 1뎁스 탭·버튼 |
| `caption.normal/strong` | 14 / 400 or 700 / 1.32 | 부가정보 |
| `label.normal/strong` | 12 / 400 or 700 / 1.2 | 배지 |

### 규칙

- **굵기 enum**: 400 (normal) · 600 (emphasis, 앱 전용 제한) · 700 (strong)
- **줄간격**: 1.2 (헤딩) · 1.32 (본문 기본) · 1.4 (긴 본문) · 1.52 (장문 reading)
- **사이즈 스케일**: 12 · 14 · 15 · 16 · 17 · 18 · 20 · 22 · 24 · 26 · 40 (11 단계)

**자세히는**: [`snapshots/tokens/tokens.json`](../snapshots/tokens/tokens.json) (`atomic.Display/Title/Body/Caption/Label` + `atomic.lineHeight`)

---

## 4. Component Stylings

> 컴포넌트 정의는 Figma 라이브러리(`Anchor Design System`)에 있으며, 본 저장소는 토큰을 관리하고 추출된 컴포넌트 스냅샷([`snapshots/components/components.json`](../snapshots/components/components.json))을 둡니다. 아래는 토큰 + Figma 컴포넌트 매트릭스에서 추출한 요약입니다.

### Button — 대표 컴포넌트

> 사용자가 원하는 동작을 수행하도록 돕는 요소. 화면에서 중요한 행동을 강조하거나 주요 작업을 완료할 때 사용.

Button은 v0.1의 **유일한 상세 정의 컴포넌트**입니다. Props · 유효 조합 · 토큰 바인딩 · 사이즈 스케일 · State · 접근성 · Do/Don't 전체 사양은 별도 문서가 **단일 소스**입니다:

→ **[`docs/Button.md`](Button.md)** (Anchor Button)

요약: `Variant`(Solid · Outline) × `Color`(Primary · Secondary · Subtlest · Neutral · Inverse · Ghost · Danger) × `Size`(Sm~3Xl) × `Shape`(Square=R8 · Rounded=R100) × `State`(Default · Hover · Focus · Loading · Disabled). 모든 값은 시맨틱 토큰 ref로만 바인딩(inline HEX 금지)하며 Light/Dark 자동 swap됩니다. **프로덕트에서 쓰는 조합은 Button.md의 유효 조합표(사용 계약)만** 따릅니다 — Figma에 존재한다고 다 쓸 수 있는 건 아닙니다.

### Input (Border 토큰만)

| State | Token |
|---|---|
| Default | `border.input.default` |
| Hover | `border.input.hover` |
| Focus | `border.input.focus` |

### 기타 컴포넌트

대부분의 컴포넌트(Card · Modal · Tab · Snackbar 등)는 본 저장소에 별도 토큰 매트릭스가 없습니다. Figma 라이브러리를 source로 삼되, 색상·간격·둥글기·그림자는 항상 토큰을 통해 참조합니다.

---

## 5. Layout Principles

**(미정 — v0.1에서는 정의되지 않음)**

AI는 Grid · Breakpoint · Container max-width를 임의로 추정하지 말고, 필요 시 사용자 확인을 요청해야 합니다. 향후 정의 예정이며, 현재는 다음 단편적 정보만 활용 가능합니다:
- Space scale: `2 · 4 · 6 · 8 · 10 · 12 · 16 · 18 · 20 · 24 · 32 · 40` px (12 단계, `atomic.Spacing.*`)
- Spacing semantic 토큰은 deferred (§10)
- Button은 hug-content (고정 폭 없음, 텍스트 길이 + padding으로 결정)

---

## 6. Depth & Elevation

### 깊이의 원칙

Anchor의 깊이는 **shadow가 아닌 layer 토큰의 surface 대비**로 표현합니다. `background.surface.base` → `.on` → `.onlayer`의 명도 차가 1차 깊이를, `background.layer.popup` · `.snackbar` · `.sheet`가 2차 부유층을 담당하며, `shadow.*`는 떨어진 광원의 인상을 더하는 보조 수단입니다. shadow가 단독으로 elevation을 표현하지 않으므로, 카드를 띄울 때는 **반드시 한 단계 위의 surface 토큰을 함께 적용**해야 합니다 (`surface.base` 위에 `surface.on` 카드 + `shadow.medium`처럼).

### Radius (둥글기)

| 토큰 | 값 |
|---|---|
| `atomic.Radius.4` | 4px |
| `atomic.Radius.8` | 8px |
| `atomic.Radius.12` | 12px |
| `atomic.Radius.16` | 16px |
| `atomic.Radius.24` | 24px |
| `atomic.Radius.100` | 100px (원형 / Pill) |

Radius semantic 토큰은 deferred.

### Shadow

그림자는 **Light/Dark 모드 토큰**입니다 — 컬러 Semantic처럼 모드 무관 이름 하나(`shadow.*`)로 참조하면 모드가 자동 swap됩니다. ads 저장 경로는 `semantic.light.Shadow.*` / `semantic.dark.Shadow.*` (전용 `Shadow` 컬렉션, Light/Dark 2모드). **깊이 단계(elevation)로 3종**이며, offset·blur는 모드 동일하고 색 알파만 Dark에서 더 진해집니다(세 종 모두 Light/Dark 값이 다름):

| 토큰 (모드 무관 표기) | offset / blur / color (Light · Dark) | 용도 |
|---|---|---|
| `shadow.high` | 0,2 / 16 / `rgba(0,0,0,0.16)` (Light) · `rgba(0,0,0,0.32)` (Dark) | 높은 부유층 — 팝업·레이어 |
| `shadow.medium` | 0,1 / 10 / `rgba(0,0,0,0.08)` (Light) · `rgba(0,0,0,0.16)` (Dark) | 중간 — 콘텐츠 카드 |
| `shadow.low` | 0,0 / 4 / `rgba(0,0,0,0.04)` (Light) · `rgba(0,0,0,0.08)` (Dark) | 낮은 — PC 박스 등 미세 입체 |

### 레이어 위계 (Semantic Background)

층위는 `background.layer.*` 토큰으로 표현:
- `popup` → `snackbar` → `sheet` → `on` → `overlay` → `overlaySubtle` → `overlaySubtlest`
- Overlay 3종은 딤·반투명 — 위쪽 컨텐츠와 겹쳐 어둡게 처리

---

## 7. Do's and Don'ts

### ✅ Do

- ✅ **Semantic 토큰만 사용** — Light/Dark 자동 swap을 위해
- ✅ 값이 없는 그룹 토큰은 **자식 경로까지** 명시 — `background.surface.base`, `border.thumbnail.default`
- ✅ Light/Dark 분기 코드 작성 금지 — 동일 토큰 이름이면 시스템이 알아서 swap
- ✅ Typography는 **계층 의미**에 맞춰 선택 — 크기만 보고 고르지 말 것
- ✅ 새 컴포넌트 만들 때 **유효 조합 매트릭스**(`§4`) 안에서만 조합

### ❌ Don't

- ❌ `background.surface` 같은 **값이 없는 그룹 토큰 직접 참조** — 값이 없음
- ❌ UI 코드/스타일에 `atomic.Blue.500` 같은 **Primitive 직접 사용** — Dark swap 깨짐
- ❌ HEX 값(`#1E84FF` 등) **하드코딩** — 토큰 이름으로 참조
- ❌ 토큰에 없는 임의의 색·간격·둥글기 값 **새로 도입** — 먼저 적합한 Semantic 검색
- ❌ `static` 변종을 일반 텍스트/아이콘에 사용 — 의도적 고정색 케이스에만
- ❌ 카테고리 컬러를 본문(`text.category.*`)과 같은 영역 배경(`background.category.*`)에 **동시 사용** — 가독성 깨짐, 배경은 `*.Subtle` 변종 사용
- ❌ `text.static.white.*` · `icon.static.white.*`를 **일반 카드 위에 사용** — Dark 모드에서 흰 배경 위 흰 텍스트로 사라짐. 딤 오버레이·짙은 카테고리 배경 위에서만
- ❌ `atomic.Purple.500` 같은 **정의되지 않은 단계 참조** — purple은 `300·700`만, `atomic.Green`·`atomic.Olive`는 단일값 (스케일 없음)

---

## 8. Responsive Behavior

**(미정 — v0.1에서는 정의되지 않음)**

부분적으로 추론 가능한 단서:
- Typography 용도에 "**PC 웹 전용**" (display.large/medium) · "**앱에서만 제한적으로**" (`*.emphasis` 굵기 600) 표기 일부 있음
- Shadow는 깊이 단계(`shadow.high`/`medium`/`low`) 3종으로, 용도가 이름에 고정되지 않은 elevation 스케일
- 브레이크포인트 · 그리드 · 디바이스별 토큰 분기 정의 없음

---

## 9. Agent Prompt Guide

AI 코딩 도구가 Anchor를 사용해 작업할 때의 **권장 워크플로우**.

### 시작 시 컨텍스트

```
@docs/DESIGN.md                       # 본 문서 — 시스템 구조 + 사용 규칙 (진입점)
@docs/Button.md                       # 대표 컴포넌트 Button 상세 사양 (단일 소스)
@snapshots/tokens/tokens.json         # 빌드 토큰 스냅샷 (atomic + semantic.light/dark)
@snapshots/components/components.json # 추출된 컴포넌트 스냅샷
```

### 작업 단계별 추가 참조

| 작업 | 추가로 컨텍스트에 포함할 위치 |
|---|---|
| 색상 결정 | `tokens.json`의 `atomic.*`(색상) · `semantic.light/dark.*` |
| 간격 결정 | `tokens.json`의 `atomic.Spacing.*` |
| 둥글기 결정 | `tokens.json`의 `atomic.Radius.*` |
| 폰트 결정 | `tokens.json`의 `atomic.Display/Title/Body/Caption/Label` + `atomic.lineHeight` |
| 그림자 결정 | `tokens.json`의 `semantic.light/dark.Shadow.*` |

### 새 컴포넌트/화면 생성 시 체크리스트

1. **Semantic 토큰만** 사용했는가 (Primitive 직접 X)
2. **값이 없는 그룹 토큰의 자식 경로** 명시했는가 (`background.surface.base` ✅ / `background.surface` ❌)
3. **Light/Dark 분기 없이** 토큰 이름만 참조했는가
4. (Button) **유효 조합표** 안의 조합인가 — [`Button.md`](Button.md) 사용 계약 참조
5. **정의되지 않은 영역**(Layout, Responsive)을 임의 결정하지 않았는가 — 미정이면 사용자 확인

### 토큰 변경/추가가 필요할 때

- 토큰의 **진실 원본은 Figma**입니다. 토큰 값·이름을 바꾸려면 **Figma에서 수정**합니다 — `tokens.json`을 직접 손으로 고치지 않습니다.
- 반영은 `figma-publish` 스킬(`/figma-publish tokens`)로 합니다 → Figma를 읽어 `tokens.json`을 재생성하고 버전을 올립니다. (→ [`.claude/rules/figma-publish.md`](../.claude/rules/figma-publish.md))
- 카테고리 버전(`_meta.version`)은 **publish 스킬이 관리** — 임의로 bump하지 않습니다.

### Anchor만의 주의사항

- `atomic.Green`, `atomic.Olive`는 **단일값 토큰** — 스케일 없음. 다른 컬러처럼 `.500` 붙이면 안 됨
- `atomic.Purple`은 **300, 700만** 존재 — 다른 단계 참조 금지
- Semantic은 ads에서 `semantic.light.*`·`semantic.dark.*`로 나뉘어 저장되지만, **사용 시엔 모드 무관 역할 이름**으로 참조 (모드 swap은 소비처 빌드가 담당)

---

## 10. Known Gaps

v0.1 시점에 **의도적으로 미확정**으로 둔 영역. 시스템이 결정한 적 없으므로 AI 도구는 해당 영역을 추정하거나 새로운 규칙을 생성하지 말고, 사용자 확인 또는 별도 정의를 요청해야 합니다.

| 영역 | 현재 상태 | 참조 |
|---|---|---|
| Spacing semantic 토큰 | Primitive 12단계(`atomic.Spacing.*`)만 정의 / semantic 미정 | §5 |
| Radius semantic 토큰 | Primitive 6단계(`atomic.Radius.*`)만 정의 / semantic 미정 | §6 |
| Layout · Grid · Container max-width | 미정 (PC 단편 정보만) | §5 |
| Responsive Breakpoint · 디바이스 분기 | 미정 | §8 |
| 컴포넌트 토큰 바인딩 (Card · Modal · Tab · Snackbar · Bottom Sheet · Badge 등) | Figma 라이브러리에 컴포넌트 정의 / 토큰 매트릭스 부재 | §4 |
| Button — `Pressed` state | enum 미정의 (`Focus`는 §4에서 정의됨) | §4 |
| `atomic.Purple` 단계 보강 | `300·700`만 존재 | §2 |
| `atomic.Green` · `atomic.Olive` 스케일 | 단일값 (스케일 없음) | §2 |
| Form validation state (error · success 입력) | 미정 | — |
| Animation · transition timing | 시스템 범위 외 | — |
| Iconography 시스템 | 본 저장소 범위 외 (별도 트랙) | — |

미확정 영역이 작업에 막히는 경우, Figma에서 결정해 `/figma-publish`로 토큰화하거나 사용자 확인을 거칩니다.

---

## Out of Scope

> **Known Gaps(§10)와 구분** — Known Gaps는 "아직 정의되지 않았지만 **언젠가 정의할**" 영역이고, Out of Scope는 "**이 문서가 다루지 않는**" 영역입니다.

본 문서(v0.1 Foundation + Token Guide)는 아래를 다루지 않습니다. AI 도구는 이 영역의 규칙을 임의로 생성하지 말고 사용자 확인을 요청해야 합니다.

- Grid System
- Motion / Transition
- Accessibility 규칙
- Icon Library
- Product-specific UI
- Service Pattern (Search · News · Comment 등)

---

## Adoption Guide

본 문서(v0.1)를 실제 작업에 **적용하는 정책**입니다.

- **신규 서비스·신규 프로젝트에 우선 적용**합니다.
- 기존 서비스(다음앱 등)는 **디자인팀 협의 후** 적용합니다.
- 실제 배포 전에는 반드시 **디자인 리뷰(Design Filtering)** 를 거칩니다.
- 본 문서는 **지속적으로 업데이트**됩니다 (확장 계획은 아래 Roadmap).

---

## Roadmap

> 본 문서의 **단계별 확장 계획**. v0.1은 현재 문서 = Foundation + Token Guide + 대표 컴포넌트(Button).

| 버전 | 범위 | 목적 |
|---|---|---|
| **v0.1** (현재) | Foundation · Token Guide · **Core Component(Button)** · Input(현 수준) | Foundation·토큰 사용 원칙 + Button으로 토큰의 실제 UI 적용 방식 이해 |
| **v0.2** | **Component Library** (아래) | AI가 다양한 컴포넌트를 일관된 규칙으로 생성 |
| **v0.3** | Service Patterns (Search · News · Comment) | — |
| **v1.0** | Layout · Responsive · Accessibility · Motion | — |

**v0.2 Component Library 추가 대상**: Input(Button 수준 상세화) · Card · Chip · Badge · Tab · Dialog · Bottom Sheet · Navigation · List Item · Search Input
**공통 템플릿**: Overview · Anatomy · Props · States · Rules · Token Mapping · Examples (가능하면 `components.json` 기반 자동 생성 권장)

---

## Related

- 대표 컴포넌트 상세: [`docs/Button.md`](Button.md)
- 토큰 스냅샷(빌드용): [`snapshots/tokens/tokens.json`](../snapshots/tokens/tokens.json)
- 컴포넌트 스냅샷: [`snapshots/components/components.json`](../snapshots/components/components.json)
- 디자인 시스템 프리뷰 사이트: [`site/`](../site/) → 생성물 `site-dist/index.html`
- 배포 파이프라인: [`.claude/rules/figma-publish.md`](../.claude/rules/figma-publish.md) · `figma-publish` 스킬
- 저장소 개요: [`README.md`](../README.md)
