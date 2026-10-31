"use strict";
const $ = (id) => document.getElementById(id);
const state = {candidates: [], avatars: [], visible: [], selected: null, filter: "selected", renderLimit: 120, exporting: false, avatarExporting: false, previewSerial: 0, previewTimer: null, favoriteSaving: new Set(), native: new Map(), nativePending: new Map(), viewerIndex: -1, viewerSerial: 0};
const defaultSettings = {"size-preset": "3456x2234", fit: "cover", "focus-x": ".5", "focus-y": ".5", exposure: "0", contrast: "1", sharpen: ".25"};
const PAGE_SIZE = 120;
const SCOPE_NAMES = {selected: "精选", all: "全部候选", favorites: "收藏"};
const RANGE_DEFAULTS = {"focus-x": .5, "focus-y": .5, exposure: 0, contrast: 1, sharpen: .25};
const TUNE_IDS = ["exposure", "contrast", "sharpen"];
const galleryScope = {kind: null, value: ""};
function displayTitle(candidate) {
  // Drop the pick number and leading parts already shown in the source line.
  const parts = candidate.title.replace(/^\s*\d+\s*·\s*/, "").split(/\s*·\s*/);
  while (parts.length > 1 && (parts[0] === "MV" || candidate.source_label.includes(parts[0]))) parts.shift();
  return parts.join(" · ");
}
function shortTime(candidate) { return candidate.timecode.replace(/\.\d+$/, ""); }
function sourceOptions(kind) {
  return [...new Set(state.candidates.filter(c => c.source_kind === kind).map(c => c.source_label))].sort((a, b) => a.localeCompare(b, "zh-CN", {numeric: true}));
}
function renderSourceFilters() {
  for (const kind of ["live", "mv"]) {
    const select = $(`${kind}-source-filter`), options = sourceOptions(kind);
    if (galleryScope.kind === kind && !options.includes(galleryScope.value)) galleryScope.value = "";
    select.replaceChildren(new Option(kind === "live" ? "全部场次" : "全部曲目", ""), ...options.map(label => new Option(label, label)));
    select.value = galleryScope.kind === kind ? galleryScope.value : "";
    select.disabled = options.length === 0;
  }
}
function matchesSourceFilter(candidate) {
  return !galleryScope.kind || (candidate.source_kind === galleryScope.kind && (!galleryScope.value || candidate.source_label === galleryScope.value));
}
function setScope(filter) { state.filter = filter; state.renderLimit = PAGE_SIZE; state.animateGallery = true; renderGallery(); }
function setSourceKind(kind) {
  if (galleryScope.kind !== kind) { galleryScope.kind = kind; galleryScope.value = ""; }
  state.renderLimit = PAGE_SIZE; state.animateGallery = true;
  renderSourceFilters(); renderGallery();
}
function scopePool() {
  const active = state.candidates.filter(c => c.active !== false);
  return state.filter === "selected" ? state.candidates.filter(c => c.selected) : state.filter === "favorites" ? state.candidates.filter(c => c.favorite) : active;
}
function visibleCandidates() {
  const scoped = scopePool().filter(matchesSourceFilter);
  return state.filter === "selected" ? scoped : [...scoped].sort((a, b) => a.source_label.localeCompare(b.source_label, "zh-CN") || a.timestamp - b.timestamp);
}
function sourceText() { return !galleryScope.kind ? "全部来源" : galleryScope.value || (galleryScope.kind === "user" ? "我截的" : galleryScope.kind === "live" ? "全部 Live 场次" : "全部 MV 曲目"); }
function scopeText() { return `${sourceText()} · ${SCOPE_NAMES[state.filter]} ${state.visible.length} 张`; }
function validSize(p) {
  return Number.isInteger(p.width) && Number.isInteger(p.height) && Math.min(p.width, p.height) >= 256 && Math.max(p.width, p.height) <= 8192 && p.width * p.height <= 34000000;
}
function nativeReady() {
  const image = $("main-preview");
  return !!state.selected?.source_available && image.dataset.quality === "native" && image.dataset.candidateId === state.selected.id;
}
function status(message, error = false) { $("export-status").textContent = message; $("export-status").classList.toggle("error", error); }
function params() {
  const size = $("size-preset").value === "custom" ? [Number($("width").value), Number($("height").value)] : $("size-preset").value.split("x").map(Number);
  return {candidate_id: state.selected?.id, width: size[0], height: size[1], fit: $("fit").value, focus_x: Number($("focus-x").value), focus_y: Number($("focus-y").value), exposure: Number($("exposure").value), contrast: Number($("contrast").value), sharpen: Number($("sharpen").value)};
}
function updatePreview() {
  const p = params();
  const width = Number.isFinite(p.width) && p.width > 0 ? p.width : 2560;
  const height = Number.isFinite(p.height) && p.height > 0 ? p.height : 1440;
  const stage = $("preview-stage");
  const stageStyle = getComputedStyle(stage);
  const paddingY = parseFloat(stageStyle.paddingTop) + parseFloat(stageStyle.paddingBottom);
  const usableWidth = stage.clientWidth - parseFloat(stageStyle.paddingLeft) - parseFloat(stageStyle.paddingRight);
  // On wide screens the stage grows to the target ratio, up to about one screen high.
  stage.style.height = innerWidth > 760 ? `${Math.round(Math.max(370, Math.min(usableWidth * height / width + paddingY, innerHeight * .8)))}px` : "";
  const usableHeight = stage.clientHeight - paddingY;
  $("preview-frame").style.width = `${Math.min(usableWidth, usableHeight * width / height)}px`;
  $("preview-frame").style.aspectRatio = `${width}/${height}`;
  const image = $("main-preview");
  image.style.objectFit = p.fit;
  image.style.objectPosition = `${p.focus_x * 100}% ${p.focus_y * 100}%`;
  image.style.filter = `brightness(${2 ** (p.exposure / 2.2)}) contrast(${p.contrast})`;
  $("preview-dimensions").textContent = `${width} × ${height}`;
  $("custom-size").hidden = $("size-preset").value !== "custom";
  $("focus-x-value").textContent = `${Math.round(p.focus_x * 100)}%`;
  $("focus-y-value").textContent = `${Math.round(p.focus_y * 100)}%`;
  $("exposure-value").textContent = p.exposure.toFixed(1);
  $("contrast-value").textContent = p.contrast.toFixed(2);
  $("sharpen-value").textContent = p.sharpen.toFixed(2);
  $("focus-x").disabled = $("focus-y").disabled = p.fit === "contain";
  $("crop-hint").textContent = p.fit === "contain" ? "完整保留原画面；屏幕比例不同时会补边。" : "拖动焦点取景；画框之外的内容会被裁掉。";
  for (const [id, active] of [["desktop-preset", width > height], ["phone-preset", width < height]]) { $(id).classList.toggle("active", active); $(id).setAttribute("aria-pressed", String(active)); }
  for (const id of Object.keys(RANGE_DEFAULTS)) {
    const changed = rangeChanged(id);
    $(`${id}-value`).classList.toggle("changed", changed);
    document.querySelector(`.reset-one[data-for="${id}"]`).classList.toggle("show", changed && !$(id).disabled);
  }
  const tuned = TUNE_IDS.filter(rangeChanged).length;
  $("tune-count").textContent = tuned ? `已调整 ${tuned} 项` : "保持原样";
  $("tune-reset").classList.toggle("show", tuned > 0);
}
function rangeChanged(id) { return Math.abs(Number($(id).value) - RANGE_DEFAULTS[id]) > 1e-6; }
// Slide back to the default so the change stays visible on the preview.
function resetRange(id) {
  const input = $(id), from = Number(input.value), to = RANGE_DEFAULTS[id], start = performance.now();
  if (from === to) return;
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) { input.value = to; updatePreview(); return; }
  let done = false;
  const finish = () => { if (done) return; done = true; input.value = to; updatePreview(); };
  requestAnimationFrame(function step(now) {
    if (done) return;
    const k = Math.min(1, (now - start) / 260);
    if (k >= 1) return finish();
    input.value = from + (to - from) * (1 - (1 - k) ** 3);
    updatePreview();
    requestAnimationFrame(step);
  });
  // Background tabs may pause animation frames; still land on the default.
  setTimeout(finish, 320);
}
function sizeChanged() {
  const p = params();
  $("fit").value = "cover";
  updatePreview();
}
// Reuse each frame's polling; a current request promotes an in-flight prefetch.
function nativeFor(candidate, priority = "current") {
  const existing = state.nativePending.get(candidate.id);
  if (existing && (priority === "prefetch" || existing.priority === "current")) return existing.promise;
  if (priority === "prefetch" && state.native.has(candidate.id)) return Promise.resolve(state.native.get(candidate.id));
  const entry = {priority};
  entry.promise = (async () => {
    // Even a memory-cached current frame tells the server to discard old work.
    let response = await fetch("/api/previews", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({candidate_id: candidate.id, priority})});
    let job = await response.json();
    if (!response.ok) throw new Error(typeof job.detail === "string" ? job.detail : "原帧暂未载入。");
    const deadline = Date.now() + 210000;
    while (job.status !== "done") {
      if (typeof dispatchEvent === "function") dispatchEvent(new CustomEvent("native-status", {detail: {id: candidate.id, status: job.status}}));
      if (job.status === "cancelled") return null;
      if (job.status === "failed") throw new Error(job.error);
      if (Date.now() > deadline) throw new Error("原帧载入超时，可重新选择这一帧。");
      await new Promise(resolve => setTimeout(resolve, 550));
      response = await fetch(`/api/preview-jobs/${encodeURIComponent(job.id)}`);
      job = await response.json();
      if (!response.ok) throw new Error("原帧载入状态暂时不可用。");
    }
    const picture = new Image();
    picture.src = job.preview;
    await picture.decode();
    state.native.set(candidate.id, job);
    return job;
  })();
  state.nativePending.set(candidate.id, entry);
  entry.promise.finally(() => {
    if (state.nativePending.get(candidate.id) === entry) state.nativePending.delete(candidate.id);
  }).catch(() => {});
  return entry.promise;
}
// Refresh power state for each new warmup, including unplugging mid-session.
async function prefetchAfter(list, index, isCurrent) {
  let count = 2;
  try {
    const response = await fetch("/api/power");
    if (response.ok && (await response.json()).on_battery) count = 1;
  } catch (_) { /* Unreadable power state defaults to AC. */ }
  for (const candidate of list.slice(index + 1, index + 1 + count)) {
    if (!isCurrent()) return;
    if (!candidate.source_available || state.native.has(candidate.id)) continue;
    try { if (!await nativeFor(candidate, "prefetch")) return; } catch (_) { return; }
  }
}
async function loadNativePreview(candidate, serial) {
  if (serial !== state.previewSerial || !$("viewer").hidden) return;
  try {
    let job = await nativeFor(candidate);
    // A rapid A -> B -> A can encounter A's cancelled shared poller.
    while (!job && serial === state.previewSerial && $("viewer").hidden && state.selected?.id === candidate.id) job = await nativeFor(candidate);
    if (!job) return;
    if (serial !== state.previewSerial || state.selected?.id !== candidate.id) return;
    stopLoader("preview", true);
    $("main-preview").src = job.preview;
    $("main-preview").dataset.quality = "native";
    $("main-preview").dataset.candidateId = candidate.id;
    $("preview-label").textContent = "视频原帧";
    $("preview-quality").textContent = `原帧 ${job.width} × ${job.height} · 未锐化`;
    $("native-preview-link").href = job.original;
    $("native-download-link").href = job.download;
    $("native-preview-link").hidden = $("native-download-link").hidden = false;
    if ($("viewer").hidden) prefetchAfter(state.visible, state.visible.indexOf(candidate), () => serial === state.previewSerial);
  } catch (error) {
    if (serial !== state.previewSerial) return;
    stopLoader("preview");
    $("preview-quality").textContent = `${error.message} 当前为选片缩略图。`;
  }
}
// Loading feedback: blurred thumbnail, a bar that follows the job's real status, elapsed time.
const loaders = {};
function renderLoader(loader) {
  loader.label.textContent = `${loader.prefix}${loader.status === "running" ? "正在解码原帧" : "排队中"} · ${((performance.now() - loader.start) / 1000).toFixed(1)} 秒`;
}
function startLoader(name, frame, bar, label, candidateId, prefix = "") {
  stopLoader(name);
  const loader = {frame, bar, label, candidateId, prefix, status: "queued", start: performance.now()};
  frame.classList.add("is-loading"); bar.className = bar.className.replace(/ (queued|running|done)/g, "") + " queued";
  loader.timer = setInterval(() => renderLoader(loader), 100); renderLoader(loader);
  loaders[name] = loader;
}
function stopLoader(name, done = false) {
  const loader = loaders[name];
  if (!loader) return;
  clearInterval(loader.timer); delete loaders[name];
  loader.frame.classList.remove("is-loading");
  loader.bar.className = loader.bar.className.replace(/ (queued|running|done)/g, "") + (done ? " done" : "");
}
addEventListener("native-status", event => {
  for (const loader of Object.values(loaders)) {
    if (loader.candidateId !== event.detail.id || !["queued", "running"].includes(event.detail.status)) continue;
    loader.status = event.detail.status;
    loader.bar.className = loader.bar.className.replace(/ (queued|running|done)/g, "") + ` ${loader.status}`;
    renderLoader(loader);
  }
});
function choose(candidate) {
  state.previewSerial += 1;
  clearTimeout(state.previewTimer);
  const serial = state.previewSerial;
  state.selected = candidate;
  const image = $("main-preview");
  const cached = state.native.get(candidate.id);
  image.src = candidate.thumbnail || candidate.preview;
  image.dataset.quality = "thumbnail";
  image.dataset.candidateId = candidate.id;
  image.alt = `${displayTitle(candidate)}，${candidate.source_label}，${candidate.timecode}`;
  image.hidden = false;
  $("preview-empty").hidden = true;
  $("image-title").textContent = displayTitle(candidate);
  $("image-source").textContent = `${candidate.source_label} · ${candidate.timecode}`;
  $("image-note").textContent = candidate.note;
  $("preview-label").textContent = "选片缩略图";
  $("preview-quality").textContent = candidate.source_available ? "正在载入视频原帧…" : "源视频不可用，当前为选片缩略图。";
  if (candidate.source_available && !cached) startLoader("preview", $("preview-frame"), $("preview-load"), $("preview-quality"), candidate.id); else stopLoader("preview");
  $("native-preview-link").hidden = $("native-download-link").hidden = true;
  $("review-badge").hidden = false;
  $("review-badge").textContent = candidate.reviewed ? "Agent 已看画面" : "自动候选 · 待看画面";
  $("export-button").disabled = state.exporting || !candidate.source_available;
  if (!state.exporting) status(candidate.source_available ? "完成后自动下载 PNG，并保留未处理原帧。" : "源视频已移动，暂时只能查看已有预览。", !candidate.source_available);
  $("export-result").hidden = true; if (!state.exporting) $("export-steps").hidden = true;
  renderGallery();
  updatePreview();
  if (candidate.source_available) state.previewTimer = setTimeout(() => loadNativePreview(candidate, serial), cached ? 0 : 180);
}
function renderWorkspaceNav() {
  const index = state.visible.findIndex(c => c.id === state.selected?.id);
  $("workspace-position").textContent = !state.selected ? "" : index < 0 ? "不在当前筛选" : `${index + 1} / ${state.visible.length}`;
  $("workspace-prev").disabled = $("workspace-next").disabled = !state.visible.length;
  $("back-to-gallery").disabled = index < 0;
}
function stepWorkspace(delta) {
  const list = state.visible;
  if (!list.length) return;
  const index = list.findIndex(c => c.id === state.selected?.id);
  choose(list[index < 0 ? 0 : (index + delta + list.length) % list.length]);
}
function revealInGallery(candidate, focus = true) {
  const index = state.visible.findIndex(c => c.id === candidate.id);
  if (index < 0) return;
  if (index >= state.renderLimit) { state.renderLimit = Math.ceil((index + 1) / PAGE_SIZE) * PAGE_SIZE; renderGallery(); }
  const target = $(`candidate-surface-${candidate.id}`);
  target?.scrollIntoView({block: "center"});
  if (focus) target?.focus({preventScroll: true});
}
function rollNumber(element, from, to) {
  element.textContent = from;
  if (from === to || matchMedia("(prefers-reduced-motion: reduce)").matches) { element.textContent = to; return; }
  const start = performance.now();
  requestAnimationFrame(function step(now) {
    if (!element.isConnected) return;
    const k = Math.min(1, (now - start) / 420);
    element.textContent = Math.round(from + (to - from) * (1 - (1 - k) ** 3));
    if (k < 1) requestAnimationFrame(step);
  });
  // Background tabs may pause animation frames; still land on the real number.
  setTimeout(() => { element.textContent = to; }, 480);
}
function renderGallery() {
  state.visible = visibleCandidates();
  const visible = state.visible, shown = visible.slice(0, state.renderLimit);
  const grid = $("candidate-grid");
  const focusedId = grid.contains(document.activeElement) ? document.activeElement.id : null;
  grid.replaceChildren();
  // Cards rise in only when the filter or library changes, not on every re-render.
  grid.classList.toggle("animate", Boolean(state.animateGallery)); state.animateGallery = false;
  shown.forEach((c, index) => {
    const card = document.createElement("article");
    card.className = "candidate" + (state.selected?.id === c.id ? " selected" : "");
    card.style.setProperty("--i", Math.min(index, 12));
    const surface = document.createElement("button"); surface.type = "button"; surface.className = "candidate-surface"; surface.setAttribute("aria-label", `看大图：${displayTitle(c)} ${c.timecode}`);
    surface.id = `candidate-surface-${c.id}`; surface.addEventListener("click", () => openViewer(index));
    const wrap = document.createElement("div"); wrap.className = "candidate-image";
    const image = document.createElement("img"); image.src = c.thumbnail || c.preview; image.alt = displayTitle(c); image.loading = "lazy";
    const time = document.createElement("span"); time.className = "candidate-time"; time.textContent = shortTime(c);
    const titleRow = document.createElement("div"); titleRow.className = "candidate-title-row";
    const text = document.createElement("div"); text.style.cssText = "flex:1;min-width:0";
    const title = document.createElement("h3"); title.className = "candidate-title";
    const titleButton = document.createElement("button"); titleButton.type = "button"; titleButton.className = "candidate-title-select"; titleButton.id = `candidate-title-${c.id}`; titleButton.textContent = displayTitle(c); titleButton.addEventListener("click", () => openViewer(index)); title.append(titleButton);
    const meta = document.createElement("p"); meta.className = "candidate-meta"; meta.textContent = `${c.source_label} · ${shortTime(c)}`;
    const favorite = document.createElement("button"); favorite.type = "button"; favorite.className = "favorite-toggle" + (c.favorite ? " is-favorite" : ""); favorite.setAttribute("aria-label", c.favorite ? "取消收藏" : "收藏画面"); favorite.setAttribute("aria-pressed", String(Boolean(c.favorite))); favorite.title = c.favorite ? "取消收藏" : "收藏画面"; favorite.disabled = state.favoriteSaving.has(c.id);
    favorite.id = `candidate-favorite-${c.id}`;
    if (state.popFavorite === c.id) favorite.classList.add("pop");
    const heart = document.createElementNS("http://www.w3.org/2000/svg", "svg"); heart.setAttribute("viewBox", "0 0 24 24"); heart.setAttribute("aria-hidden", "true"); heart.classList.add("favorite-icon");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path"); path.setAttribute("d", "M12 20.2 4.8 13A4.8 4.8 0 0 1 11.6 6.2L12 6.7l.4-.5A4.8 4.8 0 0 1 19.2 13L12 20.2Z"); heart.append(path); favorite.append(heart);
    favorite.addEventListener("click", () => toggleFavorite(c));
    wrap.append(image, time); surface.append(wrap); text.append(title, meta); titleRow.append(text, favorite); card.append(surface, titleRow); grid.append(card);
  });
  const remaining = visible.length - shown.length;
  $("gallery-more").hidden = remaining <= 0;
  $("gallery-more").textContent = `再显示 ${Math.min(PAGE_SIZE, remaining)} 张（还有 ${remaining} 张）`;
  const summary = $("gallery-summary"); summary.replaceChildren();
  const lead = document.createElement("span"); lead.append("正在看 ");
  const strong = document.createElement("strong"), count = document.createElement("span"); count.className = "count";
  strong.append(`${sourceText()} · ${SCOPE_NAMES[state.filter]} `, count, " 张"); lead.append(strong);
  rollNumber(count, state.shownCount ?? visible.length, visible.length); state.shownCount = visible.length;
  if (galleryScope.kind) lead.append(`（${SCOPE_NAMES[state.filter]}共 ${scopePool().length} 张）`);
  summary.append(lead);
  if (galleryScope.kind) {
    const chip = document.createElement("span"); chip.className = "scope-chip"; chip.textContent = galleryScope.kind === "user" ? "我截的" : galleryScope.kind === "live" ? "Live" : "MV";
    if (galleryScope.value) chip.append(` · ${galleryScope.value}`);
    const clear = document.createElement("button"); clear.type = "button"; clear.textContent = "×"; clear.setAttribute("aria-label", "清除来源筛选"); clear.addEventListener("click", () => setSourceKind(null));
    chip.append(clear); summary.append(chip);
  }
  $("gallery-empty").hidden = visible.length > 0;
  $("gallery-empty").textContent = state.filter === "favorites" ? "这个条件下还没有收藏。换个来源，或在看图模式里按 F 收藏。" : state.candidates.length ? (state.filter === "selected" ? "这个条件下还没有精选。切到「全部候选」，从视频里挑一张。" : "这个条件下还没有候选画面。换个来源，或让主控补充选片后刷新。") : "还没有视频候选。请让主控从本地 Live 或 MV 提取选片，完成后点击刷新。";
  for (const [id, active] of [["selected-filter", state.filter === "selected"], ["all-filter", state.filter === "all"], ["favorite-filter", state.filter === "favorites"], ["all-kind-filter", !galleryScope.kind], ["live-filter", galleryScope.kind === "live"], ["mv-filter", galleryScope.kind === "mv"], ["user-filter", galleryScope.kind === "user"]]) $(id).setAttribute("aria-pressed", String(active));
  $("live-source-filter").hidden = galleryScope.kind !== "live";
  $("mv-source-filter").hidden = galleryScope.kind !== "mv";
  renderWorkspaceNav();
  if (focusedId) {
    const target = $(focusedId) || grid.querySelector("button") || $("selected-filter");
    if (!target.disabled) target.focus({preventScroll: true});
  }
}
async function toggleFavorite(candidate) {
  if (state.favoriteSaving.has(candidate.id)) return;
  const focusedId = document.activeElement.id === `candidate-favorite-${candidate.id}` ? document.activeElement.id : null;
  const previous = Boolean(candidate.favorite); candidate.favorite = !previous; state.favoriteSaving.add(candidate.id);
  if (candidate.favorite) { state.popFavorite = candidate.id; setTimeout(() => { if (state.popFavorite === candidate.id) state.popFavorite = null; }, 450); }
  try {
    renderGallery(); renderViewerFavorite();
    const response = await fetch("/api/favorites", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({candidate_id: candidate.id, favorite: candidate.favorite})});
    const result = await response.json(); if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "收藏暂时无法保存。"); candidate.favorite = result.favorite;
  } catch (error) {candidate.favorite = previous; status(error.message, true); if (!$("viewer").hidden) $("viewer-quality").textContent = error.message;}
  finally {state.favoriteSaving.delete(candidate.id); try {renderGallery(); renderViewerFavorite(); if (focusedId && document.activeElement === document.body) {const target = $(focusedId) || $("candidate-grid").querySelector(".favorite-toggle") || $("favorite-filter"); if (!target.disabled) target.focus({preventScroll: true});}} catch (renderError) {status(renderError.message, true);}}
}
// Full-screen viewer over the current gallery list.
function viewerCandidate() { return state.viewerList?.[state.viewerIndex]; }
function renderViewerFavorite() {
  const candidate = viewerCandidate();
  if ($("viewer").hidden || !candidate) return;
  const button = $("viewer-favorite");
  button.firstChild.textContent = candidate.favorite ? "♥ 已收藏" : "♡ 收藏";
  button.classList.toggle("is-favorite", Boolean(candidate.favorite));
  button.setAttribute("aria-pressed", String(Boolean(candidate.favorite)));
  button.disabled = state.favoriteSaving.has(candidate.id);
}
function renderViewerStrip() {
  const strip = $("viewer-strip"), list = state.viewerList;
  const start = Math.max(0, state.viewerIndex - 30), end = Math.min(list.length, state.viewerIndex + 31);
  strip.replaceChildren();
  for (let index = start; index < end; index++) {
    const button = document.createElement("button"); button.type = "button"; button.className = index === state.viewerIndex ? "current" : "";
    button.setAttribute("aria-label", `第 ${index + 1} 张：${displayTitle(list[index])}`);
    const image = document.createElement("img"); image.src = list[index].thumbnail || list[index].preview; image.alt = ""; image.loading = "lazy";
    button.append(image); button.addEventListener("click", () => showViewer(index)); strip.append(button);
  }
  strip.querySelector(".current")?.scrollIntoView({block: "nearest", inline: "center"});
}
function showViewer(index) {
  const list = state.viewerList;
  if (!list?.length) return;
  disarmViewerEdge();
  state.viewerIndex = (index + list.length) % list.length;
  const serial = ++state.viewerSerial, candidate = list[state.viewerIndex];
  const cached = state.native.get(candidate.id), image = $("viewer-image"), quality = $("viewer-quality"), download = $("viewer-download");
  image.src = cached?.preview || candidate.thumbnail || candidate.preview;
  image.alt = `${displayTitle(candidate)}，${candidate.source_label}，${candidate.timecode}`;
  $("viewer-title").textContent = displayTitle(candidate);
  $("viewer-source").textContent = `${candidate.source_label} · ${candidate.timecode}`;
  $("viewer-note").textContent = candidate.note; $("viewer-note").title = candidate.note;
  $("viewer-position").replaceChildren(`${state.viewerScopeText} · `, Object.assign(document.createElement("strong"), {textContent: `第 ${state.viewerIndex + 1} 张`}));
  download.hidden = !cached; if (cached) download.href = cached.download;
  quality.classList.toggle("native", Boolean(cached));
  quality.textContent = cached ? `原帧 ${cached.width} × ${cached.height}` : candidate.source_available ? "缩略图 · 正在载入原帧…" : "源视频不可用 · 缩略图";
  $("viewer-use").disabled = !candidate.source_available;
  renderViewerFavorite(); renderViewerStrip();
  const current = () => serial === state.viewerSerial && !$("viewer").hidden;
  if (!candidate.source_available) { stopLoader("viewer"); return; }
  if (cached) stopLoader("viewer"); else startLoader("viewer", $("viewer"), $("viewer-load"), quality, candidate.id, "缩略图 · ");
  nativeFor(candidate).then(async job => {
    while (!job && current()) job = await nativeFor(candidate);
    if (!job || !current()) return;
    stopLoader("viewer", !cached);
    image.src = job.preview; download.href = job.download; download.hidden = false;
    quality.classList.add("native"); quality.textContent = `原帧 ${job.width} × ${job.height}`;
    prefetchAfter(list, state.viewerIndex, current);
  }).catch(error => { if (current()) { stopLoader("viewer"); quality.textContent = `${error.message} 当前为缩略图。`; } });
}
// At either end, the first press only warns; a second press within 3.5s wraps.
const viewerEdge = {armed: 0, timer: null, toastTimer: null};
function disarmViewerEdge() {
  viewerEdge.armed = 0; clearTimeout(viewerEdge.timer);
  for (const [id, text] of [["viewer-prev", "‹"], ["viewer-next", "›"]]) { $(id).classList.remove("wrap"); $(id).textContent = text; }
}
function viewerToast(html, counting) {
  const toast = $("viewer-edge");
  $("viewer-edge-text").innerHTML = html;
  toast.classList.remove("show", "counting"); void toast.offsetWidth; toast.classList.add("show");
  toast.classList.toggle("counting", counting);
  clearTimeout(viewerEdge.toastTimer); viewerEdge.toastTimer = setTimeout(() => toast.classList.remove("show", "counting"), counting ? 3500 : 1600);
}
function stepViewer(direction) {
  const length = state.viewerList?.length || 0;
  if (!length) return;
  const atEdge = direction > 0 ? state.viewerIndex === length - 1 : state.viewerIndex === 0;
  if (!atEdge) { showViewer(state.viewerIndex + direction); return; }
  if (viewerEdge.armed === direction) {
    showViewer(state.viewerIndex + direction);
    viewerToast(direction > 0 ? "回到第 1 张" : `跳到最后一张 · 第 ${length} 张`, false);
    return;
  }
  disarmViewerEdge(); viewerEdge.armed = direction;
  const image = $("viewer-image");
  image.classList.remove("bump-next", "bump-prev"); void image.offsetWidth; image.classList.add(direction > 0 ? "bump-next" : "bump-prev");
  const arrow = $(direction > 0 ? "viewer-next" : "viewer-prev"); arrow.classList.add("wrap"); arrow.textContent = "↺";
  viewerToast(direction > 0 ? "已是最后一张 · 再按 <kbd>→</kbd> 回到第 1 张" : "已是第一张 · 再按 <kbd>←</kbd> 跳到最后一张", true);
  viewerEdge.timer = setTimeout(disarmViewerEdge, 3500);
}
function openViewer(index, list = state.visible) {
  stopCapture();
  clearTimeout(state.previewTimer);
  state.previewSerial += 1;
  state.viewerList = list; state.viewerScopeText = scopeText(); state.viewerReturn = document.activeElement;
  $("viewer").hidden = false; $("viewer").classList.remove("pure"); document.body.classList.add("viewer-open");
  showViewer(index); $("viewer").focus({preventScroll: true});
}
function closeViewer(revealCurrent = true) {
  const candidate = viewerCandidate();
  $("viewer").hidden = true; document.body.classList.remove("viewer-open"); state.viewerSerial += 1;
  disarmViewerEdge(); $("viewer-edge").classList.remove("show", "counting"); stopLoader("viewer");
  if (revealCurrent && candidate) revealInGallery(candidate);
  if (revealCurrent && state.selected?.source_available && !nativeReady()) loadNativePreview(state.selected, state.previewSerial);
}
function useViewerCandidate() {
  const candidate = viewerCandidate();
  if (!candidate?.source_available) return;
  closeViewer(false); choose(candidate);
  document.querySelector(".workspace").scrollIntoView({block: "start"});
  $("export-button").focus({preventScroll: true});
}
function viewerKeydown(event) {
  if ($("viewer").hidden || event.altKey || event.ctrlKey || event.metaKey) return;
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  const actions = {ArrowLeft: () => stepViewer(-1), ArrowRight: () => stepViewer(1), Escape: () => closeViewer(), Enter: useViewerCandidate, f: () => { const c = viewerCandidate(); if (c) toggleFavorite(c); }, h: () => $("viewer").classList.toggle("pure"), " ": () => $("viewer").classList.toggle("pure")};
  if (!actions[key]) return;
  // Space always toggles the picture-only view, even when a viewer button has focus.
  // Enter on a focused control keeps its own meaning; on the picture it picks the frame.
  if (key === "Enter" && document.activeElement !== $("viewer") && $("viewer").contains(document.activeElement)) return;
  event.preventDefault(); actions[key]();
}
function renderAvatars() {
  $("avatar-grid").replaceChildren(); $("avatar-empty").hidden = state.avatars.length > 0;
  const previousAvatar = $("avatar-selection").value;
  $("avatar-selection").replaceChildren();
  for (const avatar of state.avatars) {
    const option = document.createElement("option"); option.value = avatar.id; option.textContent = avatar.title; $("avatar-selection").append(option);
    const card = document.createElement("article"); card.className = "avatar-card";
    const image = document.createElement("img"); image.src = avatar.preview; image.alt = avatar.title; image.loading = "lazy";
    const title = document.createElement("h3"); title.textContent = avatar.title;
    const link = document.createElement("a"); link.href = avatar.download; link.download = ""; link.textContent = "下载原图 ↓";
    const detail = document.createElement("p"); detail.className = "avatar-detail"; detail.textContent = `${avatar.width} × ${avatar.height} 原图 · ${avatar.direction}`;
    const reference = document.createElement("a"); reference.href = avatar.reference_url; reference.target = "_blank"; reference.rel = "noopener noreferrer"; reference.textContent = "查看官方参考 ↗"; reference.hidden = !avatar.reference_url;
    const select = document.createElement("button"); select.className = "text-button"; select.textContent = "选这张做壁纸 / 头像"; select.addEventListener("click", () => {$("avatar-selection").value = avatar.id; avatarExportChanged();});
    const actions = document.createElement("div"); actions.className = "avatar-card-actions"; actions.append(link, reference, select);
    card.append(image, title, detail, actions); $("avatar-grid").append(card);
  }
  if (state.avatars.some(a => a.id === previousAvatar)) $("avatar-selection").value = previousAvatar;
  avatarExportChanged();
}
function avatarExportChanged() {
  const avatar = state.avatars.find(a => a.id === $("avatar-selection").value);
  const size = $("avatar-export-size").value;
  const target = size === "original" ? [avatar?.width || 1, avatar?.height || 1] : size.includes("x") ? size.split("x").map(Number) : [Number(size), Number(size)];
  const fx = Number($("artwork-focus-x").value);
  const fy = Number($("artwork-focus-y").value);
  $("export-avatar").disabled = !avatar || state.avatarExporting;
  $("avatar-fit").disabled = $("artwork-focus-x").disabled = $("artwork-focus-y").disabled = size === "original";
  $("avatar-download").hidden = true;
  $("artwork-preview").hidden = !avatar;
  if (avatar) {
    const preview = $("artwork-preview"); preview.src = avatar.preview;
    preview.style.aspectRatio = `${target[0]}/${target[1]}`;
    preview.style.width = `${Math.min(268, 260 * target[0] / target[1])}px`;
    preview.style.objectFit = size === "original" ? "contain" : $("avatar-fit").value;
    preview.style.objectPosition = `${fx * 100}% ${fy * 100}%`;
    const ratios = [target[0] / avatar.width, target[1] / avatar.height];
    const scale = $("avatar-fit").value === "cover" ? Math.max(...ratios) : Math.min(...ratios);
    const scaleNote = size !== "original" && scale > 1 ? `这组尺寸需要放大 ${scale.toFixed(2)} 倍，原图细节不会增加。` : "按原图比例取景，不拉伸。";
    $("avatar-export-status").textContent = `原图 ${avatar.width} × ${avatar.height}。${scaleNote}`;
  } else $("avatar-export-status").textContent = "先生成一张插画。";
  $("artwork-focus-x-value").textContent = `${Math.round(fx * 100)}%`;
  $("artwork-focus-y-value").textContent = `${Math.round(fy * 100)}%`;
  avatarPrompt();
}
async function exportAvatar() {
  if (state.avatarExporting) return;
  state.avatarExporting = true; $("export-avatar").disabled = true; $("avatar-export-status").textContent = "正在准备头像文件…";
  const selectedSize = $("avatar-export-size").value;
  try {
    const exportSize = selectedSize === "original" || selectedSize.includes("x") ? selectedSize : Number(selectedSize);
    const response = await fetch("/api/avatar-exports", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({avatar_id: $("avatar-selection").value, size: exportSize, fit: $("avatar-fit").value, focus_x: Number($("artwork-focus-x").value), focus_y: Number($("artwork-focus-y").value)})});
    const result = await response.json(); if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "插画导出失败，请重试。");
    $("avatar-download").href = result.download; $("avatar-download").hidden = false;
    $("avatar-export-status").textContent = `已准备 ${result.width} × ${result.height}${result.upscaled ? `（由原图放大 ${result.scale.toFixed(2)} 倍）` : ""}，点击下方下载。`;
  } catch (error) {$("avatar-export-status").textContent = error.message;}
  finally {state.avatarExporting = false; $("export-avatar").disabled = !state.avatars.length;}
}
async function loadLibrary(options = {}) {
  $("refresh-library").disabled = true;
  try {
    const response = await fetch("/api/library"); if (!response.ok) throw new Error("视频画廊暂时无法打开，请确认本地服务仍在运行。");
    const data = await response.json(); state.candidates = data.candidates; state.avatars = data.avatars;
    if (!state.candidates.some(c => c.selected) && state.filter === "selected") state.filter = "all";
    state.animateGallery = true; renderSourceFilters(); renderGallery();
    const selected = state.candidates.find(c => c.id === state.selected?.id) || state.candidates[0];
    if (selected) { if (options.chooseFrame !== false) choose(selected); }
    else {state.selected = null; $("main-preview").hidden = true; $("preview-empty").hidden = false; $("preview-empty").textContent = "等待第一组视频选片"; $("export-button").disabled = true;}
    renderAvatars();
  } catch (error) { status(error.message, true); $("preview-empty").textContent = "画廊暂时无法打开"; }
  finally { $("refresh-library").disabled = false; }
}
// Recent export times on this machine, kept per browser only to phrase an estimate.
function exportHistory() { try { const list = JSON.parse(localStorage.getItem("yorushika-export-ms") || "[]"); return Array.isArray(list) ? list.filter(Number.isFinite) : []; } catch (_) { return []; } }
function recordExport(ms) { try { localStorage.setItem("yorushika-export-ms", JSON.stringify([...exportHistory(), Math.round(ms)].slice(-5))); } catch (_) { /* Storage may be unavailable. */ } }
function exportEstimate() {
  const list = exportHistory();
  if (list.length < 2) return "";
  const low = Math.max(1, Math.floor(Math.min(...list) / 1000)), high = Math.ceil(Math.max(...list) / 1000);
  return low === high ? `你这台电脑通常约 ${low} 秒` : `你这台电脑通常 ${low}–${high} 秒`;
}
async function exportImage() {
  if (!state.selected || state.exporting) return;
  const p = params();
  if (!validSize(p)) { status("尺寸需为 256–8192 的整数，且总像素不超过 3400 万。", true); return; }
  state.exporting = true; $("export-button").disabled = true; $("export-result").hidden = true;
  const started = performance.now(), typical = exportEstimate();
  const steps = [...$("export-steps").children];
  const markStep = index => steps.forEach((step, i) => { step.classList.toggle("on", i < index); step.classList.toggle("now", i === index); });
  const elapsed = () => ((performance.now() - started) / 1000).toFixed(1);
  $("export-steps").hidden = false; markStep(0); $("export-button").classList.add("busy");
  const ticker = setInterval(() => status(`已用 ${elapsed()} 秒${typical ? ` · ${typical}` : ""}`), 100);
  try {
    const response = await fetch("/api/exports", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(p)});
    const result = await response.json(); if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "导出参数有误，请检查尺寸与调整项。");
    const deadline = Date.now() + 210000;
    while (Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 800));
      const poll = await fetch(`/api/jobs/${encodeURIComponent(result.id)}`); if (!poll.ok) throw new Error("导出状态暂时不可用，请重试。");
      const job = await poll.json();
      if (job.status === "failed") throw new Error(job.error);
      if (job.status === "running") markStep(1);
      if (job.status === "done") {
        markStep(2); clearInterval(ticker); recordExport(performance.now() - started);
        $("download-link").href = job.download; $("export-preview").src = job.preview;
        $("export-filename").textContent = job.filename || "";
        $("original-link").hidden = !job.original; if (job.original) $("original-link").href = job.original;
        $("export-result").hidden = false; $("download-link").click();
        markStep(3); status(`用时 ${elapsed()} 秒，已导出 ${job.width} × ${job.height} PNG 并开始下载。下方是实际成品。`); return;
      }
    }
    throw new Error("等待导出超时。请查看本地服务状态后重试。");
  } catch (error) { clearInterval(ticker); $("export-steps").hidden = true; status(error.message, true); }
  finally { clearInterval(ticker); $("export-button").classList.remove("busy"); state.exporting = false; $("export-button").disabled = !state.selected?.source_available; }
}
function avatarPrompt() {
  const direction = $("avatar-direction").value;
  const isEdit = direction === "修改当前头像";
  const selectedAvatar = state.avatars.find(a => a.id === $("avatar-selection").value);
  const editTarget = isEdit && selectedAvatar ? `当前选中作品：${selectedAvatar.title}；原图路径 output/${selectedAvatar.relative_path}，并请读取同名元数据。参考来源：${selectedAvatar.reference_url || "请查阅作品元数据"}。\n` : "";
  const format = $("avatar-shape").value;
  const wallpaper = format.includes("手机") || format.includes("电脑");
  const prompt = `请在当前 Yorushika 项目中${isEdit ? "修改我指定的现有头像或插画" : "制作一张头像或插画壁纸"}。\n方向：${direction}。规格：${format}。\n${editTarget}${$("avatar-notes").value.trim() ? `具体要求：${$("avatar-notes").value.trim()}\n` : ""}请使用 GPT agent 的内置 image_gen 图片生成或编辑工具，遵循项目的 Yorushika 素材与风格 Skill。先查阅已确认的官方参考；不要把未确认的角色或原创吉祥物写成官方设定。希望自然、克制、手绘纸感，线条有轻微不规则，少色、留白，避免光滑塑料质感和泛滥装饰。${wallpaper ? "从一开始按目标横竖构图制作，不给手机壁纸加黑边或白边；若生成工具实际尺寸不同，记录真实原图像素，尺寸适配必须等比裁切且说明放大倍率。" : "主体适合圆形头像裁切。"}${isEdit ? "先查看目标原图，保留我没有要求改变的部分；若无法确定要改哪张，请问我。" : "本轮先给一个方向完整成品，不做多格拼图。"}\n将成品保存到当前项目 output/${wallpaper ? "illustrations" : "avatars"}/ 下，说明参考来源与原创部分、实际像素和适用用途，方便我在本地工作室刷新查看。`;
  $("avatar-prompt").value = prompt; return prompt;
}
for (const id of ["size-preset", "width", "height"]) $(id).addEventListener("input", sizeChanged);
for (const id of ["fit", "focus-x", "focus-y", "exposure", "contrast", "sharpen"]) $(id).addEventListener("input", updatePreview);
$("desktop-preset").addEventListener("click", () => {$("size-preset").value = "3456x2234"; sizeChanged();});
$("phone-preset").addEventListener("click", () => {$("size-preset").value = "1284x2778"; sizeChanged();});
$("reset-settings").addEventListener("click", () => {for (const [id, value] of Object.entries(defaultSettings)) if (id !== "size-preset") $(id).value = value; sizeChanged();});
$("selected-filter").addEventListener("click", () => setScope("selected"));
$("all-filter").addEventListener("click", () => setScope("all"));
$("favorite-filter").addEventListener("click", () => setScope("favorites"));
$("all-kind-filter").addEventListener("click", () => setSourceKind(null));
for (const kind of ["live", "mv"]) $(`${kind}-filter`).addEventListener("click", () => setSourceKind(kind));
for (const kind of ["live", "mv"]) $(`${kind}-source-filter`).addEventListener("change", () => {if (galleryScope.kind !== kind) return; galleryScope.value = $(`${kind}-source-filter`).value; state.renderLimit = PAGE_SIZE; state.animateGallery = true; renderGallery();});
$("gallery-more").addEventListener("click", () => {state.renderLimit += PAGE_SIZE; renderGallery();});
// Capture requests share the server's decoder and cache. A generation prevents
// late prefetch requests from reviving work belonging to an earlier time.
function parseCaptureTime(text) {
  const value = text.trim().replace(/：/g, ":");
  if (!/^\d+(?::\d{1,2}){0,2}(?:\.\d)?$/.test(value)) return null;
  const parts = value.split(":").map(Number);
  if (parts.slice(1).some(part => part >= 60)) return null;
  const seconds = parts.reduce((total, part) => total * 60 + part, 0);
  return Number.isFinite(seconds) && seconds <= Number.MAX_SAFE_INTEGER / 10 ? Math.round(seconds * 10) / 10 : null;
}
function captureTime(seconds) {
  const ticks = Math.round(seconds * 10), hours = Math.floor(ticks / 36000), minutes = Math.floor(ticks % 36000 / 600);
  const tail = String(Math.floor(ticks % 600 / 10)).padStart(2, "0") + (ticks % 10 ? `.${ticks % 10}` : "");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${tail}` : `${minutes}:${tail}`;
}
const capture = {sources: [], source: null, seconds: 0, serial: 0, timer: null, ready: false, saving: false, step: 2, reel: null, touchedAt: 0, scrollLeft: 0};
function stopCapture() { capture.serial++; clearTimeout(capture.timer); }
function captureMessage(message, bad = false) {
  $("cap-chapter-line").textContent = message;
  $("cap-chapter-line").classList.toggle("bad", bad); $("cap-time").classList.toggle("bad", bad);
}
function captureChapter(seconds) { return capture.source?.chapters.find(c => c.start <= seconds && seconds < c.end); }
function captureFeedback(seconds) {
  const chapter = captureChapter(seconds);
  captureMessage(chapter ? `落在第 ${capture.source.chapters.indexOf(chapter) + 1} 章 ${chapter.title} · 章节内 ${captureTime(seconds - chapter.start)}` : "这段视频没有对应章节，按整场时间截取");
}
async function captureJSON(url, body) {
  const response = await fetch(url, body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "截帧参数有误，请检查时间。");
  return data;
}
async function capturePoll(job, serial) {
  const deadline = Date.now() + 210000;
  while (serial === capture.serial && Date.now() < deadline) {
    if (job.status === "done") return job;
    if (job.status === "cancelled") return null;
    if (job.status === "failed") throw new Error(job.error || "原帧载入失败，请重试。");
    await new Promise(resolve => setTimeout(resolve, 550));
    if (serial !== capture.serial) return null;
    job = await captureJSON(`/api/preview-jobs/${encodeURIComponent(job.id)}`);
  }
  if (serial === capture.serial) throw new Error("原帧载入超时，请重试。");
  return null;
}
async function capturePicture(image, url) {
  image.src = url;
  await image.decode();
}
// The reel is for rough picking on a fixed grid (step seconds). It grows by
// nine tiles at a time towards either side; the playhead marks the exact time.
function reelRange(lo, hi, step) {
  const times = [];
  for (let k = 0; lo + k * step <= hi + 1e-6; k++) times.push(Math.round((lo + k * step) * 10) / 10);
  return times;
}
function reelTop() { return Math.max(0, Math.floor((capture.source.duration - .05) / capture.reel.step) * capture.reel.step); }
function reelCovers(seconds) {
  const reel = capture.reel;
  return reel && reel.sourceId === capture.source?.id && reel.step === capture.step && seconds >= reel.lo - reel.step && seconds <= reel.hi + reel.step;
}
function reelTile(t) {
  const button = document.createElement("button"), image = document.createElement("img"), label = document.createElement("span");
  button.type = "button"; button.className = "tile"; button.dataset.t = t; button.setAttribute("aria-label", `切换到 ${captureTime(t)}`);
  image.alt = ""; label.textContent = captureTime(t); button.append(image, label);
  const chapter = capture.source.chapters.find(c => t <= c.start && c.start < t + capture.reel.step);
  if (chapter) button.dataset.chapter = `${capture.source.chapters.indexOf(chapter) + 1} ${chapter.title}`;
  button.addEventListener("click", () => captureGo(t));
  capture.reel.tiles.set(t, button);
  return button;
}
function reelLabel(message, bad = false) {
  const reel = capture.reel;
  $("cap-loaded").textContent = message || (reel ? `已载入 ${captureTime(reel.lo)} – ${captureTime(reel.hi)} · ${reel.tiles.size} 张` : "");
  $("cap-loaded").classList.toggle("busy", Boolean(message) && !bad); $("cap-loaded").classList.toggle("bad", bad);
}
function reelEdges() {
  const reel = capture.reel;
  $("cap-strip").querySelectorAll(".edge").forEach(edge => edge.remove());
  for (const [side, at] of [["l", reel.lo <= 0], ["r", reel.hi >= reelTop()]]) {
    if (!at) continue;
    const edge = document.createElement("div"); edge.className = "tile edge"; edge.textContent = side === "l" ? "开头" : "结尾";
    side === "l" ? $("cap-playhead").after(edge) : $("cap-strip").append(edge);
  }
}
function standIn(url) {
  if (capture.ready || !url) return;
  $("cap-big").src = url; $("cap-big").hidden = false; $("cap-missing").hidden = true;
}
async function reelLoad(times, side) {
  const reel = capture.reel;
  if (side) reel.busy[side] = true;
  reelLabel(`正在补 ${captureTime(times[0])} – ${captureTime(times[times.length - 1])}…`);
  try {
    const data = await captureJSON("/api/capture-strip", {source_id: reel.sourceId, start: times[0], step: reel.step, count: times.length});
    if (reel !== capture.reel) return;
    for (const {timestamp, url} of data.frames) {
      const tile = reel.tiles.get(timestamp);
      if (!tile) continue;
      if (!url) { tile.classList.add("ready", "missing"); continue; }
      const image = tile.querySelector("img"); image.src = url;
      image.decode().then(() => tile.classList.add("ready"), () => tile.classList.add("ready", "missing"));
      if (timestamp === capture.seconds) standIn(url);
    }
    reelLabel();
  } catch (error) {
    if (reel !== capture.reel) return;
    times.forEach(t => reel.tiles.get(t)?.classList.add("ready", "missing"));
    reelLabel(error.message, true);
  } finally { if (side) reel.busy[side] = false; }
}
function reelBuild(center) {
  const step = capture.step;
  capture.reel = {sourceId: capture.source.id, step, lo: 0, hi: 0, tiles: new Map(), busy: {l: false, r: false}};
  const top = reelTop(), grid = Math.min(top, Math.max(0, Math.round(center / step) * step));
  Object.assign(capture.reel, {lo: Math.max(0, grid - 4 * step), hi: Math.min(top, grid + 4 * step)});
  const times = reelRange(capture.reel.lo, capture.reel.hi, step);
  $("cap-strip").replaceChildren($("cap-playhead"), ...times.map(reelTile));
  reelEdges(); reelPlace(true); reelLoad(times);
}
function reelExtend(side) {
  const reel = capture.reel;
  if (!reel || reel.busy[side] || (side === "r" ? reel.hi >= reelTop() : reel.lo <= 0)) return;
  const times = side === "r" ? reelRange(reel.hi + reel.step, Math.min(reelTop(), reel.hi + 9 * reel.step), reel.step)
    : reelRange(Math.max(0, reel.lo - 9 * reel.step), reel.lo - reel.step, reel.step);
  const strip = $("cap-strip"), before = strip.scrollWidth, tiles = times.map(reelTile);
  if (side === "r") { strip.append(...tiles); reel.hi = times[times.length - 1]; }
  else {
    // Keep the visible tiles still while new ones appear on the left.
    strip.style.scrollBehavior = "auto"; $("cap-playhead").after(...tiles);
    strip.scrollLeft += strip.scrollWidth - before; strip.style.scrollBehavior = ""; reel.lo = times[0];
  }
  reelEdges(); reelPlace(false); reelLoad(times, side);
}
function reelPlace(scroll) {
  const reel = capture.reel, strip = $("cap-strip");
  if (!reel?.tiles.size) return;
  let near = null;
  for (const tile of reel.tiles.values()) if (!near || Math.abs(tile.dataset.t - capture.seconds) < Math.abs(near.dataset.t - capture.seconds)) near = tile;
  reel.tiles.forEach(tile => tile.classList.toggle("on", tile === near));
  const offset = (capture.seconds - near.dataset.t) / reel.step * (near.offsetWidth + 5);
  $("cap-playhead").hidden = capture.seconds < reel.lo - reel.step / 2 || capture.seconds > reel.hi + reel.step / 2;
  $("cap-playhead").style.left = `${near.offsetLeft + near.offsetWidth / 2 + offset - 1}px`;
  if (scroll) {
    capture.touchedAt = 0;
    strip.scrollTo({left: near.offsetLeft - strip.clientWidth / 2 + near.offsetWidth / 2});
  }
}
function captureKeyToast(text) {
  $("cap-key").textContent = text; $("cap-key").classList.add("show");
  clearTimeout(captureKeyToast.timer); captureKeyToast.timer = setTimeout(() => $("cap-key").classList.remove("show"), 900);
}
async function runCapture(serial) {
  if (serial !== capture.serial || !capture.source?.available) return;
  const seconds = capture.seconds, source = capture.source;
  state.previewSerial++; clearTimeout(state.previewTimer);
  capture.ready = false; $("cap-add").disabled = true;
  $("cap-quality").textContent = "正在载入原帧"; $("cap-missing").hidden = false;
  $("cap-big").classList.add("loading"); $("cap-prog").classList.add("run");
  const timeLabel = document.createElement("strong"); timeLabel.textContent = captureTime(seconds);
  $("cap-big-meta").replaceChildren(timeLabel, document.createTextNode(` · ${source.label}${captureChapter(seconds) ? ` · ${captureChapter(seconds).title}` : ""}`));
  if (reelCovers(seconds)) reelPlace(true); else reelBuild(seconds);
  const tile = capture.reel.tiles.get(seconds);
  if (tile?.classList.contains("ready") && !tile.classList.contains("missing")) standIn(tile.querySelector("img").src);
  else if (!$("cap-big").hidden) $("cap-missing").hidden = true;
  try {
    const current = await captureJSON("/api/capture-previews", {source_id: source.id, timestamp: seconds, priority: "current"});
    if (serial !== capture.serial || current.status === "cancelled") return;
    const job = await capturePoll(current, serial);
    if (!job || serial !== capture.serial) return;
    // Decode off-screen: the blurred stand-in may still own cap-big.
    await capturePicture(new Image(), job.original);
    if (serial !== capture.serial) return;
    capture.ready = true; $("cap-big").src = job.original;
    $("cap-big").hidden = false; $("cap-big").classList.remove("loading"); $("cap-missing").hidden = true;
    $("cap-quality").textContent = `原帧 ${job.width} × ${job.height}`; $("cap-prog").classList.remove("run");
    $("cap-add").disabled = capture.saving;
  } catch (error) {
    if (serial === capture.serial) { captureMessage(error.message, true); $("cap-prog").classList.remove("run"); }
  }
}
function captureInput() {
  stopCapture(); capture.ready = false; $("cap-add").disabled = true; $("cap-added").classList.remove("show");
  const seconds = parseCaptureTime($("cap-time").value), offset = Number($("cap-chap").value || 0);
  if (seconds === null) { captureMessage("看不懂这个时间，试试 35:08 或 2108（最多一位小数）", true); return; }
  if (!capture.source?.available) { captureMessage("请选择可用的视频", true); return; }
  const absolute = Math.round((seconds + offset) * 10) / 10;
  if (absolute >= capture.source.duration) { captureMessage(`超出视频长度 ${captureTime(capture.source.duration)}`, true); return; }
  capture.seconds = absolute; captureFeedback(absolute);
  if (reelCovers(absolute)) reelPlace(true);
  const serial = capture.serial;
  capture.timer = setTimeout(() => runCapture(serial), 350);
}
function captureGo(seconds) {
  if (!capture.source?.available || capture.saving) return;
  $("cap-chap").value = "";
  $("cap-time").value = captureTime(Math.max(0, Math.min(Math.ceil(capture.source.duration * 10) / 10 - .1, seconds)));
  captureInput();
}
function captureSourceChanged() {
  capture.source = capture.sources.find(source => source.id === $("cap-src").value); capture.reel = null;
  $("cap-chap").replaceChildren(new Option("不指定（按整场时间）", ""), ...(capture.source?.chapters || []).map((c, i) => new Option(`${String(i + 1).padStart(2, "0")} ${c.title} · ${captureTime(c.start)}`, c.start)));
  $("cap-time").value = "0:00"; captureInput();
}
async function toggleCapture() {
  const open = $("capture").classList.toggle("open"); $("capture").inert = !open;
  $("capture-toggle").setAttribute("aria-expanded", String(open));
  if (!open) { stopCapture(); return; }
  try {
    if (!capture.sources.length) {
      const data = await captureJSON("/api/capture-sources"); capture.sources = data.sources;
      $("cap-src").replaceChildren();
      for (const [kind, label] of [["live", "Live 场次"], ["mv", "MV 曲目"]]) {
        const group = document.createElement("optgroup"); group.label = label;
        for (const source of capture.sources.filter(s => s.kind === kind)) { const option = new Option(source.label + (source.available ? "" : "（不可用）"), source.id); option.disabled = !source.available; group.append(option); }
        if (group.children.length) $("cap-src").append(group);
      }
      const initial = capture.sources.find(s => s.available && s.label === state.selected?.source_label) || capture.sources.find(s => s.available);
      if (initial) $("cap-src").value = initial.id;
      captureSourceChanged();
    } else captureInput();
  } catch (error) { captureMessage(error.message, true); }
}
async function saveCapture(use) {
  if (!capture.ready || capture.saving) return;
  capture.saving = true; $("cap-add").disabled = true;
  const source_id = capture.source.id, timestamp = capture.seconds, favorite = $("cap-fav").checked;
  stopCapture();
  try {
    const result = await captureJSON("/api/captures", {source_id, timestamp, favorite});
    await loadLibrary({chooseFrame: false}); state.filter = "all"; setSourceKind("user");
    $("cap-added").textContent = `${result.duplicate ? "已在" : "已加入"}「我截的」· ${result.capture_id}${favorite ? "，也放进了收藏" : ""}`;
    $("cap-added").classList.add("show");
    if (use) {
      const candidate = state.candidates.find(c => c.id === result.candidate_id);
      if (!candidate) throw new Error("图库刷新失败，请刷新后打开这一帧。");
      choose(candidate); document.querySelector(".workspace").scrollIntoView({block: "start", behavior: "smooth"});
    }
  } catch (error) { captureMessage(error.message, true); }
  finally { capture.saving = false; $("cap-add").disabled = !capture.ready; }
}
$("capture-toggle").addEventListener("click", toggleCapture);
$("cap-src").addEventListener("change", captureSourceChanged);
$("cap-time").addEventListener("input", captureInput);
$("cap-chap").addEventListener("change", () => { $("cap-time").value = "0:00"; captureInput(); $("cap-time").focus(); $("cap-time").select(); });
document.querySelectorAll(".capture .nudge button").forEach(button => button.addEventListener("click", () => captureGo(capture.seconds + Number(button.dataset.d))));
// On the document: rebuilding the reel removes the focused tile, which used to
// drop focus to <body> and silence the arrows. Only while the panel is on screen.
document.addEventListener("keydown", event => {
  if (!$("capture").classList.contains("open") || !$("viewer").hidden || event.altKey || event.ctrlKey || event.metaKey) return;
  const box = $("capture").getBoundingClientRect();
  if (box.bottom < 0 || box.top > innerHeight || !capture.reel) return;
  if (event.target === $("cap-time") && ["ArrowUp", "ArrowDown"].includes(event.key)) {
    event.preventDefault(); captureGo(capture.seconds + (event.key === "ArrowUp" ? .5 : -.5)); return;
  }
  if (event.target.matches("input, select, textarea, [contenteditable]") || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  event.preventDefault();
  const step = capture.reel.step, dir = event.key === "ArrowLeft" ? -1 : 1, cell = capture.seconds / step;
  // Off-grid times first snap to the neighbouring tile in that direction.
  const next = Math.abs(cell - Math.round(cell)) > .01 ? (dir > 0 ? Math.ceil(cell) : Math.floor(cell)) * step : capture.seconds + dir * step;
  captureGo(next); captureKeyToast(`${dir > 0 ? "→" : "←"} ${step} 秒`);
  if (next >= capture.reel.hi - step) reelExtend("r");
  if (next <= capture.reel.lo + step) reelExtend("l");
});
document.querySelectorAll("#cap-steps button").forEach(button => button.addEventListener("click", () => {
  document.querySelectorAll("#cap-steps button").forEach(other => other.setAttribute("aria-pressed", String(other === button)));
  capture.step = Number(button.dataset.step);
  if (capture.source?.available && capture.reel) reelBuild(capture.seconds);
}));
// Grow only on the user's own scrolling; our centring scrolls must not load
// batches nobody asked for. A swipe at the very edge produces no scroll event.
function reelNearEdge(dir) {
  const strip = $("cap-strip");
  if (dir < 0 && strip.scrollLeft < 60) reelExtend("l");
  if (dir > 0 && strip.scrollLeft + strip.clientWidth > strip.scrollWidth - 60) reelExtend("r");
}
for (const type of ["pointerdown", "touchstart"]) $("cap-strip").addEventListener(type, () => { capture.touchedAt = Date.now(); }, {passive: true});
$("cap-strip").addEventListener("wheel", event => {
  if (!event.deltaX) return;
  capture.touchedAt = Date.now(); reelNearEdge(Math.sign(event.deltaX));
}, {passive: true});
$("cap-strip").addEventListener("scroll", () => {
  const strip = $("cap-strip"), dir = Math.sign(strip.scrollLeft - capture.scrollLeft);
  capture.scrollLeft = strip.scrollLeft;
  if (Date.now() - capture.touchedAt < 1200) reelNearEdge(dir);
}, {passive: true});
for (const side of ["l", "r"]) $(`cap-page-${side}`).addEventListener("click", () => {
  const strip = $("cap-strip"), dir = side === "l" ? -1 : 1;
  if (side === "l" ? strip.scrollLeft < 60 : strip.scrollLeft + strip.clientWidth > strip.scrollWidth - 60) reelExtend(side);
  strip.scrollBy({left: dir * strip.clientWidth * .9});
});
addEventListener("resize", () => reelPlace(false));
$("cap-add").addEventListener("click", () => saveCapture(false));
$("cap-use").addEventListener("click", event => { event.preventDefault(); saveCapture(true); });
$("user-filter").addEventListener("click", () => { if (state.filter === "selected") state.filter = "all"; setSourceKind("user"); });

$("workspace-prev").addEventListener("click", () => stepWorkspace(-1));
$("workspace-next").addEventListener("click", () => stepWorkspace(1));
$("back-to-gallery").addEventListener("click", () => {if (state.selected) revealInGallery(state.selected);});
$("viewer-prev").addEventListener("click", () => stepViewer(-1));
$("viewer-next").addEventListener("click", () => stepViewer(1));
$("viewer-close").addEventListener("click", () => closeViewer());
$("viewer-pure").addEventListener("click", () => $("viewer").classList.toggle("pure"));
$("viewer-image").addEventListener("click", () => $("viewer").classList.toggle("pure"));
// The workspace preview opens the viewer too, within the current gallery list when it holds this frame.
$("main-preview").addEventListener("click", () => {if (!state.selected) return; const index = state.visible.findIndex(c => c.id === state.selected.id); if (index >= 0) openViewer(index); else openViewer(0, [state.selected]);});
$("viewer-use").addEventListener("click", useViewerCandidate);
$("viewer-favorite").addEventListener("click", () => {const c = viewerCandidate(); if (c) toggleFavorite(c);});
document.addEventListener("keydown", viewerKeydown);
// Keep keyboard focus inside the viewer while it is open.
document.addEventListener("focusin", event => {if (!$("viewer").hidden && !$("viewer").contains(event.target)) $("viewer").focus({preventScroll: true});});
let swipeStart = null;
$("viewer-image").addEventListener("touchstart", event => {swipeStart = event.touches[0].clientX;}, {passive: true});
$("viewer-image").addEventListener("touchend", event => {if (swipeStart === null) return; const dx = event.changedTouches[0].clientX - swipeStart; swipeStart = null; if (Math.abs(dx) > 50) stepViewer(dx < 0 ? 1 : -1);});
$("refresh-library").addEventListener("click", loadLibrary);
$("export-button").addEventListener("click", exportImage);
for (const id of Object.keys(RANGE_DEFAULTS)) $(id).addEventListener("dblclick", () => resetRange(id));
for (const button of document.querySelectorAll(".reset-one")) button.addEventListener("click", () => resetRange(button.dataset.for));
$("tune-reset").addEventListener("click", () => TUNE_IDS.forEach(resetRange));
for (const type of ["wallpaper", "avatar"]) $(`${type}-tab`).addEventListener("click", () => {for (const t of ["wallpaper", "avatar"]) {$(`${t}-panel`).hidden = t !== type; $(`${t}-tab`).classList.toggle("active", t === type); $(`${t}-tab`).setAttribute("aria-pressed", String(t === type));} moveTabInk(); if (type === "wallpaper") updatePreview();});
function moveTabInk() { const tab = document.querySelector(".tabs button.active"); if (!tab) return; $("tab-ink").style.left = `${tab.offsetLeft}px`; $("tab-ink").style.width = `${tab.offsetWidth}px`; }
window.addEventListener("resize", moveTabInk); moveTabInk();
for (const id of ["avatar-direction", "avatar-shape", "avatar-notes"]) $(id).addEventListener("input", avatarPrompt);
$("copy-avatar-request").addEventListener("click", async () => {const prompt = avatarPrompt(); try {await navigator.clipboard.writeText(prompt); $("avatar-status").textContent = "已复制。粘贴到 Codex 即可生成；完成后回到这里刷新。";} catch (_) {$("avatar-prompt").closest("details").open = true; $("avatar-prompt").focus(); $("avatar-prompt").select(); $("avatar-status").textContent = "浏览器未允许自动复制，请复制下方已选中的需求。";}});
window.addEventListener("resize", updatePreview);
avatarPrompt(); updatePreview(); loadLibrary();

for (const id of ["avatar-selection", "avatar-export-size", "avatar-fit"]) $(id).addEventListener("change", avatarExportChanged);
$("avatar-export-size").addEventListener("change", () => {$("avatar-fit").value = "cover"; avatarExportChanged();});
for (const id of ["artwork-focus-x", "artwork-focus-y"]) $(id).addEventListener("input", avatarExportChanged);
$("export-avatar").addEventListener("click", exportAvatar);
