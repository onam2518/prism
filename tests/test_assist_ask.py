"""검수 보조 자유질문(/assist-ask)과 정책 예시 도구(get_examples) 회귀 테스트 (2026-08-13).

자유질문은 이 계층에서 **모델이 등장하는 유일한 자리**다. 위쪽 도구들은 저장된 값을 조립할
뿐이라 틀려도 틀린 티가 나는데, 생성된 문장은 그럴듯해서 틀려도 티가 안 난다. 그래서 여기
단언은 문구가 아니라 규칙이다.

  · 판정 전에는 열리지 않는다. 열면 모델이 근거와 기준만으로 사실상 판정을 해 버리고,
    그 순간 우리가 재는 대상이 사람이 아니라 모델이 된다(이 기능 전체의 전제)
  · 콘텐츠 해시는 서버가 못 박는다. 모델이 고를 수 있으면 그게 곧 남의 콘텐츠 통로다
  · 도구 결과가 비면 **모델을 부르지 않는다.** 지어낼 자리를 없앤다(프롬프트가 아니라 구조)
  · 출처 없는 문장은 응답에 실리지 않는다. 대조할 수 없는 문장은 또 하나의 추측이다
  · 골드와 자료 없는 평범한 콘텐츠가 **같은 응답**이다. 거절 방식도 신호가 된다
  · 사용자당 상한이 있고, 자료 유무와 무관하게 센다. 안 줄면 그것도 골드 신호다
  · 정책 예시는 **초안**이라고 응답에 적힌다. 확정으로 읽히면 틀린 기준으로 판정한다

## 가짜 모델을 쓰는 방식 (여기 손대는 다음 사람에게)

모델 호출은 가짜로 대체한다. 그런데 **가짜가 얌전하면 테스트가 전부 통과하면서 아무것도 재지
않는다.** 계약을 지키는 응답만 주는 가짜에게는 서버의 검사가 할 일이 없기 때문이다.
그래서 이 파일의 가짜는 일부러 계약을 어긴다(출처 없는 문장 · 없는 id 인용 · 잘못된 타입 ·
예외). `test_the_fake_model_really_violates_the_contract` 가 그 사실 자체를 못 박는다.
가짜를 "고쳐서" 얌전하게 만들면 그 테스트가 먼저 깨지며 이유를 알려 준다. 고치지 말 것.

또 생성이 일어나는 테스트는 **가짜가 실제로 불렸는지**(`calls`)를 함께 단언한다. 안 그러면
자료가 비어 모델을 건너뛴 경우와 구분되지 않아, "출처를 지웠다" 가 아니라 "애초에 답이
없었다" 로 통과해 버린다(가장 밟기 쉬운 함정).

## 무력화 실측 (2026-08-13 · 규칙을 하나씩 깨고 전체 스위트를 돌린 결과)

27개 무력화 전건이 잡혔다(탈출 0). 괄호 안은 **잡은 단언 수**다.

  01 판정 전 거절 제거 (4)          · 02 모델이 준 해시를 쓴다 setdefault (1)
  03 자료 없이도 모델 호출 (3)      · 04 출처 없는 문장 통과 (2)
  05 없는 출처 id 유지 (2)          · 06 골드만 따로 거절 (9)
  07 도구 오류 응답을 자료로 (1)    · 08 사용자 상한 제거 (1)
  09 자료 없는 질문은 미과금 (1)    · 10 예시 초안 표시 제거 (1)
  11 초안 표시가 프롬프트에서 누락 (1) · 12 빈 예시를 정의문으로 채움 (1)
  13 모르는 값을 비슷한 값으로 갈음 (1) · 14 답한 모델 미노출 (1)
  15 답한 모델 미기록 (1)           · 16 이 파일이 기본 모델을 정함 (1)
  17 설정 해석기를 안 봄 (3)        · 18 호출 실패가 원인 미지시 (1)
  19 시도한 모델 미기록 (1)         · 20 모델 예외 원문 노출 (1)
  21 라우트가 본문 team 을 믿음 (1) · 22 속도 제한 제거 (2)
  23 속도 제한 키 전역 공유 (1)     · 24 자료 상한 제거로 절단 은폐 (1)
  25 가린 자리를 '없음' 이라 말함 (3) · 26 가림 판정 건너뜀 (1)
  27 가림 판정을 표시 키로 함 (1)

⚠️ **(1) 인 항목은 그 단언이 유일한 눈이다. 지우지 말 것.** 27개 중 19개가 그렇다.
특히 02 는 통합 경로로는 전혀 드러나지 않는다(지금은 서버가 도구 인자를 직접 만들어
경쟁하는 해시가 없다). 26·27 은 **지금 설정에서는 아예 드러나지 않는다.** 큐가 뒤집는 요소가
카테고리라 값이 이미 비어 있어서다. 그 요소가 품질 사유로 옮겨 가는 날에만 작동하는 눈이다.

규칙을 고칠 때는 단언을 지우지 말고 **먼저 이 실험을 다시 돌려** 무엇이 유일한 눈인지부터
확인할 것. 실측하지 않으면 "테스트가 있으니 안전하다" 는 착각으로 남는다(2026-08-12 검수자
의견 집계 작업에서 실제로 1건짜리 눈을 하나 놓쳤다).

**기준선이 낡을 수 있다는 것도 남겨 둔다.** PR #441 로 `content_brief` 의 골드 거절이 걷히자
골드에 자료가 생겼고, 그러자 두 단언이 '자료 없는 경우'를 더는 재지 않게 됐다. 골드 동일성
쪽은 깨져서 알았지만 **09 는 깨지지도 않고 조용히 통과했다.** 실측을 다시 돌려서 잡았다.
계약이 바뀌면 실패한 테스트뿐 아니라 **통과한 테스트도 의심할 것.**

실행: python3 -m pytest tests/ -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prism.serve as SV                       # noqa: E402  (RA._SV 주입이 여기서 일어난다)
from prism import dictionaries as D            # noqa: E402
from prism import prismtools as PT             # noqa: E402
from prism import reviewassist as RA           # noqa: E402

TEAM = "team-1"
SVC = "뉴스"
INTENT = "속보·사건 추적"
CAT = "News and Politics / Politics"
EVIDENCE = "본문이 제품 구매 링크로 끝나 광고성으로 봤다"

H1 = "0000000000000001"          # 자료가 있는 평범한 콘텐츠 · 골드가 깔고 앉은 밑 콘텐츠
H_BARE = "0000000000000002"      # 실재하지만 근거·선례가 없는 평범한 콘텐츠
H_OPIN = "0000000000000003"      # 검수자 의견이 갈린 콘텐츠(운영 13.0%)
H_MISSING = "0000000000000009"   # 이 팀 색인에 없는 해시
# 골드 합성 해시. content_brief 가 밑 콘텐츠(H1)로 풀어 평소대로 답한다(PR #441).
GOLD_H = "gold:ok:0000000000000001"

# 콘텐츠에만 답이 있는 질문(사전 이름이 등장하지 않는다) · 자료 유무가 갈리는 자리를 만든다.
Q_CONTENT = "이거 왜 그렇게 봤어?"
# 사전에 답이 있는 질문(분류값 이름이 그대로 등장) · 콘텐츠와 무관하게 자료가 잡힌다.
Q_POLICY = "심층 분석 예시 알려줘"


def _row(h, *, evidence="", intent=(INTENT,), cats=(CAT,)):
    return {"content_ref": {"displayServiceName": SVC, "title": "제목", "subtitle": "",
                            "body": "본문", "body_hash": h},
            "quality_meta": {"finalGrade": "R", "reasons": ["ad"], "review": "auto",
                             "evidence": evidence},
            "item_meta": {"summary": "요약", "intent": list(intent),
                          "content_category": list(cats), "entities": [], "topic": ""},
            "trace": {}}


def _fb(*reviewers):
    """확정 판정 한 건(store/supastore 와 같은 shape)."""
    vs = [{"reviewer": r, "verdict": "bad", "note": "광고성으로 봤다", "ts": 1.0 + i,
           "element": "quality"} for i, r in enumerate(reviewers)]
    return {"verdicts": vs, "good": 0, "bad": len(vs), "n": len(vs), "consensus": "bad",
            "agree": True, "verdict": "bad", "stage": "", "note": "광고성으로 봤다"}


class _FakeStore:
    def __init__(self, feedback=None):
        self.reports = {}
        self._fb = dict(feedback or {})

    def golden_hashes(self, team=None):
        return {H1}                 # 골드가 깔고 앉은 콘텐츠는 골든셋에 올라 있다

    def feedback_map(self, team=None):
        return self._fb

    def patch_rows(self, limit=5000, team=None, content_hash=None):
        return []

    def ent_list(self, q="", type_="", status="", limit=300):
        return []

    def save_report(self, kind, payload, team=None):
        self.reports[(kind, team)] = payload

    def get_report(self, kind, team=None):
        return self.reports.get((kind, team))


class _FakeLLM:
    """계약을 **어기는** 가짜. 얌전한 가짜는 서버 검사를 재지 못한다(파일 머리말 참고)."""

    # 출처 없는 문장 · 없는 id 인용 · dict 아닌 항목 · 빈 텍스트: 넷 다 나가면 안 된다.
    BAD = {"answer": [
        {"text": "근거는 자료에 적힌 그대로입니다.", "sources": ["s1"]},   # 유일하게 살아남을 문장
        {"text": "제가 알기로는 이런 경우 보통 R 입니다.", "sources": []},
        {"text": "다른 팀 사례를 보면 G 였습니다.", "sources": ["s404"]},
        {"text": "", "sources": ["s1"]},
        "문자열 항목",
    ], "unknown": ""}

    _DEFAULT = object()          # obj=None 도 검사 대상이라 '미지정'과 구분한다

    def __init__(self, obj=_DEFAULT, boom=False, model="claude-opus-4-8"):
        self.obj = self.BAD if obj is self._DEFAULT else obj
        self.boom = boom
        self.model = model
        self.calls = []

    def complete_json(self, system, user, tag=""):
        self.calls.append({"system": system, "user": user, "tag": tag})
        if self.boom:
            raise RuntimeError("라우터 500 · 내부 사정이 그대로 드러나는 문구")
        return self.obj, None


class Base(unittest.TestCase):
    def setUp(self):
        self.store = _FakeStore()
        self.rows = [_row(H1, evidence=EVIDENCE), _row(H_BARE)]
        self.seen_team = []
        o_rows, o_store, o_llm = SV.results_rows, SV.get_store, SV.llm_for_model

        def rows_fn(limit=5000, team=None):
            self.seen_team.append(team)
            return list(self.rows)

        SV.results_rows = rows_fn
        SV.get_store = lambda: self.store
        self.addCleanup(lambda: setattr(SV, "results_rows", o_rows))
        self.addCleanup(lambda: setattr(SV, "get_store", o_store))
        self.addCleanup(lambda: setattr(SV, "llm_for_model", o_llm))
        RA._ASK_HITS.clear()                        # 상한 카운터는 프로세스 전역이라 테스트마다 초기화
        self.addCleanup(RA._ASK_HITS.clear)
        # 설정 계층의 모델 해석기(serve.assist_model)는 있을 수도 없을 수도 있다(머지 시점 차이).
        # 테스트가 그 시점에 따라 달라지면 안 되므로 기본은 '없음' 으로 두고, 필요한 테스트가
        # 직접 꽂는다.
        o_am = getattr(SV, "assist_model", None)
        if o_am is not None:
            del SV.assist_model
            self.addCleanup(lambda: setattr(SV, "assist_model", o_am))

    def llm(self, **kw):
        """가짜 모델을 꽂고 그 핸들을 돌려준다(호출 여부를 단언에 쓴다)."""
        fake = _FakeLLM(**kw)
        SV.llm_for_model = lambda m, mock: (fake, "fake")
        return fake

    def ask(self, hash=H1, stage="after", question=Q_CONTENT, team=TEAM, uid="u1", **kw):
        return RA.ask(hash=hash, stage=stage, question=question, team=team, uid=uid, **kw)


# ── 가짜가 계약을 어기는지부터 못 박는다 ─────────────────────────────────────
class TestTheFakeItself(Base):
    def test_the_fake_model_really_violates_the_contract(self):
        """가짜가 얌전해지면 아래 단언들이 전부 무의미해진다. 그 사실을 여기서 고정한다.

        가짜를 '고쳐서' 계약을 지키게 만들지 말 것. 그 순간 이 파일은 통과하면서 아무것도
        재지 않는 상태가 된다(이번 작업에서 여러 번 밟은 함정)."""
        rows = _FakeLLM.BAD["answer"]
        self.assertTrue(any(isinstance(r, dict) and not r.get("sources") for r in rows),
                        "가짜가 출처 없는 문장을 내지 않는다 = 4번 규칙을 재지 못한다")
        self.assertTrue(any(isinstance(r, dict) and "s404" in (r.get("sources") or []) for r in rows),
                        "가짜가 없는 id 를 인용하지 않는다 = 환각 인용을 재지 못한다")
        self.assertTrue(any(not isinstance(r, dict) for r in rows),
                        "가짜가 잘못된 타입을 내지 않는다 = 타입 방어를 재지 못한다")


# ── 1. 판정 전에는 열리지 않는다 ─────────────────────────────────────────────
class TestOnlyAfterAVerdict(Base):
    def test_ask_is_refused_before_a_verdict(self):
        """판정 전에 열면 "그래서 맞아 틀려" 가 나오고 모델이 사실상 판정을 해 버린다."""
        fake = self.llm()
        r = self.ask(stage="before")
        self.assertEqual(r.get("error"), RA.ASK_STAGE_MSG)
        self.assertEqual(fake.calls, [], "판정 전인데 모델을 불렀다(과금 + 답 유출)")

    def test_unknown_stage_falls_back_to_before_and_is_refused(self):
        """모르는 값은 덜 주는 쪽으로 수렴한다(오타·구버전 클라이언트가 열지 못하게)."""
        fake = self.llm()
        for stage in ("", None, "later", "AFTER_", "판정후", 7, True, ["after"]):
            r = self.ask(stage=stage)
            self.assertEqual(r.get("error"), RA.ASK_STAGE_MSG, f"stage={stage!r}")
        self.assertEqual(fake.calls, [])

    def test_after_is_accepted_and_case_insensitive(self):
        self.llm()
        self.assertNotIn("error", self.ask(stage="  AFTER "))

    def test_refusal_before_a_verdict_leaks_nothing_about_the_content(self):
        """판정 전 거절은 해시 검증보다 먼저다. 실재하는 해시와 없는 해시의 응답이 같아야
        '그 해시가 있기는 한가' 라는 부수 정보도 새지 않는다."""
        a = self.ask(hash=H1, stage="before")
        b = self.ask(hash=H_MISSING, stage="before")
        c = self.ask(hash=GOLD_H, stage="before")
        self.assertEqual(a, b)
        self.assertEqual(a, c)


# ── 2. 해시는 서버가 못 박는다 ───────────────────────────────────────────────
class TestServerPinsTheHash(Base):
    def _spy(self):
        """도구 계층에 들어간 인자를 그대로 기록한다."""
        seen = []
        o_call = RA.call

        def spy(name, args, team=None, me=""):
            seen.append({"tool": name, "args": dict(args or {}), "team": team, "me": me})
            return o_call(name, args, team=team, me=me)

        RA.call = spy
        self.addCleanup(lambda: setattr(RA, "call", o_call))
        return seen

    def test_every_hash_taking_tool_call_uses_the_request_hash(self):
        seen = self._spy()
        self.llm()
        self.ask(hash=H1, question=Q_POLICY)
        used = [c for c in seen if "hash" in c["args"]]
        self.assertTrue(used, "해시를 받는 도구가 하나도 안 불렸다(테스트가 아무것도 안 잰다)")
        for c in used:
            self.assertEqual(c["args"]["hash"], H1, f"{c['tool']} 이 다른 해시로 불렸다")

    def test_a_model_supplied_hash_is_overwritten_not_merged(self):
        """모델이 도구 인자를 채우게 되어도 이 문 하나만 지나면 해시는 고정된다.

        **이 단언이 유일한 눈이다. 지우지 말 것.** 지금은 서버가 도구 인자를 직접 만들어
        경쟁하는 해시가 없다. `a["hash"] = ch` 를 `setdefault` 로 바꿔도 통합 경로는
        멀쩡히 통과한다(무력화 실측에서 확인). 모델이 도구를 고르게 되는 날 그 한 글자가
        곧 남의 콘텐츠 통로가 되고, 그때 이걸 잡아 줄 다른 단언은 없다."""
        for evil in (H_BARE, GOLD_H, "", None, 0, ["x"], {"a": 1}):
            r = RA._ask_tool("content_brief", {"hash": evil, "stage": "after"}, H1, TEAM)
            self.assertEqual(r.get("values", {}).get("service"), SVC)
            self.assertEqual(r.get("evidence"), EVIDENCE, f"evil={evil!r} 로 다른 콘텐츠를 읽었다")

    def test_hashless_tools_do_not_get_a_hash_injected(self):
        """공용 사전 도구는 해시를 받지 않는다. 스키마에 없는 인자는 걸러져야 한다."""
        r = RA._ask_tool("get_examples", {"kind": "intent", "values": [INTENT]}, H1, TEAM)
        self.assertTrue(r.get("items"))
        self.assertNotIn("hash", r)

    def test_the_team_never_comes_from_tool_arguments(self):
        seen = self._spy()
        self.llm()
        self.ask()
        self.assertTrue(seen)
        for c in seen:
            self.assertEqual(c["team"], TEAM, "세션 팀이 아닌 값으로 도구가 불렸다")
            self.assertNotIn("team", c["args"], "도구 인자에 team 이 들어갔다(교차 팀 통로)")

    def test_no_team_no_answer(self):
        fake = self.llm()
        for t in (None, "", "   "):
            r = RA.ask(hash=H1, stage="after", question=Q_POLICY, team=t, uid="u1")
            self.assertIn("error", r, f"team={t!r} 인데 답했다")
        self.assertEqual(fake.calls, [], "팀도 없이 모델을 불렀다")


# ── 3. 자료가 없으면 생성하지 않는다 ─────────────────────────────────────────
class TestNoMaterialNoGeneration(Base):
    def test_the_model_is_not_called_when_the_tools_return_nothing(self):
        """프롬프트로 부탁하는 게 아니라 호출 자체가 일어나지 않는다."""
        fake = self.llm()
        r = self.ask(hash=H_MISSING, question=Q_CONTENT)
        self.assertEqual(fake.calls, [], "자료가 없는데 모델을 불렀다")
        self.assertEqual(r["sources"], [])
        self.assertEqual(r["answer"], [])
        self.assertFalse(r["generated"])
        self.assertEqual(r["note"], RA.ASK_NO_SOURCE)

    def test_material_present_means_the_model_runs(self):
        """대조군: 위 테스트가 '항상 안 부른다' 로 통과하는 상태가 아님을 보인다."""
        fake = self.llm()
        r = self.ask(hash=H1, question=Q_CONTENT)
        self.assertTrue(r["sources"])
        self.assertEqual(len(fake.calls), 1)
        self.assertTrue(r["generated"])

    def test_the_model_only_ever_sees_the_gathered_sources(self):
        """모델에게 주는 것은 질문 + 우리가 모은 자료뿐이다(웹·파일·외부 페치 없음).

        프롬프트에 실린 자료 id 는 응답 sources 의 id 와 정확히 같은 집합이어야 한다.
        어긋나면 모델이 우리가 모르는 재료를 봤거나, 검수자가 대조할 수 없는 자료가 있다."""
        fake = self.llm()
        r = self.ask(hash=H1, question=Q_POLICY)
        self.assertEqual(len(fake.calls), 1)
        user = fake.calls[0]["user"]
        for s in r["sources"]:
            self.assertIn("[%s]" % s["id"], user)
            self.assertIn(s["text"], user)
        self.assertIn(r["question"], user)


# ── 4. 출처 없는 문장은 나가지 못한다 ────────────────────────────────────────
class TestEverySentenceIsCited(Base):
    def test_ungrounded_and_hallucinated_citations_are_dropped(self):
        fake = self.llm()
        r = self.ask(hash=H1, question=Q_CONTENT)
        self.assertEqual(len(fake.calls), 1, "모델이 안 불렸다 = 이 단언은 아무것도 안 잰다")
        texts = [a["text"] for a in r["answer"]]
        self.assertIn("근거는 자료에 적힌 그대로입니다.", texts)
        self.assertNotIn("제가 알기로는 이런 경우 보통 R 입니다.", texts)   # 출처 없음
        self.assertNotIn("다른 팀 사례를 보면 G 였습니다.", texts)          # 없는 id 인용

    def test_every_returned_line_carries_a_real_source_id(self):
        """화면이 출처 없는 문장을 그릴 방법 자체를 없앤다(문자열이 아니라 {text, sources})."""
        fake = self.llm()
        r = self.ask(hash=H1, question=Q_POLICY)
        self.assertEqual(len(fake.calls), 1)
        ids = {s["id"] for s in r["sources"]}
        self.assertTrue(r["answer"], "답이 비면 이 단언이 공회전한다")
        for a in r["answer"]:
            self.assertTrue(a["text"])
            self.assertTrue(a["sources"], "출처 없는 문장이 나갔다")
            self.assertTrue(set(a["sources"]) <= ids, "실재하지 않는 출처를 달았다")

    def test_a_source_names_which_tool_and_which_value_it_came_from(self):
        """검수자가 대조하려면 '어느 도구의 어떤 값'인지 알아야 한다."""
        self.llm()
        r = self.ask(hash=H1, question=Q_POLICY)
        self.assertTrue(r["sources"])
        for s in r["sources"]:
            self.assertEqual(set(s), {"id", "tool", "field", "label", "text"})
            self.assertIn(s["tool"], RA.registry(), "모르는 도구 이름이 출처로 나갔다")
            self.assertTrue(s["field"] and s["text"])

    def test_a_model_answer_of_the_wrong_shape_yields_no_answer(self):
        for junk in (None, [], "문자열", {"answer": "문자열"}, {"answer": [None, 1]}, {}):
            fake = self.llm(obj=junk)
            r = self.ask(hash=H1, question=Q_CONTENT)
            self.assertEqual(len(fake.calls), 1, f"{junk!r}: 모델이 안 불렸다")
            self.assertEqual(r["answer"], [], f"{junk!r} 로 답이 만들어졌다")
            self.assertEqual(r["note"], RA.ASK_UNGROUNDED)

    def test_a_model_failure_never_leaks_its_message(self):
        """실패를 '친절하게 보여 주려는' 수정이 내부 사정을 그대로 내보낸다.

        문구를 고정값으로 못 박아 둔다. 예외 원문이 note·자료 어디로 새도 잡히게."""
        fake = self.llm(boom=True)
        r = self.ask(hash=H1, question=Q_CONTENT)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(r["answer"], [])
        self.assertEqual(r["note"], RA.ASK_UNGROUNDED)     # 고정 문구 · 예외 원문이 아니다
        self.assertNotIn("라우터", str(r))
        self.assertNotIn("500", str(r))
        for s in r["sources"]:                             # 자료 목록으로 새는 경로도 막는다
            self.assertNotIn("내부 사정", s["text"])


# ── 5. 골드를 특별 취급하지 않는다 ───────────────────────────────────────────
class TestGoldTakesTheSamePath(Base):
    def test_gold_answers_exactly_like_the_content_underneath(self):
        """거절 방식도 신호가 된다. 골드에서만 다른 모양이면 검수자가 골드를 알아본다.

        알아보는 순간 골드가 재려던 것(평소의 검수)이 사라진다.

        기준선(2026-08-13 · PR #441 로 이동): 종전에는 `content_brief` 가 골드를 거절해
        '자료 없는 콘텐츠'와 같았다. 지금은 골드 합성 해시를 **밑 콘텐츠로 풀어 평소대로**
        답하므로 비교 대상이 그 밑 콘텐츠(H1)다. 지키는 규칙 자체는 안 바뀌었다.
        **"골드 응답이 어떤 평범한 콘텐츠 부류와도 구분되지 않는다."**

        ⚠️ 뒤집는 요소(`reviewops.GOLD_FLIP_ELEMENT`)가 또 옮겨 가면 이 단언이 먼저 깨진다.
        그때도 지우지 말고 무엇과 같아야 하는지만 다시 정할 것."""
        fake = self.llm()
        gold = self.ask(hash=GOLD_H, question=Q_CONTENT)
        under = self.ask(hash=H1, question=Q_CONTENT)
        self.assertEqual(len(fake.calls), 2, "둘 중 하나가 모델을 건너뛰었다 = 경로가 갈렸다")
        self.assertTrue(gold["sources"], "골드가 여전히 빈손이다(거절이 남아 있나)")
        for k in ("answer", "sources", "generated", "note", "truncated", "model", "model_label"):
            self.assertEqual(gold[k], under[k], f"골드의 {k} 가 밑 콘텐츠와 다르다")

    def test_gold_now_answers_the_question_that_used_to_come_back_empty(self):
        """"이건 왜 R 이야" 는 콘텐츠에만 답이 있어 종전에는 골드에서 늘 빈손이었다.

        빈손이 빠르기까지 해서 그 속도 차이가 골드 신호였다. 그래서 이 PR 의 머지를 #441
        뒤로 미뤘다. 지금은 등급·근거가 참값이라(뒤집는 것은 카테고리 한 자리뿐) 평소대로 답한다."""
        fake = self.llm()
        r = self.ask(hash=GOLD_H, question="이건 왜 R 이야")
        self.assertTrue(r["generated"], "골드에서 모델을 안 불렀다")
        self.assertEqual(len(fake.calls), 1)
        self.assertTrue(r["answer"], "골드에서 답이 안 나갔다")
        self.assertEqual(r["note"], "")
        tools = {s["tool"] for s in r["sources"]}
        self.assertIn("content_brief", tools)          # 근거·등급이 실제로 실렸다
        self.assertIn(EVIDENCE, str(r["sources"]))

    def test_the_flipped_element_is_never_stated_as_absent(self):
        """큐가 뒤집는 자리를 비운 뒤 "카테고리 없음" 이라고 쓰면 화면에는 값이 그려져 있는
        골드에서 앞뒤가 안 맞고, 그 어긋남이 곧 정답이다. 자리를 **문장에서 통째로 뺀다.**

        자유질문은 도구 응답을 모델에 통째로 먹이므로 화면이 안 그리는 값도 여기서 갈리면
        모델의 답이 갈린다. `_summary3` 가 같은 이유로 같은 처리를 한다."""
        self.llm()
        blind = RA.flip_blind_key()
        self.assertTrue(blind, "가릴 요소를 못 읽었다(reviewops 상수 연결 확인)")
        label = {"content_category": "카테고리", "grade": "등급", "intent": "인텐트",
                 "reasons": "품질 사유"}[blind]
        for h in (GOLD_H, H1, H_BARE):
            blob = str(self.ask(hash=h, question=Q_CONTENT)["sources"])
            for lie in (f"{label} 없음", f"{label} 미부여", f"{label} 미상"):
                self.assertNotIn(lie, blob, f"{h}: 가린 자리를 '{lie}' 라고 사실처럼 말했다")
            self.assertNotIn(f"{label} {CAT}", blob, f"{h}: 가린 값을 그대로 실었다")

    def test_the_blind_rule_follows_the_element_key_not_the_display_key(self):
        """가림 판정은 표시 키가 아니라 **요소 키**로 한다.

        지금 뒤집는 것은 카테고리라 `content_brief` 가 값을 비워 두면 표시 로직만으로도 안 샌다.
        그런데 요소가 품질 사유로 옮겨 가면 다르다. `reason_labels` 는 `_values` 가 미리 만들어
        둔 **파생값**이라 원본(`reasons`)을 비워도 그대로 남는다. 표시 키로 판정하면 그때 가려야
        할 값이 라벨로 새어 나가고, 새는 그 순간은 아무도 안 보고 있을 것이다.
        **유일한 눈이다. 지우지 말 것** (지금 설정에서는 드러나지 않는 미래를 고정한다)."""
        from prism import reviewops as RV
        old = getattr(RV, "GOLD_FLIP_ELEMENT", "")
        RV.GOLD_FLIP_ELEMENT = "quality"
        self.addCleanup(lambda: setattr(RV, "GOLD_FLIP_ELEMENT", old))
        self.assertEqual(RA.flip_blind_key(), "reasons")
        self.llm()
        for h in (GOLD_H, H1):
            blob = str(self.ask(hash=h, question=Q_CONTENT)["sources"])
            self.assertNotIn("품질 사유", blob, f"{h}: 가려야 할 품질 사유가 라벨로 샜다")

    def test_remaining_falls_by_the_same_step_for_gold_and_plain(self):
        """자료가 채워졌으니 '자료가 없어 모델을 안 부르는' 경로 자체가 사라졌다.
        그래도 셈은 답의 내용과 무관해야 한다(줄어드는 폭이 다르면 그게 골드 판별기다)."""
        self.llm()
        steps = {}
        for name, h in (("gold", GOLD_H), ("plain", H1), ("bare", H_BARE)):
            RA._ASK_HITS.clear()
            seen = [self.ask(hash=h, question=Q_CONTENT, uid="u")["remaining"] for _ in range(3)]
            steps[name] = seen
        self.assertEqual(steps["gold"], steps["plain"])
        self.assertEqual(steps["gold"], steps["bare"])
        self.assertEqual(steps["gold"], [RA.ASK_DAILY_MAX - i for i in (1, 2, 3)])

    def test_gold_matches_the_majority_class_when_opinions_exist(self):
        """`reviewer_dissent` 만 골드에서 닫힌다(골드 원본의 의견 목록이 곧 정답이라서).

        그래서 남는 차이는 '의견 자료가 없다' 하나인데, 그 모양이 **의견 없는 평범한 콘텐츠**
        (운영 87.0%)와 같다. 골드를 특정하지 못한다는 뜻이다. 반대로 의견이 있는 콘텐츠(13.0%)
        와는 자료가 다른데, 그건 골드가 아니라 그 13% 쪽이 예외인 것이다."""
        self.store._fb = {H1: _fb("복실", "딱지"), H_OPIN: _fb("복실", "딱지")}
        self.rows = self.rows + [_row(H_OPIN, evidence=EVIDENCE)]
        self.llm()
        place = {}
        for name, h in (("gold", GOLD_H), ("no_opinion", H_BARE), ("has_opinion", H_OPIN)):
            r = self.ask(hash=h, question=Q_CONTENT)
            place[name] = {s["tool"] for s in r["sources"]}
        self.assertNotIn("reviewer_dissent", place["gold"])
        self.assertNotIn("reviewer_dissent", place["no_opinion"])
        self.assertIn("reviewer_dissent", place["has_opinion"])
        self.assertEqual(place["gold"], place["no_opinion"],
                         "골드가 다수 부류(의견 없는 콘텐츠)와도 구분된다")

    def test_a_tool_error_yields_no_source_even_when_it_carries_data(self):
        """도구가 오류와 데이터를 함께 돌려줘도 오류면 자료로 쓰지 않는다.

        골드에서 `content_brief` 는 거절 문구를 돌려준다. 그 문구가 자료에 실리면 그 한 줄이
        곧 "이건 골드다" 신호다. **지금 도구들은 오류일 때 데이터를 함께 싣지 않아 통합
        경로로는 이 규칙이 드러나지 않는다. 이 단언이 유일한 눈이다. 지우지 말 것.**"""
        o_call = RA.call
        RA.call = lambda name, args, team=None, me="": {
            "error": RA.GOLD_MSG, "values": {"grade": "R", "reason_labels": ["광고성"]},
            "evidence": "새면 안 되는 근거", "has_evidence": True,
            "items": [{"verdict": "bad", "n": 2, "why_similar": "같은 서비스"}],
            "lines": ["판정 정확 2명"]}
        self.addCleanup(lambda: setattr(RA, "call", o_call))
        fake = self.llm()
        r = self.ask(hash=H1, question=Q_POLICY)
        self.assertEqual(r["sources"], [], "오류 응답의 값이 자료로 실렸다")
        self.assertEqual(fake.calls, [])
        self.assertNotIn(RA.GOLD_MSG, str(r))

    def test_the_gold_refusal_wording_never_reaches_the_reviewer(self):
        """content_brief 는 골드를 거절하는데, 그 문구가 답이나 자료에 실리면 곧 골드 신호다."""
        self.llm()
        r = self.ask(hash=GOLD_H, question=Q_CONTENT)
        blob = str(r)
        self.assertNotIn(RA.GOLD_MSG, blob)
        self.assertNotIn("골드", blob)

    def test_gold_and_plain_content_run_the_same_code_path(self):
        """정책을 묻는 질문은 콘텐츠와 무관한 사전에서 자료가 나오므로 골드에서도 생성이 돈다.

        '골드는 언제나 즉시 빈손' 이 되지 않게 하는 장치다(경로가 갈리는 것을 줄인다)."""
        fake = self.llm()
        gold = self.ask(hash=GOLD_H, question=Q_POLICY)
        self.assertTrue(gold["sources"], "골드에서 정책 자료조차 안 잡혔다")
        self.assertTrue(gold["generated"])
        plain = self.ask(hash=H_BARE, question=Q_POLICY)
        self.assertTrue(plain["generated"])
        self.assertEqual(len(fake.calls), 2, "둘 중 하나가 모델을 건너뛰었다 = 경로가 갈렸다")

    def test_no_gold_branch_exists_in_the_ask_path(self):
        """규칙을 코드에서도 확인한다. is_gold 분기가 생기면 그 분기가 곧 신호원이 된다."""
        import inspect
        src = "".join(inspect.getsource(f) for f in
                      (RA.ask, RA._ask_sources, RA._ask_tool, RA._ask_clean, RA._ask_quota))
        self.assertNotIn("is_gold", src)
        self.assertNotIn("GOLD_PREFIXES", src)

    def test_the_quota_is_spent_even_when_there_is_no_material(self):
        """**셈은 답의 내용과 무관해야 한다.** 자료가 있든 없든 접수된 질문은 모두 센다.

        기준선(2026-08-13 · PR #441 로 이동): 종전에는 골드가 늘 자료 없는 쪽이라 골드 대
        평범한 콘텐츠로 쟀다. 지금은 골드에도 자료가 채워지므로 **정말로 자료가 없는 요청**
        (색인에 없는 해시)으로 잰다. 지키는 규칙은 그대로다.

        **유일한 눈이다. 지우지 말 것.** '모델을 부른 것만 세자' 는 아껴 쓰는 쪽으로 자연스러워
        보이는 수정인데, 그 순간 두 가지가 생긴다. ① 남은 횟수가 그 콘텐츠에 자료가 있는지를
        일러 주는 판별기가 된다 ② 자료 없는 해시로는 상한 없이 두드릴 수 있는 길이 열린다."""
        self.llm()
        empty = self.ask(hash=H_MISSING, question=Q_CONTENT, uid="uq")
        self.assertFalse(empty["sources"], "자료 없는 요청이 아니다(이 단언이 공회전한다)")
        self.assertFalse(empty["generated"])
        rich = self.ask(hash=H1, question=Q_CONTENT, uid="uq")
        self.assertTrue(rich["generated"])
        self.assertEqual(empty["remaining"], RA.ASK_DAILY_MAX - 1)
        self.assertEqual(rich["remaining"], RA.ASK_DAILY_MAX - 2)


# ── 6. 사용자당 상한 ─────────────────────────────────────────────────────────
class TestQuota(Base):
    def test_a_user_runs_out_and_the_model_stops_being_called(self):
        """**유일한 눈이다. 지우지 말 것.** 상한을 지나치는 수정은 테스트를 하나도 안 깨고
        과금만 늘린다(무력화 실측에서 이 단언 1건만 잡았다)."""
        fake = self.llm()
        last = None
        for _ in range(RA.ASK_DAILY_MAX):
            last = self.ask(uid="heavy", question=Q_POLICY)
        self.assertEqual(last["remaining"], 0)
        spent = len(fake.calls)
        over = self.ask(uid="heavy", question=Q_POLICY)
        self.assertEqual(over.get("error"), RA.ASK_QUOTA_MSG)
        self.assertEqual(len(fake.calls), spent, "상한을 넘겼는데 모델을 또 불렀다(과금)")

    def test_the_cap_is_per_user_not_global(self):
        self.llm()
        for _ in range(RA.ASK_DAILY_MAX):
            self.ask(uid="heavy", question=Q_POLICY)
        self.assertNotIn("error", self.ask(uid="other", question=Q_POLICY))

    def test_remaining_counts_down_by_one_per_question(self):
        self.llm()
        seen = [self.ask(uid="u9", question=Q_POLICY)["remaining"] for _ in range(3)]
        self.assertEqual(seen, [RA.ASK_DAILY_MAX - 1, RA.ASK_DAILY_MAX - 2, RA.ASK_DAILY_MAX - 3])

    def test_refused_questions_do_not_burn_the_quota(self):
        """판정 전·빈 질문은 접수되지 않았으므로 세지 않는다(모델도 안 불렸다)."""
        self.llm()
        self.ask(uid="u8", stage="before")
        self.ask(uid="u8", question="   ")
        self.assertEqual(self.ask(uid="u8", question=Q_POLICY)["remaining"], RA.ASK_DAILY_MAX - 1)


# ── 7. 상한·클램프·오류 은닉 ────────────────────────────────────────────────
class TestLimitsAndErrors(Base):
    def test_a_long_question_is_clipped_not_rejected(self):
        self.llm()
        r = self.ask(question="심층 분석 " + "가" * 5000)
        self.assertNotIn("error", r)
        self.assertLessEqual(len(r["question"]), RA.ASK_Q_MAX + 1)   # +1 = 말줄임표

    def test_an_empty_question_is_refused(self):
        fake = self.llm()
        for q in ("", "   ", None, 0):
            self.assertEqual(self.ask(question=q).get("error"), RA.ASK_EMPTY_Q, repr(q))
        self.assertEqual(fake.calls, [])

    def test_sources_are_capped_and_truncation_is_reported(self):
        """조용한 절단은 '다 봤다' 로 읽혀 판단을 그르친다."""
        o_max = RA.ASK_SRC_MAX
        RA.ASK_SRC_MAX = 2
        self.addCleanup(lambda: setattr(RA, "ASK_SRC_MAX", o_max))
        self.llm()
        r = self.ask(hash=H1, question=Q_POLICY)
        self.assertEqual(len(r["sources"]), 2)
        self.assertTrue(r["truncated"])

    def test_answer_lines_are_capped(self):
        many = {"answer": [{"text": f"문장 {i}", "sources": ["s1"]} for i in range(50)]}
        self.llm(obj=many)
        r = self.ask(hash=H1, question=Q_CONTENT)
        self.assertLessEqual(len(r["answer"]), RA.ASK_LINES_MAX)

    def test_a_broken_store_does_not_leak_its_exception(self):
        class Boom:
            def __getattr__(self, name):
                raise RuntimeError("sqlite3.OperationalError: no such table: results")

        SV.get_store = lambda: Boom()
        self.rows = []
        self.llm()
        r = self.ask(hash=H1, question=Q_CONTENT)
        self.assertNotIn("sqlite3", str(r))
        self.assertNotIn("no such table", str(r))


# ── 8. 어떤 모델이 답했는지 남긴다 ───────────────────────────────────────────
class TestModelIsRecorded(Base):
    """어떤 모델이 답했는지는 응답·로그·설정 해석에 **각각 따로** 걸린다. 그래서 이 클래스의
    단언은 대부분 하나씩만 잡는다(무력화 14·15·16·18·19 전부 1건 · 지우지 말 것)."""

    def test_the_answering_model_is_named_in_the_response(self):
        self.llm(model="claude-opus-4-8")
        r = self.ask(hash=H1, question=Q_POLICY)
        self.assertEqual(r["model"], "claude-opus-4-8")
        self.assertEqual(r["model_label"], "Claude Opus 4.8")   # modelmeta 단일 원천

    def test_the_answering_model_is_written_to_the_log(self):
        """설정이 바뀌면 답의 성격도 바뀐다. 무엇으로 답했는지 남지 않으면 되짚을 수 없다."""
        self.llm(model="gpt-5.4")
        self.ask(hash=H1, question=Q_POLICY, uid="누구")
        log = self.store.reports.get(("assist_ask_log", TEAM)) or {}
        last = (log.get("entries") or [])[-1]
        self.assertEqual(last["model"], "gpt-5.4")
        self.assertTrue(last["generated"])
        self.assertEqual(last["uid"], "누구")

    def _spy_model(self):
        """llm_for_model 에 어떤 모델 id 가 들어갔는지 기록한다."""
        seen = []
        SV.llm_for_model = lambda m, mock: (seen.append(m), (_FakeLLM(), "fake"))[1]
        return seen

    def test_the_model_comes_from_the_settings_resolver_not_from_here(self):
        """모델 해석은 설정 계층(`prism.config.assist_model` · serve 재수출) 하나뿐이다.

        여기에 기본값 표를 두면 설정과 갈라지고, 갈라진 쪽이 조용히 다른 모델로 과금한다."""
        seen = self._spy_model()
        SV.assist_model = lambda: "solar-pro3"
        self.addCleanup(lambda: delattr(SV, "assist_model"))
        self.ask(hash=H1, question=Q_POLICY)
        self.assertEqual(seen, ["solar-pro3"])

    def test_an_explicit_injection_wins_over_the_settings_resolver(self):
        seen = self._spy_model()
        SV.assist_model = lambda: "solar-pro3"
        self.addCleanup(lambda: delattr(SV, "assist_model"))
        RA.ASK_MODEL_RESOLVER = lambda: "gpt-5.4-mini"
        self.addCleanup(lambda: setattr(RA, "ASK_MODEL_RESOLVER", None))
        self.ask(hash=H1, question=Q_POLICY)
        self.assertEqual(seen, ["gpt-5.4-mini"])

    def test_a_broken_resolver_does_not_stop_the_answer(self):
        """해석이 흔들려도 답은 나간다. 빈 문자열 = llm_for_model 이 설정 모델로 해석한다.

        **이 파일이 모델 이름을 지어내면 안 된다.** 설정 계층의 기본값과 갈라지는 순간
        화면에 표시된 모델과 실제로 과금된 모델이 달라진다."""
        for bad in (lambda: 1 / 0, lambda: None, lambda: "  ", "함수가 아님"):
            seen = self._spy_model()
            RA.ASK_MODEL_RESOLVER = bad
            self.addCleanup(lambda: setattr(RA, "ASK_MODEL_RESOLVER", None))
            self.assertNotIn("error", self.ask(hash=H1, question=Q_POLICY))
            self.assertEqual(seen, [""], f"{bad!r}: 이 파일이 기본 모델을 지어냈다")

    def test_an_uncallable_model_points_at_the_setting_that_chose_it(self):
        """선택지에 라우터 키 없는 모델도 뜨므로 '설정은 멀쩡한데 호출만 실패' 가 실제로 난다.

        "답하지 못했습니다" 로 끝내면 검수자도 관리자도 어디를 봐야 할지 모른다."""
        SV.assist_model = lambda: "glm-5.2"
        self.addCleanup(lambda: delattr(SV, "assist_model"))
        SV.llm_for_model = lambda m, mock: (None, "라우터 키 없음(BizRouter·Timely)")
        r = self.ask(hash=H1, question=Q_POLICY)
        self.assertEqual(r["answer"], [])
        self.assertTrue(r["sources"], "모델이 없어도 자료는 보여 준다")
        self.assertIn("glm-5.2", r["note"], "어느 모델이 안 되는지 안 알려 준다")
        self.assertIn("시스템 설정", r["note"], "어디를 봐야 하는지 안 알려 준다")
        self.assertNotIn("라우터 키", str(r))          # 사유 원문은 싣지 않는다

    def test_the_attempted_model_is_logged_even_when_the_call_fails(self):
        """generated=False 와 함께 읽으면 "그 모델로 시도했는데 안 됐다" 가 된다.
        비워 두면 나중에 무엇이 실패했는지 되짚을 단서가 남지 않는다."""
        SV.assist_model = lambda: "glm-5.2"
        self.addCleanup(lambda: delattr(SV, "assist_model"))
        SV.llm_for_model = lambda m, mock: (None, "라우터 키 없음")
        self.ask(hash=H1, question=Q_POLICY)
        last = ((self.store.reports.get(("assist_ask_log", TEAM)) or {}).get("entries") or [])[-1]
        self.assertEqual(last["model"], "glm-5.2")
        self.assertFalse(last["generated"])


# ── 9. 정책 예시 도구 ────────────────────────────────────────────────────────
class TestGetExamples(unittest.TestCase):
    def call(self, **args):
        return PT.call("get_examples", args, team=TEAM)

    def test_examples_are_marked_as_a_draft_everywhere(self):
        """확정 정책으로 읽히면 검수자가 틀린 기준으로 판정하고, 그 판정은 측정값이 되어
        되돌릴 수도 없다. 봉투만 표시하면 항목만 뽑아 쓰는 화면에서 표시가 떨어져 나간다.

        **유일한 눈이다. 지우지 말 것**(항목 쪽 draft 를 떼도 이 단언 하나만 깨진다)."""
        r = self.call(kind="intent", values=["속보·단신"])
        self.assertTrue(r["draft"])
        self.assertIn("초안", r["draft_note"])
        self.assertTrue(r["items"])
        for it in r["items"]:
            self.assertTrue(it["draft"], "항목에 초안 표시가 없다")

    def test_a_value_without_an_example_says_so(self):
        """지어내지 않는다. 없다고 말하는 것이 이 도구의 정확성이다.

        **유일한 눈이다. 지우지 말 것.** 빈 예시를 정의문으로 채우는 수정은 '더 친절한 응답'
        처럼 보이지만, 검수자에게는 팀이 합의한 예시로 읽힌다."""
        r = self.call(kind="intent", values=["심층 분석"])
        it = r["items"][0]
        self.assertFalse(it["has_example"])
        self.assertEqual(it["example"], "")
        self.assertEqual(it["note"], PT.NO_EXAMPLE)
        self.assertTrue(it["desc"], "예시가 없어도 확정 정의는 준다")

    def test_an_unknown_value_is_returned_as_unknown_not_guessed(self):
        """비슷한 값으로 갈음하면 없는 정책이 생긴다. **카테고리 쪽이 유일한 눈이다.**
        영문 경로·구표기·한글 표시명을 차례로 시도하는 해석기라 마지막에 아무거나 돌려주면
        조용히 엉뚱한 정책을 답한다. 두 kind 를 모두 물어야 잡힌다. 지우지 말 것."""
        for kind, junk in (("intent", "있지도 않은 분류값"), ("category", "있지도 않은 카테고리")):
            r = self.call(kind=kind, values=[junk])
            self.assertEqual(r["items"], [], kind)
            self.assertEqual(r["unknown"], [junk], kind)

    def test_examples_come_from_the_dictionary_and_nowhere_else(self):
        """⚠️ 골든셋 실물 콘텐츠를 예시로 끌어오면 골드의 답을 미리 보여 주는 것이 된다
        (골드 문항은 골든셋에서만 만들어진다). 원천이 사전 하나임을 값으로 못 박는다."""
        for k, ex in list(D.INTENT_EXAMPLES.items())[:8]:
            got = self.call(kind="intent", values=[k])["items"]
            self.assertEqual(got[0]["example"], ex, k)
        for k, (_desc, ex) in list(D.TIER2_DEFS.items())[:8]:
            got = self.call(kind="category", values=[k])["items"]
            self.assertEqual(got[0]["example"], ex, k)

    def test_the_tool_never_reads_content(self):
        """해시를 받지 않으므로 어떤 콘텐츠를 보는 중인지 서버에 알리지 않는다
        (골드 문항을 보는 중에 불려도 그 사실이 새지 않는다 · scope=both 의 근거)."""
        props = PT.TOOLS["get_examples"]["inputSchema"]["properties"]
        self.assertNotIn("hash", props)
        self.assertNotIn("team", props)
        self.assertEqual(PT.TOOLS["get_examples"]["scope"], "both")

    def test_definitions_match_the_single_source(self):
        for it in self.call(kind="intent", service="뉴스", limit=60)["items"]:
            self.assertEqual(it["desc"], D.INTENT_VALUE_DEFS.get(it["key"], ""), it["key"])

    def test_category_values_are_found_however_they_are_written(self):
        """저장은 영문 경로, 화면은 한글이다. 무엇이 들어와도 같은 항목을 찾아야 한다."""
        for v in ("Politics", "News and Politics / Politics", "정치"):
            r = self.call(kind="category", values=[v])
            self.assertEqual([it["key"] for it in r["items"]], ["Politics"], v)
            self.assertEqual(r["unknown"], [], v)

    def test_a_bad_kind_is_an_error_not_a_guess(self):
        self.assertIn("error", self.call(kind="없는종류"))
        self.assertIn("error", self.call(kind=""))

    def test_limit_is_clamped_and_truncation_is_reported(self):
        for bad in ("abc", -5, 9999, None, [1]):
            r = self.call(kind="category", limit=bad)
            self.assertNotIn("error", r, f"limit={bad!r}")
            self.assertLessEqual(len(r["items"]), PT.EXAMPLE_LIMIT_MAX)
        r = self.call(kind="category", limit=3)
        self.assertEqual(len(r["items"]), 3)
        self.assertTrue(r["truncated"])
        self.assertGreater(r["total"], 3)

    def test_service_narrows_the_intent_candidates(self):
        news = {it["key"] for it in self.call(kind="intent", service="뉴스", limit=60)["items"]}
        tstory = {it["key"] for it in self.call(kind="intent", service="티스토리", limit=60)["items"]}
        self.assertNotEqual(news, tstory)
        self.assertIn("속보·단신", news)
        self.assertNotIn("속보·단신", tstory)

    def test_no_team_no_examples(self):
        for t in (None, "", "   "):
            self.assertIn("error", PT.call("get_examples", {"kind": "intent"}, team=t))


# ── 10. 자유질문이 예시의 초안 표시를 그대로 나른다 ─────────────────────────
class TestDraftMarkSurvivesTheAskPath(Base):
    def test_the_draft_note_travels_with_the_example_into_the_prompt(self):
        """모델이 초안 예시를 확정 정책처럼 옮겨 적지 못하게 자료 문장에 표시를 함께 싣는다.

        **유일한 눈이다. 지우지 말 것.** 도구 응답의 draft 표시(위 클래스)는 그대로 둔 채
        프롬프트로 넘길 때만 떼는 수정이 있고, 그러면 모델이 초안인 줄 모르고 단언조로 옮긴다."""
        fake = self.llm()
        r = self.ask(hash=H1, question="속보·단신 예시가 뭐야?")
        ex = [s for s in r["sources"] if s["tool"] == "get_examples" and ".example" in s["field"]]
        self.assertTrue(ex, "예시 자료가 안 잡혔다(이 단언이 공회전한다)")
        for s in ex:
            self.assertIn("초안", s["text"])
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("초안", fake.calls[0]["user"])


# ── 10b. 자유질문 자료에서도 내 판정은 빠진다 ────────────────────────────────
class TestTheAskerIsExcludedFromTheMaterials(Base):
    """칩('다른 검수자 의견')과 자유질문이 서로 다른 집계를 쓰면 그 차이가 또 하나의 신호다.

    골드에서 이 도구는 빈 결과를 준다. 내 표를 세면 **판정 뒤 평범한 콘텐츠는 절대 비지
    않으므로**(stage=after 가 곧 '내 표가 있다') 자료 목록에 의견 줄이 있나 없나로 골드를
    가려낼 수 있다. 자료를 모으는 경로(`_ask_sources`)에서도 나를 빼야 그 구분이 사라진다."""

    def test_my_own_verdict_is_not_material_for_my_own_question(self):
        """나만 판정한 콘텐츠 = 의견 자료 없음(골드와 같은 모양).

        **유일한 눈이다. 지우지 말 것.** `_ask_sources` 가 `me` 를 도구에 안 넘겨도 도구
        자체를 재는 테스트는 전부 통과한다(무력화 실측 39번에서 실제로 탈출했다)."""
        self.store._fb = {H1: _fb("나")}
        self.llm()
        r = self.ask(hash=H1, me="나")
        self.assertFalse([s for s in r["sources"] if s["tool"] == "reviewer_dissent"],
                         "내 판정이 내 질문의 자료로 실렸다")

    def test_other_peoples_verdicts_are_still_material(self):
        """빼는 건 나뿐이다 — 남들 의견까지 사라지면 기능이 없는 것과 같다."""
        self.store._fb = {H1: _fb("복실", "딱지")}
        self.llm()
        r = self.ask(hash=H1, me="나")
        self.assertTrue([s for s in r["sources"] if s["tool"] == "reviewer_dissent"])


# ── 11. 라우트 ───────────────────────────────────────────────────────────────
class _FakeHandler:
    client_address = ("10.0.0.1", 0)
    headers = {}

    def __init__(self, team, uid=None):
        self._team, self._uid = team, uid
        self.sent = []

    def _req_team(self):
        return self._team

    def _bearer_uid(self):
        return self._uid

    def _send(self, code, body, ctype):
        self.sent.append((code, body))


class TestRoute(unittest.TestCase):
    def setUp(self):
        self.seen = []
        o_supa, o_ask, o_rl = SV._supa, RA.ask, SV.rate_limited
        SV.rate_limited = lambda *a, **k: False
        RA.ask = lambda **kw: (self.seen.append(kw), {"answer": []})[1]
        self.addCleanup(lambda: setattr(SV, "_supa", o_supa))
        self.addCleanup(lambda: setattr(RA, "ask", o_ask))
        self.addCleanup(lambda: setattr(SV, "rate_limited", o_rl))

    def _post(self, supa, req_team, body, uid=None):
        SV._supa = lambda: supa
        fn, gate = SV._POST_ROUTES["/assist-ask"]
        h = _FakeHandler(req_team, uid)
        out = fn(h, body)
        return out, h, gate

    def test_the_route_is_gated_on_login_and_team(self):
        _out, _h, gate = self._post(True, "t7", b'{"hash":"x","stage":"after","question":"q"}')
        self.assertEqual(gate, "team")          # gate=team 이 로그인+팀 소속을 함께 건다

    def test_the_body_cannot_inject_the_team(self):
        """**유일한 눈이다. 지우지 말 것.** 라우트가 본문 team 을 한 번만 믿어도 교차 팀
        조회가 열린다(무력화 실측에서 이 단언 1건만 잡았다)."""
        body = ('{"hash":"x","stage":"after","question":"q","team":"침입팀",'
                '"args":{"team":"침입팀"}}').encode()
        self._post(True, None, body)
        self.assertIsNone(self.seen[0]["team"])   # 도구 계층이 need_team 으로 거절한다

    def test_the_local_scope_never_appears_in_supabase_mode(self):
        for req_team in (None, "", "t7"):
            self.seen.clear()
            self._post(True, req_team, b'{"hash":"x","stage":"after","question":"q"}')
            self.assertNotEqual(self.seen[0]["team"], "local", repr(req_team))

    def test_local_sqlite_gets_a_fixed_scope(self):
        self._post(False, None, b'{"hash":"x","stage":"after","question":"q"}')
        self.assertEqual(self.seen[0]["team"], "local")

    def test_the_hash_and_stage_come_from_the_body_verbatim(self):
        self._post(True, "t7",
                   '{"hash":"abc","stage":"after","question":"  왜?  "}'.encode("utf-8"))
        self.assertEqual(self.seen[0]["hash"], "abc")
        self.assertEqual(self.seen[0]["stage"], "after")
        self.assertEqual(self.seen[0]["question"], "  왜?  ")   # 자르기는 도구 계층의 몫

    def test_a_malformed_body_does_not_crash_the_route(self):
        for body in (b"", b"[]", b'"scalar"', b"not json", b"null", b"{}"):
            self.seen.clear()
            out, _h, _g = self._post(True, "t7", body)
            self.assertIsInstance(out, dict, body)

    def test_too_many_questions_are_refused_before_the_tool_layer(self):
        SV.rate_limited = lambda *a, **k: True
        _out, h, _g = self._post(True, "t7", b'{"hash":"x","stage":"after","question":"q"}')
        self.assertEqual(h.sent[0][0], 429)
        self.assertEqual(self.seen, [], "속도 제한에 걸렸는데 도구 계층까지 갔다")

    def test_the_rate_limit_key_is_per_user(self):
        keys = []
        SV.rate_limited = lambda key, **k: (keys.append(key), False)[1]
        self._post(True, "t7", b'{"hash":"x","stage":"after","question":"q"}', uid="u-1")
        self._post(True, "t7", b'{"hash":"x","stage":"after","question":"q"}', uid="u-2")
        self.assertEqual(len(set(keys)), 2, "사용자가 달라도 같은 버킷을 쓴다")


if __name__ == "__main__":
    unittest.main()
