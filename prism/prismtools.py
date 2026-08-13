"""도구 계층 · 내부 검수 보조(트랙 A)와 외부 MCP(트랙 B)가 공유하는 단일 원천.

두 트랙은 앞단이 다르다. 트랙 A 는 프리즘 화면 안의 검수 보조라 이미 로그인한 세션을 쓰고,
트랙 B 는 외부 클라이언트가 파트너 키로 부른다. 그러나 **도구 함수는 한 벌이어야 한다** —
팀 스코프·응답 상한·골드 제외 같은 규칙이 두 군데로 갈라지면 반드시 어긋나고, 어긋난 쪽이
조용히 틀린 답을 낸다. 2026-08-11 감사에서 본 것이 정확히 그것이었다(`ent_trending` 과
`ent_attr_index` 가 형제인데 한쪽만 고쳐져 운영에서 0건이 나오고 있었다).

## 이 모듈이 지키는 규칙

1. **팀은 인자로 받되 호출자가 세션·키에서 해석한 값만 넘긴다.** 도구 사용자(모델·외부
   클라이언트)가 team 을 지정할 수 없다. 저장 계층은 team 이 falsy 면 필터를 생략해
   '전 팀' 으로 해석하므로(감사 H1), team 없이 부르는 경로를 만들지 않는다.
2. **응답에 상한이 있고 잘리면 말한다.** `truncated` 와 `total` 을 함께 싣는다.
   조용한 절단은 "다 봤다" 로 읽혀 판단을 그르친다(감사 P3).
3. **골드 문항은 어떤 경로로도 나가지 않는다.** 검수자 신뢰도를 재는 장치라 모델이 읽으면
   측정 대상이 사람이 아니라 모델이 된다. 큐와 검수 표 양쪽에 섞이므로 두 경로 다 거른다.
4. **정수 인자는 검증 후 클램프한다.** 예외 원문을 응답에 싣지 않는다(감사 H3).

serve 역참조(`_SV`)는 learnops·topicops 관례를 따른다.
"""
from __future__ import annotations

from . import dictionaries as D

_SV = None                                   # serve 주입(컴포지션 루트)

# 골드 문항(가상 검증 행) 식별 접두. 배정 라우트가 이미 같은 규칙으로 거른다(serve.py).
GOLD_PREFIXES = ("gold:", "goldf:")


# ── 공통 가드 ────────────────────────────────────────────────────────────────
def qint(value, default: int, lo: int, hi: int) -> int:
    """정수 인자 검증 + 클램프. 못 읽으면 기본값으로 조용히 수렴한다(예외를 올리지 않는다).

    도구 인자는 모델이 채우므로 형식 오류가 일상적이다. 500 을 내면 모델이 고칠 단서를
    못 얻고, 예외 원문을 실으면 내부 구현이 샌다."""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return default
    return max(lo, min(hi, n))


def is_gold(item) -> bool:
    h = item.get("hash") if isinstance(item, dict) else item
    return str(h or "").startswith(GOLD_PREFIXES)


def strip_gold(items) -> list:
    """골드 문항 제거. 목록에서 빼는 것이지 큐에서 없애는 것이 아니다 —
    검수자는 화면에서 그대로 만나야 측정이 유지된다."""
    return [x for x in (items or []) if not is_gold(x)]


def cap(rows, limit: int) -> tuple:
    """(잘린 목록, truncated, total). 상한을 넘으면 앞에서 limit 개만 남긴다."""
    rows = list(rows or [])
    total = len(rows)
    if total <= limit:
        return rows, False, total
    return rows[:limit], True, total


def envelope(items, limit: int, **extra) -> dict:
    """도구 응답 공통 봉투. 잘림 여부를 항상 밝힌다."""
    rows, truncated, total = cap(items, limit)
    out = {"items": rows, "total": total, "truncated": truncated}
    out.update(extra)
    return out


def need_team(team):
    """팀이 없으면 도구를 실행하지 않는다.

    저장 계층은 team 이 falsy 면 필터를 생략해 전 팀을 돌려준다. 도구 계층에서 막지 않으면
    팀 미소속 호출이 전 팀 데이터를 읽는다(감사 H1 과 같은 실패)."""
    t = (team or "").strip() if isinstance(team, str) else team
    if not t:
        return {"error": "팀 정보가 없어 조회할 수 없습니다"}
    return None


