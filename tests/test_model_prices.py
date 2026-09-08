"""모델별 단가: 비교표의 '비용'이 토큰 수 순위가 아니라 실제 돈이 되게 하는 규칙.

종전에는 모든 모델이 설정 단가(cfg.prices) 하나로 계산돼, 같은 토큰이면 Opus 도 mini 도
같은 값이 나왔다 — 즉 '합격 최저 비용' 배지는 비용이 아니라 토큰 수를 뽑고 있었다.

  1. 표에 단가가 있는 두 모델은 **같은 토큰 수에서 다른 비용**이 나온다.
  2. 표에 없는 모델은 비용이 None 이다(0 도, 설정 단가도 아니다). 0 이면 가장 싼 모델로
     뽑히고, 설정 단가면 다시 토큰 수 순위가 된다 — 둘 다 조용히 틀린 추천이 된다.
     그래서 '합격 최저 비용'은 그런 모델을 건너뛴다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _result_for(model, in_tok=10000, out_tok=1000):
    """모델 라우팅으로 만든 클라이언트의 단가로 LLMResult 를 만든다(토큰 수는 고정)."""
    from prism import serve as SV
    from prism.llm import LLMResult
    llm, _route = SV.llm_for_model(model, True)          # mock = 키 없이 라우팅·단가만 확인
    p = llm.cfg.prices
    return LLMResult("", in_tok, out_tok, 0, 0, price_in=p.chat_in, price_out=p.chat_out,
                     price_cache_read=p.cache_read)


class TestModelPrices(unittest.TestCase):
    def test_same_tokens_different_price_different_cost(self):
        """단가가 다른 두 모델은 같은 토큰 수에서 비용이 달라야 한다."""
        from prism import modelmeta as MM
        hi, lo = MM.prices("claude-opus-5"), MM.prices("claude-haiku-4-5")
        self.assertTrue(hi and lo and hi[0] != lo[0])    # 표 자체가 두 단가를 갖고 있다
        c_hi = _result_for("claude-opus-5").cost_usd
        c_lo = _result_for("claude-haiku-4-5").cost_usd
        self.assertIsNotNone(c_hi)
        self.assertIsNotNone(c_lo)
        self.assertGreater(c_hi, c_lo)
        self.assertAlmostEqual(c_hi, 10000 / 1e6 * hi[0] + 1000 / 1e6 * hi[1], places=9)

    def test_router_prefix_and_dot_version_hit_same_row(self):
        """`anthropic/claude-sonnet-4.6` 와 `claude-sonnet-4-6` 은 같은 단가다."""
        from prism import modelmeta as MM
        self.assertEqual(MM.prices("anthropic/claude-sonnet-4.6"), MM.prices("claude-sonnet-4-6"))
        self.assertIsNone(MM.prices("에서-없는-모델"))

    def test_unknown_price_model_costs_none_and_is_skipped(self):
        """단가 미상 모델: 비용 None · '합격 최저 비용' 후보에서 제외."""
        from prism import abtest, modelmeta as MM
        from prism.learnops import cheapest_passing_model
        self.assertIsNone(MM.prices("kimi-k3"))          # 표에 없는 모델(공시가 미확인)
        self.assertIsNone(_result_for("kimi-k3").cost_usd)
        # 트레이스에 None 이 섞여도 채점이 죽지 않고 지표도 None 으로 남는다
        m = abtest.score([{"expected": {"finalGrade": "G", "reasons": []}}],
                         [{"quality_meta": {"finalGrade": "G", "reasons": []},
                           "trace": {"cost_usd": None}}])
        self.assertIsNone(m["cost_usd"])
        # 일치율이 가장 높아도 비용을 모르면 '가장 싼 합격 모델'로 추천하지 않는다
        self.assertEqual(cheapest_passing_model(
            [{"model": "kimi-k3", "grade_accuracy": 0.99, "cost_usd": m["cost_usd"]},
             {"model": "claude-haiku-4-5", "grade_accuracy": 0.90, "cost_usd": 0.5}], 0.85),
            "claude-haiku-4-5")


if __name__ == "__main__":
    unittest.main()
