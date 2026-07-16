"""게시판 문의 답변 회귀 테스트 (게시판 답변 기능).

관리자가 게시판 글에 답변을 달면 저장되고 목록/조회에 실린다.

실행: python3 -m pytest tests/test_board_answer.py -q
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestBoardAnswer(unittest.TestCase):
    def _store(self):
        from prism.store import Store
        return Store(os.path.join(tempfile.mkdtemp(), "t.db"))

    def test_answer_stored_and_listed(self):
        st = self._store()
        bid = st.board_add("bug", "검수 저장이 멈춰요", "본문", "A", team="")
        self.assertTrue(bid)
        # 초기엔 답변 없음
        self.assertEqual(st.board_get(bid, team="")["answer"], "")
        # 답변 저장
        self.assertTrue(st.board_answer(bid, "재현 확인했고 다음 배포에 반영합니다", team=""))
        got = st.board_get(bid, team="")
        self.assertEqual(got["answer"], "재현 확인했고 다음 배포에 반영합니다")
        self.assertGreater(got["answered_at"], 0)
        # 목록에도 실림
        self.assertEqual(st.board_list(team="")[0]["answer"], "재현 확인했고 다음 배포에 반영합니다")

    def test_answer_overwrite_and_missing(self):
        st = self._store()
        bid = st.board_add("feature", "다크모드 원해요", "", "B", team="")
        st.board_answer(bid, "첫 답변", team="")
        st.board_answer(bid, "수정된 답변", team="")
        self.assertEqual(st.board_get(bid, team="")["answer"], "수정된 답변")
        self.assertFalse(st.board_answer(99999, "없는 글", team=""))   # 없는 id → False


if __name__ == "__main__":
    unittest.main()