# ── 도구: 분류 체계 ──────────────────────────────────────────────────────────
TAXONOMY_KINDS = ("category", "intent", "reason", "intake_policy", "legal_type")


def get_taxonomy(kind: str, service: str = "", team=None) -> dict:
    """프리즘이 쓰는 분류 체계와 허용값. 외부(트랙 B)에 가장 값어치 있는 도구다.

    intent 는 서비스마다 후보가 다르므로 service 를 받는다(미지정이면 범용만).
    정의문은 INTENT_VALUE_DEFS 단일 원천 — 검수 화면·추출 프롬프트와 같은 문장이다."""
    kind = (kind or "").strip()
    if kind not in TAXONOMY_KINDS:
        return {"error": f"kind 는 {' · '.join(TAXONOMY_KINDS)} 중 하나입니다"}

    if kind == "category":
        vals = [{"key": t, "label": t, "desc": getattr(D, "IAB_TIER1_DESC", {}).get(t, ""),
                 "children": list((D.CONTENT_CATEGORY_TIER2.get(t) or []))} for t in D.IAB_TIER1]
    elif kind == "intent":
        names = (D.intent_categories_for(service) if service
                 else list(D.INTENT_CATEGORIES_UNIVERSAL) + list(D.INTENT_FORM_UNIVERSAL))
        vals = [{"key": n, "label": n, "desc": D.INTENT_VALUE_DEFS.get(n, "")} for n in names]
    elif kind == "reason":
        names = getattr(D, "QUALITY_META_NAMES", {})
        vals = [{"key": k, "label": names.get(k, k), "desc": v if isinstance(v, str) else ""}
                for k, v in D.QUALITY_METAS.items()]
    elif kind == "intake_policy":
        vals = [{"key": k, "label": k, "desc": _flat(v)} for k, v in (D.INTAKE_POLICY or {}).items()]
    else:
        vals = [{"key": k, "label": k, "desc": _flat(v)} for k, v in (D.LEGAL_HARM_TYPES or {}).items()]
    return {"kind": kind, "service": service or "", "values": vals, "total": len(vals)}


def _flat(v) -> str:
    """dict-of-dict 사전 값을 한 줄 설명으로 편다(사전 구조가 그대로 새지 않게)."""
    if isinstance(v, dict):
        return " · ".join(f"{k}: {x}" for k, x in v.items() if x)
    if isinstance(v, (list, tuple)):
        return " · ".join(str(x) for x in v)
    return str(v or "")


# ── 도구: 엔티티 사전 ────────────────────────────────────────────────────────
ENTITY_LIMIT_DEFAULT, ENTITY_LIMIT_MAX = 10, 50


