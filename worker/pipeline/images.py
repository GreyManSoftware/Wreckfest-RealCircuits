"""Menu images and preview.jpg from the geom top-down preview SVG (CPU only: cairosvg + Pillow).

Mirrors the example mod: each image is drawn at its named size (e.g. 250x116) in the top-left corner of a
power-of-two canvas (256x128), saved as an uncompressed .tga and converted with bimage to *_raw.bmap.
"""
from __future__ import annotations

import io
import os
import re

from PIL import Image, ImageDraw, ImageFont

BG = (22, 22, 24)
ACCENT = (200, 30, 36)
WHITE = (240, 240, 240)
GREY = (170, 170, 170)
FONT_PATHS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "C:/Windows/Fonts/arialbd.ttf")


def _font(size: int) -> ImageFont.ImageFont:
    for p in FONT_PATHS:
        if os.path.isfile(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def render_svg(svg: str, width: int = 1600) -> Image.Image:
    """Render the preview SVG (labels stripped, outline only) to RGBA."""
    import cairosvg

    svg = re.sub(r"<text\b[^>]*>.*?</text>", "", svg, flags=re.S)
    png = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=width)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def _pow2(n: int) -> int:
    p = 1
    while p < n:
        p *= 2
    return p


def _fit(img: Image.Image, w: int, h: int) -> Image.Image:
    im = img.copy()
    im.thumbnail((max(1, w), max(1, h)), Image.LANCZOS)
    return im


def _text_fit(draw: ImageDraw.ImageDraw, text: str, max_w: int, size: int) -> ImageFont.ImageFont:
    while size > 8:
        f = _font(size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return _font(8)


def card(w: int, h: int, preview: Image.Image, title: str, subtitle: str) -> Image.Image:
    """A dark card: preview outline on the right (or filling a small card), title and subtitle on the left."""
    im = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(im)
    pad = max(4, h // 20)
    small = w < 400
    wide = w / h > 3.0
    if small:
        pv = _fit(preview, w - 2 * pad, h - 2 * pad - h // 4)
        im.paste(pv, ((w - pv.width) // 2, pad), pv)
        band = h // 4
        d.rectangle([0, h - band, w, h], fill=BG)
        d.rectangle([0, h - band, w, h - band + max(2, h // 60)], fill=ACCENT)
        f = _text_fit(d, title, w - 2 * pad, band - 6)
        d.text((pad, h - band // 2 + 1), title, font=f, fill=WHITE, anchor="lm")
        return im
    pv_w = int(w * (0.30 if wide else 0.55))
    pv = _fit(preview, pv_w - 2 * pad, h - 2 * pad)
    im.paste(pv, (w - pad - pv.width, (h - pv.height) // 2), pv)
    text_w = w - pv.width - 4 * pad
    d.rectangle([pad, pad, pad + max(4, w // 150), h - pad], fill=ACCENT)
    x = pad * 3 + max(4, w // 150)
    tf = _text_fit(d, title, text_w - x, int(h * (0.30 if wide else 0.09)))
    sf = _font(max(10, int(tf.size * 0.45)))
    th = tf.getbbox(title)[3]
    y = (h - th - sf.size * 1.6) / 2 if wide else h * 0.38
    d.text((x, y), title, font=tf, fill=WHITE)
    d.text((x, y + th + sf.size * 0.6), subtitle, font=sf, fill=GREY)
    return im


def save_tga(img: Image.Image, path: str, alpha: bool) -> str:
    """Save at the named size in the top-left of a power-of-two canvas, like the example mod's sources."""
    w, h = img.size
    mode = "RGBA" if alpha else "RGB"
    canvas = Image.new(mode, (_pow2(w), _pow2(h)), (0, 0, 0, 0) if alpha else (0, 0, 0))
    canvas.paste(img.convert(mode), (0, 0))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    canvas.save(path, format="TGA", compression=None)
    return path


def save_preview_jpg(preview: Image.Image, title: str, subtitle: str, path: str, size=(1280, 720)) -> str:
    card(size[0], size[1], preview, title, subtitle).save(path, format="JPEG", quality=88)
    return path
