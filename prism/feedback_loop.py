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
    out = fb.get("output") or {}
    im = out.get("item_meta") or {}
    if im:
        lines.append(f"[에이전트 산출] 리드문={im.get('summary', '')} · 엔티티={im.get('entities', [])} "
                     f"· 인텐트={im.get('intent', [])} · 카테고리={im.get('content_category', {})}")
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
