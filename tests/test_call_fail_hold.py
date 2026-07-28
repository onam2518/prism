"""콜 실패 판정 보류: 메타 생성 콜이 끝내 실패한 건은 자동 G 로 유통하지 않는다.

배경(2026-07-28 실사례): 티스토리 여행글 1건에서 item_entities 콜만 5회(최초 1 + 재시도 4)
연속 실패했다. summary·intent·category 는 채워졌고 entities 만 빈 채로 등급 G ·
review=auto 로 통과 — 빈 메타가 사람 눈을 한 번도 안 거치고 정답 후보로 흘렀다.
법령 평가 실패(legal_fail)에 이미 있던 fail-open 금지 규칙을 메타 콜에도 확장한다.

실행: python3 -m pytest tests/ -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Res:
    """LLMResult 최소 대역(하네스가 읽는 필드만)."""

    def __init__(self, tag, fail_kind=None, detail=""):
        self.tag = tag
        self.fail_kind = fail_kind
        self.fail_detail = detail
        self.cost_usd = 0.0
        self.in_tok = self.out_tok = self.latency_ms = 0


class TestCallFailHold(unittest.TestCase):
    def _ctx(self, results, grade="G", review="auto", reason=""):
        from prism import routing as R
        from prism.harness import HCtx, Methodology
        from prism.schema import Content, ItemMeta, LegalMeta, QualityMeta, Trace
        c = Content(displayServiceName="티스토리", title="페루 - 푸노", body="본문")
        ctx = HCtx(content=c, llm=None, methodology=Methodology())
        ctx.routing = R.dispatch(c)
        ctx.legal_meta = LegalMeta()
        ctx.qm = QualityMeta(finalGrade=grade, reasons=[], review=review, review_reason=reason)
        ctx.item_meta = ItemMeta(summary="요약", entities=[], intent=["실용 정보"],
                                 content_category=["Travel / Travel Preparation"])
        ctx.trace = Trace()
        ctx.results = results
        return ctx

    def _assemble(self, ctx):
        from prism.harness import _assemble
        return _assemble(ctx)

    def test_failed_call_holds_auto_g(self):
        """실사례 재현: entities 콜만 실패 → 자동 G 가 아니라 사람 검수 보류."""
        ctx = self._ctx([_Res("item_summary"), _Res("item_entities", "timeout", "TimeoutError: …")])
        out = self._assemble(ctx)
        qm = out["quality_meta"]
        self.assertEqual(qm["review"], "yellow")            # 검수 큐로 들어간다
        self.assertEqual(qm["finalGrade"], "G")             # 등급 자체는 건드리지 않는다
        self.assertIn("item_entities", qm["review_reason"])
        self.assertIn("판정 보류", qm["review_reason"])
        self.assertIn("call_fail → 판정 보류(사람 검수)", out["trace"]["fallbacks"])

    def test_all_calls_ok_stays_auto(self):
        """정상 경로 불변 — 실패가 없으면 자동 승인 그대로."""
        ctx = self._ctx([_Res("item_summary"), _Res("item_entities")])
        qm = self._assemble(ctx)["quality_meta"]
        self.assertEqual(qm["review"], "auto")

    def test_multiple_failed_calls_all_named(self):
        ctx = self._ctx([_Res("item_entities", "network"), _Res("item_category", "parse_empty")])
        reason = self._assemble(ctx)["quality_meta"]["review_reason"]
        self.assertIn("item_category", reason)
        self.assertIn("item_entities", reason)

    def test_existing_reason_is_not_overwritten(self):
        """이미 다른 사유로 보류 중이면 그 사유를 유지(법령 게이트와 같은 규약)."""
        ctx = self._ctx([_Res("item_entities", "timeout")], review="auto",
                        reason="신뢰도 중간대역(conf=0.5)")
        qm = self._assemble(ctx)["quality_meta"]
        self.assertEqual(qm["review"], "yellow")
        self.assertEqual(qm["review_reason"], "신뢰도 중간대역(conf=0.5)")

    def test_already_yellow_untouched(self):
        ctx = self._ctx([_Res("item_entities", "timeout")], review="yellow", reason="불일치: …")
        qm = self._assemble(ctx)["quality_meta"]
        self.assertEqual(qm["review_reason"], "불일치: …")

    def test_r_grade_not_promoted_to_review(self):
        """R(폐기)은 아이템 메타가 버려지는 경로라 보류 대상이 아니다 — 기존 동작 유지."""
        ctx = self._ctx([_Res("item_entities", "timeout")], grade="R")
        qm = self._assemble(ctx)["quality_meta"]
        self.assertEqual(qm["review"], "auto")

    def test_fail_detail_still_reaches_trace(self):
        """#348 의 detail 전달과 함께 동작(같은 결과 목록을 두 규칙이 읽는다)."""
        ctx = self._ctx([_Res("item_entities", "timeout", "TimeoutError: read timed out")])
        fails = self._assemble(ctx)["trace"]["fails"]
        self.assertEqual(fails[0]["kind"], "timeout")
        self.assertIn("read timed out", fails[0]["detail"])


if __name__ == "__main__":
    unittest.main()
