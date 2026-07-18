"""OpenAI 호환 chat completions 호출 래퍼."""
from __future__ import annotations
import json
import os
import sys
import threading
import time
import urllib.request
import urllib.error

from .config import Config
from .ratelimit import RateLimiter, backoff_delay, classify_http_error

# 공시 단가 fallback (config 없을 때), USD per 1M tokens
PRICE_IN = 0.15
PRICE_OUT = 0.60
_warned_implicit_mock = False


class LLMResult:
    def __init__(self, text, in_tok, out_tok, latency_ms, retries, raw=None,
                 price_in=PRICE_IN, price_out=PRICE_OUT, fail_kind=None, tag=""):
        self.text = text
        self.tag = tag                 # 호출 태그(quality·item_summary 등) · 콜별 비용 분해용
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
        self.implicit_mock = (not mock) and (not self.api_key)   # 키 부재로 인한 암묵 mock(요청한 mock 과 구분)
        self.mock = mock or not self.api_key
        if self.implicit_mock:                                  # 배포에서 조용한 휴리스틱 라벨 생산을 로그로 표면화
            global _warned_implicit_mock
            if not _warned_implicit_mock:
                _warned_implicit_mock = True
                sys.stderr.write("[prism] WARNING: LLM API 키 없음 → mock(휴리스틱) 라벨 사용 · 실판정 아님\n")
        self.reasoning_effort = reasoning_effort or self.cfg.reasoning_effort
        self.timeout = timeout or self.cfg.timeout
        self.limiter = limiter or RateLimiter(self.cfg.rate.rpm, self.cfg.rate.tpm)
        self._mock_fn = None  # pipeline 이 주입하는 결정론적 mock 생성기
        # 모델별 파라미터 협상: 라우터(Timely 등) 경유 시 모델마다 거부 파라미터가 다르다(실측 2026-07-07).
        #  · gpt-5*: max_tokens 미지원(라우터가 기본값 주입) → max_completion_tokens 명시
        #  · gpt-5* + reasoning_effort: temperature=0 거부(기본 1만 허용) → temperature 생략
        #  · claude-*: response_format json_object 거부(json_schema 만) → response_format 생략(_parse_json 이 복구)
        #  · gemini-*: 라우터 경유 시 response_format json_object 를 400 이 아니라 '침묵 빈응답'(HTTP200·content 공백)으로
        #    돌려줘(실측 2026-07 · gpt/claude/solar 정상, gemini 계열 전량 빈값) 400 학습 트리거가 안 걸린다.
        #    → 계열 판정으로 선제 시드(response_format 생략, 프롬프트 계약 + _parse_json 복구에 의존).
        # 첫 400 안내문에서 배우고 즉시 재시도하며, 결과는 모델별 프로세스 캐시로 공유(배치 400 낭비 방지).
        bare = str(self.model or "").split("/")[-1]
        seed = set(LLMClient._PARAM_ADAPT.get(bare, ()))
        if bare.startswith("gpt-5"):
            seed.add("max_completion")
        if bare.lower().startswith("gemini"):
            seed.add("no_response_format")     # 침묵 빈응답 방지(400 미발생이라 자동학습 불가 → 선제 시드)
        self._adapt = seed                     # {"max_completion", "no_temperature", "no_response_format"}
        # 운영 집계(스레드세이프): 실패 분류 카운터
        self._lock = threading.Lock()
        self.fail_counts = {}   # {kind: n}

    _PARAM_ADAPT = {}                          # {bare_model: set(적응)} · 프로세스 전역(멱등 갱신이라 GIL 로 충분)

    def _learn_param(self, name: str):
        self._adapt.add(name)
        bare = str(self.model or "").split("/")[-1]
        LLMClient._PARAM_ADAPT.setdefault(bare, set()).add(name)

    # 공개 API
    def complete_text(self, system: str, user: str, tag: str = "") -> LLMResult:
        """자유 텍스트 완성(JSON 강제·파싱 없음) · 빌더 테스트 실행 등 산출 원문 확인용."""
        if self.mock:
            _obj, res = self._mock(system, user, tag)
            return res
        res = self._call(system, user, json_mode=False)
        res.tag = tag or res.tag
        return res

    def complete_json(self, system: str, user: str, tag: str = "") -> tuple[dict, LLMResult]:
        """JSON 객체를 강제 파싱해 반환. 실패 시 1회 재시도 후 빈 dict + 표식."""
        if self.mock:
            obj, res = self._mock(system, user, tag)
            res.tag = tag
            return obj, res

        rp = self.cfg.retry
        last_err = None
        retries = 0
        # 최초 + max_retries 재시도, 지수백오프+지터(429/5xx), 비재시도성은 즉시 fail
        for attempt in range(rp.max_retries + 1):
            try:
                res = self._call(system, user)
                obj = _parse_json(res.text)
                res.retries = retries
                res.tag = tag
                return obj, res
            except (ParseError, EmptyError) as e:
                # 형식 실패도 재시도(EMPTY 는 테스트상 최대 손실원)
                last_err = e
                if attempt < rp.max_retries:
                    retries += 1
                    time.sleep(backoff_delay(attempt, rp.base_delay, rp.max_delay, rp.jitter))
                    continue
                return self._fail("parse_empty", str(e), retries, tag=tag)
            except urllib.error.HTTPError as e:
                try:
                    detail = e.read().decode()[:200]
                except Exception:
                    detail = ""
                if e.code == 400:
                    # 파라미터 협상: 400 안내문에서 거부 파라미터를 배우고 즉시 재시도(백오프 불필요)
                    learned = ""
                    if "max_completion" not in self._adapt and "max_completion_tokens" in detail:
                        learned = "max_completion"
                    elif ("no_temperature" not in self._adapt and "temperature" in detail
                          and ("does not support" in detail or "unsupported_value" in detail
                               or "deprecated" in detail)):
                        learned = "no_temperature"
                    elif "no_response_format" not in self._adapt and "response_format" in detail:
                        learned = "no_response_format"
                    if learned:
                        self._learn_param(learned)
                        retries += 1
                        last_err = e
                        continue
                if e.code in rp.retry_status and attempt < rp.max_retries:
                    # 레이트리밋/일시 오류 → 지수백오프 재시도
                    retries += 1
                    last_err = e
                    time.sleep(backoff_delay(attempt, rp.base_delay, rp.max_delay, rp.jitter))
                    continue
                # 400(콘텐츠 필터 등) 비재시도성 → 분류 후 fail(배치 비중단)
                kind = classify_http_error(e.code, detail)
                return self._fail(kind, f"HTTP{e.code}: {detail}", retries, tag=tag)
            except Exception as e:
                # 네트워크/타임아웃: 백오프 재시도
                last_err = e
                if attempt < rp.max_retries:
                    retries += 1
                    time.sleep(backoff_delay(attempt, rp.base_delay, rp.max_delay, rp.jitter))
                    continue
                return self._fail("network", str(e), retries, tag=tag)
        return self._fail("unknown", str(last_err), retries, tag=tag)

    def _fail(self, kind, detail, retries, tag=""):
        with self._lock:
            self.fail_counts[kind] = self.fail_counts.get(kind, 0) + 1
        return ({"_fail": detail, "_fail_kind": kind},
                LLMResult("", 0, 0, 0, retries,
                          price_in=self.cfg.prices.chat_in,
                          price_out=self.cfg.prices.chat_out, fail_kind=kind, tag=tag))

    # 내부
    def _call(self, system: str, user: str, json_mode: bool = True) -> LLMResult:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        if json_mode and "no_response_format" not in self._adapt:
            # 일부 모델(라우터 경유 claude 등)은 json_object 를 거부 → 생략(프롬프트 계약 + _parse_json 복구)
            body["response_format"] = {"type": "json_object"}
        if "no_temperature" not in self._adapt:
            # 일부 추론형 모델(gpt-5 + reasoning_effort)은 temperature=0 거부(기본 1만 허용) → 생략
            body["temperature"] = 0
        if self.reasoning_effort and self.reasoning_effort != "default":
            # Upstage 가 지원하면 reasoning 강도 제어; 미지원이면 무해하게 무시됨
            body["reasoning_effort"] = self.reasoning_effort
        if "max_completion" in self._adapt:
            # max_tokens 미지원 모델(gpt-5 계열 등): 명시하면 라우터가 max_tokens 를 주입하지 않는다.
            # 추론형 모델은 추론 토큰도 이 상한에 포함되므로 넉넉히 잡는다(출력 잘림 = JSON 파싱 실패).
            body["max_completion_tokens"] = 8192

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
        obj = json.loads(s)
        # dict 강제: 배열/문자열/null 이 그대로 흘러가면 호출부 obj.get 에서 AttributeError 로
        # 배치 전체가 죽는다(response_format 미지원 계열에서 실제 발생 가능).
        # dict 가 아니면 아래 { } 슬라이스 복구([{…}] → 내부 객체)로 폴백.
        if isinstance(obj, dict):
            return obj
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
            obj = json.loads(s[i:j + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    raise ParseError(f"unparseable: {text[:80]!r}")


def _approx_tokens(text: str) -> int:
    # 한글 혼합 텍스트 거친 근사: 문자수/2.5
    return max(1, int(len(text or "") / 2.5))
