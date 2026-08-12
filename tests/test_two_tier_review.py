"""2층 검수: 최종검수자 역할(사람 단위) + 최종검수 큐(미확정분 편입/제외) +
학습 반영 후 미확정분 새 버전 자동 재실행(3-1).

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
계약: 큐 대상 = ① 의견 갈림(split) ② 정확 합의인데 분류 공백. 정상 확정 경로·수정 일방
합의·기초 표 부족·기확정 골든은 제외. 판정 저장은 final_verdicts(#173) 재사용.
재실행: rerun_unconfirmed 는 큐 대상만 · batch_budget_usd 상한 · learning_batch 가
final_rerun_after_batch(기본 켬)일 때 반영 직후 훅 호출.
"""
import json as _j
import os
import sys
import tempfile
import time as _t
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TwoTierBase(unittest.TestCase):
    def _with_store(self):
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve, st

    def _put_reviewed(self, st, title, verdicts, cats=("Sports",), grade="G"):
        from prism.store import content_hash
        content = {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": "본문 " + title}
        ch = content_hash(content)
        im = {"summary": title, "entities": [], "intent": [], "content_category": list(cats)}
        payload = {"quality_meta": {"review": "yellow", "finalGrade": grade, "reasons": []},
                   "item_meta": im,
                   "content_ref": dict(content, source_url="https://example.test/" + ch),
                   # 골드 문항이 모델·버전·검수티어·원문링크를 원본 행에서 실어 온다
                   # (없으면 출제 후보에서 빠진다 · reviewops._gold_candidates)
                   "trace": {"model": "m-test", "version": 2}}
        c = st._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,reasons,item_meta,payload,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (ch, "뉴스", title, grade, "[]", _j.dumps(im), _j.dumps(payload), _t.time()))
        c.commit()
        for rv, v in verdicts:
            st.save_feedback(ch, "뉴스", title, v, "review", "", _t.time(), reviewer=rv)
        return ch


class CfgMixin:
    def _isolate_cfg(self, payload):
        from prism import config as C
        p = os.path.join(tempfile.mkdtemp(), "config.json")
        open(p, "w", encoding="utf-8").write(_j.dumps(payload))
        orig = C.DEFAULT_CONFIG_PATH
        C.DEFAULT_CONFIG_PATH = p
        self.addCleanup(lambda: setattr(C, "DEFAULT_CONFIG_PATH", orig))


class TestReviewerRoles(TwoTierBase):
    def test_role_roundtrip(self):
        serve, _st = self._with_store()
        r = serve.set_reviewer_role("uid-1", "final", None)
        self.assertTrue(r["ok"])
        self.assertEqual(r["final_reviewers"], ["uid-1"])
        self.assertTrue(serve.is_final_reviewer("uid-1", None))
        self.assertFalse(serve.is_final_reviewer("uid-2", None))
        serve.set_reviewer_role("uid-1", "", None)               # 해제 = 기초 복귀
        self.assertEqual(serve.reviewer_roles(None), {})
        self.assertFalse(serve.set_reviewer_role("", "final")["ok"])


