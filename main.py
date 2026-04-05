import os
import random
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from crawler import YorushikaCrawler
from card_maker import LyricCard

# Rate limiter — 10 calls per minute per IP
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Yorushika Bot")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Global configuration
ASSETS_DIR = "assets"
OUTPUT_FILE = "daily_card.jpg"


@app.get("/")
async def root():
    return {"message": "Yorushika Bot is Running! Visit /card to get a lyric card."}


@app.get("/card")
@limiter.limit("10/minute")
async def generate_lyric_card(request: Request):
    """Core endpoint: trigger crawler -> composite image -> return image"""
    crawler = YorushikaCrawler()

    try:
        # 1. Start crawler
        await crawler.start()

        # 2. Fetching process
        print(">>> [API] 1. Getting URL...")
        url = await crawler.get_random_song_url()

        print(f">>> [API] 2. Fetching Lyric from {url}...")
        lyric, song_title = await crawler.get_lyric_by_url(url)

        # 3. Randomly select a background image
        # List all images in assets folder
        bg_files = [f for f in os.listdir(ASSETS_DIR) if f.lower().endswith(('.jpg', '.png', '.jpeg'))]
        if not bg_files:
            return {"error": "No background images found in assets/ folder!"}

        selected_bg = random.choice(bg_files)
        bg_path = os.path.join(ASSETS_DIR, selected_bg)
        print(f">>> [API] 3. Selected background: {selected_bg}")

        # 4. Composite image
        # Instantiate card maker
        card_maker = LyricCard(bg_path)
        card_maker.create_card(lyric, output_name=OUTPUT_FILE, song_title=song_title)

        # 5. Return generated image file
        print(">>> [API] 4. Returning image...")
        return FileResponse(OUTPUT_FILE, media_type="image/jpeg")

    except Exception as e:
        print(f"❌ API Error: {e}")
        return {"error": str(e)}

    finally:
        # Don't forget to close browser
        await crawler.stop()


if __name__ == "__main__":
    import uvicorn

    # Start local server, port 8000
    uvicorn.run(app, host="127.0.0.1", port=8000)