def lookup_entity(name: str, limit=None, team=None) -> dict:
    """엔티티 사전 조회(이름·별칭 부분일치).

    한글 NFD 입력도 찾는다 — 저장 payload 는 재실행 해시 안정성 때문에 원본을 되박아
    맥OS 발 파일이 NFD 로 들어오는데, 조회 정규화가 NFC 라 종전에는 미스했다(감사 T5).

    ⚠️ **등장 수(콘텐츠 빈도)를 여기 싣지 말 것.** 2026-08-12 트랙 B 제보로 확인: 종전
    `seen` 필드는 `r.get("n") or r.get("count")` 를 읽었는데 `ent_list` 가 다는 키는
    `n_contents` 라 값이 항상 0 이었다(죽은 필드). 그런데 키 이름만 맞추면 **그 순간 교차 팀
    유출이 생긴다** — `ent_list` 는 시그니처에 team 인자가 없고 카운트 쿼리에도 team 필터가
    없어 무조건 전역이다(형제인 `ent_trending` 은 team 을 받는 비대칭 · 이 모듈 독스트링이
    경고한 바로 그 모양). 죽은 채로 우연히 유출을 막고 있던 필드라 되살리는 대신 뺐다.
    파트너 계약에 항상 0 인 필드를 두면 빈도 신호로 읽혀 조용히 틀린다.
    되살리려면 순서가 있다: ① `ent_list` 에 team 축 추가(store·supastore 양쪽 · 카운트
    쿼리 포함) → ② 그 다음에야 여기에 필드 복원. ① 없이 ② 만 하면 감사 H1 재현이다.

    개체 레지스트리 자체(이름·별칭·타입)가 전역인 것은 결함이 아니다 — `entities` 테이블에
    team 컬럼이 없는 공용 참조 데이터다. 팀에 종속되는 것은 '어느 콘텐츠에 몇 번 나왔나' 뿐이다."""
    q = str(name or "").strip()
    if not q:
        return {"error": "찾을 이름을 넣어 주세요", "items": [], "total": 0, "truncated": False}
    lim = qint(limit, ENTITY_LIMIT_DEFAULT, 1, ENTITY_LIMIT_MAX)
    st = _SV.get_store() if _SV else None
    if not (st and hasattr(st, "ent_list")):
        return {"error": "사전이 준비되지 않았습니다", "items": [], "total": 0, "truncated": False}
    try:
        rows = st.ent_list(q=q, limit=ENTITY_LIMIT_MAX) or []
    except Exception:
        return {"error": "사전을 읽지 못했습니다", "items": [], "total": 0, "truncated": False}
    items = [{"entity_id": r.get("entity_id", ""), "name": r.get("name", ""),
              "type": r.get("type", ""), "status": r.get("status", ""),
              "aliases": list(r.get("aliases") or [])[:8]} for r in rows]
    return envelope(items, lim, query=q)


# ── 도구: 정책 예시 ──────────────────────────────────────────────────────────
EXAMPLE_KINDS = ("intent", "category")
EXAMPLE_LIMIT_DEFAULT, EXAMPLE_LIMIT_MAX = 20, 60

# 예시가 **초안**이라는 사실은 응답에 실려야 한다. 사전 코드에 그렇게 적혀 있고
# (`INTENT_EXAMPLES` = "초안 · 부분" · `TIER2_DEFS` = "예시: 초안(팀 확정 대상)"),
# 확정 정책으로 읽히면 검수자가 틀린 기준으로 판정한다. 그 판정은 되돌릴 수도 없다.
# 검수 결과는 그대로 품질 측정값으로 쓰이기 때문이다. 그래서 봉투와 항목 양쪽에 표시한다
# (봉투만 표시하면 항목만 뽑아 쓰는 화면에서 표시가 떨어져 나간다).
EXAMPLES_DRAFT_NOTE = ("예시는 초안입니다(팀 확정 전) · 확정 정책이 아니므로 판단 기준으로 삼지 마세요 · "
                       "정의(desc)는 확정 사전입니다")
NO_EXAMPLE = "등록된 예시가 없습니다"


def _tier2_key(v: str) -> str:
    """카테고리 값 → Tier2 사전 키. 'Tier1 / Tier2' 경로·개명 구표기·한글 표시명을 흡수한다.

    저장된 값은 영문 경로('News and Politics / Politics')이고 화면은 한글로 그리므로, 둘 중
    무엇이 들어와도 같은 항목을 찾아야 한다. 못 찾으면 빈 문자열 = '모르는 값'(뒤에서 unknown
    으로 되돌려 준다). 비슷한 이름으로 갈음하지 않는다. 그 순간 없는 정책을 지어내는 것이다."""
    s = str(v or "").strip()
    if not s:
        return ""
    if s in D.TIER2_DEFS:
        return s
    last = s.split("/")[-1].strip()
    last = (getattr(D, "RENAMED_TIER2", {}) or {}).get(last, last)
    if last in D.TIER2_DEFS:
        return last
    for k, ko in (getattr(D, "TIER2_KO", {}) or {}).items():   # 한글 표시명 역인용
        if ko == last and k in D.TIER2_DEFS:
            return k
    return ""


def _ex_item(key: str, label: str, desc: str, example: str) -> dict:
    ex = str(example or "").strip()
    return {"key": key, "label": label or key, "desc": str(desc or ""),
            "example": ex, "has_example": bool(ex), "draft": True,
            "note": "" if ex else NO_EXAMPLE}


