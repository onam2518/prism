"""파이프라인 오케스트레이션 + 조건부 게이팅."""
from __future__ import annotations
import re

from .schema import Content, Output, Trace, LegalMeta
from . import routing as R
from . import agents as A
from . import verify as V
from . import prompts as P
from . import dictionaries as D
from .llm import LLMClient


def extract(content_dict: dict, llm: LLMClient, *,
            legal: bool = False, quality_split: bool = False,
            slim: bool = False, emb=None, embed_categories: bool = True,
            quality_prefilter=None, prefilter_conf: float = 0.72,
            fewshot_pool=None, yellow: bool = False, yellow_low: float = 0.45) -> dict:
    """
    emb: EmbeddingClient | None: 주면 카테고리 배정을 임베딩 kNN(결정론)으로 수행.
    quality_prefilter: classify.QualityExemplars | None: 주면 품질을 임베딩 사전필터로
        먼저 시도하고, confidence >= prefilter_conf 면 LLM 을 skip(하이브리드 에스컬레이션).
    """
    content = Content.from_dict(content_dict)
    if llm.mock and llm._mock_fn is None:
        llm._mock_fn = _mock_generator

    trace = Trace(prompt_version=f"{P.quality_version()}, {P.IMETA_VERSION}")
    all_results, verdicts, fallbacks = [], [], []

    # Dispatcher (no-LLM)
    routing = R.dispatch(content)

    # 이미지 단독은 분류기 호출 전 분기
    if routing.content_track == "image_only":
        fallbacks.append("image_only: 텍스트 분류기 skip (별도 트랙)")

    # [옵션] 법령 스테이지
    legal_meta = LegalMeta(enabled=False)
    if legal and routing.content_track != "image_only":
        legal_meta, lres = A.run_legal(llm, content)
        all_results += [r for r in lres if hasattr(r, "cost_usd")]
        fallbacks += V.verify_legal(legal_meta)
        if legal_meta.representative_grade == "RED":
            # RED → 즉시 차단, 이하 스테이지 skip
            qm = _blocked_quality()
            return _assemble(content, routing, legal_meta, qm, None,
                             trace, all_results, verdicts,
                             fallbacks + ["legal RED → 전체 차단"], slim)

    # 품질 메타 (+ YELLOW: 저신뢰 → 사람 검수)
    from .schema import QualityMeta
    if routing.content_track == "image_only":
        qm = QualityMeta(finalGrade="G", reasons=[])
    else:
        qm = None
        pred_v = pred_c = None     # 임베딩 2차의견(YELLOW 판단용)
        if quality_prefilter is not None:
            pred_v, pred_c, pre_reasons = quality_prefilter.predict(content)
            # 고신뢰면 LLM skip (auto)
            if pred_v is not None and pred_c >= prefilter_conf:
                qm = QualityMeta(finalGrade=pred_v, reasons=list(pre_reasons),
                                 confidence=round(pred_c, 3))
                verdicts.append({"agent": "QualityPrefilter(emb)",
                                 "evidence": f"conf={pred_c} (auto)", "fail": None})
            else:
                fallbacks.append(f"prefilter conf={pred_c} < {prefilter_conf} → LLM 에스컬레이션")
        if qm is None:  # LLM 경로
            if quality_split:
                qm, qres = A.run_quality_split(llm, content, routing)
            else:
                fewshot = fewshot_pool.render(llm.model) if fewshot_pool else ""
                qm, qres = A.run_quality(llm, content, routing, fewshot=fewshot)
            all_results += [r for r in qres if hasattr(r, "cost_usd")]
            verdicts += [r for r in qres if isinstance(r, dict)]
            # YELLOW 판정: 2차의견과 불일치 OR 신뢰도 중간대역 → 사람 검수
            if yellow and pred_v is not None:
                qm.confidence = round(pred_c, 3)
                if pred_v != qm.finalGrade:
                    qm.review = "yellow"
                    qm.review_reason = f"불일치: emb={pred_v} vs llm={qm.finalGrade}"
                elif yellow_low <= pred_c < prefilter_conf:
                    qm.review = "yellow"
                    qm.review_reason = f"신뢰도 중간대역(conf={round(pred_c,3)})"
                if qm.review == "yellow":
                    verdicts.append({"agent": "YellowGate",
                                     "evidence": qm.review_reason, "fail": None})
    fallbacks += V.verify_quality(qm, routing.active_quality_metas)

    # 아이템 메타 (G 또는 YELLOW 일 때: YELLOW도 인텐트 부여)
    item_meta = None
    gate_item = (qm.finalGrade == "G" or qm.review == "yellow")
    if gate_item and routing.content_track != "image_only":
        item_meta, ires = A.run_item(llm, content)
        all_results += [r for r in ires if hasattr(r, "cost_usd")]
        verdicts += [r for r in ires if isinstance(r, dict)]

        # (임베딩) 카테고리 배정을 결정론 kNN 으로: LLM 의존 제거 + 사전 강제
        if emb is not None and embed_categories:
            from . import classify as C
            cats, margin = C.intent_category_classify(emb, content)
            if cats:
                item_meta.intent = cats
                verdicts.append({"agent": "IntentCategory(emb)",
                                 "evidence": f"margin={margin}", "fail": None})
            if item_meta.entities:
                # 1차: 엔티티 본질로 분류(무문맥 → 혼합주제 오염 방지)
                item_meta.content_category = C.hybrid_entity_categories(
                    emb, llm, [(e, "") for e in item_meta.entities])
                # 2차: 1차에서 미분류된 비익명 고유명사를 제목 문맥으로 복구
                unrec = [(e, content.title) for e, c in item_meta.content_category.items()
                         if c == "Unclassified" and not C.is_vague_entity(e)]
                if unrec:
                    rec = C.recover_with_context(llm, unrec)
                    for e, c in rec.items():
                        if c != "Unclassified":
                            item_meta.content_category[e] = c
                verdicts.append({"agent": "EntityCategory(2-pass: 본질+문맥복구)",
                                 "evidence": "1차 본질 분류 → 2차 문맥 식별 복구", "fail": None})

        # 사전화: LLM 직접 생성 경로(임베딩 미사용)의 카테고리를 고정 사전에 스냅.
        # 임베딩 kNN 경로 결과엔 멱등(이미 사전값) → 항상 사전 보장.
        if item_meta and item_meta.content_category:
            item_meta.content_category = D.normalize_categories(item_meta.content_category)
        fallbacks += V.verify_item(item_meta, content)

    return _assemble(content, routing, legal_meta, qm, item_meta,
                     trace, all_results, verdicts, fallbacks, slim, emb)


