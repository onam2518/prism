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


def _fake_http_text_miss(url: str) -> str:
    raise OSError("no page")                       # 나무위키 문서 없음(네트워크 0 기본값)


# 나무위키 폴백용 가짜 문서: 분류(여자 배드민턴 선수) + 인포박스(국적·출생·소속·종목)
_NAMU_HTML = (
    '<html><head><title>미등재개체 - 나무위키</title></head><body>'
    '<a href="/w/%EB%B6%84%EB%A5%98:%EB%8C%80%ED%95%9C%EB%AF%BC%EA%B5%AD%EC%9D%98%20'
    '%EC%97%AC%EC%9E%90%20%EB%B0%B0%EB%93%9C%EB%AF%BC%ED%84%B4%20%EC%84%A0%EC%88%98">분류</a>'
    '<table><tr><td><div><strong>국적</strong></div></td><td><div>대한민국 <img></div></td></tr>'
    '<tr><td><div><strong>출생</strong></div></td><td><div>2002년 2월 5일 [1]</div></td></tr>'
    '<tr><td><div><strong>소속</strong></div></td><td><div>삼성생명 배드민턴단</div></td></tr>'
    '<tr><td><div><strong>종목</strong></div></td><td><div>배드민턴</div></td></tr></table></body></html>'
)
_NAMU_AMBIG = ('<html><head><title>동명 - 나무위키</title></head><body>'
               '<a href="/w/%EB%B6%84%EB%A5%98:%EB%8F%99%EC%9D%8C%EC%9D%B4%EC%9D%98%EC%96%B4">분류</a></body></html>')


class EntdictBase(unittest.TestCase):
    def setUp(self):
        self.store = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self._orig_http = ED._http_json
        self._orig_text = ED._http_text
        ED._http_json = _fake_http_json
        ED._http_text = _fake_http_text_miss

    def tearDown(self):
        ED._http_json = self._orig_http
        ED._http_text = self._orig_text


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

    def test_enrich_miss_becomes_unlisted(self):
        """두 소스 모두 미스 → 미등재(unlisted) 분리: 개체는 남고 통계·기본 목록·재보강에서 빠짐."""
        eid = self._register("미등재개체")
        r = ED.enrich_entity(self.store, eid)
        self.assertTrue(r["ok"])
        self.assertFalse(r["matched"])
        e = self.store.ent_get(eid)
        self.assertEqual(e["status"], "unlisted")
        self.assertEqual(e["attr_meta"]["_enrich"]["result"], "miss")
        self.assertNotIn(eid, self.store.ent_pending_ids())   # 조회 이력 있음 → 재조회 대상 아님
        st = self.store.ent_stats()
        self.assertEqual(st["unlisted"], 1)
        self.assertEqual(st["pending"], 0)
        # 기본 목록에서 제외 · unlisted/all 필터로 조회
        self.assertFalse(self.store.ent_list())
        self.assertEqual(len(self.store.ent_list(status="unlisted")), 1)
        self.assertEqual(len(self.store.ent_list(status="all")), 1)

    def test_unlisted_promoted_on_later_hit(self):
        """미등재 개체가 이후 소스에 등재되면(재보강 히트) 미등재 해제."""
        eid = self._register("안세영")
        orig = ED._http_json
        ED._http_json = lambda url: {"search": []}         # 첫 조회: 위키데이터도 미스
        try:
            ED.enrich_entity(self.store, eid)
        finally:
            ED._http_json = orig
        self.assertEqual(self.store.ent_get(eid)["status"], "unlisted")
        r = ED.enrich_entity(self.store, eid)              # 재보강: 위키데이터 히트
        self.assertTrue(r["matched"])
        self.assertEqual(self.store.ent_get(eid)["status"], "active")

    def test_active_not_demoted_on_miss(self):
        """수동 확정(active) 개체는 재보강 미스에도 미등재로 강등하지 않는다."""
        eid = self._register("미등재개체")
        e = self.store.ent_get(eid)
        self.store.ent_update(eid, {"type": "TM", "status": "active",
                                    "attr_meta": {"type": {"source": "manual", "status": "confirmed"}}})
        ED.enrich_entity(self.store, eid)                  # 두 소스 미스
        self.assertEqual(self.store.ent_get(eid)["status"], "active")

    def test_mark_and_purge_unlisted(self):
        """구 데이터 이행(mark) + 미등재 일괄 정리(purge)."""
        eid = self._register("미등재개체")
        # 구 형식: 미스 기록인데 status 는 pending (이행 대상)
        self.store.ent_update(eid, {"status": "pending",
                                    "attr_meta": {"_enrich": {"source": "wikidata", "result": "miss"}}})
        self.assertEqual(self.store.ent_mark_unlisted(), 1)
        self.assertEqual(self.store.ent_get(eid)["status"], "unlisted")
        self.assertEqual(self.store.ent_purge_unlisted(), 1)
        self.assertIsNone(self.store.ent_get(eid))
        self.assertEqual(self.store.ent_id_by_alias("미등재개체"), "")     # 별칭도 정리

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


