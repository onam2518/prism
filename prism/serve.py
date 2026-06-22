"""로컬 UI (의존성 0, stdlib http.server).

  python3 -m prism.serve            # http://localhost:8765
  python3 -m prism.serve --port 9000 --mock

이미지/텍스트 입력 → (이미지면 imagext 어댑터로 Content 합성) → pipeline.extract →
리드문·엔티티·인텐트·카테고리 카드 + 원본 JSON. 키 없으면 자동 mock.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import imagext as IMG
from . import pipeline as PIPE
from .config import Config
from .llm import LLMClient

# 마지막 실행 결과(리포트 생성용)
_LAST_RESULTS: list = []


# ── multipart/form-data 파서 (cgi 제거된 3.13+ 대응, stdlib만) ───────────────
def _parse_multipart(body: bytes, boundary: str) -> dict:
    """{name: value(str) | {"filename","mime","bytes"}} 형태로 반환."""
    fields = {}
    delim = b"--" + boundary.encode()
    for part in body.split(delim):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        raw_head, payload = part.split(b"\r\n\r\n", 1)
        head = raw_head.decode("utf-8", "replace")
        disp = next((l for l in head.split("\r\n")
                     if l.lower().startswith("content-disposition")), "")
        name = _kv(disp, "name")
        if name is None:
            continue
        filename = _kv(disp, "filename")
        if filename:
            mime = next((l.split(":", 1)[1].strip() for l in head.split("\r\n")
                         if l.lower().startswith("content-type")), "image/png")
            fields[name] = {"filename": filename, "mime": mime, "bytes": payload}
        else:
            fields[name] = payload.decode("utf-8", "replace")
    return fields


def _kv(disposition: str, key: str):
    token = f'{key}="'
    i = disposition.find(token)
    if i < 0:
        return None
    j = disposition.find('"', i + len(token))
    return disposition[i + len(token):j]


# ── 파이프라인 실행 ──────────────────────────────────────────────────────────
def run_pipeline(fields: dict, *, mock: bool) -> dict:
    cfg = Config.load()
    llm = LLMClient(mock=mock, config=cfg)   # 키 없으면 LLMClient 내부서 mock=True

    images = [v for k, v in fields.items()
              if isinstance(v, dict) and v.get("bytes") and k.startswith("image")]
    source = "text"
    signals = []
    if images:
        source = "image"
        signals = IMG.extract_signals(images, mock=llm.mock)
        content = IMG.build_content(
            signals,
            displayServiceName=fields.get("displayServiceName", "포토"),
            title=fields.get("title", ""),
            caption=fields.get("caption", ""),
        )
    else:
        content = {
            "displayServiceName": fields.get("displayServiceName", ""),
            "title": fields.get("title", ""),
            "subtitle": fields.get("subtitle", ""),
            "body": fields.get("body", ""),
        }

    out = PIPE.extract(content, llm)
    _LAST_RESULTS[:] = [out]
    return {
        "source": source,
        "mock": llm.mock,
        "content": content,
        "signals": signals,
        "output": out,
    }


def vocab() -> dict:
    """드롭다운용 어휘(콘텐츠 그룹 등). dictionaries/profiles 와 동기화."""
    from . import dictionaries as D
    return {"groups": list(D.SERVICE_GROUP.keys())}


def run_batch(file_bytes: bytes, filename: str) -> dict:
    """엑셀/CSV 업로드 → ingest 매핑 → 행마다 추출 → 결과+리포트(_LAST_RESULTS)."""
    from . import ingest as ING
    ext = os.path.splitext(filename or "")[1].lower() or ".xlsx"
    cfg = Config.load()
    llm = LLMClient(mock=Handler.server_mock, config=cfg)
    fd, tmp = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file_bytes)
        a = ING.assess(tmp)
        if not a["ok"]:
            return {"error": a["reason"], "headers": a.get("headers", [])}
        contents = ING.to_contents(tmp)[:200]
        results, items = [], []
        for c in contents:
            out = PIPE.extract(c, llm)
            results.append(out)
            im = out.get("item_meta") or {}
            items.append({"title": (c.get("title") or "")[:80],
                          "summary": im.get("summary", ""),
                          "entities": im.get("entities", []),
                          "intent": im.get("intent", []),
                          "grade": (out.get("quality_meta") or {}).get("finalGrade", "")})
        _LAST_RESULTS[:] = results
        return {"source": "excel", "mock": llm.mock, "count": len(results),
                "mapping": a["mapping"], "items": items}
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def build_report_html() -> str:
    if not _LAST_RESULTS:
        return "<p>아직 실행 결과가 없습니다. 먼저 추출을 실행하세요.</p>"
    from . import dashboard as DASH
    with tempfile.TemporaryDirectory() as d:
        rpath = os.path.join(d, "results.jsonl")
        with open(rpath, "w", encoding="utf-8") as f:
            for r in _LAST_RESULTS:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        out = os.path.join(d, "report.html")
        try:
            DASH.build_integrated(rpath, out, title="Prism (이미지 트랙)")
            return open(out, encoding="utf-8").read()
        except Exception as e:
            return f"<p>리포트 생성 실패: {e}</p>"


# ── 설정(API 키 / 모델 / 엔드포인트) ─────────────────────────────────────────
_KEY_PATH = os.path.expanduser("~/.prism_key")


def load_persisted_key():
    """저장된 키가 있고 환경변수가 비어 있으면 프로세스 환경에 주입(서버 시작 시)."""
    if IMG._api_key():
        return
    try:
        if os.path.exists(_KEY_PATH):
            k = open(_KEY_PATH, encoding="utf-8").read().strip()
            if k:
                os.environ["UPSTAGE_API_KEY"] = k
    except Exception:
        pass


def config_status() -> dict:
    cfg = Config.load()
    base = (cfg.chat_url or "").rsplit("/chat/completions", 1)[0]
    return {
        "hasKey": bool(IMG._api_key()),
        "persisted": os.path.exists(_KEY_PATH),
        "model": cfg.model or "",
        "baseUrl": base,
        "configured": cfg.is_configured(),
        "forcedMock": Handler.server_mock,
    }


def apply_config(data: dict) -> dict:
    """키/모델/엔드포인트 적용. 키는 프로세스 환경에 주입, persist 시 ~/.prism_key 저장."""
    key = (data.get("api_key") or "").strip()
    if key:
        os.environ["UPSTAGE_API_KEY"] = key
        if data.get("persist"):
            try:
                with open(_KEY_PATH, "w", encoding="utf-8") as f:
                    f.write(key)
                os.chmod(_KEY_PATH, 0o600)
            except Exception:
                pass
    elif data.get("forget"):                      # 저장된 키 삭제
        os.environ.pop("UPSTAGE_API_KEY", None)
        try:
            os.remove(_KEY_PATH)
        except OSError:
            pass
    model = (data.get("model") or "").strip()
    base = (data.get("base_url") or "").strip()
    if model or base:
        cfg = Config.load()
        if base:
            cfg.set_base_url(base)
        if model:
            cfg.model = model
        try:
            cfg.save_template()                   # config.json 갱신(키는 저장 안 함)
        except Exception:
            pass
    return config_status()


def list_models() -> dict:
    """현재 키로 접근 가능한 모델 목록을 Upstage /models 에서 조회."""
    if not IMG._api_key():
        return {"ok": False, "detail": "API 키가 설정되지 않았습니다", "models": []}
    cfg = Config.load()
    url = cfg.models_url or ((cfg.chat_url or "").rsplit("/chat/completions", 1)[0] + "/models")
    if not url or not url.endswith("/models"):
        return {"ok": False, "detail": "models 엔드포인트 미설정", "models": []}
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {IMG._api_key()}")
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        data = payload.get("data") or payload.get("models") or []
        ids = sorted({(m.get("id") or m.get("name") or "") for m in data
                      if isinstance(m, dict)} - {""})
        return {"ok": True, "models": ids, "current": cfg.model or ""}
    except urllib.error.HTTPError as e:
        return {"ok": False, "detail": f"HTTP{e.code}", "models": []}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200], "models": []}


def ping_model() -> dict:
    """현재 키/설정으로 실제 1회 호출하여 연결 검증."""
    if not IMG._api_key():
        return {"ok": False, "detail": "API 키가 설정되지 않았습니다"}
    try:
        cfg = Config.load()
        if not cfg.is_configured():
            return {"ok": False, "detail": "엔드포인트·모델 미설정 (config)"}
        llm = LLMClient(config=cfg)
        if llm.mock:
            return {"ok": False, "detail": "키 인식 실패 (mock 모드로 동작)"}
        obj, res = llm.complete_json("JSON 객체 하나만 출력한다.",
                                     '{"ok": true} 형태로만 답하라.', tag="ping")
        if res.fail_kind or "_fail" in obj:
            return {"ok": False, "detail": (obj.get("_fail") or res.fail_kind or "호출 실패")[:200]}
        return {"ok": True, "detail": f"{cfg.model} 응답 정상", "latency_ms": res.latency_ms}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


_JSON = "application/json; charset=utf-8"


# ── HTTP 핸들러 ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_mock = False

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/report"):
            self._send(200, build_report_html())
        elif self.path.startswith("/config"):
            self._send(200, json.dumps(config_status(), ensure_ascii=False), _JSON)
        elif self.path.startswith("/models"):
            self._send(200, json.dumps(list_models(), ensure_ascii=False), _JSON)
        elif self.path.startswith("/vocab"):
            self._send(200, json.dumps(vocab(), ensure_ascii=False), _JSON)
        else:
            self._send(200, PAGE)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if self.path.startswith("/config"):
            try:
                self._send(200, json.dumps(apply_config(json.loads(body or b"{}")),
                                           ensure_ascii=False), _JSON)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)
            return

        if self.path.startswith("/ping"):
            self._send(200, json.dumps(ping_model(), ensure_ascii=False), _JSON)
            return

        if not self.path.startswith("/run"):
            self._send(404, "not found")
            return
        ctype = self.headers.get("Content-Type", "")
        try:
            if "multipart/form-data" in ctype:
                boundary = ctype.split("boundary=", 1)[1].strip()
                fields = _parse_multipart(body, boundary)
            else:
                fields = json.loads(body or b"{}")
            if self.path.startswith("/run-batch"):
                f = fields.get("file")
                if not isinstance(f, dict) or not f.get("bytes"):
                    result = {"error": "파일이 없습니다"}
                else:
                    result = run_batch(f["bytes"], f.get("filename", "upload.xlsx"))
            else:
                result = run_pipeline(fields, mock=self.server_mock)
            self._send(200, json.dumps(result, ensure_ascii=False), _JSON)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False), _JSON)


PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prism · 리드문·메타 추출</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.tailwindcss.com"></script>
<script>
  tailwind.config = {
    theme: { extend: {
      fontFamily: { sans: ['Geist', 'system-ui', 'sans-serif'], mono: ['"Geist Mono"', 'monospace'] },
      colors: {
        violet: { DEFAULT: '#5b52ff', hover: '#4a42e0', deep: '#281ca5' },
        solar: '#d2ff95',
        canvas: '#0b0a0f', surface: '#141318', surface2: '#1a1922',
        body: '#9aa0aa', muted: '#6e7191',
      },
    } },
  };
</script>
<script>
  document.addEventListener('alpine:init', () => {
    Alpine.data('prismApp', () => ({
      tabItems: [{ id: 'image', label: '이미지' }, { id: 'text', label: '텍스트' }, { id: 'excel', label: '엑셀' }],
      activeTabId: 'image',
      loading: false,
      status: '',
      result: null,
      batchResult: null,
      groups: ['뉴스', '연예', '스포츠', '콘텐츠', '커뮤니티', '블로그', '음악', '동영상'],
      imgGroup: '연예', imgTitle: '', imgCaption: '',
      txtGroup: '뉴스', txtTitle: '', txtBody: '',
      fileLabel: '선택된 파일 없음', excelLabel: '선택된 파일 없음',

      // 설정(API 키 / 모델)
      showSettings: false, cfg: { hasKey: false, model: '', persisted: false, forcedMock: false },
      cfgKey: '', cfgModel: '', cfgPersist: true, cfgMsg: '', cfgBusy: false,
      models: [], modelsMsg: '',

      init() {
        this.refreshConfig();
        fetch('/vocab').then(r => r.json()).then(j => { if (j.groups && j.groups.length) this.groups = j.groups; }).catch(() => {});
      },
      onExcel(e) { const fs = e.target.files; this.excelLabel = fs.length ? fs[0].name : '선택된 파일 없음'; },
      get modelOptions() {
        const a = this.models.slice();
        if (this.cfgModel && !a.includes(this.cfgModel)) a.unshift(this.cfgModel);
        return a;
      },
      async loadModels() {
        this.modelsMsg = '불러오는 중…'; this.cfgBusy = true;
        try {
          if (this.cfgKey) {                       // 입력한 키를 먼저 적용(세션)
            await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ api_key: this.cfgKey }) });
            await this.refreshConfig();
          }
          const j = await (await fetch('/models')).json();
          if (j.ok) {
            this.models = j.models;
            if (!this.cfgModel || !this.models.includes(this.cfgModel))
              this.cfgModel = this.cfg.model || this.models[0] || '';
            this.modelsMsg = this.models.length + '개 모델 불러옴';
          } else { this.modelsMsg = '실패 · ' + j.detail; }
        } catch (e) { this.modelsMsg = '오류: ' + e; }
        this.cfgBusy = false;
      },
      async refreshConfig() {
        try {
          const r = await fetch('/config'); this.cfg = await r.json();
          if (!this.cfgModel) this.cfgModel = this.cfg.model;
        } catch (e) { /* noop */ }
      },
      openSettings() { this.cfgMsg = ''; this.cfgModel = this.cfg.model || ''; this.showSettings = true; },
      async saveConfig() {
        this.cfgBusy = true; this.cfgMsg = '저장 중…';
        try {
          const r = await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: this.cfgKey, model: this.cfgModel, persist: this.cfgPersist }) });
          this.cfg = await r.json();
          this.cfgMsg = this.cfg.hasKey ? '저장됨 · 연결 테스트로 확인하세요' : '저장됨';
        } catch (e) { this.cfgMsg = '오류: ' + e; }
        this.cfgBusy = false;
        if (this.cfg.hasKey && !this.models.length) this.loadModels();   // 모델 목록 자동 로드
        this.cfgKey = '';
      },
      async forgetKey() {
        this.cfgBusy = true; this.cfgMsg = '삭제 중…';
        try { const r = await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ forget: true }) }); this.cfg = await r.json(); this.cfgMsg = '저장된 키 삭제됨'; }
        catch (e) { this.cfgMsg = '오류: ' + e; }
        this.cfgBusy = false;
      },
      async testConn() {
        this.cfgBusy = true; this.cfgMsg = '연결 테스트 중…';
        try { const j = await (await fetch('/ping', { method: 'POST' })).json();
          this.cfgMsg = (j.ok ? '✓ 성공 · ' : '✗ 실패 · ') + j.detail; }
        catch (e) { this.cfgMsg = '오류: ' + e; }
        await this.refreshConfig(); this.cfgBusy = false;
      },

      selectTab(id) { this.activeTabId = id; this.status = ''; },
      onFiles(e) {
        const fs = e.target.files;
        this.fileLabel = fs.length ? (fs.length + '개 파일 선택됨') : '선택된 파일 없음';
      },

      // DNM 메타 체계(13. 프로젝트 기획 / 1312. 아이템 메타) 기준 item_meta 필드:
      //   summary(리드문) · entities(엔티티) · intent(인텐트) · content_category(콘텐츠 카테고리)
      get im() { return (this.result && this.result.output.item_meta) || {}; },
      get q() { return (this.result && this.result.output.quality_meta) || {}; },
      get contentCats() {
        const e = this.im.content_category || {};
        return Object.keys(e).map((k) => k + ' \\u2192 ' + e[k]);
      },

      async run() {
        this.loading = true; this.status = ''; this.result = null; this.batchResult = null;
        const fd = new FormData();
        let endpoint = '/run';
        if (this.activeTabId === 'image') {
          const fs = this.$refs.files.files;
          if (!fs.length) { this.status = '이미지를 선택하세요'; this.loading = false; return; }
          for (let i = 0; i < fs.length; i++) fd.append('image' + i, fs[i]);
          fd.append('displayServiceName', this.imgGroup);
          fd.append('title', this.imgTitle);
          fd.append('caption', this.imgCaption);
        } else if (this.activeTabId === 'excel') {
          const fs = this.$refs.excel.files;
          if (!fs.length) { this.status = '엑셀/CSV 파일을 선택하세요'; this.loading = false; return; }
          fd.append('file', fs[0]); endpoint = '/run-batch';
        } else {
          fd.append('displayServiceName', this.txtGroup);
          fd.append('title', this.txtTitle);
          fd.append('body', this.txtBody);
        }
        try {
          const j = await (await fetch(endpoint, { method: 'POST', body: fd })).json();
          if (j.error) { this.status = '오류: ' + j.error; }
          else if (j.source === 'excel') { this.batchResult = j; }
          else { this.result = j; }
        } catch (e) { this.status = '오류: ' + e; }
        finally { this.loading = false; }
      },
    }));
  });
</script>
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
<style>
  [x-cloak]{display:none!important}
  body{font-family:Geist,system-ui,sans-serif}
  ::selection{background:#5b52ff;color:#fff}
  .field{width:100%;border-radius:8px;background:#0b0a0f;border:1px solid rgba(255,255,255,.10);
    color:#fff;font-size:14px;padding:9px 11px}
  .field::placeholder{color:#5b606b}
  .field:focus{outline:none;border-color:#5b52ff;box-shadow:0 0 0 1px #5b52ff}
</style>
</head>
<body class="min-h-screen bg-canvas text-body antialiased">
<div x-data="prismApp()">

  <!-- Solar 프로모 배너 (단일 액센트) -->
  <div class="flex items-center justify-center gap-2 bg-solar px-4 py-2.5 text-sm font-medium text-[#0a0d14]">
    <svg class="h-4 w-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M13 2 4.5 13.5H11l-1 8.5L19.5 10H13l0-8z"/></svg>
    <span>Prism · 이미지에서 리드문·엔티티·인텐트·콘텐츠 카테고리를 추출합니다</span>
  </div>

  <!-- 상단 네비 -->
  <header class="flex h-14 items-center justify-between border-b border-white/[0.08] px-5">
    <div class="flex items-center gap-2.5">
      <span class="text-[15px] font-semibold tracking-tight text-white">Prism</span>
      <span class="rounded bg-white/[0.06] px-1.5 py-0.5 text-[11px] font-medium text-body">Console</span>
    </div>
    <div class="flex items-center gap-2 text-[13px] text-muted">
      <span class="inline-flex items-center gap-1.5 rounded-md border border-white/[0.08] px-2.5 py-1">
        <span class="h-1.5 w-1.5 rounded-full" x-bind:class="(cfg.hasKey && !cfg.forcedMock) ? 'bg-solar' : 'bg-amber-400'"></span>
        <span x-text="cfg.forcedMock ? 'MOCK(강제)' : (cfg.hasKey ? 'Solar 연결됨' : 'MOCK · 키 미설정')"></span>
      </span>
      <button type="button" x-on:click="openSettings()" aria-label="설정"
        class="inline-flex items-center gap-1.5 rounded-md border border-white/[0.08] px-2.5 py-1 text-white transition-colors hover:bg-white/[0.05]">
        <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        설정
      </button>
    </div>
  </header>

  <!-- 설정 모달 -->
  <div x-show="showSettings" x-cloak class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4"
       x-on:click.self="showSettings = false">
    <div class="w-full max-w-md rounded-xl border border-white/[0.10] bg-surface p-6">
      <div class="mb-1 flex items-center justify-between">
        <h2 class="text-lg font-semibold text-white">설정</h2>
        <button type="button" x-on:click="showSettings = false" class="text-muted hover:text-white" aria-label="닫기">
          <svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
        </button>
      </div>
      <p class="mb-4 text-xs text-muted">Upstage API 키를 입력하면 OCR · DocVision · 생성이 실모델로 동작합니다. 키는 config에 저장되지 않습니다.</p>

      <label class="mb-1.5 block text-xs font-medium text-muted">Upstage API 키</label>
      <input x-model="cfgKey" type="password" class="field" placeholder="up_xxxxxxxx" autocomplete="off">

      <label class="mb-1.5 mt-4 block text-xs font-medium text-muted">생성 모델</label>
      <div class="flex gap-2">
        <select x-model="cfgModel" class="field flex-1">
          <template x-for="m in modelOptions" x-bind:key="m">
            <option x-bind:value="m" x-text="m"></option>
          </template>
          <template x-if="!modelOptions.length">
            <option value="" disabled>키 입력 후 '모델 불러오기'</option>
          </template>
        </select>
        <button type="button" x-on:click="loadModels()" x-bind:disabled="cfgBusy"
          class="shrink-0 rounded-lg border border-white/[0.10] px-3 py-2 text-sm font-medium text-white hover:bg-white/[0.05] disabled:opacity-50">모델 불러오기</button>
      </div>
      <span class="mt-1 block text-xs text-muted" x-text="modelsMsg"></span>

      <label class="mt-4 flex cursor-pointer items-center gap-2 text-sm text-body">
        <input type="checkbox" x-model="cfgPersist" class="h-4 w-4 rounded border-white/20 bg-canvas text-violet">
        이 기기에 저장 (재시작 후에도 유지 · <code class="text-muted">~/.prism_key</code>)
      </label>

      <div class="mt-3 flex items-center gap-2 text-xs">
        <span class="h-1.5 w-1.5 rounded-full" x-bind:class="cfg.hasKey ? 'bg-solar' : 'bg-amber-400'"></span>
        <span class="text-muted" x-text="cfg.hasKey ? ('키 설정됨 · 모델 ' + cfg.model + (cfg.persisted ? ' · 저장됨' : '')) : '키 미설정 (현재 MOCK)'"></span>
      </div>

      <div class="mt-5 flex flex-wrap items-center gap-2">
        <button type="button" x-on:click="saveConfig()" x-bind:disabled="cfgBusy"
          class="rounded-lg bg-violet px-4 py-2 text-sm font-medium text-white hover:bg-violet-hover disabled:opacity-50">저장</button>
        <button type="button" x-on:click="testConn()" x-bind:disabled="cfgBusy"
          class="rounded-lg border border-white/[0.10] px-4 py-2 text-sm font-medium text-white hover:bg-white/[0.05] disabled:opacity-50">연결 테스트</button>
        <button type="button" x-show="cfg.persisted" x-on:click="forgetKey()" x-bind:disabled="cfgBusy"
          class="rounded-lg border border-rose-500/30 px-4 py-2 text-sm font-medium text-rose-300 hover:bg-rose-500/10 disabled:opacity-50">저장키 삭제</button>
        <span class="text-xs text-body" aria-live="polite" x-text="cfgMsg"></span>
      </div>
    </div>
  </div>

  <div class="flex">
    <!-- 좌측 사이드바 -->
    <aside class="hidden w-60 shrink-0 border-r border-white/[0.08] px-3 py-6 md:block">
      <div class="mb-6 flex items-center gap-2 px-3">
        <svg class="h-5 w-5 shrink-0 text-violet" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 3v4M3 5h4M6 17v4m-2-2h4"/><path d="M13 3l2.5 6.5L22 12l-6.5 2.5L13 21l-2.5-6.5L4 12l6.5-2.5L13 3z"/></svg>
        <span class="text-sm font-semibold tracking-tight text-white">리드문 · 메타 추출</span>
      </div>
      <div class="px-3 text-[11px] font-semibold uppercase tracking-wider text-muted">입력 방식</div>
      <nav class="mt-2 space-y-0.5" aria-label="입력 방식">
        <template x-for="tabItem in tabItems" x-bind:key="tabItem.id">
          <button type="button" x-on:click="selectTab(tabItem.id)"
            x-bind:aria-current="activeTabId === tabItem.id ? 'page' : 'false'"
            class="flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors"
            x-bind:class="activeTabId === tabItem.id ? 'bg-white/[0.07] text-white font-medium' : 'text-body hover:bg-white/[0.04] hover:text-white'">
            <!-- icon: image / text / excel -->
            <svg x-show="tabItem.id === 'image'" class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.6-3.6a2 2 0 0 0-2.8 0L6 20"/></svg>
            <svg x-show="tabItem.id === 'text'" class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7V5h16v2M9 5v14m-3 0h6"/></svg>
            <svg x-show="tabItem.id === 'excel'" class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/></svg>
            <span x-text="tabItem.label"></span>
          </button>
        </template>
      </nav>
    </aside>

    <!-- 메인 -->
    <main class="min-w-0 flex-1 px-6 py-8 lg:px-10">
      <div class="mx-auto max-w-3xl">

        <!-- 입력 카드 -->
        <section class="rounded-lg border border-white/[0.08] bg-surface p-6">
          <!-- 이미지 패널 -->
          <div x-show="activeTabId === 'image'" x-cloak class="space-y-4">
            <div>
              <label class="mb-1.5 block text-xs font-medium text-muted">이미지 (여러 장이면 하나의 콘텐츠로 통합)</label>
              <label class="flex cursor-pointer items-center justify-between rounded-lg border border-dashed border-white/[0.14] bg-canvas px-4 py-3.5 text-sm transition-colors hover:border-violet/60">
                <span x-text="fileLabel" class="text-body"></span>
                <span class="inline-flex items-center gap-1.5 rounded-md bg-white/[0.08] px-3 py-1.5 text-xs font-medium text-white">
                  <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>
                  파일 선택
                </span>
                <input x-ref="files" type="file" accept="image/*" multiple class="sr-only" x-on:change="onFiles($event)">
              </label>
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div><label class="mb-1.5 block text-xs font-medium text-muted">콘텐츠 그룹</label>
                <select x-model="imgGroup" class="field">
                  <template x-for="g in groups" x-bind:key="g"><option x-bind:value="g" x-text="g"></option></template>
                </select></div>
              <div><label class="mb-1.5 block text-xs font-medium text-muted">제목 (선택)</label>
                <input x-model="imgTitle" class="field" placeholder="없으면 이미지에서 추론"></div>
            </div>
            <div><label class="mb-1.5 block text-xs font-medium text-muted">캡션 (선택)</label>
              <input x-model="imgCaption" class="field" placeholder="사진 설명이 있으면 함께 참조"></div>
          </div>
          <!-- 텍스트 패널 -->
          <div x-show="activeTabId === 'text'" x-cloak class="space-y-4">
            <div class="grid grid-cols-2 gap-3">
              <div><label class="mb-1.5 block text-xs font-medium text-muted">콘텐츠 그룹</label>
                <select x-model="txtGroup" class="field">
                  <template x-for="g in groups" x-bind:key="g"><option x-bind:value="g" x-text="g"></option></template>
                </select></div>
              <div><label class="mb-1.5 block text-xs font-medium text-muted">제목 (title)</label>
                <input x-model="txtTitle" class="field" placeholder="기사 제목"></div>
            </div>
            <div><label class="mb-1.5 block text-xs font-medium text-muted">본문 (body)</label>
              <textarea x-model="txtBody" rows="4" class="field" placeholder="본문 내용"></textarea></div>
          </div>
          <!-- 엑셀 패널 -->
          <div x-show="activeTabId === 'excel'" x-cloak class="space-y-4">
            <div>
              <label class="mb-1.5 block text-xs font-medium text-muted">엑셀 / CSV (제목·본문 컬럼 자동 매핑)</label>
              <label class="flex cursor-pointer items-center justify-between rounded-lg border border-dashed border-white/[0.14] bg-canvas px-4 py-3.5 text-sm transition-colors hover:border-violet/60">
                <span x-text="excelLabel" class="text-body"></span>
                <span class="inline-flex items-center gap-1.5 rounded-md bg-white/[0.08] px-3 py-1.5 text-xs font-medium text-white">
                  <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>
                  파일 선택
                </span>
                <input x-ref="excel" type="file" accept=".xlsx,.csv,.tsv,.jsonl,.json" class="sr-only" x-on:change="onExcel($event)">
              </label>
              <p class="mt-1.5 text-xs text-muted">행마다 한 콘텐츠로 일괄 추출합니다. 컬럼명이 제목/본문/서비스명과 달라도 자동 추론합니다. (최대 200행)</p>
            </div>
          </div>

          <div class="mt-5 flex items-center gap-3">
            <button type="button" x-on:click="run()" x-bind:disabled="loading"
              class="inline-flex items-center gap-2 rounded-lg bg-violet px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-violet-hover disabled:opacity-50">
              <svg x-show="loading" x-cloak class="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z"/></svg>
              <span x-text="loading ? '실행 중' : '추출 실행'"></span>
            </button>
            <span aria-live="polite" class="text-sm text-rose-400" x-text="status"></span>
          </div>
        </section>

        <!-- 엑셀 배치 결과 -->
        <div x-show="batchResult" x-cloak class="mt-6 space-y-4">
          <section class="rounded-lg border border-white/[0.08] bg-surface p-6">
            <div class="mb-3 flex flex-wrap items-center gap-2 text-xs">
              <span class="inline-flex items-center gap-1.5 rounded-md bg-white/[0.06] px-2.5 py-1 font-medium text-white" x-text="batchResult ? (batchResult.count + '건 처리됨') : ''"></span>
              <span x-show="batchResult && batchResult.mock" class="inline-flex items-center rounded-md bg-amber-500/15 px-2.5 py-1 font-medium text-amber-300">MOCK</span>
              <span class="text-muted" x-text="batchResult && batchResult.mapping ? ('매핑: ' + Object.entries(batchResult.mapping).map(e=>e[0]+'←'+e[1]).join(' · ')) : ''"></span>
            </div>
            <div class="overflow-auto rounded-lg border border-white/[0.08]">
              <table class="w-full text-left text-sm">
                <thead class="bg-white/[0.03] text-xs text-muted">
                  <tr><th class="px-3 py-2 font-medium">제목</th><th class="px-3 py-2 font-medium">리드문</th><th class="px-3 py-2 font-medium">엔티티</th><th class="px-3 py-2 font-medium">등급</th></tr>
                </thead>
                <tbody>
                  <template x-for="(it, i) in (batchResult ? batchResult.items : [])" x-bind:key="i">
                    <tr class="border-t border-white/[0.06] align-top">
                      <td class="px-3 py-2 text-white" x-text="it.title || '—'"></td>
                      <td class="px-3 py-2 text-body" x-text="it.summary || '—'"></td>
                      <td class="px-3 py-2 text-body" x-text="(it.entities || []).join(', ') || '—'"></td>
                      <td class="px-3 py-2"><span x-text="it.grade" x-bind:class="it.grade === 'G' ? 'text-emerald-400' : 'text-rose-400'"></span></td>
                    </tr>
                  </template>
                </tbody>
              </table>
            </div>
            <a href="/report" target="_blank" rel="noreferrer"
               class="mt-4 inline-flex items-center gap-1.5 rounded-lg border border-white/[0.10] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-white/[0.05]">
              전체 리포트 열기
              <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M7 7h10v10"/></svg>
            </a>
          </section>
        </div>

        <!-- 결과 -->
        <div x-show="result" x-cloak class="mt-6 space-y-4">
          <section class="rounded-lg border border-white/[0.08] bg-surface p-6">
            <div class="mb-4 flex flex-wrap items-center gap-2 text-xs">
              <span x-show="q.finalGrade === 'G'" class="inline-flex items-center gap-1.5 rounded-md bg-white/[0.06] px-2.5 py-1 font-medium text-white"><span class="h-1.5 w-1.5 rounded-full bg-emerald-400"></span>유통가능 · G</span>
              <span x-show="q.finalGrade !== 'G'" class="inline-flex items-center gap-1.5 rounded-md bg-white/[0.06] px-2.5 py-1 font-medium text-white"><span class="h-1.5 w-1.5 rounded-full bg-rose-400"></span>차단 · R</span>
              <span class="text-muted" x-text="result ? ('track=' + result.output.routing.content_track + ' · source=' + result.source) : ''"></span>
            </div>

            <label class="mb-1.5 block text-xs font-medium text-muted">리드문</label>
            <p class="rounded-lg border border-white/[0.08] bg-canvas p-3.5 text-[15px] leading-relaxed text-white"
               x-text="im.summary || '(빈 값 — 차단되었거나 본문 부족)'"></p>

            <div class="mt-4 grid gap-4 sm:grid-cols-2">
              <div>
                <label class="mb-1.5 block text-xs font-medium text-muted">엔티티</label>
                <div class="flex flex-wrap gap-1.5">
                  <template x-for="x in (im.entities || [])" x-bind:key="x">
                    <span class="inline-flex items-center rounded-md border border-violet/30 bg-violet/10 px-2.5 py-1 text-xs font-medium text-[#b9b3ff]" x-text="x"></span>
                  </template>
                  <span x-show="!(im.entities || []).length" class="text-xs text-muted">—</span>
                </div>
              </div>
              <div>
                <label class="mb-1.5 block text-xs font-medium text-muted">인텐트</label>
                <div class="flex flex-wrap gap-1.5">
                  <template x-for="x in (im.intent || [])" x-bind:key="x">
                    <span class="inline-flex items-center rounded-md bg-white/[0.06] px-2.5 py-1 text-xs font-medium text-body" x-text="x"></span>
                  </template>
                  <span x-show="!(im.intent || []).length" class="text-xs text-muted">—</span>
                </div>
              </div>
            </div>

            <div class="mt-4">
              <label class="mb-1.5 block text-xs font-medium text-muted">콘텐츠 카테고리</label>
              <div class="flex flex-wrap gap-1.5">
                <template x-for="x in contentCats" x-bind:key="x">
                  <span class="inline-flex items-center rounded-md bg-white/[0.06] px-2.5 py-1 text-xs font-medium text-body" x-text="x"></span>
                </template>
                <span x-show="!contentCats.length" class="text-xs text-muted">—</span>
              </div>
            </div>
          </section>

          <!-- 이미지 추출 신호 -->
          <section x-show="result && result.signals && result.signals.length" x-cloak
                   class="rounded-lg border border-white/[0.08] bg-surface p-6">
            <div class="mb-3 text-xs font-medium text-muted" x-text="result ? ('이미지 추출 신호 (' + result.signals.length + '장)') : ''"></div>
            <template x-for="(s, i) in (result ? result.signals : [])" x-bind:key="i">
              <div class="mb-3 border-l border-white/[0.10] pl-3">
                <div class="text-xs font-semibold text-white" x-text="'이미지 ' + (i + 1)"></div>
                <div class="mt-1 flex gap-2 text-sm text-body">
                  <svg class="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
                  <span x-text="s.vision || '—'"></span>
                </div>
                <div class="flex gap-2 text-sm text-body">
                  <svg class="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7V5h16v2M9 5v14m-3 0h6"/></svg>
                  <span x-text="s.ocr || '—'"></span>
                </div>
              </div>
            </template>
          </section>

          <!-- 상세 -->
          <section class="rounded-lg border border-white/[0.08] bg-surface p-6">
            <details class="group">
              <summary class="cursor-pointer text-sm text-body transition-colors hover:text-white">합성된 Content (이미지 → 4필드)</summary>
              <pre class="mt-2 overflow-auto rounded-lg border border-white/[0.08] bg-canvas p-3 font-mono text-xs text-body" x-text="result ? JSON.stringify(result.content, null, 2) : ''"></pre>
            </details>
            <details class="group mt-2">
              <summary class="cursor-pointer text-sm text-body transition-colors hover:text-white">원본 출력 JSON</summary>
              <pre class="mt-2 overflow-auto rounded-lg border border-white/[0.08] bg-canvas p-3 font-mono text-xs text-body" x-text="result ? JSON.stringify(result.output, null, 2) : ''"></pre>
            </details>
            <a href="/report" target="_blank" rel="noreferrer"
               class="mt-4 inline-flex items-center gap-1.5 rounded-lg border border-white/[0.10] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-white/[0.05]">
              전체 리포트 열기
              <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M7 7h10v10"/></svg>
            </a>
          </section>
        </div>
      </div>
    </main>
  </div>

</div>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(description="Prism 로컬 UI")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--mock", action="store_true", help="키가 있어도 강제 mock")
    a = ap.parse_args()

    Handler.server_mock = a.mock
    load_persisted_key()                              # ~/.prism_key 있으면 주입
    keyed = bool(IMG._api_key())
    mode = "MOCK(강제)" if a.mock else ("실모델" if keyed else "MOCK(키 미설정 · UI에서 설정)")
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"  Prism UI  →  http://{a.host}:{a.port}   [{mode}]")
    print("  키 설정: 우상단 설정(톱니) 버튼 · Ctrl+C 로 종료")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  종료")


if __name__ == "__main__":
    main()
