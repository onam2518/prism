"""사전 도메인 (serve 에서 분리 · 라우트 분리 4차).

구사전 화면 데이터(dict_data)·편집 오버라이드(_read/load/edit/reset)와
엔티티 사전 서버 글루(entdict_data·entdict_action·백그라운드 보강 _enrich_*)를 담당한다.
어휘 원천은 dictionaries · 엔티티 사전 실체는 entdict · HTTP 디스패치는 serve 가 유지.

컴포지션: 스토어·집계 캐시는 serve 가 `_SV` 로 주입(learnops 와 동일 관례).
"""
from __future__ import annotations

import json
import os
import threading
import time

from . import feedback_loop as FL

# 오버라이드 파일 경로 상수는 serve 에 남긴다(테스트가 serve._DICT_OVERRIDES_PATH 를
# 패치하는 계약 · 전역 상태는 serve 유지 원칙) → 여기서는 _SV 경유로 읽는다.
_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입


def dict_data() -> dict:
    """\uc0ac\uc804\u00b7\ub9e4\ud551 \ubaa8\ub4c8: \uc778\ud150\ud2b8\u00b7\ucf58\ud150\uce20 \uce74\ud14c\uace0\ub9ac\u00b7\ud488\uc9c8\u00b7\ubc95\ub839 \uc0ac\uc804\uc744 \uadf8\ub300\ub85c \ub178\ucd9c."""
    from . import dictionaries as D
    return {
        "serviceGroups": list(D.SERVICE_GROUP.keys()),
        "intentUniversal": list(D.INTENT_CATEGORIES_UNIVERSAL),
        "intentForm": list(getattr(D, "INTENT_FORM_UNIVERSAL", [])),
        "intentByService": {k: list(v) for k, v in D.INTENT_CATEGORIES_BY_SERVICE.items()},
        "serviceKeyMap": dict(getattr(D, "_SERVICE_NAME_MAP", {})),   # displayServiceName → 서비스 카테고리 키(검수 인텐트 불일치 경고용)
        "iabTier1": list(D.IAB_TIER1),
        "tier2": {k: list(v) for k, v in getattr(D, "CONTENT_CATEGORY_TIER2", {}).items()},
        "tier1Ko": dict(getattr(D, "IAB_TIER1_KO", {})),   # 한글 표시명(UI 전용 · 공식 표기는 영문)
        "tier2Ko": dict(getattr(D, "TIER2_KO", {})),
        # 도움말 표 원천: Tier2 정의·예시 / 인텐트 예시 / 등급 판정 계약
        "tier2Defs": {k: {"def": v[0], "ex": v[1]} for k, v in getattr(D, "TIER2_DEFS", {}).items()},
        "intentExamples": dict(getattr(D, "INTENT_EXAMPLES", {})),
        "gradeDefs": list(getattr(D, "GRADE_DEFS", [])),
        "iabMap": dict(getattr(D, "CATEGORY_IAB_MAP", {})),
        "domainGroups": {k: list(v) for k, v in getattr(D, "DOMAIN_GROUP_MAP", {}).items()},
        # 인텐트 정의 = 범용①(UI 전용) + 사전 원문 병합 · 데스크탑·모바일 검수 화면 공용 단일 원천
        "intentDefs": {**getattr(D, "INTENT_UNIVERSAL_DEFS", {}), **getattr(D, "INTENT_VALUE_DEFS", {})},
        # 교정 요소 사전(id·한글 라벨·단계) · feedback_loop 가 단일 원천(검수 UI 이원화 부채 해소)
        "fixElements": [{"id": e, "label": FL.ELEM_KO.get(e, e), "stage": FL.ELEM_STAGE.get(e, "analyze")}
                        for e in FL.ELEMENTS],
        "categoryCriteria": dict(getattr(D, "CATEGORY_CRITERIA", {})),
        "qualityMetas": dict(D.QUALITY_METAS),
        "qualityNames": dict(getattr(D, "QUALITY_META_NAMES", {})),
        "qualityApplies": dict(getattr(D, "QUALITY_META_APPLIES", {})),
        "legalTypes": {c: {"label": v.get("label", c), "article": v.get("article", "")}
                       for c, v in D.LEGAL_HARM_TYPES.items()},
        "intakePolicy": {k: dict(v) for k, v in getattr(D, "INTAKE_POLICY", {}).items()},
    }


