"""콘텐츠별 검수 담당 배정(sqlite): 배정 round-trip · 배타적 큐 노출 · 진척 개인화.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
설계 근거: 배정되면 담당자에게만 큐 노출(배타적), 미배정은 오픈 큐 유지.
진척: 개인 분모=내 담당 수, 팀 진척=Σ min(검수인원,N)/N ÷ 배정 콘텐츠 수.
"""
import json as _j
import os
import sys
import tempfile
import time as _t
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class AssignmentBase(unittest.TestCase):
    def _store(self):
        from prism.store import Store
        return Store(os.path.join(tempfile.mkdtemp(), "t.db"))

    def _put(self, st, h, conf=0.5, ts=None):
        """YELLOW 콘텐츠 1건 삽입(검수 큐 대상)."""
        payload = {"quality_meta": {"review": "yellow", "confidence": conf},
                   "content_ref": {"title": h, "body": "b"}}
        c = st._conn()
        c.execute("INSERT INTO results(content_hash,service,title,final_grade,payload,created_at) "
                  "VALUES(?,?,?,?,?,?)", (h, "s", h, "G", _j.dumps(payload), ts or _t.time()))
        c.commit()


class TestAssignRoundTrip(AssignmentBase):
    def test_set_get_clear(self):
        st = self._store()
        st.set_assignees("h1", ["A", "B"], min_reviewers=2, team="t")
        asg = st.assignees("t")
        self.assertEqual(asg["h1"]["reviewers"], ["A", "B"])
        self.assertEqual(asg["h1"]["min"], 2)
        # 팀 스코프 격리: 다른 팀엔 안 보임
        self.assertEqual(st.assignees("other"), {})
        # 교체(중복·순서 보존, 재배정)
        st.set_assignees("h1", ["C", "C", "A"], min_reviewers=1, team="t")
        self.assertEqual(st.assignees("t")["h1"]["reviewers"], ["C", "A"])
        # 해제
        st.clear_assignees("h1", team="t")
        self.assertEqual(st.assignees("t"), {})

    def test_min_clamped_to_assignee_count(self):
        st = self._store()
        st.set_assignees("h1", ["A"], min_reviewers=5, team="t")   # N > 인원 → 인원으로 클램프
        self.assertEqual(st.assignees("t")["h1"]["min"], 1)
        st.set_assignees("h2", ["A", "B"], min_reviewers=0, team="t")   # N < 1 → 1
        self.assertEqual(st.assignees("t")["h2"]["min"], 1)


class TestExclusiveQueue(AssignmentBase):
    def test_assigned_content_visible_only_to_assignee(self):
        st = self._store()
        self._put(st, "h_open")                       # 미배정
        self._put(st, "h_a")                          # A 담당
        st.set_assignees("h_a", ["A"], team="t")
        # A: 오픈 + 자기 담당 모두 보임
        qa = {r["hash"] for r in st.review_queue(team="t", reviewer="A")}
        self.assertEqual(qa, {"h_open", "h_a"})
        # B: 오픈만, A 담당은 숨김(배타적)
        qb = {r["hash"] for r in st.review_queue(team="t", reviewer="B")}
        self.assertEqual(qb, {"h_open"})
        # 미인증(reviewer=None): 배정 콘텐츠 전부 숨김
        qn = {r["hash"] for r in st.review_queue(team="t")}
        self.assertEqual(qn, {"h_open"})

    def test_assignee_drops_after_own_review(self):
        st = self._store()
        self._put(st, "h_a")
        st.set_assignees("h_a", ["A", "B"], min_reviewers=2, team="t")
        now = _t.time()
        st.save_feedback("h_a", "s", "T", "good", "review", "", now, reviewer="A", team="t")
        # A: 이미 검수 → 큐에서 빠짐
        self.assertEqual(st.review_queue(team="t", reviewer="A"), [])
        # B: 아직 미검수 → 여전히 보임(min 2 미충족과 무관하게 '내 몫')
        self.assertEqual([r["hash"] for r in st.review_queue(team="t", reviewer="B")], ["h_a"])

    def test_queue_item_carries_assignee_meta(self):
        st = self._store()
        self._put(st, "h_a")
        st.set_assignees("h_a", ["A", "B"], min_reviewers=2, team="t")
        row = st.review_queue(team="t", reviewer="A")[0]
        self.assertEqual(row["assignees"], ["A", "B"])
        self.assertEqual(row["min_reviewers"], 2)


