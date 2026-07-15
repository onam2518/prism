"""임베딩 클라이언트 (LLM 의존을 줄이는 결정론 레이어)."""
from __future__ import annotations
import json
import math
import os
import sys
import time
import hashlib
import urllib.request
import urllib.error

from .ratelimit import backoff_delay

_EMB_RETRY_STATUS = (429, 500, 502, 503, 504)
_warned_implicit_mock = False


def _retry_after_seconds(e):
    """HTTPError 의 Retry-After 를 안전하게 초로 파싱. HTTP-date·음수·비수치는 None(→백오프)."""
    v = e.headers.get("Retry-After") if getattr(e, "headers", None) else None
    if not v:
        return None
    try:
        return max(0, min(int(v), 60))
    except (ValueError, TypeError):
        return None

EMB_URL = "https://api.upstage.ai/v1/solar/embeddings"
QUERY_MODEL = "solar-embedding-1-large-query"
PASSAGE_MODEL = "solar-embedding-1-large-passage"
DIM_MOCK = 256
# 임베딩 단가(공시, USD/1M tokens). 콘솔 실측으로 교체 필요(미확정 표기).
PRICE_EMB = 0.10


class EmbeddingClient:
    def __init__(self, api_key=None, mock=False, cache_path=None):
        self.api_key = api_key or os.environ.get("PRISM_API_KEY", os.environ.get("UPSTAGE_API_KEY", ""))
        self.implicit_mock = (not mock) and (not self.api_key)   # 키 부재로 인한 암묵 mock(요청한 mock 과 구분)
        self.mock = mock or not self.api_key
        if self.implicit_mock:                                  # 배포에서 조용한 휴리스틱 라벨 생산을 로그로 표면화
            global _warned_implicit_mock
            if not _warned_implicit_mock:
                _warned_implicit_mock = True
                sys.stderr.write("[prism] WARNING: 임베딩 API 키 없음 → mock(휴리스틱) 임베딩 사용 · 실라벨 아님\n")
        self.cache_path = cache_path
        self._cache = _load_cache(cache_path) if cache_path else {}
        self.tokens = 0

    def embed(self, text: str, *, is_query=True) -> list:
        key = _ckey(text, is_query, self.mock)
        if key in self._cache:
            return self._cache[key]
        vec = self._mock_embed(text) if self.mock else self._api_embed(text, is_query)
        self._cache[key] = vec
        return vec

    def embed_many(self, texts: list, *, is_query=True) -> list:
        return [self.embed(t, is_query=is_query) for t in texts]

    def flush(self):
        if self.cache_path:
            _save_cache(self.cache_path, self._cache)

    # 내부
    def _api_embed(self, text, is_query, max_retries=3) -> list:
        body = json.dumps({"model": QUERY_MODEL if is_query else PASSAGE_MODEL,
                           "input": text}).encode()
        last = None
        for attempt in range(max_retries + 1):
            req = urllib.request.Request(EMB_URL, data=body, method="POST")
            req.add_header("Authorization", f"Bearer {self.api_key}")
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    payload = json.loads(resp.read().decode())
                self.tokens += payload.get("usage", {}).get("total_tokens", 0)
                return payload["data"][0]["embedding"]
            except urllib.error.HTTPError as e:              # 429/5xx → Retry-After 우선 · 아니면 지수백오프
                last = e
                if e.code in _EMB_RETRY_STATUS and attempt < max_retries:
                    ra = _retry_after_seconds(e)
                    time.sleep(ra if ra is not None else backoff_delay(attempt, 1.0, 20.0, 0.3))
                    continue
                raise
            except Exception as e:                           # 네트워크/타임아웃 → 백오프 재시도
                last = e
                if attempt < max_retries:
                    time.sleep(backoff_delay(attempt, 1.0, 20.0, 0.3))
                    continue
                raise
        raise last                                           # 이론상 도달 불가(방어)

    def _mock_embed(self, text) -> list:
        """결정론적 해싱 임베딩: 문자 3-gram 을 DIM_MOCK 버킷에 해싱 후 L2 정규화.
        같은 의미 텍스트의 코사인을 어느 정도 보존(배관 검증용)."""
        v = [0.0] * DIM_MOCK
        toks = _ngrams((text or "").lower(), 3) + (text or "").lower().split()
        for t in toks:
            h = int(hashlib.md5(t.encode()).hexdigest(), 16)
            v[h % DIM_MOCK] += 1.0
            v[(h // DIM_MOCK) % DIM_MOCK] += 0.5
        return _l2(v)

    @property
    def cost_usd(self) -> float:
        return self.tokens / 1e6 * PRICE_EMB


# 수치 유틸
def cosine(a: list, b: list) -> float:
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return s / (na * nb)


def rank_by_cosine(query_vec: list, anchors: dict) -> list:
    """anchors: {label: vec} → [(label, cos), ...] 내림차순."""
    scored = [(lbl, cosine(query_vec, v)) for lbl, v in anchors.items()]
    return sorted(scored, key=lambda kv: -kv[1])


def _l2(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _ngrams(s, n):
    s = "".join(s.split())
    return [s[i:i + n] for i in range(max(0, len(s) - n + 1))]


def _ckey(text, is_query, mock):
    h = hashlib.sha1((("Q" if is_query else "P") + ("M" if mock else "A") + text).encode()).hexdigest()[:16]
    return h


def _load_cache(path):
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_cache(path, cache):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f)
