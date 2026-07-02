"""Prism 핵심 로직 유닛 테스트 (stdlib unittest · 의존성 0).

실행: python3 -m unittest discover -s tests   (또는 python3 tests/test_core.py)
커버: content_category(list) · 사전화 · 인입 매핑(source_url) · 드릴다운 ·
      상세 표준화 · verify Tier1 화이트리스트 · 스키마 참조 필드.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDictionaries(unittest.TestCase):
    def test_normalize_content_category_snap(self):
        from prism import dictionaries as D
        self.assertEqual(D.normalize_content_category("Sports / Soccer (Domestic)"),
                         "Sports / Soccer (Domestic)")
        self.assertEqual(D.normalize_content_category("엉터리"), "Unclassified")

    def test_normalize_category_list(self):
        from prism import dictionaries as D
        out = D.normalize_category_list(["Sports", "Sports", "엉터리", "News and Politics"])
        self.assertEqual(out, ["Sports", "News and Politics"])          # 중복·미분류 제거
        self.assertEqual(D.normalize_category_list("Sports"), ["Sports"])  # 문자열 허용
        # 구 dict 형식 호환(값만 추림)
        self.assertEqual(D.normalize_category_list({"e": "Sports"}), ["Sports"])


class TestIngest(unittest.TestCase):
    def test_source_url_mapping(self):
        from prism.ingest import to_contents_rows
        rows = [{"제목": "T", "내용": "본문", "서비스명": "뉴스", "원문링크": "https://x/1"}]
        items, m = to_contents_rows(rows)
        self.assertEqual(m.get("source_url"), "원문링크")
        self.assertEqual(items[0]["source_url"], "https://x/1")

    def test_required_missing_raises(self):
        from prism.ingest import to_contents_rows
        with self.assertRaises(ValueError):
            to_contents_rows([{"제목": "T"}])                          # body 없음


class TestSchema(unittest.TestCase):
    def test_content_source_url_and_ref(self):
        from prism.schema import Content
        c = Content.from_dict({"displayServiceName": "뉴스", "title": "T",
                               "body": "B", "url": "https://x/2"})
        self.assertEqual(c.source_url, "https://x/2")                  # url→source_url
        ref = c.ref()
        self.assertEqual(ref["body"], "B")
        self.assertEqual(ref["source_url"], "https://x/2")


class TestDrillAndDetail(unittest.TestCase):
    def _rows(self):
        return [
            {"content_ref": {"title": "삼성 노조", "body": "본문1", "displayServiceName": "뉴스",
                             "source_url": "https://x/1", "body_hash": "h1"},
             "item_meta": {"summary": "리드문1", "entities": ["삼성전자"],
                           "intent": ["사건 경과 보도"], "content_category": ["News and Politics / Society"]},
             "quality_meta": {"finalGrade": "G", "reasons": []}},
            {"content_ref": {"title": "낚시", "body": "본문2", "displayServiceName": "커뮤니티"},
             "item_meta": {"summary": "리드문2", "entities": [], "intent": ["흥미·화제"],
                           "content_category": []},
             "quality_meta": {"finalGrade": "R", "reasons": ["clickbait"]}},
        ]

    def test_detail_row(self):
        from prism.serve import _detail_row
        r = _detail_row(self._rows()[0])
        self.assertEqual(r["title"], "삼성 노조")
        self.assertEqual(r["body"], "본문1")
        self.assertEqual(r["url"], "https://x/1")
        self.assertEqual(r["grade"], "G")
        self.assertIn("삼성전자", r["entities"])

    def test_drill_filter(self):
        import prism.serve as S
        S._LAST_RESULTS = self._rows()                                 # store 없을 때 메모리 사용
        orig = S.get_store
        S.get_store = lambda: None
        try:
            by_intent = S.drill_contents("intent", "사건 경과 보도")
            self.assertEqual(by_intent["n"], 1)
            by_reason = S.drill_contents("reason", "clickbait")
            self.assertEqual(by_reason["n"], 1)
            by_cat = S.drill_contents("category", "News and Politics")
            self.assertEqual(by_cat["n"], 1)
        finally:
            S.get_store = orig


class TestBadges(unittest.TestCase):
    def test_save_badges_echo_without_store(self):
        import prism.serve as S
        orig = S.get_store
        S.get_store = lambda: None
        try:
            out = S.save_badges("u1", ["첫 검수", "연속 3일"])
            self.assertTrue(out["ok"])
            self.assertEqual(out["badges"], ["첫 검수", "연속 3일"])
            self.assertFalse(out.get("persisted", True))
        finally:
            S.get_store = orig

    def test_save_badges_monotonic_union(self):
        import prism.serve as S

        class FakeStore:                                   # save_badges = 기존 ∪ 신규
            def __init__(self):
                self.saved = {"u1": ["첫 검수"]}

            def save_badges(self, uid, earned):
                cur = self.saved.get(uid, [])
                merged = cur + [b for b in earned if b not in cur]
                self.saved[uid] = merged
                return merged

        fake = FakeStore()
        orig = S.get_store
        S.get_store = lambda: fake
        try:
            out = S.save_badges("u1", ["첫 검수", "Lv.5"])
            self.assertEqual(out["badges"], ["첫 검수", "Lv.5"])   # 중복 없이 합집합
        finally:
            S.get_store = orig


class TestAggCache(unittest.TestCase):
    def test_memo_and_invalidation(self):
        import prism.serve as S
        S._AGG_CACHE.clear()
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            return calls["n"]

        a = S._agg_cached(("t", None), fn)
        b = S._agg_cached(("t", None), fn)          # 캐시 히트 → 재계산 없음
        self.assertEqual(a, b)
        self.assertEqual(calls["n"], 1)
        S._agg_bump()                                # 무효화 → 재계산
        c = S._agg_cached(("t", None), fn)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(c, 2)


class TestVerify(unittest.TestCase):
    def test_content_category_tier1_whitelist_list(self):
        from prism.verify import verify_item
        from prism.schema import ItemMeta, Content
        im = ItemMeta(content_category=["News and Politics / Society", "엉터리Tier1 / X",
                                        "News and Politics / Society"])  # 중복+사전외
        verify_item(im, Content(displayServiceName="뉴스", title="t"))
        self.assertEqual(im.content_category, ["News and Politics / Society"])  # 화이트리스트+중복제거


class TestHarnessMock(unittest.TestCase):
    def test_content_category_is_list(self):
        from prism import harness
        from prism.llm import LLMClient
        out = harness.run({"displayServiceName": "연예", "title": "정국 빌보드 1위",
                           "body": "방탄소년단 정국의 솔로 앨범이 빌보드 핫100 1위에 올랐다. 한국 솔로 최초의 기록이다."},
                          LLMClient(mock=True))
        cc = out["item_meta"]["content_category"]
        self.assertIsInstance(cc, list)


class TestQuality(unittest.TestCase):
    def test_alpha_perfect_and_chance(self):
        from prism import quality as Q
        self.assertEqual(Q.krippendorff_alpha_binary([[1, 1], [0, 0]]), 1.0)   # 완전 일치
        self.assertEqual(Q.krippendorff_alpha_binary([[1, 0]]), 0.0)           # 우연 수준
        self.assertIsNone(Q.krippendorff_alpha_binary([[1]]))                  # 평가 불가

    def test_percent_agreement(self):
        from prism import quality as Q
        self.assertEqual(Q.percent_agreement([[1, 1], [1, 0]]), 0.5)

    def test_binomial_ci(self):
        from prism import quality as Q
        lo, hi = Q.binomial_ci(0.5, 100)
        self.assertAlmostEqual(lo, 0.402, places=3)
        self.assertAlmostEqual(hi, 0.598, places=3)
        self.assertEqual(Q.binomial_ci(1.0, 0), (0.0, 1.0))                    # n=0 방어

    def test_dawid_skene_flags_bad_annotator(self):
        from prism import quality as Q
        # r1·r2 는 항상 합의, r3 는 항상 반대 → r3 오류율이 가장 높아야 함
        labels = {f"u{i}": {"r1": i % 2, "r2": i % 2, "r3": 1 - (i % 2)} for i in range(10)}
        out = Q.dawid_skene_binary(labels)
        r = out["reviewers"]
        self.assertGreater(r["r3"]["error_rate"], r["r1"]["error_rate"])
        self.assertEqual(r["r1"]["n"], 10)


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


class TestServeGamification(unittest.TestCase):
    def _with_store(self):
        import tempfile
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve, st

    def test_gold_injection_deterministic_and_answer(self):
        serve, st = self._with_store()
        content = {"displayServiceName": "뉴스", "title": "골드 문항", "subtitle": "", "body": "본문"}
        st.register_golden(None, [{"content": content,
                                   "expected": {"finalGrade": "G", "reasons": [],
                                                "content_category": ["Sports"], "summary": "s"}}])
        items1 = serve._inject_gold([], "tester")
        items2 = serve._inject_gold([], "tester")
        self.assertEqual(len(items1), 1)
        self.assertEqual(items1[0]["hash"], items2[0]["hash"])       # (검수자,일자) 결정적
        self.assertTrue(items1[0]["hash"].startswith("gold:"))
        variant = items1[0]["hash"].split(":")[1]
        # 등급 뒤집기 변형이면 표시 등급 R(정답 bad), 원본이면 G(정답 good)
        self.assertEqual(items1[0]["grade"], "R" if variant == "bad" else "G")
        r = serve.apply_gold_answer({"hash": items1[0]["hash"], "verdict": "good", "reviewer": "tester"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["gold"]["correct"], variant == "ok")
        self.assertEqual(serve._inject_gold([], "tester"), [])       # 응답한 문항 재출제 안 함

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
        self.assertEqual(len(serve._scope_golden(rows, "all", st)), 2)
        scoped = serve._scope_golden(rows, "eval", st)
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


class TestMetaPromptBaseline(unittest.TestCase):
    """기준 문서(contextual-meta-extraction v2.1) 기본 적용: 계열 라우팅·코어 규칙·사전 주입."""
    def test_family_routing(self):
        from prism import meta_prompts as MP
        self.assertEqual(MP.family_of("openai/gpt-5.4-mini"), "gpt")
        self.assertEqual(MP.family_of("gemini-3.1-pro"), "gemini")
        self.assertEqual(MP.family_of("anthropic/claude-sonnet-4.6"), "claude")
        self.assertEqual(MP.family_of("solar-pro3-260323"), "solar")
        self.assertEqual(MP.family_of("deepseek/deepseek-v3.2"), "default")

    def test_item_system_family_framing_and_dicts(self):
        from prism import prompts as P
        from prism.schema import Content
        c = Content(displayServiceName="뉴스", title="제목", subtitle="", body="본문")
        solar = P.item_system(c, "solar-pro3-260323")
        self.assertIn("CRITICAL", solar)
        self.assertIn("자가 검증", solar)
        claude = P.item_system(c, "anthropic/claude-sonnet-4.6")
        self.assertIn("<background>", claude)
        self.assertNotIn("CRITICAL", claude)          # Claude 리터럴리즘: 과격 지시 금지(문서 §5)
        gpt = P.item_system(c, "gpt-5.4")
        self.assertIn("<output_contract>", gpt)
        for sys_p in (solar, claude, gpt):            # 공통: 코어 규칙 + 사전 + 골드 예시
            self.assertIn("인용 출처 vs 핵심 주체", sys_p)
            self.assertIn("노동·사회 이슈", sys_p)     # 확정 표기 + 예시 A
            self.assertIn("Business and Finance", sys_p)
            self.assertIn("인터뷰", sys_p)             # 범용② 주입

    def test_intent_dictionary_contract(self):
        from prism import dictionaries as D
        cats = D.intent_categories_for("뉴스")
        self.assertIn("노동·사회 이슈", cats)          # 확정 1: 구 표기 교체
        self.assertNotIn("노동 이슈 보도", cats)
        for v in D.INTENT_FORM_UNIVERSAL:             # 확정 4: 범용② 8종 주입·검증 포함
            self.assertIn(v, cats)
        self.assertEqual(len(D.INTENT_FORM_UNIVERSAL), 8)


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


class TestAdminTiers(unittest.TestCase):
    """권한 2단계: 운영 관리자(허용목록) vs 팀 관리자(생성자·위임 · 팀 관리만)."""
    def test_sys_admin_allowlist_strict_and_fallback(self):
        from prism import serve
        orig = serve.admin_emails
        serve.admin_emails = lambda: {"ops@corp.com"}
        self.addCleanup(lambda: setattr(serve, "admin_emails", orig))
        self.assertTrue(serve.is_sys_admin_user("u1", "t1", "ops@corp.com"))
        self.assertFalse(serve.is_sys_admin_user("u1", "t1", "member@corp.com"))  # 팀 관리자여도 시스템 메뉴 불가
        serve.admin_emails = lambda: set()          # 허용목록 미설정 → 기존 관리자 로직 폴백
        orig_admin = serve.is_admin_user
        serve.is_admin_user = lambda uid, team, email="": True
        self.addCleanup(lambda: setattr(serve, "is_admin_user", orig_admin))
        self.assertTrue(serve.is_sys_admin_user("u1", "t1", "member@corp.com"))

    def test_local_store_clear_helpers(self):
        import tempfile
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        st.save_feedback("h1", "뉴스", "t", "good", "review", "", 1.0, reviewer="복실")
        st.clear_team_feedback()
        self.assertEqual(st.feedback_map(), {})


class TestFeedbackOrchestrator(unittest.TestCase):
    def test_route_fallback_splits_elements_by_stage(self):
        from prism import feedback_loop as FL
        from prism.llm import LLMClient
        fb = {"note": "카테고리가 틀렸고 등급도 과했다", "elements": ["category", "grade"], "title": "T"}
        items = FL.route_feedback(LLMClient(mock=True), fb)     # mock → 선택 요소 폴백(무손실)
        self.assertEqual([(i["element"], i["stage"]) for i in items],
                         [("category", "analyze"), ("grade", "judge")])

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
