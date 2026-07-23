"""콘텐츠 에이전트(실험실) · 자연어 → 위젯 조건 변환.

사용자가 한 문장으로 말한 요청을 위젯 조건(모을 것·꼭 이런 내용·제외·읽는 방식)으로 바꾼다.
후보 값(사전)은 **실제 등록된 콘텐츠의 메타**에서 뽑아 호출자(브라우저)가 넘긴다 —
LLM 이 없는 값을 지어내지 않도록 허용 목록 안에서만 고르게 하고, 결과도 목록으로 재검증한다.
LLM 미구성·실패 시에는 규칙 기반 폴백(클라이언트와 동일 규칙)이 대신한다.

serve 역참조(_SV) 는 learnops·topicops 관례를 따른다.
"""
from __future__ import annotations

import json

_SV = None                                    # serve 주입(컴포지션 루트)

TONES = ("깊게", "빠르게", "가볍게")


def _system_prompt(dic: dict) -> str:
    """허용 목록을 못 박은 시스템 프롬프트(값 창작 금지 · JSON 만 반환)."""
    def _lst(key, cap=60):
        return [str(v) for v in (dic.get(key) or [])][:cap]
    return (
        "너는 뉴스 앱의 개인화 위젯 설정을 돕는다. 사용자가 한국어 한 문장으로 원하는 소식을 말하면,\n"
        "아래 허용 목록 안에서만 골라 조건을 만든다. 목록에 없는 값은 절대 만들지 않는다.\n\n"
        f"[인물·팀 후보]\n{json.dumps(_lst('ents'), ensure_ascii=False)}\n\n"
        f"[주제·분야 후보]\n{json.dumps(_lst('topics'), ensure_ascii=False)}\n\n"
        f"[내용 성격 후보]\n{json.dumps(_lst('kinds'), ensure_ascii=False)}\n\n"
        "규칙:\n"
        "- ents/topics/kinds 의 각 값은 위 후보 문자열과 **정확히 같아야** 한다.\n"
        "- '~빼고/제외/말고' 로 말한 대상은 excl 에 넣고 다른 항목에는 넣지 않는다.\n"
        "- tone 은 '깊게'(심층·분석·자세히) · '빠르게'(속보·짧게) · '가볍게'(재미·화제) 중 하나이거나 빈 문자열.\n"
        "- name 은 사용자가 알아볼 짧은 한국어 위젯 이름(예: '경제 깊이 읽기').\n"
        "- why 는 사용자의 말 어느 부분이 어떤 조건이 됐는지 [{said, to}] 로 2~4개.\n\n"
        '반드시 이 JSON 만 반환: {"name":"","ents":[],"topics":[],"kinds":[],"excl":[],"tone":"",'
        '"why":[{"said":"","to":""}]}'
    )


def _clean(obj: dict, dic: dict) -> dict:
    """LLM 응답을 허용 목록으로 재검증(환각 값 제거)."""
    def _pick(key, dkey):
        allow = {str(v) for v in (dic.get(dkey) or [])}
        out, seen = [], set()
        for v in (obj.get(key) or []):
            s = str(v).strip()
            if s in allow and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    ents = _pick("ents", "ents")
    topics = _pick("topics", "topics")
    kinds = _pick("kinds", "kinds")
    excl_allow = {str(v) for v in (dic.get("kinds") or [])} | {str(v) for v in (dic.get("topics") or [])}
    excl = []
    for v in (obj.get("excl") or []):
        s = str(v).strip()
        if s in excl_allow and s not in excl:
            excl.append(s)
    topics = [t for t in topics if t not in excl]
    kinds = [k for k in kinds if k not in excl]
    tone = str(obj.get("tone") or "").strip()
    if tone not in TONES:
        tone = ""
    why = []
    for w in (obj.get("why") or [])[:4]:
        if isinstance(w, dict) and str(w.get("said") or "").strip():
            why.append({"said": str(w["said"]).strip()[:40], "to": str(w.get("to") or "").strip()[:30]})
    name = str(obj.get("name") or "").strip()[:40]
    return {"name": name, "ents": ents, "topics": topics, "fields": [], "kinds": kinds,
            "excl": excl, "tone": tone, "why": why}


def understand(text: str, dic: dict, model: str = "", mock: bool = False):
    """자연어 → 조건. 반환 (결과 dict | None, via) · via: 'llm' | 실패 사유."""
    text = (text or "").strip()
    if not text:
        return None, "empty"
    llm, route = _SV.llm_for_model(model, mock)
    if llm is None:
        return None, route or "no-key"
    try:
        obj, _res = llm.complete_json(_system_prompt(dic), text, tag="ca_understand")
    except Exception as e:                     # 네트워크·파싱 실패는 폴백으로 넘긴다
        return None, f"error:{type(e).__name__}"
    if not isinstance(obj, dict) or obj.get("_fail"):
        return None, (isinstance(obj, dict) and obj.get("_fail_kind")) or "fail"
    out = _clean(obj, dic)
    if not (out["ents"] or out["topics"] or out["kinds"] or out["tone"]):
        return None, "empty-result"
    return out, "llm"
