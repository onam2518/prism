"""콘텐츠 조회(메타베이스 경유) · 검수 지정.

데브 환경에서 메타가 발행된 콘텐츠를 메타베이스 API 로 조회하고, 고른 건을
프리즘으로 끌어와 기존 검수 계약(판정·교정·학습·정답셋)을 그대로 태운다.
기획 스펙: DNM 위키 Prism 페이지 "기획 · 콘텐츠 조회 → 검수 지정" 섹션.

수집 환경(2026-09-02 · docs/METACOLLECT_DESIGN.md): bi-portal 메타베이스는 사내망 전용이라
운영 서버가 직접 조회하지 못한다. 사내망 수집기(status-agent prism_push.py)가 발행분 행을
**스테이징**(/metaquery-stage)에 올리고, 운영자가 조회 화면에서 보고 고른 것만 검수로 지정한다.
스테이징은 검수 콘텐츠가 아니다(results 와 분리 · 지정 안 한 행은 TTL 로 삭제 · 보존 90일 대상 아님).
서버 직접 조회(source=metabase)는 사내망에서 프리즘을 띄운 경우에만 유효하다.

원칙
- 메타베이스 경유 · DB 직결 아님: DB 자격 증명을 보유하지 않는다. API 키(env
  PRISM_METABASE_KEY)로 요청하고 접근 범위·감사는 메타베이스가 담당.
- 재추출 없음: 발행 메타를 모델 초안으로 그대로 저장(store_save). add_contents
  경로를 쓰면 메타가 빈 dict 로 저장돼 STEP 2 가 재추출한다(mediaops 와 같은 이유).
- 서버만 호출: 브라우저가 메타베이스를 직접 부르지 않는다(키 보호).

쿼리 계약: 운영자가 시스템 설정에 저장하는 기본 SQL(metabase_query)이 아래 별칭을
SELECT 하면 화면·인입이 그대로 동작한다(데브 스키마가 확정되면 SQL 만 바꾸면 된다).
  id · service · title · subtitle · body · url · published_at · grade
  · summary · entities · intent · category · model · version
"""
from __future__ import annotations

import json
import os
import urllib.request

_SV = None            # serve 모듈 역참조(순환 import 회피 · serve 가 주입)

_LIMIT_DEFAULT = 50
_LIMIT_MAX = 200
_TIMEOUT_S = 30
_STAGE_TTL_DAYS = 7    # 스테이징 행 보존(올린 뒤 · 지정 여부 무관)
_FACET_SAMPLE = 2000   # 필터 선택지(서비스·인텐트·카테고리·모델)는 최근 올린 N건 표본에서 뽑는다(전량 훑기 회피)
_META_KEYS = ("intent", "category", "entity", "model", "meta")   # 저장소 SQL 밖에서 거르는 조건

# 조회 결과 행의 열 계약(운영자 SQL 별칭). 순서는 화면 표 기본 순서.
COLUMNS = ("id", "service", "title", "subtitle", "body", "url", "published_at",
           "grade", "summary", "entities", "intent", "category", "model", "version")


def _api_key() -> str:
    return (os.environ.get("PRISM_METABASE_KEY") or "").strip()


def _cfg():
    from .config import Config
    return Config.load()


def mq_status(team=None) -> dict:
    """설정·연결 상태(키 실값 미포함). 화면이 미설정 안내를 띄우는 근거."""
    cfg = _cfg()
    url = (getattr(cfg, "metabase_url", "") or "").strip()
    query = (getattr(cfg, "metabase_query", "") or "").strip()
    db_id = int(getattr(cfg, "metabase_db_id", 0) or 0)
    mock = bool(_SV.Handler.server_mock)
    staged, services, facets = 0, [], {"intents": [], "categories": [], "models": []}
    st = _SV.get_store()
    if st is not None and hasattr(st, "stage_count"):
        try:
            staged = int(st.stage_count(team=team))
            if staged:
                facets = _stage_facets(st, team)
                services = facets.pop("services")
        except Exception:
            staged = -1                                   # 표 미생성 등 · 화면은 '확인 불가'
    return {"ok": True, "url": url, "dbId": db_id, "hasKey": bool(_api_key()),
            "queryConfigured": bool(query), "mock": mock,
            "configured": mock or bool(url and db_id and _api_key() and query),
            "staged": staged, "stageTtlDays": _STAGE_TTL_DAYS, "services": services, "facets": facets,
            "columns": list(COLUMNS)}


