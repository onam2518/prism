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
        import urllib.request
        for k, f in (("SUPABASE_URL", "~/.prism_supabase_url"), ("SUPABASE_SERVICE_KEY", "~/.prism_supabase_key")):
            if not os.environ.get(k):
                pth = os.path.expanduser(f)
                if os.path.exists(pth):
                    os.environ[k] = open(pth, encoding="utf-8").read().strip()
        os.environ.setdefault("SUPABASE_KEY", os.environ.get("SUPABASE_SERVICE_KEY", ""))
        from prism.supastore import SupabaseStore
        cls.st_cls = SupabaseStore()
        base, key = os.environ["SUPABASE_URL"].rstrip("/"), os.environ["SUPABASE_SERVICE_KEY"]
        body = _j.dumps({"email": "contract-bot@prism.test", "password": "contract-test-pw-1",
                         "email_confirm": True}).encode()
        req = urllib.request.Request(f"{base}/auth/v1/admin/users", data=body, method="POST",
                                     headers={"apikey": key, "Authorization": f"Bearer {key}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                cls.uid = _j.loads(r.read().decode()).get("id")
        except Exception:                                 # 이미 존재 → 조회로 획득
            q = urllib.request.Request(f"{base}/auth/v1/admin/users?page=1&per_page=100",
                                       headers={"apikey": key, "Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(q, timeout=20) as r:
                users = _j.loads(r.read().decode()).get("users") or []
            cls.uid = next(u["id"] for u in users if u.get("email") == "contract-bot@prism.test")
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

    def setUp(self):
        self.st = self.st_cls
        self.team = self.team_id

    def _seed_content(self, h, title):
        self.st._upsert("contents", [{"hash": h, "service": "뉴스", "title": title,
                                      "review": "yellow", "team_id": self.team}])


if __name__ == "__main__":
    unittest.main()
