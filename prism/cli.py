"""Prism CLI."""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from .llm import LLMClient
from .embed import EmbeddingClient
from .config import Config
from .ratelimit import RateLimiter
from .store import Store, content_hash
from . import pipeline as PIPE
from . import dashboard as DASH
from . import classify as C
from . import usermeta as UM

HOME = os.path.join(os.path.dirname(os.path.dirname(__file__)))


def _mk_cfg(a):
    cfg = Config.load(getattr(a, "config", None))
    if getattr(a, "base_url", None):
        cfg.set_base_url(a.base_url)
    if getattr(a, "model", None):
        cfg.model = a.model
    if getattr(a, "reasoning", None):
        cfg.reasoning_effort = a.reasoning
    if getattr(a, "concurrency", None):
        cfg.concurrency = a.concurrency
    return cfg


_SETUP_MSG = (
    "⚙️  먼저 설정이 필요합니다.\n"
    "    python3 -m prism.cli init --base-url <엔드포인트> --model <모델>\n"
    "    export PRISM_API_KEY=...\n"
    "    (키 없이 체험하려면 명령에 --mock 을 붙이세요)")


def _mk_llm(a, cfg=None, limiter=None):
    cfg = cfg or _mk_cfg(a)
    if not a.mock and not cfg.is_configured():
        print(_SETUP_MSG)
        raise SystemExit(2)
    return LLMClient(mock=a.mock, config=cfg, limiter=limiter)


def _mk_emb(a, cfg=None):
    if getattr(a, "embed", "on") == "off":
        return None
    cfg = cfg or _mk_cfg(a)
    return EmbeddingClient(mock=a.mock, cache_path=cfg.emb_cache_path)


def _mk_prefilter(a, emb):
    path = getattr(a, "prefilter", None)
    if not path or emb is None:
        return None
    rows = _read_jsonl(path)
    return C.QualityExemplars(emb).fit(rows)


def _run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%S", time.localtime())


def _mk_fewshot(a):
    path = getattr(a, "fewshot", None)
    if not path:
        return None
    from .fewshot import FewShotPool
    return FewShotPool.from_jsonl(path)


def _extract_kwargs(a, cfg, emb, prefilter):
    return dict(legal=a.legal, quality_split=a.quality_split,
                emb=emb, quality_prefilter=prefilter, fewshot_pool=_mk_fewshot(a),
                slim=getattr(a, "slim", False), yellow=getattr(a, "yellow", False),
                prefilter_conf=cfg.thresholds.prefilter_conf,
                yellow_low=cfg.thresholds.yellow_low)


def cmd_extract(a):
    cfg = _mk_cfg(a)
    limiter = RateLimiter(cfg.rate.rpm, cfg.rate.tpm)
    llm = _mk_llm(a, cfg, limiter)
    emb = _mk_emb(a, cfg)
    prefilter = _mk_prefilter(a, emb)
    kw = _extract_kwargs(a, cfg, emb, prefilter)
    if getattr(a, "yellow", False) and prefilter is None:
        print("  ⚠️ --yellow 는 2차의견용 --prefilter <labeled.jsonl> 가 필요합니다(없으면 YELLOW 미작동)")

    if a.input:
        with open(a.input, encoding="utf-8") as f:
            content = json.load(f)
        out = PIPE.extract(content, llm, **kw)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        if emb:
            emb.flush()
        return

    if a.batch:
        out_path = a.out or "results.jsonl"
        _extract_batch(a, cfg, llm, emb, kw, out_path)


def _load_batch(path, map_str=None):
    """배치 입력 적재: jsonl 은 그대로, xlsx/csv 는 ingest 로 매핑. 판정 불가면 즉시 중단."""
    import os
    from . import ingest as ING
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jsonl", ".ndjson"):
        return [r.get("content", r) for r in _read_jsonl(path)]
    override = ING.parse_map(map_str)
    rep = ING.assess(path, override)
    if not rep["ok"]:
        print(f"  ✗ 입력 적용 불가: {rep['reason']}")
        print(f"    발견 컬럼: {rep['headers']}")
        raise SystemExit(2)
    print("  · 입력 매핑: " + " · ".join(f"{k}←{v}" for k, v in rep["mapping"].items()))
    return ING.to_contents(path, override)


