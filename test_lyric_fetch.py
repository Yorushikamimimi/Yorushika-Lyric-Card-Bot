import asyncio
from playwright.async_api import async_playwright

# === 把你刚才成功的 Proxy 配置复制过来 ===
PROXY_URL = "http://127.0.0.1:26001"


# ========================================

async def fetch_one_lyric():
    print(">>> [Init] Starting Lyric Fetch Test...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="msedge",
            headless=False,
            proxy={"server": PROXY_URL}
        )
        page = await browser.new_page()

        # 目标：抓取《好きすぎて痛い》的歌词
        target_url = "https://www.uta-net.com/song/382208/"
        print(f">>> [Network] Navigating to {target_url} ...")

        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)

            # 1. 定位歌词区域
            # Uta-Net 的标准歌词容器 ID 是 #kashi_area
            print(">>> [Parsing] Locating #kashi_area ...")
            await page.wait_for_selector("#kashi_area")

            # 2. 提取文本
            # inner_text() 是个好东西，它会自动把 <br> 转换成 \n，把 &nbsp; 转换成空格
            raw_lyrics = await page.locator("#kashi_area").inner_text()

            # 3. 简单的后处理
            # 有时候歌词前后会有空行，strip() 去掉
            clean_lyrics = raw_lyrics.strip()

            print("\n" + "=" * 20 + " LYRIC PREVIEW " + "=" * 20)
            # 只打印前 5 行预览，避免刷屏
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