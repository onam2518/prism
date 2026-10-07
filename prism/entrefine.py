"""엔티티 재처리 · 서비스 키워드 3개 선정 시험 (실험실 · 운영자 전용 · 2026-10-07).

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

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import entconf as EC
from . import meta_contract as MC

_SV = None                                   # serve 주입(learnops 관례)

BODY_MAX = 4000
WORKERS = 4
TYPES = ("PS", "OG", "LC", "AF", "EV", "TM")
PICKS = ("refined", "base", "both", "neither")

SYSTEM = """너는 콘텐츠에서 이미 뽑은 엔티티를 서비스 키워드 용도로 다시 정리하는 편집자다.
입력: 제목 · 본문 · 리드문 · 1차 엔티티 목록(이름 · 확신도 · 사전 정식명이 있으면 함께).

# 할 일
1. 1차 엔티티를 하나씩 판정한다.
   - keep: 이 콘텐츠의 서비스 키워드 후보로 쓸 수 있으면 true
   - canonical: 사전 정식명이 주어졌으면 그 표기, 없으면 원래 이름 그대로 (새 이름을 만들지 않는다)
   - type: PS(인물) · OG(기관·조직·브랜드) · LC(지역·장소) · AF(작품·제품) · EV(사건·행사) · TM(용어·개념) 중 하나
   - relevance: 0~100 · 이 콘텐츠의 핵심 주제를 대표하는 정도
2. keywords: 독자가 구독할 만한 키워드 정확히 3개(구독 버튼을 달았을 때 이 키워드로 새 글을 받고 싶을 말).
   - 엔티티 키워드: keep 이 true 인 엔티티의 canonical
   - 주제 키워드: 엔티티로는 콘텐츠의 주제가 안 잡힐 때만, 제목·리드문·본문에 그대로 나오는 2~20자 명사구(예: 금리 인하, 전기차 보조금)

# 키워드 기준
- 독자가 이 키워드로 비슷한 콘텐츠를 찾고 싶어 할 만큼 구체적인 대상을 고른다. 고유명사가 일반명사보다 먼저다.
- 빼는 것: 기자·작성자·출처 매체명, 서비스명(다음·티스토리·카페 등), 너무 일반적인 말(정부·시장·관계자·사진·영상),
  본문에 한 번 스치듯 나온 이름.
- 세 키워드는 서로 다른 대상이어야 한다. 같은 대상의 표기 변형이나 상하위(예: 삼성전자와 삼성)는 하나만 남긴다.
- 주제 키워드는 원문에 없는 말을 만들거나 바꿔 쓰지 않는다. 너무 넓은 말(경제·사회·이슈)은 쓰지 않는다.

# 출력 (JSON 한 개만)
{"entities": [{"name": string, "canonical": string, "type": string, "relevance": number, "keep": boolean}],
 "keywords": [{"text": string, "kind": "entity" | "concept"}]}"""


def baseline(item_meta: dict, content: dict) -> list:
    """지금 방식: 확신도 상위 3개(같으면 모델 순서) · [{name, conf}]."""
    sc = EC.scored_entities(item_meta or {}, {"title": (content or {}).get("title"), "body": (content or {}).get("body")})
    return sorted(sc, key=lambda s: -s["conf"])[:3] if sc else []


def _payload(content: dict, item_meta: dict, canon: dict) -> str:
    sc = EC.scored_entities(item_meta or {}, {"title": content.get("title"), "body": content.get("body")})
    ents = [{"이름": s["name"], "확신도": s["conf"], **({"사전정식명": canon[s["name"]]} if canon.get(s["name"]) else {})}
            for s in sc]
    return json.dumps({"제목": content.get("title") or "", "본문": str(content.get("body") or "")[:BODY_MAX],
                       "리드문": (item_meta or {}).get("summary") or "", "1차엔티티": ents}, ensure_ascii=False)


def validate(obj, names: list, canon: dict, text: str = "") -> dict:
    """모델 출력 검증: 1차 목록 밖 이름·사전에 없는 정식명 변경은 버린다 · 주제 키워드는 원문(text)에 있어야 한다.
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
    kws, seen, src = [], set(), EC._norm(text)
    for k in obj.get("keywords") or []:
        t = str((k.get("text") if isinstance(k, dict) else k) or "").strip()
        if t in lookup:
            c, kind = lookup[t], "entity"
        elif 2 <= len(t) <= 20 and EC._norm(t) and EC._norm(t) in src:
            c, kind = t, "concept"                                # 원문에 그대로 나오는 주제어만
        else:
            continue
        if c not in seen:
            seen.add(c)
            kws.append({"text": c, "kind": kind})
    return {"entities": ents, "keywords": kws[:3],
            "dropped": [r["name"] for r in ents if not r["keep"]],
            "renamed": [[r["name"], r["canonical"]] for r in ents if r["canonical"] != r["name"]]}


def _mock(names: list, canon: dict) -> dict:
    """--mock 서버용(화면 확인): 확신도 순서를 거꾸로 써서 지금 방식과 다르게 보이게 한다."""
    ents = [{"name": n, "canonical": canon.get(n) or n, "type": "TM", "relevance": 90 - i * 10, "keep": i < len(names) - 1}
            for i, n in enumerate(reversed(names))]
    return {"entities": ents, "keywords": [{"text": e["canonical"], "kind": "entity"} for e in ents if e["keep"]][:3]}


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


