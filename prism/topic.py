"""토픽 생성 체계: 엔티티형/사건형/조건형."""
from __future__ import annotations
from collections import Counter, defaultdict
from itertools import combinations

from .dashboard import _read_jsonl, _is_junk_entity, _canonical_entity_categories, tier1_remap
from .dictionaries import IAB_TIER1_KO as _TIER1_KO
from . import graphviz as GV
from . import theme as TH


# 임계값은 정책이 아니라 구현 선택(정성 기준). 데이터·도메인에 맞게 조정.
CO_MIN = 2            # 사건 클러스터로 묶는 공통 엔티티 최소 개수(공출현 강도).
# 주의: 추출 엔티티가 콘텐츠당 ~2개로 희소 → 3이면 공통 3개 쌍이 구조적으로 거의 없어
# 사건형이 0건으로 비어버림. 2 = 두 named 대상의 공출현(같은 사건 신호)으로 실효 하한.
DUP_HINT = 0.90       # 중복 기사 힌트(인텐트 유사 근사; 임베딩은 선택적 보조)

# 사건형 앵글: 같은 사건의 '관점'을 인텐트에서 정규화(속보/분석/반응/화제)
ANGLE_MAP = {
    "속보": "속보", "속보·사건 추적": "속보", "사건 경과 보도": "속보", "단독": "속보",
    "분석·해설": "분석", "심층 분석": "분석", "기획·심층": "분석", "전술·데이터 분석": "분석",
    "의견·논평": "반응", "의견·논쟁": "반응", "반응·리액션": "반응", "팬 반응": "반응",
    "흥미·화제": "화제", "팬덤·화제성": "화제", "인물 동정": "화제",
}


def _angle(intent: str) -> str:
    return ANGLE_MAP.get(intent, intent or "기타")


def _content_entities(rows, service_names):
    """콘텐츠별 (정크 제외) 엔티티 집합."""
    out = []
    for r in rows:
        im = r.get("item_meta") or {}
        ents = [e for e in (im.get("entities") or []) if not _is_junk_entity(e, service_names)]
        out.append(set(ents))
    return out


def _service_names(rows):
    return {r.get("content_ref", {}).get("displayServiceName", "") for r in rows}


def _grade(r):
    qm = r.get("quality_meta", {})
    return "YELLOW" if qm.get("review") == "yellow" else qm.get("finalGrade", "G")


def _title(r):
    return r.get("content_ref", {}).get("title", "")


# 엔티티형 토픽 
def build_entity_topics(rows, service_names, canon, min_contents=2):
    """단일 엔티티 → 콘텐츠. canon=엔티티→Tier1(정규화). 엔티티당 1개 풀."""
    ent_contents = defaultdict(list)
    for i, r in enumerate(rows):
        im = r.get("item_meta") or {}
        for e in (im.get("entities") or []):
            if _is_junk_entity(e, service_names):
                continue
            ent_contents[e].append(i)
    pools = []
    for e, idxs in ent_contents.items():
        idxs = sorted(set(idxs))
        if len(idxs) < min_contents:
            continue
        cat = tier1_remap(canon.get(e, "Unclassified"))
        pools.append({
            "type": "single", "cluster_id": "S-" + _slug(e),
            "name": e, "category": cat,
            "content_ids": idxs, "count": len(idxs),
            "lifecycle": "영속", "origin": "auto",
            # 모니터링: 엔티티 커버리지(해당 엔티티 언급 콘텐츠 중 매칭 비율) = 1.0(정의상 전수)
        })
    pools.sort(key=lambda p: -p["count"])
    return pools


