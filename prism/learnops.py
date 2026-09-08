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
from . import metaeval as ME

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

def _holdout_scope(team=None) -> str:
    """배치·오토파일럿 평가 범위: 홀드아웃(용도=eval) 정답이 있으면 'eval'(개선 단계가 못 본 셋으로 측정),
    없으면 'all' 로 되돌린다(용도를 지정한 적 없는 팀 하위호환 · 대신 누수 경고를 남긴다)."""
    st = _SV.get_store()
    rows = st.get_golden(team) if (st and hasattr(st, "get_golden")) else None
    if rows and _scope_golden(rows, "eval", st, team):
        return "eval"
    print("[learn] 평가용(용도=eval) 정답이 없어 전체 골든으로 평가합니다 · "
          "개선에 쓴 콘텐츠가 평가셋에 섞입니다(콘텐츠 관리 STEP 1에서 용도를 지정하세요)")
    return "all"

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

def ci_overlap(p_a: float, n_a: int, p_b: float, n_b: int) -> bool:
    """두 비율의 95% 신뢰구간(binomial_ci)이 겹치면 True(통계적으로 동등 · 판정 보류).
    회귀·개선 판정을 점 추정치만으로 하면 표본이 작을 때 우연한 등락을 실제 변화로
    오판한다 — 구간이 겹치면 '동등'으로 보고 회귀·향상 어느 쪽도 확정하지 않는다."""
    from . import quality as Q
    lo_a, hi_a = Q.binomial_ci(p_a or 0.0, int(n_a or 0))
    lo_b, hi_b = Q.binomial_ci(p_b or 0.0, int(n_b or 0))
    return lo_a <= hi_b and lo_b <= hi_a

def _clean_intent(vals, display_name: str) -> tuple:
    """골든 기대 인텐트를 사전(D.intent_categories_for) 화이트리스트로 정제.
    반환 (통과값 리스트, 드롭된 원값 리스트). 표기 흔들림('속보 · 단신')은 agents 의
    _canon 과 같은 규칙으로 흡수하고, 사전에 없는 값만 떨군다(오타 조용한 유입 차단)."""
    import re as _re
    from . import dictionaries as D

    def canon(x):
        return _re.sub(r"\s*·\s*", "·", str(x).strip())
    if isinstance(vals, str):
        vals = [vals]
    elif isinstance(vals, dict):
        vals = list(vals.values())
    elif not isinstance(vals, (list, tuple)):
        vals = [] if vals is None else [vals]
    cmap = {canon(v): v for v in D.intent_categories_for(display_name or "")}
    ok, bad = [], []
    for x in vals:
        raw = str(x).strip() if x is not None else ""
        if not raw:
            continue
        hit = cmap.get(canon(raw))
        if hit is None:
            bad.append(raw)
        elif hit not in ok:                      # 중복 제거 · 순서(대표 첫 번째) 보존
            ok.append(hit)
    return ok, bad


