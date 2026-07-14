"""엔티티 사전: 등재 게이트·멱등 등록·위키데이터 보강(모킹)·수동 확정 보존·토픽 속성 조건.

실행: python3 -m pytest tests/test_entdict.py -q  (stdlib unittest · 의존성 0 · 네트워크 0)
위키데이터 호출은 entdict._http_json 심(seam)을 가짜 응답으로 대체한다.
"""
import os
import sys
import tempfile
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import entdict as ED               # noqa: E402
from prism import topic as TP                 # noqa: E402
from prism.store import Store                 # noqa: E402


# ── 가짜 위키데이터 응답(안세영 = 여성 배드민턴 선수) ────────────────────────
_WD_DB = {
    "안세영": {"qid": "Q1", "label": "안세영",
              "claims": {
                  "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}],
                  "P21": [{"mainsnak": {"datavalue": {"value": {"id": "Q6581072"}}}}],
                  "P27": [{"mainsnak": {"datavalue": {"value": {"id": "Q884"}}}}],
                  "P106": [{"mainsnak": {"datavalue": {"value": {"id": "Q13141064"}}}}],
                  "P569": [{"mainsnak": {"datavalue": {"value": {"time": "+2002-02-05T00:00:00Z"}}}}],
              }},
    "혼합개체": {"qid": "Q9", "label": "혼합개체",
              "claims": {  # P31 이 서로 다른 타입(인간+기업) → 자동 결정 없이 보류
                  "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}},
                          {"mainsnak": {"datavalue": {"value": {"id": "Q4830453"}}}}],
              }},
}
_WD_LABELS = {"Q6581072": "여성", "Q884": "대한민국", "Q13141064": "배드민턴 선수"}


def _fake_http_json(url: str) -> dict:
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    action = q["action"][0]
    if action == "wbsearchentities":
        name = q["search"][0]
        hit = _WD_DB.get(name)
        return {"search": ([{"id": hit["qid"], "label": hit["label"]}] if hit else [])}
    ids = q["ids"][0].split("|")
    if "claims" in (q.get("props") or [""])[0]:
        ent = next((v for v in _WD_DB.values() if v["qid"] == ids[0]), None)
        return {"entities": {ids[0]: {"claims": (ent or {}).get("claims", {}),
                                      "labels": {"ko": {"value": (ent or {}).get("label", "")}}}}}
    return {"entities": {i: {"labels": {"ko": {"value": _WD_LABELS.get(i, "")}}} for i in ids}}


class EntdictBase(unittest.TestCase):
    def setUp(self):
        self.store = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self._orig_http = ED._http_json
        ED._http_json = _fake_http_json

    def tearDown(self):
        ED._http_json = self._orig_http


class TestGateAndRegister(EntdictBase):
    def test_eligible_gate(self):
        self.assertTrue(ED.eligible("안세영"))
        self.assertTrue(ED.eligible("전기차 보조금"))
        self.assertFalse(ED.eligible("A씨"))          # 익명
        self.assertFalse(ED.eligible("3억원"))         # 수치
        self.assertFalse(ED.eligible("김"))            # 1자
        self.assertFalse(ED.eligible(""))

    def test_ingest_idempotent_and_alias(self):
        items = [("ch1", ["안세영", "배드민턴", "A씨"]), ("ch2", ["안세영"])]
        r1 = ED.ingest_meta(self.store, items)
        self.assertEqual(r1["created"], 2)             # 안세영·배드민턴 (A씨 게이트 차단)
        self.assertEqual(r1["linked"], 3)
        r2 = ED.ingest_meta(self.store, items)         # 재적재 = 신규 0 · 링크 멱등
        self.assertEqual(r2["created"], 0)
        eid = self.store.ent_id_by_alias("안세영")
        self.assertTrue(eid)
        e = self.store.ent_get(eid)
        self.assertEqual(e["status"], "pending")       # 등록 시점 = 보류(타입 미부여)
        self.assertEqual(e["type"], "")
        idx = self.store.ent_attr_index(team="")
        self.assertEqual(len(idx["ch1"]), 2)
        # 별칭 등록 → 같은 개체로 흡수
        self.store.ent_alias_add("안세영 선수", eid)
        self.assertEqual(self.store.ent_id_by_alias("안세영 선수"), eid)


