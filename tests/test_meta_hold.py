"""입력 필요(hold_fields): 리드문·하위 실패 부분 보류(agents) · 품질 등급과 분리(harness) · 채우면 목록에서 빠짐(patch) · 검수 큐 라우팅(autoreview) · 채점 meta_hold_rate."""
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
    """2026-09-08 정책: 입력 필요는 품질 등급·review 를 건드리지 않는다(별개 상태)."""

    def test_mark_leaves_quality_untouched(self):
        from prism import harness as H
        from prism.schema import ItemMeta, QualityMeta
        class Ctx: pass
        ctx = Ctx(); ctx.qm = QualityMeta(finalGrade="G", reasons=[]); ctx.verdicts = []
        H._mark_meta_hold(ctx, ItemMeta(hold_fields=["summary", "entities", "intent", "content_category"]))
        self.assertEqual((ctx.qm.review, ctx.qm.finalGrade, ctx.qm.review_reason), ("auto", "G", ""))
        self.assertEqual(ctx.verdicts[0]["agent"], "MetaHold")
        self.assertTrue(ctx.verdicts[0]["evidence"].startswith("입력 필요 · 리드문"))
        ctx2 = Ctx(); ctx2.qm = QualityMeta(finalGrade="G", reasons=[]); ctx2.verdicts = []
        H._mark_meta_hold(ctx2, ItemMeta(hold_fields=["intent"]))
        self.assertIn("추출 실패: intent", ctx2.verdicts[0]["evidence"])
        ctx3 = Ctx(); ctx3.qm = QualityMeta(finalGrade="G", reasons=[]); ctx3.verdicts = []
        H._mark_meta_hold(ctx3, ItemMeta())                     # 보류 없음 → 기록도 없음
        self.assertEqual(ctx3.verdicts, [])


class TestItemGateRemoved(unittest.TestCase):
    """품질 등급은 아이템 메타 실행 조건이 아니다 — R 이어도 추출한다."""

    def test_red_content_still_gets_item_meta(self):
        from prism import harness as H
        from prism.schema import ItemMeta, QualityMeta
        class Ctx: pass
        for grade, reasons in (("R", ["harm"]), ("R", ["ad"]), ("G", [])):
            ctx = Ctx()
            ctx.methodology = H.Methodology()
            ctx.qm = QualityMeta(finalGrade=grade, reasons=list(reasons))
            ctx.routing = type("R", (), {"content_track": "text"})()
            ctx.llm = _LLM({"item_summary": {"summary": "리드문"}, "item_intent": {"intent": ["속보·단신"]},
                            "item_entities": {"entities": ["삼성전자"]},
                            "item_category": {"content_category": ["News and Politics / Society"]}})
            ctx.content = _content(); ctx.item_meta = None; ctx.emb = None
            ctx.results = []; ctx.verdicts = []; ctx.fallbacks = []
            H.st_item(ctx)
            self.assertIsNotNone(ctx.item_meta, f"{grade}/{reasons} 에서 아이템 메타가 비었다")
            self.assertEqual(ctx.item_meta.summary, "리드문")

    def test_image_only_still_skips(self):
        from prism import harness as H
        from prism.schema import QualityMeta
        class Ctx: pass
        ctx = Ctx(); ctx.methodology = H.Methodology()
        ctx.qm = QualityMeta(finalGrade="G", reasons=[])
        ctx.routing = type("R", (), {"content_track": "image_only"})()
        ctx.item_meta = None
        H.st_item(ctx)
        self.assertIsNone(ctx.item_meta)


class TestQueueRouting(unittest.TestCase):
    """입력 필요만 남은 건(품질은 auto·G)도 사람 검수 큐에 들어와야 한다."""

    def test_needs_human(self):
        from prism import autoreview as AR
        hold = {"quality_meta": {"review": "auto", "finalGrade": "G"},
                "item_meta": {"hold_fields": ["summary"]}}
        judg = {"quality_meta": {"review": "yellow", "finalGrade": ""}, "item_meta": {"hold_fields": []}}
        clean = {"quality_meta": {"review": "auto", "finalGrade": "G"}, "item_meta": {"hold_fields": []}}
        self.assertTrue(AR._needs_human(hold))
        self.assertTrue(AR._needs_human(judg))
        self.assertFalse(AR._needs_human(clean))


class TestReleaseOnPatch(unittest.TestCase):
    def _store(self):
        from prism.store import Store
        return Store(os.path.join(tempfile.mkdtemp(), "t.db"))

    def _save(self, st, hold, review="auto", reason=""):
        from prism.store import content_hash
        content = {"displayServiceName": "뉴스", "title": "보류 콘텐츠", "subtitle": "", "body": "본문"}
        out = {"content_ref": dict(content),
               "quality_meta": {"finalGrade": "G", "reasons": [], "review": review, "review_reason": reason},
               "item_meta": {"summary": "s", "entities": ["a"], "intent": [], "content_category": [], "hold_fields": hold},
               "trace": {"model": "m"}}
        st.save_result(content, out, "run1")
        return content_hash(content)

    def test_patch_fills_hold_fields(self):
        from prism import reviewops as RO, serve
        st = self._store(); ch = self._save(st, ["intent", "content_category"])
        orig = serve.get_store; serve.get_store = lambda: st
        self.addCleanup(lambda: setattr(serve, "get_store", orig))
        RO.patch_content_meta(ch, {"intent": ["속보·단신"]}, reviewer="복실")
        self.assertEqual(st.get_item_meta(ch)["hold_fields"], ["content_category"])   # 하나 남음
        RO.patch_content_meta(ch, {"content_category": ["News and Politics / Society"]}, reviewer="복실")
        self.assertEqual(st.get_item_meta(ch)["hold_fields"], [])                     # 전부 채움

    def test_patch_never_flips_quality_review(self):
        """입력 필요를 채워도 품질 판정(yellow)은 그대로 — 두 상태는 서로 건드리지 않는다."""
        from prism import reviewops as RO, serve
        st = self._store()
        ch = self._save(st, ["intent"], review="yellow", reason="사람 검수 필요(저신뢰)")
        orig = serve.get_store; serve.get_store = lambda: st
        self.addCleanup(lambda: setattr(serve, "get_store", orig))
        RO.patch_content_meta(ch, {"intent": ["속보·단신"]}, reviewer="복실")
        qm = st.recent(10)[0]["quality_meta"]
        self.assertEqual((qm["review"], qm["review_reason"]), ("yellow", "사람 검수 필요(저신뢰)"))


class TestScoreHoldRate(unittest.TestCase):
    def test_meta_hold_rate(self):
        from prism import abtest
        rows = [{"content": {}, "expected": {"finalGrade": "G", "reasons": []}}] * 2
        outs = [{"quality_meta": {"finalGrade": "G", "reasons": [], "review": "auto"}, "trace": {}, "item_meta": {"hold_fields": ["summary"]}},
                {"quality_meta": {"finalGrade": "G", "reasons": []}, "trace": {}, "item_meta": {"hold_fields": []}}]
        self.assertEqual(abtest.score(rows, outs)["meta_hold_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
