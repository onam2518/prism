"""실행 파이프라인 도메인 (serve 에서 분리 · 라우트 분리 4차 · 로드맵 2단계 3차).

콘텐츠 추가(add_contents)·단건 실행(run_pipeline)·일괄/재실행(run_batch·rerun_*)·
적재 훅(store_save: 초안 스냅샷·엔티티 사전 연동)·입력 템플릿(build_template_*)을 담당.
HTTP 디스패치는 serve 가 유지.

컴포지션: 서버 환경(스토어·LLM 라우팅·집계 캐시·비용/실패 롤업 훅·mock 플래그)은
serve 가 기동 시 `_SV` 로 주입(learnops 관례). 테스트가 serve.rerun_content 를
몽키패치하므로 rerun_all 등의 내부 호출도 `_SV.` 경유가 계약.
"""
from __future__ import annotations

import os
import re
import tempfile
import threading
import time

from . import imagext as IMG
from . import pipeline as PIPE
from .config import Config
from .schema import normalize_image_urls

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입


def _run_id() -> str:
    return "run-" + str(int(time.time() * 1000))


_BUILD_MAJOR = 1                # 큰 마일스톤에 수동 증가 · 마이너 = 배포 실행 번호(CI 자동)


def _build_id() -> str:
    """빌드 버전. 운영 = v{major}.{배포 실행 번호}(CI 가 PRISM_BUILD_NO 로 구움 · 자동 증가).
    로컬/개발(번호 없음)은 dev + 파일 수정시각으로 구분한다."""
    no = (os.environ.get("PRISM_BUILD_NO") or "").strip()
    if no.isdigit():
        return f"v{_BUILD_MAJOR}.{no}"
    try:
        return "dev " + time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(__file__)))
    except Exception:
        return "dev"


def img_coverage(contents) -> dict:
    """인입 묶음의 참조 이미지 URL 적재율(게시판 #9 · 조용한 유실 관측).

    운영 400건이 전부 image_urls 빈 목록이었는데도 아무 신호가 없었다(재실행 경로가
    []로 덮어쓰는 결함 · 2026-08-03). 인입 건수 옆에 '이미지 N/M건'을 같이 남겨
    0건이면 실행 큐에서 바로 보이게 한다. 반환 {n, with_images}."""
    rows = contents or []
    return {"n": len(rows), "with_images": sum(1 for c in rows if (c or {}).get("image_urls"))}


def _img_note(contents) -> str:
    """실행 큐 메시지 꼬리표. 건수가 0이면 붙이지 않는다."""
    s = img_coverage(contents)
    return f" · 이미지 {s['with_images']}/{s['n']}건" if s["n"] else ""


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
    if new_ids and not _SV.Handler.server_mock and os.environ.get("PRISM_ENTDICT_ENRICH", "1") == "1":
        threading.Thread(target=ED.enrich_many, args=(st, new_ids), daemon=True).start()


def store_save(pairs, source: str = "단건", team=None):
    """[(content, out), …] 를 영속 저장(+_SV._LAST_RESULTS 미러). source: 출처. team: 소속 팀(supabase).
    적재 정책(dedup): 동일 콘텐츠 + 결과 무변경이면 적재 제외(skip), 변경 시 갱신, 신규는 추가."""
    outs = [o for _, o in pairs]
    _SV._LAST_RESULTS[:] = outs
    _SV._agg_bump()                          # 결과 변경 → 집계 캐시 무효화
    st = _SV.get_store()
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


