"""평가 하네스 + A/B 테스트 · 방법론(Methodology)을 골든셋에 돌려 지표를 산출하고,
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
from . import metaeval as ME


# ── 방법론 프리셋: 흔한 '방법론 선택' 후보 ──
PRESETS = {
    "baseline": H.Methodology(name="baseline"),                               # 단일 좁힌 콜
    "split":    H.Methodology(name="split", quality_split=True),              # 분해형(4묶음)
    "legal":    H.Methodology(name="legal", legal=True),                      # 법령 스테이지 on
    "yellow":   H.Methodology(name="yellow", yellow=True),                    # YELLOW 사람검수 게이트
    "no-emb":   H.Methodology(name="no-emb", embed_categories=False),         # 임베딩 카테고리 off
    "parallel": H.Methodology(name="parallel", parallel_calls=True),          # ①②호출 동시(=현재 기본)
    "sequential": H.Methodology(name="sequential", parallel_calls=False),     # 순차 회귀 비교용
    "stage-parallel": H.Methodology(name="stage-parallel", parallel_quality_item=True),  # 품질∥아이템 동시(지연 A/B)
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


def _percentile(vals: list, p: float):
    """정렬 후 선형 인덱스 백분위(소표본 전제 · 보간 없음). 빈 목록이면 None."""
    if not vals:
        return None
    s = sorted(vals)
    i = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
    return round(float(s[i]), 1)


# ── 인텐트 정확도 지표 ──────────────────────────────────────────────────────
# 채점 단일 소스: abtest.score(일괄) 와 evalops._tally(증분) 가 이 세 함수를 공유한다.
# 두 경로가 같은 카운터 키(intent_n·intent_exact·intent_jac_sum·intent_top1·
# intent_skipped·per_intent)를 쌓고 같은 intent_report() 로 비율을 낸다.
#
# 설계 결정
# ① 순서: 헤드라인 지표(exact·jaccard·값별 P/R/F1)는 **집합 의미(순서 무시)**.
#    인텐트 순서는 산출 경로에 따라 의미가 달라 순서 일치를 지표로 쓰면 경로 교체만으로
#    지표가 흔들린다 — LLM 경로(agents._match_intents)는 모델 응답 순서를 보존하지만
#    임베딩 경로(classify.intent_category_classify)는 코사인 랭킹 순으로 재배열하고
#    classify.merge_perspective 는 관점 축을 뒤에 덧붙인다.
#    다만 대표값(첫 번째)은 소비처가 실제로 쓰므로 `intent_top1` 로 분리 측정한다(가드 대상 아님).
# ② 표본: 기대값에 인텐트 라벨이 **1개 이상** 있는 행만 분모. 라벨이 없는 행(키 자체가
#    없거나 빈 목록)은 제외하고 그 수를 `intent_skipped` 로 노출한다.
#    빈 목록을 제외하는 이유: build_golden_from_reviews 가 모든 검수 유래 골든에 intent
#    키를 무조건 써넣어 '키 유무'로는 라벨 유무를 못 가리고, R 등급 행은 harness 가
#    item_meta 를 통째로 억제해 기대·산출이 모두 공집합으로 고정 = 자동 만점 행이 되어
#    지표를 희석하고 회귀 가드의 민감도를 떨어뜨린다.


def intent_expected(exp: dict) -> list:
    """골든 기대값에서 인텐트 라벨 목록(순서 보존·중복 제거). 라벨이 없으면 빈 목록.
    문자열 단건·비목록 등 과거 골든 행의 느슨한 형태도 흡수(하위호환)."""
    v = (exp or {}).get("intent")
    if isinstance(v, str):
        v = [v]
    elif isinstance(v, dict):                    # 구 형식(키별) 방어: 값만 추림
        v = list(v.values())
    elif not isinstance(v, (list, tuple)):
        v = []
    return list(dict.fromkeys(s for s in (str(x).strip() for x in v if x is not None) if s))


def intent_got(out) -> list:
    """산출(Output.to_dict)에서 인텐트 목록. item_meta 부재(R 억제·실패·빈 산출)면 빈 목록."""
    if not isinstance(out, dict):
        return []
    im = out.get("item_meta")
    v = im.get("intent") if isinstance(im, dict) else getattr(im, "intent", None)
    if isinstance(v, str):
        v = [v]
    elif not isinstance(v, (list, tuple)):
        v = []
    return list(dict.fromkeys(s for s in (str(x).strip() for x in v if x is not None) if s))


def intent_tally(acc: dict, exp: dict, out) -> None:
    """건 1개를 인텐트 카운터에 반영(제자리 갱신). 기대 라벨이 없으면 분모 제외 후 계수만.
    산출이 None(실패·빈 산출)인 건도 '빈 집합 산출'로 채점한다 — 등급 채점이 실패 행을
    오답으로 계수하는 규칙(score/_tally 주석)과 같은 취급."""
    want = intent_expected(exp)
    if not want:
        acc["intent_skipped"] = acc.get("intent_skipped", 0) + 1
        return
    got = intent_got(out)
    sw, sg = set(want), set(got)
    acc["intent_n"] = acc.get("intent_n", 0) + 1
    acc["intent_exact"] = acc.get("intent_exact", 0) + int(sw == sg)
    u = sw | sg
    acc["intent_jac_sum"] = acc.get("intent_jac_sum", 0.0) + ((len(sw & sg) / len(u)) if u else 1.0)
    acc["intent_f1_sum"] = acc.get("intent_f1_sum", 0.0) + (2 * len(sw & sg) / (len(sw) + len(sg)))   # 샘플 기준 F1
    acc["intent_top1"] = acc.get("intent_top1", 0) + int(bool(got) and got[0] == want[0])
    per = acc.setdefault("per_intent", {})
    for v in sw | sg:
        d = per.setdefault(v, {"n": 0, "tp": 0, "fp": 0, "fn": 0})
        if v in sw:
            d["n"] += 1                          # support = 기대 등장 횟수
            d["tp" if v in sg else "fn"] += 1
        else:
            d["fp"] += 1


def intent_report(acc: dict) -> dict:
    """인텐트 카운터 → 리포트 키. 표본 0이면 비율은 0(없음)으로 두고 표본 수로 판단하게 한다."""
    n = int(acc.get("intent_n") or 0)
    by = {}
    for v, d in sorted((acc.get("per_intent") or {}).items()):
        tp, fp, fn = int(d.get("tp") or 0), int(d.get("fp") or 0), int(d.get("fn") or 0)
        p = tp / (tp + fp) if (tp + fp) else 0.0
        r = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
        by[v] = {"n": int(d.get("n") or 0), "tp": tp, "fp": fp, "fn": fn,
                 "precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3)}
    return {
        "intent_n": n,                                   # 측정 표본(기대 인텐트가 있는 행)
        "intent_skipped": int(acc.get("intent_skipped") or 0),   # 기대 인텐트 없어 제외한 행
        "intent_exact": round(acc.get("intent_exact", 0) / n, 4) if n else 0,
        "intent_jaccard": round(acc.get("intent_jac_sum", 0.0) / n, 4) if n else 0,
        "intent_f1": round(acc.get("intent_f1_sum", 0.0) / n, 4) if n else 0,
        "intent_top1": round(acc.get("intent_top1", 0) / n, 4) if n else 0,
        "by_intent_value": by,
    }


def service_key(row: dict) -> str:
    """골든 행 → 서비스 구분 키(content.displayServiceName · 비면 '(미지정)').
    abtest.score(일괄) 와 evalops._tally(증분) 가 같은 키로 세도록 단일 소스."""
    return str(((row or {}).get("content") or {}).get("displayServiceName") or "").strip() or "(미지정)"


def service_report(per: dict) -> dict:
    """서비스별 카운터 → {서비스: {n, grade_acc, ci_lo, ci_hi}} · by_reason_bucket 과 같은 모양.
    표본이 얇은 서비스를 '나쁜 서비스'로 오독하지 않게 이항 95% 신뢰구간(quality.binomial_ci)을 병기한다."""
    from . import quality as Q
    out = {}
    for k, v in sorted(per.items()):
        n = int(v.get("n") or 0)
        if not n:
            continue
        acc = (v.get("grade_ok") or 0) / n
        lo, hi = Q.binomial_ci(acc, n)
        out[k] = {"n": n, "grade_acc": round(acc, 3), "ci_lo": lo, "ci_hi": hi}
    return out


def score(rows: list, outs: list) -> dict:
    """골든셋 정답(rows[i].expected)과 산출(outs[i])을 비교해 지표 산출.
    cli.cmd_eval 과 동일 지표 · 채점 로직 단일 소스."""
    grade_hit = reason_exact = fn_block = empties = 0
    harm_n = 0                                   # 기대 R 행 수 = 유해 미탐률의 분모
    jac = cost = 0.0
    tin = tout = 0
    n = len(rows)
    per_reason = {}
    per_service = {}                             # 서비스(displayServiceName)별 등급 일치 · by_reason_bucket 과 같은 규칙
    yellow_n = auto_n = auto_hit = 0
    meta_hold = 0                                # 입력 필요(리드문·하위 추출 실패 → 사람이 채움) 행 수
    lat = []                                     # 건별 총 지연(ms) · p50/p95 산출용
    iacc: dict = {"per_intent": {}}              # 인텐트 카운터(intent_tally 단일 소스)
    for row, out in zip(rows, outs):
        exp = row.get("expected", {}) or {}
        intent_tally(iacc, exp, out)             # 실패(None) 산출도 '빈 집합'으로 채점
        ME.meta_tally(iacc, exp, out)            # 카테고리·엔티티·리드문(같은 카운터 dict)
        if exp.get("finalGrade") == "R":
            harm_n += 1                          # 산출 실패(None) 행도 분모에 포함(분자에는 미포함)
        if out is None:
            empties += 1
            continue
        qm = out["quality_meta"]
        tr = out.get("trace", {})
        if ((out.get("item_meta") or {}).get("hold_fields") if isinstance(out.get("item_meta"), dict) else None):
            meta_hold += 1
        c1 = tr.get("cost_usd")                  # 단가 미상 트레이스가 섞이면 합계도 None
        cost = None if (cost is None or c1 is None) else cost + c1
        tin += tr.get("tokens", {}).get("in", 0)
        tout += tr.get("tokens", {}).get("out", 0)
        lt = tr.get("latency_ms")
        t_ms = lt.get("total") if isinstance(lt, dict) else lt
        if t_ms:
            lat.append(float(t_ms))
        if any("fail" in str(f) or "unparse" in str(f) for f in tr.get("fallbacks", [])):
            empties += 1
        # 주의: fail/unparse 행도 아래 등급·이유 채점에 그대로 포함된다(empty_rate 와 비배타).
        #       구 cli eval 동작을 보존한 것 · 실패 산출을 '오답'으로 계수.
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
        s = per_service.setdefault(service_key(row), {"n": 0, "grade_ok": 0})
        s["n"] += 1
        s["grade_ok"] += int(grade_ok)
    by_reason = {k: {"n": v["n"], "grade_acc": round(v["grade_ok"] / v["n"], 3)}
                 for k, v in sorted(per_reason.items())}
    return {
        **intent_report(iacc),                   # 순수 추가: 기존 키 의미·이름·값 불변
        **ME.meta_report(iacc),
        "n": n,
        "grade_accuracy": round(grade_hit / n, 4) if n else 0,
        "reason_exact_match": round(reason_exact / n, 4) if n else 0,
        "reason_jaccard": round(jac / n, 4) if n else 0,
        # 유해 미탐률 = 기대 R 중 자동 G 로 흘린 비율(=1-recall(R)). 종전 분모는 '전체 행'이라
        # R 유병률만큼 축소된 값이었고, 골든에 정상 건만 늘려도 '개선'으로 보였다(2026-08-11).
        # 기대 R 행이 0이면 정의되지 않음(None) — 0% 로 표기하면 '완벽'으로 오독된다.
        "harm_miss_rate": (round(fn_block / harm_n, 4) if harm_n else None),
        "harm_miss_share": round(fn_block / n, 4) if n else 0,   # 종전 정의(전체 행 대비) 병기
        "harm_expected_n": harm_n,                               # 분모(기대 R 행 수) 노출
        "empty_rate": round(empties / n, 4) if n else 0,
        "cost_usd": None if cost is None else round(cost, 6),   # None = 단가 미상(화면 '·')
        "tokens": {"in": tin, "out": tout},
        "latency_p50_ms": _percentile(lat, 0.5),
        "latency_p95_ms": _percentile(lat, 0.95),
        "by_reason_bucket": by_reason,
        "by_service": service_report(per_service),               # 서비스별 정합성(원천 쪼개 보기)
        "yellow_rate": round(yellow_n / n, 4) if n else 0,
        "meta_hold_rate": round(meta_hold / n, 4) if n else 0,   # 전체 중 '입력 필요' 몫 · 모델이 틀린 게 아니라 못 뽑은 비율
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
            "intent_exact", "intent_jaccard",
            "harm_miss_rate", "empty_rate", "yellow_rate", "cost_usd",
            "latency_p50_ms", "latency_p95_ms"]
_BETTER_LOWER = {"harm_miss_rate", "empty_rate", "cost_usd", "latency_p50_ms", "latency_p95_ms"}


def ab_test(rows: list, meth_a: H.Methodology, meth_b: H.Methodology, llm, **kw) -> dict:
    """두 방법론을 같은 골든셋에 돌려 비교. 반환: {a, b, diff, winner, n}.
    승자: 등급 정확도 우선, 동률이면 유해 미탐률(낮을수록) 우선."""
    a_m = evaluate(rows, meth_a, llm, **kw)
    b_m = evaluate(rows, meth_b, llm, **kw)
    diff = {k: round((b_m.get(k, 0) or 0) - (a_m.get(k, 0) or 0), 6) for k in _AB_KEYS}
    # 기대 R 행이 없으면 harm_miss_rate 는 None(정의 없음) → 승자 판정에서는 0 으로 본다
    a_key = (a_m["grade_accuracy"], -(a_m.get("harm_miss_rate") or 0.0))
    b_key = (b_m["grade_accuracy"], -(b_m.get("harm_miss_rate") or 0.0))
    winner = "b" if b_key > a_key else "a" if a_key > b_key else "tie"
    # cost_usd(지표)는 LLM-only. 임베딩은 공유 클라이언트라 A/B로 쪼갤 수 없어 전체 1회로 별도 노출.
    emb = kw.get("emb")
    emb_cost = round(getattr(emb, "cost_usd", 0.0) or 0.0, 6) if emb is not None else 0.0
    return {"n": len(rows), "winner": winner,
            "a": {"name": meth_a.name, "metrics": a_m},
            "b": {"name": meth_b.name, "metrics": b_m},
            "diff": diff, "better_lower": sorted(_BETTER_LOWER),
            "embedding_cost_usd": emb_cost}