# 사건형 토픽 : 엔티티 공출현(공통 ≥ co_min) 클러스터
def build_event_topics(rows, service_names, co_min=None):
    co_min = CO_MIN if co_min is None else max(1, int(co_min))
    cent = _content_entities(rows, service_names)
    n = len(rows)
    # 역색인으로 공통 엔티티 ≥CO_MIN 인 콘텐츠 페어만 계산(전체 O(n^2) 회피)
    ent_idx = defaultdict(list)
    for i, s in enumerate(cent):
        for e in s:
            ent_idx[e].append(i)
    pair_common = Counter()
    for e, idxs in ent_idx.items():
        if len(idxs) < 2:
            continue
        for a, b in combinations(idxs, 2):
            pair_common[(a, b)] += 1
    edges = [(a, b) for (a, b), c in pair_common.items() if c >= co_min]

    # 연결요소(union-find)로 사건 클러스터 형성
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b in edges:
        union(a, b)
    groups = defaultdict(list)
    for a, b in edges:
        groups[find(a)].append(a)
        groups[find(b)].append(b)
    pools = []
    for root, members in groups.items():
        members = sorted(set(members))
        if len(members) < 2:
            continue
        # 대표 엔티티 = 클러스터 내 콘텐츠 다수에 등장한 엔티티 상위
        ent_freq = Counter()
        for i in members:
            ent_freq.update(cent[i])
        rep_entities = [e for e, _ in ent_freq.most_common(5)]
        # 앵글 = 인텐트를 관점(속보/분석/반응/화제)으로 정규화. 중복 = 동일 인텐트셋
        angles, intent_sets = {}, {}
        for i in members:
            ic = (rows[i].get("item_meta") or {}).get("intent") or []
            angles[i] = _angle(ic[0] if ic else "")
            intent_sets[i] = tuple(sorted(ic))
        dup = _dup_count(intent_sets)
        # 대표 콘텐츠 = G 우선 + 먼저 등장
        rep = sorted(members, key=lambda i: (0 if _grade(rows[i]) == "G" else 1, i))[0]
        pools.append({
            "type": "composite", "cluster_id": "C-" + _slug("·".join(rep_entities[:2])),
            "name": " · ".join(rep_entities[:3]),
            "representative_entities": rep_entities,
            "content_ids": members, "count": len(members),
            "representative_content": rep, "rep_title": _title(rows[rep]),
            "angles": list(dict.fromkeys(angles.values())),
            "dup_count": dup, "dup_rate": round(dup / len(members), 2),
            "lifecycle": "단기", "origin": "auto",
        })
    pools.sort(key=lambda p: -p["count"])
    return pools


def _dup_count(intent_sets: dict) -> int:
    """동일 인텐트셋 콘텐츠를 중복으로 근사(임베딩 0.90 유사도 대체)."""
    c = Counter(intent_sets.values())
    return sum(v - 1 for v in c.values() if v > 1)


# 조건형 토픽 : 운영자 정의 조건(인텐트 × 콘텐츠 카테고리)
FILTER_DEFS = [
    {"name": "시사 × 속보 추적", "prompt": "속보·사건 경과 추적이면서 시사·정치 콘텐츠 모아줘",
     "ent": {"News and Politics"}, "intent": {"속보", "사건 경과 보도"}},
    {"name": "경제 × 심층 분석", "prompt": "경제·산업 심층 분석 콘텐츠 필터(속보 제외)",
     "ent": {"Business and Finance"}, "intent": {"분석·해설", "기획·심층"}},
    {"name": "연예 × 화제·인물", "prompt": "연예 화제성·인물 동정 콘텐츠만",
     "ent": {"Entertainment"}, "intent": {"흥미·화제", "인물 동정"}},
    {"name": "스포츠 콘텐츠", "prompt": "스포츠 콘텐츠 전부 모아줘",
     "ent": {"Sports"}, "intent": set()},
    {"name": "테크 × 분석·트렌드", "prompt": "테크 분석·트렌드 콘텐츠 필터",
     "ent": {"Technology and Computing"}, "intent": {"분석·해설", "기획·심층", "흥미·화제"}},
    {"name": "팩트체크 모음", "prompt": "팩트체크 성격 콘텐츠 엔티티 무관 전부",
     "ent": set(), "intent": {"팩트체크"}},
    {"name": "심층·기획 큐레이션", "prompt": "심층 분석·기획 콘텐츠 엔티티 무관",
     "ent": set(), "intent": {"분석·해설", "기획·심층"}},
    {"name": "라이프스타일 × 취미", "prompt": "음식·홈·취미 라이프스타일 콘텐츠",
     "ent": {"Food and Drink", "Home and Garden", "Hobbies and Interests"},
     "intent": {"라이프스타일", "리뷰·평가", "취미·DIY", "정보 전달/팁"}},
]


def build_condition_topics(rows, canon, service_names):
    """각 운영자 필터 조건에 부합하는 콘텐츠 매칭. 1개 디멘션만 충족도 가능(OR within, AND across)."""
    # 콘텐츠별 엔티티 Tier1 집합 + 인텐트 집합
    c_ent, c_int = [], []
    for r in rows:
        im = r.get("item_meta") or {}
        ecats = {tier1_remap(c) for c in (im.get("content_category") or [])}
        c_ent.append(ecats)
        c_int.append(set(im.get("intent") or []))
    pools = []
    for f in FILTER_DEFS:
        matched = []
        for i in range(len(rows)):
            ent_ok = (not f.get("ent")) or bool(c_ent[i] & f["ent"])
            int_ok = (not f.get("intent")) or bool(c_int[i] & f["intent"])
            if ent_ok and int_ok:
                matched.append(i)
        rep = sorted(matched, key=lambda i: (0 if _grade(rows[i]) == "G" else 1, i))[:1]
        pools.append({
            "type": "filter", "cluster_id": "F-" + _slug(f["name"]),
            "name": f["name"], "prompt": f["prompt"],
            "dims": {"콘텐츠 카테고리": sorted(f.get("ent", [])),
                     "인텐트": sorted(f.get("intent", []))},
            "content_ids": matched, "count": len(matched),
            "representative_content": rep[0] if rep else None,
            "rep_title": _title(rows[rep[0]]) if rep else "",
            "lifecycle": "중장기", "origin": "manual",
            "active": len(matched) > 0,
        })
    pools.sort(key=lambda p: -p["count"])
    return pools


