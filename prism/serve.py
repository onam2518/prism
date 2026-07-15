"""로컬 UI (의존성 0, stdlib http.server).

  python3 -m prism.serve            # http://localhost:8765
  python3 -m prism.serve --port 9000 --mock

이미지/텍스트 입력 → (이미지면 imagext 어댑터로 Content 합성) → pipeline.extract →
리드문·엔티티·인텐트·카테고리 카드 + 원본 JSON. 키 없으면 자동 mock.
"""
from __future__ import annotations

import argparse
import gzip
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

# 서버 부팅 ID: 배포(프로세스 교체) 감지 + 벤더 자산 캐시버스터의 단일 원천
_BOOT_ID = "%d-%d" % (int(time.time()), os.getpid())

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
is_super_admin_user = AO.is_super_admin_user
is_admin_user = AO.is_admin_user
admin_data = AO.admin_data
admin_ingest = AO.admin_ingest
admin_action = AO.admin_action
# 하위호환 별칭: 테스트·내부 라우트가 serve 네임스페이스로 참조
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
handoff_bundle = LO.handoff_bundle
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
                # 경로는 생성 시점에 재해석: 임포트 후 PRISM_DB 를 바꾸는 테스트 격리 지원
                _STORE = Store(os.environ.get("PRISM_DB") or _DB_PATH)
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


def _entdict_after_save(st, pairs, team=None):
    """적재 훅: 엔티티 사전 등록·링크(동기·로컬) + 신규 개체 위키데이터 보강(백그라운드).
    타입·속성은 개체 사전 신규 등록 시 1회 부여(콘텐츠마다 재판정 없음 · DNM 366018723).
    mock 서버·PRISM_ENTDICT_ENRICH=0 이면 네트워크 보강 생략(테스트 결정성·오프라인)."""
    if not (st and hasattr(st, "ent_upsert")):
        return
    try:
        from . import entdict as ED
        r = ED.ingest_pairs(st, pairs, team=team or "")
    except Exception:
        return                                   # 사전 실패가 적재 자체를 막지 않는다
    new_ids = r.get("new_ids") or []
    if new_ids and not Handler.server_mock and os.environ.get("PRISM_ENTDICT_ENRICH", "1") == "1":
        threading.Thread(target=ED.enrich_many, args=(st, new_ids), daemon=True).start()


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
            _entdict_after_save(st, pairs, team=team)
            return r
        except Exception as e:
            import traceback
            traceback.print_exc()                 # 저장 실패를 조용히 삼키지 않는다(유실 관측)
            return {"error": str(e)[:200]}
    return None


def results_rows(limit: int = 5000, team=None) -> list:
    """집계용 결과 행 · 영속 저장소 우선(누적) · 메모리(_LAST_RESULTS) 폴백은 저장소 부재·오류 시만.
    저장소의 빈 결과는 그대로 신뢰한다 — 전체 삭제 직후 메모리 잔상이 폴백으로 되살아나
    화면에 유령 콘텐츠가 남는 문제 방지."""
    st = get_store()
    if st:
        try:
            return st.recent(limit, team=team)
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


def _batch_seq_cached(team) -> int:
    """학습 반영 회차(초안 버전 산정용) · 30s 캐시.
    일괄 실행(rerun_all)이 건마다 events 테이블 전체를 재조회하지 않게 한다 — 회차는
    학습 배치 때만 바뀌고 그 쓰기 경로가 _agg_bump 를 호출하므로 스테일 위험 없음."""
    def _get():
        stv = get_store()
        return stv.batch_seq(team) if (stv and hasattr(stv, "batch_seq")) else 0
    return _agg_cached(("batchseq", team), _get)


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
def quest_active() -> bool:
    """검수 목표(퀘스트) 진행 중 여부: 반영 일시가 미래로 설정돼 있으면 참.
    진행 중에는 검수 대상 초안 교체(재실행)를 물리적으로 차단한다(합의 오염 방지)."""
    try:
        cfg = Config.load()
        return LO.next_batch_time(getattr(cfg, "learn_next_at", "")) > time.time()
    except Exception:
        return False


def _is_pending_row(r: dict) -> bool:
    """미실행(STEP 1 추가만) 행 판별: 모델 기록도 산출(item_meta)도 판정(finalGrade)도 없다.
    R 등급(아이템 폐기)은 item_meta 가 비어도 판정이 있으므로 미실행이 아니다."""
    tr = r.get("trace") or {}
    qm = r.get("quality_meta") or {}
    return not ((tr.get("model") or "") or (r.get("item_meta") or {}) or (qm.get("finalGrade") or ""))


def _safe_url(u: str) -> str:
    """저장용 원문 링크 정제: http/https 만 허용(javascript:·data: 등 스크립트 스킴 차단). 그 외는 빈 문자열.
    원문 iframe 이 이 값을 src 로 로드하므로 스킴 화이트리스트로 저장형 XSS 를 차단한다."""
    u = (u or "").strip()
    low = u.lower()
    return u if (low.startswith("http://") or low.startswith("https://")) else ""


def add_contents(contents: list, purpose: str = "", team=None, source: str = "단건") -> dict:
    """STEP 1 콘텐츠 추가: 저장만 하고 모델은 돌리지 않는다(미실행 대기).
    실행은 STEP 2 모델 실행(일괄 실행 큐 · scope=pending)이 담당 · 실행 시 같은 hash 로 upsert."""
    rows = [c for c in contents
            if (c.get("title") or "").strip() or (c.get("body") or "").strip()]
    if not rows:
        return {"error": "제목·본문이 비어 있습니다"}
    from .store import content_hash as _chash
    uniq = {}                                     # 파일 내 완전 중복 행은 1건으로(추가 건수 정확)
    for c in rows:
        uniq[_chash(c)] = c
    dropped = len(rows) - len(uniq)
    rows = list(uniq.values())
    pairs = [(c, {"content_ref": {"displayServiceName": c.get("displayServiceName", ""),
                                  "title": c.get("title", ""), "subtitle": c.get("subtitle", ""),
                                  "source_url": _safe_url(c.get("source_url", "") or c.get("url", "")),
                                  "body": c.get("body", ""), "body_hash": _chash(c)},
                  "quality_meta": {}, "item_meta": {}, "trace": {}}) for c in rows]
    saved = store_save(pairs, source=source, team=team)
    if isinstance(saved, dict) and saved.get("error"):   # 저장 실패면 '추가됨'으로 속이지 않는다
        return {"error": "저장 실패 · 다시 시도하세요 (" + saved["error"][:120] + ")"}
    jid = "add:" + time.strftime("%H%M%S")               # 실행 이력에 추가 기록(클릭 -> 해당 콘텐츠)
    with _INGEST_LOCK:                                   # 키 삽입은 상태 순회와 레이스 · 락 필수
        _INGEST_STATE[jid] = {"name": source, "endpoint": "", "kind": "콘텐츠 추가", "started": time.time(),
                              "running": False, "total": len(rows), "done": len(rows), "failed": 0,
                              "last_run": time.time(), "last_msg": f"{len(rows)}건 추가 · 미실행 대기(STEP 2에서 실행)",
                              "last_ok": True, "trigger": "manual", "hashes": [_chash(c) for c in rows]}
    if (purpose or "") == "eval":
        try:
            from .store import content_hash as _chash
            stp = get_store()
            if stp and hasattr(stp, "set_purpose"):
                stp.set_purpose([_chash(c) for c in rows], "eval", team=team)
        except Exception:
            pass
    return {"ok": True, "added": len(rows), "pending": True,
            **({"duplicates": dropped} if dropped else {})}


def run_pipeline(fields: dict, *, mock: bool, team=None, model: str = "", persist: bool = True) -> dict:
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
            # 참조용 원문 링크 · 해시(서비스+제목+부제+본문) 불포함이라 정체성 무변
            "source_url": fields.get("source_url", "") or fields.get("url", ""),
        }

    out = PIPE.extract(content, llm, legal=cfg.legal_enabled)
    try:                                         # 초안 버전 = 학습 반영 회차 + 1
        (out.setdefault("trace", {}))["version"] = _batch_seq_cached(team) + 1
    except Exception:
        pass
    if not persist:                              # 실험(미저장): 추출만 하고 results·초안·홀드아웃 미기록
        return {"source": source, "mock": llm.mock, "content": content,
                "signals": signals, "output": out}
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


def rerun_all(model: str, team=None, limit: int = 200, scope: str = "all") -> dict:
    """모아진 콘텐츠를 지정 모델로 일괄 실행(수동 · 관리자). 건당 비용 발생.
    scope: pending=미실행(STEP 1 추가 대기)만 · all=전체 재실행.
    퀘스트 진행 중에는 전체 재실행 차단(검수 중 초안이 바뀌면 판정·합의가 오염된다)."""
    if scope != "pending" and quest_active():
        return {"error": "퀘스트 진행 중에는 전체 재실행이 차단됩니다(검수 중 초안 교체 방지) · "
                         "'미실행만'은 가능하며, 반영 후 실행하거나 검수 목표 카드에서 일시를 비워 목표를 해제하세요"}
    rows = results_rows(team=team)
    targets, seen, row_by_hash = [], set(), {}
    for r in rows[-int(limit):]:
        if scope == "pending" and not _is_pending_row(r):
            continue                                 # 이미 실행된 건 제외
        ch = _row_key(r.get("content_ref") or {})
        if ch and ch not in seen:
            seen.add(ch)
            targets.append(ch)
            row_by_hash[ch] = r                      # 1회 로드분 재사용 · 건마다 전체 재조회(N×5000) 방지
    if not targets:
        return {"ok": True, "done": 0, "failed": 0, "model": model, "scope": scope,
                "msg": "대상이 없습니다" + (" (미실행 콘텐츠 없음)" if scope == "pending" else "")}
    done = failed = 0
    jid = "rerun:" + time.strftime("%H%M%S")         # 실행 큐 등록(진행률·ETA)
    _job_begin(jid, model or "기본 모델", "일괄 실행", len(targets))
    _INGEST_STATE[jid]["hashes"] = list(targets)     # 작업 클릭 -> 결과 콘텐츠 보기
    try:
        for ch in targets:
            res = rerun_content(ch, model, team=team, row=row_by_hash.get(ch))
            if res.get("error"):
                failed += 1
                _INGEST_STATE[jid]["failed"] = failed
            else:
                done += 1
            _INGEST_STATE[jid]["done"] += 1
    except Exception as e:
        _job_end(jid, False, f"{done}건 실행 후 중단 · {str(e)[:80]}")
        raise
    _job_end(jid, failed == 0, f"{done}건 실행" + (f" · 실패 {failed}" if failed else " 완료"))
    return {"ok": True, "done": done, "failed": failed, "model": model}


