"""주간 운영 기록 · 검수운영 현황을 주 단위로 남긴다.

왜 별도 모듈인가: 현황판(crewops)은 '지금'만 보여준다. 주가 지나면 그 주가 어땠는지가
어디에도 남지 않아 "지난주보다 나아졌나"를 물을 수 없었다(사용자 요청 2026-07-28).

**왜 스냅샷을 적립하는가** — 지난 주를 지금 와서 계산하면 값이 흔들린다.
  · 잔여·정체·불일치·처리 가능량은 애초에 '현재 상태'라 과거값이 없다.
  · 검수량을 feedback 에서 역산하면, 재검수 때 (콘텐츠,검수자) 행이 upsert 되면서
    타임스탬프가 최신으로 옮겨가 **과거 주의 실적이 조용히 줄어든다**.
  · 활동 원장(activity_rollup)은 append-only 라 안 줄지만 팀 합계뿐이고 2026-07-22
    부터만 있다(실측: 같은 1주차 검수량이 원장 214건 vs 역산 591건으로 갈렸다).
따라서 **마감된 주는 한 번 계산해 고정**하고, 스냅샷이 없는 과거 주만 역산으로 채우되
`estimated` 로 표시한다. 스냅샷이 쌓일수록 화면이 저절로 정확해진다.

주차 정의(사용자 결정): **월요일~일요일** · 지난주(2026-07-20~26)가 **1주차**.
"""
from __future__ import annotations

import time

from .store import day_key

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입

WEEKLY_KIND = "crew_weekly"     # {weeks: {"1": {...스냅샷...}}}
WEEK1_MONDAY = "2026-07-20"     # 1주차 시작(월) · 운영 시작 시점 기준 고정 앵커
_DAY = 86400


def _to_ord(day: str) -> int:
    """'YYYY-MM-DD' → 일련일수(그레고리력 서수). 표준 라이브러리만 쓰되 date 파싱 최소화."""
    y, m, d = (int(x) for x in day.split("-"))
    return _days_from_civil(y, m, d)


def _days_from_civil(y: int, m: int, d: int) -> int:
    """Howard Hinnant 알고리즘 · 1970-01-01 = 0. (datetime 없이 주차 산술만 하면 충분)"""
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _civil_from_days(z: int) -> str:
    z += 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    y += m <= 2
    return f"{y:04d}-{m:02d}-{d:02d}"


def week_of(day: str) -> int:
    """날짜 → 주차 번호. 1주차 이전이면 0 이하(과거)."""
    return (_to_ord(day) - _to_ord(WEEK1_MONDAY)) // 7 + 1


def week_range(n: int) -> tuple:
    """주차 번호 → (월요일, 일요일) 'YYYY-MM-DD'."""
    start = _to_ord(WEEK1_MONDAY) + (int(n) - 1) * 7
    return _civil_from_days(start), _civil_from_days(start + 6)


def current_week(now=None) -> int:
    return week_of(day_key(now))


def _report(team=None) -> dict:
    try:
        return _SV._report_get(WEEKLY_KIND, team, {}) or {}
    except Exception:
        return {}


def _activity_days(team=None) -> dict:
    """활동 원장(append-only · 팀 합계). 없으면 빈 dict."""
    try:
        return (_SV._report_get("activity_rollup", team, {}) or {}).get("days") or {}
    except Exception:
        return {}


def _derive(n: int, crew: dict, act_days: dict) -> dict:
    """스냅샷이 없는 주를 현재 데이터로 역산. 정확도가 낮아 estimated 로 표시한다."""
    start, end = week_range(n)
    rows = [v for d, v in (act_days or {}).items() if start <= d <= end]
    reviews = sum(int((v or {}).get("reviews") or 0) for v in rows)
    corrections = sum(int((v or {}).get("corrections") or 0) for v in rows)
    burn = [b for b in (crew.get("burndown") or []) if start <= b.get("day", "") <= end]
    done = sum(int(b.get("done") or 0) for b in burn)
    left = burn[-1].get("left") if burn else None   # 그 주 마지막 날 잔여(역산값)
    return {"week": n, "start": start, "end": end, "estimated": True,
            "ledger_days": len(rows), "reviews": reviews, "corrections": corrections,
            "done_assigned": done, "pending_end": left,
            "stale_total": None, "final_pending": None, "golden_n": None,
            "weekly_capacity": None, "members": [], "captured_at": 0}


