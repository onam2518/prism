"""토픽 생성 체계: 엔티티형/사건형(자동) + 사용자 정의(토픽 스튜디오)."""
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
    """콘텐츠별 (정크 제외) 엔티티 집합. 품질 미달은 빈 집합(사건 클러스터에 안 섞임)."""
    out = []
    for r in rows:
        if not _eligible(r):
            out.append(set())
            continue
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


def _eligible(r) -> bool:
    """토픽 편입 자격: 품질 통과(G)만. R·YELLOW(검수 대기)는 토픽 대상 자체가 아니다."""
    return _grade(r) == "G"


def _row_hash(r) -> str:
    """콘텐츠 안정 식별자 = 검수 피드백과 동일한 content_hash. 위치 인덱스(content_ids)와
    달리 재적재·재정렬에도 유지돼 제외(큐레이션) 키로 쓴다."""
    from .store import content_hash
    ref = r.get("content_ref") or {}
    svc = ref.get("displayServiceName", "") or r.get("service", "")
    title = ref.get("title", "") or r.get("title", "")
    if title:
        return content_hash({"displayServiceName": svc, "title": title,
                             "subtitle": ref.get("subtitle", ""), "body": ref.get("body", "")})
    return ref.get("body_hash", "") or r.get("hash", "") or ""


# 엔티티형 토픽
def build_entity_topics(rows, service_names, canon, min_contents=2):
    """단일 엔티티 → 콘텐츠. canon=엔티티→Tier1(정규화). 엔티티당 1개 풀."""
    ent_contents = defaultdict(list)
    for i, r in enumerate(rows):
        if not _eligible(r):
            continue
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


def _slug(s: str) -> str:
    import re
    return re.sub(r"\s+", "-", (s or "").strip())[:40]


# ── 토픽 스튜디오: 사용자가 자연어+구조 필터로 직접 만드는 조건 기반 토픽 ──
# 매칭 의미는 디멘션 내 OR · 디멘션 간 AND 를 쓰되,
# 정의를 운영자가 UI 에서 만들고 저장한다. 차원 = 콘텐츠 카테고리(Tier1) × 인텐트 × 엔티티 키워드.

def _content_dims(rows, service_names, ent_index=None):
    """콘텐츠별 매칭 차원 사전계산: (Tier1 카테고리셋, 인텐트셋, 엔티티리스트, 자격, 개체속성리스트).
    개체속성 = 엔티티 사전 링크(content_entities)의 타입·속성 dict 들 · 사전 미사용 시 빈 리스트.
    콘텐츠 표면에 없는 속성(성별·직업 등)으로 매칭하는 축(예: '여성 스포츠인')."""
    from .entdict import row_hash
    c_cat, c_int, c_ent, elig, c_att = [], [], [], [], []
    for r in rows:
        im = r.get("item_meta") or {}
        c_cat.append({tier1_remap(c) for c in (im.get("content_category") or [])})
        c_int.append(set(im.get("intent") or []))
        c_ent.append([e for e in (im.get("entities") or []) if not _is_junk_entity(e, service_names)])
        elig.append(_eligible(r))
        c_att.append(ent_index.get(row_hash(r), []) if ent_index else [])
    return c_cat, c_int, c_ent, elig, c_att


def _eattr_conds(values):
    """'key:value' 문자열들 → [(key, value)] (비허용 키·형식 오류는 제외)."""
    from .entdict import parse_eattr
    return [c for c in (parse_eattr(v) for v in (values or [])) if c]


def _ent_match(ents, conds):
    """개체 속성 조건은 '한 개체'가 전부(AND) 만족해야 매칭.
    (콘텐츠에 여성 A와 스포츠인 B가 따로 있는 경우는 '여성 스포츠인'이 아니다)"""
    return any(all(str(e.get(k, "")) == v for k, v in conds) for e in ents)


_DIMS = ("cats", "intents", "keywords")


