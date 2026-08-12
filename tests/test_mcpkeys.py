"""MCP 파트너 키(트랙 B · prism/mcpkeys.py): 발급·해석·폐기·레이트리밋·사용 기록.

이 파일의 절반은 2026-08-11 감사에서 실제로 뚫렸던 것들의 회귀 가드다. 특히:
  · O3  자기 팀 관리자가 **타 팀** 배포 키를 폐기할 수 있었다 → revoke 는 (key_id, team) 복합 필터
  · H1  저장 계층이 team falsy 를 '전 팀'으로 읽어 팀 미소속 계정이 타 팀 콘텐츠를 덮어썼다
        → team 없는 키는 발급 자체를 막는다
  · O2  인증 실패마다 상태를 재기록해 실사용 감사 기록이 밀려났다 → 인증 실패는 기록하지 않는다
  · H4  키 경로에 상한이 없었다 → 키당 분당 + 일일 상한

store(SQLite)와 supastore(Supabase) 두 구현이 **같은 계약**을 만족하는지도 여기서 단언한다.
supastore 는 네트워크 없이 in-memory PostgREST 대역(_FakeRest)으로 돌린다 — 운영 DB 는 건드리지 않는다.

실행: python3 -m pytest tests/test_mcpkeys.py -q
"""
import os
import re
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEAM_A = "team-a"
TEAM_B = "team-b"
USER_1 = "user-1"
USER_2 = "user-2"


class _FakeServe:
    """mcpkeys 가 참조하는 serve 표면만 흉내낸다(get_store · _supa · is_admin_user)."""

    def __init__(self, store, supa=True, admins=()):
        self._store = store
        self._supa_on = supa
        self._admins = set(admins)

    def get_store(self):
        return self._store

    def _supa(self):
        return ("https://example.test", "key") if self._supa_on else None

    def is_admin_user(self, uid, team, email=""):
        return (uid, team) in self._admins


# ── supabase 대역: PostgREST 질의를 메모리 표에 대고 흉내낸다(네트워크·운영 DB 무접촉) ──
class _FakeRest:
    """SupabaseStore._req/_get/_count 를 가로채는 최소 PostgREST.

    지원하는 것만 지원한다: eq/is 필터 · order · select · POST/PATCH/DELETE.
    mcpkeys 가 만드는 질의가 이 범위를 벗어나면 테스트가 KeyError 로 터지게 두는 편이,
    조용히 '전체 매칭'으로 넘어가 팀 격리 검증을 무력화하는 것보다 낫다.
    """

    def __init__(self):
        self.tables = {}
        self.seq = 0

    # -- 질의 파싱 --
    def _filters(self, query):
        out = []
        for part in (query or "").split("&"):
            if not part or "=" not in part:
                continue
            col, val = part.split("=", 1)
            if col in ("select", "order", "limit", "offset"):
                continue
            import urllib.parse
            if val.startswith("eq."):
                out.append((col, "eq", urllib.parse.unquote(val[3:])))
            elif val.startswith("gte."):
                out.append((col, "gte", urllib.parse.unquote(val[4:])))
            elif val.startswith("is."):
                out.append((col, "is", val[3:]))
            else:
                raise AssertionError("대역이 모르는 필터: %s=%s" % (col, val))
        return out

    def _match(self, row, filters):
        for col, op, val in filters:
            cur = row.get(col)
            if op == "eq":
                if str(cur) != val:
                    return False
            elif op == "gte":
                if str(cur or "") < val:
                    return False
            elif op == "is":
                want = {"true": True, "false": False, "null": None}[val]
                if want is None:
                    if cur is not None:
                        return False
                elif bool(cur) is not want:
                    return False
        return True

    def rows(self, table):
        return self.tables.setdefault(table, [])

    def req(self, method, table, *, query="", body=None, prefer=""):
        rows = self.rows(table)
        if method == "POST":
            made = []
            for r in (body or []):
                r = dict(r)
                self.seq += 1
                r.setdefault("id", self.seq)
                r.setdefault("created_at", _now_iso())
                r.setdefault("revoked", False)
                # UNIQUE(key_hash) 재현: 같은 해시 두 번 넣으면 DB 가 거절한다
                if table == "mcp_keys" and any(x.get("key_hash") == r.get("key_hash") for x in rows):
                    raise RuntimeError("supabase POST mcp_keys 실패(HTTP409)")
                if table == "mcp_keys" and not (r.get("team_id") and r.get("user_id")):
                    raise RuntimeError("supabase POST mcp_keys 실패(HTTP400 not-null)")
                rows.append(r)
                made.append(r)
            return made if "representation" in prefer else []
        f = self._filters(query)
        if method == "PATCH":
            hit = [r for r in rows if self._match(r, f)]
            for r in hit:
                r.update(body or {})
            return hit if "representation" in prefer else []
        if method == "DELETE":
            keep = [r for r in rows if not self._match(r, f)]
            self.tables[table] = keep
            return []
        return [dict(r) for r in rows if self._match(r, f)]

    def get(self, table, query=""):
        out = self.req("GET", table, query=query)
        m = re.search(r"(?:^|&)order=([a-z_]+)\.(asc|desc)", query or "")
        if m:
            out.sort(key=lambda r: str(r.get(m.group(1)) or ""), reverse=(m.group(2) == "desc"))
        return out

    def count(self, table, query=""):
        return len(self.req("GET", table, query=query))


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def _sqlite_store():
    from prism.store import Store
    return Store(os.path.join(tempfile.mkdtemp(), "mcpkeys.db"))