# ── 엔티티 사전 모듈 · 개체 고유키·타입·속성 관리 + 위키데이터/나무위키 보강 ──
# 일괄 보강 진행 상태(단일 실행 가드): UI 가 GET /entdict 폴링으로 N/M 진척을 표시.
_ENRICH_STATE = {"running": False, "total": 0, "done": 0, "hit": 0, "miss": 0, "fail": 0, "finished_at": 0}
_ENRICH_LOCK = threading.Lock()


def _enrich_run(st, ids):
    from . import entdict as ED
    ED._wd_breaker_reset()                          # 배치 시작마다 브레이커 리셋: 이전 배치의 tripped 가 이 배치를 영구 차단하지 않게
    S = _ENRICH_STATE
    for i, eid in enumerate(ids):
        if i:
            time.sleep(ED.ENRICH_DELAY)              # 위키데이터 429 회피(예의 호출)
        try:
            r = ED.enrich_entity(st, eid)
            if not r.get("ok"):
                S["fail"] += 1
            elif r.get("matched"):
                S["hit"] += 1
            else:
                S["miss"] += 1
        except Exception:
            S["fail"] += 1
        S["done"] += 1
    S["running"] = False
    S["finished_at"] = time.time()


def _enrich_start(st, ids) -> bool:
    """일괄 보강 백그라운드 시작. 이미 실행 중이거나 대상 없음 → False."""
    with _ENRICH_LOCK:
        if _ENRICH_STATE["running"] or not ids:
            return False
        _ENRICH_STATE.update(running=True, total=len(ids), done=0, hit=0, miss=0, fail=0, finished_at=0)
    threading.Thread(target=_enrich_run, args=(st, list(ids)), daemon=True).start()
    return True


_ENT_NORMALIZED = False                                    # 미등재 이행(1회성) 실행 여부


def entdict_data(q: str = "", type_: str = "", status: str = "", limit: int = 300) -> dict:
    """목록·통계·메타(타입/속성 필드 사전) + 일괄 보강 진행 상태. 편집 폼·필터의 단일 원천."""
    from . import entdict as ED
    global _ENT_NORMALIZED
    st = _SV.get_store()
    meta = {"types": dict(ED.ENTITY_TYPES),
            "attrFields": {t: [[k, lb] for k, lb in fs] for t, fs in ED.ATTR_FIELDS.items()},
            "occupationGroups": [g for g, _ in ED.OCCUPATION_GROUPS] + ["기타"],
            "eattrKeys": list(ED.ALLOWED_EATTR_KEYS)}
    if not (st and hasattr(st, "ent_list")):
        return {"items": [], "stats": {}, "meta": meta, "enrich": dict(_ENRICH_STATE)}
    if not _ENT_NORMALIZED:                                # 구 데이터: 미스 기록 보류 → 미등재 이행
        _ENT_NORMALIZED = True
        try:
            st.ent_mark_unlisted()
        except Exception:
            pass
    return {"items": st.ent_list(q=q, type_=type_, status=status, limit=limit),
            "stats": st.ent_stats(), "meta": meta, "enrich": dict(_ENRICH_STATE)}


