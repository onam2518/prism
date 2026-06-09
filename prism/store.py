"""SQLite 영속성 (운영 하드닝). 결과·usage·판정사례를 파일 DB에 적재."""
from __future__ import annotations
import json
import sqlite3
import threading
import time
import hashlib

_local = threading.local()


def content_hash(content: dict) -> str:
    s = (content.get("displayServiceName", "") + "\x1f" + content.get("title", "")
         + "\x1f" + content.get("subtitle", "") + "\x1f" + content.get("body", ""))
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


class Store:
    def __init__(self, path: str):
        self.path = path
        self._init()

    def _conn(self) -> sqlite3.Connection:
        # 스레드별 커넥션(ThreadPool 동시 쓰기 안전)
        c = getattr(_local, "conn", None)
        if c is None or getattr(_local, "path", None) != self.path:
            c = sqlite3.connect(self.path, timeout=30)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
            _local.conn = c
            _local.path = self.path
        return c

    def _init(self):
        c = self._conn()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS results(
          content_hash TEXT PRIMARY KEY, run_id TEXT, service TEXT, title TEXT,
          final_grade TEXT, reasons TEXT, item_meta TEXT, payload TEXT,
          cost_usd REAL, fail_kind TEXT, created_at REAL);
        CREATE TABLE IF NOT EXISTS usage(
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, n INTEGER,
          cost_usd REAL, tokens_in INTEGER, tokens_out INTEGER);
        CREATE TABLE IF NOT EXISTS runs(
          run_id TEXT PRIMARY KEY, started_at REAL, finished_at REAL,
          n INTEGER, config TEXT, metrics TEXT);
        CREATE TABLE IF NOT EXISTS cases(
          content_hash TEXT PRIMARY KEY, service TEXT, title TEXT,
          final_grade TEXT, reasons TEXT, source TEXT, manual_review INTEGER,
          created_at REAL);
        CREATE INDEX IF NOT EXISTS ix_results_run ON results(run_id);
        """)
        c.commit()

    # 결과 upsert / resume
    def done_hashes(self, only_ok: bool = True) -> set:
        """이미 처리된 content_hash 집합. only_ok=True 면 실패건은 미처리로 간주(재시도)."""
        c = self._conn()
        q = "SELECT content_hash FROM results"
        if only_ok:
            q += " WHERE fail_kind IS NULL OR fail_kind=''"
        return {r[0] for r in c.execute(q)}

    def save_result(self, content: dict, out: dict, run_id: str):
        ch = content_hash(content)
        qm = out.get("quality_meta", {})
        tr = out.get("trace", {})
        fbs = tr.get("fallbacks", [])
        fail_kind = ""
        for f in fbs:
            s = str(f)
            if "HTTP" in s or "_fail" in s or "예외" in s:
                fail_kind = "api"
                break
        c = self._conn()
        c.execute("""INSERT INTO results
          (content_hash,run_id,service,title,final_grade,reasons,item_meta,payload,cost_usd,fail_kind,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(content_hash) DO UPDATE SET
            run_id=excluded.run_id, final_grade=excluded.final_grade,
            reasons=excluded.reasons, item_meta=excluded.item_meta,
            payload=excluded.payload, cost_usd=excluded.cost_usd,
            fail_kind=excluded.fail_kind, created_at=excluded.created_at""",
          (ch, run_id, content.get("displayServiceName", ""), content.get("title", ""),
           qm.get("finalGrade", ""), json.dumps(qm.get("reasons", []), ensure_ascii=False),
           json.dumps(out.get("item_meta"), ensure_ascii=False),
           json.dumps(out, ensure_ascii=False), tr.get("cost_usd", 0.0),
           fail_kind, time.time()))
        c.commit()

    def get_by_hashes(self, hashes) -> dict:
        """content_hash → payload(dict). resume 시 skip 한 건의 기존 결과 회수."""
        if not hashes:
            return {}
        c = self._conn()
        out = {}
        hl = list(hashes)
        for k in range(0, len(hl), 500):
            chunk = hl[k:k + 500]
            ph = ",".join("?" * len(chunk))
            for h, payload in c.execute(
                    f"SELECT content_hash,payload FROM results WHERE content_hash IN ({ph})",
                    chunk):
                out[h] = json.loads(payload)
        return out

    def export_results(self, run_id: str | None = None) -> list:
        c = self._conn()
        q = "SELECT payload FROM results"
        args = ()
        if run_id:
            q += " WHERE run_id=?"
            args = (run_id,)
        return [json.loads(r[0]) for r in c.execute(q, args)]

    # runs / usage
    def start_run(self, run_id, n, config):
        c = self._conn()
        c.execute("INSERT OR REPLACE INTO runs(run_id,started_at,n,config) VALUES(?,?,?,?)",
                  (run_id, time.time(), n, json.dumps(config, ensure_ascii=False)))
        c.commit()

    def finish_run(self, run_id, metrics):
        c = self._conn()
        c.execute("UPDATE runs SET finished_at=?, metrics=? WHERE run_id=?",
                  (time.time(), json.dumps(metrics, ensure_ascii=False), run_id))
        c.commit()

    def log_usage(self, kind, n, cost, tin, tout):
        c = self._conn()
        c.execute("INSERT INTO usage(ts,kind,n,cost_usd,tokens_in,tokens_out) VALUES(?,?,?,?,?,?)",
                  (time.time(), kind, n, cost, tin, tout))
        c.commit()

    def usage_since(self, since_ts=0):
        c = self._conn()
        return list(c.execute(
            "SELECT ts,kind,n,cost_usd,tokens_in,tokens_out FROM usage WHERE ts>=? ORDER BY ts",
            (since_ts,)))
