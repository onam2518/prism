"""검수 수정·골든셋의 엔티티 포함: 엔티티 교정 왕복(patch → 조회) + 골든 레코드 포함 +
정규화(공백·중복·비문자열) 계약.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
계약: /patch-meta(patch_content_meta) 가 entities 패치를 받으면 문자열 목록으로 정규화해
item_meta 에 반영하고 patch_log(element=entities)에 전/후를 남긴다. 골든 승격
(build_golden_from_reviews) 정답에는 **검수자가 고친 축만** 담기고(교정 없으면 키 자체가 없다),
관리자 등록(register_golden) 정답에는 entities 가 포함된다.
정합성(grade) 지표는 기존대로 finalGrade·reasons 만 비교(엔티티는 저장만 확장).
"""
import json as _j
import os
import sys
import tempfile
import time as _t
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class EntityEditBase(unittest.TestCase):
    def _with_store(self):
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve, st

    def _put_reviewed(self, st, title, verdicts, cats=("Sports",), entities=()):
        from prism.store import content_hash
        content = {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "본문 " + title}
        ch = content_hash(content)
        im = {"summary": title, "entities": list(entities), "intent": [], "content_category": list(cats)}
        payload = {"quality_meta": {"review": "yellow", "finalGrade": "G", "reasons": []},
                   "item_meta": im, "content_ref": dict(content)}
        c = st._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,reasons,item_meta,payload,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (ch, "뉴스", title, "G", "[]", _j.dumps(im), _j.dumps(payload), _t.time()))
        c.commit()
        for rv, v in verdicts:
            st.save_feedback(ch, "뉴스", title, v, "review", "", _t.time(), reviewer=rv)
        return ch


class TestEntityPatchRoundtrip(EntityEditBase):
    def test_patch_and_read_back(self):
        serve, st = self._with_store()
        ch = self._put_reviewed(st, "엔티티 고칠 건", [("A", "good")], entities=("오답개체",))
        r = serve.patch_content_meta(ch, {"entities": ["손흥민", "토트넘"]}, reviewer="복실")
        self.assertTrue(r["ok"])
        self.assertEqual(st.get_item_meta(ch).get("entities"), ["손흥민", "토트넘"])
        # 목록 응답(raw_rows · 슬림)에도 즉시 반영 · 메타 원본은 상세(raw_detail)가 노출
        row = next(x for x in serve.raw_rows(limit=50)["items"] if x["hash"] == ch)
        self.assertEqual(row["entities"], ["손흥민", "토트넘"])
        self.assertEqual(serve.raw_detail(ch)["item"]["item_meta"].get("entities"), ["손흥민", "토트넘"])
        self.assertGreaterEqual(st.patches_today("복실"), 1)      # 교정 이력(감사·미션 fill1) 기록

    def test_patch_normalizes_input(self):
        serve, st = self._with_store()
        ch = self._put_reviewed(st, "지저분한 입력", [("A", "good")])
        # 공백 트림 · 빈 값·None 제거 · 중복 제거(순서 보존) · 비문자열 강제 변환
        r = serve.patch_content_meta(ch, {"entities": [" 손흥민 ", "", "손흥민", 123, None, "토트넘"]},
                                     reviewer="복실")
        self.assertTrue(r["ok"])
        self.assertEqual(st.get_item_meta(ch).get("entities"), ["손흥민", "123", "토트넘"])

    def test_patch_non_list_coerced(self):
        serve, st = self._with_store()
        ch = self._put_reviewed(st, "문자열 입력", [("A", "good")])
        self.assertTrue(serve.patch_content_meta(ch, {"entities": "손흥민"}, reviewer="복실")["ok"])
        self.assertEqual(st.get_item_meta(ch).get("entities"), ["손흥민"])
        self.assertTrue(serve.patch_content_meta(ch, {"entities": []}, reviewer="복실")["ok"])  # 전체 삭제 허용
        self.assertEqual(st.get_item_meta(ch).get("entities"), [])

    def test_patch_log_element_is_entities(self):
        serve, st = self._with_store()
        ch = self._put_reviewed(st, "이력 확인", [("A", "good")], entities=("옛개체",))
        serve.patch_content_meta(ch, {"entities": ["새개체"]}, reviewer="복실")
        pr = next(p for p in st.patch_rows() if p.get("hash") == ch)
        self.assertEqual(pr.get("element"), "entities")            # 요소 귀속(FL.ELEMENTS 와 동일 id)
        self.assertEqual((pr.get("before") or {}).get("entities"), ["옛개체"])
        self.assertEqual((pr.get("after") or {}).get("entities"), ["새개체"])


