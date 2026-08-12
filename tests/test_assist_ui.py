"""검수 보조 패널(트랙 A · 화면) 회귀 가드.

이 패널은 품질 측정의 독립성 위에 서 있다. 판정 전에 남이 내린 판정이 새면 재는 대상이
사람이 아니라 모델(또는 먼저 판정한 사람)이 되어 버린다. 그래서 문구가 아니라 **규칙**을
단언한다.

  1. 기본은 접힘이고 펼 때만 부른다(호출 비용 + 주의 분산).
  2. stage 는 화면이 정하고 근거는 내 표(myVerdict) 하나뿐이다.
  3. 판정 전에는 선례·다른 검수자 의견을 **그리지도 부르지도** 않는다. 숨기는 것으로는
     부족하다 — 검수자끼리의 일치도로 신뢰도를 계산하는데, B 가 A 의 판정을 보고 정하면
     그 일치는 독립된 근거가 아니고 지표가 조용히 부푼다.
  4. 골드 문항에서 패널이 다르게 보이면 안 된다. 막는 방식이 골드를 알려 주면
     검수자가 그걸 배우고, 그 순간 측정 대상이 평소의 검수가 아니게 된다.
  5. 근거가 없으면 없다고 쓰고, 잘렸으면 잘렸다고 쓴다.
  6. /assist 가 없어도(404) 화면이 깨지지 않는다.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKUP = os.path.join(ROOT, "prism", "ui", "19e-review-assist.html")
APPJS = os.path.join(ROOT, "prism", "vendor", "app-17-assist.js")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _fn(js, name):
    """조각에서 메서드 본문 한 덩어리를 떼어 낸다(들여쓰기 6칸 관례)."""
    m = re.search(r"\n      " + name + r"\(.*?\n      \}", js, re.S)
    return m.group(0) if m else ""


class TestAssistMarkup(unittest.TestCase):
    def test_fragment_is_composed_into_page(self):
        """조각이 PAGE 에 합성되고, 검수 상세의 자리표(#asxSlot)로 텔레포트된다."""
        from prism.page import PAGE
        self.assertIn('x-teleport="#asxSlot"', PAGE)
        self.assertIn('id="asxSlot"', PAGE)

    def test_slot_sits_above_verdict(self):
        """자리표는 '검수 판정' 블록 바로 위 = 근거를 읽고 판정한다는 순서."""
        from prism.page import PAGE
        self.assertLess(PAGE.index('id="asxSlot"'), PAGE.index('class="dve__verdict"'))

    def test_fragment_precedes_xdata_close(self):
        """조각 파일명이 x-data 루트를 닫는 20-* 보다 앞서야 스코프 안에 든다."""
        names = sorted(n for n in os.listdir(os.path.join(ROOT, "prism", "ui")) if n.endswith(".html"))
        self.assertIn("19e-review-assist.html", names)
        self.assertLess(names.index("19e-review-assist.html"), names.index("20-ingest-policy.html"))

    def test_others_verdicts_are_not_rendered_before_mine(self):
        """선례·다른 검수자 의견·수정 제안은 판정 후 블록 안에만 있다.

        x-show(숨김)가 아니라 x-if(미생성)여야 한다 — 숨긴 값은 DOM 에 남아 언젠가 읽힌다."""
        m = _read(MARKUP)
        self.assertIn('x-if="asxAfter()"', m)
        after = m[m.index('x-if="asxAfter()"'):]
        before = m[:m.index('x-if="asxAfter()"')]
        for needle in ("asxPrecItems()", "asxDisItems()", "asxSuggest()", "asxCut("):
            self.assertIn(needle, after, needle)
            self.assertNotIn(needle, before, f"{needle} 이 판정 전 화면에 있습니다")

    def test_section_skeleton_is_constant(self):
        """자료가 없어도 섹션은 그대로 선다 · 섹션 유무가 콘텐츠 힌트가 되면 안 된다
        (골드 문항이 '선례 없는 평범한 콘텐츠'와 같은 모양이어야 하는 이유이기도 하다)."""
        m = _read(MARKUP)
        before = m[:m.index('x-if="asxAfter()"')]
        self.assertEqual(before.count('class="asx__sec"'), 3)          # 요약·근거·기준
        self.assertNotIn('class="asx__sec" x-show=', before)           # 판정 전 섹션은 조건부 표시 없음
        for empty in ("요약 없음", "저장된 근거 없음", "기준 없음"):
            self.assertIn(empty, before, empty)

    def test_draft_values_section_is_gone(self):
        """content_brief.values 는 그리지 않는다 · 골드에서는 뒤집기 전 참값이라 화면과 어긋난다."""
        m = _read(MARKUP)
        self.assertNotIn("asxValues", m)
        self.assertNotIn("초안 값", m)

    def test_stage_echo_mismatch_is_surfaced(self):
        """서버가 다른 단계로 처리했으면 화면이 알아야 한다(조용한 before 강등 탐지)."""
        self.assertIn("asxStageEcho()", _read(MARKUP))

    def test_truncation_is_visible_on_both_lists(self):
        """조용한 절단은 '다 봤다'로 읽힌다 · 선례와 다른 검수자 의견 양쪽 다."""
        m = _read(MARKUP)
        self.assertIn("asxCut(asxPrec)", m)
        self.assertIn("asxCut(asxDis)", m)

    def test_precedent_shows_how_many_agreed(self):
        """몇 사람이 그렇게 봤는지가 검수자가 무게를 다는 근거다."""
        self.assertIn("asxWho(p.n)", _read(MARKUP))

    def test_masked_precedent_identifiers_are_left_blank(self):
        """선례가 정답셋 원본이면 서버가 hash·title·reason 을 지운다.

        빈 자리를 '(제목 없음)' 같은 문구로 채우면 고장처럼 읽히고, 무엇보다 '가려진 항목'을
        눈에 띄게 만들어 오히려 표시가 된다. 그냥 그리지 않는다."""
        m = _read(MARKUP)
        self.assertIn('x-show="p.title"', m)
        self.assertIn('x-show="p.reason"', m)
        prec = re.sub(r"<!--.*?-->", "", m, flags=re.S)                # 주석은 화면에 안 나온다
        prec = prec[prec.index("asxPrecItems()"):prec.index("다른 검수자 의견")]
        self.assertNotIn("제목 없음", prec)

    def test_suggestions_are_not_dressed_up(self):
        """제안은 '센 사실'이다 · 권장·정답으로 읽히게 꾸미지 않는다."""
        m = _read(MARKUP)
        self.assertIn("s.basis", m)
        self.assertNotIn("'근거 · ' + s.basis", m)                     # basis 문구를 화면이 덧칠하지 않는다
        shown = re.sub(r"<!--.*?-->", "", m, flags=re.S)               # 주석은 화면에 안 나온다
        for word in ("권장", "추천", "정답"):
            self.assertNotIn(word, shown, f"화면 문구에 '{word}' 가 있습니다")
        css = _read(os.path.join(ROOT, "prism", "vendor", "app.css"))
        to = re.search(r"\.asx__to\{[^}]*\}", css).group(0)
        self.assertNotIn("font-weight", to)                            # 고친 값을 굵기로 밀지 않는다

    def test_panel_starts_collapsed(self):
        m = _read(MARKUP)
        self.assertIn('x-show="asxOpen"', m)
        self.assertIn("asxToggle()", m)


class TestAssistApp(unittest.TestCase):
    def test_js_fragment_joins_bundle(self):
        """조각은 글롭이라 자동 편입 · 로더(app.js)보다 앞서야 한다."""
        from prism import assets
        parts = assets.parts(assets.JS_BUNDLE)
        self.assertIn("app-17-assist.js", parts)
        self.assertLess(parts.index("app-17-assist.js"), parts.index("app.js"))

    def test_stage_comes_from_my_own_verdict(self):
        """팀 합의(fb.verdict)가 아니라 내 표 · 남의 판정으로 내 화면이 after 가 되면 안 된다."""
        body = _fn(_read(APPJS), "asxStage")
        self.assertIn("myVerdict", body)
        self.assertNotIn("fb.verdict", body)

    def test_stage_is_sent_and_echo_is_compared(self):
        js = _read(APPJS)
        self.assertIn("'content_brief', { hash: hash, stage: stage }", js)
        self.assertIn("this.asxSent = stage", js)
        echo = _fn(js, "asxStageEcho")
        self.assertIn("this.asxSent", echo)

    def test_others_verdicts_are_not_even_fetched_before_mine(self):
        """판정 전에는 선례·다른 검수자 의견을 호출조차 하지 않는다."""
        load = _fn(_read(APPJS), "async asxLoad")
        self.assertIn("if (stage === 'after')", load)
        gate = load.index("if (stage === 'after')")
        for tool in ("verdict_precedents", "reviewer_dissent"):
            self.assertIn(tool, load[gate:], tool)
            self.assertNotIn(tool, load[:gate], f"{tool} 이 stage 게이트 밖에서 불립니다")

    def test_after_only_tools_carry_stage(self):
        """서버가 stage 를 필수로 받아 판정 전 호출을 거절한다(after_only) · 빠뜨리면 오류만 뜬다.

        실 백엔드 연동에서 실제로 밟은 자리다 — 초기 계약에는 두 도구에 stage 가 없었다."""
        load = _fn(_read(APPJS), "async asxLoad")
        for tool in ("verdict_precedents", "reviewer_dissent"):
            call = re.search(r"'" + tool + r"', \{[^}]*\}", load)
            self.assertIsNotNone(call, tool)
            self.assertIn("stage: stage", call.group(0), f"{tool} 호출에 stage 가 없습니다")

    def test_gold_looks_like_any_other_content(self):
        """골드에서 패널이 사라지면 그게 골드 신호다 · 패널은 그대로 두고 빈 상태로 그린다.

        서버를 부르지도 않는다(부르면 골드 거절 문구가 뜨거나, 뒤집기 전 참값이 내려온다)."""
        js = _read(APPJS)
        avail = _fn(js, "asxAvail")
        self.assertNotIn("asxGold", avail, "패널 유무로 골드를 가르면 검수자가 골드를 배웁니다")
        load = _fn(js, "async asxLoad")
        self.assertIn("this.asxGold(this.detail)", load)
        gold = load[load.index("this.asxGold(this.detail)"):]
        self.assertIn("return;", gold)
        self.assertNotIn("asxErr", gold)                               # 골드에서 오류 문구가 뜨면 안 된다

    def test_summary_and_criteria_never_come_from_the_server(self):
        """불변식: 패널에 그리는 텍스트는 화면 값 또는 공용 사전에서만 나온다.

        골드 문항은 큐가 등급을 뒤집어 보여주는데(reviewops._inject_gold) 서버 도구는 같은
        해시의 저장된 참값을 읽는다. 서버가 만든 요약을 그리면 화면과 어긋나고, 그 어긋남만으로
        뒤집힌 문항이 드러난다 = 정답 유출."""
        js = _read(APPJS)
        summ, crit = _fn(js, "asxSummary"), _fn(js, "asxCriteria")
        for body, name in ((summ, "asxSummary"), (crit, "asxCriteria")):
            self.assertIn("this.detail", body, name)
            self.assertNotIn("asxBrief", body, f"{name} 가 서버 응답을 읽고 있습니다")
        self.assertIn("INTENT_DEF", crit)                              # /dict = get_taxonomy 와 같은 원천
        self.assertIn("qualityMetas", crit)
        # 뒤집기 규칙을 화면이 다시 구현하면 그 어긋남이 새 오라클이 된다
        for banned in ("flip", "뒤집", "golden_hashes"):
            self.assertNotIn(banned, summ + crit, banned)

    def test_gold_flip_rule_still_matches_the_comment(self):
        """이 패널의 설계 근거(큐가 골드 등급을 뒤집는다)가 사라지면 설계도 다시 봐야 한다."""
        src = _read(os.path.join(ROOT, "prism", "reviewops.py"))
        self.assertIn("flip = int(h, 16) % 2 == 1", src)
        self.assertIn('"hash": f"gold:', src)

    def test_missing_route_disables_quietly(self):
        """/assist 404 = 보조 영역만 조용히 비활성 · 토스트(_err)로 검수를 방해하지 않는다."""
        js = _read(APPJS)
        self.assertIn("r.status === 404", js)
        self.assertIn("this.asxOff = true", js)
        self.assertNotIn("this._err(", js)

    def test_closed_panel_never_calls(self):
        """접혀 있으면 부르지 않는다(호출 비용 · 주의 분산)."""
        self.assertIn("if (!this.asxOpen", _fn(_read(APPJS), "asxWatch"))

    def test_suggestions_guarded_by_both_sides(self):
        """화면 판단과 서버가 되돌려 준 단계가 모두 '판정 후'일 때만 제안을 그린다."""
        js = _read(APPJS)
        on = _fn(js, "asxSuggestOn")
        self.assertIn("asxAfter()", on)
        self.assertIn("asxEff() === 'after'", on)
        self.assertIn("asxSuggestOn()", _fn(js, "asxSuggest"))

    def test_parses_as_javascript(self):
        if not shutil.which("node"):
            self.skipTest("node 미설치")
        r = subprocess.run(["node", "--check", APPJS], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[:400])


# ── 실행 단언(정적 검사로 못 잡는 것) ────────────────────────────────────────
# 앞의 검사들은 "코드가 서버 응답을 읽는가" 를 문자열로 본다. 그것만으로는 부족하다.
# 이번 작업에서 독립성이 깨진 세 자리 중 **플레이스홀더 건은 클라이언트의 렌더 선택**이라
# 서버 응답이 양쪽 다 같았고(title=""), 서버 테스트로는 원리적으로 잡히지 않았다.
# 잡을 수 있는 곳이 여기뿐이라 조각을 실제로 실행해 결과를 맞대 본다.
#
# 핵심은 '포함'이 아니라 '동일성'이다. "골드 자료가 안 들어갔다" 는 세 번 다 통과했다.
# 물어야 할 것은 "평범한 행과 골드 행의 결과가 같은가" 이고, 다른 곳은 화면에 이미
# 그려진 등급 한 글자뿐이어야 한다.
_HARNESS = r"""
const fs = require('fs');
global.window = {};
eval(fs.readFileSync(__JS__, 'utf8'));
const part = window.PRISM_APP_PARTS[0]();
const stubs = {                                  // 다른 조각이 주는 것들(app-01·03·08)
  catKo: (v) => v,
  reasonBoth: (v) => v,
  INTENT_DEF: { '속보·사건 추적': '막 발생한 사건을 처음 알리는 글.' },
  dictData: { qualityMetas: { ad: '광고성 정의문' }, qualityNames: { ad: '광고성' } },
  myVerdict: () => '',
  finalMode: false,
};
const app = Object.assign({}, stubs, part);
const row = { hash: 'abc123', service: '뉴스', title: '전기요금 개편안 발표, 가구별 영향은',
              category: ['News/Politics'], grade: 'G', reasons: ['ad'],
              intent: ['속보·사건 추적'], entities: ['전기요금', '가구별'], fb: {} };
const brief = { stage: 'before', has_evidence: false, evidence: null };

app.detail = row;                                // (가) 평범한 행
app.asxBrief = brief;
const normal = { summary: app.asxSummary(), criteria: app.asxCriteria(), avail: app.asxAvail() };

// (나) 같은 콘텐츠의 골드 사본 — 큐가 등급을 뒤집어 보여준다(_inject_gold) · 서버는 안 부른다
app.detail = Object.assign({}, row, { hash: 'gold:bad:abc123', grade: 'R' });
app.asxBrief = brief;
const gold = { summary: app.asxSummary(), criteria: app.asxCriteria(), avail: app.asxAvail() };

// (다) 서버가 저장된 참값(뒤집기 전 등급 G)을 실어 보내도 화면은 그걸 그리지 않는다
app.asxBrief = Object.assign({}, brief, {
  summary3: ['서버가 만든 요약', '모델 초안: 등급 G · 광고성', '서버 셋째 줄'],
  values: { grade: 'G' },
  criteria: [{ key: '서버기준', desc: '서버가 고른 기준' }],
});
const poisoned = { summary: app.asxSummary(), criteria: app.asxCriteria() };

console.log(JSON.stringify({ normal, gold, poisoned }));
"""


class TestAssistGoldParityByExecution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("node 미설치")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(_HARNESS.replace("__JS__", json.dumps(APPJS)))
            path = f.name
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True)
        finally:
            os.unlink(path)
        if r.returncode != 0:
            raise AssertionError("조각 실행 실패: " + r.stderr[:500])
        cls.out = json.loads(r.stdout)

    def test_gold_row_still_gets_the_panel(self):
        """패널 유무로 골드를 가르면 검수자가 골드를 배운다."""
        self.assertTrue(self.out["normal"]["avail"])
        self.assertTrue(self.out["gold"]["avail"])

    def test_summary_differs_only_by_what_is_on_screen(self):
        """평범한 행과 골드 사본의 3줄 요약은 **화면에 그려진 등급 한 글자**만 달라야 한다.

        골드는 큐가 등급을 뒤집어 보여주므로 요약도 뒤집힌 값을 따라가야 화면과 일치한다.
        서버가 만든 요약을 그리면 여기서 화면 등급과 요약 등급이 갈리고, 그 어긋남만으로
        뒤집힌 문항이 드러난다."""
        a, b = self.out["normal"]["summary"], self.out["gold"]["summary"]
        self.assertEqual(len(a), 3)
        self.assertEqual(len(b), 3)
        self.assertEqual(a[0], b[0])                                   # 서비스·제목·카테고리
        self.assertEqual(a[2], b[2])                                   # 인텐트·개체
        self.assertIn("등급 G", a[1])
        self.assertIn("등급 R", b[1])
        self.assertEqual(a[1].replace("등급 G", "등급 R"), b[1])        # 나머지는 한 글자도 다르지 않다

    def test_criteria_are_identical(self):
        """분류 기준은 공용 사전에서 오므로 골드든 아니든 같아야 한다."""
        self.assertEqual(self.out["normal"]["criteria"], self.out["gold"]["criteria"])
        self.assertTrue(self.out["gold"]["criteria"])                  # 빈 비교로 통과하지 않게

    def test_server_values_never_reach_the_panel(self):
        """서버가 뒤집기 전 참값을 실어 보내도 화면 출력이 흔들리지 않는다(오라클 차단)."""
        self.assertEqual(self.out["poisoned"]["summary"], self.out["gold"]["summary"])
        self.assertEqual(self.out["poisoned"]["criteria"], self.out["gold"]["criteria"])
        blob = " ".join(self.out["poisoned"]["summary"]) + json.dumps(
            self.out["poisoned"]["criteria"], ensure_ascii=False)
        for leaked in ("서버가 만든 요약", "서버 셋째 줄", "서버기준", "서버가 고른 기준"):
            self.assertNotIn(leaked, blob, leaked)


if __name__ == "__main__":
    unittest.main()
