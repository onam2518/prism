"""프런트 감사(2026-08-04) 회귀.

- idx 19: 토픽 스튜디오 모델 선택 = <x-modelpick> 규약(네이티브 select 금지 · 미연결 모델 차단)
  · 서버 전송은 model id 만(값 형식 provider|model 에서 추출)
- idx 32: +PT 토스트는 keyed x-for 로 감싼다 — Alpine 의 key 는 x-for 전용이라
  x-if 단독 요소에 걸면 무동작(연속 검수 시 애니메이션 재시작 안 됨)
- idx 33: 검수 목록 필터 체인은 getter 재계산 대신 루트 x-effect 1회 계산(_rawRecalc)
  · 동작 동등성은 node 로 실제 실행해 확인(node 없으면 skip)
- idx 34: 죽은 코드(참조 0건 grep 확증) 재유입 금지
- idx 35: 카드 헤더 장식 MutationObserver 는 추가된 서브트리만 스캔
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _vendor_sources():
    out = {}
    for p in glob.glob(os.path.join(ROOT, "prism/vendor/app-*.js")):
        with open(p, encoding="utf-8") as fh:
            out[os.path.basename(p)] = fh.read()
    return out


class TestStudioModelpick(unittest.TestCase):
    """idx 19 · 토픽 스튜디오도 공통 <x-modelpick> 을 쓴다(전체 개수는 test_modelmeta 가 확인)."""

    def test_studio_uses_modelpick_not_native_select(self):
        src = _read("prism/ui/07-studio.html")
        self.assertNotIn('x-model="studioModel"', src)          # 네이티브 select 잔존 금지
        self.assertIn("<x-modelpick", src)
        m = re.search(r'<x-modelpick[^>]*value="studioModel"[^>]*>', src)
        self.assertIsNotNone(m, "studioModel 을 쓰는 x-modelpick 이 없습니다")
        self.assertIn('groups="textGroups"', m.group(0))        # 데이터 원천 = 공통 제공자 그룹

    def test_suggest_sends_bare_model_id(self):
        """x-modelpick 값은 provider|model — 서버(llm_for_model)엔 model id 만 보내야 한다."""
        src = _read("prism/vendor/app-06-weeklyleague.js")
        self.assertIn("split('|').pop()", src)
        self.assertNotIn("model: this.studioModel", src)        # 원값 그대로 전송 금지


class TestPtToastKeyedFor(unittest.TestCase):
    """idx 32 · +PT 토스트: keyed x-for 라야 id 교체 때 요소가 재생성돼 애니메이션이 재시작된다."""

    def test_pttoast_wrapped_in_keyed_x_for(self):
        src = _read("prism/ui/00-head.html")
        m = re.search(r'<template x-for="t in \(ptToast \? \[ptToast\] : \[\]\)"[^>]*'
                      r'x-bind:key="t\.id"', src)
        self.assertIsNotNone(m, "+PT 토스트가 keyed x-for 로 감싸져 있지 않습니다")

    def test_no_key_binding_outside_x_for(self):
        """회귀 원인: x-bind:key 는 x-for 루프 diff 전용 — 단독 요소에선 무동작."""
        src = _read("prism/ui/00-head.html")
        self.assertNotIn('x-bind:key="ptToast.id"', src)


class TestRawFilterRecalc(unittest.TestCase):
    """idx 33 · 필터 체인은 x-effect 1회 계산: getter 로 두면 바인딩(효과)마다 전량 재계산."""

    def test_getters_replaced_by_recalc(self):
        src = _read("prism/vendor/app-02-_afterverdict.js")
        for g in ("get rawScoped", "get rawFiltered", "get rawDoneHidden"):
            self.assertNotIn(g, src, "%s 가 getter 로 돌아왔습니다(바인딩마다 풀 스캔)" % g)
        self.assertIn("_rawRecalc()", src)

    def test_root_effect_wired(self):
        src = _read("prism/ui/00-head.html")
        m = re.search(r'<div x-data="prismApp\(\)"[^>]*x-effect="_rawRecalc\(\)"', src)
        self.assertIsNotNone(m, "루트 x-effect 연결이 없으면 목록·건수가 갱신되지 않습니다")

    def test_recalc_behavior_matches_old_getters(self):
        """실제 실행(node): todo 숨김·done 전용·검색·타인 배정 제외·부족 분류 우선·숨김 건수."""
        if not shutil.which("node"):
            self.skipTest("node 미설치")
        script = r"""
