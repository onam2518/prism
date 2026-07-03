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
from . import agents as AG
from . import meta_prompts as MP
from . import learnops as LO
from . import adminops as AO
from .config import Config, DEFAULT_CONFIG_PATH

LO._SV = sys.modules[__name__]      # 학습 도메인에 서버 컴포지션 주입(-m 실행의 __main__ 포함)
AO._SV = sys.modules[__name__]      # 관리자·인증 도메인에도 동일 주입
_supa = AO._supa
auth_action = AO.auth_action
validate_jwt = AO.validate_jwt
jwt_email = AO.jwt_email
admin_emails = AO.admin_emails
is_sys_admin_user = AO.is_sys_admin_user
is_admin_user = AO.is_admin_user
admin_data = AO.admin_data
admin_ingest = AO.admin_ingest
admin_action = AO.admin_action
# 하위호환 별칭: 테스트·데스크탑·내부 라우트가 serve 네임스페이스로 참조
sync_learned = LO.sync_learned
eval_golden = LO.eval_golden
register_golden = LO.register_golden
golden_list = LO.golden_list
build_golden_from_reviews = LO.build_golden_from_reviews
compare_models_on_golden = LO.compare_models_on_golden
snapshot_prompts = LO.snapshot_prompts
learning_batch = LO.learning_batch
learn_data = LO.learn_data
learn_spec_md = LO.learn_spec_md
learn_export = LO.learn_export
meta_compile_run = LO.meta_compile_run
start_learning_scheduler = LO.start_learning_scheduler
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


def _save_drafts(st, pairs, team=None):
    """(hash, 모델, 버전) 초안 스냅샷 적재 · 결과 비교 팝업의 전체 이력 원천.
    같은 (모델, 버전) 재실행은 upsert 로 최신 산출만 유지된다."""
    if not (st and hasattr(st, "save_draft")):
        return
    from .store import content_hash as _ch
    for content, out in pairs:
        tr = (out or {}).get("trace") or {}
        try:
            st.save_draft(_ch(content), tr.get("model", "") or "", int(tr.get("version") or 1),
                          out.get("item_meta") or {}, out.get("quality_meta") or {}, team=team)
        except Exception:
            pass


def store_save(pairs, source: str = "단건", team=None):
    """[(content, out), …] 를 영속 저장(+_LAST_RESULTS 미러). source: 출처. team: 소속 팀(supabase).
    적재 정책(dedup): 동일 콘텐츠 + 결과 무변경이면 적재 제외(skip), 변경 시 갱신, 신규는 추가."""
    outs = [o for _, o in pairs]
    _LAST_RESULTS[:] = outs
    _agg_bump()                          # 결과 변경 → 집계 캐시 무효화
    st = get_store()
    if st:
        try:
            r = st.save_dedup(pairs, _run_id(), source=source, team=team)
            _save_drafts(st, pairs, team=team)
            return r
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
    try:                                       # 산출이 동일해도(비-YELLOW 포함) 모델·버전 표기가 갱신되도록 무조건 upsert
        st.save_many([(fields, result.get("output") or {})], "rerun", source="재실행",
                     team=team, include_all=True)
    except Exception:
        pass
    _save_drafts(st, [(fields, result.get("output") or {})], team=team)
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
        "intentForm": list(getattr(D, "INTENT_FORM_UNIVERSAL", [])),
        "intentByService": {k: list(v) for k, v in D.INTENT_CATEGORIES_BY_SERVICE.items()},
        "iabTier1": list(D.IAB_TIER1),
        "tier2": {k: list(v) for k, v in getattr(D, "CONTENT_CATEGORY_TIER2", {}).items()},
        "iabMap": dict(getattr(D, "CATEGORY_IAB_MAP", {})),
        "domainGroups": {k: list(v) for k, v in getattr(D, "DOMAIN_GROUP_MAP", {}).items()},
        "intentDefs": dict(getattr(D, "INTENT_VALUE_DEFS", {})),
        "categoryCriteria": dict(getattr(D, "CATEGORY_CRITERIA", {})),
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


