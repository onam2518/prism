"""골드 문항이 뒤집는 값 = 카테고리(2026-08-13 · 종전 등급).

이 파일이 지키는 규칙은 넷이고, 전부 **문구가 아니라 동작**으로 단언한다.

1. 뒤집는 값은 등급이 아니다 · 등급·리드문·엔티티·인텐트는 참값 그대로여야 저장된
   판정 근거(quality_meta.evidence)와 화면이 어긋나지 않는다. 어긋나면 그 어긋남이
   곧 정답 해설이 된다(검수 보조가 근거를 보여주는 기능이라 실제로 샜다).
2. 뒤집은 값이 검수자에게 **보인다** · 아무도 안 보는 자리를 뒤집으면 골드는 통과율만
   높은 무의미한 장치가 된다.
3. 골드 행이 화면에서 구분되지 않는다 · 모델·버전·검수티어·원문링크가 빈 행은 골드뿐이라
   그 부재 자체가 표시였다. 지어내지 않고 원본 콘텐츠 행에서 실어 온다.
4. 안전장치가 오류에 열리지 않는다 · 원본을 못 읽으면 **출제하지 않는다**(fail-closed).

무력화 실험(각 단언이 실제로 무엇을 잡는지)은 PR 본문 참고. 특히
`test_origin_lookup_failure_stops_gold` 와 `test_unflippable_golden_is_not_offered` 는
각각 **유일한 눈**이다 · 지우면 골드가 조용히 티 나는 상태·거짓 채점으로 돌아간다.

실행: python3 -m pytest tests/test_gold_flip.py -q
"""
import json as _j
import os
import sys
import tempfile
import time as _t
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SVC = "뉴스"
TRUE_CATS = ["Sports / Golf", "Sports / Basketball"]


class GoldFlipBase(unittest.TestCase):
    def _with_store(self):
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve, st

    def _content(self, title):
        return {"displayServiceName": SVC, "title": title, "subtitle": "", "body": "본문 " + title}

    def _put_row(self, st, content, cats, *, model="m-test", version=4, review="auto",
                 url="https://example.test/x", grade="G"):
        """원본 콘텐츠 행(results). 골드는 여기서 모델·버전·검수티어·원문링크를 실어 온다."""
        from prism.store import content_hash
        ch = content_hash(content)
        im = {"summary": "리드문 " + content["title"], "entities": ["개체A"],
              "intent": ["실용 정보"], "content_category": list(cats)}
        payload = {"quality_meta": {"review": review, "finalGrade": grade, "reasons": [],
                                    "evidence": "광고성 문구 없음 · 정보 전달 중심"},
                   "item_meta": im,
                   "content_ref": dict(content, source_url=url),
                   "trace": {"model": model, "version": version}}
        c = st._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,reasons,"
                  "item_meta,payload,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  (ch, SVC, content["title"], grade, "[]", _j.dumps(im), _j.dumps(payload), _t.time()))
        c.commit()
        return ch

    def _seed_gold(self, st, title="골드 원본", cats=TRUE_CATS, **kw):
        """골든 1건 + 그 원본 콘텐츠 행 → 출제 가능한 골드 후보."""
        content = self._content(title)
        exp = {"finalGrade": "G", "reasons": [], "content_category": list(cats),
               "summary": "리드문 " + title, "entities": ["개체A"], "intent": ["실용 정보"]}
        st.register_golden(None, [{"content": content, "expected": exp}])
        return self._put_row(st, content, cats, **kw), exp

    def _gold_of(self, serve, reviewer="tester", n=1):
        """골드 1건을 뽑는다. 변형(뒤집힘 여부)은 해시 홀짝이라 제목을 바꿔 가며 찾는다."""
        items = serve._inject_gold([], reviewer)
        self.assertEqual(len(items), n)
        return items[0]

    def _seed_variant(self, st, want_flip: bool):
        """원하는 변형(뒤집힘/원본)의 골드가 나올 때까지 제목을 바꿔 가며 심는다."""
        from prism.store import content_hash
        for i in range(60):
            title = f"골드 원본 {i}"
            h = content_hash(self._content(title))
            if (int(h, 16) % 2 == 1) is want_flip:
                return self._seed_gold(st, title=title)
        raise AssertionError("변형을 만들 제목을 못 찾음")


