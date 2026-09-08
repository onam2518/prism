"""아이템 메타 채점 · 문제점 진단 · 쿡북(진단 → 프롬프트 지시) · 표준 라이브러리만.

등급·사유·인텐트만 재던 평가에 카테고리(계층 F1) · 엔티티(정규화 F1) · 리드문(임베딩 코사인 ·
무키·오류면 문자 2-gram F1)을
더한다. 근거는 docs/EVAL_META_SIMILARITY.md. abtest.score 가 행마다 meta_tally 를 부르고
마지막에 meta_report 를 합친다(인텐트 계수와 같은 구조).
"""
from __future__ import annotations
import difflib
import hashlib
import re
from collections import Counter

from . import dictionaries as D
from .entdict import normalize_name

EMBED_FN = None                  # 리드문 임베딩 주입 훅([텍스트] → [벡터]) · None 이면 실키가 있을 때만 클라이언트 생성
SUMMARY_FALLBACK_GATE = 0.4      # 2-gram 폴백일 때 리드문에만 쓰는 게이트 · 메타 게이트(0.6)는 코사인 기준
_ENT_STRIP = re.compile(r"\(주\)|㈜|주식회사|\([^)]*\)|[\s·,.'\"]+")


def _f1(a: set, b: set) -> float:
    return (2 * len(a & b) / (len(a) + len(b))) if (a or b) else 1.0


def _cats(v) -> set:
    out = set()
    for c in (v if isinstance(v, (list, tuple)) else [v]):
        p = D.normalize_content_category(str(c or "")) if c else ""
        if p and p != "Unclassified":
            out.add(p)
    return out


def _hier(cats: set) -> set:
    """계층 F1 용 조상 확장: 'T1 / T2' 에 'T1' 을 더한다(부모만 맞춰도 부분 점수)."""
    return cats | {c.split(" / ")[0].strip() for c in cats}


def ent_norm(s) -> str:
    return _ENT_STRIP.sub("", normalize_name(s).lower())


def _ents(v) -> set:
    return {ent_norm(x) for x in (v or []) if isinstance(x, str) and ent_norm(x)}


def _partial(a: str, b: str) -> bool:
    return (len(a) >= 2 and len(b) >= 2 and (a in b or b in a)) or difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def _ent_partial_f1(want: set, got: set) -> float:
    """MUC 식: 정확 일치 1점 · 부분 일치 0.5점."""
    if not (want or got):
        return 1.0
    hit = 0.0
    left = set(got)
    for w in want:
        if w in left:
            hit += 1; left.discard(w)
        elif any(_partial(w, g) for g in left):
            hit += 0.5; left.discard(next(g for g in left if _partial(w, g)))
    return 2 * hit / (len(want) + len(got))


def _bigrams(s: str) -> Counter:
    t = "".join(str(s or "").split())
    return Counter(t[i:i + 2] for i in range(len(t) - 1))


def summary_sim(a: str, b: str) -> float:
    """폴백: 문자 2-gram F1. 어순만 다른 동의문에 박하다(그래서 게이트도 SUMMARY_FALLBACK_GATE)."""
    x, y = _bigrams(a), _bigrams(b)
    if not (x or y):
        return 1.0
    return 2 * sum((x & y).values()) / (sum(x.values()) + sum(y.values()))


def _auto_embed(texts: list):
    """실키가 있을 때만 임베딩. mock(키 없음)이면 None → 2-gram 폴백."""
    from .config import Config
    from .embed import EmbeddingClient
    emb = EmbeddingClient(cache_path=Config.load().emb_cache_path)
    if emb.mock:                                 # mock 은 해싱 임베딩이라 의미 비교가 아니다(topicops 와 같은 규칙)
        return None
    vecs = [emb.embed(t, is_query=False) for t in texts]
    emb.flush()                                  # 같은 정답셋을 라운드마다 다시 재는 오토파일럿의 재호출을 줄인다
    return vecs


