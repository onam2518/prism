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
import tempfile
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import imagext as IMG
from . import pipeline as PIPE
from . import prompts as PR
from .config import Config, DEFAULT_CONFIG_PATH
from .llm import LLMClient

# 마지막 실행 결과(리포트 생성용)
_LAST_RESULTS: list = []


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
def run_pipeline(fields: dict, *, mock: bool) -> dict:
    cfg = Config.load()
    llm = make_text_llm(cfg, mock)           # 텍스트 슬롯(solar|router). 무키면 내부서 mock

    images = [v for k, v in fields.items()
              if isinstance(v, dict) and v.get("bytes") and k.startswith("image")]
    source = "text"
    signals = []
    if images:
        source = "image"
        signals = IMG.extract_signals(images, mock=llm.mock)
        content = IMG.build_content(
            signals,
            displayServiceName=fields.get("displayServiceName", "포토"),
            title=fields.get("title", ""),
            caption=fields.get("caption", ""),
        )
    else:
        content = {
            "displayServiceName": fields.get("displayServiceName", ""),
            "title": fields.get("title", ""),
            "subtitle": fields.get("subtitle", ""),
            "body": fields.get("body", ""),
        }

    out = PIPE.extract(content, llm, legal=cfg.legal_enabled)
    _LAST_RESULTS[:] = [out]
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
               "quality_metas", "legal_types", "domain_groups", "category_iab_map"}
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
                               "category_iab_map": "CATEGORY_IAB_MAP"}.get(target, ""), {})
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
    """\ud1a0\ud53d \ubaa8\ub4c8: \ub9c8\uc9c0\ub9c9 \ucd94\ucd9c \uacb0\uacfc\uc5d0\uc11c \uc5d4\ud2f0\ud2f0\ud615\u00b7\uc0ac\uac74\ud615\u00b7\uc870\uac74\ud615 \ud1a0\ud53d \ube4c\ub4dc."""
    if not _LAST_RESULTS:
        return {"n_contents": 0, "single": [], "composite": [], "filter": [], "summary": {}}
    from . import topic as TP
    with tempfile.TemporaryDirectory() as d:
        rpath = os.path.join(d, "r.jsonl")
        with open(rpath, "w", encoding="utf-8") as f:
            for r in _LAST_RESULTS:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        try:
            return TP.build_topics(rpath)
        except Exception as e:
            return {"error": str(e)[:200], "n_contents": len(_LAST_RESULTS),
                    "single": [], "composite": [], "filter": [], "summary": {}}


def dashboard_data() -> dict:
    """\ub300\uc2dc\ubcf4\ub4dc \ubaa8\ub4c8: \ub9c8\uc9c0\ub9c9 \uacb0\uacfc \uc9d1\uacc4(\uc720\ud1b5 G/R \u00b7 \uc778\ud150\ud2b8 \u00b7 \uce74\ud14c\uace0\ub9ac \u00b7 \ud488\uc9c8 \uc0ac\uc720)."""
    rows = _LAST_RESULTS
    n = len(rows)
    g = sum(1 for r in rows if (r.get("quality_meta") or {}).get("finalGrade") == "G")
    intent_c, cat_c, reason_c = {}, {}, {}
    lead_sum = lead_n = ent_total = 0
    for r in rows:
        im = r.get("item_meta") or {}
        for t in (im.get("intent") or []):
            intent_c[t] = intent_c.get(t, 0) + 1
        for v in (im.get("content_category") or {}).values():
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

    return {
        "n": n, "g": g, "r": n - g, "gPct": round(g / n * 100) if n else 0,
        "entities": ent_total, "avgLead": round(lead_sum / lead_n) if lead_n else 0,
        "intents": topk(intent_c), "categories": topk(cat_c), "qualityReasons": topk(reason_c),
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
    if not _LAST_RESULTS:
        return {"empty": True, "n_contents": 0, "users": [], "personas_def": [],
                "note": "먼저 [실행 · 추출]에서 콘텐츠를 추출하세요. content_id 는 추출 순서(0부터)와 매칭됩니다."}
    with tempfile.TemporaryDirectory() as d:
        rpath = os.path.join(d, "r.jsonl")
        with open(rpath, "w", encoding="utf-8") as f:
            for r in _LAST_RESULTS:
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
        results, items = [], []
        for c in contents:
            out = PIPE.extract(c, llm, legal=cfg.legal_enabled)
            results.append(out)
            im = out.get("item_meta") or {}
            items.append({"title": (c.get("title") or "")[:80],
                          "summary": im.get("summary", ""),
                          "entities": im.get("entities", []),
                          "intent": im.get("intent", []),
                          "grade": (out.get("quality_meta") or {}).get("finalGrade", "")})
        _LAST_RESULTS[:] = results
        return {"source": "excel", "mock": llm.mock, "count": len(results),
                "mapping": a["mapping"], "items": items}
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def build_report_html() -> str:
    if not _LAST_RESULTS:
        return "<p>아직 실행 결과가 없습니다. 먼저 추출을 실행하세요.</p>"
    from . import dashboard as DASH
    with tempfile.TemporaryDirectory() as d:
        rpath = os.path.join(d, "results.jsonl")
        with open(rpath, "w", encoding="utf-8") as f:
            for r in _LAST_RESULTS:
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
    """config 의 system_prompt 를 추출 프롬프트에 반영(추가 지시)."""
    try:
        PR.EXTRA_INSTRUCTION = Config.load().system_prompt or ""
    except Exception:
        pass


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
        "configured": cfg.is_configured(),
        "forcedMock": Handler.server_mock,
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
    """키/모델/엔드포인트/추론강도/추가지시 적용. 키만 프로세스 환경(+옵션 ~/.prism_key)."""
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
    slot_keys = ("text_provider", "text_model", "vision_provider", "vision_model")
    has_slot = any(k in data for k in slot_keys)
    has_legal = "legal_enabled" in data
    if model or base or reasoning or has_sp or has_slot or has_legal:
        cfg = Config.load()
        if base:
            cfg.set_base_url(base)
        if model:
            cfg.model = model
        if reasoning:
            cfg.reasoning_effort = reasoning
        if has_sp:
            cfg.system_prompt = (data.get("system_prompt") or "").strip()
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
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/report"):
            self._send(200, build_report_html())
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
            self._send(200, json.dumps(dashboard_data(), ensure_ascii=False), _JSON)
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
                result = run_pipeline(fields, mock=self.server_mock)
            self._send(200, json.dumps(result, ensure_ascii=False), _JSON)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)


PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prism</title>
<meta name="description" content="이미지·텍스트·엑셀에서 리드문·엔티티·인텐트·콘텐츠 카테고리를 추출하는 콘텐츠 메타 도구">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Crect width='24' height='24' rx='6' fill='%235b52ff'/%3E%3Cpath d='M12 4l1.7 5L19 12l-5.3 1.7L12 19l-1.7-5.3L5 12l5.3-1.7z' fill='%23fff'/%3E%3C/svg%3E">
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
        violet: { DEFAULT: '#5b52ff', hover: '#4a42e0', deep: '#281ca5' },
        solar: '#d2ff95',
        canvas: '#0b0a0f', surface: '#141318', surface2: '#1a1922',
        body: '#9aa0aa', muted: '#6e7191',
      },
    } },
  };
