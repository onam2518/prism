"""코드 감사 2026-08-11 · 검수운영·대시보드·알림 수정 회귀 테스트.

여기서 지키는 계약(전부 재현된 결함의 재발 방지):
- [O1] 대시보드 롤업은 **콘텐츠 단위**로 센다 — 상위 집계 v == 드릴다운 n · pct ≤ 100
       (종전: 태그 단위 누적이라 하위 분류가 여럿이면 한 건이 여러 번 계수 · pct 200% 관측)
- [P3] 결과 CSV 는 상한을 넘겨도 **조용히 잘리지 않는다**(파일 첫 줄에 잘림 고지)
- [D1] 정체 회수는 **슬롯 단위** — 방금 배정된 슬롯은 같은 사람 몫이라도 회수하지 않는다
       (부재·비활성 인원 몫은 나이와 무관하게 전량 회수)
- [D2] 자동 운영 회차 키는 **선점**한다 — 동시 호출에도 웨이브는 1회 · dry-run 은 회차를 안 쓴다
- [D3] 본인 확인(/crew-confirm)은 관리자 전용 HR 필드(rate_override·leave_*)를 받지 않는다
- [D4] 자동 인입의 건별 추출 실패는 계수·표면화된다(failed · 메시지 · last_ok=False · 실패 원장)
- [D7] 운영 파라미터는 허용 범위로 클램프하고 사유를 응답에 담는다(조용한 무시 금지)
- [O4] 비용 알림 중복방지는 **팀 스코프**(하루에 팀 1곳만 통지되던 문제)
- [O5] 웹훅 발송이 실패하면 쿨다운을 소진하지 않는다(60초 뒤 재시도 가능)

실행: python3 -m pytest tests/test_audit_ops.py -q  (stdlib unittest · 의존성 0)
외부 발송은 전부 가짜로 대체한다 — 이 테스트는 네트워크를 쓰지 않는다.
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class OpsBase(unittest.TestCase):
    def _serve(self):
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)
        serve._agg_bump()
        return serve

    def _row(self, title, cats=(), intents=(), reasons=(), grade="G"):
        content = {"displayServiceName": "svc", "title": title, "subtitle": "", "body": "본문 " + title}
        out = {"item_meta": {"content_category": list(cats), "intent": list(intents),
                             "entities": ["e1"], "summary": "요약"},
               "quality_meta": {"finalGrade": grade, "reasons": list(reasons)},
               "trace": {"model": "m1"}}
        return (content, out)


# ── [O1] 대시보드 롤업 = 콘텐츠 단위 ────────────────────────────────────────
class TestRollupCountsContents(OpsBase):
    """한 콘텐츠에 같은 Tier1 의 하위 분류가 여러 개 붙는 것은 정상 입력이다(스키마: 콘텐츠
    단위 N개·복수 매핑). 그때 상위 집계만 태그 단위로 세면 같은 응답 안에서 분자(태그)·
    분모(행 수)·드릴다운(콘텐츠)이 서로 다른 단위가 된다 — 실측 pct 200% · 집계 4 vs 드릴 1."""

    def _fixture(self, serve):
        st = serve._STORE
        st.save_many([self._row("A", ["뉴스/정치", "뉴스/경제"], ["정보"], ["사유1", "사유1"]),
                      self._row("B", ["뉴스/사회"], ["정보"], ["사유1"]),
                      self._row("C", ["쇼핑/패션"], ["구매", "구매"], [])],
                     run_id="r1", source="test")
        serve._agg_bump()

    def test_rollup_matches_drilldown(self):
        from prism import dashops
        serve = self._serve()
        self._fixture(serve)
        d = dashops._dashboard_compute()
        for kind, key in (("category", "categories"), ("intent", "intents"), ("reason", "qualityReasons")):
            for e in d[key]:
                got = dashops.drill_contents(kind, e["k"])["n"]
                self.assertEqual(e["v"], got, f"{kind}={e['k']}: 집계 {e['v']} vs 드릴 {got}")

    def test_multi_tag_content_counted_once(self):
        from prism import dashops
        serve = self._serve()
        self._fixture(serve)
        d = dashops._dashboard_compute()
        cats = {e["k"]: e["v"] for e in d["categories"]}
        self.assertEqual(cats["뉴스"], 2)            # A·B 두 건(A 의 하위 2개는 1건으로)
        self.assertEqual(cats["쇼핑"], 1)
        self.assertEqual({e["k"]: e["v"] for e in d["intents"]}["구매"], 1)   # 중복 태그 1건
        self.assertEqual({e["k"]: e["v"] for e in d["qualityReasons"]}["사유1"], 2)

    def test_pct_never_exceeds_100(self):
        """분자가 태그 단위면 백분율이 100% 를 넘어 분포로 읽을 수 없게 된다."""
        from prism import dashops
        serve = self._serve()
        serve._STORE.save_many(
            [self._row("A", ["뉴스/정치", "뉴스/경제", "뉴스/사회", "뉴스/국제"], []),
             self._row("B", ["쇼핑/패션"], [])], run_id="r2", source="test")
        serve._agg_bump()
        d = dashops._dashboard_compute()
        for e in d["categories"]:
            self.assertLessEqual(e["pct"], 100, e)
            self.assertEqual(e["v"], dashops.drill_contents("category", e["k"])["n"])

    def test_report_entity_categories_match_graph(self):
        """통합 리포트(dashboard._aggregate)의 카테고리 막대도 그래프(_graph)와 같은 단위."""
        from prism import dashboard as DASH
        rows = [{"item_meta": {"content_category": ["뉴스/정치", "뉴스/경제"], "entities": []},
                 "quality_meta": {"finalGrade": "G"}, "content_ref": {"displayServiceName": "s"}},
                {"item_meta": {"content_category": ["쇼핑/패션"], "entities": []},
                 "quality_meta": {"finalGrade": "G"}, "content_ref": {"displayServiceName": "s"}}]
        agg = dict(DASH._aggregate(rows)["entity_categories"])
        self.assertEqual(agg[DASH.tier1_remap("뉴스")], 1)      # 하위 2개 → 콘텐츠 1건
        self.assertEqual(agg[DASH.tier1_remap("쇼핑")], 1)


# ── [P3] CSV 내보내기: 잘렸으면 파일에 적는다 ───────────────────────────────
class TestCsvTruncationIsVisible(OpsBase):
    def test_truncated_export_says_so(self):
        from prism import dashops
        serve = self._serve()
        serve._STORE.save_many([self._row(f"글{i}", ["뉴스/정치"], []) for i in range(3)],
                               run_id="r3", source="test")
        serve._agg_bump()
        with mock.patch.object(dashops, "CSV_MAX_ROWS", 2):
            body = dashops.build_results_csv().decode("utf-8")
        lines = [ln for ln in body.splitlines() if ln.strip()]
        self.assertIn("내보내기 상한", lines[1])          # 헤더 바로 다음 = 파일 열자마자 보인다
        self.assertEqual(len(lines), 1 + 1 + 2)          # 헤더 + 고지 + 상한 만큼의 데이터

    def test_full_export_has_no_notice(self):
        from prism import dashops
        serve = self._serve()
        serve._STORE.save_many([self._row(f"글{i}", ["뉴스/정치"], []) for i in range(3)],
                               run_id="r4", source="test")
        serve._agg_bump()
        body = dashops.build_results_csv().decode("utf-8")
        self.assertNotIn("내보내기 상한", body)
        self.assertEqual(len([ln for ln in body.splitlines() if ln.strip()]), 4)


# ── 검수운영(crewops) 공통 픽스처 ───────────────────────────────────────────
class CrewBase(OpsBase):
    def _content(self, st, h, ts=None):
        payload = {"quality_meta": {"review": "yellow", "confidence": 0.5},
                   "content_ref": {"title": h, "body": "b"}}
        c = st._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,payload,created_at) "
                  "VALUES(?,?,?,?,?,?)", (h, "s", h, "G", json.dumps(payload), ts or time.time()))
        c.commit()

    def _h(self, i):
        return "%016x" % i

    def _backdate(self, st, h, days):
        c = st._conn()
        c.execute("UPDATE assignments SET ts=? WHERE content_hash=?", (time.time() - days * 86400, h))
        c.commit()


# ── [D1] 정체 회수는 슬롯 단위 ──────────────────────────────────────────────
class TestRebalanceSlotAge(CrewBase):
    """종전에는 '정체된 사람'의 미완료 배정을 전량 회수했다 — 10일 묵은 1건 때문에 몇 초 전
    배정된 2건까지 남에게 넘어가고, set_assignees 가 DELETE+INSERT 라 진짜 정체분의 정체일도
    0 으로 리셋됐다(다음 점검에서 '방금 배정'으로 보인다)."""

    def _fixture(self, serve):
        st = serve._STORE
        for n, ch in (("alice", "boksil"), ("bob", "ddakji")):
            st.set_reviewer(n, n, ch)
        for i in (1, 2, 3):
            self._content(st, self._h(i))
            st.set_assignees(self._h(i), ["alice"])
        self._backdate(st, self._h(1), 10)          # 1건만 10일 전 배정
        serve._agg_bump()
        return st

    def test_only_the_stalled_slot_moves(self):
        serve = self._serve()
        self._fixture(serve)
        r = serve.CRW.rebalance(None)
        self.assertEqual([m["hash"] for m in r["moves"]], [self._h(1)])
        self.assertEqual(r["n"], 1)

    def test_unavailable_member_loses_all_slots(self):
        """부재·비활성 인원 몫은 슬롯 나이와 무관하게 전량 회수한다 — 그 사람은 이번 주에
        아무것도 못 보므로, 방금 배정이라고 남겨 두면 그 콘텐츠가 통째로 멈춘다."""
        serve = self._serve()
        self._fixture(serve)
        serve.CRW.set_profile("alice", {"status": "inactive"})
        serve._agg_bump()
        r = serve.CRW.rebalance(None)
        self.assertEqual(sorted(m["hash"] for m in r["moves"]),
                         [self._h(1), self._h(2), self._h(3)])

    def test_store_without_assign_times_keeps_old_behavior(self):
        """배정 시각을 못 주는 스토어(assignment_times 미지원)에서는 슬롯 나이 검사를 건너뛰고
        종전 동작(사람 단위)을 그대로 쓴다 — 시각이 없으면 정체 자체를 판정할 수 없으므로
        회수는 '자리에 없는 사람' 기준으로만 일어난다."""
        serve = self._serve()
        st = self._fixture(serve)
        orig = st.assignments_snapshot
        st.assignments_snapshot = lambda team=None: (orig(team)[0], {})
        self.addCleanup(lambda: setattr(st, "assignments_snapshot", orig))
        serve._agg_bump()
        self.assertEqual(serve.CRW.rebalance(None)["n"], 0)      # 정체 근거 없음 → 손대지 않는다
        serve.CRW.set_profile("alice", {"status": "inactive"})
        serve._agg_bump()
        self.assertEqual(serve.CRW.rebalance(None)["n"], 3)      # 자리에 없는 사람 몫은 전량 회수


# ── [D2] 자동 운영 회차 키 선점 ─────────────────────────────────────────────
class TestAutoTickClaimsCycle(CrewBase):
    """화면의 '지금 실행'(POST /crew-auto)과 크론(prism.crewbot · 10:00 KST)이 같은 시각에
    겹치면 웨이브가 두 번 발행됐다. set_wave 는 같은 기한이면 계획을 합산하므로 계획이 실제
    배정의 2배가 되고, 감사 원장에도 같은 배분이 두 줄 남는다."""

    def _fixture(self, serve, n=6):
        st = serve._STORE
        for name, ch in (("alice", "boksil"), ("bob", "ddakji"), ("carol", "daesik")):
            st.set_reviewer(name, name, ch)
        for i in range(1, n + 1):
            self._content(st, self._h(i))
        serve.CRW.set_settings({"auto_wave": 1, "wave_min_reviewers": 2})
        serve._agg_bump()
        return st

    def _assign_log(self, serve):
        return [(e["mode"], e["n"]) for e in
                ((serve._report_get("assign_log", None, {}) or {}).get("items") or [])]

    def test_concurrent_ticks_issue_one_wave(self):
        serve = self._serve()
        st = self._fixture(serve)
        out, bar = [], threading.Barrier(2)

        def run():
            bar.wait()
            try:
                out.append(serve.CRW.auto_tick(None))
            except Exception as e:                   # 예외도 결과로 남겨 원인을 보이게
                out.append({"exc": repr(e)})
        ths = [threading.Thread(target=run) for _ in range(2)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        self.assertEqual([o for o in out if "exc" in o], [])
        waves = [o["wave"] for o in out if o.get("wave")]
        self.assertEqual(len(waves), 1, out)                     # 배분은 한 호출에서만
        slots = sum(len(a["reviewers"]) for a in st.assignees(None).values())
        planned = sum((serve.CRW.wave(None).get("plan") or {}).values())
        self.assertEqual(planned, slots)                         # 계획 = 실제(2배 부풀지 않는다)
        self.assertEqual(self._assign_log(serve).count(("여력만큼 나눔", 6)), 1)

    def test_dry_run_does_not_consume_the_cycle(self):
        serve = self._serve()
        st = self._fixture(serve)
        r0 = serve.CRW.auto_tick(None, apply=False)
        self.assertTrue(r0["wave"]["n"])
        self.assertEqual(st.assignees(None), {})
        self.assertEqual(serve.CRW.auto_state(None), {})
        r1 = serve.CRW.auto_tick(None)               # 미리보기 뒤에도 실제 실행은 살아 있다
        self.assertTrue(r1["wave"]["ok"])
        self.assertTrue(st.assignees(None))

    def test_second_tick_in_same_cycle_is_noop(self):
        serve = self._serve()
        st = self._fixture(serve)
        serve.CRW.auto_tick(None)
        n1 = len(st.assignees(None))
        self.assertIsNone(serve.CRW.auto_tick(None)["wave"])
        self.assertEqual(len(st.assignees(None)), n1)

    def test_failed_wave_can_retry_within_the_cycle(self):
        """선점을 쓴 실행이 실패하면(그 순간 배정 가능한 사람 0명) 다음 점검이 다시 시도한다 —
        '1회 보장' 때문에 그 주 자동 배분이 통째로 사라지면 안 된다."""
        serve = self._serve()
        st = self._fixture(serve)
        for rid in ("alice", "bob", "carol"):
            serve.CRW.set_profile(rid, {"status": "inactive"})
        serve._agg_bump()
        r1 = serve.CRW.auto_tick(None)
        self.assertFalse(r1["wave"]["ok"])                       # 배정 가능한 사람이 없다
        self.assertEqual(st.assignees(None), {})
        for rid in ("alice", "bob", "carol"):
            serve.CRW.set_profile(rid, {"status": "active"})
        serve._agg_bump()
        r2 = serve.CRW.auto_tick(None)                           # 같은 사이클이지만 재시도된다
        self.assertTrue(r2["wave"]["ok"])
        self.assertTrue(st.assignees(None))

    def test_crash_during_wave_does_not_lose_the_cycle(self):
        """배분 도중 죽어도(원격 쓰기 실패 등) 그 주 자동 배분이 통째로 사라지면 안 된다 —
        회차 선점만 소진하고 아무것도 안 나가는 상태가 가장 나쁘다."""
        serve = self._serve()
        st = self._fixture(serve)
        with mock.patch.object(serve.CRW, "plan_distribute",
                               side_effect=RuntimeError("원격 쓰기 실패")):
            with self.assertRaises(RuntimeError):
                serve.CRW.auto_tick(None)
        self.assertEqual(st.assignees(None), {})
        r = serve.CRW.auto_tick(None)
        self.assertTrue(r["wave"]["ok"])
        self.assertTrue(st.assignees(None))


# ── [D3] 본인 확인은 관리자 필드를 받지 않는다 ──────────────────────────────
class TestConfirmWeekWhitelist(CrewBase):
    def test_admin_only_fields_are_ignored(self):
        serve = self._serve()
        serve._STORE.set_reviewer("alice", "alice", "boksil")
        r = serve.CRW.confirm_week("alice", {"hours_per_week": 5, "rate_override": 600,
                                             "leave_from": "2020-01-01", "leave_to": "2099-01-01"})
        p = r["profile"]
        self.assertEqual(p["hours_per_week"], 5)                 # 본인이 고칠 수 있는 값은 반영
        self.assertEqual(p["rate_override"], 0)                  # 처리율 수동 상한 = 관리자 전용
        self.assertEqual((p["leave_from"], p["leave_to"]), ("", ""))
        self.assertTrue(serve.CRW.profiles()["alice"]["confirmed"])

    def test_admin_route_still_sets_them(self):
        serve = self._serve()
        serve._STORE.set_reviewer("alice", "alice", "boksil")
        p = serve.CRW.set_profile("alice", {"rate_override": 120, "leave_from": "2026-08-01"})["profile"]
        self.assertEqual(p["rate_override"], 120)
        self.assertEqual(p["leave_from"], "2026-08-01")


# ── [D7] 운영 파라미터 범위 검증 ────────────────────────────────────────────
class TestSettingsRange(CrewBase):
    def test_out_of_range_is_clamped_and_explained(self):
        serve = self._serve()
        st = serve._STORE
        st.set_reviewer("alice", "alice", "boksil")
        cfg = serve.CRW.set_settings({"buffer": -1.0, "stale_days": -5, "rate_cap_per_hour": 0})
        self.assertEqual(cfg["buffer"], 0.1)
        self.assertEqual(cfg["stale_days"], 1)
        self.assertEqual(cfg["rate_cap_per_hour"], 1)
        self.assertEqual(len(cfg["_notes"]), 3)                  # 조용한 무시 금지
        serve._agg_bump()
        m = serve.CRW._crew_compute()["members"][0]
        self.assertGreaterEqual(m["weekly_capacity"], 0)         # 주간 캐파가 음수가 되지 않는다

    def test_unknown_and_unparsable_keys_are_reported(self):
        serve = self._serve()
        cfg = serve.CRW.set_settings({"몰라": 1, "stale_days": "숫자아님"})
        self.assertNotIn("몰라", cfg)
        self.assertEqual(cfg["stale_days"], serve.CRW.DEFAULT_SETTINGS["stale_days"])
        self.assertEqual(len(cfg["_notes"]), 2)

    def test_every_setting_has_a_range(self):
        """새 파라미터를 추가하면서 범위를 빠뜨리면 다시 음수가 저장된다."""
        from prism import crewops as CRW
        self.assertEqual(set(CRW.SETTINGS_RANGE), set(CRW.DEFAULT_SETTINGS))
        for k, (lo, hi) in CRW.SETTINGS_RANGE.items():
            self.assertLessEqual(lo, CRW.DEFAULT_SETTINGS[k], k)   # 기본값은 범위 안이어야 한다
            self.assertLessEqual(CRW.DEFAULT_SETTINGS[k], hi, k)


# ── [D4] 자동 인입: 건별 추출 실패를 표면화 ─────────────────────────────────
class _FakeLLM:
    def __init__(self, model="real-m", mock=False):
        self.model = model
        self.mock = mock


class TestIngestFailureIsVisible(unittest.TestCase):
    """종전에는 건별 예외를 통째로 삼키면서 실패 건도 done 에 세고 last_ok=True 였다.
    화면엔 '제외 0'(중복 제외 0)이 찍혀 유실이 오히려 부정됐다 — 5분마다 초록불."""

    def _serve(self):
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._INGEST_STATE.clear()
        orig_mock = serve.Handler.server_mock
        serve.Handler.server_mock = True
        self.addCleanup(serve._INGEST_STATE.clear)
        self.addCleanup(lambda: setattr(serve.Handler, "server_mock", orig_mock))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)
        return serve

    def _run(self, serve, n=5, boom=(2, 4), mock=True):
        from prism import ingestops as IO
        from prism import pipeline as PIPE
        rows = [{"제목": f"제목{i}", "본문": f"본문{i}", "콘텐츠 그룹": "뉴스"} for i in range(n)]
        orig_fetch, orig_extract = IO._fetch_records, PIPE.extract
        IO._fetch_records = lambda *a, **k: (rows, None)
        self.addCleanup(lambda: setattr(IO, "_fetch_records", orig_fetch))
        seen = {"n": 0}

        def flaky(c, llm, **kw):
            seen["n"] += 1
            if seen["n"] in boom:
                raise RuntimeError("모의 추출 실패(스키마 오류)")
            return {"item_meta": {"summary": "s"}, "quality_meta": {"finalGrade": "G"},
                    "trace": {"model": "real-m", "cost_usd": 0.0, "by_call": {}}}
        PIPE.extract = flaky
        self.addCleanup(lambda: setattr(PIPE, "extract", orig_extract))
        orig_llm = serve.make_text_llm
        serve.make_text_llm = lambda cfg, m: _FakeLLM(mock=mock)
        self.addCleanup(lambda: setattr(serve, "make_text_llm", orig_llm))
        r = IO.ingest_run_source({"id": "s1", "name": "테스트소스",
                                  "endpoint": "https://example.com/api"}, trigger="manual")
        return r, IO._INGEST_STATE["s1"]

    def test_failures_are_counted_and_surfaced(self):
        serve = self._serve()
        r, state = self._run(serve)
        self.assertEqual(r["failed"], 2)
        self.assertEqual(r["extracted"], 3)
        self.assertIn("스키마 오류", r["fail_error"])
        self.assertEqual(state["failed"], 2)
        self.assertFalse(state["last_ok"])                       # 유실이 있으면 초록불이 아니다
        self.assertIn("추출 실패 2건", state["last_msg"])

    def test_clean_run_stays_green(self):
        serve = self._serve()
        r, state = self._run(serve, boom=())
        self.assertEqual(r["failed"], 0)
        self.assertTrue(state["last_ok"])
        self.assertNotIn("추출 실패", state["last_msg"])
        self.assertEqual(state["done"], state["total"])

    def test_failure_reaches_the_fail_ledger(self):
        """실호출(mock=False)이면 트리아지 원장에도 남는다 — 원인 추적이 가능해야 한다."""
        serve = self._serve()
        self._run(serve, mock=False)
        fail = serve.fail_rollup_data(None, days=7)
        self.assertEqual(fail["total"], 2)
        self.assertEqual([e["k"] for e in fail["by_kind"]], ["extract"])
        self.assertEqual(len(fail["recent"]), 2)


# ── [O4][O5] 운영 알림 ──────────────────────────────────────────────────────
class _Clock:
    """alerts 모듈이 보는 시간(테스트에서만 앞으로 감는다). 실제 sleep 은 쓰지 않는다."""

    def __init__(self, t0):
        self.now = t0

    def time(self):
        return self.now


class TestAlerts(unittest.TestCase):
    def setUp(self):
        from prism import alerts as AL
        self.AL = AL
        AL._LAST_SENT.clear()
        AL._FAILS.clear()
        AL._COST_ALERTED.clear()
        self.addCleanup(AL._LAST_SENT.clear)
        self.addCleanup(AL._FAILS.clear)
        self.addCleanup(AL._COST_ALERTED.clear)
        self._env = {k: os.environ.get(k) for k in ("PRISM_ALERT_WEBHOOK", "PRISM_ALERT_COST_USD")}
        os.environ["PRISM_ALERT_WEBHOOK"] = "https://hooks.slack.example/x"   # 아래에서 가로챈다
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _outbound(self, boom=False):
        """실제 요청 금지 — urlopen 을 가짜로 대체하고 호출 횟수만 센다."""
        calls = []

        def fake(req, timeout=5):
            calls.append(req)
            if boom:
                raise OSError("net down")
            return mock.MagicMock()
        p = mock.patch.object(self.AL.urllib.request, "urlopen", side_effect=fake)
        p.start()
        self.addCleanup(p.stop)
        return calls

    def test_failed_send_does_not_burn_the_cooldown(self):
        """[O5] 웹훅 순단 1회로 장애 알림이 1시간(비용은 6시간) 침묵하면 안 된다."""
        clock = _Clock(time.time())
        with mock.patch.object(self.AL, "time", clock):
            calls = self._outbound(boom=True)
            self.assertFalse(self.AL.notify("fail_spike", "1차", cooldown_sec=3600))
            self.assertFalse(self.AL.notify("fail_spike", "2차(60초 전)", cooldown_sec=3600))
            self.assertEqual(len(calls), 1)                      # 60초 안에는 재시도 폭주 방지
            clock.now += 61
            self.assertFalse(self.AL.notify("fail_spike", "3차(60초 뒤)", cooldown_sec=3600))
            self.assertEqual(len(calls), 2)                      # 실패했으니 재시도가 가능해야 한다

    def test_successful_send_keeps_the_cooldown(self):
        clock = _Clock(time.time())
        with mock.patch.object(self.AL, "time", clock):
            calls = self._outbound()
            self.assertTrue(self.AL.notify("fail_spike", "1차", cooldown_sec=3600))
            clock.now += 600
            self.assertFalse(self.AL.notify("fail_spike", "쿨다운 안", cooldown_sec=3600))
            self.assertEqual(len(calls), 1)

    def test_cost_alert_is_per_team(self):
        """[O4] 비용 원장이 팀 스코프라 임계 초과도 팀별이다 — 하루 1팀만 통지되면 안 된다."""
        os.environ["PRISM_ALERT_COST_USD"] = "10"
        calls = self._outbound()
        for team, spend in (("A", 12.0), ("B", 34.0), ("C", 99.0)):
            self.AL.on_cost("2026-08-11", spend, team=team)
        self.assertEqual(len(calls), 3)
        self.AL.on_cost("2026-08-11", 50.0, team="A")            # 같은 팀·같은 날은 1회
        self.assertEqual(len(calls), 3)
        bodies = [json.loads(r.data.decode("utf-8"))["text"] for r in calls]
        self.assertTrue(all("팀 " in b for b in bodies), bodies)  # 어느 팀인지 문구로 식별된다

    def test_cost_alert_retries_after_failed_send(self):
        """[O5] 발송 실패가 그날의 비용 알림을 통째로 소진하면 안 된다."""
        os.environ["PRISM_ALERT_COST_USD"] = "10"
        clock = _Clock(time.time())
        with mock.patch.object(self.AL, "time", clock):
            boom = self._outbound(boom=True)
            self.AL.on_cost("2026-08-11", 12.0, team="A")
            self.assertEqual(len(boom), 1)
            self.assertEqual(self.AL._COST_ALERTED, {})          # 못 보냈으면 '보냄' 표시도 없다
        clock.now += 61
        with mock.patch.object(self.AL, "time", clock):
            ok = self._outbound()
            self.AL.on_cost("2026-08-11", 12.0, team="A")
            self.assertEqual(len(ok), 1)
            self.assertEqual(self.AL._COST_ALERTED, {"A": "2026-08-11"})

    def test_cost_rollup_passes_the_team(self):
        """dashops 가 팀을 안 넘기면 위 팀 스코프가 무의미해진다(연결 계약)."""
        from prism import dashops
        seen = []
        with mock.patch.object(dashops.AL, "on_cost", lambda day, total, team=None: seen.append(team)):
            with mock.patch.object(dashops, "_SV", mock.MagicMock()):
                dashops._SV._report_get.return_value = {}
                dashops._log_cost_rollup({"cost_usd": 1.0, "model": "m",
                                          "by_call": {"summary": {"n": 1, "cost": 1.0}}}, team="T1")
        self.assertEqual(seen, ["T1"])


if __name__ == "__main__":
    unittest.main()
