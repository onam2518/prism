"""솔라 디사이드 판정 시험 (실험실 · 운영자 전용).

Upstage Solar Decide 는 글을 쓰지 않고 타입이 정해진 판정(choice·score·noul)과 확률만 돌려주는
판정 전용 모델이다(TypeSafe Jev 와 같은 /v1/systemone 형식). LLM 파이프라인처럼 메타를 전부 만들게 하지 않고,
'먼저 싸게 판정 → 자신 있는 건만 자동 처리'하는 앞단 판정기로 시험한다.

호출 구조(2026-10-06 운영 실측 근거):
  · 과금은 질문마다 state(본문)를 다시 센다 — noul 17개는 본문 증가분 ×17, choice 1개는 ×1
    → 예/아니오 여러 개 대신 **choice(확률 분포)** 로 묻고 질문 수를 줄인다(질문 101개 → 7개 안팎)
  · 요청 ① 본문 전체: 품질 등급 choice 1개(메타 11종 + 문제 없음)
    요청 ② 제목+앞부분 LEAD_MAX 자: 인텐트 choice 4개(보편·형식·공통 둘) + 대분류 choice 1개 · ①과 동시에
    요청 ③ 고른 대분류의 소분류 choice(1~2개) · ② 뒤
  · 라우트 제약: choice 선택지 문항당 26개(라벨=A~Z 한 글자) · 한 요청 질문 MAX_QUESTIONS 개 ·
    응답 지연 0.5~20초로 들쭉날쭉(시간 초과 한 번 재시도)
  · 정의문은 핵심 한 문장(' · ' 앞)만 · defs=False 면 이름만(토큰 절반 이하 · 2단계 비교용)
등급·인텐트·카테고리마다 확신도(conf)를 남겨 처리율·정확도 표와 확률 보정 표를 만든다(selective).
엔티티·리드문·근거 문장은 글을 써야 해서 묻지 않는다.

채점은 abtest.score 단일 소스를 그대로 쓴다 — 산출을 파이프라인 Output 모양으로 되돌려 넘긴다.
실행은 백그라운드 잡 + 폴링 · 결과는 메모리에만 둔다.
# ponytail: 잡 결과 메모리 보관(재배포 시 사라짐) · 기준 모델과 같은 표본 비교가 필요해지면 eval_runs 에 적재
# ponytail: 확률 기준을 같은 표본으로 고르고 재므로 낙관적 · 운영 적용 전엔 골든을 보정용/검증용으로 나눈다
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
from . import metaeval as ME

_SV = None                                   # serve 주입(learnops 관례)

URL = os.environ.get("PRISM_DECIDE_URL", "https://api.upstage.ai/v1/systemone")
MODEL = "solar-decide"
PRICE_IN = 0.10 / 1e6                        # ponytail: 정가 추정($0.10/M · 할인가 $0.05) · 콘솔 공시 확인 후 교체
GATE = 0.5                                   # R 판정선: 1 - P(문제 없음) 이 이 값 이상이면 R(화면에서 바꿀 수 있음)
PICK_MIN = 0.3                               # 인텐트·사유·대분류 2순위를 붙이는 최소 확률
INTENT_MAX = 3                               # 골든 건당 평균 1.9개 · 최대 4개
CAT_SECOND = PICK_MIN
BODY_MAX = 8000                              # 요청 ①(유해 판정)만 본문 전체에 가깝게
LEAD_MAX = 1500                              # 요청 ②③(인텐트·카테고리)은 앞부분만 — 질문마다 본문을 다시 과금하므로
WORKERS = 4                                  # 건 동시 수(건마다 요청 2개 동시 → 실제 동시 요청 약 8)
TIMEOUT = 60                                 # 요청 하나 · 초과 시 한 번 재시도
MAX_QUESTIONS = 100                          # 한 요청 질문 상한(초과 시 422)
THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99)
_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"         # choice 라벨 = 한 글자(라우트가 한 토큰 라벨 26개까지만 받는다)
_NONE = "Z"                                  # 모든 choice 의 '해당 없음'(없으면 범위 밖에도 확신 높게 아무거나 고른다)


def _short(desc: str) -> str:
    """정의문 핵심 한 문장: ' · ' 앞(뒤는 미부여 예외 규칙 · 판정 모델은 긴 규칙에 약하다)."""
    return str(desc or "").split(" · ")[0].strip()


def _choice(instr: str, items: list, defs: bool, none_text: str) -> dict:
    """items = [(이름, 정의문)] → choice 문항(라벨 A~ · 마지막 Z=해당 없음). 25개 초과는 호출자가 나눈다."""
    assert len(items) <= 25, len(items)
    crit = {_LABELS[i]: (f"{n}: {_short(d)}" if defs and _short(d) else n) for i, (n, d) in enumerate(items)}
    crit[_NONE] = none_text
    return {"type": "choice", "instructions": instr, "criteria": crit}


def _intent_groups() -> list:
    """인텐트 68 = 보편 10 · 형식 8 · 공통 50(25개씩 둘) → choice 4문항의 (키, 안내, 이름 목록)."""
    com = list(D.INTENT_CATEGORIES_COMMON)
    return [("int_u", "이 콘텐츠의 성격(보편 인텐트)으로 가장 알맞은 것은?", list(D.INTENT_CATEGORIES_UNIVERSAL)),
            ("int_f", "이 콘텐츠의 형식으로 가장 알맞은 것은?", list(D.INTENT_FORM_UNIVERSAL)),
            ("int_c1", "이 콘텐츠의 인텐트로 가장 알맞은 것은?", com[:25]),
            ("int_c2", "이 콘텐츠의 인텐트로 가장 알맞은 것은?", com[25:])]


def grade_question(defs: bool = True) -> dict:
    items = [(D.QUALITY_META_NAMES.get(k, k), v.split(":", 1)[-1]) for k, v in D.QUALITY_METAS.items()]
    return {"grade": _choice("이 콘텐츠에 있는 가장 큰 품질 문제는?", items, defs, "문제 없음 · 위 어디에도 해당하지 않는 정상 콘텐츠")}


def lead_questions(defs: bool = True) -> dict:
    """요청 ②: 인텐트 4 + 대분류 1."""
    q = {k: _choice(instr, [(n, D.INTENT_VALUE_DEFS.get(n, "")) for n in names], defs, "해당 없음")
         for k, instr, names in _intent_groups()}
    q["cat1"] = _choice("이 콘텐츠의 주제 대분류는?", [(t1, D.IAB_TIER1_DESC.get(t1, "")) for t1 in D.CONTENT_CATEGORY_TIER2],
                        defs, "어느 카테고리에도 해당하지 않는다")
    return q


def questions(defs: bool = True) -> dict:
    """요청 ①②의 질문 전부(테스트·점검용)."""
    return {**grade_question(defs), **lead_questions(defs)}


def cat2_questions(idxs, defs: bool = True) -> dict:
    """요청 ③: 고른 대분류(인덱스)의 소분류 choice."""
    t1s = list(D.CONTENT_CATEGORY_TIER2)
    return {f"cat2_{i}": _choice(f"이 콘텐츠의 대분류는 {t1s[i]}이다. 소분류는?",
                                 [(t2, (D.TIER2_DEFS.get(t2) or ("",))[0]) for t2 in D.CONTENT_CATEGORY_TIER2[t1s[i]]],
                                 defs, "해당 없음") for i in idxs}


def _probs(ans: dict) -> dict:
    q = ans or {}
    return {k: float(v or 0) for k, v in (q.get("probabilities") or {q.get("choice"): 1.0}).items() if k}


def _ranked(ans: dict, n_opts: int) -> list:
    """(확률, 라벨) 높은 순 · 해당 없음·범위 밖 라벨 제외."""
    return sorted(((p, k) for k, p in _probs(ans).items() if k != _NONE and k in _LABELS[:n_opts]), reverse=True)


def cat1_picks(answers: dict) -> list:
    """대분류 인덱스: 1위 + (2위 확률 ≥ CAT_SECOND 면) 2위 · '해당 없음'은 뺀다."""
    top = _ranked(answers.get("cat1") or {}, len(D.CONTENT_CATEGORY_TIER2))
    return [_LABELS.index(k) for r, (p, k) in enumerate(top[:2]) if not r or p >= CAT_SECOND]


def to_output(resp: dict, gate: float = GATE) -> dict:
    """systemone 답 → 파이프라인 Output 모양(quality_meta·item_meta) + 확신도(conf) · abtest.score 가 그대로 채점."""
    a = resp.get("answers") or {}
    metas = list(D.QUALITY_METAS)
    g = _probs(a.get("grade"))
    p_none = g.get(_NONE, 0.0) if g else 1.0
    rk = _ranked(a.get("grade") or {}, len(metas))
    is_r = (1 - p_none) >= gate
    reasons = [metas[_LABELS.index(k)] for r, (p, k) in enumerate(rk[:3]) if not r or p >= PICK_MIN] if is_r and rk else []
    ints = []
    for key, _, names in _intent_groups():
        ints += [(p, names[_LABELS.index(k)]) for p, k in _ranked(a.get(key) or {}, len(names))]
    ints.sort(reverse=True)
    intents = [n for p, n in ints if p >= PICK_MIN][:INTENT_MAX] or [n for _, n in ints[:1]]
    t1s = list(D.CONTENT_CATEGORY_TIER2)
    cats = []
    for i in cat1_picks(a):
        t2s = D.CONTENT_CATEGORY_TIER2[t1s[i]]
        sub2 = _ranked(a.get(f"cat2_{i}") or {}, len(t2s))
        if sub2:
            cats.append(f"{t1s[i]} / {t2s[_LABELS.index(sub2[0][1])]}")
    c1 = _ranked(a.get("cat1") or {}, len(t1s))
    return {"quality_meta": {"finalGrade": "R" if is_r else "G", "reasons": reasons, "review": ""},
            "item_meta": {"intent": intents, "content_category": cats},
            "conf": {"grade": round(max(p_none, 1 - p_none), 4),
                     "intent": round(ints[0][0], 4) if ints else 0.0,
                     "cat": round(c1[0][0], 4) if c1 else 0.0},
            "probs": {"meta": {**{metas[_LABELS.index(k)]: round(p, 3) for p, k in rk}, "none": round(p_none, 3)},
                      "intent": [[n, round(p, 3)] for p, n in ints[:5]],
                      "cat1": [[t1s[_LABELS.index(k)], round(p, 3)] for p, k in c1[:3]]}}


def _state(content: dict, cap: int) -> dict:
    c = content or {}
    return {"title": str(c.get("title") or ""), "subtitle": str(c.get("subtitle") or ""),
            "body": str(c.get("body") or "")[:cap]}


def judge(content: dict, gate: float = GATE, post=None, key: str = "", defs: bool = True) -> dict:
    """한 건 판정: 요청 ①②를 동시에, ③은 ② 뒤. 반환 Output 모양 + conf + trace · 실패는 예외."""
    t0 = time.time()
    post = post or _post
    call = lambda st, q: post({"model": MODEL, "state": st, "questions": q}, key)
    lead = _state(content, LEAD_MAX)
    with ThreadPoolExecutor(2) as ex:
        f1 = ex.submit(call, _state(content, BODY_MAX), grade_question(defs))
        f2 = ex.submit(call, lead, lead_questions(defs))
        resps = [f1.result(), f2.result()]
    a = {k: v for r in resps for k, v in (r.get("answers") or {}).items()}
    picks = cat1_picks(a)
    if picks:
        resps.append(call(lead, cat2_questions(picks, defs)))
        a.update(resps[-1].get("answers") or {})
    tin = sum(int((r.get("usage") or {}).get("input_tokens") or 0) for r in resps)
    out = to_output({"answers": a}, gate)
    out["trace"] = {"latency_ms": round((time.time() - t0) * 1000), "tokens": {"in": tin, "out": 0},
                    "cost_usd": round(tin * PRICE_IN, 6), "model": resps[0].get("model") or MODEL,
                    "requests": len(resps), "questions": sum(1 for _ in a)}
    return out


def selective(pairs: list) -> dict:
    """[(확신도, 맞음)] → 처리율·정확도(확률 기준별) + 확률 보정(구간별 평균 확신 대 실제 정확도) + ECE.
    처리율 = 기준 이상인 건 비율 · 자동 처리한다면 그만큼을 이 정확도로 처리한다는 뜻."""
    n = len(pairs)
    rows = []
    for t in THRESHOLDS:
        sel = [ok for c, ok in pairs if c >= t]
        rows.append({"t": t, "n": len(sel), "coverage": round(len(sel) / n, 4) if n else 0,
                     "acc": round(sum(sel) / len(sel), 4) if sel else None})
    bins, ece = [], 0.0
    for b in range(10):
        lo, hi = b / 10, (b + 1) / 10
        inb = [(c, ok) for c, ok in pairs if lo <= c < hi or (b == 9 and c == 1.0)]
        if not inb:
            continue
        conf = sum(c for c, _ in inb) / len(inb)
        acc = sum(ok for _, ok in inb) / len(inb)
        ece += len(inb) / n * abs(acc - conf)
        bins.append({"lo": lo, "hi": hi, "n": len(inb), "conf": round(conf, 4), "acc": round(acc, 4)})
    return {"n": n, "acc": round(sum(ok for _, ok in pairs) / n, 4) if n else None,
            "rows": rows, "bins": bins, "ece": round(ece, 4) if n else None}


def selective_report(rows: list, outs: list) -> dict:
    """골든 정답 대비 축별 (확신도, 맞음) 수집 → selective. 등급=G/R 일치 · 인텐트=첫 값이 정답에 있음 ·
    카테고리=대분류 1위가 정답 대분류에 있음. 산출 실패(None)·정답 없는 축은 뺀다."""
    gp, ip, cp = [], [], []
    for row, out in zip(rows, outs):
        if not out:
            continue
        exp = row.get("expected") or {}
        cf = out.get("conf") or {}
        if exp.get("finalGrade") in ("G", "R"):
            gp.append((cf.get("grade", 0), out["quality_meta"]["finalGrade"] == exp["finalGrade"]))
        want = abtest.intent_expected(exp)
        got = out["item_meta"]["intent"]
        if want and exp.get("intent_review") != "needed" and got:
            ip.append((cf.get("intent", 0), got[0] in want))
        wt1 = {c.split(" / ")[0] for c in ME._cats(exp.get("content_category"))}
        top = (out.get("probs") or {}).get("cat1") or []
        if wt1 and top:
            cp.append((cf.get("cat", 0), top[0][0] in wt1))
    return {"grade": selective(gp), "intent": selective(ip), "cat": selective(cp)}


def _key() -> str:
    return os.environ.get("UPSTAGE_API_KEY") or C.Config.load().api_key or ""


def _post(body: dict, key: str, retry: int = 1) -> dict:
    req = urllib.request.Request(URL, data=json.dumps(body, ensure_ascii=False).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:     # 4xx 본문에 원인(베타 권한·형식 오류)이 있다 → 화면에 그대로
        raise RuntimeError(f"HTTP {e.code} · {e.read().decode(errors='replace')[:300]}") from None
    except (TimeoutError, urllib.error.URLError) as e:   # 응답 지연이 들쭉날쭉해 가끔 늦는다 → 한 번 더
        if retry > 0:
            return _post(body, key, retry - 1)
        raise RuntimeError(f"응답 시간 초과({TIMEOUT}초 · 재시도 후) · {e}") from None


def _mock_post(body: dict, key: str) -> dict:
    """--mock 서버용 가짜 응답(화면 확인용) · 등급은 광고성 R · 그 밖 choice 는 첫 선택지."""
    ans = {}
    for k in body["questions"]:
        pr = {"A": 0.7, "Z": 0.3} if k == "grade" else {"A": 0.8, "B": 0.15, "Z": 0.05}
        ans[k] = {"type": "choice", "choice": "A", "probabilities": pr}
    return {"model": MODEL + "-mock", "answers": ans, "usage": {"input_tokens": 1000, "output_tokens": 0}}


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
        out = judge({"title": body.get("title"), "body": body.get("body")}, float(body.get("gate") or GATE), post, key,
                    defs=body.get("defs", True) is not False)
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


def start(team=None, n: int = 100, scope: str = "all", gate: float = GATE, defs: bool = True) -> dict:
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
        judge(rows[0].get("content") or {}, gate, post, key, defs)
    except Exception as e:
        return {"ok": False, "error": f"첫 건 호출 실패 · {e}"}
    with _LOCK:
        _SEQ += 1
        rid = _SEQ
        _RUNS[rid] = {"running": True, "total": len(rows), "done": 0, "fails": 0, "started": time.time(),
                      "report": None, "error": "", "gate": gate, "defs": defs, "team": team}
        for k in [k for k, v in _RUNS.items() if not v["running"] and k < rid - 10]:
            _RUNS.pop(k, None)
    threading.Thread(target=_run, args=(rid, rows, gate, post, key, defs), daemon=True).start()
    return {"ok": True, "id": rid, "total": len(rows)}


def _run(rid, rows, gate, post, key, defs=True):
    run = _RUNS[rid]
    errors = []

    def one(row):
        try:
            return judge(row.get("content") or {}, gate, post, key, defs)
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
        rep["selective"] = selective_report(rows, outs)
        tq = [o["trace"] for o in outs if o]
        rep["tokens_per_item"] = round(sum(t["tokens"]["in"] for t in tq) / len(tq)) if tq else None
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


def status(run_id, team=None) -> dict:
    try:
        rid = int(run_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "잘못된 id"}
    with _LOCK:
        run = _RUNS.get(rid)
        if not run or run.get("team") != team:     # 다른 팀 실행은 없는 것과 같다(entrefine.status 와 동일)
            return {"ok": False, "error": "만료된 실행입니다 · 다시 실행하세요"}
        return {"ok": True, "id": rid, **{k: v for k, v in run.items() if k not in ("started", "team")}}
