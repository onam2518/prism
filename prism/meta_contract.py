"""공통 메타의 값 검증과 표시용 투영. 구 문자열 이력은 자동으로 현행 정답이 되지 않는다."""
from . import dictionaries as D

FIELDS = ("summary", "entities", "intent", "content_category")
ENTITY_TYPES = {"PS", "OG", "LC", "AF", "EV", "TM", None}
# 호출 실패 대체 카테고리(2026-10-14 정책): 모델 호출이 끝내 실패한 분류 필드(intent·content_category)는
# 빈 값(NULL) 대신 이 값을 단다. 사전 값이 아니므로 채점·정답·승격·학습에서는 '분류 없음'으로 본다.
# 사람 검수 라우팅은 그대로 meta_status·hold_fields 가 맡는다.
FAIL_CATEGORY = "ETC_OUT"
FAIL_STATUSES = ("call_failed", "invalid_output", "configuration_error")
NO_CATEGORY = ("Unclassified", FAIL_CATEGORY)


def fail_value(field):
    """실패 상태 필드에 저장할 값. 분류 필드만 대체 카테고리 · 리드문·엔티티는 종전대로 빈 값."""
    if field == "intent":
        return [FAIL_CATEGORY]
    if field == "content_category":
        return [{"tier1": FAIL_CATEGORY, "tier2": None}]
    return "" if field == "summary" else []


def fill_failed(im):
    """호출 실패로 비어 있는 분류 필드를 대체 카테고리로 채운다(임베딩 등으로 채워진 값은 둔다)."""
    statuses = getattr(im, "meta_status", None) or {}
    for field in ("intent", "content_category"):
        if not getattr(im, field) and statuses.get(field) in FAIL_STATUSES:
            setattr(im, field, fail_value(field))


def real_categories(values):
    """미분류·실패 대체값을 뺀 카테고리 경로(승격·배정 판정용)."""
    return [p for p in category_paths(values) if p not in NO_CATEGORY]


def drop_fail(values):
    """검수 저장 값에서 실패 대체값 제거 · 그대로 저장해 확정값이 되는 것을 막는다."""
    return [v for v in values if FAIL_CATEGORY not in (v, category_path(v))]


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
        if ((evidence or {}).get("input_revision") != incoming.get("input_revision")
                or previous.get("policy_version") != incoming.get("policy_version")):
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


def current_meta(row):
    im = row.get("item_meta") or {}
    if im.get("contract_version") == "dnm-common-2026-09-29-r3":
        return (im.get("publishable") is True and im.get("scope_status") == "eligible"
                and bool(row.get("_dnm_policy_version"))
                and im.get("policy_version") == row["_dnm_policy_version"]
                and im.get("input_revision") == row.get("_dnm_input_revision")
                and im.get("item_unique_key") == row.get("_dnm_item_unique_key"))
    return not im.get("policy_version") or im["policy_version"] == "dnm-common-2026-09-29-r3"
