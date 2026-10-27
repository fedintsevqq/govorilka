"""Картинки для витрины репозитория: баннер README и превью для соцсетей (1280x640).

    python tools/make_brand.py

Берёт иконку из govorilka/assets и снимок окна из site/img/recording-dark.png (его делает tools/shots.py), рисует
фон из двух мягких цветных пятен, как на сайте, и кладёт результат в site/img. Нужен Pillow.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "img"
FONTS = Path("C:/Windows/Fonts")
BG = (11, 12, 16)
TEXT = (238, 240, 244)
MUTED = (162, 168, 180)
ACCENT_A, ACCENT_B = (124, 92, 255), (63, 182, 255)


def font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / ("segoeuib.ttf" if bold else "segoeui.ttf")), size)


def background(width: int, height: int) -> Image.Image:
    glow = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(glow)
    draw.ellipse((-width * 0.20, -height * 0.55, width * 0.45, height * 0.75), fill=(78, 56, 170))
    draw.ellipse((width * 0.55, -height * 0.45, width * 1.15, height * 0.65), fill=(28, 104, 160))
    glow = glow.filter(ImageFilter.GaussianBlur(width // 12))
    return Image.blend(Image.new("RGB", (width, height), BG), glow, 0.85)


def rounded(image: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *image.size), radius, fill=255)
    out = image.convert("RGBA")
    out.putalpha(mask)
    return out


def paste_with_shadow(canvas: Image.Image, image: Image.Image, position: tuple[int, int], radius: int) -> None:
    card = rounded(image, radius)
    pad = 50
    shadow = Image.new("RGBA", (card.width + pad * 2, card.height + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((pad, pad + 18, pad + card.width, pad + card.height + 18), radius, fill=(0, 0, 0, 190))
    shadow = shadow.filter(ImageFilter.GaussianBlur(26))
    canvas.paste(shadow, (position[0] - pad, position[1] - pad), shadow)
    border = Image.new("RGBA", (card.width + 2, card.height + 2), (255, 255, 255, 34))
    border.putalpha(rounded(Image.new("L", border.size, 255), radius + 1).getchannel("A").point(lambda v: 34 if v else 0))
    canvas.paste(border, (position[0] - 1, position[1] - 1), border)
    canvas.paste(card, position, card)


def chip(canvas: Image.Image, xy: tuple[int, int], text: str, size: int) -> int:
    """Плашка с полупрозрачной заливкой: рисуется на отдельном слое, иначе Pillow не смешивает её с фоном."""
    f = font(False, size)
    width = int(ImageDraw.Draw(canvas).textlength(text, font=f) + size * 1.4)
    x, y = xy
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle((x, y, x + width, y + size * 2), size, fill=(255, 255, 255, 20), outline=(255, 255, 255, 60))
    canvas.alpha_composite(layer)
    ImageDraw.Draw(canvas).text((x + size * 0.7, y + size * 0.42), text, font=f, fill=(196, 202, 214))
    return width


def brand(width: int, height: int, scale: float, shot_x: float, shot_width: int, chips: bool, name: str) -> None:
    canvas = background(width, height).convert("RGBA")
    shot = Image.open(OUT / "recording-dark.png").convert("RGB")
    shot = shot.resize((shot_width, int(shot_width * shot.height / shot.width)), Image.LANCZOS)
    paste_with_shadow(canvas, shot, (int(width * shot_x), int(height * 0.18)), int(18 * scale))

    icon = Image.open(ROOT / "govorilka" / "assets" / "icon.png").convert("RGBA")
    icon_size = int(112 * scale)
    icon = icon.resize((icon_size, icon_size), Image.LANCZOS)
    left = int(64 * scale)
    top = int(height * (0.17 if chips else 0.2))
    canvas.paste(icon, (left, top), icon)

    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.text((left, top + icon_size + int(18 * scale)), "Говорилка", font=font(True, int(78 * scale)), fill=TEXT)
    y = top + icon_size + int(18 * scale) + int(116 * scale)
    for line in ("Созвоны в текст на русском.", "Всё считается на вашем компьютере."):
        draw.text((left, y), line, font=font(False, int(30 * scale)), fill=MUTED)
        y += int(42 * scale)
    if chips:
        x, y = left, y + int(22 * scale)
        for label in ("Бесплатно", "Открытый код", "Windows и Mac"):
            x += chip(canvas, (x, y), label, int(20 * scale)) + int(12 * scale)
    canvas.convert("RGB").save(OUT / name, optimize=True)
    print(name, canvas.size)


if __name__ == "__main__":
    brand(1280, 640, 1.0, 0.55, 800, True, "social-preview.png")
    brand(1280, 360, 0.62, 0.60, 560, False, "banner.png")