def _supa_store():
    from prism.supastore import SupabaseStore
    st = SupabaseStore.__new__(SupabaseStore)          # __init__ 우회(환경변수·네트워크 없음)
    rest = _FakeRest()
    st._req = rest.req
    st._get = rest.get
    st._count = rest.count
    st._rest = rest
    return st


# ══ 두 구현이 함께 통과해야 하는 계약 ═══════════════════════════════════════
class McpKeysContractMixin:
    """self.st(스토어) 를 서브클래스가 제공. mcpkeys 는 _SV 로 가짜 serve 를 받는다."""

    def setUp(self):
        from prism import mcpkeys
        self.mk = mcpkeys
        self.st = self.make_store()
        self.serve = _FakeServe(self.st, supa=True, admins={(USER_1, TEAM_A)})
        self._prev_sv = mcpkeys._SV
        mcpkeys._SV = self.serve
        mcpkeys._reset_state()
        self.addCleanup(lambda: setattr(mcpkeys, "_SV", self._prev_sv))
        self.addCleanup(mcpkeys._reset_state)

    # ── 발급 ──
    def test_issue_returns_plaintext_once_and_binds_owner(self):
        r = self.mk.issue(USER_1, TEAM_A, days=30, label="검색팀")
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(r["key"].startswith("pmk_"))
        self.assertTrue(r["prefix"].startswith("pmk_"))
        self.assertLess(len(r["prefix"]), len(r["key"]), "접두는 평문보다 짧아야 한다")
        self.assertGreater(r["expires_at"], time.time())
        # 목록에는 평문도 해시도 없다 · 접두만
        items = self.mk.list_keys(USER_1, TEAM_A)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["prefix"], r["prefix"])
        self.assertEqual(items[0]["label"], "검색팀")
        self.assertNotIn("hash", items[0])
        self.assertNotIn("key", items[0])

    def test_issue_without_team_is_refused(self):
        """감사 H1: team falsy 는 저장 계층에서 '전 팀'으로 읽힌다 → 발급 자체를 막는다."""
        for bad in (None, "", "   ", 0, False):
            r = self.mk.issue(USER_1, bad)
            self.assertFalse(r.get("ok"), "팀 없이 발급됐다: %r" % (bad,))
            self.assertIn("팀", r.get("error", ""))
        self.assertEqual(self.mk.list_keys(USER_1, TEAM_A), [])

    def test_issue_without_user_is_refused(self):
        self.assertFalse(self.mk.issue("", TEAM_A).get("ok"))

    def test_days_are_clamped(self):
        now = time.time()
        far = self.mk.issue(USER_1, TEAM_A, days=99999)
        self.assertLessEqual(far["expires_at"] - now, self.mk.MAX_DAYS * 86400 + 5)
        near = self.mk.issue(USER_1, TEAM_A, days=-3)
        self.assertGreater(near["expires_at"], now)
        bad = self.mk.issue(USER_1, TEAM_A, days="이상한값")       # 예외 원문이 새지 않는다(감사 H3)
        self.assertTrue(bad.get("ok"))
        self.assertLessEqual(bad["expires_at"] - now, self.mk.DEFAULT_DAYS * 86400 + 5)

    def test_per_user_cap(self):
        for i in range(self.mk.MAX_KEYS_PER_USER):
            self.assertTrue(self.mk.issue(USER_1, TEAM_A, label="k%d" % i).get("ok"))
        over = self.mk.issue(USER_1, TEAM_A)
        self.assertFalse(over.get("ok"))
        self.assertIn(str(self.mk.MAX_KEYS_PER_USER), over.get("error", ""))
        # 상한은 사용자별 · 다른 사람은 영향 없다
        self.assertTrue(self.mk.issue(USER_2, TEAM_A).get("ok"))
        # 폐기하면 자리가 난다(안 그러면 5회 교체 후 영구 잠김)
        first = self.mk.list_keys(USER_1, TEAM_A)[0]
        self.assertTrue(self.mk.revoke(first["key_id"], TEAM_A))
        self.assertTrue(self.mk.issue(USER_1, TEAM_A).get("ok"))

    # ── 해석(resolve) ──
    def test_resolve_roundtrip(self):
        r = self.mk.issue(USER_1, TEAM_A)
        got = self.mk.resolve(r["key"])
        self.assertEqual(got["user_id"], USER_1)
        self.assertEqual(got["team"], TEAM_A)
        self.assertEqual(got["key_id"], r["key_id"])
        self.assertTrue(got["is_admin"], "발급자가 관리자면 키도 관리자")
        self.assertEqual(self.mk.resolve("Bearer " + r["key"]), got, "Bearer 접두도 받는다")

    def test_key_power_does_not_exceed_issuer(self):
        """비관리자가 발급한 키는 관리자 도구를 못 본다(키는 발급자 권한을 넘지 않는다)."""
        r = self.mk.issue(USER_2, TEAM_A)
        self.assertFalse(self.mk.resolve(r["key"])["is_admin"])

    def test_admin_flag_follows_current_role_not_snapshot(self):
        """발급 후 강등되면 그 키의 관리자 권한도 즉시 사라진다(스냅샷 아님)."""
        r = self.mk.issue(USER_1, TEAM_A)
        self.assertTrue(self.mk.resolve(r["key"])["is_admin"])
        self.serve._admins.clear()
        self.assertFalse(self.mk.resolve(r["key"])["is_admin"])

    def test_resolve_rejects_junk_and_wrong_keys(self):
        self.mk.issue(USER_1, TEAM_A)
        for bad in ("", None, "  ", "pr_live_abcdefgh", "pmk_", "pmk_짧음",
                    "pmk_" + "z" * 40, "Bearer nonsense"):
            self.assertIsNone(self.mk.resolve(bad), "받아들이면 안 되는 키: %r" % (bad,))

    def test_revoked_key_stops_resolving(self):
        r = self.mk.issue(USER_1, TEAM_A)
        self.assertTrue(self.mk.revoke(r["key_id"], TEAM_A))
        self.assertIsNone(self.mk.resolve(r["key"]))

    def test_expired_key_stops_resolving(self):
        r = self.mk.issue(USER_1, TEAM_A, days=1)
        self._expire(r["key_id"])
        self.assertIsNone(self.mk.resolve(r["key"]))
        # 만료 키는 유효 상한에서도 빠진다
        self.assertEqual([k for k in self.mk.list_keys(USER_1, TEAM_A) if not k["expired"]], [])

    # ── 폐기(감사 O3 회귀 · 이 파일에서 가장 중요한 단언) ──
    def test_revoke_from_other_team_fails(self):
        """자기 팀 관리자가 **타 팀** 키를 폐기할 수 있으면 안 된다(감사 O3).
        배포 키(pr_live_)가 팀 스코프 없이 만들어져 실제로 이게 가능했다."""
        mine = self.mk.issue(USER_1, TEAM_A)
        theirs = self.mk.issue(USER_2, TEAM_B)
        self.assertFalse(self.mk.revoke(theirs["key_id"], TEAM_A), "타 팀 키가 폐기됐다")
        self.assertIsNotNone(self.mk.resolve(theirs["key"]), "타 팀 키가 무효화됐다")
        # 자기 팀 키는 정상 폐기
        self.assertTrue(self.mk.revoke(mine["key_id"], TEAM_A))
        # 반대 방향도 같다
        self.assertFalse(self.mk.revoke(mine["key_id"], TEAM_B))

    def test_revoke_without_team_fails(self):
        """team 을 안 주면(=falsy) 절대 지우지 않는다 — 그게 '전 팀'으로 읽히는 자리다."""
        r = self.mk.issue(USER_1, TEAM_A)
        for bad in (None, "", "   "):
            self.assertFalse(self.mk.revoke(r["key_id"], bad))
        self.assertIsNotNone(self.mk.resolve(r["key"]))

    def test_revoke_unknown_key_is_false(self):
        self.assertFalse(self.mk.revoke("없는키", TEAM_A))
        self.assertFalse(self.mk.revoke("", TEAM_A))

    def test_store_revoke_needs_both_filters(self):
        """스토어 계층도 같은 계약: (key_id, team) 둘 다 맞아야 폐기된다."""
        r = self.mk.issue(USER_1, TEAM_A)
        self.assertFalse(self.st.mcp_key_revoke(r["key_id"], TEAM_B))
        self.assertFalse(self.st.mcp_key_revoke(r["key_id"], None))
        self.assertTrue(self.st.mcp_key_revoke(r["key_id"], TEAM_A))

    # ── 목록 격리 ──
    def test_list_is_scoped_to_owner_and_team(self):
        self.mk.issue(USER_1, TEAM_A, label="A1")
        self.mk.issue(USER_2, TEAM_A, label="A2")
        self.mk.issue(USER_1, TEAM_B, label="B1")
        self.assertEqual([k["label"] for k in self.mk.list_keys(USER_1, TEAM_A)], ["A1"])
        self.assertEqual([k["label"] for k in self.mk.list_keys(USER_2, TEAM_A)], ["A2"])
        self.assertEqual([k["label"] for k in self.mk.list_keys(USER_1, TEAM_B)], ["B1"])
        for bad in (None, ""):                          # 팀·사용자 미상 = 빈 목록(전체 폴백 없음)
            self.assertEqual(self.mk.list_keys(USER_1, bad), [])
            self.assertEqual(self.mk.list_keys(bad, TEAM_A), [])
        self.assertEqual(self.st.mcp_keys_for(USER_1, None), [])
        self.assertEqual(self.st.mcp_keys_for(None, TEAM_A), [])

    # ── 평문 미보관 ──
    def test_plaintext_never_lands_in_storage(self):
        """평문이 스토어 어디에도 남지 않는다(해시 대조만) · 읽기 계약에 해시도 없다."""
        r = self.mk.issue(USER_1, TEAM_A, label="누출검사")
        self.mk.log_call(r["key_id"], "search_contents", True, 12, 345)
        blob = self.dump_storage()
        self.assertNotIn(r["key"], blob, "평문 키가 저장소에 남았다")
        self.assertNotIn(r["key"][len("pmk_"):], blob, "평문 비밀부가 저장소에 남았다")
        import hashlib
        h = hashlib.sha256(r["key"].encode()).hexdigest()
        self.assertIn(h, blob, "대조용 해시는 있어야 한다(그것만 있어야 한다)")
        # 어떤 읽기 경로도 해시를 내보내지 않는다
        for row in (self.st.mcp_key_find(key_id=r["key_id"]),
                    self.st.mcp_keys_for(USER_1, TEAM_A)[0]):
            self.assertNotIn("key_hash", row)
            self.assertNotIn("hash", row)

    # ── 레이트리밋(감사 H4) ──
    def test_per_minute_cap(self):
        r = self.mk.issue(USER_1, TEAM_A)
        kid = r["key_id"]
        for i in range(self.mk.PER_MIN):
            allowed, wait = self.mk.rate_check(kid)
            self.assertTrue(allowed, "%d 번째에서 이미 막혔다" % i)
            self.assertEqual(wait, 0)
        allowed, wait = self.mk.rate_check(kid)
        self.assertFalse(allowed, "분당 상한을 넘겼는데 통과했다")
        self.assertGreater(wait, 0)
        self.assertLessEqual(wait, 61)
        # 상한은 키별 · 다른 키는 멀쩡하다
        other = self.mk.issue(USER_2, TEAM_A)
        self.assertEqual(self.mk.rate_check(other["key_id"]), (True, 0))

    def test_daily_cap(self):
        r = self.mk.issue(USER_1, TEAM_A)
        kid = r["key_id"]
        prev = self.mk.PER_DAY
        self.mk.PER_DAY = 3
        try:
            self.mk._reset_state()
            self.assertTrue(self.mk.rate_check(kid)[0])
            self.assertTrue(self.mk.rate_check(kid)[0])
            self.assertTrue(self.mk.rate_check(kid)[0])
            allowed, wait = self.mk.rate_check(kid)
            self.assertFalse(allowed, "일일 상한을 넘겼는데 통과했다")
            self.assertGreater(wait, 0)
        finally:
            self.mk.PER_DAY = prev

    def test_daily_cap_survives_restart(self):
        """재배포(프로세스 재시작)가 일일 상한을 통째로 리셋하면 상한이 무의미해진다 —
        그날 첫 호출에서 사용 기록으로 기준선을 다시 잡는다."""
        r = self.mk.issue(USER_1, TEAM_A)
        kid = r["key_id"]
        prev = self.mk.PER_DAY
        self.mk.PER_DAY = 3
        try:
            for _ in range(3):
                self.mk.rate_check(kid)
                self.mk.log_call(kid, "t", True, 1, 1)
            self.mk._reset_state()                     # = 프로세스 재시작
            self.assertFalse(self.mk.rate_check(kid)[0], "재시작이 일일 상한을 리셋했다")
        finally:
            self.mk.PER_DAY = prev

    def test_rate_check_rejects_empty_key(self):
        self.assertEqual(self.mk.rate_check("")[0], False)
        self.assertEqual(self.mk.rate_check(None)[0], False)

    # ── 사용 기록(감사 O2) ──
    def test_log_call_records_full_row(self):
        r = self.mk.issue(USER_1, TEAM_A)
        self.assertIsNotNone(self.mk.resolve(r["key"]))
        self.mk.log_call(r["key_id"], "get_content", True, 42, 1024)
        row = self.calls()[0]
        self.assertEqual(row["key_id"], r["key_id"])
        self.assertEqual(row["user_id"], USER_1)
        self.assertEqual(row["team"], TEAM_A)
        self.assertEqual(row["prefix"], r["prefix"])
        self.assertEqual(row["tool"], "get_content")
        self.assertEqual((row["ok"], row["ms"], row["resp_bytes"]), (True, 42, 1024))

    def test_ok_and_fail_are_separate_buckets(self):
        r = self.mk.issue(USER_1, TEAM_A)
        self.mk.resolve(r["key"])
        self.mk.log_call(r["key_id"], "a", True, 1, 10)
        self.mk.log_call(r["key_id"], "b", False, 2, 20)
        self.mk.log_call(r["key_id"], "c", True, 3, 30)
        since = 0
        self.assertEqual(self.st.mcp_call_count(r["key_id"], since, ok=True), 2)
        self.assertEqual(self.st.mcp_call_count(r["key_id"], since, ok=False), 1)
        self.assertEqual(self.st.mcp_call_count(r["key_id"], since), 3)
        u = self.mk.usage(r["key_id"], TEAM_A)
        self.assertEqual((u["ok"], u["fail"]), (2, 1))
        self.assertEqual(self.mk.usage(r["key_id"], TEAM_B), {"ok": 0, "fail": 0},
                         "타 팀이 남의 키 사용량을 본다")

    def test_auth_failures_are_never_logged(self):
        """감사 O2: 틀린 키를 아무리 두드려도 사용 기록은 1건도 늘지 않는다.
        (스펙트럼 관문은 실패마다 상태를 전량 재기록해 실사용 기록 500건을 밀어냈다.)"""
        good = self.mk.issue(USER_1, TEAM_A)
        self.mk.resolve(good["key"])
        self.mk.log_call(good["key_id"], "real", True, 5, 50)
        base = len(self.calls())
        for i in range(200):
            self.assertIsNone(self.mk.resolve("pmk_" + "wrong%030d" % i))
        revoked = self.mk.issue(USER_2, TEAM_A)
        self.mk.revoke(revoked["key_id"], TEAM_A)
        self.assertIsNone(self.mk.resolve(revoked["key"]))
        expired = self.mk.issue(USER_2, TEAM_A, days=1)
        self._expire(expired["key_id"])
        self.assertIsNone(self.mk.resolve(expired["key"]))
        self.assertEqual(len(self.calls()), base, "인증 실패가 사용 기록에 적재됐다")

    def test_log_call_ignores_unknown_key(self):
        """정체를 모르는 key_id 는 기록하지 않는다(추측한 사용자·팀으로 감사 기록을 오염시키지 않는다)."""
        self.mk.log_call("존재하지-않는-키", "tool", True, 1, 1)
        self.assertEqual(self.calls(), [])

    def test_log_call_clamps_bad_numbers(self):
        r = self.mk.issue(USER_1, TEAM_A)
        self.mk.resolve(r["key"])
        self.mk.log_call(r["key_id"], "t", True, "이상한값", None)
        row = self.calls()[0]
        self.assertEqual((row["ms"], row["resp_bytes"]), (0, 0))

    # ── helpers(구현별) ──
    def _expire(self, key_id):
        raise NotImplementedError

    def calls(self) -> list:
        raise NotImplementedError

    def dump_storage(self) -> str:
        raise NotImplementedError


