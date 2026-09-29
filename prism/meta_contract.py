"""공통 메타의 값 검증과 표시용 투영. 구 문자열 이력은 자동으로 현행 정답이 되지 않는다."""
from . import dictionaries as D

FIELDS = ("summary", "entities", "intent", "content_category")
ENTITY_TYPES = {"PS", "OG", "LC", "AF", "EV", "TM", None}


def entity_name(value):
    name = value.get("name") if isinstance(value, dict) else value
    return name if isinstance(name, str) else ""


def entity_names(values):
    return [entity_name(v) for v in (values or []) if entity_name(v)]


def category_path(value):
    if isinstance(value, dict):
        return " / ".join(v for v in (value.get("tier1"), value.get("tier2")) if isinstance(v, str) and v)
    return value if isinstance(value, str) else ""


def category_paths(values):
    return [category_path(v) for v in (values or []) if category_path(v)]


def clean_entities(values):
    out = []
    for v in values if isinstance(values, (list, tuple)) else []:
        name = entity_name(v).strip()
        if not name:
            continue
        if isinstance(v, dict):
            if "type" not in v or (v["type"] is not None and (not isinstance(v["type"], str) or v["type"] not in ENTITY_TYPES)):
                continue
            v = {"name": name, "type": v["type"]}
        else:
            v = name
        if v not in out:
            out.append(v)
    return out


def clean_categories(values):
    out = []
    for v in values if isinstance(values, (list, tuple)) else []:
        if isinstance(v, dict):
            t1, t2 = v.get("tier1"), v.get("tier2")
            if (not isinstance(t1, str) or t1 not in D.IAB_TIER1 or "tier2" not in v
                    or (t2 is not None and t2 not in D.CONTENT_CATEGORY_TIER2.get(t1, []))):
                continue
        path = D.normalize_content_category(category_path(v))
        if not path or path == "Unclassified":
            continue
        if isinstance(v, dict):
            if "tier1" not in v or "tier2" not in v:
                continue
            parts = path.split(" / ", 1)
            v = {"tier1": parts[0], "tier2": parts[1] if len(parts) > 1 else None}
        else:
            v = path
        if v not in out:
            out.append(v)
    return out


def preserve_manual(previous, incoming):
    """자동 저장은 검수 확정값을 보존하고, 입력 변경 시 재검수 상태를 남긴다."""
    previous, incoming = previous or {}, dict(incoming or {})
    manual = previous.get("manual_fields") or {}
    if not manual or incoming.get("manual_fields"):
        return incoming
    statuses = dict(incoming.get("meta_status") or {})
    required = list(incoming.get("manual_review_required") or [])
    hold = list(incoming.get("hold_fields") or [])
    for field, evidence in manual.items():
        if field not in FIELDS:
            continue
        incoming[field] = previous.get(field, "" if field == "summary" else [])
        if (evidence or {}).get("input_revision") != incoming.get("input_revision"):
            if field not in required:
                required.append(field)
            if field not in hold:
                hold.append(field)
            statuses[field] = "pending"
        else:
            statuses[field] = "success" if incoming[field] else "no_value"
            hold = [v for v in hold if v != field]
    incoming.update(manual_fields=manual, meta_status=statuses,
                    manual_review_required=required, hold_fields=hold)
    return incoming