def _sql_quote(v: str) -> str:
    """문자열 리터럴 이스케이프(작은따옴표 배가). 관리자 전용 경로지만 따옴표 깨짐 방지."""
    return "'" + str(v).replace("'", "''") + "'"


def _build_sql(base: str, f: dict, limit: int, offset: int) -> str:
    """운영자 기본 SQL 을 서브쿼리로 감싸 조건·페이지를 얹는다(별칭 계약 기준)."""
    conds = []
    if (f.get("service") or "").strip():
        conds.append("service = " + _sql_quote(f["service"].strip()))
    if (f.get("grade") or "").strip():
        conds.append("grade = " + _sql_quote(f["grade"].strip()))
    kw = (f.get("keyword") or "").strip()
    if kw:
        like = _sql_quote("%" + kw + "%")
        conds.append("(title ILIKE {0} OR body ILIKE {0})".format(like))
    if (f.get("date_from") or "").strip():
        conds.append("published_at >= " + _sql_quote(f["date_from"].strip()))
    if (f.get("date_to") or "").strip():
        conds.append("published_at < " + _sql_quote(f["date_to"].strip()))
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    return ("SELECT * FROM (" + base.rstrip().rstrip(";") + ") mq" + where +
            " ORDER BY published_at DESC LIMIT %d OFFSET %d" % (limit, offset))


def _call_metabase(sql: str) -> list:
    """POST /api/dataset (native query) → [{열: 값}] 행 목록. 표준 라이브러리만 사용."""
    cfg = _cfg()
    url = (cfg.metabase_url or "").rstrip("/") + "/api/dataset"
    body = json.dumps({"database": int(cfg.metabase_db_id or 0), "type": "native",
                       "native": {"query": sql}}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "X-API-KEY": _api_key()})
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    if j.get("error"):
        raise RuntimeError(str(j.get("error"))[:300])
    data = j.get("data") or {}
    cols = [(c.get("name") or "").lower() for c in (data.get("cols") or [])]
    return [dict(zip(cols, row)) for row in (data.get("rows") or [])]


def _as_list(v) -> list:
    """발행 메타의 배열 필드 관대 파싱: JSON 배열 문자열·구분자 문자열·리스트 모두 수용."""
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v or "").strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            arr = json.loads(s)
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()]
        except Exception:
            pass
    # 구분자: 세로줄·쉼표·띄어 쓴 가운뎃점(" · "). 붙여 쓴 가운뎃점은 값의 일부다("속보·단신" 같은 인텐트 명칭).
    for sep in ("|", ",", " · "):
        if sep in s:
            return [p.strip() for p in s.split(sep) if p.strip()]
    return [s]


def _row_content(row: dict) -> dict:
    return {"displayServiceName": str(row.get("service") or ""),
            "title": str(row.get("title") or ""),
            "subtitle": str(row.get("subtitle") or ""),
            "body": str(row.get("body") or ""),
            "source_url": _SV._safe_url(str(row.get("url") or "")),
            "image_urls": []}


def _row_hash(row: dict) -> str:
    from .store import content_hash
    return content_hash(_row_content(row))


