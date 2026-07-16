"""학습·골든·평가 도메인 (serve 에서 분리 · 라우트 분리 1차).

검수 합의 → 골든 축적 → 회귀 평가 → 프롬프트 반영(learning_batch)의 폐루프와
그 산출물(학습 데이터·소요서·버전 스냅샷)을 담당한다. HTTP 디스패치는 serve 가 유지.

컴포지션: 서버 환경(스토어·LLM 라우팅·리포트 영속·mock 플래그)은 serve 가 기동 시
`_SV`(자기 모듈 객체)로 주입한다. `python -m prism.serve`(__main__) 실행에서 역 import 로
serve 인스턴스가 이중 생성되는 문제를 피하기 위한 구조 · 순환 import 없음.
"""
from __future__ import annotations
import json
import threading
import time

from . import feedback_loop as FL
from . import meta_prompts as MP
from . import prompts as PR
from .config import Config

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입


def sync_learned():
    """배치 결과 피드백 → 단계별 학습 보정(LEARNED)으로 컴파일해 프롬프트에 자동 반영.
    모델 귀속 라우트는 LEARNED_BY_MODEL 계층으로 분리(그 모델 프롬프트에만 병기)."""
    try:
        st = _SV.get_store()
        try:                                       # 관리자가 끈 지시(개별 무효화)는 컴파일에서 제외
            ex = _SV.disabled_directives()
        except Exception:
            ex = set()
        learned = st.learned_by_stage(exclude=ex) if st else {}
        PR.LEARNED = {k: (learned.get(k) or "") for k in ("extract", "analyze", "review", "judge")}
        bm = (st.routes_by_stage_model(exclude=ex)
              if (st and hasattr(st, "routes_by_stage_model")) else {})
        PR.LEARNED_BY_MODEL = {m: {stg: "\n".join(f"- {t}" for t in items)
                                   for stg, items in stages.items()}
                               for m, stages in bm.items() if m}
    except Exception as e:
        # 스토어 일시 오류로 기존 학습 보정을 빈 값으로 리셋하면 안 된다(조용한 품질 후퇴).
        # 기존 PR.LEARNED / LEARNED_BY_MODEL 을 그대로 유지하고, 미초기화 상태만 빈 값 시드.
        if not getattr(PR, "LEARNED", None):
            PR.LEARNED = {"extract": "", "analyze": "", "review": "", "judge": ""}
        if not getattr(PR, "LEARNED_BY_MODEL", None):
            PR.LEARNED_BY_MODEL = {}
        print(f"[learn] sync_learned 실패 · 기존 보정 유지: {e}")

_LAST_EVAL_DETAIL = []                             # (폴백 캐시) 최근 평가 불일치 · 원천은 store reports

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
    st = _SV.get_store()
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
        llm, _route = _SV.llm_for_model(used_model, _SV.Handler.server_mock)
        if llm is None:
            return {"ok": False, "error": f"모델 호출 불가({_route}): {used_model}"}
    else:
        llm = _SV.make_text_llm(cfg, _SV.Handler.server_mock)
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
    _SV._report_save("eval_detail", {"items": detail, "ts": time.time()}, team)
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
    st = _SV.get_store()
    if _SV._supa() and not _SV.is_admin_user(uid, team, email):   # 로컬(sqlite)은 개방(타 관리자 라우트와 동일 게이트)
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
    _SV._agg_bump()
    return {"ok": True, "count": n, "skipped": skipped, "merged": bool(merge)}

def golden_list(team=None) -> dict:
    """관리자 골든 브라우저: 목록 + 출처 집계 + 라벨 오류 의심(최근 평가 불일치) 표시."""
    st = _SV.get_store()
    if not (st and hasattr(st, "golden_rows")):
        return {"ok": False, "error": "지원하지 않는 저장소", "items": []}
    ev = _SV._report_get("eval_detail", team, {}) or {}
    flagged = {f.get("hash") for f in (ev.get("items") or _LAST_EVAL_DETAIL or [])}
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
    st = _SV.get_store()
    if not (st and hasattr(st, "feedback_map") and hasattr(st, "upsert_golden")):
        return {"ok": False, "error": "지원하지 않는 저장소"}
    rows = _SV.results_rows(team=team)
    try:
        fmap = st.feedback_map(team=team)
    except Exception:
        fmap = {}
    weights = _SV.reviewer_weights(team)               # 골드 정확도 기반 신뢰도(G-4)
    min_good = max(1, int(getattr(Config.load(), "golden_min_good", 1) or 1))   # 확정 최소 '정확' 인원
    try:                                               # 리드 최종판정: 다수결보다 우선(타이브레이크)
        finals = _SV.final_verdicts(team)
    except Exception:
        finals = {}
    try:
        existing = st.golden_hashes(team)
        by_source = {r["hash"]: r["source"] for r in st.golden_rows(team, limit=10000)} if hasattr(st, "golden_rows") else {}
    except Exception:
        existing, by_source = set(), {}
    entries, need_list, no_cat, no_grade, disagree = [], [], 0, 0, 0
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
        fv = (finals.get(ch) or {}).get("verdict")     # 리드 최종판정(있으면 다수결보다 우선)
        if fv == "bad":                                # 리드가 '수정 필요' 확정 → 승격 금지 + 검수 유래 골든 강등
            disagree += 1
            if ch in existing and by_source.get(ch, "review") == "review":
                demote.append(ch)
            continue
        if fv != "good" and not (fb.get("good", 0) >= min_good and gw > bw):   # 정확 최소 인원 + 가중 다수
            disagree += 1
            # 검수 유래 골든이 뒤집힘(가중 열세) → 강등. 관리자 등록분(manual)은 보존.
            if ch in existing and by_source.get(ch, "review") == "review" and bw > gw:
                demote.append(ch)
            continue
        im = r.get("item_meta") or {}
        qm = r.get("quality_meta") or {}
        if qm.get("finalGrade", "") not in ("G", "R"):   # 빈/보류 등급(judge 실패·판정 보류)은 골든 승격 제외 · grade_accuracy 잠식 방지
            no_grade += 1
            need_list.append({"hash": ch, "title": content.get("title", ""),
                              "service": content.get("displayServiceName", ""), "reason": "grade"})
            continue
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
        if by_source.get(e["hash"]) == "manual":         # 관리자 확정(manual) 골든은 검수 유래로 덮어쓰지 않음(정답 소실 방지)
            continue
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
            "total": total, "need_category": no_cat, "need_grade": no_grade,
            "need_list": need_list[:50], "disagree": disagree, "min_good": min_good}

