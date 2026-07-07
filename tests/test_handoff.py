"""핸드오프 번들: 노하우 결속(골든 ← 사람 사유·교정) · 백로그 · manifest 건수·증분.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
구조 근거는 TESTING.md 참조.
"""
import hashlib
import io
import json
import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class HandoffBase(unittest.TestCase):
    def _with_store(self):
        import tempfile
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve, st

    def _put_reviewed(self, st, title, verdicts, cats=("Sports",)):
        """YELLOW 결과 + 검수자 (판정, 노트) 삽입 → content_hash 반환."""
        import json as _j
        import time as _t
        from prism.store import content_hash
        content = {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "본문 " + title}
        ch = content_hash(content)
        im = {"summary": title, "entities": [], "intent": [], "content_category": list(cats)}
        payload = {"quality_meta": {"review": "yellow", "finalGrade": "G", "reasons": []},
                   "item_meta": im, "content_ref": dict(content)}
        c = st._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,reasons,item_meta,payload,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (ch, "뉴스", title, "G", "[]", _j.dumps(im), _j.dumps(payload), _t.time()))
        c.commit()
        now = _t.time()
        for i, (rv, v, note) in enumerate(verdicts):
            st.save_feedback(ch, "뉴스", title, v, "review", note, now + i, reviewer=rv)
        return ch


class TestKnowhow(HandoffBase):
    def test_knowhow_binds_reasons_and_revisions(self):
        serve, st = self._with_store()
        ch = self._put_reviewed(st, "사유 콘텐츠",
                                [("A", "good", "리드문이 사실 요약이라 정확"), ("B", "good", "")])
        st.log_patch(ch, "B", "summary", {"summary": "전"}, {"summary": "후"})
        st.save_reap(ch, "A", {"remember": "r", "explain": "핵심 근거", "ask": "", "plan": "다음엔 개체 확인"})
        # 사유 없는 수동 골든 → 노하우 계층(knowhow.jsonl)에서 제외돼야 함
        st.register_golden(None, [{"content": {"displayServiceName": "뉴스", "title": "수동", "subtitle": "", "body": "b"},
                                   "expected": {"finalGrade": "G", "content_category": ["Sports"]}}],
                           replace=True, source="manual")
        serve.build_golden_from_reviews(None)
        fname, text = serve.learn_export("knowhow", None)
        self.assertEqual(fname, "prism_knowhow.jsonl")
        rows = [json.loads(l) for l in text.split("\n") if l]
        self.assertEqual(len(rows), 1)                                   # 사유 있는 골든만
        r = rows[0]
        self.assertEqual((r["hash"], r["consensus"]), (ch, "good"))
        self.assertIn("리드문이 사실 요약이라 정확", [o["note"] for o in r["opinions"]])
        self.assertEqual(r["revisions"][0]["element"], "summary")        # 교정 전/후 결속
        self.assertEqual(r["rationales"][0]["explain"], "핵심 근거")     # REAP 사유 결속
        d = serve.learn_data(None)
        self.assertEqual((d["knowhow"]["n"], d["extractable"]["knowhow"]), (1, 1))
        self.assertEqual(d["knowhow"]["coverage"], 0.5)                  # 골든 2건 중 1건 결속


class TestBacklog(HandoffBase):
    def test_backlog_collects_split_and_class_gap(self):
        serve, st = self._with_store()
        self._put_reviewed(st, "갈림 콘텐츠", [("A", "good", "정확"), ("B", "bad", "리드문 오류")])
        from prism import learnops as LO
        rows = LO.backlog_rows(None)
        splits = [r for r in rows if r["type"] == "split"]
        self.assertEqual(len(splits), 1)                                 # 의견 갈림 = 원시 의견 보존
        self.assertEqual({o["verdict"] for o in splits[0]["opinions"]}, {"good", "bad"})
        self.assertIn("리드문 오류", [o["note"] for o in splits[0]["opinions"]])
        self.assertTrue(any(r["type"] == "class_gap" for r in rows))     # 커버리지 부족 클래스 동봉


class TestHandoffBundle(HandoffBase):
    def test_bundle_manifest_counts_and_increment(self):
        serve, st = self._with_store()
        ch = self._put_reviewed(st, "합의 콘텐츠", [("A", "good", "근거 노트"), ("B", "good", "")])
        st.log_patch(ch, "A", "summary", {"summary": "전"}, {"summary": "후"})
        serve.build_golden_from_reviews(None)
        fname, blob = serve.handoff_bundle(None)
        self.assertTrue(fname.startswith("prism_handoff_01_"), fname)
        z = zipfile.ZipFile(io.BytesIO(blob))
        names = set(z.namelist())
        for need in ("manifest.json", "prism_sft.jsonl", "prism_dpo.jsonl", "prism_knowhow.jsonl",
                     "backlog.jsonl", "finetune_spec.md", "dictionaries.json", "data_card.md",
                     "eval_report.json"):
            self.assertIn(need, names)
        man = json.loads(z.read("manifest.json"))
        self.assertEqual(man["seq"], 1)
        self.assertIsNone(man["prev"])
        for member, key in (("prism_sft.jsonl", "sft"), ("prism_dpo.jsonl", "dpo"),
                            ("prism_knowhow.jsonl", "knowhow"), ("backlog.jsonl", "backlog")):
            n = len([l for l in z.read(member).decode("utf-8").split("\n") if l.strip()])
            self.assertEqual(man["counts"][key], n, member)              # manifest 건수 = 실제 줄 수
        self.assertEqual(man["files"]["prism_sft.jsonl"]["sha256"],
                         hashlib.sha256(z.read("prism_sft.jsonl")).hexdigest())
        # 2차 발행: seq 증가 + 직전 참조 · 신규 데이터 없음 → 증분 전부 0
        fname2, blob2 = serve.handoff_bundle(None)
        man2 = json.loads(zipfile.ZipFile(io.BytesIO(blob2)).read("manifest.json"))
        self.assertEqual((man2["seq"], man2["prev"]["seq"]), (2, 1))
        self.assertTrue(all(v == 0 for v in man2["delta"].values()))


if __name__ == "__main__":
    unittest.main()
