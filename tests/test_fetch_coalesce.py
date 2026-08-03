"""동시 중복 GET 합치기 규약(2026-08-03).

부팅 시 같은 GET 이 여러 곳에서 동시에 나갔다(운영 실측 · 콘텐츠 검수 화면 기준):
  /auth 4회 · /learn-report 4회 · /dict 3회 · /dashboard 2회 · /raw 2회 (API 29회 중 11회가 중복)
_afetch 가 진행 중인 같은 URL 의 GET 을 나눠 쓰도록 해 왕복을 줄였다.

캐시가 아니라 '진행 중 요청 합치기'라는 점이 핵심이다 — 끝난 요청은 다음에 새로 나가야
목록 새로고침이 여전히 동작한다. 쓰기는 합치면 안 된다(두 번 눌렀으면 두 번 보낸다).
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "prism/vendor/app-03-opendetail.js")


def _src():
    with open(SRC, encoding="utf-8") as fh:
        return fh.read()


def _fn_body(src, name):
    """`name(...) {` 부터 중괄호 균형이 맞을 때까지."""
    i = src.index(name + "(")
    i = src.index("{", i)
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
        j += 1
    raise AssertionError("본문을 찾지 못함: " + name)


class TestFetchCoalesce(unittest.TestCase):
    def test_only_get_is_coalesced(self):
        """쓰기까지 합치면 판정·저장이 조용히 한 번만 나간다."""
        body = _fn_body(_src(), "async _afetch")
        self.assertIn("!== 'GET'", body)
        self.assertIn("_afetchOnce", body)

    def test_response_is_cloned_for_each_caller(self):
        """본문 스트림은 한 번만 읽힌다 — clone 없이 나눠 주면 두 번째 .json() 이 터진다."""
        body = _fn_body(_src(), "async _afetch")
        self.assertGreaterEqual(body.count(".clone()"), 2)

    def test_inflight_entry_is_released(self):
        """끝난 요청까지 남기면 캐시가 된다 — 새로고침이 안 먹는다."""
        body = _fn_body(_src(), "async _afetch")
        self.assertIn("finally", body)
        self.assertIn("delete this._inflight[url]", body)

    def test_retry_logic_survived_the_split(self):
        """401 → 토큰 갱신 → 1회 재시도는 _afetchOnce 로 옮겨 갔을 뿐 사라지면 안 된다."""
        once = _fn_body(_src(), "async _afetchOnce")
        self.assertIn("r.status === 401", once)
        self.assertIn("authRefresh()", once)

    def test_key_includes_query_string(self):
        """/raw?limit=200 과 /raw?limit=2000 은 다른 요청이다 — url 전체가 키여야 한다."""
        body = _fn_body(_src(), "async _afetch")
        self.assertIn("this._inflight[url]", body)
        self.assertNotIn("split('?')", body)


if __name__ == "__main__":
    unittest.main()