_LAST_LEARN_REPORT = {}                               # 최근 일배치 결과(수신·표시용)

def cheapest_passing_model(models: list, gate: float) -> str:
    """'합격하는 가장 싼 모델' 추천: 등급 일치율이 게이트 이상인 모델 중 비용 최저.
    비용 미계측(None)은 제외 · 동률이면 일치율 높은 쪽. 없으면 빈 문자열."""
    ok = [m for m in (models or [])
          if (m.get("grade_accuracy") or 0) >= gate and m.get("cost_usd") is not None]
    if not ok:
        return ""
    ok.sort(key=lambda m: (m["cost_usd"], -(m.get("grade_accuracy") or 0)))
    return ok[0].get("model") or ""


def compare_models_on_golden(models=None, team=None, scope: str = "all") -> dict:
    """골든셋(사람 확정 정답)을 여러 모델에 실호출로 돌려 정합성 비교 → 최적 모델 선택 근거.
    모델별 제공자·엔드포인트를 라우팅(llm_for_model)하고, 키 없는 모델은 건너뛰되 사유를 노출."""
    st = _SV.get_store()
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
        llm, route = _SV.llm_for_model(model, _SV.Handler.server_mock)
        if llm is None:
            skipped.append({"model": model, "reason": route})
            continue
        m = abtest.evaluate(rows[:200], H.Methodology(name=model), llm, concurrency=8)
        out.append({"model": model, "route": route, "real": (not llm.mock), "n": min(len(rows), 200),
                    "grade_accuracy": m.get("grade_accuracy"), "reason_jaccard": m.get("reason_jaccard"),
                    "reason_exact_match": m.get("reason_exact_match"), "empty_rate": m.get("empty_rate"),
                    "cost_usd": m.get("cost_usd"), "tokens": m.get("tokens"),
                    "latency_p50_ms": m.get("latency_p50_ms"), "latency_p95_ms": m.get("latency_p95_ms")})
    if not out:
        return {"ok": False, "error": "호출 가능한 모델이 없습니다 · API 키(Upstage/라우터)를 확인하세요",
                "skipped": skipped, "golden_n": len(rows)}
    out.sort(key=lambda r: (-(r.get("grade_accuracy") or 0), -(r.get("reason_jaccard") or 0)))
    gate = float(getattr(cfg.thresholds, "eval_gate", 0.85) or 0.85)
    return {"ok": True, "models": out, "skipped": skipped,
            "best": out[0]["model"], "golden_n": len(rows),
            "eval_gate": gate, "cheapest_passing": cheapest_passing_model(out, gate)}

def snapshot_prompts(team=None) -> dict:
    """학습 반영 직후, 다음 초안 버전(v = 반영 회차 + 1)이 쓰게 될 단계(콜)별 최종
    시스템 프롬프트를 영속한다 · 버전별 산출을 그때의 프롬프트로 재현하는 근거."""
    st = _SV.get_store()
    if not st:
        return {}
    try:
        ver = int(st.batch_seq(team)) + 1
    except Exception:
        ver = 1
    from .schema import Content
    c = Content(displayServiceName="뉴스", title="(스냅샷)", subtitle="", body="(스냅샷 본문)")
    _SV.sync_prompt()
    cfg = Config.load()
    base_model = cfg.model or ""
    cm = dict(getattr(cfg, "meta_call_models", {}) or {})
    calls = {}
    for call in MP.CALLS:
        m = cm.get(call) or base_model
        try:
            calls[call] = {"model": m, "system": PR.call_system(c, call, m)}
        except Exception:
            pass
    payload = {"version": ver, "ts": time.time(), "model": base_model,
               "quality_version": PR.quality_version(), "calls": calls,
               "learned": dict(PR.LEARNED), "learned_by_model": dict(PR.LEARNED_BY_MODEL)}
    try:
        payload["item"] = PR.item_system(c, base_model)
    except Exception:
        pass
    _SV._report_save(f"prompt_snapshot_v{ver}", payload, team)
    _SV._report_save("prompt_snapshot_latest", payload, team)
    return {"version": ver, "calls": list(calls.keys())}

