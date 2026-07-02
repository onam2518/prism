"""REAP 피드백 하네스 — 사람 피드백을 Remember→Explain→Ask→Plan 에이전트로 가공.

검수자의 교정 메모를 raw 그대로 LEARNED 에 넣지 않고, REAP 4단계로 구조화한다:
  Remember(기억) — 비난·감정을 걷어내고 객관 사실부터 인지
  Explain(설명)  — 잘된 점·부족한 점을 구체 근거로 설명
  Ask(질문)      — 검수자의 의도를 묻고 확인
  Plan(계획)     — 다음 추출에 적용할 개선 지시(프롬프트에 덧붙일 명령형)
산출된 plan 이 store.learned_by_stage 를 통해 단계별 프롬프트(LEARNED)로 자동 반영된다.

키가 없으면 mock REAP 로 동작(배관 검증). 비용·지연 최소화를 위해 4단계를 1회 구조화
콜로 산출하되, 프롬프트가 4단계 추론을 강제한다(에이전트가 REAP 순서로 사고).
"""
from __future__ import annotations

REAP_STAGES = ("remember", "explain", "ask", "plan")
_STAGE_KO = {"extract": "추출", "analyze": "분석", "review": "검수", "judge": "판정"}

REAP_SYSTEM = (
    "너는 콘텐츠 메타 추출 파이프라인의 '피드백 코치'다. 검수자의 교정 피드백을 받아 "
    "REAP 4단계로 구조화한다. 비난·감정 표현은 제거하고 객관 사실에서 출발한다. "
    "반드시 아래 JSON 키만 채워라(다른 키 금지).\n"
    "- remember: 감정·비난을 걷어내고 무엇이 객관적으로 일어났는지(에이전트 산출 vs 검수 지적)를 사실로만 1~2문장.\n"
    "- explain: 잘된 점과 부족한 점을 구체 근거(어떤 필드·어떤 값)로 설명.\n"
    "- ask: 검수자가 원한 올바른 결과가 무엇인지 확인하는 한 문장 질문.\n"
    "- plan: 다음 추출부터 적용할 개선 지시를 명령형 1~2문장으로. 해당 단계 프롬프트에 그대로 "
    "덧붙일 수 있게 구체적이되 이 한 건을 넘어 일반화. 콘텐츠 제목 같은 고유값은 넣지 말 것."
)


def _reap_user(fb: dict) -> str:
    stage = fb.get("stage", "analyze")
    lines = [
        f"[검수 단계] {stage} ({_STAGE_KO.get(stage, stage)})",
        f"[검수자 메모] {fb.get('note', '').strip() or '(메모 없음)'}",
        f"[콘텐츠 제목] {fb.get('title', '') or '(없음)'}",
    ]
    if fb.get("model"):
        lines.append(f"[초안 생성 모델] {fb['model']}")
    out = fb.get("output") or {}
    im = out.get("item_meta") or {}
    if im:
        lines.append(f"[에이전트 산출] 리드문={im.get('summary', '')} · 엔티티={im.get('entities', [])} "
                     f"· 인텐트={im.get('intent', [])} · 카테고리={im.get('content_category', [])}")
    qm = out.get("quality_meta") or {}
    if qm:
        lines.append(f"[품질 판정] 등급={qm.get('finalGrade', '')} · 사유={qm.get('reasons', [])}")
    return "\n".join(lines)


def run_reap(llm, fb: dict) -> dict:
    """fb: {stage, note, title, output?} → {remember, explain, ask, plan, stage}.
    키 없거나 mock 이면 휴리스틱 mock REAP."""
    stage = fb.get("stage", "analyze")
    if getattr(llm, "mock", False):
        r = _mock_reap(fb)
        r["stage"] = stage
        return r
    try:
        obj, _res = llm.complete_json(REAP_SYSTEM, _reap_user(fb), tag="reap")
    except Exception as e:
        print(f"  [warn] REAP 모델 호출 실패 → plan 폴백: {e}")
        obj = {}
    out = {k: (obj.get(k) or "").strip() for k in REAP_STAGES}
    if not out["plan"]:                    # 안전망: plan 비면 메모 폴백
        out["plan"] = (fb.get("note") or "").strip()
    out["stage"] = stage
    return out


META_SYSTEM = (
    "너는 프롬프트 개선 '메타컴파일러'다. 한 단계(추출/분석/검수/판정)에 대한 여러 검수자의 교정 "
    "피드백 묶음을 받아 다음 추출 프롬프트에 넣을 '정제된 개선 지시'로 컴파일한다.\n"
    "- 중복·유사 항목은 하나로 병합. 자주 반복되는 교정을 우선(앞에).\n"
    "- 서로 충돌하는 지적은 directive 에 억지로 합치지 말고 ambiguities 로 분리한다"
    "(= 가이드가 모호하다는 신호 → 명확화 필요).\n"
    "반드시 JSON 만: {\"directive\": \"명령형 1~5줄, 일반화·구체적\", \"ambiguities\": [\"무엇에 대해 의견이 갈리는지\", ...]}"
)


