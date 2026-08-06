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
import threading
import time
import urllib.error
import urllib.request
import weakref
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# 서버 부팅 ID: 배포(프로세스 교체) 감지 + 벤더 자산 캐시버스터의 단일 원천
_BOOT_ID = "%d-%d" % (int(time.time()), os.getpid())

from . import assets                   # 벤더 조각 → 단일 번들(app-bundle.js/css) 합성
from . import entconf as EC
from . import imagext as IMG
from . import entlabel as ELB          # 엔티티 관련성 라벨(확신도 최적화 정답 수집)
from . import modelmeta as MM         # 모델 표시 정보(이름·제공자·비용 등급) · 선택 드롭다운 원천
from . import pipeline as _PIPE_MOD
PIPE = _PIPE_MOD    # 테스트가 serve.PIPE.extract 를 패치 · 별칭 유지(runops 와 같은 모듈 객체)
from . import prompts as PR
from . import agents as AG
from . import meta_prompts as MP
from . import learnops as LO
from . import adminops as AO
from .config import Config, DEFAULT_CONFIG_PATH

LO._SV = sys.modules[__name__]      # 학습 도메인에 서버 컴포지션 주입(-m 실행의 __main__ 포함)
AO._SV = sys.modules[__name__]      # 관리자·인증 도메인에도 동일 주입

from . import topicops as TPO
from . import mediaops as MO
from . import dictops as DO
from .topicops import (topics_data, topic_studio_action,
                       start_topic_scheduler, topic_drill)
from .mediaops import media_action, media_native
from .dictops import (dict_data, load_dict_overrides, edit_dict,
                      reset_dict_overrides, entdict_data, entdict_action, _ENRICH_STATE)

TPO._SV = sys.modules[__name__]     # 토픽 도메인 주입(라우트 분리 4차)
MO._SV = sys.modules[__name__]      # 미디어 실험실 주입(동일)
DO._SV = sys.modules[__name__]      # 사전 편집 도메인 주입(동일)

from . import reviewops as RV
from . import dashops as DS

RV._SV = sys.modules[__name__]      # 검수 도메인 주입(로드맵 2단계 2차)
DS._SV = sys.modules[__name__]      # 대시보드·롤업 주입(동일)

from . import runops as RN
from . import umops as UMO
from . import memfs as MF
from . import ingestops as IG
from . import boardops as BD
from . import evalops as EVO
from . import deployops as DEP
from . import crewops as CRW           # 검수 인력 운영(HR) · '검수운영' 메뉴
from . import weekops as WKO           # 주간 운영 기록(주 마감 스냅샷 적립·조회)

RN._SV = sys.modules[__name__]      # 실행 파이프라인 주입(로드맵 2단계 3차)
UMO._SV = sys.modules[__name__]     # 사용자 메타 글루 주입(동일)
MF._SV = sys.modules[__name__]      # 파일 기반 메모리(실험실) 주입(동일)
from . import caagent as CA           # 콘텐츠 에이전트(실험실): 자연어 → 위젯 조건
CA._SV = sys.modules[__name__]      # 동일 주입
IG._SV = sys.modules[__name__]      # 인입·잡 주입(동일)
BD._SV = sys.modules[__name__]      # 게시판 주입(동일)
EVO._SV = sys.modules[__name__]     # 평가 런 도메인 주입(Atelier eval_runs 이식)
DEP._SV = sys.modules[__name__]     # 프롬프트 배포 도메인 주입(Atelier deployments 이식)
CRW._SV = sys.modules[__name__]     # 검수 인력 운영(HR) 주입(동일)
ELB._SV = sys.modules[__name__]     # 엔티티 라벨 원장 주입(동일)
WKO._SV = sys.modules[__name__]     # 주간 운영 기록 주입(동일)

_run_id = RN._run_id
_build_id = RN._build_id
_save_drafts = RN._save_drafts
_entdict_after_save = RN._entdict_after_save
store_save = RN.store_save
add_contents = RN.add_contents
run_pipeline = RN.run_pipeline
rerun_all = RN.rerun_all
rerun_content = RN.rerun_content
build_template_csv = RN.build_template_csv
build_template_xlsx = RN.build_template_xlsx
run_batch = RN.run_batch
_logs_rows = UMO._logs_rows
_write_jsonl = UMO._write_jsonl
usermeta_data = UMO.usermeta_data
_usermeta_compute = UMO._usermeta_compute
usermeta_save_profiles = UMO.usermeta_save_profiles
build_usermeta_template_csv = UMO.build_usermeta_template_csv
backfill_urls = IG.backfill_urls
_validate_public_url = IG._validate_public_url
_fetch_records = IG._fetch_records
check_source_url = IG.check_source_url
ingest_run_source = IG.ingest_run_source
_fmt_dur = IG._fmt_dur
_job_begin = IG._job_begin
_job_end = IG._job_end
_jobs_persist = IG._jobs_persist
_jobs_restore = IG._jobs_restore
ingest_status = IG.ingest_status
start_ingest_scheduler = IG.start_ingest_scheduler
_INGEST_STATE = IG._INGEST_STATE            # 같은 dict 객체 공유(테스트 뮤테이션 계약)
_INGEST_LOCK = IG._INGEST_LOCK
board_data = BD.board_data
board_action = BD.board_action
build_results_csv = DS.build_results_csv
build_report_html = DS.build_report_html

# 테스트·learnops(_SV=serve)·핸들러 호환 재수출 · 대입 형태 = pyflakes 오탐 회피
distribute_assignments = RV.distribute_assignments
final_verdicts = RV.final_verdicts
set_final_verdict = RV.set_final_verdict
reviewer_roles = RV.reviewer_roles
set_reviewer_role = RV.set_reviewer_role
is_final_reviewer = RV.is_final_reviewer
final_review_queue = RV.final_review_queue
_final_stats = RV._final_stats
_finals_today = RV._finals_today
_inject_gold_final = RV._inject_gold_final
rerun_unconfirmed = RV.rerun_unconfirmed
_log_assign = RV._log_assign
assign_log_data = RV.assign_log_data
award_quest_bonus = RV.award_quest_bonus
apply_feedback = RV.apply_feedback
apply_gold_answer = RV.apply_gold_answer
mission_progress = RV.mission_progress
_check_missions = RV._check_missions
reviewer_weights = RV.reviewer_weights
_reap_async = RV._reap_async
register_reviewer = RV.register_reviewer
save_badges = RV.save_badges
patch_content_meta = RV.patch_content_meta
arena_data = RV.arena_data
_fb_epoch = RV._fb_epoch
_arena_compute = RV._arena_compute
_row_key = RV._row_key
_lack_classes = RV._lack_classes
raw_rows = RV.raw_rows
raw_detail = RV.raw_detail
model_stats = RV.model_stats
_hist_epoch = RV._hist_epoch
content_history = RV.content_history
drafts_for = RV.drafts_for
review_queue = RV.review_queue
_inject_gold = RV._inject_gold
# 검수 인력 운영(HR) 재수출 · 테스트·핸들러 호환
crew_data = CRW.crew_data
weekly_records = WKO.weekly_records
capture_week = WKO.capture
crew_capacity = CRW.capacity
crew_profiles = CRW.profiles
set_crew_profile = CRW.set_profile
crew_plan_distribute = CRW.plan_distribute
crew_rebalance = CRW.rebalance
crew_auto_tick = CRW.auto_tick
crew_escalate = CRW.escalate_split
crew_needs_confirm = CRW.needs_confirm
crew_confirm_week = CRW.confirm_week
dashboard_data = DS.dashboard_data
_dashboard_compute = DS._dashboard_compute
drill_contents = DS.drill_contents
cost_rollup_data = DS.cost_rollup_data
fail_rollup_data = DS.fail_rollup_data
activity_daily_data = DS.activity_daily_data
_log_cost_rollup = DS._log_cost_rollup
_log_fail_rollup = DS._log_fail_rollup
_log_activity_rollup = DS._log_activity_rollup

# 테스트·외부 호환 재수출(serve.<이름> 계약 유지) · 대입 형태 = pyflakes 미사용 오탐 회피
topic_snapshot = TPO.topic_snapshot
similar_topics = TPO.similar_topics
topic_personas = TPO.topic_personas
_sanitize_def = TPO._sanitize_def
_ent_index = TPO._ent_index
_TOPIC_SNAP_CAP = TPO._TOPIC_SNAP_CAP
media_s5ab = MO.media_s5ab

# 사전 편집 오버라이드 파일 경로 · 테스트가 이 바인딩을 패치하므로 serve 에 유지(dictops 는 _SV 경유)
_DICT_OVERRIDES_PATH = os.path.join(os.path.dirname(DEFAULT_CONFIG_PATH), "dict_overrides.json")
_supa = AO._supa
auth_action = AO.auth_action
validate_jwt = AO.validate_jwt
jwt_email = AO.jwt_email
admin_emails = AO.admin_emails
is_sys_admin_user = AO.is_sys_admin_user
is_super_admin_user = AO.is_super_admin_user
is_admin_user = AO.is_admin_user
menu_allowed = AO.menu_allowed
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
eval_run_start = EVO.eval_run_start
eval_run_resume = EVO.eval_run_resume
eval_run_cancel = EVO.eval_run_cancel
eval_runs_list = EVO.eval_runs_list
eval_run_report = EVO.eval_run_report
rubric_start = EVO.rubric_start
rubric_cancel = EVO.rubric_cancel
eval_run_compare = EVO.eval_run_compare
autopilot_start = EVO.autopilot_start
autopilot_stop = EVO.autopilot_stop
autopilot_status = EVO.autopilot_status
snapshot_prompts = LO.snapshot_prompts
learning_batch = LO.learning_batch
learn_data = LO.learn_data
learn_spec_md = LO.learn_spec_md
learn_export = LO.learn_export
handoff_bundle = LO.handoff_bundle
meta_compile_run = LO.meta_compile_run
builder_compile = LO.builder_compile
builder_test = LO.builder_test
deployment_save = DEP.deployment_save
deployment_remove = DEP.deployment_remove
deployments_list = DEP.deployments_list
deployment_key_new = DEP.deployment_key_new
deployment_key_revoke = DEP.deployment_key_revoke
serve_prompt = DEP.serve_prompt
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


def results_rows(limit: int = 5000, team=None) -> list:
    """집계용 결과 행 · 영속 저장소 우선(누적) · 메모리(_LAST_RESULTS) 폴백은 저장소 부재·오류 시만.
    저장소의 빈 결과는 그대로 신뢰한다 — 전체 삭제 직후 메모리 잔상이 폴백으로 되살아나
    화면에 유령 콘텐츠가 남는 문제 방지.
    원격 스토어(supabase)만 30s 캐시: /raw·/final-queue·/model-stats·/drill 이 요청마다
    팀 콘텐츠 전량(최대 5왕복·수 MB)을 재조회하지 않게. HTTP 쓰기 경로는 전부 _agg_bump 를
    호출하므로 스테일 없음. sqlite(로컬·테스트)는 무캐시 유지 — 테스트가 스토어에 직접 쓰고
    바로 읽는 계약(몽키패치 관례)과 충돌하지 않고, 로컬 조회는 원래 저렴하다.
    호출측 정렬·절단이 캐시를 오염시키지 않게 리스트는 복사해 반환."""
    st = get_store()
    if st:
        try:
            if getattr(st, "REMOTE", False):
                return list(_agg_cached_store(("rows", team, limit), st,
                                              lambda: st.recent(limit, team=team)))
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


def _agg_cached_store(key, st, fn, ttl: float = _AGG_TTL):
    """_agg_cached + 스토어 동일성 검증(약참조). 원본 행처럼 '어느 스토어에서 읽었는지'가
    정합의 전제인 캐시에 쓴다 — 테스트의 _STORE 교체·백엔드 전환 시 즉시 미스가 되어
    이전 스토어의 행이 유령처럼 남지 않는다."""
    now = time.time()
    hit = _AGG_CACHE.get(key)
    if hit and hit[0] > now and hit[1] == _AGG_VERSION and len(hit) == 4 and hit[3]() is st:
        return hit[2]
    val = fn()
    _AGG_CACHE[key] = (now + ttl, _AGG_VERSION, val, weakref.ref(st))
    return val


def _batch_seq_cached(team) -> int:
    """학습 반영 회차(초안 버전 산정용) · 30s 캐시.
    일괄 실행(rerun_all)이 건마다 events 테이블 전체를 재조회하지 않게 한다 — 회차는
    학습 배치 때만 바뀌고 그 쓰기 경로가 _agg_bump 를 호출하므로 스테일 위험 없음."""
    def _get():
        stv = get_store()
        return stv.batch_seq(team) if (stv and hasattr(stv, "batch_seq")) else 0
    return _agg_cached(("batchseq", team), _get)


def feedback_map_cached(team=None) -> dict:
    """검수 피드백 전량 map · 원격 스토어(supabase)만 30s 캐시(원본 행 results_rows 와 대칭).
    /raw·/final-queue·드릴·토픽 드릴이 요청마다 feedback 테이블 전량(1000행 페이지 반복 +
    reviewers_map 왕복)을 재조회하지 않게 한다. 피드백을 바꾸는 쓰기 경로(판정 저장·실행취소·
    전체 삭제·골드 응답·교정·관리자 삭제)는 전부 _agg_bump 를 호출하므로 스테일 없음.
    sqlite(로컬·테스트)는 무캐시 — 스토어에 직접 쓰고 바로 읽는 테스트 계약 유지.
    반환 dict 는 캐시 공유본이므로 수정 금지(읽기 전용)."""
    st = get_store()
    if not (st and hasattr(st, "feedback_map")):
        return {}
    if getattr(st, "REMOTE", False):
        return _agg_cached_store(("fmap", team), st, lambda: st.feedback_map(team=team))
    return st.feedback_map(team=team)


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


# ── 학습 지시 무효화(개별 끄기) ─────────────────────────────────────────────────────
def disabled_directives(team=None) -> set:
    """관리자가 끈 학습 지시 원문 집합(전역 · reports kind='disabled_directives').
    다음 학습 반영(컴파일)부터 제외 · 원본 라우트·메모 행은 보존(감사 가능)."""
    rep = _report_get("disabled_directives", None, {}) or {}
    return {str((i or {}).get("text") or "").strip()
            for i in (rep.get("items") or []) if (i or {}).get("text")}


