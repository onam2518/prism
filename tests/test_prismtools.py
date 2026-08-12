"""도구 계층(prismtools) 회귀 테스트 (2026-08-12).

이 계층은 내부 검수 보조(트랙 A)와 외부 MCP(트랙 B)가 공유한다. 여기서 지키는 규칙이
깨지면 두 앞단이 동시에 깨지므로, 규칙 자체를 단언한다.

지키는 것(전부 2026-08-11 감사에서 실제로 뚫렸던 것들):
  · 팀 없이 도구가 돌지 않는다 — 저장 계층은 team 이 falsy 면 전 팀을 준다(H1)
  · 도구 사용자가 team 을 지정할 수 없다 — 지정 가능하면 그 자체가 교차 팀 통로다
  · 잘리면 말한다 — 조용한 절단은 '다 봤다' 로 읽힌다(P3)
  · 골드 문항은 어떤 경로로도 안 나간다 — 나가면 측정 대상이 사람이 아니라 모델이 된다
  · 정수 인자는 클램프하고 예외 원문을 응답에 싣지 않는다(H3)

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import dictionaries as D
from prism import prismtools as PT

TEAM = "team-1"


class TestTeamScope(unittest.TestCase):
    def test_tool_refuses_to_run_without_a_team(self):
        for t in (None, "", "   "):
            r = PT.call("get_taxonomy", {"kind": "intent"}, team=t)
            self.assertIn("error", r, f"team={t!r} 인데 실행됐다")

    def test_caller_supplied_team_in_args_is_ignored(self):
        """도구 사용자(모델·외부 클라이언트)가 팀을 바꿔치기할 수 없어야 한다."""
        r = PT.call("get_taxonomy", {"kind": "intent", "team": "다른팀"}, team=TEAM)
        self.assertNotIn("error", r)
        self.assertIn("values", r)

    def test_unknown_tool_is_rejected(self):
        self.assertIn("error", PT.call("없는도구", {}, team=TEAM))


class TestGuards(unittest.TestCase):
    def test_qint_clamps_and_falls_back(self):
        self.assertEqual(PT.qint("abc", 20, 1, 100), 20)     # 못 읽으면 기본값
        self.assertEqual(PT.qint(None, 20, 1, 100), 20)
        self.assertEqual(PT.qint(-5, 20, 1, 100), 1)         # 하한
        self.assertEqual(PT.qint(9999, 20, 1, 100), 100)     # 상한
        self.assertEqual(PT.qint("50", 20, 1, 100), 50)

    def test_gold_items_never_pass_through(self):
        items = [{"hash": "gold:ok:abc"}, {"hash": "goldf:bad:def"}, {"hash": "real-hash"}]
        self.assertEqual(PT.strip_gold(items), [{"hash": "real-hash"}])
        self.assertTrue(PT.is_gold({"hash": "gold:ok:x"}))
        self.assertTrue(PT.is_gold("goldf:bad:x"))
        self.assertFalse(PT.is_gold({"hash": "abc123"}))

    def test_envelope_reports_truncation(self):
        e = PT.envelope([1, 2, 3, 4, 5], 3)
        self.assertEqual(e["items"], [1, 2, 3])
        self.assertEqual(e["total"], 5)
        self.assertTrue(e["truncated"])

    def test_envelope_is_honest_when_nothing_was_cut(self):
        e = PT.envelope([1, 2], 10)
        self.assertFalse(e["truncated"])
        self.assertEqual(e["total"], 2)

    def test_errors_do_not_leak_internals(self):
        """예외 원문·스택이 응답에 실리면 안 된다."""
        spec = PT.TOOLS["get_taxonomy"]
        orig = spec["fn"]
        spec["fn"] = lambda **kw: (_ for _ in ()).throw(RuntimeError("내부 경로 /secret/path"))
        self.addCleanup(lambda: spec.__setitem__("fn", orig))
        r = PT.call("get_taxonomy", {"kind": "intent"}, team=TEAM)
        self.assertIn("error", r)
        self.assertNotIn("secret", str(r))


class TestTaxonomy(unittest.TestCase):
    def test_every_kind_returns_values(self):
        for kind in PT.TAXONOMY_KINDS:
            r = PT.get_taxonomy(kind, team=TEAM)
            self.assertNotIn("error", r, kind)
            self.assertGreater(r["total"], 0, kind)

    def test_bad_kind_is_rejected_with_guidance(self):
        r = PT.get_taxonomy("없는종류", team=TEAM)
        self.assertIn("error", r)
        self.assertIn("intent", r["error"])          # 고를 수 있는 값을 알려준다

    def test_intent_is_service_scoped(self):
        news = {v["key"] for v in PT.get_taxonomy("intent", service="뉴스", team=TEAM)["values"]}
        tstory = {v["key"] for v in PT.get_taxonomy("intent", service="티스토리", team=TEAM)["values"]}
        self.assertIn("정책·행정", news)
        self.assertNotIn("정책·행정", tstory)         # 서비스 전용 값이 새지 않는다

    def test_intent_definitions_come_from_the_single_source(self):
        """검수 화면·추출 프롬프트와 같은 문장이어야 한다(사전이 갈리면 기준이 갈린다)."""
        for v in PT.get_taxonomy("intent", service="뉴스", team=TEAM)["values"]:
            self.assertEqual(v["desc"], D.INTENT_VALUE_DEFS.get(v["key"], ""))

    def test_nested_dict_policy_is_flattened_not_leaked(self):
        for v in PT.get_taxonomy("intake_policy", team=TEAM)["values"]:
            self.assertIsInstance(v["desc"], str)


class TestRegistry(unittest.TestCase):
    def test_scope_filtering(self):
        for scope in ("internal", "external"):
            got = PT.tools_for(scope)
            self.assertTrue(got)
            for name, spec in got.items():
                self.assertIn(spec["scope"], (scope, "both"), name)

    def test_every_tool_declares_a_usable_schema(self):
        for name, spec in PT.TOOLS.items():
            for key in ("scope", "title", "desc", "inputSchema", "fn"):
                self.assertIn(key, spec, f"{name}.{key} 없음")
            schema = spec["inputSchema"]
            self.assertEqual(schema.get("type"), "object", name)
            self.assertFalse(schema.get("additionalProperties", True), f"{name}: 미정의 인자 허용")
            for req in schema.get("required", []):
                self.assertIn(req, schema.get("properties", {}), f"{name}: required {req} 미정의")

    def test_no_tool_exposes_team_as_an_argument(self):
        """team 이 스키마에 있으면 모델이 채울 수 있다 = 교차 팀 통로."""
        for name, spec in PT.TOOLS.items():
            self.assertNotIn("team", spec["inputSchema"].get("properties", {}), name)


if __name__ == "__main__":
    unittest.main()
