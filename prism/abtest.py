"""평가 하네스 + A/B 테스트 — 방법론(Methodology)을 골든셋에 돌려 지표를 산출하고,
두 방법론을 같은 데이터셋에 돌려 성능을 비교한다.

'파이프라인 인입 후 어떤 방법론을 선택할지(테스트·성능 평가)'를 1급으로 만드는 모듈.
방법론 = harness.Methodology(baseline·분해형·하이브리드·few-shot 등). 채점기는 cli eval 과
공유한다(같은 지표). A/B = evaluate(A) vs evaluate(B) + diff.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

from . import harness as H


# ── 방법론 프리셋: 흔한 '방법론 선택' 후보 ──
PRESETS = {
    "baseline": H.Methodology(name="baseline"),                               # 단일 좁힌 콜
    "split":    H.Methodology(name="split", quality_split=True),              # 분해형(4묶음)
    "legal":    H.Methodology(name="legal", legal=True),                      # 법령 스테이지 on
    "yellow":   H.Methodology(name="yellow", yellow=True),                    # YELLOW 사람검수 게이트
    "no-emb":   H.Methodology(name="no-emb", embed_categories=False),         # 임베딩 카테고리 off
}


def load_methodology(spec: str) -> H.Methodology:
    """프리셋 이름 또는 JSON 파일 경로 → Methodology."""
    if spec in PRESETS:
        return PRESETS[spec]
    if os.path.isfile(spec):
        with open(spec, encoding="utf-8") as f:
            m = H.Methodology.from_dict(json.load(f))
        if m.name == "default":
            m.name = os.path.splitext(os.path.basename(spec))[0]
        return m
    raise ValueError(f"방법론을 찾을 수 없습니다: {spec} (프리셋 {list(PRESETS)} 또는 JSON 경로)")


def score(rows: list, outs: list) -> dict:
    """골든셋 정답(rows[i].expected)과 산출(outs[i])을 비교해 지표 산출.
    cli.cmd_eval 과 동일 지표 — 채점 로직 단일 소스."""
    grade_hit = reason_exact = fn_block = empties = 0
    jac = cost = 0.0
    tin = tout = 0
    n = len(rows)
    per_reason = {}
    yellow_n = auto_n = auto_hit = 0
    for row, out in zip(rows, outs):
        if out is None:
            empties += 1
            continue
        exp = row.get("expected", {})
        qm = out["quality_meta"]
        tr = out.get("trace", {})
        cost += tr.get("cost_usd", 0.0)
        tin += tr.get("tokens", {}).get("in", 0)
        tout += tr.get("tokens", {}).get("out", 0)
        if any("fail" in str(f) or "unparse" in str(f) for f in tr.get("fallbacks", [])):
            empties += 1
        # 주의: fail/unparse 행도 아래 등급·이유 채점에 그대로 포함된다(empty_rate 와 비배타).
        #       구 cli eval 동작을 보존한 것 — 실패 산출을 '오답'으로 계수.
        grade_ok = qm["finalGrade"] == exp.get("finalGrade")
        grade_hit += int(grade_ok)
        if qm.get("review") == "yellow":
            yellow_n += 1
        else:
            auto_n += 1
            auto_hit += int(grade_ok)
        got, want = set(qm["reasons"]), set(exp.get("reasons", []))
        reason_exact += int(got == want)
        u = got | want
        jac += (len(got & want) / len(u)) if u else 1.0
        if exp.get("finalGrade") == "R" and qm["finalGrade"] == "G":
            fn_block += 1
        bucket = (exp.get("reasons") or ["normal"])[0]
        d = per_reason.setdefault(bucket, {"n": 0, "grade_ok": 0})
        d["n"] += 1
        d["grade_ok"] += int(grade_ok)
    by_reason = {k: {"n": v["n"], "grade_acc": round(v["grade_ok"] / v["n"], 3)}
                 for k, v in sorted(per_reason.items())}
    return {
        "n": n,
        "grade_accuracy": round(grade_hit / n, 4) if n else 0,
        "reason_exact_match": round(reason_exact / n, 4) if n else 0,
        "reason_jaccard": round(jac / n, 4) if n else 0,
        "harm_miss_rate": round(fn_block / n, 4) if n else 0,
        "empty_rate": round(empties / n, 4) if n else 0,
        "cost_usd": round(cost, 6),
        "tokens": {"in": tin, "out": tout},
        "by_reason_bucket": by_reason,
        "yellow_rate": round(yellow_n / n, 4) if n else 0,
        "auto_coverage": round(auto_n / n, 4) if n else 0,
        "auto_grade_accuracy": round(auto_hit / auto_n, 4) if auto_n else 0,
    }


def run_methodology(rows: list, methodology: H.Methodology, llm, *,
                    emb=None, prefilter=None, fewshot_pool=None, concurrency: int = 8) -> list:
    """방법론을 골든셋에 돌려 산출 리스트 반환(채점 전)."""
    n = len(rows)
    outs = [None] * n
    errs = []

    def work(i):
        try:
            outs[i] = H.run(rows[i]["content"], llm, methodology, emb=emb,
                            quality_prefilter=prefilter, fewshot_pool=fewshot_pool)
        except Exception as e:
            outs[i] = None
            errs.append((i, e))

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        list(ex.map(work, range(n)))
    if errs:                # 무음 흡수 방지: 실패가 empty_rate 로만 둔갑하지 않게 첫 건 노출
        i0, e0 = errs[0]
        print(f"  [warn] 평가 중 {len(errs)}건 예외(empty 처리). 첫 건 #{i0}: {e0}")
    return outs


def evaluate(rows: list, methodology: H.Methodology, llm, **kw) -> dict:
    """방법론을 골든셋에 돌려 지표 산출(run + score)."""
    conc = kw.pop("concurrency", 8)
    outs = run_methodology(rows, methodology, llm, concurrency=conc, **kw)
    m = score(rows, outs)
    m["methodology"] = methodology.to_dict()
    return m


# 비교에 노출하는 핵심 지표(높을수록 좋음 / 낮을수록 좋음 구분은 _BETTER_LOWER)
_AB_KEYS = ["grade_accuracy", "auto_grade_accuracy", "reason_jaccard",
            "harm_miss_rate", "empty_rate", "yellow_rate", "cost_usd"]
_BETTER_LOWER = {"harm_miss_rate", "empty_rate", "cost_usd"}


def ab_test(rows: list, meth_a: H.Methodology, meth_b: H.Methodology, llm, **kw) -> dict:
    """두 방법론을 같은 골든셋에 돌려 비교. 반환: {a, b, diff, winner, n}.
    승자: 등급 정확도 우선, 동률이면 유해 미탐률(낮을수록) 우선."""
    a_m = evaluate(rows, meth_a, llm, **kw)
    b_m = evaluate(rows, meth_b, llm, **kw)
    diff = {k: round((b_m.get(k, 0) or 0) - (a_m.get(k, 0) or 0), 6) for k in _AB_KEYS}
    a_key = (a_m["grade_accuracy"], -a_m["harm_miss_rate"])
    b_key = (b_m["grade_accuracy"], -b_m["harm_miss_rate"])
    winner = "b" if b_key > a_key else "a" if a_key > b_key else "tie"
    # cost_usd(지표)는 LLM-only. 임베딩은 공유 클라이언트라 A/B로 쪼갤 수 없어 전체 1회로 별도 노출.
    emb = kw.get("emb")
    emb_cost = round(getattr(emb, "cost_usd", 0.0) or 0.0, 6) if emb is not None else 0.0
    return {"n": len(rows), "winner": winner,
            "a": {"name": meth_a.name, "metrics": a_m},
            "b": {"name": meth_b.name, "metrics": b_m},
            "diff": diff, "better_lower": sorted(_BETTER_LOWER),
            "embedding_cost_usd": emb_cost}
