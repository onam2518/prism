"""토픽 큐레이션: 품질 자격(G-only) + 개별 콘텐츠 제외(자동·사용자 토픽 공통).

- 자격: R·YELLOW 콘텐츠는 토픽(엔티티·사건·조건·사용자) 대상 자체가 아니다.
- 제외: 토픽 id × content_hash 오버레이 · 매칭 정의는 그대로 · excluded_n 표면화 · 복구 가능.

실행: python3 -m pytest tests/test_topic_curation.py -q  (stdlib unittest · 의존성 0)
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _row(title, grade="G", review="", entities=(), intent=(), cats=(), svc="뉴스"):
    return {"content_ref": {"title": title, "subtitle": "", "body": "본문-" + title,
                            "displayServiceName": svc},
            "item_meta": {"summary": "리드-" + title, "entities": list(entities),
                          "intent": list(intent), "content_category": list(cats)},
            "quality_meta": {"finalGrade": grade, "review": review, "reasons": []}}


def _rows():
    return [
        _row("삼성 분석1", entities=["삼성전자", "이재용"], intent=["분석·해설"],
             cats=["Business and Finance"]),                                     # 0 G
        _row("삼성 분석2", entities=["삼성전자", "이재용"], intent=["기획·심층"],
             cats=["Business and Finance"]),                                     # 1 G
        _row("삼성 낚시", grade="R", entities=["삼성전자", "이재용"], intent=["분석·해설"],
             cats=["Business and Finance"]),                                     # 2 R → 대상 아님
        _row("삼성 대기", review="yellow", entities=["삼성전자"], intent=["분석·해설"],
             cats=["Business and Finance"]),                                     # 3 YELLOW → 대상 아님
        _row("넷플 화제", entities=["넷플릭스"], intent=["흥미·화제"],
             cats=["Entertainment"]),                                            # 4 G
    ]


def _hash(rows, i):
    from prism.topic import _row_hash
    return _row_hash(rows[i])


def _build(rows, **kw):
    from prism.topic import build_topics
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "r.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return build_topics(p, **kw)


class TestEligibility(unittest.TestCase):
    """품질 미달(R·YELLOW)은 어떤 토픽에도 편입되지 않는다."""

    def test_ineligible_rows_in_no_pool(self):
        rows = _rows()
        d = _build(rows, settings={"entity_min": 1})
        self.assertEqual(d["n_contents"], 5)
        self.assertEqual(d["n_eligible"], 3)                 # 0·1·4 만
        for grp in ("single", "composite", "filter"):
            for p in d[grp]:
                self.assertFalse({2, 3} & set(p["content_ids"]),
                                 f"{grp}:{p['cluster_id']} 에 품질 미달 콘텐츠 포함")

    def test_entity_and_event_membership(self):
        rows = _rows()
        d = _build(rows, settings={"entity_min": 1})
        samsung = next(p for p in d["single"] if p["name"] == "삼성전자")
        self.assertEqual(samsung["content_ids"], [0, 1])     # R(2)·YELLOW(3) 제외
        self.assertTrue(d["composite"])                      # 0·1 공통 엔티티 2개 → 사건 형성
        self.assertEqual(d["composite"][0]["content_ids"], [0, 1])

    def test_custom_and_catalog_skip_ineligible(self):
        from prism import topic as TP
        rows = _rows()
        d = _build(rows, custom_defs=[{"id": "U-경제", "name": "경제",
                                       "cats": ["Business and Finance"]}])
        core = next(b for g in d["custom"] for b in g["bundles"] if b["kind"] == "core")
        self.assertEqual(core["content_ids"], [0, 1])
        cat = TP.studio_catalog(rows, {"뉴스"})
        ents = {x["k"]: x["v"] for x in cat["keywords"]}
        self.assertEqual(ents.get("삼성전자"), 2)            # 5건 언급이지만 자격 2건만 집계

    def test_preview_total_is_eligible(self):
        from prism import topic as TP
        rows = _rows()
        pv = TP.preview_definition(rows, {"뉴스"}, {"name": "x", "cats": [], "intents": [], "keywords": []})
        self.assertEqual(pv["n_total"], 3)
        self.assertEqual(pv["bundles"][0]["count"], 3)       # 조건 없음 = 자격 전수


class TestExclusionOverlay(unittest.TestCase):
    """토픽 id × content_hash 제외: 자동(엔티티·사건·조건)·사용자 토픽 동일 방식."""

    def test_entity_pool_exclusion(self):
        rows = _rows()
        h0 = _hash(rows, 0)
        d = _build(rows, settings={"entity_min": 1},
                   exclusions={"S-삼성전자": [{"h": h0, "title": "삼성 분석1"}]})
        samsung = next(p for p in d["single"] if p["name"] == "삼성전자")
        self.assertEqual(samsung["content_ids"], [1])
        self.assertEqual(samsung["count"], 1)
        self.assertEqual(samsung["excluded_n"], 1)
        other = next(p for p in d["single"] if p["name"] == "이재용")
        self.assertEqual(other["content_ids"], [0, 1])       # 다른 토픽은 무영향

    def test_event_rep_reselected(self):
        rows = _rows()
        d0 = _build(rows)
        ev = d0["composite"][0]
        self.assertEqual(ev["representative_content"], 0)
        d = _build(rows, exclusions={ev["cluster_id"]: [_hash(rows, 0)]})
        ev2 = next(p for p in d["composite"] if p["cluster_id"] == ev["cluster_id"])
        self.assertEqual(ev2["content_ids"], [1])
        self.assertEqual(ev2["representative_content"], 1)
        self.assertEqual(ev2["rep_title"], "삼성 분석2")

    def test_custom_group_exclusion_string_entry(self):
        rows = _rows()
        d = _build(rows, custom_defs=[{"id": "U-경제", "name": "경제",
                                       "cats": ["Business and Finance"]}],
                   exclusions={"U-경제": [_hash(rows, 1)]})   # 하위호환: 문자열 항목
        g = d["custom"][0]
        core = next(b for b in g["bundles"] if b["kind"] == "core")
        self.assertEqual(core["content_ids"], [0])
        self.assertEqual(core["excluded_n"], 1)
        self.assertEqual(g["core_count"], 1)

    def test_filter_active_recomputed(self):
        rows = [_rows()[4]]                                   # 연예 1건만
        d0 = _build(rows)
        ent = next(p for p in d0["filter"] if p["name"] == "연예 × 화제·인물")
        self.assertTrue(ent["active"])
        d = _build(rows, exclusions={ent["cluster_id"]: [_hash(rows, 0)]})
        ent2 = next(p for p in d["filter"] if p["name"] == "연예 × 화제·인물")
        self.assertEqual(ent2["count"], 0)
        self.assertFalse(ent2["active"])                      # 전량 제외 → 저조 전환

    def test_row_hash_matches_feedback_key(self):
        from prism.store import content_hash
        rows = _rows()
        ref = rows[0]["content_ref"]
        self.assertEqual(_hash(rows, 0), content_hash({
            "displayServiceName": ref["displayServiceName"], "title": ref["title"],
            "subtitle": ref["subtitle"], "body": ref["body"]}))


class TestServeActions(unittest.TestCase):
    """exclude/restore 액션 · 드릴 정합 · 삭제 시 제외 정리 (sqlite 스토어)."""

    def setUp(self):
        import prism.serve as S
        from prism.store import Store
        self.S = S
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_store, self._orig_last = S._STORE, list(S._LAST_RESULTS)
        S._STORE = Store(os.path.join(self._tmp.name, "t.db"))
        S._LAST_RESULTS[:] = _rows()

    def tearDown(self):
        self.S._STORE = self._orig_store
        self.S._LAST_RESULTS[:] = self._orig_last
        self._tmp.cleanup()

    def test_exclude_restore_roundtrip(self):
        S, rows = self.S, _rows()
        h0 = _hash(rows, 0)
        td = S.topic_studio_action({"action": "save", "def": {"name": "경제", "cats": ["Business and Finance"]}})
        g = next(x for x in td["custom"] if x["name"] == "경제")
        core = next(b for b in g["bundles"] if b["kind"] == "core")
        self.assertEqual(core["count"], 2)

        td = S.topic_studio_action({"action": "exclude", "id": g["id"], "hash": h0,
                                    "title": "삼성 분석1", "topic": "경제"})
        core = next(b for x in td["custom"] for b in x["bundles"] if b["kind"] == "core")
        self.assertEqual((core["count"], core["excluded_n"]), (1, 1))
        self.assertEqual(len(td["exclusions"][g["id"]]), 1)

        dr = S.topic_drill(core["cluster_id"])                # 드릴도 제외 반영 + 그룹 id 반환
        self.assertEqual(dr["topic_id"], g["id"])
        self.assertEqual(dr["n"], 1)
        self.assertNotIn(h0, [it["hash"] for it in dr["items"]])

        td = S.topic_studio_action({"action": "restore", "id": g["id"], "hash": h0})
        core = next(b for x in td["custom"] for b in x["bundles"] if b["kind"] == "core")
        self.assertEqual(core["count"], 2)
        self.assertNotIn(g["id"], td["exclusions"])

    def test_exclude_requires_ids(self):
        out = self.S.topic_studio_action({"action": "exclude", "id": "", "hash": ""})
        self.assertFalse(out.get("ok", True))

    def test_delete_cleans_exclusions_and_settings_keep_them(self):
        S, rows = self.S, _rows()
        td = S.topic_studio_action({"action": "save", "def": {"name": "경제", "cats": ["Business and Finance"]}})
        gid = next(x for x in td["custom"] if x["name"] == "경제")["id"]
        S.topic_studio_action({"action": "exclude", "id": gid, "hash": _hash(rows, 0)})
        auto_ev = _build(rows)["composite"][0]["cluster_id"]  # 자동 토픽 제외도 공존
        S.topic_studio_action({"action": "exclude", "id": auto_ev, "hash": _hash(rows, 1)})

        td = S.topic_studio_action({"action": "settings", "settings": {"co_min": 2}})
        self.assertIn(gid, td["exclusions"])                  # 튜닝 저장이 제외를 지우지 않는다
        td = S.topic_studio_action({"action": "delete", "id": gid})
        self.assertNotIn(gid, td["exclusions"])               # 토픽 삭제 = 그 토픽 제외 정리
        self.assertIn(auto_ev, td["exclusions"])              # 자동 토픽 제외는 유지

    def test_entity_auto_topic_drill_and_exclude(self):
        S, rows = self.S, _rows()
        S.topic_studio_action({"action": "settings", "settings": {"entity_min": 1}})
        dr = S.topic_drill("S-삼성전자")
        self.assertEqual((dr["topic_id"], dr["n"]), ("S-삼성전자", 2))
        td = S.topic_studio_action({"action": "exclude", "id": "S-삼성전자", "hash": _hash(rows, 0)})
        samsung = next(p for p in td["single"] if p["name"] == "삼성전자")
        self.assertEqual((samsung["count"], samsung["excluded_n"]), (1, 1))
        self.assertEqual(S.topic_drill("S-삼성전자")["n"], 1)

    def test_preview_samples_carry_detail_contract(self):
        """미리보기 표본 배지 클릭 → 공통 상세 스플릿뷰: 표본이 상세 필드 전체를 갖춘다."""
        out = self.S.topic_studio_action({"action": "preview",
                                          "def": {"name": "x", "cats": ["Business and Finance"]}})
        core = next(b for b in out["preview"]["bundles"] if b["kind"] == "core")
        self.assertTrue(core.get("samples"))
        for k in ("hash", "title", "body", "url", "entities", "intent", "category", "grade"):
            self.assertIn(k, core["samples"][0], f"표본에 상세 필드 누락: {k}")
        self.assertEqual(core["samples"][0]["title"], "삼성 분석1")


if __name__ == "__main__":
    unittest.main()
