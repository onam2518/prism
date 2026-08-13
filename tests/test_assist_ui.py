"""검수 보조 대화창(트랙 A · 화면) 회귀 가드.

이 화면은 품질 측정의 독립성 위에 서 있다. 판정 전에 남이 내린 판정이 새면 재는 대상이
사람이 아니라 모델(또는 먼저 판정한 사람)이 되어 버린다. 그래서 문구가 아니라 **규칙**을
단언한다.

  1. 판정 전에는 선례·다른 검수자 의견·교정 사례를 **부르지 않는다.** 칩은 잠긴 채로 보이고
     눌러도 아무 일도 일어나지 않는다. 숨기지 않는 이유는 2번이다.
  2. 잠김은 **판정 단계로만** 정해진다. 칩 구성·문구·잠김이 콘텐츠를 타면 그 차이로 골드를
     알아볼 수 있다(칩이 하나 없어지는 것도 신호다).
  3. stage 는 화면이 정하고 근거는 내 표(myVerdict) 하나뿐이다.
  4. 골드 문항에서 화면이 다르게 보이면 안 된다. 막는 방식이 골드를 알려 주면
     검수자가 그걸 배우고, 그 순간 측정 대상이 평소의 검수가 아니게 된다.
  5. 근거가 없으면 없다고 쓰고, 잘렸으면 잘렸다고 쓴다.
  6. 자유질문 답변은 **출처가 붙은 문장만** 그린다(서버가 걸러도 화면이 또 거른다).
  7. /assist 가 없어도(404) 화면이 깨지지 않는다.

## 여기 쓰는 서버 응답은 실물이다 (여기 손대는 다음 사람에게)

아래 `_LIVE_*` 는 로컬 실 서버(`python3 -m prism.serve --mock` · 임시 DB)에 골드 사본과
평범한 콘텐츠를 심고 **HTTP 로 받아 온 응답**이다. 계약 문서를 보고 목을 지어내면 문서가
틀린 자리에서 그 목이 조용하다 — 실제로 그렇게 `stage` 필수 누락을 늦게 잡았다.
계약이 바뀌면 목을 손으로 고치지 말고 서버에서 다시 받아 올 것.

**실행 단언(맨 아래 클래스)이 이 파일의 본체다.** 문자열 검사는 "코드가 무엇을 읽는가"만
보는데, 이번 기능에서 깨진 자리는 전부 *차이가 보이나* 였다. 조각을 실제로 실행해 골드
사본과 평범한 콘텐츠의 출력이 같은지 묻는다. 픽셀 단위 확인(칩 크기·창 높이)은 헤드리스
브라우저로 따로 쟀다(PR 본문).

## 무력화 실측 (2026-08-13 · 규칙을 하나씩 깨고 이 파일을 돌린 결과)

57개 중 이 파일이 52개를 잡는다(전체 탈출 0). 괄호 안은 **잡은 단언 수**다.
33~36·39(본인 판정 제외의 서버 쪽)는 `tests/test_reviewassist.py`·`tests/test_assist_ask.py`
가 잡는다 — 그쪽 실측은 `TestMyOwnVerdictIsExcluded` 주석 참고.

  01 잠긴 칩을 화면에서 지움 (1)      · 02 잠긴 칩도 눌리게 (2)
  03 판정 전 자유질문 허용 (2)        · 04 출처 없는 문장 통과 (1)
  05 없는 출처 id 유지 (1)           · 06 서버가 만든 요약을 그림 (1)
  07 서버가 고른 기준을 그림 (2)      · 08 골드에서 대화창을 숨김 (7)
  09 골드에서 서버를 안 부름 (4)      · 10 의견을 사람별로 넘김 (1)
  11 의견 3줄을 화면이 자름 (2)       · 12 모은 인원 수 삭제 (2)
  13 선례 잘림 표시 삭제 (2)          · 14 의견 잘림을 '몇 건 중 몇 건' 으로 (2)
  15 빈 근거 자리를 지어냄 (2)        · 16 가려진 제목을 '(제목 없음)' 으로 (1)
  17 stage 를 팀 합의로 (10)         · 18 after_only 도구에 stage 누락 (2)
  19 404 에 토스트 (1)               · 20 404 를 보고도 계속 호출 (2)
  21 자유질문 404 가 전체를 끔 (1)    · 22 답한 모델 미노출 (1)
  23 콘텐츠가 바뀌어도 대화 잔류 (1)   · 24 콘텐츠가 바뀌면 창을 닫음 (1)
  25 정책 예시에 해시 전송 (2)        · 26 예시 초안 표시 제거 (1)
  27 잠긴 칩이 몰래 선례를 미리 부름 (2) · 28 버튼 자리를 px 로 박음 (1)
  29 이미지가 없으면 버튼째 숨김 (1)   · 30 고친 값을 굵기로 강조 (1)
  31 교정 사례를 '권장' 이라 부름 (1)  · 32 서버 단계 강등을 조용히 넘김 (1)
  37 인원 수 문구에서 '내 판정은 빼고' 삭제 (2) · 38 reviewer 를 도구 인자에 실음 (2)
  ── 첫 인사(제목) · 어디서나 뜨는 버튼과 안내(2026-08-13 3회차) ──
  40 인사 제목을 서버 응답에서 가져옴 (4) · 41 서버 요약을 인사로 씀 (1)
  42 빈 제목을 '(제목 없음)' 으로 채움 (1) · 43 인사를 반말로 (3)
  44 골드에서만 제목을 자름 (3)             · 45 옮겨도 옛 제목으로 인사 (1)
  46 버튼을 다시 검수 상세에서만 띄움 (1)   · 47 안내를 한 문구로 뭉뚱그림 (1)
  48 이미 그 화면인데 바로가기를 붙임 (1)   · 49 최종 검수에서 '콘텐츠 검수로 가기' (1)
  50 바로가기가 죽은 링크가 됨 (1)          · 51 최종 검수 바로가기가 탭을 안 바꿈 (1)
  52 안내 자리에 칩을 그림 (1)              · 53 안내 자리에 입력칸을 그림 (1)
  54 안내 자리에서 칩 호출이 나감 (1)       · 55 안내가 콘텐츠 값을 봄 (2)
  56 화면을 옮기면 창을 닫음 (2)            · 57 옮겨도 옛 대화가 남음 (1)

⚠️ **(1) 인 항목은 그 단언이 유일한 눈이다. 지우지 말 것.** 57개 중 33개가 그렇다.

56·57 은 3회차에서 **탈출했다**(0건). 화면을 옮겼다 돌아오는 경로가 검수 안에서 콘텐츠가
바뀌는 경로와 달라서, 콘텐츠 추종만 재던 단언에 걸리지 않았다. 왕복을 그대로 재는 단언
(`test_the_window_stays_open_across_screens`·`test_no_stale_talk_survives_leaving_the_review_screen`·
`test_coming_back_binds_to_the_new_content`)을 추가해 막았다.

15·22 는 처음에 **탈출했다**(0건). 15 는 "저장된 근거 없음" 문자열이 파일 머리말 주석에도
있어 코드에서 문구를 바꿔도 문자열 검사가 통과했고, 22 는 응답 **필드**만 봐서 화면이 그
값을 안 그려도 통과했다. 둘 다 "출력을 본다" 로 고쳤다(`_code` 로 주석 제거 ·
`modelLine`·`empties` 로 그려진 문장 확인). 규칙을 고칠 때는 단언을 지우지 말고
**먼저 이 실험을 다시 돌려** 무엇이 유일한 눈인지부터 확인할 것.

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
CSS = os.path.join(ROOT, "prism", "vendor", "app.css")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _fn(js, name):
    """조각에서 메서드 본문 한 덩어리를 떼어 낸다(들여쓰기 6칸 관례)."""
    m = re.search(r"\n      " + name + r"\(.*?\n      \}", js, re.S)
    return m.group(0) if m else ""


def _block(markup, start, end):
    """start 부터 그 뒤에 처음 나오는 end 까지. end 를 문서 앞에서 찾으면 엉뚱한 데를 잘라
    검사가 조용히 아무것도 안 보게 된다."""
    i = markup.index(start)
    return markup[i:markup.index(end, i + len(start))]


def _shown(markup):
    """주석을 걷어 낸 마크업 = 화면에 실제로 나가는 문구."""
    return re.sub(r"<!--.*?-->", "", markup, flags=re.S)


def _code(js):
    """주석을 걷어 낸 코드. 주석이 같은 문구를 설명하고 있으면 문자열 검사가 속는다."""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return "\n".join(line.split("//")[0] for line in js.splitlines())


class TestAssistMarkup(unittest.TestCase):
    def test_fragment_is_composed_and_floats_over_the_page(self):
        """조각이 PAGE 에 합성되고 body 로 텔레포트된다.

        검수 상세 안에 두면 탭·조상 스타일에 눌리고, 상세 레이아웃(판정 UI)을 다시 밀어낸다."""
        from prism.page import PAGE
        self.assertIn('x-teleport="body"', _read(MARKUP))
        self.assertIn("asxfab", PAGE)
        self.assertIn("asxchat", PAGE)

    def test_the_old_in_detail_panel_is_gone(self):
        """검수 상세 안 자리표(#asxSlot)와 '검수 보조 열기' 버튼은 걷혔다."""
        from prism.page import PAGE
        self.assertNotIn('id="asxSlot"', PAGE)
        self.assertNotIn("검수 보조 열기'", PAGE)          # 상세 안 토글 버튼의 x-text 잔재

    def test_fragment_precedes_xdata_close(self):
        """조각 파일명이 x-data 루트를 닫는 20-* 보다 앞서야 스코프 안에 든다."""
        names = sorted(n for n in os.listdir(os.path.join(ROOT, "prism", "ui")) if n.endswith(".html"))
        self.assertIn("19e-review-assist.html", names)
        self.assertLess(names.index("19e-review-assist.html"), names.index("20-ingest-policy.html"))

    def test_floating_button_wears_the_reviewers_character(self):
        """버튼 얼굴은 그 사람이 고른 캐릭터(charOptions·reviewerChar 재사용 · 새로 만들지 않는다)."""
        m = _read(MARKUP)
        self.assertIn("charImg(reviewerChar)", m)
        self.assertIn('aria-label="검수 보조 열기"', m)
        self.assertIn("data-tip=\"검수를 대신하지 않습니다", m)     # 종전 버튼 툴팁을 이어받는다

    def test_button_survives_a_missing_image(self):
        """이미지가 깨져도 버튼은 남는다 · 그림만 숨긴다(버튼이 사라지면 부를 길이 없다)."""
        m = _read(MARKUP)
        err = re.search(r'class="asxfab__char".*?x-on:error="([^"]+)"', m, re.S)
        self.assertIsNotNone(err, "이미지 실패 처리가 없습니다")
        self.assertIn("target.style.display", err.group(1))
        self.assertNotIn("closest('button')", err.group(1))

    def test_the_greeting_bubble_is_drawn_from_the_message_text(self):
        """첫 인사는 말풍선 한 줄이다 · 마크업이 제목을 다시 만들지 않는다(문구는 JS 한 곳)."""
        m = _read(MARKUP)
        blk = _block(m, "m.kind === 'hello'", "m.kind === 'text'")
        self.assertIn('x-text="m.text"', blk)
        for banned in ("detail.title", "asxHello()", "asxBrief"):
            self.assertNotIn(banned, blk, f"인사 마크업이 {banned} 를 읽습니다")

    def test_chip_row_is_one_fixed_list(self):
        """칩은 asxChips 한 목록에서 나온다 · 콘텐츠에 따라 늘거나 줄면 그게 골드 힌트다."""
        m = _read(MARKUP)
        self.assertIn('x-for="c in asxChips"', m)
        chips = _block(m, 'class="asxchat__chips"', 'class="asxchat__ask"')
        for banned in ("asxAfter()", "detail.", "asxBrief"):
            self.assertNotIn(banned, chips, f"칩 목록이 {banned} 를 봅니다")

    def test_locked_chips_are_shown_not_hidden(self):
        """판정 전 칩은 지우지 않고 잠근다 · 없으면 '왜 없지', 잠겨 있으면 규칙이 드러난다.

        (칩 줄 전체는 '대화가 되는 자리'에서만 그린다 — 그건 화면 상태이지 판정 단계가 아니다.
        여기서 보는 것은 **칩 하나하나**가 단계 때문에 사라지지 않는가다.)"""
        chips = _block(_read(MARKUP), '<template x-for="c in asxChips"', "</template>")
        self.assertIn("asxLocked(c)", chips)
        self.assertIn("is-locked", chips)
        self.assertIn('aria-disabled', chips)
        for banned in ('x-if="', 'x-show="!asxLocked', 'x-show="asxLocked(c) ? false'):
            self.assertNotIn(banned, chips.replace('x-show="asxLocked(c)"', ""),
                             "잠긴 칩을 화면에서 지우고 있습니다")

    def test_free_question_opens_only_after_a_verdict_and_says_why(self):
        """자유질문은 판정 뒤에만 · 판정 전에는 왜 잠겼는지 한 줄로 알린다."""
        ask = _block(_read(MARKUP), 'class="asxchat__ask"', "</template>\n")
        self.assertIn('x-if="asxAfter()', ask)
        self.assertIn('x-show="!asxAfter()"', ask)
        self.assertIn("asxAskHint()", ask)
        self.assertIn("판정을 낸 뒤에", _fn(_read(APPJS), "asxAskHint"))

    def test_answers_always_carry_their_sources(self):
        """출처 없는 문장을 그릴 수 있는 자리를 화면에 두지 않는다(이 기능의 안전장치)."""
        ans = _block(_read(MARKUP), "m.kind === 'ans'", "m.kind === 'text'")
        self.assertIn('x-for="(a, ai) in m.lines"', ans)
        self.assertIn('x-for="(sid, si) in a.sources"', ans)
        self.assertIn("asxSrcLabel(m, sid)", ans)
        self.assertIn("asxModelLine(m)", ans)                 # 어떤 모델이 답했는지 보인다
        # 답변 줄은 asxAnsLines 가 거른 것만 온다(원본 result.answer 를 그대로 그리지 않는다)
        self.assertNotIn("m.answer", ans)

    def test_truncation_is_visible(self):
        """조용한 절단은 '다 봤다'로 읽힌다 · 선례 목록·의견 요약·자유질문 자료 셋 다."""
        m, js = _read(MARKUP), _read(APPJS)
        self.assertIn('x-show="m.cut"', m)
        self.assertIn('x-show="m.truncated"', m)
        self.assertIn("건만 보여줍니다", _fn(js, "asxCut"))
        cut = _fn(js, "asxDisCut")
        self.assertIn("지적한 요소가 더 있습니다", cut)
        self.assertNotIn("건 중", cut)
        self.assertNotIn("4", cut)                            # 상한 값은 서버 한 곳에만

    def test_empty_states_are_kept(self):
        """없는 자리를 채우지 않는다 · 빈 결과는 빈 결과라고 쓴다.

        주석을 걷고 본다 — 파일 머리말이 같은 문구를 설명하고 있어서, 코드에서 문구를
        바꿔도 주석만 보고 통과해 버린 적이 있다(무력화 실측 15번에서 드러났다)."""
        code = _code(_read(APPJS))
        for empty in ("저장된 근거 없음", "기준 없음", "선례 없음", "다른 의견 없음",
                      "비슷한 교정 사례 없음", "요약 없음", "사전에 없습니다"):
            self.assertIn(empty, code, empty)
        self.assertIn('x-text="m.empty"', _read(MARKUP))

    def test_precedent_shows_how_many_agreed(self):
        """몇 사람이 그렇게 봤는지가 검수자가 무게를 다는 근거다."""
        self.assertIn("asxWho(p.n)", _read(MARKUP))

    def test_masked_precedent_identifiers_are_left_blank(self):
        """선례가 정답셋 원본이면 서버가 hash·title·reason 을 지운다.

        빈 자리를 '(제목 없음)' 같은 문구로 채우면 '가려진 항목'이 오히려 눈에 띈다."""
        m = _read(MARKUP)
        self.assertIn('x-show="p.title"', m)
        self.assertIn('x-show="p.reason"', m)
        prec = _shown(m)
        prec = _block(prec, "m.kind === 'prec'", "m.kind === 'sug'")
        self.assertNotIn("제목 없음", prec)

    def test_the_headcount_says_it_excludes_me(self):
        """'3명 의견을 모았습니다' 로는 나를 포함하는지 알 수 없다 · 서버가 나를 뺀 수를 준다.

        읽는 사람이 헷갈린 채로 읽으면 무게를 잘못 단다(칩 이름이 '다른 검수자 의견' 인 것과
        같은 뜻이라 문구도 그렇게 적는다)."""
        self.assertIn("asxDisNote(m.n)", _read(MARKUP))
        note = _fn(_read(APPJS), "asxDisNote")
        self.assertIn("내 판정은 빼고", note)
        self.assertIn("명 의견을 모았습니다", note)

    def test_the_asker_is_sent_so_the_server_can_drop_my_own_verdict(self):
        """로컬(로그인 없음)에서도 내 판정을 뺄 수 있게 이름을 보낸다.

        **도구 인자(args)가 아니라 본문 최상위**에 둔다 — 인자로 받으면 남의 이름을 넣어
        두 번 불러 그 사람의 판정을 알아낼 수 있고, 그게 이 도구가 감추려는 것이다.
        운영(supabase)에서는 서버가 로그인 uid 로 덮어 이 값을 무시한다."""
        js = _read(APPJS)
        for fn in ("async asxCall", "async asxAsk"):
            body = _fn(js, fn)
            self.assertIn("reviewer: this.reviewer", body, fn)
        self.assertNotIn("args: { reviewer", js)
        self.assertNotIn("reviewer: this.reviewer })", _fn(js, "async asxAnswer"))

    def test_dissent_is_a_digest_not_a_roster(self):
        """다른 검수자 의견은 사람별 나열이 아니라 서버가 조립한 3줄 요약 말풍선 하나다."""
        js = _read(APPJS)
        blk = _block(js, "if (id === 'dissent')", "asxSummaryMsg()")
        self.assertIn("lines: (r.lines || [])", blk)           # 그대로 옮긴다
        self.assertIn("n: Number(r.n || 0)", blk)              # 인원은 남긴다
        for gone in ("r.reviewer", "r.reason", "r.verdicts", "r.items"):
            self.assertNotIn(gone, blk, f"{gone} 이 아직 화면으로 넘어갑니다")
        for banned in (".slice(", ".join(", ".map(", ".substring(", ".replace("):
            self.assertNotIn(banned, blk, f"의견 3줄을 {banned} 로 가공합니다")

    def test_suggestions_are_not_dressed_up(self):
        """교정 사례는 '센 사실'이다 · 권장·정답으로 읽히게 꾸미지 않는다."""
        m = _read(MARKUP)
        self.assertIn("s.basis", m)
        shown = _shown(m) + _read(APPJS)
        for word in ("권장", "추천", "정답"):
            self.assertNotIn(word, _shown(m), f"화면 문구에 '{word}' 가 있습니다")
        self.assertNotIn("'근거 · ' + s.basis", m)
        to = re.search(r"\.asx__to\{[^}]*\}", _read(CSS)).group(0)
        self.assertNotIn("font-weight", to)                    # 고친 값을 굵기로 밀지 않는다
        self.assertIn("draft", shown + _read(APPJS))           # 정책 예시 초안 표시는 서버 문장 그대로
        self.assertIn("draft_note", _read(APPJS))

    def test_chat_follows_the_policy_palette_conventions(self):
        """드래그 이동·자리 기억은 정책 팔레트(polpal)와 같은 관례를 쓴다."""
        m, js = _read(MARKUP), _read(APPJS)
        self.assertIn("asxDragStart($event)", m)
        self.assertIn('x-bind:style="asxStyle()"', m)
        self.assertIn("localStorage", _fn(js, "asxSave"))
        self.assertIn("localStorage", _fn(js, "asxRestore"))

    def test_two_floating_buttons_share_one_size_token(self):
        """정책 도움말(?) 버튼 왼쪽에 같은 높이로 선다 · 자리 계산에 px 를 다시 적지 않는다."""
        css = _read(CSS)
        self.assertIn("--ds-fab-size", css)
        pol = re.search(r"\.polfab\{[^}]*\}", css).group(0)
        fab = re.search(r"\.asxfab\{[^}]*\}", css).group(0)
        self.assertIn("var(--ds-fab-size)", pol)
        self.assertIn("var(--ds-fab-size)", fab)
        self.assertIn("right:calc(var(--ds-space-5) + var(--ds-fab-size) + var(--ds-space-2))", fab)
        self.assertNotIn("44px", fab)                          # 크기는 변수 한 곳에서만
        self.assertIn("bottom:var(--ds-space-5)", fab)         # 정책 버튼과 같은 높이


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

    def test_after_only_tools_carry_stage(self):
        """서버가 stage 를 필수로 받아 판정 전 호출을 거절한다(after_only) · 빠뜨리면 오류만 뜬다.

        실 백엔드 연동에서 실제로 밟은 자리다 — 초기 계약에는 두 도구에 stage 가 없었다."""
        js = _read(APPJS)
        for tool in ("verdict_precedents", "reviewer_dissent"):
            call = re.search(r"'" + tool + r"',\s*\n?\s*\{[^}]*\}", js)
            self.assertIsNotNone(call, tool)
            self.assertIn("stage: this.asxStage()", call.group(0), f"{tool} 호출에 stage 가 없습니다")
        self.assertIn("stage: stage", _fn(js, "async asxLoadBrief"))

    def test_free_question_sends_the_after_stage_and_the_screens_hash(self):
        """해시는 화면이 열고 있는 콘텐츠 · 단계는 after(서버가 판정 전 질문을 거절한다)."""
        ask = _fn(_read(APPJS), "async asxAsk")
        self.assertIn("'/assist-ask'", ask)
        self.assertIn("stage: 'after'", ask)
        self.assertIn("hash: hash", ask)
        self.assertIn("!this.asxAfter()", ask)                 # 판정 전에는 부르지 않는다

    def test_the_panel_has_no_gold_branch_at_all(self):
        """골드 분기가 화면에 하나도 없어야 한다.

        분기는 언젠가 화면 차이로 새고, 검수자는 그 차이로 골드를 배운다. 골드를 안전하게
        만드는 일은 전부 서버가 한다(reviewassist 가 큐가 뒤집는 그 한 자리만 비운다)."""
        js = _read(APPJS)
        code = "\n".join(line.split("//")[0] for line in js.splitlines())   # 줄 주석 제외
        code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)                   # 블록 주석 제외
        for banned in ("asxGold", "'gold", '"gold', "/^gold", "isGoldRow"):
            self.assertNotIn(banned, code, f"화면에 골드 분기가 남아 있습니다: {banned}")
        mk = _shown(_read(MARKUP))
        self.assertNotIn("gold", mk)

    def test_summary_and_criteria_never_come_from_the_server(self):
        """불변식: 말풍선에 그리는 텍스트는 화면 값 또는 공용 사전에서만 나온다.

        골드 문항은 큐가 카테고리를 뒤집어 보여주는데(reviewops._inject_gold) 서버 도구는 같은
        해시의 저장된 참값을 읽는다. 서버가 만든 요약을 그리면 화면과 어긋나고, 그 어긋남만으로
        뒤집힌 문항이 드러난다 = 정답 유출."""
        js = _read(APPJS)
        summ, crit = _fn(js, "asxSummary"), _fn(js, "asxCriteria")
        for body, name in ((summ, "asxSummary"), (crit, "asxCriteria")):
            self.assertIn("this.detail", body, name)
            self.assertNotIn("asxBrief", body, f"{name} 가 서버 응답을 읽고 있습니다")
        self.assertIn("INTENT_DEF", crit)                      # /dict = get_taxonomy 와 같은 원천
        self.assertIn("qualityMetas", crit)
        self.assertNotIn("summary3", js)                       # 서버가 만든 요약은 쓰지 않는다
        for banned in ("flip", "뒤집", "golden_hashes"):
            self.assertNotIn(banned, summ + crit, banned)

    def test_dictionary_tools_are_asked_with_screen_values(self):
        """정책 예시·개체 사전은 **화면 값**으로 묻는다(해시를 보내지 않는다).

        해시를 보내면 서버가 저장된 참값으로 답하고, 골드에서는 그 답이 화면과 어긋난다."""
        js = _read(APPJS)
        for name in ("async asxExamples", "async asxEntities"):
            body = _fn(js, name)
            self.assertIn("this.detail", body, name)
            self.assertNotIn("hash", body, f"{name} 가 해시를 보냅니다")

    def test_gold_flip_target_still_matches_this_screen_design(self):
        """이 화면의 설계 근거는 '큐가 골드의 **어떤 값을** 뒤집는가' 다.

        대상이 무엇인지 모르면 이 화면이 무엇을 가려야 하는지도 알 수 없으므로(가릴 자리가 곧
        뒤집는 자리다) 문자열이 아니라 **큐가 실제로 내보내는 값**으로 못박는다.

        여기가 깨지면 화면 설계를 다시 봐야 한다: 저장된 참값을 보여 주는 자리
        (`content_brief` 의 values·suggestions)에서 **새로 바뀐 그 요소**를 가려야 한다."""
        from prism import feedback_loop as FL
        from prism import reviewops as RV
        self.assertIn(RV.GOLD_FLIP_ELEMENT, FL.ELEMENTS)
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
        pairs = {"summary": ("summary", "summary"), "entities": ("entities", "entities"),
                 "intent": ("intent", "intent"), "category": ("category", "content_category"),
                 "grade": ("grade", "finalGrade"), "quality": ("reasons", "reasons")}
        self.assertEqual(set(pairs), set(FL.ELEMENTS))            # 요소가 늘면 여기도 봐야 한다
        differ = {el for el, (rk, ek) in pairs.items() if item[rk] != exp[ek]}
        self.assertEqual(differ, {RV.GOLD_FLIP_ELEMENT},
                         f"큐가 뒤집는 자리가 바뀌었습니다: {differ} · 화면이 기대는 전제가 바뀌었습니다")

    def test_missing_route_disables_quietly(self):
        """/assist 404 = 조용히 접기 · 토스트(_err)로 검수를 방해하지 않는다."""
        js = _read(APPJS)
        self.assertIn("r.status === 404", js)
        self.assertIn("this.asxOff = true", js)
        self.assertNotIn("this._err(", js)

    def test_suggestions_guarded_by_both_sides(self):
        """화면 판단과 서버가 되돌려 준 단계가 모두 '판정 후'일 때만 교정 사례를 그린다."""
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
# 앞의 검사들은 "코드가 무엇을 읽는가" 를 문자열로 본다. 그것만으로는 부족하다. 이 계층에서
# 깨진 자리는 전부 *차이가 보이나* 였고, 그중 하나(빈 제목을 '(제목 없음)' 으로 채운 것)는
# 서버 응답이 양쪽 다 같아서 서버 테스트로는 원리적으로 안 잡혔다. 조각을 실제로 실행해
# 골드 사본과 평범한 콘텐츠의 **출력을 통째로 맞대 본다.**
#
# 아래 서버 응답은 로컬 실 서버에서 HTTP 로 받아 온 실물이다(파일 머리말 참고).
_LIVE_BRIEF_AFTER = {
    "stage": "after",
    "summary3": ["뉴스 · 전기요금 개편안 발표", "모델 초안: 등급 R · 광고성",
                 "모델이 남긴 판정 근거: 본문이 제품 구매 링크로 끝나 광고성으로 봤다"],
    "evidence": "본문이 제품 구매 링크로 끝나 광고성으로 봤다", "has_evidence": True,
    "values": {"service": "뉴스", "title": "전기요금 개편안 발표", "grade": "R",
               "reasons": ["ad"], "reason_labels": ["광고성"], "intent": ["실용 정보"],
               "content_category": [], "entities": ["김도현"], "summary": "리드문"},
    "criteria": [{"key": "실용 정보", "desc": "독자가 바로 따라 할 수 있는 …"}],
    "suggestions": [],
}
_LIVE_PREC = {"items": [{"hash": "8a05f80a33383caa", "title": "광고성 판정 사례 2",
                         "verdict": "bad", "n": 2, "reason": "광고성으로 봤다", "ts": 1786585895.4,
                         "why_similar": "같은 서비스: 뉴스 · 같은 품질 사유: 광고성"}],
              "total": 1, "truncated": False}
