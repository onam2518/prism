"""토픽 조건 축 정책(13211 · 132112): 분야 Tier 2 · 기본 제외 표 · 제외 표현 · 출처 4종 · 기준값 설정.

실행: python3 -m unittest tests.test_topic_policy  (stdlib unittest · 의존성 0)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import topic as TP                                        # noqa: E402


def _row(title, cats=(), intent=(), entities=(), svc="뉴스", src=None, grade="G"):
    r = {"content_ref": {"title": title, "subtitle": "", "body": "본문-" + title,
                         "displayServiceName": svc},
         "item_meta": {"summary": "리드-" + title, "entities": list(entities),
                       "intent": list(intent), "content_category": list(cats)},
         "quality_meta": {"finalGrade": grade, "review": "", "reasons": []}}
    if src is not None:
        r["src"] = src
    return r


def _core(rows, d, **kw):
    pv = TP.preview_definition(rows, set(), d, **kw)
    return next((b for b in pv["bundles"] if b["kind"] == "core"), {"content_ids": [], "count": 0})


class TestCategoryTiers(unittest.TestCase):
    """2-7: 분야 조건에 Tier 1 과 Custom Tier 2 를 함께 건다."""

    def test_tier1_and_tier2_both_match(self):
        rows = [_row("축구", cats=["Sports / Soccer (International)"]),
                _row("야구", cats=["Sports / Baseball (Domestic)"])]
        self.assertEqual(_core(rows, {"cats": ["Sports"]})["count"], 2)              # 상위만 말하면 상위만
        self.assertEqual(_core(rows, {"cats": ["Sports / Soccer (International)"]})["content_ids"], [0])

    def test_catalog_and_taxonomy_offer_tier2(self):
        rows = [_row("축구", cats=["Sports / Soccer (International)"])]
        cats = [x["k"] for x in TP.studio_catalog(rows, set())["cats"]]
        self.assertIn("Sports", cats)
        self.assertIn("Sports / Soccer (International)", cats)
        self.assertIn("Sports / Golf", TP.meta_taxonomy()["cats"])


class TestBaseExclusion(unittest.TestCase):
    """4-16: 기본 제외는 정상 상태만 통과시키고 유료광고 포함도 뺀다(스펙 표 그대로)."""

    def test_only_service_status_passes(self):
        rows = [_row("정상", src={"status": "SERVICE"}), _row("삭제", src={"status": "DELETE"}),
                _row("대기", src={"status": "STANDBY"}), _row("모름", src={}),
                _row("유료광고", src={"status": "SERVICE", "includePaidAd": True}),
                _row("광고", src={"status": "SERVICE", "ads": True})]
        blocked, _ = TP._feed_blocked(TP._content_dims(rows, set()), {})
        self.assertEqual(blocked, {1, 2, 4, 5})                 # 3(모름)은 통과 · 유료광고 포함도 제외
        off, _ = TP._feed_blocked(TP._content_dims(rows, set()), {"base_excl": False})
        self.assertEqual(off, {1})                             # 삭제는 운영자 옵션으로 해제 불가


class TestNegMarks(unittest.TestCase):
    """4-18: 제외를 뜻하는 말은 빼고 · 제외 · 말고 · 아닌 네 가지."""

    def test_four_marks_only(self):
        self.assertEqual(set(TP._NEG_MARKS), {"빼", "제외", "말고", "아닌"})
        for mark in ("는 빼고", "은 제외", " 말고", "이 아닌"):
            self.assertTrue(TP._neg_after("속보" + mark + " 분석", "속보"), mark)
        for mark in ("는 제거하고", " 없이"):
            self.assertFalse(TP._neg_after("속보" + mark + " 분석", "속보"), mark)


class TestSourceAxis(unittest.TestCase):
    """4-37: 출처 축은 CP · 서비스 · 채널 · 매체 네 종류 · 정확 일치."""

    def test_four_kinds_exact_match(self):
        rows = [_row("클립", svc="카카오TV", src={"cp": "연합뉴스", "channel": "ch-77", "cp_type": "tv"}),
                _row("본문", svc="뉴스", src={"cp": "연합뉴스TV", "cp_type": "media"})]
        for v, want in (("카카오TV", [0]), ("연합뉴스", [0]), ("ch-77", [0]), ("tv", [0]),
                        ("연합뉴스TV", [1]), ("media", [1])):
            self.assertEqual(_core(rows, {"srcs": [v]})["content_ids"], want, v)
        self.assertEqual(_core(rows, {"srcs": ["연합"]})["count"], 0)      # 부분일치 금지
        srcs = [x["k"] for x in TP.studio_catalog(rows, set())["srcs"]]
        self.assertEqual(set(srcs) & {"ch-77", "tv", "media"}, {"ch-77", "tv", "media"})


class TestThresholdSettings(unittest.TestCase):
    """4-43: 긴 글 · 많이 읽힌 기준값은 코드 상수가 아니라 설정값."""

    def test_settings_override_defaults(self):
        self.assertEqual(TP.parse_feed_text("긴 글만")["min_len"], TP.FEED_LEN_LONG)
        fd = TP.parse_feed_text("긴 글만 많이 읽힌 것", thresholds={"len_long": 900, "dri_high": 0.7})
        self.assertEqual((fd["min_len"], fd["min_dri"]), (900, 0.7))
        self.assertEqual(TP.feed_thresholds({"len_long": "x"})["len_long"], TP.FEED_LEN_LONG)


class TestEattrIsFifthAxis(unittest.TestCase):
    """4-17: 조건 축은 넷 · 개체 속성은 늘 걸리지 않는 보조 축."""

    def test_no_condition_makes_no_bundle(self):
        rows = [_row("a", cats=["Sports"])]
        self.assertEqual(TP.preview_definition(rows, set(), {"name": "빈"})["bundles"], [])
        specs, must, opt = TP._def_bundles({"cats": ["Sports"]})
        self.assertEqual([k for k, _ in must], ["cats"])                  # eattrs 없으면 축은 넷뿐


class _FakeStore:
    """별칭 조회만 하는 최소 스토어(엔티티 사전 대역)."""

    def __init__(self, aliases):
        self.aliases = aliases

    def ent_id_by_alias(self, name):
        return self.aliases.get(name, "")


class TestEntityCommonKey(unittest.TestCase):
    """2-6: 엔티티 조건은 문자열 부분일치가 아니라 공통키와 이표기 묶음."""

    def _rows(self):
        return [_row("A", entities=["손흥민"]), _row("B", entities=["쏘니"]),
                _row("C", entities=["손흥민상회"])]

    KEYS = {"손흥민": "e_son", "쏘니": "e_son"}

    def test_alias_bundle_matches_and_substring_does_not(self):
        core = _core(self._rows(), {"keywords": ["손흥민"]}, ent_keys=self.KEYS)
        self.assertEqual(core["content_ids"], [0, 1])          # 이표기는 같은 묶음 · 다른 이름은 미매칭

    def test_exclusion_uses_key_too(self):
        blocked = TP._neg_blocked(TP._content_dims(self._rows(), set(), ent_keys=self.KEYS),
                                  {"keywords": ["쏘니"]}, self.KEYS)
        self.assertEqual(blocked, {0, 1})

    def test_auto_entity_topic_id_is_common_key(self):
        pools = TP.build_entity_topics(self._rows(), set(), {}, min_contents=1, ent_keys=self.KEYS)
        son = next(p for p in pools if p["entity_key"] == "e_son")
        self.assertEqual((son["cluster_id"], son["count"]), ("S-e_son", 2))
        other = next(p for p in pools if not p["entity_key"])
        self.assertEqual(other["cluster_id"], "S-손흥민상회")   # 미등재는 종전대로 이름 기준
        # 옛 식별자(S-<이름>) 를 함께 실어 개별 제외·편입·일시정지 기억이 끊기지 않는다(검토 차단 항목 (나))
        self.assertTrue(set(son["legacy_ids"]) >= {"S-손흥민"}, son["legacy_ids"])
        self.assertEqual(other.get("legacy_ids"), [])

    def test_canon_keys_never_invents_values(self):
        from prism import entdict as ED
        st = _FakeStore({"손흥민": "e_son", "쏘니": "e_son"})
        self.assertEqual(ED.canon_keys(st, ["손흥민", "쏘니", "없는이름"]),
                         {"손흥민": "e_son", "쏘니": "e_son"})
        # 같은 이름으로 정규화되는 표기(공백 차이 등)는 하나만 남기지 않고 전부 돌려준다
        self.assertEqual(ED.canon_keys(st, ["손흥민", "손흥민 "]),
                         {"손흥민": "e_son", "손흥민 ": "e_son"})


if __name__ == "__main__":
    unittest.main()
