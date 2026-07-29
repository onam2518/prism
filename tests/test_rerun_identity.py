"""재실행이 같은 행을 갱신해야 한다(새 행을 만들면 안 된다).

배경(2026-07-29 로컬 실측): 콘텐츠 4건을 재실행하면 6건이 됐다.
콘텐츠 정체성(content_hash)은 '원본' 4필드로 만드는데, 적재되는 payload 의 content_ref 는
파이프라인이 정규화한 본문(끝 공백 제거 등)이었다. 재실행은 그 ref 로 입력을 재구성하므로
원본과 다른 키가 나왔고, upsert 가 갱신 대신 삽입이 됐다.
supabase 는 원본 본문을 컬럼에 담아 되돌려주므로 같은 문제가 없다 — sqlite 를 같은 계약으로.

실행: python3 -m pytest tests/ -q
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RAW_BODY = "본문 " * 5            # 끝 공백 = normalize_text 가 바꾸는 대표 케이스
RAW = {"displayServiceName": "티스토리", "title": "페루 - 푸노", "subtitle": "", "body": RAW_BODY}


def _out(summary="요약"):
    """파이프라인 출력 모사: content_ref 가 정규화본을 들고 있다."""
    from prism.schema import Content
    return {"content_ref": Content.from_dict(RAW).ref(),
            "quality_meta": {"review": "auto", "finalGrade": "G", "reasons": []},
            "item_meta": {"summary": summary}, "trace": {"model": "m1", "version": 1}}


class TestPayloadKeepsIdentity(unittest.TestCase):
    def _store(self):
        from prism.store import Store
        return Store(os.path.join(tempfile.mkdtemp(), "t.db"))

    def test_normalization_would_change_the_key(self):
        """가드가 헛돌지 않는지: 정규화본으로 키를 만들면 실제로 달라진다."""
        from prism.schema import Content
        from prism.store import IDENTITY_FIELDS, content_hash
        ref = Content.from_dict(RAW).ref()
        self.assertNotEqual(ref["body"], RAW_BODY)                   # 끝 공백이 사라진다
        self.assertNotEqual(content_hash({k: ref.get(k, "") for k in IDENTITY_FIELDS}),
                            content_hash(RAW))

    def test_saved_payload_carries_original_identity(self):
        from prism.store import IDENTITY_FIELDS, content_hash
        st = self._store()
        st.save_dedup([(RAW, _out())], "run1")
        ref = st.recent()[0]["content_ref"]
        self.assertEqual(ref["body"], RAW_BODY)                      # 정규화본이 아니라 원본
        self.assertEqual(content_hash({k: ref.get(k, "") for k in IDENTITY_FIELDS}),
                         content_hash(RAW))                          # ref 로 재구성해도 같은 키

    def test_rerun_updates_same_row(self):
        """ref 로 입력을 재구성해 다시 저장 = 재실행 경로. 행이 늘면 안 된다."""
        st = self._store()
        st.save_dedup([(RAW, _out("첫 초안"))], "run1")
        for i in range(3):
            ref = st.recent()[0]["content_ref"]
            fields = {k: ref.get(k, "") for k in
                      ("displayServiceName", "title", "subtitle", "body")}
            st.save_many([(fields, _out(f"재실행 {i}"))], "rerun", source="재실행")
            self.assertEqual(st.count(), 1, f"{i+1}번째 재실행에서 행이 늘었다")
        self.assertEqual(st.recent()[0]["item_meta"]["summary"], "재실행 2")   # 갱신은 반영

    def test_save_result_path_too(self):
        st = self._store()
        st.save_result(RAW, _out(), "run1")
        ref = st.recent()[0]["content_ref"]
        self.assertEqual(ref["body"], RAW_BODY)

    def test_caller_out_object_is_not_mutated(self):
        """payload 는 복사본을 만들어 바꾼다 — 호출자가 들고 있는 out 은 그대로."""
        st = self._store()
        out = _out()
        normalized = out["content_ref"]["body"]
        st.save_dedup([(RAW, out)], "run1")
        self.assertEqual(out["content_ref"]["body"], normalized)

    def test_partial_content_leaves_other_fields(self):
        """content 에 없는 필드는 출력 ref 값을 유지한다(image 트랙 등 부분 입력 대비)."""
        from prism.store import _payload_with_identity
        out = {"content_ref": {"title": "출력제목", "body": "출력본문", "source_url": "u"}}
        got = _payload_with_identity({"title": "원본제목"}, out)["content_ref"]
        self.assertEqual(got["title"], "원본제목")
        self.assertEqual(got["body"], "출력본문")
        self.assertEqual(got["source_url"], "u")


if __name__ == "__main__":
    unittest.main()