def add_contents(contents: list, purpose: str = "", team=None, source: str = "단건") -> dict:
    """STEP 1 콘텐츠 추가: 저장만 하고 모델은 돌리지 않는다(미실행 대기).
    실행은 STEP 2 모델 실행(일괄 실행 큐 · scope=pending)이 담당.
    이미 저장된 콘텐츠(동일 hash)는 건드리지 않고 '기존'으로만 집계한다 — 누적 마스터
    파일을 통째로 재업로드하는 운영을 지원(신규만 추가 · 기존은 재실행 대상이 안 된다).
    (종전엔 재추가가 빈 메타로 upsert 되어 기존 실행 결과가 미실행으로 되돌아갔고,
    STEP 2가 그걸 다시 실행해 이중 과금됐다 · 운영 2026-08-05 중복 배치 200건.)"""
    rows = [c for c in contents
            if (c.get("title") or "").strip() or (c.get("body") or "").strip()]
    if not rows:
        return {"error": "제목·본문이 비어 있습니다"}
    from .store import content_hash as _chash
    uniq = {}                                     # 파일 내 완전 중복 행은 1건으로(추가 건수 정확)
    for c in rows:
        uniq[_chash(c)] = c
    dropped = len(rows) - len(uniq)
    known = {}
    st0 = _SV.get_store()
    if st0 is not None and hasattr(st0, "existing_hashes"):
        try:
            known = st0.existing_hashes(list(uniq.keys()), team=team) or {}
        except Exception:
            known = {}    # 조회 실패 → 전량 신규 취급(upsert 멱등 · 빈 결과는 save_dedup 가드가 보호)
    existing = sum(1 for h in uniq if h in known)
    # 참조 이미지 URL 정규화(http(s)·중복 제거·상한)를 인입 경로 공통으로. 원본 dict 는 건드리지 않는다.
    rows = [dict(c, image_urls=normalize_image_urls(c.get("image_urls") or c.get("images")))
            for h, c in uniq.items() if h not in known]
    pairs = [(c, {"content_ref": {"displayServiceName": c.get("displayServiceName", ""),
                                  "title": c.get("title", ""), "subtitle": c.get("subtitle", ""),
                                  "source_url": _SV._safe_url(c.get("source_url", "") or c.get("url", "")),
                                  # 참조 이미지 URL(게시판 #9) · sqlite 는 payload.content_ref 가 유일한
                                  # 보존처라 여기서 빠지면 STEP 1 추가분의 이미지가 사라진다
                                  "image_urls": list(c.get("image_urls") or []),
                                  "body": c.get("body", ""), "body_hash": _chash(c)},
                  "quality_meta": {}, "item_meta": {}, "trace": {}}) for c in rows]
    saved = store_save(pairs, source=source, team=team) if rows else None
    if isinstance(saved, dict) and saved.get("error"):   # 저장 실패면 '추가됨'으로 속이지 않는다
        return {"error": "저장 실패 · 다시 시도하세요 (" + saved["error"][:120] + ")"}
    msg = f"신규 {len(rows)}건 추가"
    if existing:
        msg += f" · 기존 {existing}건 유지(재실행 안 함)"
    msg += (" · 미실행 대기(STEP 2에서 실행)" if rows else " · 모두 이미 등록된 콘텐츠") + _img_note(rows)
    jid = "add:" + time.strftime("%H%M%S")               # 실행 이력에 추가 기록(클릭 -> 해당 콘텐츠)
    with _SV._INGEST_LOCK:                                   # 키 삽입은 상태 순회와 레이스 · 락 필수
        _SV._INGEST_STATE[jid] = {"name": source, "endpoint": "", "kind": "콘텐츠 추가", "started": time.time(),
                              "running": False, "total": len(uniq), "done": len(uniq), "failed": 0,
                              "last_run": time.time(),
                              "last_msg": msg,
                              "last_ok": True, "trigger": "manual", "hashes": list(uniq.keys())}
    if (purpose or "") == "eval" and rows:               # 용도 지정은 신규만(기존 행 용도 불변)
        try:
            stp = _SV.get_store()
            if stp and hasattr(stp, "set_purpose"):
                stp.set_purpose([_chash(c) for c in rows], "eval", team=team)
        except Exception:
            pass
    return {"ok": True, "added": len(rows), "existing": existing, "pending": True,
            "with_images": img_coverage(rows)["with_images"],   # 이미지 유실 관측(게시판 #9)
            **({"duplicates": dropped} if dropped else {})}


# 비재시도성 콜 실패(ratelimit.classify_http_error 분류): 크레딧 소진(billing)·프로젝트
# 지출 한도(quota)·인증(auth)은 재시도·재실행해도 계속 실패한다(실측: 2026-07-29 402 832건).
# 폴백 모델도 같은 키·같은 402 로 죽으므로, 감지 즉시 멈추는 게 비용·시간 모두 이득.
_NONRETRY_KINDS = ("billing", "quota", "auth")
_NONRETRY_LABEL = {"billing": "크레딧 부족", "quota": "프로젝트 지출 한도",
                   "auth": "인증 오류"}          # 실패 원장 화면(failKindKr)과 같은 표기


