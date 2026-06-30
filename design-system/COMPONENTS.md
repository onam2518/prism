# Prism 컴포넌트 구조 사전

각 컴포넌트의 **해부(anatomy) · 부품(parts) · API · variant · 상태 · 구성 규칙**을 사전에 못박는 문서. Perplexity 기준.

- 비주얼 토큰 → `DESIGN.md`
- UX·플로우 → `PRINCIPLES.md`
- **구조 계약(이 문서)** → 컴포넌트가 무엇으로 이루어지는가

규칙: 부품 이름은 `ds-{component}__{part}`. 색·간격은 토큰만 참조. 새 컴포넌트보다 **기존 8 primitive의 variant** 우선.

---

## 0. 분류 (Primitive → Composite)

```
Primitive (13)
  Button(+pill·trailingIcon) · IconButton(+[data-tip]) · Input(+composer) · Select · Card
  · Badge(+meta칩) · Tabs · Table · Dialog · Toast · Toggle · Steps · AttachmentChip

Composite (4)
  Citation · Composer · SourceRail · AnswerBlock

Character (4)
  Character(다음프렌즈 4종) · Persona · PersonaCard(홀로 카드) · Processing

Widget (3)
  Widget(kind 기능/정보) · WidgetGrid · LauncherWidget

안내·대화 (2)
  Coachmark(온보딩) · Assistant(플로팅 도우미+작업 상태창)

Infographic (5)
  Stat/StatGrid · ProgressBar(+Distribution) · ProgressRing · Skeleton · Spinner

Shell & Nav (7)
  AppShell · Pane · PaneTitle · Sidebar · NavGroup · NavItem · StatusDot

Form (4)
  Segmented · KeyField · Accordion · FileDropzone

Result (6)
  DefinitionList · FieldRow · GradePill · EmptyState · CodeBlock · JsonViewer

Operations (3)
  InlineEditor · EditBar · ChipInput
```

> 총 50+ 컴포넌트 모두 `src/*.tsx` 로 구현 완료. `preview/index.html` 에서 확인.
> 추가 산정·우선순위·Perplexity 기능 매핑·스킬 적용 노트 → **`COMPONENT_ROADMAP.md`**.

원칙: **Composite는 새 CSS를 거의 만들지 않는다.** Primitive를 순서대로 배치한 레이아웃일 뿐.

---

## 1. Button

화면당 주 행동을 나르는 최소 단위. teal primary는 화면당 하나.

```
┌─────────────────────────────┐
│ [icon?]  Label  [icon?]      │   ds-btn  (root)
└─────────────────────────────┘
   └ __icon   └ __label
```

| 부품 | 필수 | 설명 |
|---|---|---|
| `root` (`.ds-btn`) | ● | 인터랙션 박스. variant 클래스가 색/높이 결정 |
| `__icon` | ○ | 16–18px, `currentColor` 상속 |
| `__label` | ● | 14px / 500 / FK Grotesk |

- **variant**: `primary`(teal 44px) · `secondary`(보더 44px) · `ghost`(36px 조용) · `pill`(34px 필터, `aria-pressed`로 active)
- **상태**: default · hover · active(pressed) · disabled(40%) · focus(teal ring)
- **API**: `variant`, `active`(pill 전용), 그 외 표준 `<button>` 속성
- **구성 규칙**: 아이콘은 라벨 좌측 기본. 아이콘 단독이면 `aria-label` 필수.
- **Don't**: 한 화면에 primary 2개 · 색만 다른 새 variant.

---

## 2. Input

사용자 입력의 시작점. `composer`는 화면의 주인공(시그니처 Ask 웰).

```
 Label                         ← __label (선택)
┌─────────────────────────────┐
│ placeholder text…           │  ds-field  (root)
└─────────────────────────────┘
 hint / error message          ← __hint (선택)
```

| 부품 | 필수 | 설명 |
|---|---|---|
| `__label` (`.ds-label`) | ○ | 14px / 500, 필드 위 6px |
| `root` (`.ds-field`) | ● | 웰. `--composer`면 radius 16·padding 16/18·16px |
| `__hint` (`.ds-hint`) | ○ | 13px muted, error면 `--error` |

- **variant**: `field`(표준 radius 10) · `composer`(radius 16, 큰 Ask 박스)
- **상태**: rest(subtle shadow) · focus(teal 보더 + ring) · invalid(error 보더/ring) · disabled
- **API**: `label`, `variant`, `invalid`, `hint` + 표준 `<input>`
- **구성 규칙**: composer는 하단에 **Action bar**(아이콘 + Submit) 결합 가능 → Composite 참조.
- **Don't**: 라벨 없이 placeholder만으로 의미 전달 · 에러를 색만으로(반드시 `__hint` 텍스트 동반).