class TestSqliteMcpKeys(McpKeysContractMixin, unittest.TestCase):
    def make_store(self):
        return _sqlite_store()

    def _expire(self, key_id):
        c = self.st._conn()
        c.execute("UPDATE mcp_keys SET expires_at=? WHERE key_id=?", (time.time() - 60, key_id))
        c.commit()

    def calls(self):
        c = self.st._conn()
        return [{"key_id": r[0], "team": r[1], "user_id": r[2], "prefix": r[3], "tool": r[4],
                 "ok": bool(r[5]), "ms": r[6], "resp_bytes": r[7]}
                for r in c.execute("SELECT key_id,team,user_id,prefix,tool,ok,ms,resp_bytes "
                                   "FROM mcp_calls ORDER BY id")]

    def dump_storage(self):
        """DB 파일 + WAL 을 통째로 훑는다(컬럼만 보면 '어딘가 다른 칸에 남았다'를 못 잡는다)."""
        self.st._conn().commit()
        raw = b""
        for p in (self.st.path, self.st.path + "-wal", self.st.path + "-shm"):
            if os.path.exists(p):
                with open(p, "rb") as fh:
                    raw += fh.read()
        return raw.decode("utf-8", "replace")


class TestSupabaseMcpKeys(McpKeysContractMixin, unittest.TestCase):
    """운영 DB 무접촉 · PostgREST 질의를 메모리 대역에 태워 같은 시나리오를 돌린다."""

    def make_store(self):
        return _supa_store()

    def _expire(self, key_id):
        for r in self.st._rest.rows("mcp_keys"):
            if r["key_id"] == key_id:
                r["expires_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                                time.gmtime(time.time() - 60))

    def calls(self):
        return [{"key_id": r["key_id"], "team": r.get("team_id") or "",
                 "user_id": r.get("user_id") or "", "prefix": r.get("key_prefix") or "",
                 "tool": r.get("tool") or "", "ok": bool(r.get("ok")),
                 "ms": r.get("ms"), "resp_bytes": r.get("resp_bytes")}
                for r in self.st._rest.rows("mcp_calls")]

    def dump_storage(self):
        import json
        return json.dumps(self.st._rest.tables, ensure_ascii=False, default=str)

    def test_queries_carry_team_filter(self):
        """폐기·목록 질의가 실제로 team_id 필터를 달고 나가는지(대역이 아니라 질의 문자열로) 확인."""
        seen = []
        base_req, base_get = self.st._req, self.st._get
        self.st._req = lambda m, t, **kw: (seen.append((m, t, kw.get("query") or "")) or
                                           base_req(m, t, **kw))
        self.st._get = lambda t, q="": (seen.append(("GET", t, q)) or base_get(t, q))
        r = self.mk.issue(USER_1, TEAM_A)
        seen.clear()
        self.mk.list_keys(USER_1, TEAM_A)
        self.mk.revoke(r["key_id"], TEAM_A)
        for method, table, q in seen:
            if table == "mcp_keys" and method in ("GET", "PATCH"):
                self.assertIn("team_id=eq.", q, "팀 필터 없는 질의: %s %s %s" % (method, table, q))

    def test_store_refuses_teamless_row(self):
        """DB 제약(team_id NOT NULL) 이중 방어: 모듈을 우회해도 팀 없는 행은 안 들어간다."""
        with self.assertRaises(RuntimeError):
            self.st.mcp_key_add(USER_1, "", "kid", "hash", "pmk_abc…", "", time.time() + 60)


class TestSqliteStoreConstraints(unittest.TestCase):
    """sqlite 도 같은 이중 방어(팀·사용자 빈 값 거부 · 해시 유일)."""

    def setUp(self):
        self.st = _sqlite_store()

    def test_teamless_row_is_rejected(self):
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.st.mcp_key_add(USER_1, "", "kid1", "h1", "p", "", time.time() + 60)
        with self.assertRaises(sqlite3.IntegrityError):
            self.st.mcp_key_add("", TEAM_A, "kid2", "h2", "p", "", time.time() + 60)

    def test_hash_is_unique(self):
        import sqlite3
        self.st.mcp_key_add(USER_1, TEAM_A, "kid1", "same", "p", "", time.time() + 60)
        with self.assertRaises(sqlite3.IntegrityError):
            self.st.mcp_key_add(USER_2, TEAM_B, "kid2", "same", "p", "", time.time() + 60)


class TestBothStoresShareTheSameSurface(unittest.TestCase):
    """이중 구현 표류 방지: 두 스토어가 같은 이름·같은 인자로 MCP 키 계약을 갖는다."""

    METHODS = ("mcp_key_add", "mcp_key_find", "mcp_keys_for", "mcp_key_revoke",
               "mcp_key_touch", "mcp_call_add", "mcp_call_count")

    def test_signatures_match(self):
        import inspect
        from prism.store import Store
        from prism.supastore import SupabaseStore
        for name in self.METHODS:
            a = getattr(Store, name, None)
            b = getattr(SupabaseStore, name, None)
            self.assertTrue(a and b, "한쪽에만 있는 메서드: %s" % name)
            self.assertEqual(str(inspect.signature(a)), str(inspect.signature(b)),
                             "%s 시그니처가 갈렸다" % name)

    def test_row_shape_matches(self):
        """읽기 계약(키 집합)이 두 구현에서 같아야 한다 — 해시는 어느 쪽에도 없다."""
        sq, sp = _sqlite_store(), _supa_store()
        exp = time.time() + 600
        sq.mcp_key_add(USER_1, TEAM_A, "kid", "hash-x", "pmk_abcdef…", "메모", exp)
        sp.mcp_key_add(USER_1, TEAM_A, "kid", "hash-x", "pmk_abcdef…", "메모", exp)
        a, b = sq.mcp_key_find(key_id="kid"), sp.mcp_key_find(key_id="kid")
        self.assertEqual(sorted(a), sorted(b))
        self.assertNotIn("key_hash", a)
        for k in ("key_id", "team", "user_id", "prefix", "label"):
            self.assertEqual(a[k], b[k], "필드 %s 가 다르다" % k)
        self.assertEqual(a["revoked"], b["revoked"])
        self.assertAlmostEqual(a["expires_at"], b["expires_at"], delta=1.5)
        self.assertEqual(sq.mcp_key_find(key_hash="hash-x")["key_id"],
                         sp.mcp_key_find(key_hash="hash-x")["key_id"])
        self.assertIsNone(sq.mcp_key_find())
        self.assertIsNone(sp.mcp_key_find())


class TestScopeTeam(unittest.TestCase):
    """운영은 팀이 없으면 없는 채로(→ 발급 거부) · sqlite 단독만 단일 테넌트로 정규화한다."""

    def setUp(self):
        from prism import mcpkeys
        self.mk = mcpkeys
        self._prev = mcpkeys._SV
        self.addCleanup(lambda: setattr(mcpkeys, "_SV", self._prev))

    def test_supabase_mode_keeps_none(self):
        from prism import mcpkeys
        mcpkeys._SV = _FakeServe(_sqlite_store(), supa=True)
        self.assertIsNone(self.mk.scope_team(None))
        self.assertIsNone(self.mk.scope_team(""))
        self.assertEqual(self.mk.scope_team(TEAM_A), TEAM_A)

    def test_local_mode_uses_single_tenant(self):
        from prism import mcpkeys
        mcpkeys._SV = _FakeServe(_sqlite_store(), supa=False)
        self.assertEqual(self.mk.scope_team(None), self.mk.LOCAL_TEAM)
        self.assertEqual(self.mk.scope_team(TEAM_A), TEAM_A)

    def test_local_mode_key_is_admin(self):
        """로컬 단독은 게이트 자체가 열려 있으므로 키도 관리자."""
        from prism import mcpkeys
        st = _sqlite_store()
        mcpkeys._SV = _FakeServe(st, supa=False)
        mcpkeys._reset_state()
        self.addCleanup(mcpkeys._reset_state)
        r = self.mk.issue("local", self.mk.LOCAL_TEAM)
        self.assertTrue(self.mk.resolve(r["key"])["is_admin"])


class TestServeWiring(unittest.TestCase):
    """serve 가 mcpkeys 를 계약대로 물고 있는지(주입·라우트 등록·가로채기 없음)."""

    def test_module_is_injected_and_routes_registered(self):
        from prism import mcpkeys, serve
        self.assertIs(mcpkeys._SV, serve, "serve 가 _SV 를 주입하지 않았다")
        self.assertIn("/mcp-keys", serve._GET_ROUTES)
        self.assertIn("/mcp-key-new", serve._POST_ROUTES)
        self.assertIn("/mcp-key-revoke", serve._POST_ROUTES)
        # 발급·폐기는 팀 소속 필수(팀 없는 계정이 키를 만들면 감사 H1 재현)
        self.assertEqual(serve._POST_ROUTES["/mcp-key-new"][1], "team")
        self.assertEqual(serve._POST_ROUTES["/mcp-key-revoke"][1], "team")

    def test_transport_route_cannot_swallow_key_routes(self):
        """전송이 POST /mcp 를 등록해도 최장 접두 우선이라 /mcp-key-* 가 먼저 잡힌다."""
        from prism import serve
        order = sorted(list(serve._POST_ROUTES) + ["/mcp"], key=len, reverse=True)
        for path in ("/mcp-key-new", "/mcp-key-revoke"):
            first = next(p for p in order if path.startswith(p))
            self.assertEqual(first, path)

    def test_key_routes_are_not_public(self):
        from prism import serve
        self.assertNotIn("/mcp-keys", serve._TEAMLESS_OK_GET)


if __name__ == "__main__":
    unittest.main()
