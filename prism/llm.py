"""OpenAI 호환 chat completions 호출 래퍼."""
from __future__ import annotations
import hashlib
import http.client
import json
import os
import re
import socket
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


# ── 프롬프트 캐시 usage 정규화 ───────────────────────────────────────────────
# 제공자·라우터마다 캐시 토큰 필드명이 다르다. 하나만 읽으면 '캐시가 이미 걸려 있어도
# 우리는 모르는' 상태가 된다(실제로 그랬다: 종전 파서는 prompt_tokens/completion_tokens 만 읽음).
# → 알려진 필드를 전부 훑어 (읽기·쓰기) 두 값으로 정규화한다.
#
# 채택 규칙: **합산하지 않고 처음 발견한 0 아닌 값을 쓴다.** LiteLLM 류 프록시는 원 제공자
# 필드와 OpenAI 호환 필드를 동시에 실어 주기도 해서, 합산하면 같은 토큰을 두 번 센다.
# 어느 필드에서 왔는지는 cache_source 로 남겨 라우터 동작을 사후 진단한다.
_CACHE_FIELDS = (
    ("prompt_tokens_details.cached_tokens", "read"),         # OpenAI·Azure·OpenRouter·LiteLLM·vLLM
    ("cache_read_input_tokens", "read"),                     # Anthropic
    ("prompt_cache_hit_tokens", "read"),                     # DeepSeek
    ("prompt_tokens_details.cache_write_tokens", "write"),   # 신형 OpenAI
    ("cache_creation_input_tokens", "write"),                # Anthropic
)
# '캐시 필드를 아예 안 주는 제공자'와 '주는데 값이 0(진짜 미스)'는 진단이 전혀 다르다.
# 후자를 구분하려고 0 이어도 존재만 하면 source 에 'miss:' 표식을 남긴다.
_CACHE_MISS_FIELDS = tuple(p for p, _ in _CACHE_FIELDS) + ("prompt_cache_miss_tokens",)


def _dig(d, path: str):
    """점 표기 경로로 중첩 dict 조회(없으면 None)."""
    cur = d
    for p in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def parse_cache_tokens(usage) -> tuple:
    """usage → (cache_read, cache_write, source).

    source: 값을 얻은 필드 경로(복수면 '+' 결합) · 0 이지만 필드가 있으면 'miss:<경로>' ·
    캐시 정보를 아예 안 주면 빈 문자열. 캐싱이 안 먹는 원인(라우터가 필드를 안 실어 줌 vs
    실어 주는데 매번 미스)을 가르는 유일한 단서라 값과 함께 보존한다."""
    if not isinstance(usage, dict):
        return 0, 0, ""
    read = write = 0
    srcs = []
    for path, kind in _CACHE_FIELDS:
        try:
            n = int(_dig(usage, path) or 0)
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue
        if kind == "read" and not read:
            read = n
            srcs.append(path)
        elif kind == "write" and not write:
            write = n
            srcs.append(path)
    if srcs:
        return read, write, "+".join(srcs)
    for path in _CACHE_MISS_FIELDS:                 # 값 0 이어도 필드 존재 = 제공자가 보고는 함
        if _dig(usage, path) is not None:
            return 0, 0, "miss:" + path
    return 0, 0, ""


# 캐시 키에 허용할 문자(라우터가 헤더·경로로 재사용해도 안전하게). 태그의 ':'(q:format_val ·
# legal:fraud)는 '-' 로 치환된다.
_SLUG = re.compile(r"[^a-zA-Z0-9_.-]+")


