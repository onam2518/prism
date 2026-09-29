"""정답셋 인텐트 재확정(공통 68 전환 · 2026-09-29): 일괄 정리 · 측정 제외 · 한 건 확정.

실행: python3 -m unittest tests.test_golden_intent_reconfirm
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mk():
    from prism import serve
    from prism.store import Store, content_hash
    st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
    serve._STORE = st
    rows = {
        "a": {"displayServiceName": "뉴스", "title": "일대일", "subtitle": "", "body": "본문 a"},
        "b": {"displayServiceName": "커뮤니티", "title": "조건부", "subtitle": "", "body": "본문 b"},
        "c": {"displayServiceName": "뉴스", "title": "이미 확정", "subtitle": "", "body": "본문 c"},
        "d": {"displayServiceName": "뉴스", "title": "인텐트 없음", "subtitle": "", "body": "본문 d"},
    }
    st.upsert_golden(content_hash(rows["a"]), rows["a"], {"finalGrade": "G", "reasons": [], "intent": ["생활·실용정보", "실용 정보", "속보·사건 추적"]})
    st.upsert_golden(content_hash(rows["b"]), rows["b"], {"finalGrade": "G", "reasons": [], "intent": ["의견·토론"]})
    st.upsert_golden(content_hash(rows["c"]), rows["c"], {"finalGrade": "G", "reasons": [], "intent": ["심층 분석"], "intent_review": "confirmed"})
    st.upsert_golden(content_hash(rows["d"]), rows["d"], {"finalGrade": "R", "reasons": ["ad"]})
    return serve, st, {k: content_hash(v) for k, v in rows.items()}


def _exp(st, h):
    return next(e["expected"] for e in st.golden_entries() if e["hash"] == h)


class TestMigrate(unittest.TestCase):
    def setUp(self):
        self.serve, self.st, self.h = _mk()
        self.addCleanup(lambda: setattr(self.serve, "_STORE", None))

    def test_one2one_substituted_conditional_marked_confirmed_kept(self):
        from prism import learnops as LO
        r = LO.golden_intent_migrate(None)
        self.assertEqual((r["ok"], r["total"], r["flagged"], r["substituted"], r["retired_rows"], r["already_confirmed"]),
                         (True, 4, 2, 1, 1, 1))
        a = _exp(self.st, self.h["a"])
        self.assertEqual(a["intent"], ["실용 정보", "속보·사건 추적"])           # 치환 + 중복 접힘
        self.assertEqual((a["intent_review"], a["intent_migrated"]), ("needed", ["생활·실용정보 → 실용 정보"]))
        self.assertNotIn("intent_retired", a)
        b = _exp(self.st, self.h["b"])
        self.assertEqual((b["intent"], b["intent_review"], b["intent_retired"]), (["의견·토론"], "needed", ["의견·토론"]))
        self.assertEqual(_exp(self.st, self.h["c"])["intent_review"], "confirmed")   # 확정분은 그대로
        self.assertNotIn("intent_review", _exp(self.st, self.h["d"]))                 # 인텐트 없는 정답은 대상 아님
        r2 = LO.golden_intent_migrate(None)                                           # 멱등
        self.assertEqual((r2["flagged"], r2["substituted"]), (2, 0))

    def test_needed_rows_are_excluded_from_intent_metrics(self):
        from prism import abtest
        acc = {}
        abtest.intent_tally(acc, {"intent": ["심층 분석"], "intent_review": "needed"}, {"item_meta": {"intent": ["심층 분석"]}})
        self.assertEqual((acc.get("intent_n", 0), acc.get("intent_unconfirmed")), (0, 1))
        abtest.intent_tally(acc, {"intent": ["심층 분석"], "intent_review": "confirmed"}, {"item_meta": {"intent": ["심층 분석"]}})
        abtest.intent_tally(acc, {"intent": ["심층 분석"]}, {"item_meta": {"intent": ["속보·사건 추적"]}})   # 표식 없는 옛 행은 종전대로 계수
        self.assertEqual((acc["intent_n"], acc["intent_exact"]), (2, 1))
        self.assertEqual(abtest.intent_report(acc)["intent_unconfirmed"], 1)

    def test_confirm_validates_and_counts(self):
        from prism import learnops as LO
        LO.golden_intent_migrate(None)
        bad = LO.golden_intent_confirm(self.h["b"], ["의견·토론"], team=None)          # 폐기값은 거절
        self.assertFalse(bad["ok"]); self.assertIn("의견·토론", bad["error"])
        bad2 = LO.golden_intent_confirm(self.h["b"], ["없는값"], team=None)
        self.assertFalse(bad2["ok"])
        ok = LO.golden_intent_confirm(self.h["b"], ["의견·논쟁", "의견·논쟁", " 옹호·지지 "], team=None, by="검수자")
        self.assertEqual((ok["ok"], ok["intent"]), (True, ["의견·논쟁", "옹호·지지"]))
        b = _exp(self.st, self.h["b"])
        self.assertEqual((b["intent_review"], b["intent_confirmed_by"]), ("confirmed", "검수자"))
        self.assertNotIn("intent_retired", b)
        self.assertFalse(LO.golden_intent_confirm("없는해시", ["심층 분석"], team=None)["ok"])
        lst = LO.golden_list(None)
        self.assertEqual(lst["intent_review_counts"], {"needed": 1, "confirmed": 2, "retired": 0})
        row = next(i for i in lst["items"] if i["hash"] == self.h["a"])
        self.assertEqual((row["intent_review"], row["intent"]), ("needed", ["실용 정보", "속보·사건 추적"]))


class TestFinalQueue(unittest.TestCase):
    """재확정 필요 정답은 최종검수(2차) 큐에 '인텐트 재확정' 항목으로 오른다 · 결과 행이 없는 정답은 제외."""

    def test_reconfirm_items_in_final_queue(self):
        from prism import serve, reviewops as RV, learnops as LO
        from prism.store import content_hash
        serve, st, h = _mk()
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        c = {"displayServiceName": "뉴스", "title": "일대일", "subtitle": "", "body": "본문 a"}
        out = {"content_ref": c, "item_meta": {"summary": "s", "entities": [], "intent": ["실용 정보"], "content_category": ["News and Politics"]},
               "quality_meta": {"finalGrade": "G", "reasons": []}, "trace": {"model": "m", "prompt_version": "x"}}
        serve.store_save([(c, out)], source="테스트")               # 정답 a 의 결과 행
        LO.golden_intent_migrate(None)
        q = RV.final_review_queue(None)
        items = [i for i in q["items"] if i.get("final_reason") == "인텐트 재확정"]
        self.assertEqual([i["hash"] for i in items], [content_hash(c)])   # 결과 행이 있는 a 만 · b 는 결과 없음
        self.assertEqual((items[0]["golden_intent"], items[0]["final"]), (["실용 정보", "속보·사건 추적"], ""))
        LO.golden_intent_confirm(content_hash(c), ["실용 정보"], team=None, by="최종검수자")
        q2 = RV.final_review_queue(None)
        self.assertEqual([i for i in q2["items"] if i.get("final_reason") == "인텐트 재확정"], [])


if __name__ == "__main__":
    unittest.main()