def _batch_regressions(pre: dict, post: dict, min_bucket_n: int = 5) -> list:
    """개선 후 평가가 전보다 나빠진 지점 목록(원복 사유 문구 · 없으면 빈 목록).
    ① 정합성 2%p 초과 악화 ② 유해 미탐률(harm_miss_rate) 악화
    ③ 버킷별 정합성 10%p 초과 하락(표본 min_bucket_n 이상 버킷만 · 소표본 노이즈 배제).
    cli tune RegressionGuard 를 서버 자동 배치로 이식(단일 스칼라 가드의 사각 해소)."""
    out = []
    try:
        d = round((post.get("grade_accuracy") or 0.0) - (pre.get("grade_accuracy") or 0.0), 4)
    except (TypeError, ValueError):
        return out
    if d < -0.02:
        out.append(f"정합성 {d:+.1%} 악화")
    pre_miss = float(pre.get("harm_miss_rate") or 0.0)
    post_miss = float(post.get("harm_miss_rate") or 0.0)
    if post_miss > pre_miss + 1e-9:
        out.append(f"유해 미탐 {pre_miss:.1%}→{post_miss:.1%} 악화")
    post_b = post.get("by_reason_bucket") or {}
    for b, pv in (pre.get("by_reason_bucket") or {}).items():
        if int((pv or {}).get("n") or 0) < min_bucket_n:
            continue
        ba = float((pv or {}).get("grade_acc") or 0.0)
        ca = float((post_b.get(b) or {}).get("grade_acc", ba))
        if ca < ba - 0.10:
            out.append(f"버킷 {b} {ba:.0%}→{ca:.0%} 회귀")
    return out


def learning_batch(team=None, models=None) -> dict:
    """배치 학습: ① 정확분 골든 축적(평가 셋 고정) ② 개선 전 회귀 점수 ③ 피드백 병합→프롬프트 개선
    ④ 개선 후 회귀 점수 → 전/후 delta 기록. 정합성 2%p 초과 악화·유해 미탐 악화·버킷 회귀
    중 하나라도 걸리면 개선을 반영하지 않고 이전 프롬프트를 유지한다(방향 검증 · 진동 방지)."""
    golden = build_golden_from_reviews(team)             # 전/후를 같은 정답셋으로 재도록 먼저 고정
    prev_learned = dict(PR.LEARNED)
    prev_by_model = {m: dict(v) for m, v in (PR.LEARNED_BY_MODEL or {}).items()}
    eval_pre = eval_golden(team)                         # 개선 전(현행 프롬프트) 점수
    improve = meta_compile_run(team)
    changed = (PR.LEARNED != prev_learned) or (PR.LEARNED_BY_MODEL != prev_by_model)
    delta = None
    if changed and eval_pre.get("ok"):
        evalr = eval_golden(team)                        # 개선 후 점수(같은 셋)
        try:
            delta = round((evalr.get("grade_accuracy") or 0.0) - (eval_pre.get("grade_accuracy") or 0.0), 4)
        except (TypeError, ValueError):
            delta = None
        regressions = _batch_regressions(eval_pre, evalr) if evalr.get("ok") else []
        if regressions:                                  # 악화 가드: 이전 프롬프트로 원복
            PR.LEARNED = prev_learned
            PR.LEARNED_BY_MODEL = prev_by_model
            improve = dict(improve or {})
            improve["reverted"] = True
            improve["revert_reason"] = " · ".join(regressions) + " → 이번 보정 미반영(이전 프롬프트 유지)"
            evalr = eval_pre                             # 유지되는 프롬프트 기준 점수로 보고
    else:
        evalr = eval_pre
        if eval_pre.get("ok"):
            delta = 0.0
    compare = compare_models_on_golden(models, team) if (models and len(models) > 1) else None
    try:                                        # 학습 반영 회차 기록 → 초안 버전(v = 회차+1)
        stv = _SV.get_store()
        if stv and hasattr(stv, "log_event_once"):
            # reviewer_id 는 uuid 컬럼(nullable) · 시스템 이벤트는 reviewer 없이 NULL 로 기록한다.
            # '(system)' 문자열은 uuid 위반이라 supabase insert 가 실패 → 회차 미기록 → 버전 v1 고착의 원인.
            stv.log_event_once(None, "learn_batch", int(time.time()), 0, team=team)
    except Exception as e:
        print(f"  [warn] learn_batch 회차 기록 실패: {e}")
    try:                                        # 이번 회차가 만든 프롬프트를 버전과 함께 영속
        snap = snapshot_prompts(team)
    except Exception:
        snap = {}
    final_rerun = None
    try:                                        # 2층 검수 3-1: 미확정분을 방금 반영된 새 버전으로 재실행
        if bool(getattr(Config.load(), "final_rerun_after_batch", True)) and hasattr(_SV, "rerun_unconfirmed"):
            final_rerun = _SV.rerun_unconfirmed(team)
            if final_rerun and final_rerun.get("done"):
                print(f"  [batch] 미확정분 {final_rerun['done']}건을 새 버전으로 재실행(최종검수용)")
    except Exception:
        final_rerun = None
    report = {"ok": True, "ts": time.time(), "improve": improve, "golden": golden,
              "eval": evalr, "compare": compare, "prompt_snapshot": snap,
              "eval_pre": ({"grade_accuracy": eval_pre.get("grade_accuracy"), "n": eval_pre.get("evaluated")}
                           if eval_pre.get("ok") else None),
              "improve_delta": delta, "final_rerun": final_rerun,
              "grade_accuracy": evalr.get("grade_accuracy") if evalr.get("ok") else None}
    global _LAST_LEARN_REPORT
    _LAST_LEARN_REPORT = report
    _SV._report_save("learn_report", report, team)
    try:                                        # 버전별 리포트도 영속(버전 히스토리 상세용)
        sver = int((snap or {}).get("version") or 0)
        if sver:
            _SV._report_save(f"learn_report_v{sver}", report, team)
    except Exception:
        pass
    _SV._agg_bump()
    try:                                        # 반영 완료 모먼트: 접속 팀원 전체에 축하 토스트(SSE)
        stv2 = _SV.get_store()
        done_ver = int(stv2.batch_seq(team)) if (stv2 and hasattr(stv2, "batch_seq")) else 0
        _SV.broadcast({"type": "learn_batch", "version": done_ver,
                       "grade_accuracy": report.get("grade_accuracy"),
                       "improve_delta": delta, "reverted": bool((improve or {}).get("reverted")),
                       "confirmed": golden.get("confirmed"), "ts": report["ts"]})
    except Exception:
        pass
    print(f"  [batch] 학습 일배치 · 골든 확정 {golden.get('confirmed')} · 카테고리필요 "
          f"{golden.get('need_category')} · 정합성(grade) {report['grade_accuracy']}")
    return report