def _def_bundles(d):
    """정의(필수/선택) → 묶음 명세. 필수는 모든 묶음에 AND, 선택은 각각이 별도 '관련' 묶음.
    묶음 = (kind, valueset) · valueset = [(dim, value)] · 전부 AND 매칭.
    · 핵심(core): 필수 ∧ 모든 선택
    · 관련(related): 필수 ∧ 선택 하나 (선택값마다)
    하위호환: req 가 전혀 없으면 선택 없이 '전부 필수'(= 기존 AND 단일 묶음)로 해석."""
    sel = {k: [str(v) for v in (d.get(k) or []) if str(v).strip()] for k in _DIMS}
    req_raw = d.get("req") or {}
    has_req = any(req_raw.get(k) for k in _DIMS)
    if has_req:
        req = {k: [v for v in sel[k] if v in (req_raw.get(k) or [])] for k in _DIMS}
    else:
        req = {k: list(sel[k]) for k in _DIMS}          # 하위호환: 전부 필수
    must = [(k, v) for k in _DIMS for v in req[k]]
    # 개체 속성 조건(eattrs)은 항상 필수: '같은 개체 AND' 의미라 선택(관련 묶음) 분해가 성립하지 않음
    must += [("eattrs", v) for v in dict.fromkeys(str(x).strip() for x in (d.get("eattrs") or []) if str(x).strip())]
    opt = [(k, v) for k in _DIMS for v in sel[k] if v not in req[k]]
    specs = []
    if opt:
        specs.append(("core", must + opt))
        for o in opt:
            specs.append(("related", must + [o]))
    else:
        specs.append(("core", must))
    # 중복 valueset 제거(선택 1개면 핵심==관련)
    seen, out = set(), []
    for kind, vs in specs:
        key = frozenset(vs)
        if key in seen:
            continue
        seen.add(key)
        out.append((kind, vs))
    return out, must, opt


# 개체 속성 키 한글 라벨(묶음 라벨 표시용)
_EATTR_KO = {"type": "타입", "gender": "성별", "occupation": "직업", "nationality": "국적",
             "affiliation": "소속", "org_kind": "조직", "country": "국가", "loc_kind": "장소",
             "af_kind": "종류", "ev_kind": "종류", "domain": "도메인"}


def _label_one(k, v):
    if k != "eattrs":
        return v
    kk, _, vv = str(v).partition(":")
    return f"{_EATTR_KO.get(kk, kk)}={vv}"


def _valueset_label(vs):
    return " · ".join(_label_one(k, v) for k, v in vs) if vs else "전체(조건 없음)"


def _match_valueset(dims, vs):
    """valueset(=[(dim,value)]) 를 전부 만족(AND)하는 콘텐츠 인덱스. 키워드는 엔티티 부분일치.
    eattrs 는 모아서 '같은 개체 AND' 로 판정."""
    c_cat, c_int, c_ent, elig, c_att = dims
    econds = _eattr_conds([v for k, v in vs if k == "eattrs"])
    out = []
    for i in range(len(c_cat)):
        if not elig[i]:
            continue
        ok = True
        for k, v in vs:
            if k == "cats":
                if v not in c_cat[i]:
                    ok = False; break
            elif k == "intents":
                if v not in c_int[i]:
                    ok = False; break
            elif k == "eattrs":
                continue                               # 아래에서 일괄 판정
            else:  # keywords: 엔티티 부분일치
                vl = v.lower()
                if not any(vl in e.lower() for e in c_ent[i]):
                    ok = False; break
        if ok and econds and not _ent_match(c_att[i], econds):
            ok = False
        if ok:
            out.append(i)
    return out


def _neg_blocked(dims, neg) -> set:
    """제외 조건(neg={cats,intents,keywords})에 걸리는 콘텐츠 인덱스 집합.
    차원·값 무관 하나라도 걸리면 탈락(OR) · 토픽의 모든 묶음에 공통 적용 · 키워드는 엔티티 부분일치."""
    c_cat, c_int, c_ent, elig, _c_att = dims
    cats = set((neg or {}).get("cats") or [])
    intents = set((neg or {}).get("intents") or [])
    kws = [str(k).strip().lower() for k in ((neg or {}).get("keywords") or []) if str(k).strip()]
    if not (cats or intents or kws):
        return set()
    out = set()
    for i in range(len(c_cat)):
        if not elig[i]:
            continue
        if (cats and c_cat[i] & cats) or (intents and c_int[i] & intents) or \
           (kws and any(any(k in e.lower() for e in c_ent[i]) for k in kws)):
            out.add(i)
    return out


