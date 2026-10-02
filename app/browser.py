"""User-operated Instagram browser; extraction reads the rendered DOM only."""

import asyncio
import inspect
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit


EXTRACT_JS = r"""() => {
  const sourceUrl = location.href;
  const isDM = /\/direct(?:\/|$)/.test(location.pathname);
  const source = isDM ? 'dm' : /\/saved(?:\/|$)/.test(location.pathname) ? 'saved' : 'post';
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    if (!el || el.closest('[hidden], [aria-hidden="true"]')) return false;
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && el.getClientRects().length > 0;
  };
  const instagram = host => host === 'instagram.com' || host.endsWith('.instagram.com');
  const meaningful = value => {
    const t = clean(value);
    if (!t || /^(?:photo|image|video) by\b/i.test(t) || /^(?:Instagram|photo|image|video|사진|이미지|view post|view reel|open post|게시물 보기|릴스 보기)$/i.test(t)) return '';
    return t;
  };
  const permalink = raw => {
    try {
      let u = new URL(raw, sourceUrl);
      if (!/^https?:$/.test(u.protocol)) return null;
      if (u.hostname === 'l.instagram.com') {
        const target = u.searchParams.get('u');
        if (!target) return null;
        u = new URL(target);
        if (!/^https?:$/.test(u.protocol)) return null;
      }
      if (instagram(u.hostname)) {
        const m = u.pathname.match(/^\/(p|reel|tv)\/([^/]+)(?:\/|$)/);
        if (!m) return null;
        return {url: `https://www.instagram.com/${m[1]}/${m[2]}/`, external: false};
      }
      if (!isDM) return null;
      u.hash = '';
      return {url: u.href, external: true};
    } catch (_) { return null; }
  };
  const dialogs = [...document.querySelectorAll('[role="dialog"]')].filter(visible);
  const mains = [...document.querySelectorAll('main, [role="main"]')].filter(visible);
  let scope = dialogs.at(-1) || mains.at(-1) || document.body;
  if (isDM) {
    const logs = [...scope.querySelectorAll('[role="log"]')].filter(visible);
    if (logs.length) scope = logs.at(-1);
    else {
      // Semantic message regions take precedence over a main that also contains the inbox.
      const regions = [...scope.querySelectorAll('[aria-label]')].filter(el =>
        visible(el) && !el.matches('nav, aside, header') &&
        /^(?:messages|message list|conversation|chat messages|메시지|대화)(?:\s|$)/i.test(el.getAttribute('aria-label') || '') &&
        el.querySelector('a[href]') && !el.querySelector('nav, aside'));
      if (regions.length) scope = regions.at(-1);
    }
  }
  const results = new Map();
  const put = item => {
    const previous = results.get(item.url);
    if (!previous) results.set(item.url, item);
    else {
      if (item.text.length > previous.text.length) previous.text = item.text;
      if (previous.title === '내용 확인이 필요한 게시물' && item.title !== previous.title) previous.title = item.title;
    }
  };
  const surrounding = anchor => {
    let card = anchor.closest('article, [role="listitem"], [role="row"], li');
    if (!card || !scope.contains(card)) {
      card = anchor;
      for (let n = 0; n < 5 && card.parentElement && card.parentElement !== scope; n++) {
        const p = card.parentElement;
        if (p.matches('nav, aside, header') || clean(p.innerText).length > 1200) break;
        // Stop before joining separate cards or messages into one description.
        const urls = [...p.querySelectorAll('a[href]')].map(a => permalink(a.href)).filter(Boolean);
        if (new Set(urls.map(u => u.url)).size > 1) break;
        card = p;
      }
    }
    const texts = [meaningful(card.innerText)];
    for (const img of card.querySelectorAll('img[alt]')) if (visible(img)) texts.push(meaningful(img.alt));
    const label = meaningful(anchor.getAttribute('aria-label'));
    if (label && !/^(?:open|view|go to|열기|보기)\b/i.test(label)) texts.push(label);
    return [...new Set(texts.filter(Boolean))].join(' ').slice(0, 1200);
  };
  for (const anchor of scope.querySelectorAll('a[href]')) {
    if (!visible(anchor) || anchor.closest('nav, aside, header, [role="navigation"]')) continue;
    const link = permalink(anchor.getAttribute('href'));
    if (!link) continue;
    const text = surrounding(anchor);
    const title = text ? text.slice(0, 140) : link.external ? new URL(link.url).hostname : '내용 확인이 필요한 게시물';
    put({url: link.url, title, text, source, source_url: sourceUrl});
  }
  // Detail pages sometimes contain no anchor pointing to their own permalink.
  const own = permalink(sourceUrl);
  if (own && !own.external) {
    const meta = meaningful(document.querySelector('meta[property="og:title"]')?.content);
    const headings = [...scope.querySelectorAll('h1')].filter(visible).map(el => meaningful(el.innerText)).filter(Boolean);
    const article = [...scope.querySelectorAll('article')].find(visible);
    const caption = article ? meaningful(article.innerText) : headings.join(' ');
    const titleText = meaningful((meta || '').replace(/^.*? on Instagram:\s*/i, '').replace(/^Instagram\s*[-:|]\s*/i, ''));
    const text = [...new Set([caption, titleText].filter(Boolean))].join(' ').slice(0, 1200);
    put({url: own.url, title: titleText.slice(0, 140) || headings[0]?.slice(0, 140) || '내용 확인이 필요한 게시물', text, source: 'post', source_url: sourceUrl});
  }
  return [...results.values()];
}"""


