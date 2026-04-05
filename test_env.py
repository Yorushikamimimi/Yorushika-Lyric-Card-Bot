import asyncio
from playwright.async_api import async_playwright


async def run_smoke_test():
    print(">>> [Init] Starting Smoke Test...")

    async with async_playwright() as p:
        # Launch browser
        # headless=True is the default, but during development it's sometimes set to False for easier debugging
        print(">>> [Browser] Launching Chromium...")
        browser = await p.chromium.launch(headless=True)

        # Context and Page are the browser instance and tab
        context = await browser.new_context()
        page = await context.new_page()

        target_url = "https://yorushika.com/"
        print(f">>> [Network] Navigating to {target_url} ...")

        try:
            # timeout=10000 means error if not loaded in 10 seconds, prevents indefinite waiting
            await page.goto(target_url, timeout=10000)

            # Get page title
            title = await page.title()

            # Double Check: verify title contains expected keywords
            if "ヨルシカ" in title or "Yorushika" in title:
                print(f"✅ [Success] Connected! Page Title: {title}")
            else:
                print(f"⚠️ [Warning] Title mismatch. Got: {title}")

        except Exception as e:
            print(f"❌ [Error] Connection failed: {e}")

        finally:
            await browser.close()
            print(">>> [Cleanup] Browser closed.")


if __name__ == "__main__":
    # In PyCharm, right-click and Run 'test_env' directly
    asyncio.run(run_smoke_test())