def cmd_check(a):
    """엑셀/CSV 가 우리 스키마로 동작 가능한지 판정(가능/불가능 + 추론 매핑)."""
    from . import ingest as ING
    rep = ING.assess(a.file, ING.parse_map(getattr(a, "map", None)))
    mark = "\033[32m가능\033[0m" if rep["ok"] else "\033[31m불가능\033[0m"
    print(f"\n[{mark}]  {a.file}  ·  {rep['n_rows']}행")
    print(f"  {rep['reason']}")
    print(f"  발견 컬럼: {rep['headers']}")
    print("  추론 매핑:")
    for f in ("title", "body", "subtitle", "displayServiceName"):
        v = rep["mapping"].get(f)
        print(f"    {f:20} ← {v if v else '(없음, 기본값 사용)' if f not in ING.REQUIRED else '✗ 필수 미발견'}")
    if rep["samples"]:
        print("  샘플:")
        for s in rep["samples"]:
            print("    " + json.dumps(s, ensure_ascii=False))
    if not rep["ok"]:
        print("  → --map \"title=컬럼명,body=컬럼명\" 으로 지정하면 동작합니다.")


def _extract_batch(a, cfg, llm, emb, kw, out_path):
    """배치 추출 본체: extract/report 공용. out_path 에 results.jsonl 기록, metrics 반환."""
    rows = _load_batch(a.batch, getattr(a, "map", None))
    store = None if a.no_db else Store(cfg.db_path)
    run_id = _run_id()

    # 재개(resume): DB 에 이미 성공 처리된 건은 skip
    skip = set()
    if store and a.resume:
        done = store.done_hashes(only_ok=True)
        skip = {i for i, r in enumerate(rows) if content_hash(r) in done}
    todo = [i for i in range(len(rows)) if i not in skip]

    results = [None] * len(rows)
    errors = []
    t0 = time.time()
    if store:
        store.start_run(run_id, len(rows), cfg.redacted())

    def work(i):
        # per-item 격리 + 증분 DB 저장(중단 내성)
        try:
            results[i] = PIPE.extract(rows[i], llm, **kw)
        except Exception as e:
            errors.append((i, str(e)))
            results[i] = {"content_ref": {"title": rows[i].get("title", "")},
                          "quality_meta": {"finalGrade": "G", "reasons": []},
                          "item_meta": None,
                          "trace": {"fallbacks": [f"extract 예외: {e}"],
                                    "cost_usd": 0, "tokens": {"in": 0, "out": 0}}}
        if store:
            try:
                store.save_result(rows[i], results[i], run_id)
            except Exception:
                pass

    with ThreadPoolExecutor(max_workers=cfg.concurrency) as ex:
        list(ex.map(work, todo))
    if emb:
        emb.flush()

    # resume 로 skip 한 건은 DB 에서 기존 결과를 회수해 출력 일관성 유지
    if skip and store:
        want = {content_hash(rows[i]): i for i in skip}
        cached = store.get_by_hashes(want.keys())
        for h, payload in cached.items():
            results[want[h]] = payload

    with open(out_path, "w", encoding="utf-8") as f:
        for r in results:
            if r is not None:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 메트릭·실패분류 집계 + run manifest
    done_results = [r for r in results if r is not None]
    # 신규 처리분(todo)만의 비용/토큰: usage(증분 지출)용. resume 캐시는 제외.
    new_cost = sum(results[i].get("trace", {}).get("cost_usd", 0)
                   for i in todo if results[i] is not None)
    new_tin = sum(results[i].get("trace", {}).get("tokens", {}).get("in", 0)
                  for i in todo if results[i] is not None)
    new_tout = sum(results[i].get("trace", {}).get("tokens", {}).get("out", 0)
                   for i in todo if results[i] is not None)
    cost = sum(r.get("trace", {}).get("cost_usd", 0) for r in done_results)
    tin = sum(r.get("trace", {}).get("tokens", {}).get("in", 0) for r in done_results)
    tout = sum(r.get("trace", {}).get("tokens", {}).get("out", 0) for r in done_results)
    # 임베딩 비용은 공유 클라이언트라 배치 단위로 1회만 합산(중복계상 방지)
    emb_cost = emb.cost_usd if emb is not None else 0.0
    emb_tok = emb.tokens if emb is not None else 0
    cost += emb_cost
    new_cost += emb_cost
    dt = time.time() - t0
    g = sum(1 for r in done_results if r.get("quality_meta", {}).get("finalGrade") == "G")
    metrics = {
        "n_total": len(rows), "n_processed": len(todo), "n_skipped": len(skip),
        "G": g, "R": len(done_results) - g, "cost_usd": round(cost, 6),
        "cost_breakdown": {"llm": round(cost - emb_cost, 6), "embedding": round(emb_cost, 6)},
        "tokens": {"in": tin, "out": tout, "embedding": emb_tok},
        "elapsed_s": round(dt, 1),
        "throughput_per_s": round(len(todo) / dt, 2) if dt else 0,
        "fail_breakdown": dict(llm.fail_counts), "exceptions": len(errors),
    }
    if store:
        store.finish_run(run_id, metrics)
        store.log_usage("extract", len(todo), new_cost, new_tin, new_tout)
    _write_manifest(cfg, run_id, "extract", metrics)

    print(f"✓ {len(todo)}건 처리"
          + (f" (+{len(skip)}건 resume skip)" if skip else "") + f" → {out_path}")
    print(f"  G {g} / R {len(done_results)-g}  ·  비용 ${cost:.4f}  ·  "
          f"{dt:.1f}s ({metrics['throughput_per_s']}건/s)  ·  토큰 in {tin:,} out {tout:,}")
    if llm.fail_counts or errors:
        print(f"  ⚠️ 실패분류 {dict(llm.fail_counts)} · 예외 {len(errors)}건 (격리·배치 완주)")
    print(f"  run manifest: runs/{run_id}.json  ·  DB: {('off' if a.no_db else cfg.db_path)}")
    return metrics