def _nonretry_kinds(out: dict) -> list:
    """산출 trace.fails 중 비재시도성 실패 종류만 추린다(중복 제거·정렬). 없으면 빈 목록."""
    fails = ((out or {}).get("trace") or {}).get("fails") or []
    return sorted({str((f or {}).get("kind") or "") for f in fails
                   if str((f or {}).get("kind") or "") in _NONRETRY_KINDS})


def _log_run_ledgers(content: dict, out: dict, *, mock: bool, team=None, content_hash: str = ""):
    """실호출 1건을 비용·실패 원장에 기록(mock 무기록). run_pipeline 뿐 아니라 엑셀 일괄
    추출(run_batch)·자동 인입(ingestops.ingest_run_source)도 이 훅을 타야 일별 비용 롤업·
    실패 트리아지(AL.on_cost/on_fail 임계 알림 포함)가 실키 지출을 빠짐없이 본다 —
    종전엔 두 경로가 PIPE.extract 를 직접 불러 402 폭주도 원장에 한 건도 안 남았다."""
    if mock:
        return
    trace = (out or {}).get("trace") or {}
    _SV._log_cost_rollup(trace, team=team)       # 비용 원장: 일별×모델×콜 누적(실호출만)
    # 실패 원장: content_hash 가 있으면 '최근 실패 콘텐츠' 목록도 관리(실패 등재·성공 해소)
    _SV._log_fail_rollup(trace, service=(content or {}).get("displayServiceName", ""),
                         team=team, content_hash=content_hash,
                         title=(content or {}).get("title", ""))


