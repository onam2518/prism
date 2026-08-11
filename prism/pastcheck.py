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
import json
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
# (키, 라벨, PAST 매핑, 기대 표기, 1회성) · 1회성 = 기대 정확히 1건 → 2건 이상이면 중복 판정(R4)
CONTRACT = (
    ("impression", "피드 노출", "ViewImp", "1건 이상", False),
    ("search", "콘텐츠 찾기(검색)", "Event · Search", "1건 이상", False),
    ("click", "카드 탭(클릭)", "Event · ClickContent", "1건 이상", False),
    ("usage", "읽기 종료(사용성)", "Usage · UsagePage", "1건 이상", False),
    ("react", "감정 반응(피드백)", "Event · Like / Dislike", "1건 이상", False),
    ("comment", "댓글 등록", "Event · 행동 이름 규칙", "1건 이상", False),
)
# 주입 예시를 켰을 때만 대조하는 1회성 기대. 실측 관찰의 "앱 실행 로그 동일 시각 2회"가
# 기획 R4 의 중복 판정 사례라 체크리스트에서 누락(0건)과 중복(2건 이상)의 차이를 보여 준다.
CONTRACT_INJECTED = (("AppLaunch", "앱 실행", "Event · AppLaunch", "정확히 1건", True),)

# 판정 근거 규칙 항목(R2 수용 기준: 판정 근거가 되는 규칙 항목을 로그별로 확인).
# 이름은 정본 문서(11. 정책 및 운영 · 12. 수집 데이터 정의)의 규칙 단위를 그대로 쓴다.
RULE_REQUIRED = "필수 11필드"
RULE_INVALID = "무효값 금지"
RULE_TYPE4 = "행동 유형 4종"
RULE_KIND16 = "표준 분류 16종"
RULE_NAME = "명명 규칙(화면_액션)"
RULE_ADS = "광고 로그 분리"
RULE_DUP = "중복 전송 금지"
RULE_CPROP = "커스텀 속성 사전 등록"
RULE_RECOMMEND = "권장 필드"
RULE_BYPASS = "바이패스 전달(형식만)"


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
    """판정 ① 로그 단위(형식) + 중복(②의 로그 표면).

    반환은 [level, msg, field, rule] 목록. field·rule 은 기획 131 R2 수용 기준
    ("불합격 사유에 위반 필드명이 포함될 것" · "판정 근거가 되는 규칙 항목을 로그별로 확인")
    을 위해 붙인다. 뒤에 덧붙이므로 [level, msg] 만 읽는 기존 소비자와 호환된다."""
    v = []
    for k in REQUIRED:
        val = env.get(k, None)
        if val is None:
            v.append(["fail", "필수 필드 누락: " + k, k, RULE_REQUIRED])
        elif isinstance(val, str) and val.strip().lower() in INVALID:
            v.append(["fail", "무효값 전송: " + k + " (무효값은 필드 자체를 생략)", k, RULE_INVALID])
    at = env.get("action_type")
    if at is not None and at not in TYPES4:
        v.append(["fail", "행동 유형 정의 외 값: " + str(at), "action_type", RULE_TYPE4])
    kind = env.get("action_kind", None)
    if kind is not None:
        if isinstance(kind, str) and kind.strip().lower() in INVALID:
            v.append(["fail", "무효값 전송: action_kind (무효값은 필드 자체를 생략)",
                      "action_kind", RULE_INVALID])
        elif kind not in KINDS16:
            v.append(["fail", "표준 분류 정의 외 값: " + str(kind), "action_kind", RULE_KIND16])
    name = env.get("action_name") or ""
    if name.startswith("axzad_"):
        v.append(["warn", "광고 계측 이벤트 혼입 · 행동 로그 순수성 위반(광고에 표준 분류 오용)",
                  "action_name", RULE_ADS])
    elif name and not _NAME_RE.match(name):
        v.append(["warn", "명명 규칙 위반: 화면_액션 형식이 아니거나 무맥락 이름(" + name + ")",
                  "action_name", RULE_NAME])
    luid = env.get("log_unique_id")
    if luid and luid in seen_luids:
        v.append(["warn", "동일 log_unique_id 중복 전송(집계 시 중복 제거 대상)",
                  "log_unique_id", RULE_DUP])
    for k in env:
        if k.startswith("custom_props."):
            ck = k.split(".", 1)[1]
            if ck.startswith("tesla_"):
                # 바이패스: PAST 는 값을 검증하지 않고 전달만 한다 → 존재만 확인(R7)
                v.append(["info", "바이패스 전달 확인: " + ck + " (값은 판정 대상 아님)", k, RULE_BYPASS])
            elif ck not in CPROP_KEYS:
                v.append(["warn", "커스텀 속성 미등록 키: " + ck + " (사전 등록제 · tesla_* 만 예외)",
                          k, RULE_CPROP])
    # 노출 추가 정보 등 JSON 문자열 필드는 형식만 판정한다(R7 · 값의 의미는 판정 안 함)
    for k, val in env.items():
        if isinstance(val, str) and val[:1] in ("{", "["):
            try:
                json.loads(val)
            except ValueError:
                v.append(["fail", "JSON 문자열 형식 위반: " + k, k, RULE_BYPASS])
    if env.get("action_kind") == "ClickContent" and "click.layer1" not in env:
        v.append(["info", "권장 필드 미수집: click.layer1 (영역별 클릭률 집계 조건)",
                  "click.layer1", RULE_RECOMMEND])
    return v