class TestEnrich(EntdictBase):
    def _register(self, name):
        ED.ingest_meta(self.store, [("ch1", [name])])
        return self.store.ent_id_by_alias(name)

    def test_enrich_person(self):
        eid = self._register("안세영")
        r = ED.enrich_entity(self.store, eid)
        self.assertTrue(r["ok"] and r["matched"])
        e = self.store.ent_get(eid)
        self.assertEqual(e["type"], "PS")
        self.assertEqual(e["status"], "active")
        self.assertEqual(e["attrs"]["gender"], "여성")
        self.assertEqual(e["attrs"]["nationality"], "대한민국")
        self.assertEqual(e["attrs"]["occupation"], "스포츠인")            # 대분류 스냅
        self.assertEqual(e["attrs"]["occupation_detail"], "배드민턴 선수")
        self.assertEqual(e["attrs"]["birth_year"], "2002")
        self.assertEqual(e["external_ids"]["wikidata"], "Q1")
        self.assertEqual(e["attr_meta"]["gender"]["source"], "wikidata")

    def test_enrich_miss_records_and_stays_pending(self):
        eid = self._register("미등재개체")
        r = ED.enrich_entity(self.store, eid)
        self.assertTrue(r["ok"])
        self.assertFalse(r["matched"])
        e = self.store.ent_get(eid)
        self.assertEqual(e["status"], "pending")
        self.assertEqual(e["attr_meta"]["_enrich"]["result"], "miss")
        self.assertNotIn(eid, self.store.ent_pending_ids())   # 조회 이력 있음 → 재조회 대상 아님

    def test_type_conflict_stays_pending_with_candidates(self):
        eid = self._register("혼합개체")
        ED.enrich_entity(self.store, eid)
        e = self.store.ent_get(eid)
        self.assertEqual(e["type"], "")                # 복수 타입 충돌 → 자동 결정 없이 보류
        self.assertEqual(e["status"], "pending")
        self.assertIn("Q5", e["attr_meta"]["_type_candidates"])

    def test_manual_confirmed_not_overwritten(self):
        eid = self._register("안세영")
        ED.enrich_entity(self.store, eid)
        e = self.store.ent_get(eid)
        attrs, am = e["attrs"], e["attr_meta"]
        attrs["affiliation"] = "삼성생명 배드민턴단"   # 사람 확정
        am["affiliation"] = {"source": "manual", "status": "confirmed"}
        self.store.ent_update(eid, {"attrs": attrs, "attr_meta": am})
        ED.enrich_entity(self.store, eid)              # 재보강
        e2 = self.store.ent_get(eid)
        self.assertEqual(e2["attrs"]["affiliation"], "삼성생명 배드민턴단")   # 확정 보존
        self.assertEqual(e2["attrs"]["gender"], "여성")                     # 자동 필드는 갱신 유지


class TestOccupationSnap(unittest.TestCase):
    def test_snap(self):
        self.assertEqual(ED.snap_occupation(["배드민턴 선수"]), "스포츠인")
        self.assertEqual(ED.snap_occupation(["가수", "배우"]), "연예인")
        self.assertEqual(ED.snap_occupation(["국회의원"]), "정치인")
        self.assertEqual(ED.snap_occupation(["점성술사"]), "기타")
        self.assertEqual(ED.snap_occupation([]), "")


class TestTopicEattr(unittest.TestCase):
    """완료 기준 시나리오: 성별=여성 ∧ 직업=스포츠인 → '여성 스포츠인' 토픽.
    조건 전부를 '한 개체'가 만족해야 한다(여성 A + 남성 스포츠인 B 콘텐츠는 미매칭)."""

    def _rows(self):
        return [
            {"content_ref": {"displayServiceName": "스포츠", "title": "안세영 우승", "subtitle": "", "body": "b1"},
             "item_meta": {"entities": ["안세영"], "intent": [], "content_category": []}},
            {"content_ref": {"displayServiceName": "뉴스", "title": "혼합 기사", "subtitle": "", "body": "b2"},
             "item_meta": {"entities": ["김철수", "이영희"], "intent": [], "content_category": []}},
        ]

    def _ent_index(self, rows):
        return {
            ED.row_hash(rows[0]): [{"type": "PS", "name": "안세영", "gender": "여성", "occupation": "스포츠인"}],
            # 여성(정치인) + 스포츠인(남성) 이 서로 다른 개체 → '여성 스포츠인' 아님
            ED.row_hash(rows[1]): [{"type": "PS", "name": "김철수", "gender": "남성", "occupation": "스포츠인"},
                                   {"type": "PS", "name": "이영희", "gender": "여성", "occupation": "정치인"}],
        }

    def test_female_athlete_topic(self):
        rows = self._rows()
        d = {"id": "U-t", "name": "여성 스포츠인", "eattrs": ["gender:여성", "occupation:스포츠인"]}
        groups = TP.build_custom_topics(rows, set(), [d], ent_index=self._ent_index(rows))
        self.assertEqual(len(groups), 1)
        core = groups[0]["bundles"][0]
        self.assertEqual(core["count"], 1)                     # 같은 개체 AND → 1건만
        self.assertEqual(core["content_ids"], [0])
        self.assertIn("성별=여성", core["label"])

    def test_eattr_requires_dictionary(self):
        rows = self._rows()
        d = {"id": "U-t", "name": "여성 스포츠인", "eattrs": ["gender:여성"]}
        groups = TP.build_custom_topics(rows, set(), [d], ent_index=None)   # 사전 미구축
        self.assertEqual(groups[0]["bundles"][0]["count"], 0)

    def test_mixed_with_keyword_dim(self):
        rows = self._rows()
        d = {"id": "U-t", "name": "혼합", "keywords": ["안세영"], "eattrs": ["gender:여성"],
             "req": {"keywords": ["안세영"]}}
        groups = TP.build_custom_topics(rows, set(), [d], ent_index=self._ent_index(rows))
        self.assertEqual(groups[0]["bundles"][0]["count"], 1)

    def test_parse_eattr_whitelist(self):
        self.assertEqual(ED.parse_eattr("gender:여성"), ("gender", "여성"))
        self.assertIsNone(ED.parse_eattr("몰래키:값"))         # 비허용 키
        self.assertIsNone(ED.parse_eattr("gender:"))
        self.assertIsNone(ED.parse_eattr("여성"))

    def test_eattr_catalog(self):
        rows = self._rows()
        cat = TP.eattr_catalog(self._ent_index(rows))
        keys = {c["k"] for c in cat}
        self.assertIn("gender:여성", keys)
        self.assertIn("occupation:스포츠인", keys)
        top = next(c for c in cat if c["k"] == "type:PS")
        self.assertEqual(top["v"], 3)


if __name__ == "__main__":
    unittest.main()