def learn_data(team=None) -> dict:
    """학습 데이터 현황(관리자): 클래스 커버리지·일치도·검수자 신뢰도·라벨 오류 후보·추출 가능량·소요 대비.
    기준치는 논문 근거(LEARNING_DESIGN.md): SetFit 8/클래스 · LIMA 1k · Llama Guard 13.5k ·
    InstructGPT 33k · tinyBenchmarks 100/축 · CI 는 Miller 2024."""
    from . import quality as Q
    from . import dictionaries as D
    st = _SV.get_store()
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
    # + 골든 합의 가중치(reviewer_weights · 실제 다수결에 쓰는 값) + 최근 7일 골드 추세
    ds = Q.dawid_skene_binary(Q.feedback_labels(fmap))
    arena = st.arena_stats(team=team)
    try:
        weights = _SV.reviewer_weights(team) or {}
    except Exception:
        weights = {}
    DAY = 86400.0
    now = time.time()
    try:
        wk_gold = st.gold_stats_since(now - 7 * DAY, team=team) if hasattr(st, "gold_stats_since") else {}
        two_gold = st.gold_stats_since(now - 14 * DAY, team=team) if hasattr(st, "gold_stats_since") else {}
    except Exception:
        wk_gold, two_gold = {}, {}
    reviewers = []
    for row in arena.get("leaderboard", []):
        rv = row["reviewer"]
        rid = row.get("reviewer_id") or rv                 # supabase=uuid · sqlite=닉네임 동일
        dsr = (ds.get("reviewers") or {}).get(rid) or {}   # DS 키(uuid 우선)로 조회 · feedback_labels 와 정렬
        w = wk_gold.get(rid) or wk_gold.get(rv) or {"n": 0, "correct": 0}
        t = two_gold.get(rid) or two_gold.get(rv) or {"n": 0, "correct": 0}
        pv_n = t["n"] - w["n"]                             # 그 전 7일 = 14일 창 - 최근 7일 창
        pv_corr = t["correct"] - w["correct"]
        trend = None                                       # 양쪽 표본 3건 이상일 때만(소표본 노이즈 방지)
        if w["n"] >= 3 and pv_n >= 3:
            trend = round(w["correct"] / w["n"] - pv_corr / pv_n, 4)
        reviewers.append({"reviewer": rv, "n": row.get("reviews", 0),
                          "agree_rate": row.get("agree_rate"),
                          "gold_n": row.get("gold_n", 0), "gold_acc": row.get("gold_acc"),
                          "ds_error": dsr.get("error_rate"),
                          "weight": weights.get(rid) if weights.get(rid) is not None else weights.get(rv),
                          "gold_trend": trend, "gold_wk_n": w["n"], "gold_pv_n": max(0, pv_n)})
    # 골든 정합성 ± 95% CI(최근 일배치 평가 기준, Miller 2024)
    rep = _SV._report_get("learn_report", team, _LAST_LEARN_REPORT) or {}
    ev = rep.get("eval") or {}
    acc_ci = None
    if ev.get("ok") and ev.get("n"):
        lo, hi = Q.binomial_ci(ev.get("grade_accuracy") or 0.0, int(ev["n"]))
        acc_ci = {"acc": ev.get("grade_accuracy"), "n": int(ev["n"]), "lo": lo, "hi": hi}
    # 추출 가능량
    prows = st.patch_rows(team=team) if hasattr(st, "patch_rows") else []
    patch_n = len(prows)
    fstats = st.feedback_stats(team=team)
    rationale_n = fstats.get("learned", 0)
    # 노하우 결속: 골든 중 사람 판단 사유(검수 노트·교정 이력)가 연결된 건 · knowhow.jsonl 의 원천.
    # REAP 사유는 내보내기 시점에 합류(건별 질의 비용상 집계에는 미포함 → 소량 가산될 수 있음).
    from .store import content_hash as _chash
    patch_hashes = {p["hash"] for p in prows}
    knowhow_n = 0
    for g in golden:
        ch = _chash(g.get("content") or {})
        e = fmap.get(ch) or {}
        if ch in patch_hashes or any((v.get("note") or "").strip() for v in e.get("verdicts", [])):
            knowhow_n += 1
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
    # 사전 갭(드롭 관측): 최근 트레이스의 Verifier 드롭 집계 · 사전 별칭·프롬프트 보정의 1차 신호
    gap = {"intent": {}, "category": {}}
    gap_retries = {"intent": 0, "category": 0}
    try:
        for r in _SV.results_rows(team=team):
            for v in ((r.get("trace") or {}).get("agent_verdicts") or []):
                d = v.get("drop") if isinstance(v, dict) else None
                if not d or d.get("call") not in gap:
                    continue
                for val in (d.get("values") or []):
                    gap[d["call"]][val] = gap[d["call"]].get(val, 0) + 1
                if d.get("retried"):
                    gap_retries[d["call"]] += 1
    except Exception:
        pass
    dict_gap = {k: sorted(v.items(), key=lambda x: -x[1])[:10] for k, v in gap.items()}
    dict_gap["retries"] = gap_retries
    # 가이드 모호 신호: 최근 학습 반영의 메타컴파일이 '서로 충돌해 지시로 합치지 못한' 지적(ambiguities)
    # → 정책 가이드 명확화 백로그. 공통(results) + 모델 귀속(model_results) 전부 집계.
    guide_amb = []
    improve = rep.get("improve") or {}
    for stage in ("extract", "analyze", "review", "judge"):
        for a in (((improve.get("results") or {}).get(stage) or {}).get("ambiguities") or []):
            t = str(a).strip()
            if t:
                guide_amb.append({"stage": stage, "model": "", "text": t})
    for m, stages in sorted((improve.get("model_results") or {}).items()):
        for stage, r in (stages or {}).items():
            for a in ((r or {}).get("ambiguities") or []):
                t = str(a).strip()
                if t:
                    guide_amb.append({"stage": stage, "model": m, "text": t})
    return {"ok": True, "golden_n": golden_n, "grade_dist": grade_dist,
            "guide_ambiguities": guide_amb, "guide_ambiguities_ts": rep.get("ts"),
            "dict_gap": dict_gap,
            "coverage": coverage, "covered": sum(1 for c in coverage if c["lack"] == 0),
            "class_total": len(coverage), "per_class_target": PER_CLASS_TARGET,
            "alpha": alpha, "agreement": agree, "multi_units": multi_units,
            "reviewers": reviewers, "acc_ci": acc_ci,
            "label_flags": list((_SV._report_get("eval_detail", team, {}) or {}).get("items") or _LAST_EVAL_DETAIL),
            "split": split_list[:50], "split_n": len(split_list),
            "extractable": {"sft": golden_n, "dpo": patch_n, "rationale": rationale_n,
                            "knowhow": knowhow_n},
            "knowhow": {"n": knowhow_n,
                        "coverage": (round(knowhow_n / golden_n, 3) if golden_n else None)},
            "requirements": requirements}

