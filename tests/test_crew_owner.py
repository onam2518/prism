"""담당 규칙 · 서비스·주제(Tier1)별 전담 검수자 (사용자 결정 2026-09-02).

· 전담만 둔다(우선 단계 없음). 규칙에 걸린 콘텐츠는 담당자에게만 간다.
· 담당자 순서 = 주 → 부. 부 담당은 주 담당 잔여가 상한을 넘겼을 때만 받는다.
· 담당자 전원이 부재·초과·제외면 남에게 보내지 않고 보류(미배정 유지)로 드러낸다.
· 담당이 없는 콘텐츠는 담당 영역이 없는 사람이 먼저 받는다.
· 서비스 규칙이 주제 규칙보다 우선한다.
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class OwnerBase(unittest.TestCase):
    def _serve(self):
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)
        return serve

    def _content(self, st, i, service="뉴스", cat=None):
        """저장 해시를 본문에서 파생(결과 뷰가 content_ref 로 키를 다시 만든다)."""
        from prism.store import content_hash
        ref = {"displayServiceName": service, "title": "c%04d" % i, "subtitle": "", "body": "b%04d" % i}
        h = content_hash(ref)
        payload = {"quality_meta": {"review": "yellow", "confidence": 0.5, "finalGrade": "G"},
                   "content_ref": ref, "item_meta": {"content_category": [cat] if cat else []}}
        c = st._conn()
        c.execute("INSERT OR REPLACE INTO results(content_hash,service,title,final_grade,payload,created_at) "
                  "VALUES(?,?,?,?,?,?)", (h, service, ref["title"], "G", json.dumps(payload), time.time()))
        c.commit()
        from prism import serve
        serve._agg_bump()                       # 직접 INSERT 는 집계 캐시(분류·서비스 맵)를 안 올린다
        return h

    def _team(self, serve, names=("a", "b", "c"), hours=4):
        st = serve._STORE
        for rid in names:
            st.set_reviewer(rid, rid, "boksil")
            serve.CRW.set_profile(rid, {"hours_per_week": hours})
        return st


class RulesCrud(OwnerBase):
    def test_roundtrip_and_validation(self):
        serve = self._serve()
        r = serve.CRW.set_owner_rules([{"kind": "service", "value": "스포츠", "reviewers": ["a", "b", "a"]}])
        self.assertTrue(r["ok"])
        self.assertEqual(serve.CRW.owner_rules()[0]["reviewers"], ["a", "b"])     # 중복 제거 · 순서 유지
        self.assertFalse(serve.CRW.set_owner_rules([{"kind": "x", "value": "v", "reviewers": ["a"]}])["ok"])
        self.assertFalse(serve.CRW.set_owner_rules([{"kind": "service", "value": "", "reviewers": ["a"]}])["ok"])
        self.assertFalse(serve.CRW.set_owner_rules([{"kind": "service", "value": "v", "reviewers": []}])["ok"])
        dup = [{"kind": "category", "value": "금융", "reviewers": ["a"]},
               {"kind": "category", "value": "금융", "reviewers": ["b"]}]
        self.assertFalse(serve.CRW.set_owner_rules(dup)["ok"])
        self.assertEqual(len(serve.CRW.owner_rules()), 1)                           # 실패 시 종전 규칙 유지
        self.assertTrue(serve.CRW.set_owner_rules([])["ok"])
        self.assertEqual(serve.CRW.owner_rules(), [])

    def test_options_and_crew_data_expose_rules(self):
        serve = self._serve()
        st = self._team(serve)
        self._content(st, 1, "스포츠", "연예"); self._content(st, 2, "뉴스", "금융")
        serve.CRW.set_owner_rules([{"kind": "category", "value": "부동산", "reviewers": ["a"]}])
        opt = serve.CRW.owner_options()
        self.assertEqual(opt["service"], ["뉴스", "스포츠"])
        self.assertEqual(opt["category"], ["금융", "부동산", "연예"])                 # 규칙에 쓴 값도 포함
        d = serve.CRW.crew_data()
        self.assertEqual(d["owner_rules"][0]["value"], "부동산")
        self.assertIn("owner_options", d)
        self.assertNotIn("owner_rules", serve.CRW.crew_data(scope_uid="a"))        # 본인 카드에는 없음


class PlanWithOwners(OwnerBase):
    def test_owned_content_goes_only_to_owner(self):
        serve = self._serve()
        st = self._team(serve)
        hs = [self._content(st, i, "스포츠") for i in range(6)]
        serve.CRW.set_owner_rules([{"kind": "service", "value": "스포츠", "reviewers": ["a"]}])
        r = serve.CRW.plan_distribute(hs, min_reviewers=1)
        self.assertTrue(r["ok"])
        self.assertEqual(set(r["plan"]), {"a"})
        self.assertEqual(r["plan"]["a"]["owned"], 6)
        self.assertEqual(r["owner_used"], {"서비스 스포츠": 6})

    def test_unowned_content_prefers_people_without_owned_area(self):
        serve = self._serve()
        st = self._team(serve)
        hs = [self._content(st, i, "뉴스") for i in range(4)]
        serve.CRW.set_owner_rules([{"kind": "service", "value": "스포츠", "reviewers": ["a"]}])
        r = serve.CRW.plan_distribute(hs, min_reviewers=1)
        self.assertNotIn("a", r["plan"])                                            # 전담자는 자기 영역을 위해 비워 둔다
        self.assertEqual(sum(p["n"] for p in r["plan"].values()), 4)

    def test_secondary_gets_work_only_after_primary_is_over_cap(self):
        serve = self._serve()
        st = self._team(serve, hours=0.5)                                            # 캐파를 작게
        hs = [self._content(st, i, "스포츠") for i in range(300)]
        serve.CRW.set_owner_rules([{"kind": "service", "value": "스포츠", "reviewers": ["a", "b"]}])
        r = serve.CRW.plan_distribute(hs, min_reviewers=1)
        self.assertEqual(set(r["plan"]), {"a", "b"})
        self.assertGreater(r["plan"]["a"]["n"], 0)
        self.assertGreater(r["plan"]["b"]["n"], 0)
        # 주 담당이 캐파 상한을 채운 뒤에야 부 담당이 받는다
        self.assertGreaterEqual(r["plan"]["a"]["pending_after"], r["plan"]["a"]["capacity"])
        few = serve.CRW.plan_distribute(hs[:1], min_reviewers=1)
        self.assertEqual(set(few["plan"]), {"a"})

    def test_held_when_all_owners_unavailable(self):
        serve = self._serve()
        st = self._team(serve)
        hs = [self._content(st, i, "스포츠") for i in range(3)] + [self._content(st, 10, "뉴스")]
        serve.CRW.set_owner_rules([{"kind": "service", "value": "스포츠", "reviewers": ["a"]}])
        serve.CRW.set_profile("a", {"status": "leave"})
        r = serve.CRW.plan_distribute(hs, min_reviewers=1, apply=True, by="t")
        self.assertEqual(r["owner_held_n"], 3)
        self.assertEqual(r["owner_held"][0]["rule"], "서비스 스포츠")
        self.assertEqual(r["n"], 1)                                                  # 뉴스 1건만 배정
        asg = serve._STORE.assignees(None)
        self.assertEqual(set(asg), {hs[3]})                                          # 전담 건은 미배정 유지

    def test_second_slot_filled_from_pool_when_one_owner(self):
        serve = self._serve()
        st = self._team(serve)
        hs = [self._content(st, i, "스포츠") for i in range(2)]
        serve.CRW.set_owner_rules([{"kind": "service", "value": "스포츠", "reviewers": ["a"]}])
        r = serve.CRW.plan_distribute(hs, min_reviewers=2, apply=True, by="t")
        asg = serve._STORE.assignees(None)
        for h in hs:
            rv = set(asg[h]["reviewers"])
            self.assertIn("a", rv)
            self.assertEqual(len(rv), 2)

    def test_service_rule_beats_category_rule(self):
        serve = self._serve()
        st = self._team(serve)
        h = self._content(st, 1, "스포츠", "금융")
        serve.CRW.set_owner_rules([{"kind": "category", "value": "금융", "reviewers": ["b"]},
                                   {"kind": "service", "value": "스포츠", "reviewers": ["a"]}])
        r = serve.CRW.plan_distribute([h], min_reviewers=1)
        self.assertEqual(set(r["plan"]), {"a"})
        h2 = self._content(st, 2, "뉴스", "금융")
        r2 = serve.CRW.plan_distribute([h2], min_reviewers=1)
        self.assertEqual(set(r2["plan"]), {"b"})


class RebalanceWithOwners(OwnerBase):
    def test_stalled_owned_slot_moves_only_within_owner_group(self):
        serve = self._serve()
        st = self._team(serve)
        h = self._content(st, 1, "스포츠")
        serve.CRW.set_owner_rules([{"kind": "service", "value": "스포츠", "reviewers": ["a", "b"]}])
        st.set_assignees(h, ["a"], min_reviewers=1)
        serve.CRW.set_profile("a", {"status": "leave"})                              # 부재 → 전량 회수 대상
        r = serve.CRW.rebalance(apply=False)
        self.assertEqual(r["n"], 1)
        self.assertEqual(r["moves"][0]["to"], "b")
        serve.CRW.set_profile("b", {"status": "leave"})
        r2 = serve.CRW.rebalance(apply=False)
        self.assertEqual(r2["n"], 0)
        self.assertEqual(r2["owner_kept"], 1)                                        # c 에게 새지 않는다


class RouteAndMarkup(OwnerBase):
    def test_route_super_gated(self):
        from prism import serve
        self.assertEqual(serve._POST_ROUTES["/crew-owner"][1], "super")
        self.assertEqual(serve._menu_for_path("/crew-owner"), "crew")

    def test_markup(self):
        from prism import page
        for needle in ("담당 규칙", "crewOwnerSave()", "p.owned", "owner_held_n", "crewOwnedOf(m.id)",
                       "owner_kept"):
            self.assertIn(needle, page.PAGE, needle)


if __name__ == "__main__":
    unittest.main()
