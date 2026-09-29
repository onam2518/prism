"""추출 에이전트 래퍼 (Quality·Legal·Item)."""
from __future__ import annotations
from . import prompts as P
from .meta_prompts import CALLS as CALL_ORDER      # ①②③④ · 트레이스 적재 순서의 단일 원천
from .schema import QualityMeta, ItemMeta, LegalMeta, HarmType


EVIDENCE_MAX = 400        # 근거 문장 보관 상한(문장 한둘이면 충분 · 적재 payload 비대화 방지)


def _evidence_text(obj) -> str:
    """모델 응답에서 근거 문장을 꺼낸다. 문자열이 아니면(목록·객체) 읽을 수 있게 펴서 담는다.

    지어내지 않는다 — 모델이 준 값만 쓰고, 없으면 빈 문자열이다. 이 값은 검수 보조 브리핑의
    '왜 그렇게 판정했나' 원천이라, 나중에 다시 물어 재생성하면 사후 추측이 된다(2026-08-12)."""
    ev = obj.get("evidence") if isinstance(obj, dict) else None
    if isinstance(ev, (list, tuple)):
        ev = " · ".join(str(x).strip() for x in ev if str(x).strip())
    elif isinstance(ev, dict):
        ev = " · ".join(f"{k}: {v}" for k, v in ev.items())
    ev = str(ev or "").strip()
    return ev[:EVIDENCE_MAX]


def run_quality(llm, content, routing, fewshot: str = "") -> tuple[QualityMeta, list]:
    """단일 좁힌 콜(기본). active set 으로 규칙 적재를 줄여 Solar 규칙망각 완화.
    fewshot: 쿡북 Ch4 few-shot 예시 블록(모델 레시피로 결정)."""
    sys = P.quality_system(routing.active_quality_metas, routing.service_group,
                           examples=fewshot)
    obj, res = llm.complete_json(sys, P.quality_user(content), tag="quality")
    grade = obj.get("finalGrade")
    ev = _evidence_text(obj)
    if obj.get("_fail"):                       # 호출 실패 = 판정 보류(빈 응답을 G 로 유통하지 않는다 · fail-open 금지)
        qm = QualityMeta(finalGrade="", reasons=[], review="yellow",
                         review_reason="품질 호출 실패 · 판정 보류", evidence=ev)
    elif not isinstance(grade, str) or not grade.strip():
        # 계약 키 부재(래핑·이름 변형 응답)·빈 등급 = 판정이 없는 것. 종전 기본값 "G" 는
        # 모델이 R 이라고 해도 자동 G 로 유통시켰다(파싱은 성공해 fail_kind 도 안 붙는 사각지대).
        qm = QualityMeta(finalGrade="", reasons=obj.get("reasons", []) or [], review="yellow",
                         review_reason="품질 응답에 finalGrade 없음 · 판정 보류", evidence=ev)
    else:
        qm = QualityMeta(
            finalGrade=grade,
            reasons=obj.get("reasons", []) or [],
            evidence=ev,
        )
    verdict = {"agent": "QualityAgent", "evidence": ev,
               "fail": obj.get("_fail")}
    return qm, [res, verdict]


def run_quality_split(llm, content, routing) -> tuple[QualityMeta, list]:
    """분해형(옵션 A/B). 4개 관심사 묶음을 각각 좁은 규칙으로 호출."""
    reasons, results, verdicts, evs = [], [], [], []
    failed = False
    for gkey in P.QUALITY_GROUPS:
        sys = P.quality_group_system(gkey, routing.active_quality_metas, routing.service_group)
        if not sys:
            continue
        obj, res = llm.complete_json(sys, P.quality_user(content), tag=f"q:{gkey}")
        failed = failed or bool(obj.get("_fail"))
        reasons += obj.get("reasons", []) or []
        results.append(res)
        ev = _evidence_text(obj)
        if ev:                                 # 묶음별 근거는 어느 묶음 것인지 붙여 합친다
            evs.append(f"[{gkey}] {ev}")
        verdicts.append({"agent": f"QualityAgent:{gkey}", "evidence": ev})
    ev_all = " · ".join(evs)[:EVIDENCE_MAX]
    if failed:                                 # 일부 묶음이라도 실패 = 커버리지 불명 → 판정 보류(fail-open 금지)
        qm = QualityMeta(finalGrade="", reasons=reasons, review="yellow",
                         review_reason="품질 호출 일부 실패 · 판정 보류", evidence=ev_all)
    else:
        qm = QualityMeta(finalGrade="R" if reasons else "G", reasons=reasons, evidence=ev_all)
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


