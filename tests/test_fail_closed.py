"""fail-closed 회귀 테스트(2026-07-15 감사 P1-1·2·3).

모더레이션 파이프라인은 호출 실패·정합성 붕괴 시 유해 콘텐츠를 자동 GREEN/G 로
유통하지 않고 사람 검수로 보류(fail-closed)해야 한다.

- run_legal: 라우터/스코어러 호출 실패 → LegalMeta.failed (0점 GREEN 방지)
- verify_quality: 모델 R + 사유 전량 사전외 → 자동 G 로 뒤집지 않고 보류
- (cli 배치 예외 fallback 은 finalGrade='' 로 바뀌어 G 집계에서 빠짐)

실행: python3 -m pytest tests/test_fail_closed.py -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Res:
    """LLMResult 최소 대역(trace 집계·필터용)."""
    def __init__(self, tag=""):
        self.tag = tag
        self.cost_usd = 0.0
        self.in_tok = self.out_tok = self.latency_ms = 0
        self.fail_kind = None


class _FakeLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self._i = 0
        self.mock = True
        self.model = "fake"

    def complete_json(self, sys, user, tag=""):
        obj = self._responses[self._i] if self._i < len(self._responses) else {"_fail": "exhausted"}
        self._i += 1
        return obj, _Res(tag)


def _content():
    from prism.schema import Content
    return Content.from_dict({"displayServiceName": "뉴스", "title": "제목", "body": "본문"})


class TestRunLegalFailClosed(unittest.TestCase):
    def test_router_fail_marks_failed_not_green(self):
        from prism import agents as A
        lm, res = A.run_legal(_FakeLLM([{"_fail": "http_429"}]), _content())
        self.assertTrue(lm.failed)
        self.assertEqual(lm.harm_types, [])          # 유형 열거 불가 → 빈 채로 보류

    def test_scorer_fail_marks_failed(self):
        from prism import agents as A
        from prism import dictionaries as D
        code = next(iter(D.LEGAL_HARM_TYPES))
        llm = _FakeLLM([{"harm_types": [{"code": code, "confidence": 0.9}]}, {"_fail": "timeout"}])
        lm, res = A.run_legal(llm, _content())
        self.assertTrue(lm.failed)

    def test_clean_success_not_failed(self):
        from prism import agents as A
        from prism import dictionaries as D
        code = next(iter(D.LEGAL_HARM_TYPES))
        llm = _FakeLLM([{"harm_types": [{"code": code, "confidence": 0.9}]}, {"a": 0, "b": 0, "c": 0}])
        lm, res = A.run_legal(llm, _content())
        self.assertFalse(lm.failed)


class TestVerifyQualityFailClosed(unittest.TestCase):
    def test_R_with_all_reasons_out_of_dict_holds(self):
        from prism import verify as V
        from prism.schema import QualityMeta
        qm = QualityMeta(finalGrade="R", reasons=["___not_in_dict___"])
        V.verify_quality(qm, active_metas=[])        # 활성 없음 → 사유 전량 제거
        self.assertEqual(qm.finalGrade, "")          # 자동 G 아님
        self.assertEqual(qm.review, "yellow")        # 사람 검수 보류

    def test_R_with_valid_reason_stays_R(self):
        from prism import verify as V
        from prism.schema import QualityMeta
        qm = QualityMeta(finalGrade="R", reasons=["ad"])
        V.verify_quality(qm, active_metas=["ad"])
        self.assertEqual(qm.finalGrade, "R")

    def test_G_with_valid_reason_forced_R(self):
        from prism import verify as V
        from prism.schema import QualityMeta
        qm = QualityMeta(finalGrade="G", reasons=["ad"])
        V.verify_quality(qm, active_metas=["ad"])
        self.assertEqual(qm.finalGrade, "R")

    def test_G_no_reason_stays_G(self):
        from prism import verify as V
        from prism.schema import QualityMeta
        qm = QualityMeta(finalGrade="G", reasons=[])
        V.verify_quality(qm, active_metas=[])
        self.assertEqual(qm.finalGrade, "G")


if __name__ == "__main__":
    unittest.main()
