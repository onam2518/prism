"""인텐트 정의 주입 + 판정 근거(evidence) 보존 회귀 테스트 (2026-08-12).

배경(운영 실측):
- 검수 지적 730건 중 인텐트가 60.4%(441건)로 1위. 오탐 1위 '심층 분석'·4위 '실용 정보'가
  모두 정의 없는 8종에 속했다. 프롬프트는 범용① 을 이름만 나열했다.
- quality_meta 의 판정 근거는 운영(supabase)에 0% 저장돼 있었다. contents 행은
  model·version 만 남기고 trace.agent_verdicts 를 버린다(전체 payload 보관은 sqlite 뿐).
  quality_meta 는 jsonb 로 실리므로 거기 담으면 마이그레이션 없이 남는다.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import os
import sys
import unittest
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import agents as A
from prism import dictionaries as D
from prism import meta_prompts as M
from prism import prompts as P
from prism.schema import QualityMeta


def _content():
    """quality_user 가 속성으로 읽는 최소 콘텐츠."""
    return type("C", (), {"displayServiceName": "뉴스", "title": "제목",
                          "subtitle": "", "body": "본문"})()


class TestIntentDefsReachPrompt(unittest.TestCase):
    """정의가 사전에 있는 것만으로는 부족하다 — 프롬프트까지 도달해야 한다."""

    def test_every_universal_intent_has_a_definition(self):
        missing = [v for v in D.INTENT_CATEGORIES_UNIVERSAL if not D.INTENT_VALUE_DEFS.get(v)]
        self.assertEqual(missing, [], f"범용① 정의 누락: {missing}")

    def test_definitions_are_injected_into_the_prompt(self):
        # 수정 전에는 " / ".join(...) 로 이름만 나열해 정의가 프롬프트에 닿지 않았다.
        block = M.intent_dictionary_text("뉴스").split("[범용 ②")[0]
        for v in D.INTENT_CATEGORIES_UNIVERSAL:
            self.assertIn(v, block, f"{v} 가 범용① 블록에 없다")
            self.assertIn(D.INTENT_VALUE_DEFS[v][:18], block, f"{v} 정의문이 주입되지 않았다")

    def test_oversampled_values_carry_a_do_not_assign_clause(self):
        """오탐 상위 값은 '미부여' 경계를 반드시 갖는다(붙이지 말아야 할 때를 적어야 준다)."""
        for v in ("심층 분석", "실용 정보", "학술·전문", "속보·사건 추적"):
            self.assertIn("미부여", D.INTENT_VALUE_DEFS[v], f"{v} 에 미부여 경계가 없다")

    def test_no_contradiction_with_call_rules_on_editorial(self):
        """사설·칼럼 → 의견·논쟁 우선은 260715 회의 결정이다. 정의가 이를 뒤집으면 안 된다."""
        d = D.INTENT_VALUE_DEFS["의견·논쟁"]
        self.assertIn("사설·칼럼", d)
        self.assertNotIn("미부여: 한쪽 입장만", d)     # 초안이 회의 결정과 충돌하던 문구

    def test_prompt_version_bumped(self):
        self.assertNotIn("v16", P.IMETA_VERSION.split(" ")[0])


class TestEvidencePersisted(unittest.TestCase):
    """근거는 재생성하지 않고 저장한다 — 나중에 다시 물으면 사후 추측이 된다."""

    def test_quality_meta_carries_evidence_field(self):
        self.assertIn("evidence", asdict(QualityMeta()))

    def test_evidence_survives_serialization(self):
        qm = QualityMeta(finalGrade="R", reasons=["ad"], evidence="광고 문구가 본문 절반")
        self.assertEqual(asdict(qm)["evidence"], "광고 문구가 본문 절반")

    def test_run_quality_stores_model_evidence(self):
        class _LLM:
            def complete_json(self, sys, user, tag=""):
                return {"finalGrade": "R", "reasons": ["ad"], "evidence": "협찬 고지 없음"}, object()

        class _R:
            active_quality_metas = ["ad"]
            service_group = "뉴스"
        qm, _ = A.run_quality(_LLM(), _content(), _R())
        self.assertEqual(qm.evidence, "협찬 고지 없음")   # 수정 전: qm 에 안 담기고 버려졌다

    def test_evidence_is_not_invented_when_absent(self):
        class _LLM:
            def complete_json(self, sys, user, tag=""):
                return {"finalGrade": "G", "reasons": []}, object()

        class _R:
            active_quality_metas = []
            service_group = "뉴스"
        qm, _ = A.run_quality(_LLM(), _content(), _R())
        self.assertEqual(qm.evidence, "")   # 없으면 빈 값. 지어내지 않는다

    def test_evidence_list_or_dict_is_flattened(self):
        self.assertEqual(A._evidence_text({"evidence": ["a", "b"]}), "a · b")
        self.assertEqual(A._evidence_text({"evidence": {"k": "v"}}), "k: v")
        self.assertEqual(A._evidence_text({}), "")

    def test_evidence_is_capped(self):
        long = {"evidence": "가" * (A.EVIDENCE_MAX + 500)}
        self.assertEqual(len(A._evidence_text(long)), A.EVIDENCE_MAX)

    def test_failed_call_still_keeps_evidence_and_holds_verdict(self):
        class _LLM:
            def complete_json(self, sys, user, tag=""):
                return {"_fail": "timeout", "evidence": "부분 응답"}, object()

        class _R:
            active_quality_metas = []
            service_group = "뉴스"
        qm, _ = A.run_quality(_LLM(), _content(), _R())
        self.assertEqual(qm.review, "yellow")          # 판정 보류 계약 불변
        self.assertEqual(qm.finalGrade, "")
        self.assertEqual(qm.evidence, "부분 응답")


if __name__ == "__main__":
    unittest.main()
