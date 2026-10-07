"""핵심 키워드 / 문장 재가공 시험 (실험실 · 운영자 전용 · 2026-10-07).

1차 추출 뒤 두 호출을 순서대로 탄다: ① 핵심 키워드(구독 키워드 3개) → ② 핵심 문장(핵심 키워드 + 메타로 재구축).
**입력은 발행된 메타(리드문·엔티티·인텐트·카테고리)뿐이다** · 제목·본문은 넣지 않고, 근거 확인도 메타 안에서 한다(사용자 2026-10-07).
키워드 유형: 조합형(사건을 함축하는 복합 명사구 · 우선) / 단일형(엔티티 또는 단일 용어).
호출마다 모델·프롬프트(규칙부)를 실험실 › 모델·프롬프트 화면에서 고쳐 저장한다(reports kind=CONFIG_KIND · 팀 단위).
출력 형식(JSON 스키마)은 코드가 항상 프롬프트 뒤에 붙인다 · 규칙을 고쳐도 결과 해석이 깨지지 않게.

왜: 엔티티를 서비스(키워드)로 쓰려면 '본문에 있는 이름인가'를 넘어 '이 콘텐츠를 대표하나'를 골라야 한다.
운영 정답셋 실측(875건): 검수 전 초안과 정답 엔티티가 완전히 같은 건 87% · 초안 엔티티 97%가 정답에 남음 ·
확신도(entconf · 제목·리드문·첫 문단·빈도 규칙) 판별력 AUC 0.64 로 모델 순서(0.64)와 같다.
→ 확신도 상위 3개는 사실상 모델이 앞에 낸 3개이고, 지금 정답셋으로는 키워드 품질을 잴 수 없다.

그래서 1차 추출 뒤 엔티티만 다시 보는 호출을 하나 더 둔다(이 모듈):
  · 1차 엔티티마다 keep(키워드 후보) · canonical(사전 정식명 · 사전에 없으면 원문 표기) · type · relevance(0~100)
  · keywords 정확히 3개(후보가 모자라면 있는 만큼) · 1차 목록 밖 이름 생성 금지(코드에서 다시 막는다)
키워드 구독 서비스 기준(사용자 2026-10-07): 구독 키워드는 고유명만으로 모자라 주제어(예: 금리 인하)도 받는다
  · kind=entity: 1차 엔티티에서 고르고 사전 정식명으로 맞춤(구독 매칭이 안정적)
  · kind=concept: 엔티티 목록 밖이어도 되지만 제목·리드문·본문에 실제로 나오는 말만(지어낸 말 차단 · 코드에서 확인)
화면은 지금 방식(확신도 상위 3개)과 나란히 보여 주고, 검수자가 고른 쪽을 events(kind=entkw_vote)에 남긴다
→ 키워드 정답 라벨이 쌓이면 그때 두 방식을 정량 비교한다.
# ponytail: 일괄 결과는 메모리 보관(재배포 시 사라짐) · 투표만 영속 · 운영 반영 시 결과도 적재
"""
from __future__ import annotations

import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import entconf as EC
from . import meta_contract as MC

_SV = None                                   # serve 주입(learnops 관례)

WORKERS = 4
TYPES = ("PS", "OG", "LC", "AF", "EV", "TM")
PICKS = ("refined", "base", "both", "neither")

