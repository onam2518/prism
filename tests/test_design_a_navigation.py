"""Execute the real navigation owner with an isolated browser/API shim."""
import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node required for browser logic')
class EvaluationNavigation(unittest.TestCase):
    def test_openers_choose_peer_without_background_poll_stealing_focus(self):
        script = r"""
const assert = require('assert');
const listeners = {};
global.window = {PRISM_APP_PARTS: [], addEventListener: (type, fn) => listeners[type] = fn};
global.location = {origin: 'http://fixture'};
require('./prism/vendor/app-04-_err.js');
const a = window.PRISM_APP_PARTS[0]();
const result = {ok: true, models: []};
Object.assign(a, {mod: 'home', _authHeaders: () => ({}),
  selectMod(id) { this.mod = id; }, liveToast() {},
  _afetch: async () => ({json: async () => ({ok: true, id: 7, job: {id: 7, status: 'done', result}})}),
  loadEvalRuns() {}, loadPilot() {}, pollEvalRun(id) { this.evalRunId = id; }});
(async () => {
  await a.openCompareResult(7);
  assert.equal(a.mod, 'evaluate'); assert.equal(a.evalMode, 'compare'); assert.equal(a.cmpResult, result);
  a.mod = 'home'; a.evalMode = 'pilot'; a._bindCompareMessage();
  await listeners.message({origin: 'http://foreign', data: {type: 'prism-compare-done', id: 7}});
  assert.equal(a.mod, 'home');
  await listeners.message({origin: location.origin, data: {type: 'prism-compare-done', id: 7}});
  assert.equal(a.evalMode, 'compare'); assert.equal(a.mod, 'evaluate');
  a.openEvalRun(8); assert.equal(a.evalMode, 'run'); assert.equal(a.evalRunId, 8);
  a.evalMode = 'compare'; await a.resumeEvalRun(9); assert.equal(a.evalMode, 'run');
  a.evalMode = 'compare'; await a.runGolden(); assert.equal(a.evalMode, 'run');
  await a.startPilot(); assert.equal(a.evalMode, 'pilot');
  a.pollCompare(7); await new Promise(setImmediate); assert.equal(a.evalMode, 'pilot');
})().catch(e => {console.error(e); process.exit(1)});
"""
        subprocess.run(['node', '-e', script], cwd=ROOT, check=True, capture_output=True, text=True)

    def test_compare_results_ignore_out_of_order_responses(self):
        script = r"""
const assert = require('assert');
global.window = {PRISM_APP_PARTS: [], addEventListener() {}};
global.location = {origin: 'http://fixture'};
require('./prism/vendor/app-04-_err.js');
const a = window.PRISM_APP_PARTS[0]();
const selected17 = {ok: true, marker: 'selected-17', models: []};
const selected18 = {ok: true, marker: 'selected-18', models: []};
const older = {ok: true, marker: 'older-last', models: []};
let resolveLast;
Object.assign(a, {mod: 'evaluate', _authHeaders: () => ({}), liveToast() {},
  selectMod(id) { this.mod = id; },
  _afetch: async (url) => {
    if (url === '/model-compare-last') return await new Promise(resolve => { resolveLast = resolve; });
    if (url === '/compare-status?id=17') return {json: async () => ({ok: true, job: {id: 17, result: selected17}})};
    throw new Error('unexpected URL: ' + url);
  }});
(async () => {
  const preload = a.loadCompareLast();
  await Promise.resolve();
  await a.openCompareResult(17);
  resolveLast({json: async () => older});
  await preload;
  assert.equal(a.cmpResult, selected17);

  let resolve17, resolve18;
  a._afetch = async (url) => await new Promise(resolve => {
    if (url.endsWith('17')) resolve17 = resolve;
    else if (url.endsWith('18')) resolve18 = resolve;
    else throw new Error('unexpected URL: ' + url);
  });
  const open17 = a.openCompareResult(17);
  const open18 = a.openCompareResult(18);
  resolve18({json: async () => ({ok: true, job: {id: 18, result: selected18}})});
  await open18;
  resolve17({json: async () => ({ok: true, job: {id: 17, result: selected17}})});
  await open17;
  assert.equal(a.cmpResult, selected18);
})().catch(e => {console.error(e); process.exit(1)});
"""
        subprocess.run(['node', '-e', script], cwd=ROOT, check=True, capture_output=True, text=True)
