"""조회용 벡터가 조건·팀·원문 수정·장애 처리의 경계를 넘지 않는지 확인."""
import copy
import os
import unittest
from unittest.mock import patch

from prism import topicvectors as V


def row(title, *, service="뉴스", status=""):
    return {"content_ref": {"title": title, "body": title + " 본문", "displayServiceName": service},
            "item_meta": {"entities": ["삼성전자"], "intent": ["심층 분석"],
                          "content_category": ["Business and Finance"]},
            "quality_meta": {}, "src": {"status": status}}


class TopicVectors(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"PRISM_QDRANT_URL": "http://test.internal", "PRISM_QDRANT_KEY": "test"})
        self.env.start()
        self.addCleanup(self.env.stop)
        V._queries.clear()

    def test_point_tracks_team_full_input_and_restrictions(self):
        r = row("기사")
        self.assertNotEqual(V._point(r, "A")[0], V._point(r, "B")[0])
        old = V._point(r, "A")[0]
        changed = copy.deepcopy(r)
        changed["content_ref"]["body"] += " 새 사실"
        self.assertNotEqual(old, V._point(changed, "A")[0])
        self.assertIsNone(V._point(row("삭제", status="DELETE"), "A"))
        self.assertIsNone(V._point(row("카페", service="다음카페"), "A"))
        prefix = row("긴 기사")
        prefix["content_ref"]["body"] = "가" * 8000
        tail = copy.deepcopy(prefix)
        tail["content_ref"]["body"] += "수정"
        self.assertNotEqual(V._point(prefix, "A")[0], V._point(tail, "A")[0])

    def test_index_results_cannot_add_excluded_or_stale_content(self):
        rows = [row("허용"), row("제외")]
        pid = V._point(rows[0], "A")[0]
        calls = []

        def request(method, path, body):
            calls.append(body)
            if path.endswith('/query'):
                return {"points": [{"id": V._point(rows[1], "A")[0], "score": .99},
                                   {"id": pid, "score": .8}, {"id": "stale", "score": .9}]}
            return [{"id": pid}]

        with patch.object(V, '_request', side_effect=request), patch.object(V, '_embedding_client') as client:
            client.return_value._api_embed.return_value = [1., 0.]
            found, info = V.rank("검색", rows, [0], "A")
        self.assertEqual(found, [{"i": 0, "semantic_score": .8}])
        self.assertEqual(calls[-1]["filter"]["must"],
                         [{"key": "team", "match": {"value": "A"}}, {"has_id": [pid]}])
        self.assertEqual(info["matched"], 1)

    def test_partial_index_is_reported_and_fills_in_background(self):
        rows = [row("준비"), row("신규")]
        pid = V._point(rows[0], "A")[0]
        with patch.object(V, '_existing', return_value={pid}), patch.object(V, '_background_sync') as sync, \
                patch.object(V, '_embedding_client') as client, patch.object(V, '_request', return_value={"points": [{"id": pid, "score": .7}]}):
            client.return_value._api_embed.return_value = [1., 0.]
            found, info = V.rank("부분", rows, [0, 1], "A")
        self.assertEqual(info, {"status": "partial", "matched": 2, "eligible_news": 2, "indexed": 1})
        self.assertEqual(len(found), 1)
        sync.assert_called_once_with(rows, "A")

    def test_unavailable_or_invalid_vector_uses_basic_samples(self):
        with patch.object(V, '_existing', side_effect=TimeoutError):
            found, info = V.rank("검색", [row("기사")], [0], "A")
        self.assertEqual(found, [])
        self.assertEqual(info["status"], "unavailable")
        for vector in ([float('nan')], [0.], [True], []):
            with self.assertRaises(ValueError):
                V._valid_vector(vector)

    def test_preview_keeps_counts_and_explicit_filter(self):
        from prism import topicops as T
        rows = [row("허용1"), row("허용2"), row("다른 분야")]
        rows[2]["item_meta"]["content_category"] = ["Sports"]
        from types import SimpleNamespace
        sv = SimpleNamespace(results_rows=lambda **kw: rows, _detail_row=lambda r: {"title": r["content_ref"]["title"]})
        with patch.object(T, '_SV', sv), patch.object(T, '_ent_keys', return_value={}), \
                patch.object(V, 'rank', return_value=([{"i": 1, "semantic_score": .8}], {"status": "ready"})) as rank:
            result = T.topic_studio_action({"action": "preview", "semantic": True,
                "def": {"name": "경제", "prompt": "경제 뉴스", "cats": ["Business and Finance"]}}, team="A")
        core = result["preview"]["bundles"][0]
        self.assertEqual(core["count"], 2)
        self.assertEqual(rank.call_args.args[2], [0, 1])
        self.assertEqual(core["samples"][0]["title"], "허용2")
        self.assertEqual(core["samples"][0]["semantic_score"], .8)


if __name__ == '__main__':
    unittest.main()
