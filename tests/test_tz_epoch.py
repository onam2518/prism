"""timestamptz UTC 해석 회귀 테스트(2026-07-15 감사 P1-17).

저장은 UTC(gmtime)로 하는데 읽기가 time.mktime(로컬 해석)이면 비UTC 호스트(KST 등)에서
주간창·스트릭·'현재 초안 이후 검수' 판정이 스큐된다. calendar.timegm 으로 UTC 해석해야 한다.

이 테스트는 TZ=Asia/Seoul 을 강제해 UTC 호스트(CI)에서도 회귀를 잡는다.

실행: python3 -m pytest tests/test_tz_epoch.py -q
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_FIXED_TS = 1784000000        # 고정 UTC epoch(2026-07-15 무렵)


class TestUtcEpoch(unittest.TestCase):
    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Seoul"       # UTC+9 강제 → mktime(로컬) 사용 시 9h 스큐 노출
        if hasattr(time, "tzset"):
            time.tzset()

    def tearDown(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        if hasattr(time, "tzset"):
            time.tzset()

    def _utc_str(self, ts):
        return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts))

    def test_serve_fb_epoch_utc_roundtrip(self):
        from prism import serve as SV
        s = self._utc_str(_FIXED_TS)          # save_feedback 이 쓰는 것과 동일 형식
        self.assertEqual(int(SV._fb_epoch(s)), _FIXED_TS)   # KST 호스트에서도 정확 복원

    def test_supastore_epoch_utc_roundtrip(self):
        from prism import supastore as SS
        s = self._utc_str(_FIXED_TS)
        self.assertEqual(int(SS._epoch(s)), _FIXED_TS)

    def test_two_paths_agree(self):
        from prism import serve as SV
        from prism import supastore as SS
        s = self._utc_str(_FIXED_TS)
        self.assertEqual(int(SV._fb_epoch(s)), int(SS._epoch(s)))  # 두 해석 경로 일치

    def test_fb_epoch_float_passthrough(self):
        from prism import serve as SV
        self.assertEqual(SV._fb_epoch(1234.5), 1234.5)        # sqlite float 경로 불변


if __name__ == "__main__":
    unittest.main()
