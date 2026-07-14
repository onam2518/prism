# 퀘스트 독려 봇 (`prism.questbot`)

검수 목표(퀘스트)가 **진행 중일 때**, 진척도가 **팀 평균 미만**인 검수자에게
슬랙 **DM** 으로 독려 메시지를 보낸다. 의존성 0(stdlib) · 허브 파일 무수정.

- 진척 기준: `serve.py` 홈 히어로의 **팀 진척율과 동일**(그 콘텐츠의 현재 초안 생성
  이후 검수만 유효 = '퀘스트 창'). 로스터엔 검수 0건 팀원도 포함되어 평균·대상에 반영된다.
- 대상: `진척 < 팀 평균 × ratio`(기본 `ratio=1.0` = 평균 미만). 평균이 0(아무도 시작 안 함)이면 대상 없음.
- **1인 1회**: 같은 퀘스트(마감 일시 기준)에는 한 번만 보낸다(`questbot_sent` 리포트로 영속). `--force` 로 재발송.

## 식별자 매핑 (이메일 자동조회)

```
프리즘 검수자(auth uuid) → 이메일(Supabase Auth Admin API)
                        → 슬랙 user id(Slack users.lookupByEmail) → DM
```

- `reviewers` 테이블엔 이메일이 없어, **Supabase Auth Admin API** (`/auth/v1/admin/users`)를
  서비스 키로 조회해 `uuid→email` 을 얻는다(운영 env 재사용).
- 자동조회 실패 시 폴백: `~/.prism_slack_members` (JSON, `{"<uuid|이름|이메일>": "U0…"}`).
  이메일 원천이 없는 로컬 sqlite 에선 이 파일이 유일 경로다.

## 사전 준비

### 1) 슬랙 봇 토큰 (DM 발송)

Incoming Webhook(패치노트용)은 채널 전용이라 DM 을 못 보낸다. **Bot Token** 이 필요하다.

1. https://api.slack.com/apps → **Create New App** → From scratch (예: `Prism 퀘스트 봇`)
2. **OAuth & Permissions** → Bot Token Scopes 에 `chat:write`, `users:read.email` 추가
3. **Install to Workspace** → 발급된 `xoxb-…` 저장:
   ```bash
   echo 'xoxb-…' > ~/.prism_slack_bot_token && chmod 600 ~/.prism_slack_bot_token
   # 또는 env: export SLACK_BOT_TOKEN=xoxb-…
   ```
   토큰은 비밀키다. 레포·커밋·로그에 절대 넣지 않는다.

### 2) 스토어 / 이메일 원천

운영과 동일하게 `PRISM_BACKEND=supabase` · `SUPABASE_URL` · `SUPABASE_SERVICE_KEY` 를 준다
(이메일 자동조회가 Supabase Auth 를 쓴다).

## 실행

```bash
# 미리보기: 발송 없이 대상·메시지만 출력(먼저 이걸로 확인)
python3 -m prism.questbot --team <team_id> --dry-run

# 실제 발송
python3 -m prism.questbot --team <team_id>

# 옵션
#   --threshold-ratio 0.5   팀 평균의 50% 미만만 대상(더 뒤처진 사람만)
#   --force                 같은 퀘스트 재발송(1인 1회 무시)
#   --learn-next-at 2026-07-20T18:00   마감 일시 override(기본은 Config 에서 로드)
```

`--team` 미지정 시 `PRISM_TEAM` env 를 쓰고, 그것도 없으면 전역 스코프.

## 크론 (정기 독려)

Fly 머신 내부 crontab 예 — 매일 오전 10시(KST) 넛지:

```cron
# UTC 01:00 = KST 10:00. 스토어·슬랙 시크릿은 머신 env 로 주입
0 1 * * *  cd /app && python3 -m prism.questbot --team <team_id> >> /var/log/questbot.log 2>&1
```

퀘스트가 비활성이면 봇은 아무것도 보내지 않고 조용히 종료하므로, 매일 걸어 두어도
퀘스트 기간에만 동작한다. 1인 1회 가드가 있어 하루 여러 번 돌아도 중복 DM 은 없다.

## 동작 원리 (요약)

`prism/questbot.py`:
- `compute_progress(store, team, learn_next_at)` → `per_done{uid:건수}` · `avg` · `members`.
- `select_laggards(progress, ratio)` → 평균×ratio 미만 검수자(뒤처진 순).
- `run(...)` → 대상 선별 → uuid→email→슬랙 id 해석 → `compose()` 메시지 → `chat.postMessage` DM,
  성공분을 `questbot_sent` 리포트에 기록(재발송 방지).

테스트: `tests/test_questbot.py` (슬랙·Supabase 는 전부 대체 · 오프라인).
```bash
python3 -m unittest tests.test_questbot
```
