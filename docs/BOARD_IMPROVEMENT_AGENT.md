# 게시판 기반 기능개선 자동화 에이전트 — 설계·핸드오프 (2026-07-15)

> 상태: **설계안(미적용)**. 크론 루틴은 아직 생성하지 않았다. 다른 세션에서 이 문서를 받아
> 이어서 세팅한다. 조사·검증은 완료(게시판 스키마·Supabase 접근·루틴 스펙·프롬프트).

## 목적
프리즘 **게시판(제안·오류)** 에 쌓이는 사용자 피드백을 주기적으로 읽어,
**기능개선 소요 확인 → 개선 과제 도출 → 구현 → PR** 까지 잇는 **크론 스케줄드 클라우드 에이전트**.
사람은 올라온 PR 만 검토·머지하면 된다(에이전트는 머지하지 않음).

## 데이터 소스 (검증 완료)
- Supabase 프로젝트: **`yujinhcdbllcnnfvcmfp`** (Prism 운영 DB · docker-compose 기본 URL 과 일치)
- 테이블: **`public.prism_board`**
  - 컬럼: `id, team_key, kind, title, body, author_id, status, created_at`
  - `kind` ∈ {`feature`, `bug`} · `status` ∈ {`open`, `doing`, `done`}
  - 등록=팀원 · 상태변경=관리자(`board_action`, serve.py) · 코드 접근: `supastore.board_list/board_get/board_set_status`
- **접근 검증**: Supabase MCP(`execute_sql`)로 `prism_board` 조회·PATCH 가능함을 이 세션에서 확인.
  → 루틴은 Supabase MCP 를 데이터 소스로 쓴다(레포 스크립트/서비스키 프로비저닝 불필요).

## 루틴 설계 (RemoteTrigger / claude.ai routines)
| 항목 | 값 |
|---|---|
| 스케줄 | 주 1회 · 매주 월요일 오전 → **cron `0 0 * * 1`** (월 00:00 UTC = **월 09:00 KST**) |
| 환경 | Default `env_017NEzuQVtQCwV1Ff57LbsCj` (anthropic_cloud) |
| 모델 | `claude-sonnet-5` (조정 가능) |
| repo | `https://github.com/onam2518/prism` |
| allowed_tools | `Bash, Read, Write, Edit, Glob, Grep` |
| MCP | Supabase 커넥터 `connector_uuid=8dcb0be9-1126-442f-a4a1-5d785f41a284` · name `Supabase` · url `https://mcp.supabase.com/mcp` |

> 주의(핸드오프): 클라우드 루틴에 `gh`(PR 생성)·git push 권한과 Supabase MCP 가 실제로 붙는지
> 첫 수동 실행(`RemoteTrigger action:"run"`)으로 검증할 것. MCP 가 헤드리스에서 빠지면
> 게시판 접근 대안(서비스키 기반 `scripts/board_dump.py` 신설 또는 `/board` API+토큰)이 필요.

## 에이전트 프롬프트 (루틴 events[].message.content)
```
프리즘 게시판 기반 기능개선 자동화(주 1회). 저장소: onam2518/prism · CLAUDE.md 규칙을 반드시 준수한다
(main 직접 커밋·push 금지 · feat/… 또는 fix/… 브랜치 → PR · pip 의존성 추가 금지(파이썬 표준 라이브러리만) ·
커밋 전 python3 -m unittest discover tests 전체 통과 · 커밋 메시지 한국어 conventional + Co-Authored-By 트레일러).

1. 소요 수집: Supabase MCP 로 프로젝트 yujinhcdbllcnnfvcmfp 의 public.prism_board 에서 status='open' 인 글
   (id, kind, title, body, team_key)을 조회한다. 한 건도 없으면 아무 것도 하지 말고 종료한다(빈 PR 금지).
2. 과제 도출: 글들을 클러스터링해 실행 가능한 기능개선/버그수정 과제로 정리하고, 이번 회차에 처리할
   '가장 가치 높고 안전·자기완결적인 1건'을 고른다. 대규모 리팩터·DB 마이그레이션 필요·프롬프트/등급 로직
   변경·불확실한 건은 이번엔 구현하지 않고 과제 목록으로만 남긴다.
3. 구현: feat/… 또는 fix/… 브랜치에서 최소 범위로 구현하고 회귀 테스트를 추가한다. 전체 테스트 통과 확인.
4. PR: gh pr create 로 PR 을 연다(절대 머지하지 않음 · 사람 리뷰). 본문에 근거가 된 게시판 글 id·요약과
   '이번에 처리하지 않은 나머지 과제 목록'을 적는다.
5. 처리 표시: 반영(PR 화)한 게시판 글은 Supabase MCP 로 prism_board.status 를 'doing' 으로 갱신해
   다음 회차 중복 선택을 막는다.
6. 안전: 파괴적 변경·대량 수정·프롬프트/등급 로직 변경은 하지 않는다(그런 소요는 PR 설명의 과제 목록에만
   남긴다). 실현 방법이 애매하면 코드 대신 '과제 목록 문서(docs/…)' PR 로 남긴다.
```

## RemoteTrigger create body (그대로 쓰거나 /schedule 로 생성)
```json
{
  "name": "Prism 게시판 기능개선 에이전트",
  "cron_expression": "0 0 * * 1",
  "enabled": true,
  "mcp_connections": [
    {"connector_uuid": "8dcb0be9-1126-442f-a4a1-5d785f41a284", "name": "Supabase", "url": "https://mcp.supabase.com/mcp"}
  ],
  "job_config": {
    "ccr": {
      "environment_id": "env_017NEzuQVtQCwV1Ff57LbsCj",
      "session_context": {
        "model": "claude-sonnet-5",
        "sources": [{"git_repository": {"url": "https://github.com/onam2518/prism"}}],
        "allowed_tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
      },
      "events": [{"data": {
        "uuid": "<새 lowercase v4 uuid>",
        "session_id": "", "type": "user", "parent_tool_use_id": null,
        "message": {"role": "user", "content": "<위 '에이전트 프롬프트' 전문>"}
      }}]
    }
  }
}
```
생성: `/schedule` 스킬 또는 https://claude.ai/code/routines · 삭제도 이 UI 에서.

## 미결정 · 옵션 (다른 세션에서 판단)
- **주기**: 주 1회(제안). PR 노이즈 vs 반영 속도 트레이드오프 — 일 1회면 `0 0 * * *`.
- **회차당 처리량**: 1건(안전 우선). 여러 건 병렬 PR 도 가능하나 리뷰 부담 증가.
- **status='doing' 자동 갱신**: 중복 방지에 필요하나, PR 반려 시 되돌리는 훅은 없음(수동 open 복귀).
- **범위 게이트**: 현재 프롬프트가 프롬프트/등급/마이그레이션 변경을 제외. 이 경계는 회차 로그로 관찰 후 조정.
- **첫 실행 검증**: 생성 후 `RemoteTrigger action:"run"` 1회로 gh·push·Supabase MCP 접근을 확인.

## 참고 코드 위치
- 게시판: `prism/supastore.py` `board_list/board_get/board_set_status`(1319~) · `prism/serve.py` `board_action`(2932) · 프론트 `mods` 게시판 항목(`vendor/app.js`)
- 스케줄러 관례: `prism/learnops.py` `start_learning_scheduler`·`handoff_bundle` · `prism/serve.py` `start_topic_scheduler`
