/* User-directed DOM capture. No cookies, credentials, network, or background jobs. */
(() => {
  "use strict";
  const LIMIT = 500;
  const MAX_PASSES = 20;
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
  function scopeFor(source) {
    const dialogs = [...document.querySelectorAll('[role="dialog"]')].filter(visible);
    const mains = [...document.querySelectorAll('main, [role="main"]')].filter(visible);
    const scope = dialogs.at(-1) || mains.at(-1);
    if (!scope) throw new Error("게시물 영역을 찾지 못했습니다. 화면을 새로고침하고 다시 수집해주세요.");
    if (source !== "dm") return scope;
    const outsideInbox = el => !el.closest('nav, aside, header, [role="navigation"]');
    const logs = [...scope.querySelectorAll('[role="log"]')].filter(el => visible(el) && outsideInbox(el));
    if (logs.length) return logs.at(-1);
    const regions = [...scope.querySelectorAll("[aria-label]")].filter(el =>
      visible(el) && outsideInbox(el) && el.clientHeight > 100 && el.querySelector("a[href]") && !el.querySelector('nav, aside, [role="navigation"]') &&
      /^(?:messages|message list|conversation|chat messages|메시지|대화)(?:\s|$)/i.test(el.getAttribute("aria-label") || "") &&
      el.getBoundingClientRect().left > innerWidth * .22);
    if (regions.length) return regions.at(-1);
    const scrollRegions = [...scope.querySelectorAll("*")].filter(el => {
      if (!visible(el) || !outsideInbox(el) || el.querySelector('nav, aside, [role="navigation"]')) return false;
      const rect = el.getBoundingClientRect();
      return /(auto|scroll)/.test(getComputedStyle(el).overflowY) &&
        el.clientHeight > 150 && el.scrollHeight > el.clientHeight + 30 &&
        rect.left > innerWidth * .22 && rect.right > innerWidth * .65;
    }).sort((a, b) => b.clientHeight - a.clientHeight);
    if (scrollRegions.length) return scrollRegions[0];
    throw new Error("열린 DM 대화의 메시지 영역을 찾지 못했습니다. 대화를 새로 열거나 공유 게시물을 직접 열어 현재 화면 수집을 사용해주세요.");
  }
  function description(anchor, scope, page) {
    let card = anchor.closest('article, [role="listitem"], [role="row"], li');
    if (!card || !scope.contains(card)) {
      card = anchor;
      for (let depth = 0; depth < 5 && card.parentElement && card.parentElement !== scope; depth++) {
        const parent = card.parentElement;
        if (parent.matches('nav, aside, header, [role="navigation"]') || clean(parent.innerText).length > 1200) break;
        const urls = [...parent.querySelectorAll("a[href]")].map(a => normalizeLink(a.getAttribute("href"), page.sourceUrl, page.source)).filter(Boolean);
        if (new Set(urls.map(link => link.url)).size > 1) break;
        card = parent;
      }
    }
    // A broad row/article can contain several messages. Never borrow their text.
    const cardLinks = [...card.querySelectorAll("a[href]")].map(a => normalizeLink(a.getAttribute("href"), page.sourceUrl, page.source)).filter(Boolean);
    if (new Set(cardLinks.map(link => link.url)).size > 1) card = anchor;
    const texts = [meaningful(card.innerText)];
    for (const img of card.querySelectorAll("img[alt]")) if (visible(img)) texts.push(meaningful(img.alt));
    const label = meaningful(anchor.getAttribute("aria-label"));
    if (label && !/^(?:open|view|go to|열기|보기)\b/i.test(label)) texts.push(label);
    return [...new Set(texts.filter(Boolean))].join(" ").slice(0, 1200);
  }
  function mergeRecords(existing, incoming) {
    const records = new Map();
    for (const item of [...existing, ...incoming]) {
      if (!item || typeof item.url !== "string") continue;
      const old = records.get(item.url);
      if (old) {
        if ((item.text || "").length > (old.text || "").length) {
          records.set(item.url, {...item, source: old.source === "post" ? item.source : old.source, source_url: old.source === "post" ? item.source_url : old.source_url});
        }
      } else if (records.size < LIMIT) records.set(item.url, {...item});
    }
    return [...records.values()];
  }
  function extract(page) {
    const scope = scopeFor(page.source);
    if (page.source === "post") {
      const own = normalizeLink(page.sourceUrl, page.sourceUrl, page.source);
      const meta = meaningful(document.querySelector('meta[property="og:title"]')?.content || "")
        .replace(/^.*? on Instagram:\s*/i, "").replace(/^Instagram\s*[-:|]\s*/i, "");
      const headings = [...scope.querySelectorAll("h1")].filter(visible).map(el => meaningful(el.innerText)).filter(Boolean);
      const article = [...scope.querySelectorAll("article")].find(visible);
      const caption = article ? meaningful(article.innerText) : headings.join(" ");
      const text = [...new Set([caption, meta].filter(Boolean))].join(" ").slice(0, 1200);
      return [{url: own.url, title: (meta || headings[0] || text || "내용 확인이 필요한 게시물").slice(0, 140), text, source: page.source, source_url: page.sourceUrl}];
    }
    const records = [];
    for (const anchor of scope.querySelectorAll("a[href]")) {
      if (!visible(anchor) || anchor.closest('nav, aside, header, [role="navigation"]')) continue;
      const link = normalizeLink(anchor.getAttribute("href"), page.sourceUrl, page.source);
      if (!link) continue;
      const text = description(anchor, scope, page);
      const title = text ? text.slice(0, 140) : link.external ? new URL(link.url).hostname : "내용 확인이 필요한 게시물";
      records.push({url: link.url, title, text, source: page.source, source_url: page.sourceUrl});
      if (records.length >= LIMIT) break;
    }
    return mergeRecords([], records);
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
    if (!["current", "scroll"].includes(mode)) throw new Error("수집 방식을 확인해주세요.");
    const initial = validatePage(location.href);
    let records = [], stalled = 0, notice = "", passes = 0;
    for (let index = 0; index < (mode === "scroll" && initial.source !== "post" ? MAX_PASSES : 1); index++) {
      if (options.cancelled?.()) throw new Error("수집을 중단했습니다.");
      const page = validatePage(location.href);
      if (page.sourceUrl !== initial.sourceUrl) { notice = "수집 중 화면이 바뀌어 여기까지 가져왔습니다. 새 화면은 다시 수집해주세요."; break; }
      if (blockedAuth()) throw new Error("로그인 화면이 표시됩니다. PC 브라우저에서 직접 로그인하고 다시 수집해주세요.");
      records = mergeRecords(records, extract(page));
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
    return {records, notice, passes};
  }
  globalThis.WherewegoCollector = {validatePage, normalizeLink, mergeRecords, collect, extract, scopeFor, LIMIT, MAX_PASSES};
  if (globalThis.chrome?.runtime?.onConnect && !globalThis.__wherewegoCaptureListener) {
    globalThis.__wherewegoCaptureListener = true;
    chrome.runtime.onConnect.addListener(port => {
      if (port.name !== "wherewego-capture") return;
      let cancelled = false, started = false;
      port.onDisconnect.addListener(() => { cancelled = true; });
      port.onMessage.addListener(message => {
        if (started || !["current", "scroll"].includes(message?.mode)) return;
        started = true;
        const send = value => { if (!cancelled) { try { port.postMessage(value); } catch (_) { cancelled = true; } } };
        collect(message.mode, {cancelled: () => cancelled, progress: value => send({type: "progress", ...value})})
          .then(result => send({type: "result", ...result}))
          .catch(error => send({type: "error", message: error.message || "화면을 수집하지 못했습니다."}));
      });
    });
  }
})();
