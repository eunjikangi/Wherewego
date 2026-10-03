"""Chromium fixtures for the extension's rendered-DM collection path."""
import os
from pathlib import Path
import unittest


CONTENT_JS = (Path(__file__).parents[1] / "extension" / "content.js").read_text()


@unittest.skipUnless(os.getenv("TEST_BROWSER") == "1", "Set TEST_BROWSER=1 to run Chromium DOM checks")
class DMCardDOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            executable_path=os.getenv("CHROMIUM_EXECUTABLE") or self.playwright.chromium.executable_path,
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        self.page = await self.browser.new_page(viewport={"width": 390, "height": 850})

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def screen(self, html):
        await self.page.route("https://www.instagram.com/**", lambda route: route.fulfill(
            body="<!doctype html><meta charset='utf-8'>" + html,
            content_type="text/html; charset=utf-8"))
        await self.page.goto("https://www.instagram.com/direct/t/123/")
        await self.page.evaluate(CONTENT_JS)

    async def capture(self, mode="current"):
        return await self.page.evaluate("mode => WherewegoCollector.collect(mode)", mode)

    async def test_no_main_narrow_dm_semantic_links_and_plain_text(self):
        await self.screen("""
          <aside><div role='link' data-url='/p/INBOX/'>다른 대화 비밀</div></aside>
          <div role='log' style='height:500px'>
            <div role='listitem'><div role='button' data-url='https%3A%2F%2Fwww.instagram.com%2Freel%2FSEMANTIC%2F'>
              부산 여행 <img alt='바닷가 카페' width='180' height='140'>
            </div></div>
            <div>평문으로 보낸 링크 https://www.instagram.com/p/LITERAL/</div>
          </div>""")
        result = await self.capture()
        self.assertEqual({item["url"] for item in result["records"]}, {
            "https://www.instagram.com/reel/SEMANTIC/", "https://www.instagram.com/p/LITERAL/"})
        self.assertTrue(all(item["source"] == "dm" for item in result["records"]))
        self.assertTrue(all("다른 대화 비밀" not in item["text"] for item in result["records"]))

    async def test_rolebutton_card_is_opened_only_in_explicit_resolve_mode(self):
        await self.screen("""
          <div role='log' style='height:500px'>
            <div role='listitem'>
              <div id='avatar' role='button' aria-label='프로필 보기'><img width='30' height='30'></div>
              <div id='card' role='button' aria-label='공유한 게시물'><div>
                <img alt='Photo by cafeowner' width='180' height='140'>성수 카페
              </div></div>
            </div>
          </div>
          <script>
            window.cardClicks=0; window.avatarClicks=0;
            document.querySelector('#avatar').onclick=()=>window.avatarClicks++;
            document.querySelector('#card').onclick=()=>{
              window.cardClicks++;
              const modal=document.createElement('div'); modal.setAttribute('role','dialog');
              modal.innerHTML=`<button aria-label='닫기'>닫기</button><article>
                <p>성수 카페 서울 성동구 연무장길 12 아메리카노 4500원</p>
                <a href='/p/OPENED/'><time>오늘</time></a>
                <a href='/reel/SUGGESTED/'>추천 릴스</a></article>`;
              modal.querySelector('button').onclick=()=>modal.remove();
              document.body.append(modal);
            };
          </script>""")
        current = await self.capture()
        self.assertEqual(current["records"], [])
        self.assertEqual(current["unresolved_cards"], 1)
        self.assertEqual(await self.page.evaluate("cardClicks"), 0)
        resolved = await self.capture("resolve")
        self.assertEqual(len(resolved["records"]), 1)
        self.assertEqual(resolved["records"][0]["url"], "https://www.instagram.com/p/OPENED/")
        self.assertIn("아메리카노 4500원", resolved["records"][0]["text"])
        self.assertEqual(resolved["records"][0]["source_url"], "https://www.instagram.com/direct/t/123/")
        self.assertEqual(resolved["unresolved_cards"], 0)
        self.assertEqual(await self.page.evaluate("avatarClicks"), 0)
        self.assertEqual(await self.page.locator('[role="dialog"]').count(), 0)

    async def test_attachment_and_opaque_id_are_never_invented_as_posts(self):
        await self.screen("""
          <div role='log' style='height:500px'>
            <div role='button' id='photo' data-id='1234567890'><img width='160' height='140'>사진</div>
            <div role='button' aria-label='전송 취소' id='unsend'><img width='180' height='140'></div>
          </div>
          <script>
            window.unsendClicks=0; document.querySelector('#unsend').onclick=()=>window.unsendClicks++;
            document.querySelector('#photo').onclick=()=>{
              const modal=document.createElement('div'); modal.setAttribute('role','dialog');
              modal.innerHTML=`<button aria-label='Close'>Close</button><img width='250' height='250'>`;
              modal.querySelector('button').onclick=()=>modal.remove(); document.body.append(modal);
            };
          </script>""")
        result = await self.capture("resolve")
        self.assertEqual(result["records"], [])
        self.assertEqual(result["unresolved_cards"], 1)
        self.assertEqual(await self.page.evaluate("unsendClicks"), 0)
        self.assertEqual(await self.page.locator('[role="dialog"]').count(), 0)

    async def test_lazy_rendered_permalink_and_card_opening_bound(self):
        await self.screen("""
          <div role='log' style='height:500px'></div>
          <script>
            window.openedCards=0;
            const log=document.querySelector('[role="log"]');
            for(let index=0;index<23;index++) {
              const card=document.createElement('div'); card.setAttribute('role','button');
              card.setAttribute('aria-label','공유한 릴스'); card.innerHTML='<img width="160" height="130">';
              card.onclick=()=>{
                openedCards++;
                const modal=document.createElement('div'); modal.setAttribute('role','dialog');
                modal.innerHTML='<button aria-label="Close">Close</button><article></article>';
                modal.querySelector('button').onclick=()=>modal.remove(); document.body.append(modal);
                setTimeout(()=>{modal.querySelector('article').innerHTML=`맛집 릴스 <a href='/reel/CARD${index}/'>게시물</a>`;},150);
              }; log.append(card);
            }
          </script>""")
        result = await self.capture("resolve")
        self.assertEqual(await self.page.evaluate("openedCards"), 20)
        self.assertEqual(len(result["records"]), 20)
        self.assertEqual(result["unresolved_cards"], 3)
        self.assertIn("최대 20개", result["notice"])

    async def test_scrolling_pane_with_composer_excludes_unlabelled_inbox(self):
        await self.screen("""
          <div style='height:180px;overflow:auto'>
            <div style='height:500px'><a href='/direct/t/OTHER/'>다른 대화</a><a href='/p/PRIVATE/'>다른 게시물</a></div>
          </div>
          <div id='messages' style='height:260px;overflow:auto'>
            <div style='height:600px'><div role='link' data-href='/p/REAL/'>열린 대화 공유 게시물</div></div>
          </div>
          <textarea aria-label='Message' style='width:340px;height:40px'></textarea>""")
        result = await self.capture()
        self.assertEqual([item["url"] for item in result["records"]], ["https://www.instagram.com/p/REAL/"])

    async def test_cancellation_restores_the_opened_modal(self):
        await self.screen("""
          <div role='log' style='height:500px'>
            <div role='button' id='card' aria-label='공유한 게시물'><img width='160' height='130'></div>
          </div>
          <script>
            window.cancelCapture=false;
            document.querySelector('#card').onclick=()=>{
              const modal=document.createElement('div'); modal.setAttribute('role','dialog');
              modal.innerHTML='<button aria-label="Close">Close</button><article>불러오는 중</article>';
              modal.querySelector('button').onclick=()=>modal.remove(); document.body.append(modal);
              window.cancelCapture=true;
            };
          </script>""")
        result = await self.page.evaluate("""async () => {
          try { await WherewegoCollector.collect('resolve', {cancelled: () => cancelCapture}); }
          catch(error) { return error.message; }
        }""")
        self.assertIn("중단", result)
        self.assertEqual(await self.page.locator('[role="dialog"]').count(), 0)

    async def test_post_route_waits_for_post_dom_and_returns_to_conversation(self):
        await self.screen("""
          <main><div role='log' style='height:500px'>
            <div role='button' id='card' aria-label='공유한 게시물'><img width='160' height='130'></div>
            <div>친구의 비공개 대화 내용</div>
          </div></main>
          <script>
            const log=document.querySelector('[role="log"]');
            document.querySelector('#card').onclick=()=>{
              history.pushState(null,'','/p/ROUTED/');
              setTimeout(()=>{
                log.style.display='none';
                const post=document.createElement('div'); post.setAttribute('role','article'); post.id='post'; post.textContent='진짜 게시물 카페 메뉴';
                document.querySelector('main').append(post);
              },150);
            };
            addEventListener('popstate',()=>{document.querySelector('#post')?.remove();log.style.display='block';});
          </script>""")
        result = await self.capture("resolve")
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["url"], "https://www.instagram.com/p/ROUTED/")
        self.assertIn("진짜 게시물", result["records"][0]["text"])
        self.assertNotIn("비공개 대화", result["records"][0]["text"])
        self.assertEqual(self.page.url, "https://www.instagram.com/direct/t/123/")


if __name__ == "__main__":
    unittest.main()
