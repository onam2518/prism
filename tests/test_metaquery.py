"""콘텐츠 조회(메타베이스)·검수 지정(metaquery): 조회→지정 인입 계약.

배경(기획 2026-09-02): 데브 환경에 메타가 발행되기 시작해 인입 기본 경로가
자동 인입에서 "메타베이스 조회 → 검수 지정"으로 바뀐다. 지정은 발행 메타를
모델 초안으로 복사 인입한다(media_register 와 같은 이유로 add_contents 미사용).

계약:
· 조회 기본 소스는 스테이징(사내망 수집기가 /metaquery-stage 로 올린 행 · 2026-09-02 수집 환경 결정)
· source=metabase(서버 직접 호출)는 모의 모드에서 예시 행 · 실모드는 설정 필요 · 인입 여부(registered) 표시
· 스테이징은 검수 콘텐츠가 아니다: results 분리 · 선택 삭제 · TTL(7일) 자동 정리 · 지정은 register 그대로
· 지정 즉시 실행 완료(_is_pending_row 아님) · 발행 메타 보존 · 모델 호출 0건
· review=yellow 로 저장되어 검수 대기 큐(review_queue)에 잡힌다
· 같은 콘텐츠 재지정은 기존 불변(existing 집계) · purpose=eval 은 홀드아웃
· 라우트: GET /metaquery(admin) · POST /metaquery-search·register·stage·stage-delete(admin) + content 메뉴 게이트
· 자동 인입 스케줄러는 기본 비활성(PRISM_INGEST_AUTO=1 일 때만 기동)

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import json as _j
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class MetaqueryBase(unittest.TestCase):
    def _serve(self):
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._INGEST_STATE.clear()
        orig_mock = serve.Handler.server_mock
        serve.Handler.server_mock = True                  # 모의 조회 + 외부 부수효과 차단
        self.addCleanup(serve._INGEST_STATE.clear)
        self.addCleanup(lambda: setattr(serve.Handler, "server_mock", orig_mock))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)
        self._isolate_cfg({})
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
            return {}
        orig = serve.PIPE.extract
        serve.PIPE.extract = fake
        self.addCleanup(lambda: setattr(serve.PIPE, "extract", orig))
        return calls


class TestMetaquerySearch(MetaqueryBase):
    def test_mock_search_returns_rows_with_registered_flag(self):
        from prism import metaquery as MQ
        serve = self._serve()
        r = MQ.mq_search({"source": "metabase"})
        self.assertTrue(r["ok"])
        self.assertTrue(r["mock"])
        self.assertTrue(r["rows"])
        row = r["rows"][0]
        for k in ("hash", "service", "title", "grade", "registered"):
            self.assertIn(k, row)
        self.assertFalse(row["registered"])               # 아직 아무것도 인입 전

    def test_search_marks_already_registered(self):
        from prism import metaquery as MQ
        serve = self._serve()
        rows = MQ.mq_search({"source": "metabase"})["rows"]
        MQ.mq_register({"rows": rows[:1]})
        again = MQ.mq_search({"source": "metabase"})["rows"]
        self.assertTrue(again[0]["registered"])           # 재조회에서 인입됨 표시
        self.assertFalse(again[1]["registered"])

    def test_unconfigured_without_mock_is_guided_error(self):
        """실모드 + 미설정: 조용한 빈 목록이 아니라 설정 경로를 알려 주는 에러."""
        from prism import metaquery as MQ
        serve = self._serve()
        serve.Handler.server_mock = False
        r = MQ.mq_search({"source": "metabase"})
        self.assertFalse(r["ok"])
        self.assertIn("설정", r["error"])
        self.assertTrue(MQ.mq_search({})["ok"])             # 기본(스테이징)은 설정 없이도 빈 목록으로 동작


class TestMetaqueryRegister(MetaqueryBase):
    def test_register_is_done_not_pending_and_queued(self):
        """지정 즉시 실행 완료 · 발행 메타 보존 · review=yellow 로 검수 큐 편입 · 모델 호출 0."""
        from prism import metaquery as MQ
        serve = self._serve()
        calls = self._stub_extract(serve)
        rows = MQ.mq_search({"source": "metabase"})["rows"]
        r = MQ.mq_register({"rows": rows[:2]})
        self.assertEqual((r["ok"], r["added"], r["existing"]), (True, 2, 0))
        saved = serve.results_rows()
        self.assertEqual(len(saved), 2)
        for row in saved:
            self.assertFalse(serve._is_pending_row(row))
            qm = row.get("quality_meta") or {}
            self.assertEqual(qm.get("review"), "yellow")
            tr = row.get("trace") or {}
            self.assertTrue(str(tr.get("model", "")).startswith("dev:"))
            self.assertTrue(tr.get("source_id"))          # 원본 ID 보존
        self.assertEqual(calls, [])                       # 재추출 없음
        queue = serve._STORE.review_queue(limit=10)
        self.assertEqual(len(queue), 2)                   # 검수 대기 큐 계약
        run = serve.rerun_all("m1", scope="pending")      # 미실행 대상 아님(이중 과금 방지)
        self.assertEqual(run["done"], 0)

    def test_duplicate_register_keeps_existing(self):
        from prism import metaquery as MQ
        serve = self._serve()
        rows = MQ.mq_search({"source": "metabase"})["rows"]
        MQ.mq_register({"rows": rows[:1]})
        changed = _j.loads(_j.dumps(rows[0]))
        changed["grade"] = "R"                            # 재지정이 덮어쓰면 안 된다
        r = MQ.mq_register({"rows": [changed]})
        self.assertEqual((r["added"], r["existing"]), (0, 1))
        saved = serve.results_rows()
        self.assertEqual(len(saved), 1)
        self.assertEqual((saved[0].get("quality_meta") or {}).get("finalGrade"), "G")

    def test_purpose_eval_sets_holdout(self):
        from prism import metaquery as MQ
        serve = self._serve()
        rows = MQ.mq_search({"source": "metabase"})["rows"]
        r = MQ.mq_register({"rows": rows[:1], "purpose": "eval"})
        pm = serve._STORE.purpose_map()
        self.assertEqual(pm.get(r["hashes"][0]), "eval")

    def test_rejects_empty_rows_and_meta(self):
        from prism import metaquery as MQ
        serve = self._serve()
        self.assertFalse(MQ.mq_register({})["ok"])
        self.assertFalse(MQ.mq_register({"rows": [{"title": "", "body": ""}]})["ok"])
        bare = {"id": "x", "service": "뉴스", "title": "제목만", "body": "본문",
                "grade": "", "summary": "", "entities": "", "intent": "", "category": ""}
        self.assertFalse(MQ.mq_register({"rows": [bare]})["ok"])   # 발행 메타 전무 = 초안 아님
        self.assertEqual(serve.results_rows(), [])

    def test_source_url_scheme_whitelisted(self):
        from prism import metaquery as MQ
        serve = self._serve()
        row = MQ.mq_search({"source": "metabase"})["rows"][0]
        row["url"] = "javascript:alert(1)"
        row2 = dict(row)
        MQ.mq_register({"rows": [row2]})
        saved = serve.results_rows()
        self.assertEqual((saved[0].get("content_ref") or {}).get("source_url", ""), "")


class TestMetaqueryStage(MetaqueryBase):
    """스테이징 = 수집기가 올린 '고르기 전' 목록. 조회 기본 소스 · 검수 콘텐츠와 분리."""

    def _rows(self, n=3, **over):
        rows = []
        for i in range(1, n + 1):
            r = {"id": "k%d" % i, "service": "뉴스" if i % 2 else "스포츠", "title": "수집 기사 %d" % i,
                 "subtitle": "", "body": "본문 %d 내용" % i, "url": "https://example.com/%d" % i,
                 "published_at": "2026-09-0%dT10:00:00" % i, "grade": "G" if i % 2 else "",
                 "summary": "", "entities": [], "intent": [], "category": [], "model": "", "version": 0}
            r.update(over)
            rows.append(r)
        return rows

    def test_stage_then_search_default_source(self):
        from prism import metaquery as MQ
        serve = self._serve()
        serve.Handler.server_mock = False                 # 실모드 · 메타베이스 미설정이어도 스테이징은 동작
        r = MQ.mq_stage({"rows": self._rows(3)})
        self.assertEqual((r["ok"], r["added"], r["updated"], r["staged"]), (True, 3, 0, 3))
        got = MQ.mq_search({})
        self.assertEqual((got["ok"], got["source"], got["n"]), (True, "stage", 3))
        self.assertFalse(got["mock"])
        for row in got["rows"]:
            self.assertIn("hash", row); self.assertFalse(row["registered"]); self.assertIn("staged_at", row)
        self.assertEqual(serve.results_rows(), [])        # 검수 콘텐츠에는 아무것도 없다
        self.assertEqual(MQ.mq_status()["staged"], 3)

    def test_stage_filters_and_upsert(self):
        from prism import metaquery as MQ
        serve = self._serve()
        MQ.mq_stage({"rows": self._rows(3)})
        self.assertEqual(MQ.mq_search({"service": "뉴스"})["n"], 2)
        self.assertEqual(MQ.mq_search({"grade": "G"})["n"], 2)
        self.assertEqual(MQ.mq_search({"keyword": "기사 2"})["n"], 1)
        self.assertEqual(MQ.mq_search({"date_from": "2026-09-02", "date_to": "2026-09-03"})["n"], 1)
        again = MQ.mq_stage({"rows": self._rows(3, grade="R")})   # 같은 콘텐츠 재수집 → 덮어쓰기(행 수 불변)
        self.assertEqual((again["added"], again["updated"], again["staged"]), (0, 3, 3))
        self.assertTrue(all(r["grade"] == "R" for r in MQ.mq_search({})["rows"]))

    def test_stage_rejects_empty_and_unsafe_url(self):
        from prism import metaquery as MQ
        self._serve()
        self.assertFalse(MQ.mq_stage({})["ok"])
        self.assertFalse(MQ.mq_stage({"rows": [{"title": "", "body": ""}]})["ok"])
        r = MQ.mq_stage({"rows": self._rows(1, url="javascript:alert(1)")})
        self.assertTrue(r["ok"])
        self.assertEqual(MQ.mq_search({})["rows"][0]["url"], "")

    def test_register_from_stage_and_delete_rest(self):
        """화면 흐름: 스테이징에서 고른 건만 지정 · 나머지는 삭제. 지정된 콘텐츠는 삭제해도 남는다."""
        from prism import metaquery as MQ
        serve = self._serve()
        calls = self._stub_extract(serve)
        MQ.mq_stage({"rows": self._rows(3)})
        rows = MQ.mq_search({})["rows"]
        picked = [r for r in rows if r["grade"] == "G"][:1]
        reg = MQ.mq_register({"rows": picked})
        self.assertEqual((reg["ok"], reg["added"]), (True, 1))
        self.assertEqual(calls, [])
        again = MQ.mq_search({})["rows"]
        self.assertEqual(sum(1 for r in again if r["registered"]), 1)   # 스테이징엔 남고 '인입됨' 표시
        d = MQ.mq_stage_delete({"hashes": [r["hash"] for r in again]})
        self.assertEqual((d["ok"], d["deleted"], d["staged"]), (True, 3, 0))
        self.assertEqual(MQ.mq_search({})["n"], 0)
        self.assertEqual(len(serve.results_rows()), 1)    # 검수 콘텐츠는 그대로
        self.assertFalse(MQ.mq_stage_delete({})["ok"])

    def test_stage_ttl_purge(self):
        from prism import metaquery as MQ
        serve = self._serve()
        MQ.mq_stage({"rows": self._rows(2)})
        c = serve._STORE._conn()
        c.execute("UPDATE mq_stage SET staged_at=staged_at-%d WHERE hash=(SELECT hash FROM mq_stage LIMIT 1)"
                  % ((MQ._STAGE_TTL_DAYS + 1) * 86400))
        c.commit()
        r = MQ.mq_stage({"rows": self._rows(1, id="fresh", title="새 행")})   # 올릴 때 만료분 정리
        self.assertEqual((r["purged"], r["staged"]), (1, 2))


class TestMetaqueryRoutes(unittest.TestCase):
    def test_routes_registered_with_admin_gates(self):
        from prism import serve
        fn, admin = serve._GET_ROUTES["/metaquery"]
        self.assertTrue(admin)
        self.assertIn("/metaquery", serve._GET_ORDER)     # _GET_ORDER 스냅샷 이전 등록 확인
        for path in ("/metaquery-search", "/metaquery-register", "/metaquery-stage", "/metaquery-stage-delete"):
            fn, gate = serve._POST_ROUTES[path]
            self.assertEqual(gate, "admin")
        self.assertEqual(serve._menu_for_path("/metaquery-register"), "content")
        self.assertEqual(serve._menu_for_path("/metaquery-stage"), "content")

    def test_config_masks_metabase_key(self):
        from prism import serve
        st = serve.config_status()
        self.assertIn("hasMetabaseKey", st)
        self.assertNotIn("metabaseKey", st)               # 실값 노출 금지


class TestIngestDefaultOff(unittest.TestCase):
    def test_scheduler_requires_optin_env(self):
        from prism import ingestops as IG
        orig_thread = IG._INGEST_THREAD
        orig_env = os.environ.pop("PRISM_INGEST_AUTO", None)
        try:
            IG._INGEST_THREAD = None
            IG.start_ingest_scheduler()                   # 기본: 기동하지 않는다
            self.assertIsNone(IG._INGEST_THREAD)
        finally:
            IG._INGEST_THREAD = orig_thread
            if orig_env is not None:
                os.environ["PRISM_INGEST_AUTO"] = orig_env


if __name__ == "__main__":
    unittest.main()
