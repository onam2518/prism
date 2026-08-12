"""프런트엔드 감사 수정 잠금(2026-08-11 · F1·F2·F4·F5·F6).

브라우저 없이 되돌아감을 잡기 위해 조각 소스와 CSS 를 직접 읽어 계약을 검사한다.
어느 항목이든 규약을 되돌리면 화면은 멀쩡히 뜨는데 조용히 나빠지는 자리라
(레이아웃 붕괴 · 빈 화면 무안내 · 메시지 뒤섞임) 테스트로 못을 박아 둔다.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "prism", "ui")
VENDOR_DIR = os.path.join(ROOT, "prism", "vendor")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _ui(name):
    return _read(os.path.join(UI_DIR, name))


def _js(name):
    return _read(os.path.join(VENDOR_DIR, name))


def _brace_block(src, at):
    """`at` 이후 첫 `{` 부터 짝이 맞는 `}` 까지."""
    start = src.index("{", at)
    depth = 0
    for j in range(start, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError("본문 끝을 찾지 못했다")


def _fn_body(src, header):
    """`header` 로 **선언된**(줄 앞) 함수의 본문 · 같은 이름의 호출부에 걸리지 않는다."""
    m = re.search(r"\n\s*" + re.escape(header), src)
    if not m:
        raise AssertionError("선언을 찾지 못했다: %s" % header)
    return _brace_block(src, m.start())


def _catch_body(src, header):
    """함수 본문 안 첫 catch 블록의 알맹이."""
    body = _fn_body(src, header)
    m = re.search(r"catch\s*\([^)]*\)\s*", body)
    if not m:
        raise AssertionError("catch 가 없다: %s" % header)
    return _brace_block(body, m.end() - 1)


class TestFlexDisplayContract(unittest.TestCase):
    """[F1] x-show 와 같은 요소의 인라인 display 는 Alpine 이 첫 렌더에 지운다.

    지워지고 나면 gap·flex-wrap·align-items·flex-direction 이 통째로 무효가 되므로
    display 는 반드시 클래스가 받아야 한다(ARCHITECTURE 'UI 구조' 주의 ③).
    """

    @staticmethod
    def _classes_with_display():
        """app.css 가 display 를 선언하는 단일 클래스 셀렉터 이름 집합."""
        css = re.sub(r"/\*.*?\*/", "", _read(os.path.join(VENDOR_DIR, "app.css")), flags=re.S)
        out = set()
        for sel, block in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            if "display:" not in block:
                continue
            for one in sel.split(","):
                m = re.fullmatch(r"\s*\.([A-Za-z0-9_-]+)\s*", one)
                if m:
                    out.add(m.group(1))
        return out

    def test_no_inline_display_on_x_show_without_class_fallback(self):
        backed = self._classes_with_display()
        self.assertIn("flexrow", backed)
        offenders = []
        for name in sorted(os.listdir(UI_DIR)):
            if not name.endswith(".html"):
                continue
            src = _ui(name)
            for tag in re.finditer(r"<[a-zA-Z][^>]*>", src):
                t = tag.group(0)
                if "x-show" not in t:
                    continue
                style = re.search(r'style="([^"]*)"', t)
                if not style or "display:" not in style.group(1):
                    continue
                cls = re.search(r'class="([^"]*)"', t)
                names = set((cls.group(1) if cls else "").split())
                if not (names & backed):
                    line = src[:tag.start()].count("\n") + 1
                    offenders.append("%s:%d" % (name, line))
        self.assertEqual(offenders, [], "x-show 요소의 인라인 display 는 .flexrow/.flexcol 로 옮겨라: %s" % offenders)

    def test_flexcol_exists_for_column_groups(self):
        """세로 그룹(19b-crew 그룹 배정)은 flex-direction 까지 클래스가 책임진다."""
        css = _read(os.path.join(VENDOR_DIR, "app.css"))
        self.assertRegex(css, r"\.flexcol\{display:flex;flex-direction:column\}")
        self.assertIn('class="flexcol"', _ui("19b-crew.html"))


class TestIngestMessageSlots(unittest.TestCase):
    """[F4] 같은 화면에 동시에 뜨는 두 패널이 메시지 프로퍼티를 공유하면 안 된다."""

    def test_fragments_own_distinct_names(self):
        # 주석 언급은 세지 않는다 · `this.<name>` 실제 사용만 본다
        five = re.findall(r"this\.(ingest(?:Once)?Msg)\b", _js("app-05-costdata.js"))
        eight = re.findall(r"this\.(ingest(?:Once)?Msg)\b", _js("app-08-copytext.js"))
        self.assertTrue(five and set(five) == {"ingestOnceMsg"},   # 일회성 가져오기(ingestOnceBusy 와 접두 일치)
                        "app-05 는 ingestOnceMsg 만 써야 한다: %s" % sorted(set(five)))
        self.assertTrue(eight and set(eight) == {"ingestMsg"},     # 인입 소스 저장
                        "app-08 은 ingestMsg 만 써야 한다: %s" % sorted(set(eight)))

    def test_two_panels_bind_different_slots(self):
        src = _ui("02-content-manage.html")
        slots = re.findall(r'x-text="(ingest(?:Once)?Msg)"', src)
        self.assertEqual(sorted(slots), ["ingestMsg", "ingestOnceMsg"],
                         "일회성 가져오기 · 인입 소스 추가 패널은 서로 다른 메시지 슬롯을 써야 한다")


class TestLoaderFailuresAreVisible(unittest.TestCase):
    """[F5] 1차 화면 로더가 fetch 실패를 삼키면 빈 화면 + 무안내로 남는다."""

    LOADERS = [
        ("app-00-tabitems.js", "async loadRaw("),
        ("app-00-tabitems.js", "async loadAssignLog("),
        ("app-02-_afterverdict.js", "async loadDash("),
        ("app-04-_err.js", "async loadArena("),
        ("app-05-costdata.js", "async loadRoutesRaw("),
        ("app-05-costdata.js", "async loadGoldenStatus("),
        ("app-05-costdata.js", "async loadGoldenList("),
        ("app-05-costdata.js", "async loadLearnReport("),
        ("app-06-weeklyleague.js", "async loadTopics("),
        ("app-07-eventhint.js", "async loadDict("),
        ("app-07-eventhint.js", "async loadMem("),
        ("app-07-eventhint.js", "async loadDemoLab("),
        ("app-07-eventhint.js", "async loadEntdict("),
    ]

    def test_primary_loaders_report_failure(self):
        for fname, header in self.LOADERS:
            with self.subTest(loader=header):
                self.assertIn("_err(", _catch_body(_js(fname), header),
                              "%s %s 의 catch 가 실패를 삼킨다" % (fname, header))

    def test_pilot_poll_survives_a_dropped_request(self):
        """폴링 체인은 catch 안에서 재예약해야 순단 후 자력 복구된다."""
        catch = _catch_body(_js("app-04-_err.js"), "async loadPilot(")
        self.assertIn("_pilotPollT", catch)
        self.assertIn("setTimeout", catch)

    def test_entdict_poll_reschedules_outside_the_catch(self):
        """loadEntdict 는 재예약 코드가 catch 밖(함수 말미)이라 실패해도 폴링이 이어진다."""
        body = _fn_body(_js("app-07-eventhint.js"), "async loadEntdict(")
        self.assertLess(body.index("catch"), body.index("_entPollT = setTimeout"))


class TestSelectModAvoidsRefetch(unittest.TestCase):
    """[F2] 메뉴 전환마다 같은 데이터를 무조건 재조회하지 않는다."""

    def setUp(self):
        self.body = _fn_body(_js("app-02-_afterverdict.js"), "selectMod(id)")

    def test_dict_is_loaded_once_per_session(self):
        """/dict 는 36KB 이고 세션 중 사실상 불변 · 편집·초기화는 응답으로 dictData 를 직접 갱신한다."""
        self.assertNotIn("this.loadDict();", self.body.replace("if (!this.dictData) this.loadDict();", ""))
        self.assertEqual(self.body.count("if (!this.dictData) this.loadDict();"), 2)  # dict · content 메뉴

    def test_shared_loaders_use_the_throttled_variants(self):
        for bare in ("this.loadDash()", "this.loadArena()", "this.loadGoldenStatus()"):
            with self.subTest(loader=bare):
                self.assertNotIn(bare + ";", self.body)
        self.assertIn("this.loadDashThrottled()", self.body)
        self.assertIn("this.loadArenaThrottled()", self.body)
        self.assertIn("this.loadGoldenStatusThrottled()", self.body)

    def test_crew_keeps_the_unthrottled_reload(self):
        """검수운영 후보 풀은 배정 직후 최신화가 목적이라 스로틀에서 뺀다(의도된 예외)."""
        crew = [ln for ln in self.body.splitlines() if "this.loadCrew();" in ln]
        self.assertEqual(len(crew), 1)
        self.assertIn("this.loadRaw();", crew[0])

    def test_throttle_wrappers_share_the_same_window(self):
        eight = _js("app-08-copytext.js")
        for name in ("loadDashThrottled", "loadRawThrottled", "loadArenaThrottled", "loadGoldenStatusThrottled"):
            with self.subTest(wrapper=name):
                self.assertIn(name + "()", eight)
                self.assertIn("4000", _fn_body(eight, name + "()"))


class TestGoldenListDisplayCap(unittest.TestCase):
    """[F6] 정답셋 목록도 검수 표와 같은 표시 캡 200 + 더 보기 규약을 따른다."""

    def test_cap_matches_the_review_table(self):
        four, two = _js("app-04-_err.js"), _js("app-02-_afterverdict.js")
        self.assertIn("goldenShown: 200,", four)
        self.assertIn("get goldenShownList()", four)
        self.assertIn("this.filteredGolden.slice(0, this.goldenShown)", four)
        self.assertIn("rawShown: 200,", two)   # 규약 원천(P2-2)이 살아 있어야 같은 캡이라 말할 수 있다

    def test_markup_renders_the_capped_list_with_a_more_button(self):
        src = _ui("14-golden.html")
        self.assertIn('x-for="g in goldenShownList"', src)
        self.assertNotIn('x-for="g in filteredGolden"', src)
        self.assertIn('x-on:click="goldenShown += 200"', src)


if __name__ == "__main__":
    unittest.main()