def _verdict(violations: list) -> str:
    levels = {v[0] for v in violations}
    for lv in ("fail", "warn", "info"):
        if lv in levels:
            return lv
    return "pass"


def _bad_on(team) -> bool:
    return bool((MF._SV._report_get(KIND, team, {}) or {}).get("bad"))


def logviewer_data(team=None) -> dict:
    """검증 결과 한 벌. 판정·교차 검사는 **세션 전체 봉투**로 하고 표시만 상한을 건다.

    표시 상한(LOGS_MAX)으로 자른 목록에 교차 검사(노출-클릭 연결)를 걸면, 노출 로그가
    창 밖으로 밀리는 순간 이후의 정상 클릭이 전부 '연결 끊김'으로 오탐됐다 —
    노출은 피드 진입 때 한 번만 쌓이고 이벤트는 계속 붙기 때문(2026-08 감사 T6).
    중복(dup) 집계·중복 판정도 같은 이유로 잘리지 않은 전체가 기준이다."""
    events = list(MF._demo_session(team).get("events") or [])
    records = [{"env": _envelope(e, i), "label": MF.EV_LABEL.get(e.get("event"), str(e.get("event")))}
               for i, e in enumerate(events)]
    bad_on = _bad_on(team)
    if bad_on:
        records += [dict(r, injected=True) for r in _bad_examples(MF._now_t())]
    seen, judged, dup = set(), [], 0
    for r in records:
        env = r["env"]
        vio = _judge(env, seen)
        seen.add(env.get("log_unique_id"))
        dup += 1 if any(v[3] == RULE_DUP for v in vio) else 0
        kind = env.get("action_kind")
        judged.append({"t": _dt.datetime.fromtimestamp(env["access_timestamp"] / 1000).strftime("%H:%M:%S"),
                       "label": r.get("label", ""),
                       "title": "[" + str(env.get("action_type")) + "] "
                                + (str(kind) + " · " if kind is not None else "") + str(env.get("action_name")),
                       "verdict": _verdict(vio), "violations": vio,
                       "feedback": kind in FEEDBACK_KINDS,
                       "injected": bool(r.get("injected")),
                       "fields": [[k, ("true" if v is True else "false" if v is False else str(v)),
                                   GRADE.get(k, "선택")] for k, v in env.items()]})
    # 표시용만 상한: 세션 로그는 최근 LOGS_MAX 건 · 주입 예시는 항상 함께 보인다
    logs = judged[:len(events)][-LOGS_MAX:] + judged[len(events):]
    counts = {}
    for e in events:
        ev = e.get("event")
        counts["usage" if ev in ("read", "skim") else ev] = counts.get("usage" if ev in ("read", "skim") else ev, 0) + 1
    spec = list(CONTRACT)
    if bad_on:                                       # 1회성 기대는 주입 예시가 있을 때만 대조
        spec += list(CONTRACT_INJECTED)
        for r in records:
            if r.get("injected") and r["env"].get("action_kind") == "AppLaunch":
                counts["AppLaunch"] = counts.get("AppLaunch", 0) + 1
    items = [_checklist_item(k, lb, mp, exp, once, counts.get(k, 0))
             for k, lb, mp, exp, once in spec]
    hit = sum(1 for it in items if it["state"] == "pass")
    return {"session": {"uuid": DEVICE_UUID, "suid": SESSION_SUID, "service_id": SERVICE_ID,
                        "deployment": "sandbox", "sdk_type": "WEB", "islogin": False},
            "logs": logs, "n": len(logs),
            "behavior": {"ops": len(events), "logs": len(events), "dup": dup},
            "checklist": {"items": items, "pass_rate": round(hit / len(items) * 100) if items else 0,
                          "done": sum(1 for it in items if it["n"]), "total": len(items),
                          "missing": [it["label"] for it in items if it["state"] == "miss"],
                          "dups": [it["label"] for it in items if it["state"] == "dup"]},
            "bypass": _bypass_summary(records),
            "bad_on": bad_on}