_LIVE_DISSENT = {"lines": ["판정 정확 1명 · 수정 필요 2명", "지적한 요소: 품질 사유 2명 · 카테고리 1명",
                           "의견 갈림 · 소수 의견 1명"], "n": 3, "split": True, "truncated": False}
_LIVE_DISSENT_GOLD = {"lines": [], "n": 0, "split": False, "truncated": False}
_LIVE_EXAMPLES = {"items": [{"key": "Golf", "label": "골프", "desc": "골프 대회·선수",
                             "example": "KPGA 투어 우승", "has_example": True, "draft": True, "note": ""}],
                  "total": 1, "truncated": False, "kind": "category", "draft": True,
                  "draft_note": "예시는 초안입니다(팀 확정 전) · 확정 정책이 아니므로 판단 기준으로 삼지 마세요 · 정의(desc)는 확정 사전입니다",
                  "unknown": []}
_LIVE_ENTITY = {"items": [{"entity_id": "e1", "name": "김도현", "type": "person",
                           "status": "confirmed", "aliases": []}],
                "total": 1, "truncated": False, "query": "김도현"}
# 자유질문: 가짜 모델이 계약을 어긴 응답을 냈고 **서버가 두 줄을 버린 뒤** 남은 실물.
# 화면도 한 번 더 거르는지 보려고 여기서 다시 오염시킨다(아래 poisoned).
_LIVE_ASK = {
    "stage": "after", "question": "이거 왜 그렇게 봤어?",
    "answer": [{"text": "저장된 근거에는 본문이 구매 링크로 끝난다고 적혀 있습니다.", "sources": ["s1"]},
               {"text": "부여된 인텐트는 실용 정보입니다.", "sources": ["s3", "s4"]}],
    "sources": [{"id": "s1", "tool": "content_brief", "field": "evidence",
                 "label": "모델이 남긴 판정 근거", "text": "본문이 제품 구매 링크로 끝나 광고성으로 봤다"},
                {"id": "s3", "tool": "content_brief", "field": "values.intent",
                 "label": "부여된 인텐트", "text": "인텐트 실용 정보"},
                {"id": "s4", "tool": "content_brief", "field": "criteria.실용 정보",
                 "label": "분류 기준 · 실용 정보", "text": "독자가 바로 따라 할 수 있는 …"}],
    "generated": True, "model": "solar-pro2", "model_label": "Solar Pro 2",
    "note": "", "remaining": 29, "truncated": False,
}

