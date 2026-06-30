---
name: Button
version: v0.1.1
status: WIP — 사양 동결 전
figma: https://www.figma.com/design/lgfSHlSDZjwG3NdDlwhThp?node-id=3578-4283
references:
  - seed: https://seed-design.io/docs/components/button
  - atlassian: https://atlassian.design/components/button/examples
  - shadcn: https://ui.shadcn.com/docs/components/button
---

# Button

> **용도** — 사용자가 원하는 동작을 수행하도록 돕는 요소. 화면에서 중요한 행동을 강조하거나, 주요 작업을 완료할 때 사용.
>
> **키워드**: Button · Btn · 버튼 · CTA · Call to Action

---

## Overview — 위계와 원칙

버튼은 화면에서 **"지금 할 수 있는 행동"을 시각적 무게로 서열화**하는 요소다. Anchor Button은 그 서열을 세 축으로 표현한다 — **색(Color)으로 중요도**, **변형(Variant)으로 강조 강도**, **모양·크기(Shape·Size)로 맥락**. 사용자는 색과 면적만 보고도 "여기서 가장 중요한 행동이 무엇인지"를 읽어낼 수 있어야 한다.

**핵심 원칙**

- **한 화면에 핵심 액션은 하나.** `Solid / Primary`는 그 화면에서 사용자가 완료하길 바라는 **단일 주요 행동**(저장·다음·구매)에만 쓴다. Primary가 둘 이상이면 위계가 무너진다.
- **보조 행동은 무게를 낮춘다.** 취소·뒤로·대체 경로는 `Neutral`·`Ghost`로 내려, Primary가 시각적으로 도드라지게 둔다.
- **파괴적 행동은 색으로 경고한다.** 삭제·영구 변경처럼 되돌리기 어려운 액션은 `Danger`로 칠한다. Danger는 강조가 아니라 경고다.
- **Variant = 강조 강도.** `Solid`는 면(fill)으로 가장 강하게, `Outline`은 테두리로 한 단계 낮게 말한다.
- **Shape·Size는 맥락이 정한다.** `Square`(R8)가 기본, `Rounded`(R100)는 브랜드·캡슐형 맥락에 한정(허용표 참조). 폼·툴바는 작게, 히어로·랜딩 CTA는 크게.
- **모양을 바꾸면 포커스 링도 따라간다.** 포커스 링 R = 버튼 R + 2 (Square 8 → 10).

---

## 1. Props

| Property | Type (enum) | Default | 비고 |
|---|---|---|---|
| `Variant` | `Solid` · `Outline` | `Solid` | |
| `Color` | `Primary` · `Secondary` · `Subtlest` · `Neutral` · `Inverse` · `Ghost` · `Danger` | `Primary` | `Secondary`=Solid 전용 |
| `Size` | `Sm` · `Md` · `Lg` · `Xl` · `2Xl` · `3Xl` | `Lg` | |
| `Shape` | `Square` · `Rounded` | `Square` | Square = R8, Rounded = R100 |
| `State` | `Default` · `Hover` · `Focus` · `Loading` · `Disabled` | `Default` | Pressed 미정의 |
| `◐LeadingIcon` | Boolean | `False` | 앞 아이콘 슬롯 (16px) |
| `◐TrailingIcon` | Boolean | `False` | 뒤 아이콘 슬롯 (16px) |
| `IconVariant` | Icon 라이브러리 에셋 | `Icon/Normal/Blank` | 두 슬롯이 공유하는 글리프 swap |

> `◐` = 슬롯 표기. Leading/Trailing은 variant 축이 아니라 **인스턴스 토글 + 아이콘 swap 슬롯**이다.

### 유효 조합 (Allowed Combinations) — 사용 계약

> Figma에는 개발 편의를 위해 거의 모든 조합이 빌드돼 있지만, **프로덕트에서 쓰는 것은 아래 허용표뿐**이다. Figma에 존재한다고 해서 사용 가능한 조합은 아니다.