def run_pipeline(fields: dict, *, mock: bool, team=None, model: str = "", persist: bool = True,
                 vision=None, batch_seq=None) -> dict:
    cfg = Config.load()
    if (model or "").strip():                # 모델 지정 재실행: 제공자·키를 모델에 맞게 라우팅
        llm, _route = _SV.llm_for_model(model.strip(), mock)
        if llm is None:
            return {"error": f"모델 호출 불가({_route}): {model}"}
    else:
        llm = _SV.make_text_llm(cfg, mock)       # 텍스트 슬롯(solar|router). 무키면 내부서 mock

    # 업로드 순서(image0, image1, …) = 가중치 순서. 첫 장이 대표.
    # 참조 이미지 URL(게시판 #9 · 추출 입력 아님 · 해시 불포함). 업로드 파일(image0…)과 달리
    # 문자열/목록이며, 여기서 content 에 실어 주지 않으면 저장 계층(supastore.sync_contents 는
    # out.content_ref 가 아니라 content 를 읽는다)이 image_urls 를 [] 로 덮어써 수집 이미지가 사라진다.
    ref_images = normalize_image_urls(fields.get("image_urls") or fields.get("images"))
    imgs = [(k, v) for k, v in fields.items()
            if isinstance(v, dict) and v.get("bytes") and k.startswith("image")]
    imgs.sort(key=lambda kv: int(re.sub(r"\D", "", kv[0]) or 0))
    images = [v for _, v in imgs]
    source = "text"
    signals = []
    vision_used = None
    if images:
        source = "image"
        images, dropped = IMG.cap_images(images)   # 장수 상한(비전 호출 전)
        signals = IMG.extract_signals(images, mock=llm.mock, vision=vision)
        _vp, _vm = vision if vision else IMG._vision_cfg()   # 실제 요청 슬롯(UI 표기용 · 순수사진 폴백은 signals note)
        vision_used = {"provider": _vp, "model": _vm}
        content = IMG.build_content(
            signals,
            displayServiceName=fields.get("displayServiceName", "포토"),
            title=fields.get("title", ""),
            caption=fields.get("caption", ""),
            text_body=fields.get("body", ""),
            dropped=dropped,
        )
        _su = fields.get("source_url", "") or fields.get("url", "")
        if _su:                                   # 원문 링크(참조 · 정체성 해시 불변)
            content["source_url"] = _su
        if ref_images:
            content["image_urls"] = ref_images
    else:
        content = {
            "displayServiceName": fields.get("displayServiceName", ""),
            "title": fields.get("title", ""),
            "subtitle": fields.get("subtitle", ""),
            "body": fields.get("body", ""),
            # 참조용 원문 링크 · 해시(서비스+제목+부제+본문) 불포함이라 정체성 무변
            "source_url": fields.get("source_url", "") or fields.get("url", ""),
            "image_urls": ref_images,             # 참조용 이미지 URL · 위와 같은 참조 패턴
        }

    out = PIPE.extract(content, llm, legal=cfg.legal_enabled)
    # 폴백 체인: 실호출인데 산출이 전량 빈값이면 예비 모델로 1회씩 재시도(최대 3 · 성공 시 채택).
    # 단 비재시도성 실패(크레딧 소진·지출 한도·인증)면 예비 모델도 같은 402/401 로 죽는다 —
    # 무의미한 호출 증폭(건당 최대 3배)을 막기 위해 폴백을 생략한다.
    if not llm.mock and _SV._pipeline_empty(out) and not _nonretry_kinds(out):
        primary = ((getattr(llm, "model", "") or "").strip()
                   or (model or "").strip() or (cfg.model or ""))
        for fm in [str(m).strip() for m in (getattr(cfg, "fallback_models", None) or [])][:3]:
            if not fm or fm == primary:
                continue
            fllm, _route = _SV.llm_for_model(fm, mock)
            if fllm is None or fllm.mock:
                continue
            retry = PIPE.extract(content, fllm, legal=cfg.legal_enabled)
            if not _SV._pipeline_empty(retry):
                (retry.setdefault("trace", {}))["fallback_from"] = primary or "(기본)"
                out = retry
                break
    try:                                         # 초안 버전 = 학습 반영 회차 + 1
        # batch_seq: 일괄 실행(rerun_all)이 시작 시 1회 조회해 건별로 주입 — 건마다
        # store_save 의 _agg_bump 가 캐시를 무효화해 매건 events 재조회하던 것을 방지.
        seq = int(batch_seq) if batch_seq is not None else _SV._batch_seq_cached(team)
        (out.setdefault("trace", {}))["version"] = seq + 1
    except Exception:
        pass
    # 비용·실패 원장(실호출만 · 실험 포함). 실패 원장의 콘텐츠 식별은 영속 실행만 전달:
    # 실패 → 최근 실패 목록 등재(개별 재실행 대상) · 무실패 성공 → 목록에서 해소.
    # 실험(persist=False)은 저장이 없어 재실행 불가라 식별 제외.
    from .store import content_hash as _chash
    _log_run_ledgers(content, out, mock=llm.mock, team=team,
                     content_hash=(_chash(content) if persist else ""))
    if not persist:                              # 실험(미저장): 추출만 하고 results·초안·홀드아웃 미기록
        return {"source": source, "mock": llm.mock, "content": content,
                "signals": signals, "output": out, "vision_used": vision_used}
    store_save([(content, out)], team=team)      # 영속 저장(+미러, 팀 태깅)
    if (fields.get("purpose") or "") == "eval":  # 평가용 지정: 검수 대상에서 제외(홀드아웃)
        try:
            from .store import content_hash as _chash
            stp = _SV.get_store()
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
        "vision_used": vision_used,
    }


SELECTED_MAX = 500              # 선택 실행 1회 상한(요청 본문·실행 시간 폭주 방지)
PENDING_MAX = 2000              # '미실행만' 1회 상한 — 인입 경로(엑셀 N개·수동) 무관 합산 실행.
                                # 미실행은 전부 운영자가 명시적으로 추가한 것이라 전량 실행이 의도다
                                # (전체 재실행 all 은 200 유지 · 실수로 전량 재과금 방지)
BATCH_ADD_MAX = 2000            # 엑셀 추가(STEP 1) 1회 상한 — 누적 마스터 파일 재업로드 수용
                                # (종전 200 은 '추출 실행' 비용 가드가 추가 경로까지 묶은 것)


