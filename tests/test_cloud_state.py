import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.cloud_state import CloudState, NotFound


class PreconditionFailed(Exception):
    pass


class FakeBlob:
    def __init__(self, bucket, name):
        self.bucket = bucket
        self.name = name
        self.generation = None

    def reload(self):
        if self.bucket.error:
            raise self.bucket.error
        if self.name not in self.bucket.objects:
            raise NotFound('missing')
        self.generation = self.bucket.objects[self.name][1]

    def download_as_bytes(self, if_generation_match):
        self.reload()
        if self.generation != if_generation_match:
            raise PreconditionFailed()
        return self.bucket.objects[self.name][0]

    def upload_from_string(self, data, content_type, if_generation_match):
        if self.bucket.error:
            raise self.bucket.error
        current = self.bucket.objects.get(self.name, (None, 0))[1]
        if current != if_generation_match:
            raise PreconditionFailed()
        assert content_type == 'application/json'
        self.generation = current + 1
        self.bucket.objects[self.name] = (data.encode('utf-8'), self.generation)


class FakeBucket:
    def __init__(self):
        self.objects = {}
        self.error = None

    def blob(self, name):
        return FakeBlob(self, name)

    def put(self, name, values):
        self.objects['instagram-organizer/' + name] = (json.dumps(values).encode(), 1)


class FakeClient:
    def __init__(self):
        self.storage = FakeBucket()
        self.names = []

    def bucket(self, name):
        self.names.append(name)
        return self.storage


STATE = {
    'cookies': [{'name': 'sessionid', 'value': 'fixture-cookie', 'domain': '.instagram.com', 'path': '/', 'secure': True}],
    'origins': [{'origin': 'https://www.instagram.com', 'localStorage': [{'name': 'fixture', 'value': 'value'}]}],
}
AI = {'api_key': 'fixture-api-key', 'model': 'gpt-4.1-mini'}


class CloudStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.client = FakeClient()
        self.state = CloudState('existing-private-bucket', self.temporary.name, client=self.client)
        self.path = Path(self.temporary.name) / 'ai-settings.json'

    def test_missing_blobs_leave_existing_local_settings_untouched(self):
        self.path.write_text(json.dumps(AI))
        self.assertIsNone(self.state.restore_ai_settings())
        self.assertIsNone(self.state.load_browser_state())
        self.assertEqual(json.loads(self.path.read_text()), AI)

    def test_ai_restore_is_atomic_and_private(self):
        self.client.storage.put('ai-settings.json', AI)
        self.path.write_text('old')
        self.assertEqual(self.state.restore_ai_settings(), AI)
        self.assertEqual(json.loads(self.path.read_text()), AI)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(list(Path(self.temporary.name).glob('.ai-settings-*')), [])

    def test_failed_atomic_replace_preserves_local_good_settings(self):
        self.client.storage.put('ai-settings.json', AI)
        self.path.write_text('old-good-setting')
        with patch('app.cloud_state.os.replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.state.restore_ai_settings()
        self.assertEqual(self.path.read_text(), 'old-good-setting')
        self.assertEqual(list(Path(self.temporary.name).glob('.ai-settings-*')), [])

    def test_valid_settings_and_browser_state_round_trip(self):
        self.path.write_text(json.dumps(AI))
        self.state.save_ai_settings()
        self.state.save_browser_state(STATE)
        reloaded = CloudState('existing-private-bucket', self.temporary.name, client=self.client)
        self.assertEqual(reloaded.load_browser_state(), STATE)
        self.assertEqual(reloaded.restore_ai_settings(), AI)
        self.assertEqual(set(self.client.storage.objects), {
            'instagram-organizer/ai-settings.json', 'instagram-organizer/browser-state.json',
        })
        self.assertEqual(self.client.names, ['existing-private-bucket', 'existing-private-bucket'])

    def test_stale_writer_does_not_overwrite_newer_browser_snapshot(self):
        self.client.storage.put('browser-state.json', STATE)
        stale = CloudState('existing-private-bucket', self.temporary.name, client=self.client)
        self.assertEqual(stale.load_browser_state(), STATE)
        self.assertEqual(self.state.load_browser_state(), STATE)
        newer = {'cookies': [], 'origins': []}
        self.state.save_browser_state(newer)
        with self.assertRaises(PreconditionFailed):
            stale.save_browser_state(STATE)
        self.assertEqual(self.state.load_browser_state(), newer)

    def test_only_notfound_is_treated_as_missing(self):
        for error in (PermissionError('denied'), OSError('network failure')):
            self.client.storage.error = error
            with self.assertRaises(type(error)):
                self.state.load_browser_state()
            with self.assertRaises(type(error)):
                self.state.restore_ai_settings()

    def test_corrupted_remote_settings_do_not_replace_local_or_expose_body(self):
        self.path.write_text(json.dumps(AI))
        secret = 'private-fixture-value'
        for payload in (secret.encode(), json.dumps({'api_key': secret}).encode(), b'{"api_key":NaN,"model":"gpt"}'):
            self.client.storage.objects['instagram-organizer/ai-settings.json'] = (payload, 1)
            with self.assertRaises(ValueError) as error:
                self.state.restore_ai_settings()
            self.assertNotIn(secret, str(error.exception))
            self.assertEqual(json.loads(self.path.read_text()), AI)

    def test_malformed_browser_state_is_rejected_without_upload(self):
        for values in ({'cookies': {}, 'origins': []}, {'cookies': [], 'origins': [{}]}, {'cookies': [{'value': 'private-cookie'}], 'origins': []}):
            self.client.storage.put('browser-state.json', values)
            with self.assertRaises(ValueError):
                self.state.load_browser_state()
            self.client.storage.objects.clear()
            with self.assertRaises(ValueError):
                self.state.save_browser_state(values)
            self.assertFalse(self.client.storage.objects)

    def test_invalid_local_ai_settings_cannot_overwrite_cloud_settings(self):
        self.client.storage.put('ai-settings.json', AI)
        self.path.write_text('{"api_key":"private-fixture-key","model":"bad model"}')
        with self.assertRaises(ValueError) as error:
            self.state.save_ai_settings()
        self.assertNotIn('private-fixture-key', str(error.exception))
        self.assertEqual(self.client.storage.objects['instagram-organizer/ai-settings.json'][1], 1)


if __name__ == '__main__':
    unittest.main()