def register_golden(uid, team, rows, email="", merge=False) -> dict:
    """관리자가 팀 골든셋 등록. merge=True 면 기존에 병합(upsert), False 면 전체 교체.
    등록 전 검증·정규화: finalGrade G|R 강제, content_category 사전 스냅, title 필수,
    intent 사전 화이트리스트 정제(사전 밖 값은 드롭 + 경고 · 행 자체는 살린다)."""
    from . import dictionaries as D
    st = _SV.get_store()
    if _SV._supa() and not _SV.is_admin_user(uid, team, email):   # 로컬(sqlite)은 개방(타 관리자 라우트와 동일 게이트)
        return {"ok": False, "error": "관리자 전용입니다"}
    valid, skipped = [], 0
    intent_dropped, intent_samples = 0, []
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
        ents = exp.get("entities") or []
        ents = ents if isinstance(ents, (list, tuple)) else [ents]
        exp["entities"] = list(dict.fromkeys(
            s for s in (str(x).strip() for x in ents if x is not None) if s))
        if "intent" in exp:                       # 키가 없으면 그대로 없음(측정 표본 제외 유지)
            kept, bad = _clean_intent(exp.get("intent"),
                                      content.get("displayServiceName", ""))
            exp["intent"] = kept
            if bad:
                intent_dropped += len(bad)
                intent_samples.extend(bad)
        valid.append({"content": content, "expected": exp})
    n = st.register_golden(team, valid, replace=not merge, source="manual")
    _SV._agg_bump()
    out = {"ok": True, "count": n, "skipped": skipped, "merged": bool(merge)}
    if intent_dropped:                            # 오타 조용한 유입 방지: 등록 응답에 경고 노출
        seen = list(dict.fromkeys(intent_samples))[:10]
        out["intent_dropped"] = intent_dropped
        out["intent_dropped_values"] = seen
        out["warning"] = f"사전에 없는 인텐트 {intent_dropped}건 제외: " + " · ".join(seen)
    return out

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
        grade = qm.get("finalGrade", "")
        cats = [c for c in (im.get("content_category") or []) if c and c != "Unclassified"]
        # R 은 하네스가 아이템 메타를 폐기(harness._assemble)해 분류가 영구 공백이다 → 분류 요건 면제.
        # 요구하면 기대 R 행이 한 건도 골든에 못 들어가 유해 미탐률 분모가 늘 0(측정 불가)이 된다.
        if not cats and grade != "R":                 # 카테고리 공백 → 골든 미확정(채워야 함)
            no_cat += 1
            need_list.append({"hash": ch, "title": content.get("title", ""),
                              "service": content.get("displayServiceName", "")})
            continue
        exp = {"finalGrade": grade, "reasons": qm.get("reasons", []) or []}
        if cats:                                      # 메타 키는 있을 때만 — 빈 기대는 채점 분모에서 빠진다(metaeval.meta_tally)
            exp.update({"intent": im.get("intent", []) or [], "content_category": cats,
                        "summary": im.get("summary", ""), "entities": im.get("entities", []) or []})
        entries.append({"hash": ch, "content": content, "expected": exp})
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

def promotion_pending(team=None) -> dict:
    """반영 대기 집계(읽기 전용 · 쓰기 없음): 다음 학습 반영 때 승격될 수와 승격을 막는
    사유 분해. 분류 규칙은 build_golden_from_reviews(승격)·reviewops.final_review_queue(큐)와
    동일해야 한다 — 셋이 갈라지면 '골든도 큐도 아닌' 영구 미확정이 생긴다(테스트로 정합 고정).
    배경(운영 2026-08-06): 검수 488건 vs 정답 122건. 학습 반영이 7/22 이후 멈췄는데 그
    사실이 어디에도 안 보여 '검수가 반영이 안 된다'로 읽혔다. 반환:
    {promote(승격 대기 · 골든 제외), split(의견 갈림 → 최종검수), no_grade, no_cat,
     base_fix(수정필요 일방 합의 → 기초 검수 교정 몫)}"""
    from .store import content_hash
    st = _SV.get_store()
    if not (st and hasattr(st, "feedback_map")):
        return {}
    rows = _SV.results_rows(team=team)
    try:
        fmap = _SV.feedback_map_cached(team)          # 현황 조회 전용 · 원격 30s 캐시
    except Exception:
        fmap = {}
    weights = _SV.reviewer_weights(team, fmap=fmap)
    min_good = max(1, int(getattr(Config.load(), "golden_min_good", 1) or 1))
    try:
        finals = _SV.final_verdicts(team)
    except Exception:
        finals = {}
    try:
        golden = st.golden_hashes(team)
    except Exception:
        golden = set()
    out = {"promote": 0, "split": 0, "no_grade": 0, "no_cat": 0, "base_fix": 0}
    seen = set()
    for r in rows:
        ref = r.get("content_ref") or {}
        content = {"displayServiceName": ref.get("displayServiceName", ""), "title": ref.get("title", ""),
                   "subtitle": ref.get("subtitle", ""), "body": ref.get("body", "")}
        ch = content_hash(content)
        if ch in seen:
            continue
        seen.add(ch)
        fb = fmap.get(ch)
        if not fb or ch in golden:                    # 기초 검수 없음 · 이미 골든 → 대기 아님
            continue
        fv = (finals.get(ch) or {}).get("verdict")
        if fv == "bad":                               # 리드가 '제외' 확정 → 대기 아님(결정 완료)
            continue
        gw = sum(weights.get(v.get("reviewer_id") or v.get("reviewer"), 1.0)
                 for v in fb.get("verdicts", []) if v.get("verdict") == "good")
        bw = sum(weights.get(v.get("reviewer_id") or v.get("reviewer"), 1.0)
                 for v in fb.get("verdicts", []) if v.get("verdict") == "bad")
        agreed = fv == "good" or (fb.get("good", 0) >= min_good and gw > bw)
        grade = (r.get("quality_meta") or {}).get("finalGrade", "")
        grade_ok = grade in ("G", "R")
        cats = [c for c in ((r.get("item_meta") or {}).get("content_category") or [])
                if c and c != "Unclassified"]
        if agreed and grade_ok and (cats or grade == "R"):   # R 은 분류 요건 면제(승격 게이트와 동일)
            out["promote"] += 1
        elif agreed and not grade_ok:
            out["no_grade"] += 1
        elif agreed:
            out["no_cat"] += 1
        elif fb.get("good") and fb.get("bad"):
            out["split"] += 1
        else:
            out["base_fix"] += 1
    return out


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


