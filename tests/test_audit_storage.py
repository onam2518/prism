"""저장 계층 감사(2026-08 · storage) 회귀 테스트 S1~S12.

버그
  [S8]  ent_trending 이 team falsy 여도 team=eq.'' 필터를 걸어 운영에서 항상 0건.
  [S11] sqlite contents_by_hash 가 subtitle 을 빈 문자열로 고정(학습데이터에서 부제 소실).
  [S12] supastore 삭제·상태변경 계열이 매칭 행 유무와 무관하게 항상 True.
성능(왕복 제거)
  [S1] 엔티티 사전 적재 N+1(개체당 2~4왕복) → 별칭 일괄 조회 · 일괄 등록 · 일괄 링크.
  [S2] arena_stats 가 assignments 를 2~3회 전량 다운로드 → 1회 조회 후 재사용.
  [S3] 큐 '개수' 하나 때문에 본문 400행 + 배정 전량 → review_queue_count.
  [S4] patch_counts·event_bonus·golden_contrib_counts 를 서버측 집계 RPC 로(폴백 유지).
  [S5] golden_count 가 골든 전량을 받아 len() → Content-Range 카운트(_count).
  [S6] draft_times 가 초안 이력 전량 → hashes= 로 좁힘(값 파리티 필수).
  [S7] ent_purge_unlisted 3N왕복 · ent_mark_unlisted N왕복 → 100개 청크 in.().
  [S9] sqlite patch_log·feedback 인덱스 부재(SCAN + TEMP B-TREE).
  [S10] register_golden 이 행마다 커밋 → executemany + 단일 커밋.

supabase 케이스는 네트워크 없이 조회 쿼리·전송 행·왕복 수만 검증한다
(__init__ 우회 · tests/test_store_contract.py 의 기존 스텁 관례와 동일).

실행: python3 -m pytest tests/test_audit_storage.py -q
"""
import json as _j
import os
import sys
import tempfile
import time as _t
import unicodedata
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── 공통 헬퍼 ────────────────────────────────────────────────────────────────
def _sqlite_store():
    from prism.store import Store
    return Store(os.path.join(tempfile.mkdtemp(), "t.db"))


def _stub_supa(get_impl=None, req_impl=None):
    """네트워크 없는 SupabaseStore 스텁. calls 에 (method, table, query, prefer) 기록."""
    from prism.supastore import SupabaseStore
    st = SupabaseStore.__new__(SupabaseStore)
    calls = []

    def default_get(table, query=""):
        calls.append(("GET", table, query, ""))
        return []

    def default_req(method, table, query="", body=None, prefer=""):
        calls.append((method, table, query, prefer))
        return []

    st._get = get_impl or default_get
    st._req = req_impl or default_req
    st._rpc_or_none = lambda fn, args: None          # 집계 RPC 미적용 환경(폴백 경로)
    return st, calls


def _put_yellow(st, h, conf=0.5, ts=None, subtitle=""):
    """YELLOW 콘텐츠 1건 삽입(검수 큐 대상)."""
    payload = {"quality_meta": {"review": "yellow", "confidence": conf},
               "content_ref": {"title": h, "subtitle": subtitle, "body": "b"}}
    c = st._conn()
    c.execute("INSERT INTO results(content_hash,service,title,final_grade,payload,created_at) "
              "VALUES(?,?,?,?,?,?)", (h, "s", h, "G", _j.dumps(payload), ts or _t.time()))
    c.commit()


class _CountingConn:
    """sqlite 커넥션 프록시 · commit 횟수를 센다([S10] 단일 트랜잭션 확인)."""

    def __init__(self, conn):
        self._c = conn
        self.commits = 0

    def commit(self):
        self.commits += 1
        return self._c.commit()

    def __getattr__(self, name):
        return getattr(self._c, name)


# ── [S1] 엔티티 사전 적재 벌크화 ─────────────────────────────────────────────
def _ingest_legacy(store, items, team=""):
    """수정 전 건별 루프(파리티 기준선) · 반환 계약이 같은지 비교하는 데만 쓴다."""
    from prism import entdict as ED
    created = linked = 0
    new_ids = []
    for ch, ents in items:
        seen = set()
        for surface in (ents or []):
            norm = ED.normalize_name(surface)
            if norm in seen or not ED.eligible(norm):
                continue
            seen.add(norm)
            eid = store.ent_id_by_alias(norm)
            if not eid:
                e = ED._empty_entry(norm)
                eid = e["entity_id"]
                store.ent_upsert(e)
                store.ent_alias_add(norm, eid)
                created += 1
                new_ids.append(eid)
            store.ent_link(ch, eid, surface, team=team)
            linked += 1
    return {"created": created, "linked": linked, "new_ids": new_ids}