</script>
<script>
  document.addEventListener('alpine:init', () => {
    Alpine.data('prismApp', () => ({
      tabItems: [{ id: 'image', label: '이미지' }, { id: 'text', label: '텍스트' }, { id: 'excel', label: '엑셀' }],
      activeTabId: 'image',
      // 어드민 모듈 셸
      mod: 'run',
      // 워크플로 순서: ① 실행 → ② 산출(부여 순서) → ③ 현황 → ④ 정책·기반
      mods: [
        { g: '실행', items: [
          { id: 'run', label: '추출 실행', cov: 'done', icon: 'm5 12 5 5L20 7' } ] },
        { g: '산출 · 메타', items: [
          { id: 'quality', label: '품질 메타', cov: 'done', icon: 'M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z' },
          { id: 'topic', label: '토픽', cov: 'done', icon: 'M12 2 2 7l10 5 10-5zM2 17l10 5 10-5M2 12l10 5 10-5' },
          { id: 'user', label: '사용자 메타', cov: 'poc', icon: 'M16 21v-2a4 4 0 0 0-8 0v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8' } ] },
        { g: '현황', items: [
          { id: 'dash', label: '대시보드', cov: 'done', icon: 'M3 3v18h18M8 14v3m4-7v7m4-11v11' },
          { id: 'eval', label: '검증 · 평가', cov: 'poc', icon: 'M9 11l3 3 8-8M21 12a9 9 0 1 1-6.2-8.5' } ] },
        { g: '정책 · 기반', items: [
          { id: 'intake', label: '인입 · 적용 대상', cov: 'poc', icon: 'M4 4h16v6H4zM4 14h16v6H4z' },
          { id: 'dict', label: '사전 · 정책', cov: 'done', icon: 'M4 4h16v16H4zM8 4v16M8 9h12M8 14h12' } ] },
      ],
      dashData: null, topicData: null, dictData: null, userData: null, modBusy: false, dictGroup: '',
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
        this.refreshConfig();
        fetch('/vocab').then(r => r.json()).then(j => { if (j.groups && j.groups.length) this.groups = j.groups; }).catch(() => {});
        // Cmd/Ctrl + Enter 로 추출 실행
        window.addEventListener('keydown', (e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !this.loading) { e.preventDefault(); this.run(); }
        });
      },
      get tabLabel() { return (this.tabItems.find(t => t.id === this.activeTabId) || {}).label || ''; },
      get modLabel() {
        for (const g of this.mods) for (const it of g.items) if (it.id === this.mod) return it.label;
        return '';
      },
      selectMod(id) {
        this.mod = id; this.status = '';
        if (id === 'dash') this.loadDash();
        else if (id === 'topic') this.loadTopics();
        else if (id === 'dict') this.loadDict();
        else if (id === 'user') this.loadUser();
      },
      async loadDash() { this.modBusy = true; try { this.dashData = await (await fetch('/dashboard')).json(); } catch (e) {} this.modBusy = false; },
      async loadTopics() { this.modBusy = true; try { this.topicData = await (await fetch('/topics')).json(); } catch (e) {} this.modBusy = false; },
      async loadDict() { this.modBusy = true; try { this.dictData = await (await fetch('/dict')).json(); if (!this.dictGroup) this.dictGroup = (this.dictData.serviceGroups || [])[0] || ''; } catch (e) {} this.modBusy = false; },
      // 사전·정책 편집(사용자 직접 수정)
      editT: null, editKey: null, editKind: 'list', editVal: '', editTitle: '', editMsg: '', editExtra: '',
      startEdit(target, key, value, kind, title) {
        this.editT = target; this.editKey = key; this.editKind = kind || 'list'; this.editTitle = title || target; this.editMsg = ''; this.editExtra = '';
        this.editVal = (kind === 'text') ? (value || '') : (Array.isArray(value) ? value.join('\\n') : '');
      },
      startEditLegal(code, v) { this.startEdit('legal_types', code, (v && v.label) || '', 'text', '법령 · ' + code); this.editExtra = (v && v.article) || ''; },
      cancelEdit() { this.editT = null; this.editMsg = ''; },
      async saveEdit() {
        let value = this.editKind === 'text' ? this.editVal : this.editVal.split('\\n').map(s => s.trim()).filter(Boolean);
        if (this.editT === 'legal_types') value = { label: this.editVal, article: this.editExtra };
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
          if (!this.cfgModel) this.cfgModel = this.cfg.model;
          if (this.cfg.reasoning) this.reasoning = this.cfg.reasoning;
          if (typeof this.cfg.systemPrompt === 'string') this.systemPrompt = this.cfg.systemPrompt;
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
      setReasoning(id) { this.reasoning = id; this.applyPrefs(); },

      selectTab(id) { this.activeTabId = id; this.status = ''; },

      // DNM 메타 체계(13. 프로젝트 기획 / 1312. 아이템 메타) 기준 item_meta 필드:
      //   summary(리드문) · entities(엔티티) · intent(인텐트) · content_category(콘텐츠 카테고리)
      get im() { return (this.result && this.result.output.item_meta) || {}; },
      get q() { return (this.result && this.result.output.quality_meta) || {}; },
      get contentCats() {
        const e = this.im.content_category || {};
        return Object.keys(e).map((k) => k + ' \\u2192 ' + e[k]);
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
  /* ── 타이포 스케일(디자인 시스템 토큰) ──
     글꼴: Pretendard Variable. 영역별 굵기·크기 규칙:
       본문 14/400 · 라벨 11/600(uppercase) · 패널 제목 13/600
       타이틀바 13/600 · 히어로 18/600 · 수치 23/600(tabular) */
  :root{
    --ds-font:"Pretendard Variable",Pretendard,system-ui,-apple-system,sans-serif;
    --ctrl-h:42px;            /* 단일 컨트롤 높이(입력·셀렉트·드롭존 공통) */
    --ctrl-r:8px;             /* 컨트롤 radius(토큰 control) */
    --ctrl-px:12px;           /* 컨트롤 좌우 패딩 */
  }
  body{font-family:var(--ds-font);font-size:14px;line-height:1.5;letter-spacing:-.003em;
    background:
      radial-gradient(820px 420px at 100% -6%, rgba(91,82,255,.12), transparent 60%),
      radial-gradient(680px 360px at 0% 0%, rgba(210,255,149,.045), transparent 55%),
      #0b0a0f;
    background-attachment:fixed}
  ::selection{background:#5b52ff;color:#fff}
  ::-webkit-scrollbar{width:11px;height:11px}
  ::-webkit-scrollbar-thumb{background:rgba(255,255,255,.09);border-radius:8px;border:3px solid transparent;background-clip:content-box}
  ::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.18);background-clip:content-box}

  /* ── 폼 컨트롤(단일 규격) ──
     입력·셀렉트·드롭존은 같은 높이(--ctrl-h)·radius·패딩·테두리를 공유한다.
     textarea 는 다행이므로 높이만 자동, 나머지 토큰은 동일. */
  .field{width:100%;box-sizing:border-box;border-radius:var(--ctrl-r);background:#0d0c12;
    border:1px solid rgba(255,255,255,.10);color:#fff;font-family:var(--ds-font);font-size:14px;
    transition:border-color .15s,box-shadow .15s,background .15s}
  input.field,select.field{height:var(--ctrl-h);padding:0 var(--ctrl-px)}
  textarea.field{padding:11px var(--ctrl-px);line-height:1.55;min-height:96px;resize:vertical}
  .field::placeholder{color:#5b606b}
  .field:hover{border-color:rgba(255,255,255,.18)}
  .field:focus{outline:none;border-color:#5b52ff;box-shadow:0 0 0 3px rgba(91,82,255,.22);background:#0b0a0f}
  select.field{appearance:none;-webkit-appearance:none;padding-right:34px;cursor:pointer;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%239aa0aa' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
    background-repeat:no-repeat;background-position:right 11px center}

  /* ── 디자인 시스템 파일럿(이미지 업로드: ImageDropzone + AttachmentChip) ── */
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
  /* HeroEmpty(빈 상태) */
  .ds-hero{display:flex;flex-direction:column;align-items:center;text-align:center;gap:4px;
    padding:48px 24px;border:1px solid var(--ds-hairline);border-radius:var(--ds-radius-xl);font-family:var(--ds-font-body);
    background:radial-gradient(440px 210px at 50% 0%,var(--ds-primary-tint),transparent 70%),var(--ds-surface)}
  .ds-hero__title{font-family:var(--ds-font-sans);font-size:var(--ds-size-heading);font-weight:600;color:var(--ds-ink);margin-top:12px}
  .ds-hero__desc{font-size:var(--ds-size-body);color:var(--ds-muted);max-width:30rem;line-height:var(--ds-lh-normal)}
  .ds-hero__chips{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-top:16px}

  /* 드롭존(파일 업로드) — .field 와 동일 규격. 점선 테두리·우측 버튼만 다름 */
  .dropzone{display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;
    box-sizing:border-box;height:var(--ctrl-h);padding:0 6px 0 var(--ctrl-px);
    border-radius:var(--ctrl-r);border:1px dashed rgba(255,255,255,.16);background:#0d0c12;
    cursor:pointer;font-size:14px;color:#9aa0aa;transition:border-color .15s,background .15s}
  .dropzone:hover{border-color:rgba(91,82,255,.55);background:#0b0a0f}
  .dropzone.drag{border-color:#5b52ff;border-style:solid;background:rgba(91,82,255,.10);color:#c8c3ff}
  .dropzone .pick{flex:none;display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 12px;
    border-radius:6px;background:rgba(255,255,255,.08);color:#fff;font-size:12px;font-weight:600}
  .dropzone .name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

  /* 썸네일 미리보기 */
  .thumb{position:relative;aspect-ratio:1;border-radius:9px;overflow:hidden;border:1px solid rgba(255,255,255,.10);background:#0d0c12}
  .thumb img{width:100%;height:100%;object-fit:cover;display:block}
  .thumb-x{position:absolute;top:3px;right:3px;width:18px;height:18px;display:flex;align-items:center;justify-content:center;
    border-radius:5px;background:rgba(8,8,12,.72);color:#fff;opacity:0;transition:opacity .12s}
  .thumb:hover .thumb-x{opacity:1}
  .thumb-x svg{width:11px;height:11px}

  /* 복사 토스트 */
  .toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);z-index:60;
    padding:8px 16px;border-radius:10px;font-size:13px;font-weight:600;color:#fff;
    background:rgba(20,19,24,.92);border:1px solid rgba(255,255,255,.12);
    box-shadow:0 12px 30px -10px rgba(0,0,0,.7);backdrop-filter:blur(8px)}
  /* 인라인 복사 버튼 */
  .copybtn{display:inline-flex;align-items:center;gap:4px;border-radius:6px;padding:3px 8px;font-size:11.5px;
    font-weight:600;color:#9aa0aa;border:1px solid rgba(255,255,255,.10);transition:color .12s,border-color .12s,background .12s}
  .copybtn:hover{color:#fff;border-color:rgba(255,255,255,.2);background:rgba(255,255,255,.05)}
  .copybtn svg{width:12px;height:12px}

  /* 카드: 토큰 유지 + 미세 입체(상단 하이라이트)·호버 리프트 */
  .card{box-shadow:inset 0 1px 0 rgba(255,255,255,.045);
    transition:transform .2s cubic-bezier(.32,.72,0,1),border-color .2s}
  .card:hover{transform:translateY(-1px);border-color:rgba(255,255,255,.14)}

  /* 리드문 hero */
  .lead{position:relative;overflow:hidden;
    background:linear-gradient(180deg,rgba(91,82,255,.10),rgba(91,82,255,.02))!important;
    border-color:rgba(91,82,255,.24)!important}
  .lead::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:#5b52ff}

  /* 사이드바 활성 항목 좌측 액센트 */
  .navitem.active::before{content:"";position:absolute;left:-12px;top:50%;transform:translateY(-50%);
    width:3px;height:18px;border-radius:2px;background:#5b52ff}

  /* primary 버튼 미세 그라데이션 */
  .btn-primary{background:linear-gradient(180deg,#6760ff,#5b52ff)!important;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.14),0 8px 22px -10px rgba(91,82,255,.65);
    transition:filter .15s,transform .08s}
  .btn-primary:hover{filter:brightness(1.07)}
  .btn-primary:active{transform:translateY(1px)}

  /* 라벨 (위계·여백 리듬) */
  .lbl{display:block;margin-bottom:7px;font-size:11px;font-weight:600;letter-spacing:.05em;
    text-transform:uppercase;color:#6e7191}

  /* 칩 (엔티티/인텐트/카테고리 시각 구분) */
  .chip{display:inline-flex;align-items:center;gap:5px;border-radius:7px;padding:3px 10px;
    font-size:12px;font-weight:500;line-height:1.5;border:1px solid transparent;
    transition:border-color .15s,background .15s,transform .1s}
  .chip:hover{transform:translateY(-1px)}
  .chip-ent{background:linear-gradient(180deg,rgba(91,82,255,.18),rgba(91,82,255,.07));
    border-color:rgba(91,82,255,.34);color:#c4beff}
  .chip-ent::before{content:"";width:5px;height:5px;border-radius:50%;background:#7c74ff;flex:none}
  .chip-int{background:rgba(76,185,167,.12);border-color:rgba(76,185,167,.26);color:#84dccc}
  .chip-cat{background:rgba(255,255,255,.05);border-color:rgba(255,255,255,.09);color:#c9ccd3}

  /* 등급 pill */
  .gpill{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:3px 11px;font-size:12px;font-weight:600}
  .gpill .d{width:6px;height:6px;border-radius:50%;background:currentColor;box-shadow:0 0 7px currentColor}
  .gpill-g{background:rgba(52,211,153,.13);color:#5fe0ad}
  .gpill-r{background:rgba(251,113,133,.13);color:#ff9bab}

  /* 테이블 (엑셀 결과) */
  .tbl{width:100%;border-collapse:separate;border-spacing:0;font-size:13px}
  .tbl th{text-align:left;font-weight:600;font-size:11px;letter-spacing:.05em;text-transform:uppercase;
    color:#6e7191;padding:9px 12px;background:rgba(255,255,255,.025)}
  .tbl td{padding:11px 12px;border-top:1px solid rgba(255,255,255,.06);vertical-align:top;color:#c9ccd3}
  .tbl tbody tr{transition:background .12s}
  .tbl tbody tr:hover{background:rgba(255,255,255,.035)}

  /* 모달 */
  .modal-bg{backdrop-filter:blur(7px);-webkit-backdrop-filter:blur(7px);background:rgba(4,4,8,.6)}
  .modal{box-shadow:0 26px 72px -22px rgba(0,0,0,.85),inset 0 1px 0 rgba(255,255,255,.05)}

  /* details 토글 마커 */
  details>summary{list-style:none}
  details>summary::-webkit-details-marker{display:none}
  details>summary::before{content:"\203A";display:inline-block;width:1em;margin-right:5px;
    transition:transform .15s;color:#6e7191}
  details[open]>summary::before{transform:rotate(90deg)}

  /* 빈 상태 */
  .empty{border:1px dashed rgba(255,255,255,.10);border-radius:12px;padding:40px 24px;text-align:center;color:#6e7191}

  /* 고급 폴리시 */
  html{scroll-behavior:smooth}
  .tnum{font-variant-numeric:tabular-nums}
  .lead,h1,.panel-hd b,.drow .v p{text-wrap:pretty}
  :where(button,a,[role=tab],select,summary):focus-visible{outline:2px solid rgba(124,116,255,.7);
    outline-offset:2px;border-radius:8px}
  /* 노이즈 오버레이 — 평면감 제거(은은) */
  .noise{position:fixed;inset:0;z-index:1;pointer-events:none;opacity:.025;mix-blend-mode:overlay;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='160' height='160' filter='url(%23n)'/%3E%3C/svg%3E")}
  /* 스켈레톤 로딩 */
  .skel{position:relative;overflow:hidden;background:rgba(255,255,255,.04);border-radius:8px}
  .skel::after{content:"";position:absolute;inset:0;
    background:linear-gradient(90deg,transparent,rgba(255,255,255,.06),transparent);
    transform:translateX(-100%);animation:shimmer 1.4s infinite}
  @keyframes shimmer{100%{transform:translateX(100%)}}

  /* 패널 (시안 C — 구조·패널형) */
  .panel{border:1px solid rgba(255,255,255,.08);border-radius:14px;background:#141318;overflow:hidden;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.045);transition:transform .2s cubic-bezier(.32,.72,0,1),border-color .2s}
  .panel:hover{transform:translateY(-1px);border-color:rgba(255,255,255,.14)}
  .panel-hd{display:flex;align-items:center;justify-content:space-between;gap:10px;
    padding:13px 18px;border-bottom:1px solid rgba(255,255,255,.07);background:rgba(255,255,255,.018)}
  .panel-hd b{color:#fff;font-size:13px;font-weight:600;letter-spacing:.01em}
  .panel-hd .meta{font-size:12px;color:#6e7191}
  .panel-bd{padding:18px}
  .drow{display:grid;grid-template-columns:124px 1fr;gap:16px;padding:15px 18px;
    border-bottom:1px solid rgba(255,255,255,.05);align-items:start}
  .drow:last-child{border-bottom:0}
  .drow .k{font-size:12px;font-weight:600;color:#6e7191;padding-top:3px}
  .drow .v{min-width:0}

  /* ── Playground 3분할 셸 ── */
  .shell{display:grid;grid-template-columns:236px minmax(0,1fr) 332px;height:100dvh;overflow:hidden}
  .pane{display:flex;flex-direction:column;min-width:0;min-height:0}
  .pane+.pane{border-left:1px solid rgba(255,255,255,.07)}
  /* 타이틀바: 내용 영역과 명확히 분리(별도 배경·하단 경계). 버튼 없음 — 제목/상태만. */
  .titlebar{flex:none;height:56px;display:flex;align-items:center;gap:9px;padding:0 18px;
    border-bottom:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.03);
    font-size:16px;font-weight:700;color:#fff;letter-spacing:-.01em}
  .titlebar .sub{font-weight:500;color:#6e7191;font-size:12px;letter-spacing:0}
  .titlebar .dot{width:7px;height:7px;border-radius:50%;flex:none}
  /* 로고: 마크(분광 프리즘) + 워드마크 + 기능 태그 */
  .logo-mark{flex:none;width:27px;height:27px;border-radius:8px;display:flex;align-items:center;justify-content:center;
    background:linear-gradient(150deg,#6760ff,#4a42e0);
    box-shadow:0 5px 14px -5px rgba(91,82,255,.75),inset 0 1px 0 rgba(255,255,255,.2)}
  .logo-mark svg{width:17px;height:17px}
  .logo-word{font-size:16px;font-weight:700;letter-spacing:-.02em;color:#fff}
  .logo-sub{font-size:11px;font-weight:500;color:#6e7191;letter-spacing:0;margin-left:-2px}
  .pbody{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden}
  .pbody.pad{padding:18px}
  .pbody.center{padding:26px 30px}

  /* 좌측 내비 그룹 라벨 */
  .navgrp{padding:0 12px;margin:18px 0 6px;font-size:11px;font-weight:600;letter-spacing:.06em;
    text-transform:uppercase;color:#565b66}

  /* 추론강도 세그먼트 */
  .seg{display:flex;gap:3px;padding:3px;border-radius:9px;background:#0d0c12;border:1px solid rgba(255,255,255,.09)}
  .seg button{flex:1;border-radius:6px;padding:6px 0;font-size:12px;font-weight:600;color:#8b909b;
    transition:color .15s,background .15s}
  .seg button.on{background:rgba(91,82,255,.22);color:#c8c3ff;box-shadow:inset 0 1px 0 rgba(255,255,255,.06)}
  .seg button:not(.on):hover{color:#fff}

  /* Configuration 패널 구획 */
  .cfgsec{padding:18px;border-bottom:1px solid rgba(255,255,255,.06)}
  .cfgsec:last-child{border-bottom:0}
  /* 접이식 '연결·모델' 헤더 */
  .acc{display:flex;align-items:center;gap:8px;width:100%;text-align:left;cursor:pointer}
  .acc-t{font-size:13px;font-weight:600;color:#fff;letter-spacing:.01em;flex:none}
  .acc-s{flex:1;min-width:0;text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
    font-size:11.5px;color:#6e7191}
  .acc-chev{flex:none;margin-left:auto;width:16px;height:16px;color:#8b909b;transition:transform .18s}
  .acc-chev.open{transform:rotate(180deg)}
  .acc:hover .acc-t{color:#fff}
  .acc:hover .acc-chev{color:#fff}
  /* 접이식 내부 하위 구획(테두리 없이 간격만) */
  .subsec+.subsec{padding-top:16px;border-top:1px solid rgba(255,255,255,.06)}

  /* 스텝식 설정(텍스트 → 이미지 → 키) */
  .steps{display:flex;gap:6px}
  .steps button{flex:1;display:inline-flex;align-items:center;justify-content:center;gap:5px;height:34px;
    border-radius:8px;font-size:12.5px;font-weight:600;color:#8b909b;background:#0d0c12;
    border:1px solid rgba(255,255,255,.09);transition:color .12s,border-color .12s,background .12s}
  .steps button.on{color:#fff;border-color:rgba(91,82,255,.5);background:rgba(91,82,255,.12)}
  .steps button:not(.on):hover{color:#fff}
  .steps button i{display:flex;align-items:center;justify-content:center;width:17px;height:17px;border-radius:50%;
    font-size:10px;font-weight:700;font-style:normal;background:rgba(255,255,255,.1);color:#c9ccd3}
  .steps button.on i{background:#5b52ff;color:#fff}
  .sdot{width:6px;height:6px;border-radius:50%;flex:none}
  .sdot.ok{background:#5fe0ad;box-shadow:0 0 6px rgba(95,224,173,.7)}
  .sdot.warn{background:#e2a33c}
  .step-nav{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:14px}
  .step-nav button{font-size:12.5px;font-weight:600;color:#b9b3ff;transition:color .12s}
  .step-nav button:hover{color:#fff}
  .step-nav button.back{color:#8b909b}
  .step-nav button.back:hover{color:#fff}
  .keycard{padding:13px;border:1px solid rgba(255,255,255,.08);border-radius:11px;background:rgba(255,255,255,.022)}
  .keycard+.keycard{margin-top:10px}

  /* ── 설정 탭(Atelier 방식): API 키 / 모델 ── */
  .cfgtabs{display:flex;gap:4px;padding:10px 14px 0;border-bottom:1px solid rgba(255,255,255,.07)}
  .cfgtabs button{appearance:none;background:none;border:0;cursor:pointer;padding:9px 14px;border-radius:8px 8px 0 0;
    font-size:13px;font-weight:600;color:#8b909b;position:relative;transition:color .12s}
  .cfgtabs button:hover{color:#fff}
  .cfgtabs button.on{color:#fff}
  .cfgtabs button.on::after{content:"";position:absolute;left:10px;right:10px;bottom:-1px;height:2px;background:#5b52ff;border-radius:2px}
  /* 통합 라우터 카드 */
  .routercard{border:1px solid rgba(91,82,255,.30);border-radius:12px;padding:15px;
    background:linear-gradient(180deg,rgba(91,82,255,.10),rgba(91,82,255,.02))}
  .routercard .rc-h{display:flex;align-items:center;gap:7px;margin-bottom:3px}
  .routercard .rc-h b{font-size:13px;font-weight:700;color:#fff}
  .rc-badge{font-size:9.5px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:#c8c3ff;
    background:rgba(91,82,255,.22);border-radius:5px;padding:2px 6px}
  .routercard .rc-d{margin:0 0 13px;font-size:11.5px;color:#8b909b;line-height:1.55}
  .sectitle{font-size:13px;font-weight:700;color:#fff;margin:20px 0 4px}
  .secdesc{font-size:11.5px;color:#6e7191;margin:0 0 12px;line-height:1.55}
  /* 키 행 */
  .krow+.krow{margin-top:12px;padding-top:12px;border-top:1px solid rgba(255,255,255,.06)}
  .krow-top{display:flex;align-items:center;justify-content:space-between;margin-bottom:7px}
  .krow-nm{font-size:13px;font-weight:600;color:#fff}
  .krow-st{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:#8b909b}
  .keyin{position:relative}
  .keyin input{padding-right:38px}
  .keyin .eye{position:absolute;right:6px;top:50%;transform:translateY(-50%);width:28px;height:28px;display:flex;
    align-items:center;justify-content:center;border:0;background:none;color:#6e7191;cursor:pointer}
  .keyin .eye:hover{color:#fff}
  .keyin .eye svg{width:16px;height:16px}

  /* 히어로 빈 상태 */
  .hero{display:flex;flex-direction:column;align-items:center;justify-content:center;
    text-align:center;padding:64px 24px;border:1px dashed rgba(255,255,255,.10);border-radius:16px;
    background:radial-gradient(420px 200px at 50% 0%,rgba(91,82,255,.08),transparent 70%)}
  .hero .orb{width:54px;height:54px;border-radius:16px;display:flex;align-items:center;justify-content:center;
    background:linear-gradient(180deg,rgba(91,82,255,.28),rgba(91,82,255,.08));
    border:1px solid rgba(91,82,255,.3);box-shadow:0 12px 30px -12px rgba(91,82,255,.6);color:#c8c3ff}
  .schip{display:inline-flex;align-items:center;gap:6px;border-radius:8px;padding:7px 12px;font-size:12.5px;
    font-weight:500;color:#c9ccd3;background:rgba(255,255,255,.045);border:1px solid rgba(255,255,255,.09);
    cursor:pointer;transition:border-color .15s,background .15s,transform .1s}
  .schip:hover{border-color:rgba(91,82,255,.45);background:rgba(91,82,255,.10);transform:translateY(-1px)}

  /* 인포그래픽 — 통계 타일·분포 바·도넛 */
  .tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
  .tile{border-radius:12px;padding:13px 14px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.07)}
  .tile .n{font-size:23px;font-weight:600;color:#fff;line-height:1.1;letter-spacing:-.01em}
  .tile .t{margin-top:3px;font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:#6e7191}
  .bar{display:grid;grid-template-columns:96px 1fr 38px;align-items:center;gap:10px}
  .bar .lab{font-size:12.5px;color:#c9ccd3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .track{height:8px;border-radius:6px;background:rgba(255,255,255,.06);overflow:hidden}
  .track .fill{height:100%;border-radius:6px;background:linear-gradient(90deg,#5b52ff,#7c74ff)}
  .bar .pc{font-size:12px;color:#8b909b;text-align:right}
  .ring{width:108px;height:108px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex:none}
  .ring i{width:78px;height:78px;border-radius:50%;background:#141318;display:flex;flex-direction:column;
    align-items:center;justify-content:center}
  .ring .pv{font-size:21px;font-weight:600;color:#fff;line-height:1}
  .ring .pl{font-size:10px;color:#6e7191;margin-top:2px}
</style>
</head>
<body class="text-body antialiased">
<div class="noise" aria-hidden="true"></div>
<div x-data="prismApp()" class="shell">

  <!-- ━━━━━ 좌측 페인 · 내비게이션 ━━━━━ -->
  <aside class="pane">
    <div class="titlebar">
      <span class="logo-mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke-linecap="round" stroke-linejoin="round">
          <path d="M10 4 4 20h12z" stroke="#fff" stroke-width="1.7"/>
          <path d="M14.5 13h6" stroke="#d2ff95" stroke-width="1.7"/>
          <path d="M14.5 16.5h6" stroke="#a8d4ff" stroke-width="1.7"/>
        </svg>
      </span>
      <span class="logo-word">Prism</span>
    </div>
    <div class="pbody pad">
      <template x-for="grp in mods" x-bind:key="grp.g">
        <div>
          <div class="navgrp" x-text="grp.g"></div>
          <nav class="space-y-0.5">
            <template x-for="it in grp.items" x-bind:key="it.id">
              <button type="button" x-on:click="selectMod(it.id)"
                class="navitem relative flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors"
                x-bind:class="mod === it.id ? 'active bg-white/[0.07] text-white font-medium' : 'text-body hover:bg-white/[0.04] hover:text-white'">
                <svg class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path x-bind:d="it.icon"></path></svg>
                <span x-text="it.label"></span>
                <span class="ml-auto h-1.5 w-1.5 rounded-full"
                  x-bind:class="it.cov === 'done' ? 'bg-emerald-400' : (it.cov === 'poc' ? 'bg-violet-400' : 'bg-amber-400')"></span>
              </button>
            </template>
          </nav>
        </div>
      </template>
      <div class="navgrp">산출</div>
      <a href="/report" target="_blank" rel="noreferrer"
        class="navitem relative flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm text-body transition-colors hover:bg-white/[0.04] hover:text-white">
        <svg class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M7 7h10v10"/></svg>
        <span>전체 리포트 ↗</span>
      </a>
      <div class="mt-5 border-t border-white/[0.07] pt-3 px-1 text-[10.5px] leading-relaxed text-muted">
        <div class="font-semibold mb-1">상태 · 실증 단계</div>
        <div class="flex items-center gap-1.5"><span class="h-1.5 w-1.5 rounded-full bg-emerald-400"></span> 실동작</div>
        <div class="flex items-center gap-1.5"><span class="h-1.5 w-1.5 rounded-full bg-violet-400"></span> PoC · 부분</div>
        <div class="flex items-center gap-1.5"><span class="h-1.5 w-1.5 rounded-full bg-amber-400"></span> 기획</div>
      </div>
    </div>
  </aside>

  <!-- ━━━━━ 가운데 페인 · 활성 모듈 ━━━━━ -->
  <main class="pane">
    <div class="titlebar">
      <span x-text="modLabel"></span>
      <span class="ml-auto inline-flex items-center gap-1.5">
        <span class="dot" x-bind:class="(textReady && !cfg.forcedMock) ? 'bg-solar' : 'bg-amber-400'"></span>
        <span class="sub" x-text="cfg.forcedMock ? 'MOCK(강제)' : (textReady ? (providerLabels[textProvider] + ' 연결됨') : 'MOCK · 키 미설정')"></span>
      </span>
    </div>
    <div class="pbody center">
      <!-- ═══ 모듈: 실행 · 추출 ═══ -->
      <div x-show="mod === 'run'" class="mx-auto max-w-3xl">
        <!-- 입력 방식 -->
        <div class="seg seg3 mb-5">
          <template x-for="t in tabItems" x-bind:key="t.id">
            <button type="button" x-on:click="selectTab(t.id)" x-bind:class="activeTabId === t.id ? 'on' : ''" x-text="t.label"></button>
          </template>
        </div>

        <!-- 입력 카드 -->
        <section class="panel">
          <div class="panel-bd">
          <!-- 콘텐츠 그룹: 입력 시 지정(이미지·텍스트 공용). 엑셀은 컬럼에서 자동 -->
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
            <div class="ds-dark ds-pilot">
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
            <div class="flex items-center justify-between gap-2 rounded-lg border border-white/[0.08] bg-white/[0.02] px-3 py-2.5">
              <div class="text-xs text-muted">컬럼 양식 · <span class="text-body">콘텐츠 그룹 · 제목 · 부제 · 본문</span> (제목·본문 필수)</div>
              <a href="/template.csv" download
                class="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-white/[0.12] px-2.5 py-1 text-xs font-medium text-white transition-colors hover:bg-white/[0.06]">
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
              <p class="mt-1.5 text-xs text-muted">행마다 한 콘텐츠로 일괄 추출합니다. 컬럼명이 제목/본문/서비스명과 달라도 자동 추론합니다. (최대 200행)</p>
            </div>
          </div>

          <div class="mt-5 flex items-center gap-3">
            <button type="button" x-on:click="run()" x-bind:disabled="loading"
              class="btn-primary inline-flex items-center gap-2 rounded-lg bg-violet px-5 py-2.5 text-sm font-medium text-white disabled:opacity-50">
              <svg x-show="loading" x-cloak class="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z"/></svg>
              <span x-text="loading ? '실행 중' : '추출 실행'"></span>
            </button>
            <span class="text-xs text-muted">모델 · 추론 강도는 우측 설정 패널에서 변경</span>
            <span aria-live="polite" class="ml-auto text-sm text-rose-400" x-text="status"></span>
          </div>
          </div>
        </section>

        <!-- 빈 상태 (HeroEmpty: 캐릭터 + 추천 칩) -->
        <div x-show="!result && !batchResult && !loading" x-cloak class="mt-6">
          <div class="ds-dark ds-pilot ds-hero">
            <span class="ds-character ds-character--bob" style="width:104px;height:104px"><img src="/vendor/daesik-batter.svg" alt="대식 (타자)"></span>
            <h2 class="ds-hero__title">콘텐츠에서 리드문과 메타를 추출합니다</h2>
            <p class="ds-hero__desc">이미지·텍스트·엑셀을 입력하면 리드문(요약 한 문장)·엔티티·인텐트·콘텐츠 카테고리가 한 방향으로 정리됩니다.</p>
            <div class="ds-hero__chips">
              <button type="button" class="ds-attachment" x-on:click="selectTab('image')" style="cursor:pointer">
                <span class="ds-attachment__icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.6-3.6a2 2 0 0 0-2.8 0L6 20"/></svg></span>
                <span class="ds-attachment__name">이미지에서 추출</span>
              </button>
              <button type="button" class="ds-attachment" x-on:click="selectTab('text')" style="cursor:pointer">
                <span class="ds-attachment__icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M4 7V5h16v2M9 5v14m-3 0h6"/></svg></span>
                <span class="ds-attachment__name">기사 본문 붙여넣기</span>
              </button>
              <button type="button" class="ds-attachment" x-on:click="selectTab('excel')" style="cursor:pointer">
                <span class="ds-attachment__icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 3v18"/></svg></span>
                <span class="ds-attachment__name">엑셀 일괄 처리</span>
              </button>
            </div>
          </div>
        </div>

        <!-- 처리 대기 화면: Processing(캐릭터) + Steps (디자인 시스템) -->
        <div x-show="loading" x-cloak class="ds-dark ds-pilot mt-6 panel" style="border-color:var(--ds-hairline)">
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
                <div class="ring" x-bind:style="'background:conic-gradient(#5b52ff ' + batchStats.gPct + '%, rgba(255,255,255,.07) 0)'">
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
                <span x-show="batchResult && batchResult.mock" class="inline-flex items-center rounded-md bg-amber-500/15 px-2 py-0.5 text-xs font-semibold text-amber-300">MOCK</span>
              </div>
              <button type="button" class="copybtn" x-on:click="exportBatchCsv()">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>
                CSV 내보내기
              </button>
            </div>
            <div class="overflow-auto">
              <table class="tbl">
                <thead>
                  <tr><th>제목</th><th>리드문</th><th>엔티티</th><th>등급</th></tr>
                </thead>
                <tbody>
                  <template x-for="(it, i) in (batchResult ? batchResult.items : [])" x-bind:key="i">
                    <tr>
                      <td class="text-white" x-text="it.title || '—'"></td>
                      <td x-text="it.summary || '—'"></td>
                      <td><div class="flex flex-wrap gap-1"><template x-for="e in (it.entities || [])" x-bind:key="e"><span class="chip chip-ent" x-text="e"></span></template><span x-show="!(it.entities||[]).length">—</span></div></td>
                      <td><span class="gpill" x-bind:class="it.grade === 'G' ? 'gpill-g' : 'gpill-r'"><span class="d"></span><span x-text="it.grade || '—'"></span></span></td>
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
                <span x-show="q.finalGrade === 'G'" class="gpill gpill-g"><span class="d"></span>유통가능 · G</span>
                <span x-show="q.finalGrade !== 'G'" class="gpill gpill-r"><span class="d"></span>차단 · R</span>
                <span class="meta" x-text="result ? (result.output.routing.content_track + ' · ' + result.source) : ''"></span>
              </div>
            </div>
            <div class="drow">
              <div class="k">리드문</div>
              <div class="v">
                <div class="flex items-start gap-2">
                  <p class="flex-1 text-[15px] leading-relaxed text-white" x-text="im.summary || '(빈 값 — 차단되었거나 본문 부족)'"></p>
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
                <template x-for="x in (im.entities || [])" x-bind:key="x"><span class="chip chip-ent" x-text="x"></span></template>
                <span x-show="!(im.entities || []).length" class="text-xs text-muted">—</span>
              </div>
            </div>
            <div class="drow">
              <div class="k">인텐트</div>
              <div class="v flex flex-wrap gap-1.5">
                <template x-for="x in (im.intent || [])" x-bind:key="x"><span class="chip chip-int" x-text="x"></span></template>
                <span x-show="!(im.intent || []).length" class="text-xs text-muted">—</span>
              </div>
            </div>
            <div class="drow">
              <div class="k">콘텐츠 카테고리</div>
              <div class="v flex flex-wrap gap-1.5">
                <template x-for="x in contentCats" x-bind:key="x"><span class="chip chip-cat" x-text="x"></span></template>
                <span x-show="!contentCats.length" class="text-xs text-muted">—</span>
              </div>
            </div>
          </section>

          <!-- 이미지 추출 신호 -->
          <section x-show="result && result.signals && result.signals.length" x-cloak class="panel">
            <div class="panel-hd"><b>이미지 추출 신호</b><span class="meta tnum" x-text="result ? (result.signals.length + '장') : ''"></span></div>
            <div class="panel-bd space-y-3">
              <template x-for="(s, i) in (result ? result.signals : [])" x-bind:key="i">
                <div class="border-l border-white/[0.10] pl-3">
                  <div class="text-xs font-semibold text-white" x-text="'이미지 ' + (i + 1)"></div>
                  <div class="mt-1 flex gap-2 text-sm text-body">
                    <svg class="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
                    <span x-text="s.vision || '—'"></span>
                  </div>
                  <div class="flex gap-2 text-sm text-body">
                    <svg class="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7V5h16v2M9 5v14m-3 0h6"/></svg>
                    <span x-text="s.ocr || '—'"></span>
                  </div>
                  <p x-show="s.note" x-cloak class="mt-1 text-xs text-amber-300/90" x-text="s.note"></p>
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
                   class="inline-flex items-center gap-1.5 text-xs font-medium text-[#b9b3ff] transition-colors hover:text-white">
                  전체 리포트 열기
                  <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M7 7h10v10"/></svg>
                </a>
              </div>
            </div>
            <div class="panel-bd">
              <details class="group">
                <summary class="cursor-pointer text-sm text-body transition-colors hover:text-white">합성된 Content (이미지 → 4필드)</summary>
                <pre class="mt-2 overflow-auto rounded-lg border border-white/[0.08] bg-canvas p-3 font-mono text-xs text-body" x-text="result ? JSON.stringify(result.content, null, 2) : ''"></pre>
              </details>
              <details class="group mt-2">
                <summary class="cursor-pointer text-sm text-body transition-colors hover:text-white">원본 출력 JSON</summary>
                <pre class="mt-2 overflow-auto rounded-lg border border-white/[0.08] bg-canvas p-3 font-mono text-xs text-body" x-text="result ? JSON.stringify(result.output, null, 2) : ''"></pre>
              </details>
            </div>
          </section>
        </div>
      </div>

      <!-- ═══ 모듈: 대시보드 ═══ -->
      <div x-show="mod === 'dash'" x-cloak class="mx-auto max-w-4xl">
        <div x-show="!dashData || !dashData.n" class="empty">아직 집계할 결과가 없습니다. <b class="text-body">실행 · 추출</b>에서 추출(엑셀 일괄 권장)을 먼저 실행하세요.</div>
        <div x-show="dashData && dashData.n" class="space-y-4">
          <div class="tiles">
            <div class="tile"><div class="n tnum" x-text="(dashData?dashData.gPct:0) + '%'"></div><div class="t">유통 가능 G</div></div>
            <div class="tile"><div class="n tnum" x-text="dashData?dashData.n:0"></div><div class="t">처리 건수</div></div>
            <div class="tile"><div class="n tnum" x-text="dashData?dashData.entities:0"></div><div class="t">엔티티 수</div></div>
            <div class="tile"><div class="n tnum" x-text="dashData?dashData.avgLead:0"></div><div class="t">평균 리드문(자)</div></div>
          </div>
          <div class="flex items-center gap-6 panel"><div class="panel-bd flex items-center gap-6 w-full">
            <div class="ring" x-bind:style="'background:conic-gradient(#5fe0ad ' + (dashData?dashData.gPct:0) + '%, rgba(255,255,255,.07) 0)'">
              <i><span class="pv tnum" x-text="(dashData?dashData.gPct:0)+'%'"></span><span class="pl">유통 가능</span></i></div>
            <div class="min-w-0 flex-1">
              <div class="lbl" style="margin-bottom:2px">인텐트 분포</div>
              <template x-for="it in (dashData?dashData.intents:[])" x-bind:key="it.k">
                <div class="bar"><span class="lab" x-text="it.k"></span><span class="track"><span class="fill" x-bind:style="'width:'+Math.max(it.pct,4)+'%'"></span></span><span class="pc tnum" x-text="it.v"></span></div>
              </template>
            </div>
          </div></div>
          <div class="panel"><div class="panel-hd"><b>콘텐츠 카테고리 분포</b><span class="meta">상위</span></div><div class="panel-bd">
            <template x-for="it in (dashData?dashData.categories:[])" x-bind:key="it.k">
              <div class="bar"><span class="lab" x-text="it.k"></span><span class="track"><span class="fill" x-bind:style="'width:'+Math.max(it.pct,4)+'%'"></span></span><span class="pc tnum" x-text="it.v"></span></div>
            </template>
            <p x-show="dashData && dashData.qualityReasons && dashData.qualityReasons.length" class="mt-3 text-xs text-muted">품질 사유 상위: <span x-text="(dashData?dashData.qualityReasons:[]).map(x=>x.k+'('+x.v+')').join(' · ')"></span></p>
          </div></div>
        </div>
      </div>

      <!-- ═══ 모듈: 인입 · 적용대상 ═══ -->
      <div x-show="mod === 'intake'" x-cloak class="mx-auto max-w-4xl space-y-4">
        <div class="panel"><div class="panel-hd"><b>ITEM TYPE 처리 정책</b><span class="meta">131</span></div>
          <div class="overflow-auto"><table class="tbl"><thead><tr><th>ITEM TYPE</th><th>필터 대상</th><th>처리 방식</th><th>상태</th></tr></thead><tbody>
            <tr><td class="text-white">텍스트형</td><td>O</td><td>정상 분류(품질 메타 부여)</td><td><span class="gpill gpill-g"><span class="d"></span>구현</span></td></tr>
            <tr><td class="text-white">이미지형</td><td>△</td><td>GREEN 일괄 + 캡션 텍스트(시각 이해)</td><td><span class="chip chip-int">PoC</span></td></tr>
            <tr><td class="text-white">영상형</td><td>X</td><td>GREEN 일괄(Argos 별도)</td><td><span class="text-xs text-muted">계획</span></td></tr>
            <tr><td class="text-white">SNS형</td><td>X</td><td>서비스 자체 필터 후 인입</td><td><span class="text-xs text-muted">계획</span></td></tr>
            <tr><td class="text-white">묶음형 · 데이터형</td><td>X</td><td>GREEN 일괄(고도화 과제)</td><td><span class="text-xs text-muted">계획</span></td></tr>
          </tbody></table></div>
        </div>
        <div class="panel"><div class="panel-hd"><b>콘텐츠 출처 분류</b></div><div class="panel-bd">
          <div class="flex flex-wrap gap-1.5"><span class="chip chip-cat">PGC 기존 미디어</span><span class="chip chip-cat">UGC 사용자 생성</span><span class="chip chip-cat">AIGC AI 생성</span><span class="chip chip-cat">AIEC AI 보정</span></div>
          <p class="mt-2 text-xs text-muted">식별 표준 · C2PA(자격 증명) · SynthID(워터마크). 발행자 정보로 PGC/UGC 1차 식별.</p>
        </div></div>
      </div>

      <!-- ═══ 모듈: 품질 메타 ═══ -->
      <div x-show="mod === 'quality'" x-cloak class="mx-auto max-w-3xl space-y-4">
        <div class="panel"><div class="panel-bd flex items-center justify-between gap-3">
          <div class="text-xs text-muted">법령 1차 필터(13종 위반 라우팅·스코어링)를 추출에 포함합니다. 켜면 다음 추출부터 적용(추가 호출).</div>
          <label class="inline-flex cursor-pointer items-center gap-2 text-[13px] text-body">
            <input type="checkbox" x-model="legalEnabled" x-on:change="toggleLegal()" class="h-4 w-4 rounded border-white/20 bg-canvas text-violet">
            법령 필터 포함
          </label>
        </div></div>
        <div x-show="!result" class="empty"><b class="text-body">실행 · 추출</b>에서 단건 추출을 실행하면 그 콘텐츠의 품질·법령 판정 상세가 여기에 표시됩니다.</div>
        <div x-show="result" class="space-y-4">
          <div class="panel"><div class="panel-hd"><b>유통 판정</b>
            <span x-show="qm.finalGrade === 'G'" class="gpill gpill-g"><span class="d"></span>유통 가능 · G</span>
            <span x-show="qm.finalGrade !== 'G'" class="gpill gpill-r"><span class="d"></span>차단 · R</span>
          </div><div class="panel-bd">
            <div class="drow"><div class="k">검수</div><div class="v text-sm text-body" x-text="(qm.review || 'auto') + (qm.confidence != null ? (' · conf ' + qm.confidence) : '')"></div></div>
            <div class="drow"><div class="k">품질 사유</div><div class="v flex flex-wrap gap-1.5">
              <template x-for="r in (qm.reasons || [])" x-bind:key="r"><span class="chip chip-cat" x-text="r"></span></template>
              <span x-show="!(qm.reasons || []).length" class="text-xs text-muted">없음(통과)</span>
            </div></div>
            <div class="drow"><div class="k">법령</div><div class="v">
              <span class="text-sm text-body" x-text="lm.enabled ? ('대표등급 ' + lm.representative_grade + ' · ' + lm.representative_score) : '법령 필터 비활성(옵션)'"></span>
              <div class="mt-1.5 flex flex-wrap gap-1.5"><template x-for="h in (lm.harm_types || [])" x-bind:key="h.code"><span class="chip chip-int" x-text="h.code + ' · ' + h.grade"></span></template></div>
            </div></div>
          </div></div>
        </div>
      </div>

      <!-- ═══ 모듈: 토픽 ═══ -->
      <div x-show="mod === 'topic'" x-cloak class="mx-auto max-w-4xl space-y-4">
        <div x-show="!topicData || !topicData.n_contents" class="empty">아직 토픽을 만들 결과가 없습니다. <b class="text-body">실행 · 추출</b>에서 여러 건(엑셀 일괄)을 추출하세요.</div>
        <div x-show="topicData && topicData.n_contents" class="space-y-4">
          <div class="tiles" style="grid-template-columns:repeat(3,1fr)">
            <div class="tile"><div class="n tnum" x-text="topicData?(topicData.summary.single||0):0"></div><div class="t">엔티티형</div></div>
            <div class="tile"><div class="n tnum" x-text="topicData?(topicData.summary.composite||0):0"></div><div class="t">사건형</div></div>
            <div class="tile"><div class="n tnum" x-text="topicData?(topicData.summary.filter||0):0"></div><div class="t">조건형</div></div>
          </div>
          <div class="panel"><div class="panel-hd"><b>엔티티형 · 사건형 토픽</b><span class="meta tnum" x-text="topicData ? (topicData.n_contents + '건 기준') : ''"></span></div>
            <div class="overflow-auto"><table class="tbl"><thead><tr><th>유형</th><th>클러스터</th><th>대표 엔티티</th><th>멤버</th></tr></thead><tbody>
              <template x-for="t in (topicData?topicData.single:[])" x-bind:key="t.cluster_id"><tr><td>엔티티형</td><td class="text-white" x-text="t.cluster_id"></td><td x-text="(t.entities||t.rep_entities||[]).join(' · ')"></td><td x-text="t.n_contents || (t.contents?t.contents.length:'')"></td></tr></template>
              <template x-for="t in (topicData?topicData.composite:[])" x-bind:key="t.cluster_id"><tr><td>사건형</td><td class="text-white" x-text="t.cluster_id"></td><td x-text="(t.rep_entities||t.entities||[]).join(' · ')"></td><td x-text="t.n_contents || (t.contents?t.contents.length:'')"></td></tr></template>
              <template x-if="!(topicData&&(topicData.single.length||topicData.composite.length))"><tr><td colspan="4" class="text-muted">엔티티 공유 클러스터 없음(데이터가 많을수록 형성)</td></tr></template>
            </tbody></table></div>
          </div>
          <div class="panel"><div class="panel-hd"><b>조건형 토픽</b><span class="meta">관심사 × 소비 방식</span></div><div class="panel-bd flex flex-wrap gap-1.5">
            <template x-for="t in (topicData?topicData.filter:[])" x-bind:key="t.cluster_id"><span class="chip" x-bind:class="t.active ? 'chip-ent' : 'chip-cat'" x-text="(t.name||t.label) + (t.active?(' · '+(t.n_contents||'')):'')"></span></template>
          </div></div>
        </div>
      </div>

      <!-- ═══ 모듈: 사전 · 매핑 ═══ -->
      <div x-show="mod === 'dict'" x-cloak class="mx-auto max-w-4xl space-y-4">
        <div class="panel"><div class="panel-bd flex items-center justify-between gap-3">
          <div class="text-xs text-muted">각 체계의 정책(사전·카테고리·법령)을 <span class="text-body">직접 수정</span>할 수 있습니다. 저장 시 즉시 추출에 반영되고 로컬에 영속됩니다.</div>
          <button type="button" x-on:click="resetDict()" class="rounded-md border border-rose-500/30 px-3 py-1.5 text-xs font-medium text-rose-300 hover:bg-rose-500/10">편집 초기화</button>
        </div></div>

        <!-- 인라인 편집 바 -->
        <div x-show="editT" x-cloak class="panel" style="border-color:rgba(91,82,255,.4)"><div class="panel-bd">
          <div class="flex items-center justify-between mb-2"><b class="text-sm text-white" x-text="'편집 · ' + editTitle"></b>
            <span class="text-xs text-muted" x-text="editKind==='list' ? '한 줄에 하나씩' : '텍스트'"></span></div>
          <textarea x-model="editVal" rows="6" class="field" style="height:auto;padding:11px 12px"></textarea>
          <div class="mt-2 flex items-center gap-2">
            <button type="button" x-on:click="saveEdit()" class="rounded-lg bg-violet px-3.5 py-1.5 text-[13px] font-medium text-white hover:bg-violet-hover">저장</button>
            <button type="button" x-on:click="cancelEdit()" class="rounded-lg border border-white/[0.10] px-3.5 py-1.5 text-[13px] font-medium text-white hover:bg-white/[0.05]">취소</button>
            <span class="text-xs text-muted" x-text="editMsg"></span>
          </div>
        </div></div>

        <div x-show="dictData" class="space-y-4">
          <div class="panel"><div class="panel-hd"><b>인텐트 · 범용(8)</b>
            <button type="button" class="copybtn" x-on:click="startEdit('intent_universal', null, dictData.intentUniversal, 'list', '인텐트 범용')">편집</button>
          </div><div class="panel-bd flex flex-wrap gap-1.5">
            <template x-for="i in (dictData?dictData.intentUniversal:[])" x-bind:key="i"><span class="chip chip-int" x-text="i"></span></template>
          </div></div>
          <div class="panel"><div class="panel-hd"><b>인텐트 · 서비스별</b>
            <select x-model="dictGroup" class="field" style="width:auto;height:32px;padding:0 28px 0 10px">
              <template x-for="g in (dictData?dictData.serviceGroups:[])" x-bind:key="g"><option x-bind:value="g" x-text="g"></option></template>
            </select>
            <button type="button" class="copybtn" x-on:click="startEdit('intent_by_service', dictGroup, (dictData.intentByService[dictGroup]||[]), 'list', '인텐트 · ' + dictGroup)">편집</button>
          </div><div class="panel-bd flex flex-wrap gap-1.5">
            <template x-for="i in (dictData && dictData.intentByService[dictGroup] ? dictData.intentByService[dictGroup] : [])" x-bind:key="i"><span class="chip chip-int" x-text="i"></span></template>
            <span x-show="!(dictData && dictData.intentByService[dictGroup] && dictData.intentByService[dictGroup].length)" class="text-xs text-muted">항목 없음</span>
          </div></div>
          <div class="panel"><div class="panel-hd"><b>콘텐츠 카테고리 · Tier1 / Tier2</b><span class="meta tnum" x-text="dictData ? (dictData.iabTier1.length + ' Tier1') : ''"></span>
            <button type="button" class="copybtn ml-auto" x-on:click="startEdit('iab_tier1', null, dictData.iabTier1, 'list', 'Tier1 목록')">Tier1 편집</button>
          </div>
            <div class="panel-bd space-y-2.5" style="max-height:340px;overflow:auto">
              <template x-for="c in (dictData?dictData.iabTier1:[])" x-bind:key="c">
                <div>
                  <div class="flex items-center gap-2 mb-1">
                    <div class="text-[13px] font-semibold text-white" x-text="c"></div>
                    <button type="button" class="text-[11px] text-muted hover:text-white" x-on:click="startEdit('tier2', c, (dictData.tier2[c]||[]), 'list', 'Tier2 · ' + c)">편집</button>
                  </div>
                  <div class="flex flex-wrap gap-1.5">
                    <template x-for="t2 in (dictData && dictData.tier2[c] ? dictData.tier2[c] : [])" x-bind:key="t2"><span class="chip chip-cat" x-text="t2"></span></template>
                  </div>
                </div>
              </template>
            </div>
          </div>
          <div class="grid grid-cols-2 gap-4">
            <div class="panel"><div class="panel-hd"><b>도메인 그룹</b><span class="meta">Tier1 7묶음</span></div><div class="panel-bd space-y-2">
              <template x-for="(ts,g) in (dictData?dictData.domainGroups:{})" x-bind:key="g">
                <div><span class="chip chip-ent" x-text="g"></span> <span class="text-xs text-muted" x-text="ts.join(' · ')"></span></div>
              </template>
            </div></div>
            <div class="panel"><div class="panel-hd"><b>자사 ↔ IAB v3.0 매핑</b><span class="meta tnum" x-text="dictData?Object.keys(dictData.iabMap).length+'건':''"></span></div>
              <div class="overflow-auto" style="max-height:280px"><table class="tbl"><thead><tr><th>자사 경로</th><th>IAB 공식</th></tr></thead><tbody>
                <template x-for="(v,k) in (dictData?dictData.iabMap:{})" x-bind:key="k"><tr><td class="text-white" x-text="k"></td><td class="text-muted" x-text="v"></td></tr></template>
              </tbody></table></div></div>
          </div>
          <div class="grid grid-cols-2 gap-4">
            <div class="panel"><div class="panel-hd"><b>품질 메타</b><span class="meta tnum" x-text="dictData?Object.keys(dictData.qualityMetas).length+'종':''"></span></div>
              <div class="overflow-auto" style="max-height:280px"><table class="tbl"><thead><tr><th>ID</th><th>메타명 · 정의</th><th>적용</th><th></th></tr></thead><tbody>
                <template x-for="(v,k) in (dictData?dictData.qualityMetas:{})" x-bind:key="k"><tr>
                  <td class="text-white" x-text="k"></td>
                  <td><span class="text-white" x-text="(dictData.qualityNames&&dictData.qualityNames[k])||''"></span> <span class="text-muted" x-text="v"></span></td>
                  <td><span class="chip" x-bind:class="(dictData.qualityApplies&&dictData.qualityApplies[k]==='ugc')?'chip-int':'chip-cat'" x-text="(dictData.qualityApplies&&dictData.qualityApplies[k]==='ugc')?'UGC':'전체'"></span></td>
                  <td><button type="button" class="text-[11px] text-muted hover:text-white" x-on:click="startEdit('quality_metas', k, v, 'text', '품질 · ' + k)">편집</button></td>
                </tr></template>
              </tbody></table></div></div>
            <div class="panel"><div class="panel-hd"><b>법령 위반 유형</b><span class="meta tnum" x-text="dictData?Object.keys(dictData.legalTypes).length+'종':''"></span></div>
              <div class="overflow-auto" style="max-height:280px"><table class="tbl"><thead><tr><th>코드</th><th>유형</th><th>근거</th><th></th></tr></thead><tbody>
                <template x-for="(v,k) in (dictData?dictData.legalTypes:{})" x-bind:key="k"><tr><td class="text-white" x-text="k"></td><td x-text="v.label"></td><td class="text-muted" x-text="v.article"></td><td><button type="button" class="text-[11px] text-muted hover:text-white" x-on:click="startEditLegal(k, v)">편집</button></td></tr></template>
              </tbody></table></div></div>
          </div>
        </div>
      </div>

      <!-- ═══ 모듈: 사용자 메타 ═══ -->
      <div x-show="mod === 'user'" x-cloak class="mx-auto max-w-4xl space-y-4">
        <div class="panel"><div class="panel-bd">
          <div class="flex items-center justify-between gap-3 flex-wrap">
            <div class="text-xs text-muted">행동 로그(TIARA형)를 올리면 추출 콘텐츠와 조인해 <span class="text-body">소비 형태 · 강도 · 선호</span>를 산출합니다. <code class="text-[#b9b3ff]">content_id</code> = 추출 순서(0부터).</div>
            <div class="flex items-center gap-2">
              <a href="/usermeta-template.csv" download class="inline-flex items-center gap-1.5 rounded-md border border-white/[0.12] px-2.5 py-1 text-xs font-medium text-white hover:bg-white/[0.06]">템플릿</a>
              <label class="inline-flex cursor-pointer items-center gap-1.5 rounded-md bg-violet px-3 py-1.5 text-xs font-semibold text-white hover:bg-violet-hover">
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
              <span class="chip chip-ent" x-text="u.persona"></span>
              <span class="meta tnum ml-auto" x-text="'조회 ' + u.engagement.views + ' · 클릭률 ' + u.engagement.click_rate + ' · 평균체류 ' + u.engagement.avg_dwell_sec + 's'"></span>
            </div><div class="panel-bd">
              <div class="drow"><div class="k">소비 형태</div><div class="v text-sm text-body" x-text="Object.entries(u.form).map(e=>e[0]+':'+e[1]).join(' · ')"></div></div>
              <div class="drow"><div class="k">소비 강도</div><div class="v flex flex-wrap gap-1.5">
                <template x-for="(v,k) in u.intensity" x-bind:key="k"><span class="chip" x-bind:class="v==='고'?'chip-ent':(v==='중'?'chip-int':'chip-cat')" x-text="k + ' (' + v + ')'"></span></template>
              </div></div>
              <div class="drow"><div class="k">선호 엔티티</div><div class="v flex flex-wrap gap-1.5">
                <template x-for="e in (u.affinity_entities||[])" x-bind:key="e[0]"><span class="chip chip-ent" x-text="e[0]"></span></template>
                <span x-show="!(u.affinity_entities||[]).length" class="text-xs text-muted">—</span>
              </div></div>
            </div></div>
          </template>
        </div>

        <!-- 명세(페르소나 정의·공식) -->
        <div class="panel"><div class="panel-hd"><b>페르소나 정의 · 8종</b><span class="meta">형태 + 맥락별 강도 시그니처</span></div>
          <div class="overflow-auto"><table class="tbl"><thead><tr><th>페르소나</th><th>설명</th><th>형태(깊이·체류)</th></tr></thead><tbody>
            <template x-for="p in (userData?userData.personas_def:[])" x-bind:key="p.id">
              <tr><td class="text-white" x-text="p.full || p.name"></td><td x-text="p.desc"></td><td x-text="(p.form['깊이']||'') + ' · ' + (p.form['체류·완주']||'')"></td></tr>
            </template>
            <template x-if="!(userData&&userData.personas_def&&userData.personas_def.length)"><tr><td colspan="3" class="text-muted">먼저 [실행·추출]에서 콘텐츠를 추출하세요.</td></tr></template>
          </tbody></table></div>
        </div>
        <p class="text-xs text-muted" x-show="userData && userData.formula" x-text="userData ? userData.formula : ''"></p>
      </div>

      <!-- ═══ 모듈: 검증 · 평가 ═══ -->
      <div x-show="mod === 'eval'" x-cloak class="mx-auto max-w-3xl space-y-4">
        <div x-show="!result" class="empty"><b class="text-body">실행 · 추출</b>에서 단건 추출을 실행하면 그 콘텐츠의 검증(trace·fallback·비용)이 표시됩니다.</div>
        <div x-show="result" class="space-y-4">
          <div class="panel"><div class="panel-hd"><b>추출 trace</b><span class="meta" x-text="tr.prompt_version || ''"></span></div><div class="panel-bd">
            <div class="drow"><div class="k">fallback</div><div class="v flex flex-wrap gap-1.5">
              <template x-for="f in (tr.fallbacks || [])" x-bind:key="f"><span class="chip chip-cat" x-text="f"></span></template>
              <span x-show="!(tr.fallbacks||[]).length" class="text-xs text-muted">없음</span>
            </div></div>
            <div class="drow"><div class="k">검증 verdict</div><div class="v flex flex-wrap gap-1.5">
              <template x-for="(v,i) in (tr.agent_verdicts || [])" x-bind:key="i"><span class="chip chip-int" x-text="(typeof v==='string')?v:JSON.stringify(v)"></span></template>
              <span x-show="!(tr.agent_verdicts||[]).length" class="text-xs text-muted">없음</span>
            </div></div>
            <div class="drow"><div class="k">비용 · 토큰</div><div class="v text-sm text-body tnum" x-text="'$' + (tr.cost_usd||0).toFixed(4) + ' · ' + JSON.stringify(tr.tokens||{})"></div></div>
            <div class="drow"><div class="k">지연(ms)</div><div class="v text-sm text-body tnum" x-text="JSON.stringify(tr.latency_ms||{})"></div></div>
          </div></div>
          <p class="text-xs text-muted">정량 평가(ROUGE·정확도 게이트)는 정답셋 연동 시 활성화됩니다(계획).</p>
        </div>
      </div>

    </div>
  </main>

  <!-- ━━━━━ 우측 페인 · Configuration ━━━━━ -->
  <aside class="pane">
    <div class="titlebar"><span>설정</span></div>
    <div class="pbody">

      <!-- 탭: API 키 / 모델 (Atelier 방식) -->
      <div class="cfgtabs">
        <button type="button" x-on:click="cfgTab = 'keys'" x-bind:class="cfgTab === 'keys' ? 'on' : ''">API 키</button>
        <button type="button" x-on:click="cfgTab = 'models'" x-bind:class="cfgTab === 'models' ? 'on' : ''">모델</button>
      </div>

      <!-- ① API 키 -->
      <div x-show="cfgTab === 'keys'" class="cfgsec">
        <!-- 통합 라우터 카드 -->
        <div class="routercard">
          <div class="rc-h"><b>통합 라우터</b><span class="rc-badge">권장</span></div>
          <p class="rc-d">한 키로 여러 모델(OpenAI · Anthropic · Google · Solar 등)을 호출합니다.</p>
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
                  class="rounded-lg bg-violet px-3.5 py-1.5 text-[13px] font-medium text-white hover:bg-violet-hover disabled:opacity-50" x-text="keyState(s) ? '변경' : '저장'"></button>
                <button type="button" x-show="keyPersisted(s)" x-on:click="forgetKey(s)"
                  class="rounded-lg border border-rose-500/30 px-3.5 py-1.5 text-[13px] font-medium text-rose-300 hover:bg-rose-500/10">삭제</button>
                <span class="text-xs text-muted" aria-live="polite" x-text="keyMsgs[s]"></span>
              </div>
            </div>
          </template>
        </div>

        <!-- 직접 호출(Solar) -->
        <div class="sectitle">직접 호출</div>
        <p class="secdesc">각 회사 키로 직접 호출합니다. 통합 라우터와 함께 등록해도 됩니다.</p>
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
              class="rounded-lg bg-violet px-3.5 py-1.5 text-[13px] font-medium text-white hover:bg-violet-hover disabled:opacity-50" x-text="cfg.hasKey ? '변경' : '저장'"></button>
            <button type="button" x-show="cfg.hasKey" x-on:click="testConn()" x-bind:disabled="cfgBusy"
              class="rounded-lg border border-white/[0.10] px-3.5 py-1.5 text-[13px] font-medium text-white hover:bg-white/[0.05] disabled:opacity-50">연결 테스트</button>
            <button type="button" x-show="cfg.persisted" x-on:click="forgetKey('solar')"
              class="rounded-lg border border-rose-500/30 px-3.5 py-1.5 text-[13px] font-medium text-rose-300 hover:bg-rose-500/10">삭제</button>
            <span class="text-xs text-muted" aria-live="polite" x-text="keyMsgs.solar"></span>
          </div>
        </div>

        <label class="mt-4 flex cursor-pointer items-center gap-2 text-[13px] text-body">
          <input type="checkbox" x-model="cfgPersist" class="h-4 w-4 rounded border-white/20 bg-canvas text-violet">
          이 기기에 저장 (재시작 후에도 유지)
        </label>
        <p class="mt-3 text-xs text-muted">키 저장 시 연결을 확인합니다. [모델] 탭에서는 연결된 제공자의 모델만 선택 가능합니다.</p>
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
            class="mt-2 w-full rounded-lg border border-white/[0.10] px-3 py-1.5 text-[13px] font-medium text-white hover:bg-white/[0.05] disabled:opacity-50">Solar 모델 새로고침</button>
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
          <p class="mt-1.5 text-xs text-muted">순수 사진은 멀티모달 모델 권장(Upstage는 텍스트형 이미지에 적합).</p>
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
          <p class="mt-1.5 text-xs text-muted">높일수록 추론 깊이는 늘고 속도는 느려집니다.</p>
        </div>

        <div class="cfgsec">
          <label class="lbl">System Prompt (추가 지시)</label>
          <textarea x-model="systemPrompt" x-on:blur="applyPrefs()" rows="4" class="field"
            placeholder="예) 리드문은 25자 이내로. 인물명은 직책과 함께 표기."></textarea>
          <div class="mt-2 flex items-center gap-2">
            <button type="button" x-on:click="applyPrefs()"
              class="rounded-lg border border-white/[0.10] px-3.5 py-1.5 text-[13px] font-medium text-white hover:bg-white/[0.05]">적용</button>
            <span class="text-xs text-muted" aria-live="polite" x-text="prefMsg"></span>
          </div>
          <p class="mt-2 text-xs text-muted">출력 스키마(리드문·엔티티·인텐트·콘텐츠 카테고리)는 유지하며 추출 방향만 조향합니다.</p>
        </div>
      </div>

    </div>
  </aside>

  <!-- 복사 토스트 -->
  <div x-show="copyMsg" x-cloak x-transition.opacity class="toast" x-text="copyMsg" aria-live="polite"></div>

</div>
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
