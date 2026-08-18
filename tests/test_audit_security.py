"""보안·HTTP 계층 감사 수정 회귀 가드 (2026-08-11).

교차 팀 접근 차단(H1·D5·H2·O3·D6) · HTTP 견고성(P1·H3·H4·H5·H6·O2) · 성능(T7·F3).
"타 팀 데이터가 실제로 막히는가" 를 단언하는 것이 이 파일의 목적이다.

실행: python3 -m pytest tests/test_audit_security.py -q
"""
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ════════════════════════════════════════════════════════════════════════════
# 운영(supabase) 모드 흉내: 저장소는 sqlite 그대로 두고 인증·팀 판정만 가짜로 켠다.
# 토큰 2개 — tok-team(teamA 소속) · tok-noteam(인증됨 · 팀 없음 = 솔로 가입 계정)
# ════════════════════════════════════════════════════════════════════════════
_UIDS = {"tok-team": "uid-team", "tok-team-b": "uid-team-b", "tok-noteam": "uid-noteam"}
_TEAMS = {"uid-team": "teamA", "uid-team-b": "teamB"}


class SupaGateMixin:
    """서버를 스레드로 띄우고 serve 의 인증 훅만 대체."""

    @classmethod
    def _boot(cls):
        os.environ["PRISM_BACKEND"] = "sqlite"
        os.environ["PRISM_DB"] = os.path.join(tempfile.mkdtemp(), "audit_sec.db")
        from http.server import ThreadingHTTPServer
        from prism import serve as SV
        from prism import topicops as TPO
        cls.SV, cls.TPO = SV, TPO
        SV._STORE = None
        SV.Handler.server_mock = True
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), SV.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls._orig = {k: getattr(SV, k) for k in
                     ("_supa", "validate_jwt", "jwt_email", "team_of",
                      "is_sys_admin_user", "is_admin_user", "is_super_admin_user")}
        SV._supa = lambda: ("http://supabase.local", "test-key")
        SV.validate_jwt = lambda tok, strict=False: _UIDS.get((tok or "").strip())
        SV.jwt_email = lambda tok: ""
        SV.team_of = lambda uid: _TEAMS.get(uid)
        SV.is_sys_admin_user = lambda *a, **k: False
        SV.is_admin_user = lambda *a, **k: False
        SV.is_super_admin_user = lambda *a, **k: False

    @classmethod
    def _halt(cls):
        for k, v in cls._orig.items():
            setattr(cls.SV, k, v)
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.SV._STORE = None

    def setUp(self):
        self.SV._RL_HITS.clear()                 # 레이트리밋 버킷 격리(테스트 간 429 전염 방지)

    def _call(self, path, obj=None, token=""):
        data = json.dumps(obj).encode() if obj is not None else None
        hdrs = {"Content-Type": "application/json"} if data is not None else {}
        if token:
            hdrs["Authorization"] = "Bearer " + token
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     headers=hdrs, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")


