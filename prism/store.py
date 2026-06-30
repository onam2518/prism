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
        CREATE TABLE IF NOT EXISTS feedback(
          content_hash TEXT PRIMARY KEY, service TEXT, title TEXT,
          verdict TEXT, stage TEXT, note TEXT, ts REAL);
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

    # ── 배치 저장(단일 트랜잭션) + UI 조회/집계 ──
    def save_many(self, pairs, run_id: str):
        """pairs: [(content, out), …] 를 단일 트랜잭션으로 upsert(멱등). 반환: 건수."""
        rows = []
        for content, out in pairs:
            ch = content_hash(content)
            qm = out.get("quality_meta", {}) or {}
            tr = out.get("trace", {}) or {}
            fail_kind = ""
            for f in tr.get("fallbacks", []) or []:
                s = str(f)
                if "HTTP" in s or "_fail" in s or "예외" in s:
                    fail_kind = "api"; break
            rows.append((ch, run_id, content.get("displayServiceName", ""), content.get("title", ""),
                         qm.get("finalGrade", ""), json.dumps(qm.get("reasons", []), ensure_ascii=False),
                         json.dumps(out.get("item_meta"), ensure_ascii=False),
                         json.dumps(out, ensure_ascii=False), tr.get("cost_usd", 0.0),
                         fail_kind, time.time()))
        if not rows:
            return 0
        c = self._conn()
        c.executemany("""INSERT INTO results
          (content_hash,run_id,service,title,final_grade,reasons,item_meta,payload,cost_usd,fail_kind,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(content_hash) DO UPDATE SET
            run_id=excluded.run_id, final_grade=excluded.final_grade, reasons=excluded.reasons,
            item_meta=excluded.item_meta, payload=excluded.payload, cost_usd=excluded.cost_usd,
            fail_kind=excluded.fail_kind, created_at=excluded.created_at""", rows)
        c.commit()
        return len(rows)

    def save_dedup(self, pairs, run_id: str) -> dict:
        """적재 정책: content_hash 기준 멱등.
        · 신규 → insert  · 기존인데 메타(등급·item_meta·reasons) 변경 → update
        · 동일 콘텐츠 + 결과 무변경 → 적재 제외(skip, DB 미기록).
        (trace·cost 같은 실행 부산물은 비교에서 제외 — 매 실행 달라지므로)
        반환: {inserted, updated, skipped}"""
        c = self._conn()
        ins = upd = skip = 0
        rows = []
        for content, out in pairs:
            ch = content_hash(content)
            qm = out.get("quality_meta", {}) or {}
            tr = out.get("trace", {}) or {}
            new_im = json.dumps(out.get("item_meta"), ensure_ascii=False, sort_keys=True)
            new_gr = qm.get("finalGrade", "")
            new_rs = json.dumps(qm.get("reasons", []), ensure_ascii=False, sort_keys=True)
            cur = c.execute("SELECT item_meta, final_grade, reasons FROM results WHERE content_hash=?", (ch,)).fetchone()
            if cur is not None:
                try: old_im = json.dumps(json.loads(cur[0]), ensure_ascii=False, sort_keys=True)
                except Exception: old_im = cur[0] or ""
                try: old_rs = json.dumps(json.loads(cur[2]), ensure_ascii=False, sort_keys=True)
                except Exception: old_rs = cur[2] or ""
                if old_im == new_im and (cur[1] or "") == new_gr and old_rs == new_rs:
                    skip += 1
                    continue                      # 동일 콘텐츠·결과 → 적재 제외
                upd += 1
            else:
                ins += 1
            fail_kind = ""
            for f in tr.get("fallbacks", []) or []:
                s = str(f)
                if "HTTP" in s or "_fail" in s or "예외" in s:
                    fail_kind = "api"; break
            rows.append((ch, run_id, content.get("displayServiceName", ""), content.get("title", ""),
                         new_gr, json.dumps(qm.get("reasons", []), ensure_ascii=False),
                         json.dumps(out.get("item_meta"), ensure_ascii=False),
                         json.dumps(out, ensure_ascii=False), tr.get("cost_usd", 0.0),
                         fail_kind, time.time()))
        if rows:
            c.executemany("""INSERT INTO results
              (content_hash,run_id,service,title,final_grade,reasons,item_meta,payload,cost_usd,fail_kind,created_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(content_hash) DO UPDATE SET
                run_id=excluded.run_id, final_grade=excluded.final_grade, reasons=excluded.reasons,
                item_meta=excluded.item_meta, payload=excluded.payload, cost_usd=excluded.cost_usd,
                fail_kind=excluded.fail_kind, created_at=excluded.created_at""", rows)
            c.commit()
        return {"inserted": ins, "updated": upd, "skipped": skip}

    def recent(self, limit: int = 5000) -> list:
        """최근 적재 결과(payload)를 시간순(오래된→최신)으로. content_id = 리스트 인덱스."""
        c = self._conn()
        rows = [json.loads(r[0]) for r in c.execute(
            "SELECT payload FROM results ORDER BY created_at DESC LIMIT ?", (int(limit),))]
        rows.reverse()
        return rows

    def count(self) -> int:
        c = self._conn()
        return int(c.execute("SELECT COUNT(*) FROM results").fetchone()[0])

    def grade_stats(self) -> dict:
        c = self._conn()
        n = int(c.execute("SELECT COUNT(*) FROM results").fetchone()[0])
        g = int(c.execute("SELECT COUNT(*) FROM results WHERE final_grade='G'").fetchone()[0])
        return {"total": n, "g": g, "r": n - g, "gPct": round(g / n * 100) if n else 0}

    def clear(self):
        c = self._conn()
        c.execute("DELETE FROM results"); c.execute("DELETE FROM usage"); c.commit()

    def recent_meta(self, limit: int = 200) -> list:
        """배치 결과 콘텐츠별 행(피드백 부착용): content_hash·서비스·제목·등급·요약·카테고리."""
        c = self._conn()
        rows = []
        for ch, svc, ti, grade, im in c.execute(
                "SELECT content_hash,service,title,final_grade,item_meta FROM results ORDER BY created_at DESC LIMIT ?",
                (int(limit),)):
            try:
                imd = json.loads(im) if im else {}
            except Exception:
                imd = {}
            cat = " · ".join(f"{k}→{v}" for k, v in ((imd or {}).get("content_category") or {}).items())
            rows.append({"hash": ch, "service": svc or "", "title": ti or "",
                         "grade": grade or "", "summary": (imd or {}).get("summary", ""), "category": cat})
        return rows

    # ── 평가 피드백 / 학습 루프 ──
    def save_feedback(self, content_hash, service, title, verdict, stage, note, ts):
        """콘텐츠별 평가 피드백 upsert(콘텐츠당 최신 1건)."""
        c = self._conn()
        c.execute("""INSERT INTO feedback(content_hash,service,title,verdict,stage,note,ts)
          VALUES(?,?,?,?,?,?,?)
          ON CONFLICT(content_hash) DO UPDATE SET
            verdict=excluded.verdict, stage=excluded.stage, note=excluded.note, ts=excluded.ts""",
          (content_hash, service or "", title or "", verdict or "", stage or "analyze", note or "", ts))
        c.commit()

    def feedback_map(self) -> dict:
        """content_hash → {verdict,stage,note} (배치 결과에 현재 피드백 표시용)."""
        c = self._conn()
        out = {}
        for ch, v, s, n in c.execute("SELECT content_hash,verdict,stage,note FROM feedback"):
            out[ch] = {"verdict": v, "stage": s, "note": n}
        return out

    def learned_by_stage(self, limit_per_stage: int = 20) -> dict:
        """문제(bad) 피드백의 교정 메모를 단계별로 모아 학습 보정 텍스트로 컴파일."""
        c = self._conn()
        out = {"extract": [], "analyze": [], "review": [], "judge": []}
        for stage, note, title in c.execute(
                "SELECT stage,note,title FROM feedback WHERE verdict='bad' AND note!='' ORDER BY ts DESC"):
            st = stage if stage in out else "analyze"
            if len(out[st]) < limit_per_stage:
                t = (title or "").strip()
                out[st].append(f"- {note.strip()}" + (f" (예: {t})" if t else ""))
        return {k: "\n".join(v) for k, v in out.items() if v}

    def feedback_stats(self) -> dict:
        c = self._conn()
        n = int(c.execute("SELECT COUNT(*) FROM feedback").fetchone()[0])
        bad = int(c.execute("SELECT COUNT(*) FROM feedback WHERE verdict='bad'").fetchone()[0])
        good = int(c.execute("SELECT COUNT(*) FROM feedback WHERE verdict='good'").fetchone()[0])
        learned = sum(1 for _ in c.execute("SELECT 1 FROM feedback WHERE verdict='bad' AND note!=''"))
        return {"total": n, "good": good, "bad": bad, "learned": learned}

    def clear_feedback(self):
        c = self._conn()
        c.execute("DELETE FROM feedback"); c.commit()

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
