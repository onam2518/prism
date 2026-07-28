"""검수 인력 운영(HR) 도메인 · '검수운영' 메뉴 백엔드.

정답셋 검수는 사람 손이 드는 일인데 '누가 · 언제 · 얼마나' 할 수 있는지가 어디에도
없어 배정이 감으로 나갔다(2026-07-28 운영 실측: 900슬롯을 한 번에 뿌린 뒤 11일간
293건 정체 · 처리속도가 사람마다 23배 차이인데 전원 100건 균등 배정 · 유휴 인원 2명).
남은 일의 실제 크기는 팀 합계 4시간인데 11일째 안 끝나는 상태였다. 캐파 문제가
아니라 스케줄 부재다. 이 모듈이 그 공백을 메운다.

  ① 캐파 실측 `capacity`      판정 간격에서 처리율을 계산(자가신고 최소화)
  ② 인력 원장 `profiles`      주간 약속 시간 · 근무 요일 · 부재 · 상태(HR 필드)
  ③ 계획   `plan_distribute`  균등이 아니라 '남은 여력' 비례 + 부재 제외 + 마감
  ④ 회수   `rebalance`        정체분을 회수해 여력 있는 사람에게 이관

설계 원칙(HR): 지표는 '얼마나 빠른가'가 아니라 '본인이 약속한 만큼 대비 어디쯤인가'로
표시하고, 이상 신호는 경고가 아니라 재배정·코칭 트리거로 연결한다. 감시 도구로 보이는
순간 팀이 등을 돌리기 때문이다. 개인 지표 열람은 슈퍼관리자 전체 · 본인은 자기 것만
(`crew_data(scope_uid=...)`).

저장: reports kind(팀 스코프 · DDL 불필요). reviewer_roles·final_verdicts 와 같은 패턴.
실측값은 저장하지 않고 매번 계산(TTL 캐시)한다. 원장에 실측을 굳혀두면 몰아치기 이력이
그대로 캐파로 박혀 배정을 왜곡한다.

컴포지션: 서버 환경(스토어·집계 캐시·리포트 영속)은 serve 가 기동 시 `_SV` 로 주입
(learnops 관례 · 순환 import 없음). 테스트가 serve.get_store 등을 몽키패치하므로
그 이름들의 호출은 `_SV.` 경유가 계약이다.
"""
from __future__ import annotations

import heapq
import statistics
import time

from .store import day_key

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입

PROFILE_KIND = "crew_profile"   # {uid: {hours_per_week, workdays, status, leave_from, leave_to, note, rate_override}}
SETTINGS_KIND = "crew_settings"
WAVE_KIND = "crew_wave"         # 현재 웨이브 {due_at, opened_at, by, plan:{uid:n}}

STATUSES = ("active", "onboarding", "leave", "inactive")

DEFAULT_SETTINGS = {
    "rate_cap_per_hour": 90,    # 과속 상한 · 40초/건보다 빠른 속도는 캐파로 인정하지 않는다(본문 미독 구간)
    "buffer": 0.8,              # 여유율 · 약속 시간을 100% 검수로 채울 수 있는 사람은 없다
    "stale_days": 3,            # 배정 후 손 안 댄 기간이 이만큼이면 정체
    "speed_floor_sec": 10,      # 건당 이보다 빠르면 코칭 플래그(과속)
    "gold_min_acc": 0.6,        # 골드 정확도 하한(표본 5건 이상일 때만 판정)
    "calib_target": 20,         # 온보딩 캘리브레이션 문항 수 · 통과 전에는 정식 배정 제외
    "default_hours": 2.0,       # 주간 약속 시간 미입력자의 잠정값(원장 입력 전 계획이 0 이 되는 것 방지)
}

# 실측 표본 하한: 이보다 적으면 처리율을 신뢰하지 않고 팀 중앙값을 쓴다(신규·복귀자 보호)
_MIN_GAP_SAMPLE = 8
_GAP_MIN, _GAP_MAX = 1.0, 600.0     # 판정 간격 유효 구간(초) · 10분 초과는 휴지로 보고 제외
_SESSION_GAP = 1800.0               # 세션 분할 기준(초)
# 판정 성향(정확 비율) 비교 하한. 2~3건으로 '기준 이탈' 딱지를 붙이면 신규자가 바로 코칭 대상이 된다
_MIN_JUDGE_SAMPLE = 20


