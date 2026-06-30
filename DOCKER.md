# Prism — Docker (팀 공유 서버)

Prism 코어는 **의존성 0**(파이썬 stdlib)이라 이미지가 가볍고 빌드가 단순하다. 팀이 한 호스트의
컨테이너를 공유하면 그대로 **실시간 휴먼인루프(HITL)** 환경이 된다(검수·피드백·REAP가 한 DB로).

## 빠른 시작

```bash
# 1) 빌드
docker build -t prism .

# 2) 실행 (키 없으면 자동 mock)
docker run -p 8765:8765 -v prism-data:/data prism
#   → http://localhost:8765

# 3) 실모델 (Upstage 키 주입)
docker run -p 8765:8765 -e UPSTAGE_API_KEY=up_xxx -v prism-data:/data prism
```

## compose (권장 — 팀 서버)

```bash
export UPSTAGE_API_KEY=up_xxx          # 또는 .env 파일
docker compose up -d                   # 백그라운드 상시 가동(restart)
docker compose logs -f                 # REAP/검수 로그 확인
```

LAN의 팀원은 `http://<호스트IP>:8765` 로 접속해 같은 검수 큐·피드백을 공유한다.

## 영속화 · 설정

| 항목 | 방법 |
|---|---|
| DB(검수·피드백·REAP·결과) | 볼륨 `prism-data:/data` (env `PRISM_DB=/data/prism.db`) |
| API 키 | `-e UPSTAGE_API_KEY=...` (이미지에 굽지 않음) |
| 모델 | `-e PRISM_MODEL=solar-pro3` |
| 동시성·RPM·TPM | `-e PRISM_CONCURRENCY/PRISM_RPM/PRISM_TPM` |

## 메모

- 키 미주입 시 자동 mock — 설치·키 없이 UI 흐름 전체를 체험 가능.
- 벤더 에셋(Tailwind/Alpine/Pretendard)은 `prism/vendor/` 에 포함 → 컨테이너는 **오프라인 동작**.
- 이미지엔 코어(`prism/`)만 포함(`.dockerignore` 로 design-system·docs·desktop 제외).
