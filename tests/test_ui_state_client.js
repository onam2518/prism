/* 의존성 없는 클라이언트 회귀: 목록 재조회의 선택 유지·순번 가드 · 상세 조회 실패 · 상세 이동 연타 가드. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function app() {
  const window = {};
  for (const f of ['app-00-tabitems.js', 'app-01-bulkpertxt.js'])
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../prism/vendor', f), 'utf8'), { window, URLSearchParams });
  const a = {};
  for (const part of window.PRISM_APP_PARTS) Object.defineProperties(a, Object.getOwnPropertyDescriptors(part()));
  a.errs = []; a._err = (m) => a.errs.push(m); a._absorbFreshFb = () => {};
  return a;
}
const reply = (j) => ({ json: async () => j });
const later = () => { let done; const p = new Promise((r) => { done = r; }); return [p, done]; };

async function run() {
  // 1) loadRaw: 새 목록에 남은 행의 선택은 유지 · 빠진 행은 해제
  let a = app();
  a.rawSel = { hash: 'h1', item_meta: {} }; a.assignSel = { hash: 'gone' };
  a._afetch = async () => reply({ ok: true, items: [{ hash: 'h1' }, { hash: 'h2' }] });
  await a.loadRaw();
  assert.equal(a.rawSel.hash, 'h1', 'SSE 재조회가 열어 둔 JSON 원문을 닫으면 안 된다');
  assert.equal(a.assignSel, null, '목록에서 빠진 행의 배정 편집은 해제');

  // 2) 순번 가드: 늦게 온 옛 응답은 버린다(옛 2000 이 새 3000 을 덮지 않음)
  a = app();
  const [p1, r1] = later(), [p2, r2] = later();
  a._afetch = async (url) => (url.includes('limit=2000') ? p1 : p2);
  const first = a.loadRaw(2000), second = a.loadRaw(3000);
  r2(reply({ ok: true, items: [{ hash: 'new' }] })); await second;
  r1(reply({ ok: true, items: [{ hash: 'old' }] })); await first;
  assert.equal(a.rawData.items[0].hash, 'new');

  // 3) 상세 단건 조회 실패면 빈 상세를 열지 않고 안내
  a = app(); let opened = 0;
  a.openDetail = () => { opened++; };
  a._afetch = async () => reply({ ok: false, error: 'not found' });
  assert.equal(await a.openRawFull({ hash: 'x' }), null);
  assert.equal(opened, 0); assert.match(a.errs[0], /상세 불러오기 실패/);

  // 4) detailGo 연타: 진행 중 두 번째 이동은 무시 · 실패면 idx 유지
  a = app(); a.openDetail = () => {};
  const [p3, r3] = later();
  a._afetch = async () => p3;
  a.detailNav = { list: [{ hash: 'a' }, { hash: 'b' }, { hash: 'c' }], idx: 0 };
  const go = a.detailGo(1); await a.detailGo(1);
  r3(reply({ ok: true, item: { body: 'b' } })); await go;
  assert.equal(a.detailNav.idx, 1, '빠른 연타가 같은 칸으로 겹치면 안 된다');
  a._afetch = async () => { throw new Error('net'); };
  await a.detailGo(1);
  assert.equal(a.detailNav.idx, 1, '조회 실패 시 이동하지 않는다');
  console.log('UI state client: selection keep, stale guard, detail failure, detailGo guard passed');
}
run().catch((e) => { console.error(e); process.exitCode = 1; });
