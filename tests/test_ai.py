import json
import os
import stat
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.ai import AISettings
from app.classifier import classify_records
from app.main import app
import test_core


def response_for(items):
    response = Mock()
    response.json.return_value = {"choices": [{"message": {"content": json.dumps({"items": items})}}]}
    return response


class AISettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_validated_setting_reload_and_private_key(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENAI_API_KEY": "", "AI_PROVIDER": "openai"}):
            settings = AISettings(directory)
            self.assertFalse(settings.public()["configured"])
            with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response_for([{"id": 0, "category": "카페"}]))) as request:
                public = await settings.configure("fake-test-api-key", "gpt-4.1-mini")
                self.assertEqual(request.call_args.kwargs["json"]["model"], "gpt-4.1-mini")
            self.assertEqual(public, {"configured": True, "provider": "openai", "model": "gpt-4.1-mini", "source": "app"})
            self.assertNotIn("fake-test-api-key", json.dumps(public))
            self.assertEqual(stat.S_IMODE(settings.path.stat().st_mode), 0o600)
            reloaded = AISettings(directory)
            self.assertEqual(reloaded.key(), "fake-test-api-key")
            with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response_for([]))):
                with self.assertRaises(ValueError):
                    await settings.configure("invalid-new-key", "gpt-4.1-mini")
            self.assertEqual(settings.key(), "fake-test-api-key")

    async def test_explicit_key_and_model_and_partial_response(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "wrong-env-key"}), patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response_for([{ "id": 0, "category": "여행"}]))) as request:
            records, warning = await classify_records([{"text": "해변에서 쉬는 하루"}, {"text": "커피 카페"}], True, "explicit-test-key", "gpt-4.1-mini")
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer explicit-test-key")
        self.assertEqual(records[0]["category"], "여행")
        self.assertEqual(records[0]["classification"], "ai")
        self.assertEqual(records[1]["category"], "카페")
        self.assertTrue(warning)


class AIAPITests(test_core.APITests):
    # Inherit the existing HTTP setup without repeating unrelated inherited tests.
    test_auth_and_csrf_cover_api_and_browser = None
    test_collection_job_persists_results = None
    test_edit_export_and_validation = None

    def test_setting_and_reclassification_preserve_manual_edits(self):
        self.login()
        headers = {"Origin": "http://testserver"}
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response_for([{ "id": 0, "category": "카페"}]))):
            response = self.client.put("/api/ai/config", json={"api_key": "fake-test-key", "model": "gpt-4.1-mini"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.client.get("/api/status").json()["ai_available"])
        self.assertNotIn("fake-test-key", response.text)
        app.state.store.upsert([
            {"url": "https://example.com/1", "title": "문화 정보", "text": "파란 하늘", "source": "dm"},
            {"url": "https://example.com/2", "title": "직접 수정", "text": "커피", "source": "dm"},
        ])
        manual = next(item for item in app.state.store.all() if item["url"].endswith("2"))
        app.state.store.update(manual["id"], "문화·취미", "내가 정함")
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response_for([{ "id": 0, "category": "여행"}]))):
            response = self.client.post("/api/classify", json={"only_pending": False}, headers=headers)
            self.assertEqual(response.status_code, 200)
            for _ in range(50):
                status = self.client.get("/api/status").json()
                if not status["busy"]:
                    break
                import time
                time.sleep(.01)
        self.assertEqual(status["job"]["status"], "done")
        items = self.client.get("/api/items").json()["items"]
        self.assertEqual(next(item for item in items if item["url"].endswith("1"))["category"], "여행")
        self.assertEqual(next(item for item in items if item["url"].endswith("2"))["category"], "문화·취미")
        self.assertNotIn("fake-test-key", self.client.get("/api/export").text)
