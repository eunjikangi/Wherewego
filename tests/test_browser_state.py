import os
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.browser import BrowserManager


STATE = {
    'cookies': [{'name': 'sessionid', 'value': 'fixture-value', 'domain': '.instagram.com', 'path': '/'}],
    'origins': [{'origin': 'https://www.instagram.com', 'localStorage': [{'name': 'fixture-key', 'value': 'fixture-value'}]}],
}


class BrowserStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_returns_none_until_browser_is_ready(self):
        self.assertIsNone(await BrowserManager().snapshot())

    async def test_snapshot_returns_current_context_state(self):
        manager = BrowserManager()
        manager._context = Mock(storage_state=AsyncMock(return_value=STATE))
        self.assertEqual(await manager.snapshot(), STATE)
        manager._context.storage_state.assert_awaited_once_with()

    async def test_state_is_detached_and_invalid_state_is_rejected(self):
        state = {'cookies': [], 'origins': []}
        manager = BrowserManager(restored_state=state)
        state['cookies'].append({'changed': True})
        self.assertEqual(manager._restored_state, {'cookies': [], 'origins': []})
        for values in ({}, {'cookies': 'private-data', 'origins': []}, {'cookies': [], 'origins': [{'origin': 'javascript:example'}]}):
            with self.assertRaises(ValueError):
                BrowserManager(restored_state=values)

    async def test_restore_happens_before_first_navigation_and_only_once(self):
        events = []
        class Page:
            url = 'about:blank'
            async def add_init_script(self, script):
                self.script = script
                events.append('script')
            async def goto(self, url, **kwargs):
                events.append('goto')
                self.url = url
        class Context:
            def __init__(self): self.pages = [Page()]
            def on(self, *args): pass
            async def add_cookies(self, cookies):
                events.append('cookies')
                self.cookies = cookies
        context = Context()
        playwright = Mock()
        playwright.chromium.launch_persistent_context = AsyncMock(return_value=context)
        launcher = Mock(start=AsyncMock(return_value=playwright))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'DATA_DIR': directory}), patch('playwright.async_api.async_playwright', return_value=launcher):
            manager = BrowserManager(restored_state=STATE)
            first = await manager.start()
            second = await manager.start()
        self.assertIs(first, second)
        self.assertEqual(events, ['cookies', 'script', 'goto'])
        self.assertEqual(context.cookies, STATE['cookies'])
        self.assertIsNone(manager._restored_state)
        playwright.chromium.launch_persistent_context.assert_awaited_once()

    async def test_default_local_profile_does_not_apply_cloud_state(self):
        page = Mock(url='https://www.instagram.com/')
        page.add_init_script = AsyncMock()
        context = Mock(pages=[page], add_cookies=AsyncMock())
        playwright = Mock()
        playwright.chromium.launch_persistent_context = AsyncMock(return_value=context)
        launcher = Mock(start=AsyncMock(return_value=playwright))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'DATA_DIR': directory}), patch('playwright.async_api.async_playwright', return_value=launcher):
            await BrowserManager().start()
        context.add_cookies.assert_not_awaited()
        page.add_init_script.assert_not_awaited()


@unittest.skipUnless(os.getenv('TEST_BROWSER') == '1', 'Set TEST_BROWSER=1 to run Chromium restore checks')
class BrowserStateDOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            executable_path=(os.getenv('CHROMIUM_EXECUTABLE') or self.playwright.chromium.executable_path), headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage'],
        )
        self.context = await self.browser.new_context()
        await self.context.route('**/*', lambda route: route.fulfill(body='<main>Local fixture</main>', content_type='text/html'))

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def test_origin_matching_single_use_and_safe_string_values(self):
        state = {
            'cookies': [],
            'origins': [{'origin': 'https://www.instagram.com', 'localStorage': [
                {'name': 'fixture-key', 'value': 'saved-value'},
                {'name': 'quoted-key', 'value': '"; window.injectionRan = true; // </script> 한글'},
            ]}],
        }
        page = await self.context.new_page()
        await page.add_init_script(BrowserManager._storage_restore_script(state['origins']))
        await page.goto('https://example.com/')
        self.assertIsNone(await page.evaluate('localStorage.getItem("fixture-key")'))
        await page.goto('https://www.instagram.com/')
        self.assertEqual(await page.evaluate('localStorage.getItem("fixture-key")'), 'saved-value')
        self.assertIsNone(await page.evaluate('window.injectionRan'))
        self.assertEqual(await page.evaluate('localStorage.getItem("quoted-key")'), state['origins'][0]['localStorage'][1]['value'])
        await page.evaluate('localStorage.setItem("fixture-key", "new-login-value")')
        await page.reload()
        self.assertEqual(await page.evaluate('localStorage.getItem("fixture-key")'), 'new-login-value')
        await page.evaluate('localStorage.clear()')
        await page.reload()
        self.assertIsNone(await page.evaluate('localStorage.getItem("fixture-key")'))
        later = await self.context.new_page()
        await later.goto('https://www.instagram.com/')
        self.assertIsNone(await later.evaluate('localStorage.getItem("fixture-key")'))

    async def test_existing_origin_values_take_precedence(self):
        page = await self.context.new_page()
        await page.goto('https://www.instagram.com/')
        await page.evaluate('localStorage.setItem("fixture-key", "current-value")')
        await page.add_init_script(BrowserManager._storage_restore_script(STATE['origins']))
        await page.reload()
        self.assertEqual(await page.evaluate('localStorage.getItem("fixture-key")'), 'current-value')


if __name__ == '__main__':
    unittest.main()
