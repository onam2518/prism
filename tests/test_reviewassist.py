"""내부 검수 보조(reviewassist) 회귀 테스트 (2026-08-12).

이 계층은 검수자를 돕되 **품질 측정의 독립성을 깨지 않는 것**이 존재 조건이다.
그래서 문구가 아니라 규칙을 단언한다. 문구는 바뀌어도 되지만 아래는 바뀌면 안 된다.

  · 팀 없이는 돌지 않는다 · 도구 사용자가 team 을 지정할 수 없다(교차 팀 통로)
  · 로컬 편의값("local")이 supabase 모드로 새지 않는다(새면 전 팀 조회 · 감사 H1)
  · 골드 문항은 어떤 경로로도 안 나간다(요청 해시 · 목록 양쪽)
  · 잘리면 말한다 · 예외 원문을 응답에 싣지 않는다
  · **판정 전(before)에는 판단 재료가 나가지 않는다** — 이 파일에서 가장 중요한 단언.
    추천성 키는 비어 있는 게 아니라 키가 없어야 하고(있으면 클라이언트가 언젠가 읽는다),
    남이 내린 판정(선례·다른 검수자 의견)은 아예 거절돼야 한다(먼저 보면 기준점이 된다).
  · 1인 판정은 선례가 아니다 · 1건짜리 교정은 제안이 아니다
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

AFTER = {"stage": "after"}


def _args(h=H1, **kw):
    """판정 뒤 단계로 부르는 인자(선례·불일치는 after 가 아니면 거절된다)."""
    return dict({"hash": h}, **dict(AFTER, **kw))


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


def _confirmed(note="광고성으로 봤다", ts=1.0):
    """확정 판정 = 최소 인원 이상이 갈리지 않고 같게 본 것."""
    return _fb(*[(f"검수자{i}", "bad", note, ts + i) for i in range(RA.PRECEDENT_MIN_N)])


def _patch(h, reviewer, field, before, after, ts=10.0):
    return {"hash": h, "reviewer": reviewer, "element": field,
            "before": {field: before}, "after": {field: after}, "ts": ts}


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
                r = RA.call(tool, _args(), team=t)
                self.assertIn("error", r, f"{tool} team={t!r} 인데 실행됐다")

    def test_direct_call_also_refuses_without_a_team(self):
        """등록부를 거치지 않고 함수를 직접 불러도 팀 없이는 돌지 않는다(저장 계층이 전 팀을 준다)."""
        self.assertIn("error", RA.content_brief(hash=H1, team=None))
        self.assertIn("error", RA.verdict_precedents(hash=H1, stage="after", team=""))
        self.assertIn("error", RA.reviewer_dissent(hash=H1, stage="after", team=None))

    def test_team_in_args_is_ignored(self):
        r = RA.call("content_brief", {"hash": H1, "team": "다른팀"}, team=TEAM)
        self.assertNotIn("error", r)
        self.assertTrue(self.seen_team)
        self.assertEqual(set(self.seen_team), {TEAM})     # 조회는 세션 팀으로만 나갔다

    def test_no_tool_exposes_team_as_an_argument(self):
        for name, spec in RA.TOOLS.items():
            self.assertNotIn("team", spec["inputSchema"].get("properties", {}), name)

    def test_unknown_tool_is_rejected(self):
        self.assertIn("error", RA.call("없는도구", _args(), team=TEAM))


# ── 라우트의 팀 해석(로컬 편의값이 운영으로 새지 않는다) ────────────────────
class _FakeHandler:
    def __init__(self, team):
        self._team = team

    def _req_team(self):
        return self._team


class TestRouteTeamResolution(unittest.TestCase):
    """라우트는 sqlite 단일 팀 편의를 위해 고정 스코프("local")를 쓴다. 이 값이 supabase
    모드로 새면 저장 계층이 팀 필터를 건 채로 존재하지 않는 팀을 보거나, 최악에는 가드가
    풀려 전 팀을 읽는다(감사 H1 과 같은 실패). 코드를 읽어야만 안전한 상태로 두지 않는다."""

    def _resolve(self, supa, req_team, body=b'{"tool":"content_brief","args":{"hash":"x"}}'):
        seen = []
        o_supa, o_call = SV._supa, RA.call
        SV._supa = lambda: supa
        RA.call = lambda name, args, team=None: (seen.append(team), {"ok": True})[1]
        self.addCleanup(lambda: setattr(SV, "_supa", o_supa))
        self.addCleanup(lambda: setattr(RA, "call", o_call))
        fn, _gate = SV._POST_ROUTES["/assist"]
        fn(_FakeHandler(req_team), body)
        return seen[0]

    def test_local_scope_never_appears_in_supabase_mode(self):
        for req_team in (None, "", "team-7"):
            got = self._resolve(True, req_team)
            self.assertNotEqual(got, "local", f"req_team={req_team!r} 인데 로컬 스코프가 샜다")

    def test_supabase_without_a_team_fails_closed(self):
        self.assertIsNone(self._resolve(True, None))      # 도구 계층이 need_team 으로 거절한다

    def test_supabase_uses_the_session_team(self):
        self.assertEqual(self._resolve(True, "team-7"), "team-7")

    def test_body_cannot_inject_the_team(self):
        body = '{"tool":"content_brief","args":{"hash":"x","team":"침입팀"},"team":"침입팀"}'.encode()
        self.assertIsNone(self._resolve(True, None, body))

    def test_local_sqlite_gets_a_fixed_scope(self):
        self.assertEqual(self._resolve(False, None), "local")

    def test_malformed_body_does_not_crash_the_route(self):
        for body in (b"", b"[]", b'"scalar"', b"not json", b"null"):
            self.assertIsNone(self._resolve(True, None, body), body)


# ── 골드 문항 차단 ───────────────────────────────────────────────────────────
class TestGoldIsNeverExposed(Base):
    def setUp(self):
        # 골드 행이 콘텐츠·판정·교정 로그 어디에 섞여도 나가면 안 된다.
        # 교정은 임계값(2건) 이상 깔아 둔다 — 1건이면 임계값 때문에 안 나온 것인지
        # 골드 가드 때문에 안 나온 것인지 구분되지 않아 테스트가 거짓 안심을 준다.
        self.install(
            rows=[_row(H1), _row(GOLD_H, title="골드 원문")],
            feedback={GOLD_H: _confirmed("골드 정답")},
            patches=[_patch(GOLD_H, "복실", "content_category", ["정치"], ["사회"], 10.0),
                     _patch(GOLD_H, "딱지", "content_category", ["정치"], ["사회"], 11.0)],
        )

    def test_gold_never_gets_data_from_any_tool(self):
        for gh in ("gold:ok:abc", "goldf:bad:abc", GOLD_H):
            for tool in RA.TOOLS:
                r = RA.call(tool, _args(gh), team=TEAM)
                self.assertFalse(r.get("items"), f"{tool}({gh}) 가 항목을 냈다")
                self.assertNotIn("values", r, f"{tool}({gh}) 가 부여값을 냈다")
                self.assertNotIn("summary3", r, f"{tool}({gh}) 가 브리핑을 냈다")

    def test_list_tools_return_empty_not_an_error_for_gold(self):
        """오류 문구는 화면에 뜨고, 뜨는 순간 '이건 골드다' 신호가 된다.

        검수자가 골드를 알아보면 골드가 재려던 것(평소의 검수)이 사라진다 — 골드는 선례가
        없는 게 자연스러우므로 '선례 없는 평범한 콘텐츠' 와 같은 응답을 준다."""
        for tool, spec in RA.TOOLS.items():
            if not spec.get("after_only"):
                continue
            gold = RA.call(tool, _args(GOLD_H), team=TEAM)
            plain = RA.call(tool, _args(H1), team=TEAM)      # 실재하되 선례가 없는 평범한 콘텐츠
            self.assertNotIn("error", gold, f"{tool}: 골드에서만 오류가 뜨면 그게 신호다")
            self.assertEqual(gold, plain, f"{tool}: 골드 응답이 평범한 콘텐츠와 다르다")

    def test_content_brief_still_refuses_gold(self):
        """골드 문항의 화면 값은 골든 정답을 일부러 뒤집은 사본이다(reviewops._inject_gold).

        저장된 행으로 브리핑을 만들면 화면과 어긋나고, 그 어긋남 자체가 정답이 된다.
        서버가 안전하게 만들 수 없으므로 아예 주지 않는다(클라이언트가 화면 값으로 조립)."""
        r = RA.call("content_brief", {"hash": GOLD_H, "stage": "after"}, team=TEAM)
        self.assertEqual(r.get("error"), RA.GOLD_MSG)

    def test_gold_never_appears_among_precedents(self):
        r = RA.call("verdict_precedents", _args(), team=TEAM)
        for it in r.get("items") or []:
            self.assertFalse(PT.is_gold(it["hash"]), f"골드가 선례로 샜다: {it['hash']}")

    def test_gold_patches_never_become_suggestions(self):
        r = RA.call("content_brief", {"hash": H1, "stage": "after"}, team=TEAM)
        self.assertEqual(r["suggestions"], [])            # 골드 교정만 있었으므로 제안이 없다


# ── 독립성(가장 중요) ────────────────────────────────────────────────────────
class TestIndependence(Base):
    def setUp(self):
        self.install(
            rows=[_row(H1), _row(_h(2)), _row(_h(3))],
            feedback={_h(2): _confirmed(), _h(3): _confirmed()},
            patches=[_patch(_h(2), "딱지", "content_category", ["정치"], ["사회"], 10.0),
                     _patch(_h(3), "복실", "content_category", ["정치"], ["사회"], 11.0)],
        )

    def test_before_stage_yields_no_judgment_material_from_any_tool(self):
        """도구 전체를 돈다 — 도구가 늘어도 이 단언이 그대로 지킨다.

        추천성 키는 비어 있는 게 아니라 **없어야** 하고, 남이 내린 판정은 아예 안 나가야 한다."""
        for tool, spec in RA.TOOLS.items():
            r = RA.call(tool, {"hash": H1, "stage": "before"}, team=TEAM)
            leaked = set(_keys(r)) & set(RA.SUGGESTIVE_KEYS)
            self.assertEqual(leaked, set(), f"{tool}: 판정 전 응답에 추천성 키가 있다: {leaked}")
            self.assertFalse(r.get("items"), f"{tool}: 판정 전인데 남의 판정을 줬다")
            if spec.get("after_only"):
                self.assertEqual(r.get("error"), RA.AFTER_ONLY_MSG, tool)

    def test_after_only_tools_are_blocked_on_the_server_not_the_screen(self):
        """규칙이 클라이언트에 있으면 다음 클라이언트가 어긴다. 직접 호출도 막혀야 한다."""
        for tool, spec in RA.TOOLS.items():
            if not spec.get("after_only"):
                continue
            for stage in ("before", "", None, "later", "판정중", 7):
                r = spec["fn"](hash=H1, stage=stage, team=TEAM)
                self.assertEqual(r.get("error"), RA.AFTER_ONLY_MSG, f"{tool} stage={stage!r}")
                self.assertEqual(r.get("items"), [], f"{tool} stage={stage!r}")

    def test_after_only_tools_answer_once_the_verdict_is_in(self):
        for tool, spec in RA.TOOLS.items():
            if spec.get("after_only"):
                r = RA.call(tool, _args(), team=TEAM)
                self.assertNotIn("error", r, tool)

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
        r = RA.call("reviewer_dissent", _args(), team=TEAM)
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
            fb[_h(i)] = _confirmed(ts=float(i))
        self.install(rows, feedback=fb)

    def test_truncation_is_reported(self):
        self._many(8)
        r = RA.call("verdict_precedents", _args(limit=3), team=TEAM)
        self.assertEqual(len(r["items"]), 3)
        self.assertEqual(r["total"], 8)
        self.assertTrue(r["truncated"])

    def test_nothing_cut_says_so(self):
        self._many(2)
        r = RA.call("verdict_precedents", _args(limit=20), team=TEAM)
        self.assertEqual(r["total"], 2)
        self.assertFalse(r["truncated"])

    def test_limit_is_clamped_not_errored(self):
        self._many(3)
        for bad in ("abc", -5, 9999, None):
            r = RA.call("verdict_precedents", _args(limit=bad), team=TEAM)
            self.assertNotIn("error", r, f"limit={bad!r}")

    def test_items_carry_the_similarity_basis_and_headcount(self):
        """무엇이 비슷한지·몇 사람이 같게 봤는지 밝히지 않으면 검수자가 스스로 판단할 수 없다."""
        self._many(1)
        it = RA.call("verdict_precedents", _args(), team=TEAM)["items"][0]
        self.assertEqual(set(it), {"hash", "title", "verdict", "reason", "ts", "why_similar", "n"})
        self.assertEqual(it["n"], RA.PRECEDENT_MIN_N)
        self.assertIn(SVC, it["why_similar"])
        self.assertIn("광고성", it["why_similar"])        # 공유한 품질 사유를 이름으로 밝힌다

    def test_a_single_reviewer_verdict_is_not_a_precedent(self):
        """1인 판정을 확정 선례로 되먹이면 그 한 사람의 편향이 증폭되고 '선례' 가 거짓이 된다."""
        self.install([_row(H1), _row(_h(2))],
                     feedback={_h(2): _fb(("복실", "bad", "혼자 봤다", 1.0))})
        self.assertEqual(RA.call("verdict_precedents", _args(), team=TEAM)["total"], 0)

    def test_unconfirmed_and_split_verdicts_are_not_precedents(self):
        self.install([_row(H1), _row(_h(2)), _row(_h(3))],
                     feedback={_h(2): _fb(("복실", "good", "", 1.0), ("딱지", "bad", "", 2.0)),
                               _h(3): {}})
        self.assertEqual(RA.call("verdict_precedents", _args(), team=TEAM)["total"], 0)

    def test_other_services_and_unrelated_values_are_excluded(self):
        self.install(
            [_row(H1),
             _row(_h(2), service="스포츠"),                              # 다른 서비스
             _row(_h(3), reasons=("hate",), intent=("실용 정보",), cats=("스포츠",))],  # 겹치는 값 없음
            feedback={_h(2): _confirmed(), _h(3): _confirmed()})
        self.assertEqual(RA.call("verdict_precedents", _args(), team=TEAM)["total"], 0)


# ── 불일치 ───────────────────────────────────────────────────────────────────
class TestDissent(Base):
    def test_both_sides_are_shown_in_time_order(self):
        self.install([_row(H1)],
                     feedback={H1: _fb(("복실", "good", "문제 없다", 100.0),
                                       ("딱지", "bad", "광고성이다", "2026-08-01T00:00:00+00:00"))})
        r = RA.call("reviewer_dissent", _args(), team=TEAM)
        self.assertTrue(r["split"])
        self.assertEqual([i["verdict"] for i in r["items"]], ["good", "bad"])   # ISO ts 도 흡수
        self.assertEqual(set(r["items"][0]), {"reviewer", "verdict", "reason", "ts"})
        self.assertEqual(r["items"][1]["reason"], "광고성이다")

    def test_agreement_is_not_a_split(self):
        self.install([_row(H1)],
                     feedback={H1: _fb(("복실", "bad", "", 1.0), ("딱지", "bad", "", 2.0))})
        r = RA.call("reviewer_dissent", _args(), team=TEAM)
        self.assertFalse(r["split"])
        self.assertEqual(r["total"], 2)

    def test_no_verdicts_yet(self):
        self.install([_row(H1)])
        r = RA.call("reviewer_dissent", _args(), team=TEAM)
        self.assertEqual(r["items"], [])
        self.assertFalse(r["split"])


# ── 수정 제안(판정 뒤) ───────────────────────────────────────────────────────
class TestSuggestions(Base):
    def _brief(self):
        return RA.call("content_brief", {"hash": H1, "stage": "after"}, team=TEAM)["suggestions"]

    def test_suggestion_is_assembled_from_past_corrections(self):
        self.install(
            rows=[_row(H1), _row(_h(2)), _row(_h(3))],
            patches=[_patch(_h(2), "복실", "content_category", ["정치"], ["사회"], 10.0),
                     _patch(_h(3), "딱지", "content_category", ["정치"], ["사회"], 11.0)])
        sg = self._brief()
        self.assertEqual(len(sg), 1)
        self.assertEqual(set(sg[0]), {"field", "from", "to", "count", "reviewers", "basis"})
        self.assertEqual((sg[0]["field"], sg[0]["from"], sg[0]["to"]),
                         ("content_category", "정치", "사회"))
        self.assertEqual((sg[0]["count"], sg[0]["reviewers"]), (2, 2))
        self.assertIn("2건", sg[0]["basis"])              # 근거는 실제로 센 사실뿐이다
        self.assertIn("2명", sg[0]["basis"])

    def test_one_correction_is_not_a_precedent(self):
        """1건은 선례가 아니라 한 사람의 판단이다."""
        self.install(rows=[_row(H1), _row(_h(2))],
                     patches=[_patch(_h(2), "복실", "content_category", ["정치"], ["사회"])])
        self.assertEqual(self._brief(), [])

    def test_headcount_is_reported_separately_from_the_count(self):
        """같은 사람이 두 번 고친 것과 두 사람이 각각 고친 것은 무게가 다르다 · 판단은 검수자 몫."""
        self.install(rows=[_row(H1), _row(_h(2)), _row(_h(3))],
                     patches=[_patch(_h(2), "복실", "content_category", ["정치"], ["사회"], 10.0),
                              _patch(_h(3), "복실", "content_category", ["정치"], ["사회"], 11.0)])
        sg = self._brief()
        self.assertEqual((sg[0]["count"], sg[0]["reviewers"]), (2, 1))

    def test_list_fields_are_compared_element_by_element(self):
        """목록 전체가 같아야 한다면 운영에서 거의 안 걸려 기능이 없는 것과 같다."""
        self.install(
            rows=[_row(H1, cats=("정치", "사회")), _row(_h(2), cats=("정치", "경제")),
                  _row(_h(3), cats=("정치", "문화"))],
            patches=[_patch(_h(2), "복실", "content_category", ["정치", "경제"], ["정치", "국제"], 10.0),
                     _patch(_h(3), "딱지", "content_category", ["정치", "문화"], ["정치", "국제"], 11.0)])
        # 대상의 현재 값에 없는 요소('경제'·'문화')에서 출발한 교정이라 제안이 되지 않는다
        self.assertEqual(self._brief(), [])
        self.install(
            rows=[_row(H1, cats=("정치", "사회")), _row(_h(2), cats=("정치", "사회")),
                  _row(_h(3), cats=("정치", "사회"))],
            patches=[_patch(_h(2), "복실", "content_category", ["정치", "사회"], ["정치", "국제"], 10.0),
                     _patch(_h(3), "딱지", "content_category", ["정치", "사회"], ["정치", "국제"], 11.0)])
        sg = self._brief()
        self.assertEqual((sg[0]["from"], sg[0]["to"]), ("사회", "국제"))   # 바뀐 요소만 짚는다

    def test_ambiguous_multi_element_changes_are_not_paired(self):
        """여러 개가 한꺼번에 바뀌면 무엇이 무엇으로 바뀌었는지 모른다. 짝을 지어내지 않는다."""
        self.install(
            rows=[_row(H1, cats=("정치", "사회")), _row(_h(2), cats=("정치", "사회")),
                  _row(_h(3), cats=("정치", "사회"))],
            patches=[_patch(_h(2), "복실", "content_category", ["정치", "사회"], ["경제", "국제"], 10.0),
                     _patch(_h(3), "딱지", "content_category", ["정치", "사회"], ["경제", "국제"], 11.0)])
        self.assertEqual(self._brief(), [])

    def test_filling_an_empty_field_is_counted(self):
        """빈 카테고리 채우기는 실제로 가장 잦은 교정이다 · 대상도 비어 있을 때만 센다."""
        self.install(
            rows=[_row(H1, cats=()), _row(_h(2), cats=()), _row(_h(3), cats=())],
            patches=[_patch(_h(2), "복실", "content_category", [], ["사회"], 10.0),
                     _patch(_h(3), "딱지", "content_category", [], ["사회"], 11.0)])
        sg = self._brief()
        self.assertEqual((sg[0]["from"], sg[0]["to"], sg[0]["count"]), ("", "사회", 2))

    def test_corrections_from_a_different_starting_value_are_not_suggested(self):
        self.install(
            rows=[_row(H1), _row(_h(2), cats=("경제",)), _row(_h(3), cats=("경제",))],
            patches=[_patch(_h(2), "복실", "content_category", ["경제"], ["사회"], 10.0),
                     _patch(_h(3), "딱지", "content_category", ["경제"], ["사회"], 11.0)])
        self.assertEqual(self._brief(), [])               # 출발값이 다르면 선례가 아니다

    def test_other_services_are_not_suggested(self):
        self.install(
            rows=[_row(H1), _row(_h(2), service="스포츠"), _row(_h(3), service="스포츠")],
            patches=[_patch(_h(2), "복실", "content_category", ["정치"], ["사회"], 10.0),
                     _patch(_h(3), "딱지", "content_category", ["정치"], ["사회"], 11.0)])
        self.assertEqual(self._brief(), [])

    def test_own_corrections_are_not_suggested_back(self):
        self.install(
            rows=[_row(H1)],
            patches=[_patch(H1, "복실", "content_category", ["정치"], ["사회"], 10.0),
                     _patch(H1, "딱지", "content_category", ["정치"], ["사회"], 11.0)])
        self.assertEqual(self._brief(), [])


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
            r = RA.call(tool, _args(_h(99)), team=TEAM)
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
            self.assertIn("error", RA.call(tool, _args("  "), team=TEAM), tool)

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
            if spec.get("after_only"):               # 단계를 못 받으면 막을 수가 없다
                self.assertIn("stage", schema.get("properties", {}), name)

    def test_shared_internal_tools_are_reachable(self):
        """골드 브리핑은 클라이언트가 화면 값으로 조립해야 하는데(서버가 못 만든다) 그러려면
        분류 기준 정의문이 필요하다. get_taxonomy 는 해시를 안 받아 어떤 콘텐츠를 보는지
        서버에 알리지 않고, 응답도 콘텐츠와 무관해 골드든 아니든 완전히 같다."""
        r = RA.call("get_taxonomy", {"kind": "intent", "service": SVC}, team=TEAM)
        self.assertNotIn("error", r)
        self.assertTrue(r["values"])
        self.assertIn("get_taxonomy", RA.registry())
        for name in RA.TOOLS:                        # 트랙 A 정의가 이름 충돌에서 이긴다
            self.assertIs(RA.registry()[name], RA.TOOLS[name], name)

    def test_shared_tools_still_need_a_team(self):
        self.assertIn("error", RA.call("get_taxonomy", {"kind": "intent"}, team=None))

    def test_route_is_registered_behind_the_team_gate(self):
        fn, gate = SV._POST_ROUTES["/assist"]
        self.assertEqual(gate, "team")                    # 로그인 + 팀 소속(fail-closed)


if __name__ == "__main__":
    unittest.main()