class TestGoldenIncludesEntities(EntityEditBase):
    def test_uncorrected_meta_is_not_copied_into_golden(self):
        """교정이 없으면 메타 4축은 정답에 담기지 않는다.

        모델 산출(item_meta)을 그대로 정답으로 베끼면 메타 F1 이 '현행 모델과의 근접도'가
        되어 모델 비교에서 현행 모델이 자동으로 유리해진다. 키가 없으면 meta_tally 가
        그 행을 분모에서 뺀다(측정 안 함 > 거짓 측정)."""
        from prism import metaeval as ME
        serve, st = self._with_store()
        ch = self._put_reviewed(st, "합의 승격 건", [("A", "good"), ("B", "good")],
                                entities=("손흥민", "토트넘"))
        g = serve.build_golden_from_reviews(None)
        self.assertTrue(g["ok"])
        self.assertIn(ch, st.golden_hashes())                       # 승격 자체는 그대로
        exp = next(x for x in st.get_golden(None)
                   if x["content"].get("title") == "합의 승격 건")["expected"]
        for k in ("entities", "intent", "summary", "content_category"):
            self.assertNotIn(k, exp)
        self.assertEqual(exp.get("finalGrade"), "G")                # 등급·사유는 판정으로 확인된 값
        self.assertEqual(g["meta_n"], {"intent": 0, "content_category": 0,
                                       "summary": 0, "entities": 0})
        acc = {}
        ME.meta_tally(acc, exp, {"item_meta": {"entities": ["아무개"], "content_category": ["News"],
                                               "summary": "아무 리드문"}})
        self.assertEqual((acc.get("ent_n", 0), acc.get("cat_n", 0), acc.get("sum_n", 0)), (0, 0, 0))

    def test_patched_entities_become_golden_answer(self):
        serve, st = self._with_store()
        ch = self._put_reviewed(st, "고쳐서 편입 건", [("A", "good"), ("B", "bad")],
                                entities=("오답개체",))
        serve.patch_content_meta(ch, {"entities": ["정답개체"], "content_category": ["News"]},
                                 reviewer="리드")
        serve.set_final_verdict(ch, "good", by="리드", team=None)   # 고쳐서 편입
        g = serve.build_golden_from_reviews(None)
        exp = next(x for x in st.get_golden(None)
                   if x["content"].get("title") == "고쳐서 편입 건")["expected"]
        self.assertEqual(exp.get("entities"), ["정답개체"])          # 수정본이 곧 정답
        self.assertEqual(exp.get("content_category"), ["News"])
        self.assertNotIn("summary", exp)                            # 안 고친 축은 정답에서 빠진다
        self.assertNotIn("intent", exp)
        self.assertEqual(g["meta_n"], {"intent": 0, "content_category": 1,
                                       "summary": 0, "entities": 1})   # 축별 정답 수 = 메타 F1 분모

    def test_register_golden_normalizes_entities(self):
        serve, st = self._with_store()
        # 타 테스트가 흘린 SUPABASE_* env 로 관리자 게이트가 켜지지 않게 로컬(sqlite) 모드 강제
        prev = os.environ.get("PRISM_BACKEND")
        os.environ["PRISM_BACKEND"] = "sqlite"
        self.addCleanup(lambda: (os.environ.__setitem__("PRISM_BACKEND", prev) if prev is not None
                                 else os.environ.pop("PRISM_BACKEND", None)))
        rows = [{"content": {"displayServiceName": "뉴스", "title": "수동 등록", "subtitle": "", "body": "b"},
                 "expected": {"finalGrade": "G", "content_category": ["News"],
                              "entities": [" 손흥민 ", "", "손흥민"]}},
                {"content": {"displayServiceName": "뉴스", "title": "엔티티 없음", "subtitle": "", "body": "b2"},
                 "expected": {"finalGrade": "G", "content_category": ["News"]}}]
        r = serve.register_golden("uid", None, rows)
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 2)
        by_title = {x["content"]["title"]: x["expected"] for x in st.get_golden(None)}
        self.assertEqual(by_title["수동 등록"].get("entities"), ["손흥민"])
        self.assertEqual(by_title["엔티티 없음"].get("entities"), [])   # 키 없음 → 빈 목록으로 통일


class TestGradeMetricUnaffected(EntityEditBase):
    def test_score_ignores_entities(self):
        # 정합성(grade) 채점은 finalGrade·reasons 만 비교 · 엔티티가 달라도 지표 불변(저장만 확장)
        from prism import abtest
        rows = [{"content": {"title": "t"}, "expected": {"finalGrade": "G", "reasons": [],
                                                         "entities": ["정답개체"]}}]
        outs = [{"quality_meta": {"finalGrade": "G", "reasons": []},
                 "item_meta": {"entities": ["엉뚱개체"]}, "trace": {}}]
        m = abtest.score(rows, outs)
        self.assertEqual(m["grade_accuracy"], 1.0)
        self.assertEqual(m["reason_jaccard"], 1.0)


if __name__ == "__main__":
    unittest.main()