_HARNESS = r"""
const fs = require('fs');
global.window = {};
global.localStorage = { getItem: () => null, setItem: () => {} };
eval(fs.readFileSync(__JS__, 'utf8'));
const LIVE = __LIVE__;
const part = window.PRISM_APP_PARTS[0]();
const stubs = {                                  // 다른 조각이 주는 것들(app-00·03·04)
  catKo: (v) => v,
  reasonBoth: (v) => v,
  charOptions: [{ id: 'boksil', label: '복실', img: '/vendor/boksil-catcher.svg' },
                { id: 'ddakji', label: '딱지', img: '/vendor/ddakji-manager.svg' }],
  charImg(id) { return (this.charOptions.find((c) => c.id === id) || this.charOptions[0]).img; },
  reviewerChar: 'ddakji',
  INTENT_DEF: { '실용 정보': '독자가 바로 따라 할 수 있는 방법·조건·절차를 알려주는 것.' },
  dictData: { qualityMetas: { ad: '광고성 정의문' }, qualityNames: { ad: '광고성' } },
  _myVerdict: '',
  myVerdict() { return this._myVerdict; },
  fmtTs: () => '8.13 10:00',
  // 화면 상태(다른 조각이 준다): 콘텐츠 검수 메뉴 · 상세 열림 · 최종검수 맥락.
  // finalMode 는 실물과 같이 **게터**다(app-01) — 맥락이 풀리면 저절로 false 가 된다.
  // 여기서 평범한 불리언으로 두면 asxGo 가 맥락을 지워도 테스트만 계속 최종검수라고 믿는다.
  mod: 'create', detailOpen: true, createTab: 'raw', finalCtx: null,
  get finalMode() { return !!(this.finalCtx && this.detail && this.finalCtx.hash === this.detail.hash); },
  _went: [],
  selectMod(id) { this._went.push(id); this.mod = id; },
  $nextTick: (fn) => fn && fn(),
  $refs: {},
  _authHeaders: () => ({}),
};
// 로더(app.js)와 같은 방식으로 합친다 — **게터를 보존**해야 한다.
// Object.assign 은 게터를 그 순간의 값으로 굳혀 버려서 finalMode 같은 파생 상태가 죽는다.
const app = {};
Object.defineProperties(app, Object.getOwnPropertyDescriptors(stubs));
Object.defineProperties(app, Object.getOwnPropertyDescriptors(part));
const row = { hash: 'abc123', service: '뉴스', title: '전기요금 개편안 발표',
              category: ['Sports / Golf'], grade: 'R', reasons: ['ad'],
              intent: ['실용 정보'], entities: ['김도현'], fb: {} };
const goldSame = Object.assign({}, row, { hash: 'gold:bad:abc123' });
const goldReal = Object.assign({}, row, { hash: 'gold:bad:abc123',
                                          category: ['Business and Finance / Economy'] });

let calls = [];
let REPLY = {};
app._afetch = (url, opt) => {
  const b = JSON.parse(opt.body);
  if (url === '/assist-ask') {
    // 해시 **값**은 콘텐츠마다 다른 게 당연하다. 비교할 것은 '해시를 실었나' 다.
    calls.push('ask|' + b.stage + '|' + (b.hash ? 'hash' : 'empty')
               + '|' + (b.reviewer === undefined ? 'no-me' : 'me'));
    return Promise.resolve({ status: REPLY.__askStatus || 200,
                             json: async () => ({ ok: true, result: REPLY.ask }) });
  }
  calls.push(b.tool + '|' + (b.args.stage || '') + '|' + (b.args.limit || '') + '|' + (b.args.kind || '')
             + '|' + (b.args.hash === undefined ? '-' : (b.args.hash ? 'hash' : 'empty'))
             + '|' + (b.reviewer === undefined ? 'no-me' : 'me') + (b.args.reviewer ? '|IN-ARGS' : ''));
  return Promise.resolve({ status: REPLY.__status || 200,
                           json: async () => ({ ok: true, result: REPLY[b.tool] }) });
};

const normal = {
  content_brief: LIVE.brief, verdict_precedents: LIVE.prec, reviewer_dissent: LIVE.dissent,
  get_examples: LIVE.examples, lookup_entity: LIVE.entity, ask: LIVE.ask,
};
const gold = Object.assign({}, normal, { reviewer_dissent: LIVE.dissentGold });

async function run(detail, verdict, reply, opts) {
  opts = opts || {};
  calls = [];
  REPLY = reply;
  app._myVerdict = verdict;
  app.mod = 'create'; app.detailOpen = true; app.finalCtx = null;      // 검수 상세가 열린 자리
  app.asxOff = false; app.asxAskOff = false; app.asxOpen = false;
  app.asxKey = ''; app.asxMsgs = []; app.asxBrief = null; app.asxBriefKey = ''; app.asxErr = '';
  app.detail = detail;
  app.asxWatch();                                // x-effect 가 하는 일(콘텐츠 붙기)
  app.asxToggle();                               // 열기
  for (const c of app.asxChips) await app.asxChip(c);
  if (opts.ask) { app.asxQ = opts.ask; await app.asxAsk(); }
  const dis = app.asxMsgs.filter((m) => m.title === '다른 검수자 의견')[0];
  const ans = app.asxMsgs.filter((m) => m.kind === 'ans')[0];
  return { calls: calls.slice(), msgs: JSON.parse(JSON.stringify(app.asxMsgs)),
           chips: app.asxChips.map((c) => [c.label, app.asxLocked(c), app.asxChipTip(c)]),
           open: app.asxOpen, off: app.asxOff, askOff: app.asxAskOff, err: app.asxErr,
           avail: app.asxAvail(), fab: app.charImg(app.reviewerChar),
           // 화면이 실제로 그리는 문장들(빈 자리 문구·답한 모델) — 필드가 아니라 출력으로 본다
           modelLine: ans ? app.asxModelLine(ans) : '',
           disNote: dis && dis.n ? app.asxDisNote(dis.n) : '',
           askHint: app.asxAskHint(),
           empties: app.asxMsgs.filter((m) => m.empty !== undefined).map((m) => [m.title, m.empty]) };
}

(async () => {
const out = {};
out.normalAfter = await run(row, 'bad', normal, { ask: '이거 왜 그렇게 봤어?' });
out.goldSameAfter = await run(goldSame, 'bad', normal, { ask: '이거 왜 그렇게 봤어?' });
out.goldRealAfter = await run(goldReal, 'bad', gold, { ask: '이거 왜 그렇게 봤어?' });
out.normalBefore = await run(row, '', normal, { ask: '판정 전에도 물어볼래' });
out.goldBefore = await run(goldReal, '', gold, { ask: '판정 전에도 물어볼래' });

// 서버가 저장된 참값(뒤집기 전 카테고리)과 자기가 만든 요약을 실어 보내도 화면은 안 그린다
const poisonedReply = Object.assign({}, gold, { content_brief: Object.assign({}, LIVE.brief, {
  summary3: ['서버가 만든 요약', '서버 둘째 줄', '서버 셋째 줄'],
  values: Object.assign({}, LIVE.brief.values, { content_category: ['Sports / Golf'] }),
  criteria: [{ key: '서버기준', desc: '서버가 고른 기준' }],
}) });
out.poisoned = await run(goldReal, 'bad', poisonedReply);

// 자유질문: 출처 없는 문장·없는 id 인용을 서버가 흘려보내도 화면이 버린다
const badAsk = Object.assign({}, LIVE.ask, { answer: [
  { text: '출처가 있는 문장.', sources: ['s1'] },
  { text: '제가 알기로는 보통 R 입니다.', sources: [] },
  { text: '다른 팀 사례에서는 G 였습니다.', sources: ['s404'] },
  { text: '', sources: ['s1'] },
] });
out.badAsk = await run(row, 'bad', Object.assign({}, normal, { ask: badAsk }), { ask: '왜?' });

// 서버가 before 로 강등해 답하면 화면이 그 사실을 적는다
const echoReply = Object.assign({}, normal, {
  content_brief: Object.assign({}, LIVE.brief, { stage: 'before' }) });
out.echo = await run(row, 'bad', echoReply);

// /assist 404 = 조용히 접기(대화도 비우고 창도 닫는다 · 토스트 없음)
out.gone = await run(row, 'bad', Object.assign({}, normal, { __status: 404 }));
// /assist-ask 404 = 입력칸만 접는다(칩은 그대로)
out.askGone = await run(row, 'bad', Object.assign({}, normal, { __askStatus: 404 }), { ask: '왜?' });

// 자료가 하나도 없는 콘텐츠: 빈 자리를 지어내지 않고 없다고 쓴다
const bare = {
  content_brief: Object.assign({}, LIVE.brief, { evidence: null, has_evidence: false,
                                                 criteria: [], suggestions: [] }),
  verdict_precedents: { items: [], total: 0, truncated: false },
  reviewer_dissent: { lines: [], n: 0, split: false, truncated: false },
  get_examples: { items: [], total: 0, truncated: false, draft_note: '' },
  lookup_entity: { items: [], total: 0, truncated: false },
  ask: Object.assign({}, LIVE.ask, { answer: [], generated: false, model: '', model_label: '',
                                     note: '가지고 있는 자료로는 답할 수 없습니다' }),
};
out.bare = await run(row, 'bad', bare, { ask: '왜?' });

// 잘림: 목록이 잘린 것(선례)과 요소 나열이 잘린 것(의견)은 다른 문장이어야 한다
const cutReply = Object.assign({}, normal, {
  verdict_precedents: Object.assign({}, LIVE.prec, { total: 7, truncated: true }),
  reviewer_dissent: Object.assign({}, LIVE.dissent, { truncated: true }),
});
out.cut = await run(row, 'bad', cutReply);

// 열어 둔 채 콘텐츠를 옮기면: 대화는 비고 열림은 유지된다
app.detail = Object.assign({}, row, { hash: 'other999', title: '다른 콘텐츠' });
app.asxWatch();
out.moved = { open: app.asxOpen, msgs: JSON.parse(JSON.stringify(app.asxMsgs)) };

// 첫 인사: 제목만 화면 값에서 갈아 끼우며 문구를 받아 본다(서버는 안 부른다).
// 서버가 다른 제목을 실어 보내는 상황도 함께 본다 — 화면이 그걸 읽으면 골드에서 갈린다.
const LONG = '한국은행 금융통화위원회가 기준금리를 연 3.50%로 여덟 차례 연속 동결하기로 결정했다고 밝혔다';
app.asxBrief = { stage: 'after', values: { title: '서버가 준 제목' }, summary3: ['서버 요약'] };
out.hello = {};
[['normal', 'abc123', '한국은행 기준금리 동결 결정'],
 ['gold', 'gold:bad:abc123', '한국은행 기준금리 동결 결정'],
 ['goldLong', 'gold:bad:abc123', LONG],
 ['long', 'abc123', LONG],
 ['short', 'abc123', '금리 동결'],
 ['empty', 'abc123', ''],
 ['spaces', 'abc123', '   ']].forEach(([k, hash, title]) => {
  app.detail = Object.assign({}, row, { hash: hash, title: title });
  out.hello[k] = app.asxHello();
});
// 어느 자리에 서 있나: 화면 상태만 보고 갈린다(콘텐츠 값은 안 본다)
function place(setup) {
  app.mod = 'create'; app.detailOpen = true; app.createTab = 'raw';
  app.finalCtx = null; app.detail = row; app._went = [];
  setup();
  app.asxKey = ''; app.asxMsgs = []; app.asxOpen = false;
  app.asxWatch();
  app.asxToggle();                                 // 열어 본다(안내는 열었을 때 보인다)
  const before = { state: app.asxState(), avail: app.asxAvail(), text: app.asxGuideText(),
                   go: app.asxGoLabel(), msgs: app.asxMsgs.length, open: app.asxOpen };
  app.asxGo();                                     // 바로가기가 실제로 옮기는가
  return Object.assign(before, { after: { mod: app.mod, tab: app.createTab,
                                          detailOpen: app.detailOpen, went: app._went.slice(),
                                          state: app.asxState() } });
}
out.place = {
  ready: place(() => {}),
  noContent: place(() => { app.detailOpen = false; }),
  noContentButDetailKept: place(() => { app.detailOpen = false; app.detail = row; }),
  final: place(() => { app.finalCtx = { hash: row.hash }; }),   // 최종검수 맥락(게터가 본다)
  elsewhere: place(() => { app.mod = 'dict'; app.detailOpen = false; }),
  elsewhereGold: place(() => { app.mod = 'dict'; app.detailOpen = false; app.detail = goldReal; }),
};
/* 창을 열어 둔 채 화면을 옮겼다가 **다른 콘텐츠**로 돌아온다.
   ① 창은 닫히지 않는다(검수로 돌아왔을 때 다시 열게 하지 않는다)
   ② 검수 밖에서는 옛 대화가 남지 않는다(어느 콘텐츠 얘기인지 모른 채 읽는 것이 제일 나쁘다)
   ③ 돌아오면 **새 콘텐츠**로 붙는다 */
app.mod = 'create'; app.detailOpen = true; app.finalCtx = null; app.detail = row;
app._myVerdict = 'bad'; REPLY = normal;
app.asxOff = false; app.asxKey = ''; app.asxMsgs = []; app.asxOpen = false;
app.asxWatch();
app.asxToggle();
await app.asxChip(app.asxChips[0]);                // 대화를 쌓아 둔다
const tripStart = { open: app.asxOpen, msgs: app.asxMsgs.length,
                    first: JSON.parse(JSON.stringify(app.asxMsgs[0] || {})) };
app.mod = 'dict'; app.detailOpen = false;          // 다른 화면으로
app.asxWatch();
const tripAway = { open: app.asxOpen, msgs: app.asxMsgs.length, state: app.asxState(),
                   guide: app.asxGuideText(), go: app.asxGoLabel() };
app.mod = 'create'; app.detailOpen = true;         // 검수로 복귀 + 다른 콘텐츠
app.detail = Object.assign({}, row, { hash: 'zzz999', title: '두 번째 콘텐츠' });
app.asxWatch();
out.trip = { start: tripStart, away: tripAway,
             back: { open: app.asxOpen, state: app.asxState(),
                     msgs: JSON.parse(JSON.stringify(app.asxMsgs)) } };
app.mod = 'create'; app.detailOpen = true; app.detail = row;

// 안내 자리에서는 칩을 눌러도 아무 일이 없어야 한다(마크업에도 없지만 호출도 막는다)
app.mod = 'dict'; app.detailOpen = false; app.asxKey = ''; app.asxMsgs = []; calls = [];
app.asxWatch();
await app.asxChip(app.asxChips[0]);
app.asxQ = '여기서도 물어볼래'; await app.asxAsk();
out.placeInert = { calls: calls.slice(), msgs: app.asxMsgs.length };
app.mod = 'create'; app.detailOpen = true;

// 인사가 실제로 첫 말풍선으로 서는가(제목이 비어도 깨지지 않는가)
app.asxMsgs = [];
app.detail = Object.assign({}, row, { hash: 'bare000', title: '' });
app.asxKey = 'bare000';
app.asxGreet();
out.helloBare = JSON.parse(JSON.stringify(app.asxMsgs));

console.log(JSON.stringify(out));
})();
"""