class TestFlipTarget(GoldFlipBase):
    def test_grade_and_text_fields_are_never_flipped(self):
        """등급·리드문·엔티티·인텐트는 참값 그대로.

        무력화: `_gold_item` 이 등급을 다시 뒤집으면(종전 코드) 즉시 깨진다. 이 단언이
        없으면 '저장된 근거와 화면이 어긋나지 않는다' 는 이번 변경의 전제가 사라진다."""
        serve, st = self._with_store()
        _ch, exp = self._seed_gold(st)
        g = self._gold_of(serve)
        self.assertEqual(g["grade"], exp["finalGrade"])
        self.assertEqual(g["summary"], exp["summary"])
        self.assertEqual(g["entities"], exp["entities"])
        self.assertEqual(g["intent"], exp["intent"])

    def test_flipped_variant_changes_exactly_one_category_slot(self):
        """뒤집기 변형은 카테고리 한 자리만 바꾼다 · 개수가 달라지면 그것도 표시가 된다."""
        serve, st = self._with_store()
        _ch, exp = self._seed_variant(st, want_flip=True)
        g = self._gold_of(serve)
        self.assertTrue(g["hash"].startswith("gold:bad:"))
        true_cats = exp["content_category"]
        self.assertEqual(len(g["category"]), len(true_cats))
        diff = [i for i, c in enumerate(g["category"]) if c != true_cats[i]]
        self.assertEqual(len(diff), 1, g["category"])

    def test_wrong_category_is_a_real_taxonomy_value_with_a_different_tier1(self):
        """심는 값은 **분류 체계 안의 실제 값**이고 Tier1 이 참값과 겹치지 않는다.

        · 체계 밖 문자열이면 '모델이 낸 값' 으로 보이지 않아 골드가 티 난다.
        · 같은 Tier1 안의 이웃 값이면 '둘 다 맞다' 가 정당해져 정답이 하나로 안 정해진다."""
        from prism import dictionaries as D
        from prism.reviewops import gold_wrong_category
        valid = {f"{t1} / {t2}" for t1, t2s in D.CONTENT_CATEGORY_TIER2.items() for t2 in t2s}
        seen = set()
        for i in range(200):                                   # 해시를 바꿔 가며 200회
            h = f"{i:016x}"
            out = gold_wrong_category(TRUE_CATS, h)
            self.assertIsNotNone(out)
            changed = [c for c in out if c not in TRUE_CATS]
            self.assertEqual(len(changed), 1)
            self.assertIn(changed[0], valid)                   # 체계 안의 값
            self.assertNotEqual(changed[0].split("/")[0].strip(), "Sports")   # Tier1 불겹침
            seen.add(changed[0])
        self.assertGreater(len(seen), 5)                       # 한 값으로 굳지 않는다

    def test_wrong_category_is_deterministic_per_content(self):
        """같은 콘텐츠는 늘 같은 모양으로 보인다(폴링·검수자에 따라 흔들리면 그것도 신호)."""
        from prism.reviewops import gold_wrong_category
        a = gold_wrong_category(TRUE_CATS, "0" * 15 + "1")
        b = gold_wrong_category(TRUE_CATS, "0" * 15 + "1")
        self.assertEqual(a, b)

    def test_flip_element_constant_matches_a_real_feedback_element(self):
        """뒤집는 요소는 검수자가 실제로 지목·교정하는 요소 어휘 안에 있어야 한다."""
        from prism import feedback_loop as FL
        from prism.reviewops import GOLD_FLIP_ELEMENT
        self.assertIn(GOLD_FLIP_ELEMENT, FL.ELEMENTS)
        self.assertNotEqual(GOLD_FLIP_ELEMENT, "grade")


