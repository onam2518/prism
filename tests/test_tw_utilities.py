"""사전 빌드된 Tailwind 산출물(prism/vendor/tw.css)이 마크업보다 뒤처지지 않는지 검사(2026-08-03).

배경: 엔티티 관련성 라벨 블록이 `class="flex flex-col gap-1"` 로 세로 스택을 의도했는데
tw.css 에 `.flex-col` 이 없어(빌드가 마크업보다 뒤처짐) 행들이 가로로 서고, 이름 길이마다
제각각 접혀 버튼·집계가 어긋났다. `space-y-1`·`space-y-2` 도 같은 이유로 무동작이었다.

tw.css 는 `scripts/build_tailwind.sh`(npx tailwindcss) 산출물이라 커밋 시 자동 재생성되지 않는다.
그래서 "마크업이 쓰는 유틸리티가 어딘가에 실제로 정의돼 있는가"를 여기서 잠근다 —
빠뜨리면 화면이 조용히 깨지는 대신 이 테스트가 먼저 깨진다.

고치는 법 둘 중 하나:
  1) `bash scripts/build_tailwind.sh` 로 tw.css 재생성
  2) 그 블록을 app.css 전용 클래스로 작성 (의존성 0 원칙에 더 맞다 · .entlab/.stack-* 가 그 예)
"""
import glob
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 값이 붙는 유틸리티 접두(반드시 '-' 가 뒤따른다). 프로젝트 자체 클래스(w-run, ca-* 등)와
# 접두가 겹칠 수 있어, 최종 판정은 '어느 스타일시트에도 정의가 없는가'로 한다.
TW_VALUE = re.compile(
    r"^(flex|grid|items|justify|self|content|place|gap|space|order|col|row|"
    r"w|h|min|max|p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|"
    r"text|font|leading|tracking|whitespace|break|align|list|indent|"
    r"bg|border|rounded|ring|shadow|opacity|overflow|cursor|outline|"
    r"top|left|right|bottom|z|inset|basis|grow|shrink|divide|object|"
    r"transition|transform|animate|filter|translate|rotate|scale|origin)-"
)
# 값 없이 단독으로 쓰는 유틸리티
TW_BARE = {
    "flex", "grid", "block", "inline", "inline-flex", "inline-block", "hidden",
    "table", "contents", "truncate", "antialiased", "transform", "filter",
    "relative", "absolute", "fixed", "sticky", "static", "sr-only",
    "uppercase", "italic", "underline",
}
# x-bind:class / :class 의 JS 표현식은 리터럴이 아니므로 제외한다(앞에 - 또는 : 가 붙는다)
CLASS_ATTR = re.compile(r'(?<![-:\w])class="([^"]*)"')


def _class_names(css):
    return {m.group(1).replace("\\", "") for m in re.finditer(r"\.((?:\\.|[A-Za-z0-9_-])+)", css)}


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _defined():
    """페이지가 실제로 싣는 모든 정의 원천: vendor/*.css + UI 조각 안의 <style> 블록."""
    tw = _class_names(_read(os.path.join(ROOT, "prism/vendor/tw.css")))
    own = set()
    for f in glob.glob(os.path.join(ROOT, "prism/vendor/*.css")):
        if os.path.basename(f) == "tw.css":
            continue
        own |= _class_names(_read(f))
    for f in glob.glob(os.path.join(ROOT, "prism/ui/*.html")):
        for m in re.finditer(r"<style[^>]*>(.*?)</style>", _read(f), re.S):
            own |= _class_names(m.group(1))
    return tw, own


def _markup_classes():
    """content 스캔 대상(scripts/tailwind.config.js 와 같은 범위)의 리터럴 class 토큰."""
    files = (
        glob.glob(os.path.join(ROOT, "prism/ui/*.html"))
        + glob.glob(os.path.join(ROOT, "prism/vendor/app*.js"))
        + [os.path.join(ROOT, "prism/page.py")]
    )
    seen = {}
    for f in files:
        for m in CLASS_ATTR.finditer(_read(f)):
            for tok in m.group(1).split():
                seen.setdefault(tok, os.path.relpath(f, ROOT))
    return seen


class TestTailwindUtilitiesExist(unittest.TestCase):
    def test_no_undefined_utility_classes(self):
        tw, own = _defined()
        missing = sorted(
            "%s (%s)" % (tok, where)
            for tok, where in _markup_classes().items()
            if (TW_VALUE.match(tok) or tok in TW_BARE) and tok not in tw and tok not in own
        )
        self.assertEqual(
            missing, [],
            "마크업이 쓰는 유틸리티 클래스가 어느 스타일시트에도 없습니다 — 화면이 조용히 깨집니다. "
            "`bash scripts/build_tailwind.sh` 로 tw.css 를 재생성하거나 app.css 전용 클래스로 옮기세요: "
            + repr(missing),
        )

    def test_flex_col_case_stays_fixed(self):
        """이 사고의 원본 케이스. tw.css 에 flex-direction 이 없는 동안에는 flex-col 을 쓰면 안 된다."""
        if "flex-direction" in _read(os.path.join(ROOT, "prism/vendor/tw.css")):
            self.skipTest("tw.css 재생성됨 · flex-col 사용 가능")
        self.assertNotIn("flex-col", _markup_classes(),
                         "tw.css 에 .flex-col 이 없는 상태에서 마크업이 flex-col 을 씁니다")

    def test_entlabel_block_has_own_styles(self):
        """시안 A: 관련성 라벨 블록은 유틸리티가 아니라 전용 클래스로 정렬을 잡는다."""
        css = _read(os.path.join(ROOT, "prism/vendor/app.css"))
        self.assertIn(".entlab__row{display:grid", css)      # 컬럼 폭 고정 = 버튼 x좌표 정렬
        self.assertIn("container-type:inline-size", css)      # 창이 아니라 패널 폭에 반응
        from prism import page
        self.assertIn('class="entlab"', page.PAGE)
        self.assertIn('class="entlab__row"', page.PAGE)


if __name__ == "__main__":
    unittest.main()
