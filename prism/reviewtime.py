"""검수 소요 시간 측정 (2026-10-07 · 검수 인력 시간 측정).

콘텐츠 상세 진입(openDetail) → 판정 저장까지를 브라우저가 재서 상세를 떠날 때 한 줄로 보낸다
(POST /review-time · vendor/app-22-reviewtime.js). 저장은 기존 events 테이블(kind=review_time ·
meta=JSON) · DB 구조 변경 없음. 보너스 0 이라 미션 보너스 합계(prism_agg_event_bonus)에 영향 없다.

기록 항목(사용자 결정 2026-10-07):
  · 화면에 머문 시간(wall)과 실제 작업 시간(active) 둘 다 · active 는 탭이 가려졌거나
    IDLE_MS 동안 입력이 없던 구간을 뺀 값
  · 판정 시각(verdict_*)과 교정 메모 저장 시각(note_*)을 따로 · '수정'은 버튼을 누른 순간이 판정,
    교정 메모를 쓰고 완료한 순간이 note
  · 판정 없이 닫으면 outcome=abandoned · 이미 내가 판정한 콘텐츠를 다시 열면 outcome=revisit
  · 콘텐츠 해시·판정·골드 여부·본문 길이

과거 추정치(kind=review_time_est)는 판정 기록의 연속 판정 간격으로 1회 적재했다
(crewops.capacity 와 같은 규칙: 1초 미만·10분 초과 간격은 버림 · meta.method=gap).
실측과 섞지 않고 따로 집계한다.
"""
from __future__ import annotations

import statistics

IDLE_MS = 120_000                # 입력 없는 구간을 작업 시간에서 빼는 기준(브라우저와 같은 값)
MAX_MS = 4 * 3600 * 1000         # 한 번 진입의 상한 · 넘는 값은 잘라 저장(탭을 켜 둔 채 퇴근 등)
RECORDED_MAX_AGE = 7 * 86400     # recorded_at 허용 과거 범위(초)
RECORDED_SKEW = 300              # recorded_at 허용 미래 오차(초 · 브라우저 시계)
OUTCOMES = ("verdict", "abandoned", "revisit")
_MS_FIELDS = ("wall_ms", "active_ms", "verdict_wall_ms", "verdict_active_ms", "note_wall_ms", "note_active_ms")


def _ms(v):
    if v is None or v == "":
        return None
    try:
        return max(0, min(MAX_MS, int(float(v))))
    except (TypeError, ValueError):
        raise ValueError("시간 값이 숫자가 아닙니다") from None


def clean(data: dict) -> dict:
    """브라우저 기록 → 저장할 meta. 신뢰 경계라 형식·범위를 여기서 고정한다."""
    h = str((data or {}).get("hash") or "").strip()
    if not h or len(h) > 96:
        raise ValueError("콘텐츠 해시가 필요합니다")
    outcome = str(data.get("outcome") or "")
    if outcome not in OUTCOMES:
        raise ValueError("허용하지 않는 결과 값입니다")
    import uuid
    event_id = data.get("event_id")
    if event_id:
        try:
            event_id = str(uuid.UUID(str(event_id)))
        except ValueError:
            raise ValueError("시간 기록 ID가 올바르지 않습니다") from None
    meta = {"hash": h, "outcome": outcome, "verdict": str(data.get("verdict") or "")[:16]}
    if event_id:
        meta["event_id"] = event_id
    if data.get("recorded_at") is not None:
        import time, math
        try:
            recorded, now = float(data["recorded_at"]), time.time()
            # 대기열 재전송까지 7일 · 시계 오차 5분까지만 받는다(그 밖은 과거·미래 일자 집계를 오염)
            if not math.isfinite(recorded) or not now - RECORDED_MAX_AGE <= recorded <= now + RECORDED_SKEW:
                raise ValueError()
            meta["recorded_at"] = min(recorded, now)
        except (TypeError, ValueError):
            raise ValueError("기록 시각이 올바르지 않습니다") from None
    for k in _MS_FIELDS:
        meta[k] = _ms(data.get(k))
    if meta["wall_ms"] is None or meta["active_ms"] is None:
        raise ValueError("머문 시간과 작업 시간이 필요합니다")
    meta["active_ms"] = min(meta["active_ms"], meta["wall_ms"])      # 작업 시간은 머문 시간을 넘을 수 없다
    if outcome == "verdict" and meta["verdict_wall_ms"] is None:
        raise ValueError("판정 시각이 없습니다")
    try:
        meta["body_len"] = max(0, int(data.get("body_len") or 0))
    except (TypeError, ValueError):
        meta["body_len"] = 0
    meta["gold"] = bool(data.get("gold")) or h.startswith("gold:")
    meta["src"] = str(data.get("src") or "")[:24]
    return meta