KW_RULES = """너는 콘텐츠의 발행된 메타만 보고 구독 서비스용 핵심 키워드를 만드는 편집자다.
입력: 리드문 · 엔티티 목록(발행 순서 · 사전 정식명이 있으면 함께) · 인텐트 · 카테고리. 원문 제목·본문은 주어지지 않는다.

# 할 일
1. 엔티티를 하나씩 판정한다.
   - keep: 핵심 키워드 재료로 쓸 수 있으면 true
   - canonical: 사전 정식명이 주어졌으면 그 표기, 없으면 원래 이름 그대로 (새 이름을 만들지 않는다)
   - type: PS(인물) · OG(기관·조직·브랜드) · LC(지역·장소) · AF(작품·제품) · EV(사건·행사) · TM(용어·개념) 중 하나
   - relevance: 0~100 · 이 콘텐츠의 핵심 주제를 대표하는 정도
2. keywords: 구독할 만한 핵심 키워드 정확히 3개. 유형은 둘이다.
   - 조합형(combo): 이 콘텐츠의 사건을 핵심적으로 함축하는 복합 명사구 · 엔티티 1개 이상 + 리드문에 나오는 말로 만든다
     (예: 한국은행 기준금리 인하 · 강호필 내란 혐의 소환 · 스마일게이트 미래시 사전예약)
   - 단일형(single): 엔티티 하나(canonical) 또는 리드문에 그대로 나오는 단일 용어
   - **조합형을 먼저** 둔다. 사건이 뚜렷하면 조합형 1~2개 + 단일형으로 채우고, 사건이 없는 글(맛집·후기 등)은 단일형만 써도 된다.

# 키워드 기준
- 조합형은 2~5어절 · 30자 이하 명사구 · 동사로 끝내지 않는다(인하했다 ✗ → 인하 ✓) · 조사를 붙이지 않는다.
- 조합형의 모든 어절은 엔티티·리드문·인텐트·카테고리에 나오는 말이어야 한다. 메타에 없는 말을 지어내지 않는다.
- 단일형은 구독할 만큼 구체적인 대상을 고른다. 고유명사가 일반명사보다 먼저다.
- 빼는 것: 기자·작성자·출처 매체명, 서비스명(다음·티스토리·카페 등), 너무 넓은 말(정부·시장·관계자·경제·사회·이슈).
- 세 키워드는 서로 겹치지 않게 한다. 조합형에 이미 든 엔티티를 단일형으로 다시 쓰는 것은 그 엔티티가 따로 구독할 가치가 클 때만."""

KW_SCHEMA = """# 출력 (JSON 한 개만 · 이 형식은 고정)
{"entities": [{"name": string, "canonical": string, "type": string, "relevance": number, "keep": boolean}],
 "keywords": [{"text": string, "kind": "combo" | "single"}]}"""

SENT_RULES = """너는 핵심 키워드와 발행된 메타만으로 구독자에게 알릴 핵심 문장 1개를 다시 쓰는 편집자다.
입력: 핵심 키워드(조합형·단일형) · 리드문 · 엔티티 · 인텐트 · 카테고리. 원문 제목·본문은 주어지지 않는다.

# 기준
- 조합형 핵심 키워드가 있으면 그 사건을 문장의 중심에 둔다. 없으면 단일형 키워드와 리드문으로 쓴다.
- 입력 메타에 있는 사실만 쓴다. 메타에 없는 수치·날짜·인용을 만들지 않는다. 과장·추측·평가(최고·충격·반드시)는 넣지 않는다.
- 핵심 대상(인물·기관·작품 등)을 이름으로 넣는다. 대명사로 시작하지 않는다.
- 인텐트에 맞는 말투를 쓴다(속보·사건은 사실 전달 · 후기·리뷰는 경험 요약 · 실용 정보는 무엇을 알 수 있는지).
- 40~80자 · 평서문 한 문장 · 따옴표·이모지·해시태그를 쓰지 않는다."""

SENT_SCHEMA = """# 출력 (JSON 한 개만 · 이 형식은 고정)
{"sentence": string}"""

SENT_MIN, SENT_MAX = 10, 120                 # 검증 범위(규칙의 40~80자보다 넓게 · 벗어나면 실패로 표시)
PROMPT_MAX = 8000
CALLS = ("keyword", "sentence")
DEFAULT_RULES = {"keyword": KW_RULES, "sentence": SENT_RULES}
SCHEMAS = {"keyword": KW_SCHEMA, "sentence": SENT_SCHEMA}
CONFIG_KIND = "lab_core_config"


def get_config(team=None) -> dict:
    """{keyword:{model, rules, custom}, sentence:{...}, schemas} · 저장값이 없으면 기본 규칙."""
    saved = (_SV._report_get(CONFIG_KIND, team, {}) if _SV else {}) or {}
    out = {}
    for c in CALLS:
        v = saved.get(c) or {}
        rules = str(v.get("rules") or "").strip()
        out[c] = {"model": str(v.get("model") or ""), "rules": rules or DEFAULT_RULES[c], "custom": bool(rules)}
    return {**out, "schemas": SCHEMAS, "defaults": DEFAULT_RULES}