def meta_compile(llm, stage: str, raw_text: str) -> dict:
    """한 단계의 누적 검수 피드백(plan 묶음) → {directive, ambiguities}. 다수 의견을 병합·정리하고
    충돌은 '명확화 필요'로 분리. 키 없으면 mock(원문 일부)."""
    if not (raw_text or "").strip():
        return {"stage": stage, "directive": "", "ambiguities": []}
    if getattr(llm, "mock", False):
        return {"stage": stage, "directive": raw_text.strip()[:400], "ambiguities": []}
    try:
        obj, _res = llm.complete_json(META_SYSTEM, f"[단계] {stage}\n[검수자 피드백 묶음]\n{raw_text}",
                                      tag="metacompile")
    except Exception as e:
        print(f"  [warn] meta_compile 실패: {e}")
        obj = {}
    return {"stage": stage,
            "directive": (obj.get("directive") or "").strip() or raw_text.strip(),
            "ambiguities": obj.get("ambiguities") or []}


# ── 피드백 오케스트레이터: 교정 원문 → 요소별 개선사항 재분류(분기) ──
ELEMENTS = ("summary", "entities", "intent", "category", "grade", "quality")
ELEM_STAGE = {"summary": "analyze", "entities": "analyze", "intent": "analyze",
              "category": "analyze", "grade": "judge", "quality": "review"}
_ELEM_KO = {"summary": "리드문", "entities": "엔티티", "intent": "인텐트",
            "category": "카테고리", "grade": "등급·유통", "quality": "품질 사유"}

ROUTE_SYSTEM = (
    "너는 콘텐츠 메타 추출 파이프라인의 '피드백 오케스트레이터'다. 검수자의 교정 원문을 읽고, "
    "지적이 어느 요소(들)에 대한 것인지 재분류해 요소별 개선 지시로 분해한다.\n"
    "- 요소 id 는 다음만 사용: summary(리드문), entities(엔티티), intent(인텐트), "
    "category(카테고리), grade(등급·유통), quality(품질 사유).\n"
    "- 검수자가 선택한 요소 힌트가 있어도, 원문이 다른 요소를 함께 지적하면 그 요소도 포함하라.\n"
    "- directive 는 그 요소를 다루는 프롬프트에 덧붙일 명령형 1~2문장. 이 한 건을 넘어 일반화하고, "
    "콘텐츠 제목 같은 고유값은 넣지 말 것.\n"
    "반드시 JSON 만: {\"items\": [{\"element\": \"위 id\", \"directive\": \"...\"}]}"
)


def route_feedback(llm, fb: dict) -> list:
    """검수 교정 원문을 요소별 개선사항으로 재분류 → [{element, stage, directive}].
    mock/실패 시 폴백: 선택 요소(들) 그대로 원문을 지시로 사용(무손실)."""
    note = (fb.get("note") or "").strip()
    hint = [e for e in (fb.get("elements") or []) if e in ELEMENTS] or ["summary"]
    fallback = [{"element": e, "stage": ELEM_STAGE[e], "directive": note} for e in hint if note]
    if not note or getattr(llm, "mock", False):
        return fallback
    user = (f"[검수자 선택 요소 힌트] {', '.join(_ELEM_KO[e] + '(' + e + ')' for e in hint)}\n"
            + _reap_user(fb))
    try:
        obj, _res = llm.complete_json(ROUTE_SYSTEM, user, tag="route")
    except Exception as e:
        print(f"  [warn] 피드백 라우팅 실패 → 선택 요소 폴백: {e}")
        return fallback
    out = []
    for it in (obj.get("items") or []):
        el = str(it.get("element") or "").strip()
        dv = str(it.get("directive") or "").strip()
        if el in ELEMENTS and dv:
            out.append({"element": el, "stage": ELEM_STAGE[el], "directive": dv})
    return out or fallback


def _mock_reap(fb: dict) -> dict:
    note = (fb.get("note") or "").strip() or "(메모 없음)"
    stage = fb.get("stage", "analyze")
    ko = _STAGE_KO.get(stage, stage)
    return {
        "remember": f"검수자가 {ko} 단계 산출에 대해 다음을 지적함: {note}",
        "explain": f"해당 단계 산출이 기대와 어긋난 부분이 있음. 핵심 지적: {note}",
        "ask": "검수자가 원한 올바른 결과는 무엇인가?",
        "plan": f"{note} — 다음 {ko}부터 이 점을 반영해 처리하라.",
    }
