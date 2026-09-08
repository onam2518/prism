"""토픽 편입(2026-09-08): 유통 불가 콘텐츠는 사후 편입만 되고 형성·활성 판정·대표·cluster_id 는 유통 가능분 기준."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _row(title, ents, grade="G", review="auto"):
    return {"content_ref": {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": title},
            "quality_meta": {"finalGrade": grade, "reasons": [], "review": review},
            "item_meta": {"summary": title, "entities": list(ents), "intent": ["속보·단신"],
                          "content_category": ["News and Politics / Society"], "hold_fields": []}}


class TestAttachNondist(unittest.TestCase):
    def _rows(self):
        return [_row("a", ["삼성전자", "총파업"]), _row("b", ["삼성전자", "총파업"]),
                _row("c", ["삼성전자", "총파업"], grade="R"),          # 유통 불가
                _row("d", ["삼성전자"], grade="", review="yellow")]     # 판정 보류

    def test_entity_topic_counts_only_distributable(self):
        from prism import topic as T
        rows = self._rows(); svc = T._service_names(rows)
        pools = T.build_entity_topics(rows, svc, {}, min_contents=2)
        p = next(x for x in pools if x["name"] == "삼성전자")
        self.assertEqual(p["count"], 2)                      # G 2건만 활성 판정 기준
        self.assertEqual(p["content_ids"], [0, 1])
        T.attach_nondist(pools, rows, svc, co_min=1)
        self.assertEqual(p["count"], 2)                      # 편입해도 count 불변
        self.assertEqual(p["content_ids"], [0, 1])           # 제외·드릴다운 키도 불변
        self.assertEqual(sorted(p["nondist_ids"]), [2, 3])
        self.assertEqual(p["nondist_n"], 2)

    def test_cluster_id_and_rep_unchanged(self):
        from prism import topic as T
        rows = self._rows(); svc = T._service_names(rows)
        comp = T.build_event_topics(rows, svc, co_min=2)
        if not comp:
            self.skipTest("사건 토픽 미형성")
        before = [(p["cluster_id"], p["representative_content"], p["count"]) for p in comp]
        T.attach_nondist(comp, rows, svc, co_min=2)
        after = [(p["cluster_id"], p["representative_content"], p["count"]) for p in comp]
        self.assertEqual(before, after)                      # 형성 결과는 그대로

    def test_no_nondist_rows_is_noop(self):
        from prism import topic as T
        rows = [_row("a", ["삼성전자"]), _row("b", ["삼성전자"])]
        svc = T._service_names(rows)
        pools = T.build_entity_topics(rows, svc, {}, min_contents=2)
        T.attach_nondist(pools, rows, svc, co_min=1)
        self.assertNotIn("nondist_n", pools[0])


if __name__ == "__main__":
    unittest.main()
