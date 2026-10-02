"use strict";

// Instagram content stays in this tab until the user confirms the import.
(() => {
  const storageKey = "wherewego.pendingImport.v1";
  const prefix = "#wherewego=";
  const maxBytes = 1536 * 1024;
  let memory = null;
  let captureError = "";
  let storageFailed = false;
  const byteLength = (value) => new TextEncoder().encode(value).length;

  function cleanHash() {
    if (window.location.hash.startsWith(prefix)) {
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }
  }

  function persist(raw) {
    memory = raw;
    try {
      window.sessionStorage.setItem(storageKey, raw);
      storageFailed = false;
      captureError = "";
      cleanHash();
      return true;
    } catch {
      storageFailed = true;
      return false;
    }
  }

  function capture() {
    if (!window.location.hash.startsWith(prefix)) return false;
    const fragment = window.location.hash;
    if (fragment.length > maxBytes + prefix.length) {
      captureError = "가져올 자료가 너무 큽니다. 확장에서 자료를 복사한 뒤 앱에 붙여넣어 주세요.";
      return false;
    }
    let raw;
    try {
      raw = decodeURIComponent(fragment.slice(prefix.length));
      if (byteLength(raw) > maxBytes) throw new Error("size");
    } catch {
      captureError = "가져올 자료를 읽지 못했습니다. 확장에서 다시 보내거나 복사해 붙여넣어 주세요.";
      return false;
    }
    captureError = "";
    if (!persist(raw)) return false;
    cleanHash();
    return true;
  }

  function read() {
    if (memory !== null) return memory;
    try { return window.sessionStorage.getItem(storageKey); }
    catch { storageFailed = true; return null; }
  }

  function clear() {
    memory = null;
    captureError = "";
    try { window.sessionStorage.removeItem(storageKey); } catch { /* Hash and memory are still cleared. */ }
    cleanHash();
  }

  window.WherewegoImportBridge = {
    capture, read, persist, clear,
    get error() { return captureError; },
    get storageFailed() { return storageFailed; },
    maxBytes,
  };

  const newlyStored = capture();
  // A same-origin navigation restores an existing Strict cookie after an
  // extension or Firebase redirect. Only a newly captured hash triggers it.
  if (newlyStored && window.location.pathname === "/login") {
    window.location.replace("/");
    return;
  }

  function loginNotice() {
    if (window.location.pathname !== "/login") return;
    const form = document.querySelector("form[action='/login']");
    if (!form) return;
    const raw = read();
    if (!raw && !captureError) return;
    const notice = document.createElement("p");
    notice.setAttribute("role", captureError || storageFailed ? "alert" : "status");
    notice.style.cssText = "font-size:14px;line-height:1.7;color:#52617b";
    if (captureError) notice.textContent = captureError;
    else if (storageFailed) {
      notice.textContent = "브라우저의 임시 저장공간을 사용할 수 없어 주소에 자료를 유지하고 있습니다. 로그인 후 가져올 내용을 확인해 주세요.";
      if (window.location.hash.startsWith(prefix)) form.setAttribute("action", "/login" + window.location.hash);
    } else notice.textContent = "PC에서 보낸 자료를 이 탭에 임시로 보관했습니다. 앱에 로그인하면 내용을 확인하고 가져올 수 있어요.";
    form.before(notice);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", loginNotice, { once: true });
  else loginNotice();
})();