| Variant | 허용 Color | Size | Shape | ◐Leading | ◐Trailing |
|---|---|---|---|---|---|
| `Solid` | Primary · Secondary · Neutral · Inverse · Ghost · Danger | Sm · Md · Lg · 2Xl · 3Xl | `Square` | Allow | **Disallow** |
| `Solid` | Secondary · Inverse | Sm · Md · Lg · 2Xl · 3Xl | `Rounded` | Allow | **Disallow** |
| `Outline` | Neutral · Ghost · Subtlest | Sm · Md · Lg · 2Xl · 3Xl | `Square` | Allow | Allow |
| `Outline` | Ghost | **Xl** | `Rounded` | Allow | Allow |

**규칙**
- **Trailing 아이콘은 Solid에서 금지**, Outline에서만 허용.
- **`Xl` 사이즈는 `Outline · Ghost · Rounded`에서만.** 나머지 행은 Sm·Md·Lg·2Xl·3Xl(5단계).
- **Rounded는 색 제한**: Solid Rounded = Secondary·Inverse만, Outline Rounded = Ghost(Xl)만.
- `Subtlest`는 **Outline Square에서만** 허용.
- `Loading` 상태는 라벨·아이콘 숨김 → 스피너(토글값 무관).

---

## 2. Anatomy

```
┌───────────────────────────────────────────┐  ← HoverLayer (absolute inset -1px, 전 variant 공통 오버레이)
│  [◐LeadingIcon]   라벨(Label)   [◐TrailingIcon]  │
└───────────────────────────────────────────┘  ← FocusRing (Focus 상태에서만, absolute inset -2px)
   padding(V×H)·gap·min-height = Size로 결정
   배경/테두리/텍스트색 = Variant×Color로 결정
   radius = Shape로 결정 (Square 8 / Rounded 100)
```

- **Label**: 필수. typography = Size, 색 = Variant×Color. hug-content.
- **◐LeadingIcon / ◐TrailingIcon**: 선택. 16px, gap = Size. `IconVariant`로 글리프 swap.
- **HoverLayer**: 모든 variant에 깔린 투명 오버레이 — Hover 상태에서 `{background.state.hover}` 표시.
- **FocusRing**: Focus 상태에서만 나타나는 별도 레이어.

---

## 3. 토큰 바인딩

> 스펙 바인딩은 semantic 토큰 ref가 정본이다. 각 토큰이 Light/Dark에서 어떤 atomic·실제값으로 풀리는지는 아래 **§3-0 해석표**에 모았으므로 — Figma·토큰 레포를 따로 열지 않아도 이 문서만으로 값을 따라갈 수 있다. (출처: `tokens.json` v0.5.1 · 치수·타입은 `DESIGN.md`)

### 3-0. 토큰 해석표 (`tokens.json` v0.5.1)

> 모드에 따라 atomic이 갈리는 토큰은 Light/Dark를 함께 적는다.

**배경 (background)**

| 토큰 | Light | Dark |
|---|---|---|
| `{background.interaction.primary}` | `atomic.Blue.500` `#1e84ff` | `atomic.Blue.500` `#1e84ff` |
| `{background.interaction.secondary}` | `atomic.Gray.800` `#303233` | `atomic.Gray.50` `#f4f5f7` |
| `{background.interaction.neutral}` | `atomic.Gray.100` `#e4e6e8` | `atomic.Gray.500` `#74797f` |
| `{background.interaction.inverse}` | `atomic.White.1000` `#ffffff` | `atomic.Gray.500` `#74797f` |
| `{background.interaction.subtlest}` | `atomic.Black.20` `rgba(0,0,0,.02)` | `atomic.White.20` `rgba(255,255,255,.02)` |
| `{background.interaction.danger}` | `atomic.Red.500` `#ff4e33` | `atomic.Red.500` `#ff4e33` |
| `{background.interaction.disabled}` | `atomic.Gray.200` `#c7cbcf` | `atomic.Gray.600` `#5b5f65` |
| `{background.state.hover}` | `atomic.Black.50` `rgba(0,0,0,.04)` | `atomic.White.50` `rgba(255,255,255,.04)` |

