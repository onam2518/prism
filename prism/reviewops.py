"""검수 도메인 (serve 에서 분리 · 라우트 분리 4차 · 로드맵 2단계 2차).

1층 검수(판정 apply_feedback · 대기열 review_queue · 대상 목록 raw_rows · 교정
patch_content_meta · 이력/초안 content_history/drafts_for · 모델 현황 model_stats),
2층 최종검수(final_review_queue · set_final_verdict · 역할 reviewer_roles), 배정
(distribute_assignments · assign_log), 게임화(arena · 미션 · 배지 · 검수자 등록)를 담당.
HTTP 디스패치는 serve 가 유지.

컴포지션: 서버 환경(스토어·결과 뷰·집계 캐시·리포트 영속·SSE 방송·mock 플래그)은
serve 가 기동 시 `_SV`(자기 모듈 객체)로 주입(learnops 관례 · 순환 import 없음).
테스트가 serve.get_store·serve._inject_gold·serve.rerun_unconfirmed 등을 몽키패치하므로
그 이름들의 호출은 이동 후에도 `_SV.` 경유가 계약이다.
"""
from __future__ import annotations

import heapq
import threading
import time

from . import feedback_loop as FL
from . import learnops as LO
from . import prompts as PR
from .config import Config

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입


def distribute_assignments(st, hashes, reviewers, min_reviewers=1, team=None) -> dict:
    """선택 콘텐츠를 선택 인원에게 균등 분배 배정(덮어쓰기).
    시작 부하 = 검수자별 미완료 배정 수(assignment_load) → 항상 부하가 가장 적은
    사람부터 채워 최종 부하가 고르게 되도록 한다. 콘텐츠당 담당 min_reviewers 명
    (서로 다른 사람)씩 배정하고 통과 인원 N 도 같은 값으로 둔다.
    반환: {"n": 처리 건수, "per_reviewer": {reviewer: 배정 건수}, "min_reviewers": N}"""
    hs = [h for h in dict.fromkeys(hashes or []) if h]
    rvs = [r for r in dict.fromkeys(reviewers or []) if r]
    if not (hs and rvs):
        return {"n": 0, "per_reviewer": {}, "min_reviewers": 0}
    n_per = max(1, min(len(rvs), int(min_reviewers or 1)))
    load = {}
    if hasattr(st, "assignment_load"):
        try:
            load = st.assignment_load(team=team) or {}
        except Exception:
            load = {}                                    # 부하 조회 실패 시 0 부하로 분배(배정은 계속)
    heap = [(int(load.get(r, 0)), i, r) for i, r in enumerate(rvs)]   # i = 동률 시 선택 순서 유지
    heapq.heapify(heap)
    groups = {}                                          # 담당 조합(tuple) → 콘텐츠 목록(호출 최소화)
    for h in hs:
        picked = [heapq.heappop(heap) for _ in range(n_per)]
        groups.setdefault(tuple(p[2] for p in picked), []).append(h)
        for ld, i, r in picked:
            heapq.heappush(heap, (ld + 1, i, r))
    per, n = {}, 0
    for combo, chunk in groups.items():
        n += st.set_assignees_bulk(chunk, list(combo), min_reviewers=n_per, team=team)
        for r in combo:
            per[r] = per.get(r, 0) + len(chunk)
    return {"n": n, "per_reviewer": per, "min_reviewers": n_per}


# ── 리드 최종판정(의견 갈림 해소) ──────────────────────────────────────────────────
def final_verdicts(team=None) -> dict:
    """리드(슈퍼관리자 이상)가 확정한 최종판정 {hash: {verdict, by, ts}} · 의견 갈림 해소.
    reports kind='final_verdicts'(팀 스코프) · DDL 불필요 · 골든 승격에서 다수결보다 우선."""
    rep = _SV._report_get("final_verdicts", team, {}) or {}
    return dict(rep.get("items") or {})


def set_final_verdict(hash_, verdict, by="", team=None) -> dict:
    """최종판정 저장/철회(verdict 빈 값 = 철회). 검수자 개별 의견 행은 건드리지 않는다."""
    h = (hash_ or "").strip()
    if not h:
        return {"ok": False, "error": "hash 누락"}
    rep = _SV._report_get("final_verdicts", team, {}) or {}
    items = dict(rep.get("items") or {})
    if verdict in ("good", "bad"):
        items[h] = {"verdict": verdict, "by": by or "", "ts": time.time()}
    else:
        items.pop(h, None)
    _SV._report_save("final_verdicts", {"items": items}, team)
    _SV._agg_bump()
    return {"ok": True, "final": items.get(h)}


# ── 2층 검수: 최종검수자 역할 + 최종검수 큐 ─────────────────────────────────
def reviewer_roles(team=None) -> dict:
    """최종검수자 역할 {reviewer_id: 'final'} · reports kind='reviewer_roles'(팀 스코프 · DDL 불필요).
    기초검수자는 기본값(기록 없음) · 역할은 사람 단위(배정 건 단위 아님 · 혼선 방지)."""
    rep = _SV._report_get("reviewer_roles", team, {}) or {}
    return dict(rep.get("items") or {})


