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

    def test_run_due_batch_repeats_when_configured(self):
        """반복 주기(learn_repeat_days)가 있으면 소진 대신 같은 시각 +N일 미래 회차로 재생성.
        팀 태그 유지 · 새 진행률 창(quest_meta) 기록 · 밀린 회차는 미래 첫 회차까지 스킵."""
        import datetime as _dt
        import time as _t
        import types
        from prism import learnops as LO
        cap = {}
        cfg = types.SimpleNamespace(learn_next_at="2020-01-06T04:30", learn_team="team-X",
                                    learn_repeat_days=7,
                                    save_template=lambda: cap.__setitem__("saved", True))
        o_batch, o_config, o_sv = LO.learning_batch, LO.Config, LO._SV
        self.addCleanup(lambda: (setattr(LO, "learning_batch", o_batch),
                                 setattr(LO, "Config", o_config), setattr(LO, "_SV", o_sv)))
        LO.learning_batch = lambda team=None, models=None: cap.__setitem__("team", team)
        LO.Config = types.SimpleNamespace(load=lambda: cfg)
        LO._SV = types.SimpleNamespace(_report_save=lambda k, p, t=None: cap.__setitem__("qm", (k, p, t)))
        self.assertTrue(LO._run_due_batch(cfg, now=9_999_999_999))
        self.assertEqual(cap.get("team"), "team-X")
        self.assertTrue(cfg.learn_next_at)                       # 소진 대신 재생성
        nxt = _dt.datetime.strptime(cfg.learn_next_at, "%Y-%m-%dT%H:%M")
        self.assertGreater(nxt.timestamp(), _t.time())           # 미래 회차
        self.assertEqual((nxt.hour, nxt.minute), (4, 30))        # 같은 시각 유지
        self.assertEqual((nxt - _dt.datetime(2020, 1, 6, 4, 30)).days % 7, 0)   # 7일 주기 정합
        self.assertEqual(cfg.learn_team, "team-X")               # 팀 태그 유지
        self.assertEqual((cap.get("qm") or (None,))[0], "quest_meta")   # 새 진행 창 기록
        self.assertTrue(cap.get("saved"))


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

    def test_learn_data_surfaces_guide_ambiguities(self):
        """메타컴파일 ambiguities(의견 충돌)가 학습 데이터에 '가이드 명확화 필요'로 집계된다."""
        import tempfile
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        serve._report_save("learn_report", {"ok": True, "ts": 123.0, "improve": {
            "results": {"analyze": {"directive": "", "ambiguities": ["'속보'와 '단신' 중 어느 표기인지 갈림"]},
                        "review": {"directive": "", "ambiguities": []}},
            "model_results": {"gpt-x": {"judge": {"directive": "", "ambiguities": ["등급을 얼마나 보수적으로 볼지 갈림"]}}},
        }}, None)
        d = serve.learn_data(None)
        amb = d["guide_ambiguities"]
        self.assertEqual(len(amb), 2)
        self.assertEqual(amb[0], {"stage": "analyze", "model": "",
                                  "text": "'속보'와 '단신' 중 어느 표기인지 갈림"})
        self.assertEqual((amb[1]["stage"], amb[1]["model"]), ("judge", "gpt-x"))
        self.assertEqual(d["guide_ambiguities_ts"], 123.0)

    def test_learn_data_empty_ambiguities_when_no_report(self):
        import tempfile
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        d = serve.learn_data(None)
        self.assertEqual(d["guide_ambiguities"], [])


class TestReviewerCalibration(unittest.TestCase):
    """검수자 캘리브레이션: 창 필터(gold_stats_since) · 합의 가중치·골드 추세 표면화."""

    def _with_store(self):
        import tempfile
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve, st

    def _gold(self, st, rows):
        c = st._conn()
        c.executemany("INSERT INTO gold_checks(content_hash,reviewer,expected,verdict,correct,ts) "
                      "VALUES(?,?,?,?,?,?)", rows)
        c.commit()

    def test_gold_stats_since_window(self):
        import time as _t
        _serve, st = self._with_store()
        now = _t.time()
        DAY = 86400.0
        self._gold(st, [("g1", "A", "ok", "ok", 1, now - DAY),
                        ("g2", "A", "ok", "ok", 0, now - 10 * DAY)])
        wk = st.gold_stats_since(now - 7 * DAY)
        self.assertEqual((wk["A"]["n"], wk["A"]["correct"]), (1, 1))
        two = st.gold_stats_since(now - 14 * DAY)
        self.assertEqual((two["A"]["n"], two["A"]["correct"]), (2, 1))

    def test_learn_data_carries_weight_and_trend(self):
        import time as _t
        serve, st = self._with_store()
        now = _t.time()
        DAY = 86400.0
        st.save_feedback("h1", "s", "T", "good", "review", "", now, reviewer="A")   # 리더보드 진입
        # 최근 7일: 3건 중 2정답 · 그 전 7일: 3건 중 1정답 → 추세 = 2/3 - 1/3
        self._gold(st, [("w%d" % i, "A", "ok", "ok", 1 if i < 2 else 0, now - DAY) for i in range(3)])
        self._gold(st, [("p%d" % i, "A", "ok", "ok", 1 if i < 1 else 0, now - 10 * DAY) for i in range(3)])
        d = serve.learn_data(None)
        row = next(r for r in d["reviewers"] if r["reviewer"] == "A")
        # 골드 6건(≥5) · 정확도 3/6 → 가중치 0.5 + 0.5*0.5 = 0.75 (골든 다수결에 쓰는 실값)
        self.assertAlmostEqual(row["weight"], 0.75, places=4)
        self.assertAlmostEqual(row["gold_trend"], round(2 / 3 - 1 / 3, 4), places=4)
        self.assertEqual((row["gold_wk_n"], row["gold_pv_n"]), (3, 3))

    def test_trend_hidden_on_small_samples(self):
        import time as _t
        serve, st = self._with_store()
        now = _t.time()
        st.save_feedback("h1", "s", "T", "good", "review", "", now, reviewer="A")
        self._gold(st, [("g1", "A", "ok", "ok", 1, now - 86400.0)])   # 표본 3건 미만 → 추세 미표시
        d = serve.learn_data(None)
        row = next(r for r in d["reviewers"] if r["reviewer"] == "A")
        self.assertIsNone(row["gold_trend"])