def rerun_all(model: str, team=None, limit: int = 200, scope: str = "all",
              hashes=None, force_quest: bool = False) -> dict:
    """모아진 콘텐츠를 지정 모델로 일괄 실행(수동 · 관리자). 건당 비용 발생.
    scope: pending=미실행(STEP 1 추가 대기)만 · all=전체 재실행 ·
           selected=hashes 로 지정한 건만(콘텐츠 관리 표에서 다중 선택).
    퀘스트 진행 중에는 전체 재실행 차단(검수 중 초안이 바뀌면 판정·합의가 오염된다).
    선택 실행은 개별 재실행과 같은 규약 — 확인 모달을 거친 force_quest 로만 강행한다."""
    picked = [h for h in dict.fromkeys(hashes or []) if h]
    if picked:
        scope = "selected"
        limit = min(len(picked), SELECTED_MAX)   # 선택분은 '선택한 만큼' 실행(창 상한과 무관)
    if scope == "all" and _SV.quest_active():
        return {"error": "퀘스트 진행 중에는 전체 재실행이 차단됩니다(검수 중 초안 교체 방지) · "
                         "'미실행만'은 가능하며, 반영 후 실행하거나 검수 목표 카드에서 일시를 비워 목표를 해제하세요"}
    if scope == "selected" and not picked:
        return {"error": "선택된 콘텐츠가 없습니다"}
    if scope == "selected" and _SV.quest_active() and not force_quest:
        return {"error": "퀘스트 진행 중에는 검수 중 콘텐츠의 초안 재실행이 차단됩니다 · "
                         "반영 후 실행하거나 검수 목표 카드에서 목표를 해제하세요"}
    # limit 은 '창'이 아니라 '한 번에 실행할 최대 건수'다. 예전엔 rows[-limit:] 로 먼저 잘라
    # 그 안에서 미실행을 찾았는데, 결과 뷰가 최신순이라 잘린 창은 '가장 오래된 200건'이었다.
    # 그래서 콘텐츠가 200건을 넘으면 방금 올린 미실행분이 창 밖으로 밀려 영영 실행되지 않았다
    # (2026-07-28 운영: 600건 중 미실행 200건이 '미실행만 0건'으로 보이고 실행 불가).
    # 먼저 대상을 고르고 그다음에 상한을 적용한다 · 최신순으로 채워 방금 올린 것부터 처리.
    rows = _SV.results_rows(team=team)
    want = set(picked)
    targets, seen, row_by_hash = [], set(), {}
    for r in rows:
        if scope == "pending" and not _SV._is_pending_row(r):
            continue                                 # 이미 실행된 건 제외
        ch = _SV._row_key(r.get("content_ref") or {})
        if scope == "selected" and ch not in want:
            continue                                 # 표에서 고른 것만
        if ch and ch not in seen:
            seen.add(ch)
            targets.append(ch)
            row_by_hash[ch] = r                      # 1회 로드분 재사용 · 건마다 전체 재조회(N×5000) 방지
            if len(targets) >= int(limit):
                break
    if not targets:
        return {"ok": True, "done": 0, "failed": 0, "model": model, "scope": scope,
                "msg": "대상이 없습니다"
                       + (" (미실행 콘텐츠 없음)" if scope == "pending" else "")
                       + (" (선택한 콘텐츠를 찾지 못했습니다 · 삭제되었을 수 있음)"
                          if scope == "selected" else "")}
    # 선택분이 상한을 넘으면 조용히 자르지 않고 응답에 남긴다(잘린 줄 모르고 '다 돌았다'고 읽는 것 방지)
    over_cap = max(0, len(picked) - len(targets)) if scope == "selected" else 0
    done = failed = 0
    spent = 0.0
    budget = float(getattr(Config.load(), "batch_budget_usd", 0.0) or 0.0)   # 0 = 무제한
    budget_stop = False
    halt_kinds = []                 # 비재시도성 실패(크레딧·한도·인증) 감지 시 조기 중단 사유
    try:
        # 학습 반영 회차는 배치 시작 시 1회만 조회해 건별로 전달 — 건마다 store_save 의
        # _agg_bump 가 전역 캐시를 무효화해 _batch_seq_cached 가 매건 미스나면서
        # events 를 재조회(supabase 는 건당 GET 1만 행)하던 것을 막는다.
        batch_seq = _SV._batch_seq_cached(team)
    except Exception:
        batch_seq = None                             # 조회 실패 시 건별 폴백(종전 동작)
    jid = "rerun:" + time.strftime("%H%M%S")         # 실행 큐 등록(진행률·ETA)
    _SV._job_begin(jid, model or "기본 모델",
                   "선택 실행" if scope == "selected" else "일괄 실행", len(targets))
    _SV._INGEST_STATE[jid]["hashes"] = list(targets)     # 작업 클릭 -> 결과 콘텐츠 보기
    try:
        for ch in targets:
            res = _SV.rerun_content(ch, model, team=team, row=row_by_hash.get(ch),
                                    force_quest=force_quest, register_job=False,
                                    batch_seq=batch_seq)
            # 크레딧 소진·지출 한도·인증 실패는 재실행해도 계속 실패한다 — 이 건은 실패로
            # 집계하고 남은 대상은 즉시 중단한다(수천 회 무의미한 402 호출 방지).
            halt_kinds = [] if res.get("error") else _nonretry_kinds(res.get("output") or {})
            # 실패 건도 이미 지불한 비용이 있다(재시도로 버린 200 응답 · llm._fail 이 실어 준다).
            # 종전에는 성공 분기에서만 더해서, 실패가 많은 배치가 예산 상한을 그대로 뚫었다.
            spent += float((((res.get("output") or {}).get("trace") or {}).get("cost_usd")) or 0.0)
            if res.get("error") or halt_kinds:
                failed += 1
                _SV._INGEST_STATE[jid]["failed"] = failed
            else:
                done += 1
            _SV._INGEST_STATE[jid]["done"] += 1
            if halt_kinds:
                break
            if budget > 0 and spent >= budget:       # 예산 상한: 도달 시 남은 대상 중단(비용 통제)
                budget_stop = True
                break
    except Exception as e:
        _SV._job_end(jid, False, f"{done}건 실행 후 중단 · {str(e)[:80]}")
        raise
    halt_msg = ""
    if halt_kinds:
        labels = "·".join(_NONRETRY_LABEL.get(k, k) for k in halt_kinds)
        left = len(targets) - done - failed
        halt_msg = (f"{labels} 감지 · 재실행해도 계속 실패해 {done}건 실행 후 중단"
                    + (f" · 남은 {left}건 미실행" if left else "") + " · 조치 후 다시 실행하세요")
        _SV._job_end(jid, False, halt_msg)
    elif budget_stop:
        _SV._job_end(jid, False, f"예산 상한 ${budget:g} 도달 · {done}건 실행(${spent:.4f}) 후 중단"
                             + (f" · 실패 {failed}" if failed else ""))
    else:
        _SV._job_end(jid, failed == 0, f"{done}건 실행" + (f" · 실패 {failed}" if failed else " 완료")
                                       + (f" · 상한 초과 {over_cap}건 제외" if over_cap else ""))
    return {"ok": True, "done": done, "failed": failed, "model": model, "scope": scope,
            "spent_usd": round(spent, 6), "budget_stop": budget_stop,
            "halt_kinds": halt_kinds,
            **({"msg": halt_msg} if halt_msg else {}),
            "over_cap": over_cap,
            "skipped": (len(targets) - done - failed) if (budget_stop or halt_kinds) else 0}


