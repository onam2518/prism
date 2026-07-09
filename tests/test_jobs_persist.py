"""실행 큐 영속 회귀: 배포·재시작에도 실행 이력 유지 + 끊긴 배치는 중단 표시.

배경: _INGEST_STATE 는 메모리 전용이라 Fly 배포(재시작)마다 실행 이력이 사라지고,
실행 중이던 배치는 흔적 없이 증발했다(2026-07-08 회의 소요 E).

실행: python3 -m pytest tests/ -q
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestJobsPersist(unittest.TestCase):
    def test_persist_restore_and_interrupt_marking(self):
        import prism.serve as SV
        from prism.store import Store
        with tempfile.TemporaryDirectory() as d:
            st = Store(os.path.join(d, "t.db"))
            orig_store = SV.get_store
            orig_state = dict(SV._INGEST_STATE)
            SV.get_store = lambda: st
            SV._INGEST_STATE.clear()
            self.addCleanup(lambda: (setattr(SV, "get_store", orig_store),
                                     SV._INGEST_STATE.clear(),
                                     SV._INGEST_STATE.update(orig_state)))

            SV._job_begin("j1", "엑셀", "엑셀 일괄 추출", 10)
            SV._job_end("j1", True, "완료 10건")
            SV._job_begin("j2", "기본 모델", "일괄 실행", 5)   # 실행 중 상태로 재시작 시뮬레이션

            SV._INGEST_STATE.clear()                            # 프로세스 재시작
            SV._jobs_restore()
            s = SV._INGEST_STATE
            self.assertEqual(set(s.keys()), {"j1", "j2"})       # 이력 복원
            self.assertTrue(s["j1"]["last_ok"])                 # 완료 기록 그대로
            self.assertFalse(s["j2"]["running"])                # 유령 진행률 없음
            self.assertIn("중단", s["j2"]["last_msg"])          # 관리자에게 재실행 안내
            self.assertFalse(SV.ingest_status()["running"])     # 배포 가드 신호도 해제

            SV._jobs_restore()                                  # 재호출 멱등(기존 항목 보존)
            self.assertEqual(len(SV._INGEST_STATE), 2)

    def test_persist_caps_history(self):
        import prism.serve as SV
        from prism.store import Store
        with tempfile.TemporaryDirectory() as d:
            st = Store(os.path.join(d, "t.db"))
            orig_store = SV.get_store
            orig_state = dict(SV._INGEST_STATE)
            SV.get_store = lambda: st
            SV._INGEST_STATE.clear()
            self.addCleanup(lambda: (setattr(SV, "get_store", orig_store),
                                     SV._INGEST_STATE.clear(),
                                     SV._INGEST_STATE.update(orig_state)))
            for i in range(25):
                SV._job_begin(f"j{i}", "n", "일괄 실행", 1)
                SV._job_end(f"j{i}", True, "완료")
            snap = st.get_report("jobs")
            self.assertEqual(len(snap), 20)                     # 최근 20건만 영속


if __name__ == "__main__":
    unittest.main()
