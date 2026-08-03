"""인텐트 정확도 지표 · 회귀 가드 · 골든 등록 검증.

배경: 평가·회귀 체계가 finalGrade / reasons 만 봐서 expected["intent"] 는 골든에
저장만 되고 어떤 지표에도 반영되지 않았다 → 인텐트 프롬프트·사전을 바꿔도 회귀 가드가
전부 침묵했다. 이 파일은 그 측정 수단의 계약을 고정한다.

① abtest.score 의 인텐트 지표(완전일치·부분일치·불일치·기대값 없음)
② evalops._tally(증분) 가 abtest.score(일괄) 와 같은 값을 낸다(단일 소스)
③ learnops._batch_regressions 인텐트 항 발동/미발동(최소 표본 가드 포함)
④ evalops.eval_run_compare 도 같은 가드를 공유
⑤ register_golden 의 사전 밖 인텐트 정제

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _row(expected: dict) -> dict:
    return {"content": {"displayServiceName": "뉴스", "title": "t", "subtitle": "", "body": "b"},
            "expected": expected}


def _out(grade="G", reasons=None, intent=None) -> dict:
    """harness.Output.to_dict 형태의 최소 산출."""
    im = None if intent is None else {"summary": "s", "entities": [],
                                      "intent": list(intent), "content_category": []}
    return {"quality_meta": {"finalGrade": grade, "reasons": list(reasons or []), "review": "auto"},
            "item_meta": im, "trace": {}}


class TestIntentScore(unittest.TestCase):
    """지표 계산 정확성 · 기존 키 불변(순수 추가)."""

    def test_exact_partial_miss_and_unlabeled(self):
        from prism import abtest
        rows = [
            _row({"finalGrade": "G", "reasons": [], "intent": ["심층 분석", "인터뷰"]}),   # 완전 일치
            _row({"finalGrade": "G", "reasons": [], "intent": ["심층 분석", "인터뷰"]}),   # 부분 일치
            _row({"finalGrade": "G", "reasons": [], "intent": ["실용 정보"]}),            # 전혀 불일치
            _row({"finalGrade": "G", "reasons": []}),                                    # 기대값 없음
            _row({"finalGrade": "G", "reasons": [], "intent": []}),                      # 빈 목록 = 라벨 없음
        ]
        outs = [
            _out(intent=["인터뷰", "심층 분석"]),      # 순서만 다름 → 집합 의미로 일치
            _out(intent=["심층 분석"]),                # 2개 중 1개
            _out(intent=["오락·유머"]),                # 교집합 0
            _out(intent=["실용 정보"]),                # 분모 제외(기대 라벨 없음)
            _out(intent=[]),
        ]
        m = abtest.score(rows, outs)
        self.assertEqual(m["intent_n"], 3)              # 라벨 있는 3건만 분모
        self.assertEqual(m["intent_skipped"], 2)        # 제외 건수 노출
        self.assertEqual(m["intent_exact"], round(1 / 3, 4))
        # 자카드: 1.0 + 0.5 + 0.0 = 1.5 / 3
        self.assertEqual(m["intent_jaccard"], 0.5)
        # 대표(첫 번째): ①은 순서가 뒤집혀 불일치, ②는 일치, ③ 불일치
        self.assertEqual(m["intent_top1"], round(1 / 3, 4))

    def test_order_is_ignored_for_set_metrics(self):
        from prism import abtest
        rows = [_row({"finalGrade": "G", "reasons": [], "intent": ["인터뷰", "심층 분석"]})]
        m = abtest.score(rows, [_out(intent=["심층 분석", "인터뷰"])])
        self.assertEqual(m["intent_exact"], 1.0)        # 집합 의미 = 순서 무시
        self.assertEqual(m["intent_jaccard"], 1.0)
        self.assertEqual(m["intent_top1"], 0.0)         # 대표값은 별도 지표로 순서를 본다

    def test_per_value_precision_recall_f1(self):
        from prism import abtest
        rows = [_row({"finalGrade": "G", "reasons": [], "intent": ["심층 분석"]}),
                _row({"finalGrade": "G", "reasons": [], "intent": ["심층 분석"]}),
                _row({"finalGrade": "G", "reasons": [], "intent": ["실용 정보"]})]
        outs = [_out(intent=["심층 분석"]),              # tp
                _out(intent=["실용 정보"]),              # 심층 분석 fn · 실용 정보 fp
                _out(intent=["실용 정보"])]              # tp
        m = abtest.score(rows, outs)
        deep = m["by_intent_value"]["심층 분석"]
        self.assertEqual((deep["n"], deep["tp"], deep["fp"], deep["fn"]), (2, 1, 0, 1))
        self.assertEqual((deep["precision"], deep["recall"], deep["f1"]), (1.0, 0.5, 0.667))
        prac = m["by_intent_value"]["실용 정보"]
        self.assertEqual((prac["n"], prac["tp"], prac["fp"], prac["fn"]), (1, 1, 1, 0))
        self.assertEqual((prac["precision"], prac["recall"]), (0.5, 1.0))   # 과부여가 보인다

    def test_empty_output_counts_as_miss(self):
        """산출 실패(None)·item_meta 억제(R 등급)는 '빈 집합 산출' = 오답으로 계수."""
        from prism import abtest
        rows = [_row({"finalGrade": "G", "reasons": [], "intent": ["심층 분석"]}),
                _row({"finalGrade": "R", "reasons": ["adult"], "intent": ["심층 분석"]})]
        m = abtest.score(rows, [None, _out(grade="R", reasons=["adult"], intent=None)])
        self.assertEqual(m["intent_n"], 2)
        self.assertEqual(m["intent_jaccard"], 0.0)
        self.assertEqual(m["by_intent_value"]["심층 분석"]["fn"], 2)

    def test_no_intent_anywhere_is_zero_sample(self):
        from prism import abtest
        rows = [_row({"finalGrade": "G", "reasons": []}) for _ in range(3)]
        m = abtest.score(rows, [_out(intent=[]) for _ in range(3)])
        self.assertEqual((m["intent_n"], m["intent_skipped"]), (0, 3))
        self.assertEqual(m["intent_jaccard"], 0)
        self.assertEqual(m["by_intent_value"], {})

    def test_existing_keys_unchanged(self):
        """순수 추가 계약: 인텐트가 어긋나도 기존 등급·사유 지표는 그대로."""
        from prism import abtest
        rows = [_row({"finalGrade": "G", "reasons": ["ad"], "intent": ["심층 분석"]})]
        m = abtest.score(rows, [_out(grade="G", reasons=["ad"], intent=["오락·유머"])])
        self.assertEqual(m["grade_accuracy"], 1.0)
        self.assertEqual(m["reason_exact_match"], 1.0)
        self.assertEqual(m["reason_jaccard"], 1.0)
        self.assertEqual(m["harm_miss_rate"], 0)
        self.assertEqual(m["intent_exact"], 0.0)


class TestTallyParity(unittest.TestCase):
    """증분 채점(evalops._tally)이 일괄 채점(abtest.score)과 같은 지표를 낸다."""

    def test_same_metrics_as_score(self):
        from prism import abtest, evalops
        rows = [
            _row({"finalGrade": "G", "reasons": [], "intent": ["심층 분석", "인터뷰"]}),
            _row({"finalGrade": "G", "reasons": [], "intent": ["실용 정보"]}),
            _row({"finalGrade": "G", "reasons": []}),
            _row({"finalGrade": "R", "reasons": ["adult"], "intent": ["오락·유머"]}),
        ]
        outs = [_out(intent=["심층 분석"]), _out(intent=["실용 정보"]),
                _out(intent=["인터뷰"]), None]
        batch = abtest.score(rows, outs)
        m = evalops._zero_metrics()
        for row, out in zip(rows, outs):
            evalops._tally(m, row, out)
        inc = abtest.intent_report(m)
        for k in ("intent_n", "intent_skipped", "intent_exact", "intent_jaccard",
                  "intent_top1", "by_intent_value"):
            self.assertEqual(inc[k], batch[k], f"{k} 불일치(일괄 vs 증분)")

    def test_tally_row_carries_intent_for_judge(self):
        """건별 결과에 기대·산출 인텐트를 대칭으로 실어 루브릭 저지 입력에 노출."""
        from prism import evalops
        m = evalops._zero_metrics()
        r = evalops._tally(m, _row({"finalGrade": "G", "reasons": [], "intent": ["인터뷰", "심층 분석"]}),
                           _out(intent=["심층 분석"]))
        self.assertEqual(r["expected"]["intent"], ["인터뷰", "심층 분석"])   # 순서 보존(대표 첫 번째)
        self.assertEqual(r["got"]["intent"], ["심층 분석"])
        r2 = evalops._tally(m, _row({"finalGrade": "G", "reasons": [], "intent": ["인터뷰"]}), None)
        self.assertEqual(r2["expected"]["intent"], ["인터뷰"])

    def test_resumed_run_without_intent_counters(self):
        """구 런 메트릭(인텐트 키 없음)으로 재개해도 예외 없이 누적된다(하위호환)."""
        from prism import abtest, evalops
        legacy = {"n": 0, "grade_hit": 0, "reason_exact": 0, "jaccard_sum": 0.0,
                  "harm_miss": 0, "empty": 0, "cost_usd": 0.0, "tok_in": 0, "tok_out": 0,
                  "lat": [], "yellow": 0, "auto_n": 0, "auto_hit": 0, "per_reason": {}}
        evalops._tally(legacy, _row({"finalGrade": "G", "reasons": [], "intent": ["인터뷰"]}),
                       _out(intent=["인터뷰"]))
        self.assertEqual(abtest.intent_report(legacy)["intent_n"], 1)


class TestIntentRegressionGuard(unittest.TestCase):
    """회귀 가드: 자카드 악화 · 값별 F1 회귀 · 최소 표본 오탐 방지."""

    def _rep(self, jac, n=40, by=None, ga=0.9):
        return {"grade_accuracy": ga, "harm_miss_rate": 0.0, "by_reason_bucket": {},
                "intent_n": n, "intent_jaccard": jac, "by_intent_value": by or {}}

    def test_jaccard_drop_regresses(self):
        from prism import learnops as LO
        g = LO._batch_regressions(self._rep(0.80), self._rep(0.70))
        self.assertTrue(any("인텐트 일치" in x for x in g), g)

    def test_small_drop_within_threshold_ok(self):
        from prism import learnops as LO
        self.assertEqual(LO._batch_regressions(self._rep(0.80), self._rep(0.77)), [])

    def test_min_sample_guard_blocks_false_alarm(self):
        """표본이 min_intent_n 미만이면 큰 낙폭도 가드를 발동시키지 않는다(소표본 오탐 방지)."""
        from prism import learnops as LO
        self.assertEqual(LO._batch_regressions(self._rep(0.9, n=5), self._rep(0.1, n=5)), [])
        self.assertEqual(LO._batch_regressions(self._rep(0.9, n=40), self._rep(0.1, n=3)), [])
        self.assertTrue(LO._batch_regressions(self._rep(0.9, n=20), self._rep(0.1, n=20)))

    def test_per_value_f1_regression(self):
        from prism import learnops as LO
        pre = self._rep(0.80, by={"포토·영상 중심": {"n": 8, "f1": 0.80}})
        post = self._rep(0.80, by={"포토·영상 중심": {"n": 8, "f1": 0.60}})
        g = LO._batch_regressions(pre, post)
        self.assertTrue(any("포토·영상 중심" in x and "F1" in x for x in g), g)

    def test_per_value_low_support_ignored(self):
        from prism import learnops as LO
        pre = self._rep(0.80, by={"인터뷰": {"n": 2, "f1": 0.9}})
        post = self._rep(0.80, by={"인터뷰": {"n": 2, "f1": 0.1}})
        self.assertEqual(LO._batch_regressions(pre, post), [])

    def test_legacy_reports_without_intent_keys(self):
        from prism import learnops as LO
        pre = {"grade_accuracy": 0.9, "harm_miss_rate": 0.0, "by_reason_bucket": {}}
        post = {"grade_accuracy": 0.9, "harm_miss_rate": 0.0, "by_reason_bucket": {}}
        self.assertEqual(LO._batch_regressions(pre, post), [])

    def test_grade_guard_still_fires(self):
        from prism import learnops as LO
        g = LO._batch_regressions(self._rep(0.8, ga=0.9), self._rep(0.8, ga=0.8))
        self.assertTrue(any("정합성" in x for x in g), g)


class TestEvalRunCompareSharesGuard(unittest.TestCase):
    """eval_run_compare(수동 런 비교)도 같은 인텐트 가드를 태운다."""

    def _with_serve(self):
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve, st

    def _done_run(self, st, per_intent, n=40):
        rid = st.eval_run_create("", "", "all", n)
        m = {"n": n, "grade_hit": n, "reason_exact": n, "jaccard_sum": float(n),
             "harm_miss": 0, "empty": 0, "cost_usd": 0.0, "tok_in": 0, "tok_out": 0,
             "lat": [1.0], "yellow": 0, "auto_n": n, "auto_hit": n, "per_reason": {},
             "intent_n": n, "intent_exact": 0, "intent_top1": 0, "intent_skipped": 0,
             "intent_jac_sum": 0.0, "per_intent": per_intent}
        m["intent_jac_sum"] = per_intent.pop("_jac", 0.0)
        st.eval_run_update(rid, status="done", cursor=n, metrics=m, finished=time.time())
        return rid

    def test_intent_regression_blocks_adoption(self):
        serve, st = self._with_serve()
        a = self._done_run(st, {"_jac": 34.0, "심층 분석": {"n": 20, "tp": 18, "fp": 1, "fn": 2}})
        b = self._done_run(st, {"_jac": 20.0, "심층 분석": {"n": 20, "tp": 6, "fp": 9, "fn": 14}})
        r = serve.eval_run_compare(a, b, None)
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["verdict"], "regressed")
        self.assertTrue(any("인텐트" in x for x in r["regressions"]), r["regressions"])

    def test_intent_improvement_is_not_regression(self):
        serve, st = self._with_serve()
        a = self._done_run(st, {"_jac": 20.0, "심층 분석": {"n": 20, "tp": 6, "fp": 9, "fn": 14}})
        b = self._done_run(st, {"_jac": 34.0, "심층 분석": {"n": 20, "tp": 18, "fp": 1, "fn": 2}})
        r = serve.eval_run_compare(a, b, None)
        self.assertEqual(r["regressions"], [])
        self.assertEqual(r["verdict"], "even")           # 등급 동률 · 회귀 없음
        self.assertGreater(r["b"]["intent_jaccard"], r["a"]["intent_jaccard"])

    def test_report_exposes_sample_size(self):
        serve, st = self._with_serve()
        rid = self._done_run(st, {"_jac": 30.0, "심층 분석": {"n": 20, "tp": 15, "fp": 2, "fn": 5}})
        rep = serve.eval_run_report(rid, None)
        self.assertEqual(rep["intent_n"], 40)
        self.assertIn("intent_skipped", rep)
        self.assertEqual(rep["by_intent_value"]["심층 분석"]["precision"], 0.882)


class TestEndToEndRun(unittest.TestCase):
    """mock 평가 런 완주 → 리포트에 인텐트 지표·표본 수가 실제로 실린다."""

    def test_mock_run_report_has_intent_keys(self):
        from prism import serve
        from prism.store import Store, content_hash
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        orig = serve.Handler.server_mock
        serve.Handler.server_mock = True                  # 외부 호출 0(결정론 mock)
        self.addCleanup(lambda: (setattr(serve, "_STORE", None),
                                 setattr(serve.Handler, "server_mock", orig)))
        for i in range(4):
            c = {"displayServiceName": "뉴스", "title": f"인텐트 평가 {i}",
                 "subtitle": "", "body": f"본문 {i} · 인텐트 지표 확인용 텍스트입니다."}
            exp = {"finalGrade": "G", "reasons": []}
            if i < 2:                                     # 절반만 인텐트 라벨 부여
                exp["intent"] = ["심층 분석"]
            st.upsert_golden(content_hash(c), c, exp)
        r = serve.eval_run_start(None, model="", scope="all")
        self.assertTrue(r.get("ok"), r)
        t0 = time.time()
        while time.time() - t0 < 60:
            run = st.eval_run_get(r["id"])
            if run and run["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(st.eval_run_get(r["id"])["status"], "done")
        rep = serve.eval_run_report(r["id"])
        self.assertEqual((rep["intent_n"], rep["intent_skipped"]), (2, 2))   # 표본 4건 중 2건
        for k in ("intent_exact", "intent_jaccard", "intent_top1", "by_intent_value"):
            self.assertIn(k, rep)
        row = st.eval_results_list(r["id"])[0]
        self.assertIn("intent", row["expected"])          # 루브릭 저지 입력에 인텐트 노출


class TestRegisterGoldenIntent(unittest.TestCase):
    """골든 등록: 사전 밖 인텐트 정제 · 경고 노출 · 하위호환."""

    def _with_store(self):
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        prev = os.environ.get("PRISM_BACKEND")           # 타 테스트가 흘린 SUPABASE_* env 차단
        os.environ["PRISM_BACKEND"] = "sqlite"
        self.addCleanup(lambda: (os.environ.__setitem__("PRISM_BACKEND", prev) if prev is not None
                                 else os.environ.pop("PRISM_BACKEND", None)))
        return serve, st

    def test_drops_unknown_and_keeps_row(self):
        serve, st = self._with_store()
        rows = [{"content": {"displayServiceName": "뉴스", "title": "오타 인텐트",
                             "subtitle": "", "body": "b"},
                 "expected": {"finalGrade": "G", "content_category": ["News"],
                              "intent": ["심층 분석", "심층분석(오타)", "  인터뷰  ", "심층 분석"]}}]
        r = serve.register_golden("uid", None, rows)
        self.assertEqual((r["count"], r["skipped"]), (1, 0))     # 행은 살린다(하위호환)
        self.assertEqual(r["intent_dropped"], 1)
        self.assertEqual(r["intent_dropped_values"], ["심층분석(오타)"])
        self.assertIn("사전에 없는 인텐트", r["warning"])
        exp = st.get_golden(None)[0]["expected"]
        self.assertEqual(exp["intent"], ["심층 분석", "인터뷰"])  # 공백 정리·중복 제거·순서 보존

    def test_spacing_variant_is_absorbed(self):
        serve, st = self._with_store()
        rows = [{"content": {"displayServiceName": "뉴스", "title": "표기 흔들림",
                             "subtitle": "", "body": "b"},
                 "expected": {"finalGrade": "G", "content_category": ["News"],
                              "intent": ["속보 · 사건 추적"]}}]
        r = serve.register_golden("uid", None, rows)
        self.assertNotIn("intent_dropped", r)
        self.assertEqual(st.get_golden(None)[0]["expected"]["intent"], ["속보·사건 추적"])

    def test_missing_key_stays_missing(self):
        """인텐트 키가 없던 골든에 빈 목록을 주입하지 않는다(측정 표본 정의 유지)."""
        serve, st = self._with_store()
        rows = [{"content": {"displayServiceName": "뉴스", "title": "인텐트 없음",
                             "subtitle": "", "body": "b"},
                 "expected": {"finalGrade": "G", "content_category": ["News"]}}]
        serve.register_golden("uid", None, rows)
        self.assertNotIn("intent", st.get_golden(None)[0]["expected"])

    def test_service_specific_value_allowed(self):
        """서비스 분기 인텐트는 해당 displayServiceName 에서만 통과."""
        from prism import learnops as LO
        ok, bad = LO._clean_intent(["속보·단신"], "뉴스")
        self.assertEqual((ok, bad), (["속보·단신"], []))
        ok2, bad2 = LO._clean_intent(["속보·단신"], "멜론")
        self.assertEqual((ok2, bad2), ([], ["속보·단신"]))

    def test_non_list_input_tolerated(self):
        from prism import learnops as LO
        self.assertEqual(LO._clean_intent("인터뷰", "뉴스"), (["인터뷰"], []))
        self.assertEqual(LO._clean_intent(None, "뉴스"), ([], []))


if __name__ == "__main__":
    unittest.main()