class LLMResult:
    def __init__(self, text, in_tok, out_tok, latency_ms, retries, raw=None,
                 price_in=PRICE_IN, price_out=PRICE_OUT, fail_kind=None, tag="",
                 cache_read_tok=0, cache_write_tok=0, cache_source="",
                 price_cache_read=None):
        self.text = text
        self.tag = tag                 # 호출 태그(quality·item_summary 등) · 콜별 비용 분해용
        self.in_tok = in_tok
        self.out_tok = out_tok
        # 프롬프트 캐시 토큰(정규화 · 제공자가 보고하지 않으면 0)
        self.cache_read_tok = int(cache_read_tok or 0)     # 캐시에서 읽은 입력 토큰
        self.cache_write_tok = int(cache_write_tok or 0)   # 캐시에 새로 쓴 입력 토큰
        self.cache_source = cache_source or ""             # 어느 usage 필드에서 왔는지(진단용)
        self.latency_ms = latency_ms
        self.retries = retries
        self.raw = raw
        self.price_in = price_in
        self.price_out = price_out
        self.price_cache_read = price_cache_read   # None = 미설정 → 캐시 토큰도 정가(하위호환)
        self.fail_kind = fail_kind     # None | content_filter | too_long | auth | ...
        self.fail_detail = ""          # 실패 원문(예외 메시지·HTTP 본문 앞부분) · 운영 진단용

    @property
    def cost_usd(self) -> float:
        """캐시 읽기 단가 반영 비용.

        · price_cache_read is None → 종전 식과 완전히 동일(하위호환 계약).
        · 캐시 읽은 토큰은 prompt_tokens 의 **부분집합**으로 본다(OpenAI 호환 규약 ·
          DeepSeek 은 hit+miss=prompt_tokens, Upstage 도 OpenAI 호환). 그래서 in_tok 에서
          빼고 캐시 단가로 다시 더한다. 뒤집힌 값(캐시>입력)은 라우터 이상이므로 clamp.
        · 캐시 '쓰기'(cache_creation)는 제공자마다 프리미엄(예: 정가의 1.25배)이 있고 없고가
          갈리는데 Upstage 는 공시가 없다 → **지금은 정가(price_in)로 계산한다.** 단가가
          확인되면 여기에 price_cache_write 분기를 추가할 것."""
        billed_in, cost = self.in_tok, 0.0
        if self.price_cache_read is not None and self.cache_read_tok > 0:
            cached = min(self.cache_read_tok, max(0, self.in_tok))
            billed_in = self.in_tok - cached
            cost += cached / 1e6 * self.price_cache_read
        return cost + billed_in / 1e6 * self.price_in + self.out_tok / 1e6 * self.price_out


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
        #  · prompt_cache_key: 옵트인(cfg.prompt_cache)으로만 전송. 미지 파라미터를 400 으로
        #    거부하는 라우터가 있어 같은 협상 테이블에 'no_cache_key' 로 태운다.
        # 첫 400 안내문에서 배우고 즉시 재시도하며, 결과는 모델별 프로세스 캐시로 공유(배치 400 낭비 방지).
        bare = str(self.model or "").split("/")[-1]
        seed = set(LLMClient._PARAM_ADAPT.get(bare, ()))
        if bare.startswith("gpt-5"):
            seed.add("max_completion")
        if bare.lower().startswith("gemini"):
            seed.add("no_response_format")     # 침묵 빈응답 방지(400 미발생이라 자동학습 불가 → 선제 시드)
        self._adapt = seed                     # {"max_completion","no_temperature","no_response_format","no_cache_key"}
        # 운영 집계(스레드세이프): 실패 분류 카운터
        self._lock = threading.Lock()
        self.fail_counts = {}   # {kind: n}

    _PARAM_ADAPT = {}                          # {bare_model: set(적응)} · 프로세스 전역(멱등 갱신이라 GIL 로 충분)

    def _learn_param(self, name: str):
        self._adapt.add(name)
        bare = str(self.model or "").split("/")[-1]
        LLMClient._PARAM_ADAPT.setdefault(bare, set()).add(name)

    # ── 프롬프트 캐시 키 ────────────────────────────────────────────────────
    def cache_key_on(self) -> bool:
        """이번 요청에 prompt_cache_key 를 실을지. 옵트인(cfg.prompt_cache) + 거부 학습 반영.

        기본 off 인 이유: 라우터가 미지 파라미터를 400 이 아니라 '침묵 빈응답'으로 돌려주는
        전례가 있어(gemini + response_format, 2026-07 실측) 400 학습이 못 잡는 실패 모드가 있다.
        관측(A)부터 먼저 배포해 캐시가 이미 걸려 있는지 확인한 뒤, PRISM_PROMPT_CACHE=1 로 켠다."""
        return bool(getattr(self.cfg, "prompt_cache", False)) and "no_cache_key" not in self._adapt

    def _cache_key(self, system: str, tag: str, json_on: bool) -> str:
        """프롬프트 캐시 키 = '프리픽스 정체성'. 콘텐츠(user)가 아니라 프리픽스만으로 결정된다.

        구성: 호출 종류(tag) + 프리픽스 지문 sha1[:12].
        지문 입력 = 모델 · reasoning_effort · 구조화 출력 on/off · **system 문자열 전문**.
          · 서비스: system 에 서비스별 인텐트 사전이 통째로 들어간다 → 지문이 자동 반영.
          · 프롬프트 버전: 버전 문자열이 아니라 그 버전이 만들어 낸 system 본문을 해싱한다.
            버전 태그는 실제 프리픽스의 프록시일 뿐이고(스튜디오 래퍼 편집·LEARNED 누적은
            버전을 올리지 않는다), 우리가 원하는 계약은 '같은 프리픽스 ⇒ 같은 키'다.
          · reasoning_effort·구조화 출력은 system 밖에 있으면서 실제 프리픽스를 분기시키므로
            지문에 포함한다(캐시 위험 ②·① · 키가 프리픽스를 앞지르지 않게 한다).
        """
        ident = "\x00".join([str(self.model or ""), str(self.reasoning_effort or ""),
                             "json" if json_on else "text", system or ""])
        h = hashlib.sha1(ident.encode("utf-8", "replace")).hexdigest()[:12]
        slug = _SLUG.sub("-", str(tag or "call")).strip("-") or "call"
        return "prism-%s-%s" % (slug[:32], h)

    # 공개 API
    def complete_text(self, system: str, user: str, tag: str = "") -> LLMResult:
        """자유 텍스트 완성(JSON 강제·파싱 없음) · 빌더 테스트 실행 등 산출 원문 확인용."""
        if self.mock:
            _obj, res = self._mock(system, user, tag)
            return res
        res = self._call(system, user, json_mode=False, tag=tag)
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
                res = self._call(system, user, tag=tag)
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
                    elif self.cache_key_on() and "prompt_cache_key" in detail:
                        learned = "no_cache_key"           # 라우터가 이름을 짚어 거부
                    elif (self.cache_key_on()
                          and classify_http_error(400, detail) == "bad_request"):
                        # 이름을 안 짚는 라우터도 있다("Extra inputs are not permitted" 류).
                        # 우리가 가장 최근에 얹은 비필수 파라미터가 prompt_cache_key 이므로
                        # 원인 미상 400 이면 그것부터 빼고 1회 재시도한다(보수적 = 종전 동작 복귀).
                        # 콘텐츠 필터·입력 초과로 분류되는 400 은 여기 오지 않는다(오탐 방지).
                        learned = "no_cache_key"
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
                # 네트워크/타임아웃/응답 형식: 백오프 재시도 후 종류를 나눠 기록
                last_err = e
                if attempt < rp.max_retries:
                    retries += 1
                    time.sleep(backoff_delay(attempt, rp.base_delay, rp.max_delay, rp.jitter))
                    continue
                return self._fail(classify_exc(e), f"{type(e).__name__}: {e}", retries, tag=tag)
        return self._fail("unknown", str(last_err), retries, tag=tag)

    def _fail(self, kind, detail, retries, tag=""):
        with self._lock:
            self.fail_counts[kind] = self.fail_counts.get(kind, 0) + 1
        res = LLMResult("", 0, 0, 0, retries,
                        price_in=self.cfg.prices.chat_in,
                        price_out=self.cfg.prices.chat_out, fail_kind=kind, tag=tag)
        res.fail_detail = str(detail or "")[:300]      # 원인 원문 보존(하네스 → 실패 원장 → 화면)
        return ({"_fail": detail, "_fail_kind": kind}, res)

    # 내부
    def _call(self, system: str, user: str, json_mode: bool = True, tag: str = "") -> LLMResult:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        json_on = json_mode and "no_response_format" not in self._adapt
        if json_on:
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
        if self.cache_key_on():
            # Upstage 공식 캐싱 파라미터. 같은 프리픽스면 같은 값이 나가야 캐시가 붙는다.
            body["prompt_cache_key"] = self._cache_key(system, tag, json_on)

        # 레이트리밋 게이팅(입력 토큰 근사로 예약)
        self.limiter.acquire(_approx_tokens(system + user))

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.cfg.chat_url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")

        t0 = time.time()
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        latency = int((time.time() - t0) * 1000)
        # 200 인데 봉투가 계약과 다른 경우(프록시 HTML·잘린 본문·choices 누락)를 연결 실패로
        # 오분류하지 않는다 — 여기서 안 잡으면 JSONDecodeError·KeyError 가 network 으로 샌다.
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ResponseError(f"비 JSON 응답({e}): {raw[:120]!r}")
        if not isinstance(payload, dict):
            raise ResponseError(f"응답이 객체가 아님({type(payload).__name__})")

        choices = payload.get("choices") or []
        try:
            text = (choices[0]["message"]["content"] if choices else "") or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ResponseError(f"choices 형식 불일치({e}): {raw[:120]!r}")
        usage = payload.get("usage", {})
        in_tok = usage.get("prompt_tokens", _approx_tokens(system + user))
        out_tok = usage.get("completion_tokens", _approx_tokens(text))
        # 캐시 토큰 정규화: 제공자마다 필드명이 달라 전부 훑는다(없으면 0 · 비용식은 하위호환).
        c_read, c_write, c_src = parse_cache_tokens(usage)
        if not text.strip():
            raise EmptyError("empty completion")
        return LLMResult(text, in_tok, out_tok, latency, 0, raw=payload,
                         price_in=self.cfg.prices.chat_in,
                         price_out=self.cfg.prices.chat_out,
                         cache_read_tok=c_read, cache_write_tok=c_write, cache_source=c_src,
                         price_cache_read=self.cfg.prices.cache_read, tag=tag)

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


class ResponseError(Exception):
    """HTTP 200 인데 응답 봉투가 우리 계약과 다름(비 JSON · choices 누락 등).
    연결 문제가 아니므로 network 으로 뭉뚱그리지 않는다."""


def classify_exc(e: Exception) -> str:
    """비-HTTP 예외 세분화. 종전에는 전부 'network(연결 실패)' 한 바구니라
    운영에서 '연결 실패' 배지만 보고는 타임아웃인지 응답 형식 문제인지 알 수 없었다
    (2026-07-28 item_entities 실패 진단 불가). 재시도 정책은 그대로 두고 이름만 나눈다."""
    if isinstance(e, ResponseError):
        return "bad_response"
    if isinstance(e, (TimeoutError, socket.timeout)):          # 3.8 은 socket.timeout != TimeoutError
        return "timeout"
    if isinstance(e, urllib.error.URLError):
        reason = getattr(e, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):  # 연결 단계 타임아웃은 URLError 로 감싸여 온다
            return "timeout"
        return "network"
    if isinstance(e, (ConnectionError, http.client.HTTPException, OSError)):
        return "network"
    return "unknown"


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