def learn_spec_md(team=None, d=None) -> str:
    """파인튜닝 스펙·소요서(.md) 생성: 살아있는 검수·골든 수치를 근거로 한 요구사항 문서.
    이 도구의 최종 산출물(관리자 주입 → 검수 → 골든 → 스펙·소요) · 기준치는 전부 논문 출처.
    d: 호출부가 이미 계산한 learn_data 주입(backlog_rows 와 동일 관례) — 번들 발행의 중복 전량 집계 방지."""
    from . import dictionaries as _D
    d = d if d is not None else learn_data(team)
    if not d.get("ok"):
        return "# 파인튜닝 소요서\n\n데이터가 없습니다."
    rep = _SV._report_get("learn_report", team, _LAST_LEARN_REPORT) or {}
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
    L.append("- 카테고리 공식 표기는 영문(IAB 는 공식 번역 미배포) · 본 문서의 한글은 표시용 병기")
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
    kh = d.get("knowhow") or {}
    if d.get("golden_n"):
        cov = kh.get("coverage")
        L.append(f"- 판단 노하우 결속: 골든 {d['golden_n']}건 중 **{kh.get('n', 0)}건"
                 f"({round(cov * 100, 1) if cov is not None else 0}%)** 에 사람 사유(검수 노트·교정 이력) 연결"
                 " · 원문 확인은 핸드오프 번들 knowhow.jsonl")
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
            L.append(f"| {_D.category_bilingual(c['cls'])} | {c['have']} | {c['lack']} |")
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
    st = _SV.get_store()
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
    if kind == "knowhow":                              # 판단 궤적: 골든 ← 검수 의견·REAP 사유·교정 전/후 결속
        for r in knowhow_rows(team):
            if r["revisions"] or r["rationales"] or any(o.get("note") for o in r["opinions"]):
                lines.append(json.dumps(r, ensure_ascii=False))
        return "prism_knowhow.jsonl", "\n".join(lines)
    return None, f"알 수 없는 종류: {kind}"