def _bundle(rows, dims, cid, kind, vs, sample=0, blocked=None):
    ids = _match_valueset(dims, vs)
    if blocked:
        ids = [i for i in ids if i not in blocked]
    ranked = sorted(ids, key=lambda i: (0 if _grade(rows[i]) == "G" else 1, i))
    b = {
        "cluster_id": cid, "kind": kind, "label": _valueset_label(vs),
        "valueset": [{"dim": k, "v": v} for k, v in vs],
        "content_ids": ids, "count": len(ids),
        "representative_content": ranked[0] if ranked else None,
        "rep_title": _title(rows[ranked[0]]) if ranked else "",
    }
    if sample:
        # i = 행 인덱스: 호출부(serve 미리보기)가 상세 화면 계약(_detail_row)으로 확장하는 키
        b["samples"] = [{"i": i, "title": _title(rows[i])[:70], "grade": _grade(rows[i])} for i in ranked[:sample]]
    return b


def build_custom_topics(rows, service_names, defs, ent_index=None):
    """저장된 사용자 정의 목록 → 그룹 리스트. 각 그룹 = 토픽 1개가 여러 묶음(핵심+관련)으로 펼쳐짐."""
    if not defs:
        return []
    dims = _content_dims(rows, service_names, ent_index=ent_index)
    groups = []
    for d in defs:
        specs, must, opt = _def_bundles(d)
        neg = d.get("neg") or {}
        blocked = _neg_blocked(dims, neg)
        did = d.get("id") or ("U-" + _slug(d.get("name") or d.get("prompt") or "topic"))
        bundles = []
        for idx, (kind, vs) in enumerate(specs):
            cid = did + ("-core" if kind == "core" else "-r" + str(idx))
            bundles.append(_bundle(rows, dims, cid, kind, vs, blocked=blocked))
        groups.append({
            "id": did, "type": "custom", "origin": "user",
            "name": d.get("name") or "(무제 토픽)", "prompt": d.get("prompt") or "",
            "must": [{"dim": k, "v": v, "label": _label_one(k, v)} for k, v in must],
            "opt": [{"dim": k, "v": v, "label": _label_one(k, v)} for k, v in opt],
            "neg": [{"dim": k, "v": v} for k in _DIMS for v in (neg.get(k) or [])],
            "bundles": bundles, "n_bundles": len(bundles),
            "core_count": next((b["count"] for b in bundles if b["kind"] == "core"), 0),
        })
    groups.sort(key=lambda g: -(g.get("core_count") or 0))
    return groups


def preview_definition(rows, service_names, d, sample=6, ent_index=None):
    """생성 폼 실시간 미리보기: 저장 전 정의의 묶음(핵심+관련)별 매칭 수·표본."""
    dims = _content_dims(rows, service_names, ent_index=ent_index)
    specs, must, opt = _def_bundles(d)
    blocked = _neg_blocked(dims, d.get("neg") or {})
    bundles = []
    for idx, (kind, vs) in enumerate(specs):
        bundles.append(_bundle(rows, dims, "prev-" + str(idx), kind, vs,
                               sample=sample if kind == "core" else 0, blocked=blocked))
    return {
        "n_total": sum(1 for x in dims[3] if x), "bundles": bundles,
        "must_n": len(must), "opt_n": len(opt), "neg_blocked": len(blocked),
    }


def studio_catalog(rows, service_names=None, top_kw=30):
    """생성 폼 셀렉터 원천: 현재 데이터에 실재하는 인텐트·Tier1 카테고리·엔티티(키워드 후보) + 빈도.
    하드코딩 사전이 아니라 데이터에서 뽑아 매칭이 실제로 성립하는 값만 노출."""
    svc = service_names if service_names is not None else _service_names(rows)
    int_c, cat_c, ent_c = Counter(), Counter(), Counter()
    for r in rows:
        if not _eligible(r):
            continue
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