def save_config(body: dict, team=None) -> dict:
    """호출별 모델·규칙 저장 · 규칙이 비었거나 기본과 같으면 기본으로 되돌림."""
    cur = (_SV._report_get(CONFIG_KIND, team, {}) or {}) if _SV else {}
    for c in CALLS:
        v = (body or {}).get(c)
        if not isinstance(v, dict):
            continue
        rules = str(v.get("rules") or "").strip()
        if len(rules) > PROMPT_MAX:
            return {"ok": False, "error": f"프롬프트가 너무 깁니다({PROMPT_MAX}자 이하)"}
        if rules == DEFAULT_RULES[c].strip():
            rules = ""
        cur[c] = {"model": str(v.get("model") or "")[:120], "rules": rules}
    _SV._report_save(CONFIG_KIND, cur, team)
    return {"ok": True, **get_config(team)}


def _system(call: str, cfg: dict) -> str:
    return (cfg.get(call) or {}).get("rules", DEFAULT_RULES[call]).strip() + "\n\n" + SCHEMAS[call]


def validate_sentence(obj) -> str:
    if not isinstance(obj, dict):
        raise ValueError("JSON 객체가 아닙니다")
    t = " ".join(str(obj.get("sentence") or "").split()).strip().strip('"“”')
    if not (SENT_MIN <= len(t) <= SENT_MAX):
        raise ValueError(f"핵심 문장 길이가 범위를 벗어났습니다({len(t)}자)")
    return t


def baseline(item_meta: dict, content: dict) -> list:
    """지금 방식: 확신도 상위 3개(같으면 모델 순서) · [{name, conf}]."""
    sc = EC.scored_entities(item_meta or {}, {"title": (content or {}).get("title"), "body": (content or {}).get("body")})
    return sorted(sc, key=lambda s: -s["conf"])[:3] if sc else []


def _metas(item_meta: dict) -> dict:
    """발행 메타 묶음(입력·근거 확인 공용) · 제목·본문은 넣지 않는다."""
    im = item_meta or {}
    return {"리드문": str(im.get("summary") or ""), "엔티티": MC.entity_names(im.get("entities")),
            "인텐트": [str(x) for x in (im.get("intent") or []) if x],
            "카테고리": MC.category_paths(im.get("content_category")) or []}


def _meta_text(item_meta: dict) -> str:
    m = _metas(item_meta)
    return " ".join([m["리드문"], *m["엔티티"], *m["인텐트"], *m["카테고리"]])


def _payload(item_meta: dict, canon: dict) -> str:
    m = _metas(item_meta)
    ents = [{"이름": n, **({"사전정식명": canon[n]} if canon.get(n) else {})} for n in m["엔티티"]]
    return json.dumps({"리드문": m["리드문"], "엔티티": ents, "인텐트": m["인텐트"], "카테고리": m["카테고리"]}, ensure_ascii=False)