# ── 저장 계층(reports kind · 팀 스코프) ──────────────────────────────────────
def settings(team=None) -> dict:
    """운영 파라미터(기본값 위에 팀 설정을 덮음)."""
    out = dict(DEFAULT_SETTINGS)
    out.update({k: v for k, v in ((_SV._report_get(SETTINGS_KIND, team, {}) or {}).get("items") or {}).items()
                if k in DEFAULT_SETTINGS})
    return out


def set_settings(patch: dict, team=None) -> dict:
    cur = dict(((_SV._report_get(SETTINGS_KIND, team, {}) or {}).get("items") or {}))
    for k, v in (patch or {}).items():
        if k not in DEFAULT_SETTINGS:
            continue
        try:
            cur[k] = float(v) if isinstance(DEFAULT_SETTINGS[k], float) else int(v)
        except (TypeError, ValueError):
            continue
    _SV._report_save(SETTINGS_KIND, {"items": cur}, team)
    _SV._agg_bump()
    return settings(team)


def profiles(team=None) -> dict:
    """인력 원장 {uid: profile} · 기록 없는 사람은 기본 프로필로 취급(crew_data 에서 채움)."""
    return dict(((_SV._report_get(PROFILE_KIND, team, {}) or {}).get("items") or {}))


def _blank_profile(cfg: dict) -> dict:
    return {"hours_per_week": float(cfg["default_hours"]), "workdays": [0, 1, 2, 3, 4],
            "status": "active", "leave_from": "", "leave_to": "", "note": "",
            "rate_override": 0, "confirmed": False}


def set_profile(uid: str, patch: dict, team=None, by: str = "") -> dict:
    """인력 원장 갱신(부분 패치). 입력 주체는 관리자이고 본인은 확인만 하는 운영이라
    `confirmed` 로 '본인이 확인했는지'를 따로 남긴다(합의 없는 일방 배정 방지)."""
    uid = (uid or "").strip()
    if not uid:
        return {"ok": False, "error": "누구인지 알 수 없습니다"}
    cfg = settings(team)
    items = profiles(team)
    p = dict(items.get(uid) or _blank_profile(cfg))
    patch = dict(patch or {})
    if "hours_per_week" in patch:
        try:
            p["hours_per_week"] = max(0.0, min(40.0, float(patch["hours_per_week"] or 0)))
        except (TypeError, ValueError):
            return {"ok": False, "error": "일주일에 낼 시간은 숫자로 적어주세요"}
    if "workdays" in patch:
        wd = patch.get("workdays") or []
        p["workdays"] = sorted({int(d) for d in wd if str(d).lstrip("-").isdigit() and 0 <= int(d) <= 6})
    if "status" in patch:
        s = (patch.get("status") or "").strip()
        if s not in STATUSES:
            return {"ok": False, "error": "고를 수 없는 상태입니다"}
        p["status"] = s
    for k in ("leave_from", "leave_to"):
        if k in patch:
            v = (patch.get(k) or "").strip()[:10]
            if v and not _valid_date(v):
                return {"ok": False, "error": "날짜는 2026-07-28 처럼 적어주세요"}
            p[k] = v
    if p.get("leave_from") and p.get("leave_to") and p["leave_from"] > p["leave_to"]:
        return {"ok": False, "error": "자리 비우는 날이 돌아오는 날보다 늦습니다"}
    if "note" in patch:
        p["note"] = str(patch.get("note") or "")[:200]
    if "rate_override" in patch:
        try:
            p["rate_override"] = max(0, min(600, int(float(patch.get("rate_override") or 0))))
        except (TypeError, ValueError):
            p["rate_override"] = 0
    if "confirmed" in patch:
        p["confirmed"] = bool(patch.get("confirmed"))
    p["updated_at"] = time.time()
    p["updated_by"] = (by or "")[:80]
    items[uid] = p
    _SV._report_save(PROFILE_KIND, {"items": items}, team)
    _SV._agg_bump()
    return {"ok": True, "profile": p}


def _valid_date(s: str) -> bool:
    try:
        time.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def wave(team=None) -> dict:
    """현재 웨이브(주 사이클) {due_at, opened_at, by, plan}. 없으면 빈 dict."""
    return dict((_SV._report_get(WAVE_KIND, team, {}) or {}).get("item") or {})


