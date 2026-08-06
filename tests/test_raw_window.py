"""검수 대상 목록(raw_rows)의 상한 창(limit) 회귀 테스트.

증상: 콘텐츠를 추가할수록 예전 검수 완료분이 목록에서 사라지고, STEP 1 로 추가만 한
콘텐츠가 limit 을 넘으면 표가 통째로 비었다.
원인: 상한을 '제외 규칙(미실행·평가용 홀드아웃)보다 먼저' 적용해, 검수 대상이 아닌 행이
최신 창의 자리를 차지한 채 걸러졌다. → 세는 순서를 뒤집는다(제외 후 limit).
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRawRowsWindow(unittest.TestCase):
    def _with_store(self):
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve, st

    def _put(self, st, title, ts, pending=False):
        """결과 1건 적재. pending=True 면 STEP 1 추가만(모델·산출·판정 없음) 상태."""
        from prism.store import content_hash
        content = {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "본문 " + title}
        ch = content_hash(content)
        if pending:
            payload = {"quality_meta": {}, "item_meta": {}, "trace": {}, "content_ref": dict(content)}
        else:
            payload = {"quality_meta": {"review": "yellow", "finalGrade": "G", "reasons": []},
                       "item_meta": {"summary": title}, "trace": {"model": "solar", "version": 1},
                       "content_ref": dict(content)}
        c = st._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,reasons,item_meta,payload,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (ch, "뉴스", title, "G", "[]", "{}", json.dumps(payload), ts))
        c.commit()
        return ch

    def test_pending_rows_do_not_consume_window(self):
        """미실행 콘텐츠를 나중에 추가해도 기존 검수 대상은 목록에 남는다."""
        serve, st = self._with_store()
        now = time.time()
        done = [self._put(st, f"검수대상{i}", now - 100 + i) for i in range(3)]
        for i in range(5):                       # 이후 추가만 한(미실행) 콘텐츠 = 최신 5건
            self._put(st, f"추가만{i}", now + i, pending=True)
        hashes = [it["hash"] for it in serve.raw_rows(limit=3)["items"]]
        self.assertEqual(sorted(hashes), sorted(done))   # 최신 3건이 전부 미실행이어도 0건이 되지 않는다

    def test_eval_holdout_does_not_consume_window(self):
        """평가용 홀드아웃도 창의 자리를 먹지 않는다(미실행과 동일 규칙)."""
        serve, st = self._with_store()
        now = time.time()
        h_review = self._put(st, "검수용", now - 10)
        h_eval = self._put(st, "평가용", now)             # 더 최신 = 자르기 우선순위가 높았던 자리
        st.set_purpose([h_eval], "eval")
        hashes = [it["hash"] for it in serve.raw_rows(limit=1)["items"]]
        self.assertEqual(hashes, [h_review])

    def test_limit_still_caps_and_keeps_recent_first(self):
        """상한 자체는 유지 · 최근순으로 limit 건까지."""
        serve, st = self._with_store()
        now = time.time()
        for i in range(5):
            self._put(st, f"콘텐츠{i}", now + i)
        items = serve.raw_rows(limit=2)["items"]
        self.assertEqual([it["title"] for it in items], ["콘텐츠4", "콘텐츠3"])


class TestRawWideReload(unittest.TestCase):
    """목록 기본 창 = 2000(전체). 종전 '최신 200건' 창은 방금 올린 것만 보이는 착시를 만들었다
    (2026-08-06 운영: 400건 넣고 목록·배정 풀에 200건만 · rawWide 넓힘 플래그는 제거)."""

    def _src(self, rel):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            return f.read()

    def test_load_raw_default_is_wide(self):
        src = self._src("prism/vendor/app-00-tabitems.js")
        self.assertIn("String(limit || 2000)", src)
        self.assertNotIn("rawWide", src)                           # 좁은 창 분기 잔재 금지

    def test_filter_pick_no_longer_widens(self):
        src = self._src("prism/vendor/app-01-bulkpertxt.js")
        self.assertIn("rawRevPick(v)", src)
        self.assertNotIn("rawWide", src)

    def test_narrow_pool_calls_removed(self):
        """배정 후보 풀(크루 탭·배정 모달·팀 탭)이 좁은 창(1000)을 따로 쓰지 않는다."""
        for rel in ("prism/vendor/app-00-tabitems.js", "prism/vendor/app-02-_afterverdict.js",
                    "prism/ui/15-team.html", "prism/ui/19b-crew.html"):
            self.assertNotIn("loadRaw(1000)", self._src(rel), rel)

    def test_unassigned_filter_wired(self):
        """검수 필터 '미배정 콘텐츠만'(2026-08-06 요청): 상태 선언 · 체인 조건 · 마크업 3점 배선."""
        self.assertIn("rawUnassignedOnly: false", self._src("prism/vendor/app-01-bulkpertxt.js"))
        chain = self._src("prism/vendor/app-02-_afterverdict.js")
        self.assertIn("this.rawUnassignedOnly && (this.isGoldRow(r)", chain)
        from prism import page
        self.assertIn("미배정 콘텐츠만", page.PAGE)
        self.assertIn("if (rawUnassignedOnly) rawMineOnly = false", page.PAGE)   # 상호 배타

    def test_ui_wires_filter_and_hint_to_pick(self):
        from prism import page
        self.assertIn('x-on:change="rawRevPick($event.target.value)"', page.PAGE)
        self.assertIn("rawRevPick('')", page.PAGE)                 # '검수 완료 N건 숨김 · 보기' 힌트도 동일 경로


if __name__ == "__main__":
    unittest.main()