def _retry_hint(what: str, bad_values: list, allowed: list) -> str:
    """전량 드롭 재요청용 user 말미 추가문. 프리픽스(system)는 건드리지 않으므로
    프롬프트 캐시에도 영향이 없다(캐시 키는 system 지문 기반)."""
    bad = " · ".join(str(v) for v in (bad_values or [])[:5])
    lst = " · ".join(str(v) for v in (allowed or [])[:60])
    return (f"\n\n[재요청] {what} [{bad}] 은(는) 허용 목록에 없는 표기다. "
            f"아래 목록의 표기를 **그대로** 복사해 다시 고르라(목록 밖 값·변형 표기 금지).\n{lst}")


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
    """독립 4호출(계약 2026-09-22 · 371131847 '호출 구조'): ① summary · ② entities ·
    ③ intent · ④ content_category 를 제목·본문에서 각각 추출한다.
    어느 호출도 다른 호출의 출력을 입력으로 받지 않으므로 ① 이 비거나 실패해도 나머지 셋을
    실행하고 결과를 살린다. 호출 실패·계약 키 부재는 그 필드만 hold_fields 에 넣는다(항목별 상태).
    호출별 기계 검증(사전 불일치 드롭, 전량 드롭 시 1회 재요청)은 그대로 둔다.
    parallel=True(방법론 옵션): 네 호출 동시 실행 · 트레이스는 ①②③④ 순으로 적재해 결정론 유지."""
    import re as _re
    from . import dictionaries as D

    def _aslist(v):                                # 규칙 보정: 문자열 단일값 → 리스트(재요청 절감)
        if isinstance(v, str) and v.strip():
            return [v]
        return v if isinstance(v, list) else []

    def _canon(x):                                 # 규칙 보정: '속보 · 단신' 등 공백 변형 흡수
        return _re.sub(r"\s*·\s*", "·", str(x).strip())

    sinks = {c: [] for c in CALL_ORDER}            # 호출별 트레이스(병렬이어도 적재 순서는 고정)

    def ask(call: str, tag: str, extra: str = "", require: str = "") -> dict:
        out = sinks[call]
        c_llm = _call_llm(llm, call)
        sysp = P.call_system(content, call, getattr(c_llm, "model", "") or "")
        obj, res = c_llm.complete_json(sysp, P.call_user(call, content) + extra, tag=tag)
        # 계약 키 부재를 실패로 승격: 파싱은 됐지만 계약을 안 지킨 응답(래핑·이름 변형)은
        # fail_kind 가 없어 하네스의 yellow 가드를 그대로 빠져나갔다 — 빈 메타가 review=auto 로
        # 유통되던 사각지대(2026-07-28 사고와 결과 동일). 빈 값(정당한 신호)과는 구분한다.
        miss = bool(require) and not obj.get("_fail") and require not in obj
        if miss:
            res.fail_kind = getattr(res, "fail_kind", None) or "contract_miss"
            if not getattr(res, "fail_detail", ""):
                res.fail_detail = f"응답에 '{require}' 키 없음: {str(obj)[:120]}"
        out.append(res)
        out.append({"agent": f"ItemAgent:{call}", "model": getattr(c_llm, "model", "") or "",
                    "fail": obj.get("_fail") or (f"계약 키 없음: {require}" if miss else None)})
        return obj

    def _failed(obj, key) -> bool:                 # 호출 실패 · 계약 키 부재 = 그 필드만 보류
        return bool(obj.get("_fail")) or key not in obj

    # ① 리드문 — 빈 문자열은 '생성 불가' 라는 정당한 신호이지 보류가 아니다.
    def call_summary():
        o = ask("summary", "item_summary", require="summary")
        s = o.get("summary")
        return (s.strip() if isinstance(s, str) else ""), _failed(o, "summary")

    # ② 엔티티(핵심만 · 개수 상한 없음 · 2026-07-08 수량 정책 전환)
    def call_entities():
        o = ask("entities", "item_entities", require="entities")
        return [str(x).strip() for x in _aslist(o.get("entities")) if str(x).strip()], _failed(o, "entities")

    # ③ 인텐트: 사전 표기 정확 일치만 통과, 전량 드롭이면 1회 재요청
    def call_intent():
        valid = set(D.intent_categories())
        canon_map = {_canon(v): v for v in valid}

        def _match(vals):
            ok, bad = [], []
            for x in vals:
                hit = canon_map.get(_canon(x))
                (ok if hit else bad).append(hit or x)
            return ok, bad
        o = ask("intent", "item_intent", require="intent")
        raw = [str(x).strip() for x in _aslist(o.get("intent")) if str(x).strip()]
        intent, dropped = _match(raw)
        retried = False
        if raw and not intent:
            retried = True
            # 재요청은 **요청이 달라야** 의미가 있다(temperature=0 · 종전에는 바이트 단위로 같은
            # 요청을 보내 같은 답을 받고 비용만 2배였다). 실패 값과 허용 목록을 명시해 다시 묻는다.
            o = ask("intent", "item_intent", require="intent",
                    extra=_retry_hint("직전 응답의 인텐트", raw, sorted(valid)))
            raw = [str(x).strip() for x in _aslist(o.get("intent")) if str(x).strip()]
            intent, bad2 = _match(raw)
            dropped += bad2
        if dropped:                                # 사전 갭 관측: 드롭 원값·재요청 여부를 트레이스에 보존
            sinks["intent"].append({"agent": "Verifier:intent", "fail": None,
                                    "evidence": f"사전 불일치 드롭 {len(dropped)}건: " + " · ".join(dropped[:5])
                                                + (" (전량 드롭 → 재요청 1회)" if retried else ""),
                                    "drop": {"call": "intent", "values": dropped[:10],
                                             "service": content.displayServiceName, "retried": retried}})
        return intent, _failed(o, "intent")

    # ④ 콘텐츠 카테고리: 사전 경로 정규화(스냅), 전량 드롭이면 1회 재요청
    def call_category():
        o = ask("category", "item_category", require="content_category")
        raw = [str(x) for x in _aslist(o.get("content_category")) if str(x).strip()]
        cats = D.normalize_category_list(raw)
        retried = False
        if raw and not cats:
            retried = True
            o = ask("category", "item_category", require="content_category",
                    extra=_retry_hint("직전 응답의 콘텐츠 카테고리", raw,
                                      [f"{t1} / …" for t1 in sorted(D.IAB_TIER1)]))
            raw = [str(x) for x in _aslist(o.get("content_category")) if str(x).strip()]
            cats = D.normalize_category_list(raw)
        dropped = [x for x in raw if not D.normalize_category_list([x])]
        if dropped:
            sinks["category"].append({"agent": "Verifier:category", "fail": None,
                                      "evidence": f"사전 스냅 실패 드롭 {len(dropped)}건: " + " · ".join(dropped[:5])
                                                  + (" (전량 드롭 → 재요청 1회)" if retried else ""),
                                      "drop": {"call": "category", "values": dropped[:10],
                                               "service": content.displayServiceName, "retried": retried}})
        return cats, _failed(o, "content_category")

    runners = {"summary": call_summary, "entities": call_entities,
               "intent": call_intent, "category": call_category}
    box, errs = {}, []

    def _run(call):
        try:
            box[call] = runners[call]()
        except Exception as e:                     # 순차 모드와 동일하게 전파
            errs.append(e)

    if parallel:
        import threading
        ts = [threading.Thread(target=_run, args=(c,)) for c in CALL_ORDER]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    else:
        for c in CALL_ORDER:
            _run(c)
    results = [r for c in CALL_ORDER for r in sinks[c]]   # 트레이스 순서 결정론(①②③④)
    if errs:
        raise errs[0]
    summary, f_sum = box["summary"]
    ents, f_ent = box["entities"]
    intent, f_int = box["intent"]
    cats, f_cat = box["category"]
    hold = [k for k, f in (("summary", f_sum), ("entities", f_ent),
                           ("intent", f_int), ("content_category", f_cat)) if f]
    return ItemMeta(summary=summary, entities=ents, intent=intent, content_category=cats,
                    hold_fields=hold), results


