"""Common evaluation HTTP contract: real routing, frozen reads and hosted gates."""
import contextlib
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch


class EvaluationHTTPTests(unittest.TestCase):
    def setUp(self):
        from prism import config, serve
        from prism.store import Store, content_hash

        self.sv = serve
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {"PRISM_BACKEND": "sqlite"}))
        self.stack.enter_context(patch.object(config, "DEFAULT_CONFIG_PATH",
                                             os.path.join(self.temp.name, "config.json")))
        self.store = Store(os.path.join(self.temp.name, "test.db"))
        self.stack.enter_context(patch.object(serve, "_STORE", self.store))
        self.stack.enter_context(patch.object(serve, "_LLM_CACHE", {}))
        self.stack.enter_context(patch.object(serve.Handler, "server_mock", True))
        self.stack.enter_context(patch.object(serve, "_supa", return_value=False))
        for i in range(3):
            content = {"displayServiceName": "뉴스", "title": f"HTTP 평가 {i}",
                       "body": f"평가 원문 {i}. 지역 소식을 안내합니다."}
            self.store.upsert_golden(content_hash(content), content,
                                     {"finalGrade": "G", "reasons": []})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._close_server)

    def _close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, path, body=None, token=""):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.server.server_port}{path}", data=data, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def complete_run(self):
        code, result = self.request("/evaluation-start", {
            "models": ["solar-pro2", "gpt-5.4"], "scope": "all",
            "limits": {"max_calls": 100}, "request_id": "http-contract"})
        self.assertEqual(code, 200)
        self.assertTrue(result.get("ok"), result)
        run_id = result["id"]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            code, detail = self.request("/evaluation-run?id=" + run_id)
            self.assertEqual(code, 200)
            self.assertTrue(detail.get("ok"), detail)
            if detail["run"]["status"] not in ("queued", "running", "cancel_requested"):
                self.assertEqual(detail["run"]["status"], "completed", detail)
                return run_id, detail["run"]
            time.sleep(0.02)
        self.fail("common evaluation did not finish")

    def test_start_history_and_detail_are_real_read_only_routes(self):
        run_id, run = self.complete_run()
        self.assertEqual(len(run["model_results"]), 2)
        self.assertTrue(run["snapshot"]["rows"])
        self.assertTrue(run["basis_fingerprint"])
        # A result lookup must never resolve a paid client or an embedding client.
        with patch.object(self.sv, "llm_for_model", side_effect=AssertionError("paid lookup")), \
                patch("prism.evaluation.semantic_embed", side_effect=AssertionError("embedding lookup")):
            _, history = self.request("/evaluation-runs")
            self.assertIn(run_id, [item["id"] for item in history["items"]])
            _, again = self.request("/evaluation-run?id=" + run_id)
            self.assertEqual(again["run"]["items"], run["items"])
            self.assertEqual(again["run"]["cost"], run["cost"])
            _, diagnostic = self.request('/eval-grounding?kind=evaluation&id=' + run_id)
            self.assertTrue(diagnostic['ok'], diagnostic)
            self.assertEqual(diagnostic['status'], 'not_started')

    def test_environment_save_and_preview_do_not_call_models(self):
        from prism.llm import LLMClient
        with patch.object(LLMClient, 'complete_json', side_effect=AssertionError('paid save')):
            code, saved = self.request('/evaluation-environment-save', {
                'name': '정답·원문 검토', 'judge_model': 'solar-pro2', 'max_calls': 80})
            self.assertEqual(code, 200)
            self.assertTrue(saved['ok'], saved)
            environment = saved['environment']
            self.assertEqual(environment['judge_model'], 'solar-pro2')
            self.assertEqual(environment['limits']['max_calls'], 80)
            _, listed = self.request('/evaluation-environments')
            self.assertIn(environment['id'], [item['id'] for item in listed['items']])
            _, preview = self.request('/evaluation-preview?scope=all')
            self.assertTrue(preview['ok'], preview)
            self.assertEqual(preview['total'], 3)
            self.assertIsNone(preview['cost']['total_usd'])

    def test_completed_resume_reuses_outputs_and_request_key(self):
        run_id, run = self.complete_run()
        with patch.object(self.sv, "llm_for_model", side_effect=AssertionError("duplicate call")):
            code, resumed = self.request("/evaluation-resume", {"id": run_id})
            self.assertEqual(code, 200)
            self.assertTrue(resumed["ok"], resumed)
            self.assertEqual(resumed["run"]["items"], run["items"])

    def test_explicit_repeat_creates_a_new_run_from_the_stored_conditions(self):
        run_id, run = self.complete_run()
        # This action must not capture changed operational prompts or gold data.
        with patch('prism.execution.capture', side_effect=AssertionError('recapture forbidden')):
            code, repeated = self.request('/evaluation-rerun', {'id': run_id, 'request_id': 'repeat-http'})
        self.assertEqual(code, 200)
        self.assertTrue(repeated['ok'], repeated)
        self.assertNotEqual(repeated['id'], run_id)
        self.assertEqual(repeated['run']['snapshot'], run['snapshot'])
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            _, detail = self.request('/evaluation-run?id=' + repeated['id'])
            if detail['run']['status'] == 'completed':
                break
            time.sleep(.02)
        self.assertEqual(detail['run']['status'], 'completed')

    def test_invalid_inputs_do_not_start_calls(self):
        with patch.object(self.sv, "llm_for_model", side_effect=AssertionError("invalid start")):
            for body in ([], {"models": []}, {"models": ["solar-pro2"], "scope": "bad"},
                         {"models": ["solar-pro2"], "limits": {"max_calls": -1}}):
                with self.subTest(body=body):
                    code, result = self.request("/evaluation-start", body)
                    self.assertLess(code, 500)
                    self.assertFalse(result.get("ok", False), result)

    def test_hosted_routes_reject_anonymous_and_non_admin_mutations(self):
        mutations = ("/evaluation-start", "/evaluation-resume", "/evaluation-cancel", '/evaluation-rerun',
                     "/evaluation-candidate-start", "/evaluation-candidate-cancel",
                     "/evaluation-environment-save", '/eval-grounding-start', '/eval-grounding-cancel')
        with patch.object(self.sv, "_supa", return_value=True), \
                patch.object(self.sv, "validate_jwt", side_effect=lambda token: token or None), \
                patch.object(self.sv, "jwt_email", return_value=""), \
                patch.object(self.sv, "team_of", return_value="team-a"), \
                patch.object(self.sv, "is_admin_user", return_value=False), \
                patch.object(self.sv, "is_sys_admin_user", return_value=False), \
                patch.object(self.sv, "llm_for_model", side_effect=AssertionError("unauthorized call")):
            for path in ("/evaluation-runs", "/evaluation-run?id=missing",
                         "/evaluation-candidate?id=missing", '/evaluation-environments',
                         '/evaluation-preview?scope=all', '/eval-grounding?kind=evaluation&id=missing'):
                self.assertEqual(self.request(path)[0], 401, path)
            for path in mutations:
                for token in ("", "member"):
                    self.assertIn(self.request(path, {"id": "missing"}, token)[0], (401, 403), path)

    def test_hosted_foreign_team_cannot_read_or_mutate_local_run(self):
        run_id, _ = self.complete_run()
        with patch.object(self.sv, "_supa", return_value=True), \
                patch.object(self.sv, "validate_jwt", return_value="admin-b"), \
                patch.object(self.sv, "jwt_email", return_value=""), \
                patch.object(self.sv, "team_of", return_value="team-b"), \
                patch.object(self.sv, "is_admin_user", return_value=True), \
                patch.object(self.sv, "is_sys_admin_user", return_value=False), \
                patch.object(self.sv, "llm_for_model", side_effect=AssertionError("foreign call")):
            _, history = self.request("/evaluation-runs", token="admin-b")
            self.assertNotIn(run_id, [item["id"] for item in history["items"]])
            for path, body in (("/evaluation-run?id=" + run_id, None),
                               ("/evaluation-resume", {"id": run_id, "team": ""}),
                               ("/evaluation-cancel", {"id": run_id, "team": ""})):
                _, result = self.request(path, body, "admin-b")
                self.assertFalse(result.get("ok"), result)


if __name__ == "__main__":
    unittest.main()