def validate(obj, names: list, canon: dict, text: str = "") -> dict:
    """모델 출력 검증: 1차 목록 밖 이름·사전에 없는 정식명 변경은 버린다 · 키워드는 메타(text) 근거가 있어야 한다.
    조합형 = 2~5어절·엔티티 포함·모든 어절이 메타에 있음 · 단일형 = 엔티티 정식명 또는 메타에 그대로 있는 용어 · 조합형 먼저.
    반환 {entities, keywords:[{text, kind}], dropped, renamed}."""
    if not isinstance(obj, dict):
        raise ValueError("JSON 객체가 아닙니다")
    allowed = {n: {n, canon.get(n) or n} for n in names}
    by_name, ents = {}, []
    for e in obj.get("entities") or []:
        if not isinstance(e, dict) or e.get("name") not in allowed:
            continue
        n = e["name"]
        cn = str(e.get("canonical") or n)
        cn = cn if cn in allowed[n] else n                    # 사전 근거 없는 개명은 받지 않는다
        try:
            rel = max(0, min(100, int(float(e.get("relevance")))))
        except (TypeError, ValueError):
            rel = 0
        row = {"name": n, "canonical": cn, "type": e.get("type") if e.get("type") in TYPES else "",
               "relevance": rel, "keep": bool(e.get("keep"))}
        if n not in by_name:
            by_name[n] = row
            ents.append(row)
    lookup = {}
    for r in ents:
        lookup[r["name"]] = r["canonical"]
        lookup[r["canonical"]] = r["canonical"]
    src = EC._norm(text)
    ent_norms = {EC._norm(x) for x in lookup}
    combos, singles, seen = [], [], set()
    for k in obj.get("keywords") or []:
        t = " ".join(str((k.get("text") if isinstance(k, dict) else k) or "").split())
        kind = (k.get("kind") if isinstance(k, dict) else "") or ""
        words = t.split(" ")
        if t in lookup and kind != "combo":
            c, kind = lookup[t], "single"
        elif kind == "combo" or len(words) >= 2:
            # 조합형: 2~5어절 · 30자 이하 · 엔티티 1개 이상 포함 · 모든 어절이 메타에 있음(지어낸 말 차단)
            if not (2 <= len(words) <= 5 and len(t) <= 30):
                continue
            if not any(e and e in EC._norm(t) for e in ent_norms):
                continue
            if not all(EC._norm(w) in src for w in words):
                continue
            c, kind = t, "combo"
        elif 2 <= len(t) <= 20 and EC._norm(t) in src:
            c, kind = t, "single"                                   # 메타에 그대로 나오는 단일 용어
        else:
            continue
        if c not in seen:
            seen.add(c)
            (combos if kind == "combo" else singles).append({"text": c, "kind": kind})
    kws = combos + singles                                          # 조합형 우선
    return {"entities": ents, "keywords": kws[:3],
            "dropped": [r["name"] for r in ents if not r["keep"]],
            "renamed": [[r["name"], r["canonical"]] for r in ents if r["canonical"] != r["name"]]}


def _mock(names: list, canon: dict) -> dict:
    """--mock 서버용(화면 확인): 확신도 순서를 거꾸로 써서 지금 방식과 다르게 보이게 한다."""
    ents = [{"name": n, "canonical": canon.get(n) or n, "type": "TM", "relevance": 90 - i * 10, "keep": i < len(names) - 1}
            for i, n in enumerate(reversed(names))]
    kws = [{"text": e["canonical"], "kind": "single"} for e in ents if e["keep"]][:3]
    if len(names) >= 2:
        kws = [{"text": names[0] + " " + names[1], "kind": "combo"}] + kws[:2]
    return {"entities": ents, "keywords": kws}


def _canon(names: list) -> dict:
    st = _SV.get_store() if _SV else None
    try:
        hit = st.ent_by_names(names) if (st and hasattr(st, "ent_by_names")) else {}
    except Exception:
        hit = {}
    out = {}
    for n in names:
        e = hit.get(n) or {}
        nm = e.get("canonical_name") or e.get("name") or ""
        if nm and nm != n:
            out[n] = nm
    return out


def _call(llm, system: str, user: str, tag: str):
    obj, res = llm.complete_json(system, user, tag=tag)
    if isinstance(obj, dict) and obj.get("_fail"):
        raise ValueError("모델 호출 실패 · " + str(obj.get("_fail_kind") or ""))
    return obj, {"in": getattr(res, "in_tok", 0), "out": getattr(res, "out_tok", 0)}


def refine(content: dict, item_meta: dict, llm, mock: bool = False, system: str = "") -> dict:
    """① 핵심 키워드 · 반환 {base, refined:{entities,keywords,dropped,renamed}, latency_ms, tokens}."""
    names = MC.entity_names((item_meta or {}).get("entities"))
    if not names:
        raise ValueError("1차 엔티티가 없습니다")
    canon = _canon(names)
    t0 = time.time()
    if mock:
        obj, tokens = _mock(names, canon), {}
    else:
        obj, tokens = _call(llm, system or _system("keyword", {}), _payload(item_meta, canon), "core_keyword")
    # 비교 기준(base)의 확신도는 원문으로 계산하지만, 재가공 입력·근거 확인은 발행 메타만 쓴다
    return {"base": baseline(item_meta, content), "refined": validate(obj, names, canon, _meta_text(item_meta)),
            "latency_ms": round((time.time() - t0) * 1000), "tokens": tokens}


