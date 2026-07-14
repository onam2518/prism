"""사용자 메타: 목업(MOCKUP). 행동 로그 연결 시 실데이터."""
from __future__ import annotations
import json
from collections import defaultdict as _dd
from . import graphviz as GV
from . import theme as TH

WARNING = ""

# 8 페르소나  : 소비 형태 + 맥락별 강도 시그니처
# intensity: 인텐트(실데이터 값) → 저/중/고. form: 5개 형태 피처.
PERSONAS = [
    {"id": "P1", "name": "정독러", "full": "결정 직전의 정독러",
     "desc": "한 주제를 끝까지 파고들고 저장·재방문 · 속보는 즉시 스킵",
     "form": {"세션 길이": "장", "체류·완주": "고", "전환·이동": "느림", "깊이": "몰입", "시간대": "평일 야간"},
     "intensity": {"기획·심층": "고", "분석·해설": "고", "의견·논평": "중", "속보": "저", "흥미·화제": "저"},
     "topics": ["Business and Finance", "News and Politics", "Science"], "breadth": 0.55, "weight": 14},
    {"id": "P2", "name": "스낵러", "full": "출퇴근 스낵러",
     "desc": "짧고 잦은 세션 · 숏폼·이슈 카드 위주 · 본문은 약함",
     "form": {"세션 길이": "단", "체류·완주": "저", "전환·이동": "빠름", "깊이": "훑기", "시간대": "출퇴근"},
     "intensity": {"흥미·화제": "저", "유머": "중", "속보": "저", "인물 동정": "저", "기획·심층": "저"},
     "topics": ["Entertainment", "Sports", "News and Politics"], "breadth": 0.85, "weight": 22},
    {"id": "P3", "name": "조사자", "full": "이슈 추적 조사자",
     "desc": "특정 사건 발생 시 단기 집중 정독 · 종료 후 이탈",
     "form": {"세션 길이": "장", "체류·완주": "고", "전환·이동": "보통", "깊이": "단기 몰입", "시간대": "사건 발생기"},
     "intensity": {"사건 경과 보도": "고", "분석·해설": "고", "의견·논평": "중", "속보": "중"},
     "topics": ["News and Politics"], "breadth": 0.5, "weight": 12},
    {"id": "P4", "name": "이중모드", "full": "낮밤 이중모드 직장인",
     "desc": "주간 훑기 ↔ 야간 몰입 · 시간대로 형태 전환",
     "form": {"세션 길이": "주간 단/야간 장", "체류·완주": "주간 저/야간 고", "전환·이동": "보통", "깊이": "시간대 전환", "시간대": "주·야 이중"},
     "intensity": {"분석·해설": "고", "기획·심층": "고", "흥미·화제": "저", "속보": "중"},
     "topics": ["News and Politics", "Business and Finance"], "breadth": 0.6, "weight": 12},
    {"id": "P5", "name": "편식러", "full": "엔터 화제성 편식러",
     "desc": "소비 대부분이 단일 카테고리(연예 화제성)에 과집중",
     "form": {"세션 길이": "중", "체류·완주": "중", "전환·이동": "보통", "깊이": "훑기+편중", "시간대": "수시"},
     "intensity": {"흥미·화제": "중", "인물 동정": "중", "분석·해설": "저"},
     "topics": ["Entertainment"], "breadth": 0.3, "weight": 12},
    {"id": "P6", "name": "팬덤", "full": "팬덤 추종형",
     "desc": "특정 팀·인물 관련만 집중 · 이벤트 시 폭증 · 형태 무관 소비",
     "form": {"세션 길이": "이벤트시 장", "체류·완주": "고", "전환·이동": "느림", "깊이": "엔티티 추종", "시간대": "이벤트 연동"},
     "intensity": {"인물 동정": "고", "흥미·화제": "고", "속보": "고", "분석·해설": "중"},
     "topics": ["Sports", "Entertainment"], "breadth": 0.35, "weight": 12},
    {"id": "P7", "name": "전환기", "full": "관심사 전환기 사용자",
     "desc": "관심 토픽이 급전환 중(드리프트) · 신규 토픽 고강도 형성",
     "form": {"세션 길이": "중", "체류·완주": "중", "전환·이동": "보통", "깊이": "전환 중", "시간대": "수시"},
     "intensity": {"분석·해설": "중", "라이프스타일": "중", "기획·심층": "중"},
     "topics": ["Technology and Computing", "Business and Finance"], "breadth": 0.6, "weight": 8},
    {"id": "P8", "name": "라이트", "full": "주말 회귀 라이트 유저",
     "desc": "신호 희소(콜드·저데이터) · 강도 산정 어려움",
     "form": {"세션 길이": "단", "체류·완주": "저", "전환·이동": "빠름", "깊이": "저데이터", "시간대": "주말 오전"},
     "intensity": {"흥미·화제": "저", "속보": "저"}, "topics": [], "breadth": 1.0, "weight": 8},
]

# 활용 시나리오 
SCENARIOS = [
    {"title": "소비 형태 기반 홈 재배치", "desc": "같은 콘텐츠 풀 · 형태에 따라 순서·구성·밀도만 다르게",
     "ex": "훑기형=숏폼·이슈 카드 위로 / 파고들기형=이어보기·심층·카페 위로"},
    {"title": "능동형 컴포넌트 (토픽 활용)", "desc": "선호(토픽×소비 맥락)를 토픽 조건으로 변환해 능동 삽입",
     "ex": "사건형=관심 사건 묶음 · 조건형=재테크×심층 슬롯 · 엔티티형=팔로우 엔티티 큐레이션"},
    {"title": "유저 프로파일링 (데이터→컴포넌트)", "desc": "행동→형태→강도→페르소나→산출까지 단계 추적",
     "ex": "“이 데이터 때문에 이 컴포넌트가 떴다”를 근거로 설명"},
    {"title": "광고 타겟팅", "desc": "선호 콘텐츠 카테고리 × 선호 인텐트 교차로 정밀 매칭",
     "ex": "Business and Finance × 심층 분석 관심 → 금융 콘텐츠·광고 (맥락 부적합 노출 감소)"},
]

_LV = {"저": 1, "중": 2, "고": 3}


def _pcat(c):
    v = c["entity_categories"][0] if c["entity_categories"] else "기타"
    return _t1(v)


def _t1(v):
    from .dashboard import tier1_remap
    return tier1_remap(v)


def build_user_meta(results_path: str, n_users: int = 200,
                    logs_path: str = None, demo: bool = False, profiles: dict = None) -> dict:
    """logs_path 있으면 실데이터, demo=True 면 합성 목업, 기본은 빈 상태(개념·명세만).
    동일 산출 구조 → 대시보드 그대로 재사용. (가짜 데이터를 기본 노출하지 않는다)"""
    if logs_path:
        return build_from_logs(results_path, logs_path, profiles=profiles)
    if demo:
        return build_mock(results_path, n_users)
    return _empty_user_meta(results_path)


