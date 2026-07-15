"""마크업 태그 균형 가드: 미닫힘 <template>/<div>/<section> 이 조용히 화면을 삼키는 회귀 차단.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
배경(2026-07-15): 리베이스 봉합 실수로 <template> 2개가 닫히지 않아 이후 마크업 전체가
template 의 비활성 내용물로 흡수 → 콘텐츠 검수·스튜디오 화면이 콘솔 에러 없이 통째로 소멸.
브라우저 파서는 이런 오류를 조용히 넘기므로 정적 균형 검사가 유일한 조기 경보다.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TAGS = ("template", "div", "section", "table", "thead", "tbody", "tr", "ul", "select")


def _balance(markup: str, tag: str):
    """(최종 깊이, 미닫힘 여는 태그의 등장 순번 목록, 초과 닫힘 순번 목록)."""
    opens, overs = [], []
    depth = 0
    pat = re.compile(rf"<{tag}\b[^>]*?(/?)>|</{tag}>")
    for n, m in enumerate(pat.finditer(markup), 1):
        if m.group(0).startswith(f"</{tag}"):
            depth -= 1
            if opens:
                opens.pop()
            if depth < 0:
                overs.append(n)
                depth = 0
        elif m.group(1) != "/":                    # 자기 닫힘(<div/>)은 제외
            depth += 1
            opens.append(n)
    return depth, opens, overs


class TestMarkupBalance(unittest.TestCase):
    def _check(self, markup: str, label: str):
        for tag in TAGS:
            depth, opens, overs = _balance(markup, tag)
            self.assertEqual(depth, 0,
                             f"{label}: <{tag}> 미닫힘 {len(opens)}개(등장 순번 {opens[:5]}) — "
                             "이후 마크업이 조용히 삼켜져 화면이 사라질 수 있습니다")
            self.assertEqual(overs, [],
                             f"{label}: </{tag}> 초과 닫힘(순번 {overs[:5]})")

    def test_desktop_page(self):
        from prism.page import PAGE
        self._check(PAGE, "page.py PAGE")

    def test_mobile_page(self):
        from prism.page_mobile import MOBILE_PAGE
        self._check(MOBILE_PAGE, "page_mobile.py MOBILE_PAGE")


if __name__ == "__main__":
    unittest.main()
