// 周读：只读抖音、小红书后台，按帖输出 日期｜标题｜数字。不点任何按钮。
// 跑法：sh publish/ego/run.sh read_stats [输出.csv]（ARGS[0] 可省，省了只打印）
const SPACE = "夜鹿-四平台后台数据（常驻）";
const entry = (await listTaskSpaces()).find((s) => s.name === SPACE);
if (!entry) throw new Error(`没找到 Space「${SPACE}」，请本人在 ego 里建好并登录四个后台`);
const task = entry.ownership === "user" ? await claimTaskSpace(entry.id)
  : entry.ownership === "agentDelegatedToUser" ? await takeOverTaskSpace(entry.id)
  : await taskSpace(entry.id);
for (const t of await task.tabs()) if (!t.label) await task.adopt(t.page);
const tabs = await task.tabs();
const keep = tabs.map((t) => t.label);
const find = (host) => { const t = tabs.find((x) => x.url.includes(host)); if (!t) throw new Error(`Space 里没有 ${host} 标签页`); return task.page(t.label); };
const text = (p) => p.evaluate(() => document.body.innerText);
const needLogin = (t) => /扫码登录|登录后|立即登录|验证码/.test(t.slice(0, 3000));
const out = [];

// 抖音：作品管理
{
  const p = find("creator.douyin.com");
  await p.goto("https://creator.douyin.com/creator-micro/content/manage", { timeout: 45000 });
  await p.waitForTimeout(4000);
  let t = await text(p);
  if (needLogin(t)) { await task.handOff(); throw new Error("抖音要重新登录：请本人在 Space 里扫码后再跑"); }
  for (let i = 0; i < 25 && !t.includes("没有更多作品"); i++) {
    await p.mouse.move(700, 500); await p.mouse.wheel(0, 1500); await p.waitForTimeout(900); t = await text(p);
  }
  const total = +(t.match(/作品 \((\d+)\)/) || [])[1];
  const L = t.split("\n"); let title = "";
  const KEYS = { 播放: "views", 点赞: "likes", 评论: "comments", 分享: "shares", 收藏: "saves" };
  for (let i = 0; i < L.length; i++) {
    if (L[i].startsWith("ヨルシカ")) title = L[i].split("。")[0];
    const m = L[i].match(/^(\d{4})年(\d+)月(\d+)日 (\d+:\d+)$/);
    if (!m) continue;
    const r = { platform: "抖音", date: `${m[1]}-${m[2].padStart(2, "0")}-${m[3].padStart(2, "0")}`, time: m[4], title };
    for (let j = i + 1; j < i + 22 && j < L.length - 1; j++) if (KEYS[L[j]] && !(KEYS[L[j]] in r)) r[KEYS[L[j]]] = L[j + 1];
    out.push(r);
  }
  const n = out.length;
  console.log(`抖音：读到 ${n} 篇，后台显示 ${total} 篇 ${n === total ? "✓" : "✗ 条数对不上"}`);
}

// 小红书：笔记管理（卡片五个数顺序：浏览、评论、点赞、收藏、分享）
{
  const p = find("creator.xiaohongshu.com");
  await p.goto("https://creator.xiaohongshu.com/new/note-manager", { timeout: 45000 });
  await p.waitForTimeout(5000);
  let t = await text(p);
  if (needLogin(t)) { await task.handOff(); throw new Error("小红书要重新登录：请本人在 Space 里扫码后再跑"); }
  const total = +(t.match(/全部 (\d+)/) || [])[1];
  const count = (s) => (s.match(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/gm) || []).length;
  for (let i = 0; i < 20 && count(t) < total; i++) {
    await p.evaluate(() => { for (const el of document.querySelectorAll("*")) if (el.scrollHeight > el.clientHeight + 50 && /auto|scroll/.test(getComputedStyle(el).overflowY)) el.scrollTop = el.scrollHeight; window.scrollTo(0, document.body.scrollHeight); });
    await p.waitForTimeout(1500); t = await text(p);
  }
  const L = t.split("\n"); let n = 0;
  for (let i = 1; i < L.length; i++) {
    const m = L[i].match(/^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})$/);
    if (!m) continue;
    const [views, comments, likes, saves, shares] = L.slice(i + 1, i + 6);
    out.push({ platform: "小红书", date: m[1], time: m[2], title: L[i - 1], views, likes, saves, comments, shares }); n++;
  }
  console.log(`小红书：读到 ${n} 篇，后台显示 ${total} 篇 ${n === total ? "✓" : "✗ 条数对不上"}`);
}

await task.finish({ keep });
const cols = ["platform", "date", "time", "title", "views", "likes", "saves", "comments", "shares"];
const csv = [cols.join(","), ...out.map((r) => cols.map((c) => `"${String(r[c] ?? "").replace(/"/g, '""')}"`).join(","))].join("\n") + "\n";
if (ARGS[0]) { const fs = await import("node:fs/promises"); await fs.writeFile(ARGS[0], csv); console.log(`已写 ${ARGS[0]}`); }
console.log(csv);
