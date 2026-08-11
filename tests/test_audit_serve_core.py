"""serve-core 감사 수정 회귀(2026-08-04 · 감사 idx 1·2·8·23·24).

- _client_ip: Fly 프록시 뒤(FLY_APP_NAME 신호)에서만 Fly-Client-IP → X-Forwarded-For 첫 항목을
  신뢰하고, 아니면 client_address 유지(헤더 위조 차단). 레이트리밋 키가 프록시 IP 공유 버킷이
  되어 전 사용자 동반 429 가 나던 문제.
- 무인증 /config: config_status 전체 계산(모델 목록·저장 건수·팀 링크 원격 왕복) 없이
  로컬 값 슬림 응답(15초 헬스체크·로그인 화면 경로).
- do_GET 디스패치: 핸들러 예외 = 500 JSON(무응답 연결 종료 방지 · do_POST 와 대칭).
- /presence: gate=team + 운영에서 team=None 이면 방송 거부(전 팀 SSE 스트림 주입 차단).
- /reviewer: 팀 캐시(_TEAM_CACHE) 무효화를 등록(팀 쓰기) 완료 후로 이동
  (병렬 GET 이 옛 팀을 60s TTL 로 재캐시하는 경합 방지).

실행: python3 -m pytest tests/test_audit_serve_core.py -q  (stdlib unittest · 의존성 0)
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeHandler:
    """핸들러 단위 테스트용 최소 스텁(_client_ip · _p_presence · _p_reviewer 가 쓰는 것만)."""

    def __init__(self, headers=None, addr=("127.0.0.1", 50000), uid=None, team=None):
        self.headers = headers or {}
        self.client_address = addr
        self._uid = uid
        self._team = team

    def _bearer_uid(self):
        return self._uid

    def _req_team(self):
        return self._team

    def _inject_reviewer(self, data):
        data["reviewer"] = self._uid or data.get("reviewer") or ""
        return True


class TestClientIp(unittest.TestCase):
    """감사 idx 1: 프록시 뒤에서만 클라이언트 IP 헤더를 신뢰한다."""

    def setUp(self):
        self._orig = os.environ.pop("FLY_APP_NAME", None)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._orig is None:
            os.environ.pop("FLY_APP_NAME", None)
        else:
            os.environ["FLY_APP_NAME"] = self._orig

    def test_without_proxy_headers_are_ignored(self):
        """프록시 뒤가 아니면 헤더는 클라이언트가 위조 가능 → client_address 유지."""
        from prism import serve as SV
        h = _FakeHandler(headers={"Fly-Client-IP": "198.51.100.9",
                                  "X-Forwarded-For": "198.51.100.8"},
                         addr=("192.0.2.1", 1))
        self.assertEqual(SV._client_ip(h), "192.0.2.1")

    def test_behind_proxy_fly_client_ip_wins(self):
        from prism import serve as SV
        os.environ["FLY_APP_NAME"] = "prism-item"
        h = _FakeHandler(headers={"Fly-Client-IP": "198.51.100.9",
                                  "X-Forwarded-For": "203.0.113.7, 10.0.0.1"},
                         addr=("172.16.0.1", 1))
        self.assertEqual(SV._client_ip(h), "198.51.100.9")

    def test_behind_proxy_xff_first_entry_fallback(self):
        from prism import serve as SV
        os.environ["FLY_APP_NAME"] = "prism-item"
        h = _FakeHandler(headers={"X-Forwarded-For": " 203.0.113.7 , 10.0.0.1"},
                         addr=("172.16.0.1", 1))
        self.assertEqual(SV._client_ip(h), "203.0.113.7")

    def test_behind_proxy_without_headers_falls_back(self):
        """프록시 신호가 있어도 헤더가 없으면(로컬 직접 호출) client_address."""
        from prism import serve as SV
        os.environ["FLY_APP_NAME"] = "prism-item"
        self.assertEqual(SV._client_ip(_FakeHandler(addr=("192.0.2.5", 1))), "192.0.2.5")
        self.assertEqual(SV._client_ip(_FakeHandler(addr=None)), "?")


class _ServerMixin:
    """sqlite 스크래치 서버(스레드) 공용 부팅."""

    @classmethod
    def _boot(cls):
        os.environ["PRISM_BACKEND"] = "sqlite"
        os.environ["PRISM_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
        from http.server import ThreadingHTTPServer
        from prism import serve as SV
        cls.SV = SV
        SV._STORE = None
        SV.Handler.server_mock = True
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), SV.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def _halt(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.SV._STORE = None

    def _req(self, path, obj=None, headers=None):
        data = json.dumps(obj).encode() if obj is not None else None
        hdrs = {"Content-Type": "application/json"} if data else {}
        hdrs.update(headers or {})
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     headers=hdrs, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")


class TestGetDispatch500(_ServerMixin, unittest.TestCase):
    """감사 idx 8: GET 핸들러 예외가 무응답 연결 종료가 아니라 500 JSON 으로 돌아온다."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._halt()

    def test_handler_exception_returns_500_json(self):
        # 수정 전: 핸들러 예외가 do_GET 밖으로 전파 → 응답 없이 연결 종료(empty reply)
        SV = self.SV
        orig = SV._GET_ROUTES["/vocab"]

        def _boom(h, q):
            raise RuntimeError("경계 밖 실패")
        SV._GET_ROUTES["/vocab"] = (_boom, orig[1])
        self.addCleanup(lambda: SV._GET_ROUTES.__setitem__("/vocab", orig))
        code, body = self._req("/vocab")
        self.assertEqual(code, 500)
        # 고정 문구만 · 파이썬 예외 원문은 서버 로그로만(내부 구현 노출 차단 · 감사 H3)
        self.assertIn("error", json.loads(body))
        self.assertNotIn("경계 밖 실패", body)

    def test_bad_int_query_falls_back_to_default(self):
        """정수 쿼리 검증(_qint): 비수치·범위 밖은 500 이 아니라 기본값·경계로 수렴(감사 H3)."""
        for path in ("/raw?limit=abc", "/entdict?limit=abc", "/raw?limit=-1",
                     "/raw?limit=99999999999", "/crew-weekly?weeks=abc"):
            code, _body = self._req(path)
            self.assertIn(code, (200, 403), path)     # 403 = 역할 게이트(로컬 sqlite 는 200)

    def test_valid_get_still_ok(self):
        code, body = self._req("/raw?limit=2")
        self.assertEqual(code, 200)
        self.assertIn("items", json.loads(body))


