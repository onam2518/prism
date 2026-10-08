"""조치 상세에서 일반 검수로 돌아올 때 운영 폼과 이력이 따라오지 않는다."""
import pathlib
import shutil
import subprocess
import unittest


class ReviewDetailModeTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js 없음')
    def test_management_is_explicit_and_does_not_leak_into_review(self):
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const window = {};
for (const file of ['app-03-opendetail.js', 'app-20-operations.js']) {
  vm.runInNewContext(fs.readFileSync('prism/vendor/' + file, 'utf8'), {window});
}
const app = {};
for (const part of window.PRISM_APP_PARTS) Object.defineProperties(app, Object.getOwnPropertyDescriptors(part()));
for (const name of ['_rtStart','loadEntLookup','loadEntLabels','opsOpen']) app[name] = () => {};
app._categoryPaths = () => [];
app._err = message => { throw Error(message); };
app.opsRequest = async () => ({basis:{snapshot:{title:'검수 제목',body:'원문',item_meta:{},quality_meta:{}}}});
(async () => {
  await app.opsOpenCase({hash:'1234567890abcdef'}, true);
  assert.equal(app.opsManage, true);
  app.histOpen = true;
  app.openDetail({hash:'fedcba0987654321'});
  assert.equal(app.opsManage, false);
  assert.equal(app.histOpen, false);
  await app.opsOpenCase({hash:'1234567890abcdef'});
  assert.equal(app.opsManage, false);
  assert.equal(app.detail.body, '원문');
})().catch(e => { console.error(e); process.exitCode = 1; });
'''
        subprocess.run(['node', '-e', script], cwd=pathlib.Path(__file__).resolve().parents[1],
                       check=True, capture_output=True, text=True, timeout=10)
