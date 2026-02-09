from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os


class LyricCard:
    def __init__(self, bg_path, font_path=None):
        self.bg_path = bg_path
        # Windows 常用中日文字体路径
        # 优先尝试 微软雅黑/UI (msyh.ttc) 或 Meiryo (meiryo.ttc)
        # 如果你想要更好看的字体，可以在网上下载 .ttf 文件放到项目里，然后改这里
        valid_fonts = [
            "C:/Windows/Fonts/msyh.ttc",  # 微软雅黑
            "C:/Windows/Fonts/simhei.ttf",  # 黑体
            "C:/Windows/Fonts/meiryo.ttc",  # 日文系统字体
        ]

        self.font_path = font_path
        if not self.font_path:
            for f in valid_fonts:
                if os.path.exists(f):
                    self.font_path = f
                    break

        if not self.font_path:
            raise FileNotFoundError("❌ 找不到支持日文的系统字体，请手动指定 font_path！")

    def create_card(self, text, output_name="result.jpg"):
        print(f">>> [Image] Loading background: {self.bg_path}")

        # 1. 打开图片并转换为 RGB (防止 PNG 透明底报错)
        img = Image.open(self.bg_path).convert("RGB")

        # 2. 裁剪/缩放图片到固定尺寸 (比如 1080x1080 的正方形卡片)
        target_size = (1080, 1080)
        img = self._center_crop(img, target_size)

        # 3. 加一层黑色遮罩 (Overlay)，防止背景太亮导致文字看不清
        # 创建一个半透明黑色图层 (Alpha=100)
        overlay = Image.new('RGBA', target_size, (0, 0, 0, 100))
        img.paste(Image.alpha_composite(img.convert('RGBA'), overlay), (0, 0))
        img = img.convert("RGB")  # 转回 RGB 方便保存 JPG

        # 4. 绘制文字
        draw = ImageDraw.Draw(img)

        # 动态调整字号：字越多，字越小
        font_size = 60 if len(text) < 20 else 40
        font = ImageFont.truetype(self.font_path, font_size)

        # 计算文字宽带，确保存储
        # textbbox 是 Pillow 10.0+ 的新标准写法，替代 textsize
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # 居中坐标
        x = (target_size[0] - text_width) / 2
        y = (target_size[1] - text_height) / 2

        # 加上一点阴影，更有质感
        shadow_offset = 3
        draw.text((x + shadow_offset, y + shadow_offset), text, font=font, fill=(50, 50, 50))  # 阴影
        draw.text((x, y), text, font=font, fill=(255, 255, 255))  # 正文

        # 5. 保存
        print(f">>> [Image] Saving card to {output_name}...")
        img.save(output_name, quality=95)
        print("✅ Card generated successfully!")

    def _center_crop(self, img, size):
        """ 辅助函数：中心裁剪，保持比例 """
        target_w, target_h = size
        img_w, img_h = img.size

        # 计算缩放比例
        scale = max(target_w / img_w, target_h / img_h)
        new_w = int(img_w * scale)
        new_h = int(img_h * scale)

        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # 中心裁剪
        left = (new_w - target_w) / 2
        top = (new_h - target_h) / 2
        right = (new_w + target_w) / 2
        bottom = (new_h + target_h) / 2

        return img.crop((left, top, right, bottom))


# --- 测试代码 ---
if __name__ == "__main__":
    # 使用刚才抓到的那句歌词做测试
    sample_lyric = "マジでぎゅんぎゅん\n好きすぎて痛い"

    try:
        # 确保根目录下有 bg.jpg
        card = LyricCard("assets/bg.jpg")
        card.create_card(sample_lyric)

        # Windows 下自动打开图片查看结果
        os.startfile("result.jpg")

    except Exception as e:
        print(f"❌ Error: {e}")