"""운영 알림(alerts) 회귀 테스트.

- 웹훅 미설정 시 전 기능 무동작(무비용 계약)
- notify 쿨다운: 같은 키 연속 발송 차단
- on_fail: 1시간 윈도 누적 임계(PRISM_ALERT_FAIL_N) 도달 시에만 발송
- on_cost: 임계 미설정이면 무동작 · 초과 시 일 1회만 발송
- 발송 실패(네트워크 예외)를 삼키는지

실행: python3 -m pytest tests/test_alerts.py -q
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import alerts as AL


def _reset():
    AL._LAST_SENT.clear()
    AL._FAILS.clear()
    AL._COST_ALERTED_DAY = ""


class TestAlerts(unittest.TestCase):
    def setUp(self):
        _reset()
        self._old = {k: os.environ.pop(k, None)
                     for k in ("PRISM_ALERT_WEBHOOK", "PRISM_ALERT_FAIL_N", "PRISM_ALERT_COST_USD")}
        self.sent = []
        patcher = mock.patch.object(AL.urllib.request, "urlopen",
                                    side_effect=lambda req, timeout=5: self.sent.append(req) or mock.MagicMock())
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset()

    def test_disabled_without_webhook(self):
        self.assertFalse(AL.enabled())
        self.assertFalse(AL.notify("k", "x"))
        AL.on_fail(100)
        AL.on_cost("2026-07-17", 999.0)
        self.assertEqual(self.sent, [])           # 미설정 → 발송 0

    def test_notify_cooldown(self):
        os.environ["PRISM_ALERT_WEBHOOK"] = "https://hooks.slack.example/x"
        self.assertTrue(AL.notify("k", "첫 발송"))
        self.assertFalse(AL.notify("k", "쿨다운 내 재발송"))
        self.assertTrue(AL.notify("k2", "다른 키는 즉시"))
        self.assertEqual(len(self.sent), 2)

    def test_fail_threshold(self):
        os.environ["PRISM_ALERT_WEBHOOK"] = "https://hooks.slack.example/x"
        os.environ["PRISM_ALERT_FAIL_N"] = "5"
        AL.on_fail(4)
        self.assertEqual(self.sent, [])           # 임계 미만
        AL.on_fail(1, kind="parse", model="solar-pro2")
        self.assertEqual(len(self.sent), 1)       # 누적 5 → 발송
        AL.on_fail(10)
        self.assertEqual(len(self.sent), 1)       # 쿨다운(1시간) 내 재발송 차단

    def test_cost_threshold_daily_once(self):
        os.environ["PRISM_ALERT_WEBHOOK"] = "https://hooks.slack.example/x"
        AL.on_cost("2026-07-17", 50.0)
        self.assertEqual(self.sent, [])           # 임계 미설정 → 무동작
        os.environ["PRISM_ALERT_COST_USD"] = "10"
        AL.on_cost("2026-07-17", 5.0)
        self.assertEqual(self.sent, [])           # 미만
        AL.on_cost("2026-07-17", 12.0)
        self.assertEqual(len(self.sent), 1)       # 초과 → 발송
        AL.on_cost("2026-07-17", 20.0)
        self.assertEqual(len(self.sent), 1)       # 같은 날 재발송 차단

    def test_send_failure_swallowed(self):
        os.environ["PRISM_ALERT_WEBHOOK"] = "https://hooks.slack.example/x"
        with mock.patch.object(AL.urllib.request, "urlopen", side_effect=OSError("net down")):
            self.assertFalse(AL.notify("k9", "x"))   # 예외 미전파


if __name__ == "__main__":
    unittest.main()
