import os
import asyncio
from playwright.async_api import async_playwright

# =================Configuration=================
# Proxy address (default: local Clash)
PROXY_URL = os.environ.get("PROXY_URL", "http://127.0.0.1:26001")
# Yorushika's artist page on Uta-Net
TARGET_URL = os.environ.get("UTANET_ARTIST_URL", "https://www.uta-net.com/artist/22669/")


# ========================================

async def main():
    print(f">>> [Init] Connecting via Proxy: {PROXY_URL}")

    async with async_playwright() as p:
        # 1. Launch browser with explicit proxy specification
        # Uses local Edge browser
        browser = await p.chromium.launch(
            headless=False,
            proxy={"server": PROXY_URL}  # <--- Core change: force traffic through proxy
        )

        # 2. Anti-crawler: disguise as normal user
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            print(f">>> [Network] Goto {TARGET_URL} ...")
            # Extended timeout to 60 seconds, some pages load slowly
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)

            # 3. [Core Technique] Scroll down frantically to trigger Lazy Load
            print(">>> [Action] Scrolling down to load song list...")
            for i in range(5):
                await page.mouse.wheel(0, 1000)  # Scroll down 1000px
                await asyncio.sleep(0.5)  # Brief pause for visual tracking

            # 4. Wait for lyrics list container (tables usually have an ID or Class)
            # Using rough search: find links containing '/song/'
            print(">>> [Parsing] Waiting for song links...")
            await page.wait_for_selector("a[href*='/song/']", timeout=15000)

            # Extract
            elements = await page.locator("a[href*='/song/']").all()

            valid_songs = []
            for el in elements:
                txt = await el.inner_text()
                url = await el.get_attribute("href")
                # Simple filter: song name cannot be empty, link length should be normal
                if txt and url and len(txt.strip()) > 0:
                    full_url = f"https://www.uta-net.com{url}"
                    valid_songs.append(f"{txt.strip()} | {full_url}")

            # Deduplicate (set) and print
            unique_songs = list(set(valid_songs))

            print(f"\n✅ SUCCESS! Found {len(unique_songs)} songs.")
            print("=" * 40)
            for s in unique_songs[:10]:  # Print only first 10 songs
                print(s)
            print("=" * 40)

        except Exception as e:
            print(f"❌ Error: {e}")
            # Screenshot for analysis
            await page.screenshot(path="final_error.png")

        finally:
            print(">>> [Cleanup] Closing browser...")
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
