import asyncio
from playwright.async_api import async_playwright


async def run_smoke_test():
    print(">>> [Init] Starting Smoke Test...")

    async with async_playwright() as p:
        # Launch browser
        # headless=True 是默认的，但在开发阶段有时候改成 False 方便 Debug 看看发生了什么
        print(">>> [Browser] Launching Chromium...")
        browser = await p.chromium.launch(headless=True)

        # Context 和 Page 也就是浏览器实例和标签页
        context = await browser.new_context()
        page = await context.new_page()

        target_url = "https://yorushika.com/"
        print(f">>> [Network] Navigating to {target_url} ...")

        try:
            # timeout=10000 意思是 10秒没加载完就报错，防止死等
            await page.goto(target_url, timeout=10000)

            # 获取页面 Title
            title = await page.title()

            # Double Check: 验证 title 是否包含预期关键字
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
    # PyCharm 直接右键 Run 'test_env' 即可
    asyncio.run(run_smoke_test())