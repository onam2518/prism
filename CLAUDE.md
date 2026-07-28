# Prism · Claude 세션 공통 규칙

이 저장소는 **여러 Claude 세션과 사람이 같은 작업 트리에서 동시에 작업하는 일이 잦다.**
모든 세션은 아래 규칙을 따른다. (배경: 세션 간 간섭으로 남의 미커밋 작업이 커밋·배포·리셋에
휩쓸리는 사고 방지)

## 동시작업(멀티 세션) 원칙

1. **시작 전 확인**: 작업 시작 시 `git status`와 현재 브랜치를 먼저 확인한다.
   내 것이 아닌 미커밋 변경(다른 세션의 작업)이 보이면 그 파일은 **절대 수정·스테이징·
   스태시·리셋·되돌리기 하지 않는다.** 같은 파일을 건드려야 하면 중단하고 사용자에게 조정을 요청한다.
2. **main 직접 커밋·push 금지**: 기능 단위 브랜치(`feat/…`, `fix/…`, `docs/…`) → PR → 머지.
   (GitHub 브랜치 보호로 강제됨 · 소유자 예외 있음)
3. **커밋 범위 최소화**: `git add -A`, `git add .` 금지. 내가 수정한 파일만 경로를 명시해
   스테이징한다. 커밋 전 `git status`로 남의 변경이 섞이지 않았는지 재확인.
4. **브랜치 이동 최소화**: 메인 체크아웃(HEAD)은 다른 세션들이 공유하는 상태다.
   미커밋 변경이 있는 상태의 checkout/rebase/reset 은 원칙적으로 금지 — 필요하면
   `git worktree add`(별도 워크트리)로 대신한다. HEAD 를 옮겼다면 작업 종료 전 `main` 으로 복귀시킨다.
5. **배포는 클린 스냅샷에서만**: 작업 트리가 더러운 상태에서 `fly deploy` 금지.
   커밋된 ref 를 `git worktree add --detach <경로> <커밋>` 으로 받아 그 안에서 배포한다.
6. **로컬 실행 격리**: 서버 구동 시 운영 기본 포트(8765)를 피하고,
   `PRISM_DB=$(mktemp -d)/t.db` 로 실 DB 를 격리한다. 세션 종료 전 띄운 프로세스를 정리한다.
7. **허브 파일 주의**: `prism/serve.py`(HTTP 디스패치)는 거의 모든 기능이 지나가는
   파일이라 세션 간 충돌이 가장 잦다. (UI 는 2026-07-17 분할: 마크업 `prism/ui/NN-*.html`
   조각 · 앱 JS `vendor/app-NN-*.js` 조각 — 겹치지 않는 화면이면 동시 수정 안전)
   이 파일들의 다른 세션 미커밋 변경을 발견하면 겹치는 편집을 피하고 사용자에게 알린다.

## 빌드·테스트

- **의존성 0 원칙**: Python 3.8+ 표준 라이브러리만 사용. 새 pip 의존성 추가 금지.
- 테스트: `python3 -m unittest discover tests` (pytest 가 있으면 `python3 -m pytest tests/ -q`)
- 커밋 전 전체 테스트 통과를 확인한다. 테스트는 임시 디렉토리 DB 를 쓰므로 병렬 세션과 안전.
- 로컬 서버: `PRISM_DB=$(mktemp -d)/t.db python3 -m prism.serve --mock --port <임시포트>`
- 커밋 메시지: 한국어 conventional(`feat(scope): 요약 · 상세는 · 구분`) +
  `Co-Authored-By` 트레일러 유지.

## 구조 힌트

- **작업 시작 전 `ARCHITECTURE.md` 를 먼저 볼 것**: serve.py 도메인 클러스터 지도 ·
  라우트 추가 방법 · 리팩토링 로드맵. 기능 위치를 찾느라 큰 파일을 통독하지 않는다.
- HTTP 디스패치 `serve.py` · 학습·골든·소요서·핸드오프 번들 `learnops.py` ·
  UI 마크업 `prism/ui/NN-*.html`(합성: `page.py`) · 앱 JS `vendor/app-NN-*.js`(로더:
  `vendor/app.js`) · 저장 계층 `store.py`(SQLite) / `supastore.py`(팀)
