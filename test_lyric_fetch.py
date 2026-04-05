import os
import asyncio
from playwright.async_api import async_playwright

# Proxy address (default: local Clash)
PROXY_URL = os.environ.get("PROXY_URL", "http://127.0.0.1:26001")


# ========================================

async def fetch_one_lyric():
    print(">>> [Init] Starting Lyric Fetch Test...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            proxy={"server": PROXY_URL}
        )
        page = await browser.new_page()

        # Target: fetch lyrics for "好きすぎて痛い"
        target_url = "https://www.uta-net.com/song/382208/"
        print(f">>> [Network] Navigating to {target_url} ...")

        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)

            # 1. Locate the lyrics area
            # Uta-Net's standard lyrics container ID is #kashi_area
            print(">>> [Parsing] Locating #kashi_area ...")
            await page.wait_for_selector("#kashi_area")

            # 2. Extract text
            # inner_text() automatically converts <br> to \n and &nbsp; to spaces
            raw_lyrics = await page.locator("#kashi_area").inner_text()

            # 3. Simple post-processing
            # Lyrics sometimes have blank lines at start/end, strip() removes them
            clean_lyrics = raw_lyrics.strip()

            print("\n" + "=" * 20 + " LYRIC PREVIEW " + "=" * 20)
            # Print only first 8 lines as preview to avoid flooding the screen
            preview_lines = clean_lyrics.split('\n')[:8]
            for line in preview_lines:
                print(line)
            print("..." + "\n" + "=" * 55)

            print(f"✅ Success! Total length: {len(clean_lyrics)} chars")

        except Exception as e:
            print(f"❌ Error: {e}")
            await page.screenshot(path="lyric_error.png")

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(fetch_one_lyric())
