"""전체 감사(2026-07-14) 정합성 수정 회귀.

- llm._parse_json: dict 강제([{…}] 복구 · 비객체는 ParseError)
- store.rename_reviewer: assignments·board 키 이관
- store.remove_content: assignments·assignment_cfg 연쇄 삭제
- usermeta._parse_ts: aware/naive 혼재 정렬 안전 + 조인 키 네임스페이스 분리
"""
import os
import tempfile
import unittest


class TestParseJsonDictEnforce(unittest.TestCase):
    def test_array_wrapped_object_recovers_inner_dict(self):
        from prism.llm import _parse_json
        self.assertEqual(_parse_json('[{"a": 1}]'), {"a": 1})

    def test_non_object_json_raises_parse_error(self):
        from prism.llm import _parse_json, ParseError
        for bad in ('"문자열"', "[1, 2, 3]", "null", "42"):
            with self.assertRaises(ParseError, msg=bad):
                _parse_json(bad)

    def test_plain_object_still_ok(self):
        from prism.llm import _parse_json
        self.assertEqual(_parse_json('{"k": "v"}'), {"k": "v"})


class TestStoreCascades(unittest.TestCase):
    def setUp(self):
        from prism.store import Store
        self.db = os.path.join(tempfile.mkdtemp(), "t.db")
        self.st = Store(self.db)

    def test_rename_reviewer_moves_assignments_and_board(self):
        c = self.st._conn()
        self.st.set_reviewer("김검수")
        c.execute("INSERT INTO assignments(content_hash,reviewer,team,ts) VALUES('h1','김검수','',1)")
        c.execute("INSERT INTO board(team,kind,title,body,reviewer,status,ts) "
                  "VALUES('','제안','t','b','김검수','open',1)")
        c.commit()
        r = self.st.rename_reviewer("김검수", "박검수")
        self.assertTrue(r["ok"])
        self.assertEqual(c.execute("SELECT reviewer FROM assignments WHERE content_hash='h1'").fetchone()[0],
                         "박검수")
        self.assertEqual(c.execute("SELECT reviewer FROM board WHERE title='t'").fetchone()[0], "박검수")

    def test_remove_content_deletes_assignments(self):
        c = self.st._conn()
        c.execute("INSERT INTO results(content_hash,payload,created_at) VALUES('h2','{}',1)")
        c.execute("INSERT INTO assignments(content_hash,reviewer,team,ts) VALUES('h2','김검수','',1)")
        c.execute("INSERT INTO assignment_cfg(content_hash,team,min_reviewers) VALUES('h2','',2)")
        c.commit()
        self.assertTrue(self.st.remove_content("h2"))
        self.assertIsNone(c.execute("SELECT 1 FROM assignments WHERE content_hash='h2'").fetchone())
        self.assertIsNone(c.execute("SELECT 1 FROM assignment_cfg WHERE content_hash='h2'").fetchone())


class TestUsermetaTimeAndJoin(unittest.TestCase):
    def test_parse_ts_mixed_aware_naive_sortable(self):
        from prism.usermeta import _parse_ts
        vals = [_parse_ts(1700000000), _parse_ts("2026-07-14T09:00:00Z"),
                _parse_ts("2026-07-14T18:30:00")]
        self.assertTrue(all(v is not None and v.tzinfo is None for v in vals))
        sorted(vals)  # aware/naive 혼재였다면 TypeError

    def test_join_key_namespaces_do_not_clobber(self):
        import json
        from prism.usermeta import build_from_logs
        d = tempfile.mkdtemp()
        rp, lp = os.path.join(d, "r.jsonl"), os.path.join(d, "l.jsonl")
        rows = []
        for i in range(4):
            rows.append({"content_ref": {"title": f"제목{i}", "displayServiceName": "뉴스",
                                          # 행 3의 외부 id 가 "1"(행 1의 인덱스 키와 충돌 후보)
                                          **({"id": "1"} if i == 3 else {})},
                         "item_meta": {"summary": f"s{i}", "intent": ["정보 획득"],
                                       "content_category": ["News"], "entities": [f"e{i}"]},
                         "quality_meta": {"finalGrade": "G"}})
        with open(rp, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
        with open(lp, "w", encoding="utf-8") as f:
            f.write(json.dumps({"user_id": "u1", "content_id": "1", "event": "click"}))
        out = build_from_logs(rp, lp)
        u = out["users"][0]
        # 계약: content_id=추출 순서 → 인덱스 1(제목1)에 조인돼야 함(외부 id "1"=행 3 아님)
        self.assertIn("e1", json.dumps(u, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
