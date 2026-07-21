"""파일 기반 메모리(실험실 · memfs) 테스트.

- 경로 규칙 · 버전 토큰(동시 수정 보호) · 파일당 크기 제한 · 파일 수 제한
- 소비 시연(consume) → /topics/<주제>.md 자동 생성 · [observed] 누적 · 설명 줄 갱신
- 주입 미리보기(빈 상태 · 목록)
serve 없이 스텁 _SV 로 모듈 단위 검증(스토어는 인메모리 KV).
"""
import unittest

from prism import memfs as MF


class _Store:
    def __init__(self):
        self.reports = {}

    def save_report(self, kind, payload, team=None):
        self.reports[(kind, team)] = payload


class _SV:
    def __init__(self, rows=None):
        self.store = _Store()
        self.rows = rows or []

    def get_store(self):
        return self.store

    def _report_get(self, kind, team=None, default=None):
        return self.store.reports.get((kind, team), default)

    def results_rows(self, limit=5000, team=None):
        return self.rows


def _row(title, cat="Business and Finance", intent="기획·심층", grade="G"):
    return {"content_ref": {"title": title},
            "item_meta": {"content_category": [cat], "intent": [intent], "summary": "요약"},
            "quality_meta": {"finalGrade": grade}}


class TestMemfs(unittest.TestCase):
    def setUp(self):
        self._old = MF._SV
        MF._SV = _SV(rows=[_row("반도체 실적 심층 분석"),
                           _row("유통 불가 콘텐츠", grade="R"),
                           _row("야구 개막전 하이라이트", cat="Sports", intent="흥미·화제")])

    def tearDown(self):
        MF._SV = self._old

    # ── 경로 규칙 ──
    def test_valid_path(self):
        ok = ["profile.md", "/profile.md", "preferences.md", "topics/food.md",
              "areas/프리즘-런칭.md", "people/멘토.md"]
        for p in ok:
            self.assertIsNotNone(MF.valid_path(p), p)
        bad = ["", "profile.txt", "secret.md", "topics/../etc.md", "topics/a/b.md",
               "unknown/a.md", "topics/.md", "topics/한 칸띄움.md"]
        for p in bad:
            self.assertIsNone(MF.valid_path(p), p)

    # ── 빈 상태 · 주입 미리보기 ──
    def test_empty_state(self):
        d = MF.memory_data(team="t1")
        self.assertEqual(d["files"], [])
        self.assertIn("비어 있습니다", d["injection"])
        self.assertNotIn("contents", d)                  # 소비 카탈로그는 시연(demo) 소관

    # ── 전체 쓰기(생성 · 버전 토큰) ──
    def test_write_and_version_conflict(self):
        d = MF.memory_ops({"op": "write", "path": "topics/food.md",
                           "content": "---\n파일: /topics/food.md\n설명: 음식 취향\n---\n"}, team="t1")
        self.assertNotIn("error", d)
        f = d["files"][0]
        self.assertEqual((f["path"], f["ver"], f["desc"]), ("topics/food.md", 1, "음식 취향"))
        # 버전 불일치 → 충돌
        d2 = MF.memory_ops({"op": "write", "path": "topics/food.md", "content": "x", "ver": 9}, team="t1")
        self.assertIn("버전 충돌", d2["error"])
        # 버전 일치 → 덮어쓰기 · 버전 증가
        d3 = MF.memory_ops({"op": "write", "path": "topics/food.md", "content": "x", "ver": 1}, team="t1")
        self.assertEqual(d3["files"][0]["ver"], 2)

    def test_write_rejects_bad_path_and_size(self):
        self.assertIn("경로", MF.memory_ops({"op": "write", "path": "hack/../x.md", "content": ""}, team="t1")["error"])
        big = "a" * (MF.MAX_BYTES + 1)
        self.assertIn("크기 제한", MF.memory_ops({"op": "write", "path": "profile.md", "content": big}, team="t1")["error"])

    def test_max_files(self):
        old = MF.MAX_FILES
        MF.MAX_FILES = 1
        try:
            MF.memory_ops({"op": "write", "path": "profile.md", "content": "a"}, team="t1")
            d = MF.memory_ops({"op": "write", "path": "preferences.md", "content": "b"}, team="t1")
            self.assertIn("파일 수 제한", d["error"])
        finally:
            MF.MAX_FILES = old

    # ── 끝에 추가 · 삭제 ──
    def test_append_and_delete(self):
        MF.memory_ops({"op": "write", "path": "profile.md", "content": "머리"}, team="t1")
        d = MF.memory_ops({"op": "append", "path": "profile.md", "line": "- [stated] 야구를 좋아한다"}, team="t1")
        self.assertIn("- [stated] 야구를 좋아한다", d["files"][0]["content"])
        self.assertEqual(d["files"][0]["ver"], 2)
        self.assertEqual(d["wrote"]["line"], "- [stated] 야구를 좋아한다")
        d2 = MF.memory_ops({"op": "delete", "path": "profile.md"}, team="t1")
        self.assertEqual(d2["files"], [])
        self.assertIn("없습니다", MF.memory_ops({"op": "append", "path": "profile.md", "line": "x"}, team="t1")["error"])

    # ── 주입 미리보기 · 팀 격리 ──
    def test_injection_and_team_scope(self):
        MF.memory_ops({"op": "write", "path": "profile.md",
                       "content": "---\n파일: /profile.md\n설명: 기본 정보\n---\n"}, team="t1")
        inj = MF.memory_data(team="t1")["injection"]
        self.assertIn("- /profile.md · 기본 정보", inj)
        self.assertEqual(MF.memory_data(team="t2")["files"], [])   # 다른 팀은 빈 상태

    def test_unknown_op(self):
        self.assertIn("지원하지 않는", MF.memory_ops({"op": "hack"}, team="t1")["error"])