# ── H1: POST 도 팀 fail-closed ──────────────────────────────────────────────
class TestPostTeamFailClosed(SupaGateMixin, unittest.TestCase):
    """[H1] PR#190 의 team fail-closed 가 GET 에만 걸려 POST 로 뚫리던 경로.
    팀 미소속 인증계정이 POST 로 전 팀 집계를 읽고 타 팀 콘텐츠를 덮어쓸 수 있었다."""

    # (경로, 본문) · 전부 팀 콘텐츠를 읽거나 쓰는 라우트
    CASES = [
        ("/feedback", {"hash": "de9e27b44ac1e99c", "verdict": "good", "stage": "analyze"}),
        ("/patch-meta", {"hash": "de9e27b44ac1e99c", "patch": {"content_category": ["침입"]}}),
        ("/eval-judge", {"hash": "de9e27b44ac1e99c", "verdict": "adopt"}),
        ("/board", {"action": "list"}),
        ("/entdict", {"action": "detail", "id": "E-1"}),
        ("/source-status", {"hash": "de9e27b44ac1e99c", "on": True}),
        # 미리보기는 results_rows 를 훑는다 — 팀 없는 계정이면 전 팀 콘텐츠가 표본이 된다
        ("/topic-studio", {"action": "preview", "def": {"name": "t", "cats": ["Sports"]}}),
    ]

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._halt()

    def test_teamless_account_blocked(self):
        for path, body in self.CASES:
            code, out = self._call(path, body, token="tok-noteam")
            self.assertEqual(code, 403, f"{path} → {code} {out[:120]}")
            self.assertIn("팀", json.loads(out).get("error", ""), path)

    def test_anonymous_blocked(self):
        for path, body in self.CASES:
            code, _ = self._call(path, body)
            self.assertIn(code, (401, 403), path)

    def test_team_member_still_allowed(self):
        for path, body in self.CASES:
            code, out = self._call(path, body, token="tok-team")
            self.assertNotEqual(code, 403, f"{path} → {out[:120]}")
            self.assertLess(code, 500, f"{path} → {out[:120]}")

    def test_reviewer_signup_stays_open_for_teamless(self):
        """가입(/reviewer)은 팀이 없는 상태로 들어오는 경로라 팀 게이트를 걸면 안 된다.
        자기 reviewer 행만 건드리므로 교차 팀 벡터도 아니다."""
        code, _ = self._call("/reviewer", {"mode": "login", "reviewer": "uid-noteam"},
                             token="tok-noteam")
        self.assertEqual(code, 200)


# ── D5: 교정 저장이 팀 스코프를 스토어까지 전달 ──────────────────────────────
class _RecStore:
    """호출 인자를 기록하는 스토어(팀 스코프 전달 검증용)."""

    REMOTE = False

    def __init__(self, team_of_hash="teamA"):
        self.calls = []
        self._owner = team_of_hash

    def _match(self, team):
        return team is None or team == self._owner

    def get_item_meta(self, content_hash, team=None):
        self.calls.append(("get_item_meta", content_hash, team))
        return {"content_category": ["원래값"]} if self._match(team) else None

    def update_item_meta(self, content_hash, patch, team=None):
        self.calls.append(("update_item_meta", content_hash, team))
        return self._match(team)

    def update_quality(self, content_hash, grade, reasons=None, team=None):
        self.calls.append(("update_quality", content_hash, team))
        return "R" if self._match(team) else None

    def log_patch(self, *a, **k):
        self.calls.append(("log_patch", k.get("team")))

    def get_reap(self, content_hash, team=None):
        self.calls.append(("get_reap", content_hash, team))
        return []

    def feedback_map(self, team=None):
        return {}

    def get_golden(self, team=None):
        return []


class TestPatchMetaTeamScope(unittest.TestCase):
    """[D5] patch_content_meta 가 team 을 읽기·쓰기 스토어 호출에 전달한다."""

    def setUp(self):
        from prism import serve as SV
        self.SV = SV
        self._orig = SV._STORE
        self.st = _RecStore()
        SV._STORE = self.st
        self.addCleanup(lambda: setattr(SV, "_STORE", self._orig))

    def test_team_reaches_store(self):
        out = self.SV.patch_content_meta("h1", {"content_category": ["x"], "finalGrade": "G"},
                                         "teamA", reviewer="uid-1")
        self.assertTrue(out["ok"])
        for name in ("get_item_meta", "update_item_meta", "update_quality"):
            hit = [c for c in self.st.calls if c[0] == name]
            self.assertTrue(hit, name)
            self.assertEqual(hit[0][-1], "teamA", name)

    def test_other_team_patch_is_noop(self):
        """타 팀 해시를 알아도 team 이 다르면 스토어가 행을 못 찾아 쓰기가 성립하지 않는다."""
        out = self.SV.patch_content_meta("h1", {"content_category": ["침입"]},
                                         "teamB", reviewer="uid-2")
        self.assertFalse(out["ok"])


