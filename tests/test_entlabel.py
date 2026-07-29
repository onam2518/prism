"""엔티티 관련성 라벨 · 확신도 가중치 최적화의 정답 수집(2026-07-29).

배경: 가중치를 어떻게 조합해도 판별력이 AUC 0.61 로 평평했다. 정답이 없어서다.
검수 교정 기록은 5건뿐이고 전부 추가(삭제 0) · 사전 상태는 '실존 개체인가'를 잴 뿐
'이 기사에서 중요한가'가 아니다. 그래서 라벨을 직접 받는다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import entlabel as ELB


class _FakeSV:
    def __init__(self):
        self.reports = {}

    def _report_get(self, kind, team=None, default=None):
        return self.reports.get(kind, default if default is not None else {})

    def _report_save(self, kind, payload, team=None):
        self.reports[kind] = payload


class Base(unittest.TestCase):
    def setUp(self):
        prev = ELB._SV
        self.sv = _FakeSV()
        ELB._SV = self.sv
        self.addCleanup(lambda: setattr(ELB, "_SV", prev))


class PutTest(Base):
    def test_records_a_vote(self):
        r = ELB.put("h1", "삼성전자", "y", "u1")
        self.assertTrue(r["ok"])
        self.assertEqual(r["mine"], "y")
        self.assertEqual(r["counts"], {"yes": 1, "no": 0, "n": 1})

    def test_same_reviewer_overwrites_not_duplicates(self):
        ELB.put("h1", "삼성전자", "y", "u1")
        r = ELB.put("h1", "삼성전자", "n", "u1")
        self.assertEqual(r["counts"], {"yes": 0, "no": 1, "n": 1})   # 2표가 되면 안 된다

    def test_blank_label_cancels_own_vote(self):
        ELB.put("h1", "삼성전자", "y", "u1")
        r = ELB.put("h1", "삼성전자", "", "u1")
        self.assertEqual(r["mine"], "")
        self.assertEqual(r["counts"]["n"], 0)

    def test_multiple_reviewers_accumulate(self):
        ELB.put("h1", "삼성전자", "y", "u1")
        r = ELB.put("h1", "삼성전자", "n", "u2")
        self.assertEqual(r["counts"], {"yes": 1, "no": 1, "n": 2})

    def test_missing_fields_rejected(self):
        for args in (("", "e", "y", "u"), ("h", "", "y", "u"), ("h", "e", "y", "")):
            self.assertFalse(ELB.put(*args)["ok"], args)

    def test_ledger_is_capped(self):
        prev = ELB.MAX_ITEMS
        ELB.MAX_ITEMS = 5
        self.addCleanup(lambda: setattr(ELB, "MAX_ITEMS", prev))
        for i in range(12):
            ELB.put(f"h{i}", "e", "y", "u1")
        self.assertLessEqual(len(self.sv.reports[ELB.LABEL_KIND]["items"]), 5)


class ReadTest(Base):
    def test_for_content_scopes_to_that_hash(self):
        ELB.put("h1", "삼성전자", "y", "u1")
        ELB.put("h2", "엘지", "n", "u1")
        got = ELB.for_content("h1", "u1")["labels"]
        self.assertEqual(list(got), ["삼성전자"])
        self.assertEqual(got["삼성전자"]["mine"], "y")

    def test_other_reviewers_vote_is_not_mine(self):
        ELB.put("h1", "삼성전자", "y", "u1")
        got = ELB.for_content("h1", "u2")["labels"]
        self.assertEqual(got["삼성전자"]["mine"], "")     # 남의 표가 내 표로 보이면 안 된다
        self.assertEqual(got["삼성전자"]["yes"], 1)

    def test_entity_name_with_pipe_is_scoped_correctly(self):
        """키가 hash|entity 라 이름에 | 가 있어도 콘텐츠 경계가 새면 안 된다."""
        ELB.put("h1", "A|B", "y", "u1")
        self.assertIn("A|B", ELB.for_content("h1", "u1")["labels"])
        self.assertEqual(ELB.for_content("h", "u1")["labels"], {})


class ExportTest(Base):
    def test_summary_counts_agreement(self):
        ELB.put("h1", "a", "y", "u1"); ELB.put("h1", "a", "y", "u2")   # 합의(있음)
        ELB.put("h1", "b", "n", "u1")                                   # 합의(없음)
        ELB.put("h1", "c", "y", "u1"); ELB.put("h1", "c", "n", "u2")   # 갈림
        s = ELB.summary()
        self.assertEqual((s["agreed_yes"], s["agreed_no"], s["split"]), (1, 1, 1))
        self.assertEqual(s["reviewers"], 2)

    def test_export_drops_split_samples(self):
        """의견이 갈린 표본은 정답으로 쓸 수 없다."""
        ELB.put("h1", "a", "y", "u1")
        ELB.put("h1", "c", "y", "u1"); ELB.put("h1", "c", "n", "u2")
        rows = ELB.export_rows()
        self.assertEqual([r["entity"] for r in rows], ["a"])
        self.assertEqual(rows[0]["label"], 1)


class RouteTest(unittest.TestCase):
    def test_registered_and_login_gated(self):
        from prism import serve
        self.assertIn("/entity-labels", serve._GET_ORDER)
        self.assertIn("/entity-label", serve._POST_ORDER)
        self.assertIsNone(serve._menu_for_path("/entity-label"))   # 메뉴 권한과 무관(전 검수자)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "prism", "serve.py"), encoding="utf-8") as f:
            self.assertIn('@_post_route("/entity-label", gate="login")', f.read())


class MarkupTest(unittest.TestCase):
    def test_label_ui_in_low_confidence_area(self):
        from prism import page
        self.assertIn("이 엔티티가 콘텐츠와 관련 있나요?", page.PAGE)
        self.assertIn("setEntLabel", page.PAGE)
        self.assertIn("entLowOpen && entsLow().length", page.PAGE)   # 경계선 구간에만 노출


if __name__ == "__main__":
    unittest.main()
