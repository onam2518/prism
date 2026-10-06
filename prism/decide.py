"""솔라 디사이드 판정 시험 (실험실 · 운영자 전용).

Upstage Solar Decide 는 글을 쓰지 않고 타입이 정해진 판정(choice·score·noul)과 확률만 돌려주는
판정 전용 모델이다(TypeSafe Jev 와 같은 /v1/systemone 형식). 그래서 맡길 수 있는 것만 묻는다:
  · 품질 메타 11종 → noul 각각 · 하나라도 임계 이상이면 R (파이프라인 등급 규칙과 같음)
  · 인텐트 68종  → noul 각각 · 임계 이상 상위 INTENT_MAX 개(없으면 최고 1개)
  · 카테고리     → 1차(21종+해당 없음) choice 를 위와 같은 호출에 · 고른 1차의 2차 choice 는 두 번째 호출로 묻는다
                  (1차 2순위 확률이 CAT_SECOND 이상이면 그 경로도 함께)
라우트 제약(운영 422 로 확인): choice 선택지는 한 문항 26개(라벨=A~Z 한 글자) · 한 요청 질문 MAX_QUESTIONS 개.
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
MAX_QUESTIONS = 100                          # 한 요청 질문 상한(초과 시 422)
_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"         # choice 라벨 = 한 글자(라우트가 한 토큰 라벨 26개까지만 받는다)
_NONE = "Z"                                  # 1차 카테고리 '해당 없음'(없으면 범위 밖에도 확신 높게 아무거나 고른다)


def questions() -> dict:
    """1차 호출 질문(품질 메타·인텐트·대분류). 키는 ASCII(q_·i·cat 접두 + 순번) · 정의문 원천은 dictionaries 하나뿐."""
    q = {}
    for k, desc in D.QUALITY_METAS.items():
        q["q_" + k] = {"type": "noul", "instructions": "이 콘텐츠가 다음에 해당하는가? " + desc}
    for i, (name, desc) in enumerate(D.INTENT_VALUE_DEFS.items()):
        q[f"i{i}"] = {"type": "noul", "instructions": f"이 콘텐츠에 인텐트 '{name}'을(를) 붙이는가? 정의: {desc}"}
    t1s = list(D.CONTENT_CATEGORY_TIER2)
    crit = {_LABELS[i]: f"{t1}: {D.IAB_TIER1_DESC.get(t1, '')}".rstrip(": ") for i, t1 in enumerate(t1s)}
    crit[_NONE] = "어느 카테고리에도 해당하지 않는다"
    q["cat1"] = {"type": "choice", "instructions": "이 콘텐츠의 주제 대분류는?", "criteria": crit}
    return q


def cat2_questions(idxs) -> dict:
    """2차 호출 질문: 고른 대분류(인덱스)의 소분류 choice."""
    t1s = list(D.CONTENT_CATEGORY_TIER2)
    return {f"cat2_{i}": {"type": "choice", "instructions": f"이 콘텐츠의 대분류는 {t1s[i]}이다. 소분류는?",
                          "criteria": {_LABELS[j]: f"{t2}: {(D.TIER2_DEFS.get(t2) or ('',))[0]}".rstrip(": ")
                                       for j, t2 in enumerate(D.CONTENT_CATEGORY_TIER2[t1s[i]])}}
            for i in idxs}


def _ranked(q: dict) -> list:
    return sorted(((v or 0, k) for k, v in (q.get("probabilities") or {q.get("choice"): 1.0}).items() if k), reverse=True)


def cat1_picks(answers: dict) -> list:
    """대분류 인덱스: 1위 + (2위 확률 ≥ CAT_SECOND 면) 2위 · '해당 없음'은 뺀다."""
    n = len(D.CONTENT_CATEGORY_TIER2)
    top = [(p, k) for p, k in _ranked(answers.get("cat1") or {}) if k != _NONE and k in _LABELS[:n]]
    return [_LABELS.index(k) for r, (p, k) in enumerate(top[:2]) if not r or p >= CAT_SECOND]


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
            ans[k] = {"type": "choice", "choice": "A", "probabilities": {"A": 0.8, "B": 0.2}}
    return {"model": MODEL + "-mock", "answers": ans, "usage": {"input_tokens": 1000, "output_tokens": 0}}


def to_output(resp: dict, gate: float = NOUL_GATE) -> dict:
    """systemone 응답 → 파이프라인 Output 모양(quality_meta·item_meta) · abtest.score 가 그대로 채점."""
    a = resp.get("answers") or {}
    noul = lambda k: float((a.get(k) or {}).get("noul") or 0.0)
    reasons = [k for k in D.QUALITY_METAS if noul("q_" + k) >= gate]
    names = list(D.INTENT_VALUE_DEFS)
    scored = sorted(((noul(f"i{i}"), n) for i, n in enumerate(names)), reverse=True)
    intents = [n for p, n in scored if p >= gate][:INTENT_MAX] or ([scored[0][1]] if scored else [])
    t1s = list(D.CONTENT_CATEGORY_TIER2)
    cats = []
    for i in cat1_picks(a):
        t2s = D.CONTENT_CATEGORY_TIER2[t1s[i]]
        sub2 = [k2 for _, k2 in _ranked(a.get(f"cat2_{i}") or {}) if k2 in _LABELS[:len(t2s)]]
        if sub2:
            cats.append(f"{t1s[i]} / {t2s[_LABELS.index(sub2[0])]}")
    return {"quality_meta": {"finalGrade": "R" if reasons else "G", "reasons": reasons, "review": ""},
            "item_meta": {"intent": intents, "content_category": cats},
            "probs": {"meta": {k: round(noul("q_" + k), 3) for k in D.QUALITY_METAS},
                      "intent": [[n, round(p, 3)] for p, n in scored[:5]]}}


def judge(content: dict, gate: float = NOUL_GATE, post=None, key: str = "") -> dict:
    """한 건 판정. 반환 Output 모양 + trace(지연·토큰·비용) · 실패는 예외."""
    t0 = time.time()
    post, state = post or _post, _state(content)
    resp = post({"model": MODEL, "state": state, "questions": questions()}, key)
    a = dict(resp.get("answers") or {})
    tin = int((resp.get("usage") or {}).get("input_tokens") or 0)
    picks = cat1_picks(a)
    if picks:                                    # 소분류는 고른 대분류만 두 번째 호출로(한 요청 질문 상한 때문에)
        r2 = post({"model": MODEL, "state": state, "questions": cat2_questions(picks)}, key)
        a.update(r2.get("answers") or {})
        tin += int((r2.get("usage") or {}).get("input_tokens") or 0)
    out = to_output({**resp, "answers": a}, gate)
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
    try:                                         # 첫 건을 먼저 불러 형식·권한 오류면 바로 멈춘다(100건 헛호출 방지)
        judge(rows[0].get("content") or {}, gate, post, key)
    except Exception as e:
        return {"ok": False, "error": f"첫 건 호출 실패 · {e}"}
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
