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
function setScope(filter) { state.filter = filter; state.renderLimit = PAGE_SIZE; renderGallery(); }
function setSourceKind(kind) {
  if (galleryScope.kind !== kind) { galleryScope.kind = kind; galleryScope.value = ""; }
  state.renderLimit = PAGE_SIZE;
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
function sourceText() { return !galleryScope.kind ? "全部来源" : galleryScope.value || (galleryScope.kind === "live" ? "全部 Live 场次" : "全部 MV 曲目"); }
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
    $("preview-quality").textContent = `${error.message} 当前为选片缩略图。`;
  }
}
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
  $("native-preview-link").hidden = $("native-download-link").hidden = true;
  $("review-badge").hidden = false;
  $("review-badge").textContent = candidate.reviewed ? "Agent 已看画面" : "自动候选 · 待看画面";
  $("export-button").disabled = state.exporting || !candidate.source_available;
  if (!state.exporting) status(candidate.source_available ? "完成后自动下载 PNG，并保留未处理原帧。" : "源视频已移动，暂时只能查看已有预览。", !candidate.source_available);
  $("export-result").hidden = true;
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
function renderGallery() {
  state.visible = visibleCandidates();
  const visible = state.visible, shown = visible.slice(0, state.renderLimit);
  const grid = $("candidate-grid");
  const focusedId = grid.contains(document.activeElement) ? document.activeElement.id : null;
  grid.replaceChildren();
  shown.forEach((c, index) => {
    const card = document.createElement("article");
    card.className = "candidate" + (state.selected?.id === c.id ? " selected" : "");
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
  const strong = document.createElement("strong"); strong.textContent = scopeText(); lead.append(strong);
  if (galleryScope.kind) lead.append(`（${SCOPE_NAMES[state.filter]}共 ${scopePool().length} 张）`);
  summary.append(lead);
  if (galleryScope.kind) {
    const chip = document.createElement("span"); chip.className = "scope-chip"; chip.textContent = galleryScope.kind === "live" ? "Live" : "MV";
    if (galleryScope.value) chip.append(` · ${galleryScope.value}`);
    const clear = document.createElement("button"); clear.type = "button"; clear.textContent = "×"; clear.setAttribute("aria-label", "清除来源筛选"); clear.addEventListener("click", () => setSourceKind(null));
    chip.append(clear); summary.append(chip);
  }
  $("gallery-empty").hidden = visible.length > 0;
  $("gallery-empty").textContent = state.filter === "favorites" ? "这个条件下还没有收藏。换个来源，或在看图模式里按 F 收藏。" : state.candidates.length ? (state.filter === "selected" ? "这个条件下还没有精选。切到「全部候选」，从视频里挑一张。" : "这个条件下还没有候选画面。换个来源，或让主控补充选片后刷新。") : "还没有视频候选。请让主控从本地 Live 或 MV 提取选片，完成后点击刷新。";
  for (const [id, active] of [["selected-filter", state.filter === "selected"], ["all-filter", state.filter === "all"], ["favorite-filter", state.filter === "favorites"], ["all-kind-filter", !galleryScope.kind], ["live-filter", galleryScope.kind === "live"], ["mv-filter", galleryScope.kind === "mv"]]) $(id).setAttribute("aria-pressed", String(active));
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
  if (!candidate.source_available) return;
  nativeFor(candidate).then(async job => {
    while (!job && current()) job = await nativeFor(candidate);
    if (!job || !current()) return;
    image.src = job.preview; download.href = job.download; download.hidden = false;
    quality.classList.add("native"); quality.textContent = `原帧 ${job.width} × ${job.height}`;
    prefetchAfter(list, state.viewerIndex, current);
  }).catch(error => { if (current()) quality.textContent = `${error.message} 当前为缩略图。`; });
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
  clearTimeout(state.previewTimer);
  state.previewSerial += 1;
  state.viewerList = list; state.viewerScopeText = scopeText(); state.viewerReturn = document.activeElement;
  $("viewer").hidden = false; $("viewer").classList.remove("pure"); document.body.classList.add("viewer-open");
  showViewer(index); $("viewer").focus({preventScroll: true});
}
function closeViewer(revealCurrent = true) {
  const candidate = viewerCandidate();
  $("viewer").hidden = true; document.body.classList.remove("viewer-open"); state.viewerSerial += 1;
  disarmViewerEdge(); $("viewer-edge").classList.remove("show", "counting");
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
async function loadLibrary() {
  $("refresh-library").disabled = true;
  try {
    const response = await fetch("/api/library"); if (!response.ok) throw new Error("视频画廊暂时无法打开，请确认本地服务仍在运行。");
    const data = await response.json(); state.candidates = data.candidates; state.avatars = data.avatars;
    if (!state.candidates.some(c => c.selected) && state.filter === "selected") state.filter = "all";
    renderSourceFilters(); renderGallery();
    const selected = state.candidates.find(c => c.id === state.selected?.id) || state.candidates[0];
    if (selected) choose(selected);
    else {state.selected = null; $("main-preview").hidden = true; $("preview-empty").hidden = false; $("preview-empty").textContent = "等待第一组视频选片"; $("export-button").disabled = true;}
    renderAvatars();
  } catch (error) { status(error.message, true); $("preview-empty").textContent = "画廊暂时无法打开"; }
  finally { $("refresh-library").disabled = false; }
}
async function exportImage() {
  if (!state.selected || state.exporting) return;
  const p = params();
  if (!validSize(p)) { status("尺寸需为 256–8192 的整数，且总像素不超过 3400 万。", true); return; }
  state.exporting = true; $("export-button").disabled = true; $("export-result").hidden = true; status("正在从原视频提取这一帧，通常 15 秒左右…");
  try {
    const response = await fetch("/api/exports", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(p)});
    const result = await response.json(); if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "导出参数有误，请检查尺寸与调整项。");
    const deadline = Date.now() + 210000;
    while (Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 800));
      const poll = await fetch(`/api/jobs/${encodeURIComponent(result.id)}`); if (!poll.ok) throw new Error("导出状态暂时不可用，请重试。");
      const job = await poll.json();
      if (job.status === "failed") throw new Error(job.error);
      if (job.status === "done") {
        $("download-link").href = job.download; $("export-preview").src = job.preview;
        $("export-filename").textContent = job.filename || "";
        $("original-link").hidden = !job.original; if (job.original) $("original-link").href = job.original;
        $("export-result").hidden = false; $("download-link").click();
        status(`已导出 ${job.width} × ${job.height} PNG，已开始下载。下方是实际成品。`); return;
      }
      status("正在处理原帧并保存壁纸，请稍等…");
    }
    throw new Error("等待导出超时。请查看本地服务状态后重试。");
  } catch (error) { status(error.message, true); }
  finally { state.exporting = false; $("export-button").disabled = !state.selected?.source_available; }
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
for (const kind of ["live", "mv"]) $(`${kind}-source-filter`).addEventListener("change", () => {if (galleryScope.kind !== kind) return; galleryScope.value = $(`${kind}-source-filter`).value; state.renderLimit = PAGE_SIZE; renderGallery();});
$("gallery-more").addEventListener("click", () => {state.renderLimit += PAGE_SIZE; renderGallery();});
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
for (const type of ["wallpaper", "avatar"]) $(`${type}-tab`).addEventListener("click", () => {for (const t of ["wallpaper", "avatar"]) {$(`${t}-panel`).hidden = t !== type; $(`${t}-tab`).classList.toggle("active", t === type); $(`${t}-tab`).setAttribute("aria-pressed", String(t === type));} if (type === "wallpaper") updatePreview();});
for (const id of ["avatar-direction", "avatar-shape", "avatar-notes"]) $(id).addEventListener("input", avatarPrompt);
$("copy-avatar-request").addEventListener("click", async () => {const prompt = avatarPrompt(); try {await navigator.clipboard.writeText(prompt); $("avatar-status").textContent = "已复制。粘贴到 Codex 即可生成；完成后回到这里刷新。";} catch (_) {$("avatar-prompt").closest("details").open = true; $("avatar-prompt").focus(); $("avatar-prompt").select(); $("avatar-status").textContent = "浏览器未允许自动复制，请复制下方已选中的需求。";}});
window.addEventListener("resize", updatePreview);
avatarPrompt(); updatePreview(); loadLibrary();

for (const id of ["avatar-selection", "avatar-export-size", "avatar-fit"]) $(id).addEventListener("change", avatarExportChanged);
$("avatar-export-size").addEventListener("change", () => {$("avatar-fit").value = "cover"; avatarExportChanged();});
for (const id of ["artwork-focus-x", "artwork-focus-y"]) $(id).addEventListener("input", avatarExportChanged);
$("export-avatar").addEventListener("click", exportAvatar);