**텍스트 (text)**

| 토큰 | Light | Dark |
|---|---|---|
| `{text.static.white.primary}` | `atomic.White.1000` `#ffffff` | `atomic.White.1000` `#ffffff` |
| `{text.inverse}` | `atomic.White.1000` `#ffffff` | `atomic.Black.1000` `#000000` |
| `{text.secondary}` | `atomic.Black.900` `rgba(0,0,0,.88)` | `atomic.White.900` `rgba(255,255,255,.88)` |
| `{text.disabled}` | `atomic.Black.500` `rgba(0,0,0,.32)` | `atomic.White.500` `rgba(255,255,255,.32)` |
| `{text.state.info}` | `atomic.Blue.500` `#1e84ff` | `atomic.Blue.500` `#1e84ff` |
| `{text.state.accent}` | `atomic.Red.500` `#ff4e33` | `atomic.Red.500` `#ff4e33` |

**아이콘 (icon)**

| 토큰 | Light | Dark |
|---|---|---|
| `{icon.static.white.primary}` | `atomic.White.1000` `#ffffff` | `atomic.White.1000` `#ffffff` |
| `{icon.inverse}` | `atomic.White.900` `rgba(255,255,255,.88)` | `atomic.Black.900` `rgba(0,0,0,.88)` |
| `{icon.secondary}` | `atomic.Black.900` `rgba(0,0,0,.88)` | `atomic.White.900` `rgba(255,255,255,.88)` |
| `{icon.subtlest}` | `atomic.Black.300` `rgba(0,0,0,.16)` | `atomic.White.300` `rgba(255,255,255,.16)` |
| `{icon.state.info}` | `atomic.Blue.500` `#1e84ff` | `atomic.Blue.500` `#1e84ff` |

**테두리 (border)**

| 토큰 | Light | Dark |
|---|---|---|
| `{border.button.outline}` | `atomic.Black.200` `rgba(0,0,0,.1)` | `atomic.White.300` `rgba(255,255,255,.16)` |
| `{border.focus}` (공통 전용 포커스) | `atomic.Blue.500` `#1e84ff` | `atomic.Blue.500` `#1e84ff` |

**치수·타입 (모드 무관 · `DESIGN.md` + tokens.json atomic)**

| 토큰 | 값 |
|---|---|
| `{atomic.Radius.8}` / `{atomic.Radius.100}` | 8px / 100px |
| `{atomic.Spacing.4 / .6 / .8 / .10 / .12 / .16 / .20}` | 4 / 6 / 8 / 10 / 12 / 16 / 20 px |
| `{caption.strong}` | 14px / 700 / 1.32 |
| `{body.small.strong}` | 15px / 700 / 1.32 |
| `{body.medium.strong}` | 16px / 700 / 1.32 |
| `{body.large.strong}` | 17px / 700 / 1.4 |

### 3-1. Color 매트릭스 (Variant=Solid · State=Default)

| Color | backgroundColor | textColor | iconColor |
|---|---|---|---|
| `Primary` | `{background.interaction.primary}` | `{text.static.white.primary}` | `{icon.static.white.primary}` |
| `Secondary` | `{background.interaction.secondary}` | `{text.inverse}` | `{icon.inverse}` |
| `Subtlest` | `{background.interaction.subtlest}` | `{text.secondary}` | `{icon.secondary}` |
| `Neutral` | `{background.interaction.neutral}` | `{text.secondary}` | `{icon.secondary}` |
| `Inverse` | `{background.interaction.inverse}` | `{text.secondary}` | `{icon.secondary}` |
| `Ghost` | `transparent` | `{text.secondary}` | `{icon.secondary}` |
| `Danger` | `{background.interaction.danger}` | `{text.static.white.primary}` | `{icon.static.white.primary}` |

> `{background.interaction.subtlest}`는 `tokens.json` v0.5.1에 게시됨 (Light `Black.20` / Dark `White.20`).

