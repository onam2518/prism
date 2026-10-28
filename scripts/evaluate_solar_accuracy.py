#!/usr/bin/env python3
"""기존 생성 결과에 정답의 원문 근거 검사와 정보 보존 평가를 추가한다.

UPSTAGE_API_KEY와 --live가 필요하다. 생성 모델을 다시 호출하지 않는다.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prism import accuracy_eval as A, context_eval as C, solar_context as S
from scripts.evaluate_solar_context import API, cost, digest, write_json


def run(folder, api, workers=4):
    logpath = folder / "accuracy_reviews.jsonl"
    if logpath.exists():
        raise ValueError("accuracy evaluation already exists; do not overwrite")
    rows = json.loads((folder / "dataset.json").read_text())
    outputs = json.loads((folder / "results.json").read_text())
    report = json.loads((folder / "evaluation.json").read_text())
    manifest = json.loads((folder / "manifest.json").read_text())
    if manifest["model"] != S.MODEL or digest(rows) != manifest["dataset_hash"]:
        raise ValueError("model or dataset manifest mismatch")
    facts = {(r["variant"], r["i"]): r.get("review") if r["ok"] else None
             for r in (json.loads(line) for line in (folder / "fact_reviews.jsonl").read_text().splitlines())}
    write_json(folder / "accuracy_manifest.json", {
        "version": A.VERSION, "model": S.MODEL, "temperature": 0, "reasoning_effort": "none",
        "max_tokens": 4096, "dataset_hash": manifest["dataset_hash"],
        "reference_system": C.FACT_REVIEW_SYSTEM, "coverage_system": A.COVERAGE_SYSTEM,
        "reference_decomposition": "once per reference, before either candidate coverage review",
        "status": "exploratory; same-model judge, not human-calibrated accuracy"})
    records, references, coverage = [], {}, {}

    def ask(record, system, user, validate):
        try:
            record.update(api.chat(system, json.dumps(user, ensure_ascii=False), max_tokens=4096))
            if record["model"] != S.MODEL or record["finish_reason"] != "stop":
                raise ValueError("unexpected or incomplete judge response")
            record["review"] = validate(json.loads(record["text"]))
            record["ok"] = True
        except Exception as error:
            record.update(ok=False, error=type(error).__name__)
        return record

    def reference(i):
        row = rows[i]
        source = row["content"].get("title", "") + "\n" + row["content"].get("body", "")
        expected = row["expected"].get("summary", "")
        status = row["expected"].get("meta_status", {}).get("summary")
        if not expected or status not in (None, "success"):
            return {"stage": "reference", "i": i, "ok": False, "error": "unlabeled_reference"}
        return ask({"stage": "reference", "i": i}, C.FACT_REVIEW_SYSTEM,
                   {"source_units": C.source_units(source), "candidate": expected},
                   lambda obj: C.validate_fact_review(source, expected, obj))

    def cover(variant, i):
        record = {"stage": "coverage", "variant": variant, "i": i}
        ref = references[i]
        if not ref["ok"]:
            return {**record, "ok": False, "error": "reference_review_failed"}
        claims = A.reference_claims(ref["review"])
        text = outputs[variant][i]["prediction"].get("summary", "")
        if not claims:
            return {**record, "ok": False, "error": "no_grounded_reference_claims"}
        if not text.strip():
            result = {"items": [{"id": key, "verdict": "missing", "evidence_ids": []} for key in claims]}
            return {**record, "ok": True, "review": A.validate_coverage(claims, text, result)}
        return ask(record, A.COVERAGE_SYSTEM,
                   {"reference_claims": claims, "candidate_units": C.source_units(text)},
                   lambda obj: A.validate_coverage(claims, text, obj))

    with logpath.open("x") as log, ThreadPoolExecutor(max_workers=workers) as pool:
        tasks = [pool.submit(reference, i) for i in range(len(rows))]
        for task in as_completed(tasks):
            r = task.result(); references[r["i"]] = r; records.append(r)
            log.write(json.dumps(r, ensure_ascii=False) + "\n"); log.flush()
        print("reference review", len(references), "/", len(rows), flush=True)
        tasks = [pool.submit(cover, v, i) for i in range(len(rows))
                 for v in (list(outputs) if i % 2 == 0 else list(reversed(outputs)))]
        for n, task in enumerate(as_completed(tasks), 1):
            r = task.result(); coverage[r["variant"], r["i"]] = r; records.append(r)
            log.write(json.dumps(r, ensure_ascii=False) + "\n"); log.flush()
            if n % 12 == 0:
                print("coverage review", n, "/", len(tasks), flush=True)
    scored = report["per_document"]
    for v, values in scored.items():
        for i, metrics in enumerate(values):
            checked = coverage[v, i]
            review = checked.get("review") if checked["ok"] else None
            metrics["reference_claim_coverage"] = review["coverage"] if review else None
            metrics["reference_claim_contradiction"] = review["contradiction"] if review else None
            out = outputs[v][i]
            metrics.update(A.diagnostics([out["calls"][c]["ok"] for c in ("summary", "entities", "intent", "category")],
                                         out["prediction"].get("summary", ""), facts.get((v, i)), review))
    write_json(folder / "accuracy_evaluation.json", {
        "version": A.VERSION, "n": len(rows), "comparison": C.compare(scored["baseline"], scored["candidate"]),
        "per_document": scored,
        "reference_reviews_valid": sum(r["ok"] for r in references.values()),
        "reference_claims_excluded": sum(c["verdict"] != "supported" for r in references.values() if r["ok"] for c in r["review"]["claims"]),
        "reference_claims_total": sum(len(r["review"]["claims"]) for r in references.values() if r["ok"]),
        "coverage_reviews_valid": sum(r["ok"] for r in coverage.values()),
        "additional_judge_cost_usd": sum(cost(r.get("usage", {})) for r in records),
        "limitations": report["limitations"] + [
            "reference coverage measures grounded reference information, not all important source information",
            "unsupported reference claims are reported and excluded from coverage, not silently treated as correct",
            "all-clear is a strict exploratory diagnostic for summary and schema only, not calibrated accuracy or automatic approval",
            "atomic decomposition and evidence support still depend on a same-model judge"]})
    print("accuracy evaluation saved", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--folder", type=Path, required=True)
    p.add_argument("--live", action="store_true")
    args = p.parse_args()
    if not args.live or not os.environ.get("UPSTAGE_API_KEY"):
        p.error("requires --live and UPSTAGE_API_KEY")
    run(args.folder, API(os.environ["UPSTAGE_API_KEY"]))