if __name__ == "__main__":
    unittest.main()


class TestDemoSession(unittest.TestCase):
    """소비 시연 세션(STEP 1~4): 이벤트 수집 → 실시간 측정 → 결론(실로직 재사용) → 리셋."""

    def setUp(self):
        self._old = MF._SV
        rows = [_row("반도체 실적 심층 분석"),
                _row("야구 개막전 하이라이트", cat="Sports", intent="흥미·화제"),
                _row("걸그룹 데뷔 무대", cat="Entertainment", intent="흥미·화제"),
                _row("금리 전망 해설", intent="분석·해설"),
                _row("유통 불가", grade="R"),
                _row("전기차 시장 분석", cat="Technology and Computing", intent="분석·해설")]
        MF._SV = _SV(rows=rows)

    def tearDown(self):
        MF._SV = self._old

    def test_empty_demo(self):
        d = MF.demo_data(team="t1")
        self.assertEqual(len(d["contents"]), 5)          # R 제외
        self.assertEqual(d["session"], {"events_n": 0, "impressions": 0, "consumed": 0})
        self.assertEqual(d["stream"], [])
        self.assertEqual(d["live"]["cats"], [])
        self.assertNotIn("conclusion", d)                # 소비 전에는 결론 없음
        self.assertTrue(d["suggests"])                   # 추천 유도 문구
        for s in d["suggests"]:                          # 사용자 언어만 · 내부 카테고리명 비노출
            self.assertNotIn("News and Politics", s)
            self.assertNotIn("Business", s)
        self.assertIn("경제·재테크 몰아보기", d["suggests"])
        self.assertEqual(d["contents"][0]["cat_ko"], "경제·재테크")

    def test_impression_batch_dedup(self):
        MF.demo_ops({"op": "event", "event": "impression", "idxs": [0, 1, 2]}, team="t1")
        d = MF.demo_ops({"op": "event", "event": "impression", "idxs": [1, 2, 3]}, team="t1")
        self.assertEqual(d["session"]["impressions"], 4)  # 중복 노출 무시

    def test_click_then_read_collapses_to_one_viewed(self):
        MF.demo_ops({"op": "event", "event": "click", "idx": 0}, team="t1")
        d = MF.demo_ops({"op": "event", "event": "read", "idx": 0, "dwell_sec": 50, "scroll_pct": 95}, team="t1")
        self.assertEqual(d["session"]["consumed"], 1)
        self.assertEqual(d["live"]["eng"]["views"], 1)
        self.assertEqual(d["live"]["eng"]["clicks"], 1)   # 클릭 병합
        self.assertIn("클릭가중 2.0", d["logic"])
        self.assertEqual(d["wrote"]["path"], "topics/business-and-finance.md")
        self.assertTrue(d["live"]["cats"][0]["name"].startswith("Business"))
        self.assertIn("[Usage] UsagePage", d["stream"][0])   # 콘솔형 로그 라인
        self.assertIn("dwell=50s", d["stream"][0])
        self.assertIn("conclusion", d)                   # 소비 즉시 실시간 결론

    def test_click_only_not_consumed(self):
        d = MF.demo_ops({"op": "event", "event": "click", "idx": 1}, team="t1")
        self.assertEqual(d["session"]["consumed"], 0)     # 클릭만으로는 소비 아님
        self.assertEqual(d["live"]["eng"]["views"], 0)

    def test_live_conclusion_low_data_provisional(self):
        d = MF.demo_ops({"op": "event", "event": "read", "idx": 0, "dwell_sec": 50, "scroll_pct": 95}, team="t1")
        c = d["conclusion"]
        self.assertTrue(c["persona"]["provisional"])      # 5건 미만 → 잠정
        self.assertIn("잠정", c["note"])
        self.assertEqual(len(c["chain"]), 1)
        self.assertIn("topics/business-and-finance.md", c["memory"]["files"][0])
        self.assertEqual(len(c["scenarios"]), 3)

    def test_live_conclusion_five_reads_not_provisional(self):
        for i in (0, 1, 2, 3, 5):
            MF.demo_ops({"op": "event", "event": "click", "idx": i}, team="t1")
            d = MF.demo_ops({"op": "event", "event": "read", "idx": i, "dwell_sec": 60, "scroll_pct": 95}, team="t1")
        c = d["conclusion"]
        self.assertFalse(c["persona"]["provisional"])     # 5건 이상 → 본판정
        self.assertEqual(d["session"]["consumed"], 5)
        self.assertEqual(len([x for x in c["chain"] if "클릭" in x["act"]]), 5)

    def test_search_understands_user_language_chip(self):
        d = MF.demo_ops({"op": "event", "event": "search", "query": "경제·재테크 몰아보기"}, team="t1")
        self.assertNotIn("error", d)
        self.assertIn("business-and-finance", MF.memory_data(team="t1")["files"][0]["path"])

    def test_search_records_stated_and_filters_topic(self):
        d = MF.demo_ops({"op": "event", "event": "search", "query": "반도체 심층 몰아보기"}, team="t1")
        self.assertIn("Search", d["logic"])
        self.assertIn("ViewSearchResults", d["logic"])
        self.assertIn('[Event] Search query=', d["stream"][0])
        body = MF.memory_data(team="t1")["files"][0]["content"]
        self.assertIn("[stated]", body)
        self.assertIn("반도체 심층 몰아보기", body)
        self.assertIn("business-and-finance", MF.memory_data(team="t1")["files"][0]["path"])
        self.assertIn("입력하세요", MF.demo_ops({"op": "event", "event": "search", "query": " "}, team="t1")["error"])

    def test_reset_keeps_memory_files(self):
        MF.demo_ops({"op": "event", "event": "read", "idx": 0, "dwell_sec": 40}, team="t1")
        self.assertEqual(len(MF.memory_data(team="t1")["files"]), 1)
        d = MF.demo_ops({"op": "reset"}, team="t1")
        self.assertEqual(d["session"]["events_n"], 0)
        self.assertEqual(len(MF.memory_data(team="t1")["files"]), 1)   # 메모리 파일은 유지

    def test_bad_inputs(self):
        self.assertIn("찾을 수 없습니다", MF.demo_ops({"op": "event", "event": "read", "idx": 99}, team="t1")["error"])
        self.assertIn("올바르지", MF.demo_ops({"op": "event", "event": "read", "idx": "x"}, team="t1")["error"])
        self.assertIn("지원하지", MF.demo_ops({"op": "event", "event": "hover", "idx": 0}, team="t1")["error"])
        self.assertIn("지원하지", MF.demo_ops({"op": "hack"}, team="t1")["error"])

    def test_react_records_observed_and_custom_props_logic(self):
        MF.demo_ops({"op": "event", "event": "click", "idx": 0}, team="t1")
        d = MF.demo_ops({"op": "event", "event": "react", "idx": 0, "emotion": "화나요"}, team="t1")
        self.assertIn("Custom Properties", d["logic"])
        self.assertIn("[Event] Like emotion='화나요'", d["stream"][0])
        body = MF.memory_data(team="t1")["files"][0]["content"]
        self.assertIn("[observed]", body)
        self.assertIn("반응 '화나요'", body)
        self.assertIn("지원하지 않는 반응", MF.demo_ops({"op": "event", "event": "react", "idx": 0, "emotion": "글쎄요"}, team="t1")["error"])

    def test_comment_recorded_as_stated(self):
        d = MF.demo_ops({"op": "event", "event": "comment", "idx": 0, "text": "반도체는 지금이 저점 같다"}, team="t1")
        self.assertIn("WriteComment", d["logic"])
        body = MF.memory_data(team="t1")["files"][0]["content"]
        self.assertIn('- [stated]', body)
        self.assertIn("반도체는 지금이 저점 같다", body)
        self.assertNotIn("소비 기록 · 1회", body.splitlines()[2])   # 댓글은 [observed] 횟수에 미포함
        self.assertIn("내용을 입력", MF.demo_ops({"op": "event", "event": "comment", "idx": 0, "text": " "}, team="t1")["error"])

    def test_reactions_boost_preference_and_intensity(self):
        # 읽지 않은 콘텐츠라도 반응·댓글이 호응 가중으로 선호에 합산된다
        MF.demo_ops({"op": "event", "event": "react", "idx": 1, "emotion": "좋아요"}, team="t1")
        d = MF.demo_ops({"op": "event", "event": "comment", "idx": 1, "text": "역전승 최고"}, team="t1")
        resp = d["live"]["resp"]
        self.assertEqual((resp["reacts"], resp["comments"], resp["pos"], resp["neg"]), (1, 1, 1, 0))
        self.assertEqual(resp["boost"], 2.5)              # 반응 1.0 + 댓글 1.5
        self.assertEqual(d["live"]["cats"][0]["name"], "Sports")
        self.assertEqual(d["live"]["cats"][0]["w"], 2.5)
        self.assertIn("흥미·화제", d["live"]["intensity"])  # 강도(판정 입력)에도 반영
        self.assertIn("호응 가중 +1.5", d["logic"])
        d2 = MF.demo_ops({"op": "event", "event": "react", "idx": 0, "emotion": "화나요"}, team="t1")
        self.assertEqual(d2["live"]["resp"]["neg"], 1)    # 부정 감정 분포
        self.assertEqual(d2["live"]["resp"]["emos"]["화나요"], 1)
        # 같은 콘텐츠에 감정을 바꿔 누르면 교체(중복 가중 없음)
        d3 = MF.demo_ops({"op": "event", "event": "react", "idx": 0, "emotion": "좋아요"}, team="t1")
        self.assertEqual(d3["live"]["resp"]["reacts"], 2)          # idx1 + idx0 각 1건
        self.assertEqual(d3["live"]["resp"]["emos"].get("화나요"), None)
        self.assertEqual(d3["live"]["resp"]["emos"]["좋아요"], 2)

    def test_react_comment_in_conclusion_chain(self):
        MF.demo_ops({"op": "event", "event": "read", "idx": 0, "dwell_sec": 50, "scroll_pct": 95}, team="t1")
        MF.demo_ops({"op": "event", "event": "react", "idx": 0, "emotion": "좋아요"}, team="t1")
        c = MF.demo_ops({"op": "event", "event": "comment", "idx": 0, "text": "좋은 분석"}, team="t1")["conclusion"]
        acts = [x["act"] for x in c["chain"]]
        self.assertTrue(any("반응 '좋아요'" in a for a in acts))
        self.assertTrue(any("댓글" in a for a in acts))
        self.assertTrue(any("[stated]" in x["measure"] for x in c["chain"]))
        self.assertTrue(any(b[0] == "호응" and "가중 +2.5" in b[1] for b in c["basis"]))
