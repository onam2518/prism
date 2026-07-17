"""라우트 디스패치 가드: 짧은 접두가 더 긴 라우트를 가로채지 않는지.

실행: python3 -m pytest tests/test_route_dispatch.py -q  (stdlib unittest · 의존성 0)
배경: /reviewer 가 /reviewer-role(최종검수자 지정)을 삼켜 가입 핸들러의
"팀을 찾을 수 없습니다"가 응답되던 운영 버그(2026-07-16). GET/POST 모두
라우트 테이블(최장 접두 우선)로 전환된 뒤에도 순서 불변식 + 실호출 왕복으로 막는다.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDispatchShadowing(unittest.TestCase):
    """가로채기 가드: GET/POST 라우트 테이블의 최장 접두 우선 순서 불변식을 검증한다."""

    def _assert_longest_prefix(self, table, order, name):
        self.assertTrue(table, name + " 라우트 테이블이 비어 있음")
        self.assertEqual(list(order), sorted(table, key=len, reverse=True),
                         name + " 순서는 접두 길이 내림차순이어야 합니다(최장 접두 우선)")
        # 겹치는 접두쌍(예: /reviewer ⊂ /reviewer-role)이 실제로 긴 쪽 먼저 매칭되는지 전수 확인
        for short in table:
            for long in table:
                if long != short and long.startswith(short):
                    self.assertLess(list(order).index(long), list(order).index(short),
                                    f"{name}: {short!r} 가 {long!r} 보다 먼저 검사되면 가로챕니다")

    def test_get_table_longest_prefix(self):
        from prism import serve
        self._assert_longest_prefix(serve._GET_ROUTES, serve._GET_ORDER, "GET")

    def test_post_table_longest_prefix(self):
        from prism import serve
        self._assert_longest_prefix(serve._POST_ROUTES, serve._POST_ORDER, "POST")


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
