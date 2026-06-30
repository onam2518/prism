"""에이전트 하네스 — 방법론(Methodology)으로 구성되는 에이전트 그래프 실행기.

기존 `pipeline.extract()` 의 흩어진 손잡이(legal·quality_split·emb·prefilter·yellow…)를
1급 **Methodology** 객체로 형식화하고, 디스패처·에이전트·검증·집계를 **등록부(REGISTRY)
기반 스테이지 그래프**로 실행한다. `pipeline.extract()` 는 기본 방법론으로 이 하네스를
호출하는 호환 셰임이 된다(검증된 동작 보존).

이 위에 평가/A·B(`abtest.py`)와 REAP 피드백 루프(`feedback_loop.py`)가 올라간다.

흐름:  Dispatch → Legal → Quality → Item  → Assemble
       (no-LLM)  (opt)    (LLM)     (LLM)    (no-LLM)
각 스테이지는 공유 컨텍스트(HCtx)를 읽고 쓴다. legal RED 면 halt → 이후 스테이지 skip.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .schema import Content, Output, Trace, LegalMeta, QualityMeta
from . import routing as R
from . import agents as A
from . import verify as V
from . import prompts as P
from . import dictionaries as D


# ── 방법론: '인입 후 어떤 방법으로 처리·판정할지'의 선언적 1급 객체 ──
@dataclass
class Methodology:
    """A/B 는 두 Methodology 를 같은 데이터셋에 돌려 비교한다. 단계별 모델·프롬프트는
    prompts.STAGE_DIRECTIVE / config.stage_models 와 연동(serve sync_prompt)."""
    name: str = "default"
    version: str = "m1"
    legal: bool = False
    quality_split: bool = False
    embed_categories: bool = True
    yellow: bool = False
    prefilter_conf: float = 0.72
    yellow_low: float = 0.45
    slim: bool = False
    stages: tuple = ("dispatch", "legal", "quality", "item")   # assemble 은 항상 종단

    def to_dict(self) -> dict:
        return {"name": self.name, "version": self.version, "legal": self.legal,
                "quality_split": self.quality_split, "embed_categories": self.embed_categories,
                "yellow": self.yellow, "prefilter_conf": self.prefilter_conf,
                "yellow_low": self.yellow_low, "slim": self.slim, "stages": list(self.stages)}

    @classmethod
    def from_dict(cls, d: dict) -> "Methodology":
        d = dict(d or {})
        if "stages" in d and d["stages"]:
            d["stages"] = tuple(d["stages"])
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── 실행 컨텍스트: 스테이지들이 공유하는 가변 상태 ──
@dataclass
class HCtx:
    content: Content
    llm: object
    methodology: Methodology
    emb: object = None                 # 런타임 리소스(능력) — 방법론이 아니라 주입
    prefilter: object = None
    fewshot_pool: object = None
    routing: object = None
    legal_meta: object = None
    qm: object = None
    item_meta: object = None
    results: list = field(default_factory=list)
    verdicts: list = field(default_factory=list)
    fallbacks: list = field(default_factory=list)
    trace: object = None
    halt: bool = False                 # legal RED → 조기 종료
    pred_v: object = None              # 임베딩 2차의견(YELLOW 판단 캐리)
    pred_c: object = None


# ── 스테이지(에이전트 그래프 노드). 각자 ctx 를 변이. ──
def st_dispatch(ctx: HCtx):
    """Dispatcher(no-LLM): 서비스 그룹·활성 메타·콘텐츠 트랙 결정."""
    ctx.routing = R.dispatch(ctx.content)
    if ctx.routing.content_track == "image_only":
        ctx.fallbacks.append("image_only: 텍스트 분류기 skip (별도 트랙)")


def st_legal(ctx: HCtx):
    """[옵션] 법령 스테이지. RED → 즉시 차단(halt)."""
    if not ctx.methodology.legal or ctx.routing.content_track == "image_only":
        return
    lm, lres = A.run_legal(ctx.llm, ctx.content)
    ctx.legal_meta = lm
    ctx.results += [r for r in lres if hasattr(r, "cost_usd")]
    ctx.fallbacks += V.verify_legal(lm)
    if lm.representative_grade == "RED":
        ctx.qm = _blocked_quality()
        ctx.fallbacks.append("legal RED → 전체 차단")
        ctx.halt = True


def st_quality(ctx: HCtx):
    """품질 메타(+ YELLOW: 저신뢰 → 사람 검수). 임베딩 사전필터 하이브리드 에스컬레이션."""
    m = ctx.methodology
    if ctx.routing.content_track == "image_only":
        ctx.qm = QualityMeta(finalGrade="G", reasons=[])
        return
    qm = None
    if ctx.prefilter is not None:
        ctx.pred_v, ctx.pred_c, pre_reasons = ctx.prefilter.predict(ctx.content)
        if ctx.pred_v is not None and ctx.pred_c >= m.prefilter_conf:    # 고신뢰 → LLM skip
            qm = QualityMeta(finalGrade=ctx.pred_v, reasons=list(pre_reasons),
                             confidence=round(ctx.pred_c, 3))
            ctx.verdicts.append({"agent": "QualityPrefilter(emb)",
                                 "evidence": f"conf={ctx.pred_c} (auto)", "fail": None})
        else:
            ctx.fallbacks.append(f"prefilter conf={ctx.pred_c} < {m.prefilter_conf} → LLM 에스컬레이션")
    if qm is None:                                                       # LLM 경로
        if m.quality_split:
            qm, qres = A.run_quality_split(ctx.llm, ctx.content, ctx.routing)
        else:
            fewshot = ctx.fewshot_pool.render(ctx.llm.model) if ctx.fewshot_pool else ""
            qm, qres = A.run_quality(ctx.llm, ctx.content, ctx.routing, fewshot=fewshot)
        ctx.results += [r for r in qres if hasattr(r, "cost_usd")]
        ctx.verdicts += [r for r in qres if isinstance(r, dict)]
        if m.yellow and ctx.pred_v is not None:                         # YELLOW 판정
            qm.confidence = round(ctx.pred_c, 3)
            if ctx.pred_v != qm.finalGrade:
                qm.review = "yellow"
                qm.review_reason = f"불일치: emb={ctx.pred_v} vs llm={qm.finalGrade}"
            elif m.yellow_low <= ctx.pred_c < m.prefilter_conf:
                qm.review = "yellow"
                qm.review_reason = f"신뢰도 중간대역(conf={round(ctx.pred_c, 3)})"
            if qm.review == "yellow":
                ctx.verdicts.append({"agent": "YellowGate",
                                     "evidence": qm.review_reason, "fail": None})
    ctx.qm = qm
    ctx.fallbacks += V.verify_quality(qm, ctx.routing.active_quality_metas)


def st_item(ctx: HCtx):
    """아이템 메타(G 또는 YELLOW). 임베딩 kNN 카테고리(2-pass) + 사전화."""
    m = ctx.methodology
    gate_item = (ctx.qm.finalGrade == "G" or ctx.qm.review == "yellow")
    if not (gate_item and ctx.routing.content_track != "image_only"):
        return
    im, ires = A.run_item(ctx.llm, ctx.content)
    ctx.item_meta = im
    ctx.results += [r for r in ires if hasattr(r, "cost_usd")]
    ctx.verdicts += [r for r in ires if isinstance(r, dict)]
    if ctx.emb is not None and m.embed_categories:
        from . import classify as C
        cats, margin = C.intent_category_classify(ctx.emb, ctx.content)
        if cats:
            im.intent = cats
            ctx.verdicts.append({"agent": "IntentCategory(emb)",
                                 "evidence": f"margin={margin}", "fail": None})
        if im.entities:
            im.content_category = C.hybrid_entity_categories(
                ctx.emb, ctx.llm, [(e, "") for e in im.entities])
            unrec = [(e, ctx.content.title) for e, c in im.content_category.items()
                     if c == "Unclassified" and not C.is_vague_entity(e)]
            if unrec:
                rec = C.recover_with_context(ctx.llm, unrec)
                for e, c in rec.items():
                    if c != "Unclassified":
                        im.content_category[e] = c
            ctx.verdicts.append({"agent": "EntityCategory(2-pass: 본질+문맥복구)",
                                 "evidence": "1차 본질 분류 → 2차 문맥 식별 복구", "fail": None})
    if im and im.content_category:
        im.content_category = D.normalize_categories(im.content_category)   # 사전화
    ctx.fallbacks += V.verify_item(im, ctx.content)


REGISTRY = {
    "dispatch": st_dispatch,
    "legal": st_legal,
    "quality": st_quality,
    "item": st_item,
}


def run(content_dict: dict, llm, methodology: Methodology = None, *,
        emb=None, quality_prefilter=None, fewshot_pool=None) -> dict:
    """방법론으로 에이전트 그래프를 실행 → 통합 메타 dict."""
    m = methodology or Methodology()
    content = Content.from_dict(content_dict)
    if llm.mock and getattr(llm, "_mock_fn", None) is None:
        llm._mock_fn = _mock_generator
    ctx = HCtx(content=content, llm=llm, methodology=m, emb=emb,
               prefilter=quality_prefilter, fewshot_pool=fewshot_pool,
               legal_meta=LegalMeta(enabled=False),
               trace=Trace(prompt_version=f"{P.quality_version()}, {P.IMETA_VERSION}"))
    for key in m.stages:
        if ctx.halt:
            break
        REGISTRY[key](ctx)
    return _assemble(ctx)


def _assemble(ctx: HCtx) -> dict:
    """Aggregator(no-LLM): 통합 + trace 집계.

    주의: emb 는 배치 공유 클라이언트라 per-content 누적비용을 여기 더하면 중복계상된다.
    임베딩 비용은 호출측(cli)에서 배치 단위로 1회만 합산한다 → 여기선 LLM 비용만."""
    t = ctx.trace
    t.agent_verdicts = [v for v in ctx.verdicts if v.get("evidence") or v.get("fail")]
    t.fallbacks = ctx.fallbacks
    t.cost_usd = round(sum(r.cost_usd for r in ctx.results), 6)
    t.tokens = {"in": sum(r.in_tok for r in ctx.results),
                "out": sum(r.out_tok for r in ctx.results)}
    t.latency_ms = {"total": sum(r.latency_ms for r in ctx.results)}
    out = Output(ctx.content.ref(), ctx.routing, ctx.legal_meta, ctx.qm, ctx.item_meta, t)
    return out.to_dict(slim=ctx.methodology.slim)


def _blocked_quality():
    return QualityMeta(finalGrade="R", reasons=["graphic"])   # 차단 표식


# ── Mock 생성기: 키 없이 배관/대시보드 검증용(정확도 아님). 키워드 휴리스틱. ──
_KW = {
    "ad": ["구매", "할인", "쿠폰", "협찬", "링크", "최저가", "광고"],
    "gambling": ["복권", "로또", "베팅", "토토", "카지노"],
    "clickbait": ["충격", "경악", "발칵", "결국", "이것", "?!"],
    "sexual": ["19금", "야한", "노출"],
    "spam": ["꼭 보세요", "공유", "감동실화"],
    "shallow": [],
}


def _mock_generator(system: str, user: str, tag: str) -> dict:
    body = user.lower()
    if tag == "quality" or tag.startswith("q:"):
        reasons = []
        for meta, kws in _KW.items():
            if any(k.lower() in body for k in kws):
                reasons.append(meta)
        m = re.search(r"body:\s*(.*)", user, re.S)
        blen = len(m.group(1).strip()) if m else 0
        if blen < 30:
            reasons.append("shallow")
        reasons = list(dict.fromkeys(reasons))
        if tag.startswith("q:"):
            return {"reasons": reasons, "evidence": "mock"}
        return {"finalGrade": "R" if reasons else "G", "reasons": reasons,
                "evidence": "mock heuristic"}

    if tag == "item":
        title = _field(user, "title")
        ents = _mock_entities(title + " " + _field(user, "body"))
        svc = _field(user, "displayServiceName")
        cats = D.intent_categories_for(svc)[:2]
        ecat = {}
        for e in ents:
            ecat[e] = "News and Politics / Society"
        return {"summary": f"{(ents[0] if ents else '주제')} 관련 내용을 정리",
                "entities": ents, "intent": cats, "content_category": ecat}

    if tag == "legal_route":
        codes = []
        if any(k in body for k in ["사기", "환불 안", "먹튀"]):
            codes.append({"code": "fraud", "confidence": 0.6})
        if any(k in body for k in ["명예", "허위사실"]):
            codes.append({"code": "defamation", "confidence": 0.5})
        return {"harm_types": codes}

    if tag.startswith("legal:"):
        return {"a": 20, "b": 15, "c": 10, "evidence": "mock"}
    return {}


def _field(user: str, key: str) -> str:
    m = re.search(rf"^{key}:\s*(.*)$", user, re.M)
    return m.group(1).strip() if m else ""


def _mock_entities(text: str) -> list:
    toks = re.findall(r"[가-힣A-Za-z]{2,}", text)
    stop = {"있다", "했다", "관련", "내용", "오늘", "그리고", "이번"}
    freq = {}
    for t in toks:
        if t in stop:
            continue
        freq[t] = freq.get(t, 0) + 1
    ranked = sorted(freq, key=lambda k: (-freq[k], -len(k)))
    return ranked[:3]
