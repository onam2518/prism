"""Supabase(PostgREST) 백엔드 스토어 · SQLite Store 와 동일 메서드 계약.

dual-mode 의 한 축: PRISM_BACKEND=supabase 면 serve 가 이 스토어를 쓴다(기본은 sqlite).
의존성 0 유지를 위해 urllib(stdlib)로 PostgREST REST API 를 호출한다. 검수자는 auth uuid
(reviewer key)로 식별하며, 표시명/캐릭터는 prism.reviewers 에 둔다.

전제(운영): Supabase 대시보드에서 `prism` 스키마 노출 + 서버 env
  SUPABASE_URL, SUPABASE_SERVICE_KEY(service_role, 비밀).
설계: SUPABASE_MIGRATION.md
"""
from __future__ import annotations

import http.client
import json
import threading
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .store import level_of


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
        path = url[len(self.url):]                       # /rest/v1/… (keep-alive 는 host 기준)
        status, raw = self._http(method, path, data, headers)
        if status >= 400:
            raise RuntimeError(f"supabase {method} {table} HTTP{status}: {raw[:300]}")
        return json.loads(raw) if raw.strip() else []

    _TLS = threading.local()                             # 스레드별 keep-alive 연결(ThreadingHTTPServer 대응)

    def _http(self, method, path, data, headers):
        """PostgREST 호출을 keep-alive 연결로 실행. 매 호출 새 TLS 핸드셰이크(urllib)가
        도쿄(Fly)→서울(supabase) 왕복을 요청마다 추가하던 비용 제거(2026-07-08 실측 API 400~860ms)."""
        host = self.url.split("://", 1)[1]
        for attempt in (0, 1):                           # 유휴 종료된 소켓은 1회 재수립
            conns = getattr(self._TLS, "conns", None)
            if conns is None:
                conns = self._TLS.conns = {}
            c = conns.get(host)
            if c is None:
                c = conns[host] = http.client.HTTPSConnection(host, timeout=20)
            try:
                c.request(method, path, body=data, headers=headers)
                resp = c.getresponse()
                return resp.status, resp.read().decode("utf-8", "replace")
            except (http.client.HTTPException, ConnectionError, OSError):
                try:
                    c.close()
                except Exception:
                    pass
                conns.pop(host, None)
                if attempt:
                    raise

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
        if mode == "join":
            if not code:            # 코드 없는 join 이 조용히 새 팀을 만들던 사고 방지(유령 '내 팀')
                return None
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
        rows = self._get("reviewers", f"select=id,name,avatar,is_admin,super_admin&team_id=eq.{urllib.parse.quote(team)}&order=name")
        return [{"id": r["id"], "name": r.get("name") or r["id"], "avatar": r.get("avatar") or "boksil",
                 "is_admin": bool(r.get("is_admin")), "super_admin": bool(r.get("super_admin"))} for r in rows]

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

    def is_team_super(self, uid, team) -> bool:
        """슈퍼관리자(생성자 OR super_admin 위임) · 운영 작업 메뉴 전체(시스템 설정 제외)."""
        t = self.team_info(team)
        if t and uid and t.get("created_by") == uid:      # 생성자는 항상 슈퍼관리자
            return True
        if not (uid and team):
            return False
        rows = self._get("reviewers", f"select=super_admin&id=eq.{urllib.parse.quote(uid)}"
                         f"&team_id=eq.{urllib.parse.quote(team)}")
        return bool(rows and rows[0].get("super_admin"))

    def set_member_super(self, team, member_id, on: bool):
        """슈퍼관리자 위임/회수 · 부여는 팀 생성자만(게이트는 adminops.admin_action)."""
        self._req("PATCH", "reviewers",
                  query=f"id=eq.{urllib.parse.quote(member_id)}&team_id=eq.{urllib.parse.quote(team)}",
                  body={"super_admin": bool(on)}, prefer="return=minimal")

    def clear_team_feedback(self, team):
        self._req("DELETE", "feedback", query=f"team_id=eq.{urllib.parse.quote(team)}", prefer="return=minimal")

    def clear_team_contents(self, team):
        self._req("DELETE", "contents", query=f"team_id=eq.{urllib.parse.quote(team)}", prefer="return=minimal")
        self._req("DELETE", "drafts", query=f"team_key=eq.{urllib.parse.quote(team)}", prefer="return=minimal")

    def remove_content(self, content_hash: str, team=None) -> bool:
        """콘텐츠 개별 삭제(관리자): 결과 + 파생(초안 이력·검수 피드백·평가 판정) 연쇄 삭제.
        골든(확정 정답)과 patch_log(교정 이력·DPO 원천)는 보존한다. purpose 는 contents 컬럼이라 함께 삭제."""
        h = (content_hash or "").strip()
        if not h:
            return False
        hq = urllib.parse.quote(h)
        tid = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        tk = f"&team_key=eq.{urllib.parse.quote(team)}" if team else ""
        self._req("DELETE", "contents", query=f"hash=eq.{hq}" + tid, prefer="return=minimal")
        self._req("DELETE", "drafts", query=f"content_hash=eq.{hq}" + tk, prefer="return=minimal")
        self._req("DELETE", "feedback", query=f"content_hash=eq.{hq}" + tid, prefer="return=minimal")
        self._req("DELETE", "eval_checks", query=f"hash=eq.{hq}" + tid, prefer="return=minimal")
        return True

    def set_source_url(self, content_hash, url, team=None) -> bool:
        """원문 링크 백필: contents.source_url 단일 컬럼만 PATCH(초안·판정 등 파생 불변).
        return=representation 으로 실제 매칭 행이 있었는지 판별한다."""
        h = (content_hash or "").strip()
        if not h:
            return False
        tid = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        rows = self._req("PATCH", "contents", query=f"hash=eq.{urllib.parse.quote(h)}" + tid,
                         body={"source_url": url}, prefer="return=representation")
        return bool(rows)

    def delete_team(self, team):
        """팀 삭제(위험): 멤버 소속 해제 후 팀 행 삭제. 콘텐츠·피드백 등 팀 데이터는 별도 삭제."""
        if not team:
            return
        self._req("PATCH", "reviewers", query=f"team_id=eq.{urllib.parse.quote(team)}",
                  body={"team_id": None}, prefer="return=minimal")
        self._req("DELETE", "teams", query=f"id=eq.{urllib.parse.quote(team)}", prefer="return=minimal")

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

    def delete_feedback(self, content_hash, reviewer, team=None) -> str:
        """판정 실행취소: 해당 검수자의 표 행을 삭제(빈 표 upsert 는 팀 표 수를 부풀린다).
        반환: 삭제된 이전 판정('' = 행 없음)."""
        h = (content_hash or "").strip()
        if not (h and reviewer):
            return ""
        q = (f"content_hash=eq.{urllib.parse.quote(h)}&reviewer_id=eq.{urllib.parse.quote(reviewer)}"
             + (f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""))
        rows = self._get("feedback", "select=verdict&" + q)
        if not rows:
            return ""
        self._req("DELETE", "feedback", query=q, prefer="return=minimal")
        return rows[0].get("verdict") or ""

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

    def batch_seq(self, team=None) -> int:
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        return len(self._get("events", f"select=id&kind=eq.learn_batch{tq}&limit=10000"))

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
        q = "select=content_hash,reviewer_id,verdict,stage,note,element,reap_plan,title,service,ts"
        if team:
            q += f"&team_id=eq.{urllib.parse.quote(team)}"
        return self._get("feedback", q)

    def feedback_map(self, team=None) -> dict:
        """content_hash → 합의 집계(SQLite 와 동일 shape). reviewer 라벨은 표시명."""
        names = self.reviewers_map(team)
        rows = sorted(self._all_feedback(team), key=lambda r: r.get("ts") or "")
        out = {}
        for r in rows:
            v = r.get("verdict")
            if v not in ("good", "bad"):           # 과거 취소가 남긴 빈 표는 집계 제외(표 수 정합)
                continue
            ch = r["content_hash"]
            rv = names.get(r["reviewer_id"], {}).get("name", r["reviewer_id"])
            e = out.setdefault(ch, {"verdicts": [], "good": 0, "bad": 0})
            e["verdicts"].append({"reviewer": rv, "reviewer_id": r["reviewer_id"], "verdict": v,
                                  "stage": r.get("stage"), "note": r.get("note"), "ts": r.get("ts"),
                                  "element": r.get("element") or ""})
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
        """공통(모델 미기록) 라우트만 · 모델 귀속 라우트는 routes_by_stage_model 참조."""
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        rows = self._get("feedback_routes", "select=stage,directive"
                         f"{tq}&or=(model.is.null,model.eq.)"
                         f"&order=created_at.desc&limit={limit_per_stage * 4}")
        out = {}
        seen = set()
        for r in rows:
            st = r.get("stage") if r.get("stage") in ("extract", "analyze", "review", "judge") else "analyze"
            d = (r.get("directive") or "").strip()
            if not d or (st, d) in seen:
                continue
            seen.add((st, d))
            lst = out.setdefault(st, [])
            if len(lst) < limit_per_stage:
                lst.append(d)
        return out

    def routes_by_stage_model(self, limit_per_stage: int = 20, team=None) -> dict:
        """모델 귀속 라우트: {model: {stage: [directive, …]}} · 모델별 learned 계층의 원천."""
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        rows = self._get("feedback_routes", "select=stage,directive,model"
                         f"{tq}&model=neq.&order=created_at.desc&limit={limit_per_stage * 8}")
        out = {}
        seen = set()
        for r in rows:
            st = r.get("stage") if r.get("stage") in ("extract", "analyze", "review", "judge") else "analyze"
            d, m = (r.get("directive") or "").strip(), (r.get("model") or "").strip()
            if not d or not m or (m, st, d) in seen:
                continue
            seen.add((m, st, d))
            lst = out.setdefault(m, {}).setdefault(st, [])
            if len(lst) < limit_per_stage:
                lst.append(d)
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
        # 팀을 떠난(옮긴) 검수자의 과거 기여도 이름으로 표시: 팀 밖 id 는 전역 조회로 보강
        missing = {r["reviewer_id"] for r in rows} - set(names)
        if missing:
            names.update({k: v for k, v in self.reviewers_map(None).items() if k in missing})
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
            total_targets = len([r for r in self._get(
                "contents", "select=hash,model&team_id=eq." + urllib.parse.quote(team))
                if (r.get("model") or "")])           # 미실행(추가만) 콘텐츠는 진척 분모에서 제외
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
        # 보너스(적립·초기화 오프셋)만 있는 검수자도 포함: 피드백 전체 삭제 후에도 보존 점수가 보이게
        ids = set(board) | {k for k, b in bonuses.items() if k and (b or {}).get("total")}
        for rid in ids:
            v = board.get(rid) or {"reviews": 0, "corrections": 0,
                                   "wk_reviews": 0, "wk_corr": 0, "pv_reviews": 0, "pv_corr": 0}
            gs = gold.get(rid) or {"n": 0, "acc": 0.0}
            mult = _mult(rid)
            base = (v["reviews"] * 10 + v["corrections"] * 25 + patches.get(rid, 0) * 5
                    + cons_match.get(rid, 0) * 5 + gs["n"] * 10)
            # 초기화 오프셋(음수 이벤트)로 합이 음수가 될 수 있어 0 하한(레벨·리그 표시 정합)
            pts = max(0, round(base * mult) + (bonuses.get(rid) or {}).get("total", 0))
            wk_base = v["wk_reviews"] * 10 + v["wk_corr"] * 25
            pv_base = v["pv_reviews"] * 10 + v["pv_corr"] * 25
            meta = names.get(rid, {})
            leaderboard.append({"reviewer": meta.get("name", rid), "reviewer_id": rid, "reviews": v["reviews"],
                                "corrections": v["corrections"], "points": pts,
                                "level": level_of(pts), "streak": _streak(days_by.get(rid, set())),
                                "char": meta.get("avatar", "boksil"), "progress": _prog(v["reviews"]),
                                "week_points": max(0, round(wk_base * mult) + (bonuses.get(rid) or {}).get("week", 0)),
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
    def sync_contents(self, pairs, source: str = "단건", team=None, include_all: bool = False):
        """검토 대상(review=='yellow')만 prism.contents 로 upsert(파이어호스 제외).
        include_all=True 는 재실행처럼 기존 행 갱신이 목적일 때: 비-YELLOW 결과도
        upsert 해 모델·버전·review 상태가 최신 실행을 따라가게 한다."""
        from .store import content_hash
        rows = []
        for content, out in pairs:
            qm = out.get("quality_meta", {}) or {}
            if not include_all and (qm.get("review") or "") != "yellow":
                continue                              # 검토 대상만
            row = {"hash": content_hash(content), "service": content.get("displayServiceName", ""),
                   "title": content.get("title", ""), "subtitle": content.get("subtitle", ""),
                   "body": content.get("body", ""),
                   "source_url": content.get("source_url", "") or content.get("url", ""),
                   "source": source, "final_grade": qm.get("finalGrade", ""),
                   "item_meta": out.get("item_meta"), "quality_meta": qm,
                   "model": (out.get("trace") or {}).get("model", "") or "",
                   "version": int((out.get("trace") or {}).get("version") or 1),
                   "review": qm.get("review", "")}
            if team:
                row["team_id"] = team
            rows.append(row)
        # 같은 배치 내 동일 hash 중복(업로드 파일의 중복 행)은 마지막 것만 남긴다.
        # 중복이 섞이면 upsert 전체가 Postgres 21000(cardinality)으로 실패한다(2026-07-06 실사용 발견).
        uniq = {}
        for row in rows:
            uniq[(row["hash"], row.get("team_id") or "")] = row
        rows = list(uniq.values())
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
        rows = self._get("contents", f"select=hash,service,title,subtitle,body{tq}&limit={int(limit)}")
        return {r["hash"]: {"displayServiceName": r.get("service") or "", "title": r.get("title") or "",
                            "subtitle": r.get("subtitle") or "", "body": r.get("body") or ""} for r in rows}

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
        rows = self._get("contents", "select=hash,service,title,final_grade,item_meta,source,model,version,purpose"
                         f"{tq}&order=created_at.desc&limit={int(limit)}")
        out = []
        for r in rows:
            im = r.get("item_meta") or {}
            cat = " · ".join(im.get("content_category") or [])
            out.append({"hash": r["hash"], "service": r.get("service") or "", "title": r.get("title") or "",
                        "grade": r.get("final_grade") or "", "summary": im.get("summary", ""),
                        "category": cat, "source": r.get("source") or "단건", "model": r.get("model") or "",
                        "version": int(r.get("version") or 1), "purpose": r.get("purpose") or "review"})
        return out

    def save_report(self, kind: str, payload, team=None):
        row = {"kind": kind, "team_key": team or "", "payload": payload}
        self._upsert("reports", [row])

    def get_report(self, kind: str, team=None):
        tq = urllib.parse.quote(team or "")
        rows = self._get("reports", f"select=payload&kind=eq.{urllib.parse.quote(kind)}&team_key=eq.{tq}")
        return rows[0]["payload"] if rows else None

    def save_draft(self, content_hash, model, version, item_meta, quality_meta, team=None):
        """(콘텐츠, 모델, 버전) 초안 스냅샷 upsert · 결과 비교 팝업의 전체 이력 원천."""
        self._upsert("drafts", [{"content_hash": content_hash, "team_key": team or "",
                                 "model": model or "", "version": int(version or 1),
                                 "item_meta": item_meta or {}, "quality_meta": quality_meta or {}}])

    def draft_history(self, content_hash, team=None, limit: int = 20) -> list:
        q = (f"select=model,version,item_meta,quality_meta,created_at"
             f"&content_hash=eq.{urllib.parse.quote(content_hash)}"
             f"&team_key=eq.{urllib.parse.quote(team or '')}&order=created_at.desc&limit={int(limit)}")
        rows = self._get("drafts", q)
        return [{"model": r.get("model") or "", "version": int(r.get("version") or 1),
                 "item_meta": r.get("item_meta") or {}, "quality_meta": r.get("quality_meta") or {},
                 "ts": _epoch(r.get("created_at"))} for r in rows]

    def save_eval_check(self, content_hash, reviewer, verdict, expected="", got="", team=None) -> bool:
        """평가 불일치 건 판정 upsert(1인 1표). verdict: adopt|reject."""
        if verdict not in ("adopt", "reject") or not content_hash:
            return False
        row = {"hash": content_hash, "reviewer": reviewer or "(익명)", "verdict": verdict,
               "expected": expected or "", "got": got or ""}
        if team:
            row["team_id"] = team
        self._upsert("eval_checks", [row])
        return True

    def eval_check_counts(self, team=None) -> dict:
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        out = {}
        for r in self._get("eval_checks", "select=hash,reviewer,verdict" + tq):
            d = out.setdefault(r["hash"], {"adopt": 0, "reject": 0, "reviewers": {}})
            v = r.get("verdict") or ""
            if v in ("adopt", "reject"):
                d[v] += 1
                d["reviewers"][r.get("reviewer") or "(익명)"] = v
        return out

    def set_purpose(self, hashes, purpose, team=None) -> int:
        """콘텐츠 용도 지정: review(검수용)|eval(평가용 홀드아웃)."""
        if purpose not in ("review", "eval"):
            return 0
        hs = [h for h in (hashes or []) if h]
        if not hs:
            return 0
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        q = "hash=in.(" + ",".join(urllib.parse.quote(h) for h in hs) + ")" + tq
        self._req("PATCH", "contents", query=q, body={"purpose": purpose}, prefer="return=minimal")
        return len(hs)

    def purpose_map(self, team=None) -> dict:
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        rows = self._get("contents", "select=hash,purpose" + tq)
        return {r["hash"]: (r.get("purpose") or "review") for r in rows}

    def recent(self, limit: int = 5000, team=None) -> list:
        tq = f"&team_id=eq.{urllib.parse.quote(team)}" if team else ""
        rows = self._get("contents", "select=hash,service,title,subtitle,body,source_url,item_meta,quality_meta,model,version"
                         f"{tq}&order=created_at.desc&limit={int(limit)}")
        # subtitle 보존: 재구성 콘텐츠의 해시가 저장 해시와 일치해야 재실행 upsert·골든 매칭이
        # 같은 행을 가리킨다(과거엔 subtitle 소실로 부제 있는 콘텐츠가 유령 행을 만들었음).
        out = [{"item_meta": r.get("item_meta") or {}, "quality_meta": r.get("quality_meta") or {},
                "trace": {"model": r.get("model") or "", "version": int(r.get("version") or 1)},
                "content_ref": {"title": r.get("title", ""), "displayServiceName": r.get("service", ""),
                                "subtitle": r.get("subtitle", "") or "", "body": r.get("body", ""),
                                "source_url": r.get("source_url", ""),
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
    def save_many(self, pairs, run_id="", source="단건", team=None, include_all=False):
        return self.sync_contents(pairs, source, team=team, include_all=include_all)

    def save_dedup(self, pairs, run_id="", source="단건", team=None):
        # 관리자 인입(수동·엑셀·자동)은 등급 무관 전량 적재(include_all).
        # yellow 필터는 파이어호스 시절 잔재 · sqlite(전량 저장)와 어긋나 G/auto 콘텐츠가
        # 목록에 안 뜨는 결함이 있었다(2026-07-06). 검수 대기 구분은 review 컬럼이 담당.
        n = self.sync_contents(pairs, source, team=team, include_all=True)
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