def _dump_dict(st):
    c = st._conn()
    return ({r[0] for r in c.execute("SELECT entity_id FROM entities")},
            {(r[0], r[1]) for r in c.execute("SELECT alias,entity_id FROM entity_aliases")},
            {(r[0], r[1], r[2], r[3]) for r in
             c.execute("SELECT content_hash,entity_id,surface,team FROM content_entities")})


class TestS1BulkIngest(unittest.TestCase):
    ITEMS = [(f"{i:016x}", [f"개체{i}_{j}" for j in range(6)]) for i in range(20)]

    def test_return_and_rows_identical_to_per_item_loop(self):
        """created·linked·new_ids 와 실제 적재 결과가 건별 루프와 완전히 같아야 한다."""
        from prism import entdict as ED
        a, b = _sqlite_store(), _sqlite_store()
        r_new = ED.ingest_meta(a, self.ITEMS, team="T")
        r_old = _ingest_legacy(b, self.ITEMS, team="T")
        self.assertEqual(r_new["created"], r_old["created"])
        self.assertEqual(r_new["linked"], r_old["linked"])
        self.assertEqual(r_new["new_ids"], r_old["new_ids"])      # 순서까지 동일(후속 보강 대상)
        self.assertEqual(_dump_dict(a), _dump_dict(b))

    def test_repeat_ingest_is_idempotent(self):
        from prism import entdict as ED
        st = _sqlite_store()
        r1 = ED.ingest_meta(st, self.ITEMS, team="T")
        snap = _dump_dict(st)
        r2 = ED.ingest_meta(st, self.ITEMS, team="T")
        self.assertEqual(r1["created"], 120)
        self.assertEqual(r2["created"], 0)                        # 재적재는 신규 0
        self.assertEqual(r2["linked"], r1["linked"])              # 링크 수는 동일(멱등)
        self.assertEqual(_dump_dict(st), snap)

    def test_shared_entity_across_contents_created_once(self):
        from prism import entdict as ED
        st = _sqlite_store()
        r = ED.ingest_meta(st, [("a" * 16, ["안세영", "배드민턴"]),
                                ("b" * 16, ["안세영", "올림픽"])], team="T")
        self.assertEqual(r["created"], 3)                         # 안세영은 1회만 등록
        self.assertEqual(r["linked"], 4)
        self.assertEqual(len(r["new_ids"]), 3)

    def test_surface_and_team_preserved_on_links(self):
        """링크에는 정규화 이름이 아니라 원표기(surface)와 team 이 그대로 실린다."""
        from prism import entdict as ED
        st = _sqlite_store()
        ED.ingest_meta(st, [("c" * 16, ["  안세영  "])], team="team-uuid-123")
        rows = list(st._conn().execute("SELECT surface, team FROM content_entities"))
        self.assertEqual(rows[0][0], "  안세영  ")
        self.assertEqual(rows[0][1], "team-uuid-123")

    def test_nfd_alias_absorbed_without_creating_duplicate(self):
        """[T5 보존] NFC 조회가 미스하면 NFD 로 한 번 더 찾고, 맞으면 NFC 별칭을 덧붙인다.
        일괄 조회로 바꿔도 이 흡수가 계속 동작해야 한다(같은 개체가 둘로 갈라지면 안 됨)."""
        from prism import entdict as ED
        st = _sqlite_store()
        nfc = unicodedata.normalize("NFC", "안세영")
        nfd = unicodedata.normalize("NFD", nfc)
        self.assertNotEqual(nfc, nfd)                             # 전제: 두 표현이 다르다
        st.ent_upsert({"entity_id": "e_legacy", "name": nfd, "status": "pending"})
        st.ent_alias_add(nfd, "e_legacy")                         # 구 데이터 = NFD 별칭만 존재
        r = ED.ingest_meta(st, [("d" * 16, [nfc])], team="T")
        self.assertEqual(r["created"], 0)                         # 신규 개체를 만들지 않는다
        self.assertEqual(st.ent_id_by_alias(nfc), "e_legacy")     # NFC 별칭이 덧붙었다
        self.assertEqual(st.ent_id_by_alias(nfd), "e_legacy")     # 기존 별칭은 재바인딩 없음
        self.assertEqual(
            [r[0] for r in st._conn().execute("SELECT entity_id FROM content_entities")], ["e_legacy"])

    def test_ineligible_names_still_skipped(self):
        from prism import entdict as ED
        st = _sqlite_store()
        r = ED.ingest_meta(st, [("e" * 16, ["A씨", "3억원", "네티즌", "안세영"])], team="T")
        self.assertEqual(r["created"], 1)
        self.assertEqual(r["linked"], 1)

    def test_roundtrips_scale_with_chunks_not_entities(self):
        """개체 120건: 수정 전 480왕복(별칭 GET 120 + 개체 POST 120 + 별칭 POST 120 + 링크 POST 120).
        일괄화 후에는 청크 수 비례여야 한다."""
        st, calls = _stub_supa()
        from prism import entdict as ED
        r = ED.ingest_meta(st, self.ITEMS, team="T")
        self.assertEqual(r["created"], 120)
        self.assertEqual(r["linked"], 120)
        self.assertLessEqual(len(calls), 10, f"왕복 과다: {[c[:3] for c in calls]}")
        # 테이블별로도 개체 수에 비례하지 않는다
        for table in ("entities", "entity_aliases", "content_entities"):
            n = sum(1 for c in calls if c[1] == table)
            self.assertLess(n, 10, f"{table} {n}왕복")

    def test_bulk_link_keeps_ignore_duplicates(self):
        """링크 일괄 삽입은 ignore-duplicates 여야 한다(merge-duplicates 면 같은 배치 안의
        중복 PK 에서 PostgREST 가 400 을 낸다 · 기존 ent_link 와 같은 의미)."""
        st, calls = _stub_supa()
        st.ent_link_many([("a" * 16, "e_1", "표기"), ("a" * 16, "e_1", "다른표기")], team="T")
        posts = [c for c in calls if c[0] == "POST" and c[1] == "content_entities"]
        self.assertEqual(len(posts), 1)
        self.assertIn("ignore-duplicates", posts[0][3])

    def test_bulk_helpers_match_single_row_api(self):
        """벌크 4종이 sqlite 단건 API 와 같은 행을 남긴다(계약 동형)."""
        a, b = _sqlite_store(), _sqlite_store()
        e = {"entity_id": "e_x", "name": "n", "status": "pending"}
        a.ent_upsert_many([e]); a.ent_alias_add_many([("n", "e_x")]); a.ent_link_many([("h" * 16, "e_x", "s")], team="T")
        b.ent_upsert(e); b.ent_alias_add("n", "e_x"); b.ent_link("h" * 16, "e_x", "s", team="T")
        self.assertEqual(_dump_dict(a), _dump_dict(b))
        self.assertEqual(a.ent_ids_by_aliases(["n", "없음"]), {"n": "e_x"})


