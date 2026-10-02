"use strict";

(() => {
  const state = {
    status: null,
    items: [],
    source: "",
    category: "",
    query: "",
    browserOpen: false,
    browserStarting: false,
    collectionStarting: false,
    classifyStarting: false,
    importStarting: false,
    importAwaitingCompletion: false,
    pendingImport: null,
    pendingImportRaw: null,
    configSaving: false,
    aiPreference: null,
    aiConfig: null,
    aiEditorProvider: "gemini",
    aiEditorModels: {},
    requestId: 0,
    statusRequestId: 0,
    pollTimer: null,
    searchTimer: null,
    toastTimer: null,
    loadFailed: false,
    statusFailed: false,
  };
  const byId = (id) => document.getElementById(id);
  const sourceNames = { saved: "저장함", dm: "DM 공유", post: "게시물", current: "현재 화면", unknown: "현재 화면" };
  const classificationNames = { rules: "키워드 분류", ai: "AI 분류", manual: "직접 분류", pending: "분류 보류" };
  const defaultCategories = ["맛집", "카페", "여행", "쇼핑·패션", "뷰티", "집·인테리어", "운동·건강", "공부·업무", "문화·취미", "분류 보류"];
  const aiProviders = { gemini: { name: "Gemini", model: "gemini-flash-latest" }, openai: { name: "OpenAI", model: "gpt-4.1-mini" } };
  const browserUrl = "/browser/vnc.html?autoconnect=true&resize=scale&path=browser/websockify";
  const importBridge = window.WherewegoImportBridge;
  const maxImportBytes = 1536 * 1024;
  const instagramHosts = new Set(["instagram.com", "www.instagram.com", "m.instagram.com"]);

  function node(tag, className, content) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (content !== undefined) element.textContent = content;
    return element;
  }

  function toast(message, isError = false) {
    const element = byId("toast");
    clearTimeout(state.toastTimer);
    element.textContent = message;
    element.classList.toggle("error", isError);
    element.hidden = false;
    state.toastTimer = setTimeout(() => { element.hidden = true; }, isError ? 7000 : 4500);
  }

  async function request(path, options = {}) {
    const headers = { ...options.headers };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(path, { credentials: "same-origin", ...options, headers });
    if (response.status === 401) {
      if (state.pendingImportRaw) importBridge?.persist(state.pendingImportRaw);
      let fallbackHash = "";
      if (importBridge?.storageFailed && state.pendingImportRaw) {
        const encoded = encodeURIComponent(state.pendingImportRaw);
        if (encoded.length <= maxImportBytes) fallbackHash = "#wherewego=" + encoded;
        else throw new Error("앱 로그인이 만료되었습니다. 이 탭을 유지하고 새 탭에서 앱에 로그인한 뒤 다시 가져와 주세요.");
      } else if (importBridge?.storageFailed && window.location.hash.startsWith("#wherewego=")) fallbackHash = window.location.hash;
      window.location.assign("/login" + fallbackHash);
      throw new Error("앱에 다시 로그인해 주세요.");
    }
    let body;
    try { body = await response.json(); } catch { body = {}; }
    if (!response.ok) {
      const detail = typeof body.detail === "string" ? body.detail : typeof body.message === "string" ? body.message : `요청을 처리하지 못했습니다. (${response.status})`;
      throw new Error(detail);
    }
    return body;
  }

  function setConnection(mode, label) {
    byId("connection-status").className = `status-pill ${mode}`;
    byId("connection-label").textContent = label;
  }

  function isBusy() {
    return Boolean(state.status?.busy || state.collectionStarting || state.classifyStarting || state.browserStarting || state.importStarting || state.importAwaitingCompletion);
  }

  function updateControls() {
    const ready = Boolean(state.status?.browser_ready);
    const busy = isBusy();
    byId("collect-current").disabled = !ready || busy;
    byId("collect-scroll").disabled = !ready || busy;
    byId("browser-button").disabled = state.browserStarting;
    byId("empty-action").disabled = false;
    byId("import-confirm").disabled = busy || !state.pendingImport;
    byId("import-confirm").textContent = state.importStarting || state.importAwaitingCompletion ? "저장·분류 중" : "가져오기·분류";
    byId("import-discard").disabled = state.importStarting || state.importAwaitingCompletion;
    byId("import-discard").hidden = !state.pendingImportRaw && !byId("import-paste-input").value && !importBridge?.error;
    byId("import-paste-preview").disabled = state.importStarting || state.importAwaitingCompletion;
    byId("import-paste-input").disabled = state.importStarting || state.importAwaitingCompletion;
    byId("use-ai").disabled = busy;
    byId("ai-control").hidden = !state.status?.ai_available;
    byId("ai-setup-cta").hidden = Boolean(state.status?.ai_available);
    byId("reclassify-button").disabled = !state.status?.ai_available || busy || !(state.status?.total > 0);
    byId("use-ai").checked = Boolean(state.status?.ai_available && state.aiPreference !== false);
    const label = state.browserStarting ? "브라우저 준비 중" : state.browserOpen ? "서버 브라우저 접기" : "서버 브라우저 열기";
    byId("browser-button").replaceChildren(document.createTextNode(label), node("span", "", state.browserOpen ? "↑" : "↗"));
    byId("browser-button").lastChild.setAttribute("aria-hidden", "true");
    byId("browser-button").setAttribute("aria-expanded", String(state.browserOpen));
    if (state.statusFailed) setConnection("offline", "연결 확인 필요");
    else if (busy) setConnection("busy", state.browserStarting ? "브라우저 준비 중" : "작업 중");
    else if (state.status) setConnection("ready", "서버 연결됨");
    byId("browser-panel").hidden = !state.browserOpen;
    if (ready && state.browserOpen) mountBrowser();
    if (busy) byId("collection-hint").textContent = "작업이 끝나면 수집한 자료가 아래에 표시됩니다.";
    else byId("collection-hint").textContent = byId("use-ai").checked ? "서버 화면을 수집하고 게시물 설명을 추가로 읽습니다. 캡챠가 반복되면 PC 수집을 사용하세요." : "서버 브라우저에서 열어 둔 저장함이나 DM 화면을 수집합니다.";
    byId("sidebar-total").textContent = String(state.status?.total ?? state.items.length);
  }

  function mountBrowser() {
    const host = byId("browser-frame-host");
    byId("browser-loading").hidden = true;
    if (host.firstChild) return;
    const frame = node("iframe");
    frame.src = browserUrl;
    frame.title = "인스타그램에 직접 로그인하는 서버 브라우저";
    frame.setAttribute("allow", "clipboard-read; clipboard-write; fullscreen");
    frame.allowFullscreen = true;
    host.append(frame);
  }

  function renderJob() {
    const job = state.status?.job;
    const panel = byId("job-progress");
    const busy = isBusy();
    const failed = ["error", "failed"].includes(job?.status);
    const completed = ["completed", "done", "success"].includes(job?.status);
    panel.hidden = !busy && !failed && !completed;
    panel.className = `job-progress${failed ? " failed" : completed && !busy ? " completed" : ""}`;
    if (state.importStarting) byId("job-message").textContent = "확인한 자료를 가져와 분류합니다.";
    else if (state.browserStarting) byId("job-message").textContent = "서버 브라우저를 준비하고 있습니다.";
    else if (state.collectionStarting) byId("job-message").textContent = "현재 열어 둔 화면에서 자료를 수집합니다.";
    else if (state.classifyStarting) byId("job-message").textContent = "저장한 자료를 AI로 다시 분류하고 있습니다.";
    else {
      let message = job?.message || (busy ? "자료를 수집하고 분류하고 있습니다." : failed ? "작업을 완료하지 못했습니다." : "수집이 완료되었습니다.");
      if (completed && !job?.message && (job?.added !== undefined || job?.updated !== undefined)) message += ` · 새 자료 ${job.added ?? 0}개 · 갱신 ${job.updated ?? 0}개`;
      byId("job-message").textContent = message;
    }
  }

  function renderCategories() {
    const categories = Array.from(new Set([...(state.status?.categories || []), ...state.items.map((item) => item.category || "분류 보류")]));
    if (state.category && !categories.includes(state.category)) categories.push(state.category);
    categories.sort((a, b) => a.localeCompare(b, "ko"));
    const filters = byId("category-filters");
    filters.replaceChildren();
    if (!categories.length) filters.append(node("p", "category-empty", "자료를 모으면 카테고리가\n여기에 표시됩니다."));
    for (const category of categories) {
      const button = node("button", `category-button${state.category === category ? " active" : ""}`, category);
      button.type = "button";
      button.setAttribute("aria-pressed", String(state.category === category));
      button.addEventListener("click", () => {
        state.category = state.category === category ? "" : category;
        renderCategories();
        loadItems();
      });
      filters.append(button);
    }
    byId("clear-category").hidden = !state.category;
  }

  function safeUrl(value) {
    try {
      const url = new URL(value);
      return ["https:", "http:"].includes(url.protocol) ? url : null;
    } catch { return null; }
  }

  function makeCard(item) {
    const card = node("article", "item-card");
    const category = item.category || "분류 보류";
    const top = node("div", "card-top");
    top.append(node("span", `category-tag${category === "분류 보류" ? " pending" : ""}`, category), node("span", "classification", classificationNames[item.classification] || (category === "분류 보류" ? "분류 보류" : "키워드 분류")));
    const url = safeUrl(item.url);
    const title = item.title || (url ? `${url.hostname.replace(/^www\./, "")}에서 가져온 링크` : "수집한 자료");
    const heading = node("h3", "item-title", title);
    heading.title = title;
    const content = item.text || "아직 분류할 내용이 충분하지 않습니다. 원문을 확인하고 직접 분류할 수 있어요.";
    card.append(top, heading, node("p", `item-text${item.text ? "" : " missing"}`, content));
    if (url) {
      const link = node("a", "item-link");
      link.href = url.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.title = item.url;
      link.setAttribute("aria-label", `${title} 원문 새 창에서 열기`);
      const display = `${url.hostname.replace(/^www\./, "")}${url.pathname === "/" ? "" : url.pathname}`;
      const arrow = node("span", "link-arrow", "↗");
      arrow.setAttribute("aria-hidden", "true");
      link.append(arrow, node("span", "link-text", display));
      card.append(link);
    }
    if (item.note) card.append(node("p", "item-note", item.note));
    const bottom = node("div", "card-bottom");
    const sources = node("div", "source-badges");
    for (const source of Array.from(new Set(item.sources || []))) sources.append(node("span", "source-badge", sourceNames[source] || source));
    if (!sources.childElementCount) sources.append(node("span", "source-badge", "현재 화면"));
    const edit = node("button", "edit-button", "분류 수정");
    edit.type = "button";
    edit.setAttribute("aria-expanded", "false");
    edit.setAttribute("aria-label", `${title} 분류 수정`);
    bottom.append(sources, edit);
    const editor = node("form", "item-editor");
    editor.hidden = true;
    const categoryLabel = node("label", "field-label", "카테고리");
    const categoryInput = node("select", "editor-input");
    for (const name of state.status?.categories || defaultCategories) {
      const option = node("option", "", name);
      option.value = name;
      categoryInput.append(option);
    }
    categoryInput.value = category;
    categoryInput.required = true;
    categoryLabel.append(categoryInput);
    const noteLabel = node("label", "field-label", "메모");
    const noteInput = node("textarea", "editor-textarea");
    noteInput.value = item.note || "";
    noteInput.placeholder = "기억해 둘 내용을 적어 보세요.";
    noteInput.maxLength = 1000;
    noteInput.rows = 3;
    noteLabel.append(noteInput);
    const actions = node("div", "editor-actions");
    const cancel = node("button", "button button-outline", "취소");
    cancel.type = "button";
    const save = node("button", "button button-primary", "저장");
    save.type = "submit";
    actions.append(cancel, save);
    editor.append(categoryLabel, noteLabel, actions);
    function closeEditor() {
      editor.hidden = true;
      edit.setAttribute("aria-expanded", "false");
      edit.textContent = "분류 수정";
      edit.focus();
    }
    edit.addEventListener("click", () => {
      if (!editor.hidden) { closeEditor(); return; }
      editor.hidden = false;
      edit.setAttribute("aria-expanded", "true");
      edit.textContent = "접기";
      categoryInput.focus();
    });
    cancel.addEventListener("click", () => {
      categoryInput.value = category;
      noteInput.value = item.note || "";
      closeEditor();
    });
    editor.addEventListener("submit", async (event) => {
      event.preventDefault();
      const updatedCategory = categoryInput.value.trim();
      if (!updatedCategory) { categoryInput.focus(); return; }
      save.disabled = true;
      cancel.disabled = true;
      edit.disabled = true;
      save.textContent = "저장 중";
      try {
        await request(`/api/items/${encodeURIComponent(item.id)}`, { method: "PATCH", body: JSON.stringify({ category: updatedCategory, note: noteInput.value.trim() }) });
        toast("분류와 메모를 저장했습니다.");
        await Promise.all([loadItems(), refreshStatus()]);
      } catch (error) { toast(error.message || "수정 내용을 저장하지 못했습니다.", true); }
      finally { save.disabled = false; cancel.disabled = false; edit.disabled = false; save.textContent = "저장"; }
    });
    card.append(bottom, editor);
    return card;
  }

  function renderItems() {
    const grid = byId("items-grid");
    grid.replaceChildren(...state.items.map(makeCard));
    byId("result-count").textContent = `${state.items.length}개`;
    byId("library-heading").textContent = state.category || (state.source ? sourceNames[state.source] : "전체 자료");
    const filters = [state.source ? sourceNames[state.source] : "", state.category, state.query ? `‘${state.query}’ 검색` : ""].filter(Boolean);
    byId("filter-summary").hidden = !filters.length;
    byId("filter-summary").textContent = filters.join(" · ");
    const empty = byId("empty-state");
    empty.hidden = state.items.length > 0;
    if (state.loadFailed) {
      byId("empty-title").textContent = "자료를 불러오지 못했어요";
      byId("empty-description").textContent = "서버 연결을 확인한 뒤 다시 불러와 주세요.";
      byId("empty-action").textContent = "다시 불러오기";
      byId("empty-footnote").hidden = true;
    } else if (filters.length) {
      byId("empty-title").textContent = "조건에 맞는 자료가 없어요";
      byId("empty-description").textContent = "다른 검색어를 입력하거나 필터를 해제해 보세요.";
      byId("empty-action").textContent = "필터 초기화";
      byId("empty-footnote").hidden = true;
    } else {
      byId("empty-title").textContent = "아직 모아 둔 자료가 없어요";
      byId("empty-description").textContent = "PC 크롬·엣지에 확장 프로그램을 설치하세요.\n평소 쓰는 Instagram에서 자료를 가져온 뒤 이곳에서 확인할 수 있어요.";
      byId("empty-action").textContent = "PC에서 가져오는 방법 보기 ↑";
      byId("empty-footnote").hidden = false;
    }
  }

  async function loadItems() {
    const requestId = ++state.requestId;
    const params = new URLSearchParams({ q: state.query, category: state.category, source: state.source });
    byId("items-loading").hidden = false;
    byId("empty-state").hidden = true;
    try {
      const data = await request(`/api/items?${params}`);
      if (requestId !== state.requestId) return;
      state.items = Array.isArray(data.items) ? data.items : [];
      state.loadFailed = false;
      renderItems();
      renderCategories();
    } catch (error) {
      if (requestId !== state.requestId) return;
      state.loadFailed = true;
      state.items = [];
      renderItems();
      toast(error.message || "자료를 불러오지 못했습니다.", true);
    } finally {
      if (requestId === state.requestId) byId("items-loading").hidden = true;
    }
  }

  function schedulePoll() {
    clearTimeout(state.pollTimer);
    state.pollTimer = setTimeout(refreshStatus, isBusy() ? 2000 : 6000);
  }

  async function refreshStatus() {
    const requestId = ++state.statusRequestId;
    const wasBusy = Boolean(state.status?.busy);
    try {
      const status = await request("/api/status");
      if (requestId !== state.statusRequestId) return;
      const totalChanged = state.status && status.total !== state.status.total;
      if (status.session_warning && status.session_warning !== state.status?.session_warning) toast(status.session_warning, true);
      state.status = status;
      state.statusFailed = false;
      if (state.importAwaitingCompletion && !status.busy) {
        const jobStatus = status.job?.status;
        if (["done", "completed", "success"].includes(jobStatus)) {
          state.importAwaitingCompletion = false;
          discardImport();
          toast("가져온 자료를 저장했습니다.");
        } else if (["error", "failed", "idle"].includes(jobStatus)) {
          state.importAwaitingCompletion = false;
          showImportError(status.job?.message || "자료를 저장하지 못했습니다. 가져올 내용을 유지했으니 다시 시도해 주세요.");
        }
      }
      updateControls();
      renderJob();
      renderCategories();
      if ((wasBusy && !status.busy) || totalChanged) await loadItems();
    } catch (error) {
      if (requestId !== state.statusRequestId) return;
      if (!state.statusFailed) toast(error.message || "서버에 연결하지 못했습니다.", true);
      state.statusFailed = true;
      updateControls();
    } finally { if (requestId === state.statusRequestId) schedulePoll(); }
  }

  async function toggleBrowser() {
    if (state.browserStarting) return;
    state.browserOpen = !state.browserOpen;
    updateControls();
    if (!state.browserOpen) return;
    if (state.status?.browser_ready) {
      byId("browser-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
      return;
    }
    state.browserStarting = true;
    ++state.statusRequestId;
    clearTimeout(state.pollTimer);
    byId("browser-loading").hidden = false;
    updateControls();
    renderJob();
    try {
      const status = await request("/api/browser/start", { method: "POST" });
      if (status && "browser_ready" in status) state.status = status;
      state.statusFailed = false;
      await refreshStatus();
    } catch (error) {
      state.browserOpen = false;
      toast(error.message || "브라우저를 시작하지 못했습니다.", true);
    } finally {
      state.browserStarting = false;
      updateControls();
      renderJob();
      schedulePoll();
    }
  }

  async function collect(mode) {
    if (isBusy() || !state.status?.browser_ready) return;
    state.collectionStarting = true;
    ++state.statusRequestId;
    clearTimeout(state.pollTimer);
    updateControls();
    renderJob();
    try {
      await request("/api/collect", { method: "POST", body: JSON.stringify({ mode, use_ai: Boolean(state.status?.ai_available && byId("use-ai").checked) }) });
      await refreshStatus();
      if (!state.status?.busy) await loadItems();
    } catch (error) { toast(error.message || "자료를 수집하지 못했습니다.", true); }
    finally {
      state.collectionStarting = false;
      updateControls();
      renderJob();
      schedulePoll();
    }
  }

  async function reclassify() {
    if (isBusy() || !state.status?.ai_available) return;
    state.classifyStarting = true;
    ++state.statusRequestId;
    clearTimeout(state.pollTimer);
    updateControls();
    renderJob();
    try {
      await request("/api/classify", { method: "POST", body: JSON.stringify({ only_pending: false }) });
      await refreshStatus();
      if (!state.status?.busy) await loadItems();
    } catch (error) { toast(error.message || "AI 분류를 시작하지 못했습니다.", true); }
    finally {
      state.classifyStarting = false;
      updateControls();
      renderJob();
      schedulePoll();
    }
  }

  function importUrl(value, field, instagramOnly = false) {
    if (typeof value !== "string" || !value.trim() || value.length > 2048) throw new Error(field + "을 확인해 주세요.");
    const parsed = safeUrl(value.trim());
    if (!parsed || parsed.username || parsed.password) throw new Error("로그인 정보가 없는 HTTP 또는 HTTPS 링크만 가져올 수 있어요.");
    if (instagramOnly && !instagramHosts.has(parsed.hostname.toLowerCase())) throw new Error("수집 화면 주소는 Instagram 주소여야 합니다.");
    return parsed;
  }

  function parseImport(raw) {
    if (typeof raw !== "string" || !raw.trim()) throw new Error("확장에서 복사한 자료를 붙여넣어 주세요.");
    if (new TextEncoder().encode(raw).length > maxImportBytes) throw new Error("자료가 너무 큽니다. 확장에서 적은 수의 자료를 나누어 가져오세요.");
    let payload;
    try { payload = JSON.parse(raw); }
    catch { throw new Error("자료 형식을 읽지 못했습니다. 확장의 ‘자료 복사’로 복사한 내용을 붙여넣어 주세요."); }
    if (!payload || typeof payload !== "object" || Array.isArray(payload) || payload.version !== 1 ||
        Object.keys(payload).some((key) => !["version", "records"].includes(key)) ||
        !Array.isArray(payload.records) || payload.records.length < 1 || payload.records.length > 500) {
      throw new Error("지원하는 가져오기 형식이 아닙니다. 한 번에 1~500개의 자료를 가져올 수 있어요.");
    }
    return payload.records.map((record) => {
      if (!record || typeof record !== "object" || Array.isArray(record) ||
          Object.keys(record).some((key) => !["url", "title", "text", "source", "source_url"].includes(key)) ||
          !["saved", "dm", "post"].includes(record.source)) {
        throw new Error("자료에 지원하지 않는 항목이 있습니다. 확장에서 자료를 다시 모아 주세요.");
      }
      const url = importUrl(record.url, "자료 링크");
      if (instagramHosts.has(url.hostname.toLowerCase())) {
        if (!/^\/(?:p|reel|tv)\/[A-Za-z0-9_-]+\/?$/.test(url.pathname)) throw new Error("Instagram 게시물·릴스 링크만 가져올 수 있어요.");
      } else if (record.source !== "dm") throw new Error("외부 링크는 DM에서 가져온 자료만 지원합니다.");
      const title = record.title === undefined ? "" : record.title;
      const text = record.text === undefined ? "" : record.text;
      if (typeof title !== "string" || title.length > 250 || typeof text !== "string" || text.length > 2500) {
        throw new Error("자료의 제목이나 설명이 너무 깁니다. 확장에서 다시 모아 주세요.");
      }
      const clean = { url: record.url.trim(), title, text, source: record.source };
      if (record.source_url !== undefined) {
        importUrl(record.source_url, "수집 화면 주소", true);
        clean.source_url = record.source_url.trim();
      }
      return clean;
    });
  }

  function showImportError(message) {
    byId("import-error").textContent = message;
    byId("import-error").hidden = !message;
  }

  function renderImportPreview() {
    const records = state.pendingImport;
    byId("import-preview").hidden = !records;
    byId("import-list").replaceChildren();
    if (!records) { updateControls(); return; }
    byId("import-count").textContent = records.length + "개";
    const counts = { saved: 0, dm: 0, post: 0 };
    for (const record of records) counts[record.source] += 1;
    byId("import-summary").textContent = Object.entries(counts).filter(([, count]) => count).map(([source, count]) => sourceNames[source] + " " + count + "개").join(" · ");
    for (const record of records) {
      const row = node("li", "import-record");
      const heading = node("div", "import-record-heading");
      heading.append(node("span", "source-badge", sourceNames[record.source]), node("strong", "", record.title || "제목 없는 자료"));
      row.append(heading, node("p", "import-record-url", record.url));
      if (record.text.trim()) {
        const context = node("details", "import-record-context");
        context.append(node("summary", "", "분류에 사용할 설명 보기"), node("p", "", record.text));
        row.append(context);
      } else row.append(node("p", "import-record-missing", "설명이 없어 AI가 분류하기 어려울 수 있어요."));
      byId("import-list").append(row);
    }
    updateControls();
  }

  function stageImport(raw, focus = true) {
    state.pendingImportRaw = raw;
    try {
      state.pendingImport = parseImport(raw);
      importBridge?.persist(raw);
      showImportError(importBridge?.storageFailed ? "브라우저 임시 저장공간을 사용할 수 없습니다. 가져오기를 마치기 전 이 탭을 닫지 마세요." : "");
    } catch (error) {
      state.pendingImport = null;
      showImportError(error.message || "가져올 자료를 읽지 못했습니다.");
    }
    renderImportPreview();
    if (focus) {
      if (state.pendingImport) {
        byId("import-preview").scrollIntoView({ behavior: "smooth", block: "nearest" });
        byId("import-preview").focus({ preventScroll: true });
      } else byId("import-paste-input").focus();
    }
  }

  function loadPendingImport(focus = true) {
    importBridge?.capture();
    if (importBridge?.error) {
      state.pendingImport = null;
      showImportError(importBridge.error);
      renderImportPreview();
      return;
    }
    const raw = importBridge?.read();
    if (raw) stageImport(raw, focus);
  }

  function discardImport() {
    state.pendingImport = null;
    state.pendingImportRaw = null;
    importBridge?.clear();
    byId("import-paste-input").value = "";
    showImportError("");
    renderImportPreview();
  }

  async function confirmImport() {
    if (isBusy() || !state.pendingImport) return;
    const records = state.pendingImport;
    state.importStarting = true;
    ++state.statusRequestId;
    clearTimeout(state.pollTimer);
    updateControls();
    renderJob();
    try {
      await request("/api/import", {
        method: "POST",
        body: JSON.stringify({ records, use_ai: Boolean(state.status?.ai_available && byId("use-ai").checked) }),
      });
      state.importAwaitingCompletion = true;
      toast("자료를 가져오고 있습니다. 저장이 끝날 때까지 내용을 유지합니다.");
      await refreshStatus();
      if (!state.status?.busy) await loadItems();
      byId("job-progress").focus({ preventScroll: true });
    } catch (error) {
      showImportError(error.message || "자료를 가져오지 못했습니다. 내용을 유지했으니 다시 시도해 주세요.");
    } finally {
      state.importStarting = false;
      updateControls();
      renderJob();
      schedulePoll();
    }
  }

  async function loadAiConfig() {
    try {
      const config = await request("/api/ai/config");
      const provider = Object.hasOwn(aiProviders, config.provider) ? config.provider : "openai";
      const model = config.model || aiProviders[provider].model;
      state.aiConfig = { configured: Boolean(config.configured), provider, model, source: config.source };
      if (!state.configSaving) {
        byId("ai-provider").value = provider;
        byId("ai-model").value = model;
        state.aiEditorProvider = provider;
        state.aiEditorModels[provider] = model;
      }
      byId("ai-config-status").textContent = config.configured ? `${aiProviders[provider].name} API가 연결되어 있습니다. 모델: ${model}${config.source === "env" || config.source === "environment" ? " · 서버 환경 변수에서 설정됨" : ""}` : `아직 연결된 ${aiProviders[provider].name} API 키가 없습니다.`;
      byId("ai-api-key").placeholder = config.configured ? "변경할 API 키를 입력하세요" : "API 키를 입력하세요";
    } catch (error) {
      byId("ai-config-status").textContent = "설정 상태를 불러오지 못했습니다. 다시 열어 확인해 주세요.";
    }
  }

  function openAiSettings() {
    byId("ai-api-key").value = "";
    byId("ai-settings-error").hidden = true;
    byId("ai-settings-dialog").showModal();
    loadAiConfig();
  }

  function closeAiSettings() {
    if (state.configSaving) return;
    byId("ai-settings-dialog").close();
  }

  byId("ai-settings-button").addEventListener("click", openAiSettings);
  byId("ai-setup-cta").addEventListener("click", openAiSettings);
  byId("ai-settings-close").addEventListener("click", closeAiSettings);
  byId("ai-settings-cancel").addEventListener("click", closeAiSettings);
  byId("ai-settings-dialog").addEventListener("close", () => { byId("ai-api-key").value = ""; });
  byId("ai-settings-dialog").addEventListener("cancel", (event) => { if (state.configSaving) event.preventDefault(); });
  byId("ai-provider").addEventListener("change", () => {
    const currentModel = byId("ai-model").value.trim();
    if (currentModel) state.aiEditorModels[state.aiEditorProvider] = currentModel;
    const provider = byId("ai-provider").value;
    if (!Object.hasOwn(aiProviders, provider)) return;
    state.aiEditorProvider = provider;
    byId("ai-model").value = state.aiEditorModels[provider] || aiProviders[provider].model;
  });
  byId("ai-settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.configSaving) return;
    const keyInput = byId("ai-api-key");
    const model = byId("ai-model").value.trim();
    const provider = byId("ai-provider").value;
    if (!keyInput.value.trim() || !model || !Object.hasOwn(aiProviders, provider)) return;
    state.configSaving = true;
    byId("ai-settings-error").hidden = true;
    for (const id of ["ai-settings-save", "ai-settings-close", "ai-settings-cancel", "ai-api-key", "ai-model", "ai-provider"]) byId(id).disabled = true;
    byId("ai-settings-save").textContent = "연결 확인 중";
    try {
      await request("/api/ai/config", { method: "PUT", body: JSON.stringify({ api_key: keyInput.value.trim(), model, provider }) });
      keyInput.value = "";
      await Promise.all([loadAiConfig(), refreshStatus()]);
      toast("AI API를 연결했습니다.");
      byId("ai-settings-dialog").close();
    } catch (error) {
      byId("ai-settings-error").textContent = error.message || "API 연결을 확인하지 못했습니다.";
      byId("ai-settings-error").hidden = false;
    } finally {
      keyInput.value = "";
      state.configSaving = false;
      for (const id of ["ai-settings-save", "ai-settings-close", "ai-settings-cancel", "ai-api-key", "ai-model", "ai-provider"]) byId(id).disabled = false;
      byId("ai-settings-save").textContent = "연결 확인 · 저장";
    }
  });
  byId("use-ai").addEventListener("change", () => { state.aiPreference = byId("use-ai").checked; updateControls(); });
  byId("reclassify-button").addEventListener("click", reclassify);

  function resetFilters() {
    state.source = "";
    state.category = "";
    state.query = "";
    byId("search-input").value = "";
    for (const button of document.querySelectorAll(".source-button")) {
      const active = button.dataset.source === "";
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    }
    renderCategories();
    loadItems();
  }

  for (const button of document.querySelectorAll(".source-button")) button.addEventListener("click", () => {
    state.source = button.dataset.source;
    for (const other of document.querySelectorAll(".source-button")) {
      const active = other === button;
      other.classList.toggle("active", active);
      other.setAttribute("aria-pressed", String(active));
    }
    loadItems();
  });
  byId("clear-category").addEventListener("click", () => { state.category = ""; renderCategories(); loadItems(); });
  byId("search-input").addEventListener("input", (event) => {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => { state.query = event.target.value.trim(); loadItems(); }, 250);
  });
  byId("browser-button").addEventListener("click", toggleBrowser);
  byId("empty-action").addEventListener("click", () => {
    if (state.loadFailed) loadItems();
    else if (state.source || state.category || state.query) resetFilters();
    else {
      byId("client-collection").scrollIntoView({ behavior: "smooth", block: "start" });
      byId("client-collection").focus({ preventScroll: true });
    }
  });
  byId("collect-current").addEventListener("click", () => collect("current"));
  byId("collect-scroll").addEventListener("click", () => collect("scroll"));
  byId("logout-button").addEventListener("click", async () => {
    const button = byId("logout-button");
    button.disabled = true;
    try {
      await request("/api/logout", { method: "POST" });
      window.location.assign("/login");
    } catch (error) { toast(error.message || "로그아웃하지 못했습니다.", true); button.disabled = false; }
  });
  byId("import-paste-preview").addEventListener("click", () => stageImport(byId("import-paste-input").value));
  byId("import-paste-input").addEventListener("input", updateControls);
  byId("import-confirm").addEventListener("click", confirmImport);
  byId("import-discard").addEventListener("click", () => { discardImport(); byId("client-collection").focus({ preventScroll: true }); });
  window.addEventListener("hashchange", () => { if (!state.importStarting && !state.importAwaitingCompletion) loadPendingImport(); });
  window.addEventListener("pagehide", () => clearTimeout(state.pollTimer));
  loadPendingImport();
  Promise.all([refreshStatus(), loadItems()]);
})();
