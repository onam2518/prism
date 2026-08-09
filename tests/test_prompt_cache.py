"""프롬프트 캐시 관측·옵트인 회귀.

왜 관측이 먼저인가: 종전 응답 파서는 usage 의 prompt_tokens/completion_tokens 만 읽어서,
**제공자가 이미 캐싱해 주고 있어도 우리는 알 수 없었다**(비용도 정가로 계산됐다).
config.prices.cache_read 는 선언만 되고 참조가 0건, LLMResult.retries 는 트레이스에
도달하지 않았고, by_call 의 ms 는 롤업으로 옮겨지지 않아 지연이 아무 데도 남지 않았다.
이 파일은 그 네 구멍(캐시 토큰·캐시 단가·재시도·지연)과 prompt_cache_key 옵트인의
안전장치(400 거부 시 자동 제거), 그리고 캐시 프리픽스 안정성을 함께 잠근다.

실행: python3 -m pytest tests/test_prompt_cache.py -q  (stdlib unittest · 의존성 0)
"""
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── 공통 대역 ────────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, obj):
        self._b = json.dumps(obj).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _payload(usage=None):
    return {"choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": usage if usage is not None else {"prompt_tokens": 100, "completion_tokens": 20}}


def _http400(msg: bytes):
    return urllib.error.HTTPError("http://x", 400, "Bad Request", None, io.BytesIO(msg))


class _ClientMixin:
    def setUp(self):
        from prism.llm import LLMClient
        LLMClient._PARAM_ADAPT.clear()          # 프로세스 캐시 격리(테스트 간 오염 방지)

    def _client(self, model="solar-pro2", **kw):
        from prism.config import Config
        from prism.llm import LLMClient
        cfg = Config()                          # 로컬 config.json 오염 방지(Config.load 회피)
        cfg.chat_url = "http://unit.test/v1/chat/completions"
        for k, v in kw.items():
            if k == "cache_read":
                cfg.prices.cache_read = v
            else:
                setattr(cfg, k, v)
        return LLMClient(config=cfg, api_key="test-key", model=model)

    def _run(self, client, responses, system="시스템", user="사용자", tag="t"):
        sent = []
        orig = urllib.request.urlopen

        def fake(req, timeout=None):
            sent.append(json.loads(req.data.decode("utf-8")))
            r = responses[min(len(sent) - 1, len(responses) - 1)]
            if isinstance(r, Exception):
                raise r
            return _Resp(r)

        urllib.request.urlopen = fake
        try:
            obj, res = client.complete_json(system, user, tag=tag)
        finally:
            urllib.request.urlopen = orig
        return sent, obj, res


