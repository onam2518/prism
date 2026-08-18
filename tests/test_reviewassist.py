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

# 판단 재료를 담을 수 있는 키 전부. 도구 응답 모양이 바뀌어도 "재료가 안 나간다" 단언이
# 따라가도록 여기 모아 둔다 — 특정 키 이름을 테스트에 박으면 계약이 바뀔 때 단언이 조용히
# 무력해진다(2026-08-13 dissent 를 3줄 집계로 바꾸며 items 가 사라졌다).
PAYLOAD_KEYS = ("items", "lines", "values", "summary3", "suggestions", "evidence", "criteria")


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
    def __init__(self, feedback=None, patches=None, golden=None):
        self._fb, self._pt = dict(feedback or {}), list(patches or [])
        self._golden = set(golden or ())

    def golden_hashes(self, team=None):
        return set(self._golden)

    def feedback_map(self, team=None):
        return self._fb

    def patch_rows(self, limit=5000, team=None, content_hash=None):
        return list(self._pt)

    def ent_list(self, q="", type_="", status="", limit=300):
        return [{"entity_id": "e1", "name": "벨루가", "type": "PS",
                 "status": "listed", "aliases": ["beluga"], "n_contents": 3}]


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
    def install(self, rows, feedback=None, patches=None, golden=None):
        """저장 계층을 가짜로 갈아끼우고, 도구가 어떤 team 으로 조회했는지 기록한다."""
        self.seen_team = []
        store = _FakeStore(feedback, patches, golden)
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
    def __init__(self, team, uid=""):
        self._team = team
        self._uid = uid

    def _req_team(self):
        return self._team

    def _bearer_uid(self):
        return self._uid


class TestRouteTeamResolution(unittest.TestCase):
    """라우트는 sqlite 단일 팀 편의를 위해 고정 스코프("local")를 쓴다. 이 값이 supabase
    모드로 새면 저장 계층이 팀 필터를 건 채로 존재하지 않는 팀을 보거나, 최악에는 가드가
    풀려 전 팀을 읽는다(감사 H1 과 같은 실패). 코드를 읽어야만 안전한 상태로 두지 않는다."""

    def _both(self, supa, req_team, body=b'{"tool":"content_brief","args":{"hash":"x"}}', uid=""):
        """라우트가 도구에 넘기는 (팀, 묻는 사람)."""
        seen = []
        o_supa, o_call = SV._supa, RA.call
        SV._supa = lambda: supa
        RA.call = lambda name, args, team=None, me="": (seen.append((team, me)), {"ok": True})[1]
        self.addCleanup(lambda: setattr(SV, "_supa", o_supa))
        self.addCleanup(lambda: setattr(RA, "call", o_call))
        fn, _gate = SV._POST_ROUTES["/assist"]
        fn(_FakeHandler(req_team, uid), body)
        return seen[0]

    def _resolve(self, supa, req_team, body=b'{"tool":"content_brief","args":{"hash":"x"}}'):
        return self._both(supa, req_team, body)[0]

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

    # ── 묻는 사람(me): '다른 검수자 의견' 에서 내 판정을 빼는 데 쓴다 ──────────
    def test_supabase_takes_me_from_the_session_only(self):
        """운영에서 본문 값을 쓰면 남의 이름으로 두 번 불러 **그 사람의 판정**을 알아낼 수 있다
        (이 도구가 감추려는 것이 정확히 그것이다). 로그인 uid 만 믿는다."""
        body = '{"tool":"reviewer_dissent","args":{"hash":"x","stage":"after"},"reviewer":"남의이름"}'.encode()
        self.assertEqual(self._both(True, "team-7", body, uid="uid-9")[1], "uid-9")

    def test_supabase_without_a_login_has_no_me(self):
        body = '{"tool":"reviewer_dissent","args":{"hash":"x","stage":"after"},"reviewer":"남의이름"}'.encode()
        self.assertEqual(self._both(True, "team-7", body, uid="")[1], "")

    def test_local_sqlite_takes_me_from_the_body(self):
        """로컬은 로그인이 없다 — 검수 저장 경로(_inject_reviewer)와 같은 규칙을 쓴다."""
        body = '{"tool":"reviewer_dissent","args":{"hash":"x","stage":"after"},"reviewer":"복실"}'.encode()
        self.assertEqual(self._both(False, None, body)[1], "복실")

    def test_me_is_absent_when_the_client_says_nothing(self):
        """구 클라이언트는 reviewer 를 안 보낸다 · 그때는 아무도 빼지 않는다(오류가 아니다)."""
        self.assertEqual(self._both(False, None)[1], "")


# ── 골드 문항 차단 ───────────────────────────────────────────────────────────
class TestGoldIsNeverExposed(Base):
    def setUp(self):
        # 골드 행이 콘텐츠·판정·교정 로그 어디에 섞여도 나가면 안 된다.
        # 교정은 임계값(2건) 이상 깔아 둔다 — 1건이면 임계값 때문에 안 나온 것인지
        # 골드 가드 때문에 안 나온 것인지 구분되지 않아 테스트가 거짓 안심을 준다.
        self.install(
            rows=[_row(H1), _row(GOLD_H, title="골드 원문")],
            feedback={GOLD_H: _confirmed("골드 정답")},
            patches=[_patch(GOLD_H, "복실", "intent", ["속보·사건 추적"], ["해설·팩트체크"], 10.0),
                     _patch(GOLD_H, "딱지", "intent", ["속보·사건 추적"], ["해설·팩트체크"], 11.0)],
        )

    def test_gold_never_gets_data_from_any_tool(self):
        for gh in ("gold:ok:abc", "goldf:bad:abc", GOLD_H):
            for tool in RA.TOOLS:
                r = RA.call(tool, _args(gh), team=TEAM)
                for key in PAYLOAD_KEYS:
                    self.assertFalse(r.get(key), f"{tool}({gh}) 가 {key} 를 냈다")

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

    def test_unresolvable_gold_hash_gets_nothing(self):
        """접두만 골드이고 밑 콘텐츠를 못 푸는 해시는 조회하지 않는다(fail-closed).

        그대로 조회하면 데이터에 섞인 골드 행 자체를 자료로 내주게 된다."""
        r = RA.call("content_brief", {"hash": GOLD_H, "stage": "after"}, team=TEAM)
        self.assertEqual(r.get("error"), RA.NOT_FOUND_MSG)
        for tool, spec in RA.TOOLS.items():
            if not spec.get("after_only"):
                continue
            r = RA.call(tool, _args(GOLD_H), team=TEAM)
            self.assertNotIn("error", r, tool)             # 오류 문구는 화면에 뜨고 = 신호
            for key in PAYLOAD_KEYS:
                self.assertFalse(r.get(key), f"{tool} 가 {key} 를 냈다")

    def test_gold_never_appears_among_precedents(self):
        r = RA.call("verdict_precedents", _args(), team=TEAM)
        for it in r.get("items") or []:
            self.assertFalse(PT.is_gold(it["hash"]), f"골드가 선례로 샜다: {it['hash']}")

    def test_gold_patches_never_become_suggestions(self):
        r = RA.call("content_brief", {"hash": H1, "stage": "after"}, team=TEAM)
        self.assertEqual(r["suggestions"], [])            # 골드 교정만 있었으므로 제안이 없다


