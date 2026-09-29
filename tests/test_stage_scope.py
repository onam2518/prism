"""1차 발행 범위 회귀 (2026-09-29 정책 감사 3-3·4-8·4-9·4-19).

- 서비스 그룹: displayServiceName 원문이 media/ugc 로 정확히 갈린다(4-19).
- 법령 대표 등급이 최고 위험이어도 아이템 메타 4종은 나온다(4-8 · 위키 364314733).
- 품질 단계를 끄면 등급은 빈 값('판정 없음')이고 G 로 유통되지 않는다(3-3).
- 토픽 집계는 빈 등급을 기본 통과(G)로 세지 않는다(4-9 · 위키 279904498).

실행: python3 -m unittest tests.test_stage_scope
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestServiceGroup(unittest.TestCase):
    """displayServiceName 원문별 그룹 판정(위키 278036632 · PGC 5 / UGC 5)."""

    CASES = {
        "뉴스": "media", "연예": "media", "스포츠": "media",
        "콘텐츠뷰 (일반)": "media", "콘텐츠뷰(일반)": "media", "멜론": "media",
        "다음카페": "ugc", "콘텐츠뷰 (커뮤니티)": "ugc", "콘텐츠뷰(커뮤니티)": "ugc",
        "티스토리": "ugc", "VOD": "ugc", "루프": "ugc",
        "카카오TV": "ugc", "카카오비디오": "ugc",        # 마이그레이션 기간 구 명칭
        "": "media", "없는서비스": "media",              # 미정의 = 보수적 default
    }

    def test_dispatch_group_by_display_name(self):
        from prism import routing as R
        from prism.schema import Content
        for name, want in self.CASES.items():
            got = R.dispatch(Content(displayServiceName=name, title="제목", body="본문")).service_group
            self.assertEqual(got, want, f"{name!r} -> {got}")

    def test_ugc_only_metas_active_for_ugc_services(self):
        """UGC 전용 3종(format·political·hate)이 실제 UGC 서비스에서 켜진다."""
        from prism import routing as R
        from prism.schema import Content
        for name in ("다음카페", "콘텐츠뷰 (커뮤니티)", "티스토리", "VOD", "루프"):
            active = R.dispatch(Content(displayServiceName=name, title="t", body="b")).active_quality_metas
            for m in ("format", "political", "hate"):
                self.assertIn(m, active, f"{name} · {m}")
        for name in ("뉴스", "연예", "스포츠", "콘텐츠뷰 (일반)", "멜론"):
            active = R.dispatch(Content(displayServiceName=name, title="t", body="b")).active_quality_metas
            for m in ("format", "political", "hate"):
                self.assertNotIn(m, active, f"{name} · {m}")


def _legal_red_mock(system, user, tag):
    """법령 대표 등급 RED(스코어 100)를 만드는 결정론 mock."""
    from prism import harness as H
    if tag == "legal_route":
        return {"harm_types": [{"code": "fraud", "confidence": 0.9}]}
    if tag.startswith("legal:"):
        return {"a": 40, "b": 30, "c": 30, "evidence": "mock"}
    return H._mock_generator(system, user, tag)


def _mock_llm(fn=None):
    from prism.llm import LLMClient
    llm = LLMClient(model="mock-model", api_key="", mock=True)
    llm._mock_fn = fn
    return llm


CONTENT = {"displayServiceName": "뉴스", "title": "삼성전자 노사 협상 결렬",
           "body": "삼성전자 노사가 협상에 이르지 못해 노조가 총파업을 예고했다. " * 3}


class TestLegalRedKeepsItemMeta(unittest.TestCase):
    """4-8: 법령 최고 위험이어도 아이템 메타 4종은 추출한다(위키 364314733 · 2026-09-08)."""

    def test_item_meta_survives_legal_red(self):
        from prism import harness as H
        out = H.run(CONTENT, _mock_llm(_legal_red_mock), H.Methodology(legal=True))
        self.assertEqual(out["legal_meta"]["representative_grade"], "RED")
        im = out["item_meta"]
        self.assertIsNotNone(im, "법령 RED 에서 아이템 메타가 통째로 비었다(구 halt 회귀)")
        self.assertTrue(im.get("summary"))
        self.assertTrue(im.get("entities"))
        self.assertTrue(im.get("intent"))
        self.assertTrue(im.get("content_category"))
        # 유통 차단은 등급으로 한다(품질 판정은 차단 표식으로 대체 · 추가 호출 없음)
        self.assertEqual(out["quality_meta"]["finalGrade"], "R")


class TestQualityStageOff(unittest.TestCase):
    """3-3: 품질 판정 단계를 끄면 등급은 빈 값('판정 없음') · 기본값은 현재 동작(켬)."""

    def test_default_is_on(self):
        from prism import harness as H
        from prism.config import Config
        self.assertTrue(H.Methodology().quality)
        self.assertTrue(Config().quality_stage)

    def test_grade_empty_and_no_quality_call(self):
        from prism import harness as H
        out = H.run(CONTENT, _mock_llm(), H.Methodology(quality=False))
        self.assertEqual(out["quality_meta"]["finalGrade"], "")      # 빈 값 = 판정 없음
        self.assertNotIn("quality", out["trace"]["by_call"])          # 품질 호출 생략
        self.assertTrue(out["item_meta"]["entities"])                 # 아이템 메타는 그대로

    def test_empty_grade_is_not_green(self):
        """빈 등급을 G 로 채우는 fail-open 이 없어야 한다(검수·유통 공통 원칙)."""
        from prism import harness as H
        out = H.run(CONTENT, _mock_llm(), H.Methodology(quality=False))
        self.assertNotEqual(out["quality_meta"]["finalGrade"], "G")


def _topic_row(title, ents, qm=None):
    r = {"content_ref": {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": title},
         "item_meta": {"summary": title, "entities": list(ents), "intent": ["속보·단신"],
                       "content_category": ["News and Politics / Society"], "hold_fields": []}}
    if qm is not None:
        r["quality_meta"] = qm
    return r


class TestTopicWithoutQualityGrade(unittest.TestCase):
    """4-9: 토픽 대상 조건은 품질 등급에 묶이지 않는다(위키 279904498 · 2026-09-22 수정)."""

    def _rows(self):
        return [
            _topic_row("a", ["삼성전자", "총파업"]),                                   # 품질 단계 off = 등급 없음
            _topic_row("b", ["삼성전자", "총파업"], {"finalGrade": "", "reasons": [], "review": "auto"}),
            _topic_row("c", ["삼성전자"], {"finalGrade": "G", "reasons": [], "review": "auto"}),
            _topic_row("d", ["삼성전자"], {"finalGrade": "R", "reasons": ["ad"], "review": "auto"}),
            _topic_row("e", ["삼성전자"], {"finalGrade": "", "reasons": [], "review": "yellow"}),
        ]

    def test_no_grade_is_not_counted_as_green(self):
        from prism import topic as T
        rows = self._rows()
        self.assertEqual([T._grade(r) for r in rows], ["", "", "G", "R", "YELLOW"])

    def test_no_grade_rows_stay_in_topic_scope(self):
        """등급 없음은 대상에서 빼지 않는다. 빠지는 것은 R 과 검수 대기(YELLOW)뿐."""
        from prism import topic as T
        rows = self._rows()
        self.assertEqual([T._eligible(r) for r in rows], [True, True, True, False, False])
        pools = T.build_entity_topics(rows, T._service_names(rows), {}, min_contents=2)
        p = next(x for x in pools if x["name"] == "삼성전자")
        self.assertEqual(p["content_ids"], [0, 1, 2])


class TestDashboardNoGrade(unittest.TestCase):
    """4-24 · 4-9: 대시보드도 빈 등급을 G 로 세지 않는다(집계·관계도 같은 규칙)."""

    ROWS = [
        {"content_ref": {"displayServiceName": "뉴스", "title": "a"},
         "quality_meta": {"finalGrade": "G", "reasons": []},
         "item_meta": {"entities": ["삼성전자"], "intent": [], "content_category": []}},
        {"content_ref": {"displayServiceName": "다음카페", "title": "b"},
         "quality_meta": {"finalGrade": "", "reasons": []},
         "item_meta": {"entities": ["총파업"], "intent": [], "content_category": []}},
        {"content_ref": {"displayServiceName": "티스토리", "title": "c"},
         "item_meta": {"entities": ["노조"], "intent": [], "content_category": []}},
    ]

    def test_aggregate_counts_empty_grade_as_none(self):
        from prism import dashboard as DB
        g = DB._aggregate(self.ROWS)["grades"]
        self.assertEqual((g["G"], g["none"]), (1, 2))

    def test_graph_node_grade_is_none_not_green(self):
        from prism import dashboard as DB
        nodes = DB._graph(self.ROWS)[0]
        grades = [n.get("grade") for n in nodes if n["kind"] == "content"]
        self.assertEqual(grades, ["G", "none", "none"])


if __name__ == "__main__":
    unittest.main()
