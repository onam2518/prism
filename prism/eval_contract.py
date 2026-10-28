"""평가 경로가 공유하는 정답 상태 규칙. 원문·정답의 변경은 수행하지 않는다."""


def field_status(expected, field):
    if field == "intent" and expected.get("intent_review") == "needed":
        return "pending"
    status = (expected.get("meta_status") or {}).get(field)
    if status not in (None, "success", "no_value"):
        return "excluded"
    if status == "no_value":
        return "excluded" if expected.get(field) else "scored"
    if expected.get(field) is None:
        return "missing"
    if not expected[field] or isinstance(expected[field], str) and not expected[field].strip():
        return "empty"
    if field == "summary" and not isinstance(expected[field], str):
        return "excluded"
    if field == "content_category":
        from .metaeval import _cats
        if not _cats(expected[field]):
            return "excluded"
    if field == "entities":
        from .metaeval import _ents
        if not _ents(expected[field]):
            return "excluded"
    if field == "intent":
        from .abtest import intent_expected
        if not intent_expected(expected):
            return "excluded"
    return "scored"
