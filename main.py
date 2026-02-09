import os
import random
import asyncio
from fastapi import FastAPI
from fastapi.responses import FileResponse
from crawler import YorushikaCrawler
from card_maker import LyricCard

app = FastAPI(title="Yorushika Bot")

# 全局配置
ASSETS_DIR = "assets"
OUTPUT_FILE = "daily_card.jpg"


@app.get("/")
async def root():
    return {"message": "Yorushika Bot is Running! Visit /card to get a lyric card."}


@app.get("/card")
async def generate_lyric_card():
    """ 核心接口：触发爬虫 -> 合成图片 -> 返回图片 """
    crawler = YorushikaCrawler()

    try:
        # 1. 启动爬虫
        await crawler.start()

        # 2. 抓取流程
        print(">>> [API] 1. Getting URL...")
        url = await crawler.get_random_song_url()

        print(f">>> [API] 2. Fetching Lyric from {url}...")
        lyric = await crawler.get_lyric_by_url(url)

        # 3. 随机选一张背景图
        # 列出 assets 文件夹下所有的图片
        bg_files = [f for f in os.listdir(ASSETS_DIR) if f.lower().endswith(('.jpg', '.png', '.jpeg'))]
        if not bg_files:
            return {"error": "No background images found in assets/ folder!"}

        selected_bg = random.choice(bg_files)
        bg_path = os.path.join(ASSETS_DIR, selected_bg)
        print(f">>> [API] 3. Selected background: {selected_bg}")

        # 4. 合成图片
        # 实例化卡片制作器
        card_maker = LyricCard(bg_path)
        card_maker.create_card(lyric, output_name=OUTPUT_FILE)

        # 5. 返回生成的图片文件
        print(">>> [API] 4. Returning image...")
        return FileResponse(OUTPUT_FILE, media_type="image/jpeg")

    except Exception as e:
        print(f"❌ API Error: {e}")
        return {"error": str(e)}

    finally:
        # 别忘了关浏览器
        await crawler.stop()


if __name__ == "__main__":
    import uvicorn

    # 启动本地服务器，端口 8000
    uvicorn.run(app, host="127.0.0.1", port=8000)