class TestTeamIdsContract(unittest.TestCase):
    """[H2-2] 팀 단위 배치의 순회 원천 · 두 백엔드가 같은 계약을 지켜야 분기 없이 돈다."""

    def test_sqlite_returns_single_bucket(self):
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "teams.db"))
        self.assertEqual(st.team_ids(), [None])       # 로컬 = 무팀 버킷 하나

    def test_supastore_lists_teams_in_one_roundtrip(self):
        from prism import supastore
        st = supastore.SupabaseStore.__new__(supastore.SupabaseStore)
        seen = []
        st._get = lambda table, query="": (seen.append((table, query))
                                           or [{"id": "teamA"}, {"id": "teamB"}, {"id": None}])
        self.assertEqual(st.team_ids(50), ["teamA", "teamB"])   # 빈 id 는 버린다
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], "teams")
        self.assertIn("select=id", seen[0][1])
        self.assertIn("limit=50", seen[0][1])

    def test_snapshot_teams_caps_and_warns(self):
        from prism import serve as SV, topicops as TPO
        cap = TPO._TOPIC_SNAP_TEAM_CAP

        class _ManyTeams:
            def team_ids(_s, limit=200):
                return [f"t{i}" for i in range(cap + 5)][:limit]

        orig = SV._STORE
        SV._STORE = _ManyTeams()
        self.addCleanup(lambda: setattr(SV, "_STORE", orig))
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            teams = TPO.snapshot_teams()
        self.assertEqual(len(teams), cap)
        self.assertIn("상한", buf.getvalue())         # 조용히 자르지 않는다

    def test_snapshot_teams_survives_store_failure(self):
        from prism import serve as SV, topicops as TPO

        class _Broken:
            def team_ids(_s, limit=200):
                raise RuntimeError("teams 조회 실패")

        orig = SV._STORE
        SV._STORE = _Broken()
        self.addCleanup(lambda: setattr(SV, "_STORE", orig))
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(TPO.snapshot_teams(), [])
        self.assertIn("팀 목록 조회 실패", buf.getvalue())


class TestSupastoreTeamFilters(unittest.TestCase):
    """[D5·D6] supastore 쿼리에 team_id=eq. 가 실제로 붙는지(교차 팀 PATCH 차단의 실체)."""

    def _store(self):
        from prism import supastore
        st = supastore.SupabaseStore.__new__(supastore.SupabaseStore)
        st.q = []
        st._get = lambda table, query="": (st.q.append(("GET", table, query))
                                           or [{"item_meta": {}, "final_grade": "R",
                                                "quality_meta": {}, "reviewer_id": "u1"}])
        st._req = lambda m, table, query="", body=None, prefer="": (
            st.q.append((m, table, query)) or [])
        return st

    def test_item_meta_and_quality_are_team_filtered(self):
        st = self._store()
        st.get_item_meta("h1", team="teamA")
        st.update_item_meta("h1", {"a": 1}, team="teamA")
        st.update_quality("h1", "G", None, team="teamA")
        st.set_source_status("h1", "gone", "u1", team="teamA")
        st.set_ops_hold("h1", True, team="teamA")
        self.assertTrue(st.q)
        for m, _t, q in st.q:
            self.assertIn("hash=eq.h1", q)
            self.assertIn("team_id=eq.teamA", q, f"{m} {q}")

    def test_falsy_team_keeps_legacy_query(self):
        """team 이 없는 호출(로컬·팀 개념 없음)은 기존 동작 유지 — 없는 팀으로 좁혀 0건이 되면 안 된다."""
        st = self._store()
        st.update_item_meta("h1", {"a": 1})
        self.assertTrue(all("team_id" not in q for _m, _t, q in st.q))

    def test_get_reap_and_save_reap_team_filtered(self):
        st = self._store()
        st.reviewers_map = lambda team=None: st.q.append(("MAP", "reviewers", str(team))) or {}
        st.get_reap("h1", team="teamA")
        st.save_reap("h1", "u1", {"plan": "p"}, team="teamA")
        gets = [q for m, t, q in st.q if t == "feedback"]
        self.assertTrue(gets)
        for q in gets:
            self.assertIn("team_id=eq.teamA", q)
        self.assertIn(("MAP", "reviewers", "teamA"), st.q)   # 표시명 매핑도 팀 스코프


