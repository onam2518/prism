"""토픽 상태(활성 · 일시정지 · 초안 · 보관) · 변경 기록 · 신호 · 출처 축 · 원천(feed) 조건 (스펙 132112)."""
import os
import tempfile
import time
import unittest


def _row(title, grade="G", entities=(), intent=(), cats=(), svc="뉴스", src=None, imgs=None, ts=None):
    r = {"content_ref": {"title": title, "subtitle": "", "body": "본문-" + title, "displayServiceName": svc},
         "item_meta": {"summary": "리드-" + title, "entities": list(entities),
                       "intent": list(intent), "content_category": list(cats)},
         "quality_meta": {"finalGrade": grade, "review": "", "reasons": []}}
    if src is not None:
        r["src"] = src
    if imgs is not None:
        r["content_ref"]["image_urls"] = imgs
    if ts is not None:
        r["_ts"] = ts
    return r


class TestFeedAndSource(unittest.TestCase):
    """topic.py 순수 함수: 문장 → feed 해석 · 필드 없음 집계 · 형식/기간/표시 · 출처 축 · 신호."""

    def test_parse_feed_text(self):
        from prism.topic import parse_feed_text
        fd = parse_feed_text("스포츠 리뷰. 최근 2주 안에 올라온 것만. 쇼츠는 빼고, 사진 있는 것만. 단독 위주로. 광고 포함해도 돼")
        self.assertEqual(fd["days"], 14)
        self.assertEqual(fd["neg_types"], ["VIDEO/SHORTS"])
        self.assertEqual(fd["image"], "yes")
        self.assertEqual(fd["flags"], ["isExclusive"])
        self.assertFalse(fd["base_excl"])
        self.assertEqual(parse_feed_text("그냥 스포츠"), {})              # 조건 없음 → 빈 dict(기본 제외만)

    def test_unknown_field_blocks_and_counts_miss(self):
        from prism.topic import _content_dims, _feed_blocked
        rows = [_row("모름"), _row("있음", imgs=["u1"]), _row("없음", src={"image_cnt": 0})]
        blocked, miss = _feed_blocked(_content_dims(rows, set()), {"image": "yes"})
        self.assertEqual(blocked, {0, 2})
        self.assertEqual(dict(miss), {"image": 1})                        # 필드 없는 행만 '필드 없음'

    def test_types_days_flags_base_exclusion(self):
        from prism.topic import _content_dims, _feed_blocked
        now = time.time()
        rows = [_row("쇼츠", src={"type": "VIDEO", "subtype": "SHORTS"}),
                _row("글", src={"type": "TEXT", "isExclusive": True, "org_ts": now - 86400}),
                _row("옛글", src={"type": "TEXT", "isExclusive": False, "org_ts": now - 30 * 86400}),
                _row("광고", src={"type": "TEXT", "ads": True})]
        dims = _content_dims(rows, set())
        b, _ = _feed_blocked(dims, {"neg_types": ["VIDEO/SHORTS"]})
        self.assertIn(0, b); self.assertNotIn(1, b)
        b, miss = _feed_blocked(dims, {"flags": ["isExclusive"]})
        self.assertEqual(b, {0, 2, 3})                                    # 0: 플래그 모름 · 2: false · 3: 광고 기본 제외
        self.assertEqual(miss["isExclusive"], 1)
        b, _ = _feed_blocked(dims, {"days": 7})
        self.assertIn(2, b); self.assertNotIn(1, b)
        b, _ = _feed_blocked(dims, {})
        self.assertEqual(b, {3})                                          # 조건 없어도 광고는 기본 제외
        b, _ = _feed_blocked(dims, {"base_excl": False})
        self.assertEqual(b, set())

    def test_source_axis_match_and_exclude(self):
        from prism.topic import preview_definition
        rows = [_row("뉴스 글", cats=["Sports"]), _row("티비 클립", cats=["Sports"], svc="카카오TV")]
        pv = preview_definition(rows, set(), {"cats": ["Sports"], "srcs": ["카카오TV"],
                                              "req": {"cats": ["Sports"], "srcs": ["카카오TV"]}})
        core = next(b for b in pv["bundles"] if b["kind"] == "core")
        self.assertEqual(core["content_ids"], [1])
        pv = preview_definition(rows, set(), {"cats": ["Sports"], "req": {"cats": ["Sports"]},
                                              "neg": {"srcs": ["카카오TV"]}})
        core = next(b for b in pv["bundles"] if b["kind"] == "core")
        self.assertEqual(core["content_ids"], [0])
        self.assertEqual(pv["neg_blocked"], 1)

    def test_row_stats_signal(self):
        from prism.topicops import _row_stats
        now = time.time()
        rows = [_row("a", ts=now - 4 * 86400), _row("b", ts=now - 5 * 86400)]
        st = _row_stats([0, 1], rows, now)
        self.assertEqual((st["today"], st["d7"], st["signal"]), (0, 2, "정체 4일"))
        rows = [_row("p%d" % i, ts=now - 10 * 86400) for i in range(25)] + [_row("n", ts=now - 3600)]
        st = _row_stats(list(range(26)), rows, now)
        self.assertEqual((st["today"], st["d7"], st["prev7"], st["signal"]), (1, 1, 25, "급감"))

    def test_sanitize_status_and_feed(self):
        from prism.serve import _sanitize_def
        d = _sanitize_def({"name": "x", "status": "weird", "srcs": ["뉴스"], "neg": {"srcs": ["뉴스", "카페"]},
                           "feed": {"days": "3", "types": ["shorts", "video/shorts"], "flags": ["isexclusive"]}})
        self.assertEqual(d["status"], "active")
        self.assertEqual(d["neg"]["srcs"], ["카페"])                      # 선택과 겹치는 제외는 버림
        self.assertEqual((d["feed"]["days"], d["feed"]["types"], d["feed"]["flags"]), (3, ["VIDEO/SHORTS"], ["isExclusive"]))
        self.assertEqual(_sanitize_def({"name": "y"})["feed"], {})


