"""엔티티 사전: 개체 고유키·타입(NER 6종)·타입별 속성 레지스트리.

DNM '엔티티 타입 정의 및 구분'(위키 366018723) 기준:
- 타입·속성은 LLM 추출 출력이 아니라 적재 단계 부여 메타 · 개체 사전 신규 등록 시 1회.
- 개체당 타입 1개 · 복수 후보 충돌 시 자동 결정하지 않고 보류(pending).
- 보류 = 타입만 미부여. 개체·콘텐츠 노출은 유지, 타입 의존 분기만 비활성.

POC 보강: 공개 NER 모델 대신 Wikidata(무키·stdlib urllib) 조회로 타입(P31)과
속성(성별·국적·출생연도·직업·소속 등)을 채운다. 목적: 콘텐츠 표면에 없는 개체
속성으로 토픽을 구성한다(예: gender=여성 ∧ occupation=스포츠인 → '여성 스포츠인').
수동 확정(source=manual·status=confirmed)된 필드는 재보강이 덮어쓰지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .classify import is_vague_entity

# ── 타입 사전(NER 표준 6종 · TTA/KLUE 호환) ────────────────────────────────
ENTITY_TYPES = {
    "PS": "인물", "OG": "기관·조직", "LC": "지역·장소",
    "AF": "인공물", "EV": "사건·행사", "TM": "용어·개념",
}

# 타입별 속성 필드(키 → 한글 라벨) · UI 편집 폼·검증의 단일 원천
ATTR_FIELDS = {
    "PS": [("gender", "성별"), ("nationality", "국적"), ("birth_year", "출생연도"),
           ("occupation", "직업(대분류)"), ("occupation_detail", "직업(상세)"), ("affiliation", "소속")],
    "OG": [("org_kind", "조직 종류"), ("country", "국가"), ("industry", "산업")],
    "LC": [("loc_kind", "장소 종류"), ("country", "국가"), ("parent", "상위 행정구역")],
    "AF": [("af_kind", "인공물 종류"), ("creator", "제작 주체"), ("release_year", "발표연도")],
    "EV": [("ev_kind", "사건 종류"), ("start_date", "시작일"), ("end_date", "종료일"), ("location", "장소")],
    "TM": [("domain", "도메인")],
}

# 직업 대분류: 자유 라벨(위키데이터 P106 등) → 토픽 조건으로 쓸 수 있는 고정값 스냅.
# 콘텐츠 카테고리 스냅(dictionaries.normalize_content_category)과 같은 취지 · 순서 = 판정 우선순위.
OCCUPATION_GROUPS = [
    ("스포츠인", ("선수", "축구", "야구", "농구", "배구", "골프", "테니스", "배드민턴", "수영",
                "육상", "빙상", "스케이트", "체조", "유도", "태권도", "격투", "복싱", "레슬링",
                "e스포츠", "프로게이머", "footballer", "athlete", "player")),
    ("연예인", ("가수", "배우", "방송인", "코미디언", "개그", "아이돌", "래퍼", "성우", "모델",
              "유튜버", "인플루언서", "탤런트", "singer", "actor", "actress", "entertainer")),
    ("정치인", ("정치인", "국회의원", "대통령", "총리", "장관", "시장", "도지사", "군수", "구청장",
              "외교관", "politician", "diplomat")),
    ("기업인", ("기업인", "경영자", "사업가", "최고경영자", "창업", "businessperson", "entrepreneur",
              "executive")),
    ("예술인", ("작가", "소설가", "시인", "화가", "조각가", "음악가", "작곡가", "연주", "지휘자",
              "영화 감독", "연출가", "프로듀서", "만화가", "웹툰", "디자이너", "writer", "artist",
              "composer", "film director")),
    ("학자·전문가", ("교수", "학자", "연구원", "연구자", "의사", "변호사", "판사", "검사", "과학자",
                 "경제학자", "약사", "회계사", "professor", "researcher", "scientist", "physician",
                 "lawyer")),
    ("언론인", ("기자", "언론인", "앵커", "아나운서", "평론가", "칼럼니스트", "journalist")),
]


def snap_occupation(labels) -> str:
    """직업 라벨들(자유 문자열) → 대분류 1개. 미매칭은 '기타'."""
    text = " ".join(str(x) for x in (labels or [])).lower()
    if not text.strip():
        return ""
    for group, kws in OCCUPATION_GROUPS:
        if any(k.lower() in text for k in kws):
            return group
    return "기타"


# ── 등록(정규화·게이트·고유키) ──────────────────────────────────────────────
def normalize_name(s: str) -> str:
    return " ".join(str(s or "").split())


import re as _re

_NUM_UNIT = _re.compile(r"[\d,.\s]+[가-힣]{0,2}")      # 수치+단위(3억원·10명·2026년) · vague 정규식 보강


def eligible(name: str) -> bool:
    """사전 등재 게이트: 익명·일반·수치 엔티티(A씨·네티즌·3억원…)는 등재하지 않는다."""
    n = normalize_name(name)
    if not (2 <= len(n) <= 40):
        return False
    if _NUM_UNIT.fullmatch(n):
        return False
    return not is_vague_entity(n)


def new_entity_id(name: str) -> str:
    """개체 고유키(대리키). 외부 공통키(통검·object_id·위키데이터 QID)는 external_ids 에 매핑."""
    return "e_" + hashlib.sha1(normalize_name(name).encode("utf-8")).hexdigest()[:12]


def _empty_entry(name: str) -> dict:
    now = time.time()
    return {"entity_id": new_entity_id(name), "name": normalize_name(name),
            "type": "", "status": "pending", "attrs": {}, "attr_meta": {},
            "external_ids": {}, "merged_into": "", "created_at": now, "updated_at": now}


def ingest_meta(store, items, team="") -> dict:
    """적재 훅 본체. items = [(content_hash, entities[str]), …].
    사전 조회(별칭 포함) → 히트 시 링크만, 미스 시 신규 등록(보류) + 링크. 개체당 재판정 없음.
    반환: {created, linked, new_ids} · new_ids 는 후속 보강(위키데이터) 대상."""
    created = linked = 0
    new_ids = []
    for ch, ents in items:
        seen = set()
        for surface in (ents or []):
            norm = normalize_name(surface)
            if norm in seen or not eligible(norm):
                continue
            seen.add(norm)
            eid = store.ent_id_by_alias(norm)
            if not eid:
                e = _empty_entry(norm)
                eid = e["entity_id"]
                store.ent_upsert(e)
                store.ent_alias_add(norm, eid)
                created += 1
                new_ids.append(eid)
            store.ent_link(ch, eid, surface, team=team)
            linked += 1
    return {"created": created, "linked": linked, "new_ids": new_ids}


def ingest_pairs(store, pairs, team="") -> dict:
    """파이프라인 적재 경로용: [(content, out), …] → ingest_meta."""
    from .store import content_hash
    items = []
    for content, out in pairs:
        ents = ((out or {}).get("item_meta") or {}).get("entities") or []
        if ents:
            items.append((content_hash(content), ents))
    return ingest_meta(store, items, team=team)


def ingest_rows(store, rows, team="") -> dict:
    """백필용: 적재된 결과 payload 행들 → ingest_meta. 해시는 _detail_row 와 동일 규칙."""
    from .store import content_hash
    items = []
    for r in rows:
        im = r.get("item_meta") or {}
        ref = r.get("content_ref") or {}
        ents = im.get("entities") or []
        title = ref.get("title", "") or r.get("title", "")
        if not ents or not title:
            continue
        ch = content_hash({"displayServiceName": ref.get("displayServiceName", "") or r.get("service", ""),
                           "title": title, "subtitle": ref.get("subtitle", ""),
                           "body": ref.get("body", "")})
        items.append((ch, ents))
    return ingest_meta(store, items, team=team)


def row_hash(r: dict) -> str:
    """결과 payload 행 → content_hash (serve._detail_row 와 동일 규칙 · 토픽 조인용)."""
    from .store import content_hash
    ref = r.get("content_ref") or {}
    title = ref.get("title", "") or r.get("title", "")
    if not title:
        return ref.get("body_hash", "") or r.get("hash", "")
    return content_hash({"displayServiceName": ref.get("displayServiceName", "") or r.get("service", ""),
                         "title": title, "subtitle": ref.get("subtitle", ""),
                         "body": ref.get("body", "")})


# ── Wikidata 조회(POC 보강 소스 · 무키·stdlib) ─────────────────────────────
WD_API = "https://www.wikidata.org/w/api.php"
_UA = "prism-entdict/0.1 (item meta pipeline; contact: ops)"


def _http_json(url: str) -> dict:
    """단일 네트워크 심(seam) · 테스트는 이 함수를 대체한다.
    429(레이트리밋)는 Retry-After 준수(캡 60s · 기본 20s)로 최대 3회 재시도 —
    일괄 보강처럼 연속 호출이 몰릴 때 실패가 조용히 누적되는 것을 막는다."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as ex:
            if ex.code != 429 or attempt == 3:
                raise
            time.sleep(min(int(ex.headers.get("Retry-After") or 20), 60))


