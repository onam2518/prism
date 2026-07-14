"""임베딩 기반 결정론 분류기 (LLM 의존을 줄인 라우팅·카테고리 분류)."""
from __future__ import annotations
from . import dictionaries as D
from .embed import rank_by_cosine


# 1) 인텐트
# 관점 축(논조 판정)은 라벨 임베딩 kNN으로 판별 불가 → 후보에서 제외하고 LLM 판정을 병합 보존(harness).
PERSPECTIVE_INTENTS = ("옹호·지지", "반박·비판")


def intent_category_anchors(emb, display_name: str) -> dict:
    cats = [c for c in D.intent_categories_for(display_name) if c not in PERSPECTIVE_INTENTS]
    return {c: emb.embed(c, is_query=False) for c in cats}


def merge_perspective(emb_intents: list, llm_intents: list, cap: int = 2) -> list:
    """임베딩 kNN 인텐트에 LLM의 관점 축 판정을 병합. 논조는 kNN이 못 보는 축이라 LLM 값을 보존한다."""
    persp = [v for v in (llm_intents or []) if v in PERSPECTIVE_INTENTS]
    if not persp:
        return list(emb_intents or [])
    base = [c for c in (emb_intents or []) if c not in PERSPECTIVE_INTENTS]
    return (base[: max(0, cap - 1)] + persp[:1])[:cap]


def intent_category_classify(emb, content, top_k=2, min_margin=0.0) -> tuple[list, float]:
    anchors = intent_category_anchors(emb, content.displayServiceName)
    q = emb.embed(_content_text(content), is_query=True)
    ranked = rank_by_cosine(q, anchors)
    if not ranked:
        return [], 0.0
    margin = ranked[0][1] - (ranked[top_k][1] if len(ranked) > top_k else 0.0)
    picks = [c for c, s in ranked[:top_k]]
    return picks, round(margin, 4)


class QualityExemplars:
    """라벨된 예시(gold)로 구성한 kNN 인덱스.
    label = {finalGrade, reasons}. 콘텐츠를 임베딩해 최근접 예시들로 투표."""

    def __init__(self, emb):
        self.emb = emb
        self.items = []  # (vec, finalGrade, reasons)

    def fit(self, gold_rows: list):
        from .schema import Content
        for row in gold_rows:
            c = Content.from_dict(row["content"])
            v = self.emb.embed(_content_text(c), is_query=False)
            exp = row.get("expected", {})
            self.items.append((v, exp.get("finalGrade", "G"),
                               tuple(exp.get("reasons", []))))
        return self

    def predict(self, content, k=5):
        """반환: (verdict|None, confidence, reasons). confidence 낮으면 None→LLM 에스컬레이션."""
        if not self.items:
            return None, 0.0, []
        q = self.emb.embed(_content_text(content), is_query=True)
        from .embed import cosine
        scored = sorted(((cosine(q, v), g, r) for v, g, r in self.items),
                        key=lambda t: -t[0])[:k]
        if not scored:
            return None, 0.0, []
        topsim = scored[0][0]
        votes_R = sum(s for s, g, r in scored if g == "R")
        votes_G = sum(s for s, g, r in scored if g == "G")
        total = votes_R + votes_G or 1.0
        if votes_R >= votes_G:
            verdict, conf = "R", votes_R / total
        else:
            verdict, conf = "G", votes_G / total
        # 최근접 예시가 너무 멀면(topsim 낮음) 신뢰 못 함
        conf *= min(1.0, max(0.0, topsim))
        # 다수결 reasons (R 일 때만)
        reasons = []
        if verdict == "R":
            cnt = {}
            for s, g, r in scored:
                if g == "R":
                    for x in r:
                        cnt[x] = cnt.get(x, 0) + s
            reasons = [x for x, _ in sorted(cnt.items(), key=lambda kv: -kv[1])[:3]]
        return verdict, round(conf, 4), reasons


def _content_text(content) -> str:
    return f"{content.title} {content.subtitle} {content.body}".strip()