class TestVisibleToReviewer(GoldFlipBase):
    def test_flipped_category_reaches_every_surface_the_reviewer_reads(self):
        """뒤집은 값이 큐·검수 표·최종검수 큐 어디서나 검수자 눈앞에 온다.

        무력화: 뒤집기를 화면에 안 실리는 필드로 옮기면(예: item_meta 안쪽만 바꾸면)
        검수 표 행 단언이 깨진다."""
        serve, st = self._with_store()
        _ch, exp = self._seed_variant(st, want_flip=True)
        # 검수 대상 실 항목 1건(골드는 실 항목이 있을 때만 섞인다)
        other = self._content("평범한 콘텐츠")
        self._put_row(st, other, ["Travel / Hotels"], review="yellow")
        g = self._gold_of(serve)
        shown = g["category"]
        self.assertNotEqual(shown, exp["content_category"])

        raw = serve.raw_rows(reviewer="tester")
        grow = [r for r in raw["items"] if r["hash"].startswith("gold:")][0]
        self.assertEqual(grow["category"], shown)                       # 표 행
        self.assertEqual(grow["item_meta"]["content_category"], shown)  # 상세가 펴 보는 메타

        q = serve.review_queue({"reviewer": "tester"})
        qrow = [r for r in q["items"] if r["hash"].startswith("gold:")][0]
        self.assertEqual(qrow["category"], shown)

    def test_final_queue_gold_carries_the_flipped_category(self):
        serve, st = self._with_store()
        _ch, exp = self._seed_variant(st, want_flip=True)
        items = serve._inject_gold_final([], "F")
        g = [i for i in items if i["hash"].startswith("goldf:")][0]
        self.assertNotEqual(g["category"], exp["content_category"])
        self.assertEqual(g["grade"], exp["finalGrade"])                 # 등급은 참값


class TestIndistinguishable(GoldFlipBase):
    def _rows(self, serve, st):
        self._seed_gold(st)
        real = self._content("평범한 콘텐츠")
        self._put_row(st, real, ["Travel / Hotels"], model="m-real", version=5,
                      review="yellow", url="https://example.test/real")
        raw = serve.raw_rows(reviewer="tester")
        gold = [r for r in raw["items"] if r["hash"].startswith("gold:")]
        norm = [r for r in raw["items"] if r.get("title") == "평범한 콘텐츠"]
        self.assertEqual((len(gold), len(norm)), (1, 1))
        return gold[0], norm[0]

    def test_row_shape_differs_only_where_it_structurally_must(self):
        """검수 표 행의 키 집합이 같다. 딱 하나 남는 차이는 구조적인 것이라 이름으로 못박는다.

        슬림 목록은 본문·메타 원본을 빼고 상세 진입 시 /raw-detail 로 채운다. 골드는 그
        라우트를 탈 수 없어(합성 해시 · 저장 행을 주면 뒤집기 전 참값이 샌다) 목록에 동봉한다.
        무력화: 골드 어댑트에서 키를 하나만 빼도(종전엔 class_gap·final·assignees·
        min_reviewers·ops_hold·source_status 가 빠져 있었다) 즉시 깨진다."""
        serve, st = self._with_store()
        g, n = self._rows(serve, st)
        self.assertEqual(set(n) - set(g), set())
        self.assertEqual(set(g) - set(n),
                         {"body", "item_meta", "quality_meta", "entities_scored"})

    def test_fields_that_render_as_badges_are_filled_from_the_origin_row(self):
        """모델·버전·원문링크는 **원본 행의 값**. 빈 값이면 배지·탭이 사라져 골드가 드러난다.

        무력화: `_gold_item` 이 model/version/url 을 다시 ""·None 으로 박으면 깨진다.
        (운영 실측 2026-08-13 · 콘텐츠 1,451건 전량이 이 셋을 갖고 있다 = 빈 행은 골드뿐)"""
        serve, st = self._with_store()
        g, _n = self._rows(serve, st)
        self.assertEqual(g["model"], "m-test")
        self.assertEqual(g["version"], 4)
        self.assertEqual(g["url"], "https://example.test/x")

    def test_review_tier_is_not_hardcoded_yellow(self):
        """검수 티어도 원본 값. 종전에는 골드만 늘 'yellow' 였는데 운영 콘텐츠의 99.1%가
        'auto' 라 골드가 YELLOW 배지 하나로 100% 식별됐다."""
        serve, st = self._with_store()
        g, _n = self._rows(serve, st)
        self.assertEqual(g["review"], "auto")

    def test_patching_a_gold_row_succeeds_like_any_other_row(self):
        """골드에서만 뜨는 '저장 실패' 토스트가 없어야 한다 · 오류 자체가 골드 표시다."""
        serve, st = self._with_store()
        self._seed_gold(st)
        g = self._gold_of(serve)
        res = serve.patch_content_meta(g["hash"], {"content_category": ["Travel / Hotels"]},
                                       None, reviewer="tester")
        self.assertEqual(res, {"ok": True})

    def test_gold_patch_writes_nowhere(self):
        """골드 교정은 정답셋도 교정 로그도 건드리지 않는다.

        · 골든 원본 행의 item_meta 를 덮으면 정답이 검수자의 오답으로 바뀐다.
        · patch_log 는 learnops 의 DPO 내보내기가 before=rejected 로 그대로 쓴다.
          우리가 심은 가짜 카테고리가 학습 데이터가 된다."""
        serve, st = self._with_store()
        ch, exp = self._seed_gold(st)
        g = self._gold_of(serve)
        serve.patch_content_meta(g["hash"], {"content_category": ["Pets / Dogs"],
                                             "summary": "덮어쓰기 시도"}, None, reviewer="tester")
        self.assertEqual((st.get_item_meta(ch) or {}).get("content_category"),
                         exp["content_category"])
        self.assertEqual(st.patch_rows(), [])


