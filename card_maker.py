from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
import os


class LyricCard:
    def __init__(self, bg_path, font_path=None):
        self.bg_path = bg_path
        # Font priority: project bundled > Windows > Linux
        self.font_path = font_path
        if not self.font_path:
            # Project bundled fonts (used in Docker)
            project_fonts = [
                "fonts/NotoSerifJP-Regular.otf",
                "fonts/NotoSerifJP-Regular.ttf",
            ]
            for f in project_fonts:
                if os.path.exists(f):
                    self.font_path = f
                    break

            # Windows system fonts
            if not self.font_path:
                windows_fonts = [
                    "C:/Windows/Fonts/msyh.ttc",  # Microsoft YaHei
                    "C:/Windows/Fonts/simhei.ttf",  # SimHei
                    "C:/Windows/Fonts/meiryo.ttc",  # Meiryo
                ]
                for f in windows_fonts:
                    if os.path.exists(f):
                        self.font_path = f
                        break

            # Linux system fonts (fonts-noto-cjk in Docker)
            if not self.font_path:
                linux_fonts = [
                    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
                    "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc",
                    "/usr/share/fonts/noto-cjk/NotoSerifCJK-Regular.ttc",
                ]
                for f in linux_fonts:
                    if os.path.exists(f):
                        self.font_path = f
                        break

        if not self.font_path:
            raise FileNotFoundError(
                "No Japanese font found. Please specify font_path or add fonts/NotoSerifJP-Regular.otf to the project."
            )

    def create_card(self, text, output_name="demo.jpg", artist_name=None, song_title=None):
        print(f">>> [Image] Loading background: {self.bg_path}")

        # Polaroid layout dimensions
        photo_w, photo_h = 1080, 1080  # Photo area
        info_h = 180  # Bottom info section height
        border = 40  # White border thickness

        # Full card size (including border)
        card_w = photo_w + border * 2
        card_h = photo_h + info_h + border * 2

        # 1. Open background image and center-crop to square
        img = Image.open(self.bg_path).convert("RGB")
        img = self._center_crop(img, (photo_w, photo_h))

        # 2. Add semi-transparent black overlay (protect lyric readability)
        overlay = Image.new("RGBA", (photo_w, photo_h), (0, 0, 0, 70))
        img_rgba = img.convert("RGBA")
        img_rgba = Image.alpha_composite(img_rgba, overlay)
        img = img_rgba.convert("RGB")

        # 3. Draw lyric text (centered)
        draw = ImageDraw.Draw(img)

        # Dynamic font size: more chars = smaller font
        char_count = len(text.replace("\n", ""))
        font_size = 58 if char_count < 20 else 42 if char_count < 40 else 36
        font = ImageFont.truetype(self.font_path, font_size)

        # Draw text line by line
        lines = text.split("\n")
        line_height = font_size * 1.5
        total_text_height = line_height * len(lines)
        max_text_width = photo_w - 60

        y_offset = (photo_h - total_text_height) / 2
        for line in lines:
            line_width = sum(1.2 if '\u4e00' <= c <= '\u9fff' else 0.7 for c in line) * font_size * 0.5
            if line_width > max_text_width:
                line = line[:int(max_text_width / (font_size * 0.6))] + "..."
            x_center = photo_w / 2
            y_offset += line_height
            # Shadow
            draw.text((x_center + 2, y_offset - font_size * 0.9 + 2), line,
                      font=font, fill=(30, 30, 30), anchor="mm")
            # White text
            draw.text((x_center, y_offset - font_size * 0.9), line,
                      font=font, fill=(255, 255, 255), anchor="mm")

        # 4. Assemble complete card
        card = Image.new("RGB", (card_w, card_h), (255, 255, 255))
        card.paste(img, (border, border))

        # 5. Bottom info area: artist name + song title
        info_draw = ImageDraw.Draw(card)
        info_text_y = border + photo_h + 40  # Bottom info section start

        # Artist name (Yorushika)
        artist_display = artist_name or "ヨルシカ / Yorushika"
        artist_font = ImageFont.truetype(self.font_path, 22)
        info_draw.text(
            (card_w / 2, info_text_y + 20),
            artist_display,
            font=artist_font,
            fill=(140, 140, 140),
            anchor="mm"
        )

        # Song title
        if song_title:
            song_font = ImageFont.truetype(self.font_path, 30)
            # Truncate long titles
            if len(song_title) > 30:
                song_title = song_title[:28] + "..."
            info_draw.text(
                (card_w / 2, info_text_y + 75),
                song_title,
                font=song_font,
                fill=(60, 60, 60),
                anchor="mm"
            )

        # 6. Add shadow (simulate Polaroid thickness)
        card = self._add_polaroid_shadow(card, border)

        # 7. Save
        print(f">>> [Image] Saving card to {output_name}...")
        card.save(output_name, quality=95)
        print("✅ Card generated successfully!")

    def _add_polaroid_shadow(self, card, border):
        """Add shadow to card, simulating Polaroid photo thickness"""
        # Create shadow layer (shadow only, no card content)
        shadow_offset = 8
        shadow_layer = Image.new("RGB", card.size, (160, 160, 160))
        shadow_layer = ImageOps.expand(shadow_layer, border=shadow_offset, fill=(140, 140, 140))
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=8))
        # Paste card onto shadow to create offset shadow effect
        result = Image.new("RGB", card.size, (255, 255, 255))
        result.paste(shadow_layer, (shadow_offset, shadow_offset))
        result.paste(card, (0, 0))
        return result

    def _center_crop(self, img, size):
        """Center crop, maintain aspect ratio"""
        target_w, target_h = size
        img_w, img_h = img.size
        scale = max(target_w / img_w, target_h / img_h)
        new_w = int(img_w * scale)
        new_h = int(img_h * scale)
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        left = (new_w - target_w) / 2
        top = (new_h - target_h) / 2
        right = (new_w + target_w) / 2
        bottom = (new_h + target_h) / 2
        return img.crop((left, top, right, bottom))


# --- Test code ---
if __name__ == "__main__":
    sample_lyric = "マジでぎゅんぎゅん\n好きすぎて痛い"

    try:
        card = LyricCard("assets/bg.jpg")
        card.create_card(sample_lyric)
        os.startfile("demo.jpg")
    except Exception as e:
        print(f"❌ Error: {e}")
