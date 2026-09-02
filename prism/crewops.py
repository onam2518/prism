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
OWNER_KIND = "crew_owner"       # 담당 규칙 {items:[{kind: service|category, value, reviewers:[uid…]}]}
OWNER_KINDS = ("service", "category")   # 서비스(displayServiceName) · 주제(Tier1 분류)
OWNER_MAX_RULES = 200

STATUSES = ("active", "onboarding", "leave", "inactive")

DEFAULT_SETTINGS = {
    "rate_cap_per_hour": 90,    # 과속 상한 · 40초/건보다 빠른 속도는 캐파로 인정하지 않는다(본문 미독 구간)
    "buffer": 0.8,              # 여유율 · 약속 시간을 100% 검수로 채울 수 있는 사람은 없다
    "stale_days": 3,            # 배정 후 손 안 댄 기간이 이만큼이면 정체
    "speed_floor_sec": 10,      # 건당 이보다 빠르면 코칭 플래그(과속)
    "gold_min_acc": 0.6,        # 골드 정확도 하한(표본 5건 이상일 때만 판정)
    "calib_target": 20,         # 온보딩 캘리브레이션 문항 수 · 통과 전에는 정식 배정 제외
    "default_hours": 2.0,       # 주간 약속 시간 미입력자의 잠정값(원장 입력 전 계획이 0 이 되는 것 방지)
    # ── 과거 실측 vs 이번 주 신고: 무엇에 무게를 둘지 ──────────────────────────
    # 실측 속도만으로 캐파를 잡으면 배정이 빠른 소수에게 쏠린다(운영 실측 2026-07-28:
    # 신고 시간이 전원 기본값이라 캐파 차이가 전적으로 속도에서 나와 4.1배 · 상위 3명 40%).
    # 속도를 팀 중앙값 쪽으로 당겨(shrink) 캐파가 '이번 주에 내겠다고 한 시간'에 더
    # 비례하게 만든다. 1=실측 그대로 · 0=전원 팀 중앙값(순수 시간 비례).
    "rate_shrink": 0.5,
    # 이번 주 본인 확인을 안 한 사람의 신고 시간은 지난주 값이라 근거가 약하다 —
    # 조금 보수적으로 잡되 0 으로 만들지는 않는다(일이 아예 안 가면 그것도 쏠림이다).
    "unconfirmed_factor": 0.8,
    # ── 배정 하한·상한: 밀린 사람에게 더 얹지 않되, 놀리지도 않는다 ─────────────
    # 하한 — 아무도 캐파의 이만큼을 채우기 전에는 다음 사람으로 넘어가지 않는다.
    # 소수에게 몰아주고 나머지를 0건으로 두는 것도 쏠림이다(사용자 결정 2026-07-28).
    "min_fill_ratio": 0.5,
    # 상한 — 잔여가 이미 캐파의 이 배를 넘긴 사람은 새 배정을 뒤로 미룬다. 성과 판단이
    # 아니라 용량 판단이다(더 줘도 그 주에 못 하고 그 콘텐츠까지 같이 정체된다).
    # 다만 팀 전체가 초과면 배정 자체가 멈추므로, 다른 후보가 없을 때는 받는다.
    "cap_limit": 1.0,
    # ── 자동 운영(기본 꺼짐) · 사람이 켜야 돈다. 남의 일을 옮기는 동작이라 기본값은 수동 ──
    "auto_wave": 0,             # 1 = 주 사이클이 시작되면 아직 아무도 안 맡은 것을 여력만큼 자동 배분
    "auto_rebalance": 0,        # 1 = 기한 하루 전에 멈춰 있는 일을 여유 있는 사람에게 자동 이관
    "wave_weekday": 0,          # 사이클 시작 요일(0=월)
    "wave_hour": 10,            # 사이클 시작 시각(팀 타임존 · 기본 KST 10시)
    "wave_days": 4,             # 기한 = 시작 + N일(기본 금요일 오전 10시)
    "wave_batch": 300,          # 한 사이클에 자동으로 내보낼 최대 건수
    "wave_min_reviewers": 2,    # 자동 배분 시 콘텐츠당 담당 수
    "auto_escalate": 0,         # 1 = 의견이 갈린 건에 3번째 검수자를 자동으로 붙임
    # 아래 둘은 '누가 무엇을 받을지'의 순서만 바꾼다(총량·공평은 그대로) → 기본 켬
    "match_strength": 1,        # 그 분야를 잘 보는 사람에게 우선 배정
    "lack_first": 1,            # 정답셋이 부족한 분류를 먼저 배정
}

