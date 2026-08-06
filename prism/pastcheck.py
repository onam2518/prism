"""PAST 로그 검증 도구(실험실 · 사용자 › 로그뷰어) 백엔드.

기획 "13. 로그 검증 도구"(daumcorp pplan/444498040) 기반. 뷰어가 아니라 검증 도구:
로그가 "보이는지"가 아니라 "맞게 심어졌는지"를 기대 목록과 대조해 기계 판정한다.
- 판정 정본 = 12. 수집 데이터 정의(필수 11 · 유형 4 · 분류 16) + 11. 정책 및 운영(무효값 · 명명)
- 판정 3단계: ① 로그 단위(형식) ② 행동 단위(1회 조작 1건 · 중복) ③ 시나리오 단위(체크리스트)
- 검증 세션 = 시연 세션 그대로: 시연이 곧 기기 식별자(uuid) 기준 1인 분리 환경이라
  별도 세션 시작 없이 시연 탭의 행동이 실시간 판정 대상이 된다
- 위반 예시 주입: 실측 관찰(pplan/433947067)의 대표 위반을 합성해 판정 동작을 보여준다
  (시연 세션 이벤트와 별개 저장 · 측정·결론 계산에는 영향 없음)

저장: usermeta_logviewer = {"bad": bool} (주입 여부만 · 판정은 매 요청 재계산)
컴포지션: 시연 세션·스토어는 memfs(_SV 주입 포함)를 재사용한다.
"""
from __future__ import annotations

import datetime as _dt
import re

from . import memfs as MF

KIND = "usermeta_logviewer"
LOGS_MAX = 40                     # 스트림 표시 상한(최신순)
SERVICE_ID = "prism_lab"
# 기기·세션 식별자는 TIARA 승계 포맷 예시(식별자·SDK 문서의 실측 리터럴 형태)
DEVICE_UUID = "8I1wbjLiF32B_190605124927342"
SESSION_SUID = "w-Fz1UJbOTjgN3_260805931784866"
PAGE_FEED, PAGE_ARTICLE = "my_contents", "article_detail"

REQUIRED = ("log_unique_id", "access_timestamp", "service_id", "deployment", "sdk_type",
            "uuid", "suid", "islogin", "action_type", "action_name", "page")
TYPES4 = ("Pageview", "Event", "ViewImp", "Usage")
KINDS16 = ("ViewContent", "ClickContent", "Like", "Dislike", "DoubleLike", "AddToWishList",
           "Search", "ViewSearchResults", "AddToCart", "BeginCheckout", "Purchase",
           "PurchaseCancel", "AppLaunch", "AppExit", "Login", "UsagePage")
INVALID = ("", "null", "-", "unknown", "undefined")
FEEDBACK_KINDS = ("Like", "Dislike", "DoubleLike", "AddToWishList")   # 유실 금지 등급
_NAME_RE = re.compile(r"^[0-9A-Za-z가-힣]+_[0-9A-Za-z가-힣_]+$")       # 화면_액션 형식
CPROP_KEYS = ("emotion",)         # 데모의 사전 등록 커스텀 키 · tesla_* 접두는 등록 예외

# 수집 등급 표기(로그 상세 보기) · 12. 수집 데이터 정의 기준
GRADE = dict({k: "필수" for k in REQUIRED},
             action_kind="선택", section="권장", clog_seq="선택",
             **{"content.id": "조건부", "content.type": "조건부",
                "viewimp_contents[].id": "조건부", "viewimp_contents[].type": "조건부",
                "viewimp_contents[].imp_ordnum": "권장",
                "usage.duration": "조건부", "usage.scroll_percent": "선택",
                "search.search_term": "조건부", "search.search_type": "선택",
                "click.layer1": "권장", "click.ordnum": "선택",
                "custom_props.emotion": "선택(등록 키)"})

# 시나리오 체크리스트(③): 시연 화면의 기대 로그 = 이벤트 계약 6종
CONTRACT = (
    ("impression", "피드 노출", "ViewImp"),
    ("search", "콘텐츠 찾기(검색)", "Event · Search"),
    ("click", "카드 탭(클릭)", "Event · ClickContent"),
    ("usage", "읽기 종료(사용성)", "Usage · UsagePage"),
    ("react", "감정 반응(피드백)", "Event · Like / Dislike"),
    ("comment", "댓글 등록", "Event · 행동 이름 규칙"),
)


def _luid(i: int) -> str:
    """UUID v7 모양의 합성 로그 고유번호(시연용 · 순번 기반 결정적)."""
    return "0198f2a1-%04x-7abc-8def-%012x" % (i & 0xffff, i)


def _ts_ms(tstr: str) -> int:
    try:
        hh, mm, ss = [int(x) for x in (tstr or "").split(":")]
        now = _dt.datetime.now()
        return int(_dt.datetime(now.year, now.month, now.day, hh, mm, ss).timestamp() * 1000)
    except (TypeError, ValueError):
        return int(_dt.datetime.now().timestamp() * 1000)