def sentence(item_meta: dict, keywords: list, llm, mock: bool = False, system: str = "") -> dict:
    """② 핵심 문장 · 핵심 키워드 + 발행 메타로 재구축(제목·본문 없음) · 반환 {text, base(지금 리드문), from_keywords, …}."""
    t0 = time.time()
    m = _metas(item_meta)
    kws = [{"키워드": k["text"], "유형": "조합형" if k["kind"] == "combo" else "단일형"} for k in (keywords or [])]
    if mock:
        lead = (keywords or [{"text": (m["엔티티"] or ["콘텐츠"])[0]}])[0]["text"]
        obj, tokens = {"sentence": lead + "에 관한 소식을 정리한 글입니다"}, {}
    else:
        user = json.dumps({"핵심키워드": kws, **m}, ensure_ascii=False)
        obj, tokens = _call(llm, system or _system("sentence", {}), user, "core_sentence")
    return {"text": validate_sentence(obj), "base": m["리드문"], "from_keywords": bool(kws),
            "latency_ms": round((time.time() - t0) * 1000), "tokens": tokens}


def _model_id(model: str) -> str:
    """픽커 값(제공자|모델) → 모델 id · 평가 화면이 보내기 전에 떼는 것과 같은 규칙(app-04 · split('|').pop())."""
    return str(model or "").split("|")[-1].strip()


FAST_EFFORT = "minimal"                      # 메타 다듬기는 깊은 추론이 필요 없다
FAST_TIMEOUT = 120                           # 60초 기본은 추론 모델 한 호출에도 모자라 끊고 재시도했다
_FAST: dict = {}


def _fast(llm):
    """이 탭 전용 사본: Solar 추론 강도 minimal + 제한 시간 여유 · 공유 캐시(llm_for_model)는 건드리지 않는다.
    운영 실측(2026-10-07 · solar-pro4-260806 · 같은 콘텐츠): 기본 61~71초·출력 3.5~4.3K 토큰(대부분 추론) →
    minimal 4.8~6.8초·출력 274 토큰 · 키워드 품질 같음. 기본 제한 60초를 넘겨 끊고 재시도하던 것도 사라진다.
    # ponytail: Solar 만 적용 · 다른 제공자는 reasoning_effort 거절 시 자동 회피가 없어 기본 유지(필요하면 모델별로 확인 후 추가)"""
    if not str(getattr(llm, "model", "") or "").startswith("solar"):
        return llm
    hit = _FAST.get(llm.model)
    if hit is None:
        from .llm import LLMClient
        cfg = copy.deepcopy(llm.cfg)
        cfg.timeout = max(int(getattr(cfg, "timeout", 0) or 0), FAST_TIMEOUT)
        hit = _FAST[llm.model] = LLMClient(config=cfg, model=llm.model, reasoning_effort=FAST_EFFORT)
    return hit


def _llm(model: str):
    mock = bool(getattr(getattr(_SV, "Handler", None), "server_mock", False))
    if mock:
        return None, True, ""
    model = _model_id(model)
    llm, route = _SV.llm_for_model(model, False)
    if llm is not None:
        llm = _fast(llm)
    return llm, False, ("" if llm is not None else f"모델을 부를 수 없습니다({route or model or '기본 모델'})")


def _engines(team=None):
    """설정에서 호출별 (llm, mock, system, model) · 모델을 못 부르면 오류 문구."""
    cfg = get_config(team)
    out = {}
    for c in CALLS:
        llm, mock, err = _llm(cfg[c]["model"])
        if err:
            return None, f"{'핵심 키워드' if c == 'keyword' else '핵심 문장'} · {err}"
        out[c] = (llm, mock, _system(c, cfg), _model_id(cfg[c]["model"]) or "(기본)")
    return out, ""


