import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import urllib.error

SPEC = importlib.util.spec_from_file_location(
    "firebase_gcloud", Path(__file__).parents[1] / "deploy/firebase/firebase_gcloud.py"
)
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)

CONFIG = {"hosting": {
    "site": "ml-cherry-wherewego", "public": "hosting/public",
    "ignore": ["firebase.json"],
    "redirects": [{"source": "/**", "destination": "https://organizer-test.run.app", "type": 302}],
}}


class FirebaseCloudShellTests(unittest.TestCase):
    def client(self):
        return adapter.Firebase("ml-cherry", "unit-test-secret")

    def test_redirect_conversion(self):
        self.assertEqual(adapter.redirect_config(CONFIG, "ml-cherry-wherewego"),
                         {"redirects": [{"glob": "/**", "location": "https://organizer-test.run.app", "statusCode": 302}]})

    def test_rejects_unsupported_config_and_destinations(self):
        for destination in ("http://organizer-test.run.app", "https://example.com",
                            "https://organizer-test.run.app/path", "https://user@organizer-test.run.app",
                            "https://organizer-test.run.app/?token=value"):
            config = json.loads(json.dumps(CONFIG))
            config["hosting"]["redirects"][0]["destination"] = destination
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                adapter.redirect_config(config, "ml-cherry-wherewego")
        config = json.loads(json.dumps(CONFIG))
        config["hosting"]["rewrites"] = []
        with self.assertRaises(ValueError):
            adapter.redirect_config(config, "ml-cherry-wherewego")

    def test_site_list_paginates(self):
        client = self.client()
        with patch.object(client, "request", side_effect=[
            {"sites": [{"name": "projects/ml-cherry/sites/first"}], "nextPageToken": "next"},
            {"sites": [{"name": "projects/ml-cherry/sites/second"}]},
        ]) as request:
            self.assertEqual(len(client.sites()), 2)
            self.assertEqual(request.call_args_list[1].kwargs["query"], {"pageToken": "next"})

    def test_does_not_publish_foreign_site(self):
        client = self.client()
        with patch.object(client, "sites", return_value=[{"name": "projects/ml-cherry/sites/other"}]), \
             patch.object(client, "request") as request:
            with self.assertRaises(RuntimeError):
                client.publish_redirect(CONFIG)
            request.assert_not_called()

    def test_redirect_only_create_finalize_release(self):
        client = self.client()
        version = "sites/ml-cherry-wherewego/versions/abc123"
        with patch.object(client, "sites", return_value=[{"name": "projects/ml-cherry/sites/ml-cherry-wherewego"}]), \
             patch.object(client, "request", side_effect=[{"name": version}, {"status": "FINALIZED"}, {"name": "release"}]) as request:
            self.assertEqual(client.publish_redirect(CONFIG), {"name": "release"})
            calls = request.call_args_list
            self.assertEqual(len(calls), 3)
            self.assertEqual(calls[0].args[1:],
                             ("POST", "projects/-/sites/ml-cherry-wherewego/versions"))
            self.assertEqual(calls[0].kwargs["body"]["config"]["redirects"][0]["statusCode"], 302)
            self.assertEqual(calls[1].args[1:], ("PATCH", version))
            self.assertEqual(calls[1].kwargs, {"body": {"status": "FINALIZED"}, "query": {"updateMask": "status"}})
            self.assertEqual(calls[2].args[2], "projects/-/sites/ml-cherry-wherewego/channels/live/releases")
            self.assertEqual(calls[2].kwargs["query"], {"versionName": version})

    def test_finalize_failure_does_not_release(self):
        client = self.client()
        with patch.object(client, "sites", return_value=[{"siteId": "ml-cherry-wherewego"}]), \
             patch.object(client, "request", side_effect=[
                 {"name": "sites/ml-cherry-wherewego/versions/abc"}, RuntimeError("Finalize failed"),
             ]) as request:
            with self.assertRaises(RuntimeError):
                client.publish_redirect(CONFIG)
            self.assertEqual(request.call_count, 2)

    def test_api_error_does_not_expose_token(self):
        client = self.client()
        error = urllib.error.HTTPError("https://firebasehosting.googleapis.com/", 403, "Forbidden", {},
                                        io.BytesIO(b'{"error":{"message":"unit-test-secret denied"}}'))
        with patch.object(adapter.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                client.project_metadata()
        self.assertNotIn("unit-test-secret", str(raised.exception))
        self.assertIn("HTTP 403", str(raised.exception))

    def test_token_is_captured_and_not_printed(self):
        result = subprocess.CompletedProcess([], 0, "unit-test-secret\n", "")
        output = io.StringIO()
        with patch.object(adapter.subprocess, "run", return_value=result) as run, contextlib.redirect_stdout(output):
            self.assertEqual(adapter.gcloud_token("ml-cherry"), "unit-test-secret")
        self.assertEqual(output.getvalue(), "")
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertIn("--project=ml-cherry", run.call_args.args[0])

    def test_projects_list_matches_firebase_cli_json_contract(self):
        output = io.StringIO()
        with patch.dict(adapter.os.environ, {"WHEREWEGO_PROJECT_ID": "ml-cherry"}), \
             patch.object(adapter, "gcloud_token", return_value="unit-test-secret"), \
             patch.object(adapter.Firebase, "project_metadata", return_value={"projectId": "ml-cherry"}), \
             contextlib.redirect_stdout(output):
            self.assertEqual(adapter.main(["projects:list", "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue()), {"status": "success", "result": [{"projectId": "ml-cherry"}]})
        self.assertNotIn("unit-test-secret", output.getvalue())


if __name__ == "__main__":
    unittest.main()
