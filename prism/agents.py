"""추출 에이전트 래퍼 (Quality·Legal·Item)."""
from __future__ import annotations
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


# 아이템 메타 실행 설정(serve.sync_prompt 주입): 분리형 4호출 여부 + 호출별 모델 티어
META_CFG = {"four_calls": True, "call_models": {}}
# 호출별 모델 라우팅 팩토리(serve 주입): model_id -> llm | None
LLM_FOR_CALL = None


def _call_llm(main_llm, call: str):
    mid = ((META_CFG.get("call_models") or {}).get(call) or "").strip()
    if mid and LLM_FOR_CALL is not None:
        try:
            alt = LLM_FOR_CALL(mid)
            if alt is not None:
                return alt
        except Exception:
            pass
    return main_llm


def run_item(llm, content, parallel: bool = False) -> tuple[ItemMeta, list]:
    if META_CFG.get("four_calls", True):
        return _run_item_calls(llm, content, parallel=parallel)
    sys = P.item_system(content, getattr(llm, "model", "") or "")
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


def _run_item_calls(llm, content, parallel: bool = False) -> tuple[ItemMeta, list]:
    """분리형 순차 4호출(계약): ① summary → ② entities → ③ intent → ④ content_category.
    단락 차단(① 빈 문자열 → 후속 생략) · 호출 사이 기계 검증(사전 불일치 드롭, 전량 드롭 시 1회 재요청).
    parallel=True(방법론 옵션 · A/B 검증용): ①·②만 동시 실행. 두 호출의 user 프롬프트가
    prior 를 쓰지 않아(meta_prompts.call_user) 산출은 순차와 동일하고, 트레이스는 ①→② 순서로
    적재해 결정론을 유지한다. 트레이드오프 = ① 빈 문자열일 때 ② 호출 비용 낭비(차단 계약 예외)."""
    import re as _re
    from . import dictionaries as D
    results, prior = [], {}

    def _aslist(v):                                # 규칙 보정: 문자열 단일값 → 리스트(재요청 절감)
        if isinstance(v, str) and v.strip():
            return [v]
        return v if isinstance(v, list) else []

    def _canon(x):                                 # 규칙 보정: '속보 · 단신' 등 공백 변형 흡수
        return _re.sub(r"\s*·\s*", "·", str(x).strip())

    def ask(call: str, tag: str, sink: list = None) -> dict:
        out = results if sink is None else sink
        c_llm = _call_llm(llm, call)
        sysp = P.call_system(content, call, getattr(c_llm, "model", "") or "")
        obj, res = c_llm.complete_json(sysp, P.call_user(call, content, prior), tag=tag)
        out.append(res)
        out.append({"agent": f"ItemAgent:{call}", "model": getattr(c_llm, "model", "") or "",
                    "fail": obj.get("_fail")})
        return obj

    # ①·② (parallel 이면 동시 · 아니면 계약 순차)
    if parallel:
        import threading
        box, errs, r1, r2 = {}, [], [], []

        def _t(key, call, tag, sink):
            try:
                box[key] = ask(call, tag, sink)
            except Exception as e:                 # 순차 모드와 동일하게 전파
                errs.append(e)
        t1 = threading.Thread(target=_t, args=("o1", "summary", "item_summary", r1))
        t2 = threading.Thread(target=_t, args=("o2", "entities", "item_entities", r2))
        t1.start(); t2.start(); t1.join(); t2.join()
        results += r1 + r2                          # 트레이스 순서 결정론(①→②)
        if errs:
            raise errs[0]
        o1, o2 = box.get("o1") or {}, box.get("o2") or {}
    else:
        o1 = ask("summary", "item_summary")

    summary = (o1.get("summary") or "").strip() if isinstance(o1.get("summary"), str) else ""
    prior["summary"] = summary
    if not summary:                                # 단락 차단: 하위 호출 생략, 빈 값 적재
        return ItemMeta(summary="", entities=[], intent=[], content_category=[]), results

    # ② 엔티티(1~3개 강제)
    if not parallel:
        o2 = ask("entities", "item_entities")
    ents = [str(x).strip() for x in _aslist(o2.get("entities")) if str(x).strip()][:3]
    prior["entities"] = ents

    # ③ 인텐트: 사전 표기 정확 일치만 통과, 전량 드롭이면 1회 재요청
    valid_intents = set(D.intent_categories_for(content.displayServiceName))
    canon_map = {_canon(v): v for v in valid_intents}

    def _match_intents(vals):
        ok, bad = [], []
        for x in vals:
            hit = canon_map.get(_canon(x))
            (ok if hit else bad).append(hit or x)
        return ok, bad
    o3 = ask("intent", "item_intent")
    raw3 = [str(x).strip() for x in _aslist(o3.get("intent")) if str(x).strip()]
    intent, dropped3 = _match_intents(raw3)
    retried3 = False
    if raw3 and not intent:
        retried3 = True
        o3 = ask("intent", "item_intent")
        raw3 = [str(x).strip() for x in _aslist(o3.get("intent")) if str(x).strip()]
        got2, bad2 = _match_intents(raw3)
        intent = got2
        dropped3 += bad2
    if dropped3:                                   # 사전 갭 관측: 드롭 원값·재요청 여부를 트레이스에 보존
        results.append({"agent": "Verifier:intent", "fail": None,
                        "evidence": f"사전 불일치 드롭 {len(dropped3)}건: " + " · ".join(dropped3[:5])
                                    + (" (전량 드롭 → 재요청 1회)" if retried3 else ""),
                        "drop": {"call": "intent", "values": dropped3[:10],
                                 "service": content.displayServiceName, "retried": retried3}})
    prior["intent"] = intent

    # ④ 콘텐츠 카테고리: 사전 경로 정규화(스냅), 전량 드롭이면 1회 재요청
    o4 = ask("category", "item_category")
    raw4 = [str(x) for x in _aslist(o4.get("content_category")) if str(x).strip()]
    cats = D.normalize_category_list(raw4)
    retried4 = False
    if raw4 and not cats:
        retried4 = True
        o4 = ask("category", "item_category")
        raw4 = [str(x) for x in _aslist(o4.get("content_category")) if str(x).strip()]
        cats = D.normalize_category_list(raw4)
    dropped4 = [x for x in raw4 if not D.normalize_category_list([x])]
    if dropped4:
        results.append({"agent": "Verifier:category", "fail": None,
                        "evidence": f"사전 스냅 실패 드롭 {len(dropped4)}건: " + " · ".join(dropped4[:5])
                                    + (" (전량 드롭 → 재요청 1회)" if retried4 else ""),
                        "drop": {"call": "category", "values": dropped4[:10],
                                 "service": content.displayServiceName, "retried": retried4}})

    return ItemMeta(summary=summary, entities=ents, intent=intent, content_category=cats), results


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
