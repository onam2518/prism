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
from . import prompts as PR
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
    llm = make_text_llm(cfg, mock)           # 텍스트 슬롯(solar|router). 무키면 내부서 mock

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
    llm = make_text_llm(cfg, Handler.server_mock)
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
_KEY_PATH = os.path.expanduser("~/.prism_key")              # Upstage Solar
_ROUTER_KEY_PATH = os.path.expanduser("~/.prism_router_key")  # BizRouter


def load_persisted_key():
    """저장된 키가 있고 환경변수가 비어 있으면 프로세스 환경에 주입(서버 시작 시)."""
    if not IMG._api_key() and os.path.exists(_KEY_PATH):
        try:
            k = open(_KEY_PATH, encoding="utf-8").read().strip()
            if k:
                os.environ["UPSTAGE_API_KEY"] = k
        except Exception:
            pass
    if not IMG._router_key() and os.path.exists(_ROUTER_KEY_PATH):
        try:
            k = open(_ROUTER_KEY_PATH, encoding="utf-8").read().strip()
            if k:
                os.environ["PRISM_ROUTER_KEY"] = k
        except Exception:
            pass


def make_text_llm(cfg: Config, mock: bool) -> LLMClient:
    """텍스트 슬롯 제공자에 맞춰 LLMClient 구성.
    solar=직접(Upstage), router=BizRouter(prefixed 모델 + 라우터 키)."""
    if cfg.text_provider == "router" and IMG._router_key() and cfg.text_model:
        cfg.chat_url = (cfg.router_url or "https://bizrouter.ai/api/v1").rstrip("/") + "/chat/completions"
        return LLMClient(mock=mock, config=cfg, api_key=IMG._router_key(), model=cfg.text_model)
    return LLMClient(mock=mock, config=cfg)


def sync_prompt():
    """config 의 system_prompt 를 추출 프롬프트에 반영(추가 지시)."""
    try:
        PR.EXTRA_INSTRUCTION = Config.load().system_prompt or ""
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
        "reasoning": cfg.reasoning_effort or "default",
        "systemPrompt": cfg.system_prompt or "",
        "configured": cfg.is_configured(),
        "forcedMock": Handler.server_mock,
        # 모델 슬롯
        "hasRouterKey": bool(IMG._router_key()),
        "routerPersisted": os.path.exists(_ROUTER_KEY_PATH),
        "textProvider": cfg.text_provider or "solar",
        "textModel": cfg.text_model or "",
        "visionProvider": cfg.vision_provider or "upstage_ie",
        "visionModel": cfg.vision_model or "",
    }