const fs = require('fs');
globalThis.window = {};
eval(fs.readFileSync(process.argv[2], 'utf8'));   // app-01 (myVerdict)
eval(fs.readFileSync(process.argv[3], 'utf8'));   // app-02 (_rawRecalc)
const app = {};
for (const make of window.PRISM_APP_PARTS) {
  Object.defineProperties(app, Object.getOwnPropertyDescriptors(make()));
}
app.opsAdmin = false; app.arenaData = { my_id: 'me' }; app.reviewer = '나';
app.rawData = { items: [
  { hash: 'a', title: '삼성 뉴스', category: ['News'], reasons: [], grade: 'G', model: 'm1',
    service: 's1', fb: { mine: 'good' }, assignees: [] },                    // 내가 판정 완료
  { hash: 'b', title: 'LG 뉴스', category: [], reasons: [], grade: 'R', model: 'm1',
    service: 's1', fb: {}, assignees: [] },
  { hash: 'c', title: '타인 배정', category: [], reasons: [], grade: 'G', model: 'm1',
    service: 's1', fb: {}, assignees: ['other'] },                           // 타인 배정 → 숨김
  { hash: 'd', title: '부족 분류', category: [], reasons: [], grade: 'G', model: 'm1',
    service: 's1', fb: {}, assignees: [], class_gap: true },
] };
app.rawQ = ''; app.rawGrade = ''; app.rawModel = ''; app.rawSvc = '';
app.rawRev = 'todo'; app.rawMineOnly = false; app.rawGapFirst = false;
app._rawRecalc();
const todo = { scoped: app.rawScoped.map(r => r.hash),
               filtered: app.rawFiltered.map(r => r.hash), hidden: app.rawDoneHidden };
app.rawRev = 'done'; app._rawRecalc();
const done = { filtered: app.rawFiltered.map(r => r.hash), hidden: app.rawDoneHidden };
app.rawRev = 'todo'; app.rawQ = '삼성'; app._rawRecalc();
const search = { filtered: app.rawFiltered.map(r => r.hash), hidden: app.rawDoneHidden };
app.rawQ = ''; app.rawGapFirst = true; app._rawRecalc();
const gap = app.rawFiltered.map(r => r.hash);
console.log(JSON.stringify({ todo, done, search, gap }));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run(
                ["node", path,
                 os.path.join(ROOT, "prism/vendor/app-01-bulkpertxt.js"),
                 os.path.join(ROOT, "prism/vendor/app-02-_afterverdict.js")],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[:400])
            out = json.loads(r.stdout)
        finally:
            os.unlink(path)
        self.assertEqual(out["todo"]["scoped"], ["a", "b", "d"])   # 타인 배정 c 제외
        self.assertEqual(out["todo"]["filtered"], ["b", "d"])      # 내 판정 완료 a 숨김
        self.assertEqual(out["todo"]["hidden"], 1)                 # '감춰진 N건' 힌트
        self.assertEqual(out["done"]["filtered"], ["a"])           # 완료 필터 = 내 판정분만
        self.assertEqual(out["done"]["hidden"], 0)                 # todo 아닐 땐 힌트 없음
        self.assertEqual(out["search"]["filtered"], [])            # '삼성' 일치분(a)은 todo 가 숨김
        self.assertEqual(out["search"]["hidden"], 1)
        self.assertEqual(out["gap"], ["d", "b"])                   # 부족 분류 우선 · 안정 정렬


class TestDeadCodeRemoved(unittest.TestCase):
    """idx 34 · 참조 0건 grep 확증 후 제거한 정의들의 재유입 금지."""

    DEAD = ("studioModelList", "refreshStudioModels", "modelsMsgStudio",
            "crewEscalate", "crewEscRes", "showProfileFields", "teamMode", "caEditing",
            "this.bulkModel")

    def test_dead_identifiers_absent(self):
        offenders = []
        srcs = _vendor_sources()
        for p in glob.glob(os.path.join(ROOT, "prism/ui/*.html")):
            with open(p, encoding="utf-8") as fh:
                srcs[os.path.basename(p)] = fh.read()
        for name, src in sorted(srcs.items()):
            for ident in self.DEAD:
                if ident in src:
                    offenders.append("%s: %s" % (name, ident))
        self.assertEqual(offenders, [], "제거된 죽은 코드 재유입: %r" % (offenders,))

    def test_client_drill_and_result_getters_stay_removed(self):
        src = _vendor_sources()["app-02-_afterverdict.js"]
        self.assertNotIn("async drill(", src)                      # topicDrill 만 남긴다
        src8 = _vendor_sources()["app-08-copytext.js"]
        self.assertNotIn("get im()", src8)
        self.assertNotIn("get q()", src8)