# ── 골드 문항에도 평소대로 답한다(2026-08-13) ────────────────────────────────
class TestGoldAnsweredLikeAnyContent(Base):
    """큐가 뒤집는 자리가 등급에서 카테고리로 옮겨져(reviewops.GOLD_FLIP_ELEMENT) 이 모듈이
    골드에 답할 수 있게 됐다. 거절을 유지하면 골드에서만 "저장된 근거 없음" 이 되고,
    근거 적재율이 오를수록 그 빈칸이 골드를 가리킨다(그게 이 변경의 이유다).

    답하는 방식이 핵심이다. **큐가 뒤집는 요소는 전 콘텐츠에서 말하지 않는다.** 골드에서만
    가리면 그 차이가 다음 신호이고, 골드에서만 다르게 답하려면 뒤집기 규칙을 이 모듈에
    두 번째로 구현해야 한다. 그래서 골드 응답과 밑 콘텐츠 응답이 **완전히 같다** ·
    `/assist-ask`(자유질문)가 이 응답을 모델에 통째로 먹이므로, 화면이 안 그리는 필드에서
    갈려도 모델의 답이 갈리고 그 답이 곧 골드 표시가 된다."""

    def setUp(self):
        self.install(
            rows=[_row(H1, evidence="광고 문구 없음"), _row(_h(2)), _row(_h(3))],
            feedback={_h(2): _confirmed(), _h(3): _confirmed()},
            patches=[_patch(_h(2), "복실", "summary", "옛 리드문", "새 리드문", 10.0),
                     _patch(_h(3), "딱지", "summary", "옛 리드문", "새 리드문", 11.0)],
        )
        self.gold = "gold:bad:" + H1              # 큐가 내보내는 형식

    def _brief(self, h, stage="after"):
        return RA.call("content_brief", {"hash": h, "stage": stage}, team=TEAM)

    def test_gold_brief_is_identical_to_the_underlying_content(self):
        """도구 응답 자체가 한 글자도 다르지 않아야 한다(패널이 안 그리는 필드까지)."""
        g, n = self._brief(self.gold), self._brief(H1)
        self.assertNotIn("error", g)
        self.assertEqual(g, n)
        self.assertTrue(n["has_evidence"])                # 빈 비교로 통과하지 않게
        for stage in ("before", "after"):
            self.assertEqual(self._brief(self.gold, stage), self._brief(H1, stage), stage)

    def test_the_flipped_field_is_never_stated_as_fact(self):
        """참이 아닐 수 있는 값은 사실로 말하지 않는다 · 전 콘텐츠 공통(골드 분기 없음)."""
        key = RA.flip_blind_key()
        self.assertTrue(key)
        for h in (H1, self.gold):
            vals = self._brief(h)["values"]
            self.assertIn(key, vals)                      # 키는 남긴다(모양이 갈리면 안 된다)
            self.assertFalse(vals[key], h)

    def test_the_flipped_field_does_not_come_back_through_summary3(self):
        """비운 값이 3줄 요약으로 되돌아오면 자리만 옮긴 것이다."""
        for h in (H1, self.gold):
            blob = " ".join(self._brief(h)["summary3"])
            for v in _row(H1)["item_meta"]["content_category"]:
                self.assertNotIn(str(v), blob, h)
            self.assertNotIn("카테고리", blob, h)          # "미부여" 로 말하지도 않는다

    def test_no_suggestion_for_the_flipped_field(self):
        """비운 자리는 '비어 있던 필드를 채운 교정' 제안을 부른다. 그 자리는 아예 뺀다."""
        key = RA.flip_blind_key()
        for h in (H1, self.gold):
            for s in self._brief(h).get("suggestions") or []:
                self.assertNotEqual(s["field"], key, h)

    def test_precedents_for_gold_match_the_underlying_content(self):
        """골드만 늘 선례가 비면 그 빈칸이 신호다(운영에서 평범한 콘텐츠는 100% 선례가 있다)."""
        g = RA.call("verdict_precedents", _args(self.gold), team=TEAM)
        n = RA.call("verdict_precedents", _args(H1), team=TEAM)
        self.assertEqual(g, n)
        self.assertTrue(n["items"])                       # 빈 비교로 통과하지 않게

    def test_dissent_stays_shut_for_gold(self):
        """골드 원본은 '정확' 다수결로 골든이 된 콘텐츠라 그 의견 목록이 곧 정답이다.
        여기만 골드에서 닫힌다 · 운영에서 의견이 갈린 콘텐츠는 13.0% 뿐이라 흔한 빈칸이다."""
        g = RA.call("reviewer_dissent", _args(self.gold), team=TEAM)
        self.assertFalse(g.get("lines"))
        self.assertFalse(g.get("items"))

    def test_flipped_element_is_never_a_similarity_axis(self):
        """유사도 축 제외도 **전 콘텐츠 공통**. `why_similar` 는 화면에 그대로 뜨는 문구라
        `같은 카테고리: …` 가 뒤집힌 화면과 어긋나면 그 어긋남이 곧 정답이다."""
        from prism import reviewops as RV
        self.assertNotIn(RV.GOLD_FLIP_ELEMENT, [a[0] for a in RA._similarity_axes()])
        for h in (H1, self.gold):
            for it in RA.call("verdict_precedents", _args(h), team=TEAM)["items"]:
                self.assertNotIn("같은 카테고리", it["why_similar"], h)


