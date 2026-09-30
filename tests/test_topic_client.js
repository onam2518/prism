/* No DOM or third-party dependencies: exercise the shipped preview/approval client. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function run() {
  const requests = [], confirmations = [];
  let approve = true, conflict = false;
  const window = { confirm(text) { confirmations.push(text); return approve; } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../prism/vendor/app-06-weeklyleague.js'), 'utf8'), { window });
  const app = window.PRISM_APP_PARTS[0]();
  app.topicData = { revision: 7 };
  app._authHeaders = () => ({ Authorization: 'fixture-only' });
  app._afetch = async (url, options) => {
    assert.equal(url, '/topic-studio');
    const body = JSON.parse(options.body); requests.push(body);
    const reply = body.action === 'preview_action'
      ? conflict ? { ok: false, conflict: true, error: 'changed' }
        : { ok: true, preview_token: 'verified-token', preview: { topics: [{
          id: 'topic-a', name: '검증 토픽', count: 2, status: 'draft',
          conditions: { condition_expr: { any: [{ all: [{ field: 'entities', values: ['A'] }, { field: 'intent', values: ['리뷰'] }] }, { field: 'entities', values: ['B'] }] } },
          samples: [{ title: '표본' }], external_refs: ['fixture-reference'], feed_labels: []
        }] } }
      : { ok: true, revision: 8 };
    return { json: async () => reply };
  };
  const request = { action: 'save', id: 'topic-a', def: { name: '검증 토픽', keywords: ['A'] } };
  const result = await app._studioPost(request);
  assert.equal(result.revision, 8);
  assert.equal(requests.length, 2);
  assert.equal(requests[0].request.expected_revision, 7);
  assert.equal(requests[1].preview_token, 'verified-token');
  assert.equal(requests[1].expected_revision, 7);
  assert.equal(request.preview_token, undefined);
  assert.match(confirmations[0], /A 그리고 리뷰/);
  assert.match(confirmations[0], /또는 B/);
  assert.match(confirmations[0], /fixture-reference/);

  requests.length = 0; confirmations.length = 0; approve = false;
  assert.equal((await app._studioPost(request)).ok, false);
  assert.equal(requests.length, 1, 'cancellation must not write');

  requests.length = 0; confirmations.length = 0; conflict = true;
  assert.equal((await app._studioPost(request)).conflict, true);
  assert.equal(requests.length, 1, 'a stale preview must not write');
  assert.equal(confirmations.length, 0);
  console.log('Topic client: preview, approval, cancellation, conflict and nested conditions passed');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