**색 사용 가이드 (위계 순)**

| Color | 위계 | 언제 쓰나 |
|---|---|---|
| `Primary` | 최상 | 화면당 단일 핵심 액션(저장·다음·제출·구매) |
| `Secondary` | 상 | Primary 옆 두 번째 강조, 또는 짙은 강조가 필요한데 브랜드색은 양보할 때 (Solid 전용) |
| `Danger` | 맥락 | 삭제·영구 변경 등 파괴적 액션 — 위험 신호 |
| `Neutral` | 중 | 일반 보조 액션(취소·뒤로·필터) |
| `Inverse` | 중 | 다크·짙은 표면 위 보조 액션 |
| `Ghost` | 하 | 밀집 영역·인라인·툴바의 저강조 액션 |
| `Subtlest` | 최하 | 거의 배경에 녹는 초저강조 (Outline Square 전용) |

> 실제 무게는 **색 × Variant**로 결정된다 — 같은 `Neutral`이라도 Solid는 보조 액션, Outline은 그보다 한 단계 낮게 읽힌다.

### 3-2. Outline 매트릭스 (Variant=Outline · State=Default)

모든 Outline 공통: `border: 1px solid {border.button.outline}` + rounded(Square `{atomic.Radius.8}` / Rounded `{atomic.Radius.100}`).

| Color | backgroundColor | textColor | 부류 |
|---|---|---|---|
| `Primary` | `transparent` | `{text.state.info}` | 강조색 — 투명 + 의미색 텍스트 |
| `Danger` | `transparent` | `{text.state.accent}` | 강조색 — 투명 + 의미색 텍스트 |
| `Ghost` | `transparent` | `{text.secondary}` | 무채 — 투명 |
| `Neutral` | `{background.interaction.neutral}` | `{text.secondary}` | 표면색 — fill 유지 |
| `Inverse` | `{background.interaction.inverse}` | `{text.secondary}` | 표면색 — fill 유지 |
| `Subtlest` | `{background.interaction.subtlest}` | `{text.secondary}` | 표면색 — fill 유지 |

### 3-3. 사이즈 스케일 (전 variant 공통)

| Size | typography | padding (수직 × 수평) | gap (icon) | min-height |
|---|---|---|---|---|
| `Sm` | `{caption.strong}` (14) | `{atomic.Spacing.6}` × `{atomic.Spacing.8}` | `{atomic.Spacing.4}` | 32px |
| `Md` | `{body.small.strong}` (15) | `{atomic.Spacing.8}` × `{atomic.Spacing.12}` | `{atomic.Spacing.4}` | 36px |
| `Lg` | `{body.small.strong}` (15) | `{atomic.Spacing.10}` × `{atomic.Spacing.16}` | `{atomic.Spacing.4}` | 40px |
| `Xl` | `{body.small.strong}` (15) | `{atomic.Spacing.12}` × `{atomic.Spacing.16}` | `{atomic.Spacing.4}` | 44px |
| `2Xl` | `{body.medium.strong}` (16) | `{atomic.Spacing.12}` × `{atomic.Spacing.16}` | `{atomic.Spacing.8}` | 48px |
| `3Xl` | `{body.large.strong}` (17) | `{atomic.Spacing.16}` × `{atomic.Spacing.20}` | `{atomic.Spacing.8}` | 56px |

### 3-4. Shape (radius)

| Shape | rounded |
|---|---|
| `Square` | `{atomic.Radius.8}` |
| `Rounded` | `{atomic.Radius.100}` |

### 3-5. State

```yaml
states:
  default:  {}                                          # 기본값
  hover:    { overlay: "{background.state.hover}" }      # HoverLayer 오버레이(전 variant 공통)
  focus:    { ring: "2px solid {border.focus}",          # 전용 공통 포커스 토큰(=Blue.500)
              ringOffset: "2px (inset -2px)",
              ringRadius: "버튼 R + 2 (Square 8 → 10)" }
  loading:  { label: hidden, icon: hidden,               # 라벨·아이콘 숨김
              spinner: "24px 중앙",
              width: "고정 (hug 해제 — 레이아웃 시프트 방지)" }
  disabled: { backgroundColor: "{background.interaction.disabled}",
              textColor: "{text.disabled}",
              iconColor: "{icon.subtlest}" }
```

