"""진척율·퀘스트 모집단 정합: 분자(검수 건수)와 분모(검수 대상)가 같은 집합을 봐야 한다.

증상(2026-07-17): 퀘스트 생성 직후 검수 0건인데 '팀 평균 4/4 · 완주'로 표시,
홈 히어로가 '검수 대상 4건 중 내가 45건 검수 · 0%'로 모순 표기.
원인: 분자가 전 기간 누적 피드백(이미 확정·삭제된 콘텐츠 포함), 분모는 현재 YELLOW.

실행: python3 -m pytest tests/test_arena_scope.py -q  (stdlib unittest · 의존성 0)
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ArenaScopeBase(unittest.TestCase):
    def _store(self):
        from prism.store import Store
        return Store(os.path.join(tempfile.mkdtemp(), "t.db"))

    def _put(self, st, h, review="yellow"):
        """콘텐츠 1건 삽입(review 상태 지정 · yellow=검수 대상)."""
        payload = {"quality_meta": {"review": review, "confidence": 0.5},
                   "trace": {"model": "m1"}}
        c = st._conn()
        c.execute("INSERT INTO results(content_hash,service,title,final_grade,payload,created_at) "
                  "VALUES(?,?,?,?,?,?)", (h, "s", h, "G", json.dumps(payload), time.time()))
        c.commit()

    def _seed(self):
        """h1=YELLOW(대상) · h2=GREEN(자동통과) · h3=삭제됨(피드백만 잔존).
        A 는 셋 다 검수(누적 3건), B 는 삭제된 h3 만 검수."""
        st = self._store()
        self._put(st, "h1", "yellow")
        self._put(st, "h2", "green")
        now = time.time()
        for ch in ("h1", "h2", "h3"):
            st.save_feedback(ch, "s", ch, "good", "review", "", now, reviewer="A")
        st.save_feedback("h3", "s", "h3", "bad", "review", "", now, reviewer="B")
        return st


class TestArenaStatsTargetScope(ArenaScopeBase):
    def test_yellow_hashes_is_target_population(self):
        st = self._seed()
        self.assertEqual(st.yellow_hashes(), {"h1"})
        self.assertEqual(st.yellow_count(), 1)

    def test_progress_counts_only_current_targets(self):
        st = self._seed()
        a = st.arena_stats()
        self.assertEqual(a["total_targets"], 1)
        rows = {r["reviewer"]: r for r in a["leaderboard"]}
        # 누적 검수 수(점수 원천)는 유지되, 캡션·진척 분자는 현재 대상 기준으로 분리
        self.assertEqual(rows["A"]["reviews"], 3)
        self.assertEqual(rows["A"]["target_reviews"], 1)
        self.assertEqual(rows["A"]["progress"], 1.0)
        # 대상 아닌 콘텐츠만 검수한 B: 누적이 있어도 진척 0 (기존엔 min(누적,분모)로 100% 오표기)
        self.assertEqual(rows["B"]["reviews"], 1)
        self.assertEqual(rows["B"]["target_reviews"], 0)
        self.assertEqual(rows["B"]["progress"], 0.0)

    def test_assigned_fields_exported_for_hero_caption(self):
        st = self._seed()
        st.set_assignees("h1", ["B"], min_reviewers=1)
        a = st.arena_stats()
        rows = {r["reviewer"]: r for r in a["leaderboard"]}
        self.assertEqual(rows["B"]["assigned_total"], 1)
        self.assertEqual(rows["B"]["assigned_done"], 0)
        self.assertEqual(rows["B"]["progress"], 0.0)      # 담당 기준 분모=1 · 완료 0
        # 배정 기준 팀 진척: h1 담당(B) 미검수 → 0
        self.assertEqual(a["team_progress"], 0.0)


class TestAssignedTargets(ArenaScopeBase):
    """모집단 = YELLOW ∪ 배정된 살아있는 콘텐츠: 일괄 배정 운영(auto 포함)의 분모 정합."""

    def test_assigned_auto_content_counts_as_target(self):
        st = self._seed()
        st.set_assignees("h2", ["A"], min_reviewers=1)   # 자동통과(auto) 콘텐츠를 배정
        self.assertEqual(st.review_targets(), {"h1", "h2"})
        a = st.arena_stats()
        self.assertEqual(a["total_targets"], 2)
        rows = {r["reviewer"]: r for r in a["leaderboard"]}
        self.assertEqual(rows["A"]["target_reviews"], 2)  # h1(yellow)+h2(배정) 둘 다 유효 검수
        self.assertEqual(rows["A"]["progress"], 1.0)      # 담당 h2 1건 중 1건 완료

    def test_orphan_assignment_excluded_from_targets(self):
        st = self._seed()
        st.set_assignees("h3", ["B"], min_reviewers=1)   # h3 는 삭제된 콘텐츠(고아 배정)
        self.assertEqual(st.review_targets(), {"h1"})
        rows = {r["reviewer"]: r for r in st.arena_stats()["leaderboard"]}
        self.assertEqual(rows["B"]["assigned_total"], 0)  # 고아는 '내 담당' 수에서 제외
        self.assertEqual(rows["B"]["progress"], 0.0)

    def test_clear_contents_removes_assignments(self):
        st = self._seed()
        st.set_assignees("h1", ["A"], min_reviewers=1)
        st.clear_team_contents()
        self.assertEqual(st.assignees(), {})              # 고아 배정 잔존 금지


class TestSupastorePagination(unittest.TestCase):
    """supabase _get 페이지네이션: PostgREST 는 요청 limit 과 무관하게 서버 max-rows(1000)로
    응답을 클램프한다(2026-07-17 실측) → 1000행 초과 테이블이 조용히 잘리지 않아야 한다."""

    def _store_with_rows(self, total):
        os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
        os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
        from prism.supastore import SupabaseStore
        st = SupabaseStore()
        data = [{"content_hash": f"h{i:04d}", "reviewer_id": "r"} for i in range(total)]
        calls = []

        def fake_req(method, table, *, query="", body=None, prefer=""):
            calls.append(query)
            import re as _re
            lim = int(_re.search(r"limit=(\d+)", query).group(1))
            off = int((_re.search(r"offset=(\d+)", query) or [None, "0"])[1])
            return [dict(r) for r in data[off:off + min(lim, 1000)]]  # 서버 max-rows=1000 클램프 모사

        st._req = fake_req
        return st, calls

    def test_collects_beyond_server_cap(self):
        st, calls = self._store_with_rows(2500)
        rows = st._get("assignments", "select=content_hash,reviewer_id")
        self.assertEqual(len(rows), 2500)                 # 1000 캡을 넘어 전량 수집
        self.assertTrue(all("order=" in q for q in calls))  # offset 페이징 안정 정렬 보장

    def test_explicit_limit_is_caller_cap_not_server_cap(self):
        st, _ = self._store_with_rows(2500)
        rows = st._get("feedback", "select=content_hash&limit=1500")
        self.assertEqual(len(rows), 1500)                 # limit=1500 이 1000 에서 잘리지 않음
        rows = st._get("feedback", "select=content_hash&limit=50")
        self.assertEqual(len(rows), 50)                   # 소량 상한은 그대로 존중


class TestQuestTargetScope(ArenaScopeBase):
    def test_fresh_quest_not_completed_by_old_reviews(self):
        """퀘스트 생성 직후: 옛(확정·삭제) 콘텐츠 검수가 진행률로 잡혀 '완주' 되면 안 된다."""
        from prism import serve
        from prism.config import Config
        st = self._seed()
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        cfg = Config()
        cfg.learn_next_at = "2030-01-02T09:30"
        with mock.patch.object(Config, "load", staticmethod(lambda path=None: cfg)):
            d = serve._arena_compute(None)
        self.assertGreater(d.get("next_batch_at") or 0, 0)
        # 유효 모집단 = 현재 YELLOW(h1) 뿐: h2(자동통과)·h3(삭제) 검수는 제외
        self.assertEqual(d.get("quest_done"), 1)
        # 팀 평균도 대상 스코프: A 의 h1 1건 → 1 (기존엔 h2·h3 포함 4건까지 부풀어 4/4 완주)
        self.assertEqual(d.get("quest_avg_done"), 1)
        self.assertLessEqual(d.get("quest_avg_done"), d.get("total_targets"))

    def _quest_compute(self, st):
        from prism import serve
        from prism.config import Config
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        cfg = Config()
        cfg.learn_next_at = "2030-01-02T09:30"
        with mock.patch.object(Config, "load", staticmethod(lambda path=None: cfg)):
            return serve._arena_compute(None)

    def test_team_progress_is_mean_of_personal_ratios_with_assignments(self):
        """배정 운영: 게이지 = 개인별(완료/배정) 진척의 팀 평균 — 총 대상 수가 커도 왜곡 없음.
        A 는 배정 h1 을 완료(1/1), B 는 배정 h2 미완료(0/1) → 팀 평균 0.5."""
        st = self._seed()
        st.set_assignees("h1", ["A"], min_reviewers=1)   # A 는 h1 을 이미 검수(유효)
        st.set_assignees("h2", ["B"], min_reviewers=1)   # h2(자동통과·배정으로 대상 편입) 미검수
        d = self._quest_compute(st)
        self.assertEqual(d.get("total_targets"), 2)
        self.assertEqual(d.get("quest_team_progress"), 0.5)

    def test_team_progress_without_assignments_uses_total_targets(self):
        """미배정 운영: 개인 분모 = 총 대상 → 기존 '평균 건수/총건수' 관점과 동치."""
        st = self._seed()                                # 대상 h1 뿐 · A 만 유효 검수 1건
        d = self._quest_compute(st)
        self.assertEqual(d.get("quest_team_progress"), 1.0)

    def test_orphan_assignment_ignored_in_team_progress(self):
        """삭제된 콘텐츠(고아 배정)는 개인 분모에 안 잡힌다(대상 스코프 필터)."""
        st = self._seed()
        st.set_assignees("h1", ["A"], min_reviewers=1)
        st.set_assignees("h3", ["B"], min_reviewers=1)   # h3 은 삭제됨 → 무시
        d = self._quest_compute(st)
        self.assertEqual(d.get("quest_team_progress"), 1.0)   # A 1/1 만 평균에 참여


if __name__ == "__main__":
    unittest.main()
