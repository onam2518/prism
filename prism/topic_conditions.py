"""토픽 조건식 트리와 3값 논리. None은 미판정이며 부정해도 미판정이다."""
from . import meta_contract as MC

_FIELDS = {"entities", "intent", "content_category", "source"}
_ALIASES = {"keywords": "entities", "intents": "intent", "cats": "content_category", "srcs": "source"}


def sanitize(expr, depth=0):
    if depth > 12 or not isinstance(expr, dict):
        raise ValueError("조건식은 깊이 12 이하의 객체여야 합니다")
    groups = [k for k in ("all", "any", "not") if k in expr]
    if groups:
        if len(groups) != 1 or len(expr) != 1:
            raise ValueError("조건 그룹에는 all·any·not 하나만 지정하세요")
        k = groups[0]
        if k == "not":
            return {k: sanitize(expr[k], depth + 1)}
        if not isinstance(expr[k], list) or not 1 <= len(expr[k]) <= 50:
            raise ValueError("조건 그룹은 1~50개 조건이 필요합니다")
        return {k: [sanitize(e, depth + 1) for e in expr[k]]}
    if not isinstance(expr.get("field"), str):
        raise ValueError("조건 필드는 문자열이어야 합니다")
    field = _ALIASES.get(expr.get("field"), expr.get("field"))
    op = expr.get("op", "in")
    values = expr.get("values", [expr["value"]] if "value" in expr else [])
    if field not in _FIELDS or op not in ("in", "eq") or not isinstance(values, list):
        raise ValueError("지원하지 않는 조건 필드·연산자입니다")
    if not 1 <= len(values) <= 50 or any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError("조건값은 비어 있지 않은 문자열 목록이어야 합니다")
    if op == "eq" and len(values) != 1:
        raise ValueError("eq 조건은 값 하나만 사용합니다")
    return {"field": field, "op": op, "values": list(dict.fromkeys(values))}


def evaluate(expr, row):
    if "all" in expr or "any" in expr:
        k = "all" if "all" in expr else "any"
        vals = [evaluate(e, row) for e in expr[k]]
        if k == "all":
            return False if False in vals else None if None in vals else True
        return True if True in vals else None if None in vals else False
    if "not" in expr:
        v = evaluate(expr["not"], row)
        return None if v is None else not v
    im = row.get("item_meta") or {}
    field = expr["field"]
    if field == "source":
        from .topic import feed_fields
        f = feed_fields(row)
        values = {v for v in (f["service"], f["cp"], f["channel"], f["cp_type"]) if v}
    else:
        # 명시된 상태·정책이 미확정/구버전이면 값이 남아 있어도 활용하지 않는다.
        status = (im.get("meta_status") or {}).get(field)
        version = im.get("policy_version")
        if status not in (None, "success") or (version and version != "dnm-common-2026-09-29-r3"):
            return None
        values = (set(MC.entity_names(im.get(field))) if field == "entities" else
                  set(MC.category_paths(im.get(field))) if field == "content_category" else
                  set(im.get(field) or []))
        if field == "content_category":
            values |= {v.split(" / ")[0] for v in values}
    if not values:
        return None
    return bool(values.intersection(expr["values"]))