def summary_sims(pairs: list) -> tuple:
    """(기대, 산출) 리드문 쌍 목록 → (유사도 목록, 방법). 평가 1회분 리드문을 한 번에 모아 넘긴다.
    ponytail: EmbeddingClient 가 단건 API 라 호출 수는 문장 수 그대로(캐시로 중복만 흡수) ·
    배치 입력이 필요하면 embed._api_embed 에 input 리스트를 먼저 넣는다."""
    texts = [t for p in pairs for t in p]
    try:
        vecs = (EMBED_FN or _auto_embed)(texts) if texts else None
    except Exception:                            # 무키·네트워크·한도 오류로 평가가 멈추지 않게
        vecs = None
    if vecs and len(vecs) == len(texts):
        from .embed import cosine
        return ([min(1.0, max(0.0, cosine(vecs[i], vecs[i + 1]))) for i in range(0, len(texts), 2)],
                "embed_cosine")
    return [summary_sim(a, b) for a, b in pairs], "bigram_f1"


def _im(out) -> dict:
    im = out.get("item_meta") if isinstance(out, dict) else None
    return im if isinstance(im, dict) else {}


def meta_tally(acc: dict, exp: dict, out) -> None:
    """건 1개를 카테고리·엔티티·리드문 카운터에 반영. 기대값이 비어 있는 필드는 분모 제외.
    산출 None(실패)은 빈 산출로 채점(등급·인텐트 규칙과 동일)."""
    im = _im(out)
    wc = _cats(exp.get("content_category"))
    if wc:
        gc = _cats(im.get("content_category"))
        acc["cat_n"] = acc.get("cat_n", 0) + 1
        acc["cat_f1_sum"] = acc.get("cat_f1_sum", 0.0) + _f1(wc, gc)
        acc["cat_hf1_sum"] = acc.get("cat_hf1_sum", 0.0) + _f1(_hier(wc), _hier(gc))
        acc["cat_exact"] = acc.get("cat_exact", 0) + int(wc == gc)
        per = acc.setdefault("per_cat", {})
        for v in wc | gc:
            d = per.setdefault(v, {"n": 0, "tp": 0, "fp": 0, "fn": 0})
            if v in wc:
                d["n"] += 1; d["tp" if v in gc else "fn"] += 1
            else:
                d["fp"] += 1
        conf = acc.setdefault("cat_conf", Counter())
        for w in wc - gc:
            for g in gc - wc:
                conf[(w, g)] += 1
    we = _ents(exp.get("entities"))
    if we:
        ge = _ents(im.get("entities"))
        acc["ent_n"] = acc.get("ent_n", 0) + 1
        acc["ent_f1_sum"] = acc.get("ent_f1_sum", 0.0) + _f1(we, ge)
        acc["ent_pf1_sum"] = acc.get("ent_pf1_sum", 0.0) + _ent_partial_f1(we, ge)
        acc.setdefault("ent_missed", Counter()).update(we - ge)
        acc.setdefault("ent_spurious", Counter()).update(ge - we)
    ws = str(exp.get("summary") or "").strip()
    if ws:
        acc.setdefault("sum_pairs", []).append((ws, str(im.get("summary") or "")))


def _prf(d: dict) -> dict:
    tp, fp, fn = d["tp"], d["fp"], d["fn"]
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    return {"n": d["n"], "tp": tp, "fp": fp, "fn": fn, "precision": round(p, 3), "recall": round(r, 3),
            "f1": round((2 * p * r / (p + r)) if (p + r) else 0.0, 3)}