---

## 3. Select

정해진 값(모델·콘텐츠 그룹) 선택. 구조는 Input과 동일, root만 `<select>`.

```
 Label
┌─────────────────────────────┐
│ 선택값                    ▾ │  ds-field (select)
└─────────────────────────────┘
```

- **API**: `label`, `options: string[]`, `invalid`
- **상태**: Input과 공유(focus/invalid/disabled)
- **구성 규칙**: 옵션 7개 초과·검색 필요 시 Select가 아니라 별도 Combobox(예정).
- **Don't**: 자유 입력에 Select 사용.

---

## 4. Card

콘텐츠 표면. variant가 역할을 정한다. 깊이는 그림자보다 보더·온도차로.

```
answer                          source
┌──────────────────────────┐   ┌────────────────────────┐
│ [eyebrow?]               │   │ [fav] domain           │
│ ─ 본문 콘텐츠 ─           │   │       title (2줄 clamp) │
│                          │   └────────────────────────┘
└──────────────────────────┘    hover → teal 보더 + lift
```

| variant | 배경 | 보더 | radius | 그림자 | 용도 |
|---|---|---|---|---|---|
| `answer` | surface | hairline-soft | 12 | 없음(플랫) | 답변/콘텐츠 블록 |
| `source` | white | hairline | 10 | subtle→hover standard | 인용 소스 카드 |
| `feed` | surface | hairline-soft | 12 | 없음 | Discover 스토리(이미지 top) |

| 부품(권장) | 설명 |
|---|---|
| `__eyebrow` | 카드 상단 라벨(예: "Answer") — caption muted |
| `__media` | feed 카드 상단 이미지(16:9, full-bleed) |
| `__body` | 본문 슬롯 |
| `__footer` | 액션/메타 |

- **API**: `variant`, children(자유 구성)
- **상태**: source만 hover(teal lift). answer/feed는 정적.
- **구성 규칙**: 답변 본문은 `.ds-answer`(16px/1.63/68ch). source는 가로 Rail 안에 배치.
- **Don't**: answer 카드에 그림자 · source 카드를 본문 위에 띄워 가리기.

---

## 5. Badge

상태·라벨·인용을 한눈에. teal 계열은 절제(액션/인용 신호).

```
┌──────────────┐
│ [dot?] Label │   ds-badge
└──────────────┘
```

| variant | 배경 / 글자 | 용도 |
|---|---|---|
| `pro` | deep / white | PRO·모델 라벨 |
| `status` | tint / teal | New·Beta·포커스 |
| `citation` | tint / deep, 1px6px | 인라인 인용 `[1]` |
| `neutral` | hairline-soft / body | 일반 라벨 |
| `success`·`error`·`warning` | 의미색 14% / 의미색 | 결과 상태 |

- **부품**: `__dot`(6px, `currentColor`) 선택
- **API**: `variant`, `dot`
- **구성 규칙**: 줄글로 충분하면 배지 금지. 인용은 본문 흐름 속 `citation`/`.ds-citation`(superscript).
- **Don't**: 비인터랙티브 장식에 teal 배지 · 한 영역 배지 남발.

---

## 6. Tabs

"장소" 전환(단계 아님). 상단=밑줄, 세로=하이라이트.

```
underline                         sidebar
 Home  Discover  Spaces  Library    ┌ Home      (active: tint)
 ──────                             │ Discover
 (active: ink + teal 2px 밑줄)      └ Library
```

| 부품 | 설명 |
|---|---|
| `root` (`.ds-tabs--{variant}`) | tablist |
| `__tab` (`.ds-tab`) | role=tab, active=`--active` |
| `__icon` / `__label` | tab 내부 |

- **variant**: `underline`(상단 가로) · `sidebar`(세로)
- **상태**: inactive(muted) · hover(ink) · active(밑줄 또는 tint) · focus(ring)
- **API**: `items: {id,label,icon?}[]`, `value`, `onChange`, `variant`
- **구성 규칙**: 멀티스텝(단계)에는 Tabs 금지 — 단계는 별도 Stepper(예정). Tabs는 동급 장소 전환만.
- **Don't**: 활성 표시를 색만으로(밑줄/배경 동반) · 탭 7개 초과.

---

## 7. Table

정형 데이터. 조용한 헤더 + 헤어라인 행, hover tint.

