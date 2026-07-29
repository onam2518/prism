"""엔티티 입력 위젯(하이브리드 픽커) 회귀: 입력하지 않은 값이 대신 들어가던 문제.

배경(2026-07-29 문의 접수):
  ① "회사" 를 입력하고 Enter 하면 사전의 "지주회사" 가 대신 추가됐다. 검색이 부분 일치인데
     Enter 가 무조건 '첫 후보' 를 넣어서, 사용자가 입력하지 않은 값이 들어갔다.
  ② '새 엔티티로 추가' 안내가 한번 뜨면 사라지지 않았다. 이미 선택된 값을 다시 넣거나
     대상 배열이 없으면 hybAdd 가 그냥 return 해 검색어·열림 상태가 남았다.

node 로 조각을 로드해 실제 함수를 호출한다(브라우저·서버 없이). node 없으면 skip.
실행: python3 -m pytest tests/ -q
"""
import json
import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PICKER = os.path.join(_ROOT, "prism", "vendor", "app-10-hybridpick.js")

_HARNESS = r"""
global.window = {};
require(%s);
function mk(ents) {
  const o = {};
  for (const m of window.PRISM_APP_PARTS) Object.defineProperties(o, Object.getOwnPropertyDescriptors(m()));
  o.hybEnt = ents; o.dictData = { iabTier1: [], tier2: {}, intents: {} };
  return o;
}
const out = {};
const ENTS = [{ name: '지주회사', type: '' }, { name: '사물라', type: 'PS' }];
// ① 사전에 없는 말을 치고 Enter → 친 그대로 들어가야 한다
{ const o = mk(ENTS), sel = [];
  o.hybInput('t', '회사'); o.hybEnter('t', { kind: 'entity', free: true }, {}, sel);
  out.typed = sel.slice(); out.typedFree = o.hybSt('t').free.slice(); }
// ② 부분 일치 후보가 있어도 첫 후보로 바뀌면 안 된다
{ const o = mk(ENTS), sel = [];
  o.hybInput('t', '회사');
  out.candidates = o._hybMatches('t', 'entity', {}, sel).map((x) => x.v); }
// ③ 정확히 일치하는 등재 값은 그것을 쓴다(중복 신규 생성 방지)
{ const o = mk(ENTS), sel = [];
  o.hybInput('t', '지주회사'); o.hybEnter('t', { kind: 'entity', free: true }, {}, sel);
  out.exact = sel.slice(); out.exactFree = o.hybSt('t').free.slice(); }
// ④ 사전 한정 필드(분류·의도)는 종전대로 첫 후보
{ const o = mk([]), sel = [];
  o.hybGroups = () => [{ label: 'g', items: [{ v: 'Sports / Baseball', ko: '스포츠 / 야구', def: '' }] }];
  o.hybInput('c', '야구'); o.hybEnter('c', { kind: 'category', free: false }, {}, sel);
  out.dictOnly = sel.slice(); }
// ⑤ 이미 선택된 값을 다시 넣어도 입력창·드롭다운은 닫힌다
{ const o = mk(ENTS), sel = ['회사'];
  o.hybInput('t', '회사'); o.hybAdd('t', sel, '회사', true);
  out.dupState = { q: o.hybSt('t').q, open: o.hybSt('t').open, sel: sel.slice() }; }
// ⑥ 대상 배열이 없어도 닫힌다
{ const o = mk(ENTS);
  o.hybInput('u', '회사'); o.hybAdd('u', undefined, '회사', true);
  out.nilState = { q: o.hybSt('u').q, open: o.hybSt('u').open }; }
// ⑦ 빈 입력 Enter 는 아무 일도 하지 않는다
{ const o = mk(ENTS), sel = [];
  o.hybInput('t', '   '); o.hybEnter('t', { kind: 'entity', free: true }, {}, sel);
  out.blank = sel.slice(); }
console.log(JSON.stringify(out));
""" % json.dumps(_PICKER)


class TestHybridPicker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("node 미설치")
        r = subprocess.run(["node", "-e", _HARNESS], capture_output=True, text=True)
        if r.returncode != 0:
            raise AssertionError("하네스 실행 실패: " + (r.stderr or "")[-800:])
        cls.out = json.loads(r.stdout.strip().split("\n")[-1])

    def test_typed_value_wins_over_partial_match(self):
        """'회사' 를 치면 '회사' 가 들어가야 한다 · 사전의 '지주회사' 가 대신 들어가면 안 된다."""
        self.assertIn("지주회사", self.out["candidates"], "부분 일치 후보 상황이 재현되지 않음")
        self.assertEqual(self.out["typed"], ["회사"])
        self.assertEqual(self.out["typedFree"], ["회사"])   # 신규(자유 입력) 표시

    def test_exact_dictionary_match_is_reused(self):
        """정확히 같은 등재 값이면 그것을 쓴다(같은 말로 개체가 둘 생기지 않게)."""
        self.assertEqual(self.out["exact"], ["지주회사"])
        self.assertEqual(self.out["exactFree"], [])         # 등재분이라 신규 아님

    def test_dictionary_only_field_keeps_first_candidate(self):
        """분류·의도는 새 값을 만들 수 없으므로 첫 후보 선택이 맞다(종전 동작 유지)."""
        self.assertEqual(self.out["dictOnly"], ["Sports / Baseball"])

    def test_dropdown_closes_even_when_nothing_added(self):
        """이미 선택된 값·대상 없음에서도 검색어와 드롭다운은 정리돼야 한다.
        (남으면 '새 엔티티로 추가' 안내가 계속 떠 있다)"""
        self.assertEqual(self.out["dupState"], {"q": "", "open": False, "sel": ["회사"]})
        self.assertEqual(self.out["nilState"], {"q": "", "open": False})

    def test_blank_enter_is_noop(self):
        self.assertEqual(self.out["blank"], [])


if __name__ == "__main__":
    unittest.main()