def refine(content: dict, item_meta: dict, llm, mock: bool = False) -> dict:
    """한 건 재처리 · 반환 {base, refined:{entities,keywords,dropped,renamed}, latency_ms, tokens}."""
    names = MC.entity_names((item_meta or {}).get("entities"))
    if not names:
        raise ValueError("1차 엔티티가 없습니다")
    canon = _canon(names)
    t0 = time.time()
    tokens = {}
    if mock:
        obj = _mock(names, canon)
    else:
        obj, res = llm.complete_json(SYSTEM, _payload(content, item_meta, canon), tag="entrefine")
        if isinstance(obj, dict) and obj.get("_fail"):
            raise ValueError("모델 호출 실패 · " + str(obj.get("_fail_kind") or ""))
        tokens = {"in": getattr(res, "in_tok", 0), "out": getattr(res, "out_tok", 0)}
    text = " ".join(str(x or "") for x in (content.get("title"), (item_meta or {}).get("summary"), content.get("body")))
    return {"base": baseline(item_meta, content), "refined": validate(obj, names, canon, text),
            "latency_ms": round((time.time() - t0) * 1000), "tokens": tokens}


def _llm(model: str):
    mock = bool(getattr(getattr(_SV, "Handler", None), "server_mock", False))
    if mock:
        return None, True, ""
    llm, route = _SV.llm_for_model(model, False)
    return llm, False, ("" if llm is not None else f"모델을 부를 수 없습니다({route or model})")


def _content_of(h: str, team=None):
    """해시 → (content, item_meta) · 정답셋 우선(검수된 엔티티) · 없으면 콘텐츠 원본."""
    st = _SV.get_store()
    for r in (st.get_golden(team) if hasattr(st, "get_golden") else []) or []:
        from .store import golden_hash
        if golden_hash(r) == h:
            exp = r.get("expected") or {}
            return r.get("content") or {}, {"entities": exp.get("entities"), "summary": exp.get("summary")}
    return None, None


def try_one(body: dict, team=None) -> dict:
    llm, mock, err = _llm(str(body.get("model") or ""))
    if err:
        return {"ok": False, "error": err}
    h = str(body.get("hash") or "").strip()
    if h:
        content, im = _content_of(h, team)
        if content is None:
            return {"ok": False, "error": "정답셋에서 이 해시를 찾지 못했습니다"}
    else:
        content = {"title": body.get("title") or "", "body": body.get("body") or ""}
        im = {"entities": [x.strip() for x in str(body.get("entities") or "").split(",") if x.strip()], "summary": ""}
    try:
        return {"ok": True, "hash": h, "title": content.get("title") or "", **refine(content, im, llm, mock)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── 정답셋 일괄 시험(백그라운드 잡 · decide 와 같은 패턴) ──────────────────────
_LOCK = threading.Lock()
_RUNS: dict = {}
_SEQ = 0


def start(team=None, n: int = 30, model: str = "") -> dict:
    global _SEQ
    llm, mock, err = _llm(model)
    if err:
        return {"ok": False, "error": err}
    st = _SV.get_store()
    from .store import golden_hash
    rows = []
    for r in (st.get_golden(team) if hasattr(st, "get_golden") else []) or []:
        exp = r.get("expected") or {}
        if len(MC.entity_names(exp.get("entities"))) >= 3:
            rows.append((golden_hash(r), r.get("content") or {}, {"entities": exp.get("entities"), "summary": exp.get("summary")}))
    rows = rows[:max(1, min(int(n or 30), 300))]
    if not rows:
        return {"ok": False, "error": "엔티티가 3개 이상인 정답이 없습니다"}
    with _LOCK:
        _SEQ += 1
        rid = _SEQ
        _RUNS[rid] = {"running": True, "total": len(rows), "done": 0, "items": [], "error": "", "model": model or "(기본)",
                      "started": time.time()}
        for k in [k for k, v in _RUNS.items() if not v["running"] and k < rid - 10]:
            _RUNS.pop(k, None)
    threading.Thread(target=_run, args=(rid, rows, llm, mock), daemon=True).start()
    return {"ok": True, "id": rid, "total": len(rows)}


def _run(rid, rows, llm, mock):
    run = _RUNS[rid]

    def one(row):
        h, content, im = row
        try:
            item = {"hash": h, "title": content.get("title") or "", **refine(content, im, llm, mock)}
        except Exception as e:
            item = {"hash": h, "title": content.get("title") or "", "error": str(e)}
        with _LOCK:
            run["items"].append(item)
            run["done"] += 1

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
    if not ok:
        return {"n": 0}
    ov = [len({b["name"] for b in i["base"]} & {k["text"] for k in i["refined"]["keywords"]}) for i in ok]
    ents = sum(len(i["refined"]["entities"]) for i in ok)
    return {"n": len(ok), "fails": len(items) - len(ok),
            "same3": sum(1 for x in ov if x == 3), "overlap_avg": round(sum(ov) / len(ok), 2),
            "dropped_share": round(sum(len(i["refined"]["dropped"]) for i in ok) / max(1, ents), 3),
            "renamed": sum(len(i["refined"]["renamed"]) for i in ok),
            "short": sum(1 for i in ok if len(i["refined"]["keywords"]) < 3),
            "concept_share": round(sum(1 for i in ok for k in i["refined"]["keywords"] if k["kind"] == "concept")
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
        out = {"ok": True, "id": rid, **{k: v for k, v in run.items() if k not in ("started", "items")}}
    return {**out, "items": items, "summary": summary(items)}


def vote(body: dict, reviewer: str, team=None) -> dict:
    """검수자 선택 → events(kind=entkw_vote) · 키워드 정답 라벨의 원천."""
    pick = str(body.get("pick") or "")
    h = str(body.get("hash") or "").strip()
    if pick not in PICKS or not h:
        return {"ok": False, "error": "선택 값이 올바르지 않습니다"}
    meta = {"hash": h, "pick": pick, "model": str(body.get("model") or "")[:80],
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