# ── [S2] 아레나 재계산의 배정 재조회 제거 ────────────────────────────────────
class TestS2AssignmentsQueriedOnce(unittest.TestCase):
    def test_supastore_arena_downloads_assignments_once(self):
        st, calls = _stub_supa()
        st.arena_stats(team="T")
        n = sum(1 for c in calls if c[1] == "assignments")
        self.assertEqual(n, 1, f"배정 조회 {n}회: {[c[2] for c in calls if c[1] == 'assignments']}")

    def test_sqlite_arena_calls_assignees_once(self):
        st = _sqlite_store()
        _put_yellow(st, "a" * 16)
        st.set_assignees("a" * 16, ["A"], team="T")
        real, n = st.assignees, [0]

        def counted(team=None, hashes=None):
            n[0] += 1
            return real(team, hashes)

        st.assignees = counted
        st.arena_stats(team="T")
        self.assertEqual(n[0], 1)

    def test_review_targets_value_unchanged_with_assigned_arg(self):
        """assigned= 를 주든 안 주든 모집단은 같아야 한다(분모 불변)."""
        st = _sqlite_store()
        _put_yellow(st, "a" * 16)
        _put_yellow(st, "b" * 16)
        st.set_assignees("b" * 16, ["A"], team="T")
        st.set_assignees("c" * 16, ["A"], team="T")          # 고아 배정(콘텐츠 없음)
        self.assertEqual(st.review_targets("T"),
                         st.review_targets("T", assigned=set(st.assignees("T"))))


