"""평가 런(evalops · Atelier eval_runs 이식): 스토어 계약 · 백그라운드 실행 · 재개.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mk_store():
    import tempfile
    from prism.store import Store
    return Store(os.path.join(tempfile.mkdtemp(), "t.db"))


def _seed_golden(st, n=5):
    """골든 n건 시드 · content_hash 목록 반환."""
    from prism.store import content_hash
    hashes = []
    for i in range(n):
        content = {"displayServiceName": "뉴스", "title": f"평가 콘텐츠 {i}",
                   "subtitle": "", "body": f"본문 {i} · 평가 런 테스트용 텍스트입니다."}
        expected = {"finalGrade": "G" if i % 2 == 0 else "R",
                    "reasons": [] if i % 2 == 0 else ["adult"]}
        st.upsert_golden(content_hash(content), content, expected)
        hashes.append(content_hash(content))
    return hashes


class TestEvalRunStore(unittest.TestCase):
    """SQLite 스토어 계약: 런 생성·갱신·조회 · 건별 결과 upsert·재개 판별."""

    def test_run_roundtrip(self):
        st = _mk_store()
        rid = st.eval_run_create("", "solar-pro", "all", 7, created_by="uid-1")
        self.assertGreater(rid, 0)
        run = st.eval_run_get(rid)
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["total"], 7)
        self.assertEqual(run["model"], "solar-pro")
        st.eval_run_update(rid, cursor=3, metrics={"n": 3, "grade_hit": 2})
        st.eval_run_update(rid, status="done", finished=time.time())
        run = st.eval_run_get(rid)
        self.assertEqual(run["cursor"], 3)
        self.assertEqual(run["status"], "done")
        self.assertEqual(run["metrics"]["grade_hit"], 2)
        self.assertTrue(run["finished"])
        items = st.eval_runs_list()
        self.assertEqual(items[0]["id"], rid)

    def test_results_upsert_and_hashes(self):
        st = _mk_store()
        rid = st.eval_run_create("", "", "all", 2)
        rows = [{"hash": "h1", "title": "t1", "expected": {"finalGrade": "G", "reasons": []},
                 "got": {"finalGrade": "R", "reasons": ["adult"]}, "passed": False, "error": ""},
                {"hash": "h2", "title": "t2", "expected": {"finalGrade": "R", "reasons": ["adult"]},
                 "got": {"finalGrade": "R", "reasons": ["adult"]}, "passed": True, "error": ""}]
        st.eval_results_add(rid, rows)
        st.eval_results_add(rid, rows)                   # 재실행 upsert 안전
        self.assertEqual(st.eval_result_hashes(rid), {"h1", "h2"})
        fails = st.eval_results_list(rid, only_fail=True)
        self.assertEqual(len(fails), 1)
        self.assertEqual(fails[0]["hash"], "h1")
        self.assertEqual(fails[0]["got"]["finalGrade"], "R")


class TestEvalRunFlow(unittest.TestCase):
    """serve 컴포지션 + mock LLM 으로 런 전체 흐름(시작→완주→리포트→재개)."""

    def _with_serve(self):
        from prism import serve
        from prism import evalops
        st = _mk_store()
        serve._STORE = st
        self._orig_mock = serve.Handler.server_mock
        serve.Handler.server_mock = True                 # 외부 호출 0(결정론 mock)
        self.addCleanup(lambda: (setattr(serve, "_STORE", None),
                                 setattr(serve.Handler, "server_mock", self._orig_mock)))
        return serve, evalops, st

    def _wait_done(self, st, rid, timeout=60):
        t0 = time.time()
        while time.time() - t0 < timeout:
            run = st.eval_run_get(rid)
            if run and run["status"] != "running":
                return run
            time.sleep(0.1)
        self.fail("평가 런이 제한 시간 안에 끝나지 않았습니다")

    def test_start_to_done_and_report(self):
        serve, evalops, st = self._with_serve()
        _seed_golden(st, 5)
        r = serve.eval_run_start(None, model="", scope="all")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["total"], 5)
        run = self._wait_done(st, r["id"])
        self.assertEqual(run["status"], "done")
        self.assertEqual(run["cursor"], 5)
        self.assertEqual(len(st.eval_results_list(r["id"])), 5)
        rep = serve.eval_run_report(r["id"])
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["evaluated"], 5)
        for k in ("grade_accuracy", "reason_jaccard", "harm_miss_rate",
                  "by_reason_bucket", "grade_ci", "basis", "detail", "status"):
            self.assertIn(k, rep)
        self.assertEqual(rep["basis"]["scope"], "all")
        lst = serve.eval_runs_list(None)
        self.assertTrue(lst["ok"])
        self.assertEqual(lst["items"][0]["id"], r["id"])
        self.assertFalse(lst["items"][0]["stalled"])

    def test_report_has_item_meta_axes(self):
        """즉시 평가·비교표와 같은 4축(인텐트·카테고리·엔티티·리드문)이 런 리포트에도 실린다
        (ME.meta_tally/_report 를 _tally/eval_run_report 가 abtest.score 와 같은 방식으로 호출)."""
        from prism.store import content_hash
        serve, evalops, st = self._with_serve()
        content = {"displayServiceName": "뉴스", "title": "카카오뱅크 실적 발표",
                   "subtitle": "", "body": "카카오뱅크가 3분기 실적을 발표했다 카카오뱅크 주가는 상승했다"}
        expected = {"finalGrade": "G", "reasons": [],
                    "intent": ["속보·사건 추적", "심층 분석"],   # mock: D.intent_categories_for('뉴스')[:2]
                    "entities": ["카카오뱅크"],
                    "content_category": ["News and Politics / Society"]}   # mock 은 항상 이 값을 낸다
        st.upsert_golden(content_hash(content), content, expected)
        r = serve.eval_run_start(None, model="", scope="all")
        self.assertTrue(r.get("ok"), r)
        self._wait_done(st, r["id"])
        rep = serve.eval_run_report(r["id"])
        self.assertTrue(rep["ok"])
        for k in ("intent_f1", "cat_hf1", "ent_f1", "summary_sim"):
            self.assertIn(k, rep)
        self.assertEqual(rep["intent_n"], 1)
        self.assertEqual(rep["intent_f1"], 1.0)              # 기대·산출 인텐트 완전 일치
        self.assertEqual(rep["ent_n"], 1)
        self.assertGreater(rep["ent_f1"], 0)                 # 산출 엔티티에 기대값이 부분적으로 포함
        self.assertEqual(rep["cat_n"], 1)
        self.assertEqual(rep["cat_exact"], 1.0)               # mock 산출 카테고리가 기대값과 정확히 일치

    def test_start_without_golden(self):
        serve, evalops, st = self._with_serve()
        r = serve.eval_run_start(None)
        self.assertFalse(r.get("ok"))
        self.assertIn("골든셋", r.get("error", ""))

    def test_stalled_resume_completes_remaining(self):
        serve, evalops, st = self._with_serve()
        hashes = _seed_golden(st, 4)
        # 서버 재시작으로 유실된 런 흉내: running 행 + 앞 2건만 결과 존재 · 스레드 없음
        rid = st.eval_run_create("", "", "all", 4)
        st.eval_results_add(rid, [{"hash": h, "title": "", "expected": {"finalGrade": "G", "reasons": []},
                                   "got": {"finalGrade": "G", "reasons": []}, "passed": True, "error": ""}
                                  for h in hashes[:2]])
        st.eval_run_update(rid, cursor=2, metrics={"n": 2, "grade_hit": 2, "reason_exact": 2,
                                                   "jaccard_sum": 2.0, "harm_miss": 0, "empty": 0,
                                                   "cost_usd": 0.0, "tok_in": 0, "tok_out": 0,
                                                   "lat": [], "yellow": 0, "auto_n": 2, "auto_hit": 2,
                                                   "per_reason": {}})
        lst = serve.eval_runs_list(None)
        self.assertTrue(lst["items"][0]["stalled"])       # 프로세스에 스레드 없음 → 재개 대상 표시
        rr = serve.eval_run_resume(rid, None)
        self.assertTrue(rr.get("ok"), rr)
        self.assertEqual(rr["remain"], 2)                 # 저장된 2건 제외 · 남은 건만
        run = self._wait_done(st, rid)
        self.assertEqual(run["status"], "done")
        self.assertEqual(len(st.eval_result_hashes(rid)), 4)
        self.assertEqual(run["metrics"]["n"], 4)          # 카운터가 기존 2건 위에 누적

    def test_cancel_without_thread_marks_cancelled(self):
        serve, evalops, st = self._with_serve()
        rid = st.eval_run_create("", "", "all", 3)
        r = serve.eval_run_cancel(rid, None)
        self.assertTrue(r.get("ok"))
        self.assertEqual(st.eval_run_get(rid)["status"], "cancelled")


class TestEvalRubric(unittest.TestCase):
    """루브릭 진단(4축 · Atelier rubric-judge 이식): 채점 흐름·게이트·미채점만 재실행."""

    def _with_serve(self):
        from prism import serve
        from prism import evalops
        st = _mk_store()
        serve._STORE = st
        self._orig_mock = serve.Handler.server_mock
        serve.Handler.server_mock = True
        self.addCleanup(lambda: (setattr(serve, "_STORE", None),
                                 setattr(serve.Handler, "server_mock", self._orig_mock)))
        return serve, evalops, st

    def _done_run(self, serve, evalops, st, n=4):
        """골든 n건으로 평가 런을 완주시키고 run_id 반환."""
        _seed_golden(st, n)
        r = serve.eval_run_start(None)
        t0 = time.time()
        while time.time() - t0 < 60:
            if st.eval_run_get(r["id"])["status"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(st.eval_run_get(r["id"])["status"], "done")
        return r["id"]

    def _patch_judge(self, evalops, scores):
        orig = evalops._judge_batch
        evalops._judge_batch = lambda llm, items: {it["id"]: dict(scores) for it in items}
        self.addCleanup(lambda: setattr(evalops, "_judge_batch", orig))

    def _wait_rubric(self, st, rid, timeout=60):
        t0 = time.time()
        while time.time() - t0 < timeout:
            run = st.eval_run_get(rid)
            if run.get("rubric_status") in ("done", "failed", "cancelled"):
                return run
            time.sleep(0.1)
        self.fail("루브릭 채점이 제한 시간 안에 끝나지 않았습니다")

    def test_rubric_flow_and_aggregate(self):
        serve, evalops, st = self._with_serve()
        rid = self._done_run(serve, evalops, st, n=4)
        self._patch_judge(evalops, {"accuracy": 5, "format": 4, "policy": 5,
                                    "conciseness": 3, "note": "양호"})
        r = serve.rubric_start(rid, None)
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["pending"], 4)
        run = self._wait_rubric(st, rid)
        self.assertEqual(run["rubric_status"], "done")
        self.assertEqual(run["rubric"]["n"], 4)
        self.assertEqual(run["rubric"]["accuracy"], 5.0)
        self.assertEqual(run["rubric"]["conciseness"], 3.0)
        rows = st.eval_results_list(rid)
        self.assertTrue(all((x.get("rubric") or {}).get("note") == "양호" for x in rows))
        rep = serve.eval_run_report(rid)
        self.assertEqual(rep["rubric_status"], "done")
        self.assertEqual(rep["rubric"]["n"], 4)

    def test_rubric_requires_done_run(self):
        serve, evalops, st = self._with_serve()
        rid = st.eval_run_create("", "", "all", 3)     # running 상태
        r = serve.rubric_start(rid, None)
        self.assertFalse(r.get("ok"))
        self.assertIn("완주", r.get("error", ""))

    def test_rubric_rerun_only_missing(self):
        serve, evalops, st = self._with_serve()
        rid = self._done_run(serve, evalops, st, n=3)
        self._patch_judge(evalops, {"accuracy": 4, "format": 4, "policy": 4,
                                    "conciseness": 4, "note": ""})
        serve.rubric_start(rid, None)
        self._wait_rubric(st, rid)
        r = serve.rubric_start(rid, None)              # 전건 채점 완료 → 재실행 거부
        self.assertFalse(r.get("ok"))
        self.assertIn("채점할 건이 없습니다", r.get("error", ""))


class TestEvalRunCompare(unittest.TestCase):
    """런 비교·회귀 가드: 학습배치 _batch_regressions 와 단일 소스 판정."""

    def _with_serve(self):
        from prism import serve
        st = _mk_store()
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve, st

    def _done_run(self, st, ga, harm=0.0):
        """지표를 지정한 완주 런 행 생성(실행 없이 · n=10 기준)."""
        rid = st.eval_run_create("", "", "all", 10)
        m = {"n": 10, "grade_hit": int(round(ga * 10)), "reason_exact": 8,
             "jaccard_sum": 8.0, "harm_miss": int(round(harm * 10)), "empty": 0,
             "cost_usd": 0.01, "tok_in": 100, "tok_out": 100, "lat": [5.0],
             "yellow": 0, "auto_n": 10, "auto_hit": int(round(ga * 10)), "per_reason": {}}
        st.eval_run_update(rid, status="done", cursor=10, metrics=m, finished=time.time())
        return rid

    def test_regression_blocks_adoption(self):
        serve, st = self._with_serve()
        a = self._done_run(st, 0.9)
        b = self._done_run(st, 0.8)                    # 등급 일치율 -10%p → 회귀
        r = serve.eval_run_compare(a, b, None)
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["verdict"], "regressed")
        self.assertTrue(any("정합성" in g for g in r["regressions"]))

    def test_improved_and_even(self):
        serve, st = self._with_serve()
        a = self._done_run(st, 0.7)
        b = self._done_run(st, 0.8)
        self.assertEqual(serve.eval_run_compare(a, b, None)["verdict"], "improved")
        self.assertEqual(serve.eval_run_compare(a, a, None)["verdict"], "even")

    def test_harm_miss_worsening_regresses(self):
        serve, st = self._with_serve()
        a = self._done_run(st, 0.8, harm=0.0)
        b = self._done_run(st, 0.8, harm=0.1)          # 유해 미탐 악화 → 정합성 동일해도 회귀
        r = serve.eval_run_compare(a, b, None)
        self.assertEqual(r["verdict"], "regressed")
        self.assertTrue(any("유해" in g for g in r["regressions"]))

    def test_requires_done_runs(self):
        serve, st = self._with_serve()
        a = self._done_run(st, 0.8)
        b = st.eval_run_create("", "", "all", 10)      # running
        r = serve.eval_run_compare(a, b, None)
        self.assertFalse(r.get("ok"))
        self.assertIn("완주한 런", r.get("error", ""))


class TestAutopilot(unittest.TestCase):
    """오토파일럿(자동 개선 루프): 목표 달성·정체 종료·골든 게이트·단일 실행·수동 중지."""

    def _with_serve(self):
        from prism import serve
        st = _mk_store()
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve, st

    def _patch_batch(self, accs, delay=0.0):
        """learning_batch 를 라운드별 정확도 시퀀스로 대체(마지막 값 반복)."""
        import prism.learnops as LO
        orig = LO.learning_batch
        state = {"i": 0}

        def fake(team=None, models=None, **kw):
            import time as _t
            if delay:
                _t.sleep(delay)
            i = state["i"]
            state["i"] += 1
            acc = accs[min(i, len(accs) - 1)]
            return {"ok": True, "grade_accuracy": acc,
                    "eval_pre": {"grade_accuracy": max(0.0, round(acc - 0.05, 4))},
                    "improve": {"reverted": False}, "improve_delta": 0.05,
                    "prompt_snapshot": {"version": i + 1}}
        LO.learning_batch = fake
        self.addCleanup(lambda: setattr(LO, "learning_batch", orig))

    def _wait(self, st, timeout=60):
        t0 = time.time()
        while time.time() - t0 < timeout:
            run = st.autopilot_latest(None)
            if run and run["status"] != "running":
                return run
            time.sleep(0.05)
        self.fail("오토파일럿이 제한 시간 안에 끝나지 않았습니다")

    def test_target_reached(self):
        serve, st = self._with_serve()
        _seed_golden(st, 3)
        self._patch_batch([0.7, 0.85, 0.93])
        r = serve.autopilot_start(None, target=0.9, max_rounds=5)
        self.assertTrue(r.get("ok"), r)
        run = self._wait(st)
        self.assertEqual(run["status"], "done")
        self.assertIn("목표 달성", run["stop_reason"])
        self.assertEqual(run["round"], 3)
        self.assertEqual(len(run["history"]), 3)
        self.assertEqual(run["best_accuracy"], 0.93)
        self.assertEqual(run["start_accuracy"], 0.65)   # 1라운드 개선 전 점수
        self.assertFalse(run["history"][0]["reverted"])

    def test_stall_stops(self):
        serve, st = self._with_serve()
        _seed_golden(st, 3)
        self._patch_batch([0.7, 0.7, 0.7, 0.7])
        serve.autopilot_start(None, target=0.95, max_rounds=5)
        run = self._wait(st)
        self.assertEqual(run["status"], "done")
        self.assertIn("정체", run["stop_reason"])
        self.assertEqual(run["round"], 3)               # 1라운드 최고 경신 후 2연속 무향상

    def test_stall_uses_overall_score(self):
        """등급이 제자리여도 메타 축(엔티티 F1)이 오르면 향상으로 본다 · 정체 판정 = 종합 점수."""
        serve, st = self._with_serve()
        _seed_golden(st, 3)
        import prism.learnops as LO
        orig = LO.learning_batch
        ents = iter([0.5, 0.6, 0.7, 0.8, 0.9])
        def fake(team=None, models=None, model=""):
            return {"ok": True, "grade_accuracy": 0.7, "eval_pre": {"grade_accuracy": 0.7},
                    "eval": {"ok": True, "grade_accuracy": 0.7, "ent_n": 10, "ent_f1": next(ents)},
                    "improve": {"reverted": False}, "prompt_snapshot": {"version": 1}}
        LO.learning_batch = fake
        self.addCleanup(lambda: setattr(LO, "learning_batch", orig))
        serve.autopilot_start(None, target=0.95, max_rounds=4)
        run = self._wait(st)
        self.assertIn("최대 라운드", run["stop_reason"])   # 종합이 매 라운드 올라 정체로 끊기지 않는다
        self.assertEqual(run["round"], 4)
        self.assertEqual(run["best_accuracy"], 0.7)

    def test_meta_target_blocks_done(self):
        """등급이 목표를 넘어도 아이템 메타가 메타 목표 미달이면 목표 달성이 아니다 · 런에 meta_target 기록."""
        serve, st = self._with_serve()
        _seed_golden(st, 3)
        import prism.learnops as LO
        orig = LO.learning_batch
        ents = iter([0.65, 0.75, 0.85])
        def fake(team=None, models=None, model=""):
            return {"ok": True, "grade_accuracy": 0.95, "eval_pre": {"grade_accuracy": 0.9},
                    "eval": {"ok": True, "grade_accuracy": 0.95, "ent_n": 10, "ent_f1": next(ents)},
                    "improve": {"reverted": False}, "prompt_snapshot": {"version": 1}}
        LO.learning_batch = fake
        self.addCleanup(lambda: setattr(LO, "learning_batch", orig))
        r = serve.autopilot_start(None, target=0.9, max_rounds=5, meta_target=0.8)
        self.assertTrue(r.get("ok"), r)
        run = self._wait(st)
        self.assertIn("목표 달성", run["stop_reason"])
        self.assertEqual(run["round"], 3)               # 0.65·0.75 는 메타 80% 미달 · 0.85 에서 달성
        self.assertEqual(run["meta_target"], 0.8)
        self.assertFalse(serve.autopilot_start(None, target=0.9, meta_target=0.3).get("ok"))

    def test_meta_gate_configurable(self):
        from prism.config import Config, _merge
        cfg = _merge(Config(), {"thresholds": {"meta_gate": 0.8}})
        self.assertEqual(cfg.thresholds.meta_gate, 0.8)

    def test_requires_golden(self):
        serve, st = self._with_serve()
        r = serve.autopilot_start(None)
        self.assertFalse(r.get("ok"))
        self.assertIn("정답셋", r.get("error", ""))

    def test_single_active_and_manual_stop(self):
        serve, st = self._with_serve()
        _seed_golden(st, 3)
        self._patch_batch([0.6], delay=0.3)             # 느린 라운드 · 목표 미달로 계속 돎
        r = serve.autopilot_start(None, target=0.99, max_rounds=8)
        self.assertTrue(r.get("ok"), r)
        r2 = serve.autopilot_start(None)
        self.assertFalse(r2.get("ok"))
        self.assertIn("이미", r2.get("error", ""))
        rs = serve.autopilot_stop(None)
        self.assertTrue(rs.get("ok"), rs)
        run = self._wait(st)
        self.assertEqual(run["status"], "stopped")
        self.assertIn("수동 중지", run["stop_reason"])
        self.assertTrue(serve.autopilot_status(None)["ok"])


if __name__ == "__main__":
    unittest.main()


class TestAutopilotModel(TestAutopilot if 'TestAutopilot' in globals() else unittest.TestCase):
    """평가 모델 지정 · 호출 실패 라운드는 장애로 종료."""

    def test_model_passthrough_and_empty_guard(self):
        from prism import learnops as LO
        serve, st = self._with_serve()
        _seed_golden(st, 3)
        om = serve.Handler.server_mock; serve.Handler.server_mock = True      # 모델 라우팅은 mock 클라이언트로
        self.addCleanup(lambda: setattr(serve.Handler, "server_mock", om))
        seen = []
        orig = LO.learning_batch
        def fake(team=None, models=None, model="", **kw):
            seen.append(model)
            return {"ok": True, "grade_accuracy": 0.0, "eval": {"empty_rate": 1.0},
                    "eval_pre": {"grade_accuracy": 0.0}, "improve": {"reverted": False},
                    "improve_delta": 0.0, "prompt_snapshot": {"version": 1}}
        LO.learning_batch = fake
        self.addCleanup(lambda: setattr(LO, "learning_batch", orig))
        r = serve.autopilot_start(None, target=0.9, max_rounds=5, model="solar-pro2")
        self.assertTrue(r.get("ok"), r); self.assertEqual(r["model"], "solar-pro2")
        run = self._wait(st)
        self.assertEqual(run["status"], "failed")
        self.assertIn("모델 호출 실패", run["error"]); self.assertIn("solar-pro2", run["error"])
        self.assertEqual(seen, ["solar-pro2"])                     # 첫 라운드에서 바로 멈춤(라운드 낭비 없음)


class TestAutopilotRoundMetrics(TestAutopilot):
    """라운드 이력에 비교표와 같은 지표 · 목표 달성은 등급 목표 + 메타 게이트 전부 통과."""

    def test_metrics_and_gate(self):
        from prism import learnops as LO
        serve, st = self._with_serve()
        _seed_golden(st, 3)
        orig = LO.learning_batch
        evs = [{"grade_accuracy": 0.95, "intent_n": 10, "intent_f1": 0.4, "cat_n": 10, "cat_hf1": 0.9, "cost_usd": 0.5, "empty_rate": 0.0},
               {"grade_accuracy": 0.96, "intent_n": 10, "intent_f1": 0.8, "cat_n": 10, "cat_hf1": 0.9, "cost_usd": 0.4, "empty_rate": 0.0}]
        state = {"i": 0}
        def fake(team=None, models=None, **kw):
            ev = evs[min(state["i"], 1)]; state["i"] += 1
            return {"ok": True, "grade_accuracy": ev["grade_accuracy"], "eval": ev,
                    "eval_pre": {"grade_accuracy": 0.9}, "improve": {"reverted": False},
                    "improve_delta": 0.05, "prompt_snapshot": {"version": state["i"]}}
        LO.learning_batch = fake
        self.addCleanup(lambda: setattr(LO, "learning_batch", orig))
        r = serve.autopilot_start(None, target=0.9, max_rounds=5)
        self.assertTrue(r.get("ok"), r)
        run = self._wait(st)
        h = run["history"]
        self.assertEqual(len(h), 2)                                  # 1라운드는 인텐트 게이트 미달 → 계속 · 2라운드 통과
        self.assertEqual(h[0]["metrics"]["gate_fails"], ["intent_f1"]); self.assertFalse(h[0]["metrics"]["passed"])
        self.assertTrue(h[1]["metrics"]["passed"]); self.assertIn("overall", h[1]["metrics"])
        self.assertEqual(h[1]["metrics"]["cat_hf1"], 0.9)
        self.assertIn("전부 통과", run["stop_reason"])


class TestAutopilotStalled(TestAutopilot):
    def test_status_marks_dead_running_as_stopped(self):
        serve, st = self._with_serve()
        _seed_golden(st, 3)
        self._patch_batch([0.95])
        rid = st.autopilot_create(None, 0.9, 5)                    # running 인데 스레드는 없다(서버 재시작 상황)
        r = serve.autopilot_status(None)
        self.assertEqual(r["run"]["id"], rid); self.assertTrue(r["run"]["stalled"])
        self.assertEqual(r["run"]["status"], "stopped")
        self.assertEqual(st.autopilot_latest(None)["status"], "stopped")   # DB 에도 정리 → 시작 폼이 다시 보인다
        self.assertTrue(serve.autopilot_start(None, target=0.9, max_rounds=1).get("ok"))
        self._wait(st)                                             # 스레드가 끝난 뒤 정리(가짜 배치 복원 전에 실런 방지)
