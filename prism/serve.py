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
        else:
            self._send(200, PAGE)

    def do_POST(self):
        if not self.path.startswith("/run"):
            self._send(404, "not found")
            return
        ctype = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            if "multipart/form-data" in ctype:
                boundary = ctype.split("boundary=", 1)[1].strip()
                fields = _parse_multipart(body, boundary)
            else:
                fields = json.loads(body or b"{}")
            result = run_pipeline(fields, mock=self.server_mock)
            self._send(200, json.dumps(result, ensure_ascii=False),
                       "application/json; charset=utf-8")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False),
                       "application/json; charset=utf-8")


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
      tabItems: [{ id: 'image', label: '이미지 업로드' }, { id: 'text', label: '텍스트 입력' }],
      activeTabId: 'image',
      loading: false,
      status: '',
      result: null,
      imgGroup: '포토', imgTitle: '', imgCaption: '',
      txtGroup: '뉴스', txtTitle: '', txtBody: '',
      fileLabel: '선택된 파일 없음',

      selectTab(id) { this.activeTabId = id; this.status = ''; },
      onFiles(e) {
        const fs = e.target.files;
        this.fileLabel = fs.length ? (fs.length + '개 파일 선택됨') : '선택된 파일 없음';
      },

      get im() { return (this.result && this.result.output.item_meta) || {}; },
      get q() { return (this.result && this.result.output.quality_meta) || {}; },
      get entityCats() {
        const e = this.im.entity_categories || {};
        return Object.keys(e).map((k) => k + ' \\u2192 ' + e[k]);
      },

      async run() {
        this.loading = true; this.status = ''; this.result = null;
        const fd = new FormData();
        if (this.activeTabId === 'image') {
          const fs = this.$refs.files.files;
          if (!fs.length) { this.status = '이미지를 선택하세요'; this.loading = false; return; }
          for (let i = 0; i < fs.length; i++) fd.append('image' + i, fs[i]);
          fd.append('displayServiceName', this.imgGroup);
          fd.append('title', this.imgTitle);
          fd.append('caption', this.imgCaption);
        } else {
          fd.append('displayServiceName', this.txtGroup);
          fd.append('title', this.txtTitle);
          fd.append('body', this.txtBody);
        }
        try {
          const r = await fetch('/run', { method: 'POST', body: fd });
          const j = await r.json();
          if (j.error) { this.status = '오류: ' + j.error; }
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
        <span class="h-1.5 w-1.5 rounded-full" x-bind:class="(result && result.mock) ? 'bg-solar' : 'bg-violet'"></span>
        <span x-text="(result && result.mock) ? 'MOCK' : 'Solar'"></span>
      </span>
    </div>
  </header>

  <div class="flex">
    <!-- 좌측 사이드바 -->
    <aside class="hidden w-60 shrink-0 border-r border-white/[0.08] px-3 py-6 md:block">
      <div class="px-3 text-[11px] font-semibold uppercase tracking-wider text-muted">입력 방식</div>
      <nav class="mt-2 space-y-0.5" aria-label="입력 방식">
        <template x-for="tabItem in tabItems" x-bind:key="tabItem.id">
          <button type="button" x-on:click="selectTab(tabItem.id)"
            x-bind:aria-current="activeTabId === tabItem.id ? 'page' : 'false'"
            class="flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors"
            x-bind:class="activeTabId === tabItem.id ? 'bg-white/[0.07] text-white font-medium' : 'text-body hover:bg-white/[0.04] hover:text-white'">
            <!-- icon: image / type -->
            <svg x-show="tabItem.id === 'image'" class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.6-3.6a2 2 0 0 0-2.8 0L6 20"/></svg>
            <svg x-show="tabItem.id === 'text'" class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7V5h16v2M9 5v14m-3 0h6"/></svg>
            <span x-text="tabItem.label"></span>
          </button>
        </template>
      </nav>
      <div class="mt-6 px-3 text-[11px] font-semibold uppercase tracking-wider text-muted">산출</div>
      <div class="mt-2 space-y-0.5 px-3 text-sm text-body">
        <p class="py-1">리드문 · 엔티티</p>
        <p class="py-1">인텐트 · 카테고리</p>
      </div>
    </aside>

    <!-- 메인 -->
    <main class="min-w-0 flex-1 px-6 py-8 lg:px-10">
      <div class="mx-auto max-w-3xl">
        <h1 class="text-3xl font-semibold tracking-tight text-white">리드문 · 메타 추출</h1>
        <p class="mt-2 text-[15px] text-body">
          이미지(또는 텍스트)를 넣으면 OCR과 DocVision으로 내용을 추출하고, Solar로 리드문·엔티티·인텐트·콘텐츠 카테고리를 생성합니다.
        </p>
        <hr class="my-7 border-white/[0.08]">

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
                <input x-model="imgGroup" class="field" placeholder="포토/뉴스/스포츠"></div>
              <div><label class="mb-1.5 block text-xs font-medium text-muted">제목 (선택)</label>
                <input x-model="imgTitle" class="field" placeholder="없으면 이미지에서 추론"></div>
            </div>
            <div><label class="mb-1.5 block text-xs font-medium text-muted">캡션 (선택)</label>
              <input x-model="imgCaption" class="field" placeholder="사진 설명이 있으면 함께 참조"></div>
          </div>
          <!-- 텍스트 패널 -->
          <div x-show="activeTabId === 'text'" x-cloak class="space-y-4">
            <div class="grid grid-cols-2 gap-3">
              <div><label class="mb-1.5 block text-xs font-medium text-muted">콘텐츠 그룹 (displayServiceName)</label>
                <input x-model="txtGroup" class="field"></div>
              <div><label class="mb-1.5 block text-xs font-medium text-muted">제목 (title)</label>
                <input x-model="txtTitle" class="field" placeholder="기사 제목"></div>
            </div>
            <div><label class="mb-1.5 block text-xs font-medium text-muted">본문 (body)</label>
              <textarea x-model="txtBody" rows="4" class="field" placeholder="본문 내용"></textarea></div>
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

        <!-- 결과 -->
        <div x-show="result" x-cloak class="mt-6 space-y-4">
          <section class="rounded-lg border border-white/[0.08] bg-surface p-6">
            <div class="mb-4 flex flex-wrap items-center gap-2 text-xs">
              <span x-show="q.finalGrade === 'G'" class="inline-flex items-center gap-1.5 rounded-md bg-white/[0.06] px-2.5 py-1 font-medium text-white"><span class="h-1.5 w-1.5 rounded-full bg-emerald-400"></span>유통가능 · G</span>
              <span x-show="q.finalGrade !== 'G'" class="inline-flex items-center gap-1.5 rounded-md bg-white/[0.06] px-2.5 py-1 font-medium text-white"><span class="h-1.5 w-1.5 rounded-full bg-rose-400"></span>차단 · R</span>
              <span class="text-muted" x-text="result ? ('track=' + result.output.routing.content_track + ' · source=' + result.source) : ''"></span>
            </div>

            <label class="mb-1.5 block text-xs font-medium text-muted">리드문 (item_meta.intent)</label>
            <p class="rounded-lg border border-white/[0.08] bg-canvas p-3.5 text-[15px] leading-relaxed text-white"
               x-text="im.intent || '(빈 값 — 차단되었거나 본문 부족)'"></p>

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
                <label class="mb-1.5 block text-xs font-medium text-muted">인텐트 카테고리</label>
                <div class="flex flex-wrap gap-1.5">
                  <template x-for="x in (im.intent_categories || [])" x-bind:key="x">
                    <span class="inline-flex items-center rounded-md bg-white/[0.06] px-2.5 py-1 text-xs font-medium text-body" x-text="x"></span>
                  </template>
                  <span x-show="!(im.intent_categories || []).length" class="text-xs text-muted">—</span>
                </div>
              </div>
            </div>

            <div class="mt-4">
              <label class="mb-1.5 block text-xs font-medium text-muted">엔티티 카테고리</label>
              <div class="flex flex-wrap gap-1.5">
                <template x-for="x in entityCats" x-bind:key="x">
                  <span class="inline-flex items-center rounded-md bg-white/[0.06] px-2.5 py-1 text-xs font-medium text-body" x-text="x"></span>
                </template>
                <span x-show="!entityCats.length" class="text-xs text-muted">—</span>
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
    keyed = bool(IMG._api_key())
    mode = "MOCK(강제)" if a.mock else ("실모델" if keyed else "MOCK(키 없음)")
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"  Prism UI  →  http://{a.host}:{a.port}   [{mode}]")
    print("  Ctrl+C 로 종료")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  종료")


if __name__ == "__main__":
    main()
