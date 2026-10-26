"""Menu images and preview.jpg, drawn from the TrackModel per layout (CPU only: Pillow + numpy).

Mirrors the example mod: each image is drawn at its named size (e.g. 250x116) in the top-left corner of a
power-of-two canvas (256x128), saved as an uncompressed .tga and converted with bimage to *_raw.bmap.

Style (2026-10-24, Chris: the old thumbnails, a scaled-down preview SVG, were unreadable at 250x116): like stock
event thumbnails (``data/menu/textures/event_*_small_250x116_raw``: a thick white outline with a direction arrow on
a transparent background, the menu's own panel behind it), each layout gets its own images, tight-cropped to that
layout's lap with a fixed margin: the lap as a thick white stroke with a dark outline, scaled to the image (about
4 px at 250x116), a chequered start/finish bar across it and a direction arrow; the circuit's other layouts as a
faint thin line on the larger images only. Small images carry no text; the loading, results and environment cards
put the outline on a dark background beside the title.
"""
from __future__ import annotations

import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

BG = (22, 22, 24)
BG2 = (34, 36, 40)
ACCENT = (200, 30, 36)
WHITE = (245, 245, 245)
GREY = (170, 170, 170)
OUTLINE = (12, 12, 14)
FAINT = (120, 124, 130)
FONT_PATHS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "C:/Windows/Fonts/arialbd.ttf")
SS = 4  # supersampling for smooth strokes
SIZES = {"loading": (1920, 1200), "results": (812, 162), "small": (250, 116), "large": (832, 386)}


