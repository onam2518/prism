"""PAST 로그 검증 도구(실험실 · 로그뷰어 · pastcheck) 테스트.

- 시연 세션 이벤트 → PAST 봉투 합성 · 판정 ①(형식) ②(중복) ③(체크리스트)
- 위반 예시 주입: 불합격(무효값·필수 누락) · 경고(명명·광고·중복) · 정보(권장 미수집)
serve 없이 스텁 _SV 로 모듈 단위 검증(스토어는 인메모리 KV · memfs 재사용).
"""
import unittest

from prism import memfs as MF
from prism import pastcheck as PC


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
            "item_meta": {"content_category": [cat], "intent": [intent], "summary": "요약",
                          "entities": []},
            "quality_meta": {"finalGrade": grade}}


class TestPastCheck(unittest.TestCase):
    def setUp(self):
        self._old = MF._SV
        MF._SV = _SV(rows=[_row("반도체 실적 심층 분석"),
                           _row("야구 개막전 하이라이트", cat="Sports", intent="흥미·화제")])

    def tearDown(self):
        MF._SV = self._old

    def test_empty_session(self):
        d = PC.logviewer_data(team="t1")
        self.assertEqual((d["logs"], d["n"], d["bad_on"]), ([], 0, False))
        self.assertEqual(d["behavior"], {"ops": 0, "logs": 0, "dup": 0})
        self.assertEqual(d["checklist"]["pass_rate"], 0)
        self.assertEqual(len(d["checklist"]["missing"]), 6)
        self.assertEqual(d["session"]["service_id"], "prism_lab")

    def test_demo_actions_all_pass_and_checklist_full(self):
        MF.demo_ops({"op": "event", "event": "impression", "idxs": [0, 1]}, team="t1")
        MF.demo_ops({"op": "event", "event": "search", "query": "반도체"}, team="t1")
        MF.demo_ops({"op": "event", "event": "click", "idx": 0}, team="t1")
        MF.demo_ops({"op": "event", "event": "read", "idx": 0, "dwell_sec": 50, "scroll_pct": 95}, team="t1")
        MF.demo_ops({"op": "event", "event": "react", "idx": 0, "emotion": "화나요"}, team="t1")
        MF.demo_ops({"op": "event", "event": "comment", "idx": 0, "text": "저점 같다"}, team="t1")
        d = PC.logviewer_data(team="t1")
        self.assertEqual(d["n"], 7)                       # 노출 2 + 검색·클릭·정독·반응·댓글
        self.assertTrue(all(l["verdict"] == "pass" for l in d["logs"]))
        self.assertEqual(d["behavior"], {"ops": 7, "logs": 7, "dup": 0})
        self.assertEqual(d["checklist"]["pass_rate"], 100)
        self.assertEqual(d["checklist"]["missing"], [])
        titles = [l["title"] for l in d["logs"]]
        self.assertTrue(any("[ViewImp]" in t for t in titles))
        self.assertTrue(any("Dislike" in t for t in titles))          # 화나요 = 부정 피드백
        fb = [l for l in d["logs"] if l["feedback"]]
        self.assertEqual(len(fb), 1)
        usage = [l for l in d["logs"] if "UsagePage" in l["title"]][0]
        self.assertIn(["usage.duration", "50000", "조건부"], usage["fields"])   # 초 → ms
        comment = [l for l in d["logs"] if "댓글등록" in l["title"]][0]
        self.assertNotIn("저점", str(comment["fields"]))   # 본문은 로그 미수집(입력 텍스트 금지)

    def test_inject_examples_and_clear(self):
        MF.demo_ops({"op": "event", "event": "read", "idx": 0, "dwell_sec": 40}, team="t1")
        d = PC.logviewer_ops({"op": "inject"}, team="t1")
        self.assertTrue(d["bad_on"])
        inj = [l for l in d["logs"] if l["injected"]]
        self.assertEqual(len(inj), 7)
        # 중복 쌍의 원본(앱 실행)은 정상이라 pass · 재전송본만 경고
        self.assertEqual({l["verdict"] for l in inj}, {"pass", "fail", "warn", "info"})
        msgs = " ".join(v[1] for l in inj for v in l["violations"])
        self.assertIn("무효값 전송: action_kind", msgs)
        self.assertIn("필수 필드 누락: page", msgs)
        self.assertIn("광고 계측 이벤트 혼입", msgs)
        self.assertIn("중복 전송", msgs)
        self.assertIn("명명 규칙 위반", msgs)
        self.assertIn("권장 필드 미수집: click.layer1", msgs)
        self.assertEqual(d["behavior"]["dup"], 1)
        self.assertTrue(all(l["verdict"] == "pass" for l in d["logs"] if not l["injected"]))
        d2 = PC.logviewer_ops({"op": "clear"}, team="t1")
        self.assertFalse(d2["bad_on"])
        self.assertFalse(any(l["injected"] for l in d2["logs"]))
        self.assertIn("지원하지", PC.logviewer_ops({"op": "hack"}, team="t1")["error"])

    def test_team_scope(self):
        MF.demo_ops({"op": "event", "event": "click", "idx": 0}, team="t1")
        self.assertEqual(PC.logviewer_data(team="t2")["n"], 0)   # 다른 팀은 빈 세션


if __name__ == "__main__":
    unittest.main()
