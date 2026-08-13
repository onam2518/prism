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


def _dissent_block(markup: str) -> str:
    """'다른 검수자 의견' 섹션 마크업만 떼어 낸다.

    문자열 첫 등장으로 자르면 상단 배지 툴팁이 잡힌다(그 문구에도 같은 말이 들어 있다).
    섹션 라벨을 앵커로 쓴다."""
    start = markup.index('<div class="dve__lbl">다른 검수자 의견')
    end = markup.index('<div class="dve__lbl">비슷한 교정 사례')
    return markup[start:end]


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
        for needle in ("asxPrecItems()", "asxDisLines()", "asxSuggest()", "asxCut("):
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
        """조용한 절단은 '다 봤다'로 읽힌다 · 선례와 다른 검수자 의견 양쪽 다.

        뜻이 서로 다르다 — 선례는 목록이 잘린 것이고, 의견 요약은 둘째 줄의 **지적 요소
        나열**이 잘린 것이다(의견 자체는 전부 셌다). 의견 요약에 '몇 건 중 몇 건' 을 쓰면
        의견을 일부만 봤다는 뜻으로 읽힌다."""
        m, js = _read(MARKUP), _read(APPJS)
        self.assertIn("asxCut(asxPrec)", m)
        self.assertIn("asxDisCut()", m)
        self.assertIn("건만 보여줍니다", _fn(js, "asxCut"))
        cut = _fn(js, "asxDisCut")
        self.assertIn("지적한 요소가 더 있습니다", cut)
        self.assertNotIn("건 중", cut)
        self.assertNotIn("4", cut)                                     # 상한 값은 서버 한 곳에만

    def test_precedent_shows_how_many_agreed(self):
        """몇 사람이 그렇게 봤는지가 검수자가 무게를 다는 근거다."""
        self.assertIn("asxWho(p.n)", _read(MARKUP))

    def test_dissent_is_a_digest_not_a_roster(self):
        """다른 검수자 의견은 사람별 나열이 아니라 서버가 조립한 3줄 요약이다.

        디테일이 과하면 판단을 저해한다 — 사람 수만큼 이름·사유 원문을 읽는 동안
        특정 사람의 문장에 판단이 끌려간다."""
        m = _read(MARKUP)
        blk = _dissent_block(m)
        self.assertIn("asxDisLines()", blk)
        self.assertIn("asxDisN()", blk)
        for gone in ("v.reviewer", "v.reason", "asxVerdictLabel(v", "asxDisItems"):
            self.assertNotIn(gone, blk, f"{gone} 이 아직 그려집니다")
        self.assertIn("의견 갈림", blk)                                  # split 배지는 유지
        self.assertIn("다른 의견 없음", blk)                              # 비면 자리를 채우지 않는다

    def test_dissent_lines_are_drawn_verbatim(self):
        """서버가 준 줄을 그대로 쓴다 · 화면이 다시 가공하면 왜 그렇게 보이는지가 두 곳으로 갈린다."""
        m = _read(MARKUP)
        blk = _dissent_block(m)
        self.assertIn('x-text="s"', blk)                                # 줄 자체는 손대지 않는다
        body = _fn(_read(APPJS), "asxDisLines")
        for banned in (".slice(", ".join(", ".map(", ".substring(", ".replace("):
            self.assertNotIn(banned, body, f"asxDisLines 가 {banned} 로 가공합니다")

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

    def test_gold_flip_target_still_matches_this_panel_design(self):
        """이 패널의 설계 근거는 '큐가 골드의 **어떤 값을** 뒤집는가' 다.

        종전 이 테스트는 뒤집기 **식**(`flip = int(h,16) % 2 == 1`)과 해시 접두만 봤다.
        2026-08-13 에 뒤집는 대상이 등급에서 카테고리로 바뀌었는데 식도 접두도 그대로라
        **그대로 통과했다**. 감시 장치가 감시를 안 하고 있었던 것이다. 대상이 무엇인지
        모르면 이 패널이 무엇을 가려야 하는지도 알 수 없으므로(가릴 자리가 곧 뒤집는 자리다)
        문자열이 아니라 **큐가 실제로 내보내는 값**으로 못박는다.

        여기가 깨지면 패널 설계를 다시 봐야 한다: 저장된 참값을 보여 주는 자리
        (`content_brief` 의 values·suggestions)에서 **새로 바뀐 그 요소**를 가려야 한다.
        """
        from prism import feedback_loop as FL
        from prism import reviewops as RV
        self.assertIn(RV.GOLD_FLIP_ELEMENT, FL.ELEMENTS)          # 검수 요소 어휘 안의 값
        h = "0" * 15 + "1"                                        # 홀수 = 뒤집기 변형
        self.assertTrue(int(h, 16) % 2 == 1)
        content = {"displayServiceName": "뉴스", "title": "골드", "subtitle": "", "body": "본문"}
        exp = {"finalGrade": "G", "reasons": ["ad"], "summary": "리드문",
               "entities": ["개체A"], "intent": ["실용 정보"],
               "content_category": ["Sports / Golf", "Travel / Hotels"]}
        om = {"model": "m", "version": 3, "review": "auto", "url": "https://ex.test/x"}
        shown = RV.gold_wrong_category(exp["content_category"], h)
        item = RV._gold_item(h, content, exp, om, shown, True)
        self.assertTrue(item["hash"].startswith("gold:bad:"))
        # 큐가 보여 주는 값 ↔ 골든 정답: 다른 자리가 GOLD_FLIP_ELEMENT 하나뿐이어야 한다
        # 검수 요소 id → (큐 행 키, 골든 정답 키). quality(품질 사유)는 행에서 reasons 다.
        pairs = {"summary": ("summary", "summary"), "entities": ("entities", "entities"),
                 "intent": ("intent", "intent"), "category": ("category", "content_category"),
                 "grade": ("grade", "finalGrade"), "quality": ("reasons", "reasons")}
        self.assertEqual(set(pairs), set(FL.ELEMENTS))            # 요소가 늘면 여기도 봐야 한다
        differ = {el for el, (rk, ek) in pairs.items() if item[rk] != exp[ek]}
        self.assertEqual(differ, {RV.GOLD_FLIP_ELEMENT},
                         f"큐가 뒤집는 자리가 바뀌었습니다: {differ} · 패널이 가리는 자리도 함께 봐야 합니다")

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
(async () => {
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

// (라) 골드에서 asxLoad 가 서버를 **부르지 않고** 빈 의견 요약을 만든다.
//      _afetch 를 부르면 즉시 실패하도록 심어 둔다 — 정적 검사가 아니라 실행으로 막는다.
let called = 0;
app._afetch = () => { called += 1; return Promise.reject(new Error('골드는 서버를 부르면 안 된다')); };
app._authHeaders = () => ({});
app.myVerdict = () => 'good';                    // 판정 후 = 선례·의견을 부르는 단계
app.detail = Object.assign({}, row, { hash: 'gold:bad:abc123', grade: 'R' });
app.asxOff = false; app.asxKey = '';
await app.asxLoad();
const goldDis = { called, lines: app.asxDisLines(), n: app.asxDisN(), cut: app.asxDisCut(),
                  split: !!(app.asxDis || {}).split, prec: app.asxPrecItems().length };

// (마) 평범한 콘텐츠인데 의견이 하나도 없을 때(서버가 빈 요약을 준다)
app.asxDis = { lines: [], n: 0, split: false, truncated: false };
app.asxPrec = { items: [], total: 0, truncated: false };
const bareDis = { called, lines: app.asxDisLines(), n: app.asxDisN(), cut: app.asxDisCut(),
                  split: !!app.asxDis.split, prec: app.asxPrecItems().length };

// (바) 요약이 있을 때: 줄은 그대로 · 인원은 남고 · 이름/사유가 남아 와도 안 그린다 · 잘림은 '반영' 문장
app.asxDis = { lines: ['정확 쪽이 우세합니다.', '분류가 넓다는 지적이 있습니다.', '리드문은 문제없다고 봤습니다.'],
               n: 4, split: true, truncated: true,
               items: [{ reviewer: '복실', reason: '분류 적절', verdict: 'good' }] };
const digest = { lines: app.asxDisLines(), n: app.asxDisN(), cut: app.asxDisCut() };

console.log(JSON.stringify({ normal, gold, poisoned, goldDis, bareDis, digest }));
})();
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

    def test_gold_never_calls_the_server_for_dissent(self):
        """정적 검사가 아니라 실행으로 막는다 · _afetch 를 부르면 실패하도록 심어 뒀다."""
        self.assertEqual(self.out["goldDis"]["called"], 0)

    def test_gold_dissent_looks_like_a_content_with_no_opinions(self):
        """골드의 의견 요약이 '의견이 하나도 없는 평범한 콘텐츠'와 한 글자도 다르지 않아야 한다."""
        self.assertEqual(self.out["goldDis"], self.out["bareDis"])
        self.assertEqual(self.out["goldDis"]["lines"], [])
        self.assertEqual(self.out["goldDis"]["cut"], "")               # 없는 절단을 말하지 않는다

    def test_dissent_lines_pass_through_untouched(self):
        """서버가 준 줄을 그대로 쓴다 · 인원은 남기고 이름·사유는 남아 와도 안 쓴다."""
        d = self.out["digest"]
        self.assertEqual(d["lines"], ["정확 쪽이 우세합니다.", "분류가 넓다는 지적이 있습니다.",
                                      "리드문은 문제없다고 봤습니다."])
        self.assertEqual(d["n"], 4)
        blob = " ".join(d["lines"]) + d["cut"]
        for leaked in ("복실", "분류 적절"):
            self.assertNotIn(leaked, blob, leaked)

    def test_truncated_digest_talks_about_elements_not_opinions(self):
        """의견 요약의 truncated 는 둘째 줄의 지적 요소가 잘렸다는 뜻이다.

        의견 자체는 전부 셌으므로 '몇 건 중 몇 건' 으로 쓰면 안 된다 — 일부만 봤다는
        뜻으로 읽힌다."""
        cut = self.out["digest"]["cut"]
        self.assertEqual(cut, "지적한 요소가 더 있습니다 · 많이 나온 것부터 적었습니다")
        self.assertNotIn("건 중", cut)


if __name__ == "__main__":
    unittest.main()
