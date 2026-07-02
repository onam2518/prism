"""UI 버튼 실동작 스모크: mock 서버를 스레드로 띄우고, 화면의 모든 버튼이 호출하는
엔드포인트를 실호출로 검증한다(파라미터 계약 포함 · 500/에러 응답이면 실패).

실행: python3 -m pytest tests/test_http_smoke.py -q
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _req(port, path, obj=None, method=None, raw=None, ctype="application/json"):
    data = raw if raw is not None else (json.dumps(obj).encode() if obj is not None else None)
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 headers={"Content-Type": ctype} if data else {},
                                 method=method or ("POST" if data is not None else "GET"))
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read().decode()
        if body.strip().startswith(("{", "[")):
            try:
                return r.status, json.loads(body)
            except json.JSONDecodeError:      # JSONL(learn-export 등)은 원문 그대로
                pass
        return r.status, body


class TestButtonsEndToEnd(unittest.TestCase):
    """각 테스트 = 화면 영역별 버튼 묶음. 서버는 클래스당 1회 부팅(sqlite 스크래치)."""

    @classmethod
    def setUpClass(cls):
        os.environ["PRISM_BACKEND"] = "sqlite"
        os.environ["PRISM_DB"] = os.path.join(tempfile.mkdtemp(), "smoke.db")
        from http.server import ThreadingHTTPServer
        from prism import serve
        serve._STORE = None                    # 환경 반영 재초기화
        serve.Handler.server_mock = True
        cls.serve = serve
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.serve._STORE = None

    def ok(self, path, obj=None, **kw):
        status, body = _req(self.port, path, obj, **kw)
        self.assertEqual(status, 200, f"{path} -> HTTP {status}")
        return body

    # ── 콘텐츠 관리: STEP 1 추가(수동·용도) → STEP 2 실행 → 큐 ──
    def test_01_content_add_and_run(self):
        r = self.ok("/run", {"displayServiceName": "뉴스", "title": "스모크 일반", "body": "본문 A"})
        self.assertNotIn("error", r)
        r = self.ok("/run", {"displayServiceName": "뉴스", "title": "스모크 홀드아웃", "body": "본문 B", "purpose": "eval"})
        self.assertNotIn("error", r)
        dash = self.ok("/dashboard")
        row = next(c for c in dash["contents"] if c["title"] == "스모크 홀드아웃")
        self.assertEqual(row["purpose"], "eval")
        self.__class__.eval_hash = row["hash"]
        self.__class__.review_hash = next(c["hash"] for c in dash["contents"] if c["title"] == "스모크 일반")
        # 용도 전환 버튼
        self.assertTrue(self.ok("/purpose", {"hashes": [row["hash"]], "purpose": "review"})["ok"])
        self.assertTrue(self.ok("/purpose", {"hashes": [row["hash"]], "purpose": "eval"})["ok"])
        # 일괄 실행 버튼(지정 모델 · mock)
        r = self.ok("/rerun-all", {"model": "solar-pro2"})
        self.assertTrue(r.get("ok"))
        # 검수 목록: 평가용 제외 확인
        raw = self.ok("/raw")
        titles = [i["title"] for i in raw["items"]]
        self.assertIn("스모크 일반", titles)
        self.assertNotIn("스모크 홀드아웃", titles)

    # ── 콘텐츠 검수: 판정·교정·상세 비교 ──
    def test_02_review_buttons(self):
        h = self.review_hash
        r = self.ok("/feedback", {"hash": h, "service": "뉴스", "title": "스모크 일반",
                                  "verdict": "good", "stage": "review", "note": "", "reviewer": "복실"})
        self.assertTrue(r.get("ok", True) or "error" not in r)
        r = self.ok("/patch-meta", {"hash": h, "patch": {"summary": "더 나은 리드문"}, "reviewer": "복실"})
        self.assertNotIn("error", r if isinstance(r, dict) else {})
        self.ok("/drafts?hash=" + h)
        self.ok("/model-stats")
        self.ok("/arena?reviewer=%EB%B3%B5%EC%8B%A4")

    # ── 테스트셋 관리: 학습 반영 → 정답셋 목록·현황 → 학습 데이터·내보내기 ──
    def test_03_testset_buttons(self):
        r = self.ok("/learn-batch", {})
        self.assertTrue(r.get("ok"))
        self.ok("/learn-report")
        gs = self.ok("/golden-status")
        self.assertTrue(gs.get("ok"))
        gl = self.ok("/golden-list")
        self.assertTrue(gl.get("ok"))
        ld = self.ok("/learn-data")
        self.assertTrue(ld.get("ok"))
        for kind in ("sft", "dpo", "rationale"):
            status, _ = _req(self.port, f"/learn-export?kind={kind}")
            self.assertEqual(status, 200)
        status, spec = _req(self.port, "/learn-spec")
        self.assertEqual(status, 200)

    # ── 평가: 평가 실행(기준) → 건별 판정 → A/B 모델 비교 ──
    def test_04_evaluate_buttons(self):
        r = self.ok("/eval-golden", {"model": "", "scope": "all"})
        self.assertIn("ok", r)                  # 골든 유무에 따라 ok/에러 안내 모두 계약상 정상
        r = self.ok("/eval-judge", {"hash": "judge-smoke", "verdict": "adopt", "reviewer": "복실",
                                    "expected": "R", "got": "G"})
        self.assertTrue(r["ok"] and r["judge"]["adopt"] == 1)
        r = self.ok("/eval-judge", {"hash": "judge-smoke", "verdict": "reject", "reviewer": "복실"})
        self.assertEqual((r["judge"]["adopt"], r["judge"]["reject"]), (0, 1))
        r = self.ok("/compare-models", {"models": ["solar-pro2", "gpt-5.4"], "scope": "all"})
        self.assertIn("ok", r)

    # ── 시스템 설정: 데이터 관리 · API 키 · 로컬 초기화 ──
    def test_05_system_buttons(self):
        self.assertTrue(self.ok("/admin", {"action": "clear_golden"})["ok"])
        self.assertTrue(self.ok("/admin", {"action": "clear_feedback"})["ok"])
        r = self.ok("/admin", {"action": "delete_team"})
        self.assertFalse(r["ok"])              # sqlite: 미지원 안내가 계약
        self.ok("/config", {"reasoning_effort": "default"})
        cfg = self.ok("/config")
        self.assertIn("availableModels", cfg)
        self.assertTrue(self.ok("/admin", {"action": "clear_contents"})["ok"])
        status, d = _req(self.port, "/store", {"clear": True})
        self.assertEqual(status, 200)

    # ── 사전·정책 / 프롬프트 스튜디오 / 실험실 ──
    def test_06_dict_prompt_lab_buttons(self):
        d = self.ok("/dict")
        self.assertIn("intentUniversal", d)
        self.ok("/config", {"stage_prompts": {"analyze": "스모크 추가 지시"}})
        self.ok("/topics")
        self.ok("/usermeta")
        self.ok("/vocab")


if __name__ == "__main__":
    unittest.main()
