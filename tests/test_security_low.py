"""낮음 심각도 보안 회귀 테스트(2026-07-15 감사).

- CSV 수식 인젝션: 셀 선두 = + - @ 를 선행 작은따옴표로 중화.
- PostgREST 해시 형식 검증(_HASH_RE): 16진수 16자만 필터 통과(심층방어).
- 비밀 파일 원자적 0600 기록(_write_private).
- 인입 아웃바운드 응답 크기 상한(_FETCH_MAX) 상수 존재.

실행: python3 -m pytest tests/test_security_low.py -q
"""
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCsvFormulaInjection(unittest.TestCase):
    def test_leading_formula_chars_neutralized(self):
        from prism import serve as SV
        row = {"item_meta": {"summary": "=HYPERLINK(\"http://evil\")", "entities": ["@SUM(1)"],
                             "intent": [], "content_category": []},
               "quality_meta": {"finalGrade": "G", "reasons": ["-1+1"]},
               "content_ref": {"title": "+cmd", "displayServiceName": "뉴스"}}
        orig = SV.results_rows
        SV.results_rows = lambda limit=5000, team=None: [row]
        self.addCleanup(lambda: setattr(SV, "results_rows", orig))
        csv = SV.build_results_csv().decode("utf-8")
        self.assertIn('"\'=HYPERLINK', csv)          # 선행 ' 로 수식 무력화
        self.assertIn('"\'@SUM(1)"', csv)
        self.assertIn('"\'-1+1"', csv)
        self.assertNotIn('"=HYPERLINK', csv)         # 원본 수식 시작 형태는 없음
        self.assertIn('"\'+cmd"', csv)               # 제목 열이 content_ref 에서 채워짐(+ 는 수식 중화) · 구버그(content 키)면 공란
        self.assertIn("뉴스", csv)                    # 서비스 열도 content_ref 에서 채워짐


class TestHashFormatValidation(unittest.TestCase):
    def test_hash_regex(self):
        from prism.supastore import _HASH_RE
        self.assertTrue(_HASH_RE.match("9a58a1a78f349398"))       # 정상 16진수 16자
        for bad in ("9a58a1a78f349398,x", "9a58a1a78f34939", "GG58a1a78f349398",
                    "gold:bad:9f35c03daf6a2c77", "9a58a1a78f349398)", ""):
            self.assertFalse(_HASH_RE.match(bad), bad)


class TestWritePrivate(unittest.TestCase):
    def test_creates_0600(self):
        from prism import serve as SV
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "k")
            SV._write_private(p, "SECRET")
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
            self.assertEqual(open(p).read(), "SECRET")
            # 기존 느슨한 권한 파일도 강제 0600
            os.chmod(p, 0o644)
            SV._write_private(p, "SECRET2")
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
            self.assertEqual(open(p).read(), "SECRET2")


class TestFetchSizeCap(unittest.TestCase):
    def test_cap_constant_present(self):
        from prism import serve as SV
        self.assertTrue(SV._FETCH_MAX > 0)
        self.assertLessEqual(SV._FETCH_MAX, 64 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
