"""x-bind:disabled 에 '없는 키'가 들어가 버튼이 처음부터 잠기는 회귀 차단.

배경(2026-07-28): 메타 생성 실패 콘텐츠의 '재실행' 버튼이 한 번도 누르지 않았는데
전건 비활성이었다. 원인은 `x-bind:disabled="rerunBusy[f.hash]"` —
rerunBusy 는 {} 로 시작하므로 모든 행이 '없는 키' 접근이고, Alpine 3.14 는 그 결과를
falsy 로 취급하지 않아 disabled 를 걸어버린다(브라우저 실측: obj.missing → disabled=true,
정의된 false·정의된 undefined → 정상). !! 로 불린 변환하면 해결된다.

브라우저 없이 마크업만 정적 검사한다(의존성 0).
실행: python3 -m pytest tests/ -q
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prism", "ui")
_BIND = re.compile(r'x-bind:disabled="([^"]*)"')
# 안전한 형태: 불린 변환(!!·!) · 비교 · 논리 결합 · 길이 비교 등 결과가 확정 불린인 식
_SAFE = re.compile(r"!!|^!|[=<>]|&&|\|\||\.length|\?")


class TestDisabledBinding(unittest.TestCase):
    def _exprs(self):
        for name in sorted(os.listdir(_UI)):
            if not name.endswith(".html"):
                continue
            with open(os.path.join(_UI, name), encoding="utf-8") as f:
                for i, line in enumerate(f.read().split("\n"), 1):
                    for m in _BIND.finditer(line):
                        yield name, i, m.group(1).strip()

    def test_index_access_is_boolean_coerced(self):
        """객체 인덱스(obj[key])를 그대로 넘기면 '없는 키'에서 버튼이 영구 비활성된다."""
        bad = [f"{n}:{i} · {e}" for n, i, e in self._exprs()
               if "[" in e and not _SAFE.search(e)]
        self.assertEqual(bad, [], "x-bind:disabled 에 객체 인덱스를 그대로 넘겼습니다 · "
                                 "!! 로 불린 변환하세요(없는 키에서 버튼이 잠깁니다): " + str(bad))

    def test_known_rerun_buttons_are_coerced(self):
        """실제로 사고가 났던 두 곳(실패 콘텐츠·콘텐츠 목록의 재실행)을 못 박아 둔다."""
        found = [e for _, _, e in self._exprs() if "rerunBusy" in e]
        self.assertTrue(found, "재실행 버튼 바인딩을 찾지 못했습니다(경로 변경 시 이 테스트를 갱신하세요)")
        for e in found:
            self.assertTrue(e.startswith("!!"), f"불린 변환 누락: {e}")


if __name__ == "__main__":
    unittest.main()