def meta_report(acc: dict) -> dict:
    cn, en = acc.get("cat_n", 0), acc.get("ent_n", 0)
    pairs = acc.get("sum_pairs") or []
    sims, sim_method = summary_sims(pairs)       # 리드문은 여기서 한 번에(임베딩 호출을 한 지점으로)
    sn = len(pairs)
    r4 = lambda s, n: round(s / n, 4) if n else 0
    return {
        "cat_n": cn, "cat_f1": r4(acc.get("cat_f1_sum", 0.0), cn), "cat_hf1": r4(acc.get("cat_hf1_sum", 0.0), cn),
        "cat_exact": r4(acc.get("cat_exact", 0), cn),
        "by_category": {v: _prf(d) for v, d in sorted((acc.get("per_cat") or {}).items())},
        "cat_confusion": [{"expected": w, "got": g, "n": n}
                          for (w, g), n in (acc.get("cat_conf") or Counter()).most_common(10)],
        "ent_n": en, "ent_f1": r4(acc.get("ent_f1_sum", 0.0), en), "ent_f1_partial": r4(acc.get("ent_pf1_sum", 0.0), en),
        "ent_missed": [{"name": k, "n": n} for k, n in (acc.get("ent_missed") or Counter()).most_common(10)],
        "ent_spurious": [{"name": k, "n": n} for k, n in (acc.get("ent_spurious") or Counter()).most_common(10)],
        "summary_n": sn, "summary_sim": r4(sum(sims), sn), "summary_low_n": sum(1 for s in sims if s < 0.3),
        "summary_sim_method": sim_method,        # embed_cosine · bigram_f1(게이트·툴팁 표기 근거)
    }


# ── 종합 점수 · 게이트 ───────────────────────────────────────────────────────
WEIGHTS = {"grade_accuracy": 0.4, "intent_f1": 0.15, "cat_hf1": 0.15, "ent_f1": 0.15, "summary_sim": 0.15}
_FIELD_N = {"intent_f1": "intent_n", "cat_hf1": "cat_n", "ent_f1": "ent_n", "summary_sim": "summary_n"}
FIELD_KO = {"grade_accuracy": "등급 일치율", "intent_f1": "인텐트 F1", "cat_hf1": "카테고리 F1(계층)",
            "ent_f1": "엔티티 F1", "summary_sim": "리드문 유사도"}


def overall(m: dict, grade_gate: float, meta_gate: float) -> dict:
    """측정된 필드만 가중 평균 · 게이트(등급=grade_gate · 메타=meta_gate) 미달 필드 목록."""
    tot = s = 0.0
    fails = []
    for k, w in WEIGHTS.items():
        if k != "grade_accuracy" and not (m.get(_FIELD_N[k]) or 0):
            continue
        v = float(m.get(k) or 0)
        s += w * v; tot += w
        g = grade_gate if k == "grade_accuracy" else meta_gate
        if k == "summary_sim" and m.get("summary_sim_method") == "bigram_f1":
            g = SUMMARY_FALLBACK_GATE            # 2-gram 은 어순 차이에 박하다 → 리드문만 낮은 선
        if v < g:
            fails.append(k)
    return {"overall": round(s / tot, 4) if tot else 0, "gate_fails": fails, "passed": not fails}


# ── 진단(문제점 도출) · 쿡북(지시 생성) ─────────────────────────────────────
def _rid(stage: str, text: str) -> str:
    return hashlib.sha1((stage + "\x1f" + text).encode()).hexdigest()[:12]


def _recipe(field, kind, key, n, score, stage, text, why):
    return {"id": _rid(stage, text), "field": field, "kind": kind, "key": key, "n": n, "score": score,
            "stage": stage, "directive": text, "why": why}