class TestScoring(GoldFlipBase):
    def test_flipped_answer_is_bad_and_clean_answer_is_good(self):
        """채점: 뒤집힌 문항의 정답은 '수정필요', 원본 그대로면 '정확'."""
        for want_flip, right, wrong in ((True, "bad", "good"), (False, "good", "bad")):
            with self.subTest(flip=want_flip):
                serve, st = self._with_store()
                self._seed_variant(st, want_flip=want_flip)
                g = self._gold_of(serve)
                r = serve.apply_gold_answer({"hash": g["hash"], "verdict": right,
                                             "reviewer": "tester"})
                self.assertTrue(r["gold"]["correct"])
                serve2, st2 = self._with_store()
                self._seed_variant(st2, want_flip=want_flip)
                g2 = self._gold_of(serve2)
                r2 = serve2.apply_gold_answer({"hash": g2["hash"], "verdict": wrong,
                                               "reviewer": "tester"})
                self.assertFalse(r2["gold"]["correct"])

    def test_answer_ledger_tags_the_scheme(self):
        """원장에 회차 표기(`@category`)를 남긴다 · 표기 없는 행이 옛 '등급 뒤집기' 회차다.
        표기가 없으면 잰 것이 다른 두 회차가 한 분모에 조용히 섞인다."""
        serve, st = self._with_store()
        self._seed_gold(st)
        g = self._gold_of(serve)
        serve.apply_gold_answer({"hash": g["hash"], "verdict": "good", "reviewer": "tester"})
        rows = st._conn().execute(
            "SELECT expected, verdict, correct FROM gold_checks").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0][0].endswith("@category"), rows[0])
        self.assertTrue(rows[0][1].endswith("@category"), rows[0])
        # 표기는 정오 판정을 바꾸지 않는다(양쪽에 같은 표기를 붙인다)
        self.assertEqual(bool(rows[0][2]), rows[0][0] == rows[0][1])

    def test_response_verdict_labels_stay_untagged(self):
        """화면이 읽는 응답의 expected 는 종전 그대로 good|bad(토스트 문구 계약 불변)."""
        serve, st = self._with_store()
        self._seed_gold(st)
        g = self._gold_of(serve)
        r = serve.apply_gold_answer({"hash": g["hash"], "verdict": "good", "reviewer": "tester"})
        self.assertIn(r["gold"]["expected"], ("good", "bad"))