def _empty_user_meta(results_path: str) -> dict:
    """행동 로그 미연결 기본 상태: 사용자 인스턴스 없이 페르소나 정의·명세·시나리오만 표시."""
    try:
        n = sum(1 for r in _read_jsonl(results_path)
                if r.get("quality_meta", {}).get("finalGrade") == "G")
    except Exception:
        n = 0
    pdefs = [{"id": p["id"], "name": p["name"], "full": p["full"], "desc": p["desc"],
              "form": p["form"], "intensity": p["intensity"]} for p in PERSONAS]
    return {
        "warning": ("행동 로그 소스가 연결되지 않았습니다 · 가짜 데이터 대신 개념·명세만 표시 · "
                    "실데이터 연결: usermeta/report 에 --logs <behavior_logs.jsonl> · "
                    "동작하는 목업 DEMO는 GitHub 저장소(README의 DEMO 링크)에서 확인/다운로드"),
        "is_mock": False, "empty": True, "source": "(행동 로그 미연결)",
        "n_contents": n, "users": [],
        "aggregate": {"personas": [[p["name"], 0] for p in PERSONAS],
                      "entity_categories": [], "intent_categories": [],
                      "intensity": [["고", 0], ["중", 0], ["저", 0]], "form_depth": [],
                      "engagement": {"avg_click_rate": 0, "high": 0, "low": 0}},
        "personas_def": pdefs, "scenarios": SCENARIOS,
        "formula": "소비 강도 = 맥락(인텐트)별 Σ(체류/30 × 클릭가중)의 상대 등급(저/중/고)",
    }


# 실데이터 경로: 행동 로그 스펙
#   {"user_id","content_id","event":"impression|click","dwell_sec","scroll_pct","ts"}
#   content_id 는 results 의 행 index 또는 content_ref.id / title 과 매칭.
_CENTROID = {  # (depth, intensity, breadth) → 8 페르소나 시그니처
    "정독러": (.9, .85, .4), "스낵러": (.2, .3, .85), "조사자": (.8, .8, .4),
    "이중모드": (.55, .7, .6), "편식러": (.35, .5, .2), "팬덤": (.7, .8, .25),
    "전환기": (.5, .55, .6), "라이트": (.1, .2, 1.0),
}


def build_from_logs(results_path: str, logs_path: str, profiles: dict = None) -> dict:
    from collections import defaultdict
    rows = _read_jsonl(results_path)
    by_id = {}
    for i, r in enumerate(rows):
        im = r.get("item_meta") or {}
        item = {"idx": i, "title": r.get("content_ref", {}).get("title", ""),
                "service": r.get("content_ref", {}).get("displayServiceName", ""),
                "intent_categories": im.get("intent", []),
                "entity_categories": [_t1(c) for c in (im.get("content_category") or [])],
                "entities": im.get("entities", [])}
        by_id[str(i)] = item
        by_id[item["title"]] = item
        if r.get("content_ref", {}).get("id"):
            by_id[str(r["content_ref"]["id"])] = item

    sess = defaultdict(list)
    for lg in _read_jsonl(logs_path):
        it = by_id.get(str(lg.get("content_id")))
        if it:
            sess[lg.get("user_id", "?")].append((it, lg))

    pdefs = [{"id": p["id"], "name": p["name"], "full": p["full"], "desc": p["desc"],
              "form": p["form"], "intensity": p["intensity"]} for p in PERSONAS]
    users = []
    for uid, evs in sess.items():
        viewed = list({it["idx"]: it for it, _ in evs}.values())
        logs = [{"seq": j + 1, "content_idx": it["idx"], "title": it["title"][:30],
                 "service": it["service"], "event": lg.get("event", "impression"),
                 "dwell_sec": int(lg.get("dwell_sec", 0)), "scroll_pct": int(lg.get("scroll_pct", 0)),
                 "cat": (it["entity_categories"][0] if it["entity_categories"] else "기타"),
                 "intent": (it["intent_categories"][0] if it["intent_categories"] else "기타")}
                for j, (it, lg) in enumerate(evs)]
        form, intensity, prof = _profile_from_logs(viewed, logs)
        pname = _nearest_persona(form, intensity, viewed)
        users.append({"user_id": uid, "profile": (profiles or {}).get(uid),
                      "persona": pname, "persona_id": _pid(pname),
                      "persona_full": pname, "topic": prof["ent"][0][0] if prof["ent"] else "기타",
                      "form": form, "intensity": intensity, "behavior_log": logs,
                      "interest_entity_categories": prof["ent"], "interest_intent_categories": prof["int"],
                      "affinity_entities": prof["ents"], "engagement": prof["eng"],
                      "persona_derivation": [["행동 로그(실데이터)", f"{len(logs)}건"],
                                             ["소비 형태(FORM)", " · ".join(f"{k}:{v}" for k, v in form.items())],
                                             ["소비 강도", " · ".join(f"{k}={v}" for k, v in list(intensity.items())[:4])],
                                             ["페르소나(근접 매칭)", pname]]})
    agg = _aggregate_users(users)
    return {"warning": "", "is_mock": False,
            "source": f"실 행동 로그 {logs_path} → 소비 형태·강도 → 페르소나 (실데이터)",
            "n_contents": len(rows), "users": users, "aggregate": agg,
            "personas_def": pdefs, "scenarios": SCENARIOS,
            "formula": "소비 강도 = 맥락(인텐트)별 Σ(체류/30 × 클릭가중)의 상대 등급(저/중/고)"}


def _profile_from_logs(viewed, logs):
    from collections import Counter
    w_int, w_ent, ents = Counter(), Counter(), Counter()
    lbi = {l["content_idx"]: l for l in logs}
    dwell_sum = scroll_sum = clicks = 0
    for c in viewed:
        l = lbi.get(c["idx"], {})
        dwell_sum += l.get("dwell_sec", 0); scroll_sum += l.get("scroll_pct", 0)
        clicks += 1 if l.get("event") == "click" else 0
        w = (l.get("dwell_sec", 10) / 30.0) * (2.0 if l.get("event") == "click" else 1.0)
        for ic in c["intent_categories"]:
            w_int[ic] += w
        for ec in c["entity_categories"]:
            w_ent[ec] += w
        for e in c["entities"]:
            ents[e] += w
    n = max(1, len(viewed))
    avg_dwell = dwell_sum / n; avg_scroll = scroll_sum / n
    depth = "몰입" if avg_dwell >= 45 and avg_scroll >= 60 else ("훑기" if avg_dwell < 15 else "혼합")
    comp = "고" if avg_scroll >= 60 else ("저" if avg_scroll < 30 else "중")
    form = {"세션 길이": "장" if len(viewed) >= 25 else ("단" if len(viewed) < 8 else "중"),
            "체류·완주": comp, "전환·이동": "느림" if avg_dwell >= 45 else "빠름",
            "깊이": depth, "시간대": "관측 기반"}
    top_int = _top(w_int, 6)
    mx = max([v for _, v in top_int], default=1)
    intensity = {k: ("고" if v >= mx * .66 else "중" if v >= mx * .33 else "저") for k, v in top_int}
    eng = {"views": len(viewed), "clicks": clicks,
           "click_rate": round(clicks / n, 2), "avg_dwell_sec": round(avg_dwell, 1)}
    return form, intensity, {"ent": _top(w_ent, 5), "int": top_int, "ents": _top(ents, 8), "eng": eng}


def _nearest_persona(form, intensity, viewed):
    if len(viewed) < 5:
        return "라이트"
    depth = {"몰입": .9, "혼합": .5, "훑기": .2}.get(form["깊이"], .5)
    iv = [{"고": 1, "중": .6, "저": .25}.get(v, .5) for v in intensity.values()]
    inten = sum(iv) / len(iv) if iv else .3
    breadth = min(1.0, len({*()} | set()) or len(intensity) / 6)
    best, bd = "스낵러", 9
    for name, (d, i, b) in _CENTROID.items():
        dist = (d - depth) ** 2 + (i - inten) ** 2 + (b - breadth) ** 2
        if dist < bd:
            bd, best = dist, name
    return best