def set_wave(due_at, by: str = "", plan=None, team=None) -> dict:
    """웨이브 마감 설정/해제(due_at 빈 값 = 해제). 마감이 있어야 신호등이 '늦음'을 판정한다."""
    try:
        due = float(due_at or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "기한을 읽을 수 없습니다"}
    if due <= 0:
        _SV._report_save(WAVE_KIND, {"item": {}}, team)
        _SV._agg_bump()
        return {"ok": True, "wave": {}}
    item = {"due_at": due, "opened_at": (wave(team).get("opened_at") or time.time()),
            "by": (by or "")[:80], "plan": dict(plan or {})}
    _SV._report_save(WAVE_KIND, {"item": item}, team)
    _SV._agg_bump()
    return {"ok": True, "wave": item}


# ── 캐파 실측 ────────────────────────────────────────────────────────────────
def _epoch(ts) -> float:
    """feedback ts → epoch(sqlite=float · supabase=UTC 문자열). reviewops._fb_epoch 와 동일 해석."""
    return _SV._fb_epoch(ts)


def capacity(team=None, fmap=None) -> dict:
    """검수자별 실측 {uid: {n_total, n28, n7, active_days, median_sec, rate_per_hour, last_ts, sessions}}.

    처리율은 '연속 판정 간격의 중앙값'에서 낸다. 총 시간 나누기 건수를 쓰면 중간의 긴
    휴지가 섞여 실제보다 느리게 나오고, 평균을 쓰면 한 번의 긴 간격에 끌려간다.
    표본이 적은 사람(_MIN_GAP_SAMPLE 미만)은 팀 중앙값을 빌려 쓴다. 신규자에게
    우연히 나온 극단값으로 배정량을 정하지 않기 위해서다."""
    st = _SV.get_store()
    if fmap is None:
        try:
            fmap = st.feedback_map(team=team) if st else {}
        except Exception:
            fmap = {}
    by_rv = {}
    for e in (fmap or {}).values():
        for v in (e.get("verdicts") or []):
            if v.get("verdict") not in ("good", "bad"):
                continue
            rid = v.get("reviewer_id") or v.get("reviewer") or ""
            t = _epoch(v.get("ts"))
            if rid and t:
                by_rv.setdefault(rid, []).append(t)
    now = time.time()
    raw, all_gaps = {}, []
    for rid, ts in by_rv.items():
        ts.sort()
        gaps = [b - a for a, b in zip(ts, ts[1:]) if _GAP_MIN <= (b - a) <= _GAP_MAX]
        all_gaps += gaps
        sessions = 1 + sum(1 for a, b in zip(ts, ts[1:]) if (b - a) > _SESSION_GAP)
        raw[rid] = {"n_total": len(ts), "n28": sum(1 for x in ts if x > now - 28 * 86400),
                    "n7": sum(1 for x in ts if x > now - 7 * 86400),
                    "active_days": len({day_key(x) for x in ts}), "last_ts": ts[-1] if ts else 0,
                    "sessions": sessions, "_gaps": gaps}
    team_median = statistics.median(all_gaps) if all_gaps else 0.0
    out = {}
    for rid, d in raw.items():
        gaps = d.pop("_gaps")
        med = statistics.median(gaps) if len(gaps) >= _MIN_GAP_SAMPLE else (team_median or 0.0)
        d["median_sec"] = round(med, 1)
        d["rate_per_hour"] = round(3600.0 / med, 1) if med > 0 else 0.0
        d["measured"] = len(gaps) >= _MIN_GAP_SAMPLE          # False = 팀 중앙값을 빌려 쓴 잠정값
        out[rid] = d
    return out


def _effective_rate(meas: dict, prof: dict, cfg: dict, team_rate: float) -> float:
    """배정 계산에 쓰는 시간당 처리율. 과속은 캐파가 아니라 품질 경보라 상한을 씌운다."""
    r = float(prof.get("rate_override") or 0) or float((meas or {}).get("rate_per_hour") or 0) or team_rate
    return max(1.0, min(float(cfg["rate_cap_per_hour"]), r))


def _on_leave(prof: dict, now=None) -> bool:
    """오늘이 부재 기간 안인지. 시작·종료 중 한쪽만 있어도 그 방향으로 열린 구간으로 본다."""
    today = day_key(now)
    f, t = (prof.get("leave_from") or ""), (prof.get("leave_to") or "")
    if not (f or t):
        return False
    return (not f or today >= f) and (not t or today <= t)