def process(content: dict, item_meta: dict, eng: dict, progress=None) -> dict:
    """① 핵심 키워드 → ② 핵심 문장(키워드를 받아 재구축) 순서 · 키워드가 실패해도 문장은 메타만으로 쓴다(from_keywords=False)."""
    out = {}
    if progress:
        progress("keyword")
    try:
        llm, mock, system, _ = eng["keyword"]
        out.update(refine(content, item_meta, llm, mock, system))
    except Exception as e:
        out["error"] = str(e)
        out["base"] = baseline(item_meta, content)
    if progress:
        progress("sentence")
    try:
        llm, mock, system, _ = eng["sentence"]
        out["sentence"] = sentence(item_meta, (out.get("refined") or {}).get("keywords") or [], llm, mock, system)
    except Exception as e:
        out["sentence"] = {"error": str(e), "base": str((item_meta or {}).get("summary") or "")}
    return out


def _content_of(h: str, team=None):
    """해시 → (content, item_meta) · 정답셋(검수된 엔티티·리드문)."""
    st = _SV.get_store()
    from .store import golden_hash
    for r in (st.get_golden(team) if hasattr(st, "get_golden") else []) or []:
        if golden_hash(r) == h:
            exp = r.get("expected") or {}
            return r.get("content") or {}, {"entities": exp.get("entities"), "summary": exp.get("summary"), "intent": exp.get("intent"), "content_category": exp.get("content_category")}
    return None, None


def try_one(body: dict, team=None) -> dict:
    eng, err = _engines(team)
    if err:
        return {"ok": False, "error": err}
    h = str(body.get("hash") or "").strip()
    if h:
        content, im = _content_of(h, team)
        if content is None:
            return {"ok": False, "error": "정답셋에서 이 해시를 찾지 못했습니다"}
    else:
        content = {"title": body.get("title") or "", "body": body.get("body") or ""}
        split = lambda k: [x.strip() for x in str(body.get(k) or "").split(",") if x.strip()]
        im = {"entities": split("entities"), "summary": str(body.get("summary") or ""),
              "intent": split("intent"), "content_category": split("category")}
    return {"ok": True, "hash": h, "title": content.get("title") or "",
            "models": {c: eng[c][3] for c in CALLS}, **process(content, im, eng)}


# ── 정답셋 일괄 시험(백그라운드 잡 · decide 와 같은 패턴) ──────────────────────
_LOCK = threading.Lock()
_RUNS: dict = {}
_SEQ = 0


def start(team=None, n: int = 30) -> dict:
    global _SEQ
    eng, err = _engines(team)
    if err:
        return {"ok": False, "error": err}
    st = _SV.get_store()
    from .store import golden_hash
    rows = []
    for r in (st.get_golden(team) if hasattr(st, "get_golden") else []) or []:
        exp = r.get("expected") or {}
        if len(MC.entity_names(exp.get("entities"))) >= 3:
            rows.append((golden_hash(r), r.get("content") or {}, {"entities": exp.get("entities"), "summary": exp.get("summary"), "intent": exp.get("intent"), "content_category": exp.get("content_category")}))
    rows = rows[:max(1, min(int(n or 30), 300))]
    if not rows:
        return {"ok": False, "error": "엔티티가 3개 이상인 정답이 없습니다"}
    with _LOCK:
        _SEQ += 1
        rid = _SEQ
        _RUNS[rid] = {"running": True, "total": len(rows), "done": 0, "items": [], "active": {}, "error": "",
                      "models": {c: eng[c][3] for c in CALLS}, "started": time.time()}
        for k in [k for k, v in _RUNS.items() if not v["running"] and k < rid - 10]:
            _RUNS.pop(k, None)
    threading.Thread(target=_run, args=(rid, rows, eng), daemon=True).start()
    return {"ok": True, "id": rid, "total": len(rows)}


