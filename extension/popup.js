(() => {
  "use strict";
  const DEFAULT_APP = "https://ml-cherry-wherewego.web.app";
  const TARGETS = new Set([DEFAULT_APP, "https://instagram-organizer-yxpw24pk4a-du.a.run.app"]);
  const MAX_FRAGMENT = 1500000, MAX_JSON_BYTES = 6 * 1024 * 1024, MAX_FRAME_BYTES = 75 * 1024, MAX_MEDIA_BYTES = 750 * 1024, MAX_THUMBNAIL_BYTES = 24 * 1024, MAX_IMAGES = 10;
  function targetOrigin(value) {
    let url;
    try { url = new URL(String(value).trim()); } catch (_) { throw new Error("앱 주소를 확인해주세요."); }
    if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash ||
        !["", "/"].includes(url.pathname) || !TARGETS.has(url.origin)) throw new Error("앱 주소는 ml-cherry-wherewego.web.app 또는 배포된 Wherewego Cloud Run 주소를 사용해주세요.");
    return url.origin;
  }
  function transferUrl(target, records) {
    if (!Array.isArray(records) || !records.length || records.length > 500) throw new Error("전송할 항목은 1~500개여야 합니다.");
    const fragment = "#wherewego=" + encodeURIComponent(JSON.stringify({version: 1, records}));
    if (fragment.length > MAX_FRAGMENT) throw new Error("내용이 많아 주소로 전송할 수 없습니다. 사진이 많으면 JSON 복사 → 앱의 붙여넣기로 가져오거나 항목을 나누어 보내주세요. 목록은 이 창에 그대로 있습니다.");
    return targetOrigin(target) + "/" + fragment;
  }
  function postUrl(raw) {
    let url; try { url = new URL(raw); } catch (_) { return null; }
    const match = url.pathname.match(/^\/(p|reel|tv)\/([A-Za-z0-9_-]+)\/?$/);
    return url.protocol === "https:" && ["instagram.com", "www.instagram.com"].includes(url.hostname) && !url.username && !url.password && match ? `https://www.instagram.com/${match[1]}/${match[2]}/` : null;
  }
  function jpegBytes(value) {
    const prefix = "data:image/jpeg;base64,";
    if (typeof value !== "string" || !value.startsWith(prefix)) throw new Error("사진 형식을 확인해주세요.");
    const encoded = value.slice(prefix.length);
    if (!encoded || encoded.length % 4 || !/^[A-Za-z0-9+/]+={0,2}$/.test(encoded)) throw new Error("사진 형식을 확인해주세요.");
    return encoded.length / 4 * 3 - (encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0);
  }
  function cropBounds(rect, viewport, image) {
    const numbers = [rect?.left, rect?.top, rect?.width, rect?.height, viewport?.width, viewport?.height, image?.width, image?.height];
    if (!numbers.every(Number.isFinite) || rect.width < 120 || rect.height < 120 || viewport.width < 160 || viewport.height < 160 ||
        image.width < 160 || image.height < 160 || image.width > 32768 || image.height > 32768 || rect.left < 0 || rect.top < 0 ||
        rect.left + rect.width > viewport.width + 1 || rect.top + rect.height > viewport.height + 1) throw new Error("사진 영역을 확인할 수 없습니다. 게시물 사진을 화면 가운데 놓고 다시 시도해주세요.");
    const scaleX = image.width / viewport.width, scaleY = image.height / viewport.height;
    if (Math.abs(scaleX - scaleY) > Math.max(scaleX, scaleY) * .08) throw new Error("화면 크기가 바뀌었습니다. 창 크기를 유지하고 다시 눌러주세요.");
    const x = Math.max(0, Math.ceil(rect.left * scaleX)), y = Math.max(0, Math.ceil(rect.top * scaleY));
    return {x, y, width: Math.min(image.width - x, Math.floor(rect.width * scaleX)), height: Math.min(image.height - y, Math.floor(rect.height * scaleY))};
  }
  function frameFingerprint(pixels) {
    let hash = 2166136261;
    for (let index = 0; index < pixels.length; index += 4) { const gray = Math.round((pixels[index] * 3 + pixels[index + 1] * 6 + pixels[index + 2]) / 10); hash = Math.imul(hash ^ (gray >> 3), 16777619); }
    return (hash >>> 0).toString(16);
  }
  function sameCaptureState(before, after) {
    return before?.post_url === after?.post_url && before?.media_id === after?.media_id &&
      before?.viewport?.width === after?.viewport?.width && before?.viewport?.height === after?.viewport?.height &&
      ["left", "top", "width", "height"].every(key => Number.isFinite(before?.rect?.[key]) && Number.isFinite(after?.rect?.[key]) && Math.abs(before.rect[key] - after.rect[key]) <= 1) &&
      (before.video_time === null && after.video_time === null || Number.isFinite(before.video_time) && Number.isFinite(after.video_time) && Math.abs(before.video_time - after.video_time) <= .2);
  }
  function mergeRecords(existing, incoming) {
    const map = new Map(existing.map(item => [item.url, item]));
    for (const item of incoming) {
      const old = map.get(item.url);
      if (!old) { if (map.size < 500) map.set(item.url, item); continue; }
      const richer = (item.text || "").length > (old.text || "").length ? item : old;
      const media = [], known = new Set(); let bytes = 0;
      for (const frame of [...(old.media || []), ...(item.media || [])]) {
        if (media.length >= MAX_IMAGES || known.has(frame.data_url)) continue;
        let size; try { size = jpegBytes(frame.data_url); } catch (_) { continue; }
        if (size > MAX_FRAME_BYTES || bytes + size > MAX_MEDIA_BYTES) continue;
        known.add(frame.data_url); bytes += size; media.push({...frame, index: media.length + 1});
      }
      const merged = {...old, ...richer, source: old.source === "post" ? item.source : old.source, source_url: old.source === "post" ? item.source_url : old.source_url};
      if (media.length) { merged.media = media; merged.thumbnail = old.thumbnail || item.thumbnail; }
      map.set(item.url, merged);
    }
    return [...map.values()];
  }
  globalThis.WherewegoPopup = {targetOrigin, transferUrl, postUrl, jpegBytes, cropBounds, frameFingerprint, sameCaptureState, mergeRecords, jpegFromCanvas, cropBitmap, MAX_FRAGMENT, MAX_JSON_BYTES, MAX_FRAME_BYTES, MAX_MEDIA_BYTES, MAX_THUMBNAIL_BYTES, MAX_IMAGES};
  if (typeof document === "undefined") return;
  const $ = id => document.getElementById(id);
  let records = [], busy = false, port = null, lastScreenshotAt = 0;
  const status = (text, error = false) => { $("status").textContent = text; $("status").classList.toggle("error", error); };
  function render() {
    $("count").textContent = records.length + "개";
    $("review").replaceChildren();
    for (let index = 0; index < records.length; index++) {
      const item = records[index], row = document.createElement("li"), title = document.createElement("strong"), url = document.createElement("span"), source = document.createElement("small");
      title.textContent = item.title || "내용 확인이 필요한 게시물"; url.textContent = item.url;
      source.textContent = (({saved: "저장함", dm: "DM", post: "게시물"})[item.source] || "게시물") + (item.text ? " · 설명 포함" : " · URL만 있음") + (item.media?.length ? ` · 사진·프레임 ${item.media.length}장` : "");
      row.append(title, url, source);
      if (item.media?.length) {
        const previews = document.createElement("div"); previews.className = "media-previews";
        for (const frame of item.media) { const image = document.createElement("img"); image.src = frame.data_url; image.alt = frame.kind === "video_frame" ? `영상 ${frame.time_seconds || 0}초 프레임` : `사진 ${frame.index}`; previews.append(image); }
        row.append(previews);
      }
      if (item.text) {
        const description = document.createElement("details"), summary = document.createElement("summary"), paragraph = document.createElement("p");
        summary.textContent = "설명 보기"; paragraph.textContent = item.text; description.append(summary, paragraph); row.append(description);
      }
      const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "삭제"; remove.className = "remove"; remove.disabled = busy;
      remove.setAttribute("aria-label", (item.title || "항목") + " 삭제"); remove.addEventListener("click", () => { records.splice(index, 1); render(); }); row.append(remove); $("review").append(row);
    }
    for (const id of ["captureCurrent", "captureScroll", "captureDm", "captureMedia", "appUrl"]) $(id).disabled = busy;
    for (const id of ["send", "copy", "clear"]) $(id).disabled = busy || !records.length;
  }
  async function assertActive(tab, expectedUrl) {
    const [active] = await chrome.tabs.query({active: true, windowId: tab.windowId});
    if (active?.id !== tab.id || postUrl(active.url) !== expectedUrl) throw new Error("활성 탭이나 게시물이 바뀌었습니다. Instagram 게시물로 돌아와 다시 눌러주세요.");
  }
  async function jpegFromCanvas(canvas, limit) {
    let working = canvas;
    for (let attempt = 0; attempt < 12; attempt++) {
      const quality = [.82, .68, .5, .36, .22][Math.min(attempt, 4)], blob = await working.convertToBlob({type: "image/jpeg", quality});
      if (blob.size <= limit) {
        const bytes = new Uint8Array(await blob.arrayBuffer()); let binary = "";
        for (let start = 0; start < bytes.length; start += 8192) binary += String.fromCharCode(...bytes.subarray(start, start + 8192));
        return "data:image/jpeg;base64," + btoa(binary);
      }
      if (attempt >= 4) { const smaller = new OffscreenCanvas(Math.max(96, Math.round(working.width * .8)), Math.max(96, Math.round(working.height * .8))); smaller.getContext("2d").drawImage(working, 0, 0, smaller.width, smaller.height); working = smaller; }
    }
    throw new Error("사진 용량을 줄이지 못했습니다. 다른 사진에서 다시 시도해주세요.");
  }
  async function captureCropped(tab, request) {
    const expectedUrl = postUrl(request.post_url);
    if (!expectedUrl) throw new Error("게시물 주소를 확인할 수 없습니다.");
    // Chrome permits at most two visible-tab captures per second.
    const delay = Math.max(0, lastScreenshotAt + 600 - Date.now());
    if (delay) await new Promise(resolve => setTimeout(resolve, delay));
    await assertActive(tab, expectedUrl);
    const inspect = async () => {
      const [result] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: () => {
        const api = globalThis.WherewegoMedia;
        if (!api) throw new Error("사진 읽기 연결이 끊겼습니다.");
        const media = api.mainMedia();
        return {post_url: api.postUrl(location.href), media_id: media.element.currentSrc || media.element.src || media.element.getAttribute("src") || "", rect: media.rect,
          viewport: {width: innerWidth, height: innerHeight}, video_time: media.element.tagName === "VIDEO" ? media.element.currentTime : null};
      }});
      return result?.result;
    };
    const before = await inspect();
    if (before?.post_url !== expectedUrl || before.media_id !== request.media_id || before.viewport.width !== request.viewport.width || before.viewport.height !== request.viewport.height) {
      throw new Error("게시물 사진이나 화면 크기가 바뀌었습니다. 다시 눌러주세요.");
    }
    lastScreenshotAt = Date.now();
    const screenshot = await chrome.tabs.captureVisibleTab(tab.windowId, {format: "png"});
    await assertActive(tab, expectedUrl);
    if (!sameCaptureState(before, await inspect())) throw new Error("사진 영역이 움직여 캡처를 중단했습니다. 스크롤·탭 이동을 멈추고 다시 눌러주세요.");
    const bitmap = await createImageBitmap(await (await fetch(screenshot)).blob());
    try { return await cropBitmap(bitmap, before.rect, before.viewport); }
    finally { bitmap.close(); }
  }
  async function cropBitmap(bitmap, rect, viewport) {
      const bounds = cropBounds(rect, viewport, bitmap), scale = Math.min(1, 1280 / Math.max(bounds.width, bounds.height));
      const canvas = new OffscreenCanvas(Math.max(1, Math.round(bounds.width * scale)), Math.max(1, Math.round(bounds.height * scale)));
      canvas.getContext("2d").drawImage(bitmap, bounds.x, bounds.y, bounds.width, bounds.height, 0, 0, canvas.width, canvas.height);
      const small = new OffscreenCanvas(128, 128); small.getContext("2d").drawImage(canvas, canvas.width * .01, canvas.height * .01, canvas.width * .98, canvas.height * .98, 0, 0, 128, 128);
      const fingerprint = frameFingerprint(small.getContext("2d").getImageData(0, 0, 128, 128).data);
      const thumbScale = Math.min(1, 320 / Math.max(canvas.width, canvas.height));
      const thumbnail = new OffscreenCanvas(Math.max(1, Math.round(canvas.width * thumbScale)), Math.max(1, Math.round(canvas.height * thumbScale))); thumbnail.getContext("2d").drawImage(canvas, 0, 0, thumbnail.width, thumbnail.height);
      return {data_url: await jpegFromCanvas(canvas, MAX_FRAME_BYTES), thumbnail: await jpegFromCanvas(thumbnail, MAX_THUMBNAIL_BYTES), fingerprint};
  }
  async function capture(mode) {
    busy = true; render();
    status(({scroll: "최대 20번 스크롤하며 가져옵니다. 이 창을 열어두세요.", resolve: "열린 대화의 공유 게시물을 최대 20개 확인합니다. 다른 대화는 열지 않습니다.", media: "현재 게시물의 사진·슬라이드·대표 영상 프레임을 읽습니다. 이 창을 열어두세요. 창을 닫으면 중단됩니다."})[mode] || "현재 화면에서 불러온 항목을 읽고 있습니다.");
    try {
      const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
      if (!tab?.id || !tab.url) throw new Error("Instagram 탭에서 다시 열어주세요.");
      const url = new URL(tab.url);
      if (url.protocol !== "https:" || !["instagram.com", "www.instagram.com"].includes(url.hostname)) throw new Error("PC 브라우저의 Instagram 저장함·DM 대화·게시물 탭에서 수집해주세요.");
      if (mode === "media" && !postUrl(tab.url)) throw new Error("사진·영상은 게시물 상세 화면에서 읽습니다. 저장함이나 DM의 게시물을 먼저 열어주세요.");
      await chrome.scripting.executeScript({target: {tabId: tab.id}, files: mode === "media" ? ["content.js", "media.js"] : ["content.js"]});
      await new Promise((resolve, reject) => {
        let finished = false, screenshotCount = 0;
        port = chrome.tabs.connect(tab.id, {name: mode === "media" ? "wherewego-media" : "wherewego-capture"});
        const thisPort = port;
        thisPort.onMessage.addListener(message => {
          if (message.type === "capture_request") {
            if (mode !== "media" || ++screenshotCount > 10) { thisPort.postMessage({type: "capture_response", id: message.id, error: "한 번에 최대 10장까지 읽을 수 있습니다."}); return; }
            captureCropped(tab, message).then(result => { if (!finished) thisPort.postMessage({type: "capture_response", id: message.id, result}); }).catch(error => { if (!finished) try { thisPort.postMessage({type: "capture_response", id: message.id, error: error.message || "사진을 캡처하지 못했습니다."}); } catch (_) {} });
          } else if (message.type === "progress") {
            status(mode === "media" ? `사진·프레임 ${message.count}장 읽는 중… 이 창을 열어두세요.` : `화면 ${message.passes || 1}번 · ${message.count}개 확인 중… 이 창을 열어두세요.`);
          } else if (message.type === "result") {
            finished = true; records = mergeRecords(records, message.records || []);
            status(records.length + "개를 준비했습니다. 사진과 목록을 확인한 후 앱으로 보내세요." + (message.notice ? " " + message.notice : ""));
            thisPort.disconnect(); port = null; resolve();
          } else if (message.type === "error") {
            finished = true; thisPort.disconnect(); port = null; reject(new Error(message.message));
          }
        });
        thisPort.onDisconnect.addListener(() => { void chrome.runtime.lastError; if (!finished) { finished = true; reject(new Error("탭과 연결이 끊겼습니다. Instagram 화면에서 다시 수집해주세요.")); } });
        thisPort.postMessage({mode});
      });
    } catch (error) { status(error.message || "화면을 읽지 못했습니다. Instagram 탭에서 다시 시도해주세요.", true); }
    finally { busy = false; render(); }
  }
  $("captureCurrent").addEventListener("click", () => capture("current"));
  $("captureScroll").addEventListener("click", () => capture("scroll"));
  $("captureDm").addEventListener("click", () => capture("resolve"));
  $("captureMedia").addEventListener("click", () => capture("media"));
  $("clear").addEventListener("click", () => { records = []; render(); status("목록을 비웠습니다."); });
  $("send").addEventListener("click", async () => {
    try { const url = transferUrl($("appUrl").value, records); await chrome.storage.local.set({wherewegoApp: targetOrigin($("appUrl").value)}); await chrome.tabs.create({url}); status("앱을 열었습니다. 앱에서 사진·목록을 확인한 뒤 가져오기·분류를 눌러주세요."); }
    catch (error) { status(error.message || "앱을 열지 못했습니다.", true); }
  });
  $("copy").addEventListener("click", async () => {
    try { const payload = JSON.stringify({version: 1, records}); if (new Blob([payload]).size > MAX_JSON_BYTES) throw new Error("사진이 많아 붙여넣기 한도 6MiB를 넘었습니다. 일부 항목을 삭제해 나누어 보내주세요. 목록은 이 창에 그대로 있습니다."); await navigator.clipboard.writeText(payload); status("JSON을 복사했습니다. 앱의 PC에서 가져오기 → 붙여넣기에 넣어주세요. 사진·공유 링크·설명이 포함됩니다."); }
    catch (error) { status(error.message || "클립보드 복사가 되지 않았습니다. 이 창을 다시 열어 앱으로 보내기를 사용해주세요.", true); }
  });
  $("appUrl").addEventListener("change", async () => { try { const origin = targetOrigin($("appUrl").value); await chrome.storage.local.set({wherewegoApp: origin}); $("appUrl").value = origin; status("앱 주소를 저장했습니다."); } catch (error) { $("appUrl").value = DEFAULT_APP; status(error.message, true); } });
  window.addEventListener("unload", () => { if (port) port.disconnect(); });
  chrome.storage.local.get("wherewegoApp").then(saved => { try { $("appUrl").value = targetOrigin(saved.wherewegoApp || DEFAULT_APP); } catch (_) { $("appUrl").value = DEFAULT_APP; } }).catch(() => { $("appUrl").value = DEFAULT_APP; });
  render();
})();
