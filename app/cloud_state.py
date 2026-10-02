"""Private GCS snapshots for validated settings and Playwright storage state.

The bucket must already exist. No raw browser profile or SQLite database is
mounted or uploaded. Call these synchronous methods with asyncio.to_thread.
"""

import contextlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

try:
    from google.api_core.exceptions import NotFound
except ImportError:
    # Keeps injected-client tests usable without the optional Google SDK.
    class NotFound(Exception):
        pass


class CloudState:
    def __init__(self, bucket_name, directory, client=None, prefix='instagram-organizer'):
        if not isinstance(bucket_name, str) or not bucket_name.strip():
            raise ValueError('클라우드 상태 저장 버킷을 지정해주세요.')
        self.bucket_name = bucket_name.strip()
        self.directory = Path(directory)
        self._client = client
        self._bucket = None
        self.prefix = str(prefix).strip('/')
        self._generations = {}

    def _blob(self, filename):
        if self._bucket is None:
            if self._client is None:
                from google.cloud import storage
                self._client = storage.Client()
            self._bucket = self._client.bucket(self.bucket_name)
        name = f'{self.prefix}/{filename}' if self.prefix else filename
        return self._bucket.blob(name)

    def _download(self, filename):
        blob = self._blob(filename)
        try:
            blob.reload()
            generation = int(blob.generation)
            payload = blob.download_as_bytes(if_generation_match=generation)
        except NotFound:
            self._generations[filename] = 0
            return None
        self._generations[filename] = generation
        return payload

    def _upload(self, filename, values):
        blob = self._blob(filename)
        if filename not in self._generations:
            try:
                blob.reload()
                self._generations[filename] = int(blob.generation)
            except NotFound:
                self._generations[filename] = 0
        # A stale instance must fail rather than overwrite a newer snapshot.
        blob.upload_from_string(
            json.dumps(values, ensure_ascii=False, allow_nan=False),
            content_type='application/json',
            if_generation_match=self._generations[filename],
        )
        self._generations[filename] = int(blob.generation)

    @staticmethod
    def _parse(payload, message):
        try:
            return json.loads(payload, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, TypeError, UnicodeError):
            raise ValueError(message) from None

    @staticmethod
    def _ai_values(values):
        message = '클라우드 AI 설정 형식이 올바르지 않습니다.'
        if not isinstance(values, dict) or set(values) != {'api_key', 'model'}:
            raise ValueError(message)
        key, model = values['api_key'], values['model']
        if not isinstance(key, str) or not isinstance(model, str):
            raise ValueError(message)
        key, model = key.strip(), model.strip()
        if (not key or len(key) > 512 or not key.isascii()
                or any(c.isspace() or not c.isprintable() for c in key)
                or not re.fullmatch(r'[A-Za-z0-9._:/-]{1,80}', model)):
            raise ValueError(message)
        return {'api_key': key, 'model': model}

    @staticmethod
    def _browser_values(values):
        message = '클라우드 브라우저 상태 형식이 올바르지 않습니다.'
        if (not isinstance(values, dict) or set(values) != {'cookies', 'origins'}
                or not isinstance(values['cookies'], list) or not isinstance(values['origins'], list)):
            raise ValueError(message)
        for cookie in values['cookies']:
            if not isinstance(cookie, dict) or any(not isinstance(cookie.get(key), str) for key in ('name', 'value', 'domain', 'path')):
                raise ValueError(message)
        for origin in values['origins']:
            if not isinstance(origin, dict) or not isinstance(origin.get('origin'), str):
                raise ValueError(message)
            try:
                parsed = urlsplit(origin['origin'])
                if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError(message)
            except ValueError:
                raise ValueError(message) from None
            entries = origin.get('localStorage', [])
            if not isinstance(entries, list) or any(
                not isinstance(entry, dict) or not isinstance(entry.get('name'), str) or not isinstance(entry.get('value'), str)
                for entry in entries
            ):
                raise ValueError(message)
        try:
            # Validates nested IndexedDB data too, and returns a detached JSON copy.
            return json.loads(json.dumps(values, allow_nan=False))
        except (ValueError, TypeError, OverflowError):
            raise ValueError(message) from None

    def restore_ai_settings(self):
        payload = self._download('ai-settings.json')
        if payload is None:
            return None
        values = self._ai_values(self._parse(payload, '클라우드 AI 설정 형식이 올바르지 않습니다.'))
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary_path = None
        try:
            descriptor, temporary_path = tempfile.mkstemp(prefix='.ai-settings-', dir=self.directory)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(values, handle, ensure_ascii=False, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.directory / 'ai-settings.json')
        finally:
            if temporary_path is not None:
                with contextlib.suppress(OSError):
                    Path(temporary_path).unlink()
        return values

    def save_ai_settings(self):
        payload = (self.directory / 'ai-settings.json').read_bytes()
        values = self._ai_values(self._parse(payload, '클라우드 AI 설정 형식이 올바르지 않습니다.'))
        self._upload('ai-settings.json', values)

    def load_browser_state(self):
        payload = self._download('browser-state.json')
        if payload is None:
            return None
        return self._browser_values(self._parse(payload, '클라우드 브라우저 상태 형식이 올바르지 않습니다.'))

    def save_browser_state(self, state):
        self._upload('browser-state.json', self._browser_values(state))