# ── [S3] 큐 개수 전용 경로 ───────────────────────────────────────────────────
class TestS3QueueCount(unittest.TestCase):
    def _fixture(self):
        st = _sqlite_store()
        for i in range(5):
            _put_yellow(st, f"{i:016x}")
        st.set_assignees(f"{1:016x}", ["A"], team="T")
        st.save_feedback(f"{2:016x}", "s", "t", "good", "analyze", "", _t.time(), reviewer="A", team="T")
        return st

    def test_sqlite_count_equals_queue_length(self):
        st = self._fixture()
        for kw in ({"team": "T"}, {"team": "T", "reviewer": "A"},
                   {"team": "T", "reviewer": "A", "see_all": True},
                   {"team": "T", "only_unreviewed": False}):
            self.assertEqual(st.review_queue_count(**kw), len(st.review_queue(**kw)), kw)

    def test_supastore_count_equals_queue_length_and_skips_body(self):
        rows = [{"hash": f"{i:016x}", "service": "s", "title": "t", "body": "본문" * 500,
                 "source_url": "", "final_grade": "A", "item_meta": {}, "quality_meta": {},
                 "review": "yellow", "model": "m", "created_at": "2026-08-01T00:00:00"}
                for i in range(7)]
        seen = []

        def fake_get(table, query=""):
            seen.append((table, query))
            if table == "contents":
                cols = query.split("select=", 1)[1].split("&")[0].split(",")
                return [{k: r[k] for k in cols if k in r} for r in rows]
            return []

        st, _ = _stub_supa(get_impl=fake_get)
        self.assertEqual(st.review_queue_count(team="T"), len(st.review_queue(team="T")))
        cnt_q = [q for t, q in seen if t == "contents"][0]        # 카운트 경로가 먼저 돈다
        self.assertNotIn("body", cnt_q)                           # 본문·메타는 받지 않는다
        self.assertNotIn("item_meta", cnt_q)

    def test_supastore_count_respects_limit_cap(self):
        rows = [{"hash": f"{i:016x}", "review": "yellow", "created_at": "2026-08-01T00:00:00"}
                for i in range(400)]
        st, _ = _stub_supa(get_impl=lambda t, q="": (rows if t == "contents" else []))
        self.assertEqual(st.review_queue_count(limit=100, team="T"), 100)   # len(queue[:100]) 과 동일

    def test_queue_only_fetches_assignments_for_candidates(self):
        """배정 조회가 후보 해시 in.() 로 좁혀진다(전량 다운로드 제거)."""
        rows = [{"hash": f"{i:016x}", "service": "", "title": "", "body": "", "source_url": "",
                 "final_grade": "", "item_meta": {}, "quality_meta": {}, "review": "yellow",
                 "model": "", "created_at": "2026-08-01T00:00:00"} for i in range(3)]
        seen = []

        def fake_get(table, query=""):
            seen.append((table, query))
            return rows if table == "contents" else []

        st, _ = _stub_supa(get_impl=fake_get)
        st.review_queue(team="T")
        aq = [q for t, q in seen if t == "assignments"]
        self.assertTrue(aq and all("content_hash=in.(" in q for q in aq), aq)


# ── [S4] 카운트 집계의 서버측 RPC 이관(폴백 유지) ────────────────────────────
class TestS4AggregateRpc(unittest.TestCase):
    def _rows(self, table):
        if table == "patch_log":
            return [{"reviewer_id": "u1"}, {"reviewer_id": "u1"}, {"reviewer_id": None}]
        if table == "events":
            now = _t.strftime("%Y-%m-%dT%H:%M:%S", _t.gmtime())
            old = _t.strftime("%Y-%m-%dT%H:%M:%S", _t.gmtime(_t.time() - 30 * 86400))
            return [{"reviewer_id": "u1", "bonus": 10, "created_at": now},
                    {"reviewer_id": "u1", "bonus": 5, "created_at": old}]
        return []

    def test_fallback_when_rpc_missing(self):
        st, _ = _stub_supa(get_impl=lambda t, q="": self._rows(t))
        self.assertEqual(st.patch_counts("T"), {"u1": 2})         # 사람 아닌 행위자는 제외
        self.assertEqual(st.event_bonus("T"), {"u1": {"total": 15, "week": 10}})
        self.assertEqual(st.golden_contrib_counts("T"), {"u1": 2})

    def test_rpc_result_used_and_rows_not_downloaded(self):
        used = []

        def boom(table, query=""):
            used.append(table)
            return []

        st, _ = _stub_supa(get_impl=boom)
        st._rpc_or_none = lambda fn, args: ({"u1": 2} if fn != "prism_agg_event_bonus"
                                            else {"u1": {"total": 15, "week": 10}})
        self.assertEqual(st.patch_counts("T"), {"u1": 2})
        self.assertEqual(st.event_bonus("T"), {"u1": {"total": 15, "week": 10}})
        self.assertEqual(st.golden_contrib_counts("T"), {"u1": 2})
        self.assertEqual(used, [], f"RPC 적용 환경에서 행을 내려받았다: {used}")

    def test_rpc_and_fallback_agree(self):
        st, _ = _stub_supa(get_impl=lambda t, q="": self._rows(t))
        fallback = (st.patch_counts("T"), st.event_bonus("T"), st.golden_contrib_counts("T"))
        st._rpc_or_none = lambda fn, args: {"prism_agg_patch_counts": {"u1": 2},
                                            "prism_agg_event_bonus": {"u1": {"total": 15, "week": 10}},
                                            "prism_agg_golden_contrib": {"u1": 2}}[fn]
        self.assertEqual((st.patch_counts("T"), st.event_bonus("T"), st.golden_contrib_counts("T")),
                         fallback)


