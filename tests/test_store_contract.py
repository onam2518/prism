"""스토어 계약 테스트: sqlite(Store)와 supabase(SupabaseStore)가 같은 의미로 동작해야 하는
메서드들을 하나의 시나리오 모음으로 검증한다(이중 구현 표류 방지).

- 기본: sqlite 에 대해 항상 실행.
- supabase: 환경변수 PRISM_TEST_SUPABASE=1 + SUPABASE_URL/SUPABASE_SERVICE_KEY 가 있을 때만
  같은 시나리오를 라이브로 실행(테스트 전용 team_key 로 스코프 · 종료 시 정리). CI 기본은 skip.

실행: python3 -m pytest tests/test_store_contract.py -q
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_TEAM = None            # sqlite 는 단일 팀(None)


class StoreContractMixin:
    """구현체 공용 시나리오. self.st(스토어)·self.team 을 서브클래스가 제공."""

    def test_purpose_roundtrip(self):
        st, team = self.st, self.team
        h = self._hash("계약-용도")
        self._seed_content(h, "계약-용도")
        self.assertEqual(st.set_purpose([h], "eval", team=team), 1)
        self.assertEqual(st.purpose_map(team).get(h), "eval")
        self.assertEqual(st.set_purpose([h], "잘못된값", team=team), 0)
        st.set_purpose([h], "review", team=team)
        self.assertEqual(st.purpose_map(team).get(h), "review")

    def test_eval_check_one_vote_upsert(self):
        st, team = self.st, self.team
        h = self._hash("계약-판정")
        self.assertTrue(st.save_eval_check(h, "계약검수자", "adopt", "R", "G", team=team))
        self.assertTrue(st.save_eval_check(h, "계약검수자", "reject", team=team))
        c = st.eval_check_counts(team=team).get(h) or {}
        self.assertEqual((c.get("adopt"), c.get("reject")), (0, 1))
        self.assertFalse(st.save_eval_check(h, "계약검수자", "이상", team=team))

    def test_report_persistence(self):
        st, team = self.st, self.team
        payload = {"ts": 1.0, "golden": {"confirmed": 3}}
        st.save_report("contract_test", payload, team=team)
        got = st.get_report("contract_test", team=team)
        self.assertEqual((got or {}).get("golden", {}).get("confirmed"), 3)
        st.save_report("contract_test", {"ts": 2.0}, team=team)      # upsert
        self.assertNotIn("golden", st.get_report("contract_test", team=team) or {})
        self.assertIsNone(st.get_report("contract_없는키", team=team))

    def test_golden_register_and_count(self):
        st, team = self.st, self.team
        content = {"displayServiceName": "뉴스", "title": "계약-골든", "subtitle": "", "body": "b"}
        n = st.register_golden(team, [{"content": content, "expected": {"finalGrade": "G"}}],
                               replace=True, source="manual")
        self.assertEqual(n, 1)
        self.assertGreaterEqual(st.golden_count(team), 1)
        rows = st.golden_rows(team, limit=10)
        self.assertTrue(any(r.get("title") == "계약-골든" for r in rows))

    def test_save_many_include_all_nonyellow(self):
        """재실행 경로 계약: 비-YELLOW 결과도 include_all=True 면 upsert 된다
        (supabase 가 yellow 필터로 재실행 갱신을 놓치던 엣지의 회귀 방지)."""
        st, team = self.st, self.team
        content = {"displayServiceName": "뉴스", "title": "계약-재실행", "subtitle": "", "body": "b"}
        out = {"quality_meta": {"finalGrade": "G", "review": ""}, "item_meta": {},
               "trace": {"model": "contract-m2", "version": 2}}
        n = st.save_many([(content, out)], "rerun", source="재실행", team=team, include_all=True)
        self.assertEqual(n, 1)

    def test_save_dedup_persists_nonyellow(self):
        """인입 경로 계약: 관리자 인입(수동·엑셀·자동)은 G/auto 등 비-YELLOW 도 전량 적재된다.
        (supabase 가 yellow 만 저장해 인입 콘텐츠가 목록에 안 뜨던 2026-07-06 결함의 회귀 방지)"""
        st, team = self.st, self.team
        content = {"displayServiceName": "뉴스", "title": "계약-인입-G", "subtitle": "", "body": "본문"}
        out = {"quality_meta": {"finalGrade": "G", "review": "auto"}, "item_meta": {"summary": "s"},
               "trace": {"model": "contract-m3", "version": 1}}
        st.save_dedup([(content, out)], "run-x", source="단건", team=team)
        from prism.store import content_hash
        h = content_hash(content)
        self.assertIn(h, {r["hash"] for r in st.recent_meta(200, team=team)},
                      "비-YELLOW 인입 콘텐츠가 저장 목록에 없다")

    def test_first_source_survives_rerun(self):
        """인입 경로 라벨 계약: source 는 최초 인입 1회만 기록한다.
        재실행 upsert 가 덮어쓰면 인입 채널 추적이 불가능해진다
        (2026-08-03 운영 실측: prism_contents 400건 전부 '재실행'으로 덮임 · 복구 불가)."""
        from prism.store import content_hash
        st, team = self.st, self.team
        content = {"displayServiceName": "뉴스", "title": "계약-출처보존", "subtitle": "", "body": "본문"}
        out = {"quality_meta": {"finalGrade": "G", "review": "yellow"}, "item_meta": {"summary": "s1"},
               "trace": {"model": "contract-src1", "version": 1}}
        st.save_dedup([(content, out)], "run-src", source="엑셀", team=team)      # 최초 인입
        out2 = {"quality_meta": {"finalGrade": "R", "review": "yellow"}, "item_meta": {"summary": "s2"},
                "trace": {"model": "contract-src2", "version": 2}}
        st.save_many([(content, out2)], "rerun", source="재실행", team=team, include_all=True)
        h = content_hash(content)
        row = next(r for r in st.recent_meta(200, team=team) if r["hash"] == h)
        self.assertEqual(row["source"], "엑셀", "재실행이 최초 인입 경로를 덮어썼다")
        self.assertEqual(row["model"], "contract-src2", "나머지 필드는 최신 실행을 따라가야 한다")

    def test_blank_source_is_backfilled(self):
        """라벨이 비어 있던 행(구 데이터·CLI 경로)은 다음 저장에서 채워진다 —
        '최초 값 보존'이 빈 값을 영구 고착시키면 안 된다."""
        from prism.store import content_hash
        st, team = self.st, self.team
        content = {"displayServiceName": "뉴스", "title": "계약-출처백필", "subtitle": "", "body": "본문"}
        out = {"quality_meta": {"finalGrade": "G", "review": "yellow"}, "item_meta": {"summary": "s"},
               "trace": {"model": "contract-src3", "version": 1}}
        st.save_dedup([(content, out)], "run-blank", source="", team=team)
        out2 = dict(out, item_meta={"summary": "s2"})
        st.save_dedup([(content, out2)], "run-blank2", source="자동 인입", team=team)
        h = content_hash(content)
        row = next(r for r in st.recent_meta(200, team=team) if r["hash"] == h)
        self.assertEqual(row["source"], "자동 인입")

    def test_save_dedup_duplicate_rows_in_one_batch(self):
        """한 배치에 동일 hash 행이 중복돼도 저장이 성공한다(1건으로 병합).
        supabase 는 중복이 섞이면 upsert 전체가 Postgres 21000 으로 죽던 결함의 회귀 방지."""
        st, team = self.st, self.team
        content = {"displayServiceName": "뉴스", "title": "계약-중복행", "subtitle": "", "body": "본문"}
        out = {"quality_meta": {"finalGrade": "G", "review": "auto"}, "item_meta": {"summary": "s"},
               "trace": {"model": "contract-m5", "version": 1}}
        st.save_dedup([(content, out), (dict(content), dict(out))], "run-z", source="배치", team=team)
        from prism.store import content_hash
        h = content_hash(content)
        self.assertEqual(sum(1 for r in st.recent_meta(200, team=team) if r["hash"] == h), 1)

    def test_remove_content_cascade(self):
        """개별 삭제 계약: 콘텐츠 삭제 시 결과·초안·피드백 파생이 함께 사라진다(골든은 보존)."""
        st, team = self.st, self.team
        content = {"displayServiceName": "뉴스", "title": "계약-삭제", "subtitle": "", "body": "본문"}
        out = {"quality_meta": {"finalGrade": "G", "review": "auto"}, "item_meta": {},
               "trace": {"model": "contract-m4", "version": 1}}
        st.save_dedup([(content, out)], "run-y", source="단건", team=team)
        from prism.store import content_hash
        h = content_hash(content)
        st.save_draft(h, "contract-m4", 1, {}, {}, team=team)
        self.assertTrue(st.remove_content(h, team=team))
        self.assertNotIn(h, {r["hash"] for r in st.recent_meta(200, team=team)},
                         "삭제 후에도 목록에 남아 있다")
        self.assertEqual(st.draft_history(h, team=team), [], "삭제 후에도 초안 이력이 남아 있다")

    def test_draft_history_roundtrip(self):
        """(hash, 모델, 버전) 초안 이력: 버전별 축적 + 같은 키 upsert."""
        st, team = self.st, self.team
        h = self._hash("계약-이력")
        st.save_draft(h, "m1", 1, {"summary": "v1"}, {"finalGrade": "G"}, team=team)
        st.save_draft(h, "m1", 2, {"summary": "v2"}, {"finalGrade": "G"}, team=team)
        st.save_draft(h, "m1", 2, {"summary": "v2-갱신"}, {"finalGrade": "R"}, team=team)
        rows = st.draft_history(h, team=team)
        self.assertEqual(len(rows), 2)
        by_v = {r["version"]: r for r in rows}
        self.assertEqual(by_v[1]["item_meta"].get("summary"), "v1")
        self.assertEqual(by_v[2]["item_meta"].get("summary"), "v2-갱신")
        self.assertEqual(st.draft_history(self._hash("계약-이력없음"), team=team), [])

    def test_routes_model_layering(self):
        """피드백 라우트: 모델 미기록=공통, 모델 귀속=그 모델 전용으로 분리 조회."""
        st, team = self.st, self.team
        h = self._hash("계약-라우트")
        st.save_routes(h, "", [{"element": "category", "stage": "analyze", "directive": "공통 지시"}],
                       team=team)
        st.save_routes(h, "", [{"element": "category", "stage": "analyze", "directive": "solar 전용 지시"}],
                       team=team, model="solar-계약")
        common = st.routes_by_stage(team=team)
        self.assertIn("공통 지시", common.get("analyze", []))
        self.assertNotIn("solar 전용 지시", common.get("analyze", []))
        bm = st.routes_by_stage_model(team=team)
        self.assertIn("solar 전용 지시", (bm.get("solar-계약") or {}).get("analyze", []))
        self.assertNotIn("", bm)

    def test_content_roundtrip_hash_stable(self):
        """저장→재구성(recent) 콘텐츠의 해시 불변: subtitle 이 소실되면 재실행 upsert 가
        원본 행 대신 유령 행을 만들고 골든 매칭이 깨진다(supabase 회귀 방지)."""
        from prism.store import content_hash
        st, team = self.st, self.team
        content = {"displayServiceName": "뉴스", "title": "계약-부제보존", "subtitle": "부제목", "body": "b"}
        out = {"content_ref": dict(content),
               "quality_meta": {"finalGrade": "G", "review": "yellow"}, "item_meta": {},
               "trace": {"model": "contract-m", "version": 1}}
        st.save_many([(content, out)], "t", source="계약", team=team, include_all=True)
        row = next(r for r in st.recent(team=team)
                   if (r.get("content_ref") or {}).get("title") == "계약-부제보존")
        ref = row["content_ref"]
        rebuilt = {"displayServiceName": ref.get("displayServiceName", ""), "title": ref.get("title", ""),
                   "subtitle": ref.get("subtitle", ""), "body": ref.get("body", "")}
        self.assertEqual(content_hash(rebuilt), content_hash(content))

    # ── helpers ──
    def _hash(self, title):
        from prism.store import content_hash
        return content_hash({"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "b"})


class TestSqliteContract(StoreContractMixin, unittest.TestCase):
    def setUp(self):
        from prism.store import Store
        self.st = Store(os.path.join(tempfile.mkdtemp(), "contract.db"))
        self.team = TEST_TEAM

    def _seed_content(self, h, title):
        import json as _j
        c = self.st._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,reasons,item_meta,payload,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (h, "뉴스", title, "G", "[]", "{}", _j.dumps({"quality_meta": {}}), time.time()))
        c.commit()


@unittest.skipUnless(os.environ.get("PRISM_TEST_SUPABASE") == "1",
                     "라이브 supabase 계약 테스트는 PRISM_TEST_SUPABASE=1 일 때만")
class TestSupabaseContract(StoreContractMixin, unittest.TestCase):
    """일회용 auth 계정 + 팀을 만들어 스코프하고 종료 시 전부 정리한다
    (contents.team_id 가 prism_teams FK · created_by 가 auth.users FK 라 실제 생성 필요)."""

    @classmethod
    def setUpClass(cls):
        import json as _j
        import secrets
        import urllib.request
        import uuid
        cls._env_added = []                               # 이 클래스가 주입한 env 만 종료 시 제거
        for k, f in (("SUPABASE_URL", "~/.prism_supabase_url"), ("SUPABASE_SERVICE_KEY", "~/.prism_supabase_key")):
            if not os.environ.get(k):
                pth = os.path.expanduser(f)
                if os.path.exists(pth):
                    os.environ[k] = open(pth, encoding="utf-8").read().strip()
                    cls._env_added.append(k)
        if not os.environ.get("SUPABASE_KEY"):
            os.environ["SUPABASE_KEY"] = os.environ.get("SUPABASE_SERVICE_KEY", "")
            cls._env_added.append("SUPABASE_KEY")
        from prism.supastore import SupabaseStore
        cls.st_cls = SupabaseStore()
        base, key = os.environ["SUPABASE_URL"].rstrip("/"), os.environ["SUPABASE_SERVICE_KEY"]
        # 실행마다 일회용 자격증명(고정 비밀번호 하드코딩 금지 · 잔존 계정의 로그인 가능성 차단)
        email = f"contract-bot-{uuid.uuid4().hex[:12]}@prism.test"
        body = _j.dumps({"email": email, "password": secrets.token_urlsafe(24),
                         "email_confirm": True}).encode()
        req = urllib.request.Request(f"{base}/auth/v1/admin/users", data=body, method="POST",
                                     headers={"apikey": key, "Authorization": f"Bearer {key}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            cls.uid = _j.loads(r.read().decode()).get("id")
        cls.team_id = cls.st_cls.ensure_team(cls.uid, "create", "계약 테스트팀(자동 정리)")

    @classmethod
    def tearDownClass(cls):
        import urllib.parse
        import urllib.request
        st, team = cls.st_cls, cls.team_id
        q = urllib.parse.quote(team or "")
        for table, col in (("contents", "team_id"), ("eval_checks", "team_id"),
                           ("golden", "team_id"), ("reports", "team_key"),
                           ("drafts", "team_key"), ("feedback_routes", "team_id")):
            try:
                st._req("DELETE", table, query=f"{col}=eq.{q}", prefer="return=minimal")
            except Exception:
                pass
        try:
            st.delete_team(team)
        except Exception:
            pass
        try:                                              # 일회용 계정 삭제
            base, key = os.environ["SUPABASE_URL"].rstrip("/"), os.environ["SUPABASE_SERVICE_KEY"]
            req = urllib.request.Request(f"{base}/auth/v1/admin/users/{cls.uid}", method="DELETE",
                                         headers={"apikey": key, "Authorization": f"Bearer {key}"})
            urllib.request.urlopen(req, timeout=20).read()
        except Exception:
            pass
        for k in getattr(cls, "_env_added", []):          # 주입 env 정리(타 테스트 모드 판정 오염 방지)
            os.environ.pop(k, None)

    def setUp(self):
        self.st = self.st_cls
        self.team = self.team_id

    def _seed_content(self, h, title):
        self.st._upsert("contents", [{"hash": h, "service": "뉴스", "title": title,
                                      "review": "yellow", "team_id": self.team}])


class TestSupastoreSystemEventNull(unittest.TestCase):
    """시스템 이벤트(learn_batch 등)는 reviewer 없이 NULL uuid 로 기록돼야 한다.
    prism_events.reviewer_id 는 uuid 컬럼(nullable) · '(system)' 문자열은 uuid 위반이라
    supabase insert 가 실패해 학습 회차가 안 쌓였다(버전 v1 고착 · 회귀 방지).
    네트워크 없이 조회/삽입 구성만 검증(__init__ 우회)."""

    def _stub(self):
        from prism.supastore import SupabaseStore
        st = SupabaseStore.__new__(SupabaseStore)
        cap = {}
        st._get = lambda table, query="": (cap.__setitem__("get_q", query) or [])   # 중복 없음
        st._req = lambda method, table, **kw: cap.__setitem__("row", (kw.get("body") or [{}])[0])
        return st, cap

    def test_null_reviewer_uses_is_null_and_null_id(self):
        st, cap = self._stub()
        self.assertTrue(st.log_event_once(None, "learn_batch", 1720000000, 0, team="t1"))
        self.assertIn("reviewer_id=is.null", cap["get_q"])       # 조회는 IS NULL
        self.assertNotIn("reviewer_id=eq.", cap["get_q"])
        self.assertIsNone(cap["row"].get("reviewer_id"))         # 삽입은 NULL(uuid 위반 회피)
        self.assertEqual(cap["row"].get("kind"), "learn_batch")

    def test_real_reviewer_uses_eq(self):
        st, cap = self._stub()
        st.log_event_once("uuid-123", "mission:x", 1720000000, 10, team="t1")
        self.assertIn("reviewer_id=eq.uuid-123", cap["get_q"])   # 실제 reviewer 는 그대로
        self.assertEqual(cap["row"].get("reviewer_id"), "uuid-123")


class TestSupastorePatchLogUuid(unittest.TestCase):
    """작업 이력(patch_log)의 reviewer_id 는 uuid 컬럼이다. '(재실행)'·'(익명)' 같은 라벨을
    그대로 넣으면 PostgREST 400 → 호출부가 예외를 삼켜 행이 통째로 사라진다.
    (2026-07-28 운영 로그: 재실행마다 400 · 이전 초안 이력 전량 유실 · prism_events 의
    '(system)' 과 같은 부류의 사고.) 네트워크 없이 삽입 구성만 검증(__init__ 우회)."""

    def _stub(self):
        from prism.supastore import SupabaseStore
        st = SupabaseStore.__new__(SupabaseStore)
        cap = {}
        st._req = lambda method, table, **kw: cap.__setitem__("row", (kw.get("body") or [{}])[0])
        st._get = lambda table, query="": (cap.__setitem__("get_q", query) or [])
        return st, cap

    def test_label_actor_is_stored_as_null_not_dropped(self):
        st, cap = self._stub()
        st.log_patch("h" * 16, "(재실행)", "rerun:solar->claude", {"model": "solar"}, {"model": "claude"})
        self.assertIsNone(cap["row"]["reviewer_id"])             # uuid 위반 회피 → 행은 살아남는다
        self.assertEqual(cap["row"]["element"], "rerun:solar->claude")   # element 는 손대지 않는다
        self.assertEqual(cap["row"]["before"], {"model": "solar"})
        st.log_patch("h" * 16, "(익명)", "category", {}, {})
        self.assertIsNone(cap["row"]["reviewer_id"])

    def test_real_uuid_is_preserved(self):
        st, cap = self._stub()
        uid = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
        st.log_patch("h" * 16, uid, "summary", {}, {})
        self.assertEqual(cap["row"]["reviewer_id"], uid)

    def test_element_prefix_stays_matchable(self):
        """content_history 가 element.startswith('rerun:') 로 이전 초안을 찾는다.
        라벨을 element 앞에 붙이는 식으로 고치면 그 이력이 화면에서 사라진다(회귀 방지)."""
        st, cap = self._stub()
        st.log_patch("h" * 16, "(재실행)", "rerun:a->b", {}, {})
        self.assertTrue(cap["row"]["element"].startswith("rerun:"))

    def test_read_path_restores_label(self):
        from prism.supastore import SupabaseStore
        st = SupabaseStore.__new__(SupabaseStore)
        st._get = lambda table, query="": [
            {"content_hash": "a" * 16, "reviewer_id": None, "element": "rerun:a->b",
             "before": {}, "after": {}, "created_at": "2026-07-28T01:00:00"},
            {"content_hash": "b" * 16, "reviewer_id": None, "element": "category",
             "before": {}, "after": {}, "created_at": "2026-07-28T01:00:00"},
            {"content_hash": "c" * 16, "reviewer_id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
             "element": "summary", "before": {}, "after": {}, "created_at": "2026-07-28T01:00:00"},
        ]
        rows = st.patch_rows()
        self.assertEqual([r["reviewer"] for r in rows],
                         ["(재실행)", "(익명)", "3f2504e0-4f89-11d3-9a0c-0305e82c3301"])

    def test_non_human_rows_excluded_from_person_counts(self):
        from prism.supastore import SupabaseStore
        st = SupabaseStore.__new__(SupabaseStore)
        st._get = lambda table, query="": [{"reviewer_id": None}, {"reviewer_id": None},
                                           {"reviewer_id": "u1"}]
        self.assertEqual(st.patch_counts(), {"u1": 1})           # 재실행이 사람 점수로 잡히지 않는다
        st2 = SupabaseStore.__new__(SupabaseStore)
        st2._get = lambda table, query="": self.fail("uuid 아닌 값으로 조회하면 안 된다")
        self.assertEqual(st2.patches_today("(익명)"), 0)


class TestSupastoreFirstSourceKept(unittest.TestCase):
    """supabase 인입 경로 라벨: PostgREST upsert 는 보낸 컬럼을 무조건 덮어쓰므로,
    sync_contents 가 쓰기 직전에 기존 source 를 읽어 되돌려 보내야 한다.
    (운영 prism_contents 400건이 전부 '재실행'으로 덮인 사고의 회귀 방지 ·
    네트워크 없이 조회 쿼리/전송 행 구성만 검증한다 · __init__ 우회.)"""

    CONTENT = {"displayServiceName": "뉴스", "title": "출처보존", "subtitle": "", "body": "본문"}
    OUT = {"quality_meta": {"finalGrade": "G", "review": "yellow"}, "item_meta": {},
           "trace": {"model": "m2", "version": 2}}

    def _stub(self, existing):
        from prism.supastore import SupabaseStore
        st = SupabaseStore.__new__(SupabaseStore)
        cap = {}
        st._get = lambda table, query="": (cap.__setitem__("get_q", query) or existing)
        st._req = lambda method, table, **kw: cap.__setitem__("rows", kw.get("body") or [])
        return st, cap

    def _hash(self):
        from prism.store import content_hash
        return content_hash(self.CONTENT)

    def test_existing_label_is_sent_back(self):
        st, cap = self._stub([{"hash": self._hash(), "source": "엑셀"}])
        st.sync_contents([(self.CONTENT, self.OUT)], source="재실행", include_all=True)
        self.assertEqual(cap["rows"][0]["source"], "엑셀")
        self.assertEqual(cap["rows"][0]["model"], "m2")          # 실행 정보는 최신으로 갱신
        self.assertIn("select=hash,source", cap["get_q"])        # 라벨만 조회(본문 미다운로드)
        self.assertIn(f"hash=in.({self._hash()})", cap["get_q"])
        self.assertNotIn("team_id", cap["get_q"])                # 충돌 키(PK)와 같은 스코프로 조회

    def test_new_row_keeps_incoming_label(self):
        st, cap = self._stub([])
        st.sync_contents([(self.CONTENT, self.OUT)], source="자동 인입", include_all=True)
        self.assertEqual(cap["rows"][0]["source"], "자동 인입")

    def test_blank_existing_label_is_backfilled(self):
        st, cap = self._stub([{"hash": self._hash(), "source": ""}])
        st.sync_contents([(self.CONTENT, self.OUT)], source="단건", include_all=True)
        self.assertEqual(cap["rows"][0]["source"], "단건")

    def test_lookup_failure_does_not_block_save(self):
        """라벨 조회가 실패해도 적재는 계속된다(보존은 최선 노력)."""
        st, cap = self._stub([])
        st._get = lambda table, query="": (_ for _ in ()).throw(RuntimeError("supabase GET 실패"))
        st.sync_contents([(self.CONTENT, self.OUT)], source="재실행", include_all=True)
        self.assertEqual(cap["rows"][0]["source"], "재실행")


if __name__ == "__main__":
    unittest.main()