def _checklist_item(key, label, mapping, expect, once, n) -> dict:
    """행동 단위 판정(R4): 기대 있음·발생 0건 = 누락 / 1회성 기대·2건 이상 = 중복."""
    state = "miss" if n == 0 else ("dup" if (once and n > 1) else "pass")
    return {"key": key, "label": label, "map": mapping, "expect": expect,
            "n": n, "state": state, "ok": state == "pass"}


def _bypass_summary(records: list) -> dict:
    """피드백 바이패스 확인(R7): 값의 의미는 판정하지 않고 존재·형식·연결만 본다.

    노출-클릭 연결 키는 로그 하나로는 볼 수 없어(교차 검사) 여기서 집계한다 —
    클릭한 content.id 가 앞선 노출 집합에 있어야 노출·클릭이 이어진다.
    ⚠ records 는 표시 상한을 적용하기 **전** 전체 봉투여야 한다(잘린 목록으로 판정하면
    창 밖으로 밀린 노출 때문에 정상 계측이 연결 끊김으로 오탐된다)."""
    imp, hit, miss, tesla, badjson = set(), 0, [], 0, 0
    for r in records:
        env = r["env"]
        for k, val in env.items():
            if k.startswith("custom_props.tesla_"):
                tesla += 1
            if isinstance(val, str) and val[:1] in ("{", "["):
                try:
                    json.loads(val)
                except ValueError:
                    badjson += 1
        iid = env.get("viewimp_contents[].id")
        if iid is not None:
            imp.add(str(iid))
        if env.get("action_kind") == "ClickContent" and "content.id" in env:
            cid = str(env["content.id"])
            if cid in imp:
                hit += 1
            else:
                miss.append(cid)
    return {"tesla": tesla, "bad_json": badjson, "link_ok": hit, "link_miss": miss[:8],
            "imp_ids": len(imp)}


# ── 판정 규칙 시뮬레이터(기획 131 · Prism 조건부 활용안) ────────────────────
# 131 배치 검토 결론은 "3안 독립 신설"이고 Prism 실험실 배치는 부적합(실 로그를 외부
# 인프라로 반출 불가)이다. 그 문서가 Prism 에 남긴 유일한 조기 시범 범위가
# "실 로그 없이 가능한 판정 규칙 시뮬레이터(샘플 로그 붙여넣기 검사)"라 그것만 만든다.
# 붙여넣은 표본만 판정하고 저장하지 않는다.
SIM_MAX = 200_000                 # 입력 상한(바이트) · 그 이상은 표본이 아니라 덤프다
SIM_LOGS_MAX = 200                # 판정 표시 상한
# 실 PAST 로그는 그룹 표기(common.page · action.type)로 오는데 판정 키는 평탄 표기다.
# 붙여넣기 편의를 위해 그룹 접두를 벗기고 action.* 만 별칭으로 맞춘다.
SIM_ALIAS = {"action.type": "action_type", "action.name": "action_name",
             "action.kind": "action_kind", "action.action_type": "action_type",
             "action.action_name": "action_name", "action.action_kind": "action_kind"}


