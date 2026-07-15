"""2026-07-15 전수 QA 감사에서 발견된 결함 회귀 가드.

- 유령 콘텐츠: 전체 삭제 후 메모리 잔상(_LAST_RESULTS)이 화면에 부활
- 골든 업로드: 로컬(sqlite) 모드에서 관리자 게이트에 막혀 등록 불가
- 사전 초기화: overrides 파일만 지우고 메모리는 재시작까지 유지
- QA 시드: YELLOW(검수 대기) 0건 → 진척 게이지 분모 0 고정
- 라우팅: /favicon.ico 가 323KB SPA HTML 로 폴스루 · 미등록 경로 200

실행: python3 -m pytest tests/test_qa_audit_fixes.py -q
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mk_store():
    from prism.store import Store
    return Store(os.path.join(tempfile.mkdtemp(), "t.db"))


def _row(title, grade="G", review=""):
    qm = {"finalGrade": grade, "reasons": []}
    if review:
        qm["review"] = review
    return ({"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "본문 " + title},
            {"content_ref": {"title": title, "displayServiceName": "뉴스", "body": "본문 " + title},
             "item_meta": {"summary": "리드문"}, "quality_meta": qm, "trace": {}})


class TestGhostContents(unittest.TestCase):
    """전체 삭제 후 유령 콘텐츠: results_rows 가 '빈 저장소'를 '저장소 없음'으로 오인해 메모리 폴백."""

    def _bind(self, st):
        import prism.serve as S
        orig_get, orig_last = S.get_store, list(S._LAST_RESULTS)
        S.get_store = lambda: st
        self.addCleanup(lambda: (setattr(S, "get_store", orig_get),
                                 S._LAST_RESULTS.__setitem__(slice(None), orig_last)))
        return S

    def test_empty_store_is_trusted_over_memory(self):
        S = self._bind(_mk_store())
        S._LAST_RESULTS[:] = [_row("유령")[1]]
        self.assertEqual(S.results_rows(), [])           # 빈 저장소 = 빈 결과(폴백 금지)
        S.get_store = lambda: None
        self.assertEqual(len(S.results_rows()), 1)       # 저장소 부재 시에만 메모리 폴백

    def test_clear_contents_purges_memory_mirror(self):
        st = _mk_store()
        S = self._bind(st)
        from prism import adminops as AO
        S.store_save([_row("지워질 콘텐츠")], source="test")
        self.assertEqual(len(S.results_rows()), 1)
        r = AO.admin_action("", None, {"action": "clear_contents"})
        self.assertTrue(r["ok"])
        self.assertEqual(S._LAST_RESULTS, [])            # 메모리 미러 동반 정리
        self.assertEqual(S.results_rows(), [])           # 화면 원천에서 즉시 사라짐


class TestGoldenLocalUpload(unittest.TestCase):
    """골든셋 .jsonl 등록: 로컬(sqlite)은 개방 · supabase 는 관리자 게이트 유지."""

    def _bind(self, st):
        import prism.serve as S
        orig = S.get_store
        S.get_store = lambda: st
        self.addCleanup(lambda: setattr(S, "get_store", orig))
        return S

    def test_local_mode_open_supabase_gated(self):
        S = self._bind(_mk_store())
        rows = [{"content": {"displayServiceName": "뉴스", "title": "골든", "subtitle": "", "body": "b"},
                 "expected": {"finalGrade": "G", "content_category": [], "reasons": []}}]
        r = S.register_golden("", None, rows, "", merge=True)    # 로컬: uid 없음 = 허용
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 1)
        # supabase 모드: 비관리자는 여전히 차단
        orig_supa, orig_adm = S._supa, S.is_admin_user
        S._supa = lambda: ("http://auth.test", "k")
        S.is_admin_user = lambda *a, **k: False
        self.addCleanup(lambda: (setattr(S, "_supa", orig_supa),
                                 setattr(S, "is_admin_user", orig_adm)))
        r2 = S.register_golden("", None, rows, "")
        self.assertFalse(r2["ok"])
        self.assertIn("관리자 전용", r2["error"])


class TestDictResetImmediate(unittest.TestCase):
    """사전 편집 초기화: overrides 파일 삭제 + 메모리 원본 즉시 복원(재시작 불필요)."""

    def test_reset_restores_base_without_restart(self):
        import prism.serve as S
        from prism import dictionaries as D
        orig_path = S._DICT_OVERRIDES_PATH
        orig_vals = (list(D.INTENT_CATEGORIES_UNIVERSAL), D._BASE_SNAPSHOT)
        S._DICT_OVERRIDES_PATH = os.path.join(tempfile.mkdtemp(), "ov.json")

        def _restore():
            S._DICT_OVERRIDES_PATH = orig_path
            D.INTENT_CATEGORIES_UNIVERSAL = orig_vals[0]
            D._BASE_SNAPSHOT = orig_vals[1]
        self.addCleanup(_restore)

        D._BASE_SNAPSHOT = None                          # 이 테스트 기준의 '원본' 확정
        base = ["기준 인텐트 A", "기준 인텐트 B"]
        D.INTENT_CATEGORIES_UNIVERSAL = list(base)
        r = S.edit_dict({"target": "intent_universal", "value": ["편집된 인텐트"]})
        self.assertTrue(r.get("saved"))
        self.assertEqual(D.INTENT_CATEGORIES_UNIVERSAL, ["편집된 인텐트"])
        S.reset_dict_overrides()
        self.assertEqual(D.INTENT_CATEGORIES_UNIVERSAL, base)   # 재시작 없이 원본 복원
        self.assertFalse(os.path.exists(S._DICT_OVERRIDES_PATH))


class TestQaSeedYellow(unittest.TestCase):
    """QA 시드: 검수 대기(YELLOW) 2건 포함 → 진척 게이지 분모(yellow_count)·대기 큐가 채워진다."""

    def test_seed_creates_yellow_targets(self):
        import prism.serve as S
        from prism import qa_seed
        st = _mk_store()
        orig_get, orig_last = S.get_store, list(S._LAST_RESULTS)
        orig_mock = S.Handler.server_mock
        S.get_store = lambda: st
        S.Handler.server_mock = True                    # 시드는 외부 호출 0(결정론)
        self.addCleanup(lambda: (setattr(S, "get_store", orig_get),
                                 setattr(S.Handler, "server_mock", orig_mock),
                                 S._LAST_RESULTS.__setitem__(slice(None), orig_last)))
        out = qa_seed.seed(verbose=False)
        self.assertTrue(out["ok"])
        self.assertEqual(out["contents"], 12)
        self.assertEqual(out["yellow"], 2)
        self.assertEqual(st.yellow_count(), 2)          # 진척 게이지 분모
        q = S.review_queue({"reviewer": ""})            # 검수 대기 큐 = YELLOW 2건
        self.assertEqual(q["n"], 2)
        self.assertEqual(st.count(), 12)


class TestOnboardingLocalBranch(unittest.TestCase):
    """온보딩 모달: 로컬(sqlite) 모드는 이메일·비밀번호 없이 닉네임·캐릭터만으로 시작(로그인 벽 금지)."""

    def test_page_has_local_register_branch(self):
        from prism import page
        # 로컬 등록 섹션(닉네임·캐릭터)과 supabase 전용 로그인 섹션이 분기돼 있어야 한다
        self.assertIn("backend!=='supabase' && !authToken && !authBusy", page.PAGE)
        self.assertIn("backend==='supabase' && !authToken && !authBusy", page.PAGE)
        self.assertIn("'시작하기'", page.PAGE)                     # 로컬 CTA(이메일·비번 요구 없음)
        # CTA 활성 조건: 로컬은 닉네임만 요구
        self.assertIn("(authToken || backend!=='supabase') ? !(reviewer||'').trim()", page.PAGE)


def _http(port, path, obj=None, ctype="application/json"):
    data = json.dumps(obj).encode() if obj is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 headers={"Content-Type": ctype} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


class TestRoutingContracts(unittest.TestCase):
    """favicon 실자산 응답 · 미등록 경로 404 · 로컬 골든 업로드 HTTP 왕복 · 삭제 후 화면 정리."""

    @classmethod
    def setUpClass(cls):
        os.environ["PRISM_BACKEND"] = "sqlite"
        os.environ["PRISM_DB"] = os.path.join(tempfile.mkdtemp(), "routes.db")
        from http.server import ThreadingHTTPServer
        from prism import config as _cfg
        from prism import serve
        cls._orig_cfg_path = _cfg.DEFAULT_CONFIG_PATH   # 실제 config.json 오염 방지(격리)
        _cfg.DEFAULT_CONFIG_PATH = os.path.join(tempfile.mkdtemp(), "config.json")
        cls._cfg_mod = _cfg
        serve._STORE = None
        serve._LAST_RESULTS[:] = []
        serve.Handler.server_mock = True
        cls.serve = serve
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.serve._STORE = None
        cls._cfg_mod.DEFAULT_CONFIG_PATH = cls._orig_cfg_path

    def test_favicon_serves_real_icon(self):
        code, ctype, body = _http(self.port, "/favicon.ico")
        self.assertEqual(code, 200)
        self.assertIn("image/svg", ctype)               # SPA HTML(323KB) 폴스루 금지
        self.assertLess(len(body), 100_000)

    def test_unknown_path_is_404_but_pages_stay_200(self):
        code, ctype, _ = _http(self.port, "/nonexistent-route-xyz")
        self.assertEqual(code, 404)
        self.assertIn("application/json", ctype)        # API 오타가 HTML 200 으로 가려지지 않게
        for path in ("/", "/?m=home", "/m"):
            code, ctype, _ = _http(self.port, path)
            self.assertEqual((path, code), (path, 200))
            self.assertIn("text/html", ctype)

    def test_golden_upload_roundtrip_local(self):
        line = json.dumps({"content": {"displayServiceName": "뉴스", "title": "HTTP 골든", "body": "b"},
                           "expected": {"finalGrade": "G"}}, ensure_ascii=False)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/golden",
                                     data=line.encode(), headers={"Content-Type": "application/jsonl"})
        with urllib.request.urlopen(req, timeout=20) as r:
            out = json.loads(r.read())
        self.assertTrue(out.get("ok"), out)             # 로컬 모드: 관리자 게이트 없이 등록
        self.assertEqual(out.get("count"), 1)

    def test_clear_contents_empties_views(self):
        code, _, body = _http(self.port, "/run", {"displayServiceName": "뉴스", "title": "삭제 대상",
                                                  "body": "삭제 후 화면에 남으면 안 되는 본문"})
        self.assertEqual(code, 200)
        code, _, body = _http(self.port, "/raw?limit=100")
        self.assertGreaterEqual(json.loads(body)["n"], 1)
        code, _, body = _http(self.port, "/admin", {"action": "clear_contents"})
        self.assertTrue(json.loads(body)["ok"])
        code, _, body = _http(self.port, "/raw?limit=100")
        self.assertEqual(json.loads(body)["n"], 0)      # 유령 콘텐츠 없음


if __name__ == "__main__":
    unittest.main()
