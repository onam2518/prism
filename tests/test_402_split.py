"""402 는 두 가지이고 조치가 다르다 — 원장에서 구분되어야 한다(2026-07-30).

타임리 라우터 문서:
  insufficient_balance   = 스페이스 크레딧 부족   → 충전
  project_limit_exceeded = 프로젝트 지출 한도 도달 → 한도 상향

종전엔 둘 다 'billing' 으로 뭉쳐서, 실패 원장만 보고는 무엇을 해야 할지 알 수 없었다.
실측: 7/29 에 402 가 832건 났는데 어느 쪽인지 사후 판별이 불가능했다(응답 본문을 들고
있는 recent 항목은 재실행 성공 시 지워진다 · 90일 남는 일별 카운터에는 종류만 있었다).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism.ratelimit import classify_http_error as classify


class ClassifyTest(unittest.TestCase):
    def test_insufficient_balance_is_billing(self):
        self.assertEqual(classify(402, '{"error":{"code":"insufficient_balance"}}'), "billing")

    def test_project_limit_is_quota(self):
        self.assertEqual(classify(402, '{"error":{"code":"project_limit_exceeded"}}'), "quota")

    def test_quota_wording_variants(self):
        for b in ('{"code":"limit_exceeded"}', "project spend limit reached", "quota exhausted"):
            self.assertEqual(classify(402, b), "quota", b)

    def test_unknown_402_body_defaults_to_billing(self):
        """구분이 안 되면 더 흔한 쪽(크레딧)으로 둔다 — 조치 안내가 하나는 맞는다."""
        self.assertEqual(classify(402, ""), "billing")
        self.assertEqual(classify(402, "payment required"), "billing")

    def test_other_codes_unchanged(self):
        self.assertEqual(classify(401, ""), "auth")
        self.assertEqual(classify(403, ""), "forbidden")
        self.assertEqual(classify(429, ""), "http_429")


class UiTest(unittest.TestCase):
    def _js(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "prism", "vendor", "app-05-costdata.js"), encoding="utf-8") as f:
            return f.read()

    def test_labels_are_distinct_and_actionable(self):
        s = self._js()
        self.assertIn("billing: '크레딧 부족'", s)
        self.assertIn("quota: '프로젝트 지출 한도'", s)

    def test_legacy_key_kept_for_old_rows(self):
        """2026-07-29 이전 적재분은 http_402 로 남아 있다 — 라벨이 사라지면 안 된다."""
        self.assertIn("http_402:", self._js())

    def test_separate_counters(self):
        s = self._js()
        self.assertIn("get failBillingN()", s)
        self.assertIn("get failQuotaN()", s)

    def test_banner_tells_what_to_do(self):
        from prism import page
        self.assertIn("한도 상향 필요", page.PAGE)
        self.assertIn("충전 필요", page.PAGE)


if __name__ == "__main__":
    unittest.main()