def _base(i, tstr, page, name, atype, kind=None):
    env = {"log_unique_id": _luid(i), "access_timestamp": _ts_ms(tstr),
           "service_id": SERVICE_ID, "deployment": "sandbox", "sdk_type": "WEB",
           "uuid": DEVICE_UUID, "suid": SESSION_SUID, "islogin": False,
           "action_type": atype}
    if kind is not None:
        env["action_kind"] = kind
    env.update({"action_name": name, "page": page, "section": "mycontents", "clog_seq": i + 1})
    return env


def _envelope(e: dict, i: int) -> dict:
    """시연 세션 이벤트 1건 → PAST 봉투(평탄 표기 · 그룹은 점 표기)."""
    ev, idx, t = e.get("event"), e.get("idx"), e.get("t", "")
    if ev == "impression":
        env = _base(i, t, PAGE_FEED, "마이콘텐츠_피드_노출", "ViewImp")
        env.update({"viewimp_contents[].id": idx, "viewimp_contents[].type": "content",
                    "viewimp_contents[].imp_ordnum": idx})
    elif ev == "search":
        env = _base(i, t, PAGE_FEED, "마이콘텐츠_콘텐츠찾기_완료", "Event", "Search")
        env.update({"search.search_term": (e.get("title") or "")[:60], "search.search_type": "직접입력"})
    elif ev == "click":
        env = _base(i, t, PAGE_FEED, "마이콘텐츠_카드_클릭", "Event", "ClickContent")
        env.update({"content.id": idx, "content.type": "content",
                    "click.layer1": "main_feed", "click.ordnum": idx})
    elif ev in ("read", "skim"):
        env = _base(i, t, PAGE_ARTICLE, "기사상세_나가기_완료", "Usage", "UsagePage")
        env.update({"content.id": idx, "usage.duration": int(e.get("dwell") or 0) * 1000,
                    "usage.scroll_percent": int(e.get("scroll") or 0)})
    elif ev == "react":
        kind = "Like" if (e.get("emo") or "") in MF.EMO_POS else "Dislike"
        env = _base(i, t, PAGE_ARTICLE, "기사상세_감정반응_클릭", "Event", kind)
        env.update({"content.id": idx, "content.type": "content",
                    "custom_props.emotion": e.get("emo") or ""})
    elif ev == "comment":
        env = _base(i, t, PAGE_ARTICLE, "기사상세_댓글등록_완료", "Event")
        env.update({"content.id": idx, "content.type": "content"})
    else:
        env = _base(i, t, PAGE_FEED, str(ev or ""), "Event")
    return env


def _bad_examples(t: str) -> list:
    """실측 관찰의 대표 위반 합성(주입 예시): 무효값 · 무맥락 이름 · 광고 혼입 ·
    중복 전송 · 필수 누락 · 권장 미수집. 순번은 세션과 겹치지 않게 9000대."""
    ex = []
    e1 = _base(9001, t, "home_tab", "홈탭_진입", "Event", "")          # 빈 문자열 분류(무효값)
    e2 = _base(9002, t, "home_tab", "item", "Event", "ClickContent")   # 무맥락 이름 + 권장 미수집
    e2.update({"content.id": 0, "content.type": "content"})
    e3 = _base(9003, t, "home_tab", "axzad_imp", "Event", "ViewContent")   # 광고 계측 혼입(분류 오용)
    e4 = _base(9004, t, "home_tab", "앱_실행_완료", "Event", "AppLaunch")
    e5 = dict(e4)                                                      # 동일 log_unique_id 중복 전송
    e6 = _base(9006, t, "", "기사상세_나가기_완료", "Usage", "UsagePage")
    e6.pop("page")                                                     # 필수 필드(page) 누락
    e6.update({"usage.duration": 12000, "usage.scroll_percent": 40})
    e7 = _base(9007, t, "home_tab", "홈탭_기사_클릭", "Event", "ClickContent")
    e7.update({"content.id": 1, "content.type": "content"})            # 형식은 정상 · 권장(layer1) 미수집
    for env, label in ((e1, "빈 분류값"), (e2, "무맥락 이름"), (e3, "광고 혼입"),
                       (e4, "앱 실행"), (e5, "중복 전송"), (e6, "필수 누락"), (e7, "권장 미수집")):
        ex.append({"env": env, "label": "주입 예시 · " + label})
    return ex