def _font(size: int) -> ImageFont.ImageFont:
    for p in FONT_PATHS:
        if os.path.isfile(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def _pow2(n: int) -> int:
    p = 1
    while p < n:
        p *= 2
    return p


def _text_fit(draw: ImageDraw.ImageDraw, text: str, max_w: int, size: int) -> ImageFont.ImageFont:
    while size > 8:
        f = _font(size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return _font(8)


# ------------------------------------------------------------------------------------------- layout outlines
def _route_xy(route: dict) -> np.ndarray:
    return np.array([[q["x"], q["y"]] for q in route["samples"]], dtype=np.float64)


def _section(route: dict, a: float, b: float) -> np.ndarray:
    s = np.array([q["s"] for q in route["samples"]])
    xy = _route_xy(route)
    return xy[(s >= a - 1e-6) & (s <= b + 1e-6)]


def layout_outlines(model: dict, layouts: list[dict]) -> dict[str, dict]:
    """Per manifest layout (by scene name): ``lap`` (closed polyline in driving order from its start/finish line,
    TrackModel x/y), ``extra`` (alternative routes the layout may also drive: drawn tracks' alt routes) and
    ``others`` (every other road of the circuit, for the faint context line)."""
    routes = {r["id"]: r for r in model["routes"]}
    main = next(r for r in model["routes"] if r["aiIndex"] == 0)
    own = {lay["id"]: lay for lay in (model.get("layouts") or [])}
    out = {}
    for lay in layouts:
        key = lay["key"]
        mid = "main" if key in ("forward", "reverse") else key
        if own:
            ml = own.get(mid) or own.get("main") or next(iter(own.values()))
            lap = np.vstack([_section(routes[sec["route"]], float(sec["fromS"]), float(sec["toS"]))
                             for sec in ml["sections"]])
            extra = []
            used = {sec["route"] for sec in ml["sections"]}
            others = [_route_xy(r) for r in model["routes"] if r["id"] not in used]
            if used != {main["id"]}:
                others.append(_route_xy(main))
        else:
            lap = _route_xy(main)
            extra = [_route_xy(r) for r in model["routes"] if r["aiIndex"] != 0]
            others = []
        if lay.get("reverse"):
            lap = np.vstack([lap[:1], lap[1:][::-1]])
        keep = np.concatenate([[True], np.hypot(*np.diff(lap, axis=0).T) > 1e-6])
        out[lay["name"]] = {"lap": lap[keep], "extra": extra, "others": others}
    return out


# ------------------------------------------------------------------------------------------- drawing
def _frame(pts: np.ndarray, box: tuple[float, float, float, float], margin: float):
    """Transform model x/y into the box (x0, y0, x1, y1, pixels) keeping the aspect, north up, centred."""
    lo, hi = pts.min(0), pts.max(0)
    span = np.maximum(hi - lo, 1e-6)
    x0, y0, x1, y1 = box
    bw, bh = (x1 - x0) - 2 * margin, (y1 - y0) - 2 * margin
    k = min(bw / span[0], bh / span[1])
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    c = (lo + hi) / 2

    def tf(p: np.ndarray) -> np.ndarray:
        p = np.asarray(p, dtype=np.float64).reshape(-1, 2)
        return np.column_stack([cx + (p[:, 0] - c[0]) * k, cy - (p[:, 1] - c[1]) * k])
    return tf


def _line(d: ImageDraw.ImageDraw, pts: np.ndarray, fill, width: float, closed: bool) -> None:
    """A thick polyline without seams: vertices thinned to about half the width apart, each segment drawn with
    round joints (a disc at every vertex), so neighbouring segment polygons don't leave hairline gaps."""
    pts = np.asarray(pts, dtype=np.float64)
    keep, last = [0], pts[0]
    for i in range(1, len(pts)):
        if float(np.hypot(*(pts[i] - last))) >= max(1.0, width * 0.5):
            keep.append(i)
            last = pts[i]
    q = [tuple(map(float, pts[i])) for i in keep]
    if closed:
        q.append(q[0])
    elif keep[-1] != len(pts) - 1:
        q.append(tuple(map(float, pts[-1])))
    w = max(1, int(round(width)))
    r = w / 2.0
    d.line(q, fill=fill, width=w)
    for p in q:
        d.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=fill)


def _along(pts: np.ndarray, frac: float) -> tuple[np.ndarray, np.ndarray]:
    seg = np.hypot(*np.diff(np.vstack([pts, pts[:1]]), axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    t = frac * cum[-1]
    i = int(np.clip(np.searchsorted(cum, t, side="right") - 1, 0, len(pts) - 1))
    a, b = pts[i], pts[(i + 1) % len(pts)]
    f = (t - cum[i]) / max(seg[i], 1e-9)
    d = b - a
    return a + d * f, d / max(float(np.hypot(*d)), 1e-9)


def _tangent_at(pts: np.ndarray, i: int, reach: int = 4) -> np.ndarray:
    n = len(pts)
    d = pts[(i + reach) % n] - pts[(i - reach) % n]
    return d / max(float(np.hypot(*d)), 1e-9)


def draw_outline(img: Image.Image, box, outline: dict, stroke: float, faint: bool = True) -> None:
    """The layout's lap in ``box`` (pixels, already supersampled): faint other roads, the lap as a white stroke
    with a dark outline, a chequered start/finish bar and a direction arrow."""
    lap = outline["lap"]
    margin = stroke * 2.6
    tf = _frame(lap, box, margin)
    d = ImageDraw.Draw(img)
    if faint:
        for o in outline["others"]:
            _line(d, tf(o), FAINT + (150,), stroke * 0.35, False)
    halo = stroke * 0.45
    P = tf(lap)
    for e in outline["extra"]:
        _line(d, tf(e), OUTLINE + (255,), stroke * 0.7 + 2 * halo, False)
    _line(d, P, OUTLINE + (255,), stroke + 2 * halo, True)
    for e in outline["extra"]:
        _line(d, tf(e), (200, 200, 200, 255), stroke * 0.7, False)
    _line(d, P, WHITE + (255,), stroke, True)
    # start/finish: a chequered bar across the lap at its first point
    t = _tangent_at(P, 0)
    n = np.array([-t[1], t[0]])
    L, T = stroke * 3.4, stroke * 1.3
    p0 = P[0]
    cells_a, cells_b = 4, 2
    for a in range(cells_a):
        for b in range(cells_b):
            u0, u1 = -L / 2 + a * L / cells_a, -L / 2 + (a + 1) * L / cells_a
            v0, v1 = -T / 2 + b * T / cells_b, -T / 2 + (b + 1) * T / cells_b
            quad = [p0 + n * u + t * v for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]
            col = (20, 20, 20, 255) if (a + b) % 2 else (250, 250, 250, 255)
            d.polygon([tuple(q) for q in quad], fill=col)
    d.polygon([tuple(p0 + n * u + t * v) for u, v in ((-L / 2, -T / 2), (L / 2, -T / 2), (L / 2, T / 2), (-L / 2, T / 2))],
              outline=OUTLINE + (255,), width=max(1, int(stroke * 0.25)))
    # direction arrow a little way round the lap (stock thumbnails put an arrowhead on the line)
    q, dirn = _along(P, 0.12)
    dirn = _tangent_at(P, int(np.argmin(np.hypot(*(P - q).T))))
    nn = np.array([-dirn[1], dirn[0]])
    s = stroke * 2.4
    tri = [q + dirn * s, q - dirn * s * 0.7 + nn * s * 0.85, q - dirn * s * 0.7 - nn * s * 0.85]
    d.polygon([tuple(p) for p in tri], fill=WHITE + (255,), outline=OUTLINE + (255,), width=max(1, int(stroke * 0.3)))


def _stroke_px(w: int, h: int) -> float:
    """Lap stroke width: ~4 px at 250x116, scaled with the image."""
    return max(3.6, 0.016 * min(w, h * 2.16))


def outline_image(w: int, h: int, outline: dict, transparent: bool = True, faint: bool = False,
                  bg=BG) -> Image.Image:
    """Just the outline at w x h (stock event thumbnail style), transparent or on a plain dark background."""
    big = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0) if transparent else bg + (255,))
    draw_outline(big, (0, 0, w * SS, h * SS), outline, _stroke_px(w, h) * SS, faint)
    return big.resize((w, h), Image.LANCZOS)


def _background(w: int, h: int) -> Image.Image:
    """A dark vertical gradient with a soft vignette."""
    y = np.linspace(0, 1, h)[:, None]
    x = np.linspace(-1, 1, w)[None, :]
    v = 1.0 - 0.35 * (x ** 2) - 0.25 * ((y - 0.45) * 2) ** 2
    base = np.array(BG, dtype=np.float64)
    top = np.array(BG2, dtype=np.float64)
    a = (base[None, None, :] * (1 - v[..., None]) + top[None, None, :] * v[..., None])
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB").convert("RGBA")


def card(w: int, h: int, outline: dict, title: str, subtitle: str, text: bool = True) -> Image.Image:
    """Dark card: the layout outline (faint other roads behind), title and subtitle on the left when ``text``."""
    im = _background(w, h)
    pad = max(4, h // 20)
    wide = w / h > 3.0
    if not text:
        big = im.resize((w * SS, h * SS))
        draw_outline(big, (0, 0, w * SS, h * SS), outline, _stroke_px(w, h) * SS, faint=w >= 400)
        return big.resize((w, h), Image.LANCZOS).convert("RGB")
    ow = int(w * (0.34 if wide else 0.58))
    big = Image.new("RGBA", (ow * SS, h * SS), (0, 0, 0, 0))
    draw_outline(big, (0, 0, ow * SS, h * SS), outline, _stroke_px(ow, h) * SS * (0.8 if wide else 1.0), faint=True)
    ov = big.resize((ow, h), Image.LANCZOS)
    im.alpha_composite(ov, (w - ow - pad, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([pad, pad, pad + max(4, w // 150), h - pad], fill=ACCENT)
    x = pad * 3 + max(4, w // 150)
    text_w = w - ow - x - 2 * pad
    tf = _text_fit(d, title, text_w, int(h * (0.30 if wide else 0.09)))
    sf = _text_fit(d, subtitle, text_w, max(10, int(tf.size * 0.45)))  # never into the outline's column
    th = tf.getbbox(title)[3]
    y = (h - th - sf.size * 1.6) / 2 if wide else h * 0.38
    d.text((x, y), title, font=tf, fill=WHITE)
    d.text((x, y + th + sf.size * 0.6), subtitle, font=sf, fill=GREY)
    return im.convert("RGB")


SKEW_SHIFT = 11 / 69  # stock env_*_skew_182x70: a parallelogram, the top row 11 px right of the bottom one
ENV_KINDS = ("large_832x386", "small_250x116", "skew_182x70")  # every stock environment ships all three


def skew(w: int, h: int, outline: dict) -> Image.Image:
    """The environment's skewed header tile (stock ``env_*_skew_182x70_raw``: the lobby header shows it left of the
    event name; without it the game draws its pink missing-texture square, Chris 2026-10-26): the small card's art
    in a parallelogram, transparent outside, each row 171 of 182 px wide, slanting 11 px over the height."""
    im = card(w, h, outline, "", "", text=False).convert("RGBA")
    span = w - round(SKEW_SHIFT * (h - 1))
    y = np.arange(h)[:, None] + 0.5
    x = np.arange(w)[None, :] + 0.5
    x0 = SKEW_SHIFT * (h - y)                     # the left edge at this row
    a = np.clip(np.minimum(x - x0, x0 + span - x) + 0.5, 0.0, 1.0)  # 1 px anti-aliased edges
    im.putalpha(Image.fromarray((a * 255).astype(np.uint8), "L"))
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


def save_preview_jpg(outline: dict, title: str, subtitle: str, path: str, size=(1280, 720)) -> str:
    card(size[0], size[1], outline, title, subtitle).save(path, format="JPEG", quality=88)
    return path


def check_menu(images: dict[str, Image.Image], layouts: list[str], env: str | None = None) -> list[str]:
    """Problems with a build's menu images ({relpath: image}): each layout has its small, results and loading
    image at the named size (the name ends in _<w>x<h>_raw), layouts of one track don't share a picture, and the
    environment ``env`` has all three stock variants (large, small, skew)."""
    probs = []
    if env is not None:
        for kind in ENV_KINDS:
            if f"data/menu/textures/env_{env}_{kind}_raw" not in images:
                probs.append(f"environment {env}: no {kind} image")
    for rel, im in images.items():
        size = rel.rsplit("_", 2)[-2]
        w, h = (int(v) for v in size.split("x"))
        if im.size != (w, h):
            probs.append(f"{rel}: {im.size[0]}x{im.size[1]}, not {w}x{h}")
    for lay in layouts:
        for kind in ("small_250x116", "results_812x162", "loading_1920x1200"):
            if not any(r.endswith(f"event_{lay}_{kind}_raw") for r in images):
                probs.append(f"{lay}: no {kind} image")
    smalls = {lay: next((np.asarray(im) for r, im in images.items() if r.endswith(f"/event_{lay}_small_250x116_raw")),
                        None) for lay in layouts}
    names = [n for n in layouts if smalls[n] is not None]
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if smalls[a].shape == smalls[b].shape and np.array_equal(smalls[a], smalls[b]):
                probs.append(f"{a} and {b} have the same small image")
    return probs
