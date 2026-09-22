import importlib.util
import json
import pathlib
import unittest


_PATH = pathlib.Path(__file__).parents[1] / "scripts" / "deploy_checks.py"
_SPEC = importlib.util.spec_from_file_location("deploy_checks", _PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class DeployChecksTest(unittest.TestCase):
    def test_ingesting_mock_responses_are_fail_closed(self):
        self.assertEqual(_MODULE.ingesting_state('{"ingesting": false}'), "idle")
        self.assertEqual(_MODULE.ingesting_state('{"ingesting": true}'), "busy")
        self.assertEqual(_MODULE.ingesting_state("not-json"), "unknown")
        self.assertEqual(_MODULE.ingesting_state('{"backend": "supabase"}'), "unknown")
        self.assertEqual(_MODULE.ingesting_state('{"ingesting": 0}'), "unknown")

    def test_deployment_requires_exact_build_and_runtime_flags(self):
        target = {"build": "v1.42", "forcedMock": False,
                  "backend": "supabase", "configured": True}
        self.assertTrue(_MODULE.deployment_ready(json.dumps(target), "v1.42"))

        old_build = dict(target, build="v1.41")
        mock_response = dict(target, forcedMock=True)
        for response in (old_build, mock_response):
            self.assertFalse(_MODULE.deployment_ready(json.dumps(response), "v1.42"))
        self.assertFalse(_MODULE.deployment_ready("{}", "v1.42"))
        self.assertFalse(_MODULE.deployment_ready(json.dumps(dict(target, configured=1)), "v1.42"))
