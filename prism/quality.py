"""검수 품질 통계: 일치도(Krippendorff alpha) · 주석자 신뢰도(Dawid-Skene EM) · 이항 신뢰구간.

논문 근거(LEARNING_DESIGN.md 참조):
- Krippendorff's alpha: Hayes & Krippendorff 2007(표준 신뢰도 지표),
  Artstein & Poesio 2008(임계값 기계 적용 금지 → 참고 지표로만 표시).
- Dawid & Skene 1979: 정답 없는 상황에서 주석자 오류율·참 라벨 동시 최대우도 추정(EM).
- 이항 신뢰구간: Miller 2024(arXiv:2411.00640) SEM=sqrt(p(1-p)/n) 기반 95% CI 보고.
"""
from __future__ import annotations

import math


def binomial_ci(p: float, n: int, z: float = 1.96) -> tuple:
    """이항 비율 95% 신뢰구간(정규 근사). 반환 (lo, hi). n=0 이면 (0, 1)."""
    if not n:
        return (0.0, 1.0)
    sem = math.sqrt(max(p * (1 - p), 0.0) / n)
    return (round(max(0.0, p - z * sem), 4), round(min(1.0, p + z * sem), 4))


def krippendorff_alpha_binary(units: list) -> float | None:
    """이진(nominal) Krippendorff's alpha. units = [콘텐츠별 라벨 리스트(0/1)].
    평가 가능(라벨 2개 이상) 유닛만 사용. 계산 불가 시 None."""
    pairable = [u for u in units if len(u) >= 2]
    if not pairable:
        return None
    n_total = sum(len(u) for u in pairable)
    ones = sum(sum(u) for u in pairable)
    zeros = n_total - ones
    if not ones or not zeros:                     # 전원 동일 라벨 → 분산 없음
        return 1.0
    # 관찰 불일치 Do: 유닛 내 순서쌍(0,1)+(1,0) 비율(유닛 크기 보정)
    do_num = 0.0
    for u in pairable:
        m = len(u)
        o = sum(u)
        do_num += (2.0 * o * (m - o)) / (m - 1)
    do = do_num / n_total
    # 기대 불일치 De: 전체 풀 무작위 순서쌍
    de = (2.0 * ones * zeros) / (n_total * (n_total - 1))
    if de == 0:
        return 1.0
    return round(1.0 - do / de, 4)


def percent_agreement(units: list) -> float | None:
    """유닛(콘텐츠) 내 쌍별 일치 비율 평균. 라벨 2개 이상 유닛만."""
    pairable = [u for u in units if len(u) >= 2]
    if not pairable:
        return None
    acc = 0.0
    for u in pairable:
        m = len(u)
        o = sum(u)
        pairs = m * (m - 1) / 2
        agree = (o * (o - 1) + (m - o) * (m - o - 1)) / 2
        acc += agree / pairs
    return round(acc / len(pairable), 4)


def dawid_skene_binary(labels: dict, iters: int = 30) -> dict:
    """Dawid-Skene(1979) EM · 이진 특화. labels = {unit: {reviewer: 0|1}}.
    반환 {"reviewers": {reviewer: {"n", "sensitivity", "specificity", "error_rate"}},
          "units": {unit: p1(참 라벨=1 확률)}}. 데이터 부족 시 빈 dict 들."""
    units = {u: rv for u, rv in labels.items() if rv}
    reviewers = sorted({r for rv in units.values() for r in rv})
    if not units or not reviewers:
        return {"reviewers": {}, "units": {}}
    # 초기화: 다수결 소프트 라벨
    p1 = {u: (sum(rv.values()) / len(rv)) for u, rv in units.items()}
    sens = {r: 0.8 for r in reviewers}            # P(라벨1|참1)
    spec = {r: 0.8 for r in reviewers}            # P(라벨0|참0)
    prior1 = 0.5
    for _ in range(iters):
        # M-step: 검수자 혼동률 + 사전확률
        for r in reviewers:
            s_num = s_den = p_num = p_den = 0.0
            for u, rv in units.items():
                if r not in rv:
                    continue
                w1 = p1[u]
                w0 = 1 - w1
                s_den += w1
                p_den += w0
                if rv[r] == 1:
                    s_num += w1
                else:
                    p_num += w0
            sens[r] = min(max(s_num / s_den if s_den else 0.5, 1e-3), 1 - 1e-3)
            spec[r] = min(max(p_num / p_den if p_den else 0.5, 1e-3), 1 - 1e-3)
        prior1 = min(max(sum(p1.values()) / len(p1), 1e-3), 1 - 1e-3)
        # E-step: 유닛 참 라벨 사후확률
        for u, rv in units.items():
            l1 = math.log(prior1)
            l0 = math.log(1 - prior1)
            for r, v in rv.items():
                l1 += math.log(sens[r] if v == 1 else 1 - sens[r])
                l0 += math.log(1 - spec[r] if v == 1 else spec[r])
            m = max(l1, l0)
            e1, e0 = math.exp(l1 - m), math.exp(l0 - m)
            p1[u] = e1 / (e1 + e0)
    out_r = {}
    for r in reviewers:
        n = sum(1 for rv in units.values() if r in rv)
        err = round(1 - (sens[r] + spec[r]) / 2, 4)   # 균형 오류율
        out_r[r] = {"n": n, "sensitivity": round(sens[r], 4),
                    "specificity": round(spec[r], 4), "error_rate": err}
    return {"reviewers": out_r, "units": {u: round(v, 4) for u, v in p1.items()}}


def feedback_units(fmap: dict) -> list:
    """feedback_map → alpha/일치율용 유닛 리스트(good=1, bad=0)."""
    units = []
    for e in fmap.values():
        u = [1 if v.get("verdict") == "good" else 0
             for v in e.get("verdicts", []) if v.get("verdict") in ("good", "bad")]
        if u:
            units.append(u)
    return units


def feedback_labels(fmap: dict) -> dict:
    """feedback_map → Dawid-Skene 입력 {unit: {reviewer_key: 0|1}}.
    검수자 키는 reviewer_id(uuid) 우선 · 없으면 표시명(sqlite). gold_stats(uuid 키)·
    build_golden_from_reviews(uuid 우선 조회)와 동일 키를 써야 신뢰도 블렌드가 성립한다."""
    out = {}
    for ch, e in fmap.items():
        rv = {(v.get("reviewer_id") or v.get("reviewer")): (1 if v.get("verdict") == "good" else 0)
              for v in e.get("verdicts", []) if v.get("verdict") in ("good", "bad")}
        if rv:
            out[ch] = rv
    return out