# ── [S5] 골든 개수 = Content-Range 카운트 ────────────────────────────────────
class TestS5GoldenCount(unittest.TestCase):
    def test_count_uses_content_range_not_row_download(self):
        from prism.supastore import SupabaseStore
        st = SupabaseStore.__new__(SupabaseStore)
        st.url, st.key, st.base = "https://x", "k", "https://x/rest/v1"
        seen = []

        def fake_http(method, path, data, headers):
            seen.append((method, path, headers.get("Range")))
            return 206, "[]", {"Content-Range": "0-0/3000"}

        st._http = fake_http
        st._get = lambda t, q="": self.fail("행을 내려받으면 안 된다")
        self.assertEqual(st.golden_count("T"), 3000)
        self.assertEqual(len(seen), 1)                            # 1왕복
        self.assertEqual(seen[0][2], "0-0")                       # 행 0
        self.assertIn("team_id=eq.T", seen[0][1])                 # 팀 스코프 유지

    def test_falls_back_to_row_count_on_error(self):
        from prism.supastore import SupabaseStore
        st = SupabaseStore.__new__(SupabaseStore)
        st.url, st.key, st.base = "https://x", "k", "https://x/rest/v1"
        st._http = lambda *a, **k: (500, "err", {})
        st._get = lambda t, q="": [{"id": 1}, {"id": 2}]
        self.assertEqual(st.golden_count("T"), 2)

    def test_sqlite_contract_unchanged(self):
        st = _sqlite_store()
        st.register_golden(None, [{"content": {"title": "a"}, "expected": {"finalGrade": "G"}}])
        self.assertEqual(st.golden_count(None), 1)


# ── [S6] draft_times 값 파리티(퀘스트 보상 기준) ─────────────────────────────
class TestS6DraftTimesParity(unittest.TestCase):
    def _fixture(self):
        st = _sqlite_store()
        for i in range(6):
            for v in (1, 2):
                st.save_draft(f"{i:016x}", "m", v, {}, {}, team="T")
                _t.sleep(0.001)
        st.save_draft("f" * 16, "m", 1, {}, {}, team="OTHER")
        return st

    def test_narrowed_equals_full_subset(self):
        st = self._fixture()
        full = st.draft_times("T")
        subset = {f"{i:016x}" for i in (0, 2, 5)}
        self.assertEqual(st.draft_times("T", hashes=subset),
                         {k: v for k, v in full.items() if k in subset})

    def test_latest_draft_wins(self):
        st = self._fixture()
        h = f"{0:016x}"
        rows = [r[0] for r in st._conn().execute(
            "SELECT ts FROM drafts WHERE content_hash=? AND team=?", (h, "T"))]
        self.assertEqual(st.draft_times("T", hashes=[h])[h], max(rows))

    def test_empty_and_none_hashes(self):
        st = self._fixture()
        self.assertEqual(st.draft_times("T", hashes=[]), {})
        self.assertEqual(st.draft_times("T", hashes=None), st.draft_times("T"))

    def test_team_scope_kept(self):
        st = self._fixture()
        self.assertNotIn("f" * 16, st.draft_times("T", hashes=["f" * 16]))

    def test_supastore_narrow_uses_in_filter_and_matches_full(self):
        drafts = [{"content_hash": f"{i:016x}", "created_at": f"2026-08-0{i+1}T00:00:00"}
                  for i in range(5)]
        seen = []

        def fake_get(table, query=""):
            seen.append(query)
            if "content_hash=in.(" in query:
                ids = query.split("content_hash=in.(", 1)[1].split(")")[0].split(",")
                return [r for r in drafts if r["content_hash"] in ids]
            return drafts

        st, _ = _stub_supa(get_impl=fake_get)
        full = st.draft_times("T")
        want = {f"{i:016x}" for i in (1, 3)}
        got = st.draft_times("T", hashes=want)
        self.assertEqual(got, {k: v for k, v in full.items() if k in want})
        self.assertTrue(any("content_hash=in.(" in q for q in seen))

    def test_reviewops_helper_falls_back_for_old_stores(self):
        from prism import reviewops as RO

        class Old:                                            # hashes 인자를 모르는 스토어
            def draft_times(self, team=None):
                return {"a": 1.0}

        self.assertEqual(RO._draft_times(Old(), "T", {"a"}), {"a": 1.0})

    def test_quest_progress_value_unchanged(self):
        """퀘스트 진척(유효 검수 판정)이 좁힌 조회로 바뀌어도 같은 값이어야 한다."""
        from prism import reviewops as RO
        st = _sqlite_store()
        now = _t.time()
        _put_yellow(st, "a" * 16)
        _put_yellow(st, "b" * 16)
        st.save_draft("a" * 16, "m", 1, {}, {}, team="T")
        st.save_feedback("a" * 16, "s", "t", "good", "analyze", "", now + 10, reviewer="A", team="T")
        st.save_feedback("b" * 16, "s", "t", "bad", "analyze", "", now - 10, reviewer="A", team="T")
        fm = st.feedback_map(team="T")
        targets = st.review_targets("T")
        narrowed = RO._draft_times(st, "T", set(fm) & set(targets))
        self.assertEqual(narrowed, {k: v for k, v in st.draft_times("T").items() if k in fm})