def diagnose(m: dict, min_n: int = 3) -> list:
    """지표 → 문제점 목록. 각 항목에 쿡북 지시(stage · directive)를 붙여 돌려준다. 큰 문제 순."""
    out = []
    for k, d in (m.get("by_reason_bucket") or {}).items():
        if d.get("n", 0) >= min_n + 2 and d.get("grade_acc", 1) < 0.7:
            out.append(_recipe("grade", "bucket", k, d["n"], d["grade_acc"], "review",
                               f"사유 '{k}' 유형에서 등급 판정이 자주 어긋난다. 이 유형은 판정 기준을 한 번 더 확인하고 근거 문장을 명시한 뒤 등급을 정한다.",
                               f"사유 '{k}' {d['n']}건 중 등급 일치 {d['grade_acc']:.0%}"))
    for v, d in (m.get("by_intent_value") or {}).items():
        if d.get("n", 0) >= min_n and d.get("f1", 1) < 0.6:
            miss = d["fn"] >= d["fp"]
            out.append(_recipe("intent", "missed" if miss else "spurious", v, d["fn" if miss else "fp"], d["f1"], "analyze",
                               (f"인텐트 '{v}' 를 놓치는 경우가 잦다. 본문에 해당 속성이 드러나면 반드시 포함한다."
                                if miss else f"인텐트 '{v}' 를 근거 없이 붙이는 경우가 잦다. 본문에 명시적 근거가 있을 때만 붙인다."),
                               f"인텐트 '{v}' F1 {d['f1']:.2f} (누락 {d['fn']} · 과다 {d['fp']})"))
    for c in (m.get("cat_confusion") or []):
        if c["n"] >= 2:
            t1 = c["expected"].split(" / ")[0]
            crit = D.CATEGORY_CRITERIA.get(t1, "")
            out.append(_recipe("category", "confusion", f"{c['expected']} → {c['got']}", c["n"], None, "analyze",
                               f"'{c['expected']}' 로 분류해야 할 콘텐츠를 '{c['got']}' 로 분류하는 경우가 잦다. 두 분류가 헷갈리면 '{c['expected']}' 를 우선한다."
                               + (f" 기준: {crit}" if crit else ""),
                               f"{c['n']}건 혼동"))
    confused = {c["expected"] for c in (m.get("cat_confusion") or []) if c["n"] >= 2}
    for v, d in (m.get("by_category") or {}).items():
        if v not in confused and d.get("n", 0) >= min_n and d.get("f1", 1) < 0.6 and d["fn"] > d["fp"]:
            out.append(_recipe("category", "missed", v, d["fn"], d["f1"], "analyze",
                               f"카테고리 '{v}' 누락이 잦다. 해당 주제 신호가 있으면 복수 카테고리에 '{v}' 를 포함한다.",
                               f"카테고리 '{v}' F1 {d['f1']:.2f} (누락 {d['fn']})"))
    missed = [e for e in (m.get("ent_missed") or []) if e["n"] >= 2]
    if missed:
        names = ", ".join(e["name"] for e in missed[:5])
        out.append(_recipe("entities", "missed", names, sum(e["n"] for e in missed), m.get("ent_f1"), "extract",
                           f"엔티티 누락이 잦다(예: {names}). 본문에 등장하는 인물·기관·제품·작품명은 빠짐없이 표기 그대로 추출한다.",
                           f"반복 누락 {len(missed)}종"))
    spur = [e for e in (m.get("ent_spurious") or []) if e["n"] >= 2]
    if spur:
        names = ", ".join(e["name"] for e in spur[:5])
        out.append(_recipe("entities", "spurious", names, sum(e["n"] for e in spur), m.get("ent_f1"), "extract",
                           f"본문에 없는 엔티티를 만들어 내는 경우가 있다(예: {names}). 본문에 나온 표기만 추출하고 추론으로 보태지 않는다.",
                           f"반복 과다 {len(spur)}종"))
    if (m.get("summary_n") or 0) >= min_n and (m.get("summary_sim") or 1) < 0.35:
        out.append(_recipe("summary", "low", "리드문", m.get("summary_low_n", 0), m.get("summary_sim"), "extract",
                           "리드문은 주체와 핵심 사건을 담은 한 문장으로, 본문 첫 문단의 사실만 쓰고 해석·수식은 넣지 않는다.",
                           f"리드문 유사도 {m.get('summary_sim'):.2f} · 낮은 건 {m.get('summary_low_n', 0)}"))
    out.sort(key=lambda r: -(r["n"] or 0))
    return out
