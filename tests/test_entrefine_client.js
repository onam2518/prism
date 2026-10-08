const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function run() {
  const window = {};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../prism/vendor/app-23-entrefine.js'), 'utf8'), { window, clearTimeout });
  const app = window.PRISM_APP_PARTS[0]();
  assert.equal(app.erSentenceText(null), '');
  assert.equal(app.erSentenceText({ draft: '검사 미통과 초안', error: '길이' }), '검사 미통과 초안');
  assert.equal(app.erSentenceLength({ text: '가 😀 나' }), 5); // Python len과 같은 공백·Unicode 코드포인트 수
  assert.equal(app.erSentenceText({ text: '확정', draft: '초안' }), '확정');
  const config = {
    default_model: 'solar-pro4', defaults: { keyword: 'default kw', sentence: 'default sentence' },
    keyword: { model: 'solar-pro3', rules: 'three', rules_by_model: { 'solar-pro3': 'three' } },
    sentence: { model: '', rules: 'default sentence', rules_by_model: {} }
  };
  app._erApplyCfg(config);
  app.erRules.keyword = 'edited three';
  app.erSelectModel('keyword', 'upstage|solar-pro4');
  assert.equal(app.erRules.keyword, 'default kw');
  app.erRules.keyword = 'edited four';
  app.erSelectModel('keyword', 'solar-pro3');
  assert.equal(app.erRules.keyword, 'edited three');
  app._authHeaders = () => ({});
  let saved;
  app._afetch = async (url, options) => {
    saved = JSON.parse(options.body);
    return { json: async () => ({ ok: true, ...config }) };
  };
  await app.erSaveCfg();
  assert.equal(saved.keyword.rules_by_model['solar-pro3'], 'edited three');
  assert.equal(saved.keyword.rules_by_model['solar-pro4'], 'edited four');
  assert.equal(saved.sentence.rules_by_model['solar-pro4'], 'default sentence');

  app.erItems = Array.from({ length: 25 }, (_, i) => ({ hash: String(i) }));
  assert.equal(app.erPageCount(), 3);
  assert.equal(app.erPageItems().length, 10);
  app.erPage = 3;
  assert.equal(app.erPageItems().length, 5);
  assert.equal(app.erPageItems()[0].hash, '20');
  app.erPageItems()[0]._voted = 'both';
  assert.equal(app.erItems[20]._voted, 'both');
  app.erPageSize = 20; app.erPage = 1;
  assert.equal(app.erPageCount(), 2);

  let release, requests = 0;
  app._afetch = async () => {
    requests++;
    await new Promise(resolve => { release = resolve; });
    return { json: async () => ({ ok: false, error: 'test failure' }) };
  };
  const pending = app.erRun();
  await app.erRun();
  assert.equal(requests, 1);
  assert.equal(app.erRunning, true);
  assert.equal(app.erPage, 1);
  release(); await pending;
  assert.equal(app.erRunning, false);
  assert.equal(app.erErr, true);
  app._afetch = async () => { throw new Error('offline'); };
  await app.erLoadCfg();
  assert.equal(app.erCfgErr, true);
  assert.equal(app.erCfgMsg, 'offline');
  console.log('Core keyword client: model drafts, pagination, duplicate start and errors passed');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
