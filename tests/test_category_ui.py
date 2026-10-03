"""Exercise real category controls, grounded place cards and media import preview."""
import base64
import io
import json
import os
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlsplit

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def jpeg_data():
    stream = io.BytesIO()
    Image.new("RGB", (24, 24), "#4477aa").save(stream, format="JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(stream.getvalue()).decode()


@unittest.skipUnless(os.getenv("TEST_BROWSER") == "1", "Set TEST_BROWSER=1 for category UI checks")
class CategoryUITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            executable_path=os.getenv("CHROMIUM_EXECUTABLE") or self.playwright.chromium.executable_path,
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.requests = []
        self.posts = []
        self.records = [
            {"id": "cafe", "url": "https://www.instagram.com/p/CAFE/", "title": "카페 소개",
             "text": "성수 카페", "category": "카페", "classification": "ai", "sources": ["saved", "dm"],
             "thumbnail": jpeg_data(), "details": {
                 "summary": "성수에서 커피와 디저트를 먹을 수 있는 곳", "media_status": "analyzed", "media_count": 2,
                 "places": [{"name": "모아커피", "address": "서울 성동구 연무장길 10", "area": "성수",
                             "menus": [{"name": "버터 스콘", "price": "5,000원"}], "hours": "10:00–20:00",
                             "evidence": "모아커피 / 연무장길 10"}], "tags": ["디저트"],
                 "ocr_text": "모아커피\n서울 성동구 연무장길 10\n버터 스콘 5,000원"}},
            {"id": "food", "url": "https://www.instagram.com/p/FOOD/", "title": "제주 맛집",
             "text": "제주 국수", "category": "맛집", "classification": "manual", "sources": ["dm"],
             "details": {"places": [{"name": "제주국수", "area": "제주", "address": "", "menus": []}],
                         "media_status": "partial", "media_count": 1}},
            {"id": "pending", "url": "https://www.instagram.com/reel/UNKNOWN/",
             "title": '<img src=x onerror="window.__injected=true">', "text": "", "category": "분류 보류",
             "classification": "pending", "sources": ["saved"], "thumbnail": "https://tracker.invalid/photo.jpg",
             "details": {"places": [], "media_status": "none"}},
        ]
        await self.context.route("**/*", self.route)
        await self.page.goto("http://library.test/")
        await self.page.wait_for_function("document.querySelectorAll('.item-card').length === 3")
        await self.page.wait_for_function("document.getElementById('overview-total').textContent === '전체 3개'")

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def route(self, route):
        request = route.request
        parsed = urlsplit(request.url)
        self.requests.append((request.method, parsed.path))
        if parsed.hostname != "library.test":
            await route.abort()
            return
        path = parsed.path
        if request.method == "POST":
            self.posts.append((path, request.post_data_json))
            data = self.status()
        elif path == "/api/status":
            data = self.status()
        elif path == "/api/library/overview":
            data = {"total": 3, "category_counts": {"카페": 1, "맛집": 1, "분류 보류": 1},
                    "source_counts": {"saved": 2, "dm": 2, "post": 0}, "needs_media": 2,
                    "areas": ["성수", "제주"]}
        elif path == "/api/items":
            params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
            items = list(self.records)
            if params.get("category"):
                items = [item for item in items if item["category"] == params["category"]]
            if params.get("source"):
                items = [item for item in items if params["source"] in item["sources"]]
            if params.get("q"):
                items = [item for item in items if params["q"] in json.dumps(item, ensure_ascii=False)]
            if params.get("area"):
                items = [item for item in items if any(place.get("area") == params["area"]
                                                     for place in item["details"].get("places", []))]
            if params.get("needs_media") == "true":
                items = [item for item in items if item["category"] == "분류 보류" or item["details"].get("media_status") in ("failed", "partial")]
            data = {"items": items}
        elif path == "/api/ai/config":
            data = {"configured": True, "provider": "gemini", "model": "gemini-flash-latest"}
        else:
            filename = "index.html" if path == "/" else path.rsplit("/", 1)[-1]
            local = ROOT / "static" / filename
            if filename not in {"index.html", "style.css", "app.js", "login-import.js"}:
                await route.fulfill(status=404, body="Not found")
                return
            content_type = "text/html" if filename.endswith(".html") else "text/css" if filename.endswith(".css") else "application/javascript"
            await route.fulfill(status=200, body=local.read_bytes(), content_type=content_type)
            return
        await route.fulfill(status=200, json=data)

    def status(self):
        return {"browser_ready": False, "busy": False, "total": 3,
                "categories": ["맛집", "카페", "여행", "분류 보류"], "ai_available": True,
                "job": {"status": "idle", "message": ""}}

    async def wait_cards(self, ids):
        await self.page.wait_for_function(
            "ids => JSON.stringify(Array.from(document.querySelectorAll('.item-card')).map(x => x.dataset.itemId)) === JSON.stringify(ids)", arg=ids)

    async def test_category_source_area_search_and_reset_keep_global_totals(self):
        cafe = self.page.locator("#category-tiles .category-tile").filter(has_text="카페")
        await cafe.click()
        await self.wait_cards(["cafe"])
        self.assertEqual(await cafe.get_attribute("aria-pressed"), "true")
        # Counts always describe the whole library, even when the results are one card.
        self.assertEqual(await self.page.locator("[data-source-count=saved]").inner_text(), "2")
        self.assertIn("1개", await self.page.locator("#category-tiles .category-tile").filter(has_text="맛집").inner_text())
        await self.page.locator(".source-button[data-source=dm]").click()
        await self.page.locator("#area-filter").select_option("성수")
        await self.page.locator("#search-input").fill("스콘")
        await self.page.wait_for_function("document.querySelectorAll('.active-filter').length === 4")
        await self.wait_cards(["cafe"])
        self.assertEqual(await self.page.locator("#overview-total").inner_text(), "전체 3개")
        await self.page.get_by_role("button", name="‘스콘’ 검색 필터 해제").click()
        await self.page.locator("#reset-filters").click()
        await self.wait_cards(["cafe", "food", "pending"])
        self.assertEqual(await self.page.locator("#search-input").input_value(), "")
        self.assertEqual(await self.page.locator("#area-filter").input_value(), "")
        self.assertTrue(await self.page.locator("#filter-summary").is_hidden())
        await self.page.locator("#pending-filter").click()
        await self.wait_cards(["pending"])
        await self.page.locator("#reset-filters").click()
        await self.page.locator("#needs-media-filter").click()
        await self.wait_cards(["food", "pending"])
        self.assertEqual(self.errors, [])

    async def test_place_cards_show_grounded_menus_maps_and_safe_thumbnails(self):
        card = self.page.locator('[data-item-id="cafe"]')
        self.assertIn("서울 성동구 연무장길 10", await card.inner_text())
        self.assertIn("버터 스콘", await card.inner_text())
        self.assertIn("5,000원", await card.inner_text())
        self.assertEqual(await card.locator("img").count(), 1)
        google = await card.get_by_role("link", name="모아커피 Google 지도에서 주소 검색, 새 창").get_attribute("href")
        self.assertEqual(parse_qs(urlsplit(google).query)["query"], ["서울 성동구 연무장길 10"])
        self.assertEqual(await self.page.locator('[data-item-id="food"] .map-link').count(), 0)
        pending = self.page.locator('[data-item-id="pending"]')
        self.assertEqual(await pending.locator("img").count(), 0)
        self.assertIn("<img", await pending.inner_text())
        self.assertFalse(await self.page.evaluate("Boolean(window.__injected)"))
        await card.locator(".card-evidence summary").click()
        self.assertIn("버터 스콘 5,000원", await card.locator(".ocr-text").first.inner_text())
        await self.page.locator("#view-list").click()
        self.assertIn("list-view", await self.page.locator("#items-grid").get_attribute("class"))
        await self.page.set_viewport_size({"width": 390, "height": 844})
        self.assertFalse(await self.page.evaluate("document.documentElement.scrollWidth > innerWidth"))
        self.assertEqual(self.errors, [])

    async def test_media_preview_requires_confirmation_and_sends_extract_details(self):
        await self.page.locator("#import-paste-panel").evaluate("element => element.open = true")
        record = {"url": "https://www.instagram.com/p/SLIDES/", "title": "사진으로 된 메뉴",
                  "text": "", "source": "post", "thumbnail": jpeg_data(),
                  "media": [{"data_url": jpeg_data(), "kind": "image", "index": 1},
                            {"data_url": jpeg_data(), "kind": "video_frame", "index": 2, "time_seconds": 1.5}]}
        raw = json.dumps({"version": 1, "records": [record]}, ensure_ascii=False)
        await self.page.locator("#import-paste-input").fill(raw)
        await self.page.locator("#import-paste-preview").click()
        await self.page.locator("#import-preview").wait_for(state="visible")
        self.assertIn("사진·동영상 프레임 2장", await self.page.locator("#import-summary").inner_text())
        self.assertEqual(await self.page.locator("#import-list img").count(), 2)
        self.assertEqual(self.posts, [])
        await self.page.wait_for_function("document.getElementById('use-ai').checked")
        await self.page.locator("#import-confirm").click()
        await self.page.wait_for_function("document.getElementById('import-confirm').textContent === '저장·분류 중'")
        body = self.posts[0][1]
        self.assertTrue(body["extract_details"])
        self.assertTrue(body["use_ai"])
        self.assertEqual(body["records"], [record])
        self.assertEqual(self.errors, [])

    async def test_large_manual_import_is_preserved_when_expired_session_cannot_store_it(self):
        raw = json.dumps({"version": 1, "records": [
            {"url": f"https://www.instagram.com/p/MEDIA{i}/", "title": "x" * 250,
             "text": "한" * 2500, "source": "post"} for i in range(250)]}, ensure_ascii=False)
        self.assertGreater(len(raw.encode()), 1536 * 1024)
        await self.page.locator("#import-paste-panel").evaluate("element => element.open = true")
        await self.page.evaluate("() => { Storage.prototype.setItem = () => { throw new DOMException('quota'); }; }")
        await self.page.locator("#import-paste-input").fill(raw)
        await self.page.locator("#import-paste-preview").click()
        await self.page.locator("#import-preview").wait_for(state="visible")
        self.assertIn("탭을 닫지", await self.page.locator("#import-error").inner_text())
        async def expired(route):
            await route.fulfill(status=401, json={"detail": "expired"})
        await self.page.route("**/api/import", expired)
        await self.page.locator("#import-confirm").click()
        await self.page.locator("#import-error").filter(has_text="새 탭").wait_for(state="visible")
        self.assertEqual(self.page.url, "http://library.test/")
        self.assertEqual(await self.page.locator("#import-paste-input").input_value(), raw)
        self.assertTrue(await self.page.locator("#import-preview").is_visible())
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
