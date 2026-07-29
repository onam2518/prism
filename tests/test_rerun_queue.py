"""개별 재실행도 실행 큐에 남는다.

종전에는 일괄·선택 실행(rerun_all)만 큐에 등록되고, 표의 '재실행' 버튼과 실패 콘텐츠
'재실행' 은 흔적이 없었다 — 눌렀는데 돌긴 한 건지 확인할 방법이 없었다.
배치 안에서 호출될 때는 배치 잡이 이미 열려 있으므로 건마다 잡을 만들지 않는다.

실행: python3 -m pytest tests/ -q
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class RerunQueueBase(unittest.TestCase):
    def _serve(self):
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._INGEST_STATE.clear()
        self.addCleanup(serve._INGEST_STATE.clear)
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)
        return serve

    def _put(self, serve, title="재실행 대상"):
        from prism.store import content_hash
        ref = {"displayServiceName": "티스토리", "title": title, "subtitle": "", "body": "본문"}
        h = content_hash(ref)
        payload = {"content_ref": dict(ref, body_hash=h),
                   "quality_meta": {"review": "auto", "finalGrade": "G"},
                   "trace": {"model": "m0"}, "item_meta": {"summary": "s"}}
        c = serve._STORE._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,payload,created_at) "
                  "VALUES(?,?,?,?,?,?)", (h, "티스토리", title, "G", json.dumps(payload), time.time()))
        c.commit()
        return h

    def _fake_pipeline(self, serve, error=""):
        # rerun_content 는 runops 네임스페이스의 run_pipeline 을 부른다 —
        # serve 쪽 재수출을 패치하면 실제 파이프라인이 그대로 돌아 테스트가 헛돈다.
        import prism.runops as RN
        orig = RN.run_pipeline
        out = {"error": error} if error else {
            "output": {"content_ref": {}, "quality_meta": {"finalGrade": "G"},
                       "item_meta": {"summary": "새 초안"}, "trace": {"model": "m1", "cost_usd": 0.002}}}
        RN.run_pipeline = lambda *a, **k: out
        self.addCleanup(lambda: setattr(RN, "run_pipeline", orig))

    def _jobs(self, serve):
        return serve.ingest_status()["jobs"]


class TestSingleRerunQueued(RerunQueueBase):
    def test_job_registered_with_progress_and_hash(self):
        serve = self._serve()
        h = self._put(serve, "페루 - 푸노")
        self._fake_pipeline(serve)
        serve.rerun_content(h, "m1")
        jobs = [j for j in self._jobs(serve) if j["kind"] == "개별 재실행"]
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["name"], "페루 - 푸노")           # 큐에서 무엇을 돌렸는지 보인다
        self.assertEqual((j["total"], j["done"]), (1, 1))
        self.assertTrue(j["last_ok"])
        self.assertFalse(j["running"])                      # 끝난 잡은 진행 중으로 남지 않는다
        self.assertEqual(j["hashes"], [h])                  # 작업 클릭 -> 이 콘텐츠 보기

    def test_failure_is_recorded_not_silent(self):
        serve = self._serve()
        h = self._put(serve)
        self._fake_pipeline(serve, error="모델 호출 불가")
        serve.rerun_content(h, "m1")
        j = [x for x in self._jobs(serve) if x["kind"] == "개별 재실행"][0]
        self.assertFalse(j["last_ok"])
        self.assertIn("모델 호출 불가", j["last_msg"])

    def test_blocked_attempt_does_not_pollute_queue(self):
        """퀘스트에 막혀 실행 자체를 안 했으면 큐에 남기지 않는다."""
        serve = self._serve()
        h = self._put(serve)
        orig = serve.quest_active
        serve.quest_active = lambda: True
        self.addCleanup(lambda: setattr(serve, "quest_active", orig))
        r = serve.rerun_content(h, "m1")
        self.assertIn("퀘스트", r.get("error", ""))
        self.assertEqual([x for x in self._jobs(serve) if x["kind"] == "개별 재실행"], [])

    def test_missing_content_does_not_register(self):
        serve = self._serve()
        self._fake_pipeline(serve)
        serve.rerun_content("없는해시", "m1")
        self.assertEqual([x for x in self._jobs(serve) if x["kind"] == "개별 재실행"], [])


class TestBatchDoesNotDoubleRegister(RerunQueueBase):
    def test_selected_rerun_makes_one_job_not_one_per_item(self):
        serve = self._serve()
        hs = [self._put(serve, f"콘텐츠 {i}") for i in range(3)]
        self._fake_pipeline(serve)
        serve.rerun_all("m1", None, hashes=hs)
        kinds = [j["kind"] for j in self._jobs(serve)]
        self.assertEqual(kinds.count("선택 실행"), 1)
        self.assertEqual(kinds.count("개별 재실행"), 0)      # 건마다 잡이 쌓이지 않는다

    def test_batch_job_counts_all_items(self):
        serve = self._serve()
        hs = [self._put(serve, f"콘텐츠 {i}") for i in range(3)]
        self._fake_pipeline(serve)
        serve.rerun_all("m1", None, hashes=hs)
        j = [x for x in self._jobs(serve) if x["kind"] == "선택 실행"][0]
        self.assertEqual((j["total"], j["done"]), (3, 3))


if __name__ == "__main__":
    unittest.main()
