"""사용자 메타 서버 글루 (serve 에서 분리 · 라우트 분리 4차 · 로드맵 2단계 3차).

행동 로그 파싱(_logs_rows)·현황 집계(usermeta_data)·프로필 저장(usermeta_save_profiles)·
서식 템플릿을 담당. 계산 실체는 usermeta·personagen 모듈 · HTTP 디스패치는 serve 유지.

컴포지션: 스토어·집계 캐시는 serve 가 `_SV` 로 주입(learnops 관례).
"""
from __future__ import annotations

import json
import os
import tempfile

from .config import Config

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입


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
        return _SV._agg_cached(("usermeta", team), lambda: _usermeta_compute(None, "", team))
    return _usermeta_compute(logs_bytes, filename, team)


def _usermeta_compute(logs_bytes, filename, team) -> dict:
    from . import personagen as PG
    from . import usermeta as UM
    rows = _SV.results_rows()
    if not rows:
        return {"empty": True, "n_contents": 0, "users": [], "personas_def": [],
                "note": "먼저 [실행 · 추출]에서 콘텐츠를 추출하세요. content_id 는 추출 순서(0부터)와 매칭됩니다."}
    st = _SV.get_store()
    profiles = (_SV._report_get("usermeta_profiles", team, {}) or {}).get("users") or {}
    if logs_bytes:
        log_rows = _logs_rows(logs_bytes, filename)
        if log_rows and st and hasattr(st, "save_report"):
            st.save_report("usermeta_logs", {"rows": log_rows, "name": filename}, team=team)
            _SV._agg_bump()                               # 로그 갱신 → 사용자 메타 캐시 무효화
    else:
        log_rows = (_SV._report_get("usermeta_logs", team, {}) or {}).get("rows") or []
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
    gen = (_SV._report_get("usermeta_personas", team, {}) or {}).get("items") or {}
    need = [u for u in data.get("users", [])
            if profiles.get(u.get("user_id")) and u["user_id"] not in gen]
    if need:
        try:
            llm = _SV.make_text_llm(Config.load(), _SV.Handler.server_mock)
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
    st = _SV.get_store()
    cur = (_SV._report_get("usermeta_profiles", team, {}) or {}).get("users") or {}
    n = 0
    for p in profs or []:
        p = PG.normalize_profile(p if isinstance(p, dict) else {})
        if p["user_id"]:
            cur[p["user_id"]] = p
            n += 1
    if n and st and hasattr(st, "save_report"):
        st.save_report("usermeta_profiles", {"users": cur}, team=team)
        _SV._agg_bump()                                   # 프로필 변경 → 사용자 메타 캐시 즉시 무효화
    out = _SV.usermeta_data(team=team)
    out["saved"] = n
    return out


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
