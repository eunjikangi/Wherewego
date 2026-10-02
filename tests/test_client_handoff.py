"""Browser-level checks for extension -> Hosting -> login -> import preview.

The two hostnames model the cross-site Firebase/Cloud Run redirect without
contacting Instagram or requiring a real administrator credential.
"""
import html
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest
from urllib.parse import parse_qs, quote, urlsplit

from app.main import LOGIN_PAGE


ROOT = Path(__file__).resolve().parents[1]
STORAGE_KEY = "wherewego.pendingImport.v1"
CATEGORIES = ["맛집", "카페", "여행", "쇼핑·패션", "뷰티", "집·인테리어",
              "운동·건강", "공부·업무", "문화·취미", "분류 보류"]


class HandoffServer:
    def __init__(self):
        self.requests = []
        self.import_attempts = []
        self.imports = []
        self.login_posts = 0
        self.reject_next_import = False
        self.delay_next_job = False
        self.job_state = None
        self.pending_records = []
        self.records = [{
            "url": "https://www.instagram.com/p/CAFE/",
            "title": '<img src=x onerror="window.__injected = true"> 카페',
            "text": "서울 카페 커피 추천",
            "source": "saved",
            "source_url": "https://www.instagram.com/person/saved/",
        }]
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def send(self, code=200, body="", content_type="text/html; charset=utf-8",
                     location=None, cookie=None):
                raw = body.encode("utf-8") if isinstance(body, str) else body
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "same-origin")
                self.send_header("Content-Security-Policy",
                                 "default-src 'self'; script-src 'self'; "
                                 "style-src 'self' 'unsafe-inline'; "
                                 "img-src 'self' data: blob:; connect-src 'self'; "
                                 "frame-src 'self'; object-src 'none'; "
                                 "base-uri 'self'; form-action 'self'")
                if location:
                    self.send_header("Location", location)
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                self.end_headers()
                self.wfile.write(raw)

            def api(self, data, code=200):
                self.send(code, json.dumps(data, ensure_ascii=False),
                          "application/json; charset=utf-8")

            def authenticated(self):
                return "organizer_session=test-session" in self.headers.get("Cookie", "")

            def do_GET(self):
                path = urlsplit(self.path).path
                owner.requests.append(("GET", path, self.authenticated()))
                if path == "/source":
                    envelope = json.dumps({"version": 1, "records": owner.records},
                                          ensure_ascii=False, separators=(",", ":"))
                    fragment = quote(envelope, safe="~()*!.'-")
                    self.send(body='<a id="handoff" href="' +
                              html.escape("/handoff#wherewego=" + fragment, quote=True) +
                              '">앱으로 가져오기</a>')
                    return
                if path == "/handoff":
                    # Neither Hosting nor the authentication redirect names a
                    # fragment: Chromium must inherit the original payload.
                    self.send(302, location=owner.app_url + "/")
                    return
                if path == "/login":
                    self.send(body=LOGIN_PAGE.replace("ERROR", ""))
                    return
                if path in ("/login-import.js", "/app.js", "/static/app.js",
                            "/style.css", "/static/style.css"):
                    filename = path.rsplit("/", 1)[-1]
                    content_type = ("text/css; charset=utf-8" if filename.endswith(".css")
                                    else "application/javascript; charset=utf-8")
                    self.send(body=(ROOT / "static" / filename).read_bytes(),
                              content_type=content_type)
                    return
                if not self.authenticated():
                    if path.startswith("/api/"):
                        self.api({"detail": "로그인이 필요합니다."}, code=401)
                    else:
                        self.send(303, location="/login")
                    return
                if path == "/":
                    self.send(body=(ROOT / "static" / "index.html").read_bytes())
                elif path == "/api/status":
                    self.api(owner.status())
                elif path == "/api/items":
                    self.api({"items": owner.items()})
                elif path == "/api/ai/config":
                    self.api({"configured": True, "provider": "gemini",
                              "model": "gemini-flash-latest", "source": "saved"})
                else:
                    self.send(404, body="Not found")

            def do_POST(self):
                path = urlsplit(self.path).path
                owner.requests.append(("POST", path, self.authenticated()))
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                if path == "/login":
                    owner.login_posts += 1
                    values = parse_qs(raw.decode("utf-8"))
                    if values.get("password") != ["test-password"]:
                        self.send(401, body=LOGIN_PAGE.replace(
                            "ERROR", "<p>관리자 암호를 확인하세요.</p>"))
                        return
                    self.send(303, location="/",
                              cookie="organizer_session=test-session; Path=/; "
                                     "HttpOnly; SameSite=Strict")
                    return
                if not self.authenticated():
                    self.api({"detail": "로그인이 필요합니다."}, code=401)
                    return
                if path == "/api/import":
                    data = json.loads(raw)
                    owner.import_attempts.append(data)
                    if owner.reject_next_import:
                        owner.reject_next_import = False
                        self.api({"detail": "가져오기를 잠시 처리하지 못했습니다."}, code=503)
                        return
                    if owner.delay_next_job:
                        owner.delay_next_job = False
                        owner.pending_records = data["records"]
                        owner.job_state = "running"
                    else:
                        owner.imports.extend(data["records"])
                        owner.job_state = "done"
                    self.api(owner.status())
                    return
                self.api({"detail": "Unexpected mutation"}, code=404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        port = self.server.server_port
        self.app_url = f"http://127.0.0.1:{port}"
        self.source_url = f"http://localhost:{port}/source"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def finish_job(self, success):
        if success:
            self.imports.extend(self.pending_records)
        self.pending_records = []
        self.job_state = "done" if success else "error"

    def status(self):
        state = self.job_state or ("done" if self.imports else "idle")
        message = ("분류 작업 저장 실패" if state == "error" else
                   "가져오기 진행 중" if state == "running" else
                   "가져오기 완료" if self.imports else "자료를 가져오세요.")
        return {"browser_ready": False, "busy": state == "running",
                "total": len(self.imports), "categories": CATEGORIES,
                "ai_available": True, "session_warning": "",
                "job": {"status": state, "message": message,
                        "added": len(self.imports), "updated": 0}}

    def items(self):
        return [dict(record, id=f"item-{index}", category="카페",
                     classification="ai", sources=[record["source"]])
                for index, record in enumerate(self.imports)]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


@unittest.skipUnless(os.getenv("TEST_BROWSER") == "1",
                     "Set TEST_BROWSER=1 to run Chromium handoff checks")
class ClientHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.server = HandoffServer()
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            executable_path=(os.getenv("CHROMIUM_EXECUTABLE")
                             or self.playwright.chromium.executable_path),
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()
        self.server.close()

    async def handoff(self):
        await self.page.goto(self.server.source_url)
        await self.page.locator("#handoff").click()

    async def login(self):
        await self.page.locator("#password").fill("test-password")
        await self.page.locator("form button[type=submit]").click()
        await self.page.locator("#import-preview").wait_for(state="visible")
        await self.page.wait_for_function(
            "document.getElementById('use-ai').checked")

    def assert_no_remote_browser_or_automatic_upload(self):
        self.assertEqual(self.server.import_attempts, [])
        self.assertFalse(any(method == "POST" and path in (
            "/api/browser/start", "/api/collect", "/api/classify")
            for method, path, _ in self.server.requests))

    async def test_logged_out_fragment_survives_redirect_and_login_then_requires_confirm(self):
        await self.handoff()
        await self.page.wait_for_url(self.server.app_url + "/login")
        await self.page.wait_for_function(
            "(key) => document.readyState === 'complete' && "
            "location.pathname === '/login' && !location.hash && "
            "sessionStorage.getItem(key) !== null", arg=STORAGE_KEY)
        pending = await self.page.evaluate(
            "(key) => sessionStorage.getItem(key)", STORAGE_KEY)
        self.assertEqual(json.loads(pending)["records"], self.server.records)
        self.assertEqual(await self.page.evaluate("location.hash"), "")
        await self.login()
        self.assertEqual(await self.page.evaluate("location.hash"), "")
        self.assert_no_remote_browser_or_automatic_upload()
        preview = self.page.locator("#import-list")
        self.assertIn(self.server.records[0]["title"], await preview.inner_text())
        self.assertEqual(await preview.locator("img").count(), 0)
        self.assertFalse(await self.page.evaluate("Boolean(window.__injected)"))
        await self.page.locator("#import-confirm").click()
        await self.page.wait_for_function(
            "(key) => sessionStorage.getItem(key) === null", arg=STORAGE_KEY)
        self.assertEqual(len(self.server.import_attempts), 1)
        self.assertEqual(self.server.imports, self.server.records)
        self.assertTrue(self.server.import_attempts[0]["use_ai"])
        self.assertEqual(self.server.login_posts, 1)

    async def test_existing_strict_session_retries_same_origin_without_password_prompt(self):
        await self.context.add_cookies([{
            "name": "organizer_session", "value": "test-session",
            "url": self.server.app_url, "httpOnly": True, "sameSite": "Strict",
        }])
        await self.handoff()
        await self.page.locator("#import-preview").wait_for(state="visible")
        self.assertEqual(self.page.url, self.server.app_url + "/")
        self.assertEqual(self.server.login_posts, 0)
        # The external navigation omitted Strict cookies; the bridge then
        # created a same-origin retry on which the session became available.
        root_requests = [authenticated for method, path, authenticated
                         in self.server.requests if method == "GET" and path == "/"]
        self.assertIn(False, root_requests)
        self.assertEqual(root_requests[-1], True)
        self.assert_no_remote_browser_or_automatic_upload()

    async def test_rejected_and_failed_background_jobs_keep_data_until_successful_retry(self):
        await self.context.add_cookies([{
            "name": "organizer_session", "value": "test-session",
            "url": self.server.app_url, "httpOnly": True, "sameSite": "Strict",
        }])
        self.server.reject_next_import = True
        self.server.delay_next_job = True
        await self.handoff()
        await self.page.locator("#import-preview").wait_for(state="visible")
        await self.page.locator("#import-confirm").click()
        await self.page.locator("#import-error").wait_for(state="visible")
        pending = await self.page.evaluate(
            "(key) => sessionStorage.getItem(key)", STORAGE_KEY)
        self.assertEqual(json.loads(pending)["records"], self.server.records)
        self.assertEqual(self.server.imports, [])
        self.assertEqual(len(self.server.import_attempts), 1)

        # Accepting a background job does not mean its records are durable.
        async with self.page.expect_response(
                lambda response: response.url.endswith("/api/import")
                and response.request.method == "POST"):
            await self.page.locator("#import-confirm").click()
        pending = await self.page.evaluate(
            "(key) => sessionStorage.getItem(key)", STORAGE_KEY)
        self.assertEqual(json.loads(pending)["records"], self.server.records)
        self.assertEqual(self.server.imports, [])
        self.server.finish_job(success=False)
        await self.page.locator("#import-error").filter(
            has_text="분류 작업 저장 실패").wait_for(state="visible")
        pending = await self.page.evaluate(
            "(key) => sessionStorage.getItem(key)", STORAGE_KEY)
        self.assertEqual(json.loads(pending)["records"], self.server.records)

        self.server.delay_next_job = True
        async with self.page.expect_response(
                lambda response: response.url.endswith("/api/import")
                and response.request.method == "POST"):
            await self.page.locator("#import-confirm").click()
        pending = await self.page.evaluate(
            "(key) => sessionStorage.getItem(key)", STORAGE_KEY)
        self.assertEqual(json.loads(pending)["records"], self.server.records)
        self.server.finish_job(success=True)
        await self.page.wait_for_function(
            "(key) => sessionStorage.getItem(key) === null", arg=STORAGE_KEY)
        self.assertEqual(len(self.server.import_attempts), 3)
        self.assertEqual(self.server.imports, self.server.records)


if __name__ == "__main__":
    unittest.main()
