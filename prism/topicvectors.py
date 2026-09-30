"""뉴스 토픽 미리보기의 의미순 샘플 · 조건 판정과 저장은 topicops가 유지."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import logging
import math
import os
import threading
import time
import urllib.error
import urllib.request
import uuid

from . import topic
from .embed import EmbeddingClient, PASSAGE_MODEL, QUERY_MODEL

COLLECTION = "prism_topic_news_v1"
TEXT_LIMIT = 6000
_syncing = set()
_lock = threading.Lock()
_queries = {}


def _request(method, path, body=None):
    base = os.environ.get("PRISM_QDRANT_URL", "").rstrip("/")
    key = os.environ.get("PRISM_QDRANT_KEY", "")
    if not base or not key:
        raise RuntimeError("Vector search is not configured")
    req = urllib.request.Request(base + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "api-key": key},
                                 method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp).get("result")


def _embedding_client():
    from .config import Config
    cfg = Config.load()
    key = os.environ.get("UPSTAGE_API_KEY") or (cfg.api_key if "upstage.ai" in cfg.chat_url else "")
    if not key:
        raise RuntimeError("A real embedding key is required")
    return EmbeddingClient(api_key=key)


def _valid_vector(vector):
    if (not isinstance(vector, list) or not vector
            or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in vector)
            or not any(vector)):
        raise ValueError("Invalid embedding vector")
    return vector


def _point(row, team):
    ref = row.get("content_ref") or {}
    if ref.get("displayServiceName") != "뉴스" or not topic._eligible(row):
        return None
    text = "\n".join(str(ref.get(k) or "").strip() for k in ("title", "subtitle", "body")).strip()
    if not text:
        return None
    # 전체 원문 해시로 잘린 본문 뒷부분의 수정도 이전 벡터와 구분.
    revision = hashlib.sha256(text.encode()).hexdigest()
    scope = str(team) if team is not None else "local"
    pid = str(uuid.uuid5(uuid.NAMESPACE_URL,
                         "prism-topic:" + scope + ":" + topic._row_hash(row) + ":" + revision + ":" + PASSAGE_MODEL))
    return pid, text[:TEXT_LIMIT], {"team": scope, "input_revision": revision, "model": PASSAGE_MODEL}


def _points(rows, team):
    return {i: point for i, row in enumerate(rows) if (point := _point(row, team)) is not None}


def _existing(ids):
    out = set()
    for start in range(0, len(ids), 200):
        found = _request("POST", f"/collections/{COLLECTION}/points",
                         {"ids": ids[start:start + 200], "with_payload": False, "with_vector": False})
        out.update(str(p["id"]) for p in found)
    return out


def sync(rows, team, *, prune=False):
    """조회용 인덱스만 갱신. 원본·메타·토픽·검수 결과는 보존."""
    points = _points(rows, team)
    # 빈 팀도 조회용 인덱스 정리가 가능해야 한다.
    client = _embedding_client()
    probe = _valid_vector(client.embed("콘텐츠 탐색 연결 확인", is_query=False))
    size = len(probe)
    try:
        config = _request("GET", f"/collections/{COLLECTION}")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        _request("PUT", f"/collections/{COLLECTION}",
                 {"vectors": {"size": size, "distance": "Cosine", "on_disk": True}})
        _request("PUT", f"/collections/{COLLECTION}/index?wait=true",
                 {"field_name": "team", "field_schema": "keyword"})
    else:
        if config["config"]["params"]["vectors"]["size"] != size:
            raise ValueError("Embedding model dimensions changed; a new collection is required")
    ids = [p[0] for p in points.values()]
    existing = _existing(ids)
    pending = list({p[0]: p for p in points.values() if p[0] not in existing}.values())

    def encode(point):
        pid, text, payload = point
        vector = _valid_vector(_embedding_client().embed(text, is_query=False))
        if len(vector) != size:
            raise ValueError("Embedding dimension mismatch")
        return {"id": pid, "vector": vector, "payload": payload}

    with ThreadPoolExecutor(max_workers=3) as pool:
        for start in range(0, len(pending), 30):
            batch = list(pool.map(encode, pending[start:start + 30]))
            _request("PUT", f"/collections/{COLLECTION}/points?wait=true", {"points": batch})
    if prune:
        scope = str(team) if team is not None else "local"
        condition = {"must": [{"key": "team", "match": {"value": scope}}]}
        if ids:
            condition["must_not"] = [{"has_id": ids}]
        _request("POST", f"/collections/{COLLECTION}/points/delete?wait=true", {"filter": condition})
    return {"eligible_news": len(points), "indexed": len(set(ids)), "added": len(pending)}


def _background_sync(rows, team):
    scope = str(team) if team is not None else "local"
    with _lock:
        if scope in _syncing:
            return
        _syncing.add(scope)

    def run():
        try:
            sync(rows, team)
        except Exception as exc:
            logging.warning("Topic vector sync failed (%s)", type(exc).__name__)
        finally:
            with _lock:
                _syncing.discard(scope)
    threading.Thread(target=run, daemon=True).start()


def rank(query, rows, ids, team, limit=5):
    """현재 조건을 통과한 ID만 검색. 순위는 조건·건수·토픽에 영향 없음."""
    allowed_rows = set(ids)
    current = {i: p for i, p in _points(rows, team).items() if i in allowed_rows}
    total = len(ids)
    info = {"status": "disabled", "matched": total, "eligible_news": len(current), "indexed": 0}
    if not os.environ.get("PRISM_QDRANT_URL") or not query or not current:
        return [], info
    try:
        allowed = {p[0]: i for i, p in current.items()}
        existing = _existing(list(allowed)) & set(allowed)
        info["indexed"] = len(existing)
        if len(existing) < len(allowed):
            _background_sync(rows, team)
        if not existing:
            info["status"] = "indexing"
            return [], info
        query = str(query).strip()[:2000]
        cache_key = (QUERY_MODEL, hashlib.sha256(query.encode()).hexdigest())
        with _lock:
            cached = _queries.get(cache_key)
        if cached and time.monotonic() - cached[0] < 600:
            vector = cached[1]
        else:
            vector = _valid_vector(_embedding_client()._api_embed(query, True, max_retries=0))
            with _lock:
                if len(_queries) >= 128:
                    _queries.clear()
                _queries[cache_key] = (time.monotonic(), vector)
        scope = str(team) if team is not None else "local"
        result = _request("POST", f"/collections/{COLLECTION}/points/query", {
            "query": vector, "limit": max(1, min(int(limit), 5)), "with_payload": False,
            "filter": {"must": [{"key": "team", "match": {"value": scope}}, {"has_id": list(existing)}]},
        })
        # 조회 인덱스의 반환값도 현재 허용 목록으로 재검증.
        ranked = []
        seen = set()
        for point in result["points"]:
            pid = str(point.get("id"))
            score = point.get("score")
            if pid in existing and pid not in seen and isinstance(score, (int, float)) and math.isfinite(score):
                ranked.append({"i": allowed[pid], "semantic_score": round(score, 4)})
                seen.add(pid)
        info["status"] = "ready" if len(existing) == total else "partial"
        return ranked[:5], info
    except Exception as exc:
        logging.warning("Topic vector query unavailable (%s)", type(exc).__name__)
        info["status"] = "unavailable"
        return [], info


def main():
    parser = argparse.ArgumentParser(description="뉴스 토픽 조회용 벡터의 추가 이관")
    parser.add_argument("--all-teams", action="store_true")
    parser.add_argument("--team")
    parser.add_argument("--prune", action="store_true", help="현재 원본 목록에서 사라진 조회용 벡터 정리")
    args = parser.parse_args()
    from . import serve
    st = serve.get_store()
    teams = st.team_ids() if args.all_teams else [args.team]
    if getattr(st, "REMOTE", False) and teams == [None]:
        parser.error("운영 DB에서는 --team 또는 --all-teams 필요")
    for index, team in enumerate(teams):
        rows = serve.results_rows(team=team)
        result = sync(rows, team, prune=args.prune)
        st.save_report("topic_vector_migration_v1", dict(result, ts=time.time(), collection=COLLECTION), team=team)
        print(json.dumps(dict(result, team_number=index + 1), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