def _pid(name):
    for p in PERSONAS:
        if p["name"] == name:
            return p["id"]
    return "P?"


def _aggregate_users(users):
    from collections import Counter
    pdist = Counter(u["persona"] for u in users)
    ent_c, int_c, inten = Counter(), Counter(), Counter()
    form_depth = Counter()
    for u in users:
        for c, _ in u["interest_entity_categories"]:
            ent_c[c] += 1
        for c, _ in u["interest_intent_categories"]:
            int_c[c] += 1
        for lv in u["intensity"].values():
            inten[lv] += 1
        form_depth[u["form"]["깊이"]] += 1
    crs = [u["engagement"]["click_rate"] for u in users]
    return {"personas": [[p["name"], pdist.get(p["name"], 0)] for p in PERSONAS],
            "entity_categories": _sortc(ent_c), "intent_categories": _sortc(int_c),
            "intensity": [[lv, inten.get(lv, 0)] for lv in ("고", "중", "저")],
            "form_depth": _sortc(form_depth),
            "engagement": {"avg_click_rate": round(sum(crs) / len(crs), 2) if crs else 0,
                           "high": sum(1 for c in crs if c >= 0.6),
                           "low": sum(1 for c in crs if c < 0.4)}}


def build_mock(results_path: str, n_users: int = 200) -> dict:
    rows = _read_jsonl(results_path)
    catalog = []
    for i, r in enumerate(rows):
        if r.get("quality_meta", {}).get("finalGrade") != "G":
            continue
        im = r.get("item_meta") or {}
        if not im:
            continue
        catalog.append({
            "idx": i, "title": r.get("content_ref", {}).get("title", ""),
            "service": r.get("content_ref", {}).get("displayServiceName", ""),
            "intent_categories": im.get("intent", []),
            "entity_categories": [_t1(c) for c in (im.get("content_category") or [])],
            "entities": im.get("entities", []),
        })

    from collections import Counter, defaultdict
    by_cat = defaultdict(list)
    for c in catalog:
        by_cat[(c["entity_categories"][0] if c["entity_categories"] else "기타")].append(c)
    cat_rank = [k for k, _ in Counter({k: len(v) for k, v in by_cat.items()}).most_common()
                if k != "기타"]

    # 가중치로 페르소나 분포 생성(결정론) → 다양하게 200명
    seq = []
    for p in PERSONAS:
        seq += [p] * p["weight"]
    users = []
    for u in range(n_users):
        persona = seq[(u * 7 + u // len(seq)) % len(seq)]   # 결정론 셔플
        topic = _user_topic(persona, cat_rank, u)
        viewed = _viewed(persona, topic, by_cat, catalog, u)
        logs = _logs(persona, viewed, u)
        prof = _profile(persona, topic, viewed, logs)
        users.append({
            "user_id": f"mock-user-{u+1:03d}", "persona": persona["name"],
            "persona_id": persona["id"], "persona_full": persona["full"],
            "topic": topic, "form": _user_form(persona, u),
            "intensity": prof["intensity"], "behavior_log": logs,
            "interest_entity_categories": prof["ent"], "interest_intent_categories": prof["int"],
            "affinity_entities": prof["ents"], "engagement": prof["eng"],
            "persona_derivation": prof["deriv"],
        })

    # 집계(개별이 아니라 한눈에)
    pdist = Counter(u["persona"] for u in users)
    ent_cat_users, int_cat_users = Counter(), Counter()
    intensity_cells = Counter()      # 저/중/고 셀 수
    form_depth = Counter()
    for u in users:
        for c, _ in u["interest_entity_categories"]:
            ent_cat_users[c] += 1
        for c, _ in u["interest_intent_categories"]:
            int_cat_users[c] += 1
        for lv in u["intensity"].values():
            intensity_cells[lv] += 1
        form_depth[u["form"]["깊이"]] += 1
    crs = [u["engagement"]["click_rate"] for u in users]
    aggregate = {
        "personas": [[p["name"], pdist.get(p["name"], 0)] for p in PERSONAS],
        "entity_categories": _sortc(ent_cat_users),
        "intent_categories": _sortc(int_cat_users),
        "intensity": [[lv, intensity_cells.get(lv, 0)] for lv in ("고", "중", "저")],
        "form_depth": _sortc(form_depth),
        "engagement": {"avg_click_rate": round(sum(crs) / len(crs), 2) if crs else 0,
                       "high": sum(1 for c in crs if c >= 0.6),
                       "low": sum(1 for c in crs if c < 0.4)},
    }
    return {"warning": WARNING, "is_mock": True,
            "source": " results.jsonl (G 콘텐츠) → 합성 행동 로그 → 소비 형태·강도 → 페르소나",
            "n_contents": len(catalog), "users": users, "aggregate": aggregate,
            "personas_def": [{"id": p["id"], "name": p["name"], "full": p["full"],
                              "desc": p["desc"], "form": p["form"], "intensity": p["intensity"]}
                             for p in PERSONAS],
            "scenarios": SCENARIOS,
            "formula": "소비 강도 = f(체류·완주·저장·재방문·진입경로): 맥락(인텐트)별 상대값으로 저/중/고 등급화"}


def _user_topic(persona, cat_rank, u):
    """페르소나 선호 토픽 풀에서 유저별로 다른 토픽 선택(다양성)."""
    pool = [t for t in persona["topics"] if t in cat_rank] or cat_rank[:6]
    if not pool:
        return "기타"
    return pool[u % len(pool)]


def _viewed(persona, topic, by_cat, catalog, u):
    target = {"장": 34, "중": 18, "단": 8}.get(persona["form"]["세션 길이"][:1], 16)
    if persona["id"] == "P8":
        target = 6
    picks, seen = [], set()

    def add(c):
        if c["idx"] not in seen:
            seen.add(c["idx"]); picks.append(c)

    npref = int(target * (1 - persona["breadth"]) + target * 0.4)
    items = by_cat.get(topic, [])
    off = (u * 5) % max(1, len(items))
    for c in items[off:] + items[:off]:
        add(c)
        if len(picks) >= npref:
            break
    for c in catalog[u::max(2, 11 - int(persona["breadth"] * 8))]:
        add(c)
        if len(picks) >= target:
            break
    return picks[:target] or catalog[u::max(1, len(catalog) // max(1, target))][:target]


def _logs(persona, viewed, u):
    """형태 시그니처로 노출/클릭/체류 생성(가짜)."""
    comp = _LV.get(_last_level(persona["form"]["체류·완주"]), 2)
    base_dwell = {1: 6, 2: 30, 3: 80}[comp]
    click_p = {1: 0.2, 2: 0.5, 3: 0.78}[comp]
    logs = []
    for j, c in enumerate(viewed):
        dwell = max(1, base_dwell + ((u * 7 + j * 13) % max(6, base_dwell)) - base_dwell // 2)
        clicked = ((u * 3 + j * 7) % 100) < click_p * 100
        logs.append({
            "seq": j + 1, "content_idx": c["idx"], "title": c["title"][:30],
            "service": c["service"], "event": "click" if clicked else "impression",
            "dwell_sec": dwell, "scroll_pct": 20 + ((u * 3 + j * 11) % 80),
            "cat": (c["entity_categories"][0] if c["entity_categories"] else "기타"),
            "intent": (c["intent_categories"][0] if c["intent_categories"] else "기타"),
        })
    return logs


def _last_level(s):
    for lv in ("고", "중", "저"):
        if lv in s:
            return lv
    return "중"


def _user_form(persona, u):
    f = dict(persona["form"])
    return f


def _profile(persona, topic, viewed, logs):
    from collections import Counter
    w_int, w_ent, ents = Counter(), Counter(), Counter()
    lbi = {l["content_idx"]: l for l in logs}
    for c in viewed:
        l = lbi.get(c["idx"], {})
        w = (l.get("dwell_sec", 10) / 30.0) * (2.0 if l.get("event") == "click" else 1.0)
        for ic in c["intent_categories"]:
            w_int[ic] += w
        for ec in c["entity_categories"]:
            w_ent[ec] += w
        for e in c["entities"]:
            ents[e] += w
    top_int = _top(w_int, 6)
    # 소비 강도: 페르소나 시그니처를 실제 소비한 인텐트에 투영
    intensity = {}
    for ic, _w in top_int:
        if ic in persona["intensity"]:
            intensity[ic] = persona["intensity"][ic]
    # 시그니처에 있으나 미소비한 맥락도 일부 노출(맥락별 강도 개념 보존)
    for ic, lv in persona["intensity"].items():
        intensity.setdefault(ic, lv)
    eng = {"views": len(viewed), "clicks": sum(1 for l in logs if l["event"] == "click"),
           "click_rate": round(sum(1 for l in logs if l["event"] == "click") / max(1, len(logs)), 2),
           "avg_dwell_sec": round(sum(l["dwell_sec"] for l in logs) / max(1, len(logs)), 1)}
    deriv = [["행동 로그", f"{len(logs)}건 (클릭 {eng['clicks']}·평균체류 {eng['avg_dwell_sec']}s)"],
             ["소비 형태(FORM)", " · ".join(f"{k}:{v}" for k, v in persona["form"].items())],
             ["소비 강도", " · ".join(f"{k}={v}" for k, v in list(intensity.items())[:4])],
             ["페르소나", f"{persona['id']} {persona['full']}"]]
    return {"ent": _top(w_ent, 5), "int": top_int, "ents": _top(ents, 8),
            "intensity": intensity, "eng": eng, "deriv": deriv}


def attach_generated(data: dict, gen: dict) -> dict:
    """생성 페르소나(personagen 산출)를 사용자·정의 표에 병행 부착.
    기존 8종 매칭은 그대로 두고, 생성 카드는 별도 필드(gen_persona)와
    personas_def 뒤(generated=True)에 얹어 화면에서 나란히 비교."""
    if not gen:
        data["generated_n"] = 0
        return data
    shown = {}
    for u in data.get("users", []):
        g = gen.get(u.get("user_id"))
        if g:
            u["gen_persona"] = g
            shown[g.get("id", u["user_id"])] = g
    data.setdefault("personas_def", [])
    data["personas_def"] = ([p for p in data["personas_def"] if not p.get("generated")]
                            + sorted(shown.values(), key=lambda g: g.get("id", "")))
    data["generated_n"] = len(shown)
    return data


def _sortc(counter):
    return [[k, v] for k, v in sorted(counter.items(), key=lambda kv: -kv[1])]


def _top(d, k):
    return [[name, round(w, 2)] for name, w in sorted(d.items(), key=lambda kv: -kv[1])[:k]]


def _read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows



# 관계도: 페르소나 ↔ 관심 콘텐츠 카테고리(공유 관심사로 페르소나가 묶임)
_UG_COL = {"persona": "#1e84ff", "cat": "#a05cff"}


def _persona_graph(data):
    pc = _dd(lambda: _dd(int))
    for u in data.get("users", []):
        p = u.get("persona", "")
        if not p:
            continue
        for c, w in u.get("interest_entity_categories", []):
            pc[p][c] += w
    nodes, links = {}, []
    for p, cats in pc.items():
        pid = "p:" + p
        nodes[pid] = {"id": pid, "label": p, "kind": "persona", "val": 9, "hub": True}
        for c, w in sorted(cats.items(), key=lambda x: -x[1])[:5]:
            kid = "k:" + c
            if kid not in nodes:
                nodes[kid] = {"id": kid, "label": c, "kind": "cat", "val": 5, "hub": True}
            links.append({"s": pid, "t": kid, "w": min(3, 1 + w // 2)})
    if not nodes:
        return ""
    return GV.vendor_script() + GV.section(
        "umg", list(nodes.values()), links, _UG_COL,
        "페르소나 관계도", "페르소나 ↔ 관심 콘텐츠 카테고리 · 공유 관심사로 묶임 · 호버=연결 강조",
        legend=[("페르소나", "#1e84ff"), ("관심 콘텐츠 카테고리", "#a05cff")], height=440)


def render_html(results_path: str, n_users: int = 200,
                logs_path: str = None, demo: bool = False, notice: str = "") -> str:
    data = build_user_meta(results_path, n_users, logs_path, demo)
    html = _HTML.replace("/*__DATA__*/", json.dumps(data, ensure_ascii=False))
    html = html.replace("__GRAPH__", _persona_graph(data))
    if notice:
        html = html.replace("<body>", "<body>" + notice, 1)
        html = html.replace("<h1>사용자 메타</h1>", "<h1>사용자 메타 (DEMO)</h1>", 1)
    return TH.inject(html)


def build_html(results_path: str, out_path: str, n_users: int = 200,
               logs_path: str = None, demo: bool = False, notice: str = "") -> dict:
    htmltext = render_html(results_path, n_users, logs_path, demo, notice)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(htmltext)
    data = build_user_meta(results_path, n_users, logs_path, demo)
    return {"users": len(data["users"]), "out": out_path}


_HTML = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>사용자 메타</title><style>
:root{--bg:var(--ds-canvas,#f4f5f7);--card:var(--ds-surface,#fff);--s2:var(--ds-surface-on,#f4f5f7);--line:var(--ds-hairline,rgba(0,0,0,.08));--mut:var(--ds-muted,rgba(0,0,0,.48));--fg:var(--ds-ink,#000);--fg2:var(--ds-body,rgba(0,0,0,.88));
--ac:var(--ds-primary,#1e84ff);--cat:var(--ds-cat-entertainment,#a05cff);--warn:var(--ds-warning,#ff9429);--g:var(--ds-success,#18ba45);--int:var(--ds-cat-sports,#5c77ff);
--sh:var(--ds-shadow-medium,0 1px 10px 0 rgba(0,0,0,.08));
--font:var(--ds-font-body,'Pretendard Variable',-apple-system,sans-serif)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);
color:var(--fg);font:14px/1.55 var(--font);-webkit-font-smoothing:antialiased}
.warn{background:rgba(226,163,60,.07);border:1px solid var(--line);border-left:2px solid var(--warn);color:#ff9429;padding:11px 18px;font-size:12.5px;font-weight:600}
header{padding:18px 26px;border-bottom:1px solid var(--line)}
h1{font-size:24px;margin:0;font-weight:600;letter-spacing:-.6px}.sub{color:var(--mut);font-size:13px;margin-top:4px}
.wrap{padding:20px 26px;display:grid;grid-template-columns:1fr 1fr;gap:16px;max-width:1560px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;box-shadow:var(--sh)}
.card.full{grid-column:1/-1}
.card h2{font-size:12px;margin:0 0 12px;color:var(--mut);text-transform:uppercase;letter-spacing:.08em;font-weight:600}
.card h2 .n{color:var(--fg);text-transform:none;font-size:12px;margin-left:6px;font-weight:400}
.bar{display:flex;align-items:center;gap:9px;margin:5px 0;font-size:12px}
.bar .lab{width:128px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--fg2)}
.bar .track{flex:1;background:var(--ds-surface-on);border-radius:999px;height:8px;overflow:hidden}
.bar .fill{display:block;height:100%;min-width:3px;border-radius:999px}
.bar .n{width:34px;text-align:right;color:var(--mut);font-size:11px;font-variant-numeric:tabular-nums}
.bar.click{cursor:pointer}.bar.click:hover .lab{color:var(--fg)}
.kpis{display:flex;gap:10px;flex-wrap:wrap}
.kpi{flex:1;min-width:80px;background:var(--ds-surface-on);border:1px solid var(--line);border-radius:10px;padding:12px;text-align:center}
.kpi b{font-size:22px;display:block;font-weight:700}.kpi span{font-size:11px;color:var(--mut)}
.intensity{display:flex;gap:8px}.intensity .lv{flex:1;text-align:center;border-radius:10px;padding:12px;border:1px solid var(--line)}
.lv.hi{background:rgba(39,166,68,.14);color:var(--g)}.lv.mid{background:rgba(30,132,255,.14);color:var(--ac)}.lv.lo{background:rgba(138,143,152,.12);color:var(--mut)}
.lv b{font-size:24px;display:block;font-weight:700}
.pdefs{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}
.pdef{background:var(--s2);border:1px solid var(--line);border-radius:10px;padding:12px}
.pdef .pid{font-size:11px;color:var(--cat);font-weight:700}.pdef .pn{font-weight:700;font-size:14px}
.pdef .pd{color:var(--fg2);font-size:11.5px;margin:5px 0;line-height:1.45}
.pdef .pf{font-size:10.5px;color:var(--mut)}
.pdef .pi{margin-top:5px}.pi .t{font-size:10px;border-radius:5px;padding:1px 6px;margin:1px}
.t.hi{background:rgba(39,166,68,.18);color:var(--g)}.t.mid{background:rgba(30,132,255,.16);color:var(--ac)}.t.lo{background:rgba(138,143,152,.14);color:var(--mut)}
.sc{background:var(--ds-surface-on);border:1px solid var(--line);border-radius:9px;padding:11px 13px;margin-bottom:9px}
.sc .t{font-weight:600;font-size:13px}.sc .d{color:var(--fg2);font-size:11.5px;margin-top:2px}.sc .ex{color:var(--ds-muted);font-size:11px;font-style:italic;margin-top:4px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.chip{cursor:pointer;border:1px solid var(--line);border-radius:999px;padding:4px 12px;font-size:12px;color:var(--mut);background:var(--card)}
.chip.on{background:rgba(30,132,255,.14);color:var(--ac);border-color:rgba(30,132,255,.4)}
.ugrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(232px,1fr));gap:10px}
.ucard{background:var(--ds-surface-on);border:1px solid var(--line);border-radius:10px;padding:12px;cursor:pointer;transition:border-color .15s}
.ucard:hover{border-color:var(--ds-border-input-hover)}.ucard .nm{font-weight:700;font-size:13px;display:flex;align-items:center}
.av{width:26px;height:26px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;margin-right:8px;color:#fff}
.uid{color:var(--mut);font-size:11px}.eng{display:flex;gap:12px;margin-top:7px;font-size:11px;color:var(--mut)}.eng b{color:var(--fg)}
.ints{display:flex;flex-wrap:wrap;gap:3px;margin-top:6px}
#ov{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;z-index:9}
#dw{position:fixed;top:0;right:0;height:100%;width:min(800px,96vw);background:var(--ds-surface-on);border-left:1px solid var(--line);
 box-shadow:rgba(0,0,0,.5) -12px 0 40px;overflow:auto;transform:translateX(100%);transition:transform .22s;z-index:10;padding:22px 24px}
#dw.open{transform:none}#dw .x{position:absolute;top:16px;right:18px;color:var(--mut);cursor:pointer;font-size:20px}
.stage{border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:10px 0;background:var(--ds-surface-on)}
.stage .h{font-weight:600;font-size:13px;margin-bottom:8px}.flowarrow{text-align:center;color:var(--ds-muted);font-size:13px;margin:0}
.kv{font-size:12px;color:var(--fg2);margin:3px 0}.kv b{color:var(--mut);font-weight:500}
canvas#ug{width:100%;height:360px;display:block;background:var(--ds-surface-on);border:1px solid var(--line);border-radius:10px;cursor:grab}
.lgd{display:flex;gap:14px;font-size:11px;color:var(--mut);margin-top:8px;flex-wrap:wrap}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;vertical-align:middle;margin-right:4px}
code.j{display:block;white-space:pre-wrap;background:var(--ds-surface-on);border:1px solid var(--line);border-radius:6px;padding:9px;font-size:11px;color:var(--ds-body);margin-top:6px;max-height:220px;overflow:auto}
table.heat{border-collapse:collapse;font-size:11px;min-width:100%}
table.heat th{color:var(--mut);font-weight:600;padding:6px 7px;text-align:center;white-space:nowrap;border-bottom:1px solid var(--line);font-size:10.5px}
table.heat td.rh{color:var(--fg2);font-weight:600;white-space:nowrap;padding:6px 10px 6px 4px;text-align:left}
table.heat td.hc{text-align:center;padding:6px;border-radius:4px;font-size:9px;letter-spacing:-1px}
td.hc.hi{background:rgba(39,166,68,.26);color:#18ba45}td.hc.mid{background:var(--ds-primary-tint);color:var(--ds-primary-deep)}td.hc.lo{background:rgba(120,124,132,.12);color:#6b7488}td.hc.na{background:transparent}
canvas#scat{width:100%;height:300px;display:block;background:var(--ds-surface-on);border:1px solid var(--line);border-radius:10px}
.home{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.hcol{background:var(--ds-surface-on);border:1px solid var(--line);border-radius:10px;padding:11px}
.hcol .ht{font-weight:700;font-size:12.5px;margin-bottom:8px;display:flex;align-items:center;gap:6px}
.slot{font-size:11.5px;border-radius:7px;padding:6px 9px;margin:4px 0;background:var(--ds-surface-on);border:1px solid var(--line);color:var(--fg2)}
.slot.up{border-color:rgba(39,166,68,.45);color:var(--fg)}.slot.dn{border-color:var(--ds-hairline);opacity:.55}
.tl{display:flex;gap:2px;align-items:flex-end;height:48px;margin:6px 0;overflow-x:auto;padding-bottom:2px}
.tl .b{flex:0 0 6px;border-radius:2px 2px 0 0;min-height:4px}
</style></head><body>
<div class="warn" id="warn"></div>
<header><span class="eyebrow">소비 · 사용자 메타</span><h1>사용자 메타</h1><div class="sub" id="sub"></div></header>
<div class="wrap">
 <div class="card"><h2>페르소나 분포 <span class="n" id="pn"></span></h2>
  <div class="uid" style="margin-bottom:8px">클릭 → 해당 페르소나 유저만 필터 · 8유형</div><div id="pdist"></div></div>
 <div class="card"><h2>소비 강도 분포 <span class="hint" data-tip="맥락 셀 단위 · 저·중·고">?</span></h2>
  <div class="intensity" id="intsum"></div>
  <div class="uid" style="margin-top:10px">소비 강도 = 형태(체류·완주·저장·재방문·진입경로)에서 산출되는 최종 평가 지표 · 인텐트(소비 맥락)별 상대값</div></div>
 <div class="card full"><h2>8 페르소나 정의 <span class="hint" data-tip="소비 형태 + 맥락별 강도">?</span></h2><div class="pdefs" id="pdefs"></div></div>
 <div class="card full"><h2>소비 맥락별 강도 히트맵 <span class="hint" data-tip="페르소나 × 인텐트 → 저·중·고">?</span></h2>
  <div style="overflow-x:auto"><div id="heat"></div></div>
  <div class="lgd"><span><span class="dot" style="background:#18ba45"></span>고강도</span><span><span class="dot" style="background:#1e84ff"></span>중강도</span><span><span class="dot" style="background:var(--ds-muted)"></span>저강도</span><span style="margin-left:auto">같은 콘텐츠 맥락도 페르소나에 따라 강도가 다름: 주제 중심 체계로는 구분 불가</span></div></div>
 <div class="card"><h2>소비 형태 지형 <span class="hint" data-tip="깊이 × 소비 강도 · 200명">?</span></h2>
  <canvas id="scat"></canvas>
  <div class="lgd" id="scatlgd"></div></div>
 <div class="card"><h2>홈 재배치 시나리오 <span class="hint" data-tip="같은 풀 · 형태별 순서·밀도">?</span></h2><div id="home"></div></div>
 <div class="card"><h2>관심 콘텐츠 카테고리 <span class="hint" data-tip="보유 유저수">?</span></h2><div id="edist"></div></div>
 <div class="card"><h2>관심 인텐트 <span class="hint" data-tip="소비 맥락">?</span></h2><div id="idist"></div></div>
 __GRAPH__
 <div class="card full"><h2>활용 시나리오 <span class="hint" data-tip="사용자 메타 × 콘텐츠 메타">?</span></h2><div id="scen"></div></div>
 <div class="card full"><h2>유저 관계도 <span class="hint" data-tip="페르소나 허브 · 유저 노드 클릭 시 상세">?</span></h2>
  <canvas id="ug"></canvas>
  <div class="lgd"><span><span class="dot" style="background:#1e84ff"></span>유저</span>
   <span><span class="dot" style="background:#a05cff"></span>페르소나(공유 허브)</span>
   <span style="margin-left:auto">드래그=이동 · 휠=줌 · 같은 페르소나 유저가 가까이 묶임</span></div></div>
 <div class="card full"><h2>유저 목록 <span class="n" id="un"></span></h2>
  <div class="chips" id="ufilter"></div><div class="ugrid" id="ugrid"></div></div>
</div>
<div id="ov" onclick="closeU()"></div><div id="dw"><span class="x" onclick="closeU()">✕</span><div id="dwc"></div></div>
<script>
const D=/*__DATA__*/;const esc=s=>(s||"").replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
let pFilter=null;
const _w=document.getElementById('warn');
if(D.warning){_w.textContent=D.warning;}else{_w.style.display='none';}
const _kind=D.is_mock?'합성':(D.empty?'':'실');
document.getElementById('sub').textContent=D.source+(D.users.length?`  ·  ${_kind} 사용자 ${D.users.length}명 · 소스 콘텐츠 ${D.n_contents}건`:`  ·  소스 콘텐츠 ${D.n_contents}건`);
function col(s){let h=0;for(const ch of (s||''))h=(h*31+ch.charCodeAt(0))%360;return `hsl(${h},58%,56%)`;}
// 페르소나 고정 8색 팔레트(해시 충돌 방지)
const PAL=['#1e84ff','#18ba45','#ff9429','#ff5c66','#a05cff','#18ba45','#ff9429','#8a8f98'];
const PCOL={};(D.personas_def||[]).forEach((p,i)=>{PCOL[p.id]=PAL[i%PAL.length];PCOL[p.name]=PAL[i%PAL.length];});
function pcolor(k){return PCOL[k]||'#9aa6b5';}
const _cssv=(n,f)=>{const v=getComputedStyle(document.documentElement).getPropertyValue(n).trim();return v||f;};
const T_INK=_cssv('--ds-ink','#000'),T_MUT=_cssv('--ds-muted','rgba(0,0,0,.48)'),T_BG=_cssv('--ds-canvas','#f4f5f7'),T_LINE=_cssv('--ds-hairline','rgba(0,0,0,.12)'),T_FAINT=_cssv('--ds-placeholder','rgba(0,0,0,.32)'),T_PRIDEEP=_cssv('--ds-primary-deep','#004fad');
const LVC={'고':'#18ba45','중':'#1e84ff','저':'#8a8f98'};const LVK={'고':'hi','중':'mid','저':'lo'};
function bars(pairs,opt){opt=opt||{};const mx=Math.max(1,...pairs.map(p=>p[1]));
 return pairs.length?pairs.map(([k,v])=>`<div class="bar ${opt.click?'click':''}" ${opt.click?`onclick="filterP('${esc(k)}')"`:''}>
  <span class="lab" title="${esc(k)}">${esc(k)}</span>
  <span class="track"><span class="fill" style="width:${v/mx*100}%;background:${opt.color||col(k)}"></span></span>
  <span class="n">${v}</span></div>`).join(''):'<div class="uid">데이터 부족</div>';}

document.getElementById('pn').textContent=`총 ${D.users.length}명`;
document.getElementById('pdist').innerHTML=bars(D.aggregate.personas,{click:true,color:'#a05cff'});
// 소비 강도
document.getElementById('intsum').innerHTML=D.aggregate.intensity.map(([lv,n])=>
 `<div class="lv ${LVK[lv]}"><b>${n}</b>${lv}강도</div>`).join('');
// 페르소나 정의
document.getElementById('pdefs').innerHTML=D.personas_def.map(p=>`
 <div class="pdef"><div class="pid">${p.id}</div><div class="pn">${esc(p.name)}</div>
  <div class="pd">${esc(p.desc)}</div>
  <div class="pf">${Object.entries(p.form).map(([k,v])=>esc(k)+':'+esc(v)).join(' · ')}</div>
  <div class="pi">${Object.entries(p.intensity).map(([k,v])=>`<span class="t ${LVK[v]}">${esc(k)} ${v}</span>`).join('')}</div>
 </div>`).join('');
document.getElementById('edist').innerHTML=bars(D.aggregate.entity_categories.slice(0,12));
document.getElementById('idist').innerHTML=bars(D.aggregate.intent_categories.slice(0,12),{color:'#18ba45'});
document.getElementById('scen').innerHTML=D.scenarios.map(s=>
 `<div class="sc"><div class="t">${esc(s.title)}</div><div class="d">${esc(s.desc)}</div><div class="ex">예: ${esc(s.ex)}</div></div>`).join('');

// 소비 맥락별 강도 히트맵 (페르소나 × 인텐트)
(function(){
 const seen={};D.personas_def.forEach(p=>Object.keys(p.intensity).forEach(c=>seen[c]=(seen[c]||0)+1));
 const cols=Object.keys(seen).sort((a,b)=>seen[b]-seen[a]);
 const dots={'고':'●●●','중':'●●','저':'●'};
 let h='<table class="heat"><tr><th style="text-align:left">페르소나 \\ 맥락</th>'+cols.map(c=>`<th>${esc(c)}</th>`).join('')+'</tr>';
 D.personas_def.forEach(p=>{h+=`<tr><td class="rh">${p.id} ${esc(p.name)}</td>`+cols.map(c=>{const v=p.intensity[c];return `<td class="hc ${v?LVK[v]:'na'}">${v?dots[v]:''}</td>`;}).join('')+'</tr>';});
 document.getElementById('heat').innerHTML=h+'</table>';
})();

// 홈 재배치 시나리오 ( 사례1)
(function(){
 const rows=[['상단 1','실시간 이슈','숏폼','이어서 깊이 보기'],['상단 2','추천 뉴스','실시간 이슈','관련 심층 기획'],
  ['상단 3','동영상','1분 영상','카페 깊이글'],['중단','쇼핑','뉴스 요약','저장함 이어보기'],['하단','카페 인기','심층 기획 ↓','숏폼 ↓']];
 function cols(idx,cls){return rows.map(r=>`<div class="slot ${r[idx].includes('↓')?'dn':(cls||'up')}">${esc(r[idx])}</div>`).join('');}
 document.getElementById('home').innerHTML=
  `<div class="home">
    <div class="hcol"><div class="ht" style="color:var(--mut)">기준 (공통)</div>${rows.map(r=>`<div class="slot">${esc(r[1])}</div>`).join('')}</div>
    <div class="hcol"><div class="ht">가볍게 훑는형</div>${cols(2)}</div>
   </div>
   <div style="height:8px"></div>
   <div class="home"><div class="hcol" style="grid-column:1/-1"><div class="ht">검색·파고들기형</div>
     <div class="home" style="margin-top:4px">${rows.map(r=>`<div class="slot ${r[3].includes('↓')?'dn':'up'}">${esc(r[3])}</div>`).join('')}</div></div></div>`;
})();

// 소비 형태 지형 산점도 (깊이 × 강도)
(function(){
 const cv=document.getElementById('scat'),cx=cv.getContext('2d');let DPR=Math.min(2,devicePixelRatio||1);
 function rs(){cv.width=cv.clientWidth*DPR;cv.height=cv.clientHeight*DPR;}rs();addEventListener('resize',()=>{rs();draw();});
 const DEPTH={'몰입':0.92,'단기 몰입':0.82,'엔티티 추종':0.72,'시간대 전환':0.55,'전환 중':0.5,'훑기+편중':0.32,'훑기':0.2,'저데이터':0.08};
 function iscore(u){const v=Object.values(u.intensity).map(x=>({'고':3,'중':2,'저':1}[x]||1));return v.length?v.reduce((a,b)=>a+b,0)/v.length/3:0.2;}
 function draw(){cx.setTransform(DPR,0,0,DPR,0,0);const W=cv.clientWidth,H=cv.clientHeight,p=34;cx.clearRect(0,0,W,H);
  cx.strokeStyle=T_LINE;cx.lineWidth=1;cx.strokeRect(p,10,W-p-12,H-p-10);
  cx.fillStyle=T_MUT;cx.font="10px 'Pretendard Variable',-apple-system,sans-serif";
  cx.fillText('← 훑기',p+2,H-p+16);cx.fillText('몰입 →',W-58,H-p+16);
  cx.save();cx.translate(12,H-p);cx.rotate(-Math.PI/2);cx.fillText('저강도 → 고강도',0,0);cx.restore();
  for(let i=0;i<D.users.length;i++){const u=D.users[i];const dx=DEPTH[u.form['깊이']]??0.4,dy=iscore(u);
   // 결정론 지터(±그룹 클라우드): 200명이 8점으로 뭉치지 않게
   const jx=((i*97%41)/41-0.5)*0.16,jy=((i*53%37)/37-0.5)*0.22;
   const x=p+Math.max(0,Math.min(1,dx+jx))*(W-p-22),y=10+Math.max(0,Math.min(1,(1-dy)+jy))*(H-p-20);
   cx.fillStyle=pcolor(u.persona_id);cx.globalAlpha=.6;cx.beginPath();cx.arc(x,y,6.5,0,7);cx.fill();
   cx.globalAlpha=.9;cx.lineWidth=1;cx.strokeStyle=T_BG;cx.stroke();}
  cx.globalAlpha=1;}
 draw();
 document.getElementById('scatlgd').innerHTML=D.personas_def.map(p=>`<span><span class="dot" style="background:${pcolor(p.id)}"></span>${esc(p.name)}</span>`).join('');
})();

// 유저 목록 + 필터
function pcol(pid){return pcolor(pid);}
function renderUsers(){
 const us=pFilter?D.users.filter(u=>u.persona===pFilter):D.users;
 document.getElementById('un').textContent=`${us.length}명${pFilter?` · ${pFilter}`:''}`;
 document.getElementById('ugrid').innerHTML=us.slice(0,120).map(u=>{
  const c=pcol(u.persona_id);
  return `<div class="ucard" onclick="openU('${u.user_id}')">
   <div class="nm"><span class="av" style="background:${c}">${u.persona_id}</span>${esc(u.persona)}</div>
   <div class="uid">${u.user_id} · ${esc(u.topic)}</div>
   <div class="ints">${Object.entries(u.intensity).slice(0,3).map(([k,v])=>`<span class="t ${LVK[v]}">${esc(k)} ${v}</span>`).join('')}</div>
   <div class="eng"><span>소비 <b>${u.engagement.views}</b></span><span>클릭률 <b>${u.engagement.click_rate}</b></span><span>체류 <b>${u.engagement.avg_dwell_sec}s</b></span></div>
  </div>`;}).join('') + (us.length>120?`<div class="uid" style="padding:10px">…외 ${us.length-120}명</div>`:'');
}
function filterP(p){pFilter=(pFilter===p?null:p);renderUserChips();renderUsers();}
function renderUserChips(){
 const names=D.aggregate.personas.map(p=>p[0]);
 document.getElementById('ufilter').innerHTML=`<span class="chip ${!pFilter?'on':''}" onclick="filterP(null)">전체</span>`+
  names.map(n=>`<span class="chip ${pFilter===n?'on':''}" onclick="filterP('${esc(n)}')">${esc(n)}</span>`).join('');
}
renderUserChips();renderUsers();

// 상세 드로어: 행동→형태→강도→페르소나→산출
function openU(uid){
 const u=D.users.find(x=>x.user_id===uid);if(!u)return;
 const mxd=Math.max(1,...u.behavior_log.map(l=>l.dwell_sec));
 const tl=u.behavior_log.slice(0,60).map(l=>`<div class="b" title="${esc(l.title)} · ${l.dwell_sec}s · ${esc(l.intent)}" style="height:${8+l.dwell_sec/mxd*40}px;background:${l.event==='click'?'#1e84ff':T_FAINT}"></div>`).join('');
 const log=u.behavior_log.slice(0,40).map(l=>`<span style="color:${l.event==='click'?T_PRIDEEP:'#8a8f98'}">${l.event==='click'?'●':'○'}</span> ${esc(l.title)} <span class="uid">(${l.dwell_sec}s·${esc(l.intent)})</span>`).join('<br>');
 document.getElementById('dwc').innerHTML=`
  <h2 style="margin:0 0 2px">${esc(u.persona)} <span class="uid">${u.persona_id} · ${esc(u.topic)}</span></h2>
  <div class="uid" style="margin-bottom:12px">${u.user_id} · ${esc(u.persona_full)}</div>
  ${u.persona_derivation.map((d,i)=>`<div class="stage"><div class="h">${i+1}. ${esc(d[0])}</div><div class="kv">${esc(d[1])}</div></div>${i<u.persona_derivation.length-1?'<div class="flowarrow">↓</div>':''}`).join('')}
  <div class="stage"><div class="h">소비 강도 (맥락별)</div>
   ${Object.entries(u.intensity).map(([k,v])=>`<span class="t ${LVK[v]}" style="font-size:11px;margin:2px">${esc(k)} = ${v}</span>`).join('')}</div>
  <div class="stage"><div class="h">관심 콘텐츠 카테고리</div><div class="kv">${u.interest_entity_categories.map(c=>esc(c[0])+' ('+c[1]+')').join(' · ')}</div></div>
  <div class="stage"><div class="h">행동 타임라인 (시계열 소비 순서 · 막대=체류시간 · 파랑=클릭)</div><div class="tl">${tl}</div></div>
  <div class="stage"><div class="h">행동 로그 (합성, 상위 40)</div><code class="j">${log}</code></div>`;
 document.getElementById('ov').style.display='block';document.getElementById('dw').classList.add('open');
}
function closeU(){document.getElementById('ov').style.display='none';document.getElementById('dw').classList.remove('open');}

// 유저↔페르소나 관계도 (캔버스 force, 경량)
(function(){
 const cv=document.getElementById('ug'),cx=cv.getContext('2d');let DPR=Math.min(2,devicePixelRatio||1);
 function rs(){cv.width=cv.clientWidth*DPR;cv.height=cv.clientHeight*DPR;}rs();addEventListener('resize',()=>{rs();fit();});
 const pers=D.personas_def.map(p=>p.name);
 const N=[],L=[];const pidx={};
 pers.forEach((p,i)=>{pidx[p]=N.length;N.push({id:'p:'+p,kind:'p',label:p,x:0,y:0,vx:0,vy:0,deg:0});});
 const sample=D.users.length>140?D.users.filter((_,i)=>i%2===0):D.users;
 sample.forEach(u=>{const i=N.length;N.push({id:u.user_id,kind:'u',label:u.persona_id,x:0,y:0,vx:0,vy:0,deg:0});L.push([i,pidx[u.persona]]);});
 N.forEach((n,i)=>{n.x=cv.clientWidth/2+Math.cos(i*2.399)*Math.sqrt(i)*16;n.y=cv.clientHeight/2+Math.sin(i*2.399)*Math.sqrt(i)*16;});
 L.forEach(([a,b])=>{N[a].deg++;N[b].deg++;});
 let view={x:0,y:0,k:1},drag=null,pan=null,ticks=0;
 function fit(){if(!N.length)return;let xs=N.map(n=>n.x),ys=N.map(n=>n.y);let a=Math.min(...xs),b=Math.max(...xs),c=Math.min(...ys),d=Math.max(...ys);
  let k=Math.min(cv.clientWidth/(b-a+80),cv.clientHeight/(d-c+80));k=Math.max(.1,Math.min(k,2));view.k=k;view.x=cv.clientWidth/2-(a+b)/2*k;view.y=cv.clientHeight/2-(c+d)/2*k;}
 function step(){for(let i=0;i<N.length;i++)for(let j=i+1;j<N.length;j++){let a=N[i],b=N[j],dx=a.x-b.x,dy=a.y-b.y,d2=dx*dx+dy*dy||1;if(d2<25)d2=25;let f=600/d2,d=Math.sqrt(d2);a.vx+=dx/d*f;a.vy+=dy/d*f;b.vx-=dx/d*f;b.vy-=dy/d*f;}
  for(const[s,t]of L){let a=N[s],b=N[t],dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)+.01,f=(d-60)*.02;a.vx+=dx/d*f;a.vy+=dy/d*f;b.vx-=dx/d*f;b.vy-=dy/d*f;}
  const cx0=cv.clientWidth/2,cy0=cv.clientHeight/2;for(const n of N){n.vx+=(cx0-n.x)*.006;n.vy+=(cy0-n.y)*.006;n.vx*=.86;n.vy*=.86;n.vx=Math.max(-30,Math.min(30,n.vx));n.vy=Math.max(-30,Math.min(30,n.vy));if(n!==drag){n.x+=n.vx;n.y+=n.vy;}}}
 function draw(){cx.setTransform(DPR,0,0,DPR,0,0);cx.clearRect(0,0,cv.width,cv.height);cx.save();cx.translate(view.x,view.y);cx.scale(view.k,view.k);
  cx.strokeStyle=T_LINE;cx.lineWidth=.5;for(const[s,t]of L){cx.beginPath();cx.moveTo(N[s].x,N[s].y);cx.lineTo(N[t].x,N[t].y);cx.stroke();}
  for(const n of N){const r=n.kind==='p'?9+Math.min(16,n.deg*.3):4.5;cx.fillStyle=pcolor(n.label);cx.beginPath();cx.arc(n.x,n.y,r,0,7);cx.fill();
   if(n.kind==='p'){cx.fillStyle=T_INK;cx.font=`${12/view.k}px 'Pretendard Variable',-apple-system,sans-serif`;cx.fillText(n.label,n.x+r+2,n.y+4);}}cx.restore();}
 function loop(){if(ticks<200){step();if(!drag&&!pan)fit();ticks++;}draw();requestAnimationFrame(loop);}loop();
 function pick(mx,my){const x=(mx-view.x)/view.k,y=(my-view.y)/view.k;let best=null,bd=200/view.k/view.k;for(const n of N){const d=(n.x-x)**2+(n.y-y)**2;if(d<bd){bd=d;best=n;}}return best;}
 let dn=null,dt=0;cv.addEventListener('mousedown',e=>{const r=cv.getBoundingClientRect(),n=pick(e.clientX-r.left,e.clientY-r.top);dt=Date.now();dn=n;if(n)drag=n;else pan={x:e.clientX,y:e.clientY,vx:view.x,vy:view.y};});
 cv.addEventListener('mouseup',e=>{if(dn&&Date.now()-dt<220&&dn.kind==='u')openU(dn.id);dn=null;});
 addEventListener('mousemove',e=>{const r=cv.getBoundingClientRect();if(drag){drag.x=(e.clientX-r.left-view.x)/view.k;drag.y=(e.clientY-r.top-view.y)/view.k;drag.vx=drag.vy=0;}else if(pan){view.x=pan.vx+(e.clientX-pan.x);view.y=pan.vy+(e.clientY-pan.y);}});
 addEventListener('mouseup',()=>{drag=null;pan=null;});
 cv.addEventListener('wheel',e=>{e.preventDefault();const r=cv.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top,f=e.deltaY<0?1.1:.9;view.x=mx-(mx-view.x)*f;view.y=my-(my-view.y)*f;view.k*=f;},{passive:false});
})();
</script></body></html>"""