SCROLL_JS = r"""() => {
  const dm = /\/direct(?:\/|$)/.test(location.pathname);
  const visible = el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
  const scope = [...document.querySelectorAll('[role="dialog"], main, [role="main"]')].filter(visible).at(-1) || document.body;
  const candidates = [scope, ...scope.querySelectorAll('*')].filter(el => {
    if (!visible(el) || el.closest('nav, aside, header, [role="navigation"]')) return false;
    const style = getComputedStyle(el);
    return /(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight + 30 && el.clientHeight > 150;
  });
  if (dm) {
    // Inbox lists are normally on the left; prefer the active conversation on the right.
    const logs = candidates.filter(el => el.matches('[role="log"]') || el.closest('[role="log"]'));
    const right = candidates.filter(el => el.getBoundingClientRect().right > innerWidth * .65 && el.getBoundingClientRect().left > innerWidth * .25);
    const target = (logs.length ? logs : right).sort((a,b) => (b.matches('[role="log"]') - a.matches('[role="log"]')) || b.clientHeight - a.clientHeight)[0];
    if (!target) return {moved: false, reason: 'no_conversation_scroller'};
    const before = target.scrollTop;
    target.scrollBy({top: -Math.max(400, target.clientHeight * .75), behavior: 'instant'});
    return {moved: before !== target.scrollTop};
  }
  const target = candidates.sort((a,b) => b.clientHeight - a.clientHeight)[0];
  if (target && target !== document.body && target !== document.documentElement) {
    const before = target.scrollTop;
    target.scrollBy({top: Math.max(500, target.clientHeight * .8), behavior: 'instant'});
    return {moved: before !== target.scrollTop};
  }
  const before = scrollY;
  window.scrollBy({top: Math.max(500, innerHeight * .8), behavior: 'instant'});
  return {moved: before !== scrollY};
}"""


DETAIL_META_JS = r"""() => {
  const visible = el => !el.closest('[hidden], [aria-hidden="true"]') &&
    el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const scopes = [...document.querySelectorAll('[role="dialog"], main, [role="main"]')].filter(visible);
  const scope = scopes.at(-1) || document.body;
  return {
    title: document.querySelector('meta[property="og:title"]')?.content || '',
    descriptions: [...document.querySelectorAll('meta[property="og:description"], meta[name="description"]')]
      .map(el => el.content || ''),
    headings: [...scope.querySelectorAll('h1')].filter(visible).map(el => el.innerText || '')
  };
}"""