---

## 4. 접근성 · 터치 타깃

- **최소 터치 타깃 44×44.** `Sm`(32)·`Md`(36)·`Lg`(40)은 이에 못 미치므로 **모바일·터치 우선 맥락에서는 `2Xl`(48)·`3Xl`(56)**을 쓰거나 충분한 hit-area 여백을 둔다. `Xl`(44)은 허용표상 `Outline · Ghost · Rounded` 전용이라 일반 터치 타깃 용도로는 못 쓴다. `Sm`~`Lg`는 포인터(데스크톱) 밀집 UI에 한정.
- **Loading은 고정폭으로 레이아웃 시프트를 막는다.** 라벨·아이콘을 숨기고 스피너로 바꿀 때 hug를 풀어 폭을 고정한다.
- **Disabled 대비.** `{text.disabled}`/`{background.interaction.disabled}` 조합은 의도적으로 저대비다 — **중요한 정보를 disabled 버튼에만 담지 말 것**. 비활성 사유는 별도 안내로 보완한다.
- **아이콘 단독 의미 금지.** 아이콘만으로 동작을 전달해야 하면 `aria-label`로 텍스트 대체를 제공한다.
- **포커스 가시성.** Focus 링은 2px·offset 2px로 키보드 사용자에게 또렷해야 한다. 링 색·두께를 임의로 줄이지 않는다.

---

## 5. Do's & Don'ts

### Do
- **화면당 `Solid / Primary`는 하나만.** 그 화면의 단일 핵심 액션에만 쓴다.
- **보조 액션은 `Outline / Neutral`(또는 `Ghost`)로 내린다.**
- **파괴적 액션은 `Danger`로.**
- **다크 표면 위에서는 `Inverse`를 쓴다.**
- **허용표(§1)에 있는 조합만 프로덕트에 쓴다.**
- **`Square`(R8)를 기본으로.** `Rounded`(R100)는 허용된 색·맥락에만.
- **R을 바꾸면 포커스 링 R도 함께(버튼 R + 2).**
- **터치 우선 맥락은 `2Xl`(48)·`3Xl`(56).** `Sm`~`Lg`는 포인터 밀집 UI에 한정.
- **라벨 굵기는 `Normal`·`Strong`만.** 기본은 `Strong`.

### Don't
- **Primary를 한 화면에 둘 이상 두지 말 것.**
- **`Solid`에 Trailing 아이콘을 넣지 말 것.** (Outline에서만 허용)
- **`Subtlest`를 Solid에 쓰지 말 것.** (Outline Square 전용)
- **`Xl`을 `Outline / Ghost / Rounded` 외에 쓰지 말 것.**
- **Solid Rounded를 Secondary·Inverse 외 색에 쓰지 말 것.**
- **`Danger`를 강조 용도로 쓰지 말 것.** Danger는 위험 신호다.
- **disabled 버튼에만 중요한 정보를 담지 말 것.**

---

## 6. Known Gaps

- **`Pressed` state 없음.** 현재 Hover/Focus만 정의. press 피드백이 필요해지면 별도 정의 필요.
- **인라인 텍스트 링크는 Button이 다루지 않는다.** 별도 Text Link 요소.
- **두 줄/보조문(SubText) 미채택.** 니즈 생기면 별도 검토.

---

_Anchor Design System · 값 출처 `tokens.json` v0.5.1 + `DESIGN.md`(§3-0 해석표) · Figma 참고: [컴포넌트 세트](https://www.figma.com/design/lgfSHlSDZjwG3NdDlwhThp?node-id=3578-4283) · v0.1.1 · 기준일 2026-06-30_