# ── [S7] 미등재 정리·이행 청크화 ─────────────────────────────────────────────
class TestS7EntityPurgeChunked(unittest.TestCase):
    def test_supastore_purge_uses_in_chunks(self):
        ents = [{"entity_id": f"e_{i:04d}"} for i in range(250)]
        st, calls = _stub_supa(get_impl=lambda t, q="": (ents if t == "entities" else []))
        self.assertEqual(st.ent_purge_unlisted(), 250)
        dels = [c for c in calls if c[0] == "DELETE"]
        self.assertEqual(len(dels), 9, [c[1] for c in dels])       # 3테이블 × 청크 3(=250/100)
        self.assertTrue(all("entity_id=in.(" in c[2] for c in dels))
        # 자식(링크·별칭)을 먼저 지운다(ent_delete 와 같은 순서)
        self.assertEqual([c[1] for c in dels[:3]],
                         ["content_entities", "entity_aliases", "entities"])

    def test_supastore_mark_uses_single_patch_per_chunk(self):
        ents = [{"entity_id": f"e_{i:04d}", "attr_meta": {"_enrich": {"result": "miss"}}}
                for i in range(150)] + [{"entity_id": "e_keep", "attr_meta": {}}]
        st, calls = _stub_supa(get_impl=lambda t, q="": (ents if t == "entities" else []))
        self.assertEqual(st.ent_mark_unlisted(), 150)              # 보강 미스만 대상
        patches = [c for c in calls if c[0] == "PATCH"]
        self.assertEqual(len(patches), 2)
        self.assertTrue(all("entity_id=in.(" in c[2] for c in patches))

    def test_sqlite_purge_removes_children_and_counts(self):
        st = _sqlite_store()
        st.ent_upsert({"entity_id": "e_1", "name": "a", "status": "unlisted"})
        st.ent_upsert({"entity_id": "e_2", "name": "b", "status": "pending"})
        st.ent_alias_add("a", "e_1"); st.ent_alias_add("b", "e_2")
        st.ent_link("h" * 16, "e_1", "a"); st.ent_link("h" * 16, "e_2", "b")
        self.assertEqual(st.ent_purge_unlisted(), 1)
        ents, aliases, links = _dump_dict(st)
        self.assertEqual(ents, {"e_2"})
        self.assertEqual({a for a, _ in aliases}, {"b"})
        self.assertEqual({e for _, e, _, _ in links}, {"e_2"})

    def test_sqlite_purge_noop_when_nothing_unlisted(self):
        st = _sqlite_store()
        st.ent_upsert({"entity_id": "e_1", "name": "a", "status": "pending"})
        self.assertEqual(st.ent_purge_unlisted(), 0)
        self.assertEqual(_dump_dict(st)[0], {"e_1"})