class TestNamuFallback(EntdictBase):
    """위키데이터 미스 → 나무위키 폴백(POC 전용 · CC BY-NC-SA 라 폐기 전제 데이터에만 사용)."""

    def _register(self, name):
        ED.ingest_meta(self.store, [("ch1", [name])])
        return self.store.ent_id_by_alias(name)

    def test_namu_fallback_fills_type_and_attrs(self):
        eid = self._register("미등재개체")               # 위키데이터 미스
        ED._http_text = lambda url: _NAMU_HTML
        r = ED.enrich_entity(self.store, eid)
        self.assertTrue(r["ok"] and r["matched"])
        self.assertEqual(r["source"], "namuwiki")
        e = self.store.ent_get(eid)
        self.assertEqual(e["type"], "PS")               # 분류 '…선수' → PS
        self.assertEqual(e["status"], "active")
        self.assertEqual(e["attrs"]["gender"], "여성")   # 분류 '여자 …'
        self.assertEqual(e["attrs"]["nationality"], "대한민국")
        self.assertEqual(e["attrs"]["birth_year"], "2002")
        self.assertEqual(e["attrs"]["occupation"], "스포츠인")
        self.assertEqual(e["attrs"]["affiliation"], "삼성생명 배드민턴단")
        self.assertEqual(e["attr_meta"]["gender"]["source"], "namuwiki")
        self.assertEqual(e["external_ids"]["namuwiki"], "미등재개체")

    def test_namu_ambiguous_stays_pending(self):
        eid = self._register("동명개체")
        ED._http_text = lambda url: _NAMU_AMBIG
        r = ED.enrich_entity(self.store, eid)
        self.assertTrue(r["ok"])
        self.assertFalse(r["matched"])
        self.assertTrue(r.get("ambiguous"))
        e = self.store.ent_get(eid)
        self.assertEqual(e["type"], "")                  # 동음이의 → 자동 결정 없이 보류
        self.assertEqual(e["status"], "pending")
        self.assertEqual(e["attr_meta"]["_enrich"]["result"], "ambiguous")

    def test_namu_confirmed_not_overwritten(self):
        eid = self._register("미등재개체")
        e = self.store.ent_get(eid)
        self.store.ent_update(eid, {"attrs": {"gender": "남성"},
                                    "attr_meta": {"gender": {"source": "manual", "status": "confirmed"}}})
        ED._http_text = lambda url: _NAMU_HTML
        ED.enrich_entity(self.store, eid)
        self.assertEqual(self.store.ent_get(eid)["attrs"]["gender"], "남성")   # 사람 확정 우선

    def test_namu_gate_env_off(self):
        eid = self._register("미등재개체")
        ED._http_text = lambda url: _NAMU_HTML
        os.environ["PRISM_ENTDICT_NAMU"] = "0"
        try:
            r = ED.enrich_entity(self.store, eid)
        finally:
            os.environ.pop("PRISM_ENTDICT_NAMU", None)
        self.assertFalse(r["matched"])                   # 게이트 꺼짐 → 폴백 미사용(미스 처리)

    def test_namu_first_skips_wikidata(self):
        """나무위키 1순위: 히트하면 위키데이터를 호출하지 않는다(소스 우선순위 계약)."""
        eid = self._register("안세영")                   # 위키데이터에도 있는 개체
        wd_calls = []
        orig = ED._http_json
        ED._http_json = lambda url: wd_calls.append(url) or orig(url)
        try:
            ED._http_text = lambda url: _NAMU_HTML
            r = ED.enrich_entity(self.store, eid)
        finally:
            ED._http_json = orig
        self.assertEqual(r["source"], "namuwiki")
        self.assertEqual(wd_calls, [])                   # 위키데이터 미호출
        e = self.store.ent_get(eid)
        self.assertEqual((e["attr_meta"]["_enrich"] or {}).get("source"), "namuwiki")

    def test_namu_ambiguous_falls_to_wikidata(self):
        """나무위키 동음이의 → 위키데이터 폴백이 해소하면 위키데이터 채택."""
        eid = self._register("안세영")
        ED._http_text = lambda url: _NAMU_AMBIG
        r = ED.enrich_entity(self.store, eid)
        self.assertTrue(r["matched"])
        self.assertEqual(r["source"], "wikidata")
        self.assertEqual(r["qid"], "Q1")

    def test_reenrich_namu_overrides_auto_wikidata(self):
        """전체 재보강 시나리오: 위키데이터 auto 값 위에 나무위키가 갱신(확정 아님 → 허용)."""
        eid = self._register("안세영")
        r1 = ED.enrich_entity(self.store, eid)           # 나무위키 미스(기본) → 위키데이터
        self.assertEqual(r1["source"], "wikidata")
        ED._http_text = lambda url: _NAMU_HTML           # 재보강: 나무위키 등장
        r2 = ED.enrich_entity(self.store, eid)
        self.assertEqual(r2["source"], "namuwiki")
        e = self.store.ent_get(eid)
        self.assertEqual(e["attr_meta"]["gender"]["source"], "namuwiki")
        self.assertEqual(e["external_ids"]["wikidata"], "Q1")     # 외부키 매핑은 누적 보존


