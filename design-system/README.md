# Prism Design System

Prism UI의 디자인 토큰과 React 컴포넌트. Upstage 정렬 다크 테마(바이올렛 단일 인터랙션 · Solar 단일 액센트 · 이진 radius · 그림자 없음 · Geist)를 코드로 고정한 **시작점(seed)**.

`/design-sync`가 읽도록 만든 패키지입니다.

## 구성

```
design-system/
  package.json            # designSystem.{tokens,theme,components} 명시
  tokens/tokens.json      # 디자인 토큰 (W3C 포맷)
  src/
    theme.css             # 토큰 → CSS 변수
    components.css         # 컴포넌트 스타일(토큰만 참조)
    tokens.ts             # 타입드 토큰
    Button / Input / Select / Card / Badge / Tabs (.tsx)
    index.ts
  tailwind.preset.cjs     # Tailwind 프로젝트용 프리셋
```

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
| Primary | `violet #5b52ff` (hover `#4a42e0`) — 유일한 인터랙션 색 |
| Accent | `solar #d2ff95` — 상단 배너 한 곳만 |
| Surface | canvas `#0b0a0f` · surface `#141318` · surface-2 `#1a1922` |
| Text | heading `#fff` · body `#9aa0aa` · muted `#6e7191` |
| Border | `rgba(255,255,255,0.08)` 헤어라인 |
| Font | Geist / Geist Mono |
| Radius | 이진: `0` 레이아웃 · `8px` 컨트롤 |
| Shadow | 없음 (surface 대비 + 헤어라인으로 깊이) |

## Prism UI(serve.py)와의 관계

현재 Prism UI는 `serve.py` 안의 인라인 Tailwind입니다. 이 패키지는 그 토큰을 추출한 것으로, `/design-sync` 후 컴포넌트 체계가 정리되면 `serve.py`의 인라인 마크업을 이 컴포넌트/토큰 기준으로 맞춰갈 수 있습니다.

> 컴포넌트 비주얼은 토큰을 그대로 반영한 최소 구현입니다. 디자인을 더 손보면 `tokens.json` + `components.css`만 고치면 전체에 반영됩니다.
