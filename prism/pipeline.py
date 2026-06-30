"""파이프라인 호환 셰임 — 에이전트 하네스(harness.py)로 위임.

내부 오케스트레이션은 `harness.py` 의 등록부 기반 에이전트 그래프로 재설계됐다.
이 모듈은 기존 `extract()` 진입점을 보존(serve·cli·imagext 무수정)하면서 방법론을
구성해 하네스를 호출한다. 동작은 재설계 이전과 동일하다.
"""
from __future__ import annotations

from . import harness as H
from .harness import Methodology, _mock_generator   # 하위호환 재노출

__all__ = ["extract", "Methodology"]


def extract(content_dict: dict, llm, *,
            legal: bool = False, quality_split: bool = False,
            slim: bool = False, emb=None, embed_categories: bool = True,
            quality_prefilter=None, prefilter_conf: float = 0.72,
            fewshot_pool=None, yellow: bool = False, yellow_low: float = 0.45) -> dict:
    """기존 호출 계약 보존. 선언 손잡이 → Methodology, 런타임 리소스(emb·prefilter·
    fewshot_pool)는 하네스에 주입."""
    m = Methodology(legal=legal, quality_split=quality_split, slim=slim,
                    embed_categories=embed_categories, prefilter_conf=prefilter_conf,
                    yellow=yellow, yellow_low=yellow_low)
    return H.run(content_dict, llm, m, emb=emb,
                 quality_prefilter=quality_prefilter, fewshot_pool=fewshot_pool)


# 일부 도구가 pipeline._mock_generator 를 참조할 수 있어 재노출 유지
_mock_generator = _mock_generator
