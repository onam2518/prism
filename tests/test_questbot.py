"""퀘스트 독려 봇: 진척 계산·평균 미만 선별·재발송 방지·발송 오케스트레이션.

네트워크(슬랙/Supabase)는 전부 대체(monkeypatch)해 의존성 0·오프라인으로 검증.
실행: python3 -m pytest tests/ -q
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import questbot as QB


def _future(minutes=120):
    return time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() + minutes * 60))


def _past(minutes=120):
    return time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() - minutes * 60))


class FakeStore:
    """questbot 이 쓰는 계약만 구현한 인메모리 스토어."""

    def __init__(self, names, per_done, dts=None):
        # names: {uid: name} · per_done: {uid: 유효검수 콘텐츠 수}
        self._names = names
        self._reports = {}
        self._dts = dts or {}
        # per_done 을 feedback_map 형태로 합성: 콘텐츠 ch(uid,i) 각각에 그 uid 의 good 표 1건
        self._fm = {}
        for uid, n in per_done.items():
            for i in range(n):
                ch = f"{uid}:{i}"
                self._fm[ch] = {"verdicts": [{"reviewer_id": uid, "verdict": "good", "ts": 1000.0}]}

    def reviewers_map(self, team=None):
        return {uid: {"name": nm} for uid, nm in self._names.items()}

    def draft_times(self, team=None):
        return dict(self._dts)                # 기본 0 → 모든 표가 창 안(유효)

    def feedback_map(self, team=None):
        return {k: dict(v) for k, v in self._fm.items()}

    def get_report(self, kind, team=None):
        return self._reports.get((kind, team))

    def save_report(self, kind, payload, team=None):
        self._reports[(kind, team)] = payload


class TestProgress(unittest.TestCase):
    def test_avg_and_roster_includes_zero_reviewers(self):
        st = FakeStore({"a": "A", "b": "B", "c": "C"}, {"a": 6, "b": 2})
        prog = QB.compute_progress(st, None, _future())
        self.assertTrue(prog["active"])
        self.assertEqual(prog["members"], 3)                 # c(0건)도 로스터
        self.assertAlmostEqual(prog["avg"], 8 / 3)           # (6+2+0)/3
        self.assertEqual(prog["per_done"], {"a": 6, "b": 2, "c": 0})

    def test_draft_window_excludes_pre_draft_reviews(self):
        # b 의 표(ts=1000)가 초안 생성(2000) 이전이면 유효 검수에서 제외
        st = FakeStore({"a": "A", "b": "B"}, {"a": 1, "b": 1}, dts={"b:0": 2000.0})
        prog = QB.compute_progress(st, None, _future())
        self.assertEqual(prog["per_done"], {"a": 1, "b": 0})

    def test_inactive_when_deadline_past(self):
        st = FakeStore({"a": "A"}, {"a": 1})
        self.assertFalse(QB.compute_progress(st, None, _past())["active"])
        self.assertFalse(QB.compute_progress(st, None, "")["active"])


class TestSelect(unittest.TestCase):
    def test_below_average_selected_and_sorted(self):
        st = FakeStore({"a": "A", "b": "B", "c": "C"}, {"a": 6, "b": 2, "c": 0})
        prog = QB.compute_progress(st, None, _future())      # avg = 8/3 ≈ 2.67
        lag = QB.select_laggards(prog, 1.0)
        self.assertEqual([r["uid"] for r in lag], ["c", "b"])  # done 오름차순, a 는 평균 이상
        self.assertNotIn("a", [r["uid"] for r in lag])

    def test_no_targets_when_avg_zero(self):
        st = FakeStore({"a": "A", "b": "B"}, {})
        prog = QB.compute_progress(st, None, _future())
        self.assertEqual(QB.select_laggards(prog, 1.0), [])

    def test_ratio_tightens_target(self):
        st = FakeStore({"a": "A", "b": "B", "c": "C"}, {"a": 6, "b": 2, "c": 0})
        prog = QB.compute_progress(st, None, _future())      # avg ≈ 2.67 · ×0.5 = 1.33
        self.assertEqual([r["uid"] for r in QB.select_laggards(prog, 0.5)], ["c"])


class TestCompose(unittest.TestCase):
    def test_zero_and_partial_messages(self):
        t0, _ = QB.compose("가온", 0, 3.4, time.time() + 3600)
        self.assertIn("가온", t0)
        self.assertIn("시작하지 않으셨어요", t0)
        t1, blocks = QB.compose("나은", 2, 5.0, time.time() + 3600)
        self.assertIn("2건", t1)
        self.assertIn("3건만 더", t1)                        # 5 - 2 = 3
        self.assertEqual(blocks[-1]["elements"][0]["url"], QB.APP_URL)


class TestRun(unittest.TestCase):
    def setUp(self):
        # 슬랙/Supabase 네트워크 대체
        self._orig = (QB.auth_emails, QB.lookup_slack_id, QB.send_dm)
        self.sent = []
        QB.auth_emails = lambda: {"a": "a@x.io", "b": "b@x.io", "c": "c@x.io"}
        QB.lookup_slack_id = lambda tok, email: {"a@x.io": "U_A", "b@x.io": "U_B",
                                                 "c@x.io": "U_C"}.get(email, "")

        def _send(tok, uid, text, blocks=None):
            self.sent.append((uid, text))
            return {"ok": True}
        QB.send_dm = _send

    def tearDown(self):
        QB.auth_emails, QB.lookup_slack_id, QB.send_dm = self._orig

    def test_sends_to_below_average_only_and_is_idempotent(self):
        st = FakeStore({"a": "A", "b": "B", "c": "C"}, {"a": 6, "b": 2, "c": 0})
        r1 = QB.run(st, team=None, learn_next_at=_future(), token="xoxb-x", log=lambda *a: None)
        self.assertEqual(r1["sent"], 2)                      # b, c (평균 미만)
        self.assertEqual({u for u, _ in self.sent}, {"U_B", "U_C"})

        # 재실행: 같은 퀘스트 → 재발송 안 함(1인 1회)
        self.sent.clear()
        r2 = QB.run(st, team=None, learn_next_at=_future(), token="xoxb-x", log=lambda *a: None)
        self.assertEqual(r2["sent"], 0)
        self.assertEqual(r2["skipped"], 2)
        self.assertEqual(self.sent, [])

        # --force 는 재발송
        r3 = QB.run(st, team=None, learn_next_at=_future(), token="xoxb-x",
                    force=True, log=lambda *a: None)
        self.assertEqual(r3["sent"], 2)

    def test_inactive_quest_sends_nothing(self):
        st = FakeStore({"a": "A", "b": "B"}, {"a": 1, "b": 0})
        r = QB.run(st, team=None, learn_next_at=_past(), token="xoxb-x", log=lambda *a: None)
        self.assertFalse(r["active"])
        self.assertEqual(r["sent"], 0)
        self.assertEqual(self.sent, [])

    def test_dry_run_does_not_send(self):
        st = FakeStore({"a": "A", "b": "B", "c": "C"}, {"a": 6, "b": 2, "c": 0})
        r = QB.run(st, team=None, learn_next_at=_future(), token="xoxb-x",
                   dry_run=True, log=lambda *a: None)
        self.assertEqual(r["sent"], 0)
        self.assertEqual(self.sent, [])
        self.assertEqual({x["status"] for x in r["results"]}, {"dry"})

    def test_unresolved_slack_user_is_skipped_not_failed(self):
        QB.lookup_slack_id = lambda tok, email: ""          # 아무도 슬랙 매핑 안 됨
        st = FakeStore({"a": "A", "b": "B", "c": "C"}, {"a": 6, "b": 2, "c": 0})
        r = QB.run(st, team=None, learn_next_at=_future(), token="xoxb-x", log=lambda *a: None)
        self.assertEqual(r["sent"], 0)
        self.assertEqual(r["skipped"], 2)
        self.assertTrue(all(x["status"].startswith("skip:") for x in r["results"]))


class AsgStore(FakeStore):
    """배정(assignees)과 검수운영 기한(crew_wave)을 함께 가진 스토어."""

    def __init__(self, names, per_done, asg=None, due_at=0.0):
        super().__init__(names, per_done)
        self._asg = asg or {}
        if due_at:
            self._reports[("crew_wave", None)] = {"item": {"due_at": due_at}}

    def assignees(self, team=None):
        return {ch: {"reviewers": list(rv), "min": 1} for ch, rv in self._asg.items()}


class TestAssignmentMode(unittest.TestCase):
    """독려 기준을 '팀 평균 미만'에서 '내가 맡은 몫과 내 기한'으로 전환.
    남과 비교하는 독려는 팀을 방어적으로 만들고, 많이 맡은 사람이 억울해진다."""

    def test_wave_due_and_assignment_progress(self):
        due = time.time() + 7200
        st = AsgStore({"a": "A", "b": "B"}, {"a": 2, "b": 0},
                      asg={"a:0": ["a"], "a:1": ["a"], "x1": ["a"], "x2": ["b"]}, due_at=due)
        self.assertAlmostEqual(QB.wave_due(st), due, places=3)
        p = QB.assignment_progress(st)
        self.assertEqual(p["a"], {"assigned": 3, "done": 2, "pending": 1})   # a:0·a:1 은 판정함
        self.assertEqual(p["b"], {"assigned": 1, "done": 0, "pending": 1})

    def test_targets_are_people_with_work_left_not_below_average(self):
        """많이 맡아 많이 한 사람도 남았으면 대상 · 적게 했어도 다 끝냈으면 제외."""
        asg = {"a:%d" % i: ["a"] for i in range(5)}      # dict | dict 는 3.9+ · 저장소는 3.8 지원
        asg.update({"x9": ["a"], "b:only": ["b"]})
        st = AsgStore({"a": "A", "b": "B"}, {"a": 5, "b": 0}, asg=asg)
        prog = QB.compute_progress(st, None, _future())
        mine = QB.select_by_assignment(prog, QB.assignment_progress(st), 0)
        names = [m["name"] for m in mine]
        self.assertIn("A", names)                      # 5건 했지만 1건 남음 → 대상
        self.assertIn("B", names)                      # 0건 · 1건 남음 → 대상
        # 다 끝낸 사람은 빠진다
        st2 = AsgStore({"a": "A"}, {"a": 1}, asg={"a:0": ["a"]})
        self.assertEqual(QB.select_by_assignment(QB.compute_progress(st2, None, _future()),
                                                 QB.assignment_progress(st2), 0), [])

    def test_message_talks_about_my_share_only(self):
        text, blocks = QB.compose_mine("해씨", 4, 10, 6, time.time() + 3600)
        self.assertIn("6건 남았어요", text)
        self.assertIn("10건 중 4건", text)
        self.assertIn("오늘까지", text)
        self.assertNotIn("평균", text)                 # 남과 비교하지 않는다
        self.assertTrue(blocks[0]["text"]["text"])
        zero, _ = QB.compose_mine("에디", 0, 8, 8, 0)
        self.assertIn("아직 시작 전", zero)
        self.assertIn("틈날 때", zero)                 # 기한 없으면 재촉하지 않는다

    def test_falls_back_to_team_average_without_assignments(self):
        st = FakeStore({"a": "A", "b": "B"}, {"a": 6, "b": 0})
        r = QB.run(st, team=None, learn_next_at=_future(), token="", dry_run=True, log=lambda *a: None)
        self.assertEqual(r["mode"], "avg")

    def test_assignment_mode_runs_even_when_quest_deadline_passed(self):
        """맡은 일이 남아 있으면 퀘스트 일시가 지났어도 독려한다(기한 원천이 다르다)."""
        st = AsgStore({"a": "A"}, {"a": 0}, asg={"x1": ["a"]}, due_at=time.time() + 3600)
        r = QB.run(st, team=None, learn_next_at=_past(), token="", dry_run=True, log=lambda *a: None)
        self.assertEqual(r["mode"], "mine")
        self.assertEqual(r["targets"], 1)
        self.assertTrue(r["deadline"] > time.time())


if __name__ == "__main__":
    unittest.main()
