"""인텐트 라벨 위생: 하드코딩된 라벨 표가 현행 사전과 어긋나지 않는가.

배경 — 인텐트 값의 정본은 `prism/dictionaries.py` 하나인데, 표시·정규화용 대응표가
세 곳(토픽 앵글 · 데모 사용자말 · 콘텐츠 에이전트 표시)에 따로 있다. 2026-07-02 사전
개편 뒤 이 표들에 폐기 라벨(기획·심층 · 흥미·화제 · 의견·논평 …)이 그대로 남아
현행 값은 거의 안 잡히는 상태였다. 각 표를 현행/레거시로 분리했고, 이 테스트가
그 분리를 사전과 대조해 다시 어긋나는 것을 막는다.

  · 현행 표의 키는 전부 사전에 있어야 한다(오타·폐기값 유입 차단)
  · 레거시 표의 키는 사전에 없어야 한다(사전에 되살아나면 현행으로 옮기라는 신호)

추가로 핸드오프 번들이 범용②(형식·전달)를 동봉하는지, "포토" 트랙 표식이 PGC
폴백으로 남아 있는지(조사 결론 고정)를 함께 검증한다.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import io
import json
import os
import re
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _vocab():
    """현행 사전이 인정하는 인텐트 값 전량(전 출처 공통 68개 · 2026-09-22)."""
    from prism import dictionaries as D
    return set(D.intent_categories())


class _LabelTableBase(unittest.TestCase):
    def _assert_split(self, current, legacy, merged, where):
        vocab = _vocab()
        self.assertFalse(set(current) - vocab,
                         f"{where}: 사전에 없는 값이 현행 표에 있음 → {sorted(set(current) - vocab)}")
        self.assertFalse(set(legacy) & vocab,
                         f"{where}: 사전에 되살아난 값이 레거시 표에 남음 → {sorted(set(legacy) & vocab)}")
        self.assertTrue(current, f"{where}: 현행 표가 비었음")
        for k in list(current) + list(legacy):       # 병합본이 양쪽을 모두 커버(호출부 계약)
            self.assertIn(k, merged, f"{where}: 병합 누락 {k}")
        for k, v in current.items():                 # 현행이 레거시를 덮는다(같은 키 충돌 시)
            self.assertEqual(merged[k], v, f"{where}: 현행 값이 우선해야 함 {k}")


class TestTopicAngleMap(_LabelTableBase):
    """토픽 사건형 앵글 정규화(topic.ANGLE_MAP)."""

    def test_current_and_legacy_split(self):
        from prism import topic as T
        self._assert_split(T._ANGLE_CURRENT, T._ANGLE_LEGACY, T.ANGLE_MAP, "topic.ANGLE_MAP")
        self.assertEqual(set(T.ANGLE_MAP.values()), {"속보", "분석", "반응", "화제"})

    def test_angle_normalizes_both_generations(self):
        from prism import topic as T
        self.assertEqual(T._angle("심층 분석"), "분석")            # 현행 사전 값
        self.assertEqual(T._angle("기획·심층"), "분석")            # 과거 저장분(폐기 라벨)
        self.assertEqual(T._angle("팬덤·화제성"), "화제")
        self.assertEqual(T._angle("흥미·화제"), "화제")
        self.assertEqual(T._angle(""), "기타")                     # 인텐트 없음
        self.assertEqual(T._angle("정형정보"), "정형정보")         # 미분류 값은 원문 통과

    def test_current_covers_every_replaced_legacy_angle(self):
        """폐기 라벨이 가리키던 관점은 현행 사전 값으로도 도달 가능해야 한다(기능 후퇴 방지)."""
        from prism import topic as T
        for angle in set(T._ANGLE_LEGACY.values()):
            self.assertIn(angle, set(T._ANGLE_CURRENT.values()), angle)


class TestHandoffBundleDictionaries(unittest.TestCase):
    """핸드오프 번들 사전 동봉: 범용②가 빠지면 학습 재현 조건이 통째로 어긋난다."""

    def _bundle(self):
        import tempfile
        import time as _t
        from prism import serve
        from prism.store import Store, content_hash
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        content = {"displayServiceName": "뉴스", "title": "합의 콘텐츠", "subtitle": "", "body": "본문"}
        ch = content_hash(content)
        im = {"summary": "요약", "entities": [], "intent": ["포토·영상 중심"],
              "content_category": ["Sports"]}
        payload = {"quality_meta": {"review": "yellow", "finalGrade": "G", "reasons": []},
                   "item_meta": im, "content_ref": dict(content)}
        c = st._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,reasons,"
                  "item_meta,payload,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  (ch, "뉴스", "합의 콘텐츠", "G", "[]", json.dumps(im), json.dumps(payload), _t.time()))
        c.commit()
        now = _t.time()
        for i, (rv, note) in enumerate((("A", "근거 노트"), ("B", ""))):
            st.save_feedback(ch, "뉴스", "합의 콘텐츠", "good", "review", note, now + i, reviewer=rv)
        serve.build_golden_from_reviews(None)
        fname, blob = serve.handoff_bundle(None)
        self.assertTrue(fname, blob)
        return json.loads(zipfile.ZipFile(io.BytesIO(blob)).read("dictionaries.json"))

    def test_form_universal_and_defs_shipped(self):
        from prism import dictionaries as D
        d = self._bundle()
        self.assertEqual(d["intent_form_universal"], list(D.INTENT_FORM_UNIVERSAL))
        self.assertIn("포토·영상 중심", d["intent_form_universal"])     # 범용② 누락 회귀 가드
        self.assertEqual(d["intent_universal"], list(D.INTENT_CATEGORIES_UNIVERSAL))
        # 번들 사전만으로 라벨 공간 전량을 복원할 수 있어야 한다
        shipped = (set(d["intent_universal"]) | set(d["intent_form_universal"])
                   | set(d["intent_common"]))
        self.assertFalse(_vocab() - shipped, sorted(_vocab() - shipped))
        # 폐기 대응표도 동봉(과거 정답 라벨을 읽을 때 필요 · 자동 치환용 아님)
        self.assertEqual(set(d["intent_retired"]), set(D.INTENT_RETIRED))
        # 정의문(프롬프트 주입·검수 판단 근거)도 동봉
        self.assertIn("포토·영상 중심", d["intent_defs"])
        self.assertIn("의견·논쟁", d["intent_defs"])                    # 범용① 정의
        self.assertFalse(_vocab() - set(d["intent_defs"]))


class TestCandidatesIgnoreSource(unittest.TestCase):
    """2026-09-22 전환 고정: 출처가 무엇이든(트랙 표식 "포토"·빈 값 포함) 후보는 같은 68개다.

    종전에는 "포토"·"영상" 같은 인입 트랙 표식이 PGC 폴백으로 빠져 범용 18개만 받았다.
    그 폴백(그리고 서비스 키 매핑 자체)이 정책상 금지돼 사라졌다."""

    def test_candidate_set_is_the_common_68(self):
        from prism import dictionaries as D
        cats = D.intent_categories()
        self.assertEqual(len(cats), 68)
        self.assertEqual(len(set(cats)), 68)
        self.assertIn("속보·단신", cats)          # 옛 서비스 분기 값도 전 출처 후보
        self.assertIn("클립·하이라이트", cats)

    def test_service_key_mapping_is_gone(self):
        from prism import dictionaries as D
        self.assertFalse(hasattr(D, "_service_key"))
        self.assertFalse(hasattr(D, "INTENT_CATEGORIES_BY_SERVICE"))


if __name__ == "__main__":
    unittest.main()
