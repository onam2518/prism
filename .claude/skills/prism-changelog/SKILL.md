---
name: prism-changelog
description: 프리즘 변경사항을 과제 단위 표로 정리해 Confluence(Prism 기능 및 변경 사항)에 기재. 사용자가 "변경사항 정리해", "컨플루언스에 변경사항 정리", "7/14부터 변경 정리해줘"처럼 특정 기간 머지 PR 을 문서로 정리해 달라고 할 때 사용. 머지 PR 수집 → 과제 그룹핑 → 일시·AS-IS/TO-BE 표 작성 → 화면 캡처 표 → 페이지 갱신.
---

# prism-changelog · 변경사항 Confluence 정리

지정 기간에 main 에 머지된 PR 들을 **과제 단위**로 묶어, Confluence 페이지
**"Prism 기능 및 변경 사항"** (DNM 스페이스 · pageId `419037211` · cloudId `axzcorp.atlassian.net`)에
표로 기재한다. 첫 기록(2026.07.14~07.21 · PR #157~#258)은 이미 이 형식으로 작성돼 있다 — 형식이
궁금하면 페이지를 먼저 페치해 참고한다.

## 사전 조건

- **Atlassian MCP**(claude_ai_Atlassian_Rovo) 연결 필요: `getConfluencePage` · `updateConfluencePage`.
- **문서 작성 규칙 4건을 먼저 읽는다** (`~/.claude/projects/-Users-pete-axz-pc-Desktop/memory/`):
  `feedback_no_dash_symbol`(대시 기호 금지), `feedback_policy_first_tone`(정책 중심 톤),
  `feedback_text_only_edits`(수정 전 본문 페치 · 기존 구조 보존), `feedback_writing_persona_kim_younha`(구체·수식어 절제).
- 마커 파일 `~/.prism_changelog_last`: 마지막 정리의 끝 PR 번호와 날짜. 없으면 사용자에게 시작점을 묻는다.

## 작업 순서

### 1. 범위 결정

사용자 인자(#N부터 · 날짜부터)가 있으면 그것을, 없으면 `~/.prism_changelog_last` 다음부터 오늘까지.

```bash
gh pr list --state merged --limit 200 --json number,title,mergedAt
```

mergedAt 은 UTC 다. **일시 표기는 KST(+9h) 변환 후 `MM.DD` 형식**, 기간이면 `MM.DD ~ MM.DD`.

### 2. 과제 그룹핑

PR 을 낱개로 나열하지 않는다. **기능·정책 변화 중심으로 과제 10~15건**으로 묶는다:

- 정책 변화(판정 기준·권한·보존 등)와 신규 기능이 각각 행이 된다.
- 리팩토링·자잘한 버그픽스는 "구조 리팩토링과 기반 정비" 한 행으로 묶는다.
- docs 단독 PR 은 관련 과제의 비고로 흡수한다.
- 근거가 부족하면 커밋 메시지 본문(`git log`)과 관련 문서(HANDOFF.md 등)로 배경을 보강한다.

### 3. 페이지 갱신

1. `getConfluencePage`(html)로 **현재 본문을 반드시 페치**한다(사용자가 수시로 직접 편집).
2. 기존 본문은 그대로 보존하고, 리드 문단 아래에 새 기간 섹션을 **최신이 위**로 삽입:
   `<h2>YYYY.MM.DD ~ MM.DD</h2>` + 변경 표 + 주요 화면 표. (기존 첫 기록에 h2 가 없으면 그대로 두고 새 섹션만 추가.)
3. 변경 표 형식 · 열은 이 순서 고정:

   | 일시 | 과제명 | 배경 | 주요 변경사항 (AS-IS → TO-BE) | 비고 |

   - 행 정렬은 **착수 시점 순**(일시 오름차순).
   - **모든 셀은 불릿(`<ul><li>`) · 명사형 종결**("~유통됩니다" ✗ → "~유통 보류(fail-closed)" ○).
   - 주요 변경사항 셀은 `<p><strong>AS-IS</strong></p><ul>…</ul><p><strong>TO-BE</strong></p><ul>…</ul>`.
   - 비고에는 PR 번호, 후속 과제, 관련 기획서 링크(사용자 가이드 `395378726` · 관리자 가이드 `397737997` 등).
   - 대시 기호(—, 문장 연결 하이픈) 금지 · 가운뎃점(·)과 화살표(→)는 허용.
4. 표는 `data-layout="full-width"`.

### 4. 주요 화면 표 (UI 변경이 있을 때)

**구분 | 화면 | 내용** 3열 표. 구분=메뉴명, 화면=캡처 이미지, 내용=불릿 · 명사형.
캡처 절차는 아래 레시피 참조. UI 변경이 없는 기간이면 생략한다.

### 5. 마무리

- `~/.prism_changelog_last` 를 `끝PR번호<TAB>YYYY-MM-DD` 로 갱신.
- 사용자에게 페이지 링크와 과제 수, 캡처 수를 보고한다.

## 화면 캡처 레시피 (함정 포함)

운영 데이터를 쓰지 않는다. **격리 목 서버 + 시드**로 캡처하고 문서에 "격리된 시연 환경(모의 데이터)" 문구를 남긴다.

```bash
D=<scratchpad>/qa && mkdir -p $D
export PRISM_BACKEND=sqlite PRISM_DB=$D/qa.db PRISM_CONFIG=$D/qa_config.json PRISM_ENTDICT_ENRICH=0
python3 scripts/seed_qa.py                       # 콘텐츠 12건 시드(멱등)
python3 -m prism.serve --mock --port 8973 &      # 8765 회피
```

1. **검수자 등록 모달**이 첫 화면을 가린다 → headless 단독 캡처 불가. Chrome 을
   `--headless=new --remote-debugging-port=9333` 로 띄우고 **CDP(Node 내장 WebSocket)** 로
   `localStorage.setItem('prism_reviewer','복실'); localStorage.setItem('prism_reviewer_char','boksil')`
   심은 뒤 `/?m=<모듈>` 별로 `Page.captureScreenshot`. (검증된 스크립트 패턴: Emulation 1440×1100 @2x →
   navigate → 2.6s 대기 → 캡처. 모듈 id 는 URL `?m=` 파라미터.)
2. `sips -Z 1440 -s format jpeg -s formatOptions 82` 로 압축(장당 150~200KB).
3. **첨부 업로드는 Atlassian API 토큰이 이 머신에 없다** → 사용자의 로그인된 Chrome(MCP claude-in-chrome) 경유:
   - 로그인 상태 먼저 확인(로그인 페이지로 리다이렉트되면 사용자에게 로그인 요청 · 대행 금지).
   - 캡처 파일을 로컬 HTTP 서버로 서빙하되 **PNA 프리플라이트 대응 필수**: OPTIONS 204 +
     `Access-Control-Allow-Private-Network: true` + `Access-Control-Allow-Origin: *`
     (없으면 https 페이지에서 localhost fetch 가 무한 대기).
   - Confluence 탭에서 `javascript_tool` 로 fetch → FormData →
     `POST /wiki/rest/api/content/419037211/child/attachment` (헤더 `X-Atlassian-Token: nocheck`).
   - **JS 실행 45초 제한** → 한 번에 2~3장씩 나눠 업로드.
   - 응답의 `extensions.fileId` 를 받아 본문 media 노드로 삽입:
     `<div data-type="media-group"><div data-type="media" data-media-type="file" data-id="<fileId>" data-collection="contentId-419037211">이름.jpg</div></div>`
4. 종료 시 목 서버 · CORS 서버 · headless Chrome 프로세스를 반드시 정리한다.