def _available(prof: dict, now=None) -> bool:
    """새 배정을 받을 수 있는 사람인지. 온보딩·휴면·부재는 계획에서 뺀다
    (지금은 휴가 중인 사람에게도 배정이 나가고 있다)."""
    return prof.get("status") == "active" and not _on_leave(prof, now)


# ── 종합 현황(현황판 · 팀원 카드) ────────────────────────────────────────────
def _norm_reviewers(raw) -> dict:
    """reviewers_map 정규화 {uid: {name, avatar}}. 두 스토어의 계약이 갈려 있다 ·
    supabase 는 {uid: {name, avatar}}, sqlite 는 {이름: 아바타문자열}(키가 곧 이름).
    기존 호출부가 키만 써서 드러나지 않던 차이라 스토어를 건드리지 않고 여기서 흡수한다."""
    out = {}
    for k, v in (raw or {}).items():
        if isinstance(v, dict):
            out[k] = {"name": v.get("name") or k, "avatar": v.get("avatar") or "boksil"}
        else:
            out[k] = {"name": k, "avatar": (v or "boksil") if isinstance(v, str) else "boksil"}
    return out


def crew_data(team=None, scope_uid: str = "") -> dict:
    """검수운영 화면 데이터. scope_uid 를 주면 그 사람 카드만(본인 열람 · HR 민감도)."""
    return _SV._agg_cached(("crew", team, scope_uid or ""),
                           lambda: _crew_compute(team, scope_uid), ttl=20.0)