def parse(events: list) -> list:
    """저장소 events_since 결과 → stats 입력(meta JSON 해석 · 한국 시간 일자). 깨진 meta 행은 버린다."""
    import json
    import time
    out = []
    for e in events or []:
        try:
            m = json.loads(e.get("meta") or "{}")
        except (TypeError, ValueError):
            continue
        out.append({**e, "meta": m if isinstance(m, dict) else {},
                    "day": time.strftime("%Y-%m-%d", time.gmtime(float(e.get("ts") or 0) + 9 * 3600))})
    return out


def _pct(xs: list, p: float):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


def _sec(v):
    return round(v / 1000, 1) if v is not None else None


def stats(rows: list, names: dict = None) -> dict:
    """events 행([{reviewer, kind, meta(dict), ts}]) → 검수자별·일자별 요약.
    기본 지표 = 처음 판정한 진입(outcome=verdict · 골드 제외)의 판정까지 작업 시간 중앙값·상위 10%.
    추정치(review_time_est)는 est_* 로만 따로 낸다."""
    names = names or {}
    by_rv, by_day = {}, {}
    for r in rows:
        m, rid = r.get("meta") or {}, r.get("reviewer") or ""
        d = by_rv.setdefault(rid, {"act": [], "wall": [], "note": [], "abandoned": 0, "revisit": 0, "gold": 0, "est": []})
        day = r.get("day") or ""
        if r.get("kind") == "review_time_est":
            if m.get("est_ms"):
                d["est"].append(m["est_ms"])
            continue
        if m.get("gold"):
            d["gold"] += 1
            continue
        oc = m.get("outcome")
        if oc in ("abandoned", "revisit"):
            d[oc] += 1
            continue
        if oc != "verdict" or m.get("verdict_active_ms") is None:
            continue
        d["act"].append(m["verdict_active_ms"])
        d["wall"].append(m.get("verdict_wall_ms") or 0)
        if m.get("note_active_ms") is not None:
            d["note"].append(m["note_active_ms"])
        dd = by_day.setdefault(day, [])
        dd.append(m["verdict_active_ms"])
    people = []
    for rid, d in by_rv.items():
        people.append({"reviewer": rid, "name": names.get(rid) or rid, "n": len(d["act"]),
                       "active_med_s": _sec(statistics.median(d["act"])) if d["act"] else None,
                       "active_p90_s": _sec(_pct(d["act"], 0.9)),
                       "wall_med_s": _sec(statistics.median(d["wall"])) if d["wall"] else None,
                       "note_med_s": _sec(statistics.median(d["note"])) if d["note"] else None,
                       "abandoned": d["abandoned"], "revisit": d["revisit"], "gold": d["gold"],
                       "est_n": len(d["est"]),
                       "est_med_s": _sec(statistics.median(d["est"])) if d["est"] else None})
    people.sort(key=lambda p: (-p["n"], -p["est_n"], p["name"]))
    days = [{"day": k, "n": len(v), "active_med_s": _sec(statistics.median(v))} for k, v in sorted(by_day.items())]
    return {"ok": True, "people": people, "days": days, "idle_s": IDLE_MS // 1000}