# ── [S8] ent_trending 의 team falsy 규칙 ─────────────────────────────────────
class TestS8TrendingTeamFilter(unittest.TestCase):
    def test_supastore_no_team_filter_when_falsy(self):
        seen = []
        st, _ = _stub_supa(get_impl=lambda t, q="": (seen.append(q) or []))
        st.ent_trending(hours=48, limit=8, team="")
        self.assertNotIn("team=eq.", seen[0])                      # 빈 문자열 일치 필터 금지
        seen.clear()
        st.ent_trending(hours=48, limit=8, team=None)
        self.assertNotIn("team=eq.", seen[0])
        seen.clear()
        st.ent_trending(hours=48, limit=8, team="team-uuid")
        self.assertIn("team=eq.team-uuid", seen[0])                # 팀 지정 시엔 그대로 건다

    def test_sqlite_global_query_sees_team_links(self):
        """운영은 링크를 팀 uuid 로 저장한다 — 전역 조회(team='')가 0건이면 안 된다."""
        st = _sqlite_store()
        st.ent_upsert({"entity_id": "e_1", "name": "안세영", "status": "pending"})
        for h in ("a" * 16, "b" * 16, "c" * 16):
            st.ent_link(h, "e_1", "안세영", team="team-uuid-123")
        self.assertEqual([t["id"] for t in st.ent_trending(hours=48, team="")], ["e_1"])
        self.assertEqual([t["id"] for t in st.ent_trending(hours=48, team=None)], ["e_1"])
        self.assertEqual([t["id"] for t in st.ent_trending(hours=48, team="team-uuid-123")], ["e_1"])
        self.assertEqual(st.ent_trending(hours=48, team="다른팀"), [])   # 팀 지정은 그대로 좁힌다

    def test_topicops_trending_call_shape(self):
        """유일한 호출부(topicops)는 team='' 로 전역 조회를 요청한다 — 계약 확인."""
        import inspect
        from prism import topicops
        src = inspect.getsource(topicops)
        self.assertIn('ent_trending(hours=48, limit=8, team="")', src)