class TestFinalQueue(TwoTierBase):
    def test_queue_selection_rules(self):
        serve, st = self._with_store()
        ch_split = self._put_reviewed(st, "의견 갈림 건", [("A", "good"), ("B", "bad")])
        ch_nocat = self._put_reviewed(st, "분류 없는 합의", [("A", "good"), ("B", "good")], cats=())
        self._put_reviewed(st, "정상 확정 경로", [("A", "good"), ("B", "good")])          # 제외
        self._put_reviewed(st, "수정 일방 합의", [("A", "bad"), ("B", "bad")])            # 제외
        self._put_reviewed(st, "검수 없음", [])                                          # 제외
        q = serve.final_review_queue(None)
        got = {i["title"]: i["final_reason"] for i in q["items"]}
        self.assertEqual(got, {"의견 갈림 건": "의견 갈림", "분류 없는 합의": "분류 없음"})
        by = {i["title"]: i for i in q["items"]}
        self.assertEqual(by["의견 갈림 건"]["fb"]["good"], 1)     # 기초 의견 요약 동반
        self.assertEqual(by["의견 갈림 건"]["final"], "")
        self.assertIn("hash", by["의견 갈림 건"])
        # 최종판정 저장 → 큐에 상태 반영 · 리드 'good' 이면 골든 승격 후 큐에서 제거
        serve.set_final_verdict(ch_split, "good", by="리드", team=None)
        q2 = serve.final_review_queue(None)
        self.assertEqual({i["title"]: i["final"] for i in q2["items"]}.get("의견 갈림 건"), "good")
        row2 = {i["title"]: i for i in q2["items"]}["의견 갈림 건"]
        self.assertEqual(row2["final_by"], "리드")                # 목록 = 결정 현황판(누가 · 언제)
        self.assertGreater(float(row2["final_ts"]), 0)
        serve.build_golden_from_reviews(None)                    # 학습 반영 시 승격
        self.assertIn(ch_split, st.golden_hashes())
        q3 = serve.final_review_queue(None)
        self.assertNotIn("의견 갈림 건", [i["title"] for i in q3["items"]])   # 확정분은 큐에서 사라짐
        self.assertIn(ch_nocat, [i["hash"] for i in q3["items"]])           # 분류 공백은 잔류

    def test_no_grade_agreement_stays_in_queue(self):
        """정확 합의 + 분류 있음이라도 등급(G/R)이 비면 승격 게이트(build_golden_from_reviews
        의 no_grade)에 막힌다 — 큐가 '승격 예정'으로 오인해 건너뛰면 골든도 큐도 아닌 채
        영구 미확정으로 남는다(감사 2026-08-04)."""
        serve, st = self._with_store()
        ch = self._put_reviewed(st, "등급 없는 합의", [("A", "good"), ("B", "good")], grade="")
        q = serve.final_review_queue(None)
        got = {i["title"]: i["final_reason"] for i in q["items"]}
        self.assertEqual(got.get("등급 없는 합의"), "등급 없음")
        # 승격 게이트와 정합: 학습 반영을 돌려도 이 건은 골든이 되지 않는다(need_grade)
        r = serve.build_golden_from_reviews(None)
        self.assertNotIn(ch, st.golden_hashes())
        self.assertEqual(r.get("need_grade"), 1)
        # 등급을 채우면(합의+분류+등급) 정상 확정 경로 → 큐에서 빠진다
        serve.patch_content_meta(ch, {"finalGrade": "G"}, team=None, reviewer="리드")
        q2 = serve.final_review_queue(None)
        self.assertNotIn(ch, [i["hash"] for i in q2["items"]])


class TestRerunUnconfirmed(TwoTierBase, CfgMixin):
    def _stub_rerun(self, serve, cost=0.02):
        calls = []
        def fake(ch, model, team=None, row=None, force_quest=False):
            calls.append((ch, force_quest))
            return {"output": {"trace": {"cost_usd": cost}}}
        orig = serve.rerun_content
        serve.rerun_content = fake
        self.addCleanup(lambda: setattr(serve, "rerun_content", orig))
        return calls

    def test_targets_queue_only(self):
        serve, st = self._with_store()
        self._isolate_cfg({})
        ch_split = self._put_reviewed(st, "의견 갈림 건", [("A", "good"), ("B", "bad")])
        ch_nocat = self._put_reviewed(st, "분류 없는 합의", [("A", "good"), ("B", "good")], cats=())
        self._put_reviewed(st, "정상 확정 경로", [("A", "good"), ("B", "good")])          # 재실행 비대상
        calls = self._stub_rerun(serve)
        r = serve.rerun_unconfirmed(None)
        self.assertTrue(r["ok"])
        self.assertEqual(r["done"], 2)
        self.assertEqual(r["failed"], 0)
        self.assertEqual({c[0] for c in calls}, {ch_split, ch_nocat})
        self.assertTrue(all(fq for _, fq in calls))            # 퀘스트 가드 정당 우회 플래그
        self.assertAlmostEqual(r["spent_usd"], 0.04)

    def test_budget_cap_stops_remaining(self):
        serve, st = self._with_store()
        self._isolate_cfg({"batch_budget_usd": 0.02})          # 1건 비용으로 상한 도달
        self._put_reviewed(st, "갈림1", [("A", "good"), ("B", "bad")])
        self._put_reviewed(st, "갈림2", [("A", "good"), ("B", "bad")])
        calls = self._stub_rerun(serve, cost=0.02)
        r = serve.rerun_unconfirmed(None)
        self.assertEqual(len(calls), 1)                        # 상한 도달 → 남은 대상 중단
        self.assertEqual(r["done"], 1)

    def test_empty_queue_noop(self):
        serve, _st = self._with_store()
        self._isolate_cfg({})
        def boom(*a, **k):
            raise AssertionError("빈 큐에서 재실행 호출 금지")
        orig = serve.rerun_content
        serve.rerun_content = boom
        self.addCleanup(lambda: setattr(serve, "rerun_content", orig))
        r = serve.rerun_unconfirmed(None)
        self.assertEqual((r["done"], r["failed"], r["spent_usd"]), (0, 0, 0.0))