class TestProxyRateLimitKey(_ServerMixin, unittest.TestCase):
    """감사 idx 1(HTTP 왕복): Fly 프록시 뒤에서 레이트리밋 버킷이 실제 클라이언트 IP 로 갈린다."""

    @classmethod
    def setUpClass(cls):
        cls._boot()

    @classmethod
    def tearDownClass(cls):
        cls._halt()

    def setUp(self):
        self._orig = os.environ.pop("FLY_APP_NAME", None)
        self.addCleanup(self._restore)
        os.environ["FLY_APP_NAME"] = "prism-item"

    def _restore(self):
        if self._orig is None:
            os.environ.pop("FLY_APP_NAME", None)
        else:
            os.environ["FLY_APP_NAME"] = self._orig

    def test_different_clients_not_shared_bucket(self):
        """수정 전: 두 사용자가 같은 프록시 IP 버킷을 공유해 1초 안 연속 요청이면 후자가 429."""
        t0 = time.time()
        code_a, _ = self._req("/check-source", {"url": ""},
                              headers={"Fly-Client-IP": "198.51.100.1"})
        code_b, _ = self._req("/check-source", {"url": ""},
                              headers={"Fly-Client-IP": "198.51.100.2"})
        self.assertEqual(code_a, 200)
        self.assertEqual(code_b, 200)          # 다른 클라이언트는 다른 버킷(수정 전 429)
        # 같은 클라이언트의 연속 요청은 여전히 min_interval(1s)에 걸린다(억제 목적 유지)
        code_a2, _ = self._req("/check-source", {"url": ""},
                               headers={"Fly-Client-IP": "198.51.100.1"})
        if time.time() - t0 < 1.0:             # 느린 머신에서 창을 벗어나면 판정 생략(플레이크 방지)
            self.assertEqual(code_a2, 429)

    def test_xff_fallback_used_when_no_fly_header(self):
        code, _ = self._req("/check-source", {"url": ""},
                            headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"})
        self.assertEqual(code, 200)
        code2, _ = self._req("/check-source", {"url": ""},
                             headers={"X-Forwarded-For": "203.0.113.10, 10.0.0.1"})
        self.assertEqual(code2, 200)           # XFF 첫 항목이 버킷 키(공유 버킷이면 429)


class TestConfigSlimUnauth(_ServerMixin, unittest.TestCase):
    """감사 idx 2: 운영(supabase) 무인증 /config 는 config_status 를 아예 타지 않는다."""

    @classmethod
    def setUpClass(cls):
        cls._boot()
        cls._orig_supa = cls.SV._supa
        cls.SV._supa = lambda: ("http://supabase.local", "test-key")   # 운영 모드 흉내

    @classmethod
    def tearDownClass(cls):
        cls.SV._supa = cls._orig_supa
        cls._halt()

    def test_unauth_config_skips_config_status(self):
        SV = self.SV

        def _boom(team=None):
            raise AssertionError("무인증 /config 가 config_status 를 호출하면 안 됩니다")

        orig = SV.config_status
        SV.config_status = _boom
        try:
            code, body = self._req("/config")
        finally:
            SV.config_status = orig
        self.assertEqual(code, 200)
        cfg = json.loads(body)
        # 기존 슬림 계약과 동일한 키 집합(배포 검증 curl /config · 로그인 화면 · 헬스체크)
        self.assertEqual(set(cfg), {"bootId", "build", "configured", "forcedMock",
                                    "ingesting", "backend", "authRequired", "keyManagedByServer"})
        self.assertEqual(cfg["backend"], "supabase")
        self.assertTrue(cfg["authRequired"])
        for k in ("systemPrompt", "availableModels", "guideUrls", "storedCount", "modelMeta"):
            self.assertNotIn(k, cfg, k)

    def test_unauth_presence_rejected(self):
        """감사 idx 23(게이트): 운영에서 미인증 /presence 는 gate=team 이 401 로 차단."""
        code, _ = self._req("/presence", {"hash": "x", "action": "viewing"})
        self.assertEqual(code, 401)


class TestPresenceTeamGuard(unittest.TestCase):
    """감사 idx 23: team=None 브로드캐스트(전 팀 SSE 주입) 거부 · 팀 스코프 방송 유지."""

    def test_gate_registered_as_team(self):
        from prism import serve as SV
        self.assertEqual(SV._POST_ROUTES["/presence"][1], "team")

    def test_supa_teamless_broadcast_refused(self):
        from prism import serve as SV
        orig = SV._supa
        SV._supa = lambda: ("http://supabase.local", "test-key")
        q = SV._sse_subscribe("teamA")                # 다른 팀 구독자
        try:
            out = SV._p_presence(_FakeHandler(uid="uid-noteam", team=None),
                                 b'{"hash": "h1", "action": "viewing"}')
            self.assertEqual(out, {"ok": False})
            self.assertTrue(q.empty(), "team=None 방송이 전 팀 구독자에게 전파되면 안 됩니다")
        finally:
            SV._supa = orig
            SV._sse_unsubscribe(q)

    def test_supa_team_scoped_broadcast_delivered(self):
        from prism import serve as SV
        orig = SV._supa
        SV._supa = lambda: ("http://supabase.local", "test-key")
        q_same = SV._sse_subscribe("teamA")
        q_other = SV._sse_subscribe("teamB")
        try:
            out = SV._p_presence(_FakeHandler(uid="uid-a", team="teamA"),
                                 b'{"hash": "h2", "action": "typing"}')
            self.assertEqual(out, {"ok": True})
            ev = q_same.get_nowait()
            self.assertEqual((ev["type"], ev["reviewer"], ev["hash"]), ("presence", "uid-a", "h2"))
            self.assertTrue(q_other.empty(), "같은 팀에만 방송되어야 합니다")
        finally:
            SV._supa = orig
            SV._sse_unsubscribe(q_same)
            SV._sse_unsubscribe(q_other)

    def test_sqlite_local_broadcast_kept(self):
        """로컬(sqlite · 단일 팀) 모드는 기존처럼 전 구독자 방송 유지."""
        from prism import serve as SV
        q = SV._sse_subscribe(None)
        try:
            body = json.dumps({"reviewer": "복실", "hash": "h3"}).encode("utf-8")
            out = SV._p_presence(_FakeHandler(uid=None, team=None), body)
            self.assertEqual(out, {"ok": True})
            ev = q.get_nowait()
            self.assertEqual((ev["reviewer"], ev["action"]), ("복실", "viewing"))
        finally:
            SV._sse_unsubscribe(q)


class TestReviewerTeamCachePopOrder(unittest.TestCase):
    """감사 idx 24: 팀 캐시 무효화는 등록(팀 쓰기) '완료 후' — 쓰기 중 재캐시 경합 차단."""

    def test_pop_happens_after_register_write(self):
        from prism import serve as SV
        uid = "uid-pop-order"
        SV._TEAM_CACHE[uid] = ("old-team", time.time() + 60)
        seen = {}
        orig = SV.register_reviewer

        def fake_register(data):
            # 팀 쓰기 진행 중 시점: pop 이 이미 실행됐다면(수정 전 순서) 캐시가 비어 있어
            # 병렬 GET 의 team_of 가 옛 팀을 다시 캐시하는 경합 창이 열린다.
            seen["cached_during_write"] = uid in SV._TEAM_CACHE
            return {"ok": True}

        SV.register_reviewer = fake_register
        try:
            out = SV._p_reviewer(_FakeHandler(uid=uid), b"{}")
        finally:
            SV.register_reviewer = orig
            SV._TEAM_CACHE.pop(uid, None)
        self.assertTrue(out.get("ok"))
        self.assertTrue(seen["cached_during_write"],
                        "무효화가 등록 완료 전에 실행되면 재캐시 경합이 재발합니다")

    def test_cache_invalidated_after_success(self):
        from prism import serve as SV
        uid = "uid-pop-after"
        SV._TEAM_CACHE[uid] = ("old-team", time.time() + 60)
        orig = SV.register_reviewer
        SV.register_reviewer = lambda data: {"ok": True}
        try:
            SV._p_reviewer(_FakeHandler(uid=uid), b"{}")
        finally:
            SV.register_reviewer = orig
        self.assertNotIn(uid, SV._TEAM_CACHE, "등록 완료 후에는 즉시 무효화되어야 합니다")


if __name__ == "__main__":
    unittest.main()
