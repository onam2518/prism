"""최종검수자는 기초 검수 배정 대상이 아니다(사용자 결정 2026-07-28).

최종검수자는 기초 판정이 갈렸을 때 확정하는 2층 역할이라, 같은 콘텐츠의 기초 검수를
맡으면 자기 판정을 자기가 확정하게 된다. 자동(여력 비례·재배정·불일치 추가)과
수동(직접 지정) 어느 경로에서도 후보에 들어가면 안 된다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import crewops as CRW


class AssignablePoolTest(unittest.TestCase):
    def _m(self, uid, final=False):
        return {"id": uid, "name": uid.upper(), "is_final": final}

    def test_final_reviewer_dropped(self):
        pool = CRW._assignable([self._m("a"), self._m("f", True), self._m("b")])
        self.assertEqual([m["id"] for m in pool], ["a", "b"])

    def test_no_final_keeps_everyone(self):
        pool = CRW._assignable([self._m("a"), self._m("b")])
        self.assertEqual(len(pool), 2)

    def test_all_final_yields_empty(self):
        self.assertEqual(CRW._assignable([self._m("f", True)]), [])

    def test_missing_flag_is_treated_as_not_final(self):
        self.assertEqual(len(CRW._assignable([{"id": "x", "name": "X"}])), 1)


class AutoPathsUsePoolTest(unittest.TestCase):
    """세 자동 경로가 모두 _assignable 을 거치는지(한 곳만 빠지면 조용히 새어 나간다)."""

    def _src(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "prism", "crewops.py"), encoding="utf-8") as f:
            return f.read()

    def _fn(self, name):
        s = self._src()
        i = s.index(f"def {name}(")
        j = s.find("\ndef ", i + 1)
        return s[i:j if j > 0 else len(s)]

    def test_plan_distribute(self):
        self.assertIn("_assignable(", self._fn("plan_distribute"))

    def test_rebalance_takers(self):
        self.assertIn("_assignable(", self._fn("rebalance"))

    def test_escalate_pool(self):
        self.assertIn("_assignable(", self._fn("escalate_split"))


class ManualPathTest(unittest.TestCase):
    """수동 배정은 화면이 후보에서 빼되, API 직접 호출을 위해 서버도 막는다."""

    def _serve_src(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "prism", "serve.py"), encoding="utf-8") as f:
            return f.read()

    def test_both_assign_routes_filter(self):
        s = self._serve_src()
        calls = s.count("reviewers, dropped = _drop_finals(reviewers")
        self.assertEqual(calls, 2)                            # 개별 + 일괄

    def test_drop_finals_filters_and_reports(self):
        from prism import serve as S
        orig = S.reviewer_roles
        S.reviewer_roles = lambda team=None: {"f": "final"}
        self.addCleanup(lambda: setattr(S, "reviewer_roles", orig))
        keep, dropped = S._drop_finals(["a", "f", "b"], None)
        self.assertEqual(keep, ["a", "b"])
        self.assertEqual(dropped, ["f"])

    def test_drop_finals_survives_lookup_failure(self):
        from prism import serve as S
        orig = S.reviewer_roles

        def boom(team=None):
            raise RuntimeError("원장 조회 실패")

        S.reviewer_roles = boom
        self.addCleanup(lambda: setattr(S, "reviewer_roles", orig))
        keep, dropped = S._drop_finals(["a"], None)
        self.assertEqual(keep, ["a"])            # 배정 자체를 막지는 않는다
        self.assertEqual(dropped, [])


class MarkupTest(unittest.TestCase):
    def test_pickers_use_candidates_not_all_members(self):
        from prism import page
        # 후보 목록(x-for)은 assignCandidates 로 · 이름 해석은 assignMembers 유지
        self.assertNotIn('x-for="m in assignMembers"', page.PAGE)
        self.assertIn('x-for="m in assignCandidates"', page.PAGE)

    def test_assign_log_shows_names_not_uuid(self):
        from prism import page
        self.assertNotIn("(a.reviewers || []).join(', ')", page.PAGE)
        self.assertIn("bulkNames(a.reviewers)", page.PAGE)

    def test_panel_titles_renamed(self):
        from prism import page
        self.assertIn("<b>자동 배정</b>", page.PAGE)
        self.assertIn("<b>수동 배정</b>", page.PAGE)
        self.assertNotIn("여력 비례 배정", page.PAGE)
        self.assertNotIn("직접 지정 배정", page.PAGE)


if __name__ == "__main__":
    unittest.main()