def apply_config(data: dict) -> dict:
    """키/모델/엔드포인트/추론강도/추가지시 적용. 키만 프로세스 환경(+옵션 ~/.prism_key)."""
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
    # BizRouter 키(별도)
    rkey = (data.get("router_api_key") or "").strip()
    if rkey:
        os.environ["PRISM_ROUTER_KEY"] = rkey
        if data.get("persist"):
            try:
                with open(_ROUTER_KEY_PATH, "w", encoding="utf-8") as f:
                    f.write(rkey)
                os.chmod(_ROUTER_KEY_PATH, 0o600)
            except Exception:
                pass
    elif data.get("forget_router"):
        os.environ.pop("PRISM_ROUTER_KEY", None)
        try:
            os.remove(_ROUTER_KEY_PATH)
        except OSError:
            pass
    model = (data.get("model") or "").strip()
    base = (data.get("base_url") or "").strip()
    reasoning = (data.get("reasoning") or "").strip()
    has_sp = "system_prompt" in data
    slot_keys = ("text_provider", "text_model", "vision_provider", "vision_model")
    has_slot = any(k in data for k in slot_keys)
    if model or base or reasoning or has_sp or has_slot:
        cfg = Config.load()
        if base:
            cfg.set_base_url(base)
        if model:
            cfg.model = model
        if reasoning:
            cfg.reasoning_effort = reasoning
        if has_sp:
            cfg.system_prompt = (data.get("system_prompt") or "").strip()
        for k in slot_keys:
            if k in data:
                setattr(cfg, k, (data.get(k) or "").strip())
        try:
            cfg.save_template()                   # config.json 갱신(키는 저장 안 함)
        except Exception:
            pass
    sync_prompt()
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
        elif self.path.startswith("/vendor/"):
            self._send_vendor(self.path.split("?", 1)[0].rsplit("/", 1)[-1])
        else:
            self._send(200, PAGE)

    _VENDOR_CT = {
        ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".woff2": "font/woff2",
        ".woff": "font/woff",
    }

    def _send_vendor(self, name):
        safe = os.path.basename(name)
        ext = os.path.splitext(safe)[1].lower()
        path = os.path.join(os.path.dirname(__file__), "vendor", safe)
        if ext not in self._VENDOR_CT or not os.path.isfile(path):
            self._send(404, "not found")
            return
        with open(path, "rb") as f:
            self._send(200, f.read(), self._VENDOR_CT[ext])

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
<title>Prism</title>
<meta name="description" content="이미지·텍스트·엑셀에서 리드문·엔티티·인텐트·콘텐츠 카테고리를 추출하는 콘텐츠 메타 도구">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Crect width='24' height='24' rx='6' fill='%235b52ff'/%3E%3Cpath d='M12 4l1.7 5L19 12l-5.3 1.7L12 19l-1.7-5.3L5 12l5.3-1.7z' fill='%23fff'/%3E%3C/svg%3E">
<link href="/vendor/pretendard.css" rel="stylesheet">
<script src="/vendor/tailwind.js"></script>
<script>
  tailwind.config = {
    theme: { extend: {
      fontFamily: {
        sans: ['"Pretendard Variable"', 'Pretendard', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
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
      group: '뉴스',
      imgTitle: '', imgCaption: '',
      txtTitle: '', txtBody: '',
      fileLabel: '선택된 파일 없음', excelLabel: '선택된 파일 없음',

      // 설정(키 / 모델 슬롯 / 추론강도 / 추가 지시) — 우측 설정 패널
      cfg: { hasKey: false, model: '', persisted: false, forcedMock: false, hasRouterKey: false },
      cfgKey: '', cfgModel: '', cfgPersist: true, cfgMsg: '', cfgBusy: false,
      models: [], modelsMsg: '',
      reasoning: 'default', systemPrompt: '', prefMsg: '',
      reasoningOpts: [{ id: 'low', label: 'Low' }, { id: 'default', label: 'Medium' }, { id: 'high', label: 'High' }],

      // BizRouter(통합 라우터) 키 + 모델 슬롯
      rKey: '', rMsg: '',
      textProvider: 'solar', textModel: '',
      visionProvider: 'upstage_ie', visionModel: '',
      slotMsg: '',
      routerTextModels: ['openai/gpt-5.4', 'openai/gpt-5.4-mini', 'anthropic/claude-sonnet-4.6',
        'anthropic/claude-opus-4.6', 'google/gemini-2.5-pro', 'google/gemini-2.5-flash', 'deepseek/deepseek-v3.2'],
      routerVisionModels: ['google/gemini-2.5-flash', 'google/gemini-2.5-pro', 'openai/gpt-5.4',
        'openai/gpt-5-mini', 'anthropic/claude-sonnet-4.6', 'anthropic/claude-opus-4.6'],

      init() {
        this.refreshConfig();
        fetch('/vocab').then(r => r.json()).then(j => { if (j.groups && j.groups.length) this.groups = j.groups; }).catch(() => {});
      },
      get tabLabel() { return (this.tabItems.find(t => t.id === this.activeTabId) || {}).label || ''; },
      get textReady() { return this.textProvider === 'router' ? !!this.cfg.hasRouterKey : !!this.cfg.hasKey; },
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
          if (this.cfg.reasoning) this.reasoning = this.cfg.reasoning;
          if (typeof this.cfg.systemPrompt === 'string') this.systemPrompt = this.cfg.systemPrompt;
          if (this.cfg.textProvider) this.textProvider = this.cfg.textProvider;
          if (typeof this.cfg.textModel === 'string' && this.cfg.textModel) this.textModel = this.cfg.textModel;
          if (this.cfg.visionProvider) this.visionProvider = this.cfg.visionProvider;
          if (typeof this.cfg.visionModel === 'string' && this.cfg.visionModel) this.visionModel = this.cfg.visionModel;
        } catch (e) { /* noop */ }
      },
      // BizRouter 키
      async saveRouterKey() {
        this.rMsg = '저장 중…';
        try {
          const r = await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ router_api_key: this.rKey, persist: this.cfgPersist }) });
          this.cfg = await r.json(); this.rKey = '';
          this.rMsg = this.cfg.hasRouterKey ? '✓ 라우터 키 저장됨' : '저장 실패';
        } catch (e) { this.rMsg = '오류: ' + e; }
      },
      async forgetRouterKey() {
        try { const r = await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ forget_router: true }) }); this.cfg = await r.json(); this.rMsg = '라우터 키 삭제됨'; }
        catch (e) { this.rMsg = '오류: ' + e; }
      },
      // 텍스트 슬롯(메타 생성)
      setTextProvider(p) { this.textProvider = p; if (p === 'router' && !this.textModel) this.textModel = this.routerTextModels[0]; this.saveTextSlot(); },
      async saveTextSlot() {
        this.slotMsg = '저장 중…';
        const payload = { text_provider: this.textProvider };
        if (this.textProvider === 'router') payload.text_model = this.textModel;
        else if (this.cfgModel) payload.model = this.cfgModel;
        try { const r = await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload) }); this.cfg = await r.json(); this.slotMsg = '✓ 적용됨'; }
        catch (e) { this.slotMsg = '오류: ' + e; }
      },
      // 비전 슬롯(이미지 맥락 생성)
      setVisionProvider(p) { this.visionProvider = p; if (p === 'router' && !this.visionModel) this.visionModel = this.routerVisionModels[0]; this.saveVisionSlot(); },
      async saveVisionSlot() {
        this.slotMsg = '저장 중…';
        try { const r = await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ vision_provider: this.visionProvider, vision_model: this.visionModel }) });
          this.cfg = await r.json(); this.slotMsg = '✓ 적용됨'; }
        catch (e) { this.slotMsg = '오류: ' + e; }
      },
      async applyPrefs() {
        this.prefMsg = '저장 중…';
        try {
          await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reasoning: this.reasoning, system_prompt: this.systemPrompt }) });
          this.prefMsg = '✓ 적용됨';
        } catch (e) { this.prefMsg = '오류: ' + e; }
      },
      setReasoning(id) { this.reasoning = id; this.applyPrefs(); },
      async saveConfig() {
        this.cfgBusy = true; this.cfgMsg = '저장 중…';
        try {
          const r = await fetch('/config', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: this.cfgKey, model: this.cfgModel, persist: this.cfgPersist }) });
          this.cfg = await r.json(); this.cfgKey = '';
        } catch (e) { this.cfgMsg = '오류: ' + e; this.cfgBusy = false; return; }
        if (this.cfg.hasKey && !this.models.length) this.loadModels();   // 모델 목록 자동 로드
        if (this.cfg.hasKey) { await this.testConn(); }                  // 저장 즉시 모델 유효성 검증
        else { this.cfgMsg = '저장됨'; this.cfgBusy = false; }
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

      // 엑셀 배치 인포그래픽: 총건·등급분포·인텐트 상위·평균 리드문 길이
      get batchStats() {
        const its = (this.batchResult && this.batchResult.items) || [];
        const n = its.length;
        const g = its.filter((x) => x.grade === 'G').length;
        const counts = {};
        let lenSum = 0, lenN = 0;
        for (const x of its) {
          for (const t of (x.intent || [])) counts[t] = (counts[t] || 0) + 1;
          if (x.summary) { lenSum += x.summary.length; lenN += 1; }
        }
        const top = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 5)
          .map(([k, v]) => ({ k, v, pct: n ? Math.round((v / n) * 100) : 0 }));
        return {
          n, g, r: n - g,
          gPct: n ? Math.round((g / n) * 100) : 0,
          ents: its.reduce((s, x) => s + ((x.entities || []).length), 0),
          avgLen: lenN ? Math.round(lenSum / lenN) : 0,
          intents: top,
        };
      },

      async run() {
        this.loading = true; this.status = ''; this.result = null; this.batchResult = null;
        const fd = new FormData();
        let endpoint = '/run';
        if (this.activeTabId === 'image') {
          const fs = this.$refs.files.files;
          if (!fs.length) { this.status = '이미지를 선택하세요'; this.loading = false; return; }
          for (let i = 0; i < fs.length; i++) fd.append('image' + i, fs[i]);
          fd.append('displayServiceName', this.group);
          fd.append('title', this.imgTitle);
          fd.append('caption', this.imgCaption);
        } else if (this.activeTabId === 'excel') {
          const fs = this.$refs.excel.files;
          if (!fs.length) { this.status = '엑셀/CSV 파일을 선택하세요'; this.loading = false; return; }
          fd.append('file', fs[0]); endpoint = '/run-batch';
        } else {
          fd.append('displayServiceName', this.group);
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
<script defer src="/vendor/alpine.js"></script>
<style>
  [x-cloak]{display:none!important}
  *{-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
  /* ── 타이포 스케일(디자인 시스템 토큰) ──
     글꼴: Pretendard Variable. 영역별 굵기·크기 규칙:
       본문 14/400 · 라벨 11/600(uppercase) · 패널 제목 13/600
       타이틀바 13/600 · 히어로 18/600 · 수치 23/600(tabular) */
  :root{
    --ds-font:"Pretendard Variable",Pretendard,system-ui,-apple-system,sans-serif;
    --ctrl-h:42px;            /* 단일 컨트롤 높이(입력·셀렉트·드롭존 공통) */
    --ctrl-r:8px;             /* 컨트롤 radius(토큰 control) */
    --ctrl-px:12px;           /* 컨트롤 좌우 패딩 */
  }
  body{font-family:var(--ds-font);font-size:14px;line-height:1.5;letter-spacing:-.003em;
    background:
      radial-gradient(820px 420px at 100% -6%, rgba(91,82,255,.12), transparent 60%),
      radial-gradient(680px 360px at 0% 0%, rgba(210,255,149,.045), transparent 55%),
      #0b0a0f;
    background-attachment:fixed}
  ::selection{background:#5b52ff;color:#fff}
  ::-webkit-scrollbar{width:11px;height:11px}
  ::-webkit-scrollbar-thumb{background:rgba(255,255,255,.09);border-radius:8px;border:3px solid transparent;background-clip:content-box}
  ::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.18);background-clip:content-box}

  /* ── 폼 컨트롤(단일 규격) ──
     입력·셀렉트·드롭존은 같은 높이(--ctrl-h)·radius·패딩·테두리를 공유한다.
     textarea 는 다행이므로 높이만 자동, 나머지 토큰은 동일. */
  .field{width:100%;box-sizing:border-box;border-radius:var(--ctrl-r);background:#0d0c12;
    border:1px solid rgba(255,255,255,.10);color:#fff;font-family:var(--ds-font);font-size:14px;
    transition:border-color .15s,box-shadow .15s,background .15s}
  input.field,select.field{height:var(--ctrl-h);padding:0 var(--ctrl-px)}
  textarea.field{padding:11px var(--ctrl-px);line-height:1.55;min-height:96px;resize:vertical}
  .field::placeholder{color:#5b606b}
  .field:hover{border-color:rgba(255,255,255,.18)}
  .field:focus{outline:none;border-color:#5b52ff;box-shadow:0 0 0 3px rgba(91,82,255,.22);background:#0b0a0f}
  select.field{appearance:none;-webkit-appearance:none;padding-right:34px;cursor:pointer;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%239aa0aa' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
    background-repeat:no-repeat;background-position:right 11px center}

  /* 드롭존(파일 업로드) — .field 와 동일 규격. 점선 테두리·우측 버튼만 다름 */
  .dropzone{display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;
    box-sizing:border-box;height:var(--ctrl-h);padding:0 6px 0 var(--ctrl-px);
    border-radius:var(--ctrl-r);border:1px dashed rgba(255,255,255,.16);background:#0d0c12;
    cursor:pointer;font-size:14px;color:#9aa0aa;transition:border-color .15s,background .15s}
  .dropzone:hover{border-color:rgba(91,82,255,.55);background:#0b0a0f}
  .dropzone .pick{flex:none;display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 12px;
    border-radius:6px;background:rgba(255,255,255,.08);color:#fff;font-size:12px;font-weight:600}
  .dropzone .name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

  /* 카드: 토큰 유지 + 미세 입체(상단 하이라이트)·호버 리프트 */
  .card{box-shadow:inset 0 1px 0 rgba(255,255,255,.045);
    transition:transform .2s cubic-bezier(.32,.72,0,1),border-color .2s}
  .card:hover{transform:translateY(-1px);border-color:rgba(255,255,255,.14)}

  /* 리드문 hero */
  .lead{position:relative;overflow:hidden;
    background:linear-gradient(180deg,rgba(91,82,255,.10),rgba(91,82,255,.02))!important;
    border-color:rgba(91,82,255,.24)!important}
  .lead::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:#5b52ff}

  /* 사이드바 활성 항목 좌측 액센트 */
  .navitem.active::before{content:"";position:absolute;left:-12px;top:50%;transform:translateY(-50%);
    width:3px;height:18px;border-radius:2px;background:#5b52ff}

  /* primary 버튼 미세 그라데이션 */
  .btn-primary{background:linear-gradient(180deg,#6760ff,#5b52ff)!important;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.14),0 8px 22px -10px rgba(91,82,255,.65);
    transition:filter .15s,transform .08s}
  .btn-primary:hover{filter:brightness(1.07)}
  .btn-primary:active{transform:translateY(1px)}

  /* 라벨 (위계·여백 리듬) */
  .lbl{display:block;margin-bottom:7px;font-size:11px;font-weight:600;letter-spacing:.05em;
    text-transform:uppercase;color:#6e7191}

  /* 칩 (엔티티/인텐트/카테고리 시각 구분) */
  .chip{display:inline-flex;align-items:center;gap:5px;border-radius:7px;padding:3px 10px;
    font-size:12px;font-weight:500;line-height:1.5;border:1px solid transparent;
    transition:border-color .15s,background .15s,transform .1s}
  .chip:hover{transform:translateY(-1px)}
  .chip-ent{background:linear-gradient(180deg,rgba(91,82,255,.18),rgba(91,82,255,.07));
    border-color:rgba(91,82,255,.34);color:#c4beff}
  .chip-ent::before{content:"";width:5px;height:5px;border-radius:50%;background:#7c74ff;flex:none}
  .chip-int{background:rgba(76,185,167,.12);border-color:rgba(76,185,167,.26);color:#84dccc}
  .chip-cat{background:rgba(255,255,255,.05);border-color:rgba(255,255,255,.09);color:#c9ccd3}

  /* 등급 pill */
  .gpill{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:3px 11px;font-size:12px;font-weight:600}
  .gpill .d{width:6px;height:6px;border-radius:50%;background:currentColor;box-shadow:0 0 7px currentColor}
  .gpill-g{background:rgba(52,211,153,.13);color:#5fe0ad}
  .gpill-r{background:rgba(251,113,133,.13);color:#ff9bab}

  /* 테이블 (엑셀 결과) */
  .tbl{width:100%;border-collapse:separate;border-spacing:0;font-size:13px}
  .tbl th{text-align:left;font-weight:600;font-size:11px;letter-spacing:.05em;text-transform:uppercase;
    color:#6e7191;padding:9px 12px;background:rgba(255,255,255,.025)}
  .tbl td{padding:11px 12px;border-top:1px solid rgba(255,255,255,.06);vertical-align:top;color:#c9ccd3}
  .tbl tbody tr{transition:background .12s}
  .tbl tbody tr:hover{background:rgba(255,255,255,.035)}

  /* 모달 */
  .modal-bg{backdrop-filter:blur(7px);-webkit-backdrop-filter:blur(7px);background:rgba(4,4,8,.6)}
  .modal{box-shadow:0 26px 72px -22px rgba(0,0,0,.85),inset 0 1px 0 rgba(255,255,255,.05)}

  /* details 토글 마커 */
  details>summary{list-style:none}
  details>summary::-webkit-details-marker{display:none}
  details>summary::before{content:"\203A";display:inline-block;width:1em;margin-right:5px;
    transition:transform .15s;color:#6e7191}
  details[open]>summary::before{transform:rotate(90deg)}

  /* 빈 상태 */
  .empty{border:1px dashed rgba(255,255,255,.10);border-radius:12px;padding:40px 24px;text-align:center;color:#6e7191}

  /* 고급 폴리시 */
  html{scroll-behavior:smooth}
  .tnum{font-variant-numeric:tabular-nums}
  .lead,h1,.panel-hd b,.drow .v p{text-wrap:pretty}
  :where(button,a,[role=tab],select,summary):focus-visible{outline:2px solid rgba(124,116,255,.7);
    outline-offset:2px;border-radius:8px}
  /* 노이즈 오버레이 — 평면감 제거(은은) */
  .noise{position:fixed;inset:0;z-index:1;pointer-events:none;opacity:.025;mix-blend-mode:overlay;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='160' height='160' filter='url(%23n)'/%3E%3C/svg%3E")}
  /* 스켈레톤 로딩 */
  .skel{position:relative;overflow:hidden;background:rgba(255,255,255,.04);border-radius:8px}
  .skel::after{content:"";position:absolute;inset:0;
    background:linear-gradient(90deg,transparent,rgba(255,255,255,.06),transparent);
    transform:translateX(-100%);animation:shimmer 1.4s infinite}
  @keyframes shimmer{100%{transform:translateX(100%)}}

  /* 패널 (시안 C — 구조·패널형) */
  .panel{border:1px solid rgba(255,255,255,.08);border-radius:14px;background:#141318;overflow:hidden;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.045);transition:transform .2s cubic-bezier(.32,.72,0,1),border-color .2s}
  .panel:hover{transform:translateY(-1px);border-color:rgba(255,255,255,.14)}
  .panel-hd{display:flex;align-items:center;justify-content:space-between;gap:10px;
    padding:13px 18px;border-bottom:1px solid rgba(255,255,255,.07);background:rgba(255,255,255,.018)}
  .panel-hd b{color:#fff;font-size:13px;font-weight:600;letter-spacing:.01em}
  .panel-hd .meta{font-size:12px;color:#6e7191}
  .panel-bd{padding:18px}
  .drow{display:grid;grid-template-columns:124px 1fr;gap:16px;padding:15px 18px;
    border-bottom:1px solid rgba(255,255,255,.05);align-items:start}
  .drow:last-child{border-bottom:0}
  .drow .k{font-size:12px;font-weight:600;color:#6e7191;padding-top:3px}
  .drow .v{min-width:0}

  /* ── Playground 3분할 셸 ── */
  .shell{display:grid;grid-template-columns:236px minmax(0,1fr) 332px;height:100dvh;overflow:hidden}
  .pane{display:flex;flex-direction:column;min-width:0;min-height:0}
  .pane+.pane{border-left:1px solid rgba(255,255,255,.07)}
  /* 타이틀바: 내용 영역과 명확히 분리(별도 배경·하단 경계). 버튼 없음 — 제목/상태만. */
  .titlebar{flex:none;height:53px;display:flex;align-items:center;gap:9px;padding:0 18px;
    border-bottom:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.022);
    font-size:13px;font-weight:600;color:#fff;letter-spacing:.01em}
  .titlebar .sub{font-weight:500;color:#6e7191;font-size:12px}
  .titlebar .dot{width:7px;height:7px;border-radius:50%;flex:none}
  /* 로고: 마크(분광 프리즘) + 워드마크 + 기능 태그 */
  .logo-mark{flex:none;width:27px;height:27px;border-radius:8px;display:flex;align-items:center;justify-content:center;
    background:linear-gradient(150deg,#6760ff,#4a42e0);
    box-shadow:0 5px 14px -5px rgba(91,82,255,.75),inset 0 1px 0 rgba(255,255,255,.2)}
  .logo-mark svg{width:17px;height:17px}
  .logo-word{font-size:16px;font-weight:700;letter-spacing:-.02em;color:#fff}
  .logo-sub{font-size:11px;font-weight:500;color:#6e7191;letter-spacing:0;margin-left:-2px}
  .pbody{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden}
  .pbody.pad{padding:18px}
  .pbody.center{padding:26px 30px}

  /* 좌측 내비 그룹 라벨 */
  .navgrp{padding:0 12px;margin:18px 0 6px;font-size:11px;font-weight:600;letter-spacing:.06em;
    text-transform:uppercase;color:#565b66}

  /* 추론강도 세그먼트 */
  .seg{display:flex;gap:3px;padding:3px;border-radius:9px;background:#0d0c12;border:1px solid rgba(255,255,255,.09)}
  .seg button{flex:1;border-radius:6px;padding:6px 0;font-size:12px;font-weight:600;color:#8b909b;
    transition:color .15s,background .15s}
  .seg button.on{background:rgba(91,82,255,.22);color:#c8c3ff;box-shadow:inset 0 1px 0 rgba(255,255,255,.06)}
  .seg button:not(.on):hover{color:#fff}

  /* Configuration 패널 구획 */
  .cfgsec{padding:18px;border-bottom:1px solid rgba(255,255,255,.06)}
  .cfgsec:last-child{border-bottom:0}

  /* 히어로 빈 상태 */
  .hero{display:flex;flex-direction:column;align-items:center;justify-content:center;
    text-align:center;padding:64px 24px;border:1px dashed rgba(255,255,255,.10);border-radius:16px;
    background:radial-gradient(420px 200px at 50% 0%,rgba(91,82,255,.08),transparent 70%)}
  .hero .orb{width:54px;height:54px;border-radius:16px;display:flex;align-items:center;justify-content:center;
    background:linear-gradient(180deg,rgba(91,82,255,.28),rgba(91,82,255,.08));
    border:1px solid rgba(91,82,255,.3);box-shadow:0 12px 30px -12px rgba(91,82,255,.6);color:#c8c3ff}
  .schip{display:inline-flex;align-items:center;gap:6px;border-radius:8px;padding:7px 12px;font-size:12.5px;
    font-weight:500;color:#c9ccd3;background:rgba(255,255,255,.045);border:1px solid rgba(255,255,255,.09);
    cursor:pointer;transition:border-color .15s,background .15s,transform .1s}
  .schip:hover{border-color:rgba(91,82,255,.45);background:rgba(91,82,255,.10);transform:translateY(-1px)}

  /* 인포그래픽 — 통계 타일·분포 바·도넛 */
  .tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
  .tile{border-radius:12px;padding:13px 14px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.07)}
  .tile .n{font-size:23px;font-weight:600;color:#fff;line-height:1.1;letter-spacing:-.01em}
  .tile .t{margin-top:3px;font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:#6e7191}
  .bar{display:grid;grid-template-columns:96px 1fr 38px;align-items:center;gap:10px}
  .bar .lab{font-size:12.5px;color:#c9ccd3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .track{height:8px;border-radius:6px;background:rgba(255,255,255,.06);overflow:hidden}
  .track .fill{height:100%;border-radius:6px;background:linear-gradient(90deg,#5b52ff,#7c74ff)}
  .bar .pc{font-size:12px;color:#8b909b;text-align:right}
  .ring{width:108px;height:108px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex:none}
  .ring i{width:78px;height:78px;border-radius:50%;background:#141318;display:flex;flex-direction:column;
    align-items:center;justify-content:center}
  .ring .pv{font-size:21px;font-weight:600;color:#fff;line-height:1}
  .ring .pl{font-size:10px;color:#6e7191;margin-top:2px}
</style>
</head>
<body class="text-body antialiased">
<div class="noise" aria-hidden="true"></div>
<div x-data="prismApp()" class="shell">

  <!-- ━━━━━ 좌측 페인 · 내비게이션 ━━━━━ -->
  <aside class="pane">
    <div class="titlebar">
      <span class="logo-mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke-linecap="round" stroke-linejoin="round">
          <path d="M10 4 4 20h12z" stroke="#fff" stroke-width="1.7"/>
          <path d="M14.5 13h6" stroke="#d2ff95" stroke-width="1.7"/>
          <path d="M14.5 16.5h6" stroke="#a8d4ff" stroke-width="1.7"/>
        </svg>
      </span>
      <span class="logo-word">Prism</span>
    </div>
    <div class="pbody pad">
      <div class="navgrp">입력</div>
      <nav class="space-y-0.5" aria-label="입력 방식">
        <template x-for="tabItem in tabItems" x-bind:key="tabItem.id">
          <button type="button" x-on:click="selectTab(tabItem.id)"
            x-bind:aria-current="activeTabId === tabItem.id ? 'page' : 'false'"
            class="navitem relative flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors"
            x-bind:class="activeTabId === tabItem.id ? 'active bg-white/[0.07] text-white font-medium' : 'text-body hover:bg-white/[0.04] hover:text-white'">
            <svg x-show="tabItem.id === 'image'" class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.6-3.6a2 2 0 0 0-2.8 0L6 20"/></svg>
            <svg x-show="tabItem.id === 'text'" class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7V5h16v2M9 5v14m-3 0h6"/></svg>
            <svg x-show="tabItem.id === 'excel'" class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/></svg>
            <span x-text="tabItem.label"></span>
          </button>
        </template>
      </nav>
      <div class="navgrp">산출</div>
      <a href="/report" target="_blank" rel="noreferrer"
        class="navitem relative flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm text-body transition-colors hover:bg-white/[0.04] hover:text-white">
        <svg class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3v18h18M8 14v3m4-7v7m4-11v11"/></svg>
        <span>전체 리포트</span>
      </a>
    </div>
  </aside>

  <!-- ━━━━━ 가운데 페인 · 캔버스 ━━━━━ -->
  <main class="pane">
    <div class="titlebar">
      <span x-text="tabLabel + ' 입력'"></span>
      <span class="ml-auto inline-flex items-center gap-1.5">
        <span class="dot" x-bind:class="(textReady && !cfg.forcedMock) ? 'bg-solar' : 'bg-amber-400'"></span>
        <span class="sub" x-text="cfg.forcedMock ? 'MOCK(강제)' : (textReady ? (textProvider === 'router' ? 'BizRouter 연결됨' : 'Solar 연결됨') : 'MOCK · 키 미설정')"></span>
      </span>
    </div>
    <div class="pbody center">
      <div class="mx-auto max-w-3xl">

        <!-- 입력 카드 -->
        <section class="panel">
          <div class="panel-bd">
          <!-- 콘텐츠 그룹: 입력 시 지정(이미지·텍스트 공용). 엑셀은 컬럼에서 자동 -->
          <div x-show="activeTabId !== 'excel'" x-cloak class="mb-4">
            <label class="lbl">콘텐츠 그룹</label>
            <select x-model="group" class="field">
              <template x-for="g in groups" x-bind:key="g"><option x-bind:value="g" x-text="g"></option></template>
            </select>
          </div>
          <!-- 이미지 -->
          <div x-show="activeTabId === 'image'" x-cloak class="space-y-4">
            <div>
              <label class="lbl">이미지 (여러 장이면 하나의 콘텐츠로 통합)</label>
              <label class="dropzone">
                <span class="name" x-text="fileLabel"></span>
                <span class="pick">
                  <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>
                  파일 선택
                </span>
                <input x-ref="files" type="file" accept="image/*" multiple class="sr-only" x-on:change="onFiles($event)">
              </label>
            </div>
            <div><label class="lbl">제목 (선택)</label>
              <input x-model="imgTitle" class="field" placeholder="없으면 이미지에서 추론"></div>
            <div><label class="lbl">캡션 (선택)</label>
              <input x-model="imgCaption" class="field" placeholder="사진 설명이 있으면 함께 참조"></div>
          </div>
          <!-- 텍스트 -->
          <div x-show="activeTabId === 'text'" x-cloak class="space-y-4">
            <div><label class="lbl">제목 (title)</label>
              <input x-model="txtTitle" class="field" placeholder="기사 제목"></div>
            <div><label class="lbl">본문 (body)</label>
              <textarea x-model="txtBody" rows="5" class="field" placeholder="본문 내용"></textarea></div>
          </div>
          <!-- 엑셀 -->
          <div x-show="activeTabId === 'excel'" x-cloak class="space-y-4">
            <div>
              <label class="lbl">엑셀 / CSV (제목·본문 컬럼 자동 매핑)</label>
              <label class="dropzone">
                <span class="name" x-text="excelLabel"></span>
                <span class="pick">
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
              class="btn-primary inline-flex items-center gap-2 rounded-lg bg-violet px-5 py-2.5 text-sm font-medium text-white disabled:opacity-50">
              <svg x-show="loading" x-cloak class="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z"/></svg>
              <span x-text="loading ? '실행 중' : '추출 실행'"></span>
            </button>
            <span class="text-xs text-muted">모델 · 추론 강도는 우측 설정 패널에서 변경</span>
            <span aria-live="polite" class="ml-auto text-sm text-rose-400" x-text="status"></span>
          </div>
          </div>
        </section>

        <!-- 빈 상태 (히어로 + 추천 칩) -->
        <div x-show="!result && !batchResult && !loading" x-cloak class="mt-6">
          <div class="hero">
            <div class="orb">
              <svg class="h-7 w-7" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l2.2 6.3L21 11.5l-6.8 2.2L12 21l-2.2-7.3L3 11.5l6.8-2.2z"/></svg>
            </div>
            <h2 class="mt-4 text-lg font-semibold text-white">콘텐츠에서 리드문과 메타를 추출합니다</h2>
            <p class="mt-1.5 max-w-md text-sm text-muted">이미지·텍스트·엑셀을 입력하면 리드문(요약 한 문장)·엔티티·인텐트·콘텐츠 카테고리가 한 방향으로 정리됩니다.</p>
            <div class="mt-5 flex flex-wrap items-center justify-center gap-2">
              <button type="button" class="schip" x-on:click="selectTab('image')">
                <svg class="h-3.5 w-3.5 text-violet" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.6-3.6a2 2 0 0 0-2.8 0L6 20"/></svg>
                이미지에서 추출
              </button>
              <button type="button" class="schip" x-on:click="selectTab('text')">
                <svg class="h-3.5 w-3.5 text-violet" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7V5h16v2M9 5v14m-3 0h6"/></svg>
                기사 본문 붙여넣기
              </button>
              <button type="button" class="schip" x-on:click="selectTab('excel')">
                <svg class="h-3.5 w-3.5 text-violet" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 3v18"/></svg>
                엑셀 일괄 처리
              </button>
            </div>
          </div>
        </div>

        <!-- 로딩 스켈레톤 -->
        <div x-show="loading" x-cloak class="mt-6">
          <div class="panel">
            <div class="panel-hd"><b>처리 중</b><span class="skel" style="width:92px;height:18px"></span></div>
            <div class="drow"><div class="k">리드문</div><div class="v"><div class="skel" style="height:46px"></div></div></div>
            <div class="drow"><div class="k">엔티티</div><div class="v"><div class="skel" style="width:62%;height:22px"></div></div></div>
            <div class="drow"><div class="k">인텐트</div><div class="v"><div class="skel" style="width:46%;height:22px"></div></div></div>
            <div class="drow"><div class="k">콘텐츠 카테고리</div><div class="v"><div class="skel" style="width:74%;height:22px"></div></div></div>
          </div>
        </div>

        <!-- 엑셀 배치 결과 + 인포그래픽 -->
        <div x-show="batchResult" x-cloak x-transition.opacity.duration.250ms class="mt-6 space-y-4">
          <!-- 집계 인포그래픽 -->
          <section class="panel">
            <div class="panel-hd"><b>집계</b><span class="meta tnum" x-text="batchResult ? (batchStats.n + '건 분석') : ''"></span></div>
            <div class="panel-bd space-y-5">
              <div class="tiles">
                <div class="tile"><div class="n tnum" x-text="batchStats.n"></div><div class="t">총 건수</div></div>
                <div class="tile"><div class="n tnum" x-text="batchStats.g"></div><div class="t">유통가능 G</div></div>
                <div class="tile"><div class="n tnum" x-text="batchStats.ents"></div><div class="t">엔티티 수</div></div>
                <div class="tile"><div class="n tnum" x-text="batchStats.avgLen"></div><div class="t">평균 리드문(자)</div></div>
              </div>
              <div class="flex items-center gap-6">
                <div class="ring" x-bind:style="'background:conic-gradient(#5b52ff ' + batchStats.gPct + '%, rgba(255,255,255,.07) 0)'">
                  <i><span class="pv tnum" x-text="batchStats.gPct + '%'"></span><span class="pl">유통가능</span></i>
                </div>
                <div class="min-w-0 flex-1 space-y-2.5">
                  <div class="lbl" style="margin-bottom:2px">인텐트 분포 (상위 5)</div>
                  <template x-for="it in batchStats.intents" x-bind:key="it.k">
                    <div class="bar">
                      <span class="lab" x-text="it.k"></span>
                      <span class="track"><span class="fill" x-bind:style="'width:' + Math.max(it.pct, 4) + '%'"></span></span>
                      <span class="pc tnum" x-text="it.v + '건'"></span>
                    </div>
                  </template>
                  <div x-show="!batchStats.intents.length" class="text-xs text-muted">인텐트 데이터 없음</div>
                </div>
              </div>
            </div>
          </section>

          <section class="panel">
            <div class="panel-hd">
              <div class="flex items-center gap-2">
                <b>행별 결과</b>
                <span x-show="batchResult && batchResult.mock" class="inline-flex items-center rounded-md bg-amber-500/15 px-2 py-0.5 text-xs font-semibold text-amber-300">MOCK</span>
              </div>
              <span class="meta">우측 산출 · 전체 리포트 참조</span>
            </div>
            <div class="overflow-auto">
              <table class="tbl">
                <thead>
                  <tr><th>제목</th><th>리드문</th><th>엔티티</th><th>등급</th></tr>
                </thead>
                <tbody>
                  <template x-for="(it, i) in (batchResult ? batchResult.items : [])" x-bind:key="i">
                    <tr>
                      <td class="text-white" x-text="it.title || '—'"></td>
                      <td x-text="it.summary || '—'"></td>
                      <td><div class="flex flex-wrap gap-1"><template x-for="e in (it.entities || [])" x-bind:key="e"><span class="chip chip-ent" x-text="e"></span></template><span x-show="!(it.entities||[]).length">—</span></div></td>
                      <td><span class="gpill" x-bind:class="it.grade === 'G' ? 'gpill-g' : 'gpill-r'"><span class="d"></span><span x-text="it.grade || '—'"></span></span></td>
                    </tr>
                  </template>
                </tbody>
              </table>
            </div>
            <p x-show="batchResult && batchResult.mapping" class="px-4 py-2.5 text-xs text-muted" x-text="batchResult && batchResult.mapping ? ('매핑: ' + Object.entries(batchResult.mapping).map(e=>e[0]+'←'+e[1]).join(' · ')) : ''"></p>
          </section>
        </div>

        <!-- 단건 결과 -->
        <div x-show="result" x-cloak x-transition.opacity.duration.250ms class="mt-6 space-y-4">
          <section class="panel">
            <div class="panel-hd">
              <b>아이템 메타</b>
              <div class="flex items-center gap-2">
                <span x-show="q.finalGrade === 'G'" class="gpill gpill-g"><span class="d"></span>유통가능 · G</span>
                <span x-show="q.finalGrade !== 'G'" class="gpill gpill-r"><span class="d"></span>차단 · R</span>
                <span class="meta" x-text="result ? (result.output.routing.content_track + ' · ' + result.source) : ''"></span>
              </div>
            </div>
            <div class="drow">
              <div class="k">리드문</div>
              <div class="v"><p class="text-[15px] leading-relaxed text-white" x-text="im.summary || '(빈 값 — 차단되었거나 본문 부족)'"></p></div>
            </div>
            <div class="drow">
              <div class="k">엔티티</div>
              <div class="v flex flex-wrap gap-1.5">
                <template x-for="x in (im.entities || [])" x-bind:key="x"><span class="chip chip-ent" x-text="x"></span></template>
                <span x-show="!(im.entities || []).length" class="text-xs text-muted">—</span>
              </div>
            </div>
            <div class="drow">
              <div class="k">인텐트</div>
              <div class="v flex flex-wrap gap-1.5">
                <template x-for="x in (im.intent || [])" x-bind:key="x"><span class="chip chip-int" x-text="x"></span></template>
                <span x-show="!(im.intent || []).length" class="text-xs text-muted">—</span>
              </div>
            </div>
            <div class="drow">
              <div class="k">콘텐츠 카테고리</div>
              <div class="v flex flex-wrap gap-1.5">
                <template x-for="x in contentCats" x-bind:key="x"><span class="chip chip-cat" x-text="x"></span></template>
                <span x-show="!contentCats.length" class="text-xs text-muted">—</span>
              </div>
            </div>
          </section>

          <!-- 이미지 추출 신호 -->
          <section x-show="result && result.signals && result.signals.length" x-cloak class="panel">
            <div class="panel-hd"><b>이미지 추출 신호</b><span class="meta tnum" x-text="result ? (result.signals.length + '장') : ''"></span></div>
            <div class="panel-bd space-y-3">
              <template x-for="(s, i) in (result ? result.signals : [])" x-bind:key="i">
                <div class="border-l border-white/[0.10] pl-3">
                  <div class="text-xs font-semibold text-white" x-text="'이미지 ' + (i + 1)"></div>
                  <div class="mt-1 flex gap-2 text-sm text-body">
                    <svg class="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
                    <span x-text="s.vision || '—'"></span>
                  </div>
                  <div class="flex gap-2 text-sm text-body">
                    <svg class="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7V5h16v2M9 5v14m-3 0h6"/></svg>
                    <span x-text="s.ocr || '—'"></span>
                  </div>
                  <p x-show="s.note" x-cloak class="mt-1 text-xs text-amber-300/90" x-text="s.note"></p>
                </div>
              </template>
            </div>
          </section>

          <!-- 상세 -->
          <section class="panel">
            <div class="panel-hd"><b>상세</b>
              <a href="/report" target="_blank" rel="noreferrer"
                 class="inline-flex items-center gap-1.5 text-xs font-medium text-[#b9b3ff] transition-colors hover:text-white">
                전체 리포트 열기
                <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M7 7h10v10"/></svg>
              </a>
            </div>
            <div class="panel-bd">
              <details class="group">
                <summary class="cursor-pointer text-sm text-body transition-colors hover:text-white">합성된 Content (이미지 → 4필드)</summary>
                <pre class="mt-2 overflow-auto rounded-lg border border-white/[0.08] bg-canvas p-3 font-mono text-xs text-body" x-text="result ? JSON.stringify(result.content, null, 2) : ''"></pre>
              </details>
              <details class="group mt-2">
                <summary class="cursor-pointer text-sm text-body transition-colors hover:text-white">원본 출력 JSON</summary>
                <pre class="mt-2 overflow-auto rounded-lg border border-white/[0.08] bg-canvas p-3 font-mono text-xs text-body" x-text="result ? JSON.stringify(result.output, null, 2) : ''"></pre>
              </details>
            </div>
          </section>
        </div>
      </div>
    </div>
  </main>

  <!-- ━━━━━ 우측 페인 · Configuration ━━━━━ -->
  <aside class="pane">
    <div class="titlebar"><span>설정</span></div>
    <div class="pbody">

      <!-- Upstage Solar 키 -->
      <div class="cfgsec">
        <label class="lbl">Upstage Solar 키</label>
        <input x-model="cfgKey" type="password" class="field" placeholder="up_xxxxxxxx" autocomplete="off">
        <label class="mt-3 flex cursor-pointer items-center gap-2 text-[13px] text-body">
          <input type="checkbox" x-model="cfgPersist" class="h-4 w-4 rounded border-white/20 bg-canvas text-violet">
          이 기기에 저장 (<code class="text-muted">~/.prism_key</code>)
        </label>
        <div class="mt-3 flex flex-wrap gap-2">
          <button type="button" x-on:click="saveConfig()" x-bind:disabled="cfgBusy"
            class="rounded-lg bg-violet px-3.5 py-1.5 text-[13px] font-medium text-white hover:bg-violet-hover disabled:opacity-50">저장</button>
          <button type="button" x-on:click="testConn()" x-bind:disabled="cfgBusy"
            class="rounded-lg border border-white/[0.10] px-3.5 py-1.5 text-[13px] font-medium text-white hover:bg-white/[0.05] disabled:opacity-50">연결 테스트</button>
          <button type="button" x-show="cfg.persisted" x-on:click="forgetKey()" x-bind:disabled="cfgBusy"
            class="rounded-lg border border-rose-500/30 px-3.5 py-1.5 text-[13px] font-medium text-rose-300 hover:bg-rose-500/10 disabled:opacity-50">키 삭제</button>
        </div>
        <div class="mt-2.5 flex items-center gap-2 text-xs">
          <span class="h-1.5 w-1.5 rounded-full" x-bind:class="cfg.hasKey ? 'bg-solar' : 'bg-amber-400'"></span>
          <span class="text-muted" x-text="cfgMsg || (cfg.hasKey ? ('키 설정됨' + (cfg.persisted ? ' · 저장됨' : '')) : '키 미설정 (현재 MOCK)')"></span>
        </div>
      </div>

      <!-- BizRouter(통합 라우터) 키 -->
      <div class="cfgsec">
        <label class="lbl">BizRouter 키 <span class="font-normal normal-case tracking-normal text-muted">· 멀티모달/타사 모델용(선택)</span></label>
        <input x-model="rKey" type="password" class="field" placeholder="sk-br-v1-…" autocomplete="off">
        <div class="mt-3 flex flex-wrap gap-2">
          <button type="button" x-on:click="saveRouterKey()"
            class="rounded-lg bg-violet px-3.5 py-1.5 text-[13px] font-medium text-white hover:bg-violet-hover">저장</button>
          <button type="button" x-show="cfg.routerPersisted" x-on:click="forgetRouterKey()"
            class="rounded-lg border border-rose-500/30 px-3.5 py-1.5 text-[13px] font-medium text-rose-300 hover:bg-rose-500/10">키 삭제</button>
        </div>
        <div class="mt-2.5 flex items-center gap-2 text-xs">
          <span class="h-1.5 w-1.5 rounded-full" x-bind:class="cfg.hasRouterKey ? 'bg-solar' : 'bg-white/20'"></span>
          <span class="text-muted" x-text="rMsg || (cfg.hasRouterKey ? '라우터 키 설정됨' : '미설정 (BizRouter 모델 사용 시 필요)')"></span>
        </div>
      </div>

      <!-- 텍스트 모델(메타 생성) -->
      <div class="cfgsec">
        <label class="lbl">텍스트 모델 <span class="font-normal normal-case tracking-normal text-muted">· 리드문·메타 생성</span></label>
        <div class="seg mb-2">
          <button type="button" x-on:click="setTextProvider('solar')" x-bind:class="textProvider === 'solar' ? 'on' : ''">Solar</button>
          <button type="button" x-on:click="setTextProvider('router')" x-bind:class="textProvider === 'router' ? 'on' : ''">BizRouter</button>
        </div>
        <!-- Solar 모델 -->
        <div x-show="textProvider === 'solar'">
          <select x-model="cfgModel" x-on:change="saveTextSlot()" class="field">
            <template x-for="m in modelOptions" x-bind:key="m"><option x-bind:value="m" x-text="m"></option></template>
            <template x-if="!modelOptions.length"><option value="" disabled>키 입력 후 모델 불러오기</option></template>
          </select>
          <button type="button" x-on:click="loadModels()" x-bind:disabled="cfgBusy"
            class="mt-2 w-full rounded-lg border border-white/[0.10] px-3 py-1.5 text-[13px] font-medium text-white hover:bg-white/[0.05] disabled:opacity-50">모델 불러오기</button>
          <span class="mt-1.5 block text-xs text-muted" x-text="modelsMsg"></span>
        </div>
        <!-- BizRouter 텍스트 모델 -->
        <div x-show="textProvider === 'router'" x-cloak>
          <select x-model="textModel" x-on:change="saveTextSlot()" class="field">
            <template x-for="m in routerTextModels" x-bind:key="m"><option x-bind:value="m" x-text="m"></option></template>
          </select>
          <p class="mt-1.5 text-xs text-muted" x-show="!cfg.hasRouterKey">BizRouter 키가 필요합니다.</p>
        </div>
      </div>

      <!-- 이미지 맥락 모델(비전) -->
      <div class="cfgsec">
        <label class="lbl">이미지 맥락 모델 <span class="font-normal normal-case tracking-normal text-muted">· 이미지 이해</span></label>
        <div class="seg mb-2">
          <button type="button" x-on:click="setVisionProvider('upstage_ie')" x-bind:class="visionProvider === 'upstage_ie' ? 'on' : ''">Upstage</button>
          <button type="button" x-on:click="setVisionProvider('router')" x-bind:class="visionProvider === 'router' ? 'on' : ''">BizRouter</button>
        </div>
        <div x-show="visionProvider === 'upstage_ie'">
          <p class="text-xs text-muted">Upstage Information Extraction · Solar 키 사용. 텍스트가 있는 이미지에 적합(순수 사진은 미검출 가능).</p>
        </div>
        <div x-show="visionProvider === 'router'" x-cloak>
          <select x-model="visionModel" x-on:change="saveVisionSlot()" class="field">
            <template x-for="m in routerVisionModels" x-bind:key="m"><option x-bind:value="m" x-text="m"></option></template>
          </select>
          <p class="mt-1.5 text-xs text-muted">멀티모달 모델로 순수 사진까지 이해. <span x-show="!cfg.hasRouterKey">BizRouter 키가 필요합니다.</span></p>
        </div>
        <span class="mt-1.5 block text-xs text-muted" aria-live="polite" x-text="slotMsg"></span>
      </div>

      <!-- 추론 강도 -->
      <div class="cfgsec">
        <label class="lbl">추론 강도 (Reasoning Effort)</label>
        <div class="seg">
          <template x-for="o in reasoningOpts" x-bind:key="o.id">
            <button type="button" x-on:click="setReasoning(o.id)"
              x-bind:class="reasoning === o.id ? 'on' : ''" x-text="o.label"></button>
          </template>
        </div>
        <p class="mt-1.5 text-xs text-muted">높일수록 추론 깊이는 늘고 속도는 느려집니다.</p>
      </div>

      <!-- System Prompt (추가 지시) -->
      <div class="cfgsec">
        <label class="lbl">System Prompt (추가 지시)</label>
        <textarea x-model="systemPrompt" x-on:blur="applyPrefs()" rows="4" class="field"
          placeholder="예) 리드문은 25자 이내로. 인물명은 직책과 함께 표기."></textarea>
        <div class="mt-2 flex items-center gap-2">
          <button type="button" x-on:click="applyPrefs()"
            class="rounded-lg border border-white/[0.10] px-3.5 py-1.5 text-[13px] font-medium text-white hover:bg-white/[0.05]">적용</button>
          <span class="text-xs text-muted" aria-live="polite" x-text="prefMsg"></span>
        </div>
        <p class="mt-2 text-xs text-muted">출력 스키마(리드문·엔티티·인텐트·콘텐츠 카테고리)는 유지하며 추출 방향만 조향합니다.</p>
      </div>

    </div>
  </aside>

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
    sync_prompt()                                     # config 의 추가 지시 반영
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
