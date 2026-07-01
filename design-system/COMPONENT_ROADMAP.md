# Prism 컴포넌트 로드맵 · 산정

세 가지 입력을 합쳐 필요한 컴포넌트를 산정한 문서.
1. **Prism 실제 코드 감사** (serve.py · dashboard.html · 이미지→메타 파이프라인)
2. **Perplexity 제품 기능** 매핑 (Spaces · Pages · Pro Search · Discover · Focus modes · File upload)
3. **High-end visual design 스킬** (Editorial Luxury 기법, Anchor 정합 범위만)

범례: ✅ 구현 완료 · 🔜 다음 · 💤 백로그

---

## 0. 브랜드 결정 (확정 ✅)

> **Source of truth = Anchor Design System(axz).** 무채색 캔버스 + Blue(Primary)·Red(Accent) + 도메인 카테고리색 · Pretendard · Light/Dark 자동 swap.

- 이력: Upstage 다크+violet → Perplexity teal 라이트(v0.2) → **Anchor 전면 채택(v0.3, 2026-06-30)**.
- 결정: 토큰 원본을 **Anchor**(`anchor/DESIGN.md`·`anchor/Button.md`·`anchor/tokens.json`)로 고정. teal(`#20808d`)·FK Grotesk 폐기.
- 함의:
  - `theme.css`·`tokens.ts`·`tailwind.preset.cjs`·`tokens/tokens.json`은 Anchor semantic 토큰을 `--ds-*` 변수로 박제 · **변수 API는 유지, 값만 swap**(기존 50+ 컴포넌트 무수정 호환).
  - serve.py 적용: teal `#20808d→#1e84ff`(Blue.500), 잉크 그림자 `rgba(9,23,23,…)→rgba(0,0,0,…)`, 따뜻한 뉴트럴 → Anchor 무채색. 앱 별칭(`--ds-violet*→--ds-primary*`)으로 폴백 제거.
  - 다크는 별도 브랜드 아님 · 같은 `--ds-*` 이름이 `.ds-dark`/`[data-theme=dark]`에서 자동 swap.
  - 원칙: **Semantic 토큰만 · Primitive 직접 참조 금지 · Light/Dark 분기 금지.**

---

## 1. 구현 완료 (40개)

### Primitive (12)
✅ Button(+pill·trailingIcon) · Input(+composer) · Select · Card(answer/source/feed) · Badge(+meta칩 entity/intent/category/reason) · Tabs(underline/sidebar) · Table(+rowActions) · Dialog · Toast · Toggle · **Steps** · **AttachmentChip**

### Composite (4)
✅ Citation · Composer · SourceRail · AnswerBlock

### Character (3)
✅ Character(다음프렌즈 4종) · Persona · Processing

### Infographic (5)
✅ Stat/StatGrid · ProgressBar(+Distribution) · ProgressRing(도넛) · Skeleton · Spinner

### Shell & Nav (7)
✅ AppShell · Pane · PaneTitle · Sidebar · NavGroup · NavItem · StatusDot

### Form (4)
✅ Segmented · KeyField(eye+상태) · Accordion · FileDropzone

### Result (6)
✅ DefinitionList · FieldRow · GradePill(G/R) · EmptyState · CodeBlock · JsonViewer

### Operations (3)
✅ InlineEditor · EditBar · ChipInput

---

## 2. Prism 코드 감사 → 도메인 컴포넌트 (이미지→메타 파이프라인)

serve.py(2156줄, 인라인 Tailwind+Alpine) · dashboard.html(Canvas) 기준.

| 컴포넌트 | 용도 | 상태 | 비고 |
|---|---|---|---|
| **Steps** | 배치 처리 단계(이미지→OCR→비전→메타→판정) | ✅ | Perplexity Pro Search 5-step 패턴 |
| **MetaChip** (Badge variant) | 엔티티·인텐트·카테고리·차단사유 | ✅ | Badge `entity/intent/category/reason` |
| **Stat/StatGrid** | 배치 집계(총건수·G수·엔티티·평균길이) | ✅ | |
| **ProgressBar/Distribution** | 인텐트·엔티티 분포(상위 N) | ✅ | |
| **ProgressRing** | 품질 분포(G 유통비율 %) | ✅ | conic-gradient |
| **Skeleton / Spinner** | 로딩 자리·버튼 로딩 | ✅ | |
| **Processing** | 단건/배치 대기 화면 | ✅ | 캐릭터 + Steps 조합 가능 |
| **AttachmentChip** | 업로드 파일/이미지 칩 | ✅ | |
| **FileDropzone** | 이미지/엑셀 드래그·드롭·붙여넣기 입력 | ✅ | drag 상태, 범용 |
| **DefinitionList/FieldRow** | 단건 결과 상세(리드문·엔티티·인텐트·카테고리) | ✅ | MetaChip 조합, MetadataPanel 대체 |
| **CodeBlock/JsonViewer** | 원본 JSON 보기 + 복사 | ✅ | CopyButton·ExpandableCode 흡수 |
| **KeyField** | API 키 입력(비밀 + 눈 토글 + 상태 dot) | ✅ | KeyInputGroup 대체 |
| **StatusDot** | 모델 연결 상태(연결/미연결/MOCK) | ✅ | ConnectionStatus 대체 |
| **EmptyState** | 빈 상태(캐릭터 + 제안 칩) | ✅ | HeroEmpty 대체 |
| **GradePill** | G/R 품질 판정 | ✅ | |
| **Accordion** | 설정·상세 접힘/펼침 | ✅ | |
| **ThumbnailGallery** | 선택 이미지 5열 그리드 + 제거 | 🔜 | FileDropzone + AttachmentChip 조합으로 합성 가능 |
| **ImageSignalPanel** | 이미지별 OCR/Vision 신호 | 💤 | |
| **GraphCanvas** | 관계도(force-directed, Canvas) | 💤 | 라이브러리 0 |

