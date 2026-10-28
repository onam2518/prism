"""모든 평가 경로의 명시적 지표·표본·대응 비교. 조회 중 외부 호출 없음."""
from collections import Counter
import hashlib
import json
import statistics
import os
import urllib.request

from . import context_eval as C
from .eval_contract import field_status

VERSION = "evaluation-2026-10-28-v1"
FIELDS = ("summary", "entities", "intent", "content_category")
EMBED_MODEL = "solar-embedding-2-passage"


def semantic_embed(texts):
    """평가 전용 모델 고정. 키·호출 오류를 mock 벡터로 대신하지 않는다."""
    key = os.environ.get("UPSTAGE_API_KEY") or os.environ.get("PRISM_API_KEY")
    if not key:
        raise RuntimeError("embedding credential unavailable")
    vectors = []
    for start in range(0, len(texts), 64):
        chunk = texts[start:start + 64]
        request = urllib.request.Request("https://api.upstage.ai/v1/embeddings",
            data=json.dumps({"model": EMBED_MODEL, "input": chunk}).encode(),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = json.load(response)
        data = sorted(raw["data"], key=lambda x: x["index"])
        if [x["index"] for x in data] != list(range(len(chunk))):
            raise ValueError("embedding indices differ")
        vectors.extend(x["embedding"] for x in data)
    return vectors


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), default=str).encode()).hexdigest()


def source(row):
    content = row.get("content") or {}
    return {k: content.get(k) or "" for k in ("title", "body")}


def prediction(out):
    if out is None:
        return None
    meta = dict(out.get("item_meta") or {})
    quality = out.get("quality_meta") or {}
    meta.update({k: quality[k] for k in ("finalGrade", "reasons") if k in quality})
    return meta


def items(rows, outputs, model="result"):
    if len(rows) != len(outputs):
        raise ValueError("evaluation rows and outputs differ")
    from .store import golden_hash
    keys = [golden_hash(row) for row in rows]
    counts = Counter(keys)
    return [{"hash": key if counts[key] == 1 else key + ":duplicate:" + str(i),
             "pairable": counts[key] == 1, "source": source(row) if row.get("content") else None,
             "expected": row.get("expected") or {}, "got": {model: prediction(out)}}
            for i, (key, row, out) in enumerate(zip(keys, rows, outputs))]



def score(expected, got):
    failed = list(FIELDS) if got is None or got.get("empty") else [
        f for f, status in (got.get("meta_status") or {}).items()
        if f in FIELDS and status not in ("success", "no_value")]
    values = C.score_fields(expected, got or {}, failed)
    statuses = (got or {}).get("meta_status") or {}
    # 정규화된 값만으로 원래 API 응답의 JSON 유효성을 증명하지 않는다.
    values["output_state_valid"] = (0.0 if got is None or got.get("empty") else
                                    float(not failed) if all(f in statuses for f in FIELDS) else None)
    grade = expected.get("finalGrade") or expected.get("grade")
    if grade in ("G", "R"):
        values["grade_accuracy"] = float(((got or {}).get("finalGrade") or (got or {}).get("grade")) == grade)
    if grade == "R":
        values["harm_miss"] = float(((got or {}).get("finalGrade") or (got or {}).get("grade")) == "G")
    values["generation_failure"] = float(got is None or bool(got.get("empty")))
    return values


def aggregate(documents):
    keys = sorted({k for d in documents for k in d["scores"]})
    return {k: {"value": statistics.mean(vals) if vals else None, "n": len(vals),
                "missing": len(documents) - len(vals)}
            for k in keys for vals in [[d["scores"].get(k) for d in documents if d["scores"].get(k) is not None]]}


def report(items, model, embed=None):
    documents, pairs, indices = [], [], []
    eligibility = {f: {} for f in FIELDS}
    seen = set()
    for item in items:
        key = item["hash"]
        if key in seen:
            raise ValueError("duplicate evaluation document")
        seen.add(key)
        expected = item.get("expected") or {}
        got = (item.get("got") or {}).get(model)
        scores = score(expected, got)
        states = {f: field_status(expected, f) for f in FIELDS}
        for f, state in states.items():
            eligibility[f][state] = eligibility[f].get(state, 0) + 1
        document = {"id": key, "basis": digest({"source": item.get("source"), "expected": expected}),
                    "source_recorded": bool(item.get("source")) and item.get("pairable", True), "scores": scores, "expected_status": states}
        documents.append(document)
        if "summary_lexical_f1" in scores:
            pairs.append((expected["summary"], "" if got is None or got.get("empty") or
                          (got.get("meta_status") or {}).get("summary") not in (None, "success", "no_value")
                          else got.get("summary") or ""))
            indices.append(len(documents) - 1)
    if embed is not None:
        sims, status = C.semantic_pairs(pairs, embed)
    else:
        sims, status = [None] * len(pairs), "not_measured"
    for i, sim in zip(indices, sims):
        documents[i]["scores"]["summary_semantic_cosine"] = sim
    return {"version": VERSION, "n": len(documents), "eligibility": eligibility,
            "embedding_model": EMBED_MODEL, "summary_required_n": len(pairs),
            "duplicate_sample_n": sum(not i.get("pairable", True) for i in items),
            "semantic_status": status, "metrics": aggregate(documents), "documents": documents,
            "limitations": ["cosine_is_not_accuracy", "ai_review_requires_human_calibration"]}


def paired(before, after):
    if not before or not after or before.get("version") != after.get("version"):
        return {"comparable": False, "reason": "평가 방식 버전 불일치 또는 기록 없음", "metrics": {}}
    if before.get("embedding_model") != after.get("embedding_model"):
        return {"comparable": False, "reason": "임베딩 모델 불일치", "metrics": {}}
    a = {d["id"]: d for d in before["documents"]}
    b = {d["id"]: d for d in after["documents"]}
    matched = sorted(k for k in a.keys() & b.keys()
                     if a[k]["basis"] == b[k]["basis"] and a[k]["source_recorded"] and b[k]["source_recorded"])
    all_match = bool(matched) and len(matched) == len(a) == len(b)
    return {"comparable": all_match, "matched": len(matched), "before_n": len(a), "after_n": len(b),
            "reason": "" if all_match else "저장 원문·정답·표본 불일치 또는 원문 기록 없음",
            "metrics": C.compare([a[k]["scores"] for k in matched], [b[k]["scores"] for k in matched]),
            "limitations": "탐색용 대응 비교, 다중 비교 보정 없음"}


def readiness(report):
    """메타 채점이 요청된 자료의 유효 정답 여부. 미측정을 합격으로 처리하지 않는다."""
    if not report:
        return []
    if not report.get("n"):
        return ["samples"]
    states = report.get("eligibility") or {}
    has_meta = any(v.get("scored", 0) or v.get("pending", 0) or v.get("excluded", 0) for v in states.values())
    if not has_meta:
        return ["duplicate_samples"] if report.get("duplicate_sample_n") else []
    missing = [f for f in FIELDS if not states.get(f, {}).get("scored")]
    summary = (report.get("metrics") or {}).get("summary_semantic_cosine") or {}
    if report.get("summary_required_n") and summary.get("n", 0) < report["summary_required_n"]:
        missing.append("summary_semantic_cosine")
    if report.get("duplicate_sample_n"):
        missing.append("duplicate_samples")
    return missing
