"""position:fixed 팝오버가 transform 걸린 조상 안에서 화면 밖으로 나가지 않는지 검사(2026-08-03).

배경: `.panel:hover{transform:translateY(-3px)}` 때문에 패널이 fixed 자식의 포함 블록이 된다.
mpMenuBox 가 잰 값은 뷰포트 좌표인데 기준이 패널로 바뀌므로 패널의 좌상단이 한 번 더 더해져
드롭다운이 화면 밖으로 나갔다. 버튼을 누르는 순간은 커서가 반드시 그 패널 위라 항상 재현됐다.
(증상: 필터를 눌러도 안 보이다가 커서를 빼면 튀어나오고 곧 바깥 클릭으로 닫힘)

해법: 열린 드롭다운을 품은 패널은 호버 리프트를 멈춘다(.panel:hover:has(...){transform:none}).
이 테스트는 "fixed 로 띄우는 팝오버를 새로 만들면 그 해제 선택자도 함께 넣는가"를 강제한다.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = os.path.join(ROOT, "prism/vendor/app.css")

# 팝오버 클래스 → 그 팝오버가 '열림'을 나타내는 선택자 조각(해제 규칙에 반드시 등장해야 한다)
FIXED_POPOVERS = {
    "mpick__menu": ".mpick__btn[aria-expanded=\"true\"]",
    "fpop__panel": ".fpop__btn.is-open",
}


def _css():
    with open(CSS, encoding="utf-8") as fh:
        return fh.read()


class TestPopoverAnchor(unittest.TestCase):
    def test_known_fixed_popovers_are_declared(self):
        """알려진 팝오버가 실제로 position:fixed 로 떠 있는지(전제 확인)."""
        css = _css()
        for cls in FIXED_POPOVERS:
            self.assertIn(".%s{position:fixed" % cls, css,
                          "%s 가 더 이상 fixed 가 아니면 이 테스트의 전제를 갱신하세요" % cls)

    def test_hover_lift_is_disabled_while_open(self):
        """열린 팝오버를 품은 패널은 호버 리프트를 멈춘다 — 안 그러면 화면 밖으로 나간다."""
        css = _css()
        # transform:none 으로 되돌리는 규칙 블록을 찾는다
        blocks = re.findall(r"([^{}]*):has\(([^)]*)\)[^{}]*\{[^}]*transform:\s*none[^}]*\}", css)
        guarded = " ".join(sel + " " + inner for sel, inner in blocks)
        self.assertTrue(blocks, "`.panel:hover:has(...){transform:none}` 해제 규칙이 없습니다")
        for cls, open_sel in FIXED_POPOVERS.items():
            self.assertIn(open_sel, guarded,
                          "%s 의 열림 상태(%s)가 호버 리프트 해제 규칙에 없습니다 — "
                          "패널 위에서 열면 팝오버가 화면 밖으로 나갑니다" % (cls, open_sel))

    def test_no_new_fixed_popover_without_guard(self):
        """position:fixed 로 뜨는 팝오버가 새로 생기면 해제 규칙에도 등록돼 있어야 한다."""
        css = _css()
        found = set(re.findall(r"\.([A-Za-z0-9_-]+)\{position:fixed", css))
        # 팝오버가 아닌 fixed(전역 오버레이·토스트 등)는 패널 안에 놓이지 않으므로 대상이 아니다
        not_popover = {
            m for m in found
            if not (m.endswith("__menu") or m.endswith("__panel") or m.endswith("__pop"))
        }
        unknown = sorted(found - not_popover - set(FIXED_POPOVERS))
        self.assertEqual(
            unknown, [],
            "패널 안에서 fixed 로 뜨는 팝오버가 새로 생겼습니다. app.css 의 "
            "`.panel:hover:has(...){transform:none}` 에 열림 상태 선택자를 추가하고 "
            "이 테스트의 FIXED_POPOVERS 에도 등록하세요: %r" % (unknown,),
        )


if __name__ == "__main__":
    unittest.main()
