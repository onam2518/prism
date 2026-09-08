"""토픽 제외 조건(neg): 정의 차원의 배제 — '속보는 빼줘'가 실제 매칭에서 동작.

- 사용자 토픽: neg={cats,intents,keywords} · 하나라도 걸리면 모든 묶음에서 탈락(OR).
- 자동생성(휴리스틱): 배제 표현('빼줘/제외/말고')이 붙은 라벨은 선택이 아니라 neg 로.

실행: python3 -m pytest tests/test_topic_neg.py -q  (stdlib unittest · 의존성 0)
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_topic_curation import _row  # noqa: E402  (공용 행 헬퍼 재사용)


def _build(rows, **kw):
    from prism.topic import build_topics
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "r.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return build_topics(p, **kw)


def _rows():
    return [
        _row("경제 심층", entities=["삼성전자"], intent=["분석·해설"],
             cats=["Business and Finance"]),                                     # 0
        _row("경제 속보", entities=["한화"], intent=["분석·해설", "속보"],
             cats=["Business and Finance"]),                                     # 1 속보 겸함
        _row("연예 화제", entities=["넷플릭스"], intent=["흥미·화제"],
             cats=["Entertainment"]),                                            # 2
    ]


class TestCustomNeg(unittest.TestCase):
    def _custom(self, neg, rows=None):
        d = _build(rows or _rows(),
                   custom_defs=[{"id": "U-경제", "name": "경제",
                                 "cats": ["Business and Finance"], "neg": neg}])
        return next(b for g in d["custom"] for b in g["bundles"] if b["kind"] == "core")

    def test_neg_intent_blocks(self):
        core = self._custom({"intents": ["속보"]})
        self.assertEqual(core["content_ids"], [0])

    def test_neg_keyword_blocks_substring(self):
        core = self._custom({"keywords": ["한화"]})
        self.assertEqual(core["content_ids"], [0])

    def test_neg_cat_blocks(self):
        rows = _rows()
        rows[1]["item_meta"]["content_category"] = ["Business and Finance", "Sports"]
        core = self._custom({"cats": ["Sports"]}, rows=rows)
        self.assertEqual(core["content_ids"], [0])

    def test_neg_applies_to_all_bundles(self):
        d = _build(_rows(), custom_defs=[{
            "id": "U-경제", "name": "경제", "cats": ["Business and Finance"],
            "intents": ["분석·해설"], "req": {"cats": ["Business and Finance"]},
            "neg": {"intents": ["속보"]}}])
        g = d["custom"][0]
        self.assertTrue(any(x["v"] == "속보" for x in g["neg"]))
        for b in g["bundles"]:                                # 핵심·관련 전부에서 탈락
            self.assertNotIn(1, b["content_ids"])

    def test_preview_reports_blocked(self):
        from prism import topic as TP
        pv = TP.preview_definition(_rows(), {"뉴스"},
                                   {"cats": ["Business and Finance"], "neg": {"intents": ["속보"]}})
        self.assertEqual(pv["neg_blocked"], 1)
        core = next(b for b in pv["bundles"] if b["kind"] == "core")
        self.assertEqual(core["content_ids"], [0])


class TestSanitizeAndSuggest(unittest.TestCase):
    def test_sanitize_parses_neg_and_drops_conflicts(self):
        from prism.serve import _sanitize_def
        d = _sanitize_def({"name": "x", "intents": ["분석·해설"],
                           "neg": {"intents": ["속보", "분석·해설"], "keywords": ["한화"]}})
        self.assertEqual(d["neg"]["intents"], ["속보"])       # 선택과 겹치면 선택 우선
        self.assertEqual(d["neg"]["keywords"], ["한화"])
        self.assertEqual(d["neg"]["cats"], [])

    def test_suggest_dims_detects_negation(self):
        from prism.topic import suggest_dims
        rows = _rows()
        sug = suggest_dims("금융 심층 분석 콘텐츠 모으고 속보는 빼줘", rows, {"뉴스"})
        self.assertTrue(any("속보" in x for x in sug["neg"]["intents"]))
        self.assertFalse(any("속보" in x for x in sug["intents"]))
        self.assertIn("심층 분석", sug["intents"])

    def test_suggest_dims_neg_category(self):
        from prism.topic import suggest_dims
        sug = suggest_dims("연예 화제 콘텐츠 모아줘, 스포츠는 빼고", _rows(), {"뉴스"})
        self.assertIn("Sports", sug["neg"]["cats"])
        self.assertNotIn("Sports", sug["cats"])

    def test_suggest_dims_neg_keyword(self):
        from prism.topic import suggest_dims
        sug = suggest_dims("경제 콘텐츠에서 한화 제외해줘", _rows(), {"뉴스"})
        self.assertIn("한화", sug["neg"]["keywords"])
        self.assertNotIn("한화", sug["keywords"])

    def test_suggest_dims_without_negation_unchanged(self):
        from prism.topic import suggest_dims
        sug = suggest_dims("심층 분석 콘텐츠 모아줘", _rows(), {"뉴스"})
        self.assertIn("심층 분석", sug["intents"])
        self.assertEqual(sug["neg"], {"cats": [], "intents": [], "keywords": [], "srcs": []})


class TestSuggestPromptAllFamilies(unittest.TestCase):
    def test_exclude_lands_in_every_family_wrapper(self):
        """exclude 지시는 {SCHEMA}·{RULES}·{EXAMPLES} 슬롯으로 전 계열 래퍼에 조립된다.
        (gemini·claude 래퍼는 {SELF_CHECK} 슬롯이 없는 기존 설계 — 핵심 제약은 RULES 로 커버)"""
        from prism import meta_prompts as MP
        models = {"gpt": "gpt-5.4", "gemini": "openrouter/gemini-2.5-pro",
                  "claude": "claude-opus-4-8", "solar": "solar-pro2", "default": "unknown-x"}
        marks = ('"exclude": {"cats"',                     # 스키마
                 "exclude(제외)", "빼줘/제외/말고",          # 규칙(배제 표현 + must/optional 금지)
                 "배제 대상은 must/optional 에 절대 넣지 않는다",
                 '"exclude":{"cats":[],"intents":["속보","사건 경과 보도"]')  # 예시
        for fam, model in models.items():
            p = MP.topic_suggest_system(model, ["스포츠=Sports"], ["심층 분석"], ["Sports"], [])
            for m in marks:
                self.assertIn(m, p, f"{fam} 래퍼에 exclude 지시 누락: {m}")


class TestNegWithExclusionOverlay(unittest.TestCase):
    def test_neg_and_per_content_exclusion_compose(self):
        """정의 제외(neg)와 개별 제외(오버레이)가 함께 동작."""
        from prism.topic import _row_hash
        rows = _rows() + [_row("경제 심층2", entities=["카카오"], intent=["분석·해설"],
                               cats=["Business and Finance"])]                   # 3
        d = _build(rows,
                   custom_defs=[{"id": "U-경제", "name": "경제",
                                 "cats": ["Business and Finance"],
                                 "neg": {"intents": ["속보"]}}],
                   exclusions={"U-경제": [_row_hash(rows[0])]})
        core = next(b for g in d["custom"] for b in g["bundles"] if b["kind"] == "core")
        self.assertEqual(core["content_ids"], [3])            # 1=neg · 0=개별 제외
        self.assertEqual(core["excluded_n"], 1)               # 개별 제외만 집계(neg 는 매칭 자체 제외)


if __name__ == "__main__":
    unittest.main()