class TestProgressPersonalized(AssignmentBase):
    def test_individual_denominator_is_my_assignment_count(self):
        st = self._store()
        for h in ("h1", "h2", "h3"):
            self._put(st, h)
            st.set_assignees(h, ["A"], team="t")
        now = _t.time()
        st.save_feedback("h1", "s", "T", "good", "review", "", now, reviewer="A", team="t")
        stats = st.arena_stats(team="t")
        row = next(r for r in stats["leaderboard"] if r["reviewer_id"] == "A")
        self.assertAlmostEqual(row["progress"], round(1 / 3, 4))   # 담당 3 중 1 검수
        # 팀 진척 = (1 + 0 + 0)/3 (각 min1, 부분 크레딧 합산)
        self.assertAlmostEqual(stats["team_progress"], round(1 / 3, 4))

    def test_team_progress_partial_credit_with_min_n(self):
        st = self._store()
        self._put(st, "h1")
        st.set_assignees("h1", ["A", "B"], min_reviewers=2, team="t")
        now = _t.time()
        st.save_feedback("h1", "s", "T", "good", "review", "", now, reviewer="A", team="t")
        # 1콘텐츠 · N=2 · 1명 검수 → min(1,2)/2 = 0.5
        self.assertAlmostEqual(st.arena_stats(team="t")["team_progress"], 0.5)
        st.save_feedback("h1", "s", "T", "good", "review", "", now, reviewer="B", team="t")
        # 2명 검수 → min(2,2)/2 = 1.0 (통과)
        self.assertAlmostEqual(st.arena_stats(team="t")["team_progress"], 1.0)

    def test_split_workload_weighted_sum(self):
        # 배치 분할: c1→A, c2→B (각 min1). A만 검수 → 팀 (1+0)/2 = 0.5
        st = self._store()
        self._put(st, "c1"); self._put(st, "c2")
        st.set_assignees("c1", ["A"], team="t")
        st.set_assignees("c2", ["B"], team="t")
        now = _t.time()
        st.save_feedback("c1", "s", "T", "good", "review", "", now, reviewer="A", team="t")
        stats = st.arena_stats(team="t")
        self.assertAlmostEqual(stats["team_progress"], 0.5)
        a = next(r for r in stats["leaderboard"] if r["reviewer_id"] == "A")
        b = next((r for r in stats["leaderboard"] if r["reviewer_id"] == "B"), None)
        self.assertAlmostEqual(a["progress"], 1.0)                 # A 담당 1/1
        if b:
            self.assertAlmostEqual(b["progress"], 0.0)             # B 담당 0/1

    def test_no_assignment_keeps_legacy_progress(self):
        # 배정이 전혀 없으면 기존 산식(팀 전체 YELLOW 분모) 그대로
        st = self._store()
        for h in ("h1", "h2"):
            self._put(st, h)
        now = _t.time()
        st.save_feedback("h1", "s", "T", "good", "review", "", now, reviewer="A", team="t")
        stats = st.arena_stats(team="t")
        row = next(r for r in stats["leaderboard"] if r["reviewer_id"] == "A")
        self.assertAlmostEqual(row["progress"], round(1 / 2, 4))   # min(1,2)/2


class TestServeWiring(AssignmentBase):
    """serve 계층: raw_rows 담당자 표시 · review_queue reviewer 전달(배타 노출)."""
    def _with_store(self):
        from prism import serve
        st = self._store()
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve, st

    def _put_exec(self, st, title):
        """실행된(비-pending) YELLOW 콘텐츠(모델·판정 포함) 삽입 → 검수 표에 노출."""
        from prism.store import content_hash
        content = {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "본문 " + title}
        ch = content_hash(content)
        payload = {"quality_meta": {"review": "yellow", "finalGrade": "G", "reasons": []},
                   "item_meta": {"summary": title}, "trace": {"model": "gpt", "version": 1},
                   "content_ref": dict(content, body_hash=ch)}
        c = st._conn()
        c.execute("INSERT INTO results(content_hash,service,title,final_grade,reasons,item_meta,payload,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (ch, "뉴스", title, "G", "[]", _j.dumps({"summary": title}), _j.dumps(payload), _t.time()))
        c.commit()
        return ch

    def test_raw_rows_show_assignees(self):
        serve, st = self._with_store()
        ch = self._put_exec(st, "T1")
        st.set_assignees(ch, ["alice", "bob"], min_reviewers=2, team="")
        row = next(r for r in serve.raw_rows(reviewer="alice")["items"] if r["hash"] == ch)
        self.assertEqual(row["assignees"], ["alice", "bob"])
        self.assertEqual(row["min_reviewers"], 2)

    def test_review_queue_endpoint_threads_reviewer(self):
        serve, st = self._with_store()
        self._put(st, "h_open")
        self._put(st, "h_a")
        st.set_assignees("h_a", ["alice"], team="")
        qa = serve.review_queue({"reviewer": "alice"})
        qb = serve.review_queue({"reviewer": "bob"})
        self.assertIn("h_a", [i["hash"] for i in qa["items"]])   # 담당자는 자기 배정 봄
        self.assertNotIn("h_a", [i["hash"] for i in qb["items"]])  # 비담당은 배타적으로 숨김


if __name__ == "__main__":
    unittest.main()
