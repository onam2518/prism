#!/usr/bin/env node
// prism-weekly · 슬라이드 덱 HTML → 장당 PNG 캡처 (Chrome headless CDP)
//
// 사용: node capture_deck.mjs <deck.html> <outdir> [scale]
//   deck.html : prism-slides 빌드 산출물(단일 HTML) 경로 또는 URL
//   outdir    : slide-01.png … 저장 디렉토리(없으면 생성)
//   scale     : deviceScaleFactor(기본 2 = 3840×2160 픽셀)
// 요구: Google Chrome · Node 22 이상(내장 WebSocket · fetch).
// 캡처 전 #hud · #progress 를 숨기고 애니메이션을 끈 뒤 .slide 를 순서대로 활성화한다.

import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const CHROME = process.env.CHROME_BIN
  || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const [, , deckArg, outArg, scaleArg] = process.argv;
if (!deckArg || !outArg) {
  console.error('usage: node capture_deck.mjs <deck.html> <outdir> [scale]');
  process.exit(1);
}
const deckUrl = /^https?:/.test(deckArg) ? deckArg : 'file://' + resolve(deckArg);
const outDir = resolve(outArg);
mkdirSync(outDir, { recursive: true });
const scale = Number(scaleArg || 2);
const port = Number(process.env.CDP_PORT || 9377);

const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${port}`,
  '--no-first-run', '--no-default-browser-check', '--hide-scrollbars',
  `--user-data-dir=${outDir}/.chrome-profile`, 'about:blank',
], { stdio: 'ignore' });
const cleanup = () => { try { chrome.kill(); } catch { /* 이미 종료 */ } };
process.on('exit', cleanup);
process.on('SIGINT', () => { cleanup(); process.exit(130); });

async function waitEndpoint() {
  for (let i = 0; i < 50; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/list`);
      const page = (await r.json()).find((t) => t.type === 'page');
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* 아직 기동 전 */ }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error('Chrome DevTools 연결 실패 (포트 ' + port + ')');
}

try {
  const ws = new WebSocket(await waitEndpoint());
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let seq = 0;
  const pending = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? rej(new Error(m.error.message)) : res(m.result);
    }
  };
  const send = (method, params = {}) => new Promise((res, rej) => {
    const id = ++seq;
    pending.set(id, { res, rej });
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, returnByValue: true });
    if (r.exceptionDetails) throw new Error('페이지 JS 오류: ' + JSON.stringify(r.exceptionDetails));
    return r.result.value;
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride',
    { width: 1920, height: 1080, deviceScaleFactor: scale, mobile: false });
  await send('Page.navigate', { url: deckUrl });
  await new Promise((r) => setTimeout(r, 2000)); // 로드 + 내장 폰트 적용 대기

  await evaluate(`(() => {
    const st = document.createElement('style');
    st.textContent = '#hud,#progress{display:none!important}'
      + '*{animation:none!important;transition:none!important}';
    document.head.appendChild(st); return true; })()`);

  const n = await evaluate("document.querySelectorAll('.slide').length");
  if (!n) throw new Error('.slide 요소가 없습니다. prism-slides 덱이 맞는지 확인하세요.');

  for (let i = 0; i < n; i++) {
    await evaluate(`(() => {
      const s = document.querySelectorAll('.slide');
      s.forEach((el, k) => el.classList.toggle('active', k === ${i}));
      s[${i}].scrollTop = 0; return true; })()`);
    await new Promise((r) => setTimeout(r, 250));
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    const file = `${outDir}/slide-${String(i + 1).padStart(2, '0')}.png`;
    writeFileSync(file, Buffer.from(shot.data, 'base64'));
    console.error(`${i + 1}/${n} -> ${file}`);
  }

  ws.close();
  console.log(String(n));
} finally {
  cleanup();
}
