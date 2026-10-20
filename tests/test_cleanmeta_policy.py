import copy
import json
import unittest
from unittest.mock import patch

from prism import cleanmeta as C, cleanmeta_policy as P


def fake(changes=None, missing=(), seen=None):
    def post(payload, key):
        if seen is not None:
            seen.append(copy.deepcopy(payload))
        r = C.mock_post(payload, key)
        for qid, choice in (changes or {}).items():
            if qid in r['answers']:
                a = r['answers'][qid]
                a.update(choice=choice, probabilities={k: float(k == choice) for k in a['probabilities']})
        for qid in missing:
            r['answers'].pop(qid, None)
        return r
    return post


class CommonPolicyTest(unittest.TestCase):
    def test_first_call_can_finish_and_all_respects_provider_limit(self):
        seen = []
        fast = P.judge({'body': '일반 본문'}, 'cascade', fake(seen=seen))
        self.assertEqual(fast['trace']['requests'], 1)
        self.assertEqual(fast['trace']['questions'], 21)
        self.assertEqual(fast['common_status'], 'no_common_issue_found')
        self.assertEqual(len(fast['results']), 15)
        all_result = P.judge({'title': '제목', 'body': '끝까지 검사' * 2600}, 'all', fake(seen=seen))
        self.assertTrue(all(len(p['questions']) <= 100 for p in seen))
        self.assertEqual(all_result['trace']['questions'], 126)
        self.assertEqual(all_result['trace']['requests'], 2)
        self.assertEqual(''.join(s['text'] for s in all_result['segments'] if s['field'] == 'body'), '끝까지 검사' * 2600)
        self.assertEqual({v for g in P.GROUPS.values() for v in g[2]}, {'VL-%02d' % n for n in range(1, 14)})

    def test_mandatory_positive_overrides_negative_screen(self):
        changes = {'violence.threat': 'A', 'violence.threat_evidence': 'A'}
        r = P.judge({'body': '가상의 검수 사례'}, 'cascade', fake(changes))
        self.assertEqual(r['trace']['requests'], 2)
        threat = next(p for p in r['policies'] if p['id'] == 'threat')
        self.assertEqual(threat['status'], 'matched')
        self.assertEqual(threat['action_candidate'], 'urgent_review')
        self.assertEqual(r['common_status'], 'review_required')

    def test_positive_without_evidence_never_matches(self):
        r = P.judge({'body': '가상의 검수 사례'}, 'cascade', fake({'violence.threat': 'A'}))
        self.assertEqual(next(p for p in r['policies'] if p['id'] == 'threat')['status'], 'undetermined')
        a = next(a for a in r['observations'] if a['id'] == 'violence.threat')
        self.assertEqual(a['value'], 'unknown')
        self.assertEqual(a['raw_answer']['choice'], 'A')
        self.assertNotEqual(r['common_status'], 'no_common_issue_found')

    def test_external_claims_in_free_text_do_not_become_confirmed(self):
        r = P.judge({'body': '가상의 성적 유포 사례', 'context': '모두 비동의가 확실하다'}, 'cascade',
                    fake({'sexual.nonconsensual_signal': 'A', 'sexual.nonconsensual_signal_evidence': 'A', 'binding.sexual-nonconsent': 'A'}))
        a = next(a for a in r['observations'] if a['id'] == 'verified.nonconsent')
        self.assertEqual(a['value'], 'unknown')
        self.assertEqual(next(p for p in r['policies'] if p['id'] == 'sexual-nonconsent')['status'], 'undetermined')

    def test_subject_binding_and_confirmed_source_required(self):
        data = {'body': 'A와 B에 관한 검수 사례', 'case_ref': '대상 A', 'reviewed_facts': [
            {'atom_id': 'verified.nonconsent', 'value': 'present', 'source': '가상 검수 기록 1', 'subject_ref': '대상 A'}]}
        changes = {'sexual.nonconsensual_signal': 'A', 'sexual.nonconsensual_signal_evidence': 'A'}
        r = P.judge(data, 'cascade', fake(changes))
        self.assertEqual(next(p for p in r['policies'] if p['id'] == 'sexual-nonconsent')['status'], 'undetermined')
        changes['binding.sexual-nonconsent'] = 'A'
        r = P.judge(data, 'cascade', fake(changes))
        self.assertEqual(next(p for p in r['policies'] if p['id'] == 'sexual-nonconsent')['status'], 'matched')
        bad = copy.deepcopy(data); bad['reviewed_facts'][0]['subject_ref'] = '다른 대상'
        with self.assertRaises(ValueError):
            P.validate(bad)

    def test_confirmed_order_cannot_be_skipped_by_model_screen(self):
        data = {'body': '사례', 'case_ref': '게시물 A', 'reviewed_facts': [
            {'atom_id': 'verified.order', 'value': 'present', 'source': '가상 확인 문서', 'subject_ref': '게시물 A'}]}
        r = P.judge(data, 'cascade', fake({'legal.signal': 'A', 'legal.signal_evidence': 'A', 'binding.official-order': 'A'}))
        self.assertEqual(next(p for p in r['policies'] if p['id'] == 'official-order')['status'], 'matched')

    def test_context_is_review_not_blanket_exception(self):
        r = P.judge({'body': '인용된 가상 위협'}, 'cascade', fake({
            'violence.threat': 'A', 'violence.threat_evidence': 'A', 'context.violence': 'A'}))
        self.assertEqual(next(p for p in r['policies'] if p['id'] == 'threat')['status'], 'undetermined')
        self.assertEqual(r['common_status'], 'review_required')

    def test_age_and_service_do_not_become_common_bans(self):
        r = P.judge({'body': '가상의 광고와 잔혹 묘사'}, 'all', fake({
            'commercial.present': 'A', 'commercial.present_evidence': 'A', 'violence.graphic': 'A', 'violence.graphic_evidence': 'A'}))
        self.assertEqual(r['common_status'], 'no_common_issue_found')
        self.assertEqual({p['layer'] for p in r['policies'] if p['status'] == 'matched'}, {'age', 'service'})

    def test_text_scope_has_no_missing_media_hold(self):
        seen = []
        r = P.judge({'title': '제목', 'body': '정상 본문'}, 'cascade', fake(seen=seen))
        self.assertEqual(r['input_scope'], 'text')
        self.assertEqual(r['coverage']['assessed_parts'], ['body', 'title'])
        self.assertNotIn('missing_parts', r['coverage'])
        self.assertNotIn('unsupported_parts', seen[0]['state'])
        self.assertEqual(r['common_status'], 'no_common_issue_found')
        self.assertEqual(r['status'], 'complete')

    def test_nontext_scope_is_rejected_before_any_model_call(self):
        with patch.object(C, '_engine') as engine:
            for policy in ('common_v1', 'vl13'):
                for part in ('image', 'video', 'audio', 'link', 'history'):
                    r = C.try_one({'body': '본문', 'policy_set': policy, 'requested_parts': ['body', part]})
                    self.assertFalse(r['ok'])
                    self.assertIn('텍스트만', r['error'])
            engine.assert_not_called()

    def test_missing_answer_and_total_failure(self):
        r = P.judge({'body': '본문'}, 'cascade', fake(missing=['route.child']))
        self.assertNotEqual(r['common_status'], 'no_common_issue_found')
        self.assertIn('route.child', r['errors'])
        seen = []
        def fail(payload, key):
            seen.append(payload)
            raise RuntimeError('PRIVATE secret')
        r = P.judge({'body': '본문'}, 'cascade', fail)
        self.assertEqual(len(seen), 1)
        self.assertEqual(r['status'], 'failed')
        self.assertNotIn('PRIVATE', json.dumps(r))

    def test_shared_questions_once_and_custom_prompts_in_correct_stage(self):
        seen = []
        prompts = {'screen': '첫 단계 실험 지침', 'detail': '상세 단계 실험 지침'}
        data = {'body': '본문', 'prompts': prompts}
        r = P.judge(data, 'cascade', fake({'route.fraud': 'A', 'route.civic': 'A'}, seen=seen))
        self.assertTrue(all('첫 단계 실험 지침' in q['instructions'] for q in seen[0]['questions'].values()))
        self.assertTrue(all('상세 단계 실험 지침' in q['instructions'] for q in seen[1]['questions'].values()))
        self.assertEqual(sum('claim.factual' in p['questions'] for p in seen), 1)
        self.assertEqual(r['prompts'], prompts)
        self.assertTrue(r['custom_prompts'])
        self.assertNotEqual(r['prompt_hash'], P.judge({'body': '본문'}, 'cascade', fake())['prompt_hash'])

    def test_validate_before_model_and_defaults_without_model(self):
        with patch.object(C, '_engine') as engine:
            self.assertTrue(C.try_one({'action': 'defaults'})['ok'])
            for extra in [{'prompts': {'screen': 'x'}}, {'prompts': {'screen': '', 'detail': 'x'}},
                          {'requested_parts': ['fake']}, {'reviewed_facts': [{'atom_id': 'fake'}]}]:
                r = C.try_one({'body': '본문', 'policy_set': 'common_v1', **extra})
                self.assertFalse(r['ok'])
            engine.assert_not_called()

    def test_public_route_result_preserves_model_and_prompt_provenance(self):
        with patch.object(C, '_engine', return_value=(C.mock_post, '', True, 'jev', 'solar')):
            r = C.try_one({'body': '본문', 'policy_set': 'common_v1', 'mode': 'compare'})
        self.assertTrue(r['ok'])
        self.assertEqual(r['type_count'], 15)
        self.assertEqual(r['actual_action'], 'none')
        self.assertEqual(r['all_question_count'], 126)
        self.assertTrue(all(run['score_source'] == 'mock' for run in r['runs']))
        self.assertEqual(r['runs'][0]['prompt_hash'], r['runs'][1]['prompt_hash'])


if __name__ == '__main__':
    unittest.main()
