"""일괄 실행의 학습 반영 회차(초안 버전) 조회는 배치당 1회 — 건마다 events 재조회 방지.

배경(감사 2026-08-04): _batch_seq_cached 는 '일괄 실행이 건마다 events 전체를 재조회하지
않게' 만든 캐시인데, 배치 루프의 store_save 가 건마다 _agg_bump() 로 전역 캐시 버전을
올려 다음 건의 조회가 항상 미스났다(자기무효화). supabase 는 batch_seq 가 events 를
limit=10000 으로 GET 하므로 200건 배치 = 원격 GET 200회였다. 수정: rerun_all 이 시작 시
1회 조회한 값을 batch_seq 인자로 건별 전달하고, run_pipeline 은 주입값이 있으면 캐시를
다시 묻지 않는다.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import json as _j
import os
import sys
import tempfile
import time as _t
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeLLM:
    def __init__(self, model, mock=True):
        self.model = model
        self.mock = mock


class BatchSeqBase(unittest.TestCase):
    def _serve_with_rows(self, n):
        from prism import serve
        from prism.store import Store, content_hash
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        serve._INGEST_STATE.clear()
        self.addCleanup(serve._INGEST_STATE.clear)
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)
        c = st._conn()
        for i in range(n):
            content = {"displayServiceName": "뉴스", "title": "t%d" % i, "subtitle": "", "body": "b%d" % i}
            ch = content_hash(content)
            payload = {"quality_meta": {"finalGrade": "G"}, "item_meta": {"summary": "s"},
                       "trace": {"model": "m"}, "content_ref": dict(content)}
            c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,payload,created_at) "
                      "VALUES(?,?,?,?,?,?)", (ch, "뉴스", "t%d" % i, "G", _j.dumps(payload), _t.time() + i))
        c.commit()
        return serve

    def _isolate_cfg(self, payload):
        from prism import config as C
        p = os.path.join(tempfile.mkdtemp(), "config.json")
        open(p, "w", encoding="utf-8").write(_j.dumps(payload))
        orig = C.DEFAULT_CONFIG_PATH
        C.DEFAULT_CONFIG_PATH = p
        self.addCleanup(lambda: setattr(C, "DEFAULT_CONFIG_PATH", orig))

    def _count_batch_seq(self, serve, value=5):
        count = {"n": 0}
        orig = serve._batch_seq_cached

        def fake(team):
            count["n"] += 1
            return value
        serve._batch_seq_cached = fake
        self.addCleanup(lambda: setattr(serve, "_batch_seq_cached", orig))
        return count


class TestRerunAllQueriesOnce(BatchSeqBase):
    def test_batch_queries_seq_once_and_passes_to_items(self):
        serve = self._serve_with_rows(4)
        self._isolate_cfg({})
        count = self._count_batch_seq(serve, value=5)
        kws = []
        orig = serve.rerun_content
        serve.rerun_content = lambda ch, model, team=None, row=None, **kw: (
            kws.append(kw) or {"output": {"trace": {"cost_usd": 0.0}}})
        self.addCleanup(lambda: setattr(serve, "rerun_content", orig))
        r = serve.rerun_all("m2", scope="all")
        self.assertEqual(r["done"], 4)
        self.assertEqual(count["n"], 1)                     # 배치당 1회(건마다 재조회 없음)
        self.assertEqual([kw.get("batch_seq") for kw in kws], [5, 5, 5, 5])


class TestRunPipelineHonorsInjectedSeq(BatchSeqBase):
    def _fake_extract(self, serve):
        o_ext, o_make = serve.PIPE.extract, serve.make_text_llm
        serve.PIPE.extract = lambda content, llm, legal=False: {
            "item_meta": {"summary": "s"}, "quality_meta": {"finalGrade": "G"}, "trace": {}}
        serve.make_text_llm = lambda cfg, mock: _FakeLLM("m", mock=True)
        self.addCleanup(lambda: (setattr(serve.PIPE, "extract", o_ext),
                                 setattr(serve, "make_text_llm", o_make)))

    def test_injected_seq_skips_cache_and_stamps_version(self):
        serve = self._serve_with_rows(0)
        self._isolate_cfg({})
        count = self._count_batch_seq(serve, value=9)
        self._fake_extract(serve)
        res = serve.run_pipeline({"displayServiceName": "뉴스", "title": "t", "subtitle": "", "body": "b"},
                                 mock=True, persist=False, batch_seq=5)
        self.assertEqual(res["output"]["trace"]["version"], 6)   # 주입값 + 1
        self.assertEqual(count["n"], 0)                          # 캐시(=스토어) 미조회

    def test_without_injection_falls_back_to_cache(self):
        serve = self._serve_with_rows(0)
        self._isolate_cfg({})
        count = self._count_batch_seq(serve, value=9)
        self._fake_extract(serve)
        res = serve.run_pipeline({"displayServiceName": "뉴스", "title": "t", "subtitle": "", "body": "b"},
                                 mock=True, persist=False)
        self.assertEqual(res["output"]["trace"]["version"], 10)  # 종전 동작 유지
        self.assertEqual(count["n"], 1)


if __name__ == "__main__":
    unittest.main()