def _wd(params: dict) -> dict:
    q = urllib.parse.urlencode({**params, "format": "json"})
    return _http_json(f"{WD_API}?{q}")


def wd_search(name: str):
    """이름(ko) → 최상위 후보 {id, label, description} 또는 None."""
    d = _wd({"action": "wbsearchentities", "search": name, "language": "ko", "limit": 1})
    hits = d.get("search") or []
    return hits[0] if hits else None


def wd_entity(qid: str) -> dict:
    d = _wd({"action": "wbgetentities", "ids": qid,
             "props": "claims|labels|aliases", "languages": "ko|en"})
    return (d.get("entities") or {}).get(qid) or {}


def wd_labels(qids) -> dict:
    """QID 목록 → {qid: 한국어 라벨(없으면 영어)}. 50개 단위 배치."""
    out = {}
    qids = [q for q in dict.fromkeys(qids) if q]
    for i in range(0, len(qids), 50):
        d = _wd({"action": "wbgetentities", "ids": "|".join(qids[i:i + 50]),
                 "props": "labels", "languages": "ko|en"})
        for q, e in (d.get("entities") or {}).items():
            labels = e.get("labels") or {}
            out[q] = (labels.get("ko") or labels.get("en") or {}).get("value", "")
    return out


def _claim_qids(claims: dict, prop: str) -> list:
    out = []
    for s in claims.get(prop, []) or []:
        try:
            v = s["mainsnak"]["datavalue"]["value"]
            if isinstance(v, dict) and v.get("id"):
                out.append(v["id"])
        except (KeyError, TypeError):
            pass
    return out