def set_reviewer_role(rid: str, role: str, team=None) -> dict:
    """역할 지정/해제: role='final' 지정 · 그 외 값 = 해제(기초로 복귀)."""
    rid = (rid or "").strip()
    if not rid:
        return {"ok": False, "error": "대상이 없습니다"}
    rep = _SV._report_get("reviewer_roles", team, {}) or {}
    items = dict(rep.get("items") or {})
    if role == "final":
        items[rid] = "final"
    else:
        items.pop(rid, None)
    _SV._report_save("reviewer_roles", {"items": items}, team)
    _SV._agg_bump()
    return {"ok": True, "final_reviewers": sorted(items)}


def is_final_reviewer(uid, team=None) -> bool:
    return bool(uid) and uid in reviewer_roles(team)


def final_review_queue(team=None, reviewer: str = "") -> dict:
    """최종검수 큐: 기초 검수를 거쳤지만 골든으로 확정되지 못한 미확정분만.
    대상 = ① 의견 갈림(split · 가중 다수결 미결) ② 정확 합의인데 분류 공백.
    기초 합의 기준은 build_golden_from_reviews 와 동일 · 판정은 final_verdicts(편입/제외)로."""
    from .store import content_hash
    st = _SV.get_store()
    if not st:
        return {"ok": False, "items": [], "n": 0}
    rows = _SV.results_rows(team=team)
    try:
        fmap = st.feedback_map(team=team)
    except Exception:
        fmap = {}
    weights = reviewer_weights(team)
    min_good = max(1, int(getattr(Config.load(), "golden_min_good", 1) or 1))
    finals = final_verdicts(team)
    try:
        golden = st.golden_hashes(team)
    except Exception:
        golden = set()
    out = []
    for r in reversed(rows):                       # 최근순
        ref = r.get("content_ref") or {}
        content = {"displayServiceName": ref.get("displayServiceName", ""), "title": ref.get("title", ""),
                   "subtitle": ref.get("subtitle", ""), "body": ref.get("body", "")}
        ch = content_hash(content)
        fb = fmap.get(ch)
        if not fb or ch in golden:                 # 기초 검수 없음 · 이미 골든 확정 → 대상 아님
            continue
        gw = sum(weights.get(v.get("reviewer_id") or v.get("reviewer"), 1.0)
                 for v in fb.get("verdicts", []) if v.get("verdict") == "good")
        bw = sum(weights.get(v.get("reviewer_id") or v.get("reviewer"), 1.0)
                 for v in fb.get("verdicts", []) if v.get("verdict") == "bad")
        im = r.get("item_meta") or {}
        cats = [c for c in (im.get("content_category") or []) if c and c != "Unclassified"]
        agreed = fb.get("good", 0) >= min_good and gw > bw
        if agreed and cats:                        # 정상 확정 경로(다음 학습 반영 때 승격) → 대상 아님
            continue
        if agreed and not cats:
            reason = "분류 없음"
        elif fb.get("good") and fb.get("bad"):     # 의견 갈림(가중 미결 포함)
            reason = "의견 갈림"
        else:                                      # 수정필요 일방 합의·기초 표 부족 → 기초 큐 몫
            continue
        d = _SV._detail_row(r)
        d["version"] = int((r.get("trace") or {}).get("version") or 0) or None   # 초안 프롬프트 버전(재실행 여부 식별)
        d["final_reason"] = reason
        fv = finals.get(ch) or {}
        d["final"] = fv.get("verdict", "")
        d["final_by"] = fv.get("by", "")               # 목록 = 결정 현황판: 누가 · 언제
        d["final_ts"] = fv.get("ts", 0)
        out.append(d)
        if len(out) >= 200:
            break
    out = _SV._attach_fb(out, team, reviewer)
    if reviewer and out and bool(getattr(Config.load(), "final_gold_check", True)):
        out = _inject_gold_final(out, reviewer, team)   # 골드 캘리브레이션(블라인드 · 응답은 gold_checks 로)
    return {"ok": True, "items": out, "n": len(out), "stats": _final_stats(finals)}


def _final_stats(finals: dict) -> dict:
    """최종검수자 지표: 누적 판정·편입/제외(철회분은 원장에서 빠져 자동 제외) · by = 판정자별."""
    by = {}
    for v in (finals or {}).values():
        k = (v.get("by") or "").strip() or "(미상)"
        d = by.setdefault(k, {"n": 0, "good": 0, "bad": 0})
        d["n"] += 1
        d["good" if v.get("verdict") == "good" else "bad"] += 1
    return {"total": sum(d["n"] for d in by.values()),
            "good": sum(d["good"] for d in by.values()),
            "bad": sum(d["bad"] for d in by.values()), "by": by}