# ── [S9] sqlite 인덱스 ───────────────────────────────────────────────────────
class TestS9Indexes(unittest.TestCase):
    def test_indexes_exist(self):
        st = _sqlite_store()
        names = {r[0] for r in st._conn().execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertTrue({"ix_patch_hash", "ix_patch_reviewer", "ix_feedback_reviewer"} <= names)

    def test_single_content_history_uses_index(self):
        st = _sqlite_store()
        for i in range(50):
            st.log_patch(f"{i % 5:016x}", "A", "summary", {}, {})
        plan = " ".join(str(r) for r in st._conn().execute(
            "EXPLAIN QUERY PLAN SELECT content_hash,reviewer,element,before,after,ts FROM patch_log "
            "WHERE content_hash=? ORDER BY ts DESC LIMIT 100", ("0" * 16,)))
        self.assertIn("ix_patch_hash", plan)
        self.assertNotIn("SCAN patch_log", plan)

    def test_reviewer_lookups_use_index(self):
        st = _sqlite_store()
        st.log_patch("a" * 16, "A", "summary", {}, {})
        st.save_feedback("a" * 16, "s", "t", "good", "analyze", "", _t.time(), reviewer="A")
        p = " ".join(str(r) for r in st._conn().execute(
            "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM patch_log WHERE reviewer=? AND ts>=?", ("A", 0)))
        f = " ".join(str(r) for r in st._conn().execute(
            "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM feedback WHERE reviewer=? AND ts>=?", ("A", 0)))
        self.assertIn("ix_patch_reviewer", p)
        self.assertIn("ix_feedback_reviewer", f)


# ── [S10] register_golden 단일 트랜잭션 ──────────────────────────────────────
class TestS10RegisterGoldenBatch(unittest.TestCase):
    def _rows(self, n, prefix="t"):
        return [{"content": {"displayServiceName": "s", "title": f"{prefix}{i}", "subtitle": "", "body": "b"},
                 "expected": {"finalGrade": "G"}} for i in range(n)]

    def test_single_commit_for_batch(self):
        st = _sqlite_store()
        proxy = _CountingConn(st._conn())
        st._conn = lambda: proxy
        n = st.register_golden(None, self._rows(200), replace=True, source="manual")
        self.assertEqual(n, 200)
        self.assertEqual(proxy.commits, 1, f"커밋 {proxy.commits}회(행마다 커밋하면 안 된다)")

    def test_rows_and_return_identical_to_per_row_upsert(self):
        from prism.store import content_hash
        a, b = _sqlite_store(), _sqlite_store()
        rows = self._rows(50)
        n = a.register_golden(None, rows, replace=False, source="manual")
        for r in rows:
            b.upsert_golden(content_hash(r["content"]), r["content"], r["expected"], source="manual")
        self.assertEqual(n, 50)
        self.assertEqual(a.golden_hashes(), b.golden_hashes())
        self.assertEqual({r["hash"] for r in a.golden_rows()}, {r["hash"] for r in b.golden_rows()})
        self.assertEqual({r["source"] for r in a.golden_rows()}, {"manual"})

    def test_replace_clears_then_inserts(self):
        st = _sqlite_store()
        st.register_golden(None, self._rows(3, "old"), replace=True)
        st.register_golden(None, self._rows(2, "new"), replace=True)
        self.assertEqual(st.golden_count(None), 2)

    def test_invalid_rows_skipped(self):
        st = _sqlite_store()
        rows = self._rows(2) + [{"content": {}, "expected": {}}, {"content": {"title": "x"}}]
        self.assertEqual(st.register_golden(None, rows, replace=True), 2)

    def test_duplicate_hashes_in_one_batch(self):
        st = _sqlite_store()
        rows = self._rows(1) * 3
        self.assertEqual(st.register_golden(None, rows, replace=True), 3)   # 반환은 처리 건수
        self.assertEqual(st.golden_count(None), 1)                          # 저장은 해시 1건


# ── [S11] contents_by_hash 의 subtitle ───────────────────────────────────────
class TestS11SubtitleRestored(unittest.TestCase):
    def test_subtitle_read_from_content_ref(self):
        st = _sqlite_store()
        _put_yellow(st, "a" * 16, subtitle="부제입니다")
        self.assertEqual(st.contents_by_hash()["a" * 16]["subtitle"], "부제입니다")

    def test_missing_subtitle_is_empty_string(self):
        st = _sqlite_store()
        _put_yellow(st, "b" * 16)
        self.assertEqual(st.contents_by_hash()["b" * 16]["subtitle"], "")

    def test_identity_fields_roundtrip(self):
        """이 dict 로 해시를 다시 만들어도 원본과 같아야 한다(유령 행 재발 방지)."""
        from prism.store import content_hash
        st = _sqlite_store()
        content = {"displayServiceName": "s", "title": "제목", "subtitle": "부제", "body": "본문"}
        ch = content_hash(content)
        c = st._conn()
        c.execute("INSERT INTO results(content_hash,service,title,payload,created_at) VALUES(?,?,?,?,?)",
                  (ch, "s", "제목", _j.dumps({"content_ref": content}), _t.time()))
        c.commit()
        self.assertEqual(content_hash(st.contents_by_hash()[ch]), ch)


# ── [S12] 삭제·상태변경의 정직한 bool ────────────────────────────────────────
class TestS12MutationsReturnRealResult(unittest.TestCase):
    def _st(self, rows):
        return _stub_supa(req_impl=lambda m, t, query="", body=None, prefer="": rows)

    def test_missing_row_returns_false(self):
        st, _ = self._st([])
        self.assertFalse(st.remove_golden("a" * 16, team="T"))
        self.assertFalse(st.board_set_status(1, "done", team="T"))
        self.assertFalse(st.board_answer(1, "답", team="T"))
        self.assertFalse(st.board_delete(1, team="T"))
        self.assertFalse(st.ent_delete("e_1"))
        self.assertFalse(st.ent_update("e_1", {"status": "confirmed"}))

    def test_matched_row_returns_true(self):
        st, _ = self._st([{"id": 1}])
        self.assertTrue(st.remove_golden("a" * 16, team="T"))
        self.assertTrue(st.board_set_status(1, "done", team="T"))
        self.assertTrue(st.board_answer(1, "답", team="T"))
        self.assertTrue(st.board_delete(1, team="T"))
        self.assertTrue(st.ent_delete("e_1"))
        self.assertTrue(st.ent_update("e_1", {"status": "confirmed"}))

    def test_representation_requested(self):
        st, calls = _stub_supa()
        st.remove_golden("a" * 16, team="T")
        st.board_delete(1, team="T")
        st.ent_update("e_1", {"status": "confirmed"})
        self.assertTrue(all("return=representation" in c[3] for c in calls), calls)

    def test_empty_field_update_still_false(self):
        st, _ = self._st([{"id": 1}])
        self.assertFalse(st.ent_update("e_1", {"없는필드": 1}))    # 갱신할 컬럼 없음

    def test_sqlite_contract_matches(self):
        st = _sqlite_store()
        self.assertFalse(st.ent_delete("e_없음"))
        self.assertFalse(st.ent_update("e_없음", {"status": "confirmed"}))
        st.ent_upsert({"entity_id": "e_1", "name": "a"})
        self.assertTrue(st.ent_update("e_1", {"status": "confirmed"}))
        self.assertTrue(st.ent_delete("e_1"))


if __name__ == "__main__":
    unittest.main()
