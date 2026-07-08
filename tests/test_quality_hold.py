"""품질 판정 보류 회귀: 호출 실패를 G(유통 가능)로 유통하지 않는다(fail-open 금지 · 2026-07-08 회의).

- 호출 실패 → finalGrade '' + review yellow(판정 보류) · 검증기가 G 로 되돌리지 않는다
- 보류 행은 fail_kind=api 로 적재돼 다음 배치에서 재실행 대상이 된다
- 엔티티 상한 절단(verify_item) 제거: 상한 없음 · 핵심만 정책과 정합

실행: python3 -m pytest tests/ -q
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FailLLM:
    model = "fake"
    mock = False

    def __init__(self, fail=True):
        self.fail = fail

    def complete_json(self, sys_p, user_p, tag=""):
        if self.fail:
            return {"_fail": "HTTP500: boom", "_fail_kind": "http"}, None
        return {"finalGrade": "G", "reasons": [], "evidence": "ok"}, None


class _Routing:
    active_quality_metas = []
    service_group = "뉴스"


class TestQualityHold(unittest.TestCase):
    def _content(self):
        from prism.schema import Content
        return Content(displayServiceName="뉴스", title="t", body="b")

    def test_call_failure_holds_grade(self):
        from prism.agents import run_quality
        qm, _ = run_quality(_FailLLM(), self._content(), _Routing())
        self.assertEqual(qm.finalGrade, "")               # G 폴백 금지
        self.assertEqual(qm.review, "yellow")
        self.assertIn("판정 보류", qm.review_reason)
        ok, _ = run_quality(_FailLLM(fail=False), self._content(), _Routing())
        self.assertEqual((ok.finalGrade, ok.review), ("G", "auto"))   # 정상 경로 불변

    def test_verifier_keeps_hold(self):
        from prism.schema import QualityMeta
        from prism.verify import verify_quality
        hold = QualityMeta(finalGrade="", reasons=[], review="yellow", review_reason="호출 실패")
        verify_quality(hold, [])
        self.assertEqual(hold.finalGrade, "")             # 보류는 정합 보정 제외
        normal = QualityMeta(finalGrade="", reasons=[])   # 보류 아님(review=auto) → 기존 보정 유지
        verify_quality(normal, [])
        self.assertEqual(normal.finalGrade, "G")

    def test_hold_rows_marked_for_rerun(self):
        from prism.store import Store, content_hash
        with tempfile.TemporaryDirectory() as d:
            st = Store(os.path.join(d, "t.db"))
            c = {"displayServiceName": "뉴스", "title": "보류건", "subtitle": "", "body": "b"}
            out = {"content_ref": {**c, "body_hash": content_hash(c)}, "item_meta": {},
                   "quality_meta": {"finalGrade": "", "review": "yellow"},
                   "trace": {"fallbacks": ["quality_fail → 판정 보류(재실행 대상)"]}}
            st.save_many([(c, out)], run_id="t")
            row = st._conn().execute("SELECT fail_kind FROM results").fetchone()
            self.assertEqual(row[0], "api")               # done_hashes(only_ok) 제외 → 재실행 대상
            self.assertNotIn(content_hash(c), st.done_hashes(only_ok=True))

    def test_entities_no_truncation(self):
        from prism.schema import ItemMeta
        from prism.verify import verify_item
        im = ItemMeta(entities=["a", "b", "c", "d", "e", " "])
        notes = verify_item(im, self._content())
        self.assertEqual(im.entities, ["a", "b", "c", "d", "e"])   # 상한 절단 없음 · 빈 값만 정제
        self.assertFalse(any("절단" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
