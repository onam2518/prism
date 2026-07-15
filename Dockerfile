# Prism 로컬 웹 UI — 의존성 0(파이썬 stdlib만). 슬림 이미지.
# 빌드:  docker build -t prism .
# 실행:  docker run -p 8765:8765 -e UPSTAGE_API_KEY=... -v prism-data:/data prism
FROM python:3.12-slim

WORKDIR /app

# 코어 패키지만(의존성 없음). 벤더 에셋(Tailwind/Alpine/폰트)은 prism/vendor 에 포함.
COPY prism/ ./prism/
COPY data/*.template.jsonl ./data/

# DB·런 산출물은 볼륨(/data)에 영속화. 키는 런타임 env.
ENV PRISM_DB=/data/prism.db \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8765

# 헬스체크(의존성 0 · curl 없이 stdlib): /config 200 확인. Fly 체크와 별개로 로컬 docker run 에도 유효.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/config', timeout=4).status==200 else 1)" || exit 1

# 팀 공유(LAN HITL)를 위해 0.0.0.0 바인드. 키 없으면 자동 mock.
CMD ["python", "-m", "prism.serve", "--host", "0.0.0.0", "--port", "8765"]
