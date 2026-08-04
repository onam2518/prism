"""본문에 섞여 들어온 HTML 마크업 제거(schema.strip_html).

배경(2026-08-03 운영 400건 실측): 티스토리·다음카페 UGC 24건의 body 에 HTML 태그·
CSS·이미지 URL 이 정제 없이 저장돼 있었다. 최악 1건은 본문 49,039자 중 라틴 33,311자 ·
한글 1,895자(= 사실상 마크업 덩어리). 리드문·엔티티 추출 품질을 떨어뜨리고 LLM 토큰만
먹는다.

이 테스트가 지키는 두 축:
  1) 마크업은 확실히 걷어낸다(script·style 은 내용까지 · 블록 경계는 개행).
  2) **평문은 절대 훼손하지 않는다** — 부등호 문장·꺾쇠 제목·HTML 설명 글.
  3) 콘텐츠 정체성(store.content_hash)은 바뀌지 않는다 — 기존 적재분 무영향.

실행: python3 -m pytest tests/ -q
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism.schema import Content, normalize_rich_text, strip_html   # noqa: E402

# 티스토리 여행기 실측 패턴: 정렬 div + 인라인 style + 이미지 + nbsp
TISTORY = (
    '<div style="text-align: center;">'
    '<img src="https://t1.daumcdn.net/cfile/tistory/9938A.jpg" width="600" height="400" />'
    '<span style="color: #333333; font-family: Nanum;">푸노의 아침</span></div>'
    '<p>티티카카 호수는&nbsp;해발 3,800m 에 있다.</p>'
)


class TestStripsMarkup(unittest.TestCase):
    def test_tistory_div_style_and_img(self):
        got = strip_html(TISTORY)
        self.assertIn("푸노의 아침", got)
        self.assertIn("티티카카 호수는", got)
        for junk in ("<div", "<img", "src=", "daumcdn", "text-align", "#333333", "&nbsp;"):
            self.assertNotIn(junk, got, f"마크업 잔재: {junk}")

    def test_style_block_content_is_removed(self):
        """태그만 벗기면 CSS 본문이 텍스트로 남는다 → 내용까지 제거해야 한다."""
        got = strip_html('<style type="text/css">.wrap{color:#fff;margin:0 auto}'
                         '\n#hd b{font-size:12px}</style><p>본문 시작</p>')
        self.assertIn("본문 시작", got)
        for junk in ("color:#fff", "font-size", "margin", ".wrap", "#hd"):
            self.assertNotIn(junk, got)

    def test_script_block_content_is_removed(self):
        got = strip_html('<script type="text/javascript">var a=1;function f(){alert("x");}'
                         '</script><p>기사 본문</p>')
        self.assertIn("기사 본문", got)
        for junk in ("var a", "function", "alert"):
            self.assertNotIn(junk, got)

    def test_unclosed_script_does_not_leak(self):
        """닫히지 않은 script 도 끝까지 제거(잘린 크롤 본문 대비)."""
        got = strip_html('<p>앞</p><script>var leak="비밀";')
        self.assertIn("앞", got)
        self.assertNotIn("leak", got)

    def test_comment_and_doctype(self):
        got = strip_html("<!doctype html><p>앞</p><!-- 광고 삽입 위치 --><p>뒤</p>")
        self.assertNotIn("광고 삽입", got)
        self.assertNotIn("doctype", got.lower())
        self.assertIn("앞", got)
        self.assertIn("뒤", got)

    def test_namespaced_word_paste_tags(self):
        got = strip_html("<o:p></o:p>워드에서 붙여넣은 문단<v:shape id='x' />")
        self.assertEqual(got.strip(), "워드에서 붙여넣은 문단")

    def test_dangling_tail_only_is_cleaned(self):
        """크롤 절단: 온전한 태그 없이 '속성 있는 잘린 꼬리 태그'만 있어도 정제한다.

        종전엔 _looks_like_html=False 라 평문 경로로 빠져 _DANGLING 이 도달 불가 코드였다
        (감사 2026-08-04) — 마크업 조각이 LLM 입력·리드문 추출에 그대로 남았다."""
        got = strip_html('수도권 폭우 현장 <div class="article_view" data-translation')
        self.assertEqual(got.strip(), "수도권 폭우 현장")
        self.assertEqual(strip_html('본문 내용 <div class="trunc').strip(), "본문 내용")

    def test_dangling_tail_after_complete_tag(self):
        """앞에 온전한 태그가 있는 경우와 없는 경우의 비일관 해소 — 같은 꼬리는 같은 결과.
        닫힌 따옴표 속성 뒤가 잘린 꼬리(`class="x" data-y`)도 제거된다."""
        got = strip_html('<p>앞문단</p> 본문 끝 <div class="article_view" data-translation')
        self.assertIn("본문 끝", got)
        self.assertNotIn("article_view", got)

    def test_entity_with_dangling_tail(self):
        """엔티티 동반 경로에서도 꼬리가 남지 않는다."""
        got = strip_html('폭우&nbsp;속보 <div class="article_view')
        self.assertIn("폭우", got)
        self.assertNotIn("article_view", got)
        self.assertNotIn("&nbsp;", got)

    def test_entities_are_unescaped(self):
        self.assertEqual(strip_html("A&nbsp;B &amp; C &lt;3 &#66;&#x43;").replace("\xa0", " "),
                         "A B & C <3 BC")


class TestBlockBoundaries(unittest.TestCase):
    def test_block_tags_become_line_breaks(self):
        """`</p><p>` 경계가 사라지면 단어가 붙는다."""
        got = strip_html("<p>가나다</p><p>라마바</p><div>사아자</div>")
        self.assertNotIn("가나다라마바", got)
        self.assertIn("가나다", got)
        self.assertIn("라마바", got)
        self.assertIn("사아자", got)

    def test_br_becomes_line_break(self):
        got = strip_html("첫줄<br>둘째줄<br/>셋째줄")
        self.assertEqual(got, "첫줄\n둘째줄\n셋째줄")

    def test_inline_tags_do_not_split_words(self):
        """인라인 태그는 공백을 넣지 않는다 — `<b>강</b>조` 는 한 단어."""
        self.assertEqual(strip_html("<b>강</b>조 및 <i>emphasis</i>text"), "강조 및 emphasistext")

    def test_table_cells_separate(self):
        got = strip_html("<table><tr><td>서울</td><td>부산</td></tr></table>")
        self.assertNotIn("서울부산", got)


class TestPlainTextIsUntouched(unittest.TestCase):
    """마크업이 아닌 본문은 한 글자도 바뀌면 안 된다."""

    UNTOUCHED = [
        "오늘 날씨가 좋아 산책을 나갔다.",
        "조건은 3 < 5 이고 10 > 7 이다.",                      # 부등호 평문
        "a<b 이고 b>c 이므로 a<c 다.",                          # 공백 없는 부등호
        "영화 <Parasite> 와 <기생충> 은 같은 작품이다.",        # 꺾쇠 제목 표기
        "HTML 에서 <p> 는 문단, <br> 은 줄바꿈을 뜻한다.",      # HTML 설명 평문(홑태그 2개)
        "수식: if (x < y) { return y > x; }",                   # 코드 예시
        "부등호 하나만: <",
        "a<b",                                                   # 이름뿐인 꼬리 = 평문 부등호
        "AT&T 와 R&D 는 그대로 둔다.",                           # 반쪽 엔티티 오작동 방지
        "",
    ]

    def test_plain_text_round_trips_exactly(self):
        for src in self.UNTOUCHED:
            self.assertEqual(strip_html(src), src, f"평문이 훼손됐다: {src!r}")

    def test_none_and_blank(self):
        self.assertEqual(strip_html(None), "")
        self.assertEqual(strip_html(""), "")

    def test_escaped_code_sample_survives(self):
        """원문이 `&lt;div&gt;` 로 이스케이프해 둔 코드 예시는 태그가 아니다 → 살아남아야."""
        got = strip_html("<pre><code>&lt;div class=&quot;x&quot;&gt;</code></pre>")
        self.assertIn('<div class="x">', got)


class TestContentIntegration(unittest.TestCase):
    def test_from_dict_cleans_body_title_subtitle(self):
        c = Content.from_dict({"displayServiceName": "티스토리",
                               "title": "<b>페루</b> - 푸노",
                               "subtitle": "<span>부제</span>",
                               "body": TISTORY})
        self.assertEqual(c.title, "페루 - 푸노")
        self.assertEqual(c.subtitle, "부제")
        self.assertNotIn("<", c.body)
        self.assertIn("티티카카 호수는 해발 3,800m", c.body)

    def test_service_name_and_url_are_not_markup_stripped(self):
        """URL 은 마크업 제거 대상이 아니다(쿼리·경로 보존)."""
        u = "https://news.v.daum.net/v/20181108111043049?a=1"
        c = Content.from_dict({"title": "t", "body": "b", "source_url": u,
                               "displayServiceName": "다음카페"})
        self.assertEqual(c.source_url, u)
        self.assertEqual(c.displayServiceName, "다음카페")

    def test_markup_shrinks_payload(self):
        """실측 성격 재현: 마크업 덩어리 본문이 실제로 크게 줄어든다."""
        blob = ('<div style="font-family: \'Nanum Gothic\'; color: #222;">'
                '<img src="https://img.example.com/a/b/c/verylongname.jpg">' * 30 +
                "본문 한 줄</div>")
        self.assertLess(len(strip_html(blob)), len(blob) / 10)

    def test_normalize_rich_text_collapses_whitespace(self):
        got = normalize_rich_text("<p>가</p>\n\n\n\n<p>나</p>")
        self.assertEqual(got, "가\n\n나")


class TestFewShotPrompt(unittest.TestCase):
    def test_example_body_is_stripped_before_truncation(self):
        """골든셋 본문을 앞 180자로 자르기 전에 정제한다 — 안 그러면 예시가 마크업뿐."""
        from prism.fewshot import FewShotPool
        pool = FewShotPool([{"content": {"displayServiceName": "티스토리", "title": "t",
                                         "body": TISTORY},
                             "expected": {"finalGrade": "G", "reasons": []}}])
        rendered = pool.render("solar-pro2")
        self.assertIn("푸노의 아침", rendered)
        self.assertNotIn("<div", rendered)
        self.assertNotIn("daumcdn", rendered)


class TestPathologicalInput(unittest.TestCase):
    """본문은 외부 입력이다 — 정규식 백트래킹 폭발이 없어야 한다.

    `<div` + 공백 2만개에서 539ms 가 걸렸던 회귀(속성부 끝의 `\\s*/?\\s*` 이중 별표)를 막는다.
    """

    def test_no_catastrophic_backtracking(self):
        import time
        cases = ["<div" + " " * 20000 + "본문",
                 '<div class="' + "a" * 20000,
                 "<" * 20000 + "본문",
                 "값이 3 < 5 이고 10 > 7 이다. " * 2000,
                 "<p>앞</p><script>" + "x" * 40000]
        for bad in cases:
            t = time.time()
            strip_html(bad)
            el = time.time() - t
            self.assertLess(el, 1.0, f"{el:.2f}s 소요 · 백트래킹 폭발 의심: {bad[:24]!r}")

    def test_real_scale_blob_shrinks(self):
        """실측 성격 재현: 마크업 덩어리 3만자대가 한 자릿수 % 로 줄어든다."""
        unit = ('<div style="text-align:center; font-family:\'Nanum Gothic\';">'
                '<img src="https://t1.daumcdn.net/cfile/tistory/99887766.jpg" '
                'width="600" style="border:0px;" />'
                '<span style="font-size:13px;">푸노의 아침&nbsp;</span></div>\n')
        blob = "<style>.a{color:#fff}</style>" + unit * 200 + "<p>끝</p>"
        got = strip_html(blob)
        self.assertGreater(len(blob), 30000)
        self.assertLess(len(got), len(blob) * 0.1)
        self.assertIn("푸노의 아침", got)
        self.assertNotIn("daumcdn", got)


class TestIdentityUnchanged(unittest.TestCase):
    """적용 지점 가드: 콘텐츠 정체성 키(store.content_hash)는 '원본' dict 로 만든다.

    strip_html 은 `Content`(= LLM 입력)만 정제하므로 기존 적재분의 해시가 바뀌지 않고,
    재실행도 같은 행을 갱신한다. 이 가드가 깨지면 기존 400건이 신규 행으로 복제된다.
    """

    RAW = {"displayServiceName": "티스토리", "title": "페루 - 푸노", "subtitle": "",
           "body": TISTORY}

    def test_content_hash_uses_raw_dict(self):
        from prism.store import content_hash
        before = "5ecb9a3f0e0b7d0f"          # 값 자체가 아니라 '원본 기준' 임을 확인
        del before
        self.assertEqual(content_hash(self.RAW),
                         content_hash(dict(self.RAW)))          # 순수 함수
        c = Content.from_dict(self.RAW)
        self.assertNotEqual(c.body, self.RAW["body"])           # 정제는 실제로 일어난다
        self.assertNotEqual(                                    # 정제본으로 키를 만들면 달라진다
            content_hash({"displayServiceName": c.displayServiceName, "title": c.title,
                          "subtitle": c.subtitle, "body": c.body}),
            content_hash(self.RAW))

    def test_saved_payload_keeps_raw_body_and_row_count(self):
        """저장 → ref 재구성 → 재저장(재실행 경로)에서 행이 늘지 않는다."""
        from prism.store import IDENTITY_FIELDS, Store, content_hash
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        out = {"content_ref": Content.from_dict(self.RAW).ref(),
               "quality_meta": {"finalGrade": "G", "reasons": []},
               "item_meta": {"summary": "요약"}, "trace": {"model": "m1"}}
        st.save_dedup([(self.RAW, out)], "run1")
        ref = st.recent()[0]["content_ref"]
        self.assertEqual(ref["body"], self.RAW["body"])         # payload 는 원본 본문 유지
        self.assertEqual(content_hash({k: ref.get(k, "") for k in IDENTITY_FIELDS}),
                         content_hash(self.RAW))
        fields = {k: ref.get(k, "") for k in IDENTITY_FIELDS}
        st.save_many([(fields, dict(out, item_meta={"summary": "재실행"}))], "run2")
        self.assertEqual(st.count(), 1, "재실행이 새 행을 만들었다 = 정체성이 깨졌다")


if __name__ == "__main__":
    unittest.main()
