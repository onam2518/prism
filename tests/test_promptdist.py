"""프롬프트 배포·결과 검증 도구(promptdist · 트랙 B) 회귀 테스트 (2026-08-13).

이 두 도구는 **밖으로 나가는 계약**이다. 파트너는 여기서 받은 프롬프트로 자기 모델을 돌리고,
여기서 받은 검증 결과를 근거로 자기 파이프라인을 고친다. 그래서 규칙 자체를 단언한다.

지키는 것(깨지면 각각 어떤 일이 벌어지는지):
  · 학습 보정이 응답에 절대 안 실린다 · 실리면 팀 검수 이력이 파트너에게 나가고, 같은 버전이
    같은 프롬프트를 뜻하지 않게 되어 이 도구의 존재 이유(재현성)가 사라진다
  · 버전·지문이 항상 실린다 · 없으면 '파트너가 알아서 만든 메타' 와 구분이 안 된다
  · 모르는 모델명이 default 로 수렴한다(예외가 아니다) · 터지면 파트너 파이프라인이 멈춘다
  · 서비스별 인텐트 후보가 섞이지 않는다 · 섞이면 그 서비스에 없는 값이 정답처럼 보인다
  · 검증이 판정하지 않는다(등급·점수 키 부재) · 붙이는 순간 근거 없는 판정이 계약이 된다
  · 팀 없이 못 돈다 · args 의 team 은 무시된다(감사 H1)
  · 규칙 문구가 계약 원문에 실재한다 · 안 그러면 없는 규칙을 규칙이라고 말하게 된다

## 무력화 실험(2026-08-13 · 스위트 전체 1,984건 기준 · 실측)

계약을 어기는 변경을 하나씩 심고 몇 건이 잡는지 셌다. 괄호 안은 **이 파일 밖**에서 잡힌 수다.

  M1  학습 보정 병기(팀 데이터 유출 + 재현성 파괴)      4건 (밖 0)  TestLearnedNeverShips
  M2  version 키 제거                          1건 (밖 0)  TestVersionAlwaysShips
  M3  fingerprint 를 본문과 무관한 상수로           1건 (밖 0)  TestVersionAlwaysShips
  M4  모르는 모델명에 예외                         2건 (밖 0)  TestModelFamilyFallback
  M5  계열 래퍼 무시(client_model 이 장식)          2건 (밖 0)  FamilyFallback·Honesty
  M6  service 무시(서비스 후보 섞임)                4건 (밖 0)  ServiceScoping 외
  M7  상한 넘어도 자른 프롬프트를 준다                 1건 (밖 0)  TestResponseCap
  M8  검증이 등급을 낸다                          1건 (밖 0)  TestValidationDoesNotJudge
  M9  조건부 쌍을 위반으로 확정                      1건 (밖 0)  TestValidationDoesNotJudge
  M10 규칙 문구를 지어냄                          1건 (밖 0)  TestRuleTextsAreRealContractText
  M11 사전이 바뀌어 인용 문구가 유령이 됨               1건 (밖 1)  같은 클래스
  M12 user_template 파생값 치환 제거              2건 (밖 0)  TestUserTemplate
  M13 requires 를 손으로 적음                    2건 (밖 0)  TestUserTemplate
  M14 잘림 보고 제거                             1건 (밖 3)  TestResponseCap
  M15 팀 강제 제거                              1건 (밖 3)  TestTeamScope
  M16 args 의 team 을 그대로 넘김                  1건 (밖 1)  TestTeamScope
  M18 도구를 internal 로도 엶                    1건 (밖 0)  TestRegistry
  (M17 = 전송 배선. tests/test_mcpserver.py 2건 · 그 파일에서만 잡힌다)

**'밖 0' 인 항목은 이 파일이 유일한 눈이다. 지우지 말 것.** 지우면 그 계약은 아무도 보지 않는다.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import dictionaries as D
from prism import meta_prompts as MP
from prism import prismtools as PT
from prism import promptdist as PD
from prism import prompts as PR

TEAM = "team-1"


def prompt(**kw):
    kw.setdefault("call", "intent")
    kw.setdefault("service", "뉴스")
    return PT.call("get_extraction_prompt", kw, team=TEAM)


def validate(result, service="뉴스"):
    return PT.call("validate_result", {"result": result, "service": service}, team=TEAM)


# ══════════════════════════════════════════════════════════════════════════════
class TestLearnedNeverShips(unittest.TestCase):
    """학습 보정은 어떤 경로로도 응답에 실리지 않는다.

    무력화 실험 M1(learned 를 병기하게 되돌림): 이 클래스가 **4건**으로 잡고, 스위트의 나머지
    1,980건에서는 **0건**이다. 여기가 유일한 눈이다 · 지우지 말 것."""

    def setUp(self):
        """실제 운영에서 채워지는 자리에 표식을 심는다. **가짜가 계약을 어겨도 통과하는 상태를
        만들지 않기 위해** 여기 심는 값은 진짜 경로(prompts.LEARNED · LEARNED_BY_MODEL)를
        그대로 쓴다. 딴 데 심으면 도구가 진짜 경로를 열어 놔도 테스트가 초록으로 남는다."""
        self.mark = "학습보정표식-DO-NOT-SHIP"
        self.orig = dict(PR.LEARNED)
        self.orig_bm = dict(PR.LEARNED_BY_MODEL)
        PR.LEARNED = {"extract": self.mark + "-extract", "analyze": self.mark + "-analyze",
                      "review": "", "judge": ""}
        PR.LEARNED_BY_MODEL = {"gpt-5": {"extract": self.mark + "-gpt", "analyze": ""}}
        self.addCleanup(lambda: setattr(PR, "LEARNED", self.orig))
        self.addCleanup(lambda: setattr(PR, "LEARNED_BY_MODEL", self.orig_bm))

    def test_the_marker_really_would_have_shipped_through_the_other_builder(self):
        """가짜 표식이 진짜 경로에 심겼는지 먼저 확인한다.

        이 단언이 없으면 '표식이 애초에 어디에도 안 들어가는' 상태에서도 아래 테스트가 전부
        통과한다(= 아무것도 안 재는 초록). 최근 이 저장소가 세 번 밟은 함정이라 앞에 둔다."""
        class C:
            displayServiceName, title, body = "뉴스", "t", "b"
        shipped = PR.call_system(C(), "intent", "gpt-5")
        self.assertIn(self.mark, shipped, "표식이 운영 프롬프트 경로에 심기지 않았다(테스트가 헛돈다)")

    def test_no_call_leaks_the_learned_block(self):
        for call in PD.CALLS:
            for model in ("gpt-5", "", "claude-opus-5"):
                r = prompt(call=call, client_model=model)
                self.assertNotIn(self.mark, json.dumps(r, ensure_ascii=False),
                                 "%s/%s 응답에 학습 보정이 실렸다" % (call, model))

    def test_the_model_scoped_learned_block_does_not_leak_either(self):
        """모델 귀속 보정(LEARNED_BY_MODEL)은 client_model 을 그대로 받는 자리라 더 위험하다."""
        r = prompt(client_model="gpt-5")
        self.assertNotIn(self.mark, json.dumps(r, ensure_ascii=False))

    def test_the_prompt_is_identical_with_and_without_learned_data(self):
        """보정이 있든 없든 같은 프롬프트가 나가야 한다(지문까지 같아야 재현이 성립한다)."""
        with_data = prompt()
        PR.LEARNED = {"extract": "", "analyze": "", "review": "", "judge": ""}
        PR.LEARNED_BY_MODEL = {}
        without = prompt()
        self.assertEqual(with_data["fingerprint"], without["fingerprint"])
        self.assertEqual(with_data["system"], without["system"])

    def test_the_size_of_the_learned_block_is_not_reported_either(self):
        """분량도 팀 데이터의 신호다. 응답의 바이트 수가 보정에 따라 흔들리면 안 된다."""
        a = prompt()["bytes"]
        PR.LEARNED = {"extract": "x" * 5000, "analyze": "", "review": "", "judge": ""}
        self.assertEqual(prompt()["bytes"], a)


# ══════════════════════════════════════════════════════════════════════════════
class TestVersionAlwaysShips(unittest.TestCase):
    """버전·지문이 빠지면 이 도구는 A안(파트너가 알아서)과 구분되지 않는다.

    무력화 실험 M2(version 키 제거) **1건** · M3(지문을 본문과 무관한 상수로) **1건**.
    둘 다 이 클래스가 유일하게 잡는다(스위트의 나머지에서 0건)."""

    def test_every_call_carries_the_version_and_a_fingerprint(self):
        for call in PD.CALLS:
            r = prompt(call=call)
            self.assertEqual(r["version"], PR.IMETA_VERSION, call)
            self.assertTrue(r["fingerprint"].startswith("sha256:"), call)

    def test_the_fingerprint_actually_tracks_the_prompt_body(self):
        """지문이 본문과 무관하면 '같은 버전 = 같은 프롬프트' 대조가 거짓말이 된다."""
        a, b = prompt(service="뉴스"), prompt(service="티스토리")
        self.assertNotEqual(a["fingerprint"], b["fingerprint"])
        self.assertEqual(a["fingerprint"], prompt(service="뉴스")["fingerprint"])

    def test_the_validator_reports_the_rule_version_too(self):
        self.assertEqual(validate({"intent": []})["version"], PR.IMETA_VERSION)


# ══════════════════════════════════════════════════════════════════════════════
class TestHonestyAboutDifferences(unittest.TestCase):
    """무엇을 뺐는지 응답이 말한다. 안 말하면 파트너는 결과 차이를 우리 탓으로 읽는다."""

    def test_the_learned_exclusion_is_stated_in_the_response(self):
        txt = " ".join(prompt()["differs_from_prism_run"])
        self.assertIn("학습 보정", txt)
        self.assertIn("다를 수 있습니다", txt)

    def test_an_edited_family_wrapper_is_disclosed(self):
        """운영자가 래퍼를 편집하면 version 은 그대로인데 프롬프트가 달라진다 · 그 사실을 알린다."""
        self.assertFalse(prompt(client_model="gpt-5")["wrapper_customized"])
        orig = dict(MP.WRAPPER_OVERRIDES)
        MP.WRAPPER_OVERRIDES = dict(orig, gpt="편집됨 {ROLE}{SCHEMA}{RULES}{EXAMPLES}{SELF_CHECK}{LEARNED}")
        self.addCleanup(lambda: setattr(MP, "WRAPPER_OVERRIDES", orig))
        r = prompt(client_model="gpt-5")
        self.assertTrue(r["wrapper_customized"])
        self.assertTrue(any("fingerprint" in d for d in r["differs_from_prism_run"]))
        self.assertIn("편집됨", r["system"])           # 현행 프롬프트를 준다(숨기지 않는다)


# ══════════════════════════════════════════════════════════════════════════════
class TestModelFamilyFallback(unittest.TestCase):
    """모르는 모델명은 조용히 default 로 수렴한다(실패하지 않는다).

    무력화 실험 M4(모르는 모델명에 예외를 냄): 이 클래스가 **2건**으로 잡는다(수렴·쓰레기값).
    M5(계열을 무시하고 늘 범용 래퍼로 조립): **2건**(이 클래스 1 + 공개 클래스 1).
    둘 다 다른 파일에서는 0건."""

    def test_an_unknown_model_falls_back_without_failing(self):
        for model in ("듣도보도못한모델", "llama-4", "", "  ", "mistral-large"):
            r = prompt(client_model=model)
            self.assertNotIn("error", r, repr(model))
            self.assertEqual(r["family"], "default", repr(model))
            self.assertIn("system", r)

    def test_known_families_are_matched(self):
        for model, fam in (("gpt-5", "gpt"), ("google/gemini-3-pro", "gemini"),
                           ("claude-opus-5", "claude"), ("solar-pro2", "solar")):
            self.assertEqual(prompt(client_model=model)["family"], fam, model)

    def test_the_family_actually_changes_the_wrapper(self):
        """계열이 응답에만 적히고 프롬프트가 안 바뀌면 client_model 은 장식이다."""
        bodies = {prompt(client_model=m)["system"] for m in ("gpt-5", "gemini-3-pro", "claude-opus-5", "solar-pro2")}
        self.assertEqual(len(bodies), 4)

    def test_a_garbage_model_value_does_not_blow_up(self):
        """도구 인자는 모델이 채우므로 형식 오류가 일상이다(감사 H3 · 예외 원문 미노출)."""
        for junk in ({"nope": 1}, 42, ["x"], None):
            r = PT.call("get_extraction_prompt", {"call": "intent", "service": "뉴스",
                                                  "client_model": junk}, team=TEAM)
            self.assertNotIn("error", r, repr(junk))


# ══════════════════════════════════════════════════════════════════════════════
class TestServiceScoping(unittest.TestCase):
    """서비스별 후보가 섞이면 그 서비스에 없는 값이 정답처럼 보인다.

    무력화 실험 M6(call_system 에 넘기는 service 를 빈 문자열로 고정): 이 클래스가 **2건**,
    조립 동일성·지문 테스트가 **2건**을 더 잡는다(합 4건 · 다른 파일에서는 0건).
    후보 목록은 '- 값:' 정의문 줄로 본다 · 계약 원문과 골드 예시에는 다른 서비스 값이 문장으로
    등장하므로(예: 티스토리 프롬프트의 골드 예시가 뉴스 값을 쓴다) 단순 substring 으로는
    아무것도 잡지 못한다. 이 구분을 지운 채로 substring 검사만 남기지 말 것."""

    def test_service_specific_candidates_do_not_bleed_across_services(self):
        news = prompt(service="뉴스")["system"]
        tstory = prompt(service="티스토리")["system"]
        self.assertIn("- 정책·행정:", news)
        self.assertNotIn("- 정책·행정:", tstory)
        self.assertIn("- 리뷰·분석:", tstory)
        self.assertNotIn("- 리뷰·분석:", news)

    def test_every_service_gets_exactly_its_own_candidate_block(self):
        for svc, vals in D.INTENT_CATEGORIES_BY_SERVICE.items():
            sysmsg = prompt(service=svc)["system"]
            self.assertIn("[서비스 카테고리 분류값 · %s]" % svc, sysmsg, svc)
            for v in vals:
                self.assertIn("- %s:" % v, sysmsg, "%s 후보 %s 누락" % (svc, v))

    def test_an_unknown_service_converges_to_the_pgc_fallback(self):
        """프리즘 운영과 같은 처리다(미정의 dsn = 범용 분류값만)."""
        r = prompt(service="파트너서비스X")
        self.assertEqual(r["service_resolved"], "")
        self.assertIn("displayServiceName 미정의", r["system"])


# ══════════════════════════════════════════════════════════════════════════════
class TestPromptIsAssembledNotRewritten(unittest.TestCase):
    """프롬프트를 여기서 새로 쓰지 않는다(기준이 두 벌이 되는 것을 막는 눈)."""

    def test_the_body_is_exactly_what_meta_prompts_assembles(self):
        for call in PD.CALLS:
            self.assertEqual(prompt(call=call, client_model="gpt-5")["system"],
                             MP.call_system("gpt-5", call, "뉴스", learned=""))

    def test_the_output_schema_comes_from_the_contract(self):
        for call in PD.CALLS:
            self.assertEqual(prompt(call=call)["output_schema"], MP.CALL_SCHEMAS[call])

    def test_a_bad_call_name_is_refused_with_the_choices(self):
        r = prompt(call="없는콜")
        self.assertIn("error", r)
        for c in PD.CALLS:
            self.assertIn(c, r["error"])


# ══════════════════════════════════════════════════════════════════════════════
class TestUserTemplate(unittest.TestCase):
    """입력 계약(user 메시지)은 손으로 적지 않고 실제 조립 함수에서 뽑는다.

    무력화 실험 M12(치환 두 줄 삭제 = 라벨이 바뀐 것과 같은 효과) **2건** ·
    M13(requires 를 손으로 적음) **2건**. 전부 이 클래스이고 다른 파일에서는 0건.
    `test_the_derived_length_is_a_placeholder_not_a_constant` 가 없으면 파트너에게
    '본문 글자수: 6' 이라는 **엉뚱한 상수**가 계약처럼 나간다 · 이 단언이 유일한 눈이다."""

    def test_the_derived_length_is_a_placeholder_not_a_constant(self):
        t = prompt(call="intent")["user_template"]
        self.assertIn("{본문 글자수}", t)
        self.assertNotIn("본문 글자수: %d" % len(PD._PH_BODY), t)

    def test_the_image_count_line_keeps_the_information_none_distinction(self):
        t = prompt(call="intent")["user_template"]
        self.assertIn("정보 없음", t)
        self.assertIn("0장", t)                     # '정보 없음 != 0장' 이 계약에 남아 있어야 한다
        self.assertNotIn(MP._IMG_UNKNOWN, t)        # 자리표로 바뀌었다

    def test_requires_is_derived_from_the_template_not_hand_written(self):
        self.assertEqual(prompt(call="summary")["requires"], [])
        self.assertEqual(prompt(call="entities")["requires"], [])
        self.assertEqual(prompt(call="intent")["requires"], ["summary"])
        self.assertEqual(sorted(prompt(call="category")["requires"]),
                         ["entities", "intent", "summary"])

    def test_every_required_prior_output_appears_in_the_template(self):
        for call in PD.CALLS:
            r = prompt(call=call)
            for k in r["requires"]:
                self.assertIn(k + ":", r["user_template"], "%s/%s" % (call, k))


# ══════════════════════════════════════════════════════════════════════════════
class TestResponseCap(unittest.TestCase):
    """상한을 넘으면 **자른 프롬프트를 주지 않는다.** 반쪽 기준은 조용히 다른 기준이다.

    무력화 실험 M7(상한을 넘어도 자른 프롬프트를 실어 보냄): 이 클래스가 **1건**으로 잡고
    다른 어떤 테스트도 못 잡는다. M14(잘림 보고 제거)는 여기 1건 + 형제 도구 테스트 3건."""

    def test_normal_prompts_are_under_the_cap_and_say_so(self):
        for call in PD.CALLS:
            r = prompt(call=call)
            self.assertFalse(r["truncated"], call)
            self.assertLessEqual(r["bytes"], PD.PROMPT_MAX_BYTES, call)
            self.assertEqual(len(r["system"].encode("utf-8")), r["bytes"], call)

    def test_an_oversized_prompt_is_refused_not_silently_cut(self):
        orig = PD.PROMPT_MAX_BYTES
        PD.PROMPT_MAX_BYTES = 100
        self.addCleanup(lambda: setattr(PD, "PROMPT_MAX_BYTES", orig))
        r = prompt()
        self.assertTrue(r["truncated"])
        self.assertIn("error", r)
        self.assertNotIn("system", r, "잘린 프롬프트가 나갔다(반쪽 기준으로 돌게 된다)")

    def test_validation_findings_report_truncation(self):
        many = {"intent": ["없는값%d" % i for i in range(PD.ISSUE_LIMIT + 5)]}
        r = validate(many)
        self.assertTrue(r["truncated"])
        self.assertEqual(r["total"], PD.ISSUE_LIMIT + 5)
        self.assertEqual(len(r["items"]), PD.ISSUE_LIMIT)


# ══════════════════════════════════════════════════════════════════════════════
class TestValidationDoesNotJudge(unittest.TestCase):
    """등급·점수·합불을 내지 않는다.

    무력화 실험 M8(응답에 `"grade"` 한 줄 추가) **1건** · M9(조건부 쌍을 위반으로 확정) **1건**.
    둘 다 이 클래스가 유일하게 잡는다(다른 파일 전체 0건)."""

    BANNED = ("grade", "score", "verdict", "quality", "rating", "pass", "passed",
              "valid", "is_valid", "ok", "confidence", "accuracy")

    def test_no_verdict_shaped_key_exists_anywhere_in_the_response(self):
        for res in ({"intent": ["속보·단신"]}, {"intent": ["없는값"]},
                    {"content_category": ["Sports / Politics"]}):
            r = validate(res)
            self.assertNotIn("error", r)
            for k in self.BANNED:
                self.assertNotIn(k, r, "판정성 키 %s 가 생겼다" % k)
            for it in r["items"]:
                for k in self.BANNED:
                    self.assertNotIn(k, it, "항목에 판정성 키 %s 가 생겼다" % k)

    def test_zero_findings_is_worded_as_no_rule_violation_not_as_correct(self):
        r = validate({"summary": "삼성전자 노사 협상이 결렬됐다.",
                      "intent": ["속보·단신"],
                      "content_category": ["Business and Finance / Industries"]})
        self.assertEqual(r["total"], 0)
        self.assertIn("규칙 위반 없음", r["note"])
        self.assertIn("정확하다", r["note"])          # '정확하다는 뜻이 아니다' 를 명시한다

    def test_conditional_pairs_are_flagged_as_check_not_violation(self):
        """근거를 봐야 갈리는 쌍에 위반 딱지를 붙이면 그것도 근거 없는 판정이다."""
        r = validate({"intent": ["후기·리뷰·비평", "리뷰·분석"]}, service="티스토리")
        self.assertEqual([it["kind"] for it in r["items"]], ["check"])
        self.assertIn("판단하지 않고", r["items"][0]["message"])


# ══════════════════════════════════════════════════════════════════════════════
class TestValidationCatchesTheRules(unittest.TestCase):
    """무엇을 잡는지 규칙별로 단언한다(잡는다고 적어 두고 안 잡는 일이 없게)."""

    def one(self, res, service="뉴스"):
        r = validate(res, service)
        self.assertEqual(len(r["items"]), 1, r["items"])
        return r["items"][0]

    def test_a_value_outside_the_dictionary(self):
        self.assertEqual(self.one({"intent": ["초대박이슈"]})["code"], "intent_unknown")

    def test_a_value_that_belongs_to_another_service(self):
        it = self.one({"intent": ["리뷰·분석"]})
        self.assertEqual(it["code"], "intent_not_in_service")
        self.assertIn("티스토리", it["message"])       # 어느 서비스 것인지 알려준다

    def test_the_same_value_is_fine_in_its_own_service(self):
        self.assertEqual(validate({"intent": ["리뷰·분석"]}, "티스토리")["total"], 0)

    def test_a_type_error(self):
        it = self.one({"intent": "속보·단신"})
        self.assertEqual(it["code"], "wrong_type")
        self.assertIn("배열", it["message"])

    def test_a_pair_that_cannot_be_used_together_by_definition(self):
        it = self.one({"intent": ["포토·영상 중심", "그래픽·인포그래픽"]})
        self.assertEqual((it["code"], it["kind"]), ("pair_exclusive", "violation"))

    def test_category_paths(self):
        cases = {
            "Sports / Politics": ("category_wrong_tier1", "News and Politics / Politics"),
            "News and Politics / Politics / Local": ("category_tier3", ""),
            "Style and Fashion / Personal Care": ("category_outdated", "Style and Fashion / Beauty"),
            "Politics": ("category_missing_tier1", "News and Politics / Politics"),
            "없는카테고리": ("category_unknown", ""),
        }
        for value, (code, fix) in cases.items():
            it = self.one({"content_category": [value]})
            self.assertEqual(it["code"], code, value)
            if fix:
                self.assertEqual(it["fix"], fix, value)

    def test_a_wrong_parent_fix_keeps_the_tier2_instead_of_dropping_it(self):
        """정규화는 소속이 어긋나면 Tier 2 를 버린다. 그걸 고침안으로 주면 판정이 조용히 사라진다."""
        self.assertEqual(D.normalize_content_category("Sports / Politics"), "Sports")
        self.assertEqual(self.one({"content_category": ["Sports / Politics"]})["fix"],
                         "News and Politics / Politics")

    def test_quantity_and_order_contract_rules(self):
        self.assertEqual(self.one({"content_category": []})["code"], "category_empty")
        self.assertEqual(self.one({"intent": ["속보·단신", "속보·단신"]})["code"], "duplicate")
        self.assertEqual(self.one({"summary": "", "intent": ["속보·단신"]})["code"],
                         "summary_empty_but_others_filled")

    def test_fields_outside_the_four_field_contract(self):
        it = self.one({"grade": "G"})
        self.assertEqual(it["code"], "unknown_field")

    def test_only_the_fields_present_are_checked(self):
        r = validate({"entities": ["삼성전자"]})
        self.assertEqual(r["checked_fields"], ["entities"])
        self.assertEqual(r["total"], 0)

    def test_a_stringified_json_body_is_accepted(self):
        self.assertEqual(validate(json.dumps({"intent": ["없는값"]}))["items"][0]["code"],
                         "intent_unknown")

    def test_a_non_object_body_is_refused_with_guidance(self):
        for bad in ([1, 2], 7, "not json"):
            r = validate(bad)
            self.assertIn("error", r)
            self.assertNotIn("Traceback", r["error"])


# ══════════════════════════════════════════════════════════════════════════════
class TestRuleTextsAreRealContractText(unittest.TestCase):
    """응답에 싣는 규칙 문구가 계약 원문에 실재하는지 대조한다.

    무력화 실험 M10(규칙 문구를 지어냄): 이 클래스 **1건** · 스위트의 나머지에서 0건.
    M11(사전에서 '배타로 본다' 문장을 지움): 여기 1건 + tests/test_prompt_policy.py 1건.
    이 눈이 없으면 파트너에게 **없는 규칙**을 규칙이라고 말하게 된다."""

    def test_every_quoted_rule_exists_in_its_source(self):
        for text, src in PD.RULE_TEXTS:
            source = PD.rule_source(src)
            self.assertTrue(source, "원문 자리를 못 찾았다: %r" % (src,))
            self.assertIn(text, source, "계약 원문에 없는 문구다: %r (자리 %r)" % (text, src))

    def test_every_pair_rule_names_values_that_exist_in_the_dictionary(self):
        known, _ = PD._known_intents()
        for a, b, _kind, _src, _rule in PD.PAIR_RULES:
            self.assertIn(a, known, a)
            self.assertIn(b, known, b)


# ══════════════════════════════════════════════════════════════════════════════
class TestTeamScope(unittest.TestCase):
    """팀은 키에서만 온다(감사 H1). 두 도구 모두 팀 데이터를 안 읽지만 강제는 그대로 지난다."""

    def test_neither_tool_runs_without_a_team(self):
        for name, args in (("get_extraction_prompt", {"call": "intent"}),
                           ("validate_result", {"result": {}, "service": "뉴스"})):
            for t in (None, "", "   "):
                self.assertIn("error", PT.call(name, args, team=t), "%s team=%r" % (name, t))

    def test_a_caller_supplied_team_never_reaches_the_tool(self):
        """도구가 받는 team 을 **직접 본다.**

        응답 본문만 보는 단언으로는 이걸 못 잡는다(실측: `call` 이 args 의 team 을 그대로
        쓰게 바꿔도 이 두 도구는 팀 데이터를 안 읽어 응답이 한 글자도 안 변한다 · 무력화
        실험 M16 에서 이 파일 0건). 지금 안 새는 것과 통로가 없는 것은 다른 말이라 통로를 본다."""
        seen = {}
        spec = PT.TOOLS["get_extraction_prompt"]
        orig = spec["fn"]
        spec["fn"] = lambda team=None, **kw: seen.update(team=team, kw=kw) or {"ok": True}
        self.addCleanup(lambda: spec.__setitem__("fn", orig))
        PT.call("get_extraction_prompt",
                {"call": "intent", "service": "뉴스", "team": "남의팀"}, team=TEAM)
        self.assertEqual(seen["team"], TEAM, "args 의 team 이 키에서 온 팀을 밀어냈다")
        self.assertNotIn("team", seen["kw"])

    def test_a_team_in_the_arguments_does_not_change_the_response(self):
        r = PT.call("get_extraction_prompt",
                    {"call": "intent", "service": "뉴스", "team": "남의팀"}, team=TEAM)
        self.assertNotIn("error", r)
        self.assertNotIn("남의팀", json.dumps(r, ensure_ascii=False))

    def test_neither_schema_exposes_team(self):
        for name in ("get_extraction_prompt", "validate_result"):
            self.assertNotIn("team", PT.TOOLS[name]["inputSchema"]["properties"], name)

    def test_no_team_scoped_data_reaches_the_response(self):
        """골든셋 실물·다른 팀 값이 섞일 자리가 없는지 본다(사전과 계약 원문만 나가야 한다).

        나가는 예시는 `meta_prompts.GOLD` = 계약 원문 예시다(팀 골든셋 실물이 아니다 · 확인된
        예외). 팀 골든셋을 예시로 끌어오면 골드 문항의 답을 미리 보여 주는 것이 된다
        (`prismtools.get_examples` 독스트링과 같은 이유)."""
        r = prompt(call="category")
        for gold in MP.GOLD:
            self.assertTrue(gold["in"][:40] in r["system"], "계약 예시가 빠졌다: %s" % gold["in"][:40])
        self.assertNotIn(TEAM, json.dumps(r, ensure_ascii=False))


# ══════════════════════════════════════════════════════════════════════════════
class TestRegistry(unittest.TestCase):
    def test_both_tools_are_external_scope(self):
        for name in ("get_extraction_prompt", "validate_result"):
            self.assertEqual(PT.TOOLS[name]["scope"], "external", name)
            self.assertIn(name, PT.tools_for("external"))
            self.assertNotIn(name, PT.tools_for("internal"))

    def test_schemas_follow_the_registry_convention(self):
        for name in ("get_extraction_prompt", "validate_result"):
            s = PT.TOOLS[name]["inputSchema"]
            self.assertEqual(s["type"], "object", name)
            self.assertFalse(s.get("additionalProperties", True), name)
            for req in s["required"]:
                self.assertIn(req, s["properties"], "%s.%s" % (name, req))

    def test_the_call_enum_is_the_contract_call_list(self):
        self.assertEqual(PT.TOOLS["get_extraction_prompt"]["inputSchema"]
                         ["properties"]["call"]["enum"], list(MP.CALLS))

    def test_the_description_warns_that_validation_is_not_a_verdict(self):
        self.assertIn("판정하지 않는다", PT.TOOLS["validate_result"]["desc"])


if __name__ == "__main__":
    unittest.main()
