import asyncio
from playwright.async_api import async_playwright

# =================配置区=================
# 请根据你的实际情况修改端口，Clash通常是7890
PROXY_URL = "http://127.0.0.1:26001"
TARGET_URL = "https://www.uta-net.com/artist/22669/"


# ========================================

async def main():
    print(f">>> [Init] Connecting via Proxy: {PROXY_URL}")

    async with async_playwright() as p:
        # 1. 启动浏览器时明确指定 proxy
        # channel="msedge" 使用你本地的 Edge
        browser = await p.chromium.launch(
            channel="msedge",
            headless=False,
            proxy={"server": PROXY_URL}  # <--- 核心修改：强制走代理
        )

        # 2. 也是反爬的一环，伪装成正常用户
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            print(f">>> [Network] Goto {TARGET_URL} ...")
            # 延长超时时间到 60秒，日站有时候很慢
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)

            # 3. 【核心技巧】疯狂向下滚动，触发 Lazy Load
            print(">>> [Action] Scrolling down to load song list...")
            for i in range(5):
                await page.mouse.wheel(0, 1000)  # 向下滚 1000px
                await asyncio.sleep(0.5)  # 稍微歇一下，让人眼能跟上

            # 4. 等待歌词列表的主容器 (通常表格会有个 ID 或 Class)
            # 我们直接用最粗暴的模糊搜索：找包含 '/song/' 的链接
            print(">>> [Parsing] Waiting for song links...")
            await page.wait_for_selector("a[href*='/song/']", timeout=15000)

            # 提取
            elements = await page.locator("a[href*='/song/']").all()

            valid_songs = []
            for el in elements:
                txt = await el.inner_text()
                url = await el.get_attribute("href")
                # 简单过滤：歌名不能为空，且链接长度正常
                if txt and url and len(txt.strip()) > 0:
                    full_url = f"https://www.uta-net.com{url}"
                    valid_songs.append(f"{txt.strip()} | {full_url}")

            # 去重 (set) 并打印
            unique_songs = list(set(valid_songs))

            print(f"\n✅ SUCCESS! Found {len(unique_songs)} songs.")
            print("=" * 40)
            for s in unique_songs[:10]:  # 只打印前10首
                print(s)
            print("=" * 40)

        except Exception as e:
            print(f"❌ Error: {e}")
            # 再次截图分析
            await page.screenshot(path="final_error.png")

        finally:
            print(">>> [Cleanup] Closing browser...")
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())