"""미디어 등록(media_register): 추출 결과를 메타째 저장하는 계약.

배경(실험실 정리 2026-08-13): 미디어 탭을 콘텐츠 추가 탭으로 승격하며 등록 경로 신설.
add_contents 로 등록하면 메타가 빈 dict 로 저장돼 STEP 2 가 텍스트 파이프라인으로
재추출한다(비전·영상 메타 유실 + 이중 과금). media_register 는 추출 미리보기의
(content, out) 쌍을 그대로 store_save 해 등록 즉시 '실행 완료' 상태가 되는 게 핵심.

계약:
· 등록 즉시 실행 완료(_is_pending_row 아님) · 아이템 메타·판정 그대로 보존
· 등록은 모델 호출 0건 · STEP 2 '미실행만' 실행 대상에 안 잡힌다
· 같은 4필드 해시 재등록은 기존 불변(existing=1)
· purpose=eval 은 홀드아웃 지정(/run 과 동일)
· 추출 메타 없는 등록·빈 제목/본문은 거부

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import json as _j
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _content():
    return {"displayServiceName": "포토", "title": "사진 기사", "subtitle": "",
            "body": "합성 본문", "source_url": "https://example.com/a"}


def _output():
    return {"item_meta": {"summary": "리드문", "intent": ["포토·영상 중심"],
                          "entities": ["별명#인물"], "content_category": ["Sports"]},
            "quality_meta": {"finalGrade": "G", "review": "auto", "reasons": []},
            "trace": {"model": "vision-x", "cost_usd": 0.0}}


class MediaRegisterBase(unittest.TestCase):
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
            return _j.loads(_j.dumps(_output()))
        orig = serve.PIPE.extract
        serve.PIPE.extract = fake
        self.addCleanup(lambda: setattr(serve.PIPE, "extract", orig))
        return calls


class TestMediaRegister(MediaRegisterBase):
    def test_register_is_done_not_pending(self):
        """등록 즉시 실행 완료 상태 · 메타 보존 · STEP 2 재실행 0건(이중 과금 없음)."""
        from prism import mediaops as MO
        serve = self._serve()
        calls = self._stub_extract(serve)
        r = MO.media_register(_content(), _output())
        self.assertEqual((r["ok"], r["added"], r["existing"]), (True, 1, 0))
        rows = serve.results_rows()
        self.assertEqual(len(rows), 1)
        self.assertFalse(serve._is_pending_row(rows[0]))
        self.assertEqual((rows[0].get("quality_meta") or {}).get("finalGrade"), "G")
        self.assertEqual((rows[0].get("item_meta") or {}).get("summary"), "리드문")
        self.assertEqual(calls, [])                       # 등록 자체는 모델 호출 0
        run = serve.rerun_all("m1", scope="pending")      # 미실행 대상에 안 잡힌다
        self.assertEqual(run["done"], 0)
        self.assertEqual(calls, [])

    def test_duplicate_register_keeps_existing(self):
        """같은 4필드 재등록: 기존 불변 · existing 집계(add_contents 와 동일 정책)."""
        from prism import mediaops as MO
        serve = self._serve()
        MO.media_register(_content(), _output())
        out2 = _output()
        out2["quality_meta"]["finalGrade"] = "R"          # 재등록이 덮어쓰면 안 된다
        r = MO.media_register(_content(), out2)
        self.assertEqual((r["ok"], r["added"], r["existing"]), (True, 0, 1))
        rows = serve.results_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].get("quality_meta") or {}).get("finalGrade"), "G")

    def test_purpose_eval_sets_holdout(self):
        from prism import mediaops as MO
        from prism.store import content_hash
        serve = self._serve()
        MO.media_register(_content(), _output(), purpose="eval")
        pm = serve._STORE.purpose_map()
        self.assertEqual(pm.get(content_hash(_content())), "eval")

    def test_rejects_without_meta_or_body(self):
        """빈 메타·빈 제목/본문 거부: 미리보기 없이 등록 버튼만 눌린 경로 차단."""
        from prism import mediaops as MO
        serve = self._serve()
        r1 = MO.media_register(_content(), {})
        self.assertFalse(r1["ok"])
        r2 = MO.media_register({"displayServiceName": "포토", "title": "", "body": ""}, _output())
        self.assertFalse(r2["ok"])
        self.assertEqual(serve.results_rows(), [])

    def test_source_url_scheme_whitelisted(self):
        """저장형 XSS 가드: http(s) 외 스킴은 빈 값으로 정제(/run 경로와 동일)."""
        from prism import mediaops as MO
        serve = self._serve()
        c = _content()
        c["source_url"] = "javascript:alert(1)"
        MO.media_register(c, _output())
        rows = serve.results_rows()
        self.assertEqual((rows[0].get("content_ref") or {}).get("source_url", ""), "")


if __name__ == "__main__":
    unittest.main()
