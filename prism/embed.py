"""임베딩 클라이언트 (LLM 의존을 줄이는 결정론 레이어)."""
from __future__ import annotations
import json
import math
import os
import hashlib
import urllib.request
import urllib.error

EMB_URL = "https://api.upstage.ai/v1/solar/embeddings"
QUERY_MODEL = "solar-embedding-1-large-query"
PASSAGE_MODEL = "solar-embedding-1-large-passage"
DIM_MOCK = 256
# 임베딩 단가(공시, USD/1M tokens). 콘솔 실측으로 교체 필요(미확정 표기).
PRICE_EMB = 0.10


class EmbeddingClient:
    def __init__(self, api_key=None, mock=False, cache_path=None):
        self.api_key = api_key or os.environ.get("PRISM_API_KEY", os.environ.get("UPSTAGE_API_KEY", ""))
        self.mock = mock or not self.api_key
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
    def _api_embed(self, text, is_query) -> list:
        body = {"model": QUERY_MODEL if is_query else PASSAGE_MODEL, "input": text}
        req = urllib.request.Request(EMB_URL, data=json.dumps(body).encode(),
                                     method="POST")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
        self.tokens += payload.get("usage", {}).get("total_tokens", 0)
        return payload["data"][0]["embedding"]

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