class TestBatchHook(TwoTierBase, CfgMixin):
    def _stub_batch_env(self):
        """learning_batch 를 스토어만으로 돌리는 최소 픽스처(delta 테스트와 동일 접근)."""
        from prism import learnops as LO
        from prism import prompts as PR
        old_learned = dict(PR.LEARNED)
        old_bm = PR.LEARNED_BY_MODEL
        self.addCleanup(lambda: (PR.LEARNED.update(old_learned), setattr(PR, "LEARNED_BY_MODEL", old_bm)))
        PR.LEARNED = {"extract": "", "analyze": "", "review": "", "judge": ""}
        PR.LEARNED_BY_MODEL = {}
        orig_e, orig_i = LO.eval_golden, LO.meta_compile_run
        LO.eval_golden = lambda team=None, model="", scope="all": {"ok": True, "grade_accuracy": 0.9, "evaluated": 10}
        LO.meta_compile_run = lambda team=None: {"ok": True, "results": {}}
        self.addCleanup(lambda: (setattr(LO, "eval_golden", orig_e), setattr(LO, "meta_compile_run", orig_i)))
        return LO

    def test_batch_runs_final_rerun_by_default(self):
        serve, _st = self._with_store()
        self._isolate_cfg({})                                   # final_rerun_after_batch 기본 True
        LO = self._stub_batch_env()
        cap = {}
        orig = serve.rerun_unconfirmed
        serve.rerun_unconfirmed = lambda team=None: cap.update(team=team) or {"ok": True, "done": 3, "failed": 0, "spent_usd": 0.01}
        self.addCleanup(lambda: setattr(serve, "rerun_unconfirmed", orig))
        rep = LO.learning_batch(None)
        self.assertTrue(rep["ok"])
        self.assertEqual((rep.get("final_rerun") or {}).get("done"), 3)     # 훅 결과가 회차 보고서에 동반
        self.assertIn("team", cap)

    def test_batch_skips_when_disabled(self):
        serve, _st = self._with_store()
        self._isolate_cfg({"final_rerun_after_batch": False})
        LO = self._stub_batch_env()
        def boom(team=None):
            raise AssertionError("끔 상태에서 재실행 호출 금지")
        orig = serve.rerun_unconfirmed
        serve.rerun_unconfirmed = boom
        self.addCleanup(lambda: setattr(serve, "rerun_unconfirmed", orig))
        rep = LO.learning_batch(None)
        self.assertIsNone(rep.get("final_rerun"))


