"""스레드세이프 레이트리미터 (운영 하드닝). RPM/TPM 토큰버킷."""
from __future__ import annotations
import threading
import time


class RateLimiter:
    def __init__(self, rpm: int = 0, tpm: int = 0):
        self.rpm = rpm
        self.tpm = tpm
        self._lock = threading.Lock()
        self._req_times = []        # 최근 1분 요청 시각
        self._tok_events = []       # (시각, 토큰수)

    def acquire(self, est_tokens: int = 0):
        """제한을 넘지 않을 때까지 블록. 호출당 1회 진입."""
        if not self.rpm and not self.tpm:
            return
        while True:
            with self._lock:
                now = time.time()
                self._evict(now)
                wait = 0.0
                if self.rpm and len(self._req_times) >= self.rpm:
                    wait = max(wait, 60 - (now - self._req_times[0]))
                if self.tpm:
                    used = sum(t for _, t in self._tok_events)
                    if used + est_tokens > self.tpm and self._tok_events:
                        wait = max(wait, 60 - (now - self._tok_events[0][0]))
                if wait <= 0:
                    self._req_times.append(now)
                    if est_tokens:
                        self._tok_events.append((now, est_tokens))
                    return
            time.sleep(min(wait, 5.0))

    def _evict(self, now):
        cut = now - 60
        self._req_times = [t for t in self._req_times if t > cut]
        self._tok_events = [(t, n) for t, n in self._tok_events if t > cut]


def backoff_delay(attempt: int, base: float, cap: float, jitter: float) -> float:
    """지수백오프 + 지터. attempt 0,1,2... → base*2^attempt, cap 상한, ±jitter.
    Math.random 류 금지 환경 대비: 시각 기반 의사난수로 지터 산출(결정 불필요)."""
    raw = min(cap, base * (2 ** attempt))
    frac = (time.time() * 1000) % 1000 / 1000.0   # 0~1 의사난수
    delta = raw * jitter * (frac - 0.5) * 2        # ±(raw*jitter)
    return max(0.0, raw + delta)


def classify_http_error(code: int, body: str) -> str:
    """비재시도성 오류를 운영 분류로 라벨링(매니페스트 집계용)."""
    b = (body or "").lower()
    if code == 400:
        if any(k in b for k in ("content", "policy", "safety", "filter", "moderation")):
            return "content_filter"     # 콘텐츠 필터 거부
        if any(k in b for k in ("length", "token", "context", "maximum")):
            return "too_long"
        return "bad_request"
    if code == 401:
        return "auth"
    if code == 403:
        return "forbidden"
    if code == 404:
        return "not_found"
    if code == 422:
        return "unprocessable"
    if code == 402:
        # 402 는 두 가지이고 **대응이 완전히 다르다**(타임리 라우터 문서 · 2026-07-30):
        #   insufficient_balance   = 스페이스 크레딧 부족  → 충전해야 한다
        #   project_limit_exceeded = 프로젝트 지출 한도 도달 → 한도를 올려야 한다
        # 종전엔 둘 다 'billing' 으로 뭉쳐서, 원장만 보고는 무엇을 해야 하는지 알 수 없었다
        # (실측: 7/29 에 402 832건이 났는데 충전인지 한도인지 사후 판별이 불가능했다).
        if any(k in b for k in ("project_limit", "limit_exceeded", "spend", "quota")):
            return "quota"              # 프로젝트 지출 한도(한도 상향 · 재실행해도 계속 실패)
        return "billing"                # 잔액·크레딧 소진(충전 전에는 재실행해도 계속 실패)
    return f"http_{code}"
