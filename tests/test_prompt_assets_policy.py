"""프롬프트 자산 전면 정비 회귀 (2026-09-22 정책 · 511247058 · 371131847 · 278036632 · 319783633).

무엇을 고정하는가
- 모델 계열 전체(gpt·gemini·claude·solar·deepseek·o1/o3/o4·default)의 **실제 합성 결과**에
  폐지된 문구가 하나도 없다. 규칙·예시·래퍼 중 어디에 다시 들어와도 여기서 깨진다.
- 저장 버전 두 개(v31·v32)가 모두 렌더되고, 현행 품질 정책 문장을 담는다.
- 외부 배포 프롬프트가 서비스 문구 없이 나간다.
- 학습 피드백의 폐기 분류값은 치환하지 않고 '재확인 필요' 표식만 받는다(단일 별칭 치환 금지).

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import dictionaries as D
from prism import feedback_loop as FL
from prism import meta_prompts as MP
from prism import promptdist as PD
from prism import promptstore as PS

# 계열별 대표 모델. family_of 가 인식하는 분기를 전부 지난다(o1/o3/o4 는 gpt 계열로 수렴).
FAMILY_MODELS = {
    "gpt": "gpt-5.4", "gemini": "gemini-3-pro", "claude": "claude-opus-4-8",
    "solar": "solar-pro3", "default": "deepseek-chat",
    "o1": "o1-pro", "o3": "o3-mini", "o4": "o4-mini",
    "router": "openai/gpt-5.4",
}

# 2026-09-22 로 폐지된 문구. 하나라도 합성 프롬프트에 남으면 모델이 서비스 분기를 다시 한다.
BANNED = (
    "displayServiceName",
    "서비스 카테고리",          # "displayServiceName으로 서비스 카테고리를 분기한다"
    "미정의",                   # "미정의값이면 PGC 그룹으로 처리하고 범용 분류값만 부여"
    "콘텐츠뷰면",               # "콘텐츠뷰면 서비스값 '칼럼'을 함께 부여한다"
    "리드문을 기반으로",
    "서비스값",
    "서비스별 후보",
)


def _all_call_systems(model: str) -> str:
    return "\n".join(MP.call_system(model, call) for call in MP.CALLS)


class TestNoServiceBranchInAnyFamily(unittest.TestCase):
    def test_every_family_composition_is_clean(self):
        for family, model in FAMILY_MODELS.items():
            blob = _all_call_systems(model)
            for phrase in BANNED:
                self.assertNotIn(phrase, blob, f"{family}({model}) 합성 프롬프트에 '{phrase}'")

    def test_merged_single_call_is_clean(self):
        from prism import prompts as P
        from prism.schema import Content
        c = Content(displayServiceName="뉴스", title="t", subtitle="", body="b")
        for model in FAMILY_MODELS.values():
            blob = P.item_system(c, model)
            for phrase in BANNED:
                self.assertNotIn(phrase, blob, f"통합 1콜({model})에 '{phrase}'")
        self.assertEqual(P.item_user(c), "title: t\nbody: b")   # 모델 입력은 제목·본문뿐

    def test_retired_intent_values_are_not_offered(self):
        blob = _all_call_systems("gpt-5.4")
        for old in D.INTENT_RETIRED:
            self.assertNotIn(old, blob, f"통합으로 폐기된 '{old}' 가 후보·정의문에 남음")

    def test_gold_examples_show_title_body_only(self):
        # 규칙에서 서비스 분기를 걷어내도 예시가 서비스명을 판정 근거처럼 다시 보여 주면 안 된다.
        for ex in MP.GOLD:
            self.assertTrue(ex["in"].startswith("title="), ex["in"][:40])
        self.assertIn("삼성전자", MP.gold_examples("entities"))   # 예시 내용·기대 출력은 보존

    def test_no_call_rule_cites_another_calls_output(self):
        # 4호출은 서로의 출력을 입력으로 받지 않는다(call_user 는 title·body 만 보낸다).
        # ③ 인텐트 규칙이 판정 근거로 '리드문'을 들면 모델이 없는 입력을 찾는다.
        self.assertNotIn("리드문", MP.CALL_RULES["intent"])
        self.assertNotIn("리드문", MP.CALL_RULES["category"])
        self.assertNotIn("리드문", MP.CALL_RULES["entities"])

    def test_family_wrappers_have_no_service_branch(self):
        for family, tpl in MP.FAMILY_WRAPPER_DEFAULT.items():
            for phrase in BANNED:
                self.assertNotIn(phrase, tpl, f"{family} 래퍼에 '{phrase}'")


class TestQualityVersionsRender(unittest.TestCase):
    """저장 버전 전체(v31·v32)가 렌더되고 현행 품질 정책 문장을 담는다."""

    def setUp(self):
        self.metas = D.active_quality_metas("ugc")

    def _render(self, version, group="ugc"):
        return PS.render_quality_system(self.metas, group, "", version=version)

    def test_both_builtin_versions_render(self):
        for version in ("v31", "v32"):
            txt = self._render(version)
            self.assertIn("finalGrade", txt)
            self.assertIn("[0순위", txt)                       # 2-14
            self.assertIn("본문 도입부", txt)                   # 2-15
            self.assertIn("1단계 제목 선별", txt)                # 2-24
            self.assertIn("2단계 제목+본문 정밀 판정", txt)        # 2-24
            self.assertIn("사용자 생성(UGC) 그룹", txt)          # 2-25 · 그룹 이름 한 단어 이상
            self.assertIn("graphic 을 먼저 평가", txt)           # 4-33

    def test_whitelist_names_international_conflict(self):
        self.assertIn("국제분쟁", self._render("v31"))

    def test_group_rules_differ_by_service_group(self):
        media = self._render("v31", "media")
        self.assertIn("전문 생성(PGC) 그룹", media)
        self.assertNotIn("사용자 생성(UGC) 그룹", media)

    def test_v32_ad_rule_is_three_condition_and(self):
        # 4-23: 화보·실적 보도를 광고 신호로 잡던 v32 규칙을 3축 AND + 제외로 바꿨다.
        ad = PS.get("v32")["meta_rules"]["ad"]
        self.assertIn("세 축을 모두 충족할 때만", ad)
        self.assertIn("화보", ad.split("제외(=ad 아님)")[1])
        self.assertIn("실적", ad.split("제외(=ad 아님)")[1])
        self.assertNotIn("브랜드 화보·시즌 컬렉션·라인업 소개", ad)

    def test_v32_keeps_its_own_experiment_rules(self):
        # 두 버전의 차이(v32 의 실험 규칙)는 그대로 둔다 · 정책 문장만 맞췄다.
        v31, v32 = PS.get("v31"), PS.get("v32")
        self.assertNotEqual(v31["meta_rules"]["spam"], v32["meta_rules"]["spam"])
        self.assertEqual(v31["procedure"], v32["procedure"])

    def test_active_version_unchanged(self):
        self.assertEqual(PS.active_name(), "v31")

    def test_render_survives_a_user_version_without_new_keys(self):
        # new_from 으로 만든 옛 사용자 버전에는 whitelist·quant·group_rules 가 없다 → .get 폴백.
        from unittest import mock
        legacy = {k: v for k, v in PS.get("v31").items()
                  if k not in ("whitelist", "quant", "group_rules")}
        with mock.patch.object(PS, "get", return_value=legacy):
            txt = self._render("vtest_legacy")
        self.assertIn("finalGrade", txt)
        self.assertNotIn("[0순위", txt)


class TestLegalLabelsMatchPolicyTable(unittest.TestCase):
    """4-21 · 274040185 '위반 유형(요약)' 표의 라벨·근거 법령 표기."""

    TABLE = {
        "defamation": ("명예훼손", "형법 §307·§309, 정보통신망법 §70"),
        "insult": ("모욕", "형법 §311"),
        "obscenity": ("음란물 유포", "정보통신망법 §44의7①-1, 형법 §243"),
        "sexual_violence": ("성폭력 촬영물·유포", "성폭력처벌법 §14"),
        "privacy_violation": ("개인정보 침해", "개인정보보호법 §71"),
        "stalking": ("스토킹·괴롭힘", "스토킹처벌법 §18, 정보통신망법 §44의7①-3"),
        "hate_speech": ("차별·혐오 조장", "정보통신망법 §44의7①-2의2"),
        "copyright": ("저작권 침해", "저작권법 §136"),
        "fraud": ("사기·허위 정보", "형법 §347, 정보통신망법 §44의7②"),
        "election_interference": ("선거 관련 위반", "공직선거법 §250·§230"),
        "ad_fraud": ("기만적 광고", "표시광고법 §3, 신문법 §6③"),
        "gambling": ("사행성 조장", "정보통신망법 §44의7①-6, 형법 §247"),
        "drug_weapon": ("마약류·무기 제조 정보", "정보통신망법 §44의7①-6의3·4"),
    }

    def test_thirteen_types_match(self):
        self.assertEqual(sorted(D.LEGAL_HARM_TYPES), sorted(self.TABLE))
        for code, (label, article) in self.TABLE.items():
            self.assertEqual(D.LEGAL_HARM_TYPES[code]["label"], label, code)
            self.assertEqual(D.LEGAL_HARM_TYPES[code]["article"], article, code)


class TestExternalPromptHasNoServiceWording(unittest.TestCase):
    def test_shipped_prompt_is_service_free(self):
        for call in PD.CALLS:
            out = PD.get_extraction_prompt(call=call, client_model="gpt-5")
            self.assertNotIn("error", out, call)
            for phrase in BANNED:
                self.assertNotIn(phrase, out["system"], f"{call} 배포 프롬프트에 '{phrase}'")
            self.assertNotIn("{displayServiceName}", out["user_template"])

    def test_service_argument_does_not_change_the_prompt(self):
        a = PD.get_extraction_prompt(call="intent", client_model="gpt-5")
        b = PD.get_extraction_prompt(call="intent", service="티스토리", client_model="gpt-5")
        self.assertEqual(a["system"], b["system"])
        self.assertEqual(a["fingerprint"], b["fingerprint"])

    def test_differences_note_never_mentions_service_branching(self):
        for line in PD._differences(True):
            for phrase in BANNED:
                self.assertNotIn(phrase, line)

    def test_validate_result_no_longer_requires_service(self):
        from prism import prismtools as PT
        self.assertEqual(PT.TOOLS["validate_result"]["inputSchema"]["required"], ["result"])
        out = PD.validate_result(result={"intent": D.intent_categories()[:1]})
        self.assertNotIn("error", out)


class TestRetiredIntentsAreMarkedNotRenamed(unittest.TestCase):
    """단일 별칭 치환 금지(511247058) · 옛 이름은 남기고 표식만 붙인다."""

    def test_line_with_retired_value_gets_marked(self):
        out = FL.mark_retired_intents("- 음악 기사에는 신곡·앨범 발매 를 붙여라")
        self.assertIn("신곡·앨범 발매", out)                   # 치환하지 않는다
        self.assertIn("재확인 필요", out)
        self.assertIn("재판정", out)

    def test_clean_line_is_untouched(self):
        txt = "- 속보성 기사에는 속보·사건 추적 을 붙여라"
        self.assertEqual(FL.mark_retired_intents(txt), txt)

    def test_meta_compile_input_carries_the_mark(self):
        class _Mock:
            mock = True
        out = FL.meta_compile(_Mock(), "analyze", "- 차트·랭킹 을 우선 부여")
        self.assertIn("차트·랭킹", out["directive"])
        self.assertIn("재확인 필요", out["directive"])


if __name__ == "__main__":
    unittest.main()