def _slug(s: str) -> str:
    import re
    return re.sub(r"\s+", "-", (s or "").strip())[:40]


# ── 토픽 스튜디오: 사용자가 자연어+구조 필터로 직접 만드는 조건형 토픽 ──
# 조건형(FILTER_DEFS)과 동일한 매칭 의미(디멘션 내 OR · 디멘션 간 AND)를 쓰되,
# 정의를 운영자가 UI 에서 만들고 저장한다. 차원 = 콘텐츠 카테고리(Tier1) × 인텐트 × 엔티티 키워드.

def _content_dims(rows, service_names):
    """콘텐츠별 매칭 차원 사전계산: (Tier1 카테고리셋, 인텐트셋, 엔티티리스트)."""
    c_cat, c_int, c_ent = [], [], []
    for r in rows:
        im = r.get("item_meta") or {}
        c_cat.append({tier1_remap(c) for c in (im.get("content_category") or [])})
        c_int.append(set(im.get("intent") or []))
        c_ent.append([e for e in (im.get("entities") or []) if not _is_junk_entity(e, service_names)])
    return c_cat, c_int, c_ent


def _match_ids(dims, d):
    """정의 d(cats/intents/keywords)에 부합하는 콘텐츠 인덱스. 각 차원은 OR, 차원 간 AND.
    빈 차원은 무조건 통과(제약 없음) · 키워드는 엔티티 부분일치(대소문자 무시)."""
    c_cat, c_int, c_ent = dims
    cats = set(d.get("cats") or [])
    intents = set(d.get("intents") or [])
    kws = [k.strip().lower() for k in (d.get("keywords") or []) if str(k).strip()]
    out = []
    for i in range(len(c_cat)):
        if cats and not (c_cat[i] & cats):
            continue
        if intents and not (c_int[i] & intents):
            continue
        if kws and not any(any(k in e.lower() for e in c_ent[i]) for k in kws):
            continue
        out.append(i)
    return out


def _def_pool(rows, dims, d):
    """정의 → 조건형 풀 shape(드릴다운·그래프가 filter 와 동일하게 다룸)."""
    matched = _match_ids(dims, d)
    rep = sorted(matched, key=lambda i: (0 if _grade(rows[i]) == "G" else 1, i))[:1]
    cid = d.get("id") or ("U-" + _slug(d.get("name") or d.get("prompt") or "topic"))
    return {
        "type": "filter", "cluster_id": cid,
        "name": d.get("name") or "(무제 토픽)", "prompt": d.get("prompt") or "",
        "dims": {"콘텐츠 카테고리": sorted(d.get("cats") or []),
                 "인텐트": sorted(d.get("intents") or []),
                 "키워드": sorted(d.get("keywords") or [])},
        "content_ids": matched, "count": len(matched),
        "representative_content": rep[0] if rep else None,
        "rep_title": _title(rows[rep[0]]) if rep else "",
        "lifecycle": "사용자", "origin": "user", "active": len(matched) > 0,
    }


def build_custom_topics(rows, service_names, defs):
    """저장된 사용자 정의 목록 → 조건형 풀 리스트(매칭 많은 순)."""
    if not defs:
        return []
    dims = _content_dims(rows, service_names)
    pools = [_def_pool(rows, dims, d) for d in defs]
    pools.sort(key=lambda p: -p["count"])
    return pools


def preview_definition(rows, service_names, d, sample=6):
    """생성 폼 실시간 미리보기: 저장 전 정의의 매칭 수·대표·표본 제목."""
    dims = _content_dims(rows, service_names)
    ids = _match_ids(dims, d)
    ranked = sorted(ids, key=lambda i: (0 if _grade(rows[i]) == "G" else 1, i))
    return {
        "count": len(ids), "n_total": len(rows),
        "rep_title": _title(rows[ranked[0]]) if ranked else "",
        "samples": [{"title": _title(rows[i])[:70], "grade": _grade(rows[i])} for i in ranked[:sample]],
    }


