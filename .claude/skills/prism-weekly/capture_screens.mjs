#!/usr/bin/env node
// prism-weekly · 프리즘 화면 캡처 (격리 목 서버 대상 · Chrome headless CDP)
//
// 사용: node capture_screens.mjs <shots.json> <outdir>
// shots.json: {
//   "base": "http://127.0.0.1:8978",            // 생략 가능(기본값)
//   "viewport": {"w":1440,"h":900,"scale":2},    // 생략 가능
//   "shots": [ {"name":"labrun","url":"/?m=lab","clicks":["사용자"]} ]
// }
// - clicks 는 버튼 텍스트 **정확 일치(===)** 로 찾는다. 부분 일치는 네비 메뉴 오클릭 위험.
//   부분 일치가 꼭 필요하면 {"text":"...","exact":false} 로.
// - 검수자 등록 모달은 localStorage 프리셋(복실)으로 우회한다.
// - 산출: <outdir>/<name>.png (embed_shots.py 규약: 이후 <name>.jpg 로 압축해 사용)

import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const CHROME = process.env.CHROME_BIN
  || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const [, , cfgArg, outArg] = process.argv;
if (!cfgArg || !outArg) {
  console.error('usage: node capture_screens.mjs <shots.json> <outdir>');
  process.exit(1);
}
const cfg = JSON.parse(readFileSync(cfgArg, 'utf8'));
const base = cfg.base || 'http://127.0.0.1:8978';
const vp = cfg.viewport || { w: 1440, h: 900, scale: 2 };
const outDir = resolve(outArg);
mkdirSync(outDir, { recursive: true });
const port = Number(process.env.CDP_PORT || 9378);

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
    } catch { /* 기동 대기 */ }
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
    { width: vp.w, height: vp.h, deviceScaleFactor: vp.scale, mobile: false });
  await send('Page.addScriptToEvaluateOnNewDocument', { source:
    "localStorage.setItem('prism_reviewer','복실');localStorage.setItem('prism_reviewer_char','boksil');" });

  for (const s of cfg.shots) {
    await send('Page.navigate', { url: s.url.startsWith('http') ? s.url : base + s.url });
    await new Promise((r) => setTimeout(r, s.wait || 2600));
    for (const c of (s.clicks || [])) {
      const text = typeof c === 'string' ? c : c.text;
      const exact = typeof c === 'string' ? true : c.exact !== false;
      const ok = await evaluate(`(() => {
        const t = ${JSON.stringify(text)};
        const bs = [...document.querySelectorAll('button')].filter(b => b.offsetParent
          && (${exact} ? b.textContent.trim() === t : b.textContent.trim().includes(t)));
        if (bs[0]) { bs[0].click(); return true } return false })()`);
      if (!ok) console.error(`경고: ${s.name} 에서 버튼 "${text}" 못 찾음`);
      await new Promise((r) => setTimeout(r, 900));
    }
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(`${outDir}/${s.name}.png`, Buffer.from(shot.data, 'base64'));
    console.error(`captured ${s.name}`);
  }
  ws.close();
} finally {
  cleanup();
}