_REAP_JOIN_CAP = 300                               # REAP 건별 조회 상한(요청 비용 억제 · rationale 과 동일)

def knowhow_rows(team=None) -> list:
    """골든 1건마다 사람 판단의 전체 궤적을 결합: 검수 의견(판정·노트)·REAP 사유(explain/plan)·
    교정 전/후·합의 상태. '왜 이 정답인가'를 건 단위로 검증할 수 있는 노하우 계층(knowhow.jsonl)."""
    st = _SV.get_store()
    if not st:
        return []
    from .store import content_hash
    try:
        fmap = st.feedback_map(team=team)
    except Exception:
        fmap = {}
    patches = {}
    for p in (st.patch_rows(team=team) if hasattr(st, "patch_rows") else []):
        patches.setdefault(p["hash"], []).append(
            {"reviewer": p.get("reviewer", ""), "element": p.get("element", ""),
             "before": p.get("before") or {}, "after": p.get("after") or {}, "ts": p.get("ts")})
    rows, reap_joined = [], 0
    for g in (st.get_golden(team) if hasattr(st, "get_golden") else []):
        content, exp = g.get("content") or {}, g.get("expected") or {}
        ch = content_hash(content)
        e = fmap.get(ch) or {}
        opinions = [{"reviewer": v.get("reviewer"), "verdict": v.get("verdict"),
                     "stage": v.get("stage"), "note": (v.get("note") or "").strip(),
                     "ts": v.get("ts")} for v in e.get("verdicts", [])]
        rationales = []
        if opinions and reap_joined < _REAP_JOIN_CAP and hasattr(st, "get_reap"):
            try:
                rationales = [{"reviewer": r.get("reviewer"), "explain": r.get("explain") or "",
                               "plan": r.get("plan") or "", "remember": r.get("remember") or "",
                               "ask": r.get("ask") or "", "stage": r.get("stage")}
                              for r in st.get_reap(ch)]
                reap_joined += 1
            except Exception:
                rationales = []
        rows.append({"hash": ch, "content": content, "expected": exp,
                     "consensus": e.get("consensus", ""), "agree": e.get("agree"),
                     "opinions": opinions, "rationales": rationales,
                     "revisions": patches.get(ch, [])})
    return rows

def backlog_rows(team=None, d=None) -> list:
    """모델러 백로그(backlog.jsonl): 다음 반복에서 손볼 미해결 신호를 유형별 한 줄로 —
    split(의견 갈림 · 원시 의견 포함) / label_flag(골든-모델 어긋남 · 오류 의심) /
    dict_gap(사전에 없어 드롭된 산출값) / class_gap(커버리지 부족 클래스)."""
    d = d if (d and d.get("ok")) else learn_data(team)
    if not d.get("ok"):
        return []
    st = _SV.get_store()
    try:
        fmap = st.feedback_map(team=team) if st else {}
    except Exception:
        fmap = {}
    rows = []
    for ch, e in fmap.items():                         # split 은 전량(learn_data 의 표시용 50건 상한과 무관)
        if e.get("consensus") != "split":
            continue
        rows.append({"type": "split", "hash": ch, "n": e.get("n", 0),
                     "good": e.get("good", 0), "bad": e.get("bad", 0),
                     "opinions": [{"reviewer": v.get("reviewer"), "verdict": v.get("verdict"),
                                   "stage": v.get("stage"), "note": (v.get("note") or "").strip(),
                                   "ts": v.get("ts")} for v in e.get("verdicts", [])]})
    for f in d.get("label_flags") or []:
        rows.append(dict({"type": "label_flag"}, **(f if isinstance(f, dict) else {"item": f})))
    dg = d.get("dict_gap") or {}
    for call in ("intent", "category"):
        for val, n in (dg.get(call) or []):
            rows.append({"type": "dict_gap", "call": call, "value": val, "drops": n})
    for c in d.get("coverage") or []:
        if c.get("lack", 0) > 0:
            rows.append({"type": "class_gap", "cls": c["cls"], "have": c["have"], "lack": c["lack"]})
    return rows

def _data_card_md(d, man) -> str:
    """데이터 카드(data_card.md): Datasheets for Datasets(Gebru et al. 2021) 축약 양식.
    수치는 전부 실데이터 자동 기입 · 라이선스/민감정보 항목만 배포 전 사람이 확정."""
    c = man["counts"]
    kh = d.get("knowhow") or {}
    cov = kh.get("coverage")
    L = ["# Prism 핸드오프 데이터 카드", "",
         f"발행 #{man['seq']} · {time.strftime('%Y-%m-%d %H:%M', time.localtime(man['ts']))}"
         f" · 프롬프트 v{man.get('prompt_version') or '?'}",
         "", "## 목적·구성",
         "- 용도: 콘텐츠 분류·운영 특화 LLM 파인튜닝(SFT→DPO) · 태스크 정의는 finetune_spec.md",
         f"- 구성: SFT {c['sft']} · 선호쌍(DPO) {c['dpo']} · 판단근거(rationale) {c['rationale']}"
         f" · 노하우 궤적 {c['knowhow']} · 백로그 {c['backlog']}",
         "", "## 수집·라벨링 절차",
         "- 사람 검수(HITL) 합의로 정답 확정: 판정(정확/수정) → 교정(전/후 보존) → 합의 승격(골든)",
         f"- 노하우 결속: 골든 {c['golden']}건 중 {kh.get('n', 0)}건"
         f"({round(cov * 100, 1) if cov is not None else 0}%)에 판단 사유(노트·교정·REAP) 연결 · 원문은 knowhow.jsonl",
         "", "## 알려진 한계 (백로그 동봉)",
         f"- 의견 갈림(split) {c['split']}건 · 라벨 오류 의심 {c['label_flags']}건 → backlog.jsonl 에 원시 의견 포함",
         f"- 검수 일치도: 단순 {d.get('agreement') if d.get('agreement') is not None else '·'}"
         f" · Krippendorff α {d.get('alpha') if d.get('alpha') is not None else '·'} (참고 지표 · 임계값 기계 적용 금지)",
         "", "## 배포 전 확인 (사람 작성)",
         "- [ ] 라이선스·이용 범위:",
         "- [ ] 개인정보·민감정보 점검:",
         "- [ ] 수령자(모델러)·전달 채널:", ""]
    return "\n".join(L)

