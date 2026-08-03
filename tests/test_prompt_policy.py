"""프롬프트·사전 정책 회귀 (2026-07-08 회의 소요 반영).

- 엔티티 수량: 1~3 상한 → 핵심만(건당 가변). 프롬프트 3곳(규칙·스키마·자가검증)과
  agents 절단([:3]) 동기 제거 · 잔존 상한 표기가 다시 들어오면 실패.
- 복합 명사: 분해 금지 지침이 엔티티 정의에 존재.
- 인텐트 관점 축: 옹호·지지 / 반박·비판 신설(범용① · 전 서비스 주입) + 정의 +
  의견·논쟁과의 경계 지침이 사전 주입 텍스트에 포함.

실행: python3 -m pytest tests/ -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestPromptPolicy(unittest.TestCase):
    def test_entity_count_cap_removed(self):
        from prism import meta_prompts as MP
        blob = MP.CALL_RULES["entities"] + MP.CALL_SCHEMAS["entities"] + MP.CALL_SELF_CHECK["entities"]
        self.assertNotIn("1~3", blob)                       # 상한 표기 재유입 방지
        self.assertIn("복합 명사", blob)                     # 분해 금지 지침 존재
        self.assertIn("상한은 없다", blob)

    def test_intent_count_cap_removed(self):
        # 인텐트도 엔티티와 동일 정책: N개 · 상한 없음 (2026-07-17 · 구 '총 1~3개 권장' 폐기)
        from prism import meta_prompts as MP
        blob = MP.CALL_RULES["intent"]
        self.assertNotIn("1~3", blob)
        self.assertNotIn("0~2", blob)
        self.assertIn("상한은 없다", blob)

    def test_verify_no_intent_truncation(self):
        # 검증 단계가 사전 화이트리스트 정제만 하고 개수를 절단하지 않는다
        from prism import verify as V
        from prism import dictionaries as D
        from prism.schema import ItemMeta, Content
        vals = D.intent_categories_for("뉴스")[:5]
        im = ItemMeta(summary="s", entities=[], intent=list(vals), content_category=[])
        V.verify_item(im, Content(displayServiceName="뉴스", title="t", body="b"))
        self.assertEqual(im.intent, vals)                   # 5개 그대로 보존([:2] 절단 재유입 방지)

    def test_entity_count_cap_removed_merged_fallback(self):
        # 통합 1콜 폴백 스키마도 분리형과 같은 정책(상한 없음)이어야 한다 (2026-07-09 잔존 표기 제거)
        from prism import meta_prompts as MP
        blob = MP.MERGED_SCHEMA + MP.MERGED_SELF_CHECK
        self.assertNotIn("1~3", blob)
        self.assertIn("상한 없음", blob)

    def test_agents_no_entity_truncation(self):
        import inspect
        from prism import agents as AG
        src = inspect.getsource(AG)
        self.assertNotIn("entities\")) if str(x).strip()][:3]", src)   # [:3] 절단 제거

    def test_perspective_intents_registered(self):
        from prism import dictionaries as D
        for svc in ("뉴스", "티스토리", "미정의서비스"):
            vals = D.intent_categories_for(svc)
            self.assertIn("옹호·지지", vals)                 # 범용① = 전 서비스 주입·검증 통과
            self.assertIn("반박·비판", vals)
        self.assertIn("옹호·지지", D.INTENT_VALUE_DEFS)
        self.assertIn("반박·비판", D.INTENT_VALUE_DEFS)
        self.assertIn("의견·논쟁", D.INTENT_VALUE_DEFS.get("옹호·지지", ""))   # 경계 명시

    def test_intent_dictionary_text_has_boundary_rule(self):
        from prism import meta_prompts as MP
        txt = MP.intent_dictionary_text("뉴스")
        self.assertIn("옹호·지지", txt)
        self.assertIn("반박·비판", txt)
        self.assertIn("관점 축 구분", txt)                   # 경계 지침 주입

    # ── 2026-07-09 교정: 관점 축 동시 성립 시 반박·비판 우선 + kNN은 관점 판정 제외 ──
    def test_perspective_tiebreak_rule(self):
        from prism import dictionaries as D
        from prism import meta_prompts as MP
        self.assertIn("반대하는 것이 논지의 중심", D.INTENT_VALUE_DEFS["반박·비판"])   # 정의 우선 규칙
        self.assertIn("반박·비판을 우선", MP.intent_dictionary_text("뉴스"))          # 프롬프트 우선 규칙

    def test_intent_anchors_exclude_perspective(self):
        from prism import classify as C

        class _FakeEmb:
            def embed(self, text, is_query=False):
                return [1.0, 0.0]

        anchors = C.intent_category_anchors(_FakeEmb(), "뉴스")
        self.assertNotIn("옹호·지지", anchors)               # 논조는 kNN 후보에서 제외
        self.assertNotIn("반박·비판", anchors)
        self.assertIn("심층 분석", anchors)                  # 나머지 후보는 유지

    def test_merge_perspective_preserves_llm_verdict(self):
        from prism.classify import merge_perspective
        # 임베딩 kNN + LLM 관점 축 → 관점 축 보존 · 개수 상한 없음(base 절단 금지 · 관점은 우선 규칙상 1개)
        self.assertEqual(merge_perspective(["트렌드·시장 분석", "정형정보"], ["반박·비판", "심층 분석"]),
                         ["트렌드·시장 분석", "정형정보", "반박·비판"])
        self.assertEqual(merge_perspective(["심층 분석"], []), ["심층 분석"])          # 관점 없으면 그대로
        self.assertEqual(merge_perspective([], ["옹호·지지"]), ["옹호·지지"])          # emb 결과 없어도 보존

    def test_trend_intent_def_scoped(self):
        from prism import dictionaries as D
        d = D.INTENT_VALUE_DEFS["트렌드·시장 분석"]
        self.assertIn("소비·시장·라이프스타일일 때만", d)     # 여론조사 과포괄 차단
        self.assertNotIn("설문 기반 +", d)                   # 구 정의(무조건 통합) 재유입 방지

    # ── 2026-07-09 정책 정합: 멜론=PGC · 품질 메타 우선순위는 상황부 규칙 ──
    def test_melon_service_group_is_media(self):
        from prism import dictionaries as D
        self.assertEqual(D.SERVICE_GROUP["음악"], "media")    # 멜론 = PGC (위키 277118998)
        # PGC 자동 비활성 메타가 음악 그룹에도 적용되는지 (political·hate·format 미검사)
        active = D.active_quality_metas(D.SERVICE_GROUP["음악"])
        for m in ("political", "hate", "format"):
            self.assertNotIn(m, active)

    def test_quality_priority_contextual_rules(self):
        from prism import dictionaries as D
        from prism import promptstore as P
        self.assertFalse(hasattr(D, "QUALITY_PRIORITY"))      # 고정 우선순위 리스트 폐기
        txt = D.priority_rules_text(["graphic", "ad", "shallow", "sexual", "hate", "profanity"])
        self.assertIn("graphic 을 ad·shallow 보다 우선", txt)  # 비활성(political) 제외 렌더
        self.assertIn("hate 을 profanity 보다 우선", txt)
        self.assertIn("둘 다 명확하면 둘 다 부여", txt)
        self.assertEqual(D.priority_rules_text(["clickbait", "spam"]), "")   # 해당 규칙 없음
        sysmsg = P.render_quality_system(
            D.active_quality_metas("ugc"), "ugc", json_guard="")
        self.assertIn("동시 부여가 원칙", sysmsg)              # 절차 문구 갱신 반영
        self.assertNotIn("graphic > sexual", sysmsg)          # 구 고정 체인 재유입 방지


# ── 2026-08-03 운영파트 합의: '포토·영상 중심' 판정 기준 전면 개정(imeta@v16) ──
# 가정: 이번 합의가 260715 회의 결정('캡션이 본문과 직접 연관될 때만')을 대체한다(양방향 충돌).
class TestPhotoIntentCriteria(unittest.TestCase):
    def test_old_caption_rule_removed(self):
        from prism import meta_prompts as MP
        from prism import dictionaries as D
        blob = MP.CALL_RULES["intent"] + D.INTENT_VALUE_DEFS["포토·영상 중심"]
        self.assertNotIn("캡션이 본문과 직접 연관될 때만", blob)     # 구 기준 재유입 방지
        self.assertNotIn("이미지·영상이 많더라도", blob)             # 정량 프레이밍 제거
        self.assertNotIn("이미지가 다수여도", blob)

    def test_new_criteria_in_rules(self):
        from prism import meta_prompts as MP
        r = MP.CALL_RULES["intent"]
        self.assertIn("직접 촬영", r)                                # 컨셉: 직접 촬영한 현장 사진
        self.assertIn("이미지 구좌", r)                              # 목적: 편성 선별
        self.assertIn("한 장뿐이어도", r)                            # 1장이어도 부여 가능
        self.assertIn("정량 임계로 판정하지 않는다", r)              # 정량 기준 금지(합의 조건)

    def test_negative_cases_enumerated(self):
        """X사례군 6종이 미부여 규칙으로 명문화됐는가."""
        from prism import meta_prompts as MP
        r = MP.CALL_RULES["intent"]
        for token in ("프로필 사진", "사진이 내용의 주가 아닌 것", "직접 촬영이 아닌",
                      "생성형 AI 이미지만", "품질이 낮거나", "단순 보도"):
            self.assertIn(token, r, token)

    def test_boundary_rules_present(self):
        """경계 규칙 신설: 현장취재·르포 / 그래픽·인포그래픽 / 카드뉴스·인포그래픽 / 인터뷰."""
        from prism import meta_prompts as MP
        r = MP.CALL_RULES["intent"]
        self.assertIn("포토·영상 중심의 경계", r)
        for v in ("현장취재·르포", "그래픽·인포그래픽", "카드뉴스·인포그래픽", "인터뷰"):
            self.assertIn(v, r, v)
        self.assertIn("자동 병기 금지", r)                           # 변별력 0 방지(운영파트 조건)

    def test_no_photo_count_range_notation(self):
        """정량 장수 표기 금지(기존 수량 상한 가드와 같은 계약)."""
        from prism import meta_prompts as MP
        r = MP.CALL_RULES["intent"]
        for bad in ("1~3", "0~2", "3~5장", "장 이상"):
            self.assertNotIn(bad, r, bad)

    def test_form_universal_defs_injected(self):
        """범용②도 정의문 병기 → INTENT_VALUE_DEFS 수정만으로 프롬프트가 따라온다."""
        from prism import meta_prompts as MP
        from prism import dictionaries as D
        txt = MP.intent_dictionary_text("뉴스")
        self.assertIn(D.INTENT_VALUE_DEFS["포토·영상 중심"], txt)
        self.assertIn(D.INTENT_VALUE_DEFS["현장취재·르포"], txt)
        self.assertNotIn(" / ".join(D.INTENT_FORM_UNIVERSAL), txt)   # 구 '이름만 나열' 형태 제거

    def test_intent_user_message_signals(self):
        """③ 인텐트 콜 입력 확장: title·본문 글자수·이미지 수·본문 도입부."""
        from prism import meta_prompts as MP
        from prism.schema import Content
        c = Content(displayServiceName="뉴스", title="[오늘의 1면 사진] 폭염", body="가" * 1200,
                    image_urls=["https://x/1.jpg", "https://x/2.jpg"])
        u = MP.call_user("intent", c, {"summary": "리드문"})
        self.assertIn("[오늘의 1면 사진]", u)                        # 제목 표지가 판정 단서
        self.assertIn("본문 글자수: 1200", u)
        self.assertIn("이미지 수: 2", u)
        self.assertIn("본문 도입부:", u)
        self.assertLess(len(u), 1200)                                # 본문 전문 주입 아님(토큰 억제)

    def test_image_count_unknown_is_not_zero(self):
        """image_urls 미제공(운영 인입 현황) → '0장'으로 단정하지 않는다."""
        from prism import meta_prompts as MP
        from prism.schema import Content
        for c in (Content(displayServiceName="뉴스", title="t", body="b"),
                  Content(displayServiceName="뉴스", title="t", body="b", image_urls=[])):
            u = MP.call_user("intent", c, {"summary": "s"})
            self.assertIn("이미지 수: 정보 없음", u)
            self.assertNotIn("이미지 수: 0", u)

    def test_intent_example_shows_boundary(self):
        from prism import dictionaries as D
        ex = D.INTENT_EXAMPLES["포토·영상 중심"]
        self.assertIn("O)", ex)
        self.assertIn("X)", ex)

    def test_imeta_version_bumped(self):
        from prism import prompts as P
        self.assertTrue(P.IMETA_VERSION.startswith("imeta@v16"), P.IMETA_VERSION)


# ── 2026-07-10 보완: 시드 지문 불일치 시 내장 버전 자동 재시드 ──
class TestPromptSeedMigration(unittest.TestCase):
    def setUp(self):
        import tempfile
        from prism import promptstore as P
        self.P = P
        self._tmp = tempfile.mkdtemp()
        self._orig = (P.PROMPTS_DIR, P.QUALITY_PATH)
        P.PROMPTS_DIR = self._tmp
        P.QUALITY_PATH = os.path.join(self._tmp, "quality.json")

    def tearDown(self):
        import shutil
        self.P.PROMPTS_DIR, self.P.QUALITY_PATH = self._orig
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_fresh_seed_has_stamp_and_new_procedure(self):
        import json as J
        P = self.P
        self.assertIn("동시 부여가 원칙", P.get("v31")["procedure"])   # 코드 시드 반영
        data = J.load(open(P.QUALITY_PATH, encoding="utf-8"))
        self.assertEqual(data["seed_stamp"], P._seed_stamp())          # 지문 기록

    def test_stale_file_reseeds_builtins_preserving_custom(self):
        import json as J
        P = self.P
        stale = {                                                      # 구 포맷: 지문 없음 + 옛 문구 + 커스텀
            "active": "myv",
            "versions": {
                "v31": {**P._seed_v31(),
                        "procedure": "[판정 절차]\n4. 복수일 때 대표 우선순위: {priority}."},
                "myv": {**P._seed_v31(), "procedure": "내 튜닝 절차"},
            },
        }
        os.makedirs(P.PROMPTS_DIR, exist_ok=True)
        J.dump(stale, open(P.QUALITY_PATH, "w", encoding="utf-8"), ensure_ascii=False)
        self.assertIn("동시 부여가 원칙", P.get("v31")["procedure"])   # 내장은 코드 시드로 갱신
        self.assertEqual(P.active_name(), "myv")                       # active 선택 보존
        self.assertEqual(P.get("myv")["procedure"], "내 튜닝 절차")     # 사용자 버전 보존
        data = J.load(open(P.QUALITY_PATH, encoding="utf-8"))
        self.assertEqual(data["seed_stamp"], P._seed_stamp())          # 지문 기록됨

    def test_up_to_date_file_not_rewritten(self):
        P = self.P
        P.get("v31")                                                   # 시드 생성(지문 최신)
        before = open(P.QUALITY_PATH, encoding="utf-8").read()
        P.get("v31"); P.active_name()                                  # 재로드해도
        after = open(P.QUALITY_PATH, encoding="utf-8").read()
        self.assertEqual(before, after)                                # 재작성 없음(멱등)


if __name__ == "__main__":
    unittest.main()
