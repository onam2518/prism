"""스토어 계약(sqlite): 학습 루프 테이블 · 콘텐츠 용도 · 평가 판정.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
구조 근거는 TESTING.md 참조.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestLearningStore(unittest.TestCase):
    def _store(self):
        import tempfile
        from prism.store import Store
        return Store(os.path.join(tempfile.mkdtemp(), "t.db"))

    def test_gold_checks_and_stats(self):
        st = self._store()
        self.assertTrue(st.save_gold_check("h1", "A", "good", "good"))
        self.assertFalse(st.save_gold_check("h2", "A", "bad", "good"))
        gs = st.gold_stats()["A"]
        self.assertEqual((gs["n"], gs["correct"], gs["acc"]), (2, 1, 0.5))
        self.assertEqual(st.gold_answered("A"), {"h1", "h2"})

    def test_patch_log_appends(self):
        st = self._store()
        st.log_patch("h1", "A", "category", {"content_category": []}, {"content_category": ["Sports"]})
        st.log_patch("h1", "A", "summary", {"summary": "구"}, {"summary": "신"})   # 같은 검수자 2건 무손실
        rows = st.patch_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(st.patch_counts()["A"], 2)

    def test_event_once_dedup(self):
        st = self._store()
        self.assertTrue(st.log_event_once("A", "mission:daily5", 20000, 20))
        self.assertFalse(st.log_event_once("A", "mission:daily5", 20000, 20))   # 같은 날 중복 보상 금지
        self.assertEqual(st.event_bonus()["A"]["total"], 20)

    def test_feedback_element_persisted(self):
        import time as _t
        st = self._store()
        st.save_feedback("h1", "svc", "T", "bad", "analyze", "[카테고리] 교정", _t.time(),
                         reviewer="A", element="category")
        c = st._conn()
        self.assertEqual(c.execute("SELECT element FROM feedback").fetchone()[0], "category")

    def test_arena_quality_multiplier_and_consensus(self):
        import time as _t
        st = self._store()
        now = _t.time()
        # A·B 가 h1 에 good 합의(합의 일치 +5씩) · A 골드 5건 전부 정답 → 배율 1.0 유지
        st.save_feedback("h1", "s", "T", "good", "review", "", now, reviewer="A")
        st.save_feedback("h1", "s", "T", "good", "review", "", now, reviewer="B")
        for i in range(5):
            st.save_gold_check(f"g{i}", "A", "good", "good")
        lb = {r["reviewer"]: r for r in st.arena_stats()["leaderboard"]}
        self.assertEqual(lb["A"]["quality_mult"], 1.0)
        self.assertEqual(lb["A"]["consensus_matches"], 1)
        # A: 검수1(10) + 합의1(5) + 골드5(50) = 65 · B: 검수1(10) + 합의1(5) = 15
        self.assertEqual(lb["A"]["points"], 65)
        self.assertEqual(lb["B"]["points"], 15)
        # 골드 전부 오답이면 배율 0.5
        st2 = self._store()
        st2.save_feedback("h1", "s", "T", "good", "review", "", now, reviewer="C")
        for i in range(5):
            st2.save_gold_check(f"g{i}", "C", "good", "bad")
        lb2 = {r["reviewer"]: r for r in st2.arena_stats()["leaderboard"]}
        self.assertEqual(lb2["C"]["quality_mult"], 0.5)

    def test_review_queue_split_and_uncertainty_order(self):
        import json as _j
        import time as _t
        st = self._store()

        def put(h, conf, ts):
            payload = {"quality_meta": {"review": "yellow", "confidence": conf},
                       "content_ref": {"title": h, "body": "b"}}
            c = st._conn()
            c.execute("INSERT INTO results(content_hash,service,title,final_grade,payload,created_at) "
                      "VALUES(?,?,?,?,?,?)", (h, "s", h, "G", _j.dumps(payload), ts))
            c.commit()
        put("h_hi", 0.9, _t.time())
        put("h_lo", 0.2, _t.time() - 10)
        put("h_split", 0.8, _t.time() - 20)
        now = _t.time()
        st.save_feedback("h_split", "s", "T", "good", "review", "", now, reviewer="A")
        st.save_feedback("h_split", "s", "T", "bad", "review", "", now, reviewer="B")
        q = st.review_queue()
        self.assertEqual(q[0]["hash"], "h_split")                    # 불일치 재검토 최우선
        self.assertTrue(q[0]["split"])
        self.assertEqual([r["hash"] for r in q[1:]], ["h_lo", "h_hi"])   # 저확신 우선


class TestContentPurpose(unittest.TestCase):
    """콘텐츠 용도(검수용/평가용 홀드아웃): 지정·조회 · 검수 목록 제외 · 평가 스코프."""
    def _with_store(self):
        import tempfile
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve, st

    def _put(self, st, title):
        import json as _j
        import time as _t
        from prism.store import content_hash
        content = {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "본문 " + title}
        ch = content_hash(content)
        payload = {"quality_meta": {"review": "yellow", "finalGrade": "G", "reasons": []},
                   "item_meta": {"summary": title}, "content_ref": dict(content)}
        c = st._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,reasons,item_meta,payload,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (ch, "뉴스", title, "G", "[]", "{}", _j.dumps(payload), _t.time()))
        c.commit()
        return ch

    def test_set_purpose_roundtrip_and_invalid(self):
        _, st = self._with_store()
        h1, h2 = self._put(st, "가"), self._put(st, "나")
        self.assertEqual(st.set_purpose([h1], "eval"), 1)
        self.assertEqual(st.purpose_map().get(h1), "eval")
        self.assertIsNone(st.purpose_map().get(h2))          # 미지정 = review 취급(호출부 기본)
        self.assertEqual(st.set_purpose([h1], "이상한값"), 0)   # 잘못된 용도는 거부
        self.assertEqual(st.set_purpose([h1], "review"), 1)   # 재전환 upsert
        self.assertEqual(st.purpose_map().get(h1), "review")

    def test_raw_rows_excludes_eval_holdout(self):
        serve, st = self._with_store()
        h1, h2 = self._put(st, "검수용 콘텐츠"), self._put(st, "평가용 콘텐츠")
        st.set_purpose([h2], "eval")
        hashes = {it["hash"] for it in serve.raw_rows(team=None)["items"]}
        self.assertIn(h1, hashes)
        self.assertNotIn(h2, hashes)                          # 홀드아웃은 검수 대상에서 제외

    def test_scope_golden_filters_to_eval_pool(self):
        serve, st = self._with_store()
        from prism.store import content_hash
        c1 = {"displayServiceName": "뉴스", "title": "일반", "subtitle": "", "body": "b1"}
        c2 = {"displayServiceName": "뉴스", "title": "홀드아웃", "subtitle": "", "body": "b2"}
        rows = [{"content": c1, "expected": {"finalGrade": "G"}},
                {"content": c2, "expected": {"finalGrade": "R"}}]
        st.set_purpose([content_hash(c2)], "eval")
        from prism import learnops as LO
        self.assertEqual(len(LO._scope_golden(rows, "all", st)), 2)
        scoped = LO._scope_golden(rows, "eval", st)
        self.assertEqual([r["content"]["title"] for r in scoped], ["홀드아웃"])


class TestEvalJudgment(unittest.TestCase):
    """평가 건별 판정(집단 지성): 1인 1표 upsert · 합의 → 정답 교정 필요 플래그."""
    def _with_store(self):
        import tempfile
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve, st

    def test_save_upsert_one_vote_and_counts(self):
        _, st = self._with_store()
        self.assertTrue(st.save_eval_check("h1", "복실", "adopt", "R", "G"))
        self.assertTrue(st.save_eval_check("h1", "용희", "reject"))
        self.assertTrue(st.save_eval_check("h1", "복실", "reject"))   # 재판정 = upsert(표 이동)
        self.assertFalse(st.save_eval_check("h1", "딱지", "이상"))     # 잘못된 판정 거부
        c = st.eval_check_counts()["h1"]
        self.assertEqual((c["adopt"], c["reject"]), (0, 2))
        self.assertEqual(c["reviewers"]["복실"], "reject")

    def test_adopt_consensus_flags_golden_fix(self):
        serve, st = self._with_store()
        from prism.store import content_hash
        content = {"displayServiceName": "뉴스", "title": "교정 대상", "subtitle": "", "body": "b"}
        ch = content_hash(content)
        st.register_golden(None, [{"content": content, "expected": {"finalGrade": "G"}}],
                           replace=True, source="manual")
        st.save_eval_check(ch, "복실", "adopt", "G", "R")
        items = serve.golden_list(None)["items"]
        row = next(it for it in items if it["hash"] == ch)
        self.assertTrue(row["fix_needed"])          # 채택 합의(min_good=1) → 교정 필요
        st.save_eval_check(ch, "용희", "reject")
        st.save_eval_check(ch, "딱지", "reject")
        row = next(it for it in serve.golden_list(None)["items"] if it["hash"] == ch)
        self.assertFalse(row["fix_needed"])         # 탈락 우세로 뒤집히면 해제


if __name__ == "__main__":
    unittest.main()