def studio_catalog(rows, service_names=None, top_kw=30):
    """생성 폼 셀렉터 원천: 현재 데이터에 실재하는 인텐트·Tier1 카테고리·엔티티(키워드 후보) + 빈도.
    하드코딩 사전이 아니라 데이터에서 뽑아 매칭이 실제로 성립하는 값만 노출."""
    svc = service_names if service_names is not None else _service_names(rows)
    int_c, cat_c, ent_c = Counter(), Counter(), Counter()
    for r in rows:
        im = r.get("item_meta") or {}
        for t in (im.get("intent") or []):
            if t:
                int_c[t] += 1
        for c in (im.get("content_category") or []):
            cat_c[tier1_remap(c)] += 1
        for e in (im.get("entities") or []):
            if not _is_junk_entity(e, svc):
                ent_c[e] += 1

    def rank(c, k=None):
        items = c.most_common(k) if k else sorted(c.items(), key=lambda x: (-x[1], x[0]))
        return [{"k": a, "v": b} for a, b in items]

    return {"intents": rank(int_c), "cats": rank(cat_c), "keywords": rank(ent_c, top_kw)}


def suggest_dims(text, rows, service_names=None):
    """자연어 문장 → 차원 제안(휴리스틱 · 모델 호출 없음, 의존성 0).
    카탈로그의 인텐트·카테고리 라벨이 문장에 부분일치하면 채택, 문장 토큰 중
    엔티티 카탈로그와 일치하는 것을 키워드 후보로. 즉각·결정적, 데이터 기반."""
    cat = studio_catalog(rows, service_names)
    t = (text or "").lower()
    intents = [x["k"] for x in cat["intents"] if x["k"] and x["k"].lower() in t]
    cats = [x["k"] for x in cat["cats"] if x["k"] and x["k"].lower() in t]
    # 카테고리 한글 별칭도 시도(Tier1 영문 라벨이 문장에 없을 때)
    for x in cat["cats"]:
        ko = _TIER1_KO.get(x["k"]) if isinstance(_TIER1_KO, dict) else None
        if ko and ko.lower() in t and x["k"] not in cats:
            cats.append(x["k"])
    keywords = [x["k"] for x in cat["keywords"] if x["k"] and x["k"].lower() in t][:5]
    return {"cats": cats, "intents": intents, "keywords": keywords}


# 토픽 탭 HTML (대시보드와 동일 토큰)

# 관계도: 콘텐츠(묶음 멤버)가 어떤 풀(엔티티·사건·조건)에 어떻게 들어가는지
_GRAPH_COL = {"content": "#1e84ff", "entity": "#ff9429", "category": "#a05cff",
              "event": "#18ba45", "filter": "#ff5c66"}
_GRAPH_CMAX = 140                         # 콘텐츠 노드 상한(과밀 방지)


def _graph_data(d, rows):
    nodes, links = {}, []
    state = {"cn": 0}

    def add(nid, label, kind, val, hub=False):
        n = nodes.get(nid)
        if n:
            n["val"] = max(n["val"], val)
            n["hub"] = n["hub"] or hub
        else:
            nodes[nid] = {"id": nid, "label": label, "kind": kind, "val": val, "hub": hub}

    def title(i):
        t = (rows[i].get("content_ref", {}).get("title", "") if 0 <= i < len(rows) else "")
        return (t or f"콘텐츠 {i}")[:16]

    def add_members(pool_id, ids, cap, w):
        for i in ids[:cap]:
            nid = "c:" + str(i)
            if nid not in nodes:
                if state["cn"] >= _GRAPH_CMAX:
                    continue
                add(nid, title(i), "content", 3)
                state["cn"] += 1
            links.append({"s": pool_id, "t": nid, "w": w})

    # 엔티티형: 엔티티 → 카테고리 + 대표 콘텐츠(이 엔티티 묶음)
    for p in d["single"][:40]:
        eid = "e:" + p["name"]
        add(eid, p["name"], "entity", 2 + min(7, p["count"]))
        kid = "k:" + p["category"]
        add(kid, p["category"], "category", 14, hub=True)
        links.append({"s": eid, "t": kid, "w": 1})
        add_members(eid, p.get("content_ids", []), 4, 1)
    # 사건형: 사건 → 멤버 콘텐츠(같은 사건 묶음) + 대표 엔티티
    for p in d["composite"][:20]:
        cid = "ev:" + p["cluster_id"]
        add(cid, p["name"][:16], "event", 5 + min(6, p["count"]), hub=True)
        add_members(cid, p.get("content_ids", []), 10, 2)
        for e in p.get("representative_entities", [])[:3]:
            eid = "e:" + e
            add(eid, e, "entity", 3)
            links.append({"s": cid, "t": eid, "w": 1})
    # 조건형: 조건 → 매칭 콘텐츠(조건 묶음) + 콘텐츠 카테고리
    for p in d["filter"]:
        if not p.get("active"):
            continue
        fid = "f:" + p["cluster_id"]
        add(fid, p["name"], "filter", 6, hub=True)
        add_members(fid, p.get("content_ids", []), 6, 1)
        for cat in p["dims"].get("콘텐츠 카테고리", []):
            kid = "k:" + cat
            add(kid, cat, "category", 14, hub=True)
            links.append({"s": fid, "t": kid, "w": 1})
    return list(nodes.values()), links