def handoff_bundle(team=None):
    """모델러 핸드오프 번들(.zip): 학습데이터 3종 + 노하우 궤적 + 백로그 + 소요서 + 재현 스냅샷
    (프롬프트·사전) + 데이터 카드 + manifest(건수·체크섬·직전 발행 대비 증분).
    발행 이력은 reports('handoff_log')에 영속 → 다음 발행에서 증분 자동 산출.
    반환 (filename, zip_bytes) 또는 (None, error)."""
    import hashlib
    import io
    import zipfile
    st = _SV.get_store()
    if not st:
        return None, "store unavailable"
    d = learn_data(team)
    if not d.get("ok"):
        return None, d.get("error") or "데이터가 없습니다"
    files = {}
    for kind in ("sft", "dpo", "rationale", "knowhow"):
        fname, text = learn_export(kind, team)
        if fname:
            files[fname] = text
    files["backlog.jsonl"] = "\n".join(json.dumps(r, ensure_ascii=False)
                                       for r in backlog_rows(team, d))
    files["finetune_spec.md"] = learn_spec_md(team, d=d)   # 이미 계산한 learn_data 재사용(전량 집계 2회 방지)
    from . import dictionaries as D
    files["dictionaries.json"] = json.dumps({            # taxonomy 외재화 방식 → 사전 동봉이 재현 조건
        "iab_tier1": list(D.IAB_TIER1),
        "tier2": {k: list(v) for k, v in (getattr(D, "CONTENT_CATEGORY_TIER2", {}) or {}).items()},
        "intent_universal": list(getattr(D, "INTENT_CATEGORIES_UNIVERSAL", []) or []),
        "intent_by_service": {k: list(v) for k, v in
                              (getattr(D, "INTENT_CATEGORIES_BY_SERVICE", {}) or {}).items()},
    }, ensure_ascii=False, indent=2)
    snap = _SV._report_get("prompt_snapshot_latest", team)
    if snap:
        files["prompts_snapshot.json"] = json.dumps(snap, ensure_ascii=False, indent=2)
    rep = _SV._report_get("learn_report", team, _LAST_LEARN_REPORT) or {}
    files["eval_report.json"] = json.dumps({
        "learn_report": rep, "acc_ci": d.get("acc_ci"),
        "label_flags": d.get("label_flags") or [],
        "alpha": d.get("alpha"), "agreement": d.get("agreement")}, ensure_ascii=False, indent=2)

    def _nl(name):
        return len([x for x in (files.get(name) or "").split("\n") if x.strip()])
    log = list((_SV._report_get("handoff_log", team) or {}).get("entries") or [])
    seq = len(log) + 1
    counts = {"golden": d.get("golden_n", 0), "sft": _nl("prism_sft.jsonl"),
              "dpo": _nl("prism_dpo.jsonl"), "rationale": _nl("prism_rationale.jsonl"),
              "knowhow": _nl("prism_knowhow.jsonl"), "backlog": _nl("backlog.jsonl"),
              "split": d.get("split_n", 0), "label_flags": len(d.get("label_flags") or [])}
    try:
        golden_total = st.golden_count(team) if hasattr(st, "golden_count") else None
    except Exception:
        golden_total = None
    prev = log[-1] if log else None
    manifest = {"tool": "prism", "seq": seq, "ts": time.time(), "team": team or "",
                "prompt_version": (snap or {}).get("version"),
                "model": (snap or {}).get("model", ""),
                "counts": counts, "golden_total": golden_total,
                "knowhow_coverage": (d.get("knowhow") or {}).get("coverage"),
                "caps": {"golden_rows": 1000, "reap_join": _REAP_JOIN_CAP},
                "prev": ({"seq": prev["seq"], "ts": prev["ts"], "counts": prev["counts"]}
                         if prev else None),
                "delta": ({k: counts.get(k, 0) - int((prev["counts"] or {}).get(k, 0))
                           for k in counts} if prev else None)}
    files["data_card.md"] = _data_card_md(d, manifest)
    manifest["files"] = {}
    for name, text in files.items():
        b = text.encode("utf-8")
        manifest["files"][name] = {"bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for name, text in files.items():
            z.writestr(name, text)
    log.append({"seq": seq, "ts": manifest["ts"], "counts": counts})
    _SV._report_save("handoff_log", {"entries": log[-50:]}, team)
    return f"prism_handoff_{seq:02d}_{time.strftime('%Y%m%d')}.zip", buf.getvalue()

_learn_sched_started = False

def next_batch_time(next_at, now=None) -> float:
    """검수 목표(퀘스트) 일시('YYYY-MM-DDTHH:MM' 로컬) → epoch. 미지정·형식 오류는 0.
    now 는 시그니처 호환용(파싱에 미사용)."""
    if not next_at:
        return 0.0
    try:
        import datetime as _dt
        return _dt.datetime.strptime(str(next_at).strip()[:16], "%Y-%m-%dT%H:%M").timestamp()
    except Exception:
        return 0.0


def _run_due_batch(cfg, now=None) -> bool:
    """퀘스트 일시가 도달했으면 그 퀘스트를 만든 팀(learn_team)으로 학습 배치를 1회 실행하고
    목표를 소진한다. 실행했으면 True. 스케줄러 루프와 테스트가 공유.
    팀 태깅이 핵심: team 없이 돌리면 골든 승격·버전(batch_seq)이 팀 스코프 조회에서 사라진다."""
    due = next_batch_time(getattr(cfg, "learn_next_at", ""))
    if not due or due > (now if now is not None else time.time()):
        return False
    learning_batch(getattr(cfg, "learn_team", "") or None)   # 골든·버전을 그 팀에 태깅
    try:
        c = Config.load()
        rep = int(getattr(c, "learn_repeat_days", 0) or 0)
        if rep > 0:                               # 반복 퀘스트: 같은 시각 +N일로 자동 재생성(팀 태그 유지)
            import datetime as _dt
            nxt = _dt.datetime.fromtimestamp(due)
            now_ts = time.time()
            while nxt.timestamp() <= now_ts + 60:  # 서버 정지 등으로 밀렸으면 미래 첫 회차까지 스킵
                nxt += _dt.timedelta(days=rep)
            c.learn_next_at = nxt.strftime("%Y-%m-%dT%H:%M")
            c.save_template()
            try:                                   # 새 진행률 창 시작점(홈 퀘스트 카드 D-day 원천)
                _SV._report_save("quest_meta", {"started_at": now_ts, "next_at": c.learn_next_at},
                                 getattr(c, "learn_team", "") or None)
            except Exception:
                pass
        else:                                     # 목표 소진(1회 실행 · 재실행 방지)
            c.learn_next_at = ""
            c.save_template()
    except Exception:
        pass
    return True


def start_learning_scheduler(hour: int = 4):
    """검수 목표(퀘스트) 스케줄러: 관리자가 지정한 일시(Config.learn_next_at)에 학습 반영을
    1회 실행하고 목표를 소진(비움)한다. 다음 목표는 관리자가 '퀘스트 생성'으로 다시 지정.
    10분 단위 재평가라 재시작 불필요 · 서버당 1회. hour 인자는 하위호환용(미사용)."""
    global _learn_sched_started
    if _learn_sched_started:
        return
    _learn_sched_started = True

    def _loop():
        while True:
            try:
                cfg = Config.load()
                if not _run_due_batch(cfg):
                    due = next_batch_time(getattr(cfg, "learn_next_at", ""))
                    wait = 600 if not due else min(600, max(30, due - time.time()))
                    time.sleep(wait)
                    continue
                time.sleep(60)
            except Exception as e:
                print(f"  [warn] 학습 배치 실패: {e}")
                time.sleep(600)

    threading.Thread(target=_loop, daemon=True).start()


def meta_compile_run(team=None) -> dict:
    """메타컴파일러: 팀의 단계별 누적 검수 피드백을 병합·충돌정리 → 정제 지시. LEARNED 갱신.
    반환: {results:{stage:{directive,ambiguities}}} (불일치=가이드 명확화 신호 표면화)."""
    st = _SV.get_store()
    if not st:
        return {"ok": False, "error": "store unavailable"}
    cfg = Config.load()
    llm = _SV.make_text_llm(cfg, _SV.Handler.server_mock)
    try:                                           # 관리자가 끈 지시(개별 무효화)는 컴파일에서 제외
        ex = _SV.disabled_directives()
    except Exception:
        ex = set()
    raw = st.learned_by_stage(team=team, exclude=ex)
    results = {}
    for stage, text in raw.items():
        results[stage] = FL.meta_compile(llm, stage, text)
    # 컴파일된 directive 를 단계 프롬프트(LEARNED)로 반영 · raw 누적 대체
    PR.LEARNED = {k: (results.get(k, {}).get("directive") or "") for k in ("extract", "analyze", "review", "judge")}
    # 모델 귀속 라우트는 모델별 그룹으로 따로 컴파일 → 그 모델 프롬프트에만 병기
    by_model = (st.routes_by_stage_model(team=team, exclude=ex)
                if hasattr(st, "routes_by_stage_model") else {})
    model_results = {}
    for m, stages in sorted(by_model.items()):
        model_results[m] = {stage: FL.meta_compile(llm, stage, "\n".join(f"- {t}" for t in items))
                            for stage, items in stages.items()}
    PR.LEARNED_BY_MODEL = {m: {stg: (r.get("directive") or "") for stg, r in cr.items()}
                           for m, cr in model_results.items()}
    return {"ok": True, "results": results, "model_results": model_results}
