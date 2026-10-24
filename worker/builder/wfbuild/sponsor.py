"""Our own sponsor boards (category ``sponsorBoards``): ad-fence panels and billboards on posts, with textures
rendered here (the handle text in bold, an Instagram-style camera glyph we draw ourselves, a few colourways).
Stock ad props reference stock textures we can't change, so these are meshes in the base scene, not
``#xref`` subscenes.

Configuration: ``environments.json`` ``"sponsor"`` (text, colourways, how many boards, on/off), overridden by the
JSON file named in ``TRACKGEN_SPONSOR`` (merged over it; ``{"enabled": false}`` turns the boards off). The planner
(``dressing``) places them; ``build_scene`` turns those placements into one mesh (``sponsor_boards``) and writes
the textures as TGA under ``data/track/<slug>/textures`` (bimage'd by the pipeline like the generated maps).

Asset frame (as the stock ad fences): the printed face is local **-Y** (it faces the road / the oncoming view),
the board runs along local X, the origin is on the ground at its middle.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

import numpy as np

from . import envs
from .geometry import Mesh

KIND = "sponsor"
CATEGORY = "sponsorBoards"
FENCE = {"w": 3.84, "h": 1.0, "z0": 0.08, "t": 0.05}                # panel width, height, gap below, thickness
BILLBOARD = {"w": 7.0, "h": 3.5, "z0": 2.0, "t": 0.12, "post": 0.22, "sink": 0.6}
TEX = {"fence": (1024, 512), "billboard": (1024, 1024)}   # front art in the top half, plain back in the bottom half


@lru_cache(maxsize=1)
def config() -> dict:
    cfg = dict(envs.table().get("sponsor") or {"enabled": False})
    path = os.environ.get("TRACKGEN_SPONSOR")
    if path:
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def enabled() -> bool:
    c = config()
    return bool(c.get("enabled")) and bool(c.get("text")) and bool(c.get("colourways"))


def n_colourways() -> int:
    return len(config().get("colourways") or [])


def asset_names() -> list[str]:
    if not enabled():
        return []
    return [f"sponsor_{kind}{k}" for kind in ("fence", "billboard") for k in range(n_colourways())]


def kind_of(asset: str) -> tuple[str, int] | None:
    """('fence' | 'billboard', colourway) for one of our assets, else None."""
    for kind in ("billboard", "fence"):
        pre = f"sponsor_{kind}"
        if asset.startswith(pre) and asset[len(pre):].isdigit():
            return kind, int(asset[len(pre):])
    return None


def asset_info(asset: str) -> dict | None:
    k = kind_of(asset)
    if k is None:
        return None
    if k[0] == "fence":
        return {"r": FENCE["w"] / 2.0, "h": FENCE["z0"] + FENCE["h"], "face": "-y", "width": FENCE["w"],
                "depth": 0.3, "mesh": True}
    return {"r": BILLBOARD["w"] / 2.0 + 0.3, "h": BILLBOARD["z0"] + BILLBOARD["h"], "face": "-y",
            "width": BILLBOARD["w"], "depth": 0.8, "mesh": True}


def is_mesh(asset: str) -> bool:
    return kind_of(asset) is not None


# ----------------------------------------------------------------------------------------------- textures
def texture_rel(slug: str, kind: str, k: int) -> str:
    """Data path (without extension) of a board's texture."""
    return f"data/track/{slug}/textures/{slug}_sponsor_{kind}{k}_c"


FONT_PATHS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf",
              "C:/Windows/Fonts/segoeuib.ttf")