def eattr_catalog(ent_index, top=100) -> list:
    """개체 속성 조건 후보: 엔티티 사전에 실재하는 'key:value' 빈도(콘텐츠 링크 기준).
    토픽 스튜디오 '엔티티 속성' 셀렉터 원천 · 실제로 매칭이 성립하는 값만 노출."""
    from .entdict import ALLOWED_EATTR_KEYS
    cnt = Counter()
    for ents in (ent_index or {}).values():
        for e in ents:
            for k in ALLOWED_EATTR_KEYS:
                v = str(e.get(k) or "").strip()
                if v:
                    cnt[f"{k}:{v}"] += 1
    return [{"k": a, "v": b, "label": _label_one("eattrs", a)}
            for a, b in sorted(cnt.items(), key=lambda x: (-x[1], x[0]))[:top]]


def meta_taxonomy():
    """시스템 전체 아이템메타 분류 어휘(현재 데이터 유무와 무관): 카테고리(Tier1)·인텐트 전량.
    조건값 자동생성이 '우리의 모든 아이템메타'를 후보로 고려하도록 사전(dictionaries)에서 직접 구성.
    데이터에 아직 없는 값도 미래 매칭을 위해 조건으로 선택 가능."""
    from . import dictionaries as D
    cats = list(_TIER1_KO.keys()) if isinstance(_TIER1_KO, dict) else []
    intents = []

    def _add(seq):
        for v in (seq or []):
            if v and v not in intents:
                intents.append(v)

    _add(getattr(D, "INTENT_CATEGORIES_UNIVERSAL", []))
    _add(getattr(D, "INTENT_FORM_UNIVERSAL", []))
    for vs in (getattr(D, "INTENT_CATEGORIES_BY_SERVICE", {}) or {}).values():
        _add(vs)
    return {"cats": cats, "intents": intents}


# 여러 라벨에 공통으로 걸쳐 과매칭을 유발하는 일반어(토큰 부분일치에서 제외)
_SUGGEST_STOP = {"분석", "정보", "보도", "소식", "콘텐츠", "기사", "방송", "발표", "중심"}


def _label_tokens(label):
    """라벨(·/공백/쉼표로 구분)을 유의미 토큰으로. 2자+ · 일반어 제외."""
    import re as _re
    return [tok for tok in _re.split(r"[·,/\s]+", label or "")
            if len(tok) >= 2 and tok not in _SUGGEST_STOP]


# 배제 표현: 라벨 언급 직후 이 표지가 이어지면 '빼 달라'는 뜻으로 해석("속보는 빼줘")
_NEG_MARKS = ("빼", "제외", "말고", "제거", "없이")


def _neg_after(t, frag):
    """t 안의 frag 등장 위치 바로 뒤(조사 포함 8자)에 배제 표지가 있는가."""
    p = 0
    while True:
        i = t.find(frag, p)
        if i < 0:
            return False
        tail = t[i + len(frag): i + len(frag) + 8]
        if any(m in tail for m in _NEG_MARKS):
            return True
        p = i + 1