def get_examples(kind: str = "", values=None, service: str = "", limit=None, team=None) -> dict:
    """분류값의 **정책 예시**와 정의문. 검수 화면에서 사전·정책 탭으로 건너가는 왕복을 없앤다.

    원천은 사전의 예시 텍스트 하나뿐이다(`INTENT_EXAMPLES` · `TIER2_DEFS`). 도움말 표가 쓰는
    바로 그 값이라 화면과 도구가 갈릴 수 없다.

    ⚠️ **골든셋 실물 콘텐츠를 예시로 끌어오지 않는다.** 골드 문항은 골든셋에서 만들어지므로
    (`reviewops._inject_gold` 가 `get_golden` 만 돈다) 골든 콘텐츠를 예시로 보여 주는 것은
    골드의 답을 미리 보여 주는 것과 같다. 검수자가 예시에서 본 제목을 큐의 골드에서 알아보는
    순간 골드가 재려던 것이 사라진다. '실물이라 더 좋은 예시'라는 이유로도 열지 않는다.
    선례 도구가 골든 원본의 식별자를 지우는 것(GOLDEN_BLIND_FIELDS)과 같은 이유다.

    모르는 값은 `unknown` 으로 되돌려 준다. 비슷한 값으로 갈음하면 없는 정책이 생긴다."""
    kind = (kind or "").strip()
    if kind not in EXAMPLE_KINDS:
        return {"error": f"kind 는 {' · '.join(EXAMPLE_KINDS)} 중 하나입니다"}
    lim = qint(limit, EXAMPLE_LIMIT_DEFAULT, 1, EXAMPLE_LIMIT_MAX)
    if isinstance(values, (list, tuple)):
        asked = [str(v).strip() for v in values if str(v or "").strip()]
    else:
        asked = [str(values).strip()] if str(values or "").strip() else []

    items, unknown = [], []
    if kind == "intent":
        defs = D.INTENT_VALUE_DEFS
        pool = (D.intent_categories_for(service) if service
                else list(D.INTENT_CATEGORIES_UNIVERSAL) + list(D.INTENT_FORM_UNIVERSAL))
        keys = []
        for v in (asked or pool):
            # 특정 값을 콕 집어 물으면 서비스 후보 밖이어도 답한다(정의가 있는 값이면 실재한다).
            (keys if (v in pool or v in defs) else unknown).append(v)
        for k in dict.fromkeys(keys):
            items.append(_ex_item(k, k, defs.get(k, ""), D.INTENT_EXAMPLES.get(k, "")))
    else:
        allt = [t2 for lst in D.CONTENT_CATEGORY_TIER2.values() for t2 in lst]
        keys = []
        for v in (asked or allt):
            k = _tier2_key(v)
            (keys.append(k) if k else unknown.append(v))
        for k in dict.fromkeys(keys):
            desc, ex = (tuple(D.TIER2_DEFS.get(k) or ("", "")) + ("", ""))[:2]
            items.append(_ex_item(k, (getattr(D, "TIER2_KO", {}) or {}).get(k, k), desc, ex))

    return envelope(items, lim, kind=kind, service=service or "", draft=True,
                    draft_note=EXAMPLES_DRAFT_NOTE, unknown=unknown[:EXAMPLE_LIMIT_MAX])