```
 Model        Context   Status     ← thead th (caption muted)
 ───────────────────────────────
 sonar-pro    128k      ● Ready    ← tbody td (ink) / hover: tint
 claude-opus  1M        Beta
```

| 부품 | 설명 |
|---|---|
| `thead th` | 좌측 정렬, 13px / 600 muted, 하단 hairline |
| `tbody td` | 14–15px ink, 행 구분 hairline-soft |
| 행 hover | primary-tint 배경 |

- **API**: `columns: {key,header,render?}[]`, `data: T[]`, `rowKey?`
- **구성 규칙**: 셀 커스텀은 `column.render`로(배지·링크 삽입). 행 선택·정렬은 향후 확장 슬롯.
- **Don't**: 얼룩(zebra) 배경 · 두꺼운 보더 그리드(에디토리얼 유지).

---

## 8. Dialog

흐름을 끊는 결정(공유·설정·업그레이드)만. 따뜻한 페이퍼 + 잉크 틴트 그림자.

```
   ▒▒▒▒▒ backdrop (scrim 40%) ▒▒▒▒▒
   ┌───────────────────────────┐
   │ Title                     │  __title (22/600)
   │ body 콘텐츠…              │  __body
   │            [취소] [확인]  │  __footer (우정렬)
   └───────────────────────────┘
```

| 부품 | 필수 | 설명 |
|---|---|---|
| `backdrop` (`.ds-dialog-backdrop`) | ● | scrim, 클릭 시 닫힘(옵션) |
| `root` (`.ds-dialog`) | ● | radius 16, padding 28, modal shadow |
| `__title` | ○ | heading 22 / 600 |
| `__body` | ● | 본문 슬롯 |
| `__footer` | ○ | 우정렬, ghost(취소) + primary(확인) |

- **API**: `open`, `onClose`, `title`, `footer`, `closeOnBackdrop`
- **상태**: 열림(fade+rise 애니메이션) · 닫힘(unmount) · reduced-motion(애니메이션 제거)
- **닫기 경로**: Esc · 백드롭 · 취소 버튼 (셋 다 제공)
- **구성 규칙**: footer는 ghost→primary 순(왼→오). 파괴적 확인은 primary 대신 error 톤 검토.
- **Don't**: 닫기 경로 없는 모달 · 모달 위에 모달 중첩.

---

## Composite (사전 정의 — 향후 빌드)

### C1. Composer (시그니처)
```
┌──────────────────────────────────────────┐
│ Ask anything…                            │  Input(composer)
│                                          │
│ [Web][Academic][Writing]   [📎][🎙][ Ask ]│  __toolbar
│  └ Pill row (좌)            └ Action bar(우)
└──────────────────────────────────────────┘
```
- 구성: `Input(composer)` + `__toolbar`(좌 Pill row, 우 아이콘 Button[] + Submit primary)
- 규칙: Submit은 유일한 teal primary. 포커스 시 전체 웰 teal ring.

### C2. Source Rail
```
[source][source][source][source] →   가로 스크롤, Card(source)[]
```
- 구성: 가로 스크롤 컨테이너 + `Card(source)` 반복. 답변 위(모바일) 또는 우측(데스크톱).
- 규칙: 본문을 가리지 않음. favicon 16px 고정.

### C3. Answer Block
```
Source Rail
Card(answer) { ds-answer + 인라인 Citation[] }
Related (chips/Pill)
```
- 구성: Source Rail → Answer Card → Related. 읽기 순서 = 배치 순서.
- 규칙: 68ch 측정 폭, 인용은 타이포 흐름 속 superscript.

### C4. Toast (예정 primitive)
- 구조: `root`(다크 `#091717`) + `__icon?` + `__label`. 하단 중앙, 3초 자동 dismiss.

### C5. Toggle (예정 primitive)
- 구조: `track`(on=teal/off=`#d6d6cc`) + `thumb`(white 18px). 설정 스위치.

---

## 부품 네이밍 규약 (요약)

| 패턴 | 의미 | 예 |
|---|---|---|
| `ds-{c}` | 컴포넌트 root | `ds-card` |
| `ds-{c}--{variant}` | variant | `ds-card--source` |
| `ds-{c}__{part}` | 부품 | `ds-dialog__title` |
| `aria-*` | 상태 | `aria-pressed`, `aria-selected` |

> 새 컴포넌트를 정의하려면: 이 문서에 **anatomy + parts + API + variant + 상태 + Don't** 6칸을 먼저 채운 뒤 코드를 쓴다. 구조 정의 없이 컴포넌트 추가 금지.
