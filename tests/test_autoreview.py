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

    def test_assigned_includes_auto_decided_yellow_first(self):
        # 배정 미검수는 자동 확정(G/R · 비-YELLOW)도 대상 · YELLOW 가 앞에(진짜 검수 필요 우선)
        serve = self._serve()
        self._seed(serve, "자동확정 배정", "G", "auto", assign_to="pete")   # 비-YELLOW
        self._seed(serve, "검수필요 배정", "R", "yellow", assign_to="pete")  # YELLOW
        run = self.AR.start(team=None, reviewer="pete")
        self.assertEqual(run["scope"], "assigned")
        self.assertEqual(run["total"], 2)                                  # 자동확정도 포함(종전엔 빠졌다)
        s = self._finish(run)
        self.assertEqual(s["items"][0]["title"], "검수필요 배정")           # YELLOW 우선
        self.assertEqual({it["title"] for it in s["items"]}, {"자동확정 배정", "검수필요 배정"})

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


class TestAutoReviewPersistence(_Base):
    """초안은 콘텐츠 검수처럼 저장 계층에 상시 적재된다 — 나갔다 와도·재시작해도 유지 ·
    재실행하면 이미 초안 있는 건 스킵(같은 걸 두 번 판정하지 않는다)."""

    def test_drafts_persisted_to_store(self):
        serve = self._serve()
        ch = self._seed(serve, "배정 글", "R", "yellow", assign_to="pete")
        self._finish(self.AR.start(team=None, reviewer="pete"))
        saved = serve._STORE.ai_drafts("pete")
        self.assertIn(ch, saved)
        self.assertEqual(saved[ch]["verdict"], "bad")
        self.assertEqual(saved[ch]["model"], "claude-opus-5")
        self.assertIn("grade", saved[ch]["elements"])
        self.assertEqual(serve._STORE.ai_drafts("other"), {})       # 검수자별 격리

    def test_rerun_skips_already_drafted(self):
        serve = self._serve()
        self._seed(serve, "배정 글", "R", "yellow", assign_to="pete")
        self._finish(self.AR.start(team=None, reviewer="pete"))       # 1회차: 초안 적재
        run2 = self.AR.start(team=None, reviewer="pete")             # 2회차: 이미 초안(정확/수정) 있음 → 없음
        self.assertEqual(run2["total"], 0)
        self.assertIn("empty", run2)

    def test_rerun_retries_failed_drafts(self):
        # 라우터 실패로 verdict 빈 초안('직접 검수')은 재실행 시 다시 판정한다(에디 케이스 2026-08-25).
        serve = self._serve()
        ch = self._seed(serve, "실패했던 글", "G", "yellow", assign_to="pete")
        serve._STORE.save_ai_draft(ch, "pete", {"verdict": "", "confidence": 0,
            "reason": "심판 모델 호출 실패 · 직접 검수하세요", "elements": [], "model": "claude-opus-5"})
        run = self.AR.start(team=None, reviewer="pete")
        self.assertEqual(run["total"], 1)                            # 실패분은 재판정 대상(스킵 안 함)
        s = self._finish(run)
        self.assertEqual(s["items"][0]["ai"]["verdict"], "good")     # 다시 돌려 정상 판정됨
        self.assertEqual(serve._STORE.ai_drafts("pete")[ch]["verdict"], "good")   # 저장본도 갱신(upsert)

    def test_inbox_survives_memory_wipe(self):
        # 배포/재시작로 _RUNS(메모리)가 비어도 저장된 초안은 inbox 로 그대로 복원된다(Pete 의 요지).
        serve = self._serve()
        ch = self._seed(serve, "배정 글", "R", "yellow", assign_to="pete")
        self._finish(self.AR.start(team=None, reviewer="pete"))
        self.AR._RUNS.clear()                                        # 서버 재시작 시뮬레이션
        inb = self.AR.inbox(team=None, reviewer="pete")
        self.assertTrue(inb["ok"])
        self.assertEqual([it["title"] for it in inb["items"]], ["배정 글"])
        self.assertEqual(inb["items"][0]["ai"]["verdict"], "bad")
        self.assertEqual(inb["items"][0]["grade"], "R")             # 등급 dot 원천(콘텐츠 관리 차용)
        self.assertEqual(inb["items"][0]["judged"], "")             # 아직 확정 안 함
        self.assertEqual(inb["assigned"], 1)                        # 진척 스트립 '내 배정' 수

    def test_inbox_marks_confirmed(self):
        serve = self._serve()
        ch = self._seed(serve, "배정 글", "G", "yellow", assign_to="pete")
        self._finish(self.AR.start(team=None, reviewer="pete"))
        serve._STORE.save_feedback(ch, "뉴스", "배정 글", "good", "review", "AI 초안 확인", time.time(), reviewer="pete")
        serve._agg_bump()
        inb = self.AR.inbox(team=None, reviewer="pete")
        self.assertEqual(inb["items"][0]["judged"], "good")         # 확정분은 '반영됨' 으로

    def test_inbox_unconfirmed_first(self):
        serve = self._serve()
        a = self._seed(serve, "확정할 글", "G", "yellow", assign_to="pete")
        self._seed(serve, "안 본 글", "R", "yellow", assign_to="pete")
        self._finish(self.AR.start(team=None, reviewer="pete"))
        serve._STORE.save_feedback(a, "뉴스", "확정할 글", "good", "review", "", time.time(), reviewer="pete")
        serve._agg_bump()
        inb = self.AR.inbox(team=None, reviewer="pete")
        self.assertEqual(inb["items"][0]["title"], "안 본 글")        # 미확정 먼저
        self.assertEqual(inb["items"][-1]["title"], "확정할 글")       # 확정분은 뒤로


class TestAutoReviewFailReason(unittest.TestCase):
    """판정 실패 시 사유를 초안에 남긴다 — 종전엔 버려서 '직접 검수'만 떠 원인 파악이 안 됐다
    (에디 2026-08-25: 라우터 키 401 인데 화면엔 이유가 없었다)."""

    def test_auth_fail_surfaces_key_hint(self):
        from prism import autoreview as AR

        class _FailLLM:
            def complete_json(self, system, user, tag=""):
                return {"_fail": "HTTP401: unauthorized", "_fail_kind": "auth"}, None

        d = AR._judge_one(_FailLLM(), {"content_ref": {"title": "x"}, "item_meta": {}, "quality_meta": {}})
        self.assertEqual(d["verdict"], "")
        self.assertEqual(d["fail_kind"], "auth")
        self.assertIn("PRISM_TIMELY_KEY", d["reason"])       # 원인·조치가 화면에 바로 보이게

    def test_unknown_fail_generic_reason(self):
        from prism import autoreview as AR

        class _FailLLM:
            def complete_json(self, system, user, tag=""):
                return {"_fail": "boom", "_fail_kind": "weird"}, None

        d = AR._judge_one(_FailLLM(), {"content_ref": {}, "item_meta": {}, "quality_meta": {}})
        self.assertEqual(d["verdict"], "")
        self.assertIn("직접 검수", d["reason"])


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