_COMPARE_MAX_MODELS = 6                               # 한 번에 비교하는 모델 수 상한(라우터 부하 · 표 폭)
_COMPARE_MAX_ROWS = 200                               # 모델당 골든 건수 상한


def _compare_item(row: dict, outs_by_model: dict) -> dict:
    """건 1개의 정답 vs 모델별 산출(등급·사유·정오) · 화면의 건별 비교표 행."""
    from .store import content_hash
    c = row.get("content") or {}
    exp = row.get("expected") or {}
    want_g = exp.get("finalGrade") or ""
    want_r = sorted(exp.get("reasons") or [])
    got = {}
    for model, out in outs_by_model.items():
        if out is None:
            got[model] = {"grade": "", "reasons": [], "ok": False, "empty": True}
            continue
        qm = out.get("quality_meta") or {}
        g = qm.get("finalGrade") or ""
        got[model] = {"grade": g, "reasons": sorted(qm.get("reasons") or []),
                      "ok": (g == want_g), "empty": False}
    grades = {v["grade"] for v in got.values()}
    return {"hash": content_hash(c), "title": (c.get("title") or "")[:60],
            "expected": {"grade": want_g, "reasons": want_r}, "got": got,
            "all_ok": all(v["ok"] for v in got.values()),
            "split": len(grades) > 1}               # 모델끼리 등급이 갈린 건


def _compare_prepare(models, team, scope: str):
    """골든 준비 + 모델별 클라이언트 라우팅. 실패면 (None, 오류 dict)."""
    st = _SV.get_store()
    if not (st and hasattr(st, "get_golden")):
        return None, {"ok": False, "error": "골든셋을 지원하지 않는 저장소"}
    rows = st.get_golden(team)
    if not rows:
        return None, {"ok": False, "error": "골든셋이 비어 있습니다 · 검수로 '정확' 확정분을 쌓으세요"}
    rows = _scope_golden(rows, scope, st, team)
    if not rows:
        return None, {"ok": False, "error": "평가용으로 지정된 콘텐츠의 정답이 없습니다 · 콘텐츠 관리 STEP 1에서 용도를 지정하세요"}
    cfg = Config.load()
    cand = list(dict.fromkeys(m for m in (models or []) if m)) or [cfg.model]
    if len(cand) > _COMPARE_MAX_MODELS:
        return None, {"ok": False, "error": f"한 번에 {_COMPARE_MAX_MODELS}개까지 비교할 수 있습니다"}
    rows = rows[:_COMPARE_MAX_ROWS]
    ready, skipped = [], []
    for model in cand:
        llm, route = _SV.llm_for_model(model, _SV.Handler.server_mock)
        if llm is None:
            skipped.append({"model": model, "reason": route})
        else:
            ready.append((model, llm, route))
    if not ready:
        return None, {"ok": False, "error": "호출 가능한 모델이 없습니다 · API 키(Upstage/라우터)를 확인하세요",
                      "skipped": skipped, "golden_n": len(rows)}
    return {"rows": rows, "ready": ready, "skipped": skipped, "cfg": cfg, "scope": scope}, None


_COMPARE_CHUNK = 8                                    # 진척도 갱신 주기(8-way 한 바퀴 · 창에서 막대가 자주 움직이게)


