"""내부 검수 보조(reviewassist) 회귀 테스트 (2026-08-12).

이 계층은 검수자를 돕되 **품질 측정의 독립성을 깨지 않는 것**이 존재 조건이다.
그래서 문구가 아니라 규칙을 단언한다. 문구는 바뀌어도 되지만 아래는 바뀌면 안 된다.

  · 팀 없이는 돌지 않는다 · 도구 사용자가 team 을 지정할 수 없다(교차 팀 통로)
  · 골드 문항은 어떤 경로로도 안 나간다(요청 해시 · 목록 양쪽)
  · 잘리면 말한다 · 예외 원문을 응답에 싣지 않는다
  · **판정 전(before) 응답에는 추천성 키가 존재하지 않는다** — 이 파일에서 가장 중요한 단언.
    비어 있는 게 아니라 키가 없어야 한다. 있으면 클라이언트가 언젠가 읽고 답이 샌다.
  · 저장된 근거가 없으면 없다고 말한다(지어내지 않는다)

실행: python3 -m pytest tests/ -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prism.serve as SV                       # noqa: E402  (RA._SV 주입이 여기서 일어난다)
from prism import prismtools as PT             # noqa: E402
from prism import reviewassist as RA           # noqa: E402

TEAM = "team-1"
SVC = "뉴스"
INTENT = "속보·사건 추적"


def _h(n: int) -> str:
    """저장 키 형식(16자) — _row_key 가 재계산 없이 그대로 쓴다."""
    return f"{n:016d}"


H1 = _h(1)
# 골드 해시도 16자로 만들어 실제 _row_key 를 통과시킨다(골드가 데이터에 섞인 상황 재현).
GOLD_H = "gold:00000000001"


def _row(h, *, service=SVC, title="제목", grade="R", reasons=("ad",), intent=(INTENT,),
         cats=("정치",), evidence="", summary="요약문", entities=()):
    return {
        "content_ref": {"displayServiceName": service, "title": title, "subtitle": "",
                        "body": "본문", "body_hash": h},
        "quality_meta": {"finalGrade": grade, "reasons": list(reasons), "review": "auto",
                         "evidence": evidence},
        "item_meta": {"summary": summary, "intent": list(intent), "content_category": list(cats),
                      "entities": list(entities), "topic": ""},
        "trace": {},
    }


def _fb(*pairs):
    """feedback_map 한 건(store/supastore 와 같은 shape). pairs=(reviewer, verdict, note, ts)."""
    vs = [{"reviewer": r, "verdict": v, "note": nt, "ts": ts} for r, v, nt, ts in pairs]
    g = sum(1 for x in vs if x["verdict"] == "good")
    b = sum(1 for x in vs if x["verdict"] == "bad")
    cons = "good" if g > b else "bad" if b > g else ("split" if (g or b) else "")
    return {"verdicts": vs, "good": g, "bad": b, "n": len(vs), "consensus": cons,
            "agree": len(vs) > 0 and (g == 0 or b == 0),
            "verdict": cons or (vs[-1]["verdict"] if vs else ""),
            "stage": "", "note": vs[-1]["note"] if vs else ""}


class _FakeStore:
    def __init__(self, feedback=None, patches=None):
        self._fb, self._pt = dict(feedback or {}), list(patches or [])

    def feedback_map(self, team=None):
        return self._fb

    def patch_rows(self, limit=5000, team=None, content_hash=None):
        return list(self._pt)


def _keys(o):
    """응답 어디에든(중첩 포함) 나타나는 키 전부."""
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from _keys(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from _keys(v)


class Base(unittest.TestCase):
    def install(self, rows, feedback=None, patches=None):
        """저장 계층을 가짜로 갈아끼우고, 도구가 어떤 team 으로 조회했는지 기록한다."""
        self.seen_team = []
        store = _FakeStore(feedback, patches)
        o_rows, o_store = SV.results_rows, SV.get_store

        def rows_fn(limit=5000, team=None):
            self.seen_team.append(team)
            return list(rows)

        SV.results_rows = rows_fn
        SV.get_store = lambda: store
        self.addCleanup(lambda: setattr(SV, "results_rows", o_rows))
        self.addCleanup(lambda: setattr(SV, "get_store", o_store))
        return store


# ── 팀 스코프 ────────────────────────────────────────────────────────────────
class TestTeamScope(Base):
    def setUp(self):
        self.install([_row(H1)])

    def test_no_tool_runs_without_a_team(self):
        for tool in RA.TOOLS:
            for t in (None, "", "   "):
                r = RA.call(tool, {"hash": H1}, team=t)
                self.assertIn("error", r, f"{tool} team={t!r} 인데 실행됐다")

    def test_direct_call_also_refuses_without_a_team(self):
        """등록부를 거치지 않고 함수를 직접 불러도 팀 없이는 돌지 않는다(저장 계층이 전 팀을 준다)."""
        self.assertIn("error", RA.content_brief(hash=H1, team=None))
        self.assertIn("error", RA.verdict_precedents(hash=H1, team=""))
        self.assertIn("error", RA.reviewer_dissent(hash=H1, team=None))

    def test_team_in_args_is_ignored(self):
        r = RA.call("content_brief", {"hash": H1, "team": "다른팀"}, team=TEAM)
        self.assertNotIn("error", r)
        self.assertTrue(self.seen_team)
        self.assertEqual(set(self.seen_team), {TEAM})     # 조회는 세션 팀으로만 나갔다

    def test_no_tool_exposes_team_as_an_argument(self):
        for name, spec in RA.TOOLS.items():
            self.assertNotIn("team", spec["inputSchema"].get("properties", {}), name)

    def test_unknown_tool_is_rejected(self):
        self.assertIn("error", RA.call("없는도구", {"hash": H1}, team=TEAM))


# ── 골드 문항 차단 ───────────────────────────────────────────────────────────
class TestGoldIsNeverExposed(Base):
    def setUp(self):
        # 골드 행이 콘텐츠·판정·교정 로그 어디에 섞여도 나가면 안 된다.
        self.install(
            rows=[_row(H1), _row(GOLD_H, title="골드 원문")],
            feedback={GOLD_H: _fb(("복실", "bad", "골드 정답", 100.0))},
            patches=[{"hash": GOLD_H, "reviewer": "복실", "element": "summary",
                      "before": {"summary": "요약문"}, "after": {"summary": "골드 정답 요약"},
                      "ts": 100.0}],
        )

    def test_requesting_a_gold_hash_is_refused_by_every_tool(self):
        for gh in ("gold:ok:abc", "goldf:bad:abc", GOLD_H):
            for tool in RA.TOOLS:
                r = RA.call(tool, {"hash": gh}, team=TEAM)
                self.assertIn("error", r, f"{tool}({gh}) 가 응답을 냈다")
                self.assertFalse(r.get("items"), f"{tool}({gh}) 가 항목을 냈다")

    def test_gold_never_appears_among_precedents(self):
        r = RA.call("verdict_precedents", {"hash": H1}, team=TEAM)
        for it in r.get("items") or []:
            self.assertFalse(PT.is_gold(it["hash"]), f"골드가 선례로 샜다: {it['hash']}")

    def test_gold_patches_never_become_suggestions(self):
        r = RA.call("content_brief", {"hash": H1, "stage": "after"}, team=TEAM)
        self.assertEqual(r["suggestions"], [])            # 골드 교정만 있었으므로 제안이 없다


# ── 독립성(가장 중요) ────────────────────────────────────────────────────────
class TestIndependence(Base):
    def setUp(self):
        self.install(
            rows=[_row(H1), _row(_h(2))],
            patches=[{"hash": _h(2), "reviewer": "딱지", "element": "summary",
                      "before": {"summary": "요약문"}, "after": {"summary": "고친 요약문"},
                      "ts": 10.0}],
        )

    def test_before_response_has_no_suggestive_key_at_all(self):
        """비어 있는 게 아니라 키 자체가 없어야 한다 — 있으면 클라이언트가 언젠가 쓴다."""
        r = RA.call("content_brief", {"hash": H1, "stage": "before"}, team=TEAM)
        self.assertNotIn("error", r)
        leaked = set(_keys(r)) & set(RA.SUGGESTIVE_KEYS)
        self.assertEqual(leaked, set(), f"판정 전 응답에 추천성 키가 있다: {leaked}")

    def test_unknown_stage_falls_back_to_before(self):
        """모르는 값은 덜 주는 쪽으로 수렴한다(오타·구버전 클라이언트가 추천을 열지 못하게)."""
        for stage in ("", None, "later", "afterwards", "추천", "AFTER_", 7):
            r = RA.call("content_brief", {"hash": H1, "stage": stage}, team=TEAM)
            self.assertEqual(r["stage"], "before", f"stage={stage!r}")
            self.assertNotIn("suggestions", r, f"stage={stage!r} 인데 제안이 나왔다")

    def test_after_stage_is_the_only_way_to_get_suggestions(self):
        r = RA.call("content_brief", {"hash": H1, "stage": "  AFTER "}, team=TEAM)
        self.assertEqual(r["stage"], "after")
        self.assertIn("suggestions", r)

    def test_dissent_does_not_hand_over_a_majority_answer(self):
        """다수결·합의 값을 담으면 그것이 정답으로 읽혀 검수자가 자기 판단을 접는다."""
        self.install(rows=[_row(H1)],
                     feedback={H1: _fb(("복실", "good", "", 1.0), ("딱지", "bad", "", 2.0),
                                       ("대식", "bad", "", 3.0))})
        r = RA.call("reviewer_dissent", {"hash": H1}, team=TEAM)
        for k in ("consensus", "majority", "verdict", "agree", "good", "bad", "recommended"):
            self.assertNotIn(k, r, f"불일치 응답에 {k} 가 있다")


# ── 근거를 지어내지 않는다 ───────────────────────────────────────────────────
class TestNoFabrication(Base):
    def test_missing_evidence_is_reported_as_missing(self):
        self.install([_row(H1, evidence="")])
        r = RA.call("content_brief", {"hash": H1}, team=TEAM)
        self.assertIsNone(r["evidence"])                  # 빈 문자열로 얼버무리지 않는다
        self.assertFalse(r["has_evidence"])
        self.assertEqual(len(r["summary3"]), 3)
        self.assertIn(RA.NO_EVIDENCE, r["summary3"][2])   # 없다고 말한다

    def test_stored_evidence_is_relayed_verbatim(self):
        ev = "본문이 제품 구매 링크로 끝나 광고성으로 봤다"
        self.install([_row(H1, evidence=ev)])
        r = RA.call("content_brief", {"hash": H1}, team=TEAM)
        self.assertEqual(r["evidence"], ev)               # 조립일 뿐 재생성이 아니다
        self.assertTrue(r["has_evidence"])
        self.assertIn(ev, r["summary3"][2])

    def test_summary3_is_always_three_lines(self):
        self.install([_row(H1, evidence=""), _row(_h(2), evidence="근거")])
        for h in (H1, _h(2)):
            s = RA.call("content_brief", {"hash": h}, team=TEAM)["summary3"]
            self.assertEqual(len(s), 3, h)
            self.assertTrue(all(isinstance(x, str) and x for x in s), h)

    def test_criteria_definitions_come_from_the_single_source(self):
        """정의문 원천은 INTENT_VALUE_DEFS 하나 — 보조가 자기 문장을 쓰면 기준이 갈린다."""
        from prism import dictionaries as D
        self.install([_row(H1)])
        crit = RA.call("content_brief", {"hash": H1}, team=TEAM)["criteria"]
        by_key = {c["key"]: c["desc"] for c in crit}
        self.assertIn(INTENT, by_key)
        self.assertEqual(by_key[INTENT], D.INTENT_VALUE_DEFS.get(INTENT, ""))


# ── 선례 ─────────────────────────────────────────────────────────────────────
class TestPrecedents(Base):
    def _many(self, n):
        rows = [_row(H1)]
        fb = {}
        for i in range(2, 2 + n):
            rows.append(_row(_h(i), title=f"과거 {i}"))
            fb[_h(i)] = _fb((f"검수자{i}", "bad", "광고성으로 봤다", float(i)))
        self.install(rows, feedback=fb)

    def test_truncation_is_reported(self):
        self._many(8)
        r = RA.call("verdict_precedents", {"hash": H1, "limit": 3}, team=TEAM)
        self.assertEqual(len(r["items"]), 3)
        self.assertEqual(r["total"], 8)
        self.assertTrue(r["truncated"])

    def test_nothing_cut_says_so(self):
        self._many(2)
        r = RA.call("verdict_precedents", {"hash": H1, "limit": 20}, team=TEAM)
        self.assertEqual(r["total"], 2)
        self.assertFalse(r["truncated"])

    def test_limit_is_clamped_not_errored(self):
        self._many(3)
        for bad in ("abc", -5, 9999, None):
            r = RA.call("verdict_precedents", {"hash": H1, "limit": bad}, team=TEAM)
            self.assertNotIn("error", r, f"limit={bad!r}")

    def test_items_carry_the_similarity_basis(self):
        """무엇이 비슷한지 밝히지 않으면 검수자가 스스로 판단할 수 없다."""
        self._many(1)
        it = RA.call("verdict_precedents", {"hash": H1}, team=TEAM)["items"][0]
        self.assertEqual(set(it), {"hash", "title", "verdict", "reason", "ts", "why_similar"})
        self.assertIn(SVC, it["why_similar"])
        self.assertIn("광고성", it["why_similar"])        # 공유한 품질 사유를 이름으로 밝힌다

    def test_unconfirmed_and_split_verdicts_are_not_precedents(self):
        self.install([_row(H1), _row(_h(2)), _row(_h(3))],
                     feedback={_h(2): _fb(("복실", "good", "", 1.0), ("딱지", "bad", "", 2.0)),
                               _h(3): {}})
        self.assertEqual(RA.call("verdict_precedents", {"hash": H1}, team=TEAM)["total"], 0)

    def test_other_services_and_unrelated_values_are_excluded(self):
        self.install(
            [_row(H1),
             _row(_h(2), service="스포츠"),                              # 다른 서비스
             _row(_h(3), reasons=("hate",), intent=("실용 정보",), cats=("스포츠",))],  # 겹치는 값 없음
            feedback={_h(2): _fb(("복실", "bad", "", 1.0)),
                      _h(3): _fb(("딱지", "bad", "", 2.0))})
        self.assertEqual(RA.call("verdict_precedents", {"hash": H1}, team=TEAM)["total"], 0)


# ── 불일치 ───────────────────────────────────────────────────────────────────
class TestDissent(Base):
    def test_both_sides_are_shown_in_time_order(self):
        self.install([_row(H1)],
                     feedback={H1: _fb(("복실", "good", "문제 없다", 100.0),
                                       ("딱지", "bad", "광고성이다", "2026-08-01T00:00:00+00:00"))})
        r = RA.call("reviewer_dissent", {"hash": H1}, team=TEAM)
        self.assertTrue(r["split"])
        self.assertEqual([i["verdict"] for i in r["items"]], ["good", "bad"])   # ISO ts 도 흡수
        self.assertEqual(set(r["items"][0]), {"reviewer", "verdict", "reason", "ts"})
        self.assertEqual(r["items"][1]["reason"], "광고성이다")

    def test_agreement_is_not_a_split(self):
        self.install([_row(H1)],
                     feedback={H1: _fb(("복실", "bad", "", 1.0), ("딱지", "bad", "", 2.0))})
        r = RA.call("reviewer_dissent", {"hash": H1}, team=TEAM)
        self.assertFalse(r["split"])
        self.assertEqual(r["total"], 2)

    def test_no_verdicts_yet(self):
        self.install([_row(H1)])
        r = RA.call("reviewer_dissent", {"hash": H1}, team=TEAM)
        self.assertEqual(r["items"], [])
        self.assertFalse(r["split"])


# ── 수정 제안(판정 뒤) ───────────────────────────────────────────────────────
class TestSuggestions(Base):
    def test_suggestion_is_assembled_from_past_corrections(self):
        self.install(
            rows=[_row(H1), _row(_h(2)), _row(_h(3))],
            patches=[{"hash": _h(2), "reviewer": "복실", "element": "content_category",
                      "before": {"content_category": ["정치"]},
                      "after": {"content_category": ["사회"]}, "ts": 10.0},
                     {"hash": _h(3), "reviewer": "딱지", "element": "content_category",
                      "before": {"content_category": ["정치"]},
                      "after": {"content_category": ["사회"]}, "ts": 11.0}])
        sg = RA.call("content_brief", {"hash": H1, "stage": "after"}, team=TEAM)["suggestions"]
        self.assertEqual(len(sg), 1)
        self.assertEqual(set(sg[0]), {"field", "from", "to", "basis"})
        self.assertEqual((sg[0]["field"], sg[0]["from"], sg[0]["to"]),
                         ("content_category", "정치", "사회"))
        self.assertIn("2건", sg[0]["basis"])              # 근거는 실제로 센 선례 수다

    def test_corrections_from_a_different_starting_value_are_not_suggested(self):
        self.install(
            rows=[_row(H1), _row(_h(2), cats=("경제",))],
            patches=[{"hash": _h(2), "reviewer": "복실", "element": "content_category",
                      "before": {"content_category": ["경제"]},
                      "after": {"content_category": ["사회"]}, "ts": 10.0}])
        r = RA.call("content_brief", {"hash": H1, "stage": "after"}, team=TEAM)
        self.assertEqual(r["suggestions"], [])            # 출발값이 다르면 선례가 아니다

    def test_own_corrections_are_not_suggested_back(self):
        self.install(
            rows=[_row(H1)],
            patches=[{"hash": H1, "reviewer": "복실", "element": "content_category",
                      "before": {"content_category": ["정치"]},
                      "after": {"content_category": ["사회"]}, "ts": 10.0}])
        r = RA.call("content_brief", {"hash": H1, "stage": "after"}, team=TEAM)
        self.assertEqual(r["suggestions"], [])


# ── 오류 처리 · 등록부 · 라우트 ──────────────────────────────────────────────
class TestErrorsAndRegistry(Base):
    def setUp(self):
        self.install([_row(H1)])

    def test_errors_do_not_leak_internals(self):
        spec = RA.TOOLS["content_brief"]
        orig = spec["fn"]
        spec["fn"] = lambda **kw: (_ for _ in ()).throw(RuntimeError("내부 경로 /secret/path"))
        self.addCleanup(lambda: spec.__setitem__("fn", orig))
        r = RA.call("content_brief", {"hash": H1}, team=TEAM)
        self.assertIn("error", r)
        self.assertNotIn("secret", str(r))

    def test_unknown_content_is_a_readable_error(self):
        for tool in ("content_brief", "verdict_precedents"):
            r = RA.call(tool, {"hash": _h(99)}, team=TEAM)
            self.assertEqual(r.get("error"), RA.NOT_FOUND_MSG, tool)

    def test_malformed_input_becomes_a_tool_error_not_a_crash(self):
        """앞단이 HTTP 본문이라 문자열도 dict 도 아닌 값이 들어온다. 500 은 내부 노출이다."""
        for name, args in ((123, {}), (None, {}), ("content_brief", ["배열"]),
                           ("content_brief", "문자열"), ("content_brief", None)):
            r = RA.call(name, args, team=TEAM)
            self.assertIsInstance(r, dict, f"{name!r}/{args!r}")
            self.assertIn("error", r, f"{name!r}/{args!r}")

    def test_missing_hash_is_rejected(self):
        for tool in RA.TOOLS:
            self.assertIn("error", RA.call(tool, {"hash": "  "}, team=TEAM), tool)

    def test_every_tool_is_internal_only(self):
        """외부 MCP(트랙 B)에 열리면 파트너 키로 팀 검수 이력이 통째로 나간다."""
        for name, spec in RA.TOOLS.items():
            self.assertEqual(spec["scope"], "internal", name)
            self.assertNotIn(name, PT.tools_for("external"), name)

    def test_every_tool_declares_a_usable_schema(self):
        for name, spec in RA.TOOLS.items():
            for key in ("scope", "title", "desc", "inputSchema", "fn"):
                self.assertIn(key, spec, f"{name}.{key} 없음")
            schema = spec["inputSchema"]
            self.assertEqual(schema.get("type"), "object", name)
            self.assertFalse(schema.get("additionalProperties", True), f"{name}: 미정의 인자 허용")
            for req in schema.get("required", []):
                self.assertIn(req, schema.get("properties", {}), f"{name}: required {req} 미정의")

    def test_route_is_registered_behind_the_team_gate(self):
        fn, gate = SV._POST_ROUTES["/assist"]
        self.assertEqual(gate, "team")                    # 로그인 + 팀 소속(fail-closed)


if __name__ == "__main__":
    unittest.main()