class TestKnowhowReapTeamScope(unittest.TestCase):
    """[D6] 핸드오프 번들(knowhow·rationale)이 get_reap 에 team 을 넘긴다."""

    def test_learn_export_passes_team(self):
        from prism import serve as SV
        st = _RecStore()
        st.contents_by_hash = lambda team=None: {}
        st.feedback_map = lambda team=None: {
            "h1": {"verdicts": [{"reviewer": "u1", "verdict": "bad", "stage": "analyze",
                                 "note": "사유"}]}}
        orig = SV._STORE
        SV._STORE = st
        self.addCleanup(lambda: setattr(SV, "_STORE", orig))
        SV.learn_export("rationale", "teamA")
        hit = [c for c in st.calls if c[0] == "get_reap"]
        self.assertTrue(hit)
        self.assertEqual(hit[0][-1], "teamA")


# ── H2: /topics 팀 스코프 ────────────────────────────────────────────────────
class TestTopicsTeamScope(SupaGateMixin, unittest.TestCase):
    """[H2] GET /topics 만 팀을 넘기지 않아 응답 titles 에 전 팀 제목이 실리던 경로."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._halt()

    def test_route_passes_request_team(self):
        SV = self.SV
        seen = []
        orig = SV.topics_data
        SV.topics_data = lambda team=None: seen.append(team) or {"single": [], "custom": []}
        self.addCleanup(lambda: setattr(SV, "topics_data", orig))
        code, _ = self._call("/topics", token="tok-team")
        self.assertEqual(code, 200)
        self.assertEqual(seen, ["teamA"])

    def test_compute_scopes_rows_and_cache_key(self):
        """행 조회가 team 을 타고, 캐시 키도 팀별로 갈린다(팀 A 결과가 팀 B 에 재사용되면 안 된다)."""
        SV = self.SV
        seen = []
        orig = SV.results_rows
        SV.results_rows = lambda limit=5000, team=None: seen.append(team) or []
        self.addCleanup(lambda: setattr(SV, "results_rows", orig))
        SV._agg_bump()
        SV.topics_data("teamA")
        SV.topics_data("teamB")
        SV.topics_data("teamA")                       # 캐시 적중 → 재조회 없음
        self.assertEqual(seen, ["teamA", "teamB"])

    def test_snapshot_badge_is_per_team(self):
        """[H2-2] 스냅샷 적재도 팀 버킷 — 읽기만 팀 스코프면 배지가 조용히 비고,
        전역 버킷으로 폴백하면 last_delta 의 토픽 라벨(전 팀 콘텐츠 파생)이 다시 샌다."""
        SV, TPO = self.SV, self.TPO
        secret = {"single": [{"cluster_id": "S-비밀", "name": "팀A비밀토픽",
                              "type": "single", "count": 3}],
                  "composite": [], "custom": []}
        empty = {"single": [], "composite": [], "custom": []}
        orig = SV.topics_data
        SV.topics_data = lambda team=None: (secret if team == "teamA" else empty)
        self.addCleanup(lambda: setattr(SV, "topics_data", orig))

        delta = TPO.topic_snapshot("teamA")
        self.assertEqual(delta["changed_n"], 1)
        code, body = self._call("/topics", token="tok-team")           # 팀 A = 자기 배지
        self.assertEqual(code, 200)
        snap = json.loads(body)["snapshot"]
        self.assertTrue(snap["last_ts"])
        self.assertIn("팀A비밀토픽", body)

        code, body_b = self._call("/topics", token="tok-team-b")       # 팀 B = 남의 라벨 없음
        self.assertEqual(code, 200)
        self.assertNotIn("팀A비밀토픽", body_b)
        self.assertIsNone(json.loads(body_b)["snapshot"]["last_ts"])

    def test_snapshot_all_iterates_teams_and_survives_failure(self):
        """주기 배치는 팀을 순회하고, 한 팀에서 터져도 나머지 팀은 계속 돈다."""
        SV, TPO = self.SV, self.TPO
        seen = []

        def _fake(team=None):
            seen.append(team)
            if team == "teamB":
                raise RuntimeError("팀 B 계산 실패")
            return {"single": [], "composite": [], "custom": []}

        orig = (SV.topics_data, TPO.snapshot_teams)
        SV.topics_data = _fake
        TPO.snapshot_teams = lambda: ["teamA", "teamB", "teamC"]
        self.addCleanup(lambda: (setattr(SV, "topics_data", orig[0]),
                                 setattr(TPO, "snapshot_teams", orig[1])))
        self.assertEqual(TPO.topic_snapshot_all(), 2)                  # A·C 성공 · B 만 실패
        self.assertEqual(seen, ["teamA", "teamB", "teamC"])

    def test_drill_uses_same_team(self):
        SV = self.SV
        seen = []
        orig = SV.results_rows
        SV.results_rows = lambda limit=5000, team=None: seen.append(team) or []
        self.addCleanup(lambda: setattr(SV, "results_rows", orig))
        SV.topic_drill("S-x", "teamA")
        self.assertEqual(seen, ["teamA"])


# ── O3: 배포 키 폐기 소유 확인 ───────────────────────────────────────────────
class _DeployStore:
    def __init__(self):
        self.revoked = []

    def deploy_get(self, dep_id, team=None):
        return {"id": dep_id, "team": "teamA"} if team in (None, "teamA") else None

    def deploy_key_revoke(self, key_id, dep_id):
        self.revoked.append((key_id, dep_id))
        return True


class TestDeployKeyRevokeOwnership(unittest.TestCase):
    """[O3] 폐기만 (id, team) 복합 필터를 빠져나가 타 팀 배포 키를 끊을 수 있었다."""

    def setUp(self):
        from prism import serve as SV
        self.SV = SV
        self.st = _DeployStore()
        orig = SV._STORE
        SV._STORE = self.st
        self.addCleanup(lambda: setattr(SV, "_STORE", orig))

    def test_other_team_revoke_refused(self):
        out = self.SV.deployment_key_revoke(7, 3, "teamB")
        self.assertFalse(out["ok"])
        self.assertEqual(self.st.revoked, [])          # 스토어까지 내려가지 않는다

    def test_own_team_revoke_ok(self):
        out = self.SV.deployment_key_revoke(7, 3, "teamA")
        self.assertTrue(out["ok"])
        self.assertEqual(self.st.revoked, [(3, 7)])

    def test_supastore_returns_actual_rowcount(self):
        """없는 키 폐기에 {"ok": true} 가 나가던 조용한 무시 제거(return=representation)."""
        from prism import supastore
        st = supastore.SupabaseStore.__new__(supastore.SupabaseStore)
        st._req = lambda m, t, query="", body=None, prefer="": ([] if "id=eq.9" in query
                                                                else [{"id": 3}])
        self.assertFalse(st.deploy_key_revoke(9, 7))
        self.assertTrue(st.deploy_key_revoke(3, 7))


# ── H3: 정수 쿼리 검증 ──────────────────────────────────────────────────────
class TestQueryIntValidation(unittest.TestCase):
    """[H3] `?limit=abc` 가 500 + 파이썬 예외 원문을 응답에 싣던 경로."""

    def test_qint_contract(self):
        from prism import serve as SV
        q = {"limit": ["abc"], "neg": ["-5"], "big": ["99999999999"], "ok": ["7"], "empty": [""]}
        self.assertEqual(SV._qint(q, "limit", 100, 1, 5000), 100)     # 비수치 → 기본값
        self.assertEqual(SV._qint(q, "none", 30, 1, 365), 30)         # 키 없음 → 기본값
        self.assertEqual(SV._qint(q, "empty", 30, 1, 365), 30)        # 빈 값 → 기본값
        self.assertEqual(SV._qint(q, "neg", 100, 1, 5000), 1)         # 음수 → 하한
        self.assertEqual(SV._qint(q, "big", 100, 1, 5000), 5000)      # 초대형 → 상한
        self.assertEqual(SV._qint(q, "ok", 100, 1, 5000), 7)


# ── H4: 공개 서빙 라우트 보호 ────────────────────────────────────────────────
class TestPublicPromptRoute(SupaGateMixin, unittest.TestCase):
    """[H4] 무인증 /api/v1/prompt 에 레이트리밋이 없어 원격 왕복·키 대입이 무제한이었다."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._halt()

    def test_rate_limited(self):
        codes = [self._call(f"/api/v1/prompt?slug=guess{i}")[0] for i in range(70)]
        self.assertIn(429, codes)

    def test_slug_existence_hidden(self):
        """슬러그가 있든 없든, 키가 틀리든 없든 같은 404 — 응답 코드로 슬러그를 열거할 수 없다."""
        from prism import serve as SV
        code_unknown, body_unknown = self._call("/api/v1/prompt?slug=nosuchslug")
        self.assertEqual(code_unknown, 404)
        st = SV.get_store()
        SV.deployment_save(None, slug="known-slug", version=0)
        SV._report_save("prompt_snapshot_latest", {"version": 1, "calls": {}}, None)
        SV._RL_HITS.clear()                           # 최소간격(0.2s) 걸림 방지 · 여기선 코드 동일성만 본다
        code_known, body_known = self._call("/api/v1/prompt?slug=known-slug")
        self.assertEqual(code_known, 404)
        self.assertEqual(json.loads(body_known), json.loads(body_unknown))
        self.assertTrue(st)