def _compare_run_model(item, rows, cfg, progress=None):
    """모델 1개를 청크 단위로 돌려 (지표 dict, 산출 list). progress(done) 로 진척을 알린다."""
    from . import abtest
    from . import harness as H
    model, llm, route = item
    meth = H.Methodology(name=model)
    outs = []
    for i in range(0, len(rows), _COMPARE_CHUNK):
        outs += abtest.run_methodology(rows[i:i + _COMPARE_CHUNK], meth, llm, concurrency=8)
        if progress:
            progress(len(outs))
    m = abtest.score(rows, outs)
    gate = float(getattr(cfg.thresholds, "eval_gate", 0.85) or 0.85)
    meta_gate = float(getattr(cfg.thresholds, "meta_gate", 0.6) or 0.6)
    keep = ("grade_accuracy", "reason_jaccard", "reason_exact_match", "empty_rate", "harm_miss_rate",
            "cost_usd", "tokens", "latency_p50_ms", "latency_p95_ms",
            "intent_n", "intent_f1", "cat_n", "cat_f1", "cat_hf1", "ent_n", "ent_f1", "ent_f1_partial",
            "summary_n", "summary_sim")
    return {"model": model, "route": route, "real": (not llm.mock), "n": len(rows),
            **{k: m.get(k) for k in keep}, **ME.overall(m, gate, meta_gate), "issues": ME.diagnose(m)}, outs


def _compare_finish(prep: dict, done: list, team) -> dict:
    """모델별 (지표, 산출) → 순위 · 건별 비교 · 영속. 동기·백그라운드 공용."""
    cfg, rows = prep["cfg"], prep["rows"]
    out = [d[0] for d in done]
    outs_by = {d[0]["model"]: d[1] for d in done}
    items = [_compare_item(row, {m: outs_by[m][i] for m in outs_by}) for i, row in enumerate(rows)]
    # 순위 = 게이트 통과 먼저 · 그다음 종합 점수(등급 0.4 + 인텐트·카테고리·엔티티·리드문 각 0.15)
    out.sort(key=lambda r: (not r.get("passed"), -(r.get("overall") or 0), -(r.get("grade_accuracy") or 0)))
    stage_prompts = dict(cfg.stage_prompts or {})
    for r in out:                                 # 이미 프롬프트에 들어간 지시는 '반영됨' 표시
        for it in r.get("issues") or []:
            it["applied"] = it["directive"] in (stage_prompts.get(it["stage"]) or "")
    gate = float(getattr(cfg.thresholds, "eval_gate", 0.85) or 0.85)
    res = {"ok": True, "models": out, "skipped": prep["skipped"],
           "best": out[0]["model"], "golden_n": len(rows), "scope": prep["scope"], "ts": time.time(),
           "eval_gate": gate, "meta_gate": float(getattr(cfg.thresholds, "meta_gate", 0.6) or 0.6),
           "cheapest_passing": cheapest_passing_model(out, gate),
           "items": items,
           "split_n": sum(1 for it in items if it["split"]),
           "miss_n": sum(1 for it in items if not it["all_ok"])}
    try:
        _SV.get_store().save_report("model_compare", res, team)
    except Exception as e:                        # 영속 실패는 비교 결과 자체를 막지 않는다
        print(f"  [compare] 결과 저장 실패: {e}")
    return res


def compare_models_on_golden(models=None, team=None, scope: str = "all") -> dict:
    """골든셋(사람 확정 정답)을 여러 모델에 실호출로 돌려 정합성 비교 → 최적 모델 선택 근거(동기).
    모델들은 동시에 돌린다(모델 간 병렬 · 모델 안 8-way). 결과는 reports(model_compare) 에 영속."""
    from concurrent.futures import ThreadPoolExecutor
    prep, err = _compare_prepare(models, team, scope)
    if err:
        return err
    with ThreadPoolExecutor(max_workers=max(1, min(4, len(prep["ready"])))) as ex:
        done = list(ex.map(lambda it: _compare_run_model(it, prep["rows"], prep["cfg"]), prep["ready"]))
    return _compare_finish(prep, done, team)


# ── 백그라운드 큐: 모델별 진척도를 보이며 돌린다(별도 창 /vendor/compare-window.html 이 폴링) ──
# ponytail: 잡은 프로세스 메모리(최근 20건) · 재시작하면 사라진다 · 이력이 필요해지면 eval_runs 처럼 테이블로
_COMPARE_JOBS: dict = {}
_COMPARE_LOCK = threading.Lock()
_COMPARE_SEQ = [0]
_COMPARE_KEEP = 20


def _job_public(j: dict, with_result: bool) -> dict:
    d = {k: j[k] for k in ("id", "ts", "scope", "status", "golden_n", "skipped", "error", "finished")}
    d["models"] = {m: dict(v) for m, v in j["models"].items()}
    if with_result and j.get("result"):
        d["result"] = j["result"]
    return d


