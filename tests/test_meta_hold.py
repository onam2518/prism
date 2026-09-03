"""메타 보류 → yellow: 리드문 재시도·하위 실패 부분 보류(agents) · yellow 표시(harness) · 채우면 해제(store·patch) · 채점 meta_hold_rate."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))



class _Res:
    fail_detail = ""


class _LLM:
    """tag → obj. obj 에 '_fail' 이 있으면 호출 실패로 본다(llm._fail 계약과 동일 키)."""
    model = "solar-pro2"
    mock = True

    def __init__(self, by_tag):
        self.by_tag, self.calls = by_tag, []

    def complete_json(self, system, user, tag=""):
        self.calls.append(tag)
        return dict(self.by_tag.get(tag, {})), _Res()


def _content(**kw):
    from prism.schema import Content
    d = {"displayServiceName": "뉴스", "title": "제목", "subtitle": "", "body": "본문 내용입니다."}
    d.update(kw)
    return Content(**d)


class TestAgentsHold(unittest.TestCase):
    def setUp(self):
        from prism import agents as AG
        AG.META_CFG = {"four_calls": True, "call_models": {}}

    def test_summary_failure_holds_all(self):
        from prism import agents as AG
        for obj in ({"_fail": "api"}, {"lead": "키 이름 틀림"}):            # 호출 실패 · 계약 키 부재
            llm = _LLM({"item_summary": obj})
            im, _ = AG.run_item(llm, _content())
            self.assertEqual(llm.calls, ["item_summary"])               # 재호출 없이 보류(비용 0)
            self.assertEqual(im.hold_fields, ["summary", "entities", "intent", "content_category"])

    def test_empty_summary_is_signal_not_hold(self):
        from prism import agents as AG
        im, _ = AG.run_item(_LLM({"item_summary": {"summary": ""}}), _content())
        self.assertEqual(im.hold_fields, [])

    def test_partial_hold_keeps_good_fields(self):
        from prism import agents as AG
        llm = _LLM({"item_summary": {"summary": "리드문"}, "item_entities": {"_fail": "api"},
                    "item_intent": {"intent": ["속보·단신"]}, "item_category": {"content_category": ["News and Politics / Society"]}})
        im, _ = AG.run_item(llm, _content())
        self.assertEqual(im.hold_fields, ["entities"])
        self.assertEqual(im.intent, ["속보·단신"]); self.assertTrue(im.content_category)


class TestHarnessMark(unittest.TestCase):
    def test_mark_sets_yellow_keeps_grade(self):
        from prism import harness as H
        from prism.schema import ItemMeta, QualityMeta
        class Ctx: pass
        ctx = Ctx(); ctx.qm = QualityMeta(finalGrade="G", reasons=[]); ctx.verdicts = []
        H._mark_meta_hold(ctx, ItemMeta(hold_fields=["summary", "entities", "intent", "content_category"]))
        self.assertEqual(ctx.qm.review, "yellow"); self.assertEqual(ctx.qm.finalGrade, "G")
        self.assertTrue(ctx.qm.review_reason.startswith("메타 보류 · 리드문"))
        self.assertEqual(ctx.verdicts[0]["agent"], "MetaHold")
        ctx2 = Ctx(); ctx2.qm = QualityMeta(finalGrade="G", reasons=[]); ctx2.verdicts = []
        H._mark_meta_hold(ctx2, ItemMeta(hold_fields=["intent"]))
        self.assertIn("추출 실패: intent", ctx2.qm.review_reason)
        ctx3 = Ctx(); ctx3.qm = QualityMeta(finalGrade="G", reasons=[]); ctx3.verdicts = []
        H._mark_meta_hold(ctx3, ItemMeta())                     # 보류 없음 → 그대로
        self.assertEqual(ctx3.qm.review, "auto")


class TestReleaseOnPatch(unittest.TestCase):
    def _store(self):
        from prism.store import Store
        return Store(os.path.join(tempfile.mkdtemp(), "t.db"))

    def _save(self, st, hold, review="yellow", reason="메타 보류 · 추출 실패: intent, content_category"):
        from prism.store import content_hash
        content = {"displayServiceName": "뉴스", "title": "보류 콘텐츠", "subtitle": "", "body": "본문"}
        out = {"content_ref": dict(content),
               "quality_meta": {"finalGrade": "G", "reasons": [], "review": review, "review_reason": reason},
               "item_meta": {"summary": "s", "entities": ["a"], "intent": [], "content_category": [], "hold_fields": hold},
               "trace": {"model": "m"}}
        st.save_result(content, out, "run1")
        return content_hash(content)

    def test_patch_fills_and_releases(self):
        from prism import reviewops as RO, serve
        st = self._store(); ch = self._save(st, ["intent", "content_category"])
        orig = serve.get_store; serve.get_store = lambda: st
        self.addCleanup(lambda: setattr(serve, "get_store", orig))
        RO.patch_content_meta(ch, {"intent": ["속보·단신"]}, reviewer="복실")
        im = st.get_item_meta(ch)
        self.assertEqual(im["hold_fields"], ["content_category"])               # 하나 남음 → yellow 유지
        self.assertEqual(st.recent(10)[0]["quality_meta"]["review"], "yellow")
        RO.patch_content_meta(ch, {"content_category": ["News and Politics / Society"]}, reviewer="복실")
        self.assertEqual(st.get_item_meta(ch)["hold_fields"], [])
        qm = st.recent(10)[0]["quality_meta"]
        self.assertEqual((qm["review"], qm["review_reason"]), ("auto", ""))   # 전부 채움 → 해제

    def test_release_does_not_touch_judgment_yellow(self):
        st = self._store(); ch = self._save(st, [], reason="R 판정이나 사유가 전부 사전 밖 · 검수 보류")
        self.assertFalse(st.release_meta_hold(ch))
        self.assertEqual(st.recent(10)[0]["quality_meta"]["review"], "yellow")


class TestScoreHoldRate(unittest.TestCase):
    def test_meta_hold_rate(self):
        from prism import abtest
        rows = [{"content": {}, "expected": {"finalGrade": "G", "reasons": []}}] * 2
        outs = [{"quality_meta": {"finalGrade": "G", "reasons": [], "review": "yellow"}, "trace": {}, "item_meta": {"hold_fields": ["summary"]}},
                {"quality_meta": {"finalGrade": "G", "reasons": []}, "trace": {}, "item_meta": {"hold_fields": []}}]
        self.assertEqual(abtest.score(rows, outs)["meta_hold_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