def _num(v):
    """모델이 숫자를 문자열("20")로 내도 배치를 죽이지 않게 하는 안전 변환.
    변환 불가는 None — 호출부가 '점수 불신(보류)' 경로로 합류시킨다.
    (종전: "20"+"15"+"10" = "201510" → 등급 비교에서 TypeError 가 추출 전체로 전파돼
     이미 추출한 앞 건들의 LLM 비용까지 통째로 버려졌다 · llm._fail 의 배치 비중단 계약 붕괴)"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def run_legal(llm, content) -> tuple[LegalMeta, list]:
    """LegalRouter → 유형별 LegalScorer. 유형별은 병렬 가능(여기선 순차, pipeline 에서 묶음)."""
    sys = P.legal_router_system()
    obj, res = llm.complete_json(sys, P._content_block(content), tag="legal_route")
    results = [res]
    lm = LegalMeta(enabled=True)
    if obj.get("_fail"):                        # 라우터 호출 실패 → 유형 열거 불가 · GREEN 으로 유통 안 함(fail-closed)
        lm.failed = True
        return lm, results
    for cand in obj.get("harm_types", []) or []:
        if not isinstance(cand, dict):          # 형식 이탈(문자열 나열 등) → 유형 열거 불신
            lm.failed = True
            continue
        code = cand.get("code")
        conf = _num(cand.get("confidence", 0))
        if not code:
            continue
        if conf is None:                        # 신뢰도 형식 불량 → 임계 판정 불가 · 보류(0점 GREEN 방지)
            lm.failed = True
            continue
        if conf < 0.3:
            continue
        from . import dictionaries as D
        if code not in D.LEGAL_HARM_TYPES:
            continue
        ssys = P.legal_scorer_system(code)
        sobj, sres = llm.complete_json(ssys, P._content_block(content), tag=f"legal:{code}")
        results.append(sres)
        if sobj.get("_fail"):                   # 스코어러 실패 → 이 유형 점수 불신 · 보류 표식(0점 GREEN 방지)
            lm.failed = True
            continue
        a, b, c = (_num(sobj.get("a", 0)), _num(sobj.get("b", 0)), _num(sobj.get("c", 0)))
        if a is None or b is None or c is None:  # 점수 형식 불량 → 스코어러 실패와 동일 취급
            lm.failed = True
            continue
        total = a + b + c
        lm.harm_types.append(HarmType(
            code=code, routed_article=D.LEGAL_HARM_TYPES[code]["article"],
            scores={"a": a, "b": b, "c": c, "total": total},
            grade=D.legal_grade(total),
        ))
    return lm, results