def _claim_qid_current(claims: dict, prop: str) -> str:
    """경력형 프로퍼티(P54 소속팀 등): 종료일(P582) 없는 현행 진술 우선, 없으면 마지막."""
    cur = last = ""
    for s in claims.get(prop, []) or []:
        try:
            q = s["mainsnak"]["datavalue"]["value"]["id"]
        except (KeyError, TypeError):
            continue
        last = q
        if "P582" not in (s.get("qualifiers") or {}):
            cur = q
    return cur or last


def _claim_year(claims: dict, prop: str) -> str:
    for s in claims.get(prop, []) or []:
        try:
            t = s["mainsnak"]["datavalue"]["value"]["time"]   # +1992-07-08T00:00:00Z
            return t.lstrip("+")[:4]
        except (KeyError, TypeError):
            pass
    return ""


# P31(instance of) → 타입. 판정 불가는 보류(문서: 모호하면 미부여) · 후보만 남긴다.
WD_P31_TYPE = {
    "Q5": "PS",                                                # 인간
    # LC: 국가·행정구역·도시·시설(좌표 보유는 아래 휴리스틱이 추가 판정)
    "Q6256": "LC", "Q515": "LC", "Q5119": "LC", "Q35657": "LC", "Q484170": "LC",
    "Q1496967": "LC", "Q41176": "LC", "Q483110": "LC",
    # OG: 기업·정당·팀·정부기관·단체·학교·그룹
    "Q4830453": "OG", "Q891723": "OG", "Q783794": "OG", "Q7278": "OG", "Q476028": "OG",
    "Q847017": "OG", "Q12973014": "OG", "Q327333": "OG", "Q163740": "OG", "Q31855": "OG",
    "Q3918": "OG", "Q215380": "OG", "Q2088357": "OG", "Q1194951": "OG",
    # AF: 작품·프로그램·제품·브랜드·서비스
    "Q11424": "AF", "Q5398426": "AF", "Q15416": "AF", "Q7366": "AF", "Q482994": "AF",
    "Q431289": "AF", "Q2424752": "AF", "Q7889": "AF", "Q571": "AF", "Q7397": "AF",
    "Q1004": "AF", "Q581714": "AF", "Q15709879": "AF",
    # EV: 선거·대회·시즌·축제·재난
    "Q40231": "EV", "Q13406554": "EV", "Q16510064": "EV", "Q27020041": "EV",
    "Q132241": "EV", "Q1656682": "EV", "Q3839081": "EV", "Q175331": "EV",
    # TM: 개념·통화·제도(빈출만 · 대부분은 보류→수동)
    "Q131723": "TM", "Q8142": "TM",
}