class TestFailClosed(GoldFlipBase):
    def test_golden_without_origin_row_is_not_offered(self):
        """원본 콘텐츠 행이 없는 골든은 출제하지 않는다 · 모델·원문링크가 빈 행이 곧 골드 표시.
        (운영에서 실제로 생긴다: clear_contents 는 콘텐츠만 지우고 골든은 남긴다 ·
        2026-08-13 실측 골든 240건 중 22건이 이 상태)"""
        serve, st = self._with_store()
        content = self._content("원본 없는 골든")
        st.register_golden(None, [{"content": content,
                                   "expected": {"finalGrade": "G", "reasons": [],
                                                "content_category": TRUE_CATS, "summary": "s"}}])
        self.assertEqual(serve._inject_gold([], "tester"), [])
        self.assertEqual(serve._inject_gold_final([], "F"), [])

    def test_origin_row_without_model_is_not_offered(self):
        """모델을 못 읽는 원본(미실행 행)도 마찬가지 · 빈 배지를 내보내느니 안 낸다."""
        serve, st = self._with_store()
        self._seed_gold(st, model="")
        self.assertEqual(serve._inject_gold([], "tester"), [])

    def test_origin_lookup_failure_stops_gold(self):
        """**유일한 눈**: 원본 조회가 터지면 출제하지 않는다(안전장치가 오류에 열리면 안 된다).
        지우지 말 것 · 여기가 열리면 조회가 흔들릴 때마다 골드가 빈 배지로 나간다."""
        serve, st = self._with_store()
        self._seed_gold(st)

        def boom(*_a, **_k):
            raise RuntimeError("조회 실패")

        st.origin_meta_for = boom
        self.assertEqual(serve._inject_gold([], "tester"), [])
        self.assertEqual(serve._inject_gold_final([], "F"), [])

    def test_store_without_the_lookup_stops_gold(self):
        """구계약 스토어(조회 자체가 없음)도 같은 결론 · 있는 척하지 않는다."""
        serve, st = self._with_store()
        self._seed_gold(st)
        del st.__class__.origin_meta_for
        self.addCleanup(lambda: setattr(
            st.__class__, "origin_meta_for", _ORIGIN_META_FOR))
        self.assertEqual(serve._inject_gold([], "tester"), [])

    def _clear_category(self, st):
        """화면에 보일 카테고리를 정답·원본 산출 양쪽에서 없앤다(같은 해시 = 같은 변형).
        정답이 비면 원본 산출로 채워 보여 주므로(reviewops.gold_shown_meta) 둘 다 비워야
        '보이는 카테고리가 없는' 상태가 된다."""
        rows = st.get_golden(None)
        self.assertEqual(len(rows), 1)
        content = rows[0]["content"]
        st.register_golden(None, [{"content": content,
                                   "expected": dict(rows[0]["expected"], content_category=[])}])
        self._put_row(st, content, [])
        return content

    def test_review_golden_without_corrections_still_shows_meta(self):
        """검수 유래 골든의 정답은 사람이 고친 축만 담는다(learnops.build_golden_from_reviews).
        빈 축을 그대로 내보내면 리드문·엔티티가 골드 문항에서만 사라져 그 부재가 골드 표시가
        된다 — 없는 축은 원본 콘텐츠 행의 산출로 채워 보낸다(지어내지 않는다)."""
        serve, st = self._with_store()
        content = self._content("교정 없는 검수 골든")
        st.register_golden(None, [{"content": content,
                                   "expected": {"finalGrade": "G", "reasons": []}}])
        self._put_row(st, content, TRUE_CATS)
        g = self._gold_of(serve)
        self.assertEqual(g["summary"], "리드문 교정 없는 검수 골든")
        self.assertEqual(g["entities"], ["개체A"])
        self.assertEqual(g["intent"], ["실용 정보"])
        self.assertEqual(len(g["category"]), len(TRUE_CATS))

    def test_unflippable_golden_is_not_offered(self):
        """**유일한 눈**: 카테고리가 없어 뒤집을 수 없는 골든은 '뒤집기' 변형으로 내보내지 않는다.
        내보내면 화면은 멀쩡한데 정답만 '수정필요' 가 되어 채점이 거짓말을 한다."""
        serve, st = self._with_store()
        self._seed_variant(st, want_flip=True)          # 뒤집기 변형인 골든을 심고
        self._clear_category(st)                        # 보이는 카테고리를 지운다
        self.assertEqual(serve._inject_gold([], "tester"), [])

    def test_clean_variant_without_category_is_still_offered(self):
        """반대로 '원본 그대로' 변형은 카테고리가 없어도 출제된다(뒤집을 것이 없어도 정답은 '정확')."""
        serve, st = self._with_store()
        self._seed_variant(st, want_flip=False)
        self._clear_category(st)
        items = serve._inject_gold([], "tester")
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["hash"].startswith("gold:ok:"))
        self.assertEqual(items[0]["category"], [])


from prism.store import Store as _Store           # noqa: E402  (원복용 원본 참조)
_ORIGIN_META_FOR = _Store.origin_meta_for


if __name__ == "__main__":
    unittest.main()