class BrowserManager:
    def __init__(self, restored_state=None):
        self._restored_state = None
        if restored_state is not None:
            from .cloud_state import CloudState
            self._restored_state = CloudState._browser_values(restored_state)
        self._playwright = None
        self._context = None
        self._page = None
        self._start_lock = asyncio.Lock()
        self._collect_lock = asyncio.Lock()

    @property
    def ready(self):
        return self._context is not None

    @property
    def page(self):
        return self._page

    async def start(self):
        async with self._start_lock:
            if self.ready:
                return self._page
            from playwright.async_api import async_playwright

            profile = Path(os.environ.get('DATA_DIR', '/data')) / 'browser'
            profile.mkdir(parents=True, exist_ok=True)
            self._playwright = await async_playwright().start()
            kwargs = {
                'user_data_dir': str(profile),
                'headless': os.environ.get('HEADLESS', '0').lower() in {'1', 'true', 'yes'},
                'viewport': {'width': 1280, 'height': 900},
                'args': ['--no-sandbox', '--disable-dev-shm-usage'],
            }
            executable = os.environ.get('CHROMIUM_EXECUTABLE')
            if executable:
                kwargs['executable_path'] = executable
            try:
                self._context = await self._playwright.chromium.launch_persistent_context(**kwargs)
                self._context.on('close', self._context_closed)
                self._page = self._context.pages[-1] if self._context.pages else await self._context.new_page()
                if self._restored_state is not None:
                    await self._context.add_cookies(self._restored_state['cookies'])
                    # Only the initial user tab receives this script. Other tabs
                    # share origin storage and must not revive an old snapshot
                    # after the user clears storage or signs out.
                    await self._page.add_init_script(self._storage_restore_script(self._restored_state['origins']))
                    self._restored_state = None
                if self._page.url in {'about:blank', ''}:
                    await self._page.goto('https://www.instagram.com/', wait_until='domcontentloaded', timeout=60000)
                return self._page
            except Exception:
                await self.close()
                raise

    @staticmethod
    def _storage_restore_script(origins):
        # JSON is passed as data, never evaluated as user-provided JavaScript.
        values = json.dumps(origins, ensure_ascii=True, allow_nan=False)
        return r"""(() => {
          const origins = """ + values + r""";
          const entry = origins.find(value => value.origin === location.origin);
          if (!entry) return;
          try {
            const marker = '__organizer_restored_storage_v1';
            if (sessionStorage.getItem(marker) === '1') return;
            sessionStorage.setItem(marker, '1');
            for (const item of entry.localStorage || []) {
              // A current profile value takes precedence over the snapshot.
              if (localStorage.getItem(item.name) === null) localStorage.setItem(item.name, item.value);
            }
          } catch (_) {
            // Storage may be unavailable in opaque or browser-controlled frames.
          }
        })();"""

    async def snapshot(self):
        context = self._context
        if context is None:
            return None
        return await context.storage_state()

    def _context_closed(self, *_):
        self._context = None
        self._page = None

    @staticmethod
    def _check_auth_url(url):
        path = urlsplit(url).path.lower()
        if '/accounts/login' in path:
            raise ValueError('서버 브라우저 화면에서 직접 Instagram에 로그인한 뒤 다시 수집해주세요.')
        if any(part in path for part in ('/challenge', '/checkpoint', '/accounts/verify', '/accounts/suspended', '/accounts/disabled')):
            raise ValueError('Instagram 확인 또는 계정 제한 화면이 열려 있습니다. 브라우저에서 안내를 직접 처리한 뒤 다시 수집해주세요.')

    @staticmethod
    def _check_url(url):
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower()
        if parsed.scheme not in {'http', 'https'} or not (host == 'instagram.com' or host.endswith('.instagram.com')):
            raise ValueError('서버 브라우저에서 Instagram 탭을 열고 수집할 화면을 선택해주세요.')
        BrowserManager._check_auth_url(url)
        path = parsed.path.lower()
        if path.startswith('/direct') and not re.match(r'^/direct/t/[^/]+(?:/|$)', path):
            raise ValueError('DM 목록에서 수집할 대화를 먼저 열어주세요. 현재 열린 대화의 메시지와 공유 링크만 수집합니다.')
        if not (
            re.match(r'^/(?:[^/]+/)?saved(?:/|$)', path)
            or re.match(r'^/direct/t/[^/]+(?:/|$)', path)
            or re.match(r'^/(?:p|reel|tv)/[^/]+(?:/|$)', path)
        ):
            raise ValueError('저장함, DM 대화 또는 게시물 상세 화면을 열고 수집해주세요. 홈 피드와 일반 프로필은 수집하지 않습니다.')

    async def _active_page(self):
        if not self.ready:
            raise ValueError('먼저 서버 브라우저를 시작해주세요.')
        pages = []
        for page in self._context.pages:
            if page.is_closed():
                continue
            host = (urlsplit(page.url).hostname or '').lower()
            if host == 'instagram.com' or host.endswith('.instagram.com'):
                pages.append(page)
        if not pages:
            raise ValueError('서버 브라우저에서 Instagram 탭을 열고 수집할 화면을 선택해주세요.')
        focused = []
        for page in pages:
            try:
                if await page.evaluate('() => document.hasFocus()'):
                    focused.append(page)
            except Exception:
                continue
        self._page = (focused or pages)[-1]
        self._check_url(self._page.url)
        return self._page

    async def collect(self, mode='current', progress=None):
        if mode not in {'current', 'scroll'}:
            raise ValueError('수집 방식은 current 또는 scroll이어야 합니다.')

        async def notify(message):
            if progress:
                result = progress(message)
                if inspect.isawaitable(result):
                    await result

        async with self._collect_lock:
            page = await self._active_page()
            await notify('현재 열린 Instagram 화면의 로드된 게시물과 링크를 읽습니다. 전체 저장함이나 모든 DM을 가져오는 것은 아닙니다.')
            gathered = {}
            stalled_scrolls = 0
            initial_url = page.url
            for iteration in range(20 if mode == 'scroll' else 1):
                self._check_url(page.url)
                if page.url != initial_url:
                    await notify('수집 중 화면이 변경되어 여기까지 수집했습니다. 새 화면은 다시 수집해주세요.')
                    break
                # Login overlays can occur without a URL change; no credentials are read.
                login_visible = await page.locator('input[name="username"], input[name="password"]').evaluate_all(
                    '(els) => els.some(el => el.getClientRects().length && getComputedStyle(el).visibility !== "hidden")'
                )
                if login_visible:
                    raise ValueError('로그인 화면이 표시됩니다. 서버 브라우저에서 직접 로그인한 뒤 다시 수집해주세요.')
                items = await page.evaluate(EXTRACT_JS)
                for item in items:
                    existing = gathered.get(item['url'])
                    if not existing or len(item.get('text', '')) > len(existing.get('text', '')):
                        gathered[item['url']] = item
                await notify(f'현재 화면에서 {len(gathered)}개 항목을 수집했습니다.')
                if mode == 'current' or iteration == 19:
                    break
                scroll = await page.evaluate(SCROLL_JS)
                if scroll.get('reason') == 'no_conversation_scroller':
                    await notify('활성 대화의 스크롤 영역을 찾지 못했습니다. 대화를 열고 직접 스크롤한 뒤 현재 화면 수집을 사용해주세요.')
                    break
                # Empty/link-free messages can fill many screens; only a stalled
                # scroll position indicates an end, not unchanged extracted links.
                stalled_scrolls = 0 if scroll.get('moved') else stalled_scrolls + 1
                if stalled_scrolls >= 3:
                    break
                await asyncio.sleep(.7)
            return list(gathered.values())

    @staticmethod
    def _detail_url(url):
        """Only Instagram permalinks are eligible for detail navigation."""
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or '').lower()
            if parsed.scheme not in {'http', 'https'} or not (host == 'instagram.com' or host.endswith('.instagram.com')):
                return None
            match = re.match(r'^/(p|reel|tv)/([A-Za-z0-9_-]+)(?:/|$)', parsed.path)
            if match:
                return f'https://www.instagram.com/{match[1]}/{match[2]}/'
        except (TypeError, ValueError):
            pass
        return None

    @staticmethod
    def _caption_text(value):
        text = ' '.join(str(value or '').split())
        text = re.sub(r'^.*? on Instagram:\s*', '', text, flags=re.I).strip()
        if not text or text == '내용 확인이 필요한 게시물':
            return ''
        if re.search(
            r'^(?:Instagram\b|log in\b|sign up\b|create an account\b|see Instagram\b|join Instagram\b|photo by\b)'
            r'|Instagram photos and videos|Instagram 사진 및 동영상'
            r'|(?:sorry[, ]+)?this page (?:isn.t|is not) available|this content (?:isn.t|is not) available'
            r'|(?:이 )?페이지를 사용할 수 없|콘텐츠를 사용할 수 없|페이지를 찾을 수 없',
            text, re.I,
        ):
            return ''
        return text[:1200]

    async def enrich(self, records, progress=None):
        """Read captions in one temporary tab, retaining every original record."""
        copies = [dict(record) for record in records]
        targets = {}
        for index, record in enumerate(copies):
            url = self._detail_url(record.get('url', ''))
            if url:
                targets.setdefault(url, []).append(index)
        selected = list(targets.items())[:20]
        if not selected:
            return copies

        async def notify(message):
            if progress:
                result = progress(message)
                if inspect.isawaitable(result):
                    await result

        async with self._collect_lock:
            if not self.ready:
                raise ValueError('먼저 서버 브라우저를 시작하고 직접 Instagram에 로그인해주세요.')
            original_page = self._page
            detail_page = None
            filled = 0
            try:
                detail_page = await self._context.new_page()
                await notify(f'별도 탭에서 게시물 설명을 보완합니다. 한 번에 최대 20개이며, 이번에는 {len(selected)}개를 확인합니다.')
                for position, (url, indices) in enumerate(selected, 1):
                    for index in indices:
                        copies[index]['detail_checked'] = True
                    if position > 1:
                        await asyncio.sleep(1)
                    try:
                        await detail_page.goto(url, wait_until='domcontentloaded', timeout=25000)
                    except Exception:
                        self._check_auth_url(detail_page.url)
                        await notify(f'게시물 {position}/{len(selected)}을 열지 못해 원래 정보를 유지합니다.')
                        continue
                    self._check_auth_url(detail_page.url)
                    # Deleted/unavailable posts or unrelated redirects add no content.
                    if self._detail_url(detail_page.url) != url:
                        continue
                    try:
                        login_visible = await detail_page.locator('input[name="username"], input[name="password"]').evaluate_all(
                            '(els) => els.some(el => el.getClientRects().length && getComputedStyle(el).visibility !== "hidden")'
                        )
                        if login_visible:
                            raise ValueError('게시물 상세에서 로그인 화면이 표시됩니다. 서버 브라우저에서 직접 로그인한 뒤 다시 분류해주세요.')
                        # Prefer descriptions/headings over the full article, which
                        # can contain comments as well as the post caption.
                        metadata = await detail_page.evaluate(DETAIL_META_JS)
                        extracted = await detail_page.evaluate(EXTRACT_JS)
                    except ValueError:
                        raise
                    except Exception:
                        await notify(f'게시물 {position}/{len(selected)}의 설명을 읽지 못해 원래 정보를 유지합니다.')
                        continue
                    own = next((item for item in extracted if self._detail_url(item.get('url', '')) == url), {})
                    descriptions = [self._caption_text(value) for value in metadata.get('descriptions', [])]
                    headings = [self._caption_text(value) for value in metadata.get('headings', [])]
                    title = self._caption_text(metadata.get('title')) or self._caption_text(own.get('title'))
                    parts = [value for value in descriptions + headings + [title] if value]
                    if not parts:
                        fallback = self._caption_text(own.get('text'))
                        if fallback:
                            parts.append(fallback)
                    text = ' '.join(dict.fromkeys(parts))[:1200]
                    if text:
                        for index in indices:
                            record = copies[index]
                            # Source and source_url describe where the user saved or
                            # shared this item, not the temporary detail tab.
                            previous = ' '.join(str(record.get('text', '')).split())
                            from_dm = record.get('source') == 'dm' or 'dm' in record.get('sources', [])
                            context = previous[:800] if from_dm and previous and previous not in text else ''
                            record['text'] = (text + ('\n공유 문맥: ' + context if context else ''))[:2000]
                            record['title'] = (title or parts[0])[:140]
                        filled += 1
                    await notify(f'게시물 설명 {position}/{len(selected)}개 확인, {filled}개 보완했습니다.')
                if len(targets) > 20:
                    await notify('이번 설명 보완은 20개까지입니다. 나머지 항목은 이후 다시 분류해 설명을 확인해주세요.')
                return copies
            finally:
                if detail_page is not None:
                    try:
                        await detail_page.close()
                    except Exception:
                        pass
                if original_page is not None:
                    try:
                        if not original_page.is_closed():
                            await original_page.bring_to_front()
                    except Exception:
                        pass

    async def close(self):
        context, playwright = self._context, self._playwright
        self._context = None
        self._page = None
        self._playwright = None
        try:
            if context is not None:
                await context.close()
        finally:
            if playwright is not None:
                await playwright.stop()
