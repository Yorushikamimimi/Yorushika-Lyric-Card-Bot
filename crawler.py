import os
import asyncio
import random
from playwright.async_api import async_playwright, Page

# === Configuration ===
# Proxy address (default: local Clash)
PROXY_URL = os.environ.get("PROXY_URL", "http://127.0.0.1:26001")
# Yorushika's artist page on Uta-Net
ARTIST_URL = os.environ.get("UTANET_ARTIST_URL", "https://www.uta-net.com/artist/22669/")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


# =============

class YorushikaCrawler:
    def __init__(self):
        self.browser = None
        self.playwright = None

    async def start(self):
        print(">>> [Crawler] Starting browser...")
        self.playwright = await async_playwright().start()
        # Still use headless=True to keep silent
        self.browser = await self.playwright.chromium.launch(
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
        """[Core wrapper] Page navigation with retry mechanism"""
        for i in range(retries):
            try:
                print(f"    -> Accessing {url} (Attempt {i + 1}/{retries})...")
                # timeout=30000 (30s timeout)
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                return  # Success, return directly
            except Exception as e:
                print(f"    ⚠️ Connection failed: {e}")
                if i < retries - 1:
                    wait_time = (i + 1) * 2  # Wait 2s, 4s on failure...
                    print(f"    ⏳ Waiting {wait_time}s before retry...")
                    await asyncio.sleep(wait_time)
                else:
                    raise e  # Retries exhausted, raise exception

    async def get_random_song_url(self) -> str:
        page = await self.browser.new_page(user_agent=USER_AGENT)
        try:
            print(f">>> [Crawler] 1. Fetching song list...")
            await self._goto_with_retry(page, ARTIST_URL)

            # Simple scroll
            await page.mouse.wheel(0, 3000)
            await asyncio.sleep(1)  # Wait briefly for rendering

            # Wait for links
            await page.wait_for_selector("a[href*='/song/']", timeout=10000)
            elements = await page.locator("a[href*='/song/']").all()

            song_urls = []
            for el in elements:
                href = await el.get_attribute("href")
                if href and "/song/" in href:
                    song_urls.append(f"https://www.uta-net.com{href}")

            # Deduplicate
            unique = list(set(song_urls))
            if not unique:
                raise Exception("No songs found (List empty)!")

            selected = random.choice(unique)
            print(f"✅ [Crawler] Found {len(unique)} songs. Selected: {selected}")
            return selected

        finally:
            await page.close()

    async def get_lyric_by_url(self, url: str) -> tuple[str, str]:
        """Fetch lyric and song title, return (lyric, song_title)"""
        print(">>> [Crawler] 🛌 Resting for 2s...")
        await asyncio.sleep(2)

        page = await self.browser.new_page(user_agent=USER_AGENT)
        try:
            print(f">>> [Crawler] 2. Fetching lyric...")
            await self._goto_with_retry(page, url)

            # Extract song title
            song_title = ""
            try:
                # Uta-Net song page title is in h2 or .title class
                title_el = page.locator("h2")
                if await title_el.count() > 0:
                    song_title = await title_el.first.inner_text()
                    song_title = song_title.strip()
            except Exception:
                pass

            await page.wait_for_selector("#kashi_area", timeout=10000)
            raw = await page.locator("#kashi_area").inner_text()

            lines = [line.strip() for line in raw.split('\n') if line.strip()]

            if len(lines) > 5:
                preview = "\n".join(lines[:8])
            else:
                preview = "\n".join(lines)

            print(f"✅ [Crawler] Lyric captured! ({len(preview)} chars)")
            print(f"✅ [Crawler] Song title: {song_title}")
            return preview, song_title

        finally:
            await page.close()


# --- Test entry point ---
async def test_run():
    bot = YorushikaCrawler()
    await bot.start()
    try:
        url = bot.get_random_song_url()
        lyric = bot.get_lyric_by_url(url)
        print("\n" + "=" * 30)
        print(lyric)
        print("=" * 30)
    except Exception as e:
        print(f"❌ FATAL ERROR: {e}")
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(test_run())
