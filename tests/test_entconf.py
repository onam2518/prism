"""엔티티 확신도(entconf) 회귀: 읽기 시점 결정적 산출 + 0.5 기본 필터의 원천.

- 제목 포함 엔티티 > 본문 스침 엔티티(순서 보장 · 주간회의 설계 요구)
- 저연관 사례(엔비디아 기사 후반에 1회 스친 '박민우')는 0.5 미만으로 접힘
- 결정성: 같은 입력이면 항상 같은 출력(저장 없는 읽기 시점 산출의 전제)
- 노출 경로: serve._detail_row 와 reviewops.raw_rows 가 entities_scored 를
  병행 노출하고 기존 entities 키(문자열 배열 계약)는 그대로 둔다

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import entconf as EC

# 주간회의 사례 재현: 엔비디아 기사에 스치듯 등장한 저연관 인명 '박민우'
REF = {
    "displayServiceName": "뉴스",
    "title": "엔비디아, 차세대 GPU 공개",
    "subtitle": "",
    "body": ("엔비디아가 신제품 발표 행사에서 차세대 GPU 아키텍처를 공개했다. "
             "엔비디아는 이번 GPU 가 데이터센터 학습 성능을 크게 끌어올린다고 밝혔다. "
             "업계는 엔비디아의 공급 일정과 가격 정책에 주목하고 있다. "
             "한편 이날 행사장에는 국내 스타트업 관계자인 박민우 씨도 참석했다."),
}
IM = {
    "summary": "엔비디아가 데이터센터 성능을 높인 차세대 GPU 아키텍처를 공개했다.",
    "entities": ["엔비디아", "차세대 GPU", "데이터센터", "박민우"],
}


class TestEntityConfidence(unittest.TestCase):
    def test_title_beats_body_graze(self):
        """제목 포함(강한 가점) 엔티티가 본문 스침 엔티티보다 항상 높다."""
        core = EC.entity_confidence("엔비디아", REF, IM)
        graze = EC.entity_confidence("박민우", REF, IM)
        self.assertGreater(core, graze)
        self.assertGreater(core, 0.5)      # 주제 엔티티는 기본 노출(conf > 0.5)

    def test_low_relevance_below_half(self):
        """회의 사례 고정: 후반 1회 스침 + 제목·리드문 신호 0 → 0.5 미만으로 접힘."""
        self.assertLess(EC.entity_confidence("박민우", REF, IM), 0.5)

    def test_light_normalization_partial_match(self):
        """공백·가운뎃점 무시 부분일치: '차세대 GPU' 는 띄어쓰기 차이와 무관하게 매칭."""
        self.assertGreater(EC.entity_confidence("차세대 GPU", REF, IM), 0.5)
        self.assertGreater(EC.entity_confidence("차세대GPU", REF, IM), 0.0)

    def test_deterministic(self):
        """결정성: 같은 입력이면 항상 같은 출력(마이그레이션 0 소급 적용의 전제)."""
        a = EC.scored_entities(IM, REF)
        b = EC.scored_entities(IM, REF)
        self.assertEqual(a, b)
        self.assertEqual([s["name"] for s in a], IM["entities"])   # 순서 보존
        for s in a:
            self.assertGreaterEqual(s["conf"], 0.0)
            self.assertLessEqual(s["conf"], 1.0)

    def test_empty_inputs(self):
        self.assertEqual(EC.entity_confidence("", REF, IM), 0.0)
        self.assertEqual(EC.scored_entities({}, {}), [])
        self.assertEqual(EC.scored_entities(None, None), [])


class TestExposure(unittest.TestCase):
    def _row(self):
        return {"content_ref": dict(REF), "item_meta": dict(IM),
                "quality_meta": {"finalGrade": "G", "review": "yellow"},
                "trace": {"model": "m", "version": 1}}

    def test_detail_row_parallel_key(self):
        """_detail_row: entities(계약) 그대로 + entities_scored 병행 노출."""
        import prism.serve as SV
        d = SV._detail_row(self._row())
        self.assertEqual(d["entities"], IM["entities"])            # 기존 키 불변(하위 호환)
        self.assertEqual([s["name"] for s in d["entities_scored"]], IM["entities"])
        confs = {s["name"]: s["conf"] for s in d["entities_scored"]}
        self.assertGreater(confs["엔비디아"], 0.5)
        self.assertLess(confs["박민우"], 0.5)

    def test_raw_rows_slim_and_detail(self):
        """raw_rows(슬림): entities 키 유지 · 무거운 필드 제외. 확신도는 raw_detail(단건)이 노출."""
        import prism.serve as SV
        row = self._row()

        class FakeStore:
            def feedback_map(self, team=None):
                return {}

            def purpose_map(self, team=None):
                return {}

        orig = (SV.results_rows, SV.get_store, SV._inject_gold)
        SV.results_rows = lambda limit=5000, team=None: [row]
        SV.get_store = lambda: FakeStore()
        SV._inject_gold = lambda items, reviewer, team=None: []
        self.addCleanup(lambda: (setattr(SV, "results_rows", orig[0]),
                                 setattr(SV, "get_store", orig[1]),
                                 setattr(SV, "_inject_gold", orig[2])))

        r = SV.raw_rows()
        self.assertTrue(r["ok"])
        item = r["items"][0]
        self.assertEqual(item["entities"], IM["entities"])         # 기존 키 불변
        for heavy in ("body", "item_meta", "quality_meta", "entities_scored"):
            self.assertNotIn(heavy, item)                          # 목록은 슬림(상세는 단건 라우트)
        d = SV.raw_detail(item["hash"])
        self.assertTrue(d["ok"])
        self.assertEqual(d["item"]["body"], REF["body"])           # 상세 = 본문·메타 원본 복원
        self.assertEqual(d["item"]["item_meta"], IM)
        scored = d["item"]["entities_scored"]
        self.assertEqual([s["name"] for s in scored], IM["entities"])
        confs = {s["name"]: s["conf"] for s in scored}
        self.assertGreater(confs["엔비디아"], 0.5)
        self.assertLess(confs["박민우"], 0.5)
        self.assertFalse(SV.raw_detail("없는해시")["ok"])


if __name__ == "__main__":
    unittest.main()


class NonTitleCeilingTest(unittest.TestCase):
    """제목 밖 엔티티가 구조적으로 막혀 있던 결함(2026-07-29 실데이터 진단).

    예전 가중치는 제목을 뺀 합이 0.45 라, 리드문·첫문단·본문 반복이 전부 걸려도
    기본 노출선(0.5)을 넘을 수 없었다. 운영 표본 1,990개에서 제목 밖 1,889개가
    한 개도 통과하지 못했다 — 확신도가 사실상 '제목에 있나?'의 다른 표현이었다.
    """

    def test_non_title_can_now_pass(self):
        from prism import entconf as EC
        body = ("나루호 발사가 성공했다. " * 2) + ("다른 문장. " * 20) + ("나루호 후속. " * 3)
        ref = {"title": "우주 개발 소식", "body": body}          # 제목에 없음
        im = {"summary": "나루호 발사 성공", "entities": ["나루호"]}
        self.assertGreaterEqual(EC.entity_confidence("나루호", ref, im), 0.5)

    def test_summary_only_still_folds(self):
        """리드문에만 스치고 원문 근거가 없으면 여전히 접힌다(오추출 방어)."""
        from prism import entconf as EC
        ref = {"title": "우주 개발 소식", "body": "본문에는 없는 이름입니다. " * 30}
        im = {"summary": "무관한이름 언급", "entities": ["무관한이름"]}
        self.assertLess(EC.entity_confidence("무관한이름", ref, im), 0.5)

    def test_title_alone_is_on_the_line(self):
        from prism import entconf as EC
        ref = {"title": "삼성전자 신제품", "body": "관련 없는 본문. " * 30}
        im = {"summary": "", "entities": ["삼성전자"]}
        self.assertAlmostEqual(EC.entity_confidence("삼성전자", ref, im), 0.5, places=3)

    def test_non_title_ceiling_is_above_threshold(self):
        """가중치 합이 다시 0.5 아래로 내려가면 같은 결함이 재발한다."""
        from prism import entconf as EC
        self.assertGreater(EC.W_SUMMARY + EC.W_FIRST + EC.W_FREQ, 0.5)
