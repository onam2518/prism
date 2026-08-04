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

    def _content_cat(self, st, i, cat, ts=None):
        """분류를 단 콘텐츠 1건. 저장 해시를 본문에서 파생시킨다(운영 저장 경로와 동일) —
        결과 뷰가 content_ref 로 키를 다시 만들기 때문에 합성 해시로는 조인이 안 된다."""
        from prism.store import content_hash
        ref = {"displayServiceName": "s", "title": "c%04d" % i, "subtitle": "", "body": "b%04d" % i}
        h = content_hash(ref)
        payload = {"quality_meta": {"review": "yellow", "confidence": 0.5, "finalGrade": "G"},
                   "content_ref": ref, "item_meta": {"content_category": [cat]}}
        c = st._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,payload,created_at) "
                  "VALUES(?,?,?,?,?,?)", (h, "s", ref["title"], "G", json.dumps(payload), ts or time.time()))
        c.commit()
        return h


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
            self._content(serve._STORE, self._h(i))
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

    def test_reuses_crew_compute_tables(self):
        """[감사 #15] rebalance 는 _crew_compute 조회분(asg·fmap·targets)을 재사용한다 —
        같은 호출 안에서 feedback·assignments 전량이 두 번 내려오면 안 된다(결과는 불변)."""
        serve = self._serve()
        st = self._stalled(serve)
        calls = {"fmap": 0}
        orig = st.feedback_map
        def counting(team=None):
            calls["fmap"] += 1
            return orig(team=team)
        st.feedback_map = counting
        r = serve.CRW.rebalance(None)
        self.assertEqual(r["n"], 8)                             # 동작 불변(위 테스트와 동일 결과)
        self.assertEqual(calls["fmap"], 1)                      # 전량 조회는 1회뿐


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
    def test_crew_tab_registered_super_only(self):
        """검수운영은 인력 지표를 다루므로 팀 관리자에게도 기본 비공개.
        '운영 관리' 메뉴 안의 탭이지만 권한 id 는 crew 를 그대로 써서 앞뒤 게이트를 하나로 둔다."""
        from prism import adminops as AO
        self.assertIn("crew", AO.CONFIGURABLE_MENUS)
        self.assertEqual(AO.DEFAULT_MENU_PERMS["crew"], {"super": True, "admin": False})
        self.assertIn("검수운영", AO.MENU_LABELS["crew"])
        # 팀 관리는 종전대로 팀 관리자도 볼 수 있다(계정·권한은 그들의 일)
        self.assertEqual(AO.DEFAULT_MENU_PERMS["admin"], {"super": True, "admin": True})

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


