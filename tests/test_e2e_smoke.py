"""E2E 스모크(회의 소요 G): 실제 서버 프로세스를 부팅해 핵심 사용자 플로우의
HTTP 계약을 전 구간 검증한다. 화면(JS)은 별도 수동 확인 · 여기는 브라우저가 치는
요청 그대로를 stdlib 만으로 재현한다(의존성 0 원칙).

플로우: 부팅 → 페이지·번들 서빙 → 단건 추출 → 검수 목록 → 판정 → 작업 이력 →
판정 취소(행 삭제·이력 보존) → 원문 링크 백필 → 실행 큐 상태.

실행: python3 -m pytest tests/ -q  (이 파일 단독: python3 -m unittest tests.test_e2e_smoke)
"""
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _req(base, path, data=None, headers=None, raw_body=None):
    body = raw_body if raw_body is not None else (
        json.dumps(data).encode("utf-8") if data is not None else None)
    r = urllib.request.Request(base + path, data=body, headers=headers or {})
    with urllib.request.urlopen(r, timeout=15) as resp:
        payload = resp.read()
    try:
        return json.loads(payload)
    except ValueError:
        return payload.decode("utf-8", "replace")


class TestE2ESmoke(unittest.TestCase):
    proc = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = os.path.join(cls.tmp.name, "cfg.json")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write('{"chat_url": "", "model": ""}')
        cls.port = _free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        env = {**os.environ, "PRISM_DB": os.path.join(cls.tmp.name, "t.db"),
               "PRISM_CONFIG": cfg, "PRISM_BACKEND": "sqlite"}
        env.pop("SUPABASE_URL", None)
        env.pop("SUPABASE_SERVICE_KEY", None)
        cls.proc = subprocess.Popen(
            [sys.executable, "-m", "prism.serve", "--mock", "--port", str(cls.port)],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        for _ in range(100):                       # 최대 10초 부팅 대기
            try:
                _req(cls.base, "/config")
                return
            except Exception:
                if cls.proc.poll() is not None:
                    raise RuntimeError("서버 프로세스가 부팅 중 종료됨")
                time.sleep(0.1)
        raise RuntimeError("서버 부팅 시간 초과(10초)")

    @classmethod
    def tearDownClass(cls):
        if cls.proc:
            cls.proc.terminate()
            cls.proc.wait(timeout=10)
        cls.tmp.cleanup()

    def test_full_flow(self):
        base = self.base
        # ① 페이지·번들 서빙(배포 산출물 자체가 깨지면 여기서 잡힌다)
        page = _req(base, "/")
        self.assertIn("/vendor/app.js", page)
        # 앱 JS = 조각(app-NN-*.js) + 로더(app.js) 합성(앱 분할 6차) · 페이지가 참조하는
        # 조각 전부를 실제로 서빙받아 합쳐서 번들 무결성을 본다(조각 누락 = 여기서 잡힘)
        parts = re.findall(r'/vendor/(app-\d\d-[\w.\-]+\.js)', page)
        self.assertGreaterEqual(len(parts), 1, "앱 조각 script 태그가 페이지에 없음")
        bundle = "".join(_req(base, f"/vendor/{p}?v=x") for p in parts) + _req(base, "/vendor/app.js?v=x")
        self.assertIn("setFeedback", bundle)
        self.assertIn("PRISM_APP_PARTS", bundle)

        # ①-b 모바일 검수 페이지(/m): 전용 셸 + 벤더 버스터 · 기존 /m* 프리픽스 라우트 비잠식
        m = _req(base, "/m")
        self.assertIn("mreview()", m)
        self.assertIn("/vendor/mobile.js?v=", m)
        self.assertIn("mreview()", _req(base, "/m/"))              # 트레일링 슬래시 허용
        # ①-c 모바일 v2: PWA manifest(홈화면 설치) · /vendor 서빙 + start_url=/m
        self.assertIn('rel="manifest"', m)
        wm = _req(base, "/vendor/m.webmanifest")
        self.assertEqual((wm or {}).get("start_url"), "/m")
        self.assertTrue((wm or {}).get("icons"))
        # ①-d 모바일 v2: 정의 확장(등급·사유·카테고리 탭) + 요소 표 + 중앙 모달 + 테마 전환 + 배포 감지 배너 마커
        for marker in ("catDef(", "gradeDef(", "reasonDef(", "m-meta__row", "m-modal", "toggleTheme(",
                       "/vendor/gmarket.css", "updateAvail"):
            self.assertIn(marker, m)
        self.assertIn("'+' + earned", m)           # 완료 화면 획득 PT = 실누적(earned) · 추정식 아님
        self.assertIsInstance(_req(base, "/models"), dict)         # /models 는 여전히 JSON 라우트(비잠식)

        # ② 단건 추출(mock LLM · 실제 파이프라인 경유)
        r = _req(base, "/run", data={"displayServiceName": "뉴스", "title": "스모크 기사",
                                     "body": "전 구간 흐름 검증 본문"},
                 headers={"Content-Type": "application/json"})
        self.assertIn("item_meta", r.get("output") or {}, msg=str(r)[:400])

        # ③ 검수 목록: 방금 추출한 콘텐츠가 뜬다
        raw = _req(base, "/raw?limit=10")
        self.assertTrue(raw["ok"]) and self.assertEqual(raw["n"], 1)
        row = raw["items"][0]
        self.assertEqual(row["title"], "스모크 기사")
        self.assertFalse(row["fb"]["n"])           # 아직 미검수
        h = row["hash"]

        # ④ 판정 저장 → 목록 fb 반영
        r = _req(base, "/feedback", data={"hash": h, "verdict": "good", "reviewer": "스모크봇",
                                          "service": "뉴스", "title": "스모크 기사"},
                 headers={"Content-Type": "application/json"})
        self.assertTrue(r["ok"])
        fb = _req(base, "/raw?limit=10")["items"][0]["fb"]
        self.assertEqual((fb["verdict"], fb["n"], fb["good"]), ("good", 1, 1))

        # ⑤ 작업 이력: 판정이 타임라인에 남는다(로컬 sqlite 는 접근 제한 없음)
        hist = _req(base, "/history?hash=" + h)
        self.assertIn("판정 · 정확", [i["label"] for i in hist["items"]])

        # ⑥ 판정 취소: 표 행 삭제 + 취소 사실은 이력에 보존
        time.sleep(0.9)                            # 검수 속도 제한(검수자당 0.8초) 준수
        _req(base, "/feedback", data={"hash": h, "verdict": "", "reviewer": "스모크봇"},
             headers={"Content-Type": "application/json"})
        self.assertEqual(_req(base, "/raw?limit=10")["items"][0]["fb"]["n"], 0)
        labels = [i["label"] for i in _req(base, "/history?hash=" + h)["items"]]
        self.assertIn("판정 취소", labels)

        # ⑦ 원문 링크 백필(multipart 업로드): source_url 만 갱신
        csv = "제목,링크\n스모크 기사,https://news.example.com/smoke\n".encode("utf-8")
        bnd = "smokeboundary"
        body = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"file\"; "
                f"filename=\"map.csv\"\r\nContent-Type: text/csv\r\n\r\n").encode() + csv + f"\r\n--{bnd}--\r\n".encode()
        r = _req(base, "/backfill-urls", raw_body=body,
                 headers={"Content-Type": f"multipart/form-data; boundary={bnd}"})
        self.assertEqual(r.get("updated"), 1)
        self.assertEqual(_req(base, "/raw?limit=10")["items"][0]["url"],
                         "https://news.example.com/smoke")

        # ⑧ 실행 큐 상태(배포 가드가 읽는 신호)
        self.assertFalse(_req(base, "/ingest-status")["running"])


if __name__ == "__main__":
    unittest.main()