def entdict_action(data: dict, team=None, mock: bool = False) -> dict:
    """변경·보강 액션. update 는 사람 확정(수동) — attr_meta 를 confirmed 로 마킹해
    이후 위키데이터 재보강이 덮어쓰지 않게 한다(사전은 사람이 최종 결정)."""
    from . import entdict as ED
    st = _SV.get_store()
    if not (st and hasattr(st, "ent_upsert")):
        return {"ok": False, "error": "저장소 없음"}
    action = (data.get("action") or "").strip()
    eid = (data.get("id") or "").strip()
    if action != "detail":
        _SV._agg_bump()                                   # 등재·수정·보강은 토픽 엔티티 속성 인덱스에 반영 → 캐시 무효화

    if action == "detail":
        e = st.ent_get(eid)
        if not e:
            return {"ok": False, "error": "개체 없음"}
        return {"ok": True, "entity": e, "aliases": st.ent_aliases(eid),
                "contents": st.ent_contents(eid)}

    if action == "update":
        e = st.ent_get(eid)
        if not e:
            return {"ok": False, "error": "개체 없음"}
        am = dict(e.get("attr_meta") or {})
        fields = {"updated_at": time.time()}
        if "type" in data:
            t = (data.get("type") or "").strip()
            if t and t not in ED.ENTITY_TYPES:
                return {"ok": False, "error": f"허용되지 않는 타입: {t}"}
            fields["type"] = t
            fields["status"] = "active" if t else "pending"
            am["type"] = {"source": "manual", "status": "confirmed"}
        skipped = []                              # 타입 스키마에 없는 속성 키(조용한 유실 방지 · 호출자에 알림)
        if isinstance(data.get("attrs"), dict):
            attrs = dict(e.get("attrs") or {})
            typ = fields.get("type", e.get("type") or "")
            allowed = {k for k, _ in ED.ATTR_FIELDS.get(typ, [])}
            for k, v in data["attrs"].items():
                if allowed and k not in allowed:
                    skipped.append(k)
                    continue
                v = str(v or "").strip()
                if v:
                    attrs[k] = v
                    am[k] = {"source": "manual", "status": "confirmed"}
                else:
                    attrs.pop(k, None)
                    am.pop(k, None)
            fields["attrs"] = attrs
        alias = ED.normalize_name(data.get("alias") or "")
        if alias:
            other = st.ent_id_by_alias(alias)
            if other and other != eid:
                return {"ok": False, "error": "이미 다른 개체의 별칭입니다"}
            st.ent_alias_add(alias, eid)
        fields["attr_meta"] = am
        st.ent_update(eid, fields)
        out = {"ok": True, "entity": st.ent_get(eid), "aliases": st.ent_aliases(eid)}
        if skipped:
            out["skipped_attrs"] = skipped
        return out

    if action == "add":
        name = ED.normalize_name(data.get("name") or "")
        if not name:
            return {"ok": False, "error": "이름이 필요합니다"}
        if st.ent_id_by_alias(name):
            return {"ok": False, "error": "이미 등재된 개체(별칭 포함)입니다"}
        e = ED._empty_entry(name)
        st.ent_upsert(e)
        st.ent_alias_add(name, e["entity_id"])
        return {"ok": True, "entity": st.ent_get(e["entity_id"])}

    if action == "delete":
        return {"ok": st.ent_delete(eid)}

    if action == "purge_unlisted":
        return {"ok": True, "purged": st.ent_purge_unlisted()}

    if action == "enrich":
        if mock:
            return {"ok": True, "mock": True, "matched": False}
        return ED.enrich_entity(st, eid)

    if action == "enrich_pending":
        if mock:
            return {"ok": True, "mock": True, "queued": 0}
        # scope=all → 전체 재보강(조회 이력 있어도 다시 · 소스 우선순위 변경 반영 · 확정 필드 보존)
        if (data.get("scope") or "") == "all":
            ids = st.ent_ids(int(data.get("limit") or 5000))
        else:
            ids = st.ent_pending_ids(int(data.get("limit") or 200))
        started = _enrich_start(st, ids)
        return {"ok": True, "queued": len(ids) if started else 0,
                "already_running": bool(ids) and not started and _ENRICH_STATE["running"],
                "enrich": dict(_ENRICH_STATE)}

    if action == "backfill":
        rows = st.recent(int(data.get("limit") or 1000), team=team)
        r = ED.ingest_rows(st, rows, team=team or "")
        queued = 0
        if r.get("new_ids") and not mock and os.environ.get("PRISM_ENTDICT_ENRICH", "1") == "1":
            if _enrich_start(st, r["new_ids"]):
                queued = len(r["new_ids"])
        return {"ok": True, "scanned": len(rows), "created": r["created"], "linked": r["linked"],
                "enrich_queued": queued, "enrich": dict(_ENRICH_STATE)}

    return {"ok": False, "error": f"알 수 없는 액션: {action}"}




def _read_overrides() -> dict:
    try:
        if os.path.exists(_SV._DICT_OVERRIDES_PATH):
            with open(_SV._DICT_OVERRIDES_PATH, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"  ⚠️ 사전 오버라이드 파일 읽기 실패 · 무시하고 코드 기본 사전 사용"
              f"({_SV._DICT_OVERRIDES_PATH}): {type(e).__name__}: {e}")
    return {}


def _fmt_vals(vals) -> str:
    v = [str(x) for x in vals]
    return ", ".join(v[:12]) + (f" 외 {len(v) - 12}종" if len(v) > 12 else "")