# ── H5: 집계 캐시 회수 ──────────────────────────────────────────────────────
class TestAggCacheSweep(unittest.TestCase):
    """[H5] _AGG_CACHE 만 만료 항목 회수 경로가 없어 사용자 수만큼 스냅샷이 영구 적재됐다."""

    def test_expired_entries_reclaimed(self):
        from prism import serve as SV
        SV._AGG_CACHE.clear()
        self.addCleanup(SV._AGG_CACHE.clear)
        past = time.time() - 1
        for i in range(300):                          # 만료된 항목 300개(과거 사용자별 스냅샷 흉내)
            SV._AGG_CACHE[("crew", "teamA", f"uid-{i}")] = (past, SV._AGG_VERSION, {"big": i})
        self.assertEqual(len(SV._AGG_CACHE), 300)
        SV._agg_cached(("new", "teamA"), lambda: 1)
        self.assertEqual(len(SV._AGG_CACHE), 1)       # 만료분 회수 + 새 항목만


# ── H6: 무인증 /auth 오류 문구 ───────────────────────────────────────────────
class TestAuthErrorHygiene(unittest.TestCase):
    """[H6] supabase auth 원문·내부 예외 문자열이 무인증 응답에 그대로 실리던 경로."""

    def test_http_error_body_not_leaked(self):
        import io
        from prism import adminops as AO
        orig = (AO._supa, AO._auth_post)
        AO._supa = lambda: ("http://auth.test", "k")

        def boom(url, path, key, body):
            raise urllib.error.HTTPError(
                "u", 400, "bad", None,
                io.BytesIO('{"code":400,"error_code":"invalid_credentials",'
                           '"msg":"내부 문구"}'.encode("utf-8")))
        AO._auth_post = boom
        self.addCleanup(lambda: (setattr(AO, "_supa", orig[0]),
                                 setattr(AO, "_auth_post", orig[1])))
        r = AO.auth_action({"mode": "login", "email": "a@b.c", "password": "pw"})
        self.assertFalse(r["ok"])
        self.assertNotIn("invalid_credentials", r["error"])
        self.assertNotIn("내부 문구", r["error"])
        self.assertNotIn("400", r["error"])

    def test_network_error_not_leaked(self):
        from prism import adminops as AO
        orig = (AO._supa, AO._auth_post)
        AO._supa = lambda: ("http://auth.test", "k")

        def boom(url, path, key, body):
            raise OSError("[Errno 8] nodename nor servname provided: supa-internal.example")
        AO._auth_post = boom
        self.addCleanup(lambda: (setattr(AO, "_supa", orig[0]),
                                 setattr(AO, "_auth_post", orig[1])))
        r = AO.auth_action({"mode": "login", "email": "a@b.c", "password": "pw"})
        self.assertFalse(r["ok"])
        self.assertNotIn("supa-internal", r["error"])