def rerun_content(content_hash: str, model: str, team=None, row=None, force_quest: bool = False,
                  register_job: bool = True, batch_seq=None) -> dict:
    """같은 콘텐츠를 지정 모델로 재실행(초안 재생성 · 관리자). 기존 초안은 덮어쓰되
    이전 초안을 patch_log 에 남겨(rerun:구모델) 이력·비교 근거를 보존한다.
    row: 일괄 실행(rerun_all)이 미리 로드한 행 주입 — 건마다 전체 테이블 재조회 방지.
    register_job: 실행 큐에 이 건을 등록(기본). 일괄·선택 실행은 배치 잡을 이미 열었으므로
    False 로 불러 건마다 잡이 쌓이지 않게 한다.
    batch_seq: 일괄 실행이 시작 시 1회 조회한 학습 반영 회차 주입 — 건마다 재조회 방지."""
    st = _SV.get_store()
    ch = (content_hash or "").strip()
    if not (st and ch):
        return {"error": "콘텐츠를 찾을 수 없습니다"}
    if row is None:
        for r in _SV.results_rows(team=team):
            if _SV._row_key(r.get("content_ref") or {}) == ch:
                row = r
                break
    if not row:
        return {"error": "콘텐츠를 찾을 수 없습니다(본문 미보존 항목일 수 있음)"}
    ref = row.get("content_ref") or {}
    fields = {"displayServiceName": ref.get("displayServiceName", ""), "title": ref.get("title", ""),
              "subtitle": ref.get("subtitle", ""), "body": ref.get("body", ""),
              "source_url": ref.get("source_url", ""),   # 재실행 upsert 가 원문 링크를 지우지 않게 보존
              # 이미지 URL도 동일하게 되실어야 한다(게시판 #9). 빠뜨리면 STEP 2 모델 실행(=일괄
              # 재실행)이 STEP 1 에서 들어온 수집 이미지를 매번 [] 로 덮어썼다 —
              # 운영 400건이 전부 source='재실행' · image_urls 빈 목록이던 원인(2026-08-03).
              "image_urls": list(ref.get("image_urls") or [])}
    if _SV.quest_active() and not _SV._is_pending_row(row) and not force_quest:
        return {"error": "퀘스트 진행 중에는 검수 중 콘텐츠의 초안 재실행이 차단됩니다 · "
                         "반영 후 실행하거나 검수 목표 카드에서 목표를 해제하세요"}
    old_model = (row.get("trace") or {}).get("model", "") or ""
    # 개별 재실행도 실행 큐에 남긴다 — 일괄·선택 실행만 보이고 건별 실행은 흔적이 없어
    # '눌렀는데 돌긴 한 건가'를 확인할 방법이 없었다. 게이트를 통과한 뒤에만 등록해
    # 차단된 시도로 큐가 지저분해지지 않게 한다.
    jid = ""
    if register_job:
        jid = "rerun1:" + time.strftime("%H%M%S") + ":" + ch[:6]
        _SV._job_begin(jid, (ref.get("title") or "콘텐츠")[:40], "개별 재실행", 1)
        _SV._INGEST_STATE[jid]["hashes"] = [ch]      # 작업 클릭 -> 이 콘텐츠 보기
    result = run_pipeline(fields, mock=_SV.Handler.server_mock, team=team, model=model,
                          batch_seq=batch_seq)
    if result.get("error"):
        if jid:
            _SV._job_end(jid, False, "실패 · " + str(result.get("error"))[:80])
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
    _SV._agg_bump()
    if jid:
        cost = float(((result.get("output") or {}).get("trace") or {}).get("cost_usd") or 0.0)
        _SV._INGEST_STATE[jid]["done"] = 1
        _SV._job_end(jid, True, "1건 실행 완료" + (f" · ${cost:.4f}" if cost else ""))
    return result


