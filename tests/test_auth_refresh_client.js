/* 의존성 없는 클라이언트 회귀: 토큰 갱신 단일 비행 · 다른 탭이 회전한 토큰 채택 · Esc 맨 위 모달 판정. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function app(store, fetchImpl) {
  const window = {};
  const ctx = {
    window,
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    fetch: fetchImpl,
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../prism/vendor/app-04-_err.js'), 'utf8'), ctx);
  const a = {};
  for (const part of window.PRISM_APP_PARTS) Object.defineProperties(a, Object.getOwnPropertyDescriptors(part()));
  a.lives = 0; a.startLive = () => { a.lives++; };
  return { a, ctx };
}
const reply = (j) => ({ json: async () => j });

async function run() {
  // 1) 동시 401 여러 건 → 갱신 요청은 한 번(같은 refresh_token 재사용 금지)
  let store = { prism_token: 'a1', prism_rtoken: 'r1' }, calls = [];
  let { a } = app(store, async (url, o) => { calls.push(JSON.parse(o.body).refresh_token); await null; return reply({ ok: true, access_token: 'a2', refresh_token: 'r2' }); });
  a.authToken = 'a1'; a.rtoken = 'r1';
  const rs = await Promise.all([a.authRefresh(), a.authRefresh(), a.authRefresh()]);
  assert.deepEqual(rs, [true, true, true]);
  assert.deepEqual(calls, ['r1'], '동시 갱신은 단일 비행');
  assert.equal(store.prism_rtoken, 'r2'); assert.equal(a.authToken, 'a2');

  // 2) 다른 탭이 이미 회전 → 네트워크 없이 저장소 토큰 채택
  store = { prism_token: 'b2', prism_rtoken: 'q2' }; calls = [];
  ({ a } = app(store, async () => { calls.push(1); return reply({ ok: false }); }));
  a.authToken = 'b1'; a.rtoken = 'q1';
  assert.equal(await a.authRefresh(), true);
  assert.equal(calls.length, 0, '이미 회전된 토큰은 다시 갱신하지 않는다');
  assert.equal(a.authToken, 'b2'); assert.equal(a.rtoken, 'q2');

  // 3) 갱신 중 다른 탭과 경합(already_used) → 그 사이 저장소에 들어온 최신 토큰 채택
  store = { prism_token: 'c1', prism_rtoken: 's1' };
  ({ a } = app(store, async () => { store.prism_token = 'c9'; store.prism_rtoken = 's9'; return reply({ ok: false, error: '세션 갱신 실패' }); }));
  a.authToken = 'c1'; a.rtoken = 's1';
  assert.equal(await a.authRefresh(), true);
  assert.equal(a.authToken, 'c9'); assert.equal(a.rtoken, 's9');

  // 4) 진짜 만료(저장소도 그대로) → false(호출자가 재로그인 안내) · 다음 호출은 다시 시도 가능
  store = { prism_token: 'd1', prism_rtoken: 't1' }; calls = [];
  ({ a } = app(store, async () => { calls.push(1); return reply({ ok: false }); }));
  a.authToken = 'd1'; a.rtoken = 't1';
  assert.equal(await a.authRefresh(), false);
  assert.equal(await a.authRefresh(), false);
  assert.equal(calls.length, 2, '실패 후 단일 비행 잠금이 풀려야 한다');

  // 5) Esc: 보이는 대화상자 중 z-index 최대 하나만 · 같은 이벤트의 두 번째 판정도 스냅숏 기준
  const mk = (z, shown) => { const b = { z, getClientRects: () => (shown ? [1] : []) }; b.closest = () => b; return b; };
  const lo = mk('74', true), hi = mk('76', true), hidden = mk('80', false);
  let ctx; ({ a, ctx } = app({}, null));
  ctx.document = { querySelectorAll: () => [lo, hi, hidden] };
  ctx.getComputedStyle = (b) => ({ zIndex: b.z });
  const ev = {};
  assert.equal(a._escTop(ev, hi), true);
  hi.getClientRects = () => [];                         // 첫 핸들러가 닫음
  assert.equal(a._escTop(ev, lo), false, '같은 Esc 로 아래 모달까지 닫히면 안 된다');
  assert.equal(a._escTop({}, lo), true, '다음 Esc 에서는 아래 모달이 맨 위');
  assert.equal(a._escTop({}, null), false);
  console.log('auth refresh client ok');
}
run().catch((e) => { console.error(e); process.exit(1); });
