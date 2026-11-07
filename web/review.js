"use strict";
// 待挑: one picture at a time. 留 = this exact moment, favorited and accepted;
// 不要 = a counter-example (with an optional reason); 换时刻 = scene fine, moment not.
(() => {
  const $ = id => document.getElementById(id);
  const review = {items: [], index: 0, pick: 0, front: "review-img-a", history: [], loaded: false, advancing: null, reasonOpen: false};
  window.reviewDirty = false;

  const pending = item => !item.decision || item.decision === "skip";
  function order() {
    // Undecided first, skipped after them, decided last; stable inside each group.
    const rank = item => !item.decision ? 0 : item.decision === "skip" ? 1 : 2;
    return review.items.map((item, at) => [item, at]).sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map(([item]) => item);
  }
  function offsetText(seconds) { return seconds === 0 ? "0s" : `${seconds > 0 ? "+" : ""}${seconds}s`; }
  function toast(text, undo = true) {
    $("review-toast-text").textContent = text; $("review-undo").hidden = !undo;
    $("review-toast").classList.add("show"); clearTimeout(toast.timer);
    toast.timer = setTimeout(() => $("review-toast").classList.remove("show"), 2800);
  }

  async function load() {
    $("review-empty").hidden = true;
    try {
      const response = await fetch("/api/review-queue");
      if (!response.ok) throw new Error("待挑队列暂时打不开");
      // One batch at a time, so counts and the keep rate stay per batch:
      // the first batch with undecided pictures, else the newest one.
      const all = (await response.json()).items;
      const batches = [...new Set(all.map(item => item.batch))];
      const active = batches.find(batch => all.some(item => item.batch === batch && pending(item))) ?? batches[batches.length - 1];
      review.all = all;
      review.items = all.filter(item => item.batch === active);
      review.loaded = true;
      const queue = order();
      review.index = Math.max(0, review.items.indexOf(queue.find(pending) || queue[0]));
      review.pick = null;
      render();
    } catch (error) { $("review-empty").hidden = false; $("review-empty").textContent = error.message; }
  }

  function badge(left) { $("review-tab-count").textContent = left || ""; $("review-tab-count").hidden = !left; }
  function current() { return review.items[review.index]; }
  function tally() {
    const count = kind => review.items.filter(item => item.decision === kind).length;
    const done = review.items.filter(item => item.decision && item.decision !== "skip").length;
    $("review-done").textContent = done; $("review-all").textContent = review.items.length;
    for (const kind of ["keep", "drop", "moment", "skip"]) $(`review-${kind}-count`).textContent = count(kind);
    $("review-bar").style.width = review.items.length ? `${done / review.items.length * 100}%` : "0";
    // Badge counts every batch, so a finished batch doesn't look like an empty queue.
    badge((review.all || review.items).filter(pending).length);
    $("review-batch").textContent = review.items[0]?.batch || "待挑";
  }

  function render() {
    tally(); renderQueue();
    const item = current(), empty = !review.items.length, finished = !empty && !review.items.some(pending);
    $("review-desk").hidden = empty || (finished && !review.revisit);
    $("review-finished").hidden = !finished || review.revisit;
    $("review-empty").hidden = !empty;
    if (empty) { $("review-empty").textContent = "还没有待挑的新候选。新的一批生成后会出现在这里。"; return; }
    if (finished && !review.revisit) {
      const kept = review.items.filter(i => i.decision === "keep").length;
      $("review-finished-text").textContent = `留下 ${kept} 张，留下比例 ${Math.round(kept / review.items.length * 100)}%。「换时刻」的 ${review.items.filter(i => i.decision === "moment").length} 个位置会在下一批重新找。`;
      const following = [...new Set((review.all || []).filter(pending).map(i => i.batch))].find(b => b !== review.items[0]?.batch);
      const nextButton = $("review-next-batch");
      nextButton.hidden = !following;
      if (following) nextButton.textContent = `下一批：${following.replace(/^待挑 · /, "")}（${review.all.filter(i => i.batch === following && pending(i)).length} 张）→`;
      return;
    }
    if (review.pick === null) review.pick = item.decision === "keep" && item.kept_moment !== null ? item.kept_moment : item.system_moment;
    $("review-title").textContent = item.title;
    $("review-source").textContent = `${item.source_label} · ${item.moments[item.system_moment].timecode.split(".")[0]} · ${item.candidate_id}`;
    $("review-why").replaceChildren(...(item.reasons.length ? item.reasons : ["系统按画面质量挑出"]).map(text => Object.assign(document.createElement("li"), {textContent: text})));
    const strip = $("review-strip"); strip.replaceChildren();
    item.moments.forEach((moment, k) => {
      if (moment.cut_before && k > 0) strip.append(Object.assign(document.createElement("span"), {className: "review-cut", title: "这里切了镜头"}));
      const button = document.createElement("button"); button.type = "button"; button.className = "review-moment";
      button.setAttribute("aria-label", `${offsetText(moment.offset)} 的画面`);
      const image = Object.assign(document.createElement("img"), {src: moment.url, alt: "", loading: "lazy"});
      const label = Object.assign(document.createElement("em"), {textContent: offsetText(moment.offset) + (moment.label ? ` · ${moment.label}` : "")});
      button.append(image, label);
      if (k === item.system_moment) button.append(Object.assign(document.createElement("span"), {className: "review-sys", textContent: "系统选"}));
      button.addEventListener("click", () => setPick(k));
      strip.append(button);
    });
    const done = item.decision && item.decision !== "skip";
    $("review-state").hidden = !done;
    $("review-state").textContent = done ? `已决定：${{keep: "留", drop: "不要", moment: "换时刻"}[item.decision]}${item.drop_reason ? `「${item.drop_reason}」` : ""} · 可以重新选，或按 Z 撤销` : "";
    closeReasons();
    setPick(review.pick);
  }

  function show(src) {
    const a = $(review.front), b = $(review.front === "review-img-a" ? "review-img-b" : "review-img-a");
    if (a.getAttribute("src") === src) return;
    b.onload = () => { b.classList.remove("out"); a.classList.add("out"); review.front = b.id; };
    b.src = src;
  }
  function setPick(k) {
    const item = current(); if (!item) return;
    review.pick = Math.max(0, Math.min(item.moments.length - 1, k));
    const moment = item.moments[review.pick];
    document.querySelectorAll("#review-strip .review-moment").forEach((button, j) => button.classList.toggle("on", j === review.pick));
    document.querySelector("#review-strip .review-moment.on")?.scrollIntoView({block: "nearest", inline: "nearest"});
    show(moment.url);
    $("review-stamp").textContent = review.pick === item.system_moment ? "系统选的时刻" : "你换的时刻";
    $("review-time").textContent = `${moment.timecode.split(".")[0]} · ${offsetText(moment.offset)}`;
    $("review-note").textContent = review.pick === item.system_moment ? "按 ← → 看看前后有没有更好的一刻。" : `留下时保存 ${offsetText(moment.offset)} 这一刻（原推荐时间也会记下）。`;
  }

  function renderQueue() {
    const queue = $("review-queue"); queue.replaceChildren();
    review.items.forEach((item, k) => {
      const button = document.createElement("button"); button.type = "button";
      button.className = "review-q" + (k === review.index ? " cur" : "");
      if (item.decision) button.dataset.s = item.decision;
      button.setAttribute("aria-label", `${item.title} ${item.candidate_id}`);
      const moment = item.moments[item.decision === "keep" && item.kept_moment !== null ? item.kept_moment : item.system_moment];
      button.append(Object.assign(document.createElement("img"), {src: moment.url, alt: "", loading: "lazy"}));
      button.addEventListener("click", () => go(k));
      queue.append(button);
    });
    queue.querySelector(".cur")?.scrollIntoView({block: "nearest", inline: "center"});
  }
  function go(k) { review.index = k; review.pick = null; review.revisit = true; render(); }
  function next() {
    const after = [...review.items.slice(review.index + 1), ...review.items.slice(0, review.index + 1)];
    const target = after.find(item => !item.decision) || after.find(item => item.decision === "skip" && item !== current());
    review.revisit = false;
    if (target) { review.index = review.items.indexOf(target); review.pick = null; }
    render();
  }

  function verdict(kind, text) {
    const box = $("review-verdict"); box.className = `review-verdict ${kind} show`; box.firstElementChild.textContent = text;
    setTimeout(() => { box.className = "review-verdict"; }, 380);
  }
  // Optimistic: the next picture shows at once; saves go out one after another
  // in order, and a failed save puts that picture's previous state back.
  let saving = Promise.resolve();
  function save(item, body, before, failText) {
    saving = saving.then(async () => {
      try {
        const response = await fetch("/api/review-decisions", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
        if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || failText);
        window.reviewDirty = true;
      } catch (error) {
        Object.assign(item, before); tally(); renderQueue();
        toast(`${item.candidate_id}：${error.message}`, false);
      }
    });
  }
  const snapshot = item => ({decision: item.decision, kept_moment: item.kept_moment, drop_reason: item.drop_reason});
  // A keep shows its stamp briefly; any next key ends that pause instead of being lost.
  function flushAdvance() { if (!review.advancing) return; clearTimeout(review.advancing); review.advancing = null; next(); }
  function decide(decision, reason = "") {
    flushAdvance();
    const item = current(); if (!item) return;
    const before = snapshot(item);
    const body = {candidate_id: item.id, decision, reason};
    if (decision === "keep") body.moment = review.pick;
    item.decision = decision; item.kept_moment = decision === "keep" ? review.pick : null; item.drop_reason = reason;
    save(item, body, before, "没有保存成功，请重试");
    if (document.activeElement === $("review-reason-text")) document.activeElement.blur();
    review.history.push({index: review.index, before});
    const moved = decision === "keep" && review.pick !== item.system_moment;
    if (decision === "keep") verdict("keep", "留");
    toast(decision === "keep" ? `已留 · 加入收藏${moved ? `（换到 ${offsetText(item.moments[review.pick].offset)}）` : ""}`
      : decision === "moment" ? "不算反例 · 这个位置下一批会在附近重新找时刻"
      : decision === "drop" ? `已记为不要${reason ? `：「${reason}」` : ""}` : "已跳过，稍后再看");
    if (decision === "keep") { tally(); renderQueue(); review.advancing = setTimeout(() => { review.advancing = null; next(); }, 300); }
    else next();
  }
  function undo() {
    const last = review.history.pop(); if (!last) return;
    const item = review.items[last.index], now = snapshot(item);
    const body = last.before.decision === "keep" ? {candidate_id: item.id, decision: "keep", moment: last.before.kept_moment}
      : {candidate_id: item.id, decision: last.before.decision, reason: last.before.drop_reason || ""};
    Object.assign(item, last.before);
    save(item, body, now, "撤销没有成功，请重试");
    review.index = last.index; review.pick = null; review.revisit = true;
    $("review-toast").classList.remove("show"); render();
  }

  function openReasons() { review.reasonOpen = true; $("review-reasons").classList.add("open"); verdict("drop", "不要"); }
  function closeReasons() { review.reasonOpen = false; $("review-reasons").classList.remove("open"); $("review-reason-text").value = ""; }
  function drop() { if (review.reasonOpen) decide("drop"); else openReasons(); }

  $("review-keep").addEventListener("click", () => decide("keep"));
  $("review-drop").addEventListener("click", drop);
  $("review-skip").addEventListener("click", () => decide("skip"));
  $("review-undo").addEventListener("click", undo);
  $("review-restart").addEventListener("click", () => { review.revisit = true; go(0); });
  $("review-next-batch").addEventListener("click", () => { review.revisit = false; load(); });
  document.querySelectorAll("#review-reasons button").forEach(button => button.addEventListener("click", () => {
    if (button.dataset.moment) decide("moment"); else decide("drop", button.textContent);
  }));
  $("review-reason-text").addEventListener("keydown", event => {
    // 普通回车留给输入法选字；⌘/Ctrl+回车才提交
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && !event.isComposing) { event.preventDefault(); decide("drop", event.target.value.trim()); }
    if (event.key === "Escape") event.target.blur();
  });
  document.addEventListener("keydown", event => {
    if ($("review-panel").hidden || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.target.matches("input, select, textarea, [contenteditable]")) return;
    const key = event.key.toLowerCase();
    if (key === "z") { flushAdvance(); undo(); return; }
    if ($("review-desk").hidden) return;
    if (["arrowright", "arrowleft", "x"].includes(key)) flushAdvance();
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") { event.preventDefault(); setPick(review.pick + (event.key === "ArrowRight" ? 1 : -1)); }
    else if (key === "f") decide("keep");
    else if (key === "x") drop();
    else if (event.key === " ") { event.preventDefault(); decide("skip"); }
    else if (event.key === "Escape" && review.reasonOpen) closeReasons();
  });
  window.reviewOpen = () => { if (!review.loaded) load(); else render(); };
  window.reviewReload = load;
  // Badge count even before the tab is opened.
  fetch("/api/review-queue").then(r => r.ok ? r.json() : null).then(data => {
    if (!data || review.loaded) return;
    const left = data.items.filter(pending).length;
    badge(left);
  }).catch(() => {});
})();
