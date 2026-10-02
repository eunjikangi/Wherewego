(() => {
  "use strict";
  const DEFAULT_APP = "https://ml-cherry-wherewego.web.app";
  const TARGETS = new Set(["https://ml-cherry-wherewego.web.app", "https://instagram-organizer-yxpw24pk4a-du.a.run.app"]);
  const MAX_FRAGMENT = 1500000;
  function targetOrigin(value) {
    let url;
    try { url = new URL(String(value).trim()); } catch (_) { throw new Error("앱 주소를 확인해주세요."); }
    if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash ||
        !["", "/"].includes(url.pathname) || !TARGETS.has(url.origin)) {
      throw new Error("앱 주소는 ml-cherry-wherewego.web.app 또는 배포된 Wherewego Cloud Run 주소를 사용해주세요.");
    }
    return url.origin;
  }
  function transferUrl(target, records) {
    if (!Array.isArray(records) || !records.length || records.length > 500) throw new Error("전송할 항목은 1~500개여야 합니다.");
    const fragment = "#wherewego=" + encodeURIComponent(JSON.stringify({version: 1, records}));
    if (fragment.length > MAX_FRAGMENT) throw new Error("내용이 많아 주소로 전송할 수 없습니다. 항목을 일부 삭제해서 나누어 보내거나 JSON 복사 후 앱의 붙여넣기를 사용해주세요.");
    return targetOrigin(target) + "/" + fragment;
  }
  globalThis.WherewegoPopup = {targetOrigin, transferUrl, MAX_FRAGMENT};
  if (typeof document === "undefined") return;
  const $ = id => document.getElementById(id);
  let records = [], busy = false, port = null;
  const status = (text, isError = false) => { $("status").textContent = text; $("status").classList.toggle("error", isError); };
  function merge(next) {
    const map = new Map(records.map(item => [item.url, item]));
    for (const item of next) {
      const old = map.get(item.url);
      if (old && (item.text || "").length > (old.text || "").length) {
        map.set(item.url, {...item, source: old.source === "post" ? item.source : old.source, source_url: old.source === "post" ? item.source_url : old.source_url});
      } else if (!old && map.size < 500) map.set(item.url, item);
    }
    records = [...map.values()];
  }
  function render() {
    $("count").textContent = records.length + "개";
    $("review").replaceChildren();
    for (let index = 0; index < records.length; index++) {
      const item = records[index];
      const row = document.createElement("li");
      const title = document.createElement("strong");
      title.textContent = item.title || "내용 확인이 필요한 게시물";
      const url = document.createElement("span");
      url.textContent = item.url;
      const source = document.createElement("small");
      source.textContent = ({saved: "저장함", dm: "DM", post: "게시물"})[item.source] + (item.text ? " · 설명 포함" : " · URL만 있음");
      let description;
      if (item.text) {
        description = document.createElement("details");
        const summary = document.createElement("summary"); summary.textContent = "설명 보기";
        const paragraph = document.createElement("p"); paragraph.textContent = item.text;
        description.append(summary, paragraph);
      }
      const remove = document.createElement("button");
      remove.type = "button"; remove.textContent = "삭제"; remove.className = "remove";
      remove.setAttribute("aria-label", (item.title || "항목") + " 삭제");
      remove.disabled = busy;
      remove.addEventListener("click", () => { records.splice(index, 1); render(); });
      row.append(title, url, source);
      if (description) row.append(description);
      row.append(remove);
      $("review").append(row);
    }
    for (const id of ["captureCurrent", "captureScroll", "appUrl"]) $(id).disabled = busy;
    for (const id of ["send", "copy", "clear"]) $(id).disabled = busy || records.length === 0;
  }
  async function capture(mode) {
    busy = true; render();
    status(mode === "scroll" ? "최대 20번 스크롤하며 가져옵니다. 완료까지 이 창을 열어두세요. 창을 닫으면 중단됩니다." : "현재 화면에서 불러온 항목을 읽고 있습니다.");
    try {
      const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
      if (!tab?.id || !tab.url) throw new Error("활성 탭을 확인할 수 없습니다. Instagram 탭에서 다시 열어주세요.");
      const url = new URL(tab.url);
      if (url.protocol !== "https:" || !["instagram.com", "www.instagram.com"].includes(url.hostname)) {
        throw new Error("PC 브라우저의 Instagram 저장함·DM 대화·게시물 탭에서 수집해주세요.");
      }
      await chrome.scripting.executeScript({target: {tabId: tab.id}, files: ["content.js"]});
      await new Promise((resolve, reject) => {
        let finished = false;
        port = chrome.tabs.connect(tab.id, {name: "wherewego-capture"});
        port.onMessage.addListener(message => {
          if (message.type === "progress") {
            status("화면 " + message.passes + "번 · " + message.count + "개 확인 중… 이 창을 열어두세요.");
          } else if (message.type === "result") {
            finished = true; merge(message.records);
            status(records.length + "개를 준비했습니다. 아래 목록을 확인한 후 앱으로 보내세요." + (message.notice ? " " + message.notice : ""));
            port.disconnect(); port = null; resolve();
          } else if (message.type === "error") {
            finished = true; port.disconnect(); port = null;
            reject(new Error(message.message));
          }
        });
        port.onDisconnect.addListener(() => {
          if (!finished) reject(new Error("탭과 연결이 끊겼습니다. Instagram 화면을 확인하고 다시 수집해주세요."));
          void chrome.runtime.lastError;
        });
        port.postMessage({mode});
      });
    } catch (error) {
      status(error.message || "화면을 읽지 못했습니다. Instagram 탭에서 다시 시도해주세요.", true);
    } finally { busy = false; render(); }
  }
  $("captureCurrent").addEventListener("click", () => capture("current"));
  $("captureScroll").addEventListener("click", () => capture("scroll"));
  $("clear").addEventListener("click", () => { records = []; render(); status("목록을 비웠습니다."); });
  $("send").addEventListener("click", async () => {
    try {
      const url = transferUrl($("appUrl").value, records);
      await chrome.storage.local.set({wherewegoApp: targetOrigin($("appUrl").value)});
      await chrome.tabs.create({url});
      status("앱을 열었습니다. 앱에서 목록과 AI 사용 여부를 확인한 뒤 가져오기를 눌러주세요.");
    } catch (error) { status(error.message || "앱을 열지 못했습니다.", true); }
  });
  $("copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify({version: 1, records}));
      status("JSON을 복사했습니다. 앱의 PC에서 가져오기 → 붙여넣기에 넣어주세요. 공유 링크와 메시지 설명이 들어 있습니다.");
    } catch (_) { status("클립보드 복사가 되지 않았습니다. 이 창을 다시 열어 앱으로 보내기를 사용해주세요.", true); }
  });
  $("appUrl").addEventListener("change", async () => {
    try {
      const origin = targetOrigin($("appUrl").value);
      await chrome.storage.local.set({wherewegoApp: origin});
      $("appUrl").value = origin;
      status("앱 주소를 저장했습니다.");
    } catch (error) { $("appUrl").value = DEFAULT_APP; status(error.message, true); }
  });
  window.addEventListener("unload", () => { if (port) port.disconnect(); });
  chrome.storage.local.get("wherewegoApp").then(saved => {
    try { $("appUrl").value = targetOrigin(saved.wherewegoApp || DEFAULT_APP); }
    catch (_) { $("appUrl").value = DEFAULT_APP; }
  }).catch(() => { $("appUrl").value = DEFAULT_APP; });
  render();
})();