def _finals_today(reviewer: str, team=None) -> int:
    """오늘 확정한 최종판정 수(미션 final1 판정용 · 철회분 제외)."""
    if not reviewer:
        return 0
    day = int(time.time() // 86400)
    return sum(1 for v in final_verdicts(team).values()
               if (v.get("by") or "") == reviewer and int(float(v.get("ts") or 0) // 86400) == day)


def _inject_gold_final(items: list, reviewer: str, team=None) -> list:
    """최종검수 큐 골드 캘리브레이션(블라인드): 기확정 골든 1건을 미확정분처럼 섞어 출제.
    변형은 기초 골드(G-1)와 동일 규칙 — hash 짝수 = 원본(정답 편입) / 홀수 = 등급 뒤집기(정답 제외).
    hash 'goldf:' 접두 → /final-verdict 가 gold_checks 로 분리 기록(final_verdicts 무오염 ·
    정확도는 gold_stats 를 타고 신뢰가중에 합류). 선택·위치는 (검수자, 일자) 시드로 결정적 ·
    응답한 문항은 재출제 안 함(기초 골드와 응답 원장 공유)."""
    st = _SV.get_store()
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
        if h not in answered and content.get("title"):
            cands.append((h, content, exp))
    if not cands:
        return items
    import hashlib as _hl
    import random as _rd
    day = int(time.time() // 86400)
    rng = _rd.Random(int(_hl.sha1(f"goldf:{reviewer}:{day}".encode()).hexdigest()[:8], 16))
    h, content, exp = cands[rng.randrange(len(cands))]
    flip = int(h, 16) % 2 == 1                     # 홀수 = 등급 뒤집기(정답 '제외')
    grade = exp.get("finalGrade", "") or "G"
    out = list(items)
    out.insert(rng.randint(0, len(out)), {
        "hash": f"goldf:{'bad' if flip else 'ok'}:{h}",
        "title": content.get("title", ""), "subtitle": content.get("subtitle", ""),
        "service": content.get("displayServiceName", ""), "url": "", "model": "",
        "body": content.get("body", ""), "summary": exp.get("summary", ""),
        "entities": exp.get("entities", []) or [], "intent": exp.get("intent", []) or [],
        "category": exp.get("content_category", []) or [],
        "grade": ("R" if grade == "G" else "G") if flip else grade,
        "reasons": exp.get("reasons", []) or [], "version": None,
        "final_reason": "의견 갈림", "final": "",
        "fb": {"n": 2, "good": 1, "bad": 1, "verdict": "", "ts": 0}})
    return out


def rerun_unconfirmed(team=None, limit: int = 100) -> dict:
    """학습 반영 직후: 미확정분(최종검수 큐 대상)을 새 버전 프롬프트로 재실행(2층 검수 3-1).
    최종검수자가 '기초 의견이 반영된 초안'으로 판정하도록 초안만 갱신 · 기초 의견 행은 불변.
    비용: 미확정 건수만큼 실호출 · batch_budget_usd 상한 준수 · 퀘스트 가드는 정당 우회
    (반영 직후 새 버전 초안 생성이 목적 · 기초 의견 수집은 이미 끝난 콘텐츠만 대상)."""
    q = final_review_queue(team)
    hashes = [i["hash"] for i in (q.get("items") or [])][:max(1, int(limit))]
    if not hashes:
        return {"ok": True, "done": 0, "failed": 0, "spent_usd": 0.0}
    want = set(hashes)
    row_by = {}
    for r in _SV.results_rows(team=team):
        ch = _row_key(r.get("content_ref") or {})
        if ch in want:
            row_by[ch] = r
    budget = float(getattr(Config.load(), "batch_budget_usd", 0.0) or 0.0)
    spent, done, failed = 0.0, 0, 0
    for ch in hashes:
        res = _SV.rerun_content(ch, "", team=team, row=row_by.get(ch), force_quest=True)
        if res.get("error"):
            failed += 1
        else:
            done += 1
            spent += float((((res.get("output") or {}).get("trace") or {}).get("cost_usd")) or 0.0)
        if budget > 0 and spent >= budget:         # 예산 상한: 남은 대상 중단
            break
    return {"ok": True, "done": done, "failed": failed, "spent_usd": round(spent, 6)}


# ── 배정 감사 추적 ───────────────────────────────────────────────────────────
def _log_assign(by: str, mode: str, n: int, reviewers: list, minr: int, team=None):
    """배정 실행 기록(reports kind='assign_log' · 상한 100): 누가 · 언제 · 어떤 방식으로 ·
    몇 건을 · 누구에게. '별도 지정 안 했는데 배정돼 있음 · 누가?'를 없애는 감사 원장."""
    try:
        rep = _SV._report_get("assign_log", team, {}) or {}
        items = list(rep.get("items") or [])
        items.append({"ts": time.time(), "by": (by or "(미상)")[:80], "mode": mode,
                      "n": int(n or 0), "reviewers": [str(r)[:40] for r in (reviewers or [])][:10],
                      "min": int(minr or 0)})
        _SV._report_save("assign_log", {"items": items[-100:]}, team)
    except Exception:
        pass


def assign_log_data(team=None) -> dict:
    """배정 이력 조회(최신순)."""
    rep = _SV._report_get("assign_log", team, {}) or {}
    return {"ok": True, "items": list(reversed(rep.get("items") or []))}


QUEST_BONUS_PT = 100        # 기한 내 배정 완주 보너스 · 검수 10건 값(미션 10~20P 대비 대형 목표감)


def award_quest_bonus(team=None) -> dict:
    """퀘스트 완주 보상: 학습 반영(배치) 시점에 '기한 내 내 배정 전량 검수'한 검수자에게
    +100P 지급. 유효 검수 판정은 퀘스트 진척(quest_team_progress)과 동일 관점(대상 스코프 ×
    현행 초안 이후 검수). 회차 키(questbonus:v{다음 버전})로 log_event_once → 회차당 1회(멱등).
    배정이 없던 검수자는 완주 기준이 없어 대상 아님(카드도 보상 줄을 배정자에게만 노출)."""
    st = _SV.get_store()
    if not (st and hasattr(st, "log_event_once") and hasattr(st, "assignees")):
        return {"ok": False, "awarded": [], "bonus": QUEST_BONUS_PT}
    try:
        dts = st.draft_times(team) if hasattr(st, "draft_times") else {}
    except Exception:
        dts = {}
    try:
        targets = (st.review_targets(team) if hasattr(st, "review_targets")
                   else st.yellow_hashes(team) if hasattr(st, "yellow_hashes") else None)
    except Exception:
        targets = None
    per = {}                                    # 검수자 → 유효 검수한 대상 집합
    for ch, e in (st.feedback_map(team=team) or {}).items():
        if targets is not None and ch not in targets:
            continue
        base = float(dts.get(ch) or 0)
        for v in e.get("verdicts") or []:
            if _fb_epoch(v.get("ts")) >= base:
                rid = v.get("reviewer_id") or v.get("reviewer") or ""
                per.setdefault(rid, set()).add(ch)
    try:
        asg = st.assignees(team=team) or {}
    except Exception:
        asg = {}
    asg = {ch: a for ch, a in asg.items() if targets is None or ch in targets}
    by_rv = {}                                  # 배정 검수자 → [완료, 배정]
    for ch, a in asg.items():
        for rv in (a.get("reviewers") or []):
            c = by_rv.setdefault(rv, [0, 0])
            c[1] += 1
            if ch in (per.get(rv) or ()):
                c[0] += 1
    try:
        ver = int(st.batch_seq(team) if hasattr(st, "batch_seq") else 0) + 1   # 이번에 반영될 버전
    except Exception:
        ver = 0
    key = f"questbonus:v{ver}"
    awarded = []
    for rv, (done, total) in sorted(by_rv.items()):
        if total and done >= total:
            try:
                if st.log_event_once(rv, key, 0, QUEST_BONUS_PT, team=team):
                    awarded.append(rv)
            except Exception:
                pass
    if awarded:
        _SV._agg_bump()                          # 아레나 점수 즉시 반영
    return {"ok": True, "awarded": awarded, "bonus": QUEST_BONUS_PT, "version": ver}


# ── 파이프라인 실행 ──────────────────────────────────────────────────────────
def apply_feedback(data: dict) -> dict:
    """콘텐츠별 평가 피드백 저장 → 학습 루프 즉시 반영. {clear:true} 면 전체 초기화."""
    st = _SV.get_store()
    if not st:
        return {"ok": False, "error": "store unavailable"}
    if data.get("clear"):
        team = data.get("_team")
        if _SV._supa() and not team:                    # 팀 스코프 없이 전 팀 삭제 금지(멀티테넌시 격리)
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
            _SV.broadcast({"type": "feedback", "hash": ch, "reviewer": disp, "verdict": "",
                       "title": data.get("title", ""), "service": data.get("service", ""), "ts": time.time()},
                      team=data.get("_team"))
            _SV._agg_bump()
            return {"ok": True, "feedback": st.feedback_stats(team=data.get("_team")),
                    "learned": {k: bool(v) for k, v in (PR.LEARNED or {}).items()}}
        # 배정 배타 검수: 지정 검수자가 있는 콘텐츠는 지정된 사람만 판정할 수 있다.
        # 생성자·관리자도 예외 없음(직접 검수하려면 콘텐츠 관리에서 배정을 수정) ·
        # 미지정 콘텐츠는 종전대로 전원 가능 · 판정 취소(위 분기)는 배정과 무관하게 허용.
        try:
            asg1 = ((st.assignees(team=data.get("_team")) or {}).get(ch)
                    if hasattr(st, "assignees") else None)
        except Exception:
            asg1 = None
        if asg1 and asg1.get("reviewers") and reviewer not in asg1["reviewers"]:
            return {"ok": False, "error": "다른 검수자에게 배정된 콘텐츠입니다 · "
                                          "직접 검수하려면 콘텐츠 관리에서 배정을 수정하세요"}
        note = (data.get("note") or "").strip()
        elements = [e for e in (data.get("elements") or []) if e in FL.ELEMENTS]
        if not elements and (data.get("element") or "").strip() in FL.ELEMENTS:
            elements = [(data.get("element") or "").strip()]         # 단일 요소 하위호환
        stage = data.get("stage") or (FL.ELEM_STAGE.get(elements[0]) if elements else "analyze") or "analyze"
        st.save_feedback(ch, data.get("service", ""), data.get("title", ""),
                         verdict, stage, note, time.time(), reviewer=reviewer,
                         team=data.get("_team"), element=",".join(elements))
        # 활동 원장(append-only): feedback 은 upsert 라 재검수 시 과거 활동이 이동 —
        # 판정 행위 시점에 일별 누적해 검수 활동 추이를 보존한다
        _SV._log_activity_rollup(team=data.get("_team"), reviews=1,
                                 corrections=(1 if verdict == "bad" else 0))
        _SV.broadcast({"type": "feedback", "hash": ch, "reviewer": disp,
                   "verdict": verdict, "title": data.get("title", ""),
                   "service": data.get("service", ""), "ts": time.time()},
                  team=data.get("_team"))
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
    _SV._agg_bump()                                    # 피드백/진척율 변경 → 집계·아레나 캐시 무효화
    out = {"ok": True, "feedback": st.feedback_stats(team=data.get("_team")),
           "learned": {k: bool(v) for k, v in (PR.LEARNED or {}).items()}}
    if not data.get("clear") and missions:
        out["missions_completed"] = missions       # 이번 행동으로 새로 달성된 미션(1회 보상)
    return out


def apply_gold_answer(data: dict) -> dict:
    """골드 문항(정답 알려진 검증 문항, Oleson 2011) 응답 처리.
    hash 형식 gold:<ok|bad>:<content_hash>. feedback 테이블은 건드리지 않는다."""
    st = _SV.get_store()
    parts = (data.get("hash") or "").split(":")
    if not (st and hasattr(st, "save_gold_check")) or len(parts) < 3:
        return {"ok": False, "error": "골드 문항 처리 불가"}
    verdict = data.get("verdict") or ""
    if not verdict:                                # 판정 취소 = 무기록
        return {"ok": True, "gold": None}
    expected = "good" if parts[1] == "ok" else "bad"
    reviewer = (data.get("reviewer") or "").strip() or "(익명)"
    correct = st.save_gold_check(parts[2], reviewer, expected, verdict, team=data.get("_team"))
    _SV._log_activity_rollup(team=data.get("_team"), gold_n=1,
                             gold_correct=(1 if correct else 0))   # 활동 원장(골드 응답)
    missions = _check_missions(reviewer, data.get("_team"))
    _SV._agg_bump()
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
MISSION_FINAL = {"id": "final1", "label": "최종 판정", "total": 1, "bonus": 20}   # 최종검수자 전용


def mission_progress(reviewer, team=None) -> list:
    """검수자별 오늘의 미션 진행도. 판정은 저장된 행동 데이터로만(자가 신고 없음).
    최종검수자에겐 final1(최종 판정 1건)이 추가된다 — 기초 검수자 목록엔 미노출."""
    st = _SV.get_store()
    if not (st and reviewer and hasattr(st, "feedback_today")):
        return []
    try:
        done = {"daily5": st.feedback_today(reviewer, team=team),
                "gold1": st.gold_today(reviewer, team=team).get("correct", 0),
                "split1": st.split_reviewed_today(reviewer, team=team),
                "fill1": (st.patches_today(reviewer, team=team) if hasattr(st, "patches_today") else 0)}
        ms = list(MISSIONS)
        if reviewer in reviewer_roles(team):
            ms.append(MISSION_FINAL)
            done["final1"] = _finals_today(reviewer, team)
    except Exception:
        return []
    out = []
    for m in ms:
        d = min(done.get(m["id"], 0), m["total"])
        out.append({**m, "done": d, "completed": d >= m["total"]})
    return out


def _check_missions(reviewer, team=None) -> list:
    """달성 미션을 이벤트 로그에 1회 기록(중복 보상 방지) → 새로 달성된 미션 목록 반환."""
    st = _SV.get_store()
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
    st = _SV.get_store()
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
        llm = _SV.make_text_llm(cfg, _SV.Handler.server_mock)   # 서버 mock 존중(키 없으면도 mock)
        st = _SV.get_store()
        routes = FL.route_feedback(llm, fb)             # 요소 재분류(mock/실패 시 선택 요소 폴백)
        if st and routes and hasattr(st, "save_routes"):
            st.save_routes(content_hash, reviewer, routes, team=fb.get("_team"),
                           model=fb.get("model", ""))
        reap = FL.run_reap(llm, fb)
        if st:
            st.save_reap(content_hash, reviewer, reap)
        # 프롬프트 즉시반영 없음(일배치 학습에서 합의 반영). plan 은 저장·브로드캐스트만.
        _SV.broadcast({"type": "reap", "hash": content_hash, "reviewer": reviewer,
                   "stage": reap.get("stage", ""), "plan": reap.get("plan", ""),
                   "ask": reap.get("ask", ""),
                   "routed": [r["element"] for r in routes]}, team=fb.get("_team"))
    except Exception as e:
        print(f"  [warn] 피드백 후처리 실패(hash={content_hash[:12]}): {e}")


def reap_for(data: dict) -> dict:
    """콘텐츠의 검수자별 REAP 산출(UI 표시)."""
    st = _SV.get_store()
    if not st:
        return {"ok": False, "items": []}
    return {"ok": True, "items": st.get_reap((data.get("hash") or "").strip())}


# ── Supabase Auth(ID/PW) · 서버 프록시 + JWT 검증(supabase 모드) ──
def register_reviewer(data: dict) -> dict:
    """검수자 등록: (인증 uid 또는 이름) + 표시명 + 캐릭터 (+ supabase 면 팀 생성/가입)."""
    st = _SV.get_store()
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
        if _SV._supa():
            st.set_reviewer(rv, name, ch)            # team_id 미전달 = 팀 유지(upsert 부분 갱신)
        elif hasattr(st, "rename_reviewer"):
            r = st.rename_reviewer(rv, name)         # sqlite: 키=이름 → 이력 키 이관
            if not r.get("ok"):
                return r
        _SV._agg_bump()                                  # 리더보드 등 집계에 새 이름 즉시 반영
        _SV.broadcast({"type": "reviewer", "reviewer": name, "char": ch},
                  team=(st.reviewer_team(rv) if (_SV._supa() and hasattr(st, "reviewer_team")) else None))
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
    if _SV._supa() and hasattr(st, "ensure_team"):
        if tmode == "none":                          # 팀 없이 가입(솔로) · 팀 생성은 관리자 메뉴
            st.set_reviewer(rv, name, ch, None)
        else:
            team = st.ensure_team(rv, tmode, data.get("team_name"), data.get("invite_code"))
            if not team:
                return {"ok": False, "error": "팀을 찾을 수 없습니다 · 초대코드를 확인하세요"}
            st.set_reviewer(rv, name, ch, team)
    else:
        st.set_reviewer(rv, name, ch)
    _SV.broadcast({"type": "reviewer", "reviewer": name, "char": ch}, team=team)
    info = st.team_info(team) if (team and hasattr(st, "team_info")) else None
    return {"ok": True, "team": info}                # info.invite_code 로 초대코드 표시


def save_badges(uid, earned) -> dict:
    """획득 배지 라벨을 서버(prism_reviewers.badges)에 영속. 최신 전체 목록 반환.
    로컬(sqlite) 모드엔 영속 테이블이 없으므로 그대로 echo(클라 localStorage 폴백)."""
    st = _SV.get_store()
    labels = [str(x) for x in (earned or []) if x]
    if st and hasattr(st, "save_badges") and uid:
        try:
            return {"ok": True, "badges": st.save_badges(uid, labels)}
        except Exception as e:
            return {"ok": False, "error": str(e), "badges": labels}
    return {"ok": True, "badges": labels, "persisted": False}


def patch_content_meta(content_hash, patch, team=None, reviewer="") -> dict:
    """검수자 구조화 교정(빈 카테고리 채우기 등) → 저장된 item_meta 패치. 골든 완성에 기여.
    교정 전/후를 patch_log 에 append(선호쌍 데이터 원천 · 다중 요소 교정 무손실).
    finalGrade(G|R)·reasons 키는 quality_meta 교정으로 분기(최종검수 '고쳐서 편입')."""
    st = _SV.get_store()
    if not (st and hasattr(st, "update_item_meta")):
        return {"ok": False, "error": "지원하지 않는 저장소"}
    ch = (content_hash or "").strip()
    patch = dict(patch or {})
    grade = patch.pop("finalGrade", None)
    reasons = patch.pop("reasons", None)
    before = None
    if patch and hasattr(st, "get_item_meta"):
        try:
            cur = st.get_item_meta(ch)
            if isinstance(cur, dict):
                before = {k: cur.get(k) for k in patch}           # 패치 대상 키의 이전 값만
        except Exception:
            before = None
    ok = st.update_item_meta(ch, patch) if patch else False
    if ok and before is not None and hasattr(st, "log_patch"):
        element = "category" if "content_category" in patch else ",".join(sorted(patch))
        try:
            st.log_patch(ch, reviewer or "(익명)", element, before, patch, team=team)
        except Exception:
            pass
    if grade in ("G", "R") and hasattr(st, "update_quality"):     # 등급 교정(이전 등급을 이력에 보존)
        prev = st.update_quality(ch, grade, reasons)
        if prev is not None:
            ok = True
            if prev != grade and hasattr(st, "log_patch"):
                try:
                    st.log_patch(ch, reviewer or "(익명)", "grade",
                                 {"finalGrade": prev}, {"finalGrade": grade}, team=team)
                except Exception:
                    pass
    _SV._agg_bump()
    return {"ok": bool(ok)}


def arena_data(team=None) -> dict:
    """평가 아레나(게임화) 데이터: 팀 정확도 + 리더보드 + 검수 대기(퀘스트). team 별 스코핑 · TTL 캐시."""
    return _SV._agg_cached(("arena", team), lambda: _arena_compute(team), ttl=15.0)


def _fb_epoch(ts) -> float:
    """feedback ts → epoch. sqlite=float · supabase=timestamptz(UTC) 문자열(supastore._epoch 와 동일 해석).
    UTC 저장분이므로 calendar.timegm 으로 UTC 해석 — mktime(로컬 해석)은 비UTC 호스트에서 스큐."""
    try:
        return float(ts)
    except (TypeError, ValueError):
        try:
            import calendar
            return calendar.timegm(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            return 0.0


def _arena_compute(team=None) -> dict:
    st = _SV.get_store()
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
        d["last_batch_at"] = float((_SV._report_get("learn_report", team) or {}).get("ts") or 0)
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
            try:                                  # 모집단 = 검수 대상(YELLOW ∪ 배정) — 분모(total_targets)와 동일.
                targets = (st.review_targets(team) if hasattr(st, "review_targets")
                           else st.yellow_hashes(team) if hasattr(st, "yellow_hashes") else None)
            except Exception:
                targets = None
            fm = st.feedback_map(team=team) or {}
            per = {}                              # 검수자 → 유효 검수한 대상 집합
            for ch, e in fm.items():
                if targets is not None and ch not in targets:
                    continue                      # 대상 아님(이미 확정·삭제·자동통과) → 옛 검수가 새 퀘스트를 완주시키는 것 방지
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
            # 퀘스트 게이지 = '개인별 진척도의 팀 평균'(0..1). 총 대상(예: 200)보다 개인 배정이
            # 적은 운영에서 '평균 건수/총건수'가 영구 미달로 왜곡되는 것 방지 —
            # 배정이 있으면 개인 분모 = 그 사람의 배정(대상 내), 없으면 총 대상(기존 관점과 동치).
            try:
                asg = st.assignees(team=team) if hasattr(st, "assignees") else {}
            except Exception:
                asg = {}
            asg = {ch: a for ch, a in (asg or {}).items()
                   if targets is None or ch in targets}
            if asg:
                by_rv = {}                        # 배정 검수자 → [완료, 배정]
                for ch, a in asg.items():
                    for rv in (a.get("reviewers") or []):
                        cnt = by_rv.setdefault(rv, [0, 0])
                        cnt[1] += 1
                        if ch in (per.get(rv) or ()):
                            cnt[0] += 1
                progs = [c[0] / c[1] for c in by_rv.values() if c[1]]
                d["quest_team_progress"] = round(sum(progs) / len(progs), 4) if progs else 0.0
            else:
                tt = int(d.get("total_targets") or 0)
                d["quest_team_progress"] = (round(sum(min(len(s), tt) for s in per.values())
                                                  / (members * tt), 4) if members and tt else 0.0)
            qs = float((_SV._report_get("quest_meta", team) or {}).get("started_at") or 0)
            if qs:
                d["quest_started_at"] = qs
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


def _lack_classes(team=None) -> set:
    """골든 보유가 부족한(클래스당 8건 미만 · SetFit 기준) Tier1 집합.
    능동학습 라벨 예산 배분의 원천 — 이 분류의 검수가 정답셋 커버리지에 더 기여한다."""
    def _calc():
        st = _SV.get_store()
        if not (st and hasattr(st, "get_golden")):
            return set()
        from . import dictionaries as D
        per = {}
        try:
            for g in st.get_golden(team) or []:
                t1s = {str(c).split("/")[0].strip()
                       for c in ((g.get("expected") or {}).get("content_category") or []) if c}
                for t1 in t1s:
                    per[t1] = per.get(t1, 0) + 1
        except Exception:
            return set()
        return {t1 for t1 in D.IAB_TIER1 if per.get(t1, 0) < 8}
    return _SV._agg_cached(("lackcls", team), _calc)


def raw_rows(limit: int = 100, team=None, reviewer: str = "") -> dict:
    """검수 대상 콘텐츠: 판정 결과 전체를 한 표로(모델·버전·필터 · 빠른 검수).
    검수 대기(YELLOW)·불일치도 포함되며, 검수자 식별 시 골드 문항을 섞는다."""
    rows = _SV.results_rows(team=team)
    st = _SV.get_store()
    lack = _lack_classes(team)                     # 부족 분류(정답셋 커버리지) 배지 원천
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
    try:                                           # 리드 최종판정(의견 갈림 해소 배지)
        finals = final_verdicts(team)
    except Exception:
        finals = {}
    out = []
    for r in reversed(rows[-int(limit):]):         # 최근순
        ref = r.get("content_ref") or {}
        im = r.get("item_meta") or {}
        qm = r.get("quality_meta") or {}
        tr = r.get("trace") or {}
        ch = _row_key(ref)
        if pmap.get(ch) == "eval":
            continue
        if _SV._is_pending_row(r):                     # 미실행(STEP 1 추가만) 콘텐츠는 검수 대상 아님
            continue
        fb = fmap.get(ch) or {}
        # 내 판정(mine)·내 교정(note·elems) 계산은 _fb_public 단일 원천(드릴 목록과 동일 규약).
        # (합의가 동점 split 인데 배지가 '수정 필요'로 뭉뚱그려져 "정확으로 바꿨는데 수정필요로 조회" 혼란 방지)
        out.append({"hash": ch,
                    "service": ref.get("displayServiceName", ""), "title": ref.get("title", ""),
                    "body": ref.get("body", ""), "url": ref.get("source_url", ""),
                    "images": ref.get("image_urls", []) or [],
                    "grade": qm.get("finalGrade", ""), "reasons": qm.get("reasons", []) or [],
                    "category": im.get("content_category", []) or [],
                    "summary": im.get("summary", ""), "entities": im.get("entities", []) or [],
                    "intent": im.get("intent", []) or [],
                    "model": tr.get("model", "") or "",
                    "version": int(tr.get("version") or 1),
                    "review": qm.get("review", "") or "",
                    "split": bool(fb.get("good") and fb.get("bad")),
                    "final": (finals.get(ch) or {}).get("verdict", ""),
                    "class_gap": bool(lack and {str(c).split("/")[0].strip()
                                                for c in (im.get("content_category") or [])} & lack),
                    "fb": _SV._fb_public(fb, reviewer),
                    "assignees": (asg.get(ch) or {}).get("reviewers", []),
                    "min_reviewers": (asg.get(ch) or {}).get("min", 0),
                    "ops_hold": bool(qm.get("ops_hold")),   # 운영자 수동 노출제한(라벨 아님 · 학습 미포함)
                    "source_status": ref.get("source_status") or {},   # 원문 소실 신고 플래그(게시판 #10)
                    "item_meta": im, "quality_meta": qm})
    # 골드 문항(정답 알려진 검증 문항) 삽입: 큐와 동일 규칙, 표 형태로 어댑트.
    # 검수할 실제 콘텐츠가 있을 때만 섞는다 — 콘텐츠 전체 삭제 후 골드만 홀로 남는 오인 방지.
    if reviewer and out:
        gold_items = _SV._inject_gold([], reviewer, team)
        for g in gold_items:
            out.insert(0, {"hash": g["hash"], "service": g.get("service", ""), "title": g.get("title", ""),
                           "body": g.get("body", ""), "url": "", "images": [],
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
    rows = _SV.results_rows(team=team)
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
    st = _SV.get_store()
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
    st = _SV.get_store()
    ch = (content_hash or "").strip()
    cur = None
    for r in _SV.results_rows(team=team):
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
    st = _SV.get_store()
    if not st:
        return {"ok": False, "error": "store unavailable", "items": []}
    only_un = data.get("only_unreviewed", True)
    limit = int(data.get("limit") or 100)
    rv = (data.get("reviewer") or "").strip()
    items = st.review_queue(limit=limit, only_unreviewed=bool(only_un), team=data.get("team"),
                            reviewer=rv or None,                # 배정 콘텐츠 배타 노출
                            see_all=bool(data.get("see_all")))  # 생성자·슈퍼관리자 = 배타 우회(전체 열람)
    if items:                                                   # 실제 큐가 있을 때만 골드 삽입(빈 큐에 골드만 뜨는 것 방지)
        items = _SV._inject_gold(items, rv, data.get("team"))
    return {"ok": True, "items": items, "n": len(items)}


def _inject_gold(items: list, reviewer: str, team=None) -> list:
    """골든셋에서 골드 문항을 생성해 큐에 삽입(블라인드). [Oleson 2011 · Kittur 2008]
    변형: hash 짝수 = 원본 그대로(정답 good) / 홀수 = 등급 뒤집기(정답 bad).
    선택·위치는 (검수자, 일자) 시드로 결정적(폴링 때마다 재배치 방지). 응답한 문항은 재출제 안 함."""
    st = _SV.get_store()
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
