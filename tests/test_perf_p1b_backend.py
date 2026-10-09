"""백엔드 성능 P1-B 회귀 테스트.

  1. 판정 저장: 배정은 그 건만 조회 · 오늘 지급 확인된 미션은 log_event_once 재호출 없음 ·
     오늘 판정 해시 1회 조회를 daily5·split1 이 공유
  2. /raw: purpose_map 전량 조회 없음(행의 purpose) · 배정 30s 캐시 + 쓰기 무효화
  3. 메타 전용 조회(results_rows body=False): 원격은 본문·부제·원천 필드를 select 하지 않음
  4. 폴링 경량화: ent_stats 카운트 전용 · ent_list 링크 청크 · 평가 런 상세 캐시 ·
     실험 원문 링크 기억 · 실행 큐 해시 목록 제외 · 오토파일럿 상태 golden_hashes 제외

실행: python3 -m pytest tests/test_perf_p1b_backend.py -q
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import serve as SV              # noqa: E402
from prism import reviewops as RO          # noqa: E402
from prism import evalops as EO            # noqa: E402
from prism import keywordlab as KL         # noqa: E402
from prism.supastore import SupabaseStore  # noqa: E402


def _supa(get=None, count=None):
    """네트워크 없는 SupabaseStore · _get/_count 호출을 기록한다."""
    st = SupabaseStore.__new__(SupabaseStore)
    st.url, st.base, st.key = "https://x.supabase.co", "https://x.supabase.co/rest/v1", "k"
    st.gets, st.counts = [], []

    def _get(table, query=""):
        st.gets.append((table, query))
        return (get or (lambda t, q: []))(table, query)

    def _count(table, query="select=id"):
        st.counts.append((table, query))
        return (count or (lambda t, q: 0))(table, query)
    st._get, st._count = _get, _count
    return st


class _Remote:
    """약참조 가능한 원격 스토어 흉내(호출 횟수 기록)."""
    REMOTE = True

    def __init__(self):
        self.calls = {}

    def _hit(self, name):
        self.calls[name] = self.calls.get(name, 0) + 1


class TestFeedbackRoundTrips(unittest.TestCase):
    def test_assignment_lookup_is_single_hash(self):
        seen = []

        class St:
            def assignees(self, team=None, hashes=None):
                seen.append(hashes)
                return {"h1": {"reviewers": ["other"], "min": 1}}
        with mock.patch.object(SV, "get_store", lambda: St()):
            r = RO.apply_feedback({"hash": "h1", "verdict": "good", "reviewer": "me"})
        self.assertFalse(r["ok"])                       # 배정 배타 유지
        self.assertEqual(seen, [["h1"]])                # 전량(None)이 아니라 그 건만

    def test_awarded_mission_not_rechecked(self):
        class St:
            def __init__(self):
                self.once = 0

            def log_event_once(self, *a, **k):
                self.once += 1
                return self.once == 1
        st = St()
        done = [{"id": "daily5", "label": "x", "bonus": 1, "completed": True},
                {"id": "gold1", "label": "y", "bonus": 1, "completed": False}]
        with mock.patch.object(SV, "get_store", lambda: st), \
                mock.patch.object(RO, "mission_progress", lambda rv, team=None: done):
            first = RO._check_missions("me", "t")
            RO._check_missions("me", "t")
            RO._check_missions("me", "t")
            RO._check_missions("you", "t")              # 다른 검수자는 따로 확인
        self.assertEqual([m["id"] for m in first], ["daily5"])
        self.assertEqual(st.once, 2)                    # me 1회 + you 1회(반복 판정은 건너뜀)

    def test_split_reuses_today_hashes(self):
        h = "0123456789abcdef"
        st = _supa(get=lambda t, q: [{"content_hash": h, "verdict": "good"},
                                     {"content_hash": h, "verdict": "bad"}] if "in.(" in q else [])
        st._today_iso = lambda: "2026-10-10T00:00:00Z"
        self.assertEqual(st.split_reviewed_today("u", team="t", today=[h]), 1)
        self.assertFalse(any("reviewer_id=" in q for _t, q in st.gets))   # 오늘 판정 재조회 없음

    def test_mission_progress_fetches_today_once(self):
        class St:
            n = 0

            def feedback_today_hashes(self, reviewer, team=None):
                St.n += 1
                return ["a", "b"]

            def feedback_today(self, *a, **k):
                raise AssertionError("feedback_today 재조회")

            def split_reviewed_today(self, reviewer, team=None, today=None):
                assert today == ["a", "b"]
                return 1

            def gold_today(self, *a, **k):
                return {"correct": 0}
        with mock.patch.object(SV, "get_store", lambda: St()), \
                mock.patch.object(RO, "reviewer_roles", lambda team=None: {}):
            ms = {m["id"]: m["done"] for m in RO.mission_progress("me")}
        self.assertEqual(St.n, 1)
        self.assertEqual((ms["daily5"], ms["split1"]), (2, 1))


class TestRawNoRescan(unittest.TestCase):
    def test_assignees_cached_and_invalidated_by_write(self):
        class St(_Remote):
            def assignees(self, team=None, hashes=None):
                self._hit("asg")
                return {"h": {"reviewers": ["a"], "min": 1}}
        st = St()
        with mock.patch.object(SV, "get_store", lambda: st):
            SV.assignees_cached("t")
            SV.assignees_cached("t")
            self.assertEqual(st.calls["asg"], 1)        # 폴링 재조회는 캐시
            SV._agg_bump()                              # 배정 쓰기 경로가 부르는 무효화
            SV.assignees_cached("t")
            self.assertEqual(st.calls["asg"], 2)

    def test_raw_skips_eval_purpose_without_purpose_map(self):
        def row(title, purpose):
            return {"content_ref": {"title": title, "displayServiceName": "뉴스",
                                    "body_hash": (title * 16)[:16]},
                    "item_meta": {"summary": "s"}, "quality_meta": {"finalGrade": "G"},
                    "trace": {"model": "m", "version": 1}, "purpose": purpose}

        class St:
            def purpose_map(self, team=None):
                raise AssertionError("purpose_map 전량 조회")

            def assignees(self, team=None, hashes=None):
                return {}

            def feedback_map(self, team=None):
                return {}
        kw = []
        with mock.patch.object(SV, "get_store", lambda: St()), \
                mock.patch.object(SV, "results_rows",
                                  lambda limit=5000, team=None, **k: kw.append(k) or
                                  [row("a", "review"), row("b", "eval")]):
            r = SV.raw_rows()
        self.assertEqual([i["title"] for i in r["items"]], ["a"])
        self.assertEqual(kw[0].get("body"), False)      # 목록은 메타 전용 조회


class TestMetaOnlyRows(unittest.TestCase):
    def test_recent_meta_select_excludes_body(self):
        st = _supa(get=lambda t, q: [{"hash": "h" * 16, "title": "t", "purpose": "eval"}])
        rows = st.recent(10, team="t", body=False)
        sel = st.gets[-1][1].split("&")[0]
        for col in ("body", "subtitle", "source_fields"):
            self.assertNotIn(col, sel.split("=", 1)[1].split(","))
        self.assertEqual(rows[0]["purpose"], "eval")
        self.assertEqual(rows[0]["content_ref"]["body_hash"], "h" * 16)
        st.recent(10, team="t")                          # 기본(전체 행)은 본문 포함 유지
        self.assertIn("body", st.gets[-1][1].split("&")[0].split("=", 1)[1].split(","))

    def test_results_rows_meta_cache_key_separate(self):
        class St(_Remote):
            def recent(self, limit, team=None, body=True):
                self._hit("full" if body else "meta")
                return [{"body": body}]
        st = St()
        with mock.patch.object(SV, "get_store", lambda: st):
            SV._agg_bump()
            self.assertEqual(SV.results_rows(team="t", body=False), [{"body": False}])
            self.assertEqual(SV.results_rows(team="t"), [{"body": True}])
            SV.results_rows(team="t", body=False)
        self.assertEqual(st.calls, {"meta": 1, "full": 1})

    def test_dashboard_requests_meta_rows(self):
        from prism import dashops as DO
        kw = []
        with mock.patch.object(SV, "results_rows", lambda limit=5000, team=None, **k: kw.append(k) or []), \
                mock.patch.object(SV, "get_store", lambda: None):
            DO._dashboard_compute("t")
        self.assertEqual(kw, [{"body": False}])


class TestPolledEndpoints(unittest.TestCase):
    def test_ent_stats_counts_only(self):
        st = _supa(count=lambda t, q: 7 if "type=eq.PS" in q else 10)
        s = st.ent_stats()
        self.assertEqual(st.gets, [])                    # 행 다운로드 없음
        self.assertEqual(s["byType"]["PS"], 7)
        self.assertEqual(s["total"], 10)

    def test_ent_list_links_chunked(self):
        ents = [{"entity_id": f"e{i}", "name": f"n{i}"} for i in range(250)]
        st = _supa(get=lambda t, q: ents if t == "entities" else [{"entity_id": "e1", "content_hash": "c"}])
        st._ent_norm = lambda r: dict(r)
        rows = st.ent_list(limit=300)
        links = [q for t, q in st.gets if t == "content_entities"]
        self.assertEqual(len(links), 3)                  # 100개씩 청크
        self.assertEqual(next(r for r in rows if r["entity_id"] == "e1")["n_contents"], 1)

    def test_eval_run_detail_cached_and_single_run_lookup(self):
        class St(_Remote):
            def eval_run_get(self, run_id, team=None):
                self._hit("get")
                return {"status": "running", "cursor": 3, "total": 9, "metrics": {}}

            def eval_results_list(self, run_id, team=None, only_fail=False, limit=2000, checked=False):
                self._hit("fails")
                assert checked
                return [{"hash": "h", "expected": {"finalGrade": "G"}, "got": {"finalGrade": "R"}}]

            def eval_check_counts(self, team=None):
                self._hit("checks")
                return {}
        st = St()
        with mock.patch.object(SV, "get_store", lambda: st), \
                mock.patch.object(SV, "_report_get", lambda *a, **k: {}):
            a = EO.eval_run_report(1, "t")
            b = EO.eval_run_report(1, "t")
        self.assertEqual(len(a["detail"]), 1)
        self.assertEqual(a["detail"], b["detail"])
        self.assertEqual(st.calls, {"get": 2, "fails": 1, "checks": 1})   # 폴링당 런 조회 1회 · 상세는 재사용

    def test_lab_source_urls_memoized_per_run(self):
        n = []

        class St:
            def origin_meta_for(self, hashes, team):
                n.append(1)
                return {"h": {"url": "https://x"}}
        items = [{"hash": "h"}]
        with mock.patch.object(KL, "_store", lambda: St()):
            self.assertEqual(KL._source_urls(items, "t", "run-p1b"), {"h": "https://x"})
            KL._source_urls(items, "t", "run-p1b")
        self.assertEqual(len(n), 1)

    def test_autopilot_status_light_select(self):
        st = _supa(get=lambda t, q: [{"id": 5, "status": "done"}])
        run = st.autopilot_latest("t", with_hashes=False)
        sel = st.gets[-1][1].split("&")[0]
        self.assertNotIn("golden_hashes", sel)
        self.assertNotIn("*", sel)
        self.assertNotIn("golden_hashes", run)

        class St:
            def autopilot_latest(self, team=None, with_hashes=True):
                return {"id": 991, "status": "done"}

            def autopilot_get(self, run_id, team=None):
                St.full = getattr(St, "full", 0) + 1
                return {"id": 991, "status": "done", "golden_hashes": ["a", "b", "c"]}
        with mock.patch.object(SV, "get_store", lambda: St()):
            self.assertEqual(EO.autopilot_status("t")["run"]["golden_n"], 3)
            self.assertEqual(EO.autopilot_status("t")["run"]["golden_n"], 3)
        self.assertEqual(St.full, 1)                     # 건수는 런별로 기억(해시 목록 1회만)


if __name__ == "__main__":
    unittest.main()
