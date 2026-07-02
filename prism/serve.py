"""로컬 UI (의존성 0, stdlib http.server).

  python3 -m prism.serve            # http://localhost:8765
  python3 -m prism.serve --port 9000 --mock

이미지/텍스트 입력 → (이미지면 imagext 어댑터로 Content 합성) → pipeline.extract →
리드문·엔티티·인텐트·카테고리 카드 + 원본 JSON. 키 없으면 자동 mock.
"""
from __future__ import annotations

import argparse
import json
import os
import queue as _queue
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import feedback_loop as FL
from . import imagext as IMG
from . import pipeline as PIPE
from . import prompts as PR
from .config import Config, DEFAULT_CONFIG_PATH
from .llm import LLMClient

# 마지막 실행 결과(리포트 생성용 · 즉시 응답 미러)
_LAST_RESULTS: list = []

# ── 로컬 영속 저장소(SQLite) · 추출 결과를 재시작해도 누적 보존 ──
_STORE = None
# PRISM_DB(컨테이너 볼륨 등) 우선, 없으면 기존 기본 위치(config 옆) · 기존 데이터 이동 방지.
_DB_PATH = os.environ.get("PRISM_DB") or os.path.join(os.path.dirname(DEFAULT_CONFIG_PATH), "prism.db")


def backend_mode():
    """('supabase'|'sqlite', required). 운영=Supabase 전용 원칙:
    · PRISM_BACKEND=supabase → supabase(필수, 미가용이면 시작 실패)
    · PRISM_BACKEND=sqlite   → sqlite(로컬·개발 강제)
    · 미설정 + Supabase 키 있음 → supabase 자동(운영)
    · 미설정 + 키 없음        → sqlite(로컬·오프라인)
    조용한 SQLite 폴백은 하지 않는다(운영 오설정을 숨기지 않으려고)."""
    from . import supastore
    b = (os.environ.get("PRISM_BACKEND") or "").strip().lower()
    if b == "sqlite":
        return "sqlite", False
    if b == "supabase":
        return "supabase", True
    if supastore.configured():
        return "supabase", True
    return "sqlite", False


def get_store():
    """Store 싱글턴(dual-mode). 운영은 Supabase 전용 · supabase 의도 시 SQLite 로 조용히
    폴백하지 않는다(초기화 실패면 비활성, main 이 시작을 막는다)."""
    global _STORE
    if _STORE is None:
        mode, _required = backend_mode()
        try:
            if mode == "supabase":
                from . import supastore
                _STORE = supastore.SupabaseStore()
            else:
                from .store import Store
                _STORE = Store(_DB_PATH)
        except Exception as e:
            print(f"  [ERROR] {mode} store 초기화 실패: {e}")
            _STORE = False
    return _STORE or None


def _run_id() -> str:
    return "run-" + str(int(time.time() * 1000))


def _build_id() -> str:
    """빌드 식별자(이 모듈 파일의 수정시각) · 설치본이 최신인지 확인용."""
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(__file__)))
    except Exception:
        return "?"


def store_save(pairs, source: str = "단건", team=None):
    """[(content, out), …] 를 영속 저장(+_LAST_RESULTS 미러). source: 출처. team: 소속 팀(supabase).
    적재 정책(dedup): 동일 콘텐츠 + 결과 무변경이면 적재 제외(skip), 변경 시 갱신, 신규는 추가."""
    outs = [o for _, o in pairs]
    _LAST_RESULTS[:] = outs
    _agg_bump()                          # 결과 변경 → 집계 캐시 무효화
    st = get_store()
    if st:
        try:
            return st.save_dedup(pairs, _run_id(), source=source, team=team)
        except Exception:
            pass
    return None


def results_rows(limit: int = 5000, team=None) -> list:
    """집계용 결과 행 · 영속 저장소 우선(누적), 없으면 메모리(_LAST_RESULTS)."""
    st = get_store()
    if st:
        try:
            rows = st.recent(limit, team=team)
            if rows:
                return rows
        except Exception:
            pass
    return _LAST_RESULTS


# ── 집계 캐시(B-4): 대시보드·아레나는 매 로드마다 최대 5000행 재스캔 → 짧은 TTL 메모 ──
# 쓰기(추출·인입·피드백·동기화) 시 _agg_bump() 로 무효화. ThreadingHTTPServer 다중스레드는
# GIL 하 dict 원자성으로 충분(중복 계산은 무해). TTL 은 안전망(무효화 누락 대비).
_AGG_CACHE = {}                      # key -> (expiry_ts, version, value)
_AGG_VERSION = 0
_AGG_TTL = 30.0


def _agg_bump():
    """집계 캐시 무효화(버전 증가). 결과·피드백이 바뀌는 모든 경로에서 호출."""
    global _AGG_VERSION
    _AGG_VERSION += 1


def _agg_cached(key, fn, ttl: float = _AGG_TTL):
    now = time.time()
    hit = _AGG_CACHE.get(key)
    if hit and hit[0] > now and hit[1] == _AGG_VERSION:
        return hit[2]
    val = fn()
    _AGG_CACHE[key] = (now + ttl, _AGG_VERSION, val)
    return val


# ── multipart/form-data 파서 (cgi 제거된 3.13+ 대응, stdlib만) ───────────────
def _parse_multipart(body: bytes, boundary: str) -> dict:
    """{name: value(str) | {"filename","mime","bytes"}} 형태로 반환."""
    fields = {}
    delim = b"--" + boundary.encode()
    for part in body.split(delim):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        raw_head, payload = part.split(b"\r\n\r\n", 1)
        head = raw_head.decode("utf-8", "replace")
        disp = next((l for l in head.split("\r\n")
                     if l.lower().startswith("content-disposition")), "")
        name = _kv(disp, "name")
        if name is None:
            continue
        filename = _kv(disp, "filename")
        if filename:
            mime = next((l.split(":", 1)[1].strip() for l in head.split("\r\n")
                         if l.lower().startswith("content-type")), "image/png")
            fields[name] = {"filename": filename, "mime": mime, "bytes": payload}
        else:
            fields[name] = payload.decode("utf-8", "replace")
    return fields


def _kv(disposition: str, key: str):
    token = f'{key}="'
    i = disposition.find(token)
    if i < 0:
        return None
    j = disposition.find('"', i + len(token))
    return disposition[i + len(token):j]


# ── 파이프라인 실행 ──────────────────────────────────────────────────────────
def run_pipeline(fields: dict, *, mock: bool, team=None, model: str = "") -> dict:
    cfg = Config.load()
    if (model or "").strip():                # 모델 지정 재실행: 제공자·키를 모델에 맞게 라우팅
        llm, _route = llm_for_model(model.strip(), mock)
        if llm is None:
            return {"error": f"모델 호출 불가({_route}): {model}"}
    else:
        llm = make_text_llm(cfg, mock)       # 텍스트 슬롯(solar|router). 무키면 내부서 mock

    # 업로드 순서(image0, image1, …) = 가중치 순서. 첫 장이 대표.
    imgs = [(k, v) for k, v in fields.items()
            if isinstance(v, dict) and v.get("bytes") and k.startswith("image")]
    imgs.sort(key=lambda kv: int(re.sub(r"\D", "", kv[0]) or 0))
    images = [v for _, v in imgs]
    source = "text"
    signals = []
    if images:
        source = "image"
        images, dropped = IMG.cap_images(images)   # 장수 상한(비전 호출 전)
        signals = IMG.extract_signals(images, mock=llm.mock)
        content = IMG.build_content(
            signals,
            displayServiceName=fields.get("displayServiceName", "포토"),
            title=fields.get("title", ""),
            caption=fields.get("caption", ""),
            dropped=dropped,
        )
    else:
        content = {
            "displayServiceName": fields.get("displayServiceName", ""),
            "title": fields.get("title", ""),
            "subtitle": fields.get("subtitle", ""),
            "body": fields.get("body", ""),
        }

    out = PIPE.extract(content, llm, legal=cfg.legal_enabled)
    try:                                         # 초안 버전 = 학습 반영 회차 + 1
        stv = get_store()
        (out.setdefault("trace", {}))["version"] = (stv.batch_seq(team) + 1) if (stv and hasattr(stv, "batch_seq")) else 1
    except Exception:
        pass
    store_save([(content, out)], team=team)      # 영속 저장(+미러, 팀 태깅)
    if (fields.get("purpose") or "") == "eval":  # 평가용 지정: 검수 대상에서 제외(홀드아웃)
        try:
            from .store import content_hash as _chash
            stp = get_store()
            if stp and hasattr(stp, "set_purpose"):
                stp.set_purpose([_chash(content)], "eval", team=team)
        except Exception:
            pass
    return {
        "source": source,
        "mock": llm.mock,
        "content": content,
        "signals": signals,
        "output": out,
    }


def rerun_all(model: str, team=None, limit: int = 200) -> dict:
    """모아진 콘텐츠 전체를 지정 모델로 일괄 실행(수동 · 관리자). 건당 비용 발생."""
    rows = results_rows(team=team)
    done = failed = 0
    seen = set()
    for r in rows[-int(limit):]:
        ref = r.get("content_ref") or {}
        ch = _row_key(ref)
        if ch in seen:
            continue
        seen.add(ch)
        res = rerun_content(ch, model, team=team)
        if res.get("error"):
            failed += 1
        else:
            done += 1
    return {"ok": True, "done": done, "failed": failed, "model": model}


def auto_rerun_after_batch(team=None):
    """학습 반영 종료 후 자동 재실행(옵션 · Config.auto_rerun_after_batch):
    각 콘텐츠를 기존 모델(미기록이면 기본 모델)로 새 버전 초안 재생성."""
    cfg = Config.load()
    rows = results_rows(team=team)
    done = 0
    seen = set()
    for r in rows:
        ref = r.get("content_ref") or {}
        ch = _row_key(ref)
        if ch in seen:
            continue
        seen.add(ch)
        m = (r.get("trace") or {}).get("model", "") or cfg.model
        if not rerun_content(ch, m, team=team).get("error"):
            done += 1
    print(f"  [batch] 자동 재실행 완료 {done}건(새 버전)")


def rerun_content(content_hash: str, model: str, team=None) -> dict:
    """같은 콘텐츠를 지정 모델로 재실행(초안 재생성 · 관리자). 기존 초안은 덮어쓰되
    이전 초안을 patch_log 에 남겨(rerun:구모델) 이력·비교 근거를 보존한다."""
    st = get_store()
    ch = (content_hash or "").strip()
    if not (st and ch):
        return {"error": "콘텐츠를 찾을 수 없습니다"}
    row = None
    fields = None
    for r in results_rows(team=team):
        ref = r.get("content_ref") or {}
        if _row_key(ref) == ch:
            row = r
            fields = {"displayServiceName": ref.get("displayServiceName", ""), "title": ref.get("title", ""),
                      "subtitle": ref.get("subtitle", ""), "body": ref.get("body", "")}
            break
    if not row:
        return {"error": "콘텐츠를 찾을 수 없습니다(본문 미보존 항목일 수 있음)"}
    old_model = (row.get("trace") or {}).get("model", "") or ""
    result = run_pipeline(fields, mock=Handler.server_mock, team=team, model=model)
    if result.get("error"):
        return result
    try:                                       # 산출이 동일해도 모델 표기가 갱신되도록 무조건 upsert
        st.save_many([(fields, result.get("output") or {})], "rerun", source="재실행", team=team)
    except Exception:
        pass
    if hasattr(st, "log_patch"):               # 이전 초안 보존(이력)
        try:
            st.log_patch(ch, "(재실행)", f"rerun:{old_model or '?'}->{model}",
                         {"item_meta": row.get("item_meta"), "quality_meta": row.get("quality_meta"),
                          "model": old_model},
                         {"model": model}, team=team)
        except Exception:
            pass
    _agg_bump()
    return result


def vocab() -> dict:
    """드롭다운용 어휘(콘텐츠 그룹 등). dictionaries/profiles 와 동기화."""
    from . import dictionaries as D
    return {"groups": list(D.SERVICE_GROUP.keys())}


def build_template_csv() -> bytes:
    """엑셀 일괄 입력용 CSV 템플릿(UTF-8 BOM → Excel 한글 정상). 헤더+예시 2행.

    헤더는 ingest 별칭과 일치: 콘텐츠 그룹·제목·부제·본문. 제목·본문이 필수.
    """
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["콘텐츠 그룹", "제목", "부제", "본문"])
    w.writerow(["뉴스", "삼성전자 노조 임금 협상 결렬",
                "중앙노동위 조정 불성립",
                "삼성전자가 중앙노동위원회 조정에서 노조와 합의에 이르지 못했다. 양측은 임금 인상폭을 두고 이견을 좁히지 못했다."])
    w.writerow(["스포츠", "손흥민 시즌 10호골",
                "",
                "토트넘이 홈 경기에서 승리했다. 손흥민이 후반 결승골을 터뜨리며 시즌 10호골을 기록했다."])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


# \u2500\u2500 \uc5b4\ub4dc\ubbfc \ubaa8\ub4c8 \ub370\uc774\ud130(\uc2e4\ub370\uc774\ud130 \uc5f0\uacb0) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
def dict_data() -> dict:
    """\uc0ac\uc804\u00b7\ub9e4\ud551 \ubaa8\ub4c8: \uc778\ud150\ud2b8\u00b7\ucf58\ud150\uce20 \uce74\ud14c\uace0\ub9ac\u00b7\ud488\uc9c8\u00b7\ubc95\ub839 \uc0ac\uc804\uc744 \uadf8\ub300\ub85c \ub178\ucd9c."""
    from . import dictionaries as D
    return {
        "serviceGroups": list(D.SERVICE_GROUP.keys()),
        "intentUniversal": list(D.INTENT_CATEGORIES_UNIVERSAL),
        "intentByService": {k: list(v) for k, v in D.INTENT_CATEGORIES_BY_SERVICE.items()},
        "iabTier1": list(D.IAB_TIER1),
        "tier2": {k: list(v) for k, v in getattr(D, "CONTENT_CATEGORY_TIER2", {}).items()},
        "iabMap": dict(getattr(D, "CATEGORY_IAB_MAP", {})),
        "domainGroups": {k: list(v) for k, v in getattr(D, "DOMAIN_GROUP_MAP", {}).items()},
        "qualityMetas": dict(D.QUALITY_METAS),
        "qualityNames": dict(getattr(D, "QUALITY_META_NAMES", {})),
        "qualityApplies": dict(getattr(D, "QUALITY_META_APPLIES", {})),
        "legalTypes": {c: {"label": v.get("label", c), "article": v.get("article", "")}
                       for c, v in D.LEGAL_HARM_TYPES.items()},
        "intakePolicy": {k: dict(v) for k, v in getattr(D, "INTAKE_POLICY", {}).items()},
    }


_DICT_OVERRIDES_PATH = os.path.join(os.path.dirname(DEFAULT_CONFIG_PATH), "dict_overrides.json")


def _read_overrides() -> dict:
    try:
        if os.path.exists(_DICT_OVERRIDES_PATH):
            with open(_DICT_OVERRIDES_PATH, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def load_dict_overrides():
    """저장된 사전 편집(overrides)을 dictionaries 에 적용(서버 시작 시)."""
    from . import dictionaries as D
    ov = _read_overrides()
    if ov:
        try:
            D.apply_profile(ov)
        except Exception:
            pass


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
        os.makedirs(os.path.dirname(_DICT_OVERRIDES_PATH) or ".", exist_ok=True)
        with open(_DICT_OVERRIDES_PATH, "w", encoding="utf-8") as f:
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
    """편집 초기화: overrides 삭제(베이스 사전은 다음 재시작 시 복원)."""
    try:
        os.remove(_DICT_OVERRIDES_PATH)
    except OSError:
        pass
    out = dict_data()
    out["resetNote"] = "초기화됨 · 베이스 사전은 서버 재시작 시 완전 복원"
    return out


def topics_data() -> dict:
    """\ud1a0\ud53d \ubaa8\ub4c8: \uc801\uc7ac\ub41c \uacb0\uacfc\uc5d0\uc11c \uc5d4\ud2f0\ud2f0\ud615\u00b7\uc0ac\uac74\ud615\u00b7\uc870\uac74\ud615 \ud1a0\ud53d \ube4c\ub4dc."""
    rows = results_rows()
    if not rows:
        return {"n_contents": 0, "single": [], "composite": [], "filter": [], "summary": {}}
    from . import topic as TP
    with tempfile.TemporaryDirectory() as d:
        rpath = os.path.join(d, "r.jsonl")
        with open(rpath, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        try:
            return TP.build_topics(rpath)
        except Exception as e:
            return {"error": str(e)[:200], "n_contents": len(rows),
                    "single": [], "composite": [], "filter": [], "summary": {}}


def dashboard_data(team=None) -> dict:
    """\ub300\uc2dc\ubcf4\ub4dc \ubaa8\ub4c8 \uc9d1\uacc4. team \ubcc4 \uc2a4\ucf54\ud551 \u00b7 \uc9e7\uc740 TTL \uce90\uc2dc(\ubc18\ubcf5 \ub85c\ub4dc \uc2dc 5000\ud589 \uc7ac\uc2a4\uce94 \ubc29\uc9c0)."""
    return _agg_cached(("dash", team), lambda: _dashboard_compute(team))


def _dashboard_compute(team=None) -> dict:
    """\uc801\uc7ac \uacb0\uacfc \uc9d1\uacc4(\uc720\ud1b5 G/R \u00b7 \uc778\ud150\ud2b8 \u00b7 \uce74\ud14c\uace0\ub9ac \u00b7 \ud488\uc9c8 \uc0ac\uc720) + \ucf58\ud150\uce20\ubcc4 \ud53c\ub4dc\ubc31."""
    rows = results_rows(team=team)
    n = len(rows)
    g = sum(1 for r in rows if (r.get("quality_meta") or {}).get("finalGrade") == "G")
    intent_c, cat_c, reason_c = {}, {}, {}
    lead_sum = lead_n = ent_total = 0
    for r in rows:
        im = r.get("item_meta") or {}
        for t in (im.get("intent") or []):
            intent_c[t] = intent_c.get(t, 0) + 1
        for v in (im.get("content_category") or []):
            top = (v or "").split("/")[0].strip()
            if top:
                cat_c[top] = cat_c.get(top, 0) + 1
        ent_total += len(im.get("entities") or [])
        s = im.get("summary") or ""
        if s:
            lead_sum += len(s); lead_n += 1
        for rs in ((r.get("quality_meta") or {}).get("reasons") or []):
            reason_c[rs] = reason_c.get(rs, 0) + 1

    def topk(dd, k=8):
        items = sorted(dd.items(), key=lambda x: -x[1])[:k]
        return [{"k": a, "v": b, "pct": round(b / n * 100) if n else 0} for a, b in items]

    # 콘텐츠별 행 + 평가 피드백(학습 루프) 부착
    contents, fb_stats = [], {"total": 0, "good": 0, "bad": 0, "learned": 0}
    try:
        st = get_store()
        if st:
            fmap = st.feedback_map(team=team)
            for row in st.recent_meta(team=team):
                row["fb"] = fmap.get(row["hash"], {})
                contents.append(row)
            fb_stats = st.feedback_stats(team=team)
    except Exception:
        pass

    return {
        "n": n, "g": g, "r": n - g, "gPct": round(g / n * 100) if n else 0,
        "entities": ent_total, "avgLead": round(lead_sum / lead_n) if lead_n else 0,
        "intents": topk(intent_c), "categories": topk(cat_c), "qualityReasons": topk(reason_c),
        "contents": contents, "feedback": fb_stats,
    }


def drill_contents(kind: str, value: str, team=None) -> dict:
    """대시보드 드릴다운: intent|category|reason = value 로 판정된 콘텐츠 목록."""
    rows = results_rows(team=team)
    out = []
    for r in rows:
        im = r.get("item_meta") or {}
        qm = r.get("quality_meta") or {}
        if kind == "intent":
            hit = value in (im.get("intent") or [])
        elif kind == "category":
            hit = any((v or "").split("/")[0].strip() == value or (v or "").strip() == value
                      for v in (im.get("content_category") or []))
        elif kind == "reason":
            hit = value in (qm.get("reasons") or [])
        else:
            hit = False
        if hit:
            out.append(_detail_row(r))
    return {"ok": True, "kind": kind, "value": value, "items": _attach_fb(out, team), "n": len(out)}


def topic_drill(cluster_id: str) -> dict:
    """토픽 드릴다운: 해당 토픽(클러스터)에 묶인 콘텐츠 목록. 배치 결과 드릴다운과 동일 shape."""
    rows = results_rows()
    if not rows or not cluster_id:
        return {"ok": True, "kind": "topic", "value": cluster_id or "", "items": [], "n": 0}
    td = topics_data()                        # single/composite/filter (각 content_ids 보유)
    cluster = None
    for grp in ("single", "composite", "filter"):
        for c in td.get(grp, []):
            if c.get("cluster_id") == cluster_id:
                cluster = c
                break
        if cluster:
            break
    if not cluster:
        return {"ok": False, "kind": "topic", "value": cluster_id, "items": [], "n": 0,
                "error": "토픽을 찾을 수 없습니다(데이터가 갱신되었을 수 있음)"}
    ids = cluster.get("content_ids") or []
    out = _attach_fb([_detail_row(rows[i]) for i in ids if 0 <= i < len(rows)])
    name = cluster.get("name") or cluster.get("label") or cluster_id
    return {"ok": True, "kind": "topic", "value": name, "items": out, "n": len(out)}


def _detail_row(r: dict) -> dict:
    """콘텐츠 상세/목록 공용 행: 렌더에 필요한 필드 표준화(공통 컴포넌트 입력)."""
    from .store import content_hash
    im = r.get("item_meta") or {}
    qm = r.get("quality_meta") or {}
    ref = r.get("content_ref") or {}
    svc = ref.get("displayServiceName", "") or r.get("service", "")
    title = ref.get("title", "") or r.get("title", "")
    sub = ref.get("subtitle", "")
    body = ref.get("body", "")
    # 피드백 키와 동일한 content_hash 사용(서비스+제목+부제+본문) → 목록 어디서나 검수상태 매칭
    chash = content_hash({"displayServiceName": svc, "title": title, "subtitle": sub, "body": body}) if title else (ref.get("body_hash", "") or r.get("hash", ""))
    return {
        "hash": chash,
        "title": ref.get("title", "") or r.get("title", ""),
        "subtitle": ref.get("subtitle", ""),
        "model": (r.get("trace") or {}).get("model", "") or r.get("model", "") or "",
        "service": ref.get("displayServiceName", "") or r.get("service", ""),
        "url": ref.get("source_url", "") or r.get("url", ""),
        "body": ref.get("body", ""),
        "summary": im.get("summary", ""),
        "entities": im.get("entities", []) or [],
        "intent": im.get("intent", []) or [],
        "category": im.get("content_category", []) or [],
        "grade": qm.get("finalGrade", "") or r.get("grade", ""),
        "reasons": qm.get("reasons", []) or [],
    }


def _attach_fb(items, team=None):
    """상세행 리스트에 검수 피드백 상태(fb: verdict·ts) 부착 → 콘텐츠 목록 어디서나 '검수 완료' 표기."""
    st = get_store()
    if not (st and hasattr(st, "feedback_map")):
        return items
    try:
        fmap = st.feedback_map(team=team)
    except Exception:
        return items
    for it in items:
        it["fb"] = fmap.get(it.get("hash"), {}) or {}
    return items


def _logs_to_jsonl(data: bytes, filename: str, out_path: str):
    """행동 로그(csv/tsv/jsonl) → jsonl 정규화. 컬럼: user_id·content_id·event·dwell_sec·scroll_pct·ts."""
    ext = os.path.splitext(filename or "")[1].lower()
    text = data.decode("utf-8-sig", "replace")
    rows = []
    if ext in (".csv", ".tsv"):
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
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def usermeta_data(logs_bytes: bytes = None, filename: str = "") -> dict:
    """사용자 메타 모듈: 행동 로그 업로드 시 실데이터로 소비 형태·강도·선호 산출,
    없으면 페르소나 정의·공식·시나리오(명세)만."""
    from . import usermeta as UM
    rows = results_rows()
    if not rows:
        return {"empty": True, "n_contents": 0, "users": [], "personas_def": [],
                "note": "먼저 [실행 · 추출]에서 콘텐츠를 추출하세요. content_id 는 추출 순서(0부터)와 매칭됩니다."}
    with tempfile.TemporaryDirectory() as d:
        rpath = os.path.join(d, "r.jsonl")
        with open(rpath, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        logs_path = None
        if logs_bytes:
            logs_path = os.path.join(d, "logs.jsonl")
            _logs_to_jsonl(logs_bytes, filename, logs_path)
        try:
            return UM.build_user_meta(rpath, logs_path=logs_path)
        except Exception as e:
            return {"error": str(e)[:200], "users": [], "personas_def": []}


def build_usermeta_template_csv() -> bytes:
    """행동 로그 템플릿. content_id = 추출 순서(0부터)."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["user_id", "content_id", "event", "dwell_sec", "scroll_pct", "ts"])
    w.writerow(["u1", "0", "click", "62", "80", "2026-06-23T21:10"])
    w.writerow(["u1", "2", "click", "48", "70", "2026-06-23T21:14"])
    w.writerow(["u2", "1", "impression", "8", "20", "2026-06-23T08:02"])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def run_batch(file_bytes: bytes, filename: str, purpose: str = "", team=None) -> dict:
    """엑셀/CSV 업로드 → ingest 매핑 → 행마다 추출 → 결과+리포트(_LAST_RESULTS)."""
    from . import ingest as ING
    ext = os.path.splitext(filename or "")[1].lower() or ".xlsx"
    cfg = Config.load()
    llm = make_text_llm(cfg, Handler.server_mock)
    fd, tmp = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file_bytes)
        a = ING.assess(tmp)
        if not a["ok"]:
            return {"error": a["reason"], "headers": a.get("headers", [])}
        contents = ING.to_contents(tmp)[:200]
        results, items, pairs = [], [], []
        for c in contents:
            out = PIPE.extract(c, llm, legal=cfg.legal_enabled)
            results.append(out)
            pairs.append((c, out))
            im = out.get("item_meta") or {}
            items.append({"title": (c.get("title") or "")[:80],
                          "summary": im.get("summary", ""),
                          "entities": im.get("entities", []),
                          "intent": im.get("intent", []),
                          "grade": (out.get("quality_meta") or {}).get("finalGrade", "")})
        store_save(pairs, source="배치", team=team)  # 영속 저장(단일 트랜잭션 배치)
        if (purpose or "") == "eval":               # 평가용 지정: 검수 대상에서 제외(홀드아웃)
            try:
                from .store import content_hash as _chash
                stp = get_store()
                if stp and hasattr(stp, "set_purpose"):
                    stp.set_purpose([_chash(c) for c, _ in pairs], "eval", team=team)
            except Exception:
                pass
        return {"source": "excel", "mock": llm.mock, "count": len(results),
                "mapping": a["mapping"], "items": items}
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


# ── 자동 인입: 작업 상태(진행률) + 백그라운드 폴링 스케줄러 ──
_INGEST_STATE = {}                         # {sid: {name,endpoint,running,total,done,last_run,last_msg,last_ok,trigger}}
_INGEST_LOCK = threading.Lock()
_INGEST_THREAD = None
_INGEST_STOP = threading.Event()


def _fetch_records(endpoint: str, limit: int, method: str, auth: str):
    """REST 엔드포인트에서 레코드 배열을 가져옴. (rows, error) 반환."""
    import urllib.request
    endpoint = (endpoint or "").strip()
    if not endpoint:
        return None, "엔드포인트가 비어 있습니다"
    url = endpoint
    if "limit=" not in url and (method or "GET").upper() == "GET":
        url += ("&" if "?" in url else "?") + "limit=" + str(int(limit))
    req = urllib.request.Request(url, method=(method or "GET").upper())
    if auth:
        req.add_header("Authorization", auth)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        return None, f"API 호출 실패: {str(e)[:160]}"
    if isinstance(data, dict):
        rows = next((data[k] for k in ("records", "data", "items", "results")
                     if isinstance(data.get(k), list)), None)
        rows = rows if rows is not None else [data]
    else:
        rows = data
    if not isinstance(rows, list) or not rows:
        return None, "레코드가 없습니다(빈 응답)"
    return rows, None


def ingest_run_source(source: dict, trigger: str = "manual") -> dict:
    """소스 1건 인입(진행률 추적). fetch → 매핑 → 건별 추출(진행 갱신) → dedup 적재."""
    from . import ingest as ING
    sid = source.get("id") or ("ep:" + (source.get("endpoint") or ""))
    limit = int(source.get("limit") or 100)
    with _INGEST_LOCK:
        if _INGEST_STATE.get(sid, {}).get("running"):
            return {"ok": False, "error": "이미 인입 중", "skipped_run": True}
        _INGEST_STATE[sid] = {"name": source.get("name") or "소스", "endpoint": source.get("endpoint", ""),
                              "running": True, "total": 0, "done": 0, "last_run": _INGEST_STATE.get(sid, {}).get("last_run", 0),
                              "last_msg": "수신 중…", "last_ok": None, "trigger": trigger}
    try:
        rows, err = _fetch_records(source.get("endpoint", ""), limit,
                                   source.get("method", "GET"), source.get("auth", ""))
        if err:
            _INGEST_STATE[sid].update(running=False, last_run=time.time(), last_ok=False, last_msg=err)
            return {"ok": False, "error": err}
        try:
            contents, m = ING.to_contents_rows(rows[:limit])
        except Exception as e:
            msg = str(e)[:200]
            _INGEST_STATE[sid].update(running=False, last_run=time.time(), last_ok=False, last_msg=msg)
            return {"ok": False, "error": msg, "headers": list(rows[0].keys()) if rows else []}
        _INGEST_STATE[sid].update(total=len(contents), done=0, last_msg="추출 중…")
        cfg = Config.load()
        llm = make_text_llm(cfg, Handler.server_mock)
        pairs = []
        for c in contents:
            try:
                pairs.append((c, PIPE.extract(c, llm, legal=cfg.legal_enabled)))
            except Exception:
                pass
            _INGEST_STATE[sid]["done"] += 1
        stats = {"inserted": 0, "updated": 0, "skipped": 0}
        st = get_store()
        if st and pairs:
            stats = st.save_dedup(pairs, "ingest-" + time.strftime("%Y%m%d-%H%M%S"), source="자동 인입")
        msg = f"{len(rows)}건 수신 → 신규 {stats['inserted']} · 갱신 {stats['updated']} · 제외 {stats['skipped']}"
        _INGEST_STATE[sid].update(running=False, last_run=time.time(), last_ok=True, last_msg=msg)
        return {"ok": True, "fetched": len(rows), "extracted": len(pairs),
                "mapping": m, "mock": llm.mock, **stats}
    except Exception as e:
        _INGEST_STATE[sid].update(running=False, last_run=time.time(), last_ok=False, last_msg=str(e)[:160])
        return {"ok": False, "error": str(e)[:160]}


def ingest_status() -> dict:
    """실행 큐/자동 인입 상태(진행률 포함) + 스케줄러 동작 여부."""
    jobs = []
    for sid, s in _INGEST_STATE.items():
        jobs.append({"id": sid, **s})
    return {"jobs": jobs, "scheduler": bool(_INGEST_THREAD and _INGEST_THREAD.is_alive()),
            "running": any(j["running"] for j in jobs)}


def _ingest_scheduler():
    """활성 API 소스를 interval 초마다 자동 폴링(백그라운드). 5분 등 가이드대로."""
    while not _INGEST_STOP.wait(timeout=10):
        try:
            cfg = Config.load()
            now = time.time()
            for s in (cfg.ingest_sources or []):
                if s.get("type") == "kafka" or not s.get("enabled"):
                    continue                                   # 중지/카프카는 자동 폴링 안 함
                sid = s.get("id") or ("ep:" + (s.get("endpoint") or ""))
                stt = _INGEST_STATE.get(sid, {})
                if stt.get("running"):
                    continue
                interval = max(15, int(s.get("interval") or 300))
                if now - stt.get("last_run", 0) >= interval:
                    ingest_run_source(s, trigger="auto")
        except Exception:
            pass


def start_ingest_scheduler():
    """백그라운드 자동 인입 스케줄러 시작(중복 방지)."""
    global _INGEST_THREAD
    if _INGEST_THREAD and _INGEST_THREAD.is_alive():
        return
    _INGEST_STOP.clear()
    _INGEST_THREAD = threading.Thread(target=_ingest_scheduler, name="prism-ingest", daemon=True)
    _INGEST_THREAD.start()


def build_results_csv() -> bytes:
    """적재된 추출 결과(콘텐츠 현황)를 CSV(엑셀)로 내보냄."""
    rows = results_rows()
    out = ["제목,서비스,리드문,엔티티,인텐트,콘텐츠 카테고리,등급,품질 사유"]
    def esc(v):
        s = str(v if v is not None else "")
        return '"' + s.replace('"', '""') + '"'
    for r in rows:
        im = r.get("item_meta") or {}
        qm = r.get("quality_meta") or {}
        c = r.get("content") or {}
        cat = " · ".join(im.get("content_category") or [])
        out.append(",".join(esc(x) for x in [
            c.get("title", ""), c.get("displayServiceName", ""), im.get("summary", ""),
            " · ".join(im.get("entities") or []), " · ".join(im.get("intent") or []),
            cat, qm.get("finalGrade", ""), " · ".join(qm.get("reasons") or []),
        ]))
    return ("﻿" + "\r\n".join(out)).encode("utf-8")


def build_report_html() -> str:
    rows = results_rows()
    if not rows:
        return "<p>아직 실행 결과가 없습니다. 먼저 추출을 실행하세요.</p>"
    from . import dashboard as DASH
    with tempfile.TemporaryDirectory() as d:
        rpath = os.path.join(d, "results.jsonl")
        with open(rpath, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        out = os.path.join(d, "report.html")
        try:
            DASH.build_integrated(rpath, out, title="Prism (이미지 트랙)")
            return open(out, encoding="utf-8").read()
        except Exception as e:
            return f"<p>리포트 생성 실패: {e}</p>"


# ── 설정(API 키 / 모델 / 엔드포인트) ─────────────────────────────────────────
_KEY_PATH = os.path.expanduser("~/.prism_key")              # Upstage Solar
# 라우터별 키 저장 경로(BizRouter · Timely). env 는 imagext.ROUTERS[*]['key_env'].
_ROUTER_KEY_PATHS = {
    "bizrouter": os.path.expanduser("~/.prism_bizrouter_key"),
    "timely": os.path.expanduser("~/.prism_timely_key"),
}


def load_persisted_key():
    """저장된 키가 있고 환경변수가 비어 있으면 프로세스 환경에 주입(서버 시작 시)."""
    if not IMG._api_key() and os.path.exists(_KEY_PATH):
        try:
            k = open(_KEY_PATH, encoding="utf-8").read().strip()
            if k:
                os.environ["UPSTAGE_API_KEY"] = k
        except Exception:
            pass
    for service, path in _ROUTER_KEY_PATHS.items():
        env = IMG.ROUTERS[service]["key_env"]
        if not IMG.router_key(service) and os.path.exists(path):
            try:
                k = open(path, encoding="utf-8").read().strip()
                if k:
                    os.environ[env] = k
            except Exception:
                pass


def make_text_llm(cfg: Config, mock: bool) -> LLMClient:
    """텍스트 슬롯 제공자에 맞춰 LLMClient 구성.
    solar=직접(Upstage), bizrouter/timely=통합 라우터(public id 모델 + 라우터 키)."""
    if IMG.is_router(cfg.text_provider) and IMG.router_key(cfg.text_provider) and cfg.text_model:
        cfg.chat_url = IMG.router_chat_url(cfg.text_provider)
        return LLMClient(mock=mock, config=cfg,
                         api_key=IMG.router_key(cfg.text_provider), model=cfg.text_model)
    return LLMClient(mock=mock, config=cfg)


# 모델 계열 → 라우터 public id 접두(bizrouter=provider/model 형식)
_MODEL_FAMILY_PREFIX = {"gpt": "openai", "o1": "openai", "o3": "openai", "o4": "openai",
                        "claude": "anthropic", "gemini": "google", "deepseek": "deepseek"}


def llm_for_model(model: str, mock: bool):
    """모델 id 로 제공자·엔드포인트·키를 해석해 전용 LLMClient 구성(다중 모델 실호출 라우팅).
    solar* = Upstage 직접, 그 외 = 키 보유 라우터(bizrouter=provider/model · timely=bare id).
    반환 (llm, route). 호출 불가(키 없음) 모델은 (None, 사유)."""
    cfg = Config.load()                                # 모델별 사본(공유 cfg 변형 방지)
    mid = (model or "").strip() or cfg.model
    if mock:
        return LLMClient(mock=True, config=cfg, model=mid), "mock"
    bare = mid.split("/")[-1]
    if bare.startswith("solar"):
        if not (cfg.api_key or IMG._api_key()):
            return None, "Upstage 키 없음"
        return LLMClient(config=cfg, model=bare), "solar"
    routers = ([cfg.text_provider] if IMG.is_router(cfg.text_provider) else []) + ["bizrouter", "timely"]
    for svc in dict.fromkeys(routers):                 # 설정 제공자 우선, 중복 제거
        if not IMG.router_key(svc):
            continue
        pid = bare if svc == "timely" else mid
        if svc == "bizrouter" and "/" not in pid:
            fam = next((v for k, v in _MODEL_FAMILY_PREFIX.items() if pid.startswith(k)), "")
            pid = (fam + "/" + pid) if fam else pid
        cfg.chat_url = IMG.router_chat_url(svc)
        return LLMClient(config=cfg, api_key=IMG.router_key(svc), model=pid), svc
    return None, "라우터 키 없음(BizRouter·Timely)"


def sync_prompt():
    """단계별 모델의 프롬프트(원천) + 학습 보정(피드백)을 추출 파이프라인에 반영.

    stage_models[stage] 로 단계 모델을 정하고, model_prompts[model][stage] 프롬프트를 우선 사용.
    없으면 기존 stage_prompts(전역 폴백) → 기본값.
    """
    try:
        cfg = Config.load()
        sp = dict(cfg.stage_prompts or {})        # 전역 폴백(하위호환)
        sm = dict(cfg.stage_models or {})          # {stage: model_id}
        mp = dict(cfg.model_prompts or {})         # {model_id: {stage: prompt}}
        PR.EXTRA_INSTRUCTION = cfg.system_prompt or ""

        def resolve(stage, legacy=""):
            model = (sm.get(stage) or "").strip()
            by_model = ((mp.get(model) or {}).get(stage) or "").strip() if model else ""
            return by_model or (sp.get(stage) or legacy or "").strip()

        PR.STAGE_DIRECTIVE = {
            "extract": resolve("extract"),
            "analyze": resolve("analyze", cfg.system_prompt or ""),
            "review": resolve("review"),
            "judge": resolve("judge"),
        }
        PR.EXTRA = PR.STAGE_DIRECTIVE          # 별칭 일관 유지
        sync_learned()
    except Exception:
        pass


def sync_learned():
    """배치 결과 피드백 → 단계별 학습 보정(LEARNED)으로 컴파일해 프롬프트에 자동 반영."""
    try:
        st = get_store()
        learned = st.learned_by_stage() if st else {}
        PR.LEARNED = {k: (learned.get(k) or "") for k in ("extract", "analyze", "review", "judge")}
    except Exception:
        PR.LEARNED = {"extract": "", "analyze": "", "review": "", "judge": ""}


def apply_feedback(data: dict) -> dict:
    """콘텐츠별 평가 피드백 저장 → 학습 루프 즉시 반영. {clear:true} 면 전체 초기화."""
    st = get_store()
    if not st:
        return {"ok": False, "error": "store unavailable"}
    if data.get("clear"):
        st.clear_feedback()
    else:
        ch = (data.get("hash") or "").strip()
        if not ch:
            return {"ok": False, "error": "hash required"}
        if ch.startswith("gold:"):                 # 골드 문항 응답 → gold_checks 로 분리(피드백 오염 방지)
            return apply_gold_answer(data)
        verdict = data.get("verdict") or ""        # good | bad | ""(취소)
        reviewer = (data.get("reviewer") or "").strip() or "(익명)"   # 귀속 키(uid 또는 이름)
        disp = (data.get("name") or "").strip() or reviewer          # 토스트 표시명
        note = (data.get("note") or "").strip()
        elements = [e for e in (data.get("elements") or []) if e in FL.ELEMENTS]
        if not elements and (data.get("element") or "").strip() in FL.ELEMENTS:
            elements = [(data.get("element") or "").strip()]         # 단일 요소 하위호환
        stage = data.get("stage") or (FL.ELEM_STAGE.get(elements[0]) if elements else "analyze") or "analyze"
        st.save_feedback(ch, data.get("service", ""), data.get("title", ""),
                         verdict, stage, note, time.time(), reviewer=reviewer,
                         team=data.get("_team"), element=",".join(elements))
        broadcast({"type": "feedback", "hash": ch, "reviewer": disp,
                   "verdict": verdict, "title": data.get("title", ""),
                   "service": data.get("service", ""), "ts": time.time()})
        if verdict == "bad" and note:              # 오케스트레이터: 원문 재분류(요소·단계 분기) + REAP 가공
            fb = {"stage": stage, "note": note, "title": data.get("title", ""),
                  "elements": elements, "_team": data.get("_team"),
                  "model": (data.get("model") or "").strip()}
            try:                                   # 요소 메타 맥락(재분류 정확도용)
                im = st.get_item_meta(ch) if hasattr(st, "get_item_meta") else None
                if im:
                    fb["output"] = {"item_meta": im}
            except Exception:
                pass
            threading.Thread(target=_reap_async, args=(ch, reviewer, fb),
                             daemon=True).start()
        missions = _check_missions(reviewer, data.get("_team"))
    # 프롬프트 반영은 '일배치 학습'에서 합의 후 1회(진동 방지). 여기선 수집만.
    _agg_bump()                                    # 피드백/진척율 변경 → 집계·아레나 캐시 무효화
    out = {"ok": True, "feedback": st.feedback_stats(),
           "learned": {k: bool(v) for k, v in (PR.LEARNED or {}).items()}}
    if not data.get("clear") and missions:
        out["missions_completed"] = missions       # 이번 행동으로 새로 달성된 미션(1회 보상)
    return out


def apply_gold_answer(data: dict) -> dict:
    """골드 문항(정답 알려진 검증 문항, Oleson 2011) 응답 처리.
    hash 형식 gold:<ok|bad>:<content_hash>. feedback 테이블은 건드리지 않는다."""
    st = get_store()
    parts = (data.get("hash") or "").split(":")
    if not (st and hasattr(st, "save_gold_check")) or len(parts) < 3:
        return {"ok": False, "error": "골드 문항 처리 불가"}
    verdict = data.get("verdict") or ""
    if not verdict:                                # 판정 취소 = 무기록
        return {"ok": True, "gold": None}
    expected = "good" if parts[1] == "ok" else "bad"
    reviewer = (data.get("reviewer") or "").strip() or "(익명)"
    correct = st.save_gold_check(parts[2], reviewer, expected, verdict, team=data.get("_team"))
    missions = _check_missions(reviewer, data.get("_team"))
    _agg_bump()
    out = {"ok": True, "gold": {"correct": bool(correct), "expected": expected}}
    if missions:
        out["missions_completed"] = missions
    return out


# ── 오늘의 미션(판정·보상 있는 형태) · 대상 = 불확실/불일치 콘텐츠(Lewis & Gale 1994) ──
MISSIONS = [
    {"id": "daily5", "label": "오늘의 검수", "total": 5, "bonus": 20},
    {"id": "gold1", "label": "골드 정답", "total": 1, "bonus": 15},
    {"id": "split1", "label": "불일치 재검토", "total": 1, "bonus": 15},
    {"id": "fill1", "label": "분류 채우기", "total": 1, "bonus": 10},
]


def mission_progress(reviewer, team=None) -> list:
    """검수자별 오늘의 미션 진행도. 판정은 저장된 행동 데이터로만(자가 신고 없음)."""
    st = get_store()
    if not (st and reviewer and hasattr(st, "feedback_today")):
        return []
    try:
        done = {"daily5": st.feedback_today(reviewer, team=team),
                "gold1": st.gold_today(reviewer, team=team).get("correct", 0),
                "split1": st.split_reviewed_today(reviewer, team=team),
                "fill1": (st.patches_today(reviewer, team=team) if hasattr(st, "patches_today") else 0)}
    except Exception:
        return []
    out = []
    for m in MISSIONS:
        d = min(done.get(m["id"], 0), m["total"])
        out.append({**m, "done": d, "completed": d >= m["total"]})
    return out


def _check_missions(reviewer, team=None) -> list:
    """달성 미션을 이벤트 로그에 1회 기록(중복 보상 방지) → 새로 달성된 미션 목록 반환."""
    st = get_store()
    if not (st and reviewer and hasattr(st, "log_event_once")):
        return []
    day = int(time.time() // 86400)
    fresh = []
    for m in mission_progress(reviewer, team):
        if not m["completed"]:
            continue
        try:
            if st.log_event_once(reviewer, "mission:" + m["id"], day, m["bonus"], team=team):
                fresh.append({"id": m["id"], "label": m["label"], "bonus": m["bonus"]})
        except Exception:
            pass
    return fresh


def reviewer_weights(team=None) -> dict:
    """검수자 신뢰도 가중치(골든 합의용): 골드 정확도 기반 0.5+0.5*acc(응답 5건 이상).
    데이터 없으면 빈 dict → 전원 1.0(기존 다수결과 동일). [Dawid-Skene 1979 근사 · Snow 2008]"""
    st = get_store()
    try:
        gold = st.gold_stats(team) if (st and hasattr(st, "gold_stats")) else {}
    except Exception:
        gold = {}
    return {rv: round(0.5 + 0.5 * g["acc"], 4) for rv, g in gold.items() if g.get("n", 0) >= 5}


def _reap_async(content_hash: str, reviewer: str, fb: dict):
    """피드백 후처리(비동기): ① 오케스트레이터가 교정 원문을 요소·단계별 개선 지시로 재분류(분기 저장)
    ② REAP(Remember→Explain→Ask→Plan) 가공 → plan 저장 → 브로드캐스트."""
    try:
        cfg = Config.load()
        llm = make_text_llm(cfg, Handler.server_mock)   # 서버 mock 존중(키 없으면도 mock)
        st = get_store()
        routes = FL.route_feedback(llm, fb)             # 요소 재분류(mock/실패 시 선택 요소 폴백)
        if st and routes and hasattr(st, "save_routes"):
            st.save_routes(content_hash, reviewer, routes, team=fb.get("_team"),
                           model=fb.get("model", ""))
        reap = FL.run_reap(llm, fb)
        if st:
            st.save_reap(content_hash, reviewer, reap)
        # 프롬프트 즉시반영 없음(일배치 학습에서 합의 반영). plan 은 저장·브로드캐스트만.
        broadcast({"type": "reap", "hash": content_hash, "reviewer": reviewer,
                   "stage": reap.get("stage", ""), "plan": reap.get("plan", ""),
                   "ask": reap.get("ask", ""),
                   "routed": [r["element"] for r in routes]})
    except Exception as e:
        print(f"  [warn] 피드백 후처리 실패(hash={content_hash[:12]}): {e}")


def reap_for(data: dict) -> dict:
    """콘텐츠의 검수자별 REAP 산출(UI 표시)."""
    st = get_store()
    if not st:
        return {"ok": False, "items": []}
    return {"ok": True, "items": st.get_reap((data.get("hash") or "").strip())}


# ── Supabase Auth(ID/PW) · 서버 프록시 + JWT 검증(supabase 모드) ──
_JWT_CACHE = {}
_JWT_LOCK = threading.Lock()


def _supa():
    """(url, service_key) 또는 None(=sqlite 모드)."""
    from . import supastore
    if os.environ.get("PRISM_BACKEND") == "supabase" and supastore.configured():
        return os.environ["SUPABASE_URL"].rstrip("/"), os.environ["SUPABASE_SERVICE_KEY"]
    return None


def _auth_post(url, path, key, body):
    req = urllib.request.Request(url + path, method="POST",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"apikey": key, "Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}


def auth_action(data: dict) -> dict:
    """로그인/가입 프록시(서버만 키 보유). mode=login|signup. 키는 프론트에 노출 안 함."""
    s = _supa()
    if not s:
        return {"ok": False, "error": "supabase 모드가 아닙니다"}
    url, key = s
    email = (data.get("email") or "").strip()
    pw = data.get("password") or ""
    if not email or not pw:
        return {"ok": False, "error": "이메일·비밀번호를 입력하세요"}
    try:
        if data.get("mode") == "signup":            # 내부 도구: 가입 즉시 확인(admin)
            try:
                _auth_post(url, "/auth/v1/admin/users", key,
                           {"email": email, "password": pw, "email_confirm": True})
            except urllib.error.HTTPError as e:
                if e.code not in (422, 409):        # 이미 존재 → 로그인으로 진행
                    raise
        tok = _auth_post(url, "/auth/v1/token?grant_type=password", key, {"email": email, "password": pw})
        at = tok.get("access_token")
        if not at:
            return {"ok": False, "error": "로그인 실패"}
        return {"ok": True, "access_token": at, "uid": (tok.get("user") or {}).get("id"), "email": email}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP{e.code}: {e.read().decode('utf-8', 'replace')[:160]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


def validate_jwt(token: str):
    """user JWT → uid(검증). 60s 캐시. 실패 시 None."""
    s = _supa()
    if not s or not token:
        return None
    now = time.time()
    with _JWT_LOCK:
        hit = _JWT_CACHE.get(token)
        if hit and hit[1] > now:
            return hit[0]
    url, key = s
    uid = None
    email = ""
    try:
        req = urllib.request.Request(url + "/auth/v1/user", method="GET",
                                     headers={"apikey": key, "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            u = json.loads(r.read().decode("utf-8"))
            uid = u.get("id")
            email = (u.get("email") or "").strip().lower()
    except Exception:
        uid = None
    if uid:
        with _JWT_LOCK:
            _JWT_CACHE[token] = (uid, now + 60)
            _JWT_EMAIL[token] = (email, now + 60)
    return uid


_JWT_EMAIL = {}                                  # token -> (email, expiry)


def jwt_email(token: str) -> str:
    """user JWT → email(소문자). validate_jwt 캐시 재사용. 관리자 허용목록 판정용."""
    if not token:
        return ""
    with _JWT_LOCK:
        hit = _JWT_EMAIL.get(token)
    if hit and hit[1] > time.time():
        return hit[0]
    validate_jwt(token)                          # 캐시 채우기(email 동반)
    with _JWT_LOCK:
        hit = _JWT_EMAIL.get(token)
    return hit[0] if hit else ""


def admin_emails() -> set:
    """관리자 허용목록(엄격 모드). env PRISM_ADMIN_EMAILS 또는 ~/.prism_admin_emails(콤마/개행 구분)."""
    raw = os.environ.get("PRISM_ADMIN_EMAILS", "")
    if not raw:
        try:
            p = os.path.expanduser("~/.prism_admin_emails")
            if os.path.exists(p):
                raw = open(p, encoding="utf-8").read()
        except Exception:
            raw = ""
    return {e.strip().lower() for e in raw.replace("\n", ",").split(",") if e.strip()}


def is_admin_user(uid, team, email="") -> bool:
    """관리자 판정. 허용목록이 설정돼 있으면 '그 이메일만'(엄격) · 팀 생성자·위임 로직 무시.
    허용목록 미설정 시 기존 로직(팀 생성자 OR is_admin 위임)."""
    allow = admin_emails()
    if allow:
        return bool(email and email.strip().lower() in allow)
    st = get_store()
    return bool(st and team and hasattr(st, "is_team_admin") and st.is_team_admin(uid, team))


def is_sys_admin_user(uid, team, email="") -> bool:
    """운영(시스템) 관리자: 허용목록(~/.prism_admin_emails) 이메일만.
    허용목록 미설정 시 기존 관리자 로직으로 폴백(단독 운영 호환).
    팀 관리자(생성자·위임)는 '팀 관리'만 담당하고 시스템 메뉴는 운영 관리자 전용."""
    allow = admin_emails()
    if allow:
        return bool(email and email.strip().lower() in allow)
    return is_admin_user(uid, team, email)


def register_reviewer(data: dict) -> dict:
    """검수자 등록: (인증 uid 또는 이름) + 표시명 + 캐릭터 (+ supabase 면 팀 생성/가입)."""
    st = get_store()
    if not st:
        return {"ok": False}
    rv = (data.get("reviewer") or "").strip()        # 키: 이름(sqlite) 또는 uid(supabase 주입)
    if not rv:
        return {"ok": False, "error": "검수자 식별 실패"}
    # 로그인: 기존 프로필(이름·캐릭터·팀) 로드 · 재입력/재등록 없음
    if data.get("mode") == "login" and hasattr(st, "get_reviewer"):
        prof = st.get_reviewer(rv)
        if not prof:
            return {"ok": False, "needSignup": True, "error": "가입이 필요합니다"}
        info = st.team_info(prof.get("team")) if (prof.get("team") and hasattr(st, "team_info")) else None
        return {"ok": True, "name": prof["name"], "char": prof["char"], "team": info,
                "badges": prof.get("badges") or []}       # 서버 배지 기준선(기기 간 중복 축하 방지)
    name = (data.get("name") or "").strip() or rv
    ch = (data.get("char") or "boksil").strip()
    team = None
    tmode = (data.get("team_mode") or "join")
    if _supa() and hasattr(st, "ensure_team"):
        if tmode == "none":                          # 팀 없이 가입(솔로) · 팀 생성은 관리자 메뉴
            st.set_reviewer(rv, name, ch, None)
        else:
            team = st.ensure_team(rv, tmode, data.get("team_name"), data.get("invite_code"))
            if not team:
                return {"ok": False, "error": "팀을 찾을 수 없습니다 · 초대코드를 확인하세요"}
            st.set_reviewer(rv, name, ch, team)
    else:
        st.set_reviewer(rv, name, ch)
    broadcast({"type": "reviewer", "reviewer": name, "char": ch})
    info = st.team_info(team) if (team and hasattr(st, "team_info")) else None
    return {"ok": True, "team": info}                # info.invite_code 로 초대코드 표시


def save_badges(uid, earned) -> dict:
    """획득 배지 라벨을 서버(prism_reviewers.badges)에 영속. 최신 전체 목록 반환.
    로컬(sqlite) 모드엔 영속 테이블이 없으므로 그대로 echo(클라 localStorage 폴백)."""
    st = get_store()
    labels = [str(x) for x in (earned or []) if x]
    if st and hasattr(st, "save_badges") and uid:
        try:
            return {"ok": True, "badges": st.save_badges(uid, labels)}
        except Exception as e:
            return {"ok": False, "error": str(e), "badges": labels}
    return {"ok": True, "badges": labels, "persisted": False}


_LAST_EVAL_DETAIL = []                             # 최근 골든 평가의 건별 불일치(라벨 오류 후보)


def _scope_golden(rows, scope, st, team=None):
    """평가 대상 콘텐츠 풀 필터: eval=평가용 홀드아웃만 / all=전체 정답셋."""
    if scope != "eval" or not rows:
        return rows
    from .store import content_hash
    try:
        pm = st.purpose_map(team) if hasattr(st, "purpose_map") else {}
    except Exception:
        pm = {}
    return [r for r in rows if pm.get(content_hash(r.get("content") or {}), "review") == "eval"]


def eval_golden(team=None, model: str = "", scope: str = "all") -> dict:
    """프로세스 1 · 관리자 등록 골든셋으로 원천 프롬프트 정합성 측정(기대 vs 실제). abtest 재사용.
    건별 불일치를 _LAST_EVAL_DETAIL 로 보존 → 라벨 오류 후보 플래깅(Northcutt 2021: 기계 플래그→휴먼 확정)."""
    from .store import content_hash
    st = get_store()
    if not (st and hasattr(st, "get_golden")):
        return {"ok": False, "error": "골든셋 평가는 Supabase 모드 전용입니다"}
    rows = st.get_golden(team)
    if not rows:
        return {"ok": False, "error": "등록된 골든셋이 없습니다 · 팀 관리에서 등록하세요"}
    rows = _scope_golden(rows, scope, st, team)
    if not rows:
        return {"ok": False, "error": "평가용으로 지정된 콘텐츠의 정답이 없습니다 · 콘텐츠 관리 STEP 1에서 용도를 지정하세요"}
    from . import abtest
    from . import harness as H
    cfg = Config.load()
    used_model = (model or "").strip()
    if used_model:                               # 기준 모델 지정: 제공자·키 라우팅
        llm, _route = llm_for_model(used_model, Handler.server_mock)
        if llm is None:
            return {"ok": False, "error": f"모델 호출 불가({_route}): {used_model}"}
    else:
        llm = make_text_llm(cfg, Handler.server_mock)
        used_model = cfg.model or getattr(llm, "model", "") or ""
    meth = H.Methodology(name="골든셋")
    sample = rows[:300]
    outs = abtest.run_methodology(sample, meth, llm, concurrency=8)
    m = abtest.score(sample, outs)
    m["methodology"] = meth.to_dict()
    global _LAST_EVAL_DETAIL
    detail = []
    for row, out in zip(sample, outs):
        if out is None:
            continue
        exp = (row.get("expected") or {}).get("finalGrade", "")
        got = (out.get("quality_meta") or {}).get("finalGrade", "")
        if exp and got and exp != got:
            c = row.get("content") or {}
            detail.append({"hash": content_hash(c), "title": (c.get("title") or "")[:60],
                           "expected": exp, "got": got})
    _LAST_EVAL_DETAIL = detail
    try:                                         # 건별 판정(집단 지성) 현황 부착
        jm = st.eval_check_counts(team) if hasattr(st, "eval_check_counts") else {}
    except Exception:
        jm = {}
    for d in detail:
        d["judge"] = jm.get(d["hash"]) or {"adopt": 0, "reject": 0, "reviewers": {}}
    m["detail"] = detail
    m["min_good"] = int(getattr(cfg, "golden_min_good", 1) or 1)
    from . import quality as Q
    lo, hi = Q.binomial_ci(m.get("grade_accuracy") or 0.0, len(sample))
    m["grade_ci"] = {"lo": lo, "hi": hi, "n": len(sample)}   # 95% CI(Miller 2024)
    m["ok"] = True
    m["evaluated"] = len(sample)
    try:                                         # 평가 기준(어떤 모델·버전으로 쟀는지) 명시
        seq = st.batch_seq(team) if hasattr(st, "batch_seq") else 0
    except Exception:
        seq = 0
    m["basis"] = {"model": used_model, "version": seq + 1, "scope": scope}
    return m


def register_golden(uid, team, rows, email="", merge=False) -> dict:
    """관리자가 팀 골든셋 등록. merge=True 면 기존에 병합(upsert), False 면 전체 교체.
    등록 전 검증·정규화: finalGrade G|R 강제, content_category 사전 스냅, title 필수."""
    from . import dictionaries as D
    st = get_store()
    if not is_admin_user(uid, team, email):
        return {"ok": False, "error": "관리자 전용입니다"}
    valid, skipped = [], 0
    for r in rows:
        if not (isinstance(r, dict) and isinstance(r.get("content"), dict) and isinstance(r.get("expected"), dict)):
            skipped += 1
            continue
        content, exp = r["content"], dict(r["expected"])
        grade = str(exp.get("finalGrade", "")).strip().upper()
        if not content.get("title") or grade not in ("G", "R"):
            skipped += 1
            continue
        exp["finalGrade"] = grade
        exp["content_category"] = D.normalize_category_list(exp.get("content_category") or [])
        exp["reasons"] = [str(x) for x in (exp.get("reasons") or []) if x]
        valid.append({"content": content, "expected": exp})
    n = st.register_golden(team, valid, replace=not merge, source="manual")
    _agg_bump()
    return {"ok": True, "count": n, "skipped": skipped, "merged": bool(merge)}


def golden_list(team=None) -> dict:
    """관리자 골든 브라우저: 목록 + 출처 집계 + 라벨 오류 의심(최근 평가 불일치) 표시."""
    st = get_store()
    if not (st and hasattr(st, "golden_rows")):
        return {"ok": False, "error": "지원하지 않는 저장소", "items": []}
    flagged = {f.get("hash") for f in (_LAST_EVAL_DETAIL or [])}
    items = st.golden_rows(team, limit=300)
    cmeta = {}
    try:                                       # 정답의 유래 초안(검수 당시 모델·버전) 부착
        for r in (st.recent_meta(1000, team=team) if hasattr(st, "recent_meta") else []):
            cmeta[r.get("hash")] = r
    except Exception:
        cmeta = {}
    try:
        jm = st.eval_check_counts(team) if hasattr(st, "eval_check_counts") else {}
    except Exception:
        jm = {}
    mg = int(getattr(Config.load(), "golden_min_good", 1) or 1)
    for it in items:
        it["flagged"] = it["hash"] in flagged
        m = cmeta.get(it["hash"]) or {}
        it["model"] = m.get("model", "") or ""
        it["version"] = m.get("version")
        j = jm.get(it["hash"]) or {}
        it["fix_needed"] = bool(j.get("adopt", 0) >= mg and j.get("adopt", 0) > j.get("reject", 0))
    return {"ok": True, "items": items,
            "source_counts": (st.golden_source_counts(team) if hasattr(st, "golden_source_counts") else {}),
            "total": (st.golden_count(team) if hasattr(st, "golden_count") else len(items))}


def build_golden_from_reviews(team=None) -> dict:
    """검수 = 골든 생성: '정확' 신뢰도 가중 다수결 + 카테고리 채워진 콘텐츠 → 골든셋에 **누적**(upsert).
    전체 교체가 아니므로 관리자 등록분(source=manual)과 과거 확정분을 보존하고, 합의가 '수정필요'로
    뒤집힌 검수 유래 골든은 강등(제거)한다. 신규 확정 기여 검수자에게 1회 보상(지연 보상 · von Ahn 2004).
    반환: 확정 총계·신규·강등·카테고리필요(목록 포함)·불일치."""
    from .store import content_hash
    st = get_store()
    if not (st and hasattr(st, "feedback_map") and hasattr(st, "upsert_golden")):
        return {"ok": False, "error": "지원하지 않는 저장소"}
    rows = results_rows(team=team)
    try:
        fmap = st.feedback_map(team=team)
    except Exception:
        fmap = {}
    weights = reviewer_weights(team)               # 골드 정확도 기반 신뢰도(G-4)
    min_good = max(1, int(getattr(Config.load(), "golden_min_good", 1) or 1))   # 확정 최소 '정확' 인원
    try:
        existing = st.golden_hashes(team)
        by_source = {r["hash"]: r["source"] for r in st.golden_rows(team, limit=10000)} if hasattr(st, "golden_rows") else {}
    except Exception:
        existing, by_source = set(), {}
    entries, need_list, no_cat, disagree = [], [], 0, 0
    demote = []                                    # 합의가 뒤집힌 검수 유래 골든(강등 대상)
    contributors = {}                              # hash → 정확 판정 검수자 키 목록(신규 확정 보상)
    for r in rows:
        ref = r.get("content_ref") or {}
        content = {"displayServiceName": ref.get("displayServiceName", ""), "title": ref.get("title", ""),
                   "subtitle": ref.get("subtitle", ""), "body": ref.get("body", "")}
        ch = content_hash(content)
        fb = fmap.get(ch)
        if not fb:
            continue
        # 신뢰도 가중 다수결: 골드 정확도 기반 가중치(없으면 전원 1.0 = 기존 다수결과 동일)
        gw = sum(weights.get(v.get("reviewer_id") or v.get("reviewer"), 1.0)
                 for v in fb.get("verdicts", []) if v.get("verdict") == "good")
        bw = sum(weights.get(v.get("reviewer_id") or v.get("reviewer"), 1.0)
                 for v in fb.get("verdicts", []) if v.get("verdict") == "bad")
        if not (fb.get("good", 0) >= min_good and gw > bw):   # 정확 최소 인원 + 가중 다수
            disagree += 1
            # 검수 유래 골든이 뒤집힘(가중 열세) → 강등. 관리자 등록분(manual)은 보존.
            if ch in existing and by_source.get(ch, "review") == "review" and bw > gw:
                demote.append(ch)
            continue
        im = r.get("item_meta") or {}
        qm = r.get("quality_meta") or {}
        cats = [c for c in (im.get("content_category") or []) if c and c != "Unclassified"]
        if not cats:                                  # 카테고리 공백 → 골든 미확정(채워야 함)
            no_cat += 1
            need_list.append({"hash": ch, "title": content.get("title", ""),
                              "service": content.get("displayServiceName", "")})
            continue
        entries.append({"hash": ch, "content": content, "expected": {
            "finalGrade": qm.get("finalGrade", ""), "reasons": qm.get("reasons", []) or [],
            "intent": im.get("intent", []) or [], "content_category": cats,
            "summary": im.get("summary", ""), "entities": im.get("entities", []) or []}})
        contributors[ch] = [v.get("reviewer_id") or v.get("reviewer")
                            for v in fb.get("verdicts", []) if v.get("verdict") == "good"]
    new = 0
    for e in entries:
        st.upsert_golden(e["hash"], e["content"], e["expected"], team=team, source="review")
        if e["hash"] not in existing:
            new += 1
            # 골든 기여 1회 보상(hash 당 · 검수자당 1번): 유능감 정보 제공(Ryan & Deci 2000)
            if hasattr(st, "log_event_once"):
                for rv in contributors.get(e["hash"], []):
                    try:
                        st.log_event_once(rv, "golden:" + e["hash"], 0, 10,
                                          meta=e["content"].get("title", "")[:60], team=team)
                    except Exception:
                        pass
    for ch in demote:
        try:
            st.remove_golden(ch, team=team)
        except Exception:
            pass
    total = st.golden_count(team) if hasattr(st, "golden_count") else len(entries)
    return {"ok": True, "confirmed": len(entries), "new": new, "demoted": len(demote),
            "total": total, "need_category": no_cat, "need_list": need_list[:50],
            "disagree": disagree, "min_good": min_good}


def patch_content_meta(content_hash, patch, team=None, reviewer="") -> dict:
    """검수자 구조화 교정(빈 카테고리 채우기 등) → 저장된 item_meta 패치. 골든 완성에 기여.
    교정 전/후를 patch_log 에 append(선호쌍 데이터 원천 · 다중 요소 교정 무손실)."""
    st = get_store()
    if not (st and hasattr(st, "update_item_meta")):
        return {"ok": False, "error": "지원하지 않는 저장소"}
    ch = (content_hash or "").strip()
    before = None
    if hasattr(st, "get_item_meta"):
        try:
            cur = st.get_item_meta(ch)
            if isinstance(cur, dict):
                before = {k: cur.get(k) for k in (patch or {})}   # 패치 대상 키의 이전 값만
        except Exception:
            before = None
    ok = st.update_item_meta(ch, patch or {})
    if ok and before is not None and hasattr(st, "log_patch"):
        element = "category" if "content_category" in (patch or {}) else ",".join(sorted(patch or {}))
        try:
            st.log_patch(ch, reviewer or "(익명)", element, before, patch or {}, team=team)
        except Exception:
            pass
    _agg_bump()
    return {"ok": bool(ok)}


_LAST_LEARN_REPORT = {}                               # 최근 일배치 결과(수신·표시용)


def compare_models_on_golden(models=None, team=None, scope: str = "all") -> dict:
    """골든셋(사람 확정 정답)을 여러 모델에 실호출로 돌려 정합성 비교 → 최적 모델 선택 근거.
    모델별 제공자·엔드포인트를 라우팅(llm_for_model)하고, 키 없는 모델은 건너뛰되 사유를 노출."""
    st = get_store()
    if not (st and hasattr(st, "get_golden")):
        return {"ok": False, "error": "골든셋을 지원하지 않는 저장소"}
    rows = st.get_golden(team)
    if not rows:
        return {"ok": False, "error": "골든셋이 비어 있습니다 · 검수로 '정확' 확정분을 쌓으세요"}
    rows = _scope_golden(rows, scope, st, team)
    if not rows:
        return {"ok": False, "error": "평가용으로 지정된 콘텐츠의 정답이 없습니다 · 콘텐츠 관리 STEP 1에서 용도를 지정하세요"}
    from . import abtest
    from . import harness as H
    cfg = Config.load()
    cand = list(dict.fromkeys(m for m in (models or []) if m)) or [cfg.model]
    out, skipped = [], []
    for model in cand:
        llm, route = llm_for_model(model, Handler.server_mock)
        if llm is None:
            skipped.append({"model": model, "reason": route})
            continue
        m = abtest.evaluate(rows[:200], H.Methodology(name=model), llm, concurrency=8)
        out.append({"model": model, "route": route, "real": (not llm.mock), "n": min(len(rows), 200),
                    "grade_accuracy": m.get("grade_accuracy"), "reason_jaccard": m.get("reason_jaccard"),
                    "reason_exact_match": m.get("reason_exact_match"), "empty_rate": m.get("empty_rate"),
                    "cost_usd": m.get("cost_usd"), "tokens": m.get("tokens")})
    if not out:
        return {"ok": False, "error": "호출 가능한 모델이 없습니다 · API 키(Upstage/라우터)를 확인하세요",
                "skipped": skipped, "golden_n": len(rows)}
    out.sort(key=lambda r: (-(r.get("grade_accuracy") or 0), -(r.get("reason_jaccard") or 0)))
    return {"ok": True, "models": out, "skipped": skipped,
            "best": out[0]["model"], "golden_n": len(rows)}


def learning_batch(team=None, models=None) -> dict:
    """일배치 학습(하루 1회): ① 피드백 병합→프롬프트 개선 ② 정확분 골든 축적 ③ 골든 회귀 평가(다중 모델)."""
    improve = meta_compile_run(team)
    golden = build_golden_from_reviews(team)
    evalr = eval_golden(team)                        # 현재 프롬프트 회귀 점수
    compare = compare_models_on_golden(models, team) if (models and len(models) > 1) else None
    try:                                        # 학습 반영 회차 기록 → 초안 버전(v = 회차+1)
        stv = get_store()
        if stv and hasattr(stv, "log_event_once"):
            stv.log_event_once("(system)", "learn_batch", int(time.time()), 0, team=team)
    except Exception:
        pass
    report = {"ok": True, "ts": time.time(), "improve": improve, "golden": golden,
              "eval": evalr, "compare": compare,
              "grade_accuracy": evalr.get("grade_accuracy") if evalr.get("ok") else None}
    global _LAST_LEARN_REPORT
    _LAST_LEARN_REPORT = report
    _agg_bump()
    if getattr(Config.load(), "auto_rerun_after_batch", False):   # 옵션: 개선 버전으로 자동 재실행
        threading.Thread(target=auto_rerun_after_batch, args=(team,), daemon=True).start()
    print(f"  [batch] 학습 일배치 · 골든 확정 {golden.get('confirmed')} · 카테고리필요 "
          f"{golden.get('need_category')} · 정합성(grade) {report['grade_accuracy']}")
    return report


def learn_data(team=None) -> dict:
    """학습 데이터 현황(관리자): 클래스 커버리지·일치도·검수자 신뢰도·라벨 오류 후보·추출 가능량·소요 대비.
    기준치는 논문 근거(LEARNING_DESIGN.md): SetFit 8/클래스 · LIMA 1k · Llama Guard 13.5k ·
    InstructGPT 33k · tinyBenchmarks 100/축 · CI 는 Miller 2024."""
    from . import quality as Q
    from . import dictionaries as D
    st = get_store()
    if not st:
        return {"ok": False, "error": "store unavailable"}
    golden = st.get_golden(team) if hasattr(st, "get_golden") else []
    golden_n = len(golden)
    # 클래스(Tier1) 커버리지 · 부트스트랩 하한 = 클래스당 8(SetFit)
    per_class, grade_dist = {}, {}
    for g in golden:
        exp = g.get("expected") or {}
        gr = exp.get("finalGrade") or "?"
        grade_dist[gr] = grade_dist.get(gr, 0) + 1
        t1s = {str(c).split("/")[0].strip() for c in (exp.get("content_category") or []) if c}
        for t1 in (t1s or {"(미분류)"}):
            per_class[t1] = per_class.get(t1, 0) + 1
    PER_CLASS_TARGET = 8                                # SetFit(Tunstall 2022) 클래스당 8예시
    coverage = [{"cls": t1, "have": per_class.get(t1, 0),
                 "lack": max(0, PER_CLASS_TARGET - per_class.get(t1, 0))} for t1 in D.IAB_TIER1]
    lack_total = sum(c["lack"] for c in coverage)
    # 일치도(참고 지표 · 임계값 기계 적용 금지: Artstein & Poesio 2008)
    try:
        fmap = st.feedback_map(team=team)
    except Exception:
        fmap = {}
    units = Q.feedback_units(fmap)
    alpha = Q.krippendorff_alpha_binary(units)
    agree = Q.percent_agreement(units)
    multi_units = sum(1 for u in units if len(u) >= 2)
    # 검수자 신뢰도: 합의 일치율(아레나) + 골드 정확도 + Dawid-Skene EM 오류율
    ds = Q.dawid_skene_binary(Q.feedback_labels(fmap))
    arena = st.arena_stats(team=team)
    reviewers = []
    for row in arena.get("leaderboard", []):
        rv = row["reviewer"]
        dsr = (ds.get("reviewers") or {}).get(rv) or {}
        reviewers.append({"reviewer": rv, "n": row.get("reviews", 0),
                          "agree_rate": row.get("agree_rate"),
                          "gold_n": row.get("gold_n", 0), "gold_acc": row.get("gold_acc"),
                          "ds_error": dsr.get("error_rate")})
    # 골든 정합성 ± 95% CI(최근 일배치 평가 기준, Miller 2024)
    ev = (_LAST_LEARN_REPORT or {}).get("eval") or {}
    acc_ci = None
    if ev.get("ok") and ev.get("n"):
        lo, hi = Q.binomial_ci(ev.get("grade_accuracy") or 0.0, int(ev["n"]))
        acc_ci = {"acc": ev.get("grade_accuracy"), "n": int(ev["n"]), "lo": lo, "hi": hi}
    # 추출 가능량
    patch_n = len(st.patch_rows(team=team)) if hasattr(st, "patch_rows") else 0
    fstats = st.feedback_stats(team=team)
    rationale_n = fstats.get("learned", 0)
    # 학습 소요 대비(전 기준치 논문 출처)
    requirements = [
        {"kind": "분류 부트스트랩(클래스당 8)", "target": PER_CLASS_TARGET * len(D.IAB_TIER1),
         "have": golden_n, "lack": lack_total, "basis": "SetFit · Tunstall et al. 2022 · arXiv:2209.11055"},
        {"kind": "SFT 정렬(고품질)", "target": 1000, "have": golden_n,
         "lack": max(0, 1000 - golden_n), "basis": "LIMA · Zhou et al. 2023 · arXiv:2305.11206"},
        {"kind": "운영급 분류 LLM", "target": 13500, "have": golden_n,
         "lack": max(0, 13500 - golden_n), "basis": "Llama Guard · Inan et al. 2023 · arXiv:2312.06674"},
        {"kind": "선호쌍(DPO · 참고 상한)", "target": 33000, "have": patch_n,
         "lack": max(0, 33000 - patch_n), "basis": "DPO · Rafailov et al. 2023 + InstructGPT RM 33k · Ouyang et al. 2022"},
        {"kind": "평가셋(큐레이션)", "target": 100, "have": golden_n,
         "lack": max(0, 100 - golden_n), "basis": "tinyBenchmarks · Maia Polo et al. 2024 + Miller 2024(CI 병기)"},
    ]
    # 불일치(원시 의견 보존 · Plank 2022) 목록
    split_list = []
    for ch, e in fmap.items():
        if e.get("consensus") == "split":
            split_list.append({"hash": ch, "n": e.get("n", 0), "good": e.get("good", 0),
                               "bad": e.get("bad", 0)})
    return {"ok": True, "golden_n": golden_n, "grade_dist": grade_dist,
            "coverage": coverage, "covered": sum(1 for c in coverage if c["lack"] == 0),
            "class_total": len(coverage), "per_class_target": PER_CLASS_TARGET,
            "alpha": alpha, "agreement": agree, "multi_units": multi_units,
            "reviewers": reviewers, "acc_ci": acc_ci,
            "label_flags": list(_LAST_EVAL_DETAIL),
            "split": split_list[:50], "split_n": len(split_list),
            "extractable": {"sft": golden_n, "dpo": patch_n, "rationale": rationale_n},
            "requirements": requirements}


def learn_spec_md(team=None) -> str:
    """파인튜닝 스펙·소요서(.md) 생성: 살아있는 검수·골든 수치를 근거로 한 요구사항 문서.
    이 도구의 최종 산출물(관리자 주입 → 검수 → 골든 → 스펙·소요) · 기준치는 전부 논문 출처."""
    d = learn_data(team)
    if not d.get("ok"):
        return "# 파인튜닝 소요서\n\n데이터가 없습니다."
    rep = _LAST_LEARN_REPORT or {}
    g = rep.get("golden") or {}
    L = []
    L.append("# 콘텐츠 분류·운영 특화 LLM · 파인튜닝 스펙 및 소요서")
    L.append("")
    L.append("자동 생성 문서 · 근거 수치는 팀 검수(HITL) 실데이터, 기준치는 검증 논문(LEARNING_DESIGN.md 서지).")
    L.append("")
    L.append("## 1. 목적·태스크 정의")
    L.append("- 입력: 콘텐츠(서비스명·제목·부제·본문)")
    L.append("- 출력: 리드문(summary) · 핵심 개체(entities) · 인텐트 · 콘텐츠 카테고리(IAB Tier1/2 사전) · 등급(G|R) · 품질 사유")
    L.append("- 분류 기준(taxonomy)은 프롬프트 외재화 방식 유지(재학습 없이 개편 가능 · Llama Guard, Inan et al. 2023)")
    L.append("")
    L.append("## 2. 학습데이터 현황 (검수 합의 기반)")
    L.append(f"- 정답셋(골든): **{d['golden_n']}건** (검수 유래 승격 + 관리자 등록 · 등급 분포 {d.get('grade_dist')})")
    L.append(f"- 클래스 충족: **{d['covered']}/{d['class_total']}** (목표 {d['per_class_target']}건/클래스 · SetFit, Tunstall et al. 2022)")
    aa = d.get("agreement")
    L.append(f"- 검수 일치도: 단순 일치율 {aa if aa is not None else '·'} · Krippendorff α {d.get('alpha') if d.get('alpha') is not None else '·'} "
             f"(참고 지표 · Artstein & Poesio 2008)")
    if d.get("acc_ci"):
        ci = d["acc_ci"]
        L.append(f"- 현행 버전 정답 일치율: **{round(ci['acc']*100,1)}%** (95% CI {round(ci['lo']*100,1)}~{round(ci['hi']*100,1)}%, n={ci['n']} · Miller 2024)")
    if g:
        L.append(f"- 최근 학습 반영: 확정 {g.get('confirmed')} · 신규 {g.get('new')} · 분류 필요 {g.get('need_category')} · 의견 갈림 {g.get('disagree')}")
    L.append(f"- 추출 가능 데이터: SFT {d['extractable']['sft']} · 선호쌍(DPO) {d['extractable']['dpo']} · 판단근거(rationale) {d['extractable']['rationale']}")
    L.append("")
    L.append("## 3. 소요(부족분) · 기준치 대비")
    L.append("")
    L.append("| 용도 | 기준 | 보유 | 부족 | 근거 |")
    L.append("|---|---|---|---|---|")
    for r in d.get("requirements", []):
        L.append(f"| {r['kind']} | {r['target']} | {r['have']} | {r['lack']} | {r['basis']} |")
    L.append("")
    L.append("### 클래스별 부족분 (검수 대상 주입 우선순위)")
    L.append("")
    L.append("| Tier1 | 보유 | 부족 |")
    L.append("|---|---|---|")
    for c in sorted(d.get("coverage", []), key=lambda x: -x["lack"]):
        if c["lack"] > 0:
            L.append(f"| {c['cls']} | {c['have']} | {c['lack']} |")
    L.append("")
    L.append("## 4. 권장 학습 스펙 (전 항목 논문 근거)")
    L.append("- 방법: 오픈 베이스 LLM + LoRA/QLoRA 어댑터 · 학습 자원 GPU 1장급 (Hu et al. 2021 · Dettmers et al. 2023, 48GB 1장으로 65B)")
    L.append("- 1단계 SFT: 소량·고품질 우선(LIMA, Zhou et al. 2023 · 1k) · rationale 멀티태스크 병행 시 데이터 소요 절감(Distilling Step-by-Step, Hsieh et al. 2023)")
    L.append("- 2단계 선호 정렬: 검수 교정 전/후 선호쌍으로 DPO(Rafailov et al. 2023 · RL 인프라 불필요)")
    L.append("- 운영급 목표: 라벨 ~13.5k 축적 시 7B급 분류 LLM 성립 전례(Llama Guard, Inan et al. 2023)")
    L.append("- 검수자 신뢰도 반영: 골드 문항 정확도 가중 합의(Oleson et al. 2011 · Snow et al. 2008) · Dawid-Skene EM 통계 병행(1979)")
    L.append("")
    L.append("## 5. 평가 계획")
    L.append(f"- 평가셋: 큐레이션 목표 100건/축(tinyBenchmarks, Maia Polo et al. 2024) · 현재 골든 {d['golden_n']}건")
    L.append("- 판정 규칙: 정확도에 95% CI 병기, 신뢰구간이 겹치는 비교는 판정 보류(Miller 2024)")
    L.append("- 모델 선정: 같은 정답셋으로 후보 모델 실호출 비교('평가' 페이지) 결과를 스펙에 첨부")
    L.append("")
    L.append("### 검수자 신뢰도(참고)")
    L.append("")
    L.append("| 검수자 | 검수 | 합의 일치 | 골드 정확도 | EM 오류율 |")
    L.append("|---|---|---|---|---|")
    for r in d.get("reviewers", []):
        L.append(f"| {r['reviewer']} | {r['n']} | {r.get('agree_rate') if r.get('agree_rate') is not None else '·'} "
                 f"| {r.get('gold_acc') if r.get('gold_n', 0) >= 5 else '·'} ({r.get('gold_n', 0)}) "
                 f"| {r.get('ds_error') if r.get('ds_error') is not None else '·'} |")
    L.append("")
    L.append("전체 서지·설계 근거: LEARNING_DESIGN.md")
    return "\n".join(L)


def _sft_system() -> str:
    """SFT 시스템 프롬프트: 분류 기준(taxonomy)을 지시문으로 외재화(Llama Guard 방식 →
    카테고리 개편 시 재학습 불필요)."""
    from . import dictionaries as D
    return ("주어진 콘텐츠의 메타를 JSON 객체 하나로만 출력하라. 필드: summary(리드문 1문장), "
            "entities(핵심 개체 1~3), intent(속성 분류 1~2), content_category(사전 값만: "
            + ", ".join(D.IAB_TIER1) + " 또는 'Tier1 / Tier2' 경로), finalGrade(G|R), reasons(문제 사유 목록).")


def learn_export(kind: str, team=None):
    """학습데이터 JSONL 추출. kind = sft(골든→지시학습) | dpo(교정 전/후→선호쌍) | rationale(판단근거).
    반환 (filename, ndjson_text) 또는 (None, error)."""
    st = get_store()
    if not st:
        return None, "store unavailable"
    lines = []
    if kind == "sft":                                  # LIMA · Llama Guard 방식
        sys_p = _sft_system()
        for g in (st.get_golden(team) if hasattr(st, "get_golden") else []):
            content, exp = g.get("content") or {}, g.get("expected") or {}
            if not content.get("title"):
                continue
            lines.append(json.dumps({"messages": [
                {"role": "system", "content": sys_p},
                {"role": "user", "content": json.dumps(content, ensure_ascii=False)},
                {"role": "assistant", "content": json.dumps(exp, ensure_ascii=False)}]},
                ensure_ascii=False))
        return "prism_sft.jsonl", "\n".join(lines)
    if kind == "dpo":                                  # DPO(Rafailov 2023) 선호쌍: 교정 전=rejected · 후=chosen
        cmap = st.contents_by_hash(team=team) if hasattr(st, "contents_by_hash") else {}
        for p in (st.patch_rows(team=team) if hasattr(st, "patch_rows") else []):
            if not p.get("before") and not p.get("after"):
                continue
            lines.append(json.dumps({
                "prompt": {"content": cmap.get(p["hash"]) or {"hash": p["hash"]},
                           "element": p.get("element", "")},
                "chosen": p.get("after") or {}, "rejected": p.get("before") or {},
                "reviewer": p.get("reviewer", ""), "ts": p.get("ts")},
                ensure_ascii=False))
        return "prism_dpo.jsonl", "\n".join(lines)
    if kind == "rationale":                            # Distilling Step-by-Step(Hsieh 2023): 라벨+판단근거
        cmap = st.contents_by_hash(team=team) if hasattr(st, "contents_by_hash") else {}
        try:
            fmap = st.feedback_map(team=team)
        except Exception:
            fmap = {}
        count = 0
        for ch, e in fmap.items():
            for v in e.get("verdicts", []):
                note = (v.get("note") or "").strip()
                if not note:
                    continue
                reaps = []
                if count < 300 and hasattr(st, "get_reap"):    # REAP 조회 상한(요청 비용 억제)
                    try:
                        reaps = st.get_reap(ch)
                    except Exception:
                        reaps = []
                rp = next((r for r in reaps if r.get("reviewer") == v.get("reviewer")), {})
                lines.append(json.dumps({
                    "content": cmap.get(ch) or {"hash": ch},
                    "label": {"verdict": v.get("verdict"), "stage": v.get("stage"),
                              "element": v.get("element", "")},
                    "rationale": note, "reap_explain": rp.get("explain") or "",
                    "reap_plan": rp.get("plan") or ""}, ensure_ascii=False))
                count += 1
        return "prism_rationale.jsonl", "\n".join(lines)
    return None, f"알 수 없는 종류: {kind}"


_learn_sched_started = False


def start_learning_scheduler(hour: int = 4):
    """매일 지정 시각(기본 04:00)에 learning_batch 실행. 서버당 1회(진동 방지·합의 반영)."""
    global _learn_sched_started
    if _learn_sched_started:
        return
    _learn_sched_started = True

    def _loop():
        import datetime
        while True:
            now = datetime.datetime.now()
            nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if nxt <= now:
                nxt += datetime.timedelta(days=1)
            time.sleep(max(60, (nxt - now).total_seconds()))
            try:
                learning_batch(None)
            except Exception as e:
                print(f"  [warn] 학습 일배치 실패: {e}")

    threading.Thread(target=_loop, daemon=True).start()


def meta_compile_run(team=None) -> dict:
    """메타컴파일러: 팀의 단계별 누적 검수 피드백을 병합·충돌정리 → 정제 지시. LEARNED 갱신.
    반환: {results:{stage:{directive,ambiguities}}} (불일치=가이드 명확화 신호 표면화)."""
    st = get_store()
    if not st:
        return {"ok": False, "error": "store unavailable"}
    cfg = Config.load()
    llm = make_text_llm(cfg, Handler.server_mock)
    raw = st.learned_by_stage(team=team)
    results = {}
    for stage, text in raw.items():
        results[stage] = FL.meta_compile(llm, stage, text)
    # 컴파일된 directive 를 단계 프롬프트(LEARNED)로 반영 · raw 누적 대체
    PR.LEARNED = {k: (results.get(k, {}).get("directive") or "") for k in ("extract", "analyze", "review", "judge")}
    return {"ok": True, "results": results}


def admin_data(uid, team, email="") -> dict:
    """팀 관리: 팀 정보·멤버·관리자 여부. supabase 전용."""
    st = get_store()
    if not (st and team and hasattr(st, "team_members")):
        return {"ok": False, "isAdmin": False, "team": None, "members": []}
    gc = st.golden_count(team) if hasattr(st, "golden_count") else 0
    return {"ok": True, "isAdmin": is_admin_user(uid, team, email),
            "isSysAdmin": is_sys_admin_user(uid, team, email),
            "team": st.team_info(team), "members": st.team_members(team), "goldenCount": gc}


def admin_ingest(uid, team, endpoint, n, email="") -> dict:
    """관리자: 크롤러 엔드포인트에서 N건 당겨와 추출 → 전건 검토 대상으로 팀 큐 적재(배치).
    실시간 스트리밍 부담 없이 관리자가 수량 목표로 트리거."""
    st = get_store()
    if not is_admin_user(uid, team, email):
        return {"ok": False, "error": "관리자 전용입니다"}
    n = max(1, min(int(n or 20), 200))             # 수량 상한(응답성)
    from . import ingest as ING
    rows, err = _fetch_records((endpoint or "").strip(), n, "GET", "")
    if err:
        return {"ok": False, "error": err}
    try:
        contents, _m = ING.to_contents_rows(rows[:n])
    except Exception as e:
        return {"ok": False, "error": f"형식 매핑 실패: {str(e)[:140]}"}
    cfg = Config.load()
    llm = make_text_llm(cfg, Handler.server_mock)
    pairs = []
    for c in contents:
        out = PIPE.extract(c, llm, legal=cfg.legal_enabled)
        out.setdefault("quality_meta", {})["review"] = "yellow"   # 인입 배치는 전건 검토 대상
        pairs.append((c, out))
    st.sync_contents(pairs, source="자동 인입", team=team)
    return {"ok": True, "fetched": len(contents), "queued": len(pairs)}


def admin_action(uid, team, data, email="") -> dict:
    """관리자 액션(데이터 삭제·멤버 제거). 팀 생성자만."""
    st = get_store()
    if _supa() and not is_admin_user(uid, team, email):    # 로컬 단독 실행 = 관리자 취급(타 라우트와 동일)
        return {"ok": False, "error": "관리자 전용입니다"}
    act = data.get("action")
    data_ops = ("clear_feedback", "clear_contents", "clear_golden", "delete_team")
    if act in data_ops and _supa() and not is_sys_admin_user(uid, team, email):
        return {"ok": False, "error": "데이터 관리는 운영 관리자 전용입니다"}
    if act == "clear_feedback":
        st.clear_team_feedback(team)
    elif act == "clear_contents":
        st.clear_team_contents(team)
    elif act == "clear_golden":                    # 정답셋 전체 삭제(되돌릴 수 없음)
        st.register_golden(team, [], replace=True, source="manual")
    elif act == "delete_team":                     # 팀 삭제: 멤버 소속 해제 + 팀 행 삭제
        if not hasattr(st, "delete_team"):
            return {"ok": False, "error": "이 백엔드는 팀 삭제를 지원하지 않습니다"}
        st.delete_team(team)
    elif act == "remove_member" and data.get("member"):
        st.remove_member(team, data["member"])
    elif act in ("set_admin", "unset_admin") and data.get("member"):
        t = st.team_info(team) if hasattr(st, "team_info") else None
        if t and data["member"] == t.get("created_by"):
            return {"ok": False, "error": "생성자의 관리자 권한은 변경할 수 없습니다"}
        if not hasattr(st, "set_member_admin"):
            return {"ok": False, "error": "이 백엔드는 위임을 지원하지 않습니다"}
        st.set_member_admin(team, data["member"], act == "set_admin")
    elif act == "ingest":                          # 크롤러 수량 인입 → 검토 큐
        return admin_ingest(uid, team, data.get("endpoint"), data.get("n"), email)
    elif act == "create_team" and hasattr(st, "ensure_team"):   # 관리자: 새 팀 생성(초대코드 발급)
        nm = (data.get("name") or "").strip()
        if not nm:
            return {"ok": False, "error": "팀 이름을 입력하세요"}
        tid = st.ensure_team(uid, "create", nm)
        if not tid:
            return {"ok": False, "error": "팀 생성 실패"}
        prof = st.get_reviewer(uid) if hasattr(st, "get_reviewer") else None
        st.set_reviewer(uid, (prof or {}).get("name") or uid, (prof or {}).get("char", "boksil"), tid)
        info = st.team_info(tid) if hasattr(st, "team_info") else None
        return {"ok": True, "team": info, "invite": (info or {}).get("invite_code")}
    else:
        return {"ok": False, "error": "알 수 없는 액션"}
    return {"ok": True}


def arena_data(team=None) -> dict:
    """평가 아레나(게임화) 데이터: 팀 정확도 + 리더보드 + 검수 대기(퀘스트). team 별 스코핑 · TTL 캐시."""
    return _agg_cached(("arena", team), lambda: _arena_compute(team), ttl=15.0)


def _arena_compute(team=None) -> dict:
    st = get_store()
    if not st:
        return {"accuracy": 0, "good": 0, "bad": 0, "reviews": 0, "week_reviews": 0,
                "accuracy_delta": 0, "target": 0.9, "leaderboard": [], "queue": 0}
    d = st.arena_stats(team=team)
    try:
        d["queue"] = len(st.review_queue(team=team))  # 미검수 YELLOW = 남은 퀘스트
    except Exception:
        d["queue"] = 0
    return d


def _row_key(ref: dict) -> str:
    """검수 키(content_hash) 정합: supabase recent 는 body_hash 에 스토어 키(16자)를 담고,
    sqlite payload 의 body_hash 는 본문 해시(12자) → 16자면 그대로, 아니면 재계산."""
    from .store import content_hash
    bh = ref.get("body_hash") or ""
    if isinstance(bh, str) and len(bh) == 16:
        return bh
    return content_hash({"displayServiceName": ref.get("displayServiceName", ""), "title": ref.get("title", ""),
                         "subtitle": ref.get("subtitle", ""), "body": ref.get("body", "")})


def raw_rows(limit: int = 100, team=None, reviewer: str = "") -> dict:
    """검수 대상 콘텐츠: 판정 결과 전체를 한 표로(모델·버전·필터 · 빠른 검수).
    검수 대기(YELLOW)·불일치도 포함되며, 검수자 식별 시 골드 문항을 섞는다."""
    rows = results_rows(team=team)
    st = get_store()
    try:
        fmap = st.feedback_map(team=team) if st else {}
    except Exception:
        fmap = {}
    try:                                           # 평가용 홀드아웃은 검수 대상에서 제외(학습 오염 방지)
        pmap = st.purpose_map(team=team) if (st and hasattr(st, "purpose_map")) else {}
    except Exception:
        pmap = {}
    out = []
    for r in reversed(rows[-int(limit):]):         # 최근순
        ref = r.get("content_ref") or {}
        im = r.get("item_meta") or {}
        qm = r.get("quality_meta") or {}
        tr = r.get("trace") or {}
        ch = _row_key(ref)
        if pmap.get(ch) == "eval":
            continue
        fb = fmap.get(ch) or {}
        last_ts = 0
        for v in (fb.get("verdicts") or []):
            try:
                last_ts = max(last_ts, float(v.get("ts") or 0))
            except Exception:
                pass
        out.append({"hash": ch,
                    "service": ref.get("displayServiceName", ""), "title": ref.get("title", ""),
                    "body": ref.get("body", ""), "url": ref.get("source_url", ""),
                    "grade": qm.get("finalGrade", ""), "reasons": qm.get("reasons", []) or [],
                    "category": im.get("content_category", []) or [],
                    "summary": im.get("summary", ""), "entities": im.get("entities", []) or [],
                    "intent": im.get("intent", []) or [],
                    "model": tr.get("model", "") or "",
                    "version": int(tr.get("version") or 1),
                    "review": qm.get("review", "") or "",
                    "split": bool(fb.get("good") and fb.get("bad")),
                    "fb": {"verdict": fb.get("consensus") or fb.get("verdict") or "",
                           "n": fb.get("n", 0), "ts": last_ts},
                    "item_meta": im, "quality_meta": qm})
    # 골드 문항(정답 알려진 검증 문항) 삽입: 큐와 동일 규칙, 표 형태로 어댑트
    if reviewer:
        gold_items = _inject_gold([], reviewer, team)
        for g in gold_items:
            out.insert(0, {"hash": g["hash"], "service": g.get("service", ""), "title": g.get("title", ""),
                           "body": g.get("body", ""), "url": "",
                           "grade": g.get("grade", ""), "reasons": g.get("reasons", []) or [],
                           "category": g.get("category", []) or [],
                           "summary": g.get("summary", ""), "entities": g.get("entities", []) or [],
                           "intent": g.get("intent", []) or [],
                           "model": "", "version": None, "review": "yellow", "split": False,
                           "fb": {"verdict": "", "n": 0, "ts": 0},
                           "item_meta": {"summary": g.get("summary", ""), "entities": g.get("entities", []),
                                         "intent": g.get("intent", []), "content_category": g.get("category", [])},
                           "quality_meta": {"finalGrade": g.get("grade", ""), "reasons": g.get("reasons", [])}})
    return {"ok": True, "items": out, "n": len(out)}


def model_stats(team=None) -> dict:
    """결과 비교 · 요소 단위 모델별 현황: 모델별로 유통 G%·처리 건수·평균 리드문·
    인텐트/카테고리/품질 사유 상위를 집계(같은 정보요소를 모델 축으로 비교)."""
    rows = results_rows(team=team)
    by = {}
    for r in rows:
        tr = r.get("trace") or {}
        m = tr.get("model", "") or "(모델 미기록)"
        ver = int(tr.get("version") or 1)
        g = by.setdefault((m, ver), {"n": 0, "g": 0, "lead": 0, "lead_n": 0,
                                     "intents": {}, "cats": {}, "reasons": {}})
        qm = r.get("quality_meta") or {}
        im = r.get("item_meta") or {}
        g["n"] += 1
        if qm.get("finalGrade") == "G":
            g["g"] += 1
        sm = im.get("summary") or ""
        if sm:
            g["lead"] += len(sm); g["lead_n"] += 1
        for t in (im.get("intent") or []):
            g["intents"][t] = g["intents"].get(t, 0) + 1
        for c in (im.get("content_category") or []):
            top = (c or "").split("/")[0].strip()
            if top:
                g["cats"][top] = g["cats"].get(top, 0) + 1
        for rs in (qm.get("reasons") or []):
            g["reasons"][rs] = g["reasons"].get(rs, 0) + 1

    def topk(d, k=3):
        return [f"{a} ({b})" for a, b in sorted(d.items(), key=lambda x: -x[1])[:k]]
    out = []
    for (m, ver), g in sorted(by.items(), key=lambda x: (x[0][0], -x[0][1])):
        out.append({"model": m, "version": ver, "key": f"{m} · v{ver}",
                    "n": g["n"], "gPct": round(g["g"] / g["n"] * 100) if g["n"] else 0,
                    "avgLead": round(g["lead"] / g["lead_n"]) if g["lead_n"] else 0,
                    "intents": topk(g["intents"]), "categories": topk(g["cats"]),
                    "reasons": topk(g["reasons"])})
    return {"ok": True, "models": out}


def drafts_for(content_hash: str, team=None) -> dict:
    """결과 비교용 초안 스냅샷: 현재 초안 + 재실행 이력(patch_log rerun) 의 이전 초안."""
    st = get_store()
    ch = (content_hash or "").strip()
    cur = None
    for r in results_rows(team=team):
        if _row_key(r.get("content_ref") or {}) == ch:
            tr = r.get("trace") or {}
            cur = {"label": f"{tr.get('model') or '모델 미기록'} · v{int(tr.get('version') or 1)} (현재)",
                   "model": tr.get("model", ""), "version": int(tr.get("version") or 1),
                   "item_meta": r.get("item_meta") or {}, "quality_meta": r.get("quality_meta") or {}}
            break
    outs = []
    if cur:
        outs.append(cur)
    if st and hasattr(st, "patch_rows"):
        for p in st.patch_rows(limit=5000, team=team):
            if p.get("hash") != ch or not str(p.get("element", "")).startswith("rerun:"):
                continue
            bf = p.get("before") or {}
            outs.append({"label": f"{bf.get('model') or '모델 미기록'} · 이전({p.get('element','')[6:]})",
                         "model": bf.get("model", ""), "version": None,
                         "item_meta": bf.get("item_meta") or {}, "quality_meta": bf.get("quality_meta") or {}})
    return {"ok": True, "items": outs, "n": len(outs)}


def review_queue(data: dict) -> dict:
    """검수 대기 큐(YELLOW). only_unreviewed=false 면 검수된 것도 포함.
    검수자 식별 시 골드 문항(정답 알려진 검증 문항)을 큐에 몰래 섞는다."""
    st = get_store()
    if not st:
        return {"ok": False, "error": "store unavailable", "items": []}
    only_un = data.get("only_unreviewed", True)
    limit = int(data.get("limit") or 100)
    items = st.review_queue(limit=limit, only_unreviewed=bool(only_un), team=data.get("team"))
    items = _inject_gold(items, (data.get("reviewer") or "").strip(), data.get("team"))
    return {"ok": True, "items": items, "n": len(items)}


def _inject_gold(items: list, reviewer: str, team=None) -> list:
    """골든셋에서 골드 문항을 생성해 큐에 삽입(블라인드). [Oleson 2011 · Kittur 2008]
    변형: hash 짝수 = 원본 그대로(정답 good) / 홀수 = 등급 뒤집기(정답 bad).
    선택·위치는 (검수자, 일자) 시드로 결정적(폴링 때마다 재배치 방지). 응답한 문항은 재출제 안 함."""
    st = get_store()
    if not (reviewer and st and hasattr(st, "get_golden") and hasattr(st, "gold_answered")):
        return items
    try:
        golden = st.get_golden(team)
        answered = st.gold_answered(reviewer, team=team)
    except Exception:
        return items
    from .store import content_hash as _chash
    cands = []
    for g in golden:
        content, exp = g.get("content") or {}, g.get("expected") or {}
        h = _chash(content)
        if h in answered or not content.get("title"):
            continue
        cands.append((h, content, exp))
    if not cands:
        return items
    import hashlib as _hl
    import random as _rd
    day = int(time.time() // 86400)
    rng = _rd.Random(int(_hl.sha1(f"{reviewer}:{day}".encode()).hexdigest()[:8], 16))
    rng.shuffle(cands)
    k = min(len(cands), max(1, len(items) // 10))
    out = list(items)
    for h, content, exp in cands[:k]:
        flip = int(h, 16) % 2 == 1                  # 홀수 = 등급 뒤집기(정답 bad)
        grade = exp.get("finalGrade", "") or "G"
        item = {"hash": f"gold:{'bad' if flip else 'ok'}:{h}",
                "service": content.get("displayServiceName", ""), "title": content.get("title", ""),
                "body": content.get("body", ""), "summary": exp.get("summary", ""),
                "entities": exp.get("entities", []) or [], "intent": exp.get("intent", []) or [],
                "category": exp.get("content_category", []) or [],
                "grade": ("R" if grade == "G" else "G") if flip else grade,
                "reasons": exp.get("reasons", []) or [], "review_reason": "",
                "reviewed": False, "split": False, "confidence": None, "ts": None}
        out.insert(rng.randint(0, len(out)), item)
    return out


# ── 검수 엔드포인트 레이트리밋(스팸 클릭 억제) ──
_RL_HITS = {}
_RL_LOCK = threading.Lock()


def rate_limited(key: str, min_interval: float = 0.8, per_min: int = 40) -> bool:
    """key(검수자/IP)별 최소 간격·분당 상한. 초과 시 True(=429)."""
    now = time.time()
    with _RL_LOCK:
        q = _RL_HITS.setdefault(key, [])
        while q and now - q[0] > 60:
            q.pop(0)
        if (q and (now - q[-1]) < min_interval) or len(q) >= per_min:
            return True
        q.append(now)
        return False


# ── 실시간 협업(SSE): 검수 이벤트를 접속 중인 팀원에게 브로드캐스트 ──
_subscribers = []                      # list[queue.Queue]
_sub_lock = threading.Lock()


def _sse_subscribe():
    q = _queue.Queue(maxsize=128)
    with _sub_lock:
        _subscribers.append(q)
    return q


def _sse_unsubscribe(q):
    with _sub_lock:
        if q in _subscribers:
            _subscribers.remove(q)


def broadcast(event: dict):
    """접속 중 모든 SSE 구독자에게 이벤트 푸시(논블로킹, 큐 가득 차면 드롭)."""
    with _sub_lock:
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait(event)
        except _queue.Full:
            pass


def _candidate_models(cfg) -> list:
    """프롬프트 스튜디오 모델 선택지: 설정된 모델 + 저장된 모델 키 + 흔한 기본값(오프라인 대비)."""
    seen, out = set(), []
    pool = [cfg.text_model, cfg.vision_model,
            *list((cfg.stage_models or {}).values()),
            *list((cfg.model_prompts or {}).keys())]
    for m in pool + ["solar-pro2", "gpt-5.4", "claude-opus-4-8", "gemini-2.5-pro"]:
        m = (m or "").strip()
        if m and m not in seen:
            seen.add(m); out.append(m)
    return out


def config_status() -> dict:
    cfg = Config.load()
    base = (cfg.chat_url or "").rsplit("/chat/completions", 1)[0]
    return {
        "hasKey": bool(IMG._api_key()),
        "persisted": os.path.exists(_KEY_PATH),
        "model": cfg.model or "",
        "baseUrl": base,
        "reasoning": cfg.reasoning_effort or "default",
        "systemPrompt": cfg.system_prompt or "",
        "stagePrompts": dict(cfg.stage_prompts or {}),
        "stagePromptsMeta": dict(cfg.stage_prompts_meta or {}),
        "stageModels": dict(cfg.stage_models or {}),
        "modelPrompts": dict(cfg.model_prompts or {}),
        "availableModels": _candidate_models(cfg),
        "ingestSources": list(cfg.ingest_sources or []),
        "storedCount": (get_store().count() if get_store() else 0),
        "build": _build_id(),
        "configured": cfg.is_configured(),
        "forcedMock": Handler.server_mock,
        "backend": "supabase" if _supa() else "sqlite",
        "authRequired": bool(_supa()),                 # supabase 모드 → ID/PW 로그인 필요
        "keyManagedByServer": bool(_supa()),           # 운영: 키는 서버 관리(UI 키 입력 숨김)
        # 모델 슬롯
        "hasBizKey": bool(IMG.router_key("bizrouter")),
        "bizPersisted": os.path.exists(_ROUTER_KEY_PATHS["bizrouter"]),
        "hasTimelyKey": bool(IMG.router_key("timely")),
        "timelyPersisted": os.path.exists(_ROUTER_KEY_PATHS["timely"]),
        "textProvider": cfg.text_provider or "solar",
        "textModel": cfg.text_model or "",
        "visionProvider": cfg.vision_provider or "upstage_ie",
        "visionModel": cfg.vision_model or "",
        "legalEnabled": bool(cfg.legal_enabled),
    }


def apply_config(data: dict) -> dict:
    """키/모델/엔드포인트/추론강도/추가지시 적용. 키만 프로세스 환경(+옵션 ~/.prism_key).
    운영(supabase·공유 서버): 키는 서버 env 전용 → 브라우저가 보낸 키 변경은 무시(서버 키 보호)."""
    if backend_mode()[0] == "supabase":
        data = {k: v for k, v in data.items()
                if k not in ("api_key", "persist", "forget", "bizrouter_api_key", "timely_api_key")}
    key = (data.get("api_key") or "").strip()
    if key:
        os.environ["UPSTAGE_API_KEY"] = key
        if data.get("persist"):
            try:
                with open(_KEY_PATH, "w", encoding="utf-8") as f:
                    f.write(key)
                os.chmod(_KEY_PATH, 0o600)
            except Exception:
                pass
    elif data.get("forget"):                      # 저장된 키 삭제
        os.environ.pop("UPSTAGE_API_KEY", None)
        try:
            os.remove(_KEY_PATH)
        except OSError:
            pass
    # 라우터 키(BizRouter · Timely, 서비스별 별도 저장)
    for service, path in _ROUTER_KEY_PATHS.items():
        env = IMG.ROUTERS[service]["key_env"]
        rkey = (data.get(service + "_api_key") or "").strip()
        if rkey:
            os.environ[env] = rkey
            if data.get("persist"):
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(rkey)
                    os.chmod(path, 0o600)
                except Exception:
                    pass
        elif data.get("forget_" + service):
            os.environ.pop(env, None)
            try:
                os.remove(path)
            except OSError:
                pass
    model = (data.get("model") or "").strip()
    base = (data.get("base_url") or "").strip()
    reasoning = (data.get("reasoning") or "").strip()
    has_sp = "system_prompt" in data
    has_stage = "stage_prompts" in data and isinstance(data.get("stage_prompts"), dict)
    slot_keys = ("text_provider", "text_model", "vision_provider", "vision_model")
    has_slot = any(k in data for k in slot_keys)
    has_legal = "legal_enabled" in data
    has_ingest = "ingest_sources" in data and isinstance(data.get("ingest_sources"), list)
    has_smodels = "stage_models" in data and isinstance(data.get("stage_models"), dict)
    has_mprompts = "model_prompts" in data and isinstance(data.get("model_prompts"), dict)
    if model or base or reasoning or has_sp or has_stage or has_slot or has_legal or has_ingest or has_smodels or has_mprompts:
        cfg = Config.load()
        if has_ingest:
            cfg.ingest_sources = data.get("ingest_sources") or []
        if has_smodels:
            sm = data.get("stage_models") or {}
            cfg.stage_models = {k: (sm.get(k) or "").strip() for k in ("extract", "analyze", "review", "judge")}
        if has_mprompts:
            # {model_id: {stage: prompt}} 병합(빈 모델/빈 프롬프트는 정리)
            mp = dict(cfg.model_prompts or {})
            for mid, stages in (data.get("model_prompts") or {}).items():
                mid = (mid or "").strip()
                if not mid or not isinstance(stages, dict):
                    continue
                cur = dict(mp.get(mid) or {})
                for st, txt in stages.items():
                    if st in ("extract", "analyze", "review", "judge"):
                        cur[st] = (txt or "").strip()
                mp[mid] = cur
            cfg.model_prompts = mp
            meta = dict(cfg.stage_prompts_meta or {})
            stamp = time.strftime("%Y-%m-%d %H:%M")
            edited = data.get("stage")
            for k in ([edited] if edited else ("extract", "analyze", "review", "judge")):
                if k:
                    meta[k] = stamp
            cfg.stage_prompts_meta = meta
        if base:
            cfg.set_base_url(base)
        if model:
            cfg.model = model
        if reasoning:
            cfg.reasoning_effort = reasoning
        if has_sp:
            cfg.system_prompt = (data.get("system_prompt") or "").strip()
        if has_stage:
            sp = data.get("stage_prompts") or {}
            cfg.stage_prompts = {k: (sp.get(k) or "").strip() for k in ("extract", "analyze", "review", "judge")}
            # 하위호환: analyze → system_prompt 동기화
            cfg.system_prompt = cfg.stage_prompts.get("analyze", "") or cfg.system_prompt
            # 최종 수정 이력 기록(단일 단계 저장 시 그 단계만, 전체 저장 시 모두)
            meta = dict(cfg.stage_prompts_meta or {})
            stamp = time.strftime("%Y-%m-%d %H:%M")
            edited = data.get("stage")
            for k in ([edited] if edited else ("extract", "analyze", "review", "judge")):
                if k:
                    meta[k] = stamp
            cfg.stage_prompts_meta = meta
        if has_legal:
            cfg.legal_enabled = bool(data.get("legal_enabled"))
        for k in slot_keys:
            if k in data:
                setattr(cfg, k, (data.get(k) or "").strip())
        try:
            cfg.save_template()                   # config.json 갱신(키는 저장 안 함)
        except Exception:
            pass
    sync_prompt()
    return config_status()


def list_models() -> dict:
    """현재 키로 접근 가능한 모델 목록을 Upstage /models 에서 조회."""
    if not IMG._api_key():
        return {"ok": False, "detail": "API 키가 설정되지 않았습니다", "models": []}
    cfg = Config.load()
    url = cfg.models_url or ((cfg.chat_url or "").rsplit("/chat/completions", 1)[0] + "/models")
    if not url or not url.endswith("/models"):
        return {"ok": False, "detail": "models 엔드포인트 미설정", "models": []}
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {IMG._api_key()}")
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        data = payload.get("data") or payload.get("models") or []
        ids = sorted({(m.get("id") or m.get("name") or "") for m in data
                      if isinstance(m, dict)} - {""})
        return {"ok": True, "models": ids, "current": cfg.model or ""}
    except urllib.error.HTTPError as e:
        return {"ok": False, "detail": f"HTTP{e.code}", "models": []}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200], "models": []}


def ping_model() -> dict:
    """현재 키/설정으로 실제 1회 호출하여 연결 검증."""
    if not IMG._api_key():
        return {"ok": False, "detail": "API 키가 설정되지 않았습니다"}
    try:
        cfg = Config.load()
        if not cfg.is_configured():
            return {"ok": False, "detail": "엔드포인트·모델 미설정 (config)"}
        llm = LLMClient(config=cfg)
        if llm.mock:
            return {"ok": False, "detail": "키 인식 실패 (mock 모드로 동작)"}
        obj, res = llm.complete_json("JSON 객체 하나만 출력한다.",
                                     '{"ok": true} 형태로만 답하라.', tag="ping")
        if res.fail_kind or "_fail" in obj:
            return {"ok": False, "detail": (obj.get("_fail") or res.fail_kind or "호출 실패")[:200]}
        return {"ok": True, "detail": f"{cfg.model} 응답 정상", "latency_ms": res.latency_ms}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


_JSON = "application/json; charset=utf-8"


# ── HTTP 핸들러 ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_mock = False

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if "text/html" in ctype:        # WKWebView 가 옛 페이지를 캐시하지 않도록
            self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/report"):
            self._send(200, build_report_html())
        elif self.path.startswith("/export.csv"):
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=prism_results.csv")
            self.end_headers()
            self.wfile.write(build_results_csv())
        elif self.path.startswith("/config"):
            self._send(200, json.dumps(config_status(), ensure_ascii=False), _JSON)
        elif self.path.startswith("/models"):
            self._send(200, json.dumps(list_models(), ensure_ascii=False), _JSON)
        elif self.path.startswith("/vocab"):
            self._send(200, json.dumps(vocab(), ensure_ascii=False), _JSON)
        elif self.path.startswith("/dict"):
            self._send(200, json.dumps(dict_data(), ensure_ascii=False), _JSON)
        elif self.path.startswith("/topic-drill"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            self._send(200, json.dumps(topic_drill(q.get("cluster", [""])[0]),
                                       ensure_ascii=False), _JSON)
        elif self.path.startswith("/topics"):
            self._send(200, json.dumps(topics_data(), ensure_ascii=False), _JSON)
        elif self.path.startswith("/dashboard"):
            self._send(200, json.dumps(dashboard_data(self._req_team()), ensure_ascii=False), _JSON)
        elif self.path.startswith("/drill"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            self._send(200, json.dumps(drill_contents(q.get("kind", [""])[0], q.get("value", [""])[0],
                                                       self._req_team()), ensure_ascii=False), _JSON)
        elif self.path.startswith("/arena"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            d = dict(arena_data(self._req_team()))
            rv = self._bearer_uid() or q.get("reviewer", [""])[0]
            if rv:
                d["missions"] = mission_progress(rv, self._req_team())
            self._send(200, json.dumps(d, ensure_ascii=False), _JSON)
        elif self.path.startswith("/admin"):
            self._send(200, json.dumps(admin_data(self._bearer_uid(), self._req_team(), self._bearer_email()),
                                       ensure_ascii=False), _JSON)
        elif self.path.startswith("/queue"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            data = {"only_unreviewed": q.get("all", ["0"])[0] not in ("1", "true"),
                    "limit": (q.get("limit", ["100"])[0]), "team": self._req_team(),
                    "reviewer": self._bearer_uid() or q.get("reviewer", [""])[0]}
            self._send(200, json.dumps(review_queue(data), ensure_ascii=False), _JSON)
        elif self.path.startswith("/raw"):               # 검수 대상 콘텐츠(모델·버전 필터 표)
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            self._send(200, json.dumps(raw_rows(int(q.get("limit", ["100"])[0]), self._req_team(),
                                       reviewer=(self._bearer_uid() or q.get("reviewer", [""])[0])),
                                       ensure_ascii=False), _JSON)
        elif self.path.startswith("/model-stats"):       # 결과 비교: 요소 단위 모델별 현황
            self._send(200, json.dumps(model_stats(self._req_team()), ensure_ascii=False), _JSON)
        elif self.path.startswith("/drafts"):            # 결과 비교: 콘텐츠별 초안 스냅샷
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            self._send(200, json.dumps(drafts_for(q.get("hash", [""])[0], self._req_team()),
                                       ensure_ascii=False), _JSON)
        elif self.path.startswith("/events"):
            self._serve_sse()
        elif self.path.startswith("/reap"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            self._send(200, json.dumps(reap_for({"hash": q.get("hash", [""])[0]}),
                                       ensure_ascii=False), _JSON)
        elif self.path.startswith("/ingest-status"):
            self._send(200, json.dumps(ingest_status(), ensure_ascii=False), _JSON)
        elif self.path.startswith("/learn-report"):     # 최근 일배치 결과(GET 수신)
            self._send(200, json.dumps({"ok": True, "report": _LAST_LEARN_REPORT}, ensure_ascii=False), _JSON)
        elif self.path.startswith("/learn-export"):      # 학습데이터 JSONL 다운로드(관리자)
            if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                return
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            fname, text = learn_export(q.get("kind", ["sft"])[0], self._req_team())
            if not fname:
                self._send(400, json.dumps({"error": text}, ensure_ascii=False), _JSON)
                return
            data = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path.startswith("/learn-spec"):        # 파인튜닝 스펙·소요서(.md · 관리자)
            if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                return
            data = learn_spec_md(self._req_team()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="prism_finetune_spec.md"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path.startswith("/learn-data"):        # 학습 데이터 현황(관리자)
            if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                return
            self._send(200, json.dumps(learn_data(self._req_team()), ensure_ascii=False), _JSON)
        elif self.path.startswith("/golden-list"):       # 관리자 골든 브라우저
            if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                return
            self._send(200, json.dumps(golden_list(self._req_team()), ensure_ascii=False), _JSON)
        elif self.path.startswith("/golden-status"):     # 골든 생성 현황(팀원 공개): 확정·분류필요·불일치
            st = get_store()
            g = (_LAST_LEARN_REPORT or {}).get("golden") or {}
            self._send(200, json.dumps({
                "ok": True,
                "batch_seq": (st.batch_seq(self._req_team()) if (st and hasattr(st, "batch_seq")) else 0),
                "total": (st.golden_count(self._req_team()) if (st and hasattr(st, "golden_count")) else 0),
                "source_counts": (st.golden_source_counts(self._req_team())
                                  if (st and hasattr(st, "golden_source_counts")) else {}),
                "last_batch": {k: g.get(k) for k in ("confirmed", "new", "demoted", "need_category",
                                                     "disagree", "min_good")},
                "need_list": g.get("need_list") or [],
                "ts": (_LAST_LEARN_REPORT or {}).get("ts")}, ensure_ascii=False), _JSON)
        elif self.path.startswith("/prompt-defaults"):
            sync_learned()
            self._send(200, json.dumps({"defaults": PR.stage_defaults(),
                "learned": {k: bool((PR.LEARNED or {}).get(k)) for k in ("extract", "analyze", "review", "judge")}},
                ensure_ascii=False), _JSON)
        elif self.path.startswith("/usermeta-template.csv"):
            data = build_usermeta_template_csv()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="prism_behavior_log.csv"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path.startswith("/usermeta"):
            self._send(200, json.dumps(usermeta_data(), ensure_ascii=False), _JSON)
        elif self.path.startswith("/template.csv"):
            data = build_template_csv()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="prism_template.csv"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path.startswith("/vendor/"):
            self._send_vendor(self.path.split("?", 1)[0].rsplit("/", 1)[-1])
        else:
            self._send(200, PAGE)

    def _bearer_uid(self):
        auth = self.headers.get("Authorization", "")
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        return validate_jwt(token)

    def _bearer_email(self):
        auth = self.headers.get("Authorization", "")
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        return jwt_email(token)

    def _req_team(self):
        """supabase 모드: Bearer uid → 그 사용자의 team_id(요청별 팀 스코핑). 아니면 None."""
        if not _supa():
            return None
        uid = self._bearer_uid()
        st = get_store()
        return (st.reviewer_team(uid) if (uid and st and hasattr(st, "reviewer_team")) else None)

    def _inject_reviewer(self, data):
        """supabase 모드: Bearer JWT 검증 → data['reviewer']=uid(사칭 불가) + data['_team']=팀.
        sqlite 모드: True(클라이언트 이름 그대로). 인증 실패 시 False(=401)."""
        if not _supa():
            return True
        uid = self._bearer_uid()
        if not uid:
            return False
        data["reviewer"] = uid
        st = get_store()
        data["_team"] = (st.reviewer_team(uid) if (st and hasattr(st, "reviewer_team")) else None)
        return True

    def _serve_sse(self):
        """SSE 스트림: 검수 이벤트를 실시간 푸시. ThreadingHTTPServer 라 블로킹 OK."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")     # 프록시 버퍼링 방지
        self.end_headers()
        q = _sse_subscribe()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                except _queue.Empty:
                    self.wfile.write(b": ping\n\n")           # 하트비트(연결 유지·끊김 감지)
                    self.wfile.flush()
                    continue
                self.wfile.write(("data: " + json.dumps(ev, ensure_ascii=False) + "\n\n").encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass                                              # 클라이언트 종료 → 정리
        finally:
            _sse_unsubscribe(q)

    _VENDOR_CT = {
        ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".woff2": "font/woff2",
        ".woff": "font/woff",
        ".svg": "image/svg+xml",
        ".png": "image/png",
    }

    def _send_vendor(self, name):
        safe = os.path.basename(name)
        ext = os.path.splitext(safe)[1].lower()
        path = os.path.join(os.path.dirname(__file__), "vendor", safe)
        if ext not in self._VENDOR_CT or not os.path.isfile(path):
            self._send(404, "not found")
            return
        with open(path, "rb") as f:
            self._send(200, f.read(), self._VENDOR_CT[ext])

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if self.path.startswith("/config"):
            try:
                # 운영(supabase): 팀 공유 설정(모델·프롬프트·인입 등)은 관리자만 변경
                if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                    self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                    return
                self._send(200, json.dumps(apply_config(json.loads(body or b"{}")),
                                           ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/ping"):
            self._send(200, json.dumps(ping_model(), ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/store"):
            try:
                payload = json.loads(body or b"{}")
                if payload.get("clear") and _supa() and not is_admin_user(
                        self._bearer_uid(), self._req_team(), self._bearer_email()):
                    self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                    return
                st = get_store()
                if payload.get("clear") and st:
                    st.clear()
                self._send(200, json.dumps({"ok": True, "count": (st.count() if st else 0)},
                                           ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/auth"):                 # 로그인/가입 프록시(supabase)
            try:
                self._send(200, json.dumps(auth_action(json.loads(body or b"{}")),
                                           ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/feedback"):
            try:
                data = json.loads(body or b"{}")
                if not data.get("clear") and not self._inject_reviewer(data):
                    self._send(401, json.dumps({"error": "인증 필요"}, ensure_ascii=False), _JSON)
                    return
                rl_key = (data.get("reviewer") or "").strip() or self.client_address[0]
                if not data.get("clear") and rate_limited(rl_key):
                    self._send(429, json.dumps({"error": "잠시 후 다시 시도하세요(검수 속도 제한)"},
                                               ensure_ascii=False), _JSON)
                    return
                self._send(200, json.dumps(apply_feedback(data), ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/badges"):                # 배지 획득 영속(기기 간 기준선)
            try:
                data = json.loads(body or b"{}")
                uid = self._bearer_uid() or (data.get("reviewer") or "").strip()
                self._send(200, json.dumps(save_badges(uid, data.get("earned")),
                                           ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/reviewer"):
            try:
                data = json.loads(body or b"{}")
                if not self._inject_reviewer(data):
                    self._send(401, json.dumps({"error": "인증 필요"}, ensure_ascii=False), _JSON)
                    return
                self._send(200, json.dumps(register_reviewer(data), ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/admin"):
            try:
                self._send(200, json.dumps(admin_action(self._bearer_uid(), self._req_team(),
                           json.loads(body or b"{}"), self._bearer_email()), ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/learn-batch"):        # 일배치 학습 수동 실행(관리자): 개선+골든+회귀평가
            try:
                if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                    self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                    return
                data = json.loads(body or b"{}")
                self._send(200, json.dumps(learning_batch(self._req_team(), data.get("models")),
                                           ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/compare-models"):    # 골든셋 다중 모델 비교(관리자)
            try:
                if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                    self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                    return
                data = json.loads(body or b"{}")
                self._send(200, json.dumps(compare_models_on_golden(data.get("models"), self._req_team(),
                                           scope=(data.get("scope") or "all").strip()),
                                           ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/learn-report"):       # 최근 일배치 결과 수신(개선·골든·평가·모델비교)
            self._send(200, json.dumps({"ok": True, "report": _LAST_LEARN_REPORT}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/patch-meta"):          # 검수자 구조화 교정(빈 카테고리 채우기 등)
            try:
                data = json.loads(body or b"{}")
                if not self._inject_reviewer(data):
                    self._send(401, json.dumps({"error": "인증 필요"}, ensure_ascii=False), _JSON)
                    return
                res = patch_content_meta(data.get("hash"), data.get("patch"),
                                         self._req_team(), reviewer=data.get("reviewer") or "")
                if res.get("ok"):                       # 분류 채우기 미션 판정(1회 보상)
                    fresh = _check_missions((data.get("reviewer") or "").strip(), self._req_team())
                    if fresh:
                        res["missions_completed"] = fresh
                self._send(200, json.dumps(res, ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/meta-compile"):
            try:
                self._send(200, json.dumps(meta_compile_run(self._req_team()),
                                           ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/eval-judge"):        # 평가 상세 · 건별 판정(집단 지성)
            try:
                data = json.loads(body or b"{}")
                rv = (data.get("reviewer") or "").strip()
                verdict = (data.get("verdict") or "").strip()
                ch = (data.get("hash") or "").strip()
                if not rv or not ch or verdict not in ("adopt", "reject"):
                    self._send(200, json.dumps({"ok": False, "error": "판정 값이 올바르지 않습니다"}, ensure_ascii=False), _JSON)
                    return
                st = get_store()
                ok = bool(st and hasattr(st, "save_eval_check")
                          and st.save_eval_check(ch, rv, verdict, str(data.get("expected") or ""),
                                                 str(data.get("got") or ""), team=self._req_team()))
                if ok:
                    try:                          # 판정 보상: 콘텐츠당 1회 +5pt(재판정은 upsert 만)
                        st.log_event_once(rv, "evja:" + ch, 0, 5, team=self._req_team())
                    except Exception:
                        pass
                counts = {}
                try:
                    counts = (st.eval_check_counts(team=self._req_team()) or {}).get(ch) or {}
                except Exception:
                    pass
                _agg_bump()
                self._send(200, json.dumps({"ok": ok, "judge": counts or {"adopt": 0, "reject": 0, "reviewers": {}}},
                                           ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/eval-golden"):       # 등록 골든셋으로 평가 실행(기준 모델·콘텐츠 풀)
            try:
                data = json.loads(body or b"{}")
                self._send(200, json.dumps(eval_golden(self._req_team(),
                                           model=(data.get("model") or "").strip(),
                                           scope=(data.get("scope") or "all").strip()), ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/golden-remove"):     # 관리자: 골든 개별 삭제(라벨 오류 후보 처리)
            try:
                if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                    self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                    return
                data = json.loads(body or b"{}")
                st = get_store()
                ok = bool(st and hasattr(st, "remove_golden")
                          and st.remove_golden((data.get("hash") or "").strip(), team=self._req_team()))
                _agg_bump()
                self._send(200, json.dumps({"ok": ok}, ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/golden"):            # 관리자: 골든셋 등록(.jsonl 업로드 · merge 지원)
            try:
                ctype = self.headers.get("Content-Type", "")
                merge = False
                if "multipart/form-data" in ctype:
                    fields = _parse_multipart(body, ctype.split("boundary=", 1)[1].strip())
                    f = fields.get("file")
                    raw = f.get("bytes", b"") if isinstance(f, dict) else b""
                    merge = str(fields.get("merge") or "").strip().lower() in ("1", "true")
                else:
                    raw = body
                rows = [json.loads(ln) for ln in raw.decode("utf-8", "replace").splitlines() if ln.strip()]
                self._send(200, json.dumps(register_golden(self._bearer_uid(), self._req_team(), rows,
                                           self._bearer_email(), merge=merge), ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/presence"):
            try:
                p = json.loads(body or b"{}")
                broadcast({"type": "presence", "reviewer": (p.get("reviewer") or "").strip(),
                           "hash": p.get("hash") or "", "action": p.get("action") or "viewing"})
                self._send(200, json.dumps({"ok": True}, ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/purpose"):           # 관리자: 콘텐츠 용도 지정(검수용/평가용)
            try:
                if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                    self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                    return
                data = json.loads(body or b"{}")
                st = get_store()
                n = st.set_purpose([h for h in (data.get("hashes") or []) if h],
                                   (data.get("purpose") or "").strip(), team=self._req_team()) if st else 0
                _agg_bump()
                self._send(200, json.dumps({"ok": bool(n), "n": n}, ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/rerun-all"):         # 관리자: 전체 콘텐츠 일괄 실행
            try:
                if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                    self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                    return
                data = json.loads(body or b"{}")
                self._send(200, json.dumps(rerun_all((data.get("model") or "").strip(), self._req_team()),
                                           ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/rerun"):             # 관리자: 같은 콘텐츠를 다른 모델로 재실행
            try:
                if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                    self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                    return
                data = json.loads(body or b"{}")
                self._send(200, json.dumps(rerun_content(data.get("hash"), (data.get("model") or "").strip(),
                                           self._req_team()), ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/ingest-run"):
            try:
                # 콘텐츠 인입은 관리자 통제(수동·자동 공통)
                if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                    self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                    return
                p = json.loads(body or b"{}")
                res = ingest_run_source(p, trigger="manual")
                self._send(200, json.dumps(res, ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/dict"):
            try:
                payload = json.loads(body or b"{}")
                fn = reset_dict_overrides if payload.get("reset") else (lambda: edit_dict(payload))
                self._send(200, json.dumps(fn(), ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/usermeta"):
            try:
                ctype = self.headers.get("Content-Type", "")
                f = None
                if "multipart/form-data" in ctype:
                    boundary = ctype.split("boundary=", 1)[1].strip()
                    f = _parse_multipart(body, boundary).get("file")
                logs = f["bytes"] if isinstance(f, dict) and f.get("bytes") else None
                name = f.get("filename", "logs.csv") if isinstance(f, dict) else ""
                self._send(200, json.dumps(usermeta_data(logs, name), ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if not self.path.startswith("/run"):
            self._send(404, "not found")
            return
        # 추출 실행(단건·배치) = 콘텐츠 인입 → 관리자 통제(supabase 모드)
        if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
            self._send(403, json.dumps({"error": "콘텐츠 인입은 관리자 전용입니다"}, ensure_ascii=False), _JSON)
            return
        ctype = self.headers.get("Content-Type", "")
        try:
            if "multipart/form-data" in ctype:
                boundary = ctype.split("boundary=", 1)[1].strip()
                fields = _parse_multipart(body, boundary)
            else:
                fields = json.loads(body or b"{}")
            if self.path.startswith("/run-batch"):
                f = fields.get("file")
                if not isinstance(f, dict) or not f.get("bytes"):
                    result = {"error": "파일이 없습니다"}
                else:
                    result = run_batch(f["bytes"], f.get("filename", "upload.xlsx"),
                                       purpose=str(fields.get("purpose") or ""), team=self._req_team())
            else:
                result = run_pipeline(fields, mock=self.server_mock, team=self._req_team())
            self._send(200, json.dumps(result, ensure_ascii=False), _JSON)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)


PAGE = """<!doctype html>
<html lang="ko" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prism</title>
<meta name="description" content="이미지·텍스트·엑셀에서 리드문·엔티티·인텐트·콘텐츠 카테고리를 추출하는 콘텐츠 메타 도구">
<link rel="icon" type="image/svg+xml" href="/vendor/prism-favicon.svg">
<link rel="apple-touch-icon" href="/vendor/prism-icon-180.png">
<link href="/vendor/pretendard.css" rel="stylesheet">
<link href="/vendor/gmarket.css" rel="stylesheet">
<link href="/vendor/ds-theme.css" rel="stylesheet">
<link href="/vendor/ds-components.css" rel="stylesheet">
<script src="/vendor/tailwind.js"></script>
<script>
  // 전역 호버 툴팁: [data-tip] 위임 · position:fixed 로 overflow/스택 컨텍스트에 안 잘림
  (function () {
    var tip = null;
    function box() {
      if (!tip) { tip = document.createElement('div'); tip.id = 'tipfloat'; document.body.appendChild(tip); }
      return tip;
    }
    function show(el) {
      var t = el.getAttribute('data-tip'); if (!t) return;
      var b = box(); b.textContent = t; b.style.display = 'block';
      var r = el.getBoundingClientRect(), pos = el.getAttribute('data-tip-pos') || 'top';
      var tw = b.offsetWidth, th = b.offsetHeight, x, y;
      if (pos === 'bottom') { x = r.left + r.width / 2 - tw / 2; y = r.bottom + 8; }
      else if (pos === 'left') { x = r.left - tw - 8; y = r.top + r.height / 2 - th / 2; }
      else if (pos === 'right') { x = r.right + 8; y = r.top + r.height / 2 - th / 2; }
      else { x = r.left + r.width / 2 - tw / 2; y = r.top - th - 8; }
      x = Math.max(8, Math.min(x, window.innerWidth - tw - 8));
      y = Math.max(8, Math.min(y, window.innerHeight - th - 8));
      b.style.left = x + 'px'; b.style.top = y + 'px';
    }
    function hide() { if (tip) tip.style.display = 'none'; }
    document.addEventListener('mouseover', function (e) {
      var el = e.target && e.target.closest ? e.target.closest('[data-tip]') : null;
      if (el) show(el); else hide();
    });
    document.addEventListener('scroll', hide, true);
    document.addEventListener('mousedown', hide, true);
  })();
</script>
<script>
  tailwind.config = {
    theme: { extend: {
      fontFamily: {
        sans: ['"Pretendard Variable"', 'Pretendard', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      colors: {
        // Anchor(axz) 라이트 · 단일 토큰 소스(ds-theme.css 와 1:1). Blue=Primary 액션.
        // 'violet' 은 역사적 유틸명 · 값은 Anchor Blue 로 통일.
        violet: { DEFAULT: '#1e84ff', hover: '#0066db', deep: '#004fad', tint: 'rgba(30,132,255,0.16)' },
        solar: '#18ba45',
        // ⚠️ CSS 변수로 매핑(하드코딩 금지) → text-ink/bg-surface 등 유틸이 라이트/다크 자동 적응.
        // 예전엔 #000/#fff 고정이라 다크모드서 검은 글씨·흰 박스로 안 보였음.
        canvas: 'var(--ds-canvas)', surface: 'var(--ds-surface-white)', surface2: 'var(--ds-surface-white)',
        ink: 'var(--ds-ink)', body: 'var(--ds-body)', muted: 'var(--ds-muted)', hair: 'var(--ds-hairline)',
      },
    } },
  };
</script>
<style>
  /* 앱 레벨 별칭 · 역사적 변수명(--ds-violet*·--ds-surface2·--ds-solar)을
     Anchor 토큰(ds-theme.css)으로 매핑. 고정 위젯 테두리는 Blue 틴트로 전환. */
  :root{
    --ds-violet: var(--ds-primary);
    --ds-violet-hover: var(--ds-primary-hover);
    --ds-violet-deep: var(--ds-primary-deep);
    --ds-violet-tint: var(--ds-primary-tint);
    --ds-surface2: var(--ds-surface-white);
    --ds-solar: var(--ds-success);
    --ds-hair: var(--ds-hairline);
  }
</style>
<script>
  document.addEventListener('alpine:init', () => {
    Alpine.data('prismApp', () => ({
      tabItems: [{ id: 'image', label: '이미지' }, { id: 'text', label: '텍스트' }, { id: 'excel', label: '엑셀' }],
      activeTabId: 'image',
      // 위젯 홈 셸 · 홈(캔버스) + 카테고리 내비
      mod: 'home',
      dashTop: 'content',   // (구 현황 대시보드 잔여 상태 · 위젯 홈 호환용)
      drillOpen: false, drillData: null, drillBusy: false,  // 대시보드 드릴다운
      detailOpen: false, detail: null,   // 콘텐츠 상세(공통 컴포넌트): 좌 원문 렌더 · 우 평가
      badgeToast: null,   // 배지 달성 축하 오버레이
      ptToast: null,      // 검수 완료 시 점수 상승(+PT) 리워드 토스트
      errMsg: '',   // 전역 에러 토스트(조용한 실패 노출)
      dashSub: 'batch',     // 콘텐츠 서브탭: batch(배치결과) | quality(품질·법령) | topic(토픽)
      // 메뉴별 의미에 맞는 아이콘(공유 grid/square 폐기) kind 칩은 미사용
      navIcons: {
        home: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M4 11 12 4l8 7M6 10v9h12v-9" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
        auto: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M21 12a9 9 0 1 1-2.64-6.36" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><path d="M21 4v4h-4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        run: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
        queue: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M4 6h16M4 12h16M4 18h10" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><circle cx="19" cy="18" r="2" stroke="currentColor" stroke-width="1.6"/></svg>',
        dash: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M3 3v18h18M8 15v3m4-9v9m4-5v5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
        quality: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M9 12l2 2 4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        eval: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M9 11l3 3 8-8M21 12a9 9 0 1 1-6.2-8.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        user: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="8" r="3.4" stroke="currentColor" stroke-width="1.6"/><path d="M5 20a7 7 0 0 1 14 0" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
        dict: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M5 4h12a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M5 18a2 2 0 0 1 2-2h12" stroke="currentColor" stroke-width="1.5"/></svg>',
        prompt: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><rect x="3" y="4" width="18" height="16" rx="2" stroke="currentColor" stroke-width="1.5"/><path d="M7 9l3 3-3 3M13 15h4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        intake: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M3 5h18l-7 8v5l-4 2v-7z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
        review: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M9 11l2 2 4-4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12" cy="12" r="8.5" stroke="currentColor" stroke-width="1.5"/></svg>',
        admin: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><circle cx="9" cy="8" r="3" stroke="currentColor" stroke-width="1.6"/><path d="M3 20a6 6 0 0 1 12 0M16 7l2 2 4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        arena: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M8 21h8M12 17v4M6 4h12v4a6 6 0 0 1-12 0V4Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M18 5h2.5a2 2 0 0 1 0 4H18M6 5H3.5a2 2 0 0 0 0 4H6" stroke="currentColor" stroke-width="1.6"/></svg>',
        system: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" stroke="currentColor" stroke-width="1.5"/><path d="M19.4 13a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V13Z" stroke="currentColor" stroke-width="1.3"/></svg>',
      },
      // 멤버 메뉴는 둘: 콘텐츠 검수(판정·교정 → 정답 축적) / 평가(일치율·모델 비교)
      mods: [
        { g: '검수 · 평가', items: [
          { id: 'create', label: '콘텐츠 검수', ic: 'eval' },
          { id: 'evaluate', label: '평가', ic: 'dash' } ] },
        // 콘텐츠 인입(수동·자동·실행 큐)은 '콘텐츠 관리' 단일 메뉴로 통합 · 전부 관리자 통제
        // 권한 2단계: 팀 관리자(생성자·위임)는 '팀 관리'만 추가 · 나머지는 운영 관리자(허용목록) 전용
        { g: '관리자', gcond: 'admin', items: [
          { id: 'content', label: '콘텐츠 관리', ic: 'intake', cond: 'sysadmin' },
          { id: 'testset', label: '테스트셋 관리', ic: 'eval', cond: 'sysadmin' },
          { id: 'admin', label: '팀 관리', ic: 'admin', cond: 'admin' },
          { id: 'dict', label: '사전 · 정책', ic: 'dict', cond: 'sysadmin' },
          { id: 'prompt', label: '프롬프트 스튜디오', ic: 'prompt', cond: 'sysadmin' },
          // 실험실: 지금 테스트하지 않는 탐구 요소(법령·토픽·사용자) 보관
          { id: 'lab', label: '실험실', ic: 'auto', cond: 'sysadmin' },
          // 시스템 설정: 데이터 관리(상단) + API 키·모델(하단) 통합 · 운영 관리자 전용
          { id: 'system', label: '시스템 설정', ic: 'system', cond: 'sysadmin' } ] },
      ],
      get verTxt() {                         // 현재 프롬프트 버전(v=학습 반영 회차+1) · 미확정은 '-'
        return (this.goldenStatus && this.goldenStatus.batch_seq != null) ? ('v' + (this.goldenStatus.batch_seq + 1)) : '-';
      },
      navVisible(c) {                        // 메뉴 노출 판정: admin=팀 관리자 이상 · sysadmin=운영 관리자
        if (!c) return true;
        if (c === 'admin') return this.backend !== 'supabase' || (this.adminData && this.adminData.isAdmin);
        if (c === 'sysadmin') return this.backend !== 'supabase' || (this.adminData && this.adminData.isSysAdmin);
        return this.backend === c;
      },
      contentTab: 'run',                      // 콘텐츠 관리 STEP 1 카드: 수동(run)/자동(auto)
      addPurpose: 'review',                   // 추가 용도: review 검수용(기본) | eval 평가용(홀드아웃)
      createTab: 'raw',                       // 콘텐츠 검수: raw(검수 대상 콘텐츠·기본) | edit(결과 비교)
      testTab: 'status',                      // 테스트셋 관리: status(현황·학습 반영) | golden(정답셋) | data(학습 데이터)
      labTab: 'legal',                        // 실험실(지금 미테스트 요소): legal(법령) | topic(토픽) | user(사용자)
      queueTrig: '',                          // 실행 큐 자동/수동 필터
      get filteredJobs() { return (this.runningJobs || []).filter((j) => !this.queueTrig || (this.queueTrig === 'auto' ? j.trigger === 'auto' : j.trigger !== 'auto')); },
      // 위젯 홈 인터랙션 상태
      chatOpen: false, addMenuOpen: false, editing: false, theme: 'light',
      chatMsgs: [{ from: 'bot', text: '무엇을 도와드릴까요? 작업을 말로 지시해 보세요' }],
      chatDraft: '',
      dashData: null, topicData: null, dictData: null, userData: null, modBusy: false, dictGroup: '',
      // 팀 실시간 협업: 검수자 식별(이름+캐릭터) · 검수 대기 · 라이브 이벤트
      reviewer: '', reviewerEditing: false, reviewerChar: 'boksil',
      // Supabase 인증(ID/PW) · backend==='supabase' 일 때
      backend: 'supabase', authToken: '', authEmail: '', authPw: '', authPw2: '', authMode: 'login', authMsg: '', noTeam: false,
      // 팀(멀티테넌시): 생성/가입 + 내 초대코드
      teamMode: 'join', teamName: '', inviteCode: '', myInvite: '', newTeamName: '', teamMsg: '',
      charOptions: [
        { id: 'boksil', label: '복실', role: '검수', img: '/vendor/boksil-catcher.svg' },
        { id: 'daesik', label: '대식', role: '추출', img: '/vendor/daesik-batter.svg' },
        { id: 'yonghee', label: '용희', role: '분석', img: '/vendor/yonghee-pitcher.svg' },
        { id: 'ddakji', label: '딱지', role: '판정', img: '/vendor/ddakji-manager.svg' },
      ],
      queueData: { items: [], n: 0 }, queueOnlyUnreviewed: true,
      arenaData: null,
      adminData: null,        // 팀 관리(supabase)
      // 골든셋 평가(정합성) 상태 + 테스트(로우 데이터) 상태
      goldenResult: null, goldenBusy: false, goldenMsg: '',
      rawData: null, rawSel: null,
      async loadRaw() { try { const r = await (await fetch('/raw?limit=200', { headers: this._authHeaders() })).json(); if (r && r.ok) { this.rawData = r; this.rawSel = null; } } catch (e) {} },
      // 검수 대상 콘텐츠: 상단 모델→버전 구분 + 필터
      rawQ: '', rawGrade: '', rawModel: '', rawSvc: '', rawRev: '',
      // 결과 비교: 요소 단위 모델별 현황 + 콘텐츠별 초안 diff(팝업)
      cmpDraftHash: '', draftsData: null, cmpL: 0, cmpR: 1,
      cmpModalOpen: false, cmpTitle: '', cmpAmiss: false, cmpBmiss: false,
      msData: null, abAm: '', abAv: '', abBm: '', abBv: '',
      async loadModelStats() {
        try {
          const r = await (await fetch('/model-stats', { headers: this._authHeaders() })).json();
          if (r && r.ok) {
            this.msData = r;
            const ms = r.models;
            if (ms.length && !this.abAm) { this.abAm = ms[0].model; this.abAv = String(ms[0].version); }
            if (ms.length && !this.abBm) { const b = ms.find((m) => m.key !== (this.abAm + ' · v' + this.abAv)) || ms[0]; this.abBm = b.model; this.abBv = String(b.version); }
          }
        } catch (e) {}
      },
      get msModels() { return [...new Set((((this.msData||{}).models)||[]).map((m) => m.model))]; },
      msVers(model) { return (((this.msData||{}).models)||[]).filter((m) => m.model === model).map((m) => m.version).sort((a,b)=>b-a); },
      msCol(model, ver) { return (((this.msData||{}).models)||[]).find((m) => m.model === model && String(m.version) === String(ver)) || null; },
      get abCols() { return [this.msCol(this.abAm, this.abAv), this.msCol(this.abBm, this.abBv)].filter(Boolean); },
      abWin(f, mi) { const c = this.abCols; return c.length === 2 && (c[mi][f] || 0) > (c[1 - mi][f] || 0); },
      openCmpModal(r) { this.cmpTitle = r.title || '(제목 없음)'; this.cmpDraftHash = r.hash; this.cmpModalOpen = true; this.loadDrafts(); },
      async loadDrafts() {
        this.draftsData = null; this.cmpL = 0; this.cmpR = 1; this.cmpAmiss = false; this.cmpBmiss = false;
        if (!this.cmpDraftHash) return;
        try {
          const r = await (await fetch('/drafts?hash=' + encodeURIComponent(this.cmpDraftHash), { headers: this._authHeaders() })).json();
          if (!(r && r.ok)) return;
          this.draftsData = r;
          const items = r.items || [];
          const find = (m, v) => {
            let i = items.findIndex((d) => d.model === m && String(d.version) === String(v));
            if (i < 0) i = items.findIndex((d) => d.model === m);
            return i;
          };
          const ia = find(this.abAm, this.abAv), ib = find(this.abBm, this.abBv);
          this.cmpAmiss = ia < 0; this.cmpBmiss = ib < 0;
          this.cmpL = ia >= 0 ? ia : 0;
          this.cmpR = ib >= 0 ? ib : (items.length > 1 ? 1 : 0);
        } catch (e) {}
      },
      get draftPair() {
        const it = (this.draftsData||{}).items || [];
        if (!it.length) return null;
        return { l: it[Math.min(this.cmpL, it.length-1)], r: it[Math.min(this.cmpR, it.length-1)] };
      },
      get draftDiff() {
        const p = this.draftPair; if (!p) return [];
        const pick = (d) => ({
          '등급': (d.quality_meta||{}).finalGrade || '·',
          '품질 사유': ((d.quality_meta||{}).reasons||[]).join(' · ') || '·',
          '카테고리': ((d.item_meta||{}).content_category||[]).join(' · ') || '·',
          '인텐트': ((d.item_meta||{}).intent||[]).join(' · ') || '·',
          '엔티티': ((d.item_meta||{}).entities||[]).join(' · ') || '·',
          '리드문': (d.item_meta||{}).summary || '·',
        });
        const L = pick(p.l), R = pick(p.r);
        return Object.keys(L).map((k) => ({ k: k, l: L[k], r: R[k], diff: L[k] !== R[k] }));
      },
      openRawDetail(r) { this.openDetail({ hash: r.hash, title: r.title, service: r.service, body: r.body || '', url: r.url || '', summary: r.summary || '', entities: r.entities || [], intent: r.intent || [], category: r.category || [], grade: r.grade || '', reasons: r.reasons || [], model: r.model || '', fb: Object.assign({}, r.fb) }); },
      get rawModels() { return [...new Set(((this.rawData||{}).items||[]).map((r) => r.model).filter(Boolean))]; },
      get rawSvcs() { return [...new Set(((this.rawData||{}).items||[]).map((r) => r.service).filter(Boolean))]; },
      get rawFiltered() {
        return (((this.rawData||{}).items)||[]).filter((r) => {
          if (this.rawQ && !((r.title||'') + (r.category||[]).join(' ') + (r.reasons||[]).join(' ')).toLowerCase().includes(this.rawQ.toLowerCase())) return false;
          if (this.rawGrade && (r.grade||'') !== this.rawGrade) return false;
          if (this.rawModel && (r.model||'') !== this.rawModel) return false;
          if (this.rawSvc && (r.service||'') !== this.rawSvc) return false;
          if (this.rawRev === 'todo' && (r.fb && r.fb.verdict)) return false;
          if (this.rawRev === 'done' && !(r.fb && r.fb.verdict)) return false;
          return true;
        });
      },

      // 관리자: 같은 콘텐츠를 다른 모델로 재실행(초안 재생성)
      rerunHash: '', rerunModel: '', rerunBusy: false, rerunMsg: '',
      bulkModel: '', bulkBusy: false, bulkMsg: '',
      async runBulk() {
        if (!this.bulkModel) return;
        if (!confirm('모든 콘텐츠를 ' + this.bulkModel + ' 로 다시 실행합니다(건당 비용 발생 · 기존 초안은 이력 보존) · 진행할까요?')) return;
        this.bulkBusy = true; this.bulkMsg = '';
        try {
          const r = await (await fetch('/rerun-all', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ model: this.bulkModel }) })).json();
          this.bulkMsg = r && r.ok ? ('✓ 완료 ' + r.done + '건' + (r.failed ? (' · 실패 ' + r.failed) : '')) : ((r && r.error) || '실패');
          this.loadDash(); this.loadRaw();
        } catch (e) { this.bulkMsg = '일괄 실행 실패'; }
        this.bulkBusy = false;
      },
      async runRerun() {
        if (!this.rerunHash || !this.rerunModel) return;
        if (!confirm('기존 초안을 덮어씁니다(이전 초안은 이력에 보존) · 진행할까요?')) return;
        this.rerunBusy = true; this.rerunMsg = '';
        try {
          const r = await (await fetch('/rerun', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ hash: this.rerunHash, model: this.rerunModel }) })).json();
          if (r && r.error) this.rerunMsg = r.error;
          else { this.result = r; this.rerunMsg = '✓ 재실행 완료 · ' + this.rerunModel; this.loadDash(); this.loadRaw(); }
        } catch (e) { this.rerunMsg = '재실행 실패'; }
        this.rerunBusy = false;
      },
      metaResults: null, metaBusy: false,
      srcFilter: '',          // 결과 출처 필터(자동 인입/단건/배치)
      liveMsg: '', liveSeen: {}, _es: null,
      loading: false,
      status: '',
      result: null,
      batchResult: null,
      groups: ['뉴스', '연예', '스포츠', '콘텐츠', '커뮤니티', '블로그', '음악', '동영상'],
      group: '뉴스',
      imgTitle: '', imgCaption: '',
      txtTitle: '', txtBody: '',
      imgFiles: [], imgThumbs: [], imgDrag: false,
      excelFile: null, xlsDrag: false,
      copyMsg: '',

      // 설정(키 / 모델 슬롯 / 추론강도 / 추가 지시) · 우측 설정 패널
      cfg: { hasKey: false, model: '', persisted: false, forcedMock: false, hasBizKey: false, hasTimelyKey: false },
      cfgModel: '', cfgPersist: true, cfgBusy: false,
      models: [], modelsMsg: '',
      reasoning: 'default', systemPrompt: '', prefMsg: '', legalEnabled: false,
      stagePrompts: { extract: '', analyze: '', review: '', judge: '' },
      promptDefaults: { extract: '', analyze: '', review: '', judge: '' },
      stageModels: { extract: '', analyze: '', review: '', judge: '' },
      modelPrompts: {}, availableModels: [],
      stagePromptsMeta: {}, stageMsg: { extract: '', analyze: '', review: '', judge: '' },
      reasoningOpts: [{ id: 'low', label: 'Low' }, { id: 'default', label: 'Medium' }, { id: 'high', label: 'High' }],

      // 모델 슬롯 + 스텝식 설정
      textProvider: 'solar', textModel: '',
      visionProvider: 'upstage_ie', visionModel: '',
      slotMsg: '',
      keyServices: ['bizrouter', 'timely', 'solar'],  // 라우터 카드 먼저, 직접(Solar) 뒤
      keyShow: { solar: false, bizrouter: false, timely: false },
      keyInputs: { solar: '', bizrouter: '', timely: '' },
      keyMsgs: { solar: '', bizrouter: '', timely: '' },
      keyDefs: {
        solar:     { label: 'Upstage Solar 키', ph: 'up_xxxxxxxx', has: 'hasKey', persisted: 'persisted' },
        bizrouter: { label: 'BizRouter 키', ph: 'sk-br-v1-…', has: 'hasBizKey', persisted: 'bizPersisted' },
        timely:    { label: 'Timely 키', ph: 'timely API key', has: 'hasTimelyKey', persisted: 'timelyPersisted' },
      },
      providerLabels: { solar: 'Solar', upstage_ie: 'Upstage', bizrouter: 'BizRouter', timely: 'Timely' },
      modelCatalog: {
        bizrouter: {
          text: ['openai/gpt-5.4', 'openai/gpt-5.4-mini', 'anthropic/claude-sonnet-4.6',
            'anthropic/claude-opus-4.6', 'google/gemini-2.5-pro', 'google/gemini-2.5-flash', 'deepseek/deepseek-v3.2'],
          vision: ['google/gemini-2.5-flash', 'google/gemini-2.5-pro', 'openai/gpt-5.4',
            'openai/gpt-5-mini', 'anthropic/claude-sonnet-4.6', 'anthropic/claude-opus-4.6'],
        },
        timely: {
          text: ['gpt-5.4', 'gpt-5.4-mini', 'claude-opus-4-8', 'claude-sonnet-4-6', 'claude-haiku-4-5',
            'gemini-3.5-flash', 'gemini-3.1-pro-preview', 'deepseek-v4-pro', 'deepseek-chat'],
          vision: ['gpt-5.4', 'gpt-5.4-mini', 'claude-opus-4-8', 'claude-sonnet-4-6',
            'gemini-3.5-flash', 'gemini-3.1-pro-preview'],
        },
      },

      init() {
        this.loadReviewer();                           // 검수자·토큰(localStorage) · refreshConfig 의 관리자 로드보다 먼저
        this.refreshConfig();
        this.startLive();                              // 실시간 SSE 구독
        this.loadHome();                               // 배치된 홈 위젯(localStorage)
        this.loadDash();                               // 홈 위젯 데이터(/dashboard)
        this.loadArena();                              // 홈 = 아레나
        // 딥링크: ?m=run|dash|quality|... 로 특정 뷰 진입(설정은 ?settings)
        try { const q = new URLSearchParams(location.search); const m = q.get('m'); if (m) this.selectMod(m); if (q.has('settings')) this.selectMod('system'); } catch (e) {}
        fetch('/vocab').then(r => r.json()).then(j => { if (j.groups && j.groups.length) this.groups = j.groups; }).catch(() => {});
        // Cmd/Ctrl + Enter 로 추출 실행
        window.addEventListener('keydown', (e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !this.loading) { e.preventDefault(); this.run(); }
        });
      },
      get tabLabel() { return (this.tabItems.find(t => t.id === this.activeTabId) || {}).label || ''; },
      get modLabel() {
        if (this.mod === 'home') return '홈';
        for (const g of this.mods) for (const it of g.items) if (it.id === this.mod) return it.label;
        return '';
      },
      // 연결 현황(다중) · 여러 제공자를 동시에 넣어도 각각의 연결 상태를 표시
      get connList() {
        return [
          { id: 'solar', label: 'Solar', on: !!this.cfg.hasKey },
          { id: 'bizrouter', label: 'BizRouter', on: !!this.cfg.hasBizKey },
          { id: 'timely', label: 'Timely', on: !!this.cfg.hasTimelyKey },
        ];
      },
      get connCount() { return this.connList.filter((c) => c.on).length; },
      get modSub() {
        const m = { home: '검수 진척율을 함께 끝까지 · 검수할수록 진척·점수·배지로 성장합니다', auto: '콘텐츠 자동 인입 파이프라인 설정 (REST API · Kafka 등)', run: '수동으로 이미지·텍스트·엑셀 추출 (기본 운영은 자동 인입)', queue: '진행 중·대기 중인 추출 작업', dash: '추출 결과 집계 · 유통 G/R · 분포', review: 'YELLOW 사람검수 대기열 · 팀 다중 의견 + 실시간 협업', arena: '검수 진척율(개인·팀 평균) · 검수할수록 게이지가 차오르고 기여가 점수로', admin: '팀 멤버 · 초대 코드(팀 관리자)', system: '데이터 관리 + API 키·모델 설정(운영 관리자)', quality: '품질·법령 판정 + 엔티티·사건·조건 토픽', user: '행동 로그 → 소비 형태·강도·선호', eval: '콘텐츠별 평가 피드백(학습 루프) · 처리 이력·보정·비용', dict: '사전·카테고리·품질·법령 정책을 직접 수정', prompt: '추출 방향을 조향하는 시스템 프롬프트·추론 강도', intake: 'ITEM TYPE별 필터·처리 정책 + 콘텐츠 출처 분류' };
        return m[this.mod] || '';
      },
      selectMod(id) {
        this.mod = id; this.status = ''; this.addMenuOpen = false;
        // 구 메뉴 id 호환 매핑(위젯·URL): 인입류 → 콘텐츠 관리 · 검수류 → 콘텐츠 검수 · 분석/현황 → 테스트셋 관리
        if (id === 'intake') id = 'dict';
        if (id === 'run' || id === 'auto') { this.contentTab = id; id = 'content'; }
        if (id === 'queue') id = 'content';
        if (id === 'content' || id === 'evaluate') { this.loadDash(); this.loadGoldenStatus(); }
        if (id === 'prompt' || id === 'testset') this.loadGoldenStatus();
        if (id === 'dash') { id = 'create'; this.createTab = 'raw'; }
        if (id === 'review') { id = 'create'; this.createTab = 'raw'; }
        if (id === 'quality') { id = 'lab'; this.labTab = 'legal'; }
        if (id === 'user') { id = 'lab'; this.labTab = 'user'; }
        if (id === 'eval') id = 'evaluate';
        if (id === 'home') { this.loadArena(); this.loadDash(); }
        else if (id === 'create') { this.loadDash(); this.loadRaw(); }
        else if (id === 'evaluate') this.loadGoldenStatus();
        else if (id === 'arena') this.loadArena();
        else if (id === 'admin' || id === 'system') this.loadAdmin();
        else if (id === 'testset') { this.loadGoldenStatus(); this.loadLearnReport(); this.loadGoldenList(); this.loadLearnData(); this.loadAdmin(); }
        else if (id === 'lab') { this.loadDash(); this.loadTopics(); this.loadUser(); }
        else if (id === 'dict') this.loadDict();
        else if (id === 'prompt') this.loadPromptDefaults();
        if (id === 'content') { this.loadDash(); this.loadDict(); this.fetchIngestStatus(); this.pollIngestStatus(); }
      },
      toggleTheme() {
        this.theme = this.theme === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', this.theme);
      },
      // ── 플로팅 도우미(채팅 허브) ──
      chatSend(text) {
        const v = (text != null ? text : this.chatDraft).trim(); if (!v) return;
        this.chatMsgs.push({ from: 'me', text: v }); this.chatDraft = '';
        setTimeout(() => { this.chatMsgs.push({ from: 'bot', text: '알겠어요 "' + v + '" 작업을 큐에 넣었어요' }); }, 380);
      },
      chatAct(act) {
        this.chatOpen = false;
        if (act === 'extract') this.selectMod('run');
        else if (act === 'settings') this.selectMod('system');
        else if (act === 'dict') this.selectMod('dict');
      },
      // ── 홈 위젯 구성(실동작 위젯만) + 직접 배치 + localStorage 영속 ──
      placed: null,                              // 배치된 위젯 id 목록(첫 방문 = 빈 배열)
      homeCatalog: [
        { id: 'launch-run', label: '새 추출 런처' },
        { id: 'launch-batch', label: '배치 결과 런처' },
        { id: 'launch-dict', label: '사전·정책 런처' },
        { id: 'metrics', label: '핵심 지표' },
        { id: 'quality', label: '품질 점수' },
        { id: 'intents', label: '인텐트 분포' },
        { id: 'categories', label: '카테고리 분포' },
        { id: 'process', label: '처리 프로세스' },
      ],
      loadHome() { try { const s = localStorage.getItem('prism_home'); this.placed = s ? JSON.parse(s) : []; } catch (e) { this.placed = []; } },
      saveHome() { try { localStorage.setItem('prism_home', JSON.stringify(this.placed || [])); } catch (e) {} },
      hasWidget(id) { return !!(this.placed && this.placed.includes(id)); },
      addWidget(id) { this.addMenuOpen = false; if (!this.placed) this.placed = []; if (!this.placed.includes(id)) { this.placed.push(id); this.saveHome(); } },
      removeWidget(id) { this.placed = (this.placed || []).filter((x) => x !== id); this.saveHome(); },
      useRecommended() { this.placed = ['launch-run', 'launch-dict', 'metrics', 'quality', 'intents']; this.saveHome(); },
      async loadDash() { this.modBusy = true; try { this.dashData = await (await fetch('/dashboard')).json(); } catch (e) { this._err('대시보드 불러오기 실패 · 다시 시도하세요'); } this.modBusy = false; },
      async drill(kind, value) {
        this.drillOpen = true; this.drillBusy = true; this.drillData = { kind: kind, value: value, items: [] };
        try { this.drillData = await (await fetch('/drill?kind=' + kind + '&value=' + encodeURIComponent(value))).json(); } catch (e) { this._err('콘텐츠 목록 불러오기 실패'); }
        this.drillBusy = false;
      },
      async topicDrill(t) {                              // 토픽 → 묶인 콘텐츠(배치 결과 드릴다운과 동일 모달)
        if (!t || !t.cluster_id) return;
        this.drillOpen = true; this.drillBusy = true;
        this.drillData = { kind: 'topic', value: (t.name || t.label || t.cluster_id), items: [] };
        try { this.drillData = await (await fetch('/topic-drill?cluster=' + encodeURIComponent(t.cluster_id))).json(); } catch (e) { this._err('토픽 콘텐츠 불러오기 실패'); }
        this.drillBusy = false;
      },
      drillKindKr(k) { return k === 'intent' ? '인텐트' : k === 'category' ? '카테고리' : k === 'topic' ? '토픽' : '품질 사유'; },
      // 콘텐츠 상세 스플릿뷰(공통): 어떤 목록에서든 openDetail(content) 로 진입
      openDetail(c) { this.detail = Object.assign({ entities: [], intent: [], category: [], reasons: [], fb: {} }, c); if (!this.detail.fb) this.detail.fb = {}; this.editVerdict = false; this.pendingBad = false; this.detailBack = this.drillOpen; this.detailOpen = true; this.drillOpen = false; },
      editVerdict: false, pendingBad: false, detailBack: false,
      // 정확 = 즉시 완료 · 수정 필요 = 요소·사유 입력 후 '완료 처리' 로만 확정(누른다고 바로 저장 안 함)
      reviewGood() { this.pendingBad = false; this.setFeedback(this.detail, 'good'); this.editVerdict = false; },
      reviewBadComplete() {
        if (!(this.detail.fb.note || '').trim()) { this._err('무엇을 왜 고쳐야 하는지 입력하세요'); return; }
        this.detail.fb = Object.assign({}, this.detail.fb, { verdict: 'bad' });
        this.saveFbNote(this.detail);
        this.pendingBad = false; this.editVerdict = false;
      },
      // 태그 용어 정의(호버 툴팁): 인텐트=고정 설명 · 품질 사유=사전(dictData) · 카테고리=경로
      INTENT_DEF: {
        '속보·사건 추적': '새로 발생한 사건·이슈를 빠르게 전하고 후속 경과를 추적', '심층 분석': '배경·맥락·데이터로 사안을 깊이 해설',
        '팬덤·화제성': '인물·작품에 대한 팬 반응·화제 중심', '실용 정보': '방법·팁·가이드 등 바로 쓰는 정보',
        '감성·공감': '감정·경험을 나누며 공감을 유도', '오락·유머': '재미·유머 중심의 가벼운 콘텐츠',
        '의견·논쟁': '주장·토론·찬반이 오가는 콘텐츠', '학술·전문': '전문 지식·연구·기술을 다룸',
      },
      termDef(kind, val) {
        if (kind === 'intent') return this.INTENT_DEF[val] || ('인텐트 · ' + val);
        if (kind === 'reason') {
          const d = this.dictData;
          if (d && d.qualityMetas) { const nm = (d.qualityNames && d.qualityNames[val]) || ''; return (nm ? nm + ' · ' : '') + (d.qualityMetas[val] || val); }
          if (!this._dictReq) { this._dictReq = true; this.loadDict(); }
          return '품질 사유 · ' + val;
        }
        if (kind === 'category') return '콘텐츠 카테고리 · ' + val;
        if (kind === 'grade') return val === 'G' ? '등급 G · 유통 가능' : val === 'R' ? '등급 R · 유통 제외(문제 사유 있음)' : '등급 미판정';
        if (kind === 'entity') return '핵심 개체(인물·기관·작품 등) · ' + val;
        return val;
      },
      // 수정 대상 요소(파이프라인 단계 대신 '무엇을 고칠지'로 직관화). 각 요소 → 학습 단계 매핑.
      FIX_ELEMENTS: [
        { id: 'summary', label: '리드문', stage: 'analyze' },
        { id: 'entities', label: '엔티티', stage: 'analyze' },
        { id: 'intent', label: '인텐트', stage: 'analyze' },
        { id: 'category', label: '카테고리', stage: 'analyze' },
        { id: 'grade', label: '등급·유통', stage: 'judge' },
        { id: 'quality', label: '품질 사유', stage: 'review' },
      ],
      elemStage(id) { const e = this.FIX_ELEMENTS.find((x) => x.id === id); return e ? e.stage : 'analyze'; },
      elemLabel(id) { const e = this.FIX_ELEMENTS.find((x) => x.id === id); return e ? e.label : ''; },
      fmtTs(ts) {                                        // epoch(초) → 'M.D HH:mm'
        if (!ts) return '';
        const d = new Date((ts > 1e12 ? ts : ts * 1000));
        const p = (n) => (n < 10 ? '0' + n : '' + n);
        return (d.getMonth() + 1) + '.' + d.getDate() + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
      },
      // ── 배치 결과: 콘텐츠별 평가 피드백 → 학습 루프 ──
      fbNoteOpen: {},
      async setFeedback(c, verdict) {
        const cur = (c.fb && c.fb.verdict) || '';
        const v = (cur === verdict) ? '' : verdict;        // 같은 버튼 재클릭 = 취소
        c.fb = Object.assign({}, c.fb, { verdict: v, ts: (v ? Date.now() / 1000 : 0) });   // 수정 일시 기록
        if (v === 'bad') this.fbNoteOpen[c.hash] = true;
        await this._postFb({ hash: c.hash, service: c.service, title: c.title, model: c.model || '', verdict: v, stage: (c.fb.stage || 'analyze'), note: (c.fb.note || '') });
        if (cur === '' && v !== '') this.celebratePoints(10, '검수 완료');   // 새 검수 = +10 PT
      },
      // 수정 대상 요소: 다중 선택 · 서버 오케스트레이터가 원문을 재분류해 단계별로 분기
      fbElems(fb) { if (!Array.isArray(fb.elems) || !fb.elems.length) fb.elems = [fb.element || 'summary']; return fb.elems; },
      toggleFixElem(fb, id) {
        const a = this.fbElems(fb);
        const i = a.indexOf(id);
        if (i >= 0) { if (a.length > 1) a.splice(i, 1); } else a.push(id);
        fb.element = a[0];
      },
      async saveFbNote(c) {
        const hadNote = !!(c.fb && c.fb._noteRewarded);
        const els = this.fbElems(c.fb).slice();                       // 수정 대상 요소(복수 가능)
        const stage = this.elemStage(els[0]);
        const raw = (c.fb.note || '').trim();
        const tagged = raw ? ('[' + els.map((e) => this.elemLabel(e)).join('·') + '] ' + raw) : raw;
        c.fb = Object.assign({}, c.fb, { verdict: c.fb.verdict || 'bad', stage: stage, ts: Date.now() / 1000 });
        await this._postFb({ hash: c.hash, service: c.service, title: c.title, model: c.model || '', verdict: c.fb.verdict, stage: stage, elements: els, note: tagged });
        this.fbNoteOpen[c.hash] = false;
        if (!hadNote && (c.fb.note || '').trim()) { c.fb._noteRewarded = true; this.celebratePoints(25, '교정 반영'); }  // 교정 = +25 PT(서버 산정과 일치)
      },
      async _postFb(payload) {
        payload = Object.assign({ reviewer: this.reviewer || '', name: this.reviewer || '' }, payload);  // 키+표시명(supabase 면 서버가 uid 로 덮어씀)
        try {
          const r = await (await fetch('/feedback', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(payload) })).json();
          if (r && r.feedback && this.dashData) this.dashData.feedback = r.feedback;
          (r && r.missions_completed || []).forEach((m) => this.celebratePoints(m.bonus, '미션 달성 · ' + m.label));
          return r;
        } catch (e) { return null; }
      },
      // ── 팀 실시간 협업: 검수자 식별 · 검수 대기 · 라이브 ──
      loadReviewer() {
        try { this.reviewer = localStorage.getItem('prism_reviewer') || ''; this.reviewerChar = localStorage.getItem('prism_reviewer_char') || 'boksil'; this.authToken = localStorage.getItem('prism_token') || ''; } catch (e) {}
        if (!this.reviewer) this.reviewerEditing = true;            // 첫 방문 → 등록/로그인 모달
      },
      _authHeaders() { const h = { 'Content-Type': 'application/json' }; if (this.authToken) h['Authorization'] = 'Bearer ' + this.authToken; return h; },
      _err(m) { this.errMsg = m; if (this._errT) clearTimeout(this._errT); this._errT = setTimeout(() => { this.errMsg = ''; }, 4800); },
      get showProfileFields() { return this.backend !== 'supabase' || this.authMode === 'signup' || !!this.reviewer; },
      logout() {
        try { localStorage.removeItem('prism_reviewer'); localStorage.removeItem('prism_reviewer_char'); localStorage.removeItem('prism_token'); } catch (e) {}
        this.reviewer = ''; this.authToken = ''; this.adminData = null; this.arenaData = null;
        this.mod = 'home'; this.reviewerEditing = true;
      },
      async saveReviewer() {
        // 프로필 편집(이미 로그인): 재인증 없이 닉네임·캐릭터만 갱신(팀 재참가 안 함)
        if (this.backend === 'supabase' && this.authToken) {
          const nv = (this.reviewer || '').trim();
          if (!nv) { this.authMsg = '닉네임을 입력하세요'; return; }
          try { await fetch('/reviewer', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ reviewer: nv, name: nv, char: this.reviewerChar }) }); } catch (e) {}
          try { localStorage.setItem('prism_reviewer', nv); localStorage.setItem('prism_reviewer_char', this.reviewerChar); } catch (e) {}
          this.reviewerEditing = false; this.authMsg = '';
          return;
        }
        if (this.backend === 'supabase') {                          // 로그인/가입 먼저
          const email = (this.authEmail || '').trim(), pw = this.authPw || '';
          if (!email || !pw) { this.authMsg = '이메일·비밀번호를 입력하세요'; return; }
          if (this.authMode === 'signup') {
            if (pw.length < 6) { this.authMsg = '비밀번호는 6자 이상이어야 합니다'; return; }
            if (pw !== this.authPw2) { this.authMsg = '비밀번호가 일치하지 않습니다'; return; }
          }
          this.authMsg = '확인 중…';
          let r;
          try { r = await (await fetch('/auth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: this.authMode, email: email, password: pw }) })).json(); }
          catch (e) { this.authMsg = '네트워크 오류'; return; }
          if (!r.ok) { this.authMsg = this._authErr(r.error, r.raw); return; }
          this.authToken = r.access_token; this.authMsg = '';
          try { localStorage.setItem('prism_token', this.authToken); } catch (e) {}
          if (this.authMode === 'login') {                          // 로그인: 기존 프로필 로드(재입력 없음)
            let rr; try { rr = await (await fetch('/reviewer', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ mode: 'login' }) })).json(); } catch (e) { this.authMsg = '네트워크 오류'; return; }
            if (rr && rr.needSignup) { this.authMode = 'signup'; this.authMsg = '가입 정보가 없습니다 · 닉네임·캐릭터·팀을 설정해 가입하세요'; return; }
            if (!rr || !rr.ok) { this.authMsg = (rr && rr.error) || '프로필 로드 실패'; return; }
            this.reviewer = rr.name; this.reviewerChar = rr.char || this.reviewerChar;
            this._badgeSeen = Array.isArray(rr.badges) ? rr.badges : null;   // 서버 배지 기준선(기기 간)
            if (rr.team && rr.team.invite_code) { this.myInvite = rr.team.invite_code; }
            try { localStorage.setItem('prism_reviewer', this.reviewer); localStorage.setItem('prism_reviewer_char', this.reviewerChar); } catch (e) {}
            this.authEmail = ''; this.authPw = '';                   // 상태 정리(편집 재진입 시 오인 방지)
            this.reviewerEditing = false; this.authMsg = ''; this.refreshConfig();
            if (this.mod === 'arena' || this.mod === 'home') this.loadArena();
            return;
          }
        }
        // 가입(supabase) 또는 로컬: 닉네임 필수
        const v = (this.reviewer || '').trim();
        if (!v) { this.authMsg = '닉네임을 입력하세요'; return; }
        this.reviewer = v;
        try { localStorage.setItem('prism_reviewer', v); localStorage.setItem('prism_reviewer_char', this.reviewerChar); } catch (e) {}
        // 검수자 등록(이름·캐릭터 + 팀). supabase 면 서버가 Bearer 의 uid 로 귀속(사칭 불가).
        const body = { reviewer: v, name: v, char: this.reviewerChar };
        // 팀: 없음(none) 또는 코드 참가(join). 팀 생성은 관리자 메뉴 전용.
        if (this.backend === 'supabase') { body.team_mode = this.noTeam ? 'none' : 'join'; body.invite_code = this.inviteCode; }
        try {
          const rr = await (await fetch('/reviewer', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(body) })).json();
          if (rr && !rr.ok) { this.authMsg = rr.error || '등록 실패'; return; }
          if (rr && rr.team && rr.team.invite_code) { this.myInvite = rr.team.invite_code; }   // 초대코드 표시
        } catch (e) {}
        this.authEmail = ''; this.authPw = ''; this.authPw2 = '';
        this.reviewerEditing = false; this.refreshConfig();
        if (this.mod === 'arena' || this.mod === 'home') this.loadArena();
      },
      _authErr(err, raw) {
        const s = ((err || '') + ' ' + (raw || '')).toLowerCase();
        if (s.includes('invalid') && s.includes('credential')) return '이메일 또는 비밀번호가 올바르지 않습니다';
        if (s.includes('already') || s.includes('registered') || s.includes('exists')) return '이미 가입된 이메일입니다 · 로그인해 주세요';
        if (s.includes('password')) return '비밀번호를 확인하세요(6자 이상)';
        if (s.includes('email')) return '이메일 형식을 확인하세요';
        return err || '로그인 실패';
      },
      charImg(id) { return (this.charOptions.find((c) => c.id === id) || this.charOptions[0]).img; },
      startLive() {
        try {
          const es = new EventSource('/events'); this._es = es;
          es.onmessage = (e) => { let d; try { d = JSON.parse(e.data); } catch (_) { return; } this.onLive(d); };
          es.onerror = () => {};                       // 자동 재연결(브라우저 기본)
        } catch (e) {}
      },
      onLive(d) {
        if (!d || !d.type) return;
        if (d.type === 'feedback') {
          if (d.reviewer && d.reviewer !== this.reviewer) {
            const v = d.verdict === 'good' ? '정확' : d.verdict === 'bad' ? '문제' : '취소';
            this.liveToast(d.reviewer + '님 · 「' + (d.title || '콘텐츠') + '」 ' + v);
          }
          if (this.mod === 'create') this.loadRaw();
          if (this.mod === 'arena' || this.mod === 'home') this.loadArena();             // 정확도 게이지 실시간 상승
          if (this.mod === 'dash' || this.mod === 'eval' || this.mod === 'home') this.loadDashThrottled();
        } else if (d.type === 'reap') {
          if (d.plan) this.liveToast('개선안 반영 · ' + (d.plan.length > 42 ? d.plan.slice(0, 42) + '…' : d.plan));
          if (this.mod === 'arena' || this.mod === 'home') this.loadArena();
          if (this.mod === 'prompt') this.loadPromptDefaults();   // 단계 프롬프트(LEARNED) 갱신
        } else if (d.type === 'reviewer') {
          if (this.mod === 'arena' || this.mod === 'home') this.loadArena();             // 다른 사람 캐릭터 변경 반영
        } else if (d.type === 'presence' && d.reviewer && d.reviewer !== this.reviewer) {
          if (d.action === 'viewing') this.liveSeen[d.hash] = d.reviewer; else delete this.liveSeen[d.hash];
        }
      },
      liveToast(msg) { this.liveMsg = msg; clearTimeout(this._lt); this._lt = setTimeout(() => { this.liveMsg = ''; }, 4200); },
      async loadQueue() { this.modBusy = true; try { const p = new URLSearchParams(); if (!this.queueOnlyUnreviewed) p.set('all', '1'); if (this.reviewer) p.set('reviewer', this.reviewer); this.queueData = await (await fetch('/queue?' + p.toString(), { headers: this._authHeaders() })).json(); } catch (e) {} this.modBusy = false; },
      async loadArena() { try { const p = this.reviewer ? ('?reviewer=' + encodeURIComponent(this.reviewer)) : ''; this.arenaData = await (await fetch('/arena' + p, { headers: this._authHeaders() })).json(); } catch (e) { this._err('아레나 불러오기 실패'); } this.checkBadges(); },
      async loadAdmin() { try { this.adminData = await (await fetch('/admin', { headers: this._authHeaders() })).json(); } catch (e) { this._err('팀 관리 불러오기 실패'); } },
      goldenModel: '',                        // 정답셋 목록 · 유래 모델 필터
      get goldenModelList() {
        const its = (this.goldenList && this.goldenList.items) || [];
        return [...new Set(its.map(g => g.model || ''))].sort();
      },
      get filteredGolden() {
        const its = (this.goldenList && this.goldenList.items) || [];
        return this.goldenModel === '' ? its : its.filter(g => (g.model || '') === this.goldenModel);
      },
      async togglePurpose(c) {               // 관리자: 용도 전환(검수용 ↔ 평가용 홀드아웃)
        const next = c.purpose === 'eval' ? 'review' : 'eval';
        try {
          const r = await (await fetch('/purpose', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ hashes: [c.hash], purpose: next }) })).json();
          if (r && r.ok) { c.purpose = next; this.liveToast((c.title || '콘텐츠') + ' · ' + (next === 'eval' ? '평가용' : '검수용') + ' 전환'); }
          else this._err((r && r.error) || '용도 전환 실패');
        } catch (e) { this._err('용도 전환 실패'); }
      },
      evalModel: '', evalScope: 'all',        // 평가 기준: 기준 모델 · 대상 콘텐츠 풀(all=전체 정답셋 | eval=평가용 홀드아웃)
      async runGolden() {
        this.goldenBusy = true; this.goldenResult = null;
        try { this.goldenResult = await (await fetch('/eval-golden', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ model: this.evalModel, scope: this.evalScope }) })).json(); } catch (e) {}
        this.goldenBusy = false;
      },
      // 모델별 정합성 비교(골든셋 평가 탭) · 이항 95% CI 표기
      cmpA: '', cmpB: '', cmpBusy: false, cmpResult: null,
      get cmpCols() {
        const ms = (this.cmpResult && this.cmpResult.models) || [];
        return [this.cmpA, this.cmpB].map((id) => ms.find((m) => m.model === id)).filter(Boolean);
      },
      cmpWin(f, mi) { const c = this.cmpCols; return c.length === 2 && (c[mi][f] || 0) > (c[1 - mi][f] || 0); },
      async runCompare() {
        this.cmpBusy = true; this.cmpResult = null;
        try { this.cmpResult = await (await fetch('/compare-models', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ models: [this.cmpA, this.cmpB], scope: this.evalScope }) })).json(); } catch (e) { this._err('모델 비교 실패'); }
        this.cmpBusy = false;
      },
      myEvalVote(d) { const r = (d.judge && d.judge.reviewers) || {}; return r[this.reviewer] || ''; },
      evalConsensus(d) {
        const j = d.judge || {}; const mg = (this.goldenResult && this.goldenResult.min_good) || 1;
        if ((j.adopt || 0) >= mg && (j.adopt || 0) > (j.reject || 0)) return 'adopt';
        if ((j.reject || 0) >= mg && (j.reject || 0) > (j.adopt || 0)) return 'reject';
        return '';
      },
      async evalJudge(d, verdict) {                 // 평가 판정: 검수와 같은 집단 지성(1인 1표 · 재판정 허용)
        if (!this.ensureReviewer()) return;
        try {
          const r = await (await fetch('/eval-judge', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ hash: d.hash, verdict: verdict, reviewer: this.reviewer, expected: d.expected, got: d.got }) })).json();
          if (r && r.ok) { d.judge = r.judge; this.liveToast('평가 판정 저장 · ' + (verdict === 'adopt' ? '채택' : '탈락')); }
          else this._err((r && r.error) || '판정 저장 실패');
        } catch (e) { this._err('판정 저장 실패'); }
      },
      ciOf(p, n) { if (p == null || !n) return '·'; const s = Math.sqrt(Math.max(p * (1 - p), 0) / n); return this.pctTxt(Math.max(0, p - 1.96 * s)) + '~' + this.pctTxt(Math.min(1, p + 1.96 * s)); },
      // 골든 생성 현황(팀원 공개)
      goldenStatus: null,
      async loadGoldenStatus() { try { const r = await (await fetch('/golden-status', { headers: this._authHeaders() })).json(); if (r && r.ok) this.goldenStatus = r; } catch (e) {} },
      // 관리자 골든 브라우저
      goldenList: null, goldenMerge: true,
      async loadGoldenList() { try { const r = await (await fetch('/golden-list', { headers: this._authHeaders() })).json(); if (r && r.ok) this.goldenList = r; } catch (e) {} },
      async removeGolden(h) {
        if (!confirm('이 골든 항목을 제거할까요? (평가 정답셋에서 빠집니다)')) return;
        try { await fetch('/golden-remove', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ hash: h }) }); } catch (e) {}
        this.loadGoldenList(); this.loadGoldenStatus(); this.loadLearnData();
      },
      async runMetaCompile() {
        this.metaBusy = true;
        try { const r = await (await fetch('/meta-compile', { method: 'POST', headers: this._authHeaders() })).json(); this.metaResults = r.results || null; } catch (e) {}
        this.metaBusy = false; this.loadPromptDefaults();
      },
      // 일배치 학습(수동 실행): 개선 + 골든 축적 + 골든 회귀 평가 + 다중 모델 비교
      learnReport: null, learnBusy: false,
      async runLearnBatch() {
        this.learnBusy = true;
        try {                                        // 모델 비교는 '골든셋 평가' 탭에서 온디맨드(중복 실행 방지)
          const r = await (await fetch('/learn-batch', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({}) })).json();
          if (r && r.ok) { this.learnReport = r; this.metaResults = (r.improve && r.improve.results) || this.metaResults; }
          else this._err((r && r.error) || '일배치 실행 실패');
        } catch (e) { this._err('일배치 실행 실패'); }
        this.learnBusy = false; this.loadPromptDefaults(); this.loadGoldenStatus();
      },
      async loadLearnReport() { try { const r = await (await fetch('/learn-report')).json(); if (r && r.report && r.report.ts) this.learnReport = r.report; } catch (e) {} },
      // 학습 데이터 현황(관리자): 커버리지·일치도·신뢰도·오류 후보·추출(전 기준치 논문 근거)
      learnData: null, learnDataBusy: false,
      async loadLearnData() {
        this.learnDataBusy = true;
        try { const r = await (await fetch('/learn-data', { headers: this._authHeaders() })).json(); if (r && r.ok) this.learnData = r; } catch (e) {}
        this.learnDataBusy = false;
      },
      async exportLearn(kind) {
        try {
          const res = await fetch('/learn-export?kind=' + kind, { headers: this._authHeaders() });
          if (!res.ok) { this._err('내보내기 실패'); return; }
          const blob = await res.blob(); const a = document.createElement('a');
          a.href = URL.createObjectURL(blob); a.download = 'prism_' + kind + '.jsonl';
          a.click(); URL.revokeObjectURL(a.href);
        } catch (e) { this._err('내보내기 실패'); }
      },
      ciTxt(ci) { return ci ? (this.pctTxt(ci.acc) + ' · 95% CI ' + this.pctTxt(ci.lo) + '~' + this.pctTxt(ci.hi) + ' (n=' + ci.n + ')') : '·'; },
      async exportSpec() {
        try {
          const res = await fetch('/learn-spec', { headers: this._authHeaders() });
          if (!res.ok) { this._err('소요서 생성 실패'); return; }
          const blob = await res.blob(); const a = document.createElement('a');
          a.href = URL.createObjectURL(blob); a.download = 'prism_finetune_spec.md'; a.click(); URL.revokeObjectURL(a.href);
        } catch (e) { this._err('소요서 생성 실패'); }
      },
      pctTxt(v) { return (v == null) ? '·' : (Math.round(v * 1000) / 10) + '%'; },
      // 카테고리 옵션(IAB Tier1 / Tier2) · 빈칸 채우기 피커용
      get categoryOptions() {
        const d = this.dictData; if (!d) { if (!this._dictReq) { this._dictReq = true; this.loadDict(); } return []; }
        const out = [];
        (d.iabTier1 || []).forEach((t1) => { out.push(t1); ((d.tier2 && d.tier2[t1]) || []).forEach((t2) => out.push(t1 + ' / ' + t2)); });
        return out;
      },
      async fillCategory(c, val) {
        if (!val || !c) return;
        const cats = [val];
        let r = null;
        try { r = await (await fetch('/patch-meta', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ hash: c.hash, patch: { content_category: cats }, reviewer: this.reviewer || '' }) })).json(); } catch (e) { this._err('분류 저장 실패'); return; }
        c.category = cats;                                         // 로컬 즉시 반영
        this.celebratePoints(5, '분류 채움');
        (r && r.missions_completed || []).forEach((m) => this.celebratePoints(m.bonus, '미션 달성 · ' + m.label));
        this.loadGoldenStatus();
      },
      async registerGolden(ev) {
        const f = ev.target.files && ev.target.files[0]; if (!f) return;
        this.goldenMsg = '등록 중…';
        const fd = new FormData(); fd.append('file', f); fd.append('merge', this.goldenMerge ? '1' : '0');
        const h = {}; if (this.authToken) h['Authorization'] = 'Bearer ' + this.authToken;
        try {
          const r = await (await fetch('/golden', { method: 'POST', headers: h, body: fd })).json();
          this.goldenMsg = r.ok ? ('✓ ' + (r.merged ? '병합' : '교체') + ' ' + r.count + '건' + (r.skipped ? (' · 검증 제외 ' + r.skipped + '건') : '')) : (r.error || '실패');
          this.loadAdmin(); this.loadGoldenList(); this.loadGoldenStatus();
        }
        catch (e) { this.goldenMsg = '오류'; }
        ev.target.value = '';
      },
      async adminAct(action, member) {
        if (action === 'clear_feedback' && !confirm('우리 팀의 평가 피드백을 모두 삭제할까요?')) return;
        if (action === 'clear_contents' && !confirm('우리 팀의 검토 콘텐츠를 모두 삭제할까요?')) return;
        if (action === 'clear_golden' && !confirm('정답셋(골든)을 모두 삭제할까요? 되돌릴 수 없습니다.')) return;
        if (action === 'delete_team') {
          if (!confirm('팀을 삭제할까요? 멤버 소속이 모두 해제됩니다.')) return;
          if (!confirm('정말 삭제합니다. 되돌릴 수 없습니다.')) return;
        }
        try { await fetch('/admin', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ action: action, member: member }) }); } catch (e) {}
        this.loadAdmin();
      },
      async createTeam() {
        const nm = (this.newTeamName || '').trim();
        if (!nm) return;
        try {
          const r = await (await fetch('/admin', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ action: 'create_team', name: nm }) })).json();
          if (!r || !r.ok) { this._err((r && r.error) || '팀 생성 실패'); return; }
          this.teamMsg = '팀 생성됨 · 초대코드 ' + (r.invite || ''); this.newTeamName = '';
          if (r.team && r.team.invite_code) this.myInvite = r.team.invite_code;
          this.loadAdmin();
        } catch (e) { this._err('팀 생성 실패'); }
      },
      copyInvite() { try { navigator.clipboard.writeText((this.adminData && this.adminData.team && this.adminData.team.invite_code) || ''); this.inviteCopied = true; setTimeout(() => { this.inviteCopied = false; }, 1500); } catch (e) {} },
      inviteCopied: false,
      ingestEndpoint: '', ingestN: 20, ingestMsg: '', ingestBusy: false,
      async ingestRun() {
        if (!(this.ingestEndpoint || '').trim()) { this.ingestMsg = '크롤러 엔드포인트를 입력하세요'; return; }
        this.ingestBusy = true; this.ingestMsg = '인입·추출 중…';
        try { const r = await (await fetch('/admin', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ action: 'ingest', endpoint: this.ingestEndpoint, n: this.ingestN }) })).json();
          this.ingestMsg = r.ok ? ('✓ ' + r.fetched + '건 인입 → 검수 대기 ' + r.queued + '건 적재') : (r.error || '실패'); } catch (e) { this.ingestMsg = '오류'; }
        this.ingestBusy = false;
      },
      // 결과 출처 필터(자동 인입/단건/배치)
      get srcOptions() { const s = new Set(((this.dashData && this.dashData.contents) || []).map((c) => c.source || '단건')); return [...s]; },
      get filteredContents() { const cs = (this.dashData && this.dashData.contents) || []; return this.srcFilter ? cs.filter((c) => (c.source || '단건') === this.srcFilter) : cs; },
      srcBadgeClass(s) { return s === '자동 인입' ? 'ds-badge--intent' : s === '배치' ? 'ds-badge--category' : 'ds-badge--neutral'; },
      // 아레나 파생값(게이지·내 순위)
      get arenaPct() { const d = this.arenaData; return d ? Math.round((d.accuracy || 0) * 100) : 0; },
      get arenaTargetPct() { const d = this.arenaData; return d ? Math.round((d.target || 0.9) * 100) : 90; },
      // 검수 진척율: 개인(내가 검수한 대상 비율) · 팀(팀원 평균)
      get myProgressPct() { const m = this.arenaMe; return m ? Math.round((m.progress || 0) * 100) : 0; },
      get teamProgressPct() { const d = this.arenaData; return d ? Math.round((d.team_progress || 0) * 100) : 0; },
      get reviewTargets() { const d = this.arenaData; return d ? (d.total_targets || 0) : 0; },
      get arenaMe() { const d = this.arenaData; if (!d || !this.reviewer) return null; return (d.leaderboard || []).find((r) => r.reviewer === this.reviewer) || null; },
      get arenaMyRank() { const d = this.arenaData; if (!d || !this.reviewer) return 0; const i = (d.leaderboard || []).findIndex((r) => r.reviewer === this.reviewer); return i < 0 ? 0 : i + 1; },
      rankMedal(i) { return ['🥇', '🥈', '🥉'][i] || ('#' + (i + 1)); },
      // 주간 리그(D-9): 이번 주 점수 순위 + 승급/강등 존 + 지난주 대비 이동
      weeklyLeague() {
        const d = this.arenaData; if (!d || !d.leaderboard) return [];
        const board = d.leaderboard.map((r) => ({ ...r, wp: r.week_points || 0, lwp: r.last_week_points || 0 }))
          .sort((a, b) => b.wp - a.wp);
        const n = board.length;
        const upN = Math.max(1, Math.ceil(n * 0.3));
        const downN = n >= 5 ? Math.max(1, Math.floor(n * 0.2)) : 0;
        return board.map((r, i) => ({ ...r, rank: i + 1, delta: r.wp - r.lwp,
          zone: r.wp <= 0 ? 'idle' : (i < upN ? 'up' : (downN && i >= n - downN ? 'down' : 'keep')) }));
      },
      leagueActive() { return this.weeklyLeague().filter((r) => r.wp > 0).length; },
      leagueZoneKr(z) { return { up: '승급권', down: '강등권', keep: '유지권', idle: '대기' }[z] || ''; },
      leagueZoneLabel(z) { return { up: '▲ 승급', down: '▼ 강등', keep: '유지', idle: '대기' }[z] || ''; },
      leagueZoneClass(z) { return { up: 'ds-badge--success', down: 'ds-badge--category', keep: 'ds-badge--neutral', idle: 'ds-badge--neutral' }[z] || 'ds-badge--neutral'; },
      // 캐릭터 육성: 레벨 → 성장 티어·타이틀·XP
      levelTier(L) { return L >= 10 ? 4 : L >= 7 ? 3 : L >= 4 ? 2 : L >= 2 ? 1 : 0; },
      levelTitle(L) { return ['새내기 검수자', '숙련 검수자', '베테랑 검수자', '검수 마스터', '전설의 검수자'][this.levelTier(L)]; },
      levelEmoji(L) { return ['🌱', '🔰', '⭐', '🏆', '👑'][this.levelTier(L)]; },
      // 게이미피케이션(KB 프레임워크 적용): Flow 단계 + 배지 컬렉션(성취) + 오늘의 미션(도전)
      flowStage(L) { return L >= 10 ? 'Master' : L >= 4 ? 'Regular' : 'Rookie'; },
      flowStageKr(L) { return L >= 10 ? '마스터' : L >= 4 ? '정착' : '입문'; },
      badges() {
        const m = this.arenaMe; const r = (m&&m.reviews)||0, c = (m&&m.corrections)||0, s = (m&&m.streak)||0, L = (m&&m.level)||0;
        // 배지 세트(14) · 5분류: 볼륨·스트릭·기여·품질·지위. 품질 배지는 개인 실측(골드 정확도·합의·불일치 해소)
        // 기반 = 유능감 정보 제공(Sailer 2017 · Ryan & Deci 2000). cur/target/unit = 진행도(모달 표시용).
        const gn = (m&&m.gold_n)||0, ga = (m&&m.gold_acc)||0, cm = (m&&m.consensus_matches)||0, sr = (m&&m.split_reviews)||0;
        const gap = Math.round(ga * 100);
        return [
          { icon: '🌱', label: '첫 검수', desc: '첫 검수를 완료했어요', exp: 10, color: '#18ba45', cat: '볼륨', cur: r, target: 1, unit: '검수', got: r >= 1 },
          { icon: '📖', label: '검수 50', desc: '누적 50건 검수', exp: 50, color: '#1e84ff', cat: '볼륨', cur: r, target: 50, unit: '검수', got: r >= 50 },
          { icon: '📚', label: '검수 100', desc: '누적 100건 검수', exp: 100, color: '#5c77ff', cat: '볼륨', cur: r, target: 100, unit: '검수', got: r >= 100 },
          { icon: '🏆', label: '검수 300', desc: '누적 300건 검수', exp: 250, color: '#f5a623', cat: '볼륨', cur: r, target: 300, unit: '검수', got: r >= 300 },
          { icon: '🔥', label: '연속 3일', desc: '3일 연속 검수', exp: 15, color: '#ff9429', cat: '스트릭', cur: s, target: 3, unit: '일', got: s >= 3 },
          { icon: '⚡', label: '연속 7일', desc: '7일 연속 검수', exp: 30, color: '#ff6a3d', cat: '스트릭', cur: s, target: 7, unit: '일', got: s >= 7 },
          { icon: '☄️', label: '연속 14일', desc: '14일 연속 검수', exp: 70, color: '#ff4e33', cat: '스트릭', cur: s, target: 14, unit: '일', got: s >= 14 },
          { icon: '🏅', label: '개선 채택', desc: '개선안이 채택됐어요', exp: 25, color: '#a05cff', cat: '기여', cur: c, target: 1, unit: '개선', got: c >= 1 },
          { icon: '🛠️', label: '개선 10', desc: '개선안 10건 채택', exp: 90, color: '#7c5cff', cat: '기여', cur: c, target: 10, unit: '개선', got: c >= 10 },
          { icon: '🎯', label: '골드 정확도 90%', desc: '골드 문항 10건 이상 · 정확도 90%', exp: 60, color: '#18ba45', cat: '품질', cur: gap, target: 90, unit: '%', got: gn >= 10 && ga >= 0.9 },
          { icon: '🤝', label: '합의 메이커', desc: '팀 합의와 일치한 판정 50건', exp: 60, color: '#1e84ff', cat: '품질', cur: cm, target: 50, unit: '건', got: cm >= 50 },
          { icon: '⚖️', label: '불일치 해결사', desc: '의견 갈린 콘텐츠 재검토 10건', exp: 60, color: '#a05cff', cat: '품질', cur: sr, target: 10, unit: '건', got: sr >= 10 },
          { icon: '💎', label: '골든 기여 10', desc: '내 검수가 골든(정답) 확정 10건에 기여', exp: 80, color: '#f5a623', cat: '품질', cur: (m&&m.golden_contribs)||0, target: 10, unit: '건', got: ((m&&m.golden_contribs)||0) >= 10 },
          { icon: '⭐', label: 'Lv.5', desc: '레벨 5 도달', exp: 50, color: '#ffb020', cat: '지위', cur: L, target: 5, unit: 'Lv', got: L >= 5 },
          { icon: '👑', label: '마스터', desc: '레벨 10 도달(마스터)', exp: 200, color: '#f5a623', cat: '지위', cur: L, target: 10, unit: 'Lv', got: L >= 10 },
        ];
      },
      badgeModalOpen: false,
      get badgeGot() { return this.badges().filter((x) => x.got).length; },
      async checkBadges() {
        if (!this.arenaMe) return;
        const key = 'prism_badges_' + (this.reviewer || '');
        // 기준선(이미 축하함): 서버 우선(_badgeSeen, 기기 간) → 없으면 로컬 폴백
        let localSeen = null;
        try { const raw = localStorage.getItem(key); if (raw) localSeen = JSON.parse(raw); } catch (e) {}
        const server = Array.isArray(this._badgeSeen) ? this._badgeSeen : null;
        const firstLoad = (server === null && localSeen === null);   // 최초 진입 = 기준선만
        const base = [].concat(server || [], localSeen || []);
        const got = this.badges().filter((x) => x.got).map((x) => x.label);
        const fresh = got.filter((l) => !base.includes(l));
        try { localStorage.setItem(key, JSON.stringify(got)); } catch (e) {}
        // 서버 영속(단조 증가) · 신규가 있거나 서버 기준선이 아직 없을 때
        if (this.reviewer && (fresh.length || server === null)) {
          try {
            const r = await (await fetch('/badges', { method: 'POST', headers: this._authHeaders(),
              body: JSON.stringify({ reviewer: this.reviewer, earned: got }) })).json();
            if (r && Array.isArray(r.badges)) this._badgeSeen = r.badges;
          } catch (e) {}
        }
        if (firstLoad) return;                            // 최초 기준선은 축하 생략(스팸 방지)
        if (fresh.length) { const bd = this.badges().find((x) => x.label === fresh[0]); if (bd) this.celebrateBadge(bd); }
      },
      celebrateBadge(bd) { this.badgeToast = bd; if (this._btT) clearTimeout(this._btT); this._btT = setTimeout(() => { this.badgeToast = null; }, 4500); },
      // 검수 완료 → 점수 상승 리워드(성취감). 연속 검수 시 key 로 애니메이션 재시작.
      celebratePoints(pts, label) {
        this._ptId = (this._ptId || 0) + 1;
        this.ptToast = { id: this._ptId, pts: pts, label: label || '' };
        const id = this._ptId;
        if (this._ptT) clearTimeout(this._ptT);
        this._ptT = setTimeout(() => { if (this.ptToast && this.ptToast.id === id) this.ptToast = null; }, 1700);
      },
      // 오늘의 미션: 서버 판정·보상(arenaData.missions). 아래 getter 는 미션 데이터 없을 때 폴백 안내.
      get missionList() { return (this.arenaData && this.arenaData.missions) || []; },
      get todayMission() {
        const q = (this.arenaData && this.arenaData.queue) || 0;
        if (q > 0) return { txt: '대기 ' + q + '건 비우기 🔥', to: 'review', cta: '검수하기' };
        return { txt: '일치율 점검', to: 'evaluate', cta: '평가' };
      },
      xpPct(r) { return r ? (r.points % 100) : 0; },                 // 레벨당 100pt
      xpToNext(r) { return r ? (r.level * 100 - r.points) : 0; },
      async queueFeedback(it, verdict) {
        if (!this.ensureReviewer()) return;
        it.note = it.note || '';
        const wasReviewed = !!it.myVerdict;
        const r = await this._postFb({ hash: it.hash, service: it.service, title: it.title, model: it.model || '', verdict: verdict, stage: 'review', note: it.note });
        if (r && r.gold) {                              // 골드 문항: 응답 후 정오답 공개(즉시 학습 피드백)
          it.reviewed = true; it.myVerdict = verdict; it.goldRevealed = true; it.goldCorrect = !!r.gold.correct;
          if (r.gold.correct) this.celebratePoints(10, '골드 문항 정답');
          else this.liveToast('골드 문항 · 정답과 달랐어요(품질 점수에 반영)');
          return;
        }
        if (r && r.error) { this._err(r.error); return; }
        if (!wasReviewed) this.celebratePoints((verdict === 'bad' && (it.note || '').trim()) ? 25 : 10, '검수 완료');
        it.reviewed = true; it.myVerdict = verdict;
        if (this.queueOnlyUnreviewed && !it.split) this.queueData.items = (this.queueData.items || []).filter((x) => x.hash !== it.hash);
      },
      ensureReviewer() { if (!(this.reviewer || '').trim()) { this.reviewerEditing = true; return false; } return true; },
      notifyViewing(it) { try { fetch('/presence', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reviewer: this.reviewer, hash: it.hash, action: 'viewing' }) }); } catch (e) {} },
      async clearFeedback() {
        if (!confirm('누적된 평가 피드백과 학습 보정을 모두 초기화할까요?')) return;
        try { await fetch('/feedback', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ clear: true }) }); } catch (e) {}
        this.loadDash();
      },
      learnedStages: { extract: false, analyze: false, review: false, judge: false },
      async loadPromptDefaults() { try { await this.refreshConfig(); const d = await (await fetch('/prompt-defaults')).json(); this.promptDefaults = d.defaults || d; this.learnedStages = d.learned || this.learnedStages; ['extract','analyze','review','judge'].forEach((k)=>{ this.stagePrompts[k] = this.promptFor(k); }); } catch (e) {} },
      restoreDefault(stage) { this.stagePrompts[stage] = this.promptDefaults[stage] || ''; },
      async loadTopics() { this.modBusy = true; try { this.topicData = await (await fetch('/topics')).json(); } catch (e) {} this.modBusy = false; },
      async loadDict() { this.modBusy = true; try { this.dictData = await (await fetch('/dict')).json(); if (!this.dictGroup) this.dictGroup = (this.dictData.serviceGroups || [])[0] || ''; } catch (e) {} this.modBusy = false; },
      // 사전·정책 편집(사용자 직접 수정)
      editT: null, editKey: null, editKind: 'list', editVal: '', editTitle: '', editMsg: '', editExtra: '', editFilter: '',
      startEdit(target, key, value, kind, title) {
        this.editT = target; this.editKey = key; this.editKind = kind || 'list'; this.editTitle = title || target; this.editMsg = ''; this.editExtra = ''; this.editFilter = '';
        this.editVal = (kind === 'text') ? (value || '') : (Array.isArray(value) ? value.join('\\n') : '');
      },
      startEditLegal(code, v) { this.startEdit('legal_types', code, (v && v.label) || '', 'text', '법령 · ' + code); this.editExtra = (v && v.article) || ''; },
      startEditIntake(type, row) { this.startEdit('intake_policy', type, (row && row.method) || '', 'intake', '인입 정책 · ' + type); this.editFilter = (row && row.filter) || ''; this.editExtra = (row && row.status) || ''; },
      cancelEdit() { this.editT = null; this.editMsg = ''; },
      async saveEdit() {
        let value = this.editKind === 'text' || this.editKind === 'intake' ? this.editVal : this.editVal.split('\\n').map(s => s.trim()).filter(Boolean);
        if (this.editT === 'legal_types') value = { label: this.editVal, article: this.editExtra };
        if (this.editT === 'intake_policy') value = { filter: this.editFilter, method: this.editVal, status: this.editExtra };
        const body = { target: this.editT, value }; if (this.editKey != null) body.key = this.editKey;
        this.editMsg = '저장 중…';
        try {
          const r = await fetch('/dict', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
          const d = await r.json();
          if (d.error) { this.editMsg = '오류: ' + d.error; return; }
          this.dictData = d; this.editT = null;
        } catch (e) { this.editMsg = '오류: ' + e; }
      },
      async resetDict() {
        if (!confirm('사전 편집을 모두 초기화할까요? (베이스 사전은 재시작 시 완전 복원)')) return;
        try { const r = await fetch('/dict', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reset: true }) }); this.dictData = await r.json(); } catch (e) {}
      },
      async loadUser() { this.modBusy = true; try { this.userData = await (await fetch('/usermeta')).json(); } catch (e) {} this.modBusy = false; },
      async uploadUserLog(e) {
        const f = e.target.files[0]; e.target.value = ''; if (!f) return;
        this.modBusy = true;
        try { const fd = new FormData(); fd.append('file', f);
          this.userData = await (await fetch('/usermeta', { method: 'POST', body: fd })).json(); }
        catch (err) {} this.modBusy = false;
      },
      get qm() { return (this.result && this.result.output && this.result.output.quality_meta) || {}; },
      get lm() { return (this.result && this.result.output && this.result.output.legal_meta) || {}; },
      get tr() { return (this.result && this.result.output && this.result.output.trace) || {}; },
      routerKeyPresent(p) { return p === 'bizrouter' ? !!this.cfg.hasBizKey : p === 'timely' ? !!this.cfg.hasTimelyKey : false; },
      isRouter(p) { return p === 'bizrouter' || p === 'timely'; },
      providerHasKey(p) { return p === 'solar' || p === 'upstage_ie' ? !!this.cfg.hasKey : this.routerKeyPresent(p); },
      get textReady() { return this.isRouter(this.textProvider) ? this.routerKeyPresent(this.textProvider) : !!this.cfg.hasKey; },
      // ── 모델 선택(Atelier 방식): 소스별 그룹 + 연결된 제공자만 활성 ──
      get textGroups() {
        return [
          { label: '직접 · Solar', on: !!this.cfg.hasKey, items: this.modelOptions.map((m) => ({ provider: 'solar', model: m })) },
          { label: '통합 · Timely', on: !!this.cfg.hasTimelyKey, items: this.modelCatalog.timely.text.map((m) => ({ provider: 'timely', model: m })) },
          { label: '통합 · BizRouter', on: !!this.cfg.hasBizKey, items: this.modelCatalog.bizrouter.text.map((m) => ({ provider: 'bizrouter', model: m })) },
        ];
      },
      get visionGroups() {
        return [
          { label: '직접 · Upstage', on: !!this.cfg.hasKey, items: [{ provider: 'upstage_ie', model: '', label: 'Information Extraction (텍스트형 이미지)' }] },
          { label: '통합 · Timely', on: !!this.cfg.hasTimelyKey, items: this.modelCatalog.timely.vision.map((m) => ({ provider: 'timely', model: m })) },
          { label: '통합 · BizRouter', on: !!this.cfg.hasBizKey, items: this.modelCatalog.bizrouter.vision.map((m) => ({ provider: 'bizrouter', model: m })) },
        ];
      },
      get textValue() { return this.textProvider === 'solar' ? ('solar|' + (this.cfgModel || '')) : (this.textProvider + '|' + (this.textModel || '')); },
      get visionValue() { return this.visionProvider === 'upstage_ie' ? 'upstage_ie|' : (this.visionProvider + '|' + (this.visionModel || '')); },
      onTextPick(v) {
        const i = v.indexOf('|'); const p = v.slice(0, i), m = v.slice(i + 1);
        this.textProvider = p; if (p === 'solar') this.cfgModel = m; else this.textModel = m;
        this.saveTextSlot();
      },
      onVisionPick(v) {
        const i = v.indexOf('|'); const p = v.slice(0, i), m = v.slice(i + 1);
        this.visionProvider = p; this.visionModel = p === 'upstage_ie' ? '' : m;
        this.saveVisionSlot();
      },
      optVal(provider, model) { return provider + '|' + model; },
      // ── 이미지 입력: 선택·드롭·붙여넣기·썸네일 ──
      get fileLabel() { return this.imgFiles.length ? (this.imgFiles.length + '개 선택됨') : '선택된 파일 없음'; },
      get excelLabel() { return this.excelFile ? this.excelFile.name : '선택된 파일 없음'; },
      addImages(list) {
        const imgs = Array.from(list || []).filter((f) => f.type.startsWith('image/'));
        if (!imgs.length) return;
        this.imgFiles = this.imgFiles.concat(imgs);
        this._rebuildThumbs();
        this.status = '';
      },
      _rebuildThumbs() {
        this.imgThumbs.forEach((u) => URL.revokeObjectURL(u));
        this.imgThumbs = this.imgFiles.map((f) => URL.createObjectURL(f));
      },
      onFiles(e) { this.addImages(e.target.files); e.target.value = ''; },
      onDropImages(e) { this.imgDrag = false; this.addImages(e.dataTransfer.files); },
      onPasteImages(e) {
        const items = (e.clipboardData && e.clipboardData.items) || [];
        const fs = [];
        for (const it of items) { if (it.kind === 'file') { const f = it.getAsFile(); if (f) fs.push(f); } }
        if (fs.length) { e.preventDefault(); this.addImages(fs); }
      },
      removeImage(i) {
        URL.revokeObjectURL(this.imgThumbs[i]);
        this.imgFiles.splice(i, 1); this.imgThumbs.splice(i, 1);
      },
      clearImages() { this.imgThumbs.forEach((u) => URL.revokeObjectURL(u)); this.imgFiles = []; this.imgThumbs = []; },
      sizeLabel(b) { if (!b) return ''; if (b < 1024) return b + 'B'; if (b < 1048576) return (b / 1024).toFixed(0) + 'KB'; return (b / 1048576).toFixed(1) + 'MB'; },
      onExcel(e) { this.excelFile = e.target.files[0] || null; e.target.value = ''; this.status = ''; },
      onDropExcel(e) { this.xlsDrag = false; const f = e.dataTransfer.files[0]; if (f) this.excelFile = f; },
      clearExcel() { this.excelFile = null; },

      // ── 결과 복사 / 내보내기 ──
      async copyText(t, label) {
        try { await navigator.clipboard.writeText(t || ''); this.flashCopy((label || '복사') + ' 됨'); }
        catch (e) { this.flashCopy('복사 실패'); }
      },
      copyJSON() { this.copyText(JSON.stringify(this.result ? this.result.output : {}, null, 2), 'JSON'); },
      flashCopy(m) { this.copyMsg = m; clearTimeout(this._cpT); this._cpT = setTimeout(() => { this.copyMsg = ''; }, 1600); },
      exportBatchCsv() {
        const its = (this.batchResult && this.batchResult.items) || [];
        const esc = (v) => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
        const rows = [['제목', '리드문', '엔티티', '인텐트', '등급']];
        for (const it of its) rows.push([it.title, it.summary, (it.entities || []).join(' · '), (it.intent || []).join(' · '), it.grade]);
        const csv = '\\ufeff' + rows.map((r) => r.map(esc).join(',')).join('\\r\\n');
        const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
        const a = document.createElement('a'); a.href = url; a.download = 'prism_results.csv';
        document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
      },
      get modelOptions() {
        const a = this.models.slice();
        if (this.cfgModel && !a.includes(this.cfgModel)) a.unshift(this.cfgModel);
        return a;
      },
      async loadModels() {
        this.modelsMsg = '불러오는 중…'; this.cfgBusy = true;
        try {
          if (this.keyInputs.solar) {              // 입력한 키를 먼저 적용(세션)
            await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ api_key: this.keyInputs.solar }) });
            await this.refreshConfig();
          }
          const j = await (await fetch('/models')).json();
          if (j.ok) {
            this.models = j.models;
            if (!this.cfgModel || !this.models.includes(this.cfgModel))
              this.cfgModel = this.cfg.model || this.models[0] || '';
            this.modelsMsg = this.models.length + '개 모델 불러옴';
          } else { this.modelsMsg = '실패 · ' + j.detail; }
        } catch (e) { this.modelsMsg = '오류: ' + e; }
        this.cfgBusy = false;
      },
      async refreshConfig() {
        try {
          const r = await fetch('/config'); this.cfg = await r.json();
          if (this.cfg.backend) this.backend = this.cfg.backend;     // sqlite | supabase
          if (this.backend === 'supabase' && this.authToken) this.loadAdmin();  // 관리자 여부 → nav 게이팅(자동인입 등)
          if (!this.cfgModel) this.cfgModel = this.cfg.model;
          if (this.cfg.reasoning) this.reasoning = this.cfg.reasoning;
          if (typeof this.cfg.systemPrompt === 'string') this.systemPrompt = this.cfg.systemPrompt;
          if (this.cfg.stagePrompts) { ['extract','analyze','review','judge'].forEach((k)=>{ this.stagePrompts[k] = this.cfg.stagePrompts[k] || ''; }); if (!this.stagePrompts.analyze) this.stagePrompts.analyze = this.cfg.systemPrompt || ''; }
          if (this.cfg.stagePromptsMeta) this.stagePromptsMeta = this.cfg.stagePromptsMeta;
          if (this.cfg.stageModels) this.stageModels = Object.assign({ extract:'', analyze:'', review:'', judge:'' }, this.cfg.stageModels);
          if (this.cfg.modelPrompts) this.modelPrompts = this.cfg.modelPrompts;
          if (Array.isArray(this.cfg.availableModels)) this.availableModels = this.cfg.availableModels;
          if (!this.cmpA && this.availableModels.length) { this.cmpA = this.availableModels[0]; this.cmpB = this.availableModels[1] || ''; }   // A/B 기본 슬롯
          if (Array.isArray(this.cfg.ingestSources)) this.ingestSources = this.cfg.ingestSources.slice();
          if (this.cfg.textProvider) this.textProvider = this.cfg.textProvider;
          if (typeof this.cfg.textModel === 'string' && this.cfg.textModel) this.textModel = this.cfg.textModel;
          if (this.cfg.visionProvider) this.visionProvider = this.cfg.visionProvider;
          if (typeof this.cfg.visionModel === 'string' && this.cfg.visionModel) this.visionModel = this.cfg.visionModel;
          if (typeof this.cfg.legalEnabled === 'boolean') this.legalEnabled = this.cfg.legalEnabled;
        } catch (e) { /* noop */ }
      },
      async toggleLegal() {
        try { await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ legal_enabled: this.legalEnabled }) }); } catch (e) {}
      },
      // ── 키(서비스별) ──
      keyState(service) { return !!this.cfg[this.keyDefs[service].has]; },
      keyPersisted(service) { return !!this.cfg[this.keyDefs[service].persisted]; },
      async saveKey(service) {
        this.keyMsgs[service] = '저장 중…'; this.cfgBusy = true;
        try {
          const body = { persist: this.cfgPersist };
          if (service === 'solar') { body.api_key = this.keyInputs.solar; if (this.cfgModel) body.model = this.cfgModel; }
          else body[service + '_api_key'] = this.keyInputs[service];
          const r = await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
          this.cfg = await r.json(); this.keyInputs[service] = '';
        } catch (e) { this.keyMsgs[service] = '오류: ' + e; this.cfgBusy = false; return; }
        if (service === 'solar' && this.keyState('solar')) {
          if (!this.models.length) this.loadModels();
          await this.testConn();                                  // Solar 는 즉시 연결 검증
        } else { this.keyMsgs[service] = this.keyState(service) ? '✓ 저장됨' : '저장 실패'; this.cfgBusy = false; }
      },
      async forgetKey(service) {
        try {
          const body = {}; if (service === 'solar') body.forget = true; else body['forget_' + service] = true;
          const r = await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
          this.cfg = await r.json(); this.keyMsgs[service] = '키 삭제됨';
        } catch (e) { this.keyMsgs[service] = '오류: ' + e; }
      },
      async testConn() {
        this.cfgBusy = true; this.keyMsgs.solar = '연결 테스트 중…';
        try { const j = await (await fetch('/ping', { method: 'POST' })).json();
          this.keyMsgs.solar = (j.ok ? '✓ 성공 · ' : '✗ 실패 · ') + j.detail; }
        catch (e) { this.keyMsgs.solar = '오류: ' + e; }
        await this.refreshConfig(); this.cfgBusy = false;
      },
      // 텍스트 슬롯(메타 생성)
      async saveTextSlot() {
        this.slotMsg = '저장 중…';
        const payload = { text_provider: this.textProvider };
        if (this.isRouter(this.textProvider)) payload.text_model = this.textModel;
        else if (this.cfgModel) payload.model = this.cfgModel;
        try { const r = await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload) }); this.cfg = await r.json(); this.slotMsg = '✓ 적용됨'; }
        catch (e) { this.slotMsg = '오류: ' + e; }
      },
      // 비전 슬롯(이미지 맥락 생성)
      async saveVisionSlot() {
        this.slotMsg = '저장 중…';
        try { const r = await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ vision_provider: this.visionProvider, vision_model: this.visionModel }) });
          this.cfg = await r.json(); this.slotMsg = '✓ 적용됨'; }
        catch (e) { this.slotMsg = '오류: ' + e; }
      },
      async applyPrefs() {
        this.prefMsg = '저장 중…';
        try {
          await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reasoning: this.reasoning, system_prompt: this.systemPrompt }) });
          this.prefMsg = '✓ 적용됨';
        } catch (e) { this.prefMsg = '오류: ' + e; }
      },
      setReasoning(id) { this.reasoning = id; fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reasoning: id }) }).catch(() => {}); },
      async clearStore() {
        if (!confirm('적재된 추출 결과를 모두 삭제할까요? (되돌릴 수 없음)')) return;
        try { const r = await fetch('/store', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ clear: true }) });
          const d = await r.json(); this.cfg.storedCount = d.count || 0; this.loadDash(); } catch (e) {}
      },
      async applyStagePrompts() {
        this.prefMsg = '저장 중…';
        try {
          await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stage_prompts: this.stagePrompts, reasoning: this.reasoning }) });
          await this.refreshConfig(); this.prefMsg = '✓ 전체 적용됨';
        } catch (e) { this.prefMsg = '오류: ' + e; }
      },
      // 단계별 모델 + 모델별 프롬프트 ──────────────
      promptFor(stage) {
        const m = (this.stageModels[stage] || '').trim();
        const byModel = (m && this.modelPrompts[m] && this.modelPrompts[m][stage]) || '';
        return byModel || this.promptDefaults[stage] || '';
      },
      onStageModelChange(stage) { this.stagePrompts[stage] = this.promptFor(stage); },
      async saveStage(stage) {
        this.stageMsg[stage] = '저장 중…';
        const m = (this.stageModels[stage] || '').trim();
        try {
          const payload = { stage: stage, stage_models: this.stageModels };
          if (m) payload.model_prompts = { [m]: { [stage]: this.stagePrompts[stage] } };
          else payload.stage_prompts = this.stagePrompts;     // 모델 미지정 → 전역 폴백
          await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
          await this.refreshConfig(); this.stagePrompts[stage] = this.promptFor(stage); this.stageMsg[stage] = '✓ 저장됨';
          clearTimeout(this._stT); this._stT = setTimeout(() => { this.stageMsg[stage] = ''; }, 1800);
        } catch (e) { this.stageMsg[stage] = '오류: ' + e; }
      },

      // ── 자동 인입 파이프라인 소스(API/Kafka) ──
      ingestSources: [], ingestMsg: '',
      newSrc: { type: 'api', name: '', endpoint: '', method: 'GET', auth: '', brokers: '', topic: '', group: '', interval: '60' },
      resetNewSrc() { this.newSrc = { type: 'api', name: '', endpoint: '', method: 'GET', auth: '', brokers: '', topic: '', group: '', interval: '60' }; },
      addSource() {
        if (!this.newSrc.name.trim()) { this.ingestMsg = '이름을 입력하세요'; return; }
        this.ingestSources.push(Object.assign({ id: 'src-' + Date.now(), enabled: true }, this.newSrc));
        this.resetNewSrc(); this.saveIngest();
      },
      removeSource(id) { this.ingestSources = this.ingestSources.filter((s) => s.id !== id); this.saveIngest(); },
      toggleSource(s) { s.enabled = !s.enabled; this.saveIngest(); },
      ingestBusy: {}, ingestRunMsg: {}, ingestJobs: [], _ingestPoll: null,
      async ingestNow(s) {
        this.ingestBusy[s.id] = true; this.ingestRunMsg[s.id] = '';
        this.pollIngestStatus();                         // 진행률 폴링 시작
        try {
          const r = await (await fetch('/ingest-run', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: s.id, name: s.name, endpoint: s.endpoint, method: s.method || 'GET', auth: s.auth || '', limit: 100 }) })).json();
          if (!r.ok) { this.ingestRunMsg[s.id] = '오류: ' + (r.error || '실패') + (r.headers ? (' / 헤더: ' + r.headers.join(', ')) : ''); }
          else { this.ingestRunMsg[s.id] = `✓ ${r.fetched}건 수신 → 신규 ${r.inserted} · 갱신 ${r.updated} · 제외 ${r.skipped}` + (r.mock ? ' (mock)' : ''); this.loadDash(); }
        } catch (e) { this.ingestRunMsg[s.id] = '오류: ' + e; }
        this.ingestBusy[s.id] = false;
      },
      // 자동 인입 상태(진행률) 폴링 · 실행 큐/자동 인입 뷰에서 사용
      async fetchIngestStatus() { try { const d = await (await fetch('/ingest-status')).json(); this.ingestJobs = d.jobs || []; if (d.running) this.loadDashThrottled(); return d; } catch (e) { return { jobs: [], running: false }; } },
      pollIngestStatus() {
        if (this._ingestPoll) return;
        const tick = async () => { const d = await this.fetchIngestStatus(); if (!d.running) { clearInterval(this._ingestPoll); this._ingestPoll = null; this.loadDash(); } };
        this._ingestPoll = setInterval(tick, 1500); tick();
      },
      loadDashThrottled() { const now = Date.now(); if (now - (this._lastDash || 0) > 4000) { this._lastDash = now; this.loadDash(); } },
      get runningJobs() { return (this.ingestJobs || []).filter((j) => j.running); },
      get runningCount() { return (this.loading ? 1 : 0) + this.runningJobs.length; },
      srcJob(s) { return (this.ingestJobs || []).find((j) => j.id === s.id); },
      srcRunning(s) { const j = this.srcJob(s); return !!(this.ingestBusy[s.id] || (j && j.running)); },
      async saveIngest() {
        this.ingestMsg = '저장 중…';
        try { await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ingest_sources: this.ingestSources }) }); this.ingestMsg = '✓ 저장됨'; clearTimeout(this._inT); this._inT = setTimeout(() => { this.ingestMsg = ''; }, 1600); } catch (e) { this.ingestMsg = '오류: ' + e; }
      },
      // ── 현황 결과 엑셀(CSV) 다운로드 ──
      _dl(name, rows) {
        const esc = (v) => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
        const csv = '\\ufeff' + rows.map((r) => r.map(esc).join(',')).join('\\r\\n');
        const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
        a.download = name; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
      },
      exportDash() { window.open('/export.csv', '_blank'); },
      exportTopics() {
        const d = this.topicData || {}; const rows = [['유형', '클러스터', '대표 엔티티', '멤버']];
        (d.single || []).forEach((t) => rows.push(['엔티티형', t.cluster_id, (t.entities || t.rep_entities || []).join(' · '), t.n_contents || '']));
        (d.composite || []).forEach((t) => rows.push(['사건형', t.cluster_id, (t.rep_entities || t.entities || []).join(' · '), t.n_contents || '']));
        (d.filter || []).forEach((t) => rows.push(['조건형', t.cluster_id, (t.name || t.label || ''), t.n_contents || '']));
        this._dl('prism_topics.csv', rows);
      },
      exportUsers() {
        const us = (this.userData && this.userData.users) || []; const rows = [['user_id', '페르소나', '조회', '클릭률', '평균체류', '소비형태', '선호엔티티']];
        us.forEach((u) => rows.push([u.user_id, u.persona, u.engagement.views, u.engagement.click_rate, u.engagement.avg_dwell_sec, Object.entries(u.form).map((e) => e[0] + ':' + e[1]).join(' · '), (u.affinity_entities || []).map((e) => e[0]).join(' · ')]));
        this._dl('prism_users.csv', rows);
      },
      exportEval() {
        const t = this.tr || {}; const rows = [['항목', '값'], ['prompt_version', t.prompt_version || ''], ['fallbacks', (t.fallbacks || []).join(' · ')], ['cost_usd', t.cost_usd || 0], ['tokens', JSON.stringify(t.tokens || {})], ['latency_ms', JSON.stringify(t.latency_ms || {})]];
        this._dl('prism_eval.csv', rows);
      },
      selectTab(id) { this.activeTabId = id; this.status = ''; },

      // DNM 메타 체계(13. 프로젝트 기획 / 1312. 아이템 메타) 기준 item_meta 필드:
      //   summary(리드문) · entities(엔티티) · intent(인텐트) · content_category(콘텐츠 카테고리)
      get im() { return (this.result && this.result.output.item_meta) || {}; },
      get q() { return (this.result && this.result.output.quality_meta) || {}; },
      get contentCats() {
        // 1312: 콘텐츠 단위 카테고리 N개(복수 매핑) → 리스트 그대로
        return this.im.content_category || [];
      },

      // 엑셀 배치 인포그래픽: 총건·등급분포·인텐트 상위·평균 리드문 길이
      get batchStats() {
        const its = (this.batchResult && this.batchResult.items) || [];
        const n = its.length;
        const g = its.filter((x) => x.grade === 'G').length;
        const counts = {};
        let lenSum = 0, lenN = 0;
        for (const x of its) {
          for (const t of (x.intent || [])) counts[t] = (counts[t] || 0) + 1;
          if (x.summary) { lenSum += x.summary.length; lenN += 1; }
        }
        const top = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 5)
          .map(([k, v]) => ({ k, v, pct: n ? Math.round((v / n) * 100) : 0 }));
        return {
          n, g, r: n - g,
          gPct: n ? Math.round((g / n) * 100) : 0,
          ents: its.reduce((s, x) => s + ((x.entities || []).length), 0),
          avgLen: lenN ? Math.round(lenSum / lenN) : 0,
          intents: top,
        };
      },

      async run() {
        this.loading = true; this.status = ''; this.result = null; this.batchResult = null;
        const fd = new FormData();
        fd.append('purpose', this.addPurpose || 'review');   // 추가 용도(STEP 1 선택)
        let endpoint = '/run';
        if (this.activeTabId === 'image') {
          if (!this.imgFiles.length) { this.status = '이미지를 선택하세요'; this.loading = false; return; }
          this.imgFiles.forEach((f, i) => fd.append('image' + i, f));
          fd.append('displayServiceName', this.group);
          fd.append('title', this.imgTitle);
          fd.append('caption', this.imgCaption);
        } else if (this.activeTabId === 'excel') {
          if (!this.excelFile) { this.status = '엑셀/CSV 파일을 선택하세요'; this.loading = false; return; }
          fd.append('file', this.excelFile); endpoint = '/run-batch';
        } else {
          fd.append('displayServiceName', this.group);
          fd.append('title', this.txtTitle);
          fd.append('body', this.txtBody);
        }
        try {
          const j = await (await fetch(endpoint, { method: 'POST', body: fd })).json();
          if (j.error) { this.status = '오류: ' + j.error; }
          else if (j.source === 'excel') { this.batchResult = j; }
          else { this.result = j; }
        } catch (e) { this.status = '오류: ' + e; }
        finally { this.loading = false; }
      },
    }));
  });
</script>
<script defer src="/vendor/alpine.js"></script>
<style>
  [x-cloak]{display:none!important}
  *{-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
  :root{
    --ds-font:"Pretendard Variable",Pretendard,system-ui,-apple-system,sans-serif;
    --ds-font-game:'GmarketSans',"Pretendard Variable",Pretendard,system-ui,sans-serif;  /* 게임형 디스플레이 */
    --ctrl-h:42px; --ctrl-r:10px; --ctrl-px:12px;
  }
  [data-theme]{--ds-font-sans:var(--ds-font);--ds-font-body:var(--ds-font);--ds-font-display:var(--ds-font-game)}
  /* 게임형 디스플레이 폰트(GmarketSans) 적용: 제목·큰 숫자·레벨·점수 + 사이드바·타이틀·탭 등 UI 크롬. 본문·표·입력은 Pretendard 유지 */
  .panel-hd>.ds-widget__title,.panel-hd b,.ds-widget__title,.homehead__title,.homehead__sub,
  .arena-hero__big,.arena-hero__eyebrow,.ds-stat__value,.tile .n,.goldbig__v,.goldstat b,
  .lb-pts,.lb-name>span:first-child,.charcard__lvl,.charcard__title,.charcard__xptxt b,
  .badgeburst__label,.badgeburst__ttl,.gbadge__exp,.badgeburst__exp,.side-profile__score,.side-profile__gain,.side-profile__name,
  .topbar__wm,.ds-navitem,.ds-navgroup__label,.evaltabs button,.seg button,.verdictbtn,
  .ds-btn,.copybtn,.fbbtn,.srcfilter__chip,.ds-segmented__item,.ds-badge,
  .panel-hd .meta,.panel-hd>.ds-widget__actions,.ds-widget__head>.ds-widget__actions,.ds-widget__kind,
  .tnum{font-family:var(--ds-font-display)}
  .topbar__wm small{font-family:var(--ds-font)}   /* 영문 태그라인은 Pretendard 유지 */
  .codeblock textarea,.field,input,textarea,select{font-family:var(--ds-font)}   /* 입력·코드블록은 가독성 위해 Pretendard/모노 유지 */
  .codeblock textarea{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"D2Coding",monospace}
  .homehead__title{font-family:var(--ds-font-display)}   /* 3192 라인 재정의 방지(뒤에서 승리) */
  html{scroll-behavior:smooth}
  /* 문서를 뷰포트에 고정 → 스크롤은 .appbody 안에서만 상단 바(.topbar)는 절대 안 따라옴 */
  html,body{height:100%;overflow:hidden;overscroll-behavior:none}
  body{font-family:var(--ds-font);font-size:14px;line-height:1.5;letter-spacing:-.003em;color:var(--ds-ink);
    background:var(--ds-canvas);word-break:keep-all;overflow-wrap:break-word}
  ::selection{background:var(--ds-primary);color:#fff}
  ::-webkit-scrollbar{width:11px;height:11px}
  ::-webkit-scrollbar-thumb{background:rgba(0,0,0,.14);border-radius:8px;border:3px solid transparent;background-clip:content-box}
  ::-webkit-scrollbar-thumb:hover{background:rgba(0,0,0,.26);background-clip:content-box}
  /* ── 스크롤 위계 ── 메인(.home, 페이지 스크롤)은 크고 흰 트랙 채널 + 틸 thumb 로 강조,
     패널 내부 스크롤은 얇고 옅게 → 중첩 스크롤 페이지(예: 사전·정책 관리)에서 헷갈리지 않게 구분 */
  .home{scrollbar-width:auto;scrollbar-color:rgba(30,132,255,.42) var(--ds-surface-white)}
  .home::-webkit-scrollbar{width:15px}
  .home::-webkit-scrollbar-track{background:var(--ds-surface-white);border-left:1px solid var(--ds-hairline);border-radius:0}
  .home::-webkit-scrollbar-thumb{background:rgba(30,132,255,.38);border-radius:9px;border:3px solid var(--ds-surface-white);background-clip:content-box}
  .home::-webkit-scrollbar-thumb:hover{background:rgba(30,132,255,.6);background-clip:content-box}
  /* 패널 내부(표·코드 등) 스크롤: 얇고 옅은 회색, 트랙 없음 */
  .panel .overflow-auto,.panel pre{scrollbar-width:thin;scrollbar-color:rgba(0,0,0,.2) transparent}
  .panel .overflow-auto::-webkit-scrollbar,.panel pre::-webkit-scrollbar{width:8px;height:8px}
  .panel .overflow-auto::-webkit-scrollbar-track,.panel pre::-webkit-scrollbar-track{background:transparent}
  .panel .overflow-auto::-webkit-scrollbar-thumb,.panel pre::-webkit-scrollbar-thumb{background:rgba(0,0,0,.18);border-radius:6px;border:2px solid transparent;background-clip:content-box}
  .panel .overflow-auto::-webkit-scrollbar-thumb:hover,.panel pre::-webkit-scrollbar-thumb:hover{background:rgba(0,0,0,.32);background-clip:content-box}
  /* 내부 스크롤 영역을 컴포넌트로 구분 · 1px 라인 프레임(그라데이션 미사용) pre(코드)는 자체 테두리 있어 제외 */
  .panel div.overflow-auto{border:1px solid var(--ds-hairline);border-radius:10px;background:var(--ds-surface)}
  /* 텍스트 박스(A안) · 값·설명 텍스트를 옅은 테두리 박스로 통일 전체 텍스트가 잘림 없이, 문장부호 단위로 줄넘김 */
  .tbox{display:block;border:1px solid var(--ds-hairline);border-radius:8px;background:var(--ds-surface-white);
    padding:6px 9px;color:var(--ds-body);font-size:12.5px;line-height:1.5;word-break:keep-all;overflow-wrap:break-word}
  .tbox .nm{display:block;font-weight:600;color:var(--ds-ink);margin-bottom:1px}
  .ds-table td{vertical-align:top}
  /* 패널 내부 표 = 카드 안쪽에 '독립된 둥근 표 블록'으로 분리(카드 레이어와 겹침 방지).
     사방 여백 + 자체 테두리·라운드 + overflow 클립으로 표 선이 카드 모서리에 닿지 않게. */
  .panel .overflow-auto:has(> .ds-table){margin:2px 16px 16px;border:1px solid var(--ds-hairline);
    border-radius:12px;overflow:auto;background:var(--ds-surface-white)}
  .panel .overflow-auto:has(> .ds-table) .ds-table th{border-top:0}
  .panel .overflow-auto:has(> .ds-table) .ds-table tbody tr:last-child td{border-bottom:0}
  .panel .ds-table th:first-child,.panel .ds-table td:first-child{padding-left:14px}
  .panel .ds-table th:last-child,.panel .ds-table td:last-child{padding-right:12px}
  .panel .ds-table tbody tr{border-radius:8px}
  /* 표 안에서는 박스 테두리 제거(행 구분선과 이중선으로 겹쳐 보임) → 옅은 배경만으로 값 구분 */
  .ds-table td .tbox{margin:0;border:0;background:var(--ds-surface);border-radius:7px;padding:6px 10px}
  /* 상단 자유배치 안내문도 텍스트 영역 · 박스로 감싸 일관화 */
  .hintbox{border:1px solid var(--ds-hairline);border-radius:10px;background:var(--ds-surface-white);
    padding:11px 15px;margin:0 0 2px;color:var(--ds-muted);font-size:12.5px;line-height:1.55;
    word-break:keep-all;overflow-wrap:break-word}
  /* 배치 결과 · 콘텐츠별 평가 피드백(학습 루프) */
  .fbrow{display:grid;grid-template-columns:1fr auto;gap:9px 12px;align-items:start;padding:11px 13px;
    border:1px solid var(--ds-hairline-soft);border-radius:12px;background:var(--ds-surface-white);margin-bottom:8px}
  .fbrow__main{min-width:0}
  .fbrow__title{display:flex;align-items:center;gap:7px;font-size:13px;font-weight:600;color:var(--ds-ink);flex-wrap:wrap}
  .ds-table tr.is-sel td{background:var(--ds-primary-tint)}
  .fbrow__titlelink{cursor:pointer;border-radius:4px;transition:color .12s,background .12s}
  .fbrow__titlelink:hover{color:var(--ds-primary);text-decoration:underline;text-underline-offset:2px}
  .fbrow__titlelink:focus-visible{outline:2px solid var(--ds-primary);outline-offset:2px}
  .fbrow__svc{font-size:11px;font-weight:500;color:var(--ds-muted)}
  .fbrow__sum{margin-top:6px;font-size:12px;color:var(--ds-body)}
  .fbrow__act{display:flex;gap:6px;align-self:center}
  .fbbtn{font-size:12px;font-weight:600;border:1px solid var(--ds-hairline);background:var(--ds-surface-white);
    color:var(--ds-muted);border-radius:8px;padding:5px 13px;cursor:pointer;white-space:nowrap}
  .fbbtn:hover{background:var(--ds-hairline-soft)}
  .fbbtn--good{border-color:var(--ds-primary);color:var(--ds-primary);background:var(--ds-primary-tint)}
  .fbbtn--bad{border-color:#ff4e33;color:#ff4e33;background:#ffece9}
  /* ── 게임형 검수 판정 버튼(신호등): 지겨운 검수 → 한 판 하듯. 선택 시 해당 신호등이 '켜짐'(글로우) ── */
  .verdictbtn{display:inline-flex;align-items:center;gap:8px;height:36px;padding:0 16px;border-radius:9999px;
    border:1.5px solid var(--ds-hairline);background:var(--ds-surface-white);font-size:13px;font-weight:700;
    color:var(--ds-text-secondary);cursor:pointer;white-space:nowrap;
    transition:transform .12s var(--ds-ease-standard),box-shadow .16s,background .16s,border-color .16s,color .16s}
  .verdictbtn__dot{width:11px;height:11px;border-radius:50%;background:var(--ds-hairline);
    box-shadow:inset 0 0 0 1px rgba(0,0,0,.08);transition:box-shadow .16s,background .16s}
  .verdictbtn--good .verdictbtn__dot{background:#18ba45}
  .verdictbtn--bad .verdictbtn__dot{background:#ff4e33}
  .verdictbtn:hover{transform:translateY(-1px);border-color:var(--ds-border-input-hover)}
  .verdictbtn:active{transform:translateY(1px) scale(.97)}
  .verdictbtn--good.is-on{border-color:#18ba45;color:#0f8f36;background:rgba(24,186,69,.12);
    box-shadow:0 0 0 3px rgba(24,186,69,.15),0 8px 18px -8px rgba(24,186,69,.6)}
  .verdictbtn--bad.is-on{border-color:#ff4e33;color:#d63a20;background:rgba(255,78,51,.10);
    box-shadow:0 0 0 3px rgba(255,78,51,.15),0 8px 18px -8px rgba(255,78,51,.6)}
  .verdictbtn--good.is-on .verdictbtn__dot{background:#18ba45;box-shadow:0 0 0 2px rgba(24,186,69,.35),0 0 10px 2px rgba(24,186,69,.8)}
  .verdictbtn--bad.is-on .verdictbtn__dot{background:#ff4e33;box-shadow:0 0 0 2px rgba(255,78,51,.35),0 0 10px 2px rgba(255,78,51,.8)}
  [data-theme='dark'] .verdictbtn--good.is-on{color:#5fe08a} [data-theme='dark'] .verdictbtn--bad.is-on{color:#ff8a75}
  /* 패널 헤더 액션 간격: 카운트·버튼·칩이 붙지 않게(기본 gap 2px → 8px) */
  .panel-hd>.ds-widget__actions,.ds-widget__head>.ds-widget__actions{gap:8px}
  /* 수정 대상 요소 선택 칩 */
  .fixelems{display:flex;flex-wrap:wrap;gap:6px}
  .fixelem{height:28px;padding:0 12px;border-radius:9999px;border:1px solid var(--ds-hairline);background:var(--ds-surface-white);
    color:var(--ds-text-secondary);font-size:12px;font-weight:700;cursor:pointer;transition:all .12s}
  .fixelem:hover{border-color:var(--ds-border-input-hover);color:var(--ds-ink)}
  .fixelem.sel{background:#ff4e33;border-color:#ff4e33;color:#fff}
  /* 툴팁: 긴 정의문이 한 줄로 넘쳐 잘리던 것 → 줄바꿈·최대폭으로 감쌈 */
  /* 툴팁: DS 가상요소는 overflow 컨테이너에 잘림 → 전역 고정 레이어(#tipfloat)로 대체 */
  [data-tip]::after,[data-tip]::before{display:none !important}
  #tipfloat{position:fixed;z-index:120;display:none;background:rgba(17,17,17,.94);color:#fff;font-size:12px;
    line-height:1.45;padding:7px 10px;border-radius:8px;max-width:min(280px,72vw);word-break:keep-all;
    pointer-events:none;box-shadow:0 8px 22px -8px rgba(0,0,0,.35)}
  /* 정렬 원칙: 칩 높이 통일 · 필터바 컨트롤 통일 · 표 셀 수직 중앙 */
  .srcfilter__chip{display:inline-flex;align-items:center;height:30px;line-height:1}
  /* 선택 컨트롤 정책: 칩(srcfilter__chip)=다중 토글 필터 · selctl=단일 지정 선택 · selctl--a/--b=A/B 비교 슬롯 */
  .selctl{display:inline-flex;align-items:center;gap:6px;padding:4px 8px 4px 5px;border:1px solid var(--ds-hairline,rgba(0,0,0,.08));
    border-radius:12px;background:var(--ds-surface-white)}
  .selctl__tag{display:inline-flex;align-items:center;justify-content:center;height:24px;min-width:26px;padding:0 8px;
    border-radius:8px;font-size:12px;font-weight:800;color:#fff;background:var(--ds-muted,#8b8f98)}
  .selctl--a .selctl__tag{background:var(--ds-violet,#1e84ff)}
  .selctl--b .selctl__tag{background:#ff6a3d}
  .selctl .field{height:30px;border:0;background:transparent;padding:0 4px;width:auto;min-width:0}
  .selctl__lbl{font-size:11px;font-weight:600;color:var(--ds-muted);margin-left:6px}
  /* 드롭다운 폴리시: 커스텀 셰브론 + 호버·포커스 피드백(전역 select.field) */
  select.field{appearance:none;-webkit-appearance:none;cursor:pointer;
    background-image:url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none'%3E%3Cpath d='M6 9l6 6 6-6' stroke='%238b8f98' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
    background-repeat:no-repeat;background-position:right 10px center;padding-right:30px}
  select.field:hover{border-color:var(--ds-violet,#1e84ff)}
  select.field:focus-visible{outline:2px solid var(--ds-violet,#1e84ff);outline-offset:1px}
  .selctl select.field{background-position:right 4px center;padding-right:22px}
  .selctl:hover{border-color:var(--ds-violet,#1e84ff)}
  .abslot{padding:8px 12px 8px 8px;gap:8px;border-radius:14px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
  .abslot .selctl__tag{height:28px;min-width:28px;border-radius:9px;font-size:13px}
  .abslot .field{height:32px;font-weight:600}
  .abvs{font-size:12px;font-weight:800;color:var(--ds-muted);letter-spacing:.04em}
  .stepline{display:flex;align-items:center;gap:10px;margin:2px 2px 0;min-height:32px}
  .stepline__no{font-family:var(--ds-font-game);font-size:11px;font-weight:700;color:#fff;
    background:var(--ds-violet,#1e84ff);border-radius:9999px;padding:4px 11px;letter-spacing:.03em;flex:none}
  .stepline b{font-family:var(--ds-font-game);font-size:15px;font-weight:700;color:var(--ds-ink);letter-spacing:-.01em}
  .stepline .meta{font-size:11.5px;color:var(--ds-muted)}
  .subhd{display:flex;align-items:center;gap:8px;margin:18px 16px 8px;font-family:var(--ds-font-game);
    font-size:13.5px;font-weight:700;color:var(--ds-ink);letter-spacing:-.01em}
  .subhd .meta{font-family:var(--ds-font-sans);font-size:11.5px;font-weight:500;color:var(--ds-muted)}
  .abbar{display:flex;align-items:center;gap:8px;min-width:160px}
  .abbar__track{display:block;flex:1;height:6px;border-radius:99px;background:var(--ds-hairline-soft,rgba(0,0,0,.06));overflow:hidden}
  .abbar__fill{display:block;height:100%;border-radius:99px;background:var(--ds-violet,#1e84ff);transition:width var(--ds-motion-fast,.2s)}
  .abbar--b .abbar__fill{background:#ff6a3d}
  .abwin{font-size:10.5px;font-weight:800;color:var(--ds-success,#18ba45)}
  .filterbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
  .panel .filterbar{margin-left:16px;margin-right:16px}   /* 표 인셋(16px)과 좌우 정렬 */
  .filterbar .field{height:36px}
  .filterbar input.field{flex:1;min-width:180px}
  .ds-table td{vertical-align:middle}
  .ds-badge[data-tip]{cursor:help}   /* 정의 있는 배지 = help 커서 통일 */
  .fbrow__note{grid-column:1/-1;display:flex;gap:8px;align-items:center;flex-wrap:wrap;
    padding-top:9px;border-top:1px solid var(--ds-hairline-soft)}
  /* 프롬프트 스튜디오 · 단계별 모델 지정 */
  .stage-model{display:flex;align-items:center;gap:9px;margin-bottom:10px}
  .stage-model__lbl{flex:none;font-size:11px;font-weight:700;color:var(--ds-primary);background:var(--ds-primary-tint);
    border-radius:6px;padding:4px 9px}
  .stage-model .field{height:36px}
  /* 원천 프롬프트 = 코드블록 에디터(직접 수정). 모노스페이스 · 코드창 크롬(맥 신호등 도트) */
  .codeblock{position:relative;border:1px solid var(--ds-hairline);border-radius:10px;overflow:hidden;
    background:var(--ds-surface-on);box-shadow:inset 0 1px 0 rgba(255,255,255,.4)}
  .codeblock__bar{display:flex;align-items:center;gap:8px;padding:8px 12px;background:var(--ds-hairline-soft);
    border-bottom:1px solid var(--ds-hairline);font-size:11px;font-weight:700;color:var(--ds-text-secondary);font-family:var(--ds-font)}
  .codeblock__dots{display:inline-flex;gap:5px;margin-right:4px}
  .codeblock__dots i{width:9px;height:9px;border-radius:50%;display:inline-block}
  .codeblock__dots i:nth-child(1){background:#ff5f57} .codeblock__dots i:nth-child(2){background:#febc2e} .codeblock__dots i:nth-child(3){background:#28c840}
  .codeblock__stage{margin-left:auto;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;
    color:var(--ds-muted);background:var(--ds-surface-white);border:1px solid var(--ds-hairline);border-radius:6px;padding:1px 8px}
  .codeblock textarea{display:block;width:100%;border:0;background:transparent;resize:vertical;
    font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"D2Coding",monospace;font-size:12.5px;line-height:1.65;
    color:var(--ds-ink);padding:14px 16px;min-height:200px;tab-size:2;white-space:pre;overflow:auto}
  .codeblock textarea:focus{outline:none;box-shadow:none}
  .codeblock:focus-within{border-color:var(--ds-primary);box-shadow:0 0 0 3px var(--ds-primary-tint)}
  .tnum{font-variant-numeric:tabular-nums}
  :where(button,a,[role=tab],select,summary):focus-visible{outline:2px solid var(--ds-primary);outline-offset:2px;border-radius:8px}

  /* ── 폼 컨트롤(단일 규격) ── */
  .field{width:100%;box-sizing:border-box;border-radius:var(--ctrl-r);background:var(--ds-surface-white);
    border:1px solid var(--ds-hairline);color:var(--ds-ink);font-family:var(--ds-font);font-size:14px;
    transition:border-color .15s,box-shadow .15s,background .15s}
  input.field,select.field{height:var(--ctrl-h);padding:0 var(--ctrl-px)}
  textarea.field{padding:11px var(--ctrl-px);line-height:1.55;min-height:96px;resize:vertical}
  .field::placeholder{color:var(--ds-placeholder)}
  .field:hover{border-color:var(--ds-border-input-hover)}
  .field:focus{outline:none;border-color:var(--ds-primary);box-shadow:0 0 0 3px rgba(30,132,255,.16);background:var(--ds-surface-white)}
  select.field{appearance:none;-webkit-appearance:none;padding-right:34px;cursor:pointer;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%235c6a6a' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
    background-repeat:no-repeat;background-position:right 11px center}

  /* ── DS 파일럿: ImageDropzone · Hero · Empty (라이트 토큰) ── */
  .ds-pilot{--ds-font-sans:var(--ds-font);--ds-font-body:var(--ds-font);--ds-font-display:var(--ds-font)}
  .ds-dropzone{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;
    width:100%;min-height:104px;padding:18px;border:1.5px dashed var(--ds-hairline);border-radius:var(--ds-radius-lg);
    background:var(--ds-surface);color:var(--ds-muted);cursor:pointer;font-family:var(--ds-font-body);
    transition:border-color .15s,background .15s,color .15s}
  .ds-dropzone:hover{border-color:var(--ds-primary);color:var(--ds-ink)}
  .ds-dropzone.drag{border-color:var(--ds-primary);border-style:solid;background:var(--ds-primary-tint);color:var(--ds-ink)}
  .ds-dropzone .dz-ic{width:36px;height:36px;border-radius:10px;display:flex;align-items:center;justify-content:center;
    background:var(--ds-primary-tint);color:var(--ds-primary)}
  .ds-dropzone .dz-ic svg{width:18px;height:18px}
  .ds-dropzone .dz-t{font-size:var(--ds-size-label);color:var(--ds-ink);font-weight:600}
  .ds-dropzone .dz-d{font-size:var(--ds-size-caption);color:var(--ds-muted)}
  .ds-attachments{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
  .ds-hero{display:flex;flex-direction:column;align-items:center;text-align:center;gap:4px;
    padding:48px 24px;border:1px solid var(--ds-hairline);border-radius:var(--ds-radius-xl);font-family:var(--ds-font-body);
    background:var(--ds-surface)}
  .ds-hero__title{font-family:var(--ds-font-sans);font-size:var(--ds-size-heading);font-weight:600;color:var(--ds-ink);margin-top:12px}
  .ds-hero__desc{font-size:var(--ds-size-body);color:var(--ds-muted);max-width:30rem;line-height:var(--ds-lh-normal)}
  .ds-hero__chips{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-top:16px}
  .ds-empty{display:flex;flex-direction:column;align-items:center;text-align:center;gap:3px;
    padding:44px 24px;border:1px dashed var(--ds-hairline);border-radius:var(--ds-radius-lg);
    background:var(--ds-surface);font-family:var(--ds-font-body)}
  .ds-empty__title{font-family:var(--ds-font-sans);font-size:var(--ds-size-subtitle);font-weight:600;color:var(--ds-ink);margin-top:10px}
  .ds-empty__desc{font-size:var(--ds-size-body);color:var(--ds-muted)}

  /* 엑셀 드롭존(단행) */
  .dropzone{display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;
    box-sizing:border-box;height:var(--ctrl-h);padding:0 6px 0 var(--ctrl-px);
    border-radius:var(--ctrl-r);border:1px dashed var(--ds-hairline);background:var(--ds-surface);
    cursor:pointer;font-size:14px;color:var(--ds-body);transition:border-color .15s,background .15s}
  .dropzone:hover{border-color:var(--ds-primary);background:var(--ds-surface-white)}
  .dropzone.drag{border-color:var(--ds-primary);border-style:solid;background:var(--ds-primary-tint);color:var(--ds-ink)}
  .dropzone .pick{flex:none;display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 12px;
    border-radius:7px;background:var(--ds-primary-tint);color:var(--ds-primary);font-size:12px;font-weight:600}
  .dropzone .name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

  /* 썸네일 */
  .thumb{position:relative;aspect-ratio:1;border-radius:9px;overflow:hidden;border:1px solid var(--ds-hairline);background:var(--ds-surface)}
  .thumb img{width:100%;height:100%;object-fit:cover;display:block}
  .thumb-x{position:absolute;top:3px;right:3px;width:18px;height:18px;display:flex;align-items:center;justify-content:center;
    border-radius:5px;background:rgba(0,0,0,.66);color:#fff;opacity:0;transition:opacity .12s}
  .thumb:hover .thumb-x{opacity:1}
  .thumb-x svg{width:11px;height:11px}

  /* 토스트(다크 오버레이) */
  .toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);z-index:80;
    padding:8px 16px;border-radius:10px;font-size:13px;font-weight:600;color:#ffffff;
    background:#000000;box-shadow:0 12px 30px -10px rgba(0,0,0,.45)}
  .copybtn{display:inline-flex;align-items:center;gap:4px;border-radius:7px;padding:3px 8px;font-size:11.5px;
    font-weight:600;color:var(--ds-muted);border:1px solid var(--ds-hairline);background:var(--ds-surface-white);
    transition:color .12s,border-color .12s,background .12s;cursor:pointer}
  .copybtn:hover{color:var(--ds-ink);border-color:var(--ds-border-input-hover);background:var(--ds-primary-tint)}
  .copybtn svg{width:12px;height:12px}
  /* ── 버튼 변형 정의(레거시 shorthand): 재벤더링된 DS 는 --solid.--c-* API 라 --primary/--secondary/--ghost 가
        미정의 → 맨 텍스트로 렌더되던 버그 수정. 앱은 이 shorthand 를 40여곳 사용하므로 앱 레벨에서 채운다. ── */
  .ds-btn{font-weight:var(--ds-weight-strong,650);letter-spacing:-.005em}
  .ds-btn--primary{background:var(--ds-primary);color:#fff;border-color:transparent;
    box-shadow:0 1px 2px rgba(16,24,40,.14),0 4px 12px -3px color-mix(in srgb,var(--ds-primary) 42%,transparent)}
  .ds-btn--primary:hover:not(:disabled){background:var(--ds-primary-hover);color:#fff}
  .ds-btn--primary:active:not(:disabled){background:var(--ds-primary-deep);transform:translateY(.5px)}
  .ds-btn--secondary,.ds-btn--ghost{background:var(--ds-surface-white);color:var(--ds-ink);border-color:var(--ds-hairline);
    box-shadow:0 1px 2px rgba(16,24,40,.05),inset 0 1px 0 rgba(255,255,255,.6)}
  .ds-btn--secondary:hover:not(:disabled),.ds-btn--ghost:hover:not(:disabled){border-color:var(--ds-primary);background:var(--ds-primary-tint);color:var(--ds-primary-deep)}
  .ds-btn--outline{background:var(--ds-surface-white);color:var(--ds-ink);border-color:var(--ds-border-input);box-shadow:0 1px 2px rgba(16,24,40,.04)}
  .ds-btn--outline:hover:not(:disabled){border-color:var(--ds-primary);color:var(--ds-primary-deep);background:var(--ds-primary-tint)}
  /* 텍스트 버튼 금지 → 박스형(copybtn) 라벨 굵기·대비 상향 */
  .copybtn{font-weight:650;color:var(--ds-text-secondary);box-shadow:0 1px 1px rgba(16,24,40,.03)}
  .ds-iconbtn--bordered{box-shadow:0 1px 2px rgba(16,24,40,.05),inset 0 1px 0 rgba(255,255,255,.5)}
  .ds-iconbtn--bordered:hover{border-color:var(--ds-primary);color:var(--ds-primary);background:var(--ds-primary-tint)}
  /* YELLOW(사람검수 대기) 배지 · 테마 인지(라이트/다크 모두 대비 확보) */
  .ds-badge--yellow{background:rgba(255,148,41,.16);color:#b8600a}
  /* ── 다크모드 대비 보정: 하드코딩 골드/화이트가 다크서 안 보이던 부분 ── */
  [data-theme='dark'] .ds-badge--yellow{background:rgba(255,158,64,.22);color:#ffc266}
  [data-theme='dark'] .gbadge__exp,[data-theme='dark'] .badgeburst__exp{color:#ffc879;background:rgba(255,158,64,.20)}
  [data-theme='dark'] .metarow__amb{color:#ff8a75}
  [data-theme='dark'] .ds-btn--secondary,[data-theme='dark'] .ds-btn--ghost,[data-theme='dark'] .ds-btn--outline{box-shadow:0 1px 2px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.04)}
  [data-theme='dark'] .copybtn,[data-theme='dark'] .ds-iconbtn--bordered{box-shadow:0 1px 2px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.04)}
  [data-theme='dark'] .ds-stat{background:linear-gradient(180deg,var(--ds-surface-on),var(--ds-surface));box-shadow:inset 0 1px 0 rgba(255,255,255,.04),0 1px 2px rgba(0,0,0,.3)}
  /* ── 공통 버튼 스케일 정책(전 페이지): 계층=색(primary/secondary/danger) · 크기=역할 · 모서리 R8 ──
        sm=인라인·행 액션(콤팩트, 헤비하지 않게 semibold) · md=기본 · lg=CTA. DS 기본은 caption=14px 라 sm 이 커 보임 → 축소. */
  .ds-btn--s-sm{--ds-btn-h:28px;--ds-btn-px:10px;--ds-btn-fz:12.5px;font-weight:600;letter-spacing:-.01em}
  .ds-btn--s-md{--ds-btn-h:34px;--ds-btn-px:13px;--ds-btn-fz:13.5px;font-weight:600}
  .ds-btn--s-lg{--ds-btn-h:40px;--ds-btn-px:16px;--ds-btn-fz:14.5px}
  .ds-btn:not([class*="--s-"]){--ds-btn-h:36px;--ds-btn-fz:14px}   /* 크기 미지정 기본도 살짝 콤팩트 */

  /* 카드 · 패널 (라이트 + 절제된 입체) */
  .card{box-shadow:var(--ds-highlight);transition:transform .2s cubic-bezier(.32,.72,0,1),border-color .2s}
  .card:hover{transform:translateY(-1px)}
  /* .panel = 카탈로그 위젯 카드(ds-widget) 외형 상단 라이닝 금지(§4.4.2) → 헤드 구분선 없음 */
  /* 카드 계열 통일: 콘텐츠 패널은 차분(무-호버리프트) · border-card · radius-lg · shadow-low */
  .panel{border:1px solid var(--ds-border-card);border-radius:var(--ds-radius-lg);background:var(--ds-surface);overflow:hidden;
    box-shadow:0 1px 2px rgba(0,0,0,.04),0 10px 26px -12px rgba(0,0,0,.14),var(--ds-highlight);
    transition:box-shadow var(--ds-motion-standard) var(--ds-ease-standard),transform var(--ds-motion-standard) var(--ds-ease-standard)}
  .panel:hover{box-shadow:0 2px 5px rgba(0,0,0,.06),0 18px 38px -14px rgba(0,0,0,.20),var(--ds-highlight);transform:translateY(-2px)}
  /* ── 카드 폴리시: 오락실 모달 수준의 깊이·정돈감(라이트 3중 레이어 그림자 + 지표 타일) ── */
  .panel,.ds-widget{border-color:var(--ds-hairline)}
  .panel{box-shadow:0 1px 2px rgba(16,24,40,.04),0 8px 20px -10px rgba(16,24,40,.10),0 26px 50px -26px rgba(16,24,40,.15),var(--ds-highlight)}
  .panel:hover{box-shadow:0 2px 4px rgba(16,24,40,.05),0 14px 30px -12px rgba(16,24,40,.15),0 36px 64px -28px rgba(16,24,40,.22),var(--ds-highlight);transform:translateY(-3px)}
  .ds-widget{box-shadow:0 1px 2px rgba(16,24,40,.04),0 8px 20px -10px rgba(16,24,40,.10),0 24px 46px -26px rgba(16,24,40,.14),var(--ds-highlight)}
  .ds-stat{background:linear-gradient(180deg,var(--ds-surface-white),var(--ds-surface));
    box-shadow:inset 0 1px 0 rgba(255,255,255,.65),0 1px 2px rgba(16,24,40,.04),0 6px 16px -12px rgba(16,24,40,.16);
    transition:transform .16s var(--ds-ease-standard),box-shadow .16s var(--ds-ease-standard)}
  .ds-stat:hover{transform:translateY(-2px);box-shadow:0 10px 22px -12px rgba(16,24,40,.20)}
  .ds-stat__value--accent{text-shadow:0 0 18px color-mix(in srgb,var(--ds-primary) 28%,transparent)}
  .panel-hd{display:flex;align-items:center;gap:12px;padding:18px 20px 13px;border-bottom:1px solid var(--ds-hairline-soft)}
  .panel-bd{padding-top:16px}   /* 헤더 구분선 아래 본문 */
  .panel-hd>b{margin-right:auto}
  .ds-progress--drill:hover .ds-progress__label{color:var(--ds-primary)}
  .ds-progress--drill:hover .ds-progress__track{box-shadow:0 0 0 2px color-mix(in srgb,var(--ds-primary) 26%,transparent)}
  /* 콘텐츠 상세 스플릿뷰(공통 컴포넌트) */
  .detailview{background:var(--ds-layer-popup);border-radius:var(--ds-radius-xl);box-shadow:var(--ds-shadow-high);width:min(940px,94vw);max-height:88vh;display:flex;flex-direction:column;overflow:hidden}
  .detailview__hd{display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid var(--ds-divider-section)}
  .detailview__hd b{font-size:15px;color:var(--ds-ink)}
  .detailview__body{display:grid;grid-template-columns:1.25fr 1fr;min-height:0;overflow:hidden}
  .detailview__content{padding:22px 24px;overflow:auto;border-right:1px solid var(--ds-divider-section)}
  .detailview__eval{padding:22px 24px;overflow:auto;display:flex;flex-direction:column;gap:16px;background:var(--ds-surface-on)}
  .dvc__service{font-size:12px;font-weight:700;color:var(--ds-primary)}
  .dvc__title{font-size:22px;font-weight:700;line-height:1.3;margin:6px 0 4px;color:var(--ds-ink)}
  .dvc__subtitle{font-size:15px;color:var(--ds-text-secondary);margin-bottom:6px}
  .dvc__lead{margin:14px 0;padding:14px 16px;border-radius:var(--ds-radius-md);background:var(--ds-primary-tint);font-size:15px;line-height:1.6;color:var(--ds-text-secondary)}
  .dvc__lead-lbl{display:block;font-size:11px;font-weight:700;color:var(--ds-primary);margin-bottom:4px}
  .dvc__bodytext{font-size:15px;line-height:1.7;color:var(--ds-text-secondary);white-space:pre-wrap;margin-top:12px}
  .dvc__note{font-size:12px;color:var(--ds-muted);margin-top:12px}
  .dvc__src{margin-top:16px;display:inline-flex}
  .dve__lbl{font-size:11px;font-weight:700;color:var(--ds-muted);margin-bottom:6px}
  .dve__verdict{margin-top:auto;padding-top:16px;border-top:1px solid var(--ds-divider-section)}
  @media (max-width:720px){.detailview__body{grid-template-columns:1fr}.detailview__content{border-right:0;border-bottom:1px solid var(--ds-divider-section)}}
  .panel-hd b{color:var(--ds-ink);font-size:16px;font-weight:700;letter-spacing:-.01em}
  .panel-hd .meta{font-size:12px;color:var(--ds-muted)}
  .panel-bd{padding:16px 20px 20px}
  .drow{display:grid;grid-template-columns:124px 1fr;gap:16px;padding:15px 18px;
    border-bottom:1px solid var(--ds-hairline-soft);align-items:start}
  .drow:last-child{border-bottom:0}
  .drow .k{font-size:12px;font-weight:600;color:var(--ds-muted);padding-top:3px}
  .drow .v{min-width:0}
  .lead{position:relative;overflow:hidden;background:var(--ds-primary-tint)!important;
    border-color:rgba(30,132,255,.24)!important}
  .lead::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--ds-primary)}
  .lbl{display:block;margin-bottom:7px;font-size:11px;font-weight:600;letter-spacing:.05em;
    text-transform:uppercase;color:var(--ds-muted)}
  .empty{border:1px dashed var(--ds-hairline);border-radius:12px;padding:40px 24px;text-align:center;color:var(--ds-muted)}

  /* details 토글 마커 */
  details>summary{list-style:none}
  details>summary::-webkit-details-marker{display:none}
  details>summary::before{content:"›";display:inline-block;width:1em;margin-right:5px;transition:transform .15s;color:var(--ds-muted)}
  details[open]>summary::before{transform:rotate(90deg)}

  /* 스켈레톤 */
  .skel{position:relative;overflow:hidden;background:var(--ds-hairline-soft);border-radius:8px}
  .skel::after{content:"";position:absolute;inset:0;
    background:linear-gradient(90deg,transparent,rgba(0,0,0,.05),transparent);
    transform:translateX(-100%);animation:shimmer 1.4s infinite}
  @keyframes shimmer{100%{transform:translateX(100%)}}

  /* 세그먼트(입력 탭 · 추론강도) */
  .seg{display:flex;gap:3px;padding:3px;border-radius:10px;background:var(--ds-surface);border:1px solid var(--ds-hairline)}
  .seg button{flex:1;border-radius:7px;padding:6px 0;font-size:12px;font-weight:600;color:var(--ds-muted);cursor:pointer;
    transition:color .15s,background .15s}
  .seg button.on{background:var(--ds-primary-tint);color:var(--ds-primary-deep);box-shadow:var(--ds-highlight)}
  .seg button:not(.on):hover{color:var(--ds-ink)}

  /* 인포그래픽 · 타일·분포바·도넛 */
  .tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
  .tile{border-radius:var(--ds-radius-md);padding:15px 16px;background:var(--ds-surface);border:1px solid var(--ds-border-card);box-shadow:0 1px 2px rgba(0,0,0,.04),0 6px 14px -8px rgba(0,0,0,.1)}
  .tile .n{font-size:23px;font-weight:600;color:var(--ds-ink);line-height:1.1;letter-spacing:-.01em}
  .tile .t{margin-top:3px;font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--ds-muted)}
  .tile--hero .n{color:var(--ds-violet,#1e84ff);font-size:30px}
  .bar{display:grid;grid-template-columns:96px 1fr 38px;align-items:center;gap:10px}
  .bar .lab{font-size:12.5px;color:var(--ds-body);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .track{height:8px;border-radius:6px;background:var(--ds-hairline-soft);overflow:hidden}
  .track .fill{height:100%;border-radius:6px;background:var(--ds-primary)}
  .bar .pc{font-size:12px;color:var(--ds-muted);text-align:right}
  .ring{width:108px;height:108px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex:none}
  .ring i{width:78px;height:78px;border-radius:50%;background:var(--ds-surface);display:flex;flex-direction:column;
    align-items:center;justify-content:center}
  .ring .pv{font-size:21px;font-weight:600;color:var(--ds-ink);line-height:1}
  .ring .pl{font-size:10px;color:var(--ds-muted);margin-top:2px}

  /* ── 설정 다이얼로그 내부(키·모델) ── */
  .cfgtabs{display:flex;gap:4px;border-bottom:1px solid var(--ds-hairline-soft);margin-bottom:4px}
  .cfgtabs button{appearance:none;background:none;border:0;cursor:pointer;padding:9px 14px;border-radius:8px 8px 0 0;
    font-size:13px;font-weight:600;color:var(--ds-muted);position:relative;transition:color .12s}
  .cfgtabs button:hover{color:var(--ds-ink)}
  .cfgtabs button.on{color:var(--ds-ink)}
  .cfgtabs button.on::after{content:"";position:absolute;left:10px;right:10px;bottom:-1px;height:2px;background:var(--ds-primary);border-radius:2px}
  .cfgsec{padding:16px 0;border-bottom:1px solid var(--ds-hairline-soft)}
  .cfgsec:last-child{border-bottom:0}
  .routercard{border:1px solid rgba(30,132,255,.30);border-radius:12px;padding:15px;
    background:var(--ds-surface)}
  .routercard .rc-h{display:flex;align-items:center;gap:7px;margin-bottom:3px}
  .routercard .rc-h b{font-size:13px;font-weight:700;color:var(--ds-ink)}
  .rc-badge{font-size:9.5px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:var(--ds-primary-deep);
    background:rgba(30,132,255,.16);border-radius:5px;padding:2px 6px}
  .routercard .rc-d{margin:0 0 13px;font-size:11.5px;color:var(--ds-muted);line-height:1.55}
  .sectitle{font-size:13px;font-weight:700;color:var(--ds-ink);margin:20px 0 4px}
  .secdesc{font-size:11.5px;color:var(--ds-muted);margin:0 0 12px;line-height:1.55}
  .krow+.krow{margin-top:12px;padding-top:12px;border-top:1px solid var(--ds-hairline-soft)}
  .krow-top{display:flex;align-items:center;justify-content:space-between;margin-bottom:7px}
  .krow-nm{font-size:13px;font-weight:600;color:var(--ds-ink)}
  .krow-st{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--ds-muted)}
  .keyin{position:relative}
  .keyin input{padding-right:38px}
  .keyin .eye{position:absolute;right:6px;top:50%;transform:translateY(-50%);width:28px;height:28px;display:flex;
    align-items:center;justify-content:center;border:0;background:none;color:var(--ds-muted);cursor:pointer}
  .keyin .eye:hover{color:var(--ds-ink)}
  .keyin .eye svg{width:16px;height:16px}
  .sdot{width:6px;height:6px;border-radius:50%;flex:none;background:var(--ds-placeholder)}
  .sdot.ok{background:var(--ds-success)}
  .sdot.off{background:var(--ds-placeholder)}

  /* ════ 위젯 홈 셸 ════ */
  /* 상단 바(로고|타이틀 고정) + 아래(사이드바|콘텐츠) · 타이틀을 로고 위치에 고정해 정렬 표준화 */
  .appshell{display:flex;flex-direction:column;max-width:1320px;margin:0 auto;height:100dvh;overflow:hidden}
  /* 상단 바·사이드바는 완전 고정 스크롤은 콘텐츠(.home) 내부에서만 → 톱바를 절대 침범하지 않음 */
  .topbar{flex:none;display:flex;align-items:stretch;gap:0;height:68px;margin:16px 22px 0;
    background:var(--ds-fixed-bg);border:1px solid var(--ds-hairline);border-radius:var(--ds-radius-xl);
    box-shadow:0 1px 2px rgba(0,0,0,.05),var(--ds-highlight);position:relative;z-index:30}
  /* 로고 영역 = 카드 왼쪽~구분선(폭 232 = 사이드바 폭), 콘텐츠 가운데 정렬 */
  .topbar__brand{width:232px;flex:none;display:flex;align-items:center;justify-content:center;gap:10px;border-right:1px solid var(--ds-hairline)}
  /* 제공된 풀 로고(배지+PRISM+태그라인) · 라이트/다크 스왑 */
  .topbar__logo{height:56px;width:auto;display:block}
  .topbar__logo--dark{display:none}
  [data-theme='dark'] .topbar__logo--light{display:none} [data-theme='dark'] .topbar__logo--dark{display:block}
  .topbar__brand .logo{width:32px;height:32px;display:flex;align-items:center;justify-content:center;flex:none}
  .topbar__brand .logo img{width:100%;height:100%}
  .topbar__wm{font-family:var(--ds-font-sans);font-weight:600;font-size:var(--ds-size-subtitle);color:var(--ds-ink);line-height:1.12}
  .topbar__wm small{display:block;font-weight:400;font-size:11px;color:var(--ds-muted)}
  /* 타이틀 = 아래 위젯 아이콘 라인(콘텐츠 패딩선)에 정렬: 구분선(232) + 37 */
  .topbar__title{flex:1;min-width:0;padding-left:37px;display:flex;flex-direction:column;justify-content:center;gap:3px}
  .topbar__title .homehead__title{font-size:15px;line-height:1.15}
  .topbar__title .homehead__sub{font-size:12.5px;line-height:1.2}
  .topbar__tools{flex:none;display:flex;align-items:center;gap:6px;position:relative;padding-right:14px}
  /* 연결 현황(다중) · 제공자별 칩 */
  .topbar__conn{display:inline-flex;align-items:center;gap:8px;border:0;background:none;cursor:pointer;padding:4px 6px;margin-right:4px;border-radius:8px}
  .topbar__conn:hover{background:var(--ds-hairline-soft)}
  .topbar__conn--dot{padding:7px;border-radius:9999px;margin-right:6px}
  .topbar__conn--dot .ds-statusdot{width:11px;height:11px}
  .live-toast{position:fixed;top:18px;left:50%;transform:translateX(-50%);z-index:90;
    display:inline-flex;align-items:center;gap:8px;padding:9px 16px;border-radius:999px;
    background:var(--ds-ink);color:#fff;font-size:12.5px;font-weight:500;
    box-shadow:0 8px 24px rgba(0,0,0,.22)}
  .err-toast{position:fixed;top:18px;left:50%;transform:translateX(-50%);z-index:96;display:flex;align-items:center;gap:8px;padding:10px 16px;border-radius:9999px;background:var(--ds-error);color:#fff;font-size:13px;font-weight:600;box-shadow:var(--ds-shadow-high);cursor:pointer}
  .err-toast__ic{font-size:14px}
  .live-toast__dot{width:7px;height:7px;border-radius:50%;background:#18ba45;flex:none;
    box-shadow:0 0 0 3px rgba(61,220,151,.25)}
  /* ── 결과 출처 필터 ── */
  .srcfilter{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:11px}
  .srcfilter__chip{font-size:12px;font-weight:600;padding:5px 12px;border-radius:999px;cursor:pointer;
    border:1px solid var(--ds-hairline,rgba(0,0,0,0.08));background:var(--ds-surface-white);color:var(--ds-body,rgba(0,0,0,0.88))}
  .srcfilter__chip span{color:var(--ds-muted);font-variant-numeric:tabular-nums;margin-left:2px}
  .srcfilter__chip:hover{border-color:var(--ds-violet,#1e84ff)}
  .srcfilter__chip.sel{background:var(--ds-violet,#1e84ff);color:#fff;border-color:var(--ds-violet,#1e84ff)}
  .srcfilter__chip.sel span{color:rgba(255,255,255,.8)}
  .keymanaged{padding:16px;border-radius:12px;background:var(--ds-violet-tint,rgba(30,132,255,0.16));border:1px solid var(--ds-hairline,rgba(0,0,0,0.08))}
  .keymanaged b{display:block;font-size:14px;margin-bottom:6px}
  .keymanaged p{font-size:12.5px;line-height:1.6;color:var(--ds-body,rgba(0,0,0,0.88));margin:0}
  /* ── 검수자 등록 온보딩(딤드 + 중앙 모달) ── */
  .onboard{position:fixed;inset:0;z-index:120;display:flex;align-items:center;justify-content:center;padding:24px;
    background:rgba(0,0,0,.55);backdrop-filter:blur(4px)}
  .onboard__card{width:100%;max-width:440px;max-height:92vh;overflow-y:auto;background:var(--ds-surface-white);border-radius:24px;
    padding:30px 30px 24px;box-shadow:0 24px 70px rgba(0,0,0,.4);text-align:center}
  .onboard__brand{display:flex;align-items:center;justify-content:center;margin-bottom:16px}
  .onboard__logo{height:42px;width:auto;display:block}
  .onboard__logo--dark{display:none}
  [data-theme='dark'] .onboard__logo--light{display:none} [data-theme='dark'] .onboard__logo--dark{display:block}
  /* 온보딩 영역 그룹(계정 / 팀·캐릭터) · 시각적 구분 */
  .onboard__group{text-align:left;border:1px solid var(--ds-hairline);border-radius:14px;padding:16px;margin-bottom:14px;background:var(--ds-surface)}
  .onboard__group--b{margin-top:2px}
  .onboard__grouphd{font-size:11px;font-weight:800;color:var(--ds-muted);letter-spacing:.03em;margin-bottom:12px;text-transform:uppercase}
  .onboard__title{font-size:23px;font-weight:800;color:var(--ds-ink);margin:0 0 8px}
  .onboard__lead{font-size:13px;line-height:1.6;color:var(--ds-body,rgba(0,0,0,0.88));margin:0 0 22px}
  .onboard__lbl{display:block;text-align:left;font-size:12px;font-weight:700;color:var(--ds-ink);margin:0 0 7px}
  .onboard__hint{font-weight:500;color:var(--ds-muted)}
  .onboard__name{height:44px;width:100%;font-size:15px;margin-bottom:18px}
  .onboard__chars{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin-bottom:24px}
  .ochar{display:flex;flex-direction:column;align-items:center;gap:3px;padding:11px 4px 9px;border-radius:12px;
    border:1.5px solid var(--ds-hairline,rgba(0,0,0,0.08));background:var(--ds-surface,#ffffff);cursor:pointer;transition:all .15s}
  .ochar:hover{border-color:var(--ds-violet,#1e84ff);transform:translateY(-2px)}
  .ochar__ring{width:54px;height:54px;border-radius:50%;display:flex;align-items:center;justify-content:center;
    background:#fff;box-shadow:0 0 0 2px var(--ds-hairline,rgba(0,0,0,0.08));transition:box-shadow .15s}
  .ochar__ring img{width:42px;height:42px}
  .ochar b{font-size:12.5px;color:var(--ds-ink)} .ochar small{font-size:10px;color:var(--ds-muted)}
  .ochar.sel{border-color:var(--ds-violet,#1e84ff);background:var(--ds-violet-tint,rgba(30,132,255,0.16))}
  .ochar.sel .ochar__ring{box-shadow:0 0 0 3px var(--ds-violet,#1e84ff)}
  .onboard__authtabs{display:flex;gap:6px;margin-bottom:14px;background:var(--ds-hairline-soft,rgba(0,0,0,.04));padding:4px;border-radius:12px}
  .onboard__authtabs button{flex:1;height:34px;border:0;background:none;border-radius:8px;font-size:13px;font-weight:700;color:var(--ds-muted);cursor:pointer}
  .onboard__authtabs button.sel{background:var(--ds-surface-white);color:var(--ds-ink);box-shadow:0 1px 3px rgba(0,0,0,.08)}
  .onboard__authmsg{text-align:left;font-size:12px;color:#ff4e33;margin:-6px 0 12px}
  .onboard__cta{height:46px;width:100%;font-size:15px;font-weight:700}
  .onboard__cta:disabled{opacity:.45;cursor:not-allowed}
  .onboard__skip{margin-top:10px;background:none;border:0;color:var(--ds-muted);font-size:12.5px;cursor:pointer}
  /* ── 검수자 등록: 캐릭터 선택(구 약식, 미사용 호환) ── */
  .charpick{display:grid;grid-template-columns:repeat(4,1fr);gap:7px}
  .charpick__opt{display:flex;flex-direction:column;align-items:center;gap:3px;padding:8px 2px;border-radius:12px;
    border:1.5px solid var(--ds-hairline,rgba(0,0,0,0.08));background:var(--ds-surface-white);cursor:pointer;transition:all .15s}
  .charpick__opt:hover{border-color:var(--ds-violet,#1e84ff)}
  .charpick__opt img{width:34px;height:34px}
  .charpick__opt span{font-size:10.5px;color:var(--ds-muted);font-weight:600}
  .charpick__opt.sel{border-color:var(--ds-violet,#1e84ff);background:var(--ds-violet-tint,rgba(30,132,255,0.16));box-shadow:0 0 0 2px var(--ds-violet-tint,rgba(30,132,255,0.16))}
  .charpick__opt.sel span{color:var(--ds-violet,#1e84ff)}
  /* 탭 = 아래 내용과 연결된 언더라인 방식(플로팅 필름 → 콘텐츠에 붙는 탭). 활성 탭 하단 프라이머리 인디케이터. */
  .evaltabs{display:flex;gap:2px;background:transparent;padding:0 2px;border-bottom:1.5px solid var(--ds-hairline);border-radius:0;max-width:none}
  .evaltabs--sub{background:transparent;padding:0 2px}
  .evaltabs button{flex:0 0 auto;height:40px;padding:0 18px;border:0;background:none;border-radius:8px 8px 0 0;
    font-size:13.5px;font-weight:700;color:var(--ds-muted);cursor:pointer;position:relative;transition:color .15s,background .15s}
  .evaltabs button:hover:not(.sel){color:var(--ds-ink);background:var(--ds-hairline-soft)}
  .evaltabs button.sel{background:none;color:var(--ds-primary-deep);box-shadow:none}
  .evaltabs button.sel::after{content:"";position:absolute;left:10px;right:10px;bottom:-1.5px;height:2.5px;
    background:var(--ds-primary);border-radius:3px 3px 0 0}
  .evaltabs--sub button{background:none;border-radius:8px 8px 0 0}
  /* 스탯 클러스터 정렬 규칙(전 카드 공통): 숫자 line-height 1 + 라벨 동일 크기·간격 → 하단 기준선 정렬 */
  .goldgrid{display:flex;align-items:flex-end;gap:28px;margin-top:16px;flex-wrap:wrap}
  .goldbig__v{font-size:42px;font-weight:800;color:var(--ds-violet,#1e84ff);line-height:1}
  .goldbig__l{font-size:11px;color:var(--ds-muted);margin-top:6px;line-height:1.4}
  .goldstat{display:flex;flex-direction:column}
  .goldstat b{font-size:20px;font-weight:800;color:var(--ds-ink);line-height:1}
  .goldstat span{font-size:11px;color:var(--ds-muted);margin-top:6px;line-height:1.4}
  .metarow{display:flex;gap:10px;padding:11px 6px;border-bottom:1px solid var(--ds-hairline-soft)}
  .metarow__dir{font-size:13px;color:var(--ds-ink);line-height:1.55}
  .metarow__amb{font-size:11.5px;color:#ff4e33;margin-top:4px;line-height:1.5}
  .invite{display:flex;align-items:center;justify-content:space-between;gap:14px}
  .invite__code{font-family:var(--ds-font-mono,ui-monospace);font-size:26px;font-weight:800;letter-spacing:.12em;color:var(--ds-violet,#1e84ff)}
  /* ── 평가 아레나(게임화) ── */
  .arena-hero{background:linear-gradient(135deg,var(--ds-violet,#1e84ff),var(--ds-violet-deep,#004fad));
    color:#fff;border-radius:16px;padding:22px 24px;box-shadow:0 10px 30px rgba(19,52,59,.22)}
  .arena-hero__head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
  .arena-hero__eyebrow{font-size:12px;opacity:.82;font-weight:600;letter-spacing:.02em;margin-bottom:4px}
  .arena-hero__big{font-size:54px;font-weight:800;line-height:1;display:flex;align-items:baseline;gap:8px;font-variant-numeric:tabular-nums}
  .arena-hero__pct{font-size:26px;font-weight:700;opacity:.85}
  .arena-hero__delta{font-size:13px;font-weight:600;padding:3px 9px;border-radius:999px;background:rgba(255,255,255,.16)}
  .arena-hero__delta.up{color:#b8f5da} .arena-hero__delta.down{color:#ffd5d5}
  .arena-hero__target{font-size:13px;opacity:.9;white-space:nowrap;padding-top:6px}
  .arena-gauge{position:relative;height:14px;border-radius:999px;background:rgba(255,255,255,.18);margin:16px 0 12px;overflow:hidden}
  .arena-gauge__fill{position:absolute;left:0;top:0;bottom:0;border-radius:999px;
    background:linear-gradient(90deg,#18ba45,#7be3a3);transition:width .6s cubic-bezier(.22,1,.36,1)}
  .arena-gauge__target{position:absolute;top:-3px;bottom:-3px;width:3px;background:#fff;border-radius:2px;box-shadow:0 0 0 2px rgba(19,52,59,.35)}
  .arena-hero__foot{display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:12.5px;opacity:.95;flex-wrap:wrap}
  /* 히어로(파란 그라데이션) 위의 text-ink(라이트=검정) 강제 흰색 → 라이트 모드 가독성 */
  .arena-hero .text-ink,.arena-hero__foot b,.arena-hero__eyebrow{color:#fff !important}
  .arena-quest{cursor:pointer;font-weight:600;background:rgba(255,255,255,.16);padding:5px 12px;border-radius:999px}
  .arena-quest:hover{background:rgba(255,255,255,.26)}
  .arena-quest--done{cursor:default;background:rgba(255,255,255,.1)}
  .arena-cols{display:grid;grid-template-columns:1.3fr 1fr;gap:16px}
  @media (max-width:820px){.arena-cols{grid-template-columns:1fr}}
  /* 홈 아레나: 세로 스택(가로 full 사용) · 캐릭터 히어로 상단 */
  .arena-cols{display:flex;flex-direction:column;gap:16px}
  .arena-charpanel{order:-1}
  /* 2분할 선수 카드: 좌=캐릭터·스탯·검수하기 · 우=모은 배지 컬렉션 */
  .charcard--split{text-align:left;padding:0;display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,1.15fr);gap:0;overflow:hidden}
  .charcard--split .charcard__player{display:flex;gap:22px;align-items:center;padding:24px 26px;
    border-right:1px solid var(--ds-hairline-soft)}
  .charcard--split .charcard__avatar{flex:none;margin:0}
  .charcard--split .charcard__info{flex:1;min-width:0;display:flex;flex-direction:column;gap:10px}
  .charcard--split .charcard__title{margin:0;font-size:17px;text-align:left}
  .charcard--split .charcard__xpwrap{padding:0}
  .charcard--split .charcard__stats{justify-content:center;gap:26px;margin:2px 0}
  .charcard--split .charcard__mission{margin-top:2px}
  .charcard--split .charcard__collection{padding:20px 22px 16px;display:flex;flex-direction:column;min-width:0;max-height:340px}
  .charcard--split .charcard__badges-hd{margin-top:0;display:flex;align-items:center;gap:8px;flex:none}
  .charcard--split .charcard__badges{grid-template-columns:repeat(auto-fill,minmax(74px,1fr));overflow-y:auto;min-height:0;padding-right:4px;padding-bottom:2px}
  @media (max-width:900px){.charcard--split{grid-template-columns:1fr}
    .charcard--split .charcard__player{border-right:0;border-bottom:1px solid var(--ds-hairline-soft)}}
  @media (max-width:640px){.charcard--split .charcard__player{flex-direction:column;text-align:center}
    .charcard--split .charcard__stats{justify-content:center}}
  .charcard__more{margin-left:auto;font-size:11px;font-weight:700}
  /* 배지 카테고리 라벨(작게) */
  .gbadge__cat{display:block;margin-top:1px;font-size:9px;font-weight:700;color:var(--ds-muted);letter-spacing:.02em}
  .lb-row{display:flex;align-items:center;gap:10px;padding:9px 6px;border-bottom:1px solid var(--ds-hairline-soft)}
  .lb-row--me{background:var(--ds-violet-tint,rgba(30,132,255,0.16));border-radius:9px;border-bottom-color:transparent}
  .lb-rank{width:30px;text-align:center;font-weight:700;font-size:14px}
  .lb-name{flex:1;min-width:0;font-weight:600;color:var(--ds-ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .lb-streak{font-size:12px;color:var(--ds-muted)} .lb-pts{font-weight:700;color:var(--ds-violet,#1e84ff)}
  .lb-title{display:block;font-size:10px;color:var(--ds-muted);font-weight:500;margin-top:1px}
  /* 주간 리그: 존 배지·델타·점수를 고정 폭 컬럼으로 정렬(승급/유지/강등 행이 같은 열에) */
  .lb-row--league .lb-zone{min-width:60px;justify-content:center;flex:none}
  .lb-row--league .lb-delta{min-width:54px;display:inline-flex;justify-content:flex-end;text-align:right;flex:none;font-variant-numeric:tabular-nums}
  .lb-row--league .lb-pts{min-width:60px;text-align:right;flex:none}
  .lb-av{width:30px;height:30px;border-radius:50%;flex:none;display:flex;align-items:center;justify-content:center;
    background:var(--ds-surface-white);box-shadow:0 0 0 2px var(--tier-c,rgba(0,0,0,.12));overflow:hidden}
  .lb-av img{width:24px;height:24px}
  /* ── 검수 캐릭터 육성 카드 ── */
  /* 캐릭터 육성 카드: 입체 카드 + 발판 + 플로팅/브리딩 애니메이션 */
  .charcard{text-align:center;padding:22px 16px 18px;--tier-c:rgba(0,0,0,.24);
    background:radial-gradient(130% 90% at 50% -10%, color-mix(in srgb,var(--tier-c) 12%,var(--ds-surface)), var(--ds-surface) 70%);
    border:1px solid color-mix(in srgb,var(--tier-c) 22%,var(--ds-border-card));border-radius:var(--ds-radius-xl);
    box-shadow:var(--ds-shadow-medium),inset 0 1px 0 rgba(255,255,255,.5)}
  .charcard[data-tier="1"]{--tier-c:#1e84ff} .charcard[data-tier="2"]{--tier-c:#5c77ff}
  .charcard[data-tier="3"]{--tier-c:#ff9429} .charcard[data-tier="4"]{--tier-c:#a05cff}
  .lb-av[data-tier="1"]{--tier-c:#1e84ff} .lb-av[data-tier="2"]{--tier-c:#5c77ff}
  .lb-av[data-tier="3"]{--tier-c:#ff9429} .lb-av[data-tier="4"]{--tier-c:#a05cff}
  .charcard__avatar{position:relative;width:140px;height:140px;margin:2px auto 8px;display:flex;align-items:center;justify-content:center}
  .charcard__avatar::after{content:'';position:absolute;bottom:16px;left:50%;transform:translateX(-50%);width:62px;height:12px;border-radius:50%;
    background:radial-gradient(ellipse at center, color-mix(in srgb,var(--tier-c) 26%,transparent), transparent 72%);z-index:0;
    animation:charshadow 3.2s ease-in-out infinite}
  .charcard__avatar img{position:relative;z-index:1;width:84px;height:84px;transform-origin:center;
    filter:drop-shadow(0 6px 9px rgba(0,0,0,.22));
    animation:charfloat 3.2s ease-in-out infinite}
  .charcard[data-tier="0"]{--tier:0} .charcard[data-tier="1"]{--tier:1} .charcard[data-tier="2"]{--tier:2}
  .charcard[data-tier="3"]{--tier:3} .charcard[data-tier="4"]{--tier:4}
  .charcard__glow{position:absolute;inset:0;border-radius:50%;
    background:radial-gradient(circle at 50% 42%,color-mix(in srgb,var(--tier-c) 30%,transparent),transparent 70%);
    box-shadow:0 0 0 3px color-mix(in srgb,var(--tier-c) 45%,transparent), inset 0 0 24px color-mix(in srgb,var(--tier-c) 18%,transparent);
    opacity:calc(.4 + var(--tier,0)*0.13);animation:charpulse 2.8s ease-in-out infinite}
  @keyframes charfloat{0%,100%{transform:scale(calc(1 + var(--tier,0)*0.05)) translateY(0)}50%{transform:scale(calc(1 + var(--tier,0)*0.05)) translateY(-4px)}}
  @keyframes charshadow{0%,100%{transform:translateX(-50%) scale(1);opacity:.9}50%{transform:translateX(-50%) scale(.82);opacity:.6}}
  @keyframes charpulse{0%,100%{transform:scale(1);opacity:.6}50%{transform:scale(1.06);opacity:.9}}
  @media (prefers-reduced-motion:reduce){.charcard__avatar img,.charcard__avatar::after,.charcard__glow{animation:none}}
  .charcard__lvl{position:absolute;z-index:3;bottom:6px;right:6px;background:var(--tier-c);color:#fff;
    font-size:11px;font-weight:800;padding:2px 8px;border-radius:999px;box-shadow:0 2px 6px rgba(0,0,0,.18)}
  .charcard__title{font-weight:800;font-size:15px;color:var(--ds-ink);margin:2px 0 10px}
  .charcard__xpwrap{padding:0 10px}
  .charcard__xpbar{height:9px;border-radius:999px;background:var(--ds-hairline,rgba(0,0,0,0.08));overflow:hidden}
  .charcard__xpfill{height:100%;border-radius:999px;background:linear-gradient(90deg,var(--tier-c),color-mix(in srgb,var(--tier-c) 55%,#fff));transition:width .6s cubic-bezier(.22,1,.36,1)}
  .charcard__xptxt{font-size:11px;color:var(--ds-muted);margin-top:5px}
  .charcard__stats{display:flex;justify-content:center;gap:18px;margin:13px 0 4px}
  .charcard__stats div{display:flex;flex-direction:column}
  .charcard__stats b{font-size:18px;font-weight:800;color:var(--ds-ink)} .charcard__stats span{font-size:10.5px;color:var(--ds-muted)}
  .charcard__hint{font-size:11px;color:var(--ds-muted);margin-top:8px;line-height:1.5}
  /* 게이미피케이션: Flow 단계·오늘의 미션·배지 컬렉션 */
  .charcard__flow{font-size:10px;font-weight:700;color:var(--tier-c);background:color-mix(in srgb,var(--tier-c) 14%,transparent);padding:1px 7px;border-radius:9999px;vertical-align:middle}
  .charcard__mission{display:flex;align-items:center;gap:8px;margin-top:12px;padding:9px 12px;border-radius:var(--ds-radius-md);
    background:var(--ds-primary-tint);cursor:pointer;text-align:left;transition:background var(--ds-motion-fast)}
  .charcard__mission:hover{background:color-mix(in srgb,var(--ds-primary) 20%,transparent)}
  .charcard__mission-ic{flex:none;font-size:15px}
  .charcard__mission-tx{flex:1;font-family:var(--ds-font-game);font-size:11.5px;font-weight:500;color:var(--ds-text-secondary);line-height:1.45;word-break:keep-all;letter-spacing:-.01em}
  .charcard__mission-cta{flex:none;font-size:11px;font-weight:700;color:var(--ds-primary)}
  .charcard__missions{display:flex;flex-direction:column;gap:5px;margin-top:12px}
  .charcard__missions .charcard__mission{margin-top:0;padding:7px 12px}
  .charcard__mission.is-done{background:color-mix(in srgb,var(--ds-success, #18ba45) 12%,transparent)}
  .charcard__mission.is-done .charcard__mission-cta{color:var(--ds-success, #18ba45)}
  .charcard__badges-hd{margin-top:14px;text-align:left;font-size:11px;font-weight:600;color:var(--ds-muted)}
  .charcard__badges-hd b{color:var(--ds-ink)}
  .charcard__badges{margin-top:9px;display:grid;grid-template-columns:repeat(auto-fill,minmax(76px,1fr));gap:10px}
  /* 입체 오브 배지: 광택 라디얼 + 외부 글로우 + 내부 하이라이트/그림자 */
  .gbadge{display:flex;flex-direction:column;align-items:center;gap:7px;padding:12px 6px 11px;border-radius:16px;
    background:var(--ds-surface-table);border:1px solid var(--ds-border-card);transition:transform var(--ds-motion-fast) var(--ds-ease-standard)}
  .gbadge.got{background:color-mix(in srgb,var(--bc) 7%,var(--ds-surface));border-color:color-mix(in srgb,var(--bc) 26%,var(--ds-border-card))}
  .gbadge.got:hover{transform:translateY(-2px)}
  .gbadge__orb{width:48px;height:48px;border-radius:50%;display:flex;align-items:center;justify-content:center;position:relative;
    background:radial-gradient(circle at 38% 28%, color-mix(in srgb,var(--bc,#9aa0a6) 45%,#fff) 0%, var(--bc,#9aa0a6) 55%, color-mix(in srgb,var(--bc,#9aa0a6) 72%,#000) 100%);
    box-shadow:0 6px 13px -3px color-mix(in srgb,var(--bc,#9aa0a6) 60%,transparent),
      inset 0 2px 3px rgba(255,255,255,.6), inset 0 -5px 8px rgba(0,0,0,.3)}
  .gbadge__orb::after{content:'';position:absolute;top:6px;left:9px;width:20px;height:12px;border-radius:50%;
    background:radial-gradient(ellipse at center, rgba(255,255,255,.55), transparent 70%);pointer-events:none}
  .gbadge__ic{position:relative;z-index:1;font-size:22px;line-height:1;filter:drop-shadow(0 1px 1.5px rgba(0,0,0,.3))}
  .gbadge.locked{opacity:.72}
  .gbadge.locked .gbadge__orb{--bc:#9aa0a6;box-shadow:inset 0 2px 3px rgba(255,255,255,.35),inset 0 -4px 7px rgba(0,0,0,.22)}
  .gbadge.locked .gbadge__ic{font-size:15px;filter:none}
  .gbadge__label{font-size:11px;font-weight:700;color:var(--ds-ink);text-align:center;line-height:1.2}
  .gbadge.locked .gbadge__label{color:var(--ds-muted)}
  .gbadge__exp{font-size:9.5px;font-weight:800;letter-spacing:.02em;color:#b8791f;background:rgba(255,148,41,.18);padding:1.5px 7px;border-radius:9999px}
  .gbadge.locked .gbadge__exp{color:var(--ds-muted);background:var(--ds-surface-table)}
  /* 배지 달성 축하 오버레이 */
  /* 검수 완료 +PT 리워드 토스트(위로 떠오르며 페이드) */
  /* 배지 전체 보기 모달 */
  .badgemodal{background:var(--ds-layer-popup,var(--ds-surface-white));border-radius:var(--ds-radius-xl);box-shadow:var(--ds-shadow-high);
    width:min(920px,94vw);max-height:88vh;display:flex;flex-direction:column;overflow:hidden;padding:22px 24px 8px}
  .badgemodal__hd{display:flex;align-items:center;gap:12px}
  .badgemodal__ttl{font-family:var(--ds-font-display);font-size:20px;font-weight:800;color:var(--ds-ink)}
  .badgemodal__lvwrap{display:flex;align-items:center;gap:10px;margin-left:auto}
  .badgemodal__lv{font-family:var(--ds-font-display);font-weight:800;font-size:13px;color:#b8791f;background:rgba(255,148,41,.18);padding:3px 12px;border-radius:9999px}
  .badgemodal__count{font-family:var(--ds-font-display);font-size:15px;color:var(--ds-muted)} .badgemodal__count b{color:var(--ds-ink)}
  .badgemodal__xp{margin:14px 0 4px} .badgemodal__xptxt{margin-top:6px;font-size:12px;color:var(--ds-muted)} .badgemodal__xptxt b{color:var(--ds-ink)}
  .badgemodal__grid{margin-top:10px;overflow:auto;display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:6px 2px 16px}
  @media (max-width:720px){.badgemodal__grid{grid-template-columns:repeat(2,1fr)}}
  .bmcard{position:relative;border:1px solid var(--ds-hairline);border-radius:var(--ds-radius-lg);padding:18px 14px 14px;
    display:flex;flex-direction:column;align-items:center;text-align:center;gap:6px;background:var(--ds-surface-white);transition:transform .15s,box-shadow .15s}
  .bmcard.got{border-color:color-mix(in srgb,var(--bc,#1e84ff) 45%,transparent);box-shadow:0 8px 22px -12px color-mix(in srgb,var(--bc,#1e84ff) 60%,transparent)}
  .bmcard.got:hover{transform:translateY(-3px)}
  .bmcard.locked{opacity:.72;filter:saturate(.5)}
  .bmcard__cat{position:absolute;top:9px;left:11px;font-size:9px;font-weight:800;color:var(--ds-muted);letter-spacing:.03em}
  .bmcard__orb{width:56px;height:56px;margin:6px 0 2px}
  .bmcard__label{font-family:var(--ds-font-display);font-size:14px;font-weight:800;color:var(--ds-ink)}
  .bmcard__exp{font-family:var(--ds-font-display);font-size:11px;font-weight:800;color:#b8791f;background:rgba(255,148,41,.16);padding:2px 9px;border-radius:9999px}
  .bmcard__desc{font-size:11.5px;color:var(--ds-muted);line-height:1.45}
  .bmcard__foot{margin-top:4px;font-size:11.5px;font-weight:800;color:var(--ds-muted);font-family:var(--ds-font-display)}
  .bmcard__foot.is-got{color:#0f8f36}
  [data-theme='dark'] .badgemodal__lv,[data-theme='dark'] .bmcard__exp{color:#ffc266}
  .pttoast{position:fixed;left:50%;bottom:96px;z-index:96;pointer-events:none;display:inline-flex;align-items:center;gap:9px;
    padding:11px 22px;border-radius:9999px;background:linear-gradient(135deg,#22c24e,#0f8f36);color:#fff;
    font-family:var(--ds-font-display);font-weight:800;box-shadow:0 10px 26px -6px rgba(24,186,69,.6),0 0 0 4px rgba(24,186,69,.18);
    animation:pt-rise 1.7s cubic-bezier(.2,.8,.2,1) forwards;will-change:transform,opacity}
  .pttoast__spark{font-size:18px} .pttoast__pt{font-size:18px;letter-spacing:.01em}
  .pttoast__lbl{font-size:12px;font-weight:700;opacity:.92;font-family:var(--ds-font)}
  @keyframes pt-rise{0%{opacity:0;transform:translate(-50%,22px) scale(.82)}14%{opacity:1;transform:translate(-50%,0) scale(1.06)}
    28%{transform:translate(-50%,0) scale(1)}78%{opacity:1;transform:translate(-50%,-32px)}100%{opacity:0;transform:translate(-50%,-60px)}}
  @media (prefers-reduced-motion:reduce){.pttoast{animation:none}}
  .badgeburst{position:fixed;inset:0;z-index:95;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.4);backdrop-filter:blur(3px)}
  .badgeburst__card{position:relative;background:var(--ds-layer-popup);border-radius:var(--ds-radius-xl);padding:34px 40px 30px;text-align:center;
    box-shadow:var(--ds-shadow-high),0 0 0 1px color-mix(in srgb,var(--bc,#1e84ff) 30%,transparent);
    animation:bb-pop .5s cubic-bezier(.2,1.5,.4,1)}
  .badgeburst__orb{width:88px;height:88px;border-radius:50%;margin:0 auto 14px;display:flex;align-items:center;justify-content:center;position:relative;
    background:radial-gradient(circle at 38% 28%, color-mix(in srgb,var(--bc,#1e84ff) 45%,#fff) 0%, var(--bc,#1e84ff) 55%, color-mix(in srgb,var(--bc,#1e84ff) 72%,#000) 100%);
    box-shadow:0 10px 24px -4px color-mix(in srgb,var(--bc,#1e84ff) 60%,transparent), inset 0 3px 5px rgba(255,255,255,.6), inset 0 -7px 11px rgba(0,0,0,.3);
    animation:bb-bounce 1.6s ease-in-out infinite}
  .badgeburst__ic{font-size:40px;filter:drop-shadow(0 2px 2px rgba(0,0,0,.3))}
  .badgeburst__ttl{font-size:14px;font-weight:800;color:var(--bc,#1e84ff);letter-spacing:.02em}
  .badgeburst__label{font-size:22px;font-weight:800;color:var(--ds-ink);margin-top:4px}
  .badgeburst__exp{display:inline-block;margin-top:10px;font-size:13px;font-weight:800;color:#b8791f;background:rgba(255,148,41,.18);padding:3px 12px;border-radius:9999px}
  .badgeburst__hint{font-size:12px;color:var(--ds-muted);margin-top:10px}
  .badgeburst__spark{position:absolute;top:50%;left:50%;width:8px;height:8px;border-radius:50%;background:var(--bc,#1e84ff);
    transform-origin:0 0;opacity:0;animation:bb-spark 1s ease-out forwards;animation-delay:.12s;
    transform:rotate(calc(var(--i)*30deg)) translateX(0)}
  @keyframes bb-pop{0%{opacity:0;transform:scale(.7) translateY(10px)}100%{opacity:1;transform:scale(1) translateY(0)}}
  @keyframes bb-bounce{0%,100%{transform:translateY(0)}50%{transform:translateY(-8px)}}
  @keyframes bb-spark{0%{opacity:1;transform:rotate(calc(var(--i)*30deg)) translateX(30px) scale(1)}100%{opacity:0;transform:rotate(calc(var(--i)*30deg)) translateX(120px) scale(.3)}}
  @media (prefers-reduced-motion:reduce){.badgeburst__orb,.badgeburst__spark,.badgeburst__card{animation:none}}
  .conn-chip{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;color:var(--ds-ink);white-space:nowrap}
  .conn-chip--off{color:var(--ds-muted)}
  /* 본문 영역: 뷰포트 남은 높이를 꽉 채우되 자체는 스크롤 안 함(overflow:hidden) 사이드바·콘텐츠가 각자 내부 스크롤 */
  /* padding-top = 톱바(고정)와 사이드바·콘텐츠 사이 여백(좌우 22px 와 균형) */
  .appbody{flex:1;min-height:0;display:grid;grid-template-columns:232px minmax(0,1fr);gap:18px;
    padding:22px 22px 0;overflow:hidden;align-items:stretch}
  /* 사이드바: 고정 내용이 길면 자체 스크롤(스크롤바 숨김) */
  .leftcol{grid-column:1;min-height:0;overflow-y:auto;display:flex;flex-direction:column;scrollbar-width:none}
  .leftcol::-webkit-scrollbar{display:none}
  /* 콘텐츠(위젯) 영역: 여기에만 스크롤 적용 */
  .home{grid-column:2;min-width:0;min-height:0;overflow-y:auto;overflow-x:hidden;padding:0 2px 26px 0}
  .ds-sidebar .brand{display:flex;align-items:center;gap:10px;padding:4px 8px 12px;font-family:var(--ds-font-sans);font-weight:600;font-size:var(--ds-size-subtitle)}
  .ds-sidebar .brand .logo{width:32px;height:32px;display:flex;align-items:center;justify-content:center;flex:none}
  .ds-sidebar .brand .logo img{width:100%;height:100%}
  .ds-sidebar .brand small{display:block;font-weight:400;font-size:11px;color:var(--ds-muted)}
  .ds-navitem__icon{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;flex:none;color:var(--ds-muted)}
  .ds-navitem--active .ds-navitem__icon{color:var(--ds-primary)}
  .ds-navitem__icon svg{width:15px;height:15px}
  .nav-kind{margin-left:auto;font-size:9px;font-weight:600;letter-spacing:.04em;color:var(--ds-placeholder);
    border:1px solid var(--ds-hairline);border-radius:var(--ds-radius-full);padding:1px 6px}
  .ds-navitem--active .nav-kind{color:var(--ds-primary);border-color:rgba(30,132,255,.3)}
  .side-assistant{margin-top:12px}
  /* 도우미 = 사이드바·타이틀과 같은 고정 계층색(웜 그레이지) 캔버스의 흰 위젯과 색으로 구분 */
  .side-assistant__card{width:100%;display:flex;align-items:center;gap:11px;padding:12px;border:1px solid var(--ds-fixed-bd);cursor:pointer;text-align:left;
    border-radius:var(--ds-radius-xl);background:var(--ds-fixed-bg);color:var(--ds-ink);
    box-shadow:0 1px 2px rgba(0,0,0,.05),0 8px 20px -10px rgba(0,0,0,.12),var(--ds-highlight);transition:transform var(--ds-motion-fast) var(--ds-ease-standard)}
  .side-assistant__card:hover{transform:translateY(-2px)}
  .side-assistant__char{width:54px;height:54px;flex:none}
  .side-assistant__char img{width:100%;height:100%;object-fit:contain}
  .side-assistant__txt{min-width:0;display:flex;flex-direction:column;gap:2px}
  .side-assistant__t{display:flex;align-items:center;gap:7px;font-family:var(--ds-font-sans);font-size:var(--ds-size-label);font-weight:700;color:var(--ds-ink)}
  .side-assistant__s{font-size:11px;color:var(--ds-muted)}
  /* 야구카드형 프로필: 캐릭터를 라운드 타일에 얹고, 이름 옆 '에이전트' 태그 + 편집 */
  .side-assistant__card--profile{position:relative;align-items:center}
  .side-assistant__card--profile .side-assistant__char{width:52px;height:52px;border-radius:12px;background:var(--ds-primary-tint);
    display:flex;align-items:center;justify-content:center;padding:3px;box-shadow:inset 0 0 0 1px var(--ds-hairline)}
  .side-assistant__tag{font-size:10px;font-weight:700;color:var(--ds-primary);background:var(--ds-primary-tint);
    padding:1px 6px;border-radius:9999px;letter-spacing:.01em}
  .side-assistant__edit{position:absolute;top:9px;right:9px;width:22px;height:22px;display:inline-flex;align-items:center;justify-content:center;
    border-radius:7px;color:var(--ds-muted);opacity:0;transition:opacity var(--ds-motion-fast)}
  .side-assistant__card--profile:hover .side-assistant__edit{opacity:1}
  .side-assistant__edit:hover{background:var(--ds-hairline-soft);color:var(--ds-ink)}
  .side-assistant__spin{width:12px;height:12px;border:2px solid rgba(0,0,0,.14);border-top-color:var(--ds-primary);border-radius:50%;animation:ds-spin .7s linear infinite;flex:none}
  /* ── 게임형 프로필 카드(사이드 하단): 글로우 캐릭터 + 획득(+XP) 뱃지 + 점수. 지겨운 검수 → 성장 재미 ── */
  .side-profile{position:relative;width:100%;display:flex;flex-direction:column;align-items:center;gap:5px;
    padding:16px 12px 13px;border:1px solid var(--ds-fixed-bd);border-radius:var(--ds-radius-xl);
    background:var(--ds-fixed-bg);color:var(--ds-ink);cursor:pointer;overflow:hidden;text-align:center;
    box-shadow:0 1px 2px rgba(0,0,0,.05),0 8px 20px -10px rgba(0,0,0,.12),var(--ds-highlight);
    transition:transform var(--ds-motion-fast) var(--ds-ease-standard),box-shadow .2s}
  .side-profile:hover{transform:translateY(-2px);box-shadow:0 2px 4px rgba(0,0,0,.06),0 14px 28px -12px rgba(0,0,0,.2),var(--ds-highlight)}
  .side-profile__avatar{position:relative;width:76px;height:76px;display:flex;align-items:center;justify-content:center;z-index:1}
  .side-profile__avatar img{width:100%;height:100%;object-fit:contain;filter:drop-shadow(0 6px 10px rgba(0,0,0,.18));animation:sp-float 3.4s ease-in-out infinite}
  .side-profile__glow{position:absolute;top:8px;left:50%;transform:translateX(-50%);width:96px;height:96px;border-radius:50%;
    background:radial-gradient(circle,color-mix(in srgb,var(--tier-c,#ffb020) 52%,transparent) 0%,transparent 68%);
    filter:blur(5px);opacity:.7;z-index:0;animation:sp-pulse 3s ease-in-out infinite}
  .side-profile[data-tier="1"]{--tier-c:#1e84ff} .side-profile[data-tier="2"]{--tier-c:#5c77ff}
  .side-profile[data-tier="3"]{--tier-c:#ff9429} .side-profile[data-tier="4"]{--tier-c:#a05cff} .side-profile[data-tier="5"]{--tier-c:#ffb020}
  .side-profile__lvl{position:absolute;bottom:-3px;left:50%;transform:translateX(-50%);font-size:10px;font-weight:800;color:#fff;
    background:var(--tier-c,#ffb020);padding:1px 9px;border-radius:9999px;box-shadow:0 2px 5px rgba(0,0,0,.28);white-space:nowrap;z-index:2}
  .side-profile__gain{position:absolute;top:9px;left:10px;font-size:11px;font-weight:800;color:#0f8f36;
    background:rgba(24,186,69,.16);border:1px solid rgba(24,186,69,.42);padding:1px 8px;border-radius:9999px;
    box-shadow:0 2px 6px rgba(24,186,69,.32);animation:sp-float 3s ease-in-out infinite;z-index:4;pointer-events:none}
  .side-profile__name{display:flex;align-items:center;gap:6px;flex-wrap:wrap;justify-content:center;font-size:13px;font-weight:800;color:var(--ds-ink);margin-top:5px}
  .side-profile__tag{font-size:9.5px;font-weight:700;color:var(--ds-primary);background:var(--ds-primary-tint);padding:1px 6px;border-radius:9999px}
  .side-profile__score{font-size:16px;font-weight:800;color:var(--ds-ink);letter-spacing:-.01em}
  .side-profile__score b{color:var(--ds-primary-deep)}
  .side-profile__sub{font-size:10.5px;color:var(--ds-muted)}
  .side-profile__edit{position:absolute;top:9px;right:9px;width:22px;height:22px;display:inline-flex;align-items:center;justify-content:center;
    border-radius:7px;color:var(--ds-muted);opacity:0;transition:opacity .15s;z-index:3}
  .side-profile:hover .side-profile__edit{opacity:1}
  .side-profile__edit:hover{background:var(--ds-hairline-soft);color:var(--ds-ink)}
  @keyframes sp-float{0%,100%{transform:translateY(0)}50%{transform:translateY(-4px)}}
  @keyframes sp-pulse{0%,100%{opacity:.58;transform:translateX(-50%) scale(1)}50%{opacity:.9;transform:translateX(-50%) scale(1.09)}}
  @media (prefers-reduced-motion:reduce){.side-profile__avatar img,.side-profile__glow,.side-profile__gain{animation:none}}
  .side-assistant__idle{width:8px;height:8px;border-radius:50%;background:var(--ds-primary);box-shadow:0 0 0 3px rgba(30,132,255,.16);flex:none}

  /* 상단 메뉴 위젯 */
  /* 고정 위젯(사이드바·타이틀바) = teal 틴트로 시스템 크롬임을 색으로 구분
     캔버스/콘텐츠 위젯 = 화이트, 도우미 = 다크, 페이지 = 크림 */
  /* 고정/콘텐츠 구분 = 채움색이 아니라 '라운드 박스 테두리 색'으로
     모든 영역 채움은 흰색 동일, 고정 위젯(사이드바·타이틀·도우미)만 teal 테두리 */
  :root{--ds-fixed-bg:var(--ds-surface-white);--ds-fixed-bd:rgba(30,132,255,.34)}
  .ds-sidebar{background:var(--ds-fixed-bg)!important;border:1px solid var(--ds-fixed-bd)!important}
  .ds-widget,.panel{background:var(--ds-surface-white)}
  .homehead{position:relative;z-index:20;display:flex;align-items:center;justify-content:space-between;gap:16px;
    padding:11px 18px;background:var(--ds-fixed-bg);border:1px solid var(--ds-fixed-bd);border-radius:var(--ds-radius-xl);
    box-shadow:0 1px 2px rgba(0,0,0,.06),0 10px 24px -10px rgba(0,0,0,.14),var(--ds-highlight)}
  .homehead__title{font-family:var(--ds-font-display);font-size:var(--ds-size-label);font-weight:700;letter-spacing:-.01em;line-height:1.2;color:var(--ds-ink)}
  .homehead__sub{font-size:var(--ds-size-caption);color:var(--ds-muted);margin-top:3px}
  .homehead__tools{display:flex;align-items:center;gap:6px;flex:none;position:relative}
  .homehead__status{display:inline-flex;align-items:center;gap:7px;font-size:12px;color:var(--ds-muted);white-space:nowrap;
    margin-right:10px;padding-right:14px;border-right:1px solid var(--ds-hairline)}
  .addmenu{position:absolute;top:44px;right:0;z-index:50;width:220px;background:var(--ds-surface-white);border:1px solid var(--ds-hairline);
    border-radius:var(--ds-radius-lg);box-shadow:var(--ds-shadow-elevated);padding:6px;display:none}
  .addmenu.open{display:block}
  .addmenu button{display:flex;width:100%;align-items:center;gap:8px;border:0;background:transparent;border-radius:var(--ds-radius-md);
    padding:8px 10px;font-family:var(--ds-font-sans);font-size:var(--ds-size-label);color:var(--ds-ink);cursor:pointer;text-align:left}
  .addmenu button:hover{background:var(--ds-hairline-soft)}

  /* 콘텐츠 영역(모듈/홈 그리드 공용) · 타이틀바와 충분히 띄움 */
  .canvas{margin-top:0;min-width:0;margin-left:0;padding-left:0;display:flex;flex-direction:column;gap:20px}
  .canvas>.ds-widgetgrid{order:0}   /* 홈: 아레나(order:-1) 아래에 위젯 그리드 */
  .ds-widgetgrid{margin-top:0}

  /* 위젯 내부 */
  .w-stat-v{font-family:var(--ds-font-sans);font-size:var(--ds-size-heading-lg);font-weight:600;letter-spacing:-.01em;color:var(--ds-ink)}
  .w-stat-v.accent{color:var(--ds-primary)}
  .w-stat-l{font-size:var(--ds-size-caption);color:var(--ds-muted);margin-top:2px}
  .w-run{display:grid;grid-template-columns:30px 1fr;gap:10px;align-items:center;padding:7px 0}
  .w-run+.w-run{border-top:1px solid var(--ds-hairline-soft)}
  .w-run__av{width:30px;height:30px;border-radius:50%;background:var(--ds-primary-tint);display:flex;align-items:center;justify-content:center}
  .w-run__av img{width:78%;height:78%}
  .w-run__t{font-size:var(--ds-size-caption);color:var(--ds-ink);font-weight:500}
  .w-ev{display:grid;grid-template-columns:46px 1fr;gap:8px;padding:7px 0;font-size:var(--ds-size-caption)}
  .w-ev+.w-ev{border-top:1px solid var(--ds-hairline-soft)}
  .w-ev__t{color:var(--ds-placeholder);font-variant-numeric:tabular-nums}
  .w-ev__b{color:var(--ds-ink)}
  .w-agent{display:flex;align-items:center;gap:9px;padding:7px 0}
  .w-agent+.w-agent{border-top:1px solid var(--ds-hairline-soft)}
  .w-agent__av{width:30px;height:30px;border-radius:50%;background:var(--ds-primary-tint);display:flex;align-items:center;justify-content:center;flex:none}
  .w-agent__av img{width:78%;height:78%}
  .w-agent__n{font-family:var(--ds-font-sans);font-size:var(--ds-size-caption);font-weight:500;color:var(--ds-ink)}
  .w-agent__r{font-size:10px;color:var(--ds-muted)}
  .w-agent__s{margin-left:auto}
  .center{height:100%;display:flex;align-items:center;justify-content:center}

  @media (max-width:900px){.appbody{grid-template-columns:1fr}.leftcol{display:none}.home{grid-column:1}.topbar__brand{width:auto}}

  /* ════ 공통 규칙 · 위젯 종류 무관 동일 타이포·정렬 기준(단일 소스) ════ */
  /* ① 타이틀: 모든 위젯/패널/타이틀바 14px·600 동일 */
  .ds-widget__title,.homehead__title{font-size:13px;font-weight:700;line-height:1.3;color:var(--ds-ink);letter-spacing:-.01em}
  /* ② 라벨(섹션·필드·표헤더·통계·eyebrow): 12px·600·muted·비대문자 동일 */
  .lbl,.ds-eyebrow,.tile .t,.w-stat-l,.ds-stat__label,.ds-table th{font-size:12px;font-weight:600;letter-spacing:0;text-transform:none;color:var(--ds-muted)}
  .lbl{display:block;margin-bottom:8px}
  /* ③ 본문·값: 14px 동일 */
  .panel-bd,.ds-widget__body{font-size:14px;line-height:1.55;color:var(--ds-body)}
  .ds-table td{font-size:13px;color:var(--ds-body)}
  /* ④ 행(키–값) 정렬: 키열 폭·간격·패딩 동일(본문 좌측선에 정렬) */
  .drow{grid-template-columns:116px 1fr;gap:14px;padding:12px 0}
  .drow .k{font-size:12px;font-weight:600;color:var(--ds-muted);padding-top:1px}
  .drow .v,.drow .v p{font-size:14px;color:var(--ds-ink);line-height:1.55}
  /* ⑤ 통계 수치: 동일 크기 */
  .tile .n,.w-stat-v,.ds-stat__value{font-size:24px;font-weight:600;letter-spacing:-.01em;line-height:1.1}
  /* ⑥ 캡션·설명·힌트: 13px·muted 동일 */
  .ds-hint,.secdesc,.rc-d,.homehead__sub{font-size:13px;color:var(--ds-muted);line-height:1.5}
  /* ⑦ 위젯 간 세로 간격: 동일 리듬(타이트 금지) */
  .canvas .space-y-4>:not([hidden])~:not([hidden]){margin-top:28px}
  .ds-widgetgrid{gap:20px}
  /* ⑧ 위젯 헤드 캐릭터 아바타(귀엽게) · 라운드, 투명배경 캐릭터 */
  .wz-char{width:30px;height:30px;border-radius:50%;background:var(--ds-hairline-soft);overflow:hidden;display:inline-flex;align-items:center;justify-content:center;flex:none}
  .wz-char img{width:88%;height:88%;object-fit:contain}
  .wz-char--sm{width:24px;height:24px}
  /* 카드 속성 클러스터(캐릭터+기능/정보 칩) · 종류별 캐릭터 통일 · 항상 헤드 우측 끝 고정 */
  .ds-cardtype{display:inline-flex;align-items:center;gap:6px;margin-left:10px;padding-left:10px;flex:none;
    border-left:1px solid var(--ds-hairline-soft)}
  /* 헤더 그룹 구분선(정보·컨트롤·카드속성 사이 동일 간격·구분선 원칙) */
  .hd-divider{align-self:center;width:1px;height:18px;background:var(--ds-hairline-soft);margin:0 4px;flex:none}
  /* 채팅 퀵액션 = 칩 */
  .ds-chat__quick{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
  .chatchip{display:inline-flex;align-items:center;gap:4px;height:28px;padding:0 12px;border-radius:9999px;
    border:1px solid var(--ds-hairline);background:var(--ds-surface-white);color:var(--ds-text-secondary);
    font-family:var(--ds-font-display);font-size:12px;font-weight:700;cursor:pointer;white-space:nowrap;
    box-shadow:0 1px 1px rgba(16,24,40,.03);transition:border-color .12s,color .12s,background .12s,transform .1s}
  .chatchip:hover{border-color:var(--ds-primary);color:var(--ds-primary-deep);background:var(--ds-primary-tint)}
  .chatchip:active{transform:translateY(1px)}
  /* 서술형 설명 = 상단 불릿(문장 단위·마침표). 텍스트 뭉침 방지 */
  .ds-bullets{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:5px}
  .ds-bullets li{position:relative;padding-left:15px;font-size:12.5px;line-height:1.55;color:var(--ds-muted)}
  .ds-bullets li::before{content:"";position:absolute;left:3px;top:7px;width:4px;height:4px;border-radius:50%;background:var(--ds-primary)}
  .ds-bullets b{color:var(--ds-ink);font-weight:700}
  /* 패널 타이틀은 카드 구분이 명확하도록 크게(전 페이지) · 홈 위젯 타이틀보다 한 단계 위 위계 */
  .panel-hd>.ds-widget__title{margin-right:auto;font-size:16.5px;font-weight:700;letter-spacing:-.015em}
  .panel-hd>.ds-widget__title span{font-size:inherit;font-weight:inherit}
  .panel-hd>.ds-widget__actions,.ds-widget__head>.ds-widget__actions{margin-left:auto}
  /* ⑨ 패널 내부 좌측 정렬선 통일(18px) · 타이틀·본문·표·행이 한 선에 정렬
     패널 직속 표(셀패딩 12)·행(0)은 타이틀(18)보다 왼쪽이라 18로 보정 panel-bd 안은 이미 18. */
  .panel > .drow{padding-left:18px;padding-right:18px}
  .panel > .overflow-auto > .ds-table tr > :first-child,
  .panel > .ds-table tr > :first-child{padding-left:18px}
  .panel > .overflow-auto > .ds-table tr > :last-child,
  .panel > .ds-table tr > :last-child{padding-right:18px}
  .panel > .overflow-auto, .panel > .ds-table{margin-top:10px}
  .ds-table th{padding:11px 14px 12px}
  .ds-table td{padding:12px 14px}
  .ds-table thead tr{border-bottom:1px solid var(--ds-divider-section, rgba(0,0,0,0.08))}
</style>
</head>
<body class="antialiased">
<div class="ds-grain" aria-hidden="true"></div>
<div x-data="prismApp()" class="appshell" data-theme="light">

  <!-- ━━━━━ 상단 바: 로고 | 타이틀(고정) | 도구 ━━━━━ -->
  <header class="topbar">
    <div class="topbar__brand" x-on:click="selectMod('home')" style="cursor:pointer" role="button" aria-label="홈으로">
      <img class="topbar__logo topbar__logo--light" src="/vendor/prism-logo-tagline-light.png" alt="Prism · A lens on content & users">
      <img class="topbar__logo topbar__logo--dark" src="/vendor/prism-logo-tagline-dark.png" alt="Prism · A lens on content & users"></div>
    <div class="topbar__title">
      <div class="homehead__title" x-text="modLabel"></div>
      <div class="homehead__sub" x-text="modSub"></div>
    </div>
    <div class="topbar__tools">
      <!-- 사용자 식별(이름·캐릭터)은 사이드바 프로필 카드로 이관. 상단바엔 미표시 -->
      <!-- 연결 신호등: 모델별 표시 없이 단일 신호 dot (초록=연결·회색=미설정/서버관리·주황=MOCK). 상세는 호버 -->
      <button type="button" class="topbar__conn topbar__conn--dot" x-on:click="if (navVisible('sysadmin')) selectMod('system')"
        x-bind:data-tip="cfg.forcedMock ? 'MOCK (강제)' : (connCount ? '연결됨' : (cfg.keyManagedByServer ? '서버 관리 (연결됨)' : '키 미설정'))" data-tip-pos="bottom" aria-label="연결 상태">
        <span class="ds-statusdot" x-bind:class="cfg.forcedMock ? 'ds-statusdot--mock' : ((connCount || (cfg.keyManagedByServer && cfg.hasKey)) ? 'ds-statusdot--ok' : 'ds-statusdot--mock')"><span class="ds-statusdot__dot"></span></span>
      </button>
      <a href="/report" target="_blank" rel="noreferrer" class="ds-iconbtn ds-iconbtn--bordered" data-tip="전체 리포트 생성·보기" data-tip-pos="bottom" aria-label="전체 리포트"><svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M6 3h8l4 4v14H6z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M9 12h6M9 16h6M9 8h3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></a>
      <button type="button" x-show="false" x-on:click.stop="addMenuOpen = !addMenuOpen" class="ds-iconbtn ds-iconbtn--bordered" data-tip="위젯 추가" data-tip-pos="bottom" aria-label="위젯 추가"><svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg></button>
      <button type="button" x-show="false" x-on:click="editing = !editing" x-bind:class="editing ? 'ds-iconbtn ds-iconbtn--bordered ds-iconbtn--active' : 'ds-iconbtn ds-iconbtn--bordered'" x-bind:data-tip="editing ? '편집 완료' : '편집'" data-tip-pos="bottom" aria-label="편집"><svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M4 20h4L19 9l-4-4L4 16v4Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg></button>
      <button type="button" x-show="navVisible('sysadmin')" class="ds-iconbtn ds-iconbtn--bordered" x-on:click="selectMod('system')" data-tip="시스템 설정" data-tip-pos="bottom" aria-label="시스템 설정"><svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" stroke="currentColor" stroke-width="1.5"/><path d="M19.4 13a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V13Z" stroke="currentColor" stroke-width="1.3"/></svg></button>
      <button type="button" class="ds-iconbtn ds-iconbtn--bordered" x-on:click="toggleTheme()" x-bind:data-tip="theme === 'dark' ? '라이트 모드' : '다크 모드'" data-tip-pos="bottom" aria-label="테마 전환">
        <svg x-show="theme !== 'dark'" width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>
        <svg x-show="theme === 'dark'" x-cloak width="18" height="18" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="4" stroke="currentColor" stroke-width="1.6"/><path d="M12 3v2M12 19v2M5 12H3M21 12h-2M6 6l1.4 1.4M16.6 16.6 18 18M18 6l-1.4 1.4M7.4 16.6 6 18" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
      </button>
      <button type="button" x-show="reviewer" class="ds-iconbtn ds-iconbtn--bordered" x-on:click="logout()" data-tip="로그아웃" data-tip-pos="bottom" aria-label="로그아웃"><svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M15 12H4m0 0 4-4m-4 4 4 4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M10 4h8a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
      <div class="addmenu" x-bind:class="addMenuOpen ? 'open' : ''" x-on:click.outside="addMenuOpen = false">
        <template x-for="w in homeCatalog" x-bind:key="w.id">
          <button type="button" x-on:click="addWidget(w.id)" x-bind:disabled="hasWidget(w.id)" x-bind:style="hasWidget(w.id) ? 'opacity:.45;cursor:default' : ''">
            <span x-text="(hasWidget(w.id) ? '✓ ' : '＋ ') + w.label"></span>
          </button>
        </template>
      </div>
    </div>
  </header>

  <!-- 실시간 협업 토스트: 다른 검수자의 검수 활동 -->
  <div class="live-toast" x-show="liveMsg" x-cloak x-transition.opacity>
    <span class="live-toast__dot"></span><span x-text="liveMsg"></span>
  </div>

  <!-- 전역 에러 토스트(조용한 실패 노출 · A-2) -->
  <div class="err-toast" x-show="errMsg" x-cloak x-transition.opacity x-on:click="errMsg=''">
    <span class="err-toast__ic">⚠</span><span x-text="errMsg"></span>
  </div>

  <!-- 검수 완료 점수 상승(+PT) 리워드 토스트 · key 로 연속 검수 시 애니메이션 재시작 -->
  <template x-if="ptToast">
    <div class="pttoast" x-bind:key="ptToast.id">
      <span class="pttoast__spark">✨</span>
      <span class="pttoast__pt" x-text="'+' + ptToast.pts + ' PT'"></span>
      <span class="pttoast__lbl" x-show="ptToast.label" x-text="ptToast.label"></span>
    </div>
  </template>

  <!-- 배지 전체 보기 모달(목록 + 달성 여부·진행도) -->
  <div class="ds-dialog-backdrop" x-show="badgeModalOpen" x-cloak x-transition.opacity x-on:mousedown.self="badgeModalOpen=false" style="z-index:74">
    <div class="badgemodal" x-show="badgeModalOpen" x-transition>
      <div class="badgemodal__hd">
        <div class="badgemodal__ttl">배지 컬렉션</div>
        <template x-if="arenaMe">
          <div class="badgemodal__lvwrap">
            <span class="badgemodal__lv" x-text="'Lv.' + arenaMe.level"></span>
            <span class="badgemodal__count"><b x-text="badgeGot"></b> / <span x-text="badges().length"></span></span>
          </div>
        </template>
        <button type="button" class="ds-iconbtn" x-on:click="badgeModalOpen=false" aria-label="닫기"><svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></button>
      </div>
      <template x-if="arenaMe">
        <div class="badgemodal__xp">
          <div class="charcard__xpbar"><div class="charcard__xpfill" x-bind:style="'width:' + xpPct(arenaMe) + '%'"></div></div>
          <div class="badgemodal__xptxt">다음 레벨까지 <b x-text="xpToNext(arenaMe) + 'pt'"></b> · 순위 #<span x-text="arenaMyRank"></span></div>
        </div>
      </template>
      <div class="badgemodal__grid">
        <template x-for="(bd, i) in badges()" x-bind:key="i">
          <div class="bmcard" x-bind:class="bd.got ? 'got' : 'locked'" x-bind:style="bd.got ? ('--bc:' + bd.color) : ''">
            <span class="bmcard__cat" x-text="bd.cat"></span>
            <span class="gbadge__orb bmcard__orb"><span class="gbadge__ic" x-text="bd.got ? bd.icon : '🔒'"></span></span>
            <span class="bmcard__label" x-text="bd.label"></span>
            <span class="bmcard__exp" x-text="'+' + bd.exp + ' EXP'"></span>
            <span class="bmcard__desc" x-text="bd.desc"></span>
            <span class="bmcard__foot" x-bind:class="bd.got ? 'is-got' : ''"
                  x-text="bd.got ? '✓ 달성 완료' : (Math.min(bd.cur, bd.target) + ' / ' + bd.target + ' ' + bd.unit)"></span>
          </div>
        </template>
      </div>
    </div>
  </div>

  <!-- 배지 달성 축하 오버레이(성취감) -->
  <div class="badgeburst" x-show="badgeToast" x-cloak x-transition.opacity x-on:click="badgeToast = null">
    <div class="badgeburst__card" x-bind:style="badgeToast ? ('--bc:' + badgeToast.color) : ''">
      <template x-for="i in 12" x-bind:key="i"><span class="badgeburst__spark" x-bind:style="'--i:' + i"></span></template>
      <div class="badgeburst__orb"><span class="badgeburst__ic" x-text="badgeToast && badgeToast.icon"></span></div>
      <div class="badgeburst__ttl">🎉 배지 달성!</div>
      <div class="badgeburst__label" x-text="badgeToast && badgeToast.label"></div>
      <div class="badgeburst__exp" x-text="badgeToast && ('+' + badgeToast.exp + ' EXP')"></div>
      <div class="badgeburst__hint" x-text="badgeToast && badgeToast.desc"></div>
    </div>
  </div>

  <!-- 검수자 등록 온보딩(딤드 + 중앙 모달). 첫 방문 시 자동, 칩 클릭 시 변경 -->
  <div class="onboard" x-show="reviewerEditing" x-cloak x-transition.opacity
       x-on:click.self="if (reviewer) reviewerEditing = false"
       x-on:keydown.escape.window="if (reviewer) reviewerEditing = false">
    <div class="onboard__card" x-transition>
      <div class="onboard__brand">
        <img class="onboard__logo onboard__logo--light" src="/vendor/prism-logo-tagline-light.png" alt="Prism · A lens on content & users">
        <img class="onboard__logo onboard__logo--dark" src="/vendor/prism-logo-tagline-dark.png" alt="Prism · A lens on content & users"></div>
      <h2 class="onboard__title" x-text="authToken ? '검수자 정보 변경' : (authMode==='signup'?'가입하고 시작':'로그인')"></h2>
      <p class="onboard__lead">팀이 함께 콘텐츠를 검수해 정확도를 끌어올립니다. 내 검수가 점수가 되고 캐릭터가 성장해요. 계정으로 로그인하면 <b>어느 기기에서나</b> 이어집니다.</p>

      <!-- ① 로그인/가입 (비로그인) -->
      <div x-show="!authToken">
        <div class="onboard__authtabs">
          <button type="button" x-bind:class="authMode==='login'?'sel':''" x-on:click="authMode='login';authMsg=''">로그인</button>
          <button type="button" x-bind:class="authMode==='signup'?'sel':''" x-on:click="authMode='signup';authMsg=''">가입</button>
        </div>
        <!-- 그룹 A · 계정: 이메일·비밀번호(+확인)·닉네임·캐릭터 -->
        <div class="onboard__group">
          <div class="onboard__grouphd">계정</div>
          <label class="onboard__lbl">이메일</label>
          <input class="field onboard__name" type="email" placeholder="you@team.com" x-model="authEmail" style="margin-bottom:12px">
          <label class="onboard__lbl">비밀번호</label>
          <input class="field onboard__name" type="password" placeholder="••••••••" x-model="authPw" x-on:keydown.enter="saveReviewer()" x-bind:style="authMode==='signup' ? 'margin-bottom:12px' : 'margin-bottom:0'">
          <template x-if="authMode==='signup'">
            <div>
              <label class="onboard__lbl">비밀번호 확인</label>
              <input class="field onboard__name" type="password" placeholder="비밀번호 다시 입력" x-model="authPw2" x-on:keydown.enter="saveReviewer()" style="margin-bottom:12px">
              <label class="onboard__lbl">닉네임 <span class="onboard__hint"> 리더보드·검수에 표시</span></label>
              <input class="field onboard__name" placeholder="예) 김검수" x-model="reviewer" x-on:keydown.enter="saveReviewer()" style="margin-bottom:12px">
              <label class="onboard__lbl">캐릭터 선택</label>
              <div class="onboard__chars" style="margin-bottom:0">
                <template x-for="c in charOptions" x-bind:key="c.id">
                  <button type="button" class="ochar" x-bind:class="reviewerChar===c.id ? 'sel' : ''" x-on:click="reviewerChar=c.id">
                    <span class="ochar__ring"><img x-bind:src="c.img" x-bind:alt="c.label"></span>
                    <b x-text="c.label"></b><small x-text="c.role"></small>
                  </button>
                </template>
              </div>
            </div>
          </template>
        </div>
        <div class="onboard__authmsg" x-show="authMsg" x-text="authMsg"></div>
        <!-- 그룹 B · 팀(가입 시): 코드 참가 또는 팀 없음. 팀 생성은 관리자 메뉴 -->
        <template x-if="authMode==='signup'">
          <div class="onboard__group onboard__group--b">
            <div class="onboard__grouphd" style="display:flex;align-items:center;justify-content:space-between">
              <span>팀 참가</span>
              <label style="display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:700;color:var(--ds-muted);cursor:pointer;text-transform:none;letter-spacing:0">
                <input type="checkbox" x-model="noTeam"> 팀 없음</label>
            </div>
            <input x-show="!noTeam" class="field onboard__name" placeholder="팀 초대 코드 (예: A1B2C3D4)" x-model="inviteCode" style="margin-bottom:6px;text-transform:uppercase;letter-spacing:.08em;font-weight:700" x-on:keydown.enter="saveReviewer()">
            <p class="onboard__hint" style="text-align:left;display:block;margin-bottom:0" x-text="noTeam ? '팀 없이 시작합니다. 나중에 관리자에게 코드를 받아 참가할 수 있어요.' : '관리자에게 받은 코드를 입력하면 같은 팀으로 참가합니다.'"></p>
          </div>
        </template>
      </div>

      <!-- ② 프로필 편집 (로그인 상태) · 닉네임·캐릭터만 -->
      <div x-show="authToken" class="onboard__group">
        <div class="onboard__grouphd">프로필</div>
        <label class="onboard__lbl">닉네임 <span class="onboard__hint"> 리더보드·검수에 표시</span></label>
        <input class="field onboard__name" placeholder="예) 김검수" x-model="reviewer" x-on:keydown.enter="saveReviewer()">
        <label class="onboard__lbl">캐릭터 선택</label>
        <div class="onboard__chars" style="margin-bottom:0">
          <template x-for="c in charOptions" x-bind:key="c.id">
            <button type="button" class="ochar" x-bind:class="reviewerChar===c.id ? 'sel' : ''" x-on:click="reviewerChar=c.id">
              <span class="ochar__ring"><img x-bind:src="c.img" x-bind:alt="c.label"></span>
              <b x-text="c.label"></b><small x-text="c.role"></small>
            </button>
          </template>
        </div>
      </div>

      <button type="button" class="ds-btn ds-btn--primary onboard__cta"
              x-bind:disabled="authToken ? !(reviewer||'').trim() : (!(authEmail||'').trim() || !authPw || (authMode==='signup' && (!authPw2 || !(reviewer||'').trim() || (!noTeam && !(inviteCode||'').trim()))))"
              x-on:click="saveReviewer()"
              x-text="authToken ? '저장하고 시작' : (authMode==='signup'?'가입하고 시작':'로그인하고 시작')"></button>
      <button type="button" class="onboard__skip" x-show="authToken" x-on:click="reviewerEditing=false">닫기</button>
    </div>
  </div>

  <div class="appbody">
  <!-- ━━━━━ 좌측 컬럼 · 내비 + 도우미 ━━━━━ -->
  <div class="leftcol">
    <aside class="ds-sidebar">
      <!-- 홈 = 그룹 밖 독립 최상단 -->
      <nav class="ds-navgroup" style="margin-bottom:6px;padding-bottom:8px;border-bottom:1px solid var(--ds-hairline-soft)">
        <button type="button" class="ds-navitem" x-bind:class="mod === 'home' ? 'ds-navitem--active' : ''" x-on:click="selectMod('home')">
          <span class="ds-navitem__icon" x-html="navIcons.home"></span><span>홈</span>
        </button>
      </nav>
      <template x-for="grp in mods" x-bind:key="grp.g">
        <nav class="ds-navgroup" x-show="navVisible(grp.gcond)">
          <div class="ds-navgroup__label" x-text="grp.g"></div>
          <template x-for="it in grp.items" x-bind:key="it.id">
            <button type="button" class="ds-navitem" x-show="navVisible(it.cond)" x-bind:class="mod === it.id ? 'ds-navitem--active' : ''" x-on:click="selectMod(it.id)">
              <span class="ds-navitem__icon" x-html="navIcons[it.ic]"></span>
              <span x-text="it.label"></span>
            </button>
          </template>
        </nav>
      </template>
    </aside>
    <!-- 도우미 = 사이드 위젯 바로 아래(다크 박스 + 캐릭터) -->
    <div class="side-assistant">
      <!-- 사용자 프로필 카드(야구카드형): 내 캐릭터 + 이름. 누르면 '유저명 에이전트' 열림. 미설정 시 프로필 설정 -->
      <button type="button" class="side-profile" x-on:click="reviewer ? (chatOpen = !chatOpen) : (reviewerEditing = true)" x-bind:data-tier="(reviewer && arenaMe) ? levelTier(arenaMe.level) : 0" aria-label="내 프로필·에이전트">
        <span class="side-profile__glow"></span>
        <span class="side-profile__gain" x-show="reviewer && arenaMe && arenaMe.week_points>0" x-text="'+' + arenaMe.week_points + ' XP'"></span>
        <span class="side-profile__avatar">
          <img x-bind:src="charImg(reviewer ? reviewerChar : 'boksil')" alt="">
          <span class="side-profile__lvl" x-show="reviewer && arenaMe" x-text="'Lv.' + (arenaMe ? arenaMe.level : 0)"></span>
        </span>
        <span class="side-profile__name">
          <span class="side-assistant__spin" x-show="loading || modBusy"></span>
          <span x-text="reviewer || '프로필 설정'"></span>
          <span class="side-profile__tag" x-show="reviewer && !(loading || modBusy)">에이전트</span>
        </span>
        <span class="side-profile__score" x-show="reviewer && arenaMe && !(loading || modBusy)"><b x-text="(arenaMe ? arenaMe.points : 0)"></b> pt</span>
        <span class="side-profile__sub" x-show="!(reviewer && arenaMe) || (loading || modBusy)" x-text="(loading || modBusy) ? '처리하고 있어요…' : (reviewer ? '내 에이전트 열기' : '이름·캐릭터를 설정하세요')"></span>
        <span class="side-profile__edit" x-show="reviewer" x-on:click.stop="reviewerEditing = true" role="button" aria-label="프로필 편집" data-tip="편집" data-tip-pos="left"><svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M4 20h4L19 9l-4-4L4 16v4Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg></span>
      </button>
    </div>
  </div>

  <!-- ━━━━━ 우측 = 상단 메뉴 위젯 + 캔버스 ━━━━━ -->
  <div class="home">
    <div class="canvas">
      <!-- ═══ 홈: 위젯 캔버스(실동작 위젯만 · 직접 배치) ═══ -->
      <div x-show="false" class="ds-widgetgrid" x-bind:class="editing ? 'ds-widgetgrid--edit' : ''" id="grid">
        <!-- 빈 상태: 배치 도우미(첫 방문) -->
        <div x-show="placed && placed.length === 0" x-cloak class="ds-empty" style="grid-column:1/-1">
          <span class="ds-character ds-character--bob" style="width:84px;height:84px"><img src="/vendor/boksil-catcher.svg" alt=""></span>
          <div class="ds-empty__title">홈을 직접 구성해 보세요</div>
          <div class="ds-empty__desc">필요한 위젯을 골라 나만의 콘솔을 만듭니다 추천 구성으로 빠르게 시작할 수 있어요</div>
          <div style="display:flex;gap:8px;justify-content:center;margin-top:16px">
            <button type="button" class="ds-btn ds-btn--primary" x-on:click="useRecommended()">추천 구성 배치</button>
            <button type="button" class="ds-btn ds-btn--secondary" x-on:click="addMenuOpen = true">위젯 추가</button>
          </div>
        </div>

        <!-- 런처: 새 추출 → 추출 실행 -->
        <div class="ds-widget ds-widget--function ds-widget--launcher ds-widget--md" data-wid="launch-run" x-show="hasWidget('launch-run')" x-cloak role="button" tabindex="0" x-on:click="!editing && selectMod('run')">
          <button class="ds-widget__remove" x-on:click.stop="removeWidget('launch-run')" aria-label="제거"><svg width="11" height="11" viewBox="0 0 11 11"><path d="M3 3l5 5M8 3l-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>
          <span class="ds-launcher__icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg></span>
          <span class="ds-launcher__body"><span class="ds-launcher__title">새 추출</span><span class="ds-launcher__sub">이미지·텍스트·엑셀 추출</span></span>
          <span class="ds-launcher__chev"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M6 4l4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
        </div>
        <!-- 런처: 배치 결과 → 대시보드 -->
        <div class="ds-widget ds-widget--function ds-widget--launcher ds-widget--md" data-wid="launch-batch" x-show="hasWidget('launch-batch')" x-cloak role="button" tabindex="0" x-on:click="!editing && selectMod('dash')">
          <button class="ds-widget__remove" x-on:click.stop="removeWidget('launch-batch')" aria-label="제거"><svg width="11" height="11" viewBox="0 0 11 11"><path d="M3 3l5 5M8 3l-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>
          <span class="ds-launcher__icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" stroke="currentColor" stroke-width="1.6"/></svg></span>
          <span class="ds-launcher__body"><span class="ds-launcher__title">배치 결과</span><span class="ds-launcher__sub">집계·유통·분포 보기</span></span>
          <span class="ds-launcher__chev"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M6 4l4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
        </div>
        <!-- 런처: 사전·정책 → 전용 도구 -->
        <div class="ds-widget ds-widget--function ds-widget--launcher ds-widget--md" data-wid="launch-dict" x-show="hasWidget('launch-dict')" x-cloak role="button" tabindex="0" x-on:click="!editing && selectMod('dict')">
          <button class="ds-widget__remove" x-on:click.stop="removeWidget('launch-dict')" aria-label="제거"><svg width="11" height="11" viewBox="0 0 11 11"><path d="M3 3l5 5M8 3l-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>
          <span class="ds-launcher__icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M4 5h16M4 12h16M4 19h10" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg></span>
          <span class="ds-launcher__body"><span class="ds-launcher__title">사전 · 정책 관리</span><span class="ds-launcher__sub">전용 도구로 이동 ↗</span></span>
          <span class="ds-launcher__chev"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M6 4l4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
        </div>

        <!-- 핵심 지표 (정보 · md) · /dashboard 집계 -->
        <div class="ds-widget ds-widget--info ds-widget--md" data-wid="metrics" x-show="hasWidget('metrics')" x-cloak>
          <button class="ds-widget__remove" x-on:click.stop="removeWidget('metrics')" aria-label="제거"><svg width="11" height="11" viewBox="0 0 11 11"><path d="M3 3l5 5M8 3l-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>
          <div class="ds-widget__head"><div class="ds-widget__title"><span>핵심 지표</span></div><div class="ds-widget__actions"><span class="ds-widget__kind ds-widget__kind--info">정보</span></div></div>
          <div class="ds-widget__body"><div class="ds-stat-grid" style="grid-template-columns:repeat(2,1fr)">
            <div class="ds-stat"><div class="ds-stat__value ds-stat__value--accent" x-text="dashData ? dashData.n : 0"></div><div class="ds-stat__label">추출 완료</div></div>
            <div class="ds-stat"><div class="ds-stat__value" x-text="(dashData ? dashData.gPct : 0) + '%'"></div><div class="ds-stat__label">유통 가능 G</div></div>
            <div class="ds-stat"><div class="ds-stat__value" x-text="dashData ? dashData.entities : 0"></div><div class="ds-stat__label">엔티티</div></div>
            <div class="ds-stat"><div class="ds-stat__value" x-text="dashData ? dashData.avgLead : 0"></div><div class="ds-stat__label">평균 리드문</div></div>
          </div></div>
        </div>

        <!-- 품질 점수 (정보 · sm) -->
        <div class="ds-widget ds-widget--info" data-wid="quality" x-show="hasWidget('quality')" x-cloak>
          <button class="ds-widget__remove" x-on:click.stop="removeWidget('quality')" aria-label="제거"><svg width="11" height="11" viewBox="0 0 11 11"><path d="M3 3l5 5M8 3l-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>
          <div class="ds-widget__head"><div class="ds-widget__title"><span>품질 점수</span></div><div class="ds-widget__actions"><span class="ds-widget__kind ds-widget__kind--info">정보</span></div></div>
          <div class="ds-widget__body center"><div class="ds-ring" x-bind:style="'--ds-ring-size:104px;--ds-ring-pct:' + (dashData ? dashData.gPct : 0)"><span class="ds-ring__label" x-text="(dashData ? dashData.gPct : 0) + '%'"></span></div></div>
        </div>

        <!-- 인텐트 분포 (정보 · md) -->
        <div class="ds-widget ds-widget--info ds-widget--md" data-wid="intents" x-show="hasWidget('intents')" x-cloak>
          <button class="ds-widget__remove" x-on:click.stop="removeWidget('intents')" aria-label="제거"><svg width="11" height="11" viewBox="0 0 11 11"><path d="M3 3l5 5M8 3l-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>
          <div class="ds-widget__head"><div class="ds-widget__title"><span>인텐트 분포</span></div><div class="ds-widget__actions"><span class="ds-widget__kind ds-widget__kind--info">정보</span></div></div>
          <div class="ds-widget__body">
            <template x-for="it in (dashData ? dashData.intents : [])" x-bind:key="it.k">
              <div class="ds-progress" style="margin:6px 0"><div class="ds-progress__head"><span class="ds-progress__label" x-text="it.k"></span><span class="ds-progress__pct" x-text="it.v"></span></div><div class="ds-progress__track"><div class="ds-progress__fill ds-progress__fill--primary" x-bind:style="'width:'+Math.max(it.pct,4)+'%'"></div></div></div>
            </template>
            <div x-show="!(dashData && dashData.intents && dashData.intents.length)" class="w-stat-l">추출하면 분포가 표시됩니다</div>
          </div>
        </div>

        <!-- 카테고리 분포 (정보 · md) -->
        <div class="ds-widget ds-widget--info ds-widget--md" data-wid="categories" x-show="hasWidget('categories')" x-cloak>
          <button class="ds-widget__remove" x-on:click.stop="removeWidget('categories')" aria-label="제거"><svg width="11" height="11" viewBox="0 0 11 11"><path d="M3 3l5 5M8 3l-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>
          <div class="ds-widget__head"><div class="ds-widget__title"><span>카테고리 분포</span></div><div class="ds-widget__actions"><span class="ds-widget__kind ds-widget__kind--info">정보</span></div></div>
          <div class="ds-widget__body">
            <template x-for="it in (dashData ? dashData.categories : [])" x-bind:key="it.k">
              <div class="ds-progress" style="margin:6px 0"><div class="ds-progress__head"><span class="ds-progress__label" x-text="it.k"></span><span class="ds-progress__pct" x-text="it.v"></span></div><div class="ds-progress__track"><div class="ds-progress__fill ds-progress__fill--primary" x-bind:style="'width:'+Math.max(it.pct,4)+'%'"></div></div></div>
            </template>
            <div x-show="!(dashData && dashData.categories && dashData.categories.length)" class="w-stat-l">추출하면 분포가 표시됩니다</div>
          </div>
        </div>

        <!-- 처리 프로세스 (정보 · tall) · 파이프라인 4단계 -->
        <div class="ds-widget ds-widget--info ds-widget--tall" data-wid="process" x-show="hasWidget('process')" x-cloak>
          <button class="ds-widget__remove" x-on:click.stop="removeWidget('process')" aria-label="제거"><svg width="11" height="11" viewBox="0 0 11 11"><path d="M3 3l5 5M8 3l-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>
          <div class="ds-widget__head"><div class="ds-widget__title"><span>처리 프로세스</span></div><div class="ds-widget__actions"><span class="ds-widget__kind ds-widget__kind--info">정보</span></div></div>
          <div class="ds-widget__body">
            <div class="w-agent"><span class="w-agent__av"><img src="/vendor/daesik-batter.svg" alt=""></span><div><div class="w-agent__n">추출</div><div class="w-agent__r">대식 · 이미지→신호</div></div><span class="w-agent__s"><span class="ds-statusdot ds-statusdot--ok"><span class="ds-statusdot__dot"></span></span></span></div>
            <div class="w-agent"><span class="w-agent__av"><img src="/vendor/yonghee-pitcher.svg" alt=""></span><div><div class="w-agent__n">분석</div><div class="w-agent__r">용희 · 메타 분류</div></div><span class="w-agent__s"><span class="ds-statusdot ds-statusdot--ok"><span class="ds-statusdot__dot"></span></span></span></div>
            <div class="w-agent"><span class="w-agent__av"><img src="/vendor/boksil-catcher.svg" alt=""></span><div><div class="w-agent__n">검수</div><div class="w-agent__r">복실 · 품질 확인</div></div><span class="w-agent__s"><span class="ds-statusdot ds-statusdot--ok"><span class="ds-statusdot__dot"></span></span></span></div>
            <div class="w-agent"><span class="w-agent__av"><img src="/vendor/ddakji-manager.svg" alt=""></span><div><div class="w-agent__n">판정 · 부여</div><div class="w-agent__r">딱지 · 유통 결정</div></div><span class="w-agent__s"><span class="ds-statusdot ds-statusdot--ok"><span class="ds-statusdot__dot"></span></span></span></div>
          </div>
        </div>
      </div>

      <!-- ═══ 모듈: 자동 인입(파이프라인 소스 설정) · 관리자 전용 ═══ -->
      <!-- ═══ 모듈: 콘텐츠 관리(관리자) · 원페이지 STEP: 1 추가(수동/자동·용도) → 2 모델 실행 → 3 실행 큐 ═══ -->
      <div x-show="mod === 'content'" x-cloak class="w-full" style="margin-bottom:12px">
        <div class="stepline"><span class="stepline__no">STEP 1</span><b>콘텐츠 추가</b><span class="meta">수동·자동으로 콘텐츠를 모으고 용도를 지정합니다</span>
          <span class="ml-auto" style="display:flex;gap:6px;align-items:center">
            <span class="selctl" data-tip="검수용=검수·정답 축적 대상 · 평가용=검수 목록에서 제외되는 평가 전용 홀드아웃" data-tip-pos="bottom"><span class="selctl__lbl">추가 용도</span><select class="field" x-model="addPurpose"><option value="review">검수용</option><option value="eval">평가용</option></select></span>
            <button type="button" class="srcfilter__chip" x-bind:class="contentTab==='run'?'sel':''" x-on:click="contentTab='run'">수동 추가</button>
            <button type="button" class="srcfilter__chip" x-bind:class="contentTab==='auto'?'sel':''" x-on:click="contentTab='auto'; fetchIngestStatus()">자동 추가</button>
          </span>
        </div>
      </div>
      <div x-show="mod === 'content' && contentTab === 'auto'" x-cloak class="w-full space-y-4">
        <template x-if="!(backend === 'supabase' && adminData && adminData.isAdmin)">
          <ul class="ds-bullets hintbox" style="padding:14px 16px">
            <li>자동 인입은 <b>운영(팀) 관리자</b> 전용입니다.</li>
            <li>로컬 단독 실행에서는 <b>수동 추출</b>을 사용하세요.</li>
          </ul>
        </template>
        <template x-if="backend === 'supabase' && adminData && adminData.isAdmin">
        <div class="w-full space-y-4">
        <ul class="ds-bullets hintbox" style="padding:14px 16px">
          <li>콘텐츠를 <b>자동으로 추가</b>하는 수집 소스를 설정합니다.</li>
          <li>등록·활성화한 소스로 들어온 콘텐츠가 자동으로 추가되어 초안이 생성됩니다.</li>
          <li>일회성 처리는 <b>수동 추가</b>를 사용하세요.</li>
        </ul>
        <!-- 일회성 인입: 크롤러에서 수량 목표로 당겨오기(구 팀 관리 패널 이동) -->
        <section class="panel" data-fn><div class="panel-hd"><b>일회성 가져오기</b><span class="meta">크롤러에서 수량 목표로 당겨와 추가</span></div>
          <div class="panel-bd">
            <ul class="ds-bullets" style="margin-bottom:10px"><li>크롤러 엔드포인트에서 <b>수량 목표</b>로 당겨와 추출 → 전건을 팀 <b>검수 대기</b>에 적재합니다.</li><li>실시간 스트리밍 부담 없이 배치로 처리합니다.</li></ul>
            <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
              <input class="field" style="flex:1;min-width:240px" placeholder="크롤러 엔드포인트 · JSON 배열 반환 GET (예: https://my-crawler/items)" x-model="ingestEndpoint">
              <input class="field" type="number" style="width:96px" min="1" max="200" x-model.number="ingestN" placeholder="수량">
              <button type="button" class="ds-btn ds-btn--primary" x-bind:disabled="ingestBusy" x-on:click="ingestRun()" x-text="ingestBusy ? '가져오는 중…' : '가져오기 실행'"></button>
            </div>
            <span class="text-xs text-muted" style="display:block;margin-top:7px" x-text="ingestMsg"></span>
          </div>
        </section>
        <section class="panel" data-fn><div class="panel-hd"><b>인입 소스 추가</b></div>
          <div class="panel-bd" style="display:flex;flex-direction:column;gap:12px">
            <div style="display:flex;gap:10px;flex-wrap:wrap">
              <div style="flex:1;min-width:160px"><label class="lbl">유형</label>
                <select x-model="newSrc.type" class="field"><option value="api">REST API · 웹훅</option><option value="kafka">Kafka 브로커</option></select></div>
              <div style="flex:1;min-width:160px"><label class="lbl">이름</label><input x-model="newSrc.name" class="field" placeholder="예) 뉴스 수집 API"></div>
            </div>
            <template x-if="newSrc.type === 'api'">
              <div style="display:flex;flex-direction:column;gap:12px">
                <div><label class="lbl">엔드포인트 URL</label><input x-model="newSrc.endpoint" class="field" placeholder="https://… (폴링 GET 또는 웹훅 수신)"></div>
                <div style="display:flex;gap:10px;flex-wrap:wrap">
                  <div style="flex:1;min-width:120px"><label class="lbl">메서드</label><select x-model="newSrc.method" class="field"><option>GET</option><option>POST</option><option>WEBHOOK</option></select></div>
                  <div style="flex:2;min-width:180px"><label class="lbl">인증 헤더 (선택)</label><input x-model="newSrc.auth" class="field" placeholder="Authorization: Bearer …"></div>
                  <div style="flex:1;min-width:90px"><label class="lbl">폴링(초)</label><input x-model="newSrc.interval" class="field" placeholder="60"></div>
                </div>
              </div>
            </template>
            <template x-if="newSrc.type === 'kafka'">
              <div style="display:flex;flex-direction:column;gap:12px">
                <div><label class="lbl">브로커</label><input x-model="newSrc.brokers" class="field" placeholder="broker1:9092,broker2:9092"></div>
                <div style="display:flex;gap:10px;flex-wrap:wrap">
                  <div style="flex:1;min-width:140px"><label class="lbl">토픽</label><input x-model="newSrc.topic" class="field" placeholder="content.ingest"></div>
                  <div style="flex:1;min-width:140px"><label class="lbl">컨슈머 그룹</label><input x-model="newSrc.group" class="field" placeholder="prism-ingest"></div>
                </div>
              </div>
            </template>
            <div style="display:flex;align-items:center;gap:10px"><button type="button" x-on:click="addSource()" class="ds-btn ds-btn--primary">소스 추가</button><span class="text-xs" style="color:var(--ds-muted)" aria-live="polite" x-text="ingestMsg"></span></div>
          </div>
        </section>
        <section class="panel"><div class="panel-hd"><b>등록된 인입 소스</b><span class="meta" x-text="ingestSources.length + '개'"></span></div>
          <div class="panel-bd">
            <ul x-show="ingestSources.length" class="ds-bullets" style="margin-bottom:11px"><li><b>활성</b> 소스는 <b>폴링(초)</b> 주기마다 백그라운드로 자동 인입되고, <b>실행 큐</b>에 진행률이 표시됩니다.</li><li>즉시 한 번만 받으려면 <b>지금 인입</b>, 멈추려면 <b>중지</b>.</li></ul>
            <div x-show="!ingestSources.length" class="ds-empty" style="border:0;padding:16px 4px"><div class="ds-empty__desc">아직 등록된 소스가 없습니다 위에서 API · Kafka 소스를 추가하세요</div></div>
            <template x-for="s in ingestSources" x-bind:key="s.id">
              <div class="drow" style="grid-template-columns:1fr auto;align-items:center;border-bottom:1px solid var(--ds-hairline-soft)">
                <div>
                  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                    <span class="ds-badge" x-bind:class="s.type === 'kafka' ? 'ds-badge--intent' : 'ds-badge--entity'" x-text="s.type === 'kafka' ? 'Kafka' : 'API'"></span>
                    <b class="text-ink" x-text="s.name"></b>
                    <span class="ds-badge" x-bind:class="s.enabled ? 'ds-badge--success' : 'ds-badge--neutral'"><span x-show="s.enabled" class="ds-badge__dot"></span><span x-text="s.enabled ? '활성' : '중지'"></span></span>
                  </div>
                  <div class="text-xs text-muted" style="margin-top:3px" x-text="s.type === 'kafka' ? (s.brokers + ' · ' + s.topic) : (s.method + ' ' + s.endpoint)"></div>
                  <div x-show="srcRunning(s) || ingestRunMsg[s.id] || (srcJob(s) && srcJob(s).last_msg)" class="text-xs" style="margin-top:5px" x-bind:style="(ingestRunMsg[s.id]||'').startsWith('오류') || (srcJob(s) && srcJob(s).last_ok === false) ? 'color:#ff4e33' : 'color:var(--ds-primary)'" x-text="srcRunning(s) ? ('인입 중 ' + (srcJob(s) ? (srcJob(s).done + (srcJob(s).total ? ('/' + srcJob(s).total) : '') + '건') : '…')) : (ingestRunMsg[s.id] || (srcJob(s) ? srcJob(s).last_msg : ''))"></div>
                </div>
                <div style="display:flex;gap:6px">
                  <button type="button" class="ds-btn ds-btn--primary" style="height:30px;padding:0 12px" x-show="s.type !== 'kafka'" x-bind:disabled="srcRunning(s)" x-on:click="ingestNow(s)" x-text="srcRunning(s) ? '인입 중…' : '지금 인입'"></button>
                  <button type="button" class="copybtn" x-on:click="toggleSource(s)" x-text="s.enabled ? '중지' : '활성'"></button>
                  <button type="button" class="copybtn" x-on:click="removeSource(s.id)" style="color:#ff4e33;border-color:rgba(255,78,51,.3)">삭제</button>
                </div>
              </div>
            </template>
          </div>
        </section>
        </div>
        </template>
      </div>

      <!-- ═══ 모듈: 실행 · 추출 ═══ -->
      <div x-show="mod === 'content' && contentTab === 'run'" class="w-full space-y-4">

        <!-- 추출 실행 = 기능 위젯(입력 방식 탭 + 폼) -->
        <section class="panel" data-fn>
          <div class="panel-hd"><b>추출 실행</b></div>
          <div class="panel-bd">
          <!-- 입력 방식 -->
          <div class="seg seg3 mb-5">
            <template x-for="t in tabItems" x-bind:key="t.id">
              <button type="button" x-on:click="selectTab(t.id)" x-bind:class="activeTabId === t.id ? 'on' : ''" x-text="t.label"></button>
            </template>
          </div>
          <!-- 콘텐츠 그룹: 입력 시 지정(이미지·텍스트 공용) 엑셀은 컬럼에서 자동 -->
          <div x-show="activeTabId !== 'excel'" x-cloak class="mb-4">
            <label class="lbl">콘텐츠 그룹</label>
            <select x-model="group" class="field">
              <template x-for="g in groups" x-bind:key="g"><option x-bind:value="g" x-text="g"></option></template>
            </select>
          </div>
          <!-- 이미지 -->
          <div x-show="activeTabId === 'image'" x-cloak class="space-y-4"
               x-on:paste.window="activeTabId === 'image' && onPasteImages($event)">
            <!-- 디자인 시스템 파일럿: ImageDropzone + AttachmentChip (다크 스코프) -->
            <div class="ds-pilot">
              <label class="lbl">이미지 (여러 장이면 하나의 콘텐츠로 통합)</label>
              <label class="ds-dropzone" x-bind:class="imgDrag ? 'drag' : ''"
                     x-on:dragover.prevent="imgDrag = true" x-on:dragleave.prevent="imgDrag = false"
                     x-on:drop.prevent="onDropImages($event)">
                <span class="dz-ic">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 16V4m-4 4 4-4 4 4M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/></svg>
                </span>
                <span class="dz-t" x-text="imgDrag ? '여기에 놓기' : '이미지를 끌어다 놓기'"></span>
                <span class="dz-d" x-text="imgDrag ? '' : '클릭하여 선택 · 클립보드 붙여넣기 가능'"></span>
                <input type="file" accept="image/*" multiple class="sr-only" x-on:change="onFiles($event)">
              </label>
              <!-- AttachmentChip 목록 -->
              <div x-show="imgFiles.length" x-cloak class="ds-attachments">
                <template x-for="(f, i) in imgFiles" x-bind:key="i">
                  <span class="ds-attachment">
                    <span class="ds-attachment__icon">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.6-3.6a2 2 0 0 0-2.8 0L6 20"/></svg>
                    </span>
                    <span class="ds-attachment__name" x-text="f.name"></span>
                    <span class="ds-attachment__size" x-text="sizeLabel(f.size)"></span>
                    <button type="button" class="ds-attachment__remove" x-on:click="removeImage(i)" aria-label="제거">
                      <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true"><path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
                    </button>
                  </span>
                </template>
              </div>
            </div>
            <div><label class="lbl">제목 (선택)</label>
              <input x-model="imgTitle" class="field" placeholder="없으면 이미지에서 추론"></div>
            <div><label class="lbl">캡션 (선택)</label>
              <input x-model="imgCaption" class="field" placeholder="사진 설명이 있으면 함께 참조"></div>
          </div>
          <!-- 텍스트 -->
          <div x-show="activeTabId === 'text'" x-cloak class="space-y-4">
            <div><label class="lbl">제목 (title)</label>
              <input x-model="txtTitle" class="field" placeholder="기사 제목"></div>
            <div><label class="lbl">본문 (body)</label>
              <textarea x-model="txtBody" rows="5" class="field" placeholder="본문 내용"></textarea></div>
          </div>
          <!-- 엑셀 -->
          <div x-show="activeTabId === 'excel'" x-cloak class="space-y-4">
            <div class="flex items-center justify-between gap-2 rounded-lg border border-black/[0.08] bg-black/[0.02] px-3 py-2.5">
              <div class="text-xs text-muted">컬럼 양식 · <span class="text-body">콘텐츠 그룹 · 제목 · 부제 · 본문</span> (제목·본문 필수)</div>
              <a href="/template.csv" download
                class="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-black/[0.12] px-2.5 py-1 text-xs font-medium text-ink transition-colors hover:bg-black/[0.06]">
                <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>
                템플릿 내려받기
              </a>
            </div>
            <div>
              <label class="lbl">엑셀 / CSV (제목·본문 컬럼 자동 매핑)</label>
              <label class="dropzone" x-bind:class="xlsDrag ? 'drag' : ''"
                     x-on:dragover.prevent="xlsDrag = true" x-on:dragleave.prevent="xlsDrag = false"
                     x-on:drop.prevent="onDropExcel($event)">
                <span class="name" x-text="xlsDrag ? '여기에 놓기' : (excelLabel + ' · 끌어다 놓기 가능')"></span>
                <span class="pick">
                  <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>
                  파일 선택
                </span>
                <input type="file" accept=".xlsx,.csv,.tsv,.jsonl,.json" class="sr-only" x-on:change="onExcel($event)">
              </label>
              <p class="mt-1.5 text-xs text-muted">행마다 한 콘텐츠로 일괄 추출합니다 컬럼명이 제목/본문/서비스명과 달라도 자동 추론합니다 (최대 200행)</p>
            </div>
          </div>

          <div class="mt-5 flex items-center gap-3">
            <button type="button" x-on:click="run()" x-bind:disabled="loading"
              class="ds-btn ds-btn--primary ds-btn--s-lg disabled:opacity-50">
              <svg x-show="loading" x-cloak class="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z"/></svg>
              <span x-text="loading ? '실행 중' : '추출 실행'"></span>
            </button>
            <span aria-live="polite" class="ml-auto text-sm text-[#ff4e33]" x-text="status"></span>
          </div>
          </div>
        </section>


        <!-- 처리 대기 화면: Processing(캐릭터) + Steps (디자인 시스템) -->
        <div x-show="loading" x-cloak class="ds-pilot mt-6 panel" style="border-color:var(--ds-hairline)">
          <div class="panel-bd">
            <!-- Processing -->
            <div class="ds-processing">
              <span class="ds-processing__char"><span class="ds-character ds-character--bob" style="width:96px;height:96px"><img src="/vendor/yonghee-pitcher.svg" alt="용희 (투수)"></span></span>
              <div>
                <div class="ds-processing__title" x-text="activeTabId === 'excel' ? '일괄 추출 중' : '메타데이터 추출 중'"></div>
                <div class="ds-processing__msg"><span class="ds-processing__dots" x-text="activeTabId === 'excel' ? '행마다 추출하고 있어요' : (activeTabId === 'image' ? '이미지를 읽고 있어요' : '리드문·메타를 생성하고 있어요')"></span></div>
              </div>
              <div style="width:100%;max-width:340px">
                <div class="ds-progress ds-progress--indeterminate"><div class="ds-progress__track" role="progressbar"><div class="ds-progress__fill ds-progress__fill--primary"></div></div></div>
              </div>
            </div>
            <!-- Steps -->
            <div class="ds-steps mt-5" style="max-width:420px">
              <div class="ds-step ds-step--done"><div class="ds-step__rail"><span class="ds-step__marker"><svg class="ds-step__check" viewBox="0 0 12 12" fill="none"><path d="M2.5 6.2 5 8.5 9.5 3.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></span><span class="ds-step__line"></span></div><div class="ds-step__body"><div class="ds-step__title">입력 수집</div><div class="ds-step__detail" x-text="activeTabId === 'excel' ? '엑셀 행 매핑' : '콘텐츠 정규화'"></div></div></div>
              <div class="ds-step" x-bind:class="activeTabId === 'image' ? 'ds-step--active' : 'ds-step--done'"><div class="ds-step__rail"><span class="ds-step__marker"></span><span class="ds-step__line"></span></div><div class="ds-step__body"><div class="ds-step__title">이미지 이해</div><div class="ds-step__detail" x-text="activeTabId === 'image' ? '시각 모델로 읽는 중' : '텍스트는 건너뜀'"></div></div></div>
              <div class="ds-step ds-step--active"><div class="ds-step__rail"><span class="ds-step__marker"></span><span class="ds-step__line"></span></div><div class="ds-step__body"><div class="ds-step__title">메타 추출</div><div class="ds-step__detail">리드문 · 엔티티 · 인텐트 · 카테고리</div></div></div>
              <div class="ds-step ds-step--pending"><div class="ds-step__rail"><span class="ds-step__marker"></span><span class="ds-step__line"></span></div><div class="ds-step__body"><div class="ds-step__title">품질 판정</div><div class="ds-step__detail">G / R</div></div></div>
              <div class="ds-step ds-step--pending"><div class="ds-step__rail"><span class="ds-step__marker"></span></div><div class="ds-step__body"><div class="ds-step__title">완료</div></div></div>
            </div>
          </div>
        </div>

        <!-- 엑셀 배치 결과 + 인포그래픽 -->
        <div x-show="batchResult" x-cloak x-transition.opacity.duration.250ms class="mt-6 space-y-4">
          <!-- 집계 인포그래픽 -->
          <section class="panel">
            <div class="panel-hd"><b>집계</b><span class="meta tnum" x-text="batchResult ? (batchStats.n + '건 분석') : ''"></span></div>
            <div class="panel-bd space-y-5">
              <div class="tiles">
                <div class="tile"><div class="n tnum" x-text="batchStats.n"></div><div class="t">총 건수</div></div>
                <div class="tile"><div class="n tnum" x-text="batchStats.g"></div><div class="t">유통가능 G</div></div>
                <div class="tile"><div class="n tnum" x-text="batchStats.ents"></div><div class="t">엔티티 수</div></div>
                <div class="tile"><div class="n tnum" x-text="batchStats.avgLen"></div><div class="t">평균 리드문(자)</div></div>
              </div>
              <div class="flex items-center gap-6">
                <div class="ring" x-bind:style="'background:conic-gradient(var(--ds-primary) ' + batchStats.gPct + '%, var(--ds-hairline-soft) 0)'">
                  <i><span class="pv tnum" x-text="batchStats.gPct + '%'"></span><span class="pl">유통가능</span></i>
                </div>
                <div class="min-w-0 flex-1 space-y-2.5">
                  <div class="lbl" style="margin-bottom:2px">인텐트 분포 (상위 5)</div>
                  <template x-for="it in batchStats.intents" x-bind:key="it.k">
                    <div class="bar">
                      <span class="lab" x-text="it.k"></span>
                      <span class="track"><span class="fill" x-bind:style="'width:' + Math.max(it.pct, 4) + '%'"></span></span>
                      <span class="pc tnum" x-text="it.v + '건'"></span>
                    </div>
                  </template>
                  <div x-show="!batchStats.intents.length" class="text-xs text-muted">인텐트 데이터 없음</div>
                </div>
              </div>
            </div>
          </section>

          <section class="panel">
            <div class="panel-hd">
              <div class="flex items-center gap-2">
                <b>행별 결과</b>
                <span x-show="batchResult && batchResult.mock" class="inline-flex items-center rounded-md bg-[#ff9429]/15 px-2 py-0.5 text-xs font-semibold text-[#ff9429]">MOCK</span>
              </div>
              <button type="button" class="copybtn" x-on:click="exportBatchCsv()">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>
                CSV 내보내기
              </button>
            </div>
            <div class="overflow-auto">
              <table class="ds-table">
                <thead>
                  <tr><th>제목</th><th>리드문</th><th>엔티티</th><th>등급</th></tr>
                </thead>
                <tbody>
                  <template x-for="(it, i) in (batchResult ? batchResult.items : [])" x-bind:key="i">
                    <tr style="cursor:pointer" role="button" tabindex="0" x-on:click="openDetail(it)" x-on:keydown.enter="openDetail(it)" data-tip="상세·검수 열기" data-tip-pos="left">
                      <td class="text-ink" x-text="it.title || '·'"></td>
                      <td x-text="it.summary || '·'"></td>
                      <td><div class="flex flex-wrap gap-1"><template x-for="e in (it.entities || [])" x-bind:key="e"><span class="ds-badge ds-badge--entity" x-text="e"></span></template><span x-show="!(it.entities||[]).length">·</span></div></td>
                      <td><span class="ds-badge ds-badge--neutral" x-bind:class="it.grade === 'G' ? 'ds-badge--success' : 'ds-badge--error'"><span class="ds-badge__dot"></span><span x-text="it.grade || '·'"></span></span></td>
                    </tr>
                  </template>
                </tbody>
              </table>
            </div>
            <p x-show="batchResult && batchResult.mapping" class="px-4 py-2.5 text-xs text-muted" x-text="batchResult && batchResult.mapping ? ('매핑: ' + Object.entries(batchResult.mapping).map(e=>e[0]+'←'+e[1]).join(' · ')) : ''"></p>
          </section>
        </div>

        <!-- 단건 결과 -->
        <div x-show="result" x-cloak x-transition.opacity.duration.250ms class="mt-6 space-y-4">
          <section class="panel">
            <div class="panel-hd">
              <b>아이템 메타</b>
              <div class="flex items-center gap-2">
                <span x-show="q.finalGrade === 'G'" class="ds-badge ds-badge--success"><span class="ds-badge__dot"></span>유통가능 · G</span>
                <span x-show="q.finalGrade !== 'G'" class="ds-badge ds-badge--error"><span class="ds-badge__dot"></span>차단 · R</span>
                <span class="meta" x-text="result ? (result.output.routing.content_track + ' · ' + result.source) : ''"></span>
              </div>
            </div>
            <div class="drow">
              <div class="k">리드문</div>
              <div class="v">
                <div class="flex items-start gap-2">
                  <p class="flex-1 text-[15px] leading-relaxed text-ink" x-text="im.summary || '(빈 값 · 차단되었거나 본문 부족)'"></p>
                  <button type="button" class="copybtn shrink-0" x-show="im.summary" x-on:click="copyText(im.summary, '리드문')">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>
                    복사
                  </button>
                </div>
              </div>
            </div>
            <div class="drow">
              <div class="k">엔티티</div>
              <div class="v flex flex-wrap gap-1.5">
                <template x-for="x in (im.entities || [])" x-bind:key="x"><span class="ds-badge ds-badge--entity" x-text="x"></span></template>
                <span x-show="!(im.entities || []).length" class="text-xs text-muted">·</span>
              </div>
            </div>
            <div class="drow">
              <div class="k">인텐트</div>
              <div class="v flex flex-wrap gap-1.5">
                <template x-for="x in (im.intent || [])" x-bind:key="x"><span class="ds-badge ds-badge--intent" x-text="x"></span></template>
                <span x-show="!(im.intent || []).length" class="text-xs text-muted">·</span>
              </div>
            </div>
            <div class="drow">
              <div class="k">콘텐츠 카테고리</div>
              <div class="v flex flex-wrap gap-1.5">
                <template x-for="x in contentCats" x-bind:key="x"><span class="ds-badge ds-badge--category" x-text="x"></span></template>
                <span x-show="!contentCats.length" class="text-xs text-muted">·</span>
              </div>
            </div>
          </section>

          <!-- 이미지 추출 신호 -->
          <section x-show="result && result.signals && result.signals.length" x-cloak class="panel">
            <div class="panel-hd"><b>이미지 추출 신호</b><span class="meta tnum" x-text="result ? (result.signals.length + '장') : ''"></span></div>
            <div class="panel-bd space-y-3">
              <template x-for="(s, i) in (result ? result.signals : [])" x-bind:key="i">
                <div class="border-l border-black/[0.10] pl-3">
                  <div class="text-xs font-semibold text-ink" x-text="'이미지 ' + (i + 1)"></div>
                  <div class="mt-1 flex gap-2 text-sm text-body">
                    <svg class="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
                    <span x-text="s.vision || '·'"></span>
                  </div>
                  <div class="flex gap-2 text-sm text-body">
                    <svg class="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7V5h16v2M9 5v14m-3 0h6"/></svg>
                    <span x-text="s.ocr || '·'"></span>
                  </div>
                  <p x-show="s.note" x-cloak class="mt-1 text-xs text-[#ff9429]" x-text="s.note"></p>
                </div>
              </template>
            </div>
          </section>

          <!-- 상세 -->
          <section class="panel">
            <div class="panel-hd"><b>상세</b>
              <div class="flex items-center gap-2">
                <button type="button" class="copybtn" x-on:click="copyJSON()">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>
                  JSON 복사
                </button>
                <a href="/report" target="_blank" rel="noreferrer"
                   class="inline-flex items-center gap-1.5 text-xs font-medium text-violet transition-colors hover:text-ink">
                  전체 리포트 열기
                  <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M7 7h10v10"/></svg>
                </a>
              </div>
            </div>
            <div class="panel-bd">
              <details class="group">
                <summary class="cursor-pointer text-sm text-body transition-colors hover:text-ink">합성된 Content (이미지 → 4필드)</summary>
                <pre class="mt-2 overflow-auto rounded-lg border border-black/[0.08] bg-canvas p-3 font-mono text-xs text-body" x-text="result ? JSON.stringify(result.content, null, 2) : ''"></pre>
              </details>
              <details class="group mt-2">
                <summary class="cursor-pointer text-sm text-body transition-colors hover:text-ink">원본 출력 JSON</summary>
                <pre class="mt-2 overflow-auto rounded-lg border border-black/[0.08] bg-canvas p-3 font-mono text-xs text-body" x-text="result ? JSON.stringify(result.output, null, 2) : ''"></pre>
              </details>
            </div>
          </section>
        </div>
        <!-- 단건 처리 이력(마지막 수동 추출의 보정·비용·지연 · 관리자 추적용) -->
        <div x-show="result" class="space-y-4">
          <div class="panel"><div class="panel-hd"><b>처리 이력</b><span class="meta" x-text="tr.prompt_version || ''"></span><button type="button" class="copybtn" x-show="tr && tr.content_id !== undefined" x-on:click="exportEval()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>엑셀 다운로드</button></div><div class="panel-bd">
            <div class="drow"><div class="k">보정</div><div class="v flex flex-wrap gap-1.5">
              <template x-for="f in (tr.fallbacks || [])" x-bind:key="f"><span class="ds-badge ds-badge--category" x-text="f"></span></template>
              <span x-show="!(tr.fallbacks||[]).length" class="text-xs text-muted">없음</span>
            </div></div>
            <div class="drow"><div class="k">검증 verdict</div><div class="v flex flex-wrap gap-1.5">
              <template x-for="(v,i) in (tr.agent_verdicts || [])" x-bind:key="i"><span class="ds-badge ds-badge--intent" x-text="(typeof v==='string')?v:JSON.stringify(v)"></span></template>
              <span x-show="!(tr.agent_verdicts||[]).length" class="text-xs text-muted">없음</span>
            </div></div>
            <div class="drow"><div class="k">비용 · 토큰</div><div class="v text-sm text-body tnum" x-text="'$' + (tr.cost_usd||0).toFixed(4) + ' · ' + JSON.stringify(tr.tokens||{})"></div></div>
            <div class="drow"><div class="k">지연(ms)</div><div class="v text-sm text-body tnum" x-text="JSON.stringify(tr.latency_ms||{})"></div></div>
          </div></div>
        </div>
      </div>

      <!-- ═══ 모듈: 대시보드 (디자인 시스템: Stat · ProgressRing · ProgressBar) ═══ -->
      <!-- 모델 실행: 모아진 콘텐츠(자동/수동 불문)에 모델을 실행해 초안 생성 · 버전 안내 -->
      <div x-show="mod === 'content'" x-cloak class="w-full space-y-4" style="margin-top:14px">
        <section class="panel" data-fn><div class="panel-hd"><b>추가된 콘텐츠 · 용도</b><span class="meta">평가용은 검수 목록에서 제외되어 평가 전용으로 보존됩니다</span>
          <button type="button" class="ds-iconbtn ds-iconbtn--bordered ml-auto" x-on:click="loadDash()" data-tip="새로고침" data-tip-pos="bottom" aria-label="콘텐츠 목록 새로고침"><svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M20 11a8 8 0 1 0-.9 4.5M20 5v6h-6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
        </div>
          <div class="overflow-auto" style="max-height:320px"><table class="ds-table"><thead><tr><th>콘텐츠</th><th style="width:110px">서비스</th><th style="width:170px">모델</th><th style="width:70px">버전</th><th style="width:90px">용도</th><th style="width:110px"></th></tr></thead><tbody>
            <template x-for="c in ((dashData && dashData.contents) || [])" x-bind:key="'pp'+c.hash">
              <tr>
                <td class="text-ink" x-text="c.title || '(제목 없음)'"></td>
                <td class="text-muted" x-text="c.service || '·'"></td>
                <td class="text-muted" x-text="c.model || '·'"></td>
                <td class="tnum" x-text="c.version ? ('v' + c.version) : '·'"></td>
                <td><span class="ds-badge" x-bind:class="c.purpose === 'eval' ? 'ds-badge--warning' : 'ds-badge--neutral'" x-text="c.purpose === 'eval' ? '평가용' : '검수용'"></span></td>
                <td><button type="button" class="ds-btn ds-btn--outline" style="height:26px;padding:0 10px;font-size:11px" x-on:click="togglePurpose(c)" x-text="c.purpose === 'eval' ? '검수용 전환' : '평가용 전환'"></button></td>
              </tr>
            </template>
          </tbody></table></div>
          <div x-show="!((dashData && dashData.contents) || []).length" class="text-xs text-muted" style="margin:0 16px 14px">아직 추가된 콘텐츠가 없습니다 · 위에서 수동·자동으로 추가하세요</div>
        </section>
      </div>
      <div x-show="mod === 'content'" x-cloak class="w-full" style="margin:18px 0 12px">
        <div class="stepline"><span class="stepline__no">STEP 2</span><b>모델 실행</b><span class="meta">모은 콘텐츠에 모델을 실행해 초안을 만듭니다</span></div>
      </div>
      <div x-show="mod === 'content'" x-cloak class="w-full space-y-4">
        <ul class="ds-bullets hintbox" style="padding:14px 16px">
          <li>추가된 콘텐츠(자동·수동 불문)에 <b>모델을 실행</b>해 검수용 초안을 만듭니다 · 여기는 <b>수동 실행</b>입니다.</li>
          <li>실행 결과는 <b>버전 v(학습 반영 회차+1)</b> 로 기록됩니다 · 현재 다음 실행 버전: <b class="text-ink tnum" x-text="verTxt"></b></li>
          <li><b>자동 프로세스</b>: 학습 반영(매일 04:00)이 끝나면 개선된 프롬프트의 새 버전으로 <b>자동 재실행</b>되도록 설정할 수 있습니다(설정 auto_rerun_after_batch · 기본 꺼짐, 비용 발생).</li>
        </ul>
        <!-- 같은 콘텐츠를 다른 모델로 재실행(초안 재생성 · 이전 초안은 이력 보존 후 덮어씀) -->
        <section class="panel" data-fn><div class="panel-hd"><b>단건 실행</b><span class="meta">콘텐츠 하나를 지정 모델로 초안 재생성</span></div>
          <div class="panel-bd">
            <ul class="ds-bullets" style="margin-bottom:10px"><li>선택한 콘텐츠의 초안을 <b>지정 모델</b>로 다시 만듭니다 · 기존 초안은 이력에 남기고 덮어씁니다.</li><li>모델별 초안 품질 비교·교정 귀속에 사용하세요.</li></ul>
            <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
              <select class="field" style="flex:1;min-width:240px;height:38px" x-model="rerunHash">
                <option value="">콘텐츠 선택…</option>
                <template x-for="c in (dashData ? dashData.contents : [])" x-bind:key="c.hash">
                  <option x-bind:value="c.hash" x-text="(c.title || '(제목 없음)') + (c.model ? (' · 현재 ' + c.model) : '')"></option>
                </template>
              </select>
              <select class="field" style="width:auto;min-width:170px;height:38px" x-model="rerunModel">
                <option value="">모델 선택…</option>
                <template x-for="m in availableModels" x-bind:key="m"><option x-bind:value="m" x-text="m"></option></template>
              </select>
              <button type="button" class="ds-btn ds-btn--primary ds-btn--s-md" x-bind:disabled="rerunBusy || !rerunHash || !rerunModel" x-on:click="runRerun()" x-text="rerunBusy ? '재실행 중…' : '재실행'"></button>
              <span class="text-xs text-muted" x-text="rerunMsg"></span>
            </div>
          </div>
        </section>
        <!-- 일괄 실행: 모아진 콘텐츠 전체를 지정 모델로 -->
        <section class="panel" data-fn><div class="panel-hd"><b>일괄 실행</b><span class="meta">모든 콘텐츠 · 지정 모델로 초안 일괄 생성</span></div>
          <div class="panel-bd">
            <ul class="ds-bullets" style="margin-bottom:10px"><li>모아진 콘텐츠 전체를 지정 모델로 실행합니다 · 기존 초안은 이력에 남기고 덮어씁니다(건당 비용 발생).</li></ul>
            <div class="filterbar" style="margin:0">
              <select class="field" style="width:auto;min-width:170px;height:36px" x-model="bulkModel">
                <option value="">모델 선택…</option>
                <template x-for="m in availableModels" x-bind:key="'bk'+m"><option x-bind:value="m" x-text="m"></option></template>
              </select>
              <button type="button" class="ds-btn ds-btn--primary ds-btn--s-md" x-bind:disabled="bulkBusy || !bulkModel" x-on:click="runBulk()" x-text="bulkBusy ? '일괄 실행 중…' : ('일괄 실행 (' + ((dashData&&dashData.contents)||[]).length + '건)')"></button>
              <span class="text-xs text-muted" x-text="bulkMsg"></span>
            </div>
          </div>
        </section>
      </div>
      <!-- ═══ 모듈: 콘텐츠 검수(멤버) · 탭: 검수 대상 콘텐츠(기본) | 결과 비교 ═══ -->
      <div x-show="mod === 'create'" x-cloak class="w-full" style="margin-bottom:10px"><div class="evaltabs">
        <button type="button" x-bind:class="createTab==='raw'?'sel':''" x-on:click="createTab='raw'; loadRaw()">검수 대상 콘텐츠</button>
        <button type="button" x-bind:class="createTab==='edit'?'sel':''" x-on:click="createTab='edit'; loadModelStats(); loadRaw()">결과 비교</button>
      </div></div>
      <div x-show="mod === 'create' && createTab === 'edit'" x-cloak class="w-full">
        <div class="space-y-4">
          <!-- 요소 단위 모델별 결과 현황: 같은 정보요소를 모델 축으로 비교 -->
          <!-- A/B 선택: 비교할 모델과 버전 지정(별도 패널 · abslot 디자인) -->
          <section class="panel" data-fn><div class="panel-hd"><b>A/B 선택</b><span class="meta">비교할 모델과 버전을 A·B 슬롯에 지정</span>
            <button type="button" class="ds-iconbtn ds-iconbtn--bordered ml-auto" x-on:click="loadModelStats()" data-tip="새로고침" data-tip-pos="bottom" aria-label="모델·버전 목록 새로고침"><svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M20 11a8 8 0 1 0-.9 4.5M20 5v6h-6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
          </div>
            <div class="panel-bd">
              <div class="filterbar" style="margin:0 16px">
                <span class="selctl selctl--a abslot"><span class="selctl__tag">A</span>
                  <span class="selctl__lbl">모델</span>
                  <select class="field" x-model="abAm" x-on:change="abAv=String((msVers(abAm)[0]||''))"><template x-for="m in msModels" x-bind:key="'am'+m"><option x-bind:value="m" x-text="m"></option></template></select>
                  <span class="selctl__lbl">버전</span>
                  <select class="field" x-model="abAv"><template x-for="v in msVers(abAm)" x-bind:key="'av'+v"><option x-bind:value="String(v)" x-text="'v' + v"></option></template></select>
                </span>
                <span class="abvs">VS</span>
                <span class="selctl selctl--b abslot"><span class="selctl__tag">B</span>
                  <span class="selctl__lbl">모델</span>
                  <select class="field" x-model="abBm" x-on:change="abBv=String((msVers(abBm)[0]||''))"><template x-for="m in msModels" x-bind:key="'bm'+m"><option x-bind:value="m" x-text="m"></option></template></select>
                  <span class="selctl__lbl">버전</span>
                  <select class="field" x-model="abBv"><template x-for="v in msVers(abBm)" x-bind:key="'bv'+v"><option x-bind:value="String(v)" x-text="'v' + v"></option></template></select>
                </span>
              </div>
            </div>
          </section>
          <!-- 비교 결과: 요소별 현황 표 + 콘텐츠별 비교 표(상단 A/B 기준) -->
          <section class="panel" data-fn><div class="panel-hd"><b>비교 결과</b><span class="meta">상단 A/B 기준 · 요소별 현황과 콘텐츠별 비교</span></div>
            <div class="panel-bd">
              <div class="subhd" style="margin-top:4px">요소별 현황 <span class="meta">막대 = 유통 가능 비율 · ▲ = 우세</span></div>
              <template x-if="abCols.length">
                <div class="overflow-auto"><table class="ds-table"><thead><tr><th style="width:130px">항목</th>
                  <template x-for="(m,mi) in abCols" x-bind:key="'h'+mi"><th><span class="selctl__tag" x-bind:style="mi ? 'background:#ff6a3d' : 'background:var(--ds-violet,#1e84ff)'" x-text="mi ? 'B' : 'A'"></span> <span x-text="m.key"></span></th></template>
                </tr></thead><tbody>
                  <tr><td class="text-ink">유통 가능 G</td><template x-for="(m,mi) in abCols" x-bind:key="'g'+mi"><td>
                    <span class="abbar" x-bind:class="mi ? 'abbar--b' : ''">
                      <span class="abbar__track"><span class="abbar__fill" x-bind:style="'width:' + Math.max(m.gPct, 3) + '%'"></span></span>
                      <b class="tnum" x-text="m.gPct + '%'"></b><span class="abwin" x-show="abWin('gPct', mi)">▲</span>
                    </span></td></template></tr>
                  <tr><td class="text-ink">처리 건수</td><template x-for="(m,mi) in abCols" x-bind:key="'n'+mi"><td><b class="tnum" x-text="m.n"></b> <span class="abwin" x-show="abWin('n', mi)">▲</span></td></template></tr>
                  <tr><td class="text-ink">평균 리드문(자)</td><template x-for="(m,mi) in abCols" x-bind:key="'l'+mi"><td class="tnum" x-text="m.avgLead"></td></template></tr>
                  <tr><td class="text-ink">인텐트 상위</td><template x-for="(m,mi) in abCols" x-bind:key="'i'+mi"><td><template x-for="t in (m.intents||[])" x-bind:key="'it'+mi+t"><span class="ds-badge ds-badge--intent" style="margin:1px" x-text="t"></span></template><span x-show="!(m.intents||[]).length" class="text-xs text-muted">·</span></td></template></tr>
                  <tr><td class="text-ink">카테고리 상위</td><template x-for="(m,mi) in abCols" x-bind:key="'c'+mi"><td><template x-for="t in (m.categories||[])" x-bind:key="'ct'+mi+t"><span class="ds-badge ds-badge--category" style="margin:1px" x-text="t"></span></template><span x-show="!(m.categories||[]).length" class="text-xs text-muted">·</span></td></template></tr>
                  <tr><td class="text-ink">품질 사유 상위</td><template x-for="(m,mi) in abCols" x-bind:key="'r'+mi"><td><template x-for="t in (m.reasons||[])" x-bind:key="'rt'+mi+t"><span class="ds-badge ds-badge--reason" style="margin:1px" x-text="t"></span></template><span x-show="!(m.reasons||[]).length" class="text-xs text-muted">·</span></td></template></tr>
                </tbody></table></div>
              </template>
              <div x-show="!abCols.length" class="text-xs text-muted" style="margin:0 16px 10px">모델·버전 결과가 쌓이면 A/B 비교가 표시됩니다 · <b class="text-ink">콘텐츠 관리 · 모델 실행</b>으로 초안을 만들어 보세요</div>
              <div class="subhd">콘텐츠별 비교 <span class="meta">클릭 = 팝업에서 A/B 초안 나란히</span></div>
              <div class="overflow-auto" style="max-height:420px"><table class="ds-table"><thead><tr><th style="width:52px">등급</th><th>콘텐츠</th><th style="width:100px">서비스</th><th style="width:150px">현재 모델</th><th style="width:56px">버전</th></tr></thead><tbody>
                <template x-for="r in ((rawData||{}).items||[])" x-bind:key="'cmp'+r.hash">
                  <tr style="cursor:pointer" role="button" tabindex="0" x-on:click="openCmpModal(r)" x-on:keydown.enter="openCmpModal(r)" data-tip="초안 비교 팝업 열기" data-tip-pos="top">
                    <td><span class="ds-badge" x-bind:class="r.grade==='G' ? 'ds-badge--success' : 'ds-badge--neutral'" x-text="r.grade||'·'"></span></td>
                    <td class="text-ink" x-text="r.title || '(제목 없음)'"></td>
                    <td class="text-muted" x-text="r.service"></td>
                    <td class="text-xs text-muted tnum" x-text="r.model || '·'"></td>
                    <td class="text-xs text-muted tnum" x-text="r.version != null ? ('v' + r.version) : '·'"></td>
                  </tr>
                </template>
              </tbody></table>
              <div x-show="!((rawData||{}).items||[]).length" class="text-xs text-muted" style="padding:10px">데이터가 없습니다 · <b class="text-ink">콘텐츠 관리</b>에서 콘텐츠를 추가하세요</div>
              </div>
            </div>
          </section>
        </div>
      </div>

      <!-- 콘텐츠별 초안 비교 팝업 -->
      <div class="ds-dialog-backdrop" x-show="cmpModalOpen" x-cloak x-transition.opacity x-on:mousedown.self="cmpModalOpen=false" style="z-index:74">
        <div class="badgemodal" x-show="cmpModalOpen" x-transition style="max-width:860px">
          <div class="panel-hd" style="padding:0 0 12px;margin-bottom:12px;border-bottom:1px solid var(--ds-hairline,rgba(0,0,0,.08))"><b>모델별 콘텐츠 상세 비교</b><span class="meta" x-text="cmpTitle"></span>
            <button type="button" class="ds-iconbtn ml-auto" x-on:click="cmpModalOpen=false" aria-label="닫기"><svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></button>
          </div>
          <!-- 상단(모델·버전별 결과 현황)에서 설정한 A/B 를 그대로 상속(공통 콘텐츠 대상) -->
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px" x-show="draftsData && draftsData.items.length">
            <span class="selctl selctl--a"><span class="selctl__tag">A</span>
              <span class="text-xs text-ink" style="padding:0 4px" x-text="abAm ? (abAm + ' · v' + abAv) : '미설정'"></span>
              <span class="ds-badge ds-badge--warning" x-show="cmpAmiss" data-tip="이 콘텐츠에는 A 슬롯 초안이 없어 가장 가까운 초안을 표시합니다" data-tip-pos="top">초안 없음</span>
            </span>
            <span class="text-xs text-muted">vs</span>
            <span class="selctl selctl--b"><span class="selctl__tag">B</span>
              <span class="text-xs text-ink" style="padding:0 4px" x-text="abBm ? (abBm + ' · v' + abBv) : '미설정'"></span>
              <span class="ds-badge ds-badge--warning" x-show="cmpBmiss" data-tip="이 콘텐츠에는 B 슬롯 초안이 없어 가장 가까운 초안을 표시합니다" data-tip-pos="top">초안 없음</span>
            </span>
          </div>
          <template x-if="draftPair">
            <div class="overflow-auto" style="max-height:60vh"><table class="ds-table"><thead><tr><th style="width:100px">필드</th><th><span class="selctl__tag" style="background:var(--ds-violet,#1e84ff)">A</span> <span x-text="draftPair.l.label"></span></th><th><span class="selctl__tag" style="background:#ff6a3d">B</span> <span x-text="draftPair.r.label"></span></th></tr></thead><tbody>
              <template x-for="f in draftDiff" x-bind:key="'MF'+f.k">
                <tr x-bind:class="f.diff ? 'is-sel' : ''">
                  <td class="text-ink"><span x-text="f.k"></span> <span class="ds-badge ds-badge--error" x-show="f.diff" style="margin-left:4px">다름</span></td>
                  <td class="text-xs" x-text="f.l"></td>
                  <td class="text-xs" x-text="f.r"></td>
                </tr>
              </template>
            </tbody></table></div>
          </template>
          <div x-show="draftsData && draftsData.items.length < 2" class="text-xs text-muted" style="margin-top:8px">비교할 초안이 하나뿐입니다 · <b class="text-ink">콘텐츠 관리 · 다른 모델로 재실행</b>으로 다른 모델 초안을 만들어 보세요</div>
        </div>
      </div>

      <!-- ═══ 모듈: 품질 메타 ═══ -->
      <!-- ═══ 모듈: 실험실(관리자) · 지금 테스트하지 않는 탐구 요소 ═══ -->
      <div x-show="mod === 'lab'" x-cloak class="w-full" style="margin-bottom:10px">
        <div class="evaltabs">
          <button type="button" x-bind:class="labTab==='legal'?'sel':''" x-on:click="labTab='legal'">법령</button>
          <button type="button" x-bind:class="labTab==='topic'?'sel':''" x-on:click="labTab='topic'; loadTopics()">토픽</button>
          <button type="button" x-bind:class="labTab==='user'?'sel':''" x-on:click="labTab='user'; loadUser()">사용자</button>
        </div>
        <ul class="ds-bullets hintbox" style="padding:12px 16px;margin-top:10px"><li>지금 테스트 대상이 아닌 <b>탐구 요소</b>를 모아둔 공간입니다 · 테스트 대상으로 확정되면 본 메뉴로 승격합니다.</li></ul>
      </div>
      <div x-show="mod === 'lab' && labTab === 'legal'" x-cloak class="w-full space-y-4">
        <div class="panel"><div class="panel-bd flex items-center justify-between gap-3">
          <ul class="ds-bullets"><li>법령 1차 필터(13종 위반 라우팅·스코어링)를 추출에 포함합니다.</li><li>켜면 다음 추출부터 적용됩니다(추가 호출).</li></ul>
          <label class="inline-flex cursor-pointer items-center gap-2 text-[13px] text-body">
            <input type="checkbox" x-model="legalEnabled" x-on:change="toggleLegal()" class="h-4 w-4 rounded border-black/15 bg-canvas text-violet">
            법령 필터 포함
          </label>
        </div></div>
        <div x-show="!result" class="empty"><b class="text-body">실행 · 추출</b>에서 단건 추출을 실행하면 그 콘텐츠의 품질·법령 판정 상세가 여기에 표시됩니다</div>
        <div x-show="result" class="space-y-4">
          <div class="panel"><div class="panel-hd"><b>유통 판정</b>
            <span x-show="qm.finalGrade === 'G'" class="ds-badge ds-badge--success"><span class="ds-badge__dot"></span>유통 가능 · G</span>
            <span x-show="qm.finalGrade !== 'G'" class="ds-badge ds-badge--error"><span class="ds-badge__dot"></span>차단 · R</span>
          </div><div class="panel-bd">
            <div class="drow"><div class="k">검수</div><div class="v text-sm text-body" x-text="(qm.review || 'auto') + (qm.confidence != null ? (' · conf ' + qm.confidence) : '')"></div></div>
            <div class="drow"><div class="k">품질 사유</div><div class="v flex flex-wrap gap-1.5">
              <template x-for="r in (qm.reasons || [])" x-bind:key="r"><span class="ds-badge ds-badge--category" x-text="r"></span></template>
              <span x-show="!(qm.reasons || []).length" class="text-xs text-muted">없음(통과)</span>
            </div></div>
            <div class="drow"><div class="k">법령</div><div class="v">
              <span class="text-sm text-body" x-text="lm.enabled ? ('대표등급 ' + lm.representative_grade + ' · ' + lm.representative_score) : '법령 필터 비활성(옵션)'"></span>
              <div class="mt-1.5 flex flex-wrap gap-1.5"><template x-for="h in (lm.harm_types || [])" x-bind:key="h.code"><span class="ds-badge ds-badge--intent" x-text="h.code + ' · ' + h.grade"></span></template></div>
            </div></div>
          </div></div>
        </div>
      </div>

      <!-- ═══ 모듈: 토픽 (품질 · 토픽 통합 뷰) ═══ -->
      <div x-show="mod === 'lab' && labTab === 'topic'" x-cloak class="w-full space-y-4">
        <template x-if="!topicData || !topicData.n_contents"><div class="empty">아직 토픽을 만들 결과가 없습니다 <b class="text-body">실행 · 추출</b>에서 여러 건(엑셀 일괄)을 추출하세요</div></template>
        <div x-show="topicData && topicData.n_contents" class="space-y-4">
          <div class="tiles" style="grid-template-columns:repeat(3,1fr)">
            <div class="tile"><div class="n tnum" x-text="topicData?(topicData.summary.single||0):0"></div><div class="t">엔티티형</div></div>
            <div class="tile"><div class="n tnum" x-text="topicData?(topicData.summary.composite||0):0"></div><div class="t">사건형</div></div>
            <div class="tile"><div class="n tnum" x-text="topicData?(topicData.summary.filter||0):0"></div><div class="t">조건형</div></div>
          </div>
          <div class="panel"><div class="panel-hd"><b>엔티티형 · 사건형 토픽</b><span class="meta tnum" x-text="topicData ? (topicData.n_contents + '건 기준') : ''"></span><button type="button" class="copybtn" x-on:click="exportTopics()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>엑셀 다운로드</button></div>
            <div class="overflow-auto"><table class="ds-table"><thead><tr><th>유형</th><th>클러스터</th><th>대표 엔티티</th><th>멤버</th></tr></thead><tbody>
              <template x-for="t in (topicData?topicData.single:[])" x-bind:key="t.cluster_id"><tr style="cursor:pointer" role="button" tabindex="0" x-on:click="topicDrill(t)" x-on:keydown.enter="topicDrill(t)" data-tip="묶인 콘텐츠 보기" data-tip-pos="left"><td>엔티티형</td><td class="text-ink" x-text="t.cluster_id"></td><td x-text="(t.entities||t.rep_entities||[]).join(' · ')"></td><td x-text="t.n_contents || (t.count||(t.content_ids?t.content_ids.length:''))"></td></tr></template>
              <template x-for="t in (topicData?topicData.composite:[])" x-bind:key="t.cluster_id"><tr style="cursor:pointer" role="button" tabindex="0" x-on:click="topicDrill(t)" x-on:keydown.enter="topicDrill(t)" data-tip="묶인 콘텐츠 보기" data-tip-pos="left"><td>사건형</td><td class="text-ink" x-text="t.cluster_id"></td><td x-text="(t.rep_entities||t.entities||[]).join(' · ')"></td><td x-text="t.n_contents || (t.count||(t.content_ids?t.content_ids.length:''))"></td></tr></template>
              <template x-if="!(topicData&&(topicData.single.length||topicData.composite.length))"><tr><td colspan="4" class="text-muted">엔티티 공유 클러스터 없음(데이터가 많을수록 형성)</td></tr></template>
            </tbody></table></div>
          </div>
          <div class="panel"><div class="panel-hd"><b>조건형 토픽</b><span class="meta">관심사 × 소비 방식</span></div><div class="panel-bd flex flex-wrap gap-1.5">
            <template x-for="t in (topicData?topicData.filter:[])" x-bind:key="t.cluster_id"><span class="ds-badge ds-badge--neutral" style="cursor:pointer" role="button" tabindex="0" x-bind:class="t.active ? 'ds-badge--entity' : 'ds-badge--category'" x-on:click="topicDrill(t)" x-on:keydown.enter="topicDrill(t)" data-tip="묶인 콘텐츠 보기" x-text="(t.name||t.label) + (t.active?(' · '+(t.n_contents||'')):'')"></span></template>
          </div></div>
        </div>
      </div>

      <!-- ═══ 모듈: 사전 · 매핑 ═══ -->
      <div x-show="mod === 'dict'" x-cloak class="w-full space-y-4">
        <div class="panel"><div class="panel-bd flex items-center justify-between gap-3">
          <ul class="ds-bullets">
            <li>각 체계의 정책(사전·카테고리·법령)을 <b>직접 수정</b>할 수 있습니다.</li>
            <li>저장 시 즉시 추출에 반영되고 로컬에 영속됩니다.</li>
          </ul>
          <button type="button" x-on:click="resetDict()" class="ds-btn ds-btn--outline ds-btn--c-danger ds-btn--s-sm">편집 초기화</button>
        </div></div>

        <!-- 편집은 팝업(편집 다이얼로그)에서 · 화면 하단 정의 -->

        <div x-show="dictData" class="space-y-4">
          <div class="panel"><div class="panel-hd"><b>인텐트 · 범용(8)</b>
            <button type="button" class="copybtn" x-on:click="startEdit('intent_universal', null, dictData.intentUniversal, 'list', '인텐트 범용')">편집</button>
          </div><div class="panel-bd flex flex-wrap gap-1.5">
            <template x-for="i in (dictData?dictData.intentUniversal:[])" x-bind:key="i"><span class="ds-badge ds-badge--intent" x-text="i"></span></template>
          </div></div>
          <div class="panel"><div class="panel-hd"><b>인텐트 · 서비스별</b>
            <select x-model="dictGroup" class="field" style="width:auto;height:32px;padding:0 28px 0 10px">
              <template x-for="g in (dictData?dictData.serviceGroups:[])" x-bind:key="g"><option x-bind:value="g" x-text="g"></option></template>
            </select>
            <button type="button" class="copybtn" x-on:click="startEdit('intent_by_service', dictGroup, (dictData.intentByService[dictGroup]||[]), 'list', '인텐트 · ' + dictGroup)">편집</button>
          </div><div class="panel-bd flex flex-wrap gap-1.5">
            <template x-for="i in (dictData && dictData.intentByService[dictGroup] ? dictData.intentByService[dictGroup] : [])" x-bind:key="i"><span class="ds-badge ds-badge--intent" x-text="i"></span></template>
            <span x-show="!(dictData && dictData.intentByService[dictGroup] && dictData.intentByService[dictGroup].length)" class="text-xs text-muted">항목 없음</span>
          </div></div>
          <div class="panel"><div class="panel-hd"><b>콘텐츠 카테고리 · Tier1 / Tier2</b><span class="meta tnum" x-text="dictData ? (dictData.iabTier1.length + ' Tier1') : ''"></span>
            <button type="button" class="copybtn ml-auto" x-on:click="startEdit('iab_tier1', null, dictData.iabTier1, 'list', 'Tier1 목록')">Tier1 편집</button>
          </div>
            <div class="overflow-auto" style="max-height:340px"><table class="ds-table"><thead><tr><th style="width:210px">Tier1</th><th>Tier2</th><th style="width:52px" class="tnum" x-text="dictData ? (dictData.iabTier1.length) : ''"></th></tr></thead><tbody>
              <template x-for="c in (dictData?dictData.iabTier1:[])" x-bind:key="c">
                <tr>
                  <td class="text-ink" style="font-weight:600;vertical-align:top" x-text="c"></td>
                  <td><div class="flex flex-wrap gap-1.5">
                    <template x-for="t2 in (dictData && dictData.tier2[c] ? dictData.tier2[c] : [])" x-bind:key="t2"><span class="ds-badge ds-badge--category" x-text="t2"></span></template>
                    <span x-show="!(dictData && dictData.tier2[c] && dictData.tier2[c].length)" class="text-xs text-muted">항목 없음</span>
                  </div></td>
                  <td style="vertical-align:top"><button type="button" class="copybtn" x-on:click="startEdit('tier2', c, (dictData.tier2[c]||[]), 'list', 'Tier2 · ' + c)">편집</button></td>
                </tr>
              </template>
            </tbody></table></div>
          </div>
          <div class="grid grid-cols-2 gap-4">
            <div class="panel"><div class="panel-hd"><b>도메인 그룹</b><span class="meta">Tier1 7묶음</span></div>
              <div class="overflow-auto" style="max-height:280px"><table class="ds-table"><thead><tr><th style="width:120px">그룹</th><th>포함 Tier1</th><th style="width:52px"></th></tr></thead><tbody>
                <template x-for="(ts,g) in (dictData?dictData.domainGroups:{})" x-bind:key="g">
                  <tr><td style="vertical-align:top"><span class="ds-badge ds-badge--entity" x-text="g"></span></td>
                    <td class="text-muted" x-text="ts.join(' · ')"></td>
                    <td style="vertical-align:top"><button type="button" class="copybtn" x-on:click="startEdit('domain_groups', g, ts, 'list', '도메인 그룹 · ' + g)">편집</button></td></tr>
                </template>
              </tbody></table></div></div>
            <div class="panel"><div class="panel-hd"><b>자사 ↔ IAB v3.0 매핑</b><span class="meta tnum" x-text="dictData?Object.keys(dictData.iabMap).length+'건':''"></span></div>
              <div class="overflow-auto" style="max-height:280px"><table class="ds-table"><thead><tr><th>자사 경로</th><th>IAB 공식</th><th></th></tr></thead><tbody>
                <template x-for="(v,k) in (dictData?dictData.iabMap:{})" x-bind:key="k"><tr><td class="text-ink" x-text="k"></td><td><div class="tbox" x-text="v"></div></td>
                  <td><button type="button" class="copybtn" x-on:click="startEdit('category_iab_map', k, v, 'text', '매핑 · ' + k)">편집</button></td></tr></template>
              </tbody></table></div></div>
          </div>
          <div class="grid grid-cols-2 gap-4">
            <div class="panel"><div class="panel-hd"><b>품질 메타</b><span class="meta tnum" x-text="dictData?Object.keys(dictData.qualityMetas).length+'종':''"></span></div>
              <div class="overflow-auto" style="max-height:280px"><table class="ds-table"><thead><tr><th>ID</th><th>메타명 · 정의</th><th>적용</th><th></th></tr></thead><tbody>
                <template x-for="(v,k) in (dictData?dictData.qualityMetas:{})" x-bind:key="k"><tr>
                  <td class="text-ink" x-text="k"></td>
                  <td><div class="tbox"><span class="nm" x-text="(dictData.qualityNames&&dictData.qualityNames[k])||''"></span><span x-text="v"></span></div></td>
                  <td><span class="ds-badge ds-badge--neutral" x-bind:class="(dictData.qualityApplies&&dictData.qualityApplies[k]==='ugc')?'ds-badge--intent':'ds-badge--category'" x-text="(dictData.qualityApplies&&dictData.qualityApplies[k]==='ugc')?'UGC':'전체'"></span></td>
                  <td><button type="button" class="copybtn" x-on:click="startEdit('quality_metas', k, v, 'text', '품질 · ' + k)">편집</button></td>
                </tr></template>
              </tbody></table></div></div>
            <div class="panel"><div class="panel-hd"><b>법령 위반 유형</b><span class="meta tnum" x-text="dictData?Object.keys(dictData.legalTypes).length+'종':''"></span></div>
              <div class="overflow-auto" style="max-height:280px"><table class="ds-table"><thead><tr><th>코드</th><th>유형</th><th>근거</th><th></th></tr></thead><tbody>
                <template x-for="(v,k) in (dictData?dictData.legalTypes:{})" x-bind:key="k"><tr><td class="text-ink" x-text="k"></td><td><div class="tbox" x-text="v.label"></div></td><td class="text-muted" x-text="v.article"></td><td><button type="button" class="copybtn" x-on:click="startEditLegal(k, v)">편집</button></td></tr></template>
              </tbody></table></div></div>
          </div>
        </div>
      </div>

      <!-- ═══ 모듈: 사용자 메타 ═══ -->
      <div x-show="mod === 'lab' && labTab === 'user'" x-cloak class="w-full space-y-4">
        <div class="panel"><div class="panel-bd">
          <div class="flex items-center justify-between gap-3 flex-wrap">
            <ul class="ds-bullets"><li>행동 로그(TIARA형)를 올리면 추출 콘텐츠와 조인해 <b>소비 형태 · 강도 · 선호</b>를 산출합니다.</li><li><code class="text-violet">content_id</code> = 추출 순서(0부터).</li></ul>
            <div class="flex items-center gap-2">
              <a href="/usermeta-template.csv" download class="ds-btn ds-btn--secondary ds-btn--s-sm">템플릿</a>
              <label class="ds-btn ds-btn--primary ds-btn--s-sm" style="cursor:pointer">
                행동 로그 업로드
                <input type="file" accept=".csv,.tsv,.jsonl,.json" class="sr-only" x-on:change="uploadUserLog($event)">
              </label>
            </div>
          </div>
        </div></div>

        <!-- 실데이터 사용자 -->
        <div x-show="userData && userData.users && userData.users.length" class="space-y-3">
          <template x-for="u in (userData?userData.users:[])" x-bind:key="u.user_id">
            <div class="panel"><div class="panel-hd">
              <b x-text="u.user_id"></b>
              <span class="ds-badge ds-badge--entity" x-text="u.persona"></span>
              <span class="meta tnum ml-auto" x-text="'조회 ' + u.engagement.views + ' · 클릭률 ' + u.engagement.click_rate + ' · 평균체류 ' + u.engagement.avg_dwell_sec + 's'"></span>
            </div><div class="panel-bd">
              <div class="drow"><div class="k">소비 형태</div><div class="v text-sm text-body" x-text="Object.entries(u.form).map(e=>e[0]+':'+e[1]).join(' · ')"></div></div>
              <div class="drow"><div class="k">소비 강도</div><div class="v flex flex-wrap gap-1.5">
                <template x-for="(v,k) in u.intensity" x-bind:key="k"><span class="ds-badge ds-badge--neutral" x-bind:class="v==='고'?'ds-badge--entity':(v==='중'?'ds-badge--intent':'ds-badge--category')" x-text="k + ' (' + v + ')'"></span></template>
              </div></div>
              <div class="drow"><div class="k">선호 엔티티</div><div class="v flex flex-wrap gap-1.5">
                <template x-for="e in (u.affinity_entities||[])" x-bind:key="e[0]"><span class="ds-badge ds-badge--entity" x-text="e[0]"></span></template>
                <span x-show="!(u.affinity_entities||[]).length" class="text-xs text-muted">·</span>
              </div></div>
            </div></div>
          </template>
        </div>

        <!-- 명세(페르소나 정의·공식) -->
        <div class="panel"><div class="panel-hd"><b>페르소나 정의 · 8종</b><span class="meta">형태 + 맥락별 강도 시그니처</span><button type="button" class="copybtn" x-show="userData && userData.users && userData.users.length" x-on:click="exportUsers()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>엑셀 다운로드</button></div>
          <div class="overflow-auto"><table class="ds-table"><thead><tr><th>페르소나</th><th>설명</th><th>형태(깊이·체류)</th></tr></thead><tbody>
            <template x-for="p in (userData?userData.personas_def:[])" x-bind:key="p.id">
              <tr><td class="text-ink" x-text="p.full || p.name"></td><td><div class="tbox" x-text="p.desc"></div></td><td x-text="(p.form['깊이']||'') + ' · ' + (p.form['체류·완주']||'')"></td></tr>
            </template>
            <template x-if="!(userData&&userData.personas_def&&userData.personas_def.length)"><tr><td colspan="3" class="text-muted">먼저 [실행·추출]에서 콘텐츠를 추출하세요</td></tr></template>
          </tbody></table></div>
          <div class="text-xs text-muted" style="margin:10px 16px 14px" x-show="userData && userData.formula" x-text="userData ? userData.formula : ''"></div>
        </div>
      </div>

      <!-- ═══ 모듈: 테스트셋 생성 · 골든/검수/원본 뷰 ═══ -->
      <!-- ═══ 모듈: 평가 · 테스트셋(골든) 기준 정합성 수치화 + 모델별 비교(단일 페이지) ═══ -->
      <div x-show="mod === 'evaluate'" x-cloak class="w-full space-y-4">
          <section class="panel" data-fn><div class="panel-hd"><b>평가 기준</b><span class="meta">어떤 모델·버전을 어떤 콘텐츠로 잴지 먼저 정합니다</span></div>
            <div class="panel-bd">
              <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
                <span class="selctl" data-tip="정답셋과 비교할 대상 모델 · 비워두면 현재 설정 모델" data-tip-pos="bottom"><span class="selctl__lbl">기준 모델</span>
                  <select class="field" x-model="evalModel"><option value="">현재 설정 모델</option><template x-for="m in availableModels" x-bind:key="'ev'+m"><option x-bind:value="m" x-text="m"></option></template></select></span>
                <span class="selctl" data-tip="프롬프트 버전 = 학습 반영 회차 + 1 · 평가는 항상 현재 버전으로 실행됩니다" data-tip-pos="bottom"><span class="selctl__lbl">프롬프트 버전</span>
                  <b class="tnum" style="padding:0 8px;font-size:13px;height:30px;display:inline-flex;align-items:center" x-text="verTxt"></b></span>
                <span style="display:flex;gap:6px;align-items:center;margin-left:6px"><span class="selctl__lbl">대상 콘텐츠</span>
                  <button type="button" class="srcfilter__chip" x-bind:class="evalScope==='all' ? 'sel' : ''" x-on:click="evalScope='all'">전체 정답셋</button>
                  <button type="button" class="srcfilter__chip" x-bind:class="evalScope==='eval' ? 'sel' : ''" x-on:click="evalScope='eval'">평가용만</button>
                </span>
              </div>
              <ul class="ds-bullets" style="margin:11px 0 0">
                <li><b>평가용</b> 콘텐츠는 <b>콘텐츠 관리 · STEP 1</b>에서 지정합니다 · 검수 목록에서 제외되어 오염 없이 평가에만 쓰입니다.</li>
                <li>평가는 항상 <b>현재 프롬프트 버전</b>으로 실행됩니다 · 버전 간 추이는 학습 반영을 거듭하며 재평가로 비교하세요.</li>
              </ul>
            </div>
          </section>
          <section class="panel"><div class="panel-hd"><b>평가 실행</b><span class="meta">위 기준으로 정답셋과 비교 · 요약과 건별 판정</span></div>
            <div class="panel-bd">
              <ul class="ds-bullets" style="margin-bottom:11px"><li>검수 합의로 쌓인 <b>정답셋(테스트셋)</b>과 모델 결과를 비교해 요약 수치와 <b>불일치 목록</b>을 만듭니다.</li><li>불일치 건은 검수처럼 <b>건별 판정</b>합니다 · <b>채택</b>=모델 결과가 맞음(정답 교정 후보) · <b>탈락</b>=정답 유지(모델 오답 확정).</li><li>평가 건수가 적으면 오차가 큽니다 · 신뢰구간이 겹치면 우열 판단을 미룹니다.</li></ul>
              <button type="button" class="ds-btn ds-btn--primary" x-bind:disabled="goldenBusy" x-on:click="runGolden()" x-text="goldenBusy ? '평가 중… (전건 추출)' : '평가 실행'"></button>
              <span class="text-xs text-muted" style="margin-left:10px" x-show="goldenResult && !goldenResult.ok" x-text="goldenResult ? goldenResult.error : ''"></span>
              <template x-if="goldenResult && goldenResult.ok">
                <div>
                  <!-- 결과 카드화: 산개한 숫자·막대를 타일과 박스로 묶어 시선 고정 -->
                  <div class="text-xs text-muted" style="margin-top:12px" x-show="goldenResult.basis">기준: <b class="text-ink" x-text="goldenResult.basis ? (goldenResult.basis.model || '현재 설정 모델') : ''"></b> · <span class="tnum" x-text="goldenResult.basis ? ('v' + goldenResult.basis.version) : ''"></span> · <span x-text="goldenResult.basis && goldenResult.basis.scope === 'eval' ? '평가용 콘텐츠' : '전체 정답셋'"></span></div>
                  <div class="tiles" style="grid-template-columns:repeat(4,1fr);margin-top:10px">
                    <div class="tile tile--hero"><div class="n tnum" x-text="Math.round((goldenResult.grade_accuracy||0)*100)+'%'"></div><div class="t">등급 일치율<span class="tnum" x-text="goldenResult.grade_ci ? (' · 신뢰구간 ' + pctTxt(goldenResult.grade_ci.lo) + '~' + pctTxt(goldenResult.grade_ci.hi)) : ''"></span></div></div>
                    <div class="tile"><div class="n tnum" x-text="Math.round((goldenResult.reason_jaccard||0)*100)+'%'"></div><div class="t">사유 일치</div></div>
                    <div class="tile"><div class="n tnum" x-text="Math.round((goldenResult.harm_miss_rate||0)*100)+'%'"></div><div class="t">유해 놓침</div></div>
                    <div class="tile"><div class="n tnum" x-text="goldenResult.evaluated"></div><div class="t">평가 건수</div></div>
                  </div>
                  <div class="tile" style="margin-top:10px">
                    <div class="t" style="margin:0 0 8px">유형별 일치율 · 어디가 약한지(수정 우선순위)</div>
                    <template x-for="(v,k) in (goldenResult.by_reason_bucket||{})" x-bind:key="k">
                      <div class="ds-progress" style="margin:7px 0"><div class="ds-progress__head"><span class="ds-progress__label"><span class="ds-badge ds-badge--reason" style="cursor:help" x-bind:data-tip="k==='normal' ? '문제 사유 없는 일반 콘텐츠' : termDef('reason', k)" data-tip-pos="top" x-text="k"></span> <span class="tnum text-muted" x-text="'('+v.n+')'"></span></span><span class="ds-progress__pct tnum" x-text="Math.round(v.grade_acc*100)+'%'"></span></div><div class="ds-progress__track"><div class="ds-progress__fill ds-progress__fill--primary" x-bind:style="'width:'+Math.max(v.grade_acc*100,3)+'%'"></div></div></div>
                    </template>
                  </div>
                  <div class="subhd" style="margin:16px 0 8px">평가 상세 · 불일치 건별 판정 <span class="meta">채택=모델 결과가 맞음(정답 교정 후보) · 탈락=정답 유지(모델 오답 확정)</span></div>
                  <template x-if="(goldenResult.detail||[]).length">
                    <div class="overflow-auto" style="max-height:340px"><table class="ds-table"><thead><tr><th>콘텐츠</th><th style="width:64px">정답</th><th style="width:84px">모델 결과</th><th style="width:160px">판정</th><th style="width:190px">합의</th></tr></thead><tbody>
                      <template x-for="d in goldenResult.detail" x-bind:key="'ej'+d.hash">
                        <tr>
                          <td class="text-ink" x-text="d.title || '(제목 없음)'"></td>
                          <td><span class="ds-badge" style="cursor:help" x-bind:class="d.expected==='G' ? 'ds-badge--success' : 'ds-badge--neutral'" x-bind:data-tip="termDef('grade', d.expected)" data-tip-pos="top" x-text="d.expected"></span></td>
                          <td><span class="ds-badge" style="cursor:help" x-bind:class="d.got==='G' ? 'ds-badge--success' : 'ds-badge--neutral'" x-bind:data-tip="termDef('grade', d.got)" data-tip-pos="top" x-text="d.got"></span></td>
                          <td><span style="display:inline-flex;gap:6px">
                            <button type="button" class="verdictbtn verdictbtn--good" style="height:26px;padding:0 10px;font-size:11px" x-bind:class="myEvalVote(d)==='adopt' ? 'is-on' : ''" data-tip="모델 결과가 맞아요 · 정답 교정 후보로 올립니다" data-tip-pos="top" x-on:click="evalJudge(d, 'adopt')">채택</button>
                            <button type="button" class="verdictbtn verdictbtn--bad" style="height:26px;padding:0 10px;font-size:11px" x-bind:class="myEvalVote(d)==='reject' ? 'is-on' : ''" data-tip="정답이 맞아요 · 모델 오답으로 확정합니다" data-tip-pos="top" x-on:click="evalJudge(d, 'reject')">탈락</button>
                          </span></td>
                          <td><span class="text-xs text-muted tnum" x-text="'채택 ' + ((d.judge&&d.judge.adopt)||0) + ' · 탈락 ' + ((d.judge&&d.judge.reject)||0)"></span>
                            <span class="ds-badge ds-badge--warning" style="cursor:help;margin-left:6px" x-show="evalConsensus(d)==='adopt'" data-tip="채택 합의 · 정답 교정 필요(테스트셋 관리 · 정답셋 목록에 '교정 필요'로 표시)" data-tip-pos="top">교정 필요</span>
                            <span class="ds-badge ds-badge--error" style="cursor:help;margin-left:6px" x-show="evalConsensus(d)==='reject'" data-tip="탈락 합의 · 모델 오답 확정(프롬프트 개선 우선순위 근거)" data-tip-pos="top">모델 오답</span>
                          </td>
                        </tr>
                      </template>
                    </tbody></table></div>
                  </template>
                  <div x-show="!(goldenResult.detail||[]).length" class="text-xs text-muted">불일치 없음 · 건별 판정할 항목이 없습니다</div>
                  <button type="button" class="ds-btn ds-btn--secondary" style="margin-top:12px" x-on:click="selectMod('prompt')">원천 프롬프트 수정하러 가기 →</button>
                </div>
              </template>
            </div>
          </section>
          <!-- 모델별 비교: 같은 정답셋을 여러 모델에 실호출 -->
          <section class="panel"><div class="panel-hd"><b>모델별 비교</b><span class="meta">같은 정답셋으로 A·B 모델을 실호출 비교</span></div>
            <div class="panel-bd">
              <div class="filterbar" style="margin:0 0 12px">
                <span class="selctl selctl--a abslot"><span class="selctl__tag">A</span><span class="selctl__lbl">모델</span>
                  <select class="field" x-model="cmpA"><option value="">선택…</option><template x-for="m in availableModels" x-bind:key="'ca'+m"><option x-bind:value="m" x-text="m"></option></template></select></span>
                <span class="abvs">VS</span>
                <span class="selctl selctl--b abslot"><span class="selctl__tag">B</span><span class="selctl__lbl">모델</span>
                  <select class="field" x-model="cmpB"><option value="">선택…</option><template x-for="m in availableModels" x-bind:key="'cb'+m"><option x-bind:value="m" x-text="m"></option></template></select></span>
                <button type="button" class="ds-btn ds-btn--primary ds-btn--s-md" x-bind:disabled="cmpBusy || !cmpA || !cmpB || cmpA===cmpB" x-on:click="runCompare()" x-text="cmpBusy ? '비교 중… (모델별 전건 추출)' : '비교 실행'"></button>
                <span class="text-xs text-muted" x-show="cmpResult && !cmpResult.ok" x-text="cmpResult ? cmpResult.error : ''"></span>
              </div>
              <template x-if="cmpResult && cmpResult.ok && cmpCols.length">
                <div>
                  <div class="overflow-auto"><table class="ds-table"><thead><tr><th style="width:150px">항목</th>
                    <template x-for="(m,mi) in cmpCols" x-bind:key="'ch'+mi"><th><span class="selctl__tag" x-bind:style="mi ? 'background:#ff6a3d' : 'background:var(--ds-violet,#1e84ff)'" x-text="mi ? 'B' : 'A'"></span> <span x-text="m.model"></span> <span class="ds-badge ds-badge--success" x-show="m.model===cmpResult.best">★ best</span></th></template>
                  </tr></thead><tbody>
                    <tr><td class="text-ink">호출</td><template x-for="(m,mi) in cmpCols" x-bind:key="'cr'+mi"><td><span class="ds-badge" x-bind:class="m.real ? 'ds-badge--neutral' : 'ds-badge--warning'" x-bind:data-tip="m.real ? '실제 API 호출 결과' : '모의 응답 · API 키를 설정하면 실호출됩니다'" data-tip-pos="top" style="cursor:help" x-text="m.real ? (m.route||'실호출') : 'mock'"></span></td></template></tr>
                    <tr><td class="text-ink">등급 일치율 <span class="text-xs text-muted">(신뢰구간)</span></td><template x-for="(m,mi) in cmpCols" x-bind:key="'cg'+mi"><td>
                      <span class="abbar" x-bind:class="mi ? 'abbar--b' : ''">
                        <span class="abbar__track"><span class="abbar__fill" x-bind:style="'width:' + Math.max((m.grade_accuracy||0)*100, 3) + '%'"></span></span>
                        <b class="tnum" x-text="pctTxt(m.grade_accuracy)"></b><span class="abwin" x-show="cmpWin('grade_accuracy', mi)">▲</span>
                      </span>
                      <div class="text-xs text-muted tnum" style="margin-top:3px" x-text="'(' + ciOf(m.grade_accuracy, m.n) + ')'"></div></td></template></tr>
                    <tr><td class="text-ink">사유 일치</td><template x-for="(m,mi) in cmpCols" x-bind:key="'cj'+mi"><td><b class="tnum" x-text="pctTxt(m.reason_jaccard)"></b> <span class="abwin" x-show="cmpWin('reason_jaccard', mi)">▲</span></td></template></tr>
                    <tr><td class="text-ink">빈 결과</td><template x-for="(m,mi) in cmpCols" x-bind:key="'ce'+mi"><td class="tnum" x-text="pctTxt(m.empty_rate)"></td></template></tr>
                    <tr><td class="text-ink">비용($)</td><template x-for="(m,mi) in cmpCols" x-bind:key="'cc'+mi"><td class="tnum" x-text="m.cost_usd!=null ? ('$'+(Math.round(m.cost_usd*10000)/10000)) : '·'"></td></template></tr>
                  </tbody></table></div>
                  <div class="text-xs text-muted" style="margin-top:8px" x-show="(cmpResult.skipped||[]).length">비교 제외: <span x-text="(cmpResult.skipped||[]).map(s => s.model + ' (' + s.reason + ')').join(' · ')"></span></div>
                  <div class="text-xs text-muted" style="margin-top:4px">신뢰구간이 겹치면 우열 판단 보류 · 정답셋이 쌓일수록 오차가 줄어듭니다</div>
                </div>
              </template>
            </div>
          </section>
        </div>

      <!-- ═══ 모듈: 테스트셋 관리(관리자) · 탭 바 + 정답셋 목록 + 학습 데이터 + 분석 ═══ -->
      <div x-show="mod === 'testset'" x-cloak class="w-full" style="margin-bottom:10px"><div class="evaltabs">
        <button type="button" x-bind:class="testTab==='status'?'sel':''" x-on:click="testTab='status'; loadGoldenStatus(); loadLearnReport()">현황 · 학습 반영</button>
        <button type="button" x-bind:class="testTab==='golden'?'sel':''" x-on:click="testTab='golden'; loadGoldenList()">정답셋 목록</button>
        <button type="button" x-bind:class="testTab==='data'?'sel':''" x-on:click="testTab='data'; loadLearnData()">학습 데이터</button>
      </div></div>
      <!-- 테스트셋 관리 · 현황 탭: 정답 축적 현황 + 학습 반영(지금 실행 = 관리자) -->
      <div x-show="mod === 'testset' && testTab === 'status'" x-cloak class="w-full space-y-4">
          <!-- 골든 생성 현황: 누적·최근 배치·분류 필요 -->
          <section class="panel" data-fn x-init="loadGoldenStatus()"><div class="panel-hd"><b>테스트셋 현황</b><span class="meta">검수에서 '정확' 합의가 정답으로 쌓입니다 · 현재 프롬프트 <span class="tnum" x-text="verTxt"></span></span>
            <button type="button" class="ds-iconbtn ds-iconbtn--bordered ml-auto" x-on:click="loadGoldenStatus()" data-tip="새로고침" data-tip-pos="bottom" aria-label="골든 현황 새로고침"><svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M20 11a8 8 0 1 0-.9 4.5M20 5v6h-6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
          </div>
            <div class="panel-bd">
              <template x-if="goldenStatus">
                <div>
                  <div class="tiles" style="grid-template-columns:repeat(5,1fr);margin-bottom:12px">
                    <div class="tile"><div class="n tnum" x-text="goldenStatus.total"></div><div class="t">정답 누적</div></div>
                    <div class="tile"><div class="n tnum" x-text="(goldenStatus.source_counts&&goldenStatus.source_counts.review)||0"></div><div class="t">검수로 확정</div></div>
                    <div class="tile"><div class="n tnum" x-text="(goldenStatus.source_counts&&goldenStatus.source_counts.manual)||0"></div><div class="t">직접 등록</div></div>
                    <div class="tile"><div class="n tnum" x-text="(goldenStatus.last_batch&&goldenStatus.last_batch.new)||0"></div><div class="t">신규 승격</div></div>
                    <div class="tile"><div class="n tnum" x-text="(goldenStatus.last_batch&&goldenStatus.last_batch.need_category)||0"></div><div class="t">분류 필요</div></div>
                  </div>
                  <div x-show="(goldenStatus.need_list||[]).length">
                    <div class="text-xs text-muted" style="margin-bottom:6px">분류 필요 · 아래 콘텐츠의 카테고리를 채우면 다음 학습 반영 때 정답으로 승격됩니다(+5pt·미션)</div>
                    <div class="overflow-auto" style="max-height:180px"><table class="ds-table"><thead><tr><th>콘텐츠</th><th style="width:120px">서비스</th></tr></thead><tbody>
                      <template x-for="ng in goldenStatus.need_list" x-bind:key="ng.hash">
                        <tr><td x-text="ng.title || '(제목 없음)'"></td><td class="text-muted" x-text="ng.service"></td></tr>
                      </template>
                    </tbody></table></div>
                    <div class="text-xs text-muted" style="margin-top:6px"><b>콘텐츠 검수 · 결과 목록</b>에서 해당 콘텐츠 상세를 열어 분류를 골라 주세요 <button type="button" class="copybtn" x-on:click="selectMod('dash')">결과 목록 열기 →</button></div>
                  </div>
                </div>
              </template>
              <div x-show="!goldenStatus" class="text-xs text-muted">검수가 쌓이고 학습 반영이 돌면 현황이 표시됩니다(아래 ⚙ 지금 실행으로 바로 반영 가능)</div>
            </div>
          </section>
          <section class="panel" data-fn x-init="loadLearnReport()"><div class="panel-hd"><b>학습 반영</b><span class="meta">검수 의견을 모아 매일 04:00 한 번에 반영</span>
            <button type="button" class="ds-btn ds-btn--primary ml-auto" style="height:30px;padding:0 12px" x-bind:disabled="learnBusy" x-on:click="runLearnBatch()" x-text="learnBusy ? '실행 중…' : '⚙ 지금 실행'"></button>
          </div>
            <div class="panel-bd">
              <ul class="ds-bullets" style="margin-bottom:12px">
                <li>의견을 모아 <b>하루 1회</b> 반영해 결과가 흔들리지 않게 합니다 · 반영 후 새 버전 자동 재실행은 설정(auto_rerun_after_batch)으로 켤 수 있습니다.</li>
                <li>'정확' 합의는 <b>정답셋</b>으로 쌓이고, 바뀐 프롬프트는 정답셋으로 다시 평가합니다.</li>
              </ul>
              <!-- 일배치 결과 요약(상세 수치는 위 '골든셋 생성 현황' · 정합성·모델 비교는 '골든셋 평가' 탭) -->
              <template x-if="learnReport && learnReport.ts">
                <div class="text-xs text-muted" style="margin-bottom:12px">최근 반영: 정답 확정 <b class="text-ink tnum" x-text="(learnReport.golden&&learnReport.golden.confirmed)||0"></b>
                  · 신규 <b class="text-ink tnum" x-text="'+' + ((learnReport.golden&&learnReport.golden.new)||0)"></b>
                  · 분류 필요 <b class="text-ink tnum" x-text="(learnReport.golden&&learnReport.golden.need_category)||0"></b>
                  · 의견 갈림 <b class="text-ink tnum" x-text="(learnReport.golden&&learnReport.golden.disagree)||0"></b>
                  <span x-show="learnReport.golden && learnReport.golden.demoted"> · 정답 제외 <b class="text-ink tnum" x-text="learnReport.golden.demoted"></b></span>
                  · 정답 일치율 <b class="text-ink tnum" x-text="pctTxt(learnReport.grade_accuracy)"></b>
                  <span x-show="learnReport.eval && learnReport.eval.grade_ci" class="tnum" x-text="learnReport.eval && learnReport.eval.grade_ci ? (' (신뢰구간 ' + pctTxt(learnReport.eval.grade_ci.lo) + '~' + pctTxt(learnReport.eval.grade_ci.hi) + ')') : ''"></span>
                </div>
              </template>
              <!-- 개선 지시(단계별) -->
              <template x-for="stage in ['extract','analyze','review','judge']" x-bind:key="stage">
                <div x-show="metaResults && metaResults[stage] && (metaResults[stage].directive || (metaResults[stage].ambiguities||[]).length)" class="metarow">
                  <span class="ds-badge ds-badge--neutral" x-text="({extract:'추출',analyze:'분석',review:'검수',judge:'판정'})[stage]"></span>
                  <div style="flex:1;min-width:0">
                    <div class="metarow__dir" x-text="metaResults&&metaResults[stage]?metaResults[stage].directive:''"></div>
                    <template x-for="a in (metaResults&&metaResults[stage]?metaResults[stage].ambiguities:[])" x-bind:key="a">
                      <div class="metarow__amb">⚠ 의견 갈림(가이드 명확화 필요): <span x-text="a"></span></div>
                    </template>
                  </div>
                </div>
              </template>
              <div x-show="!(learnReport && learnReport.ts) && !metaResults" class="text-xs text-muted"><b class="text-ink">⚙ 지금 실행</b>을 누르면 개선·골든 축적·회귀 평가·모델 비교를 한 번에 돌립니다</div>
            </div>
          </section>
      </div><!-- /테스트셋 관리 · 현황 -->


      <div x-show="mod === 'testset' && testTab === 'golden'" x-cloak class="w-full space-y-4">
        <section class="panel" x-show="backend !== 'supabase' || (adminData && adminData.isAdmin)" x-init="loadGoldenList()"><div class="panel-hd"><b>정답셋(골든) 목록</b>
          <span class="meta" x-text="goldenList ? (goldenList.total + '건 · 검수로 확정 ' + ((goldenList.source_counts&&goldenList.source_counts.review)||0) + ' · 직접 등록 ' + ((goldenList.source_counts&&goldenList.source_counts.manual)||0)) : ((adminData&&adminData.goldenCount?adminData.goldenCount+'건 등록됨':'미등록'))"></span>
          <button type="button" class="ds-iconbtn ds-iconbtn--bordered ml-auto" x-on:click="loadGoldenList()" data-tip="새로고침" data-tip-pos="bottom" aria-label="정답셋 목록 새로고침"><svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M20 11a8 8 0 1 0-.9 4.5M20 5v6h-6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
        </div>
          <div class="panel-bd">
            <ul class="ds-bullets" style="margin-bottom:10px"><li>정답은 <b>검수 '정확' 합의</b>가 학습 반영 때 누적 승격되고, 관리자가 .jsonl 로 보완 등록할 수 있습니다.</li><li>업로드 형식 <b>{content, expected:{finalGrade, reasons, content_category}}</b> · 등록 시 등급·카테고리를 사전 기준으로 검증합니다.</li><li>평가와 어긋나 <b>오류 의심</b> 표시된 항목은 확인 후 제거하세요(정답 오류는 모델 순위를 뒤집습니다).</li></ul>
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
              <label class="ds-btn ds-btn--secondary" style="cursor:pointer">정답셋 .jsonl 등록<input type="file" accept=".jsonl" class="sr-only" x-on:change="registerGolden($event)"></label>
              <label class="text-xs text-muted" style="display:flex;align-items:center;gap:5px;cursor:pointer"><input type="checkbox" x-model="goldenMerge"> 기존에 병합(해제 시 전체 교체)</label>
              <span class="text-xs text-muted" x-text="goldenMsg"></span>
            </div>
            <template x-if="goldenList && goldenList.items && goldenList.items.length">
              <div style="margin-top:12px">
              <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:8px">
                <span class="selctl__lbl" data-tip="정답이 확정될 당시의 초안 모델 · 정답 자체는 모델과 무관한 사람 확정값" data-tip-pos="top">유래 모델</span>
                <button type="button" class="srcfilter__chip" x-bind:class="goldenModel==='' ? 'sel' : ''" x-on:click="goldenModel=''">전체</button>
                <template x-for="m in goldenModelList" x-bind:key="'gm'+m"><button type="button" class="srcfilter__chip" x-bind:class="goldenModel===m ? 'sel' : ''" x-on:click="goldenModel=m" x-text="m || '(모델 미기록)'"></button></template>
              </div>
              <div class="overflow-auto" style="max-height:300px"><table class="ds-table"><thead><tr><th>콘텐츠</th><th style="width:56px">등급</th><th>카테고리</th><th style="width:150px">유래 모델</th><th style="width:56px">버전</th><th style="width:74px">출처</th><th style="width:60px"></th></tr></thead><tbody>
                <template x-for="g in filteredGolden" x-bind:key="g.hash">
                  <tr>
                    <td><span x-text="g.title || '(제목 없음)'"></span> <span class="ds-badge ds-badge--error" style="cursor:help" x-show="g.flagged && !g.fix_needed" data-tip="최근 평가에서 모델과 불일치 · 정답 오류 후보" data-tip-pos="top">오류 의심</span> <span class="ds-badge ds-badge--warning" style="cursor:help" x-show="g.fix_needed" data-tip="평가 판정에서 '채택' 합의 · 모델 결과가 맞다고 확정된 정답(제거 후 재등록 또는 검수 재확정 필요)" data-tip-pos="top">교정 필요</span></td>
                    <td><span class="ds-badge" style="cursor:help" x-bind:class="g.grade==='G' ? 'ds-badge--success' : 'ds-badge--neutral'" x-bind:data-tip="termDef('grade', g.grade)" data-tip-pos="top" x-text="g.grade || '·'"></span></td>
                    <td><template x-for="c in (g.category||[])" x-bind:key="c"><span class="ds-badge ds-badge--category" style="cursor:help;margin:1px" x-bind:data-tip="termDef('category', c)" data-tip-pos="top" x-text="c"></span></template></td>
                    <td class="text-muted" x-text="g.model || '·'"></td>
                    <td class="tnum" x-text="g.version ? ('v' + g.version) : '·'"></td>
                    <td><span class="ds-badge ds-badge--neutral" x-text="g.source === 'manual' ? '직접' : '검수'"></span></td>
                    <td><button type="button" class="copybtn" x-on:click="removeGolden(g.hash)">제거</button></td>
                  </tr>
                </template>
              </tbody></table></div>
              </div>
            </template>
          </div>
        </section>
      </div><!-- /정답셋 목록 -->
      <!-- 학습 데이터: 커버리지·일치도·신뢰도·오류 후보 + 데이터셋 추출 -->
      <div x-show="mod === 'testset' && testTab === 'data'" x-cloak class="w-full space-y-4">
        <section class="panel" data-fn x-show="backend !== 'supabase' || (adminData && adminData.isAdmin)" x-init="loadLearnData()">
            <div class="panel-hd"><b>학습 데이터 현황</b><span class="meta">특화 LLM 학습데이터 · 소요 산정(논문 기준)</span>
              <button type="button" class="ds-btn ds-btn--secondary ml-auto" style="height:30px;padding:0 12px" x-bind:disabled="learnDataBusy" x-on:click="loadLearnData()" x-text="learnDataBusy ? '집계 중…' : '새로고침'"></button>
            </div>
            <div class="panel-bd">
              <template x-if="learnData">
                <div>
                  <div class="tiles" style="grid-template-columns:repeat(5,1fr);margin-bottom:12px">
                    <div class="tile"><div class="n tnum" x-text="learnData.golden_n"></div><div class="t">골든(정답)</div></div>
                    <div class="tile"><div class="n tnum" x-text="learnData.covered + '/' + learnData.class_total"></div><div class="t">클래스 충족</div></div>
                    <div class="tile" data-tip="Krippendorff's alpha · 참고 지표(임계값 기계 적용 금지 · Artstein & Poesio 2008)" data-tip-pos="top"><div class="n tnum" x-text="learnData.alpha == null ? '·' : learnData.alpha"></div><div class="t">일치도 α</div></div>
                    <div class="tile" data-tip="최근 골든 평가에서 모델과 정답이 어긋난 건(기계 플래그 → 사람 확정 · Northcutt 2021)" data-tip-pos="top"><div class="n tnum" x-text="(learnData.label_flags||[]).length"></div><div class="t">오류 의심</div></div>
                    <div class="tile"><div class="n tnum" x-text="learnData.split_n"></div><div class="t">의견 불일치</div></div>
                  </div>
                  <div class="text-xs text-muted" style="margin-bottom:12px" x-show="learnData.acc_ci">골든 정합성 <b class="text-ink" x-text="ciTxt(learnData.acc_ci)"></b> · 신뢰구간이 겹치는 비교는 판정 보류(Miller 2024)</div>
                  <!-- 데이터셋 추출(JSONL) -->
                  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">
                    <button type="button" class="ds-btn ds-btn--secondary" style="height:32px" x-on:click="exportLearn('sft')">SFT 내보내기 <span class="tnum" x-text="'(' + learnData.extractable.sft + ')'"></span></button>
                    <button type="button" class="ds-btn ds-btn--secondary" style="height:32px" x-on:click="exportLearn('dpo')">선호쌍(DPO) 내보내기 <span class="tnum" x-text="'(' + learnData.extractable.dpo + ')'"></span></button>
                    <button type="button" class="ds-btn ds-btn--secondary" style="height:32px" x-on:click="exportLearn('rationale')">판단근거 내보내기 <span class="tnum" x-text="'(' + learnData.extractable.rationale + ')'"></span></button>
                    <button type="button" class="ds-btn ds-btn--primary" style="height:32px" x-on:click="exportSpec()" data-tip="현재 수치·기준치·권장 스펙을 한 문서로(파인튜닝 소요서)" data-tip-pos="top">소요서(.md) 생성</button>
                  </div>
                  <!-- 학습 소요 대비(기준치 = 논문 출처) -->
                  <div class="overflow-auto" style="margin-bottom:14px"><table class="ds-table"><thead><tr><th>용도</th><th>기준</th><th>보유</th><th>부족</th><th>근거</th></tr></thead><tbody>
                    <template x-for="r in learnData.requirements" x-bind:key="r.kind">
                      <tr><td class="text-ink" x-text="r.kind"></td><td class="tnum" x-text="r.target"></td><td class="tnum" x-text="r.have"></td>
                        <td class="tnum" x-bind:class="r.lack > 0 ? 'text-ink' : ''" x-text="r.lack"></td>
                        <td class="text-xs text-muted" x-text="r.basis"></td></tr>
                    </template>
                  </tbody></table></div>
                  <div class="grid grid-cols-2 gap-4">
                    <!-- 클래스(Tier1) 커버리지 · 목표 = 클래스당 8(SetFit) -->
                    <div><div class="text-xs text-muted" style="margin-bottom:6px">클래스 커버리지 · 목표 <b class="text-ink" x-text="learnData.per_class_target + '건/클래스'"></b> (SetFit)</div>
                      <div class="overflow-auto" style="max-height:260px"><table class="ds-table"><thead><tr><th>Tier1</th><th>보유</th><th>부족</th></tr></thead><tbody>
                        <template x-for="c in [...learnData.coverage].sort((a,b)=>b.lack-a.lack)" x-bind:key="c.cls">
                          <tr><td x-text="c.cls"></td><td class="tnum" x-text="c.have"></td><td class="tnum" x-bind:class="c.lack>0?'text-ink':''" x-text="c.lack"></td></tr>
                        </template>
                      </tbody></table></div></div>
                    <!-- 검수자 신뢰도 · 합의 일치율 + 골드 정확도 + Dawid-Skene EM -->
                    <div><div class="text-xs text-muted" style="margin-bottom:6px">검수자 신뢰도 · 합의 일치율 + 골드 정확도 + EM 오류율(Dawid-Skene 1979)</div>
                      <div class="overflow-auto" style="max-height:260px"><table class="ds-table"><thead><tr><th>검수자</th><th>검수</th><th>합의 일치</th><th>골드</th><th>EM 오류율</th></tr></thead><tbody>
                        <template x-for="r in learnData.reviewers" x-bind:key="r.reviewer">
                          <tr><td class="text-ink" x-text="r.reviewer"></td><td class="tnum" x-text="r.n"></td>
                            <td class="tnum" x-text="r.agree_rate == null ? '·' : pctTxt(r.agree_rate)"></td>
                            <td class="tnum" x-text="r.gold_n >= 5 ? (pctTxt(r.gold_acc) + ' (' + r.gold_n + ')') : ('· (' + (r.gold_n||0) + ')')"></td>
                            <td class="tnum" x-text="r.ds_error == null ? '·' : pctTxt(r.ds_error)"></td></tr>
                        </template>
                        <template x-if="!learnData.reviewers.length"><tr><td colspan="5" class="text-muted">검수 데이터가 쌓이면 표시됩니다</td></tr></template>
                      </tbody></table></div></div>
                  </div>
                  <!-- 라벨 오류 후보(기계 플래그 → 휴먼 확정: 유지 또는 제거) -->
                  <div x-show="(learnData.label_flags||[]).length" style="margin-top:14px">
                    <div class="text-xs text-muted" style="margin-bottom:6px">라벨 오류 후보 · 최근 골든 평가에서 모델·정답 불일치(확인 후 오답이면 제거)</div>
                    <div class="overflow-auto" style="max-height:200px"><table class="ds-table"><thead><tr><th>콘텐츠</th><th>정답</th><th>모델</th><th style="width:60px"></th></tr></thead><tbody>
                      <template x-for="f in learnData.label_flags" x-bind:key="f.hash">
                        <tr><td x-text="f.title || f.hash"></td><td class="tnum" x-text="f.expected"></td><td class="tnum" x-text="f.got"></td>
                          <td><button type="button" class="copybtn" x-on:click="removeGolden(f.hash)">제거</button></td></tr>
                      </template>
                    </tbody></table></div>
                  </div>
                  <ul class="ds-bullets" style="margin-top:14px">
                    <li>기준치 출처: 클래스당 8(SetFit 2022) · SFT 1k(LIMA 2023) · 운영급 13.5k(Llama Guard 2023) · 선호쌍 33k(InstructGPT 2022 참고 상한) · 평가셋 100(tinyBenchmarks 2024). 전체 서지는 <b>LEARNING_DESIGN.md</b>.</li>
                    <li>선호쌍(DPO)은 검수자 교정의 <b>전/후</b>가 원천입니다 · 상세 화면에서 교정할수록 쌓입니다.</li>
                  </ul>
                </div>
              </template>
              <div x-show="!learnData" class="text-xs text-muted">집계를 불러오는 중이거나, 관리자 권한이 필요합니다</div>
            </div>
        </section>
      </div><!-- /학습 데이터 -->

      <!-- 콘텐츠 검수 · 원본 목록: 결과 원본을 가공 없이 빠르게 -->
      <div x-show="mod === 'create' && createTab === 'raw'" x-cloak class="w-full space-y-4">
        <!-- 모델 선택(별도 영역 · selctl 정책): 아래 목록의 기준 모델 지정 -->
        <section class="panel" data-fn><div class="panel-hd"><b>모델 선택</b><span class="meta">아래 목록의 기준 모델 지정</span></div>
          <div class="panel-bd">
            <div class="filterbar" style="margin:0 16px">
              <span class="selctl abslot"><span class="selctl__tag">모델</span>
                <select class="field" x-model="rawModel">
                  <option value="">전체</option>
                  <template x-for="m in rawModels" x-bind:key="'rm'+m"><option x-bind:value="m" x-text="m"></option></template>
                </select>
              </span>
            </div>
            <ul class="ds-bullets" style="margin:10px 16px 0">
              <li>선택한 모델이 <b>초기 판정한 초안</b>만 목록에 표시됩니다(누적 · 모델별 정답셋의 재료).</li>
              <li>검수 합의는 <b>이 모델의 정답셋</b>으로 쌓입니다.</li>
              <li>버전 간 비교는 <b>결과 비교</b> 탭에서 합니다.</li>
            </ul>
          </div>
        </section>
        <section class="panel" data-fn><div class="panel-hd"><b>검수 대상 콘텐츠</b><span class="meta tnum" x-text="rawData ? (rawData.n + '건 · 최근순') : ''"></span>
          <span class="ml-auto" style="display:flex;gap:8px;align-items:center">
            <a class="copybtn" style="text-decoration:none" href="/export.csv" data-tip="전체 결과 CSV 다운로드" data-tip-pos="bottom"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>CSV</a>
            <a class="copybtn" style="text-decoration:none" href="/report" target="_blank" data-tip="브라우저용 HTML 리포트 열기" data-tip-pos="bottom"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3h7v7M21 3l-9 9M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>리포트</a>
            <button type="button" class="ds-iconbtn ds-iconbtn--bordered" x-on:click="loadRaw()" data-tip="새로고침" data-tip-pos="bottom" aria-label="원본 목록 새로고침"><svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M20 11a8 8 0 1 0-.9 4.5M20 5v6h-6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
          </span>
        </div>
          <div class="panel-bd">
            <ul class="ds-bullets" style="margin-bottom:11px"><li>행 클릭 = <b>JSON 원문</b> · <b>검수하기</b> = 상세에서 판정·교정.</li><li x-show="rawModel">현재 <b class="text-ink" x-text="rawModel"></b> 초안만 표시 중입니다.</li></ul>
            <!-- 필터: 검색 + 등급/서비스/검수 상태 -->
            <div class="filterbar">
              <input class="field" placeholder="제목·카테고리·사유 검색" x-model="rawQ">
              <select class="field" style="width:auto" x-model="rawGrade"><option value="">등급 전체</option><option value="G">G</option><option value="R">R</option></select>
              <select class="field" style="width:auto" x-model="rawSvc"><option value="">서비스 전체</option><template x-for="sv in rawSvcs" x-bind:key="sv"><option x-bind:value="sv" x-text="sv"></option></template></select>
              <select class="field" style="width:auto" x-model="rawRev"><option value="">검수 전체</option><option value="todo">미검수</option><option value="done">검수 완료</option></select>
              <span class="text-xs text-muted tnum" x-text="rawFiltered.length + ' / ' + ((rawData&&rawData.n)||0) + '건'"></span>
            </div>
            <div class="overflow-auto" style="max-height:420px"><table class="ds-table"><thead><tr><th style="width:52px">등급</th><th>콘텐츠</th><th style="width:100px">서비스</th><th>카테고리</th><th>사유</th><th style="width:130px">검수</th></tr></thead><tbody>
              <template x-for="r in rawFiltered" x-bind:key="r.hash">
                <tr style="cursor:pointer" role="button" tabindex="0" x-bind:class="rawSel && rawSel.hash === r.hash ? 'is-sel' : ''" x-on:click="rawSel = (rawSel && rawSel.hash === r.hash) ? null : r" x-on:keydown.enter="rawSel = r">
                  <td><span class="ds-badge" style="cursor:help" x-bind:class="r.grade==='G' ? 'ds-badge--success' : 'ds-badge--neutral'" x-bind:data-tip="termDef('grade', r.grade)" data-tip-pos="right" x-text="r.grade||'·'"></span></td>
                  <td class="text-ink" data-tip="JSON 원문 보기" data-tip-pos="top"><span x-text="r.title || '(제목 없음)'"></span>
                    <span class="ds-badge ds-badge--yellow" style="cursor:help;margin-left:4px" x-show="r.review==='yellow'" data-tip="AI 확신이 낮아 사람 확인이 필요한 콘텐츠" data-tip-pos="top">YELLOW</span>
                    <span class="ds-badge ds-badge--error" style="margin-left:4px" x-show="r.split" data-tip="검수자 의견이 갈림 · 추가 의견 필요" data-tip-pos="top">불일치</span>
                  </td>
                  <td class="text-muted" x-text="r.service"></td>
                  <td><template x-for="c in (r.category||[])" x-bind:key="c"><span class="ds-badge ds-badge--category" style="cursor:help;margin:1px" x-bind:data-tip="termDef('category', c)" data-tip-pos="top" x-text="c"></span></template><span x-show="!(r.category||[]).length" class="text-xs text-muted">·</span></td>
                  <td><template x-for="c in (r.reasons||[])" x-bind:key="c"><span class="ds-badge ds-badge--reason" style="cursor:help;margin:1px" x-bind:data-tip="termDef('reason', c)" data-tip-pos="top" x-text="c"></span></template><span x-show="!(r.reasons||[]).length" class="text-xs text-muted">·</span></td>
                  <td x-on:click.stop>
                    <button type="button" class="ds-btn ds-btn--primary ds-btn--s-sm" x-show="!(r.fb && r.fb.verdict)" x-on:click="openRawDetail(r)">검수하기</button>
                    <span class="text-xs text-muted tnum" x-show="r.fb && r.fb.verdict" style="cursor:pointer" x-on:click="openRawDetail(r)" data-tip="완료 · 클릭하면 상세에서 수정" data-tip-pos="top" x-text="'✓ ' + (r.fb && r.fb.ts ? fmtTs(r.fb.ts) : '완료')"></span>
                  </td>
                </tr>
              </template>
            </tbody></table>
            <div x-show="!(rawData && rawData.items && rawData.items.length)" class="text-xs text-muted" style="padding:10px">데이터가 없습니다 · <b class="text-ink">콘텐츠 관리</b>에서 콘텐츠를 추가하세요</div>
            </div>
            <div x-show="rawSel" style="margin-top:10px">
              <div class="text-xs text-muted" style="margin-bottom:6px">JSON 원문 · <b class="text-ink" x-text="rawSel ? (rawSel.title || rawSel.hash) : ''"></b></div>
              <pre class="tbox" style="white-space:pre-wrap;font-size:11px;max-height:280px;overflow:auto" x-text="rawSel ? JSON.stringify({item_meta: rawSel.item_meta, quality_meta: rawSel.quality_meta}, null, 2) : ''"></pre>
            </div>
          </div>
        </section>
      </div><!-- /콘텐츠 검수 · 원본 목록 -->

      <!-- ═══ 모듈: 팀 관리 (멀티테넌시) ═══ -->
      <div x-show="mod === 'admin'" x-cloak class="w-full space-y-4">
        <section class="panel" data-fn><div class="panel-hd"><b>새 팀 만들기</b><span class="ds-badge ds-badge--neutral">관리자</span></div>
          <div class="panel-bd">
            <ul class="ds-bullets" style="margin-bottom:11px">
              <li>새 팀을 만들면 <b>초대 코드</b>가 발급됩니다.</li>
              <li>팀원은 가입 시 이 코드로 참가합니다(팀 생성은 관리자만).</li>
            </ul>
            <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
              <input class="field" style="flex:1;min-width:200px;height:38px" placeholder="팀 이름 (예: 콘텐츠검수팀)" x-model="newTeamName" x-on:keydown.enter="createTeam()">
              <button type="button" class="ds-btn ds-btn--primary ds-btn--s-md" x-on:click="createTeam()" x-bind:disabled="!(newTeamName||'').trim()">만들기</button>
              <span class="text-xs" style="color:var(--ds-success)" aria-live="polite" x-text="teamMsg"></span>
            </div>
          </div>
        </section>
        <section class="panel"><div class="panel-hd"><b>팀 정보</b><span class="meta" x-text="adminData&&adminData.team ? adminData.team.name : ''"></span></div>
          <div class="panel-bd">
            <div class="invite">
              <div><div class="text-xs text-muted" style="margin-bottom:4px">초대 코드 · 팀원에게 공유하면 같은 팀으로 참가합니다</div>
                <div class="invite__code" x-text="adminData&&adminData.team ? adminData.team.invite_code : '·'"></div></div>
              <button type="button" class="ds-btn ds-btn--secondary" x-on:click="copyInvite()" x-text="inviteCopied ? '복사됨 ✓' : '복사'"></button>
            </div>
          </div>
        </section>
        <section class="panel"><div class="panel-hd"><b>멤버</b><span class="meta" x-text="(adminData&&adminData.members?adminData.members.length:0)+'명'"></span></div>
          <div class="panel-bd">
            <template x-for="m in (adminData?adminData.members:[])" x-bind:key="m.id">
              <div class="lb-row">
                <span class="lb-av" data-tier="0"><img x-bind:src="charImg(m.avatar)" alt=""></span>
                <span class="lb-name"><span x-text="m.name"></span>
                  <span x-show="adminData.team && m.id===adminData.team.created_by" class="ds-badge ds-badge--status" style="margin-left:6px">생성자</span>
                  <span x-show="m.is_admin && !(adminData.team && m.id===adminData.team.created_by)" class="ds-badge ds-badge--intent" style="margin-left:6px">관리자</span>
                </span>
                <template x-if="adminData&&adminData.isAdmin && adminData.team && m.id!==adminData.team.created_by">
                  <span style="display:flex;gap:6px">
                    <button type="button" class="ds-btn ds-btn--outline ds-btn--c-neutral ds-btn--s-sm" x-on:click="adminAct(m.is_admin ? 'unset_admin' : 'set_admin', m.id)" x-text="m.is_admin ? '관리자 해제' : '관리자 지정'"></button>
                    <button type="button" class="ds-btn ds-btn--outline ds-btn--c-danger ds-btn--s-sm" x-on:click="adminAct('remove_member', m.id)">제거</button>
                  </span>
                </template>
              </div>
            </template>
            <div x-show="!(adminData&&adminData.members&&adminData.members.length)" class="text-xs text-muted" style="padding:8px">멤버가 없습니다</div>
            <p class="text-xs text-muted" style="padding:8px 8px 0;line-height:1.5">🎖 <b class="text-ink">검수 마스터</b>(레벨 10) 멤버에게 관리자 권한을 위임해 골든셋·정책 관리를 함께 맡길 수 있습니다.</p>
          </div>
        </section>
        <div x-show="adminData && !adminData.isAdmin" class="text-xs text-muted" style="padding:4px">멤버 관리는 팀 관리자(생성자·위임)만 가능합니다.</div>
      </div>

      <!-- ═══ 모듈: 시스템 설정(운영 관리자) · 데이터 관리(상단) + API 키·모델(하단) ═══ -->
      <div x-show="mod === 'system'" x-cloak class="w-full space-y-4">
        <section class="panel"><div class="panel-hd"><b>데이터 관리</b><span class="meta">삭제는 되돌릴 수 없습니다 · 우리 팀 데이터만 영향</span><span class="ds-badge ds-badge--neutral ml-auto">운영 관리자</span></div>
          <div class="panel-bd">
            <div style="display:flex;gap:10px;flex-wrap:wrap">
              <button type="button" class="ds-btn ds-btn--secondary" x-on:click="adminAct('clear_feedback')">평가 피드백 전체 삭제</button>
              <button type="button" class="ds-btn ds-btn--secondary" x-on:click="adminAct('clear_contents')">검토 콘텐츠 전체 삭제</button>
              <button type="button" class="ds-btn ds-btn--secondary" x-on:click="adminAct('clear_golden')">정답셋 전체 삭제</button>
              <button type="button" x-show="backend !== 'supabase'" class="ds-btn ds-btn--outline ds-btn--c-danger ds-btn--s-sm" style="height:34px" x-on:click="clearStore()">로컬 적재 데이터 초기화 <span class="tnum" x-text="'(' + (cfg.storedCount || 0) + '건)'"></span></button>
            </div>
            <div x-show="backend === 'supabase' && adminData && adminData.team" style="margin-top:14px;padding-top:14px;border-top:1px solid var(--ds-hairline-soft,rgba(0,0,0,.06))">
              <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
                <div style="flex:1;min-width:220px">
                  <b class="text-ink" style="font-size:12.5px">팀 삭제</b>
                  <div class="text-xs text-muted" style="margin-top:3px">팀과 멤버 소속이 해제됩니다 · 콘텐츠·피드백 등 팀 데이터는 위 버튼으로 먼저 삭제하세요.</div>
                </div>
                <button type="button" class="ds-btn ds-btn--outline ds-btn--c-danger" x-on:click="adminAct('delete_team')">팀 삭제</button>
              </div>
            </div>
          </div>
        </section>
        <section class="panel" data-fn><div class="panel-hd"><b>API 키</b><span class="meta">추출 호출 키 · 팀원은 입력 없이 사용</span></div>
          <div class="panel-bd">
        <!-- 운영(공유 서버): 키는 서버에서 관리 → 팀원은 입력 불필요 -->
        <!-- 팀원(비관리자): 키는 서버(관리자) 관리 · 입력 불필요. 관리자는 아래 입력으로 설정 -->
        <div x-show="cfg.keyManagedByServer && !(adminData && adminData.isAdmin)" class="keymanaged">
          <b class="text-ink">🔒 API 키는 서버에서 관리됩니다</b>
          <p>공유 서버 모드입니다. 추출 키는 <b>관리자가</b> 설정하고, 팀원은 따로 키를 넣지 않아도 바로 사용합니다.
            <span x-text="cfg.hasKey ? '· 현재 연결됨 ✓' : '· 서버에 키 미설정(관리자 확인 필요)'"></span></p>
        </div>
        <div x-show="!cfg.keyManagedByServer || (adminData && adminData.isAdmin)">
        <!-- 통합 라우터 카드 -->
        <div class="routercard">
          <div class="rc-h"><b>통합 라우터</b><span class="rc-badge">권장</span></div>
          <p class="rc-d">한 키로 여러 모델(OpenAI · Anthropic · Google · Solar 등)을 호출합니다</p>
          <template x-for="s in ['bizrouter', 'timely']" x-bind:key="s">
            <div class="krow">
              <div class="krow-top">
                <span class="krow-nm" x-text="keyDefs[s].label.replace(' 키', '')"></span>
                <span class="krow-st"><span class="sdot" x-bind:class="keyState(s) ? 'ok' : 'off'"></span><span x-text="keyState(s) ? '연결됨' : '미연결'"></span></span>
              </div>
              <div class="keyin">
                <input x-bind:type="keyShow[s] ? 'text' : 'password'" x-model="keyInputs[s]" x-bind:placeholder="keyDefs[s].ph" class="field" autocomplete="off">
                <button type="button" class="eye" x-on:click="keyShow[s] = !keyShow[s]" aria-label="키 보기">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
                </button>
              </div>
              <div class="mt-2 flex flex-wrap items-center gap-2">
                <button type="button" x-on:click="saveKey(s)" x-bind:disabled="cfgBusy"
                  class="ds-btn ds-btn--primary ds-btn--s-sm disabled:opacity-50" x-text="keyState(s) ? '변경' : '저장'"></button>
                <button type="button" x-show="keyPersisted(s)" x-on:click="forgetKey(s)" class="ds-btn ds-btn--outline ds-btn--c-danger ds-btn--s-sm">삭제</button>
                <span class="text-xs text-muted" aria-live="polite" x-text="keyMsgs[s]"></span>
              </div>
            </div>
          </template>
        </div>

        <!-- 직접 호출(Solar) · 통합 라우터처럼 카드로 묶음 -->
        <div class="routercard" style="margin-top:14px">
          <div class="rc-h"><b>직접 호출</b></div>
          <ul class="ds-bullets"><li>각 회사 키로 직접 호출합니다.</li><li>통합 라우터와 함께 등록해도 됩니다.</li></ul>
          <div class="krow">
            <div class="krow-top">
              <span class="krow-nm">Upstage Solar</span>
              <span class="krow-st"><span class="sdot" x-bind:class="cfg.hasKey ? 'ok' : 'off'"></span><span x-text="cfg.hasKey ? '연결됨' : '미연결'"></span></span>
            </div>
            <div class="keyin">
              <input x-bind:type="keyShow.solar ? 'text' : 'password'" x-model="keyInputs.solar" x-bind:placeholder="keyDefs.solar.ph" class="field" autocomplete="off">
              <button type="button" class="eye" x-on:click="keyShow.solar = !keyShow.solar" aria-label="키 보기">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
              </button>
            </div>
            <div class="mt-2 flex flex-wrap items-center gap-2">
              <button type="button" x-on:click="saveKey('solar')" x-bind:disabled="cfgBusy"
                class="ds-btn ds-btn--primary ds-btn--s-sm disabled:opacity-50" x-text="cfg.hasKey ? '변경' : '저장'"></button>
              <button type="button" x-show="cfg.hasKey" x-on:click="testConn()" x-bind:disabled="cfgBusy"
                class="ds-btn ds-btn--secondary ds-btn--s-sm disabled:opacity-50">연결 테스트</button>
              <button type="button" x-show="cfg.persisted" x-on:click="forgetKey('solar')" class="ds-btn ds-btn--outline ds-btn--c-danger ds-btn--s-sm">삭제</button>
              <span class="text-xs text-muted" aria-live="polite" x-text="keyMsgs.solar"></span>
            </div>
          </div>
        </div>

        <label class="mt-4 flex cursor-pointer items-center gap-2 text-[13px] text-body">
          <input type="checkbox" x-model="cfgPersist" class="h-4 w-4 rounded border-black/15 bg-canvas text-violet">
          이 기기에 저장 (재시작 후에도 유지)
        </label>
        <p class="mt-3 text-xs text-muted">키 저장 시 연결을 확인합니다 [모델] 탭에서는 연결된 제공자의 모델만 선택 가능합니다</p>
        </div>
      </div>
          </div>
        </section>
        <section class="panel" data-fn><div class="panel-hd"><b>모델 · 추론 강도</b><span class="meta">텍스트·이미지 모델과 추론 깊이</span></div>
          <div class="panel-bd">
        <div class="cfgsec">
          <label class="lbl">텍스트 모델 <span class="font-normal normal-case tracking-normal text-muted"> 리드문·메타</span></label>
          <select class="field" x-bind:value="textValue" x-on:change="onTextPick($event.target.value)">
            <template x-for="g in textGroups" x-bind:key="g.label">
              <optgroup x-bind:label="g.label + (g.on ? '' : ' (미연결)')">
                <template x-for="it in g.items" x-bind:key="it.model">
                  <option x-bind:value="optVal(it.provider, it.model)" x-bind:disabled="!g.on" x-text="it.model || it.label"></option>
                </template>
              </optgroup>
            </template>
          </select>
          <button type="button" x-show="cfg.hasKey" x-on:click="loadModels()" x-bind:disabled="cfgBusy"
            class="ds-btn ds-btn--secondary ds-btn--s-sm w-full mt-2 disabled:opacity-50">Solar 모델 새로고침</button>
          <span class="mt-1.5 block text-xs text-muted" x-text="modelsMsg"></span>
        </div>

        <div class="cfgsec">
          <label class="lbl">이미지 모델 <span class="font-normal normal-case tracking-normal text-muted"> 이미지 이해</span></label>
          <select class="field" x-bind:value="visionValue" x-on:change="onVisionPick($event.target.value)">
            <template x-for="g in visionGroups" x-bind:key="g.label">
              <optgroup x-bind:label="g.label + (g.on ? '' : ' (미연결)')">
                <template x-for="it in g.items" x-bind:key="it.model || it.label">
                  <option x-bind:value="optVal(it.provider, it.model)" x-bind:disabled="!g.on" x-text="it.label || it.model"></option>
                </template>
              </optgroup>
            </template>
          </select>
          <p class="mt-1.5 text-xs text-muted">순수 사진은 멀티모달 모델 권장(Upstage는 텍스트형 이미지에 적합)</p>
          <span class="mt-1 block text-xs text-muted" aria-live="polite" x-text="slotMsg"></span>
        </div>

        <div class="cfgsec">
          <label class="lbl">추론 강도 (Reasoning Effort)</label>
          <div class="seg">
            <template x-for="o in reasoningOpts" x-bind:key="o.id">
              <button type="button" x-on:click="setReasoning(o.id)"
                x-bind:class="reasoning === o.id ? 'on' : ''" x-text="o.label"></button>
            </template>
          </div>
          <p class="mt-1.5 text-xs text-muted">높일수록 추론 깊이는 늘고 속도는 느려집니다</p>
        </div>

        <div class="cfgsec">
          <p class="text-xs text-muted">단계별 추출 프롬프트(추출·분석·검수·판정)는 <b class="text-ink">프롬프트 스튜디오</b>에서 관리합니다</p>
        </div>
          </div>
        </section>
      </div>

      <!-- ═══ 모듈: 평가 아레나 (게임화) · 팀 정확도 협동 스코어 + 리더보드 ═══ -->
      <div x-show="mod === 'home' || mod === 'arena'" x-cloak class="w-full space-y-4" style="order:-1">
        <!-- 히어로: 팀 정확도 게이지(협동) -->
        <section class="arena-hero">
          <div class="arena-hero__head">
            <div><div class="arena-hero__eyebrow">내 검수 진척율 · 함께 끝까지</div>
              <div class="arena-hero__big"><span x-text="myProgressPct"></span><span class="arena-hero__pct">%</span></div>
            </div>
            <div class="arena-hero__target">팀 평균 <b x-text="teamProgressPct + '%'"></b></div>
          </div>
          <!-- 게이지: 내 진척 채움 + 팀 평균 마커 -->
          <div class="arena-gauge">
            <div class="arena-gauge__fill" x-bind:style="'width:' + myProgressPct + '%'"></div>
            <div class="arena-gauge__target" x-bind:style="'left:' + teamProgressPct + '%'" title="팀 평균"></div>
          </div>
          <div class="arena-hero__foot">
            <span>검수 대상 <b class="text-ink" x-text="reviewTargets"></b>건 중 내가 <b class="text-ink" x-text="(arenaMe?arenaMe.reviews:0)"></b>건 검수</span>
            <span class="arena-quest" x-show="arenaData && arenaData.queue" x-on:click="selectMod('review')">
              🎯 남은 <b x-text="(arenaData?arenaData.queue:0)"></b>건 검수하러 가기 →</span>
            <span class="arena-quest arena-quest--done" x-show="arenaData && !arenaData.queue">✓ 검수 대기 없음 · 깔끔!</span>
          </div>
        </section>

        <!-- 주간 리그(D-9): 이번 주 점수 승급/강등 -->
        <section class="panel" data-fn x-show="arenaData && arenaData.leaderboard && arenaData.leaderboard.length"><div class="panel-hd"><b>주간 리그 · 승급/강등</b>
          <span class="meta">최근 7일 · <b class="text-ink" x-text="leagueActive()"></b>명 활동</span></div>
          <div class="panel-bd">
            <ul class="ds-bullets" style="margin-bottom:10px"><li>이번 주 획득 점수로 매기는 순위입니다.</li><li>상위 <b>승급권</b>은 지위 보상, 하위 <b>강등권</b>은 분발 신호입니다(지난주 대비 이동 표시).</li></ul>
            <template x-for="r in weeklyLeague()" x-bind:key="r.reviewer">
              <div class="lb-row lb-row--league" x-show="r.wp>0 || leagueActive()===0" x-bind:class="r.reviewer===reviewer ? 'lb-row--me' : ''">
                <span class="lb-rank" x-text="rankMedal(r.rank-1)"></span>
                <span class="lb-av" x-bind:data-tier="levelTier(r.level)"><img x-bind:src="charImg(r.char)" alt=""></span>
                <span class="lb-name"><span x-text="r.reviewer + (r.reviewer===reviewer ? ' (나)' : '')"></span>
                  <small class="lb-title" x-text="leagueZoneKr(r.zone)"></small></span>
                <span class="ds-badge lb-zone" x-bind:class="leagueZoneClass(r.zone)" x-text="leagueZoneLabel(r.zone)"></span>
                <span class="lb-streak lb-delta" x-bind:class="r.delta>=0?'':'down'" x-text="r.delta ? ((r.delta>=0?'▲ +':'▼ ')+Math.abs(r.delta)) : '·'"></span>
                <span class="lb-pts tnum" x-text="r.wp + 'pt'"></span>
              </div>
            </template>
            <div x-show="leagueActive()===0" class="text-xs text-muted" style="padding:12px">이번 주 검수 활동이 아직 없습니다 · <b class="text-ink">검수 대기</b>에서 점수를 쌓아 승급권에 드세요</div>
          </div>
        </section>
        <div class="arena-cols">
          <!-- 리더보드 -->
          <section class="panel"><div class="panel-hd"><b>검수 리더보드</b><span class="meta" x-text="(arenaData&&arenaData.leaderboard?arenaData.leaderboard.length:0) + '명'"></span></div>
            <div class="panel-bd">
              <template x-for="(r, i) in (arenaData?arenaData.leaderboard:[])" x-bind:key="r.reviewer">
                <div class="lb-row" x-bind:class="r.reviewer===reviewer ? 'lb-row--me' : ''">
                  <span class="lb-rank" x-text="rankMedal(i)"></span>
                  <span class="lb-av" x-bind:data-tier="levelTier(r.level)"><img x-bind:src="charImg(r.char)" alt=""></span>
                  <span class="lb-name"><span x-text="r.reviewer + (r.reviewer===reviewer ? ' (나)' : '')"></span>
                    <small class="lb-title" x-text="levelEmoji(r.level)+' '+levelTitle(r.level)"></small></span>
                  <span class="lb-streak" x-show="r.streak>0" x-text="'🔥' + r.streak"></span>
                  <span class="ds-badge ds-badge--neutral" x-text="'Lv.' + r.level"></span>
                  <span class="lb-pts tnum" x-text="r.points + 'pt'"></span>
                </div>
              </template>
              <div x-show="!(arenaData&&arenaData.leaderboard&&arenaData.leaderboard.length)" class="text-xs text-muted" style="padding:12px">아직 검수 기록이 없습니다 · <b class="text-ink">검수 대기</b>에서 첫 검수를 해보세요</div>
            </div>
          </section>
          <!-- 내 검수 캐릭터 (육성) · 상단 히어로 -->
          <section class="panel arena-charpanel"><div class="panel-hd"><b>내 검수 캐릭터</b><span class="meta" x-text="reviewer ? reviewer : '이름 미설정'"></span></div>
            <div class="panel-bd">
              <div x-show="!reviewer" class="text-xs text-muted" style="padding:8px">로그인하면 나만의 <b class="text-ink">검수 캐릭터</b>가 생깁니다</div>
              <template x-if="reviewer && arenaMe">
                <div class="charcard charcard--split" x-bind:data-tier="levelTier(arenaMe.level)">
                  <!-- 좌: 선수 카드(캐릭터·레벨·스탯·검수하기) -->
                  <div class="charcard__player">
                    <div class="charcard__avatar">
                      <span class="charcard__glow"></span>
                      <img x-bind:src="charImg(arenaMe.char || reviewerChar)" alt="검수 캐릭터">
                      <span class="charcard__lvl" x-text="'Lv.' + arenaMe.level"></span>
                    </div>
                    <div class="charcard__info">
                      <div class="charcard__title"><span x-text="levelEmoji(arenaMe.level)"></span> <span x-text="levelTitle(arenaMe.level)"></span> <span class="charcard__flow" x-text="flowStageKr(arenaMe.level)"></span></div>
                      <div class="charcard__xpwrap">
                        <div class="charcard__xpbar"><div class="charcard__xpfill" x-bind:style="'width:' + xpPct(arenaMe) + '%'"></div></div>
                        <div class="charcard__xptxt">다음 레벨까지 <b x-text="xpToNext(arenaMe) + 'pt'"></b> · 순위 #<span x-text="arenaMyRank"></span></div>
                      </div>
                      <div class="charcard__stats">
                        <div><b class="tnum" x-text="arenaMe.reviews"></b><span>검수</span></div>
                        <div><b class="tnum" x-text="arenaMe.corrections"></b><span>개선 🏅</span></div>
                        <div><b class="tnum" x-text="(arenaMe.streak||0)+'일'"></b><span>🔥 스트릭</span></div>
                        <div data-tip="골드 문항(정답 알려진 검증 문항) 정확도 · 점수 배율에 반영" data-tip-pos="top"><b class="tnum" x-text="(arenaMe.gold_n||0) >= 5 ? pctTxt(arenaMe.gold_acc) : '·'"></b><span>🥇 골드</span></div>
                      </div>
                      <!-- 오늘의 미션: 서버 판정·보상(달성 시 보너스 1회 지급) -->
                      <template x-if="missionList.length">
                        <div class="charcard__missions">
                          <template x-for="ms in missionList" x-bind:key="ms.id">
                            <div class="charcard__mission" x-bind:class="ms.completed ? 'is-done' : ''" x-on:click="selectMod('review')">
                              <span class="charcard__mission-ic" x-text="ms.completed ? '✅' : '🎯'"></span>
                              <span class="charcard__mission-tx" x-text="ms.label + ' · ' + ms.done + '/' + ms.total"></span>
                              <span class="charcard__mission-cta" x-text="ms.completed ? ('+' + ms.bonus + 'pt') : '도전 →'"></span>
                            </div>
                          </template>
                        </div>
                      </template>
                      <template x-if="!missionList.length">
                        <div class="charcard__mission" x-on:click="selectMod(todayMission.to)">
                          <span class="charcard__mission-ic">🎯</span>
                          <span class="charcard__mission-tx" x-text="todayMission.txt"></span>
                          <span class="charcard__mission-cta" x-text="todayMission.cta + ' →'"></span>
                        </div>
                      </template>
                    </div>
                  </div>
                  <!-- 우: 모은 배지 컬렉션 -->
                  <div class="charcard__collection">
                    <div class="charcard__badges-hd">배지 컬렉션 <b x-text="badgeGot + ' / ' + badges().length"></b>
                      <button type="button" class="ds-btn ds-btn--ghost ds-btn--s-sm charcard__more" x-on:click="badgeModalOpen=true">전체 보기 →</button></div>
                    <div class="charcard__badges">
                      <template x-for="(bd, i) in badges()" x-bind:key="i">
                        <div class="gbadge" x-bind:class="bd.got ? 'got' : 'locked'" x-bind:style="bd.got ? ('--bc:' + bd.color) : ''" x-bind:data-tip="bd.desc + ' · +' + bd.exp + ' EXP'" data-tip-pos="top">
                          <span class="gbadge__orb"><span class="gbadge__ic" x-text="bd.got ? bd.icon : '🔒'"></span></span>
                          <span class="gbadge__label" x-text="bd.label"></span>
                          <span class="gbadge__cat" x-text="bd.cat"></span>
                        </div>
                      </template>
                    </div>
                  </div>
                </div>
              </template>
              <div x-show="reviewer && !arenaMe" class="charcard charcard--egg" data-tier="0">
                <div class="charcard__avatar"><img x-bind:src="charImg(reviewerChar)" alt="" style="opacity:.5;filter:grayscale(1)"><span class="charcard__lvl">Lv.0</span></div>
                <div class="charcard__title">🥚 검수 새싹</div>
                <div class="charcard__hint"><b class="text-ink" x-text="reviewer"></b> 의 첫 검수로 캐릭터를 깨워요 · <span class="arena-quest" x-on:click="selectMod('review')">검수하러 가기 →</span></div>
              </div>
            </div>
          </section>
        </div>
      </div>

      <!-- ═══ 모듈: 검수 대기 (팀 실시간 협업) · YELLOW 대기열 + 다중 의견 ═══ -->
      <!-- ═══ 모듈: 실행 큐 (단일 위젯) · 실제 실행 상태 ═══ -->
      <div x-show="mod === 'content'" x-cloak class="w-full" style="margin:18px 0 12px">
        <div class="stepline"><span class="stepline__no">STEP 3</span><b>실행 큐</b><span class="meta">진행 중인 추가·실행 작업 현황</span></div>
      </div>
      <div x-show="mod === 'content'" x-cloak class="w-full">
        <section class="panel"><div class="panel-hd"><b>실행 큐</b><span class="meta" x-text="(runningCount ? (runningCount + ' 실행중') : '대기 없음')"></span>
          <!-- 자동/수동 구분 필터 -->
          <span class="ml-auto" style="display:flex;gap:6px">
            <button type="button" class="srcfilter__chip" x-bind:class="queueTrig==='' ? 'sel' : ''" x-on:click="queueTrig=''">전체</button>
            <button type="button" class="srcfilter__chip" x-bind:class="queueTrig==='auto' ? 'sel' : ''" x-on:click="queueTrig='auto'">자동</button>
            <button type="button" class="srcfilter__chip" x-bind:class="queueTrig==='manual' ? 'sel' : ''" x-on:click="queueTrig='manual'">수동</button>
          </span>
        </div>
          <div class="panel-bd">
            <div x-show="loading && queueTrig !== 'auto'">
              <div class="w-run"><span class="w-run__av"><img src="/vendor/yonghee-pitcher.svg" alt=""></span><div><div class="w-run__t" x-text="(activeTabId === 'excel' ? '엑셀 일괄 추출 중' : '메타 추출 중') + ' · 수동'"></div><div class="ds-progress ds-progress--indeterminate" style="margin-top:5px"><div class="ds-progress__track"><div class="ds-progress__fill ds-progress__fill--primary"></div></div></div></div></div>
            </div>
            <template x-for="j in filteredJobs" x-bind:key="j.id">
              <div class="w-run"><span class="w-run__av"><img src="/vendor/daesik-batter.svg" alt=""></span><div style="flex:1;min-width:0">
                <div class="w-run__t"><b class="text-ink" x-text="j.name"></b> · 자동 인입 중 <span class="ds-badge" x-bind:class="j.trigger==='auto' ? 'ds-badge--intent' : 'ds-badge--entity'" x-text="j.trigger==='auto' ? '자동' : '수동'"></span> <span class="text-xs text-muted tnum" x-show="j.total" x-text="j.done + ' / ' + j.total + '건'"></span></div>
                <div class="text-xs text-muted" x-text="j.last_msg || j.endpoint"></div>
                <div class="ds-progress" x-bind:class="j.total ? '' : 'ds-progress--indeterminate'" style="margin-top:5px"><div class="ds-progress__track"><div class="ds-progress__fill ds-progress__fill--primary" x-bind:style="j.total ? ('width:' + Math.round((j.done/j.total)*100) + '%') : ''"></div></div></div>
              </div></div>
            </template>
            <div x-show="!runningCount" class="ds-empty" style="border:0;padding:20px 8px">
              <div class="ds-empty__desc">진행 중인 작업이 없습니다 <b class="text-ink">수동 추출</b> 또는 <b class="text-ink">자동 인입</b> 탭에서 실행하면 여기에 표시되고, 완료분은 <b class="text-ink">배치 결과</b>에 집계됩니다</div>
            </div>
          </div>
        </section>
      </div>

      <!-- ═══ 모듈: 프롬프트 스튜디오 (전용 도구) · 추출 단계별 프롬프트 ═══ -->
      <div x-show="mod === 'prompt'" x-cloak class="w-full space-y-4">
        <ul class="ds-bullets hintbox" style="padding:14px 16px">
          <li>각 단계의 <b>원천 프롬프트</b>를 아래 <b>코드블록</b>에서 직접 수정합니다(관리자 전용).</li>
          <li>단계마다 <b>모델을 지정</b>하면 그 모델의 프롬프트로 동작하고, 프롬프트는 <b>모델별로 분기 저장</b>됩니다.</li>
          <li>프롬프트는 <b>학습 반영 회차(버전)</b>마다 보정됩니다 · 현재 프롬프트 버전 <b class="text-ink tnum" x-text="verTxt"></b> · 지금 저장하면 이 버전의 실행에 반영됩니다.</li>
          <li>보완은 <b>테스트셋 생성 · 콘텐츠 검수</b>의 교정 피드백이 자동 반영됩니다.</li>
        </ul>
        <datalist id="modelopts"><template x-for="m in availableModels" x-bind:key="m"><option x-bind:value="m"></option></template></datalist>
        <section class="panel" data-fn><div class="panel-hd"><b>추출</b><span class="meta">대식 · 이미지 → 신호(OCR · 비전)</span><span class="ds-badge ds-badge--success ml-auto" x-show="learnedStages.extract" data-tip="배치 결과 피드백이 이 단계 프롬프트에 자동 반영 중">학습 보정 반영</span></div>
          <div class="panel-bd">
            <div class="stage-model"><span class="stage-model__lbl">모델</span><select class="field" x-model="stageModels.extract" x-on:change="onStageModelChange('extract')"><option value="">전역 프롬프트 (미지정)</option><template x-for="m in availableModels" x-bind:key="m"><option x-bind:value="m" x-text="m"></option></template></select></div>
            <div class="codeblock"><div class="codeblock__bar"><span class="codeblock__dots"><i></i><i></i><i></i></span>원천 프롬프트 · 추출<span class="codeblock__stage" x-text="stageModels.extract || '전역 프롬프트'"></span></div><textarea x-model="stagePrompts.extract" spellcheck="false" placeholder="이 단계의 원천 프롬프트(지시문)를 직접 수정하세요"></textarea></div>
            <div style="display:flex;align-items:center;gap:10px;margin-top:10px"><button type="button" x-on:click="saveStage('extract')" class="ds-btn ds-btn--primary">저장</button><button type="button" class="ds-btn ds-btn--secondary" x-on:click="restoreDefault('extract')">기본값 복원</button><span class="text-xs" style="color:var(--ds-success)" aria-live="polite" x-text="stageMsg.extract"></span><span class="text-xs" style="color:var(--ds-placeholder);margin-left:auto" x-text="stagePromptsMeta.extract ? ('최종 수정 ' + stagePromptsMeta.extract) : '수정 이력 없음'"></span></div>
          </div></section>
        <section class="panel" data-fn><div class="panel-hd"><b>분석</b><span class="meta">용희 · 신호 → 메타(리드문 · 엔티티 · 인텐트 · 카테고리)</span><span class="ds-badge ds-badge--success ml-auto" x-show="learnedStages.analyze" data-tip="배치 결과 피드백이 이 단계 프롬프트에 자동 반영 중">학습 보정 반영</span></div>
          <div class="panel-bd">
            <div class="stage-model"><span class="stage-model__lbl">모델</span><select class="field" x-model="stageModels.analyze" x-on:change="onStageModelChange('analyze')"><option value="">전역 프롬프트 (미지정)</option><template x-for="m in availableModels" x-bind:key="m"><option x-bind:value="m" x-text="m"></option></template></select></div>
            <div class="codeblock"><div class="codeblock__bar"><span class="codeblock__dots"><i></i><i></i><i></i></span>원천 프롬프트 · 분석<span class="codeblock__stage" x-text="stageModels.analyze || '전역 프롬프트'"></span></div><textarea x-model="stagePrompts.analyze" spellcheck="false" placeholder="이 단계의 원천 프롬프트(지시문)를 직접 수정하세요"></textarea></div>
            <div style="display:flex;align-items:center;gap:10px;margin-top:10px"><button type="button" x-on:click="saveStage('analyze')" class="ds-btn ds-btn--primary">저장</button><button type="button" class="ds-btn ds-btn--secondary" x-on:click="restoreDefault('analyze')">기본값 복원</button><span class="text-xs" style="color:var(--ds-success)" aria-live="polite" x-text="stageMsg.analyze"></span><span class="text-xs" style="color:var(--ds-placeholder);margin-left:auto" x-text="stagePromptsMeta.analyze ? ('최종 수정 ' + stagePromptsMeta.analyze) : '수정 이력 없음'"></span></div>
          </div></section>
        <section class="panel" data-fn><div class="panel-hd"><b>검수</b><span class="meta">복실 · 품질 메타 판정</span><span class="ds-badge ds-badge--success ml-auto" x-show="learnedStages.review" data-tip="배치 결과 피드백이 이 단계 프롬프트에 자동 반영 중">학습 보정 반영</span></div>
          <div class="panel-bd">
            <div class="stage-model"><span class="stage-model__lbl">모델</span><select class="field" x-model="stageModels.review" x-on:change="onStageModelChange('review')"><option value="">전역 프롬프트 (미지정)</option><template x-for="m in availableModels" x-bind:key="m"><option x-bind:value="m" x-text="m"></option></template></select></div>
            <div class="codeblock"><div class="codeblock__bar"><span class="codeblock__dots"><i></i><i></i><i></i></span>원천 프롬프트 · 검수<span class="codeblock__stage" x-text="stageModels.review || '전역 프롬프트'"></span></div><textarea x-model="stagePrompts.review" spellcheck="false" placeholder="이 단계의 원천 프롬프트(지시문)를 직접 수정하세요"></textarea></div>
            <div style="display:flex;align-items:center;gap:10px;margin-top:10px"><button type="button" x-on:click="saveStage('review')" class="ds-btn ds-btn--primary">저장</button><button type="button" class="ds-btn ds-btn--secondary" x-on:click="restoreDefault('review')">기본값 복원</button><span class="text-xs" style="color:var(--ds-success)" aria-live="polite" x-text="stageMsg.review"></span><span class="text-xs" style="color:var(--ds-placeholder);margin-left:auto" x-text="stagePromptsMeta.review ? ('최종 수정 ' + stagePromptsMeta.review) : '수정 이력 없음'"></span></div>
          </div></section>
        <section class="panel" data-fn><div class="panel-hd"><b>판정 · 부여</b><span class="meta">딱지 · 유통 결정 · 법령</span><span class="ds-badge ds-badge--success ml-auto" x-show="learnedStages.judge" data-tip="배치 결과 피드백이 이 단계 프롬프트에 자동 반영 중">학습 보정 반영</span></div>
          <div class="panel-bd">
            <div class="stage-model"><span class="stage-model__lbl">모델</span><select class="field" x-model="stageModels.judge" x-on:change="onStageModelChange('judge')"><option value="">전역 프롬프트 (미지정)</option><template x-for="m in availableModels" x-bind:key="m"><option x-bind:value="m" x-text="m"></option></template></select></div>
            <div class="codeblock"><div class="codeblock__bar"><span class="codeblock__dots"><i></i><i></i><i></i></span>원천 프롬프트 · 판정<span class="codeblock__stage" x-text="stageModels.judge || '전역 프롬프트'"></span></div><textarea x-model="stagePrompts.judge" spellcheck="false" placeholder="이 단계의 원천 프롬프트(지시문)를 직접 수정하세요"></textarea></div>
            <div style="display:flex;align-items:center;gap:10px;margin-top:10px"><button type="button" x-on:click="saveStage('judge')" class="ds-btn ds-btn--primary">저장</button><button type="button" class="ds-btn ds-btn--secondary" x-on:click="restoreDefault('judge')">기본값 복원</button><span class="text-xs" style="color:var(--ds-success)" aria-live="polite" x-text="stageMsg.judge"></span><span class="text-xs" style="color:var(--ds-placeholder);margin-left:auto" x-text="stagePromptsMeta.judge ? ('최종 수정 ' + stagePromptsMeta.judge) : '수정 이력 없음'"></span></div>
          </div></section>
        <section class="panel"><div class="panel-hd"><b>추론 강도</b><span class="meta">전 단계 공통</span></div>
          <div class="panel-bd">
            <div class="ds-segmented" style="max-width:300px">
              <template x-for="o in reasoningOpts" x-bind:key="o.id"><button type="button" class="ds-segmented__item" x-bind:aria-pressed="reasoning === o.id ? 'true' : 'false'" x-on:click="setReasoning(o.id)" x-text="o.label"></button></template>
            </div>
            <div style="display:flex;align-items:center;gap:10px;margin-top:14px">
              <button type="button" x-on:click="applyStagePrompts()" class="ds-btn ds-btn--secondary">전체 단계 한꺼번에 저장</button>
              <span class="text-xs" style="color:var(--ds-muted)" aria-live="polite" x-text="prefMsg"></span>
            </div>
          </div></section>
      </div>

      <!-- ═══ 모듈: 인입 정책 (전용 도구) ═══ -->
      <!-- 수집(인입) 정책: 정책 표 성격 → 사전·정책 메뉴에 통합 렌더 -->
      <div x-show="mod === 'dict'" x-cloak class="w-full space-y-4" style="margin-top:16px">
        <div class="ds-widget ds-widget--info" style="--w-accent:#1e84ff">
          <div class="ds-widget__head"><div class="ds-widget__title"><span class="ds-widget__icon-chip"><svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></span><span>ITEM TYPE 처리 정책</span></div><div class="ds-widget__actions"><span class="ds-badge ds-badge--neutral">131</span><span class="text-xs text-muted">직접 수정 가능</span><span class="ds-widget__kind ds-widget__kind--info">정보</span></div></div>
          <div class="ds-widget__body">
            <div class="overflow-auto"><table class="ds-table"><thead><tr><th>ITEM TYPE</th><th>필터 대상</th><th>처리 방식</th><th>상태</th><th></th></tr></thead><tbody>
              <template x-for="(row, t) in (dictData ? dictData.intakePolicy : {})" x-bind:key="t">
                <tr>
                  <td class="text-ink" x-text="t"></td>
                  <td x-text="row.filter"></td>
                  <td><div class="tbox" x-text="row.method"></div></td>
                  <td><span class="ds-badge" x-bind:class="row.status === '구현' ? 'ds-badge--success' : (row.status === 'PoC' ? 'ds-badge--intent' : 'ds-badge--neutral')"><span x-show="row.status==='구현'" class="ds-badge__dot"></span><span x-text="row.status"></span></span></td>
                  <td><button type="button" class="copybtn" x-on:click="startEditIntake(t, row)">편집</button></td>
                </tr>
              </template>
              <template x-if="!dictData || !dictData.intakePolicy"><tr><td colspan="5" class="text-muted">불러오는 중…</td></tr></template>
            </tbody></table></div>
          </div>
        </div>
        <div class="ds-widget ds-widget--info" style="--w-accent:#a05cff;min-height:auto">
          <div class="ds-widget__head"><div class="ds-widget__title"><span class="ds-widget__icon-chip"><svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg></span><span>콘텐츠 출처 분류</span></div><div class="ds-widget__actions"><span class="ds-widget__kind ds-widget__kind--info">정보</span></div></div>
          <div class="ds-widget__body">
            <ul class="ds-bullets" style="margin-bottom:11px">
              <li><b>식별 표준</b>: C2PA(자격 증명) · SynthID(워터마크).</li>
              <li>발행자 정보로 <b>PGC/UGC 1차 식별</b>.</li>
            </ul>
            <div class="flex flex-wrap gap-1.5"><span class="ds-badge ds-badge--category">PGC 기존 미디어</span><span class="ds-badge ds-badge--category">UGC 사용자 생성</span><span class="ds-badge ds-badge--category">AIGC AI 생성</span><span class="ds-badge ds-badge--category">AIEC AI 보정</span></div>
          </div>
        </div>
      </div>

    </div>
  </div>
  </div><!-- /.appbody -->

  <!-- ✎ 편집 팝업 · 편집 버튼 클릭 시 바로 수정(사전·정책 등) -->
  <div class="ds-dialog-backdrop" x-show="editT" x-cloak x-on:mousedown.self="cancelEdit()" style="z-index:75">
    <div class="ds-dialog" role="dialog" aria-modal="true" aria-label="편집" style="max-width:520px">
      <h2 class="ds-dialog__title" x-text="'편집 · ' + editTitle"></h2>
      <div class="ds-dialog__body" style="display:flex;flex-direction:column;gap:12px">
        <div x-show="editT === 'intake_policy'" x-cloak>
          <label class="lbl">필터 대상</label>
          <select x-model="editFilter" class="field"><option value="O">O (필터)</option><option value="△">△ (부분)</option><option value="X">X (미적용)</option></select>
        </div>
        <div>
          <label class="lbl" x-text="editKind === 'list' ? '항목 (한 줄에 하나씩)' : (editT === 'intake_policy' ? '처리 방식' : '값')"></label>
          <textarea x-model="editVal" x-bind:rows="editT === 'intake_policy' ? 3 : 8" class="field" x-bind:style="editT === 'intake_policy' ? '' : 'min-height:180px'" x-on:keydown.escape="cancelEdit()"></textarea>
        </div>
        <div x-show="editT === 'legal_types'" x-cloak>
          <label class="lbl">근거 법령(조항)</label>
          <input x-model="editExtra" class="field" placeholder="예) 정보통신망법 제44조의7">
        </div>
        <div x-show="editT === 'intake_policy'" x-cloak>
          <label class="lbl">상태</label>
          <input x-model="editExtra" class="field" placeholder="예) 구현 / PoC / 계획">
        </div>
        <span class="text-xs" style="color:var(--ds-muted)" aria-live="polite" x-text="editMsg"></span>
      </div>
      <div class="ds-dialog__footer">
        <button type="button" class="ds-btn ds-btn--ghost" x-on:click="cancelEdit()">취소</button>
        <button type="button" class="ds-btn ds-btn--primary" x-on:click="saveEdit()">저장</button>
      </div>
    </div>
  </div>

  <!-- 대시보드 드릴다운: 분포 항목 → 판정된 콘텐츠 목록 -->
  <div class="ds-dialog-backdrop" x-show="drillOpen" x-cloak x-on:mousedown.self="drillOpen=false" style="z-index:72">
    <div class="ds-dialog" role="dialog" aria-modal="true" aria-label="콘텐츠 목록" style="max-width:620px">
      <h2 class="ds-dialog__title" style="display:flex;align-items:center;gap:10px"><span x-text="drillData ? (drillKindKr(drillData.kind) + ' · ' + drillData.value) : ''"></span><span class="ds-badge ds-badge--neutral" x-text="drillData ? (drillData.n + '건') : ''"></span></h2>
      <div class="ds-dialog__body" style="max-height:64vh;overflow:auto;margin-top:6px">
        <div x-show="drillBusy" class="text-xs text-muted" style="padding:14px">불러오는 중…</div>
        <table class="ds-table" x-show="!drillBusy && drillData && drillData.items.length"><thead><tr><th>서비스</th><th>제목</th><th>등급</th><th>검수</th></tr></thead><tbody>
          <template x-for="(c,i) in (drillData?drillData.items:[])" x-bind:key="i"><tr style="cursor:pointer" role="button" tabindex="0" x-on:click="openDetail(c)" x-on:keydown.enter="openDetail(c)" data-tip="상세·검수 열기" data-tip-pos="left">
            <td x-text="c.service || '·'"></td>
            <td class="text-ink" x-text="c.title || c.summary || '·'"></td>
            <td><span class="ds-badge" x-bind:class="c.grade==='G'?'ds-badge--success':'ds-badge--error'"><span class="ds-badge__dot"></span><span x-text="c.grade || '·'"></span></span></td>
            <td><span class="ds-badge" x-show="c.fb && c.fb.verdict" x-bind:class="c.fb && c.fb.verdict==='good' ? 'ds-badge--success' : 'ds-badge--error'" x-text="c.fb && c.fb.verdict==='good' ? '✓ 완료' : '✓ 수정'"></span><span class="text-xs text-muted" x-show="!(c.fb && c.fb.verdict)">·</span></td>
          </tr></template>
        </tbody></table>
        <div x-show="!drillBusy && drillData && !drillData.items.length" class="text-xs text-muted" style="padding:14px">해당 콘텐츠가 없습니다</div>
      </div>
      <div style="text-align:right;margin-top:14px"><button type="button" class="ds-btn ds-btn--outline ds-btn--c-neutral ds-btn--s-md" x-on:click="drillOpen=false">닫기</button></div>
    </div>
  </div>

  <!-- 콘텐츠 상세 스플릿뷰(공통 컴포넌트): 좌 추출 원문 렌더 · 우 평가 -->
  <div class="ds-dialog-backdrop" x-show="detailOpen" x-cloak x-on:mousedown.self="detailOpen=false" style="z-index:74">
    <div class="detailview" role="dialog" aria-modal="true" aria-label="콘텐츠 상세">
      <div class="detailview__hd">
        <span style="display:flex;align-items:center;gap:8px">
          <button type="button" class="ds-iconbtn ds-iconbtn--sm" x-show="detailBack" x-on:click="detailOpen=false; drillOpen=true" data-tip="목록으로" data-tip-pos="bottom" aria-label="목록으로 뒤로가기"><svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M15 5l-7 7 7 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
          <b>콘텐츠 상세 · 검수</b>
        </span>
        <button type="button" class="ds-iconbtn ds-iconbtn--sm" x-on:click="detailOpen=false" aria-label="닫기"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></button>
      </div>
      <div class="detailview__body">
        <div class="detailview__content">
          <div class="dvc__service" x-text="detail && (detail.service || '·')"></div>
          <h2 class="dvc__title" x-text="detail && (detail.title || '(제목 없음)')"></h2>
          <div class="dvc__subtitle" x-show="detail && detail.subtitle" x-text="detail && detail.subtitle"></div>
          <div class="dvc__lead" x-show="detail && detail.summary"><span class="dvc__lead-lbl">리드문</span><span x-text="detail && detail.summary"></span></div>
          <div class="dvc__bodytext" x-show="detail && detail.body" x-text="detail && detail.body"></div>
          <div class="dvc__note" x-show="detail && !detail.body">전체 본문은 저장되지 않습니다 · 리드문·메타 기준으로 검수하세요</div>
          <a class="dvc__src ds-btn ds-btn--outline ds-btn--c-neutral ds-btn--s-sm" x-show="detail && detail.url" x-bind:href="detail && detail.url" target="_blank" rel="noreferrer">원문 열기 ↗</a>
        </div>
        <div class="detailview__eval">
          <div x-show="detail && detail.grade" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><span class="ds-badge" x-bind:class="detail && detail.grade==='G'?'ds-badge--success':'ds-badge--error'"><span class="ds-badge__dot"></span><span x-text="detail && (detail.grade==='G'?'유통 가능 · G':'차단 · R')"></span></span><span class="ds-badge ds-badge--intent" style="cursor:help" x-show="detail && detail.model" data-tip="이 결과 초안을 만든 모델 · 교정 피드백이 이 모델 프롬프트로 귀속됩니다" data-tip-pos="top" x-text="detail ? detail.model : ''"></span></div>
          <div class="dve__sec"><div class="dve__lbl">엔티티</div><div class="flex flex-wrap gap-1"><template x-for="e in (detail?detail.entities:[])" x-bind:key="e"><span class="ds-badge ds-badge--entity" x-text="e"></span></template><span x-show="detail && !detail.entities.length" class="text-xs text-muted">·</span></div></div>
          <div class="dve__sec"><div class="dve__lbl">인텐트</div><div class="flex flex-wrap gap-1"><template x-for="e in (detail?detail.intent:[])" x-bind:key="e"><span class="ds-badge ds-badge--intent" style="cursor:help" x-bind:data-tip="termDef('intent', e)" data-tip-pos="right" x-text="e"></span></template><span x-show="detail && !detail.intent.length" class="text-xs text-muted">·</span></div></div>
          <div class="dve__sec"><div class="dve__lbl">카테고리</div>
            <div class="flex flex-wrap gap-1 items-center">
              <template x-for="e in (detail?detail.category:[])" x-bind:key="e"><span class="ds-badge ds-badge--category" style="cursor:help" x-bind:data-tip="termDef('category', e)" data-tip-pos="right" x-text="e"></span></template>
              <!-- 빈칸 감지 → 분류 필요 + 구조화 채우기(IAB Tier1/2) -->
              <template x-if="detail && !detail.category.length">
                <span style="display:inline-flex;align-items:center;gap:6px;flex-wrap:wrap">
                  <span class="ds-badge ds-badge--error">미분류 · 분류 필요</span>
                  <select class="field" style="height:30px;width:auto;padding:0 24px 0 8px;font-size:12px" x-on:change="fillCategory(detail, $event.target.value); $event.target.value=''">
                    <option value="">분류 선택…</option>
                    <template x-for="opt in categoryOptions" x-bind:key="opt"><option x-bind:value="opt" x-text="opt"></option></template>
                  </select>
                </span>
              </template>
            </div>
          </div>
          <div class="dve__sec" x-show="detail && detail.reasons && detail.reasons.length"><div class="dve__lbl">품질 사유</div><div class="flex flex-wrap gap-1"><template x-for="e in (detail?detail.reasons:[])" x-bind:key="e"><span class="ds-badge ds-badge--reason" style="cursor:help" x-bind:data-tip="termDef('reason', e)" data-tip-pos="right" x-text="e"></span></template></div></div>
          <div class="dve__verdict">
            <div class="dve__lbl">검수 판정</div>
            <!-- 검수 완료(판정 있음 · 수정 아님): 완료 표기(색상=판정별) + 수정 일시 + 추가 수정 -->
            <template x-if="detail && detail.fb && detail.fb.verdict && !editVerdict">
              <div>
                <span class="ds-badge" x-bind:class="detail.fb.verdict==='good' ? 'ds-badge--success' : 'ds-badge--error'"><span class="ds-badge__dot"></span><span x-text="detail.fb.verdict==='good' ? '검수 완료 · 정확' : '검수 완료 · 수정 필요'"></span></span>
                <span class="text-xs text-muted" x-show="detail.fb.ts" x-text="'· 최종 수정 ' + fmtTs(detail.fb.ts)" style="margin-left:6px"></span>
                <div class="tbox" x-show="detail.fb.verdict==='bad' && detail.fb.note" style="margin-top:8px" x-text="detail.fb.note"></div>
                <button type="button" class="ds-btn ds-btn--secondary ds-btn--s-sm" style="margin-top:10px" x-on:click="editVerdict=true; pendingBad=(detail.fb.verdict==='bad')">추가 수정</button>
              </div>
            </template>
            <!-- 미검수 또는 추가 수정 중 -->
            <template x-if="detail && (!(detail.fb && detail.fb.verdict) || editVerdict)">
              <div>
                <div style="display:flex;gap:8px">
                  <button type="button" class="verdictbtn verdictbtn--good" x-bind:class="!pendingBad && detail && detail.fb && detail.fb.verdict==='good' ? 'is-on' : ''" x-on:click="reviewGood()"><span class="verdictbtn__dot"></span>정확</button>
                  <button type="button" class="verdictbtn verdictbtn--bad" x-bind:class="pendingBad ? 'is-on' : ''" x-on:click="pendingBad=true"><span class="verdictbtn__dot"></span>수정 필요</button>
                </div>
                <!-- 수정 필요: 요소·사유 입력 후 '완료 처리' 로만 확정 -->
                <template x-if="pendingBad">
                  <div style="margin-top:10px">
                    <div class="dve__lbl" style="margin-bottom:6px">어떤 요소를 고칠까요?</div>
                    <div class="fixelems">
                      <template x-for="fe in FIX_ELEMENTS" x-bind:key="fe.id">
                        <button type="button" class="fixelem" x-bind:class="fbElems(detail.fb).includes(fe.id) ? 'sel' : ''" x-on:click="toggleFixElem(detail.fb, fe.id)" x-text="fe.label"></button>
                      </template>
                    </div>
                    <textarea x-model="detail.fb.note" rows="3" class="field" style="margin-top:8px" x-bind:placeholder="fbElems(detail.fb).map((e)=>elemLabel(e)).join('·') + ' 이(가) 왜 잘못됐는지 · 요소를 여러 개 고르면 각 단계로 나눠 반영됩니다'"></textarea>
                    <div style="display:flex;gap:8px;margin-top:8px">
                      <button type="button" class="ds-btn ds-btn--primary ds-btn--s-sm" x-on:click="reviewBadComplete()" x-bind:disabled="!(detail.fb.note||'').trim()">완료 처리</button>
                      <button type="button" class="ds-btn ds-btn--secondary ds-btn--s-sm" x-on:click="pendingBad=false; if(!(detail.fb&&detail.fb.verdict)) editVerdict=false">취소</button>
                    </div>
                  </div>
                </template>
              </div>
            </template>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ⚙ 설정 = 고정 팝업(Dialog) · 위젯 아님(§9.5) -->
  <!-- 플로팅 도우미(채널톡 스타일) · 모든 기능 허브 -->
  <div class="ds-chat" x-show="chatOpen" x-cloak>
    <div class="ds-chat__head">
      <span class="ds-chat__av"><img x-bind:src="charImg(reviewer ? reviewerChar : 'boksil')" alt=""></span>
      <div><div class="ds-chat__title" x-text="(reviewer || 'Prism') + ' 에이전트'"></div><div class="ds-chat__sub"><span class="ds-statusdot ds-statusdot--ok"><span class="ds-statusdot__dot"></span></span>보통 1분 내 응답</div></div>
      <span style="margin-left:auto"><button type="button" class="ds-iconbtn ds-iconbtn--sm" x-on:click="chatOpen = false" aria-label="닫기"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></button></span>
    </div>
    <div class="ds-chat__body">
      <template x-for="(m, i) in chatMsgs" x-bind:key="i"><div class="ds-chat__msg" x-bind:class="m.from === 'me' ? 'ds-chat__msg--me' : 'ds-chat__msg--bot'" x-text="m.text"></div></template>
    </div>
    <div class="ds-chat__quick">
      <button type="button" class="chatchip" x-on:click="chatAct('extract')">＋ 새 추출</button>
      <button type="button" class="chatchip" x-on:click="chatAct('dict')">사전 편집</button>
      <button type="button" class="chatchip" x-on:click="chatAct('settings')">설정</button>
    </div>
    <div class="ds-chat__foot"><textarea class="ds-chat__input" x-model="chatDraft" rows="1" placeholder="작업을 지시하세요…" x-on:keydown.enter.prevent="chatSend()"></textarea><button type="button" class="ds-chat__send" x-on:click="chatSend()" aria-label="보내기"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 8h10M8 4l4 4-4 4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></button></div>
  </div>

  <!-- 복사 토스트 -->
  <div x-show="copyMsg" x-cloak x-transition.opacity class="toast" x-text="copyMsg" aria-live="polite"></div>

</div>

<!-- 위젯 홈 인터랙션(편집·리사이즈·드래그·틸트) · SERVICE_DESIGN §4.3 규격 -->
<script>
  // ── 캐릭터 사용 정책(공통 규칙) ──────────────────────────────────────
  // 캐릭터 = 추출 파이프라인 4단계 역할 위젯/뷰는 자신이 속한 단계의 캐릭터만 쓴다
  //   추출 대식 · 분석 용희 · 검수 복실 · 판정·부여 딱지
  // ① 모듈(뷰) = 그 모듈의 파이프라인 역할  ② 프롬프트 스튜디오 = 단계별 패널이 그 단계
  // ③ 홈 위젯 = 위젯 역할(data-wid)  ④ 도우미·빈 상태 = 복실(안내)
  var CHAR = { extract: 'daesik-batter', analyze: 'yonghee-pitcher', review: 'boksil-catcher', judge: 'ddakji-manager' };
  var MOD_STAGE = { auto: 'extract', run: 'extract', queue: 'extract', intake: 'extract',
                    dash: 'analyze', user: 'analyze', topic: 'analyze',
                    quality: 'review', eval: 'review', dict: 'judge', prompt: null };
  var WID_STAGE = { 'launch-run': 'extract', 'launch-batch': 'analyze', 'launch-dict': 'judge',
                    metrics: 'extract', quality: 'review', intents: 'analyze', categories: 'analyze', process: 'analyze' };
  function stageChar(st) { return CHAR[st] || 'yonghee-pitcher'; }
  function panelStage(t) {
    t = t || '';
    if (/추출/.test(t)) return 'extract';
    if (/분석/.test(t)) return 'analyze';
    if (/검수|품질/.test(t)) return 'review';
    if (/판정|부여|법령/.test(t)) return 'judge';
    return 'analyze';
  }
  function viewMod(el) { var v = el.closest('[x-show]'); if (!v) return ''; var m = (v.getAttribute('x-show') || '').match(/mod === '(\\w+)'/); return m ? m[1] : ''; }
  function prismCharForPanel(h, titleText) {
    var mod = viewMod(h);
    if (mod === 'prompt') return stageChar(panelStage(titleText));   // 단계별
    var st = MOD_STAGE[mod];
    return st ? stageChar(st) : stageChar(panelStage(titleText));
  }
  // 카드 속성(기능/정보) = 캐릭터+칩 고정 클러스터. 종류별 캐릭터 통일 · 항상 헤드 우측 끝.
  var KIND_CHAR = { fn: 'boksil-catcher', info: 'yonghee-pitcher' };
  function prismCardType(isFn) {
    var kind = isFn ? 'fn' : 'info';
    var wrap = document.createElement('span'); wrap.className = 'ds-cardtype';
    wrap.innerHTML = '<span class="wz-char wz-char--sm"><img src="/vendor/' + KIND_CHAR[kind] + '.svg" alt=""></span>'
      + '<span class="ds-widget__kind ds-widget__kind--' + kind + '">' + (isFn ? '기능' : '정보') + '</span>';
    return wrap;
  }
  // 헤더 액션: 정보(메타·라벨)와 컨트롤(버튼) 경계에 구분선 자동 삽입(전 카드 공통). 카드속성 구분선과 동일 규칙.
  function insertHdDivider(actions) {
    var kids = Array.prototype.slice.call(actions.children);
    for (var i = 1; i < kids.length; i++) {
      var el = kids[i];
      var isCtrl = el.matches && el.matches('button, a.ds-btn, .ds-btn, .copybtn, .ds-iconbtn');
      if (isCtrl && !(kids[i - 1].classList && kids[i - 1].classList.contains('hd-divider'))) {
        var d = document.createElement('span'); d.className = 'hd-divider'; d.setAttribute('aria-hidden', 'true');
        actions.insertBefore(d, el);
        break;                                      // 정보|컨트롤 경계 1곳
      }
    }
  }
  (function () {
    var grid = document.getElementById('grid'); if (!grid) return;
    var ORDER = ['', 'ds-widget--md', 'ds-widget--lg', 'ds-widget--tall', 'ds-widget--wide', 'ds-widget--xl'];
    function editing() { return grid.classList.contains('ds-widgetgrid--edit'); }
    // 선택 + 8핸들(우하단 리사이즈)
    var SELH = '<div class="ds-widget__sel" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>';
    var selected = null;
    function deselect() { if (selected) { selected.classList.remove('ds-widget--selected'); var s = selected.querySelector('.ds-widget__sel'); if (s) s.remove(); selected = null; } }
    function select(w) { deselect(); w.classList.add('ds-widget--selected'); w.insertAdjacentHTML('beforeend', SELH); selected = w; }
    function cycleSize(w) {
      var cur = 0; ORDER.forEach(function (c, i) { if (c && w.classList.contains(c)) cur = i; });
      ORDER.forEach(function (c) { if (c) w.classList.remove(c); });
      var nx = ORDER[(cur + 1) % ORDER.length]; if (nx) w.classList.add(nx);
    }
    grid.addEventListener('click', function (e) {
      if (!editing()) return;
      if (e.target.closest('.ds-widget__remove')) return;   // 제거는 Alpine removeWidget 가 처리
      var rs = e.target.closest('.ds-widget__sel i:nth-child(5)');
      if (rs && selected) { cycleSize(selected); return; }
      var w = e.target.closest('.ds-widget');
      if (w) { if (w !== selected) select(w); } else deselect();
    });
    // 드래그 재배치
    var dragEl = null;
    grid.addEventListener('dragstart', function (e) { if (!editing()) return; dragEl = e.target.closest('.ds-widget'); if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'; });
    grid.addEventListener('dragover', function (e) { if (!editing() || !dragEl) return; e.preventDefault(); var t = e.target.closest('.ds-widget'); if (t && t !== dragEl) { var r = t.getBoundingClientRect(); var after = (e.clientY - r.top) / r.height > 0.5; grid.insertBefore(dragEl, after ? t.nextSibling : t); } });
    grid.addEventListener('drop', function (e) { e.preventDefault(); dragEl = null; });
    new MutationObserver(function () {
      var on = editing();
      grid.querySelectorAll('.ds-widget').forEach(function (w) { w.setAttribute('draggable', on ? 'true' : 'false'); });
      if (!on) deselect();
    }).observe(grid, { attributes: true, attributeFilter: ['class'] });
    // 카드 3D 틸트 + 포일
    document.querySelectorAll('.ds-personacard').forEach(function (card) {
      card.addEventListener('pointermove', function (e) { var r = card.getBoundingClientRect(), px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height; card.style.setProperty('--ry', ((px - .5) * 16) + 'deg'); card.style.setProperty('--rx', ((.5 - py) * 16) + 'deg'); card.style.setProperty('--mx', (px * 100) + '%'); card.style.setProperty('--my', (py * 100) + '%'); });
      card.addEventListener('pointerleave', function () { card.style.setProperty('--ry', '0deg'); card.style.setProperty('--rx', '0deg'); card.style.setProperty('--mx', '50%'); card.style.setProperty('--my', '30%'); });
    });
    // 홈 위젯: 왼쪽 아이콘 제거(타이틀 왼쪽선 = 본문 정렬) + 캐릭터를 헤드 오른쪽으로
    grid.querySelectorAll('.ds-widget__head').forEach(function (head) {
      var t = head.querySelector('.ds-widget__title'); if (!t) return;
      var chip = t.querySelector('.ds-widget__icon-chip');
      var w = head.closest('.ds-widget'); var wid = w ? (w.getAttribute('data-wid') || '') : '';
      if (chip) chip.remove();
      var actions = head.querySelector('.ds-widget__actions');
      if (!actions) { actions = document.createElement('div'); actions.className = 'ds-widget__actions'; head.appendChild(actions); }
      var ek = actions.querySelector('.ds-widget__kind');            // 기존 인라인 칩 → 통일 클러스터로 교체
      var isFn = ek ? ek.classList.contains('ds-widget__kind--fn') : false;
      if (ek) ek.remove();
      insertHdDivider(actions);                                       // 정보|컨트롤 구분선
      actions.appendChild(prismCardType(isFn));                       // 캐릭터+칩 고정(우측 끝)
    });
  })();

  // ── 출력 패널(.panel) → 카탈로그 위젯 헤드로 변환(아이콘칩 + 타이틀 + 정보 칩) ──
  // 정적 패널은 즉시, Alpine x-for 로 생성되는 동적 패널은 MutationObserver 로 덮는다
  (function () {
    function decorate(root) {
      (root || document).querySelectorAll('.panel-hd:not([data-wz])').forEach(function (h) {
        h.setAttribute('data-wz', '1');
        var b = h.querySelector('b');
        var titleText = b ? b.textContent : '';
        // 타이틀 = 텍스트만(왼쪽선을 본문과 정렬) 캐릭터는 헤드 오른쪽으로
        var title = document.createElement('div'); title.className = 'ds-widget__title';
        var span = document.createElement('span');
        span.textContent = titleText; if (b) b.remove();
        title.appendChild(span);
        var actions = document.createElement('div'); actions.className = 'ds-widget__actions';
        while (h.firstChild) actions.appendChild(h.firstChild);   // 남은 메타·버튼 → actions
        insertHdDivider(actions);                                 // 정보|컨트롤 구분선(전 카드 공통)
        actions.appendChild(prismCardType(!!h.closest('[data-fn]')));   // 캐릭터+칩 고정 클러스터(우측 끝)
        h.appendChild(title); h.appendChild(actions);
      });
    }
    decorate(document);
    var host = document.querySelector('.home') || document.body;
    var pending = false;
    new MutationObserver(function () {
      if (pending) return; pending = true;
      requestAnimationFrame(function () { pending = false; decorate(document); });
    }).observe(host, { childList: true, subtree: true });
  })();
</script>
</body>
</html>"""



def main():
    ap = argparse.ArgumentParser(description="Prism 로컬 UI")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--mock", action="store_true", help="키가 있어도 강제 mock")
    a = ap.parse_args()

    Handler.server_mock = a.mock
    load_persisted_key()                              # ~/.prism_key 있으면 주입
    load_dict_overrides()                             # 사전 편집(overrides) 적용
    sync_prompt()                                     # config 의 추가 지시 반영
    # 백엔드 결정 · 운영은 Supabase 전용(조용한 로컬 폴백 금지, 미가용이면 시작 실패)
    _mode, _required = backend_mode()
    st = get_store()
    if _required:
        if not st:
            print("  [중단] Supabase 백엔드를 쓰려는데 초기화 실패 · SUPABASE_URL/SERVICE_KEY 확인.")
            print("         (로컬·오프라인은: PRISM_BACKEND=sqlite)")
            sys.exit(1)
        try:
            st.count()                                 # 연결 확인(잘못된 키·네트워크면 여기서 실패)
        except Exception as e:
            print(f"  [중단] Supabase 연결 실패: {e}")
            sys.exit(1)
    print(f"  백엔드: {_mode}" + (" · 공유(운영)" if _mode == "supabase" else " · 로컬"))
    start_ingest_scheduler()                           # 활성 소스 자동 폴링(백그라운드)
    start_learning_scheduler()                         # 매일 04:00 학습 일배치(합의 반영+골든+회귀평가)
    keyed = bool(IMG._api_key())
    mode = "MOCK(강제)" if a.mock else ("실모델" if keyed else "MOCK(키 미설정 · UI에서 설정)")
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"  Prism UI  →  http://{a.host}:{a.port}   [{mode}]")
    print("  키 설정: 우상단 설정(톱니) 버튼 · Ctrl+C 로 종료")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  종료")


if __name__ == "__main__":
    main()
