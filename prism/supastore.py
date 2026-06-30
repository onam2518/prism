"""Supabase(PostgREST) 백엔드 스토어 — SQLite Store 와 동일 메서드 계약.

dual-mode 의 한 축: PRISM_BACKEND=supabase 면 serve 가 이 스토어를 쓴다(기본은 sqlite).
의존성 0 유지를 위해 urllib(stdlib)로 PostgREST REST API 를 호출한다. 검수자는 auth uuid
(reviewer key)로 식별하며, 표시명/캐릭터는 prism.reviewers 에 둔다.

전제(운영): Supabase 대시보드에서 `prism` 스키마 노출 + 서버 env
  SUPABASE_URL, SUPABASE_SERVICE_KEY(service_role, 비밀).
설계: SUPABASE_MIGRATION.md
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


def configured() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"))


class SupabaseStore:
    """PostgREST 기반. RLS 는 service_role 로 우회(신원은 serve 의 JWT 검증으로 강제)."""

    def __init__(self):
        self.url = os.environ["SUPABASE_URL"].rstrip("/")
        self.key = os.environ["SUPABASE_SERVICE_KEY"]
        self.base = f"{self.url}/rest/v1"

    # ── REST 헬퍼 ──────────────────────────────────────────────────────────
    def _req(self, method: str, table: str, *, query: str = "", body=None, prefer: str = "") -> list:
        # public 스키마(기본 노출) + prism_ 접두사 → 노출 설정 불필요.
        url = f"{self.base}/prism_{table}" + (f"?{query}" if query else "")
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else []
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"supabase {method} {table} HTTP{e.code}: {detail}")

    def _get(self, table, query=""):
        return self._req("GET", table, query=query)

    def _upsert(self, table, rows):
        if not rows:
            return
        self._req("POST", table, body=rows, prefer="resolution=merge-duplicates,return=minimal")

    # ── 검수자 등록 + 팀(멀티테넌시) ──────────────────────────────────────
    def set_reviewer(self, reviewer, name=None, avatar="boksil", team_id=None):
        """reviewer = auth uuid(key). 이름·캐릭터·팀을 upsert."""
        row = {"id": reviewer, "name": name or reviewer, "avatar": avatar or "boksil"}
        if team_id:
            row["team_id"] = team_id
        self._upsert("reviewers", [row])

    def reviewer_team(self, reviewer):
        """검수자의 team_id(서버가 요청별로 팀 스코핑에 사용)."""
        rows = self._get("reviewers", f"select=team_id&id=eq.{urllib.parse.quote(reviewer)}")
        return rows[0].get("team_id") if rows else None

    def reviewers_map(self, team=None) -> dict:
        q = "select=id,name,avatar"
        if team:
            q += f"&team_id=eq.{urllib.parse.quote(team)}"
        out = {}
        for r in self._get("reviewers", q):
            out[r["id"]] = {"name": r.get("name") or r["id"], "avatar": r.get("avatar") or "boksil"}
        return out

    def ensure_team(self, uid, mode="create", name=None, code=None):
        """팀 생성/가입 → team_id. join: 초대코드 조회. create: 코드 생성·삽입."""
        if mode == "join" and code:
            rows = self._get("teams", f"select=id&invite_code=eq.{urllib.parse.quote(code.strip().upper())}")
            return rows[0]["id"] if rows else None
        import hashlib
        ic = hashlib.sha1(f"{uid}{name}{time.time()}".encode()).hexdigest()[:8].upper()
        rows = self._req("POST", "teams", body=[{"name": name or "내 팀", "invite_code": ic, "created_by": uid}],
                         prefer="return=representation")
        return rows[0]["id"] if rows else None

    def team_info(self, team_id):
        if not team_id:
            return None
        rows = self._get("teams", f"select=id,name,invite_code,created_by&id=eq.{urllib.parse.quote(team_id)}")
        return rows[0] if rows else None

    def team_members(self, team) -> list:
        rows = self._get("reviewers", f"select=id,name,avatar&team_id=eq.{urllib.parse.quote(team)}&order=name")
        return [{"id": r["id"], "name": r.get("name") or r["id"], "avatar": r.get("avatar") or "boksil"} for r in rows]

    def is_team_admin(self, uid, team) -> bool:
        t = self.team_info(team)
        return bool(t and uid and t.get("created_by") == uid)

    def clear_team_feedback(self, team):
        self._req("DELETE", "feedback", query=f"team_id=eq.{urllib.parse.quote(team)}", prefer="return=minimal")

    def clear_team_contents(self, team):
        self._req("DELETE", "contents", query=f"team_id=eq.{urllib.parse.quote(team)}", prefer="return=minimal")

    def remove_member(self, team, member_id):
        """팀원 제거(team_id 해제). 본인 데이터(feedback)는 남김."""
        self._req("PATCH", "reviewers", query=f"id=eq.{urllib.parse.quote(member_id)}&team_id=eq.{urllib.parse.quote(team)}",
                  body={"team_id": None}, prefer="return=minimal")

    # ── 골든셋(팀별, 관리자 등록) ──
    def register_golden(self, team, rows):
        """팀 골든셋 교체(기존 삭제 후 등록). rows: [{content, expected}]."""
        self.clear_golden(team)
        payload = [{"team_id": team, "content": r.get("content"), "expected": r.get("expected")}
                   for r in rows if r.get("content") and r.get("expected")]
        for i in range(0, len(payload), 500):
            self._req("POST", "golden", body=payload[i:i + 500], prefer="return=minimal")
        return len(payload)

    def get_golden(self, team, limit=1000):
        rows = self._get("golden", f"select=content,expected&team_id=eq.{urllib.parse.quote(team)}&limit={int(limit)}")
        return [{"content": r["content"], "expected": r["expected"]} for r in rows]

    def golden_count(self, team):
        return len(self._get("golden", f"select=id&team_id=eq.{urllib.parse.quote(team)}"))

    def clear_golden(self, team):
        self._req("DELETE", "golden", query=f"team_id=eq.{urllib.parse.quote(team)}", prefer="return=minimal")

    # ── 피드백(다중 의견) + REAP ──────────────────────────────────────────
    def save_feedback(self, content_hash, service, title, verdict, stage, note, ts, reviewer="(익명)", team=None):
        row = {"content_hash": content_hash, "reviewer_id": reviewer,
               "service": service or "", "title": title or "",
               "verdict": verdict or "", "stage": stage or "analyze", "note": note or ""}
        if team:
            row["team_id"] = team
        self._upsert("feedback", [row])

    def save_reap(self, content_hash, reviewer, reap: dict):
        q = f"content_hash=eq.{urllib.parse.quote(content_hash)}&reviewer_id=eq.{urllib.parse.quote(reviewer)}"
        self._req("PATCH", "feedback", query=q, body={
            "reap_remember": reap.get("remember", ""), "reap_explain": reap.get("explain", ""),
            "reap_ask": reap.get("ask", ""), "reap_plan": reap.get("plan", ""),
        }, prefer="return=minimal")

    def get_reap(self, content_hash) -> list:
        rows = self._get("feedback", "select=reviewer_id,reap_remember,reap_explain,reap_ask,reap_plan,stage"
                         f"&content_hash=eq.{urllib.parse.quote(content_hash)}&reap_plan=not.is.null")
        names = self.reviewers_map()
        return [{"reviewer": names.get(r["reviewer_id"], {}).get("name", r["reviewer_id"]),
                 "remember": r.get("reap_remember"), "explain": r.get("reap_explain"),
                 "ask": r.get("reap_ask"), "plan": r.get("reap_plan"), "stage": r.get("stage")}
                for r in rows if (r.get("reap_plan") or "").strip()]

    def _all_feedback(self, team=None) -> list:
        q = "select=content_hash,reviewer_id,verdict,stage,note,reap_plan,title,service,ts"
        if team:
            q += f"&team_id=eq.{urllib.parse.quote(team)}"
        return self._get("feedback", q)

    def feedback_map(self, team=None) -> dict:
        """content_hash → 합의 집계(SQLite 와 동일 shape). reviewer 라벨은 표시명."""
        names = self.reviewers_map(team)
        rows = sorted(self._all_feedback(team), key=lambda r: r.get("ts") or "")
        out = {}
        for r in rows:
            ch = r["content_hash"]
            rv = names.get(r["reviewer_id"], {}).get("name", r["reviewer_id"])
            e = out.setdefault(ch, {"verdicts": [], "good": 0, "bad": 0})
            v = r.get("verdict")
            e["verdicts"].append({"reviewer": rv, "verdict": v, "stage": r.get("stage"),
                                  "note": r.get("note"), "ts": r.get("ts")})
            if v == "good":
                e["good"] += 1
            elif v == "bad":
                e["bad"] += 1
        for e in out.values():
            g, b = e["good"], e["bad"]
            e["n"] = len(e["verdicts"])
            e["consensus"] = ("good" if g > b else "bad" if b > g else ("split" if (g or b) else ""))
            e["agree"] = e["n"] > 0 and (g == 0 or b == 0)
            last = e["verdicts"][-1]
            e["verdict"] = e["consensus"] or last["verdict"]
            e["stage"], e["note"] = last["stage"], last["note"]
        return out

    def feedback_stats(self, team=None) -> dict:
        rows = self._all_feedback(team)
        good = sum(1 for r in rows if r.get("verdict") == "good")
        bad = sum(1 for r in rows if r.get("verdict") == "bad")
        learned = sum(1 for r in rows if r.get("verdict") == "bad" and (r.get("reap_plan") or r.get("note")))
        contents = len({r["content_hash"] for r in rows})
        reviewers = len({r["reviewer_id"] for r in rows})
        by_c = {}
        for r in rows:
            by_c.setdefault(r["content_hash"], set()).add(r.get("verdict"))
        split = sum(1 for vs in by_c.values() if "good" in vs and "bad" in vs)
        return {"total": len(rows), "good": good, "bad": bad, "learned": learned,
                "contents": contents, "reviewers": reviewers, "split": split}

    def learned_by_stage(self, limit_per_stage: int = 20, team=None) -> dict:
        out = {"extract": [], "analyze": [], "review": [], "judge": []}
        rows = sorted(self._all_feedback(team), key=lambda r: r.get("ts") or "", reverse=True)
        for r in rows:
            if r.get("verdict") != "bad":
                continue
            text = (r.get("reap_plan") or "").strip() or (r.get("note") or "").strip()
            st = r.get("stage") if r.get("stage") in out else "analyze"
            if text and len(out[st]) < limit_per_stage:
                out[st].append(f"- {text}")
        return {k: "\n".join(v) for k, v in out.items() if v}

    def arena_stats(self, target: float = 0.9, team=None) -> dict:
        """팀 정확도 + 리더보드(점수·레벨·스트릭). SQLite 와 동일 shape."""
        names = self.reviewers_map(team)
        rows = self._all_feedback(team)
        DAY = 86400.0
        now = time.time()
        today = int(now // DAY)
        good = bad = 0
        board, days_by = {}, {}
        for r in rows:
            rid = r["reviewer_id"]
            b = board.setdefault(rid, {"reviews": 0, "corrections": 0})
            b["reviews"] += 1
            v = r.get("verdict")
            if v == "good":
                good += 1
            elif v == "bad":
                bad += 1
                if (r.get("reap_plan") or "").strip():
                    b["corrections"] += 1
            ts = _epoch(r.get("ts"))
            days_by.setdefault(rid, set()).add(int(ts // DAY))
        total = good + bad
        accuracy = round(good / total, 4) if total else 0.0

        def _streak(days):
            d = today
            if d not in days and (d - 1) not in days:
                return 0
            if d not in days:
                d -= 1
            s = 0
            while d in days:
                s += 1; d -= 1
            return s

        leaderboard = []
        for rid, v in board.items():
            pts = v["reviews"] * 10 + v["corrections"] * 25
            meta = names.get(rid, {})
            leaderboard.append({"reviewer": meta.get("name", rid), "reviews": v["reviews"],
                                "corrections": v["corrections"], "points": pts,
                                "level": 1 + pts // 100, "streak": _streak(days_by.get(rid, set())),
                                "char": meta.get("avatar", "boksil")})
        leaderboard.sort(key=lambda x: -x["points"])
        return {"accuracy": accuracy, "good": good, "bad": bad, "reviews": total,
                "week_reviews": 0, "accuracy_delta": 0.0, "target": target, "leaderboard": leaderboard}

    # ── 검토 콘텐츠 동기화 + 큐 + retention ────────────────────────────────
    def sync_contents(self, pairs, source: str = "단건", team=None):
        """검토 대상(review=='yellow')만 prism.contents 로 upsert(파이어호스 제외)."""
        from .store import content_hash
        rows = []
        for content, out in pairs:
            qm = out.get("quality_meta", {}) or {}
            if (qm.get("review") or "") != "yellow":
                continue                              # 검토 대상만
            row = {"hash": content_hash(content), "service": content.get("displayServiceName", ""),
                   "title": content.get("title", ""), "body": content.get("body", ""),
                   "source": source, "final_grade": qm.get("finalGrade", ""),
                   "item_meta": out.get("item_meta"), "quality_meta": qm,
                   "review": qm.get("review", "")}
            if team:
                row["team_id"] = team
            rows.append(row)
        self._upsert("contents", rows)
        return len(rows)

    def review_queue(self, limit: int = 100, only_unreviewed: bool = True, team=None) -> list:
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        rows = self._get("contents", "select=hash,service,title,final_grade,quality_meta,review,created_at"
                         f"&review=eq.yellow{tq}&order=created_at.desc&limit={int(limit) * 4}")
        reviewed = {r["content_hash"] for r in self._get("feedback", "select=content_hash" + tq)}
        out = []
        for r in rows:
            is_rev = r["hash"] in reviewed
            if only_unreviewed and is_rev:
                continue
            qm = r.get("quality_meta") or {}
            out.append({"hash": r["hash"], "service": r.get("service") or "", "title": r.get("title") or "",
                        "grade": r.get("final_grade") or "", "review_reason": qm.get("review_reason", ""),
                        "reviewed": is_rev, "ts": r.get("created_at")})
            if len(out) >= limit:
                break
        return out

    def retention(self, days: int = 30) -> int:
        """오래된 검토 콘텐츠 삭제(8GB 내 유지)."""
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - days * 86400))
        self._req("DELETE", "contents", query=f"created_at=lt.{cutoff}", prefer="return=minimal")
        return 0

    # ── dashboard/config 호환(검토 콘텐츠 기준) ──
    def count(self) -> int:
        return len(self._get("contents", "select=hash"))

    def grade_stats(self) -> dict:
        rows = self._get("contents", "select=final_grade")
        n = len(rows)
        g = sum(1 for r in rows if r.get("final_grade") == "G")
        return {"total": n, "g": g, "r": n - g, "gPct": round(g / n * 100) if n else 0}

    def recent_meta(self, limit: int = 200, team=None) -> list:
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        rows = self._get("contents", "select=hash,service,title,final_grade,item_meta,source"
                         f"{tq}&order=created_at.desc&limit={int(limit)}")
        out = []
        for r in rows:
            im = r.get("item_meta") or {}
            cat = " · ".join(f"{k}→{v}" for k, v in (im.get("content_category") or {}).items())
            out.append({"hash": r["hash"], "service": r.get("service") or "", "title": r.get("title") or "",
                        "grade": r.get("final_grade") or "", "summary": im.get("summary", ""),
                        "category": cat, "source": r.get("source") or "단건"})
        return out

    def recent(self, limit: int = 5000, team=None) -> list:
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        rows = self._get("contents", "select=item_meta,quality_meta"
                         f"{tq}&order=created_at.desc&limit={int(limit)}")
        out = [{"item_meta": r.get("item_meta") or {}, "quality_meta": r.get("quality_meta") or {}} for r in rows]
        out.reverse()
        return out

    def clear_feedback(self):
        self._req("DELETE", "feedback", query="content_hash=neq.__none__", prefer="return=minimal")

    def clear(self):
        self._req("DELETE", "contents", query="hash=neq.__none__", prefer="return=minimal")

    def log_usage(self, *a, **k):                  # supabase 모드는 usage 미적재(no-op)
        return None

    # 호환: serve 가 부르는 이름들(검토 콘텐츠 동기화로 위임)
    def save_many(self, pairs, run_id="", source="단건", team=None):
        return self.sync_contents(pairs, source, team=team)

    def save_dedup(self, pairs, run_id="", source="단건", team=None):
        n = self.sync_contents(pairs, source, team=team)
        return {"inserted": n, "updated": 0, "skipped": 0}


def _epoch(ts) -> float:
    """timestamptz 문자열 → epoch. 실패 시 0."""
    if not ts:
        return 0.0
    try:
        s = str(ts)[:19]
        return time.mktime(time.strptime(s, "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0.0
