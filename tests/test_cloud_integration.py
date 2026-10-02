import asyncio
import copy
import json
import os
import stat
import tempfile
import threading
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app import main
from app.store import Store


PASSWORD = "fixture-admin-password-long"
AI = {"api_key": "restored-private-api-key", "model": "gpt-4.1-mini"}
STATE = {"cookies": [{"name": "sessionid", "value": "private-cookie-fixture",
                      "domain": ".instagram.com", "path": "/", "secure": True}], "origins": []}
ERROR_SECRET = "private-provider-error-detail"


class FakeCloud:
    def __init__(self, directory, events, restored_ai):
        self.directory = Path(directory)
        self.events = events
        self.restored_ai = restored_ai
        self.saved_ai = []
        self.saved_browser = []
        self.ai_error = None
        self.browser_error = None

    def restore_ai_settings(self):
        self.events.append("restore_ai")
        if self.restored_ai is not None:
            path = self.directory / "ai-settings.json"
            path.write_text(json.dumps(self.restored_ai), encoding="utf-8")
            path.chmod(0o600)

    def load_browser_state(self):
        self.events.append("load_browser")
        return copy.deepcopy(STATE)

    def save_ai_settings(self):
        self.events.append("save_ai")
        if self.ai_error:
            raise self.ai_error
        self.saved_ai.append(json.loads((self.directory / "ai-settings.json").read_text()))

    def save_browser_state(self, state):
        self.events.append("save_browser")
        if self.browser_error:
            raise self.browser_error
        self.saved_browser.append(copy.deepcopy(state))


class FakeBrowser:
    def __init__(self, restored_state, events):
        self.restored_state = copy.deepcopy(restored_state)
        self.events = events
        self.ready = False
        self.closed = False
        self.snapshot_error = None

    async def start(self):
        self.ready = True

    async def snapshot(self):
        self.events.append("snapshot")
        if self.snapshot_error:
            raise self.snapshot_error
        return copy.deepcopy(STATE)

    async def close(self):
        self.events.append("close")
        self.ready = False
        self.closed = True


class FakeStore:
    def __init__(self, directory):
        self.local = Store(Path(directory) / "fixture-store")
        self.calls = []
        self.total_release = None
        self.total_started = None
        self.loop = None

    def total(self):
        self.calls.append(("total", threading.get_ident()))
        if self.total_release is not None:
            self.loop.call_soon_threadsafe(self.total_started.set)
            self.total_release.wait(timeout=1)
        return self.local.total()

    def all(self, q="", category="", source=""):
        self.calls.append(("all", threading.get_ident()))
        return self.local.all(q, category, source)

    def update(self, item_id, category, note):
        self.calls.append(("update", threading.get_ident()))
        return self.local.update(item_id, category, note)

    def upsert(self, records):
        self.calls.append(("upsert", threading.get_ident()))
        return self.local.upsert(records)

    def reclassify(self, records):
        self.calls.append(("reclassify", threading.get_ident()))
        return self.local.reclassify(records)


class CloudIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.events = []
        self.store = FakeStore(self.directory)

    @asynccontextmanager
    async def application(self, bucket="private-fixture-bucket", restored_ai=AI):
        self.cloud = FakeCloud(self.directory, self.events, restored_ai)

        def browser_factory(restored_state):
            self.events.append("browser_init")
            self.browser = FakeBrowser(restored_state, self.events)
            return self.browser

        def store_factory(**kwargs):
            self.events.append("store_init")
            self.store_arguments = kwargs
            return self.store

        environment = {"ADMIN_PASSWORD": PASSWORD, "DATA_DIR": str(self.directory),
                       "STORE_BACKEND": "firestore", "ORGANIZER_STATE_BUCKET": bucket,
                       "GOOGLE_CLOUD_PROJECT": "fixture-project", "FIRESTORE_DATABASE": "fixture-database",
                       "OPENAI_API_KEY": "", "OPENAI_MODEL": "gpt-4.1-mini",
                       "PUBLIC_ORIGIN": "http://testserver", "COOKIE_SECURE": "0"}
        with patch.dict(os.environ, environment), \
                patch("app.cloud_state.CloudState", return_value=self.cloud) as cloud_class, \
                patch("app.firestore_store.FirestoreStore", side_effect=store_factory) as store_class, \
                patch("app.main.BrowserManager", side_effect=browser_factory):
            self.cloud_class = cloud_class
            self.store_class = store_class
            async with main.lifespan(main.app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://testserver") as client:
                    yield client

    async def login(self, client):
        response = await client.post("/login", data={"password": PASSWORD}, headers={"Origin": "http://testserver"})
        self.assertEqual(response.status_code, 303)

    def classification_result(self):
        return ([{"category": "카페", "classification": "ai"}], "")

    async def test_firestore_requires_state_bucket_before_cloud_connections(self):
        with self.assertRaisesRegex(RuntimeError, "ORGANIZER_STATE_BUCKET"):
            async with self.application(bucket=""):
                self.fail("Startup should have failed")
        self.assertEqual(self.events, [])
        self.cloud_class.assert_not_called()
        self.store_class.assert_not_called()

    async def test_startup_restores_settings_before_ai_and_browser_initialization(self):
        async with self.application() as client:
            self.assertEqual(self.events, ["restore_ai", "load_browser", "store_init", "browser_init"])
            self.cloud_class.assert_called_once_with("private-fixture-bucket", self.directory)
            self.assertEqual(self.store_arguments, {"project_id": "fixture-project", "database": "fixture-database"})
            self.assertEqual(main.app.state.ai.key(), AI["api_key"])
            self.assertEqual(self.browser.restored_state, STATE)
            await self.login(client)
            response = await client.get("/api/ai/config")
            self.assertEqual(response.json(), {"configured": True, "model": AI["model"], "source": "app"})
            self.assertNotIn(AI["api_key"], response.text)
        self.assertTrue(self.browser.closed)

    async def test_firestore_endpoints_edit_filter_export_and_use_worker_threads(self):
        self.store.local.upsert([{"url": "https://www.instagram.com/p/ONE/", "title": "성수 카페",
                                  "text": "커피 디저트", "category": "카페", "classification": "rules", "source": "saved"}])
        loop_thread = threading.get_ident()
        async with self.application() as client:
            await self.login(client)
            self.assertEqual((await client.get("/api/status")).json()["total"], 1)
            response = await client.get("/api/items", params={"q": "성수", "category": "카페", "source": "saved"})
            item = response.json()["items"][0]
            response = await client.patch(f'/api/items/{item["id"]}', json={"category": "여행", "note": "방문 계획"}, headers={"Origin": "http://testserver"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual((response.json()["category"], response.json()["note"], response.json()["classification"]), ("여행", "방문 계획", "manual"))
            exported = await client.get("/api/export")
            self.assertEqual(exported.json()["items"][0]["note"], "방문 계획")
            self.assertIn("attachment", exported.headers["content-disposition"])
            self.assertEqual((await client.get("/api/items", params={"category": "카페"})).json()["items"], [])
            self.assertEqual((await client.patch("/api/items/missing", json={"category": "여행"}, headers={"Origin": "http://testserver"})).status_code, 404)
        self.assertTrue(self.store.calls)
        self.assertTrue(all(thread_id != loop_thread for _, thread_id in self.store.calls))

    async def test_slow_status_read_does_not_block_event_loop(self):
        async with self.application() as client:
            await self.login(client)
            self.store.loop = asyncio.get_running_loop()
            self.store.total_started = asyncio.Event()
            self.store.total_release = threading.Event()
            request = asyncio.create_task(client.get("/api/status"))
            try:
                await asyncio.wait_for(self.store.total_started.wait(), timeout=2)
                heartbeat = asyncio.create_task(asyncio.sleep(0, result="loop responsive"))
                self.assertEqual(await heartbeat, "loop responsive")
                self.assertFalse(request.done())
                self.assertNotEqual(self.store.calls[-1][1], threading.get_ident())
            finally:
                self.store.total_release.set()
                response = await request
            self.assertEqual(response.status_code, 200)

    async def test_ai_configuration_is_saved_to_cloud_without_exposing_key(self):
        new_key = "new-private-api-key"
        async with self.application() as client:
            await self.login(client)
            with patch("app.ai.classify_records", new=AsyncMock(return_value=self.classification_result())) as classify:
                response = await client.put("/api/ai/config", json={"api_key": new_key, "model": "gpt-4.1-mini"}, headers={"Origin": "http://testserver"})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(new_key, response.text)
            self.assertEqual(self.cloud.saved_ai, [{"api_key": new_key, "model": "gpt-4.1-mini"}])
            self.assertEqual(classify.await_args.kwargs["api_key"], new_key)
            self.assertEqual(main.app.state.ai.key(), new_key)
            self.assertFalse(main.app.state.busy)

    async def test_cloud_ai_save_failure_restores_previous_settings_and_hides_secret(self):
        new_key = "new-private-api-key"
        async with self.application() as client:
            await self.login(client)
            previous = main.app.state.ai.path.read_bytes()
            self.cloud.ai_error = RuntimeError(f"{ERROR_SECRET} {new_key} {AI['api_key']}")
            with self.assertLogs(level="ERROR") as logs, patch("app.ai.classify_records", new=AsyncMock(return_value=self.classification_result())):
                response = await client.put("/api/ai/config", json={"api_key": new_key}, headers={"Origin": "http://testserver"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(main.app.state.ai.path.read_bytes(), previous)
            self.assertEqual(main.app.state.ai.key(), AI["api_key"])
            self.assertEqual(stat.S_IMODE(main.app.state.ai.path.stat().st_mode), 0o600)
            self.assertFalse(main.app.state.busy)
            for secret in (ERROR_SECRET, new_key, AI["api_key"]):
                self.assertNotIn(secret, response.text)
                self.assertNotIn(secret, " ".join(logs.output))
            self.assertEqual(self.cloud.saved_ai, [])

    async def test_failed_first_cloud_ai_configuration_leaves_no_local_key(self):
        async with self.application(restored_ai=None) as client:
            await self.login(client)
            self.cloud.ai_error = RuntimeError(ERROR_SECRET)
            with self.assertLogs(level="ERROR"), patch("app.ai.classify_records", new=AsyncMock(return_value=self.classification_result())):
                response = await client.put("/api/ai/config", json={"api_key": "new-private-api-key"}, headers={"Origin": "http://testserver"})
            self.assertEqual(response.status_code, 503)
            self.assertFalse(main.app.state.ai.path.exists())
            self.assertEqual(main.app.state.ai.public()["source"], "none")

    async def test_invalid_api_key_values_are_not_echoed_in_validation_errors(self):
        marker = "private-api-key-validation-fixture"
        async with self.application() as client:
            await self.login(client)
            with patch("app.ai.classify_records", new=AsyncMock()) as classify:
                for value in (marker + "x" * 1025, {"nested_key": marker}):
                    with self.subTest(value_type=type(value).__name__):
                        response = await client.put("/api/ai/config", json={"api_key": value}, headers={"Origin": "http://testserver"})
                        self.assertEqual(response.status_code, 422)
                        self.assertNotIn(marker, response.text)
                        self.assertTrue(response.json()["detail"])
                        for error in response.json()["detail"]:
                            self.assertEqual(set(error), {"type", "loc", "msg"})
                classify.assert_not_awaited()
            self.assertEqual(main.app.state.ai.key(), AI["api_key"])
            self.assertEqual(self.cloud.saved_ai, [])

    async def test_malformed_ai_json_does_not_echo_sensitive_body(self):
        marker = "private-api-key-malformed-json-fixture"
        async with self.application() as client:
            await self.login(client)
            with patch("app.ai.classify_records", new=AsyncMock()) as classify:
                response = await client.put(
                    "/api/ai/config", content='{"api_key":"' + marker + '","model":}',
                    headers={"Origin": "http://testserver", "Content-Type": "application/json"},
                )
                classify.assert_not_awaited()
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(marker, response.text)
            self.assertEqual(response.json()["detail"][0]["type"], "json_invalid")
            self.assertEqual(set(response.json()["detail"][0]), {"type", "loc", "msg"})
            self.assertEqual(main.app.state.ai.key(), AI["api_key"])
            self.assertEqual(self.cloud.saved_ai, [])

    async def test_snapshot_failures_warn_without_logging_contents_and_shutdown_checkpoints(self):
        async with self.application() as client:
            await self.login(client)
            await client.post("/api/browser/start", headers={"Origin": "http://testserver"})
            for failure in ("snapshot", "upload"):
                with self.subTest(failure=failure):
                    if failure == "snapshot":
                        self.browser.snapshot_error = RuntimeError(f"{ERROR_SECRET} {STATE['cookies'][0]['value']}")
                    else:
                        self.cloud.browser_error = RuntimeError(f"{ERROR_SECRET} {STATE['cookies'][0]['value']}")
                    with self.assertLogs(level="ERROR") as logs:
                        await main.checkpoint_browser()
                    response = await client.get("/api/status")
                    self.assertIn("로그인 상태를 저장하지 못했습니다", response.json()["session_warning"])
                    for secret in (ERROR_SECRET, STATE["cookies"][0]["value"]):
                        self.assertNotIn(secret, response.text)
                        self.assertNotIn(secret, " ".join(logs.output))
                    self.browser.snapshot_error = None
                    self.cloud.browser_error = None
            self.assertEqual(self.cloud.saved_browser, [])
        self.assertEqual(self.cloud.saved_browser, [STATE])
        self.assertTrue(self.browser.closed)
        self.assertEqual(self.events[-3:], ["snapshot", "save_browser", "close"])


if __name__ == "__main__":
    unittest.main()
