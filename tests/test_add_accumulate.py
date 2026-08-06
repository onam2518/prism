"""STEP 1 추가의 신규/기존 구분과 인입 경로 무관 미실행 합산 실행.

배경(운영 2026-08-05): 같은 200건 파일을 다시 올리자 ① '200건 추가됨'으로 표기돼 신규가
없다는 사실이 보이지 않았고 ② 재추가 upsert 가 기존 실행 결과를 빈 메타로 덮어써 미실행으로
되돌렸으며 ③ STEP 2 가 그 200건을 다시 실행해 LLM 비용이 이중 지출됐다. 또 '미실행만'
실행이 회당 200건이라 엑셀 2개(400건)를 나눠 올리면 한 번에 돌지 않아 파일 단위처럼 보였다.

계약:
· add_contents: 이미 저장된 해시는 건드리지 않고 existing 으로만 집계(added=신규만)
· save_dedup(sqlite): 빈 결과가 기존 실행 결과를 덮어쓰지 않는다(재추가 가드)
· run_batch(추출): 이미 실행 완료된 기존 행은 건너뛴다(skipped_done)
· /rerun-all scope=pending: PENDING_MAX(2000) — 엑셀·수동 등 인입 경로 무관 전량 합산 실행

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import json as _j
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeLLM:
    def __init__(self, model, mock=True):
        self.model = model
        self.mock = mock


def _ok_out(model="m1"):
    return {"item_meta": {"summary": "s"}, "quality_meta": {"finalGrade": "G", "review": "auto"},
            "trace": {"model": model, "cost_usd": 0.0}}


def _content(i):
    return {"displayServiceName": "뉴스", "title": f"기사 {i}", "body": f"본문 {i}"}


class AccumulateBase(unittest.TestCase):
    def _serve(self):
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._INGEST_STATE.clear()
        orig_mock = serve.Handler.server_mock
        serve.Handler.server_mock = True                  # 외부 부수효과(사전 보강 등) 차단
        self.addCleanup(serve._INGEST_STATE.clear)
        self.addCleanup(lambda: setattr(serve.Handler, "server_mock", orig_mock))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)
        self._isolate_cfg({})
        orig_llm = serve.make_text_llm
        serve.make_text_llm = lambda cfg, m: _FakeLLM("m1", mock=True)
        self.addCleanup(lambda: setattr(serve, "make_text_llm", orig_llm))
        return serve

    def _isolate_cfg(self, payload):
        from prism import config as C
        p = os.path.join(tempfile.mkdtemp(), "config.json")
        open(p, "w", encoding="utf-8").write(_j.dumps(payload))
        orig = C.DEFAULT_CONFIG_PATH
        C.DEFAULT_CONFIG_PATH = p
        self.addCleanup(lambda: setattr(C, "DEFAULT_CONFIG_PATH", orig))

    def _stub_extract(self, serve):
        calls = []

        def fake(content, llm, legal=False):
            calls.append(content.get("title"))
            return _j.loads(_j.dumps(_ok_out()))
        orig = serve.PIPE.extract
        serve.PIPE.extract = fake
        self.addCleanup(lambda: setattr(serve.PIPE, "extract", orig))
        return calls

    def _pending_titles(self, serve):
        return [r.get("content_ref", {}).get("title") for r in serve.results_rows()
                if serve._is_pending_row(r)]


class TestAddDedup(AccumulateBase):
    def test_readd_reports_existing_and_keeps_results(self):
        serve = self._serve()
        self._stub_extract(serve)
        r1 = serve.add_contents([_content(1), _content(2)], source="배치")
        self.assertEqual((r1["added"], r1["existing"]), (2, 0))
        run = serve.rerun_all("m1", scope="pending")
        self.assertEqual((run["done"], run["failed"]), (2, 0))
        # 같은 파일 재업로드 + 신규 1건: 신규만 추가 · 기존은 유지로만 집계
        r2 = serve.add_contents([_content(1), _content(2), _content(3)], source="배치")
        self.assertEqual((r2["added"], r2["existing"]), (1, 2))
        # 기존 실행 결과가 미실행으로 되돌아가지 않는다(이중 과금의 뿌리)
        self.assertEqual(self._pending_titles(serve), ["기사 3"])
        grades = {r["content_ref"]["title"]: (r.get("quality_meta") or {}).get("finalGrade")
                  for r in serve.results_rows()}
        self.assertEqual(grades["기사 1"], "G")
        self.assertEqual(grades["기사 2"], "G")
        # 실행 큐 메시지에도 신규/기존이 갈라져 남는다
        msgs = [j.get("last_msg", "") for j in serve._INGEST_STATE.values()
                if j.get("kind") == "콘텐츠 추가"]
        self.assertTrue(any("신규 1건 추가 · 기존 2건 유지" in m for m in msgs), msgs)

    def test_all_existing_says_no_new(self):
        serve = self._serve()
        serve.add_contents([_content(1)], source="배치")
        r = serve.add_contents([_content(1)], source="배치")
        self.assertEqual((r["added"], r["existing"]), (0, 1))
        self.assertTrue(r["pending"])                     # 추가 모드 응답 계약 유지(UI 분기)

    def test_save_dedup_guard_blocks_empty_overwrite(self):
        """저장 계층 가드: add 경로가 아니어도 빈 결과는 실행 결과를 덮어쓰지 못한다."""
        from prism.store import Store, content_hash
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        c = _content(1)
        st.save_dedup([(c, _ok_out())], "r1")
        r = st.save_dedup([(c, {"content_ref": {}, "quality_meta": {}, "item_meta": {}, "trace": {}})], "r2")
        self.assertEqual(r["skipped"], 1)
        self.assertEqual(r["updated"], 0)
        self.assertTrue(st.existing_hashes([content_hash(c)])[content_hash(c)])   # 여전히 '실행됨'


class TestStepTwoMergesAllPaths(AccumulateBase):
    CSV = ("콘텐츠 그룹,제목,본문\n"
           "뉴스,엑셀 하나,본문a\n"
           "뉴스,엑셀 둘,본문b\n").encode("utf-8")

    def test_excel_plus_manual_run_together(self):
        """엑셀(STEP 1) + 수동 1건 → 미실행 3건이 한 번의 '미실행만' 실행으로 전부 돈다."""
        serve = self._serve()
        calls = self._stub_extract(serve)
        r = serve.run_batch(self.CSV, "u.csv", add_only=True)
        self.assertEqual((r["added"], r.get("existing", 0)), (2, 0))
        serve.add_contents([_content(9)], source="단건")
        run = serve.rerun_all("m1", scope="pending")
        self.assertEqual((run["done"], run["failed"]), (3, 0))
        self.assertEqual(sorted(calls), ["기사 9", "엑셀 둘", "엑셀 하나"])
        self.assertEqual(self._pending_titles(serve), [])

    def test_route_uses_pending_max(self):
        """/rerun-all: 미실행만은 PENDING_MAX 로, 전체 재실행은 200 가드 유지."""
        from prism import serve, runops as RN
        seen = {}
        orig = serve.rerun_all
        serve.rerun_all = lambda *a, **k: (seen.update(k), {"ok": True})[1]
        self.addCleanup(lambda: setattr(serve, "rerun_all", orig))

        class FakeH:
            def _req_team(self):
                return None
        serve._p_rerun_all(FakeH(), _j.dumps({"scope": "pending"}).encode())
        self.assertEqual(seen["limit"], RN.PENDING_MAX)
        serve._p_rerun_all(FakeH(), _j.dumps({"scope": "all"}).encode())
        self.assertEqual(seen["limit"], 200)

    def test_pending_over_200_runs_in_one_batch(self):
        """엑셀 2개(합 210건) 시나리오: 미실행 210건이 한 번에 전부 실행된다(종전 200 상한)."""
        from prism import runops as RN
        serve = self._serve()
        calls = self._stub_extract(serve)
        serve.add_contents([_content(i) for i in range(105)], source="배치")
        serve.add_contents([_content(i) for i in range(105, 210)], source="배치")
        run = serve.rerun_all("m1", scope="pending", limit=RN.PENDING_MAX)
        self.assertEqual((run["done"], run["failed"]), (210, 0))
        self.assertEqual(len(calls), 210)


class TestBatchUploadAccumulate(AccumulateBase):
    CSV = ("콘텐츠 그룹,제목,본문\n"
           "뉴스,엑셀 하나,본문a\n"
           "뉴스,엑셀 둘,본문b\n").encode("utf-8")

    def test_extract_skips_already_run(self):
        """추출 실행 재업로드: 이미 실행 완료된 행은 건너뛴다(재과금 방지)."""
        serve = self._serve()
        calls = self._stub_extract(serve)
        r1 = serve.run_batch(self.CSV, "u.csv")
        self.assertEqual(r1["count"], 2)
        r2 = serve.run_batch(self.CSV, "u.csv")
        self.assertEqual(r2["count"], 0)
        self.assertEqual(r2["skipped_done"], 2)
        self.assertEqual(len(calls), 2)                   # 두 번째 업로드는 LLM 콜 0
        # 신규가 섞이면 신규만 실행된다
        csv3 = self.CSV + "뉴스,엑셀 셋,본문c\n".encode("utf-8")
        r3 = serve.run_batch(csv3, "u.csv")
        self.assertEqual((r3["count"], r3["skipped_done"]), (1, 2))

    def test_add_cap_reports_truncation(self):
        """추가 상한(BATCH_ADD_MAX)을 넘는 파일은 잘린 건수를 응답에 남긴다(조용한 절단 금지)."""
        from prism import runops as RN
        serve = self._serve()
        orig = RN.BATCH_ADD_MAX
        RN.BATCH_ADD_MAX = 2
        self.addCleanup(lambda: setattr(RN, "BATCH_ADD_MAX", orig))
        r = serve.run_batch(self.CSV + "뉴스,엑셀 셋,본문c\n".encode("utf-8"), "u.csv", add_only=True)
        self.assertEqual((r["added"], r["truncated"]), (2, 1))


if __name__ == "__main__":
    unittest.main()