def _flatten(obj, prefix="") -> dict:
    """중첩 dict → 점 표기 평탄화. 리스트·스칼라는 값 그대로 둔다(형식 판정 대상)."""
    out = {}
    for k, v in (obj or {}).items():
        key = prefix + str(k)
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def _normalize(env: dict) -> dict:
    """그룹 표기를 판정 키로 정렬(common. 접두 제거 · action.* 별칭)."""
    out = {}
    for k, v in env.items():
        if k in SIM_ALIAS:
            k = SIM_ALIAS[k]
        elif k.startswith("common."):
            k = k[len("common."):]
        out[k] = v
    return out


def _parse_logs(text: str):
    """JSON 배열 · 단건 객체 · JSONL(줄당 1건) 모두 받는다. (봉투 목록, 파싱 오류)."""
    text = (text or "").strip()
    if not text:
        return [], []
    try:                                             # ① 배열 또는 단건
        doc = json.loads(text)
        rows = doc if isinstance(doc, list) else [doc]
        envs, errs = [], []
        for i, r in enumerate(rows):
            if isinstance(r, dict):
                envs.append(_normalize(_flatten(r)))
            else:
                errs.append("%d번째 항목이 객체가 아닙니다" % (i + 1))
        return envs, errs
    except ValueError:
        pass
    envs, errs = [], []                              # ② JSONL
    for ln, line in enumerate(text.splitlines(), 1):
        line = line.strip().rstrip(",")
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError as e:
            errs.append("%d행 파싱 실패: %s" % (ln, str(e)[:60]))
            continue
        if isinstance(r, dict):
            envs.append(_normalize(_flatten(r)))
        else:
            errs.append("%d행이 객체가 아닙니다" % ln)
    return envs, errs


def simulate(body: dict) -> dict:
    """붙여넣은 샘플 로그를 같은 규칙으로 판정한다(저장 없음 · 세션과 무관)."""
    text = (body or {}).get("text") or ""
    if not text.strip():
        return {"error": "판정할 로그를 붙여넣어 주세요"}
    if len(text) > SIM_MAX:
        return {"error": "입력이 너무 큽니다 · %dKB 이하 표본만 판정합니다" % (SIM_MAX // 1024)}
    envs, errs = _parse_logs(text)
    if not envs:
        return {"error": "판정할 로그를 찾지 못했습니다 · " + (errs[0] if errs else "JSON 객체 또는 배열")}
    seen, logs = set(), []
    for env in envs[:SIM_LOGS_MAX]:
        vio = _judge(env, seen)
        seen.add(env.get("log_unique_id"))
        kind = env.get("action_kind")
        logs.append({"t": "·", "label": "붙여넣기 표본",
                     "title": "[" + str(env.get("action_type")) + "] "
                              + (str(kind) + " · " if kind is not None else "") + str(env.get("action_name")),
                     "verdict": _verdict(vio), "violations": vio,
                     "feedback": kind in FEEDBACK_KINDS, "injected": False,
                     "fields": [[k, ("true" if v is True else "false" if v is False else str(v)),
                                 GRADE.get(k, "선택")] for k, v in env.items()]})
    tally = {lv: sum(1 for l in logs if l["verdict"] == lv) for lv in ("pass", "info", "warn", "fail")}
    return {"logs": logs, "n": len(logs), "parsed": len(envs), "errors": errs[:8],
            "truncated": max(0, len(envs) - SIM_LOGS_MAX), "tally": tally,
            "bypass": _bypass_summary([{"env": e} for e in envs])}


def logviewer_ops(body: dict, team=None) -> dict:
    """조작: inject(위반 예시 주입) · clear(주입 제거) · 성공 시 최신 판정 데이터 반환."""
    op = (body or {}).get("op") or ""
    if op not in ("inject", "clear"):
        return {"error": "지원하지 않는 조작입니다: " + str(op)[:20]}
    st = MF._SV.get_store()
    if st and hasattr(st, "save_report"):
        st.save_report(KIND, {"bad": op == "inject"}, team=team)
    return logviewer_data(team)
