"""OpenAI 호환 chat completions 호출 래퍼."""
from __future__ import annotations
import json
import os
import threading
import time
import urllib.request
import urllib.error

from .config import Config
from .ratelimit import RateLimiter, backoff_delay, classify_http_error

# 공시 단가 fallback (config 없을 때), USD per 1M tokens
PRICE_IN = 0.15
PRICE_OUT = 0.60


class LLMResult:
    def __init__(self, text, in_tok, out_tok, latency_ms, retries, raw=None,
                 price_in=PRICE_IN, price_out=PRICE_OUT, fail_kind=None):
        self.text = text
        self.in_tok = in_tok
        self.out_tok = out_tok
        self.latency_ms = latency_ms
        self.retries = retries
        self.raw = raw
        self.price_in = price_in
        self.price_out = price_out
        self.fail_kind = fail_kind     # None | content_filter | too_long | auth | ...

    @property
    def cost_usd(self) -> float:
        return self.in_tok / 1e6 * self.price_in + self.out_tok / 1e6 * self.price_out


class LLMClient:
    def __init__(self, model=None, api_key=None, mock=False,
                 reasoning_effort=None, timeout=None, config: Config | None = None,
                 limiter: RateLimiter | None = None):
        self.cfg = config or Config.load()
        self.model = model or self.cfg.model
        self.api_key = api_key or self.cfg.api_key or os.environ.get("PRISM_API_KEY", os.environ.get("UPSTAGE_API_KEY", ""))
        self.mock = mock or not self.api_key
        self.reasoning_effort = reasoning_effort or self.cfg.reasoning_effort
        self.timeout = timeout or self.cfg.timeout
        self.limiter = limiter or RateLimiter(self.cfg.rate.rpm, self.cfg.rate.tpm)
        self._mock_fn = None  # pipeline 이 주입하는 결정론적 mock 생성기
        # 운영 집계(스레드세이프): 실패 분류 카운터
        self._lock = threading.Lock()
        self.fail_counts = {}   # {kind: n}

    # 공개 API
    def complete_json(self, system: str, user: str, tag: str = "") -> tuple[dict, LLMResult]:
        """JSON 객체를 강제 파싱해 반환. 실패 시 1회 재시도 후 빈 dict + 표식."""
        if self.mock:
            return self._mock(system, user, tag)

        rp = self.cfg.retry
        last_err = None
        retries = 0
        # 최초 + max_retries 재시도, 지수백오프+지터(429/5xx), 비재시도성은 즉시 fail
        for attempt in range(rp.max_retries + 1):
            try:
                res = self._call(system, user)
                obj = _parse_json(res.text)
                res.retries = retries
                return obj, res
            except (ParseError, EmptyError) as e:
                # 형식 실패도 재시도(EMPTY 는 테스트상 최대 손실원)
                last_err = e
                if attempt < rp.max_retries:
                    retries += 1
                    time.sleep(backoff_delay(attempt, rp.base_delay, rp.max_delay, rp.jitter))
                    continue
                return self._fail("parse_empty", str(e), retries)
            except urllib.error.HTTPError as e:
                try:
                    detail = e.read().decode()[:200]
                except Exception:
                    detail = ""
                if e.code in rp.retry_status and attempt < rp.max_retries:
                    # 레이트리밋/일시 오류 → 지수백오프 재시도
                    retries += 1
                    last_err = e
                    time.sleep(backoff_delay(attempt, rp.base_delay, rp.max_delay, rp.jitter))
                    continue
                # 400(콘텐츠 필터 등) 비재시도성 → 분류 후 fail(배치 비중단)
                kind = classify_http_error(e.code, detail)
                return self._fail(kind, f"HTTP{e.code}: {detail}", retries)
            except Exception as e:
                # 네트워크/타임아웃: 백오프 재시도
                last_err = e
                if attempt < rp.max_retries:
                    retries += 1
                    time.sleep(backoff_delay(attempt, rp.base_delay, rp.max_delay, rp.jitter))
                    continue
                return self._fail("network", str(e), retries)
        return self._fail("unknown", str(last_err), retries)

    def _fail(self, kind, detail, retries):
        with self._lock:
            self.fail_counts[kind] = self.fail_counts.get(kind, 0) + 1
        return ({"_fail": detail, "_fail_kind": kind},
                LLMResult("", 0, 0, 0, retries,
                          price_in=self.cfg.prices.chat_in,
                          price_out=self.cfg.prices.chat_out, fail_kind=kind))

    # 내부
    def _call(self, system: str, user: str) -> LLMResult:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "stream": False,
        }
        if self.reasoning_effort and self.reasoning_effort != "default":
            # Upstage 가 지원하면 reasoning 강도 제어; 미지원이면 무해하게 무시됨
            body["reasoning_effort"] = self.reasoning_effort

        # 레이트리밋 게이팅(입력 토큰 근사로 예약)
        self.limiter.acquire(_approx_tokens(system + user))

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.cfg.chat_url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")

        t0 = time.time()
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        latency = int((time.time() - t0) * 1000)

        choices = payload.get("choices") or []
        text = (choices[0]["message"]["content"] if choices else "") or ""
        usage = payload.get("usage", {})
        in_tok = usage.get("prompt_tokens", _approx_tokens(system + user))
        out_tok = usage.get("completion_tokens", _approx_tokens(text))
        if not text.strip():
            raise EmptyError("empty completion")
        return LLMResult(text, in_tok, out_tok, latency, 0, raw=payload,
                         price_in=self.cfg.prices.chat_in,
                         price_out=self.cfg.prices.chat_out)

    def _mock(self, system, user, tag) -> tuple[dict, LLMResult]:
        obj = self._mock_fn(system, user, tag) if self._mock_fn else {}
        text = json.dumps(obj, ensure_ascii=False)
        # mock 도 토큰/지연을 근사 계상해 비용 모델을 굴려본다
        in_tok = _approx_tokens(system + user)
        out_tok = _approx_tokens(text) + (1400 if self.reasoning_effort != "off" else 0)
        return obj, LLMResult(text, in_tok, out_tok, 5, 0, raw={"mock": True},
                              price_in=self.cfg.prices.chat_in,
                              price_out=self.cfg.prices.chat_out)


class ParseError(Exception):
    pass


class EmptyError(Exception):
    pass


def _parse_json(text: str) -> dict:
    """json_object 강제에도 모델이 코드펜스/잡텍스트를 붙이는 경우 복구."""
    if not text or not text.strip():
        raise EmptyError("empty")
    s = text.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # 코드펜스 제거
    if s.startswith("```"):
        s = s.strip("`")
        if s[:4].lower() == "json":
            s = s[4:]
    # 첫 { ~ 마지막 } 슬라이스
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j != -1 and j > i:
        try:
            return json.loads(s[i:j + 1])
        except json.JSONDecodeError:
            pass
    raise ParseError(f"unparseable: {text[:80]!r}")


def _approx_tokens(text: str) -> int:
    # 한글 혼합 텍스트 거친 근사: 문자수/2.5
    return max(1, int(len(text or "") / 2.5))