class TestFinalGamification(TwoTierBase, CfgMixin):
    def _seed_golden(self, serve, st):
        """정상 확정 경로 1건을 골든으로 승격시켜 캘리브레이션 후보를 만든다."""
        ch = self._put_reviewed(st, "골든 확정 건", [("A", "good"), ("B", "good")])
        serve.build_golden_from_reviews(None)
        return ch

    def test_gold_calibration_injected_and_recorded(self):
        serve, st = self._with_store()
        self._isolate_cfg({})
        gold_h = self._seed_golden(serve, st)
        self._put_reviewed(st, "의견 갈림 건", [("A", "good"), ("B", "bad")])   # 실 항목이 있어야 출제
        q = serve.final_review_queue(None, reviewer="F")
        golds = [i for i in q["items"] if i["hash"].startswith("goldf:")]
        self.assertEqual(len(golds), 1)
        g = golds[0]
        self.assertEqual(g["final_reason"], "의견 갈림")                # 블라인드: 실 항목과 동일 외형
        self.assertEqual((g["fb"]["good"], g["fb"]["bad"]), (1, 1))
        parts = g["hash"].split(":")
        self.assertEqual(parts[2], gold_h)
        # 정답 응답 → gold_checks 분리 기록 · final_verdicts 무오염 · 같은 문항 재출제 없음
        expected = "good" if parts[1] == "ok" else "bad"
        r = serve.apply_gold_answer({"hash": g["hash"], "verdict": expected, "reviewer": "F", "_team": None})
        self.assertTrue(r["ok"])
        self.assertTrue(r["gold"]["correct"])
        self.assertEqual(serve.final_verdicts(None), {})
        q2 = serve.final_review_queue(None, reviewer="F")
        self.assertFalse([i for i in q2["items"] if i["hash"].startswith("goldf:")])

    def test_gold_check_config_off(self):
        serve, st = self._with_store()
        self._isolate_cfg({"final_gold_check": False})
        self._seed_golden(serve, st)
        self._put_reviewed(st, "의견 갈림 건", [("A", "good"), ("B", "bad")])
        q = serve.final_review_queue(None, reviewer="F")
        self.assertFalse([i for i in q["items"] if i["hash"].startswith("goldf:")])

    def test_final_mission_and_stats(self):
        serve, st = self._with_store()
        self._isolate_cfg({})
        ch = self._put_reviewed(st, "의견 갈림 건", [("A", "good"), ("B", "bad")])
        serve.set_reviewer_role("F", "final", None)
        self.assertNotIn("final1", [m["id"] for m in serve.mission_progress("A", None)])   # 기초에겐 미노출
        serve.set_final_verdict(ch, "good", by="F", team=None)
        ms = {m["id"]: m for m in serve.mission_progress("F", None)}
        self.assertTrue(ms["final1"]["completed"])
        fresh = serve._check_missions("F", None)
        self.assertIn("final1", [m["id"] for m in fresh])              # 달성 보상 1회
        self.assertNotIn("final1", [m["id"] for m in serve._check_missions("F", None)])   # 중복 보상 없음
        q = serve.final_review_queue(None)
        self.assertEqual((q["stats"]["total"], q["stats"]["good"], q["stats"]["bad"]), (1, 1, 0))
        self.assertEqual(q["stats"]["by"]["F"]["n"], 1)


class TestFinalAnswerEdit(TwoTierBase, CfgMixin):
    """정답 확정(고쳐서 편입): 최종검수자가 필드를 직접 고치면 그 수정본이 골든 정답이 된다."""

    def test_patch_grade_and_fields_update_row(self):
        serve, st = self._with_store()
        ch = self._put_reviewed(st, "고칠 건", [("A", "good"), ("B", "bad")])
        r = serve.patch_content_meta(ch, {"summary": "고친 요약", "finalGrade": "R"}, reviewer="F")
        self.assertTrue(r["ok"])
        row = next(x for x in serve.results_rows() if (x.get("content_ref") or {}).get("title") == "고칠 건")
        self.assertEqual((row.get("item_meta") or {}).get("summary"), "고친 요약")
        self.assertEqual((row.get("quality_meta") or {}).get("finalGrade"), "R")
        self.assertGreaterEqual(st.patches_today("F"), 1)          # 교정 이력(감사·미션) 기록

    def test_grade_only_patch_ok(self):
        serve, st = self._with_store()
        ch = self._put_reviewed(st, "등급만", [("A", "good"), ("B", "bad")])
        self.assertTrue(serve.patch_content_meta(ch, {"finalGrade": "R"}, reviewer="F")["ok"])
        self.assertFalse(serve.patch_content_meta("없는해시", {"finalGrade": "G"}, reviewer="F")["ok"])
        self.assertFalse(serve.patch_content_meta(ch, {"finalGrade": "X"}, reviewer="F")["ok"])   # 허용 밖 등급

    def test_correction_becomes_golden_expected(self):
        serve, st = self._with_store()
        self._isolate_cfg({})
        ch = self._put_reviewed(st, "의견 갈림 건", [("A", "good"), ("B", "bad")])
        self.assertIn(ch, [i["hash"] for i in serve.final_review_queue(None)["items"]])
        serve.patch_content_meta(ch, {"summary": "확정 요약", "content_category": ["News"],
                                      "finalGrade": "R"}, reviewer="리드")
        serve.set_final_verdict(ch, "good", by="리드", team=None)   # 고쳐서 편입
        serve.build_golden_from_reviews(None)
        g = next(x for x in st.get_golden(None) if x["content"].get("title") == "의견 갈림 건")
        self.assertEqual(g["expected"]["summary"], "확정 요약")      # 수정본이 곧 정답
        self.assertEqual(g["expected"]["content_category"], ["News"])
        self.assertEqual(g["expected"]["finalGrade"], "R")
        self.assertNotIn(ch, [i["hash"] for i in serve.final_review_queue(None)["items"]])   # 확정 후 큐에서 제거


if __name__ == "__main__":
    unittest.main()
