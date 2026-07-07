"""페르소나 능동 생성: 사용자 메타(프로필) + 소비 프로필 → 사용자별 페르소나 카드.

고정 8종(usermeta.PERSONAS) 분류와 별개로, 프로필과 행동 로그가 모두 갖춰진
사용자마다 새 페르소나를 만든다(생성 단위=사용자, 기존 8종과 병행 표시).
LLM 호출 실패·mock 모드에서는 결정론 폴백으로 같은 구조를 만들어 키 없이도 전 기능 체험.
"""
from __future__ import annotations
import json

# 프로필 스키마(식별 정보는 받지 않는다 · 실명/연락처 없음)
PROFILE_FIELDS = ("user_id", "age_band", "interests", "day_part", "note")
AGE_BANDS = ("10대", "20대", "30대", "40대", "50대", "60대 이상", "미상")
DAY_PARTS = ("출퇴근", "주간", "야간", "주말", "수시")

GEN_CAP = 200        # 한 번에 생성하는 최대 사용자 수(비용 가드)


def profile_template_csv() -> bytes:
    """프로필 업로드 서식. interests 는 세미콜론(;) 구분."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["user_id", "age_band", "interests", "day_part", "note"])
    w.writerow(["u1", "30대", "재테크;야구", "야간", ""])
    w.writerow(["u2", "20대", "아이돌", "출퇴근", ""])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def parse_profiles(data: bytes, filename: str = "") -> list:
    """CSV/TSV/JSONL → 프로필 dict 리스트(user_id 없는 행은 버림)."""
    import os
    ext = os.path.splitext(filename or "")[1].lower()
    text = data.decode("utf-8-sig", "replace")
    rows = []
    if ext in (".csv", ".tsv", ""):
        import csv
        import io
        for row in csv.DictReader(io.StringIO(text), delimiter="\t" if ext == ".tsv" else ","):
            rows.append({(k or "").strip(): (v.strip() if isinstance(v, str) else v)
                         for k, v in row.items() if k})
    else:
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return [normalize_profile(r) for r in rows if (r.get("user_id") or "").strip()]


def normalize_profile(raw: dict) -> dict:
    """폼/서식 입력 공통 정규화. interests: 문자열(;·, 구분) 또는 리스트 허용."""
    it = raw.get("interests") or []
    if isinstance(it, str):
        for sep in (";", "·", ","):
            if sep in it:
                it = [s.strip() for s in it.split(sep)]
                break
        else:
            it = [it.strip()] if it.strip() else []
    return {"user_id": str(raw.get("user_id") or "").strip(),
            "age_band": str(raw.get("age_band") or "").strip(),
            "interests": [s for s in it if s][:8],
            "day_part": str(raw.get("day_part") or "").strip(),
            "note": str(raw.get("note") or "").strip()[:200]}


_SYSTEM = (
    "너는 콘텐츠 소비 행동 분석가다. 사용자 메타(프로필)와 행동 로그에서 산출한 소비 프로필을 근거로 "
    "이 사용자만의 페르소나 카드를 JSON 객체 하나로 만든다. 근거에 없는 사실을 지어내지 않는다.\n"
    '키: {"name": "한국어 별칭(2~8자, ~러/~형 같은 유형명)", "full": "수식어 포함 전체 이름(20자 이내)", '
    '"desc": "소비 행동 특징 한 문장(60자 이내)", "basis": ["판단 근거 3개(각 40자 이내)"]}'
)


def _fallback(profile: dict, u: dict) -> dict:
    """LLM 없이도 같은 구조를 만드는 결정론 폴백: 깊이 × 주관심 × 시간대 조합."""
    depth = (u.get("form") or {}).get("깊이", "혼합")
    dlab = {"몰입": "정독", "훑기": "스낵", "혼합": "탐색"}.get(depth, "탐색")
    ents = u.get("interest_entity_categories") or []
    cat = (profile.get("interests") or [None])[0] or (ents[0][0] if ents else "관심사 미상")
    dp = profile.get("day_part") or ""
    eng = u.get("engagement") or {}
    name = f"{cat} {dlab}형"
    return {"name": name[:12], "full": (f"{dp} {name}".strip())[:20],
            "desc": f"{cat} 중심 소비 · 깊이 {depth} · 평균 체류 {eng.get('avg_dwell_sec', 0)}초 · 클릭률 {eng.get('click_rate', 0)}",
            "basis": [f"행동 로그 {eng.get('views', 0)}건에서 깊이 {depth} 산출",
                      f"선호 카테고리 {cat}" + (f" · 관심 선언 {' '.join(profile.get('interests') or [])[:20]}" if profile.get("interests") else ""),
                      f"프로필 {profile.get('age_band') or '연령 미상'}" + (f" · {dp} 이용" if dp else "")]}


def _gen_one(llm, profile: dict, u: dict) -> dict:
    """사용자 1명 페르소나 생성. LLM 결과가 스키마를 못 채우면 폴백."""
    user = json.dumps({
        "프로필": {k: profile.get(k) for k in ("age_band", "interests", "day_part") if profile.get(k)},
        "소비 형태": u.get("form"), "소비 강도": u.get("intensity"),
        "관심 카테고리": u.get("interest_entity_categories"),
        "관심 맥락": u.get("interest_intent_categories"),
        "선호 엔티티": [e[0] for e in (u.get("affinity_entities") or [])[:5]],
        "지표": u.get("engagement"),
    }, ensure_ascii=False)
    obj, _res = llm.complete_json(_SYSTEM, user, tag="persona_gen")
    if not (isinstance(obj, dict) and obj.get("name") and obj.get("desc")):
        return _fallback(profile, u)
    basis = obj.get("basis") if isinstance(obj.get("basis"), list) else []
    return {"name": str(obj.get("name"))[:12], "full": str(obj.get("full") or obj.get("name"))[:20],
            "desc": str(obj.get("desc"))[:80], "basis": [str(b)[:60] for b in basis[:3]] or _fallback(profile, u)["basis"]}


def generate_personas(llm, profiles: dict, users: list, start_idx: int = 0) -> dict:
    """대상 사용자들(프로필 보유 · 로그 산출 완료)의 페르소나 생성 → {user_id: 카드}.
    카드 구조는 usermeta.PERSONAS 정의와 동일(form·intensity) + generated 표식."""
    out = {}
    for i, u in enumerate(users[:GEN_CAP]):
        uid = u.get("user_id", "")
        p = profiles.get(uid) or {}
        card = _gen_one(llm, p, u)
        out[uid] = {"id": f"G{start_idx + i + 1}", "generated": True, "user_id": uid,
                    "name": card["name"], "full": card["full"], "desc": card["desc"],
                    "basis": card["basis"], "form": u.get("form") or {},
                    "intensity": u.get("intensity") or {}}
    return out
