"""모델 표시 정보(prism/modelmeta.py) + 모델 선택 드롭다운 마크업 펼치기(page.py).

설계 근거: 드롭다운이 원본 id 대신 읽는 이름·제공자·비용 등급을 보여준다.
등급은 실제 누적 비용 원장(cost_rollup)에서 계산하되, 과금 0 인 묶음(라우터 402 로 전건
실패한 배치)은 평균을 왜곡하므로 제외한다.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import modelmeta as MM


class TestLabel(unittest.TestCase):
    def test_version_segments_join_with_dot(self):
        self.assertEqual(MM.label("claude-opus-4-8"), "Claude Opus 4.8")
        self.assertEqual(MM.label("claude-sonnet-4-6"), "Claude Sonnet 4.6")
        self.assertEqual(MM.label("claude-haiku-4-5"), "Claude Haiku 4.5")

    def test_router_prefix_stripped(self):
        self.assertEqual(MM.label("anthropic/claude-opus-4.6"), "Claude Opus 4.6")
        self.assertEqual(MM.label("google/gemini-2.5-flash"), "Gemini 2.5 Flash")

    def test_name_and_version_split(self):
        self.assertEqual(MM.label("solar-pro2"), "Solar Pro 2")      # pro2 → Pro 2

    def test_gpt_keeps_brand_hyphen(self):
        self.assertEqual(MM.label("gpt-5.4"), "GPT-5.4")
        self.assertEqual(MM.label("gpt-5.4-mini"), "GPT-5.4 mini")

    def test_unknown_id_survives(self):
        self.assertEqual(MM.label(""), "")
        self.assertTrue(MM.label("some-new-model"))                  # 모르는 id 도 이름은 만든다


class TestFamily(unittest.TestCase):
    def test_known_families(self):
        for mid, fam in (("claude-opus-4-8", "anthropic"), ("gpt-5.4", "openai"),
                         ("gemini-3.5-flash", "google"), ("solar-pro2", "upstage"),
                         ("deepseek-chat", "deepseek"), ("codestral", "mistral"),
                         ("grok-4.3", "xai")):
            self.assertEqual(MM.family(mid), fam, mid)

    def test_router_prefix_wins(self):
        self.assertEqual(MM.family("anthropic/claude-opus-4.6"), "anthropic")

    def test_unknown_is_blank_not_crash(self):
        self.assertEqual(MM.family("zzz-9"), "")
        self.assertEqual(MM.family(""), "")


class TestTiers(unittest.TestCase):
    def _rep(self, days):
        return {"days": days}

    def test_high_tier_from_average(self):
        rep = self._rep({"2026-07-15": {"models": {"m": {"n": 100, "cost": 0.9}}}})
        t = MM.tiers_from_cost(rep)["m"]
        self.assertEqual(t["tier"], "high")                          # $0.009/건
        self.assertAlmostEqual(t["avg_usd"], 0.009, places=6)

    def test_low_tier_from_average(self):
        rep = self._rep({"2026-07-15": {"models": {"m": {"n": 100, "cost": 0.05}}}})
        self.assertEqual(MM.tiers_from_cost(rep)["m"]["tier"], "low")   # $0.0005/건

    def test_small_sample_has_no_tier(self):
        rep = self._rep({"2026-07-15": {"models": {"m": {"n": 3, "cost": 0.9}}}})
        self.assertEqual(MM.tiers_from_cost(rep)["m"]["tier"], "")

    def test_zero_cost_batch_excluded(self):
        """라우터 402 로 전건 실패한 날(실행 수만 쌓이고 과금 0)이 평균을 끌어내리면 안 된다.
        2026-07-28 실제 사고: Opus 실제 $0.0067/건이 실패 600건 때문에 $0.0034/건이 됐다."""
        rep = self._rep({
            "2026-07-15": {"models": {"m": {"n": 200, "cost": 1.34}}},   # 정상 · $0.0067/건
            "2026-07-28": {"models": {"m": {"n": 600, "cost": 0.0}}},    # 402 전건 실패
        })
        t = MM.tiers_from_cost(rep)["m"]
        self.assertEqual(t["n"], 200)                                # 실패분은 표본에서 빠진다
        self.assertAlmostEqual(t["avg_usd"], 0.0067, places=4)
        self.assertEqual(t["tier"], "high")

    def test_model_meta_covers_never_run_models(self):
        meta = MM.model_meta({}, ["claude-haiku-4-5"])
        self.assertIn("claude-haiku-4-5", meta)
        row = meta["claude-haiku-4-5"]
        self.assertEqual(row["label"], "Claude Haiku 4.5")
        self.assertEqual(row["familyLabel"], "Anthropic")
        self.assertEqual(row["tier"], "")                            # 표본 없음 = 무표기
        self.assertEqual(row["runs"], 0)


class TestRouterCatalogInSync(unittest.TestCase):
    """서버 표시 목록(KNOWN_ROUTER_MODELS)과 화면 호출 목록(app-02 modelCatalog)이
    어긋나면 처음 고르는 모델이 원본 id 로 보인다 — 두 목록의 일치를 지킨다."""

    def test_client_catalog_ids_all_known(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "prism", "vendor", "app-02-_afterverdict.js"),
                  encoding="utf-8") as f:
            src = f.read()
        i = src.index("modelCatalog:")
        block = src[i:src.index("\n      },", i)]
        ids = set(re.findall(r"'([a-zA-Z0-9._/-]+)'", block))
        ids = {m for m in ids if "-" in m or "/" in m or "." in m}    # 키(text/vision) 제외
        missing = sorted(ids - set(MM.KNOWN_ROUTER_MODELS))
        self.assertFalse(missing, f"modelmeta.KNOWN_ROUTER_MODELS 에 없는 모델: {missing}")


class TestModelPickMarkup(unittest.TestCase):
    """<x-modelpick> 한 줄이 드롭다운 마크업으로 펼쳐지는지(page.py)."""

    def test_all_placeholders_expanded(self):
        from prism import page
        self.assertNotIn("<x-modelpick", page.PAGE)                  # 미펼침 태그가 남으면 안 된다
        self.assertEqual(page.PAGE.count('class="mpick"'), 10)       # 모델 고르는 곳 10군데(토픽 스튜디오 합류)

    def test_expansion_wires_value_and_setter(self):
        from prism import page
        out = page._mpick_markup({"key": "t", "value": "rawModel", "set": "rawModel=$v",
                                  "options": "rawModels", "first-label": "전체", "first-value": ""})
        self.assertIn("mpRows(rawModels,{first:{label:'전체',value:''}})", out)
        self.assertIn("rawModel=row.value", out)                     # $v → 고른 행의 값
        self.assertIn('x-bind:class="(rawModel)===row.value', out)   # 현재 선택 표시

    def test_grouped_source_uses_groups(self):
        from prism import page
        out = page._mpick_markup({"key": "g", "value": "textValue", "set": "onTextPick($v)",
                                  "groups": "textGroups"})
        self.assertIn("mpRows(textGroups,{})", out)
        self.assertIn("onTextPick(row.value)", out)

    def test_quotes_in_labels_escaped(self):
        from prism import page
        out = page._mpick_markup({"key": "q", "value": "v", "set": "v=$v",
                                  "options": "opts", "first-label": "it's all"})
        self.assertIn("\\'", out)                                    # 식이 깨지지 않게 이스케이프


if __name__ == "__main__":
    unittest.main()
