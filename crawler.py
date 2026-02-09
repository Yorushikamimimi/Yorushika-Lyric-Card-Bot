import asyncio
import random
from playwright.async_api import async_playwright, Page

# === 配置区 ===
# 你的代理地址
PROXY_URL = "http://127.0.0.1:26001"
ARTIST_URL = "https://www.uta-net.com/artist/22669/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


# =============

class YorushikaCrawler:
    def __init__(self):
        self.browser = None
        self.playwright = None

    async def start(self):
        print(">>> [Crawler] Starting browser...")
        self.playwright = await async_playwright().start()
        # 依然使用 headless=True 保持静默
        self.browser = await self.playwright.chromium.launch(
            channel="msedge",
            headless=True,
            proxy={"server": PROXY_URL}
        )

    async def stop(self):
        if self.browser:
            await self.browser.close()
            print(">>> [Crawler] Browser closed.")
        if self.playwright:
            await self.playwright.stop()

    async def _goto_with_retry(self, page: Page, url: str, retries=3):
        """ 【核心封装】带重试机制的页面跳转 """
        for i in range(retries):
            try:
                print(f"    -> Accessing {url} (Attempt {i + 1}/{retries})...")
                # timeout=30000 (30秒超时)
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                return  # 成功则直接返回
            except Exception as e:
                print(f"    ⚠️ Connection failed: {e}")
                if i < retries - 1:
                    wait_time = (i + 1) * 2  # 失败等待 2秒, 4秒...
                    print(f"    ⏳ Waiting {wait_time}s before retry...")
                    await asyncio.sleep(wait_time)
                else:
                    raise e  # 次数用尽，抛出异常

    async def get_random_song_url(self) -> str:
        page = await self.browser.new_page(user_agent=USER_AGENT)
        try:
            print(f">>> [Crawler] 1. Fetching song list...")
            await self._goto_with_retry(page, ARTIST_URL)

            # 简单的滚动
            await page.mouse.wheel(0, 3000)
            await asyncio.sleep(1)  # 稍微等一下渲染

            # 等待链接
            await page.wait_for_selector("a[href*='/song/']", timeout=10000)
            elements = await page.locator("a[href*='/song/']").all()

            song_urls = []
            for el in elements:
                href = await el.get_attribute("href")
                if href and "/song/" in href:
                    song_urls.append(f"https://www.uta-net.com{href}")

            # 去重
            unique = list(set(song_urls))
            if not unique:
                raise Exception("No songs found (List empty)!")

            selected = random.choice(unique)
            print(f"✅ [Crawler] Found {len(unique)} songs. Selected: {selected}")
            return selected

        finally:
            await page.close()

    async def get_lyric_by_url(self, url: str) -> str:
        # 【拟人化】在请求详情页之前，故意休息 2 秒，防止被服务器判定为机器人
        print(">>> [Crawler] 🛌 Resting for 2s...")
        await asyncio.sleep(2)

        page = await self.browser.new_page(user_agent=USER_AGENT)
        try:
            print(f">>> [Crawler] 2. Fetching lyric...")
            await self._goto_with_retry(page, url)

            await page.wait_for_selector("#kashi_area", timeout=10000)
            raw = await page.locator("#kashi_area").inner_text()

            # 简单清洗
            lines = [line.strip() for line in raw.split('\n') if line.strip()]

            # 为了卡片好看，我们只取比较“整齐”的一段
            # 比如取中间的几句，或者直接取前几句
            if len(lines) > 5:
                # 稍微取一点稍微长一点的歌词，太短的不好看
                preview = "\n".join(lines[:8])
            else:
                preview = "\n".join(lines)

            print(f"✅ [Crawler] Lyric captured! ({len(preview)} chars)")
            return preview

        finally:
            await page.close()


# --- 测试入口 ---
async def test_run():
    bot = YorushikaCrawler()
    await bot.start()
    try:
        url = await bot.get_random_song_url()
        lyric = await bot.get_lyric_by_url(url)
        print("\n" + "=" * 30)
        print(lyric)
        print("=" * 30)
    except Exception as e:
        print(f"❌ FATAL ERROR: {e}")
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(test_run())