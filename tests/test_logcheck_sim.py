"""로그뷰어를 기획 131(요구사항 및 화면 설계) 수용 기준에 맞춘 부분(2026-08-09).

131 의 배치 검토 결론은 3안 독립 신설이고 Prism 배치는 부적합(실 로그를 외부 인프라로
반출 불가)이다. 그 문서가 Prism 에 남긴 조기 시범 범위가 "실 로그 없이 가능한 판정 규칙
시뮬레이터(샘플 로그 붙여넣기 검사)"라 그것과, 실 로그 없이도 충족 가능한 수용 기준만 구현했다.

여기서 잠그는 수용 기준
  R2 · 불합격 사유에 위반 필드명이 포함될 것 / 판정 근거 규칙 항목을 로그별로 확인할 것
  R4 · 기대 있음 발생 0건 = 누락, 기대 1건 발생 2건 이상 = 중복으로 판정할 것
  R7 · 값의 의미는 판정하지 않고 존재와 형식만 판정할 것
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("PRISM_DB", os.path.join(tempfile.mkdtemp(), "t.db"))
os.environ.setdefault("PRISM_BACKEND", "sqlite")

import prism.serve  # noqa: F401  (memfs._SV 주입)
from prism import memfs as MF
from prism import pastcheck as PC


def _log(**over):
    env = {"log_unique_id": "a1", "access_timestamp": 1785900000000,
           "common": {"service_id": "daum_news", "deployment": "real", "sdk_type": "WEB",
                      "uuid": "u-1", "suid": "s-1", "islogin": False, "page": "home_tab"},
           "action": {"type": "Event", "name": "홈탭_기사_클릭", "kind": "ClickContent"},
           "click.layer1": "main_feed"}
    env.update(over)
    return env


class TestViolationAttribution(unittest.TestCase):
    """R2 수용 기준: 위반 필드명 + 판정 근거 규칙 항목."""

    def test_every_violation_carries_field_and_rule(self):
        r = PC.simulate({"text": json.dumps([_log(action={"type": "Nope", "name": "item", "kind": "Bogus"})])})
        vio = r["logs"][0]["violations"]
        self.assertTrue(vio)
        for v in vio:
            self.assertEqual(len(v), 4, "판정은 [level, msg, field, rule] 4항이어야 한다: %r" % (v,))
            self.assertTrue(v[2], "위반 필드명이 비었습니다: %r" % (v,))
            self.assertTrue(v[3], "판정 근거 규칙이 비었습니다: %r" % (v,))

    def test_missing_required_names_the_field(self):
        env = _log()
        del env["common"]["page"]
        r = PC.simulate({"text": json.dumps([env])})
        miss = [v for v in r["logs"][0]["violations"] if v[3] == PC.RULE_REQUIRED]
        self.assertEqual([v[2] for v in miss], ["page"])

    def test_rules_map_to_the_canonical_rule_names(self):
        env = _log(action={"type": "Nope", "name": "item", "kind": "Bogus"})
        env["common"]["service_id"] = ""
        r = PC.simulate({"text": json.dumps([env])})
        rules = {v[3] for v in r["logs"][0]["violations"]}
        for expected in (PC.RULE_TYPE4, PC.RULE_KIND16, PC.RULE_NAME, PC.RULE_INVALID):
            self.assertIn(expected, rules)


class TestSimulatorInput(unittest.TestCase):
    """붙여넣기 입력 형태: 배열 · 단건 · JSONL · 그룹 표기."""

    def test_array_single_and_jsonl(self):
        one = json.dumps(_log())
        self.assertEqual(PC.simulate({"text": one})["n"], 1)
        self.assertEqual(PC.simulate({"text": "[" + one + "," + one + "]"})["n"], 2)
        self.assertEqual(PC.simulate({"text": one + "\n" + one})["n"], 2)

    def test_group_notation_is_normalized(self):
        """실 PAST 로그는 common.page · action.type 그룹 표기로 온다."""
        r = PC.simulate({"text": json.dumps([_log()])})
        self.assertEqual(r["logs"][0]["verdict"], "pass",
                         "그룹 표기가 판정 키로 정렬되지 않았습니다: %r" % (r["logs"][0]["violations"],))
        keys = [f[0] for f in r["logs"][0]["fields"]]
        self.assertIn("page", keys)
        self.assertIn("action_type", keys)

    def test_flat_notation_also_works(self):
        flat = {"log_unique_id": "b1", "access_timestamp": 1, "service_id": "s", "deployment": "real",
                "sdk_type": "WEB", "uuid": "u", "suid": "s1", "islogin": False, "page": "home_tab",
                "action_type": "Pageview", "action_name": "홈탭_진입"}
        self.assertEqual(PC.simulate({"text": json.dumps(flat)})["logs"][0]["verdict"], "pass")

    def test_bad_input_is_rejected_with_a_reason(self):
        self.assertIn("error", PC.simulate({"text": "   "}))
        self.assertIn("error", PC.simulate({"text": "not json"}))
        self.assertIn("error", PC.simulate({"text": "x" * (PC.SIM_MAX + 1)}))

    def test_partial_parse_keeps_going(self):
        """한 줄이 깨져도 나머지는 판정한다 — 표본 붙여넣기는 지저분하게 들어온다."""
        r = PC.simulate({"text": json.dumps(_log()) + "\n{oops\n" + json.dumps(_log())})
        self.assertEqual(r["n"], 2)
        self.assertTrue(r["errors"])

    def test_nothing_is_persisted(self):
        """시뮬레이터는 저장하지 않는다 — 실 로그 표본이 들어와도 남지 않아야 한다."""
        before = PC.logviewer_data(team="simteam")["n"]
        PC.simulate({"text": json.dumps([_log()])})
        self.assertEqual(PC.logviewer_data(team="simteam")["n"], before)


class TestBypassChecks(unittest.TestCase):
    """R7 수용 기준: 값의 의미는 판정하지 않고 존재와 형식만."""

    def test_tesla_prefix_is_confirmed_not_flagged(self):
        r = PC.simulate({"text": json.dumps([_log(custom_props={"tesla_slot": "A1"})])})
        v = [x for x in r["logs"][0]["violations"] if x[3] == PC.RULE_BYPASS]
        self.assertEqual([x[0] for x in v], ["info"], "바이패스 키는 위반이 아니라 확인 항목이다")
        self.assertEqual(r["bypass"]["tesla"], 1)

    def test_unregistered_custom_key_is_a_warning(self):
        r = PC.simulate({"text": json.dumps([_log(custom_props={"whatever": 1})])})
        self.assertIn(PC.RULE_CPROP, {v[3] for v in r["logs"][0]["violations"]})

    def test_broken_json_string_fails_on_format_only(self):
        r = PC.simulate({"text": json.dumps([_log(viewimp_extra="{oops")])})
        bad = [v for v in r["logs"][0]["violations"] if v[3] == PC.RULE_BYPASS]
        self.assertEqual([v[0] for v in bad], ["fail"])
        self.assertEqual(r["bypass"]["bad_json"], 1)
        # 형식이 맞으면 값이 무엇이든 통과(의미 판정 금지)
        ok = PC.simulate({"text": json.dumps([_log(viewimp_extra='{"whatever":"거짓말"}')])})
        self.assertEqual(ok["bypass"]["bad_json"], 0)

    def test_impression_click_link_key(self):
        imp = _log(action={"type": "ViewImp", "name": "홈탭_노출"})
        imp["viewimp_contents[].id"] = 7
        click = _log(log_unique_id="a2")
        click["content.id"] = 7
        r = PC.simulate({"text": json.dumps([imp, click])})
        self.assertEqual(r["bypass"]["link_ok"], 1)
        self.assertEqual(r["bypass"]["link_miss"], [])
        # 노출 없이 클릭만 오면 연결 불일치
        r2 = PC.simulate({"text": json.dumps([click])})
        self.assertEqual(r2["bypass"]["link_miss"], ["7"])


class TestChecklistMissVsDup(unittest.TestCase):
    """R4 수용 기준: 누락과 중복을 구분 판정."""

    def test_zero_occurrence_is_miss(self):
        d = PC.logviewer_data(team="cl1")
        self.assertTrue(all(it["state"] == "miss" for it in d["checklist"]["items"]))
        self.assertEqual(d["checklist"]["done"], 0)

    def test_once_expected_twice_sent_is_dup(self):
        PC.logviewer_ops({"op": "inject"}, team="cl2")
        d = PC.logviewer_data(team="cl2")
        app = [it for it in d["checklist"]["items"] if it["key"] == "AppLaunch"]
        self.assertEqual(len(app), 1, "주입 시 1회성 기대 항목이 체크리스트에 들어와야 한다")
        self.assertEqual((app[0]["n"], app[0]["state"]), (2, "dup"))
        self.assertIn("앱 실행", d["checklist"]["dups"])

    def test_once_item_absent_without_injection(self):
        """주입을 끄면 1회성 항목 자체가 없어야 한다 — 0건을 누락으로 세면 오판정."""
        PC.logviewer_ops({"op": "inject"}, team="cl3")
        PC.logviewer_ops({"op": "clear"}, team="cl3")
        d = PC.logviewer_data(team="cl3")
        self.assertEqual([it for it in d["checklist"]["items"] if it["key"] == "AppLaunch"], [])
        self.assertEqual(d["checklist"]["total"], len(PC.CONTRACT))

    def test_occurrence_makes_it_pass(self):
        MF.demo_ops({"op": "event", "event": "read", "idx": 0, "dwell_sec": 40}, team="cl4")
        d = PC.logviewer_data(team="cl4")
        usage = [it for it in d["checklist"]["items"] if it["key"] == "usage"][0]
        self.assertEqual((usage["n"], usage["state"]), (1, "pass"))
        self.assertTrue(usage["expect"])                   # 기대 표기가 화면에 뜬다
        self.assertEqual(d["checklist"]["done"], 1)


class TestRouteAndDeepLink(unittest.TestCase):
    def test_simulator_route_registered(self):
        import prism.serve as SV
        self.assertIn("/usermeta-logcheck", str(SV._POST_ROUTES))

    def test_subview_deep_link(self):
        """위키·문서에서 로그뷰어 화면을 바로 걸 수 있어야 한다(?m=user&view=viewer)."""
        src = open(os.path.join(ROOT, "prism/vendor/app-02-_afterverdict.js"), encoding="utf-8").read()
        self.assertIn("q.get('view')", src)
        self.assertIn("this.labUserView = vw", src)
        self.assertIn("loadLogViewer()", src)

    def test_scope_is_stated_on_screen(self):
        """131 결론은 독립 신설이다 · 화면이 스스로를 본 도구로 오인시키면 안 된다."""
        from prism import page
        self.assertIn("이 화면의 범위", page.PAGE)
        self.assertIn("독립 신설", page.PAGE)
        self.assertIn("443646328", page.PAGE)              # 131 원문 링크


if __name__ == "__main__":
    unittest.main()
