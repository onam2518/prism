"""검수 보조 패널(트랙 A · 화면) 회귀 가드.

이 패널은 품질 측정의 독립성 위에 서 있다. 판정 전에 추천·정답 암시가 새면 재는 대상이
사람이 아니라 모델이 되어 버린다. 그래서 문구가 아니라 **규칙**을 단언한다.

  1. 기본은 접힘이고 펼 때만 부른다(호출 비용 + 주의 분산).
  2. stage 는 화면이 정하고 근거는 내 표(myVerdict) 하나뿐이다.
  3. 수정 제안은 stage=after 에서만 그린다(서버 계약 + 화면 이중 방어).
  4. 골드 문항에는 패널이 붙지 않는다.
  5. 근거가 없으면 없다고 쓰고, 잘렸으면 잘렸다고 쓴다.
  6. /assist 가 없어도(404) 화면이 깨지지 않는다.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKUP = os.path.join(ROOT, "prism", "ui", "19e-review-assist.html")
APPJS = os.path.join(ROOT, "prism", "vendor", "app-17-assist.js")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


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

    def test_suggestions_only_after_verdict(self):
        """수정 제안 영역은 stage=after 에서만 그린다(화면 쪽 이중 방어)."""
        m = _read(MARKUP)
        sec = m[m.index("수정 제안"):]
        self.assertIn("asxStage()==='after'", m)
        self.assertIn("asxSuggest()", sec)
        # 판정 전 화면(선례·다른 검수자 의견)에는 제안 바인딩이 없다
        before = m[:m.index("수정 제안")]
        self.assertNotIn("asxSuggest", before)

    def test_missing_evidence_is_said_out_loud(self):
        """근거 필드 신설 이전 데이터는 실제로 비어 있다 · 빈 자리를 채우지 않는다."""
        m = _read(MARKUP)
        self.assertIn("저장된 근거 없음", m)
        self.assertIn("asxBrief && !asxBrief.has_evidence", m)

    def test_truncation_is_visible(self):
        """조용한 절단은 '다 봤다'로 읽힌다 · 잘림 표시를 화면에 남긴다."""
        self.assertIn("asxCut(asxPrec)", _read(MARKUP))

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
        js = _read(APPJS)
        m = re.search(r"asxStage\(\)\s*\{[^}]*\}", js)
        self.assertIsNotNone(m)
        self.assertIn("myVerdict", m.group(0))
        self.assertNotIn("fb.verdict", m.group(0))

    def test_stage_is_sent_to_server(self):
        js = _read(APPJS)
        self.assertIn("'content_brief', { hash: hash, stage: stage }", js)

    def test_gold_gets_no_assist(self):
        """골드는 검수자 신뢰도를 재는 장치 · 보조가 붙으면 측정이 사라진다."""
        js = _read(APPJS)
        self.assertIn("asxGold", js)
        self.assertIn("/^gold/", js)
        m = re.search(r"asxAvail\(\)\s*\{.*?\n      \}", js, re.S)
        self.assertIsNotNone(m)
        self.assertIn("asxGold", m.group(0))

    def test_missing_route_disables_quietly(self):
        """/assist 404 = 보조 영역만 조용히 비활성 · 토스트(_err)로 검수를 방해하지 않는다."""
        js = _read(APPJS)
        self.assertIn("r.status === 404", js)
        self.assertIn("this.asxOff = true", js)
        self.assertNotIn("this._err(", js)

    def test_closed_panel_never_calls(self):
        """접혀 있으면 부르지 않는다(호출 비용 · 주의 분산)."""
        js = _read(APPJS)
        m = re.search(r"asxWatch\(\)\s*\{.*?\n      \}", js, re.S)
        self.assertIsNotNone(m)
        self.assertIn("if (!this.asxOpen", m.group(0))

    def test_suggestions_guarded_in_app_too(self):
        js = _read(APPJS)
        m = re.search(r"asxSuggest\(\)\s*\{.*?\n      \}", js, re.S)
        self.assertIsNotNone(m)
        self.assertIn("asxStage() !== 'after'", m.group(0))

    def test_parses_as_javascript(self):
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("node 미설치")
        r = subprocess.run(["node", "--check", APPJS], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[:400])


if __name__ == "__main__":
    unittest.main()
