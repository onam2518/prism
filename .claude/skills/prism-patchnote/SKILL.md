---
name: prism-patchnote
description: 프리즘 패치노트를 생성해 슬랙 채널로 발송. 사용자가 "패치노트 보내줘", "슬랙에 패치노트", "#60부터 패치노트 만들어줘"처럼 특정 시점 이후 머지된 PR 을 모아 패치노트를 만들어 달라고 할 때 사용. 머지 PR 수집 → 데일리로그로 맥락 보강 → Block Kit 작성 → 미리보기 확인 → Incoming Webhook 발송.
---

# prism-patchnote · 프리즘 패치노트 슬랙 발송

지정한 시점 이후 main 에 머지된 PR 들을 모아 한국어 패치노트를 만들고,
Slack Incoming Webhook 으로 채널에 발송한다. **발송 전 사용자 미리보기 확인이 필수다**
(외부 채널 발송이므로 · 사용자가 "확인 없이 바로 보내"라고 명시한 경우만 생략).

## 사전 조건

`~/.prism_slack_webhook` 파일에 웹훅 URL 이 있어야 한다. 없으면 작업을 멈추고 아래 발급 안내를 전한다:

1. https://api.slack.com/apps → **Create New App** → From scratch → 이름 예: `Prism 패치노트`
2. **Incoming Webhooks** → Activate → **Add New Webhook to Workspace** → 발송할 채널 선택
3. 발급된 URL 저장:
   `echo 'https://hooks.slack.com/services/…' > ~/.prism_slack_webhook && chmod 600 ~/.prism_slack_webhook`

웹훅 URL 은 비밀키다. 레포·커밋·로그에 절대 넣지 않는다.

## 작업 순서

### 1. 범위 결정

사용자가 준 시점 인자를 해석한다:

- **PR 번호**: "#60부터", "PR 60 이후" → 번호 ≥ 60 인 머지 PR 전부
- **날짜**: "7/7 이후", "2026-07-07부터" → mergedAt ≥ 해당일 00:00 (KST)
- **인자 없음**: `~/.prism_patchnote_last` (형식: `ISO시각<TAB>범위문자열`)가 있으면 그 이후,
  없으면 사용자에게 시점을 물어본다.

### 2. 머지 PR 수집

```bash
gh pr list --state merged --base main --limit 200 \
  --json number,title,url,mergedAt,author --jq 'sort_by(.number)'
```

범위로 필터한 뒤, 제목만으로 내용이 불명확한 PR 은 `gh pr view <번호> --json body` 로 본문을 본다.

### 3. 맥락 보강

해당 기간의 `prism/daily-log/*.md` 를 읽고 각 PR 의 배경·사용자 체감 효과를 파악한다.
데일리로그가 커밋 제목보다 훨씬 정확한 소스이므로, 로그가 있으면 로그 서술을 우선한다.

### 4. 패치노트 작성 원칙 (2026-07-09 사용자 확정)

- **독자는 비개발 검수자·관리자.** 화면에서 보이는 단어만 쓴다. 개발 용어(UUID·머지·커밋·API·
  배포·데이터 계층 등)는 쓰지 않거나 풀어쓴다.
  예: "리더보드에 닉네임 대신 알 수 없는 영문·숫자 코드가 보이던 문제" (O) / "UUID 노출 수정" (X)
- **말투는 해요체.**
- **형식은 불릿 + 하위 불릿.** 상단 불릿(`•`)에 핵심 한 문장, 상세·조건·예시는 하위 불릿
  (`    ◦` · 공백 4칸 들여쓰기)으로 내린다. **문장이 끝나면(마침표) 줄을 바꾼다.**
  **em-dash(—) 는 쓰지 않는다.**
- **PR 번호·링크는 본문에 쓰지 않는다** (독자에게 무의미). 범위 추적은 발송 시 `--mark` 기록으로만.
- 분류 4종 고정 · 중요도순: `✨ 새 기능` · `🐛 버그 수정` · `⚡ 성능·안정성` · `📝 문서·운영`
  (없는 분류는 생략). 검수 업무에 영향 큰 것부터.
- 한 PR 을 여러 항목으로 쪼개거나 사소한 PR 들을 한 항목으로 묶는 것은 자유. 오타·내부 정리
  수준은 생략. 항목 수보다 읽는 사람이 얻는 정보가 기준.
- **머지된 PR 만 포함.** 오픈 PR 은 다음 회차로.
- 머리는 header(🔭 Prism 패치노트 · 날짜)와 한 줄 요약 section.
  꼬리는 **'프리즘 바로가기' 버튼**(actions 블록 · `url: https://prism-item.fly.dev`) 뒤에
  날짜만 있는 context 라인.

### 5. payload 작성

스크래치패드에 `patchnote-payload.json` 을 만든다. Slack 제약: header 150자,
section 3,000자(넘으면 블록 분할), blocks 최대 50개, 최상위 `text`(알림 폴백) 필수.

```json
{
  "text": "Prism 패치노트 · 7/9",
  "blocks": [
    {"type": "header", "text": {"type": "plain_text", "text": "🔭 Prism 패치노트 · 7/9"}},
    {"type": "section", "text": {"type": "mrkdwn", "text": "한 줄 요약이에요."}},
    {"type": "section", "text": {"type": "mrkdwn", "text": "*✨ 새 기능*\n• 닉네임을 직접 바꿀 수 있어요.\n    ◦ 홈의 '내 검수 캐릭터'에서 변경해요.\n    ◦ 팀·캐릭터·검수 기록은 그대로 유지돼요."}},
    {"type": "divider"},
    {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "프리즘 바로가기"}, "url": "https://prism-item.fly.dev", "style": "primary"}]},
    {"type": "context", "elements": [{"type": "mrkdwn", "text": "2026-07-09 · Prism 업데이트 소식"}]}
  ]
}
```

mrkdwn 은 슬랙 문법이다: 굵게 `*텍스트*`(별 1개), 링크 `<url|라벨>`. GitHub 마크다운(`**`, `[]()`)을 쓰면 깨진다.

### 6. 검증 → 미리보기 → 발송

```bash
python3 .claude/skills/prism-patchnote/send_slack.py --dry-run patchnote-payload.json
```

통과하면 패치노트 전문을 사용자에게 보여주고 발송 확인을 받는다. 확인 후:

```bash
python3 .claude/skills/prism-patchnote/send_slack.py --mark "PR #60~#68" patchnote-payload.json
```

`--mark` 는 성공 시 `~/.prism_patchnote_last` 에 발송 범위를 기록해, 다음번 인자 없는
호출의 기본 시작점이 된다.

### 7. 결과 보고

성공/실패와 발송 범위를 보고한다. 실패 시 스크립트의 오류 메시지(HTTP 상태·응답 본문)를 그대로 전한다.