def build_template_xlsx() -> bytes:
    """엑셀 일괄 입력용 .xlsx 템플릿(의존성 0: zipfile+xml, inline string).
    헤더·예시는 CSV 템플릿과 동일 · ingest._read_xlsx 와 왕복 호환."""
    import io
    import zipfile
    from xml.sax.saxutils import escape

    rows = [["콘텐츠 그룹", "제목", "부제", "본문"],
            ["뉴스", "삼성전자 노조 임금 협상 결렬", "중앙노동위 조정 불성립",
             "삼성전자가 중앙노동위원회 조정에서 노조와 합의에 이르지 못했다. 양측은 임금 인상폭을 두고 이견을 좁히지 못했다."],
            ["스포츠", "손흥민 시즌 10호골", "",
             "토트넘이 홈 경기에서 승리했다. 손흥민이 후반 결승골을 터뜨리며 시즌 10호골을 기록했다."]]

    def cell(r, ci, v):
        col = chr(ord("A") + ci)
        return f'<c r="{col}{r}" t="inlineStr"><is><t xml:space="preserve">{escape(v)}</t></is></c>'

    sheet_rows = "".join(
        f'<row r="{ri + 1}">' + "".join(cell(ri + 1, ci, v) for ci, v in enumerate(row)) + "</row>"
        for ri, row in enumerate(rows))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<sheetData>{sheet_rows}</sheetData></worksheet>')
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="contents" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
               'Target="worksheets/sheet1.xml"/></Relationships>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                 'Target="xl/workbook.xml"/></Relationships>')
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
             '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
             '</Types>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


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
            _save_drafts(st, pairs)
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
        # 기준 프롬프트 계층: 수정 단위 = 모델 계열 쿡북 래퍼(config.family_wrappers)
        MP.WRAPPER_OVERRIDES = {k: (v or "") for k, v in (cfg.family_wrappers or {}).items()
                                if k in MP.FAMILIES}
        AG.META_CFG = {"four_calls": bool(getattr(cfg, "meta_four_calls", True)),
                       "call_models": {k: (v or "").strip()
                                       for k, v in (getattr(cfg, "meta_call_models", {}) or {}).items()
                                       if k in MP.CALLS}}
        AG.LLM_FOR_CALL = lambda mid: llm_for_model(mid, Handler.server_mock)[0]
        sync_learned()
    except Exception:
        pass


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


def _report_save(kind: str, payload, team=None):
    """운영 리포트 영속(재시작·다중 워커에도 유지 · 팀 스코프). 실패해도 흐름은 계속."""
    st = get_store()
    try:
        if st and hasattr(st, "save_report"):
            st.save_report(kind, payload, team=team)
    except Exception:
        pass


