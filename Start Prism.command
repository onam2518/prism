#!/bin/bash
# Prism 로컬 UI 실행기 — 더블클릭하면 서버가 켜지고 브라우저가 열립니다.
# (이 창을 닫으면 서버도 종료됩니다.)

PORT=8765
URL="http://127.0.0.1:${PORT}"

# 이 스크립트가 있는 폴더(=레포 루트)로 이동. 데스크탑 사본은 아래 REPO 로 대체됨.
REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO" 2>/dev/null || { echo "프로젝트 폴더를 찾을 수 없습니다: $REPO"; echo "엔터를 누르면 닫힙니다."; read -r; exit 1; }

clear
echo "──────────────────────────────────────────────"
echo "   Prism · 리드문·메타 추출  로컬 UI"
echo "──────────────────────────────────────────────"

# 이미 실행 중이면 브라우저만 열고 종료
if curl -s -o /dev/null --max-time 1 "$URL" 2>/dev/null; then
  echo "   이미 실행 중 → 브라우저를 엽니다."
  open "$URL"
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "   python3 를 찾을 수 없습니다. Python 3 설치 후 다시 시도하세요."
  echo "   엔터를 누르면 닫힙니다."; read -r; exit 1
fi

echo "   서버 시작 중...  (이 창을 닫으면 종료)"
echo "   주소 : $URL"
echo "   API 키: 브라우저 우상단 '설정' 버튼에서 입력"
echo "──────────────────────────────────────────────"

# 서버가 응답하면 브라우저 자동 오픈(백그라운드 대기)
( for _ in $(seq 1 40); do
    curl -s -o /dev/null --max-time 1 "$URL" 2>/dev/null && { open "$URL"; break; }
    sleep 0.5
  done ) &

exec python3 -m prism.serve --port "$PORT"