class TestAssistByExecution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("node 미설치")
        live = json.dumps({"brief": _LIVE_BRIEF_AFTER, "prec": _LIVE_PREC, "dissent": _LIVE_DISSENT,
                           "dissentGold": _LIVE_DISSENT_GOLD, "examples": _LIVE_EXAMPLES,
                           "entity": _LIVE_ENTITY, "ask": _LIVE_ASK}, ensure_ascii=False)
        src = _HARNESS.replace("__JS__", json.dumps(APPJS)).replace("__LIVE__", live)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(src)
            path = f.name
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=120)
        finally:
            os.unlink(path)
        if r.returncode != 0:
            raise AssertionError("조각 실행 실패: " + r.stderr[:600])
        cls.out = json.loads(r.stdout)

    # ── 골드가 화면에서 티 나지 않는다 ──────────────────────────────────────
    def test_gold_row_still_gets_the_assistant(self):
        """대화창 유무로 골드를 가르면 검수자가 골드를 배운다."""
        for k in ("normalAfter", "goldSameAfter", "goldRealAfter"):
            self.assertTrue(self.out[k]["avail"], k)
            self.assertTrue(self.out[k]["open"], k)

    def test_chip_list_is_identical_for_gold_and_normal(self):
        """칩 문구·잠김·툴팁이 콘텐츠를 타면 그 차이가 곧 골드 신호다."""
        self.assertEqual(self.out["normalAfter"]["chips"], self.out["goldRealAfter"]["chips"])
        self.assertEqual(self.out["normalBefore"]["chips"], self.out["goldBefore"]["chips"])
        self.assertTrue(self.out["normalAfter"]["chips"])          # 빈 비교로 통과하지 않게

    def test_same_answers_render_identically_for_a_gold_copy(self):
        """같은 서버 응답 + 같은 화면 값이면 대화가 한 글자도 다르지 않아야 한다."""
        a, b = self.out["normalAfter"], self.out["goldSameAfter"]
        self.assertEqual(a["msgs"], b["msgs"])
        self.assertEqual(a["calls"], b["calls"])
        self.assertEqual(a["err"], "")
        self.assertEqual(b["err"], "")

    def test_gold_calls_the_server_exactly_like_any_content(self):
        """골드에서도 같은 도구를 같은 인자로 부른다 · 호출이 갈리는 것 자체가 신호다."""
        self.assertTrue(self.out["normalAfter"]["calls"])
        self.assertEqual(self.out["goldRealAfter"]["calls"], self.out["normalAfter"]["calls"])
        self.assertEqual(self.out["goldBefore"]["calls"], self.out["normalBefore"]["calls"])

    def test_summary_follows_the_screen_not_the_stored_truth(self):
        """3줄 요약은 화면에 그려진 값을 따라간다(골드는 뒤집힌 카테고리 그대로)."""
        a = [m for m in self.out["normalAfter"]["msgs"] if m.get("kind") == "lines"][0]
        g = [m for m in self.out["goldRealAfter"]["msgs"] if m.get("kind") == "lines"][0]
        self.assertIn("Sports / Golf", a["lines"][0])
        self.assertIn("Business and Finance / Economy", g["lines"][0])
        self.assertEqual(a["lines"][1], g["lines"][1])             # 등급·품질 사유는 참값
        self.assertEqual(a["lines"][2], g["lines"][2])             # 인텐트·개체도 참값

    def test_server_summary_and_values_never_reach_the_screen(self):
        """서버가 만든 요약·저장된 참값을 실어 보내도 화면 출력이 흔들리지 않는다(오라클 차단)."""
        blob = json.dumps(self.out["poisoned"]["msgs"], ensure_ascii=False)
        for leaked in ("서버가 만든 요약", "서버 둘째 줄", "서버기준", "서버가 고른 기준"):
            self.assertNotIn(leaked, blob, leaked)
        self.assertIn("Business and Finance / Economy", blob)      # 화면 값은 그대로 따라간다

    # ── 판정 전에는 남의 판정을 부르지도 그리지도 않는다 ────────────────────
    def test_locked_chips_do_nothing_and_call_nothing(self):
        """잠긴 칩은 눌러도 호출도 말풍선도 없다(숨기지 않는 대신 확실히 막는다)."""
        before = self.out["normalBefore"]
        locked = [c[0] for c in before["chips"] if c[1]]
        self.assertEqual(locked, ["비슷한 선례", "다른 검수자 의견", "비슷한 교정 사례"])
        for tool in ("verdict_precedents", "reviewer_dissent"):
            self.assertFalse([c for c in before["calls"] if c.startswith(tool)],
                             f"판정 전에 {tool} 를 불렀습니다")
        said = [m.get("text") for m in before["msgs"] if m.get("role") == "me"]
        for name in locked:
            self.assertNotIn(name, said, f"잠긴 칩 '{name}' 이 대화에 들어갔습니다")

    def test_suggestions_are_not_drawn_before_a_verdict(self):
        """교정 사례는 판정 뒤에만 · content_brief 를 판정 전에 부르더라도 그리지 않는다."""
        kinds = [m.get("kind") for m in self.out["normalBefore"]["msgs"]]
        self.assertNotIn("sug", kinds)
        self.assertNotIn("prec", kinds)

    def test_free_question_is_refused_before_a_verdict(self):
        """판정 전 자유질문은 아예 나가지 않는다(모델이 사실상 판정해 버린다)."""
        self.assertFalse([c for c in self.out["normalBefore"]["calls"] if c.startswith("ask|")])
        self.assertFalse([c for c in self.out["goldBefore"]["calls"] if c.startswith("ask|")])

    def test_after_only_tools_are_called_with_the_after_stage(self):
        after = self.out["normalAfter"]["calls"]
        self.assertIn("verdict_precedents|after|5||hash|me", after)
        self.assertIn("reviewer_dissent|after|||hash|me", after)
        self.assertIn("ask|after|hash|me", after)

    def test_dictionary_tools_carry_no_hash(self):
        """정책 예시·개체 사전은 콘텐츠를 서버에 알리지 않는다(공용 사전 조회)."""
        for c in self.out["normalAfter"]["calls"]:
            if c.startswith("get_examples") or c.startswith("lookup_entity"):
                self.assertIn("|-|", c, c)                         # 해시 자리가 비어 있다

    # ── 자유질문: 출처 없는 문장은 그리지 않는다 ────────────────────────────
    def test_ungrounded_lines_are_dropped_by_the_screen_too(self):
        """서버가 흘려보내도 화면이 버린다 · 대조할 수 없는 문장은 또 하나의 추측이다."""
        ans = [m for m in self.out["badAsk"]["msgs"] if m.get("kind") == "ans"][0]
        self.assertEqual([a["text"] for a in ans["lines"]], ["출처가 있는 문장."])
        for a in ans["lines"]:
            self.assertTrue(a["sources"], "출처 없는 문장이 그려집니다")
        blob = json.dumps(ans, ensure_ascii=False)
        self.assertNotIn("s404", blob)                             # 없는 출처 id 는 사라진다
        self.assertNotIn("제가 알기로는", blob)

    def test_the_answering_model_is_shown(self):
        """설정이 바뀌면 답의 성격도 바뀐다 · 누가 답했는지 화면에 남는다.

        필드에 담겼는지가 아니라 **그려지는 문장**을 본다(필드만 보면 화면이 안 그려도 통과한다)."""
        ans = [m for m in self.out["normalAfter"]["msgs"] if m.get("kind") == "ans"][0]
        self.assertEqual(ans["model"], "Solar Pro 2")
        self.assertIn("Solar Pro 2", self.out["normalAfter"]["modelLine"])
        self.assertTrue(ans["lines"])
        self.assertEqual([s["id"] for s in ans["srcs"]], ["s1", "s3", "s4"])

    def test_nothing_is_invented_when_there_is_nothing(self):
        """자료가 없으면 없다고 쓴다 · 빈 자리를 그럴듯한 문구로 메우지 않는다.

        문자열 검사만으로는 부족했다(주석이 같은 문구를 설명하고 있어 코드를 바꿔도 통과했다) —
        그래서 실제로 그린 빈 자리 문구를 본다."""
        bare = self.out["bare"]
        empties = dict(bare["empties"])
        self.assertEqual(empties["모델 판정 근거"], "저장된 근거 없음")
        self.assertEqual(empties["이 콘텐츠에 걸린 분류 기준"], "기준 없음")
        self.assertEqual(empties["비슷한 선례"], "선례 없음")
        self.assertEqual(empties["다른 검수자 의견"], "다른 의견 없음")
        self.assertIn("비슷한 교정 사례 없음", empties["비슷한 교정 사례"])
        self.assertIn("등록된 예시가 없습니다", empties["정책 예시"])
        ent = [m for m in bare["msgs"] if m.get("title") == "개체 사전"][0]
        self.assertEqual([i["desc"] for i in ent["items"]], ["사전에 없습니다"])
        # 모델이 답을 못 냈으면 서버 문구를 그대로 옮기고 지어내지 않는다
        ans = [m for m in bare["msgs"] if m.get("kind") == "ans"][0]
        self.assertEqual(ans["lines"], [])
        self.assertEqual(ans["note"], "가지고 있는 자료로는 답할 수 없습니다")
        self.assertEqual(bare["modelLine"], "")

    def test_two_kinds_of_truncation_say_different_things(self):
        """선례는 '목록이 잘렸다' · 의견 요약은 '지적 요소가 더 있다'(의견은 전부 셌다)."""
        cut = self.out["cut"]
        prec = [m for m in cut["msgs"] if m.get("kind") == "prec"][0]
        dis = [m for m in cut["msgs"] if m.get("title") == "다른 검수자 의견"][0]
        self.assertEqual(prec["cut"], "전체 7건 중 1건만 보여줍니다")
        self.assertEqual(dis["cut"], "지적한 요소가 더 있습니다 · 많이 나온 것부터 적었습니다")

    # ── 잃지 말아야 할 것들 ────────────────────────────────────────────────
    def test_dissent_is_one_digest_bubble_with_the_headcount(self):
        """3줄 집계 말풍선 하나 · 인원은 남기고 이름·사유는 그리지 않는다."""
        d = [m for m in self.out["normalAfter"]["msgs"]
             if m.get("title") == "다른 검수자 의견"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["lines"], _LIVE_DISSENT["lines"])    # 그대로
        self.assertEqual(d[0]["n"], 3)
        self.assertNotIn("reviewer", json.dumps(d[0], ensure_ascii=False))
        # 그 수가 나를 뺀 수라는 것을 문장이 말한다(서버가 본인 판정을 뺀다)
        self.assertEqual(self.out["normalAfter"]["disNote"], "내 판정은 빼고 3명 의견을 모았습니다")

    def test_who_is_asking_is_sent_outside_the_tool_arguments(self):
        """서버가 내 판정을 빼려면 누가 묻는지 알아야 한다 · 단 **인자로는 못 넣게** 한다."""
        for c in self.out["normalAfter"]["calls"]:
            self.assertTrue(c.endswith("|me"), c)
            self.assertNotIn("IN-ARGS", c, "reviewer 가 도구 인자에 실렸습니다")

    def test_gold_empty_dissent_looks_like_a_content_with_no_opinions(self):
        """골드는 서버가 빈 결과를 준다 · 그 모양이 '의견 없는 콘텐츠'와 같아야 한다."""
        g = [m for m in self.out["goldRealAfter"]["msgs"]
             if m.get("title") == "다른 검수자 의견"][0]
        self.assertEqual(g["lines"], [])
        self.assertEqual(g["n"], 0)
        self.assertEqual(g["empty"], "다른 의견 없음")
        self.assertEqual(g["kind"], "lines")                       # 말풍선 모양도 같다

    def test_stage_mismatch_is_written_on_the_bubble(self):
        """서버가 조용히 before 로 떨어뜨리면 화면이 그 사실을 적는다."""
        ev = [m for m in self.out["echo"]["msgs"] if m.get("title") == "모델 판정 근거"][0]
        self.assertIn("서버가 처리한 단계 판정 전", ev["echo"])

    def test_missing_route_closes_quietly(self):
        """/assist 404 · 조용히 접는다(대화도 비우고 오류 문구도 남기지 않는다)."""
        g = self.out["gone"]
        self.assertTrue(g["off"])
        self.assertFalse(g["open"])
        self.assertEqual(g["msgs"], [])
        self.assertEqual(g["err"], "")

    def test_missing_ask_route_only_folds_the_input(self):
        """/assist-ask 404 · 칩은 그대로 쓰고 입력칸만 접는다."""
        g = self.out["askGone"]
        self.assertTrue(g["askOff"])
        self.assertTrue(g["open"])
        self.assertFalse([m for m in g["msgs"] if m.get("kind") == "ans"])
        self.assertTrue([m for m in g["msgs"] if m.get("kind") == "lines"])

    def test_moving_to_another_content_resets_the_talk_but_stays_open(self):
        """열어 두면 따라간다 · 대화는 새 콘텐츠 것으로 갈아 끼우고 창은 그대로 둔다."""
        mv = self.out["moved"]
        self.assertTrue(mv["open"])
        self.assertEqual(len(mv["msgs"]), 1)                       # 새 콘텐츠 인사 하나
        self.assertEqual(mv["msgs"][0]["kind"], "hello")
        self.assertIn("다른 콘텐츠", mv["msgs"][0]["text"])         # 새 제목으로 다시 묻는다

    # ── 첫 화면 = 제목을 부르며 묻는 인사 ──────────────────────────────────
    def test_the_greeting_names_the_content_it_is_looking_at(self):
        """콘텐츠를 넘겨 가며 쓰는 도구라 '지금 어느 콘텐츠인지' 가 먼저 분명해야 한다."""
        hello = self.out["hello"]
        self.assertEqual(hello["normal"], "「한국은행 기준금리 동결 결정」에 대해 무엇이 궁금하세요?")
        first = self.out["normalAfter"]["msgs"][0]
        self.assertEqual(first["kind"], "hello")                   # 대화의 첫 말풍선이다
        self.assertEqual(first["role"], "bot")

    def test_the_greeting_is_identical_for_a_gold_copy(self):
        """같은 제목이면 골드 사본에서도 한 글자도 다르지 않아야 한다(길이 규칙도 같다)."""
        hello = self.out["hello"]
        self.assertEqual(hello["normal"], hello["gold"])
        self.assertEqual(hello["long"], hello["goldLong"])          # 긴 제목도 같은 규칙으로 자른다

    def test_a_long_title_is_clipped_by_one_rule(self):
        """한쪽만 자르면 그게 신호다 · 상한은 콘텐츠를 보지 않는 상수 하나뿐이다."""
        js = _read(APPJS)
        clip = _fn(js, "asxTitleClip")
        self.assertIn("ASX_TITLE_MAX", clip)
        for banned in ("gold", "hash", "detail"):
            self.assertNotIn(banned, clip, f"제목 자르기가 {banned} 를 봅니다")
        long_hi = self.out["hello"]["long"]
        self.assertTrue(long_hi.endswith("…」에 대해 무엇이 궁금하세요?"), long_hi)
        self.assertLess(len(long_hi), 70)
        self.assertEqual(self.out["hello"]["short"], "「금리 동결」에 대해 무엇이 궁금하세요?")

    def test_an_empty_title_does_not_get_an_invented_one(self):
        """빈 자리를 '(제목 없음)' 으로 채우면 그 자리가 오히려 표시가 된다(선례에서 밟은 함정).

        제목 없이 묻는 형태로 떨어지고, 인사 자체는 깨지지 않는다."""
        hello = self.out["hello"]
        self.assertEqual(hello["empty"], "이 콘텐츠에 대해 무엇이 궁금하세요?")
        self.assertEqual(hello["spaces"], hello["empty"])           # 공백뿐인 제목도 같은 자리
        for bad in ("(제목 없음)", "제목 없음", "「」", "undefined", "null"):
            self.assertNotIn(bad, hello["empty"], bad)
        bare = self.out["helloBare"]
        self.assertEqual(len(bare), 1)
        self.assertEqual(bare[0]["text"], hello["empty"])

    def test_the_greeting_never_reads_the_server_answer(self):
        """서버가 다른 제목을 실어 보내도 인사는 화면 값만 쓴다(골드에서 갈릴 자리를 안 만든다)."""
        blob = json.dumps(self.out["hello"], ensure_ascii=False)
        self.assertNotIn("서버가 준 제목", blob)
        self.assertNotIn("서버 요약", blob)
        body = _fn(_read(APPJS), "asxHello")
        self.assertIn("this.detail", body)
        self.assertNotIn("asxBrief", body)

    def test_the_greeting_asks_politely(self):
        """제품 카피가 전부 존댓말이라 여기만 반말이면 튄다."""
        for k, v in self.out["hello"].items():
            self.assertTrue(v.endswith("무엇이 궁금하세요?"), f"{k}: {v}")

    # ── 못 쓰는 자리: 왜 못 쓰는지 + 어디로 가야 하는지 ──────────────────────
    def test_each_place_says_what_to_do_there(self):
        """한 문구로 뭉뚱그리지 않는다 · 자리마다 할 일이 다르다."""
        p = self.out["place"]
        self.assertEqual(p["ready"]["state"], "ready")
        self.assertTrue(p["ready"]["avail"])
        self.assertEqual(p["noContent"]["state"], "no-content")
        self.assertIn("콘텐츠를 열면", p["noContent"]["text"])
        self.assertEqual(p["final"]["state"], "final")
        self.assertIn("최종 검수", p["final"]["text"])
        self.assertEqual(p["elsewhere"]["state"], "elsewhere")
        self.assertEqual(p["elsewhere"]["text"], "콘텐츠 검수에서만 동작합니다")

    def test_the_shortcut_is_only_offered_where_it_leads_somewhere_else(self):
        """이미 콘텐츠 검수 화면이면 바로가기를 붙이지 않는다(같은 자리로 보내는 버튼).

        최종 검수는 콘텐츠 검수 **안의 탭**이라 '콘텐츠 검수로 가기' 가 거짓이 된다 —
        그 자리에서는 옆 탭 이름을 그대로 부른다."""
        p = self.out["place"]
        self.assertEqual(p["noContent"]["go"], "")
        self.assertEqual(p["elsewhere"]["go"], "콘텐츠 검수로 가기")
        self.assertEqual(p["final"]["go"], "검수 대상 콘텐츠로 가기")

    def test_the_shortcut_actually_moves(self):
        """링크만 있고 안 가면 없는 것만 못하다 · 눌렀을 때 화면이 실제로 바뀐다."""
        p = self.out["place"]
        self.assertEqual(p["elsewhere"]["after"]["went"], ["create"])       # 메뉴를 옮긴다
        self.assertEqual(p["elsewhere"]["after"]["mod"], "create")
        self.assertEqual(p["elsewhere"]["after"]["tab"], "raw")
        self.assertEqual(p["elsewhere"]["after"]["state"], "no-content")    # 안내가 다음 단계로 바뀐다
        fin = p["final"]["after"]
        self.assertEqual(fin["tab"], "raw")                                 # 옆 탭으로 옮긴다
        self.assertFalse(fin["detailOpen"])                                 # 최종검수 상세는 닫는다
        self.assertEqual(fin["state"], "no-content")                        # 안내가 다음 단계로 바뀐다
        self.assertEqual(p["noContent"]["after"]["went"], [])               # 갈 곳이 없으면 안 움직인다

    def test_the_guidance_never_looks_at_the_content(self):
        """노출·안내가 콘텐츠 값을 타면 그게 신호가 된다 · 골드에서도 같은 문구·같은 자리다."""
        p = self.out["place"]
        self.assertEqual(p["elsewhere"]["text"], p["elsewhereGold"]["text"])
        self.assertEqual(p["elsewhere"]["go"], p["elsewhereGold"]["go"])
        self.assertEqual(p["elsewhere"]["state"], p["elsewhereGold"]["state"])
        js = _read(APPJS)
        # asxState 는 '상세가 열렸나' 를 보느라 detail.hash 유무만 확인한다(값은 안 본다).
        for name in ("asxState", "asxGuideText", "asxGoLabel"):
            body = _fn(js, name)
            for banned in ("title", "category", "grade", "reasons", "asxBrief", "isGold"):
                self.assertNotIn(banned, body, f"{name} 가 콘텐츠 값({banned})을 봅니다")
        for name in ("asxGuideText", "asxGoLabel"):
            self.assertNotIn("detail", _fn(js, name), f"{name} 가 콘텐츠를 봅니다")

    def test_nothing_is_clickable_in_the_guidance_state(self):
        """쓸 수 없는 것을 보여주면 눌러 보게 된다 · 칩도 입력칸도 아예 없고 호출도 안 된다."""
        inert = self.out["placeInert"]
        self.assertEqual(inert["calls"], [])
        self.assertEqual(inert["msgs"], 0)
        m = _shown(_read(MARKUP))                                  # 주석은 화면에 안 나온다
        for tag in ('class="asxchat__chips"', 'class="asxchat__ask"'):
            head = m[:m.index(tag)].rstrip()
            head = head[:head.rfind("<div")].rstrip()
            self.assertTrue(head.endswith('<template x-if="asxAvail()">'),
                            f"{tag} 가 대화 가능한 자리에서만 그려지지 않습니다: …{head[-60:]}")

    def test_the_window_stays_open_across_screens(self):
        """화면을 옮겨도 창은 닫지 않는다 · 검수로 돌아왔을 때 다시 열게 하지 않는다.

        사용자가 "매번 누르는 게 복잡하다" 고 한 것과 같은 문제다."""
        t = self.out["trip"]
        self.assertTrue(t["start"]["open"])
        self.assertTrue(t["away"]["open"], "화면을 옮겼다고 창을 닫았습니다")
        self.assertTrue(t["back"]["open"])

    def test_no_stale_talk_survives_leaving_the_review_screen(self):
        """검수 밖에서 옛 대화가 남으면 **어느 콘텐츠 얘기인지 모른 채** 읽게 된다(제일 나쁘다)."""
        t = self.out["trip"]
        self.assertGreater(t["start"]["msgs"], 1)                  # 실제로 쌓아 두고 시작한다
        self.assertEqual(t["away"]["msgs"], 0, "화면을 옮겼는데 옛 대화가 남아 있습니다")
        self.assertEqual(t["away"]["state"], "elsewhere")
        self.assertEqual(t["away"]["go"], "콘텐츠 검수로 가기")

    def test_coming_back_binds_to_the_new_content(self):
        """돌아오면 그 콘텐츠로 바로 붙는다 · 인사가 새 제목으로 다시 선다."""
        back = self.out["trip"]["back"]
        self.assertEqual(back["state"], "ready")
        self.assertEqual(len(back["msgs"]), 1)
        self.assertEqual(back["msgs"][0]["kind"], "hello")
        self.assertIn("두 번째 콘텐츠", back["msgs"][0]["text"])
        blob = json.dumps(back["msgs"], ensure_ascii=False)
        self.assertNotIn("한국은행", blob)                          # 옛 콘텐츠 흔적이 없다

    def test_the_button_shows_everywhere(self):
        """버튼이 검수 상세에서만 뜨면 이 기능이 있는지조차 모른다 · 노출은 화면 상태로만."""
        fab = _block(_read(MARKUP), '<button type="button" class="asxfab"', "</button>")
        self.assertIn('x-show="!asxOpen && !asxOff"', fab)
        self.assertNotIn("asxAvail()", fab)
        self.assertNotIn("detail", fab)

    def test_the_button_wears_the_chosen_character(self):
        """버튼 얼굴은 그 사람이 고른 캐릭터 · 못 찾으면 기본값으로 조용히 수렴한다."""
        self.assertEqual(self.out["normalAfter"]["fab"], "/vendor/ddakji-manager.svg")


if __name__ == "__main__":
    unittest.main()