- 모델 선택은 `<x-modelpick …>` 한 줄로 쓴다(`page.py` 합성 중 드롭다운 마크업으로 펼침 ·
  데이터는 `vendor/app-13-modelpick.js` · 이름·비용 등급 원천은 `prism/modelmeta.py`).
  네이티브 `<select>` 로 모델 목록을 새로 만들지 말 것.
- 새 GET 라우트는 `serve.py` 의 `@_get_route` 테이블에 등록(최장 접두 우선 · 순서 무관).
  POST 도 동일하게 `@_post_route` 테이블(게이트 옵션 admin·super·login·team 지원).
- 새 도메인 기능은 serve.py 에 쌓지 말고 별도 모듈로 시작(`usermeta.py`·`entdict.py` 패턴).
- 문서: 개발 인수인계 `HANDOFF.md` · 학습 설계 `LEARNING_DESIGN.md` · 테스트 구조 `TESTING.md` ·
  배포 `DOCKER.md`, `fly.toml`(앱 `prism-item` · nrt) · 아티팩트 URL 색인 `docs/ARTIFACTS.md`

## 아티팩트 산출물 기록

- 프리즘 관련 **아티팩트를 새로 배포하면** `docs/ARTIFACTS.md` 표에 최신순(맨 위)으로 한 줄 추가한다:
  `| 최종 수정일 | [제목](URL) |`. 프리즘과 무관한 자료(티큐·ordent 등)는 기재하지 않는다.
- 같은 URL 을 재배포(갱신)한 경우 새 행을 만들지 말고 기존 행의 날짜만 갱신한다.
- 이 색인 갱신도 다른 변경과 동일하게 브랜치 → PR → 머지로 반영한다(main 직접 커밋 금지).
  아티팩트 배포와 같은 작업 흐름 안에서 함께 처리한다.

<!-- ASTRYX:START -->
Astryx v0.1.8 · 153 components
CLI: run every command as `npx astryx <cmd>` (shown below as `astryx ...`).

SETUP (once, in your app entry e.g. main.tsx) — without these, components render unstyled:
  import "@astryxdesign/core/reset.css";
  import "@astryxdesign/core/astryx.css";

WORKFLOW — discover, don't guess. Before writing UI:
1. `astryx build "<idea>"` — START HERE: returns a kit (closest [page] + [block]s + [component]s). No args = full playbook.
2. `astryx template <name> [--skeleton]` — scaffold the [page]/[block]s it named, or study their layout. Templates are reference code.
3. `astryx component <Name>` — props + examples for every component you use.

RULES:
- No <div> — components do all layout/spacing. Full page → AppShell; sidebar nav → SideNav.
- Frame first: pick the shell (AppShell / Layout+LayoutPanel) and budget regions in px BEFORE writing content (`astryx docs layout`).
- Dense data = rows (Table, List/Item) edge-to-edge — never Card-wrapped list items. Card = dashboard widgets, galleries, settings groups only.
- Status → StatusDot/Token; Badge only for counts and enumerated states, never decoration.
- Custom styling: component props first; else style/className with tokens — var(--color-*|--spacing-*|--radius-*). No raw hex/px. (No StyleX/Tailwind compiler here — don't use xstyle/utility classes.)
- Tokens for every value (`astryx docs tokens`). Brand/accent via `astryx theme` — never override --color-* in :root.
- SELF-CHECK before you finish: re-read the file and replace any raw <div>/<span> layout, imported .css/@apply, or hardcoded value (#hex, 16px) with the component or a token (var(--color-*|--spacing-*|…)). If unsure a component/prop exists, run `astryx component <Name>` / `astryx search "<thing>"`; don't hand-roll CSS.

MORE CLI:
  search "<query>"   find any component / hook / doc / template / block
  component --list   153 components by category
  template --list    page + block recipes
  docs <topic>       color, elevation, icons, illustrations, internationalization, layout, migration, motion, principles, shape, spacing, styling, theme, tokens, typography
  swizzle <Name>     eject component source for deep customization
  upgrade --apply    run after any @astryxdesign/core bump
<!-- ASTRYX:END -->