def _font(size: int):
    from PIL import ImageFont

    for p in FONT_PATHS:
        if os.path.isfile(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def _rgb(c) -> tuple[int, int, int]:
    return tuple(int(v) for v in c)  # type: ignore[return-value]


def _glyph(draw, box, colour, width: int) -> None:
    """A camera glyph in the Instagram manner (rounded square, lens ring, flash dot), drawn from primitives."""
    x0, y0, x1, y1 = box
    s = x1 - x0
    draw.rounded_rectangle(box, radius=int(s * 0.28), outline=colour, width=width)
    c = ((x0 + x1) / 2, (y0 + y1) / 2)
    r = s * 0.23
    draw.ellipse((c[0] - r, c[1] - r, c[0] + r, c[1] + r), outline=colour, width=width)
    d = s * 0.075
    fx, fy = x1 - s * 0.22, y0 + s * 0.22
    draw.ellipse((fx - d, fy - d, fx + d, fy + d), fill=colour)


def render_front(kind: str, cw: dict, text: str, size: tuple[int, int]):
    """The printed face (PIL image, top-down rows): background, a slanted accent stripe, the glyph and the text in
    bold, sized to fill the board."""
    from PIL import Image, ImageDraw

    W, H = size
    bg, fg, acc = _rgb(cw["background"]), _rgb(cw["text"]), _rgb(cw["accent"])
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    # accent: a slanted band at the left end and a thin rule along the bottom, as circuit sponsor boards have
    band = int(W * 0.06)
    d.polygon([(0, 0), (band * 2, 0), (band, H), (0, H)], fill=acc)
    d.rectangle((0, H - max(4, H // 22), W, H), fill=acc)
    d.rectangle((0, 0, W, max(3, H // 40)), fill=acc)
    left = band * 2 + W * 0.03
    pad = H * 0.16
    g = (H - 2 * pad) * (0.78 if kind == "fence" else 0.62)
    gx0 = left
    gy0 = (H - g) / 2
    _glyph(d, (gx0, gy0, gx0 + g, gy0 + g), fg, max(3, int(g * 0.095)))
    tx0 = gx0 + g + W * 0.035
    avail = W - tx0 - W * 0.04
    def fit(s: str, start: int, max_h: float):
        size_px = start
        while True:
            f = _font(size_px)
            bb = d.textbbox((0, 0), s, font=f)
            if (bb[2] - bb[0] <= avail and bb[3] - bb[1] <= max_h) or size_px <= 8:
                return f, bb
            size_px -= 2

    tag = cw.get("tagline") if kind == "billboard" else None
    f, bb = fit(text, int(H * 0.62), H - 2 * pad if not tag else (H - 2 * pad) * 0.55)
    th = bb[3] - bb[1]
    lines = [(text, f, bb, fg)]
    gap = 0.0
    if tag:
        f2, bb2 = fit(tag, int(th * 0.55), th * 0.5)
        lines.append((tag, f2, bb2, acc))
        gap = th * 0.35
    total = sum(b[3] - b[1] for _s, _f, b, _c in lines) + gap
    y = (H - total) / 2
    for s, ff, b, col in lines:  # centred as a block beside the glyph
        d.text((tx0 + (avail - (b[2] - b[0])) / 2 - b[0], y - b[1]), s, font=ff, fill=col)
        y += b[3] - b[1] + gap
    return img


def write_textures(root: str, slug: str) -> dict[str, str]:
    """Render every board texture to <root>/data/track/<slug>/textures/*.tga; returns {material key: data path}."""
    from PIL import Image

    from .maps import write_tga

    out: dict[str, str] = {}
    if not enabled():
        return out
    cfg = config()
    for k, cw in enumerate(cfg["colourways"]):
        for kind in ("fence", "billboard"):
            W, H = TEX[kind]
            sheet = Image.new("RGB", (W, H), _rgb(cw.get("back", (70, 72, 76))))
            sheet.paste(render_front(kind, {**cw, "tagline": cfg.get("tagline") if kind == "billboard" else None},
                                     cfg["text"], (W, H // 2)), (0, 0))
            rel = texture_rel(slug, kind, k)
            write_tga(os.path.join(root, *rel.split("/")) + ".tga", np.asarray(sheet, dtype=np.float64)[::-1])
            out[f"sponsor_{kind}{k}"] = rel
    return out


def material_specs(textures: dict[str, str]) -> dict[str, dict]:
    """Material specs (materials.build format) for the boards: albedo in slot 1, the stock zero-spec map in 9, a
    metal surface (they are solid but far behind the run-off)."""
    return {key: {"name": f"{key}#metal",
                  "slots": [(1, rel + ".tga", 1, (1.0, 1.0)),
                            (9, "data/art/textures/default_zero_spec_s.tga", 1, (1.0, 1.0))]}
            for key, rel in textures.items()}


# ----------------------------------------------------------------------------------------------- meshes
def _box(m: Mesh, centre, ax, ay, w: float, d: float, z0: float, z1: float, mat: str, front_uv, back_uv) -> None:
    """A box w along ax, d along ay, from z0 to z1; the -ay face gets ``front_uv`` (u0, v0, u1, v1), the rest
    ``back_uv``."""
    c = np.asarray(centre, dtype=np.float64)
    hx, hy = np.append(ax * w / 2.0, 0.0), np.append(ay * d / 2.0, 0.0)
    base = np.array([c[0], c[1], 0.0])
    P = {}
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz, z in ((0, z0), (1, z1)):
                P[(sx, sy, sz)] = m.vert(base + sx * hx + sy * hy + np.array([0.0, 0.0, z]))
    u0, v0, u1, v1 = front_uv
    b0, c0, b1, c1 = back_uv
    out = lambda v: np.append(v, 0.0)  # noqa: E731
    # front (-Y): left to right as seen from the front is +X... seen from -Y looking at +Y, +X is to the right
    m.oriented_face([P[(-1, -1, 0)], P[(1, -1, 0)], P[(1, -1, 1)], P[(-1, -1, 1)]], mat, out(-ay),
                    [(u0, v0), (u1, v0), (u1, v1), (u0, v1)])
    m.oriented_face([P[(1, 1, 0)], P[(-1, 1, 0)], P[(-1, 1, 1)], P[(1, 1, 1)]], mat, out(ay),
                    [(b0, c0), (b1, c0), (b1, c1), (b0, c1)])
    for sx in (-1, 1):
        m.oriented_face([P[(sx, -1, 0)], P[(sx, 1, 0)], P[(sx, 1, 1)], P[(sx, -1, 1)]], mat, out(ax * sx),
                        [(b0, c0), (b1, c0), (b1, c1), (b0, c1)])
    m.oriented_face([P[(-1, -1, 1)], P[(1, -1, 1)], P[(1, 1, 1)], P[(-1, 1, 1)]], mat, np.array([0, 0, 1.0]),
                    [(b0, c0), (b1, c0), (b1, c1), (b0, c1)])
    m.oriented_face([P[(-1, -1, 0)], P[(-1, 1, 0)], P[(1, 1, 0)], P[(1, -1, 0)]], mat, np.array([0, 0, -1.0]),
                    [(b0, c0), (b1, c0), (b1, c1), (b0, c1)])


FRONT_UV = (0.0, 0.5, 1.0, 1.0)     # TGA rows run bottom-up: the top half of the sheet is v 0.5..1
BACK_UV = (0.02, 0.02, 0.98, 0.45)


def build_mesh(placements) -> Mesh | None:
    """All sponsor placements as one solid mesh (``col = true``), in TrackModel coordinates."""
    m = Mesh("sponsor_boards", kind=KIND)
    for p in placements:
        k = kind_of(p.asset)
        if k is None:
            continue
        kind, cw = k
        mat = f"sponsor_{kind}{cw}"
        ax = np.array([np.cos(p.yaw), np.sin(p.yaw)])          # local +X
        ay = np.array([-np.sin(p.yaw), np.cos(p.yaw)])         # local +Y (the printed face is -Y)
        z = float(p.z)
        if kind == "fence":
            F = FENCE
            _box(m, (p.x, p.y), ax, ay, F["w"], F["t"], z + F["z0"], z + F["z0"] + F["h"], mat, FRONT_UV, BACK_UV)
            for sx in (-1, 1):  # two short feet
                q = np.array([p.x, p.y]) + ax * sx * (F["w"] / 2.0 - 0.15) + ay * 0.08
                _box(m, q, ax, ay, 0.06, 0.25, z - 0.2, z + F["z0"] + 0.2, mat, BACK_UV, BACK_UV)
        else:
            B = BILLBOARD
            _box(m, (p.x, p.y), ax, ay, B["w"], B["t"], z + B["z0"], z + B["z0"] + B["h"], mat, FRONT_UV, BACK_UV)
            for sx in (-1, 1):
                q = np.array([p.x, p.y]) + ax * sx * (B["w"] / 2.0 - 1.0) + ay * (B["t"] / 2.0 + B["post"] / 2.0)
                _box(m, q, ax, ay, B["post"], B["post"], z - B["sink"], z + B["z0"] + B["h"] - 0.2, mat,
                     BACK_UV, BACK_UV)
    return None if m.empty else m
