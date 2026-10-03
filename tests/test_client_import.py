import io
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import test_core
from app.main import EXTENSION_FILES, IMPORT_BODY_LIMIT, app


class ClientImportTests(test_core.APITests):
    test_auth_and_csrf_cover_api_and_browser = None
    test_collection_job_persists_results = None
    test_edit_export_and_validation = None

    headers = {"Origin": "http://testserver"}

    def record(self, **changes):
        result = {
            "url": "https://www.instagram.com/p/IMPORT_ONE/?igsh=tracking",
            "title": "카페 커피", "text": "성수 카페 디저트", "source": "saved",
            "source_url": "https://www.instagram.com/me/saved/?ignored=private",
        }
        result.update(changes)
        return result

    def wait_for_job(self):
        for _ in range(100):
            result = self.client.get("/api/status").json()
            if not result["busy"]:
                break
            time.sleep(.01)
        self.assertFalse(result["busy"])
        self.assertEqual(result["job"]["status"], "done")
        return result

    def submit(self, records, **changes):
        body = {"records": records, "use_ai": False}
        body.update(changes)
        return self.client.post("/api/import", json=body, headers=self.headers)

    def test_import_requires_admin_session_and_same_origin(self):
        body = {"records": [self.record()]}
        self.assertEqual(self.client.post("/api/import", json=body, headers=self.headers).status_code, 401)
        self.assertEqual(self.client.get("/extension.zip", follow_redirects=False).status_code, 303)
        self.login()
        self.assertEqual(self.client.post("/api/import", json=body,
                         headers={"Origin": "https://wrong.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/import", json=body).status_code, 403)
        app.state.busy = True
        try:
            self.assertEqual(self.submit([self.record()]).status_code, 409)
        finally:
            app.state.busy = False
        self.assertEqual(app.state.store.total(), 0)

    def test_import_does_not_start_or_read_remote_browser(self):
        self.login()
        with patch.object(app.state.browser, "start", new=AsyncMock()) as start, patch.object(
            app.state.browser, "collect", new=AsyncMock()
        ) as collect, patch.object(app.state.browser, "enrich", new=AsyncMock()) as enrich:
            response = self.submit([self.record()])
            self.assertEqual(response.status_code, 200)
            result = self.wait_for_job()
            start.assert_not_awaited()
            collect.assert_not_awaited()
            enrich.assert_not_awaited()
        self.assertEqual((result["job"]["added"], result["total"]), (1, 1))
        item = self.client.get("/api/items").json()["items"][0]
        self.assertEqual(item["url"], "https://www.instagram.com/p/IMPORT_ONE/")
        self.assertEqual((item["category"], item["sources"]), ("카페", ["saved"]))

    def test_duplicate_source_merging_keeps_manual_category_and_note(self):
        self.login()
        self.assertEqual(self.submit([self.record()]).status_code, 200)
        self.wait_for_job()
        item = app.state.store.all()[0]
        app.state.store.update(item["id"], "문화·취미", "친구와 갈 곳")
        changed = self.record(
            url="http://instagram.com/p/IMPORT_ONE#caption", source="dm",
            source_url="https://www.instagram.com/direct/t/123/",
            text="성수 카페 디저트 친구에게 공유한 게시물",
        )
        self.assertEqual(self.submit([changed]).status_code, 200)
        result = self.wait_for_job()
        self.assertEqual((result["job"]["added"], result["job"]["updated"]), (0, 1))
        item = app.state.store.all()[0]
        self.assertEqual(len(app.state.store.all()), 1)
        self.assertEqual(item["sources"], ["saved", "dm"])
        self.assertEqual((item["category"], item["classification"], item["note"]),
                         ("문화·취미", "manual", "친구와 갈 곳"))

    def test_strict_schema_bounds_and_invalid_links_do_not_echo_private_input(self):
        self.login()
        marker = "private-password-fixture"
        bad_records = [
            self.record(url="javascript:alert(1)"),
            self.record(url="https://user:" + marker + "@www.instagram.com/p/ABC/"),
            self.record(url="https://www.instagram.com/accounts/login/"),
            self.record(url="https://www.instagram.com/p/ABC/extra"),
            self.record(url="https://www.instagram.com/p/%2F/"),
            self.record(url="https://www.instagram.com:9000/p/ABC/"),
            self.record(url="https://example.com/", source="saved"),
            self.record(url="https://example.com/", source="post"),
            self.record(url="https://www.instagram.com/p/ABC/\\extra"),
            self.record(source_url="https://outside.example/thread"),
            self.record(source_url="https://user:" + marker + "@instagram.com/direct/t/123"),
            self.record(source="unsupported"),
            self.record(cookies=marker),
            self.record(password=marker),
            self.record(category="카페"),
            self.record(title="x" * 251),
            self.record(text="x" * 2501),
            self.record(url="https://example.com/" + "x" * 2048, source="dm"),
            self.record(text=123),
        ]
        for record in bad_records:
            with self.subTest(record_keys=list(record)):
                response = self.submit([record])
                self.assertEqual(response.status_code, 422)
                self.assertNotIn(marker, response.text)
        for body in (
            {"records": []},
            {"records": [self.record()] * 501},
            {"records": [self.record()], "cookies": marker},
            {"records": [self.record()], "use_ai": "false"},
        ):
            response = self.client.post("/api/import", json=body, headers=self.headers)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(marker, response.text)
        self.assertEqual(app.state.store.total(), 0)

    def test_dm_external_links_are_normalized_without_fetching_them(self):
        self.login()
        with patch("httpx.AsyncClient.get", new=AsyncMock()) as fetch:
            response = self.submit([self.record(
                url="https://example.com/place?id=3&utm_source=ig#details", source="dm",
                title="제주 여행", text="제주 여행 숙소", source_url=None,
            )])
            self.assertEqual(response.status_code, 200)
            self.wait_for_job()
            fetch.assert_not_awaited()
        item = app.state.store.all()[0]
        self.assertEqual(item["url"], "https://example.com/place?id=3")
        self.assertEqual((item["category"], item["sources"]), ("여행", ["dm"]))

    def test_import_checks_missing_ai_key_before_creating_job(self):
        self.login()
        with patch.object(app.state.ai, "key", return_value=None):
            self.assertEqual(self.submit([self.record()], use_ai=True).status_code, 422)
        self.assertFalse(app.state.busy)
        self.assertEqual(app.state.store.total(), 0)

    def test_import_uses_selected_ai_provider_and_keeps_warning(self):
        self.login()
        marker = "private-gemini-key-fixture"

        async def classify(records, use_ai, **settings):
            self.assertTrue(use_ai)
            self.assertEqual(settings, {
                "api_key": marker, "model": "gemini-flash-latest", "provider": "gemini",
            })
            self.assertEqual(records[0]["source_url"], "https://www.instagram.com/me/saved/")
            return [dict(record, category="여행", classification="ai") for record in records], "일부 설명을 확인하지 못했습니다."

        with patch.object(app.state.ai, "key", return_value=marker), patch.object(
            app.state.ai, "model", return_value="gemini-flash-latest"
        ), patch.object(app.state.ai, "provider", return_value="gemini"), patch(
            "app.main.classify_records", new=AsyncMock(side_effect=classify)
        ) as classifier, patch.object(app.state.browser, "enrich", new=AsyncMock()) as enrich:
            response = self.submit([self.record()], use_ai=True)
            self.assertEqual(response.status_code, 200)
            result = self.wait_for_job()
            classifier.assert_awaited_once()
            enrich.assert_not_awaited()
        self.assertIn("일부 설명", result["job"]["message"])
        self.assertNotIn(marker, json.dumps(result))
        self.assertNotIn(marker, self.client.get("/api/export").text)
        self.assertEqual(app.state.store.all()[0]["classification"], "ai")

    def test_reclassification_uses_imported_text_without_remote_browser_by_default(self):
        self.login()
        app.state.store.upsert([self.record()])
        app.state.browser._context = object()

        async def classify(records, use_ai, **settings):
            return [dict(record, category="여행", classification="ai") for record in records], ""

        try:
            with patch.object(app.state.ai, "key", return_value="private-fixture"), patch(
                "app.main.classify_records", new=AsyncMock(side_effect=classify)
            ), patch.object(app.state.browser, "enrich", new=AsyncMock(
                side_effect=AssertionError("must not use remote Instagram login")
            )) as enrich:
                response = self.client.post("/api/classify", json={"only_pending": False}, headers=self.headers)
                self.assertEqual(response.status_code, 200)
                self.wait_for_job()
                enrich.assert_not_awaited()
            self.assertEqual(app.state.store.all()[0]["category"], "여행")
        finally:
            app.state.browser._context = None

    def test_large_or_non_json_body_is_rejected_before_import(self):
        self.login()
        headers = dict(self.headers, **{"Content-Type": "application/json"})
        response = self.client.post("/api/import", content=b"x" * (IMPORT_BODY_LIMIT + 1), headers=headers)
        self.assertEqual(response.status_code, 413)

        def chunks():
            yield b'{"records":['
            for _ in range(IMPORT_BODY_LIMIT // 65536 + 1):
                yield b"x" * 65536

        response = self.client.post("/api/import", content=chunks(), headers=headers)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.client.post("/api/import", content="not json",
                         headers=self.headers).status_code, 415)
        self.assertEqual(self.client.post("/api/import", content=b"{", headers=headers).status_code, 422)
        self.assertEqual(app.state.store.total(), 0)
        self.assertFalse(app.state.busy)

    def test_extension_zip_contains_only_allowlisted_files_and_is_deterministic(self):
        self.login()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extension = root / "extension"
            extension.mkdir()
            for name in EXTENSION_FILES:
                (extension / name).write_text("extension fixture " + name)
            (extension / "private-secret.txt").write_text("never-package-private-fixture")
            with patch("app.main.ROOT", root):
                first = self.client.get("/extension.zip")
                second = self.client.get("/extension.zip")
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.content, second.content)
            self.assertIn("attachment", first.headers["content-disposition"])
            with zipfile.ZipFile(io.BytesIO(first.content)) as bundle:
                self.assertEqual(bundle.namelist(), list(EXTENSION_FILES))
                self.assertTrue(all("private-secret" not in name for name in bundle.namelist()))
                self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in bundle.infolist()))

    def test_login_import_script_is_public_but_import_api_remains_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "login-import.js").write_text("console.log('fragment fixture');")
            with patch("app.main.STATIC", root):
                response = self.client.get("/login-import.js")
            self.assertEqual(response.status_code, 200)
            self.assertIn("application/javascript", response.headers["content-type"])
            self.assertIn("/login-import.js", self.client.get("/login").text)
            self.assertEqual(self.client.get("/login-import.js/extra", follow_redirects=False).status_code, 303)
            self.assertEqual(self.client.post("/api/import", json={"records": [self.record()]},
                             headers=self.headers).status_code, 401)


if __name__ == "__main__":
    unittest.main()
