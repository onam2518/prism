"""Verifier: 정합성·스키마·사전 화이트리스트 강제 (결정론, LLM 불필요)."""
from . import dictionaries as D


def verify_quality(qm, active_metas: list) -> list:
    """QualityMeta 를 제자리 정제. 위반 표식 리스트 반환(trace.fallbacks 용)."""
    notes = []
    allowed = set(active_metas)

    # 1) 사전 밖 / 비활성 메타 제거
    clean = []
    for r in qm.reasons or []:
        if r == D.QUALITY_NORMAL:
            continue
        if r in allowed:
            clean.append(r)
        else:
            notes.append(f"quality.reason 사전외/비활성 제거: {r}")
    # 중복 제거(순서 유지)
    seen = set()
    clean = [r for r in clean if not (r in seen or seen.add(r))]

    # 2) 정합성 강제: reasons 유무 ↔ finalGrade
    qm.reasons = clean
    forced = "R" if clean else "G"
    if qm.finalGrade != forced:
        notes.append(f"finalGrade 정합 보정: {qm.finalGrade}→{forced}")
        qm.finalGrade = forced
    return notes


def verify_item(im, content) -> list:
    notes = []
    if im is None:
        return notes

    # entities 1~3개
    if len(im.entities) > 3:
        notes.append(f"entities {len(im.entities)}→3 절단")
        im.entities = im.entities[:3]
    im.entities = [e for e in im.entities if isinstance(e, str) and e.strip()]

    # intent(분류값): 사전 화이트리스트
    valid_intents = set(D.intent_categories_for(content.displayServiceName))
    clean_int = []
    for c in im.intent or []:
        if c in valid_intents:
            clean_int.append(c)
        else:
            notes.append(f"intent 사전외 제거: {c}")
    im.intent = clean_int[:2]  # 1~2개

    # content_category: 콘텐츠 단위 N개(1312). Tier1 화이트리스트 강제·중복 제거
    clean_ec = []
    for cat in (im.content_category or []):
        tier1 = str(cat).split("/")[0].strip()
        if tier1 in D.IAB_TIER1:
            if cat not in clean_ec:
                clean_ec.append(cat)
        else:
            notes.append(f"content_category Tier1 사전외 제거: {cat}")
    im.content_category = clean_ec
    return notes


def verify_legal(lm) -> list:
    notes = []
    if not lm.enabled:
        return notes
    valid = set(D.LEGAL_HARM_TYPES.keys())
    kept = []
    best_score, best_grade = 0, "GREEN"
    for ht in lm.harm_types:
        if ht.code not in valid:
            notes.append(f"legal.harm 사전외 제거: {ht.code}")
            continue
        total = ht.scores.get("total", 0)
        ht.grade = D.legal_grade(total)
        kept.append(ht)
        if total > best_score:
            best_score, best_grade = total, ht.grade
    lm.harm_types = kept
    lm.representative_score = best_score
    lm.representative_grade = best_grade
    return notes
