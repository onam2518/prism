"""프롬프트 배포(deployops · Atelier deployments 이식): pin·키 수명주기·공개 서빙."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDeployops(unittest.TestCase):
    def _with_serve(self):
        import tempfile
        from prism import serve
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        serve._STORE = st
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        return serve, st

    def _seed_snapshot(self, st, version=3):
        payload = {"version": version, "quality_version": "v31",
                   "calls": {"summary": {"model": "solar-pro2", "system": "리드문 시스템"},
                             "grade": {"model": "solar-pro2", "system": "등급 시스템"}}}
        st.save_report(f"prompt_snapshot_v{version}", payload, None)
        st.save_report("prompt_snapshot_latest", payload, None)
        return payload

    def test_save_validates_slug_and_version(self):
        serve, st = self._with_serve()
        r = serve.deployment_save(None, slug="한글슬러그", name="x")
        self.assertFalse(r.get("ok"))
        r = serve.deployment_save(None, slug="item-extract", version=9)   # 스냅샷 없음
        self.assertFalse(r.get("ok"))
        self.assertIn("스냅샷", r.get("error", ""))
        self._seed_snapshot(st, 9)
        r = serve.deployment_save(None, slug="item-extract", version=9)
        self.assertTrue(r.get("ok"), r)
        r2 = serve.deployment_save(None, slug="item-extract", version=0)  # slug 중복
        self.assertFalse(r2.get("ok"))
        self.assertIn("slug", r2.get("error", ""))

    def test_key_lifecycle_and_serving(self):
        serve, st = self._with_serve()
        self._seed_snapshot(st, 3)
        rid = serve.deployment_save(None, slug="item-meta", name="아이템 메타", version=3)["id"]
        k = serve.deployment_key_new(rid)
        self.assertTrue(k.get("ok"))
        self.assertTrue(k["key"].startswith("pr_live_"))
        # 정상 키 → 200 + pin 버전 프롬프트
        code, body = serve.serve_prompt("item-meta", "Bearer " + k["key"])
        self.assertEqual(code, 200)
        self.assertEqual(body["version"], 3)
        self.assertEqual(body["calls"]["summary"]["system"], "리드문 시스템")
        # call 지정 · 없는 call
        code, body = serve.serve_prompt("item-meta", "Bearer " + k["key"], call="grade")
        self.assertEqual(code, 200)
        self.assertEqual(body["system"], "등급 시스템")
        code, body = serve.serve_prompt("item-meta", "Bearer " + k["key"], call="없음")
        self.assertEqual(code, 404)
        # 키 오류 경로: 무키 · 위조 · 회수
        self.assertEqual(serve.serve_prompt("item-meta", "")[0], 401)
        self.assertEqual(serve.serve_prompt("item-meta", "Bearer pr_live_fake")[0], 401)
        serve.deployment_key_revoke(rid, k["id"])
        self.assertEqual(serve.serve_prompt("item-meta", "Bearer " + k["key"])[0], 401)
        # 미지 slug · 중지된 배포
        self.assertEqual(serve.serve_prompt("ghost", "Bearer " + k["key"])[0], 404)
        serve.deployment_save(None, dep_id=rid, slug="item-meta", version=3, active=False)
        self.assertEqual(serve.serve_prompt("item-meta", "Bearer " + k["key"])[0], 404)

    def test_latest_pin_follows_snapshot(self):
        serve, st = self._with_serve()
        self._seed_snapshot(st, 3)
        rid = serve.deployment_save(None, slug="latest-dep", version=0)["id"]
        k = serve.deployment_key_new(rid)
        code, body = serve.serve_prompt("latest-dep", "Bearer " + k["key"])
        self.assertEqual((code, body["version"]), (200, 3))
        # 새 스냅샷 → pin 0 은 자동 추종
        p = self._seed_snapshot(st, 4)
        p["calls"]["summary"]["system"] = "리드문 v4"
        st.save_report("prompt_snapshot_latest", p, None)
        code, body = serve.serve_prompt("latest-dep", "Bearer " + k["key"])
        self.assertEqual(body["version"], 4)
        self.assertEqual(body["calls"]["summary"]["system"], "리드문 v4")

    def test_list_hides_hash(self):
        serve, st = self._with_serve()
        self._seed_snapshot(st, 3)
        rid = serve.deployment_save(None, slug="listed", version=3)["id"]
        serve.deployment_key_new(rid)
        items = serve.deployments_list(None)["items"]
        self.assertEqual(items[0]["slug"], "listed")
        self.assertNotIn("hash", items[0]["keys"][0])   # 목록엔 해시 미노출
        self.assertIn("prefix", items[0]["keys"][0])


if __name__ == "__main__":
    unittest.main()