def _mock_rows(f: dict, limit: int, offset: int) -> list:
    """모의 모드 표본(로컬 데모·테스트). 열 계약과 동일한 모양."""
    base = []
    for i in range(1, 8):
        base.append({
            "id": "dev-%03d" % i, "service": "뉴스" if i % 3 else "스포츠",
            "title": "데브 발행 예시 기사 %d" % i, "subtitle": "",
            "body": "메타베이스 조회 모의 본문입니다. 발행 메타가 붙은 예시 콘텐츠 %d." % i,
            "url": "https://example.com/dev/%d" % i,
            "published_at": "2026-09-01T0%d:00:00" % (i % 10),
            "grade": "G" if i % 4 else "R",
            "summary": "예시 기사 %d 관련 내용을 정리" % i,
            "entities": "예시 개체 %d · 데브 환경" % i,
            "intent": "속보·단신", "category": "News and Politics / Politics",
            "model": "dev-extractor", "version": 1})
    kw = (f.get("keyword") or "").strip()
    if kw:
        base = [r for r in base if kw in r["title"] or kw in r["body"]]
    if (f.get("grade") or "").strip():
        base = [r for r in base if r["grade"] == f["grade"].strip()]
    if (f.get("service") or "").strip():
        base = [r for r in base if r["service"] == f["service"].strip()]
    return base[offset:offset + limit]


def _stage_facets(st, team) -> dict:
    """필터 선택지(서비스·인텐트·카테고리·모델): 최근 올린 _FACET_SAMPLE 건 표본에서 뽑는다.
    하루 3.5만 건 규모에서 전량 훑지 않기 위한 타협 · 선택지는 '최근 수집분 기준'이다."""
    svcs, intents, cats, models = set(), set(), set(), set()
    for r in st.stage_list({}, _FACET_SAMPLE, 0, team=team):
        if str(r.get("service") or "").strip():
            svcs.add(str(r["service"]).strip())
        intents.update(_as_list(r.get("intent")))
        cats.update(_as_list(r.get("category")))
        m = str(r.get("model") or "").strip()
        if m:
            models.add(m)
    return {"services": sorted(svcs), "intents": sorted(intents), "categories": sorted(cats), "models": sorted(models)}


def _has_meta(r: dict) -> bool:
    g = str(r.get("grade") or "").strip().upper()
    return bool(str(r.get("summary") or "").strip() or _as_list(r.get("entities")) or _as_list(r.get("intent"))
                or _as_list(r.get("category")) or g in ("G", "R"))


def _meta_match(r: dict, f: dict) -> bool:
    """메타별 필터: 인텐트·카테고리(값 포함) · 엔티티(부분 일치) · 모델(일치) · 메타 유무(with|without)."""
    v = (f.get("intent") or "").strip()
    if v and v not in _as_list(r.get("intent")):
        return False
    v = (f.get("category") or "").strip()
    if v and v not in _as_list(r.get("category")):
        return False
    v = (f.get("entity") or "").strip().lower()
    if v and not any(v in e.lower() for e in _as_list(r.get("entities"))):
        return False
    v = (f.get("model") or "").strip()
    if v and str(r.get("model") or "").strip() != v:
        return False
    v = (f.get("meta") or "").strip()
    if v == "with" and not _has_meta(r):
        return False
    if v == "without" and _has_meta(r):
        return False
    return True