def _judge(env: dict, seen_luids: set) -> list:
    """판정 ① 로그 단위(형식) + 중복(②의 로그 표면) · [level, msg] 목록."""
    v = []
    for k in REQUIRED:
        val = env.get(k, None)
        if val is None:
            v.append(["fail", "필수 필드 누락: " + k])
        elif isinstance(val, str) and val.strip().lower() in INVALID:
            v.append(["fail", "무효값 전송: " + k + " (무효값은 필드 자체를 생략)"])
    at = env.get("action_type")
    if at is not None and at not in TYPES4:
        v.append(["fail", "행동 유형 정의 외 값: " + str(at)])
    kind = env.get("action_kind", None)
    if kind is not None:
        if isinstance(kind, str) and kind.strip().lower() in INVALID:
            v.append(["fail", "무효값 전송: action_kind (무효값은 필드 자체를 생략)"])
        elif kind not in KINDS16:
            v.append(["fail", "표준 분류 정의 외 값: " + str(kind)])
    name = env.get("action_name") or ""
    if name.startswith("axzad_"):
        v.append(["warn", "광고 계측 이벤트 혼입 · 행동 로그 순수성 위반(광고에 표준 분류 오용)"])
    elif name and not _NAME_RE.match(name):
        v.append(["warn", "명명 규칙 위반: 화면_액션 형식이 아니거나 무맥락 이름(" + name + ")"])
    luid = env.get("log_unique_id")
    if luid and luid in seen_luids:
        v.append(["warn", "동일 log_unique_id 중복 전송(집계 시 중복 제거 대상)"])
    for k in env:
        if k.startswith("custom_props."):
            ck = k.split(".", 1)[1]
            if not ck.startswith("tesla_") and ck not in CPROP_KEYS:
                v.append(["warn", "커스텀 속성 미등록 키: " + ck + " (사전 등록제 · tesla_* 만 예외)"])
    if env.get("action_kind") == "ClickContent" and "click.layer1" not in env:
        v.append(["info", "권장 필드 미수집: click.layer1 (영역별 클릭률 집계 조건)"])
    return v


def _verdict(violations: list) -> str:
    levels = {lv for lv, _ in violations}
    for lv in ("fail", "warn", "info"):
        if lv in levels:
            return lv
    return "pass"


def _bad_on(team) -> bool:
    return bool((MF._SV._report_get(KIND, team, {}) or {}).get("bad"))


def logviewer_data(team=None) -> dict:
    events = list(MF._demo_session(team).get("events") or [])
    records = [{"env": _envelope(e, i), "label": MF.EV_LABEL.get(e.get("event"), str(e.get("event")))}
               for i, e in enumerate(events)][-LOGS_MAX:]
    bad_on = _bad_on(team)
    if bad_on:
        records += [dict(r, injected=True) for r in _bad_examples(MF._now_t())]
    seen, logs, dup = set(), [], 0
    for r in records:
        env = r["env"]
        vio = _judge(env, seen)
        seen.add(env.get("log_unique_id"))
        dup += 1 if any("중복" in m for _, m in vio) else 0
        kind = env.get("action_kind")
        logs.append({"t": _dt.datetime.fromtimestamp(env["access_timestamp"] / 1000).strftime("%H:%M:%S"),
                     "label": r.get("label", ""),
                     "title": "[" + str(env.get("action_type")) + "] "
                              + (str(kind) + " · " if kind is not None else "") + str(env.get("action_name")),
                     "verdict": _verdict(vio), "violations": vio,
                     "feedback": kind in FEEDBACK_KINDS,
                     "injected": bool(r.get("injected")),
                     "fields": [[k, ("true" if v is True else "false" if v is False else str(v)),
                                 GRADE.get(k, "선택")] for k, v in env.items()]})
    counts = {}
    for e in events:
        ev = e.get("event")
        counts["usage" if ev in ("read", "skim") else ev] = counts.get("usage" if ev in ("read", "skim") else ev, 0) + 1
    items = [{"key": k, "label": lb, "map": mp, "n": counts.get(k, 0), "ok": counts.get(k, 0) > 0}
             for k, lb, mp in CONTRACT]
    hit = sum(1 for it in items if it["ok"])
    return {"session": {"uuid": DEVICE_UUID, "suid": SESSION_SUID, "service_id": SERVICE_ID,
                        "deployment": "sandbox", "sdk_type": "WEB", "islogin": False},
            "logs": logs, "n": len(logs),
            "behavior": {"ops": len(events), "logs": len(events), "dup": dup},
            "checklist": {"items": items, "pass_rate": round(hit / len(CONTRACT) * 100),
                          "missing": [it["label"] for it in items if not it["ok"]]},
            "bad_on": bad_on}


def logviewer_ops(body: dict, team=None) -> dict:
    """조작: inject(위반 예시 주입) · clear(주입 제거) · 성공 시 최신 판정 데이터 반환."""
    op = (body or {}).get("op") or ""
    if op not in ("inject", "clear"):
        return {"error": "지원하지 않는 조작입니다: " + str(op)[:20]}
    st = MF._SV.get_store()
    if st and hasattr(st, "save_report"):
        st.save_report(KIND, {"bad": op == "inject"}, team=team)
    return logviewer_data(team)