def compare_start(models, team=None, scope: str = "all") -> dict:
    """비교를 백그라운드로 시작 → {ok, id}. 진척은 compare_status(id) 로 본다."""
    from concurrent.futures import ThreadPoolExecutor
    prep, err = _compare_prepare(models, team, scope)
    if err:
        return err
    with _COMPARE_LOCK:
        _COMPARE_SEQ[0] += 1
        jid = _COMPARE_SEQ[0]
        job = {"id": jid, "ts": time.time(), "team": team or "", "scope": scope, "status": "running",
               "golden_n": len(prep["rows"]), "skipped": prep["skipped"], "error": "", "finished": None,
               "models": {m: {"done": 0, "total": len(prep["rows"]), "status": "queued", "route": r}
                          for m, _, r in prep["ready"]}, "result": None}
        _COMPARE_JOBS[jid] = job
        for old in sorted(_COMPARE_JOBS)[:-_COMPARE_KEEP]:
            _COMPARE_JOBS.pop(old, None)

    def _one(item):
        pm = job["models"][item[0]]
        pm["status"] = "running"
        def _prog(n):
            pm["done"] = n
        r = _compare_run_model(item, prep["rows"], prep["cfg"], _prog)
        pm["status"] = "done"
        return r

    def _run():
        try:
            with ThreadPoolExecutor(max_workers=max(1, min(4, len(prep["ready"])))) as ex:
                done = list(ex.map(_one, prep["ready"]))
            job["result"] = _compare_finish(prep, done, team)
            job["status"] = "done"
        except Exception as e:                    # 무음 실패 방지: 창에 사유를 보인다
            job["status"] = "failed"
            job["error"] = str(e)[:300]
            for pm in job["models"].values():
                if pm["status"] != "done":
                    pm["status"] = "failed"
        job["finished"] = time.time()

    threading.Thread(target=_run, name=f"prism-compare-{jid}", daemon=True).start()
    return {"ok": True, "id": jid, "models": list(job["models"]), "golden_n": len(prep["rows"]),
            "skipped": prep["skipped"]}


def compare_status(job_id, team=None) -> dict:
    j = _COMPARE_JOBS.get(int(job_id or 0))
    if not j or (j["team"] or "") != (team or ""):
        return {"ok": False, "error": "그런 비교 작업이 없습니다(서버 재시작으로 사라졌을 수 있음)"}
    return {"ok": True, "job": _job_public(j, with_result=True)}


def compare_jobs(team=None) -> dict:
    """이 팀의 최근 비교 작업 목록(최신순 · 결과 본문 없이)."""
    js = [_job_public(j, False) for j in _COMPARE_JOBS.values() if (j["team"] or "") == (team or "")]
    return {"ok": True, "jobs": sorted(js, key=lambda d: -d["id"])}


def last_model_compare(team=None) -> dict:
    """마지막 모델 비교 결과(영속분) · 없으면 ok=False."""
    st = _SV.get_store()
    rep = st.get_report("model_compare", team) if (st and hasattr(st, "get_report")) else None
    return rep if rep else {"ok": False, "error": "저장된 비교 결과가 없습니다"}

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

MIN_INTENT_N = 20                                # 인텐트 스칼라 가드 최소 측정 표본
INTENT_JACCARD_DROP = 0.05                       # 인텐트 자카드 허용 악화 폭(초과 시 회귀)