def set_directive_disabled(text: str, disabled: bool) -> dict:
    """지시 1건 끄기/켜기 · 텍스트 정확 일치 키(상한 200건)."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "지시 원문이 비어 있습니다"}
    rep = _report_get("disabled_directives", None, {}) or {}
    items = [i for i in (rep.get("items") or [])
             if (i or {}).get("text") and i["text"].strip() != text]
    if disabled:
        items.append({"text": text, "ts": time.time()})
    _report_save("disabled_directives", {"items": items[-200:]}, None)
    return {"ok": True, "disabled": bool(disabled), "disabled_n": len(items)}


def routes_overview(team=None) -> dict:
    """지시 원본 목록(공통+모델 귀속) + 끔 상태 · '지시 원본 관리' 뷰의 원천."""
    st = get_store()
    if not st:
        return {"ok": False, "items": []}
    dis = disabled_directives()
    items = []
    for stage, lst in (st.routes_by_stage(50, team=team) or {}).items():
        for t in lst:
            items.append({"stage": stage, "model": "", "text": t, "disabled": t in dis})
    for m, stages in (st.routes_by_stage_model(50, team=team) or {}).items():
        for stage, lst in (stages or {}).items():
            for t in lst:
                items.append({"stage": stage, "model": m, "text": t, "disabled": t in dis})
    listed = {i["text"] for i in items}
    for t in sorted(dis):                          # 원본이 더 안 보여도 끔 목록은 관리 가능하게
        if t not in listed:
            items.append({"stage": "", "model": "", "text": t, "disabled": True})
    return {"ok": True, "items": items, "disabled_n": len(dis)}


def quest_active() -> bool:
    """검수 목표(퀘스트) 진행 중 여부: 반영 일시가 미래로 설정돼 있으면 참.
    진행 중에는 검수 대상 초안 교체(재실행)를 물리적으로 차단한다(합의 오염 방지)."""
    try:
        cfg = Config.load()
        return LO.next_batch_time(getattr(cfg, "learn_next_at", "")) > time.time()
    except Exception:
        return False


def _pipeline_empty(out: dict) -> bool:
    """추출 산출이 전량 빈값인지(폴백 체인 트리거): 아이템 메타도 판정도 없다."""
    im = (out or {}).get("item_meta") or {}
    qm = (out or {}).get("quality_meta") or {}
    return not (im.get("summary") or im.get("entities") or im.get("content_category")
                or qm.get("finalGrade"))


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


# 메뉴별 권한(생성자 설정) 백엔드 강제: POST 액션(쓰기) 경로 → 메뉴 id.
# 인프라(/config·/ingest-status)·멤버(/feedback·/board)·상태폴 경로는 미포함(가시성은 프론트 담당).
_MENU_POST_ROUTES = (
    ("/topic-studio", "studio"), ("/prompt", "studio"), ("/meta-compile", "studio"),
    ("/builder", "studio"), ("/deployment", "studio"),
    ("/media-extract", "lab"), ("/usermeta", "lab"),
    ("/dict", "dict"),
    ("/golden", "testset"), ("/learn", "testset"), ("/compare-models", "testset"),
    ("/ingest-run", "content"), ("/rerun", "content"), ("/run", "content"), ("/store", "content"),
    ("/crew", "crew"),
)


# 접두 매칭의 예외: 메뉴 권한과 무관한 '본인 것' 액션. /crew-confirm 은 전 검수자가
# 자기 일정을 확인하는 경로인데 접두가 /crew 라 검수운영(슈퍼관리자 전용) 메뉴 권한에
# 걸려 일반 검수자가 확인 자체를 못 했다(2026-07-28 실사용 신고).
_MENU_POST_EXEMPT = ("/crew-confirm",)


def _menu_for_path(path: str):
    """POST 경로 → 관리자 메뉴 id(없으면 None). 메뉴별 권한 백엔드 강제용."""
    p = (path or "").split("?", 1)[0]
    for prefix in _MENU_POST_EXEMPT:                 # 예외를 먼저 본다(접두가 더 길다)
        if p == prefix or p.startswith(prefix):
            return None
    for prefix, menu in _MENU_POST_ROUTES:
        if p == prefix or p.startswith(prefix):
            return menu
    return None


def vocab() -> dict:
    """드롭다운용 어휘(콘텐츠 그룹 등). dictionaries/profiles 와 동기화."""
    from . import dictionaries as D
    return {"groups": list(D.SERVICE_GROUP.keys())}


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
        "images": ref.get("image_urls", []) or r.get("images", []) or [],
        "body": ref.get("body", ""),
        "summary": im.get("summary", ""),
        "entities": im.get("entities", []) or [],
        # 읽기 시점 확신도 병행 노출(entconf.py) · entities 키는 계약 유지(하위 호환)
        "entities_scored": EC.scored_entities(im, ref),
        "intent": im.get("intent", []) or [],
        "category": im.get("content_category", []) or [],
        "grade": qm.get("finalGrade", "") or r.get("grade", ""),
        "reasons": qm.get("reasons", []) or [],
        "source_status": ref.get("source_status") or {},   # 원문 소실 신고 플래그(게시판 #10)
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


def _attach_fb(items, team=None, reviewer: str = "", fmap=None):
    """상세행 리스트에 검수 피드백 상태(fb: verdict·ts) 부착 → 콘텐츠 목록 어디서나 '검수 완료' 표기.
    reviewer 를 주면 '완료' 판정이 내 표(mine) 기준으로 동작한다(드릴 경로 정합 · 2026-07-10).
    fmap 을 주면(호출측이 이미 조회) feedback 전량 재조회를 생략한다.
    미지정 시 feedback_map_cached(원격 30s 캐시) — 드릴·토픽 드릴이 요청마다 전량 재조회하지 않게."""
    st = get_store()
    if not (st and hasattr(st, "feedback_map")):
        return items
    if fmap is None:
        try:
            fmap = feedback_map_cached(team)
        except Exception:
            return items
    for it in items:
        it["fb"] = _fb_public(fmap.get(it.get("hash"), {}) or {}, reviewer)
    return items


def _key_dir():
    """키 파일 저장 디렉토리. 운영 컨테이너는 PRISM_CONFIG(/data/config.json)가 가리키는
    볼륨 디렉토리에 저장해야 재배포에도 남는다 — 홈(~)은 루트FS 라 머신 재생성마다 초기화되어
    '키 저장했는데 다음 접속에 없음' 사고의 원인이었다. PRISM_CONFIG 미설정(로컬)은 기존대로 홈."""
    cfg = os.environ.get("PRISM_CONFIG")
    if cfg:
        d = os.path.dirname(cfg)
        if d and os.path.isdir(d):
            return d
    return os.path.expanduser("~")


_KEY_PATH = os.path.join(_key_dir(), ".prism_key")          # Upstage Solar
# 라우터별 키 저장 경로(BizRouter · Timely). env 는 imagext.ROUTERS[*]['key_env'].
_ROUTER_KEY_PATHS = {
    "bizrouter": os.path.join(_key_dir(), ".prism_bizrouter_key"),
    "timely": os.path.join(_key_dir(), ".prism_timely_key"),
}


def _legacy_key_path(path):
    """볼륨 경로 도입 전 저장 위치(홈 고정). 이전 저장분 읽기·삭제 호환용."""
    return os.path.expanduser("~/" + os.path.basename(path))


def _read_key_file(path):
    """저장된 키 읽기: 현행 경로 → 과거(홈) 경로 순. 없으면 빈 문자열."""
    for p in dict.fromkeys((path, _legacy_key_path(path))):
        if os.path.exists(p):
            try:
                k = open(p, encoding="utf-8").read().strip()
                if k:
                    return k
            except Exception:
                pass
    return ""


def _key_persisted(path):
    return os.path.exists(path) or os.path.exists(_legacy_key_path(path))


def _remove_key_file(path):
    for p in dict.fromkeys((path, _legacy_key_path(path))):
        try:
            os.remove(p)
        except OSError:
            pass


def _write_private(path, text):
    """비밀 파일(키)을 0600 으로 원자적 기록. O_CREAT mode 로 신규는 처음부터 0600,
    기존 파일은 write 전에 fchmod 로 강제 → open→write→chmod 사이 0644 노출 창 제거."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(text)


def load_persisted_key():
    """저장된 키가 있고 환경변수가 비어 있으면 프로세스 환경에 주입(서버 시작 시)."""
    if not IMG._api_key():
        k = _read_key_file(_KEY_PATH)
        if k:
            os.environ["UPSTAGE_API_KEY"] = k
    if IMG._api_key():
        _seed_solar_defaults()                        # 키 보유 + 엔드포인트·모델 미설정 자기 치유
    for service, path in _ROUTER_KEY_PATHS.items():
        env = IMG.ROUTERS[service]["key_env"]
        if not IMG.router_key(service):
            k = _read_key_file(path)
            if k:
                os.environ[env] = k


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
    if len(_TEAM_CACHE) > 512:                        # 만료 항목 정리(장기 가동 시 무한 성장 방지 · 스냅샷 순회로 크기변경 안전)
        for k, v in list(_TEAM_CACHE.items()):
            if v[1] <= now:
                _TEAM_CACHE.pop(k, None)
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


_RL_HITS = {}
_RL_LOCK = threading.Lock()


