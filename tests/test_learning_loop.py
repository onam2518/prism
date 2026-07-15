"""학습 루프: 골든 누적·오케스트레이터 라우팅 · 학습 데이터·내보내기 · QA 시드.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
구조 근거는 TESTING.md 참조.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGoldenCreation(unittest.TestCase):
    def _with_store(self):
        import tempfile
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve, st

    def _put_reviewed(self, st, title, verdicts, cats=("Sports",)):
        """YELLOW 결과 + 검수자 판정 삽입 → content_hash 반환."""
        import json as _j
        import time as _t
        from prism.store import content_hash
        content = {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "본문 " + title}
        ch = content_hash(content)
        im = {"summary": title, "entities": [], "intent": [], "content_category": list(cats)}
        payload = {"quality_meta": {"review": "yellow", "finalGrade": "G", "reasons": []},
                   "item_meta": im, "content_ref": dict(content)}
        c = st._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,reasons,item_meta,payload,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (ch, "뉴스", title, "G", "[]", _j.dumps(im), _j.dumps(payload), _t.time()))
        c.commit()
        now = _t.time()
        for rv, v in verdicts:
            st.save_feedback(ch, "뉴스", title, v, "review", "", now, reviewer=rv)
        return ch

    def test_batch_accumulates_preserves_manual_and_rewards(self):
        serve, st = self._with_store()
        # 수동 등록 골든(보존돼야 함)
        mc = {"displayServiceName": "뉴스", "title": "수동 정답", "subtitle": "", "body": "b"}
        st.register_golden(None, [{"content": mc, "expected": {"finalGrade": "G", "content_category": ["Sports"]}}],
                           replace=True, source="manual")
        ch = self._put_reviewed(st, "합의 콘텐츠", [("A", "good"), ("B", "good")])
        g = serve.build_golden_from_reviews(None)
        self.assertEqual((g["confirmed"], g["new"], g["total"]), (1, 1, 2))   # 수동분 보존 + 누적
        self.assertEqual(st.golden_contrib_counts().get("A"), 1)              # 기여 보상 1회
        g2 = serve.build_golden_from_reviews(None)
        self.assertEqual((g2["new"], g2["total"]), (0, 2))                    # 재실행 중복 없음
        self.assertEqual(st.golden_contrib_counts().get("A"), 1)             # 보상도 1회 유지
        # 합의 뒤집힘 → 검수 유래 골든 강등(수동분은 보존)
        import time as _t
        st.save_feedback(ch, "뉴스", "합의 콘텐츠", "bad", "review", "", _t.time(), reviewer="B")
        st.save_feedback(ch, "뉴스", "합의 콘텐츠", "bad", "review", "", _t.time(), reviewer="C")
        g3 = serve.build_golden_from_reviews(None)
        self.assertEqual(g3["demoted"], 1)
        self.assertEqual(g3["total"], 1)                                      # 수동 1건만 남음
        self.assertNotIn(ch, st.golden_hashes())

    def test_need_category_listed(self):
        serve, st = self._with_store()
        self._put_reviewed(st, "분류 없는 합의", [("A", "good")], cats=())
        g = serve.build_golden_from_reviews(None)
        self.assertEqual(g["need_category"], 1)
        self.assertEqual(g["need_list"][0]["title"], "분류 없는 합의")

    def test_manual_golden_same_hash_not_overwritten(self):
        # P1-7: 같은 콘텐츠에 관리자 수동 골든이 있으면 검수 합의로 덮어쓰지 않는다(정답 소실 방지).
        serve, st = self._with_store()
        from prism.store import content_hash
        title = "겹치는 콘텐츠"
        content = {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "본문 " + title}
        st.register_golden(None, [{"content": content,
                                   "expected": {"finalGrade": "R", "content_category": ["Sports"], "reasons": ["graphic"]}}],
                           replace=True, source="manual")
        self._put_reviewed(st, title, [("A", "good"), ("B", "good")], cats=("Sports",))   # 검수는 G
        serve.build_golden_from_reviews(None)
        ch = content_hash(content)
        golds = st.get_golden(None)
        self.assertEqual(len(golds), 1)                                   # 검수 유래로 별도 추가 안 됨
        self.assertEqual(golds[0]["expected"]["finalGrade"], "R")        # 관리자 R 유지(검수 G 로 안 바뀜)
        self.assertEqual(st.golden_source_counts(None).get("manual"), 1)  # source 도 manual 유지
        self.assertIn(ch, st.golden_hashes())

    def test_empty_grade_not_promoted(self):
        # P1-11: finalGrade 가 빈(판정 보류·judge 실패) 콘텐츠는 합의가 있어도 골든 승격 제외.
        import json as _j
        import time as _t
        from prism.store import content_hash
        serve, st = self._with_store()
        title = "빈 등급 콘텐츠"
        content = {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "본문 " + title}
        ch = content_hash(content)
        im = {"summary": title, "entities": [], "intent": [], "content_category": ["Sports"]}
        payload = {"quality_meta": {"review": "yellow", "finalGrade": "", "reasons": []},
                   "item_meta": im, "content_ref": dict(content)}
        c = st._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,reasons,item_meta,payload,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (ch, "뉴스", title, "", "[]", _j.dumps(im), _j.dumps(payload), _t.time()))
        c.commit()
        now = _t.time()
        st.save_feedback(ch, "뉴스", title, "good", "review", "", now, reviewer="A")
        st.save_feedback(ch, "뉴스", title, "good", "review", "", now, reviewer="B")
        g = serve.build_golden_from_reviews(None)
        self.assertEqual(g["need_grade"], 1)
        self.assertEqual(g["confirmed"], 0)                              # 승격 안 됨
        self.assertNotIn(ch, st.golden_hashes())

    def test_register_validation_and_merge(self):
        serve, st = self._with_store()
        orig = serve.is_admin_user
        serve.is_admin_user = lambda *a, **k: True
        self.addCleanup(lambda: setattr(serve, "is_admin_user", orig))
        rows = [
            {"content": {"displayServiceName": "뉴스", "title": "정상", "subtitle": "", "body": "b"},
             "expected": {"finalGrade": "g", "content_category": ["World News"], "reasons": []}},
            {"content": {"displayServiceName": "뉴스", "title": "등급 오류", "subtitle": "", "body": "b"},
             "expected": {"finalGrade": "X"}},
        ]
        r = serve.register_golden("u", None, rows, "a@b.c", merge=False)
        self.assertEqual((r["count"], r["skipped"]), (1, 1))                  # 검증 제외 집계
        exp = st.get_golden(None)[0]["expected"]
        self.assertEqual(exp["finalGrade"], "G")                              # 대문자 정규화
        self.assertEqual(exp["content_category"], ["News and Politics / International News"])   # 별칭 스냅
        # 병합 등록: 기존 유지 + 추가
        rows2 = [{"content": {"displayServiceName": "뉴스", "title": "추가", "subtitle": "", "body": "b2"},
                  "expected": {"finalGrade": "R", "content_category": ["Sports"]}}]
        r2 = serve.register_golden("u", None, rows2, "a@b.c", merge=True)
        self.assertEqual(r2["count"], 1)
        self.assertEqual(st.golden_count(None), 2)
        src = st.golden_source_counts(None)
        self.assertEqual(src.get("manual"), 2)

    def test_fill_mission_counts_patches(self):
        serve, st = self._with_store()
        st.log_patch("h1", "A", "category", {"content_category": []}, {"content_category": ["Sports"]})
        ms = {m["id"]: m for m in serve.mission_progress("A")}
        self.assertTrue(ms["fill1"]["completed"])


class TestFeedbackOrchestrator(unittest.TestCase):
    def test_route_fallback_splits_elements_by_stage(self):
        from prism import feedback_loop as FL
        from prism.llm import LLMClient
        fb = {"note": "카테고리가 틀렸고 등급도 과했다", "elements": ["category", "grade"], "title": "T"}
        items = FL.route_feedback(LLMClient(mock=True), fb)     # mock → 선택 요소 폴백(무손실)
        self.assertEqual([(i["element"], i["stage"]) for i in items],
                         [("category", "analyze"), ("grade", "judge")])

    def test_route_low_confidence_falls_back(self):
        """재분류 confidence 가 임계 미만이면 선택 요소 폴백(스테이지 오염 방지 · 무손실)."""
        from prism import feedback_loop as FL

        class L:
            mock = False
            def complete_json(self, sys_p, user_p, tag=""):
                return {"items": [{"element": "grade", "directive": "이상한 재분류", "confidence": 0.2}]}, {"tag": tag}
        fb = {"note": "리드문이 과장됐다", "elements": ["summary"], "title": "T"}
        items = FL.route_feedback(L(), fb)
        self.assertEqual([(i["element"], i["directive"]) for i in items],
                         [("summary", "리드문이 과장됐다")])                  # 폴백 채택

        class L2(L):
            def complete_json(self, sys_p, user_p, tag=""):
                return {"items": [{"element": "grade", "directive": "등급 보수적으로", "confidence": 0.9}]}, {"tag": tag}
        items2 = FL.route_feedback(L2(), fb)
        self.assertEqual(items2[0]["element"], "grade")                     # 고확신은 재분류 채택

    def test_routes_persist_and_feed_learned(self):
        import tempfile
        import time as _t
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        st.save_feedback("h1", "s", "T", "bad", "analyze", "[카테고리·등급·유통] 원문",
                         _t.time(), reviewer="A", element="category,grade")
        # 후처리를 동기 실행(mock LLM → 요소 폴백 라우팅 + REAP)
        serve._reap_async("h1", "A", {"stage": "analyze", "note": "카테고리가 틀렸고 등급도 과했다",
                                      "title": "T", "elements": ["category", "grade"]})
        routed = st.routes_by_stage()
        self.assertIn("analyze", routed)
        self.assertIn("judge", routed)                          # 등급 지적 → judge 단계로 분기
        learned = st.learned_by_stage()
        self.assertIn("카테고리가 틀렸", learned["analyze"])
        self.assertIn("judge", learned)

    def test_learned_model_layering_in_prompt(self):
        """모델 귀속 보정은 그 모델 프롬프트에만 병기, 공통 보정은 항상 병기."""
        from prism import prompts as PR
        old, old_bm = dict(PR.LEARNED), PR.LEARNED_BY_MODEL
        self.addCleanup(lambda: (PR.LEARNED.update(old), setattr(PR, "LEARNED_BY_MODEL", old_bm)))
        PR.LEARNED["analyze"] = "- 공통 보정"
        PR.LEARNED_BY_MODEL = {"solar-x": {"analyze": "- 솔라 보정"}}
        base = PR._learned("analyze")
        self.assertIn("공통 보정", base)
        self.assertNotIn("솔라 보정", base)
        mine = PR._learned("analyze", "solar-x")
        self.assertIn("공통 보정", mine)
        self.assertIn("솔라 보정", mine)
        other = PR._learned("analyze", "gpt-x")
        self.assertNotIn("솔라 보정", other)

    def test_next_batch_time_deadline(self):
        """검수 목표(퀘스트) 일시 파싱: 지정 일시 -> epoch · 미지정/형식 오류 -> 0."""
        import datetime as dt
        from prism.learnops import next_batch_time
        self.assertEqual(dt.datetime.fromtimestamp(next_batch_time("2026-07-10T22:00")),
                         dt.datetime(2026, 7, 10, 22, 0))
        self.assertEqual(next_batch_time(""), 0.0)
        self.assertEqual(next_batch_time(None), 0.0)
        self.assertEqual(next_batch_time("이상한값"), 0.0)
        # 초 단위가 붙어도 분까지만 해석
        self.assertEqual(dt.datetime.fromtimestamp(next_batch_time("2026-07-10T22:00:59")),
                         dt.datetime(2026, 7, 10, 22, 0))


    def test_learning_batch_delta_and_revert(self):
        """개선 전/후 delta 기록 · 2%p 초과 악화면 LEARNED 원복 + 보정 미반영 표기."""
        import tempfile
        import json as _j
        from prism import config as C
        from prism import learnops as LO
        from prism import prompts as PR
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        cfgp = os.path.join(tempfile.mkdtemp(), "config.json")
        open(cfgp, "w", encoding="utf-8").write(_j.dumps({}))
        orig_p = C.DEFAULT_CONFIG_PATH
        C.DEFAULT_CONFIG_PATH = cfgp
        self.addCleanup(lambda: setattr(C, "DEFAULT_CONFIG_PATH", orig_p))
        old_learned = dict(PR.LEARNED)
        old_bm = PR.LEARNED_BY_MODEL
        self.addCleanup(lambda: (PR.LEARNED.update(old_learned), setattr(PR, "LEARNED_BY_MODEL", old_bm)))
        PR.LEARNED = {"extract": "", "analyze": "", "review": "", "judge": ""}
        PR.LEARNED_BY_MODEL = {}

        evals = [{"ok": True, "grade_accuracy": 0.9, "evaluated": 10},
                 {"ok": True, "grade_accuracy": 0.5, "evaluated": 10}]      # 개선 후 대폭 악화
        def fake_eval(team=None, model="", scope="all"):
            return evals.pop(0) if evals else {"ok": True, "grade_accuracy": 0.5, "evaluated": 10}
        def fake_improve(team=None):
            PR.LEARNED = {"extract": "", "analyze": "- 악화 지시", "review": "", "judge": ""}
            return {"ok": True, "results": {"analyze": {"directive": "- 악화 지시"}}}
        orig_e, orig_i = LO.eval_golden, LO.meta_compile_run
        LO.eval_golden, LO.meta_compile_run = fake_eval, fake_improve
        self.addCleanup(lambda: (setattr(LO, "eval_golden", orig_e), setattr(LO, "meta_compile_run", orig_i)))
        rep = LO.learning_batch(None)
        self.assertAlmostEqual(rep["improve_delta"], -0.4)
        self.assertTrue(rep["improve"].get("reverted"))
        self.assertEqual(PR.LEARNED["analyze"], "")                          # 원복됨
        self.assertEqual(rep["grade_accuracy"], 0.9)                         # 유지 프롬프트 기준 보고
        self.assertEqual((rep.get("eval_pre") or {}).get("grade_accuracy"), 0.9)

    def test_run_due_batch_scopes_to_quest_team(self):
        """퀘스트 도달 시 learn_team 으로 배치 실행 + 목표 소진. team 없이 돌리면 골든 승격·
        버전(batch_seq)이 팀 스코프 조회에서 사라지는 회귀를 막는다(핵심 픽스)."""
        import types
        from prism import learnops as LO
        cap = {}
        cfg = types.SimpleNamespace(learn_next_at="2020-01-01T00:00", learn_team="team-X",
                                    save_template=lambda: cap.__setitem__("saved", True))
        o_batch, o_config = LO.learning_batch, LO.Config
        self.addCleanup(lambda: (setattr(LO, "learning_batch", o_batch), setattr(LO, "Config", o_config)))
        LO.learning_batch = lambda team=None, models=None: cap.__setitem__("team", team)
        LO.Config = types.SimpleNamespace(load=lambda: cfg)
        self.assertTrue(LO._run_due_batch(cfg, now=9_999_999_999))
        self.assertEqual(cap.get("team"), "team-X")      # None 아님 = 그 팀으로 배치
        self.assertEqual(cfg.learn_next_at, "")          # 목표 소진(1회 실행)
        self.assertTrue(cap.get("saved"))

    def test_run_due_batch_falls_back_to_none_without_team(self):
        """learn_team 미설정(구 퀘스트·로컬 sqlite)이면 None 으로 폴백 — 기존 동작 보존."""
        import types
        from prism import learnops as LO
        cap = {}
        cfg = types.SimpleNamespace(learn_next_at="2020-01-01T00:00", learn_team="",
                                    save_template=lambda: None)
        o_batch, o_config = LO.learning_batch, LO.Config
        self.addCleanup(lambda: (setattr(LO, "learning_batch", o_batch), setattr(LO, "Config", o_config)))
        LO.learning_batch = lambda team=None, models=None: cap.__setitem__("team", team)
        LO.Config = types.SimpleNamespace(load=lambda: cfg)
        LO._run_due_batch(cfg, now=9_999_999_999)
        self.assertIsNone(cap.get("team"))               # "" or None → None

    def test_run_due_batch_skips_when_not_due(self):
        """미래 일시면 배치 미실행 · False(도달 전 오작동 방지)."""
        import types
        from prism import learnops as LO
        cap = {}
        cfg = types.SimpleNamespace(learn_next_at="2099-01-01T00:00", learn_team="team-X")
        o_batch = LO.learning_batch
        self.addCleanup(lambda: setattr(LO, "learning_batch", o_batch))
        LO.learning_batch = lambda team=None, models=None: cap.__setitem__("team", team)
        self.assertFalse(LO._run_due_batch(cfg, now=0))
        self.assertNotIn("team", cap)                    # 미실행


class TestLearnData(unittest.TestCase):
    def test_learn_data_and_exports(self):
        import tempfile
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        content = {"displayServiceName": "뉴스", "title": "T", "subtitle": "", "body": "B"}
        st.register_golden(None, [{"content": content,
                                   "expected": {"finalGrade": "G", "content_category": ["Sports"]}}])
        st.log_patch("h1", "A", "category", {"content_category": []}, {"content_category": ["Sports"]})
        d = serve.learn_data(None)
        self.assertTrue(d["ok"])
        self.assertEqual(d["golden_n"], 1)
        self.assertEqual(d["extractable"]["dpo"], 1)
        self.assertEqual(len(d["requirements"]), 5)
        cov = {c["cls"]: c for c in d["coverage"]}
        self.assertEqual(cov["Sports"]["have"], 1)
        fn, text = serve.learn_export("sft", None)
        self.assertEqual(fn, "prism_sft.jsonl")
        self.assertEqual(len(text.splitlines()), 1)
        fn, text = serve.learn_export("dpo", None)
        self.assertEqual(len(text.splitlines()), 1)
        self.assertIn("rejected", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestQASeed(unittest.TestCase):
    """QA 목업 시드: 10건·용도·골든·멱등(격리 DB·config)."""
    def test_seed_counts_and_idempotency(self):
        import tempfile
        from prism import serve, config as C
        from prism.store import Store
        orig_path = C.DEFAULT_CONFIG_PATH
        C.DEFAULT_CONFIG_PATH = os.path.join(tempfile.mkdtemp(), "config.json")
        self.addCleanup(lambda: setattr(C, "DEFAULT_CONFIG_PATH", orig_path))
        st = Store(os.path.join(tempfile.mkdtemp(), "qa.db"))
        serve._STORE = st
        serve.Handler.server_mock = True
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        from prism import qa_seed
        out = qa_seed.seed(verbose=False)
        self.assertTrue(out["ok"] and out["contents"] == 12)          # 확정 10 + 검수 대기(YELLOW) 2
        rows = st.recent_meta(50)
        self.assertEqual(len(rows), 12)
        self.assertEqual(sum(1 for r in rows if r["purpose"] == "eval"), 2)
        self.assertGreaterEqual(st.golden_count(), 1)
        self.assertEqual(st.batch_seq(), 1)                    # 학습 반영 1회 → 다음 버전 v2
        self.assertEqual(st.yellow_count(), 2)                 # 진척 게이지 분모(검수 대기)
        self.assertEqual(len(serve.raw_rows()["items"]), 10)    # 평가용 제외(검수 대기 2 포함)
        again = qa_seed.seed(verbose=False)
        self.assertTrue(again.get("skipped"))                   # 멱등


class TestCheapestPassing(unittest.TestCase):
    """'합격하는 가장 싼 모델' 추천: 게이트 이상 중 비용 최저 · 비용 미계측 제외."""

    def test_picks_cheapest_above_gate(self):
        from prism.learnops import cheapest_passing_model
        models = [
            {"model": "big", "grade_accuracy": 0.95, "cost_usd": 0.40},
            {"model": "mid", "grade_accuracy": 0.90, "cost_usd": 0.10},
            {"model": "tiny", "grade_accuracy": 0.70, "cost_usd": 0.01},   # 게이트 미달
        ]
        self.assertEqual(cheapest_passing_model(models, 0.85), "mid")

    def test_no_cost_excluded_and_tie_prefers_accuracy(self):
        from prism.learnops import cheapest_passing_model
        models = [
            {"model": "nocost", "grade_accuracy": 0.99, "cost_usd": None},   # 비용 미계측 제외
            {"model": "a", "grade_accuracy": 0.90, "cost_usd": 0.10},
            {"model": "b", "grade_accuracy": 0.92, "cost_usd": 0.10},        # 동률 → 일치율 높은 쪽
        ]
        self.assertEqual(cheapest_passing_model(models, 0.85), "b")

    def test_none_passing_returns_empty(self):
        from prism.learnops import cheapest_passing_model
        self.assertEqual(cheapest_passing_model(
            [{"model": "x", "grade_accuracy": 0.5, "cost_usd": 0.01}], 0.85), "")
        self.assertEqual(cheapest_passing_model([], 0.85), "")


if __name__ == "__main__":
    unittest.main()