def _batch_regressions(pre: dict, post: dict, min_bucket_n: int = 5,
                       min_intent_n: int = MIN_INTENT_N) -> list:
    """개선 후 평가가 전보다 나빠진 지점 목록(원복 사유 문구 · 없으면 빈 목록).
    ① 정합성 2%p 초과 악화 AND 두 신뢰구간(ci_overlap) 비중첩(표본 노이즈로 겹치면 동등 처리 ·
       n(evaluated) 없는 구 리포트는 CI 판단 불가라 종전처럼 점 추정치만으로 판정)
    ② 유해 미탐률(harm_miss_rate) 악화 — 단 어느 한쪽이라도 None(기대 R 행 0 = **측정 불가**)이면
       비교하지 않는다. 0.0 으로 읽으면 '악화 없음'이 되어 가드가 영원히 안 걸리고,
       유해 축을 아예 못 잰 런이 조용히 통과한다.
    ③ 버킷별 정합성 10%p 초과 하락(표본 min_bucket_n 이상 버킷만 · 소표본 노이즈 배제)
    ④ 인텐트 자카드 5%p 초과 악화(측정 표본 min_intent_n 이상일 때만)
    ⑤ 인텐트 값별 F1 10%p 초과 하락(support min_bucket_n 이상 · ③과 동일 규칙)
    ⑥ 카테고리 F1·엔티티 F1·리드문 유사도 5%p 초과 악화(표본 min_intent_n 이상 · ④와 동일 규칙 ·
       오토파일럿 목표·종합 점수가 읽는 5축 전부를 원복 가드도 읽게).
    cli tune RegressionGuard 를 서버 자동 배치로 이식(단일 스칼라 가드의 사각 해소).

    ④ 임계 근거: 인텐트 측정 표본은 골든 전체가 아니라 '기대 인텐트가 달린 행'뿐이라
    등급(①·전건 분모)보다 작다. 표본 n 에서 한 건이 완전히 뒤집히면 평균 자카드는
    1/n 만큼 움직이므로, n≥20 · 임계 5%p 조합이면 단일 건의 최대 변동(5%p)이 단독으로는
    가드를 넘기지 못한다 — 소표본 노이즈로 인한 오탐 원복을 막는 최소 조합.
    ⑤ 는 값별(다중 라벨) 지표라 기존 버킷 가드 ③ 과 같은 임계·같은 최소 표본을 쓴다
    (운영자가 기억할 임계를 늘리지 않는다). intent_exact 는 다중 라벨 전부일치라
    한 값만 어긋나도 0/1 로 튀는 고분산 지표이므로 리포트만 하고 가드로는 쓰지 않는다."""
    out = []
    try:
        d = round((post.get("grade_accuracy") or 0.0) - (pre.get("grade_accuracy") or 0.0), 4)
    except (TypeError, ValueError):
        return out
    n_pre, n_post = int(pre.get("evaluated") or 0), int(post.get("evaluated") or 0)
    # n 없는 구 리포트는 CI 판단 불가 → 종전처럼 점 추정치만으로 판정(하위호환 · ④·⑤와 같은 규칙)
    if d < -0.02 and not (n_pre and n_post
                          and ci_overlap(pre.get("grade_accuracy") or 0.0, n_pre,
                                         post.get("grade_accuracy") or 0.0, n_post)):
        out.append(f"정합성 {d:+.1%} 악화")
    pre_miss, post_miss = pre.get("harm_miss_rate"), post.get("harm_miss_rate")
    if pre_miss is not None and post_miss is not None:      # None = 기대 R 행 0(측정 불가) → 비교 안 함
        pre_miss, post_miss = float(pre_miss), float(post_miss)
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
    # ④·⑤ 인텐트 · 구 리포트(키 없음)는 표본 0 으로 읽혀 자동 skip(하위호환)
    pre_in = int(pre.get("intent_n") or 0)
    post_in = int(post.get("intent_n") or 0)
    if pre_in >= min_intent_n and post_in >= min_intent_n:
        try:
            dj = round(float(post.get("intent_jaccard") or 0.0)
                       - float(pre.get("intent_jaccard") or 0.0), 4)
        except (TypeError, ValueError):
            dj = 0.0
        if dj < -INTENT_JACCARD_DROP:
            out.append(f"인텐트 일치 {dj:+.1%} 악화(n={post_in})")
        post_i = post.get("by_intent_value") or {}
        for v, pv in (pre.get("by_intent_value") or {}).items():
            if int((pv or {}).get("n") or 0) < min_bucket_n:
                continue
            bf = float((pv or {}).get("f1") or 0.0)
            cf = float((post_i.get(v) or {}).get("f1", bf))
            if cf < bf - 0.10:
                out.append(f"인텐트 {v} F1 {bf:.0%}→{cf:.0%} 회귀")
    for k in ("cat_hf1", "ent_f1", "summary_sim"):                       # ⑥ 메타 나머지 축
        nk = ME._FIELD_N[k]
        if int(pre.get(nk) or 0) < min_intent_n or int(post.get(nk) or 0) < min_intent_n:
            continue
        dm = round(float(post.get(k) or 0.0) - float(pre.get(k) or 0.0), 4)
        if dm < -INTENT_JACCARD_DROP:
            out.append(f"{ME.FIELD_KO[k]} {dm:+.1%} 악화(n={int(post.get(nk) or 0)})")
    return out