def _snap(n: int, crew: dict, act_days: dict, now: float) -> dict:
    """마감된 주 1개를 고정값으로 만든다. 흐름 지표는 날짜가 붙어 있어 나중에 계산해도
    같지만, 상태 지표(잔여·정체 등)는 '적립 시점' 값이라 captured_at 을 함께 남긴다."""
    row = _derive(n, crew, act_days)
    s = crew.get("summary") or {}
    row.update({"estimated": False, "captured_at": now,
                "stale_total": s.get("stale_total"), "final_pending": s.get("final_pending"),
                "golden_n": s.get("golden_n"), "weekly_capacity": s.get("weekly_capacity"),
                "targets_n": s.get("targets_n"), "active_members": s.get("active_members")})
    if row.get("pending_end") is None:
        row["pending_end"] = s.get("pending")
    row["members"] = [{"id": m.get("id"), "name": m.get("name"),
                       "done": ((m.get("load") or {}).get("done") or 0),
                       "pending": ((m.get("load") or {}).get("pending") or 0)}
                      for m in (crew.get("members") or [])][:20]
    return row


def capture(team=None, crew=None, now=None) -> int:
    """마감된 주 중 스냅샷이 없는 주를 적립한다. 현황판을 열 때마다 호출되는 게으른 적립이라
    별도 스케줄러가 필요 없다(활동 원장과 같은 방식). 이미 있는 주는 덮지 않는다 —
    나중에 다시 계산하면 재검수 때문에 값이 줄어들기 때문이다.

    반환: 이번에 새로 적립한 주 수."""
    now = time.time() if now is None else now
    cur = current_week(now)
    if cur <= 1:                                     # 아직 마감된 주가 없다
        return 0
    rep = _report(team)
    weeks = rep.setdefault("weeks", {})
    todo = [n for n in range(1, cur) if str(n) not in weeks]
    if not todo:
        return 0
    if crew is None:
        try:
            crew = _SV.crew_data(team=team)
        except Exception:
            return 0
    act = _activity_days(team)
    for n in todo:
        weeks[str(n)] = _snap(n, crew, act, now)
    try:
        _SV._report_save(WEEKLY_KIND, rep, team)
    except Exception:
        return 0
    return len(todo)


def weekly_records(team=None, weeks: int = 8, crew=None, now=None) -> dict:
    """주간 기록 조회. 마감된 주는 스냅샷(없으면 역산+estimated), 이번 주는 진행 중 값."""
    now = time.time() if now is None else now
    cur = current_week(now)
    if crew is None:
        try:
            crew = _SV.crew_data(team=team)
        except Exception:
            crew = {}
    act = _activity_days(team)
    saved = (_report(team).get("weeks") or {})
    out = []
    first = max(1, cur - int(weeks) + 1)
    for n in range(first, cur + 1):
        if n == cur:                                 # 이번 주는 아직 진행 중
            row = _derive(n, crew, act)
            s = crew.get("summary") or {}
            row.update({"estimated": False, "running": True,
                        "pending_end": s.get("pending"), "stale_total": s.get("stale_total"),
                        "final_pending": s.get("final_pending"), "golden_n": s.get("golden_n"),
                        "weekly_capacity": s.get("weekly_capacity")})
        else:
            row = dict(saved.get(str(n)) or _derive(n, crew, act))
            row["running"] = False
        row["label"] = f"{n}주차"
        out.append(row)
    out.reverse()                                    # 최근 주가 위
    return {"ok": True, "week1": WEEK1_MONDAY, "current": cur, "weeks": out}