def _run(rid, rows, eng):
    run = _RUNS[rid]

    def one(row):
        h, content, im = row
        def progress(stage):
            with _LOCK:
                run["active"][h] = stage
        try:
            item = {"hash": h, "title": content.get("title") or "", **process(content, im, eng, progress)}
        except Exception as e:
            item = {"hash": h, "title": (content.get("title") or "") if isinstance(content, dict) else "",
                    "error": str(e), "sentence": {"error": "재가공하지 못했습니다"}}
        finally:
            with _LOCK:
                run["items"].append(item)
                run["done"] += 1
                run["active"].pop(h, None)

    try:
        with ThreadPoolExecutor(WORKERS) as ex:
            list(ex.map(one, rows))
    except Exception as e:
        with _LOCK:
            run["error"] = str(e)
    finally:
        with _LOCK:
            run["running"] = False
            run["elapsed_s"] = round(time.time() - run["started"], 1)


def summary(items: list) -> dict:
    """일괄 결과 요약: 지금 방식과 키워드가 겹치는 정도 · 빠진 엔티티 비율 · 정식명 바뀐 건."""
    ok = [i for i in items if not i.get("error")]
    sents = [i["sentence"] for i in items if isinstance(i.get("sentence"), dict) and i["sentence"].get("text")]
    sent = {"sent_n": len(sents), "sent_kw": sum(1 for x in sents if x.get("from_keywords")), "sent_fails": sum(1 for i in items if (i.get("sentence") or {}).get("error")),
            "sent_len_avg": round(sum(len(x["text"]) for x in sents) / len(sents)) if sents else None,
            "sent_same": sum(1 for x in sents if x["text"].strip() == (x.get("base") or "").strip())}
    if not ok:
        return {"n": 0, "fails": len(items), **sent}
    ov = [len({b["name"] for b in i["base"]} & {k["text"] for k in i["refined"]["keywords"]}) for i in ok]
    ents = sum(len(i["refined"]["entities"]) for i in ok)
    return {"n": len(ok), "fails": len(items) - len(ok),
            "same3": sum(1 for x in ov if x == 3), "overlap_avg": round(sum(ov) / len(ok), 2),
            "dropped_share": round(sum(len(i["refined"]["dropped"]) for i in ok) / max(1, ents), 3),
            "renamed": sum(len(i["refined"]["renamed"]) for i in ok),
            "short": sum(1 for i in ok if len(i["refined"]["keywords"]) < 3),
            **sent,
            "combo_share": round(sum(1 for i in ok for k in i["refined"]["keywords"] if k["kind"] == "combo")
                                   / max(1, sum(len(i["refined"]["keywords"]) for i in ok)), 3)}


def status(run_id) -> dict:
    try:
        rid = int(run_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "잘못된 id"}
    with _LOCK:
        run = _RUNS.get(rid)
        if not run:
            return {"ok": False, "error": "만료된 실행입니다 · 다시 실행하세요"}
        items = list(run["items"])
        out = {"ok": True, "id": rid, **{k: v for k, v in run.items() if k not in ("started", "items", "active")},
               "active": dict(run["active"]), "elapsed_s": round(time.time() - run["started"], 1)}
    return {**out, "items": items, "summary": summary(items)}


def vote(body: dict, reviewer: str, team=None) -> dict:
    """검수자 선택 → events(kind=entkw_vote) · 키워드 정답 라벨의 원천."""
    pick = str(body.get("pick") or "")
    h = str(body.get("hash") or "").strip()
    if pick not in PICKS or not h:
        return {"ok": False, "error": "선택 값이 올바르지 않습니다"}
    meta = {"hash": h, "pick": pick, "model": str(body.get("model") or "")[:80], "call": "keyword",
            "base": [str(x)[:80] for x in (body.get("base") or [])][:3],
            "refined": [str(x)[:80] for x in (body.get("refined") or [])][:3]}
    st = _SV.get_store()
    st.log_event(reviewer, "entkw_vote", json.dumps(meta, ensure_ascii=False), team=team)
    return {"ok": True}


def votes(team=None) -> dict:
    st = _SV.get_store()
    rows = st.events_since(("entkw_vote",), 0, team) if hasattr(st, "events_since") else []
    tally = {p: 0 for p in PICKS}
    for r in rows:
        try:
            p = json.loads(r.get("meta") or "{}").get("pick")
        except (TypeError, ValueError):
            continue
        if p in tally:
            tally[p] += 1
    return {"ok": True, "n": sum(tally.values()), "tally": tally}