def learning_batch(team=None, models=None, model: str = "") -> dict:
    """배치 학습: ① 정확분 골든 축적(평가 셋 고정) ② 개선 전 회귀 점수 ③ 피드백 병합→프롬프트 개선
    ④ 개선 후 회귀 점수 → 전/후 delta 기록. 정합성 2%p 초과 악화·유해 미탐 악화·버킷 회귀
    중 하나라도 걸리면 개선을 반영하지 않고 이전 프롬프트를 유지한다(방향 검증 · 진동 방지)."""
    try:                                       # 퀘스트 완주 보상: 반영 시점, 기한 내 배정 완주자 +100P(회차당 1회 · 멱등)
        quest_bonus = _SV.award_quest_bonus(team)
    except Exception:
        quest_bonus = None
    golden = build_golden_from_reviews(team)             # 전/후를 같은 정답셋으로 재도록 먼저 고정
    prev_learned = dict(PR.LEARNED)
    prev_by_model = {m: dict(v) for m, v in (PR.LEARNED_BY_MODEL or {}).items()}
    scope = _holdout_scope(team)                        # 전/후를 개선이 못 본 홀드아웃으로 잰다(없으면 전체)
    eval_pre = eval_golden(team, model=model, scope=scope)   # 개선 전(현행 프롬프트) 점수 · model 비면 기본 텍스트 슬롯
    improve = meta_compile_run(team)
    changed = (PR.LEARNED != prev_learned) or (PR.LEARNED_BY_MODEL != prev_by_model)
    delta = None
    if changed and eval_pre.get("ok"):
        evalr = eval_golden(team, model=model, scope=scope)   # 개선 후 점수(같은 셋 · 같은 모델)
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
              "quest_bonus": quest_bonus,       # 완주 보너스 지급 결과(수령자·회차) — 리포트로 추적
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
                       "confirmed": golden.get("confirmed"), "ts": report["ts"]}, team=team)   # 팀 스코프 브로드캐스트(교차팀 유출 차단)
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
                        reaps = st.get_reap(ch, team=team)     # 팀 스코프: 타 팀 검수자 사유 혼입 차단
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
                              for r in st.get_reap(ch, team=team)]   # 팀 스코프(핸드오프 번들 유출 차단)
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
        # 범용②(형식·전달 8종)는 서비스와 무관하게 항상 프롬프트에 주입된다
        # (meta_prompts.intent_dictionary_text) → 빠지면 '포토·영상 중심' 등이 라벨 공간에서
        # 통째로 사라져 학습 재현이 안 된다.
        "intent_form_universal": list(getattr(D, "INTENT_FORM_UNIVERSAL", []) or []),
        "intent_by_service": {k: list(v) for k, v in
                              (getattr(D, "INTENT_CATEGORIES_BY_SERVICE", {}) or {}).items()},
        # 값 정의문도 재현 조건: 서비스 분기 값은 설명이 프롬프트에 함께 들어가고(동 함수),
        # 범용①·②는 검수 화면 정의(/dict intentDefs)와 같은 병합본이 라벨 판단 근거였다.
        "intent_defs": dict(getattr(D, "INTENT_VALUE_DEFS", {}) or {}),
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
    seq = int(log[-1]["seq"]) + 1 if log else 1     # 마지막 항목 파생(len 기반은 로그 트림·삭제 시 고착·역행)
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

def next_batch_time(next_at) -> float:
    """검수 목표(퀘스트) 일시('YYYY-MM-DDTHH:MM' 로컬) → epoch. 미지정·형식 오류는 0."""
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


def start_learning_scheduler():
    """검수 목표(퀘스트) 스케줄러: 관리자가 지정한 일시(Config.learn_next_at)에 학습 반영을
    1회 실행하고 목표를 소진(비움)한다. 다음 목표는 관리자가 '퀘스트 생성'으로 다시 지정.
    10분 단위 재평가라 재시작 불필요 · 서버당 1회."""
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
                try:
                    from . import alerts as AL
                    AL.on_batch_fail(e)            # 조용한 학습 중단 방지(웹훅 미설정 시 무동작)
                except Exception:
                    pass
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