class TestMcpGatewayRateLimit(SupaGateMixin, unittest.TestCase):
    """[O2-2] 무인증 공개 경로에 IP 상한이 없으면 실패 트래픽이 무제한이 된다.

    이 클래스는 2026-08-13 스펙트럼 관문 제거 때 그쪽에서 옮겨 왔다. 관문은 사라졌지만
    거기서 배운 것은 `/mcp` 에 그대로 적용된다. **옮겨 오지 않았으면 그 교훈이 모듈과
    함께 사라졌을 것이다** — 프리즘의 무인증 MCP 표면은 이제 `/mcp` 하나뿐이고,
    이 파일이 그 표면의 상한 규칙을 지키는 유일한 자리다. 지우지 말 것."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._halt()

    def _hit(self, method="tools/list", i=1):
        return self._call("/mcp", {"jsonrpc": "2.0", "id": i, "method": method, "params": {}})[0]

    def test_rate_limited(self):
        """총량 상한은 있어야 한다. 키 대입 스프레이를 IP 로 억제하는 몫이다."""
        codes = [self._hit() for _ in range(260)]
        self.assertIn(429, codes)

    def test_handshake_is_not_rate_limited(self):
        """MCP 접속 절차는 한 연결에서 연달아 나간다. 최소 간격을 두면 정상 클라이언트가 끊긴다.

        2026-08-12 운영 실측(연결 재사용 · 당시 스펙트럼 관문):
        1회 401(0.127s) → 2회 429(41ms 뒤) → 3회 429. `min_interval=0.1` 은
        "정상 연사에는 여유 있다" 는 전제였는데, 접속 절차(initialize →
        notifications/initialized → tools/list)가 수십 ms 안에 끝나 그 전제가 틀렸다.
        브라우저로 눌러 보는 시연은 간격이 넉넉해 통과하므로 눈으로는 안 보인다.

        상한 자체를 없애자는 게 아니다. 분당 총량은 위 test_rate_limited 가 계속 지킨다.
        여기서 막는 것은 **간격 규칙의 부활**뿐이다."""
        codes = [self._hit(m, i) for i, m in
                 enumerate(("initialize", "notifications/initialized", "tools/list"))]
        self.assertNotIn(429, codes, f"접속 절차가 상한에 걸렸다: {codes}")


# ── P1: 클라이언트 조기 종료 가드 ────────────────────────────────────────────
class TestDisconnectGuard(SupaGateMixin, unittest.TestCase):
    """[P1] 응답 본문 write 에 끊김 가드가 없어 중단 요청 1건당 stderr 3.2KB·34줄이 샜다."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._halt()

    def test_flush_swallows_broken_pipe(self):
        SV = self.SV
        h = SV.Handler.__new__(SV.Handler)
        h.request_version = "HTTP/1.1"
        h._headers_buffer = [b"HTTP/1.1 200 OK\r\n"]
        h.close_connection = False

        class _Dead:
            def write(self, _b):
                raise BrokenPipeError(32, "Broken pipe")

        h.wfile = _Dead()
        self.assertFalse(h._flush(b"body"))           # 예외가 밖으로 나가지 않는다
        self.assertTrue(h.close_connection)

    def test_server_survives_abrupt_client_reset(self):
        """헤더만 읽고 RST 로 끊는 요청 10건 뒤에도 서버가 정상 응답한다."""
        for _ in range(10):
            s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nAccept-Encoding: identity\r\n\r\n")
            s.recv(64)                                # 헤더 일부만 읽고
            s.close()                                 # 본문 미소진 상태로 RST
        code, _ = self._call("/boot")
        self.assertEqual(code, 200)


