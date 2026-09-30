const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
async function run() {
  const window = {}, pending = [];
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../prism/vendor/app-04-_err.js'), 'utf8'), { window, URLSearchParams, clearTimeout, setTimeout });
  const app = window.PRISM_APP_PARTS[0]();
  app._authHeaders = () => ({});
  app._afetch = (url, options) => {
    assert.equal(options.method, undefined, 'viewing must be read-only');
    return new Promise(resolve => pending.push({ url, reply: body => resolve({ json: async () => body }) }));
  };
  app.evalRunId = 99; app.goldenResult = { status: 'running' }; app.goldenBusy = true;
  app.pilot = { id: 99, status: 'running' }; app.cmpModels = ['A', ''];
  const a = app.openEvalHistory({ kind: 'eval', id: 1 });
  const b = app.openEvalHistory({ kind: 'pilot', id: 'p2-3', pilot_id: 2, round: 3 });
  assert.match(pending[1].url, /kind=pilot&id=2&round=3/);
  pending[1].reply({ ok: true, kind: 'pilot', models: [{ model: 'P' }], items: [] }); await b;
  pending[0].reply({ ok: true, kind: 'eval', models: [{ model: 'old' }] }); await a;
  assert.equal(app.evalDetail.kind, 'pilot', 'late response must not replace selected record');
  assert.equal(app.evalDetailBusy, false);
  assert.equal(app.evalRunId, 99); assert.equal(app.goldenResult.status, 'running'); assert.equal(app.goldenBusy, true);
  assert.equal(app.pilot.id, 99);
  const c = app.openEvalHistory({ kind: 'compare', id: 'ck', key: 'model_compare_v1234567890' });
  pending[2].reply({ ok: true, kind: 'compare', models: [{ model: 'A' }, { model: 'B' }], items: [
    { hash: '1', title: 'DNM metadata', got: { A: { ok: null } }, split: false },
    { hash: '2', title: 'Failed item', got: { A: { empty: true } }, split: false },
    { hash: '3', title: 'Grade mismatch', got: { A: { ok: false }, B: { ok: true } }, split: true }
  ] }); await c;
  assert.equal(app.evalCols.length, 2, 'launch model slots must not filter history');
  assert.equal(app.evalItems.length, 3);
  app.evalItemFilter = 'miss'; assert.equal(app.evalItems.length, 2);
  app.evalItemFilter = 'split'; assert.equal(app.evalItems.length, 1);
  app.evalItemFilter = 'all'; app.evalItemSearch = 'dnm'; assert.equal(app.evalItems.length, 1);
  assert.equal(app.evalMetaText({}, 'summary'), '기록 없음');
  assert.equal(app.evalMetaText({}, 'summary', true), '·');
  assert.equal(app.evalMetaText({ entities: [] }, 'entities', true), '없음');
  assert.equal(app.evalExpectedNote('missing'), '정답 미등록 · 채점 제외');
  assert.equal(app.evalExpectedNote('unrecorded'), '과거 상세 미저장');
  assert.equal(app.evalExpectedNote('scored'), '');
  app.evalDetail.expected_coverage = { total: 3, fields: { intent: 1, summary: 0 } };
  assert.match(app.evalExpectedCoverage, /인텐트 1\/3건/);
  assert.match(app.evalExpectedCoverage, /리드문 0\/3건/);
  app.evalDetail.items[0].expected_status = { intent: 'missing' };
  app.evalDetail.items[1].expected_status = { intent: 'scored' };
  app.evalItemFilter = 'expected'; app.evalItemSearch = '';
  assert.equal(app.evalItems.length, 1);
  assert.equal(app.evalItemCells(app.evalItems[0])[0].expected, true);
  app.evalDetail.expected_coverage.legacy = true;
  assert.equal(app.evalExpectedCoverage, '');
  assert.equal(app.evalMetaText({ entities: [{ name: 'Prism', type: 'OG' }] }, 'entities'), 'Prism (OG)');
  const d = app.openEvalHistory({ kind: 'eval', id: 404 }); pending[3].reply({ ok: false, error: 'missing' }); await d;
  assert.equal(app.evalDetail, null); assert.equal(app.evalDetailError, 'missing');
  let focus = '';
  app.loadEvalRuns = () => {};
  app.evalTabKey({ key: 'Home', preventDefault() {}, currentTarget: { querySelector(id) { return { focus() { focus = id; } }; } } });
  assert.equal(app.evalTab, 'run'); assert.equal(focus, '#eval-tab-run');
  console.log('Evaluation client: all kinds, races, errors, filtering, live state, keyboard tabs passed');
}
run().catch(e => { console.error(e); process.exitCode = 1; });