def map_type(entity: dict):
    """위키데이터 엔티티 → (타입 or '', 후보 P31 QID들). 서로 다른 타입 후보 충돌 시 보류."""
    claims = entity.get("claims") or {}
    p31 = _claim_qids(claims, "P31")
    tags = {WD_P31_TYPE[q] for q in p31 if q in WD_P31_TYPE}
    if not tags and "P625" in claims:                          # 좌표 보유 → 장소
        tags = {"LC"}
    if len(tags) == 1:
        return tags.pop(), p31
    return "", p31


def extract_attrs(typ: str, entity: dict) -> dict:
    """타입별 속성 추출. QID 참조값은 wd_labels 배치로 한국어 라벨 해석."""
    claims = entity.get("claims") or {}
    need = set()
    plan = {}
    def take(field, prop, first=True, current=False):
        qs = [_claim_qid_current(claims, prop)] if current else _claim_qids(claims, prop)
        qs = [q for q in qs if q]
        if qs:
            plan[field] = qs if not first else qs[:1]
            need.update(plan[field])
    if typ == "PS":
        take("gender", "P21")
        take("nationality", "P27")
        take("_occupations", "P106", first=False)
        for p in ("P54", "P108", "P102"):                      # 소속팀 → 고용주 → 정당
            take("affiliation", p, current=True)
            if "affiliation" in plan:
                break
    elif typ == "OG":
        take("country", "P17")
        take("industry", "P452")
        take("org_kind", "P31")
    elif typ == "LC":
        take("country", "P17")
        take("parent", "P131")
        take("loc_kind", "P31")
    elif typ == "AF":
        take("af_kind", "P31")
        for p in ("P170", "P178", "P176", "P175", "P123"):     # 창작자·개발사·제조사·아티스트·발행처
            take("creator", p)
            if "creator" in plan:
                break
    elif typ == "EV":
        take("ev_kind", "P31")
        take("location", "P276")
    labels = wd_labels(need) if need else {}
    attrs = {}
    for field, qs in plan.items():
        vals = [labels.get(q, "") for q in qs]
        vals = [v for v in vals if v]
        if not vals:
            continue
        if field == "_occupations":
            attrs["occupation_detail"] = ", ".join(vals[:5])
            attrs["occupation"] = snap_occupation(vals)
        else:
            attrs[field] = vals[0]
    if typ == "PS":
        y = _claim_year(claims, "P569")
        if y:
            attrs["birth_year"] = y
    elif typ == "AF":
        y = _claim_year(claims, "P577")
        if y:
            attrs["release_year"] = y
    elif typ == "EV":
        for field, prop in (("start_date", "P580"), ("end_date", "P582")):
            for s in claims.get(prop, []) or []:
                try:
                    attrs[field] = s["mainsnak"]["datavalue"]["value"]["time"].lstrip("+")[:10]
                    break
                except (KeyError, TypeError):
                    pass
    return attrs


# ── 나무위키 폴백(POC 전용) ──────────────────────────────────────────────
# ⚠ 라이선스: 나무위키 콘텐츠는 CC BY-NC-SA(비영리·동일조건). 이 앱은 체계 검증용
#   POC 이고 사전 데이터는 통검 DB 연동 시 전량 폐기 전제라 한정 사용한다(2026-07-14 협의).
#   운영(상용) 전환·데이터 이전 금지 · 통검 연동 시 이 블록은 제거 대상.
#   위키데이터 미스인 신규 개체당 1회 조회(저볼륨)로 제한 · PRISM_ENTDICT_NAMU=0 으로 끔.
NAMU_URL = "https://namu.wiki/w/"
_BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _http_text(url: str) -> str:
    """HTML 조회 심(seam) · 테스트는 이 함수를 대체한다."""
    req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA})
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.read().decode("utf-8", "replace")


