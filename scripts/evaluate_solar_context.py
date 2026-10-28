#!/usr/bin/env python3
"""고정 JSONL 정답으로 Solar Pro 4 기존/개선 프롬프트를 비교한다.

기본은 프롬프트 내보내기만. --live를 지정해야 외부 API를 호출한다.
키는 UPSTAGE_API_KEY 환경변수에서 읽고 파일·로그에 기록하지 않는다.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prism import context_eval as CE, dictionaries as D, meta_prompts as MP, solar_context as SC, solar_accuracy as SA
from prism.schema import Content

EMBED_MODEL = "solar-embedding-2-passage"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def prompt_sets(candidate_module=SC):
    baseline, candidate = {}, {}
    for call in MP.CALLS:
        rules = MP.CALL_RULES[call].strip()
        dictionary = MP.call_dictionary(call)
        if dictionary:
            rules += "\n\n" + dictionary
        baseline[call] = MP._compose("solar", MP.CALL_ROLES[call], MP.CALL_SCHEMAS[call],
                                     rules, MP.gold_examples(call), MP.CALL_SELF_CHECK[call], "")
        candidate[call] = candidate_module.call_system(call)
    return {"baseline": baseline, "candidate": candidate}


def validate_output(call, obj):
    key = "content_category" if call == "category" else call
    if not isinstance(obj, dict) or set(obj) != {key}:
        raise ValueError("output keys differ")
    value = obj[key]
    if call == "summary":
        if not isinstance(value, str):
            raise ValueError("summary is not a string")
        return value
    if not isinstance(value, list):
        raise ValueError("output is not a list")
    if call == "intent":
        if any(not isinstance(x, str) or x not in D.intent_categories() for x in value):
            raise ValueError("intent outside dictionary")
    elif call == "entities":
        for x in value:
            if (not isinstance(x, dict) or set(x) != {"name", "type"}
                    or not isinstance(x["name"], str) or not x["name"].strip()
                    or x["type"] not in ("PS", "OG", "LC", "AF", "EV", "TM", None)):
                raise ValueError("entity schema differs")
    else:
        for x in value:
            if not isinstance(x, dict) or set(x) != {"tier1", "tier2"}:
                raise ValueError("category schema differs")
            if (not isinstance(x["tier1"], str) or x["tier1"] not in D.IAB_TIER1
                    or x["tier2"] is not None and x["tier2"] not in D.CONTENT_CATEGORY_TIER2[x["tier1"]]):
                raise ValueError("category outside dictionary")
    return value


class API:
    def __init__(self, key):
        self.key = key

    def post(self, path, data):
        req = urllib.request.Request("https://api.upstage.ai/v1/" + path,
                                     data=json.dumps(data).encode(), headers={
                                         "Authorization": "Bearer " + self.key,
                                         "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)

    def chat(self, system, user, max_tokens=2048):
        start = time.monotonic()
        raw = self.post("chat/completions", {
            "model": SC.MODEL, "messages": [{"role": "system", "content": system},
                                            {"role": "user", "content": user}],
            "temperature": 0, "reasoning_effort": "none", "max_tokens": max_tokens,
            "response_format": {"type": "json_object"}})
        info = {"model": raw.get("model"), "usage": raw.get("usage", {}),
                "latency_ms": round((time.monotonic() - start) * 1000)}
        choice = raw["choices"][0]
        info["finish_reason"] = choice.get("finish_reason")
        info["text"] = choice["message"].get("content", "")
        return info


def load_rows(path, limit):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    seen = set()
    for row in rows:
        if not isinstance(row.get("content"), dict) or not isinstance(row.get("expected"), dict):
            raise ValueError("each row needs content and expected objects")
        row["id"] = row.get("id") or digest(row["content"])
        source_id = digest({k: row["content"].get(k, "") for k in ("title", "body")})
        if source_id in seen:
            raise ValueError("duplicate source in evaluation dataset")
        seen.add(source_id)
    return sorted(rows, key=lambda row: digest(row["id"]))[:limit]


def cost(usage):
    inp, out = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    cached = min(inp, usage.get("prompt_tokens_details", {}).get("cached_tokens", 0))
    # 2026-10-28 Upstage 공식 Solar Pro 4 가격. 청구서가 아닌 usage 기반 추정값.
    return ((inp - cached) * .30 + cached * .06 + out * 1.20) / 1_000_000


def run(args):
    if (args.output / "generation.jsonl").exists():
        raise ValueError("use a new output directory; existing evaluation artifacts are immutable")
    args.output.mkdir(parents=True, exist_ok=True)
    candidate_module = SA if args.candidate == "accuracy" else SC
    prompts = prompt_sets(candidate_module)
    write_json(args.output / "prompts.json", prompts)
    for variant, calls in prompts.items():
        folder = args.output / variant
        folder.mkdir(exist_ok=True)
        for call, prompt in calls.items():
            (folder / (call + ".system.txt")).write_text(prompt, encoding="utf-8")
    manifest = {"model": SC.MODEL, "prompt_version": candidate_module.VERSION, "evaluator_version": CE.VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(), "reasoning_effort": "none",
                "temperature": 0, "max_tokens": 2048, "embedding_model": EMBED_MODEL,
                "prompt_hashes": {v: {k: digest(p) for k, p in calls.items()} for v, calls in prompts.items()},
                "prompt_basis": "repository defaults; runtime overrides and learned feedback excluded",
                "scope": "four independent source-metadata calls; no quality gate or retries",
                "judge": "same Solar snapshot, separate blind calls; not independent or human-calibrated" if args.judge else "not measured"}
    write_json(args.output / "manifest.json", manifest)
    if not args.live:
        return
    if not os.environ.get("UPSTAGE_API_KEY") or not args.golden:
        raise ValueError("--live requires UPSTAGE_API_KEY and --golden")
    rows = load_rows(args.golden, args.limit)
    if not rows:
        raise ValueError("empty evaluation dataset")
    manifest.update(dataset_hash=digest(rows), n=len(rows), row_ids=[r["id"] for r in rows],
                    dataset_provenance=[r.get("provenance", {}) for r in rows])
    write_json(args.output / "manifest.json", manifest)
    write_json(args.output / "dataset.json", rows)
    api = API(os.environ["UPSTAGE_API_KEY"])
    results = {v: [{"prediction": {}, "calls": {}} for _ in rows] for v in prompts}

    def generate(i, variant, call):
        row = rows[i]
        content = Content.from_dict(row["content"])
        user = MP.call_user(call, content)
        record = {"i": i, "id": row["id"], "variant": variant, "call": call,
                  "user_sha256": digest(user)}
        try:
            record.update(api.chat(prompts[variant][call], user))
            if record["model"] != SC.MODEL or record["finish_reason"] != "stop":
                raise ValueError("unexpected model or incomplete response")
            record["value"] = validate_output(call, json.loads(record["text"]))
            record["ok"] = True
        except Exception as error:
            record.update(ok=False, error=type(error).__name__)
        return record

    jobs = [(i, variant, call) for i in range(len(rows))
            for variant in (list(prompts) if i % 2 == 0 else list(reversed(prompts))) for call in MP.CALLS]
    with (args.output / "generation.jsonl").open("w") as log, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(generate, *job) for job in jobs]
        for n, future in enumerate(as_completed(futures), 1):
            record = future.result()
            log.write(json.dumps(record, ensure_ascii=False) + "\n"); log.flush()
            target = results[record["variant"]][record["i"]]
            target["calls"][record["call"]] = record
            key = "content_category" if record["call"] == "category" else record["call"]
            target["prediction"][key] = record.get("value", "" if key == "summary" else [])
            if n % 16 == 0 or n == len(jobs):
                print("generation", n, "/", len(jobs), flush=True)
    write_json(args.output / "results.json", results)
    evaluate(args.output, rows, results, api, args.judge, args.workers)


def evaluate(folder, rows, results, api, judge=False, workers=4):
    scored = {v: [CE.score_fields(row["expected"], out["prediction"],
                                 ["content_category" if call == "category" else call
                                  for call, record in out["calls"].items() if not record["ok"]])
                  for row, out in zip(rows, outs)]
              for v, outs in results.items()}
    pairs, positions = [], []
    for v, outs in results.items():
        for i, (row, out) in enumerate(zip(rows, outs)):
            for call, record in out["calls"].items():
                scored[v][i][call + "_call_success"] = float(record["ok"])
            scored[v][i]["generation_cost_usd"] = sum(cost(r.get("usage", {})) for r in out["calls"].values())
            if "summary_lexical_f1" in scored[v][i]:
                pairs.append((row["expected"]["summary"], out["prediction"].get("summary", "")))
                positions.append((v, i))
    embed_usage = []
    def embed(texts):
        vectors = []
        for start in range(0, len(texts), 64):
            chunk = texts[start:start + 64]
            raw = api.post("embeddings", {"model": EMBED_MODEL, "input": chunk})
            data = sorted(raw["data"], key=lambda x: x["index"])
            if [x["index"] for x in data] != list(range(len(chunk))):
                raise ValueError("embedding indices differ")
            vectors.extend(x["embedding"] for x in data)
            embed_usage.append({"model": raw.get("model"), "usage": raw.get("usage", {})})
        return vectors
    sims, status = CE.semantic_pairs(pairs, embed)
    for (v, i), score in zip(positions, sims):
        scored[v][i]["summary_semantic_cosine"] = score
    fact_records = []
    if judge:
        def review(v, i):
            row = rows[i]
            source = row["content"].get("title", "") + "\n" + row["content"].get("body", "")
            candidate = results[v][i]["prediction"].get("summary", "")
            record = {"variant": v, "i": i}
            try:
                # 정답·프롬프트 변형 이름·다른 후보를 평가자에게 전달하지 않는다.
                record.update(api.chat(CE.FACT_REVIEW_SYSTEM, json.dumps({"source_units": CE.source_units(source), "candidate": candidate}, ensure_ascii=False), max_tokens=4096))
                if record["model"] != SC.MODEL or record["finish_reason"] != "stop":
                    raise ValueError("incomplete or unexpected judge response")
                record["review"] = CE.validate_fact_review(source, candidate, json.loads(record["text"]))
                record["ok"] = True
            except Exception as error:
                record.update(ok=False, error=type(error).__name__)
            return record
        with ThreadPoolExecutor(max_workers=workers) as pool, (folder / "fact_reviews.jsonl").open("w") as log:
            tasks = [pool.submit(review, v, i) for i in range(len(rows))
                     for v in (list(results) if i % 2 == 0 else list(reversed(results)))]
            for n, future in enumerate(as_completed(tasks), 1):
                record = future.result(); fact_records.append(record)
                log.write(json.dumps(record, ensure_ascii=False) + "\n"); log.flush()
                metrics = scored[record["variant"]][record["i"]]
                for key in ("support_rate", "contradiction_rate", "unverifiable_rate"):
                    metrics["judge_" + key] = record.get("review", {}).get(key)
                if n % 12 == 0:
                    print("fact review", n, "/", len(tasks), flush=True)
    report = {"evaluation_version": CE.VERSION, "n": len(rows), "embedding_status": status,
              "embedding_usage": embed_usage, "comparison": CE.compare(scored["baseline"], scored["candidate"]),
              "per_document": scored, "judge_valid": sum(r["ok"] for r in fact_records),
              "judge_requested": len(fact_records),
              "judge_cost_usd": sum(cost(r.get("usage", {})) for r in fact_records),
              "limitations": ["pilot sample; no multiple-comparison correction",
                              "cosine is not accuracy or factuality; no calibrated pass threshold",
                              "same-model judge is provisional and requires independent human calibration",
                              "generation failures stay in metric denominators; judge failures are missing",
                              "runtime overrides, learned feedback, retries, and production configuration not evaluated"]}
    write_json(folder / "evaluation.json", report)
    print(json.dumps({"n": len(rows), "embedding_status": status, "judge_valid": report["judge_valid"]}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=24)
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--candidate", choices=("context", "accuracy"), default="context")
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 300:
        parser.error("limit must be 1..300")
    run(args)


if __name__ == "__main__":
    main()