def mq_search(data: dict, team=None) -> dict:
    """조건 조회. source=stage(기본 · 수집기가 올린 스테이징) | metabase(서버 직접 호출 · 사내망 전용).
    반환 행에 프리즘 인입 여부(registered)를 함께 표시한다."""
    st_info = mq_status(team)
    limit = max(1, min(_LIMIT_MAX, int(data.get("limit") or _LIMIT_DEFAULT)))
    offset = max(0, int(data.get("offset") or 0))
    f = {k: data.get(k) for k in ("service", "grade", "keyword", "date_from", "date_to")}
    mf = {k: str(data.get(k) or "") for k in _META_KEYS}
    meta_on = any(v.strip() for v in mf.values())
    source = str(data.get("source") or "stage")
    if source == "stage":
        st = _SV.get_store()
        if st is None or not hasattr(st, "stage_list"):
            return {"ok": False, "error": "저장소가 준비되지 않았습니다"}
        try:
            rows = st.stage_list({**f, **mf}, limit, offset, team=team)   # 메타 조건도 저장소가 거른다(전량 훑기 없음)
        except Exception as e:
            return {"ok": False, "error": "스테이징 조회 실패 · 표(prism_mq_stage) 생성 여부를 확인하세요 (SUPABASE_MIGRATION.md) · "
                    + str(e)[:120]}
    elif st_info["mock"]:
        rows = [r for r in _mock_rows(f, _LIMIT_MAX, 0) if _meta_match(r, mf)][offset:offset + limit] if meta_on \
            else _mock_rows(f, limit, offset)
    elif not st_info["configured"]:
        return {"ok": False, "error": "메타베이스 연결이 설정되지 않았습니다 · 시스템 설정에서 URL·DB·키·기본 SQL 을 저장하세요"}
    else:
        cfg = _cfg()
        try:
            rows = _call_metabase(_build_sql(cfg.metabase_query, f, limit, offset))
        except Exception as e:
            return {"ok": False, "error": "메타베이스 조회 실패 · " + str(e)[:200]}
    hashes = [_row_hash(r) for r in rows]
    known = {}
    st = _SV.get_store()
    if st is not None and hashes and hasattr(st, "existing_hashes"):
        try:
            known = st.existing_hashes(hashes, team=team) or {}
        except Exception:
            known = {}
    out = []
    for r, ch in zip(rows, hashes):
        row = {k: r.get(k) for k in COLUMNS}
        row["hash"] = ch
        row["registered"] = ch in known
        if r.get("staged_at") is not None:
            row["staged_at"] = r.get("staged_at")
        out.append(row)
    return {"ok": True, "rows": out, "n": len(out), "offset": offset, "limit": limit,
            "source": source, "mock": st_info["mock"] and source != "stage"}


def mq_stage(data: dict, team=None) -> dict:
    """수집기(사내망)가 조회 결과 행을 올린다. 검수 지정이 아니다 · 화면에서 고르기 전 목록.
    행 모양은 register 와 같다(COLUMNS 별칭). 제목·본문이 모두 비면 제외. 같은 해시는 최신으로 덮는다."""
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        return {"ok": False, "error": "올릴 행이 없습니다"}
    if len(rows) > _LIMIT_MAX:
        return {"ok": False, "error": "한 번에 %d건까지 올릴 수 있습니다" % _LIMIT_MAX}
    items, empty = [], 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        c = _row_content(r)
        if not (c["title"].strip() or c["body"].strip()):
            empty += 1
            continue
        row = {k: r.get(k) for k in COLUMNS}
        row["url"] = c["source_url"]                      # 스킴 화이트리스트 통과분만 보관
        for k in ("entities", "intent", "category"):      # 목록 필드는 리스트로 통일(필터·표시 일관)
            row[k] = _as_list(r.get(k))
        row["model"] = str(r.get("model") or "").strip()
        items.append((_row_hash(r), row))
    if not items:
        return {"ok": False, "error": "올릴 수 있는 행이 없습니다 (제목·본문 비어 있음)"}
    st = _SV.get_store()
    if st is None or not hasattr(st, "stage_put"):
        return {"ok": False, "error": "저장소가 준비되지 않았습니다"}
    try:
        res = st.stage_put(items, team=team)
        purged = int(st.stage_purge(_STAGE_TTL_DAYS, team=team) or 0)
        staged = int(st.stage_count(team=team))
    except Exception as e:
        return {"ok": False, "error": "스테이징 저장 실패 · 표(prism_mq_stage) 생성 여부를 확인하세요 (SUPABASE_MIGRATION.md) · "
                + str(e)[:120]}
    return {"ok": True, "added": int(res.get("added", 0)), "updated": int(res.get("updated", 0)),
            "skipped_empty": empty, "purged": purged, "staged": staged}