---

## 3. Perplexity 기능 → 컴포넌트 매핑

Perplexity 제품 기능(Spaces·Pages·Pro Search·Discover·Focus·File·Model)을 Prism 맥락으로 변환.

| Perplexity 기능 | UI 표면 | Prism 매핑 | 상태 |
|---|---|---|---|
| **Pro Search (5-step)** | 검색 단계 표시 | **Steps** (메타 파이프라인) | ✅ |
| **Focus Modes** | Web/Academic 토글 | Button `pill` row | ✅ |
| **Citations** | 인라인 [n] + 소스 | Citation · SourceRail | ✅ |
| **Answer** | 답변 블록 | AnswerBlock | ✅ |
| **File Upload** | PDF/CSV/이미지 첨부 | AttachmentChip · ImageDropzone🔜 | ◑ |
| **Model Selection** | 모델 선택기 | Select → **ModelMenu**(설명 포함) | 🔜 |
| **Discover** | 트렌드 피드 | Card `feed` → **FeedGrid** | 🔜 |
| **Spaces** | 프로젝트 환경(파일·지시·공유) | **SpaceCard** + 파일 리스트 | 🔜 |
| **Pages** | 발행용 문서 | **PageLayout**(커버+섹션) | 💤 |
| **Threads / Library** | 대화 목록 | **ThreadList**(행+메타) | 🔜 |
| **Answer actions** | Copy·Rewrite·Share·Export | **AnswerActions**(ghost 툴바) | 🔜 |
| **Citation hover** | 소스 미리보기 팝오버 | **CitationPopover** | 💤 |
| **Tasks(scheduled)** | 예약 검색 | (Prism 배치 스케줄과 연계) | 💤 |

---

## 4. High-end 스킬 적용 노트 (의도적 취사선택)

스킬 기본값(Ethereal Glass: OLED 블랙·네온 오브·강한 블러·극적 회전)은 Anchor "무채색 캔버스 · 의미 있는 곳에만 액센트 · calm over spectacle"과 **충돌**. → 스킬의 **Editorial Luxury** 아키타입과 품질 기법만 채택.

**채택 ✅**
- 필름 그레인 텍스처(`.ds-grain`, opacity 0.035) · 페이퍼 질감
- Double-Bezel 중첩 카드(`.ds-bezel` + `__core`, 동심원 radius) · composer/hero
- Button-in-Button 트레일링 아이콘(`trailingIcon` + 자석 hover)
- Eyebrow 태그(`.ds-eyebrow`) · 매크로 여백 · 2xl squircle(`--ds-radius-2xl`)
- 커스텀 cubic-bezier(이미 토큰) · 스크롤 진입 리빌(`.ds-reveal`, transform/opacity/blur, IntersectionObserver)
- 표면 내부 하이라이트(`--ds-highlight`)

**거부 ❌ (Anchor 원칙 위반)**
- OLED 블랙 배경 / 네온 글로우 오브 → 무채색 캔버스 유지
- 전면 글래스 블러 → 떠있는 레이어(팝오버/모달)에만
- 카드 회전(-2deg) / 과장 모션 → 절제된 진입만
- 배너 폰트는 Pretendard 폴백 체인에 잔류(에셋 없을 때만)

> 원칙 충돌 시 우선순위: **PRINCIPLES.md(단계 명확성·절제) > 스킬(스펙터클)**. 스킬은 "품질의 도구"로 쓰되 브랜드 톤을 바꾸지 않는다.

---

## 5. 다음 작업 추천 순서

1. **ImageDropzone + ThumbnailGallery** · 파이프라인 입구(가장 큰 UX 임팩트)
2. **MetadataPanel + CopyButton** · 단건 결과 핵심 산출물
3. **KeyInputGroup + ConnectionStatus** · 설정/연결 상태
4. **HeroEmpty** · 빈 상태(캐릭터 활용)
5. serve.py 실제 연결 · 토큰/컴포넌트 확정 후 인라인 마크업 교체