def _crew_compute(team=None, scope_uid: str = "") -> dict:
    st = _SV.get_store()
    if not st:
        return {"ok": False, "members": [], "summary": {}, "error": "store unavailable"}
    cfg = settings(team)
    now = time.time()
    try:
        rvs = _norm_reviewers(st.reviewers_map(team))
    except Exception:
        rvs = {}
    try:
        fmap = st.feedback_map(team=team) or {}
    except Exception:
        fmap = {}
    try:
        asg = st.assignees(team=team) or {}
    except Exception:
        asg = {}
    try:
        targets = st.review_targets(team) if hasattr(st, "review_targets") else set()
    except Exception:
        targets = set()
    try:
        golden = st.golden_hashes(team) or set()
    except Exception:
        golden = set()
    try:
        gold = st.gold_stats(team) if hasattr(st, "gold_stats") else {}
    except Exception:
        gold = {}
    try:
        weights = _SV.reviewer_weights(team) or {}
    except Exception:
        weights = {}
    try:
        roles = _SV.reviewer_roles(team) or {}
    except Exception:
        roles = {}

    meas = capacity(team, fmap=fmap)
    profs = profiles(team)
    wv = wave(team)
    due_at = float(wv.get("due_at") or 0)
    # 팀 기준 처리율: 표본 부족자는 capacity 가 이미 팀 중앙값을 채워주므로 전원을 넣어도
    # 왜곡되지 않는다(측정자만 걸러 쓰면 초기 팀에서 폴백 상수 30 으로 떨어져 캐파가 어긋난다)
    rates = [m["rate_per_hour"] for m in meas.values() if m["rate_per_hour"] > 0]
    team_rate = statistics.median(rates) if rates else 30.0

    done_pairs = set()                              # (콘텐츠, 검수자) 완료 조합
    good_by, n_by = {}, {}
    for ch, e in fmap.items():
        for v in (e.get("verdicts") or []):
            if v.get("verdict") not in ("good", "bad"):
                continue
            rid = v.get("reviewer_id") or v.get("reviewer") or ""
            done_pairs.add((ch, rid))
            n_by[rid] = n_by.get(rid, 0) + 1
            if v.get("verdict") == "good":
                good_by[rid] = good_by.get(rid, 0) + 1
    ratios = [good_by.get(r, 0) / n for r, n in n_by.items() if n >= _MIN_JUDGE_SAMPLE]
    team_good = statistics.median(ratios) if ratios else 0.5

    asg_ts = _assign_ts(team)
    load = {}                                       # uid → [배정, 미완료, 최고 정체일]
    for ch, a in asg.items():
        if targets and ch not in targets:
            continue                                # 삭제·확정된 콘텐츠의 고아 배정은 부하가 아니다
        for rv in (a.get("reviewers") or []):
            c = load.setdefault(rv, [0, 0, 0.0])
            c[0] += 1
            if (ch, rv) not in done_pairs:
                c[1] += 1
                age = (now - asg_ts.get((ch, rv), 0)) / 86400 if asg_ts.get((ch, rv)) else 0.0
                c[2] = max(c[2], age)

    ids = sorted(set(rvs) | set(meas) | set(profs) | set(load),
                 key=lambda i: -(meas.get(i, {}).get("n28", 0)))
    members = []
    for uid in ids:
        if scope_uid and uid != scope_uid:
            continue
        prof = dict(_blank_profile(cfg))
        prof.update(profs.get(uid) or {})
        m = meas.get(uid) or {}
        lo = load.get(uid) or [0, 0, 0.0]
        rate = _effective_rate(m, prof, cfg, team_rate)
        weekly = round(rate * float(prof.get("hours_per_week") or 0) * float(cfg["buffer"]))
        g = gold.get(uid) or {}
        nb = n_by.get(uid, 0)
        good_ratio = round(good_by.get(uid, 0) / nb, 4) if nb else None
        card = {
            "id": uid, "name": (rvs.get(uid) or {}).get("name") or uid[:8],
            "avatar": (rvs.get(uid) or {}).get("avatar") or "boksil",
            "is_final": uid in roles, "profile": prof,
            "on_leave": _on_leave(prof, now), "available": _available(prof, now),
            # rate_per_hour = 실제로 잰 속도 · effective_rate = 소화량 계산에 쓴 값(상한 적용 후).
            # 둘을 함께 주지 않으면 화면의 계산식이 결과와 안 맞아 보인다(225건/h 인데 결과는 상한 기준).
            "measured": {"rate_per_hour": m.get("rate_per_hour", 0.0), "median_sec": m.get("median_sec", 0.0),
                         "effective_rate": round(rate, 1), "capped": rate < float(m.get("rate_per_hour") or 0),
                         "estimated": not m.get("measured", False), "n_total": m.get("n_total", 0),
                         "n28": m.get("n28", 0), "n7": m.get("n7", 0),
                         "active_days": m.get("active_days", 0), "sessions": m.get("sessions", 0),
                         "last_ts": m.get("last_ts", 0)},
            "load": {"assigned": lo[0], "pending": lo[1], "done": lo[0] - lo[1],
                     "stale_days": round(lo[2], 1),
                     "progress": round((lo[0] - lo[1]) / lo[0], 4) if lo[0] else None},
            "quality": {"gold_n": g.get("n", 0), "gold_acc": g.get("acc", 0.0),
                        "weight": weights.get(uid), "good_ratio": good_ratio, "n_judged": nb},
            "weekly_capacity": weekly,
            "spare": max(0, weekly - lo[1]) if _available(prof, now) else 0,
            "hours_left": round(lo[1] / rate, 2) if rate else 0.0,
        }
        card["flags"] = _coach_flags(card, cfg, team_good)
        card["signal"] = _signal(card, cfg, due_at, now)
        members.append(card)

    out = {"ok": True, "members": members, "settings": cfg, "wave": wv,
           "scope": "me" if scope_uid else "team", "server_now": now}
    if scope_uid:
        return out
    out["summary"] = _summary(members, fmap, targets, golden, cfg, due_at, now, team_rate)
    out["burndown"] = _burndown(fmap, targets, asg, days=21)
    return out


def _assign_ts(team=None) -> dict:
    """(콘텐츠, 검수자) → 배정 시각. 정체 일수 계산용 · 스토어가 안 주면 빈 dict(정체 0 취급)."""
    st = _SV.get_store()
    try:
        if hasattr(st, "assignment_times"):
            return st.assignment_times(team=team) or {}
    except Exception:
        pass
    return {}