def _assemble(content, routing, legal_meta, qm, item_meta,
              trace, results, verdicts, fallbacks, slim, emb=None):
    """Aggregator (no-LLM): 통합 + trace 집계."""
    trace.agent_verdicts = [v for v in verdicts if v.get("evidence") or v.get("fail")]
    trace.fallbacks = fallbacks
    # 주의: emb 는 배치에서 공유 클라이언트라 per-content 누적비용을 여기 더하면
    # 합산 시 중복계상된다. 임베딩 비용은 호출측(cli)에서 배치 단위로 1회만 합산한다.
    trace.cost_usd = round(sum(r.cost_usd for r in results), 6)   # LLM 비용만
    trace.tokens = {
        "in": sum(r.in_tok for r in results),
        "out": sum(r.out_tok for r in results),
    }
    trace.latency_ms = {"total": sum(r.latency_ms for r in results)}
    out = Output(content.ref(), routing, legal_meta, qm, item_meta, trace)
    return out.to_dict(slim=slim)


def _blocked_quality():
    from .schema import QualityMeta
    return QualityMeta(finalGrade="R", reasons=["graphic"])  # 차단 표식


# Mock 생성기: 키 없이 배관/대시보드 검증용. 정확도 아님.
# 간단한 키워드 휴리스틱으로 그럴듯한 메타를 결정론적으로 생성.
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
        # 본문이 매우 짧으면 shallow
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
        # 제목에서 따옴표/대문자 토큰을 엔티티로 근사
        title = _field(user, "title")
        ents = _mock_entities(title + " " + _field(user, "body"))
        svc = _field(user, "displayServiceName")
        cats = D.intent_categories_for(svc)[:2]
        ecat = {}
        for e in ents:
            ecat[e] = "News and Politics / Society"
        return {"summary": f"{(ents[0] if ents else '주제')} 관련 내용을 정리",
                "entities": ents, "intent": cats,
                "content_category": ecat}

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
    # 한글 2글자 이상 명사 후보 상위 빈도 3개(아주 거친 근사)
    toks = re.findall(r"[가-힣A-Za-z]{2,}", text)
    stop = {"있다", "했다", "관련", "내용", "오늘", "그리고", "이번"}
    freq = {}
    for t in toks:
        if t in stop:
            continue
        freq[t] = freq.get(t, 0) + 1
    ranked = sorted(freq, key=lambda k: (-freq[k], -len(k)))
    return ranked[:3]
