import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).parents[1] / "deploy/firebase/deploy.sh"
BASE = "asia-northeast3-docker.pkg.dev/ml-cherry/cloud-run-source-deploy/instagram-organizer"


class DeploymentImageTests(unittest.TestCase):
    def test_foreign_project_and_repository_fail_before_credentials(self):
        for image in (
            "asia-northeast3-docker.pkg.dev/foreign-project/cloud-run-source-deploy/instagram-organizer:latest",
            "asia-northeast3-docker.pkg.dev/ml-cherry/another/instagram-organizer:latest",
            "https://example.com/image:latest",
        ):
            with self.subTest(image=image):
                result = subprocess.run(
                    ["bash", str(SCRIPT), "--project", "ml-cherry", "--image", image, "--check"],
                    capture_output=True, text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--image must use this project", result.stderr)

    def test_invalid_suffix_fails_before_credentials(self):
        for suffix in (":tag;exit", ":tag with spaces", "@sha256:abc", ":"):
            with self.subTest(suffix=suffix):
                result = subprocess.run(
                    ["bash", str(SCRIPT), "--project", "ml-cherry", "--image", BASE + suffix, "--check"],
                    capture_output=True, text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("valid tag or a sha256 digest", result.stderr)

    def test_check_reads_existing_tag_and_digest_without_mutating(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            gcloud = directory / "gcloud"
            firebase = directory / "firebase"
            log = directory / "calls.jsonl"
            gcloud.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "args = sys.argv[1:]\n"
                "with open(os.environ['WHEREWEGO_TEST_CALLS'], 'a') as handle:\n"
                "    handle.write(json.dumps(args) + '\\n')\n"
                "if args[:3] == ['projects', 'describe', 'ml-cherry']:\n"
                "    print(json.dumps({'projectNumber': '405407040120'}))\n"
                "elif args[:3] == ['billing', 'projects', 'describe']:\n"
                "    print(json.dumps({'billingEnabled': True}))\n"
                "elif args[:4] == ['artifacts', 'docker', 'images', 'describe']:\n"
                "    print('sha256:' + 'a' * 64)\n"
                "else:\n"
                "    sys.exit('Unexpected command: ' + ' '.join(args))\n",
                encoding="utf-8",
            )
            firebase.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "assert sys.argv[1:] == ['projects:list', '--json']\n"
                "print(json.dumps({'result': [{'projectId': 'ml-cherry'}]}))\n",
                encoding="utf-8",
            )
            gcloud.chmod(0o700)
            firebase.chmod(0o700)
            env = dict(os.environ, GCLOUD_BIN=str(gcloud), FIREBASE_BIN=str(firebase),
                       WHEREWEGO_TEST_CALLS=str(log))
            for suffix in (":latest", "@sha256:" + "a" * 64):
                result = subprocess.run(
                    ["bash", str(SCRIPT), "--project", "ml-cherry", "--image", BASE + suffix, "--check"],
                    env=env, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("No resources were created or deployed.", result.stdout)
            calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(calls), 6)
            self.assertTrue(all("--project=ml-cherry" in call for call in calls))
            self.assertTrue(all(call[:1] not in (["run"], ["services"], ["builds"]) for call in calls))


if __name__ == "__main__":
    unittest.main()