def mq_stage_delete(data: dict, team=None) -> dict:
    """스테이징 행 삭제(화면에서 고른 뒤 남은 것 정리). 검수 콘텐츠(results)는 건드리지 않는다."""
    hashes = [str(h) for h in (data.get("hashes") or []) if h]
    if not hashes:
        return {"ok": False, "error": "삭제할 행을 선택하세요"}
    st = _SV.get_store()
    if st is None or not hasattr(st, "stage_delete"):
        return {"ok": False, "error": "저장소가 준비되지 않았습니다"}
    try:
        n = int(st.stage_delete(hashes, team=team) or 0)
        staged = int(st.stage_count(team=team))
    except Exception as e:
        return {"ok": False, "error": "스테이징 삭제 실패 · " + str(e)[:120]}
    return {"ok": True, "deleted": n, "staged": staged}


def _row_out(row: dict, team=None) -> dict:
    """발행 메타 → 모델 초안(out). review=yellow 로 저장해 검수 큐 계약에 태운다."""
    item = {}
    if str(row.get("summary") or "").strip():
        item["summary"] = str(row.get("summary")).strip()
    for src, dst in (("entities", "entities"), ("intent", "intent"), ("category", "content_category")):
        vals = _as_list(row.get(src))
        if vals:
            item[dst] = vals
    grade = str(row.get("grade") or "").strip().upper()
    quality = {"finalGrade": grade if grade in ("G", "R") else "",
               "reasons": [], "review": "yellow", "confidence": 0.5}
    try:
        ver = int(row.get("version") or 0)
    except Exception:
        ver = 0
    if ver <= 0:
        try:
            ver = int(_SV._batch_seq_cached(team)) + 1
        except Exception:
            ver = 1
    model = str(row.get("model") or "").strip() or "metabase"
    trace = {"model": "dev:" + model, "version": ver,
             "source_id": str(row.get("id") or "")}
    return {"item_meta": item, "quality_meta": quality, "trace": trace}


def mq_register(data: dict, team=None) -> dict:
    """선택 행을 검수 대상으로 지정: 콘텐츠+발행 메타를 복사 인입(재추출 없음)."""
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        return {"ok": False, "error": "지정할 콘텐츠가 없습니다 · 조회 결과에서 선택하세요"}
    if len(rows) > _LIMIT_MAX:
        return {"ok": False, "error": "한 번에 %d건까지 지정할 수 있습니다" % _LIMIT_MAX}
    pairs, hashes, empty = [], [], 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        c = _row_content(r)
        if not (c["title"].strip() or c["body"].strip()):
            empty += 1
            continue
        out = _row_out(r, team)
        if not (out["item_meta"] or out["quality_meta"].get("finalGrade")):
            empty += 1                      # 발행 메타가 전혀 없으면 초안이 아니다
            continue
        pairs.append((c, out))
        hashes.append(_row_hash(r))
    if not pairs:
        return {"ok": False, "error": "지정 가능한 행이 없습니다 (제목·본문 또는 발행 메타 비어 있음)"}
    st = _SV.get_store()
    known = {}
    if st is not None and hasattr(st, "existing_hashes"):
        try:
            known = st.existing_hashes(hashes, team=team) or {}
        except Exception:
            known = {}
    fresh = [(p, h) for p, h in zip(pairs, hashes) if h not in known]
    added_hashes = [h for _, h in fresh]
    if fresh:
        saved = _SV.store_save([p for p, _ in fresh], source="메타베이스", team=team)
        if isinstance(saved, dict) and saved.get("error"):
            return {"ok": False, "error": "저장 실패 · 다시 시도하세요 (" + str(saved["error"])[:120] + ")"}
    if (data.get("purpose") or "") == "eval" and added_hashes:
        try:
            if st and hasattr(st, "set_purpose"):
                st.set_purpose(added_hashes, "eval", team=team)
        except Exception:
            pass
    return {"ok": True, "added": len(fresh), "existing": len(pairs) - len(fresh),
            "skipped_empty": empty, "hashes": added_hashes}
