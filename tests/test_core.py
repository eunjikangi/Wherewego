import json
import os
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.classifier import classify, classify_records
from app.main import app, signed_session, valid_session
from app.store import Store, normalize_url


class ClassificationTests(unittest.TestCase):
    def test_rules_and_pending(self):
        self.assertEqual(classify({"text": "성수 카페 커피 디저트 추천"}), ("카페", "rules"))
        self.assertEqual(classify({"text": "제주 여행 숙소 추천"}), ("여행", "rules"))
        self.assertEqual(classify({"url": "https://food.com/123", "title": "food.com", "text": ""}), ("분류 보류", "pending"))
        self.assertEqual(classify({"title": "내용 확인이 필요한 게시물", "text": "https://travel.example/foo"}), ("분류 보류", "pending"))
        self.assertEqual(classify({"text": "여행 카페"}), ("분류 보류", "pending"))

    def test_url_validation(self):
        self.assertEqual(normalize_url("https://www.instagram.com/p/ABC/?igsh=secret#caption"), "https://www.instagram.com/p/ABC/")
        self.assertEqual(normalize_url("https://l.instagram.com/?u=https%3A%2F%2Fexample.com%2F%3Fid%3D3%26utm_source%3Dig"), "https://example.com/?id=3")
        self.assertEqual(normalize_url("javascript:alert(1)"), "")
        self.assertEqual(normalize_url("https://user:password@example.com/"), "")
        self.assertEqual(normalize_url("http://example.com:443/a"), "http://example.com:443/a")
        self.assertEqual(normalize_url("http://[::1]:9000/a"), "http://[::1]:9000/a")

    def test_dedup_and_manual_edits_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            record = {"url": "https://www.instagram.com/p/ABC/?igsh=1", "title": "카페 추천", "text": "커피", "source": "saved", "category": "카페", "classification": "rules"}
            self.assertEqual(store.upsert([record]), (1, 0))
            item = store.all()[0]
            store.update(item["id"], "문화·취미", "친구에게 추천")
            record.update(url="https://www.instagram.com/p/ABC/", source="dm", text="커피 디저트 카페 추천")
            self.assertEqual(store.upsert([record]), (0, 1))
            item = store.all()[0]
            self.assertEqual(store.total(), 1)
            self.assertEqual(item["sources"], ["saved", "dm"])
            self.assertEqual((item["category"], item["note"], item["classification"]), ("문화·취미", "친구에게 추천", "manual"))
            self.assertEqual(store.upsert([record]), (0, 0))
            self.assertEqual(len(store.all(q="친구", source="dm", category="문화·취미")), 1)

    def test_session_tampering_and_expiry(self):
        password = "test-password-long-enough"
        token = signed_session(password)
        self.assertTrue(valid_session(token, password))
        self.assertFalse(valid_session(token + "0", password))
        self.assertFalse(valid_session(token, "a-different-password"))
        self.assertFalse(valid_session(signed_session(password, int(time.time()) - 1), password))
        for invalid in ("", "broken", "====.invalid", "💬.a", "!@#$%.a"):
            self.assertFalse(valid_session(invalid, password))


class AIFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_ai_response_preserves_rule_results(self):
        for response in ({"choices": []}, {"choices": [{"message": {"content": '{"items":[null]}'}}]}):
            fake = unittest.mock.Mock()
            fake.json.return_value = response
            with patch.dict(os.environ, {"OPENAI_API_KEY": "not-a-real-key"}), patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake)):
                items, warning = await classify_records([{"text": "카페 커피"}], use_ai=True)
            self.assertEqual(items[0]["category"], "카페")
            self.assertTrue(warning)


class APITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environ = patch.dict(os.environ, {"ADMIN_PASSWORD": "test-password-long-enough", "DATA_DIR": self.directory.name, "PUBLIC_ORIGIN": "http://testserver"})
        self.environ.start()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.environ.stop()
        self.directory.cleanup()

    def login(self):
        response = self.client.post("/login", data={"password": "test-password-long-enough"}, headers={"Origin": "http://testserver"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn("HttpOnly", response.headers["set-cookie"])

    def test_auth_and_csrf_cover_api_and_browser(self):
        self.assertEqual(self.client.get("/api/items").status_code, 401)
        self.assertEqual(self.client.get("/browser/vnc.html", follow_redirects=False).status_code, 303)
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/browser/websockify", headers={"Origin": "http://testserver"}):
                pass
        self.login()
        self.assertEqual(self.client.get("/api/status").status_code, 200)
        self.assertEqual(self.client.post("/api/collect", json={"mode": "current"}, headers={"Origin": "https://wrong.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/collect", json={"mode": "current"}, headers={"Origin": "http://testserver"}).status_code, 409)

    def test_edit_export_and_validation(self):
        self.login()
        app.state.store.upsert([{"url": "https://example.com/?id=1", "title": "링크", "source": "dm"}])
        item = self.client.get("/api/items").json()["items"][0]
        headers = {"Origin": "http://testserver"}
        response = self.client.patch(f'/api/items/{item["id"]}', json={"category": "여행", "note": "가볼 곳"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["classification"], "manual")
        self.assertEqual(self.client.patch(f'/api/items/{item["id"]}', json={"category": "unknown"}, headers=headers).status_code, 422)
        exported = self.client.get("/api/export")
        self.assertEqual(exported.json()["items"][0]["note"], "가볼 곳")
        self.assertIn("attachment", exported.headers["content-disposition"])
        self.client.post("/api/logout", headers=headers)
        self.assertEqual(self.client.get("/api/status").status_code, 401)

    def test_collection_job_persists_results(self):
        self.login()
        app.state.browser._context = object()
        fake = [{"url": "https://www.instagram.com/p/ONE/", "title": "카페 커피", "text": "성수 카페 커피", "source": "saved"}]
        try:
            with patch.object(app.state.browser, "collect", new=AsyncMock(return_value=fake)):
                response = self.client.post("/api/collect", json={"mode": "current"}, headers={"Origin": "http://testserver"})
                self.assertEqual(response.status_code, 200)
                for _ in range(30):
                    status = self.client.get("/api/status").json()
                    if not status["busy"]:
                        break
                    time.sleep(.01)
                self.assertEqual(status["job"]["status"], "done")
                self.assertEqual(status["total"], 1)
                self.assertEqual(self.client.get("/api/items").json()["items"][0]["category"], "카페")
        finally:
            app.state.browser._context = None


if __name__ == "__main__":
    unittest.main()
