"""라우트 디스패치 가드: startswith 접두 매칭이 뒤의 더 긴 라우트를 가로채지 않는지.

실행: python3 -m pytest tests/test_route_dispatch.py -q  (stdlib unittest · 의존성 0)
배경: /reviewer 가 /reviewer-role(최종검수자 지정)을 삼켜 가입 핸들러의
"팀을 찾을 수 없습니다"가 응답되던 운영 버그(2026-07-16). 같은 계열 사고를
전수 정적 검사 + 실호출 왕복으로 막는다.
"""
import inspect
import json
import os
import re
import sys
import tempfile
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDispatchShadowing(unittest.TestCase):
    """가로채기 가드. do_POST(아직 if/elif): startswith 나열 순서에서 앞선 짧은 접두가
    뒤의 긴 라우트를 삼키면 안 된다(부정 가드 `not ...`은 제외). do_GET(라우트 테이블):
    최장 접두 우선 순서 불변식을 검증한다."""

    def _routes(self, fn):
        """긍정 라우트 나열 순서와, `not startswith(...)` 로 명시 제외된 접두 집합."""
        src = inspect.getsource(fn)
        pat = re.compile(r'(not\s+)?self\.path\.startswith\("([^"]+)"')
        pos, neg = [], set()
        for m in pat.finditer(src):
            if m.group(1):
                neg.add(m.group(2))
            else:
                pos.append(m.group(2))
        return pos, neg

    def test_no_prefix_shadowing_post(self):
        from prism import serve
        routes, excluded = self._routes(serve.Handler.do_POST)
        self.assertTrue(routes, "do_POST 라우트 추출 실패")
        for i, short in enumerate(routes):
            for long in routes[i + 1:]:
                if any(long.startswith(n) for n in excluded):
                    continue                     # 짧은 쪽 조건이 명시적으로 비켜줌
                self.assertFalse(
                    long != short and long.startswith(short),
                    f"do_POST: 앞선 {short!r} 가 뒤의 {long!r} 요청을 가로챕니다 · "
                    f"긴 라우트를 앞으로 옮기거나 짧은 쪽 조건에서 제외하세요")

    def test_get_table_longest_prefix(self):
        """do_GET 은 라우트 테이블(_GET_ROUTES) + 최장 접두 우선(_GET_ORDER) 디스패치.
        순서 불변식(길이 내림차순)만 지켜지면 가로채기가 구조적으로 불가능하다."""
        from prism import serve
        self.assertTrue(serve._GET_ROUTES, "GET 라우트 테이블이 비어 있음")
        self.assertEqual(list(serve._GET_ORDER),
                         sorted(serve._GET_ROUTES, key=len, reverse=True),
                         "_GET_ORDER 는 접두 길이 내림차순이어야 합니다(최장 접두 우선)")
        # 겹치는 접두쌍(예: /entdict ⊂ /entdict-lookup)이 실제로 긴 쪽 먼저 매칭되는지 표본 확인
        for short in serve._GET_ROUTES:
            for long in serve._GET_ROUTES:
                if long != short and long.startswith(short):
                    self.assertLess(serve._GET_ORDER.index(long), serve._GET_ORDER.index(short),
                                    f"{short!r} 가 {long!r} 보다 먼저 검사되면 가로챕니다")


class TestReviewerRoleRoundtrip(unittest.TestCase):
    """POST /reviewer-role 이 가입 핸들러(/reviewer)가 아니라 역할 지정에 닿는지 실호출 왕복."""

    @classmethod
    def setUpClass(cls):
        os.environ["PRISM_BACKEND"] = "sqlite"
        os.environ["PRISM_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
        from http.server import ThreadingHTTPServer
        from prism import serve
        serve._STORE = None
        serve.Handler.server_mock = True
        cls.serve = serve
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.serve._STORE = None

    def _post(self, path, obj):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=json.dumps(obj).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())

    def test_role_assignment_reaches_handler(self):
        status, res = self._post("/reviewer-role", {"id": "uid-9", "role": "final"})
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res.get("final_reviewers"), ["uid-9"])   # 가입 핸들러였다면 이 키가 없다
        self.assertNotIn("초대코드", str(res.get("error") or ""))


if __name__ == "__main__":
    unittest.main()
