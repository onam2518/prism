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

    def test_system_inactive_turns_on_and_revives(self):
        """2-8: 설정 기간 동안 0건이면 시스템이 비활성으로 돌리고, 다시 매핑되면 되살린다."""
        from prism.topicops import _row_stats
        now = time.time()
        rows = [_row("옛글", ts=now - 20 * 86400), _row("새글", ts=now - 3600)]
        old = _row_stats([0], rows, now)
        self.assertEqual((old["inactive"], old["signal"]), (True, "비활성"))
        keep = _row_stats([0], rows, now, {"inactive_days": 60})          # 기간은 운영 설정
        self.assertEqual((keep["inactive"], keep["signal"]), (False, "정체 20일"))
        back = _row_stats([0, 1], rows, now)                              # 재매핑 → 되살아난다
        self.assertEqual((back["inactive"], back["signal"]), (False, ""))
        self.assertTrue(_row_stats([], rows, now)["inactive"])            # 매핑 0건도 비활성
        notime = _row_stats([0, 1], [_row("a"), _row("b")], now)          # 적재 시각 모름 ≠ 매핑 0건
        self.assertEqual((notime["inactive"], notime["signal"]), (False, ""))

    def test_sanitize_status_and_feed(self):
        from prism.serve import _sanitize_def
        d = _sanitize_def({"name": "x", "status": "weird", "srcs": ["뉴스"], "neg": {"srcs": ["뉴스", "카페"]},
                           "feed": {"days": "3", "types": ["shorts", "video/shorts"], "flags": ["isexclusive"]}})
        self.assertEqual(d["status"], "active")
        self.assertEqual((d["srcs"], d["neg"]["srcs"]), ([], ["뉴스", "카페"]))   # 겹치면 제외 우선(4-14)
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

    def test_unknown_entity_returns_registration_notice(self):
        """2-21: 사전에 없는 엔티티는 값을 만들지 않고 등록 안내로 돌린다."""
        r = self._act(action="save", **{"def": {"name": "미등재", "cats": ["Entertainment"],
                                                "keywords": ["없는엔티티요"]}})
        self.assertEqual(r.get("unknown_entities"), ["없는엔티티요"])
        self.assertIn("등록", r.get("notice") or "")
        r = self._act(action="save", **{"def": {"name": "등재", "keywords": ["삼성전자"],
                                                "req": {"keywords": ["삼성전자"]}}})
        self.assertNotIn("unknown_entities", r)

    def test_zero_axis_save_is_refused(self):
        """4-15: 네 축 중 하나도 없으면 저장하지 않는다(유통 가능 전건 묶음 금지)."""
        r = self._act(action="save", **{"def": {"name": "전부"}})
        self.assertFalse(r["ok"])
        self.assertIn("조건", r["error"])

    def test_merge_two_topics(self):
        """2-16: 갈라진 두 토픽을 하나로 · 조건과 제외 목록을 합치고 원본은 보관."""
        a = self._act(action="save", **{"def": {"name": "가", "cats": ["Business and Finance"],
                                                "neg": {"intents": ["팬덤·화제성"]}}})["saved"]["id"]
        b = self._act(action="save", **{"def": {"name": "나", "cats": ["Entertainment"]}})["saved"]["id"]
        r = self._act(action="merge", id=b, into=a)
        self.assertTrue(r["ok"])
        merged = next(d for d in r["customDefs"] if d["id"] == a)
        self.assertEqual(set(merged["cats"]), {"Business and Finance", "Entertainment"})
        self.assertEqual(merged["neg"]["intents"], ["팬덤·화제성"])
        self.assertEqual(next(g["status"] for g in r["custom"] if g["id"] == b), "archived")
        ids = {i for bd in self._custom(r, a)["bundles"] for i in bd["content_ids"]}
        self.assertEqual(len(ids), 3)                 # 합친 조건의 콘텐츠를 모두 묶는다(관련 묶음)
        self.assertFalse(self._act(action="merge", id=a, into=a)["ok"])

    def test_merge_carries_curation_memory(self):
        """2-16 · 3-11: 합칠 때 개별 제외 · 직접 편입 기억이 합친 쪽으로 따라간다(지우지 않는다)."""
        from prism.topic import _row_hash
        a = self._act(action="save", **{"def": {"name": "가", "cats": ["Business and Finance"]}})["saved"]["id"]
        b = self._act(action="save", **{"def": {"name": "나", "cats": ["Entertainment"]}})["saved"]["id"]
        hx = _row_hash(_row("삼성 분석1", entities=["삼성전자"], cats=["Business and Finance"]))
        hy = _row_hash(_row("연예 속보", entities=["아이유"], cats=["Entertainment"]))
        self._act(action="exclude", id=b, hash=hy, title="연예 속보")
        self._act(action="include", id=b, hash=hx, title="삼성 분석1")
        r = self._act(action="merge", id=b, into=a)
        self.assertNotIn(b, r["exclusions"])
        self.assertEqual([e["h"] for e in r["exclusions"][a]], [hy])
        self.assertEqual(self._custom(r, a)["bundles"][0].get("included_n"), 1)

    def test_include_missing_content_and_undo(self):
        """2-17: 조건에 안 걸린 콘텐츠를 운영자가 직접 넣고 되돌린다."""
        cid = self._act(action="save", **{"def": {"name": "연예", "cats": ["Entertainment"]}})["saved"]["id"]
        self.assertEqual(self._custom(self.S.topics_data(), cid)["core_count"], 1)
        from prism.topic import _row_hash
        h = _row_hash(_row("삼성 분석1", entities=["삼성전자"], cats=["Business and Finance"]))
        r = self._act(action="include", id=cid, hash=h, title="삼성 분석1")
        g = self._custom(r, cid)
        self.assertEqual((g["core_count"], g["bundles"][0]["included_n"]), (2, 1))
        g = self._custom(self._act(action="uninclude", id=cid, hash=h), cid)
        self.assertEqual(g["core_count"], 1)

    def test_preview_and_suggest_carry_feed(self):
        r = self._act(action="preview", **{"def": {"keywords": ["삼성전자"], "req": {"keywords": ["삼성전자"]},
                                                   "feed": {"image": "yes"}}})
        pv = r["preview"]
        self.assertEqual(pv["feed_miss"], {"image": 3})                    # 적재 표본엔 이미지 필드가 없다
        # 기본 제외 칩은 늘 맨 앞에 · 누를 수 없는 흐린 칩(2-19)
        self.assertEqual([c["k"] for c in pv["feed_chips"]], ["기본", "첨부"])
        self.assertTrue(pv["feed_chips"][0]["fixed"])
        r = self._act(action="suggest", text="삼성전자 분석. 카카오TV는 빼고 최근 3일")
        sg = r["suggest"]
        self.assertEqual(sg["neg"]["srcs"], ["카카오TV"])
        self.assertEqual(sg["feed"]["days"], 3)


if __name__ == "__main__":
    unittest.main()