def _coach_flags(card: dict, cfg: dict, team_good: float) -> list:
    """챙겨볼 점(잘못을 지적하는 딱지가 아니라 도와줄 거리를 짚는 표식).
    문구는 기계적인 말 대신 일상어로 쓴다. 화면에 그대로 나가는 문장이다."""
    out = []
    ms = card["measured"]
    if ms["n_total"] >= 20 and 0 < ms["median_sec"] < float(cfg["speed_floor_sec"]):
        out.append({"id": "speed", "label": "너무 빠름",
                    "hint": f"한 건에 {ms['median_sec']:.0f}초 · 본문을 읽기 어려운 속도예요"})
    q = card["quality"]
    if q["gold_n"] >= 5 and q["gold_acc"] < float(cfg["gold_min_acc"]):
        out.append({"id": "gold", "label": "정답 문항 많이 틀림",
                    "hint": f"정답이 정해진 문항을 {q['gold_acc']:.0%}만 맞혔어요"})
    if (q["good_ratio"] is not None and q["n_judged"] >= _MIN_JUDGE_SAMPLE
            and abs(q["good_ratio"] - team_good) >= 0.25):
        out.append({"id": "drift", "label": "판단 기준이 팀과 다름",
                    "hint": f"정확 판정 {q['good_ratio']:.0%} · 팀은 {team_good:.0%}"})
    if card["profile"].get("status") == "onboarding" and q["gold_n"] < int(cfg["calib_target"]):
        out.append({"id": "calib", "label": "기준 맞추는 중",
                    "hint": f"연습 문항 {q['gold_n']}개 / {int(cfg['calib_target'])}개"})
    return out


def _signal(card: dict, cfg: dict, due_at: float, now: float) -> str:
    """신호등: leave(부재) · idle(여유) · done(완료) · green · yellow · red.
    기준은 속도가 아니라 '약속·마감 대비 진행'이다(설계 원칙)."""
    if card["on_leave"] or card["profile"].get("status") == "leave":
        return "leave"
    if card["profile"].get("status") == "inactive":
        return "off"
    pending, assigned = card["load"]["pending"], card["load"]["assigned"]
    if not pending:
        return "done" if assigned else "idle"
    prog = card["load"]["progress"] or 0.0
    stale = card["load"]["stale_days"]
    if (due_at and now > due_at) or (stale >= float(cfg["stale_days"]) * 2 and prog < 0.2):
        return "red"
    if stale >= float(cfg["stale_days"]) or (due_at and (due_at - now) < 86400 and prog < 0.5):
        return "yellow"
    return "green"


def _summary(members, fmap, targets, golden, cfg, due_at, now, team_rate) -> dict:
    """현황판 요약: 남은 일의 크기 · 팀 캐파 · 예상 완료일 · 유휴 · 정체 · 2층 대기."""
    pending = sum(m["load"]["pending"] for m in members)
    hours_left = round(sum(m["hours_left"] for m in members), 1)
    weekly = sum(m["weekly_capacity"] for m in members if m["available"])
    idle = [{"id": m["id"], "name": m["name"], "spare": m["spare"]}
            for m in members if m["available"] and m["spare"] > 0 and m["load"]["pending"] == 0]
    stale = [{"id": m["id"], "name": m["name"], "pending": m["load"]["pending"],
              "days": m["load"]["stale_days"]}
             for m in members if m["load"]["pending"] and m["load"]["stale_days"] >= float(cfg["stale_days"])]
    stale.sort(key=lambda x: -x["pending"])
    split = sum(1 for ch, e in fmap.items()
                if e.get("good") and e.get("bad") and ch not in golden
                and (not targets or ch in targets))
    eta = ""                                        # 주간 캐파 기준 역산 · 캐파 0 이면 산출 불가
    if pending and weekly > 0:
        eta = day_key(now + (pending / weekly) * 7 * 86400)
    return {"pending": pending, "hours_left": hours_left, "weekly_capacity": weekly,
            "eta": eta, "idle": idle, "idle_spare": sum(i["spare"] for i in idle),
            "stale": stale[:10], "stale_total": sum(s["pending"] for s in stale),
            "final_pending": split, "golden_n": len(golden), "targets_n": len(targets),
            "team_rate": round(team_rate, 1), "due_at": due_at,
            "active_members": sum(1 for m in members if m["available"]),
            "total_members": len(members)}