class TestAutoOps(CrewBase):
    """자동 운영: 사람이 매주 잊지 않고 눌러야 도는 운영은 결국 안 돈다.
    다만 남의 일을 옮기는 동작이라 기본은 꺼둔다 · 사이클당 1회만 실행돼야 한다."""

    def _cycle_now(self):
        """이번 사이클(월 10시 KST) 시작 직후 시각. 요일에 의존하지 않는 고정점."""
        from prism.crewops import _last_open_ts, settings
        cfg = dict(settings(None))
        return _last_open_ts(time.time(), cfg) + 3600

    def test_last_open_is_recent_configured_weekday_hour(self):
        from prism.crewops import _last_open_ts
        from prism.store import day_key, _tz_sec
        cfg = {"wave_weekday": 0, "wave_hour": 10}
        now = time.time()
        ts = _last_open_ts(now, cfg)
        self.assertLessEqual(ts, now)
        self.assertGreater(ts, now - 7 * 86400 - 1)              # 최대 한 주 전
        local = ts + _tz_sec()
        self.assertEqual(int(local % 86400) // 3600, 10)         # 팀 타임존 10시
        self.assertEqual((int(local // 86400) + 3) % 7, 0)       # 월요일
        # 시작 시각 1초 전이면 지난 주 사이클을 가리킨다(같은 요일이라도 앞당겨 잡지 않음)
        self.assertEqual(_last_open_ts(ts - 1, cfg), ts - 7 * 86400)
        self.assertNotEqual(day_key(ts), day_key(ts - 7 * 86400))

    def _fixture(self, serve, n=8):
        st = serve._STORE
        base = time.time() - 86400 * 2
        for i in range(n):
            self._content(st, self._h(i))
        for k, rid in enumerate(("a", "b")):
            st.set_reviewer(rid, rid, "boksil")
            serve.CRW.set_profile(rid, {"hours_per_week": 2})
            for j in range(10):                    # 실측 표본
                h = self._h(500 + k * 100 + j)
                self._content(st, h)
                st.save_feedback(h, "s", "t", "good", "analyze", "", base + k * 40000 + j * 60, reviewer=rid)
        return st

    def test_off_by_default(self):
        serve = self._serve()
        self._fixture(serve)
        r = serve.CRW.auto_tick(None)
        self.assertFalse(r["auto_wave"])
        self.assertFalse(r["auto_rebalance"])
        self.assertIsNone(r["wave"])
        self.assertEqual(serve._STORE.assignees(None), {})       # 켜지 않으면 아무것도 안 옮긴다

    def test_wave_runs_once_per_cycle(self):
        serve = self._serve()
        st = self._fixture(serve)
        serve.CRW.set_settings({"auto_wave": 1, "wave_min_reviewers": 1})
        now = self._cycle_now()
        r1 = serve.CRW.auto_tick(None, now=now)
        self.assertTrue(r1["wave"]["ok"])
        # 대상 = 아직 아무도 안 맡은 검수 대상 전부(28건). 한 사람이 이미 판정했더라도
        # 담당이 지정되지 않았으면 커버리지가 비어 있는 것이라 배분 대상이 맞다.
        self.assertEqual(r1["wave"]["n"], 28)
        self.assertEqual(len(st.assignees(None)), 28)
        self.assertEqual(serve.CRW.wave(None)["due_at"], r1["wave"]["due_at"])
        # 같은 사이클에 다시 호출해도 두 번 나가지 않는다(화면 진입마다 불러도 안전)
        for i in range(8, 12):
            self._content(st, self._h(i))
        r2 = serve.CRW.auto_tick(None, now=now + 3600)
        self.assertIsNone(r2["wave"])
        self.assertEqual(len(st.assignees(None)), 28)
        # 다음 사이클이 오면 그 사이 늘어난 것만 새로 나간다
        r3 = serve.CRW.auto_tick(None, now=now + 7 * 86400)
        self.assertEqual(r3["wave"]["n"], 4)
        self.assertEqual(len(st.assignees(None)), 32)

    def test_dry_run_changes_nothing(self):
        serve = self._serve()
        st = self._fixture(serve)
        serve.CRW.set_settings({"auto_wave": 1})
        r = serve.CRW.auto_tick(None, now=self._cycle_now(), apply=False)
        self.assertEqual(r["wave"]["n"], 28)                     # 계획은 나오고
        self.assertEqual(st.assignees(None), {})                 # 실제로는 안 옮긴다
        self.assertEqual(serve.CRW.auto_state(None), {})         # 회차 키도 안 남긴다

    def test_rebalance_only_near_deadline(self):
        serve = self._serve()
        st = self._fixture(serve)
        st.set_assignees_bulk([self._h(i) for i in range(8)], ["a"], min_reviewers=1)
        c = st._conn()
        c.execute("UPDATE assignments SET ts=?", (time.time() - 86400 * 9,))
        c.commit()
        serve.CRW.set_settings({"auto_rebalance": 1})
        now = time.time()
        serve.CRW.set_wave(now + 5 * 86400)                      # 기한이 멀면 손대지 않는다
        self.assertIsNone(serve.CRW.auto_tick(None, now=now)["rebalance"])
        self.assertEqual(st.assignees(None)[self._h(0)]["reviewers"], ["a"])
        serve.CRW.set_wave(now + 3600)                           # 기한 하루 안 → 이관
        r = serve.CRW.auto_tick(None, now=now)
        self.assertTrue(r["rebalance"]["n"] > 0)
        self.assertEqual(r["rebalance"]["to"], {"b": r["rebalance"]["n"]})
        self.assertEqual(serve.assign_log_data(None)["items"][0]["by"], "자동 운영")

    def test_wave_marks_cycle_even_with_nothing_to_send(self):
        """내보낼 게 없어도 이번 사이클은 처리한 것으로 본다(매번 빈 계산 반복 방지)."""
        serve = self._serve()
        st = self._fixture(serve, n=4)
        st.set_assignees_bulk(sorted(st.review_targets(None)), ["a"], min_reviewers=1)   # 남는 게 없게
        serve.CRW.set_settings({"auto_wave": 1})
        now = self._cycle_now()
        r = serve.CRW.auto_tick(None, now=now)
        self.assertEqual(r["wave"]["n"], 0)
        self.assertTrue(serve.CRW.auto_state(None)["wave_cycle"])
        self.assertIsNone(serve.CRW.auto_tick(None, now=now + 60)["wave"])


class TestAdaptiveOverlap(CrewBase):
    """전건 3인 검수 대신 2인으로 시작하고 갈린 건에만 3번째를 붙인다.
    실측 불일치율(선착 2인 기준 25%)에서 판정 수를 25% 안팎 줄이는 레버."""

    def _pair(self, serve, n=6):
        st = serve._STORE
        for i in range(n):
            self._content(st, self._h(i))
        for rid in ("a", "b", "c"):
            st.set_reviewer(rid, rid, "boksil")
            serve.CRW.set_profile(rid, {"hours_per_week": 2})
        st.set_assignees_bulk([self._h(i) for i in range(n)], ["a", "b"], min_reviewers=2)
        return st

    def test_only_disagreements_need_a_third(self):
        serve = self._serve()
        st = self._pair(serve)
        now = time.time()
        # 0·1 갈림 · 2 합의 · 3 은 한 명만 봄 · 4·5 는 아무도 안 봄
        for h, (va, vb) in {0: ("good", "bad"), 1: ("bad", "good"), 2: ("good", "good")}.items():
            st.save_feedback(self._h(h), "s", "t", va, "analyze", "", now, reviewer="a")
            st.save_feedback(self._h(h), "s", "t", vb, "analyze", "", now, reviewer="b")
        st.save_feedback(self._h(3), "s", "t", "good", "analyze", "", now, reviewer="a")
        pend = {p["hash"] for p in serve.CRW.split_pending(None)}
        self.assertEqual(pend, {self._h(0), self._h(1)})

    def test_escalate_adds_a_third_who_has_not_seen_it(self):
        serve = self._serve()
        st = self._pair(serve)
        now = time.time()
        st.save_feedback(self._h(0), "s", "t", "good", "analyze", "", now, reviewer="a")
        st.save_feedback(self._h(0), "s", "t", "bad", "analyze", "", now, reviewer="b")
        r = serve.CRW.escalate_split(None)
        self.assertEqual(r["n"], 1)
        self.assertFalse(r["applied"])
        self.assertEqual(st.assignees(None)[self._h(0)]["reviewers"], ["a", "b"])   # 계획만
        serve.CRW.escalate_split(None, apply=True, by="admin@x")
        cur = st.assignees(None)[self._h(0)]
        self.assertEqual(sorted(cur["reviewers"]), ["a", "b", "c"])                 # 안 본 사람이 붙는다
        self.assertEqual(cur["min"], 3)                                             # 통과 기준도 3인
        self.assertEqual(serve.assign_log_data(None)["items"][0]["mode"], "갈린 건 한 명 더")
        # 두 번째 호출은 대상이 없다(이미 3인 배정 → split_pending 에서 빠짐)
        self.assertEqual(serve.CRW.escalate_split(None)["n"], 0)

    def test_settled_content_is_left_alone(self):
        """골든으로 확정됐거나 리드가 최종판정한 건은 3번째를 붙이지 않는다."""
        serve = self._serve()
        st = self._pair(serve)
        now = time.time()
        for i in (0, 1):
            st.save_feedback(self._h(i), "s", "t", "good", "analyze", "", now, reviewer="a")
            st.save_feedback(self._h(i), "s", "t", "bad", "analyze", "", now, reviewer="b")
        serve.set_final_verdict(self._h(0), "good", by="lead", team=None)
        pend = {p["hash"] for p in serve.CRW.split_pending(None)}
        self.assertEqual(pend, {self._h(1)})

    def test_auto_escalate_runs_every_tick_when_on(self):
        serve = self._serve()
        st = self._pair(serve)
        now = time.time()
        st.save_feedback(self._h(0), "s", "t", "good", "analyze", "", now, reviewer="a")
        st.save_feedback(self._h(0), "s", "t", "bad", "analyze", "", now, reviewer="b")
        self.assertIsNone(serve.CRW.auto_tick(None)["escalate"])       # 기본 꺼짐
        serve.CRW.set_settings({"auto_escalate": 1})
        r = serve.CRW.auto_tick(None)
        self.assertEqual(r["escalate"]["n"], 1)

    def test_escalate_reuses_crew_compute_tables(self):
        """[감사 #15] escalate_split 은 _crew_compute 조회분을 split_pending 과 공유한다 —
        feedback·assignments 전량이 한 호출에 두 번 내려오면 안 된다(결과는 불변)."""
        serve = self._serve()
        st = self._pair(serve)
        now = time.time()
        st.save_feedback(self._h(0), "s", "t", "good", "analyze", "", now, reviewer="a")
        st.save_feedback(self._h(0), "s", "t", "bad", "analyze", "", now, reviewer="b")
        calls = {"fmap": 0}
        orig = st.feedback_map
        def counting(team=None):
            calls["fmap"] += 1
            return orig(team=team)
        st.feedback_map = counting
        r = serve.CRW.escalate_split(None)
        self.assertEqual(r["n"], 1)                             # 동작 불변
        self.assertEqual(calls["fmap"], 1)                      # 전량 조회는 1회뿐


class TestStrengthMatching(CrewBase):
    def _cat_setup(self, serve):
        """두 사람 · 두 분류. a 는 Sports 에서, b 는 Books 에서 팀 결론과 잘 맞는다."""
        st = serve._STORE
        for rid in ("a", "b"):
            st.set_reviewer(rid, rid, "boksil")
            serve.CRW.set_profile(rid, {"hours_per_week": 2})
        now = time.time() - 86400

        def hist(i, cat, va, vb, vc):
            h = self._content_cat(st, 2000 + i, cat, ts=now)
            for rid, v in (("a", va), ("b", vb), ("c", vc)):
                st.save_feedback(h, "s", "t", v, "analyze", "", now + i, reviewer=rid)
        # Sports 6건: 다수(=c 와 a) good · b 는 계속 어긋남 / Books 6건: 반대
        for i in range(6):
            hist(i, "Sports", "good", "bad", "good")
        for i in range(6, 12):
            hist(i, "Books", "bad", "good", "good")
        return st

    def test_reliability_is_per_category(self):
        serve = self._serve()
        self._cat_setup(serve)
        rel = serve.CRW.category_reliability(None)
        self.assertEqual(rel["a"]["Sports"], 1.0)
        self.assertEqual(rel["a"]["Books"], 0.0)
        self.assertEqual(rel["b"]["Sports"], 0.0)
        self.assertEqual(rel["b"]["Books"], 1.0)
        # 표본이 적은 조합은 담지 않는다(적은 표본으로 강점을 단정하지 않는다)
        self.assertEqual(serve.CRW.category_reliability(None, min_n=99), {})

    def test_strong_reviewer_gets_that_category(self):
        serve = self._serve()
        st = self._cat_setup(serve)
        sports = [self._content_cat(st, 3000 + i, "Sports") for i in range(6)]
        on = serve.CRW.plan_distribute(sports, min_reviewers=1, reviewers=["a", "b"], match=True)
        off = serve.CRW.plan_distribute(sports, min_reviewers=1, reviewers=["a", "b"], match=False)
        self.assertGreater(on["plan"]["a"]["n"], off["plan"]["a"]["n"])   # 강점 쪽으로 기운다
        self.assertEqual(sum(p["n"] for p in on["plan"].values()), 6)     # 총량은 그대로

    def test_match_reuses_fmap_from_crew_compute(self):
        """[감사 #27] plan_distribute(match) 의 category_reliability 는 _crew_compute 가
        받은 fmap 을 재사용한다 — 배정 1회에 feedback 전량 조회가 2번이면 안 된다."""
        serve = self._serve()
        st = self._cat_setup(serve)
        sports = [self._content_cat(st, 5000 + i, "Sports") for i in range(4)]
        calls = {"fmap": 0}
        orig = st.feedback_map
        def counting(team=None):
            calls["fmap"] += 1
            return orig(team=team)
        st.feedback_map = counting
        r = serve.CRW.plan_distribute(sports, min_reviewers=1, reviewers=["a", "b"], match=True)
        self.assertTrue(r["ok"])
        self.assertEqual(sum(p["n"] for p in r["plan"].values()), 4)      # 동작 불변
        self.assertEqual(calls["fmap"], 1)                                # 전량 조회는 1회뿐

    def test_lack_classes_go_first(self):
        """정답셋이 부족한 분류를 앞으로 · 상한이 걸릴 때 더 값진 것이 먼저 나간다."""
        serve = self._serve()
        st = serve._STORE
        rows = [self._content_cat(st, 4000 + i, cat)
                for i, cat in enumerate(("Sports", "Books", "Sports"))]
        orig = serve._lack_classes
        serve._lack_classes = lambda team=None: {"Books"}
        try:
            serve._agg_bump()
            self.assertEqual(serve.CRW.prioritize(rows, None)[0], rows[1])   # Books 가 맨 앞
        finally:
            serve._lack_classes = orig
            serve._agg_bump()


if __name__ == "__main__":
    unittest.main()