# ── A. 캐시 토큰 파싱(제공자별 usage 형태) ───────────────────────────────────
class TestUsageParsing(unittest.TestCase):
    def _p(self, usage):
        from prism.llm import parse_cache_tokens
        return parse_cache_tokens(usage)

    def test_openai_prompt_tokens_details(self):
        """OpenAI·Azure·OpenRouter·LiteLLM·vLLM: usage.prompt_tokens_details.cached_tokens"""
        r, w, src = self._p({"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 768}})
        self.assertEqual((r, w), (768, 0))
        self.assertEqual(src, "prompt_tokens_details.cached_tokens")

    def test_openai_cache_write(self):
        """신형 OpenAI: prompt_tokens_details.cache_write_tokens(쓰기)"""
        r, w, src = self._p({"prompt_tokens": 1000,
                             "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 900}})
        self.assertEqual((r, w), (0, 900))
        self.assertIn("cache_write_tokens", src)

    def test_anthropic_shape(self):
        """Anthropic: cache_read_input_tokens / cache_creation_input_tokens"""
        r, w, src = self._p({"prompt_tokens": 1200, "cache_read_input_tokens": 800,
                             "cache_creation_input_tokens": 400})
        self.assertEqual((r, w), (800, 400))
        self.assertIn("cache_read_input_tokens", src)
        self.assertIn("cache_creation_input_tokens", src)

    def test_deepseek_shape(self):
        """DeepSeek: prompt_cache_hit_tokens / prompt_cache_miss_tokens"""
        r, w, src = self._p({"prompt_tokens": 1000, "prompt_cache_hit_tokens": 640,
                             "prompt_cache_miss_tokens": 360})
        self.assertEqual((r, w), (640, 0))
        self.assertEqual(src, "prompt_cache_hit_tokens")

    def test_absent_is_zero(self):
        """캐시 필드가 아예 없으면 0 · source 빈 문자열(= 제공자가 보고 자체를 안 함)."""
        self.assertEqual(self._p({"prompt_tokens": 10, "completion_tokens": 3}), (0, 0, ""))
        self.assertEqual(self._p(None), (0, 0, ""))
        self.assertEqual(self._p("이상한값"), (0, 0, ""))

    def test_reported_but_zero_is_distinguishable(self):
        """'보고는 하는데 매번 미스'와 '보고 자체가 없음'은 원인이 달라 구분돼야 한다."""
        r, w, src = self._p({"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 0}})
        self.assertEqual((r, w), (0, 0))
        self.assertTrue(src.startswith("miss:"), src)

    def test_duplicated_fields_not_summed(self):
        """프록시가 OpenAI 형·Anthropic 형을 동시에 실어도 이중 계상하지 않는다."""
        r, w, _ = self._p({"prompt_tokens": 1000,
                           "prompt_tokens_details": {"cached_tokens": 700},
                           "cache_read_input_tokens": 700})
        self.assertEqual(r, 700)
        self.assertEqual(w, 0)

    def test_result_carries_cache_tokens(self):
        """LLMResult 가 캐시 토큰·출처를 담는다(기본 0 · 기존 시그니처 불변)."""
        from prism.llm import LLMResult
        r = LLMResult("t", 10, 2, 5, 0)                 # 기존 위치인자 호출이 그대로 살아 있어야
        self.assertEqual((r.cache_read_tok, r.cache_write_tok, r.cache_source), (0, 0, ""))


class TestCacheTokensEndToEnd(_ClientMixin, unittest.TestCase):
    def test_call_normalizes_into_result(self):
        c = self._client()
        usage = {"prompt_tokens": 1000, "completion_tokens": 100,
                 "prompt_tokens_details": {"cached_tokens": 800}}
        _sent, _obj, res = self._run(c, [_payload(usage)])
        self.assertEqual(res.cache_read_tok, 800)
        self.assertEqual(res.cache_source, "prompt_tokens_details.cached_tokens")


# ── B. 비용 계산(캐시 단가 · 하위호환) ───────────────────────────────────────
class TestCost(unittest.TestCase):
    def _res(self, **kw):
        from prism.llm import LLMResult
        kw.setdefault("price_in", 1.0)
        kw.setdefault("price_out", 4.0)
        return LLMResult("t", kw.pop("in_tok", 1_000_000), kw.pop("out_tok", 1_000_000), 5, 0, **kw)

    def test_cache_read_none_is_backward_compatible(self):
        """cache_read=None(미설정) → 캐시 토큰이 보고돼도 종전 식과 완전히 동일."""
        base = self._res()
        self.assertAlmostEqual(base.cost_usd, 1.0 + 4.0)
        with_cache = self._res(cache_read_tok=800_000, price_cache_read=None)
        self.assertAlmostEqual(with_cache.cost_usd, base.cost_usd)

    def test_cache_read_price_applied(self):
        """캐시로 읽은 토큰만 캐시 단가 · 나머지 입력은 정가(캐시는 prompt_tokens 의 부분집합)."""
        r = self._res(cache_read_tok=800_000, price_cache_read=0.1)
        # (1M - 0.8M)*1.0 + 0.8M*0.1 + 1M*4.0 = 0.2 + 0.08 + 4.0
        self.assertAlmostEqual(r.cost_usd, 0.2 + 0.08 + 4.0)

    def test_zero_cache_tokens_unchanged(self):
        r = self._res(cache_read_tok=0, price_cache_read=0.1)
        self.assertAlmostEqual(r.cost_usd, 1.0 + 4.0)

    def test_cache_over_input_is_clamped(self):
        """라우터가 캐시>입력을 돌려줘도 음수 비용이 나오지 않는다."""
        r = self._res(in_tok=1_000_000, cache_read_tok=5_000_000, price_cache_read=0.1)
        self.assertAlmostEqual(r.cost_usd, 0.1 + 4.0)
        self.assertGreater(r.cost_usd, 0)

    def test_cache_write_charged_at_full_price(self):
        """캐시 쓰기 프리미엄은 제공자마다 갈려 지금은 정가(의도된 동작 · 주석 참조)."""
        r = self._res(cache_write_tok=1_000_000, price_cache_read=0.1)
        self.assertAlmostEqual(r.cost_usd, 1.0 + 4.0)

    def test_client_injects_configured_cache_price(self):
        from prism.config import Config
        from prism.llm import LLMClient
        cfg = Config()
        cfg.chat_url = "http://unit.test/v1/chat/completions"
        cfg.prices.cache_read = 0.015
        c = LLMClient(config=cfg, api_key="k", model="solar-pro2")
        sent = []
        orig = urllib.request.urlopen

        def fake(req, timeout=None):
            sent.append(req)
            return _Resp(_payload({"prompt_tokens": 1000, "completion_tokens": 10,
                                   "prompt_tokens_details": {"cached_tokens": 900}}))
        urllib.request.urlopen = fake
        try:
            _obj, res = c.complete_json("s", "u", tag="t")
        finally:
            urllib.request.urlopen = orig
        self.assertEqual(res.price_cache_read, 0.015)
        self.assertLess(res.cost_usd, 1000 / 1e6 * cfg.prices.chat_in + 10 / 1e6 * cfg.prices.chat_out)


# ── C. prompt_cache_key 옵트인 + 400 거부 시 자동 제거 ───────────────────────
class TestPromptCacheKey(_ClientMixin, unittest.TestCase):
    def test_off_by_default(self):
        """기본 off: 옵트인 전에는 요청 바디에 prompt_cache_key 가 없다(기존 동작 보존)."""
        sent, _obj, _res = self._run(self._client(), [_payload()])
        self.assertNotIn("prompt_cache_key", sent[0])

    def test_sent_when_enabled(self):
        sent, _obj, _res = self._run(self._client(prompt_cache=True), [_payload()])
        self.assertIn("prompt_cache_key", sent[0])
        self.assertTrue(sent[0]["prompt_cache_key"].startswith("prism-t-"), sent[0]["prompt_cache_key"])

    def test_key_is_prefix_identity_not_content(self):
        """같은 프리픽스(system)면 콘텐츠(user)가 달라도 같은 키 — 아니면 캐시가 안 붙는다."""
        c = self._client(prompt_cache=True)
        s1, _, _ = self._run(c, [_payload()], system="SYS", user="콘텐츠 A", tag="item_intent")
        s2, _, _ = self._run(c, [_payload()], system="SYS", user="콘텐츠 B", tag="item_intent")
        self.assertEqual(s1[0]["prompt_cache_key"], s2[0]["prompt_cache_key"])

    def test_key_splits_on_prefix_change(self):
        """프리픽스를 가르는 축(콜 태그·서비스 사전·모델·reasoning_effort)마다 키가 달라진다."""
        base, _, _ = self._run(self._client(prompt_cache=True), [_payload()],
                               system="SYS", tag="item_intent")
        k0 = base[0]["prompt_cache_key"]
        variants = {
            "tag": self._run(self._client(prompt_cache=True), [_payload()],
                             system="SYS", tag="item_category")[0][0]["prompt_cache_key"],
            "service": self._run(self._client(prompt_cache=True), [_payload()],
                                 system="SYS(뉴스 사전)", tag="item_intent")[0][0]["prompt_cache_key"],
            "model": self._run(self._client("solar-pro3", prompt_cache=True), [_payload()],
                               system="SYS", tag="item_intent")[0][0]["prompt_cache_key"],
            "effort": self._run(self._client(prompt_cache=True, reasoning_effort="high"),
                                [_payload()], system="SYS",
                                tag="item_intent")[0][0]["prompt_cache_key"],
        }
        for name, k in variants.items():
            self.assertNotEqual(k0, k, name)

    def test_key_is_short_and_safe(self):
        """라우터 파라미터로 안전한 길이·문자 집합(태그에 콜론이 섞이는 q:·legal: 대비)."""
        sent, _, _ = self._run(self._client(prompt_cache=True), [_payload()], tag="q:format_val")
        k = sent[0]["prompt_cache_key"]
        self.assertLessEqual(len(k), 64)
        self.assertRegex(k, r"^[A-Za-z0-9_.\-]+$")

    def test_named_400_rejection_drops_key_and_retries(self):
        """라우터가 이름을 짚어 거부하면 즉시 빼고 재시도(협상 메커니즘 · no_response_format 선례)."""
        c = self._client(prompt_cache=True)
        err = _http400(b'{"error":{"message":"Unrecognized request argument supplied: '
                       b'prompt_cache_key","param":"prompt_cache_key"}}')
        sent, obj, res = self._run(c, [err, _payload()])
        self.assertEqual(len(sent), 2)
        self.assertIn("prompt_cache_key", sent[0])
        self.assertNotIn("prompt_cache_key", sent[1])
        self.assertEqual(obj, {"ok": True})
        self.assertEqual(res.retries, 1)

    def test_unnamed_400_also_drops_key(self):
        """이름을 안 짚는 라우터("Extra inputs are not permitted")도 보수적으로 빼고 재시도."""
        c = self._client(prompt_cache=True)
        err = _http400(b'{"error":{"message":"Extra inputs are not permitted"}}')
        sent, obj, _ = self._run(c, [err, _payload()])
        self.assertEqual(len(sent), 2)
        self.assertNotIn("prompt_cache_key", sent[1])
        self.assertEqual(obj, {"ok": True})

    def test_content_filter_400_does_not_drop_key(self):
        """콘텐츠 필터 400 은 파라미터 문제가 아니다 → 헛 재시도·캐시 무력화 없이 즉시 실패."""
        c = self._client(prompt_cache=True)
        err = _http400(b'{"error":{"message":"content policy violation"}}')
        sent, obj, _ = self._run(c, [err])
        self.assertEqual(len(sent), 1)
        self.assertIn("_fail", obj)
        self.assertTrue(c.cache_key_on())

    def test_rejection_shared_across_clients_of_same_model(self):
        """거부 학습은 모델별 프로세스 캐시로 공유 — 배치에서 400 을 반복 낭비하지 않는다."""
        c1 = self._client(prompt_cache=True)
        err = _http400(b'{"error":{"message":"unknown parameter prompt_cache_key"}}')
        self._run(c1, [err, _payload()])
        c2 = self._client(prompt_cache=True)                 # 배치의 다음 아이템
        sent, _, _ = self._run(c2, [_payload()])
        self.assertEqual(len(sent), 1)
        self.assertNotIn("prompt_cache_key", sent[0])
        c3 = self._client("gpt-5.4", prompt_cache=True)      # 다른 모델은 무영향
        sent3, _, _ = self._run(c3, [_payload()])
        self.assertIn("prompt_cache_key", sent3[0])

    def test_env_switch(self):
        from prism import config as C
        self.assertTrue(C._envbool("1"))
        self.assertTrue(C._envbool("true"))
        for off in ("0", "false", "off", "no", "", "  "):
            self.assertFalse(C._envbool(off), off)


# ── D. 하네스 트레이스(캐시·재시도·wall-clock) ──────────────────────────────
class _FakeRes:
    def __init__(self, tag, ms, cache_read=0, cache_write=0, retries=0, src=""):
        self.tag, self.latency_ms = tag, ms
        self.in_tok, self.out_tok = 100, 20
        self.cache_read_tok, self.cache_write_tok, self.retries = cache_read, cache_write, retries
        self.cache_source = src
        self.cost_usd = 0.001
        self.fail_kind = None


class TestHarnessTrace(unittest.TestCase):
    def _assemble(self, results):
        from prism import harness as H
        from prism.schema import Content, QualityMeta, LegalMeta, Trace
        ctx = H.HCtx(content=Content.from_dict({"displayServiceName": "뉴스", "title": "t", "body": "b"}),
                     llm=None, methodology=H.Methodology(), legal_meta=LegalMeta(enabled=False),
                     trace=Trace(), t0=time.time() - 0.5)
        ctx.routing = H.R.dispatch(ctx.content)
        ctx.qm = QualityMeta(finalGrade="G", reasons=[])
        ctx.results = results
        return H._assemble(ctx)["trace"]

    def test_cache_tokens_and_retries_in_trace(self):
        tr = self._assemble([_FakeRes("item_summary", 300, cache_read=700, retries=2),
                             _FakeRes("item_entities", 400, cache_read=650, cache_write=10)])
        self.assertEqual(tr["tokens"]["cache_read"], 1350)
        self.assertEqual(tr["tokens"]["cache_write"], 10)
        self.assertEqual(tr["by_call"]["item_summary"]["retries"], 2)
        self.assertEqual(tr["by_call"]["item_summary"]["cache_read"], 700)
        self.assertEqual(tr["by_call"]["item_entities"]["ms"], 400)

    def test_wall_added_without_changing_total(self):
        """total(콜 지연 합)의 의미·값은 계약이라 그대로 · wall 은 실제 소요를 새 키로 추가."""
        tr = self._assemble([_FakeRes("a", 500), _FakeRes("b", 500)])
        self.assertEqual(tr["latency_ms"]["total"], 1000)     # 종전 계약(다운스트림 abtest)
        self.assertIn("wall", tr["latency_ms"])
        # 두 콜이 병렬이었다면 wall < total — 이 차이가 병렬화 효과다(total 로는 0 으로 보인다)
        self.assertLess(tr["latency_ms"]["wall"], tr["latency_ms"]["total"])

    def test_cache_source_surfaced_for_diagnosis(self):
        """캐시 0 의 원인 구분(라우터가 필드를 안 줌 vs 주는데 미스)이 트레이스에 남는다."""
        tr = self._assemble([_FakeRes("item_intent", 100, src="miss:prompt_cache_hit_tokens"),
                             _FakeRes("item_category", 100)])
        self.assertEqual(tr["by_call"]["item_intent"]["cache_src"], "miss:prompt_cache_hit_tokens")
        self.assertNotIn("cache_src", tr["by_call"]["item_category"])

    def test_missing_attrs_are_tolerated(self):
        """캐시 필드가 없는 옛 대역·폴백 결과 객체가 섞여도 트레이스 조립이 죽지 않는다."""
        class _Old:
            tag, latency_ms, in_tok, out_tok, cost_usd, fail_kind = "old", 10, 1, 1, 0.0, None
        tr = self._assemble([_Old()])
        self.assertEqual(tr["by_call"]["old"]["cache_read"], 0)
        self.assertEqual(tr["by_call"]["old"]["retries"], 0)

    def test_mock_run_has_wall_key(self):
        from prism import harness as H
        from prism.llm import LLMClient
        out = H.run({"displayServiceName": "뉴스", "title": "지연 관측", "subtitle": "",
                     "body": "wall-clock 지연이 트레이스에 남는지 확인하는 충분한 길이의 본문입니다."},
                    LLMClient(mock=True), H.Methodology())
        self.assertIn("wall", (out["trace"] or {})["latency_ms"])
        self.assertIn("cache_read", out["trace"]["tokens"])


# ── E. 비용 롤업(신규 키 누적 · 구 리포트 하위호환) ─────────────────────────
def _trace(ms=120, cache_read=0, retries=0, wall=90):
    return {"cost_usd": 0.01, "model": "gpt-x",
            "tokens": {"in": 100, "out": 50, "cache_read": cache_read, "cache_write": 4},
            "latency_ms": {"total": ms, "wall": wall},
            "by_call": {"summary": {"n": 1, "cost": 0.004, "in": 60, "out": 30, "ms": ms,
                                    "cache_read": cache_read, "cache_write": 4,
                                    "retries": retries}}}


class TestRollup(unittest.TestCase):
    def _serve(self):
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve

    def test_new_keys_accumulate(self):
        serve = self._serve()
        serve._log_cost_rollup(_trace(ms=120, cache_read=700, retries=1), team=None)
        serve._log_cost_rollup(_trace(ms=80, cache_read=500, retries=0), team=None)
        d = serve.cost_rollup_data(None, days=7)
        c = {x["call"]: x for x in d["by_call"]}["summary"]
        self.assertEqual(c["ms"], 200)                   # 지연이 원장에 영속된다(종전엔 버려졌다)
        self.assertEqual(c["cache_read"], 1200)
        self.assertEqual(c["cache_write"], 8)
        self.assertEqual(c["retries"], 1)
        self.assertEqual(d["total"]["cache_read"], 1200)
        self.assertEqual(d["total"]["wall_ms"], 180)
        self.assertEqual(d["total"]["n"], 2)             # 기존 키 불변

    def test_old_report_without_new_keys(self):
        """새 키가 없는 구 리포트에 이어 적재해도 깨지지 않고 그날부터 쌓인다."""
        serve = self._serve()
        from prism.store import day_key
        old = {"days": {day_key(): {"cost": 0.5, "n": 3, "in": 10, "out": 5,
                                    "models": {"gpt-x": {"cost": 0.5, "n": 3}},
                                    "calls": {"summary": {"cost": 0.5, "n": 3, "in": 10, "out": 5}}}}}
        serve._report_save("cost_rollup", old, None)
        d = serve.cost_rollup_data(None, days=7)         # 조회만으로도 안 깨져야
        c = {x["call"]: x for x in d["by_call"]}["summary"]
        self.assertEqual((c["ms"], c["cache_read"], c["retries"]), (0, 0, 0))
        self.assertEqual(d["total"]["cache_read"], 0)
        serve._log_cost_rollup(_trace(ms=50, cache_read=100), team=None)
        d2 = serve.cost_rollup_data(None, days=7)
        c2 = {x["call"]: x for x in d2["by_call"]}["summary"]
        self.assertEqual(c2["n"], 4)                     # 구 카운터 위에 누적
        self.assertEqual((c2["ms"], c2["cache_read"]), (50, 100))

    def test_trace_without_new_keys_is_noop(self):
        """캐시·지연 키가 없는 트레이스(구 실행 경로)도 그대로 기록된다."""
        serve = self._serve()
        serve._log_cost_rollup({"cost_usd": 0.02, "model": "m", "tokens": {"in": 1, "out": 1},
                                "by_call": {"quality": {"n": 1, "cost": 0.02, "in": 1, "out": 1}}},
                               team=None)
        d = serve.cost_rollup_data(None, days=7)
        self.assertEqual(d["total"]["n"], 1)
        self.assertEqual({x["call"]: x for x in d["by_call"]}["quality"]["ms"], 0)


# ── F. 프리픽스 안정성 가드({LEARNED} 는 반드시 최말단) ─────────────────────
class TestPrefixStability(unittest.TestCase):
    """캐시는 접두 일치로만 붙는다. 콘텐츠·검수 누적에 따라 변하는 {LEARNED} 가 프리픽스
    중간으로 옮겨가면 그 뒤 전부(사전·예시·자가검증 = 시스템 프롬프트의 대부분)가 매번
    무효화돼 캐시가 전멸한다. 계열 래퍼 5종 전부를 잠근다."""

    LEARNED = "\n\n[학습 보정 · extract] <검수_피드백>\n표기를 정규화할 것\n</검수_피드백>"

    def test_wrapper_templates_end_with_learned(self):
        from prism import meta_prompts as MP
        for fam in MP.FAMILIES:
            t = MP.FAMILY_WRAPPER_DEFAULT[fam]
            self.assertTrue(t.rstrip().endswith("{LEARNED}"), fam)
            self.assertEqual(t.count("{LEARNED}"), 1, fam)

    def test_call_system_puts_learned_at_tail(self):
        from prism import meta_prompts as MP
        models = {"gpt": "gpt-5.4", "gemini": "gemini-2.5-pro", "claude": "claude-opus-4-8",
                  "solar": "solar-pro2", "default": "some-unknown-model"}
        for fam, model in models.items():
            for call in MP.CALLS:
                s = MP.call_system(model, call, "뉴스", self.LEARNED)
                self.assertTrue(s.endswith(self.LEARNED), f"{fam}/{call}")
                # 사전·예시 같은 큰 조각은 학습 보정 '앞'에 있어야 캐시 프리픽스가 안정적이다
                self.assertLess(s.index(self.LEARNED), len(s) - len(self.LEARNED) + 1)

    def test_item_system_puts_learned_at_tail(self):
        from prism import meta_prompts as MP
        for model in ("gpt-5.4", "gemini-2.5-pro", "claude-opus-4-8", "solar-pro2", "zzz"):
            s = MP.item_system(model, "코어", "인텐트", "IAB", self.LEARNED)
            self.assertTrue(s.endswith(self.LEARNED), model)

    def test_prompts_learned_block_is_suffix(self):
        """prompts._learned 산출이 조립된 system 문자열 끝부분에 실제로 위치하는지(통합 경로)."""
        from prism import prompts as P
        from prism.schema import Content
        P.LEARNED["extract"] = "리드문은 1문장으로"
        P.LEARNED["analyze"] = "인텐트는 근거가 있을 때만"
        try:
            c = Content.from_dict({"displayServiceName": "뉴스", "title": "t", "body": "b"})
            for model in ("gpt-5.4", "gemini-2.5-pro", "claude-opus-4-8", "solar-pro2"):
                s = P.call_system(c, "intent", model)
                self.assertIn("리드문은 1문장으로", s)
                tail = s[-1200:]
                self.assertIn("인텐트는 근거가 있을 때만", tail, model)
                # 사전(가장 큰 고정 블록)은 학습 보정보다 앞 = 프리픽스가 안정적
                self.assertLess(s.index("[사전 · 인텐트 분류값"), s.index("리드문은 1문장으로"), model)
        finally:
            P.LEARNED["extract"] = P.LEARNED["analyze"] = ""

    def test_studio_override_changes_key_instead_of_poisoning_it(self):
        """위험 ③: 래퍼 중간 편집은 캐시를 무효화한다(막을 수 없음). 다만 키가 system 본문
        해시라 편집 즉시 '새 키'가 되어, 옛 키에 새 프리픽스가 얹히는 혼선은 없다."""
        from prism.config import Config
        from prism.llm import LLMClient
        from prism import meta_prompts as MP
        cfg = Config()
        cfg.chat_url = "http://unit.test/v1/chat/completions"
        cfg.prompt_cache = True
        c = LLMClient(config=cfg, api_key="k", model="solar-pro2")
        s1 = MP.call_system("solar-pro2", "intent", "뉴스", "")
        MP.WRAPPER_OVERRIDES["solar"] = "수정된 래퍼\n{ROLE}\n{RULES}\n{EXAMPLES}\n{SCHEMA}\n{SELF_CHECK}{LEARNED}"
        try:
            s2 = MP.call_system("solar-pro2", "intent", "뉴스", "")
        finally:
            MP.WRAPPER_OVERRIDES.pop("solar", None)
        self.assertNotEqual(s1, s2)
        self.assertNotEqual(c._cache_key(s1, "item_intent", True),
                            c._cache_key(s2, "item_intent", True))


if __name__ == "__main__":
    unittest.main()
