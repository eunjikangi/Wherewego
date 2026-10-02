import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock

from app.browser import BrowserManager, EXTRACT_JS


class BrowserScopeTests(unittest.TestCase):
    def test_supported_and_rejected_screens(self):
        for path in ("/someone/saved/", "/saved/collection/", "/direct/t/123/", "/p/ABC/", "/reel/DEF/"):
            BrowserManager._check_url("https://www.instagram.com" + path)
        for url in ("https://evil.example/p/ABC/", "https://www.instagram.com/", "https://www.instagram.com/direct/inbox/", "https://www.instagram.com/accounts/login/", "https://www.instagram.com/challenge/"):
            with self.assertRaises(ValueError):
                BrowserManager._check_url(url)


class ScrollRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_screens_do_not_stop_moving_scroll(self):
        manager = BrowserManager()
        class FakePage:
            url = "https://www.instagram.com/direct/t/123/"
            scans = 0
            def locator(self, selector):
                return self
            async def evaluate_all(self, expression):
                return False
            async def evaluate(self, expression):
                if expression == EXTRACT_JS:
                    self.scans += 1
                    if self.scans >= 6:
                        return [{"url": "https://www.instagram.com/p/OLD/", "text": "오래된 공유 링크", "title": "오래된 링크", "source": "dm"}]
                    return []
                return {"moved": self.scans < 6}
        page = FakePage()
        manager._active_page = AsyncMock(return_value=page)
        with unittest.mock.patch("app.browser.asyncio.sleep", new=AsyncMock()):
            result = await manager.collect(mode="scroll")
        self.assertEqual(len(result), 1)
        self.assertGreaterEqual(page.scans, 6)


@unittest.skipUnless(os.getenv("TEST_BROWSER") == "1", "Set TEST_BROWSER=1 to run Chromium DOM checks")
class DOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.p = await async_playwright().start()
        self.browser = await self.p.chromium.launch(executable_path=(os.getenv("CHROMIUM_EXECUTABLE") or self.p.chromium.executable_path), headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        self.page = await self.browser.new_page()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.p.stop()

    async def screen(self, path, html):
        await self.page.route("https://www.instagram.com/**", lambda route: route.fulfill(body=html, content_type="text/html; charset=utf-8"))
        await self.page.goto("https://www.instagram.com" + path)
        return await self.page.evaluate(EXTRACT_JS)

    async def test_saved_card_links_exclude_navigation_and_hidden(self):
        records = await self.screen("/person/saved/", """<nav><a href='/p/NAV/'>네비게이션</a></nav><main>
          <article><a href='/p/CAFE/?igsh=track'>카페 커피 디저트</a></article>
          <article><a href='/reel/TRAVEL/'><img alt='제주 여행 숙소' width='100' height='100'></a></article>
          <article hidden><a href='/p/HIDDEN/'>숨겨진 항목</a></article>
          <a href='/person/'>프로필</a></main>""")
        self.assertEqual({r["url"] for r in records}, {"https://www.instagram.com/p/CAFE/", "https://www.instagram.com/reel/TRAVEL/"})
        self.assertTrue(all(record["source"] == "saved" for record in records))
        self.assertIn("제주 여행", records[1]["text"])

    async def test_dm_only_active_conversation_links_and_safe_protocols(self):
        records = await self.screen("/direct/t/123/", """<main><aside><a href='https://example.com/inbox'>다른 대화</a></aside>
          <div role='log'><div role='listitem'>카페 추천 <a href='https://www.instagram.com/p/DM1/'>공유 게시물</a></div>
          <div role='listitem'><a href='https://l.instagram.com/?u=https%3A%2F%2Fexample.com%2Fmenu'>외부 링크</a></div>
          <a href='javascript:alert(1)'>실행 링크</a></div></main>""")
        self.assertEqual({r["url"] for r in records}, {"https://www.instagram.com/p/DM1/", "https://example.com/menu"})
        self.assertTrue(all(record["source"] == "dm" for record in records))

    async def test_detail_without_self_link(self):
        records = await self.screen("/p/DETAIL/", """<meta property='og:title' content='maker on Instagram: 제주 여행 숙소'>
          <main><article><h1>제주 여행 숙소</h1><p>제주 여행에 추천하는 호텔</p></article></main>""")
        self.assertEqual(records[0]["url"], "https://www.instagram.com/p/DETAIL/")
        self.assertIn("제주 여행", records[0]["text"])


if __name__ == "__main__":
    unittest.main()