def load_dict_overrides():
    """저장된 사전 편집(overrides)을 dictionaries 에 적용(서버 시작 시).
    적용 결과를 기동 로그로 보고한다 — 코드 기본값이 오버라이드 때문에 조용히 사라지지 않도록
    (복원분·제외분 모두 경고, 예외도 삼키지 않고 로그)."""
    from . import dictionaries as D
    ov = _read_overrides()
    if not ov:
        return
    try:
        rep = D.apply_profile(ov) or {}
    except Exception as e:
        print(f"  ⚠️ 사전 오버라이드 적용 실패 · 코드 기본 사전으로 계속: {type(e).__name__}: {e}")
        return
    restored = rep.get("restored") or {}
    for path, vals in sorted(restored.items()):
        print(f"  ⚠️ 사전 오버라이드에 코드 기본값 {len(vals)}종 누락 → 복원: {path} · {_fmt_vals(vals)}")
    for path, vals in sorted((rep.get("dropped") or {}).items()):
        print(f"  ⚠️ 사전 오버라이드가 코드 기본값 {len(vals)}종 제외(사용자 삭제 기록): "
              f"{path} · {_fmt_vals(vals)}")
    if restored and D.REMOVED_KEY not in ov:
        print("  · 삭제 기록이 없는 구형 오버라이드입니다 — 사전 편집 화면에서 한 번 저장하면 "
              "삭제 의도가 기록되고 이후 코드 신규 값과 구분됩니다")


def edit_dict(data: dict) -> dict:
    """사전·정책 편집(사용자 직접 수정). target(+key) 에 value 를 덮어쓰고 영속화·적용."""
    from . import dictionaries as D
    target = (data.get("target") or "").strip()
    allowed = {"intent_universal", "intent_by_service", "iab_tier1", "tier2",
               "quality_metas", "legal_types", "domain_groups", "category_iab_map", "intake_policy"}
    if target not in allowed:
        return {"error": f"편집 불가 target: {target}"}
    ov = _read_overrides()
    key = data.get("key")
    val = data.get("value")
    if key is not None:
        if not isinstance(ov.get(target), dict):
            # 베이스 dict 를 복사해 시작(부분 키 편집이 다른 키를 지우지 않도록)
            base = getattr(D, {"intent_by_service": "INTENT_CATEGORIES_BY_SERVICE",
                               "tier2": "CONTENT_CATEGORY_TIER2", "quality_metas": "QUALITY_METAS",
                               "legal_types": "LEGAL_HARM_TYPES", "domain_groups": "DOMAIN_GROUP_MAP",
                               "category_iab_map": "CATEGORY_IAB_MAP",
                               "intake_policy": "INTAKE_POLICY"}.get(target, ""), {})
            ov[target] = {k: (list(v) if isinstance(v, list) else v) for k, v in dict(base).items()}
        ov[target][key] = val
    else:
        ov[target] = val
    try:
        # 코드 기본값에서 뺀 값 = 의도적 삭제로 기록(뒤에 코드에 추가될 값과 구분 · 병합 의미론의 전제)
        D.stamp_removals(ov, target)
    except Exception as e:
        print(f"  ⚠️ 사전 삭제 기록 실패(다음 기동에서 코드 기본값이 복원될 수 있음): "
              f"{type(e).__name__}: {e}")
    try:
        os.makedirs(os.path.dirname(_SV._DICT_OVERRIDES_PATH) or ".", exist_ok=True)
        with open(_SV._DICT_OVERRIDES_PATH, "w", encoding="utf-8") as f:
            json.dump(ov, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return {"error": "저장 실패: " + str(e)[:120]}
    try:
        D.apply_profile(ov)
    except Exception as e:
        return {"error": "적용 실패: " + str(e)[:120]}
    out = dict_data()
    out["saved"] = True
    return out


def reset_dict_overrides() -> dict:
    """편집 초기화: overrides 파일 삭제 + 원본 사전 즉시 복원(재시작 불필요)."""
    try:
        os.remove(_SV._DICT_OVERRIDES_PATH)
    except OSError:
        pass
    from . import dictionaries as D
    try:
        D.restore_base()                 # 메모리에 적용된 override 도 즉시 걷어냄
    except Exception:
        pass
    out = dict_data()
    out["resetNote"] = "초기화됨 · 원본 사전으로 복원"
    return out