def _burndown(fmap, targets, asg, days: int = 21) -> list:
    """번다운: 일별 '배정분 완료 수'와 남은 배정 슬롯.

    잔여는 오늘의 실제 미완료에서 시작해 하루씩 거슬러 더해 복원한다(어제 잔여 =
    오늘 잔여 + 오늘 완료). 배정 이력 자체는 남지 않으므로 '지금 배정된 슬롯' 기준의
    재구성이며, 그 사이 배정이 늘었다면 과거 구간은 실제보다 적게 잡힌다.
    activity 는 배정 밖 판정까지 포함한 그날의 총 검수량(팀 활동량)."""
    pairs = {(ch, rv) for ch, a in asg.items() if not targets or ch in targets
             for rv in (a.get("reviewers") or [])}
    done_day, act_day = {}, {}
    done_total = 0
    for ch, e in fmap.items():
        for v in (e.get("verdicts") or []):
            if v.get("verdict") not in ("good", "bad"):
                continue
            t = _epoch(v.get("ts"))
            if not t:
                continue
            k = day_key(t)
            act_day[k] = act_day.get(k, 0) + 1
            if (ch, v.get("reviewer_id") or v.get("reviewer") or "") in pairs:
                done_day[k] = done_day.get(k, 0) + 1
                done_total += 1
    keys = [day_key(time.time() - i * 86400) for i in range(days - 1, -1, -1)]
    left = max(0, len(pairs) - done_total)          # 오늘 잔여 = 실제 미완료 슬롯
    out = []
    for k in reversed(keys):
        out.append({"day": k, "done": done_day.get(k, 0), "activity": act_day.get(k, 0), "left": left})
        left += done_day.get(k, 0)                  # 어제 잔여 = 오늘 잔여 + 오늘 완료
    out.reverse()
    return out


# ── 계획: 캐파 비례 배정 ─────────────────────────────────────────────────────
def plan_distribute(hashes, min_reviewers: int = 1, reviewers=None, team=None,
                    apply: bool = False, by: str = "", due_at=None) -> dict:
    """캐파 비례 배정(균등 분배의 대체). 콘텐츠당 서로 다른 담당 N명.

    reviewops.distribute_assignments 는 '미완료 건수'만 보고 나눠서, 시간당 22건 하는
    사람과 500건 하는 사람이 같은 짐을 졌다. 여기서는 각자의 남은 여력
    (주간 캐파 - 현재 미완료)에 비례해 나누고, 부재·온보딩·휴면은 애초에 제외한다.
    apply=False 면 계획만 돌려준다(미리보기 후 실행이 배정 사고를 줄인다)."""
    st = _SV.get_store()
    hs = [h for h in dict.fromkeys(hashes or []) if h]
    if not (st and hs):
        return {"ok": False, "error": "나눠줄 콘텐츠가 없습니다", "plan": {}, "n": 0}
    data = _crew_compute(team)                      # 캐시 우회: 방금 바뀐 부하를 반영해야 한다
    pool = [m for m in data["members"] if m["available"]]
    if reviewers:
        want = set(reviewers)
        pool = [m for m in pool if m["id"] in want]
        blocked = [m["name"] for m in data["members"] if m["id"] in want and not m["available"]]
    else:
        blocked = []
    if not pool:
        return {"ok": False, "error": "지금 맡길 수 있는 사람이 없습니다 (휴가·적응 중·쉬는 중은 빠집니다)",
                "plan": {}, "n": 0, "blocked": blocked}
    n_per = max(1, min(len(pool), int(min_reviewers or 1)))
    cap = {m["id"]: max(1, m["weekly_capacity"] or 1) for m in pool}
    used = {m["id"]: m["load"]["pending"] for m in pool}     # 시작 부하 = 이미 밀린 내 몫
    # 힙 키 = 여력 소진율(=부하/캐파). 항상 가장 여유 있는 사람부터 채워 '캐파 비례'가 된다.
    heap = [(used[m["id"]] / cap[m["id"]], i, m["id"]) for i, m in enumerate(pool)]
    heapq.heapify(heap)
    groups, per = {}, {}
    for h in hs:
        picked = [heapq.heappop(heap) for _ in range(n_per)]
        groups.setdefault(tuple(p[2] for p in picked), []).append(h)
        for _, i, rid in picked:
            used[rid] += 1
            per[rid] = per.get(rid, 0) + 1
            heapq.heappush(heap, (used[rid] / cap[rid], i, rid))
    plan = {rid: {"n": n, "name": next(m["name"] for m in pool if m["id"] == rid),
                  "capacity": cap[rid], "pending_after": used[rid],
                  "over": used[rid] > cap[rid]} for rid, n in per.items()}
    out = {"ok": True, "plan": plan, "n": len(hs), "min_reviewers": n_per,
           "blocked": blocked, "applied": False,
           "over": [p["name"] for p in plan.values() if p["over"]]}
    if not apply:
        return out
    done = 0
    for combo, chunk in groups.items():
        done += st.set_assignees_bulk(chunk, list(combo), min_reviewers=n_per, team=team)
    _SV._log_assign(by or "(미상)", "여력만큼 나눔", done, [p["name"] for p in plan.values()], n_per, team)
    if due_at:
        set_wave(due_at, by=by, plan={rid: p["n"] for rid, p in plan.items()}, team=team)
    _SV._agg_bump()
    out.update({"applied": True, "n": done})
    return out