def rerun_content(content_hash: str, model: str, team=None, row=None) -> dict:
    """같은 콘텐츠를 지정 모델로 재실행(초안 재생성 · 관리자). 기존 초안은 덮어쓰되
    이전 초안을 patch_log 에 남겨(rerun:구모델) 이력·비교 근거를 보존한다.
    row: 일괄 실행(rerun_all)이 미리 로드한 행 주입 — 건마다 전체 테이블 재조회 방지."""
    st = get_store()
    ch = (content_hash or "").strip()
    if not (st and ch):
        return {"error": "콘텐츠를 찾을 수 없습니다"}
    if row is None:
        for r in results_rows(team=team):
            if _row_key(r.get("content_ref") or {}) == ch:
                row = r
                break
    if not row:
        return {"error": "콘텐츠를 찾을 수 없습니다(본문 미보존 항목일 수 있음)"}
    ref = row.get("content_ref") or {}
    fields = {"displayServiceName": ref.get("displayServiceName", ""), "title": ref.get("title", ""),
              "subtitle": ref.get("subtitle", ""), "body": ref.get("body", ""),
              "source_url": ref.get("source_url", "")}   # 재실행 upsert 가 원문 링크를 지우지 않게 보존
    if quest_active() and not _is_pending_row(row):
        return {"error": "퀘스트 진행 중에는 검수 중 콘텐츠의 초안 재실행이 차단됩니다 · "
                         "반영 후 실행하거나 검수 목표 카드에서 목표를 해제하세요"}
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
    _entdict_after_save(st, [(fields, result.get("output") or {})], team=team)
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

    헤더는 ingest 별칭과 일치: 콘텐츠 그룹·제목·부제·본문·원문 링크. 제목·본문이 필수(원문 링크는 선택).
    """
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["콘텐츠 그룹", "제목", "부제", "본문", "원문 링크"])
    w.writerow(["뉴스", "삼성전자 노조 임금 협상 결렬",
                "중앙노동위 조정 불성립",
                "삼성전자가 중앙노동위원회 조정에서 노조와 합의에 이르지 못했다. 양측은 임금 인상폭을 두고 이견을 좁히지 못했다.",
                "https://v.daum.net/v/20260101000000000"])
    w.writerow(["스포츠", "손흥민 시즌 10호골",
                "",
                "토트넘이 홈 경기에서 승리했다. 손흥민이 후반 결승골을 터뜨리며 시즌 10호골을 기록했다.",
                ""])
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
    st = get_store()
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
    st = get_store()
    if not (st and hasattr(st, "ent_upsert")):
        return {"ok": False, "error": "저장소 없음"}
    action = (data.get("action") or "").strip()
    eid = (data.get("id") or "").strip()
    if action != "detail":
        _agg_bump()                                   # 등재·수정·보강은 토픽 엔티티 속성 인덱스에 반영 → 캐시 무효화

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
    """편집 초기화: overrides 파일 삭제 + 원본 사전 즉시 복원(재시작 불필요)."""
    try:
        os.remove(_DICT_OVERRIDES_PATH)
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


def _studio_config() -> dict:
    """\ud1a0\ud53d \uc2a4\ud29c\ub514\uc624 \uc124\uc815(\uc0ac\uc6a9\uc790 \uc815\uc758 \uc870\uac74\ud615 \ud1a0\ud53d + \ud074\ub7ec\uc2a4\ud130\ub9c1 \ud29c\ub2dd) \ub85c\ub4dc.
    \ud1a0\ud53d\uc740 \uc804\uc5ed(\ubb34\ud300 results_rows) \ubdf0\ub77c \uc124\uc815\ub3c4 \uc804\uc5ed(team="")\uc5d0 \uc601\uc18d\ud55c\ub2e4."""
    st = get_store()
    cfg = (st.get_report("topic_studio") if st else None) or {}
    custom = cfg.get("custom") if isinstance(cfg.get("custom"), list) else []
    settings = cfg.get("settings") if isinstance(cfg.get("settings"), dict) else {}
    exclusions = cfg.get("exclusions") if isinstance(cfg.get("exclusions"), dict) else {}
    return {"custom": custom, "settings": settings, "exclusions": exclusions}


def _save_studio_config(cfg: dict):
    st = get_store()
    if st:
        st.save_report("topic_studio", {"custom": cfg.get("custom") or [],
                                        "settings": cfg.get("settings") or {},
                                        "exclusions": cfg.get("exclusions") or {}})


def topics_data() -> dict:
    """\ud1a0\ud53d \ubaa8\ub4c8 \ub370\uc774\ud130(30s \uce90\uc2dc). \ub4dc\ub9b4\ub2e4\uc6b4 \ud074\ub9ad\ub9c8\ub2e4 \uc804\uccb4 \uc7ac\ud074\ub7ec\uc2a4\ud130\ub9c1\ud558\ub358 \ube44\uc6a9 \uc81c\uac70 \u2014
    \uc4f0\uae30(\ucd94\ucd9c\u00b7\uc2a4\ud29c\ub514\uc624 \ubcc0\uacbd)\ub294 _agg_bump \ub85c \uc989\uc2dc \ubb34\ud6a8\ud654\ub41c\ub2e4."""
    return _agg_cached(("topics",), _topics_compute)


def _topics_compute() -> dict:
    """\ud1a0\ud53d \ubaa8\ub4c8: \uc801\uc7ac\ub41c \uacb0\uacfc\uc5d0\uc11c \uc5d4\ud2f0\ud2f0\ud615\u00b7\uc0ac\uac74\ud615\u00b7\uc870\uac74\ud615 \ud1a0\ud53d + \uc0ac\uc6a9\uc790 \uc815\uc758 \ud1a0\ud53d \ube4c\ub4dc."""
    rows = results_rows()
    cfg = _studio_config()
    if not rows:
        return {"n_contents": 0, "single": [], "composite": [], "filter": [], "custom": [],
                "customDefs": cfg["custom"], "settings": cfg["settings"], "exclusions": cfg["exclusions"],
                "catalog": {"intents": [], "cats": [], "keywords": [], "eattrs": []}, "summary": {}}
    from . import topic as TP
    with tempfile.TemporaryDirectory() as d:
        rpath = os.path.join(d, "r.jsonl")
        with open(rpath, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        try:
            out = TP.build_topics(rpath, custom_defs=cfg["custom"], settings=cfg["settings"],
                                  exclusions=cfg["exclusions"], ent_index=_ent_index())
            out["exclusions"] = cfg["exclusions"]
            return out
        except Exception as e:
            return {"error": str(e)[:200], "n_contents": len(rows),
                    "single": [], "composite": [], "filter": [], "custom": [],
                    "customDefs": cfg["custom"], "settings": cfg["settings"],
                    "exclusions": cfg["exclusions"], "summary": {}}


def _ent_index() -> dict:
    """토픽 매칭용 개체 속성 인덱스({content_hash: [속성 dict]}) · 사전 미구축이면 빈 dict.
    토픽은 전역(무팀 results_rows) 뷰라 인덱스도 전역(team="")."""
    st = get_store()
    if not (st and hasattr(st, "ent_attr_index")):
        return {}
    from . import entdict as ED
    return ED.attr_index(st, team="")


def _sanitize_def(d: dict, existing_ids=None) -> dict:
    """\uc0ac\uc6a9\uc790 \uc815\uc758 \uc815\uaddc\ud654\u00b7\uac80\uc99d. id \uc5c6\uc73c\uba74 \uc0dd\uc131(\uc911\ubcf5 \ud68c\ud53c)."""
    from . import topic as TP
    name = (d.get("name") or "").strip()[:60]
    prompt = (d.get("prompt") or "").strip()[:280]

    def _strlist(v, n=20, ln=60):
        out, seen = [], set()
        for x in (v or []):
            s = str(x).strip()[:ln]
            if s and s not in seen:
                seen.add(s); out.append(s)
            if len(out) >= n:
                break
        return out

    cats = _strlist(d.get("cats"))
    intents = _strlist(d.get("intents"))
    keywords = _strlist(d.get("keywords"))
    # 개체 속성 조건: 허용 키('key:value')만 · 항상 필수(같은 개체 AND) · 최대 10개
    from . import entdict as ED
    eattrs = [s for s in _strlist(d.get("eattrs"), n=10) if ED.parse_eattr(s)]
    # 제외(neg): 선택과 독립인 배제 조건. 같은 값이 선택에도 있으면 선택을 우선(자기모순 방지).
    ng = d.get("neg") or {}
    neg = {k: [v for v in _strlist(ng.get(k))
               if v not in {"cats": cats, "intents": intents, "keywords": keywords}[k]]
           for k in ("cats", "intents", "keywords")}
    # \ud544\uc218(req): \uc120\ud0dd\ub41c \uac12\uc758 \ubd80\ubd84\uc9d1\ud569\ub9cc \uc778\uc815(\uac12 \uc5c6\uc73c\uba74 \ud558\uc704\ud638\ud658\uc73c\ub85c topic \uc774 '\uc804\ubd80 \ud544\uc218' \ucc98\ub9ac)
    rq = d.get("req") or {}
    sel = {"cats": set(cats), "intents": set(intents), "keywords": set(keywords)}
    req = {k: [v for v in _strlist(rq.get(k)) if v in sel[k]] for k in ("cats", "intents", "keywords")}
    cid = (d.get("id") or "").strip()
    if not cid:
        base = "U-" + (TP._slug(name or prompt or "topic") or "topic")
        cid, n = base, 2
        ids = set(existing_ids or [])
        while cid in ids:
            cid = base + "-" + str(n); n += 1
    return {"id": cid, "name": name or "(\ubb34\uc81c \ud1a0\ud53d)", "prompt": prompt,
            "cats": cats, "intents": intents, "keywords": keywords, "eattrs": eattrs,
            "req": req, "neg": neg}


def _studio_llm_suggest(text: str, model: str, rows, svc, mock: bool):
    """\uc790\uc5f0\uc5b4 \uc124\uba85 \u2192 \ud1a0\ud53d \ucc28\uc6d0(\uce74\ud14c\uace0\ub9ac\u00b7\uc778\ud150\ud2b8\u00b7\ud0a4\uc6cc\ub4dc)\uc744 \uc120\ud0dd \ubaa8\ub378\ub85c \ub9e4\ud551.
    \ud5c8\uc6a9 \ubaa9\ub85d(\ud604\uc7ac \ub370\uc774\ud130\uc758 \uc2e4\uc7ac \uac12)\uc73c\ub85c\ub9cc \uc81c\uc57d \u00b7 \uc2e4\ud328 \uc2dc (None, \uc0ac\uc720) \ubc18\ud658(\ud638\ucd9c\ubd80\uc5d0\uc11c \ud734\ub9ac\uc2a4\ud2f1 \ud3f4\ubc31)."""
    from . import topic as TP, meta_prompts as MP
    tax = TP.meta_taxonomy()                       # \uc2dc\uc2a4\ud15c \uc804\uccb4 \uc544\uc774\ud15c\uba54\ud0c0 \ubd84\ub958(\ub370\uc774\ud130 \uc720\ubb34 \ubb34\uad00)
    cat = TP.studio_catalog(rows, svc)             # \ud604\uc7ac \ub370\uc774\ud130\uc5d0 \uc2e4\uc7ac\ud558\ub294 \uac12(\uc6b0\uc120)
    data_cats = [c["k"] for c in cat["cats"]]
    data_int = [c["k"] for c in cat["intents"]]
    allow_cats = list(dict.fromkeys((tax["cats"] or []) + data_cats))   # \uc804\uccb4 \u222a \ub370\uc774\ud130
    allow_int = list(dict.fromkeys((tax["intents"] or []) + data_int))
    tier1_ko = getattr(TP, "_TIER1_KO", {}) or {}
    cats_ko = [((tier1_ko.get(c) or c) + "=" + c) for c in allow_cats]  # \uc601\ubb38 Tier1 + \ud55c\uae00 \ubcd1\uae30
    # \uac1c\uccb4 \uc18d\uc131 \ud6c4\ubcf4(\uc5d4\ud2f0\ud2f0 \uc0ac\uc804 \uc2e4\uc7ac\uac12): '\uc5ec\uc131 \uc2a4\ud3ec\uce20\uc778'\ub958 \uc124\uba85 \u2192 eattrs \uc870\uac74 \uc790\ub3d9\uc0dd\uc131
    ecat = TP.eattr_catalog(_ent_index())
    e_allow = [c["k"] for c in ecat]
    e_prompt = [f'{c["k"]} ({c["label"]} \u00b7 {c["v"]}\uac74)' for c in ecat[:60]]
    # \ubaa8\ub378 \uacc4\uc5f4 \ucfe1\ubd81 \ub798\ud37c\ub85c \uc870\ub9bd(\ud544\uc218/\uc120\ud0dd \uc124\uacc4\uc790 \uc5ed\ud560) \u00b7 \uc2a4\ud29c\ub514\uc624 \uc624\ubc84\ub77c\uc774\ub4dc \uc0c1\uc18d
    sysp = MP.topic_suggest_system(model, cats_ko, allow_int, data_cats, data_int, eattrs=e_prompt)
    userp = MP.topic_suggest_user(text)
    llm, route = llm_for_model(model, mock)
    if llm is None:
        return None, route
    obj, _res = llm.complete_json(sysp, userp, tag="topic_suggest")
    if not isinstance(obj, dict) or obj.get("_fail"):
        return None, (isinstance(obj, dict) and obj.get("_fail_kind")) or "fail"
    ac, ai = set(allow_cats), set(allow_int)
    must, opt = obj.get("must") or {}, obj.get("optional") or {}

    def _cv(dd, key, allow):
        return [v for v in (dd.get(key) or []) if v in allow]

    def _kw(dd):
        return [str(k).strip()[:60] for k in (dd.get("keywords") or []) if str(k).strip()]

    def _uniq(a, b):
        out = list(a)
        for x in b:
            if x not in out:
                out.append(x)
        return out

    m_cats, m_int, m_kw = _cv(must, "cats", ac), _cv(must, "intents", ai), _kw(must)
    cats = _uniq(m_cats, _cv(opt, "cats", ac))
    intents = _uniq(m_int, _cv(opt, "intents", ai))
    keywords = _uniq(m_kw, _kw(opt))[:5]
    # 제외(exclude → neg): 허용 목록으로 검증 · 선택과 겹치면 선택에서 뺀다(배제 의도 우선)
    exc = obj.get("exclude") or {}
    neg = {"cats": _cv(exc, "cats", ac), "intents": _cv(exc, "intents", ai),
           "keywords": _kw(exc)[:5]}
    cats = [x for x in cats if x not in neg["cats"]]
    intents = [x for x in intents if x not in neg["intents"]]
    keywords = [x for x in keywords if x not in neg["keywords"]]
    # 개체 속성(eattrs): 실재 후보 목록으로만 검증 · 항상 필수 취급이라 req 분리 불필요
    ea = set(e_allow)
    eattrs = [str(x).strip() for x in (obj.get("eattrs") or []) if str(x).strip() in ea][:6]
    sug = {"cats": cats, "intents": intents, "keywords": keywords, "eattrs": eattrs,
           "req": {"cats": [c for c in m_cats if c in cats], "intents": [i for i in m_int if i in intents],
                   "keywords": [k for k in m_kw if k in keywords]},
           "neg": neg}
    return sug, route


def topic_studio_action(data: dict, mock: bool = False) -> dict:
    """\ud1a0\ud53d \uc2a4\ud29c\ub514\uc624 \ubcc0\uacbd/\uc870\ud68c: save\u00b7delete\u00b7settings\u00b7preview\u00b7suggest."""
    from . import topic as TP
    action = (data.get("action") or "").strip()
    if action not in ("preview", "suggest"):
        _agg_bump()                                   # \ubcc0\uacbd\uc131 \uc561\uc158(save\u00b7delete\u00b7settings\u00b7exclude \ub4f1) \u2192 \ud1a0\ud53d \uce90\uc2dc \ubb34\ud6a8\ud654
    rows = results_rows()
    svc = TP._service_names(rows) if rows else set()

    if action == "preview":
        d = _sanitize_def(data.get("def") or {})
        pv = (TP.preview_definition(rows, svc, d, ent_index=_ent_index()) if rows else
              {"n_total": 0, "bundles": [], "must_n": 0, "opt_n": 0})
        # 표본을 상세 화면 계약(_detail_row)으로 확장: 미리보기 배지 클릭 → 공통 스플릿뷰로 바로 열람
        for b in pv.get("bundles") or []:
            if b.get("samples"):
                b["samples"] = [_detail_row(rows[s["i"]]) for s in b["samples"]
                                if isinstance(s.get("i"), int) and 0 <= s["i"] < len(rows)]
        return {"ok": True, "preview": pv}

    if action == "suggest":
        text = data.get("text") or ""
        if not rows:
            return {"ok": True, "via": "none",
                    "suggest": {"cats": [], "intents": [], "keywords": [], "eattrs": [],
                                "req": {"cats": [], "intents": [], "keywords": []},
                                "neg": {"cats": [], "intents": [], "keywords": []}}}
        model = (data.get("model") or "").strip()          # "" = \uae30\ubcf8 \uc2e4\ud589 \ubaa8\ub378
        via, route, sug = "llm", "", None
        try:
            sug, route = _studio_llm_suggest(text, model, rows, svc, mock)
        except Exception as e:
            sug, route = None, str(e)[:80]
        # \ubaa8\ub378 \ud638\ucd9c \ubd88\uac00\u00b7\uc2e4\ud328\u00b7\ube48 \uacb0\uacfc \u2192 \ud734\ub9ac\uc2a4\ud2f1(\uc989\uc2dc\u00b7\uc758\uc874\uc131 0) \ud3f4\ubc31. \ubc84\ud2bc\uc774 \ud5db\ub3cc\uc9c0 \uc54a\uac8c.
        if not sug or not (sug.get("cats") or sug.get("intents") or sug.get("keywords")
                           or sug.get("eattrs") or any((sug.get("neg") or {}).values())):
            sug = TP.suggest_dims(text, rows, svc, eattr_cands=TP.eattr_catalog(_ent_index()))
            via = "heuristic"
        sug.setdefault("eattrs", [])
        return {"ok": True, "suggest": sug, "via": via, "model": model, "route": route}

    cfg = _studio_config()
    custom = list(cfg["custom"])
    exclusions = {k: list(v or []) for k, v in (cfg["exclusions"] or {}).items()}

    if action == "save":
        if not isinstance(data.get("def"), dict) or not data["def"]:
            # def 누락(키 오타 포함)이 조용히 '(무제 토픽)' 을 만드는 것 방지 — 명시 에러로 반환
            return {"ok": False, "error": "토픽 정의(def)가 필요합니다"}
        d = _sanitize_def(data.get("def") or {}, existing_ids=[c.get("id") for c in custom])
        idx = next((i for i, c in enumerate(custom) if c.get("id") == d["id"]), -1)
        if idx >= 0:
            custom[idx] = d
        else:
            custom.append(d)
        _save_studio_config({"custom": custom, "settings": cfg["settings"], "exclusions": exclusions})
    elif action == "delete":
        cid = (data.get("id") or "").strip()
        custom = [c for c in custom if c.get("id") != cid]
        exclusions.pop(cid, None)               # 토픽 삭제 시 그 토픽의 제외 목록도 정리
        _save_studio_config({"custom": custom, "settings": cfg["settings"], "exclusions": exclusions})
    elif action == "settings":
        s = data.get("settings") or {}
        settings = dict(cfg["settings"])
        if s.get("co_min") is not None:
            settings["co_min"] = max(1, min(6, int(s.get("co_min") or 2)))
        if s.get("entity_min") is not None:
            settings["entity_min"] = max(1, min(10, int(s.get("entity_min") or 2)))
        _save_studio_config({"custom": custom, "settings": settings, "exclusions": exclusions})
    elif action in ("exclude", "restore"):
        # 큐레이션 오버레이: 토픽(자동=cluster_id · 사용자=그룹 id)에서 콘텐츠(hash) 개별 제외/복구.
        # 매칭 정의는 그대로 두는 편집 판단 — 메타 교정(검수)·정의 수정과 구분되는 세 번째 수단.
        tid = (data.get("id") or "").strip()[:80]
        h = (data.get("hash") or "").strip()[:80]
        if not tid or not h:
            return {"ok": False, "error": "토픽 id 와 콘텐츠 hash 가 필요합니다"}
        lst = [e for e in (exclusions.get(tid) or [])
               if (e.get("h") if isinstance(e, dict) else e) != h]
        if action == "exclude":
            lst.append({"h": h, "title": str(data.get("title") or "")[:80],
                        "topic": str(data.get("topic") or "")[:60], "ts": time.time()})
            lst = lst[-300:]                     # 토픽당 상한(폭주 방지)
        if lst:
            exclusions[tid] = lst
        else:
            exclusions.pop(tid, None)
        _save_studio_config({"custom": custom, "settings": cfg["settings"], "exclusions": exclusions})
    else:
        return {"ok": False, "error": "\uc54c \uc218 \uc5c6\ub294 \ub3d9\uc791"}
    return topics_data()


def media_action(data: dict) -> dict:
    """\ubbf8\ub514\uc5b4 \uba54\ud0c0 \ud30c\uc774\ud504\ub77c\uc778(\ud3ec\ud1a0\u00b7\uc601\uc0c1 \ud14d\uc2a4\ud2b8\ud654) \uc561\uc158 \ub514\uc2a4\ud328\uce58.

    \uc99d\ubd84 1: T1 \uc790\ub9c9 \ud30c\uc2f1(\ub8f0\u00b7\ubaa8\ub378 0\uac74)\ub9cc \uc2e4\ub3d9\uc791. T2 \uc624\ub514\uc624 \uc804\uc0ac\u00b7T3 \ube44\uc8fc\uc5bc \ubb18\uc0ac\ub294
    \ub77c\uc6b0\ud130/\ubbf8\ub514\uc5b4 \ubd84\ud574 \uacb0\uc815 \ud6c4 \ubcc4\ub3c4 \uc99d\ubd84\uc5d0\uc11c \ubd99\uc778\ub2e4(mediaext \ubaa8\ub4c8\uc5d0 \ud2b8\ub799\uc740 \uc774\ubbf8 \uc874\uc7ac)."""
    from . import mediaext as MX
    action = (data.get("action") or "subtitles").strip()
    if action == "subtitles":
        raw = data.get("raw") or ""
        fmt = (data.get("fmt") or "").strip()
        if not raw.strip():
            return {"ok": False, "error": "\uc790\ub9c9 \uc6d0\ubb38\uc744 \uc785\ub825\ud558\uc138\uc694"}
        return {"ok": True, **MX.parse_subtitles(raw, fmt)}
    if action == "s5ab":                              # S5 \uba54\ud0c0\ucd94\ucd9c \ubaa8\ub378 A/B(\ubbf8\uc800\uc7a5)
        text = (data.get("text") or "").strip()
        models = data.get("models") or []
        if not text:
            return {"ok": False, "error": "\ud1b5\ud569 \uc6d0\uace0(\ud14d\uc2a4\ud2b8)\ub97c \uc785\ub825\ud558\uc138\uc694"}
        if not models:
            return {"ok": False, "error": "\ud6c4\ubcf4 \ubaa8\ub378\uc744 1\uac1c \uc774\uc0c1 \uc120\ud0dd\ud558\uc138\uc694"}
        return media_s5ab(text, models, caption=data.get("caption", ""))
    return {"ok": False, "error": "\uc54c \uc218 \uc5c6\ub294 \ub3d9\uc791(\uc790\ub9c9 \ud30c\uc2f1\uc740 media_action, \uc601\uc0c1\uc740 media_native)"}


def media_s5ab(text: str, models: list, *, caption: str = "") -> dict:
    """S5 \uba54\ud0c0\ucd94\ucd9c \ubaa8\ub378 A/B(\uc2e4\ud5d8\uc2e4 \u00b7 \ubbf8\uc800\uc7a5). \uac19\uc740 \ud1b5\ud569 \uc6d0\uace0\ub97c \ud6c4\ubcf4 \ubaa8\ub378\ub4e4\uc5d0 \ud0dc\uc6cc
    \uc544\uc774\ud15c \uba54\ud0c0(\ub9ac\ub4dc\ubb38\u00b7\uc778\ud150\ud2b8\u00b7\uc5d4\ud2f0\ud2f0\u00b7IAB)\ub97c \ub098\ub780\ud788 \ube44\uad50 \u2192 '\uc120\uc815 \ub300\uae30 \uc2ac\ub86f'\uc758 \ubaa8\ub378
    \uad50\uccb4 \uc790\uc720\ub97c \uc2e4\uce21\uc73c\ub85c \uc99d\uba85. \ubaa8\ub378 \ub77c\uc6b0\ud305\uc740 llm_for_model \uc7ac\uc0ac\uc6a9(solar \uc9c1\uc811\u00b7\ub77c\uc6b0\ud130).

    \ubb34\ud0a4(\ub610\ub294 \uc11c\ubc84 mock) \uc2dc route=mock \ub85c \ub3d9\uc77c \uc0b0\ucd9c \u2014 \uc2e4\ud0a4 \uc5f0\uacb0 \uc2dc \ubaa8\ub378\ubcc4\ub85c \uac08\ub9b0\ub2e4.
    """
    body = (caption.strip() + "\n" + text).strip() if caption.strip() else text
    results = []
    for m in list(dict.fromkeys(str(x) for x in models))[:6]:   # \uc911\ubcf5 \uc81c\uac70 \u00b7 \uc0c1\ud55c 6
        try:
            res = run_pipeline({"displayServiceName": "\uc601\uc0c1", "title": "", "subtitle": "", "body": body},
                               mock=Handler.server_mock, model=m, persist=False)
        except Exception as e:
            results.append({"model": m, "error": str(e)[:120]})
            continue
        if res.get("error"):
            results.append({"model": m, "error": res["error"]})
            continue
        out = res.get("output") or {}
        im = out.get("item_meta") or {}
        tr = out.get("trace") or {}
        # 계측: 빈 산출 진단 — item_meta 가 비었는데 mock 도 아니면 실패. trace.fails 로 사유 노출
        #  (예: gemini 침묵 빈응답 → kind=parse_empty). 하네스가 '왜 빈값'을 스스로 보고한다.
        empty = not (im.get("summary") or im.get("entities") or im.get("content_category"))
        results.append({"model": m, "mock": bool(res.get("mock")),
                        "item_meta": im, "empty": empty,
                        "fails": tr.get("fails") or []})
    return {"ok": True, "results": results}


def media_native(content_bytes: bytes, mime: str, *, caption: str = "",
                 description: str = "", model: str = "") -> dict:
    """T4 \ub124\uc774\ud2f0\ube0c \ube44\ub514\uc624 \uc2e4\ud5d8(\uc2e4\ud5d8\uc2e4 \u00b7 \ubbf8\uc800\uc7a5). \uc601\uc0c1 \ud1b5\uc9dc \u2192 \ub77c\uc6b0\ud130 \uc704\uc784 \ud2b8\ub799 \u2192
    S4 \ubcd1\ud569 \u2192 \ud569\uc131 Content \u2192 \uae30\uc874 \ucd94\ucd9c(S5) \u2192 ItemMeta. results \uc5d0 \uc800\uc7a5\ud558\uc9c0 \uc54a\ub294\ub2e4.

    \ube44\uc804 \uc2ac\ub86f\uc774 \ub77c\uc6b0\ud130\uba74 \uadf8 \uc11c\ube44\uc2a4/\ubaa8\ub378\ub85c \ub124\uc774\ud2f0\ube0c \ud638\ucd9c, \uc544\ub2c8\uba74(\ub610\ub294 \uc11c\ubc84 mock) mock \ud3f4\ubc31.
    """
    from . import mediaext as MX
    cfg = Config.load()
    mock = Handler.server_mock
    service = cfg.vision_provider if MX.is_router(cfg.vision_provider) else "bizrouter"
    vmodel = cfg.vision_model or model
    nv = MX.native_video_track(content_bytes, mime, vmodel, service, mock=mock)
    merged = MX.merge_tracks(audio=nv.get("audio"), visual=nv.get("visual"))
    content = MX.build_content(merged, caption=caption, description=description)
    # S5 = \uae30\uc874 \ucd94\ucd9c \uc7ac\uc0ac\uc6a9(imagext \ub3d9\uc77c \uc124\uacc4) \u00b7 persist=False \ub85c \ubbf8\uc800\uc7a5
    res = run_pipeline({"displayServiceName": content["displayServiceName"],
                        "title": content["title"], "subtitle": content["subtitle"],
                        "body": content["body"]},
                       mock=mock, model=model, persist=False)
    return {"ok": True, "mock": bool(res.get("mock")), "native": nv,
            "merged": merged, "content": content, "output": res.get("output") or {}}


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
                row["fb"] = _fb_public(fmap.get(row["hash"], {}))
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


def drill_contents(kind: str, value: str, team=None, reviewer: str = "") -> dict:
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
    return {"ok": True, "kind": kind, "value": value, "items": _attach_fb(out, team, reviewer), "n": len(out)}


def topic_drill(cluster_id: str, team=None, reviewer: str = "") -> dict:
    """토픽 드릴다운: 해당 토픽(클러스터)에 묶인 콘텐츠 목록. 배치 결과 드릴다운과 동일 shape.
    ⚠️ rows 는 topics_data() 의 content_ids 인덱스와 정합해야 해서 무필터 유지 · 피드백 부착만
    팀 스코프. 토픽 자체의 팀 파라미터화(topics_data)는 후속(실험실 메뉴 · 관리자용)."""
    rows = results_rows()
    if not rows or not cluster_id:
        return {"ok": True, "kind": "topic", "value": cluster_id or "", "items": [], "n": 0}
    td = topics_data()                        # single/composite/filter(각 content_ids) · custom(그룹→bundles)
    cluster, topic_id = None, cluster_id      # topic_id = 제외(큐레이션) 키 · 사용자 토픽은 그룹 id
    for grp in ("single", "composite", "filter"):
        for c in td.get(grp, []):
            if c.get("cluster_id") == cluster_id:
                cluster = c
                break
        if cluster:
            break
    if not cluster:                            # 사용자 토픽: 그룹의 묶음(핵심·관련) 중에서 찾음
        for g in td.get("custom", []):
            for b in (g.get("bundles") or []):
                if b.get("cluster_id") == cluster_id:
                    cluster = dict(b)
                    cluster["name"] = (g.get("name") or "") + " · " + (b.get("label") or "")
                    topic_id = g.get("id") or cluster_id   # 제외는 그룹 전체(모든 묶음)에 적용
                    break
            if cluster:
                break
    if not cluster:
        return {"ok": False, "kind": "topic", "value": cluster_id, "items": [], "n": 0,
                "error": "토픽을 찾을 수 없습니다(데이터가 갱신되었을 수 있음)"}
    ids = cluster.get("content_ids") or []
    out = _attach_fb([_detail_row(rows[i]) for i in ids if 0 <= i < len(rows)], team, reviewer)
    name = cluster.get("name") or cluster.get("label") or cluster_id
    return {"ok": True, "kind": "topic", "value": name, "items": out, "n": len(out),
            "topic_id": topic_id}


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


def _fb_public(fb: dict, reviewer: str = "") -> dict:
    """검수 집계를 클라이언트 공개 형태로 축약. verdicts 원본(검수자 식별자·개별 표)은
    /history(팀 생성자·슈퍼관리자 전용)와 같은 민감도라 목록 응답에 싣지 않는다(2026-07-10).
    reviewer 식별 시 '내 표(mine)'와 내 교정(note·elems)을 함께 내린다('완료' 판정과
    추가 수정 프리필의 원천 · raw_rows·드릴 목록 공용 · 클라 myVerdict 는 mine 미제공
    시에만 합의 폴백). 미식별이면 mine 을 싣지 않고 note·elems 는 최신 표(하위호환).
    빈 피드백도 0 값 딕셔너리로 반환(/raw 의 fb.n==0 계약 · e2e 스모크가 검증)."""
    fb = fb or {}
    vs = fb.get("verdicts") or []
    last_ts = 0
    for v in vs:
        try:
            last_ts = max(last_ts, float(v.get("ts") or 0))
        except Exception:
            pass
    out = {"verdict": fb.get("consensus") or fb.get("verdict") or "",
           "n": fb.get("n", 0), "good": fb.get("good", 0), "bad": fb.get("bad", 0),
           "ts": last_ts, "note": fb.get("note") or "", "stage": fb.get("stage") or "",
           "elems": [e for e in ((vs[-1].get("element") or "") if vs else "").split(",") if e]}
    if reviewer:
        out["mine"], out["note"], out["stage"], out["elems"] = "", "", "", []
        for v in reversed(vs):
            if v.get("reviewer") == reviewer or v.get("reviewer_id") == reviewer:
                out["mine"] = v.get("verdict") or ""
                out["note"] = v.get("note") or ""
                out["stage"] = v.get("stage") or ""
                out["elems"] = [e for e in (v.get("element") or "").split(",") if e]
                break
    return out


def _attach_fb(items, team=None, reviewer: str = ""):
    """상세행 리스트에 검수 피드백 상태(fb: verdict·ts) 부착 → 콘텐츠 목록 어디서나 '검수 완료' 표기.
    reviewer 를 주면 '완료' 판정이 내 표(mine) 기준으로 동작한다(드릴 경로 정합 · 2026-07-10)."""
    st = get_store()
    if not (st and hasattr(st, "feedback_map")):
        return items
    try:
        fmap = st.feedback_map(team=team)
    except Exception:
        return items
    for it in items:
        it["fb"] = _fb_public(fmap.get(it.get("hash"), {}) or {}, reviewer)
    return items


def _logs_rows(data: bytes, filename: str) -> list:
    """행동 로그(csv/tsv/jsonl) → dict 행 정규화. 컬럼: user_id·content_id·event·dwell_sec·scroll_pct·ts."""
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
    return rows


def _write_jsonl(rows: list, out_path: str):
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def usermeta_data(logs_bytes: bytes = None, filename: str = "", team=None) -> dict:
    """사용자 메타 모듈: 행동 로그(업로드분 저장 → 재방문 유지)와 프로필을 조인해
    소비 형태·강도·선호 산출. 프로필·로그가 모두 갖춰진 사용자는 페르소나를
    능동 생성(미생성분만 · 별도 버튼 없음)해 저장하고 기존 8종과 병행 표시.
    조회 경로(업로드 없음)는 30s 캐시 — 요청마다 전량 재빌드(O(n²) 유사도 포함)하지 않는다."""
    if logs_bytes is None:                             # 업로드는 저장 부수효과가 있어 캐시 우회
        return _agg_cached(("usermeta", team), lambda: _usermeta_compute(None, "", team))
    return _usermeta_compute(logs_bytes, filename, team)


def _usermeta_compute(logs_bytes, filename, team) -> dict:
    from . import personagen as PG
    from . import usermeta as UM
    rows = results_rows()
    if not rows:
        return {"empty": True, "n_contents": 0, "users": [], "personas_def": [],
                "note": "먼저 [실행 · 추출]에서 콘텐츠를 추출하세요. content_id 는 추출 순서(0부터)와 매칭됩니다."}
    st = get_store()
    profiles = (_report_get("usermeta_profiles", team, {}) or {}).get("users") or {}
    if logs_bytes:
        log_rows = _logs_rows(logs_bytes, filename)
        if log_rows and st and hasattr(st, "save_report"):
            st.save_report("usermeta_logs", {"rows": log_rows, "name": filename}, team=team)
            _agg_bump()                               # 로그 갱신 → 사용자 메타 캐시 무효화
    else:
        log_rows = (_report_get("usermeta_logs", team, {}) or {}).get("rows") or []
    with tempfile.TemporaryDirectory() as d:
        rpath = os.path.join(d, "r.jsonl")
        _write_jsonl(rows, rpath)
        logs_path = None
        if log_rows:
            logs_path = os.path.join(d, "logs.jsonl")
            _write_jsonl(log_rows, logs_path)
        try:
            data = UM.build_user_meta(rpath, logs_path=logs_path, profiles=profiles)
        except Exception as e:
            return {"error": str(e)[:200], "users": [], "personas_def": []}
    gen = (_report_get("usermeta_personas", team, {}) or {}).get("items") or {}
    need = [u for u in data.get("users", [])
            if profiles.get(u.get("user_id")) and u["user_id"] not in gen]
    if need:
        try:
            llm = make_text_llm(Config.load(), Handler.server_mock)
            gen.update(PG.generate_personas(llm, profiles, need, start_idx=len(gen)))
            if st and hasattr(st, "save_report"):
                st.save_report("usermeta_personas", {"items": gen}, team=team)
        except Exception as e:
            data["gen_error"] = str(e)[:200]
    UM.attach_generated(data, gen)
    data["profiles_n"] = len(profiles)
    data["profile_fields"] = {"age_bands": list(PG.AGE_BANDS), "day_parts": list(PG.DAY_PARTS)}
    return data


def usermeta_save_profiles(profs: list, team=None) -> dict:
    """프로필(사용자 메타) upsert → 최신 사용자 메타 반환(재료가 모이면 이 안에서 능동 생성)."""
    from . import personagen as PG
    st = get_store()
    cur = (_report_get("usermeta_profiles", team, {}) or {}).get("users") or {}
    n = 0
    for p in profs or []:
        p = PG.normalize_profile(p if isinstance(p, dict) else {})
        if p["user_id"]:
            cur[p["user_id"]] = p
            n += 1
    if n and st and hasattr(st, "save_report"):
        st.save_report("usermeta_profiles", {"users": cur}, team=team)
        _agg_bump()                                   # 프로필 변경 → 사용자 메타 캐시 즉시 무효화
    out = usermeta_data(team=team)
    out["saved"] = n
    return out


def build_template_xlsx() -> bytes:
    """엑셀 일괄 입력용 .xlsx 템플릿(의존성 0: zipfile+xml, inline string).
    헤더·예시는 CSV 템플릿과 동일 · ingest._read_xlsx 와 왕복 호환."""
    import io
    import zipfile
    from xml.sax.saxutils import escape

    rows = [["콘텐츠 그룹", "제목", "부제", "본문", "원문 링크"],
            ["뉴스", "삼성전자 노조 임금 협상 결렬", "중앙노동위 조정 불성립",
             "삼성전자가 중앙노동위원회 조정에서 노조와 합의에 이르지 못했다. 양측은 임금 인상폭을 두고 이견을 좁히지 못했다.",
             "https://v.daum.net/v/20260101000000000"],
            ["스포츠", "손흥민 시즌 10호골", "",
             "토트넘이 홈 경기에서 승리했다. 손흥민이 후반 결승골을 터뜨리며 시즌 10호골을 기록했다.",
             ""]]

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


def run_batch(file_bytes: bytes, filename: str, purpose: str = "", team=None,
              add_only: bool = False) -> dict:
    """엑셀/CSV 업로드 → ingest 매핑 → (add_only=추가만 | 행마다 추출 → 결과+리포트)."""
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
        if add_only:                                 # STEP 1 = 추가만(모델 미실행 · 즉시 완료)
            r = add_contents(contents, purpose=purpose, team=team, source="배치")
            return {**r, "source": "excel", "count": r.get("added", 0), "mapping": a["mapping"]}
        results, items, pairs = [], [], []
        jid = "batch:" + time.strftime("%H%M%S")     # 실행 큐 등록(진행률·ETA)
        _job_begin(jid, (filename or "엑셀"), "엑셀 일괄 추출", len(contents))
        from .store import content_hash as _bch
        _INGEST_STATE[jid]["hashes"] = [_bch(c) for c in contents]
        try:
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
                _INGEST_STATE[jid]["done"] += 1
        except Exception as e:
            _job_end(jid, False, f"{len(results)}건 추출 후 중단 · {str(e)[:80]}")
            raise
        store_save(pairs, source="배치", team=team)  # 영속 저장(단일 트랜잭션 배치)
        _job_end(jid, True, f"{len(results)}건 추출 · 저장 완료")
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


def backfill_urls(file_bytes: bytes, filename: str, team=None) -> dict:
    """원문 링크 백필(관리자): 해시/제목 ↔ URL 매핑 표로 기존 콘텐츠의 source_url 만 갱신.
    초안(item_meta)·검수 판정·적재 시각은 건드리지 않는다 — 해시가 서비스+제목+부제+본문으로만
    계산되므로 링크 교체는 콘텐츠 정체성을 바꾸지 않는다(링크 없이 인입된 과거분 구제)."""
    from . import ingest as ING
    ext = os.path.splitext(filename or "")[1].lower() or ".csv"
    fd, tmp = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file_bytes)
        try:
            headers, rows = ING.read_table(tmp)
        except ValueError as e:
            return {"error": str(e)}
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    def _find(names):
        for h in headers or []:
            if str(h or "").strip().lower().replace(" ", "").replace("_", "") in names:
                return h
        return None
    url_col = _find(set(ING.ALIASES["source_url"]))
    hash_col = _find({"hash", "해시", "contenthash", "콘텐츠해시"})
    title_col = _find(set(ING.ALIASES["title"]))
    if not url_col or not (hash_col or title_col):
        return {"error": "필수 컬럼을 찾지 못했습니다 · URL(링크) 컬럼과 해시 또는 제목 컬럼이 필요합니다",
                "headers": headers}
    st = get_store()
    if not (st and hasattr(st, "set_source_url")):
        return {"error": "저장소가 준비되지 않았습니다"}
    by_hash, by_title = {}, {}                     # 현재 적재분 색인: 매칭 + 변화 없음 판별
    for r in results_rows(team=team):
        ref = r.get("content_ref") or {}
        h = _row_key(ref)
        by_hash[h] = ref.get("source_url", "") or r.get("url", "")
        t = (ref.get("title", "") or r.get("title", "")).strip()
        if t:
            by_title.setdefault(t, []).append(h)
    updated = unchanged = no_match = ambiguous = bad_url = 0
    misses = []                                    # 미매칭 표본(최대 10) · 사용자가 원인 파악
    for row in rows:
        url = str(row.get(url_col) or "").strip()
        h = str(row.get(hash_col) or "").strip() if hash_col else ""
        t = str(row.get(title_col) or "").strip() if title_col else ""
        if not (url.startswith("http://") or url.startswith("https://")):
            bad_url += 1
            continue
        if h and h in by_hash:
            target = h
        elif t and t in by_title:
            if len(by_title[t]) > 1:               # 동일 제목 다건 = 오적용 위험 → 해시로만 허용
                ambiguous += 1
                if len(misses) < 10:
                    misses.append(f"{t} (동일 제목 {len(by_title[t])}건 · 해시로 지정 필요)")
                continue
            target = by_title[t][0]
        else:
            no_match += 1
            if len(misses) < 10:
                misses.append(h or t or "(해시·제목 빈 행)")
            continue
        if by_hash.get(target, "") == url:
            unchanged += 1
            continue
        if st.set_source_url(target, url, team=team):
            by_hash[target] = url
            updated += 1
        else:
            no_match += 1
    if updated:
        _agg_bump()
    return {"ok": True, "rows": len(rows), "updated": updated, "unchanged": unchanged,
            "noMatch": no_match, "ambiguous": ambiguous, "badUrl": bad_url, "misses": misses}


# ── 자동 인입: 작업 상태(진행률) + 백그라운드 폴링 스케줄러 ──
_INGEST_STATE = {}                         # {sid: {name,endpoint,running,total,done,last_run,last_msg,last_ok,trigger}}
_INGEST_LOCK = threading.Lock()
_INGEST_THREAD = None
_INGEST_STOP = threading.Event()


def _validate_public_url(url: str):
    """인입 URL 검증(SSRF 방어): http/https 스킴만 허용 + 해석된 IP 가 모두 공인 대역인지 확인.
    사설·루프백·링크로컬(169.254 클라우드 메타데이터)·예약·멀티캐스트 대역은 거부.
    통과 시 None, 실패 시 사유 문자열. (잔여: DNS 리바인딩 TOCTOU 는 미방어 — 내부 도구 전제)"""
    import socket
    import ipaddress
    from urllib.parse import urlparse
    try:
        p = urlparse((url or "").strip())
    except Exception:
        return "URL 파싱 실패"
    if p.scheme not in ("http", "https"):
        return "http/https URL 만 허용됩니다"
    host = p.hostname
    if not host:
        return "호스트가 없습니다"
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except Exception as e:
        return f"호스트 확인 실패: {str(e)[:80]}"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return "주소 확인 실패"
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return "사설/내부 대역 주소는 허용되지 않습니다"
    return None


def _fetch_records(endpoint: str, limit: int, method: str, auth: str):
    """REST 엔드포인트에서 레코드 배열을 가져옴. (rows, error) 반환."""
    import urllib.request
    import urllib.error
    endpoint = (endpoint or "").strip()
    if not endpoint:
        return None, "엔드포인트가 비어 있습니다"
    url = endpoint
    if "limit=" not in url and (method or "GET").upper() == "GET":
        url += ("&" if "?" in url else "?") + "limit=" + str(int(limit))
    err = _validate_public_url(url)
    if err:
        return None, err

    class _SafeRedirect(urllib.request.HTTPRedirectHandler):   # 리다이렉트 대상도 매 홉 재검증(내부망 우회 차단)
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if _validate_public_url(newurl):
                raise urllib.error.URLError("리다이렉트 대상이 허용되지 않는 주소입니다")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    req = urllib.request.Request(url, method=(method or "GET").upper())
    if auth:
        req.add_header("Authorization", auth)
    try:
        with urllib.request.build_opener(_SafeRedirect()).open(req, timeout=20) as resp:
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
                              "kind": "자동 인입", "started": time.time(),
                              "running": True, "total": 0, "done": 0, "last_run": _INGEST_STATE.get(sid, {}).get("last_run", 0),
                              "last_msg": "수신 중…", "last_ok": None, "trigger": trigger}
    try:
        rows, err = _fetch_records(source.get("endpoint", ""), limit,
                                   source.get("method", "GET"), source.get("auth", ""))
        if err:
            _INGEST_STATE[sid].update(running=False, last_run=time.time(), last_ok=False, last_msg=err)
            _jobs_persist()
            return {"ok": False, "error": err}
        try:
            contents, m = ING.to_contents_rows(rows[:limit])
        except Exception as e:
            msg = str(e)[:200]
            _INGEST_STATE[sid].update(running=False, last_run=time.time(), last_ok=False, last_msg=msg)
            _jobs_persist()
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
            _entdict_after_save(st, pairs)
        msg = f"{len(rows)}건 수신 → 신규 {stats['inserted']} · 갱신 {stats['updated']} · 제외 {stats['skipped']}"
        _INGEST_STATE[sid].update(running=False, last_run=time.time(), last_ok=True, last_msg=msg)
        _jobs_persist()
        return {"ok": True, "fetched": len(rows), "extracted": len(pairs),
                "mapping": m, "mock": llm.mock, **stats}
    except Exception as e:
        _INGEST_STATE[sid].update(running=False, last_run=time.time(), last_ok=False, last_msg=str(e)[:160])
        _jobs_persist()
        return {"ok": False, "error": str(e)[:160]}


def _fmt_dur(seconds: float) -> str:
    s = max(0, int(seconds))
    return (f"{s // 60}분 {s % 60}초" if s >= 60 else f"{s}초")


def _job_begin(jid: str, name: str, kind: str, total: int, trigger: str = "manual"):
    """일괄 작업(엑셀·일괄 실행)을 실행 큐에 등록(진행률·ETA 추적)."""
    with _INGEST_LOCK:
        _INGEST_STATE[jid] = {"name": name, "endpoint": "", "kind": kind, "started": time.time(),
                              "running": True, "total": int(total), "done": 0, "failed": 0,
                              "last_run": 0, "last_msg": "추출 중…", "last_ok": None, "trigger": trigger}
    _jobs_persist()


def _job_end(jid: str, ok: bool, msg: str):
    s = _INGEST_STATE.get(jid)
    if not s:
        return
    dur = _fmt_dur(time.time() - (s.get("started") or time.time()))
    s.update(running=False, last_run=time.time(), last_ok=ok, last_msg=f"{msg} · 소요 {dur}")
    _jobs_persist()


def _jobs_persist():
    """실행 큐 스냅샷 영속(reports 패턴 · 전역 kind='jobs'): 배포·재시작에도 이력 유지.
    시작·종료 등 상태 전이 때만 기록(건별 진행률은 기록하지 않아 저장소 부담 없음) · 최근 20건."""
    st = get_store()
    if not (st and hasattr(st, "save_report")):
        return
    try:
        with _INGEST_LOCK:
            items = sorted(_INGEST_STATE.items(),
                           key=lambda kv: kv[1].get("started") or kv[1].get("last_run") or 0)[-20:]
            snap = {k: dict(v) for k, v in items}
        st.save_report("jobs", snap)
    except Exception:
        pass


def _jobs_restore():
    """부팅 시 실행 이력 복원. 재시작(배포)으로 끊긴 '실행 중' 작업은 중단으로 표시해
    유령 진행률을 막고, 관리자에게 재실행이 필요함을 알린다."""
    st = get_store()
    if not (st and hasattr(st, "get_report")):
        return
    try:
        snap = st.get_report("jobs")
        if not isinstance(snap, dict):
            return
        with _INGEST_LOCK:
            for k, v in snap.items():
                if k in _INGEST_STATE or not isinstance(v, dict):
                    continue
                if v.get("running"):
                    v.update(running=False, last_ok=False,
                             last_run=v.get("started") or time.time(),
                             last_msg="서버 재시작(배포)으로 중단됨 · 다시 실행하세요")
                _INGEST_STATE[k] = v
    except Exception:
        pass


def ingest_status() -> dict:
    """실행 큐 상태(자동 인입 + 일괄 작업 · 진행률·예상 잔여시간) + 스케줄러 동작 여부."""
    jobs = []
    now = time.time()
    with _INGEST_LOCK:                        # 잡 등록(키 삽입) 스레드와의 순회 레이스 차단
        snapshot = list(_INGEST_STATE.items())
    for sid, s in snapshot:
        j = {"id": sid, **s}
        if s.get("running") and s.get("started"):
            j["elapsed_s"] = int(now - s["started"])
            if s.get("done") and s.get("total"):
                rate = (now - s["started"]) / max(1, s["done"])
                j["per_item_ms"] = int(rate * 1000)
                j["eta_s"] = int(rate * max(0, s["total"] - s["done"]))
        jobs.append(j)
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
            DASH.build_integrated(rpath, out, title="Prism 리포트")
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
    if IMG._api_key():
        _seed_solar_defaults()                        # 키 보유 + 엔드포인트·모델 미설정 자기 치유
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


_TEAM_CACHE = {}                       # uid -> (team_id, expiry) · 팀 변경은 드물어 60s TTL 로 충분


def team_of(uid):
    """uid → team_id · 60s 캐시. 인증 요청마다 reviewer_team 조회가 supabase 1콜을 만들던 비용 제거
    (가입·팀 변경 시 /reviewer POST 가 해당 uid 캐시를 즉시 무효화)."""
    if not uid:
        return None
    now = time.time()
    hit = _TEAM_CACHE.get(uid)
    if hit and hit[1] > now:
        return hit[0]
    st = get_store()
    team = st.reviewer_team(uid) if (st and hasattr(st, "reviewer_team")) else None
    _TEAM_CACHE[uid] = (team, now + 60)
    return team


_LLM_CACHE = {}                                        # (model, mock) → (llm, route) · 설정 변경 시 sync_prompt 가 클리어


def llm_for_model(model: str, mock: bool):
    """모델 id 로 제공자·엔드포인트·키를 해석해 전용 LLMClient 구성(다중 모델 실호출 라우팅).
    solar* = Upstage 직접, 그 외 = 키 보유 라우터(bizrouter=provider/model · timely=bare id).
    반환 (llm, route). 호출 불가(키 없음) 모델은 (None, 사유).
    프로세스 캐시: 콜별 라우팅에서 LLM 호출 1건마다 새 클라이언트(=새 RateLimiter)가 만들어지면
    RPM/TPM 창이 매번 초기화돼 레이트리밋이 무력화된다 — 같은 (모델, mock) 은 클라이언트 재사용."""
    key = ((model or "").strip(), bool(mock))
    hit = _LLM_CACHE.get(key)
    if hit is not None:
        return hit
    out = _llm_for_model_build(model, mock)
    if out[0] is not None:                             # 실패(키 없음)는 캐시하지 않음(키 등록 즉시 반영)
        _LLM_CACHE[key] = out
    return out


def _llm_for_model_build(model: str, mock: bool):
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
        _LLM_CACHE.clear()                             # 키·엔드포인트·모델 설정 변경 반영
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
        team = data.get("_team")
        if _supa() and not team:                    # 팀 스코프 없이 전 팀 삭제 금지(멀티테넌시 격리)
            return {"ok": False, "error": "팀 스코프가 필요합니다"}
        if hasattr(st, "clear_team_feedback"):
            st.clear_team_feedback(team)
        else:
            st.clear_feedback()
    else:
        ch = (data.get("hash") or "").strip()
        if not ch:
            return {"ok": False, "error": "hash required"}
        if ch.startswith("gold:"):                 # 골드 문항 응답 → gold_checks 로 분리(피드백 오염 방지)
            return apply_gold_answer(data)
        verdict = data.get("verdict") or ""        # good | bad | ""(실행취소)
        reviewer = (data.get("reviewer") or "").strip() or "(익명)"   # 귀속 키(uid 또는 이름)
        disp = (data.get("name") or "").strip() or reviewer          # 토스트 표시명
        if not verdict:                            # 실행취소: 빈 표를 upsert 하지 않고 행을 삭제(팀 표 수 정합)
            prev = (st.delete_feedback(ch, reviewer, team=data.get("_team"))
                    if hasattr(st, "delete_feedback") else "")
            if prev:                               # 원 표는 삭제돼도 취소 사실은 작업 이력에 남긴다(감사 추적)
                st.log_patch(ch, reviewer, "undo:verdict", prev, "", team=data.get("_team"))
            broadcast({"type": "feedback", "hash": ch, "reviewer": disp, "verdict": "",
                       "title": data.get("title", ""), "service": data.get("service", ""), "ts": time.time()})
            _agg_bump()
            return {"ok": True, "feedback": st.feedback_stats(),
                    "learned": {k: bool(v) for k, v in (PR.LEARNED or {}).items()}}
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
    """검수자 신뢰도 가중치(골든 합의용) = 골드 문항 정확도와 Dawid-Skene EM 추정 정확도의 블렌드.
    w = 0.5 + 0.5*acc, acc = 두 추정의 평균(한쪽만 충분하면 그쪽만 · 각 표본 5건 이상).
    표본 없는 검수자는 미포함 → 1.0 취급(기존 다수결과 동일). [Dawid-Skene 1979 · Snow 2008]"""
    st = get_store()
    gold, ds = {}, {}
    try:
        gold = st.gold_stats(team) if (st and hasattr(st, "gold_stats")) else {}
    except Exception:
        gold = {}
    try:                                          # DS EM: 다중 라벨 유닛에서 검수자 오류율 추정
        from . import quality as Q
        fmap = st.feedback_map(team=team) if st else {}
        ds = (Q.dawid_skene_binary(Q.feedback_labels(fmap)) or {}).get("reviewers") or {}
    except Exception:
        ds = {}
    out = {}
    for rv in set(gold) | set(ds):
        accs = []
        g = gold.get(rv) or {}
        if g.get("n", 0) >= 5:
            accs.append(float(g.get("acc") or 0.0))
        d = ds.get(rv) or {}
        if d.get("n", 0) >= 5 and d.get("error_rate") is not None:
            accs.append(max(0.0, 1.0 - float(d["error_rate"])))
        if accs:
            out[rv] = round(0.5 + 0.5 * (sum(accs) / len(accs)), 4)
    return out


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
    # 닉네임 변경: 이름만 교체(팀·캐릭터·이력 유지) · 오입력 자가 수정용
    if data.get("mode") == "rename":
        name = (data.get("name") or "").strip()[:20]
        if not name:
            return {"ok": False, "error": "닉네임을 입력하세요"}
        ch = (data.get("char") or "boksil").strip()
        if _supa():
            st.set_reviewer(rv, name, ch)            # team_id 미전달 = 팀 유지(upsert 부분 갱신)
        elif hasattr(st, "rename_reviewer"):
            r = st.rename_reviewer(rv, name)         # sqlite: 키=이름 → 이력 키 이관
            if not r.get("ok"):
                return r
        _agg_bump()                                  # 리더보드 등 집계에 새 이름 즉시 반영
        broadcast({"type": "reviewer", "reviewer": name, "char": ch})
        return {"ok": True, "name": name, "char": ch}
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


def _fb_epoch(ts) -> float:
    """feedback ts → epoch. sqlite=float · supabase=timestamptz 문자열(supastore._epoch 와 동일 해석)."""
    try:
        return float(ts)
    except (TypeError, ValueError):
        try:
            return time.mktime(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            return 0.0


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
        seq = int(st.batch_seq(team) if hasattr(st, "batch_seq") else 0)
        d["next_version"] = seq + 1
        # 카드의 모델 = 검수 대상 초안을 만든 모델(provenance) · 설정 모델은 폴백
        # (설정 모델을 그대로 쓰면 claude 초안을 검수 중인데 solar 가 표기되는 오표기)
        d["next_model"] = cfg.model or ""
        try:
            tm = st.target_models(team) if hasattr(st, "target_models") else []
            d["target_models"] = tm
            if tm:
                d["next_model"] = " · ".join(tm)
        except Exception:
            d["target_models"] = []
        d["next_batch_at"] = LO.next_batch_time(getattr(cfg, "learn_next_at", ""))
        d["last_version"] = seq                    # 완료 잔상(소진 후 '반영 완료' 카드)용
        d["last_batch_at"] = float((_report_get("learn_report", team) or {}).get("ts") or 0)
        # 퀘스트 진행률은 이번 퀘스트 창으로 스코프: 생성 이후 검수된 대상만 집계.
        # 전 기간 누적(total-queue)을 쓰면 직전 버전에서 끝낸 검수가 새 퀘스트에 '완주'로 잡힌다.
        if d.get("next_batch_at"):
            # 유효 검수 = '그 콘텐츠의 현재(최신) 초안 생성 이후'의 표. 퀘스트 생성 시각 창은
            # 생성 전에 해 둔 현행 초안 검수를 놓쳐 홈 팀 진척율과 어긋난다(hash×버전 스키마 전까지의 근사.
            # 초안 시각 미상 콘텐츠는 전부 유효 취급 · 퀘스트 중 재실행은 가드로 차단되어 창이 흔들리지 않음)
            try:
                dts = st.draft_times(team) if hasattr(st, "draft_times") else {}
            except Exception:
                dts = {}
            fm = st.feedback_map(team=team) or {}
            per = {}                              # 검수자 → 유효 검수한 대상 집합
            for ch, e in fm.items():
                base = float(dts.get(ch) or 0)
                for v in e.get("verdicts") or []:
                    if _fb_epoch(v.get("ts")) >= base:
                        rid = v.get("reviewer_id") or v.get("reviewer") or ""
                        per.setdefault(rid, set()).add(ch)
            d["quest_done"] = len(set().union(*per.values())) if per else 0   # 커버리지(구클라 폴백)
            # 목표 '전량 완주'의 진척 = 팀 평균 검수 건수(홈 히어로의 팀 진척율과 같은 관점)
            try:
                members = len(set(st.reviewers_map(team) if hasattr(st, "reviewers_map") else {}) | set(per))
            except Exception:
                members = len(per)
            d["quest_avg_done"] = round(sum(len(s) for s in per.values()) / members) if members else 0
            qs = float((_report_get("quest_meta", team) or {}).get("started_at") or 0)
            if qs:
                d["quest_started_at"] = qs
    except Exception:
        pass
    return d


def board_data(team=None, uid: str = "") -> dict:
    """게시판(기능개선·오류 제보) 목록 · 팀 스코프.
    작성자는 uid 로 저장하고 표시명은 조회 시점에 해석 → 닉네임 변경이 자동 반영된다."""
    st = get_store()
    if not (st and hasattr(st, "board_list")):
        return {"items": [], "n": 0}
    items = st.board_list(team=team)
    if _supa():
        names = st.reviewers_map(None) if hasattr(st, "reviewers_map") else {}
        for it in items:
            meta = names.get(it["author_id"]) or {}
            it["author"] = meta.get("name") or (it["author_id"][:8] or "(탈퇴)")
            it["mine"] = bool(uid and it["author_id"] == uid)
    else:                                            # sqlite: 키=이름 · 단일 사용자 = 전부 내 글
        for it in items:
            it["author"] = it["author_id"]
            it["mine"] = True
    return {"items": items, "n": len(items)}


def board_action(data: dict, team=None, uid: str = "", email: str = "") -> dict:
    """게시판 동작: 등록=팀원 · 상태 변경=관리자 · 삭제=작성자 또는 관리자."""
    st = get_store()
    if not (st and hasattr(st, "board_add")):
        return {"ok": False, "error": "게시판을 지원하지 않는 저장소입니다"}
    act = (data.get("action") or "create").strip()
    rv = (data.get("reviewer") or "").strip()        # sqlite=이름 · supabase=_inject_reviewer 가 uid 주입
    if act == "create":
        title = (data.get("title") or "").strip()[:80]
        if not title:
            return {"ok": False, "error": "제목을 입력하세요"}
        st.board_add("feature" if data.get("kind") == "feature" else "bug",
                     title, (data.get("body") or "").strip()[:2000], rv, team=team)
    else:
        it = st.board_get(int(data.get("id") or 0), team=team)
        if not it:
            return {"ok": False, "error": "항목을 찾을 수 없습니다"}
        admin = (not _supa()) or is_admin_user(uid, team, email)
        if act == "status":
            if not admin:
                return {"ok": False, "error": "상태 변경은 관리자 전용입니다"}
            if data.get("status") not in ("open", "doing", "done"):
                return {"ok": False, "error": "상태 값이 올바르지 않습니다"}
            st.board_set_status(it["id"], data["status"], team=team)
        elif act == "delete":
            if not (admin or (it["author_id"] and it["author_id"] == (uid or rv))):
                return {"ok": False, "error": "작성자 또는 관리자만 삭제할 수 있습니다"}
            st.board_delete(it["id"], team=team)
        else:
            return {"ok": False, "error": "알 수 없는 동작입니다"}
    out = board_data(team, uid or rv)
    out["ok"] = True
    return out


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
    try:                                           # 콘텐츠별 검수 담당 배정(있으면 표에 표시)
        asg = st.assignees(team=team) if (st and hasattr(st, "assignees")) else {}
    except Exception:
        asg = {}
    out = []
    for r in reversed(rows[-int(limit):]):         # 최근순
        ref = r.get("content_ref") or {}
        im = r.get("item_meta") or {}
        qm = r.get("quality_meta") or {}
        tr = r.get("trace") or {}
        ch = _row_key(ref)
        if pmap.get(ch) == "eval":
            continue
        if _is_pending_row(r):                     # 미실행(STEP 1 추가만) 콘텐츠는 검수 대상 아님
            continue
        fb = fmap.get(ch) or {}
        # 내 판정(mine)·내 교정(note·elems) 계산은 _fb_public 단일 원천(드릴 목록과 동일 규약).
        # (합의가 동점 split 인데 배지가 '수정 필요'로 뭉뚱그려져 "정확으로 바꿨는데 수정필요로 조회" 혼란 방지)
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
                    "fb": _fb_public(fb, reviewer),
                    "assignees": (asg.get(ch) or {}).get("reviewers", []),
                    "min_reviewers": (asg.get(ch) or {}).get("min", 0),
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


def _hist_epoch(ts):
    """이력 정렬용 epoch: sqlite=float · supabase 피드백=ISO 문자열 혼재를 흡수."""
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def content_history(content_hash: str, team=None) -> dict:
    """콘텐츠 단위 작업 이력(최신순): 판정(피드백 표) + 교정·재실행(patch_log).
    검수 화면에서 누가 언제 무엇을 했는지 시각화(2026-07-08 회의 소요)."""
    st = get_store()
    ch = (content_hash or "").strip()
    if not (st and ch):
        return {"ok": False, "items": []}
    items = []
    try:
        fb = (st.feedback_map(team=team) or {}).get(ch) or {}
        for v in (fb.get("verdicts") or []):
            verdict = v.get("verdict") or ""
            items.append({"kind": "verdict", "who": v.get("reviewer") or "",
                          "ts": _hist_epoch(v.get("ts")),
                          "label": ("판정 · 정확" if verdict == "good"
                                    else "판정 · 수정 필요" if verdict == "bad" else "판정 취소"),
                          "note": (v.get("note") or "")[:200]})
    except Exception:
        pass
    try:
        for pr in (st.patch_rows(team=team) if hasattr(st, "patch_rows") else []):
            if pr.get("hash") != ch:
                continue
            el = pr.get("element") or ""
            if el.startswith("rerun:"):
                items.append({"kind": "rerun", "who": "",
                              "ts": _hist_epoch(pr.get("ts")),
                              "label": "초안 재실행 · " + el[len("rerun:"):].replace("->", " → "), "note": ""})
            elif el == "undo:verdict":             # 판정 실행취소(표 행은 삭제돼도 취소 사실은 남긴다)
                items.append({"kind": "undo", "who": pr.get("reviewer") or "",
                              "ts": _hist_epoch(pr.get("ts")), "label": "판정 취소", "note": ""})
            else:
                items.append({"kind": "patch", "who": pr.get("reviewer") or "",
                              "ts": _hist_epoch(pr.get("ts")),
                              "label": "교정 · " + (el or "요소"), "note": ""})
    except Exception:
        pass
    items.sort(key=lambda x: x["ts"], reverse=True)
    return {"ok": True, "items": items[:100], "n": len(items)}


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
    rv = (data.get("reviewer") or "").strip()
    items = st.review_queue(limit=limit, only_unreviewed=bool(only_un), team=data.get("team"),
                            reviewer=rv or None)                # 배정 콘텐츠 배타 노출
    items = _inject_gold(items, rv, data.get("team"))
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


def team_links() -> dict:
    """팀 가이드 링크(reports kind='team_links' 전역 행 · 운영 관리자가 시스템 설정에서 등록).
    내부 위키 URL 은 코드에 두지 않는다(공개 데모 docs/demo.html 유출 방지)."""
    try:
        st = get_store()
        d = st.get_report("team_links") if st else None
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_team_links(data: dict):
    st = get_store()
    if st:
        st.save_report("team_links", {k: str(data.get(k) or "").strip()
                                      for k in ("guide", "guide_user", "guide_admin")})


def config_status() -> dict:
    cfg = Config.load()
    base = (cfg.chat_url or "").rsplit("/chat/completions", 1)[0]
    return {
        "hasKey": bool(IMG._api_key()),
        "persisted": os.path.exists(_KEY_PATH),
        "bootId": _BOOT_ID,
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
        "metaFourCalls": bool(getattr(cfg, "meta_four_calls", True)),
        "metaCallModels": dict(getattr(cfg, "meta_call_models", {}) or {}),
        "familyWrappers": dict(getattr(cfg, "family_wrappers", {}) or {}),
        "familyWrapperDefaults": dict(MP.FAMILY_WRAPPER_DEFAULT),
        "metaCalls": list(MP.CALLS),
        "metaContract": {"rules": dict(MP.CALL_RULES), "examples": MP.gold_examples(None)},
        "ingestSources": list(cfg.ingest_sources or []),
        "guideUrls": team_links(),
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


def apply_config(data: dict, allow_key: bool = False, team=None) -> dict:
    """키/모델/엔드포인트/추론강도/추가지시 적용. 키만 프로세스 환경(+옵션 ~/.prism_key).
    운영(supabase): 키 변경은 운영 관리자(allow_key=True, /config 게이트에서 판정)만 허용.
    비관리자·미인증 요청의 키 필드는 무시(서버 키 보호)."""
    if backend_mode()[0] == "supabase" and not allow_key:
        data = {k: v for k, v in data.items()
                if k not in ("api_key", "persist", "forget", "bizrouter_api_key", "timely_api_key")}
    key = (data.get("api_key") or "").strip()
    if key:
        os.environ["UPSTAGE_API_KEY"] = key
        _seed_solar_defaults()                        # 키만 저장해도 바로 호출 가능하게
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
    has_misc = ("golden_min_good" in data) or ("learn_next_at" in data)
    if (model or base or reasoning or has_sp or has_stage or has_slot or has_legal or has_ingest
            or has_smodels or has_mprompts or has_wrappers or has_callm or has_4c or has_misc):
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
        if "learn_next_at" in data:               # 검수 목표(퀘스트) 일시 · 빈 값 = 목표 해제(삭제)
            v = str(data.get("learn_next_at") or "").strip()[:16]
            if not v:
                cfg.learn_next_at = ""
                cfg.learn_team = ""                # 퀘스트 해제 시 팀 태그도 비움
                _agg_bump()                        # 홈·사이드바 퀘스트 카드 즉시 소거
            else:
                try:
                    time.strptime(v, "%Y-%m-%dT%H:%M")
                    # 과거 일시는 거부: 저장 즉시 반영이 돼버리는 함정 방지('⚡ 즉시 반영'이 정식 경로)
                    if LO.next_batch_time(v) > time.time() + 60:
                        if not getattr(cfg, "learn_next_at", ""):
                            # 새 퀘스트 생성 = 진행률 창의 시작점(일시 수정은 시작점 유지)
                            _report_save("quest_meta", {"started_at": time.time(), "next_at": v}, team)
                        cfg.learn_next_at = v
                        cfg.learn_team = team or ""   # 스케줄러가 이 팀으로 배치 → 골든·버전이 팀에 태깅
                        _agg_bump()
                except ValueError:
                    pass
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


_SOLAR_BASE_DEFAULT = "https://api.upstage.ai/v1"
_SOLAR_MODEL_DEFAULT = "solar-pro2"


def _seed_solar_defaults():
    """Upstage 키 저장 시 엔드포인트·기본 모델이 비어 있으면 기본값을 채워 저장.
    (새 설치에서 키만 등록하면 연결 테스트·실행이 '엔드포인트·모델 미설정'으로 죽는 함정 방지)"""
    try:
        cfg = Config.load()
        if cfg.is_configured():
            return
        if not cfg.chat_url:
            cfg.set_base_url(_SOLAR_BASE_DEFAULT)
        if not cfg.model:
            cfg.model = _SOLAR_MODEL_DEFAULT
        cfg.save_template()
    except Exception:
        pass


def ping_router(service: str) -> dict:
    """라우터(BizRouter·Timely) 키로 모델 목록을 조회하여 연결 검증(OpenAI 호환 /models)."""
    info = IMG.ROUTERS.get(service)
    if not info:
        return {"ok": False, "detail": "알 수 없는 서비스"}
    key = IMG.router_key(service)
    if not key:
        return {"ok": False, "detail": "키가 설정되지 않았습니다"}
    try:
        req = urllib.request.Request(info["base"].rstrip("/") + "/models",
                                     headers={"Authorization": "Bearer " + key})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=12) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
        n = len(body.get("data") or []) if isinstance(body, dict) else 0
        return {"ok": True, "detail": info["label"] + " 연결 정상" + (f" · 모델 {n}종" if n else ""),
                "latency_ms": int((time.time() - t0) * 1000)}
    except urllib.error.HTTPError as e:
        return {"ok": False, "detail": f"HTTP {e.code} · 키 또는 권한을 확인하세요"}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


def ping_model() -> dict:
    """현재 키/설정으로 실제 1회 호출하여 연결 검증."""
    if not IMG._api_key():
        return {"ok": False, "detail": "API 키가 설정되지 않았습니다"}
    try:
        cfg = Config.load()
        if not cfg.chat_url:                          # 미설정이면 기본 엔드포인트·모델로 검증
            cfg.set_base_url(_SOLAR_BASE_DEFAULT)
        if not cfg.model:
            cfg.model = _SOLAR_MODEL_DEFAULT
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
# 공개 GET 경로(무인증): 페이지 셸·정적 자산·백엔드 상태(/config 는 라우트에서 최소 필드로
# 축약)·업로드 서식만. 그 외 데이터 GET 은 supabase(운영) 모드에서 로그인 필수(Handler._gate_get).
_PUBLIC_GET = {"/", "/m", "/config", "/favicon.ico", "/template.xlsx", "/template.csv",
               "/usermeta-template.csv", "/usermeta-profile-template.csv"}


def is_public_get(path: str) -> bool:
    """무인증 허용 GET 경로 판정(쿼리 무시 · 말미 슬래시 정규화)."""
    p = (path or "").split("?", 1)[0]
    if p.startswith("/vendor/"):
        return True
    return (p.rstrip("/") or "/") in _PUBLIC_GET


class Handler(BaseHTTPRequestHandler):
    server_mock = False

    def log_message(self, *a):
        pass

    _GZIP_CT = ("text/html", "application/json", "text/css", "application/javascript",
                "text/csv", "image/svg", "text/plain")

    def _send(self, code, body, ctype="text/html; charset=utf-8", cache=""):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        # 전송 압축: 텍스트 응답 · 1KB 이상 · 클라이언트 gzip 수용 시(첫 로드 4.7MB → ~1MB 실측 근거)
        if (code == 200 and len(data) > 1024
                and any(t in ctype for t in self._GZIP_CT)
                and "gzip" in (self.headers.get("Accept-Encoding") or "")):
            data = gzip.compress(data, 6)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(data)))
        if cache:
            self.send_header("Cache-Control", cache)
        elif "text/html" in ctype:      # WKWebView 가 옛 페이지를 캐시하지 않도록
            self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def _gate_get(self):
        """supabase(운영) 모드 데이터 GET 전역 게이트: 공개 경로 외에는 로그인(JWT) 필수.
        무인증 GET 이 팀 필터 없이(team=None) 전 팀의 콘텐츠·검수 데이터를 내려주던
        노출 차단(2026-07-10). sqlite(로컬 단독)는 기존대로 개방.
        SSE(/events)는 EventSource 가 헤더를 못 실어 token 쿼리 파라미터로 검증."""
        if not _supa() or is_public_get(self.path):
            return True
        if self.path.split("?", 1)[0].rstrip("/") == "/events":
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            return bool(validate_jwt((q.get("token") or [""])[0]))
        return bool(self._bearer_uid())

    def do_GET(self):
        if not self._gate_get():
            self._send(401, json.dumps({"error": "로그인이 필요합니다"}, ensure_ascii=False), _JSON)
            return
        if self.path.startswith("/report"):
            self._send(200, build_report_html())
        elif self.path.startswith("/export.csv"):
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=prism_results.csv")
            self.end_headers()
            self.wfile.write(build_results_csv())
        elif self.path.startswith("/config"):
            cs = config_status()
            # 운영(supabase) 무인증: 프롬프트 계약·모델 슬롯·팀 가이드 URL 은 로그인 후에만.
            # 로그인 화면·배포 검증(curl /config: backend·configured)이 쓰는 최소 필드만 공개.
            if _supa() and not self._bearer_uid():
                cs = {k: cs[k] for k in ("bootId", "build", "configured", "forcedMock",
                                         "backend", "authRequired", "keyManagedByServer") if k in cs}
            self._send(200, json.dumps(cs, ensure_ascii=False), _JSON)
        elif self.path.startswith("/models"):
            self._send(200, json.dumps(list_models(), ensure_ascii=False), _JSON)
        elif self.path.startswith("/vocab"):
            self._send(200, json.dumps(vocab(), ensure_ascii=False), _JSON)
        elif self.path.startswith("/entdict-lookup"):   # 검수 화면: 콘텐츠 엔티티 → 사전 정보(타입·속성)
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            names = [n for n in (q.get("names", [""])[0]).split("|") if n.strip()]
            st = get_store()
            found = st.ent_by_names(names) if (st and hasattr(st, "ent_by_names")) else {}
            self._send(200, json.dumps({"ok": True, "entities": found}, ensure_ascii=False), _JSON)
        elif self.path.startswith("/entdict"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            self._send(200, json.dumps(entdict_data(
                q=q.get("q", [""])[0], type_=q.get("type", [""])[0],
                status=q.get("status", [""])[0], limit=int(q.get("limit", ["300"])[0])),
                ensure_ascii=False), _JSON)
        elif self.path.startswith("/dict"):
            self._send(200, json.dumps(dict_data(), ensure_ascii=False), _JSON)
        elif self.path.startswith("/topic-drill"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            self._send(200, json.dumps(topic_drill(q.get("cluster", [""])[0], self._req_team(),
                                       reviewer=(self._bearer_uid() or q.get("reviewer", [""])[0])),
                                       ensure_ascii=False), _JSON)
        elif self.path.startswith("/topics"):
            self._send(200, json.dumps(topics_data(), ensure_ascii=False), _JSON)
        elif self.path.startswith("/dashboard"):
            self._send(200, json.dumps(dashboard_data(self._req_team()), ensure_ascii=False), _JSON)
        elif self.path.startswith("/drill"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            self._send(200, json.dumps(drill_contents(q.get("kind", [""])[0], q.get("value", [""])[0],
                                                       self._req_team(),
                                                       reviewer=(self._bearer_uid() or q.get("reviewer", [""])[0])),
                                       ensure_ascii=False), _JSON)
        elif self.path.startswith("/arena"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            d = dict(arena_data(self._req_team()))
            rv = self._bearer_uid() or q.get("reviewer", [""])[0]
            if rv:
                d["missions"] = mission_progress(rv, self._req_team())
            d["my_id"] = rv or ""                     # 내 행 식별 = reviewer_id(닉네임 변경·중복 표시명 무관)
            self._send(200, json.dumps(d, ensure_ascii=False), _JSON)
        elif self.path.startswith("/admin"):
            # 메뉴 게이팅의 원천: 인증 서버 일시 장애는 '비관리자(200)'가 아니라 503(재시도)으로 구분
            auth = self.headers.get("Authorization", "")
            token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
            try:
                uid = AO.validate_jwt(token, strict=True)
            except AO.AuthBackendUnavailable:
                self._send(503, json.dumps({"ok": False, "error": "인증 서버 연결 지연 · 자동 재시도됩니다"},
                                           ensure_ascii=False), _JSON)
                return
            if token and _supa() and not uid:
                # 토큰이 있는데 무효 = 만료(1시간) · '비관리자(200)'로 뭉개면 관리자 메뉴가 조용히 강등된다
                self._send(401, json.dumps({"ok": False, "error": "로그인이 만료됐습니다 · 세션 갱신 필요"},
                                           ensure_ascii=False), _JSON)
                return
            self._send(200, json.dumps(admin_data(uid, self._req_team(), self._bearer_email()),
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
        elif self.path.startswith("/history"):           # 검수 상세: 콘텐츠 작업 이력(판정·교정·재실행)
            # 운영(supabase): 팀 생성자·슈퍼관리자 전용(누가 언제 판정했는지 = 민감 정보) · 로컬 단독 실행은 그대로
            if _supa() and not is_super_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                self._send(403, json.dumps({"error": "팀 생성자·슈퍼관리자 전용입니다"}, ensure_ascii=False), _JSON)
                return
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            self._send(200, json.dumps(content_history(q.get("hash", [""])[0], self._req_team()),
                                       ensure_ascii=False), _JSON)
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
        elif self.path.startswith("/learn-report"):     # 최근 배치 결과(GET) · ?v=N 이면 그 버전 리포트
            from urllib.parse import parse_qs, urlparse
            _rv = (parse_qs(urlparse(self.path).query).get("v") or [""])[0].strip()
            if _rv.isdigit():                            # 버전 히스토리 상세(구버전은 미영속 → null)
                _vrep = _report_get(f"learn_report_v{int(_rv)}", self._req_team())
                self._send(200, json.dumps({"ok": bool(_vrep), "report": _vrep, "version": int(_rv)},
                                           ensure_ascii=False), _JSON)
                return
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
        elif self.path.startswith("/handoff-export"):    # 모델러 핸드오프 번들(.zip · 관리자)
            if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                return
            fname, blob = handoff_bundle(self._req_team())
            if not fname:
                self._send(400, json.dumps({"error": blob}, ensure_ascii=False), _JSON)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)
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
            # 검수 진행(반영 대기): 골든은 학습 반영 시에만 확정되므로, 반영 전에도 검수가
            # 쌓이고 있음을 현황에 표시(전부 0 + 안내 없음 = "표시가 안 된다" 혼란 방지)
            reviewed_n = good_n = 0
            try:
                for e in (st.feedback_map(team=self._req_team()) or {}).values():
                    reviewed_n += 1
                    if e.get("consensus") == "good":
                        good_n += 1
            except Exception:
                pass
            self._send(200, json.dumps({
                "ok": True,
                "batch_seq": (st.batch_seq(self._req_team()) if (st and hasattr(st, "batch_seq")) else 0),
                "total": (st.golden_count(self._req_team()) if (st and hasattr(st, "golden_count")) else 0),
                "source_counts": (st.golden_source_counts(self._req_team())
                                  if (st and hasattr(st, "golden_source_counts")) else {}),
                "reviewed": {"contents": reviewed_n, "good": good_n},
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
        elif self.path.startswith("/usermeta-profile-template.csv"):
            from . import personagen as PG
            data = PG.profile_template_csv()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="prism_user_profile.csv"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path.startswith("/usermeta"):
            self._send(200, json.dumps(usermeta_data(team=self._req_team()), ensure_ascii=False), _JSON)
        elif self.path.startswith("/board"):             # 게시판: 기능개선·오류 제보(팀 스코프)
            self._send(200, json.dumps(board_data(self._req_team(), self._bearer_uid() or ""),
                                       ensure_ascii=False), _JSON)
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
        elif self.path.split("?", 1)[0].rstrip("/") == "/m":   # 모바일 검수 전용(검수만 덜어낸 카드 UI)
            self._send(200, _mpage_versioned())
        elif self.path.startswith("/vendor/"):
            self._send_vendor(self.path.split("?", 1)[0].rsplit("/", 1)[-1])
        elif self.path.split("?", 1)[0] == "/favicon.ico":     # 브라우저 기본 요청: SPA 폴스루(323KB HTML) 방지
            self._send_vendor("prism-favicon.svg")
        elif self.path.split("?", 1)[0].rstrip("/") in ("", "/"):
            self._send(200, _page_versioned())
        else:                                                  # 미등록 경로 404: API 오타가 SPA HTML 200 으로 가려지지 않게
            self._send(404, json.dumps({"error": "not found", "path": self.path.split("?", 1)[0][:80]},
                                       ensure_ascii=False), _JSON)

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
        return team_of(self._bearer_uid())

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

    def _require_login(self):
        """supabase 모드: 유효한 로그인(Bearer uid) 필수. 통과 시 True, 실패 시 401 응답 후 False.
        조회성·실험(LLM 호출) 엔드포인트의 익명 접근·비용 남용을 차단한다."""
        if not _supa():
            return True
        if self._bearer_uid():
            return True
        self._send(401, json.dumps({"error": "로그인이 필요합니다"}, ensure_ascii=False), _JSON)
        return False

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
            # 접속 인사에 부팅 ID 동봉: 배포로 서버가 교체되면 재연결 시 값이 달라진다(새 버전 배너 트리거)
            self.wfile.write(("data: " + json.dumps({"type": "hello", "boot": _BOOT_ID}) + "\n\n").encode("utf-8"))
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
        ".webmanifest": "application/manifest+json; charset=utf-8",
    }

    def _send_vendor(self, name):
        safe = os.path.basename(name)
        ext = os.path.splitext(safe)[1].lower()
        path = os.path.join(os.path.dirname(__file__), "vendor", safe)
        if ext not in self._VENDOR_CT or not os.path.isfile(path):
            self._send(404, "not found")
            return
        # 캐시 정책: 페이지가 ?v=부팅ID 버스터를 달아 주므로 버스터 有 = 불변 1년.
        # 폰트는 css 에서 버스터 없이 참조되지만 사실상 불변 자산 → 30일.
        if ext in (".woff", ".woff2"):
            cache = "public, max-age=2592000"
        elif "?v=" in self.path:
            cache = "public, max-age=31536000, immutable"
        else:
            cache = "public, max-age=3600"
        with open(path, "rb") as f:
            self._send(200, f.read(), self._VENDOR_CT[ext], cache=cache)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if self.path.startswith("/config"):
            try:
                # 운영(supabase): 팀 공유 설정(모델·프롬프트·인입 등)은 관리자만 변경
                uid, team, email = self._bearer_uid(), self._req_team(), self._bearer_email()
                if _supa() and not is_admin_user(uid, team, email):
                    self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                    return
                # API 키 등록·삭제는 운영 관리자만(관리자 로컬 앱 = 서버 · ~/.prism_key 저장)
                allow_key = (not _supa()) or is_sys_admin_user(uid, team, email)
                data = json.loads(body or b"{}")
                if isinstance(data.get("team_links"), dict):
                    # 팀 가이드 링크(전역 공유) = 운영 관리자만
                    if not allow_key:
                        self._send(403, json.dumps({"error": "운영 관리자 전용입니다"}, ensure_ascii=False), _JSON)
                        return
                    save_team_links(data["team_links"])
                self._send(200, json.dumps(apply_config(data, allow_key=allow_key, team=team),
                                           ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/ping"):
            try:
                d = json.loads(body or b"{}")
            except Exception:
                d = {}
            svc = (d.get("service") or "").strip()
            out = ping_router(svc) if svc in IMG.ROUTERS else ping_model()
            self._send(200, json.dumps(out, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/store"):
            try:
                payload = json.loads(body or b"{}")
                team = self._req_team()
                if payload.get("clear") and _supa() and not is_admin_user(
                        self._bearer_uid(), team, self._bearer_email()):
                    self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                    return
                st = get_store()
                if payload.get("clear") and st:
                    if _supa() and not team:            # 팀 스코프 없이 전 팀 삭제 금지
                        self._send(403, json.dumps({"error": "팀 스코프가 필요합니다"}, ensure_ascii=False), _JSON)
                        return
                    if hasattr(st, "clear_team_contents"):
                        st.clear_team_contents(team)
                    else:
                        st.clear()
                    _LAST_RESULTS[:] = []          # 메모리 미러 동반 정리(삭제 후 잔상 방지)
                    _agg_bump()
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
                if data.get("clear"):
                    # 전체 초기화 = 관리자 전용 + 팀 스코프(무인증·전 팀 삭제 방지)
                    uid, team, email = self._bearer_uid(), self._req_team(), self._bearer_email()
                    if _supa() and not is_admin_user(uid, team, email):
                        self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                        return
                    data["_team"] = team
                elif not self._inject_reviewer(data):
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
            _TEAM_CACHE.pop(self._bearer_uid() or "", None)   # 가입·팀 변경 즉시 반영
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
                # 실모델 호출(비용) 트리거 · 무인증 차단(형제 라우트 /learn-batch 와 동일 게이트)
                if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                    self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                    return
                self._send(200, json.dumps(meta_compile_run(self._req_team()),
                                           ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/eval-judge"):        # 평가 상세 · 건별 판정(집단 지성)
            try:
                # 팀원 기능이지만 미인증 직접 호출은 차단(supabase 모드 · 판정 위조 방지)
                if _supa() and not self._bearer_uid():
                    self._send(403, json.dumps({"error": "로그인이 필요합니다"}, ensure_ascii=False), _JSON)
                    return
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
                # 실모델 호출(비용) 트리거 · 무인증 차단
                if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                    self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                    return
                data = json.loads(body or b"{}")
                self._send(200, json.dumps(eval_golden(self._req_team(),
                                           model=(data.get("model") or "").strip(),
                                           scope=(data.get("scope") or "all").strip()), ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/content-remove"):    # 관리자: 콘텐츠 개별 삭제(파생 데이터 연쇄)
            try:
                if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                    self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                    return
                data = json.loads(body or b"{}")
                st = get_store()
                ok = bool(st and hasattr(st, "remove_content")
                          and st.remove_content((data.get("hash") or "").strip(), team=self._req_team()))
                _agg_bump()
                self._send(200, json.dumps({"ok": ok}, ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/content-assign-bulk"):   # 슈퍼관리자: 여러 콘텐츠 일괄 배정(덮어쓰기)
            try:
                if _supa() and not is_super_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                    self._send(403, json.dumps({"error": "슈퍼관리자 전용입니다"}, ensure_ascii=False), _JSON)
                    return
                data = json.loads(body or b"{}")
                hashes = [str(h).strip() for h in (data.get("hashes") or []) if str(h).strip()]
                reviewers = [str(r).strip() for r in (data.get("reviewers") or []) if str(r).strip()]
                try:
                    minr = int(data.get("min_reviewers") or 1)
                except (TypeError, ValueError):
                    minr = 1
                st = get_store()
                if not (hashes and st and hasattr(st, "set_assignees_bulk")):
                    self._send(400, json.dumps({"error": "대상 없음 또는 미지원 백엔드"}, ensure_ascii=False), _JSON)
                    return
                n = st.set_assignees_bulk(hashes, reviewers, min_reviewers=minr, team=self._req_team())
                _agg_bump()
                self._send(200, json.dumps({"ok": True, "n": n, "reviewers": reviewers,
                                            "min_reviewers": (max(1, min(len(reviewers), minr)) if reviewers else 0)},
                                           ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/content-assign"):    # 관리자: 콘텐츠 검수 담당자 배정(배타적 노출)
            try:
                if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                    self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                    return
                data = json.loads(body or b"{}")
                h = (data.get("hash") or "").strip()
                reviewers = [str(r).strip() for r in (data.get("reviewers") or []) if str(r).strip()]
                try:
                    minr = int(data.get("min_reviewers") or 1)
                except (TypeError, ValueError):
                    minr = 1
                st = get_store()
                if not (h and st and hasattr(st, "set_assignees")):
                    self._send(400, json.dumps({"error": "hash 누락 또는 미지원 백엔드"}, ensure_ascii=False), _JSON)
                    return
                st.set_assignees(h, reviewers, min_reviewers=minr, team=self._req_team())
                _agg_bump()
                cur = (st.assignees(team=self._req_team()) or {}).get(h) or {"reviewers": [], "min": 0}
                self._send(200, json.dumps({"ok": True, "assignees": cur["reviewers"],
                                            "min_reviewers": cur["min"]}, ensure_ascii=False), _JSON)
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

        if self.path.startswith("/backfill-urls"):   # 관리자: 원문 링크 백필(source_url 만 갱신 · 초안·판정 불변)
            try:
                if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                    self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                    return
                ctype = self.headers.get("Content-Type", "")
                if "multipart/form-data" not in ctype:
                    self._send(400, json.dumps({"error": "매핑 파일이 필요합니다(multipart)"}, ensure_ascii=False), _JSON)
                    return
                fields = _parse_multipart(body, ctype.split("boundary=", 1)[1].strip())
                f = fields.get("file")
                if not isinstance(f, dict) or not f.get("bytes"):
                    self._send(400, json.dumps({"error": "파일이 없습니다"}, ensure_ascii=False), _JSON)
                    return
                r = backfill_urls(f["bytes"], f.get("filename", "map.csv"), team=self._req_team())
                self._send(200, json.dumps(r, ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/presence"):
            try:
                # 팀 SSE 방송 트리거 · 미인증 직접 호출 차단(/eval-judge 와 동일 게이트)
                if _supa() and not self._bearer_uid():
                    self._send(403, json.dumps({"error": "로그인이 필요합니다"}, ensure_ascii=False), _JSON)
                    return
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
                scope = (data.get("scope") or "all").strip()
                self._send(200, json.dumps(rerun_all((data.get("model") or "").strip(), self._req_team(),
                                                     scope=(scope if scope in ("all", "pending") else "all")),
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

        if self.path.startswith("/entdict"):
            try:
                data = json.loads(body or b"{}")
                # 권한 분리: 상세·수정·보강은 검수자(로그인)도 가능(검수 중 사전 교정 허용) ·
                # 등재/삭제/일괄 보강/백필 같은 사전 전체 작업은 관리자 전용(사전·정책과 동일 게이트)
                act = (data.get("action") or "").strip()
                if _supa():
                    if act in ("detail", "update", "enrich"):
                        if not self._bearer_uid():
                            self._send(401, json.dumps({"error": "로그인이 필요합니다"}, ensure_ascii=False), _JSON)
                            return
                    elif not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                        self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                        return
                self._send(200, json.dumps(entdict_action(data, team=self._req_team(),
                           mock=Handler.server_mock), ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/dict"):
            try:
                # 사전·정책 편집 = 관리자 전용(supabase 모드 · UI 게이팅과 정합)
                if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
                    self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                    return
                payload = json.loads(body or b"{}")
                fn = reset_dict_overrides if payload.get("reset") else (lambda: edit_dict(payload))
                self._send(200, json.dumps(fn(), ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/board"):               # 게시판: 등록·상태 변경·삭제(팀 스코프)
            try:
                data = json.loads(body or b"{}")
                if not self._inject_reviewer(data):
                    self._send(401, json.dumps({"error": "인증 필요"}, ensure_ascii=False), _JSON)
                    return
                self._send(200, json.dumps(board_action(data, team=self._req_team(),
                           uid=self._bearer_uid() or "", email=self._bearer_email()),
                           ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/topic-studio"):        # 토픽 스튜디오: 생성·삭제·튜닝(변경은 관리자) · 미리보기·제안(조회)
            try:
                data = json.loads(body or b"{}")
                action = (data.get("action") or "").strip()
                # 조회성(preview·suggest)은 로그인 필수(익명 LLM 호출·데이터 열람 차단) · 변경성은 /config 와 동일 관리자 가드
                if action not in ("preview", "suggest"):
                    uid, team, email = self._bearer_uid(), self._req_team(), self._bearer_email()
                    if _supa() and not is_admin_user(uid, team, email):
                        self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
                        return
                elif not self._require_login():
                    return
                # 변경성 액션의 캐시 무효화는 topic_studio_action 내부에서 처리
                self._send(200, json.dumps(topic_studio_action(data, mock=Handler.server_mock),
                                           ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/media-extract"):        # 미디어 메타 파이프라인: 자막 파싱(JSON) · 영상 네이티브(multipart)
            try:
                if not self._require_login():              # 익명 LLM 호출(비용 남용) 차단
                    return
                ctype = self.headers.get("Content-Type", "")
                if "multipart/form-data" in ctype:        # 업로드 → 미디어 실험(미저장): 이미지(image*) | 영상(file)
                    fields = _parse_multipart(body, ctype.split("boundary=", 1)[1].strip())
                    imgs = {k: v for k, v in fields.items()
                            if k.startswith("image") and isinstance(v, dict) and v.get("bytes")}
                    if imgs:                              # 이미지 실험: run_pipeline 이미지 분기 재사용(미저장)
                        pf = {"displayServiceName": fields.get("displayServiceName", "포토"),
                              "title": fields.get("title", ""), "caption": fields.get("caption", "")}
                        pf.update(imgs)
                        res = run_pipeline(pf, mock=Handler.server_mock,
                                           model=fields.get("model", ""), persist=False)
                        self._send(200, json.dumps({"ok": True, **res}, ensure_ascii=False), _JSON)
                        return
                    f = fields.get("file") or {}
                    if not f.get("bytes"):
                        self._send(400, json.dumps({"ok": False, "error": "이미지 또는 영상 파일이 필요합니다"}, ensure_ascii=False), _JSON)
                        return
                    res = media_native(f["bytes"], f.get("mime") or "video/mp4",
                                       caption=fields.get("caption", ""),
                                       description=fields.get("description", ""),
                                       model=fields.get("model", ""))
                    self._send(200, json.dumps(res, ensure_ascii=False), _JSON)
                else:
                    data = json.loads(body or b"{}")
                    self._send(200, json.dumps(media_action(data), ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/usermeta-profiles"):   # 사용자 메타(프로필) 입력: 폼 단건(JSON)·서식 업로드(multipart)
            try:
                if not self._require_login():
                    return
                from . import personagen as PG
                ctype = self.headers.get("Content-Type", "")
                if "multipart/form-data" in ctype:
                    boundary = ctype.split("boundary=", 1)[1].strip()
                    f = _parse_multipart(body, boundary).get("file")
                    profs = (PG.parse_profiles(f["bytes"], f.get("filename", "profiles.csv"))
                             if isinstance(f, dict) and f.get("bytes") else [])
                else:
                    p = json.loads(body or b"{}")
                    profs = p.get("profiles") or ([p.get("profile")] if p.get("profile") else [])
                self._send(200, json.dumps(usermeta_save_profiles(profs, team=self._req_team()),
                                           ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/usermeta"):
            try:
                if not self._require_login():              # 익명 전 팀 콘텐츠 열람·LLM 호출 차단
                    return
                ctype = self.headers.get("Content-Type", "")
                f = None
                if "multipart/form-data" in ctype:
                    boundary = ctype.split("boundary=", 1)[1].strip()
                    f = _parse_multipart(body, boundary).get("file")
                logs = f["bytes"] if isinstance(f, dict) and f.get("bytes") else None
                name = f.get("filename", "logs.csv") if isinstance(f, dict) else ""
                self._send(200, json.dumps(usermeta_data(logs, name, team=self._req_team()),
                                           ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if not self.path.startswith("/run"):
            self._send(404, "not found")
            return
        # 추출 실행(단건·배치) = 콘텐츠 인입 → 관리자 통제(supabase 모드)
        if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
            msg = ("로그인이 만료됐습니다 · 다시 로그인 후 시도하세요" if not self._bearer_uid()
                   else "콘텐츠 인입은 관리자 전용입니다")
            self._send(403, json.dumps({"error": msg}, ensure_ascii=False), _JSON)
            return
        ctype = self.headers.get("Content-Type", "")
        try:
            if "multipart/form-data" in ctype:
                boundary = ctype.split("boundary=", 1)[1].strip()
                fields = _parse_multipart(body, boundary)
            else:
                fields = json.loads(body or b"{}")
            add_only = str(fields.get("add_only") or "") in ("1", "true")
            if self.path.startswith("/run-batch"):
                f = fields.get("file")
                if not isinstance(f, dict) or not f.get("bytes"):
                    result = {"error": "파일이 없습니다"}
                else:
                    result = run_batch(f["bytes"], f.get("filename", "upload.xlsx"),
                                       purpose=str(fields.get("purpose") or ""), team=self._req_team(),
                                       add_only=add_only)
            elif add_only:                             # STEP 1 = 추가만(모델 미실행)
                result = add_contents([{
                    "displayServiceName": fields.get("displayServiceName", ""),
                    "title": fields.get("title", ""), "subtitle": fields.get("subtitle", ""),
                    "body": fields.get("body", ""),
                    "source_url": fields.get("source_url", ""),
                }], purpose=str(fields.get("purpose") or ""), team=self._req_team())
            else:
                result = run_pipeline(fields, mock=self.server_mock, team=self._req_team())
            self._send(200, json.dumps(result, ensure_ascii=False), _JSON)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)


from .page import PAGE                             # 앱 마크업(라우트 분리 3차)

_PAGE_V = ""


def _page_versioned() -> str:
    """벤더 js/css 링크에 ?v=부팅ID 를 붙인 페이지(1회 생성 캐시).
    새 배포 = 새 URL 이라 브라우저가 구버전 스크립트를 재사용하지 못한다(강력 새로고침 불필요)."""
    global _PAGE_V
    if not _PAGE_V:
        _PAGE_V = re.sub(r"(/vendor/[\w.\-]+\.(?:js|css))", lambda m: m.group(1) + "?v=" + _BOOT_ID, PAGE)
    return _PAGE_V


_MPAGE_V = ""


def _mpage_versioned() -> str:
    """모바일 검수 페이지(/m) · 벤더 캐시버스터는 데스크탑과 동일 규약."""
    global _MPAGE_V
    if not _MPAGE_V:
        from .page_mobile import MOBILE_PAGE
        _MPAGE_V = re.sub(r"(/vendor/[\w.\-]+\.(?:js|css))", lambda m: m.group(1) + "?v=" + _BOOT_ID, MOBILE_PAGE)
    return _MPAGE_V



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
    _jobs_restore()                                    # 실행 이력 복원 · 배포로 끊긴 배치는 중단 표시
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
    finally:
        srv.server_close()                             # 리스닝 소켓 정리(shutdown 은 accept 루프만 멈춘다)


if __name__ == "__main__":
    main()
