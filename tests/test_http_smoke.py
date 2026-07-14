"""UI 버튼 실동작 스모크: mock 서버를 스레드로 띄우고, 화면의 모든 버튼이 호출하는
엔드포인트를 실호출로 검증한다(파라미터 계약 포함 · 500/에러 응답이면 실패).

실행: python3 -m pytest tests/test_http_smoke.py -q
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
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

    # ── 정답셋 관리: 학습 반영 → 정답셋 목록·현황 → 학습 데이터·내보내기 ──
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
        # 검수 UI 공용 사전(데스크탑·모바일 단일 원천) 계약: 요소 사전 + 전 인텐트 값 정의 커버(드리프트 가드)
        from prism import feedback_loop as FL
        self.assertEqual([e["id"] for e in d.get("fixElements") or []], list(FL.ELEMENTS))
        for e in d["fixElements"]:
            self.assertTrue(e.get("label") and e.get("stage") in ("analyze", "judge", "review"))
        all_intents = set(d.get("intentUniversal") or []) | set(d.get("intentForm") or [])
        for vs in (d.get("intentByService") or {}).values():
            all_intents |= set(vs)
        self.assertFalse(all_intents - set(d["intentDefs"]), "정의 없는 인텐트 값(intentDefs 누락)")
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
        self.assertIn("improve_delta", r)               # 개선 전/후 효과 측정 필드
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
        self.assertIn("dict_gap", ld)                   # 사전 갭 집계 노출

    def test_09_learn_quest_config(self):
        """검수 목표(퀘스트) 일시 설정 왕복 + 초안 기준 진행률 + 해제(삭제)."""
        # 유효 검수 = '현재 초안 생성 이후'의 표: 퀘스트 생성 전에 한 현행 초안 검수도 진행에 포함
        # (생성 시각 창은 홈 팀 진척율과 어긋나고, 전 기간 누적은 재실행 전 옛 초안 검수까지 잡는다)
        self.ok("/feedback", {"hash": self.review_hash, "service": "뉴스", "title": "스모크 일반",
                              "verdict": "good", "stage": "review", "note": "", "reviewer": "퀘스트이전"})
        time.sleep(0.05)
        self.ok("/config", {"learn_next_at": "2030-01-02T09:30"})
        cfg = self.ok("/config")
        self.assertEqual(cfg.get("learnNextAt"), "2030-01-02T09:30")
        r = self.ok("/learn-report")
        self.assertGreater(r.get("next_batch_at") or 0, 0)
        a = self.ok("/arena")                        # 팀 퀘스트: 홈·사이드바 시한 데이터
        self.assertGreater(a.get("next_batch_at") or 0, 0)
        self.assertGreaterEqual(a.get("next_version") or 0, 1)
        self.assertIn("next_model", a)                       # 어떤 모델의 버전인지 명기(미설정 시 빈 값 허용)
        self.assertIsInstance(a.get("target_models"), list)  # 카드 모델 = 검수 대상 초안의 생성 모델(provenance)
        self.assertIn("last_version", a)                     # 완료 잔상(반영 완료 카드) 데이터
        self.assertGreater(a.get("quest_started_at") or 0, 0)
        self.assertGreaterEqual(a.get("quest_done"), 1)      # 현행 초안 검수는 생성 전이어도 유효
        self.assertIsNotNone(a.get("quest_avg_done"))        # 팀 평균 진척(카드 게이지 원천 · 인원으로 나눔)
        base_done = a.get("quest_done")
        self.ok("/config", {"learn_next_at": "2030-01-03T09:30"})  # 일시 수정: 진행률 불변
        a = self.ok("/arena")
        self.assertEqual(a.get("quest_done"), base_done)
        self.ok("/config", {"learn_next_at": "2020-01-01T04:00"})  # 과거 일시 거부(즉시 발화 함정 방지)
        self.assertEqual(self.ok("/config").get("learnNextAt"), "2030-01-03T09:30")
        self.ok("/config", {"learn_next_at": "엉터리"})           # 형식 오류 무시
        self.assertEqual(self.ok("/config").get("learnNextAt"), "2030-01-03T09:30")
        self.ok("/config", {"learn_next_at": ""})                # 목표 해제 = 퀘스트 삭제
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

    # ── 실험실 · 사용자: 프로필 + 행동 로그 → 페르소나 능동 생성(저장·재방문 유지) ──
    def test_11_usermeta_persona_gen(self):
        status, csv_t = _req(self.port, "/usermeta-profile-template.csv")
        self.assertEqual(status, 200)
        self.assertIn("user_id", csv_t)
        # 1) 프로필 단건(폼) 저장 → 아직 로그 없음 = 생성 0
        r = self.ok("/usermeta-profiles", {"profile": {
            "user_id": "smoke-u1", "age_band": "30대", "interests": "재테크, 야구", "day_part": "야간"}})
        self.assertEqual(r.get("saved"), 1)
        self.assertGreaterEqual(r.get("profiles_n") or 0, 1)
        self.assertEqual(r.get("generated_n") or 0, 0)
        # 2) 행동 로그 업로드(multipart) → 재료가 모인 사용자는 자동 생성
        log_csv = ("user_id,content_id,event,dwell_sec,scroll_pct,ts\n"
                   "smoke-u1,0,click,62,80,2026-06-23T21:10\n"
                   "smoke-u1,1,impression,8,20,2026-06-23T21:14\n").encode("utf-8")
        boundary = "smokeboundary"
        part = (f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="logs.csv"\r\n'
                "Content-Type: text/csv\r\n\r\n").encode() + log_csv + f"\r\n--{boundary}--\r\n".encode()
        r = self.ok("/usermeta", raw=part, ctype=f"multipart/form-data; boundary={boundary}")
        self.assertGreaterEqual(len(r.get("users") or []), 1)
        self.assertEqual(r.get("generated_n"), 1)
        u = next(x for x in r["users"] if x["user_id"] == "smoke-u1")
        gp = u.get("gen_persona") or {}
        self.assertTrue(gp.get("name") and gp.get("desc"))       # 생성 카드(mock=결정론 폴백)
        self.assertTrue(gp.get("basis"))                         # 판단 근거
        self.assertTrue(any(p.get("generated") for p in r.get("personas_def") or []))  # 정의 표 병행
        # 3) 재방문(GET): 로그·프로필·생성 페르소나 유지 + 재생성 없음(id 유지)
        r2 = self.ok("/usermeta")
        self.assertGreaterEqual(len(r2.get("users") or []), 1)
        u2 = next(x for x in r2["users"] if x["user_id"] == "smoke-u1")
        self.assertEqual((u2.get("gen_persona") or {}).get("id"), gp.get("id"))

    # ── 실험실 · 토픽 스튜디오: 자연어+차원 토픽 생성·미리보기·저장·튜닝 왕복 ──
    def test_13_topic_studio(self):
        td = self.ok("/topics")
        self.assertGreater(td.get("n_contents") or 0, 0, "선행 테스트가 콘텐츠를 적재해야 함")
        # 카탈로그·설정·사용자 정의 컨테이너 노출(생성 폼 원천)
        self.assertIn("catalog", td)
        self.assertEqual(set(td["catalog"]), {"intents", "cats", "keywords", "eattrs"})
        self.assertEqual(set(td.get("settings") or {}), {"co_min", "entity_min"})
        self.assertIsInstance(td.get("customDefs"), list)
        n = td["n_contents"]
        # 미리보기: 조건 없음 = 단일 '핵심' 묶음 = 전체
        pv = self.ok("/topic-studio", {"action": "preview",
                                       "def": {"name": "전체", "cats": [], "intents": [], "keywords": []}})
        self.assertEqual(pv["preview"]["n_total"], n)
        self.assertTrue(pv["preview"]["bundles"])
        self.assertEqual(pv["preview"]["bundles"][0]["count"], n)   # 핵심 = 전체
        # 필수/선택 → 다중 묶음: 필수=Sports, 선택 2개 → 핵심 1 + 관련 2
        pv2 = self.ok("/topic-studio", {"action": "preview", "def": {
            "name": "스포츠 인물", "cats": ["Sports"], "intents": ["인물·사연", "인터뷰"], "keywords": [],
            "req": {"cats": ["Sports"], "intents": [], "keywords": []}}})
        kinds = [b["kind"] for b in pv2["preview"]["bundles"]]
        self.assertIn("core", kinds)                               # 핵심(전부)
        self.assertGreaterEqual(kinds.count("related"), 2)          # 선택값마다 관련 묶음
        # 자연어 제안: 필수/선택(req) 포함 반환 · 모델 지정 시 LLM 개입(mock 은 휴리스틱 폴백)
        sg = self.ok("/topic-studio", {"action": "suggest", "text": "심층 분석 콘텐츠", "model": "solar-pro2"})
        self.assertEqual(set(sg["suggest"]), {"cats", "intents", "keywords", "req", "neg"})
        self.assertEqual(set(sg["suggest"]["neg"]), {"cats", "intents", "keywords"})
        self.assertIn(sg.get("via"), ("llm", "heuristic", "none"))
        self.assertEqual(sg.get("model"), "solar-pro2")            # 선택 모델 에코(버튼이 헛돌지 않음)
        self.assertIn("심층 분석", sg["suggest"]["intents"])         # 전체 아이템메타 분류(사전) 고려 · 데이터 유무 무관
        # 토큰 부분일치: '인물들'(substring 아님)이 인텐트 '인물·사연' 을 잡아야 함(폴백 휴리스틱 강화)
        sg2 = self.ok("/topic-studio", {"action": "suggest", "text": "스포츠 주제의 인물들 콘텐츠", "model": "solar-pro2"})
        self.assertIn("인물·사연", sg2["suggest"]["intents"])
        self.assertIn("Sports", sg2["suggest"]["cats"])
        self.assertIn("Sports", sg2["suggest"]["req"]["cats"])      # 휴리스틱 기본: 카테고리=필수
        # 저장 → 사용자 정의로 영속 + 묶음(핵심) 생성
        saved = self.ok("/topic-studio", {"action": "save", "def": {
            "name": "스모크 토픽", "prompt": "스모크 자연어 설명", "cats": [], "intents": [], "keywords": []}})
        mine = [g for g in saved.get("custom", []) if g["name"] == "스모크 토픽"]
        self.assertEqual(len(mine), 1)
        g = mine[0]
        self.assertEqual(g["core_count"], n)                       # 조건 없음 = 핵심 = 전체
        did = g["id"]
        self.assertTrue(any(d["id"] == did for d in saved["customDefs"]))
        # 재조회에서도 유지(영속)
        self.assertTrue(any(d["id"] == did for d in self.ok("/topics")["customDefs"]))
        # 묶음(핵심) 드릴다운(자동 토픽과 동일 shape)
        core_cid = next(b["cluster_id"] for b in g["bundles"] if b["kind"] == "core")
        dr = self.ok("/topic-drill?cluster=" + urllib.parse.quote(core_cid))
        self.assertTrue(dr["ok"])
        self.assertEqual(dr["n"], n)
        cid = did
        # 클러스터링 튜닝 왕복(경계값 클램프 포함)
        st = self.ok("/topic-studio", {"action": "settings", "settings": {"co_min": 1, "entity_min": 4}})
        self.assertEqual(st["settings"], {"co_min": 1, "entity_min": 4})
        self.assertEqual(self.ok("/topics")["settings"], {"co_min": 1, "entity_min": 4})
        self.ok("/topic-studio", {"action": "settings", "settings": {"co_min": 99}})  # 상한 클램프
        self.assertEqual(self.ok("/topics")["settings"]["co_min"], 6)
        self.ok("/topic-studio", {"action": "settings", "settings": {"co_min": 2, "entity_min": 2}})
        # 삭제 → 사용자 정의에서 제거
        after = self.ok("/topic-studio", {"action": "delete", "id": cid})
        self.assertFalse(any(d["id"] == cid for d in after["customDefs"]))

    # ── 엔티티 사전 메뉴: 색인·수동 등재·편집(확정)·토픽 속성 조건까지 왕복 ──
    def test_14_entdict_buttons(self):
        # 엔티티가 나오는 콘텐츠 적재(mock 엔티티 = 빈도 상위 토큰) → 적재 훅이 사전에 등재
        self.ok("/run", {"displayServiceName": "스포츠", "title": "안세영 안세영 결승 진출",
                         "body": "안세영 선수가 결승에 진출했다. 안세영 경기력이 좋았다."})
        d = self.ok("/entdict")
        self.assertEqual(set(d), {"items", "stats", "meta", "enrich"})
        self.assertEqual(set(d["meta"]["types"]), {"PS", "OG", "LC", "AF", "EV", "TM"})
        self.assertGreater(d["stats"]["total"], 0, "적재 훅이 개체를 등재해야 함")
        self.assertTrue(any(e["name"] == "안세영" for e in d["items"]))
        # 백필(기존 콘텐츠 색인) 멱등 · mock 서버는 위키데이터 보강 생략
        bf = self.ok("/entdict", {"action": "backfill"})
        self.assertTrue(bf["ok"])
        self.assertEqual(bf["enrich_queued"], 0)                    # mock = 네트워크 0
        # 수동 등재 → 목록 필터(q) → 상세 → 타입·속성 수동 확정 → 별칭
        add = self.ok("/entdict", {"action": "add", "name": "스모크개체"})
        self.assertTrue(add["ok"])
        eid = add["entity"]["entity_id"]
        self.assertEqual(add["entity"]["status"], "pending")
        self.assertTrue(any(e["entity_id"] == eid for e in self.ok("/entdict?q=" + urllib.parse.quote("스모크"))["items"]))
        up = self.ok("/entdict", {"action": "update", "id": eid, "type": "PS",
                                  "attrs": {"gender": "여성", "occupation": "스포츠인"},
                                  "alias": "스모크 선수"})
        self.assertTrue(up["ok"])
        self.assertEqual(up["entity"]["status"], "active")
        self.assertEqual(up["entity"]["attr_meta"]["gender"]["status"], "confirmed")
        self.assertIn("스모크 선수", up["aliases"])
        det = self.ok("/entdict", {"action": "detail", "id": eid})
        self.assertEqual(det["entity"]["attrs"]["gender"], "여성")
        # enrich(mock) = 네트워크 없이 안전 응답 · 허용 외 타입 거부
        self.assertTrue(self.ok("/entdict", {"action": "enrich", "id": eid}).get("mock"))
        self.assertFalse(self.ok("/entdict", {"action": "update", "id": eid, "type": "XX"})["ok"])
        # 완료 기준 e2e: 링크된 개체(안세영)에 속성 확정 → '여성 스포츠인' 토픽이 해당 콘텐츠를 잡는다
        ase = next(e for e in self.ok("/entdict?q=" + urllib.parse.quote("안세영"))["items"]
                   if e["name"] == "안세영")
        self.ok("/entdict", {"action": "update", "id": ase["entity_id"], "type": "PS",
                             "attrs": {"gender": "여성", "occupation": "스포츠인"}})
        # 검수 상세용 사전 조회: 콘텐츠 엔티티 표기(별칭 포함) → 개체 정보(타입·속성)
        lk = self.ok("/entdict-lookup?names=" + urllib.parse.quote("안세영|사전에없는표기"))
        self.assertEqual(lk["entities"]["안세영"]["attrs"]["gender"], "여성")
        self.assertNotIn("사전에없는표기", lk["entities"])
        # 전체 재보강(scope=all) 계약: mock 은 네트워크 생략 응답
        self.assertTrue(self.ok("/entdict", {"action": "enrich_pending", "scope": "all"}).get("mock"))
        cat = self.ok("/topics")["catalog"]["eattrs"]               # 링크된 개체 속성만 후보로 노출
        self.assertTrue(any(c["k"] == "gender:여성" for c in cat))
        pv = self.ok("/topic-studio", {"action": "preview", "def": {
            "name": "여성 스포츠인", "eattrs": ["gender:여성", "occupation:스포츠인", "몰래키:값"]}})
        b = pv["preview"]["bundles"][0]
        self.assertIn("성별=여성", b["label"])                       # 비허용 키는 sanitize 에서 제거
        self.assertNotIn("몰래키", b["label"])
        self.assertGreaterEqual(b["count"], 1)                      # 본문에 '여성' 없이 속성으로 매칭
        saved = self.ok("/topic-studio", {"action": "save", "def": {
            "name": "여성 스포츠인 스모크", "eattrs": ["gender:여성"]}})
        mine = [g for g in saved.get("custom", []) if g["name"] == "여성 스포츠인 스모크"]
        self.assertEqual(len(mine), 1)
        self.assertTrue(any(x["dim"] == "eattrs" for x in mine[0]["must"]))   # 항상 필수
        self.ok("/topic-studio", {"action": "delete", "id": mine[0]["id"]})
        # 삭제 → 목록·링크 제거
        self.assertTrue(self.ok("/entdict", {"action": "delete", "id": eid})["ok"])
        self.assertFalse(any(e["entity_id"] == eid for e in self.ok("/entdict?q=" + urllib.parse.quote("스모크"))["items"]))

    # ── 홈 · 닉네임 변경: 이름만 교체 · 검수 이력(리더보드)이 새 이름으로 이관 ──
    def test_12_reviewer_rename(self):
        self.ok("/reviewer", {"reviewer": "개명전", "name": "개명전", "char": "boksil"})
        self.ok("/reviewer", {"reviewer": "딱지", "name": "딱지", "char": "ddakji"})
        self.ok("/feedback", {"hash": self.review_hash, "service": "뉴스", "title": "스모크 일반",
                              "verdict": "good", "stage": "review", "note": "", "reviewer": "개명전"})
        r = self.ok("/reviewer", {"mode": "rename", "reviewer": "개명전", "name": "딱지"})
        self.assertFalse(r.get("ok"))                    # 사용 중 닉네임 거부
        r = self.ok("/reviewer", {"mode": "rename", "reviewer": "개명전", "name": "개명후"})
        self.assertTrue(r.get("ok"))
        names = [b["reviewer"] for b in (self.ok("/arena").get("leaderboard") or [])]
        self.assertIn("개명후", names)                    # 검수 이력(점수)이 새 이름으로
        self.assertNotIn("개명전", names)
        # 내 행 식별자(my_id): 클라가 이름 문자열 대신 reviewer_id 로 자기 행을 찾는 근거
        a = self.ok("/arena?reviewer=" + urllib.parse.quote("개명후"))
        self.assertEqual(a.get("my_id"), "개명후")
        self.assertTrue(any(b.get("reviewer_id") == a["my_id"] for b in a.get("leaderboard") or []))

    # ── 게시판: 등록(팀원) → 목록(최신순) → 상태 변경(관리자) → 삭제 ──
    def test_13_board(self):
        r = self.ok("/board", {"action": "create", "kind": "bug", "title": "스모크 오류 제보",
                               "body": "재현: 스모크", "reviewer": "복실"})
        self.assertTrue(r.get("ok"))
        r = self.ok("/board", {"action": "create", "kind": "feature", "title": "스모크 기능 제안",
                               "reviewer": "딱지"})
        self.assertEqual(r.get("n"), 2)
        top = r["items"][0]
        self.assertEqual(top["title"], "스모크 기능 제안")            # 최신순
        r = self.ok("/board", {"action": "status", "id": top["id"], "status": "doing", "reviewer": "딱지"})
        self.assertEqual(next(i["status"] for i in r["items"] if i["id"] == top["id"]), "doing")
        r = self.ok("/board", {"action": "status", "id": top["id"], "status": "elsewhere", "reviewer": "딱지"})
        self.assertFalse(r.get("ok"))                                # 상태 값 화이트리스트
        r = self.ok("/board", {"action": "delete", "id": top["id"], "reviewer": "딱지"})
        self.assertEqual(r.get("n"), 1)
        g = self.ok("/board")
        self.assertEqual(g.get("n"), 1)
        self.assertEqual(g["items"][0]["title"], "스모크 오류 제보")


if __name__ == "__main__":
    unittest.main()