def _report_get(kind: str, team=None, default=None):
    st = get_store()
    try:
        if st and hasattr(st, "get_report"):
            r = st.get_report(kind, team=team)
            if r is not None:
                return r
    except Exception:
        pass
    return default


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
    try:                                          # 팀 퀘스트: 다음 버전(검수 목표 일시)까지 완주
        cfg = Config.load()
        d["next_version"] = int(st.batch_seq(team) if hasattr(st, "batch_seq") else 0) + 1
        d["next_model"] = cfg.model or ""          # 어떤 모델의 어떤 버전인지 명기(퀘스트 카드)
        d["next_batch_at"] = LO.next_batch_time(getattr(cfg, "learn_next_at", ""))
    except Exception:
        pass
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
    """결과 비교용 초안 스냅샷: 현재 초안 + (hash, 모델, 버전) 전체 이력(drafts).
    이력 테이블이 비어 있으면(과거 데이터) 재실행 patch_log 의 이전 초안으로 폴백."""
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
    seen = {(o.get("model") or "", o.get("version")) for o in outs}
    had_history = False
    if st and hasattr(st, "draft_history"):
        try:
            for d in st.draft_history(ch, team=team):
                had_history = True
                k = (d.get("model") or "", d.get("version"))
                if k in seen:
                    continue
                seen.add(k)
                outs.append({"label": f"{d.get('model') or '모델 미기록'} · v{int(d.get('version') or 1)}",
                             "model": d.get("model", ""), "version": int(d.get("version") or 1),
                             "item_meta": d.get("item_meta") or {}, "quality_meta": d.get("quality_meta") or {}})
        except Exception:
            pass
    if not had_history and st and hasattr(st, "patch_rows"):
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
        "goldenMinGood": int(getattr(cfg, "golden_min_good", 1) or 1),
        "learnNextAt": str(getattr(cfg, "learn_next_at", "") or ""),
        "desktopAllowDownloads": bool(getattr(cfg, "desktop_allow_downloads", True)),
        "desktopPersistStorage": bool(getattr(cfg, "desktop_persist_storage", True)),
        "metaFourCalls": bool(getattr(cfg, "meta_four_calls", True)),
        "metaCallModels": dict(getattr(cfg, "meta_call_models", {}) or {}),
        "familyWrappers": dict(getattr(cfg, "family_wrappers", {}) or {}),
        "familyWrapperDefaults": dict(MP.FAMILY_WRAPPER_DEFAULT),
        "metaCalls": list(MP.CALLS),
        "metaContract": {"rules": dict(MP.CALL_RULES), "examples": MP.gold_examples(None)},
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
    has_wrappers = "family_wrappers" in data and isinstance(data.get("family_wrappers"), dict)
    has_callm = "meta_call_models" in data and isinstance(data.get("meta_call_models"), dict)
    has_4c = "meta_four_calls" in data
    has_desktop = ("desktop_allow_downloads" in data) or ("desktop_persist_storage" in data) \
        or ("golden_min_good" in data) or ("learn_next_at" in data)
    if (model or base or reasoning or has_sp or has_stage or has_slot or has_legal or has_ingest
            or has_smodels or has_mprompts or has_wrappers or has_callm or has_4c or has_desktop):
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
        if has_wrappers:                          # 계열 래퍼 오버라이드(빈 값 = 기본 복원)
            fw = dict(cfg.family_wrappers or {})
            for fam, tpl in (data.get("family_wrappers") or {}).items():
                if fam in MP.FAMILIES:
                    t = (tpl or "").strip()
                    if t:
                        fw[fam] = t
                    else:
                        fw.pop(fam, None)
            cfg.family_wrappers = fw
        if has_callm:                             # 호출별 모델 티어(빈 값 = 실행 모델)
            cm = {k: (v or "").strip() for k, v in (data.get("meta_call_models") or {}).items()
                  if k in MP.CALLS}
            cfg.meta_call_models = {k: v for k, v in cm.items() if v}
        if has_4c:
            cfg.meta_four_calls = bool(data.get("meta_four_calls"))
        if "golden_min_good" in data:             # 골든 확정 최소 '정확' 인원(1~9)
            try:
                cfg.golden_min_good = max(1, min(9, int(data.get("golden_min_good") or 1)))
            except (TypeError, ValueError):
                pass
        if "learn_next_at" in data:               # 검수 목표(퀘스트) 일시 · 빈 값 = 목표 해제
            v = str(data.get("learn_next_at") or "").strip()[:16]
            if not v:
                cfg.learn_next_at = ""
            else:
                try:
                    time.strptime(v, "%Y-%m-%dT%H:%M")
                    # 과거 일시는 거부: 저장 즉시 반영이 돼버리는 함정 방지('⚡ 즉시 반영'이 정식 경로)
                    if LO.next_batch_time(v) > time.time() + 60:
                        cfg.learn_next_at = v
                except ValueError:
                    pass
        if "desktop_allow_downloads" in data:
            cfg.desktop_allow_downloads = bool(data.get("desktop_allow_downloads"))
        if "desktop_persist_storage" in data:
            cfg.desktop_persist_storage = bool(data.get("desktop_persist_storage"))
        for k in slot_keys:
            if k in data:
                setattr(cfg, k, (data.get(k) or "").strip())
        try:
            cfg.save_template()                   # config.json 갱신(키는 저장 안 함)
        except Exception:
            pass
        _agg_bump()                               # 설정 파생 캐시 무효화(아레나 퀘스트 시한 등 즉시 반영)
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
        elif self.path.startswith("/prompt-preview"):    # 프롬프트 스튜디오: 콜별×모델별 최종 합성 프롬프트(관리자)
            if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                return
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            model = (q.get("model") or [""])[0]
            call = (q.get("call") or ["merged"])[0]
            svc = (q.get("service") or ["뉴스"])[0]
            from .schema import Content
            c = Content(displayServiceName=svc, title="(미리보기)", subtitle="", body="(미리보기 본문)")
            sync_prompt()
            try:
                if call in MP.CALLS:
                    sysp = PR.call_system(c, call, model)
                    userp = MP.call_user(call, c, {"summary": "(리드문)", "entities": ["(엔티티)"], "intent": ["(인텐트)"]})
                else:
                    sysp = PR.item_system(c, model)
                    userp = PR.item_user(c)
                body_out = {"ok": True, "family": MP.family_of(model), "call": call,
                            "system": sysp, "user": userp}
            except Exception as e:
                body_out = {"ok": False, "error": str(e)[:300]}
            self._send(200, json.dumps(body_out, ensure_ascii=False), _JSON)

        elif self.path.startswith("/prompt-snapshot"):  # 버전별 프롬프트 스냅샷(v 미지정 = 최신 · 관리자)
            if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                return
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            v = (q.get("v") or [""])[0].strip()
            kind = f"prompt_snapshot_v{int(v)}" if v.isdigit() else "prompt_snapshot_latest"
            snap = _report_get(kind, self._req_team())
            self._send(200, json.dumps({"ok": bool(snap), "snapshot": snap}, ensure_ascii=False), _JSON)
        elif self.path.startswith("/learn-report"):     # 최근 배치 결과(GET · 재시작에도 store 영속)
            rep = _report_get("learn_report", self._req_team(), LO._LAST_LEARN_REPORT)
            _c = Config.load()                           # 다음 반영 예정(검수 목표 일시 · 화면 표시용)
            nb = LO.next_batch_time(getattr(_c, "learn_next_at", ""))
            self._send(200, json.dumps({"ok": True, "report": rep, "next_batch_at": nb},
                                       ensure_ascii=False), _JSON)
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
            _rep = _report_get("learn_report", self._req_team(), LO._LAST_LEARN_REPORT) or {}
            g = _rep.get("golden") or {}
            self._send(200, json.dumps({
                "ok": True,
                "batch_seq": (st.batch_seq(self._req_team()) if (st and hasattr(st, "batch_seq")) else 0),
                "total": (st.golden_count(self._req_team()) if (st and hasattr(st, "golden_count")) else 0),
                "source_counts": (st.golden_source_counts(self._req_team())
                                  if (st and hasattr(st, "golden_source_counts")) else {}),
                "last_batch": {k: g.get(k) for k in ("confirmed", "new", "demoted", "need_category",
                                                     "disagree", "min_good")},
                "need_list": g.get("need_list") or [],
                "ts": _rep.get("ts")}, ensure_ascii=False), _JSON)
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
        elif self.path.startswith("/template.xlsx"):
            data = build_template_xlsx()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", 'attachment; filename="prism_template.xlsx"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

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
            self._send(200, json.dumps({"ok": True, "report": _report_get("learn_report", self._req_team(), LO._LAST_LEARN_REPORT)}, ensure_ascii=False), _JSON)
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
                if rate_limited(f"evj:{rv}"):
                    self._send(429, json.dumps({"error": "잠시 후 다시 시도하세요(판정 속도 제한)"},
                                               ensure_ascii=False), _JSON)
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


from .page import PAGE                             # 앱 마크업(라우트 분리 3차)



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
