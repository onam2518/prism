"""콘텐츠 관리 표: 모델 필터 + 다중 선택 재실행.

표에서 체크한 건만 /rerun-all 에 hashes 로 넘기면 서버가 scope=selected 로 승격해
그 건들만 실행한다. 전체 재실행(scope=all)과 달리 선택 실행은 개별 재실행과 같은
규약을 따른다 — 퀘스트 중에는 확인 모달을 거친 force 로만 강행한다.

실행: python3 -m pytest tests/ -q
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class SelectedRerunBase(unittest.TestCase):
    def _serve(self):
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)
        return serve

    def _put(self, st, i, model, ts):
        from prism.store import content_hash
        ref = {"displayServiceName": "s", "title": "c%04d" % i, "subtitle": "", "body": "b%04d" % i}
        h = content_hash(ref)
        payload = {"content_ref": ref, "quality_meta": {"review": "auto", "finalGrade": "G"},
                   "trace": {"model": model}, "item_meta": {"content_category": ["News"]}}
        c = st._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,payload,created_at) "
                  "VALUES(?,?,?,?,?,?)", (h, "s", ref["title"], "G", json.dumps(payload), ts))
        c.commit()
        return h

    def _spy(self, serve):
        """rerun_content 를 가로채 실제 모델 호출 없이 대상만 기록."""
        called = []
        orig = serve.rerun_content

        def fake(ch, model, team=None, row=None, force_quest=False, **kw):
            called.append((ch, force_quest))
            return {"output": {"trace": {"cost_usd": 0.0}}}
        serve.rerun_content = fake
        self.addCleanup(lambda: setattr(serve, "rerun_content", orig))
        return called


class TestSelectedTargets(SelectedRerunBase):
    def test_only_selected_hashes_run(self):
        serve = self._serve()
        now = time.time()
        hs = [self._put(serve._STORE, i, "m1", now + i) for i in range(10)]
        called = self._spy(serve)
        r = serve.rerun_all("m2", None, hashes=[hs[2], hs[7]])
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["scope"], "selected")            # hashes 지정 = scope 승격
        self.assertEqual({c[0] for c in called}, {hs[2], hs[7]})
        self.assertEqual(r["done"], 2)

    def test_selection_reaches_outside_display_window(self):
        """표는 최신 200건만 보여 주지만, 서버는 hash 로 받으므로 창과 무관하게 실행된다."""
        serve = self._serve()
        now = time.time()
        oldest = self._put(serve._STORE, 0, "m1", now - 9999)
        for i in range(1, 260):
            self._put(serve._STORE, i, "m1", now + i)
        called = self._spy(serve)
        r = serve.rerun_all("m2", None, hashes=[oldest])
        self.assertEqual([c[0] for c in called], [oldest])
        self.assertEqual(r["done"], 1)

    def test_duplicates_collapse(self):
        serve = self._serve()
        h = self._put(serve._STORE, 1, "m1", time.time())
        called = self._spy(serve)
        r = serve.rerun_all("m2", None, hashes=[h, h, h])
        self.assertEqual(len(called), 1)
        self.assertEqual(r["done"], 1)

    def test_unknown_hash_reports_no_target(self):
        serve = self._serve()
        self._put(serve._STORE, 1, "m1", time.time())
        called = self._spy(serve)
        r = serve.rerun_all("m2", None, hashes=["없는해시"])
        self.assertTrue(r.get("ok"))
        self.assertEqual(r["done"], 0)
        self.assertEqual(called, [])
        self.assertIn("선택한 콘텐츠를 찾지 못했습니다", r["msg"])

    def test_empty_selection_is_rejected(self):
        serve = self._serve()
        r = serve.rerun_all("m2", None, scope="selected", hashes=[])
        self.assertIn("선택된 콘텐츠가 없습니다", r.get("error", ""))

    def test_over_cap_is_reported_not_silent(self):
        """상한을 넘겨 고르면 조용히 자르지 않고 몇 건이 빠졌는지 응답에 남긴다."""
        import prism.runops as RN
        serve = self._serve()
        now = time.time()
        orig_cap = RN.SELECTED_MAX
        RN.SELECTED_MAX = 3
        self.addCleanup(lambda: setattr(RN, "SELECTED_MAX", orig_cap))
        hs = [self._put(serve._STORE, i, "m1", now + i) for i in range(5)]
        called = self._spy(serve)
        r = serve.rerun_all("m2", None, hashes=hs)
        self.assertEqual(len(called), 3)
        self.assertEqual(r["over_cap"], 2)

    def test_all_scope_unchanged(self):
        """기존 동작 불변: hashes 없으면 전체 재실행 그대로."""
        serve = self._serve()
        now = time.time()
        hs = [self._put(serve._STORE, i, "m1", now + i) for i in range(4)]
        called = self._spy(serve)
        r = serve.rerun_all("m2", None, scope="all")
        self.assertEqual({c[0] for c in called}, set(hs))
        self.assertEqual(r["scope"], "all")


class TestSelectedQuestGate(SelectedRerunBase):
    def _quest(self, serve, on):
        orig = serve.quest_active
        serve.quest_active = lambda: on
        self.addCleanup(lambda: setattr(serve, "quest_active", orig))

    def test_blocked_during_quest_without_force(self):
        serve = self._serve()
        h = self._put(serve._STORE, 1, "m1", time.time())
        self._quest(serve, True)
        called = self._spy(serve)
        r = serve.rerun_all("m2", None, hashes=[h])
        self.assertIn("퀘스트", r.get("error", ""))         # 클라가 이 문구로 확인 모달을 띄운다
        self.assertEqual(called, [])

    def test_force_passes_through_to_each_item(self):
        serve = self._serve()
        h = self._put(serve._STORE, 1, "m1", time.time())
        self._quest(serve, True)
        called = self._spy(serve)
        r = serve.rerun_all("m2", None, hashes=[h], force_quest=True)
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(called, [(h, True)])               # 건별 게이트까지 force 전달

    def test_no_force_needed_without_quest(self):
        serve = self._serve()
        h = self._put(serve._STORE, 1, "m1", time.time())
        self._quest(serve, False)
        called = self._spy(serve)
        self.assertTrue(serve.rerun_all("m2", None, hashes=[h]).get("ok"))
        self.assertEqual(called, [(h, False)])


class TestRowKeyContract(SelectedRerunBase):
    """표가 보여 주는 해시(저장 키)로 재실행 대상을 찾을 수 있어야 한다.

    sqlite recent 가 payload 안의 12자 본문 해시를 그대로 내리던 때는 _row_key 가 매번
    재계산했는데, 실행 결과의 content_ref 는 정규화된 본문이라(끝 공백 제거 등) 원본으로
    만든 저장 키와 갈렸다 → 화면 해시로 고른 건을 서버가 못 찾았다(2026-07-29).
    """

    def test_recent_carries_store_key_in_body_hash(self):
        serve = self._serve()
        st = serve._STORE
        h = self._put(st, 1, "m1", time.time())
        ref = serve.results_rows(team=None)[0]["content_ref"]
        self.assertEqual(ref["body_hash"], h)            # supastore.recent 와 같은 계약(16자 저장 키)
        self.assertEqual(serve._row_key(ref), h)

    def test_normalized_ref_still_resolves_to_store_key(self):
        """정규화가 본문을 바꾸는 콘텐츠(끝 공백)에서도 화면 해시 == 재실행 대상 키."""
        from prism.schema import Content
        from prism.store import content_hash
        serve = self._serve()
        raw = {"displayServiceName": "s", "title": "끝공백", "subtitle": "", "body": "본문 " * 5}
        key = content_hash(raw)
        payload = {"content_ref": Content.from_dict(raw).ref(),   # 정규화본이 실린 실행 결과
                   "quality_meta": {"review": "auto", "finalGrade": "G"},
                   "trace": {"model": "m1"}, "item_meta": {}}
        c = serve._STORE._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,payload,created_at) "
                  "VALUES(?,?,?,?,?,?)", (key, "s", "끝공백", "G", json.dumps(payload), time.time()))
        c.commit()
        # 정규화본으로 재계산하면 다른 키가 나온다 — 그래서 저장 키를 실어 내려야 한다
        self.assertNotEqual(content_hash({k: payload["content_ref"].get(k, "") for k in
                                          ("displayServiceName", "title", "subtitle", "body")}), key)
        called = self._spy(serve)
        r = serve.rerun_all("m2", None, hashes=[key])
        self.assertEqual([x[0] for x in called], [key])
        self.assertEqual(r["done"], 1)


class TestUiWiring(unittest.TestCase):
    def _src(self, rel):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            return f.read()

    def test_model_filter_state_and_getters(self):
        src = self._src("prism/vendor/app-08-copytext.js")
        for needle in ("contentModel: ''", "get contentModels()", "PENDING_MODEL"):
            self.assertIn(needle, src)

    def test_selection_helpers(self):
        src = self._src("prism/vendor/app-08-copytext.js")
        for needle in ("pickSel: {}", "get pickedHashes()", "get pickAllOn()",
                       "togglePickAll()", "async rerunPicked(force)"):
            self.assertIn(needle, src)

    def test_filter_change_clears_selection(self):
        """필터를 바꾸면 선택을 비운다 — 안 보이는 건이 딸려 실행되는 사고 방지."""
        src = self._src("prism/vendor/app-08-copytext.js")
        self.assertIn("pickModel(v) { this.contentModel = v; this.clearPick(); }", src)

    def test_quest_retry_path_present(self):
        src = self._src("prism/vendor/app-08-copytext.js")
        self.assertIn("rerunPicked(true)", src)              # 확인 후 강행
        self.assertIn("/퀘스트/.test", src)

    def test_markup_has_filter_and_bulk_button(self):
        from prism import page
        self.assertIn('x-on:change="pickModel($event.target.value)"', page.PAGE)
        self.assertIn('x-on:click="rerunPicked()"', page.PAGE)
        self.assertIn('x-on:change="togglePickAll()"', page.PAGE)
        self.assertIn('x-on:change="togglePick(c.hash)"', page.PAGE)


if __name__ == "__main__":
    unittest.main()