class TestStatusActions(unittest.TestCase):
    """서버 액션: 저장 상태 · 0건 활성 잠금 · 스위치(일시정지는 건수 유지 · 보관은 매칭 없음) · 변경 기록 · 자동 토픽 일시정지 기억."""

    def setUp(self):
        import prism.serve as S
        from prism.store import Store
        self.S = S
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_store, self._orig_last = S._STORE, list(S._LAST_RESULTS)
        self._orig_mock = S.Handler.server_mock
        S.Handler.server_mock = True
        S._STORE = Store(os.path.join(self._tmp.name, "t.db"))
        rows = [_row("삼성 분석1", entities=["삼성전자", "이재용"], intent=["분석·해설"], cats=["Business and Finance"]),
                _row("삼성 분석2", entities=["삼성전자", "이재용"], intent=["기획·심층"], cats=["Business and Finance"]),
                _row("티비 클립", entities=["넷플릭스"], intent=["흥미·화제"], cats=["Entertainment"], svc="카카오TV")]
        pairs = [({k: (r["content_ref"].get(k) or "") for k in ("displayServiceName", "title", "subtitle", "body")}, r)
                 for r in rows]
        S.store_save(pairs, source="test")

    def tearDown(self):
        self.S._STORE = self._orig_store
        self.S._LAST_RESULTS[:] = self._orig_last
        self.S.Handler.server_mock = self._orig_mock
        self._tmp.cleanup()

    def _act(self, **data):
        return self.S.topic_studio_action(data, mock=True, who="pete")

    def _custom(self, r, cid):
        return next(g for g in r["custom"] if g["id"] == cid)

    def test_save_toggle_archive_and_log(self):
        d = {"name": "삼성", "keywords": ["삼성전자"], "req": {"keywords": ["삼성전자"]}, "status": "active"}
        r = self._act(action="save", **{"def": d, "talk": True})
        self.assertEqual(r["saved"]["status"], "active"); self.assertFalse(r["saved"]["locked"])
        cid = r["saved"]["id"]
        g = self._custom(r, cid)
        self.assertEqual(g["status"], "active"); self.assertGreaterEqual(g["core_count"], 2)
        self.assertEqual(g["log"][-1]["who"], "pete"); self.assertTrue(g["log"][-1]["what"].startswith("만듦"))
        self.assertEqual(g["via"], "talk")                                  # 만든 방식: 말로
        n = g["core_count"]
        r = self._act(action="status", id=cid, status="paused")
        g = self._custom(r, cid)
        self.assertEqual((g["status"], g["core_count"]), ("paused", n))     # 일시정지: 건수는 계속 센다
        self.assertIn("상태 active → paused", g["log"][-1]["what"])
        r = self._act(action="status", id=cid, status="archived")
        g = self._custom(r, cid)
        self.assertEqual((g["status"], g["core_count"], g["bundles"]), ("archived", 0, []))   # 보관: 매칭 없음 · 정의만
        r = self._act(action="status", id=cid, status="active")
        self.assertEqual(self._custom(r, cid)["core_count"], n)
        self.assertEqual(self._act(action="status", id=cid, status="nope")["ok"], False)
        self.assertIn("today", g); self.assertIn("d7", g); self.assertIn("signal", g)

    def test_zero_match_locks_active_to_draft(self):
        d = {"name": "빈 토픽", "keywords": ["없는엔티티"], "req": {"keywords": ["없는엔티티"]}, "status": "active"}
        r = self._act(action="save", **{"def": d})
        self.assertEqual((r["saved"]["status"], r["saved"]["locked"]), ("draft", True))
        cid = r["saved"]["id"]
        self.assertEqual(self._custom(r, cid)["status"], "draft")
        r = self._act(action="status", id=cid, status="active")
        self.assertEqual((r["saved"]["status"], r["saved"]["locked"]), ("draft", True))
        self.assertIn("0건이라 잠금", self._custom(r, cid)["log"][-1]["what"])

    def test_auto_topic_pause_is_remembered(self):
        td = self.S.topics_data()
        auto = (td.get("single") or []) + (td.get("composite") or [])
        if not auto:
            self.skipTest("자동 토픽이 생기지 않는 표본")
        cid = auto[0]["cluster_id"]
        r = self._act(action="status", id=cid, status="paused")
        got = next(t for t in (r.get("single") or []) + (r.get("composite") or []) if t["cluster_id"] == cid)
        self.assertEqual(got["status"], "paused")
        r = self._act(action="status", id=cid, status="active")
        got = next(t for t in (r.get("single") or []) + (r.get("composite") or []) if t["cluster_id"] == cid)
        self.assertEqual(got["status"], "active")

    def test_preview_and_suggest_carry_feed(self):
        r = self._act(action="preview", **{"def": {"keywords": ["삼성전자"], "req": {"keywords": ["삼성전자"]},
                                                   "feed": {"image": "yes"}}})
        pv = r["preview"]
        self.assertEqual(pv["feed_miss"], {"image": 3})                    # 적재 표본엔 이미지 필드가 없다
        self.assertEqual([c["k"] for c in pv["feed_chips"]], ["첨부"])
        r = self._act(action="suggest", text="삼성전자 분석. 카카오TV는 빼고 최근 3일")
        sg = r["suggest"]
        self.assertEqual(sg["neg"]["srcs"], ["카카오TV"])
        self.assertEqual(sg["feed"]["days"], 3)


if __name__ == "__main__":
    unittest.main()
