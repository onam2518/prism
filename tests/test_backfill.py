"""원문 링크 백필(/backfill-urls) 회귀: source_url 만 갱신 · 초안/판정/적재 시각 불변 · 매칭 규칙.

실행: python3 -m pytest tests/ -q
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestStoreSetSourceUrl(unittest.TestCase):
    def test_sqlite_updates_only_source_url(self):
        from prism.store import Store, content_hash
        with tempfile.TemporaryDirectory() as d:
            st = Store(os.path.join(d, "t.db"))
            content = {"displayServiceName": "뉴스", "title": "알파", "subtitle": "", "body": "본문"}
            out = {"content_ref": {**content, "source_url": "", "body_hash": content_hash(content)},
                   "item_meta": {"summary": "요약"}, "quality_meta": {"finalGrade": "G"}, "trace": {}}
            st.save_many([(content, out)], run_id="t")
            ch = content_hash(content)
            before = st._conn().execute("SELECT created_at FROM results WHERE content_hash=?", (ch,)).fetchone()[0]

            self.assertTrue(st.set_source_url(ch, "https://n.example/a"))
            row = st.recent(10)[0]
            self.assertEqual(row["content_ref"]["source_url"], "https://n.example/a")
            self.assertEqual(row["item_meta"], {"summary": "요약"})           # 초안 불변
            self.assertEqual(row["quality_meta"], {"finalGrade": "G"})        # 판정 메타 불변
            after = st._conn().execute("SELECT created_at FROM results WHERE content_hash=?", (ch,)).fetchone()[0]
            self.assertEqual(before, after)                                   # 적재 시각 불변
            self.assertFalse(st.set_source_url("없는해시00000000", "https://x"))


class _FakeStore:
    """serve.backfill_urls 매칭 규칙 검증용: recent 색인 + set_source_url 호출 기록."""
    def __init__(self):
        self.calls = []
        self.rows = [
            {"content_ref": {"title": "알파", "body_hash": "a" * 16, "source_url": ""}},
            {"content_ref": {"title": "베타", "body_hash": "b" * 16, "source_url": "https://old.example/1"}},
            {"content_ref": {"title": "중복", "body_hash": "c" * 16, "source_url": ""}},
            {"content_ref": {"title": "중복", "body_hash": "d" * 16, "source_url": ""}},
        ]

    def recent(self, limit=5000, team=None):
        return self.rows

    def set_source_url(self, content_hash, url, team=None):
        self.calls.append((content_hash, url))
        return True


class TestBackfillUrls(unittest.TestCase):
    def _run(self, csv_text, name="map.csv"):
        import prism.serve as SV
        fake = _FakeStore()
        orig = SV.get_store
        SV.get_store = lambda: fake
        try:
            return SV.backfill_urls(csv_text.encode("utf-8"), name), fake
        finally:
            SV.get_store = orig

    def test_title_match_rules(self):
        r, fake = self._run(
            "제목,링크\n"
            "알파,https://n.example/alpha\n"        # 제목 1건 일치 → 갱신
            "베타,https://old.example/1\n"          # 이미 같은 값 → 변화 없음
            "중복,https://n.example/dup\n"          # 동일 제목 다건 → 건너뜀
            "없음,https://n.example/none\n"         # 미적재 → 미매칭
            "알파,ftp://bad\n")                     # http(s) 아님 → 형식 오류
        self.assertTrue(r["ok"])
        self.assertEqual((r["updated"], r["unchanged"], r["ambiguous"], r["noMatch"], r["badUrl"]),
                         (1, 1, 1, 1, 1))
        self.assertEqual(fake.calls, [("a" * 16, "https://n.example/alpha")])
        self.assertTrue(any("중복" in m for m in r["misses"]))

    def test_hash_column_priority(self):
        r, fake = self._run("해시,url\n" + "b" * 16 + ",https://new.example/2\n")
        self.assertEqual(r["updated"], 1)
        self.assertEqual(fake.calls, [("b" * 16, "https://new.example/2")])

    def test_missing_required_columns(self):
        r, _ = self._run("제목,본문\n알파,x\n")     # URL 컬럼 없음 → 안내 에러
        self.assertIn("error", r)


if __name__ == "__main__":
    unittest.main()
