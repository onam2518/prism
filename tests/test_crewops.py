"""검수 인력 운영(crewops): 캐파 실측 · 인력 원장 · 캐파 비례 배정 · 정체 회수 · 신호등.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
배경(2026-07-28 운영 실측): 900슬롯을 한 번에 뿌린 뒤 11일간 293건 정체 · 처리속도가
사람마다 23배 차이인데 전원 100건 균등 배정 · 그동안 2명은 유휴. 남은 일의 실제 크기는
팀 합계 4시간인데 끝나지 않던 상태였다. 여기서 검증하는 계약이 그 재발을 막는 장치다.
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class CrewBase(unittest.TestCase):
    def _serve(self):
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)          # 다음 테스트가 이전 crew 캐시를 보지 않게
        return serve

    def _content(self, st, h, ts=None):
        """YELLOW 콘텐츠 1건(검수 대상)."""
        payload = {"quality_meta": {"review": "yellow", "confidence": 0.5},
                   "content_ref": {"title": h, "body": "b"}}
        c = st._conn()
        c.execute("INSERT INTO results(content_hash,service,title,final_grade,payload,created_at) "
                  "VALUES(?,?,?,?,?,?)", (h, "s", h, "G", json.dumps(payload), ts or time.time()))
        c.commit()

    def _h(self, i):
        return "%016x" % i


class TestCapacity(CrewBase):
    def test_rate_from_median_gap(self):
        """처리율 = 연속 판정 간격의 중앙값. 총시간/건수는 중간 휴지에, 평균은 한 번의
        긴 간격에 끌려간다. 중앙값이라야 실제 작업 속도가 나온다."""
        serve = self._serve()
        st = serve._STORE
        base = time.time() - 86400
        for i in range(12):                        # 60초 간격 12건 + 중간에 2시간 휴지 1회
            self._content(st, self._h(i))
            gap = base + i * 60 + (7200 if i >= 6 else 0)
            st.save_feedback(self._h(i), "s", "t", "good", "analyze", "", gap, reviewer="A")
        cap = serve.CRW.capacity(None)
        self.assertEqual(cap["A"]["n_total"], 12)
        self.assertEqual(cap["A"]["median_sec"], 60.0)          # 휴지(7200초)는 유효 구간 밖 → 제외
        self.assertEqual(cap["A"]["rate_per_hour"], 60.0)
        self.assertTrue(cap["A"]["measured"])                   # 표본 8건 이상 = 실측
        self.assertEqual(cap["A"]["sessions"], 2)               # 30분 초과 간격 = 세션 분리

    def test_small_sample_borrows_team_median(self):
        """표본이 적은 사람에게 우연히 나온 극단값으로 배정량을 정하지 않는다."""
        serve = self._serve()
        st = serve._STORE
        base = time.time() - 86400
        for i in range(12):                        # A: 표본 충분(60초)
            self._content(st, self._h(i))
            st.save_feedback(self._h(i), "s", "t", "good", "analyze", "", base + i * 60, reviewer="A")
        for i in range(100, 102):                  # B: 표본 1건뿐(3초 · 우연한 연타)
            self._content(st, self._h(i))
            st.save_feedback(self._h(i), "s", "t", "good", "analyze", "", base + (i - 100) * 3, reviewer="B")
        cap = serve.CRW.capacity(None)
        self.assertTrue(cap["A"]["measured"])
        self.assertFalse(cap["B"]["measured"])                  # 잠정값 표식
        self.assertEqual(cap["B"]["median_sec"], cap["A"]["median_sec"])   # 팀 중앙값을 빌려 씀


class TestProfile(CrewBase):
    def test_roundtrip_and_validation(self):
        serve = self._serve()
        r = serve.CRW.set_profile("u1", {"hours_per_week": 3.5, "workdays": [0, 2, 4],
                                         "status": "onboarding", "note": "오전만"}, by="admin@x")
        self.assertTrue(r["ok"])
        self.assertEqual(r["profile"]["hours_per_week"], 3.5)
        self.assertEqual(r["profile"]["workdays"], [0, 2, 4])
        self.assertEqual(r["profile"]["updated_by"], "admin@x")
        self.assertEqual(serve.CRW.profiles(None)["u1"]["status"], "onboarding")
        # 부분 패치: 건드리지 않은 필드는 보존
        serve.CRW.set_profile("u1", {"hours_per_week": 5})
        p = serve.CRW.profiles(None)["u1"]
        self.assertEqual(p["workdays"], [0, 2, 4])
        self.assertEqual(p["note"], "오전만")
        # 검증: 알 수 없는 상태 · 잘못된 날짜 · 뒤집힌 기간은 거절(저장 안 됨)
        self.assertFalse(serve.CRW.set_profile("u1", {"status": "몰라"})["ok"])
        self.assertFalse(serve.CRW.set_profile("u1", {"leave_from": "2026/01/01"})["ok"])
        self.assertFalse(serve.CRW.set_profile("u1", {"leave_from": "2026-05-05",
                                                      "leave_to": "2026-05-01"})["ok"])
        self.assertEqual(serve.CRW.profiles(None)["u1"]["status"], "onboarding")
        self.assertFalse(serve.CRW.set_profile("", {"hours_per_week": 1})["ok"])
        # 상한: 주 40시간 초과는 잘라 담는다
        self.assertEqual(serve.CRW.set_profile("u1", {"hours_per_week": 99})["profile"]["hours_per_week"], 40.0)

    def test_leave_window_blocks_assignment(self):
        serve = self._serve()
        from prism.store import day_key
        today = day_key()
        serve.CRW.set_profile("away", {"leave_from": today, "leave_to": today})
        prof = serve.CRW.profiles(None)["away"]
        self.assertTrue(serve.CRW._on_leave(prof))
        self.assertFalse(serve.CRW._available(prof))
        # 상태만 active 여도 부재 기간이면 배정 대상이 아니다(휴가 중 배정 사고 방지)
        self.assertEqual(prof["status"], "active")


class TestPlanDistribute(CrewBase):
    def _team(self, serve, n_content=12):
        st = serve._STORE
        base = time.time() - 86400 * 2
        for i in range(n_content):
            self._content(st, self._h(i))
        # 빠름: 30초/건 · 느림: 300초/건 (각 10건씩 실측 표본 확보)
        for i, (rid, gap) in enumerate((("fast", 30), ("slow", 300))):
            for k in range(10):
                h = self._h(500 + i * 100 + k)
                self._content(st, h)
                st.save_feedback(h, "s", "t", "good", "analyze", "", base + i * 40000 + k * gap, reviewer=rid)
        for rid in ("fast", "slow"):
            st.set_reviewer(rid, rid, "boksil")
            serve.CRW.set_profile(rid, {"hours_per_week": 2})
        return st

    def test_capacity_proportional_not_equal(self):
        """같은 약속 시간이어도 처리율이 다르면 물량이 달라야 한다.
        (기존 distribute_assignments 는 미완료 건수만 봐서 전원 같은 짐을 졌다.)"""
        serve = self._serve()
        self._team(serve)
        r = serve.CRW.plan_distribute([self._h(i) for i in range(12)], min_reviewers=1)
        self.assertTrue(r["ok"])
        self.assertFalse(r["applied"])                          # 미리보기가 기본
        self.assertGreater(r["plan"]["fast"]["n"], r["plan"]["slow"]["n"])
        self.assertEqual(r["plan"]["fast"]["n"] + r["plan"]["slow"]["n"], 12)
        # 미리보기는 실제 배정을 만들지 않는다
        self.assertEqual(serve._STORE.assignees(None), {})

    def test_apply_writes_assignments_and_log(self):
        serve = self._serve()
        self._team(serve)
        hs = [self._h(i) for i in range(12)]
        r = serve.CRW.plan_distribute(hs, min_reviewers=2, apply=True, by="admin@x")
        self.assertTrue(r["applied"])
        asg = serve._STORE.assignees(None)
        self.assertEqual(len(asg), 12)
        for h in hs:
            self.assertEqual(len(set(asg[h]["reviewers"])), 2)  # 콘텐츠당 서로 다른 2명
        log = serve.assign_log_data(None)["items"]
        self.assertEqual(log[0]["mode"], "여력만큼 나눔")
        self.assertEqual(log[0]["by"], "admin@x")

    def test_unavailable_excluded(self):
        serve = self._serve()
        self._team(serve)
        serve.CRW.set_profile("slow", {"status": "leave"})
        r = serve.CRW.plan_distribute([self._h(i) for i in range(6)], min_reviewers=1,
                                      reviewers=["fast", "slow"])
        self.assertNotIn("slow", r["plan"])
        self.assertIn("slow", r["blocked"])
        self.assertEqual(r["plan"]["fast"]["n"], 6)

    def test_no_available_reviewer_is_error_not_crash(self):
        serve = self._serve()
        self._team(serve)
        for rid in ("fast", "slow"):
            serve.CRW.set_profile(rid, {"status": "inactive"})
        r = serve.CRW.plan_distribute([self._h(0)], min_reviewers=1)
        self.assertFalse(r["ok"])
        self.assertEqual(r["n"], 0)

    def test_over_capacity_flagged(self):
        """캐파를 넘는 배정은 막지 않되 반드시 알린다(운영 판단은 사람 몫)."""
        serve = self._serve()
        self._team(serve, n_content=0)
        for i in range(400):
            self._content(st := serve._STORE, self._h(i))
        r = serve.CRW.plan_distribute([self._h(i) for i in range(400)], min_reviewers=1)
        self.assertTrue(r["over"])


class TestRebalance(CrewBase):
    def _stalled(self, serve):
        st = serve._STORE
        base = time.time() - 86400 * 2
        for i in range(8):
            self._content(st, self._h(i))
        for rid in ("busy", "free"):
            st.set_reviewer(rid, rid, "boksil")
            serve.CRW.set_profile(rid, {"hours_per_week": 2})
        for k in range(10):                        # 실측 표본(양쪽 동일 속도)
            h = self._h(900 + k)
            self._content(st, h)
            st.save_feedback(h, "s", "t", "good", "analyze", "", base + k * 60, reviewer="free")
        st.set_assignees_bulk([self._h(i) for i in range(8)], ["busy"], min_reviewers=1)
        c = st._conn()                             # 10일 전 배정으로 되돌려 정체 재현
        c.execute("UPDATE assignments SET ts=?", (time.time() - 86400 * 10,))
        c.commit()
        return st

    def test_moves_stalled_work_to_free_reviewer(self):
        serve = self._serve()
        self._stalled(serve)
        r = serve.CRW.rebalance(None)
        self.assertTrue(r["ok"])
        self.assertEqual(r["n"], 8)
        self.assertEqual(r["from_counts"], {"busy": 8})
        self.assertEqual(r["to_counts"], {"free": 8})
        self.assertFalse(r["applied"])
        self.assertEqual(serve._STORE.assignees(None)[self._h(0)]["reviewers"], ["busy"])   # 계획만

    def test_apply_reassigns_and_logs(self):
        serve = self._serve()
        st = self._stalled(serve)
        serve.CRW.rebalance(None, apply=True, by="admin@x")
        asg = st.assignees(None)
        self.assertTrue(all(a["reviewers"] == ["free"] for h, a in asg.items()
                            if h in {self._h(i) for i in range(8)}))
        self.assertEqual(serve.assign_log_data(None)["items"][0]["mode"], "멈춘 일 넘김")

    def test_completed_work_is_not_recalled(self):
        """이미 판정한 배정은 정체가 아니다. 회수하면 검수 결과가 사라진 것처럼 보인다."""
        serve = self._serve()
        st = self._stalled(serve)
        st.save_feedback(self._h(0), "s", "t", "good", "analyze", "", time.time(), reviewer="busy")
        r = serve.CRW.rebalance(None)
        self.assertEqual(r["n"], 7)
        self.assertNotIn(self._h(0), [m["hash"] for m in r["moves"]])

    def test_no_duplicate_reviewer_on_same_content(self):
        """이관 대상이 이미 그 콘텐츠 담당이면 건너뛴다(한 콘텐츠에 같은 사람 둘 금지)."""
        serve = self._serve()
        st = self._stalled(serve)
        st.set_assignees(self._h(0), ["busy", "free"], min_reviewers=1)
        c = st._conn()
        c.execute("UPDATE assignments SET ts=?", (time.time() - 86400 * 10,))
        c.commit()
        r = serve.CRW.rebalance(None)
        for m in r["moves"]:
            if m["hash"] == self._h(0):
                self.fail("이미 담당인 사람에게 다시 이관했다")
        for h, a in st.assignees(None).items():
            self.assertEqual(len(a["reviewers"]), len(set(a["reviewers"])))


class TestCrewData(CrewBase):
    def test_signals_and_summary(self):
        serve = self._serve()
        st = serve._STORE
        base = time.time() - 86400 * 2
        for i in range(10):
            self._content(st, self._h(i))
        for k in range(10):                        # 실측 표본
            h = self._h(700 + k)
            self._content(st, h)
            st.save_feedback(h, "s", "t", "good", "analyze", "", base + k * 60, reviewer="stuck")
        for rid in ("stuck", "spare"):
            st.set_reviewer(rid, rid, "boksil")
            serve.CRW.set_profile(rid, {"hours_per_week": 2})
        st.set_assignees_bulk([self._h(i) for i in range(10)], ["stuck"], min_reviewers=1)
        c = st._conn()
        c.execute("UPDATE assignments SET ts=?", (time.time() - 86400 * 9,))
        c.commit()
        d = serve.CRW.crew_data(None)
        by = {m["name"]: m for m in d["members"]}
        self.assertEqual(by["stuck"]["signal"], "red")          # 9일 방치 · 진행 0%
        self.assertEqual(by["spare"]["signal"], "idle")
        self.assertEqual(by["stuck"]["load"]["pending"], 10)
        s = d["summary"]
        self.assertEqual(s["pending"], 10)
        self.assertEqual(s["stale_total"], 10)
        self.assertTrue(s["eta"])                               # 캐파가 있으면 완료일이 나온다
        self.assertEqual([i["name"] for i in s["idle"]], ["spare"])
        self.assertEqual(len(d["burndown"]), 21)

    def test_burndown_left_reconstructs_from_today(self):
        """잔여선은 오늘의 실제 미완료에서 거슬러 복원한다(어제 잔여 = 오늘 잔여 + 오늘 완료)."""
        serve = self._serve()
        st = serve._STORE
        for i in range(6):
            self._content(st, self._h(i))
        st.set_reviewer("r", "r", "boksil")
        st.set_assignees_bulk([self._h(i) for i in range(6)], ["r"], min_reviewers=1)
        for i in range(2):                         # 오늘 2건 완료 → 잔여 4
            st.save_feedback(self._h(i), "s", "t", "good", "analyze", "", time.time(), reviewer="r")
        b = serve.CRW.crew_data(None)["burndown"]
        self.assertEqual(b[-1]["left"], 4)
        self.assertEqual(b[-1]["done"], 2)
        self.assertEqual(b[-2]["left"], 6)                      # 어제는 6건 남아 있었다

    def test_scope_uid_hides_team_wide_numbers(self):
        """본인 열람은 자기 카드만 · 팀 요약·번다운은 주지 않는다(개인 지표 노출 범위)."""
        serve = self._serve()
        st = serve._STORE
        for rid in ("me", "other"):
            st.set_reviewer(rid, rid, "boksil")
        d = serve.CRW.crew_data(None, scope_uid="me")
        self.assertEqual(d["scope"], "me")
        self.assertEqual([m["name"] for m in d["members"]], ["me"])
        self.assertNotIn("summary", d)
        self.assertNotIn("burndown", d)

    def test_coach_flags_need_sample(self):
        """표본 2~3건으로 '기준 이탈' 딱지를 붙이면 신규자가 곧바로 코칭 대상이 된다."""
        serve = self._serve()
        st = serve._STORE
        st.set_reviewer("newbie", "newbie", "boksil")
        for i in range(3):                         # 3건 전부 good(성향 100%)인데 표본 미달
            self._content(st, self._h(i))
            st.save_feedback(self._h(i), "s", "t", "good", "analyze", "", time.time(), reviewer="newbie")
        d = serve.CRW.crew_data(None)
        flags = [f["id"] for m in d["members"] if m["name"] == "newbie" for f in m["flags"]]
        self.assertNotIn("drift", flags)


class TestReviewersMapContract(CrewBase):
    def test_normalizes_both_store_shapes(self):
        """reviewers_map 은 sqlite {이름: 아바타} · supabase {uid: {name, avatar}} 로 계약이
        갈려 있다(기존 호출부가 키만 써서 드러나지 않던 차이). 양쪽 다 흡수해야 한다."""
        from prism.crewops import _norm_reviewers
        self.assertEqual(_norm_reviewers({"해씨": "boksil"}),
                         {"해씨": {"name": "해씨", "avatar": "boksil"}})
        self.assertEqual(_norm_reviewers({"uid-1": {"name": "해씨", "avatar": "ddakji"}}),
                         {"uid-1": {"name": "해씨", "avatar": "ddakji"}})
        self.assertEqual(_norm_reviewers({"uid-2": {}})["uid-2"]["name"], "uid-2")
        self.assertEqual(_norm_reviewers(None), {})


class TestMenuGate(CrewBase):
    def test_crew_menu_registered_super_only(self):
        """검수운영은 인력 지표를 다루므로 팀 관리자에게도 기본 비공개."""
        from prism import adminops as AO
        self.assertIn("crew", AO.CONFIGURABLE_MENUS)
        self.assertEqual(AO.DEFAULT_MENU_PERMS["crew"], {"super": True, "admin": False})
        self.assertEqual(AO.MENU_LABELS["crew"], "검수운영")

    def test_post_routes_are_super_gated(self):
        from prism import serve
        for p in ("/crew-profile", "/crew-wave", "/crew-assign", "/crew-rebalance"):
            self.assertEqual(serve._POST_ROUTES[p][1], "super", p)
        self.assertEqual(serve._menu_for_path("/crew-assign"), "crew")


class TestWave(CrewBase):
    def test_set_and_clear_due(self):
        serve = self._serve()
        due = time.time() + 86400 * 3
        r = serve.CRW.set_wave(due, by="admin@x", plan={"u1": 20})
        self.assertTrue(r["ok"])
        self.assertEqual(serve.CRW.wave(None)["due_at"], due)
        self.assertEqual(serve.CRW.wave(None)["plan"], {"u1": 20})
        self.assertTrue(serve.CRW.wave(None)["opened_at"] > 0)
        serve.CRW.set_wave("")                     # 빈 값 = 해제
        self.assertEqual(serve.CRW.wave(None), {})

    def test_due_makes_overdue_red(self):
        serve = self._serve()
        st = serve._STORE
        for i in range(4):
            self._content(st, self._h(i))
        st.set_reviewer("r", "r", "boksil")
        st.set_assignees_bulk([self._h(i) for i in range(4)], ["r"], min_reviewers=1)
        serve.CRW.set_wave(time.time() - 3600)     # 마감 지남
        d = serve.CRW.crew_data(None)
        self.assertEqual(d["members"][0]["signal"], "red")


class TestSettings(CrewBase):
    def test_defaults_and_override(self):
        serve = self._serve()
        self.assertEqual(serve.CRW.settings(None)["stale_days"], 3)
        serve.CRW.set_settings({"stale_days": 7, "buffer": 0.5, "몰라": 1})
        s = serve.CRW.settings(None)
        self.assertEqual(s["stale_days"], 7)
        self.assertEqual(s["buffer"], 0.5)
        self.assertNotIn("몰라", s)                 # 모르는 키는 저장하지 않는다
        serve.CRW.set_settings({"stale_days": "숫자아님"})
        self.assertEqual(serve.CRW.settings(None)["stale_days"], 7)   # 파싱 실패 시 종전 값 유지


if __name__ == "__main__":
    unittest.main()
