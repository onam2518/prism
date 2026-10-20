import copy
import json
import unittest
from unittest.mock import patch
from prism import cleanmeta as CM


def fake(changes=None, missing=()):
    def post(payload, key):
        response = CM.mock_post(payload, key)
        for qid, choice in (changes or {}).items():
            if qid in response['answers']:
                ans = response['answers'][qid]
                ans.update(choice=choice, probabilities={k: float(k == choice) for k in ans['probabilities']})
        for qid in missing:
            response['answers'].pop(qid, None)
        return response
    return post


class CleanMetaTest(unittest.TestCase):
    def test_calls_and_multiple_types(self):
        data = {'body': '가상의 사례입니다.'}
        whole = CM.judge(data, 'all', fake())
        normal = CM.judge(data, 'cascade', fake())
        multi = CM.judge(data, 'cascade', fake({'route_privacy_violation': 'A', 'route_insult': 'C'}))
        self.assertEqual((whole['trace']['requests'], whole['trace']['questions']), (1, 88))
        self.assertEqual((normal['trace']['requests'], normal['trace']['questions']), (1, 13))
        self.assertEqual((multi['trace']['requests'], multi['trace']['questions']), (2, 24))
        self.assertEqual({x['type'] for x in multi['results'] if x['recommendation'] == 'review'}, {'privacy_violation', 'insult'})
        self.assertEqual(normal['recommendation'], 'pass_candidate')
        self.assertIsNone(whole['trace']['estimated_input_cost_usd'])

    def test_original_coverage_and_evidence(self):
        data = {'title': '제목', 'body': '앞부분\n' + '본문' * 7990 + '마지막', 'input_price': 1.5}
        seen = []
        def post(payload, key):
            seen.append(copy.deepcopy(payload))
            return fake({'route_privacy_violation': 'A', 'privacy_violation_evidence': 'Y'})(payload, key)
        result = CM.judge(data, 'cascade', post)
        spans = seen[0]['state']['source_segments']
        self.assertEqual(''.join(s['text'] for s in spans if s['field'] == 'body'), data['body'])
        for span in spans:
            self.assertEqual(span['text'], data[span['field']][span['start']:span['end']])
        self.assertEqual(seen[0]['state'], seen[1]['state'])
        self.assertEqual(next(x for x in result['results'] if x['type'] == 'privacy_violation')['evidence'], spans[-1])
        self.assertEqual(len(seen[1]['questions']['privacy_violation_evidence']['criteria']), 26)
        self.assertEqual(result['trace']['estimated_input_cost_usd'], 0.00285)

    def test_missing_or_malformed_response_never_passes(self):
        for corrupt in [lambda a: a.pop('probabilities'),
                        lambda a: a['probabilities'].update(B=float('nan')),
                        lambda a: a.update(choice='A'),
                        lambda a: a['probabilities'].update(B=True)]:
            def post(payload, key):
                response = fake()(payload, key)
                corrupt(response['answers']['route_privacy_violation'])
                return response
            result = CM.judge({'body': '본문'}, 'cascade', post)
            self.assertEqual(result['recommendation'], 'hold')
            self.assertEqual(result['status'], 'partial')
        result = CM.judge({'body': '본문'}, 'cascade', fake({'route_privacy_violation': 'A'}, ['privacy_violation_consent']))
        self.assertEqual(result['recommendation'], 'hold')
        self.assertIn('privacy_violation_consent', result['errors'])

    def test_exception_is_sanitized_and_no_retry(self):
        calls = []
        def post(payload, key):
            calls.append(payload)
            raise RuntimeError('SECRET BODY AND KEY')
        result = CM.judge({'body': '본문'}, 'cascade', post)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertIsNone(result['trace']['input_tokens'])

    def test_conflict_and_uncertainty(self):
        result = CM.judge({'body': '본문'}, 'all', fake({'privacy_violation_data': 'A'}))
        self.assertEqual(next(x for x in result['results'] if x['type'] == 'privacy_violation')['recommendation'], 'review')
        def uncertain(payload, key):
            result = fake()(payload, key)
            if 'route_insult' in result['answers']:
                result['answers']['route_insult']['probabilities'] = {'A': 0.2, 'B': 0.6, 'C': 0.2}
            return result
        result = CM.judge({'body': '본문'}, 'cascade', uncertain)
        self.assertEqual(result['trace']['requests'], 2)
        self.assertEqual(result['results'][1]['recommendation'], 'review')

    def test_validation_and_compare(self):
        for data in [[], {}, {'body': 'x' * 16001}, {'body': 'x', 'gate': True},
                     {'body': 'x', 'input_price': float('inf')}, {'body': 'x', 'mode': 'unknown'}, {'body': 'x', 'model': None}, {'body': 'x', 'model': 'bad model'}]:
            self.assertFalse(CM.try_one(data)['ok'])
        with patch.object(CM.DC, '_env', return_value=(CM.DC._mock_post, '', '')):
            result = CM.try_one({'body': '본문'})
        self.assertTrue(result['mock'])
        self.assertEqual(len(result['runs']), 2)
        self.assertEqual(result['actual_action'], 'none')
        self.assertEqual(result['storage'], 'browser_only')
        self.assertEqual(result['runs'][0]['input_hash'], result['runs'][1]['input_hash'])

    def test_route_permission_and_invalid_json(self):
        from prism import serve
        self.assertEqual(serve._POST_ROUTES['/lab-cleanmeta-try'][1], 'super')
        self.assertFalse(serve._p_lab_cleanmeta_try(None, b'{bad')['ok'])
        with patch.object(CM.DC, '_env', return_value=(CM.DC._mock_post, '', '')):
            self.assertTrue(serve._p_lab_cleanmeta_try(None, b'{"body":"sample"}')['ok'])