class TestHttp429Backoff(unittest.TestCase):
    """429 는 Retry-After 준수 재시도 · 다른 오류·재시도 소진은 그대로 전파."""

    def _http_error(self, code, retry_after="0"):
        import email.message
        h = email.message.Message()
        h["Retry-After"] = retry_after
        import urllib.error
        return urllib.error.HTTPError("http://x", code, "err", h, None)

    def test_retries_on_429_then_succeeds(self):
        calls = {"n": 0}

        def fake_urlopen(req, timeout=0):
            calls["n"] += 1
            if calls["n"] < 3:
                raise self._http_error(429)
            import io
            class R(io.BytesIO):
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return R(b'{"ok": 1}')

        import urllib.request as UR
        orig = UR.urlopen
        UR.urlopen = fake_urlopen
        try:
            self.assertEqual(ED._http_json("http://x"), {"ok": 1})
        finally:
            UR.urlopen = orig
        self.assertEqual(calls["n"], 3)

    def test_non_429_propagates(self):
        import urllib.request as UR
        orig = UR.urlopen
        UR.urlopen = lambda req, timeout=0: (_ for _ in ()).throw(self._http_error(500))
        try:
            with self.assertRaises(Exception):
                ED._http_json("http://x")
        finally:
            UR.urlopen = orig


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

    def test_suggest_dims_eattrs(self):
        """자연어의 개체 속성 언급 → eattrs 제안(사전 실재값만 · 같은 키 최다빈도 1개 · 빈도순)."""
        cands = [{"k": "type:PS", "v": 30}, {"k": "occupation:스포츠인", "v": 12},
                 {"k": "gender:여성", "v": 10}, {"k": "gender:남성", "v": 8},
                 {"k": "nationality:대한민국", "v": 5}]
        sug = TP.suggest_dims("여성 스포츠인 콘텐츠 모아줘", self._rows(), set(), eattr_cands=cands)
        self.assertEqual(sug["eattrs"], ["occupation:스포츠인", "gender:여성"])   # 빈도순 · 남성 제외(키 중복)
        sug2 = TP.suggest_dims("경제 심층 분석", self._rows(), set(), eattr_cands=cands)
        self.assertEqual(sug2["eattrs"], [])                                     # 속성 언급 없음 → 빈 배열
        sug3 = TP.suggest_dims("여성 스포츠인", self._rows(), set())              # 후보 없음(사전 미구축)
        self.assertEqual(sug3["eattrs"], [])

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
