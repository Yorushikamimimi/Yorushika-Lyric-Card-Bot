// 网页代填：新开标签页填好图、标题、正文、话题、AI 声明（和定时），停在发布页截图。绝不点「发布」。
// 跑法：sh publish/ego/run.sh fill_post dy|xhs <成品文件夹（如 …/抖音）> <文案.txt> ["YYYY-MM-DD HH:MM"]
const [plat, folder, captionFile, when] = ARGS;
if (!["dy", "xhs"].includes(plat) || !folder || !captionFile) throw new Error("参数：dy|xhs 成品文件夹 文案.txt [定时]");
const fs = await import("node:fs/promises");
const path = await import("node:path");

// 文案.txt：「标题：」「抖音标题（限 20 字）：」开头两行，空行后是正文，# 开头的一行是话题，「——」以下是给本人的备注不贴
const raw = (await fs.readFile(captionFile, "utf8")).split("——")[0];
const lines = raw.split("\n");
const pick = (re) => (lines.find((l) => re.test(l)) || "").replace(re, "").trim();
const title = (plat === "dy" && pick(/^抖音标题[^：]*：/)) || pick(/^标题：/);
const tagLine = lines.find((l) => /^#\S/.test(l.trim())) || "";
const topics = tagLine.trim().split(/\s+/).map((t) => t.replace(/^#/, "")).filter(Boolean);
const body = lines.filter((l) => !/^(抖音)?标题/.test(l) && l !== tagLine).join("\n").trim();
if (!title) throw new Error("文案里没找到「标题：」");
if (plat === "dy" && [...title].length > 20) throw new Error(`抖音标题超 20 字：${title}`);
const files = (await fs.readdir(folder)).filter((f) => /\.(png|jpe?g|webp)$/i.test(f)).sort().map((f) => path.join(folder, f));
if (!files.length) throw new Error(`${folder} 里没有图片`);
console.log({ plat, title, topics, files: files.map((f) => path.basename(f)), when: when || "不定时" });

const NAME = "夜鹿-发帖代填";
const entry = (await listTaskSpaces()).find((s) => s.name === NAME);
const task = !entry ? await taskSpace(NAME) : entry.ownership === "user" ? await claimTaskSpace(entry.id)
  : entry.ownership === "agentDelegatedToUser" ? await takeOverTaskSpace(entry.id) : await taskSpace(entry.id);
const page = await task.newPage();
const URL = plat === "dy" ? "https://creator.douyin.com/creator-micro/content/upload?default-tab=3"
  : "https://creator.xiaohongshu.com/publish/publish?source=official&target=image";
await page.goto(URL, { timeout: 45000 });
await page.waitForTimeout(4000);
const pageText = () => page.evaluate(() => document.body.innerText);
if (/扫码登录|立即登录|验证码/.test((await pageText()).slice(0, 3000))) { await task.handOff(); throw new Error("要登录或验证：请本人在 ego「夜鹿-发帖代填」里处理后重跑"); }
// 按文字点元素（用页面内 click，避开真实鼠标让抖音卡死的问题）
const jsClick = (txt, exact = true) => page.evaluate(({ txt, exact }) => {
  const els = [...document.querySelectorAll("body *")].filter((e) => e.offsetParent && (exact ? e.textContent.trim() === txt : e.textContent.includes(txt)));
  const el = els.sort((a, b) => a.textContent.length - b.textContent.length)[0];
  if (el) el.click(); return !!el;
}, { txt, exact });
const warn = [];

// 1. 上传：单个 input[type=file]，一次传完，顺序照文件名
await page.setInputFiles("input[type=file]", files);
await page.waitForTimeout(3000 + files.length * 2500);

// 2. 标题、正文、话题
if (plat === "dy") {
  await page.waitForSelector('input[placeholder="添加作品标题"]', { timeout: 60000 });
  await page.fill('input[placeholder="添加作品标题"]', title);
  await page.click("[contenteditable=true]");
  await page.keyboard.paste(body + "\n");
  for (const t of topics) { await page.keyboard.type("#" + t); await page.waitForTimeout(800); await page.keyboard.press("Space"); await page.waitForTimeout(400); }
} else {
  await page.waitForSelector('input[placeholder="填写标题会有更多赞哦"]', { timeout: 60000 });
  await page.fill('input[placeholder="填写标题会有更多赞哦"]', title);
  await page.click(".tiptap.ProseMirror");
  await page.keyboard.paste(body + "\n");
  for (const t of topics) {
    await page.keyboard.type("#" + t); await page.waitForTimeout(1500);
    const first = await page.evaluate(() => { const el = document.querySelector("[class*=suggest] [class*=item], [class*=topic] li, [class*=mention] li"); return el ? el.textContent.trim() : ""; });
    if (first && !first.replace(/^#/, "").startsWith(t)) warn.push(`话题「${t}」联想第一项是「${first.slice(0, 20)}」，请核`);
    await page.keyboard.press("Enter"); await page.waitForTimeout(500);
  }
}

// 3. AI 声明
if (plat === "dy") {
  const ok = await page.evaluate(() => { const el = document.querySelector("[class^=selectBox-]"); if (el) el.click(); return !!el; });
  await page.waitForTimeout(1200);
  if (!(ok && (await jsClick("内容由AI生成")) && (await page.waitForTimeout(500), await jsClick("确定")))) warn.push("抖音自主声明没选上，请本人选「内容由AI生成」");
} else {
  await jsClick("内容设置", false); await page.waitForTimeout(800);
  await jsClick("添加内容类型声明"); await page.waitForTimeout(800);
  if (!(await jsClick("笔记含AI合成内容"))) warn.push("小红书 AI 声明没选上，请本人在内容设置里选「笔记含AI合成内容」");
}
await page.waitForTimeout(800);

// 4. 定时（未实测：控件找不到就留给本人选，不报错）
if (when) {
  if (plat === "xhs") { await jsClick("更多设置", false); await page.waitForTimeout(800); }
  const switched = plat === "dy" ? await jsClick("定时发布") : await page.evaluate(() => {
    const lab = [...document.querySelectorAll("body *")].find((e) => e.offsetParent && e.textContent.trim() === "定时发布");
    const sw = lab && (lab.parentElement.querySelector("[class*=switch], input[type=checkbox]") || lab.closest("div")?.parentElement?.querySelector("[class*=switch]"));
    if (sw) sw.click(); return !!sw;
  });
  await page.waitForTimeout(1000);
  const filled = switched && await page.evaluate((v) => {
    const inp = [...document.querySelectorAll("input")].find((i) => i.offsetParent && /日期|时间/.test(i.placeholder || ""));
    if (!inp) return false;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(inp, v);
    inp.dispatchEvent(new Event("input", { bubbles: true })); inp.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }, when);
  if (filled) { await page.keyboard.press("Enter"); await page.waitForTimeout(800); }
  else warn.push(`定时没填上，请本人在页面选「定时发布」${when}`);
}

// 5. 截图，停在发布页，不点发布
const shot = path.join(path.dirname(folder), `代填-${plat}.png`);
await page.screenshot({ path: shot, fullPage: true });
console.log({ screenshot: shot, page: page.label, space: task.spaceId, warn });
console.log("已停在发布页：请本人核对后亲手点「发布」或「定时发布」。");
await task.finish({ keep: [page.label] });
