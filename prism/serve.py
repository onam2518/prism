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

# ── 로컬 영속 저장소(SQLite) — 추출 결과를 재시작해도 누적 보존 ──
_STORE = None
# PRISM_DB(컨테이너 볼륨 등) 우선, 없으면 기존 기본 위치(config 옆) — 기존 데이터 이동 방지.
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
    """Store 싱글턴(dual-mode). 운영은 Supabase 전용 — supabase 의도 시 SQLite 로 조용히
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
    """빌드 식별자(이 모듈 파일의 수정시각) — 설치본이 최신인지 확인용."""
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(__file__)))
    except Exception:
        return "?"


def store_save(pairs, source: str = "단건", team=None):
    """[(content, out), …] 를 영속 저장(+_LAST_RESULTS 미러). source: 출처. team: 소속 팀(supabase).
    적재 정책(dedup): 동일 콘텐츠 + 결과 무변경이면 적재 제외(skip), 변경 시 갱신, 신규는 추가."""
    outs = [o for _, o in pairs]
    _LAST_RESULTS[:] = outs
    st = get_store()
    if st:
        try:
            return st.save_dedup(pairs, _run_id(), source=source, team=team)
        except Exception:
            pass
    return None


def results_rows(limit: int = 5000, team=None) -> list:
    """집계용 결과 행 — 영속 저장소 우선(누적), 없으면 메모리(_LAST_RESULTS)."""
    st = get_store()
    if st:
        try:
            rows = st.recent(limit, team=team)
            if rows:
                return rows
        except Exception:
            pass
    return _LAST_RESULTS


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
def run_pipeline(fields: dict, *, mock: bool, team=None) -> dict:
    cfg = Config.load()
    llm = make_text_llm(cfg, mock)           # 텍스트 슬롯(solar|router). 무키면 내부서 mock

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
    store_save([(content, out)], team=team)      # 영속 저장(+미러, 팀 태깅)
    return {
        "source": source,
        "mock": llm.mock,
        "content": content,
        "signals": signals,
        "output": out,
    }


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
    """\ub300\uc2dc\ubcf4\ub4dc \ubaa8\ub4c8: \uc801\uc7ac \uacb0\uacfc \uc9d1\uacc4(\uc720\ud1b5 G/R \u00b7 \uc778\ud150\ud2b8 \u00b7 \uce74\ud14c\uace0\ub9ac \u00b7 \ud488\uc9c8 \uc0ac\uc720). team \ubcc4 \uc2a4\ucf54\ud551."""
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


def run_batch(file_bytes: bytes, filename: str) -> dict:
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
        store_save(pairs, source="배치")            # 영속 저장(단일 트랜잭션 배치)
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
        verdict = data.get("verdict") or ""        # good | bad | ""(취소)
        reviewer = (data.get("reviewer") or "").strip() or "(익명)"   # 귀속 키(uid 또는 이름)
        disp = (data.get("name") or "").strip() or reviewer          # 토스트 표시명
        note = (data.get("note") or "").strip()
        stage = data.get("stage") or "analyze"
        st.save_feedback(ch, data.get("service", ""), data.get("title", ""),
                         verdict, stage, note, time.time(), reviewer=reviewer, team=data.get("_team"))
        broadcast({"type": "feedback", "hash": ch, "reviewer": disp,
                   "verdict": verdict, "title": data.get("title", ""),
                   "service": data.get("service", ""), "ts": time.time()})
        if verdict == "bad" and note:              # REAP 피드백 하네스(백그라운드)
            fb = {"stage": stage, "note": note, "title": data.get("title", "")}
            threading.Thread(target=_reap_async, args=(ch, reviewer, fb),
                             daemon=True).start()
    sync_learned()                                 # 다음 추출부터 자동 반영
    return {"ok": True, "feedback": st.feedback_stats(),
            "learned": {k: bool(v) for k, v in (PR.LEARNED or {}).items()}}


def _reap_async(content_hash: str, reviewer: str, fb: dict):
    """REAP(Remember→Explain→Ask→Plan)로 피드백을 가공 → plan 저장 → LEARNED 재반영·브로드캐스트."""
    try:
        cfg = Config.load()
        llm = make_text_llm(cfg, Handler.server_mock)   # 서버 mock 존중(키 없으면도 mock)
        reap = FL.run_reap(llm, fb)
        st = get_store()
        if st:
            st.save_reap(content_hash, reviewer, reap)
        sync_learned()                             # 가공된 plan 을 단계 프롬프트에 반영
        broadcast({"type": "reap", "hash": content_hash, "reviewer": reviewer,
                   "stage": reap.get("stage", ""), "plan": reap.get("plan", ""),
                   "ask": reap.get("ask", "")})
    except Exception as e:
        print(f"  [warn] REAP 처리 실패(hash={content_hash[:12]}): {e}")


def reap_for(data: dict) -> dict:
    """콘텐츠의 검수자별 REAP 산출(UI 표시)."""
    st = get_store()
    if not st:
        return {"ok": False, "items": []}
    return {"ok": True, "items": st.get_reap((data.get("hash") or "").strip())}


# ── Supabase Auth(ID/PW) — 서버 프록시 + JWT 검증(supabase 모드) ──
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
    try:
        req = urllib.request.Request(url + "/auth/v1/user", method="GET",
                                     headers={"apikey": key, "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            uid = json.loads(r.read().decode("utf-8")).get("id")
    except Exception:
        uid = None
    if uid:
        with _JWT_LOCK:
            _JWT_CACHE[token] = (uid, now + 60)
    return uid


def register_reviewer(data: dict) -> dict:
    """검수자 등록: (인증 uid 또는 이름) + 표시명 + 캐릭터 (+ supabase 면 팀 생성/가입)."""
    st = get_store()
    if not st:
        return {"ok": False}
    rv = (data.get("reviewer") or "").strip()        # 키: 이름(sqlite) 또는 uid(supabase 주입)
    name = (data.get("name") or "").strip() or rv
    ch = (data.get("char") or "boksil").strip()
    if not rv:
        return {"ok": False, "error": "검수자 식별 실패"}
    team = None
    if _supa() and hasattr(st, "ensure_team"):
        team = st.ensure_team(rv, (data.get("team_mode") or "create"),
                              data.get("team_name"), data.get("invite_code"))
        if not team:
            return {"ok": False, "error": "팀을 찾을 수 없습니다 — 초대코드를 확인하세요"}
        st.set_reviewer(rv, name, ch, team)
    else:
        st.set_reviewer(rv, name, ch)
    broadcast({"type": "reviewer", "reviewer": name, "char": ch})
    info = st.team_info(team) if (team and hasattr(st, "team_info")) else None
    return {"ok": True, "team": info}                # info.invite_code 로 초대코드 표시


def eval_golden(team=None) -> dict:
    """프로세스 1 — 관리자 등록 골든셋으로 원천 프롬프트 정합성 측정(기대 vs 실제). abtest 재사용."""
    st = get_store()
    if not (st and hasattr(st, "get_golden")):
        return {"ok": False, "error": "골든셋 평가는 Supabase 모드 전용입니다"}
    rows = st.get_golden(team)
    if not rows:
        return {"ok": False, "error": "등록된 골든셋이 없습니다 — 팀 관리에서 등록하세요"}
    from . import abtest
    from . import harness as H
    cfg = Config.load()
    llm = make_text_llm(cfg, Handler.server_mock)
    m = abtest.evaluate(rows[:300], H.Methodology(name="원천"), llm, concurrency=8)
    m["ok"] = True
    m["evaluated"] = min(len(rows), 300)
    return m


def register_golden(uid, team, rows) -> dict:
    """관리자가 팀 골든셋 등록(교체). rows: [{content, expected}]."""
    st = get_store()
    if not (st and team and hasattr(st, "is_team_admin") and st.is_team_admin(uid, team)):
        return {"ok": False, "error": "관리자 전용입니다"}
    n = st.register_golden(team, [r for r in rows if isinstance(r, dict) and r.get("content") and r.get("expected")])
    return {"ok": True, "count": n}


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
    # 컴파일된 directive 를 단계 프롬프트(LEARNED)로 반영 — raw 누적 대체
    PR.LEARNED = {k: (results.get(k, {}).get("directive") or "") for k in ("extract", "analyze", "review", "judge")}
    return {"ok": True, "results": results}


def admin_data(uid, team) -> dict:
    """팀 관리: 팀 정보·멤버·관리자 여부. supabase 전용."""
    st = get_store()
    if not (st and team and hasattr(st, "team_members")):
        return {"ok": False, "isAdmin": False, "team": None, "members": []}
    gc = st.golden_count(team) if hasattr(st, "golden_count") else 0
    return {"ok": True, "isAdmin": st.is_team_admin(uid, team),
            "team": st.team_info(team), "members": st.team_members(team), "goldenCount": gc}


def admin_ingest(uid, team, endpoint, n) -> dict:
    """관리자: 크롤러 엔드포인트에서 N건 당겨와 추출 → 전건 검토 대상으로 팀 큐 적재(배치).
    실시간 스트리밍 부담 없이 관리자가 수량 목표로 트리거."""
    st = get_store()
    if not (st and team and hasattr(st, "is_team_admin") and st.is_team_admin(uid, team)):
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


def admin_action(uid, team, data) -> dict:
    """관리자 액션(데이터 삭제·멤버 제거). 팀 생성자만."""
    st = get_store()
    if not (st and team and hasattr(st, "is_team_admin") and st.is_team_admin(uid, team)):
        return {"ok": False, "error": "관리자 전용입니다"}
    act = data.get("action")
    if act == "clear_feedback":
        st.clear_team_feedback(team)
    elif act == "clear_contents":
        st.clear_team_contents(team)
    elif act == "remove_member" and data.get("member"):
        st.remove_member(team, data["member"])
    elif act == "ingest":                          # 크롤러 수량 인입 → 검토 큐
        return admin_ingest(uid, team, data.get("endpoint"), data.get("n"))
    else:
        return {"ok": False, "error": "알 수 없는 액션"}
    return {"ok": True}


def arena_data(team=None) -> dict:
    """평가 아레나(게임화) 데이터: 팀 정확도 + 리더보드 + 검수 대기(퀘스트). team 별 스코핑."""
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


def review_queue(data: dict) -> dict:
    """검수 대기 큐(YELLOW). only_unreviewed=false 면 검수된 것도 포함."""
    st = get_store()
    if not st:
        return {"ok": False, "error": "store unavailable", "items": []}
    only_un = data.get("only_unreviewed", True)
    limit = int(data.get("limit") or 100)
    items = st.review_queue(limit=limit, only_unreviewed=bool(only_un), team=data.get("team"))
    return {"ok": True, "items": items, "n": len(items)}


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
        elif self.path.startswith("/topics"):
            self._send(200, json.dumps(topics_data(), ensure_ascii=False), _JSON)
        elif self.path.startswith("/dashboard"):
            self._send(200, json.dumps(dashboard_data(self._req_team()), ensure_ascii=False), _JSON)
        elif self.path.startswith("/arena"):
            self._send(200, json.dumps(arena_data(self._req_team()), ensure_ascii=False), _JSON)
        elif self.path.startswith("/admin"):
            self._send(200, json.dumps(admin_data(self._bearer_uid(), self._req_team()),
                                       ensure_ascii=False), _JSON)
        elif self.path.startswith("/queue"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            data = {"only_unreviewed": q.get("all", ["0"])[0] not in ("1", "true"),
                    "limit": (q.get("limit", ["100"])[0]), "team": self._req_team()}
            self._send(200, json.dumps(review_queue(data), ensure_ascii=False), _JSON)
        elif self.path.startswith("/events"):
            self._serve_sse()
        elif self.path.startswith("/reap"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            self._send(200, json.dumps(reap_for({"hash": q.get("hash", [""])[0]}),
                                       ensure_ascii=False), _JSON)
        elif self.path.startswith("/ingest-status"):
            self._send(200, json.dumps(ingest_status(), ensure_ascii=False), _JSON)
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
                self._send(200, json.dumps(apply_feedback(data), ensure_ascii=False), _JSON)
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
                           json.loads(body or b"{}")), ensure_ascii=False), _JSON)
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

        if self.path.startswith("/eval-golden"):       # 등록 골든셋으로 평가 실행
            try:
                self._send(200, json.dumps(eval_golden(self._req_team()), ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/golden"):            # 관리자: 골든셋 등록(.jsonl 업로드)
            try:
                ctype = self.headers.get("Content-Type", "")
                if "multipart/form-data" in ctype:
                    f = _parse_multipart(body, ctype.split("boundary=", 1)[1].strip()).get("file")
                    raw = f.get("bytes", b"") if isinstance(f, dict) else b""
                else:
                    raw = body
                rows = [json.loads(ln) for ln in raw.decode("utf-8", "replace").splitlines() if ln.strip()]
                self._send(200, json.dumps(register_golden(self._bearer_uid(), self._req_team(), rows),
                                           ensure_ascii=False), _JSON)
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

        if self.path.startswith("/ingest-run"):
            try:
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
                    result = run_batch(f["bytes"], f.get("filename", "upload.xlsx"))
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
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Crect width='24' height='24' rx='6' fill='%2320808d'/%3E%3Cpath d='M12 4l1.7 5L19 12l-5.3 1.7L12 19l-1.7-5.3L5 12l5.3-1.7z' fill='%23fff'/%3E%3C/svg%3E">
<link href="/vendor/pretendard.css" rel="stylesheet">
<link href="/vendor/ds-theme.css" rel="stylesheet">
<link href="/vendor/ds-components.css" rel="stylesheet">
<script src="/vendor/tailwind.js"></script>
<script>
  tailwind.config = {
    theme: { extend: {
      fontFamily: {
        sans: ['"Pretendard Variable"', 'Pretendard', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      colors: {
        // Anchor(axz) 라이트 · 단일 토큰 소스(ds-theme.css 와 1:1). Blue=Primary 액션.
        // 'violet' 은 역사적 유틸명 — 값은 Anchor Blue 로 통일.
        violet: { DEFAULT: '#1e84ff', hover: '#0066db', deep: '#004fad', tint: 'rgba(30,132,255,0.16)' },
        solar: '#18ba45',
        canvas: '#f4f5f7', surface: '#ffffff', surface2: '#ffffff',
        ink: '#000000', body: 'rgba(0,0,0,0.88)', muted: 'rgba(0,0,0,0.48)', hair: 'rgba(0,0,0,0.08)',
      },
    } },
  };
</script>
<style>
  /* 앱 레벨 별칭 — 역사적 변수명(--ds-violet*·--ds-surface2·--ds-solar)을
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
      // 위젯 홈 셸 — 홈(캔버스) + 카테고리 내비
      mod: 'home',
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
      },
      mods: [
        { g: '작업', items: [
          { id: 'auto', label: '자동 인입', ic: 'auto', cond: 'admin' },
          { id: 'run', label: '수동 추출', ic: 'run' } ] },
        { g: '콘텐츠 현황', items: [
          { id: 'queue', label: '실행 큐', ic: 'queue' },
          { id: 'dash', label: '배치 결과', ic: 'dash' },
          { id: 'review', label: '검수 큐', ic: 'review' },
          { id: 'arena', label: '평가 아레나', ic: 'arena' },
          { id: 'quality', label: '품질 · 토픽', ic: 'quality' },
          { id: 'eval', label: '검증 · 평가', ic: 'eval' } ] },
        { g: '사용자 현황', items: [
          { id: 'user', label: '사용자 메타', ic: 'user' } ] },
        { g: '정책 · 기준 (전용 도구)', items: [
          { id: 'dict', label: '사전 · 정책 관리', ic: 'dict' },
          { id: 'prompt', label: '프롬프트 스튜디오', ic: 'prompt' },
          { id: 'intake', label: '인입 정책', ic: 'intake' } ] },
        { g: '팀', items: [
          { id: 'admin', label: '팀 관리', ic: 'admin', cond: 'supabase' } ] },
      ],
      // 위젯 홈 인터랙션 상태
      settingsOpen: false, chatOpen: false, addMenuOpen: false, editing: false, theme: 'light',
      chatMsgs: [{ from: 'bot', text: '무엇을 도와드릴까요? 작업을 말로 지시해 보세요' }],
      chatDraft: '',
      dashData: null, topicData: null, dictData: null, userData: null, modBusy: false, dictGroup: '',
      // 팀 실시간 HITL: 검수자 식별(이름+캐릭터) · 검수 큐 · 라이브 이벤트
      reviewer: '', reviewerEditing: false, reviewerChar: 'boksil',
      // Supabase 인증(ID/PW) — backend==='supabase' 일 때
      backend: 'sqlite', authToken: '', authEmail: '', authPw: '', authMode: 'login', authMsg: '',
      // 팀(멀티테넌시): 생성/가입 + 내 초대코드
      teamMode: 'create', teamName: '', inviteCode: '', myInvite: '',
      charOptions: [
        { id: 'boksil', label: '복실', role: '검수', img: '/vendor/boksil-catcher.svg' },
        { id: 'daesik', label: '대식', role: '추출', img: '/vendor/daesik-batter.svg' },
        { id: 'yonghee', label: '용희', role: '분석', img: '/vendor/yonghee-pitcher.svg' },
        { id: 'ddakji', label: '딱지', role: '판정', img: '/vendor/ddakji-manager.svg' },
      ],
      queueData: { items: [], n: 0 }, queueOnlyUnreviewed: true,
      arenaData: null,
      adminData: null,        // 팀 관리(supabase)
      // 평가 2탭: 원천(골든셋) / 실시간(메타컴파일)
      evalTab: 'golden', goldenResult: null, goldenBusy: false, goldenMsg: '',
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

      // 설정(키 / 모델 슬롯 / 추론강도 / 추가 지시) — 우측 설정 패널
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
      cfgTab: 'keys',                   // 설정 탭: keys | models (Atelier 방식)
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
        this.loadReviewer();                           // 검수자·토큰(localStorage) — refreshConfig 의 관리자 로드보다 먼저
        this.refreshConfig();
        this.startLive();                              // 실시간 SSE 구독
        this.loadHome();                               // 배치된 홈 위젯(localStorage)
        this.loadDash();                               // 홈 위젯 데이터(/dashboard)
        // 딥링크: ?m=run|dash|quality|... 로 특정 뷰 진입(설정은 ?settings)
        try { const q = new URLSearchParams(location.search); const m = q.get('m'); if (m) this.selectMod(m); if (q.has('settings')) this.settingsOpen = true; } catch (e) {}
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
      // 연결 현황(다중) — 여러 제공자를 동시에 넣어도 각각의 연결 상태를 표시
      get connList() {
        return [
          { id: 'solar', label: 'Solar', on: !!this.cfg.hasKey },
          { id: 'bizrouter', label: 'BizRouter', on: !!this.cfg.hasBizKey },
          { id: 'timely', label: 'Timely', on: !!this.cfg.hasTimelyKey },
        ];
      },
      get connCount() { return this.connList.filter((c) => c.on).length; },
      get modSub() {
        const m = { home: '위젯을 추가·삭제·재배치해 나만의 콘솔을 구성하세요', auto: '콘텐츠 자동 인입 파이프라인 설정 (REST API · Kafka 등)', run: '수동으로 이미지·텍스트·엑셀 추출 (기본 운영은 자동 인입)', queue: '진행 중·대기 중인 추출 작업', dash: '추출 결과 집계 · 유통 G/R · 분포', review: 'YELLOW 사람검수 대기열 · 팀 다중 의견 + 실시간 협업', arena: '팀 정확도를 함께 끌어올리는 평가 — 검수할수록 게이지가 차오르고 기여가 점수로', admin: '팀 멤버 · 초대 코드 · 데이터 관리(관리자)', quality: '품질·법령 판정 + 엔티티·사건·조건 토픽', user: '행동 로그 → 소비 형태·강도·선호', eval: '콘텐츠별 평가 피드백(학습 루프) · 추출 trace·fallback·비용', dict: '사전·카테고리·품질·법령 정책을 직접 수정', prompt: '추출 방향을 조향하는 시스템 프롬프트·추론 강도', intake: 'ITEM TYPE별 필터·처리 정책 + 콘텐츠 출처 분류' };
        return m[this.mod] || '';
      },
      selectMod(id) {
        this.mod = id; this.status = ''; this.addMenuOpen = false;
        if (id === 'home' || id === 'dash' || id === 'queue' || id === 'eval') this.loadDash();
        else if (id === 'review') this.loadQueue();
        else if (id === 'arena') this.loadArena();
        else if (id === 'admin') this.loadAdmin();
        else if (id === 'quality') this.loadTopics();
        else if (id === 'dict' || id === 'intake') this.loadDict();
        else if (id === 'user') this.loadUser();
        else if (id === 'prompt') this.loadPromptDefaults();
        if (id === 'queue' || id === 'auto') { this.fetchIngestStatus(); this.pollIngestStatus(); }
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
        else if (act === 'settings') this.settingsOpen = true;
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
      async loadDash() { this.modBusy = true; try { this.dashData = await (await fetch('/dashboard')).json(); } catch (e) {} this.modBusy = false; },
      // ── 배치 결과: 콘텐츠별 평가 피드백 → 학습 루프 ──
      fbNoteOpen: {},
      async setFeedback(c, verdict) {
        const cur = (c.fb && c.fb.verdict) || '';
        const v = (cur === verdict) ? '' : verdict;        // 같은 버튼 재클릭 = 취소
        c.fb = Object.assign({}, c.fb, { verdict: v });
        if (v === 'bad') this.fbNoteOpen[c.hash] = true;
        await this._postFb({ hash: c.hash, service: c.service, title: c.title, verdict: v, stage: (c.fb.stage || 'analyze'), note: (c.fb.note || '') });
      },
      async saveFbNote(c) {
        c.fb = Object.assign({}, c.fb, { verdict: c.fb.verdict || 'bad' });
        await this._postFb({ hash: c.hash, service: c.service, title: c.title, verdict: c.fb.verdict, stage: (c.fb.stage || 'analyze'), note: (c.fb.note || '') });
        this.fbNoteOpen[c.hash] = false;
      },
      async _postFb(payload) {
        payload = Object.assign({ reviewer: this.reviewer || '', name: this.reviewer || '' }, payload);  // 키+표시명(supabase 면 서버가 uid 로 덮어씀)
        try { const r = await (await fetch('/feedback', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(payload) })).json(); if (r && r.feedback && this.dashData) this.dashData.feedback = r.feedback; } catch (e) {}
      },
      // ── 팀 실시간 HITL: 검수자 식별 · 검수 큐 · 라이브 ──
      loadReviewer() {
        try { this.reviewer = localStorage.getItem('prism_reviewer') || ''; this.reviewerChar = localStorage.getItem('prism_reviewer_char') || 'boksil'; this.authToken = localStorage.getItem('prism_token') || ''; } catch (e) {}
        if (!this.reviewer) this.reviewerEditing = true;            // 첫 방문 → 등록/로그인 모달
      },
      _authHeaders() { const h = { 'Content-Type': 'application/json' }; if (this.authToken) h['Authorization'] = 'Bearer ' + this.authToken; return h; },
      async saveReviewer() {
        const v = (this.reviewer || '').trim(); if (!v) return;
        if (this.backend === 'supabase') {                          // 로그인/가입 먼저
          const email = (this.authEmail || '').trim(), pw = this.authPw || '';
          if (!email || !pw) { this.authMsg = '이메일·비밀번호를 입력하세요'; return; }
          this.authMsg = '확인 중…';
          let r;
          try { r = await (await fetch('/auth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: this.authMode, email: email, password: pw }) })).json(); }
          catch (e) { this.authMsg = '네트워크 오류'; return; }
          if (!r.ok) { this.authMsg = r.error || '로그인 실패'; return; }
          this.authToken = r.access_token; this.authMsg = '';
          try { localStorage.setItem('prism_token', this.authToken); } catch (e) {}
        }
        this.reviewer = v;
        try { localStorage.setItem('prism_reviewer', v); localStorage.setItem('prism_reviewer_char', this.reviewerChar); } catch (e) {}
        // 검수자 등록(이름·캐릭터 + 팀). supabase 면 서버가 Bearer 의 uid 로 귀속(사칭 불가).
        const body = { reviewer: v, name: v, char: this.reviewerChar };
        if (this.backend === 'supabase') { body.team_mode = this.teamMode; body.team_name = this.teamName; body.invite_code = this.inviteCode; }
        try {
          const rr = await (await fetch('/reviewer', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify(body) })).json();
          if (rr && !rr.ok) { this.authMsg = rr.error || '등록 실패'; return; }
          if (rr && rr.team && rr.team.invite_code) { this.myInvite = rr.team.invite_code; }   // 초대코드 표시
        } catch (e) {}
        this.reviewerEditing = false;
        if (this.mod === 'arena') this.loadArena();
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
          if (this.mod === 'review') this.loadQueue();
          if (this.mod === 'arena') this.loadArena();             // 정확도 게이지 실시간 상승
          if (this.mod === 'dash' || this.mod === 'eval' || this.mod === 'home') this.loadDashThrottled();
        } else if (d.type === 'reap') {
          if (d.plan) this.liveToast('REAP 개선안 반영 · ' + (d.plan.length > 42 ? d.plan.slice(0, 42) + '…' : d.plan));
          if (this.mod === 'arena') this.loadArena();
          if (this.mod === 'prompt') this.loadPromptDefaults();   // 단계 프롬프트(LEARNED) 갱신
        } else if (d.type === 'reviewer') {
          if (this.mod === 'arena') this.loadArena();             // 다른 사람 캐릭터 변경 반영
        } else if (d.type === 'presence' && d.reviewer && d.reviewer !== this.reviewer) {
          if (d.action === 'viewing') this.liveSeen[d.hash] = d.reviewer; else delete this.liveSeen[d.hash];
        }
      },
      liveToast(msg) { this.liveMsg = msg; clearTimeout(this._lt); this._lt = setTimeout(() => { this.liveMsg = ''; }, 4200); },
      async loadQueue() { this.modBusy = true; try { this.queueData = await (await fetch('/queue' + (this.queueOnlyUnreviewed ? '' : '?all=1'))).json(); } catch (e) {} this.modBusy = false; },
      async loadArena() { try { this.arenaData = await (await fetch('/arena')).json(); } catch (e) {} },
      async loadAdmin() { try { this.adminData = await (await fetch('/admin', { headers: this._authHeaders() })).json(); } catch (e) {} },
      async runGolden() {
        this.goldenBusy = true; this.goldenResult = null;
        try { this.goldenResult = await (await fetch('/eval-golden', { method: 'POST', headers: this._authHeaders() })).json(); } catch (e) {}
        this.goldenBusy = false;
      },
      async runMetaCompile() {
        this.metaBusy = true;
        try { const r = await (await fetch('/meta-compile', { method: 'POST', headers: this._authHeaders() })).json(); this.metaResults = r.results || null; } catch (e) {}
        this.metaBusy = false; this.loadPromptDefaults();
      },
      async registerGolden(ev) {
        const f = ev.target.files && ev.target.files[0]; if (!f) return;
        this.goldenMsg = '등록 중…';
        const fd = new FormData(); fd.append('file', f);
        const h = {}; if (this.authToken) h['Authorization'] = 'Bearer ' + this.authToken;
        try { const r = await (await fetch('/golden', { method: 'POST', headers: h, body: fd })).json(); this.goldenMsg = r.ok ? ('✓ 등록됨 ' + r.count + '건') : (r.error || '실패'); this.loadAdmin(); }
        catch (e) { this.goldenMsg = '오류'; }
        ev.target.value = '';
      },
      async adminAct(action, member) {
        if (action === 'clear_feedback' && !confirm('우리 팀의 평가 피드백을 모두 삭제할까요?')) return;
        if (action === 'clear_contents' && !confirm('우리 팀의 검토 콘텐츠를 모두 삭제할까요?')) return;
        try { await fetch('/admin', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ action: action, member: member }) }); } catch (e) {}
        this.loadAdmin();
      },
      copyInvite() { try { navigator.clipboard.writeText((this.adminData && this.adminData.team && this.adminData.team.invite_code) || ''); this.inviteCopied = true; setTimeout(() => { this.inviteCopied = false; }, 1500); } catch (e) {} },
      inviteCopied: false,
      ingestEndpoint: '', ingestN: 20, ingestMsg: '', ingestBusy: false,
      async ingestRun() {
        if (!(this.ingestEndpoint || '').trim()) { this.ingestMsg = '크롤러 엔드포인트를 입력하세요'; return; }
        this.ingestBusy = true; this.ingestMsg = '인입·추출 중…';
        try { const r = await (await fetch('/admin', { method: 'POST', headers: this._authHeaders(), body: JSON.stringify({ action: 'ingest', endpoint: this.ingestEndpoint, n: this.ingestN }) })).json();
          this.ingestMsg = r.ok ? ('✓ ' + r.fetched + '건 인입 → 검수 큐 ' + r.queued + '건 적재') : (r.error || '실패'); } catch (e) { this.ingestMsg = '오류'; }
        this.ingestBusy = false;
      },
      // 결과 출처 필터(자동 인입/단건/배치)
      get srcOptions() { const s = new Set(((this.dashData && this.dashData.contents) || []).map((c) => c.source || '단건')); return [...s]; },
      get filteredContents() { const cs = (this.dashData && this.dashData.contents) || []; return this.srcFilter ? cs.filter((c) => (c.source || '단건') === this.srcFilter) : cs; },
      srcBadgeClass(s) { return s === '자동 인입' ? 'ds-badge--intent' : s === '배치' ? 'ds-badge--category' : 'ds-badge--neutral'; },
      // 아레나 파생값(게이지·내 순위)
      get arenaPct() { const d = this.arenaData; return d ? Math.round((d.accuracy || 0) * 100) : 0; },
      get arenaTargetPct() { const d = this.arenaData; return d ? Math.round((d.target || 0.9) * 100) : 90; },
      get arenaMe() { const d = this.arenaData; if (!d || !this.reviewer) return null; return (d.leaderboard || []).find((r) => r.reviewer === this.reviewer) || null; },
      get arenaMyRank() { const d = this.arenaData; if (!d || !this.reviewer) return 0; const i = (d.leaderboard || []).findIndex((r) => r.reviewer === this.reviewer); return i < 0 ? 0 : i + 1; },
      rankMedal(i) { return ['🥇', '🥈', '🥉'][i] || ('#' + (i + 1)); },
      // 캐릭터 육성: 레벨 → 성장 티어·타이틀·XP
      levelTier(L) { return L >= 10 ? 4 : L >= 7 ? 3 : L >= 4 ? 2 : L >= 2 ? 1 : 0; },
      levelTitle(L) { return ['새내기 검수자', '숙련 검수자', '베테랑 검수자', '검수 마스터', '전설의 검수자'][this.levelTier(L)]; },
      levelEmoji(L) { return ['🌱', '🔰', '⭐', '🏆', '👑'][this.levelTier(L)]; },
      xpPct(r) { return r ? (r.points % 100) : 0; },                 // 레벨당 100pt
      xpToNext(r) { return r ? (r.level * 100 - r.points) : 0; },
      async queueFeedback(it, verdict) {
        if (!this.ensureReviewer()) return;
        it.note = it.note || '';
        await this._postFb({ hash: it.hash, service: it.service, title: it.title, verdict: verdict, stage: 'review', note: it.note });
        it.reviewed = true; it.myVerdict = verdict;
        if (this.queueOnlyUnreviewed) this.queueData.items = (this.queueData.items || []).filter((x) => x.hash !== it.hash);
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
      // 자동 인입 상태(진행률) 폴링 — 실행 큐/자동 인입 뷰에서 사용
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
    --ctrl-h:42px; --ctrl-r:10px; --ctrl-px:12px;
  }
  [data-theme]{--ds-font-sans:var(--ds-font);--ds-font-body:var(--ds-font);--ds-font-display:var(--ds-font)}
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
  /* 내부 스크롤 영역을 컴포넌트로 구분 — 1px 라인 프레임(그라데이션 미사용) pre(코드)는 자체 테두리 있어 제외 */
  .panel div.overflow-auto{border:1px solid var(--ds-hairline);border-radius:10px;background:var(--ds-surface)}
  /* 텍스트 박스(A안) — 값·설명 텍스트를 옅은 테두리 박스로 통일 전체 텍스트가 잘림 없이, 문장부호 단위로 줄넘김 */
  .tbox{display:block;border:1px solid var(--ds-hairline);border-radius:8px;background:var(--ds-surface-white);
    padding:6px 9px;color:var(--ds-body);font-size:12.5px;line-height:1.5;word-break:keep-all;overflow-wrap:break-word}
  .tbox .nm{display:block;font-weight:600;color:var(--ds-ink);margin-bottom:1px}
  .ds-table td{vertical-align:top}
  /* 표 안에서는 박스 테두리 제거(행 구분선과 이중선으로 겹쳐 보임) → 옅은 배경만으로 값 구분 */
  .ds-table td .tbox{margin:0;border:0;background:var(--ds-surface);border-radius:7px;padding:6px 10px}
  /* 상단 자유배치 안내문도 텍스트 영역 — 박스로 감싸 일관화 */
  .hintbox{border:1px solid var(--ds-hairline);border-radius:10px;background:var(--ds-surface-white);
    padding:11px 15px;margin:0 0 2px;color:var(--ds-muted);font-size:12.5px;line-height:1.55;
    word-break:keep-all;overflow-wrap:break-word}
  /* 배치 결과 · 콘텐츠별 평가 피드백(학습 루프) */
  .fbrow{display:grid;grid-template-columns:1fr auto;gap:9px 12px;align-items:start;padding:11px 13px;
    border:1px solid var(--ds-hairline-soft);border-radius:12px;background:var(--ds-surface-white);margin-bottom:8px}
  .fbrow__main{min-width:0}
  .fbrow__title{display:flex;align-items:center;gap:7px;font-size:13px;font-weight:600;color:var(--ds-ink);flex-wrap:wrap}
  .fbrow__svc{font-size:11px;font-weight:500;color:var(--ds-muted)}
  .fbrow__sum{margin-top:6px;font-size:12px;color:var(--ds-body)}
  .fbrow__act{display:flex;gap:6px;align-self:center}
  .fbbtn{font-size:12px;font-weight:600;border:1px solid var(--ds-hairline);background:var(--ds-surface-white);
    color:var(--ds-muted);border-radius:8px;padding:5px 13px;cursor:pointer;white-space:nowrap}
  .fbbtn:hover{background:var(--ds-hairline-soft)}
  .fbbtn--good{border-color:var(--ds-primary);color:var(--ds-primary);background:var(--ds-primary-tint)}
  .fbbtn--bad{border-color:#ff4e33;color:#ff4e33;background:#ffece9}
  .fbrow__note{grid-column:1/-1;display:flex;gap:8px;align-items:center;flex-wrap:wrap;
    padding-top:9px;border-top:1px solid var(--ds-hairline-soft)}
  /* 프롬프트 스튜디오 · 단계별 모델 지정 */
  .stage-model{display:flex;align-items:center;gap:9px;margin-bottom:10px}
  .stage-model__lbl{flex:none;font-size:11px;font-weight:700;color:var(--ds-primary);background:var(--ds-primary-tint);
    border-radius:6px;padding:4px 9px}
  .stage-model .field{height:36px}
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
  .field:focus{outline:none;border-color:var(--ds-primary);box-shadow:0 0 0 3px rgba(30,132,255,.16);background:#fff}
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
  .dropzone:hover{border-color:var(--ds-primary);background:#fff}
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
  .copybtn:hover{color:var(--ds-ink);border-color:var(--ds-border-input-hover);background:var(--ds-surface)}
  .copybtn svg{width:12px;height:12px}

  /* 카드 · 패널 (라이트 + 절제된 입체) */
  .card{box-shadow:var(--ds-highlight);transition:transform .2s cubic-bezier(.32,.72,0,1),border-color .2s}
  .card:hover{transform:translateY(-1px)}
  /* .panel = 카탈로그 위젯 카드(ds-widget) 외형 상단 라이닝 금지(§4.4.2) → 헤드 구분선 없음 */
  .panel{border:1px solid var(--ds-hairline);border-radius:var(--ds-radius-xl);background:var(--ds-surface);overflow:hidden;
    box-shadow:0 1px 2px rgba(0,0,0,.05),0 8px 20px -12px rgba(0,0,0,.10),var(--ds-highlight);
    transition:transform .2s cubic-bezier(.32,.72,0,1),border-color .2s}
  .panel:hover{transform:translateY(-1px)}
  .panel-hd{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:15px 18px 0}
  .panel-hd b{color:var(--ds-ink);font-size:13px;font-weight:600;letter-spacing:.01em}
  .panel-hd .meta{font-size:12px;color:var(--ds-muted)}
  .panel-bd{padding:14px 18px 18px}
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

  /* 인포그래픽 — 타일·분포바·도넛 */
  .tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
  .tile{border-radius:12px;padding:13px 14px;background:var(--ds-surface-white);border:1px solid var(--ds-hairline-soft)}
  .tile .n{font-size:23px;font-weight:600;color:var(--ds-ink);line-height:1.1;letter-spacing:-.01em}
  .tile .t{margin-top:3px;font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--ds-muted)}
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
  /* 상단 바(로고|타이틀 고정) + 아래(사이드바|콘텐츠) — 타이틀을 로고 위치에 고정해 정렬 표준화 */
  .appshell{display:flex;flex-direction:column;max-width:1320px;margin:0 auto;height:100dvh;overflow:hidden}
  /* 상단 바·사이드바는 완전 고정 스크롤은 콘텐츠(.home) 내부에서만 → 톱바를 절대 침범하지 않음 */
  .topbar{flex:none;display:flex;align-items:stretch;gap:0;height:68px;margin:16px 22px 0;
    background:var(--ds-fixed-bg);border:1px solid var(--ds-hairline);border-radius:var(--ds-radius-xl);
    box-shadow:0 1px 2px rgba(0,0,0,.05),var(--ds-highlight);position:relative;z-index:30}
  /* 로고 영역 = 카드 왼쪽~구분선(폭 232 = 사이드바 폭), 콘텐츠 가운데 정렬 */
  .topbar__brand{width:232px;flex:none;display:flex;align-items:center;justify-content:center;gap:10px;border-right:1px solid var(--ds-hairline)}
  .topbar__brand .logo{width:32px;height:32px;display:flex;align-items:center;justify-content:center;flex:none}
  .topbar__brand .logo img{width:100%;height:100%}
  .topbar__wm{font-family:var(--ds-font-sans);font-weight:600;font-size:var(--ds-size-subtitle);color:var(--ds-ink);line-height:1.12}
  .topbar__wm small{display:block;font-weight:400;font-size:11px;color:var(--ds-muted)}
  /* 타이틀 = 아래 위젯 아이콘 라인(콘텐츠 패딩선)에 정렬: 구분선(232) + 37 */
  .topbar__title{flex:1;min-width:0;padding-left:37px;display:flex;flex-direction:column;justify-content:center;gap:3px}
  .topbar__title .homehead__title{font-size:15px;line-height:1.15}
  .topbar__title .homehead__sub{font-size:12.5px;line-height:1.2}
  .topbar__tools{flex:none;display:flex;align-items:center;gap:6px;position:relative;padding-right:14px}
  /* 연결 현황(다중) — 제공자별 칩 */
  .topbar__conn{display:inline-flex;align-items:center;gap:8px;border:0;background:none;cursor:pointer;padding:4px 6px;margin-right:4px;border-radius:8px}
  .topbar__conn:hover{background:var(--ds-hairline-soft)}
  .live-toast{position:fixed;top:18px;left:50%;transform:translateX(-50%);z-index:90;
    display:inline-flex;align-items:center;gap:8px;padding:9px 16px;border-radius:999px;
    background:var(--ds-ink);color:#fff;font-size:12.5px;font-weight:500;
    box-shadow:0 8px 24px rgba(0,0,0,.22)}
  .live-toast__dot{width:7px;height:7px;border-radius:50%;background:#18ba45;flex:none;
    box-shadow:0 0 0 3px rgba(61,220,151,.25)}
  /* ── 결과 출처 필터 ── */
  .srcfilter{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:11px}
  .srcfilter__chip{font-size:12px;font-weight:600;padding:5px 12px;border-radius:999px;cursor:pointer;
    border:1px solid var(--ds-hairline,rgba(0,0,0,0.08));background:var(--ds-surface2,#fff);color:var(--ds-body,rgba(0,0,0,0.88))}
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
  .onboard__card{width:100%;max-width:440px;background:var(--ds-surface2,#fff);border-radius:24px;
    padding:30px 30px 24px;box-shadow:0 24px 70px rgba(0,0,0,.4);text-align:center}
  .onboard__brand{display:flex;align-items:center;justify-content:center;gap:8px;color:var(--ds-violet,#1e84ff);
    font-size:13px;font-weight:700;margin-bottom:14px}
  .onboard__brand img{width:26px;height:26px}
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
  .onboard__authtabs button.sel{background:var(--ds-surface2,#fff);color:var(--ds-ink);box-shadow:0 1px 3px rgba(0,0,0,.08)}
  .onboard__authmsg{text-align:left;font-size:12px;color:#ff4e33;margin:-6px 0 12px}
  .onboard__cta{height:46px;width:100%;font-size:15px;font-weight:700}
  .onboard__cta:disabled{opacity:.45;cursor:not-allowed}
  .onboard__skip{margin-top:10px;background:none;border:0;color:var(--ds-muted);font-size:12.5px;cursor:pointer}
  /* ── 검수자 등록: 캐릭터 선택(구 약식, 미사용 호환) ── */
  .charpick{display:grid;grid-template-columns:repeat(4,1fr);gap:7px}
  .charpick__opt{display:flex;flex-direction:column;align-items:center;gap:3px;padding:8px 2px;border-radius:12px;
    border:1.5px solid var(--ds-hairline,rgba(0,0,0,0.08));background:var(--ds-surface2,#fff);cursor:pointer;transition:all .15s}
  .charpick__opt:hover{border-color:var(--ds-violet,#1e84ff)}
  .charpick__opt img{width:34px;height:34px}
  .charpick__opt span{font-size:10.5px;color:var(--ds-muted);font-weight:600}
  .charpick__opt.sel{border-color:var(--ds-violet,#1e84ff);background:var(--ds-violet-tint,rgba(30,132,255,0.16));box-shadow:0 0 0 2px var(--ds-violet-tint,rgba(30,132,255,0.16))}
  .charpick__opt.sel span{color:var(--ds-violet,#1e84ff)}
  .evaltabs{display:flex;gap:6px;background:var(--ds-hairline-soft,rgba(0,0,0,.04));padding:4px;border-radius:12px;max-width:520px}
  .evaltabs button{flex:1;height:36px;border:0;background:none;border-radius:8px;font-size:13px;font-weight:700;color:var(--ds-muted);cursor:pointer}
  .evaltabs button.sel{background:var(--ds-surface2,#fff);color:var(--ds-ink);box-shadow:0 1px 3px rgba(0,0,0,.08)}
  .goldgrid{display:flex;align-items:center;gap:24px;margin-top:16px;flex-wrap:wrap}
  .goldbig__v{font-size:42px;font-weight:800;color:var(--ds-violet,#1e84ff);line-height:1}
  .goldbig__l{font-size:11px;color:var(--ds-muted);margin-top:3px}
  .goldstat{display:flex;flex-direction:column} .goldstat b{font-size:20px;font-weight:800;color:var(--ds-ink)} .goldstat span{font-size:10.5px;color:var(--ds-muted)}
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
  .arena-quest{cursor:pointer;font-weight:600;background:rgba(255,255,255,.16);padding:5px 12px;border-radius:999px}
  .arena-quest:hover{background:rgba(255,255,255,.26)}
  .arena-quest--done{cursor:default;background:rgba(255,255,255,.1)}
  .arena-cols{display:grid;grid-template-columns:1.3fr 1fr;gap:16px}
  @media (max-width:820px){.arena-cols{grid-template-columns:1fr}}
  .lb-row{display:flex;align-items:center;gap:10px;padding:9px 6px;border-bottom:1px solid var(--ds-hairline-soft)}
  .lb-row--me{background:var(--ds-violet-tint,rgba(30,132,255,0.16));border-radius:9px;border-bottom-color:transparent}
  .lb-rank{width:30px;text-align:center;font-weight:700;font-size:14px}
  .lb-name{flex:1;min-width:0;font-weight:600;color:var(--ds-ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .lb-streak{font-size:12px;color:var(--ds-muted)} .lb-pts{font-weight:700;color:var(--ds-violet,#1e84ff)}
  .lb-title{display:block;font-size:10px;color:var(--ds-muted);font-weight:500;margin-top:1px}
  .lb-av{width:30px;height:30px;border-radius:50%;flex:none;display:flex;align-items:center;justify-content:center;
    background:var(--ds-surface2,#fff);box-shadow:0 0 0 2px var(--tier-c,rgba(0,0,0,.12));overflow:hidden}
  .lb-av img{width:24px;height:24px}
  /* ── 검수 캐릭터 육성 카드 ── */
  .charcard{text-align:center;padding:6px 4px 4px;--tier-c:rgba(0,0,0,.24)}
  .charcard[data-tier="1"]{--tier-c:#1e84ff} .charcard[data-tier="2"]{--tier-c:#5c77ff}
  .charcard[data-tier="3"]{--tier-c:#ff9429} .charcard[data-tier="4"]{--tier-c:#a05cff}
  .lb-av[data-tier="1"]{--tier-c:#1e84ff} .lb-av[data-tier="2"]{--tier-c:#5c77ff}
  .lb-av[data-tier="3"]{--tier-c:#ff9429} .lb-av[data-tier="4"]{--tier-c:#a05cff}
  .charcard__avatar{position:relative;width:96px;height:96px;margin:6px auto 4px;display:flex;align-items:center;justify-content:center}
  .charcard__avatar img{position:relative;z-index:1;width:64px;height:64px;
    /* 티어가 오를수록 캐릭터가 커지고(육성) 살짝 떠오름 */
    transform:scale(calc(1 + var(--tier,0)*0.11));transition:transform .5s cubic-bezier(.22,1.4,.36,1);
    filter:drop-shadow(0 4px 8px rgba(19,52,59,.2))}
  .charcard[data-tier="0"]{--tier:0} .charcard[data-tier="1"]{--tier:1} .charcard[data-tier="2"]{--tier:2}
  .charcard[data-tier="3"]{--tier:3} .charcard[data-tier="4"]{--tier:4}
  .charcard__glow{position:absolute;inset:0;border-radius:50%;
    background:radial-gradient(circle,color-mix(in srgb,var(--tier-c) 38%,transparent),transparent 68%);
    box-shadow:0 0 0 3px color-mix(in srgb,var(--tier-c) 55%,transparent);opacity:calc(.35 + var(--tier,0)*0.16)}
  .charcard[data-tier="4"] .charcard__glow{animation:charpulse 1.8s ease-in-out infinite}
  @keyframes charpulse{0%,100%{transform:scale(1);opacity:.7}50%{transform:scale(1.08);opacity:1}}
  .charcard__lvl{position:absolute;z-index:2;bottom:-2px;right:6px;background:var(--tier-c);color:#fff;
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
  .side-assistant__t{display:flex;align-items:center;gap:7px;font-family:var(--ds-font-sans);font-size:var(--ds-size-label);font-weight:600;color:var(--ds-ink)}
  .side-assistant__s{font-size:11px;color:var(--ds-muted)}
  .side-assistant__spin{width:12px;height:12px;border:2px solid rgba(0,0,0,.14);border-top-color:var(--ds-primary);border-radius:50%;animation:ds-spin .7s linear infinite;flex:none}
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
  .homehead__title{font-family:var(--ds-font-sans);font-size:var(--ds-size-label);font-weight:700;letter-spacing:-.01em;line-height:1.2;color:var(--ds-ink)}
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

  /* 콘텐츠 영역(모듈/홈 그리드 공용) — 타이틀바와 충분히 띄움 */
  .canvas{margin-top:0;min-width:0;margin-left:0;padding-left:0}
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

  /* ════ 공통 규칙 — 위젯 종류 무관 동일 타이포·정렬 기준(단일 소스) ════ */
  /* ① 타이틀: 모든 위젯/패널/타이틀바 14px·600 동일 */
  .ds-widget__title,.homehead__title{font-size:var(--ds-size-label);font-weight:600;line-height:1.3;color:var(--ds-ink);letter-spacing:-.005em}
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
  .canvas .space-y-4>:not([hidden])~:not([hidden]){margin-top:26px}
  .ds-widgetgrid{gap:20px}
  /* ⑧ 위젯 헤드 캐릭터 아바타(귀엽게) — 라운드, 투명배경 캐릭터 */
  .wz-char{width:30px;height:30px;border-radius:50%;background:var(--ds-hairline-soft);overflow:hidden;display:inline-flex;align-items:center;justify-content:center;flex:none}
  .wz-char img{width:88%;height:88%;object-fit:contain}
  .wz-char--sm{width:24px;height:24px}
  /* ⑨ 패널 내부 좌측 정렬선 통일(18px) — 타이틀·본문·표·행이 한 선에 정렬
     패널 직속 표(셀패딩 12)·행(0)은 타이틀(18)보다 왼쪽이라 18로 보정 panel-bd 안은 이미 18. */
  .panel > .drow{padding-left:18px;padding-right:18px}
  .panel > .overflow-auto > .ds-table tr > :first-child,
  .panel > .ds-table tr > :first-child{padding-left:18px}
  .panel > .overflow-auto > .ds-table tr > :last-child,
  .panel > .ds-table tr > :last-child{padding-right:18px}
</style>
</head>
<body class="antialiased">
<div class="ds-grain" aria-hidden="true"></div>
<div x-data="prismApp()" class="appshell" data-theme="light">

  <!-- ━━━━━ 상단 바: 로고 | 타이틀(고정) | 도구 ━━━━━ -->
  <header class="topbar">
    <div class="topbar__brand"><span class="logo"><img src="/vendor/prism-mark.svg" alt="Prism"></span><div class="topbar__wm">Prism<small>A lens on content &amp; users</small></div></div>
    <div class="topbar__title">
      <div class="homehead__title" x-text="modLabel"></div>
      <div class="homehead__sub" x-text="modSub"></div>
    </div>
    <div class="topbar__tools">
      <!-- 검수자 칩: 클릭하면 정식 등록 모달(온보딩). 선택한 캐릭터를 미리보기 -->
      <button type="button" class="topbar__conn" x-on:click="reviewerEditing = true" data-tip="검수자 등록" data-tip-pos="bottom" aria-label="검수자">
        <span class="conn-chip" x-bind:class="reviewer ? 'conn-chip--on' : 'conn-chip--off'">
          <img x-show="reviewer" x-bind:src="charImg(reviewerChar)" alt="" style="width:18px;height:18px;margin:-2px 0">
          <span x-text="reviewer || '검수자 등록'"></span>
        </span>
      </button>
      <!-- 연결 현황(다중): 제공자별 연결 상태를 모두 표시 -->
      <button type="button" class="topbar__conn" x-on:click="if (!cfg.keyManagedByServer) settingsOpen = true" x-bind:data-tip="cfg.keyManagedByServer ? '연결 현황(서버 관리)' : '키 설정'" data-tip-pos="bottom" aria-label="연결 현황">
        <span x-show="cfg.forcedMock" class="conn-chip conn-chip--off"><span class="ds-statusdot ds-statusdot--mock"><span class="ds-statusdot__dot"></span></span>MOCK(강제)</span>
        <template x-if="!cfg.forcedMock && connCount === 0"><span class="conn-chip conn-chip--off"><span class="ds-statusdot ds-statusdot--mock"><span class="ds-statusdot__dot"></span></span>키 미설정</span></template>
        <template x-for="c in connList" x-bind:key="c.id">
          <span x-show="!cfg.forcedMock && (c.on || connCount === 0 ? c.on : false)" class="conn-chip conn-chip--on">
            <span class="ds-statusdot ds-statusdot--ok"><span class="ds-statusdot__dot"></span></span><span x-text="c.label"></span>
          </span>
        </template>
      </button>
      <a href="/report" target="_blank" rel="noreferrer" class="ds-iconbtn ds-iconbtn--bordered" data-tip="전체 리포트 생성·보기" data-tip-pos="bottom" aria-label="전체 리포트"><svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M6 3h8l4 4v14H6z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M9 12h6M9 16h6M9 8h3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></a>
      <button type="button" x-show="mod === 'home'" x-on:click.stop="addMenuOpen = !addMenuOpen" class="ds-iconbtn ds-iconbtn--bordered" data-tip="위젯 추가" data-tip-pos="bottom" aria-label="위젯 추가"><svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg></button>
      <button type="button" x-show="mod === 'home'" x-on:click="editing = !editing" x-bind:class="editing ? 'ds-iconbtn ds-iconbtn--bordered ds-iconbtn--active' : 'ds-iconbtn ds-iconbtn--bordered'" x-bind:data-tip="editing ? '편집 완료' : '편집'" data-tip-pos="bottom" aria-label="편집"><svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M4 20h4L19 9l-4-4L4 16v4Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg></button>
      <button type="button" x-show="!cfg.keyManagedByServer" class="ds-iconbtn ds-iconbtn--bordered" x-on:click="settingsOpen = true" data-tip="설정" data-tip-pos="bottom" aria-label="설정"><svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" stroke="currentColor" stroke-width="1.5"/><path d="M19.4 13a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V13Z" stroke="currentColor" stroke-width="1.3"/></svg></button>
      <button type="button" class="ds-iconbtn ds-iconbtn--bordered" x-on:click="toggleTheme()" x-bind:data-tip="theme === 'dark' ? '라이트 모드' : '다크 모드'" data-tip-pos="bottom" aria-label="테마 전환">
        <svg x-show="theme !== 'dark'" width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>
        <svg x-show="theme === 'dark'" x-cloak width="18" height="18" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="4" stroke="currentColor" stroke-width="1.6"/><path d="M12 3v2M12 19v2M5 12H3M21 12h-2M6 6l1.4 1.4M16.6 16.6 18 18M18 6l-1.4 1.4M7.4 16.6 6 18" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
      </button>
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

  <!-- 검수자 등록 온보딩(딤드 + 중앙 모달). 첫 방문 시 자동, 칩 클릭 시 변경 -->
  <div class="onboard" x-show="reviewerEditing" x-cloak x-transition.opacity
       x-on:click.self="if (reviewer) reviewerEditing = false"
       x-on:keydown.escape.window="if (reviewer) reviewerEditing = false">
    <div class="onboard__card" x-transition>
      <div class="onboard__brand"><img src="/vendor/prism-mark.svg" alt="Prism"><b>Prism 평가 아레나</b></div>
      <h2 class="onboard__title" x-text="reviewer ? '검수자 정보 변경' : (backend==='supabase' ? (authMode==='signup'?'가입하고 시작':'로그인') : '검수자로 등록하기')"></h2>
      <p class="onboard__lead">팀이 함께 콘텐츠를 검수해 정확도를 끌어올립니다. 내 검수가 점수가 되고
        캐릭터가 성장해요. <span x-show="backend==='supabase'">계정으로 로그인하면 <b>어느 기기에서나</b> 이어집니다.</span></p>

      <!-- supabase 모드: 이메일+비밀번호 로그인/가입 -->
      <template x-if="backend === 'supabase'">
        <div>
          <div class="onboard__authtabs">
            <button type="button" x-bind:class="authMode==='login'?'sel':''" x-on:click="authMode='login';authMsg=''">로그인</button>
            <button type="button" x-bind:class="authMode==='signup'?'sel':''" x-on:click="authMode='signup';authMsg=''">가입</button>
          </div>
          <label class="onboard__lbl">이메일</label>
          <input class="field onboard__name" type="email" placeholder="you@team.com" x-model="authEmail" style="margin-bottom:12px">
          <label class="onboard__lbl">비밀번호</label>
          <input class="field onboard__name" type="password" placeholder="••••••••" x-model="authPw"
                 x-on:keydown.enter="saveReviewer()" style="margin-bottom:12px">
          <div class="onboard__authmsg" x-show="authMsg" x-text="authMsg"></div>
        </div>
      </template>

      <label class="onboard__lbl">닉네임 <span class="onboard__hint">— 리더보드·검수에 표시됩니다</span></label>
      <input class="field onboard__name" placeholder="예) 김검수" x-model="reviewer"
             x-on:keydown.enter="saveReviewer()" autofocus>

      <label class="onboard__lbl">캐릭터 선택 <span class="onboard__hint">— 리더보드·아레나에 이 캐릭터로 표시됩니다</span></label>
      <div class="onboard__chars">
        <template x-for="c in charOptions" x-bind:key="c.id">
          <button type="button" class="ochar" x-bind:class="reviewerChar===c.id ? 'sel' : ''" x-on:click="reviewerChar=c.id">
            <span class="ochar__ring"><img x-bind:src="c.img" x-bind:alt="c.label"></span>
            <b x-text="c.label"></b><small x-text="c.role"></small>
          </button>
        </template>
      </div>

      <!-- supabase 모드: 팀 생성/가입 -->
      <template x-if="backend === 'supabase'">
        <div>
          <label class="onboard__lbl">팀</label>
          <div class="onboard__authtabs">
            <button type="button" x-bind:class="teamMode==='create'?'sel':''" x-on:click="teamMode='create'">새 팀 만들기</button>
            <button type="button" x-bind:class="teamMode==='join'?'sel':''" x-on:click="teamMode='join'">팀 참가</button>
          </div>
          <input x-show="teamMode==='create'" class="field onboard__name" placeholder="팀 이름 — 예) 콘텐츠검수팀" x-model="teamName" style="margin-bottom:6px">
          <input x-show="teamMode==='join'" class="field onboard__name" placeholder="초대 코드 — 예) A1B2C3D4" x-model="inviteCode" style="margin-bottom:6px;text-transform:uppercase">
          <p class="onboard__hint" style="text-align:left;display:block;margin-bottom:4px" x-text="teamMode==='create' ? '만들면 초대 코드가 생겨 팀원을 부를 수 있어요' : '관리자에게 받은 코드를 입력하세요'"></p>
        </div>
      </template>

      <button type="button" class="ds-btn ds-btn--primary onboard__cta"
              x-bind:disabled="!(reviewer||'').trim() || (backend==='supabase' && (!(authEmail||'').trim() || !authPw || (teamMode==='create' ? !(teamName||'').trim() : !(inviteCode||'').trim())))"
              x-on:click="saveReviewer()"
              x-text="backend==='supabase' ? (authMode==='signup'?'가입하고 시작':'로그인하고 시작') : (reviewer ? '저장하고 시작' : '시작하기')"></button>
      <button type="button" class="onboard__skip" x-show="reviewer" x-on:click="reviewerEditing=false">닫기</button>
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
        <nav class="ds-navgroup" x-show="grp.g !== '팀' || backend === 'supabase'">
          <div class="ds-navgroup__label" x-text="grp.g"></div>
          <template x-for="it in grp.items" x-bind:key="it.id">
            <button type="button" class="ds-navitem" x-show="!it.cond || (it.cond === 'admin' ? (backend !== 'supabase' || (adminData && adminData.isAdmin)) : backend === it.cond)" x-bind:class="mod === it.id ? 'ds-navitem--active' : ''" x-on:click="selectMod(it.id)">
              <span class="ds-navitem__icon" x-html="navIcons[it.ic]"></span>
              <span x-text="it.label"></span>
            </button>
          </template>
        </nav>
      </template>
    </aside>
    <!-- 도우미 = 사이드 위젯 바로 아래(다크 박스 + 캐릭터) -->
    <div class="side-assistant">
      <button type="button" class="side-assistant__card" x-on:click="chatOpen = !chatOpen" aria-label="도우미 열기">
        <span class="side-assistant__char"><img src="/vendor/boksil-catcher.svg" alt=""></span>
        <span class="side-assistant__txt">
          <span class="side-assistant__t">
            <span class="side-assistant__spin" x-show="loading || modBusy"></span>
            <span class="side-assistant__idle" x-show="!(loading || modBusy)"></span>
            <span x-text="(loading || modBusy) ? '작업 중' : '도우미'"></span>
          </span>
          <span class="side-assistant__s" x-text="(loading || modBusy) ? '처리하고 있어요…' : ((dashData && dashData.n) ? ('처리 ' + dashData.n + '건 · 유통 ' + dashData.gPct + '%') : '말로 작업을 지시하세요')"></span>
        </span>
      </button>
    </div>
  </div>

  <!-- ━━━━━ 우측 = 상단 메뉴 위젯 + 캔버스 ━━━━━ -->
  <div class="home">
    <div class="canvas">
      <!-- ═══ 홈: 위젯 캔버스(실동작 위젯만 · 직접 배치) ═══ -->
      <div x-show="mod === 'home'" class="ds-widgetgrid" x-bind:class="editing ? 'ds-widgetgrid--edit' : ''" id="grid">
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

        <!-- 핵심 지표 (정보 · md) — /dashboard 집계 -->
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

        <!-- 처리 프로세스 (정보 · tall) — 파이프라인 4단계 -->
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

      <!-- ═══ 모듈: 자동 인입(파이프라인 소스 설정) — 관리자 전용 ═══ -->
      <div x-show="mod === 'auto'" x-cloak class="w-full space-y-4">
        <div x-show="backend === 'supabase' && !(adminData && adminData.isAdmin)" class="ds-hint hintbox">자동 인입 파이프라인 설정은 <b class="text-ink">팀 관리자</b>만 가능합니다. 일회성 처리는 <b class="text-ink">수동 추출</b>을 사용하세요.</div>
        <template x-if="backend !== 'supabase' || (adminData && adminData.isAdmin)">
        <div class="w-full space-y-4">
        <p class="ds-hint hintbox">콘텐츠를 <b class="text-ink">자동으로 인입</b>하는 파이프라인 소스를 설정합니다 등록·활성화한 소스로 들어온 콘텐츠가 추출 → 분석 → 검수 → 판정을 자동으로 거칩니다 일회성 처리는 <b class="text-ink">수동 추출</b>을 사용하세요</p>
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
            <p x-show="ingestSources.length" class="text-xs text-muted" style="margin-bottom:11px"><b class="text-ink">활성</b> 소스는 <b class="text-ink">폴링(초)</b> 주기마다 백그라운드로 자동 인입되고, <b class="text-ink">실행 큐</b>에 진행률이 표시됩니다 즉시 한 번만 받으려면 <b class="text-ink">지금 인입</b>, 멈추려면 <b class="text-ink">중지</b></p>
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
      <div x-show="mod === 'run'" class="w-full space-y-4">
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
              class="btn-primary inline-flex items-center gap-2 rounded-lg bg-violet px-5 py-2.5 text-sm font-medium text-ink disabled:opacity-50">
              <svg x-show="loading" x-cloak class="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z"/></svg>
              <span x-text="loading ? '실행 중' : '추출 실행'"></span>
            </button>
            <span class="text-xs text-muted">모델·추론 강도는 상단 ⚙ 설정에서 변경</span>
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
                    <tr>
                      <td class="text-ink" x-text="it.title || '—'"></td>
                      <td x-text="it.summary || '—'"></td>
                      <td><div class="flex flex-wrap gap-1"><template x-for="e in (it.entities || [])" x-bind:key="e"><span class="ds-badge ds-badge--entity" x-text="e"></span></template><span x-show="!(it.entities||[]).length">—</span></div></td>
                      <td><span class="ds-badge ds-badge--neutral" x-bind:class="it.grade === 'G' ? 'ds-badge--success' : 'ds-badge--error'"><span class="ds-badge__dot"></span><span x-text="it.grade || '—'"></span></span></td>
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
                  <p class="flex-1 text-[15px] leading-relaxed text-ink" x-text="im.summary || '(빈 값 — 차단되었거나 본문 부족)'"></p>
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
                <span x-show="!(im.entities || []).length" class="text-xs text-muted">—</span>
              </div>
            </div>
            <div class="drow">
              <div class="k">인텐트</div>
              <div class="v flex flex-wrap gap-1.5">
                <template x-for="x in (im.intent || [])" x-bind:key="x"><span class="ds-badge ds-badge--intent" x-text="x"></span></template>
                <span x-show="!(im.intent || []).length" class="text-xs text-muted">—</span>
              </div>
            </div>
            <div class="drow">
              <div class="k">콘텐츠 카테고리</div>
              <div class="v flex flex-wrap gap-1.5">
                <template x-for="x in contentCats" x-bind:key="x"><span class="ds-badge ds-badge--category" x-text="x"></span></template>
                <span x-show="!contentCats.length" class="text-xs text-muted">—</span>
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
                    <span x-text="s.vision || '—'"></span>
                  </div>
                  <div class="flex gap-2 text-sm text-body">
                    <svg class="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7V5h16v2M9 5v14m-3 0h6"/></svg>
                    <span x-text="s.ocr || '—'"></span>
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
      </div>

      <!-- ═══ 모듈: 대시보드 (디자인 시스템: Stat · ProgressRing · ProgressBar) ═══ -->
      <div x-show="mod === 'dash'" x-cloak class="ds-pilot w-full">
        <div x-show="!dashData || !dashData.n" class="ds-empty">
          <span class="ds-character ds-character--bob" style="width:80px;height:80px"><img src="/vendor/boksil-catcher.svg" alt=""></span>
          <div class="ds-empty__title">아직 집계할 결과가 없어요</div>
          <div class="ds-empty__desc"><b>추출 실행</b>에서 추출(엑셀 일괄 권장)을 먼저 실행하세요</div>
        </div>
        <div x-show="dashData && dashData.n" class="space-y-4">
          <section class="panel"><div class="panel-hd"><b>집계</b><span class="meta tnum" x-text="(dashData?dashData.n:0) + '건'"></span><button type="button" class="copybtn" x-on:click="exportDash()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>엑셀 다운로드</button></div>
            <div class="panel-bd"><div class="ds-stat-grid">
              <div class="ds-stat"><div class="ds-stat__value ds-stat__value--accent" x-text="(dashData?dashData.gPct:0) + '%'"></div><div class="ds-stat__label">유통 가능 G</div></div>
              <div class="ds-stat"><div class="ds-stat__value" x-text="dashData?dashData.n:0"></div><div class="ds-stat__label">처리 건수</div></div>
              <div class="ds-stat"><div class="ds-stat__value" x-text="dashData?dashData.entities:0"></div><div class="ds-stat__label">엔티티 수</div></div>
              <div class="ds-stat"><div class="ds-stat__value" x-text="dashData?dashData.avgLead:0"></div><div class="ds-stat__label">평균 리드문(자)</div></div>
            </div></div>
          </section>
          <section class="panel"><div class="panel-hd"><b>유통 판정 · 인텐트 분포</b></div>
            <div class="panel-bd"><div style="display:flex;align-items:center;gap:24px">
              <div class="ds-ring" x-bind:style="'--ds-ring-size:120px;--ds-ring-pct:'+(dashData?dashData.gPct:0)"><span class="ds-ring__label" x-text="(dashData?dashData.gPct:0)+'%'"></span></div>
              <div style="min-width:0;flex:1">
                <template x-for="it in (dashData?dashData.intents:[])" x-bind:key="it.k">
                  <div class="ds-progress" style="margin:7px 0"><div class="ds-progress__head"><span class="ds-progress__label" x-text="it.k"></span><span class="ds-progress__pct" x-text="it.v"></span></div><div class="ds-progress__track"><div class="ds-progress__fill ds-progress__fill--primary" x-bind:style="'width:'+Math.max(it.pct,4)+'%'"></div></div></div>
                </template>
              </div>
            </div></div>
          </section>
          <section class="panel"><div class="panel-hd"><b>콘텐츠 카테고리 분포 · 상위</b></div>
            <div class="panel-bd">
              <template x-for="it in (dashData?dashData.categories:[])" x-bind:key="it.k">
                <div class="ds-progress" style="margin:7px 0"><div class="ds-progress__head"><span class="ds-progress__label" x-text="it.k"></span><span class="ds-progress__pct" x-text="it.v"></span></div><div class="ds-progress__track"><div class="ds-progress__fill ds-progress__fill--primary" x-bind:style="'width:'+Math.max(it.pct,4)+'%'"></div></div></div>
              </template>
              <p x-show="dashData && dashData.qualityReasons && dashData.qualityReasons.length" class="ds-hint" style="margin-top:12px">품질 사유 상위: <span x-text="(dashData?dashData.qualityReasons:[]).map(x=>x.k+'('+x.v+')').join(' · ')"></span></p>
            </div>
          </section>
          <!-- 콘텐츠별 평가 피드백 → 학습 루프(다음 추출 프롬프트에 자동 반영) -->
        </div>
      </div>

      <!-- ═══ 모듈: 품질 메타 ═══ -->
      <div x-show="mod === 'quality'" x-cloak class="w-full space-y-4">
        <div class="panel"><div class="panel-bd flex items-center justify-between gap-3">
          <div class="text-xs text-muted">법령 1차 필터(13종 위반 라우팅·스코어링)를 추출에 포함합니다 켜면 다음 추출부터 적용(추가 호출)</div>
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
      <div x-show="mod === 'quality'" x-cloak class="w-full space-y-4">
        <div x-show="!topicData || !topicData.n_contents" class="empty">아직 토픽을 만들 결과가 없습니다 <b class="text-body">실행 · 추출</b>에서 여러 건(엑셀 일괄)을 추출하세요</div>
        <div x-show="topicData && topicData.n_contents" class="space-y-4">
          <div class="tiles" style="grid-template-columns:repeat(3,1fr)">
            <div class="tile"><div class="n tnum" x-text="topicData?(topicData.summary.single||0):0"></div><div class="t">엔티티형</div></div>
            <div class="tile"><div class="n tnum" x-text="topicData?(topicData.summary.composite||0):0"></div><div class="t">사건형</div></div>
            <div class="tile"><div class="n tnum" x-text="topicData?(topicData.summary.filter||0):0"></div><div class="t">조건형</div></div>
          </div>
          <div class="panel"><div class="panel-hd"><b>엔티티형 · 사건형 토픽</b><span class="meta tnum" x-text="topicData ? (topicData.n_contents + '건 기준') : ''"></span><button type="button" class="copybtn" x-on:click="exportTopics()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>엑셀 다운로드</button></div>
            <div class="overflow-auto"><table class="ds-table"><thead><tr><th>유형</th><th>클러스터</th><th>대표 엔티티</th><th>멤버</th></tr></thead><tbody>
              <template x-for="t in (topicData?topicData.single:[])" x-bind:key="t.cluster_id"><tr><td>엔티티형</td><td class="text-ink" x-text="t.cluster_id"></td><td x-text="(t.entities||t.rep_entities||[]).join(' · ')"></td><td x-text="t.n_contents || (t.contents?t.contents.length:'')"></td></tr></template>
              <template x-for="t in (topicData?topicData.composite:[])" x-bind:key="t.cluster_id"><tr><td>사건형</td><td class="text-ink" x-text="t.cluster_id"></td><td x-text="(t.rep_entities||t.entities||[]).join(' · ')"></td><td x-text="t.n_contents || (t.contents?t.contents.length:'')"></td></tr></template>
              <template x-if="!(topicData&&(topicData.single.length||topicData.composite.length))"><tr><td colspan="4" class="text-muted">엔티티 공유 클러스터 없음(데이터가 많을수록 형성)</td></tr></template>
            </tbody></table></div>
          </div>
          <div class="panel"><div class="panel-hd"><b>조건형 토픽</b><span class="meta">관심사 × 소비 방식</span></div><div class="panel-bd flex flex-wrap gap-1.5">
            <template x-for="t in (topicData?topicData.filter:[])" x-bind:key="t.cluster_id"><span class="ds-badge ds-badge--neutral" x-bind:class="t.active ? 'ds-badge--entity' : 'ds-badge--category'" x-text="(t.name||t.label) + (t.active?(' · '+(t.n_contents||'')):'')"></span></template>
          </div></div>
        </div>
      </div>

      <!-- ═══ 모듈: 사전 · 매핑 ═══ -->
      <div x-show="mod === 'dict'" x-cloak class="w-full space-y-4">
        <div class="panel"><div class="panel-bd flex items-center justify-between gap-3">
          <div class="text-xs text-muted">각 체계의 정책(사전·카테고리·법령)을 <span class="text-body">직접 수정</span>할 수 있습니다 저장 시 즉시 추출에 반영되고 로컬에 영속됩니다</div>
          <button type="button" x-on:click="resetDict()" class="rounded-md border border-[#ff4e33]/30 px-3 py-1.5 text-xs font-medium text-[#ff4e33] hover:bg-[#ff4e33]/10">편집 초기화</button>
        </div></div>

        <!-- 편집은 팝업(편집 다이얼로그)에서 — 화면 하단 정의 -->

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
            <div class="panel-bd space-y-2.5" style="max-height:340px;overflow:auto">
              <template x-for="c in (dictData?dictData.iabTier1:[])" x-bind:key="c">
                <div>
                  <div class="flex items-center gap-2 mb-1">
                    <div class="text-[13px] font-semibold text-ink" x-text="c"></div>
                    <button type="button" class="text-[11px] text-muted hover:text-ink" x-on:click="startEdit('tier2', c, (dictData.tier2[c]||[]), 'list', 'Tier2 · ' + c)">편집</button>
                  </div>
                  <div class="flex flex-wrap gap-1.5">
                    <template x-for="t2 in (dictData && dictData.tier2[c] ? dictData.tier2[c] : [])" x-bind:key="t2"><span class="ds-badge ds-badge--category" x-text="t2"></span></template>
                  </div>
                </div>
              </template>
            </div>
          </div>
          <div class="grid grid-cols-2 gap-4">
            <div class="panel"><div class="panel-hd"><b>도메인 그룹</b><span class="meta">Tier1 7묶음</span></div><div class="panel-bd space-y-2">
              <template x-for="(ts,g) in (dictData?dictData.domainGroups:{})" x-bind:key="g">
                <div class="flex items-center gap-2"><span class="ds-badge ds-badge--entity" x-text="g"></span> <span class="text-xs text-muted" style="flex:1;min-width:0" x-text="ts.join(' · ')"></span>
                  <button type="button" class="text-[11px] text-muted hover:text-ink" x-on:click="startEdit('domain_groups', g, ts, 'list', '도메인 그룹 · ' + g)">편집</button></div>
              </template>
            </div></div>
            <div class="panel"><div class="panel-hd"><b>자사 ↔ IAB v3.0 매핑</b><span class="meta tnum" x-text="dictData?Object.keys(dictData.iabMap).length+'건':''"></span></div>
              <div class="overflow-auto" style="max-height:280px"><table class="ds-table"><thead><tr><th>자사 경로</th><th>IAB 공식</th><th></th></tr></thead><tbody>
                <template x-for="(v,k) in (dictData?dictData.iabMap:{})" x-bind:key="k"><tr><td class="text-ink" x-text="k"></td><td><div class="tbox" x-text="v"></div></td>
                  <td><button type="button" class="text-[11px] text-muted hover:text-ink" x-on:click="startEdit('category_iab_map', k, v, 'text', '매핑 · ' + k)">편집</button></td></tr></template>
              </tbody></table></div></div>
          </div>
          <div class="grid grid-cols-2 gap-4">
            <div class="panel"><div class="panel-hd"><b>품질 메타</b><span class="meta tnum" x-text="dictData?Object.keys(dictData.qualityMetas).length+'종':''"></span></div>
              <div class="overflow-auto" style="max-height:280px"><table class="ds-table"><thead><tr><th>ID</th><th>메타명 · 정의</th><th>적용</th><th></th></tr></thead><tbody>
                <template x-for="(v,k) in (dictData?dictData.qualityMetas:{})" x-bind:key="k"><tr>
                  <td class="text-ink" x-text="k"></td>
                  <td><div class="tbox"><span class="nm" x-text="(dictData.qualityNames&&dictData.qualityNames[k])||''"></span><span x-text="v"></span></div></td>
                  <td><span class="ds-badge ds-badge--neutral" x-bind:class="(dictData.qualityApplies&&dictData.qualityApplies[k]==='ugc')?'ds-badge--intent':'ds-badge--category'" x-text="(dictData.qualityApplies&&dictData.qualityApplies[k]==='ugc')?'UGC':'전체'"></span></td>
                  <td><button type="button" class="text-[11px] text-muted hover:text-ink" x-on:click="startEdit('quality_metas', k, v, 'text', '품질 · ' + k)">편집</button></td>
                </tr></template>
              </tbody></table></div></div>
            <div class="panel"><div class="panel-hd"><b>법령 위반 유형</b><span class="meta tnum" x-text="dictData?Object.keys(dictData.legalTypes).length+'종':''"></span></div>
              <div class="overflow-auto" style="max-height:280px"><table class="ds-table"><thead><tr><th>코드</th><th>유형</th><th>근거</th><th></th></tr></thead><tbody>
                <template x-for="(v,k) in (dictData?dictData.legalTypes:{})" x-bind:key="k"><tr><td class="text-ink" x-text="k"></td><td><div class="tbox" x-text="v.label"></div></td><td class="text-muted" x-text="v.article"></td><td><button type="button" class="text-[11px] text-muted hover:text-ink" x-on:click="startEditLegal(k, v)">편집</button></td></tr></template>
              </tbody></table></div></div>
          </div>
        </div>
      </div>

      <!-- ═══ 모듈: 사용자 메타 ═══ -->
      <div x-show="mod === 'user'" x-cloak class="w-full space-y-4">
        <div class="panel"><div class="panel-bd">
          <div class="flex items-center justify-between gap-3 flex-wrap">
            <div class="text-xs text-muted">행동 로그(TIARA형)를 올리면 추출 콘텐츠와 조인해 <span class="text-body">소비 형태 · 강도 · 선호</span>를 산출합니다 <code class="text-violet">content_id</code> = 추출 순서(0부터)</div>
            <div class="flex items-center gap-2">
              <a href="/usermeta-template.csv" download class="inline-flex items-center gap-1.5 rounded-md border border-black/[0.12] px-2.5 py-1 text-xs font-medium text-ink hover:bg-black/[0.06]">템플릿</a>
              <label class="inline-flex cursor-pointer items-center gap-1.5 rounded-md bg-violet px-3 py-1.5 text-xs font-semibold text-ink hover:bg-violet-hover">
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
                <span x-show="!(u.affinity_entities||[]).length" class="text-xs text-muted">—</span>
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
        </div>
        <p class="text-xs text-muted" x-show="userData && userData.formula" x-text="userData ? userData.formula : ''"></p>
      </div>

      <!-- ═══ 모듈: 검증 · 평가 ═══ -->
      <div x-show="mod === 'eval'" x-cloak class="w-full space-y-4">
        <!-- 평가 2탭: ① 원천(골든셋) ② 실시간(메타컴파일) -->
        <div class="evaltabs">
          <button type="button" x-bind:class="evalTab==='golden'?'sel':''" x-on:click="evalTab='golden'">① 원천 평가 · 골든셋</button>
          <button type="button" x-bind:class="evalTab==='live'?'sel':''" x-on:click="evalTab='live'">② 실시간 튜닝 · 메타컴파일</button>
        </div>

        <!-- ① 원천 평가(골든셋): 기대 vs 실제 정합성 → 원천 프롬프트 수정 -->
        <div x-show="evalTab === 'golden'" class="space-y-4">
          <section class="panel"><div class="panel-hd"><b>원천 평가 · 골든셋 정합성</b><span class="meta">기대(정답) vs 실제</span></div>
            <div class="panel-bd">
              <p class="text-xs text-muted" style="margin-bottom:11px">관리자가 등록한 골든셋으로 <b class="text-ink">원천 프롬프트</b>의 정합성을 측정합니다. 낮으면 <b class="text-ink">프롬프트 스튜디오</b>에서 원천 프롬프트를 수정하고 다시 평가하세요. (골든셋 등록은 <b class="text-ink">팀 관리</b>)</p>
              <button type="button" class="ds-btn ds-btn--primary" x-bind:disabled="goldenBusy" x-on:click="runGolden()" x-text="goldenBusy ? '평가 중…(원천 프롬프트로 전건 추출)' : '평가 실행'"></button>
              <span class="text-xs text-muted" style="margin-left:10px" x-show="goldenResult && !goldenResult.ok" x-text="goldenResult ? goldenResult.error : ''"></span>
              <template x-if="goldenResult && goldenResult.ok">
                <div>
                  <div class="goldgrid">
                    <div class="goldbig"><div class="goldbig__v" x-text="Math.round((goldenResult.grade_accuracy||0)*100)+'%'"></div><div class="goldbig__l">등급 정합성</div></div>
                    <div class="goldstat"><b x-text="Math.round((goldenResult.harm_miss_rate||0)*100)+'%'"></b><span>유해 미탐</span></div>
                    <div class="goldstat"><b x-text="Math.round((goldenResult.reason_jaccard||0)*100)+'%'"></b><span>이유 일치</span></div>
                    <div class="goldstat"><b x-text="goldenResult.evaluated"></b><span>평가 건</span></div>
                  </div>
                  <div style="margin-top:14px">
                    <div class="text-xs text-muted" style="margin-bottom:6px">버킷별 정합성 — 어디가 새는지(원천 프롬프트 수정 우선순위)</div>
                    <template x-for="(v,k) in (goldenResult.by_reason_bucket||{})" x-bind:key="k">
                      <div class="ds-progress" style="margin:5px 0"><div class="ds-progress__head"><span class="ds-progress__label" x-text="k+' ('+v.n+')'"></span><span class="ds-progress__pct" x-text="Math.round(v.grade_acc*100)+'%'"></span></div><div class="ds-progress__track"><div class="ds-progress__fill ds-progress__fill--primary" x-bind:style="'width:'+Math.max(v.grade_acc*100,3)+'%'"></div></div></div>
                    </template>
                  </div>
                  <button type="button" class="ds-btn ds-btn--secondary" style="margin-top:12px" x-on:click="selectMod('prompt')">원천 프롬프트 수정하러 가기 →</button>
                </div>
              </template>
            </div>
          </section>
        </div>

        <!-- ② 실시간 튜닝(메타컴파일) -->
        <div x-show="evalTab === 'live'" class="space-y-4">
          <section class="panel"><div class="panel-hd"><b>실시간 튜닝 · 메타컴파일</b><span class="meta">여러 검수 의견 → 정제 지시</span>
            <button type="button" class="ds-btn ds-btn--primary ml-auto" style="height:30px;padding:0 12px" x-bind:disabled="metaBusy" x-on:click="runMetaCompile()" x-text="metaBusy ? '정리 중…' : '🧩 학습 정리'"></button>
          </div>
            <div class="panel-bd">
              <p class="text-xs text-muted" style="margin-bottom:10px">실시간 검수 피드백(REAP plan)을 단계별로 <b class="text-ink">병합·중복제거</b>하고, 의견이 갈리는 부분은 <b class="text-ink">명확화 필요</b>로 분리해 파인튜닝 층(LEARNED)에 반영합니다.</p>
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
              <div x-show="!metaResults" class="text-xs text-muted"><b class="text-ink">🧩 학습 정리</b>를 누르면 누적 검수 피드백을 정제합니다</div>
            </div>
          </section>
        <!-- 콘텐츠별 평가 피드백 → 학습 루프(다음 추출 프롬프트에 자동 반영) -->
        <section class="panel" data-fn><div class="panel-hd"><b>콘텐츠별 평가 · 학습 루프</b>
          <span class="meta tnum" x-show="dashData && dashData.feedback" x-text="dashData ? ('평가 ' + dashData.feedback.total + ' · 학습 반영 ' + dashData.feedback.learned + '건') : ''"></span>
          <button type="button" class="ds-btn ds-btn--secondary ml-auto" style="height:30px;padding:0 12px" x-show="dashData && dashData.feedback && dashData.feedback.total" x-on:click="clearFeedback()">초기화</button>
        </div>
          <div class="panel-bd">
            <p class="text-xs text-muted" style="margin-bottom:11px">콘텐츠마다 <b class="text-ink">정확</b> / <b class="text-ink">문제</b>를 표시하고, 문제는 교정 메모를 남기면 다음 추출부터 해당 단계 프롬프트에 자동 반영됩니다(원천 프롬프트는 <b class="text-ink">프롬프트 스튜디오</b>에서 편집)</p>
            <!-- 출처 필터: 자동 인입 / 단건 / 배치 구분 -->
            <div class="srcfilter" x-show="srcOptions.length > 1">
              <button type="button" class="srcfilter__chip" x-bind:class="srcFilter==='' ? 'sel' : ''" x-on:click="srcFilter=''">전체 <span x-text="(dashData&&dashData.contents?dashData.contents.length:0)"></span></button>
              <template x-for="s in srcOptions" x-bind:key="s">
                <button type="button" class="srcfilter__chip" x-bind:class="srcFilter===s ? 'sel' : ''" x-on:click="srcFilter=s">
                  <span x-text="s"></span> <span x-text="(dashData&&dashData.contents?dashData.contents.filter(c=>(c.source||'단건')===s).length:0)"></span></button>
              </template>
            </div>
            <div class="overflow-auto" style="max-height:440px;padding:2px">
              <template x-for="c in filteredContents" x-bind:key="c.hash">
                <div class="fbrow">
                  <div class="fbrow__main">
                    <div class="fbrow__title"><span class="ds-badge" x-bind:class="c.grade==='G' ? 'ds-badge--success' : 'ds-badge--neutral'" x-text="c.grade||'-'"></span><span class="ds-badge" x-bind:class="srcBadgeClass(c.source||'단건')" x-text="c.source||'단건'"></span><span x-text="c.title || '(제목 없음)'"></span><span class="fbrow__svc" x-text="c.service"></span></div>
                    <div class="fbrow__sum tbox" x-show="c.summary" x-text="c.summary"></div>
                  </div>
                  <div class="fbrow__act">
                    <button type="button" class="fbbtn" x-bind:class="(c.fb&&c.fb.verdict==='good')?'fbbtn--good':''" x-on:click="setFeedback(c,'good')">정확</button>
                    <button type="button" class="fbbtn" x-bind:class="(c.fb&&c.fb.verdict==='bad')?'fbbtn--bad':''" x-on:click="setFeedback(c,'bad')">문제</button>
                  </div>
                  <div class="fbrow__note" x-show="(c.fb&&c.fb.verdict==='bad') || fbNoteOpen[c.hash]">
                    <input class="field" style="height:34px;flex:1;min-width:180px" placeholder="교정 메모 — 예) 카테고리를 스포츠가 아니라 정치로 / 리드문이 핵심을 놓침" x-model="c.fb.note" x-on:keydown.enter="saveFbNote(c)">
                    <select class="field" style="width:auto;height:34px;padding:0 26px 0 10px" x-model="c.fb.stage"><option value="extract">추출</option><option value="analyze">분석</option><option value="review">검수</option><option value="judge">판정</option></select>
                    <button type="button" class="ds-btn ds-btn--primary" style="height:34px" x-on:click="saveFbNote(c)">반영</button>
                  </div>
                </div>
              </template>
              <div x-show="!(dashData&&dashData.contents&&dashData.contents.length)" class="text-xs text-muted" style="padding:10px">표시할 콘텐츠가 없습니다 먼저 추출을 실행하세요</div>
              <div x-show="dashData&&dashData.contents&&dashData.contents.length && !filteredContents.length" class="text-xs text-muted" style="padding:10px">이 출처의 콘텐츠가 없습니다</div>
            </div>
          </div>
        </section>
        <div x-show="!result" class="empty"><b class="text-body">실행 · 추출</b>에서 단건 추출을 실행하면 그 콘텐츠의 검증(trace·fallback·비용)이 표시됩니다</div>
        <div x-show="result" class="space-y-4">
          <div class="panel"><div class="panel-hd"><b>추출 trace</b><span class="meta" x-text="tr.prompt_version || ''"></span><button type="button" class="copybtn" x-show="tr && tr.content_id !== undefined" x-on:click="exportEval()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>엑셀 다운로드</button></div><div class="panel-bd">
            <div class="drow"><div class="k">fallback</div><div class="v flex flex-wrap gap-1.5">
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
          <p class="text-xs text-muted">정량 평가(ROUGE·정확도 게이트)는 정답셋 연동 시 활성화됩니다(계획)</p>
        </div>
        </div><!-- /evalTab live -->
      </div>

      <!-- ═══ 모듈: 팀 관리 (멀티테넌시) ═══ -->
      <div x-show="mod === 'admin'" x-cloak class="w-full space-y-4">
        <section class="panel"><div class="panel-hd"><b>팀 정보</b><span class="meta" x-text="adminData&&adminData.team ? adminData.team.name : ''"></span></div>
          <div class="panel-bd">
            <div class="invite">
              <div><div class="text-xs text-muted" style="margin-bottom:4px">초대 코드 — 팀원에게 공유하면 같은 팀으로 참가합니다</div>
                <div class="invite__code" x-text="adminData&&adminData.team ? adminData.team.invite_code : '—'"></div></div>
              <button type="button" class="ds-btn ds-btn--secondary" x-on:click="copyInvite()" x-text="inviteCopied ? '복사됨 ✓' : '복사'"></button>
            </div>
          </div>
        </section>
        <section class="panel"><div class="panel-hd"><b>멤버</b><span class="meta" x-text="(adminData&&adminData.members?adminData.members.length:0)+'명'"></span></div>
          <div class="panel-bd">
            <template x-for="m in (adminData?adminData.members:[])" x-bind:key="m.id">
              <div class="lb-row">
                <span class="lb-av" data-tier="0"><img x-bind:src="charImg(m.avatar)" alt=""></span>
                <span class="lb-name" x-text="m.name + (adminData.team && m.id===adminData.team.created_by ? ' (관리자)' : '')"></span>
                <button type="button" x-show="adminData&&adminData.isAdmin && adminData.team && m.id!==adminData.team.created_by" class="ds-btn ds-btn--secondary" style="height:28px;padding:0 11px" x-on:click="adminAct('remove_member', m.id)">제거</button>
              </div>
            </template>
            <div x-show="!(adminData&&adminData.members&&adminData.members.length)" class="text-xs text-muted" style="padding:8px">멤버가 없습니다</div>
          </div>
        </section>
        <section class="panel" x-show="adminData&&adminData.isAdmin"><div class="panel-hd"><b>골든셋</b><span class="meta" x-text="(adminData&&adminData.goldenCount?adminData.goldenCount+'건 등록됨':'미등록')"></span></div>
          <div class="panel-bd">
            <p class="text-xs text-muted" style="margin-bottom:10px">원천 평가의 정답셋. <b class="text-ink">{content, expected:{finalGrade, reasons}}</b> 형식의 .jsonl 을 올리면 교체 등록됩니다. (검증·평가 → 원천 평가에서 이 골든셋으로 정합성 측정)</p>
            <label class="ds-btn ds-btn--secondary" style="cursor:pointer">골든셋 .jsonl 등록<input type="file" accept=".jsonl" class="sr-only" x-on:change="registerGolden($event)"></label>
            <span class="text-xs text-muted" style="margin-left:10px" x-text="goldenMsg"></span>
          </div>
        </section>
        <section class="panel" x-show="adminData&&adminData.isAdmin"><div class="panel-hd"><b>콘텐츠 인입 (검토용)</b><span class="ds-badge ds-badge--neutral">관리자</span></div>
          <div class="panel-bd">
            <p class="text-xs text-muted" style="margin-bottom:10px">크롤러 엔드포인트에서 <b class="text-ink">수량 목표</b>로 당겨와 추출 → 전건을 팀 <b class="text-ink">검수 큐</b>에 적재합니다. 실시간 스트리밍 부담 없이 배치로.</p>
            <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
              <input class="field" style="flex:1;min-width:240px" placeholder="크롤러 엔드포인트 — JSON 배열 반환 GET (예: https://my-crawler/items)" x-model="ingestEndpoint">
              <input class="field" type="number" style="width:96px" min="1" max="200" x-model.number="ingestN" placeholder="수량">
              <button type="button" class="ds-btn ds-btn--primary" x-bind:disabled="ingestBusy" x-on:click="ingestRun()" x-text="ingestBusy ? '인입 중…' : '인입 실행'"></button>
            </div>
            <span class="text-xs text-muted" style="display:block;margin-top:7px" x-text="ingestMsg"></span>
          </div>
        </section>
        <section class="panel" x-show="adminData&&adminData.isAdmin"><div class="panel-hd"><b>데이터 관리</b><span class="ds-badge ds-badge--neutral">관리자</span></div>
          <div class="panel-bd">
            <p class="text-xs text-muted" style="margin-bottom:10px">우리 팀 데이터만 삭제됩니다(다른 팀 무영향). 되돌릴 수 없습니다.</p>
            <div style="display:flex;gap:10px;flex-wrap:wrap">
              <button type="button" class="ds-btn ds-btn--secondary" x-on:click="adminAct('clear_feedback')">평가 피드백 전체 삭제</button>
              <button type="button" class="ds-btn ds-btn--secondary" x-on:click="adminAct('clear_contents')">검토 콘텐츠 전체 삭제</button>
            </div>
          </div>
        </section>
        <div x-show="adminData && !adminData.isAdmin" class="text-xs text-muted" style="padding:4px">데이터 삭제·멤버 관리는 팀 관리자(생성자)만 가능합니다.</div>
      </div>

      <!-- ═══ 모듈: 평가 아레나 (게임화) — 팀 정확도 협동 스코어 + 리더보드 ═══ -->
      <div x-show="mod === 'arena'" x-cloak class="w-full space-y-4">
        <!-- 히어로: 팀 정확도 게이지(협동) -->
        <section class="arena-hero">
          <div class="arena-hero__head">
            <div><div class="arena-hero__eyebrow">팀 정확도 · 함께 끌어올리는 점수</div>
              <div class="arena-hero__big"><span x-text="arenaPct"></span><span class="arena-hero__pct">%</span>
                <span class="arena-hero__delta" x-show="arenaData && arenaData.accuracy_delta" x-bind:class="(arenaData&&arenaData.accuracy_delta>=0)?'up':'down'"
                      x-text="arenaData ? ((arenaData.accuracy_delta>=0?'▲ +':'▼ ')+Math.round(arenaData.accuracy_delta*100)+'%p · 최근') : ''"></span>
              </div>
            </div>
            <div class="arena-hero__target">목표 <b x-text="arenaTargetPct + '%'"></b></div>
          </div>
          <!-- 게이지: 정확도 채움 + 목표 마커 -->
          <div class="arena-gauge">
            <div class="arena-gauge__fill" x-bind:style="'width:' + arenaPct + '%'"></div>
            <div class="arena-gauge__target" x-bind:style="'left:' + arenaTargetPct + '%'" title="목표"></div>
          </div>
          <div class="arena-hero__foot">
            <span>자동 판정이 검수자와 일치한 비율 · <b class="text-ink" x-text="(arenaData?arenaData.reviews:0)"></b>건 검수됨</span>
            <span class="arena-quest" x-show="arenaData && arenaData.queue" x-on:click="selectMod('review')">
              🎯 남은 퀘스트 <b x-text="(arenaData?arenaData.queue:0)"></b>건 검수하러 가기 →</span>
            <span class="arena-quest arena-quest--done" x-show="arenaData && !arenaData.queue">✓ 검수 대기 없음 — 깔끔!</span>
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
              <div x-show="!(arenaData&&arenaData.leaderboard&&arenaData.leaderboard.length)" class="text-xs text-muted" style="padding:12px">아직 검수 기록이 없습니다 — <b class="text-ink">검수 큐</b>에서 첫 검수를 해보세요</div>
            </div>
          </section>
          <!-- 내 검수 캐릭터 (육성) -->
          <section class="panel"><div class="panel-hd"><b>내 검수 캐릭터</b><span class="meta" x-text="reviewer ? reviewer : '이름 미설정'"></span></div>
            <div class="panel-bd">
              <div x-show="!reviewer" class="text-xs text-muted" style="padding:8px">우상단에서 <b class="text-ink">검수자 이름</b>을 설정하면 나만의 캐릭터가 생깁니다</div>
              <template x-if="reviewer && arenaMe">
                <div class="charcard" x-bind:data-tier="levelTier(arenaMe.level)">
                  <div class="charcard__avatar">
                    <span class="charcard__glow"></span>
                    <img x-bind:src="charImg(arenaMe.char || reviewerChar)" alt="검수 캐릭터">
                    <span class="charcard__lvl" x-text="'Lv.' + arenaMe.level"></span>
                  </div>
                  <div class="charcard__title"><span x-text="levelEmoji(arenaMe.level)"></span> <span x-text="levelTitle(arenaMe.level)"></span></div>
                  <div class="charcard__xpwrap">
                    <div class="charcard__xpbar"><div class="charcard__xpfill" x-bind:style="'width:' + xpPct(arenaMe) + '%'"></div></div>
                    <div class="charcard__xptxt">다음 레벨까지 <b x-text="xpToNext(arenaMe) + 'pt'"></b> · 순위 #<span x-text="arenaMyRank"></span></div>
                  </div>
                  <div class="charcard__stats">
                    <div><b class="tnum" x-text="arenaMe.reviews"></b><span>검수</span></div>
                    <div><b class="tnum" x-text="arenaMe.corrections"></b><span>개선 🏅</span></div>
                    <div><b class="tnum" x-text="(arenaMe.streak||0)+'일'"></b><span>🔥 스트릭</span></div>
                  </div>
                  <div class="charcard__hint">검수 +10 · 채택된 개선(REAP) +25 — 점수가 쌓이면 캐릭터가 <b class="text-ink">성장</b>해요</div>
                </div>
              </template>
              <div x-show="reviewer && !arenaMe" class="charcard charcard--egg" data-tier="0">
                <div class="charcard__avatar"><img x-bind:src="charImg(reviewerChar)" alt="" style="opacity:.5;filter:grayscale(1)"><span class="charcard__lvl">Lv.0</span></div>
                <div class="charcard__title">🥚 검수 새싹</div>
                <div class="charcard__hint"><b class="text-ink" x-text="reviewer"></b> 의 첫 검수로 캐릭터를 깨워요 — <span class="arena-quest" x-on:click="selectMod('review')">검수하러 가기 →</span></div>
              </div>
            </div>
          </section>
        </div>
      </div>

      <!-- ═══ 모듈: 검수 큐 (팀 실시간 HITL) — YELLOW 대기열 + 다중 의견 ═══ -->
      <div x-show="mod === 'review'" x-cloak class="w-full space-y-4">
        <section class="panel" data-fn><div class="panel-hd"><b>검수 큐 · YELLOW 사람검수</b>
          <span class="meta tnum" x-text="(queueData && queueData.n != null) ? (queueData.n + '건') : ''"></span>
          <label class="text-xs text-muted" style="display:flex;align-items:center;gap:5px;margin-left:auto;cursor:pointer">
            <input type="checkbox" x-model="queueOnlyUnreviewed" x-on:change="loadQueue()"> 미검수만</label>
          <button type="button" class="ds-btn ds-btn--secondary" style="height:30px;padding:0 12px" x-on:click="loadQueue()">새로고침</button>
        </div>
          <div class="panel-bd">
            <p class="text-xs text-muted" style="margin-bottom:11px">자동(임베딩-LLM)이 확신 못 한 <b class="text-ink">YELLOW</b> 콘텐츠 대기열입니다. <b class="text-ink" x-text="reviewer || '(이름 미설정)'"></b> 으로 검수하며, 여러 검수자의 의견은 모두 보존되어 <b class="text-ink">합의/불일치</b>로 집계됩니다(실시간 반영)</p>
            <div class="overflow-auto" style="max-height:480px;padding:2px">
              <template x-for="it in (queueData ? queueData.items : [])" x-bind:key="it.hash">
                <div class="fbrow" x-init="notifyViewing(it)">
                  <div class="fbrow__main">
                    <div class="fbrow__title">
                      <span class="ds-badge ds-badge--neutral" x-text="it.grade || '·'"></span>
                      <span class="ds-badge" style="background:rgba(255,148,41,.16);color:#cc6a0a">YELLOW</span>
                      <span x-text="it.title || '(제목 없음)'"></span>
                      <span class="fbrow__svc" x-text="it.service"></span>
                      <span class="ds-badge ds-badge--intent" x-show="liveSeen[it.hash]" x-text="(liveSeen[it.hash]||'') + ' 보는 중'"></span>
                      <span class="ds-badge ds-badge--success" x-show="it.reviewed && !it.myVerdict">검수됨</span>
                      <span class="ds-badge" x-show="it.myVerdict" x-bind:class="it.myVerdict==='good'?'ds-badge--success':'ds-badge--neutral'" x-text="it.myVerdict==='good'?'내 의견 · 정확':'내 의견 · 문제'"></span>
                    </div>
                    <div class="fbrow__sum tbox" x-show="it.review_reason" x-text="it.review_reason"></div>
                  </div>
                  <div class="fbrow__act">
                    <button type="button" class="fbbtn" x-bind:class="it.myVerdict==='good'?'fbbtn--good':''" x-on:click="queueFeedback(it,'good')">정확</button>
                    <button type="button" class="fbbtn" x-bind:class="it.myVerdict==='bad'?'fbbtn--bad':''" x-on:click="queueFeedback(it,'bad')">문제</button>
                  </div>
                  <div class="fbrow__note" x-show="it.myVerdict==='bad'">
                    <input class="field" style="height:34px;flex:1;min-width:180px" placeholder="교정 메모 — 다음 추출 프롬프트에 자동 반영" x-model="it.note" x-on:keydown.enter="queueFeedback(it,'bad')">
                    <button type="button" class="ds-btn ds-btn--primary" style="height:34px" x-on:click="queueFeedback(it,'bad')">반영</button>
                  </div>
                </div>
              </template>
              <div x-show="!(queueData && queueData.items && queueData.items.length)" class="ds-empty" style="border:0;padding:22px 8px">
                <div class="ds-empty__desc"><b class="text-ink">검수 대기 없음</b> · YELLOW로 분류된 콘텐츠가 쌓이면 여기 표시됩니다(자동 추출이 확신 못 한 건)</div>
              </div>
            </div>
          </div>
        </section>
      </div>

      <!-- ═══ 모듈: 실행 큐 (단일 위젯) — 실제 실행 상태 ═══ -->
      <div x-show="mod === 'queue'" x-cloak class="w-full">
        <section class="panel"><div class="panel-hd"><b>실행 큐</b><span class="meta" x-text="(runningCount ? (runningCount + ' 실행중') : '대기 없음')"></span></div>
          <div class="panel-bd">
            <div x-show="loading">
              <div class="w-run"><span class="w-run__av"><img src="/vendor/yonghee-pitcher.svg" alt=""></span><div><div class="w-run__t" x-text="activeTabId === 'excel' ? '엑셀 일괄 추출 중' : '메타 추출 중'"></div><div class="ds-progress ds-progress--indeterminate" style="margin-top:5px"><div class="ds-progress__track"><div class="ds-progress__fill ds-progress__fill--primary"></div></div></div></div></div>
            </div>
            <template x-for="j in runningJobs" x-bind:key="j.id">
              <div class="w-run"><span class="w-run__av"><img src="/vendor/daesik-batter.svg" alt=""></span><div style="flex:1;min-width:0">
                <div class="w-run__t"><b class="text-ink" x-text="j.name"></b> · 자동 인입 중 <span class="ds-badge" x-bind:class="j.trigger==='auto' ? 'ds-badge--intent' : 'ds-badge--entity'" x-text="j.trigger==='auto' ? '자동' : '수동'"></span> <span class="text-xs text-muted tnum" x-show="j.total" x-text="j.done + ' / ' + j.total + '건'"></span></div>
                <div class="text-xs text-muted" x-text="j.last_msg || j.endpoint"></div>
                <div class="ds-progress" x-bind:class="j.total ? '' : 'ds-progress--indeterminate'" style="margin-top:5px"><div class="ds-progress__track"><div class="ds-progress__fill ds-progress__fill--primary" x-bind:style="j.total ? ('width:' + Math.round((j.done/j.total)*100) + '%') : ''"></div></div></div>
              </div></div>
            </template>
            <div x-show="!runningCount" class="ds-empty" style="border:0;padding:20px 8px">
              <div class="ds-empty__desc">진행 중인 작업이 없습니다 <b class="text-ink">새 추출</b> 또는 <b class="text-ink">자동 인입</b>을 실행하면 여기에 표시되고, 완료분은 <b class="text-ink">배치 결과</b>에 집계됩니다</div>
            </div>
          </div>
        </section>
      </div>

      <!-- ═══ 모듈: 프롬프트 스튜디오 (전용 도구) — 추출 단계별 프롬프트 ═══ -->
      <div x-show="mod === 'prompt'" x-cloak class="w-full space-y-4">
        <p class="ds-hint hintbox">단계마다 <b class="text-ink">모델을 지정</b>하면 그 모델의 원천 프롬프트로 동작합니다 각 과정에 다른 모델을 쓸 수 있고, 프롬프트는 모델별로 저장됩니다 보완은 <b class="text-ink">검증 · 평가</b>의 콘텐츠별 평가 피드백이 자동 반영됩니다</p>
        <datalist id="modelopts"><template x-for="m in availableModels" x-bind:key="m"><option x-bind:value="m"></option></template></datalist>
        <section class="panel" data-fn><div class="panel-hd"><b>추출</b><span class="meta">대식 · 이미지 → 신호(OCR · 비전)</span><span class="ds-badge ds-badge--success ml-auto" x-show="learnedStages.extract" data-tip="배치 결과 피드백이 이 단계 프롬프트에 자동 반영 중">학습 보정 반영</span></div>
          <div class="panel-bd">
            <div class="stage-model"><span class="stage-model__lbl">모델</span><input class="field" list="modelopts" x-model="stageModels.extract" x-on:change="onStageModelChange('extract')" placeholder="이 단계에 사용할 모델 (미지정 = 전역 프롬프트)"></div>
            <textarea x-model="stagePrompts.extract" rows="5" class="field" placeholder="이 단계의 원천 프롬프트(지시문)"></textarea>
            <div style="display:flex;align-items:center;gap:10px;margin-top:10px"><button type="button" x-on:click="saveStage('extract')" class="ds-btn ds-btn--primary">저장</button><button type="button" class="ds-btn ds-btn--secondary" x-on:click="restoreDefault('extract')">기본값 복원</button><span class="text-xs" style="color:var(--ds-success)" aria-live="polite" x-text="stageMsg.extract"></span><span class="text-xs" style="color:var(--ds-placeholder);margin-left:auto" x-text="stagePromptsMeta.extract ? ('최종 수정 ' + stagePromptsMeta.extract) : '수정 이력 없음'"></span></div>
          </div></section>
        <section class="panel" data-fn><div class="panel-hd"><b>분석</b><span class="meta">용희 · 신호 → 메타(리드문 · 엔티티 · 인텐트 · 카테고리)</span><span class="ds-badge ds-badge--success ml-auto" x-show="learnedStages.analyze" data-tip="배치 결과 피드백이 이 단계 프롬프트에 자동 반영 중">학습 보정 반영</span></div>
          <div class="panel-bd">
            <div class="stage-model"><span class="stage-model__lbl">모델</span><input class="field" list="modelopts" x-model="stageModels.analyze" x-on:change="onStageModelChange('analyze')" placeholder="이 단계에 사용할 모델 (미지정 = 전역 프롬프트)"></div>
            <textarea x-model="stagePrompts.analyze" rows="5" class="field" placeholder="이 단계의 원천 프롬프트(지시문)"></textarea>
            <div style="display:flex;align-items:center;gap:10px;margin-top:10px"><button type="button" x-on:click="saveStage('analyze')" class="ds-btn ds-btn--primary">저장</button><button type="button" class="ds-btn ds-btn--secondary" x-on:click="restoreDefault('analyze')">기본값 복원</button><span class="text-xs" style="color:var(--ds-success)" aria-live="polite" x-text="stageMsg.analyze"></span><span class="text-xs" style="color:var(--ds-placeholder);margin-left:auto" x-text="stagePromptsMeta.analyze ? ('최종 수정 ' + stagePromptsMeta.analyze) : '수정 이력 없음'"></span></div>
          </div></section>
        <section class="panel" data-fn><div class="panel-hd"><b>검수</b><span class="meta">복실 · 품질 메타 판정</span><span class="ds-badge ds-badge--success ml-auto" x-show="learnedStages.review" data-tip="배치 결과 피드백이 이 단계 프롬프트에 자동 반영 중">학습 보정 반영</span></div>
          <div class="panel-bd">
            <div class="stage-model"><span class="stage-model__lbl">모델</span><input class="field" list="modelopts" x-model="stageModels.review" x-on:change="onStageModelChange('review')" placeholder="이 단계에 사용할 모델 (미지정 = 전역 프롬프트)"></div>
            <textarea x-model="stagePrompts.review" rows="5" class="field" placeholder="이 단계의 원천 프롬프트(지시문)"></textarea>
            <div style="display:flex;align-items:center;gap:10px;margin-top:10px"><button type="button" x-on:click="saveStage('review')" class="ds-btn ds-btn--primary">저장</button><button type="button" class="ds-btn ds-btn--secondary" x-on:click="restoreDefault('review')">기본값 복원</button><span class="text-xs" style="color:var(--ds-success)" aria-live="polite" x-text="stageMsg.review"></span><span class="text-xs" style="color:var(--ds-placeholder);margin-left:auto" x-text="stagePromptsMeta.review ? ('최종 수정 ' + stagePromptsMeta.review) : '수정 이력 없음'"></span></div>
          </div></section>
        <section class="panel" data-fn><div class="panel-hd"><b>판정 · 부여</b><span class="meta">딱지 · 유통 결정 · 법령</span><span class="ds-badge ds-badge--success ml-auto" x-show="learnedStages.judge" data-tip="배치 결과 피드백이 이 단계 프롬프트에 자동 반영 중">학습 보정 반영</span></div>
          <div class="panel-bd">
            <div class="stage-model"><span class="stage-model__lbl">모델</span><input class="field" list="modelopts" x-model="stageModels.judge" x-on:change="onStageModelChange('judge')" placeholder="이 단계에 사용할 모델 (미지정 = 전역 프롬프트)"></div>
            <textarea x-model="stagePrompts.judge" rows="5" class="field" placeholder="이 단계의 원천 프롬프트(지시문)"></textarea>
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
      <div x-show="mod === 'intake'" x-cloak class="w-full space-y-4">
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
        <div class="ds-widget ds-widget--info" style="--w-accent:#a05cff">
          <div class="ds-widget__head"><div class="ds-widget__title"><span class="ds-widget__icon-chip"><svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg></span><span>콘텐츠 출처 분류</span></div><div class="ds-widget__actions"><span class="ds-widget__kind ds-widget__kind--info">정보</span></div></div>
          <div class="ds-widget__body">
            <div class="flex flex-wrap gap-1.5"><span class="ds-badge ds-badge--category">PGC 기존 미디어</span><span class="ds-badge ds-badge--category">UGC 사용자 생성</span><span class="ds-badge ds-badge--category">AIGC AI 생성</span><span class="ds-badge ds-badge--category">AIEC AI 보정</span></div>
            <p class="ds-hint" style="margin-top:8px">식별 표준 · C2PA(자격 증명) · SynthID(워터마크) 발행자 정보로 PGC/UGC 1차 식별</p>
          </div>
        </div>
      </div>

    </div>
  </div>
  </div><!-- /.appbody -->

  <!-- ✎ 편집 팝업 — 편집 버튼 클릭 시 바로 수정(사전·정책 등) -->
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

  <!-- ⚙ 설정 = 고정 팝업(Dialog) · 위젯 아님(§9.5) -->
  <div class="ds-dialog-backdrop" x-show="settingsOpen" x-cloak x-on:mousedown.self="settingsOpen = false" style="z-index:70">
    <div class="ds-dialog" role="dialog" aria-modal="true" aria-label="설정" style="max-width:560px">
      <h2 class="ds-dialog__title">설정</h2>
      <div class="ds-dialog__body" style="max-height:70vh;overflow:auto">

      <!-- 탭: API 키 / 모델 (Atelier 방식) -->
      <div class="cfgtabs">
        <button type="button" x-on:click="cfgTab = 'keys'" x-bind:class="cfgTab === 'keys' ? 'on' : ''">API 키</button>
        <button type="button" x-on:click="cfgTab = 'models'" x-bind:class="cfgTab === 'models' ? 'on' : ''">모델</button>
        <button type="button" x-on:click="cfgTab = 'data'" x-bind:class="cfgTab === 'data' ? 'on' : ''">데이터</button>
      </div>

      <!-- ① API 키 -->
      <div x-show="cfgTab === 'keys'" class="cfgsec">
        <!-- 운영(공유 서버): 키는 서버에서 관리 → 팀원은 입력 불필요 -->
        <div x-show="cfg.keyManagedByServer" class="keymanaged">
          <b class="text-ink">🔒 API 키는 서버에서 관리됩니다</b>
          <p>공유 서버 모드입니다. 추출 키는 <b>관리자가 서버에 한 번</b> 설정하고, 팀원은 따로 키를 넣지 않아도 바로 사용합니다.
            <span x-text="cfg.hasKey ? '· 현재 연결됨 ✓' : '· 서버에 키 미설정(관리자 확인 필요)'"></span></p>
        </div>
        <div x-show="!cfg.keyManagedByServer">
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
                  class="rounded-lg bg-violet px-3.5 py-1.5 text-[13px] font-medium text-ink hover:bg-violet-hover disabled:opacity-50" x-text="keyState(s) ? '변경' : '저장'"></button>
                <button type="button" x-show="keyPersisted(s)" x-on:click="forgetKey(s)"
                  class="rounded-lg border border-[#ff4e33]/30 px-3.5 py-1.5 text-[13px] font-medium text-[#ff4e33] hover:bg-[#ff4e33]/10">삭제</button>
                <span class="text-xs text-muted" aria-live="polite" x-text="keyMsgs[s]"></span>
              </div>
            </div>
          </template>
        </div>

        <!-- 직접 호출(Solar) — 통합 라우터처럼 카드로 묶음 -->
        <div class="routercard" style="margin-top:14px">
          <div class="rc-h"><b>직접 호출</b></div>
          <p class="rc-d">각 회사 키로 직접 호출합니다 통합 라우터와 함께 등록해도 됩니다</p>
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
                class="rounded-lg bg-violet px-3.5 py-1.5 text-[13px] font-medium text-ink hover:bg-violet-hover disabled:opacity-50" x-text="cfg.hasKey ? '변경' : '저장'"></button>
              <button type="button" x-show="cfg.hasKey" x-on:click="testConn()" x-bind:disabled="cfgBusy"
                class="rounded-lg border border-black/[0.10] px-3.5 py-1.5 text-[13px] font-medium text-ink hover:bg-black/[0.05] disabled:opacity-50">연결 테스트</button>
              <button type="button" x-show="cfg.persisted" x-on:click="forgetKey('solar')"
                class="rounded-lg border border-[#ff4e33]/30 px-3.5 py-1.5 text-[13px] font-medium text-[#ff4e33] hover:bg-[#ff4e33]/10">삭제</button>
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

      <!-- ③ 데이터 -->
      <div x-show="cfgTab === 'data'" x-cloak class="cfgsec">
        <div class="routercard">
          <div class="rc-h"><b>로컬 저장 (SQLite)</b><span class="rc-badge" x-text="(cfg.storedCount || 0) + '건'"></span></div>
          <p class="rc-d">추출 결과는 로컬 DB에 누적 저장되어 재시작해도 유지됩니다 대시보드·토픽·사용자 메타가 이 데이터를 집계합니다 동일 콘텐츠·결과 무변경 시 적재되지 않습니다(중복 방지)</p>
          <button type="button" x-on:click="clearStore()" class="rounded-lg border border-[#ff4e33]/30 px-3.5 py-1.5 text-[13px] font-medium text-[#ff4e33] hover:bg-[#ff4e33]/10">적재 데이터 초기화</button>
        </div>
      </div>

      <!-- ② 모델 -->
      <div x-show="cfgTab === 'models'" x-cloak>
        <div class="cfgsec">
          <label class="lbl">텍스트 모델 <span class="font-normal normal-case tracking-normal text-muted">· 리드문·메타</span></label>
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
            class="mt-2 w-full rounded-lg border border-black/[0.10] px-3 py-1.5 text-[13px] font-medium text-ink hover:bg-black/[0.05] disabled:opacity-50">Solar 모델 새로고침</button>
          <span class="mt-1.5 block text-xs text-muted" x-text="modelsMsg"></span>
        </div>

        <div class="cfgsec">
          <label class="lbl">이미지 모델 <span class="font-normal normal-case tracking-normal text-muted">· 이미지 이해</span></label>
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

      </div>
      <div class="ds-dialog__footer"><button type="button" class="ds-btn ds-btn--ghost" x-on:click="settingsOpen = false">닫기</button></div>
    </div>
  </div>

  <!-- 플로팅 도우미(채널톡 스타일) — 모든 기능 허브 -->
  <div class="ds-chat" x-show="chatOpen" x-cloak>
    <div class="ds-chat__head">
      <span class="ds-chat__av"><img src="/vendor/boksil-catcher.svg" alt=""></span>
      <div><div class="ds-chat__title">Prism 도우미</div><div class="ds-chat__sub"><span class="ds-statusdot ds-statusdot--ok"><span class="ds-statusdot__dot"></span></span>보통 1분 내 응답</div></div>
      <span style="margin-left:auto"><button type="button" class="ds-iconbtn ds-iconbtn--sm" x-on:click="chatOpen = false" aria-label="닫기"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></button></span>
    </div>
    <div class="ds-chat__body">
      <template x-for="(m, i) in chatMsgs" x-bind:key="i"><div class="ds-chat__msg" x-bind:class="m.from === 'me' ? 'ds-chat__msg--me' : 'ds-chat__msg--bot'" x-text="m.text"></div></template>
    </div>
    <div class="ds-chat__quick">
      <button type="button" class="ds-btn ds-btn--pill" x-on:click="chatAct('extract')">새 추출</button>
      <button type="button" class="ds-btn ds-btn--pill" x-on:click="chatAct('dict')">사전 편집</button>
      <button type="button" class="ds-btn ds-btn--pill" x-on:click="chatAct('settings')">설정</button>
    </div>
    <div class="ds-chat__foot"><textarea class="ds-chat__input" x-model="chatDraft" rows="1" placeholder="작업을 지시하세요…" x-on:keydown.enter.prevent="chatSend()"></textarea><button type="button" class="ds-chat__send" x-on:click="chatSend()" aria-label="보내기"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 8h10M8 4l4 4-4 4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></button></div>
  </div>

  <!-- 복사 토스트 -->
  <div x-show="copyMsg" x-cloak x-transition.opacity class="toast" x-text="copyMsg" aria-live="polite"></div>

</div>

<!-- 위젯 홈 인터랙션(편집·리사이즈·드래그·틸트) — SERVICE_DESIGN §4.3 규격 -->
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
      var av = document.createElement('span'); av.className = 'wz-char wz-char--sm';
      av.innerHTML = '<img src="/vendor/' + stageChar(WID_STAGE[wid]) + '.svg" alt="">';
      actions.insertBefore(av, actions.firstChild);
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
        var av = document.createElement('span'); av.className = 'wz-char wz-char--sm';
        av.innerHTML = '<img src="/vendor/' + prismCharForPanel(h, titleText) + '.svg" alt="">';
        actions.appendChild(av);
        var fn = h.closest('[data-fn]');
        var kind = document.createElement('span'); kind.className = 'ds-widget__kind ds-widget__kind--' + (fn ? 'fn' : 'info'); kind.textContent = fn ? '기능' : '정보';
        actions.appendChild(kind);
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
    # 백엔드 결정 — 운영은 Supabase 전용(조용한 로컬 폴백 금지, 미가용이면 시작 실패)
    _mode, _required = backend_mode()
    st = get_store()
    if _required:
        if not st:
            print("  [중단] Supabase 백엔드를 쓰려는데 초기화 실패 — SUPABASE_URL/SERVICE_KEY 확인.")
            print("         (로컬·오프라인은: PRISM_BACKEND=sqlite)")
            sys.exit(1)
        try:
            st.count()                                 # 연결 확인(잘못된 키·네트워크면 여기서 실패)
        except Exception as e:
            print(f"  [중단] Supabase 연결 실패: {e}")
            sys.exit(1)
    print(f"  백엔드: {_mode}" + (" · 공유(운영)" if _mode == "supabase" else " · 로컬"))
    start_ingest_scheduler()                           # 활성 소스 자동 폴링(백그라운드)
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