class CleanMetaHTTPTest(unittest.TestCase):
    def test_http_route_gates_and_mock_result(self):
        from contextlib import ExitStack
        from http.server import ThreadingHTTPServer
        import threading
        import urllib.request
        import urllib.error
        from prism import serve
        with ExitStack() as stack:
            stack.enter_context(patch.object(serve, 'menu_allowed', return_value=True))
            stack.enter_context(patch.object(serve, '_supa', return_value=('test', 'test')))
            stack.enter_context(patch.object(serve.Handler, '_bearer_uid', return_value='test-user'))
            stack.enter_context(patch.object(serve.Handler, '_bearer_email', return_value='test@example.test'))
            stack.enter_context(patch.object(serve.Handler, '_req_team', return_value='test-team'))
            allowed = stack.enter_context(patch.object(serve, 'is_super_admin_user', return_value=False))
            model = stack.enter_context(patch.object(CM.DC, '_env', return_value=(CM.DC._mock_post, '', '')))
            server = ThreadingHTTPServer(('127.0.0.1', 0), serve.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                req = urllib.request.Request('http://127.0.0.1:%s/lab-cleanmeta-try' % server.server_port,
                    data=b'{"body":"synthetic example","mode":"compare"}',
                    headers={'Content-Type':'application/json'}, method='POST')
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(req)
                self.assertEqual(denied.exception.code, 403)
                denied.exception.close()
                model.assert_not_called()
                allowed.return_value = True
                with urllib.request.urlopen(req) as response:
                    result = json.load(response)
                self.assertTrue(result['ok'])
                self.assertTrue(result['mock'])
                self.assertEqual([r['trace']['requests'] for r in result['runs']], [1, 1])
            finally:
                server.shutdown(); server.server_close(); thread.join()


class CleanMetaExpandedTest(unittest.TestCase):
    def test_all_thirteen_types_and_question_limit(self):
        from prism.dictionaries import LEGAL_HARM_TYPES
        self.assertEqual(set(CM.TYPES), set(LEGAL_HARM_TYPES))
        self.assertEqual({v[0] for v in CM.PROFILES.values()}, {'VL-%02d' % n for n in range(1, 14)})
        questions = {**CM.route_questions(), **CM.detail_questions(CM.TYPES, CM.segments('제목', 'x' * 16000))}
        self.assertEqual(len(questions), CM.QUESTION_COUNT)
        self.assertLessEqual(len(questions), CM.DC.MAX_QUESTIONS)
        self.assertTrue(all(len(q['criteria']) <= 26 for q in questions.values()))
        out = CM.judge({'body': 'synthetic'}, 'cascade', fake({'route_' + k: 'A' for k in CM.TYPES}))
        self.assertEqual(len(out['results']), 13)
        self.assertEqual(out['trace']['questions'], 88)
        self.assertEqual(out['trace']['requests'], 2)
        self.assertTrue(all(r['detail_status'] == 'requested' for r in out['results']))

    def test_new_types_unknown_or_failure_cannot_pass(self):
        for kind in set(CM.TYPES) - {'privacy_violation', 'insult', 'defamation'}:
            result = CM.judge({'body': 'synthetic'}, 'cascade', fake({'route_' + kind: 'C'}))
            item = next(x for x in result['results'] if x['type'] == kind)
            self.assertEqual(item['recommendation'], 'review', kind)
            result = CM.judge({'body': 'synthetic'}, 'cascade', fake(missing=['route_' + kind]))
            self.assertEqual(result['recommendation'], 'hold', kind)

    def test_external_checks_preserve_unknown(self):
        for kind, detail in [('sexual_violence', 'consent'), ('stalking', 'repetition'),
                             ('copyright', 'permission'), ('fraud', 'truth'),
                             ('election_interference', 'truth'), ('ad_fraud', 'deception')]:
            out = CM.judge({'body': 'synthetic'}, 'cascade', fake({'route_' + kind: 'A', kind + '_' + detail: 'C'}))
            item = next(x for x in out['results'] if x['type'] == kind)
            self.assertEqual(item['recommendation'], 'review')
            self.assertEqual(next(d for d in item['details'] if d['id'] == kind + '_' + detail)['choice'], 'C')

    def test_native_model_is_sent_without_retries(self):
        seen = []
        def native(payload, key, retry):
            seen.append((payload['model'], retry))
            return CM.mock_post(payload, key)
        with patch.object(CM.DC, '_env', return_value=(native, 'test', '')), patch.object(CM.DC, '_post', side_effect=native):
            result = CM.try_one({'body': 'synthetic', 'mode': 'all', 'model': 'solar-decide'})
        self.assertTrue(result['ok'])
        self.assertEqual(seen, [('solar-decide', 0)])
        self.assertEqual(result['runs'][0]['score_source'], 'jev_probability')

    def test_absent_context_overrides_unfounded_external_confirmation(self):
        changes = {'route_fraud': 'A', 'fraud_truth': 'A'}
        result = CM.judge({'body': 'synthetic'}, 'cascade', fake(changes))
        item = next(x for x in result['results'] if x['type'] == 'fraud')
        detail = next(d for d in item['details'] if d['id'] == 'fraud_truth')
        self.assertEqual(detail['choice'], 'C')
        self.assertIsNone(detail['selected_probability'])
        self.assertEqual(detail['model_answer']['choice'], 'A')
        self.assertEqual(item['recommendation'], 'review')
        # 선별에서 관련 없음으로 답했더라도 상세의 미확인 위험 신호를 버려 통과시키지 않는다
        all_result = CM.judge({'body': 'synthetic'}, 'all', fake({'fraud_truth': 'A'}))
        self.assertEqual(next(x for x in all_result['results'] if x['type'] == 'fraud')['recommendation'], 'review')

    def test_selected_text_model_runs_both_modes_and_preserves_shared_client(self):
        from types import SimpleNamespace
        from prism.config import Config
        from prism import serve
        calls = []
        class Client:
            mock = False
            model = 'selected-text-model'
            cfg = Config()
            _adapt = set()
            def complete_json(self, system, user, tag):
                payload = json.loads(user)
                payload['model'] = self.model
                calls.append((payload, self.cfg.retry.max_retries, self.frozen_parameters, system))
                return CM.mock_post(payload, '') , SimpleNamespace(raw={'model': self.model,
                    'usage': {'prompt_tokens': 700, 'completion_tokens': 200}})
        client = Client()
        original_retry = client.cfg.retry.max_retries
        with patch.object(CM.DC, '_SV', serve), patch.object(serve.Handler, 'server_mock', False), \
             patch.object(serve, 'llm_for_model', return_value=(client, 'timely')) as resolve, \
             patch.object(CM.DC, '_env') as solar:
            result = CM.try_one({'body': 'synthetic', 'model': client.model})
        resolve.assert_called_once_with(client.model, False)
        solar.assert_not_called()
        self.assertTrue(result['ok'])
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(retry == 0 and frozen for _, retry, frozen, _ in calls))
        self.assertEqual(client.cfg.retry.max_retries, original_retry)
        self.assertTrue(all(r['requested_model'] == client.model for r in result['runs']))
        self.assertTrue(all(r['score_source'] == 'llm_self_report' for r in result['runs']))
        self.assertEqual(result['runs'][0]['trace']['input_tokens'], 700)
        self.assertIn('자기보고', calls[0][3])
        self.assertEqual(calls[0][0]['state'], calls[1][0]['state'])

    def test_missing_text_model_connection_does_not_fallback(self):
        from prism import serve
        with patch.object(CM.DC, '_SV', serve), patch.object(serve.Handler, 'server_mock', False), \
             patch.object(serve, 'llm_for_model', return_value=(None, '라우터 키 없음')), patch.object(CM.DC, '_post') as native:
            result = CM.try_one({'body': 'synthetic', 'model': 'text-model'})
        self.assertFalse(result['ok'])
        native.assert_not_called()