def namu_fetch(name: str) -> str:
    """문서 HTML. 미존재(404)·접근 불가는 빈 문자열."""
    try:
        return _http_text(NAMU_URL + urllib.parse.quote(normalize_name(name)))
    except (urllib.error.URLError, OSError, ValueError):
        return ""


def _namu_categories(html: str) -> list:
    cats = []
    for href in _re.findall(r'href="/w/([^"#?]+)"', html or ""):
        t = urllib.parse.unquote(href)
        if t.startswith("분류:"):
            cats.append(t[3:])
    return cats


def _namu_field(html: str, label: str) -> str:
    """인포박스 셀: <strong>라벨</strong>…</td><td>값</td> 패턴 · 태그·각주 제거."""
    m = _re.search(r"<strong[^>]*>" + _re.escape(label) + r"</strong>.*?</td>\s*<td[^>]*>(.*?)</td>",
                   html or "", _re.S)
    if not m:
        return ""
    txt = _re.sub(r"<[^>]+>", " ", m.group(1))
    txt = _re.sub(r"\[[^\]]{0,20}\]", "", txt)             # 각주 [1]·[주석]
    return " ".join(txt.split())[:60]


# 분류(카테고리) 키워드 → 타입. 서로 다른 타입 후보 충돌 시 보류(위키데이터 P31 과 동일 정책).
_NAMU_TYPE_RULES = [
    ("PS", ("선수", "가수", "배우", "정치인", "기업인", "방송인", "유튜버", "인터넷 방송인",
            "코미디언", "래퍼", "아이돌", "모델", "성우", "작가", "언론인", "교수")),
    ("OG", ("기업", "구단", "정당", "정부기관", "단체", "학교", "협회", "그룹", "팀", "기획사")),
    ("LC", ("행정구역", "도시", "지역", "섬", "관광지")),
    ("AF", ("영화", "드라마", "예능", "프로그램", "비디오 게임", "음반", "노래", "웹툰", "소설",
            "브랜드", "애플리케이션", "소프트웨어")),
    ("EV", ("스포츠 대회", "올림픽", "선거", "사건 사고", "축제", "시상식")),
]


# 관계성 분류(개체 자신이 아닌 출신·가족·이력 표시)는 타입 판정에서 제외 — '○○학교 출신'이
# 학교(OG)로, '권투 선수 자녀'가 선수(PS)로 오인돼 충돌·오판정을 만든다.
_NAMU_REL_CATS = ("출신", "자녀", "데뷔", "출생", "친족", "가족", "부모", "형제")


def namu_extract(html: str):
    """나무위키 문서 → (타입 or '', attrs, 분류 리스트). 동음이의 문서는 (None, …) = 보류."""
    cats = _namu_categories(html)
    if any("동음이의" in c for c in cats):
        return None, {}, cats                              # 동명이인 문서 → 자동 결정 없이 보류
    jcats = [c for c in cats if not any(x in c for x in _NAMU_REL_CATS)]
    # 타입 = 분류 다수결. 인물 문서에도 '올림픽 참가'(EV)·'아시안 게임'(AF) 류 분류가 섞이므로
    # 단일 태그 요구 대신 '명확한 다수(1위 > 2위)'만 자동 부여, 동률·근소는 보류(모호=미부여 정신 유지).
    scores = {}
    for t, kws in _NAMU_TYPE_RULES:
        n = sum(1 for c in jcats if any(k in c for k in kws))
        if n:
            scores[t] = n
    typ = ""
    if scores:
        top = sorted(scores.items(), key=lambda x: -x[1])
        if len(top) == 1 or top[0][1] > top[1][1]:
            typ = top[0][0]
    attrs = {}
    nat = _namu_field(html, "국적")
    if nat:
        attrs["nationality"] = nat.split()[0]
    birth = _namu_field(html, "출생") or _namu_field(html, "설립")
    m = _re.search(r"(19|20)\d{2}", birth)
    if m and typ == "PS":
        attrs["birth_year"] = m.group(0)
    aff = _namu_field(html, "소속") or _namu_field(html, "소속사") or _namu_field(html, "소속 구단")
    if aff and typ == "PS":
        attrs["affiliation"] = aff
    occ_src = [_namu_field(html, "직업"), _namu_field(html, "종목")] + jcats
    if typ == "PS":
        grp = snap_occupation([s for s in occ_src if s])
        if grp:
            attrs["occupation"] = grp
        detail = _namu_field(html, "직업") or _namu_field(html, "종목")
        if detail:
            attrs["occupation_detail"] = detail
        if any("여자" in c or "여성" in c for c in jcats):
            attrs["gender"] = "여성"
        elif any("남자" in c or "남성" in c for c in jcats):
            attrs["gender"] = "남성"
    return typ, attrs, cats