def suggest_dims(text, rows, service_names=None):
    """자연어 문장 → 차원 제안(휴리스틱 · 모델 호출 없음, 의존성 0).
    전체 아이템메타 분류(사전) ∪ 현재 데이터 present 를 후보로. 전체 라벨 일치 또는
    라벨을 쪼갠 유의미 토큰 부분일치('인물들'→'인물·사연')까지 잡아 substring-only 누락을 줄인다.
    카테고리 영문 라벨은 토큰화하지 않음(and/health 등 과매칭 방지) · 한글 별칭만 토큰 허용.
    배제 표현('속보는 빼줘')이 붙은 라벨은 선택이 아니라 제외 조건(neg)으로 제안한다."""
    tax = meta_taxonomy()
    cat = studio_catalog(rows, service_names)
    all_cats = list(dict.fromkeys(tax["cats"] + [x["k"] for x in cat["cats"]]))
    all_ints = list(dict.fromkeys(tax["intents"] + [x["k"] for x in cat["intents"]]))
    t = (text or "").lower()

    def _hit_frags(label):
        """라벨이 문장에 걸린 조각들(전체 라벨 + 유의미 토큰). 비면 미언급."""
        frags = []
        ll = (label or "").lower()
        if ll and ll in t:
            frags.append(ll)
        frags += [tok.lower() for tok in _label_tokens(label) if tok.lower() in t]
        return frags

    def _split(label_frags):
        return any(_neg_after(t, f) for f in label_frags)

    intents, neg_int = [], []
    for x in all_ints:
        frags = _hit_frags(x) if x else []
        if frags:
            (neg_int if _split(frags) else intents).append(x)
    cats, neg_cat = [], []
    for x in all_cats:
        frags = [x.lower()] if x.lower() in t else []        # 영문 Tier1 전체 라벨(토큰화 안 함)
        ko = _TIER1_KO.get(x) if isinstance(_TIER1_KO, dict) else None
        if not frags and ko:
            frags = _hit_frags(ko)                           # 한글 별칭은 토큰 부분일치 허용('뉴스'→'뉴스·정치')
        if frags:
            (neg_cat if _split(frags) else cats).append(x)
    keywords, neg_kw = [], []
    for x in cat["keywords"]:
        k = x["k"]
        if k and k.lower() in t:
            (neg_kw if _neg_after(t, k.lower()) else keywords).append(k)
    keywords, neg_kw = keywords[:5], neg_kw[:5]
    # 필수/선택 기본값(휴리스틱): 주제·대상(카테고리·키워드)=필수(정체성), 관점·형식(인텐트)=선택(관련 확장)
    req = {"cats": list(cats), "intents": [], "keywords": list(keywords)}
    return {"cats": cats, "intents": intents, "keywords": keywords, "req": req,
            "neg": {"cats": neg_cat, "intents": neg_int, "keywords": neg_kw}}


# 토픽 탭 HTML (대시보드와 동일 토큰)

# 관계도: 콘텐츠(묶음 멤버)가 어떤 풀(엔티티·사건)에 어떻게 들어가는지
_GRAPH_COL = {"content": "#1e84ff", "entity": "#ff9429", "category": "#a05cff",
              "event": "#18ba45"}
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
    return list(nodes.values()), links


