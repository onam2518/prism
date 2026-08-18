"""AI 초안 판정(실험실): 나에게 배정된 대기(YELLOW) 콘텐츠를 백그라운드로 채점하고 진척도를
폴링으로 준다. 이미 내가 판정한 건·골드는 대상이 아니며, 자동 커밋하지 않는다.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeJudge:
    mock = False

    def complete_json(self, system, user, tag=""):
        d = json.loads(user)
        bad = d.get("등급") == "R"                       # R = 수정 초안(테스트 결정성)
        return ({"verdict": "bad" if bad else "good", "confidence": 0.9,
                 "reason": "테스트 근거", "elements": ["grade"] if bad else []}, None)


class _Base(unittest.TestCase):
    def _serve(self):
        os.environ["PRISM_BACKEND"] = "sqlite"
        os.environ["PRISM_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
        from prism import serve, autoreview as AR
        from prism.store import Store
        serve._STORE = Store(os.environ["PRISM_DB"])
        serve.Handler.server_mock = True
        self._orig = serve.llm_for_model
        serve.llm_for_model = lambda model, mock: (_FakeJudge(), "fake")
        AR._RUNS.clear()
        self.addCleanup(lambda: setattr(serve, "llm_for_model", self._orig))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)
        self.addCleanup(AR._RUNS.clear)
        self.AR = AR
        return serve

    def _seed(self, serve, title, grade, review, feedback_by=None, assign_to=None):
        from prism.store import content_hash
        st = serve._STORE
        content = {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "본문 " + title}
        ch = content_hash(content)
        payload = {"content_ref": dict(content), "item_meta": {"summary": "요약", "entities": [], "intent": [], "content_category": ["자동차"]},
                   "quality_meta": {"finalGrade": grade, "reasons": [], "review": review}, "trace": {"model": "solar-pro2", "version": 1}}
        st._conn().execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,payload,created_at) VALUES(?,?,?,?,?,?)",
                           (ch, "뉴스", title, grade, json.dumps(payload), time.time()))
        st._conn().commit()
        if feedback_by:
            st.save_feedback(ch, "뉴스", title, "good", "review", "", time.time(), reviewer=feedback_by)
        if assign_to:
            st.set_assignees(ch, [assign_to], min_reviewers=1, team=None)
        return ch

    def _finish(self, run):
        """백그라운드 잡 완료까지 status 폴링(테스트 타임아웃 2초)."""
        rid = run["id"]
        for _ in range(200):
            s = self.AR.status(rid)
            if not s.get("running"):
                return s
            time.sleep(0.01)
        return self.AR.status(rid)


class TestAutoReviewRun(_Base):
    def test_runs_and_fills_verdicts(self):
        serve = self._serve()
        self._seed(serve, "정확한 글", "G", "yellow")
        self._seed(serve, "고칠 글", "R", "yellow")
        self._seed(serve, "확정된 글", "G", "auto")           # YELLOW 아님 = 대상 아님
        run = self.AR.start(team=None, reviewer="pete")
        self.assertTrue(run["ok"])
        self.assertEqual(run["total"], 2)
        self.assertEqual(run["model"], "claude-opus-5")
        s = self._finish(run)
        self.assertFalse(s["running"])
        self.assertEqual(s["done"], 2)
        self.assertEqual(s["pct"], 1.0)
        titles = {it["title"]: it for it in s["items"]}
        self.assertEqual(set(titles), {"정확한 글", "고칠 글"})
        self.assertEqual(titles["정확한 글"]["ai"]["verdict"], "good")
        self.assertEqual(titles["고칠 글"]["ai"]["verdict"], "bad")
        self.assertIn("grade", titles["고칠 글"]["ai"]["elements"])
        self.assertFalse(titles["정확한 글"]["sameModel"])

    def test_targets_only_content_assigned_to_me(self):
        serve = self._serve()
        self._seed(serve, "내 배정", "G", "yellow", assign_to="pete")
        self._seed(serve, "남 배정", "G", "yellow", assign_to="other")
        self._seed(serve, "미배정", "G", "yellow")
        run = self.AR.start(team=None, reviewer="pete")
        self.assertEqual(run["scope"], "assigned")
        s = self._finish(run)
        self.assertEqual([it["title"] for it in s["items"]], ["내 배정"])   # 내 배정만

    def test_falls_back_to_queue_when_no_assignments(self):
        serve = self._serve()
        self._seed(serve, "대기 글", "G", "yellow")
        run = self.AR.start(team=None, reviewer="pete")
        self.assertEqual(run["scope"], "all")                # 배정 없음 = 큐 폴백
        s = self._finish(run)
        self.assertEqual([it["title"] for it in s["items"]], ["대기 글"])

    def test_excludes_content_i_already_judged(self):
        serve = self._serve()
        self._seed(serve, "내가 본 글", "G", "yellow", feedback_by="pete")
        self._seed(serve, "안 본 글", "G", "yellow")
        s = self._finish(self.AR.start(team=None, reviewer="pete"))
        self.assertEqual([it["title"] for it in s["items"]], ["안 본 글"])

    def test_does_not_commit_verdict(self):
        serve = self._serve()
        ch = self._seed(serve, "미판정 글", "R", "yellow")
        self._finish(self.AR.start(team=None, reviewer="pete"))
        self.assertEqual(serve._STORE.feedback_map().get(ch, {}).get("n", 0), 0)   # 제안만 · 저장 안 함

    def test_empty_when_nothing_pending(self):
        serve = self._serve()
        run = self.AR.start(team=None, reviewer="pete")
        self.assertEqual(run["total"], 0)
        self.assertIn("empty", run)


class TestAutoReviewGate(unittest.TestCase):
    def test_email_allowlist_restricts(self):
        from prism import serve
        orig = os.environ.get("PRISM_AUTOREVIEW_EMAILS")
        self.addCleanup(lambda: os.environ.__setitem__("PRISM_AUTOREVIEW_EMAILS", orig) if orig is not None
                        else os.environ.pop("PRISM_AUTOREVIEW_EMAILS", None))
        os.environ["PRISM_AUTOREVIEW_EMAILS"] = "pete.ryu@axzcorp.com"
        self.assertEqual(serve._autoreview_emails(), {"pete.ryu@axzcorp.com"})
        os.environ.pop("PRISM_AUTOREVIEW_EMAILS", None)
        self.assertEqual(serve._autoreview_emails(), set())          # 미설정 = 빈 집합(운영 관리자 전체 허용)


if __name__ == "__main__":
    unittest.main()