# ── 도구 등록부 ──────────────────────────────────────────────────────────────
# scope: internal(트랙 A 전용) · external(트랙 B 전용) · both
# 각 앞단은 이 표에서 자기 scope 만 골라 노출한다. inputSchema 는 MCP tools/list 가 그대로
# 쓰고 내부 에이전트의 도구 정의로도 쓰인다 — 한 곳만 고치면 둘 다 바뀐다.
TOOLS = {
    "get_taxonomy": {
        "scope": "both",
        "title": "분류 체계 조회",
        "desc": "프리즘이 쓰는 분류 체계와 허용값을 준다. 카테고리·인텐트·품질 사유·인입 정책·법령 유형. "
                "인텐트는 서비스마다 후보가 다르므로 service 를 함께 넣으면 그 서비스 것만 준다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": list(TAXONOMY_KINDS),
                         "description": "보고 싶은 체계 하나"},
                "service": {"type": "string",
                            "description": "서비스명(뉴스·연예·스포츠·티스토리 등) · 인텐트일 때만 의미 있음"},
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
        "fn": get_taxonomy,
    },
    "lookup_entity": {
        "scope": "both",
        "title": "엔티티 사전 조회",
        "desc": "개체 사전에서 이름이나 별칭으로 찾는다. 표기가 갈린 개체를 하나로 모으는 데 쓴다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "찾을 이름(별칭도 된다)"},
                "limit": {"type": "integer", "minimum": 1, "maximum": ENTITY_LIMIT_MAX,
                          "description": f"가져올 수(기본 {ENTITY_LIMIT_DEFAULT} · 최대 {ENTITY_LIMIT_MAX})"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        "fn": lookup_entity,
    },
    "get_examples": {
        # scope=both 인 근거: 이 도구는 **콘텐츠를 읽지 않는다**. 인자에 해시가 없고 응답도
        # 팀 데이터와 무관한 공용 사전 문자열뿐이라, get_taxonomy 와 정확히 같은 부류다
        # (팀 강제는 call 이 공통으로 걸어 두므로 무팀 호출은 어차피 막힌다).
        # 외부에 여는 위험은 하나: 초안 예시가 확정 정책으로 읽히는 것. 그건 봉투와 항목
        # 양쪽의 draft 표시로 계약에 박아 막는다(파트너가 표시를 지우면 그건 파트너 책임).
        "scope": "both",
        "title": "정책 예시 조회",
        "desc": "분류값(인텐트 · 콘텐츠 카테고리 Tier2)의 정책 예시와 정의문을 준다. "
                "예시는 **초안**이라 확정 정책이 아니며 응답에 그 사실이 함께 실린다. "
                "등록된 예시가 없는 값은 없다고 답한다(지어내지 않는다).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": list(EXAMPLE_KINDS),
                         "description": "intent=인텐트 · category=콘텐츠 카테고리(Tier2)"},
                "values": {"type": "array", "items": {"type": "string"},
                           "description": "궁금한 값들(비우면 kind·service 의 후보 전체)"},
                "service": {"type": "string",
                            "description": "서비스명 · 인텐트일 때만 의미 있음(그 서비스 후보로 좁힌다)"},
                "limit": {"type": "integer", "minimum": 1, "maximum": EXAMPLE_LIMIT_MAX,
                          "description": f"가져올 수(기본 {EXAMPLE_LIMIT_DEFAULT} · 최대 {EXAMPLE_LIMIT_MAX})"},
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
        "fn": get_examples,
    },
}


def tools_for(scope: str) -> dict:
    """앞단이 노출할 도구만 고른다. scope 는 'internal' 또는 'external'."""
    return {k: v for k, v in TOOLS.items() if v["scope"] in (scope, "both")}


def call(name: str, args: dict, team=None, registry=None) -> dict:
    """도구 실행 공통 진입점.

    team 은 **호출자가 세션·키에서 해석한 값**이다. args 에 team 이 들어와도 무시한다 —
    도구 사용자가 팀을 지정할 수 있으면 그 자체가 교차 팀 접근 통로가 된다.

    registry 를 주면 그 등록부에서 찾는다(앞단 전용 묶음 · reviewassist). 도구 목록만 다를 뿐
    팀 강제·인자 필터·예외 은닉은 여기 한 벌을 쓴다 — 디스패치 규칙이 갈리면 한쪽이 조용히
    느슨해지고, 느슨해진 쪽이 팀 밖 데이터를 흘린다."""
    spec = (TOOLS if registry is None else registry).get((name or "").strip())
    if not spec:
        return {"error": f"모르는 도구입니다: {name or '(없음)'}"}
    blocked = need_team(team)
    if blocked:
        return blocked
    kw = {k: v for k, v in (args or {}).items()
          if k in (spec["inputSchema"].get("properties") or {})}
    try:
        return spec["fn"](team=team, **kw)
    except TypeError:
        return {"error": "인자가 도구 정의와 맞지 않습니다"}
    except Exception:
        # 상세는 서버 로그로. 응답에 예외 원문을 싣지 않는다(내부 구현 노출 차단).
        return {"error": "도구를 실행하지 못했습니다"}