def _graph_section(d, rows):
    nodes, links = _graph_data(d, rows)
    return GV.vendor_script() + GV.section(
        "mpg", nodes, links, _GRAPH_COL,
        "토픽 관계도", "콘텐츠가 어떤 묶음(엔티티·사건)에 어떻게 구성되는지 · 호버=연결 강조",
        legend=[("콘텐츠", "#1e84ff"), ("엔티티", "#ff9429"), ("콘텐츠 카테고리", "#a05cff"),
                ("사건형 사건", "#18ba45")],
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
    html = _MP_HTML.replace("__N__", str(d["n_contents"])) \
        .replace("__NS__", str(s["single"])).replace("__NC__", str(s["composite"])) \
        .replace("__DUP__", str(s["composite_dup_avg"])) \
        .replace("__COMP__", comp_cards) \
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
<meta name="description" content="아이템 메타(엔티티·인텐트)를 엔티티형·사건형 축으로 그룹핑하는 토픽 생성 체계">
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
.dim.neg{background:rgba(255,92,102,.14);color:#ff5c66}
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
<div class="sub">아이템 메타(엔티티·인텐트)를 서로 다른 축으로 그룹핑하는 병렬 체계 · 콘텐츠 __N__건 기반 · 하나의 콘텐츠는 여러 유형에 동시 소속 가능</div></header>
<div class="wrap">
 <div class="cards">
  <div class="kpi"><b>__NS__</b><span>엔티티형 (엔티티)</span></div>
  <div class="kpi"><b>__NC__</b><span>사건형 (사건)</span></div>
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
 </div>

 __GRAPH__
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


# ── 토픽 큐레이션: 개별 콘텐츠 제외(운영자 오버레이) ──
# 메타도 정의도 맞지만 편집 판단으로 빼야 하는 콘텐츠를 토픽 단위로 걷어낸다.
# 키 = 토픽 id(자동형 cluster_id · 사용자형 그룹 id) × content_hash 라 재적재에도 유지.
# 사건형 cluster_id 는 대표 엔티티에서 파생되므로 클러스터 구성이 크게 바뀌면 제외가
# 고아가 될 수 있다 → 관리 패널에서 복구·정리로 해소.

def _exclusion_sets(exclusions) -> dict:
    """설정의 exclusions({토픽id: [항목]}) → {토픽id: hash 집합}. 항목은 dict({h,…}) 또는 str."""
    out = {}
    for tid, lst in (exclusions or {}).items():
        hs = set()
        for e in (lst or []):
            h = e.get("h") if isinstance(e, dict) else e
            if h:
                hs.add(str(h))
        if hs:
            out[str(tid)] = hs
    return out


def _apply_exclusion(pool, key, rows, hashes, exmap):
    """풀의 content_ids 에서 제외 hash 를 걷어내고 수·대표를 재계산. excluded_n 로 표면화."""
    exset = exmap.get(key)
    ids = pool.get("content_ids") or []
    if not exset or not ids:
        return
    kept = [i for i in ids if hashes[i] not in exset]
    if len(kept) == len(ids):
        return
    pool["content_ids"] = kept
    pool["count"] = len(kept)
    pool["excluded_n"] = len(ids) - len(kept)
    if "representative_content" in pool and pool.get("representative_content") not in kept:
        ranked = sorted(kept, key=lambda i: (0 if _grade(rows[i]) == "G" else 1, i))
        pool["representative_content"] = ranked[0] if ranked else None
        if "rep_title" in pool:
            pool["rep_title"] = _title(rows[ranked[0]]) if ranked else ""


def build_topics(results_path: str, max_single: int = 200, max_composite: int = 120,
                 custom_defs=None, settings=None, exclusions=None, ent_index=None) -> dict:
    rows = _read_jsonl(results_path)
    svc = _service_names(rows)
    canon = _canonical_entity_categories(rows, svc)
    settings = settings or {}
    co_min = max(1, int(settings.get("co_min") or CO_MIN))
    entity_min = max(1, int(settings.get("entity_min") or 2))
    single = build_entity_topics(rows, svc, canon, min_contents=entity_min)
    composite = build_event_topics(rows, svc, co_min=co_min)
    custom = build_custom_topics(rows, svc, custom_defs or [], ent_index=ent_index)
    catalog = studio_catalog(rows, svc)
    catalog["eattrs"] = eattr_catalog(ent_index)       # 엔티티 사전 속성 조건 후보(빈도순)
    exmap = _exclusion_sets(exclusions)
    if exmap:
        hashes = [_row_hash(r) for r in rows]
        for p in single + composite:
            _apply_exclusion(p, p["cluster_id"], rows, hashes, exmap)
        for g in custom:
            for b in (g.get("bundles") or []):
                _apply_exclusion(b, g["id"], rows, hashes, exmap)
            g["core_count"] = next((b["count"] for b in g["bundles"] if b["kind"] == "core"), 0)
        single.sort(key=lambda p: -p["count"])
        composite.sort(key=lambda p: -p["count"])
        custom.sort(key=lambda g: -(g.get("core_count") or 0))
    # 콘텐츠 → 소속 토픽 역참조(한 콘텐츠가 세 유형 동시 소속 시연용)
    return {
        "n_contents": len(rows),
        "n_eligible": sum(1 for r in rows if _eligible(r)),
        "single": single[:max_single], "single_total": len(single),
        "composite": composite[:max_composite], "composite_total": len(composite),
        "custom": custom, "customDefs": list(custom_defs or []),
        "settings": {"co_min": co_min, "entity_min": entity_min},
        "catalog": catalog,
        "titles": [_title(r) for r in rows],
        "grades": [_grade(r) for r in rows],
        "summary": {
            "single": len(single), "composite": len(composite),
            "custom": len(custom), "custom_bundles": sum(g["n_bundles"] for g in custom),
            "composite_dup_avg": round(
                sum(p["dup_rate"] for p in composite) / len(composite), 2) if composite else 0,
        },
    }


# 하위 호환 별칭 (구 명칭)
build_metapools = build_topics
