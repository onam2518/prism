"""화면(mod) 단위 지연 렌더 규약(2026-08-03).

배경: 전 화면 마크업이 한 문서에 있어 첫 로드에 DOM 21,908개 · 그중 Alpine 바인딩 14,580개가
한꺼번에 만들어졌다. 콘텐츠 검수 화면 기준 자기 화면 노드는 7,671개뿐 — 55%가 보지도 않는
화면이었다. 숨은 화면의 x-init 까지 다 돌아 /learn-report·/golden-status 같은 남의 화면
데이터도 부팅 때 받아 왔다.

규약: 화면 단위는 <template x-if="mod === '…'"> 로 감싸 필요할 때 만든다.
탭 조건은 x-if 로 올리지 않고 안쪽 x-show 로 남긴다 — 탭 전환마다 DOM 재생성·x-init
재실행이 생기면 화면 안 이동이 더 느려진다.
"""
import glob
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

UI = glob.glob(os.path.join(ROOT, "prism/ui/*.html"))


def _read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


class TestLazyScreens(unittest.TestCase):
    def test_no_screen_is_gated_by_x_show(self):
        """x-show 로 두면 숨은 화면도 DOM·바인딩·x-init 이 다 산다."""
        offenders = []
        for f in UI:
            for m in re.finditer(r'x-show="([^"]*\bmod\s*===[^"]*)"', _read(f)):
                offenders.append("%s: %s" % (os.path.basename(f), m.group(1)))
        self.assertEqual(offenders, [],
                         "화면 단위 조건은 <template x-if> 로 감싸세요: %r" % (offenders,))

    def test_screens_are_wrapped_in_template_x_if(self):
        from prism import page
        n = len(re.findall(r'<template x-if="mod\s*===', page.PAGE))
        self.assertGreaterEqual(n, 40, "화면 단위 x-if 블록이 사라졌습니다(현재 %d개)" % n)

    def test_x_if_template_has_exactly_one_root_element(self):
        """<template x-if> 는 루트 엘리먼트가 하나여야 한다 — 여러 개면 첫 개만 렌더된다."""
        bad = []
        for f in UI:
            src = _read(f)
            for m in re.finditer(r'<template x-if="mod\s*===[^"]*">', src):
                body, depth, i = "", 0, m.end()
                while i < len(src):                       # 짝 맞는 </template> 까지
                    t = re.match(r"<(/?)template\b", src[i:])
                    if t:
                        depth += -1 if t.group(1) else 1
                        if depth < 0:
                            break
                    body += src[i]
                    i += 1
                roots, d = 0, 0
                for tm in re.finditer(r"<(/?)([a-zA-Z][\w-]*)([^>]*?)(/?)>", body):
                    close, tag, _a, self_ = tm.groups()
                    if self_ or tag.lower() in ("input", "br", "img", "hr", "link", "meta"):
                        if d == 0:
                            roots += 1
                        continue
                    if close:
                        d -= 1
                    else:
                        if d == 0:
                            roots += 1
                        d += 1
                if roots != 1:
                    bad.append("%s (루트 %d개)" % (os.path.basename(f), roots))
        self.assertEqual(bad, [], "x-if 템플릿의 루트가 하나가 아닙니다: %r" % (bad,))

    def test_tab_conditions_stay_on_x_show(self):
        """화면 x-if 조건에 탭까지 넣으면 탭 전환마다 재생성·x-init 재실행이 난다.
        (정책 팔레트의 polTab x-if 처럼 화면 게이트가 아닌 자체 x-if 는 대상이 아니다)"""
        from prism import page
        leaked = [c for c in re.findall(r'<template x-if="(mod\s*===[^"]*)"', page.PAGE)
                  if re.search(r"\b\w+Tab\s*===", c)]
        self.assertEqual(leaked, [], "탭 조건은 안쪽 x-show 로 남기세요: %r" % (leaked,))


if __name__ == "__main__":
    unittest.main()
