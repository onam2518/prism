"""저장 계층 감사(2026-08-04 · storage-scope) 회귀 테스트.

[3] supastore id 키 메서드(배포·라이브러리·평가런·오토파일럿)에 (id, team) 복합 필터 강제
    — RLS 는 service_role 로 우회되므로 스토어 필터가 유일한 교차 팀 방벽.
[4] 재실행 upsert 가 운영자 플래그(quality_meta.ops_hold · content_ref.source_status)를
    통째로 덮어쓰지 않고 승계(sqlite save_many/save_dedup · supabase sync_contents).
[9] 비유일 정렬키(created_at·ts)에 PK 타이브레이크 병기 — offset 페이징 중복/누락 방지.
[11] review_queue 의 feedback 조회를 후보 해시 in.() 청크로 축소(전량 페이징 제거).
[25] sqlite results(created_at) 인덱스 + review_queue payload 1회 파싱.

supabase 케이스는 네트워크 없이 조회 쿼리/전송 행 구성만 검증한다(__init__ 우회 ·
tests/test_store_contract.py 의 기존 스텁 관례와 동일).

실행: python3 -m pytest tests/test_storage_scope_audit.py -q
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _stub_store(get_impl=None, req_impl=None):
    """네트워크 없는 SupabaseStore 스텁. calls 에 (method, table, query) 를 기록한다."""
    from prism.supastore import SupabaseStore
    st = SupabaseStore.__new__(SupabaseStore)
    calls = []

    def default_get(table, query=""):
        calls.append(("GET", table, query))
        return []

    def default_req(method, table, query="", body=None, prefer=""):
        calls.append((method, table, query))
        return []

    st._get = get_impl or default_get
    st._req = req_impl or default_req
    return st, calls


class TestSupastoreTeamScopedIds(unittest.TestCase):
    """[3] id 만으로 조회·수정·삭제되던 자원에 team 복합 필터가 실리는지."""

    def _capture(self):
        st, calls = _stub_store()
        return st, calls

    def test_deploy_get_carries_team_filter(self):
        st, calls = self._capture()
        st.deploy_get(5, "team-A")
        self.assertIn("id=eq.5", calls[0][2])
        self.assertIn("team_id=eq.team-A", calls[0][2])

    def test_deploy_get_none_team_uses_is_null(self):
        # 레거시 team_id null 행은 team=None(단일 팀 모드)에서만 조회된다
        st, calls = self._capture()
        st.deploy_get(5, None)
        self.assertIn("team_id=is.null", calls[0][2])

    def test_deploy_get_cross_team_returns_none(self):
        # 서버가 (id, team) 로 거른다고 가정: B 팀 필터로는 A 팀 배포가 안 나온다
        dep = {"id": 5, "team_id": "team-A", "slug": "s", "name": "n", "version": 1, "active": True}

        def fake_get(table, query=""):
            return [dep] if "team_id=eq.team-A" in query else []

        st, _ = _stub_store(get_impl=fake_get)
        self.assertIsNone(st.deploy_get(5, "team-B"))       # 교차 팀 → 미노출
        self.assertEqual(st.deploy_get(5, "team-A")["id"], 5)  # 소유 팀 → 정상

    def test_mutations_carry_team_filter(self):
        st, calls = self._capture()
        st.deploy_save("team-A", dep_id=5, slug="s", name="n", version=1)
        st.deploy_remove(5, "team-A")
        st.lib_remove(7, "team-A")
        st.lib_pin(7, True, "team-A")
        st.eval_run_update(9, team="team-A", status="cancelled")
        st.autopilot_update(3, team="team-A", status="stopped")
        for method, table, query in calls:
            self.assertIn("team_id=eq.team-A", query,
                          msg=f"{method} {table} 쿼리에 team 필터가 없다: {query}")

    def test_eval_results_list_gated_by_run_ownership(self):
        # eval_results 엔 team_id 컬럼이 없다 → 런 소유 확인으로 팀 스코프를 강제한다
        run = {"id": 9, "team_id": "team-A", "status": "done"}
        touched = []

        def fake_get(table, query=""):
            touched.append((table, query))
            if table == "eval_runs":
                return [run] if "team_id=eq.team-A" in query else []
            if table == "eval_results":
                return [{"content_hash": "a" * 16, "title": "t", "expected": {}, "got": {},
                         "passed": False, "error": "", "rubric": None}]
            return []

        st, _ = _stub_store(get_impl=fake_get)
        self.assertEqual(st.eval_results_list(9, "team-B"), [])          # 타 팀 → 빈 목록
        self.assertNotIn("eval_results", [t for t, _q in touched])       # 결과 테이블 미조회
        rows = st.eval_results_list(9, "team-A")                         # 소유 팀 → 정상
        self.assertEqual(rows[0]["hash"], "a" * 16)
        self.assertEqual(st.eval_results_missing_rubric(9, "team-B"), [])  # 동일 게이트


class TestSupastoreFlagsKeptOnSync(unittest.TestCase):
    """[4] supabase: sync_contents(merge-duplicates)가 quality_meta 를 통째로 보내도
    기존 행의 ops_hold·source_status 를 읽어 되섞어 보낸다."""

    CONTENT = {"displayServiceName": "뉴스", "title": "플래그보존", "subtitle": "", "body": "본문"}
    OUT = {"quality_meta": {"finalGrade": "G", "review": "yellow"}, "item_meta": {},
           "trace": {"model": "m2", "version": 2}}

    def _hash(self):
        from prism.store import content_hash
        return content_hash(self.CONTENT)

    def _stub(self, existing):
        st, _ = _stub_store()
        cap = {}
        st._get = lambda table, query="": (cap.__setitem__("get_q", query) or existing)
        st._req = lambda method, table, **kw: cap.__setitem__("rows", kw["body"]["p_rows"])
        return st, cap

    def test_ops_flags_are_sent_back(self):
        sstat = {"state": "gone", "by": "u1", "ts": 1720000000.0}
        st, cap = self._stub([{"hash": self._hash(), "source": "엑셀",
                               "quality_meta": {"finalGrade": "R", "ops_hold": True,
                                                "source_status": sstat}}])
        st.sync_contents([(self.CONTENT, self.OUT)], source="재실행", include_all=True)
        row = cap["rows"][0]
        self.assertTrue(row["quality_meta"]["ops_hold"])                  # 노출제한 승계
        self.assertEqual(row["quality_meta"]["source_status"], sstat)     # 소실 신고 승계
        self.assertEqual(row["quality_meta"]["finalGrade"], "G")          # 신규 산출은 최신 유지
        self.assertEqual(row["source"], "엑셀")                           # 최초 인입 경로도 기존 유지
        # 호출자의 quality_meta 객체는 오염되지 않는다(복사 후 병합)
        self.assertNotIn("ops_hold", self.OUT["quality_meta"])

    def test_source_kept_even_without_flags(self):
        st, cap = self._stub([{"hash": self._hash(), "source": "엑셀", "quality_meta": {}}])
        st.sync_contents([(self.CONTENT, self.OUT)], source="재실행", include_all=True)
        self.assertEqual(cap["rows"][0]["source"], "엑셀")
        self.assertNotIn("ops_hold", cap["rows"][0]["quality_meta"])

    def test_new_row_passes_through(self):
        st, cap = self._stub([])                       # 기존 행 없음
        st.sync_contents([(self.CONTENT, self.OUT)], source="엑셀", include_all=True)
        self.assertEqual(cap["rows"][0]["source"], "엑셀")
        self.assertNotIn("ops_hold", cap["rows"][0]["quality_meta"])


class TestStoreFlagsKeptOnUpsert(unittest.TestCase):
    """[4] sqlite: save_many/save_dedup 의 payload=excluded.payload 전체 교체에도
    운영자 플래그가 승계된다(재실행 시나리오)."""

    CONTENT = {"displayServiceName": "뉴스", "title": "플래그", "subtitle": "", "body": "본문"}

    def _store(self):
        from prism.store import Store
        return Store(os.path.join(tempfile.mkdtemp(), "t.db"))

    def _out(self, summary="s1", grade="G"):
        return {"content_ref": dict(self.CONTENT),
                "quality_meta": {"finalGrade": grade, "review": "yellow", "reasons": []},
                "item_meta": {"summary": summary, "content_category": [], "intent": [], "entities": []},
                "trace": {"model": "m1", "version": 1}}

    def _flags_of(self, st):
        row = st.recent()[0]
        return ((row.get("quality_meta") or {}).get("ops_hold"),
                ((row.get("content_ref") or {}).get("source_status") or {}).get("state"))

    def test_save_many_keeps_flags_on_rerun(self):
        from prism.store import content_hash
        st = self._store()
        ch = content_hash(self.CONTENT)
        st.save_many([(self.CONTENT, self._out())], "run1", source="엑셀")
        self.assertTrue(st.set_ops_hold(ch, True))
        self.assertTrue(st.set_source_status(ch, "gone", "u1"))
        # 재실행: 새 산출(플래그 없는 quality_meta)로 같은 행을 upsert
        st.save_many([(self.CONTENT, self._out(summary="s2", grade="R"))], "rerun", source="재실행")
        hold, state = self._flags_of(st)
        self.assertTrue(hold)                          # 노출제한이 조용히 풀리지 않는다
        self.assertEqual(state, "gone")                # 원문 소실 신고도 보존
        row = st.recent()[0]
        self.assertEqual(row["quality_meta"]["finalGrade"], "R")   # 신규 산출은 반영

    def test_save_many_release_is_not_resurrected(self):
        # 해제(ops_hold=False)도 그대로 승계 — 임의로 True 로 살아나면 안 된다
        from prism.store import content_hash
        st = self._store()
        ch = content_hash(self.CONTENT)
        st.save_many([(self.CONTENT, self._out())], "run1")
        st.set_ops_hold(ch, True)
        st.set_ops_hold(ch, False)
        st.save_many([(self.CONTENT, self._out(summary="s2"))], "rerun")
        hold, _ = self._flags_of(st)
        self.assertFalse(hold)

    def test_save_dedup_keeps_flags_on_update(self):
        from prism.store import content_hash
        st = self._store()
        ch = content_hash(self.CONTENT)
        st.save_dedup([(self.CONTENT, self._out())], "run1", source="엑셀")
        st.set_ops_hold(ch, True)
        st.set_source_status(ch, "gone", "u1")
        r = st.save_dedup([(self.CONTENT, self._out(summary="바뀐 요약"))], "rerun", source="재실행")
        self.assertEqual(r["updated"], 1)              # 메타 변경 → update 경로
        hold, state = self._flags_of(st)
        self.assertTrue(hold)
        self.assertEqual(state, "gone")

    def test_save_dedup_skip_leaves_flags(self):
        from prism.store import content_hash
        st = self._store()
        ch = content_hash(self.CONTENT)
        st.save_dedup([(self.CONTENT, self._out())], "run1")
        st.set_ops_hold(ch, True)
        r = st.save_dedup([(self.CONTENT, self._out())], "rerun")   # 결과 무변경 → skip
        self.assertEqual(r["skipped"], 1)
        hold, _ = self._flags_of(st)
        self.assertTrue(hold)


class TestSupastoreOrderTiebreak(unittest.TestCase):
    """[9] 비유일 정렬키(created_at·ts)에 PK 타이브레이크 병기 — 벌크 INSERT(트랜잭션 now())
    동률 행의 offset 페이징 중복/누락 방지(contents_by_hash 와 동일 수법)."""

    def test_order_keys_have_unique_tiebreak(self):
        st, calls = _stub_store()
        st.recent(limit=100, team="T")
        st.assignees(team="T")
        st.assignments_snapshot(team="T")
        st.patch_rows(limit=100, team="T")
        st.draft_times(team="T")
        got = {t: q for _m, t, q in calls}
        self.assertIn("order=created_at.desc,hash", got["contents"])
        self.assertIn("order=ts,content_hash,reviewer_id", got["assignments"])
        self.assertIn("order=created_at.desc,id", got["patch_log"])
        self.assertIn("order=created_at.desc,content_hash,model,version", got["drafts"])

    def test_assignments_snapshot_tiebreak(self):
        st, calls = _stub_store()
        st.assignments_snapshot(team="T")
        self.assertIn("order=ts,content_hash,reviewer_id", calls[0][2])


class TestSupastoreReviewQueueFeedbackScope(unittest.TestCase):
    """[11] review_queue 의 feedback 조회가 전량(무제한 페이징)이 아니라
    후보(yellow) 해시 in.() 청크로 제한되는지 + 판정 반영 동작 불변."""

    def _rows(self):
        return [{"hash": "a" * 16, "service": "뉴스", "title": "A", "body": "", "source_url": "",
                 "final_grade": "", "item_meta": {}, "quality_meta": {"review": "yellow"},
                 "review": "yellow", "model": "m", "created_at": "2026-08-01T00:00:00"},
                {"hash": "b" * 16, "service": "뉴스", "title": "B", "body": "", "source_url": "",
                 "final_grade": "", "item_meta": {}, "quality_meta": {"review": "yellow"},
                 "review": "yellow", "model": "m", "created_at": "2026-08-02T00:00:00"}]

    def _stub(self, fb_rows):
        cap = {"fb_q": []}

        def fake_get(table, query=""):
            if table == "contents":
                return self._rows()
            if table == "feedback":
                cap["fb_q"].append(query)
                return fb_rows
            return []                                  # assignments 등

        st, _ = _stub_store(get_impl=fake_get)
        return st, cap

    def test_feedback_query_scoped_to_candidate_hashes(self):
        st, cap = self._stub([])
        st.review_queue(limit=100, team="T")
        self.assertEqual(len(cap["fb_q"]), 1)          # 후보 32건 이하 → 청크 1개
        q = cap["fb_q"][0]
        self.assertIn("content_hash=in.(", q)          # 전량 조회가 아니다
        self.assertIn("a" * 16, q)
        self.assertIn("b" * 16, q)
        self.assertIn("team_id=eq.T", q)               # 팀 스코프 유지

    def test_reviewed_rows_still_filtered(self):
        fb = [{"content_hash": "a" * 16, "verdict": "good", "reviewer_id": "r1"}]
        st, _cap = self._stub(fb)
        out = st.review_queue(limit=100, team="T")
        self.assertEqual([r["hash"] for r in out], ["b" * 16])   # 판정 완료건 제외(동작 불변)

    def test_no_candidates_skips_feedback_fetch(self):
        cap = {"fb": 0}

        def fake_get(table, query=""):
            if table == "feedback":
                cap["fb"] += 1
            return []

        st, _ = _stub_store(get_impl=fake_get)
        self.assertEqual(st.review_queue(limit=100, team="T"), [])
        self.assertEqual(cap["fb"], 0)                 # 후보 0건 → feedback 왕복 자체가 없다


class TestStoreCreatedIndex(unittest.TestCase):
    """[25] results(created_at) 인덱스 · review_queue payload 1회 파싱 동작 불변."""

    def _store(self):
        from prism.store import Store
        return Store(os.path.join(tempfile.mkdtemp(), "t.db"))

    def test_created_at_index_exists(self):
        st = self._store()
        names = [r[1] for r in st._conn().execute("PRAGMA index_list(results)")]
        self.assertIn("ix_results_created", names)

    def test_index_applies_to_existing_db(self):
        # 구 스키마 DB(인덱스 없음)를 다시 열면 _init 이 자연 적용한다
        from prism.store import Store
        path = os.path.join(tempfile.mkdtemp(), "t.db")
        st = Store(path)
        st._conn().execute("DROP INDEX ix_results_created")
        st._conn().commit()
        st2 = Store(path)
        names = [r[1] for r in st2._conn().execute("PRAGMA index_list(results)")]
        self.assertIn("ix_results_created", names)

    def test_review_queue_model_from_single_parse(self):
        st = self._store()
        content = {"displayServiceName": "뉴스", "title": "큐", "subtitle": "", "body": "본문"}
        out = {"content_ref": dict(content),
               "quality_meta": {"finalGrade": "", "review": "yellow", "reasons": [], "confidence": 0.4},
               "item_meta": {"summary": "s"}, "trace": {"model": "mX", "version": 1}}
        st.save_many([(content, out)], "run1")
        q = st.review_queue()
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["model"], "mX")          # trace 추출 동작 불변(1회 파싱)
        self.assertEqual(q[0]["confidence"], 0.4)


if __name__ == "__main__":
    unittest.main()
