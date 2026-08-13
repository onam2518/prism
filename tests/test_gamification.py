"""게임화: 골드 문항·미션·품질 가중 점수 · 레벨 커브(1만 건 완주).

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
구조 근거는 TESTING.md 참조.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestServeGamification(unittest.TestCase):
    def _with_store(self):
        import tempfile
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve, st

    def _seed_gold(self, st, title="골드 문항", cats=("Sports / Golf",)):
        """골든 1건 + 그 원본 콘텐츠 행. 원본 행이 있어야 골드가 출제된다.
        모델·버전·검수티어·원문링크를 원본에서 실어 오기 때문(fail-closed)."""
        import json as _j
        import time as _t
        from prism.store import content_hash
        content = {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "본문"}
        exp = {"finalGrade": "G", "reasons": [], "content_category": list(cats), "summary": "s"}
        st.register_golden(None, [{"content": content, "expected": exp}])
        ch = content_hash(content)
        payload = {"quality_meta": {"review": "auto", "finalGrade": "G", "reasons": []},
                   "item_meta": {"summary": "s", "entities": [], "intent": [],
                                 "content_category": list(cats)},
                   "content_ref": dict(content, source_url="https://example.test/a"),
                   "trace": {"model": "m-test", "version": 3}}
        c = st._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,reasons,"
                  "item_meta,payload,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  (ch, "뉴스", title, "G", "[]", _j.dumps(payload["item_meta"]),
                   _j.dumps(payload), _t.time()))
        c.commit()
        return ch, exp

    def test_gold_injection_deterministic_and_answer(self):
        serve, st = self._with_store()
        _ch, exp = self._seed_gold(st)
        items1 = serve._inject_gold([], "tester")
        items2 = serve._inject_gold([], "tester")
        self.assertEqual(len(items1), 1)
        self.assertEqual(items1[0]["hash"], items2[0]["hash"])       # (검수자,일자) 결정적
        self.assertTrue(items1[0]["hash"].startswith("gold:"))
        variant = items1[0]["hash"].split(":")[1]
        # 뒤집는 값은 등급이 아니라 카테고리다(2026-08-13) · 등급은 어느 변형이든 참값 그대로.
        self.assertEqual(items1[0]["grade"], exp["finalGrade"])
        if variant == "bad":
            self.assertNotEqual(items1[0]["category"], exp["content_category"])
        else:
            self.assertEqual(items1[0]["category"], exp["content_category"])
        r = serve.apply_gold_answer({"hash": items1[0]["hash"], "verdict": "good", "reviewer": "tester"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["gold"]["correct"], variant == "ok")
        self.assertEqual(serve._inject_gold([], "tester"), [])       # 응답한 문항 재출제 안 함

    def test_gold_not_injected_into_empty_list(self):
        """콘텐츠 전체 삭제(빈 목록) 후 골드 문항만 홀로 남지 않아야 한다.
        (clear_contents 는 정답셋을 지우지 않으므로 골드가 큐에 계속 섞여 1건이 남던 문제.)"""
        serve, st = self._with_store()
        ch, _exp = self._seed_gold(st)
        st.set_purpose([ch], "eval")            # 원본은 평가용 홀드아웃 = 검수 대상 목록에서 제외
        self.assertEqual(len(serve._inject_gold([], "tester")), 1)    # 골드 후보 자체는 존재
        raw = serve.raw_rows(reviewer="tester")                       # 검수 대상 0건
        self.assertEqual(raw["n"], 0)                                 # 골드가 홀로 뜨지 않음
        self.assertEqual(serve.review_queue({"reviewer": "tester"})["n"], 0)   # 빈 큐에도 골드 없음

    def test_missions_progress_and_once(self):
        import time as _t
        serve, st = self._with_store()
        now = _t.time()
        for i in range(5):
            st.save_feedback(f"h{i}", "s", "T", "good", "review", "", now, reviewer="A")
        ms = {m["id"]: m for m in serve.mission_progress("A")}
        self.assertTrue(ms["daily5"]["completed"])
        fresh = serve._check_missions("A")
        self.assertIn("daily5", [m["id"] for m in fresh])
        self.assertEqual(serve._check_missions("A"), [])             # 같은 날 재보상 없음

    def test_weighted_consensus_uses_gold_reliability(self):
        import json as _j
        import time as _t
        serve, st = self._with_store()
        # 신뢰도: A(골드 5/5 정답 → 1.0) · B·C(골드 0/5 → 0.5)
        for i in range(5):
            st.save_gold_check(f"g{i}", "A", "good", "good")
            st.save_gold_check(f"g{i}", "B", "good", "bad")
            st.save_gold_check(f"g{i}", "C", "good", "bad")
        w = serve.reviewer_weights()
        self.assertEqual((w["A"], w["B"]), (1.0, 0.5))
        # 콘텐츠: A=good(1.0) vs B+C=bad(0.5+0.5) → 가중 동수(우세 아님) → 골든 미확정
        content = {"displayServiceName": "뉴스", "title": "가중 합의", "subtitle": "", "body": "본문"}
        from prism.store import content_hash
        ch = content_hash(content)
        im = {"summary": "s", "entities": [], "intent": [], "content_category": ["Sports"]}
        payload = {"quality_meta": {"review": "yellow", "finalGrade": "G", "reasons": []},
                   "item_meta": im, "content_ref": dict(content)}
        c = st._conn()
        c.execute("INSERT INTO results(content_hash,service,title,final_grade,reasons,item_meta,payload,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (ch, "뉴스", content["title"], "G", "[]", _j.dumps(im), _j.dumps(payload), _t.time()))
        c.commit()
        now = _t.time()
        st.save_feedback(ch, "뉴스", content["title"], "good", "review", "", now, reviewer="A")
        st.save_feedback(ch, "뉴스", content["title"], "bad", "review", "", now, reviewer="B")
        st.save_feedback(ch, "뉴스", content["title"], "bad", "review", "", now, reviewer="C")
        g = serve.build_golden_from_reviews(None)
        self.assertEqual(g["confirmed"], 0)
        self.assertEqual(g["disagree"], 1)


class TestLevelCurve(unittest.TestCase):
    """레벨 커브: 개인 1만 건 검수(≈10만 pt) 완주 설계 · Lv.50 만렙."""
    def test_curve_boundaries_and_journey(self):
        from prism.store import level_of, level_floor, LEVEL_MAX
        self.assertEqual(LEVEL_MAX, 50)
        self.assertEqual(level_of(0), 1)
        self.assertEqual(level_of(99), 1)
        self.assertEqual(level_of(100), 2)              # 첫 레벨업 = 100pt(검수 10건)
        self.assertEqual(level_of(level_floor(50) - 1), 49)
        self.assertEqual(level_of(level_floor(50)), 50)
        self.assertEqual(level_of(10**9), 50)           # 만렙 상한
        journey = level_floor(50) / 10                  # 건당 10pt 기준 완주 검수량
        self.assertTrue(9000 <= journey <= 11000, journey)


class TestReviewerWeightBlend(unittest.TestCase):
    def test_gold_and_ds_blend(self):
        """가중치 = 골드 정확도·DS 추정 정확도 블렌드 · 표본(5건) 미달 축은 제외."""
        import tempfile
        import time as _t
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        # 골드 문항: A 6문항 중 3 정답(acc 0.5)
        for i in range(6):
            st.save_gold_check(f"g{i}", "A", "good", "good" if i < 3 else "bad", i < 3)
        # DS 축: A·B 가 5개 유닛에 라벨(전부 일치 -> 낮은 오류율 추정)
        now = _t.time()
        for i in range(5):
            st.save_feedback(f"h{i}", "s", f"t{i}", "good", "review", "", now, reviewer="A")
            st.save_feedback(f"h{i}", "s", f"t{i}", "good", "review", "", now, reviewer="B")
        w = serve.reviewer_weights()
        self.assertIn("A", w)
        self.assertGreater(w["A"], 0.5 + 0.5 * 0.5 - 1e-9)      # DS(고정확 추정) 블렌드로 골드 단독(0.75)보다 상승
        self.assertIn("B", w)                                    # 골드 없어도 DS 축만으로 산출
        self.assertGreaterEqual(w["B"], 0.5)


if __name__ == "__main__":
    unittest.main()