# ── F3: 배포 감지 경량 라우트 ────────────────────────────────────────────────
class TestBootRoute(SupaGateMixin, unittest.TestCase):
    """[F3] /m 이 18B bootId 하나를 읽으려고 22KB /config 를 통째로 받던 경로."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._halt()

    def test_public_and_minimal(self):
        code, body = self._call("/boot")              # 무인증(운영 모드에서도 공개)
        self.assertEqual(code, 200)
        d = json.loads(body)
        self.assertEqual(sorted(d), ["bootId", "build"])
        self.assertEqual(d["bootId"], self.SV._BOOT_ID)
        _c, cfg = self._call("/config")
        self.assertLess(len(body), len(cfg))

    def test_mobile_client_uses_it(self):
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "prism", "vendor", "mobile.js")
        with open(p, encoding="utf-8") as f:
            src = f.read()
        head = src[src.index("async checkBoot()"):src.index("async checkBoot()") + 400]
        self.assertIn("fetch('/boot')", head)
        self.assertNotIn("fetch('/config')", head)


# ── T7: 엔티티 속성 인덱스 캐시 ─────────────────────────────────────────────
class TestEntIndexCache(unittest.TestCase):
    """[T7] 토픽 스튜디오 미리보기가 타이핑마다 엔티티 사전 전량을 재조회했다."""

    def setUp(self):
        from prism import serve as SV, topicops as TPO
        self.SV, self.TPO = SV, TPO
        self.n = 0

        class _EntStore:
            REMOTE = False

            def ent_attr_index(_s, team=""):
                self.n += 1
                return {"h1": [{"type": "person"}]}

        orig = SV._STORE
        SV._STORE = _EntStore()
        SV._AGG_CACHE.clear()
        self.addCleanup(lambda: (setattr(SV, "_STORE", orig), SV._AGG_CACHE.clear()))

    def test_repeat_calls_hit_cache_and_bump_invalidates(self):
        self.TPO._ent_index()
        self.TPO._ent_index()
        self.TPO._ent_index()
        self.assertEqual(self.n, 1)
        self.SV._agg_bump()                           # 사전 등재·수정 → 즉시 무효화
        self.TPO._ent_index()
        self.assertEqual(self.n, 2)

    def test_preview_without_eattrs_skips_index(self):
        """개체 속성 조건이 없으면 인덱스 자체를 만들지 않는다(타이핑 대부분의 경로)."""
        SV = self.SV
        rows = [{"hash": "h1", "displayServiceName": "svc", "title": "제목",
                 "item_meta": {"content_category": ["Sports"], "intent": ["정보"],
                               "entities": ["손흥민"]},
                 "quality_meta": {"finalGrade": "G"}}]
        orig = SV.results_rows
        SV.results_rows = lambda limit=5000, team=None: list(rows)
        self.addCleanup(lambda: setattr(SV, "results_rows", orig))
        self.TPO.topic_studio_action({"action": "preview",
                                      "def": {"name": "t", "cats": ["Sports"]}})
        self.assertEqual(self.n, 0)                   # 조건에 eattrs 없음 → 사전 조회 0회
        self.TPO.topic_studio_action({"action": "preview",
                                      "def": {"name": "t", "cats": ["Sports"],
                                              "eattrs": ["gender:female"]}})
        self.assertEqual(self.n, 1)                   # eattrs 있으면 그때만 인덱스를 만든다


if __name__ == "__main__":
    unittest.main()
