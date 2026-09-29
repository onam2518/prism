"""정책 감사의 실패 경계: 삭제 재편입, 객체 왕복, 무입력, 학습 오염, 원천 제공 여부."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from prism import agents, topic, metaeval, meta_contract as MC
from prism.schema import Content


class TestPolicyCompletion(unittest.TestCase):
    def row(self, status=None):
        return {"content_ref": {"title": "대상", "body": "본문"},
                "src": {"status": status} if status else {},
                "quality_meta": {"finalGrade": "G"},
                "item_meta": {"entities": [{"name": "축구", "type": "TM"}],
                              "intent": ["경기 결과·리뷰"],
                              "content_category": [{"tier1": "Sports", "tier2": "Soccer (International)"}]}}

    def test_deleted_cannot_be_manually_included(self):
        rows = [self.row("DELETE")]
        pool = {"content_ids": [], "count": 0}
        topic._apply_inclusion(pool, "topic", rows, ["hash"], {"topic": {"hash"}})
        self.assertEqual(pool["content_ids"], [])
        self.assertFalse(topic._eligible(rows[0]))
        self.assertFalse(topic._feed_pass(topic.feed_fields(rows[0]), {"base_excl": False}, 100)[0])

    def test_today_is_kst_calendar_and_missing_date_is_unknown(self):
        now = 1780000000
        start = ((now + 32400) // 86400) * 86400 - 32400
        self.assertTrue(topic._feed_pass({"org_ts": start}, {"days": 1}, now)[0])
        self.assertFalse(topic._feed_pass({"org_ts": start - 1}, {"days": 1}, now)[0])
        self.assertFalse(topic._feed_pass({"org_ts": now + 1}, {"days": 14}, now)[0])
        self.assertEqual(topic._feed_pass({"ingest_ts": now - 1}, {"days": 14}, now), (False, "days"))

    def test_object_metadata_is_matched_and_scored(self):
        row = self.row()
        pv = topic.preview_definition([row], set(), {"cats": ["Sports"], "keywords": ["축구"]})
        self.assertEqual(pv["bundles"][0]["content_ids"], [0])
        acc = {}
        metaeval.meta_tally(acc, row["item_meta"], row)
        self.assertEqual((acc["ent_n"], acc["ent_f1_sum"], acc["cat_n"], acc["cat_f1_sum"]), (1, 1, 1, 1))

    def test_empty_input_does_not_call_model(self):
        llm = SimpleNamespace(complete_json=lambda *a, **kw: self.fail("empty input called model"))
        with patch.dict(agents.META_CFG, {"four_calls": True}):
            im, results = agents.run_item(llm, Content.from_dict({"title": " ", "body": None}))
        self.assertEqual(set(im.meta_status.values()), {"insufficient_input"})
        self.assertEqual(results, [])

    def test_raw_source_presence_survives_table_and_content(self):
        from prism.ingest import to_contents_rows
        rows, _ = to_contents_rows([{"title": "제목", "service_code": None, "cp_type": "", "item_unique_key": " Hamny-a "}])
        ref = Content.from_dict(rows[0]).ref()
        self.assertIsNone(ref["source_fields"]["service_code"])
        self.assertEqual(ref["source_fields"]["cp_type"], "")
        self.assertEqual(ref["source_fields"]["item_unique_key"], " Hamny-a ")
        self.assertNotIn("service_code", Content.from_dict({"title": "제목"}).ref()["source_fields"])
        self.assertEqual(ref["input_aux"], {"image_count": {"provided": False}})
        image_rows, _ = to_contents_rows([{"title": "제목", "image_urls": None}])
        self.assertEqual(Content.from_dict(image_rows[0]).input_aux, {"image_count": {"provided": True, "value": None}})

    def test_sft_excludes_unconfirmed_and_retired_intent(self):
        from prism.learnops import _training_expected
        for exp in ({"summary": "정답", "intent": ["심층 분석"], "intent_review": "needed"},
                    {"summary": "정답", "intent": ["의견·토론"], "intent_confirmed_by": "reviewer"}):
            self.assertEqual(_training_expected(exp), {"summary": "정답"})

    def test_no_value_is_evaluated_for_false_positives(self):
        acc = {}
        metaeval.meta_tally(acc, {"entities": [], "meta_status": {"entities": "no_value"}}, self.row())
        self.assertEqual((acc["ent_n"], acc["ent_f1_sum"]), (1, 0))

    def test_invalid_objects_are_not_stringified(self):
        self.assertEqual(MC.clean_entities([{"name": 123, "type": "PS"}]), [])
        self.assertEqual(MC.clean_entities([{"name": "사람", "type": []}]), [])
        self.assertEqual(MC.clean_categories([{"tier1": "not a category", "tier2": None}]), [])

    def test_nested_condition_keeps_pairing_and_unknown_negation(self):
        from prism.topic_conditions import sanitize, evaluate
        expr = sanitize({"any": [
            {"all": [{"field": "entities", "value": "한화"}, {"field": "intent", "value": "경기 결과·리뷰"}]},
            {"all": [{"field": "entities", "value": "LG"}, {"field": "intent", "value": "경기 프리뷰"}]}]})
        row = {"item_meta": {"entities": ["한화"], "intent": ["경기 프리뷰"]}}
        self.assertFalse(evaluate(expr, row))
        row["item_meta"]["intent"] = ["경기 결과·리뷰"]
        self.assertTrue(evaluate(expr, row))
        neg = sanitize({"not": {"field": "intent", "value": "경기 결과·리뷰"}})
        row["item_meta"]["meta_status"] = {"intent": "pending"}
        self.assertIsNone(evaluate(neg, row))

    def test_same_axis_values_are_or(self):
        row = self.row()
        pv = topic.preview_definition([row], set(), {"cats": ["Sports", "Entertainment"],
                                                     "req": {"cats": ["Sports", "Entertainment"]}})
        self.assertEqual(pv["bundles"][0]["content_ids"], [0])

    def test_manual_value_survives_automatic_storage(self):
        from prism import serve
        from prism.store import Store, content_hash
        with tempfile.TemporaryDirectory() as td:
            st = Store(str(Path(td) / 'db'))
            content = {"title": "대상", "body": "본문", "displayServiceName": ""}
            first = {"item_meta": {"summary": "기계", "input_revision": "r1"}, "quality_meta": {}}
            st.save_many([(content, first)], "first")
            with patch.object(serve, '_STORE', st):
                self.assertTrue(serve.patch_content_meta(content_hash(content), {"summary": "수동"}, reviewer="tester")["ok"])
            st.save_many([(content, first)], "again")
            self.assertEqual(st.get_item_meta(content_hash(content))["summary"], "수동")
            changed = copy.deepcopy(first)
            changed['item_meta']['input_revision'] = 'r2'
            st.save_many([(content, changed)], "changed")
            im = st.get_item_meta(content_hash(content))
            self.assertEqual(im['summary'], '수동')
            self.assertIn('summary', im['manual_review_required'])
            self.assertEqual(im['meta_status']['summary'], 'pending')

    def test_golden_overwrite_and_delete_preserve_history(self):
        from prism.store import Store
        with tempfile.TemporaryDirectory() as td:
            st = Store(str(Path(td) / 'db'))
            content = {"title": "대상"}
            st.upsert_golden('h', content, {"summary": "처음"})
            st.upsert_golden('h', content, {"summary": "수정"})
            st.remove_golden('h')
            history = [json.loads(r[0])['summary'] for r in st._conn().execute('SELECT expected FROM golden_history ORDER BY id')]
            self.assertEqual(history, ['처음', '수정'])

    def test_common_meta_has_no_quality_denominator(self):
        from prism.abtest import score
        row = {"content": {"title": "대상"}, "expected": {"entities": [{"name": "축구", "type": "TM"}]}}
        out = self.row()
        out['trace'] = {}
        report = score([row], [out])
        self.assertEqual(report['grade_n'], 0)
        self.assertIsNone(report['grade_accuracy'])
        self.assertEqual(report['ent_n'], 1)

    def test_supabase_source_fields_roundtrip_without_network(self):
        from prism.supastore import SupabaseStore
        st = object.__new__(SupabaseStore)
        captured = []
        st._get = lambda *args, **kwargs: []
        st._upsert = lambda table, rows: captured.extend(rows)
        content = {"title": "제목", "body": "", "displayServiceName": "뉴스", "item_unique_key": "hamny-12", "cp_type": None}
        st.sync_contents([(content, {"item_meta": {}, "quality_meta": {}})], include_all=True)
        raw = captured[0]
        self.assertIsNone(raw['source_fields']['cp_type'])
        self.assertNotIn('service_code', raw['source_fields'])
        st._get = lambda *args, **kwargs: [raw]
        ref = st.recent()[0]['content_ref']
        self.assertEqual(ref['item_unique_key'], 'hamny-12')
        self.assertEqual(ref['source_fields'], raw['source_fields'])

    def test_image_presence_changes_revision_without_service_dependency(self):
        from prism.meta_prompts import call_user
        missing = Content.from_dict({'title': '대상'})
        zero = Content.from_dict({'title': '대상', 'image_urls': []})
        null = Content.from_dict({'title': '대상', 'image_urls': None})
        self.assertIn('정보 없음', call_user('intent', missing))
        self.assertIn('이미지 수: 0', call_user('intent', zero))
        self.assertNotEqual(null.input_aux, missing.input_aux)
        self.assertEqual(call_user('summary', missing), call_user('summary', zero))

    def test_source_key_status_does_not_infer_scope(self):
        from prism.schema import source_key_status
        cases = [(None, 'missing'), (' ', 'missing'), ('hamny-', 'invalid'),
                 (' hamny-a', 'invalid'), ('hamny-a-b', 'recognized'), ('other-a', 'unregistered')]
        for value, expected in cases:
            self.assertEqual(source_key_status(value), expected)
        self.assertNotIn('scope_status', Content.from_dict({'item_unique_key': 'hamny-a'}).ref())
