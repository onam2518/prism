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

    def get_reviewer(self, reviewer):
        """기존 검수자 프로필(이름·캐릭터·팀·배지). 로그인 시 재입력 없이 로드. 없으면 None."""
        rows = self._get("reviewers", f"select=name,avatar,team_id,badges&id=eq.{urllib.parse.quote(reviewer)}")
        if not rows:
            return None
        r = rows[0]
        return {"name": r.get("name") or reviewer, "char": r.get("avatar") or "boksil",
                "team": r.get("team_id"), "badges": r.get("badges") or []}

    def save_badges(self, uid, earned) -> list:
        """획득 배지 라벨을 서버에 영속(기존 ∪ 신규, 단조 증가). 최신 전체 목록 반환.
        기기 간 '이미 축하함' 기준선이 되어 중복 축하를 막는다."""
        if not uid:
            return list(earned or [])
        rows = self._get("reviewers", f"select=badges&id=eq.{urllib.parse.quote(uid)}")
        cur = (rows[0].get("badges") if rows else None) or []
        merged = list(cur)
        for b in (earned or []):
            if b not in merged:
                merged.append(b)
        if merged != cur:
            self._req("PATCH", "reviewers", query=f"id=eq.{urllib.parse.quote(uid)}",
                      body={"badges": merged}, prefer="return=minimal")
        return merged

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
        rows = self._get("reviewers", f"select=id,name,avatar,is_admin&team_id=eq.{urllib.parse.quote(team)}&order=name")
        return [{"id": r["id"], "name": r.get("name") or r["id"], "avatar": r.get("avatar") or "boksil",
                 "is_admin": bool(r.get("is_admin"))} for r in rows]

    def is_team_admin(self, uid, team) -> bool:
        t = self.team_info(team)
        if t and uid and t.get("created_by") == uid:      # 생성자는 항상 관리자
            return True
        if not (uid and team):
            return False
        rows = self._get("reviewers", f"select=is_admin&id=eq.{urllib.parse.quote(uid)}"
                         f"&team_id=eq.{urllib.parse.quote(team)}")
        return bool(rows and rows[0].get("is_admin"))      # 위임된 관리자

    def set_member_admin(self, team, member_id, on: bool):
        """멤버에게 관리자 권한 위임/회수(생성자는 대상 아님)."""
        self._req("PATCH", "reviewers",
                  query=f"id=eq.{urllib.parse.quote(member_id)}&team_id=eq.{urllib.parse.quote(team)}",
                  body={"is_admin": bool(on)}, prefer="return=minimal")

    def clear_team_feedback(self, team):
        self._req("DELETE", "feedback", query=f"team_id=eq.{urllib.parse.quote(team)}", prefer="return=minimal")

    def clear_team_contents(self, team):
        self._req("DELETE", "contents", query=f"team_id=eq.{urllib.parse.quote(team)}", prefer="return=minimal")

    def remove_member(self, team, member_id):
        """팀원 제거(team_id 해제). 본인 데이터(feedback)는 남김."""
        self._req("PATCH", "reviewers", query=f"id=eq.{urllib.parse.quote(member_id)}&team_id=eq.{urllib.parse.quote(team)}",
                  body={"team_id": None}, prefer="return=minimal")

    # ── 골든셋(팀별 · 누적 upsert + 출처 태깅) ──
    def _team_q(self, team) -> str:
        return f"team_id=eq.{urllib.parse.quote(team)}" if team else "team_id=is.null"

    def upsert_golden(self, content_hash, content, expected, team=None, source="review"):
        """골든 엔트리 upsert(누적). (team, content_hash) 키 · 서버 단일 작성자라 삭제 후 삽입."""
        self._req("DELETE", "golden",
                  query=f"{self._team_q(team)}&content_hash=eq.{urllib.parse.quote(content_hash)}",
                  prefer="return=minimal")
        row = {"content_hash": content_hash, "content": content, "expected": expected,
               "source": source or "review"}
        if team:
            row["team_id"] = team
        self._req("POST", "golden", body=[row], prefer="return=minimal")

    def register_golden(self, team, rows, replace=True, source="manual"):
        """골든셋 등록. replace=True 면 팀 전체 교체, False 면 병합(upsert)."""
        from .store import content_hash
        if replace:
            self.clear_golden(team)
            payload = [{"team_id": team, "content": r.get("content"), "expected": r.get("expected"),
                        "content_hash": content_hash(r.get("content") or {}), "source": source or "manual"}
                       for r in rows if r.get("content") and r.get("expected")]
            for i in range(0, len(payload), 500):
                self._req("POST", "golden", body=payload[i:i + 500], prefer="return=minimal")
            return len(payload)
        n = 0
        for r in rows:
            if r.get("content") and r.get("expected"):
                self.upsert_golden(content_hash(r["content"]), r["content"], r["expected"],
                                   team=team, source=source or "manual")
                n += 1
        return n

    def get_golden(self, team, limit=1000):
        rows = self._get("golden", f"select=content,expected&{self._team_q(team)}&limit={int(limit)}")
        return [{"content": r["content"], "expected": r["expected"]} for r in rows]

    def golden_hashes(self, team=None) -> set:
        rows = self._get("golden", f"select=content_hash&{self._team_q(team)}&limit=10000")
        return {r["content_hash"] for r in rows if r.get("content_hash")}

    def golden_rows(self, team=None, limit=300) -> list:
        rows = self._get("golden", "select=content_hash,content,expected,source,created_at"
                         f"&{self._team_q(team)}&order=created_at.desc&limit={int(limit)}")
        out = []
        for r in rows:
            ct = r.get("content") or {}
            ex = r.get("expected") or {}
            out.append({"hash": r.get("content_hash") or "", "title": ct.get("title", ""),
                        "service": ct.get("displayServiceName", ""), "grade": ex.get("finalGrade", ""),
                        "category": ex.get("content_category", []) or [],
                        "source": r.get("source") or "review", "ts": _epoch(r.get("created_at"))})
        return out

    def golden_source_counts(self, team=None) -> dict:
        rows = self._get("golden", f"select=source&{self._team_q(team)}&limit=10000")
        out = {}
        for r in rows:
            k = r.get("source") or "review"
            out[k] = out.get(k, 0) + 1
        return out

    def remove_golden(self, content_hash, team=None) -> bool:
        self._req("DELETE", "golden",
                  query=f"{self._team_q(team)}&content_hash=eq.{urllib.parse.quote(content_hash)}",
                  prefer="return=minimal")
        return True

    def golden_count(self, team):
        return len(self._get("golden", f"select=id&{self._team_q(team)}"))

    def clear_golden(self, team):
        self._req("DELETE", "golden", query=self._team_q(team), prefer="return=minimal")

    def golden_contrib_counts(self, team=None) -> dict:
        """reviewer → 골든 확정 기여 수(events kind='golden:*' 1회 기록 기반)."""
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        rows = self._get("events", f"select=reviewer_id&kind=like.golden:*{tq}&limit=20000")
        out = {}
        for r in rows:
            k = r.get("reviewer_id") or ""
            out[k] = out.get(k, 0) + 1
        return out

    # ── 피드백(다중 의견) + REAP ──────────────────────────────────────────
    def save_feedback(self, content_hash, service, title, verdict, stage, note, ts, reviewer="(익명)", team=None, element=""):
        row = {"content_hash": content_hash, "reviewer_id": reviewer,
               "service": service or "", "title": title or "",
               "verdict": verdict or "", "stage": stage or "analyze", "note": note or "",
               "element": element or ""}
        if team:
            row["team_id"] = team
        self._upsert("feedback", [row])

    # ── 교정 로그(append-only) · 골드 문항 · 이벤트 ──
    def log_patch(self, content_hash, reviewer, element, before, after, team=None):
        row = {"content_hash": content_hash, "reviewer_id": reviewer or None,
               "element": element or "", "before": before, "after": after}
        if team:
            row["team_id"] = team
        self._req("POST", "patch_log", body=[row], prefer="return=minimal")

    def patch_rows(self, limit: int = 5000, team=None) -> list:
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        rows = self._get("patch_log", "select=content_hash,reviewer_id,element,before,after,created_at"
                         f"{tq}&order=created_at.desc&limit={int(limit)}")
        return [{"hash": r["content_hash"], "reviewer": r.get("reviewer_id") or "",
                 "element": r.get("element") or "", "before": r.get("before") or {},
                 "after": r.get("after") or {}, "ts": _epoch(r.get("created_at"))} for r in rows]

    def patch_counts(self, team=None) -> dict:
        out = {}
        for r in self.patch_rows(limit=10000, team=team):
            out[r["reviewer"]] = out.get(r["reviewer"], 0) + 1
        return out

    def save_gold_check(self, content_hash, reviewer, expected, verdict, team=None) -> bool:
        ok = (verdict or "") == (expected or "")
        row = {"content_hash": content_hash, "reviewer_id": reviewer or None,
               "expected": expected or "", "verdict": verdict or "", "correct": ok}
        if team:
            row["team_id"] = team
        self._req("POST", "gold_checks", body=[row], prefer="return=minimal")
        return ok

    def _gold_rows(self, team=None, extra="") -> list:
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        return self._get("gold_checks", "select=content_hash,reviewer_id,correct,created_at"
                         f"{tq}{extra}&limit=20000")

    def gold_stats(self, team=None) -> dict:
        out = {}
        for r in self._gold_rows(team):
            e = out.setdefault(r.get("reviewer_id") or "", {"n": 0, "correct": 0})
            e["n"] += 1
            e["correct"] += int(bool(r.get("correct")))
        for e in out.values():
            e["acc"] = round(e["correct"] / e["n"], 4) if e["n"] else 0.0
        return out

    def gold_answered(self, reviewer, team=None) -> set:
        rows = self._gold_rows(team, extra=f"&reviewer_id=eq.{urllib.parse.quote(reviewer or '')}")
        return {r["content_hash"] for r in rows}

    def _today_iso(self):
        return time.strftime("%Y-%m-%dT00:00:00", time.gmtime())

    def gold_today(self, reviewer, team=None) -> dict:
        rows = self._gold_rows(team, extra=(f"&reviewer_id=eq.{urllib.parse.quote(reviewer or '')}"
                                            f"&created_at=gte.{self._today_iso()}"))
        return {"n": len(rows), "correct": sum(int(bool(r.get("correct"))) for r in rows)}

    def feedback_today(self, reviewer, team=None) -> int:
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        rows = self._get("feedback", "select=content_hash"
                         f"&reviewer_id=eq.{urllib.parse.quote(reviewer or '')}"
                         f"&ts=gte.{self._today_iso()}{tq}")
        return len(rows)

    def patches_today(self, reviewer, team=None) -> int:
        """검수자의 오늘 구조화 교정 건수(분류 채우기 미션 판정용)."""
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        rows = self._get("patch_log", "select=id"
                         f"&reviewer_id=eq.{urllib.parse.quote(reviewer or '')}"
                         f"&created_at=gte.{self._today_iso()}{tq}")
        return len(rows)

    def split_reviewed_today(self, reviewer, team=None) -> int:
        """검수자가 오늘 의견 갈린(split) 콘텐츠에 판정한 건수(불일치 재검토 미션 판정용)."""
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        mine = self._get("feedback", "select=content_hash"
                         f"&reviewer_id=eq.{urllib.parse.quote(reviewer or '')}"
                         f"&ts=gte.{self._today_iso()}{tq}")
        if not mine:
            return 0
        hashes = {r["content_hash"] for r in mine}
        by_c = {}
        for r in self._all_feedback(team):
            if r.get("verdict") in ("good", "bad"):
                by_c.setdefault(r["content_hash"], set()).add(r["verdict"])
        return sum(1 for h in hashes if len(by_c.get(h, ())) > 1)

    def log_event_once(self, reviewer, kind, day, bonus, meta="", team=None) -> bool:
        q = (f"reviewer_id=eq.{urllib.parse.quote(reviewer or '')}"
             f"&kind=eq.{urllib.parse.quote(kind)}&day=eq.{int(day)}")
        if self._get("events", "select=id&" + q):
            return False
        row = {"reviewer_id": reviewer or None, "kind": kind, "day": int(day),
               "bonus": int(bonus), "meta": meta or ""}
        if team:
            row["team_id"] = team
        self._req("POST", "events", body=[row], prefer="return=minimal")
        return True

    def event_bonus(self, team=None) -> dict:
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        rows = self._get("events", f"select=reviewer_id,bonus,created_at{tq}&limit=20000")
        week_ago = time.time() - 7 * 86400.0
        out = {}
        for r in rows:
            e = out.setdefault(r.get("reviewer_id") or "", {"total": 0, "week": 0})
            b = int(r.get("bonus") or 0)
            e["total"] += b
            if _epoch(r.get("created_at")) >= week_ago:
                e["week"] += b
        return out

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
            e["verdicts"].append({"reviewer": rv, "reviewer_id": r["reviewer_id"], "verdict": v,
                                  "stage": r.get("stage"), "note": r.get("note"), "ts": r.get("ts")})
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

    def save_routes(self, content_hash, reviewer, items, team=None, model=""):
        """오케스트레이터 재분류 결과 append(초안 생성 모델 귀속 포함)."""
        rows = []
        for it in items:
            if not it.get("directive"):
                continue
            row = {"content_hash": content_hash, "reviewer_id": reviewer or None,
                   "element": it.get("element", ""), "stage": it.get("stage", "analyze"),
                   "directive": it.get("directive", ""), "model": model or ""}
            if team:
                row["team_id"] = team
            rows.append(row)
        if rows:
            self._req("POST", "feedback_routes", body=rows, prefer="return=minimal")

    def routes_by_stage(self, limit_per_stage: int = 20, team=None) -> dict:
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        rows = self._get("feedback_routes", "select=stage,directive"
                         f"{tq}&order=created_at.desc&limit={limit_per_stage * 4}")
        out = {}
        for r in rows:
            st = r.get("stage") if r.get("stage") in ("extract", "analyze", "review", "judge") else "analyze"
            lst = out.setdefault(st, [])
            dv = (r.get("directive") or "").strip()
            if dv and len(lst) < limit_per_stage:
                lst.append(dv)
        return out

    def learned_by_stage(self, limit_per_stage: int = 20, team=None) -> dict:
        out = {"extract": [], "analyze": [], "review": [], "judge": []}
        for st, items in self.routes_by_stage(limit_per_stage, team=team).items():
            out[st].extend(f"- {t}" for t in items)
        rows = sorted(self._all_feedback(team), key=lambda r: r.get("ts") or "", reverse=True)
        for r in rows:
            if r.get("verdict") != "bad":
                continue
            text = (r.get("reap_plan") or "").strip() or (r.get("note") or "").strip()
            st = r.get("stage") if r.get("stage") in out else "analyze"
            line = f"- {text}"
            if text and len(out[st]) < limit_per_stage and line not in out[st]:
                out[st].append(line)
        return {k: "\n".join(v) for k, v in out.items() if v}

    def arena_stats(self, target: float = 0.9, team=None) -> dict:
        """팀 정확도 + 리더보드 · 품질 가중(SQLite 와 동일 산식).
        점수 = (검수 10 + 교정 25 + 구조화 교정 5 + 합의 일치 5 + 골드 응답 10) × 품질 배율 + 미션 보너스."""
        names = self.reviewers_map(team)
        rows = self._all_feedback(team)
        DAY = 86400.0
        now = time.time()
        week_ago = now - 7 * DAY
        prev_ago = now - 14 * DAY                     # 지난주 창(리그 승급/강등 비교)
        today = int(now // DAY)
        good = bad = wk_good = wk_bad = 0
        board, days_by = {}, {}
        by_content = {}                               # {hash: [(reviewer, verdict)]}
        for r in rows:
            rid = r["reviewer_id"]
            b = board.setdefault(rid, {"reviews": 0, "corrections": 0,
                                       "wk_reviews": 0, "wk_corr": 0, "pv_reviews": 0, "pv_corr": 0})
            b["reviews"] += 1
            ts = _epoch(r.get("ts"))
            this_wk = ts >= week_ago
            last_wk = week_ago > ts >= prev_ago
            if this_wk:
                b["wk_reviews"] += 1
            elif last_wk:
                b["pv_reviews"] += 1
            v = r.get("verdict")
            if v in ("good", "bad"):
                by_content.setdefault(r["content_hash"], []).append((rid, v))
            if v == "good":
                good += 1
                wk_good += int(this_wk)
            elif v == "bad":
                bad += 1
                wk_bad += int(this_wk)
                if (r.get("reap_plan") or "").strip():
                    b["corrections"] += 1
                    if this_wk:
                        b["wk_corr"] += 1
                    elif last_wk:
                        b["pv_corr"] += 1
            days_by.setdefault(rid, set()).add(int(ts // DAY))
        total = good + bad
        accuracy = round(good / total, 4) if total else 0.0

        # 합의 일치·불일치 참여·합의 대비 일치율(n>=2 콘텐츠만)
        cons_match, split_part, agree_hit, agree_n = {}, {}, {}, {}
        for ch, votes in by_content.items():
            if len(votes) < 2:
                continue
            gn = sum(1 for _, v in votes if v == "good")
            bn = len(votes) - gn
            cons = "good" if gn > bn else ("bad" if bn > gn else "split")
            for rid, v in votes:
                others_g = gn - (1 if v == "good" else 0)
                others_b = bn - (1 if v == "bad" else 0)
                if others_g and others_b:
                    split_part[rid] = split_part.get(rid, 0) + 1
                if cons != "split":
                    agree_n[rid] = agree_n.get(rid, 0) + 1
                    if v == cons:
                        agree_hit[rid] = agree_hit.get(rid, 0) + 1
                        cons_match[rid] = cons_match.get(rid, 0) + 1

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

        # 검수 대상(팀 YELLOW 콘텐츠) 총량 → 진척율 분모(contents 는 YELLOW 만 적재)
        if team:
            total_targets = len(self._get("contents", "select=hash&team_id=eq." + urllib.parse.quote(team)))
        else:
            total_targets = self.count()
        gold = self.gold_stats(team)
        patches = self.patch_counts(team)
        bonuses = self.event_bonus(team)
        gcontrib = self.golden_contrib_counts(team)

        def _prog(rc):
            return round(min(rc, total_targets) / total_targets, 4) if total_targets else 0.0

        def _mult(rid):
            gs = gold.get(rid) or {}
            return round(0.5 + 0.5 * gs["acc"], 4) if gs.get("n", 0) >= 5 else 1.0

        leaderboard = []
        for rid, v in board.items():
            gs = gold.get(rid) or {"n": 0, "acc": 0.0}
            mult = _mult(rid)
            base = (v["reviews"] * 10 + v["corrections"] * 25 + patches.get(rid, 0) * 5
                    + cons_match.get(rid, 0) * 5 + gs["n"] * 10)
            pts = round(base * mult) + (bonuses.get(rid) or {}).get("total", 0)
            wk_base = v["wk_reviews"] * 10 + v["wk_corr"] * 25
            pv_base = v["pv_reviews"] * 10 + v["pv_corr"] * 25
            meta = names.get(rid, {})
            leaderboard.append({"reviewer": meta.get("name", rid), "reviews": v["reviews"],
                                "corrections": v["corrections"], "points": pts,
                                "level": 1 + pts // 100, "streak": _streak(days_by.get(rid, set())),
                                "char": meta.get("avatar", "boksil"), "progress": _prog(v["reviews"]),
                                "week_points": round(wk_base * mult) + (bonuses.get(rid) or {}).get("week", 0),
                                "last_week_points": round(pv_base * mult),
                                "gold_n": gs["n"], "gold_acc": gs["acc"], "quality_mult": mult,
                                "consensus_matches": cons_match.get(rid, 0),
                                "split_reviews": split_part.get(rid, 0),
                                "patches": patches.get(rid, 0),
                                "golden_contribs": gcontrib.get(rid, 0),
                                "agree_rate": (round(agree_hit.get(rid, 0) / agree_n[rid], 4)
                                               if agree_n.get(rid) else None)})
        leaderboard.sort(key=lambda x: -x["points"])
        members = set(names.keys()) | set(board.keys())  # 팀 전원(검수 이력 없어도 평균에 포함)
        team_progress = round(sum(_prog(board.get(m, {}).get("reviews", 0)) for m in members) / len(members), 4) if (members and total_targets) else 0.0
        return {"accuracy": accuracy, "good": good, "bad": bad, "reviews": total,
                "week_reviews": wk_good + wk_bad, "accuracy_delta": 0.0,
                "target": target, "leaderboard": leaderboard,
                "total_targets": total_targets, "team_progress": team_progress}

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
                   "source_url": content.get("source_url", "") or content.get("url", ""),
                   "source": source, "final_grade": qm.get("finalGrade", ""),
                   "item_meta": out.get("item_meta"), "quality_meta": qm,
                   "model": (out.get("trace") or {}).get("model", "") or "",
                   "review": qm.get("review", "")}
            if team:
                row["team_id"] = team
            rows.append(row)
        self._upsert("contents", rows)
        return len(rows)

    def review_queue(self, limit: int = 100, only_unreviewed: bool = True, team=None) -> list:
        """정렬 = split 재검토 우선 → 모델 확신 낮은 순(불확실성 샘플링) → 최신순."""
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        rows = self._get("contents", "select=hash,service,title,body,source_url,final_grade,item_meta,quality_meta,review,model,created_at"
                         f"&review=eq.yellow{tq}&order=created_at.desc&limit={int(limit) * 4}")
        fb = self._get("feedback", "select=content_hash,verdict" + tq)
        reviewed = {r["content_hash"] for r in fb}
        by_c = {}
        for r in fb:
            if r.get("verdict") in ("good", "bad"):
                by_c.setdefault(r["content_hash"], set()).add(r["verdict"])
        split = {ch for ch, vs in by_c.items() if len(vs) > 1}
        out = []
        for r in rows:
            is_rev = r["hash"] in reviewed
            is_split = r["hash"] in split
            if only_unreviewed and is_rev and not is_split:
                continue
            qm = r.get("quality_meta") or {}
            im = r.get("item_meta") or {}
            out.append({"hash": r["hash"], "service": r.get("service") or "", "title": r.get("title") or "",
                        "body": r.get("body") or "", "url": r.get("source_url") or "",
                        "summary": im.get("summary", ""), "entities": im.get("entities", []) or [],
                        "intent": im.get("intent", []) or [], "category": im.get("content_category", []) or [],
                        "grade": r.get("final_grade") or "", "reasons": qm.get("reasons", []) or [],
                        "review_reason": qm.get("review_reason", ""),
                        "reviewed": is_rev, "split": is_split, "model": r.get("model") or "",
                        "confidence": qm.get("confidence"), "ts": r.get("created_at")})
        out.sort(key=lambda r: (0 if r["split"] else 1,
                                r["confidence"] if isinstance(r.get("confidence"), (int, float)) else 1.0,
                                -_epoch(r.get("ts"))))
        return out[:limit]

    def contents_by_hash(self, team=None, limit: int = 5000) -> dict:
        """content_hash → 콘텐츠 dict(학습데이터 추출용)."""
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        rows = self._get("contents", f"select=hash,service,title,body{tq}&limit={int(limit)}")
        return {r["hash"]: {"displayServiceName": r.get("service") or "", "title": r.get("title") or "",
                            "subtitle": "", "body": r.get("body") or ""} for r in rows}

    def get_item_meta(self, content_hash) -> dict | None:
        """저장된 item_meta 조회(교정 로그 before 스냅샷용)."""
        rows = self._get("contents", f"select=item_meta&hash=eq.{urllib.parse.quote(content_hash)}")
        if not rows:
            return None
        im = rows[0].get("item_meta")
        return im if isinstance(im, dict) else {}

    def update_item_meta(self, content_hash, patch: dict) -> bool:
        """검수자 구조화 교정: contents.item_meta 패치(빈 카테고리 채우기 등)."""
        rows = self._get("contents", f"select=item_meta&hash=eq.{urllib.parse.quote(content_hash)}")
        if not rows:
            return False
        im = rows[0].get("item_meta") or {}
        im.update(patch or {})
        self._req("PATCH", "contents", query=f"hash=eq.{urllib.parse.quote(content_hash)}",
                  body={"item_meta": im}, prefer="return=minimal")
        return True

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
        rows = self._get("contents", "select=hash,service,title,final_grade,item_meta,source,model"
                         f"{tq}&order=created_at.desc&limit={int(limit)}")
        out = []
        for r in rows:
            im = r.get("item_meta") or {}
            cat = " · ".join(im.get("content_category") or [])
            out.append({"hash": r["hash"], "service": r.get("service") or "", "title": r.get("title") or "",
                        "grade": r.get("final_grade") or "", "summary": im.get("summary", ""),
                        "category": cat, "source": r.get("source") or "단건", "model": r.get("model") or ""})
        return out

    def recent(self, limit: int = 5000, team=None) -> list:
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        rows = self._get("contents", "select=hash,service,title,body,source_url,item_meta,quality_meta,model"
                         f"{tq}&order=created_at.desc&limit={int(limit)}")
        out = [{"item_meta": r.get("item_meta") or {}, "quality_meta": r.get("quality_meta") or {},
                "trace": {"model": r.get("model") or ""},
                "content_ref": {"title": r.get("title", ""), "displayServiceName": r.get("service", ""),
                                "body": r.get("body", ""), "source_url": r.get("source_url", ""),
                                "body_hash": r.get("hash", "")}} for r in rows]
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