# 키별 허용 범위(최소, 최대). 운영 파라미터는 화면에서 손으로 넣는 값이라 오입력 한 번에
# 배정 계획·신호등이 통째로 무의미해진다 — 음수 buffer 는 주간 캐파를 음수로 만들고(실측 -25),
# 그 뒤 `cap = max(1, weekly or 1)` 때문에 전원 캐파 1 로 붕괴해 모두가 '초과' 구간으로 떨어졌다.
# 음수 stale_days 는 미완료가 있는 전원을 즉시 노랑·빨강으로 만든다. 그래서 저장 시점에 클램프한다.
SETTINGS_RANGE = {
    "rate_cap_per_hour": (1, 600),      # 시간당 상한(40초/건 = 90 이 기본)
    "buffer": (0.1, 1.0),               # 여유율 · 0 이하면 캐파가 0/음수
    "stale_days": (1, 30),              # 정체 판정일 · 0 이하면 전원 즉시 정체
    "speed_floor_sec": (1, 600),
    "gold_min_acc": (0.0, 1.0),
    "calib_target": (1, 200),
    "default_hours": (0.5, 40.0),       # set_profile 의 hours_per_week 상한(40)과 같은 눈금
    "rate_shrink": (0.0, 1.0),
    "unconfirmed_factor": (0.1, 1.0),
    "min_fill_ratio": (0.0, 1.0),
    "cap_limit": (0.1, 5.0),
    "auto_wave": (0, 1),
    "auto_rebalance": (0, 1),
    "wave_weekday": (0, 6),
    "wave_hour": (0, 23),
    "wave_days": (1, 14),
    "wave_batch": (1, 5000),
    "wave_min_reviewers": (1, 10),
    "auto_escalate": (0, 1),
    "match_strength": (0, 1),
    "lack_first": (0, 1),
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


def _fmt_num(x) -> str:
    return f"{x:g}"


def set_settings(patch: dict, team=None) -> dict:
    """운영 파라미터 저장(허용 범위로 클램프). 반환은 저장 후 설정 전체.

    손대거나 버린 값은 `_notes` 에 사유를 담는다 — 종전에는 모르는 키·숫자가 아닌 값·
    범위 밖 값이 전부 조용히 무시되거나 그대로 저장돼, 사용자는 저장이 됐는지조차 알 수 없었다."""
    cur = dict(((_SV._report_get(SETTINGS_KIND, team, {}) or {}).get("items") or {}))
    notes = []
    for k, v in (patch or {}).items():
        if k not in DEFAULT_SETTINGS:
            notes.append(f"{k}: 모르는 설정이라 저장하지 않았습니다")
            continue
        is_float = isinstance(DEFAULT_SETTINGS[k], float)
        try:
            val = float(v) if is_float else int(v)
        except (TypeError, ValueError):
            notes.append(f"{k}: 숫자로 읽을 수 없어 종전 값을 그대로 둡니다")
            continue
        rng = SETTINGS_RANGE.get(k)
        if rng:
            lo, hi = rng
            fixed = max(lo, min(hi, val))
            if fixed != val:
                notes.append(f"{k}: {_fmt_num(lo)}~{_fmt_num(hi)} 안에서만 쓸 수 있어 "
                             f"{_fmt_num(val)} → {_fmt_num(fixed)} 로 맞췄습니다")
            val = float(fixed) if is_float else int(fixed)
        cur[k] = val
    _SV._report_save(SETTINGS_KIND, {"items": cur}, team)
    _SV._agg_bump()
    out = settings(team)
    if notes:
        out["_notes"] = notes
    return out


def profiles(team=None) -> dict:
    """인력 원장 {uid: profile} · 기록 없는 사람은 기본 프로필로 취급(crew_data 에서 채움)."""
    return dict(((_SV._report_get(PROFILE_KIND, team, {}) or {}).get("items") or {}))


def _blank_profile(cfg: dict) -> dict:
    return {"hours_per_week": float(cfg["default_hours"]), "workdays": [0, 1, 2, 3, 4],
            "status": "active", "leave_from": "", "leave_to": "", "note": "",
            "rate_override": 0, "confirmed": False, "confirmed_week": 0}


def _current_week() -> int:
    """현재 주차(weekops 원천). 주차 정의가 두 벌이 되면 확인 만료 시점이 어긋난다."""
    try:
        from .weekops import current_week
        return int(current_week())
    except Exception:
        return 0


def needs_confirm(uid: str, team=None) -> dict:
    """이번 주 본인 확인이 필요한지. 검수자는 주가 바뀌면 다시 확인해야 한다.

    이번 주차 확인이 없으면 needed=True 와 함께 현재 프로필을 돌려준다(팝업이 그대로 채운다).
    프로필이 아예 없는 신규 검수자도 needed=True — 첫 확인으로 기본값을 본인이 승인한다."""
    uid = (uid or "").strip()
    cur = _current_week()
    if not uid or cur <= 0:
        return {"ok": True, "needed": False, "week": cur}
    prof = dict((profiles(team) or {}).get(uid) or _blank_profile(settings(team)))
    needed = int(prof.get("confirmed_week") or 0) != cur
    out = {"ok": True, "needed": needed, "week": cur, "profile": prof}
    try:                                            # 팝업이 '언제부터 언제까지'를 밝힐 수 있게
        from .weekops import week_range
        out["start"], out["end"] = week_range(cur)
    except Exception:
        pass
    return out


# 본인 확인 팝업이 스스로 고칠 수 있는 항목(화이트리스트). /crew-confirm 은 게이트가 login 이라
# 전 검수자에게 열려 있는데, 종전에는 patch 를 통째로 set_profile 에 넘겨 **관리자 전용 HR 필드까지**
# 본인이 바꿀 수 있었다 — 특히 `rate_override`(처리율 수동 상한 = 자기 배정량 조작)와
# `leave_from/leave_to`(임의 기간 부재 등록)는 화면 어디에도 본인 입력란이 없는 관리자 값이다.
# 그 둘은 /crew-profile(admin)에만 남긴다.
# `status` 는 뺄 수 없다 — 주간 확인 팝업이 상태 드롭다운을 직접 제공하고("휴가·교육 중·비활성으로
# 두면 이번 주 배정에서 빠집니다" · ui/19b-crew.html) 그 값을 보내는 설계다. 여기서 막으면
# 검수자가 고른 상태가 조용히 저장되지 않는다(무반응 저장 = 또 다른 조용한 유실).
CONFIRM_FIELDS = ("hours_per_week", "workdays", "note", "status")


def confirm_week(uid: str, patch=None, team=None) -> dict:
    """본인 확인(+ 그 자리에서 고친 이번 주 일정). 본인만 호출한다(라우트가 uid 를 강제).
    받는 항목은 CONFIRM_FIELDS 로 좁힌다(관리자 전용 HR 필드는 여기로 들어오지 않는다)."""
    uid = (uid or "").strip()
    if not uid:
        return {"ok": False, "error": "로그인이 필요합니다"}
    body = {k: v for k, v in (patch or {}).items() if k in CONFIRM_FIELDS}
    body["confirmed"] = True                        # 확인 주차(confirmed_week)는 서버가 정한다(위조 방지)
    r = set_profile(uid, body, team=team, by=uid)
    if r.get("ok"):
        r["week"] = _current_week()
    return r


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
        # 확인은 '주차 단위'로 유효하다. 주간 가용 시간은 그 주의 약속이라, 주가 바뀌면
        # 지난주 확인은 이번 주 근거가 못 된다(사용자 결정 2026-07-28).
        p["confirmed"] = bool(patch.get("confirmed"))
        p["confirmed_week"] = int(_current_week()) if p["confirmed"] else 0
    if "confirmed_week" in patch:                   # 테스트·복구용 직접 지정
        try:
            p["confirmed_week"] = max(0, int(patch.get("confirmed_week") or 0))
        except (TypeError, ValueError):
            p["confirmed_week"] = 0
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
    # 같은 기한으로 다시 배정하면 같은 웨이브(추가 배정 · 계획 합산), 기한이 바뀌면 새 사이클.
    # opened_at 이 '이번 배정' 현황의 스코프 경계라서, 여기가 안 갈리면 옛 배정이 계속 섞인다.
    cur = wave(team)
    same = abs(float(cur.get("due_at") or 0) - due) < 1.0
    merged = dict(cur.get("plan") or {}) if same else {}
    for rid, n in (plan or {}).items():
        merged[rid] = merged.get(rid, 0) + n
    item = {"due_at": due, "opened_at": ((cur.get("opened_at") if same else 0) or time.time()),
            "by": (by or "")[:80], "plan": merged}
    _SV._report_save(WAVE_KIND, {"item": item}, team)
    _SV._agg_bump()
    return {"ok": True, "wave": item}


def adjust_due(due_at, by: str = "", team=None) -> dict:
    """진행 중인 웨이브의 기한만 조정(연장·단축). opened_at·plan 은 유지한다 —
    set_wave 는 기한이 바뀌면 새 사이클로 여니, '같은 배정 묶음인데 기한을 늘리고
    싶다'는 운영 요구는 이 경로로 온다. 조정 내역은 배정 이력에 남긴다."""
    cur = wave(team)
    if not cur.get("due_at"):
        return {"ok": False, "error": "진행 중인 배정 묶음이 없습니다 · 배정할 때 기한을 함께 걸어주세요"}
    try:
        due = float(due_at or 0)
    except (TypeError, ValueError):
        due = 0.0
    if due <= 0:
        return {"ok": False, "error": "기한을 읽을 수 없습니다"}
    old = float(cur["due_at"])
    item = dict(cur)
    item["due_at"] = due
    item["by"] = (by or "")[:80]
    _SV._report_save(WAVE_KIND, {"item": item}, team)
    _SV._agg_bump()

    def _fmt(t):
        return time.strftime("%m/%d %H:%M", time.localtime(t))

    _SV._log_assign(by or "(미상)", "기한 조정 " + _fmt(old) + " → " + _fmt(due), 0, [], 0, team)
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
    """배정 계산에 쓰는 시간당 처리율. 과속은 캐파가 아니라 품질 경보라 상한을 씌운다.

    상한을 씌운 뒤 팀 중앙값 쪽으로 rate_shrink 만큼 당긴다. 과거 실측만으로 캐파를
    잡으면 빠른 소수에게 배정이 쏠리는데, 정작 '이번 주에 얼마나 낼 수 있는지'는
    본인이 주차마다 신고한다 — 그쪽에 무게를 옮기기 위한 장치다(수동 상한 rate_override
    는 사람이 명시한 값이라 수축하지 않는다)."""
    ovr = float(prof.get("rate_override") or 0)
    if ovr:
        return max(1.0, min(float(cfg["rate_cap_per_hour"]), ovr))
    r = float((meas or {}).get("rate_per_hour") or 0) or team_rate
    r = max(1.0, min(float(cfg["rate_cap_per_hour"]), r))
    try:
        k = min(1.0, max(0.0, float(cfg.get("rate_shrink", 1.0))))
    except (TypeError, ValueError):
        k = 1.0
    base = max(1.0, float(team_rate or 0) or r)
    return max(1.0, base + (r - base) * k)


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


def _crew_compute(team=None, scope_uid: str = "", raw_out=None) -> dict:
    """raw_out(dict)을 주면 내부에서 조회한 원천 테이블(asg·fmap·targets·golden)을 담아
    돌려준다 — rebalance·escalate_split·plan_distribute 가 직후에 같은 테이블을 전량
    재조회하지 않게(왕복 공유). 조회에 실패한 키는 담지 않는다(호출측이 폴백 판단)."""
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
        if raw_out is not None:
            raw_out["fmap"] = fmap
    except Exception:
        fmap = {}
    try:                                            # 배정 현황 + 배정 시각을 1회 조회로(왕복 축소)
        if hasattr(st, "assignments_snapshot"):
            asg, asg_ts = st.assignments_snapshot(team)
        else:
            asg, asg_ts = (st.assignees(team=team) or {}), _assign_ts(team)
        if raw_out is not None:
            raw_out["asg"] = asg
            raw_out["asg_ts"] = asg_ts              # 슬롯별 배정 시각 · rebalance 의 슬롯 단위 정체 판정용
    except Exception:
        asg, asg_ts = {}, {}
    try:                                            # 배정 해시를 넘겨 assignments 재조회 생략
        targets = (st.review_targets(team, assigned=set(asg))
                   if hasattr(st, "review_targets") else set())
        if raw_out is not None:
            raw_out["targets"] = targets
    except Exception:
        targets = set()
    try:
        golden = st.golden_hashes(team) or set()
        if raw_out is not None:
            raw_out["golden"] = golden
    except Exception:
        golden = set()
    try:
        gold = st.gold_stats(team) if hasattr(st, "gold_stats") else {}
    except Exception:
        gold = {}
    try:                                            # fmap·gold 를 넘겨 같은 테이블 재조회 생략
        weights = _SV.reviewer_weights(team, fmap=fmap, gold=gold) or {}
    except Exception:
        weights = {}
    try:
        roles = _SV.reviewer_roles(team) or {}
    except Exception:
        roles = {}

    meas = capacity(team, fmap=fmap)
    profs = profiles(team)
    cur_week = _current_week()          # 이번 주 신고 확인 여부 판정용(주차 정의는 weekops 단일 원천)
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

    opened_at = float(wv.get("opened_at") or 0)
    wave_on = due_at > 0 and opened_at > 0
    load = {}                                       # uid → [배정, 미완료, 최고 정체일, 웨이브 배정, 웨이브 미완료]
    for ch, a in asg.items():
        if targets and ch not in targets:
            continue                                # 삭제·확정된 콘텐츠의 고아 배정은 부하가 아니다
        for rv in (a.get("reviewers") or []):
            c = load.setdefault(rv, [0, 0, 0.0, 0, 0])
            c[0] += 1
            ts = asg_ts.get((ch, rv), 0)
            # '이번 배정' 스코프 = 웨이브 시작 이후 배정된 슬롯. 배정 시각을 아예 못 주는
            # 스토어(asg_ts 전체가 빈 dict)면 전량 포함으로 완만히 퇴화하고, 일부 슬롯만
            # 시각이 없으면 시각 추적 이전의 옛 배정이라 웨이브 밖으로 본다.
            in_wave = wave_on and (ts >= opened_at - 1 if ts else not asg_ts)
            if in_wave:
                c[3] += 1
            if (ch, rv) not in done_pairs:
                c[1] += 1
                if in_wave:
                    c[4] += 1
                age = (now - ts) / 86400 if ts else 0.0
                c[2] = max(c[2], age)

    # 명단의 기준은 **현재 팀원(rvs)** 이다. 판정 이력·프로필·배정만 남은 id 는 팀에서
    # 빠진 사람이라, 명단에 남으면 배정 후보·팀 캐파·평균에 계속 섞인다
    # (2026-07-28 신고: 팀에서 제거한 '테스트'가 운영 관리에 계속 노출 — 프로필만 남아 있었다).
    # rvs 조회가 실패해 비면 화면이 통째로 비므로, 그때만 이력 합집합으로 폴백한다.
    seen = set(rvs) | set(meas) | set(profs) | set(load)
    ids = sorted((seen & set(rvs)) if rvs else seen,
                 key=lambda i: -(meas.get(i, {}).get("n28", 0)))
    # 팀에서 빠졌는데 배정이 남아 있으면 그 콘텐츠는 아무도 못 본다 — 조용히 사라지지 않게 남긴다
    orphan = sorted({rv for rv in load if rv not in ids and (load.get(rv) or [0, 0, 0.0])[1] > 0}) if rvs else []
    members = []
    for uid in ids:
        if scope_uid and uid != scope_uid:
            continue
        prof = dict(_blank_profile(cfg))
        prof.update(profs.get(uid) or {})
        m = meas.get(uid) or {}
        lo = load.get(uid) or [0, 0, 0.0, 0, 0]
        rate = _effective_rate(m, prof, cfg, team_rate)
        # 이번 주 본인 확인 여부 반영: 확인된 신고 시간은 그대로, 미확인은 지난주 값이라
        # 조금 보수적으로 본다(0 으로 만들지는 않는다 — 일이 아예 안 가는 것도 쏠림이다).
        fresh = int(prof.get("confirmed_week") or 0) == cur_week
        conf_k = 1.0 if fresh else max(0.1, min(1.0, float(cfg.get("unconfirmed_factor", 1.0) or 1.0)))
        weekly = round(rate * float(prof.get("hours_per_week") or 0) * float(cfg["buffer"]) * conf_k)
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
            "confirmed_fresh": fresh, "conf_factor": round(conf_k, 2),
            "measured": {"rate_per_hour": m.get("rate_per_hour", 0.0), "median_sec": m.get("median_sec", 0.0),
                         "effective_rate": round(rate, 1), "capped": rate < float(m.get("rate_per_hour") or 0),
                         "estimated": not m.get("measured", False), "n_total": m.get("n_total", 0),
                         "n28": m.get("n28", 0), "n7": m.get("n7", 0),
                         "active_days": m.get("active_days", 0), "sessions": m.get("sessions", 0),
                         "last_ts": m.get("last_ts", 0)},
            "load": {"assigned": lo[0], "pending": lo[1], "done": lo[0] - lo[1],
                     "stale_days": round(lo[2], 1),
                     "progress": round((lo[0] - lo[1]) / lo[0], 4) if lo[0] else None,
                     # '이번 배정'(웨이브 시작 이후 배정분) · 기한 미설정이면 None
                     "wave": ({"assigned": lo[3], "pending": lo[4], "done": lo[3] - lo[4],
                               "progress": round((lo[3] - lo[4]) / lo[3], 4) if lo[3] else None}
                              if wave_on else None)},
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
    try:                                            # 담당 규칙(서비스·주제별 전담) · 화면 편집용 선택지
        out["owner_rules"] = owner_rules(team)
        out["owner_options"] = owner_options(team)
    except Exception:
        out["owner_rules"], out["owner_options"] = [], {"service": [], "category": []}
    orphan_info = [{"id": rv, "name": (rvs.get(rv) or {}).get("name") or rv[:8],
                    "pending": (load.get(rv) or [0, 0, 0.0])[1]} for rv in orphan]
    out["summary"] = _summary(members, fmap, targets, golden, cfg, due_at, now, team_rate,
                              orphan_info=orphan_info)
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
        out.append({"id": "speed", "label": "속도 이상",
                    "hint": f"건당 {ms['median_sec']:.0f}초 · 본문 통독이 어려운 속도"})
    q = card["quality"]
    if q["gold_n"] >= 5 and q["gold_acc"] < float(cfg["gold_min_acc"]):
        out.append({"id": "gold", "label": "골드 정답률 낮음",
                    "hint": f"골드 문항 정답률 {q['gold_acc']:.0%}"})
    if (q["good_ratio"] is not None and q["n_judged"] >= _MIN_JUDGE_SAMPLE
            and abs(q["good_ratio"] - team_good) >= 0.25):
        out.append({"id": "drift", "label": "팀 기준과 편차",
                    "hint": f"정확 판정 {q['good_ratio']:.0%} · 팀은 {team_good:.0%}"})
    if card["profile"].get("status") == "onboarding" and q["gold_n"] < int(cfg["calib_target"]):
        out.append({"id": "calib", "label": "기준 보정 중",
                    "hint": f"골드 문항 {q['gold_n']}개 / 기준 {int(cfg['calib_target'])}개"})
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
    # 기한 대비 진행은 '이번 배정' 기준이 정확하다 — 누적 진행률은 옛 배정에 희석돼
    # 이번 몫을 다 끝낸 사람이 마감 임박 경고를 받는다. 정체(stale)는 누적 그대로.
    wvl = card["load"].get("wave")
    prog = ((wvl["progress"] if wvl and wvl["assigned"] else card["load"]["progress"])
            or 0.0)
    stale = card["load"]["stale_days"]
    if (due_at and now > due_at) or (stale >= float(cfg["stale_days"]) * 2 and prog < 0.2):
        return "red"
    if stale >= float(cfg["stale_days"]) or (due_at and (due_at - now) < 86400 and prog < 0.5):
        return "yellow"
    return "green"


def _summary(members, fmap, targets, golden, cfg, due_at, now, team_rate, orphan_info=None) -> dict:
    """현황판 요약: 남은 일의 크기 · 팀 캐파 · 예상 완료일 · 유휴 · 정체 · 2층 대기.

    orphan_info = 팀에서 빠졌는데 미완료 배정이 남은 사람들(그 콘텐츠는 아무도 못 본다)."""
    orphan_info = list(orphan_info or [])
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
    wave_sum = None                                 # '이번 배정' 집계 · 기한 미설정이면 None
    wv_loads = [m["load"].get("wave") for m in members]
    if any(w is not None for w in wv_loads):
        wa = sum(w["assigned"] for w in wv_loads if w)
        wp = sum(w["pending"] for w in wv_loads if w)
        wave_sum = {"assigned": wa, "pending": wp, "done": wa - wp,
                    "progress": round((wa - wp) / wa, 4) if wa else None,
                    "due_at": due_at}
    return {"pending": pending, "hours_left": hours_left, "weekly_capacity": weekly,
            "orphan": orphan_info, "wave": wave_sum,
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
# 강점 보정 폭(건 단위): 잘 보는 분야라도 이만큼까지만 앞당긴다. 용량 공평이 먼저다.
MATCH_ITEMS = 5.0


def _name_of(members, rid: str) -> str:
    for m in members or []:
        if m.get("id") == rid:
            return m.get("name") or rid
    return rid


def _assignable(members) -> list:
    """기초 검수 배정 대상. **최종검수자는 제외한다**(사용자 결정 2026-07-28).

    최종검수자는 기초 판정이 갈렸을 때 확정하는 2층 역할이라, 같은 콘텐츠의 기초 검수를
    맡으면 자기 판정을 자기가 확정하게 된다. 자동(여력 비례·재배정·불일치 추가)과 수동
    (직접 지정) 어느 쪽에서도 후보에 넣지 않는다."""
    return [m for m in members if not m.get("is_final")]


def plan_distribute(hashes, min_reviewers: int = 1, reviewers=None, team=None,
                    apply: bool = False, by: str = "", due_at=None,
                    match=None, lack_first=None) -> dict:
    """캐파 비례 배정(균등 분배의 대체). 콘텐츠당 서로 다른 담당 N명.

    reviewops.distribute_assignments 는 '미완료 건수'만 보고 나눠서, 시간당 22건 하는
    사람과 500건 하는 사람이 같은 짐을 졌다. 여기서는 각자의 남은 여력
    (주간 캐파 - 현재 미완료)에 비례해 나누고, 부재·온보딩·휴면은 애초에 제외한다.
    apply=False 면 계획만 돌려준다(미리보기 후 실행이 배정 사고를 줄인다)."""
    st = _SV.get_store()
    hs = [h for h in dict.fromkeys(hashes or []) if h]
    if not (st and hs):
        return {"ok": False, "error": "배정할 콘텐츠가 없습니다", "plan": {}, "n": 0}
    raw = {}
    data = _crew_compute(team, raw_out=raw)         # 캐시 우회: 방금 바뀐 부하를 반영해야 한다
    pool = _assignable([m for m in data["members"] if m["available"]])
    if reviewers:
        want = set(reviewers)
        pool = [m for m in pool if m["id"] in want]
        blocked = [m["name"] for m in data["members"] if m["id"] in want and not m["available"]]
    else:
        blocked = []
    if not pool:
        return {"ok": False, "error": "배정할 수 있는 검수자가 없습니다(휴가·교육 중·비활성·최종검수자 제외)",
                "plan": {}, "n": 0, "blocked": blocked}
    n_per = max(1, min(len(pool), int(min_reviewers or 1)))
    cap = {m["id"]: max(1, m["weekly_capacity"] or 1) for m in pool}
    used = {m["id"]: m["load"]["pending"] for m in pool}     # 시작 부하 = 이미 밀린 내 몫
    cfg = settings(team)
    try:
        fill_ratio = min(1.0, max(0.0, float(cfg.get("min_fill_ratio", 0.0) or 0.0)))
    except (TypeError, ValueError):
        fill_ratio = 0.0
    try:
        cap_limit = max(0.1, float(cfg.get("cap_limit", 1.0) or 1.0))
    except (TypeError, ValueError):
        cap_limit = 1.0
    use_match = int(cfg["match_strength"]) if match is None else int(bool(match))
    use_lack = int(cfg["lack_first"]) if lack_first is None else int(bool(lack_first))
    if use_lack:
        hs = prioritize(hs, team)                    # 정답셋이 부족한 분류를 먼저 내보낸다
    strengths = category_reliability(team, fmap=raw.get("fmap")) if use_match else {}   # fmap 재사용
    rules = owner_rules(team)
    need_cats = use_match or any(r.get("kind") == "category" for r in rules)
    cats = content_categories(team) if need_cats else {}
    svcs = content_services(team) if any(r.get("kind") == "service" for r in rules) else {}
    oidx = _owner_index(rules)
    owned_ids = {x for r in rules for x in (r.get("reviewers") or [])}   # 담당 영역이 있는 사람
    ids = [m["id"] for m in pool]
    idx = {m["id"]: i for i, m in enumerate(pool)}   # 동률 시 선택 순서 유지

    floors = {m["id"]: round(cap[m["id"]] * fill_ratio) for m in pool}   # 보장 하한(캐파의 절반)

    def _score(rid, cs):
        """작을수록 먼저 받는다. 세 구간으로 나눠 '하한 → 정상 → 초과' 순으로 채운다.

        · 보장(-2~): 아직 캐파의 min_fill_ratio 를 못 채운 사람. 전원이 하한을 넘기
          전에는 아무도 그 위로 못 간다 — 소수에게 몰아주고 나머지를 0건으로 두는 것도 쏠림.
        · 정상(0~): 여력 소진율(부하/캐파) 순.
        · 초과(100~): 잔여가 이미 캐파의 cap_limit 배를 넘은 사람. 더 줘도 그 주에 못 하고
          그 콘텐츠까지 같이 정체된다 — 다른 후보가 없을 때만 받는다(배정이 멈추지 않게).

        분야 강점은 구간 안에서 순서만 바꾼다(구간을 뒤집지는 못한다)."""
        u = used[rid]
        adj = u
        if strengths and cs:
            vals = [v for v in (strengths.get(rid, {}).get(c) for c in cs) if v is not None]
            if vals:
                norm = max(0.0, (sum(vals) / len(vals) - 0.5) * 2)   # 합치율 0.5~1.0 → 0~1
                adj -= MATCH_ITEMS * norm
        fl = floors.get(rid, 0)
        if fl > 0 and u < fl:
            return -2.0 + adj / fl
        if u < cap[rid] * cap_limit:
            return adj / cap[rid]
        return 100.0 + adj / cap[rid]

    def _band(sc: float) -> int:
        """_score 의 구간만 뽑는다(0 하한 미달 · 1 정상 · 2 초과) · 담당 우선은 구간 안에서만 작동."""
        return 0 if sc < -1.0 else (1 if sc < 100.0 else 2)

    groups, per, per_owned = {}, {}, {}
    owner_held, owner_used = [], {}                  # 담당자 전원 불가로 보류된 건 · 규칙별 배정 수
    for h in hs:
        cs = cats.get(h) or []
        hit = owners_for(h, oidx, svcs, cats) if rules else None
        picked = []
        if hit:
            rule, owners = hit
            rank = {rid: i for i, rid in enumerate(owners)}
            cand = [r for r in owners if r in idx]   # 부재·최종검수자·선택 제외자는 빠진다
            if not cand:
                owner_held.append({"hash": h, "rule": owner_label(rule),
                                   "owners": [(_name_of(data["members"], r)) for r in owners]})
                continue                             # 전담 · 남에게 보내지 않는다
            # 주 담당이 초과 구간에 들어가기 전까지는 부 담당에게 가지 않는다
            cand.sort(key=lambda r: (_band(_score(r, cs)) >= 2, rank[r], _score(r, cs)))
            picked = cand[:n_per]
            owner_used[owner_label(rule)] = owner_used.get(owner_label(rule), 0) + 1
        if len(picked) < n_per:
            # 담당 없는 콘텐츠(또는 담당자 수 < 건당 인원의 나머지 자리)는 담당 영역이 없는 사람 먼저.
            # 단 구간(하한→정상→초과)은 넘지 않는다 · 전담자가 놀고 있어도 초과자보다 먼저 받는다.
            rest = [r for r in ids if r not in picked]
            rest.sort(key=lambda r: (_band(_score(r, cs)), r in owned_ids, _score(r, cs), idx[r]))
            picked = picked + rest[:n_per - len(picked)]
        groups.setdefault(tuple(picked), []).append(h)
        for rid in picked:
            used[rid] += 1
            per[rid] = per.get(rid, 0) + 1
            if hit and rid in rank:
                per_owned[rid] = per_owned.get(rid, 0) + 1
    plan = {rid: {"n": n, "name": next(m["name"] for m in pool if m["id"] == rid),
                  "capacity": cap[rid], "pending_after": used[rid],
                  "floor": floors.get(rid, 0), "owned": per_owned.get(rid, 0),
                  "over": used[rid] > cap[rid]} for rid, n in per.items()}
    # 이번 배정에서 한 건도 못 받은 사람 = 잔여가 이미 캐파를 넘겨 뒤로 밀린 사람
    held = [{"name": m["name"], "pending": used[m["id"]], "capacity": cap[m["id"]]}
            for m in pool if m["id"] not in per and used[m["id"]] >= cap[m["id"]] * cap_limit]
    n_plan = sum(len(c) for c in groups.values())
    out = {"ok": True, "plan": plan, "n": n_plan, "min_reviewers": n_per,
           "blocked": blocked, "applied": False, "held": held,
           "fill_ratio": fill_ratio,
           # 담당 규칙: 규칙별 배정 수 · 담당자 전원 불가로 이번엔 배정하지 않은 건(미배정 유지)
           "owner_used": owner_used, "owner_held": owner_held,
           "owner_held_n": len(owner_held),
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

    정체 = 배정 후 stale_days 를 넘겼는데 아직 판정하지 않은 슬롯(미완료 보유 전제) ·
    또는 부재·비활성 인원의 슬롯(슬롯 나이 무관 전량 회수).
    한 콘텐츠에 같은 사람이 둘 들어가지 않도록 이관 대상에서 기존 담당은 제외한다.

    판정은 **슬롯 단위**다 — 사람 단위로만 보면 10일 묵은 1건 때문에 몇 초 전 배정된
    나머지까지 통째로 남에게 넘어간다(검수자가 열어 두고 보던 목록이 사라지고,
    set_assignees 가 DELETE+INSERT 라 실제 정체분의 정체일마저 0 으로 리셋된다).
    부재·비활성 인원 몫은 슬롯 나이와 무관하게 전량 회수한다 — 그 사람은 이번 주에
    아무것도 못 보므로 새 배정이든 옛 배정이든 남겨 두면 그 콘텐츠는 멈춘다."""
    st = _SV.get_store()
    if not st:
        return {"ok": False, "error": "store unavailable", "moves": [], "n": 0}
    cfg = settings(team)
    now = time.time()
    raw = {}
    data = _crew_compute(team, raw_out=raw)         # 원천 테이블 공유(직후 전량 재조회 방지)
    by_id = {m["id"]: m for m in data["members"]}
    stale_ids = {m["id"] for m in data["members"]
                 if m["load"]["pending"] and (m["load"]["stale_days"] >= float(cfg["stale_days"])
                                              or not m["available"])}
    if not stale_ids:
        return {"ok": True, "moves": [], "n": 0, "reason": "현재 재배정할 정체 배정이 없습니다"}
    takers = _assignable([m for m in data["members"]
                          if m["available"] and m["id"] not in stale_ids and m["spare"] > 0])
    if not takers:
        return {"ok": False, "error": "받을 여력이 있는 검수자가 없습니다(최종검수자 제외)", "moves": [], "n": 0}
    if all(k in raw for k in ("asg", "fmap", "targets")):   # _crew_compute 조회분 재사용
        asg, fmap, targets = raw["asg"], raw["fmap"], raw["targets"]
        asg_ts = raw.get("asg_ts") or {}
    else:                                           # 일부 조회 실패 시에만 직접 재시도(기존 오류 계약 유지)
        try:
            asg = st.assignees(team=team) or {}
            fmap = st.feedback_map(team=team) or {}
            targets = st.review_targets(team) if hasattr(st, "review_targets") else set()
        except Exception:
            return {"ok": False, "error": "누가 무엇을 맡았는지 불러오지 못했습니다", "moves": [], "n": 0}
        asg_ts = {}                                 # 배정 시각 불명 → 슬롯 나이 검사 없이 현행 동작
    done_pairs = {(ch, (v.get("reviewer_id") or v.get("reviewer") or ""))
                  for ch, e in fmap.items() for v in (e.get("verdicts") or [])
                  if v.get("verdict") in ("good", "bad")}
    used = {m["id"]: m["load"]["pending"] for m in takers}
    cap = {m["id"]: max(1, m["weekly_capacity"] or 1) for m in takers}
    heap = [(used[m["id"]] / cap[m["id"]], i, m["id"]) for i, m in enumerate(takers)]
    heapq.heapify(heap)
    # 담당 규칙이 있으면 전담 콘텐츠는 같은 담당자 그룹 안에서만 옮긴다(받을 담당자가 없으면 남긴다)
    rules = owner_rules(team)
    oidx = _owner_index(rules)
    svcs = content_services(team) if any(r.get("kind") == "service" for r in rules) else {}
    cats = content_categories(team) if any(r.get("kind") == "category" for r in rules) else {}
    taker_ids = {m["id"] for m in takers}
    moves, changed, owner_kept = [], {}, 0
    for ch, a in sorted(asg.items()):
        if targets and ch not in targets:
            continue
        cur = list(a.get("reviewers") or [])
        for rv in list(cur):
            if rv not in stale_ids or (ch, rv) in done_pairs or len(moves) >= max(1, int(limit)):
                continue
            # 슬롯 단위 정체 검사: 아직 자리에 있는 사람(available)이라면 '이 슬롯이 실제로
            # 묵었는지'를 본다. 배정 시각을 못 주는 스토어(asg_ts 빈 dict)는 종전대로 사람 단위.
            # 시각이 없는 개별 슬롯은 _crew_compute 의 정체일 계산과 같게 나이 0 으로 본다.
            if asg_ts and (by_id.get(rv) or {}).get("available"):
                ts = asg_ts.get((ch, rv), 0)
                age = (now - ts) / 86400 if ts else 0.0
                if age < float(cfg["stale_days"]):
                    continue
            hit = owners_for(ch, oidx, svcs, cats) if rules else None
            if hit:                                 # 전담 콘텐츠 · 담당자 중 가장 여유 있는 사람에게만
                own = [o for o in hit[1] if o in taker_ids and o not in cur]
                if not own:
                    owner_kept += 1
                    continue
                to = min(own, key=lambda o: (used[o] / cap[o], hit[1].index(o)))
                cur[cur.index(rv)] = to
                used[to] += 1
                moves.append({"hash": ch, "from": rv, "from_name": by_id[rv]["name"],
                              "to": to, "to_name": by_id[to]["name"], "owner": True})
                changed[ch] = (cur, max(1, int(a.get("min") or 1)))
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
           "from_counts": _count(moves, "from_name"), "to_counts": _count(moves, "to_name"),
           "owner_kept": owner_kept}               # 담당자 그룹에 받을 사람이 없어 남겨 둔 전담 슬롯
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


# ── 자동 운영(주 사이클) ─────────────────────────────────────────────────────
# 사람이 매주 잊지 않고 눌러야 돌아가는 운영은 결국 안 돌아간다(그래서 900슬롯이 11일 묵었다).
# 다만 남의 일을 옮기는 동작이라 기본은 꺼둔다 — 켠 팀에서만 사이클마다 1회씩 자동 실행된다.
# 실행 경로: 검수운영 화면 진입 시 POST /crew-auto · 크론은 `python3 -m prism.crewbot --team <id>`.
AUTO_KIND = "crew_auto"


def auto_state(team=None) -> dict:
    """자동 운영 상태 {wave_cycle, rebalance_cycle, last_run} · 사이클당 1회 보장용 회차 키."""
    return dict((_SV._report_get(AUTO_KIND, team, {}) or {}).get("item") or {})


def _claim_cycle(name: str, cycle: str, team=None, attempt: int = 0):
    """이번 회차를 **선점**한다(원자적 · 처음 잡은 호출만 True). 선점 장치가 없으면 None.

    종전에는 `state.get("wave_cycle") != cycle` 로 확인하고 배분이 끝난 **뒤에야** 회차 키를
    저장했다(check-then-act). 그 사이에 다른 실행이 끼면 같은 사이클에 웨이브가 두 번 나간다 —
    검수운영 화면의 '지금 실행'(POST /crew-auto)과 크론(`python3 -m prism.crewbot` · 01:00 UTC
    = 10:00 KST 로 기본 사이클 시작 시각과 같다)은 프로세스가 달라 파이썬 락으로도 못 막는다.
    set_wave 가 같은 기한이면 계획을 합산하므로 결과는 '계획이 실제 배정의 2배'였다.
    log_event_once 는 미션 보상 이중 지급을 막으려고 이미 둔 원자적 멱등 장치라 그대로 쓴다.

    attempt: 앞선 시도가 실패했으면(예: 그 순간 배정 가능한 사람이 0명) 다음 점검이 다시
    시도할 수 있어야 한다 — 회차 키에 시도 번호를 붙여 '1회 보장'과 '재시도 여지'를 같이 둔다."""
    st = _SV.get_store()
    if not (st and hasattr(st, "log_event_once")):
        return None                                 # 선점 불가 → 호출측이 종전 동작으로 퇴화
    key = f"crew_{name}:{cycle}" + (f"#{int(attempt)}" if attempt else "")
    try:
        return bool(st.log_event_once(None, key, 0, 0, meta="자동 운영 회차 선점", team=team))
    except Exception:
        return None


def _last_open_ts(now: float, cfg: dict) -> float:
    """지금 기준으로 가장 최근에 지난 '사이클 시작 시각'(epoch).
    팀 타임존으로 요일·시각을 해석한다(day_key 와 같은 기준 · 기본 KST)."""
    from .store import _tz_sec
    tz = _tz_sec()
    local = now + tz
    day = int(local // 86400)
    wd = (day + 3) % 7                              # epoch day 0 = 목요일 → 월=0 기준으로 환산
    back = (wd - int(cfg["wave_weekday"])) % 7
    open_local = (day - back) * 86400 + int(cfg["wave_hour"]) * 3600
    if open_local > local:                          # 오늘이 그 요일인데 아직 시작 시각 전 → 지난 사이클
        open_local -= 7 * 86400
    return open_local - tz


def _unassigned_targets(team=None, limit: int = 300) -> list:
    """아직 아무도 안 맡은 검수 대상. 자동 배분의 재료."""
    st = _SV.get_store()
    if not st:
        return []
    try:
        targets = st.review_targets(team) if hasattr(st, "review_targets") else set()
        asg = set(st.assignees(team=team) or {})
    except Exception:
        return []
    return sorted(targets - asg)[:max(1, int(limit))]


def auto_tick(team=None, now=None, apply: bool = True) -> dict:
    """자동 운영 1회 점검. 새 사이클이면 여력만큼 나눠 맡기고, 기한이 하루 안이면 멈춘 일을 넘긴다.

    같은 사이클에 두 번 돌지 않도록 회차 키(사이클 시작일)를 남긴다 — 화면 진입마다 호출해도
    안전하다. apply=False 면 무엇을 할지만 돌려주고 아무것도 바꾸지 않는다."""
    cfg = settings(team)
    now = time.time() if now is None else float(now)
    state = auto_state(team)
    open_ts = _last_open_ts(now, cfg)
    cycle = day_key(open_ts)
    out = {"ok": True, "applied": bool(apply), "cycle": cycle,
           "auto_wave": bool(int(cfg["auto_wave"])), "auto_rebalance": bool(int(cfg["auto_rebalance"])),
           "wave": None, "rebalance": None, "escalate": None}
    changed = dict(state)

    def _claimed(name: str, field: str, attempt: int) -> bool:
        """회차 선점 + '이 시도를 썼다'를 즉시 남긴다. 선점은 apply 일 때만 한다 —
        dry-run 이 회차 키를 소비하면 미리보기 한 번에 그 사이클의 자동 운영이 통째로
        사라진다(계획만 보고 아무것도 바꾸지 않는다는 계약 위반).
        시도 번호를 **실행 전에** 저장하는 이유: 배분 도중 예외로 죽어도(원격 쓰기 실패 등)
        다음 점검이 새 키로 다시 시도할 수 있어야 한다 — 안 그러면 그 주 자동 운영이 사라진다."""
        if not apply:
            return True
        if _claim_cycle(name, cycle, team, attempt) is False:
            return False                            # 다른 실행이 이미 이번 회차를 가져갔다
        changed[field] = attempt + 1
        _SV._report_save(AUTO_KIND, {"item": dict(changed, last_run=now)}, team)
        return True

    wave_try = int(state.get("wave_retry") or 0)
    if (int(cfg["auto_wave"]) and state.get("wave_cycle") != cycle
            and _claimed("wave", "wave_retry", wave_try)):
        hs = _unassigned_targets(team, int(cfg["wave_batch"]))
        due = open_ts + float(cfg["wave_days"]) * 86400
        if hs:
            r = plan_distribute(hs, min_reviewers=int(cfg["wave_min_reviewers"]), team=team,
                                apply=apply, by="자동 운영", due_at=(due if apply else None))
            out["wave"] = {"ok": r.get("ok"), "n": r.get("n", 0), "plan": r.get("plan", {}),
                           "due_at": due, "error": r.get("error", "")}
            if apply and r.get("ok"):
                changed["wave_cycle"] = cycle
                changed.pop("wave_retry", None)
            # 실패면 wave_retry(=이번에 쓴 시도 번호 + 1)가 그대로 남아 다음 점검이 새 키로 재시도한다
        else:
            out["wave"] = {"ok": True, "n": 0, "plan": {}, "due_at": due,
                           "error": "아직 아무도 안 맡은 콘텐츠가 없습니다"}
            if apply:
                changed["wave_cycle"] = cycle       # 내보낼 게 없어도 이번 사이클은 처리한 것으로 본다
                changed.pop("wave_retry", None)

    reb_try = int(state.get("rebalance_retry") or 0)
    if int(cfg["auto_rebalance"]) and state.get("rebalance_cycle") != cycle:
        due = float(wave(team).get("due_at") or 0)
        if (due and now >= due - 86400                  # 기한 하루 전부터 · 지나서도 한 번은 잡는다
                and _claimed("rebalance", "rebalance_retry", reb_try)):
            r = rebalance(team=team, apply=apply, by="자동 운영")
            out["rebalance"] = {"ok": r.get("ok"), "n": r.get("n", 0),
                                "to": r.get("to_counts", {}), "from": r.get("from_counts", {}),
                                "error": r.get("error", "") or r.get("reason", "")}
            if apply and r.get("ok"):
                changed["rebalance_cycle"] = cycle
                changed.pop("rebalance_retry", None)

    # 갈린 건은 사이클과 무관하게 계속 생기므로 회차 키로 묶지 않고 매번 점검한다.
    if int(cfg["auto_escalate"]):
        r = escalate_split(team=team, apply=apply, by="자동 운영")
        if r.get("n"):
            out["escalate"] = {"ok": r.get("ok"), "n": r["n"], "to": r.get("to_counts", {})}

    if apply and changed != state:
        changed["last_run"] = now
        _SV._report_save(AUTO_KIND, {"item": changed}, team)
    return out


# ── 적응형 겹치기: 2명 먼저 · 갈리면 한 명 더 ────────────────────────────────
# 전건 3인 검수는 비싸다. 실측 불일치율이 선착 2인 기준 25% 였으니, 2인으로 시작하고
# 갈린 건에만 3번째를 붙이면 같은 신뢰도로 판정 수를 25% 안팎 줄일 수 있다.
# (2,000건 정답셋 기준 6,000판정 → 4,500판정)
def split_pending(team=None, asg=None, fmap=None, golden=None) -> list:
    """3번째 눈이 필요한 콘텐츠: 배정된 담당이 전원 판정했는데 의견이 갈렸고,
    아직 아무도 더 붙지 않은 것. 이미 골든으로 확정됐거나 리드가 최종판정한 건 제외.
    asg·fmap·golden 을 주면(호출측이 이미 조회) 같은 테이블 전량 재조회를 생략한다."""
    st = _SV.get_store()
    if not st:
        return []
    try:
        if asg is None:
            asg = st.assignees(team=team) or {}
        if fmap is None:
            fmap = st.feedback_map(team=team) or {}
        if golden is None:
            golden = st.golden_hashes(team) or set()
    except Exception:
        return []
    try:
        finals = set(_SV.final_verdicts(team) or {})
    except Exception:
        finals = set()
    out = []
    for ch, a in asg.items():
        rvs = list(a.get("reviewers") or [])
        if len(rvs) != 2 or ch in golden or ch in finals:
            continue                                  # 2인 배정 건만 · 이미 결론 난 건 제외
        e = fmap.get(ch) or {}
        by = {(v.get("reviewer_id") or v.get("reviewer") or ""): v.get("verdict")
              for v in (e.get("verdicts") or []) if v.get("verdict") in ("good", "bad")}
        if not all(r in by for r in rvs):
            continue                                  # 아직 둘 다 보지 않았다
        if len({by[r] for r in rvs}) < 2:
            continue                                  # 합의됨 → 3번째가 필요 없다
        out.append({"hash": ch, "reviewers": rvs, "verdicts": {r: by[r] for r in rvs}})
    return out


def escalate_split(team=None, apply: bool = False, by: str = "", limit: int = 200) -> dict:
    """의견이 갈린 건에만 3번째 검수자를 붙인다(전건 3인 배정의 대체).
    고르는 기준은 여력(부하/캐파) · 이미 그 건을 본 두 사람은 당연히 제외한다.
    _crew_compute 를 먼저 돌려 그 원천 테이블(asg·fmap·golden)을 split_pending 과
    공유한다 — 같은 호출 안에서 feedback·assignments 전량이 두 번 내려오지 않게."""
    st = _SV.get_store()
    if not st:
        return {"ok": True, "n": 0, "moves": [], "applied": False,
                "reason": "추가 배정이 필요한 불일치 건이 없습니다"}
    raw = {}
    data = _crew_compute(team, raw_out=raw)
    items = split_pending(team, asg=raw.get("asg"), fmap=raw.get("fmap"),
                          golden=raw.get("golden"))[:max(1, int(limit))]
    if not items:
        return {"ok": True, "n": 0, "moves": [], "applied": False,
                "reason": "추가 배정이 필요한 불일치 건이 없습니다"}
    pool = _assignable([m for m in data["members"] if m["available"]])
    if not pool:
        return {"ok": False, "error": "배정할 수 있는 검수자가 없습니다(최종검수자 제외)", "n": 0, "moves": []}
    cap = {m["id"]: max(1, m["weekly_capacity"] or 1) for m in pool}
    used = {m["id"]: m["load"]["pending"] for m in pool}
    name = {m["id"]: m["name"] for m in pool}
    heap = [(used[m["id"]] / cap[m["id"]], i, m["id"]) for i, m in enumerate(pool)]
    heapq.heapify(heap)
    moves, changed = [], {}
    for it in items:
        cand, pick = [], None
        while heap:
            key = heapq.heappop(heap)
            if key[2] in it["reviewers"]:
                cand.append(key)                      # 이미 본 사람은 3번째가 될 수 없다
                continue
            pick = key
            break
        for c in cand:
            heapq.heappush(heap, c)
        if not pick:
            continue
        _, i, to = pick
        used[to] += 1
        heapq.heappush(heap, (used[to] / cap[to], i, to))
        moves.append({"hash": it["hash"], "to": to, "to_name": name[to],
                      "between": [ next((m["name"] for m in data["members"] if m["id"] == r), r)
                                   for r in it["reviewers"] ]})
        changed[it["hash"]] = it["reviewers"] + [to]
    out = {"ok": True, "n": len(moves), "moves": moves, "applied": False,
           "to_counts": _count(moves, "to_name")}
    if not (apply and moves):
        return out
    for ch, rvs in changed.items():
        st.set_assignees(ch, rvs, min_reviewers=3, team=team)   # 통과 기준도 3인으로
    _SV._log_assign(by or "(미상)", "갈린 건 한 명 더", len(moves),
                    sorted({m["to_name"] for m in moves}), 3, team)
    _SV._agg_bump()
    out["applied"] = True
    return out


# ── 강점 매칭 · 부족 분류 우선 ───────────────────────────────────────────────
# ── 담당 규칙: 서비스·주제별 전담 검수자 ──────────────────────────────────────
# 규칙 한 줄 = 기준(service|category) + 값 + 담당자 목록(순서 = 주 → 부).
# · 전담: 그 콘텐츠는 담당자에게만 간다. 담당자 전원이 부재·초과·제외면 배정하지 않고
#   '보류'로 드러낸다(남에게 새지 않는다 · 사용자 결정 2026-09-02).
# · 주/부: 목록 첫 사람이 주 담당. 부 담당은 주 담당 잔여가 상한을 넘겼을 때만 받는다.
# · 담당이 정해지지 않은 콘텐츠는 '담당 영역이 없는 사람'이 먼저 받는다(전담자의 여력을
#   그 영역에 남겨 두기 위해).
# · 한 콘텐츠가 서비스 규칙과 주제 규칙에 함께 걸리면 서비스 규칙이 이긴다(더 좁은 기준).
def owner_rules(team=None) -> list:
    """담당 규칙 목록(저장 순서 유지 · 순서가 주제 규칙 간 우선순위)."""
    items = (_SV._report_get(OWNER_KIND, team, {}) or {}).get("items") or []
    return [dict(r) for r in items if isinstance(r, dict)]


def set_owner_rules(rules, team=None, by: str = "") -> dict:
    """담당 규칙 전체 교체(화면이 표를 통째로 저장한다). 검증에 하나라도 걸리면 저장하지 않는다."""
    out, seen = [], set()
    for i, r in enumerate(list(rules or [])[:OWNER_MAX_RULES]):
        if not isinstance(r, dict):
            continue
        kind = (r.get("kind") or "").strip()
        value = str(r.get("value") or "").strip()[:120]
        rvs = []
        for x in (r.get("reviewers") or []):
            x = str(x or "").strip()
            if x and x not in rvs:
                rvs.append(x)
        if kind not in OWNER_KINDS:
            return {"ok": False, "error": "%d번째 규칙: 기준은 서비스 또는 주제여야 합니다" % (i + 1)}
        if not value:
            return {"ok": False, "error": "%d번째 규칙: 값을 고르세요" % (i + 1)}
        if not rvs:
            return {"ok": False, "error": "%d번째 규칙(%s): 담당자를 한 명 이상 고르세요" % (i + 1, value)}
        if (kind, value) in seen:
            return {"ok": False, "error": "'%s' 규칙이 두 번 있습니다 · 하나로 합치세요" % value}
        seen.add((kind, value))
        out.append({"kind": kind, "value": value, "reviewers": rvs})
    _SV._report_save(OWNER_KIND, {"items": out, "updated_at": time.time(),
                                  "updated_by": (by or "")[:80]}, team)
    _SV._agg_bump()
    return {"ok": True, "rules": out, "n": len(out)}


def owner_label(rule: dict) -> str:
    return ("서비스 " if rule.get("kind") == "service" else "주제 ") + str(rule.get("value") or "")


def _owner_index(rules) -> tuple:
    """(서비스→담당 목록, 주제→담당 목록). 주제는 규칙 순서를 지킨다(먼저 적은 규칙이 이긴다)."""
    svc, cat = {}, {}
    for r in rules or []:
        tgt = svc if r.get("kind") == "service" else cat
        tgt.setdefault(str(r.get("value") or ""), (r, list(r.get("reviewers") or [])))
    return svc, cat


def owners_for(h: str, idx: tuple, services: dict, cats: dict):
    """콘텐츠 하나의 (규칙, 담당자 목록) · 없으면 None. 서비스 규칙 → 주제 규칙 순."""
    svc, cat = idx
    hit = svc.get(services.get(h) or "")
    if hit:
        return hit
    for c in cats.get(h) or []:
        hit = cat.get(c)
        if hit:
            return hit
    return None


def content_services(team=None) -> dict:
    """{hash: 서비스명(displayServiceName)} · 담당 규칙의 재료(content_categories 관례)."""
    def _calc():
        out = {}
        for r in _SV.results_rows(team=team) or []:
            ref = r.get("content_ref") or {}
            ch = _row_key(ref)
            if ch:
                out[ch] = str(ref.get("displayServiceName") or r.get("service") or "").strip()
        return out
    return _SV._agg_cached(("crewsvcs", team), _calc, ttl=60.0)


def owner_options(team=None) -> dict:
    """화면 드롭다운용 · 지금 데이터에 있는 서비스·Tier1 주제 목록(규칙에 이미 쓴 값도 포함)."""
    svcs = {v for v in content_services(team).values() if v}
    cats = {c for cs in content_categories(team).values() for c in cs}
    for r in owner_rules(team):
        (svcs if r.get("kind") == "service" else cats).add(str(r.get("value") or ""))
    return {"service": sorted(v for v in svcs if v), "category": sorted(c for c in cats if c)}


def content_categories(team=None) -> dict:
    """{hash: [Tier1 분류]} · 강점 매칭과 부족 분류 우선의 재료.
    콘텐츠마다 get_item_meta 를 부르면 왕복이 폭발하므로 결과 뷰에서 한 번에 만든다."""
    def _calc():
        out = {}
        for r in _SV.results_rows(team=team) or []:
            ch = _row_key(r.get("content_ref") or {})
            cats = [str(c).split("/")[0].strip()
                    for c in ((r.get("item_meta") or {}).get("content_category") or []) if c]
            if ch:
                out[ch] = [c for c in cats if c and c != "Unclassified"]
        return out
    return _SV._agg_cached(("crewcats", team), _calc, ttl=60.0)


def _row_key(ref: dict) -> str:
    return _SV._row_key(ref)


def category_reliability(team=None, min_n: int = 5, fmap=None) -> dict:
    """{uid: {분류: 합치율}} · '이 사람이 이 분야에서 팀 결론과 얼마나 같게 보는가'.
    골드 문항은 분야별로 표본이 안 나오므로 다수 의견과의 합치로 근사한다
    (표본 min_n 미만인 조합은 담지 않는다 · 적은 표본으로 강점을 단정하지 않기 위해).
    fmap 을 주면(호출측이 이미 조회) feedback 전량 재조회를 생략한다(capacity(fmap=) 관례).
    결과는 _agg_cached 로 감싼다(content_categories 관례) — 배정 미리보기→실행이 연달아
    O(fmap×verdicts) 합치율 계산을 반복하지 않게. 피드백 쓰기 경로는 전부 _agg_bump 호출."""
    def _calc():
        st = _SV.get_store()
        if not st:
            return {}
        fm = fmap
        if fm is None:
            try:
                fm = st.feedback_map(team=team) or {}
            except Exception:
                return {}
        cats = content_categories(team)
        acc = {}                                      # uid → 분류 → [일치, 전체]
        for ch, e in fm.items():
            vs = [(v.get("reviewer_id") or v.get("reviewer") or "", v.get("verdict"))
                  for v in (e.get("verdicts") or []) if v.get("verdict") in ("good", "bad")]
            if len(vs) < 2:
                continue                              # 혼자 본 건은 합치를 잴 수 없다
            g = sum(1 for _, v in vs if v == "good")
            major = "good" if g * 2 > len(vs) else ("bad" if (len(vs) - g) * 2 > len(vs) else "")
            if not major:
                continue                              # 동점이면 정답이 없다
            for c in (cats.get(ch) or []):
                for rid, v in vs:
                    d = acc.setdefault(rid, {}).setdefault(c, [0, 0])
                    d[1] += 1
                    if v == major:
                        d[0] += 1
        out = {}
        for rid, per in acc.items():
            row = {c: round(a / n, 4) for c, (a, n) in per.items() if n >= max(1, int(min_n))}
            if row:
                out[rid] = row
        return out
    return _SV._agg_cached(("crewrel", team, int(min_n)), _calc)


def prioritize(hashes, team=None) -> list:
    """정답셋이 부족한 분류를 앞으로. 물량에 상한이 걸릴 때 더 값진 것이 먼저 나가게 한다.
    같은 그룹 안에서는 원래 순서를 지킨다(안정 정렬)."""
    try:
        lack = _SV._lack_classes(team) or set()
    except Exception:
        lack = set()
    if not lack:
        return list(hashes or [])
    cats = content_categories(team)
    return sorted(hashes or [], key=lambda h: 0 if (set(cats.get(h) or []) & lack) else 1)