def _client_ip(h) -> str:
    """레이트리밋 키용 클라이언트 IP. Fly 프록시 뒤(FLY_APP_NAME 환경 신호)에서는 client_address 가
    소수의 프록시 주소라 per-IP 제한이 사실상 전역 공유 버킷이 된다(다른 사용자의 요청으로 함께
    429 · 분당 상한 소진 시 전 사용자 차단) — 프록시가 채워주는 Fly-Client-IP, 없으면
    X-Forwarded-For 첫 항목을 사용. 프록시 뒤가 아니면 두 헤더는 클라이언트가 위조할 수 있어
    기존 client_address 를 유지한다."""
    if os.environ.get("FLY_APP_NAME"):
        ip = (h.headers.get("Fly-Client-IP") or "").strip()
        if not ip:
            ip = (h.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if ip:
            return ip
    return h.client_address[0] if h.client_address else "?"


def rate_limited(key: str, min_interval: float = 0.8, per_min: int = 40) -> bool:
    """key(검수자/IP)별 최소 간격·분당 상한. 초과 시 True(=429)."""
    now = time.time()
    with _RL_LOCK:
        if len(_RL_HITS) > 512:                       # 만료 키 일괄 정리(장기 가동 시 무한 성장 방지)
            for k in [k for k, v in _RL_HITS.items() if not v or now - v[-1] > 60]:
                _RL_HITS.pop(k, None)
        q = _RL_HITS.setdefault(key, [])
        while q and now - q[0] > 60:
            q.pop(0)
        if (q and (now - q[-1]) < min_interval) or len(q) >= per_min:
            return True
        q.append(now)
        return False


# ── 실시간 협업(SSE): 검수 이벤트를 접속 중인 '같은 팀' 팀원에게만 브로드캐스트 ──
_subscribers = []                      # list[(team, queue.Queue)]
_sub_lock = threading.Lock()


def _sse_subscribe(team=None):
    q = _queue.Queue(maxsize=128)
    with _sub_lock:
        _subscribers.append((team, q))
    return q


def _sse_unsubscribe(q):
    with _sub_lock:
        _subscribers[:] = [(t, sq) for (t, sq) in _subscribers if sq is not q]


def broadcast(event: dict, team=None):
    """SSE 이벤트 푸시(논블로킹, 큐 가득 차면 드롭). team 이 지정되면 같은 팀 구독자에게만
    전달해 교차팀 실시간 유출(hash·title·검수자·verdict)을 차단. team=None(로컬 sqlite 단일 팀
    또는 팀 무관 이벤트)이면 모든 구독자."""
    with _sub_lock:
        subs = list(_subscribers)
    for (sub_team, q) in subs:
        if team is not None and sub_team != team:
            continue
        try:
            q.put_nowait(event)
        except _queue.Full:
            pass


def _candidate_models(cfg, team=None) -> list:
    """프롬프트 스튜디오·모델 적용 선택지: 실제 초안 만든 모델(target_models) + 설정 모델 +
    저장된 모델 키 + 흔한 기본값(오프라인 대비)."""
    tm = []
    try:                                          # 실제 실행 이력의 모델 우선(설정에 없어도 노출)
        st = get_store()
        if st and hasattr(st, "target_models"):
            tm = st.target_models(team) or []
    except Exception:
        tm = []
    seen, out = set(), []
    pool = [*tm, cfg.text_model, cfg.vision_model,
            *list((cfg.stage_models or {}).values()),
            *list((cfg.model_prompts or {}).keys())]
    for m in pool + ["solar-pro2", "gpt-5.4", "claude-opus-4-8", "gemini-2.5-pro"]:
        m = (m or "").strip()
        if m and m not in seen:
            seen.add(m); out.append(m)
    return out


def _vision_candidates(cfg) -> list:
    """이미지 탭 시각 슬롯 선택지: 기본(Upstage IE) + 연결된 라우터의 검증 후보 + 현재 설정 슬롯.
    id 형식 = 'upstage_ie' | 'provider:model'. 라우터 후보는 키가 있을 때만 노출한다."""
    out = [{"id": "upstage_ie", "label": "기본 · 문서 시각 이해(Upstage)"}]
    seen = {"upstage_ie"}
    # 리포트(2026-07-22 · 샘플 20장) 검증 후보 — Timely 라우터. 키 연결 시에만.
    if IMG.router_key("timely"):
        for m, tag in (("gemini-3.5-flash", " · 권장"), ("gpt-5.4-mini", " · 빠름"),
                       ("claude-haiku-4-5", "")):
            cid = f"timely:{m}"
            if cid not in seen:
                seen.add(cid); out.append({"id": cid, "label": f"{m} (Timely){tag}"})
    # 관리자가 설정한 슬롯이 라우터면 포함(후보에 없던 조합도 선택 가능하게)
    if IMG.is_router(cfg.vision_provider or "") and (cfg.vision_model or ""):
        cid = f"{cfg.vision_provider}:{cfg.vision_model}"
        if cid not in seen:
            seen.add(cid)
            out.append({"id": cid, "label": f"{cfg.vision_model} ({cfg.vision_provider}) · 설정"})
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


_AUTH_MASK = "***"                                 # 인입 소스 auth(외부 API 비밀 토큰) 마스크 센티널


def _source_key(s):
    return s.get("id") or ("ep:" + (s.get("endpoint") or "")) if isinstance(s, dict) else None


def _mask_ingest_sources(sources):
    """인입 소스의 auth(비밀 토큰)를 마스킹해 응답에 실값이 노출되지 않게 한다."""
    out = []
    for s in sources:
        if isinstance(s, dict) and s.get("auth"):
            out.append({**s, "auth": _AUTH_MASK})
        else:
            out.append(s)
    return out


def _unmask_ingest_sources(new, old):
    """저장 시 auth 가 마스크 센티널이면 기존 저장값을 복원(마스킹된 응답 재저장이 실값을 덮어쓰지 않게).
    매칭 실패한 센티널은 빈 값으로(리터럴 '***' 를 자격증명으로 저장하지 않음)."""
    prev = {_source_key(s): s.get("auth") for s in (old or []) if isinstance(s, dict)}
    out = []
    for s in new:
        if isinstance(s, dict) and s.get("auth") == _AUTH_MASK:
            out.append({**s, "auth": prev.get(_source_key(s), "")})
        else:
            out.append(s)
    return out


_MMETA_CACHE = {}                      # team -> (meta, expiry) · /config 는 자주 불리고 등급은 천천히 변한다
_MMETA_TTL = 300


def _model_meta(cfg, team=None) -> dict:
    """모델 선택 드롭다운 표시 정보(이름·제공자·비용 등급). 비용 원장이 원천이라
    조회 실패는 표시 문제일 뿐이므로 조용히 빈 값으로 떨어뜨린다(설정 화면은 계속 뜬다).

    비용 원장 조회는 supabase 왕복 1회라 /config 마다 하면 낭비 — 팀별 5분 캐시.
    등급은 누적 평균이라 몇 분 늦게 반영돼도 문제가 없다."""
    now = time.time()
    hit = _MMETA_CACHE.get(team)
    if hit and hit[1] > now:
        return hit[0]
    try:
        cost = _report_get("cost_rollup", team, {}) or {}
    except Exception:
        cost = {}
    try:
        pool = list(_candidate_models(cfg, team)) + list(MM.KNOWN_ROUTER_MODELS or [])
        meta = MM.model_meta(cost, pool)                  # 안 돌린 라우터 모델도 이름은 필요
    except Exception:
        return {}
    if len(_MMETA_CACHE) > 64:                            # 만료 항목 정리(장기 가동 시 성장 억제)
        for k, v in list(_MMETA_CACHE.items()):
            if v[1] <= now:
                _MMETA_CACHE.pop(k, None)
    _MMETA_CACHE[team] = (meta, now + _MMETA_TTL)
    return meta


def config_status(team=None) -> dict:
    cfg = Config.load()
    base = (cfg.chat_url or "").rsplit("/chat/completions", 1)[0]
    return {
        "modelMeta": _model_meta(cfg, team),
        "hasKey": bool(IMG._api_key()),
        "persisted": _key_persisted(_KEY_PATH),
        "bootId": _BOOT_ID,
        "model": cfg.model or "",
        "baseUrl": base,
        "reasoning": cfg.reasoning_effort or "default",
        "systemPrompt": cfg.system_prompt or "",
        "stagePrompts": dict(cfg.stage_prompts or {}),
        "stagePromptsMeta": dict(cfg.stage_prompts_meta or {}),
        "stageModels": dict(cfg.stage_models or {}),
        "modelPrompts": dict(cfg.model_prompts or {}),
        "availableModels": _candidate_models(cfg, team),
        "visionCandidates": _vision_candidates(cfg),
        "goldenMinGood": int(getattr(cfg, "golden_min_good", 1) or 1),
        "learnNextAt": str(getattr(cfg, "learn_next_at", "") or ""),
        "learnRepeatDays": int(getattr(cfg, "learn_repeat_days", 0) or 0),
        "fallbackModels": list(getattr(cfg, "fallback_models", None) or []),
        "batchBudgetUsd": float(getattr(cfg, "batch_budget_usd", 0.0) or 0.0),
        "finalRerunAfterBatch": bool(getattr(cfg, "final_rerun_after_batch", True)),
        "finalGoldCheck": bool(getattr(cfg, "final_gold_check", True)),
        "metaFourCalls": bool(getattr(cfg, "meta_four_calls", True)),
        "metaCallModels": dict(getattr(cfg, "meta_call_models", {}) or {}),
        "familyWrappers": dict(getattr(cfg, "family_wrappers", {}) or {}),
        "familyWrapperDefaults": dict(MP.FAMILY_WRAPPER_DEFAULT),
        "metaCalls": list(MP.CALLS),
        "metaContract": {"rules": dict(MP.CALL_RULES), "examples": MP.gold_examples(None)},
        "ingestSources": _mask_ingest_sources(cfg.ingest_sources or []),
        "guideUrls": team_links(),
        "storedCount": (get_store().count() if get_store() else 0),
        "build": _build_id(),
        "configured": cfg.is_configured(),
        "forcedMock": Handler.server_mock,
        "backend": "supabase" if _supa() else "sqlite",
        "authRequired": bool(_supa()),                 # supabase 모드 → ID/PW 로그인 필요
        "keyManagedByServer": bool(_supa()),           # 운영: 키는 서버 관리(UI 키 입력 숨김)
        # 큐 실행 여부(공개 · 무인증): 배포 워크플로가 이걸 보고 배치가 끝날 때까지 배포를 대기한다
        # (기존 /ingest-status 는 supabase 모드에서 401 이라 워크플로의 큐 보호가 무력화됐음).
        "ingesting": bool(ingest_status().get("running") or _ENRICH_STATE.get("running")),
        # 모델 슬롯
        "hasBizKey": bool(IMG.router_key("bizrouter")),
        "bizPersisted": _key_persisted(_ROUTER_KEY_PATHS["bizrouter"]),
        "hasTimelyKey": bool(IMG.router_key("timely")),
        "timelyPersisted": _key_persisted(_ROUTER_KEY_PATHS["timely"]),
        "textProvider": cfg.text_provider or "solar",
        "textModel": cfg.text_model or "",
        "visionProvider": cfg.vision_provider or "upstage_ie",
        "visionModel": cfg.vision_model or "",
        "legalEnabled": bool(cfg.legal_enabled),
    }


def apply_config(data: dict, allow_key: bool = False, team=None) -> dict:
    """키/모델/엔드포인트/추론강도/추가지시 적용. 키만 프로세스 환경(+옵션 _KEY_PATH 파일 · 볼륨 우선).
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
                _write_private(_KEY_PATH, key)        # 0600 원자적(생성~chmod 사이 0644 창 제거)
            except Exception:
                pass
    elif data.get("forget"):                      # 저장된 키 삭제
        os.environ.pop("UPSTAGE_API_KEY", None)
        _remove_key_file(_KEY_PATH)
    # 라우터 키(BizRouter · Timely, 서비스별 별도 저장)
    for service, path in _ROUTER_KEY_PATHS.items():
        env = IMG.ROUTERS[service]["key_env"]
        rkey = (data.get(service + "_api_key") or "").strip()
        if rkey:
            os.environ[env] = rkey
            if data.get("persist"):
                try:
                    _write_private(path, rkey)        # 0600 원자적
                except Exception:
                    pass
        elif data.get("forget_" + service):
            os.environ.pop(env, None)
            _remove_key_file(path)
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
    has_misc = (("golden_min_good" in data) or ("learn_next_at" in data) or ("learn_repeat_days" in data)
                or ("fallback_models" in data) or ("batch_budget_usd" in data)
                or ("final_rerun_after_batch" in data) or ("final_gold_check" in data))
    if (model or base or reasoning or has_sp or has_stage or has_slot or has_legal or has_ingest
            or has_smodels or has_mprompts or has_wrappers or has_callm or has_4c or has_misc):
        cfg = Config.load()
        if has_ingest:
            # 마스킹된 auth 센티널은 기존 실값으로 복원(마스킹 응답 재저장이 시크릿을 덮어쓰지 않게).
            # RHS cfg.ingest_sources 는 할당 전 평가라 기존값(방금 Config.load 로 로드됨).
            cfg.ingest_sources = _unmask_ingest_sources(data.get("ingest_sources") or [],
                                                        cfg.ingest_sources or [])
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
        if "learn_repeat_days" in data:           # 퀘스트 반복 주기(일) · 0=반복 없음 · 상한 31일
            try:
                cfg.learn_repeat_days = max(0, min(31, int(data.get("learn_repeat_days") or 0)))
            except (TypeError, ValueError):
                pass
        if "fallback_models" in data:             # 폴백 체인(빈 산출 시 예비 모델 · 최대 3)
            fl = data.get("fallback_models")
            if isinstance(fl, list):
                cfg.fallback_models = [str(m).strip() for m in fl if str(m).strip()][:3]
        if "batch_budget_usd" in data:            # 일괄 실행 비용 상한($ · 0=무제한 · 상한 1000)
            try:
                cfg.batch_budget_usd = max(0.0, min(1000.0, float(data.get("batch_budget_usd") or 0)))
            except (TypeError, ValueError):
                pass
        if "final_rerun_after_batch" in data:     # 학습 반영 후 미확정분 새 버전 자동 재실행(2층 검수 3-1)
            cfg.final_rerun_after_batch = bool(data.get("final_rerun_after_batch"))
        if "final_gold_check" in data:            # 최종검수 골드 캘리브레이션 출제 켬/끔
            cfg.final_gold_check = bool(data.get("final_gold_check"))
        if "learn_next_at" in data:               # 검수 목표(퀘스트) 일시 · 빈 값 = 목표 해제(삭제)
            v = str(data.get("learn_next_at") or "").strip()[:16]
            if not v:
                cfg.learn_next_at = ""
                cfg.learn_team = ""                # 퀘스트 해제 시 팀 태그도 비움
                cfg.learn_repeat_days = 0          # 반복 시리즈도 함께 종료
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
    return config_status(team)


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
# POST 본문 상한(메모리 DoS 방어) · 미디어/엑셀 업로드 여유. 환경변수로 조정 가능.
try:
    _MAX_BODY = int(os.environ.get("PRISM_MAX_BODY_MB", "32")) * 1024 * 1024
except ValueError:
    _MAX_BODY = 32 * 1024 * 1024

_FETCH_MAX = 16 * 1024 * 1024           # 인입 아웃바운드 응답 크기 상한(메모리 소진 방어)

_PUBLIC_GET = {"/", "/m", "/config", "/favicon.ico", "/template.xlsx", "/template.csv",
               "/usermeta-template.csv", "/usermeta-profile-template.csv",
               "/api/v1/prompt"}   # 배포 프롬프트 서빙(자체 Bearer 키 검증 · deployops)

# 팀 없이도 접근 가능한 인증 GET(전역 참조·관리자 판정 · 팀 콘텐츠 데이터 아님).
# 그 외 데이터 GET 은 supabase 모드에서 팀 소속을 요구(team=None 전 팀 폴백 격리 붕괴 차단).
_TEAMLESS_OK_GET = {"/admin", "/models", "/vocab", "/dict", "/ingest-status"}


def is_public_get(path: str) -> bool:
    """무인증 허용 GET 경로 판정(쿼리 무시 · 말미 슬래시 정규화)."""
    p = (path or "").split("?", 1)[0]
    if p.startswith("/vendor/"):
        return True
    return (p.rstrip("/") or "/") in _PUBLIC_GET


# ═══ GET 라우트 테이블 ════════════════════════════════════════════════════
# 등록: @_get_route("/prefix") · 관리자 전용은 admin=True(공통 403 게이트 _admin_gate).
# 디스패치(do_GET)는 최장 접두 우선이라 나열 순서와 무관 — 짧은 라우트가 긴 라우트를
# 가로채던 계열의 운영 사고(2026-07-16 /reviewer vs /reviewer-role)가 구조적으로 불가능.
# 핸들러 계약: fn(h, q) → dict 반환 = 200 JSON 응답 · None 반환 = 핸들러가 직접 응답을 씀.
# (h = Handler 인스턴스 · q = parse_qs 쿼리 dict) · 가드: tests/test_route_dispatch.py
_GET_ROUTES = {}


def _get_route(prefix: str, admin: bool = False):
    def deco(fn):
        _GET_ROUTES[prefix] = (fn, admin)
        return fn
    return deco


@_get_route("/report")
def _g_report(h, q):
    h._send(200, build_report_html(team=h._req_team()))


@_get_route("/export.csv")
def _g_export_csv(h, q):
    h._send_file(build_results_csv(team=h._req_team()), "text/csv; charset=utf-8",
                 "prism_results.csv")


@_get_route("/config")
def _g_config(h, q):
    # 운영(supabase) 무인증: 프롬프트 계약·모델 슬롯·팀 가이드 URL 은 로그인 후에만.
    # 로그인 화면·배포 검증(curl /config: backend·configured)·15초 헬스체크가 쓰는 최소 필드만
    # 공개 — config_status 전체 계산(모델 목록·저장 건수·팀 링크 = 원격 왕복 약 3회)을 생략하고
    # 로컬 값만으로 즉시 응답한다(헬스체크가 supabase 지연에 물려 timeout 나는 경로 차단).
    if _supa() and not h._bearer_uid():
        return {"bootId": _BOOT_ID, "build": _build_id(),
                "configured": Config.load().is_configured(),
                "forcedMock": Handler.server_mock,
                "ingesting": bool(ingest_status().get("running") or _ENRICH_STATE.get("running")),
                "backend": "supabase", "authRequired": True, "keyManagedByServer": True}
    return config_status(h._req_team())


@_get_route("/models")
def _g_models(h, q):
    return list_models()


@_get_route("/vocab")
def _g_vocab(h, q):
    return vocab()


@_get_route("/entdict-lookup")                       # 검수 화면: 콘텐츠 엔티티 → 사전 정보(타입·속성)
def _g_entdict_lookup(h, q):
    names = [n for n in (q.get("names", [""])[0]).split("|") if n.strip()]
    st = get_store()
    found = st.ent_by_names(names) if (st and hasattr(st, "ent_by_names")) else {}
    return {"ok": True, "entities": found}


@_get_route("/entdict")
def _g_entdict(h, q):
    return entdict_data(q=q.get("q", [""])[0], type_=q.get("type", [""])[0],
                        status=q.get("status", [""])[0], limit=int(q.get("limit", ["300"])[0]))


@_get_route("/dict")
def _g_dict(h, q):
    return dict_data()


@_get_route("/topic-drill")
def _g_topic_drill(h, q):
    return topic_drill(q.get("cluster", [""])[0], h._req_team(),
                       reviewer=(h._bearer_uid() or q.get("reviewer", [""])[0]))


@_get_route("/topics")
def _g_topics(h, q):
    td = dict(topics_data())
    try:                                             # 자동 스냅샷 메타(마지막 시각·변화) 동반
        snap = _report_get("topic_snapshots", None, {}) or {}
        td["snapshot"] = {"last_ts": ((snap.get("entries") or [{}])[-1] or {}).get("ts"),
                          "delta": snap.get("last_delta")}
    except Exception:
        td["snapshot"] = None
    return td


@_get_route("/dashboard")
def _g_dashboard(h, q):
    return dashboard_data(h._req_team())


@_get_route("/drill")
def _g_drill(h, q):
    return drill_contents(q.get("kind", [""])[0], q.get("value", [""])[0], h._req_team(),
                          reviewer=(h._bearer_uid() or q.get("reviewer", [""])[0]))


@_get_route("/arena")
def _g_arena(h, q):
    d = dict(arena_data(h._req_team()))
    rv = h._bearer_uid() or q.get("reviewer", [""])[0]
    if rv:
        d["missions"] = mission_progress(rv, h._req_team())
    d["my_id"] = rv or ""                            # 내 행 식별 = reviewer_id(닉네임 변경·중복 표시명 무관)
    d["final_reviewers"] = sorted(reviewer_roles(h._req_team()))   # 2층 검수: 역할 노출(탭 게이팅)
    return d


@_get_route("/final-queue")                          # 최종검수 큐(미확정분 · 최종검수자/관리자)
def _g_final_queue(h, q):
    uid = h._bearer_uid()
    if _supa() and not (is_admin_user(uid, h._req_team(), h._bearer_email())
                        or is_final_reviewer(uid, h._req_team())):
        h._send(403, json.dumps({"error": "최종검수자 전용입니다"}, ensure_ascii=False), _JSON)
        return None
    return final_review_queue(h._req_team(), reviewer=uid or "")


@_get_route("/admin")
def _g_admin(h, q):
    # 메뉴 게이팅의 원천: 인증 서버 일시 장애는 '비관리자(200)'가 아니라 503(재시도)으로 구분
    auth = h.headers.get("Authorization", "")
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    try:
        uid = AO.validate_jwt(token, strict=True)
    except AO.AuthBackendUnavailable:
        h._send(503, json.dumps({"ok": False, "error": "인증 서버 연결 지연 · 자동 재시도됩니다"},
                                ensure_ascii=False), _JSON)
        return None
    if token and _supa() and not uid:
        # 토큰이 있는데 무효 = 만료(1시간) · '비관리자(200)'로 뭉개면 관리자 메뉴가 조용히 강등된다
        h._send(401, json.dumps({"ok": False, "error": "로그인이 만료됐습니다 · 세션 갱신 필요"},
                                ensure_ascii=False), _JSON)
        return None
    return admin_data(uid, h._req_team(), h._bearer_email())


@_get_route("/queue")
def _g_queue(h, q):
    uid = h._bearer_uid()
    # 생성자(팀 생성자·슈퍼관리자)는 배정 배타 규칙을 우회해 전체 큐를 본다(/history 열람 권한과 동일 기준).
    see_all = is_super_admin_user(uid, h._req_team(), h._bearer_email())
    return review_queue({"only_unreviewed": q.get("all", ["0"])[0] not in ("1", "true"),
                         "limit": (q.get("limit", ["100"])[0]), "team": h._req_team(),
                         "reviewer": uid or q.get("reviewer", [""])[0],
                         "see_all": see_all})


@_get_route("/raw")                                  # 검수 대상 콘텐츠(모델·버전 필터 표 · 슬림 응답)
def _g_raw(h, q):
    return raw_rows(int(q.get("limit", ["100"])[0]), h._req_team(),
                    reviewer=(h._bearer_uid() or q.get("reviewer", [""])[0]))


@_get_route("/raw-detail")                           # 검수 표 상세(해시 단건 · 본문·메타 원본·확신도)
def _g_raw_detail(h, q):
    return raw_detail((q.get("hash", [""])[0] or "").strip(), h._req_team())


@_get_route("/model-stats")                          # 결과 비교: 요소 단위 모델별 현황
def _g_model_stats(h, q):
    return model_stats(h._req_team())


@_get_route("/history")                              # 검수 상세: 콘텐츠 작업 이력(판정·교정·재실행)
def _g_history(h, q):
    # 운영(supabase): 팀 생성자·슈퍼관리자 전용(누가 언제 판정했는지 = 민감 정보) · 로컬 단독 실행은 그대로
    if _supa() and not is_super_admin_user(h._bearer_uid(), h._req_team(), h._bearer_email()):
        h._send(403, json.dumps({"error": "팀 생성자·슈퍼관리자 전용입니다"}, ensure_ascii=False), _JSON)
        return None
    return content_history(q.get("hash", [""])[0], h._req_team())


@_get_route("/drafts")                               # 결과 비교: 콘텐츠별 초안 스냅샷
def _g_drafts(h, q):
    return drafts_for(q.get("hash", [""])[0], h._req_team())


@_get_route("/events")
def _g_events(h, q):
    # EventSource 는 Authorization 헤더를 못 싣어 token 쿼리로 인증한다. 구독 팀도 이 토큰으로 해석한다
    # (_req_team 은 헤더 전용이라 SSE 에선 항상 None → 운영에서 팀 스코프가 무력화되던 회귀 수정).
    team = team_of(validate_jwt((q.get("token") or [""])[0])) if _supa() else None
    h._serve_sse(team)


@_get_route("/ingest-status")
def _g_ingest_status(h, q):
    return ingest_status()


@_get_route("/prompt-preview", admin=True)           # 프롬프트 스튜디오: 콜별×모델별 최종 합성 프롬프트(관리자)
def _g_prompt_preview(h, q):
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
        return {"ok": True, "family": MP.family_of(model), "call": call,
                "system": sysp, "user": userp}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


@_get_route("/prompt-snapshot", admin=True)          # 버전별 프롬프트 스냅샷(v 미지정 = 최신 · 관리자)
def _g_prompt_snapshot(h, q):
    v = (q.get("v") or [""])[0].strip()
    kind = f"prompt_snapshot_v{int(v)}" if v.isdigit() else "prompt_snapshot_latest"
    snap = _report_get(kind, h._req_team())
    return {"ok": bool(snap), "snapshot": snap}


@_get_route("/learn-report")                         # 최근 배치 결과(GET) · ?v=N 이면 그 버전 리포트
def _g_learn_report(h, q):
    _rv = (q.get("v") or [""])[0].strip()
    if _rv.isdigit():                                # 버전 히스토리 상세(구버전은 미영속 → null)
        _vrep = _report_get(f"learn_report_v{int(_rv)}", h._req_team())
        return {"ok": bool(_vrep), "report": _vrep, "version": int(_rv)}
    rep = _report_get("learn_report", h._req_team(), LO._LAST_LEARN_REPORT)
    _c = Config.load()                               # 다음 반영 예정(검수 목표 일시 · 화면 표시용)
    nb = LO.next_batch_time(getattr(_c, "learn_next_at", ""))
    return {"ok": True, "report": rep, "next_batch_at": nb}


@_get_route("/learn-export", admin=True)             # 학습데이터 JSONL 다운로드(관리자)
def _g_learn_export(h, q):
    fname, text = learn_export(q.get("kind", ["sft"])[0], h._req_team())
    if not fname:
        h._send(400, json.dumps({"error": text}, ensure_ascii=False), _JSON)
        return None
    h._send_file(text.encode("utf-8"), "application/x-ndjson; charset=utf-8", fname)


@_get_route("/learn-spec", admin=True)               # 파인튜닝 스펙·소요서(.md · 관리자)
def _g_learn_spec(h, q):
    h._send_file(learn_spec_md(h._req_team()).encode("utf-8"), "text/markdown; charset=utf-8",
                 "prism_finetune_spec.md")


@_get_route("/handoff-export", admin=True)           # 모델러 핸드오프 번들(.zip · 관리자)
def _g_handoff_export(h, q):
    fname, blob = handoff_bundle(h._req_team())
    if not fname:
        h._send(400, json.dumps({"error": blob}, ensure_ascii=False), _JSON)
        return None
    h._send_file(blob, "application/zip", fname)


@_get_route("/learn-data", admin=True)               # 학습 데이터 현황(관리자)
def _g_learn_data(h, q):
    return learn_data(h._req_team())


@_get_route("/cost-rollup", admin=True)              # 비용 롤업(일별×모델×콜 · 관리자)
def _g_cost_rollup(h, q):
    try:
        dq = int((q.get("days") or ["30"])[0])
    except (TypeError, ValueError):
        dq = 30
    return cost_rollup_data(h._req_team(), days=dq)


@_get_route("/fail-rollup", admin=True)              # 실패 트리아지(종류×모델×서비스 · 관리자)
def _g_fail_rollup(h, q):
    try:
        fq = int((q.get("days") or ["30"])[0])
    except (TypeError, ValueError):
        fq = 30
    return fail_rollup_data(h._req_team(), days=fq)


@_get_route("/assign-log", admin=True)               # 배정 이력(누가·언제·어떻게 · 관리자)
def _g_assign_log(h, q):
    return assign_log_data(h._req_team())


@_get_route("/crew")                                 # 검수운영: 인력 현황·캐파·정체(슈퍼관리자 이상)
def _g_crew(h, q):
    """전체 열람은 슈퍼관리자 이상. 그 외 로그인 사용자는 자기 카드만 받는다
    (개인 지표 노출 범위 결정 2026-07-28: 관리자 전체 · 본인은 자기 것)."""
    team = h._req_team()
    if _supa() and not is_super_admin_user(h._bearer_uid(), team, h._bearer_email()):
        uid = h._bearer_uid()
        if not uid:
            h._send(401, json.dumps({"error": "로그인이 필요합니다"}, ensure_ascii=False), _JSON)
            return None
        return CRW.crew_data(team, scope_uid=uid)
    data = CRW.crew_data(team)
    try:                                             # 마감된 주 게으른 적립(스케줄러 불필요)
        WKO.capture(team, crew=data)
    except Exception:
        pass
    return data


@_get_route("/crew-confirm")                         # 이번 주 본인 확인 필요 여부(로그인 사용자 본인)
def _g_crew_confirm(h, q):
    """검수자 본인이 이번 주 일정을 확인했는지. 주가 바뀌면 다시 필요하다.
    관리자 전용인 /crew-profile 과 달리 **본인만** 자기 것을 조회·확정한다."""
    uid = h._bearer_uid() or q.get("reviewer", [""])[0]
    if not uid:
        return {"ok": True, "needed": False, "week": 0}
    return CRW.needs_confirm(uid, h._req_team())


@_get_route("/entity-labels")                        # 엔티티 관련성 라벨(내 표 + 집계)
def _g_entity_labels(h, q):
    """상세 화면이 콘텐츠 단위로 읽는다(hash 지정). hash 없으면 수집 현황 요약(관리자용).
    전체 검수자가 남길 수 있다 — 경계선 표본은 많이 모을수록 값어치가 크다."""
    ch = (q.get("hash", [""])[0] or "").strip()
    uid = h._bearer_uid() or q.get("reviewer", [""])[0]
    if not ch:
        return ELB.summary(h._req_team())
    return ELB.for_content(ch, uid, h._req_team())


@_get_route("/crew-weekly")                          # 주간 운영 기록(슈퍼관리자 이상)
def _g_crew_weekly(h, q):
    team = h._req_team()
    if _supa() and not is_super_admin_user(h._bearer_uid(), team, h._bearer_email()):
        h._send(403, json.dumps({"error": "슈퍼관리자 이상만 볼 수 있습니다"}, ensure_ascii=False), _JSON)
        return None
    data = CRW.crew_data(team)
    try:
        WKO.capture(team, crew=data)
    except Exception:
        pass
    return WKO.weekly_records(team, weeks=int(q.get("weeks", ["8"])[0] or 8), crew=data)


@_get_route("/routes-raw", admin=True)               # 학습 지시 원본 목록 + 끔 상태(관리자)
def _g_routes_raw(h, q):
    return routes_overview(h._req_team())


@_get_route("/golden-list", admin=True)              # 관리자 골든 브라우저
def _g_golden_list(h, q):
    return golden_list(h._req_team())


@_get_route("/eval-runs")                            # 평가 런 이력(런 단위 영속 · Atelier 이식)
def _g_eval_runs(h, q):
    return eval_runs_list(h._req_team())


@_get_route("/autopilot-status")                     # 오토파일럿 최신 런 상태(폴링용)
def _g_autopilot_status(h, q):
    return autopilot_status(h._req_team())


@_get_route("/prompt-library", admin=True)           # 프롬프트 라이브러리 목록(스튜디오)
def _g_prompt_library(h, q):
    st = get_store()
    items = st.lib_list(h._req_team()) if (st and hasattr(st, "lib_list")) else []
    return {"ok": True, "items": items}


@_get_route("/deployments", admin=True)              # 배포 목록(키 메타 포함 · 스튜디오)
def _g_deployments(h, q):
    return deployments_list(h._req_team())


@_get_route("/api/v1/prompt")                        # 공개 서빙: slug + Bearer pr_live_ 키(자체 검증)
def _g_api_prompt(h, q):
    status, body = serve_prompt((q.get("slug") or [""])[0],
                                h.headers.get("Authorization") or "",
                                call=(q.get("call") or [""])[0])
    h._send(status, json.dumps(body, ensure_ascii=False), _JSON)
    return None


@_get_route("/eval-run-compare")                     # 두 런 비교(a=기준·b=대상) · 회귀 가드 판정
def _g_eval_run_compare(h, q):
    try:
        a = int((q.get("a") or ["0"])[0])
        b = int((q.get("b") or ["0"])[0])
    except (TypeError, ValueError):
        a = b = 0
    return eval_run_compare(a, b, h._req_team())


@_get_route("/eval-run")                             # 런 리포트(진행 중이면 부분 리포트 · 폴링용)
def _g_eval_run(h, q):
    try:
        rid = int((q.get("id") or ["0"])[0])
    except (TypeError, ValueError):
        rid = 0
    return eval_run_report(rid, h._req_team())


@_get_route("/activity-daily")                       # 검수 활동 추이(일별 · 최근 N일 · 팀 스코프 · 롤업 병합)
def _g_activity_daily(h, q):
    try:
        days = int((q.get("days") or ["30"])[0])
    except (TypeError, ValueError):
        days = 30
    return activity_daily_data(h._req_team(), days=days)


@_get_route("/golden-status")                        # 골든 생성 현황(팀원 공개): 확정·분류필요·불일치
def _g_golden_status(h, q):
    st = get_store()
    _rep = _report_get("learn_report", h._req_team(), LO._LAST_LEARN_REPORT) or {}
    g = _rep.get("golden") or {}
    # 검수 진행(반영 대기): 골든은 학습 반영 시에만 확정되므로, 반영 전에도 검수가
    # 쌓이고 있음을 현황에 표시(전부 0 + 안내 없음 = "표시가 안 된다" 혼란 방지)
    reviewed_n = good_n = 0
    try:
        for e in (st.feedback_map(team=h._req_team()) or {}).values():
            reviewed_n += 1
            if e.get("consensus") == "good":
                good_n += 1
    except Exception:
        pass
    return {
        "ok": True,
        "batch_seq": (st.batch_seq(h._req_team()) if (st and hasattr(st, "batch_seq")) else 0),
        "total": (st.golden_count(h._req_team()) if (st and hasattr(st, "golden_count")) else 0),
        "source_counts": (st.golden_source_counts(h._req_team())
                          if (st and hasattr(st, "golden_source_counts")) else {}),
        "reviewed": {"contents": reviewed_n, "good": good_n},
        "last_batch": {k: g.get(k) for k in ("confirmed", "new", "demoted", "need_category",
                                             "disagree", "min_good")},
        "need_list": g.get("need_list") or [],
        "ts": _rep.get("ts")}


@_get_route("/prompt-defaults")
def _g_prompt_defaults(h, q):
    sync_learned()
    return {"defaults": PR.stage_defaults(),
            "learned": {k: bool((PR.LEARNED or {}).get(k))
                        for k in ("extract", "analyze", "review", "judge")}}


@_get_route("/usermeta-template.csv")
def _g_usermeta_template_csv(h, q):
    h._send_file(build_usermeta_template_csv(), "text/csv; charset=utf-8",
                 "prism_behavior_log.csv")


@_get_route("/usermeta-profile-template.csv")
def _g_usermeta_profile_template_csv(h, q):
    from . import personagen as PG
    h._send_file(PG.profile_template_csv(), "text/csv; charset=utf-8", "prism_user_profile.csv")


@_get_route("/usermeta")
def _g_usermeta(h, q):
    return usermeta_data(team=h._req_team())


@_get_route("/usermeta-memory")                      # 파일 기반 메모리(실험실): 파일 트리·주입 미리보기·소비 카탈로그
def _g_usermeta_memory(h, q):
    return MF.memory_data(team=h._req_team())


@_get_route("/usermeta-demo")                        # 소비 시연(실험실 STEP 1~4): 피드·세션·실시간 측정·결론
def _g_usermeta_demo(h, q):
    return MF.demo_data(team=h._req_team())


@_get_route("/board")                                # 게시판: 기능개선·오류 제보(팀 스코프)
def _g_board(h, q):
    return board_data(h._req_team(), h._bearer_uid() or "")


@_get_route("/template.xlsx")
def _g_template_xlsx(h, q):
    h._send_file(build_template_xlsx(),
                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 "prism_template.xlsx")


@_get_route("/template.csv")
def _g_template_csv(h, q):
    h._send_file(build_template_csv(), "text/csv; charset=utf-8", "prism_template.csv")


# 디스패치 순서: 접두 길이 내림차순 → /entdict-lookup 이 /entdict 보다, /usermeta-*.csv 가
# /usermeta 보다 항상 먼저 검사된다(등록 순서 무관 · 가로채기 불가).
_GET_ORDER = sorted(_GET_ROUTES, key=len, reverse=True)


# ═══ POST 라우트 테이블 ═══════════════════════════════════════════════════
# 등록: @_post_route("/prefix", gate=...) · 디스패치(do_POST)는 GET 과 동일하게 최장 접두
# 우선(나열 순서 무관 · 가로채기 불가). 핸들러 계약: fn(h, body) → dict = 200 JSON ·
# None = 직접 응답. 예외는 디스패처가 일괄 500 처리(분기별 try/except 복붙 제거).
# gate: "admin"(403) · "super"(403) · "login"(401) · "team"(401/403) · ""(핸들러 내부 판단).
_POST_ROUTES = {}


def _post_route(prefix: str, gate: str = ""):
    def deco(fn):
        _POST_ROUTES[prefix] = (fn, gate)
        return fn
    return deco


@_post_route("/config", gate="admin")                # 운영: 팀 공유 설정 변경은 관리자만
def _p_config(h, body):
    uid, team, email = h._bearer_uid(), h._req_team(), h._bearer_email()
    # API 키 등록·삭제는 운영 관리자만(관리자 로컬 앱 = 서버 · _KEY_PATH 파일 저장)
    allow_key = (not _supa()) or is_sys_admin_user(uid, team, email)
    data = json.loads(body or b"{}")
    if isinstance(data.get("team_links"), dict):
        # 팀 가이드 링크(전역 공유) = 운영 관리자만
        if not allow_key:
            h._send(403, json.dumps({"error": "운영 관리자 전용입니다"}, ensure_ascii=False), _JSON)
            return None
        save_team_links(data["team_links"])
    return apply_config(data, allow_key=allow_key, team=team)


@_post_route("/ping", gate="login")                  # 미인증 실모델 호출(소액 과금·키 탐지) 차단
def _p_ping(h, body):
    try:
        d = json.loads(body or b"{}")
    except Exception:
        d = {}
    svc = (d.get("service") or "").strip()
    return ping_router(svc) if svc in IMG.ROUTERS else ping_model()


@_post_route("/store")
def _p_store(h, body):
    payload = json.loads(body or b"{}")
    team = h._req_team()
    if payload.get("clear") and _supa() and not is_admin_user(
            h._bearer_uid(), team, h._bearer_email()):
        h._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
        return None
    st = get_store()
    if payload.get("clear") and st:
        if _supa() and not team:                     # 팀 스코프 없이 전 팀 삭제 금지
            h._send(403, json.dumps({"error": "팀 스코프가 필요합니다"}, ensure_ascii=False), _JSON)
            return None
        if hasattr(st, "clear_team_contents"):
            st.clear_team_contents(team)
        else:
            st.clear()
        _LAST_RESULTS[:] = []                        # 메모리 미러 동반 정리(삭제 후 잔상 방지)
        _agg_bump()
    return {"ok": True, "count": (st.count() if st else 0)}


@_post_route("/auth")                                # 로그인/가입 프록시(supabase)
def _p_auth(h, body):
    # 무차별 대입·가입 남용 억제: IP당 최소간격 1s · 분당 12회(초과 시 429)
    if rate_limited("auth:" + _client_ip(h), min_interval=1.0, per_min=12):
        h._send(429, json.dumps({"error": "요청이 너무 잦습니다 · 잠시 후 다시 시도하세요"},
                                ensure_ascii=False), _JSON)
        return None
    return auth_action(json.loads(body or b"{}"))


@_post_route("/feedback")
def _p_feedback(h, body):
    data = json.loads(body or b"{}")
    if data.get("clear"):
        # 전체 초기화 = 관리자 전용 + 팀 스코프(무인증·전 팀 삭제 방지)
        uid, team, email = h._bearer_uid(), h._req_team(), h._bearer_email()
        if _supa() and not is_admin_user(uid, team, email):
            h._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
            return None
        data["_team"] = team
    elif not h._inject_reviewer(data):
        h._send(401, json.dumps({"error": "인증 필요"}, ensure_ascii=False), _JSON)
        return None
    rl_key = (data.get("reviewer") or "").strip() or _client_ip(h)
    if not data.get("clear") and rate_limited(rl_key):
        h._send(429, json.dumps({"error": "잠시 후 다시 시도하세요(검수 속도 제한)"},
                                ensure_ascii=False), _JSON)
        return None
    return apply_feedback(data)


@_post_route("/ops-hold", gate="admin")              # 운영자 수동 노출제한 토글(라벨 아님 · 학습 미포함)
def _p_ops_hold(h, body):
    data = json.loads(body or b"{}")
    ch = (data.get("hash") or "").strip()
    st = get_store()
    ok = bool(ch and st and hasattr(st, "set_ops_hold")
              and st.set_ops_hold(ch, bool(data.get("on")), team=h._req_team()))
    if ok:
        _agg_bump()                                  # 목록·집계 캐시 무효화(즉시 반영)
    return {"ok": ok}


@_post_route("/source-status", gate="login")         # 원문 소실 신고 토글(게시판 #10 · A안) · 검수자 신고라 admin 아닌 login
def _p_source_status(h, body):
    data = json.loads(body or b"{}")
    ch = (data.get("hash") or "").strip()
    by = h._bearer_uid() or (data.get("reviewer") or "").strip()   # supabase = uid(사칭 불가) · 로컬 = 표시명
    state = "gone" if data.get("on") else ""
    st = get_store()
    ok = bool(ch and st and hasattr(st, "set_source_status")
              and st.set_source_status(ch, state, by, team=h._req_team()))
    if ok:
        _agg_bump()                                  # 목록·집계 캐시 무효화(즉시 반영)
    return {"ok": ok, "state": state}


@_post_route("/check-source", gate="login")          # 온디맨드 원문 상태 확인(게시판 #10 · B안 축소형) · 판정만, 확정은 검수자 버튼
def _p_check_source(h, body):
    # 외부 GET 을 유발하는 라우트라 남용 억제: IP당 최소간격 1s · 분당 12회(초과 429)
    if rate_limited("chksrc:" + _client_ip(h), min_interval=1.0, per_min=12):
        h._send(429, json.dumps({"error": "요청이 너무 잦습니다 · 잠시 후 다시 시도하세요"},
                                ensure_ascii=False), _JSON)
        return None
    data = json.loads(body or b"{}")
    return check_source_url((data.get("url") or "").strip())


@_post_route("/badges", gate="login")                # 배지 획득 영속(기기 간 기준선) · 임의 uid 기록 차단
def _p_badges(h, body):
    data = json.loads(body or b"{}")
    uid = h._bearer_uid() or (data.get("reviewer") or "").strip()
    return save_badges(uid, data.get("earned"))


@_post_route("/reviewer")                            # 검수자 등록·가입 (최장 접두 매칭이 /reviewer-role 분리)
def _p_reviewer(h, body):
    data = json.loads(body or b"{}")
    if not h._inject_reviewer(data):
        h._send(401, json.dumps({"error": "인증 필요"}, ensure_ascii=False), _JSON)
        return None
    out = register_reviewer(data)
    # 가입·팀 변경 즉시 반영: 캐시 무효화는 팀 쓰기 '완료 후'에 해야 한다 — 등록 전에 pop 하면
    # 병렬 GET 의 team_of 가 쓰기 완료 전 옛 팀을 60s TTL 로 재캐시하는 경합이 생긴다.
    _TEAM_CACHE.pop(h._bearer_uid() or "", None)
    return out


@_post_route("/admin")                               # 권한 판단은 admin_action 내부(uid 기반)
def _p_admin(h, body):
    return admin_action(h._bearer_uid(), h._req_team(), json.loads(body or b"{}"),
                        h._bearer_email())


@_post_route("/learn-batch", gate="admin")           # 일배치 학습 수동 실행: 개선+골든+회귀평가
def _p_learn_batch(h, body):
    data = json.loads(body or b"{}")
    return learning_batch(h._req_team(), data.get("models"))


@_post_route("/apply-directive", gate="admin")       # 버전 지시를 공통/특정 모델 프롬프트에 적용
def _p_apply_directive(h, body):
    data = json.loads(body or b"{}")
    v = int(data.get("version") or 0)
    model = (data.get("model") or "common").strip()
    stages = [s for s in (data.get("stages") or []) if s in ("extract", "analyze", "review", "judge")]
    snap = _report_get(f"prompt_snapshot_v{v}", h._req_team()) or {}
    learned = snap.get("learned") or {}
    cfg = Config.load()
    applied = []
    for s in stages:
        d = (learned.get(s) or "").strip()
        if not d:
            continue
        if model == "common":                        # 공통 = 모든 모델 프롬프트에 얹힘(stage_prompts)
            cur = dict(cfg.stage_prompts or {})
            base = (cur.get(s) or "").strip()
            if d not in base:
                cur[s] = (base + ("\n\n" if base else "") + d).strip()
                cfg.stage_prompts = cur
        else:                                        # 특정 모델 전용(model_prompts[model][stage])
            mp = dict(cfg.model_prompts or {})
            mm = dict(mp.get(model) or {})
            base = (mm.get(s) or "").strip()
            if d not in base:
                mm[s] = (base + ("\n\n" if base else "") + d).strip()
                mp[model] = mm
                cfg.model_prompts = mp
        applied.append(s)
    cfg.save_template()
    sync_prompt()
    print(f"  [apply-directive] v{v} → {model} · 단계 {applied}")
    return {"ok": True, "applied": applied, "model": model, "version": v}


@_post_route("/compare-models", gate="admin")        # 골든셋 다중 모델 비교
def _p_compare_models(h, body):
    data = json.loads(body or b"{}")
    return compare_models_on_golden(data.get("models"), h._req_team(),
                                    scope=(data.get("scope") or "all").strip())


@_post_route("/patch-meta")                          # 검수자 구조화 교정(빈 카테고리 채우기 등)
def _p_patch_meta(h, body):
    data = json.loads(body or b"{}")
    if not h._inject_reviewer(data):
        h._send(401, json.dumps({"error": "인증 필요"}, ensure_ascii=False), _JSON)
        return None
    res = patch_content_meta(data.get("hash"), data.get("patch"),
                             h._req_team(), reviewer=data.get("reviewer") or "")
    if res.get("ok"):                                # 분류 채우기 미션 판정(1회 보상)
        fresh = _check_missions((data.get("reviewer") or "").strip(), h._req_team())
        if fresh:
            res["missions_completed"] = fresh
    return res


@_post_route("/prompt-library-save", gate="admin")   # 라이브러리 저장(패턴 재사용 원천)
def _p_prompt_library_save(h, body):
    d = json.loads(body or b"{}")
    name = (d.get("name") or "").strip()
    prompt = (d.get("prompt") or "").strip()
    if not name or not prompt:
        return {"ok": False, "error": "이름과 프롬프트 본문을 입력하세요"}
    st = get_store()
    if not (st and hasattr(st, "lib_add")):
        return {"ok": False, "error": "스토어가 라이브러리를 지원하지 않습니다"}
    lid = st.lib_add(h._req_team(), name[:80], (d.get("domain") or "").strip()[:40],
                     prompt, note=(d.get("note") or "").strip()[:300],
                     source=(d.get("source") or "manual").strip()[:40],
                     created_by=h._bearer_uid() or "")
    return {"ok": True, "id": lid}


@_post_route("/prompt-library-remove", gate="admin")
def _p_prompt_library_remove(h, body):
    d = json.loads(body or b"{}")
    st = get_store()
    ok = bool(st and hasattr(st, "lib_remove") and st.lib_remove(int(d.get("id") or 0), h._req_team()))
    return {"ok": ok} if ok else {"ok": False, "error": "항목을 찾을 수 없습니다"}


@_post_route("/prompt-library-pin", gate="admin")
def _p_prompt_library_pin(h, body):
    d = json.loads(body or b"{}")
    st = get_store()
    ok = bool(st and hasattr(st, "lib_pin")
              and st.lib_pin(int(d.get("id") or 0), bool(d.get("pinned")), h._req_team()))
    return {"ok": ok} if ok else {"ok": False, "error": "항목을 찾을 수 없습니다"}


@_post_route("/deployment-save", gate="admin")       # 배포 생성/수정(슬러그·버전 pin)
def _p_deployment_save(h, body):
    d = json.loads(body or b"{}")
    return deployment_save(h._req_team(), dep_id=d.get("id"), slug=d.get("slug") or "",
                           name=d.get("name") or "", version=d.get("version") or 0,
                           active=d.get("active", True), created_by=h._bearer_uid() or "")


@_post_route("/deployment-remove", gate="admin")
def _p_deployment_remove(h, body):
    d = json.loads(body or b"{}")
    return deployment_remove(int(d.get("id") or 0), h._req_team())


@_post_route("/deployment-key-new", gate="admin")    # 키 발급(평문 1회 노출 · sha256 저장)
def _p_deployment_key_new(h, body):
    d = json.loads(body or b"{}")
    return deployment_key_new(int(d.get("id") or 0), h._req_team())


@_post_route("/deployment-key-revoke", gate="admin")
def _p_deployment_key_revoke(h, body):
    d = json.loads(body or b"{}")
    return deployment_key_revoke(int(d.get("id") or 0), int(d.get("key_id") or 0), h._req_team())


@_post_route("/builder-test", gate="admin")          # 컴파일 산출을 테스트 모델로 1회 실행(실모델 비용)
def _p_builder_test(h, body):
    data = json.loads(body or b"{}")
    return builder_test(data.get("system") or "", data.get("input") or "",
                        model=(data.get("model") or "").strip(), team=h._req_team())


@_post_route("/builder-compile", gate="admin")       # 선언형 스펙→계열별 프롬프트 컴파일(실모델 비용)
def _p_builder_compile(h, body):
    data = json.loads(body or b"{}")
    return builder_compile(data.get("spec") or data, h._req_team())


@_post_route("/meta-compile", gate="admin")          # 실모델 호출(비용) 트리거 · /learn-batch 와 동일 게이트
def _p_meta_compile(h, body):
    return meta_compile_run(h._req_team())


@_post_route("/eval-judge")                          # 평가 상세 · 건별 판정(집단 지성)
def _p_eval_judge(h, body):
    # 팀원 기능이지만 미인증 직접 호출은 차단(supabase 모드 · 판정 위조 방지)
    if _supa() and not h._bearer_uid():
        h._send(403, json.dumps({"error": "로그인이 필요합니다"}, ensure_ascii=False), _JSON)
        return None
    data = json.loads(body or b"{}")
    # 검수자 귀속은 서버가 Bearer uid 로 강제(바디 reviewer 위조로 합의 스터핑·점수 파밍 방지).
    rv = h._bearer_uid() if _supa() else (data.get("reviewer") or "").strip()
    verdict = (data.get("verdict") or "").strip()
    ch = (data.get("hash") or "").strip()
    if not rv or not ch or verdict not in ("adopt", "reject"):
        return {"ok": False, "error": "판정 값이 올바르지 않습니다"}
    if rate_limited(f"evj:{rv}"):
        h._send(429, json.dumps({"error": "잠시 후 다시 시도하세요(판정 속도 제한)"},
                                ensure_ascii=False), _JSON)
        return None
    st = get_store()
    ok = bool(st and hasattr(st, "save_eval_check")
              and st.save_eval_check(ch, rv, verdict, str(data.get("expected") or ""),
                                     str(data.get("got") or ""), team=h._req_team()))
    if ok:
        try:                                         # 판정 보상: 콘텐츠당 1회 +5pt(재판정은 upsert 만)
            st.log_event_once(rv, "evja:" + ch, 0, 5, team=h._req_team())
        except Exception:
            pass
    counts = {}
    try:
        counts = (st.eval_check_counts(team=h._req_team()) or {}).get(ch) or {}
    except Exception:
        pass
    _agg_bump()
    return {"ok": ok, "judge": counts or {"adopt": 0, "reject": 0, "reviewers": {}}}


@_post_route("/eval-golden", gate="admin")           # 등록 골든셋으로 평가 실행(실모델 비용 트리거)
def _p_eval_golden(h, body):
    data = json.loads(body or b"{}")
    return eval_golden(h._req_team(), model=(data.get("model") or "").strip(),
                       scope=(data.get("scope") or "all").strip())


@_post_route("/eval-run-start", gate="admin")        # 평가 런 시작(백그라운드 · 이력 영속)
def _p_eval_run_start(h, body):
    data = json.loads(body or b"{}")
    return eval_run_start(h._req_team(), model=(data.get("model") or "").strip(),
                          scope=(data.get("scope") or "all").strip(),
                          created_by=h._bearer_uid() or "")


@_post_route("/eval-run-resume", gate="admin")       # 중단 런 재개(남은 건만 실행)
def _p_eval_run_resume(h, body):
    data = json.loads(body or b"{}")
    return eval_run_resume(int(data.get("id") or 0), h._req_team())


@_post_route("/eval-run-cancel", gate="admin")       # 실행 중 런 중단(저장 결과 유지)
def _p_eval_run_cancel(h, body):
    data = json.loads(body or b"{}")
    return eval_run_cancel(int(data.get("id") or 0), h._req_team())


@_post_route("/autopilot-start", gate="admin")       # 자동 개선 루프 시작(라운드=학습 반영 · 실모델 비용)
def _p_autopilot_start(h, body):
    data = json.loads(body or b"{}")
    return autopilot_start(h._req_team(), target=data.get("target") or 0.9,
                           max_rounds=data.get("max_rounds") or 5,
                           created_by=h._bearer_uid() or "")


@_post_route("/autopilot-stop", gate="admin")        # 라운드 경계에서 중지(반영 라운드 유지)
def _p_autopilot_stop(h, body):
    return autopilot_stop(h._req_team())


@_post_route("/eval-rubric-start", gate="admin")     # 완주 런 루브릭 채점(4축 · LLM 심사 비용)
def _p_eval_rubric_start(h, body):
    data = json.loads(body or b"{}")
    return rubric_start(int(data.get("id") or 0), h._req_team())


@_post_route("/eval-rubric-cancel", gate="admin")    # 루브릭 채점 중단(채점된 건 유지)
def _p_eval_rubric_cancel(h, body):
    data = json.loads(body or b"{}")
    return rubric_cancel(int(data.get("id") or 0), h._req_team())


@_post_route("/content-remove", gate="admin")        # 콘텐츠 개별 삭제(파생 데이터 연쇄)
def _p_content_remove(h, body):
    data = json.loads(body or b"{}")
    st = get_store()
    ok = bool(st and hasattr(st, "remove_content")
              and st.remove_content((data.get("hash") or "").strip(), team=h._req_team()))
    _agg_bump()
    return {"ok": ok}


@_post_route("/entity-label", gate="login")           # 엔티티 관련성 라벨 남기기(전 검수자)
def _p_entity_label(h, body):
    data = json.loads(body or b"{}")
    uid = h._bearer_uid() or (data.get("reviewer") or "").strip()
    if not uid:
        h._send(401, json.dumps({"error": "로그인이 필요합니다"}, ensure_ascii=False), _JSON)
        return None
    return ELB.put((data.get("hash") or ""), (data.get("entity") or ""),
                   (data.get("label") or ""), uid, team=h._req_team())


@_post_route("/crew-confirm", gate="login")          # 이번 주 본인 확인 저장(본인 것만)
def _p_crew_confirm(h, body):
    data = json.loads(body or b"{}")
    uid = h._bearer_uid() or (data.get("reviewer") or "").strip()
    if not uid:
        h._send(401, json.dumps({"error": "로그인이 필요합니다"}, ensure_ascii=False), _JSON)
        return None
    # uid 는 토큰에서만 온다 — 본문의 uid 를 믿으면 남의 확인을 대신 눌러줄 수 있다
    return CRW.confirm_week(uid, data.get("patch") or {}, team=h._req_team())


@_post_route("/crew-profile", gate="super")          # 검수운영: 인력 원장(가용 시간·근무 요일·부재·상태)
def _p_crew_profile(h, body):
    data = json.loads(body or b"{}")
    actor = h._bearer_email() or h._bearer_uid() or "(로컬)"
    if isinstance(data.get("settings"), dict):       # 운영 파라미터(여유율·정체 기준 등)
        return {"ok": True, "settings": CRW.set_settings(data["settings"], h._req_team())}
    return CRW.set_profile((data.get("uid") or "").strip(), data.get("patch") or {},
                           team=h._req_team(), by=actor)


@_post_route("/crew-wave", gate="super")             # 검수운영: 웨이브(주 사이클) 마감 설정·해제
def _p_crew_wave(h, body):
    data = json.loads(body or b"{}")
    actor = h._bearer_email() or h._bearer_uid() or "(로컬)"
    return CRW.set_wave(data.get("due_at"), by=actor, plan=data.get("plan"), team=h._req_team())


@_post_route("/crew-due", gate="super")              # 검수운영: 진행 중 웨이브의 기한만 조정(연장·단축)
def _p_crew_due(h, body):
    data = json.loads(body or b"{}")
    actor = h._bearer_email() or h._bearer_uid() or "(로컬)"
    return CRW.adjust_due(data.get("due_at"), by=actor, team=h._req_team())


@_post_route("/crew-assign", gate="super")           # 검수운영: 캐파 비례 배정(미리보기 → 실행)
def _p_crew_assign(h, body):
    data = json.loads(body or b"{}")
    hashes = [str(x).strip() for x in (data.get("hashes") or [])
              if str(x).strip() and not str(x).strip().startswith("gold:")]   # 골드 가상 행 제외(배정 라우트 공통 정책)
    try:
        minr = int(data.get("min_reviewers") or 1)
    except (TypeError, ValueError):
        minr = 1
    actor = h._bearer_email() or h._bearer_uid() or "(로컬)"
    return CRW.plan_distribute(hashes, min_reviewers=minr,
                               reviewers=[str(r).strip() for r in (data.get("reviewers") or []) if str(r).strip()],
                               team=h._req_team(), apply=bool(data.get("apply")),
                               by=actor, due_at=data.get("due_at"))


@_post_route("/crew-escalate", gate="super")         # 검수운영: 의견 갈린 건에 3번째 검수자 붙이기
def _p_crew_escalate(h, body):
    data = json.loads(body or b"{}")
    actor = h._bearer_email() or h._bearer_uid() or "(로컬)"
    return CRW.escalate_split(team=h._req_team(), apply=bool(data.get("apply")), by=actor)


@_post_route("/crew-auto", gate="super")             # 검수운영: 자동 운영 점검(사이클당 1회 · 멱등)
def _p_crew_auto(h, body):
    data = json.loads(body or b"{}")
    return CRW.auto_tick(team=h._req_team(), apply=bool(data.get("apply")))


@_post_route("/crew-rebalance", gate="super")        # 검수운영: 정체분 회수 → 여력 있는 인원에게 이관
def _p_crew_rebalance(h, body):
    data = json.loads(body or b"{}")
    actor = h._bearer_email() or h._bearer_uid() or "(로컬)"
    return CRW.rebalance(team=h._req_team(), apply=bool(data.get("apply")), by=actor)


def _drop_finals(reviewers, team=None):
    """배정 대상에서 최종검수자를 걸러낸다(수동 경로 · 자동은 crewops._assignable).

    최종검수자는 기초 판정이 갈렸을 때 확정하는 2층 역할이라 같은 콘텐츠의 기초 검수를
    맡으면 자기 판정을 자기가 확정하게 된다(사용자 결정 2026-07-28). 화면은 후보에서
    빼지만 API 직접 호출도 있으므로 서버가 최종 방어선이다.
    반환 (걸러진 목록, 제외된 id 목록)."""
    try:
        finals = set(reviewer_roles(team) or {})
    except Exception:
        return list(reviewers), []
    keep = [r for r in reviewers if r not in finals]
    return keep, [r for r in reviewers if r in finals]


@_post_route("/content-assign-bulk", gate="super")   # 여러 콘텐츠 일괄 배정(덮어쓰기)
def _p_content_assign_bulk(h, body):
    data = json.loads(body or b"{}")
    # 골드 문항(가상 검증 행 · 'gold:*')은 배정 대상 아님 — 저장돼도 재주입 행에 반영되지
    # 않아 '배정했는데 새로고침하면 미배정' 증상만 남긴다(개별 라우트와 동일 정책)
    hashes = [str(x).strip() for x in (data.get("hashes") or [])
              if str(x).strip() and not str(x).strip().startswith("gold:")]
    reviewers = [str(r).strip() for r in (data.get("reviewers") or []) if str(r).strip()]
    reviewers, dropped = _drop_finals(reviewers, h._req_team())
    try:
        minr = int(data.get("min_reviewers") or 1)
    except (TypeError, ValueError):
        minr = 1
    st = get_store()
    if dropped and not reviewers:
        h._send(400, json.dumps({"error": "최종검수자는 기초 검수 배정 대상이 아닙니다"},
                                ensure_ascii=False), _JSON)
        return None
    if not (hashes and st and hasattr(st, "set_assignees_bulk")):
        h._send(400, json.dumps({"error": "대상 없음 또는 미지원 백엔드"}, ensure_ascii=False), _JSON)
        return None
    actor = (h._bearer_email() or h._bearer_uid()
             or (data.get("reviewer") or "").strip() or "(로컬)")
    if (data.get("mode") or "") == "distribute":     # 균등 분배: 부하 적은 사람부터
        if not reviewers:
            h._send(400, json.dumps({"error": "분배할 담당자를 선택하세요"}, ensure_ascii=False), _JSON)
            return None
        r = distribute_assignments(st, hashes, reviewers, min_reviewers=minr,
                                   team=h._req_team())
        _log_assign(actor, "균등 분배", r["n"], reviewers, r["min_reviewers"], h._req_team())
        _agg_bump()
        return {"ok": True, "mode": "distribute", "n": r["n"],
                "per_reviewer": r["per_reviewer"], "min_reviewers": r["min_reviewers"]}
    n = st.set_assignees_bulk(hashes, reviewers, min_reviewers=minr, team=h._req_team())
    _log_assign(actor, ("일괄 배정" if reviewers else "일괄 해제"), n, reviewers,
                (max(1, min(len(reviewers), minr)) if reviewers else 0), h._req_team())
    _agg_bump()
    return {"ok": True, "n": n, "reviewers": reviewers,
            "min_reviewers": (max(1, min(len(reviewers), minr)) if reviewers else 0)}


@_post_route("/content-assign", gate="admin")        # 콘텐츠 검수 담당자 배정(배타적 노출)
def _p_content_assign(h, body):
    data = json.loads(body or b"{}")
    ch = (data.get("hash") or "").strip()
    reviewers = [str(r).strip() for r in (data.get("reviewers") or []) if str(r).strip()]
    reviewers, dropped = _drop_finals(reviewers, h._req_team())
    if dropped and not reviewers:
        h._send(400, json.dumps({"error": "최종검수자는 기초 검수 배정 대상이 아닙니다"},
                                ensure_ascii=False), _JSON)
        return None
    try:
        minr = int(data.get("min_reviewers") or 1)
    except (TypeError, ValueError):
        minr = 1
    st = get_store()
    if not (ch and st and hasattr(st, "set_assignees")):
        h._send(400, json.dumps({"error": "hash 누락 또는 미지원 백엔드"}, ensure_ascii=False), _JSON)
        return None
    if ch.startswith("gold:"):                       # 골드 문항(가상 검증 행): 배정 개념 없음
        h._send(400, json.dumps({"error": "골드 문항(정답 검증용)은 배정 대상이 아닙니다"},
                                ensure_ascii=False), _JSON)
        return None
    st.set_assignees(ch, reviewers, min_reviewers=minr, team=h._req_team())
    _log_assign((h._bearer_email() or h._bearer_uid()
                 or (data.get("reviewer") or "").strip() or "(로컬)"),
                ("개별 배정" if reviewers else "개별 해제"), 1, reviewers, minr, h._req_team())
    _agg_bump()
    cur = (st.assignees(team=h._req_team()) or {}).get(ch) or {"reviewers": [], "min": 0}
    return {"ok": True, "assignees": cur["reviewers"], "min_reviewers": cur["min"]}


@_post_route("/route-disable", gate="admin")         # 학습 지시 개별 끄기/켜기
def _p_route_disable(h, body):
    data = json.loads(body or b"{}")
    return set_directive_disabled(data.get("text") or "", bool(data.get("disabled")))


@_post_route("/reviewer-role", gate="super")         # 최종검수자 역할 지정/해제
def _p_reviewer_role(h, body):
    data = json.loads(body or b"{}")
    return set_reviewer_role(data.get("id") or "", (data.get("role") or "").strip(),
                             h._req_team())


@_post_route("/final-verdict")                       # 최종판정(타이브레이크): 슈퍼관리자 또는 최종검수자
def _p_final_verdict(h, body):
    if _supa() and not (is_super_admin_user(h._bearer_uid(), h._req_team(), h._bearer_email())
                        or is_final_reviewer(h._bearer_uid(), h._req_team())):
        h._send(403, json.dumps({"error": "슈퍼관리자 또는 최종검수자 전용입니다"}, ensure_ascii=False), _JSON)
        return None
    data = json.loads(body or b"{}")
    h._inject_reviewer(data)                         # supabase: reviewer=uid 통일(미션·골드 원장 정합)
    team = h._req_team()
    if "_team" not in data:
        data["_team"] = team
    rv = (data.get("reviewer") or "").strip()
    if (data.get("hash") or "").startswith("goldf:"):
        # 골드 캘리브레이션 응답 → gold_checks 분리 기록(최종판정 원장 무오염)
        return apply_gold_answer(data)
    res = set_final_verdict(data.get("hash") or "", (data.get("verdict") or "").strip(),
                            by=rv, team=team)
    if res.get("ok") and (data.get("verdict") or "").strip():
        ms = _check_missions(rv, team)
        if ms:
            res["missions_completed"] = ms
    return res


@_post_route("/golden-remove", gate="admin")         # 골든 개별 삭제(라벨 오류 후보 처리)
def _p_golden_remove(h, body):
    data = json.loads(body or b"{}")
    st = get_store()
    ok = bool(st and hasattr(st, "remove_golden")
              and st.remove_golden((data.get("hash") or "").strip(), team=h._req_team()))
    _agg_bump()
    return {"ok": ok}


@_post_route("/golden")                              # 골든셋 등록(.jsonl 업로드 · merge 지원 · 권한은 register_golden 내부)
def _p_golden(h, body):
    ctype = h.headers.get("Content-Type", "")
    merge = False
    if "multipart/form-data" in ctype:
        fields = _parse_multipart(body, ctype.split("boundary=", 1)[1].strip())
        f = fields.get("file")
        raw = f.get("bytes", b"") if isinstance(f, dict) else b""
        merge = str(fields.get("merge") or "").strip().lower() in ("1", "true")
    else:
        raw = body
    rows = [json.loads(ln) for ln in raw.decode("utf-8", "replace").splitlines() if ln.strip()]
    return register_golden(h._bearer_uid(), h._req_team(), rows, h._bearer_email(), merge=merge)


@_post_route("/backfill-urls", gate="admin")         # 원문 링크 백필(source_url 만 갱신 · 초안·판정 불변)
def _p_backfill_urls(h, body):
    ctype = h.headers.get("Content-Type", "")
    if "multipart/form-data" not in ctype:
        h._send(400, json.dumps({"error": "매핑 파일이 필요합니다(multipart)"}, ensure_ascii=False), _JSON)
        return None
    fields = _parse_multipart(body, ctype.split("boundary=", 1)[1].strip())
    f = fields.get("file")
    if not isinstance(f, dict) or not f.get("bytes"):
        h._send(400, json.dumps({"error": "파일이 없습니다"}, ensure_ascii=False), _JSON)
        return None
    return backfill_urls(f["bytes"], f.get("filename", "map.csv"), team=h._req_team())


@_post_route("/presence", gate="team")               # 팀 SSE 방송 트리거 · 미인증·무팀 직접 호출 차단
def _p_presence(h, body):
    p = json.loads(body or b"{}")
    # 검수자 귀속은 서버 uid 로 강제(프레즌스 사칭 방지) · 같은 팀에만 방송
    rv = h._bearer_uid() if _supa() else (p.get("reviewer") or "").strip()
    team = h._req_team()
    if _supa() and team is None:
        # 운영에서 team=None 방송은 broadcast 필터를 통과해 전 팀 스트림에 전파된다
        # (무팀 인증 계정의 교차팀 이벤트 주입) — 팀 스코프 없으면 방송하지 않는다.
        return {"ok": False}
    broadcast({"type": "presence", "reviewer": rv,
               "hash": p.get("hash") or "", "action": p.get("action") or "viewing"},
              team=team)
    return {"ok": True}


@_post_route("/purpose", gate="admin")               # 콘텐츠 용도 지정(검수용/평가용)
def _p_purpose(h, body):
    data = json.loads(body or b"{}")
    st = get_store()
    n = st.set_purpose([x for x in (data.get("hashes") or []) if x],
                       (data.get("purpose") or "").strip(), team=h._req_team()) if st else 0
    _agg_bump()
    return {"ok": bool(n), "n": n}


@_post_route("/rerun-all", gate="admin")             # 전체·미실행·선택 콘텐츠 일괄 실행
def _p_rerun_all(h, body):
    # hashes 지정 = 콘텐츠 관리 표에서 다중 선택한 건만 실행(scope 는 서버가 selected 로 승격).
    # force: 퀘스트 진행 중 확인 모달을 거친 강행 — 개별 재실행(/rerun)과 같은 규약.
    data = json.loads(body or b"{}")
    scope = (data.get("scope") or "all").strip()
    scope = scope if scope in ("all", "pending") else "all"
    raw = data.get("hashes")
    hashes = [str(x).strip() for x in raw[:RN.SELECTED_MAX] if str(x).strip()] if isinstance(raw, list) else []
    # 미실행만은 인입 경로(엑셀 여러 개·수동 단건) 무관 전량 합산 실행(PENDING_MAX) —
    # 회당 200 이면 엑셀 2개(400건)를 올려도 한 번에 못 돌아 '파일 단위'처럼 보였다.
    # 전체 재실행(all)은 이미 실행된 건 전량 재과금이라 200 가드 유지.
    return rerun_all((data.get("model") or "").strip(), h._req_team(), scope=scope,
                     limit=(RN.PENDING_MAX if scope == "pending" else 200),
                     hashes=hashes, force_quest=bool(data.get("force")))


@_post_route("/rerun", gate="admin")                 # 같은 콘텐츠를 다른 모델로 재실행
def _p_rerun(h, body):
    # force: 퀘스트 진행 중에도 이 한 건만 의도적 재실행(클라이언트 확인 모달 경유 · 관리자 판단)
    data = json.loads(body or b"{}")
    return rerun_content(data.get("hash"), (data.get("model") or "").strip(), h._req_team(),
                         force_quest=bool(data.get("force")))


@_post_route("/ingest-run", gate="admin")            # 콘텐츠 인입은 관리자 통제(수동·자동 공통)
def _p_ingest_run(h, body):
    return ingest_run_source(json.loads(body or b"{}"), trigger="manual")


@_post_route("/entdict")
def _p_entdict(h, body):
    data = json.loads(body or b"{}")
    # 권한 분리: 상세·수정·보강은 검수자(로그인)도 가능(검수 중 사전 교정 허용) ·
    # 등재/삭제/일괄 보강/백필 같은 사전 전체 작업은 관리자 전용(사전·정책과 동일 게이트)
    act = (data.get("action") or "").strip()
    if _supa():
        if act in ("detail", "update", "enrich"):
            if not h._bearer_uid():
                h._send(401, json.dumps({"error": "로그인이 필요합니다"}, ensure_ascii=False), _JSON)
                return None
        elif not is_admin_user(h._bearer_uid(), h._req_team(), h._bearer_email()):
            h._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
            return None
    return entdict_action(data, team=h._req_team(), mock=Handler.server_mock)


@_post_route("/dict", gate="admin")                  # 사전·정책 편집 = 관리자 전용(UI 게이팅과 정합)
def _p_dict(h, body):
    payload = json.loads(body or b"{}")
    fn = reset_dict_overrides if payload.get("reset") else (lambda: edit_dict(payload))
    return fn()


@_post_route("/board")                               # 게시판: 등록·상태 변경·삭제(팀 스코프)
def _p_board(h, body):
    data = json.loads(body or b"{}")
    if not h._inject_reviewer(data):
        h._send(401, json.dumps({"error": "인증 필요"}, ensure_ascii=False), _JSON)
        return None
    return board_action(data, team=h._req_team(), uid=h._bearer_uid() or "",
                        email=h._bearer_email())


@_post_route("/ca-understand", gate="login")          # 콘텐츠 에이전트(실험실): 자연어 → 위젯 조건(LLM · 실패 시 클라이언트 규칙 폴백)
def _p_ca_understand(h, body):
    data = json.loads(body or b"{}")
    text = (data.get("text") or "").strip()[:400]
    dic = data.get("dict") or {}
    if not isinstance(dic, dict):
        dic = {}
    out, via = CA.understand(text, dic, (data.get("model") or "").strip(), Handler.server_mock)
    return {"ok": bool(out), "cond": out, "via": via}


@_post_route("/topic-studio")                        # 토픽 스튜디오: 생성·삭제·튜닝(변경은 관리자) · 미리보기·제안(조회)
def _p_topic_studio(h, body):
    data = json.loads(body or b"{}")
    action = (data.get("action") or "").strip()
    # 조회성(preview·suggest)은 로그인 필수(익명 LLM 호출·데이터 열람 차단) · 변경성은 /config 와 동일 관리자 가드
    if action not in ("preview", "suggest"):
        if not h._admin_gate():
            return None
    elif not h._require_login():
        return None
    # 변경성 액션의 캐시 무효화는 topic_studio_action 내부에서 처리
    return topic_studio_action(data, mock=Handler.server_mock)


@_post_route("/media-extract", gate="login")         # 미디어 메타 파이프라인: 자막 파싱(JSON) · 영상 네이티브(multipart)
def _p_media_extract(h, body):
    ctype = h.headers.get("Content-Type", "")
    if "multipart/form-data" in ctype:               # 업로드 → 미디어 실험(미저장): 이미지(image*) | 영상(file)
        fields = _parse_multipart(body, ctype.split("boundary=", 1)[1].strip())
        imgs = {k: v for k, v in fields.items()
                if k.startswith("image") and isinstance(v, dict) and v.get("bytes")}
        if imgs:                                     # 이미지 실험: run_pipeline 이미지 분기 재사용(미저장)
            vp = (fields.get("vision_provider") or "").strip()
            vm = (fields.get("vision_model") or "").strip()
            vision = (vp, vm) if vp else None        # 선택 시각 슬롯(없으면 전역 config)
            pf = {"displayServiceName": fields.get("displayServiceName", "포토"),
                  "title": fields.get("title", ""), "caption": fields.get("caption", ""),
                  "body": fields.get("body", ""), "source_url": fields.get("source_url", "")}
            pf.update(imgs)
            res = run_pipeline(pf, mock=Handler.server_mock,
                               model=fields.get("model", ""), persist=False, vision=vision)
            return {"ok": True, **res}
        f = fields.get("file") or {}
        if not f.get("bytes"):
            h._send(400, json.dumps({"ok": False, "error": "이미지 또는 영상 파일이 필요합니다"}, ensure_ascii=False), _JSON)
            return None
        return media_native(f["bytes"], f.get("mime") or "video/mp4",
                            caption=fields.get("caption", ""),
                            description=fields.get("description", ""),
                            model=fields.get("model", ""),
                            subtitles=fields.get("subtitles", ""))
    return media_action(json.loads(body or b"{}"))


@_post_route("/usermeta-profiles", gate="team")      # 사용자 메타(프로필) 입력: 폼 단건(JSON)·서식 업로드(multipart)
def _p_usermeta_profiles(h, body):
    from . import personagen as PG
    ctype = h.headers.get("Content-Type", "")
    if "multipart/form-data" in ctype:
        boundary = ctype.split("boundary=", 1)[1].strip()
        f = _parse_multipart(body, boundary).get("file")
        profs = (PG.parse_profiles(f["bytes"], f.get("filename", "profiles.csv"))
                 if isinstance(f, dict) and f.get("bytes") else [])
    else:
        p = json.loads(body or b"{}")
        profs = p.get("profiles") or ([p.get("profile")] if p.get("profile") else [])
    return usermeta_save_profiles(profs, team=h._req_team())


@_post_route("/usermeta-memory", gate="team")        # 파일 기반 메모리(실험실): 소비 자동 기록·쓰기·추가·삭제
def _p_usermeta_memory(h, body):
    return MF.memory_ops(json.loads(body or b"{}"), team=h._req_team())


@_post_route("/usermeta-demo", gate="team")          # 소비 시연 조작: event(행동 수집)·finish(결론)·reset
def _p_usermeta_demo(h, body):
    return MF.demo_ops(json.loads(body or b"{}"), team=h._req_team())


@_post_route("/usermeta", gate="team")               # 행동 로그 업로드/현황 · 팀 미소속 전 팀 열람 차단
def _p_usermeta(h, body):
    ctype = h.headers.get("Content-Type", "")
    f = None
    if "multipart/form-data" in ctype:
        boundary = ctype.split("boundary=", 1)[1].strip()
        f = _parse_multipart(body, boundary).get("file")
    logs = f["bytes"] if isinstance(f, dict) and f.get("bytes") else None
    name = f.get("filename", "logs.csv") if isinstance(f, dict) else ""
    return usermeta_data(logs, name, team=h._req_team())


@_post_route("/run")                                 # 추출 실행(단건 /run · 배치 /run-batch) = 콘텐츠 인입
def _p_run(h, body):
    # 관리자 통제(supabase 모드) · 만료 로그인은 메시지로 구분
    if _supa() and not is_admin_user(h._bearer_uid(), h._req_team(), h._bearer_email()):
        msg = ("로그인이 만료됐습니다 · 다시 로그인 후 시도하세요" if not h._bearer_uid()
               else "콘텐츠 인입은 관리자 전용입니다")
        h._send(403, json.dumps({"error": msg}, ensure_ascii=False), _JSON)
        return None
    ctype = h.headers.get("Content-Type", "")
    try:
        if "multipart/form-data" in ctype:
            boundary = ctype.split("boundary=", 1)[1].strip()
            fields = _parse_multipart(body, boundary)
        else:
            fields = json.loads(body or b"{}")
        add_only = str(fields.get("add_only") or "") in ("1", "true")
        if h.path.startswith("/run-batch"):
            f = fields.get("file")
            if not isinstance(f, dict) or not f.get("bytes"):
                result = {"error": "파일이 없습니다"}
            else:
                result = run_batch(f["bytes"], f.get("filename", "upload.xlsx"),
                                   purpose=str(fields.get("purpose") or ""), team=h._req_team(),
                                   add_only=add_only)
        elif add_only:                               # STEP 1 = 추가만(모델 미실행)
            result = add_contents([{
                "displayServiceName": fields.get("displayServiceName", ""),
                "title": fields.get("title", ""), "subtitle": fields.get("subtitle", ""),
                "body": fields.get("body", ""),
                "source_url": fields.get("source_url", ""),
                # 참조 이미지 URL(게시판 #9) · 화이트리스트에서 빠져 있어 단건 추가는 항상 유실됐다.
                # 정규화(http(s)·중복·상한)는 add_contents 가 담당.
                "image_urls": fields.get("image_urls") or fields.get("images") or [],
            }], purpose=str(fields.get("purpose") or ""), team=h._req_team())
        else:
            result = run_pipeline(fields, mock=h.server_mock, team=h._req_team())
        return result
    except Exception:
        import traceback
        traceback.print_exc()                        # 인입 실패는 원인 추적용 트레이스 유지(기존 동작)
        raise


# 디스패치 순서: 접두 길이 내림차순 → /reviewer-role·/content-assign-bulk·/golden-remove·
# /rerun-all·/usermeta-profiles 가 짧은 형제 라우트보다 항상 먼저 검사된다.
_POST_ORDER = sorted(_POST_ROUTES, key=len, reverse=True)


class Handler(BaseHTTPRequestHandler):
    server_mock = False

    def log_message(self, *a):
        pass

    _GZIP_CT = ("text/html", "application/json", "text/css", "application/javascript",
                "text/csv", "image/svg", "text/plain")

    # 보안 응답 헤더. CSP 는 앱 구조(Alpine 표현식=unsafe-eval · 인라인 <script> 2개·인라인 스타일=
    # unsafe-inline · data: 폰트/아이콘 · 원문 미리보기 iframe=https: · 수집 이미지 표시 img=https:,
    # 게시판 #9)에 맞춘 실동작 정책.
    # connect-src 'self' 로 XSS 발화 시 임의 호스트 유출을 차단, frame-ancestors/base-uri/object-src 로
    # 클릭재킹·base 주입·플러그인을 봉쇄. (SSE 스트림은 _serve_sse 가 직접 헤더를 쓰므로 별도.)
    _CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; "
            "connect-src 'self'; frame-src 'self' https:; frame-ancestors 'none'; "
            "base-uri 'self'; object-src 'none'")

    def _security_headers(self):
        self.send_header("Content-Security-Policy", self._CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

    _VENDOR_CACHE = {}   # path → (mtime, raw, gz) · 파일 ~20개·수 MB → 메모리 부담 없음

    def _send_prezipped(self, code, raw, gz, ctype, cache="", etag=""):
        """사전 압축 자산 전송(_send 와 동일한 헤더 규약).
        불변 자산(벤더·부팅당 정적 HTML)이 요청마다 gzip 레벨6 재압축을 하지 않게 한다."""
        data, enc = raw, ""
        if gz is not None and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            data, enc = gz, "gzip"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self._security_headers()
        if enc:
            self.send_header("Content-Encoding", enc)
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(data)))
        if cache:
            self.send_header("Cache-Control", cache)
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(data)

    def _send_page(self, mobile=False):
        """SPA HTML: 부팅당 1회 사전압축 + ETag(부팅ID) 재검증.
        no-cache = 매 로드 재검증이라 '옛 페이지 잔존 방지'(구 no-store 의 목적)는 유지하면서,
        같은 부팅이면 304 로 전량 재전송을 생략하고 배포(새 부팅ID)면 ETag 불일치로 전체 갱신."""
        etag = '"' + _BOOT_ID + '"'
        if (self.headers.get("If-None-Match") or "").strip() == etag:
            self.send_response(304)                  # 304 는 본문 없음(RFC) · Content-Length 생략
            self._security_headers()
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        raw, gz = _page_payload(mobile)
        self._send_prezipped(200, raw, gz, "text/html; charset=utf-8",
                             cache="no-cache", etag=etag)

    def _send(self, code, body, ctype="text/html; charset=utf-8", cache=""):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self._security_headers()
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
        p = self.path.split("?", 1)[0].rstrip("/")
        if p == "/events":
            q = parse_qs(urlparse(self.path).query)
            uid = validate_jwt((q.get("token") or [""])[0])
            return bool(uid) and self._team_ok(uid, p)
        uid = self._bearer_uid()
        if not uid:
            return False
        return self._team_ok(uid, p)

    def _team_ok(self, uid, path):
        """팀 미소속 인증계정이 team=None 폴백으로 전 팀 데이터를 열람하던 격리 붕괴 차단(fail-closed).
        운영 관리자(허용목록)와 팀 없이 동작해야 하는 경로(관리자 판정·전역 참조 사전)만 예외."""
        if path in _TEAMLESS_OK_GET or is_sys_admin_user(uid, None, self._bearer_email()):
            return True
        return team_of(uid) is not None

    def do_GET(self):
        if not self._gate_get():
            self._send(401, json.dumps({"error": "로그인이 필요합니다"}, ensure_ascii=False), _JSON)
            return
        p = self.path.split("?", 1)[0]
        q = parse_qs(urlparse(self.path).query)
        for prefix in _GET_ORDER:                    # 라우트 테이블(최장 접두 우선) · 등록은 _get_route
            if p.startswith(prefix):
                fn, admin = _GET_ROUTES[prefix]
                try:                                 # do_POST 와 동일: 핸들러 예외(잘못된 쿼리값·스토어
                    if admin and not self._admin_gate():   # 일시 오류)가 무응답 연결 종료로 새지 않게 500 JSON
                        return
                    out = fn(self, q)
                    if out is not None:              # dict 반환 = 200 JSON · None = 핸들러가 직접 응답
                        self._send(200, json.dumps(out, ensure_ascii=False), _JSON)
                except Exception as e:
                    self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
                return
        if p.startswith("/vendor/"):
            self._send_vendor(p.rsplit("/", 1)[-1])
        elif p == "/favicon.ico":                    # 브라우저 기본 요청: SPA 폴스루(323KB HTML) 방지
            self._send_vendor("prism-favicon.svg")
        elif p.rstrip("/") == "/m":                  # 모바일 검수 전용(검수만 덜어낸 카드 UI)
            self._send_page(mobile=True)
        elif p.rstrip("/") in ("", "/"):
            self._send_page()
        else:                                        # 미등록 경로 404: API 오타가 SPA HTML 200 으로 가려지지 않게
            self._send(404, json.dumps({"error": "not found", "path": p[:80]},
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

    def _require_team(self):
        """supabase 모드: 로그인 + 팀 소속 필수(전 팀 데이터를 읽는 POST 라우트용).
        팀 미소속 인증계정이 team=None 폴백으로 전 팀 콘텐츠·페르소나를 열람하던 격리 붕괴 차단."""
        if not _supa():
            return True
        uid = self._bearer_uid()
        if not uid:
            self._send(401, json.dumps({"error": "로그인이 필요합니다"}, ensure_ascii=False), _JSON)
            return False
        if is_sys_admin_user(uid, None, self._bearer_email()) or team_of(uid) is not None:
            return True
        self._send(403, json.dumps({"error": "팀 소속이 필요합니다"}, ensure_ascii=False), _JSON)
        return False

    def _admin_gate(self):
        """관리자 전용 라우트 공통 게이트: supabase(운영)에서 비관리자 403 · 로컬 단독은 개방.
        (GET 라우트 테이블 admin=True 등록분이 공유 · 개별 핸들러의 게이트 복붙 제거)"""
        if _supa() and not is_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
            self._send(403, json.dumps({"error": "관리자 전용입니다"}, ensure_ascii=False), _JSON)
            return False
        return True

    def _super_gate(self):
        """슈퍼관리자 전용 라우트 공통 게이트(POST 테이블 gate='super')."""
        if _supa() and not is_super_admin_user(self._bearer_uid(), self._req_team(), self._bearer_email()):
            self._send(403, json.dumps({"error": "슈퍼관리자 전용입니다"}, ensure_ascii=False), _JSON)
            return False
        return True

    def _send_file(self, data: bytes, ctype: str, filename: str):
        """다운로드(첨부 파일) 공통 응답."""
        self.send_response(200)
        self._security_headers()                         # nosniff·HSTS 등 공통 보안 헤더(다운로드에도 무해)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_sse(self, team=None):
        """SSE 스트림: 검수 이벤트를 실시간 푸시. ThreadingHTTPServer 라 블로킹 OK.
        team 은 _g_events 가 쿼리 token 으로 해석해 넘긴다(EventSource 는 헤더 인증 불가)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")     # 프록시 버퍼링 방지
        self.end_headers()
        q = _sse_subscribe(team)                     # 구독을 구독자 팀에 묶어 교차팀 이벤트 수신 차단
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
        # 번들(app-bundle.js/css)은 디스크에 없다 — 조각을 이어붙여 만든다(assets.py).
        # path 는 캐시 사전의 키로만 쓰인다.
        bundle = assets.parts(safe)
        if ext not in self._VENDOR_CT or (bundle is None and not os.path.isfile(path)):
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
        ctype = self._VENDOR_CT[ext]
        try:                                         # (경로, mtime) 캐시: 배포 직후 접속자 수만큼
            mtime = (assets.mtime(safe) if bundle    # 반복되던 read+gzip 을 부팅당 1회로
                     else os.path.getmtime(path))    # 번들은 조각 중 최신 mtime 이 키
        except OSError:
            self._send(404, "not found")
            return
        hit = self._VENDOR_CACHE.get(path)
        if not hit or hit[0] != mtime:
            if bundle:
                raw = assets.build(safe)
            else:
                with open(path, "rb") as f:
                    raw = f.read()
            gz = (gzip.compress(raw, 6)
                  if len(raw) > 1024 and any(t in ctype for t in self._GZIP_CT) else None)
            hit = (mtime, raw, gz)
            self._VENDOR_CACHE[path] = hit
        self._send_prezipped(200, hit[1], hit[2], ctype, cache=cache)

    def do_POST(self):
        # 본문 크기 상한: 인증·라우팅보다 먼저 실행되는 read 가 무제한이면 프리-어스 메모리 DoS
        # (스레드당 증폭). 비수치 Content-Length 는 400, 초과는 413. 거부 시 연결을 닫아 잔여 본문
        # 재해석 방지.
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            self.close_connection = True
            self._send(400, json.dumps({"error": "잘못된 Content-Length"}, ensure_ascii=False), _JSON)
            return
        if length < 0 or length > _MAX_BODY:
            self.close_connection = True
            self._send(413, json.dumps({"error": f"요청 본문이 너무 큽니다(상한 {_MAX_BODY // (1024 * 1024)}MB)"},
                                       ensure_ascii=False), _JSON)
            return
        body = self.rfile.read(length)

        # 메뉴별 권한(생성자 설정) 백엔드 강제: 숨긴 메뉴의 액션은 서버가 차단(프론트 숨김만으론 보안 아님)
        _menu = _menu_for_path(self.path)
        if _menu and not menu_allowed(self._bearer_uid(), self._req_team(), self._bearer_email(), _menu):
            self._send(403, json.dumps({"error": "이 메뉴에 대한 권한이 없습니다"}, ensure_ascii=False), _JSON)
            return

        p = self.path.split("?", 1)[0]
        for prefix in _POST_ORDER:                   # 라우트 테이블(최장 접두 우선) · 등록은 _post_route
            if p.startswith(prefix):
                fn, gate = _POST_ROUTES[prefix]
                try:
                    if gate == "admin" and not self._admin_gate():
                        return
                    if gate == "super" and not self._super_gate():
                        return
                    if gate == "login" and not self._require_login():
                        return
                    if gate == "team" and not self._require_team():
                        return
                    out = fn(self, body)
                    if out is not None:              # dict 반환 = 200 JSON · None = 핸들러가 직접 응답
                        self._send(200, json.dumps(out, ensure_ascii=False), _JSON)
                except Exception as e:
                    self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
                return
        self._send(404, "not found")


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


_PAGE_BYTES = {}


def _page_payload(mobile=False):
    """(raw, gzip) 페이지 바이트 · 부팅당 1회 생성(617KB HTML 의 요청당 재압축 7ms 제거)."""
    key = "m" if mobile else "d"
    hit = _PAGE_BYTES.get(key)
    if not hit:
        raw = (_mpage_versioned() if mobile else _page_versioned()).encode("utf-8")
        hit = (raw, gzip.compress(raw, 6))
        _PAGE_BYTES[key] = hit
    return hit



def main():
    ap = argparse.ArgumentParser(description="Prism 로컬 UI")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--mock", action="store_true", help="키가 있어도 강제 mock")
    a = ap.parse_args()

    Handler.server_mock = a.mock
    load_persisted_key()                              # 저장된 키 파일 있으면 주입
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
    start_topic_scheduler()                            # 토픽 자동 리프레시 + 성과 스냅샷(1시간)
    AO.start_retention_scheduler()                     # 보존 기한 정리 · PRISM_RETENTION_DAYS 설정 시에만
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
