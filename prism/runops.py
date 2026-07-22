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
                                  "source_url": _SV._safe_url(c.get("source_url", "") or c.get("url", "")),
                                  "body": c.get("body", ""), "body_hash": _chash(c)},
                  "quality_meta": {}, "item_meta": {}, "trace": {}}) for c in rows]
    saved = store_save(pairs, source=source, team=team)
    if isinstance(saved, dict) and saved.get("error"):   # 저장 실패면 '추가됨'으로 속이지 않는다
        return {"error": "저장 실패 · 다시 시도하세요 (" + saved["error"][:120] + ")"}
    jid = "add:" + time.strftime("%H%M%S")               # 실행 이력에 추가 기록(클릭 -> 해당 콘텐츠)
    with _SV._INGEST_LOCK:                                   # 키 삽입은 상태 순회와 레이스 · 락 필수
        _SV._INGEST_STATE[jid] = {"name": source, "endpoint": "", "kind": "콘텐츠 추가", "started": time.time(),
                              "running": False, "total": len(rows), "done": len(rows), "failed": 0,
                              "last_run": time.time(), "last_msg": f"{len(rows)}건 추가 · 미실행 대기(STEP 2에서 실행)",
                              "last_ok": True, "trigger": "manual", "hashes": [_chash(c) for c in rows]}
    if (purpose or "") == "eval":
        try:
            from .store import content_hash as _chash
            stp = _SV.get_store()
            if stp and hasattr(stp, "set_purpose"):
                stp.set_purpose([_chash(c) for c in rows], "eval", team=team)
        except Exception:
            pass
    return {"ok": True, "added": len(rows), "pending": True,
            **({"duplicates": dropped} if dropped else {})}


def run_pipeline(fields: dict, *, mock: bool, team=None, model: str = "", persist: bool = True) -> dict:
    cfg = Config.load()
    if (model or "").strip():                # 모델 지정 재실행: 제공자·키를 모델에 맞게 라우팅
        llm, _route = _SV.llm_for_model(model.strip(), mock)
        if llm is None:
            return {"error": f"모델 호출 불가({_route}): {model}"}
    else:
        llm = _SV.make_text_llm(cfg, mock)       # 텍스트 슬롯(solar|router). 무키면 내부서 mock

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
    # 폴백 체인: 실호출인데 산출이 전량 빈값이면 예비 모델로 1회씩 재시도(최대 3 · 성공 시 채택)
    if not llm.mock and _SV._pipeline_empty(out):
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
        (out.setdefault("trace", {}))["version"] = _SV._batch_seq_cached(team) + 1
    except Exception:
        pass
    if not llm.mock:                             # 비용 원장: 실호출만 일별×모델×콜 누적(실험 포함)
        _SV._log_cost_rollup(out.get("trace") or {}, team=team)
    if not llm.mock:                             # 실패 원장: 실호출의 콜 실패만 누적(트리아지 원천)
        # 콘텐츠 식별은 영속 실행만 전달: 실패 → 최근 실패 목록 등재(개별 재실행 대상) ·
        # 무실패 성공 → 목록에서 해소. 실험(persist=False)은 저장이 없어 재실행 불가라 제외.
        from .store import content_hash as _chash
        _SV._log_fail_rollup(out.get("trace") or {}, service=content.get("displayServiceName", ""),
                             team=team, content_hash=(_chash(content) if persist else ""),
                             title=content.get("title", ""))
    if not persist:                              # 실험(미저장): 추출만 하고 results·초안·홀드아웃 미기록
        return {"source": source, "mock": llm.mock, "content": content,
                "signals": signals, "output": out}
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
    }


