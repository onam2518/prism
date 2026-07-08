"""새 버전 감지(부팅 ID) 회귀: 배포 후 구버전 페이지가 조용히 남는 문제의 방지 장치.

- 서버 부팅 ID 를 /config·SSE hello 로 노출 → 클라이언트가 변화를 감지해 새로고침 배너 표시
- 페이지의 벤더 js/css 링크에 ?v=부팅ID 캐시버스터 → 새로고침만으로 새 스크립트 로드(강력 새로고침 불필요)

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestUpdateNotify(unittest.TestCase):
    def test_boot_id_and_versioned_page(self):
        import prism.serve as SV
        self.assertTrue(SV._BOOT_ID)
        page = SV._page_versioned()
        self.assertIn("/vendor/app.js?v=" + SV._BOOT_ID, page)
        self.assertIn("/vendor/app.css?v=" + SV._BOOT_ID, page)
        self.assertNotIn('src="/vendor/app.js"', page)              # 버전 없는 링크 잔존 금지
        self.assertIn("updateAvail", page)                          # 새 버전 배너 마크업


if __name__ == "__main__":
    unittest.main()
