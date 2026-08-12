"""에이전트 하네스 · 방법론(Methodology)으로 구성되는 에이전트 그래프 실행기.

기존 `pipeline.extract()` 의 흩어진 손잡이(legal·quality_split·emb·prefilter·yellow…)를
1급 **Methodology** 객체로 형식화하고, 디스패처·에이전트·검증·집계를 **등록부(REGISTRY)
기반 스테이지 그래프**로 실행한다. `pipeline.extract()` 는 기본 방법론으로 이 하네스를
호출하는 호환 셰임이 된다(검증된 동작 보존).

이 위에 평가/A·B(`abtest.py`)와 REAP 피드백 루프(`feedback_loop.py`)가 올라간다.

흐름:  Dispatch → Legal → Quality → Item  → Assemble
       (no-LLM)  (opt)    (LLM)     (LLM)    (no-LLM)
각 스테이지는 공유 컨텍스트(HCtx)를 읽고 쓴다. legal RED 면 halt → 이후 스테이지 skip.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .schema import Content, Output, Trace, LegalMeta, QualityMeta
from . import routing as R
from . import agents as A
from . import verify as V
from . import prompts as P
from . import dictionaries as D


# ── 방법론: '인입 후 어떤 방법으로 처리·판정할지'의 선언적 1급 객체 ──
@dataclass
class Methodology:
    """A/B 는 두 Methodology 를 같은 데이터셋에 돌려 비교한다. 단계별 모델·프롬프트는
    prompts.STAGE_DIRECTIVE / config.stage_models 와 연동(serve sync_prompt)."""
    name: str = "default"
    version: str = "m1"
    legal: bool = False
    quality_split: bool = False
    embed_categories: bool = True
    yellow: bool = False
    prefilter_conf: float = 0.72
    yellow_low: float = 0.45
    slim: bool = False
    # 4호출 중 ①리드문·②엔티티 동시 실행. 산출은 순차와 동일(두 콜 user 프롬프트가 prior 미사용),
    # 차단 계약은 유지(① 빈값 → ② 결과 폐기·③④ 생략). 기본 on 근거: 실키 A/B(2026-07-03,
    # solar-pro3 16쌍 교차 측정) 평균 -15.5%·중앙값 -18.0% 지연, API 실패 0, 산출 일치 16/16.
    # 순차 회귀 비교는 abtest 프리셋 "sequential".
    parallel_calls: bool = True
    # quality ∥ item 스테이지 동시 실행(A/B 검증용 · 기본 off): 게이트는 사후 적용이라 산출은
    # 순차와 동일, 트레이드오프 = R(비-YELLOW) 판정 시 아이템 4호출 비용 낭비. 승격은 실측 후.
    parallel_quality_item: bool = False
    stages: tuple = ("dispatch", "legal", "quality", "item")   # assemble 은 항상 종단

    def to_dict(self) -> dict:
        return {"name": self.name, "version": self.version, "legal": self.legal,
                "quality_split": self.quality_split, "embed_categories": self.embed_categories,
                "yellow": self.yellow, "prefilter_conf": self.prefilter_conf,
                "yellow_low": self.yellow_low, "slim": self.slim,
                "parallel_calls": self.parallel_calls,
                "parallel_quality_item": self.parallel_quality_item, "stages": list(self.stages)}

    @classmethod
    def from_dict(cls, d: dict) -> "Methodology":
        d = dict(d or {})
        if "stages" in d and d["stages"]:
            d["stages"] = tuple(d["stages"])
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── 실행 컨텍스트: 스테이지들이 공유하는 가변 상태 ──
@dataclass
class HCtx:
    content: Content
    llm: object
    methodology: Methodology
    emb: object = None                 # 런타임 리소스(능력) · 방법론이 아니라 주입
    prefilter: object = None
    fewshot_pool: object = None
    routing: object = None
    legal_meta: object = None
    qm: object = None
    item_meta: object = None
    results: list = field(default_factory=list)
    verdicts: list = field(default_factory=list)
    fallbacks: list = field(default_factory=list)
    trace: object = None
    halt: bool = False                 # legal RED → 조기 종료
    pred_v: object = None              # 임베딩 2차의견(YELLOW 판단 캐리)
    pred_c: object = None
    t0: float = 0.0                    # 실행 시작 시각(wall-clock 지연 측정 · run() 이 설정)


# ── 스테이지(에이전트 그래프 노드). 각자 ctx 를 변이. ──
def st_dispatch(ctx: HCtx):
    """Dispatcher(no-LLM): 서비스 그룹·활성 메타·콘텐츠 트랙 결정."""
    ctx.routing = R.dispatch(ctx.content)
    if ctx.routing.content_track == "image_only":
        ctx.fallbacks.append("image_only: 텍스트 분류기 skip (별도 트랙)")


def st_legal(ctx: HCtx):
    """[옵션] 법령 스테이지. RED → 즉시 차단(halt)."""
    if not ctx.methodology.legal or ctx.routing.content_track == "image_only":
        return
    lm, lres = A.run_legal(ctx.llm, ctx.content)
    ctx.legal_meta = lm
    ctx.results += [r for r in lres if hasattr(r, "cost_usd")]
    ctx.fallbacks += V.verify_legal(lm)
    if lm.representative_grade == "RED":
        ctx.qm = _blocked_quality()
        ctx.fallbacks.append("legal RED → 전체 차단")
        ctx.halt = True


def st_quality(ctx: HCtx):
    """품질 메타(+ YELLOW: 저신뢰 → 사람 검수). 임베딩 사전필터 하이브리드 에스컬레이션."""
    m = ctx.methodology
    if ctx.routing.content_track == "image_only":
        ctx.qm = QualityMeta(finalGrade="G", reasons=[])
        return
    qm = None
    if ctx.prefilter is not None:
        ctx.pred_v, ctx.pred_c, pre_reasons = ctx.prefilter.predict(ctx.content)
        if ctx.pred_v is not None and ctx.pred_c >= m.prefilter_conf:    # 고신뢰 → LLM skip
            # 근거는 '모델이 설명한 문장'이 아니라 '임베딩이 자동 판정했다'는 사실 그대로 남긴다.
            # 검수 보조 브리핑이 이 값을 근거로 보여주므로, LLM 이 안 돈 건을 돈 것처럼 보이면 안 된다.
            qm = QualityMeta(finalGrade=ctx.pred_v, reasons=list(pre_reasons),
                             confidence=round(ctx.pred_c, 3),
                             evidence=f"임베딩 사전필터 자동 판정(신뢰도 {round(ctx.pred_c, 3)}) · LLM 미호출")
            ctx.verdicts.append({"agent": "QualityPrefilter(emb)",
                                 "evidence": f"conf={ctx.pred_c} (auto)", "fail": None})
        else:
            ctx.fallbacks.append(f"prefilter conf={ctx.pred_c} < {m.prefilter_conf} → LLM 에스컬레이션")
    if qm is None:                                                       # LLM 경로
        if m.quality_split:
            qm, qres = A.run_quality_split(ctx.llm, ctx.content, ctx.routing)
        else:
            fewshot = ctx.fewshot_pool.render(ctx.llm.model) if ctx.fewshot_pool else ""
            qm, qres = A.run_quality(ctx.llm, ctx.content, ctx.routing, fewshot=fewshot)
        ctx.results += [r for r in qres if hasattr(r, "cost_usd")]
        ctx.verdicts += [r for r in qres if isinstance(r, dict)]
        if m.yellow and ctx.pred_v is not None and qm.finalGrade:       # YELLOW 판정(판정 보류는 사유 보존)
            qm.confidence = round(ctx.pred_c, 3)
            if ctx.pred_v != qm.finalGrade:
                qm.review = "yellow"
                qm.review_reason = f"불일치: emb={ctx.pred_v} vs llm={qm.finalGrade}"
            elif m.yellow_low <= ctx.pred_c < m.prefilter_conf:
                qm.review = "yellow"
                qm.review_reason = f"신뢰도 중간대역(conf={round(ctx.pred_c, 3)})"
            if qm.review == "yellow":
                ctx.verdicts.append({"agent": "YellowGate",
                                     "evidence": qm.review_reason, "fail": None})
    ctx.qm = qm
    if not qm.finalGrade and qm.review == "yellow":    # '_fail' 표기 → store fail_kind=api → 다음 배치 재실행 대상
        ctx.fallbacks.append("quality_fail → 판정 보류(재실행 대상)")
    ctx.fallbacks += V.verify_quality(qm, ctx.routing.active_quality_metas)


_COMMERCE_METAS = {"ad", "spam"}                 # 광고성 계열(상거래) · D3 우선순위 적용 대상(유해·법령 메타 제외)
_AD_SUPPRESS_INTENTS = {"보도자료·공식발표"}       # 이 편집 인텐트가 붙으면 commerce 단독 R 을 사람 검수로 내린다


def _commerce_only_r(qm) -> bool:
    """품질이 R 이고 사유가 광고성 계열(ad·spam)뿐 — 유해·법령 사유는 없음(D3(a) 대상 케이스)."""
    rs = (qm.reasons or []) if qm else []
    return bool(qm) and qm.finalGrade == "R" and bool(rs) and all(r in _COMMERCE_METAS for r in rs)


def st_item(ctx: HCtx):
    """아이템 메타(G 또는 YELLOW). 임베딩 kNN 카테고리(2-pass) + 사전화."""
    m = ctx.methodology
    # 광고성 단독 R 도 아이템을 돌려 인텐트를 확보한다(D3: 편집 인텐트로 광고성-R 을 내릴지 판정하기 위함)
    gate_item = (ctx.qm.finalGrade == "G" or ctx.qm.review == "yellow" or _commerce_only_r(ctx.qm))
    if not (gate_item and ctx.routing.content_track != "image_only"):
        return
    im, ires = A.run_item(ctx.llm, ctx.content, parallel=m.parallel_calls)
    ctx.item_meta = im
    ctx.results += [r for r in ires if hasattr(r, "cost_usd")]
    ctx.verdicts += [r for r in ires if isinstance(r, dict)]
    # 인텐트는 임베딩 결정론 분류(있을 때). 콘텐츠 카테고리는 1312 기준 '콘텐츠 단위 N개'라
    # 엔티티별 분류가 아니라 LLM 콘텐츠 단위 산출 → 사전화(스냅)한다.
    if ctx.emb is not None and m.embed_categories:
        from . import classify as C
        cats, margin = C.intent_category_classify(ctx.emb, ctx.content)
        if cats:
            im.intent = C.merge_perspective(cats, im.intent)   # 관점 축(옹호/반박)은 LLM 판정 보존
            ctx.verdicts.append({"agent": "IntentCategory(emb)",
                                 "evidence": f"margin={margin}", "fail": None})
    if im and im.content_category:
        im.content_category = D.normalize_category_list(im.content_category)   # 콘텐츠 단위 사전화
    ctx.fallbacks += V.verify_item(im, ctx.content)


REGISTRY = {
    "dispatch": st_dispatch,
    "legal": st_legal,
    "quality": st_quality,
    "item": st_item,
}


def run(content_dict: dict, llm, methodology: Methodology = None, *,
        emb=None, quality_prefilter=None, fewshot_pool=None) -> dict:
    """방법론으로 에이전트 그래프를 실행 → 통합 메타 dict."""
    m = methodology or Methodology()
    content = Content.from_dict(content_dict)
    if llm.mock and getattr(llm, "_mock_fn", None) is None:
        llm._mock_fn = _mock_generator
    ctx = HCtx(content=content, llm=llm, methodology=m, emb=emb,
               prefilter=quality_prefilter, fewshot_pool=fewshot_pool,
               legal_meta=LegalMeta(enabled=False),
               trace=Trace(prompt_version=f"{P.quality_version()}, {P.IMETA_VERSION}"),
               t0=time.time())
    for key in m.stages:
        if ctx.halt:
            break
        if m.parallel_quality_item and key == "quality" and "item" in m.stages:
            _run_quality_item_parallel(ctx)
            continue
        if m.parallel_quality_item and key == "item" and "quality" in m.stages:
            continue                                   # 병렬 쌍에서 이미 소화
        REGISTRY[key](ctx)
    return _assemble(ctx)


def _run_quality_item_parallel(ctx: HCtx):
    """quality ∥ item 동시 실행(방법론 옵션): 게이트를 사후 적용 · R(비-YELLOW) 판정이면
    item 산출을 폐기해 산출 파리티를 유지한다(그 콜 비용 낭비가 트레이드오프).
    트레이스는 quality → item 순서로 병합(결정론)."""
    import copy
    import threading
    cq = copy.copy(ctx)
    cq.results, cq.verdicts, cq.fallbacks = [], [], []
    ci = copy.copy(ctx)
    ci.results, ci.verdicts, ci.fallbacks = [], [], []
    ci.qm = QualityMeta(finalGrade="G", reasons=[])    # 게이트 통과용 더미(사후 게이트가 대체)
    errs = []

    def _q():
        try:
            st_quality(cq)
        except Exception as e:
            errs.append(e)

    def _i():
        try:
            st_item(ci)
        except Exception as e:
            errs.append(e)
    t1 = threading.Thread(target=_q)
    t2 = threading.Thread(target=_i)
    t1.start(); t2.start(); t1.join(); t2.join()
    if errs:
        raise errs[0]
    ctx.qm = cq.qm
    ctx.pred_v, ctx.pred_c = cq.pred_v, cq.pred_c
    ctx.results += cq.results + ci.results
    ctx.verdicts += cq.verdicts + ci.verdicts
    ctx.fallbacks += cq.fallbacks + ci.fallbacks
    gate = (ctx.qm.finalGrade == "G" or ctx.qm.review == "yellow" or _commerce_only_r(ctx.qm))
    if gate and ctx.routing.content_track != "image_only":
        ctx.item_meta = ci.item_meta                   # 광고성 단독 R 은 인텐트 확인 위해 아이템 메타 보존(D3)
    elif ci.item_meta is not None:
        ctx.item_meta = None
        ctx.fallbacks.append("병렬 스테이지: R 판정 → 아이템 메타 폐기(비용 트레이드오프)")


def _assemble(ctx: HCtx) -> dict:
    """Aggregator(no-LLM): 통합 + trace 집계.

    주의: emb 는 배치 공유 클라이언트라 per-content 누적비용을 여기 더하면 중복계상된다.
    임베딩 비용은 호출측(cli)에서 배치 단위로 1회만 합산한다 → 여기선 LLM 비용만."""
    # 법령 평가 실패(호출 장애) → 깨끗한 자동 G 로 유통하지 않고 사람 검수로 보류(fail-open 금지)
    if getattr(ctx.legal_meta, "failed", False) and ctx.qm and ctx.qm.finalGrade == "G" and ctx.qm.review != "yellow":
        ctx.qm.review = "yellow"
        ctx.qm.review_reason = ctx.qm.review_reason or "법령 평가 호출 실패 · 판정 보류"
        ctx.fallbacks.append("legal_fail → 판정 보류(사람 검수)")
    # 메타 생성 콜이 끝내 실패한 건도 깨끗한 자동 G 로 유통하지 않는다(법령 실패와 같은 fail-open 금지).
    # 실패한 콜의 산출은 빈 값으로 남는데(예: entities=[]) 종전에는 review=auto 로 통과해,
    # 빈 메타가 사람 눈을 한 번도 거치지 않고 정답 후보로 흘렀다
    # (2026-07-28 실사례: item_entities 만 5회 실패 → entities=[] · 등급 G · review=auto).
    # fail_kind 는 재시도를 모두 소진한 최종 실패에만 붙으므로, 재시도로 살아난 콜은 잡히지 않는다.
    failed_tags = sorted({(getattr(r, "tag", "") or "(기타)") for r in ctx.results
                          if getattr(r, "fail_kind", None)})
    if failed_tags and ctx.qm and ctx.qm.finalGrade == "G" and ctx.qm.review != "yellow":
        ctx.qm.review = "yellow"
        ctx.qm.review_reason = (ctx.qm.review_reason
                                or f"메타 생성 콜 실패({', '.join(failed_tags)}) · 판정 보류")
        ctx.fallbacks.append("call_fail → 판정 보류(사람 검수)")
    # D3(a · 260715 회의): 우선순위 인텐트 > 품질 G/R > 광고성 사유. 광고성 계열(ad·spam) 단독 R 인데
    # 정당한 편집 인텐트(보도자료·공식발표)가 부여됐으면 Red 로 승격하지 않고 사람 검수로 보류.
    # 유해·법령 사유가 하나라도 있으면(_commerce_only_r=False) 적용하지 않아 모더레이션은 그대로.
    if (_commerce_only_r(ctx.qm) and ctx.item_meta
            and (set(ctx.item_meta.intent or []) & _AD_SUPPRESS_INTENTS)):
        ctx.qm.review = "yellow"
        ctx.qm.review_reason = ctx.qm.review_reason or "광고성 사유 vs 편집 인텐트(보도자료·공식발표) 상충 · 사람 검수 보류"
        ctx.qm.finalGrade = ""                          # Red 미승격 · 판정 보류(자동 G 도 아님)
        ctx.fallbacks.append("commerce_R + 편집 인텐트 → 판정 보류(사람 검수)")
    t = ctx.trace
    t.model = getattr(ctx.llm, "model", "") or ""      # 초안 생성 모델 기록
    t.agent_verdicts = [v for v in ctx.verdicts if v.get("evidence") or v.get("fail")]
    t.fallbacks = ctx.fallbacks
    t.cost_usd = round(sum(r.cost_usd for r in ctx.results), 6)
    # 캐시 토큰(cache_read/cache_write)은 제공자가 보고할 때만 0 이상이다. 이 값이 계속 0 이면
    # '캐싱이 아예 안 걸림'이고, in 대비 cache_read 비율이 캐시 적중률이다(효과 측정의 유일한 원천).
    t.tokens = {"in": sum(r.in_tok for r in ctx.results),
                "out": sum(r.out_tok for r in ctx.results),
                "cache_read": sum(int(getattr(r, "cache_read_tok", 0) or 0) for r in ctx.results),
                "cache_write": sum(int(getattr(r, "cache_write_tok", 0) or 0) for r in ctx.results)}
    # total = 콜 지연의 단순 '합'(다운스트림 계약 · abtest 의 latency_p50/p95 원천이라 의미 고정).
    # wall  = 이 콘텐츠 1건 처리에 실제로 걸린 시간(스테이지 그래프 전체의 wall-clock).
    # 병렬 구간(summary∥entities · quality∥item)은 total 에서 이중 계상되므로 total - wall ≈
    # 병렬로 겹쳐 아낀 시간이다. **병렬화 개선은 반드시 wall 로 측정할 것**(total 로 보면 0 으로 보인다).
    t.latency_ms = {"total": sum(r.latency_ms for r in ctx.results)}
    if ctx.t0:
        t.latency_ms["wall"] = max(0, int((time.time() - ctx.t0) * 1000))
    by_call = {}
    for r in ctx.results:                              # 콜 태그별 비용·토큰 분해(콜별 모델 라우팅 근거)
        tag = getattr(r, "tag", "") or "(기타)"
        b = by_call.setdefault(tag, {"n": 0, "cost": 0.0, "in": 0, "out": 0, "ms": 0,
                                     "cache_read": 0, "cache_write": 0, "retries": 0})
        b["n"] += 1
        b["cost"] = round(b["cost"] + r.cost_usd, 6)
        b["in"] += r.in_tok
        b["out"] += r.out_tok
        b["ms"] += r.latency_ms
        b["cache_read"] += int(getattr(r, "cache_read_tok", 0) or 0)
        b["cache_write"] += int(getattr(r, "cache_write_tok", 0) or 0)
        # 캐시 값이 0 일 때 원인이 둘로 갈린다: 라우터가 usage 에 캐시 필드를 아예 안 실어 줌
        # (cache_src="") vs 실어 주는데 매번 미스(cache_src="miss:…"). 진단의 유일한 단서라
        # 마지막 비어 있지 않은 출처를 남긴다(숫자가 아니므로 롤업 합산 대상 아님).
        src = getattr(r, "cache_source", "") or ""
        if src:
            b["cache_src"] = src
        # 재시도 횟수: LLMResult.retries 는 종전에 어디에도 안 실려 '몇 번 만에 성공했는지'가
        # 사라졌다(429·파싱 실패로 인한 숨은 지연·비용의 원인). 콜별로 올려 둔다.
        b["retries"] += int(getattr(r, "retries", 0) or 0)
    t.by_call = by_call
    # 콜 실패 표면화: LLMResult.fail_kind 를 trace 로 올려 빈 산출의 원인을 진단 가능하게 한다
    # (모델 A/B 에서 '빈값인데 왜'를 하네스가 스스로 보고 · 예: gemini 침묵 빈응답 → parse_empty).
    # detail(예외 원문)까지 올린다 — 종전엔 kind 만 남아 '연결 실패' 배지로는 타임아웃인지
    # 응답 형식 문제인지 가릴 수 없었다(2026-07-28). 원장·화면이 이 값을 그대로 쓴다.
    t.fails = [{"tag": getattr(r, "tag", "") or "", "kind": r.fail_kind,
                "detail": (getattr(r, "fail_detail", "") or "")[:200]}
               for r in ctx.results if getattr(r, "fail_kind", None)]
    out = Output(ctx.content.ref(), ctx.routing, ctx.legal_meta, ctx.qm, ctx.item_meta, t)
    return out.to_dict(slim=ctx.methodology.slim)


def _blocked_quality():
    return QualityMeta(finalGrade="R", reasons=["graphic"])   # 차단 표식


# ── Mock 생성기: 키 없이 배관/대시보드 검증용(정확도 아님). 키워드 휴리스틱. ──
_KW = {
    "ad": ["구매", "할인", "쿠폰", "협찬", "링크", "최저가", "광고"],
    "gambling": ["복권", "로또", "베팅", "토토", "카지노"],
    "clickbait": ["충격", "경악", "발칵", "결국", "이것", "?!"],
    "sexual": ["19금", "야한", "노출"],
    "spam": ["꼭 보세요", "공유", "감동실화"],
    "shallow": [],
}


def _mock_generator(system: str, user: str, tag: str) -> dict:
    body = user.lower()
    if tag == "quality" or tag.startswith("q:"):
        reasons = []
        for meta, kws in _KW.items():
            if any(k.lower() in body for k in kws):
                reasons.append(meta)
        m = re.search(r"body:\s*(.*)", user, re.S)
        blen = len(m.group(1).strip()) if m else 0
        if blen < 30:
            reasons.append("shallow")
        reasons = list(dict.fromkeys(reasons))
        if tag.startswith("q:"):
            return {"reasons": reasons, "evidence": "mock"}
        return {"finalGrade": "R" if reasons else "G", "reasons": reasons,
                "evidence": "mock heuristic"}

    if tag == "item_summary":                     # 분리형 ①: title·body 모두 비면 단락 차단 신호("")
        title = _field(user, "title")
        b = _field(user, "body")
        if not title.strip() and not b.strip():
            return {"summary": ""}
        ents = _mock_entities(title + " " + b)
        return {"summary": f"{(ents[0] if ents else '주제')} 관련 내용을 정리"}
    if tag == "item_entities":                    # 분리형 ②
        return {"entities": _mock_entities(_field(user, "title") + " " + _field(user, "body"))}
    if tag == "item_intent":                      # 분리형 ③: 사전 값에서 결정론 선택
        svc = _field(user, "displayServiceName")
        return {"intent": D.intent_categories_for(svc)[:2]}
    if tag == "item_category":                    # 분리형 ④
        return {"content_category": ["News and Politics / Society"]}

    if tag == "item":
        title = _field(user, "title")
        ents = _mock_entities(title + " " + _field(user, "body"))
        svc = _field(user, "displayServiceName")
        cats = D.intent_categories_for(svc)[:2]
        return {"summary": f"{(ents[0] if ents else '주제')} 관련 내용을 정리",
                "entities": ents, "intent": cats,
                "content_category": ["News and Politics / Society"]}  # 콘텐츠 단위 N개

    if tag == "legal_route":
        codes = []
        if any(k in body for k in ["사기", "환불 안", "먹튀"]):
            codes.append({"code": "fraud", "confidence": 0.6})
        if any(k in body for k in ["명예", "허위사실"]):
            codes.append({"code": "defamation", "confidence": 0.5})
        return {"harm_types": codes}

    if tag.startswith("legal:"):
        return {"a": 20, "b": 15, "c": 10, "evidence": "mock"}
    return {}


def _field(user: str, key: str) -> str:
    m = re.search(rf"^{key}:\s*(.*)$", user, re.M)
    return m.group(1).strip() if m else ""


def _mock_entities(text: str) -> list:
    toks = re.findall(r"[가-힣A-Za-z]{2,}", text)
    stop = {"있다", "했다", "관련", "내용", "오늘", "그리고", "이번"}
    freq = {}
    for t in toks:
        if t in stop:
            continue
        freq[t] = freq.get(t, 0) + 1
    ranked = sorted(freq, key=lambda k: (-freq[k], -len(k)))
    return ranked[:3]