class TestPanelObserverScoped(unittest.TestCase):
    """idx 35 · 헤더 장식 옵저버: 추가된 노드가 없으면 스킵 · 스캔은 추가 서브트리로 한정."""

    def test_observer_reads_added_nodes_only(self):
        src = _read("prism/ui/21-tail.html")
        self.assertIn("addedNodes", src)                           # 변이 레코드를 본다
        # 문서 전체 스캔은 첫 로드 1회뿐 — 옵저버 콜백에서 decorate(document) 재호출 금지
        self.assertEqual(src.count("decorate(document)"), 1)

    def test_decorate_covers_root_matching_panel_hd(self):
        """추가된 노드 자신이 panel-hd 인 경우: querySelectorAll 은 루트를 안 보므로 별도 포함."""
        src = _read("prism/ui/21-tail.html")
        self.assertIn("scope.matches", src)


if __name__ == "__main__":
    unittest.main()


class TestCrewDdayCalendar(unittest.TestCase):
    """D-day 는 달력 날짜 기준(자정 경계). 경과 시간/24h 로 세면 21시간 남은
    '내일 오전 마감'이 '오늘 마감'으로 표시된다(2026-08-04 운영 신고)."""

    def test_dday_uses_calendar_days(self):
        if not shutil.which("node"):
            self.skipTest("node 미설치")
        script = r"""
const fs = require('fs');
globalThis.window = {};
eval(fs.readFileSync(process.argv[2], 'utf8'));   // app-12 (crewDdayTxt)
const RealDate = Date;
const FIXED = new RealDate(2026, 7, 4, 14, 50, 0);          // 2026-08-04 14:50 로컬
globalThis.Date = class extends RealDate {
  constructor(...a) { super(...(a.length ? a : [FIXED.getTime()])); }
  static now() { return FIXED.getTime(); }
};
const app = {};
for (const make of window.PRISM_APP_PARTS) {
  Object.defineProperties(app, Object.getOwnPropertyDescriptors(make()));
}
const at = (...a) => new Date(...a).getTime() / 1000;
const txt = (due) => { app.crewData = { summary: { due_at: due } }; return app.crewDdayTxt; };
console.log(JSON.stringify({
  tomorrow_morning: txt(at(2026, 7, 5, 11, 59)),   // 21시간 뒤 · 내일 → D-1
  tonight: txt(at(2026, 7, 4, 23, 0)),             // 오늘 밤 → 오늘 마감
  passed: txt(at(2026, 7, 4, 10, 0)),              // 지남
  three_days: txt(at(2026, 7, 7, 9, 0)),           // → D-3
  none: txt(0),
}));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run(
                ["node", path, os.path.join(ROOT, "prism/vendor/app-12-crew.js")],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[:400])
            out = json.loads(r.stdout)
        finally:
            os.unlink(path)
        self.assertEqual(out["tomorrow_morning"], " · D-1")
        self.assertEqual(out["tonight"], " · 오늘 마감")
        self.assertEqual(out["passed"], " · 기한 지남")
        self.assertEqual(out["three_days"], " · D-3")
        self.assertEqual(out["none"], "")


class TestEvalModelRefresh(unittest.TestCase):
    """평가 기준의 기준 모델: '써 본 모델'(availableModels)만 보이던 픽커를 제공자별 전체 카탈로그
    (textGroups)로 바꾸고 새로고침을 붙였다(2026-08-26 · 새 모델을 고를 수 없던 문제)."""

    def test_eval_pick_uses_full_catalog_with_refresh(self):
        src = _read("prism/ui/13-eval.html")
        m = re.search(r'<x-modelpick[^>]*value="evalModel"[^>]*>', src)
        self.assertIsNotNone(m, "evalModel 을 쓰는 x-modelpick 이 없습니다")
        self.assertIn('groups="textGroups"', m.group(0))        # 데이터 원천 = 공통 제공자 그룹
        self.assertNotIn('options="availableModels"', m.group(0))
        self.assertIn('x-on:click="loadModels()"', src)         # 설정 화면과 같은 새로고침 재사용

    def test_eval_run_sends_bare_model_id(self):
        """x-modelpick 값은 provider|model — /eval-run-start 엔 model id 만 보낸다."""
        src = _read("prism/vendor/app-04-_err.js")
        i = src.index("'/eval-run-start'")
        line = src[i:src.index("\n", i)]
        self.assertIn("split('|').pop()", line)
        self.assertNotIn("model: this.evalModel,", line)

    def test_refresh_merges_live_router_list(self):
        """새로고침은 라우터 실목록(j.router.timely)을 스냅샷 카탈로그에 합친다(지우지 않고 추가)."""
        src = _read("prism/vendor/app-08-copytext.js")
        body = src[src.index("async loadModels()"):src.index("async refreshConfig()")]
        self.assertIn("j.router.timely", body)
        self.assertIn("this.modelCatalog.timely.text = cur.concat(", body)
