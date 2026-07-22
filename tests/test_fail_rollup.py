"""실패 트리아지 원장: 콜 실패(trace.fails)를 종류×모델×서비스×콜로 누적·조회.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
배경: fail_kind·trace.fails 는 수집되지만 집계 화면이 없어 배치 단위 실패 패턴
(gemini 침묵 빈응답 등)을 진단할 수 없었다(2026-07-15 리뷰 P2-7).
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestFailRollup(unittest.TestCase):
    def _serve(self):
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve

    def test_accumulate_and_aggregate(self):
        serve = self._serve()
        tr = {"model": "gemini-2.5-pro",
              "fails": [{"tag": "entities", "kind": "parse_empty"},
                        {"tag": "category", "kind": "api"}]}
        serve._log_fail_rollup(tr, service="뉴스", team=None)
        serve._log_fail_rollup({"model": "gemini-2.5-pro",
                                "fails": [{"tag": "entities", "kind": "parse_empty"}]},
                               service="뉴스", team=None)
        d = serve.fail_rollup_data(None, days=7)
        self.assertTrue(d["ok"])
        self.assertEqual(d["total"], 3)
        kinds = {x["k"]: x["n"] for x in d["by_kind"]}
        self.assertEqual(kinds["parse_empty"], 2)
        self.assertEqual(kinds["api"], 1)
        self.assertEqual(d["by_model"][0], {"k": "gemini-2.5-pro", "n": 3})
        self.assertEqual(d["by_service"][0], {"k": "뉴스", "n": 3})
        calls = {x["k"]: x["n"] for x in d["by_call"]}
        self.assertEqual(calls["entities"], 2)
        self.assertEqual(d["top"][0], {"kind": "parse_empty", "model": "gemini-2.5-pro",
                                       "service": "뉴스", "n": 2})

    def test_no_fails_noop(self):
        serve = self._serve()
        serve._log_fail_rollup({"model": "gpt-x", "fails": []}, service="뉴스", team=None)
        serve._log_fail_rollup({}, team=None)
        self.assertEqual(serve.fail_rollup_data(None, days=7)["total"], 0)

    def test_team_scoped(self):
        serve = self._serve()
        serve._log_fail_rollup({"model": "m", "fails": [{"tag": "summary", "kind": "api"}]},
                               service="s", team="team-A")
        self.assertEqual(serve.fail_rollup_data("team-A", days=7)["total"], 1)
        self.assertEqual(serve.fail_rollup_data("team-B", days=7)["total"], 0)

    def test_recent_failed_contents_and_resolve(self):
        """content_hash 전달 시 최근 실패 콘텐츠 등재(중복 = 최신 교체) ·
        무실패 성공 실행이 오면 목록에서 해소(카운터는 유지)."""
        serve = self._serve()
        serve._log_fail_rollup({"model": "m1", "fails": [{"tag": "summary", "kind": "api"}]},
                               service="뉴스", team=None, content_hash="h1", title="제목1")
        d = serve.fail_rollup_data(None, days=7)
        self.assertEqual([e["hash"] for e in d["recent"]], ["h1"])
        self.assertEqual(d["recent"][0]["kinds"], ["api"])
        self.assertEqual(d["recent"][0]["title"], "제목1")
        # 같은 콘텐츠 재실패 → 중복 등재 없이 최신 정보로 교체
        serve._log_fail_rollup({"model": "m2", "fails": [{"tag": "lead", "kind": "parse_empty"}]},
                               service="뉴스", team=None, content_hash="h1", title="제목1")
        d = serve.fail_rollup_data(None, days=7)
        self.assertEqual(len(d["recent"]), 1)
        self.assertEqual(d["recent"][0]["model"], "m2")
        self.assertEqual(d["recent"][0]["kinds"], ["parse_empty"])
        # 성공(무실패) 실행 → 목록에서 제거 · 실패 카운터는 그대로
        serve._log_fail_rollup({"model": "m2", "fails": []}, service="뉴스", team=None,
                               content_hash="h1", title="제목1")
        d = serve.fail_rollup_data(None, days=7)
        self.assertEqual(d["recent"], [])
        self.assertEqual(d["total"], 2)


if __name__ == "__main__":
    unittest.main()
