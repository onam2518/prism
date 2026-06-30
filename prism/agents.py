"""추출 에이전트 래퍼 (Quality·Legal·Item)."""
from . import prompts as P
from .schema import QualityMeta, ItemMeta, LegalMeta, HarmType


def run_quality(llm, content, routing, fewshot: str = "") -> tuple[QualityMeta, list]:
    """단일 좁힌 콜(기본). active set 으로 규칙 적재를 줄여 Solar 규칙망각 완화.
    fewshot: 쿡북 Ch4 few-shot 예시 블록(모델 레시피로 결정)."""
    sys = P.quality_system(routing.active_quality_metas, routing.service_group,
                           examples=fewshot)
    obj, res = llm.complete_json(sys, P.quality_user(content), tag="quality")
    qm = QualityMeta(
        finalGrade=obj.get("finalGrade", "G"),
        reasons=obj.get("reasons", []) or [],
    )
    verdict = {"agent": "QualityAgent", "evidence": obj.get("evidence", ""),
               "fail": obj.get("_fail")}
    return qm, [res, verdict]


def run_quality_split(llm, content, routing) -> tuple[QualityMeta, list]:
    """분해형(옵션 A/B). 4개 관심사 묶음을 각각 좁은 규칙으로 호출."""
    reasons, results, verdicts = [], [], []
    for gkey in P.QUALITY_GROUPS:
        sys = P.quality_group_system(gkey, routing.active_quality_metas, routing.service_group)
        if not sys:
            continue
        obj, res = llm.complete_json(sys, P.quality_user(content), tag=f"q:{gkey}")
        reasons += obj.get("reasons", []) or []
        results.append(res)
        verdicts.append({"agent": f"QualityAgent:{gkey}", "evidence": obj.get("evidence", "")})
    qm = QualityMeta(finalGrade="R" if reasons else "G", reasons=reasons)
    return qm, results + verdicts


def run_item(llm, content) -> tuple[ItemMeta, list]:
    sys = P.item_system(content)
    obj, res = llm.complete_json(sys, P.item_user(content), tag="item")
    im = ItemMeta(
        summary=obj.get("summary", ""),
        entities=obj.get("entities", []) or [],
        intent=obj.get("intent", []) or [],
        content_category=obj.get("content_category", []) or [],
        topic=obj.get("topic", "") or "",                      # 3차(미생성 시 빈 값)
        topic_categories=obj.get("topic_categories", []) or [],
    )
    return im, [res, {"agent": "ItemAgent", "fail": obj.get("_fail")}]


def run_legal(llm, content) -> tuple[LegalMeta, list]:
    """LegalRouter → 유형별 LegalScorer. 유형별은 병렬 가능(여기선 순차, pipeline 에서 묶음)."""
    sys = P.legal_router_system()
    obj, res = llm.complete_json(sys, P._content_block(content), tag="legal_route")
    results = [res]
    lm = LegalMeta(enabled=True)
    for cand in obj.get("harm_types", []) or []:
        code, conf = cand.get("code"), cand.get("confidence", 0)
        if not code or conf < 0.3:
            continue
        from . import dictionaries as D
        if code not in D.LEGAL_HARM_TYPES:
            continue
        ssys = P.legal_scorer_system(code)
        sobj, sres = llm.complete_json(ssys, P._content_block(content), tag=f"legal:{code}")
        results.append(sres)
        a, b, c = sobj.get("a", 0), sobj.get("b", 0), sobj.get("c", 0)
        total = a + b + c
        lm.harm_types.append(HarmType(
            code=code, routed_article=D.LEGAL_HARM_TYPES[code]["article"],
            scores={"a": a, "b": b, "c": c, "total": total},
            grade=D.legal_grade(total),
        ))
    return lm, results