class TestBatchRegressions(unittest.TestCase):
    """강화된 회귀 게이트(_batch_regressions): 스칼라 2%p + 유해 미탐 + 버킷 10%p."""

    def test_within_tolerance_passes(self):
        from prism.learnops import _batch_regressions
        pre = {"grade_accuracy": 0.9, "harm_miss_rate": 0.0,
               "by_reason_bucket": {"ad": {"n": 10, "grade_acc": 0.9},
                                    "tiny": {"n": 2, "grade_acc": 1.0}}}
        post = {"grade_accuracy": 0.89, "harm_miss_rate": 0.0,
                "by_reason_bucket": {"ad": {"n": 10, "grade_acc": 0.85},
                                     "tiny": {"n": 2, "grade_acc": 0.0}}}   # 소표본 급락은 무시
        self.assertEqual(_batch_regressions(pre, post), [])

    def test_harm_and_bucket_regressions_detected(self):
        from prism.learnops import _batch_regressions
        pre = {"grade_accuracy": 0.9, "harm_miss_rate": 0.0,
               "by_reason_bucket": {"ad": {"n": 10, "grade_acc": 0.9}}}
        post = {"grade_accuracy": 0.9, "harm_miss_rate": 0.05,
                "by_reason_bucket": {"ad": {"n": 10, "grade_acc": 0.7}}}
        r = _batch_regressions(pre, post)
        self.assertEqual(len(r), 2)
        self.assertTrue(any("유해 미탐" in x for x in r))
        self.assertTrue(any("버킷 ad" in x for x in r))

    def test_missing_post_bucket_not_regression(self):
        from prism.learnops import _batch_regressions
        pre = {"grade_accuracy": 0.9, "harm_miss_rate": 0.0,
               "by_reason_bucket": {"ad": {"n": 10, "grade_acc": 0.9}}}
        post = {"grade_accuracy": 0.85, "harm_miss_rate": 0.0, "by_reason_bucket": {}}
        self.assertEqual(_batch_regressions(pre, post), ["정합성 -5.0% 악화"])

    def test_learning_batch_reverts_on_harm_regression(self):
        """정확도가 올라도 유해 미탐이 악화되면 원복(단일 스칼라 가드의 사각 해소)."""
        import json as _j
        import tempfile
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
        evals = [{"ok": True, "grade_accuracy": 0.90, "harm_miss_rate": 0.00, "evaluated": 10},
                 {"ok": True, "grade_accuracy": 0.92, "harm_miss_rate": 0.10, "evaluated": 10}]
        def fake_eval(team=None, model="", scope="all"):
            return evals.pop(0) if evals else {"ok": True, "grade_accuracy": 0.92,
                                               "harm_miss_rate": 0.10, "evaluated": 10}
        def fake_improve(team=None):
            PR.LEARNED = {"extract": "", "analyze": "- 미탐 악화 지시", "review": "", "judge": ""}
            return {"ok": True, "results": {"analyze": {"directive": "- 미탐 악화 지시"}}}
        orig_e, orig_i = LO.eval_golden, LO.meta_compile_run
        LO.eval_golden, LO.meta_compile_run = fake_eval, fake_improve
        self.addCleanup(lambda: (setattr(LO, "eval_golden", orig_e), setattr(LO, "meta_compile_run", orig_i)))
        rep = LO.learning_batch(None)
        self.assertTrue(rep["improve"].get("reverted"))
        self.assertIn("유해 미탐", rep["improve"].get("revert_reason", ""))
        self.assertEqual(PR.LEARNED["analyze"], "")                          # 원복됨
        self.assertEqual(rep["grade_accuracy"], 0.90)                        # 유지 프롬프트 기준 보고


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