def _write_manifest(cfg, run_id, kind, metrics):
    os.makedirs(cfg.runs_dir, exist_ok=True)
    man = {"run_id": run_id, "kind": kind, "ts": time.time(),
           "config": cfg.redacted(), "metrics": metrics}
    with open(os.path.join(cfg.runs_dir, f"{run_id}.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)


def cmd_eval(a):
    cfg = _mk_cfg(a)
    limiter = RateLimiter(cfg.rate.rpm, cfg.rate.tpm)
    llm = _mk_llm(a, cfg, limiter)
    emb = _mk_emb(a, cfg)
    prefilter = _mk_prefilter(a, emb)
    rows = _read_jsonl(a.goldenset)
    n = len(rows)
    metrics = _eval_with(rows, llm, emb, prefilter, a)
    # 임베딩 비용 1회 합산(per-content 중복 방지)
    if emb is not None:
        metrics["cost_usd"] = round(metrics["cost_usd"] + emb.cost_usd, 6)
    metrics["fail_breakdown"] = dict(llm.fail_counts)
    if emb:
        emb.flush()
    print(json.dumps({k: v for k, v in metrics.items() if k != "cases"},
                     ensure_ascii=False, indent=2))
    acc = metrics["grade_accuracy"]
    gate = cfg.thresholds.eval_gate
    print(f"\n[게이트] 등급 일치율 {acc:.1%} · 유해 미탐률 {metrics['harm_miss_rate']:.1%}")
    if acc < gate:
        print(f"  → 임계({gate:.0%}) 미달. §6.4 에스컬레이션 검토: (a)하이브리드 (b)분해형 (c)모델교체")
    run_id = _run_id()
    _write_manifest(cfg, run_id, "eval", metrics)
    if not a.no_db:
        Store(cfg.db_path).log_usage("eval", n, metrics["cost_usd"],
                                     metrics.get("tokens", {}).get("in", 0),
                                     metrics.get("tokens", {}).get("out", 0))
    print(f"  run manifest: runs/{run_id}.json")


def _methodology_from_args(a):
    """argparse 손잡이 → harness.Methodology(평가/A·B 공유)."""
    cfg2 = _mk_cfg(a)
    return PIPE.Methodology(
        name="cli", legal=a.legal, quality_split=a.quality_split,
        yellow=getattr(a, "yellow", False),
        prefilter_conf=cfg2.thresholds.prefilter_conf,
        yellow_low=cfg2.thresholds.yellow_low)


def _eval_with(rows, llm, emb, prefilter, a):
    """평가를 하네스+방법론으로 통일하고, 채점은 abtest.score 공유(단일 소스)."""
    from . import abtest
    m = _methodology_from_args(a)
    return abtest.evaluate(rows, m, llm, emb=emb, prefilter=prefilter,
                           fewshot_pool=_mk_fewshot(a),
                           concurrency=getattr(a, "concurrency", 8) or 8)


def cmd_ab(a):
    """A/B 테스트: 두 방법론을 같은 골든셋에 돌려 성능 비교(어떤 방법론을 쓸지 결정)."""
    from . import abtest
    cfg = _mk_cfg(a)
    limiter = RateLimiter(cfg.rate.rpm, cfg.rate.tpm)
    llm = _mk_llm(a, cfg, limiter)
    emb = _mk_emb(a, cfg)
    prefilter = _mk_prefilter(a, emb)
    rows = _read_jsonl(a.goldenset)
    if getattr(a, "limit", 0):
        rows = rows[:a.limit]
    meth_a = abtest.load_methodology(a.a)
    meth_b = abtest.load_methodology(a.b)
    res = abtest.ab_test(rows, meth_a, meth_b, llm, emb=emb, prefilter=prefilter,
                         fewshot_pool=_mk_fewshot(a),
                         concurrency=getattr(a, "concurrency", 8) or 8)
    am, bm = res["a"]["metrics"], res["b"]["metrics"]
    print(f"\n  A/B 테스트 · n={res['n']}  (A={res['a']['name']}  vs  B={res['b']['name']})")
    print(f"  {'지표':<22}{'A':>12}{'B':>12}{'Δ(B-A)':>12}")
    print("  " + "-" * 58)
    for k in abtest._AB_KEYS:
        av, bv = am.get(k, 0), bm.get(k, 0)
        d = res["diff"][k]
        arrow = ""
        if d:
            good = (d < 0) if k in abtest._BETTER_LOWER else (d > 0)
            arrow = " ↑좋음" if good else " ↓나쁨"
        fmt = (lambda x: f"{x:.4f}") if k == "cost_usd" else (lambda x: f"{x:.1%}")
        print(f"  {k:<22}{fmt(av):>12}{fmt(bv):>12}{(('%+.4f' % d) if k=='cost_usd' else ('%+.1f%%' % (d*100))):>12}{arrow}")
    print("  " + "-" * 58)
    win = res["winner"]
    print(f"  승자: {'A=' + res['a']['name'] if win=='a' else 'B=' + res['b']['name'] if win=='b' else '동률'}"
          f"  (등급 정확도 우선, 동률 시 유해 미탐률)")
    ec = res.get("embedding_cost_usd", 0.0)
    print(f"  ※ cost_usd 는 LLM-only. 임베딩 비용(공유, 전체 1회): ${ec:.4f}"
          + ("  ← 임베딩 사용 차이가 있는 방법론 비교 시 이 값도 함께 고려" if ec else ""))
    if emb is not None:
        try:
            emb.flush()
        except Exception:
            pass            # 캐시 저장 실패(스테일 경로 등)가 A/B 결과를 막지 않게
    run_id = _run_id()
    _write_manifest(cfg, run_id, "ab", res)
    print(f"  run manifest: runs/{run_id}.json")


def cmd_dashboard(a):
    out = a.out or "dashboard.html"
    if getattr(a, "integrated", False):
        info = DASH.build_integrated(a.results, out,
                                     title=a.title or "Prism",
                                     n_users=getattr(a, "users", 6),
                                     logs_path=getattr(a,"logs",None), demo=getattr(a,"demo",False))
        print(f"✓ 통합 대시보드(콘텐츠+사용자메타 탭) → {out}  (콘텐츠 {info['contents']})")
    else:
        info = DASH.build(a.results, out, title=a.title or "아이템 메타 현황")
        print(f"✓ 대시보드 → {out}  (콘텐츠 {info['contents']} · 엔티티 {info['entities']})")
    print(f"  열기:  open {out}")


# topic (토픽 관리 체계: 엔티티형/사건형)
def cmd_topic(a):
    from . import topic as TP
    if a.out:
        TP.build_html(a.results, a.out)
        d = TP.build_topics(a.results)["summary"]
        print(f"✓ 토픽 → {a.out}")
        print(f"  엔티티형 {d['single']} · 사건형 {d['composite']}")
        print(f"  열기:  open {a.out}")
    else:
        print(json.dumps(TP.build_topics(a.results), ensure_ascii=False, indent=2))


# report (파이프라인 → 토픽 → 대시보드 한 번에 · 크론 친화)
def _apply_profile(a):
    """회사별 설정 JSON 적용: 브랜딩(title) + 사전 override(services/categories) 시드.
    범용화 seam: 회사마다 services·taxonomy·quality metas 만 프로파일로 갈아끼우면 됨."""
    if not getattr(a, "profile", None):
        return {}
    with open(a.profile, encoding="utf-8") as f:
        prof = json.load(f)
    if prof.get("title") and not getattr(a, "title", None):
        a.title = prof["title"]
    try:
        from . import dictionaries as D
        if hasattr(D, "apply_profile"):
            D.apply_profile(prof)
    except Exception as e:
        print(f"  ⚠️ 프로파일 사전 override 일부 실패: {e}")
    return prof


def cmd_report(a):
    """extract(배치) → topic → integrated dashboard 를 한 번에. 크론으로 매일 생성 가능."""
    from . import topic as MP
    cfg = _mk_cfg(a)
    _apply_profile(a)

    results = a.results
    if a.batch:
        limiter = RateLimiter(cfg.rate.rpm, cfg.rate.tpm)
        llm = _mk_llm(a, cfg, limiter)
        emb = _mk_emb(a, cfg)
        prefilter = _mk_prefilter(a, emb)
        kw = _extract_kwargs(a, cfg, emb, prefilter)
        results = a.results or "report_results.jsonl"
        print("· [1/2] 추출 파이프라인 실행 …")
        _extract_batch(a, cfg, llm, emb, kw, results)
    if not results:
        print("  ✗ --batch(신규 추출) 또는 --results(기존 결과) 중 하나가 필요합니다")
        return

    out = a.out or "report.html"
    print("· [2/2] 토픽 + 통합 대시보드 생성 …")
    info = DASH.build_integrated(results, out, title=a.title or "Prism",
                                 n_users=getattr(a, "users", 200),
                                 logs_path=getattr(a,"logs",None), demo=getattr(a,"demo",False))
    mp = MP.build_topics(results)["summary"]
    umode = ("실데이터" if getattr(a, "logs", None)
             else ("목업" if getattr(a, "demo", False) else "미연결(빈 상태)"))
    print(f"✓ 리포트 → {out}  (콘텐츠 {info['contents']})")
    print(f"  토픽: 엔티티형 {mp['single']} · 사건형 {mp['composite']}"
          f"  ·  사용자 메타: {umode}")
    print(f"  열기:  open {out}")


# usermeta (목업)
def cmd_usermeta(a):
    print("\033[33m" + UM.WARNING + "\033[0m\n")  # 노란 경고
    if a.out:
        info = UM.build_html(a.results, a.out, n_users=a.users, logs_path=getattr(a,'logs',None), demo=getattr(a,'demo',False))
        print(f"✓ 사용자 메타 목업 → {a.out}  (합성 사용자 {info['users']}명)")
        print(f"  열기:  open {a.out}")
    else:
        data = UM.build_user_meta(a.results, n_users=a.users, logs_path=getattr(a,'logs',None), demo=getattr(a,'demo',False))
        print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_usage(a):
    cfg = _mk_cfg(a)
    if not os.path.exists(cfg.db_path):
        print("usage DB 없음 (아직 추출/평가 실행 전)")
        return
    since = 0
    if a.since:
        since = time.mktime(time.strptime(a.since, "%Y-%m-%d"))
    total_cost = 0.0
    for ts, kind, nn, cost, tin, tout in Store(cfg.db_path).usage_since(since):
        total_cost += cost or 0
        print(f"  {time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))}  "
              f"{kind:8} n={nn:<5} ${(cost or 0):.4f}  tok {tin or 0:,}/{tout or 0:,}")
    print(f"  ─ 합계 ${total_cost:.4f}")


# doctor (헬스/연결 점검)
def cmd_doctor(a):
    import urllib.request
    cfg = _mk_cfg(a)
    print("prism doctor: 운영 점검\n")
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        mark = "✓" if cond else "✗"
        if not cond:
            ok = False
        print(f"  {mark} {name}" + (f"  · {detail}" if detail else ""))

    check("설정(엔드포인트·모델)", cfg.is_configured(),
          f"{cfg.model} @ {cfg.chat_url}" if cfg.is_configured() else "init 필요 (또는 --mock)")
    check("API 키", bool(cfg.api_key), "set" if cfg.api_key else "PRISM_API_KEY 미설정 → mock만 가능")
    # 모델 가용성
    if cfg.api_key:
        try:
            req = urllib.request.Request(cfg.models_url)
            req.add_header("Authorization", f"Bearer {cfg.api_key}")
            with urllib.request.urlopen(req, timeout=20) as r:
                ids = [m.get("id") for m in json.loads(r.read()).get("data", [])]
            check(f"모델 {cfg.model}", cfg.model in ids,
                  "가용" if cfg.model in ids else f"목록에 없음 (가용: {', '.join(ids[:3])}…)")
        except Exception as e:
            check("모델 목록 조회", False, str(e)[:60])
        # 임베딩 연결
        try:
            emb = EmbeddingClient(config=cfg) if False else EmbeddingClient(mock=False, cache_path=None)
            emb.api_key = cfg.api_key
            v = emb.embed("점검", is_query=True)
            check("임베딩 엔드포인트", len(v) > 0, f"dim {len(v)}")
        except Exception as e:
            check("임베딩 엔드포인트", False, str(e)[:60])
    # DB 쓰기 가능
    try:
        Store(cfg.db_path)
        check("DB 쓰기", True, cfg.db_path)
    except Exception as e:
        check("DB 쓰기", False, str(e)[:60])
    # config / 경로
    check("config 로드", True, f"concurrency={cfg.concurrency} rpm={cfg.rate.rpm or '무제한'} tpm={cfg.rate.tpm or '무제한'}")
    check("runs 디렉터리", True, cfg.runs_dir)
    print("\n결과:", "정상 ✓" if ok else "일부 점검 실패 ✗")


def cmd_init_config(a):
    cfg = Config()
    if getattr(a, "base_url", None):
        cfg.set_base_url(a.base_url)
    if getattr(a, "model", None):
        cfg.model = a.model
    out = a.out or os.path.join(HOME, "config.json")
    cfg.save_template(out)
    print(f"✓ 설정 저장 → {out}")
    if cfg.is_configured():
        print(f"  엔드포인트: {cfg.chat_url}")
        print(f"  모델: {cfg.model}")
    else:
        print("  ⚠️ 엔드포인트·모델 미지정 · 아래처럼 다시 실행하거나 config.json 을 채우세요:")
        print("     python3 -m prism.cli init --base-url https://api.openai.com/v1 --model gpt-4o-mini")
    print("  API 키: export PRISM_API_KEY=...   (config 에는 저장하지 않음)")


# prompt (내부 프롬프트 버전 관리/편집)
def cmd_prompt(a):
    from . import promptstore as PS
    if a.action == "list":
        print("품질 프롬프트 버전:")
        for v, active in PS.list_versions():
            print(f"  {'▶' if active else ' '} {v}" + ("  (active)" if active else ""))
    elif a.action == "show":
        body = PS.get(a.version)
        print(json.dumps(body, ensure_ascii=False, indent=2))
    elif a.action == "set":
        PS.set_active(a.version)
        print(f"✓ active 품질 프롬프트 → {a.version}")
    elif a.action == "new":
        PS.new_from(a.version, a.to)
        print(f"✓ {a.to} 생성(= {a.version} 복제). prompts/quality.json 에서 편집 후 "
              f"`prompt set {a.to}` 로 활성화.")
    elif a.action == "diff":
        b1, b2 = PS.get(a.version), PS.get(a.to)
        print(f"=== {a.version} vs {a.to} (meta_rules 차이) ===")
        for m in sorted(set(b1["meta_rules"]) | set(b2["meta_rules"])):
            if b1["meta_rules"].get(m) != b2["meta_rules"].get(m):
                print(f"\n[{m}]\n  {a.version}: {b1['meta_rules'].get(m,'')[:80]}…"
                      f"\n  {a.to}: {b2['meta_rules'].get(m,'')[:80]}…")


# tune (자가수정 루프: FailureAnalyst → PromptTuner → RegressionGuard)
def cmd_tune(a):
    from . import promptstore as PS
    cfg = _mk_cfg(a)
    limiter = RateLimiter(cfg.rate.rpm, cfg.rate.tpm)
    rows = _read_jsonl(a.goldenset)

    def run_eval(version):
        PS.set_active(version)
        llm = _mk_llm(a, cfg, limiter)
        emb = _mk_emb(a, cfg)
        pf = _mk_prefilter(a, emb)
        m = _eval_with(rows, llm, emb, pf, a)
        if emb:
            emb.flush()
        return m

    base = a.base or PS.active_name()
    print(f"[1] FailureAnalyst: baseline '{base}' 평가 중…")
    base_m = run_eval(base)
    print(_fmt_metrics(base_m))
    weak = sorted(base_m["by_reason_bucket"].items(), key=lambda kv: kv[1]["grade_acc"])
    print("\n  취약 버킷(낮은 순):")
    for b, d in weak[:5]:
        print(f"    {b:10} {d['grade_acc']:.0%} (n={d['n']})")

    cand = a.candidate
    if not cand:
        print("\n[2] PromptTuner: --candidate 미지정. 위 취약 버킷을 보강한 새 버전을 "
              "prompts/quality.json 에 작성 후 `--candidate <버전>` 으로 재실행하세요.\n"
              "    (강한 메타모델=사람/Opus 가 개정. 기본 제공: v32=ad/spam 보강)")
        return

    print(f"\n[3] 후보 '{cand}' 평가 중…")
    cand_m = run_eval(cand)
    print(_fmt_metrics(cand_m))

    print("\n[4] RegressionGuard: 회귀 안전망")
    base_acc, cand_acc = base_m["grade_accuracy"], cand_m["grade_accuracy"]
    base_miss, cand_miss = base_m["harm_miss_rate"], cand_m["harm_miss_rate"]
    regress = []
    for b in base_m["by_reason_bucket"]:
        ba = base_m["by_reason_bucket"][b]["grade_acc"]
        ca = cand_m["by_reason_bucket"].get(b, {}).get("grade_acc", ba)
        if ca < ba - 0.10:
            regress.append(f"{b} {ba:.0%}→{ca:.0%}")
    # 엄격 개선: 등급이 실제로 올라가고(>), 유해미탐 악화 없고, 회귀 버킷 없을 때만 채택
    improved = cand_acc > base_acc and cand_miss <= base_miss + 1e-9 and not regress
    unchanged = abs(cand_acc - base_acc) < 1e-9 and not regress
    print(f"  등급 {base_acc:.1%}→{cand_acc:.1%} · 유해미탐 {base_miss:.1%}→{cand_miss:.1%}")
    if regress:
        print(f"  ⚠️ 회귀 버킷: {', '.join(regress)}")
    if improved and not a.dry_run:
        PS.set_active(cand)
        print(f"  ✓ 개선 확인 → 채택 active={cand}")
    elif improved:
        print(f"  ✓ 개선 확인(dry-run, 미반영). 채택하려면 `prompt set {cand}`")
        PS.set_active(base)
    elif unchanged:
        PS.set_active(base)
        print(f"  = 변화 없음 → rollback (active={base} 유지)")
    else:
        PS.set_active(base)
        print(f"  ✗ 미개선/회귀 → rollback (active={base} 유지)")


def _fmt_metrics(m):
    return (f"  등급 {m['grade_accuracy']:.1%} · reason완전 {m['reason_exact_match']:.1%} · "
            f"유해미탐 {m['harm_miss_rate']:.1%} · EMPTY {m['empty_rate']:.1%} · ${m['cost_usd']:.4f}")


# 공통
def _read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _add_common(p):
    p.add_argument("--model", default=None)
    p.add_argument("--base-url", dest="base_url", default=None,
                   help="OpenAI 호환 엔드포인트 base URL (예: https://api.openai.com/v1)")
    p.add_argument("--mock", action="store_true", help="키 없이 결정론 스텁으로 실행")
    p.add_argument("--reasoning", default=None,
                   choices=["default", "low", "high", "off"])
    p.add_argument("--embed", default="on", choices=["on", "off"])
    p.add_argument("--prefilter", default=None,
                   help="임베딩 품질 사전필터용 gold.jsonl")
    p.add_argument("--legal", action="store_true")
    p.add_argument("--quality-split", dest="quality_split", action="store_true")
    p.add_argument("--config", default=None, help="config.json 경로")
    p.add_argument("--no-db", dest="no_db", action="store_true",
                   help="SQLite 영속화 비활성")
    p.add_argument("--fewshot", default=None,
                   help="few-shot 예시 풀 gold.jsonl (쿡북 Ch4, 모델 레시피로 K 결정)")
    p.add_argument("--yellow", action="store_true",
                   help="저신뢰/불일치 건을 YELLOW(사람 검수)로 분리 (--prefilter 참조 필요)")


def _banner():
    """실행 시 표시되는 Prism 그래픽: 빛이 프리즘을 통과해 메타 스펙트럼으로 분광."""
    from . import __version__ as ver
    R = "\033[0m"; W = "\033[97m"; G = "\033[38;5;245m"; A = "\033[38;5;99m"
    spec = [196, 208, 220, 46, 51, 33, 99]            # red→violet 스펙트럼
    bar = "".join(f"\033[48;5;{c}m  " for c in spec) + R
    art = [
        "",
        f"{A}        ╱╲{R}",
        f"{A}       ╱  ╲{R}      {W}P R I S M{R}  {G}v{ver}{R}",
        f"{A}  ━━▸ ╱ ▹▹ ╲{R}     {G}content → meta spectrum{R}",
        f"{A}     ╱______╲{R}     {G}품질·법령·아이템 · 토픽 · 사용자{R}",
        f"        {bar}",
        f"     {G}self-contained HTML · 의존성 0 · 모델 교체 가능{R}",
        "",
    ]
    print("\n".join(art))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(prog="prism", description="Prism: 콘텐츠 메타 추출·토픽·리포트 에이전트")
    ap.add_argument("--version", action="store_true", help="버전·배너 표시")
    sub = ap.add_subparsers(dest="cmd", required=False)

    pe = sub.add_parser("extract", help="단건/배치 추출")
    pe.add_argument("--input"); pe.add_argument("--batch")
    pe.add_argument("--out"); pe.add_argument("--slim", action="store_true")
    pe.add_argument("--concurrency", type=int, default=None)
    pe.add_argument("--resume", action="store_true",
                    help="DB 에 성공 처리된 건 skip하고 이어서 추출")
    pe.add_argument("--map", help="엑셀/CSV 컬럼 매핑 강제: title=제목,body=내용")
    _add_common(pe); pe.set_defaults(func=cmd_extract)

    pv = sub.add_parser("eval", help="골든셋 일치율")
    pv.add_argument("--goldenset", required=True)
    pv.add_argument("--concurrency", type=int, default=8)
    _add_common(pv); pv.set_defaults(func=cmd_eval)

    pab = sub.add_parser("ab", help="A/B: 두 방법론을 골든셋에 돌려 성능 비교")
    pab.add_argument("--goldenset", required=True)
    pab.add_argument("--a", required=True, help="방법론 A (프리셋 이름 또는 JSON 경로)")
    pab.add_argument("--b", required=True, help="방법론 B (프리셋 이름 또는 JSON 경로)")
    pab.add_argument("--limit", type=int, default=0, help="골든셋 앞 N건만(빠른 비교)")
    pab.add_argument("--concurrency", type=int, default=8)
    _add_common(pab); pab.set_defaults(func=cmd_ab)

    pd = sub.add_parser("dashboard", help="메타 현황+관계도 HTML")
    pd.add_argument("--results", required=True)
    pd.add_argument("--out"); pd.add_argument("--title")
    pd.add_argument("--integrated", action="store_true",
                    help="콘텐츠+사용자메타를 탭 단일 HTML 로 통합")
    pd.add_argument("--users", type=int, default=200)
    pd.add_argument("--logs"); pd.add_argument("--demo", action="store_true")
    pd.set_defaults(func=cmd_dashboard)

    pck = sub.add_parser("check", help="엑셀/CSV 입력이 동작 가능한지 판정(가능/불가능)")
    pck.add_argument("file"); pck.add_argument("--map")
    pck.set_defaults(func=cmd_check)

    pmp = sub.add_parser("topic", aliases=["metapool"],
                         help="토픽 엔티티형/사건형 생성 + HTML")
    pmp.add_argument("--results", default="results.jsonl")
    pmp.add_argument("--out")
    pmp.set_defaults(func=cmd_topic)

    prp = sub.add_parser("report", help="추출→토픽→대시보드 한 번에 (크론 친화 리포트)")
    prp.add_argument("--batch", help="신규 추출할 콘텐츠 jsonl (없으면 --results 사용)")
    prp.add_argument("--results", help="기존 추출 결과 jsonl 재사용")
    prp.add_argument("--out", help="출력 HTML 경로 (기본 report.html)")
    prp.add_argument("--title", default=None)
    prp.add_argument("--users", type=int, default=200)
    prp.add_argument("--profile", default=None, help="회사별 설정 JSON(브랜딩·사전 override)")
    prp.add_argument("--input", default=None)
    prp.add_argument("--resume", action="store_true")
    prp.add_argument("--concurrency", type=int, default=None)
    prp.add_argument("--map", help="엑셀/CSV 컬럼 매핑 강제")
    prp.add_argument("--logs", help="실 행동 로그 jsonl (있으면 실데이터 사용자 메타)")
    prp.add_argument("--demo", action="store_true", help="합성 목업 사용자 메타로 채움")
    _add_common(prp)
    prp.set_defaults(func=cmd_report)

    pm = sub.add_parser("usermeta", help="사용자 메타: 실로그(--logs)/목업(--demo)/빈상태")
    pm.add_argument("--results", required=True)
    pm.add_argument("--out", help="HTML 출력(생략 시 JSON stdout)")
    pm.add_argument("--users", type=int, default=200)
    pm.add_argument("--logs", help="실 행동 로그 jsonl (있으면 실데이터)")
    pm.add_argument("--demo", action="store_true", help="합성 목업으로 채움")
    pm.set_defaults(func=cmd_usermeta)

    pu = sub.add_parser("usage", help="비용/호출 로그")
    pu.add_argument("--since"); pu.add_argument("--config", default=None)
    pu.set_defaults(func=cmd_usage)

    pdoc = sub.add_parser("doctor", help="API·모델·임베딩·DB 연결 점검")
    pdoc.add_argument("--config", default=None); pdoc.add_argument("--model", default=None)
    pdoc.add_argument("--mock", action="store_true")
    pdoc.set_defaults(func=cmd_doctor)

    for nm in ("init", "init-config"):
        pinit = sub.add_parser(nm, help="설정(엔드포인트·모델) 구성 → config.json")
        pinit.add_argument("--base-url", dest="base_url", default=None,
                           help="OpenAI 호환 엔드포인트 (예: https://api.openai.com/v1)")
        pinit.add_argument("--model", default=None, help="사용할 모델명")
        pinit.add_argument("--out", default=None)
        pinit.set_defaults(func=cmd_init_config)

    pp = sub.add_parser("prompt", help="내부 품질 프롬프트 버전 관리/편집")
    pp.add_argument("action", choices=["list", "show", "set", "new", "diff"])
    pp.add_argument("version", nargs="?", default=None)
    pp.add_argument("--to", default=None, help="new/diff 대상 버전명")
    pp.set_defaults(func=cmd_prompt)

    pt = sub.add_parser("tune", help="자가수정 루프(분석→개정→회귀안전망)")
    pt.add_argument("--goldenset", required=True)
    pt.add_argument("--base", default=None, help="baseline 버전(기본=active)")
    pt.add_argument("--candidate", default=None, help="후보 버전(평가·채택 대상)")
    pt.add_argument("--concurrency", type=int, default=8)
    pt.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="개선돼도 active 변경 안 함")
    _add_common(pt); pt.set_defaults(func=cmd_tune)

    a = ap.parse_args(argv)
    if getattr(a, "version", False):
        _banner(); return
    if not getattr(a, "cmd", None):
        _banner()
        if not Config.load().is_configured():
            print("⚙️  처음이세요? 먼저 설정하세요:")
            print("    python3 -m prism.cli init --base-url <엔드포인트> --model <모델>")
            print("    (키 없이 둘러보려면 각 명령에 --mock)\n")
        ap.print_help(); return
    a.func(a)


if __name__ == "__main__":
    main()
