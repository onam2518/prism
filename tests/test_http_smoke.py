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
        body = r.read().decode("utf-8", "replace")   # 바이너리(xlsx 등)도 안전(PK 시그니처는 ASCII)
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
        from prism import config as _cfg
        from prism import serve
        cls._orig_cfg_path = _cfg.DEFAULT_CONFIG_PATH   # 실제 config.json 오염 방지(격리)
        _cfg.DEFAULT_CONFIG_PATH = os.path.join(tempfile.mkdtemp(), "config.json")
        cls._cfg_mod = _cfg
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
        cls._cfg_mod.DEFAULT_CONFIG_PATH = cls._orig_cfg_path

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
        import time as _t
        r = self.ok("/eval-judge", {"hash": "judge-smoke", "verdict": "adopt", "reviewer": "복실",
                                    "expected": "R", "got": "G"})
        self.assertTrue(r["ok"] and r["judge"]["adopt"] == 1)
        _t.sleep(0.9)                                # 판정 레이트리밋(0.8s/검수자) 준수
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
        self.assertEqual(len(d.get("intentForm") or []), 8)
        self.assertTrue(d.get("intentDefs"))                     # 정책 팔레트 원천(값 정의)
        self.assertTrue(d.get("categoryCriteria"))               # 카테고리 구분 기준 15종
        status, html = _req(self.port, "/")
        self.assertEqual(status, 200)
        self.assertIn("polpal", html)                            # 정책 팔레트 렌더 마커
        self.assertIn("polfab", html)
        self.ok("/config", {"stage_prompts": {"review": "스모크 추가 지시"}})
        self.ok("/topics")
        self.ok("/usermeta")
        self.ok("/vocab")
        status, xlsx = _req(self.port, "/template.xlsx")
        self.assertEqual(status, 200)
        self.assertTrue(xlsx.startswith("PK"))         # zip 시그니처 = 유효 xlsx
        status, csv_t = _req(self.port, "/template.csv")
        self.assertEqual(status, 200)
        self.assertIn("콘텐츠 그룹", csv_t)

    # ── 프롬프트 스튜디오: 계약 노출 · 래퍼 편집 · 호출별 모델 · 미리보기 ──
    def test_07_prompt_studio_contract(self):
        cfg = self.ok("/config")
        self.assertEqual(cfg.get("metaCalls"), ["summary", "entities", "intent", "category"])
        self.assertIn("리드문 정의", (cfg.get("metaContract") or {}).get("rules", {}).get("summary", ""))
        pv = self.ok("/prompt-preview?model=solar-pro2&call=intent&service=%EC%8A%A4%ED%8F%AC%EC%B8%A0")
        self.assertTrue(pv["ok"] and pv["family"] == "solar" and "경기 프리뷰" in pv["system"])
        # 래퍼 오버라이드 저장 → 반영 → 복원
        self.ok("/config", {"family_wrappers": {"gpt": "# X\n{ROLE}\n{SCHEMA}\n{RULES}\n{EXAMPLES}\n{SELF_CHECK}{LEARNED}"}})
        pv2 = self.ok("/prompt-preview?model=gpt-5.4&call=summary")
        self.assertTrue(pv2["system"].startswith("# X"))
        self.ok("/config", {"family_wrappers": {"gpt": ""}})
        # 호출별 모델 저장 왕복
        c = self.ok("/config", {"meta_call_models": {"category": "gpt-5.4"}, "meta_four_calls": True})
        self.assertEqual((c.get("metaCallModels") or {}).get("category"), "gpt-5.4")
        self.ok("/config", {"meta_call_models": {}})


    # ── e2e: 콘텐츠 추가 → 검수 합의·교정 → 학습 반영 → 정답셋 승격 → 평가 ──
    def test_08_e2e_review_to_golden(self):
        title = "E2E 정답셋 승격 검증"
        self.ok("/run", {"displayServiceName": "뉴스", "title": title,
                         "body": "학습 반영 이후 정답셋으로 승격되는 전체 흐름을 검증하기 위한 본문입니다. 충분한 길이를 확보합니다."})
        dash = self.ok("/dashboard")
        h = next(c["hash"] for c in dash["contents"] if c["title"] == title)
        # 검수: 교정(리드문) 후 2인 '정확' 합의 · 검수자별 레이트리밋(0.8s) 준수
        import time as _t
        fixed = "학습 반영 이후 정답셋 승격 흐름을 검증한다."
        self.ok("/patch-meta", {"hash": h, "patch": {"summary": fixed}, "reviewer": "e2e검수자1"})
        _t.sleep(0.9)
        for rv in ("e2e검수자1", "e2e검수자2"):
            self.ok("/feedback", {"hash": h, "service": "뉴스", "title": title,
                                  "verdict": "good", "stage": "review", "note": "", "reviewer": rv})
        # 학습 반영 → 정답셋 승격 확인(교정된 리드문이 정답에 반영)
        before = self.ok("/golden-status")
        r = self.ok("/learn-batch", {})
        self.assertTrue(r.get("ok"))
        gl = self.ok("/golden-list")
        row = next((g for g in gl["items"] if g["hash"] == h), None)
        self.assertIsNotNone(row, "검수 합의 콘텐츠가 정답셋으로 승격되어야 함")
        self.assertEqual(row.get("source"), "review")
        gs = self.ok("/golden-status")
        self.assertGreaterEqual(gs["total"], (before.get("total") or 0) + 1)
        self.assertGreaterEqual(gs.get("batch_seq", 0), 1)      # 버전 회차 증가
        # 정답 원문에 교정 리드문 반영 확인(learn-export sft 원천)
        status, sft = _req(self.port, "/learn-export?kind=sft")
        self.assertEqual(status, 200)
        sft_text = sft if isinstance(sft, str) else json.dumps(sft, ensure_ascii=False)
        self.assertIn(fixed, sft_text)              # 교정 리드문이 SFT 정답으로 반영
        # 승격된 정답셋으로 평가 실행(모의)
        ev = self.ok("/eval-golden", {"model": "", "scope": "all"})
        self.assertTrue(ev.get("ok"), ev)
        self.assertGreaterEqual(ev.get("evaluated", 0), 1)
        ld = self.ok("/learn-data")
        self.assertGreaterEqual(ld.get("golden_n", 0), 1)

    def test_09_learn_quest_config(self):
        """검수 목표(퀘스트) 일시 설정 왕복 + 팀 퀘스트 데이터 노출 + 해제."""
        self.ok("/config", {"learn_next_at": "2030-01-02T09:30"})
        cfg = self.ok("/config")
        self.assertEqual(cfg.get("learnNextAt"), "2030-01-02T09:30")
        r = self.ok("/learn-report")
        self.assertGreater(r.get("next_batch_at") or 0, 0)
        a = self.ok("/arena")                        # 팀 퀘스트: 홈·사이드바 시한 데이터
        self.assertGreater(a.get("next_batch_at") or 0, 0)
        self.assertGreaterEqual(a.get("next_version") or 0, 1)
        self.assertIn("next_model", a)                       # 어떤 모델의 버전인지 명기(미설정 시 빈 값 허용)
        self.ok("/config", {"learn_next_at": "2020-01-01T04:00"})  # 과거 일시 거부(즉시 발화 함정 방지)
        self.assertEqual(self.ok("/config").get("learnNextAt"), "2030-01-02T09:30")
        self.ok("/config", {"learn_next_at": "엉터리"})           # 형식 오류 무시
        self.assertEqual(self.ok("/config").get("learnNextAt"), "2030-01-02T09:30")
        self.ok("/config", {"learn_next_at": ""})                # 목표 해제
        self.assertEqual(self.ok("/config").get("learnNextAt"), "")
        self.assertEqual(self.ok("/arena").get("next_batch_at") or 0, 0)

    def test_10_prompt_snapshot_after_batch(self):
        """학습 반영이 남긴 버전별 프롬프트 스냅샷: 최신 + v 지정 조회(버전 재현 근거)."""
        self.ok("/learn-batch", {})
        r = self.ok("/prompt-snapshot")
        snap = r.get("snapshot") or {}
        ver = snap.get("version")
        self.assertGreaterEqual(int(ver or 0), 1)
        self.assertEqual(set(snap.get("calls") or {}) & {"summary", "entities", "intent", "category"},
                         {"summary", "entities", "intent", "category"})
        for cs in (snap.get("calls") or {}).values():
            self.assertTrue(cs.get("system"))
        byv = self.ok(f"/prompt-snapshot?v={ver}")
        self.assertEqual((byv.get("snapshot") or {}).get("version"), ver)


if __name__ == "__main__":
    unittest.main()
