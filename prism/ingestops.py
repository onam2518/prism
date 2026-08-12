"""인입·잡 도메인 (serve 에서 분리 · 라우트 분리 4차 · 로드맵 2단계 3차).

외부 소스 폴링(ingest_run_source·스케줄러)·공인 URL 검증(SSRF 방어)·실행 이력
잡 레지스트리(_job_*·_jobs_persist/restore·ingest_status)·원문 링크 백필을 담당.
HTTP 디스패치는 serve 가 유지.

컴포지션: 스토어·파이프라인·리포트 영속은 serve 가 `_SV` 로 주입(learnops 관례).
_INGEST_STATE 는 테스트가 serve._INGEST_STATE 로 뮤테이션하므로 재바인딩 금지
(serve 재수출과 같은 객체를 공유한다).
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time

from . import pipeline as PIPE
from .config import Config

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입


def backfill_urls(file_bytes: bytes, filename: str, team=None) -> dict:
    """원문 링크·참조 이미지 백필(관리자): 해시/제목 ↔ 링크·이미지 매핑 표로 기존 콘텐츠의
    source_url · image_urls 만 갱신. 초안(item_meta)·검수 판정·적재 시각은 건드리지 않는다 —
    해시가 서비스+제목+부제+본문으로만 계산되므로 둘 다 콘텐츠 정체성을 바꾸지 않는다.

    이미지 백필을 붙인 이유(2026-08-12): 운영 1,451건이 image_urls 전부 빈 목록인데
    원문 링크는 100% 채워져 있었다(업로드 시 '이미지 URL' 칸만 비운 것). 그 결과
    '포토·영상 중심' 판정이 근거 없이 이뤄져 검수 지적 단일 최다(39건)가 됐다.
    프롬프트는 이미 이미지 수를 신호로 받게 돼 있으므로 입력만 채우면 그대로 살아난다.
    링크 컬럼·이미지 컬럼 중 **하나만 있어도** 동작한다(둘 다 있으면 둘 다 갱신)."""
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
    img_col = _find(set(ING.ALIASES["image_urls"]))
    hash_col = _find({"hash", "해시", "contenthash", "콘텐츠해시"})
    title_col = _find(set(ING.ALIASES["title"]))
    if not (url_col or img_col) or not (hash_col or title_col):
        return {"error": "필수 컬럼을 찾지 못했습니다 · 링크 또는 이미지 URL 컬럼과 "
                         "해시 또는 제목 컬럼이 필요합니다", "headers": headers}
    st = _SV.get_store()
    if not (st and hasattr(st, "set_source_url")):
        return {"error": "저장소가 준비되지 않았습니다"}
    if img_col and not hasattr(st, "set_image_urls"):   # 구 계약 스토어면 링크만 처리(조용히 넘기지 않는다)
        img_col = None
    by_hash, by_title, by_img = {}, {}, {}          # 현재 적재분 색인: 매칭 + 변화 없음 판별
    for r in _SV.results_rows(team=team):
        ref = r.get("content_ref") or {}
        h = _SV._row_key(ref)
        by_hash[h] = ref.get("source_url", "") or r.get("url", "")
        by_img[h] = list(ref.get("image_urls") or [])
        t = (ref.get("title", "") or r.get("title", "")).strip()
        if t:
            by_title.setdefault(t, []).append(h)
    updated = unchanged = no_match = ambiguous = bad_url = 0
    img_updated = img_unchanged = 0
    misses = []                                    # 미매칭 표본(최대 10) · 사용자가 원인 파악
    for row in rows:
        url = str(row.get(url_col) or "").strip() if url_col else ""
        imgs = ING.normalize_image_urls(row.get(img_col)) if img_col else []
        imgs = [u for u in imgs if u.startswith("http://") or u.startswith("https://")]
        h = str(row.get(hash_col) or "").strip() if hash_col else ""
        t = str(row.get(title_col) or "").strip() if title_col else ""
        has_url = bool(url) and (url.startswith("http://") or url.startswith("https://"))
        if url and not has_url:                    # 링크 칸에 링크가 아닌 값이 들어온 경우만 집계
            bad_url += 1
        if not has_url and not imgs:               # 이 행에서 쓸 값이 없다
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
        if has_url:
            if by_hash.get(target, "") == url:
                unchanged += 1
            elif st.set_source_url(target, url, team=team):
                by_hash[target] = url
                updated += 1
            else:
                no_match += 1
        if imgs:
            if by_img.get(target) == imgs:
                img_unchanged += 1
            elif st.set_image_urls(target, imgs, team=team):
                by_img[target] = imgs
                img_updated += 1
            else:
                no_match += 1
    if updated or img_updated:
        _SV._agg_bump()
    return {"ok": True, "rows": len(rows), "updated": updated, "unchanged": unchanged,
            "imgUpdated": img_updated, "imgUnchanged": img_unchanged,
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
    _cgnat = ipaddress.ip_network("100.64.0.0/10")     # RFC6598 CGNAT(클라우드·k8s 내부 대역)
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return "주소 확인 실패"
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified
                or (ip.version == 4 and ip in _cgnat)):
            return "사설/내부 대역 주소는 허용되지 않습니다"
    return None


# 원문 소실 soft-404 시그니처(게시판 #10): HTTP 200 인데 본문이 '없는 글' 안내문인 페이지.
# 소스(플랫폼)별로 키를 나눠 확장한다 · 판정 참고용이라 문구는 보수적으로 유지.
SOURCE_GONE_SIGNS = {
    "tistory": ("권한이 없거나 존재하지 않는", "삭제된 게시물", "존재하지 않는 게시글"),
    "common": ("삭제되었거나 존재하지 않는 페이지",),
}
_CHECK_READ_MAX = 256 * 1024                # 시그니처 스캔용 본문 읽기 상한(메모리 방어)


def check_source_url(url: str, timeout: float = 6.0) -> dict:
    """원문 URL 온디맨드 상태 확인(게시판 #10 · B안 축소형). 판정 결과만 반환하며
    절대 자동으로 플래그를 확정하지 않는다(확정은 검수자의 '원문 확인 불가 표시' 버튼).
    분류: gone(404/410 또는 200+soft-404 시그니처) · temp(타임아웃·5xx·연결 실패) ·
    unknown(403 등 · 사람 판단) · ok(정상 응답 · 내용 대조는 사람 몫).
    반환: {ok, state, code, sign} · URL 검증 실패 시 {ok: False, error}."""
    import urllib.request
    import urllib.error
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "원문 링크가 없습니다"}
    err = _validate_public_url(url)
    if err:
        return {"ok": False, "error": err}

    class _SafeRedirect(urllib.request.HTTPRedirectHandler):   # 리다이렉트 대상도 매 홉 재검증(내부망 우회 차단)
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if _validate_public_url(newurl):
                raise urllib.error.URLError("리다이렉트 대상이 허용되지 않는 주소입니다")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    # 기본 urllib UA 는 일부 사이트가 무조건 403 → 판별력 확보용 식별 UA(가장 아님)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; PrismSourceCheck)"})
    try:
        with urllib.request.build_opener(_SafeRedirect()).open(req, timeout=timeout) as resp:
            code = int(getattr(resp, "status", None) or resp.getcode() or 0)
            body = resp.read(_CHECK_READ_MAX).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code = int(e.code or 0)
        if code in (404, 410):
            return {"ok": True, "state": "gone", "code": code, "sign": ""}
        if 500 <= code <= 599:
            return {"ok": True, "state": "temp", "code": code, "sign": ""}
        return {"ok": True, "state": "unknown", "code": code, "sign": ""}   # 403 등 = 사람 판단
    except Exception:                                                       # 타임아웃·연결 실패·DNS 등
        return {"ok": True, "state": "temp", "code": 0, "sign": ""}
    for signs in SOURCE_GONE_SIGNS.values():
        for s in signs:
            if s in body:
                return {"ok": True, "state": "gone", "code": code, "sign": s}
    return {"ok": True, "state": "ok", "code": code, "sign": ""}


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
            raw = resp.read(_SV._FETCH_MAX + 1)          # 응답 크기 상한(메모리 소진 방어)
            if len(raw) > _SV._FETCH_MAX:
                return None, f"응답이 너무 큽니다(상한 {_SV._FETCH_MAX // (1024 * 1024)}MB)"
            data = json.loads(raw.decode("utf-8", "replace"))
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
                              "running": True, "total": 0, "done": 0, "failed": 0,
                              "last_run": _INGEST_STATE.get(sid, {}).get("last_run", 0),
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
        _INGEST_STATE[sid].update(total=len(contents), done=0, failed=0, last_msg="추출 중…")
        cfg = Config.load()
        llm = _SV.make_text_llm(cfg, _SV.Handler.server_mock)
        pairs = []
        failed, first_err = 0, ""
        from .runops import _log_run_ledgers
        from .store import content_hash as _chash
        for c in contents:
            try:
                out = PIPE.extract(c, llm, legal=cfg.legal_enabled)
                pairs.append((c, out))
                # 비용·실패 원장: 자동 인입도 run_pipeline 을 안 타므로 여기서 직접 기록 —
                # 종전엔 크레딧이 마른 상태로 매 폴링 402 가 반복돼도 원장·트리아지 신호가 0 이었다.
                _log_run_ledgers(c, out, mock=llm.mock, content_hash=_chash(c))
            except Exception as e:
                # 조용한 유실 금지: 삼키기만 하면 화면엔 '제외 0'이 찍혀 유실이 오히려 부정된다.
                # 계수해서 완료 메시지·실행 큐 배지·last_ok 에 드러내고, 실패 원장에도 남긴다.
                failed += 1
                first_err = first_err or (str(e)[:160] or e.__class__.__name__)
                _INGEST_STATE[sid]["failed"] = failed
                try:
                    _log_run_ledgers(c, {"trace": {"fails": [{"kind": "extract", "tag": "ingest",
                                                              "detail": str(e)[:160]}]}},
                                     mock=llm.mock, content_hash=_chash(c))
                except Exception:
                    pass
            _INGEST_STATE[sid]["done"] += 1
        stats = {"inserted": 0, "updated": 0, "skipped": 0}
        st = _SV.get_store()
        if st and pairs:
            stats = st.save_dedup(pairs, "ingest-" + time.strftime("%Y%m%d-%H%M%S"), source="자동 인입")
            _SV._save_drafts(st, pairs)
            _SV._entdict_after_save(st, pairs)
        # 이미지 URL 적재율을 같이 남긴다(게시판 #9) — 상류가 이미지를 안 보내는지,
        # 우리 매핑이 컬럼을 못 잡는지 실행 큐에서 바로 구분된다(조용한 0건 재발 방지).
        from .runops import _img_note, img_coverage
        msg = (f"{len(rows)}건 수신 → 신규 {stats['inserted']} · 갱신 {stats['updated']} · 제외 {stats['skipped']}"
               + _img_note(contents))
        if failed:
            msg += f" · 추출 실패 {failed}건({first_err})"
        # 한 건이라도 유실됐으면 성공(초록불)이 아니다 — 5분마다 초록불이면 유실을 아무도 못 본다.
        _INGEST_STATE[sid].update(running=False, last_run=time.time(), last_ok=(failed == 0),
                                  failed=failed, last_msg=msg)
        _jobs_persist()
        return {"ok": True, "fetched": len(rows), "extracted": len(pairs),
                "failed": failed, "fail_error": first_err,
                "mapping": m, "mock": llm.mock,
                "with_images": img_coverage(contents)["with_images"], **stats}
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


_JOBS_KEEP = 100                # 완료 잡 메모리 보존 상한(레지스트리 무한 성장 방지 · 영속은 20건)


def _jobs_prune_locked():
    """완료(running=False) 잡을 최근 _JOBS_KEEP 건만 남긴다(_INGEST_LOCK 하에서 호출).
    개별 재실행(rerun1:*)·콘텐츠 추가(add:*)가 클릭마다 새 키로 영구 누적돼 장기 가동 시
    상태 폴링(1.5s 간격) 페이로드와 스냅샷 정렬 비용이 잡 수에 비례해 계속 커지던 것을
    막는다. 실행 중 잡은 건드리지 않는다. dict 는 재바인딩 금지(serve 와 객체 공유 계약)."""
    done = [(k, v) for k, v in _INGEST_STATE.items() if not (v or {}).get("running")]
    excess = len(done) - _JOBS_KEEP
    if excess <= 0:
        return
    done.sort(key=lambda kv: (kv[1] or {}).get("started") or (kv[1] or {}).get("last_run") or 0)
    for k, _ in done[:excess]:
        _INGEST_STATE.pop(k, None)


def _jobs_persist():
    """실행 큐 스냅샷 영속(reports 패턴 · 전역 kind='jobs'): 배포·재시작에도 이력 유지.
    시작·종료 등 상태 전이 때만 기록(건별 진행률은 기록하지 않아 저장소 부담 없음) · 최근 20건."""
    with _INGEST_LOCK:                            # 상태 전이 시점마다 완료 잡 상한 유지(메모리)
        _jobs_prune_locked()
    st = _SV.get_store()
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
    st = _SV.get_store()
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