# ── 독립성(가장 중요) ────────────────────────────────────────────────────────
class TestIndependence(Base):
    def setUp(self):
        self.install(
            rows=[_row(H1), _row(_h(2)), _row(_h(3))],
            feedback={_h(2): _confirmed(), _h(3): _confirmed()},
            patches=[_patch(_h(2), "딱지", "intent", ["속보·사건 추적"], ["해설·팩트체크"], 10.0),
                     _patch(_h(3), "복실", "intent", ["속보·사건 추적"], ["해설·팩트체크"], 11.0)],
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
                for key in PAYLOAD_KEYS:          # 모양이 바뀌어도 '재료가 안 나간다' 는 그대로
                    self.assertFalse(r.get(key), f"{tool} stage={stage!r} · {key}")

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

    def test_golden_origin_precedents_lose_their_identity_only(self):
        """골드 문항의 원본은 골든셋에 올라간 확정 콘텐츠라 results 에 평범한 행으로 있다.

        제목을 남기면 검수자가 나중에 큐의 골드 문항에서 그걸 알아보고 정답을 안다(선례에
        확정 판정과 등급이 실려 있으니 곧 정답 해설이다). 통째로 빼면 기능 값이 크게 깎이므로
        판단 재료는 남기고 알아보는 손잡이만 지운다."""
        self.install([_row(H1), _row(_h(2), title="골든 원본"), _row(_h(3), title="평범한 선례")],
                     feedback={_h(2): _confirmed(), _h(3): _confirmed()},
                     golden={_h(2)})
        items = {it["title"]: it for it in
                 RA.call("verdict_precedents", _args(), team=TEAM)["items"]}
        self.assertEqual(set(items), {"", "평범한 선례"})       # 골든 원본만 이름을 잃는다
        blinded = items[""]
        for f in RA.GOLDEN_BLIND_FIELDS:
            self.assertEqual(blinded[f], "", f)
        self.assertEqual((blinded["verdict"], blinded["n"]), ("bad", 2))   # 판단 재료는 남는다
        self.assertIn("광고성", blinded["why_similar"])

    def test_blinding_keeps_the_item_shape_identical(self):
        """모양이 조건에 따라 달라지면 클라이언트가 갈라진다 — 키는 남기고 값만 비운다."""
        self.install([_row(H1), _row(_h(2)), _row(_h(3))],
                     feedback={_h(2): _confirmed(), _h(3): _confirmed()},
                     golden={_h(2)})
        shapes = {tuple(sorted(it)) for it in
                  RA.call("verdict_precedents", _args(), team=TEAM)["items"]}
        self.assertEqual(len(shapes), 1)

    def test_unknown_golden_set_blinds_everything(self):
        """안전장치가 오류에 열리면 안전장치가 아니다 — 못 읽으면 다 가리는 쪽으로 닫는다."""
        store = self.install([_row(H1), _row(_h(2))], feedback={_h(2): _confirmed()})
        store.golden_hashes = lambda team=None: (_ for _ in ()).throw(RuntimeError("조회 실패"))
        it = RA.call("verdict_precedents", _args(), team=TEAM)["items"][0]
        self.assertEqual(it["title"], "")
        self.assertEqual(it["verdict"], "bad")            # 기능은 남고 식별자만 닫힌다

    def test_other_services_and_unrelated_values_are_excluded(self):
        self.install(
            [_row(H1),
             _row(_h(2), service="스포츠"),                              # 다른 서비스
             _row(_h(3), reasons=("hate",), intent=("실용 정보",), cats=("스포츠",))],  # 겹치는 값 없음
            feedback={_h(2): _confirmed(), _h(3): _confirmed()})
        self.assertEqual(RA.call("verdict_precedents", _args(), team=TEAM)["total"], 0)


# ── 불일치 집계(3줄) ─────────────────────────────────────────────────────────
class TestDissent(Base):
    def _digest(self, feedback=None, patches=None):
        self.install([_row(H1)], feedback=feedback, patches=patches)
        return RA.call("reviewer_dissent", _args(), team=TEAM)

    def test_no_reviewer_name_or_note_survives_anywhere(self):
        """이름이 보이면 누가 그렇게 봤는지가 판단에 섞이고, 사유 원문은 가장 끌려가기 쉽다.

        응답을 통째로 문자열로 훑는다 — 어느 키에 담기든 새면 잡힌다."""
        r = self._digest(
            feedback={H1: _fb(("복실", "good", "문제 없다", 100.0),
                              ("딱지", "bad", "광고성이다", "2026-08-01T00:00:00+00:00"))},
            patches=[_patch(H1, "대식", "intent", ["A"], ["B"])])
        blob = str(r)
        for leaked in ("복실", "딱지", "대식", "문제 없다", "광고성이다"):
            self.assertNotIn(leaked, blob, f"{leaked} 가 응답에 남았다")

    def test_three_lines_are_assembled_from_stored_counts(self):
        r = self._digest(feedback={H1: _fb(("복실", "good", "", 1.0), ("딱지", "bad", "", 2.0),
                                           ("대식", "bad", "", 3.0))})
        self.assertEqual(len(r["lines"]), 3)
        self.assertIn("정확 1명", r["lines"][0])
        self.assertIn("수정 필요 2명", r["lines"][0])
        self.assertIn("소수 의견 1명", r["lines"][2])
        self.assertEqual((r["n"], r["split"]), (3, True))

    def test_element_line_counts_people_from_both_axes(self):
        """판정 시 고른 요소와 실제 교정 로그를 합쳐 '몇 사람이' 지적했나를 센다."""
        fb = _fb(("복실", "bad", "", 1.0), ("딱지", "bad", "", 2.0))
        fb["verdicts"][0]["element"] = "intent,category"
        fb["verdicts"][1]["element"] = "intent"
        r = self._digest(feedback={H1: fb},
                         patches=[_patch(H1, "대식", "category", ["A"], ["B"])])
        line = r["lines"][1]
        self.assertIn("인텐트 2명", line)                  # 판정 축
        self.assertIn("카테고리 2명", line)                # 판정 + 교정 축 합산
        self.assertFalse(r["truncated"])

    def test_element_labels_come_from_the_single_source(self):
        """라벨을 여기서 새로 지으면 같은 요소가 검수 화면과 다른 이름으로 불린다."""
        from prism import feedback_loop as FL
        fb = _fb(("복실", "bad", "", 1.0))
        fb["verdicts"][0]["element"] = "summary"
        self.assertIn(FL.ELEM_KO["summary"], self._digest(feedback={H1: fb})["lines"][1])

    def test_unknown_and_operational_elements_are_not_counted(self):
        """rerun·undo 는 검수자의 요소 지적이 아니라 운영 기록이다 · 모르는 것은 버린다."""
        r = self._digest(feedback={H1: _fb(("복실", "bad", "", 1.0))},
                         patches=[_patch(H1, "복실", "rerun:a->b", {}, {}),
                                  _patch(H1, "복실", "undo:verdict", {}, {}),
                                  _patch(H1, "복실", "없는요소", {}, {})])
        self.assertIn("지적한 요소 없음", r["lines"][1])

    def test_too_many_elements_are_reported_as_truncated(self):
        fb = _fb(("복실", "bad", "", 1.0))
        fb["verdicts"][0]["element"] = "summary,entities,intent,category,grade,quality"
        r = self._digest(feedback={H1: fb})
        self.assertTrue(r["truncated"])                   # 조용히 줄이지 않는다
        self.assertEqual(r["lines"][1].count("명"), RA.DIGEST_ELEM_MAX)

    def test_agreement_is_not_a_split(self):
        r = self._digest(feedback={H1: _fb(("복실", "bad", "", 1.0), ("딱지", "bad", "", 2.0))})
        self.assertFalse(r["split"])
        self.assertEqual(r["n"], 2)
        self.assertIn("의견 일치", r["lines"][2])

    def test_no_verdicts_yields_an_empty_digest(self):
        """재료가 없으면 자리를 채우지 않는다 — 빈 줄 3개가 아니라 빈 목록이다."""
        r = self._digest()
        self.assertEqual(r["lines"], [])
        self.assertEqual((r["n"], r["split"], r["truncated"]), (0, False, False))

    def test_lines_are_never_a_length_other_than_zero_or_three(self):
        """화면이 개수를 분기하지 않아도 되게 · 0 아니면 3만 나온다."""
        cases = [None,
                 {H1: _fb(("복실", "good", "", 1.0))},
                 {H1: _fb(("복실", "good", "", 1.0), ("딱지", "bad", "", 2.0))}]
        for feedback in cases:
            self.assertIn(len(self._digest(feedback=feedback)["lines"]), (0, 3))


# ── 묻는 사람 본인의 판정은 빼고 센다 ────────────────────────────────────────
class TestMyOwnVerdictIsExcluded(Base):
    """'다른 검수자 의견' 이 이름값을 하게 만드는 규칙(2026-08-13 신설).

    ## 이 변경이 닫는 것

    내 표를 세면 **판정 뒤에는 평범한 콘텐츠가 절대 비지 않는다**(stage=after 가 곧 '내 표가
    있다' 는 뜻이라). 이 도구는 골드에서 빈 결과를 주므로 그 상태에서는 빈 결과 = 골드였고,
    검수자가 칩 한 번으로 골드를 알아볼 수 있었다(2026-08-13 실측: 나만 판정한 콘텐츠는
    3줄·n=1 · 골드는 빈 결과 · 화면 말풍선 높이도 128.9px 대 58.4px 로 갈렸다).
    내 표를 빼면 '나만 판정한 콘텐츠' 도 빈 결과가 되어 골드의 빈 결과가 흔한 모양이 된다.

    ## 이 변경이 닫지 못하는 것 (다음 사람에게)

    **다 막히지 않았다.** 검수 인원이 늘어 콘텐츠마다 나 말고도 2~3명이 붙는 운영에서는
    평범한 콘텐츠가 다시 거의 안 비고, 골드만 늘 빈다. 골드 합성 해시(`gold:*`)는 feedback
    표에 아예 들어가지 않는 구조라 이 계층에서 닫을 수 있는 문제가 아니다(백로그).
    여기서 없는 의견을 지어내면 이 모듈 4번 규칙이 깨진다 — 완화가 아니라 거짓말이 된다.

    ## 무력화 실측 (2026-08-13 · 이 규칙을 깨고 이 파일 + test_assist_ask.py 를 돌린 결과)

      33 내 표를 그대로 센다 (5)          · 34 분포에서만 빼고 요소 축은 안 뺌 (1)
      35 me 를 도구 인자로 받게 엶 (1)    · 36 운영에서도 본문 reviewer 를 믿음 (2)
      39 자유질문 자료는 내 표를 그대로 셈 (1)

    ⚠️ 34·35·39 는 **유일한 눈이다. 지우지 말 것.** 특히 39 는 처음에 탈출했다 —
    도구를 직접 부르는 단언만으로는 `_ask_sources` 가 `me` 를 안 넘겨도 전부 통과한다.
    """

    def _digest(self, feedback, me="", patches=None):
        self.install([_row(H1)], feedback=feedback, patches=patches)
        return RA.call("reviewer_dissent", _args(), team=TEAM, me=me)

    def test_only_me_looks_exactly_like_a_content_nobody_judged(self):
        """**이 파일에서 가장 중요한 단언.** 나만 판정한 콘텐츠와 아무도 안 본 콘텐츠, 그리고
        골드가 모두 같은 모양이어야 골드가 티 나지 않는다."""
        mine = self._digest({H1: _fb(("복실", "bad", "광고성", 1.0))}, me="복실")
        nobody = self._digest({}, me="복실")
        gold = RA.call("reviewer_dissent", _args(GOLD_H), team=TEAM, me="복실")
        self.assertEqual(mine, nobody)
        self.assertEqual(mine, gold)
        self.assertEqual(mine["lines"], [])

    def test_my_verdict_is_out_of_every_number(self):
        """분포·인원·갈림 모두 나를 뺀 값이다. 한쪽만 빼면 앞뒤가 안 맞는 세 줄이 나온다."""
        r = self._digest({H1: _fb(("복실", "good", "", 1.0), ("딱지", "bad", "", 2.0),
                                  ("대식", "bad", "", 3.0))}, me="복실")
        self.assertEqual(r["n"], 2)                       # 3명 중 나를 뺀 2명
        self.assertIn("수정 필요 2명", r["lines"][0])
        self.assertNotIn("정확", r["lines"][0])           # 내 '정확' 은 남의 분포가 아니다
        self.assertFalse(r["split"])                      # 남들끼리는 갈리지 않았다
        self.assertIn("의견 일치", r["lines"][2])

    def test_my_pointed_elements_are_out_of_the_second_line(self):
        """요소 축에서도 나를 뺀다(판정 시 고른 요소 · 내가 남긴 교정 로그 둘 다)."""
        fb = _fb(("복실", "bad", "", 1.0), ("딱지", "bad", "", 2.0))
        fb["verdicts"][0]["element"] = "summary"          # 나만 지적한 요소
        fb["verdicts"][1]["element"] = "intent"
        r = self._digest({H1: fb}, me="복실",
                         patches=[_patch(H1, "복실", "category", ["A"], ["B"])])
        line = r["lines"][1]
        self.assertIn("인텐트 1명", line)
        self.assertNotIn("리드문", line)                  # 내 판정 축
        self.assertNotIn("카테고리", line)                # 내 교정 로그 축

    def test_an_unknown_asker_changes_nothing(self):
        """구 클라이언트는 이름을 안 보낸다 · 그때는 아무도 빼지 않는다(오류가 아니다)."""
        fb = {H1: _fb(("복실", "good", "", 1.0), ("딱지", "bad", "", 2.0))}
        self.assertEqual(self._digest(fb, me="")["n"], 2)
        self.assertEqual(self._digest(fb, me="없는사람")["n"], 2)

    def test_the_asker_cannot_be_chosen_from_the_arguments(self):
        """인자로 남의 이름을 넣어 두 번 부르면 그 차이로 **그 사람의 판정**이 드러난다.

        `me` 는 스키마에 없으므로 공용 디스패처가 걸러 낸다 — 앞단이 세션에서 넣는 값만 산다.
        (`needs_me` 를 지우면 세션 값도 안 들어가고, 그때는 이 단언이 아니라 위 단언들이 깨진다)"""
        fb = {H1: _fb(("복실", "good", "", 1.0), ("딱지", "bad", "", 2.0))}
        self.install([_row(H1)], feedback=fb)
        r = RA.call("reviewer_dissent", dict(_args(), me="복실"), team=TEAM)
        self.assertEqual(r["n"], 2, "인자로 넣은 이름이 먹혔다(개인 판정 노출 통로)")

    def test_the_free_question_uses_the_same_digest(self):
        """칩과 자유질문이 다른 집계를 쓰면 그 차이가 또 하나의 신호다."""
        self.install([_row(H1)], feedback={H1: _fb(("복실", "bad", "광고성", 1.0))})
        got = RA._ask_tool("reviewer_dissent", dict(AFTER), H1, TEAM, "복실")
        self.assertEqual(got.get("lines"), [])


# ── 수정 제안(판정 뒤) ───────────────────────────────────────────────────────
class TestSuggestions(Base):
    def _brief(self):
        return RA.call("content_brief", {"hash": H1, "stage": "after"}, team=TEAM)["suggestions"]

    def test_suggestion_is_assembled_from_past_corrections(self):
        self.install(
            rows=[_row(H1), _row(_h(2)), _row(_h(3))],
            patches=[_patch(_h(2), "복실", "intent", ["속보·사건 추적"], ["해설·팩트체크"], 10.0),
                     _patch(_h(3), "딱지", "intent", ["속보·사건 추적"], ["해설·팩트체크"], 11.0)])
        sg = self._brief()
        self.assertEqual(len(sg), 1)
        self.assertEqual(set(sg[0]), {"field", "from", "to", "count", "reviewers", "basis"})
        self.assertEqual((sg[0]["field"], sg[0]["from"], sg[0]["to"]),
                         ("intent", "속보·사건 추적", "해설·팩트체크"))
        self.assertEqual((sg[0]["count"], sg[0]["reviewers"]), (2, 2))
        self.assertIn("2건", sg[0]["basis"])              # 근거는 실제로 센 사실뿐이다
        self.assertIn("2명", sg[0]["basis"])

    def test_one_correction_is_not_a_precedent(self):
        """1건은 선례가 아니라 한 사람의 판단이다."""
        self.install(rows=[_row(H1), _row(_h(2))],
                     patches=[_patch(_h(2), "복실", "intent", ["속보·사건 추적"], ["해설·팩트체크"])])
        self.assertEqual(self._brief(), [])

    def test_headcount_is_reported_separately_from_the_count(self):
        """같은 사람이 두 번 고친 것과 두 사람이 각각 고친 것은 무게가 다르다 · 판단은 검수자 몫."""
        self.install(rows=[_row(H1), _row(_h(2)), _row(_h(3))],
                     patches=[_patch(_h(2), "복실", "intent", ["속보·사건 추적"], ["해설·팩트체크"], 10.0),
                              _patch(_h(3), "복실", "intent", ["속보·사건 추적"], ["해설·팩트체크"], 11.0)])
        sg = self._brief()
        self.assertEqual((sg[0]["count"], sg[0]["reviewers"]), (2, 1))

    def test_list_fields_are_compared_element_by_element(self):
        """목록 전체가 같아야 한다면 운영에서 거의 안 걸려 기능이 없는 것과 같다."""
        self.install(
            rows=[_row(H1, intent=("속보·사건 추적", "해설·팩트체크")), _row(_h(2), intent=("속보·사건 추적", "인터뷰")),
                  _row(_h(3), intent=("속보·사건 추적", "심층 분석"))],
            patches=[_patch(_h(2), "복실", "intent", ["속보·사건 추적", "인터뷰"], ["속보·사건 추적", "후기·리뷰·비평"], 10.0),
                     _patch(_h(3), "딱지", "intent", ["속보·사건 추적", "심층 분석"], ["속보·사건 추적", "후기·리뷰·비평"], 11.0)])
        # 대상의 현재 값에 없는 요소('경제'·'문화')에서 출발한 교정이라 제안이 되지 않는다
        self.assertEqual(self._brief(), [])
        self.install(
            rows=[_row(H1, intent=("속보·사건 추적", "해설·팩트체크")), _row(_h(2), intent=("속보·사건 추적", "해설·팩트체크")),
                  _row(_h(3), intent=("속보·사건 추적", "해설·팩트체크"))],
            patches=[_patch(_h(2), "복실", "intent", ["속보·사건 추적", "해설·팩트체크"], ["속보·사건 추적", "후기·리뷰·비평"], 10.0),
                     _patch(_h(3), "딱지", "intent", ["속보·사건 추적", "해설·팩트체크"], ["속보·사건 추적", "후기·리뷰·비평"], 11.0)])
        sg = self._brief()
        self.assertEqual((sg[0]["from"], sg[0]["to"]), ("해설·팩트체크", "후기·리뷰·비평"))   # 바뀐 요소만 짚는다

    def test_ambiguous_multi_element_changes_are_not_paired(self):
        """여러 개가 한꺼번에 바뀌면 무엇이 무엇으로 바뀌었는지 모른다. 짝을 지어내지 않는다."""
        self.install(
            rows=[_row(H1, intent=("속보·사건 추적", "해설·팩트체크")), _row(_h(2), intent=("속보·사건 추적", "해설·팩트체크")),
                  _row(_h(3), intent=("속보·사건 추적", "해설·팩트체크"))],
            patches=[_patch(_h(2), "복실", "intent", ["속보·사건 추적", "해설·팩트체크"], ["인터뷰", "후기·리뷰·비평"], 10.0),
                     _patch(_h(3), "딱지", "intent", ["속보·사건 추적", "해설·팩트체크"], ["인터뷰", "후기·리뷰·비평"], 11.0)])
        self.assertEqual(self._brief(), [])

    def test_filling_an_empty_field_is_counted(self):
        """빈 카테고리 채우기는 실제로 가장 잦은 교정이다 · 대상도 비어 있을 때만 센다."""
        self.install(
            rows=[_row(H1, intent=()), _row(_h(2), intent=()), _row(_h(3), intent=())],
            patches=[_patch(_h(2), "복실", "intent", [], ["해설·팩트체크"], 10.0),
                     _patch(_h(3), "딱지", "intent", [], ["해설·팩트체크"], 11.0)])
        sg = self._brief()
        self.assertEqual((sg[0]["from"], sg[0]["to"], sg[0]["count"]), ("", "해설·팩트체크", 2))

    def test_corrections_from_a_different_starting_value_are_not_suggested(self):
        self.install(
            rows=[_row(H1), _row(_h(2), intent=("인터뷰",)), _row(_h(3), intent=("인터뷰",))],
            patches=[_patch(_h(2), "복실", "intent", ["인터뷰"], ["해설·팩트체크"], 10.0),
                     _patch(_h(3), "딱지", "intent", ["인터뷰"], ["해설·팩트체크"], 11.0)])
        self.assertEqual(self._brief(), [])               # 출발값이 다르면 선례가 아니다

    def test_other_services_are_not_suggested(self):
        self.install(
            rows=[_row(H1), _row(_h(2), service="스포츠"), _row(_h(3), service="스포츠")],
            patches=[_patch(_h(2), "복실", "intent", ["속보·사건 추적"], ["해설·팩트체크"], 10.0),
                     _patch(_h(3), "딱지", "intent", ["속보·사건 추적"], ["해설·팩트체크"], 11.0)])
        self.assertEqual(self._brief(), [])

    def test_own_corrections_are_not_suggested_back(self):
        self.install(
            rows=[_row(H1)],
            patches=[_patch(H1, "복실", "intent", ["속보·사건 추적"], ["해설·팩트체크"], 10.0),
                     _patch(H1, "딱지", "intent", ["속보·사건 추적"], ["해설·팩트체크"], 11.0)])
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
        """/assist 의 소비자는 검수 화면만이 아니라 내부 검수 보조 에이전트다. 에이전트가
        분류 체계·엔티티 사전을 함께 봐야 하고, 그 도구는 prismtools 에 이미 한 벌 있다 —
        tools_for("internal") 이 이 앞단을 위한 계약이라 복제하지 않고 그대로 받는다."""
        r = RA.call("get_taxonomy", {"kind": "intent", "service": SVC}, team=TEAM)
        self.assertNotIn("error", r)
        self.assertTrue(r["values"])
        self.assertIn("get_taxonomy", RA.registry())
        for name in RA.TOOLS:                        # 트랙 A 정의가 이름 충돌에서 이긴다
            self.assertIs(RA.registry()[name], RA.TOOLS[name], name)

    def test_shared_tools_still_need_a_team(self):
        self.assertIn("error", RA.call("get_taxonomy", {"kind": "intent"}, team=None))

    def test_shared_tools_reach_the_store_through_assist(self):
        """get_taxonomy 는 사전만 읽어 주입 없이도 답한다 — 그것만으로는 배선이 검증되지 않는다.

        lookup_entity 는 prismtools 의 _SV 주입에 의존하고, 그 주입 줄은 serve.py 에서 이
        모듈 import 바로 윗줄이라 머지 해소 때 함께 날아가기 쉽다(2026-08-12 트랙 B 실측:
        주입이 빠지면 500 이 아니라 '사전이 준비되지 않았습니다' 가 조용히 나간다). /assist 가
        공용 도구를 열어 준 이상 그 조용한 열화는 검수 화면에서도 그대로 보인다.

        ⚠️ **지우지 말 것.** PTL._SV 를 끊고 돌리면 이 파일 58건 중 **오직 이 1건만** 깨진다
        (무력화 실측). 트랙 B 파일에도 비슷한 단언이 있지만 그건 다른 파일·다른 앞단이라,
        여기서는 이 테스트가 유일한 눈이다. 없으면 그 열화가 이 스위트에서 완전히 안 보인다."""
        r = RA.call("lookup_entity", {"name": "벨루가"}, team=TEAM)
        self.assertNotIn("error", r)
        self.assertEqual(r["items"][0]["name"], "벨루가")

    def test_route_is_registered_behind_the_team_gate(self):
        fn, gate = SV._POST_ROUTES["/assist"]
        self.assertEqual(gate, "team")                    # 로그인 + 팀 소속(fail-closed)

    def test_serve_injects_the_module_reference(self):
        """주입이 빠지면 도구가 오류 없이 '콘텐츠를 찾지 못했습니다' 만 낸다 — 조용한 열화다.

        serve.py 는 여러 세션이 동시에 만지는 허브 파일이고 이 주입은 import 바로 다음 줄이라
        머지 해소에서 흘리기 쉽다(2026-08-12 트랙 B 브랜치와 실제로 인접 충돌).

        이 단언이 없어도 스위트는 깨진다 — 다만 **58건 중 28건**이 한꺼번에 무너져 원인이
        안 보인다(무력화 실측). 이 한 줄은 새 안전망이 아니라 그 무더기 속에서 원인을 이름으로
        가리키는 역할이다. 계약까지 지켜 주지는 않는다(저장 계층 계약은 아래 왕복 테스트 몫)."""
        self.assertIs(RA._SV, SV)


class TestRealStoreRoundTrip(unittest.TestCase):
    """가짜 스토어를 안 쓰고 실제 sqlite 를 한 번 지난다.

    다른 테스트의 _FakeStore 는 이 모듈의 판단 로직을 검증하지만 **저장 계층과의 계약이
    어긋나는 것은 못 잡는다** — 가짜가 옛 모양 그대로 답하면 테스트는 계속 통과하고 운영만
    깨진다(2026-08-12 트랙 B 가 자기 파일에서 같은 사각지대를 발견 · 그쪽은 응답 필드가
    빠졌는데도 테스트가 하나도 안 깨졌다).

    무엇을 잡는지 무력화 실험으로 확인한 것만 적는다.
      · **evidence 가 적재를 통과하는지.** 이 필드는 원래 trace.agent_verdicts 에만 있다가
        supabase 적재에서 버려져 운영에서 0% 였고, 그래서 jsonb 로 실리는 quality_meta 로
        옮긴 것이다(schema.py 2026-08-12). 적재가 다시 흘리면 이 도구는 근거가 있는 콘텐츠에도
        "근거 없음" 을 말한다 — 조용히 틀리는 쪽이라 가짜로는 영영 안 보인다.
        (실측: 저장에서 evidence 를 빼면 evidence=None 으로 이 테스트가 깨진다)
      · **feedback_map 의 합의 계약(n·agree·consensus).** 가짜는 이 모양을 손으로 만든다.
        실제가 바뀌면 선례가 조용히 0건이 된다. (실측: agree 를 빼면 total 이 1→0)

    _row_key 는 여기서 검증되지 않는다 — body_hash 가 16자가 아니면 정체성 4필드로 다시
    계산하는 폴백이 있어서, 저장 키가 안 실려도 같은 값이 나온다(실측 확인). 그 폴백이
    있다는 사실 자체가 계약이므로 굳이 여기서 재확인하지 않는다."""

    def setUp(self):
        import tempfile
        from prism.store import Store, content_hash

        self.chash = content_hash
        d = tempfile.mkdtemp()
        store = Store(os.path.join(d, "t.db"))

        def _content(title, body):
            return {"displayServiceName": SVC, "title": title, "subtitle": "", "body": body}

        def _out(grade="R", reasons=("ad",), evidence=""):
            return {"content_ref": {}, "routing": {}, "legal_meta": {},
                    "quality_meta": {"finalGrade": grade, "reasons": list(reasons),
                                     "review": "auto", "evidence": evidence},
                    "item_meta": {"summary": "요약", "entities": [], "intent": [INTENT],
                                  "content_category": ["정치"], "topic": ""},
                    "trace": {"model": "m", "cost_usd": 0.0}}

        self.target = _content("검수 대상 기사", "본문 A")
        past = _content("과거 확정 기사", "본문 B")
        store.save_many([(self.target, _out(evidence="구매 링크로 끝난다")),
                         (past, _out())], run_id="r1")
        for i, who in enumerate(("복실", "딱지")):     # 2인 확정 = 선례 자격
            store.save_feedback(self.chash(past), SVC, past["title"], "bad",
                                "analyze", "광고성으로 봤다", 100.0 + i, reviewer=who)

        o_store, o_cache = SV.get_store, SV._STORE
        SV.get_store = lambda: store
        SV._STORE = store
        self.addCleanup(lambda: setattr(SV, "get_store", o_store))
        self.addCleanup(lambda: setattr(SV, "_STORE", o_cache))

    def test_brief_and_precedents_survive_a_real_store(self):
        h = self.chash(self.target)
        brief = RA.call("content_brief", {"hash": h}, team=TEAM)
        self.assertNotIn("error", brief)
        self.assertEqual(brief["values"]["service"], SVC)
        # 적재가 evidence 를 흘리면 근거 있는 콘텐츠에도 "근거 없음" 을 말하게 된다
        self.assertEqual(brief["evidence"], "구매 링크로 끝난다")
        self.assertTrue(brief["has_evidence"])

        pre = RA.call("verdict_precedents", _args(h), team=TEAM)
        self.assertNotIn("error", pre)
        self.assertEqual(pre["total"], 1, "실제 feedback_map 의 합의 계약이 어긋났다")
        it = pre["items"][0]
        self.assertEqual((it["verdict"], it["n"]), ("bad", 2))
        self.assertIn("광고성", it["why_similar"])

    def test_dissent_digest_reads_real_feedback_rows(self):
        """집계 수치가 실제 저장 행과 맞는지 · 가짜로는 feedback_map 계약 변화를 못 잡는다."""
        from prism.store import content_hash
        st = SV.get_store()
        h = content_hash(self.target)
        # 정확 2 · 수정 필요 1 로 **비대칭**하게 만든다. 1대1 이면 라벨이 뒤바뀌어도 문자열이
        # 같아서 수치가 틀려도 단언이 통과한다(무력화 실측에서 실제로 안 잡혔다).
        st.save_feedback(h, SVC, "t", "good", "analyze", "문제 없다", 1.0,
                         reviewer="복실", element="summary")
        st.save_feedback(h, SVC, "t", "good", "analyze", "괜찮다", 2.0,
                         reviewer="대식", element="summary")
        st.save_feedback(h, SVC, "t", "bad", "analyze", "광고다", 3.0,
                         reviewer="딱지", element="intent")
        r = RA.call("reviewer_dissent", _args(h), team=TEAM)
        self.assertTrue(r["split"])
        self.assertEqual(r["n"], 3)
        self.assertIn("정확 2명", r["lines"][0])
        self.assertIn("수정 필요 1명", r["lines"][0])
        self.assertIn("소수 의견 1명", r["lines"][2])
        self.assertIn("리드문 2명", r["lines"][1])        # 실제 feedback.element 축
        self.assertIn("인텐트 1명", r["lines"][1])
        for leaked in ("복실", "딱지", "대식", "문제 없다", "괜찮다", "광고다"):
            self.assertNotIn(leaked, str(r), leaked)


def _fbx(*triples):
    """supabase 셰이프 feedback 한 건: verdicts 항목이 reviewer(표시명)·reviewer_id(uid) 둘 다.
    triples=(display_name, uid, verdict)."""
    vs = [{"reviewer": nm, "reviewer_id": uid, "verdict": v, "note": "", "ts": 1.0 + i}
          for i, (nm, uid, v) in enumerate(triples)]
    g = sum(1 for x in vs if x["verdict"] == "good")
    b = sum(1 for x in vs if x["verdict"] == "bad")
    return {"verdicts": vs, "good": g, "bad": b, "n": len(vs),
            "consensus": "good" if g > b else "bad" if b > g else "split",
            "agree": g == 0 or b == 0, "verdict": "", "stage": "", "note": ""}


class TestDissentUidNormalization(Base):
    """supabase 모드: me 는 Bearer uid 인데 verdicts 는 reviewer=표시명·reviewer_id=uid 라
    본인 판정 제외가 uid 대 표시명 불일치로 실패하던 것(감사 P1). 골드 빈 결과가 곧 골드
    표시로 새던 자리이기도 하다."""

    def test_my_verdict_excluded_by_uid_in_supabase_shape(self):
        self.install([_row(H1)], feedback={H1: _fbx(
            ("복실", "uid-me", "good"),                # 나(표시명은 복실, uid 로 로그인)
            ("딱지", "uid-2", "bad"),
            ("대식", "uid-3", "bad"))})
        r = RA.reviewer_dissent(hash=H1, stage="after", team=TEAM, me="uid-me")
        self.assertEqual(r["n"], 2)                    # 내 표 제외 → 남 2명
        self.assertFalse(r["split"])                   # 남은 둘 다 bad = 일치(내 good 이 섞이면 split 오판)
        self.assertIn("수정 필요 2명", r["lines"][0])

    def test_only_my_verdict_becomes_empty_like_gold(self):
        """나만 판정한 콘텐츠는 빈 결과가 되어 골드의 빈 결과와 같은 모양이 된다(누수 차단)."""
        self.install([_row(H1)], feedback={H1: _fbx(("복실", "uid-me", "good"))})
        r = RA.reviewer_dissent(hash=H1, stage="after", team=TEAM, me="uid-me")
        self.assertEqual(r["lines"], [])
        self.assertEqual(r["n"], 0)

    def test_tie_is_labeled_even(self):
        """동수는 '소수 의견 N명'(사실과 다름)이 아니라 '동수'로 표기."""
        self.install([_row(H1)], feedback={H1: _fbx(
            ("복실", "uid-me", "good"),                # 나 = 제외
            ("딱지", "uid-2", "good"),
            ("대식", "uid-3", "bad"))})
        r = RA.reviewer_dissent(hash=H1, stage="after", team=TEAM, me="uid-me")
        self.assertTrue(r["split"])
        self.assertIn("동수", r["lines"][2])

    def test_elem_tally_counts_a_person_once_across_axes(self):
        """같은 사람이 판정(verdicts)과 교정(patch_rows)에 다른 키로 나타나도 한 명으로 센다."""
        self.install([_row(H1)],
                     feedback={H1: _fbx(("딱지", "uid-2", "bad"), ("대식", "uid-3", "bad"))},
                     patches=[{"hash": H1, "reviewer": "딱지", "reviewer_id": "uid-2",
                               "element": "ad", "before": {}, "after": {}, "ts": 9.0}])
        r = RA.reviewer_dissent(hash=H1, stage="after", team=TEAM, me="uid-me")
        # uid-2 는 판정·교정 양쪽에 있으나 한 명 · 지적 요소 인원이 2를 넘지 않는다
        self.assertLessEqual(r["n"], 2)


if __name__ == "__main__":
    unittest.main()
