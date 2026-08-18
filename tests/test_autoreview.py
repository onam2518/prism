"""AI 초안 판정(실험실): 대기(YELLOW) 콘텐츠에 심판 모델 초안을 채우되, 이미 내가 판정한
건은 제외하고 골드는 애초에 대상이 아니며, 자동 커밋하지 않는다.

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
    """complete_json 이 콘텐츠 등급에 따라 결정적 초안을 주는 가짜 심판 모델."""
    mock = False

    def complete_json(self, system, user, tag=""):
        d = json.loads(user)
        bad = d.get("등급") == "R"                       # R = 수정 초안(테스트 결정성)
        return ({"verdict": "bad" if bad else "good", "confidence": 0.9,
                 "reason": "테스트 근거", "elements": ["grade"] if bad else []}, None)


class TestAutoReviewSuggest(unittest.TestCase):
    def _serve(self):
        os.environ["PRISM_BACKEND"] = "sqlite"
        os.environ["PRISM_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
        from prism import serve, autoreview as AR
        from prism.store import Store
        serve._STORE = Store(os.environ["PRISM_DB"])
        serve.Handler.server_mock = True
        # 심판 모델을 가짜로: llm_for_model 이 (판사, route) 반환
        self._orig = serve.llm_for_model
        serve.llm_for_model = lambda model, mock: (_FakeJudge(), "fake")
        self.addCleanup(lambda: setattr(serve, "llm_for_model", self._orig))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)
        self.AR = AR
        return serve

    def _seed(self, serve, title, grade, review, feedback_by=None):
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
        return ch

    def test_suggests_yellow_fills_verdict(self):
        serve = self._serve()
        self._seed(serve, "정확한 글", "G", "yellow")
        self._seed(serve, "고칠 글", "R", "yellow")
        self._seed(serve, "확정된 글", "G", "auto")           # YELLOW 아님 = 대상 아님
        r = self.AR.suggest(team=None, reviewer="pete", limit=10)
        self.assertTrue(r["ok"])
        self.assertEqual(r["model"], "claude-opus-5")
        titles = {it["title"]: it for it in r["items"]}
        self.assertEqual(set(titles), {"정확한 글", "고칠 글"})   # YELLOW 2건만
        self.assertEqual(titles["정확한 글"]["ai"]["verdict"], "good")
        self.assertEqual(titles["고칠 글"]["ai"]["verdict"], "bad")
        self.assertIn("grade", titles["고칠 글"]["ai"]["elements"])
        self.assertFalse(titles["정확한 글"]["sameModel"])       # 심판(opus5) != 콘텐츠(solar)

    def test_excludes_content_i_already_judged(self):
        serve = self._serve()
        self._seed(serve, "내가 본 글", "G", "yellow", feedback_by="pete")
        self._seed(serve, "안 본 글", "G", "yellow")
        r = self.AR.suggest(team=None, reviewer="pete", limit=10)
        self.assertEqual([it["title"] for it in r["items"]], ["안 본 글"])

    def test_does_not_commit_verdict(self):
        serve = self._serve()
        ch = self._seed(serve, "미판정 글", "R", "yellow")
        self.AR.suggest(team=None, reviewer="pete", limit=10)
        self.assertEqual(serve._STORE.feedback_map().get(ch, {}).get("n", 0), 0)   # 제안만 · 저장 안 함


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