# ── 회수: 정체분 이관 ────────────────────────────────────────────────────────
def rebalance(team=None, apply: bool = False, by: str = "", limit: int = 200) -> dict:
    """정체된 배정을 회수해 여력 있는 사람에게 넘긴다.

    정체 = 배정 후 stale_days 를 넘겼는데 아직 판정하지 않은 슬롯. 배정 시각을 알 수
    없는 스토어에서는(assignment_times 미지원) 진행률 0% + 미완료 보유를 정체로 본다.
    한 콘텐츠에 같은 사람이 둘 들어가지 않도록 이관 대상에서 기존 담당은 제외한다."""
    st = _SV.get_store()
    if not st:
        return {"ok": False, "error": "store unavailable", "moves": [], "n": 0}
    cfg = settings(team)
    data = _crew_compute(team)
    by_id = {m["id"]: m for m in data["members"]}
    stale_ids = {m["id"] for m in data["members"]
                 if m["load"]["pending"] and (m["load"]["stale_days"] >= float(cfg["stale_days"])
                                              or (m["load"]["progress"] == 0.0 and not m["available"])
                                              or not m["available"])}
    if not stale_ids:
        return {"ok": True, "moves": [], "n": 0, "reason": "지금은 넘길 만큼 멈춰 있는 일이 없습니다"}
    takers = [m for m in data["members"] if m["available"] and m["id"] not in stale_ids and m["spare"] > 0]
    if not takers:
        return {"ok": False, "error": "받아줄 여유가 있는 사람이 없습니다", "moves": [], "n": 0}
    try:
        asg = st.assignees(team=team) or {}
        fmap = st.feedback_map(team=team) or {}
        targets = st.review_targets(team) if hasattr(st, "review_targets") else set()
    except Exception:
        return {"ok": False, "error": "누가 무엇을 맡았는지 불러오지 못했습니다", "moves": [], "n": 0}
    done_pairs = {(ch, (v.get("reviewer_id") or v.get("reviewer") or ""))
                  for ch, e in fmap.items() for v in (e.get("verdicts") or [])
                  if v.get("verdict") in ("good", "bad")}
    used = {m["id"]: m["load"]["pending"] for m in takers}
    cap = {m["id"]: max(1, m["weekly_capacity"] or 1) for m in takers}
    heap = [(used[m["id"]] / cap[m["id"]], i, m["id"]) for i, m in enumerate(takers)]
    heapq.heapify(heap)
    moves, changed = [], {}
    for ch, a in sorted(asg.items()):
        if targets and ch not in targets:
            continue
        cur = list(a.get("reviewers") or [])
        for rv in list(cur):
            if rv not in stale_ids or (ch, rv) in done_pairs or len(moves) >= max(1, int(limit)):
                continue
            cand = []                               # 이 콘텐츠에 아직 없는 사람 중 가장 여유 있는 사람
            pick = None
            while heap:
                key = heapq.heappop(heap)
                if key[2] in cur:
                    cand.append(key)
                    continue
                pick = key
                break
            for c in cand:
                heapq.heappush(heap, c)
            if not pick:
                continue
            _, i, to = pick
            cur[cur.index(rv)] = to
            used[to] += 1
            heapq.heappush(heap, (used[to] / cap[to], i, to))
            moves.append({"hash": ch, "from": rv, "from_name": by_id[rv]["name"],
                          "to": to, "to_name": by_id[to]["name"]})
            changed[ch] = (cur, max(1, int(a.get("min") or 1)))
    out = {"ok": True, "moves": moves, "n": len(moves), "applied": False,
           "from_counts": _count(moves, "from_name"), "to_counts": _count(moves, "to_name")}
    if not (apply and moves):
        return out
    for ch, (rvlist, minr) in changed.items():
        st.set_assignees(ch, rvlist, min_reviewers=minr, team=team)
    _SV._log_assign(by or "(미상)", "멈춘 일 넘김", len(moves),
                    sorted({m["to_name"] for m in moves}), 0, team)
    _SV._agg_bump()
    out["applied"] = True
    return out


def _count(rows, key) -> dict:
    out = {}
    for r in rows:
        out[r[key]] = out.get(r[key], 0) + 1
    return out
