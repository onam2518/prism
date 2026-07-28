"""주간 본인 확인 · 주차가 넘어가면 검수자가 다시 확인해야 한다(사용자 결정 2026-07-28).

배경: 주간 가용 시간은 '그 주의 약속'인데, 검수운영 탭이 슈퍼관리자 전용이라 본인이
확인할 자리가 아예 없었다(관리자가 대신 눌러주는 표시였다). 주차 시작 이후 첫 로그인에서
한 번 받고, 확인 전에는 화면을 진행시키지 않는다.
"""
import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeSV:
    def __init__(self):
        self.reports = {}

    def _report_get(self, kind, team=None, default=None):
        return self.reports.get(kind, default if default is not None else {})

    def _report_save(self, kind, payload, team=None):
        self.reports[kind] = payload

    def _agg_bump(self):
        pass


class ConfirmBase(unittest.TestCase):
    def setUp(self):
        from prism import crewops as CRW
        from prism import weekops as WKO
        self.CRW, self.WKO = CRW, WKO
        self.sv = _FakeSV()
        # serve 가 import 시 주입한 _SV 를 잠깐 바꿔 쓴다 · None 으로 지우면 뒤따르는
        # 다른 테스트(test_crewops 등)가 통째로 깨진다 — 반드시 원래 값으로 되돌린다.
        prev_c, prev_w = CRW._SV, WKO._SV
        CRW._SV = self.sv
        WKO._SV = self.sv
        self.addCleanup(lambda: (setattr(CRW, "_SV", prev_c), setattr(WKO, "_SV", prev_w)))
        self.week = WKO.current_week()
        self.assertGreaterEqual(self.week, 1)


class NeedsConfirmTest(ConfirmBase):
    def test_new_reviewer_needs_confirm(self):
        r = self.CRW.needs_confirm("u1", None)
        self.assertTrue(r["needed"])
        self.assertEqual(r["week"], self.week)
        self.assertIn("profile", r)                       # 팝업이 채울 기본값

    def test_confirm_clears_it_for_this_week(self):
        self.CRW.confirm_week("u1", {"hours_per_week": 6, "workdays": [0, 1, 2]}, None)
        r = self.CRW.needs_confirm("u1", None)
        self.assertFalse(r["needed"])

    def test_previous_week_confirm_does_not_count(self):
        self.CRW.confirm_week("u1", {}, None)
        items = self.sv.reports[self.CRW.PROFILE_KIND]["items"]
        items["u1"]["confirmed_week"] = self.week - 1      # 지난주에 확인한 상태
        self.sv.reports[self.CRW.PROFILE_KIND] = {"items": items}
        self.assertTrue(self.CRW.needs_confirm("u1", None)["needed"])

    def test_confirm_saves_the_edited_schedule(self):
        self.CRW.confirm_week("u1", {"hours_per_week": 7.5, "workdays": [0, 4], "status": "leave"}, None)
        p = self.sv.reports[self.CRW.PROFILE_KIND]["items"]["u1"]
        self.assertEqual(p["hours_per_week"], 7.5)
        self.assertEqual(p["workdays"], [0, 4])
        self.assertEqual(p["status"], "leave")
        self.assertTrue(p["confirmed"])
        self.assertEqual(p["confirmed_week"], self.week)

    def test_client_cannot_forge_the_confirmed_week(self):
        """확인 주차는 서버가 정한다 — 본문으로 미래 주차를 밀어넣으면 영구 면제가 된다."""
        self.CRW.confirm_week("u1", {"confirmed_week": 9999}, None)
        p = self.sv.reports[self.CRW.PROFILE_KIND]["items"]["u1"]
        self.assertEqual(p["confirmed_week"], self.week)

    def test_anonymous_is_never_asked(self):
        self.assertFalse(self.CRW.needs_confirm("", None)["needed"])
        self.assertFalse(self.CRW.confirm_week("", {}, None)["ok"])

    def test_unconfirming_resets_the_week(self):
        self.CRW.confirm_week("u1", {}, None)
        self.CRW.set_profile("u1", {"confirmed": False}, None, by="admin")
        p = self.sv.reports[self.CRW.PROFILE_KIND]["items"]["u1"]
        self.assertEqual(p["confirmed_week"], 0)
        self.assertTrue(self.CRW.needs_confirm("u1", None)["needed"])


class RouteTest(unittest.TestCase):
    def test_get_route_registered_before_order_snapshot(self):
        """_GET_ORDER 는 파일 중간에서 확정된다 — 뒤에 등록하면 디스패치에서 빠진다."""
        from prism import serve
        self.assertIn("/crew-confirm", serve._GET_ORDER)
        self.assertIn("/crew-confirm", serve._POST_ORDER)

    def test_post_route_is_login_gated_not_super(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "prism", "serve.py"), encoding="utf-8") as f:
            s = f.read()
        self.assertIn('@_post_route("/crew-confirm", gate="login")', s)
        # uid 는 토큰에서만 — 본문 uid 를 믿으면 남의 확인을 대신 눌러줄 수 있다
        i = s.index('def _p_crew_confirm')
        body = s[i:i + 900]
        self.assertIn("h._bearer_uid()", body)


class MarkupTest(unittest.TestCase):
    def test_modal_present_and_not_dismissable(self):
        from prism import page
        self.assertIn("주차 일정 확인", page.PAGE)
        i = page.PAGE.index("wkConfirmOpen")
        nxt = page.PAGE.index("ds-dialog-backdrop", i)      # 이 모달 블록만(다음 모달 침범 방지)
        block = page.PAGE[i:nxt]
        # 닫기 경로가 없어야 확인 전 진행이 막힌다
        self.assertNotIn("wkConfirmOpen=false", block)
        self.assertNotIn("mousedown.self", block)
        self.assertNotIn("keydown.escape", block)

    def test_escalate_panel_removed(self):
        from prism import page
        self.assertNotIn("불일치 건 추가 배정", page.PAGE)
        self.assertNotIn("crewEscRes", page.PAGE)

    def test_alpine_expressions_have_balanced_quotes(self):
        """따옴표가 깨진 식은 렌더 시점에 조용히 죽는다(2026-07-28 실제 사고 2건).
        전 페이지를 스캔해 같은 사고를 막는다."""
        from prism import page
        bad = [m.group(1)[:80] for m in re.finditer(r'x-[a-z:.-]+="([^"]*)"', page.PAGE)
               if m.group(1).count("'") % 2]
        self.assertFalse(bad, f"홑따옴표가 맞지 않는 Alpine 식: {bad}")


if __name__ == "__main__":
    unittest.main()