def _namu_try(e: dict):
    """나무위키 조회 결과: ("hit", 타입, 속성) | ("ambiguous", 분류들) | None(게이트 꺼짐·미존재·빈약)."""
    if os.environ.get("PRISM_ENTDICT_NAMU", "1") != "1":
        return None
    html = namu_fetch(e["name"])
    if not html or "<title>" not in html:
        return None
    typ, attrs_new, cats = namu_extract(html)
    if typ is None:
        return ("ambiguous", cats)
    if not typ and not attrs_new:
        return None                                        # 아무것도 못 얻음 → 다음 소스로
    return ("hit", typ, attrs_new)


def _apply_source(store, e: dict, am: dict, now: float, source: str,
                  typ: str, attrs_new: dict, ext_key: str, ext_val: str, extra=None) -> dict:
    """보강 결과 반영(소스 공통): 수동 확정(confirmed) 필드·타입은 덮어쓰지 않는다.
    자동(auto) 값은 재보강 시 최신 소스가 갱신한다(나무위키 1순위)."""
    attrs = dict(e.get("attrs") or {})
    for k, v in attrs_new.items():
        if (am.get(k) or {}).get("status") == "confirmed":
            continue
        attrs[k] = v
        am[k] = {"source": source, "status": "auto"}
    ext = dict(e.get("external_ids") or {})
    ext[ext_key] = ext_val
    fields = {"attrs": attrs, "attr_meta": am, "external_ids": ext, "updated_at": now}
    if typ and (am.get("type") or {}).get("status") != "confirmed":
        fields["type"] = typ
        am["type"] = {"source": source, "status": "auto"}
        fields["status"] = "active"
    elif e.get("status") == "unlisted":
        fields["status"] = "pending"                       # 소스 히트 → 미등재 해제(타입은 보류 유지)
    am["_enrich"] = {"source": source, "result": "hit", "ts": now, **(extra or {})}
    store.ent_update(e["entity_id"], fields)
    return fields


