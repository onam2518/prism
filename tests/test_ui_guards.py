"""UI 회귀 가드: 바깥 클릭으로 닫히는 모달은 Esc 로도 닫힌다 · 폰트 풀 파일은 서브셋 밖 글자에만 받는다."""
import os
import re
import unittest

from prism import page

VENDOR = os.path.join(os.path.dirname(__file__), "..", "prism", "vendor")


class ModalEscTest(unittest.TestCase):
    # JS 키 핸들러가 Esc 를 따로 처리하는 모달(app-02 상세 단축키 · app-24 kwReviewKey)
    JS_ESC = ("detailOpen || cmpOpen", "kwTab==='run' && kwReview")

    def test_dismissable_modals_close_on_escape(self):
        for tag in re.findall(r'<div class="ds-dialog-backdrop"[^>]*>', page.PAGE):
            if "mousedown.self" not in tag or any(k in tag for k in self.JS_ESC):
                continue                                   # 닫기 불가 모달(주간 확인 등)은 대상 아님
            i = page.PAGE.index(tag)
            block = page.PAGE[i:i + 600]                   # 백드롭 또는 바로 안쪽 모달 상자
            self.assertIn("keydown.escape", block, tag[:120])
            self.assertIn("_escTop($event, $el)", block, "겹친 모달은 맨 위 하나만 닫혀야 함: " + tag[:120])


class FontRangeTest(unittest.TestCase):
    def test_full_fonts_have_unicode_range_without_space(self):
        for css in ("pretendard.css", "gmarket.css"):
            s = open(os.path.join(VENDOR, css), encoding="utf-8").read()
            for face in re.findall(r"@font-face\s*{[^}]*}", s):
                rng = re.search(r"unicode-range:\s*([^;]+);", face)
                self.assertIsNotNone(rng, css + " 의 @font-face 에 unicode-range 가 없으면 첫 방문마다 풀 폰트를 받는다")
                if ".subset." not in face:                 # 공백은 서브셋에만(풀 범위에 있으면 공백 하나로 풀 폰트 로드)
                    self.assertNotRegex(rng.group(1), r"U\+0020\b|U\+00(0|1)[0-9A-F]-00[2-9A-F][0-9A-F]", css)


class SelectSyncTest(unittest.TestCase):
    """x-for 로 만든 <option> 은 x-model 초기값보다 늦게 생겨 첫 옵션이 보인다(상태와 화면 불일치).
    설정 API 키에서 Timely 상태인데 'BizRouter' 가 보여 엉뚱한 키를 덮어쓰던 원인 · 옵션마다 selected 를 묶는다."""

    def test_dynamic_options_bind_selected(self):
        for sel in re.findall(r'<select[^>]*x-model[^>]*>.*?</select>', page.PAGE, re.S):
            if "x-for" not in sel:
                continue
            for opt in re.findall(r'<option\b[^>]*>', sel):
                if re.search(r'(?:x-bind)?:value=', opt):
                    self.assertIn("selected", opt, opt[:160])


if __name__ == "__main__":
    unittest.main()