def _graph_section(d, rows):
    nodes, links = _graph_data(d, rows)
    return GV.vendor_script() + GV.section(
        "mpg", nodes, links, _GRAPH_COL,
        "토픽 관계도", "콘텐츠가 어떤 묶음(엔티티·사건·조건)에 어떻게 구성되는지 · 호버=연결 강조",
        legend=[("콘텐츠", "#1e84ff"), ("엔티티", "#ff9429"), ("콘텐츠 카테고리", "#a05cff"),
                ("사건형 사건", "#18ba45"), ("조건형 조건", "#ff5c66")],
        height=480)


def render_html(results_path: str, notice: str = "") -> str:
    import html as _h
    d = build_topics(results_path)
    rows = _read_jsonl(results_path)
    s = d["summary"]

    def esc(x):
        return _h.escape(str(x))

    def chip(t, c="var(--mut)"):
        return f'<span class="k" style="color:{c}">{esc(t)}</span>'

    # 엔티티형: 순위 + 엔티티 막대
    single_rows = "".join(
        f'<div class="bar"><span class="rk">{i + 1:02d}</span><span class="lab">{esc(p["name"])}</span>'
        f'<span class="kk">{esc(p["category"])}</span>'
        f'<span class="track"><span class="fill" style="width:{min(100,p["count"]*4)}%;background:var(--ent)"></span></span>'
        f'<span class="n">{p["count"]}</span></div>'
        for i, p in enumerate(d["single"][:40])
    )
    # 사건형: 사건 카드 · 스탯 타일(콘텐츠·중복·앵글) + 앵글 칩
    comp_cards = "".join(
        f'<div class="pool comp"><div class="ph"><b>{esc(p["name"])}</b>'
        f'<span class="phr"><span class="lc">단기</span></span></div>'
        f'<div class="pe">{"".join(f"<span class=ent>{esc(e)}</span>" for e in p["representative_entities"][:5])}</div>'
        f'<div class="mt">'
        f'<div class="tile"><b>{p["count"]}</b><span>콘텐츠</span></div>'
        f'<div class="tile"><b>{p["dup_count"]}</b><span>중복 {int(p["dup_rate"]*100)}%</span></div>'
        f'<div class="tile"><b>{len(p["angles"])}</b><span>앵글</span></div></div>'
        f'<div class="angles">{"".join(f"<span class=ang>{esc(a)}</span>" for a in p["angles"][:3])}</div>'
        f'<div class="rep">대표: {esc(p["rep_title"][:50])}</div></div>'
        for p in d["composite"][:24]
    )
    # 조건형: 운영자 조건 카드 · 상태 배지 + 큰 매칭 수
    filt_cards = "".join(
        f'<div class="pool filt {"" if p["active"] else "off"}"><div class="ph">'
        f'<b>{esc(p["name"])}</b>'
        f'<span class="phr"><span class="st {"" if p["active"] else "no"}"><i></i>{"활성" if p["active"] else "저조"}</span>'
        f'<span class="lc">중장기</span></span></div>'
        f'<div class="prompt">“{esc(p["prompt"])}”</div>'
        f'<div class="dims">'
        + (("".join(f'<span class="dim ent">{esc(x)}</span>' for x in p["dims"]["콘텐츠 카테고리"])) or "")
        + (("".join(f'<span class="dim int">{esc(x)}</span>' for x in p["dims"]["인텐트"])) or "")
        + "</div>"
        f'<div class="fmatch"><span class="big">{p["count"]}</span> 매칭 콘텐츠</div>'
        f'<div class="rep">대표: {esc(p["rep_title"][:50])}</div></div>'
        for p in d["filter"]
    )

    html = _MP_HTML.replace("__N__", str(d["n_contents"])) \
        .replace("__NS__", str(s["single"])).replace("__NC__", str(s["composite"])) \
        .replace("__NF__", f'{s["filter_active"]}/{s["filter"]}') \
        .replace("__DUP__", str(s["composite_dup_avg"])) \
        .replace("__FILT__", filt_cards).replace("__COMP__", comp_cards) \
        .replace("__SINGLE__", single_rows) \
        .replace("__ST__", str(d["single_total"])) \
        .replace("__COMIN__", str(CO_MIN)) \
        .replace("__GRAPH__", _graph_section(d, rows))
    if notice:
        html = html.replace("<body>", "<body>" + notice, 1)
        html = html.replace("<h1>토픽 생성 체계</h1>", "<h1>토픽 (DEMO)</h1>", 1)
    return TH.inject(html)


def build_html(results_path: str, out_path: str, notice: str = "") -> dict:
    html = render_html(results_path, notice)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return {"out": out_path}