def builder_compile(spec: dict, team=None) -> dict:
    """선언형 스펙 → 대상 모델 계열별 시스템 프롬프트 컴파일(Atelier step1 이식).
    입력: {role, task(필수), background, constraints, format, examples, families[]}.
    각 계열의 공식 쿡북 distilled 가이드(model_guides.PROMPTING_GUIDES)를 메타 호출에
    주입해 한 번의 호출로 계열별 프롬프트를 생성한다. 적용은 기존 /config 경로
    (stage_prompts)를 재사용 — 빌더는 '만드는 곳', 반영 경로는 단일 소스 유지."""
    from . import model_guides as MG
    spec = spec or {}
    task = (spec.get("task") or "").strip()
    if not task:
        return {"ok": False, "error": "과업(무엇을 하는 프롬프트인지)을 입력하세요"}
    families = [f for f in (spec.get("families") or []) if f in MG.PROMPTING_GUIDES]
    if not families:
        return {"ok": False, "error": "대상 모델 계열을 하나 이상 선택하세요"}
    cfg = Config.load()
    meta_model = (spec.get("meta_model") or "").strip()
    if meta_model:                               # ② 메타 컴파일러 모델 지정(미지정=현재 설정 모델)
        llm, route = _SV.llm_for_model(meta_model, _SV.Handler.server_mock)
        if llm is None:
            return {"ok": False, "error": f"메타 컴파일러 모델 호출 불가({route}): {meta_model}"}
    else:
        llm = _SV.make_text_llm(cfg, _SV.Handler.server_mock)
    parts = [f"## 과업\n{task}"]
    for key, label in (("role", "역할"), ("background", "배경·의도"),
                       ("constraints", "제약(반드시 지킬 것)"), ("format", "출력 형식"),
                       ("examples", "예시(입력 → 기대 출력)")):
        v = (spec.get(key) or "").strip()
        if v:
            parts.append(f"## {label}\n{v[:2000]}")
    guides = "\n\n".join(MG.render_guide_block(f) for f in families)
    fam_list = ", ".join(families)
    system = ("당신은 프롬프트 엔지니어링 컴파일러입니다. 사용자의 선언형 스펙을 읽고 "
              "대상 모델 계열별로 그 계열의 공식 가이드에 최적화된 시스템 프롬프트를 "
              "각각 작성합니다. 스펙에 없는 요구를 지어내지 않습니다. 반드시 JSON 만 출력합니다.")
    user = f"""# 선언형 스펙

{chr(10).join(parts)}

# 계열별 공식 가이드 (이 규칙대로 각 계열 프롬프트를 최적화)

{guides}

# 출력 형식 (반드시 이 JSON 만 · 계열 키: {fam_list})

{{"prompts": {{"<계열>": "<그 계열용 완성 시스템 프롬프트>"}}, "notes": "<계열 간 차이 요약 1~2문장>"}}"""
    try:
        data, res = llm.complete_json(system, user, tag="builder")
    except Exception as e:
        return {"ok": False, "error": f"컴파일 실패: {e}"}
    got = data.get("prompts") or {}
    prompts = {f: str(got.get(f) or "").strip() for f in families}
    if not any(prompts.values()):
        return {"ok": False, "error": "컴파일 결과가 비었습니다 · 모델 설정(API 키)을 확인하세요"}
    return {"ok": True, "prompts": prompts, "notes": str(data.get("notes") or "")[:500],
            "families": families,
            "cost_usd": round(getattr(res, "cost_usd", 0.0) or 0.0, 6)}


def builder_test(system: str, user_input: str, model: str = "", team=None) -> dict:
    """빌더 산출 프롬프트를 지정 테스트 모델로 1회 실행해 원문 출력 확인(적용 전 검증).
    JSON 강제 없이 자유 텍스트로 받는다 — 형식 준수 여부 자체가 관찰 대상."""
    system = (system or "").strip()
    user_input = (user_input or "").strip()
    if not system:
        return {"ok": False, "error": "테스트할 시스템 프롬프트가 없습니다 · 먼저 컴파일하세요"}
    if not user_input:
        return {"ok": False, "error": "샘플 입력을 넣어주세요"}
    cfg = Config.load()
    used = (model or "").strip()
    if used:
        llm, route = _SV.llm_for_model(used, _SV.Handler.server_mock)
        if llm is None:
            return {"ok": False, "error": f"테스트 모델 호출 불가({route}): {used}"}
    else:
        llm = _SV.make_text_llm(cfg, _SV.Handler.server_mock)
        used = cfg.model or getattr(llm, "model", "") or ""
    try:
        res = llm.complete_text(system[:12000], user_input[:6000], tag="builder-test")
    except Exception as e:
        return {"ok": False, "error": f"테스트 실행 실패: {e}"}
    return {"ok": True, "model": used, "output": (res.text or "")[:6000],
            "latency_ms": res.latency_ms,
            "cost_usd": round(getattr(res, "cost_usd", 0.0) or 0.0, 6),
            "tokens": {"in": res.in_tok, "out": res.out_tok}}
