import base64
import copy
import json
import unittest
from unittest.mock import patch
from prism import imagecheck as IC

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j9yQAAAAASUVORK5CYII=')
CANDIDATES = [{"id": "upstage_ie"}, {"id": "timely:vision-test"}, {"id": "bizrouter:configured-model"}]


def request():
    return {"image": base64.b64encode(PNG).decode(), "mime": "image/png", "model": "timely|vision-test", "prompt": "수정한 지침"}


def response():
    return {"model": "actual-model", "usage": {"prompt_tokens": 12}, "choices": [{"message": {"content": json.dumps({"observations": [
        {"id": k, "value": "absent", "score": 0.7, "evidence": "관찰 근거"} for k in IC.CATEGORIES]})}}]}


class ImageCheckTest(unittest.TestCase):
    def setUp(self):
        self.key = patch.object(IC.IMG, 'router_key', side_effect=lambda p: 'private-key' if p == 'timely' else '')
        self.key.start()
        self.addCleanup(self.key.stop)

    def test_catalog_excludes_ocr_and_disconnected_provider(self):
        r = IC.try_one({'action': 'defaults'}, CANDIDATES)
        self.assertEqual([i['model'] for g in r['models'] for i in g['items']], ['vision-test'])
        self.assertNotIn('private-key', json.dumps(r))
        self.assertEqual(IC.catalog([]), [])

    def test_single_call_uses_selected_image_and_prompt_no_source_in_export(self):
        with patch.object(IC, '_post', return_value=response()) as post:
            r = IC.try_one(request(), CANDIDATES)
        self.assertTrue(r['ok']); self.assertEqual(r['requests'], 1)
        self.assertEqual(r['status'], 'complete'); self.assertEqual(r['response_model'], 'actual-model')
        self.assertEqual(r['observations'][0]['score'], .7)
        self.assertNotIn(request()['image'], json.dumps(r)); self.assertNotIn('private-key', json.dumps(r))
        post.assert_called_once()
        provider, payload = post.call_args[0]
        self.assertEqual(provider, 'timely'); self.assertEqual(payload['model'], 'vision-test')
        self.assertEqual(payload['messages'][1]['content'][0]['text'], '수정한 지침')
        self.assertEqual(payload['messages'][1]['content'][1]['image_url']['url'], 'data:image/png;base64,' + request()['image'])

    def test_bad_inputs_never_call_provider(self):
        cases = [[], {'model': []}, {'model': 'timely|unknown'}, {'model': 'upstage_ie'}, {'image': '%%%bad'},
                 {'image': ''}, {'image': base64.b64encode(b'<svg/>').decode()}, {'mime': 'image/jpeg'},
                 {'prompt': ' '}, {'prompt': 'x' * 8001}, {'mime': ['image/png']}]
        with patch.object(IC, '_post') as post:
            for case in cases:
                d = dict(request(), **case) if isinstance(case, dict) else case
                with self.subTest(case=str(case)[:80]):
                    self.assertFalse(IC.try_one(d, CANDIDATES)['ok'])
            with patch.object(IC, 'MAX_BYTES', 8):
                self.assertFalse(IC.try_one(request(), CANDIDATES)['ok'])
            post.assert_not_called()

    def test_invalid_results_preserve_raw_without_default_verdict_or_retry(self):
        valid = json.loads(response()['choices'][0]['message']['content'])
        invalids = ['not JSON', '{}']
        for change in ({'score': True}, {'score': float('nan')}, {'score': 1.1}, {'score': -.1}, {'value': 'unknown'}, {'evidence': ''}, {'id': []}):
            altered = copy.deepcopy(valid); altered['observations'][0].update(change)
            invalids.append(json.dumps(altered))
        altered = copy.deepcopy(valid); altered['observations'][1]['id'] = 'sexual'; invalids.append(json.dumps(altered))
        for raw in invalids:
            reply = response(); reply['choices'][0]['message']['content'] = raw
            with self.subTest(raw=raw), patch.object(IC, '_post', return_value=reply) as post:
                r = IC.try_one(request(), CANDIDATES)
                self.assertEqual(r['status'], 'invalid_response'); self.assertEqual(r['observations'], [])
                self.assertEqual(r['raw'], raw); post.assert_called_once()

    def test_provider_failure_not_retried_or_leaked(self):
        with patch.object(IC, '_post', side_effect=TimeoutError('private-key')) as post:
            r = IC.try_one(request(), CANDIDATES)
        self.assertFalse(r['ok']); self.assertNotIn('private-key', r['error']); post.assert_called_once()
        with patch.object(IC, '_post', return_value=response()):
            self.assertTrue(IC.try_one(request(), CANDIDATES)['ok'])

    def test_mock_has_no_network_or_fabricated_observation(self):
        d = request(); d['model'] = 'mock|image-fixture'
        with patch.object(IC, '_post') as post:
            r = IC.try_one(d, [], mock=True)
        post.assert_not_called(); self.assertEqual(r['requests'], 0); self.assertTrue(r['mock'])
        self.assertTrue(all(row['value'] == 'unknown' and row['score'] is None for row in r['observations']))
        self.assertFalse(IC.try_one(d, [], mock=False)['ok'])

    def test_route_is_super_only_and_old_route_removed(self):
        from prism import serve
        from pathlib import Path
        source = Path(serve.__file__).read_text()
        self.assertIn('@_post_route("/lab-imagecheck-try", gate="super")', source)
        self.assertNotIn('/lab-cleanmeta-try', source)