def rerun_all(model: str, team=None, limit: int = 200, scope: str = "all") -> dict:
    """모아진 콘텐츠를 지정 모델로 일괄 실행(수동 · 관리자). 건당 비용 발생.
    scope: pending=미실행(STEP 1 추가 대기)만 · all=전체 재실행.
    퀘스트 진행 중에는 전체 재실행 차단(검수 중 초안이 바뀌면 판정·합의가 오염된다)."""
    if scope != "pending" and _SV.quest_active():
        return {"error": "퀘스트 진행 중에는 전체 재실행이 차단됩니다(검수 중 초안 교체 방지) · "
                         "'미실행만'은 가능하며, 반영 후 실행하거나 검수 목표 카드에서 일시를 비워 목표를 해제하세요"}
    rows = _SV.results_rows(team=team)
    targets, seen, row_by_hash = [], set(), {}
    for r in rows[-int(limit):]:
        if scope == "pending" and not _SV._is_pending_row(r):
            continue                                 # 이미 실행된 건 제외
        ch = _SV._row_key(r.get("content_ref") or {})
        if ch and ch not in seen:
            seen.add(ch)
            targets.append(ch)
            row_by_hash[ch] = r                      # 1회 로드분 재사용 · 건마다 전체 재조회(N×5000) 방지
    if not targets:
        return {"ok": True, "done": 0, "failed": 0, "model": model, "scope": scope,
                "msg": "대상이 없습니다" + (" (미실행 콘텐츠 없음)" if scope == "pending" else "")}
    done = failed = 0
    spent = 0.0
    budget = float(getattr(Config.load(), "batch_budget_usd", 0.0) or 0.0)   # 0 = 무제한
    budget_stop = False
    jid = "rerun:" + time.strftime("%H%M%S")         # 실행 큐 등록(진행률·ETA)
    _SV._job_begin(jid, model or "기본 모델", "일괄 실행", len(targets))
    _SV._INGEST_STATE[jid]["hashes"] = list(targets)     # 작업 클릭 -> 결과 콘텐츠 보기
    try:
        for ch in targets:
            res = _SV.rerun_content(ch, model, team=team, row=row_by_hash.get(ch))
            if res.get("error"):
                failed += 1
                _SV._INGEST_STATE[jid]["failed"] = failed
            else:
                done += 1
                spent += float((((res.get("output") or {}).get("trace") or {}).get("cost_usd")) or 0.0)
            _SV._INGEST_STATE[jid]["done"] += 1
            if budget > 0 and spent >= budget:       # 예산 상한: 도달 시 남은 대상 중단(비용 통제)
                budget_stop = True
                break
    except Exception as e:
        _SV._job_end(jid, False, f"{done}건 실행 후 중단 · {str(e)[:80]}")
        raise
    if budget_stop:
        _SV._job_end(jid, False, f"예산 상한 ${budget:g} 도달 · {done}건 실행(${spent:.4f}) 후 중단"
                             + (f" · 실패 {failed}" if failed else ""))
    else:
        _SV._job_end(jid, failed == 0, f"{done}건 실행" + (f" · 실패 {failed}" if failed else " 완료"))
    return {"ok": True, "done": done, "failed": failed, "model": model,
            "spent_usd": round(spent, 6), "budget_stop": budget_stop,
            "skipped": (len(targets) - done - failed) if budget_stop else 0}


def rerun_content(content_hash: str, model: str, team=None, row=None, force_quest: bool = False) -> dict:
    """같은 콘텐츠를 지정 모델로 재실행(초안 재생성 · 관리자). 기존 초안은 덮어쓰되
    이전 초안을 patch_log 에 남겨(rerun:구모델) 이력·비교 근거를 보존한다.
    row: 일괄 실행(rerun_all)이 미리 로드한 행 주입 — 건마다 전체 테이블 재조회 방지."""
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
              "source_url": ref.get("source_url", "")}   # 재실행 upsert 가 원문 링크를 지우지 않게 보존
    if _SV.quest_active() and not _SV._is_pending_row(row) and not force_quest:
        return {"error": "퀘스트 진행 중에는 검수 중 콘텐츠의 초안 재실행이 차단됩니다 · "
                         "반영 후 실행하거나 검수 목표 카드에서 목표를 해제하세요"}
    old_model = (row.get("trace") or {}).get("model", "") or ""
    result = run_pipeline(fields, mock=_SV.Handler.server_mock, team=team, model=model)
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
    _SV._agg_bump()
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


# \u2500\u2500 \uc5b4\ub4dc\ubbfc \ubaa8\ub4c8 \ub370\uc774\ud130(\uc2e4\ub370\uc774\ud130 \uc5f0\uacb0) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
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
        contents = ING.to_contents(tmp)[:200]
        if add_only:                                 # STEP 1 = 추가만(모델 미실행 · 즉시 완료)
            r = add_contents(contents, purpose=purpose, team=team, source="배치")
            return {**r, "source": "excel", "count": r.get("added", 0), "mapping": a["mapping"]}
        results, items, pairs = [], [], []
        jid = "batch:" + time.strftime("%H%M%S")     # 실행 큐 등록(진행률·ETA)
        _SV._job_begin(jid, (filename or "엑셀"), "엑셀 일괄 추출", len(contents))
        from .store import content_hash as _bch
        _SV._INGEST_STATE[jid]["hashes"] = [_bch(c) for c in contents]
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
                _SV._INGEST_STATE[jid]["done"] += 1
        except Exception as e:
            _SV._job_end(jid, False, f"{len(results)}건 추출 후 중단 · {str(e)[:80]}")
            raise
        store_save(pairs, source="배치", team=team)  # 영속 저장(단일 트랜잭션 배치)
        _SV._job_end(jid, True, f"{len(results)}건 추출 · 저장 완료")
        if (purpose or "") == "eval":               # 평가용 지정: 검수 대상에서 제외(홀드아웃)
            try:
                from .store import content_hash as _chash
                stp = _SV.get_store()
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
