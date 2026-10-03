/* Explicit, bounded capture of the currently open post. Screenshots are cropped in the popup. */
(() => {
  "use strict";
  const MAX_IMAGES = 10, MAX_VIDEO_FRAMES = 4;
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  function postUrl(raw) {
    let url;
    try { url = new URL(raw); } catch (_) { throw new Error("게시물 상세 화면을 먼저 열어주세요."); }
    const match = url.pathname.match(/^\/(p|reel|tv)\/([A-Za-z0-9_-]+)\/?$/);
    if (url.protocol !== "https:" || !["www.instagram.com", "instagram.com"].includes(url.hostname) || url.username || url.password || !match) {
      throw new Error("사진·영상은 게시물 상세 화면에서 읽습니다. 저장함이나 DM의 게시물을 먼저 열어주세요.");
    }
    return `https://www.instagram.com/${match[1]}/${match[2]}/`;
  }
  const visible = element => Boolean(element && !element.closest('[hidden], [aria-hidden="true"]') &&
    element.getClientRects().length && getComputedStyle(element).display !== "none" && getComputedStyle(element).visibility !== "hidden");
  function clippedRect(element) {
    const box = element.getBoundingClientRect();
    let left = Math.max(0, box.left), top = Math.max(0, box.top), right = Math.min(innerWidth, box.right), bottom = Math.min(innerHeight, box.bottom);
    for (let ancestor = element.parentElement; ancestor && ancestor !== document.body; ancestor = ancestor.parentElement) {
      const style = getComputedStyle(ancestor), bounds = ancestor.getBoundingClientRect();
      if (/(hidden|clip|auto|scroll)/.test(style.overflowX || style.overflow || "")) { left = Math.max(left, bounds.left); right = Math.min(right, bounds.right); }
      if (/(hidden|clip|auto|scroll)/.test(style.overflowY || style.overflow || "")) { top = Math.max(top, bounds.top); bottom = Math.min(bottom, bounds.bottom); }
    }
    return {left, top, width: Math.max(0, right - left), height: Math.max(0, bottom - top)};
  }
  function mediaScope() {
    const dialogs = [...document.querySelectorAll('[role="dialog"]')].filter(visible);
    const mains = [...document.querySelectorAll('main, [role="main"]')].filter(visible);
    const scope = dialogs.at(-1) || mains.at(-1);
    if (!scope || [...document.querySelectorAll('input[name="username"], input[name="password"]')].some(visible)) {
      throw new Error("게시물 영역을 찾지 못했습니다. PC에서 로그인한 게시물 상세 화면을 열어주세요.");
    }
    if (dialogs.includes(scope) && !scope.querySelector('article, [role="article"]') && ![...scope.querySelectorAll("a[href]")].some(anchor => {
      try { return postUrl(new URL(anchor.getAttribute("href"), location.href).href) === postUrl(location.href); } catch (_) { return false; }
    })) throw new Error("현재 열린 창이 게시물인지 확인할 수 없습니다. 다른 안내 창을 닫고 게시물 상세 화면에서 다시 눌러주세요.");
    return scope;
  }
  function mainMedia(scope = mediaScope()) {
    // The primary post may be off screen while large recommendations are visible.
    // Keep capture bound to the opened post instead of choosing a later article.
    const articles = [...scope.querySelectorAll('article, [role="article"]')];
    const ownUrl = postUrl(location.href);
    const ownArticle = articles.find(article => [...article.querySelectorAll("a[href]")].some(anchor => {
      try { return postUrl(new URL(anchor.getAttribute("href"), location.href).href) === ownUrl; } catch (_) { return false; }
    }));
    const mediaRoot = ownArticle || articles[0] || scope;
    const candidates = [...mediaRoot.querySelectorAll("video, img")].filter(element => {
      if (!visible(element) || element.closest('nav, aside, header, [role="navigation"]')) return false;
      const box = element.getBoundingClientRect(), clip = clippedRect(element);
      if (box.width < 160 || box.height < 160 || clip.width < 120 || clip.height < 120) return false;
      if (element.tagName === "IMG" && (!element.complete || element.naturalWidth < 160 || element.naturalHeight < 160)) return false;
      return clip.width * clip.height >= box.width * box.height * .75;
    }).map(element => ({element, rect: clippedRect(element)})).sort((a, b) => b.rect.width * b.rect.height - a.rect.width * a.rect.height);
    const chosen = candidates[0];
    if (!chosen) throw new Error("게시물의 사진·영상이 화면에 충분히 보이지 않습니다. 사진을 화면 가운데 놓고 다시 눌러주세요.");
    return {...chosen, scope: mediaRoot};
  }
  function mediaIdentity(element) { return element.currentSrc || element.src || element.getAttribute("src") || ""; }
  function carouselControl(scope, rect, direction) {
    const labels = direction === "next" ? /^(?:next|next slide|다음|다음 슬라이드)$/i : /^(?:previous|previous slide|back|이전|이전 슬라이드)$/i;
    return [...scope.querySelectorAll('button, [role="button"]')].find(button => {
      if (!visible(button) || button.disabled || button.getAttribute("aria-disabled") === "true") return false;
      const label = button.getAttribute("aria-label") || button.getAttribute("title") || button.querySelector('[aria-label]')?.getAttribute("aria-label") || button.innerText;
      if (!labels.test(String(label || "").trim())) return false;
      const bounds = button.getBoundingClientRect(), x = bounds.left + bounds.width / 2, y = bounds.top + bounds.height / 2;
      return x >= rect.left - 36 && x <= rect.left + rect.width + 36 && y > rect.top + 12 && y < rect.top + rect.height - 12;
    });
  }
  function videoTimes(duration) {
    return Number.isFinite(duration) && duration > 0 ? [...new Set([0, .33, .66, .9].map(part => Math.round(duration * part * 1000) / 1000))].slice(0, MAX_VIDEO_FRAMES) : [];
  }
  async function seekVideo(video, time, cancelled) {
    if (Math.abs(video.currentTime - time) < .04) { await wait(180); return; }
    await new Promise((resolve, reject) => {
      let timer;
      const done = () => { clearTimeout(timer); video.removeEventListener("seeked", done); video.removeEventListener("error", failed); resolve(); };
      const failed = () => { clearTimeout(timer); video.removeEventListener("seeked", done); video.removeEventListener("error", failed); reject(new Error("영상 프레임을 읽지 못했습니다.")); };
      video.addEventListener("seeked", done, {once: true}); video.addEventListener("error", failed, {once: true});
      timer = setTimeout(failed, 1800);
      try { video.currentTime = time; } catch (_) { failed(); }
    });
    if (cancelled()) throw new Error("사진 읽기를 중단했습니다.");
    await wait(180);
  }
  async function collect(options) {
    const initialUrl = postUrl(location.href), first = mainMedia(), initialIdentity = mediaIdentity(first.element);
    const page = globalThis.WherewegoCollector?.validatePage(location.href);
    const base = page ? globalThis.WherewegoCollector.extract(page)[0] : {url: initialUrl, source: "post", source_url: initialUrl, title: "사진으로 읽은 게시물", text: ""};
    const media = [], fingerprints = new Set();
    let thumbnail = "", notice = "", moves = 0, captureAttempts = 0;
    const cancelled = () => options.cancelled() || postUrl(location.href) !== initialUrl;
    const snapshot = async (target, kind, time) => {
      if (cancelled()) throw new Error("사진 읽기를 중단했습니다.");
      const current = mainMedia();
      if (current.element !== target && mediaIdentity(current.element) !== mediaIdentity(target)) throw new Error("게시물 화면이 바뀌었습니다. 다시 눌러주세요.");
      if (captureAttempts >= MAX_IMAGES) return false;
      captureAttempts++;
      const result = await options.capture({rect: current.rect, viewport: {width: innerWidth, height: innerHeight}, post_url: initialUrl, media_id: mediaIdentity(current.element)});
      if (fingerprints.has(result.fingerprint)) return false;
      fingerprints.add(result.fingerprint);
      const frame = {data_url: result.data_url, kind, index: media.length + 1};
      if (kind === "video_frame") frame.time_seconds = Math.max(0, Math.round(time * 1000) / 1000);
      media.push(frame); if (!thumbnail) thumbnail = result.thumbnail;
      options.progress({count: media.length, kind});
      return true;
    };
    const captureVideo = async video => {
      const original = {time: video.currentTime, paused: video.paused, muted: video.muted};
      try {
        video.pause(); video.muted = true;
        const times = videoTimes(video.duration);
        if (!times.length || !video.seekable?.length) {
          await snapshot(video, "video_frame", video.currentTime || 0);
          notice += " 이 영상은 구간 이동이 어려워 현재 프레임만 가져왔습니다.";
        } else {
          for (const time of times) {
            if (cancelled() || captureAttempts >= MAX_IMAGES) break;
            await seekVideo(video, time, cancelled);
            await snapshot(video, "video_frame", time);
          }
          notice += " 영상에서 대표 프레임을 최대 4장 가져왔습니다. 음성·전체 영상 내용은 분석하지 않습니다.";
        }
      } finally {
        try { video.currentTime = original.time; } catch (_) {}
        video.muted = original.muted;
        if (!original.paused) await video.play().catch(() => { notice += " 영상 재생은 직접 다시 눌러주세요."; });
      }
    };
    {
      try {
        for (let index = 0; index < MAX_IMAGES; index++) {
          if (cancelled() || captureAttempts >= MAX_IMAGES) break;
          const current = mainMedia();
          if (current.element.tagName === "VIDEO") await captureVideo(current.element);
          else if (!await snapshot(current.element, "image")) { notice += " 같은 사진이 다시 나타나 여기까지 가져왔습니다."; break; }
          const next = carouselControl(current.scope, current.rect, "next");
          if (!next || index === MAX_IMAGES - 1 || captureAttempts >= MAX_IMAGES) break;
          const before = mediaIdentity(current.element); next.click();
          let changed = false;
          for (let attempt = 0; attempt < 8; attempt++) {
            await wait(180);
            if (cancelled()) break;
            try { if (mediaIdentity(mainMedia().element) !== before) { changed = true; break; } } catch (_) {}
          }
          if (!changed) { notice += " 다음 사진이 불러와지지 않아 여기까지 가져왔습니다. 현재 사진 위치를 직접 확인해주세요."; break; }
          moves++;
          await wait(220);
        }
      } finally {
        for (let index = 0; index < moves; index++) {
          try {
            if (postUrl(location.href) !== initialUrl) break;
            const current = mainMedia();
            if (mediaIdentity(current.element) === initialIdentity) break;
            const previous = carouselControl(current.scope, current.rect, "previous");
            if (!previous) { notice += " 시작 사진 위치를 복원하지 못했으니 직접 확인해주세요."; break; }
            const before = mediaIdentity(current.element); previous.click();
            let changed = false;
            for (let attempt = 0; attempt < 8; attempt++) {
              await wait(180);
              if (postUrl(location.href) !== initialUrl) break;
              try { if (mediaIdentity(mainMedia().element) !== before) { changed = true; break; } } catch (_) {}
            }
            if (!changed) { notice += " 시작 사진 위치를 복원하지 못했으니 직접 확인해주세요."; break; }
          } catch (_) { notice += " 사진 위치를 직접 확인해주세요."; break; }
        }
      }
      if (captureAttempts >= MAX_IMAGES) notice += " 한 번에 사진·프레임 합계 최대 10장까지 읽습니다. 남은 사진은 직접 넘긴 뒤 다시 읽어주세요.";
    }
    if (!media.length) throw new Error("읽을 사진·프레임을 가져오지 못했습니다. 게시물 상세 화면에서 다시 시도해주세요.");
    return {records: [{...base, media, thumbnail}], notice: notice.trim(), passes: 1};
  }
  globalThis.WherewegoMedia = {postUrl, clippedRect, mainMedia, carouselControl, videoTimes, collect, MAX_IMAGES, MAX_VIDEO_FRAMES};
  if (globalThis.chrome?.runtime?.onConnect && !globalThis.__wherewegoMediaListener) {
    globalThis.__wherewegoMediaListener = true;
    chrome.runtime.onConnect.addListener(port => {
      if (port.name !== "wherewego-media") return;
      let cancelled = false, started = false, sequence = 0;
      const pending = new Map();
      const send = value => { if (!cancelled) try { port.postMessage(value); } catch (_) { cancelled = true; } };
      port.onDisconnect.addListener(() => { cancelled = true; for (const request of pending.values()) { clearTimeout(request.timer); request.reject(new Error("사진 읽기를 중단했습니다.")); } pending.clear(); });
      port.onMessage.addListener(message => {
        if (message?.type === "capture_response") {
          const request = pending.get(message.id); if (!request) return;
          pending.delete(message.id); clearTimeout(request.timer);
          if (message.error) request.reject(new Error(message.error)); else request.resolve(message.result);
          return;
        }
        if (started || message?.mode !== "media") return;
        started = true;
        const capture = details => new Promise((resolve, reject) => {
          const id = ++sequence, timer = setTimeout(() => { pending.delete(id); reject(new Error("사진 캡처 시간이 초과되었습니다.")); }, 8000);
          pending.set(id, {resolve, reject, timer}); send({type: "capture_request", id, ...details});
        });
        collect({cancelled: () => cancelled, capture, progress: value => send({type: "progress", ...value})})
          .then(result => send({type: "result", ...result}))
          .catch(error => send({type: "error", message: error.message || "사진을 읽지 못했습니다."}));
      });
    });
  }
})();
