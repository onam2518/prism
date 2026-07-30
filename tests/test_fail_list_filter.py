"""메타 생성 실패 콘텐츠 표: 실패 종류·모델 필터 + 다중 선택 재실행.

'추가된 콘텐츠 · 용도' 표와 같은 규약(필터 변경 시 선택 비움 · /rerun-all 에 hashes ·
퀘스트 중에는 확인 모달을 거친 force). 실패가 한 종류로 몰릴 때 원인별로 갈라 보고
한 번에 다시 돌리기 위한 것이다(2026-07-29 운영: 31건 전부 http_402 잔액 부족).

실행: python3 -m pytest tests/ -q
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FailListSrc(unittest.TestCase):
    def _src(self, rel="prism/vendor/app-05-costdata.js"):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            return f.read()


class TestFilterState(FailListSrc):
    def test_filter_state_and_option_sources(self):
        src = self._src()
        for needle in ("failKind: '', failModel: ''", "get failKindOpts()",
                       "get failModelOpts()", "get failRows()"):
            self.assertIn(needle, src)

    def test_both_filters_applied(self):
        src = self._src()
        self.assertIn("if (this.failKind && !(f.kinds || []).includes(this.failKind)) return false;", src)
        self.assertIn("if (this.failModel && (f.model || '') !== this.failModel) return false;", src)

    def test_filter_change_clears_selection(self):
        """선택을 남긴 채 필터를 바꾸면 안 보이는 건이 딸려 실행된다 — 비운다."""
        src = self._src()
        self.assertIn("pickFailKind(v) { this.failKind = v; this.clearFailPick(); }", src)
        self.assertIn("pickFailModel(v) { this.failModel = v; this.clearFailPick(); }", src)


class TestBulkRerun(FailListSrc):
    def test_selection_helpers(self):
        src = self._src()
        for needle in ("failSel: {}, failBusy: false", "get failPicked()", "get failAllOn()",
                       "toggleFailPick(h)", "toggleFailPickAll()", "clearFailPick()"):
            self.assertIn(needle, src)

    def test_select_all_covers_visible_rows_only(self):
        src = self._src()
        self.assertIn("this.failRows.forEach((f) => { next[f.hash] = on; });", src)

    def test_posts_hashes_to_rerun_all(self):
        src = self._src()
        self.assertIn("async rerunFailPicked(force)", src)
        self.assertIn("hashes: hs", src)

    def test_refreshes_fail_list_after_run(self):
        """성공분은 서버가 목록에서 지운다 — 화면도 다시 읽어야 반영된다."""
        src = self._src()
        self.assertIn("this.clearFailPick(); this.loadDash();", src)
        self.assertIn("await this.loadFails();", src)      # 해소 건수를 세려면 갱신을 기다린다

    def test_quest_retry_path(self):
        src = self._src()
        self.assertIn("rerunFailPicked(true)", src)
        self.assertIn("/퀘스트/.test", src)

    def test_single_rerun_button_kept(self):
        """기존 건별 재실행은 그대로 둔다(한 건만 돌릴 때 더 빠름)."""
        self.assertIn("async rerunFail(f)", self._src())


class TestMarkup(unittest.TestCase):
    def test_filterbar_and_checkboxes_wired(self):
        from prism import page
        for needle in ('x-on:change="pickFailKind($event.target.value)"',
                       'x-on:change="pickFailModel($event.target.value)"',
                       'x-on:click="rerunFailPicked()"',
                       'x-on:change="toggleFailPickAll()"',
                       'x-on:change="toggleFailPick(f.hash)"'):
            self.assertIn(needle, page.PAGE)

    def test_rows_come_from_filtered_getter(self):
        from prism import page
        self.assertIn('x-for="f in failRows"', page.PAGE)          # 원본이 아니라 필터 통과분

    def test_kind_options_use_korean_labels(self):
        from prism import page
        self.assertIn('x-text="failKindKr(k)"', page.PAGE)

    def test_empty_message_distinguishes_filtered_out(self):
        """필터 때문에 비었는지, 실패가 아예 없는지 구분해서 알린다."""
        from prism import page
        self.assertIn("이 조건에 맞는 실패 콘텐츠가 없습니다", page.PAGE)


class TestBillingKind(unittest.TestCase):
    """402 = 잔액 소진. 충전 전에는 재실행해도 계속 실패하므로 목록에서 사라지지 않는다 —
    '눌러도 왜 안 사라지냐'는 오해를 화면에서 먼저 끊는다(2026-07-29 실사례)."""

    def test_402_classified_as_billing(self):
        from prism.ratelimit import classify_http_error
        self.assertEqual(classify_http_error(402, '{"code":"insufficient_balance"}'), "billing")

    def test_other_codes_unchanged(self):
        from prism.ratelimit import classify_http_error
        self.assertEqual(classify_http_error(401, ""), "auth")
        self.assertEqual(classify_http_error(429, ""), "http_429")

    def test_korean_labels_cover_billing_and_legacy_key(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "prism/vendor/app-05-costdata.js"), encoding="utf-8") as f:
            src = f.read()
        # 2026-07-30: 402 를 billing(크레딧)·quota(프로젝트 한도)로 분리 — 조치가 다르다
        self.assertIn("billing: '크레딧 부족'", src)
        self.assertIn("quota: '프로젝트 지출 한도'", src)
        self.assertIn("http_402:", src)                  # 이전 적재분의 옛 키도 읽힌다
        self.assertIn("get failBillingN()", src)

    def test_banner_present(self):
        from prism import page
        self.assertIn("충전 필요", page.PAGE)
        self.assertIn('x-show="failBillingN"', page.PAGE)


class TestQueueRefreshAndHonestToast(unittest.TestCase):
    """재실행 후 화면이 서버 상태를 따라가야 한다.

    - 실행 큐: 잡은 서버에 등록되는데 화면이 다시 읽지 않아 '큐에 안 올라간다'로 보였다
    - 토스트: '재실행 완료' 만 띄우면 목록이 그대로일 때 왜 안 줄었는지 알 수 없다
      → 실제 해소 건수를 세고, 0건이면서 잔액 부족이면 그 사실을 덧붙인다(2026-07-29)
    """

    def _src(self, rel):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            return f.read()

    def test_all_rerun_paths_refresh_queue(self):
        c8 = self._src("prism/vendor/app-08-copytext.js")
        c5 = self._src("prism/vendor/app-05-costdata.js")
        # 세 갈래 모두: 개별 재실행 · 콘텐츠 표 선택 재실행 · 실패 목록 선택 재실행
        self.assertIn("this.clearPick(); this.loadDash(); this.loadRaw && this.loadRaw(); this.fetchIngestStatus();", c8)
        self.assertIn("· 재실행 완료'); this.loadDash(); this.loadRaw && this.loadRaw(); this.fetchIngestStatus();", c8)
        self.assertIn("this.clearFailPick(); this.loadDash(); this.fetchIngestStatus();", c5)

    def test_toast_counts_cleared_not_just_executed(self):
        src = self._src("prism/vendor/app-05-costdata.js")
        self.assertIn("const before = this.failAll.length;", src)
        self.assertIn("await this.loadFails();", src)
        self.assertIn("const cleared = Math.max(0, before - this.failAll.length);", src)
        self.assertIn("'재실행 ' + (r.done || 0) + '건 · 해소 ' + cleared + '건'", src)

    def test_toast_explains_zero_clear_on_billing(self):
        src = self._src("prism/vendor/app-05-costdata.js")
        self.assertIn("cleared === 0 && this.failBillingN", src)
        self.assertIn("충전 전에는 해소되지 않습니다", src)


if __name__ == "__main__":
    unittest.main()
