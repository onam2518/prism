"""솔라 디사이드 판정 시험 (실험실 · 운영자 전용).

Upstage Solar Decide 는 글을 쓰지 않고 타입이 정해진 판정(choice·score·noul)과 확률만 돌려주는
판정 전용 모델이다(TypeSafe Jev 와 같은 /v1/systemone 형식). 그래서 맡길 수 있는 것만 묻는다:
  · 품질 메타 11종 → noul 각각 · 하나라도 임계 이상이면 R (파이프라인 등급 규칙과 같음)
  · 인텐트 68종  → noul 각각 · 임계 이상 상위 INTENT_MAX 개(없으면 최고 1개)
  · 카테고리 95종 → choice 1개 · 2순위 확률이 CAT_SECOND 이상이면 함께
엔티티·리드문·근거 문장은 글을 써야 해서 묻지 않는다(리포트에서도 뺀다).

채점은 abtest.score 단일 소스를 그대로 쓴다 — 산출을 파이프라인 Output 모양으로 되돌려 넘긴다.
실행은 autoreview 와 같은 백그라운드 잡 + 폴링 · 결과는 메모리에만 둔다.
# ponytail: 잡 결과 메모리 보관(재배포 시 사라짐) · 기준 모델과 같은 표본 비교가 필요해지면 eval_runs 에 적재
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import abtest
from . import config as C
from . import dictionaries as D

_SV = None                                   # serve 주입(learnops 관례)

URL = os.environ.get("PRISM_DECIDE_URL", "https://api.upstage.ai/v1/systemone")
MODEL = "solar-decide"
PRICE_IN = 0.10 / 1e6                        # ponytail: 정가 추정($0.10/M · 할인가 $0.05) · 콘솔 공시 확인 후 교체
NOUL_GATE = 0.5                              # 품질 메타·인텐트 noul 기본 임계(화면에서 바꿀 수 있음)
INTENT_MAX = 3                               # 골든 건당 평균 1.9개 · 최대 4개
CAT_SECOND = 0.3
BODY_MAX = 8000                              # ponytail: 본문 앞부분만 보낸다 · 긴 글 손해가 보이면 늘린다
WORKERS = 8                                  # 독립 측정에서 동시 8건이 처리량 포화점
TIMEOUT = 30
_NONE = "none"                               # 카테고리 '해당 없음'(없으면 범위 밖에도 확신 높게 아무거나 고른다)


def _cat_paths() -> list:
    return [f"{t1} / {t2}" for t1, t2s in D.CONTENT_CATEGORY_TIER2.items() for t2 in t2s]


def questions() -> dict:
    """프리즘 정의문 → 질문 목록. 키는 ASCII(q·i·c 접두 + 순번) · 정의문 원천은 dictionaries 하나뿐."""
    q = {}
    for k, desc in D.QUALITY_METAS.items():
        q["q_" + k] = {"type": "noul", "instructions": "이 콘텐츠가 다음에 해당하는가? " + desc}
    for i, (name, desc) in enumerate(D.INTENT_VALUE_DEFS.items()):
        q[f"i{i}"] = {"type": "noul", "instructions": f"이 콘텐츠에 인텐트 '{name}'을(를) 붙이는가? 정의: {desc}"}
    crit = {}
    for i, path in enumerate(_cat_paths()):
        t1, t2 = path.split(" / ")
        d = (D.TIER2_DEFS.get(t2) or ("",))[0] or D.IAB_TIER1_DESC.get(t1, "")
        crit[f"c{i}"] = f"{path}: {d}" if d else path
    crit[_NONE] = "어느 카테고리에도 해당하지 않는다"
    q["category"] = {"type": "choice", "instructions": "이 콘텐츠의 주제 카테고리는?", "criteria": crit}
    return q


def _state(content: dict) -> dict:
    c = content or {}
    return {"title": str(c.get("title") or ""), "subtitle": str(c.get("subtitle") or ""),
            "body": str(c.get("body") or "")[:BODY_MAX]}


def _key() -> str:
    return os.environ.get("UPSTAGE_API_KEY") or C.Config.load().api_key or ""


def _post(body: dict, key: str) -> dict:
    req = urllib.request.Request(URL, data=json.dumps(body, ensure_ascii=False).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:     # 4xx 본문에 원인(베타 권한·형식 오류)이 있다 → 화면에 그대로
        raise RuntimeError(f"HTTP {e.code} · {e.read().decode(errors='replace')[:300]}") from None


def _mock_post(body: dict, key: str) -> dict:
    """--mock 서버용 가짜 응답(화면 확인용) · 첫 메타·첫 인텐트·첫 카테고리만 걸린다."""
    ans = {}
    for k, q in body["questions"].items():
        if q["type"] == "noul":
            ans[k] = {"type": "noul", "noul": 0.9 if k in ("i0", "q_ad") else 0.05}
        else:
            ans[k] = {"type": "choice", "choice": "c0", "probabilities": {"c0": 0.8, "c1": 0.2}}
    return {"model": MODEL + "-mock", "answers": ans, "usage": {"input_tokens": 1000, "output_tokens": 0}}


def to_output(resp: dict, gate: float = NOUL_GATE) -> dict:
    """systemone 응답 → 파이프라인 Output 모양(quality_meta·item_meta) · abtest.score 가 그대로 채점."""
    a = resp.get("answers") or {}
    noul = lambda k: float((a.get(k) or {}).get("noul") or 0.0)
    reasons = [k for k in D.QUALITY_METAS if noul("q_" + k) >= gate]
    names = list(D.INTENT_VALUE_DEFS)
    scored = sorted(((noul(f"i{i}"), n) for i, n in enumerate(names)), reverse=True)
    intents = [n for p, n in scored if p >= gate][:INTENT_MAX] or ([scored[0][1]] if scored else [])
    paths = _cat_paths()
    cat = a.get("category") or {}
    probs = cat.get("probabilities") or {cat.get("choice"): 1.0}
    ranked = [k for k, _ in sorted(probs.items(), key=lambda kv: -(kv[1] or 0)) if k and k != _NONE]
    picks = ranked[:1] + [k for k in ranked[1:2] if (probs.get(k) or 0) >= CAT_SECOND]
    cats = [paths[int(k[1:])] for k in picks if k[:1] == "c" and k[1:].isdigit() and int(k[1:]) < len(paths)]
    return {"quality_meta": {"finalGrade": "R" if reasons else "G", "reasons": reasons, "review": ""},
            "item_meta": {"intent": intents, "content_category": cats},
            "probs": {"meta": {k: round(noul("q_" + k), 3) for k in D.QUALITY_METAS},
                      "intent": [[n, round(p, 3)] for p, n in scored[:5]]}}


def judge(content: dict, gate: float = NOUL_GATE, post=None, key: str = "") -> dict:
    """한 건 판정. 반환 Output 모양 + trace(지연·토큰·비용) · 실패는 예외."""
    t0 = time.time()
    resp = (post or _post)({"model": MODEL, "state": _state(content), "questions": questions()}, key)
    out = to_output(resp, gate)
    tin = int((resp.get("usage") or {}).get("input_tokens") or 0)
    out["trace"] = {"latency_ms": round((time.time() - t0) * 1000), "tokens": {"in": tin, "out": 0},
                    "cost_usd": round(tin * PRICE_IN, 6), "model": resp.get("model") or MODEL}
    return out


def _env():
    """(post, key, error) · mock 서버면 가짜 응답 · 키 없으면 안내 문구."""
    if getattr(getattr(_SV, "Handler", None), "server_mock", False):
        return _mock_post, "", ""
    k = _key()
    return _post, k, ("" if k else "Upstage 키가 없습니다 · 시스템 설정에서 Solar 키를 등록하세요")


def try_one(body: dict) -> dict:
    """한 건 시험(제목·본문 직접 입력) · 접근 권한·응답 모양 확인용."""
    post, key, err = _env()
    if err:
        return {"ok": False, "error": err}
    try:
        out = judge({"title": body.get("title"), "body": body.get("body")}, float(body.get("gate") or NOUL_GATE), post, key)
    except Exception as e:
        return {"ok": False, "error": f"호출 실패 · {e}"}
    return {"ok": True, **out, "meta_ko": D.QUALITY_META_NAMES}


# ── 골든셋 일괄 시험(백그라운드 잡) ─────────────────────────────────────────
_LOCK = threading.Lock()
_RUNS: dict = {}
_SEQ = 0
REPORT_KEYS = ("n", "grade_n", "grade_accuracy", "harm_miss_rate", "reason_jaccard", "empty_rate",
               "intent_n", "intent_hit", "intent_f1", "intent_top1", "cat_n", "cat_hf1", "cat_f1",
               "cost_usd", "latency_p50_ms", "latency_p95_ms")


def start(team=None, n: int = 100, scope: str = "all", gate: float = NOUL_GATE) -> dict:
    global _SEQ
    post, key, err = _env()
    if err:
        return {"ok": False, "error": err}
    from . import evalops
    rows, _, _, err = evalops._prepare(team, "", scope, resolve_model=False)
    if rows is None:
        return {"ok": False, "error": err}
    rows = rows[:max(1, min(int(n or 100), len(rows)))]
    with _LOCK:
        _SEQ += 1
        rid = _SEQ
        _RUNS[rid] = {"running": True, "total": len(rows), "done": 0, "fails": 0, "started": time.time(),
                      "report": None, "error": "", "gate": gate}
        for k in [k for k, v in _RUNS.items() if not v["running"] and k < rid - 10]:
            _RUNS.pop(k, None)
    threading.Thread(target=_run, args=(rid, rows, gate, post, key), daemon=True).start()
    return {"ok": True, "id": rid, "total": len(rows)}


def _run(rid, rows, gate, post, key):
    run = _RUNS[rid]
    errors = []

    def one(row):
        try:
            return judge(row.get("content") or {}, gate, post, key)
        except Exception as e:
            errors.append(str(e))
            return None                          # 실패 = 빈 산출로 채점(abtest.score 규칙)
        finally:
            with _LOCK:
                run["done"] += 1

    try:
        with ThreadPoolExecutor(WORKERS) as ex:
            outs = list(ex.map(one, rows))
        m = abtest.score(rows, outs)
        rep = {k: m.get(k) for k in REPORT_KEYS}
        with _LOCK:
            run["report"] = rep
            run["fails"] = len(errors)
            run["error"] = errors[0] if errors else ""
    except Exception as e:
        with _LOCK:
            run["error"] = str(e)
    finally:
        with _LOCK:
            run["running"] = False
            run["elapsed_s"] = round(time.time() - run["started"], 1)


def status(run_id) -> dict:
    try:
        rid = int(run_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "잘못된 id"}
    with _LOCK:
        run = _RUNS.get(rid)
        if not run:
            return {"ok": False, "error": "만료된 실행입니다 · 다시 실행하세요"}
        return {"ok": True, "id": rid, **{k: v for k, v in run.items() if k != "started"}}