def enrich_entity(store, entity_id: str) -> dict:
    """개체 1건 보강: ① 나무위키(한국 커버리지·랭킹 · POC 전용) → ② 위키데이터 폴백.
    나무위키 동음이의면 위키데이터로 시도, 그것도 미해소면 보류(동음이의 후보 기록).
    수동 확정(confirmed) 필드·타입은 어느 소스도 덮어쓰지 않는다.
    미히트도 기록(_enrich)해 '조회했으나 미등재'와 '미조회'를 구분한다."""
    e = store.ent_get(entity_id)
    if not e:
        return {"ok": False, "error": "개체 없음"}
    am = dict(e.get("attr_meta") or {})
    now = time.time()
    nr = _namu_try(e)
    namu_attrs = {}
    if nr and nr[0] == "hit":
        _, typ, attrs_new = nr
        fields = _apply_source(store, e, am, now, "namuwiki", typ, attrs_new, "namuwiki", e["name"])
        if fields.get("type") or (e.get("type") or ""):
            return {"ok": True, "matched": True, "source": "namuwiki",
                    "type": fields.get("type", e.get("type") or "")}
        # 타입 미판정(속성만 히트) → 위키데이터로 타입(P31)만 이어서 보강.
        # 여기서 조기 반환하면 status 가 pending 에 고정돼 타입 의존 토픽 조건에서 영영 누락된다.
        namu_attrs = attrs_new
    # ② 위키데이터 폴백(나무위키 미스·동음이의·타입 미판정)
    try:
        hit = wd_search(e["name"])
    except (urllib.error.URLError, OSError, ValueError) as ex:
        return {"ok": False, "error": f"위키데이터 조회 실패: {str(ex)[:120]}"}
    if hit:
        qid = hit["id"]
        ent = wd_entity(qid)
        typ, p31 = map_type(ent)
        new_attrs = extract_attrs(typ or e.get("type") or "", ent)
        # 나무위키가 같은 보강 사이클에서 채운 속성은 유지(소스 우선순위: 나무위키 1순위)
        new_attrs = {k: v for k, v in new_attrs.items() if k not in namu_attrs}
        if not typ and not (e.get("type") or ""):
            am["_type_candidates"] = p31[:5]               # 충돌·미판정 → 보류 + 후보 보존
        fields = _apply_source(store, e, am, now, "wikidata", typ, new_attrs,
                               "wikidata", qid, extra={"qid": qid})
        # 위키데이터 정식 라벨(ko)도 별칭으로 등재 → 다음 적재부터 표기 변형 흡수
        label = hit.get("label") or ""
        if label and normalize_name(label) != e["name"]:
            store.ent_alias_add(normalize_name(label), entity_id)
        return {"ok": True, "matched": True, "source": "wikidata", "qid": qid,
                "type": fields.get("type", e.get("type") or "")}
    if nr and nr[0] == "hit":                              # 나무위키 속성 히트 + 위키데이터 미스 → 보류 유지(미등재 강등 금지)
        return {"ok": True, "matched": True, "source": "namuwiki", "type": e.get("type") or ""}
    if nr and nr[0] == "ambiguous":                        # 둘 다 미해소 + 동음이의 → 보류(수동 확정 대기)
        am["_type_candidates"] = nr[1][:5]
        am["_enrich"] = {"source": "namuwiki", "result": "ambiguous", "ts": now}
        store.ent_update(entity_id, {"attr_meta": am, "updated_at": now})
        return {"ok": True, "matched": False, "ambiguous": True}
    # 미등재(unlisted): 두 소스 모두 미스 = 복합명사구·개념어(TM 후보)일 가능성 —
    # 개체 자체는 사전에 남기되(가치 있음) 보류 통계·기본 목록·재보강 대상에서 분리한다.
    am["_enrich"] = {"source": "namuwiki+wikidata", "result": "miss", "ts": now}
    fields = {"attr_meta": am, "updated_at": now}
    if e.get("status") == "pending":                       # 확정(active)·수동 타입은 강등하지 않음
        fields["status"] = "unlisted"
    store.ent_update(entity_id, fields)
    return {"ok": True, "matched": False}


ENRICH_DELAY = 0.4                                   # 개체 간 지연(초) · 위키데이터 429 회피(예의 호출)


def enrich_many(store, entity_ids, limit: int = 200) -> dict:
    """여러 건 순차 보강(적재 후 백그라운드·UI 일괄 버튼 공용). 실패는 건너뛰고 집계만."""
    hit = miss = fail = 0
    for i, eid in enumerate(list(entity_ids)[:limit]):
        if i:
            time.sleep(ENRICH_DELAY)
        try:
            r = enrich_entity(store, eid)
        except Exception:
            fail += 1
            continue
        if not r.get("ok"):
            fail += 1
        elif r.get("matched"):
            hit += 1
        else:
            miss += 1
    return {"hit": hit, "miss": miss, "fail": fail}


# ── 토픽 연동: 콘텐츠 → 소속 개체 속성 인덱스 ──────────────────────────────
def attr_index(store, team="") -> dict:
    """{content_hash: [개체 속성 dict(타입 포함), …]} · 토픽 조건(엔티티 속성 축) 매칭 원천.
    보류 개체도 포함(타입 의존 조건만 자연히 비활성 · 문서의 보류 정책)."""
    try:
        return store.ent_attr_index(team=team)
    except Exception as e:
        # 조용한 {} 는 엔티티 속성 토픽이 소리 없이 0건 매칭이 되는 원인 — 최소한 로그는 남긴다
        print(f"[entdict] attr_index 실패 · 속성 조건 매칭 비활성: {e}")
        return {}


ALLOWED_EATTR_KEYS = ("type", "gender", "occupation", "nationality", "affiliation",
                      "org_kind", "country", "loc_kind", "af_kind", "ev_kind", "domain")


def parse_eattr(s: str):
    """토픽 조건 문자열 'key:value' → (key, value) 또는 None(비허용 키·형식 오류)."""
    if ":" not in str(s or ""):
        return None
    k, v = str(s).split(":", 1)
    k, v = k.strip(), v.strip()
    if k in ALLOWED_EATTR_KEYS and v:
        return (k, v)
    return None
