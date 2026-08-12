"""코드 감사 2026-08-11 · LLM 파이프라인 회귀 테스트 (L1~L10 · P1~P2).

각 항목은 감사 재현 스크립트(scratchpad/p1~p9)의 실패 시나리오를 고정한 것이다.
실 API 는 호출하지 않는다 — HTTP 계층(urllib.request.urlopen)만 가짜로 바꾼다.

실행: PRISM_DB=$(mktemp -d)/t.db ENTDICT_ENRICH=0 python3 -m pytest tests/test_audit_llm.py -q
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── 공통: HTTP 계층 가짜 ────────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, body):
        self._b = body.encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _envelope(text, in_tok=3000, out_tok=200, extra_usage=None):
    usage = {"prompt_tokens": in_tok, "completion_tokens": out_tok}
    usage.update(extra_usage or {})
    return json.dumps({"choices": [{"message": {"content": text}}], "usage": usage})


class LLMFakeBase(unittest.TestCase):
    """urllib.urlopen 과 time.sleep 을 가짜로 바꾸는 공통 셋업(실 API·실 대기 없음)."""

    def _client(self, model="solar-pro3", **cfg_kw):
        import prism.llm as L
        from prism.config import Config
        cfg = Config()
        cfg.chat_url = "https://fake/v1/chat/completions"
        cfg.api_key = "k"
        for k, v in cfg_kw.items():
            setattr(cfg, k, v)
        return L.LLMClient(config=cfg, api_key="k", model=model)

    def _patch_http(self, responder):
        import prism.llm as L
        o_open, o_sleep = L.urllib.request.urlopen, L.time.sleep
        slept = []
        L.urllib.request.urlopen = responder
        L.time.sleep = lambda s: slept.append(s)
        self.addCleanup(lambda: (setattr(L.urllib.request, "urlopen", o_open),
                                 setattr(L.time, "sleep", o_sleep)))
        return slept

    def _seq_responder(self, texts, sink=None, in_tok=3000, out_tok=200):
        box = list(texts)

        def fake(req, timeout=None):
            body = json.loads(req.data.decode())
            if sink is not None:
                sink.append(body)
            t = box.pop(0) if box else texts[-1]
            return _FakeResp(_envelope(t, in_tok, out_tok))
        return fake


# ── [L1] promptstore 콜드스타트 경합·손상 파일 ──────────────────────────────
class TestPromptStoreConcurrentSeed(unittest.TestCase):
    """12스레드 동시 첫 호출에서 quality.json 을 truncate 해 JSONDecodeError 가 나던 문제.
    (재현 p9_coldstart: 20회 중 12회 예외 · 손상 파일은 영구 방치)"""

    def _tmp_store(self):
        from prism import promptstore as PS
        tmp = tempfile.mkdtemp()
        o_dir, o_path, o_seeded = PS.PROMPTS_DIR, PS.QUALITY_PATH, set(PS._SEEDED)
        PS.PROMPTS_DIR = tmp
        PS.QUALITY_PATH = os.path.join(tmp, "quality.json")
        PS._SEEDED.clear()

        def restore():
            PS.PROMPTS_DIR, PS.QUALITY_PATH = o_dir, o_path
            PS._SEEDED.clear()
            PS._SEEDED.update(o_seeded)
            shutil.rmtree(tmp, ignore_errors=True)
        self.addCleanup(restore)
        return PS, tmp

    def test_cold_start_12_threads_no_error(self):
        PS, tmp = self._tmp_store()
        errs, bodies = [], []
        lock = threading.Lock()

        def work():
            try:
                b = PS.get()
                with lock:
                    bodies.append(b)
            except Exception as e:                      # noqa: BLE001 (무엇이든 실패면 회귀)
                with lock:
                    errs.append(f"{type(e).__name__}: {e}")
        ts = [threading.Thread(target=work) for _ in range(12)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errs, [])
        self.assertEqual(len(bodies), 12)
        with open(PS.QUALITY_PATH, encoding="utf-8") as f:      # 파일이 온전한 JSON 으로 남는다
            self.assertIn("versions", json.load(f))
        # 임시파일이 남지 않는다(원자 교체 후 정리)
        self.assertEqual([f for f in os.listdir(tmp) if f.endswith(".tmp")], [])

    def test_truncated_file_is_recovered_and_backed_up(self):
        PS, tmp = self._tmp_store()
        with open(PS.QUALITY_PATH, "w", encoding="utf-8") as f:
            f.write('{"active": "v31", "versi')           # 쓰기 도중 죽어 절단된 파일
        body = PS.get()                                    # 종전: JSONDecodeError 를 영구히 던짐
        self.assertIn("intro", body)
        self.assertTrue(os.path.exists(PS.QUALITY_PATH + ".bad"))   # 원인 분석용 보존
        self.assertEqual(PS.active_name(), "v31")

    def test_deleted_file_is_reseeded_after_first_seed(self):
        """기동 1회 시드로 바꾼 뒤에도 파일이 사라지면 그 자리에서 복구된다."""
        PS, tmp = self._tmp_store()
        PS.get()
        os.unlink(PS.QUALITY_PATH)
        self.assertIn("intro", PS.get())

    def test_seed_runs_once_per_process(self):
        """ensure_seeded 가 품질 콜마다 돌지 않는다(핫패스 제거 · 경합 원천 차단)."""
        PS, tmp = self._tmp_store()
        PS.get()
        calls = []
        o_ensure = PS.ensure_seeded
        PS.ensure_seeded = lambda: calls.append(1)
        self.addCleanup(lambda: setattr(PS, "ensure_seeded", o_ensure))
        for _ in range(5):
            PS.get()
        self.assertEqual(calls, [])

    def test_user_version_survives_reseed(self):
        PS, tmp = self._tmp_store()
        PS.new_from("v31", "v33-mytuned")
        data = json.load(open(PS.QUALITY_PATH, encoding="utf-8"))
        data["seed_stamp"] = "stale"                       # 시드 문구 개정 배포 직후 상황
        with open(PS.QUALITY_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        PS._SEEDED.clear()
        PS.ensure_seeded()
        self.assertIn("v33-mytuned", dict(PS.list_versions()))


# ── [L2] 재시도로 지불한 토큰이 원장에 남는다 ───────────────────────────────
class TestRetryTokenLedger(LLMFakeBase):
    """파싱·빈응답 재시도는 HTTP 200 을 받고 버리는 것이라 매회 과금된다.
    (재현 p5_ledger: 최종 실패 100% · 재시도 후 성공 80% 미기록)"""

    def test_final_failure_records_paid_tokens(self):
        llm = self._client()
        llm.cfg.retry.max_format_retries = 2
        calls = []
        self._patch_http(self._seq_responder(["깨진출력"] * 5, sink=calls))
        obj, res = llm.complete_json("sys", "user", tag="quality")
        self.assertEqual(obj["_fail_kind"], "parse_empty")
        self.assertEqual(len(calls), 3)                       # 최초 + 형식 재시도 2
        self.assertEqual((res.in_tok, res.out_tok), (9000, 600))   # 종전 (0, 0)
        self.assertAlmostEqual(res.cost_usd, 9000 / 1e6 * 0.15 + 600 / 1e6 * 0.60, places=9)
        self.assertEqual((res.retry_in_tok, res.retry_out_tok), (9000, 600))

    def test_success_after_retry_sums_discarded_call(self):
        llm = self._client()
        self._patch_http(self._seq_responder(["깨진출력", '{"finalGrade":"G","reasons":[]}']))
        obj, res = llm.complete_json("sys", "user", tag="quality")
        self.assertEqual(obj["finalGrade"], "G")
        self.assertEqual((res.in_tok, res.out_tok), (6000, 400))   # 종전 (3000, 200)
        self.assertEqual(res.retries, 1)

    def test_empty_completion_tokens_are_billed(self):
        """빈 완성은 _call 안에서 예외가 나 결과가 사라졌다 — 예외에 결과를 실어 보존한다."""
        llm = self._client()
        self._patch_http(self._seq_responder(["", '{"summary":"정상"}']))
        obj, res = llm.complete_json("sys", "user", tag="item_summary")
        self.assertEqual(obj["summary"], "정상")
        self.assertEqual(res.in_tok, 6000)

    def test_http_failure_after_paid_attempt_keeps_tokens(self):
        """형식 실패로 한 번 과금된 뒤 402 로 끝나도 그 지출은 원장에 남는다."""
        import prism.llm as L
        llm = self._client()
        state = {"n": 0}

        def fake(req, timeout=None):
            state["n"] += 1
            if state["n"] == 1:
                return _FakeResp(_envelope("깨진출력"))
            raise L.urllib.error.HTTPError("u", 402, "insufficient_balance", {}, None)
        self._patch_http(fake)
        obj, res = llm.complete_json("sys", "user", tag="quality")
        self.assertEqual(res.fail_kind, "billing")
        self.assertEqual((res.in_tok, res.out_tok), (3000, 200))
        self.assertGreater(res.cost_usd, 0.0)

    def test_no_retry_keeps_plain_tokens(self):
        """대조군: 1회에 성공하면 종전과 완전히 동일(누적 필드 0)."""
        llm = self._client()
        self._patch_http(self._seq_responder(['{"finalGrade":"G","reasons":[]}']))
        _obj, res = llm.complete_json("sys", "user", tag="quality")
        self.assertEqual((res.in_tok, res.out_tok, res.retry_in_tok), (3000, 200, 0))


class TestBatchBudgetCountsFailures(unittest.TestCase):
    """[L2] 일괄 실행 예산 상한: 실패 건도 이미 지불한 비용을 누적해야 상한이 작동한다."""

    def _serve_with_rows(self, n):
        from prism import serve
        from prism.store import Store, content_hash
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        serve._INGEST_STATE.clear()
        self.addCleanup(serve._INGEST_STATE.clear)
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)
        c = st._conn()
        for i in range(n):
            content = {"displayServiceName": "뉴스", "title": "t%d" % i, "subtitle": "", "body": "b%d" % i}
            ch = content_hash(content)
            payload = {"quality_meta": {"finalGrade": "G"}, "item_meta": {"summary": "s"},
                       "trace": {"model": "m"}, "content_ref": dict(content)}
            c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,payload,created_at) "
                      "VALUES(?,?,?,?,?,?)", (ch, "뉴스", "t%d" % i, "G", json.dumps(payload), time.time() + i))
        c.commit()
        return serve

    def _isolate_cfg(self, payload):
        from prism import config as C
        p = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        orig = C.DEFAULT_CONFIG_PATH
        C.DEFAULT_CONFIG_PATH = p
        self.addCleanup(lambda: setattr(C, "DEFAULT_CONFIG_PATH", orig))

    def _stub(self, serve, calls, out):
        orig = serve.rerun_content
        serve.rerun_content = lambda ch, model, team=None, row=None, **kw: (calls.append(ch) or out)
        self.addCleanup(lambda: setattr(serve, "rerun_content", orig))

    def test_call_failures_consume_budget(self):
        """콜이 전량 실패한 건(에러 키 없음 · trace.fails)도 이제 실지출이 실려 상한이 걸린다.
        종전에는 llm._fail 이 토큰 0 을 실어 cost_usd=0 → 상한이 무력했다(402 폭주 시나리오)."""
        serve = self._serve_with_rows(6)
        self._isolate_cfg({"batch_budget_usd": 0.03})
        calls = []
        self._stub(serve, calls, {"output": {"item_meta": {}, "quality_meta": {},
                                             "trace": {"cost_usd": 0.02,
                                                       "fails": [{"tag": "quality",
                                                                  "kind": "parse_empty"}]}}})
        r = serve.rerun_all("m2", scope="all")
        self.assertTrue(r["budget_stop"])
        self.assertEqual(len(calls), 2)              # 0.02×2 ≥ 0.03 에서 중단
        self.assertAlmostEqual(r["spent_usd"], 0.04, places=6)

    def test_error_rows_also_consume_budget(self):
        """error 로 집계되는 건이 비용을 실어 오면 그것도 예산에 더한다(누적을 분기 밖으로)."""
        serve = self._serve_with_rows(6)
        self._isolate_cfg({"batch_budget_usd": 0.03})
        calls = []
        self._stub(serve, calls, {"error": "모델 호출 불가",
                                  "output": {"trace": {"cost_usd": 0.02}}})
        r = serve.rerun_all("m2", scope="all")
        self.assertEqual((r["done"], r["failed"]), (0, 2))
        self.assertTrue(r["budget_stop"])            # 종전: else 분기라 spent 가 0 → 전건 실행
        self.assertAlmostEqual(r["spent_usd"], 0.04, places=6)


# ── [L3] 계약 키 부재를 실패로 승격 ─────────────────────────────────────────
class _StubLLM:
    """complete_json 을 태그별 고정 응답으로 대체하는 최소 대역."""

    def __init__(self, by_tag, model="solar-pro3"):
        self.by_tag = by_tag
        self.model = model
        self.mock = False
        self.calls = []

    def complete_json(self, system, user, tag=""):
        from prism.llm import LLMResult
        self.calls.append((tag, user))
        obj = self.by_tag.get(tag, {})
        if callable(obj):
            obj = obj(len([c for c in self.calls if c[0] == tag]))
        return dict(obj), LLMResult(json.dumps(obj), 10, 10, 1, 0, tag=tag)


class TestContractKeyIsFailure(unittest.TestCase):
    """파싱은 됐지만 계약 키가 없는 응답이 빈 메타 + finalGrade=G + review=auto 로
    저장되던 사각지대(재현 p3_silent_empty D · p2_failopen B)."""

    def _content(self):
        from prism.schema import Content
        return Content(displayServiceName="뉴스", title="삼성전자 노사 협상 결렬", body="본문" * 20)

    def test_quality_missing_key_holds_yellow(self):
        from prism import agents as A
        from prism import routing as R
        c = self._content()
        llm = _StubLLM({"quality": {"verdict": "R", "note": "광고"}})
        qm, _res = A.run_quality(llm, c, R.dispatch(c))
        self.assertEqual(qm.finalGrade, "")           # 종전: 기본값 "G" 로 자동 통과
        self.assertEqual(qm.review, "yellow")
        self.assertIn("finalGrade", qm.review_reason)

    def test_quality_normal_response_unchanged(self):
        from prism import agents as A
        from prism import routing as R
        c = self._content()
        llm = _StubLLM({"quality": {"finalGrade": "R", "reasons": ["ad"]}})
        qm, _res = A.run_quality(llm, c, R.dispatch(c))
        self.assertEqual((qm.finalGrade, qm.reasons, qm.review), ("R", ["ad"], "auto"))

    def test_item_summary_missing_key_marks_call_failed(self):
        from prism import agents as A
        c = self._content()
        llm = _StubLLM({"item_summary": {"lead": "노사 협상 결렬을 전한다"}})
        im, results = A.run_item(llm, c)
        self.assertEqual(im.summary, "")
        kinds = [getattr(r, "fail_kind", None) for r in results if hasattr(r, "fail_kind")]
        self.assertIn("contract_miss", kinds)        # 하네스 yellow 가드에 태워진다
        self.assertEqual(len(llm.calls), 1)          # 단락 차단은 그대로

    def test_empty_summary_is_not_contract_failure(self):
        """빈 문자열은 '생성 불가' 라는 정당한 차단 신호 — 실패로 보지 않는다."""
        from prism import agents as A
        c = self._content()
        llm = _StubLLM({"item_summary": {"summary": ""}})
        im, results = A.run_item(llm, c)
        self.assertEqual(im.summary, "")
        self.assertEqual([getattr(r, "fail_kind", None) for r in results
                          if hasattr(r, "fail_kind")], [None])

    def test_contract_miss_holds_auto_g_in_harness(self):
        """종단: 계약 키 없는 응답 → review=yellow(사람 검수)."""
        from prism.harness import _assemble, HCtx, Methodology
        from prism.llm import LLMResult
        from prism import routing as R
        from prism.schema import ItemMeta, LegalMeta, QualityMeta, Trace
        c = self._content()
        ctx = HCtx(content=c, llm=None, methodology=Methodology())
        ctx.routing = R.dispatch(c)
        ctx.legal_meta = LegalMeta()
        ctx.qm = QualityMeta(finalGrade="G", reasons=[])
        ctx.item_meta = ItemMeta(summary="", entities=[], intent=[], content_category=[])
        ctx.trace = Trace()
        r = LLMResult("", 10, 10, 1, 0, tag="item_summary", fail_kind="contract_miss")
        ctx.results = [r]
        out = _assemble(ctx)
        self.assertEqual(out["quality_meta"]["review"], "yellow")
        self.assertEqual(out["trace"]["fails"][0]["kind"], "contract_miss")

    def test_single_key_wrapping_is_unwrapped(self):
        """보강: {"result": {...}} 래핑은 복구한다 — 이 경우는 보류가 아니라 정상 처리."""
        from prism.llm import _parse_json
        self.assertEqual(_parse_json('{"result":{"finalGrade":"R","reasons":["ad"]}}'),
                         {"finalGrade": "R", "reasons": ["ad"]})
        self.assertEqual(_parse_json('{"summary":"한 줄"}'), {"summary": "한 줄"})   # 정상 불변
        self.assertEqual(_parse_json('{"entities":["a"]}'), {"entities": ["a"]})


# ── [L4] 비용 등급 분모 ─────────────────────────────────────────────────────
class TestTierDenominator(unittest.TestCase):
    """성공·실패가 같은 날 섞이면 실패 n 이 분모에 남아 고비용 모델의 등급이 사라지던 문제."""

    def test_mixed_day_uses_billed_count(self):
        from prism import modelmeta as MM
        rep = {"days": {"2026-08-10": {"models": {
            "claude-opus-5": {"n": 1000, "n_billed": 400, "cost": round(400 * 0.0067, 6)}}}}}
        t = MM.tiers_from_cost(rep)["claude-opus-5"]
        self.assertEqual(t["tier"], "high")            # 종전: '' (avg 0.00268 로 희석)
        self.assertAlmostEqual(t["avg_usd"], 0.0067, places=5)
        self.assertEqual((t["n"], t["n_billed"]), (1000, 400))

    def test_legacy_rollup_without_new_key(self):
        """구 원장(n_billed 없음)은 종전 계산 그대로 — 하위호환."""
        from prism import modelmeta as MM
        rep = {"days": {"2026-08-01": {"models": {"solar-pro3": {"n": 100, "cost": 0.05}}}}}
        t = MM.tiers_from_cost(rep)["solar-pro3"]
        self.assertEqual((t["avg_usd"], t["n"]), (0.0005, 100))
        self.assertEqual(t["tier"], "low")

    def test_zero_cost_day_still_excluded(self):
        from prism import modelmeta as MM
        rep = {"days": {"2026-08-01": {"models": {"m": {"n": 600, "cost": 0.0}},},
                        "2026-08-02": {"models": {"m": {"n": 40, "n_billed": 40, "cost": 0.4}}}}}
        t = MM.tiers_from_cost(rep)["m"]
        self.assertEqual((t["n"], t["n_billed"]), (40, 40))
        self.assertEqual(t["tier"], "high")


# ── [L5] 전량 드롭 재요청은 요청을 바꿔서 보낸다 ────────────────────────────
class TestDictionaryRetryDiffers(unittest.TestCase):
    def test_category_retry_prompt_differs(self):
        from prism import agents as A
        from prism.schema import Content
        c = Content(displayServiceName="뉴스", title="t", body="본문" * 20)
        llm = _StubLLM({"item_summary": {"summary": "한 줄 요약"},
                        "item_entities": {"entities": ["삼성전자"]},
                        "item_intent": {"intent": ["없는분류값X"]},
                        "item_category": {"content_category": ["No Such / Tier"]}})
        _im, _res = A.run_item(llm, c)
        cats = [u for tag, u in llm.calls if tag == "item_category"]
        ints = [u for tag, u in llm.calls if tag == "item_intent"]
        self.assertEqual((len(cats), len(ints)), (2, 2))
        self.assertNotEqual(cats[0], cats[1])          # 종전: 바이트 단위로 동일
        self.assertNotEqual(ints[0], ints[1])
        self.assertIn("재요청", cats[1])
        self.assertIn("No Such / Tier", cats[1])       # 실패 값 명시
        self.assertIn("없는분류값X", ints[1])


# ── [L6] 형식 실패 재시도 분리 ──────────────────────────────────────────────
class TestFormatRetryPolicy(LLMFakeBase):
    def test_format_retry_capped_and_no_backoff(self):
        llm = self._client()
        calls = []
        slept = self._patch_http(self._seq_responder(["설명입니다 {참고} 없음"] * 6, sink=calls))
        _obj, res = llm.complete_json("sys", "user", tag="item_summary")
        self.assertEqual(len(calls), 2)                # 종전 5회(max_retries=4)
        self.assertEqual(slept, [])                    # 형식 실패에 백오프 대기 없음
        self.assertEqual(res.fail_kind, "parse_empty")

    def test_format_retry_appends_hint(self):
        llm = self._client()
        calls = []
        self._patch_http(self._seq_responder(["깨진출력", '{"summary":"ok"}'], sink=calls))
        obj, _res = llm.complete_json("sys", "user", tag="item_summary")
        self.assertEqual(obj["summary"], "ok")
        first, second = [c["messages"][1]["content"] for c in calls]
        self.assertNotEqual(first, second)
        self.assertIn("JSON", second)
        self.assertEqual(first, "user")                # 첫 요청은 그대로(프리픽스 영향 없음)
        self.assertEqual([c["messages"][0]["content"] for c in calls], ["sys", "sys"])

    def test_network_retry_policy_unchanged(self):
        """네트워크·429 재시도는 종전대로 max_retries 회 · 백오프 유지."""
        import prism.llm as L
        llm = self._client()
        state = {"n": 0}

        def fake(req, timeout=None):
            state["n"] += 1
            raise L.urllib.error.HTTPError("u", 503, "busy", {}, None)
        slept = self._patch_http(fake)
        _obj, res = llm.complete_json("sys", "user", tag="quality")
        self.assertEqual(state["n"], 5)                # 최초 + max_retries(4)
        self.assertEqual(len(slept), 4)
        self.assertEqual(res.retries, 4)


# ── [L7] 유해 미탐률 분모 ───────────────────────────────────────────────────
def _row(expected):
    return {"content": {"displayServiceName": "뉴스", "title": "t", "body": "b"},
            "expected": expected}


def _out(grade="G", reasons=None, review="auto"):
    return {"quality_meta": {"finalGrade": grade, "reasons": reasons or [], "review": review},
            "item_meta": {"summary": "s", "intent": []},
            "trace": {"cost_usd": 0.0, "tokens": {"in": 0, "out": 0}, "fallbacks": []}}


class TestHarmMissDenominator(unittest.TestCase):
    """분모가 전체 행이라 '골든에 정상 건만 늘려도 개선'으로 보이던 문제(재현 p7 케이스 I)."""

    def test_rate_is_one_minus_recall_of_r(self):
        from prism import abtest
        rows = [_row({"finalGrade": "R", "reasons": ["adult"]}) for _ in range(10)]
        rows += [_row({"finalGrade": "G", "reasons": []}) for _ in range(990)]
        outs = [_out("G") for _ in range(10)] + [_out("G") for _ in range(990)]
        m = abtest.score(rows, outs)
        self.assertEqual(m["harm_miss_rate"], 1.0)     # 기대 R 10건 전량 놓침 = 100%
        self.assertEqual(m["harm_miss_share"], 0.01)   # 종전 값(전체 대비)은 병기로 보존
        self.assertEqual(m["harm_expected_n"], 10)

    def test_adding_normal_rows_does_not_improve_metric(self):
        from prism import abtest
        base_rows = [_row({"finalGrade": "R", "reasons": ["adult"]}) for _ in range(10)]
        base_outs = [_out("G") for _ in range(10)]
        m1 = abtest.score(base_rows, base_outs)
        m2 = abtest.score(base_rows + [_row({"finalGrade": "G", "reasons": []})] * 1000,
                          base_outs + [_out("G")] * 1000)
        self.assertEqual(m1["harm_miss_rate"], m2["harm_miss_rate"])   # 종전: 0.5 → 0.005
        self.assertLess(m2["harm_miss_share"], m1["harm_miss_share"])

    def test_no_expected_r_is_none(self):
        from prism import abtest
        m = abtest.score([_row({"finalGrade": "G", "reasons": []})], [_out("G")])
        self.assertIsNone(m["harm_miss_rate"])         # 0% 로 표기하면 '완벽'으로 오독된다
        self.assertEqual(m["harm_expected_n"], 0)

    def test_ab_winner_handles_none(self):
        from prism import abtest
        a = {"grade_accuracy": 0.9, "harm_miss_rate": None}
        b = {"grade_accuracy": 0.9, "harm_miss_rate": 0.2}
        self.assertEqual((a["grade_accuracy"], -(a.get("harm_miss_rate") or 0.0)) >
                         (b["grade_accuracy"], -(b.get("harm_miss_rate") or 0.0)), True)

    def test_eval_report_basis_new_and_legacy(self):
        from prism import evalops
        m_new = {"n": 100, "harm_miss": 3, "harm_n": 12, "grade_hit": 90, "reason_exact": 0,
                 "jaccard_sum": 0.0, "empty": 0, "cost_usd": 0.0, "tok_in": 0, "tok_out": 0,
                 "lat": [], "yellow": 0, "auto_n": 100, "auto_hit": 90, "per_reason": {}}
        rep = self._report(evalops, m_new)
        self.assertEqual(rep["harm_miss_rate"], 0.25)
        self.assertEqual(rep["harm_miss_basis"], "expected_r")
        self.assertEqual(rep["harm_miss_share"], 0.03)
        m_old = dict(m_new)
        m_old.pop("harm_n")
        rep2 = self._report(evalops, m_old)
        self.assertEqual(rep2["harm_miss_rate"], 0.03)          # 구 런은 종전 정의 유지
        self.assertEqual(rep2["harm_miss_basis"], "all_rows")

    def _report(self, evalops, metrics):
        class _St:
            def eval_run_get(self, rid, team=None):
                return {"status": "done", "metrics": metrics, "cursor": 100, "total": 100}

            def eval_results_list(self, *a, **kw):
                return []

            def batch_seq(self, team=None):
                return 0
        o = evalops._SV
        evalops._SV = type("SV", (), {"get_store": staticmethod(lambda: _St())})
        self.addCleanup(lambda: setattr(evalops, "_SV", o))
        return evalops.eval_run_report(1)

    def test_tally_counts_expected_r(self):
        from prism import evalops
        m = evalops._zero_metrics()
        evalops._tally(m, _row({"finalGrade": "R", "reasons": ["adult"]}), _out("G"))
        evalops._tally(m, _row({"finalGrade": "G", "reasons": []}), _out("G"))
        evalops._tally(m, _row({"finalGrade": "R", "reasons": ["adult"]}), None)
        self.assertEqual((m["harm_n"], m["harm_miss"]), (2, 1))

    def test_tally_on_legacy_metrics_dict(self):
        """구 런 재개(harm_n 키 없음)에서도 예외 없이 누적된다."""
        from prism import evalops
        legacy = {"n": 0, "grade_hit": 0, "reason_exact": 0, "jaccard_sum": 0.0,
                  "harm_miss": 0, "empty": 0, "cost_usd": 0.0, "tok_in": 0, "tok_out": 0,
                  "lat": [], "yellow": 0, "auto_n": 0, "auto_hit": 0, "per_reason": {}}
        evalops._tally(legacy, _row({"finalGrade": "R", "reasons": ["adult"]}), _out("G"))
        self.assertEqual(legacy["harm_n"], 1)


# ── [L8] 법령 스코어러 타입 방어 ────────────────────────────────────────────
class TestLegalScoreCoercion(unittest.TestCase):
    """점수를 문자열로 내면 TypeError 가 추출 전체로 전파돼 배치가 죽던 문제(재현 p7 케이스 J)."""

    def _content(self):
        from prism.schema import Content
        return Content(displayServiceName="뉴스", title="사기 의혹", body="환불 안 해준다" * 10)

    def test_string_scores_do_not_raise(self):
        from prism import agents as A
        c = self._content()
        llm = _StubLLM({"legal_route": {"harm_types": [{"code": "fraud", "confidence": 0.6}]},
                        "legal:fraud": {"a": "20", "b": "15", "c": "10"}})
        lm, _res = A.run_legal(llm, c)                   # 종전: TypeError 전파
        self.assertEqual(len(lm.harm_types), 1)
        self.assertEqual(lm.harm_types[0].scores["total"], 45)
        self.assertFalse(lm.failed)

    def test_unparseable_score_holds_instead_of_green(self):
        from prism import agents as A
        c = self._content()
        llm = _StubLLM({"legal_route": {"harm_types": [{"code": "fraud", "confidence": 0.6}]},
                        "legal:fraud": {"a": "높음", "b": None, "c": 10}})
        lm, _res = A.run_legal(llm, c)
        self.assertTrue(lm.failed)                       # 0점 GREEN 으로 유통 금지
        self.assertEqual(lm.harm_types, [])

    def test_string_confidence_does_not_raise(self):
        from prism import agents as A
        c = self._content()
        llm = _StubLLM({"legal_route": {"harm_types": [{"code": "fraud", "confidence": "0.6"}]},
                        "legal:fraud": {"a": 20, "b": 15, "c": 10}})
        lm, _res = A.run_legal(llm, c)
        self.assertEqual(len(lm.harm_types), 1)

    def test_garbage_confidence_marks_failed(self):
        from prism import agents as A
        c = self._content()
        llm = _StubLLM({"legal_route": {"harm_types": [{"code": "fraud", "confidence": "높음"}]}})
        lm, _res = A.run_legal(llm, c)
        self.assertTrue(lm.failed)
        self.assertEqual(lm.harm_types, [])


# ── [L9] JSON 복구 폴백 ─────────────────────────────────────────────────────
class TestJsonRecovery(unittest.TestCase):
    def test_brace_in_prose_before_json(self):
        from prism.llm import _parse_json
        txt = '아래 {결과} 참고:\n{"finalGrade":"R","reasons":["ad"]}'
        self.assertEqual(_parse_json(txt)["finalGrade"], "R")      # 종전 ParseError

    def test_two_objects_takes_last(self):
        from prism.llm import _parse_json
        self.assertEqual(_parse_json('{"finalGrade":"R"}\n{"finalGrade":"G"}')["finalGrade"], "G")

    def test_brace_inside_string_literal(self):
        from prism.llm import _parse_json
        self.assertEqual(_parse_json('설명 {x}\n{"summary":"괄호 { 포함 \\" 인용"}')["summary"],
                         '괄호 { 포함 " 인용')

    def test_still_raises_for_truly_broken(self):
        from prism.llm import ParseError, _parse_json
        for bad in ("그냥 문장", "{'a': 1}", '{"a": 1,}', '{"a": '):
            with self.assertRaises(ParseError, msg=bad):
                _parse_json(bad)

    def test_existing_paths_unchanged(self):
        from prism.llm import _parse_json
        self.assertEqual(_parse_json('```json\n{"a":1}\n```'), {"a": 1})
        self.assertEqual(_parse_json('설명\n{"a":1}'), {"a": 1})
        self.assertEqual(_parse_json('[{"a":1}]'), {"a": 1})


# ── [L10] 페르소나 폴백 표식 ────────────────────────────────────────────────
class _FailLLM:
    def __init__(self, kind="billing"):
        self.kind = kind
        self.model = "m"
        self.mock = False
        self.n = 0

    def complete_json(self, system, user, tag=""):
        from prism.llm import LLMResult
        self.n += 1
        return ({"_fail": "HTTP402", "_fail_kind": self.kind},
                LLMResult("", 0, 0, 0, 0, tag=tag, fail_kind=self.kind))


class TestPersonaFallbackMarked(unittest.TestCase):
    def _users(self, n):
        return [{"user_id": "u%d" % i, "form": {"깊이": "몰입"}, "intensity": {},
                 "engagement": {"views": 10, "avg_dwell_sec": 30, "click_rate": 0.1},
                 "interest_entity_categories": [["스포츠", 3]], "rep_contents": []}
                for i in range(n)]

    def test_call_failure_is_marked_downgraded(self):
        from prism import personagen as PG
        llm = _FailLLM("billing")
        out = PG.generate_personas(llm, {"u0": {"age_band": "30대"}}, self._users(1))
        self.assertIn("downgraded", out["u0"])
        self.assertIn("billing", out["u0"]["downgraded"])

    def test_nonretryable_failure_stops_the_loop(self):
        from prism import personagen as PG
        llm = _FailLLM("billing")
        out = PG.generate_personas(llm, {}, self._users(50))
        self.assertEqual(llm.n, 1)                     # 종전: 최대 200명분 전부 호출
        self.assertEqual(len(out), 1)

    def test_transient_failure_continues(self):
        from prism import personagen as PG
        llm = _FailLLM("timeout")
        out = PG.generate_personas(llm, {}, self._users(3))
        self.assertEqual(llm.n, 3)
        self.assertTrue(all("downgraded" in v for v in out.values()))


# ── [P1] Config.load 메모이즈 ───────────────────────────────────────────────
class TestConfigCache(unittest.TestCase):
    def _cfg_path(self, payload):
        from prism import config as C
        p = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        orig = C.DEFAULT_CONFIG_PATH
        C.DEFAULT_CONFIG_PATH = p
        self.addCleanup(lambda: setattr(C, "DEFAULT_CONFIG_PATH", orig))
        return p

    def test_file_change_is_reflected(self):
        from prism.config import Config
        p = self._cfg_path({"model": "a-model"})
        self.assertEqual(Config.load().model, "a-model")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"model": "b-model-longer"}, f)
        self.assertEqual(Config.load().model, "b-model-longer")

    def test_env_override_is_not_cached(self):
        from prism.config import Config
        self._cfg_path({"model": "file-model", "concurrency": 3})
        self.assertEqual(Config.load().model, "file-model")
        os.environ["PRISM_MODEL"] = "env-model"
        self.addCleanup(lambda: os.environ.pop("PRISM_MODEL", None))
        self.assertEqual(Config.load().model, "env-model")        # 재로드가 env 를 다시 읽는다
        os.environ.pop("PRISM_MODEL")
        self.assertEqual(Config.load().model, "file-model")

    def test_mutation_does_not_poison_cache(self):
        from prism.config import Config
        self._cfg_path({"stage_prompts": {"extract": "orig"}, "fallback_models": ["m1"]})
        c1 = Config.load()
        c1.stage_prompts["extract"] = "mutated"
        c1.fallback_models.append("m2")
        c2 = Config.load()
        self.assertEqual(c2.stage_prompts, {"extract": "orig"})
        self.assertEqual(c2.fallback_models, ["m1"])

    def test_missing_file_uses_defaults(self):
        from prism import config as C
        from prism.config import Config
        orig = C.DEFAULT_CONFIG_PATH
        C.DEFAULT_CONFIG_PATH = os.path.join(tempfile.mkdtemp(), "none.json")
        self.addCleanup(lambda: setattr(C, "DEFAULT_CONFIG_PATH", orig))
        self.assertEqual(Config.load().model, "")

    def test_cache_avoids_repeat_parsing(self):
        from prism import config as C
        from prism.config import Config
        p = self._cfg_path({"model": "cached-model"})
        Config.load()                                   # 최초 1회는 읽는다
        parses = []

        class _JsonProbe:
            def __init__(self, real):
                self._real = real

            def load(self, f):
                parses.append(1)
                return self._real.load(f)

            def __getattr__(self, name):
                return getattr(self._real, name)
        real = C.json
        C.json = _JsonProbe(real)
        self.addCleanup(lambda: setattr(C, "json", real))
        for _ in range(5):
            self.assertEqual(Config.load().model, "cached-model")
        self.assertEqual(parses, [])                    # 종전: 호출마다 디스크 I/O + 파싱
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"model": "changed-model"}, f)
        self.assertEqual(Config.load().model, "changed-model")
        self.assertEqual(len(parses), 1)                # 파일이 바뀐 그때만 재파싱


# ── [P2] gold_examples 메모이즈 ─────────────────────────────────────────────
class TestGoldExamplesCache(unittest.TestCase):
    def test_memoized_and_stable(self):
        from prism import meta_prompts as MP
        a = MP.gold_examples(None)
        b = MP.gold_examples(None)
        self.assertIs(a, b)                                  # 같은 객체 = 재조립 없음
        self.assertGreater(MP.gold_examples.cache_info().hits, 0)
        for call in ("summary", "entities", "intent", "category"):
            self.assertIn("[예시 1]", MP.gold_examples(call))
        self.assertNotEqual(MP.gold_examples("summary"), MP.gold_examples("intent"))

    def test_content_matches_gold_constant(self):
        from prism import meta_prompts as MP
        txt = MP.gold_examples("entities")
        self.assertIn(MP.GOLD[0]["entities"][0], txt)


if __name__ == "__main__":
    unittest.main()