_MP_HTML = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>토픽 생성 체계</title>
<meta name="description" content="아이템 메타(엔티티·인텐트)를 엔티티형·사건형·조건형 3개 축으로 그룹핑하는 토픽 생성 체계">
<style>
:root{--bg:var(--ds-canvas,#f4f5f7);--surface:var(--ds-surface,#fff);--s2:var(--ds-surface-on,#f4f5f7);--line:var(--ds-hairline,rgba(0,0,0,.08));--mut:var(--ds-muted,rgba(0,0,0,.48));--fg:var(--ds-ink,#000);--fg2:var(--ds-body,rgba(0,0,0,.88));
--pri:var(--ds-primary,#1e84ff);--ent:var(--ds-warning,#ff9429);--int:var(--ds-cat-sports,#5c77ff);--cat:var(--ds-cat-entertainment,#a05cff);
--sh:var(--ds-shadow-medium,0 1px 10px 0 rgba(0,0,0,.08));
--sh-hi:var(--ds-shadow-high,0 2px 16px 0 rgba(0,0,0,.16));
--font:var(--ds-font-body,'Pretendard Variable',-apple-system,sans-serif);
--disp:var(--ds-font-display,'Pretendard Variable',-apple-system,sans-serif)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);
color:var(--fg);font:14.5px/1.55 var(--font);-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
header{padding:26px 28px 22px;border-bottom:1px solid var(--line);
background:radial-gradient(120% 140% at 12% -10%,rgba(104,114,214,.10),transparent 60%),radial-gradient(90% 120% at 100% 0%,rgba(70,189,169,.06),transparent 55%)}
.eyebrow{display:inline-block;font-family:var(--disp);font-size:10.5px;font-weight:600;text-transform:uppercase;
letter-spacing:.18em;color:var(--mut);border:1px solid var(--line);border-radius:999px;padding:3px 10px;margin-bottom:11px}
h1{font-family:var(--disp);font-size:26px;margin:0;font-weight:600;letter-spacing:-.022em;text-wrap:balance}
.sub{color:var(--mut);font-size:14px;margin-top:4px}
.wrap{padding:22px 28px;max-width:1500px}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:22px}
.kpi{background:linear-gradient(180deg,rgba(255,255,255,.022),transparent 70%),var(--surface);
border:1px solid var(--line);border-radius:14px;padding:17px 18px;box-shadow:var(--sh);
transition:transform .22s cubic-bezier(.32,.72,0,1),border-color .22s,box-shadow .22s}
.kpi:hover{transform:translateY(-2px);border-color:var(--ds-border-input-hover);box-shadow:var(--sh-hi)}
.kpi b{font-family:var(--disp);font-size:30px;font-weight:600;letter-spacing:-.02em;display:block;
font-variant-numeric:tabular-nums;line-height:1.1}
.kpi span{color:var(--mut);font-size:11.5px;text-transform:uppercase;letter-spacing:.08em;margin-top:3px;display:block}
.intro{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:26px}
.def{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:17px;box-shadow:var(--sh);
transition:transform .22s cubic-bezier(.32,.72,0,1),border-color .22s}
.def:hover{transform:translateY(-2px);border-color:var(--ds-border-input-hover)}
.def h3{font-family:var(--disp);margin:0 0 6px;font-size:15px;font-weight:600;letter-spacing:-.01em}
.def p{margin:0;color:var(--fg2);font-size:13px;line-height:1.55;text-wrap:pretty}
.def .tag{font-size:10.5px;font-weight:600;padding:3px 9px;border-radius:999px;margin-bottom:9px;display:inline-block;
text-transform:uppercase;letter-spacing:.06em}
h2{font-family:var(--disp);font-size:16px;color:var(--fg);letter-spacing:-.012em;margin:40px 0 4px;font-weight:600;
display:flex;align-items:center;gap:9px}
.h2d{color:var(--mut);font-size:12.5px;margin:0 0 15px;max-width:760px;line-height:1.5}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px}
.pool{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px;box-shadow:var(--sh);
transition:transform .22s cubic-bezier(.32,.72,0,1),border-color .22s,box-shadow .22s}
.pool:hover{transform:translateY(-2px);box-shadow:var(--sh-hi)}
.pool.off{opacity:.5}.pool.off:hover{transform:none}
.ph{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:8px}
.ph b{font-family:var(--disp);font-size:15px;font-weight:600;letter-spacing:-.012em}
.lc{font-size:11px;color:var(--mut);white-space:nowrap}
.prompt{color:var(--fg2);font-size:13px;font-style:italic;margin-bottom:10px}
.pe{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:8px}
.ent{font-size:12px;background:rgba(255,148,41,.16);color:var(--ent);border-radius:6px;padding:2px 8px}
.dims{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:10px}
.dim{font-size:11px;border-radius:6px;padding:2px 8px}
.dim.ent{background:rgba(160,92,255,.16);color:var(--cat)}
.dim.int{background:rgba(92,119,255,.16);color:var(--int)}
.pm{font-size:12px;color:var(--mut);margin-bottom:6px}
.rep{font-size:12px;color:var(--fg2);border-top:1px dashed var(--line);padding-top:7px}
.bar{display:flex;align-items:center;gap:10px;margin:7px 0;font-size:13px}
.bar .lab{width:140px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar .kk{width:150px;color:var(--mut);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar .track{flex:1;background:var(--ds-surface-on);border-radius:999px;height:8px;overflow:hidden}
.bar .fill{display:block;height:100%;border-radius:999px}
.bar .n{width:34px;text-align:right;color:var(--mut);font-variant-numeric:tabular-nums}
.note{color:var(--mut);font-size:12px;margin-top:8px}
.single-wrap{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:var(--sh)}
.bar:hover .lab{color:var(--ds-ink)}.bar{transition:none}.bar .fill{transition:width .5s cubic-bezier(.32,.72,0,1)}
/* ── component kit: 구조 확대(타입 액센트·중첩 하이라이트·상태 배지·스탯 타일) ── */
.kpi{display:flex;flex-direction:column;gap:6px;min-height:92px;justify-content:flex-end;
box-shadow:var(--sh),inset 0 1px 0 rgba(255,255,255,.03)}
.kpi span{order:-1;margin:0}.kpi b{margin:0}
.pool{box-shadow:var(--sh),inset 0 1px 0 rgba(255,255,255,.028)}
.comp,.filt{border-color:var(--line)}
.ph{align-items:center}
.phr{display:flex;align-items:center;gap:6px}
.lc{font-family:var(--disp);font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.07em;
color:var(--mut);background:rgba(255,255,255,.05);border:1px solid var(--line);border-radius:6px;padding:2px 7px;white-space:nowrap}
.st{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:600;border-radius:6px;padding:2px 8px;
background:rgba(70,189,169,.13);color:var(--int);white-space:nowrap}
.st i{width:6px;height:6px;border-radius:50%;background:currentColor;box-shadow:0 0 6px currentColor}
.st.no{background:rgba(139,144,155,.12);color:var(--mut)}.st.no i{box-shadow:none}
.mt{display:flex;gap:8px;margin-bottom:10px}
.tile{flex:1;background:#0b0d11;border:1px solid var(--line);border-radius:10px;padding:9px 11px}
.tile b{font-family:var(--disp);font-size:18px;font-weight:600;display:block;line-height:1.15;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.tile span{font-size:10px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
.fmatch{font-size:12.5px;color:var(--mut);margin-bottom:10px;display:flex;align-items:baseline;gap:7px}
.fmatch .big{font-family:var(--disp);font-size:23px;font-weight:600;color:var(--fg);font-variant-numeric:tabular-nums;letter-spacing:-.015em}
.angles{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:11px}
.ang{font-size:11px;color:var(--fg2);background:var(--ds-state-hover);border:1px solid var(--line);border-radius:6px;padding:2px 8px}
.ent,.dim{border:1px solid transparent}
.bar .rk{font-family:var(--disp);font-size:11px;color:var(--mut);font-variant-numeric:tabular-nums;width:24px;text-align:right;opacity:.65}
.cnt{font-family:var(--disp);font-size:11px;font-weight:600;color:var(--fg2);background:var(--s2);border:1px solid var(--line);
border-radius:6px;padding:1px 8px;vertical-align:middle;margin-left:4px;letter-spacing:0;text-transform:none}
/* 호버 도움말: 정의·설명은 본문이 아니라 여기로 */
.hint{position:relative;display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;
border:1px solid var(--line);color:var(--mut);font-size:10px;font-weight:600;font-style:normal;cursor:help;
vertical-align:middle;transition:color .15s,border-color .15s;flex:none}
.hint:hover{color:var(--fg);border-color:#3a3e48}
.hint::after{content:attr(data-tip);position:absolute;bottom:calc(100% + 9px);left:50%;
transform:translateX(-50%) translateY(4px);width:max-content;max-width:300px;
background:#15181d;border:1px solid #2c2f37;border-radius:10px;padding:11px 13px;
font-family:var(--font);font-size:12px;font-weight:400;line-height:1.6;color:var(--fg2);
white-space:pre-line;text-align:left;letter-spacing:0;text-transform:none;
opacity:0;pointer-events:none;transition:opacity .16s,transform .16s;box-shadow:var(--sh-hi);z-index:9}
.hint:hover::after{opacity:1;transform:translateX(-50%) translateY(0)}
h2 .hint{font-size:10px}
</style></head><body>
<header><span class="eyebrow">토픽 · 그룹핑 체계</span><h1>토픽 생성 체계</h1>
<div class="sub">아이템 메타(엔티티·인텐트)를 서로 다른 축으로 그룹핑하는 3개 병렬 체계 · 콘텐츠 __N__건 기반 · 하나의 콘텐츠는 세 유형에 동시 소속 가능</div></header>
<div class="wrap">
 <div class="cards">
  <div class="kpi"><b>__NS__</b><span>엔티티형 (엔티티)</span></div>
  <div class="kpi"><b>__NC__</b><span>사건형 (사건)</span></div>
  <div class="kpi"><b>__NF__</b><span>조건형 (활성/전체)</span></div>
  <div class="kpi"><b>__DUP__</b><span>사건형 평균 중복률</span></div>
 </div>
 <div class="intro">
  <div class="def"><span class="tag" style="background:rgba(255,148,41,.16);color:var(--ent)">엔티티형</span>
   <h3>이 엔티티에 해당하는 콘텐츠 <span class="hint" data-tip="• 단일 엔티티 단위
• 공통키(통검 DB) 자동 + 신생 키워드 수동 등록
• lifecycle 영속">?</span></h3>
   <span class="lc">영속</span></div>
  <div class="def"><span class="tag" style="background:rgba(92,119,255,.16);color:var(--int)">사건형</span>
   <h3>이 사건을 다룬 콘텐츠 <span class="hint" data-tip="• 엔티티 공출현(공통 ≥__COMIN__개)으로 자연 발생하는 사건 묶음
• 중복 제거 · 앵글 분산">?</span></h3>
   <span class="lc">단기</span></div>
  <div class="def"><span class="tag" style="background:var(--ds-primary-tint);color:var(--pri)">조건형</span>
   <h3>이 조건에 부합하는 콘텐츠 <span class="hint" data-tip="• 운영자가 자연어로 정의한 조건
• 인텐트 × 콘텐츠 카테고리">?</span></h3>
   <span class="lc">중장기</span></div>
 </div>

 __GRAPH__
 <h2>조건형 토픽 · 운영자 정의 조건 <span class="cnt">__NF__</span>
  <span class="hint" data-tip="• 필터(관심사) → 이슈(사건형) → 기사 3단계 드릴다운 진입점
• 저조 필터는 자동 비활성화 권고">?</span></h2>
 <div class="grid">__FILT__</div>

 <h2>사건형 토픽 · 자동 검출 사건 <span class="cnt">상위 24</span>
  <span class="hint" data-tip="• 엔티티 ≥__COMIN__개 공출현으로 자동 생성
• 대표 1건 + 관련 N건(중복 제거)
• 앵글(속보/분석/반응) 분산 노출">?</span></h2>
 <div class="grid">__COMP__</div>

 <h2>엔티티형 토픽 · 엔티티별 콘텐츠 <span class="cnt">상위 40</span>
  <span class="hint" data-tip="• 엔티티당 1개 풀(canonical)
• 엔티티(인물·기업) 팔로우 시 이 단위로 콘텐츠 공급
• 전체 __ST__개">?</span></h2>
 <div class="single-wrap">__SINGLE__</div>
</div>
</body></html>"""


def build_topics(results_path: str, max_single: int = 200, max_composite: int = 120,
                 custom_defs=None, settings=None) -> dict:
    rows = _read_jsonl(results_path)
    svc = _service_names(rows)
    canon = _canonical_entity_categories(rows, svc)
    settings = settings or {}
    co_min = max(1, int(settings.get("co_min") or CO_MIN))
    entity_min = max(1, int(settings.get("entity_min") or 2))
    single = build_entity_topics(rows, svc, canon, min_contents=entity_min)
    composite = build_event_topics(rows, svc, co_min=co_min)
    filt = build_condition_topics(rows, canon, svc)
    custom = build_custom_topics(rows, svc, custom_defs or [])
    # 콘텐츠 → 소속 토픽 역참조(한 콘텐츠가 세 유형 동시 소속 시연용)
    return {
        "n_contents": len(rows),
        "single": single[:max_single], "single_total": len(single),
        "composite": composite[:max_composite], "composite_total": len(composite),
        "filter": filt,
        "custom": custom, "customDefs": list(custom_defs or []),
        "settings": {"co_min": co_min, "entity_min": entity_min},
        "catalog": studio_catalog(rows, svc),
        "titles": [_title(r) for r in rows],
        "grades": [_grade(r) for r in rows],
        "summary": {
            "single": len(single), "composite": len(composite), "filter": len(filt),
            "custom": len(custom), "custom_active": sum(1 for p in custom if p["active"]),
            "composite_dup_avg": round(
                sum(p["dup_rate"] for p in composite) / len(composite), 2) if composite else 0,
            "filter_active": sum(1 for p in filt if p["active"]),
        },
    }


# 하위 호환 별칭 (구 명칭)
build_metapools = build_topics