def build_template_csv() -> bytes:
    """엑셀 일괄 입력용 CSV 템플릿(UTF-8 BOM → Excel 한글 정상). 헤더+예시 2행.

    헤더는 ingest 별칭과 일치: 콘텐츠 그룹·제목·부제·본문·원문 링크·이미지 URL.
    제목·본문이 필수(원문 링크·이미지 URL 은 선택 · 이미지는 쉼표로 여러 개, 게시판 #9).
    """
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["콘텐츠 그룹", "제목", "부제", "본문", "원문 링크", "이미지 URL"])
    w.writerow(["뉴스", "삼성전자 노조 임금 협상 결렬",
                "중앙노동위 조정 불성립",
                "삼성전자가 중앙노동위원회 조정에서 노조와 합의에 이르지 못했다. 양측은 임금 인상폭을 두고 이견을 좁히지 못했다.",
                "https://v.daum.net/v/20260101000000000",
                "https://img1.daumcdn.net/example/photo1.jpg"])
    w.writerow(["스포츠", "손흥민 시즌 10호골",
                "",
                "토트넘이 홈 경기에서 승리했다. 손흥민이 후반 결승골을 터뜨리며 시즌 10호골을 기록했다.",
                "", ""])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def build_template_xlsx() -> bytes:
    """엑셀 일괄 입력용 .xlsx 템플릿(의존성 0: zipfile+xml, inline string).
    헤더·예시는 CSV 템플릿과 동일 · ingest._read_xlsx 와 왕복 호환."""
    import io
    import zipfile
    from xml.sax.saxutils import escape

    rows = [["콘텐츠 그룹", "제목", "부제", "본문", "원문 링크", "이미지 URL"],
            ["뉴스", "삼성전자 노조 임금 협상 결렬", "중앙노동위 조정 불성립",
             "삼성전자가 중앙노동위원회 조정에서 노조와 합의에 이르지 못했다. 양측은 임금 인상폭을 두고 이견을 좁히지 못했다.",
             "https://v.daum.net/v/20260101000000000",
             "https://img1.daumcdn.net/example/photo1.jpg"],
            ["스포츠", "손흥민 시즌 10호골", "",
             "토트넘이 홈 경기에서 승리했다. 손흥민이 후반 결승골을 터뜨리며 시즌 10호골을 기록했다.",
             "", ""]]

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


