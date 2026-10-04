"use strict";
const $ = (id) => document.getElementById(id);
const state = {candidates: [], avatars: [], selected: null, filter: "selected", exporting: false, avatarExporting: false, previewSerial: 0, previewAbort: null, previewTimer: null, favoriteSaving: new Set()};
const defaultSettings = {"size-preset": "2560x1440", fit: "contain", "focus-x": ".5", "focus-y": ".5", exposure: "0", contrast: "1", sharpen: ".25"};
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
  const usableHeight = stage.clientHeight - parseFloat(stageStyle.paddingTop) - parseFloat(stageStyle.paddingBottom);
  const usableWidth = stage.clientWidth - parseFloat(stageStyle.paddingLeft) - parseFloat(stageStyle.paddingRight);
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
}
function sizeChanged() {
  const p = params();
  $("fit").value = p.height >= p.width ? "cover" : "contain";
  updatePreview();
}
async function loadNativePreview(candidate, serial, signal) {
  try {
    let response = await fetch("/api/previews", {method: "POST", signal, headers: {"Content-Type": "application/json"}, body: JSON.stringify({candidate_id: candidate.id})});
    let job = await response.json();
    if (!response.ok) throw new Error(typeof job.detail === "string" ? job.detail : "原帧暂未载入。");
    const deadline = Date.now() + 210000;
    while (job.status !== "done") {
      if (job.status === "failed") throw new Error(job.error);
      if (serial !== state.previewSerial || signal.aborted) return;
      if (Date.now() > deadline) throw new Error("原帧载入超时，可重新选择这一帧。");
      await new Promise(resolve => setTimeout(resolve, 550));
      response = await fetch(`/api/preview-jobs/${encodeURIComponent(job.id)}`, {signal});
      job = await response.json();
      if (!response.ok) throw new Error("原帧载入状态暂时不可用。");
    }
    const picture = new Image();
    picture.src = job.preview;
    await picture.decode();
    if (serial !== state.previewSerial || signal.aborted || state.selected?.id !== candidate.id) return;
    $("main-preview").src = job.preview;
    $("main-preview").dataset.quality = "native";
    $("main-preview").dataset.candidateId = candidate.id;
    $("preview-label").textContent = "视频原帧";
    $("preview-quality").textContent = `原帧 ${job.width} × ${job.height} · 未锐化`;
    $("native-preview-link").href = job.original;
    $("native-download-link").href = job.download;
    $("native-preview-link").hidden = $("native-download-link").hidden = false;
  } catch (error) {
    if (error.name === "AbortError" || serial !== state.previewSerial) return;
    $("preview-quality").textContent = `${error.message} 当前为选片缩略图。`;
  }
}
function choose(candidate) {
  state.previewSerial += 1;
  state.previewAbort?.abort();
  clearTimeout(state.previewTimer);
  state.previewAbort = new AbortController();
  const serial = state.previewSerial;
  const signal = state.previewAbort.signal;
  state.selected = candidate;
  const image = $("main-preview");
  image.src = candidate.thumbnail || candidate.preview;
  image.dataset.quality = "thumbnail";
  image.dataset.candidateId = candidate.id;
  image.alt = `${candidate.title}，${candidate.source_label}，${candidate.timecode}`;
  image.hidden = false;
  $("preview-empty").hidden = true;
  $("image-title").textContent = candidate.title;
  $("image-source").textContent = `${candidate.source_label} · ${candidate.timecode}`;
  $("image-note").textContent = candidate.note;
  $("preview-label").textContent = "选片缩略图";
  $("preview-quality").textContent = candidate.source_available ? "正在载入视频原帧…" : "源视频不可用，当前为选片缩略图。";
  $("native-preview-link").hidden = $("native-download-link").hidden = true;
  $("review-badge").hidden = false;
  $("review-badge").textContent = candidate.reviewed ? "Agent 已看画面" : "自动候选 · 待看画面";
  $("export-button").disabled = state.exporting || !candidate.source_available;
  if (!state.exporting) status(candidate.source_available ? "导出 PNG，保留原始截图。" : "源视频已移动，暂时只能查看已有预览。", !candidate.source_available);
  $("export-result").hidden = true;
  renderGallery();
  updatePreview();
  if (candidate.source_available) state.previewTimer = setTimeout(() => loadNativePreview(candidate, serial, signal), 180);
}
function renderGallery() {
  const picks = state.candidates.filter(c => c.selected);
  const filtered = state.filter === "selected" ? picks : state.filter === "favorites" ? state.candidates.filter(c => c.favorite) : state.filter === "all" ? state.candidates : state.candidates.filter(c => c.source_kind === state.filter);
  const visible = state.filter === "selected" ? filtered : [...filtered].sort((a, b) => a.source_label.localeCompare(b.source_label, "zh-CN") || a.timestamp - b.timestamp);
  const grid = $("candidate-grid");
  const focusedId = grid.contains(document.activeElement) ? document.activeElement.id : null;
  grid.replaceChildren();
  for (const c of visible) {
    const card = document.createElement("article");
    card.className = "candidate" + (state.selected?.id === c.id ? " selected" : "");
    const surface = document.createElement("button"); surface.type = "button"; surface.className = "candidate-surface"; surface.setAttribute("aria-pressed", String(state.selected?.id === c.id)); surface.setAttribute("aria-label", `选择 ${c.title} ${c.timecode}`);
    surface.id = `candidate-surface-${c.id}`; surface.addEventListener("click", () => choose(c));
    const wrap = document.createElement("div"); wrap.className = "candidate-image";
    const image = document.createElement("img"); image.src = c.thumbnail || c.preview; image.alt = c.title; image.loading = "lazy";
    const time = document.createElement("span"); time.className = "candidate-time"; time.textContent = c.timecode;
    const titleRow = document.createElement("div"); titleRow.className = "candidate-title-row";
    const title = document.createElement("h3"); title.className = "candidate-title";
    const titleButton = document.createElement("button"); titleButton.type = "button"; titleButton.className = "candidate-title-select"; titleButton.id = `candidate-title-${c.id}`; titleButton.textContent = c.title; titleButton.setAttribute("aria-pressed", String(state.selected?.id === c.id)); titleButton.addEventListener("click", () => choose(c)); title.append(titleButton);
    const source = document.createElement("p"); source.textContent = c.source_label;
    const chooseButton = document.createElement("button"); chooseButton.type = "button"; chooseButton.className = "candidate-select"; chooseButton.setAttribute("aria-pressed", String(state.selected?.id === c.id)); chooseButton.setAttribute("aria-label", `选择 ${c.title} ${c.timecode}`); chooseButton.textContent = "查看这一帧";
    chooseButton.id = `candidate-view-${c.id}`;
    const favorite = document.createElement("button"); favorite.type = "button"; favorite.className = "favorite-toggle" + (c.favorite ? " is-favorite" : ""); favorite.setAttribute("aria-label", c.favorite ? "取消收藏" : "收藏画面"); favorite.setAttribute("aria-pressed", String(Boolean(c.favorite))); favorite.title = c.favorite ? "取消收藏" : "收藏画面"; favorite.disabled = state.favoriteSaving.has(c.id);
    favorite.id = `candidate-favorite-${c.id}`;
    const heart = document.createElementNS("http://www.w3.org/2000/svg", "svg"); heart.setAttribute("viewBox", "0 0 24 24"); heart.setAttribute("aria-hidden", "true"); heart.classList.add("favorite-icon");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path"); path.setAttribute("d", "M12 20.2 4.8 13A4.8 4.8 0 0 1 11.6 6.2L12 6.7l.4-.5A4.8 4.8 0 0 1 19.2 13L12 20.2Z"); heart.append(path); favorite.append(heart);
    surface.append(wrap); chooseButton.addEventListener("click", () => choose(c));
    favorite.addEventListener("click", () => toggleFavorite(c));
    titleRow.append(title, favorite); wrap.append(image, time); card.append(surface, titleRow, source, chooseButton); grid.append(card);
  }
  $("gallery-summary").textContent = state.filter === "favorites" ? `${filtered.length} 张收藏 / ${state.candidates.length} 张候选。收藏由你管理，与精选分开保存。` : `${picks.length} 张精选 / ${state.candidates.length} 张候选 · ${state.candidates.filter(c => c.favorite).length} 张收藏。自动评分用于初筛；人物、歌词和构图以实际画面为准。`;
  $("gallery-empty").hidden = visible.length > 0;
  $("gallery-empty").textContent = state.filter === "favorites" ? "还没有收藏。浏览精选或候选画面，点按标题右侧的心形即可收藏。" : state.candidates.length ? (state.filter === "selected" ? "还没有精选。切到「全部候选」，从视频里挑一张。" : "这一类还没有候选画面，提取后点击刷新。") : "还没有视频候选。请让 Codex 从本地 Live 或 MV 提取选片，完成后点击刷新。";
  for (const filter of ["selected", "all", "live", "mv"]) {const active = state.filter === filter; $(`${filter}-filter`).classList.toggle("active", active); $(`${filter}-filter`).setAttribute("aria-pressed", String(active));}
  const favoriteFilter = $("favorite-filter"); favoriteFilter.classList.toggle("active", state.filter === "favorites"); favoriteFilter.setAttribute("aria-pressed", String(state.filter === "favorites"));
  if (focusedId) {
    const target = $(focusedId) || grid.querySelector("button") || favoriteFilter;
    if (!target.disabled) target.focus({preventScroll: true});
  }
}
async function toggleFavorite(candidate) {
  if (state.favoriteSaving.has(candidate.id)) return;
  const focusedId = document.activeElement.id === `candidate-favorite-${candidate.id}` ? document.activeElement.id : null;
  const previous = Boolean(candidate.favorite); candidate.favorite = !previous; state.favoriteSaving.add(candidate.id);
  try {
    renderGallery();
    const response = await fetch("/api/favorites", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({candidate_id: candidate.id, favorite: candidate.favorite})});
    const result = await response.json(); if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "收藏暂时无法保存。"); candidate.favorite = result.favorite;
  } catch (error) {candidate.favorite = previous; status(error.message, true);}
  finally {state.favoriteSaving.delete(candidate.id); try {renderGallery(); if (focusedId && document.activeElement === document.body) {const target = $(focusedId) || $("candidate-grid").querySelector(".favorite-toggle") || $("favorite-filter"); if (!target.disabled) target.focus({preventScroll: true});}} catch (renderError) {status(renderError.message, true);}}
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
    const selected = state.candidates.find(c => c.id === state.selected?.id) || state.candidates[0];
    if (selected) choose(selected);
    else {state.selected = null; $("main-preview").hidden = true; $("preview-empty").hidden = false; $("preview-empty").textContent = "等待第一组视频选片"; $("export-button").disabled = true;}
    renderGallery(); renderAvatars();
  } catch (error) { status(error.message, true); $("preview-empty").textContent = "画廊暂时无法打开"; }
  finally { $("refresh-library").disabled = false; }
}
async function exportImage() {
  if (!state.selected || state.exporting) return;
  const p = params();
  if (!Number.isInteger(p.width) || !Number.isInteger(p.height) || Math.min(p.width, p.height) < 256 || Math.max(p.width, p.height) > 8192 || p.width * p.height > 34000000) { status("尺寸需为 256–8192 的整数，且总像素不超过 3400 万。", true); return; }
  state.exporting = true; $("export-button").disabled = true; $("export-result").hidden = true; status("正在从原视频提取这一帧…");
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
        $("original-link").hidden = !job.original; if (job.original) $("original-link").href = job.original;
        $("export-result").hidden = false; status(`已导出 ${job.width} × ${job.height} PNG。下方是实际成品。`); return;
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
$("desktop-preset").addEventListener("click", () => {$("size-preset").value = "2560x1440"; sizeChanged();});
$("phone-preset").addEventListener("click", () => {$("size-preset").value = "1284x2778"; sizeChanged();});
$("reset-settings").addEventListener("click", () => {for (const [id, value] of Object.entries(defaultSettings)) if (id !== "size-preset") $(id).value = value; sizeChanged();});
$("selected-filter").addEventListener("click", () => {state.filter = "selected"; renderGallery();});
$("all-filter").addEventListener("click", () => {state.filter = "all"; renderGallery();});
for (const filter of ["live", "mv"]) $(`${filter}-filter`).addEventListener("click", () => {state.filter = filter; renderGallery();});
$("favorite-filter").addEventListener("click", () => {state.filter = "favorites"; renderGallery();});
$("refresh-library").addEventListener("click", loadLibrary);
$("export-button").addEventListener("click", exportImage);
for (const type of ["wallpaper", "avatar"]) $(`${type}-tab`).addEventListener("click", () => {for (const t of ["wallpaper", "avatar"]) {$(`${t}-panel`).hidden = t !== type; $(`${t}-tab`).classList.toggle("active", t === type); $(`${t}-tab`).setAttribute("aria-pressed", String(t === type));} if (type === "wallpaper") updatePreview();});
for (const id of ["avatar-direction", "avatar-shape", "avatar-notes"]) $(id).addEventListener("input", avatarPrompt);
$("copy-avatar-request").addEventListener("click", async () => {const prompt = avatarPrompt(); try {await navigator.clipboard.writeText(prompt); $("avatar-status").textContent = "已复制。粘贴到 Codex 即可生成；完成后回到这里刷新。";} catch (_) {$("avatar-prompt").closest("details").open = true; $("avatar-prompt").focus(); $("avatar-prompt").select(); $("avatar-status").textContent = "浏览器未允许自动复制，请复制下方已选中的需求。";}});
window.addEventListener("resize", updatePreview);
avatarPrompt(); updatePreview(); loadLibrary();

for (const id of ["avatar-selection", "avatar-export-size", "avatar-fit"]) $(id).addEventListener("change", avatarExportChanged);
$("avatar-export-size").addEventListener("change", () => {$("avatar-fit").value = "cover"; avatarExportChanged();});
for (const id of ["artwork-focus-x", "artwork-focus-y"]) $(id).addEventListener("input", avatarExportChanged);
$("export-avatar").addEventListener("click", exportAvatar);
