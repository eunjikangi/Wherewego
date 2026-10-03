/* User-directed DOM capture. No cookies, credentials, network, or background jobs. */
(() => {
  "use strict";
  const LIMIT = 500;
  const MAX_PASSES = 20;
  const MAX_SHARED_OPENINGS = 20;
  const clean = value => String(value || "").replace(/\s+/g, " ").trim();
  const visible = el => Boolean(el && !el.closest('[hidden], [aria-hidden="true"]') &&
    el.getClientRects().length && getComputedStyle(el).display !== "none" &&
    getComputedStyle(el).visibility !== "hidden");
  function validatePage(raw) {
    let url;
    try { url = new URL(raw); } catch (_) { throw new Error("PC 브라우저에서 Instagram을 열어주세요."); }
    if (url.protocol !== "https:" || !["instagram.com", "www.instagram.com"].includes(url.hostname) || url.username || url.password) {
      throw new Error("현재 탭이 Instagram이 아닙니다. PC 브라우저의 Instagram 탭에서 사용해주세요.");
    }
    const path = url.pathname;
    if (/^\/(?:accounts\/(?:login|verify|suspended|disabled)|challenge|checkpoint)(?:\/|$)/i.test(path)) {
      throw new Error("Instagram 로그인 또는 보안 확인 화면입니다. 평소 사용하는 PC 브라우저에서 직접 확인을 마친 뒤 사용해주세요.");
    }
    const source = /^\/direct\/t\/[^/]+(?:\/|$)/.test(path) ? "dm" :
      /^\/(?:[^/]+\/)?saved(?:\/|$)/.test(path) ? "saved" :
      /^\/(?:p|reel|tv)\/[A-Za-z0-9_-]+(?:\/|$)/.test(path) ? "post" : "";
    if (!source) throw new Error("저장함, 열려 있는 DM 대화 또는 게시물 상세 화면에서 사용해주세요. 홈 피드와 DM 목록은 수집하지 않습니다.");
    url.search = "";
    url.hash = "";
    return {source, sourceUrl: url.href};
  }
  function normalizeLink(raw, sourceUrl, source) {
    try {
      let url = new URL(raw, sourceUrl);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return null;
      if (url.hostname === "l.instagram.com") {
        const target = url.searchParams.get("u");
        if (!target) return null;
        url = new URL(target);
        if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return null;
      }
      if (url.hostname === "instagram.com" || url.hostname.endsWith(".instagram.com")) {
        const match = url.pathname.match(/^\/(p|reel|tv)\/([A-Za-z0-9_-]+)(?:\/|$)/);
        return match ? {url: `https://www.instagram.com/${match[1]}/${match[2]}/`, external: false} : null;
      }
      if (source !== "dm") return null;
      url.hash = "";
      return {url: url.href, external: true};
    } catch (_) { return null; }
  }
  function meaningful(value) {
    const text = clean(value);
    return !text || /^(?:photo|image|video) by\b/i.test(text) ||
      /^(?:Instagram|photo|image|video|사진|이미지|view post|view reel|open post|게시물 보기|릴스 보기)$/i.test(text) ? "" : text;
  }
  function blockedAuth() {
    return [...document.querySelectorAll('input[name="username"], input[name="password"]')].some(visible);
  }
  const excludedArea = el => Boolean(el.closest('nav, aside, header, [role="navigation"]'));
  const inboxRegion = el => {
    for (let parent = el; parent && parent !== document.body; parent = parent.parentElement) {
      if (/^(?:inbox|chats|chat list|conversation list|direct inbox|대화 목록|채팅 목록)(?:\s|$)/i.test(parent.getAttribute("aria-label") || "")) return true;
    }
    return false;
  };
  const outsideInbox = el => !excludedArea(el) && !inboxRegion(el);
  function scopeFor(source) {
    const dialogs = [...document.querySelectorAll('[role="dialog"]')].filter(visible);
    const mains = [...document.querySelectorAll('main, [role="main"]')].filter(visible);
    const scope = dialogs.at(-1) || mains.at(-1);
    if (source !== "dm") {
      if (!scope) throw new Error("게시물 영역을 찾지 못했습니다. 화면을 새로고침하고 다시 수집해주세요.");
      return scope;
    }
    // The DM conversation is not always a descendant of <main>, especially in
    // Instagram's narrow desktop layout. Semantic logs are safe on their own.
    const logs = [...document.querySelectorAll('[role="log"]')].filter(el => visible(el) && outsideInbox(el) && !el.querySelector('a[href^="/direct/t/"]'));
    if (logs.length) return logs.at(-1);
    const root = scope || document.body;
    const regions = [...document.querySelectorAll("[aria-label]")].filter(el =>
      visible(el) && outsideInbox(el) && el.clientHeight > 100 && !el.querySelector('nav, aside, [role="navigation"], a[href^="/direct/t/"]') &&
      /^(?:messages|message list|conversation|chat messages|메시지|대화)(?:\s|$)/i.test(el.getAttribute("aria-label") || ""));
    if (regions.length) return regions.at(-1);
    const composers = [...document.querySelectorAll('[contenteditable="true"], textarea')].filter(el => visible(el) && outsideInbox(el));
    const scrollRegions = [...root.querySelectorAll("*")].filter(el => {
      if (!visible(el) || !outsideInbox(el) || el.querySelector('nav, aside, [role="navigation"]')) return false;
      const rect = el.getBoundingClientRect();
      // A horizontal position alone also admits inbox lists. Require the open
      // conversation's composer directly below the candidate scrolling pane.
      const aboveComposer = composers.some(composer => {
        const input = composer.getBoundingClientRect();
        const center = (input.left + input.right) / 2;
        return center >= rect.left && center <= rect.right && input.top >= rect.top && input.top >= rect.bottom - 80;
      });
      if (!aboveComposer || el.querySelector('a[href^="/direct/t/"]')) return false;
      return /(auto|scroll)/.test(getComputedStyle(el).overflowY) &&
        el.clientHeight > 150 && el.scrollHeight > el.clientHeight + 30;
    }).sort((a, b) => b.clientHeight - a.clientHeight);
    if (scrollRegions.length) return scrollRegions[0];
    throw new Error("열린 DM 대화의 메시지 영역을 찾지 못했습니다. 대화를 새로 열거나 공유 게시물을 직접 열어 현재 화면 수집을 사용해주세요.");
  }
  const LINK_ATTRIBUTES = ["href", "data-href", "data-url", "data-permalink", "data-post-url"];
  function linksFor(element, page, textLinks = false) {
    const links = [];
    for (const name of LINK_ATTRIBUTES) {
      let raw = element.getAttribute(name);
      if (raw) {
        if (/^(?:https?%3a%2f%2f|%2f(?:p|reel|tv)%2f)/i.test(raw)) {
          try { raw = decodeURIComponent(raw); } catch (_) { continue; }
        }
        if (/^(?:www\.)?instagram\.com\/(?:p|reel|tv)\//i.test(raw)) raw = "https://" + raw;
        const link = normalizeLink(raw, page.sourceUrl, page.source);
        if (link) links.push(link);
      }
    }
    if (textLinks) {
      // Read literal, visible permalinks. Never infer a shortcode from an opaque
      // message ID or inspect React state, scripts, or private network responses.
      const text = element.innerText || "";
      for (const raw of text.match(/https?:\/\/(?:www\.)?instagram\.com\/(?:p|reel|tv)\/[A-Za-z0-9_-]+\/?/gi) || []) {
        const link = normalizeLink(raw, page.sourceUrl, page.source);
        if (link) links.push(link);
      }
    }
    return [...new Map(links.map(link => [link.url, link])).values()];
  }
  function cardLinks(card, page) {
    return [card, ...card.querySelectorAll('a[href], [role="link"], [data-href], [data-url], [data-permalink], [data-post-url]')]
      .flatMap(el => linksFor(el, page, page.source === "dm"));
  }
  function description(anchor, scope, page) {
    let card = anchor.closest('article, [role="listitem"], [role="row"], li');
    if (!card || !scope.contains(card)) {
      card = anchor;
      for (let depth = 0; depth < 5 && card.parentElement && card.parentElement !== scope; depth++) {
        const parent = card.parentElement;
        if (parent.matches('nav, aside, header, [role="navigation"]') || clean(parent.innerText).length > 1200) break;
        const urls = cardLinks(parent, page);
        if (new Set(urls.map(link => link.url)).size > 1) break;
        card = parent;
      }
    }
    // A broad row/article can contain several messages. Never borrow their text.
    const urls = cardLinks(card, page);
    if (new Set(urls.map(link => link.url)).size > 1) card = anchor;
    if (new Set(cardLinks(card, page).map(link => link.url)).size > 1) return meaningful(anchor.getAttribute("aria-label"));
    const texts = [meaningful(card.innerText)];
    for (const img of card.querySelectorAll("img[alt]")) if (visible(img)) texts.push(meaningful(img.alt));
    const label = meaningful(anchor.getAttribute("aria-label"));
    if (label && !/^(?:open|view|go to|열기|보기)\b/i.test(label)) texts.push(label);
    return [...new Set(texts.filter(Boolean))].join(" ").slice(0, 1200);
  }
  function sharedCards(scope, page) {
    const candidates = [...scope.querySelectorAll('[role="button"], button')].filter(el => {
      if (!visible(el) || !outsideInbox(el) || cardLinks(el, page).length) return false;
      const label = clean([el.getAttribute("aria-label"), el.getAttribute("title"), el.innerText].filter(Boolean).join(" "));
      if (/\b(?:delete|unsend|remove|report|block|mute|call|profile|like|react|reply|forward|download)\b|삭제|전송 취소|신고|차단|음소거|통화|프로필|좋아요|답장|전달|다운로드/i.test(label)) return false;
      const previews = [...el.querySelectorAll("img, video")].filter(visible);
      if (!previews.length) return false;
      const clearlyShared = /\b(?:shared (?:post|reel)|(?:open|view) (?:post|reel)|reel|post preview)\b|공유(?:한|된)?\s*(?:게시물|릴스)|게시물 보기|릴스 보기/i.test(label);
      const largePreview = previews.some(media => {
        const rect = media.getBoundingClientRect();
        return rect.right - rect.left >= 120 && rect.bottom - rect.top >= 100;
      });
      // Small sender avatars, message controls, and arbitrary rows cannot be
      // clicked. Large preview cards may open a photo attachment; only a real
      // post permalink in the opened view will become a record.
      return (clearlyShared || largePreview) && clean(el.innerText).length <= 1200 && !el.querySelector('input, textarea, [contenteditable="true"]');
    });
    // The outer card and nested preview can both be buttons. Open only the
    // innermost eligible preview, never the whole message or conversation row.
    return candidates.filter(el => !candidates.some(child => child !== el && el.contains(child)));
  }
  function mergeRecords(existing, incoming) {
    const records = new Map();
    for (const item of [...existing, ...incoming]) {
      if (!item || typeof item.url !== "string") continue;
      const old = records.get(item.url);
      if (old) {
        if ((item.text || "").length > (old.text || "").length) {
          records.set(item.url, {...old, ...item, source: old.source === "post" ? item.source : old.source, source_url: old.source === "post" ? item.source_url : old.source_url});
        } else if (Array.isArray(item.media) && item.media.length) records.set(item.url, {...old, media: item.media, thumbnail: item.thumbnail || old.thumbnail});
      } else if (records.size < LIMIT) records.set(item.url, {...item});
    }
    return [...records.values()];
  }
  function extractWithDiagnostics(page) {
    const scope = scopeFor(page.source);
    if (page.source === "post") {
      const own = normalizeLink(page.sourceUrl, page.sourceUrl, page.source);
      const meta = meaningful(document.querySelector('meta[property="og:title"]')?.content || "")
        .replace(/^.*? on Instagram:\s*/i, "").replace(/^Instagram\s*[-:|]\s*/i, "");
      const headings = [...scope.querySelectorAll("h1")].filter(el => visible(el) && !el.closest('[role="log"]')).map(el => meaningful(el.innerText)).filter(Boolean);
      const article = [...scope.querySelectorAll('article, [role="article"]')].find(el => visible(el) && !el.closest('[role="log"]'));
      const caption = article ? meaningful(article.innerText) : headings.join(" ");
      const text = [...new Set([caption, meta].filter(Boolean))].join(" ").slice(0, 1200);
      return {records: [{url: own.url, title: (meta || headings[0] || text || "내용 확인이 필요한 게시물").slice(0, 140), text, source: page.source, source_url: page.sourceUrl}], unresolved_cards: 0};
    }
    const records = [];
    const selector = page.source === "dm" ? 'a[href], [role="link"], [data-href], [data-url], [data-permalink], [data-post-url], [role="listitem"], [role="row"], [role="button"]' : "a[href]";
    for (const anchor of scope.querySelectorAll(selector)) {
      if (!visible(anchor) || !outsideInbox(anchor)) continue;
      const links = linksFor(anchor, page, page.source === "dm");
      if (!links.length) continue;
      const text = description(anchor, scope, page);
      for (const link of links) {
        // A broad message wrapper must not give one link the description of a
        // different shared post in the same row.
        const ownText = links.length > 1 ? "" : text;
        const title = ownText ? ownText.slice(0, 140) : link.external ? new URL(link.url).hostname : "내용 확인이 필요한 게시물";
        records.push({url: link.url, title, text: ownText, source: page.source, source_url: page.sourceUrl});
      }
      if (records.length >= LIMIT) break;
    }
    if (page.source === "dm" && document.createTreeWalker && globalThis.NodeFilter) {
      // Plain message text sometimes contains a permalink without any anchor.
      // Walk only rendered text nodes inside this conversation, never attributes
      // containing hidden message data or neighbouring conversations.
      const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode()) && records.length < LIMIT) {
        const parent = node.parentElement;
        if (!parent || !visible(parent) || !outsideInbox(parent) || parent.closest('script, style, input, textarea, [contenteditable="true"]')) continue;
        const links = linksFor({getAttribute: () => null, innerText: node.textContent}, page, true);
        if (links.length !== 1) continue;
        const text = description(parent, scope, page);
        records.push({url: links[0].url, title: (text || "내용 확인이 필요한 게시물").slice(0, 140), text, source: page.source, source_url: page.sourceUrl});
      }
    }
    return {records: mergeRecords([], records), unresolved_cards: page.source === "dm" ? sharedCards(scope, page).length : 0};
  }
  function extract(page) { return extractWithDiagnostics(page).records; }
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  function openedPost(originalDialogs, page) {
    const dialogs = [...document.querySelectorAll('[role="dialog"]')].filter(el => visible(el) && !originalDialogs.includes(el));
    let current;
    try { current = validatePage(location.href); } catch (_) { return null; }
    if (current.source === "post") {
      let scope;
      try { scope = scopeFor("post"); } catch (_) { return {record: null, dialog: dialogs.at(-1), navigation: true}; }
      // Route updates can happen before the post's DOM replaces the DM. Wait
      // for the actual post so a conversation never becomes its caption.
      const article = [...scope.querySelectorAll('article, [role="article"]')].find(el => visible(el) && !el.closest('[role="log"]'));
      const heading = [...scope.querySelectorAll("h1")].find(el => visible(el) && !el.closest('[role="log"]'));
      if (!article && !heading) return {record: null, dialog: dialogs.at(-1), navigation: true};
      return {record: {...extract(current)[0], source: "dm", source_url: page.sourceUrl}, dialog: dialogs.at(-1), navigation: true};
    }
    const dialog = dialogs.at(-1);
    if (!dialog) return null;
    const article = [...dialog.querySelectorAll('article, [role="article"]')].find(visible) || dialog;
    const anchors = [...article.querySelectorAll("a[href]")].filter(el => visible(el) && !excludedArea(el));
    const timed = anchors.filter(anchor => anchor.querySelector("time"));
    const directLinks = anchors.flatMap(anchor => linksFor(anchor, {...page, source: "post"})).filter(link => !link.external);
    const timedLinks = timed.flatMap(anchor => linksFor(anchor, {...page, source: "post"})).filter(link => !link.external);
    const unique = [...new Map((timedLinks.length ? timedLinks : directLinks).map(link => [link.url, link])).values()];
    if (unique.length !== 1) return {dialog, record: null};
    const texts = [meaningful(article.innerText), ...[...article.querySelectorAll("img[alt]")].filter(visible).map(image => meaningful(image.alt))];
    const text = [...new Set(texts.filter(Boolean))].join(" ").slice(0, 1200);
    return {record: {url: unique[0].url, title: (text || "내용 확인이 필요한 게시물").slice(0, 140), text, source: "dm", source_url: page.sourceUrl}, dialog};
  }
  async function restoreConversation(opened, page) {
    if (opened.dialog) {
      const buttons = [...opened.dialog.querySelectorAll('button, [role="button"]')].filter(visible);
      const close = buttons.find(button => /^(?:close|닫기)$/i.test(clean(button.getAttribute("aria-label") || button.innerText))) ||
        buttons.find(button => [...button.querySelectorAll('[aria-label]')].some(child => /^(?:close|닫기)$/i.test(clean(child.getAttribute("aria-label")))));
      if (close) {
        close.click();
        for (let attempt = 0; attempt < 10 && visible(opened.dialog); attempt++) await wait(100);
      }
      if (visible(opened.dialog)) return false;
    }
    if (location.href.split(/[?#]/)[0] !== page.sourceUrl) {
      // A post can replace the conversation route rather than opening a modal.
      // Restore only this one normal user navigation, never manipulate a router.
      if (!opened.navigation || !globalThis.history?.back) return false;
      history.back();
      for (let attempt = 0; attempt < 30; attempt++) {
        await wait(100);
        if (location.href.split(/[?#]/)[0] === page.sourceUrl) return true;
      }
      return false;
    }
    return true;
  }
  async function resolveSharedCards(page, options) {
    const cards = sharedCards(scopeFor("dm"), page).slice(0, MAX_SHARED_OPENINGS);
    const records = [];
    let resolved = 0, notice = "";
    for (let index = 0; index < cards.length; index++) {
      if (options.cancelled?.()) throw new Error("수집을 중단했습니다.");
      if (!visible(cards[index])) continue;
      const originalDialogs = [...document.querySelectorAll('[role="dialog"]')].filter(visible);
      cards[index].click();
      let opened;
      try {
        for (let attempt = 0; attempt < 30; attempt++) {
          await wait(100);
          opened = openedPost(originalDialogs, page);
          if (options.cancelled?.()) throw new Error("수집을 중단했습니다.");
          if (opened?.record) break;
        }
      } finally {
        // Closing the popup cancels collection. Leave the conversation restored
        // when a verified Close button or our one post navigation is available.
        if (options.cancelled?.() && opened) await restoreConversation(opened, page);
      }
      if (opened?.record) { records.push(opened.record); resolved++; }
      options.progress?.({count: records.length, passes: 1, checked_cards: index + 1});
      if (opened && !await restoreConversation(opened, page)) {
        notice = "열린 게시물의 링크까지 가져왔습니다. 게시물을 직접 닫고 DM에서 다시 수집해주세요.";
        break;
      }
      if (location.href.split(/[?#]/)[0] !== page.sourceUrl) {
        notice = "공유 게시물이 새 화면으로 열렸습니다. 그 게시물 화면에서 현재 화면 수집을 눌러주세요.";
        break;
      }
    }
    return {records, resolved, checked: cards.length, notice};
  }
  function scroll(page) {
    const scope = scopeFor(page.source);
    const candidates = [scope, ...scope.querySelectorAll("*")].filter(el => {
      if (!visible(el) || el.closest('nav, aside, header, [role="navigation"]')) return false;
      return /(auto|scroll)/.test(getComputedStyle(el).overflowY) && el.scrollHeight > el.clientHeight + 30 && el.clientHeight > 150;
    }).sort((a, b) => b.clientHeight - a.clientHeight);
    const target = candidates[0];
    if (page.source === "dm" && !target) return {moved: false, reason: "대화의 스크롤 영역을 찾지 못했습니다. 직접 스크롤하고 현재 화면 수집을 사용해주세요."};
    if (target && target !== document.body && target !== document.documentElement) {
      const before = target.scrollTop;
      target.scrollBy({top: (page.source === "dm" ? -1 : 1) * Math.max(400, target.clientHeight * .75), behavior: "instant"});
      return {moved: before !== target.scrollTop};
    }
    const before = scrollY;
    window.scrollBy({top: Math.max(500, innerHeight * .8), behavior: "instant"});
    return {moved: before !== scrollY};
  }
  async function collect(mode, options = {}) {
    if (!["current", "scroll", "resolve"].includes(mode)) throw new Error("수집 방식을 확인해주세요.");
    const initial = validatePage(location.href);
    if ((mode === "resolve" || options.resolve_shared) && initial.source !== "dm") throw new Error("공유 게시물 찾기는 열린 DM 대화에서 사용해주세요.");
    let records = [], stalled = 0, notice = "", passes = 0, unresolved = 0;
    for (let index = 0; index < (mode === "scroll" && initial.source !== "post" ? MAX_PASSES : 1); index++) {
      if (options.cancelled?.()) throw new Error("수집을 중단했습니다.");
      const page = validatePage(location.href);
      if (page.sourceUrl !== initial.sourceUrl) { notice = "수집 중 화면이 바뀌어 여기까지 가져왔습니다. 새 화면은 다시 수집해주세요."; break; }
      if (blockedAuth()) throw new Error("로그인 화면이 표시됩니다. PC 브라우저에서 직접 로그인하고 다시 수집해주세요.");
      const extracted = extractWithDiagnostics(page);
      records = mergeRecords(records, extracted.records);
      unresolved = Math.max(unresolved, extracted.unresolved_cards);
      passes++;
      options.progress?.({count: records.length, passes});
      if (records.length >= LIMIT) { notice = "최대 500개까지 가져왔습니다. 먼저 앱에 보낸 다음 나머지 화면을 다시 수집해주세요."; break; }
      if (mode !== "scroll" || initial.source === "post" || index === MAX_PASSES - 1) break;
      const movement = scroll(page);
      if (movement.reason) { notice = movement.reason; break; }
      stalled = movement.moved ? 0 : stalled + 1;
      if (stalled >= 3) break;
      await new Promise(resolve => setTimeout(resolve, 750));
    }
    if ((mode === "resolve" || options.resolve_shared) && initial.source === "dm") {
      const result = await resolveSharedCards(initial, options);
      records = mergeRecords(records, result.records);
      unresolved = Math.max(0, unresolved - result.resolved);
      if (result.notice) notice = result.notice;
      else if (result.checked >= MAX_SHARED_OPENINGS) notice = "공유 카드 최대 20개를 열어 확인했습니다. 나머지는 직접 스크롤해서 다시 수집해주세요.";
    }
    if (initial.source === "dm" && !notice) {
      if (unresolved) notice = "주소가 화면에 없는 공유 카드 " + unresolved + "개가 있습니다. ‘DM 공유 게시물 찾기’를 누르거나 공유 카드를 직접 열어 현재 화면 수집을 사용해주세요.";
      else if (!records.length) notice = "현재 DM 화면에서 게시물 주소를 찾지 못했습니다. 공유 게시물 카드가 보이도록 스크롤한 다음 ‘DM 공유 게시물 찾기’를 사용해주세요. 새 탭으로 열리는 카드는 그 탭에서 현재 화면 수집을 눌러주세요.";
    }
    return {records, notice, passes, unresolved_cards: unresolved};
  }
  globalThis.WherewegoCollector = {validatePage, normalizeLink, mergeRecords, collect, extract, extractWithDiagnostics, scopeFor, sharedCards, LIMIT, MAX_PASSES, MAX_SHARED_OPENINGS};
  if (globalThis.chrome?.runtime?.onConnect && !globalThis.__wherewegoCaptureListener) {
    globalThis.__wherewegoCaptureListener = true;
    chrome.runtime.onConnect.addListener(port => {
      if (port.name !== "wherewego-capture") return;
      let cancelled = false, started = false;
      port.onDisconnect.addListener(() => { cancelled = true; });
      port.onMessage.addListener(message => {
        if (started || !["current", "scroll", "resolve"].includes(message?.mode)) return;
        started = true;
        const send = value => { if (!cancelled) { try { port.postMessage(value); } catch (_) { cancelled = true; } } };
        globalThis.WherewegoCollector.collect(message.mode, {cancelled: () => cancelled, progress: value => send({type: "progress", ...value})})
          .then(result => send({type: "result", ...result}))
          .catch(error => send({type: "error", message: error.message || "화면을 수집하지 못했습니다."}));
      });
    });
  }
})();