def run_batch(file_bytes: bytes, filename: str, purpose: str = "", team=None,
              add_only: bool = False) -> dict:
    """엑셀/CSV 업로드 → ingest 매핑 → (add_only=추가만 | 행마다 추출 → 결과+리포트)."""
    from . import ingest as ING
    ext = os.path.splitext(filename or "")[1].lower() or ".xlsx"
    cfg = Config.load()
    llm = _SV.make_text_llm(cfg, _SV.Handler.server_mock)
    fd, tmp = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file_bytes)
        a = ING.assess(tmp)
        if not a["ok"]:
            return {"error": a["reason"], "headers": a.get("headers", [])}
        contents = ING.to_contents(tmp)
        truncated = max(0, len(contents) - BATCH_ADD_MAX)
        contents = contents[:BATCH_ADD_MAX]
        if add_only:                                 # STEP 1 = 추가만(모델 미실행 · 즉시 완료)
            r = add_contents(contents, purpose=purpose, team=team, source="배치")
            return {**r, "source": "excel", "count": r.get("added", 0), "mapping": a["mapping"],
                    **({"truncated": truncated} if truncated else {})}
        from .store import content_hash as _bch
        # 추출 실행도 누적 파일 재업로드에 안전하게: 이미 실행 완료된 기존 행은 건너뛴다
        # (신규 + 기존-미실행만 실행 · 재업로드가 곧 재과금이 되지 않게). 실행 상한은 종전 200 유지.
        known = {}
        st0 = _SV.get_store()
        if st0 is not None and hasattr(st0, "existing_hashes"):
            try:
                known = st0.existing_hashes([_bch(c) for c in contents], team=team) or {}
            except Exception:
                known = {}
        skipped_done = sum(1 for c in contents if known.get(_bch(c)))
        contents = [c for c in contents if not known.get(_bch(c))][:200]
        results, items, pairs = [], [], []
        jid = "batch:" + time.strftime("%H%M%S")     # 실행 큐 등록(진행률·ETA)
        _SV._job_begin(jid, (filename or "엑셀"), "엑셀 일괄 추출", len(contents))
        _SV._INGEST_STATE[jid]["hashes"] = [_bch(c) for c in contents]
        try:
            for c in contents:
                out = PIPE.extract(c, llm, legal=cfg.legal_enabled)
                # 비용·실패 원장: 이 경로는 run_pipeline 을 안 타므로 여기서 직접 기록 —
                # 종전엔 엑셀 일괄 추출의 실키 지출·402 실패가 원장에 한 건도 안 남았다.
                _log_run_ledgers(c, out, mock=llm.mock, team=team, content_hash=_bch(c))
                results.append(out)
                pairs.append((c, out))
                im = out.get("item_meta") or {}
                items.append({"title": (c.get("title") or "")[:80],
                              "summary": im.get("summary", ""),
                              "entities": im.get("entities", []),
                              "intent": im.get("intent", []),
                              "grade": (out.get("quality_meta") or {}).get("finalGrade", "")})
                _SV._INGEST_STATE[jid]["done"] += 1
        except Exception as e:
            _SV._job_end(jid, False, f"{len(results)}건 추출 후 중단 · {str(e)[:80]}")
            raise
        if pairs:
            store_save(pairs, source="배치", team=team)  # 영속 저장(단일 트랜잭션 배치)
        skip_note = f" · 기존 실행완료 {skipped_done}건 건너뜀" if skipped_done else ""
        _SV._job_end(jid, True, f"{len(results)}건 추출 · 저장 완료" + skip_note + _img_note(contents))
        if (purpose or "") == "eval":               # 평가용 지정: 검수 대상에서 제외(홀드아웃)
            try:
                from .store import content_hash as _chash
                stp = _SV.get_store()
                if stp and hasattr(stp, "set_purpose"):
                    stp.set_purpose([_chash(c) for c, _ in pairs], "eval", team=team)
            except Exception:
                pass
        return {"source": "excel", "mock": llm.mock, "count": len(results),
                "mapping": a["mapping"], "items": items,
                **({"skipped_done": skipped_done} if skipped_done else {}),
                "with_images": img_coverage(contents)["with_images"]}   # 이미지 유실 관측(게시판 #9)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
